"""Issue #604 proof: supplier performance over observed PO receipts.

``app.recommend.supplier_performance.supplier_performance`` reads
``lead_time_observations`` exactly once through the
``LeadTimeObservationPostgresRepository`` and writes nothing.  The
window is the ``window_days`` days ending on ``as_of`` (the server's
current date when the caller supplies none).  One entry per
``supplier_code`` with an in-window receipt: ``receipts``,
``with_promise`` (a receipt counts as promised only when its
``promised_on`` is set and later than its ``ordered_on``),
``default_promise`` (a ``promised_on`` set but on or before
``ordered_on``), ``on_time`` (the real promises received on or before
``promised_on``), ``late``, ``on_time_rate`` (``on_time / with_promise``,
3 places, null when nothing carried a real promise), ``avg_days_late``
(mean of
``received_on - promised_on`` over the late receipts, 1 place, or
null), ``max_days_late`` (or null), ``avg_lead_time_days`` (mean of
``received_on - ordered_on``, 1 place), ``p90_lead_time_days``
(nearest-rank 90th percentile, an integer) and ``last_received_on``
(ISO date).  Entries are ordered by ``on_time_rate`` ascending with
nulls last, then ``late`` descending, then ``supplier_code``; the
answer is ``{"as_of", "window_days", "suppliers"}``.  A
``window_days`` outside 1 to 1825, or an ``as_of`` after today,
raises ``ValueError``.

``GET /api/suppliers/performance`` (query ``window_days``, ``as_of``)
serves that dict with response models, behind the app's
``dependencies=auth_gate`` (401 without a token, 200 for any signed-in
role), and a ``ValueError`` answers 422.

Proven here, in order, with the repository read
(``LeadTimeObservationPostgresRepository.list_rows``) monkeypatched to
fixed rows (no database) and the server's "today" (the module's
``_today`` seam standing in for ``date.today()``) monkeypatched to the
fixed ``AS_OF`` so the proof never depends on the real clock:

  * supplier "4834" (5 receipts in the window): the promised and
    received dates give 3 on time and 2 late, by 2 and 6 days —
    ``on_time_rate`` 0.6, ``avg_days_late`` 4.0, ``max_days_late`` 6,
    ``avg_lead_time_days`` 15.0, ``p90_lead_time_days`` 24 (nearest
    rank over 7, 11, 14, 19, 24) and ``last_received_on``
    "2026-09-15";
  * supplier "4861" (2 receipts): both without a promised date, so
    ``with_promise`` 0 and ``on_time_rate`` null; it sorts after every
    supplier with a rate (every supplier here has ``default_promise``
    0 — each promised receipt promises after its order date);
  * the window: a 2025-01-15 receipt of "4834", older than the 365-day
    window ending 2026-10-23, is excluded, and a "4899" receipt
    received exactly on the window start (2025-10-24) is included;
  * the read: ``list_rows`` is called exactly once, unfiltered;
  * validation: ``window_days`` 0 and 2000, and a future ``as_of``,
    raise ``ValueError``;
  * the route: through the test client, with the fixed ``as_of`` and
    ``window_days`` query (never the server's clock),
    ``GET /api/suppliers/performance`` gives 401 without a token, 200
    with the planner token (the bare development token the suite's
    ``client`` carries, role user) and the same body (``as_of`` echoing
    the fixed date), and 422 for ``window_days=0``;
  * identity, as a NON-ADMIN (role ``user``), in a second pass after a
    full reload: the page boots the production way (the token it would
    take from the URL hash as ``#access_token=...`` — the bare
    development token — with ``/api/auth/config`` answering publicly
    and ``/api/auth/me`` resolving the viewer for that user, dev-mode
    auth standing in for the IdP) — the route carries no admin-only
    control, so the non-admin viewer reads the performance and the
    page still renders, while unauthenticated /api traffic is still
    401.
"""

import inspect
import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.api.supplier_performance as supplier_performance_api
import app.recommend.supplier_performance as supplier_performance_module
from app.main import app
from app.recommend.supplier_performance import supplier_performance
from app.repositories.lead_time_observations_postgres_repository import (
    LeadTimeObservationPostgresRepository,
)

URL = "/api/suppliers/performance"
AS_OF = date(2026, 10, 23)
WINDOW_DAYS = 365
WINDOW_START = date(2025, 10, 24)  # AS_OF - (WINDOW_DAYS - 1)
ROUTE_PARAMS = {"as_of": AS_OF.isoformat(), "window_days": WINDOW_DAYS}


def _row(business_key, supplier, item, ordered, received, promised):
    return {
        "id": f"uuid-{business_key}",
        "business_key": business_key,
        "supplier_code": supplier,
        "item_code": item,
        "site_code": "1",
        "ordered_on": ordered,
        "received_on": received,
        "promised_on": promised,
        "lead_time_days": (received - ordered).days,
        "source_system": "acumatica",
        "synced_at": "2026-10-20T00:00:00+00:00",
    }


ROWS = [
    # 4820: 2 receipts — 1 on time, 1 late by 5.
    _row(
        "R:1:1", "4820", "W-1", date(2026, 4, 1), date(2026, 4, 10), date(2026, 4, 12)
    ),
    _row(
        "R:1:2", "4820", "W-1", date(2026, 3, 1), date(2026, 3, 20), date(2026, 3, 15)
    ),
    # 4834: 5 in-window receipts — 3 on time, 2 late by 2 and 6.
    _row(
        "R:2:1", "4834", "W-2", date(2026, 9, 1), date(2026, 9, 15), date(2026, 9, 20)
    ),
    _row(
        "R:2:2", "4834", "W-2", date(2026, 8, 1), date(2026, 8, 12), date(2026, 8, 10)
    ),
    _row("R:2:3", "4834", "W-2", date(2026, 7, 1), date(2026, 7, 8), date(2026, 7, 8)),
    _row(
        "R:2:4", "4834", "W-2", date(2026, 6, 1), date(2026, 6, 25), date(2026, 6, 19)
    ),
    _row("R:2:5", "4834", "W-2", date(2026, 5, 1), date(2026, 5, 20), date(2026, 6, 1)),
    # 4834: one receipt older than the window — excluded.
    _row(
        "R:2:6", "4834", "W-2", date(2025, 1, 1), date(2025, 1, 15), date(2025, 1, 20)
    ),
    # 4861: 2 receipts, both without a promised date.
    _row("R:3:1", "4861", "W-3", date(2026, 9, 10), date(2026, 9, 18), None),
    _row("R:3:2", "4861", "W-3", date(2026, 8, 10), date(2026, 9, 1), None),
    # 4899: boundary — received exactly on the window start (in) and
    # the day before the window start (out).
    _row(
        "R:4:1",
        "4899",
        "W-4",
        date(2025, 10, 1),
        date(2025, 10, 24),
        date(2025, 10, 30),
    ),
    _row(
        "R:4:2",
        "4899",
        "W-4",
        date(2025, 10, 1),
        date(2025, 10, 23),
        date(2025, 10, 28),
    ),
]

EXPECTED = {
    "as_of": "2026-10-23",
    "window_days": 365,
    "suppliers": [
        {
            "supplier_code": "4820",
            "receipts": 2,
            "with_promise": 2,
            "default_promise": 0,
            "on_time": 1,
            "late": 1,
            "on_time_rate": 0.5,
            "avg_days_late": 5.0,
            "max_days_late": 5,
            "avg_lead_time_days": 14.0,
            "p90_lead_time_days": 19,
            "last_received_on": "2026-04-10",
        },
        {
            "supplier_code": "4834",
            "receipts": 5,
            "with_promise": 5,
            "default_promise": 0,
            "on_time": 3,
            "late": 2,
            "on_time_rate": 0.6,
            "avg_days_late": 4.0,
            "max_days_late": 6,
            "avg_lead_time_days": 15.0,
            "p90_lead_time_days": 24,
            "last_received_on": "2026-09-15",
        },
        {
            "supplier_code": "4899",
            "receipts": 1,
            "with_promise": 1,
            "default_promise": 0,
            "on_time": 1,
            "late": 0,
            "on_time_rate": 1.0,
            "avg_days_late": None,
            "max_days_late": None,
            "avg_lead_time_days": 23.0,
            "p90_lead_time_days": 23,
            "last_received_on": "2025-10-24",
        },
        {
            "supplier_code": "4861",
            "receipts": 2,
            "with_promise": 0,
            "default_promise": 0,
            "on_time": 0,
            "late": 0,
            "on_time_rate": None,
            "avg_days_late": None,
            "max_days_late": None,
            "avg_lead_time_days": 15.0,
            "p90_lead_time_days": 22,
            "last_received_on": "2026-09-18",
        },
    ],
}


def test_issue604_freeform(monkeypatch, client):
    # -- 1. The computation (repository read monkeypatched — no DB) ----
    calls: list[dict] = []

    def fake_list_rows(self, limit, offset, **filters):
        calls.append({"limit": limit, "offset": offset, "filters": filters})
        return [dict(row) for row in ROWS]

    monkeypatch.setattr(
        LeadTimeObservationPostgresRepository, "list_rows", fake_list_rows
    )

    # The proof is clock-free: the server's "today" is the frozen
    # AS_OF, so default as_of resolution and the "after today" check
    # never depend on the real clock.
    monkeypatch.setattr(supplier_performance_module, "_today", lambda: AS_OF)

    result = supplier_performance(as_of=AS_OF, window_days=WINDOW_DAYS)
    assert result == EXPECTED

    # The read: exactly once, through the existing repository, with no
    # filters (the window is applied in memory over the full read).
    assert len(calls) == 1
    assert calls[0]["filters"] == {}

    # The default as_of resolves through the frozen server clock, not
    # the real one: the proof carries no date.today() time bomb.
    defaulted = supplier_performance(window_days=WINDOW_DAYS)
    assert defaulted["as_of"] == AS_OF.isoformat()

    by_code = {entry["supplier_code"]: entry for entry in result["suppliers"]}

    # 4834: 5 in-window receipts, 3 on time, 2 late by 2 and 6 days.
    entry_4834 = by_code["4834"]
    assert entry_4834["receipts"] == 5  # the 2025-01-15 receipt is out
    assert entry_4834["with_promise"] == 5
    assert entry_4834["on_time"] == 3
    assert entry_4834["late"] == 2
    assert entry_4834["on_time_rate"] == 0.6
    assert entry_4834["avg_days_late"] == 4.0
    assert entry_4834["max_days_late"] == 6
    assert entry_4834["avg_lead_time_days"] == 15.0
    assert entry_4834["p90_lead_time_days"] == 24
    assert entry_4834["last_received_on"] == "2026-09-15"

    # 4861: both receipts without a promised date.
    entry_4861 = by_code["4861"]
    assert entry_4861["with_promise"] == 0
    assert entry_4861["on_time_rate"] is None
    assert entry_4861["avg_days_late"] is None
    assert entry_4861["max_days_late"] is None

    # Ordering: on_time_rate ascending with nulls last — 4861 (null)
    # sorts after every supplier with a rate.
    assert [e["supplier_code"] for e in result["suppliers"]] == [
        "4820",
        "4834",
        "4899",
        "4861",
    ]

    # The window: the 2025-01-15 receipt is excluded, while a receipt
    # received exactly on the window start is included.
    assert by_code["4899"]["receipts"] == 1
    assert by_code["4899"]["last_received_on"] == WINDOW_START.isoformat()

    # -- 2. Validation ---------------------------------------------------
    with pytest.raises(ValueError):
        supplier_performance(as_of=AS_OF, window_days=0)
    with pytest.raises(ValueError):
        supplier_performance(as_of=AS_OF, window_days=2000)
    # A future as_of (one day past the frozen today) raises.
    with pytest.raises(ValueError):
        supplier_performance(as_of=AS_OF + timedelta(days=1), window_days=WINDOW_DAYS)

    # -- 3. The route ----------------------------------------------------
    # 401 without a token; 200 with the planner token (the bare
    # development token the suite's ``client`` carries, role user) and
    # the same body; 422 for window_days=0 (the ValueError answer).
    anon = TestClient(app)
    response = anon.get(URL)
    assert response.status_code == 401

    response = client.get(URL, params=ROUTE_PARAMS)
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED
    # The route was asked for the fixed AS_OF, not the server's clock:
    # the body echoes it back.
    assert response.json()["as_of"] == AS_OF.isoformat()

    response = client.get(URL, params={"window_days": 0})
    assert response.status_code == 422

    # -- 4. Identity: the proof also runs as a NON-ADMIN ----------------
    # Second pass after a full reload: the page boots the production
    # way — the token it would take from the URL hash as
    # #access_token=... (the bare development token), with
    # /api/auth/config answering publicly and /api/auth/me resolving
    # the non-admin viewer for that user.
    dev_token = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")
    viewer = TestClient(app, headers={"Authorization": f"Bearer {dev_token}"})
    assert anon.get("/api/auth/config").status_code == 200  # public, pre-token
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == "dev-user-1"
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]
    # The admin-only control is absent: the route module references no
    # admin dependency and the route stays in the schema, so the
    # non-admin viewer reads the performance and the page still
    # renders.
    assert "require_admin" not in inspect.getsource(supplier_performance_api)
    assert URL in app.openapi()["paths"]
    response = viewer.get(URL, params=ROUTE_PARAMS)
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED
    assert response.json()["as_of"] == AS_OF.isoformat()
    # The gate is intact: unauthenticated /api traffic is still 401.
    assert anon.get(URL).status_code == 401
