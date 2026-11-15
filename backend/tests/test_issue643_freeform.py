"""Issue #643 proof: release tracking judges lateness by the expected date, like Expedite.

``app.aggregate.tracking.release_tracking`` reads the supplier
performance once per call — ``supplier_performance(as_of=today)``,
never once per order — and judges every line's expected date the way
``app.recommend.expedite`` does: a promise later than the order's
ERP date is the supplier's own commitment and stands as the expected
date; otherwise the promised date is the ERP's default and the
expected date is the order's ERP date plus the supplier's
``avg_lead_time_days`` (the entry whose ``supplier_code`` equals the
release row's ``supplier_code``, null when it is missing) rounded to
whole days half-up, falling back to the promised date when the
supplier has no figure.  A line counts as late exactly when its
expected date is before ``today`` (not cancelled, not completed,
short of receipt, and a blank or malformed promise is never late),
and ``days_late`` is ``today`` minus the earliest late line's
expected date.  Every order gains ``expected_on`` (the earliest
expected date of the lines that are active, not completed and short
of receipt, ISO, null when there are none) and
``promise_is_default`` (true for the ERP default, false for the
supplier's own date, null with ``expected_on``), and the response
model serves both on the route.

Proven here with the reads ``release_tracking`` makes
monkeypatched — the release rows, the ERP order records, and
``supplier_performance`` (the two read-model reads it makes are
patched to empty, so the proof never depends on the stored suppliers
or items) — with ``today`` fixed at 2026-10-26:

  * a default promise: an open order dated 2026-10-21 with a line
    promised 2026-10-21, short of receipt, whose supplier carries
    ``avg_lead_time_days`` 14.4 — state ``open`` (not ``late``),
    ``days_late`` null, ``expected_on`` "2026-11-04",
    ``promise_is_default`` true, ``counts["late"]`` 0;
  * a supplier's own promise: the same order with the line promised
    2026-10-22 — state ``late`` with ``days_late`` 4,
    ``expected_on`` "2026-10-22", ``promise_is_default`` false;
  * no lead time on record: a default promise whose supplier is
    missing from ``supplier_performance`` — the expected date falls
    back to the promised date, the order is ``late`` with
    ``days_late`` 5 and ``promise_is_default`` true;
  * one read: ``supplier_performance`` called exactly once for three
    orders;
  * an empty performance read: the same three orders with the
    performance read answering no entries — the expected dates fall
    back to the promised dates, and every order still carries
    ``expected_on`` and ``promise_is_default``, in the aggregate
    and in the route body — never dropped with an empty read;
  * the route: ``GET /api/aggregate/releases`` with the planner
    token returns ``expected_on`` and ``promise_is_default`` on each
    order.
"""

from datetime import date

from fastapi.testclient import TestClient

import app.aggregate.tracking as tracking
import app.recommend.expedite as expedite_module
from app.feed.acumatica_client import AcumaticaClient
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)

TODAY = date(2026, 10, 26)
URL_RELEASES = "/api/aggregate/releases"

# The proof's released orders: the default promise with the supplier
# on record, the supplier's own promise, and the default promise
# with the supplier off record.
ORDER_DEFAULT = "PO006431"
ORDER_PROMISED = "PO006432"
ORDER_NO_LEAD = "PO006433"

# The one entry the fake performance read carries: the 14.4-day
# average lead time of supplier "6995".
SUPPLIER_ENTRY = {
    "supplier_code": "6995",
    "receipts": 2,
    "with_promise": 1,
    "default_promise": 1,
    "on_time": 1,
    "late": 0,
    "on_time_rate": 1.0,
    "avg_days_late": None,
    "max_days_late": None,
    "avg_lead_time_days": 14.4,
    "p90_lead_time_days": 15,
    "last_received_on": "2026-09-30",
}


def _release_row(order_nbr, supplier_code, created_at):
    """One plan_releases row released as ``order_nbr``."""
    return {
        "id": 1,
        "month": "2026-11",
        "site_code": "154",
        "supplier_code": supplier_code,
        "origin": f"AIERP plan 2026-11 site 154 vendor {supplier_code}",
        "warehouse_code": "154",
        "vendor_code": supplier_code,
        "order_nbr": order_nbr,
        "outcome": "created",
        "error": None,
        "line_count": 1,
        "value": "123.45",
        "lines": [
            {
                "item_code": "MMM",
                "quantity": 10,
                "unit_cost": "2.00",
                "value": "20.00",
            }
        ],
        "actor": "admin",
        "created_at": created_at,
        "updated_at": created_at,
    }


def _erp_record(promised):
    """One ERP order record: dated 2026-10-21, open, one line short
    of receipt, the line's promised date carried by ``promised``."""
    return {
        "status": "Open",
        "hold": False,
        "date": "2026-10-21",
        "promised_on": promised,
        "total": 123.45,
        "lines": [
            {
                "item_code": "MMM",
                "warehouse_code": "154",
                "order_qty": 10.0,
                "received_qty": 0.0,
                "completed": False,
                "cancelled": False,
                "promised": promised,
            }
        ],
    }


class _FixedDate(date):
    """date with today() fixed at 2026-10-26 (the route's default)."""

    @classmethod
    def today(cls) -> date:
        return cls(2026, 10, 26)


def test_issue643_freeform(monkeypatch, user_headers):
    # -- The reads release_tracking makes, monkeypatched -------------
    calls = {"supplier_performance": 0}
    # The performance read's entries: the one the proof's scenarios
    # need, emptied for the empty-read proof.
    performance_entries = [dict(SUPPLIER_ENTRY)]

    def fake_supplier_performance(as_of=None, window_days=365):
        calls["supplier_performance"] += 1
        # The read is made with the resolved as_of — today.
        assert as_of == TODAY
        return {
            "as_of": as_of.isoformat(),
            "window_days": window_days,
            "suppliers": [dict(entry) for entry in performance_entries],
        }

    # The performance read the aggregate makes — the same
    # ``supplier_performance`` Expedite reuses.
    monkeypatch.setattr(
        expedite_module, "supplier_performance", fake_supplier_performance
    )
    # The two read-model reads the aggregate makes: empty, so no
    # supplier_name and no item_description join the proof.
    monkeypatch.setattr(SupplierPostgresRepository, "list_all", lambda self: [])
    monkeypatch.setattr(InventoryItemPostgresRepository, "list_all", lambda self: [])

    # The ERP order records: one line each, dated 2026-10-21 and
    # short of receipt; the canned line promise per scenario.
    canned = {
        ORDER_DEFAULT: "2026-10-21",  # the default promise
        ORDER_PROMISED: "2026-10-22",  # the supplier's own promise
        ORDER_NO_LEAD: "2026-10-21",  # the default promise, "9999"
    }

    def fake_read_purchase_orders(self, order_nbrs):
        return {nbr: _erp_record(canned[nbr]) for nbr in order_nbrs if nbr in canned}

    monkeypatch.setattr(
        AcumaticaClient, "read_purchase_orders", fake_read_purchase_orders
    )

    served = {
        row["order_nbr"]: row
        for row in (
            _release_row(ORDER_DEFAULT, "6995", "2026-10-21T09:00:00+00:00"),
            _release_row(ORDER_PROMISED, "6995", "2026-10-22T09:00:00+00:00"),
            _release_row(ORDER_NO_LEAD, "9999", "2026-10-23T09:00:00+00:00"),
        )
    }
    wanted: list = []
    monkeypatch.setattr(
        tracking,
        "_load_release_rows",
        lambda: [dict(served[nbr]) for nbr in wanted],
    )

    # -- 1. A default promise ----------------------------------------
    # The promise on the order day is the ERP's default, not a
    # commitment: the expected date is the order date plus 14 days
    # (14.4 half-up), after today — the order is open, not late.
    wanted[:] = [ORDER_DEFAULT]
    calls["supplier_performance"] = 0
    result = tracking.release_tracking(today=TODAY)
    assert calls["supplier_performance"] == 1
    assert result["as_of"] == "2026-10-26"
    assert result["counts"]["late"] == 0
    [order] = result["orders"]
    assert order["order_nbr"] == ORDER_DEFAULT
    assert order["state"] == "open"
    assert order["days_late"] is None
    assert order["expected_on"] == "2026-11-04"
    assert order["promise_is_default"] is True
    # The unchanged fields stand: the promise as promised_on, no
    # waiting, the open status.
    assert order["promised_on"] == "2026-10-21"
    assert order["days_waiting"] is None
    assert order["status"] == "Open"

    # -- 2. A supplier's own promise -----------------------------------
    # The same order with the line promised 2026-10-22, after the
    # order date: the supplier's own commitment stands as the
    # expected date, four days before today — late by 4.
    wanted[:] = [ORDER_DEFAULT]
    canned[ORDER_DEFAULT] = "2026-10-22"
    calls["supplier_performance"] = 0
    result = tracking.release_tracking(today=TODAY)
    assert calls["supplier_performance"] == 1
    assert result["counts"]["late"] == 1
    [order] = result["orders"]
    assert order["state"] == "late"
    assert order["days_late"] == 4
    assert order["expected_on"] == "2026-10-22"
    assert order["promise_is_default"] is False
    assert order["promised_on"] == "2026-10-22"

    # -- 3. No lead time on record -------------------------------------
    # The default promise of a supplier missing from the performance
    # read: the expected date falls back to the promised date, five
    # days before today — late by 5, the promise the ERP's default.
    wanted[:] = [ORDER_NO_LEAD]
    calls["supplier_performance"] = 0
    result = tracking.release_tracking(today=TODAY)
    assert calls["supplier_performance"] == 1
    assert result["counts"]["late"] == 1
    [order] = result["orders"]
    assert order["order_nbr"] == ORDER_NO_LEAD
    assert order["state"] == "late"
    assert order["days_late"] == 5
    assert order["expected_on"] == "2026-10-21"
    assert order["promise_is_default"] is True

    # -- 4. One read -----------------------------------------------------
    # Three orders in one call: the supplier performance is read
    # exactly once, not once per order.
    wanted[:] = [ORDER_DEFAULT, ORDER_PROMISED, ORDER_NO_LEAD]
    canned[ORDER_DEFAULT] = "2026-10-21"
    calls["supplier_performance"] = 0
    result = tracking.release_tracking(today=TODAY)
    assert calls["supplier_performance"] == 1
    assert [o["order_nbr"] for o in result["orders"]] == [
        ORDER_PROMISED,  # late, oldest release first within the state
        ORDER_NO_LEAD,  # late
        ORDER_DEFAULT,  # open
    ]
    assert result["counts"] == {
        "awaiting_approval": 0,
        "late": 2,
        "partly_received": 0,
        "received": 0,
    }

    # -- 5. The route -----------------------------------------------------
    # The route's default today is fixed at 2026-10-26 too, so the
    # same canned data answers through the response model — with the
    # planner's (non-admin) token.
    monkeypatch.setattr(tracking, "date", _FixedDate)
    from app.main import app

    with TestClient(app, headers=user_headers) as signed:
        response = signed.get(URL_RELEASES)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] == "2026-10-26"
    by_nbr = {o["order_nbr"]: o for o in body["orders"]}
    # Every order carries the two new fields.
    for nbr, order in by_nbr.items():
        assert "expected_on" in order, nbr
        assert "promise_is_default" in order, nbr
    assert by_nbr[ORDER_DEFAULT]["expected_on"] == "2026-11-04"
    assert by_nbr[ORDER_DEFAULT]["promise_is_default"] is True
    assert by_nbr[ORDER_PROMISED]["expected_on"] == "2026-10-22"
    assert by_nbr[ORDER_PROMISED]["promise_is_default"] is False
    assert by_nbr[ORDER_NO_LEAD]["expected_on"] == "2026-10-21"
    assert by_nbr[ORDER_NO_LEAD]["promise_is_default"] is True

    # -- 6. An empty performance read --------------------------------
    # The read answers with no entries at all — the expected dates
    # fall back to the promised dates, and every order still carries
    # expected_on and promise_is_default: never dropped, in the
    # aggregate and in the route body.
    performance_entries[:] = []
    wanted[:] = [ORDER_DEFAULT, ORDER_PROMISED, ORDER_NO_LEAD]
    calls["supplier_performance"] = 0
    result = tracking.release_tracking(today=TODAY)
    assert calls["supplier_performance"] == 1
    by_nbr = {order["order_nbr"]: order for order in result["orders"]}
    for nbr, order in by_nbr.items():
        assert "expected_on" in order, nbr
        assert "promise_is_default" in order, nbr
    # The default promises, no lead time on record: the promised
    # date is the expected date — late by 5, the ERP's default.
    assert by_nbr[ORDER_DEFAULT]["state"] == "late"
    assert by_nbr[ORDER_DEFAULT]["days_late"] == 5
    assert by_nbr[ORDER_DEFAULT]["expected_on"] == "2026-10-21"
    assert by_nbr[ORDER_DEFAULT]["promise_is_default"] is True
    # The supplier's own promise stands whatever the read — late by
    # 4, not the default.
    assert by_nbr[ORDER_PROMISED]["state"] == "late"
    assert by_nbr[ORDER_PROMISED]["days_late"] == 4
    assert by_nbr[ORDER_PROMISED]["expected_on"] == "2026-10-22"
    assert by_nbr[ORDER_PROMISED]["promise_is_default"] is False
    assert by_nbr[ORDER_NO_LEAD]["expected_on"] == "2026-10-21"
    assert by_nbr[ORDER_NO_LEAD]["promise_is_default"] is True
    assert result["counts"]["late"] == 3
    # Through the route, the body carries both fields on every
    # order, the empty read included.
    with TestClient(app, headers=user_headers) as signed:
        response = signed.get(URL_RELEASES)
    assert response.status_code == 200, response.text
    for order in response.json()["orders"]:
        assert "expected_on" in order, order["order_nbr"]
        assert "promise_is_default" in order, order["order_nbr"]
