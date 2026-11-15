"""Issue #941 proof: the S&OP scenarios comparison of the horizon months.

AC-1 — ``app.sop.review.build_sop_scenarios(months)`` and
``GET /api/sop/scenarios`` (mounted in ``app.main`` behind the same
``auth_gate`` as the other routers, open to any signed-in user) answer
with the comparison of the first 5 stored scenarios (newest first, read
once through the scenarios repository) against the no-lever baseline
projection of the ``months`` horizon (default 3, bounded 1 to 12
inclusive — out of bounds is 422): ``as_of`` the current server date,
``horizon_months`` the requested ``months``, ``scenarios_total`` every
stored scenario (including any beyond the first 5), ``baseline`` the
four figures of the baseline and ``scenarios`` one entry per compared
scenario.  The builder runs inside ``recommendations_memo``, calls
``aggregate_projection`` once for the baseline (``months`` and no
levers) and once per scenario (``months`` and the scenario's
``service_level``, ``demand_multiplier`` and ``lead_time_multiplier``,
the way ``GET /api/aggregate/projection`` applies them) and feeds each
result to ``_spend_view`` over the same buckets.  ``baseline`` and each
scenario carry ``demand_value`` (the sum of ``projected_demand_value``
over the horizon months and all site groups), ``total_spend`` (the sum
of the spend view's ``total_spend`` over the horizon),
``months_over_budget`` (the count of horizon months with ``over_budget``
true) and ``months_short_planned`` (the count of horizon months with at
least one site group with ``planned_shortfall`` true); each scenario
also carries its ``id``, ``name``, the three levers,
``demand_value_delta`` / ``total_spend_delta`` (the scenario minus the
baseline) and ``error`` (null normally).  A scenario whose levers make
the projection raise ``ValueError`` — or that carries a lever the
projection does not accept, such as a non-null
``service_level_by_class`` — answers with ``error`` set and null
figures and deltas, the others still answering; a ``ValueError`` from
the baseline is 422; with no stored scenarios ``scenarios`` is empty,
``scenarios_total`` is 0 and the baseline is filled (200).  The route
is typed with Pydantic models and ``backend/openapi.json`` documents
it; it writes nothing and makes no ERP call.

AC-2 — against the test database (date.today pinned to 2026-10-02, so
the horizon is 2026-10, 2026-11 and 2026-12; demand and the rest of the
projection's inputs seeded the way ``test_issue927_freeform`` does, a
budget on the first month and scenarios saved through
``POST /api/scenarios`` the way the scenario tests do): with one
scenario of ``demand_multiplier`` 1.5 the baseline ``total_spend``
equals the sum a test computes itself from
``GET /api/aggregate/spend?months=3`` and the scenario's ``total_spend``
/ ``demand_value`` equal the sums it computes from
``GET /api/aggregate/spend?months=3&demand_multiplier=1.5`` and
``GET /api/aggregate/projection?months=3&demand_multiplier=1.5`` (the
deltas the differences); ``months_over_budget`` /
``months_short_planned`` match the counts computed from those same
answers, over a seed with at least one month of each; with 6 saved
scenarios ``scenarios`` has 5 (newest first) and ``scenarios_total`` is
6, with ``aggregate_projection`` called exactly 6 times and the
recommendation computation exactly once (counted by wrapping each with a
recording function that calls the original); one scenario whose lever
makes the projection raise ``ValueError`` answers with ``error`` set and
null figures while the other rows stay filled; with no scenarios the
answer is 200 with ``scenarios`` empty and the baseline filled;
``months=1`` answers ``horizon_months`` 1 and ``months=0`` /
``months=13`` answer 422; the route writes nothing (the scenarios row
count is unchanged) and an Acumatica client class replaced by one that
raises on construction is never reached; no token answers 401; and the
route stays open to the non-admin (role "user") viewer — the admin-only
controls are absent for that caller while the comparison still renders.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.aggregate.projection as projection_module
import app.recommend.reorder as reorder_module
import app.sop.review as review_module
from app.db.connection import get_connection
from app.feed import acumatica_client as acumatica_client_module
from app.main import app

AS_OF = date(2026, 9, 30)  # the last seeded demand day
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
S1 = "S1"
S2 = "S2"
S3 = "S3"
I1, I2, I3, I5 = "I941-1", "I941-2", "I941-3", "I941-5"
I6 = "I941-6"
BUDGET_MONTH = "2026-10"  # the current month: the horizon's first month
BUDGET_AMOUNT = 1.0  # far below the first month's spend: over budget
SYNC_AS_OF = "2026-09-30T00:00:00+00:00"
URL = "/api/sop/scenarios"

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
SCENARIO_SQL = (
    "INSERT INTO scenarios (id, name, service_level, demand_multiplier, "
    "lead_time_multiplier, created_at, updated_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)


def _seed_projection(cur) -> None:
    """The projection's inputs, as test_issue927_freeform seeds them:
    two sites, S1 short in the first month of the horizon."""
    for item, site, quantity in (
        (I1, S1, 10.0),
        (I2, S1, 0.5),
        (I3, S1, 0.25),
        (I5, S2, 1.0),
        (I6, S3, 5.0),
    ):
        suffix = "s2" if site == S2 else "s1"
        cur.executemany(
            DEMAND_SQL,
            [
                (
                    f"id-de-941-{item[-1]}-{suffix}-{i}",
                    f"de-941-{item[-1]}-{suffix}-{i}",
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
    for item, duration, first in (
        (I1, 10, "2026-08-20"),
        (I2, 10, "2026-08-21"),
        (I3, 8, "2026-08-22"),
        (I5, 5, "2026-08-25"),
        (I6, 45, "2026-08-10"),
    ):
        base = date.fromisoformat(first)
        for i in range(3):
            ordered = base + timedelta(days=i)
            cur.execute(
                LEAD_SQL,
                (
                    f"id-lt-941-{item[-1]}-{i}",
                    f"lt-941-{item[-1]}-{i}",
                    f"SUP-941-{item[-1]}",
                    item,
                    ordered.isoformat(),
                    (ordered + timedelta(days=duration)).isoformat(),
                    "test",
                ),
            )
    cur.executemany(
        ITEM_SITE_SQL,
        [
            ("id-is-941-1", I1, S1, 2.5, 120.0, 5.0, "test"),
            ("id-is-941-2", I2, S1, 1.0, 100.0, 0.0, "test"),
            ("id-is-941-3", I3, S1, None, 40.0, 2.0, "test"),
            ("id-is-941-5", I5, S2, 3.0, 10.0, 0.0, "test"),
            ("id-is-941-6", I6, S3, 2.0, 50.0, 1.0, "test"),
        ],
    )
    cur.executemany(
        ON_HAND_SQL,
        [
            ("id-oh-941-1", I1, S1, 30.0, SYNC_AS_OF, "test"),
            ("id-oh-941-3", I3, S1, 10.0, SYNC_AS_OF, "test"),
            ("id-oh-941-5", I5, S2, 100.0, SYNC_AS_OF, "test"),
        ],
    )
    cur.executemany(
        OPEN_LINE_SQL,
        [
            (
                "id-ol-941-2a",
                "bk-941-2a",
                I2,
                S1,
                "SUP-941-2",
                "po",
                40.0,
                "2026-09-20",
                "2026-09-10",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-941-1a",
                "bk-941-1a",
                I1,
                S1,
                "SUP-941-1",
                "po",
                2000.0,
                "2026-12-10",
                "2026-09-20",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-941-1b",
                "bk-941-1b",
                I1,
                S1,
                "SUP-941-1",
                "po",
                99.0,
                "2027-01-15",
                "2026-09-10",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-941-1c",
                "bk-941-1c",
                I1,
                S1,
                "SUP-941-1",
                "po",
                1500.0,
                "2026-10-10",
                "2026-09-25",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-941-5",
                "bk-941-5",
                I5,
                S2,
                "SUP-941-5",
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
    # month, far below that month's spend: the month runs over budget.
    cur.execute(
        "INSERT INTO purchase_budgets (month, amount, updated_by) "
        "VALUES (%s, %s, %s)",
        (BUDGET_MONTH, BUDGET_AMOUNT, "test"),
    )


def _demand_value_of(groups) -> float:
    """The sum of projected_demand_value over every bucket of every group."""
    return sum(
        bucket["projected_demand_value"]
        for group in groups
        for bucket in group["buckets"]
    )


def _short_planned_months(groups) -> int:
    """The count of months with at least one site group planned-short."""
    return len(
        {
            bucket["month"]
            for group in groups
            for bucket in group["buckets"]
            if bucket["planned_shortfall"]
        }
    )


def _scenario_count() -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM scenarios")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _store_scenario(
    cur,
    name,
    service_level=None,
    demand_multiplier=None,
    lead_time_multiplier=None,
    when=None,
) -> str:
    """Store one scenario row directly (a lever the API would refuse)."""
    new_id = str(uuid4())
    now = when or datetime.now(UTC)
    cur.execute(
        SCENARIO_SQL,
        (
            new_id,
            name,
            service_level,
            demand_multiplier,
            lead_time_multiplier,
            now,
            now,
        ),
    )
    return new_id


def test_issue941_freeform(client, monkeypatch, user_token):
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    # The comparison resolves its own "current server date", the
    # projection resolves its default as_of against it, and the shared
    # default as-of date (recommendations, spend) is pinned too.
    monkeypatch.setattr(review_module, "date", _FixedDate)
    monkeypatch.setattr(projection_module, "date", _FixedDate)
    monkeypatch.setattr("app.aggregate.spend.date", _FixedDate)
    monkeypatch.setattr("app.stats.demand.date", _FixedDate)

    # --- no scenarios: 200, empty, baseline filled -------------------------
    response = client.get(URL)
    assert response.status_code == 200, response.text
    empty = response.json()
    assert empty["as_of"] == FIXED_TODAY.isoformat()
    assert empty["horizon_months"] == 3
    assert empty["scenarios_total"] == 0
    assert empty["scenarios"] == []
    for field in (
        "demand_value",
        "total_spend",
        "months_over_budget",
        "months_short_planned",
    ):
        assert field in empty["baseline"]
    assert empty["baseline"]["demand_value"] == 0
    assert empty["baseline"]["total_spend"] == 0
    assert empty["baseline"]["months_over_budget"] == 0
    assert empty["baseline"]["months_short_planned"] == 0

    # --- seed the projection's inputs --------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed_projection(cur)
        conn.commit()
    finally:
        conn.close()

    # --- against the existing routes: one scenario at 1.5 ------------------
    created = client.post(
        "/api/scenarios", json={"name": "Peak demand", "demand_multiplier": 1.5}
    )
    assert created.status_code == 201, created.text
    assert created.json()["demand_multiplier"] == 1.5

    spend_base = client.get("/api/aggregate/spend", params={"months": 3}).json()
    proj_base = client.get("/api/aggregate/projection", params={"months": 3}).json()
    spend_15 = client.get(
        "/api/aggregate/spend", params={"months": 3, "demand_multiplier": 1.5}
    ).json()
    proj_15 = client.get(
        "/api/aggregate/projection", params={"months": 3, "demand_multiplier": 1.5}
    ).json()

    response = client.get(URL, params={"months": 3})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] == FIXED_TODAY.isoformat()
    assert body["horizon_months"] == 3
    assert body["scenarios_total"] == 1
    assert len(body["scenarios"]) == 1
    baseline = body["baseline"]
    scenario = body["scenarios"][0]
    assert scenario["id"] == created.json()["id"]
    assert scenario["name"] == "Peak demand"
    assert scenario["demand_multiplier"] == 1.5
    assert scenario["service_level"] is None
    assert scenario["lead_time_multiplier"] is None
    assert scenario["error"] is None

    # The baseline's total spend is the sum the test computes itself from
    # GET /api/aggregate/spend?months=3.
    assert baseline["total_spend"] == pytest.approx(
        sum(month["total_spend"] for month in spend_base["months"])
    )
    assert baseline["demand_value"] == pytest.approx(_demand_value_of(proj_base))
    # The scenario's total spend / demand value are the sums the test
    # computes from the levers' spend and projection answers.
    assert scenario["total_spend"] == pytest.approx(
        sum(month["total_spend"] for month in spend_15["months"])
    )
    assert scenario["demand_value"] == pytest.approx(_demand_value_of(proj_15))
    # The deltas are the differences, scenario minus baseline.
    assert scenario["total_spend_delta"] == pytest.approx(
        scenario["total_spend"] - baseline["total_spend"]
    )
    assert scenario["demand_value_delta"] == pytest.approx(
        scenario["demand_value"] - baseline["demand_value"]
    )

    # --- the counts, from those same answers -------------------------------
    assert baseline["months_over_budget"] == sum(
        1 for month in spend_base["months"] if month["over_budget"]
    )
    assert scenario["months_over_budget"] == sum(
        1 for month in spend_15["months"] if month["over_budget"]
    )
    assert baseline["months_short_planned"] == _short_planned_months(proj_base)
    assert scenario["months_short_planned"] == _short_planned_months(proj_15)
    # The seed carries at least one month of each.
    assert baseline["months_over_budget"] >= 1
    assert baseline["months_short_planned"] >= 1

    # --- the cap: 6 saved scenarios, 5 answered, newest first --------------
    for index in range(5):
        more = client.post(
            "/api/scenarios",
            json={
                "name": f"Scenario {index}",
                "demand_multiplier": 1.0 + 0.1 * index,
            },
        )
        assert more.status_code == 201, more.text

    response = client.get(URL, params={"months": 3})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scenarios_total"] == 6
    assert len(body["scenarios"]) == 5
    listed = client.get("/api/scenarios").json()
    assert len(listed) == 6
    # Newest first: exactly the first 5 the list route answers, in order.
    assert [row["id"] for row in body["scenarios"]] == [row["id"] for row in listed[:5]]

    # One request: aggregate_projection exactly 6 times (the baseline and
    # the 5 scenarios) and the recommendation computation exactly once
    # (the memo shared by the 6 projections).
    projection_calls = [0]
    recommendation_calls = [0]
    real_projection = review_module.aggregate_projection
    real_compute = reorder_module._compute_recommendations

    def counting_projection(*args, **kwargs):
        projection_calls[0] += 1
        return real_projection(*args, **kwargs)

    def counting_compute(resolved_as_of):
        recommendation_calls[0] += 1
        return real_compute(resolved_as_of)

    monkeypatch.setattr(review_module, "aggregate_projection", counting_projection)
    monkeypatch.setattr(reorder_module, "_compute_recommendations", counting_compute)
    response = client.get(URL, params={"months": 3})
    assert response.status_code == 200, response.text
    assert projection_calls[0] == 6, "the projection runs once per scenario + baseline"
    assert recommendation_calls[0] == 1, "the recommendation read runs exactly once"

    # --- one bad scenario: ValueError -> error set, figures null -----------
    # A lever the API refuses (service_level 1.5): stored directly, the
    # projection raises ValueError for it and the row answers with the
    # message, its figures and deltas null, the other rows still filled.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _store_scenario(cur, "Bad lever", service_level=1.5)
        conn.commit()
    finally:
        conn.close()

    response = client.get(URL, params={"months": 3})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scenarios_total"] == 7
    bad = next(row for row in body["scenarios"] if row["name"] == "Bad lever")
    assert bad["error"] == "service_level must be a number strictly between 0 and 1"
    for field in (
        "demand_value",
        "total_spend",
        "months_over_budget",
        "months_short_planned",
        "demand_value_delta",
        "total_spend_delta",
    ):
        assert bad[field] is None, field
    others = [row for row in body["scenarios"] if row["name"] != "Bad lever"]
    assert others, "the other scenarios still answer"
    for row in others:
        assert row["error"] is None
        assert row["demand_value"] is not None
        assert row["total_spend"] is not None

    # --- the horizon -------------------------------------------------------
    response = client.get(URL, params={"months": 1})
    assert response.status_code == 200, response.text
    one = response.json()
    assert one["horizon_months"] == 1
    assert client.get(URL, params={"months": 0}).status_code == 422
    assert client.get(URL, params={"months": 13}).status_code == 422
    assert client.get(URL, params={"months": "not-a-number"}).status_code == 422

    # --- read-only, no ERP call --------------------------------------------
    counts_before = _scenario_count()
    assert counts_before == 7

    def _no_erp_client(self, *args, **kwargs):
        raise AssertionError("the S&OP scenarios comparison must not call the ERP")

    monkeypatch.setattr(
        acumatica_client_module.AcumaticaClient, "__init__", _no_erp_client
    )
    response = client.get(URL, params={"months": 3})
    assert response.status_code == 200, response.text
    assert _scenario_count() == counts_before, "the request wrote nothing"

    # --- unauthenticated access --------------------------------------------
    with TestClient(app) as anonymous:
        response = anonymous.get(URL)
    assert response.status_code == 401, response.text
    assert response.headers.get("WWW-Authenticate") == "Bearer"

    # --- Identity: a second pass after a full reload, as the
    #     non-admin (role "user"). ------------------------------------------
    # The caller boots the production way: the token it would take from
    # the URL hash as #access_token=... is the development token (role
    # "user") sent as its Bearer token; GET /api/auth/config answers
    # publicly and GET /api/auth/me resolves the viewer.
    canonical = client.get(URL, params={"months": 3}).json()
    viewer = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["user"]["sub"] == "dev-user-1"
    assert me_body["user"]["roles"] == ["user"]
    assert "admin" not in me_body["user"]["roles"]

    # The comparison is not an admin-only control: the viewer still gets
    # the full answer — the same body the signed-in caller above got —
    # and no token is still a 401.
    resp = viewer.get(URL, params={"months": 3})
    assert resp.status_code == 200, resp.text
    assert resp.json() == canonical
    assert anon_reloaded.get(URL).status_code == 401

    # --- a persisted unsupported lever: stable, user-safe error ------------
    # A row the projection cannot take (a non-null service_level_by_class)
    # answers with the stable UNSUPPORTED_LEVER_ERROR and null figures and
    # deltas, the baseline and the other rows still answering, and the
    # request never 422s because of the row.  The check must come from the
    # row itself, not a hardcoded lever list: a row carrying a different
    # unsupported lever (demand_multiplier_by_class) must answer the same
    # stable error too, never be silently ignored.
    now = datetime(2026, 10, 2, 12, 0, 0)
    unsupported_rows = [
        {
            "id": "id-941-unsupported",
            "name": "Per-class service level",
            "service_level": None,
            "demand_multiplier": None,
            "lead_time_multiplier": None,
            "service_level_by_class": {"A": 0.90, "B": 0.95, "C": 0.99},
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": "id-941-unsupported-2",
            "name": "Per-class demand",
            "service_level": None,
            "demand_multiplier": None,
            "lead_time_multiplier": None,
            "demand_multiplier_by_class": {"A": 1.5, "B": 1.0, "C": 0.8},
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": "id-941-supported",
            "name": "Supported levers",
            "service_level": None,
            "demand_multiplier": 1.2,
            "lead_time_multiplier": None,
            "service_level_by_class": None,
            "demand_multiplier_by_class": None,
            "created_at": now,
            "updated_at": now,
        },
    ]

    def fake_list_all():
        return [dict(row) for row in unsupported_rows]

    monkeypatch.setattr(review_module._scenarios_repo, "list_all", fake_list_all)
    response = client.get(URL, params={"months": 3})
    assert response.status_code == 200, response.text  # never a 422 for the row
    body = response.json()
    assert body["scenarios_total"] == 3
    assert len(body["scenarios"]) == 3
    assert body["baseline"]["demand_value"] is not None
    unsupported = next(
        row for row in body["scenarios"] if row["name"] == "Per-class service level"
    )
    unsupported_other = next(
        row for row in body["scenarios"] if row["name"] == "Per-class demand"
    )
    supported = next(
        row for row in body["scenarios"] if row["name"] == "Supported levers"
    )
    # The stable, user-safe error: a fixed message, no row internals, no
    # exception detail — the same for every unsupported lever the row
    # carries, whatever its name.
    assert unsupported["error"] == review_module.UNSUPPORTED_LEVER_ERROR
    assert unsupported_other["error"] == review_module.UNSUPPORTED_LEVER_ERROR
    assert "service_level_by_class" not in unsupported["error"]
    assert "demand_multiplier_by_class" not in unsupported_other["error"]
    assert "Traceback" not in unsupported["error"]
    for row in (unsupported, unsupported_other):
        for field in (
            "demand_value",
            "total_spend",
            "months_over_budget",
            "months_short_planned",
            "demand_value_delta",
            "total_spend_delta",
        ):
            assert row[field] is None, (row["name"], field)
    # The other row still answers, filled.
    assert supported["error"] is None
    assert supported["demand_value"] is not None
    assert supported["total_spend"] is not None
    assert supported["demand_value_delta"] is not None
    assert supported["total_spend_delta"] is not None
