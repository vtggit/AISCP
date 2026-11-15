"""Issue #927 proof: the S&OP review of the horizon months.

AC-1 — ``app.sop.review.build_sop_review(months)`` and
``GET /api/sop/review`` (mounted in ``app.main`` behind the same
``auth_gate`` as the other routers, open to any signed-in user)
answer with the S&OP review of the ``months`` horizon months (default
3, bounded 1 to 12 inclusive — out of bounds is 422): ``as_of`` the
current server date, ``review_month`` the first month of the horizon
("YYYY-MM"), ``horizon_months`` the requested ``months``, one
``months`` entry per horizon month in month order and
``item_sites_without_unit_cost`` the value the spend view returns.
Per month the figure fields are the sums over all site groups of that
month's projection bucket fields (``demand_units`` the
``projected_demand_units``, ``demand_value`` the
``projected_demand_value``, ``supply_units`` the
``projected_supply_units``, ``supply_value`` the
``projected_supply_value``, ``planned_receipts_units``,
``planned_receipts_value``, ``projected_on_hand_units`` and
``planned_on_hand_units`` the same-named bucket fields),
``sites_short_as_is`` the site codes whose bucket has
``projected_shortfall`` true and ``sites_short_planned`` the site
codes whose bucket has ``planned_shortfall`` true (each sorted
ascending), and ``committed_spend``, ``planned_spend``,
``total_spend``, ``budget``, ``remaining`` and ``over_budget`` copied
unchanged from the matching month of the spend view.  The builder
calls ``aggregate_projection`` exactly once (with ``months`` only — no
``as_of`` and no levers) and feeds those groups and the
``_bucket_months`` buckets of the current server date to
``_spend_view`` so the projection is not computed a second time, and
calls ``realised_accuracy`` once with its default arguments: the
``forecast`` block carries its top-level ``as_of`` and
``compared_buckets``, its ``overall`` ``wape``, ``bias``,
``effective_wape``, ``effective_bias``, ``value_added`` and
``overridden_buckets`` and the ``wape``/``bias`` of the
``by_horizon`` entry with ``bucket_index`` 1 as
``next_month_wape``/``next_month_bias`` (null without such an entry; a
null the accuracy gives stays null).  The builder runs inside
``recommendations_memo``, writes nothing and makes no ERP call.
With no recommendable item-sites every horizon month still appears,
with zero figures and empty site lists, and with no stored snapshots
the forecast rates are null — neither case is an error.  The route is
typed with Pydantic models and ``backend/openapi.json`` documents it.

AC-2 — against the test database (date.today pinned to 2026-10-02,
so the horizon is 2026-10, 2026-11 and 2026-12; demand seeded the
way ``test_issue460_freeform`` does, two sites, one budget stored for
the current month the way ``test_issue520_freeform`` does and one
forecast snapshot stored the way ``test_issue382_freeform`` does):

  * S1 (I927-1 d 10 on-hand 30, I927-2 d 0.5 no on-hand, I927-3 d
    0.25 on-hand 10 without a unit cost; a 40-unit line promised
    2026-09-20 before the horizon, a 2000-unit line promised
    2026-12-10 and a 99-unit line promised 2027-01-15 after it): the
    October bucket starts from 40 on-hand with no October supply and
    demand ceil(10.75 x 31) = 334, so S1 runs short (40 - 334 =
    -294) in the first month while S2 (I927-5 d 1, on-hand 100)
    never does;
  * over the same data each review month's summed fields equal the
    sums a test computes itself from the JSON of
    ``GET /api/aggregate/projection?months=3`` and the six spend
    fields equal the matching month of
    ``GET /api/aggregate/spend?months=3``;
  * the first month's ``sites_short_as_is`` lists exactly S1, the
    site the seed made short;
  * the first month carries the seeded budget and its remaining, the
    second month has ``budget`` null and ``over_budget`` false;
  * ``wape``, ``bias`` and ``next_month_wape`` equal the ``overall``
    and bucket-1 values of ``GET /api/forecast/accuracy`` over the
    same data, and with no snapshots the rates are null with status
    200;
  * one request calls ``aggregate_projection`` exactly once and
    ``recommendations_for_all`` exactly once (counted by wrapping
    each with a recording function that calls the original);
  * ``months=1`` answers one month with ``horizon_months`` 1 and
    ``months=0`` / ``months=13`` answer 422;
  * the route writes nothing (the row counts of ``purchase_budgets``
    and ``scenarios`` are unchanged) and an Acumatica client class
    replaced by one that raises on construction is never reached;
  * no token answers 401, and the route stays open to the non-admin
    (role "user") viewer — the admin-only controls are absent for
    that caller while the review still renders.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.aggregate.projection as projection_module
import app.sop.review as review_module
from app.db.connection import get_connection
from app.feed import acumatica_client as acumatica_client_module
from app.main import app

AS_OF = date(2026, 9, 30)  # the last seeded demand day
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
S1 = "S1"
S2 = "S2"
I1, I2, I3, I5 = "I927-1", "I927-2", "I927-3", "I927-5"
SNAP = date(2026, 8, 1)  # the snapshot date (issue #382's way)
BUDGET_MONTH = "2026-10"  # the current month: the horizon's first month
BUDGET_AMOUNT = 5000.0
SYNC_AS_OF = "2026-09-30T00:00:00+00:00"
URL = "/api/sop/review"

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
LEAD_SQL = (
    "INSERT INTO lead_time_observations (id, business_key, supplier_code, "
    "item_code, ordered_on, received_on, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
ITEM_SITE_SQL = (
    "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
    "reorder_point, safety_stock, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
ON_HAND_SQL = (
    "INSERT INTO on_hand (id, item_code, site_code, quantity_on_hand, "
    "as_of, source_system) VALUES (%s, %s, %s, %s, %s, %s)"
)
OPEN_LINE_SQL = (
    "INSERT INTO open_purchase_lines (id, business_key, item_code, "
    "site_code, supplier_code, order_type, open_units, promised_on, "
    "ordered_on, as_of, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
SNAPSHOT_SQL = (
    "INSERT INTO forecast_snapshots "
    "(id, as_of, origin, item_code, site_code, bucket_index, "
    "bucket_start, bucket_end, quantity, effective_quantity, "
    "override_days, method, demand_class, history_buckets) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def _seed_projection(cur) -> None:
    """The projection's inputs, as test_issue460_freeform seeds them:
    two sites, S1 short in the first month of the horizon."""
    # Smooth daily demand for 360 days (d = quantity, sd_d 0):
    # I1@S1 10/day, I2@S1 0.5/day, I3@S1 0.25/day, I5@S2 1/day.
    for item, site, quantity in (
        (I1, S1, 10.0),
        (I2, S1, 0.5),
        (I3, S1, 0.25),
        (I5, S2, 1.0),
    ):
        suffix = "s2" if site == S2 else "s1"
        cur.executemany(
            DEMAND_SQL,
            [
                (
                    f"id-de-927-{item[-1]}-{suffix}-{i}",
                    f"de-927-{item[-1]}-{suffix}-{i}",
                    item,
                    site,
                    quantity,
                    (FIRST_DAY + timedelta(days=i)).isoformat(),
                    "shipment",
                    "test",
                )
                for i in range(360)
            ],
        )
    # Three lead-time observations per item, all of the same duration
    # (LT the duration, sd_LT 0, n_used 3 -> not low confidence):
    # I1 and I2 10 days, I3 8 days, I5 5 days.
    for item, duration, first in (
        (I1, 10, "2026-08-20"),
        (I2, 10, "2026-08-21"),
        (I3, 8, "2026-08-22"),
        (I5, 5, "2026-08-25"),
    ):
        base = date.fromisoformat(first)
        for i in range(3):
            ordered = base + timedelta(days=i)
            cur.execute(
                LEAD_SQL,
                (
                    f"id-lt-927-{item[-1]}-{i}",
                    f"lt-927-{item[-1]}-{i}",
                    f"SUP-927-{item[-1]}",
                    item,
                    ordered.isoformat(),
                    (ordered + timedelta(days=duration)).isoformat(),
                    "test",
                ),
            )
    # item_sites: I3 has no unit cost (counted, contributes no value).
    cur.executemany(
        ITEM_SITE_SQL,
        [
            ("id-is-927-1", I1, S1, 2.5, 120.0, 5.0, "test"),
            ("id-is-927-2", I2, S1, 1.0, 100.0, 0.0, "test"),
            ("id-is-927-3", I3, S1, None, 40.0, 2.0, "test"),
            ("id-is-927-5", I5, S2, 3.0, 10.0, 0.0, "test"),
        ],
    )
    # on_hand: no row for I2 (it contributes 0 to the group start).
    cur.executemany(
        ON_HAND_SQL,
        [
            ("id-oh-927-1", I1, S1, 30.0, SYNC_AS_OF, "test"),
            ("id-oh-927-3", I3, S1, 10.0, SYNC_AS_OF, "test"),
            ("id-oh-927-5", I5, S2, 100.0, SYNC_AS_OF, "test"),
        ],
    )
    # Open purchase lines of the newest sync: I2's 40 units promised
    # 2026-09-20 (before the first bucket — stays on order throughout,
    # contributes to no bucket), I1's 2000 units promised 2026-12-10
    # (the December supply) and 99 units promised 2027-01-15 (outside
    # the window), I5's 12 units promised 2026-11-05 (the November
    # supply).
    cur.executemany(
        OPEN_LINE_SQL,
        [
            (
                "id-ol-927-2a",
                "bk-927-2a",
                I2,
                S1,
                "SUP-927-2",
                "po",
                40.0,
                "2026-09-20",
                "2026-09-10",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-927-1a",
                "bk-927-1a",
                I1,
                S1,
                "SUP-927-1",
                "po",
                2000.0,
                "2026-12-10",
                "2026-09-20",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-927-1b",
                "bk-927-1b",
                I1,
                S1,
                "SUP-927-1",
                "po",
                99.0,
                "2027-01-15",
                "2026-09-10",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-927-5",
                "bk-927-5",
                I5,
                S2,
                "SUP-927-5",
                "po",
                12.0,
                "2026-11-05",
                "2026-09-15",
                SYNC_AS_OF,
                "test",
            ),
        ],
    )
    # One budget for the current month (2026-10), the horizon's first
    # month, stored the way test_issue520_freeform stores its budgets.
    cur.execute(
        "INSERT INTO purchase_budgets (month, amount, updated_by) "
        "VALUES (%s, %s, %s)",
        (BUDGET_MONTH, BUDGET_AMOUNT, "test"),
    )


def _seed_snapshot(cur) -> None:
    """The forecast snapshot, stored the way test_issue382_freeform
    stores one: one direct insert per item-site per bucket, origin
    ``live``.  Every statistical bucket is 30.0 (method ``mean``),
    no override: the effective measures read as the statistical ones.
    Buckets 1 and 2 (through 2026-09-30) are the completed ones.
    """
    rows = []
    for item, site in ((I1, S1), (I5, S2)):
        for index in range(1, 13):
            rows.append(
                (
                    f"fs-927-{item}-{index}",
                    SNAP,
                    "live",
                    item,
                    site,
                    index,
                    SNAP + timedelta(days=30 * (index - 1) + 1),
                    SNAP + timedelta(days=30 * index),
                    30.0,
                    None,
                    None,
                    "mean",
                    "smooth",
                    13,
                )
            )
    cur.executemany(SNAPSHOT_SQL, rows)


def _row_counts() -> dict[str, int]:
    """The row counts of the tables the route must not write."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            counts = {}
            for table in ("purchase_budgets", "scenarios"):
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                counts[table] = cur.fetchone()[0]
            return counts
    finally:
        conn.close()


def test_issue927_freeform(client, monkeypatch, user_token):
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    # The review resolves its own "current server date", the
    # projection resolves its default as_of against it, and the shared
    # default as-of date (recommendations, realised accuracy) is
    # pinned to the same server date.
    monkeypatch.setattr(review_module, "date", _FixedDate)
    monkeypatch.setattr(projection_module, "date", _FixedDate)
    # The spend route resolves its default as_of from its own copy of
    # date; the shared default as-of date (recommendations, realised
    # accuracy) is pinned too.
    monkeypatch.setattr("app.aggregate.spend.date", _FixedDate)
    monkeypatch.setattr("app.stats.demand.date", _FixedDate)

    # --- no data: empty database, no snapshots ----------------------------
    # Every horizon month still appears, with zero figures and empty
    # site lists; the forecast rates are null.  Neither is an error,
    # and the default ``months`` is 3.
    response = client.get(URL)
    assert response.status_code == 200, response.text
    empty = response.json()
    assert empty["as_of"] == FIXED_TODAY.isoformat()
    assert empty["review_month"] == "2026-10"
    assert empty["horizon_months"] == 3
    assert [row["month"] for row in empty["months"]] == [
        "2026-10",
        "2026-11",
        "2026-12",
    ]
    for row in empty["months"]:
        assert row["demand_units"] == 0
        assert row["demand_value"] == 0
        assert row["supply_units"] == 0
        assert row["supply_value"] == 0
        assert row["planned_receipts_units"] == 0
        assert row["planned_receipts_value"] == 0
        assert row["projected_on_hand_units"] == 0
        assert row["planned_on_hand_units"] == 0
        assert row["sites_short_as_is"] == []
        assert row["sites_short_planned"] == []
        assert row["committed_spend"] == 0
        assert row["planned_spend"] == 0
        assert row["total_spend"] == 0
        assert row["budget"] is None
        assert row["remaining"] is None
        assert row["over_budget"] is False
    assert empty["item_sites_without_unit_cost"] == 0
    assert empty["forecast"] == {
        "as_of": FIXED_TODAY.isoformat(),
        "compared_buckets": 0,
        "wape": None,
        "bias": None,
        "effective_wape": None,
        "effective_bias": None,
        "value_added": None,
        "overridden_buckets": 0,
        "next_month_wape": None,
        "next_month_bias": None,
    }

    # --- seed ------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed_projection(cur)
        conn.commit()
    finally:
        conn.close()

    # --- no snapshots: the rates are null, the status is 200 ---------------
    response = client.get(URL, params={"months": 3})
    assert response.status_code == 200, response.text
    no_snapshots = response.json()
    assert no_snapshots["forecast"]["wape"] is None
    assert no_snapshots["forecast"]["bias"] is None
    assert no_snapshots["forecast"]["effective_wape"] is None
    assert no_snapshots["forecast"]["effective_bias"] is None
    assert no_snapshots["forecast"]["value_added"] is None
    assert no_snapshots["forecast"]["overridden_buckets"] == 0
    assert no_snapshots["forecast"]["compared_buckets"] == 0
    assert no_snapshots["forecast"]["next_month_wape"] is None
    assert no_snapshots["forecast"]["next_month_bias"] is None
    # The projection side is already live over the seeded data.
    assert no_snapshots["item_sites_without_unit_cost"] == 1
    assert no_snapshots["months"][0]["sites_short_as_is"] == [S1]

    # --- the snapshot -------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed_snapshot(cur)
        conn.commit()
    finally:
        conn.close()

    # --- the review against the existing routes ----------------------------
    projection_json = client.get(
        "/api/aggregate/projection", params={"months": 3}
    ).json()
    spend_json = client.get("/api/aggregate/spend", params={"months": 3}).json()
    accuracy_json = client.get("/api/forecast/accuracy").json()
    response = client.get(URL, params={"months": 3})
    assert response.status_code == 200, response.text
    review = response.json()
    assert review["as_of"] == FIXED_TODAY.isoformat()
    assert review["review_month"] == "2026-10"
    assert review["horizon_months"] == 3
    assert [row["month"] for row in review["months"]] == [
        "2026-10",
        "2026-11",
        "2026-12",
    ]
    assert (
        review["item_sites_without_unit_cost"]
        == spend_json["item_sites_without_unit_cost"]
        == 1
    )  # I927-3 has no unit cost

    summed_fields = (
        ("demand_units", "projected_demand_units"),
        ("demand_value", "projected_demand_value"),
        ("supply_units", "projected_supply_units"),
        ("supply_value", "projected_supply_value"),
        ("planned_receipts_units", "planned_receipts_units"),
        ("planned_receipts_value", "planned_receipts_value"),
        ("projected_on_hand_units", "projected_on_hand_units"),
        ("planned_on_hand_units", "planned_on_hand_units"),
    )
    for review_month, spend_month in zip(review["months"], spend_json["months"]):
        label = review_month["month"]
        assert label == spend_month["month"]
        # Each summed field equals the sum a test computes itself from
        # the JSON of GET /api/aggregate/projection?months=3.
        sums = {name: 0 for name, _field in summed_fields}
        short_as_is = []
        short_planned = []
        for group in projection_json:
            for bucket in group["buckets"]:
                if bucket["month"] != label:
                    continue
                for name, field in summed_fields:
                    sums[name] += bucket[field]
                if bucket["projected_shortfall"]:
                    short_as_is.append(group["group"]["code"])
                if bucket["planned_shortfall"]:
                    short_planned.append(group["group"]["code"])
        for name, _field in summed_fields:
            assert review_month[name] == pytest.approx(sums[name]), f"{name} of {label}"
        assert review_month["sites_short_as_is"] == sorted(short_as_is)
        assert review_month["sites_short_planned"] == sorted(short_planned)
        # The six spend fields are the matching spend route's month.
        for field in (
            "committed_spend",
            "planned_spend",
            "total_spend",
            "budget",
            "remaining",
        ):
            assert review_month[field] == pytest.approx(
                spend_month[field]
            ), f"{field} of {label}"
        assert review_month["over_budget"] is spend_month["over_budget"]

    # The snapshot the test stored: four compared buckets (two
    # item-sites x horizons 1 and 2), forecast 120.0 vs actuals 660.0.
    assert accuracy_json["compared_buckets"] == 4
    assert accuracy_json["overall"]["forecast_total"] == pytest.approx(120.0)
    assert accuracy_json["overall"]["actual_total"] == pytest.approx(660.0)
    assert accuracy_json["overall"]["wape"] == pytest.approx(540.0 / 660.0)
    bucket_1 = next(
        entry for entry in accuracy_json["by_horizon"] if entry["bucket_index"] == 1
    )
    assert bucket_1["wape"] == pytest.approx(270.0 / 330.0)

    # --- the shortfall sites -------------------------------------------------
    # The first month lists exactly S1, the site the seed made short
    # (40 on hand + no October supply - 334 demand = -294); S2 (100
    # on hand) never does.
    assert review["months"][0]["sites_short_as_is"] == [S1]
    assert review["months"][0]["sites_short_planned"] == sorted(
        group["group"]["code"]
        for group in projection_json
        for bucket in group["buckets"]
        if bucket["month"] == "2026-10" and bucket["planned_shortfall"]
    )
    assert review["months"][1]["sites_short_as_is"] == [S1]
    assert review["months"][2]["sites_short_as_is"] == []

    # --- the budget ------------------------------------------------------------
    first = review["months"][0]
    assert first["budget"] == pytest.approx(BUDGET_AMOUNT)
    assert first["remaining"] == pytest.approx(BUDGET_AMOUNT - first["total_spend"])
    assert first["over_budget"] is False
    for row in review["months"][1:]:
        assert row["budget"] is None
        assert row["remaining"] is None
        assert row["over_budget"] is False

    # --- the forecast --------------------------------------------------------
    forecast = review["forecast"]
    assert forecast["as_of"] == accuracy_json["as_of"]
    assert forecast["compared_buckets"] == accuracy_json["compared_buckets"]
    overall = accuracy_json["overall"]
    for field in (
        "wape",
        "bias",
        "effective_wape",
        "effective_bias",
        "value_added",
    ):
        assert forecast[field] == pytest.approx(overall[field]), field
    assert forecast["overridden_buckets"] == overall["overridden_buckets"]
    assert forecast["next_month_wape"] == pytest.approx(bucket_1["wape"])
    assert forecast["next_month_bias"] == pytest.approx(bucket_1["bias"])

    # --- one request, one projection, one recommendation ----------------------
    projection_calls = [0]
    recommendation_calls = [0]
    real_projection = review_module.aggregate_projection
    real_recommendations = projection_module.recommendations_for_all

    def counting_projection(*args, **kwargs):
        projection_calls[0] += 1
        return real_projection(*args, **kwargs)

    def counting_recommendations(*args, **kwargs):
        recommendation_calls[0] += 1
        return real_recommendations(*args, **kwargs)

    monkeypatch.setattr(review_module, "aggregate_projection", counting_projection)
    monkeypatch.setattr(
        projection_module, "recommendations_for_all", counting_recommendations
    )
    response = client.get(URL, params={"months": 3})
    assert response.status_code == 200, response.text
    assert response.json() == review  # the same review, one computation
    assert projection_calls[0] == 1, "the projection is computed exactly once"
    assert recommendation_calls[0] == 1, "recommendations_for_all runs exactly once"

    # --- the horizon -------------------------------------------------------------
    response = client.get(URL, params={"months": 1})
    assert response.status_code == 200, response.text
    one_month = response.json()
    assert one_month["horizon_months"] == 1
    assert [row["month"] for row in one_month["months"]] == ["2026-10"]
    assert one_month["months"][0] == review["months"][0]
    assert client.get(URL, params={"months": 0}).status_code == 422
    assert client.get(URL, params={"months": 13}).status_code == 422
    assert client.get(URL, params={"months": "not-a-number"}).status_code == 422

    # --- read-only, no ERP call ----------------------------------------------------
    counts_before = _row_counts()
    assert counts_before == {"purchase_budgets": 1, "scenarios": 0}

    def _no_erp_client(self, *args, **kwargs):
        raise AssertionError("the S&OP review must not call the ERP")

    monkeypatch.setattr(
        acumatica_client_module.AcumaticaClient, "__init__", _no_erp_client
    )
    response = client.get(URL, params={"months": 3})
    assert response.status_code == 200, response.text
    assert _row_counts() == counts_before, "the request wrote nothing"

    # --- unauthenticated access ------------------------------------------------------
    with TestClient(app) as anonymous:
        response = anonymous.get(URL)
    assert response.status_code == 401, response.text
    assert response.headers.get("WWW-Authenticate") == "Bearer"

    # --- Identity: a second pass after a full reload, as the
    #     non-admin (role "user"). ------------------------------------------------
    # The page boots the production way: the token it would take from
    # the URL hash as #access_token=... is the development token (role
    # "user") sent as its Bearer token; GET /api/auth/config answers
    # publicly and GET /api/auth/me resolves the viewer.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["user"]["sub"] == "dev-user-1"
    assert me_body["user"]["roles"] == ["user"]
    assert "admin" not in me_body["user"]["roles"]

    # The review is not an admin-only control: the viewer still gets
    # the full page — the same body the signed-in caller above got —
    # and no token is still a 401.
    resp = viewer.get(URL, params={"months": 3})
    assert resp.status_code == 200, resp.text
    assert resp.json() == review
    assert anon_reloaded.get(URL).status_code == 401
