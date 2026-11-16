"""Issue #208 proof: stockout rows carry their current stockout decision.

The proof covers, in one test:

* the new ``StockoutDecisionRow`` response shape: ``GET /api/stockout``
  answers with every ``StockoutRow`` field plus ``decision`` (the
  item-site's current ``stockout_decisions`` row — the latest by
  ``decided_at``, ``id`` breaking ties, exactly what
  ``GET /api/stockout/decisions`` reports — with every stored column,
  or null when the item-site has none) and ``decision_lapsed``
  (boolean); ``StockoutRow`` itself is unchanged, so the OData
  ``Stockout`` set, its ``$metadata`` and the CSV export are unchanged,
* the dismissal lapse rule: a dismissal lapses when the row's
  ``p_stockout`` exceeds the decision's ``p_stockout`` by at least
  0.10.  A dismissed row stays hidden by the default list while it is
  within the threshold, and is listed with ``decision_lapsed`` true
  once the row's stockout probability outgrows the stored one beyond
  it; a snooze never lapses,
* ``include_decided`` (boolean, default false): while false, a row
  whose current decision is a not-yet-lapsed dismissal, or a snooze
  whose ``snooze_until`` is after today (the server's UTC date), is
  left out of the list; accepted, lapsed, undecided and expired-snooze
  rows stay listed; while true, every row is listed.  Rows are left
  out after the ``category`` filter and before ``offset`` and ``limit``
  are applied, and every row keeps the ``rank`` ``stockout_ranked``
  gave it,
* the snooze anchor is the server's UTC date, never the local date:
  'tomorrow' is seeded from the UTC date, the expired-snooze case
  carries a ``snooze_until`` strictly between the row's as_of and the
  UTC date (an as_of-based expiration check would still hide it), and
  faking a local date one day ahead of the UTC date leaves the
  default list unchanged,
* the enrichment reads the current decisions with one query per
  request — never one per row — and is not limited to the first
  fetched page: a row whose latest decision sits beyond the first page
  of ``GET /api/stockout/decisions`` is still returned with its
  decision (not null),
* the OpenAPI schema carries the ``StockoutDecisionRow`` model (with
  the nullable ``StockoutDecision`` and the boolean
  ``decision_lapsed``) and the ``include_decided`` query parameter on
  ``GET /api/stockout``.

Six item-sites are seeded on fixed past dates: 2/day demand from
2025-07-06 through 2026-06-30 (the latest demand event, so the default
as-of is 2026-06-30) and pooled 8/10/12-day lead times (lt 10, sd_lt
2, steady demand so d 2, sd_d 0; mu 20, sigma 4, service level 0.95)
for every site, so all six are listed at risk — ranks 1..6 by
p_stockout (SNO 0.9987, UND 0.9772, ACC 0.8413, DIS 0.6915, LAP
0.3085, EXP 0.1587).  The decisions are recorded through
POST /api/stockout/decisions; the lapse case moves the stored
``p_stockout`` directly in the table, and the expired-snooze case
moves the stored ``snooze_until`` directly in the table, to a date
strictly between the row's as_of and the server's UTC date.
"""

import base64
import csv
import io
import xml.etree.ElementTree as ElementTree
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

import app.api.stockout as stockout_api
from app.api.stockout import StockoutRow
from app.db.connection import get_connection
from app.main import app

SITE_CODE = "SITE-208"
FIRST_DAY = date(2025, 7, 6)  # demand from here ...
DEMAND_DAYS = 360  # ... through 2026-06-30
AS_OF = "2026-06-30"  # the latest seeded demand event
AS_OF_STAMP = "2026-06-30 00:00:00+00"
ADMIN_SUB = "dev-admin-1"
EDM = "http://docs.oasis-open.org/odata/ns/edm"
ODATA_USER = "odata-machine-208"
ODATA_PASSWORD = "feed-secret-208"
FILLER_COUNT = 104  # pushes the real decisions past the first 100-row page

SNO_ITEM = "ITEM-208-SNO"  # likely, snoozed until tomorrow
UND_ITEM = "ITEM-208-UND"  # likely, never decided
ACC_ITEM = "ITEM-208-ACC"  # likely, accepted
DIS_ITEM = "ITEM-208-DIS"  # likely, dismissed, within the lapse threshold
LAP_ITEM = "ITEM-208-LAP"  # at_risk, dismissed, stored p_stockout lowered -> lapsed
EXP_ITEM = "ITEM-208-EXP"  # at_risk, snoozed, snooze_until moved to the past

# item_code -> (on_hand, unit_cost).  With d 2 and lt 10 (mu 20, sigma
# 4) the position's z-score is (on_hand - 20) / 4, so the sites rank
# SNO=1, UND=2, ACC=3, DIS=4, LAP=5, EXP=6 by p_stockout.
ITEMS = {
    SNO_ITEM: (8.0, 1.0),
    UND_ITEM: (12.0, 2.0),
    ACC_ITEM: (16.0, 5.0),
    DIS_ITEM: (18.0, 3.0),
    LAP_ITEM: (22.0, 4.0),
    EXP_ITEM: (24.0, 2.0),
}

P_STOCKOUT = {
    SNO_ITEM: 0.9987,
    UND_ITEM: 0.9772,
    ACC_ITEM: 0.8413,
    DIS_ITEM: 0.6915,
    LAP_ITEM: 0.3085,
    EXP_ITEM: 0.1587,
}

CATEGORY = {
    SNO_ITEM: "likely",
    UND_ITEM: "likely",
    ACC_ITEM: "likely",
    DIS_ITEM: "likely",
    LAP_ITEM: "at_risk",
    EXP_ITEM: "at_risk",
}

EXPECTED_UNITS_SHORT = {
    SNO_ITEM: 12.0,
    UND_ITEM: 8.03,
    ACC_ITEM: 4.33,
    DIS_ITEM: 2.79,
    LAP_ITEM: 0.79,
    EXP_ITEM: 0.33,
}

MONEY_AT_RISK = {
    SNO_ITEM: 12.0,
    UND_ITEM: 16.06,
    ACC_ITEM: 21.65,
    DIS_ITEM: 8.37,
    LAP_ITEM: 3.16,
    EXP_ITEM: 0.66,
}

EXPECTED_RANK = {
    SNO_ITEM: 1,
    UND_ITEM: 2,
    ACC_ITEM: 3,
    DIS_ITEM: 4,
    LAP_ITEM: 5,
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
    "p_stockout",
    "on_hand_units",
    "open_supply_units",
    "expected_units_short",
    "money_at_risk",
    "decided_by",
    "decided_by_name",
    "decided_at",
}

ROW_COLUMNS = set(StockoutRow.model_fields) | {"decision", "decision_lapsed"}

CSV_HEADERS = [
    "rank",
    "category",
    "item_code",
    "site_code",
    "p_stockout",
    "projected_stockout_on",
    "days_of_cover",
    "on_hand_units",
    "open_supply_units",
    "open_supply_due_by",
    "lead_time_days",
    "lead_time_demand",
    "recommended_reorder_point_units",
    "expected_units_short",
    "unit_cost",
    "money_at_risk",
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
            lead_time_rows = []
            item_site_rows = []
            on_hand_rows = []
            for item_code, (on_hand, unit_cost) in ITEMS.items():
                suffix = item_code.split("-")[2].lower()
                demand_rows.extend(
                    (
                        f"id-de-208-{suffix}-{day_index}",
                        f"de-208-{suffix}-{day_index}",
                        item_code,
                        SITE_CODE,
                        2.0,
                        (FIRST_DAY + timedelta(days=day_index)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for day_index in range(DEMAND_DAYS)
                )
                # 8, 10, 12 days -> mean 10, sample std 2.
                lead_time_rows.extend(
                    (
                        f"id-lt-208-{suffix}-{obs_index}",
                        f"lt-208-{suffix}-{obs_index}",
                        f"SUP-208-{suffix}",
                        item_code,
                        ordered_on,
                        received_on,
                        "test",
                    )
                    for obs_index, (ordered_on, received_on) in enumerate(
                        (
                            ("2026-05-01", "2026-05-09"),
                            ("2026-05-05", "2026-05-15"),
                            ("2026-05-08", "2026-05-20"),
                        ),
                        start=1,
                    )
                )
                item_site_rows.append(
                    (
                        f"id-is-208-{suffix}",
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
                        f"id-oh-208-{suffix}",
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
    response = client.post("/api/stockout/decisions", json=body)
    assert response.status_code == 201, (item_code, response.status_code, response.text)
    row = response.json()
    assert set(row.keys()) == DECISION_COLUMNS, row
    assert row["item_code"] == item_code
    assert row["site_code"] == SITE_CODE
    assert row["decision"] == decision
    assert row["as_of"] == AS_OF
    on_hand, _unit_cost = ITEMS[item_code]
    assert row["on_hand_units"] == on_hand
    assert row["open_supply_units"] == 0.0
    assert row["p_stockout"] == P_STOCKOUT[item_code]
    assert row["expected_units_short"] == EXPECTED_UNITS_SHORT[item_code]
    assert row["money_at_risk"] == MONEY_AT_RISK[item_code]
    assert row["category"] == CATEGORY[item_code]
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
                f"UPDATE stockout_decisions SET {assignments} WHERE id = %s",
                (*changes.values(), decision_id),
            )
            assert cur.rowcount == 1, f"no decision {decision_id} to move"
        conn.commit()
    finally:
        conn.close()


def _seed_fillers() -> list[str]:
    """FILLER_COUNT decisions, newer than every real one, so the real
    decisions sit beyond the first page (100 rows) of
    ``GET /api/stockout/decisions``.  The filler item-sites have no
    demand, lead time or on-hand rows, so they are never listed by
    GET /api/stockout."""
    decided_at = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    ids = [f"id-208-fill-{index:03d}" for index in range(FILLER_COUNT)]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO stockout_decisions "
                "(id, item_code, site_code, decision, as_of, category, "
                "p_stockout, on_hand_units, open_supply_units, "
                "expected_units_short, money_at_risk, decided_by, "
                "decided_at) "
                "VALUES (%s, %s, %s, 'dismissed', %s, 'likely', 0.9, 1, 0, "
                "1, 1, 'filler', %s)",
                [
                    (
                        decision_id,
                        f"ITEM-208-F{index:03d}",
                        "SITE-208F",
                        AS_OF,
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
    """One StockoutDecisionRow: every StockoutRow field, the decision
    and the flag."""
    assert set(row.keys()) == ROW_COLUMNS, row
    assert row["as_of"] == AS_OF
    assert row["site_code"] == SITE_CODE
    assert row["p_stockout"] == P_STOCKOUT[row["item_code"]]
    assert row["category"] == CATEGORY[row["item_code"]]
    assert row["expected_units_short"] == EXPECTED_UNITS_SHORT[row["item_code"]]
    assert row["money_at_risk"] == MONEY_AT_RISK[row["item_code"]]
    assert row["rank"] == EXPECTED_RANK[row["item_code"]]


def _decision_by_code(body: list[dict]) -> dict[str, dict | None]:
    return {row["item_code"]: row["decision"] for row in body}


def test_issue208_freeform(client, admin_token, monkeypatch):
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    # Review: the snooze anchor is the server's UTC date, never the
    # local date: 'tomorrow' is seeded from the UTC date, and the
    # expired-snooze case below is seeded with a date strictly between
    # the row's as_of (2026-06-30) and the UTC date, so neither a
    # local-date- nor an as_of-based expiration check can satisfy the
    # proof.
    utc_today = datetime.now(UTC).date()
    tomorrow = (utc_today + timedelta(days=1)).isoformat()
    expired_on = utc_today - timedelta(days=1)
    assert (
        date.fromisoformat(AS_OF) < expired_on < utc_today
    ), f"the proof needs the server date at least one day after {AS_OF}, got {utc_today}"

    _seed()

    # -- AC-3: undecided rows are listed with decision null. -------------
    response = client.get("/api/stockout", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == list(ITEMS), body
    assert [row["rank"] for row in body] == [EXPECTED_RANK[code] for code in ITEMS]
    for row in body:
        _assert_row(row)
        assert row["decision"] is None
        assert row["decision_lapsed"] is False

    # -- Decisions recorded through POST /api/stockout/decisions. ----------
    accepted = _post_decision(admin_client, ACC_ITEM, "accepted", action="expedite")
    dismissed = _post_decision(
        admin_client, DIS_ITEM, "dismissed", reason="coverage is fine"
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
    assert dismissed["p_stockout"] == P_STOCKOUT[DIS_ITEM]
    assert lapsed_dismissal["p_stockout"] == P_STOCKOUT[LAP_ITEM]

    # -- AC-3: not-yet-lapsed dismissals and snoozes are left out. ------
    # DIS's stored p_stockout (0.6915) still matches the row's 0.6915
    # and LAP's stored 0.3085 still matches the row's 0.3085: the
    # default list drops SNO and EXP (snoozed until tomorrow) and DIS
    # and LAP (not-yet-lapsed dismissals); the accepted row ACC and the
    # undecided row UND stay.
    response = client.get("/api/stockout", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == [UND_ITEM, ACC_ITEM], body
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
    # lands on the visible rank-3 row ACC, not the hidden rank-4 row DIS.
    response = client.get("/api/stockout", params={"limit": 1, "offset": 1})
    assert response.status_code == 200, response.text
    assert [row["item_code"] for row in response.json()] == [ACC_ITEM]
    assert response.json()[0]["rank"] == 3

    # include_decided=true lists every row, hidden ones included, with
    # the ranks stockout_ranked gave them (unchanged by the filtering).
    response = client.get(
        "/api/stockout",
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
    assert decisions[ACC_ITEM]["action"] == "expedite"
    assert decisions[DIS_ITEM]["decision"] == "dismissed"
    assert decisions[LAP_ITEM]["decision"] == "dismissed"
    assert decisions[SNO_ITEM]["decision"] == "snoozed"
    assert decisions[SNO_ITEM]["snooze_until"] == tomorrow
    assert decisions[EXP_ITEM]["decision"] == "snoozed"
    assert decisions[EXP_ITEM]["snooze_until"] == tomorrow

    # -- AC-3: moving the dismissal beyond the threshold lapses it. -----
    # Lower the stored p_stockout directly in the table: the row's
    # 0.3085 now exceeds the decision's 0.10 by 0.2085, at least
    # 0.10: lapsed, listed with the flag.
    _update_decision(lapsed_dismissal["id"], p_stockout=0.10)
    response = client.get("/api/stockout", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == [
        UND_ITEM,
        ACC_ITEM,
        LAP_ITEM,
    ], body
    assert [row["rank"] for row in body] == [2, 3, 5]
    lap_row = {row["item_code"]: row for row in body}[LAP_ITEM]
    assert lap_row["decision_lapsed"] is True
    assert lap_row["decision"]["decision"] == "dismissed"
    assert lap_row["decision"]["id"] == lapsed_dismissal["id"]
    assert lap_row["decision"]["p_stockout"] == 0.10
    assert lap_row["p_stockout"] == 0.3085
    # DIS is still within its threshold: still hidden by default.
    assert DIS_ITEM not in {row["item_code"] for row in body}
    # include_decided=true now also lists DIS (dismissed, not lapsed)
    # and SNO (snoozed until tomorrow).
    response = client.get(
        "/api/stockout",
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
    # EXP's snooze_until back into the past — strictly after the
    # row's as_of (2026-06-30) and strictly before the server's UTC
    # date, so an expiration check that compared against the row's
    # as_of (or the request's as_of) instead of the server date would
    # still hide EXP; its being listed proves the server date is the
    # anchor.
    _update_decision(expired_snooze["id"], snooze_until=expired_on.isoformat())
    response = client.get("/api/stockout", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == [
        UND_ITEM,
        ACC_ITEM,
        LAP_ITEM,
        EXP_ITEM,
    ], body
    # Ranks unchanged by the filtering: 1 (SNO) and 4 (DIS) are simply
    # gone; the listed rows keep the ranks stockout_ranked gave them.
    assert [row["rank"] for row in body] == [2, 3, 5, 6]
    exp_row = {row["item_code"]: row for row in body}[EXP_ITEM]
    assert exp_row["decision"]["decision"] == "snoozed"
    assert exp_row["decision"]["snooze_until"] == expired_on.isoformat()
    assert exp_row["decision_lapsed"] is False  # the threshold never snoozes

    # -- Review: the anchor is the server's UTC date, never the local
    #    date.  Fake a local date one day ahead of the UTC date (a time
    #    zone east of UTC, after its midnight rollover): a local-date
    #    check would treat the UTC-tomorrow snooze as due today and
    #    list SNO; the UTC-anchored check leaves the default list
    #    exactly unchanged.
    real_datetime = datetime

    class _FakeLocalDate(date):
        @classmethod
        def today(cls):
            return utc_today + timedelta(days=1)

    class _FakeLocalDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                fake = utc_today + timedelta(days=1)
                return real_datetime(fake.year, fake.month, fake.day, 12)
            return super().now(tz)

    monkeypatch.setattr(stockout_api, "date", _FakeLocalDate)
    monkeypatch.setattr(stockout_api, "datetime", _FakeLocalDatetime)
    response = client.get("/api/stockout", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == [
        UND_ITEM,
        ACC_ITEM,
        LAP_ITEM,
        EXP_ITEM,
    ], body
    assert SNO_ITEM not in {row["item_code"] for row in body}
    assert [row["rank"] for row in body] == [2, 3, 5, 6]
    monkeypatch.undo()

    # -- AC-2: the filter runs before the leaving out. -------------------
    likely = client.get("/api/stockout", params={"limit": 1000, "category": "likely"})
    assert likely.status_code == 200, likely.text
    # SNO and DIS are likely too, but their decisions hide them by
    # default.
    assert [row["item_code"] for row in likely.json()] == [UND_ITEM, ACC_ITEM]
    at_risk = client.get("/api/stockout", params={"limit": 1000, "category": "at_risk"})
    assert at_risk.status_code == 200, at_risk.text
    assert [row["item_code"] for row in at_risk.json()] == [LAP_ITEM, EXP_ITEM]
    at_risk_all = client.get(
        "/api/stockout",
        params={"limit": 1000, "category": "at_risk", "include_decided": "true"},
    )
    assert [row["item_code"] for row in at_risk_all.json()] == [LAP_ITEM, EXP_ITEM]

    # -- AC-1: one query per request, never one per row. -----------------
    current_all_calls = []
    original_current_all = stockout_api._repo.current_all

    def counting_current_all():
        current_all_calls.append(1)
        return original_current_all()

    monkeypatch.setattr(stockout_api._repo, "current_all", counting_current_all)
    response = client.get("/api/stockout", params={"limit": 1000})
    assert response.status_code == 200, response.text
    assert len(response.json()) == 4
    assert len(current_all_calls) == 1, current_all_calls
    response = client.get(
        "/api/stockout", params={"limit": 1000, "include_decided": "true"}
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 6
    assert len(current_all_calls) == 2, current_all_calls
    # limit/offset paging the enriched list does not re-query either.
    response = client.get("/api/stockout", params={"limit": 2, "offset": 2})
    assert [row["item_code"] for row in response.json()] == [LAP_ITEM, EXP_ITEM]
    assert len(current_all_calls) == 3, current_all_calls
    monkeypatch.undo()

    # -- AC-1: the enrichment is not limited to the first fetched page. --
    filler_ids = _seed_fillers()
    first_page = client.get("/api/stockout/decisions", params={"limit": 100})
    assert first_page.status_code == 200
    page_ids = [row["id"] for row in first_page.json()]
    assert len(page_ids) == 100
    # The 104 fillers are newer than every real decision, so the first
    # page holds only fillers (id DESC breaks the decided_at tie).
    assert page_ids == sorted(filler_ids, reverse=True)[:100]
    assert lapsed_dismissal["id"] not in page_ids
    # LAP's latest decision sits beyond that first page, yet the
    # stockout row still carries it.
    response = client.get("/api/stockout", params={"limit": 1000})
    assert response.status_code == 200, response.text
    lap_row = {row["item_code"]: row for row in response.json()}[LAP_ITEM]
    assert lap_row["decision"] is not None
    assert lap_row["decision"]["id"] == lapsed_dismissal["id"]
    assert lap_row["decision"]["p_stockout"] == 0.10
    assert lap_row["decision_lapsed"] is True

    # -- Review fix: an exact 0.10 difference lapses the dismissal. ----
    # Lower DIS's stored p_stockout to exactly 0.10 below the row's
    # 0.6915: the naive float difference lands just under the
    # threshold, so the lapse check must compare the decimals exactly,
    # and the boundary lapses.
    _update_decision(dismissed["id"], p_stockout=0.5915)
    assert 0.6915 - 0.5915 < 0.10  # the float difference that would miss it
    response = client.get("/api/stockout", params={"limit": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == [
        UND_ITEM,
        ACC_ITEM,
        DIS_ITEM,
        LAP_ITEM,
        EXP_ITEM,
    ], body
    assert [row["rank"] for row in body] == [2, 3, 4, 5, 6]
    dis_row = {row["item_code"]: row for row in body}[DIS_ITEM]
    assert dis_row["decision_lapsed"] is True
    assert dis_row["decision"]["id"] == dismissed["id"]
    assert dis_row["decision"]["p_stockout"] == 0.5915
    assert dis_row["p_stockout"] == 0.6915

    # -- AC-1: the OData Stockout set and $metadata are unchanged. ------
    monkeypatch.setenv("AIERP_ODATA_USER", ODATA_USER)
    monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
    token = base64.b64encode(f"{ODATA_USER}:{ODATA_PASSWORD}".encode()).decode()
    odata_headers = {"Authorization": f"Basic {token}"}
    metadata = client.get("/odata/$metadata", headers=odata_headers)
    assert metadata.status_code == 200, metadata.text
    root = ElementTree.fromstring(metadata.text)
    schema = root.find(f"{{{EDM}}}Schema")
    assert schema is not None
    stockout_type = next(
        element
        for element in schema.findall(f"{{{EDM}}}EntityType")
        if element.get("Name") == "Stockout"
    )
    property_names = {
        prop.get("Name") for prop in stockout_type.findall(f"{{{EDM}}}Property")
    }
    assert "decision" not in property_names, property_names
    assert "decision_lapsed" not in property_names, property_names
    # Sanity: it is still exactly the StockoutRow's shape.
    assert property_names == set(StockoutRow.model_fields)

    stockout_set = client.get("/odata/Stockout", headers=odata_headers)
    assert stockout_set.status_code == 200, stockout_set.text
    odata_rows = stockout_set.json()["value"]
    assert {row["item_code"] for row in odata_rows} == set(ITEMS)
    for row in odata_rows:
        assert set(row.keys()) == set(StockoutRow.model_fields)
        assert "decision" not in row
        assert "decision_lapsed" not in row

    # -- AC-1: the CSV export is unchanged. -------------------------------
    export = client.get("/api/stockout/export.csv", params={"as_of": AS_OF})
    assert export.status_code == 200, export.text
    assert export.headers["content-type"] == "text/csv; charset=utf-8"
    assert "stockout-2026-06-30.csv" in export.headers["content-disposition"]
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
    assert [row[header_index["p_stockout"]] for row in csv_rows[1:]] == [
        f"{P_STOCKOUT[code]:.4f}" for code in ITEMS
    ]

    # -- include_decided is a plain boolean query parameter: hostile
    #    values answer 422, never 500.
    for hostile in ("", "xyz", "x" * 100, "\x00", "tru\x01e"):
        assert (
            client.get("/api/stockout", params={"include_decided": hostile}).status_code
            == 422
        )

    # -- AC-1: the OpenAPI schema carries the new shape and parameter. ---
    spec = app.openapi()
    get_stockout = spec["paths"]["/api/stockout"]["get"]
    parameters = {param["name"]: param for param in get_stockout["parameters"]}
    include_decided = parameters["include_decided"]
    assert include_decided["in"] == "query"
    assert include_decided["schema"]["type"] == "boolean"
    assert include_decided["schema"]["default"] is False
    assert include_decided.get("required") in (None, False)
    for name in ("limit", "offset", "category", "as_of"):
        assert name in parameters, name
    list_schema = get_stockout["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert list_schema["type"] == "array"
    assert list_schema["items"] == {"$ref": "#/components/schemas/StockoutDecisionRow"}
    decision_row = spec["components"]["schemas"]["StockoutDecisionRow"]
    properties = decision_row["properties"]
    assert properties["decision_lapsed"]["type"] == "boolean"
    decision_schema = properties["decision"]
    assert {"$ref": "#/components/schemas/StockoutDecision"} in decision_schema.get(
        "anyOf", []
    ), decision_schema
    assert {"type": "null"} in decision_schema.get("anyOf", []), decision_schema
    # StockoutDecisionRow is StockoutRow plus exactly the two new
    # properties; StockoutRow itself is unchanged (the OData
    # assertions above prove the $metadata side of it).
    assert set(properties) == set(StockoutRow.model_fields) | {
        "decision",
        "decision_lapsed",
        "remedy",
    }
