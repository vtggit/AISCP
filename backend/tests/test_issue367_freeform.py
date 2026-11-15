"""Issue #367 proof: drift rows carry the demand basis and the other basis.

AC-1 — every row of ``app.recommend.drift.drift_ranked`` gains
``demand_basis`` (the recommendation's value) and ``other_basis``
(the recommendation's ``other_basis`` reduced to exactly its
``basis``, ``reorder_point_units`` and ``safety_stock_units``, and
null when the recommendation's is null).  ``GET /api/drift`` and
``GET /api/drift/by-key`` return both, added to the
``DriftDecisionRow`` response model with a small ``DriftOtherBasis``
model for the nested object; the OpenAPI schema documents both fields
as optional (not required) with the recommendation source's types and
nullability.  The OData ``Drift`` set is unchanged: ``DriftRow``,
the model the set declares, gains neither field, so ``/odata/$metadata``
declares no ``demand_basis`` or ``other_basis`` property in the
``Drift`` entity type and ``/odata/Drift`` rows carry neither key.
Every other drift field, the ranking, the categories, the filters and
paging, and the decision fields are unchanged.

AC-2/AC-3 — the proof runs against the test database, with demand
events, lead-time observations and item-site rows inserted for the
test, and the ``replenishment.demand_basis`` setting at its default
(``history``) and at ``forecast``.  Seeded at fixed dates (as-of
2026-09-30, date.today pinned to 2026-10-02, z 1.6449 at service
level 0.95, LT 10 and sd_LT 2 from three lead-time observations of
8, 10 and 12 days):

* Y367 — 1.0 units a day for 330 days then 2.0 a day for the last 30
  (buckets [30 x 11, 60], total 390), a current reorder point of
  25.0 at 2.0/unit, order quantity 60, and a planner override of the
  first forecast period (2026-10-01 to 2026-10-30) to 100 units:
  the history basis is d 390/360 = 1.083333, sd_d sqrt(2.5) =
  1.581139, safety stock 1.644854 * sqrt(10 * 2.5 + 4 * 1.173611) =
  8.963237, reorder point 19.796570 (9 / 20 whole units); the
  forecast basis is d 100/30 = 3.333333, sd_d 1.2533 * 5 / sqrt(30)
  = 1.144101, safety stock 1.644854 * sqrt(10 * 1.308967 + 4 *
  11.111111) = 12.476420, reorder point 45.809753 (13 / 46 whole
  units) — so the current point of 25.0 is "over" on the history
  basis (5.203430 units, 10.406860 money, rank 1) and "under" on
  the forecast basis;
* X367 — a constant 2.0 a day for 360 days (12 buckets of 60, every
  candidate backtests to MAE 0 so the mean method wins), a current
  point of 25.0 at 1.0/unit and no order quantity: both bases give
  d 2.0, sd_d 0.0, safety stock 1.644854 * 4 = 6.579415, reorder
  point 26.579415 (7 / 27 whole units) — the current point is
  "under" (mu_l 20, sigma_l 4, k 1.25, 0.20234747 short per cycle
  at 12.166667 cycles a year, 30 days of demand assumed as the
  order quantity, -> 2.46189426 units at 1.0/unit, rank 2);
* N367 — one demand event 14 months before the as-of: no_demand, no
  forecast.  Its recommendation is ``not_recommendable_demand`` with
  a null ``other_basis`` (the null source a drift row would carry)
  and it has no drift row, as only "ok" recommendations are ranked.

The checks: with the setting at its default, the two item-sites with
a forecast have drift rows with ``demand_basis`` ``history`` and an
``other_basis`` holding ``basis`` ``forecast`` and the same unit
values as the item-sites' recommendations from
``GET /api/recommendations/by-key``; with the setting at
``forecast``, ``demand_basis`` is ``forecast`` and
``other_basis.basis`` ``history``; the item-site with no forecast
has a null ``other_basis``; ``GET /api/drift/by-key`` returns the
same two fields as the list for the same item-site; ``/odata/$metadata``
has no ``demand_basis`` or ``other_basis`` property in the ``Drift``
entity type and an ``/odata/Drift`` row has neither key; and every
other field of a drift row is unchanged, listed and checked below.
"""

import base64
import math
import statistics
import xml.etree.ElementTree as ElementTree
from datetime import date, timedelta

import pytest

from app.db.connection import get_connection
from app.forecast.basis import MAE_TO_STD
from app.main import app

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
SITE = "SITE-367"
KEY = "replenishment.demand_basis"
Z95 = statistics.NormalDist().inv_cdf(0.95)  # 1.644854
EDM = "http://docs.oasis-open.org/odata/ns/edm"
ODATA_USER = "odata-machine-367"
ODATA_PASSWORD = "feed-secret-367"

# Hand-computed basis values for the two forecast item-sites.
D_H, SD_H = 390.0 / 360.0, math.sqrt(2.5)  # Y367 history inputs
SS_H = Z95 * math.sqrt(10 * SD_H**2 + 4 * D_H**2)
RP_H = 10 * D_H + SS_H
D_F, SD_F = 100.0 / 30.0, MAE_TO_STD * 5.0 / math.sqrt(30)  # Y367 forecast inputs
SS_F = Z95 * math.sqrt(10 * SD_F**2 + 4 * D_F**2)
RP_F = 10 * D_F + SS_F
SS_X, RP_X = Z95 * 4.0, 20.0 + Z95 * 4.0  # X367: both bases
NORMAL = statistics.NormalDist()
SIGMA_X = 4.0
SHORT_X = SIGMA_X * (NORMAL.pdf(1.25) - 1.25 * (1.0 - NORMAL.cdf(1.25)))
CYCLES_X = 365 * 2.0 / 60.0

# Every field a drift row carried before this issue: the pre-existing
# ``drift_ranked`` shape, checked one by one against the hand-computed
# values below (plus the decision enrichment, pinned in test181).
UNCHANGED_DRIFT_FIELDS = (
    "item_code",
    "site_code",
    "as_of",
    "reorder_point",
    "reorder_point_units",
    "safety_stock",
    "arithmetic",
    "current_reorder_point",
    "category",
    "direction",
    "units_gap",
    "money_at_stake",
    "unit_cost",
    "notes",
    "money_basis",
    "mu_l",
    "sigma_l",
    "k",
    "units_short_per_cycle",
    "order_qty",
    "cycles_per_year",
    "units_short_per_year",
    "rank",
)


def _fixed_date():
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    return _FixedDate


def _seed(cur) -> None:
    """The fixed-date rows: demand events, lead times, item-sites."""
    de = (
        "INSERT INTO demand_events (id, business_key, item_code, "
        "site_code, quantity, occurred_on, event_type, source_system) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    )
    # Y367: 1.0 a day for 330 days (11 buckets of 30) then 2.0 a day
    # for the last 30 -> buckets [30 x 11, 60], total 390.
    cur.executemany(
        de,
        [
            (
                f"id-de-367-y-{i}",
                f"de-367-y-{i}",
                "Y367",
                SITE,
                1.0,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(330)
        ],
    )
    cur.executemany(
        de,
        [
            (
                f"id-de-367-y-l-{i}",
                f"de-367-y-l-{i}",
                "Y367",
                SITE,
                2.0,
                (AS_OF - timedelta(days=29) + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(30)
        ],
    )
    # X367: constant 2.0 a day, 360 days -> 12 buckets of 60.
    cur.executemany(
        de,
        [
            (
                f"id-de-367-x-{i}",
                f"de-367-x-{i}",
                "X367",
                SITE,
                2.0,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(360)
        ],
    )
    # N367: one old demand event (14 months before the as-of) -> every
    # bucket in the window is zero -> no_demand, no forecast.
    cur.execute(
        de,
        (
            "id-de-367-n",
            "de-367-n",
            "N367",
            SITE,
            1.0,
            "2025-06-01",
            "shipment",
            "test",
        ),
    )
    # Lead times of 8, 10 and 12 days -> mean 10, sample std 2, three
    # observations (not low confidence).
    for item in ("X367", "Y367"):
        for i, (ordered, received) in enumerate(
            (
                ("2026-08-22", "2026-08-30"),
                ("2026-08-20", "2026-08-30"),
                ("2026-08-18", "2026-08-30"),
            )
        ):
            cur.execute(
                "INSERT INTO lead_time_observations (id, business_key, "
                "supplier_code, item_code, ordered_on, received_on, "
                "source_system) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    f"id-lt-367-{item}-{i}",
                    f"lt-367-{item}-{i}",
                    f"SUP-367-{item}",
                    item,
                    ordered,
                    received,
                    "test",
                ),
            )
    # item_sites rows: the current planning settings.
    cur.execute(
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, order_qty, source_system) "
        "VALUES ('id-is-367-y', 'Y367', %s, 2.0, 25.0, 60.0, 'test')",
        (SITE,),
    )
    cur.execute(
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, source_system) "
        "VALUES ('id-is-367-x', 'X367', %s, 1.0, 25.0, 'test')",
        (SITE,),
    )
    cur.execute(
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, source_system) "
        "VALUES ('id-is-367-n', 'N367', %s, 1.0, 25.0, 'test')",
        (SITE,),
    )


def _list_drift(client) -> list[dict]:
    response = client.get("/api/drift", params={"as_of": AS_OF_STR, "limit": 100})
    assert response.status_code == 200, response.text
    return response.json()


def _recommendation_by_key(client, item_code: str) -> dict:
    response = client.get(
        "/api/recommendations/by-key",
        params={"item_code": item_code, "site_code": SITE, "as_of": AS_OF_STR},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _drift_by_key(client, item_code: str) -> dict:
    response = client.get(
        "/api/drift/by-key",
        params={"item_code": item_code, "site_code": SITE},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _assert_basis_fields(row: dict, recommendation: dict) -> None:
    """The two new fields: the recommendation's demand_basis and its
    other_basis reduced to basis, reorder_point_units and
    safety_stock_units (null with the recommendation's)."""
    if recommendation["other_basis"] is None:
        assert row["other_basis"] is None
    else:
        assert row["other_basis"] == {
            "basis": recommendation["other_basis"]["basis"],
            "reorder_point_units": recommendation["other_basis"]["reorder_point_units"],
            "safety_stock_units": recommendation["other_basis"]["safety_stock_units"],
        }
    assert row["demand_basis"] == recommendation["demand_basis"]


def test_issue367_freeform(client, monkeypatch, admin_headers):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.setenv("AIERP_ODATA_USER", ODATA_USER)
    monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
    odata_headers = {
        "Authorization": "Basic "
        + base64.b64encode(f"{ODATA_USER}:{ODATA_PASSWORD}".encode()).decode()
    }

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    # Y367's planner override of the first forecast period (2026-10-01,
    # the day after the as-of, through 2026-10-30) to 100 units.
    response = client.post(
        "/api/forecast/overrides",
        json={
            "item_code": "Y367",
            "site_code": SITE,
            "period_start": "2026-10-01",
            "quantity": 100.0,
            "reason": "planner test for issue 367",
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["applies"] is True

    # The setting is at its default (history).
    response = client.get("/api/settings")
    assert response.status_code == 200, response.text
    entry = {item["key"]: item for item in response.json()}[KEY]
    assert entry["value"] == "history"
    assert entry["source"] == "default"

    # --- AC-1/AC-2: the setting at its default (history active). -------
    drift = _list_drift(client)
    # Only the two "ok" recommendations are ranked; N367 (no_demand, no
    # forecast) has no drift row.
    assert [(row["item_code"], row["site_code"]) for row in drift] == [
        ("Y367", SITE),
        ("X367", SITE),
    ]
    assert [row["rank"] for row in drift] == [1, 2]
    y_drift, x_drift = drift

    # Y367: demand_basis names the active basis, and other_basis holds
    # basis "forecast" with the same unit values as the item-site's
    # recommendation from /api/recommendations/by-key.
    assert y_drift["demand_basis"] == "history"
    assert y_drift["other_basis"] == {
        "basis": "forecast",
        "reorder_point_units": 46,
        "safety_stock_units": 13,
    }
    y_rec = _recommendation_by_key(client, "Y367")
    assert y_rec["status"] == "ok"
    assert y_rec["demand_basis"] == "history"
    assert y_rec["other_basis"]["basis"] == "forecast"
    assert y_drift["demand_basis"] == y_rec["demand_basis"]
    assert y_drift["other_basis"] == {
        "basis": y_rec["other_basis"]["basis"],
        "reorder_point_units": y_rec["other_basis"]["reorder_point_units"],
        "safety_stock_units": y_rec["other_basis"]["safety_stock_units"],
    }

    # X367: both bases give the same whole-unit values for a flat item.
    assert x_drift["demand_basis"] == "history"
    assert x_drift["other_basis"] == {
        "basis": "forecast",
        "reorder_point_units": 27,
        "safety_stock_units": 7,
    }
    x_rec = _recommendation_by_key(client, "X367")
    assert x_rec["status"] == "ok"
    _assert_basis_fields(x_drift, x_rec)

    # Every other field of the Y367 drift row is unchanged: the row is
    # built on the history basis (19.796570) against the current 25.0,
    # so it is "over" — 5.203430 units at 2.0/unit = 10.406860.
    assert set(UNCHANGED_DRIFT_FIELDS) <= set(y_drift.keys()), y_drift
    assert y_drift["item_code"] == "Y367"
    assert y_drift["site_code"] == SITE
    assert y_drift["as_of"] == AS_OF_STR
    assert y_drift["category"] == "drift"
    assert y_drift["direction"] == "over"
    assert y_drift["reorder_point"] == pytest.approx(RP_H)
    assert y_drift["reorder_point_units"] == 20
    assert y_drift["safety_stock"] == pytest.approx(SS_H)
    assert y_drift["arithmetic"] == y_rec["arithmetic"]
    assert y_drift["current_reorder_point"] == 25.0
    assert y_drift["units_gap"] == pytest.approx(25.0 - RP_H)
    assert y_drift["money_at_stake"] == pytest.approx((25.0 - RP_H) * 2.0)
    assert y_drift["unit_cost"] == 2.0
    assert y_drift["notes"] == []
    assert y_drift["money_basis"] == (
        "money_at_stake = units_gap * unit_cost "
        f"= {25.0 - RP_H:.2f} * 2 = {(25.0 - RP_H) * 2.0:.2f}"
    )
    # The "under"-only fields are null on an "over" row, as before.
    for field in (
        "mu_l",
        "sigma_l",
        "k",
        "units_short_per_cycle",
        "order_qty",
        "cycles_per_year",
        "units_short_per_year",
    ):
        assert y_drift[field] is None, field
    assert y_drift["rank"] == 1
    assert y_drift["decision"] is None
    assert y_drift["decision_lapsed"] is False

    # The X367 drift row is unchanged too: "under" the recommended
    # 26.579415 — mu_l 20, sigma_l 4, k 1.25, the normal loss
    # 0.20234747 short per cycle at 12.166667 cycles a year (30 days of
    # demand assumed as the order quantity) -> 2.46189426 units at
    # 1.0/unit.
    assert set(UNCHANGED_DRIFT_FIELDS) <= set(x_drift.keys()), x_drift
    assert x_drift["item_code"] == "X367"
    assert x_drift["site_code"] == SITE
    assert x_drift["as_of"] == AS_OF_STR
    assert x_drift["category"] == "drift"
    assert x_drift["direction"] == "under"
    assert x_drift["reorder_point"] == pytest.approx(RP_X)
    assert x_drift["reorder_point_units"] == 27
    assert x_drift["safety_stock"] == pytest.approx(SS_X)
    assert x_drift["arithmetic"] == x_rec["arithmetic"]
    assert x_drift["current_reorder_point"] == 25.0
    assert x_drift["units_gap"] == pytest.approx(25.0 - RP_X)
    assert x_drift["mu_l"] == pytest.approx(20.0)
    assert x_drift["sigma_l"] == pytest.approx(SIGMA_X)
    assert x_drift["k"] == pytest.approx(1.25)
    assert x_drift["units_short_per_cycle"] == pytest.approx(SHORT_X)
    assert x_drift["order_qty"] == pytest.approx(60.0)
    assert x_drift["cycles_per_year"] == pytest.approx(CYCLES_X)
    assert x_drift["units_short_per_year"] == pytest.approx(SHORT_X * CYCLES_X)
    assert x_drift["money_at_stake"] == pytest.approx(SHORT_X * CYCLES_X)
    assert x_drift["unit_cost"] == 1.0
    assert x_drift["notes"] == ["order quantity assumed: 30 days of demand"]
    assert x_drift["money_basis"] == (
        "money_at_stake = units_short_per_year * unit_cost "
        f"= {SHORT_X * CYCLES_X:.2f} * 1 = {SHORT_X * CYCLES_X:.2f}"
    )
    assert x_drift["rank"] == 2
    assert x_drift["decision"] is None
    assert x_drift["decision_lapsed"] is False

    # N367 has no forecast: its recommendation carries a null
    # other_basis (the null source a drift row would carry) and it has
    # no drift row, as only "ok" recommendations are ranked.
    assert "N367" not in {row["item_code"] for row in drift}
    n_rec = _recommendation_by_key(client, "N367")
    assert n_rec["status"] == "not_recommendable_demand"
    assert n_rec["demand_class"] == "no_demand"
    assert n_rec["demand_basis"] == "history"
    assert n_rec["other_basis"] is None

    # GET /api/drift/by-key returns the same two fields as the list for
    # the same item-site.
    for item_code, row in (("Y367", y_drift), ("X367", x_drift)):
        by_key = _drift_by_key(client, item_code)
        assert by_key["demand_basis"] == row["demand_basis"], item_code
        assert by_key["other_basis"] == row["other_basis"], item_code

    # --- AC-1/AC-2: the setting at forecast. ---------------------------
    response = client.put(
        f"/api/settings/{KEY}", json={"value": "forecast"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["value"] == "forecast"
    entries = {item["key"]: item for item in client.get("/api/settings").json()}
    assert entries[KEY]["value"] == "forecast"
    assert entries[KEY]["source"] == "system"

    drift = _list_drift(client)
    assert [(row["item_code"], row["site_code"]) for row in drift] == [
        ("Y367", SITE),
        ("X367", SITE),
    ]
    assert [row["rank"] for row in drift] == [1, 2]
    y_drift, x_drift = drift

    # The active basis is now the forecast: demand_basis "forecast" and
    # other_basis.basis "history", the other basis' whole-unit values
    # from the recommendation.
    assert y_drift["demand_basis"] == "forecast"
    assert y_drift["other_basis"] == {
        "basis": "history",
        "reorder_point_units": 20,
        "safety_stock_units": 9,
    }
    y_rec = _recommendation_by_key(client, "Y367")
    assert y_rec["status"] == "ok"
    assert y_rec["demand_basis"] == "forecast"
    assert y_rec["other_basis"]["basis"] == "history"
    _assert_basis_fields(y_drift, y_rec)
    assert x_drift["demand_basis"] == "forecast"
    assert x_drift["other_basis"] == {
        "basis": "history",
        "reorder_point_units": 27,
        "safety_stock_units": 7,
    }

    # Y367's drift row now prices the forecast basis: the current 25.0
    # sits under the recommended 45.809753.
    sigma_y = math.sqrt(10 * SD_F**2 + 4 * D_F**2)
    k_y = (25.0 - 10 * D_F) / sigma_y
    short_y = sigma_y * (NORMAL.pdf(k_y) - k_y * (1.0 - NORMAL.cdf(k_y)))
    cycles_y = 365 * D_F / 60.0
    assert y_drift["direction"] == "under"
    assert y_drift["reorder_point"] == pytest.approx(RP_F)
    assert y_drift["reorder_point_units"] == 46
    assert y_drift["safety_stock"] == pytest.approx(SS_F)
    assert y_drift["arithmetic"] == y_rec["arithmetic"]
    assert y_drift["current_reorder_point"] == 25.0
    assert y_drift["units_gap"] == pytest.approx(25.0 - RP_F)
    assert y_drift["mu_l"] == pytest.approx(10 * D_F)
    assert y_drift["sigma_l"] == pytest.approx(sigma_y)
    assert y_drift["k"] == pytest.approx(k_y)
    assert y_drift["units_short_per_cycle"] == pytest.approx(short_y)
    assert y_drift["order_qty"] == 60.0
    assert y_drift["cycles_per_year"] == pytest.approx(cycles_y)
    assert y_drift["units_short_per_year"] == pytest.approx(short_y * cycles_y)
    assert y_drift["money_at_stake"] == pytest.approx(2.0 * short_y * cycles_y)
    assert y_drift["unit_cost"] == 2.0
    assert y_drift["notes"] == []
    assert y_drift["rank"] == 1
    assert y_drift["decision"] is None
    assert y_drift["decision_lapsed"] is False

    # N367: no forecast under the forecast setting -> built on history
    # with the note, demand_basis "history" and other_basis still null.
    n_rec = _recommendation_by_key(client, "N367")
    assert n_rec["status"] == "not_recommendable_demand"
    assert n_rec["demand_basis"] == "history"
    assert n_rec["notes"] == ["no forecast: history used"]
    assert n_rec["other_basis"] is None

    for item_code, row in (("Y367", y_drift), ("X367", x_drift)):
        by_key = _drift_by_key(client, item_code)
        assert by_key["demand_basis"] == row["demand_basis"], item_code
        assert by_key["other_basis"] == row["other_basis"], item_code

    # Back to the default.
    response = client.delete(f"/api/settings/{KEY}", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["value"] == "history"
    assert response.json()["source"] == "default"

    # --- AC-4/AC-5: the OpenAPI schema. ---------------------------------
    spec = app.openapi()
    for path in ("/api/drift", "/api/drift/by-key"):
        schema = spec["paths"][path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        if path == "/api/drift":
            assert schema["items"]["$ref"] == ("#/components/schemas/DriftDecisionRow")
        else:
            assert schema["$ref"] == "#/components/schemas/DriftDecisionRow"
    decision_row = spec["components"]["schemas"]["DriftDecisionRow"]
    # Both fields are optional, not required.
    assert "demand_basis" not in decision_row["required"]
    assert "other_basis" not in decision_row["required"]
    # The same types and nullability as the recommendation source.
    recommendation_row = spec["components"]["schemas"]["RecommendationResponse"]
    assert decision_row["properties"]["demand_basis"] == (
        recommendation_row["properties"]["demand_basis"]
    )
    recommendation_other_ref = next(
        entry["$ref"]
        for entry in recommendation_row["properties"]["other_basis"]["anyOf"]
        if "$ref" in entry
    )
    recommendation_other = spec["components"]["schemas"][
        recommendation_other_ref.rsplit("/", 1)[-1]
    ]
    drift_other = spec["components"]["schemas"]["DriftOtherBasis"]
    assert drift_other["properties"] == {
        name: recommendation_other["properties"][name]
        for name in ("basis", "reorder_point_units", "safety_stock_units")
    }
    assert drift_other["required"] == [
        "basis",
        "reorder_point_units",
        "safety_stock_units",
    ]
    other_schema = decision_row["properties"]["other_basis"]["anyOf"]
    assert {"type": "null"} in other_schema
    assert (
        next(entry for entry in other_schema if entry.get("type") != "null")["$ref"]
        == "#/components/schemas/DriftOtherBasis"
    )

    # --- AC-1: the OData Drift set is unchanged. -------------------------
    metadata = client.get("/odata/$metadata", headers=odata_headers)
    assert metadata.status_code == 200, metadata.text
    root = ElementTree.fromstring(metadata.text)
    schema = root.find(f"{{{EDM}}}Schema")
    assert schema is not None
    drift_type = next(
        element
        for element in schema.findall(f"{{{EDM}}}EntityType")
        if element.get("Name") == "Drift"
    )
    property_names = {
        prop.get("Name") for prop in drift_type.findall(f"{{{EDM}}}Property")
    }
    assert "demand_basis" not in property_names, property_names
    assert "other_basis" not in property_names, property_names
    # Still exactly the pre-existing DriftRow shape.
    from app.api.drift import DriftRow

    assert property_names == set(DriftRow.model_fields), property_names

    drift_set = client.get("/odata/Drift", headers=odata_headers)
    assert drift_set.status_code == 200, drift_set.text
    odata_rows = drift_set.json()["value"]
    assert {(row["item_code"], row["site_code"]) for row in odata_rows} == {
        ("Y367", SITE),
        ("X367", SITE),
    }
    for row in odata_rows:
        assert "demand_basis" not in row, row
        assert "other_basis" not in row, row
