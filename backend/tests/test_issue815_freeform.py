"""Issue #815 proof: Odoo business keys keep their own line identity.

``app.recommend.expedite._parse_business_key`` parses a business key
whose first part is ``odoo`` and which carries at least four
colon-separated parts — ``odoo:<database>:<model>:<record id>`` —
the other way round: the model (the third part) is the
``order_type``, the database (the second part) the ``order_nbr``
and the LAST part (the record id) the ``line_nbr``; every other key
parses exactly as before (``OrderType:OrderNbr:LineNbr``), and a
three-part ``odoo:...`` key still parses the Acumatica way.  Each
Odoo line in ``expedite_list`` therefore keeps its own
``line_nbr``, and each Odoo ``expedite_after_stockout`` /
``expedite_late`` row in ``GET /api/exceptions`` gets its own
``<kind>:<order_nbr>/<line_nbr>`` key — ``expedite_late:demo/30``
and ``expedite_late:demo/31`` for the two lines proven here,
never one key shared by the whole database.

Proven here, in order, with every read monkeypatched to fixed rows
(no database) and the server's "today" (the module's ``_today``
seam) monkeypatched to the fixed ``AS_OF`` 2026-10-23 so the proof
never depends on the real clock:

  * parsing: ``odoo:demo:purchase.order.line:30`` parses to
    ``("purchase.order.line", "demo", "30")`` while
    ``RO:000101:1`` still parses to ``("RO", "000101", "1")`` and
    the three-part ``odoo:demo:x`` still parses the Acumatica
    way, ``("odoo", "demo", "x")``;
  * distinct lines: two late Odoo lines whose keys end ``:30`` and
    ``:31`` — seeded the way the issue 607 proof seeds open lines —
    come back out of ``expedite_list`` as two rows with
    ``line_nbr`` "30" and "31";
  * the route: ``GET /api/exceptions`` (the four other sources
    faked empty) answers the two rows with the distinct keys
    ``expedite_late:demo/30`` and ``expedite_late:demo/31``;
  * hostile magnitude: one line's on hand pushed to 1e308 against a
    daily demand of 1e-300 (the cover quotient overflows to
    infinity) - the line still comes back, with ``days_of_cover``
    null and the projected stockout on ``date.max``, and
    ``GET /api/expedite`` answers 200 with a parseable body, never
    a 500 and never a non-finite number.
"""

import math
from datetime import UTC, date, datetime

import app.recommend.expedite as expedite_module
from app.api import exceptions as exceptions_api
from app.recommend.expedite import _parse_business_key, expedite_list
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)

AS_OF = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def _open_line(key, item, supplier, ordered, promised, open_units):
    return {
        "id": f"uuid-{key}",
        "business_key": key,
        "item_code": item,
        "site_code": "154",
        "supplier_code": supplier,
        "order_type": key.split(":")[0],
        "open_units": open_units,
        "promised_on": promised,
        "ordered_on": ordered,
        "as_of": "2026-10-23T00:00:00+00:00",
        "source_system": "odoo",
        "synced_at": "2026-10-23T00:00:00+00:00",
    }


OPEN_LINES = [
    # Late: promised 2026-10-21 (after the order) is before as_of.
    _open_line(
        "odoo:demo:purchase.order.line:30",
        "AAA",
        "4883",
        date(2026, 10, 14),
        date(2026, 10, 21),
        100,
    ),
    # Late: promised 2026-10-20 (after the order) is before as_of.
    _open_line(
        "odoo:demo:purchase.order.line:31",
        "BBB",
        "4883",
        date(2026, 10, 15),
        date(2026, 10, 20),
        50,
    ),
]


def _on_hand(item, quantity):
    return {
        "id": f"uuid-onhand-{item}",
        "item_code": item,
        "site_code": "154",
        "quantity_on_hand": quantity,
        "as_of": "2026-10-23T00:00:00+00:00",
        "source_system": "odoo",
        "synced_at": "2026-10-23T00:00:00+00:00",
    }


ON_HAND = [
    _on_hand("AAA", 100),
    _on_hand("BBB", 30),
]


def _item_site(item, unit_cost):
    return {
        "id": f"uuid-itemsite-{item}",
        "item_code": item,
        "site_code": "154",
        "unit_cost": unit_cost,
        "reorder_point": None,
        "safety_stock": None,
        "max_qty": None,
        "order_qty": None,
        "master_lead_time_days": None,
        "source_system": "odoo",
        "synced_at": "2026-10-23T00:00:00+00:00",
    }


ITEM_SITES = [
    _item_site("AAA", 2.0),
    _item_site("BBB", 4.0),
]


def _recommendation(item, d):
    return {
        "status": "ok",
        "item_code": item,
        "site_code": "154",
        "reorder_point_units": 100,
        "inputs": {
            "d": d,
            "sd_d": 1.0,
            "lt": 14.0,
            "sd_lt": 1.0,
            "z": 1.6448536269514722,
            "service_level": 0.95,
        },
    }


RECOMMENDATIONS = [
    _recommendation("AAA", 50),
    _recommendation("BBB", 10),
]

# The supplier performance the proof stands in for (monkeypatched).
SUPPLIER_PERFORMANCE = {
    "as_of": "2026-10-23",
    "window_days": 365,
    "suppliers": [
        {"supplier_code": "4883", "on_time_rate": 0.8, "avg_lead_time_days": 7.0},
    ],
}

EXPECTED = {
    "as_of": "2026-10-23",
    "stale_excluded": 0,
    "lines": [
        # 1. Late (expected 2026-10-21 before as_of); the projected
        #    stockout 2026-10-25 is after the expected date, so not
        #    after the stockout. Soonest projected stockout.
        {
            "order_type": "purchase.order.line",
            "order_nbr": "demo",
            "line_nbr": "30",
            "item_code": "AAA",
            "site_code": "154",
            "supplier_code": "4883",
            "ordered_on": date(2026, 10, 14),
            "promised_on": date(2026, 10, 21),
            "expected_on": date(2026, 10, 21),
            "promise_is_default": False,
            "open_units": 100.0,
            "open_value": 200.0,
            "days_late": 2,
            "on_hand_units": 100.0,
            "d": 50.0,
            "days_of_cover": 2.0,
            "projected_stockout_on": date(2026, 10, 25),
            "reasons": ["late"],
            "supplier_on_time_rate": 0.8,
        },
        # 2. Late (expected 2026-10-20 before as_of); the projected
        #    stockout 2026-10-26 is after the expected date.
        {
            "order_type": "purchase.order.line",
            "order_nbr": "demo",
            "line_nbr": "31",
            "item_code": "BBB",
            "site_code": "154",
            "supplier_code": "4883",
            "ordered_on": date(2026, 10, 15),
            "promised_on": date(2026, 10, 20),
            "expected_on": date(2026, 10, 20),
            "promise_is_default": False,
            "open_units": 50.0,
            "open_value": 200.0,
            "days_late": 3,
            "on_hand_units": 30.0,
            "d": 10.0,
            "days_of_cover": 3.0,
            "projected_stockout_on": date(2026, 10, 26),
            "reasons": ["late"],
            "supplier_on_time_rate": 0.8,
        },
    ],
}


class _NoReleases:
    """The fake for the route's plan releases read model: nothing."""

    def list_newest(self):
        return []


class _NoPushes:
    """The fake for the route's parameter writebacks read model:
    nothing."""

    def list(self, limit=100, offset=0, item_code=None, site_code=None):
        return []


def test_issue815_freeform(monkeypatch, client):
    # -- 1. Parsing -----------------------------------------------------
    # The Odoo form: first part "odoo" with four parts — the model is
    # the order type, the database the order number, the LAST part
    # (the record id) the line number.
    assert _parse_business_key("odoo:demo:purchase.order.line:30") == (
        "purchase.order.line",
        "demo",
        "30",
    )
    # The Acumatica form parses exactly as before.
    assert _parse_business_key("RO:000101:1") == ("RO", "000101", "1")
    # A three-part Odoo key still parses the Acumatica way.
    assert _parse_business_key("odoo:demo:x") == ("odoo", "demo", "x")
    # A two-part key carries no line number, exactly as before.
    assert _parse_business_key("odoo:demo") == ("odoo", "demo", None)

    # -- 2. Distinct lines (every read monkeypatched — no DB) ----------
    calls = {
        "lines": 0,
        "on_hand": 0,
        "item_sites": 0,
        "recommendations": 0,
        "supplier": 0,
    }

    def fake_list_latest(self):
        calls["lines"] += 1
        return [dict(row) for row in OPEN_LINES]

    def fake_on_hand_list_rows(self, limit, offset, **filters):
        calls["on_hand"] += 1
        return [dict(row) for row in ON_HAND]

    def fake_item_site_list_rows(self, limit, offset, **filters):
        calls["item_sites"] += 1
        return [dict(row) for row in ITEM_SITES]

    def fake_recommendations_for_all(as_of=None):
        calls["recommendations"] += 1
        return [dict(row) for row in RECOMMENDATIONS]

    def fake_supplier_performance(as_of=None, window_days=365):
        calls["supplier"] += 1
        return dict(SUPPLIER_PERFORMANCE)

    monkeypatch.setattr(
        OpenPurchaseLinePostgresRepository, "list_latest", fake_list_latest
    )
    monkeypatch.setattr(OnHandPostgresRepository, "list_rows", fake_on_hand_list_rows)
    monkeypatch.setattr(
        ItemSitePostgresRepository, "list_rows", fake_item_site_list_rows
    )
    monkeypatch.setattr(
        expedite_module, "recommendations_for_all", fake_recommendations_for_all
    )
    monkeypatch.setattr(
        expedite_module, "supplier_performance", fake_supplier_performance
    )
    # The two read models stay empty: the rows carry no vendor or
    # description fields, exactly as the proof expects.
    monkeypatch.setattr(expedite_module._suppliers_repo, "list_all", lambda: [])
    monkeypatch.setattr(expedite_module._inventory_items_repo, "list_all", lambda: [])

    # The proof is clock-free: the server's "today" is the frozen
    # AS_OF, so default as_of resolution never depends on the real
    # clock (the route below calls expedite_list with no as_of).
    monkeypatch.setattr(expedite_module, "_today", lambda: AS_OF)

    result = expedite_list(as_of=AS_OF)
    assert result == EXPECTED
    # Each Odoo line keeps its own identity: the record id, not a
    # shared one, is the line number.
    assert [line["line_nbr"] for line in result["lines"]] == ["30", "31"]
    assert [line["order_type"] for line in result["lines"]] == [
        "purchase.order.line",
        "purchase.order.line",
    ]
    assert [line["order_nbr"] for line in result["lines"]] == ["demo", "demo"]
    assert result["stale_excluded"] == 0

    # -- 3. The exceptions route: the two lines keep distinct keys -----
    # The four other sources are faked empty; the expedite source is
    # the real expedite_list, which reads the same monkeypatched
    # seams, so the route answers the two Odoo rows above.
    monkeypatch.setattr(exceptions_api, "_ddmrp_source", lambda: (AS_OF, []))
    monkeypatch.setattr(exceptions_api, "stockout_ranked", lambda as_of=None: [])
    monkeypatch.setattr(exceptions_api, "_plan_releases_repo", _NoReleases())
    monkeypatch.setattr(exceptions_api, "_parameter_writebacks_repo", _NoPushes())
    monkeypatch.setattr(exceptions_api, "_utcnow", lambda: NOW)
    # The best-effort reads (acknowledgements, first-seen) stay empty:
    # every row reads unacknowledged and never captured.
    monkeypatch.setattr(exceptions_api._exception_acks_repo, "list", lambda: [])
    monkeypatch.setattr(exceptions_api._exception_first_seen_repo, "list", lambda: {})

    response = client.get("/api/exceptions")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] == "2026-10-23"
    assert body["sources_failed"] == []
    assert body["counts"] == {"high": 0, "medium": 2, "acknowledged": 0, "new": 0}
    # Two rows, two DISTINCT keys — one per Odoo line, keyed by the
    # database and the record id.
    keys = [row["key"] for row in body["rows"]]
    assert keys == ["expedite_late:demo/30", "expedite_late:demo/31"]
    assert len(set(keys)) == 2
    for key, item, open_units, days_late in (
        ("expedite_late:demo/30", "AAA", "100", 2),
        ("expedite_late:demo/31", "BBB", "50", 3),
    ):
        row = next(r for r in body["rows"] if r["key"] == key)
        assert row["kind"] == "expedite_late"
        assert row["severity"] == "medium"
        assert row["title"] == "Expedite late"
        assert row["item_code"] == item
        assert row["site_code"] == "154"
        assert row["detail"] == f"{open_units} units, {days_late} days late"
        assert row["view"] == "#/expedite"
        assert row["detected_on"] == "2026-10-23"
        assert row["acknowledged"] is False
        assert row["first_seen_on"] is None
        assert row["new"] is False

    # -- 4. Hostile magnitude: the cover quotient overflows -----------
    # One of the lines' on hand is pushed to 1e308 against a daily
    # demand of 1e-300: quantity / d overflows to infinity.  The
    # line still comes back: days_of_cover reads null (never a
    # non-finite number) and the projected stockout lands on
    # date.max (the timedelta overflow the module already absorbs);
    # GET /api/expedite answers 200 with a parseable body, never a
    # 500 and never an Infinity literal.
    monkeypatch.setattr(
        OnHandPostgresRepository,
        "list_rows",
        lambda self, limit, offset, **filters: [
            dict(row) for row in (_on_hand("AAA", 1e308), _on_hand("BBB", 30))
        ],
    )
    monkeypatch.setattr(
        expedite_module,
        "recommendations_for_all",
        lambda as_of=None: [
            dict(row)
            for row in (_recommendation("AAA", 1e-300), _recommendation("BBB", 10))
        ],
    )

    result = expedite_list(as_of=AS_OF)
    by_item = {line["item_code"]: line for line in result["lines"]}
    assert by_item["AAA"]["days_of_cover"] is None
    assert by_item["AAA"]["projected_stockout_on"] == date.max
    assert by_item["AAA"]["reasons"] == ["late"]
    # No non-finite figure anywhere in the answer.
    for line in result["lines"]:
        if line["days_of_cover"] is not None:
            assert math.isfinite(line["days_of_cover"])

    response = client.get("/api/expedite", params={"as_of": AS_OF.isoformat()})
    assert response.status_code == 200, response.text
    cover_by_item = {
        line["item_code"]: line["days_of_cover"] for line in response.json()["lines"]
    }
    assert cover_by_item["AAA"] is None
    assert cover_by_item["BBB"] == 3.0
