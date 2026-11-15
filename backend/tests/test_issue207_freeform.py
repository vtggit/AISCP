"""Issue #207 proof: excess rows carry their current excess decision.

The proof covers, in one test:

* the new ``ExcessDecisionRow`` response shape: ``GET /api/excess``
  answers with every ``ExcessRow`` field plus ``decision`` (the
  item-site's current ``excess_decisions`` row — the latest by
  ``decided_at``, ``id`` breaking ties, exactly what
  ``GET /api/excess/decisions`` reports — with every stored column, or
  null when the item-site has none) and ``decision_lapsed`` (boolean);
  ``ExcessRow`` itself is unchanged, so the OData ``Excess`` set, its
  ``$metadata`` and the CSV export are unchanged,
* the dismissal lapse rule: a dismissal lapses when the row's
  ``excess_units`` exceed the decision's ``excess_units`` by at least
  the larger of 1 and 10% of the decision's ``excess_units``.  A
  dismissed row stays hidden by the default list while it is within the
  threshold, and is listed with ``decision_lapsed`` true once the row's
  excess outgrows the stored one beyond it; a snooze never lapses,
* ``include_decided`` (boolean, default false): while false, a row
  whose current decision is a not-yet-lapsed dismissal, or a snooze
  whose ``snooze_until`` is after today (the server's UTC date), is
  left out of the list; accepted, lapsed, undecided and expired-snooze
  rows stay listed; while true, every row is listed.  Rows are left
  out after the ``category`` and ``dead_reason`` filters and before
  ``offset`` and ``limit`` are applied, and every row keeps the
  ``rank`` ``excess_ranked`` gave it,
* the enrichment reads the current decisions with one query per
  request — never one per row — and is not limited to the first
  fetched page: a row whose latest decision sits beyond the first page
  of ``GET /api/excess/decisions`` is still returned with its decision
  (not null),
* the OpenAPI schema carries the ``ExcessDecisionRow`` model (with the
  nullable ``ExcessDecision`` and the boolean ``decision_lapsed``) and
  the ``include_decided`` query parameter on ``GET /api/excess``.

Six item-sites are seeded on fixed past dates: 10/day-free demand of
2/day (ACC, UND) and 1/day (DIS) from 2025-07-06 through 2026-06-30
(the latest demand event, so the default as-of is 2026-06-30), and no
demand at all for the three dead (never_moved) item-sites, so every
site is listed — ranks 1..6 by money tied up (DIS 700, LAP 600, ACC
560, SNO 200, UND 180, EXP 160).  The decisions are recorded through
POST /api/excess/decisions; the lapse case moves the stored
``excess_units`` directly in the table, and the expired-snooze case
moves the stored ``snooze_until`` directly in the table.
"""

import base64
import csv
import io
import xml.etree.ElementTree as ElementTree
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

import app.api.excess as excess_api
from app.api.excess import ExcessRow
from app.db.connection import get_connection
from app.main import app

SITE_CODE = "SITE-207"
FIRST_DAY = date(2025, 7, 6)  # demand from here ...
DEMAND_DAYS = 360  # ... through 2026-06-30
AS_OF = "2026-06-30"  # the latest seeded demand event
AS_OF_STAMP = "2026-06-30 00:00:00+00"
ADMIN_SUB = "dev-admin-1"
EDM = "http://docs.oasis-open.org/odata/ns/edm"
ODATA_USER = "odata-machine-207"
ODATA_PASSWORD = "feed-secret-207"
FILLER_COUNT = 104  # pushes the real decisions past the first 100-row page

DIS_ITEM = "ITEM-207-DIS"  # excess, dismissed, within the lapse threshold
LAP_ITEM = "ITEM-207-LAP"  # dead, dismissed, stored excess_units lowered -> lapsed
ACC_ITEM = "ITEM-207-ACC"  # excess, accepted
SNO_ITEM = "ITEM-207-SNO"  # dead, snoozed until tomorrow
UND_ITEM = "ITEM-207-UND"  # excess, never decided
EXP_ITEM = "ITEM-207-EXP"  # dead, snoozed, snooze_until moved to the past

# item_code -> (on_hand, unit_cost, daily demand or None); money tied up
# (excess_units * unit_cost) falls with the pair, so the sites rank
# DIS=1, LAP=2, ACC=3, SNO=4, UND=5, EXP=6 by money.
ITEMS = {
    DIS_ITEM: (500.0, 5.0, 1.0),
    LAP_ITEM: (150.0, 4.0, None),
    ACC_ITEM: (1000.0, 2.0, 2.0),
    SNO_ITEM: (200.0, 1.0, None),
    UND_ITEM: (900.0, 1.0, 2.0),
    EXP_ITEM: (100.0, 1.6, None),
}

EXCESS_UNITS = {
    DIS_ITEM: 140.0,  # 500 - ceil(360 * 1/day)
    LAP_ITEM: 150.0,  # dead: the whole on hand
    ACC_ITEM: 280.0,  # 1000 - ceil(720 * 2/day)
    SNO_ITEM: 200.0,  # dead: the whole on hand
    UND_ITEM: 180.0,  # 900 - ceil(720 * 2/day)
    EXP_ITEM: 100.0,  # dead: the whole on hand
}

MONEY_TIED_UP = {
    DIS_ITEM: 700.0,
    LAP_ITEM: 600.0,
    ACC_ITEM: 560.0,
    SNO_ITEM: 200.0,
    UND_ITEM: 180.0,
    EXP_ITEM: 160.0,
}

EXPECTED_RANK = {
    DIS_ITEM: 1,
    LAP_ITEM: 2,
    ACC_ITEM: 3,
    SNO_ITEM: 4,
    UND_ITEM: 5,
    EXP_ITEM: 6,
}

DECISION_COLUMNS = {
    "id",
    "item_code",
    "site_code",
    "decision",
    "action",
    "reason",
    "snooze_until",
    "as_of",
    "category",
    "dead_reason",
    "on_hand_units",
    "excess_units",
    "money_tied_up",
    "decided_by",
    "decided_by_name",
    "decided_at",
}

ROW_COLUMNS = set(ExcessRow.model_fields) | {"decision", "decision_lapsed"}

CSV_HEADERS = [
    "rank",
    "category",
    "dead_reason",
    "item_code",
    "site_code",
    "on_hand_units",
    "justified_units",
    "excess_units",
    "months_of_cover",
    "unit_cost",
    "money_tied_up",
    "last_demand_on",
    "demand_window_units",
    "notes",
    "arithmetic",
    "as_of",
]


def _seed() -> None:
    """The fixed-date rows: six item-sites, one listed row each."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            demand_rows = []
            item_site_rows = []
            on_hand_rows = []
            for item_code, (on_hand, unit_cost, daily_demand) in ITEMS.items():
                suffix = item_code.split("-")[2].lower()
                if daily_demand is not None:
                    demand_rows.extend(
                        (
                            f"id-de-207-{suffix}-{day_index}",
                            f"de-207-{suffix}-{day_index}",
                            item_code,
                            SITE_CODE,
                            daily_demand,
                            (FIRST_DAY + timedelta(days=day_index)).isoformat(),
                            "shipment",
                            "test",
                        )
                        for day_index in range(DEMAND_DAYS)
                    )
                item_site_rows.append(
                    (
                        f"id-is-207-{suffix}",
                        item_code,
                        SITE_CODE,
                        unit_cost,
                        None,
                        None,
                        "test",
                    )
                )
                on_hand_rows.append(
                    (
                        f"id-oh-207-{suffix}",
                        item_code,
                        SITE_CODE,
                        on_hand,
                        AS_OF_STAMP,
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
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, reorder_point, "
                "order_qty, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                item_site_rows,
            )
            cur.executemany(
                "INSERT INTO on_hand "
                "(id, item_code, site_code, quantity_on_hand, as_of, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                on_hand_rows,
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
    response = client.post("/api/excess/decisions", json=body)
    assert response.status_code == 201, (item_code, response.status_code, response.text)
    row = response.json()
    assert set(row.keys()) == DECISION_COLUMNS, row
    assert row["item_code"] == item_code
    assert row["site_code"] == SITE_CODE
    assert row["decision"] == decision
    assert row["as_of"] == AS_OF
    on_hand, _unit_cost, _daily_demand = ITEMS[item_code]
    assert row["on_hand_units"] == on_hand
    assert row["excess_units"] == EXCESS_UNITS[item_code]
    assert row["money_tied_up"] == MONEY_TIED_UP[item_code]
    assert row["category"] == ("excess" if _daily_demand is not None else "dead")
    assert row["dead_reason"] == (None if _daily_demand is not None else "never_moved")
    assert row["decided_by"] == ADMIN_SUB
    return row


def _update_decision(decision_id: str, **changes) -> None:
    """Move a stored decision directly in the table (the append-only
    rows are the planner's record; the proof simulates the row moving
    away from what was dismissed / the snooze running out)."""
    assignments = ", ".join(f"{column} = %s" for column in changes)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE excess_decisions SET {assignments} WHERE id = %s",
                (*changes.values(), decision_id),
            )
            assert cur.rowcount == 1, f"no decision {decision_id} to move"
        conn.commit()
    finally:
        conn.close()


def _seed_fillers() -> list[str]:
    """FILLER_COUNT decisions, newer than every real one, so the real
    decisions sit beyond the first page (100 rows) of
    ``GET /api/excess/decisions``.  The filler item-sites have no
    on-hand rows, so they are never listed by GET /api/excess."""
    decided_at = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    ids = [f"id-207-fill-{index:03d}" for index in range(FILLER_COUNT)]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO excess_decisions "
                "(id, item_code, site_code, decision, action, reason, "
                "snooze_until, as_of, category, dead_reason, on_hand_units, "
                "excess_units, money_tied_up, decided_by, decided_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s)",
                [
                    (
                        decision_id,
                        f"FILL-207-{index:03d}",
                        "SITE-207-FILL",
                        "accepted",
                        "stop_replenishing",
                        "beyond the first page",
                        None,
                        AS_OF,
                        "dead",
                        "never_moved",
                        1.0,
                        1.0,
                        1.0,
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
    """One ExcessDecisionRow: every ExcessRow field, the decision and
    the flag."""
    assert set(row.keys()) == ROW_COLUMNS, row
    assert row["as_of"] == AS_OF
    assert row["site_code"] == SITE_CODE
    assert row["excess_units"] == EXCESS_UNITS[row["item_code"]]
    assert row["money_tied_up"] == MONEY_TIED_UP[row["item_code"]]
    assert row["rank"] == EXPECTED_RANK[row["item_code"]]


def _decision_by_code(body: list[dict]) -> dict[str, dict | None]:
    return {row["item_code"]: row["decision"] for row in body}


def test_issue207_freeform(client, admin_token, monkeypatch):
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    _seed()

    # -- AC-3: undecided rows are listed with decision null. -------------
    response = client.get("/api/excess", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == list(ITEMS), body
    assert [row["rank"] for row in body] == [EXPECTED_RANK[code] for code in ITEMS]
    for row in body:
        _assert_row(row)
        assert row["decision"] is None
        assert row["decision_lapsed"] is False

    # -- Decisions recorded through POST /api/excess/decisions. ----------
    accepted = _post_decision(
        admin_client, ACC_ITEM, "accepted", action="stop_replenishing"
    )
    dismissed = _post_decision(
        admin_client, DIS_ITEM, "dismissed", reason="superseded by newer SKU"
    )
    snoozed = _post_decision(admin_client, SNO_ITEM, "snoozed", snooze_until=tomorrow)
    lapsed_dismissal = _post_decision(
        admin_client, LAP_ITEM, "dismissed", reason="review scheduled"
    )
    expired_snooze = _post_decision(
        admin_client, EXP_ITEM, "snoozed", snooze_until=tomorrow
    )
    assert snoozed["snooze_until"] == tomorrow
    assert expired_snooze["snooze_until"] == tomorrow
    assert dismissed["excess_units"] == EXCESS_UNITS[DIS_ITEM]
    assert lapsed_dismissal["excess_units"] == EXCESS_UNITS[LAP_ITEM]

    # -- AC-3: not-yet-lapsed dismissals and snoozes are left out. ------
    # DIS's stored excess_units (140) still match the row's 140, within
    # the larger of 1 and 10% of 140 (= 14), and LAP's stored 150 still
    # match the row's 150: the default list drops DIS and LAP (not
    # yet-lapsed dismissals) and SNO and EXP (snoozed until tomorrow);
    # the accepted row ACC and the undecided row UND stay.
    response = client.get("/api/excess", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == [ACC_ITEM, UND_ITEM], body
    by_code = {row["item_code"]: row for row in body}
    # An undecided row is listed with decision null.
    assert by_code[UND_ITEM]["decision"] is None
    assert by_code[UND_ITEM]["decision_lapsed"] is False
    # An accepted row is listed with its decision.
    acc_row = by_code[ACC_ITEM]
    assert acc_row["decision_lapsed"] is False
    assert acc_row["decision"] == accepted
    # The dismissed rows are left out by default; so is the snooze
    # until tomorrow.
    assert DIS_ITEM not in by_code
    assert LAP_ITEM not in by_code
    assert SNO_ITEM not in by_code
    assert EXP_ITEM not in by_code
    # Rows are left out before offset and limit are applied: offset=1
    # lands on the visible rank-5 row UND, not the hidden rank-4 row SNO.
    response = client.get("/api/excess", params={"limit": 1, "offset": 1})
    assert response.status_code == 200, response.text
    assert [row["item_code"] for row in response.json()] == [UND_ITEM]
    assert response.json()[0]["rank"] == 5

    # include_decided=true lists every row, hidden ones included, with
    # the ranks excess_ranked gave them (unchanged by the filtering).
    response = client.get(
        "/api/excess",
        params={"limit": 1000, "include_decided": "true"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == list(ITEMS), body
    assert [row["rank"] for row in body] == [EXPECTED_RANK[code] for code in ITEMS]
    for row in body:
        assert row["decision_lapsed"] is False, row
    decisions = _decision_by_code(body)
    assert decisions[UND_ITEM] is None
    assert decisions[ACC_ITEM]["decision"] == "accepted"
    assert decisions[ACC_ITEM]["action"] == "stop_replenishing"
    assert decisions[DIS_ITEM]["decision"] == "dismissed"
    assert decisions[LAP_ITEM]["decision"] == "dismissed"
    assert decisions[SNO_ITEM]["decision"] == "snoozed"
    assert decisions[SNO_ITEM]["snooze_until"] == tomorrow
    assert decisions[EXP_ITEM]["decision"] == "snoozed"
    assert decisions[EXP_ITEM]["snooze_until"] == tomorrow

    # -- AC-3: moving the dismissal beyond the threshold lapses it. -----
    # Lower the stored excess_units directly in the table: the row's
    # 150 now exceed the decision's 100 by 50, beyond the larger of 1
    # and 10% of 100 (= 10): lapsed, listed with the flag.
    _update_decision(lapsed_dismissal["id"], excess_units=100.0)
    response = client.get("/api/excess", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == [
        LAP_ITEM,
        ACC_ITEM,
        UND_ITEM,
    ], body
    assert [row["rank"] for row in body] == [2, 3, 5]
    lap_row = {row["item_code"]: row for row in body}[LAP_ITEM]
    assert lap_row["decision_lapsed"] is True
    assert lap_row["decision"]["decision"] == "dismissed"
    assert lap_row["decision"]["id"] == lapsed_dismissal["id"]
    assert lap_row["decision"]["excess_units"] == 100.0
    assert lap_row["excess_units"] == 150.0
    # DIS is still within its threshold: still hidden by default.
    assert DIS_ITEM not in {row["item_code"] for row in body}
    # include_decided=true now also lists DIS (dismissed, not lapsed)
    # and SNO (snoozed until tomorrow).
    response = client.get(
        "/api/excess",
        params={"limit": 1000, "include_decided": "true"},
    )
    assert [row["item_code"] for row in response.json()] == list(ITEMS)
    all_rows = {row["item_code"]: row for row in response.json()}
    assert all_rows[DIS_ITEM]["decision_lapsed"] is False
    assert all_rows[DIS_ITEM]["decision"]["id"] == dismissed["id"]
    assert all_rows[SNO_ITEM]["decision_lapsed"] is False
    assert all_rows[SNO_ITEM]["decision"]["snooze_until"] == tomorrow

    # -- AC-3: a snooze until tomorrow is left out by default. -----------
    # (SNO above: hidden while snoozed until tomorrow, listed with
    # include_decided=true.)  An expired snooze stays listed: move
    # EXP's snooze_until back into the past.
    _update_decision(expired_snooze["id"], snooze_until=AS_OF)
    response = client.get("/api/excess", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == [
        LAP_ITEM,
        ACC_ITEM,
        UND_ITEM,
        EXP_ITEM,
    ], body
    # Ranks unchanged by the filtering: 1 (DIS) and 4 (SNO) are simply
    # gone; the listed rows keep the ranks excess_ranked gave them.
    assert [row["rank"] for row in body] == [2, 3, 5, 6]
    exp_row = {row["item_code"]: row for row in body}[EXP_ITEM]
    assert exp_row["decision"]["decision"] == "snoozed"
    assert exp_row["decision"]["snooze_until"] == AS_OF
    assert exp_row["decision_lapsed"] is False  # the threshold never snoozes

    # -- AC-2: the filters run before the leaving out. -------------------
    dead = client.get("/api/excess", params={"limit": 1000, "category": "dead"})
    assert dead.status_code == 200, dead.text
    assert [row["item_code"] for row in dead.json()] == [LAP_ITEM, EXP_ITEM]
    excess = client.get("/api/excess", params={"limit": 1000, "category": "excess"})
    assert excess.status_code == 200, excess.text
    assert [row["item_code"] for row in excess.json()] == [ACC_ITEM, UND_ITEM]
    never_moved = client.get(
        "/api/excess", params={"limit": 1000, "dead_reason": "never_moved"}
    )
    assert never_moved.status_code == 200, never_moved.text
    # SNO is never_moved too, but its snooze hides it by default.
    assert [row["item_code"] for row in never_moved.json()] == [
        LAP_ITEM,
        EXP_ITEM,
    ]

    # -- AC-1: one query per request, never one per row. -----------------
    current_all_calls = []
    original_current_all = excess_api._repo.current_all

    def counting_current_all():
        current_all_calls.append(1)
        return original_current_all()

    monkeypatch.setattr(excess_api._repo, "current_all", counting_current_all)
    response = client.get("/api/excess", params={"limit": 1000})
    assert response.status_code == 200, response.text
    assert len(response.json()) == 4
    assert len(current_all_calls) == 1, current_all_calls
    response = client.get(
        "/api/excess", params={"limit": 1000, "include_decided": "true"}
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 6
    assert len(current_all_calls) == 2, current_all_calls
    # limit/offset paging the enriched list does not re-query either.
    response = client.get("/api/excess", params={"limit": 2, "offset": 2})
    assert [row["item_code"] for row in response.json()] == [UND_ITEM, EXP_ITEM]
    assert len(current_all_calls) == 3, current_all_calls
    monkeypatch.undo()

    # -- AC-1: the enrichment is not limited to the first fetched page. --
    filler_ids = _seed_fillers()
    first_page = client.get("/api/excess/decisions", params={"limit": 100})
    assert first_page.status_code == 200
    page_ids = [row["id"] for row in first_page.json()]
    assert len(page_ids) == 100
    # The 104 fillers are newer than every real decision, so the first
    # page holds only fillers (id DESC breaks the decided_at tie).
    assert page_ids == sorted(filler_ids, reverse=True)[:100]
    assert lapsed_dismissal["id"] not in page_ids
    # LAP's latest decision sits beyond that first page, yet the excess
    # row still carries it.
    response = client.get("/api/excess", params={"limit": 1000})
    assert response.status_code == 200, response.text
    lap_row = {row["item_code"]: row for row in response.json()}[LAP_ITEM]
    assert lap_row["decision"] is not None
    assert lap_row["decision"]["id"] == lapsed_dismissal["id"]
    assert lap_row["decision"]["excess_units"] == 100.0
    assert lap_row["decision_lapsed"] is True

    # -- AC-1: the OData Excess set and $metadata are unchanged. ---------
    monkeypatch.setenv("AIERP_ODATA_USER", ODATA_USER)
    monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
    token = base64.b64encode(f"{ODATA_USER}:{ODATA_PASSWORD}".encode()).decode()
    odata_headers = {"Authorization": f"Basic {token}"}
    metadata = client.get("/odata/$metadata", headers=odata_headers)
    assert metadata.status_code == 200, metadata.text
    root = ElementTree.fromstring(metadata.text)
    schema = root.find(f"{{{EDM}}}Schema")
    assert schema is not None
    excess_type = next(
        element
        for element in schema.findall(f"{{{EDM}}}EntityType")
        if element.get("Name") == "Excess"
    )
    property_names = {
        prop.get("Name") for prop in excess_type.findall(f"{{{EDM}}}Property")
    }
    assert "decision" not in property_names, property_names
    assert "decision_lapsed" not in property_names, property_names
    # Sanity: it is still exactly the ExcessRow's shape.
    assert property_names == set(ExcessRow.model_fields)

    excess_set = client.get("/odata/Excess", headers=odata_headers)
    assert excess_set.status_code == 200, excess_set.text
    odata_rows = excess_set.json()["value"]
    assert {row["item_code"] for row in odata_rows} == set(ITEMS)
    for row in odata_rows:
        assert set(row.keys()) == set(ExcessRow.model_fields)
        assert "decision" not in row
        assert "decision_lapsed" not in row

    # -- AC-1: the CSV export is unchanged. -------------------------------
    export = client.get("/api/excess/export.csv", params={"as_of": AS_OF})
    assert export.status_code == 200, export.text
    assert export.headers["content-type"] == "text/csv; charset=utf-8"
    assert "excess-2026-06-30.csv" in export.headers["content-disposition"]
    assert export.text.startswith("\ufeff")
    csv_rows = list(
        csv.reader(io.StringIO(export.text.encode("utf-8").decode("utf-8-sig")))
    )
    # No decision columns, and no decision-based leaving out: every
    # listed row is exported, hidden ones included.
    assert csv_rows[0] == CSV_HEADERS
    assert len(csv_rows) - 1 == 6
    header_index = {name: position for position, name in enumerate(CSV_HEADERS)}
    assert [row[header_index["item_code"]] for row in csv_rows[1:]] == list(ITEMS)

    # -- include_decided is a plain boolean query parameter: hostile
    #    values answer 422, never 500.
    for hostile in ("", "xyz", "x" * 100, "\x00", "tru\x01e"):
        assert (
            client.get("/api/excess", params={"include_decided": hostile}).status_code
            == 422
        )

    # -- AC-1: the OpenAPI schema carries the new shape and parameter. ---
    spec = app.openapi()
    get_excess = spec["paths"]["/api/excess"]["get"]
    parameters = {param["name"]: param for param in get_excess["parameters"]}
    include_decided = parameters["include_decided"]
    assert include_decided["in"] == "query"
    assert include_decided["schema"]["type"] == "boolean"
    assert include_decided["schema"]["default"] is False
    assert include_decided.get("required") in (None, False)
    for name in ("limit", "offset", "category", "dead_reason", "as_of"):
        assert name in parameters, name
    list_schema = get_excess["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert list_schema["type"] == "array"
    assert list_schema["items"] == {"$ref": "#/components/schemas/ExcessDecisionRow"}
    decision_row = spec["components"]["schemas"]["ExcessDecisionRow"]
    properties = decision_row["properties"]
    assert properties["decision_lapsed"]["type"] == "boolean"
    decision_schema = properties["decision"]
    assert {"$ref": "#/components/schemas/ExcessDecision"} in decision_schema.get(
        "anyOf", []
    ), decision_schema
    assert {"type": "null"} in decision_schema.get("anyOf", []), decision_schema
    # ExcessDecisionRow is ExcessRow plus exactly the two new properties;
    # ExcessRow itself is unchanged (the OData assertions above prove the
    # $metadata side of it).
    assert set(properties) == set(ExcessRow.model_fields) | {
        "decision",
        "decision_lapsed",
    }
