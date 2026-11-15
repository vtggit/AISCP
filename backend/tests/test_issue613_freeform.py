"""Issue #613 proof: only real promises count in supplier performance.

A receipt counts as promised only when its ``promised_on`` is set and
later than its ``ordered_on``; a promise made on the order day (or
before it) is a default, not a promise.  ``with_promise``,
``on_time``, ``late``, ``on_time_rate``, ``avg_days_late`` and
``max_days_late`` use only the real promises; ``default_promise``
counts the same-day or earlier promises.  The ordering rule is
unchanged: a supplier with no real promise has a null
``on_time_rate`` and sorts after every supplier with a rate.

Proven here with the repository read
(``LeadTimeObservationPostgresRepository.list_rows``) monkeypatched to
fixed rows (no database) and the server's "today" (the module's
``_today`` seam standing in for ``date.today()``) monkeypatched to the
fixed ``AS_OF`` so the proof never depends on the real clock:

  * supplier "4870" (4 receipts in the window, mixed promises): three
    receipts promise on the order day and one promises 5 days after
    the order, received 2 days after the promise —
    ``default_promise`` 3, ``with_promise`` 1, ``late`` 1,
    ``on_time_rate`` 0.0 and ``avg_days_late`` 2.0;
  * supplier "4851" (2 receipts, both promised on or before the order
    date): ``with_promise`` 0, ``default_promise`` 2 and
    ``on_time_rate`` null — it sorts last, after every supplier with
    a rate;
  * supplier "4887" (3 receipts, two of them without an
    ``ordered_on`` — the first of them carrying a promised date): the
    promised row without an order date is neither a real promise (it
    has nothing to be later than) nor a default, so
    ``with_promise`` 1 (the normal promise, late by 2),
    ``default_promise`` 0 and ``on_time_rate`` 0.0, and the lead-time
    statistics skip the ``ordered_on``-less receipts;
  * supplier "4890" (2 receipts, all without an ``ordered_on`` — one
    of them carrying a promised date): ``with_promise`` 0,
    ``on_time_rate`` null, and the entry still computes instead of
    crashing (``avg_lead_time_days`` 0.0, ``p90_lead_time_days`` 0),
    sorting last;
  * the read: ``list_rows`` is called exactly once, unfiltered;
  * the route: through the test client, with the fixed ``as_of`` and
    ``window_days`` query (never the server's clock),
    ``GET /api/suppliers/performance`` answers 200 (401 without a
    token) and its suppliers carry ``default_promise``.
"""

from datetime import date

from fastapi.testclient import TestClient

import app.recommend.supplier_performance as supplier_performance_module
from app.main import app
from app.recommend.supplier_performance import supplier_performance
from app.repositories.lead_time_observations_postgres_repository import (
    LeadTimeObservationPostgresRepository,
)

URL = "/api/suppliers/performance"
AS_OF = date(2026, 10, 23)
WINDOW_DAYS = 365
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
        "lead_time_days": None if ordered is None else (received - ordered).days,
        "source_system": "acumatica",
        "synced_at": "2026-10-20T00:00:00+00:00",
    }


ROWS = [
    # 4870: 4 receipts — 3 same-day promises (defaults) and 1 real
    # promise 5 days after the order, received 2 days after the
    # promise (late by 2).
    _row(
        "R:1:1", "4870", "W-1", date(2026, 1, 15), date(2026, 1, 20), date(2026, 1, 15)
    ),
    _row(
        "R:1:2", "4870", "W-1", date(2026, 2, 10), date(2026, 2, 25), date(2026, 2, 10)
    ),
    _row("R:1:3", "4870", "W-1", date(2026, 3, 5), date(2026, 3, 18), date(2026, 3, 5)),
    _row("R:1:4", "4870", "W-1", date(2026, 6, 1), date(2026, 6, 8), date(2026, 6, 6)),
    # 4851: 2 receipts — one promised on the order day and one
    # promised before it: no real promise at all.
    _row("R:2:1", "4851", "W-2", date(2026, 5, 1), date(2026, 5, 12), date(2026, 5, 1)),
    _row(
        "R:2:2", "4851", "W-2", date(2026, 7, 1), date(2026, 7, 20), date(2026, 6, 25)
    ),
    # 4887: hostile rows — two receipts without an ordered_on, the
    # first of them carrying a promised date, plus one normal promise
    # 5 days after the order, received 2 days after the promise.
    _row("R:3:1", "4887", "W-3", None, date(2026, 5, 10), date(2026, 5, 12)),
    _row("R:3:2", "4887", "W-3", None, date(2026, 5, 20), None),
    _row("R:3:3", "4887", "W-3", date(2026, 6, 1), date(2026, 6, 8), date(2026, 6, 6)),
    # 4890: every receipt without an ordered_on — the lead-time
    # statistics have nothing to average, yet the entry must still
    # compute instead of crashing.
    _row("R:4:1", "4890", "W-4", None, date(2026, 4, 5), date(2026, 4, 10)),
    _row("R:4:2", "4890", "W-4", None, date(2026, 4, 8), None),
]

EXPECTED = {
    "as_of": "2026-10-23",
    "window_days": 365,
    "suppliers": [
        {
            "supplier_code": "4870",
            "receipts": 4,
            "with_promise": 1,
            "default_promise": 3,
            "on_time": 0,
            "late": 1,
            "on_time_rate": 0.0,
            "avg_days_late": 2.0,
            "max_days_late": 2,
            "avg_lead_time_days": 10.0,
            "p90_lead_time_days": 15,
            "last_received_on": "2026-06-08",
        },
        {
            "supplier_code": "4887",
            "receipts": 3,
            "with_promise": 1,
            "default_promise": 0,
            "on_time": 0,
            "late": 1,
            "on_time_rate": 0.0,
            "avg_days_late": 2.0,
            "max_days_late": 2,
            "avg_lead_time_days": 7.0,
            "p90_lead_time_days": 7,
            "last_received_on": "2026-06-08",
        },
        {
            "supplier_code": "4851",
            "receipts": 2,
            "with_promise": 0,
            "default_promise": 2,
            "on_time": 0,
            "late": 0,
            "on_time_rate": None,
            "avg_days_late": None,
            "max_days_late": None,
            "avg_lead_time_days": 15.0,
            "p90_lead_time_days": 19,
            "last_received_on": "2026-07-20",
        },
        {
            "supplier_code": "4890",
            "receipts": 2,
            "with_promise": 0,
            "default_promise": 0,
            "on_time": 0,
            "late": 0,
            "on_time_rate": None,
            "avg_days_late": None,
            "max_days_late": None,
            "avg_lead_time_days": 0.0,
            "p90_lead_time_days": 0,
            "last_received_on": "2026-04-08",
        },
    ],
}


def test_issue613_freeform(monkeypatch, client):
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

    by_code = {entry["supplier_code"]: entry for entry in result["suppliers"]}

    # 4870: mixed promises — the 3 same-day promises are defaults, the
    # single real promise is late by 2.
    entry_4870 = by_code["4870"]
    assert entry_4870["default_promise"] == 3
    assert entry_4870["with_promise"] == 1
    assert entry_4870["on_time"] == 0
    assert entry_4870["late"] == 1
    assert entry_4870["on_time_rate"] == 0.0
    assert entry_4870["avg_days_late"] == 2.0
    assert entry_4870["max_days_late"] == 2

    # 4851: both promises on or before the order date — no real
    # promise, so a null rate.
    entry_4851 = by_code["4851"]
    assert entry_4851["with_promise"] == 0
    assert entry_4851["default_promise"] == 2
    assert entry_4851["on_time_rate"] is None
    assert entry_4851["avg_days_late"] is None
    assert entry_4851["max_days_late"] is None

    # 4887: the promised receipt without an ordered_on is neither a
    # real promise nor a default — only the normal receipt is
    # promised (late by 2), and the lead-time statistics skip the
    # ordered_on-less receipts.
    entry_4887 = by_code["4887"]
    assert entry_4887["receipts"] == 3
    assert entry_4887["with_promise"] == 1
    assert entry_4887["default_promise"] == 0
    assert entry_4887["late"] == 1
    assert entry_4887["on_time_rate"] == 0.0
    assert entry_4887["avg_days_late"] == 2.0
    assert entry_4887["max_days_late"] == 2
    assert entry_4887["avg_lead_time_days"] == 7.0
    assert entry_4887["p90_lead_time_days"] == 7
    assert entry_4887["last_received_on"] == "2026-06-08"

    # 4890: no receipt has an ordered_on — the entry still computes
    # (no crash): the promised receipt is not a real promise, the rate
    # is null, and the lead-time fields fall back to 0 / 0.0.
    entry_4890 = by_code["4890"]
    assert entry_4890["receipts"] == 2
    assert entry_4890["with_promise"] == 0
    assert entry_4890["default_promise"] == 0
    assert entry_4890["on_time_rate"] is None
    assert entry_4890["avg_days_late"] is None
    assert entry_4890["max_days_late"] is None
    assert entry_4890["avg_lead_time_days"] == 0.0
    assert entry_4890["p90_lead_time_days"] == 0
    assert entry_4890["last_received_on"] == "2026-04-08"

    # The ordering rule is unchanged: on_time_rate ascending with nulls
    # last — 4851 and 4890 (both null) sort after 4870's and
    # 4887's 0.0.
    assert [e["supplier_code"] for e in result["suppliers"]] == [
        "4870",
        "4887",
        "4851",
        "4890",
    ]

    # -- 2. The route ----------------------------------------------------
    # 401 without a token; 200 with the suite's authenticated client,
    # asked for the fixed AS_OF (never the server's clock): the body
    # carries the new default_promise field.
    anon = TestClient(app)
    assert anon.get(URL).status_code == 401

    response = client.get(URL, params=ROUTE_PARAMS)
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED
    assert response.json()["as_of"] == AS_OF.isoformat()
    for entry in response.json()["suppliers"]:
        assert "default_promise" in entry
