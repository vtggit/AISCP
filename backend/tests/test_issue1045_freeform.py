"""Issue #1045 proof: the move figure prices the gap, not the order.

``GET /api/cross-erp/items`` prices ``suggested_move_units`` from the
short side's need — each short-side site whose ``stockout_category``
is not null contributes its gap, its ``reorder_point_units`` minus
its ``on_hand_units``, never below 0, a site with a null reorder
point counting 0 — capped by the excess side's summed
``excess_units``, rounded down to a whole number and never below 0,
and null while ``opportunity`` is false.  The short side's
``suggested_order_quantity`` no longer takes part: a site covered by
an order (a suggested order of 0) still contributes its whole gap.

The item-sites, the on-hand rows and the one stored item mapping
(``MOVE-1`` to ``MOVE-1``, the item held at ``154`` under
``acumatica`` and at ``WH`` under ``odoo``) are seeded the way
``test_issue1039_freeform`` seeds them, but the three shared figure
functions — ``recommendations_for_all``, ``stockout_ranked`` and
``excess_ranked`` in ``app.recommend.cross_erp`` — are replaced with
stand-ins answering fixed rows, so every figure is exact:

  * covered by an order, still short — the short-side site ``154``
    holds 8 on hand with a reorder point of 40 and a suggested order
    of 0 and is flagged ``likely``; the excess side holds 40 in
    excess; ``suggested_move_units`` is ``32`` (the gap), not 0;
  * capped by the excess — the same site against an excess side
    holding 20.5 in excess gives ``20``;
  * no reorder point — the same site with a null reorder point gives
    ``0`` while ``opportunity`` stays true;
  * no opportunity — with no site flagged the figure is null.
"""

from datetime import date

from fastapi.testclient import TestClient

from app.main import app
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository

ADMIN_TOKEN = "dev-secret-token:admin"

URL = "/api/cross-erp/items"
MAPPINGS_URL = "/api/erp-mappings"

FIXED_TODAY = date(2026, 10, 2)
AS_OF_STR = FIXED_TODAY.isoformat()
ACU_SITE = "154"
ODO_SITE = "WH"
REORDER_POINT_UNITS = 40


def _fixed_date():
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    return _FixedDate


def _stand_ins(monkeypatch, reorder_point_units, excess_units, flagged):
    """The stand-ins answering fixed rows for the three shared figure
    functions in ``app.recommend.cross_erp``.

    ``recommendations_for_all`` answers the short site's row (a
    suggested order of 0 — the site is covered by an order),
    ``stockout_ranked`` flags the short site ``likely`` while
    *flagged*, and ``excess_ranked`` answers the excess site's
    *excess_units*.
    """

    def recommendations_for_all(as_of=None):
        return [
            {
                "source_system": "acumatica",
                "item_code": "MOVE-1",
                "site_code": ACU_SITE,
                "inputs": {"d": 2.0},
                "status": "ok",
                "reorder_point_units": reorder_point_units,
                "suggested_order_quantity": 0,
            }
        ]

    def stockout_ranked(as_of=None):
        if not flagged:
            return []
        return [
            {
                "item_code": "MOVE-1",
                "site_code": ACU_SITE,
                "category": "likely",
                "rank": 1,
            }
        ]

    def excess_ranked(as_of=None):
        return [
            {
                "item_code": "MOVE-1",
                "site_code": ODO_SITE,
                "excess_units": excess_units,
                "rank": 1,
            }
        ]

    monkeypatch.setattr(
        "app.recommend.cross_erp.recommendations_for_all",
        recommendations_for_all,
    )
    monkeypatch.setattr("app.recommend.cross_erp.stockout_ranked", stockout_ranked)
    monkeypatch.setattr("app.recommend.cross_erp.excess_ranked", excess_ranked)


def _on_hand(acu_units, odo_units):
    """Re-stamp the pair's on-hand rows (the way 1039 re-stamps its
    snapshot)."""
    OnHandPostgresRepository().upsert_many(
        [
            {
                "item_code": "MOVE-1",
                "site_code": ACU_SITE,
                "quantity_on_hand": acu_units,
                "as_of": FIXED_TODAY,
                "source_system": "acumatica",
            },
            {
                "item_code": "MOVE-1",
                "site_code": ODO_SITE,
                "quantity_on_hand": odo_units,
                "as_of": FIXED_TODAY,
                "source_system": "odoo",
            },
        ]
    )


def _row(admin):
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["as_of"] == AS_OF_STR
    rows = answer["rows"]
    assert [row["source_code"] for row in rows] == ["MOVE-1"]
    return rows[0]


def test_issue1045_freeform(client, monkeypatch):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    monkeypatch.delenv("EXCESS_COVER_MONTHS", raising=False)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # -- Item-sites and the one stored item mapping (the way 1039
    #    seeds them). ----------------------------------------------------
    ItemSitePostgresRepository().upsert_many(
        [
            {
                "item_code": "MOVE-1",
                "site_code": ACU_SITE,
                "unit_cost": 1.0,
                "source_system": "acumatica",
            },
            {
                "item_code": "MOVE-1",
                "site_code": ODO_SITE,
                "unit_cost": 1.0,
                "source_system": "odoo",
            },
        ]
    )
    _on_hand(8.0, 900.0)
    response = admin.put(
        MAPPINGS_URL,
        json={
            "kind": "item",
            "target_system": "odoo",
            "source_code": "MOVE-1",
            "target_code": "MOVE-1",
        },
    )
    assert response.status_code == 200, response.text

    # -- Covered by an order, still short: the short site's suggested
    #    order is 0 while its gap (40 - 8) is 32 and the excess side
    #    holds 40 — the figure is the gap, 32, not 0. -------------------
    _stand_ins(monkeypatch, REORDER_POINT_UNITS, 40.0, True)
    row = _row(admin)
    assert row["opportunity"] is True
    assert row["short_side"] == 0
    assert row["excess_side"] == 1
    short_site = row["sides"][0]["sites"][0]
    excess_site = row["sides"][1]["sites"][0]
    assert short_site["site_code"] == ACU_SITE
    assert short_site["on_hand_units"] == 8
    assert short_site["reorder_point_units"] == REORDER_POINT_UNITS
    assert short_site["suggested_order_quantity"] == 0
    assert short_site["stockout_category"] == "likely"
    assert excess_site["site_code"] == ODO_SITE
    assert excess_site["excess_units"] == 40
    assert row["suggested_move_units"] == REORDER_POINT_UNITS - 8  # 32, the gap
    assert row["suggested_move_units"] == 32
    assert row["suggested_move_units"] != 0  # not the suggested order
    assert type(row["suggested_move_units"]) is int  # a whole number in JSON

    # -- Capped by the excess: the same gap against an excess side
    #    holding 20.5 — the excess caps and floors to 20. --------------
    _stand_ins(monkeypatch, REORDER_POINT_UNITS, 20.5, True)
    row = _row(admin)
    assert row["opportunity"] is True
    short_site = row["sides"][0]["sites"][0]
    excess_site = row["sides"][1]["sites"][0]
    assert short_site["stockout_category"] == "likely"
    assert excess_site["excess_units"] == 20.5
    assert row["suggested_move_units"] == 20  # floor(min(32, 20.5))
    assert type(row["suggested_move_units"]) is int  # a whole number in JSON

    # -- No reorder point: the gap counts 0 while the flag stays on. ----
    _stand_ins(monkeypatch, None, 40.0, True)
    row = _row(admin)
    assert row["opportunity"] is True
    assert row["short_side"] == 0
    assert row["excess_side"] == 1
    short_site = row["sides"][0]["sites"][0]
    excess_site = row["sides"][1]["sites"][0]
    assert short_site["reorder_point_units"] is None
    assert short_site["stockout_category"] == "likely"
    assert excess_site["excess_units"] == 40
    assert row["suggested_move_units"] == 0
    assert type(row["suggested_move_units"]) is int  # a whole number in JSON

    # -- No opportunity: no site is flagged — the figure is null. -------
    _stand_ins(monkeypatch, REORDER_POINT_UNITS, 40.0, False)
    row = _row(admin)
    for side in row["sides"]:
        for site in side["sites"]:
            assert site["stockout_category"] is None
    assert row["opportunity"] is False
    assert row["short_side"] is None
    assert row["excess_side"] is None
    assert row["suggested_move_units"] is None
