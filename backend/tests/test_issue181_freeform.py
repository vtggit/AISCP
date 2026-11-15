"""Issue #181 proof: drift rows carry their current planner decision.

The proof covers, in one test:

* the new ``DriftDecisionRow`` response shape: ``GET /api/drift`` and
  ``GET /api/drift/by-key`` answer with every ``DriftRow`` field plus
  ``decision`` (the item-site's current ``planner_decisions`` row —
  the latest by ``decided_at``, ``id`` breaking ties, exactly what
  ``GET /api/drift/decisions`` reports — with every stored column, or
  null when the item-site has none) and ``decision_lapsed`` (boolean),
* the dismissal lapse rule: a dismissal lapses when the row's
  ``reorder_point_units`` differs from the decision's
  ``recommended_reorder_point_units`` by at least the larger of 1 and
  10% of that recommended value.  A dismissed row stays hidden by the
  default list while it is within the threshold, and reappears with
  ``decision_lapsed`` true once it moves beyond it;
* ``include_decided`` (boolean, default false): while false, a row
  whose current decision is a not-yet-lapsed dismissal, or a snooze
  whose ``snooze_until`` is after today, is left out of the list
  before ``offset``/``limit`` are applied; accepted, lapsed,
  snoozed-until-today-or-earlier and undecided rows stay listed; while
  true, every row is listed.  Every listed row keeps the ``rank``
  ``drift_ranked`` gave it,
* ``GET /api/drift/by-key`` always returns the row with its decision
  and never hides it,
* the enrichment is not limited to the first fetched page: a row whose
  latest decision sits beyond the first page of
  ``GET /api/drift/decisions`` is still returned with its decision
  (not null),
* snooze visibility depends only on ``snooze_until``: rows snoozed
  until yesterday and until today are listed by default, and a snooze
  whose ``recommended_reorder_point_units`` was moved beyond the lapse
  threshold is not lapsed — the threshold never applies to a snooze,
* the OData ``Drift`` set and ``$metadata`` are unchanged: the
  ``Drift`` EntityType declares no ``decision`` or ``decision_lapsed``
  property, and ``GET /odata/Drift`` rows carry neither,
* the OpenAPI schema carries the ``DriftDecisionRow`` model (with the
  nullable ``PlannerDecision`` and the boolean ``decision_lapsed``)
  and the ``include_decided`` query parameter on ``GET /api/drift``.

Seven item-sites are seeded on fixed past dates: 10/day demand from
2025-09-01 through 2026-08-31 (the latest demand event, so the
default as-of is 2026-08-31), pooled 9/10/10-day lead times, and a
reorder point above the recommended ~106.16 units (whole-unit
reorder_point_units 107 for every site), so every site has an
"over" drift row ranked 1..7 by money at stake.
"""

import base64
import xml.etree.ElementTree as ElementTree
from datetime import UTC, date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

SITE_CODE = "SITE-181"
FIRST_DAY = date(2025, 9, 1)
DEMAND_DAYS = 365  # 10/day through 2026-08-31
AS_OF = "2026-08-31"  # the latest seeded demand event
UNIT_COST = 2.0
RECOMMENDED_UNITS = 107  # ceil of the recommended reorder point
DECIDED_BY = "dev-admin-1"
EDM = "http://docs.oasis-open.org/odata/ns/edm"
ODATA_USER = "odata-machine-181"
ODATA_PASSWORD = "feed-secret-181"
FILLER_COUNT = 104  # pushes one real decision past the first 100-row page

DECISION_COLUMNS = {
    "id",
    "item_code",
    "site_code",
    "decision",
    "reason",
    "snooze_until",
    "as_of",
    "recommended_reorder_point_units",
    "current_reorder_point",
    "money_at_stake",
    "decided_by",
    "decided_by_name",
    "decided_at",
}

ROW_COLUMNS = {
    "item_code",
    "site_code",
    "as_of",
    "category",
    "rank",
    "direction",
    "units_gap",
    "money_at_stake",
    "unit_cost",
    "notes",
    "money_basis",
    "reorder_point",
    "reorder_point_units",
    "safety_stock",
    "arithmetic",
    "current_reorder_point",
    "mu_l",
    "sigma_l",
    "k",
    "units_short_per_cycle",
    "order_qty",
    "cycles_per_year",
    "units_short_per_year",
    "item_description",
    "decision",
    "decision_lapsed",
    "demand_basis",
    "other_basis",
    "order_quantity",
    "service_level_source",
    "service_level",
}

# item_code -> current reorder point; money at stake (units_gap *
# UNIT_COST) falls with the point, so the sites rank A=1 .. G=7.
ITEMS = {
    "A-181": 200.0,  # undecided: listed with decision null
    "B-181": 150.0,  # accepted: listed with its decision
    "C-181": 140.0,  # dismissed, within the lapse threshold: hidden
    "D-181": 130.0,  # dismissed, then moved beyond the threshold: lapsed
    "E-181": 125.0,  # snoozed until tomorrow: hidden by default
    "F-181": 122.0,  # snoozed until yesterday, recommendation moved beyond threshold
    "G-181": 121.0,  # snoozed until today, decision beyond the first decisions page
}


def _seed() -> None:
    """The fixed-date rows: seven item-sites, one drift row each."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            demand_rows = []
            lead_time_rows = []
            item_site_rows = []
            for item_code, reorder_point in ITEMS.items():
                suffix = item_code.split("-")[0].lower()
                demand_rows.extend(
                    (
                        f"id-de-181-{suffix}-{day_index}",
                        f"de-181-{suffix}-{day_index}",
                        item_code,
                        SITE_CODE,
                        10.0,
                        (FIRST_DAY + timedelta(days=day_index)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for day_index in range(DEMAND_DAYS)
                )
                lead_time_rows.extend(
                    (
                        f"id-lt-181-{suffix}-{index}",
                        f"lt-181-{suffix}-{index}",
                        f"SUP-181-{suffix}",
                        item_code,
                        ordered.isoformat(),
                        received.isoformat(),
                        "test",
                    )
                    for index, (ordered, received) in enumerate(
                        (
                            (date(2026, 5, 1), date(2026, 5, 10)),  # 9 days
                            (date(2026, 5, 5), date(2026, 5, 15)),  # 10 days
                            (date(2026, 5, 9), date(2026, 5, 19)),  # 10 days
                        ),
                        start=1,
                    )
                )
                item_site_rows.append(
                    (
                        f"id-is-181-{suffix}",
                        item_code,
                        SITE_CODE,
                        UNIT_COST,
                        reorder_point,
                        80.0,
                        "test",
                    )
                )
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                demand_rows,
            )
            cur.executemany(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, ordered_on, "
                "received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                lead_time_rows,
            )
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, reorder_point, "
                "order_qty, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                item_site_rows,
            )
        conn.commit()
    finally:
        conn.close()


def _post_decision(client, item_code: str, decision: str, **kwargs) -> dict:
    """One decision through the real API; returns the stored row."""
    body = {
        "item_code": item_code,
        "site_code": SITE_CODE,
        "decision": decision,
        **kwargs,
    }
    response = client.post("/api/drift/decisions", json=body)
    assert response.status_code == 201, (item_code, response.status_code, response.text)
    row = response.json()
    assert set(row.keys()) == DECISION_COLUMNS, row
    assert row["item_code"] == item_code
    assert row["site_code"] == SITE_CODE
    assert row["decision"] == decision
    assert row["as_of"] == AS_OF
    assert row["recommended_reorder_point_units"] == RECOMMENDED_UNITS
    assert row["current_reorder_point"] == ITEMS[item_code]
    assert row["decided_by"] == DECIDED_BY
    return row


def _update_decision(decision_id: str, **changes) -> None:
    """Move a stored decision directly in the table (the append-only
    rows are the planner's record; the proof simulates the row's
    recommendation drifting away from what was dismissed)."""
    assignments = ", ".join(f"{column} = %s" for column in changes)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE planner_decisions SET {assignments} WHERE id = %s",
                (*changes.values(), decision_id),
            )
            assert cur.rowcount == 1, f"no decision {decision_id} to move"
        conn.commit()
    finally:
        conn.close()


def _seed_fillers() -> list[str]:
    """FILLER_COUNT decisions, newer than every real one, so the real
    decisions sit beyond the first page (100 rows) of
    ``GET /api/drift/decisions``."""
    decided_at = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    ids = [f"id-181-fill-{index:03d}" for index in range(FILLER_COUNT)]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO planner_decisions "
                "(id, item_code, site_code, decision, reason, snooze_until, "
                "as_of, recommended_reorder_point_units, current_reorder_point, "
                "money_at_stake, decided_by, decided_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        decision_id,
                        f"FILL-181-{index:03d}",
                        "SITE-181-FILL",
                        "accepted",
                        "beyond the first page",
                        None,
                        AS_OF,
                        RECOMMENDED_UNITS,
                        100.0,
                        10.0,
                        "seeder",
                        decided_at,
                    )
                    for index, decision_id in enumerate(ids)
                ],
            )
        conn.commit()
    finally:
        conn.close()
    return ids


def _assert_row(row: dict) -> None:
    """One DriftDecisionRow: every DriftRow field, the decision and the flag."""
    assert set(row.keys()) == ROW_COLUMNS, row
    assert row["as_of"] == AS_OF
    assert row["category"] == "drift"
    assert row["direction"] == "over"
    assert row["reorder_point_units"] == RECOMMENDED_UNITS
    assert row["site_code"] == SITE_CODE


def _decision_by_code(body: list[dict]) -> dict[str, dict | None]:
    return {row["item_code"]: row["decision"] for row in body}


def test_issue181_freeform(client, admin_token, monkeypatch):
    admin_client_headers = {"Authorization": f"Bearer {admin_token}"}
    today = date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()

    _seed()

    # -- AC-3: undecided rows are listed with decision null. -------------
    response = client.get("/api/drift", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == list(ITEMS), body
    assert [row["rank"] for row in body] == [1, 2, 3, 4, 5, 6, 7]
    for row in body:
        _assert_row(row)
        assert row["decision"] is None
        assert row["decision_lapsed"] is False

    # -- Decisions recorded through POST /api/drift/decisions. -----------
    # The shared client authenticates as a plain user; decisions need
    # the admin role, so the admin client posts them.
    admin_client = TestClient(app, headers=admin_client_headers)
    accepted = _post_decision(admin_client, "B-181", "accepted", reason="looks right")
    dismissed_c = _post_decision(
        admin_client,
        "C-181",
        "dismissed",
        reason="not worth changing",
    )
    dismissed_d = _post_decision(
        admin_client,
        "D-181",
        "dismissed",
        reason="will revisit later",
    )
    snoozed_e = _post_decision(
        admin_client,
        "E-181",
        "snoozed",
        snooze_until=tomorrow,
    )
    snoozed_f = _post_decision(
        admin_client,
        "F-181",
        "snoozed",
        snooze_until=tomorrow,
    )
    snoozed_g = _post_decision(
        admin_client,
        "G-181",
        "snoozed",
        snooze_until=tomorrow,
    )
    assert snoozed_e["snooze_until"] == tomorrow
    assert snoozed_f["snooze_until"] == tomorrow
    assert snoozed_g["snooze_until"] == tomorrow

    # -- AC-6: a dismissal within the lapse threshold stays hidden. -----
    # D's dismissal still matches the row (107 vs 107, within the
    # larger of 1 and 10.7), so the default list drops C and D (not
    # yet lapsed dismissals) and E/F/G (snoozed until tomorrow); the
    # accepted row B and the undecided row A stay.
    response = client.get("/api/drift", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == ["A-181", "B-181"], body
    assert [row["rank"] for row in body] == [1, 2]
    by_code = {row["item_code"]: row for row in body}
    assert by_code["A-181"]["decision"] is None
    assert by_code["A-181"]["decision_lapsed"] is False
    b_row = by_code["B-181"]
    assert b_row["decision_lapsed"] is False
    assert b_row["decision"] == accepted
    # Rows are left out before offset and limit are applied: offset=1
    # lands on the visible rank-2 row B, not the hidden rank-3 row C.
    response = client.get("/api/drift", params={"limit": 1, "offset": 1})
    assert response.status_code == 200, response.text
    assert [row["item_code"] for row in response.json()] == ["B-181"]
    assert response.json()[0]["rank"] == 2

    # include_decided=true lists every row, hidden ones included, with
    # the same ranks drift_ranked gave them.
    response = client.get(
        "/api/drift",
        params={"limit": 1000, "include_decided": "true"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == list(ITEMS), body
    assert [row["rank"] for row in body] == [1, 2, 3, 4, 5, 6, 7]
    for row in body:
        assert row["decision_lapsed"] is False, row
    decisions = _decision_by_code(body)
    assert decisions["A-181"] is None
    assert decisions["B-181"]["decision"] == "accepted"
    assert decisions["C-181"]["decision"] == "dismissed"
    assert decisions["D-181"]["decision"] == "dismissed"
    assert decisions["E-181"]["decision"] == "snoozed"
    assert decisions["E-181"]["snooze_until"] == tomorrow
    assert decisions["F-181"]["snooze_until"] == tomorrow
    assert decisions["G-181"]["snooze_until"] == tomorrow

    # -- by-key always returns the row with its decision, never hides it.
    response = client.get(
        "/api/drift/by-key",
        params={"item_code": "C-181", "site_code": SITE_CODE},
    )
    assert response.status_code == 200, response.text
    row = response.json()
    _assert_row(row)
    assert row["rank"] == 3
    assert row["decision_lapsed"] is False
    assert row["decision"]["decision"] == "dismissed"
    assert row["decision"]["id"] == dismissed_c["id"]
    assert row["decision"]["reason"] == "not worth changing"

    response = client.get(
        "/api/drift/by-key",
        params={"item_code": "A-181", "site_code": SITE_CODE},
    )
    assert response.status_code == 200, response.text
    assert response.json()["decision"] is None
    assert response.json()["decision_lapsed"] is False

    # -- AC-6: moving the dismissal beyond the threshold lapses it. -----
    # D's recommended 90 differs from the row's 107 by 17, beyond the
    # larger of 1 and 10% of 90 (= 9): lapsed, listed with the flag.
    _update_decision(dismissed_d["id"], recommended_reorder_point_units=90)
    response = client.get("/api/drift", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == [
        "A-181",
        "B-181",
        "D-181",
    ], body
    assert [row["rank"] for row in body] == [1, 2, 4]
    d_row = body[2]
    assert d_row["decision_lapsed"] is True
    assert d_row["decision"]["decision"] == "dismissed"
    assert d_row["decision"]["id"] == dismissed_d["id"]
    assert d_row["decision"]["recommended_reorder_point_units"] == 90
    # C is still within its threshold: still hidden by default.
    assert "C-181" not in {row["item_code"] for row in body}

    # -- AC-2/AC-6: snooze visibility depends only on snooze_until. -----
    # F snoozes until yesterday and G until today: both stay listed,
    # and F's snooze carries no lapse even though its recommendation
    # moved beyond the threshold (107 vs 90).
    _update_decision(
        snoozed_f["id"], snooze_until=yesterday, recommended_reorder_point_units=90
    )
    _update_decision(snoozed_g["id"], snooze_until=today.isoformat())
    response = client.get("/api/drift", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == [
        "A-181",
        "B-181",
        "D-181",
        "F-181",
        "G-181",
    ], body
    # Ranks unchanged by the filtering: 3 (C) and 5 (E) are simply gone.
    assert [row["rank"] for row in body] == [1, 2, 4, 6, 7]
    f_row = {row["item_code"]: row for row in body}["F-181"]
    assert f_row["decision"]["decision"] == "snoozed"
    assert f_row["decision"]["snooze_until"] == yesterday
    assert f_row["decision"]["recommended_reorder_point_units"] == 90
    assert f_row["decision_lapsed"] is False  # the threshold never snoozes
    g_row = {row["item_code"]: row for row in body}["G-181"]
    assert g_row["decision"]["snooze_until"] == today.isoformat()
    assert g_row["decision_lapsed"] is False

    # include_decided=true now also lists C (dismissed, not lapsed) and
    # E (snoozed until tomorrow).
    response = client.get(
        "/api/drift",
        params={"limit": 1000, "include_decided": "true"},
    )
    assert [row["item_code"] for row in response.json()] == list(ITEMS)
    assert [row["rank"] for row in response.json()] == [1, 2, 3, 4, 5, 6, 7]
    e_row = {row["item_code"]: row for row in response.json()}["E-181"]
    assert e_row["decision"]["decision"] == "snoozed"
    assert e_row["decision"]["snooze_until"] == tomorrow
    assert e_row["decision_lapsed"] is False

    # by-key returns the hidden rows too, each with its decision.
    for item_code, expected in (
        ("C-181", "dismissed"),
        ("E-181", "snoozed"),
        ("D-181", "dismissed"),
        ("G-181", "snoozed"),
    ):
        response = client.get(
            "/api/drift/by-key",
            params={"item_code": item_code, "site_code": SITE_CODE},
        )
        assert response.status_code == 200, (item_code, response.text)
        row = response.json()
        _assert_row(row)
        assert row["decision"] is not None
        assert row["decision"]["decision"] == expected
        if item_code == "D-181":
            assert row["decision_lapsed"] is True
        else:
            assert row["decision_lapsed"] is False
    response = client.get(
        "/api/drift/by-key",
        params={"item_code": "GHOST-181", "site_code": SITE_CODE},
    )
    assert response.status_code == 404

    # -- AC-2: enrichment is not limited to the first fetched page. -----
    filler_ids = _seed_fillers()
    first_page = client.get("/api/drift/decisions", params={"limit": 100})
    assert first_page.status_code == 200
    page_ids = [row["id"] for row in first_page.json()]
    assert len(page_ids) == 100
    # The 104 fillers are newer than every real decision, so the first
    # page holds only fillers (id DESC breaks the decided_at tie).
    assert page_ids == sorted(filler_ids, reverse=True)[:100]
    assert snoozed_g["id"] not in page_ids
    # G's latest decision sits beyond that first page, yet the drift
    # row still carries it.
    response = client.get("/api/drift", params={"limit": 1000})
    assert response.status_code == 200, response.text
    g_row = {row["item_code"]: row for row in response.json()}["G-181"]
    assert g_row["decision"] is not None
    assert g_row["decision"]["id"] == snoozed_g["id"]
    assert g_row["decision"]["snooze_until"] == today.isoformat()
    # And so does by-key.
    response = client.get(
        "/api/drift/by-key",
        params={"item_code": "G-181", "site_code": SITE_CODE},
    )
    assert response.status_code == 200
    assert response.json()["decision"]["id"] == snoozed_g["id"]

    # -- AC-3: the OData Drift set is unchanged by the enrichment. ------
    monkeypatch.setenv("AIERP_ODATA_USER", ODATA_USER)
    monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
    token = base64.b64encode(f"{ODATA_USER}:{ODATA_PASSWORD}".encode()).decode()
    odata_headers = {"Authorization": f"Basic {token}"}
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
    assert "decision" not in property_names, property_names
    assert "decision_lapsed" not in property_names, property_names
    # Sanity: it is still the drift row's shape.
    assert {"rank", "notes", "reorder_point_units"} <= property_names

    drift_set = client.get("/odata/Drift", headers=odata_headers)
    assert drift_set.status_code == 200, drift_set.text
    odata_rows = drift_set.json()["value"]
    assert {row["item_code"] for row in odata_rows} == set(ITEMS)
    for row in odata_rows:
        assert "decision" not in row
        assert "decision_lapsed" not in row

    # include_decided is a plain boolean query parameter: hostile values
    # answer 422, never 500.
    for hostile in ("", "xyz", "x" * 100, "\x00", "tru\x01e"):
        assert (
            client.get("/api/drift", params={"include_decided": hostile}).status_code
            == 422
        )

    # -- AC-5: the OpenAPI schema carries the new shape and parameter. --
    spec = app.openapi()
    get_drift = spec["paths"]["/api/drift"]["get"]
    parameters = {param["name"]: param for param in get_drift["parameters"]}
    include_decided = parameters["include_decided"]
    assert include_decided["in"] == "query"
    assert include_decided["schema"]["type"] == "boolean"
    assert include_decided["schema"]["default"] is False
    assert include_decided.get("required") in (None, False)
    for name in ("limit", "offset", "direction", "category", "as_of"):
        assert name in parameters, name
    list_schema = get_drift["responses"]["200"]["content"]["application/json"]["schema"]
    assert list_schema["type"] == "array"
    assert list_schema["items"] == {"$ref": "#/components/schemas/DriftDecisionRow"}
    by_key_schema = spec["paths"]["/api/drift/by-key"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert by_key_schema == {"$ref": "#/components/schemas/DriftDecisionRow"}
    decision_row = spec["components"]["schemas"]["DriftDecisionRow"]
    properties = decision_row["properties"]
    assert properties["decision_lapsed"]["type"] == "boolean"
    decision_schema = properties["decision"]
    assert {"$ref": "#/components/schemas/PlannerDecision"} in decision_schema.get(
        "anyOf", []
    ), decision_schema
    assert {"type": "null"} in decision_schema.get("anyOf", []), decision_schema
    # DriftDecisionRow is DriftRow plus the decision fields and the
    # demand-basis fields; the OData set (which reads DriftRow directly)
    # is proven unchanged by the $metadata assertions above.
    from app.api.drift import DriftRow

    assert set(properties) == set(DriftRow.model_fields) | {
        "decision",
        "decision_lapsed",
        "demand_basis",
        "other_basis",
        "order_quantity",
        "service_level_source",
        "service_level",
    }
