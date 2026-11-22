"""Issue #1559 proof: the tracked lines carry the release's stored source.

AC-1 — the tracked lines: every line Acumatica reports for a
released order gains ``source`` — the source the order's release
row (the newest ``created`` or ``exists`` row of its order number)
stored for a line of the same ``item_code`` — while a line the
release stored no source for gains no ``source`` key: an item
added to the order in Acumatica, the aggregate release's lines
and a release stored before the field.  Nothing else changes:
every other field of the order and its lines (``item_description``
included), the state, the counts and the order of the orders and
of their lines.

Proven here with the reads ``release_tracking`` makes
monkeypatched, the way
``tests/test_issue643_freeform.py`` stands in for the same names —
the release rows, the ERP order records,
``app.recommend.expedite.supplier_performance``, the suppliers and
inventory items read models (``list_all``, patched to empty for
the aggregate, the items one holding B's description for the
descriptions proof) and ``tracking.date`` for the route — with
``today`` fixed at 2026-11-10:

  * the newest row decides: order PO015591's newest release row
    stores B, R and T with the sources ``buffer``, ``reorder_point``
    and ``top_up`` while an older row of the same order number
    stores B as ``reorder_point`` — the tracked B, R and T carry
    the newest row's sources, and the X Acumatica reports that the
    release did not store carries no ``source`` key;
  * no stored source, no key: order PO015592's release row stores
    its one line without a source — the tracked line carries no
    ``source`` key either;
  * nothing else changes: every line without its ``source`` key
    equals the line Acumatica reported, and a line with it equals
    the reported line plus the key;
  * with descriptions: the inventory items read model holding B's
    description — B carries both its ``item_description`` and
    ``"buffer"``;
  * the route: ``GET /api/aggregate/releases`` with the planner's
    token, as #643's proof calls it, answers the same — the three
    sources, and no ``source`` key on X's line or PO-M's.

AC-2 — the line model: ``ReleaseTrackingLine`` gains
``source: str | None = None``; the route already answers with
``response_model_exclude_unset=True``, so a line without the key
answers without it, the way a line without ``item_description``
does — proven through the route below.
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

TODAY = date(2026, 11, 10)
URL_RELEASES = "/api/aggregate/releases"

# The proof's released orders: PO-A, whose newest row's stored
# sources decide (an older row of the same order number stores B
# differently), and PO-M, whose stored line carries no source.
ORDER_A = "PO015591"
ORDER_M = "PO015592"

# The items: B, R and T as the release stored them, X added to the
# order in Acumatica after the release, and M of PO-M.
ITEM_B, ITEM_R, ITEM_T, ITEM_X, ITEM_M = "B", "R", "T", "X", "M"

# The ERP dates: the order dated before today, every line promised
# after it and after today — open, not late, short of receipt.
ERP_DATE = "2026-11-05"
PROMISED = "2026-11-20"


def _erp_line(item_code):
    """One ERP order line: open, short of receipt, promised after."""
    return {
        "item_code": item_code,
        "warehouse_code": "154",
        "order_qty": 10.0,
        "received_qty": 0.0,
        "completed": False,
        "cancelled": False,
        "promised": PROMISED,
    }


def _erp_record(item_codes):
    """One ERP order record: open, dated, the lines in ERP order."""
    return {
        "status": "Open",
        "hold": False,
        "date": ERP_DATE,
        "promised_on": PROMISED,
        "total": 123.45,
        "lines": [_erp_line(item_code) for item_code in item_codes],
    }


def _stored_line(item_code, source=None):
    """One line of a stored release snapshot; ``source`` omitted
    while it is None, the way a snapshot stored before the field
    (and the aggregate release's lines) leave it out."""
    line = {
        "item_code": item_code,
        "quantity": 10,
        "unit_cost": "2.00",
        "value": "20.00",
    }
    if source is not None:
        line["source"] = source
    return line


def _release_row(order_nbr, lines, created_at):
    """One plan_releases row released as ``order_nbr``."""
    return {
        "id": 1,
        "month": "2026-11",
        "site_code": "154",
        "supplier_code": "6995",
        "origin": "AIERP plan 2026-11 site 154 vendor 6995",
        "warehouse_code": "154",
        "vendor_code": "6995",
        "order_nbr": order_nbr,
        "outcome": "created",
        "error": None,
        "line_count": len(lines),
        "value": "123.45",
        "lines": lines,
        "actor": "admin",
        "created_at": created_at,
        "updated_at": created_at,
    }


class _FixedDate(date):
    """date with today() fixed at 2026-11-10 (the route's default)."""

    @classmethod
    def today(cls) -> date:
        return cls(2026, 11, 10)


def test_issue1559_freeform(monkeypatch, user_headers):
    # -- The reads release_tracking makes, monkeypatched -------------
    # The performance read: answered with no entries, the way the
    # read answers when the window holds no receipts — every
    # expected date falls back to the promised date.
    def fake_supplier_performance(as_of=None, window_days=365):
        # The read is made with the resolved as_of — today.
        assert as_of == TODAY
        return {
            "as_of": as_of.isoformat(),
            "window_days": window_days,
            "suppliers": [],
        }

    monkeypatch.setattr(
        expedite_module, "supplier_performance", fake_supplier_performance
    )
    # The two read-model reads the aggregate makes: empty, so no
    # supplier_name and no item_description join the aggregate
    # proof; the items one gains B's description below.
    monkeypatch.setattr(SupplierPostgresRepository, "list_all", lambda self: [])
    items_rows: list = []

    def fake_items_list_all(self):
        return [dict(row) for row in items_rows]

    monkeypatch.setattr(
        InventoryItemPostgresRepository, "list_all", fake_items_list_all
    )

    # The ERP order records: PO-A with the four lines Acumatica
    # reports — B, R and T as released, plus X, an item the release
    # did not store — and PO-M with its one line.
    canned = {
        ORDER_A: _erp_record([ITEM_B, ITEM_R, ITEM_T, ITEM_X]),
        ORDER_M: _erp_record([ITEM_M]),
    }
    reported = {
        nbr: {line["item_code"]: line for line in record["lines"]}
        for nbr, record in canned.items()
    }

    def fake_read_purchase_orders(self, order_nbrs):
        return {nbr: canned[nbr] for nbr in order_nbrs if nbr in canned}

    monkeypatch.setattr(
        AcumaticaClient, "read_purchase_orders", fake_read_purchase_orders
    )

    # The release rows, newest first (the audit order): PO-A's
    # newest row stores B, R and T with the sources the proof
    # checks, PO-M's row its one line without a source, and PO-A's
    # older row of the same order number storing B as
    # ``reorder_point`` — the newest row must decide.
    served = [
        _release_row(
            ORDER_A,
            [
                _stored_line(ITEM_B, "buffer"),
                _stored_line(ITEM_R, "reorder_point"),
                _stored_line(ITEM_T, "top_up"),
            ],
            "2026-11-05T09:00:00+00:00",
        ),
        _release_row(ORDER_M, [_stored_line(ITEM_M)], "2026-11-06T09:00:00+00:00"),
        _release_row(
            ORDER_A,
            [_stored_line(ITEM_B, "reorder_point")],
            "2026-11-01T09:00:00+00:00",
        ),
    ]
    monkeypatch.setattr(
        tracking, "_load_release_rows", lambda: [dict(row) for row in served]
    )

    # -- 1. The newest row's stored sources decide ---------------------
    result = tracking.release_tracking(today=TODAY)
    assert result["as_of"] == TODAY.isoformat()
    # The orders and their lines keep their order: PO-A, released
    # oldest, first, both open.
    assert [order["order_nbr"] for order in result["orders"]] == [
        ORDER_A,
        ORDER_M,
    ]
    assert [order["state"] for order in result["orders"]] == ["open", "open"]
    assert result["counts"] == {
        "awaiting_approval": 0,
        "late": 0,
        "partly_received": 0,
        "received": 0,
    }
    order_a, order_m = result["orders"]
    lines_a = {line["item_code"]: line for line in order_a["lines"]}
    # The ERP line order is kept, B, R and T from the newest row's
    # stored sources — the older row's ``reorder_point`` for B does
    # not decide — and X, the item the release did not store,
    # without the key.
    assert [line["item_code"] for line in order_a["lines"]] == [
        ITEM_B,
        ITEM_R,
        ITEM_T,
        ITEM_X,
    ]
    assert lines_a[ITEM_B]["source"] == "buffer"
    assert lines_a[ITEM_R]["source"] == "reorder_point"
    assert lines_a[ITEM_T]["source"] == "top_up"
    assert "source" not in lines_a[ITEM_X]
    # PO-M's stored line carries no source: its tracked line gains
    # no key.
    [line_m] = order_m["lines"]
    assert line_m["item_code"] == ITEM_M
    assert "source" not in line_m

    # -- 2. Nothing else changes --------------------------------------
    # Every line equals the line Acumatica reported, its source key
    # set aside — every other field, item_description included, is
    # untouched (the read models are empty, so the key is absent).
    for order in result["orders"]:
        for line in order["lines"]:
            without_source = {
                key: value for key, value in line.items() if key != "source"
            }
            assert without_source == reported[order["order_nbr"]][line["item_code"]]

    # -- 3. With descriptions -----------------------------------------
    # The items read model holds B's description: B carries both its
    # item_description and its source, the other lines the null
    # description, X still without the source key.
    items_rows.append({"item_code": ITEM_B, "description": "The buffer item"})
    result = tracking.release_tracking(today=TODAY)
    order_a = result["orders"][0]
    lines_a = {line["item_code"]: line for line in order_a["lines"]}
    assert lines_a[ITEM_B]["item_description"] == "The buffer item"
    assert lines_a[ITEM_B]["source"] == "buffer"
    assert lines_a[ITEM_R]["item_description"] is None
    assert lines_a[ITEM_R]["source"] == "reorder_point"
    assert lines_a[ITEM_T]["item_description"] is None
    assert lines_a[ITEM_T]["source"] == "top_up"
    assert lines_a[ITEM_X]["item_description"] is None
    assert "source" not in lines_a[ITEM_X]
    [line_m] = result["orders"][1]["lines"]
    assert line_m["item_description"] is None
    assert "source" not in line_m

    # -- 4. The route ---------------------------------------------------
    # The route's default today is fixed at 2026-11-10 too, so the
    # same canned data answers through the response model — with
    # the planner's (non-admin) token, as #643's proof calls it.
    monkeypatch.setattr(tracking, "date", _FixedDate)
    from app.main import app

    with TestClient(app, headers=user_headers) as signed:
        response = signed.get(URL_RELEASES)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] == TODAY.isoformat()
    assert [order["order_nbr"] for order in body["orders"]] == [
        ORDER_A,
        ORDER_M,
    ]
    lines_a = {line["item_code"]: line for line in body["orders"][0]["lines"]}
    # The three sources answer on the wire — the model carries the
    # field — and B answers with its description beside its source.
    assert lines_a[ITEM_B]["source"] == "buffer"
    assert lines_a[ITEM_B]["item_description"] == "The buffer item"
    assert lines_a[ITEM_R]["source"] == "reorder_point"
    assert lines_a[ITEM_T]["source"] == "top_up"
    # ...while exclude_unset keeps the key off the lines the release
    # stored no source for, the way a line without item_description
    # answers without it.
    assert "source" not in lines_a[ITEM_X]
    [line_m] = body["orders"][1]["lines"]
    assert "source" not in line_m
