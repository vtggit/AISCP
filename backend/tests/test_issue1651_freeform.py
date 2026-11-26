"""Issue #1651 proof: a checkout that waits out the pool times out.

AC-1 — the pool's checkout deadline is the instance's DB_POOL_TIMEOUT
in seconds, read at call time: 30.0 with the variable unset, 2.5 for
"2.5", and 30.0 again for every value that is not a finite number
above 0 — "", "0", "-1", "abc", "nan" and "inf".

AC-2 and AC-3 — while one connection of a fresh, single-connection
pool is held, a second checkout raises PoolTimeoutError after
waiting at least 0.25 s and less than 3 s, and the timeout logs
exactly one WARNING through app.db.connection; the same situation
through the app answers 503 with exactly the busy body and
Retry-After: 5.

AC-4 — the timeout leaves the pool usable: no connection or waiter
is leaked, and once the held connection is given back the same
request answers 200.
"""

import logging
import time
from contextlib import suppress

import pytest

import app.db.connection as connection
from app.db.connection import PoolTimeoutError

URL = "/api/erp-sources"
BUSY_BODY = {"detail": "The database is busy; try again shortly."}


def _drain(pool) -> None:
    """Close every connection the pool holds: it ends empty and
    counted down, so nothing of it leaks into the rest of the
    session."""
    with pool._wake:
        idle = list(pool._idle)
        assert pool._live == len(idle) and pool._closing == 0
        pool._idle.clear()
        pool._live = 0
        pool._closing = 0
        pool._wake.notify_all()
    for conn, _factory in idle:
        with suppress(Exception):
            conn.close()


def test_issue1651_freeform(client, monkeypatch, caplog) -> None:
    # AC-1: the deadline reads DB_POOL_TIMEOUT at call time.
    monkeypatch.delenv("DB_POOL_TIMEOUT", raising=False)
    assert connection._pool_timeout() == 30.0
    monkeypatch.setenv("DB_POOL_TIMEOUT", "2.5")
    assert connection._pool_timeout() == 2.5
    for raw in ("", "0", "-1", "abc", "nan", "inf"):
        monkeypatch.setenv("DB_POOL_TIMEOUT", raw)
        assert connection._pool_timeout() == 30.0, raw

    # A fresh pool of one connection and a 0.3 s deadline, with the
    # app's request paths pointed at it.
    pool = connection._Pool()
    monkeypatch.setattr(connection, "_pool", pool)
    monkeypatch.setenv("DB_POOL_SIZE", "1")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "0.3")

    kept = pool.checkout()
    try:
        # AC-1, AC-3 and AC-4: a second checkout on the full pool
        # waits out its deadline and raises; the pool stays usable.
        monkeypatch.setattr(logging.getLogger("app.db.connection"), "disabled", False)
        started = time.monotonic()
        with (
            caplog.at_level(logging.WARNING, logger="app.db.connection"),
            pytest.raises(PoolTimeoutError),
        ):
            pool.checkout()
        waited = time.monotonic() - started
        assert 0.25 <= waited < 3.0, waited

        # AC-3: the timeout is one WARNING through app.db.connection.
        records = [
            record
            for record in caplog.records
            if record.name == "app.db.connection" and record.levelno == logging.WARNING
        ]
        assert len(records) == 1
        assert records[0].getMessage() == (
            "db pool: no connection became free within 0.3s; 1 of 1 in use"
        )

        # AC-2: a request that waits out the full pool answers 503
        # with the retry hint, not a 500.
        response = client.get(URL)
        assert response.status_code == 503, response.text
        assert response.json() == BUSY_BODY
        assert response.headers["Retry-After"] == "5"
    finally:
        kept.close()

    # AC-4: the held connection given back, the same request answers.
    response = client.get(URL)
    assert response.status_code == 200, response.text
    _drain(pool)
