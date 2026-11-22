"""Each pytest-xdist worker gets its own test database (``tests/conftest.py``).

Under ``pytest -n N`` every worker is a separate process with its own session. The
conftest names that session's database after the worker (``aierp_test_db_gw0``, ...), so
one worker's per-test truncation never empties another worker's tables. Without xdist
the name is unchanged. The worker session below runs in a subprocess, so this proof
needs no xdist of its own.
"""

import os
import subprocess
import sys
from pathlib import Path

import psycopg2

import tests.conftest as suite_conftest

BACKEND = Path(__file__).resolve().parents[1]

PROBE = """
import os

import psycopg2


def test_worker_database():
    name = os.environ["DB_NAME"]
    assert name == os.environ["EXPECTED_DB_NAME"]
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        dbname=name,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            assert cur.fetchone()[0] == name
            cur.execute("SELECT count(*) FROM alembic_version")
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()
"""


def _connect(dbname):
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        dbname=dbname,
    )


def _database_exists(name):
    admin = suite_conftest._admin_connection()
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            return cur.fetchone() is not None
    finally:
        admin.close()


def test_worker_db_names(monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    assert suite_conftest._worker_db_name("aierp_test_db") == "aierp_test_db"
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "")
    assert suite_conftest._worker_db_name("aierp_test_db") == "aierp_test_db"
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
    assert suite_conftest._worker_db_name("aierp_test_db") == "aierp_test_db_gw3"
    monkeypatch.setenv("AIERP_TEST_DB", "custom")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    assert (
        suite_conftest._worker_db_name(suite_conftest._test_db_name()) == "custom_gw0"
    )


def test_a_worker_session_gets_its_own_database(tmp_path):
    expected = suite_conftest._test_db_name() + "_gw9"
    probe = tmp_path / "test_probe.py"
    probe.write_text(PROBE)
    env = dict(os.environ, PYTEST_XDIST_WORKER="gw9", EXPECTED_DB_NAME=expected)
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.conftest",
            "-p",
            "no:cacheprovider",
            "-q",
            str(probe),
        ],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-3000:]
    assert "1 passed" in run.stdout
    # the worker's session dropped its database when it ended
    assert not _database_exists(expected)
    # this session's own database is the one it started with, still migrated
    own = os.environ["DB_NAME"]
    assert own == suite_conftest._TEST_DB_NAME
    conn = _connect(own)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM alembic_version")
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def test_the_pins_keep_the_generators_order():
    lines = (BACKEND / "constraints.txt").read_text().splitlines()
    pins = [line for line in lines if line and not line.startswith("#")]
    assert "execnet==2.1.2" in pins and "pytest-xdist==3.8.0" in pins
    # scripts/refresh_constraints.sh writes the pins through `LC_ALL=C sort -f`
    assert pins == sorted(pins, key=lambda line: line.upper())
