"""Issue #1588 proof: the excess stops of the parameter writeback
(AIERP-D-231).

The shared preview-row construction of
``GET /api/parameter-writeback/preview`` and
``POST /api/parameter-writeback/confirm`` gains a third source, read
once per call: the excess stops — the item-sites whose current
excess decision (the latest excess decision per pair, read through
``ExcessDecisionPostgresRepository().current_all()``) is "accepted"
with the action "stop_replenishing" and whose pair the current
excess ranking lists (``app.recommend.excess.excess_ranked``,
imported by name into the route module, read once per call only
when such a decision exists).  A stop row carries ``basis``
"excess" with the proposed reorder point and max quantity both 0,
the ``decision_id`` and ``decided_at`` of the excess decision and
the listed row's ``money_tied_up`` as ``money_at_stake``, and
appears only when the pair's ``item_sites`` row exists and its
``reorder_point`` or ``max_qty`` is above 0 — an item-site whose
values are 0 or null has nothing to stop.  A listed stop pair is
excess-managed: it loses its decision row and its buffer row,
whether or not its excess row is outstanding, and the excess rows
follow the decision rows and the buffer rows, ordered by item_code
then site_code.  The confirm writes an excess row through the
shared code, as for every row: the writer gets 0 and 0, the audit
row stores ``basis`` "excess" and the excess decision's id as
``decision_id``, and ``item_sites`` takes the after values.  The
record gains the ``excess`` kind, counted by the same rules as the
other kinds.

The data is seeded the way test 762 seeds its: ``item_sites`` and
``planner_decisions`` directly in the test database, the buffer
designated by an admin ``PUT /api/ddmrp/buffers`` with the demand
events and lead-time observations test 762 seeds, the two client
classes in the route module replaced with fakes the way test 719
replaces them, the excess decisions inserted straight into
``excess_decisions`` with every NOT NULL column filled and every
``decided_at`` given explicitly, and
``app.api.parameter_writeback.excess_ranked`` replaced with a
stand-in that answers the listed rows and counts its calls.

Proved here:

  * the stop — a listed accepted stop with reorder point 25 and
    max 60 is listed once, with ``basis`` "excess", proposed 0 and
    0, and the decision's id and time and the listed money;
  * the precedence — a listed stop that also has an outstanding
    accepted decision and an outstanding "ok" buffer is listed
    once, as "excess"; at 0 and 0 a listed stop is not listed, and
    neither are its other rows;
  * not a stop — an accepted "reduce" keeps its decision row, and
    so does a stop the stand-in does not list; a stop followed by
    a later dismissal is not listed;
  * no stops — with no stop at all, the stand-in is never called;
  * the confirm — confirming the listed stop calls the writer with
    0 and 0, stores an audit row with ``basis`` "excess" and the
    excess decision's id, and sets ``item_sites`` to 0 and 0, and
    the preview then no longer lists the pair;
  * the record — once ``item_sites.synced_at`` is later than the
    push and the values are 0 and 0, the record's ``excess`` reads
    pushed 1, read 1, unchanged 1 and rate 1.0, and the other two
    kinds answer as they did before; and a null ``synced_at`` (no
    sync ever recorded) is never read back — the record still
    answers, the push counting as pushed, never as read.
"""

from datetime import UTC, date, datetime, timedelta

import psycopg2.extras
from fastapi.testclient import TestClient

import app.api.parameter_writeback as parameter_writeback
import app.recommend.push_record as push_record_module
from app.db.connection import get_connection
from app.ddmrp.zones import buffer_zones
from app.main import app

PREVIEW_URL = "/api/parameter-writeback/preview"
CONFIRM_URL = "/api/parameter-writeback/confirm"
RECORD_URL = "/api/parameter-writeback/record"
ZONES_URL = "/api/ddmrp/zones"
BUFFERS_URL = "/api/ddmrp/buffers"

SITE = "SITE-1588"
ADMIN_TOKEN = "dev-secret-token:admin"
AS_OF = date(2026, 10, 1)

# item_code -> (item_sites id, reorder_point, max_qty, order_qty,
# source_system).  A is the listed stop, confirmed to 0 and 0; B is
# the listed stop that also carries an outstanding accepted
# decision and an outstanding "ok" buffer; C is the listed stop
# already at 0 and 0 (nothing to stop); D is the accepted "reduce"
# (not a stop, listed or not); E is the stop the stand-in does not
# list; F is the stop followed by a later dismissal.
ITEMS = {
    "ITEM-1588-A": ("is-1588-a", 25.0, 60.0, 20.0, "acumatica"),
    "ITEM-1588-B": ("is-1588-b", 25.0, 60.0, 20.0, "acumatica"),
    "ITEM-1588-C": ("is-1588-c", 0.0, 0.0, 0.0, "acumatica"),
    "ITEM-1588-D": ("is-1588-d", 80.0, 120.0, 20.0, "acumatica"),
    "ITEM-1588-E": ("is-1588-e", 30.0, 60.0, 20.0, "acumatica"),
    "ITEM-1588-F": ("is-1588-f", 45.0, 90.0, 20.0, "acumatica"),
}

# (decision_id, item_code, recommended units, money, decided_at) —
# every decision is "accepted" and outstanding: the ERP reorder
# point differs from the recommendation by more than 1.
PLANNER_DECISIONS = [
    # B: loses to its buffer, and then to its excess stop.
    ("pd-1588-b", "ITEM-1588-B", 55, 4000.0, "2026-10-01T09:00:00+00"),
    # C: outstanding at 0 and 10 — loses to its excess stop, which
    # has no row of its own at 0 and 0.
    ("pd-1588-c", "ITEM-1588-C", 10, 500.0, "2026-10-01T09:30:00+00"),
    # D, E and F keep their decision rows.
    ("pd-1588-d", "ITEM-1588-D", 100, 3000.0, "2026-10-01T10:00:00+00"),
    ("pd-1588-e", "ITEM-1588-E", 40, 2000.0, "2026-10-01T10:30:00+00"),
    ("pd-1588-f", "ITEM-1588-F", 60, 1500.0, "2026-10-01T11:00:00+00"),
]

# (id, item_code, decision, action, reason, as_of, category,
# dead_reason, on_hand_units, excess_units, money_tied_up,
# decided_by, decided_by_name, decided_at) — the excess decisions,
# inserted straight into excess_decisions.
EXCESS_DECISIONS = [
    (
        "ed-1588-a",
        "ITEM-1588-A",
        "accepted",
        "stop_replenishing",
        None,
        AS_OF,
        "excess",
        None,
        100.0,
        60.0,
        5000.0,
        "seeder",
        None,
        "2026-10-02T09:30:00+00",
    ),
    (
        "ed-1588-b",
        "ITEM-1588-B",
        "accepted",
        "stop_replenishing",
        None,
        AS_OF,
        "excess",
        None,
        120.0,
        70.0,
        4200.0,
        "seeder",
        None,
        "2026-10-02T10:00:00+00",
    ),
    (
        "ed-1588-c",
        "ITEM-1588-C",
        "accepted",
        "stop_replenishing",
        None,
        AS_OF,
        "dead",
        "stopped",
        40.0,
        40.0,
        3100.0,
        "seeder",
        None,
        "2026-10-02T10:30:00+00",
    ),
    (
        "ed-1588-d",
        "ITEM-1588-D",
        "accepted",
        "reduce",
        None,
        AS_OF,
        "excess",
        None,
        90.0,
        30.0,
        2600.0,
        "seeder",
        None,
        "2026-10-02T11:00:00+00",
    ),
    (
        "ed-1588-e",
        "ITEM-1588-E",
        "accepted",
        "stop_replenishing",
        None,
        AS_OF,
        "excess",
        None,
        80.0,
        25.0,
        2100.0,
        "seeder",
        None,
        "2026-10-02T11:30:00+00",
    ),
    (
        "ed-1588-f-stop",
        "ITEM-1588-F",
        "accepted",
        "stop_replenishing",
        None,
        AS_OF,
        "excess",
        None,
        60.0,
        20.0,
        1500.0,
        "seeder",
        None,
        "2026-10-01T08:00:00+00",
    ),
    (
        "ed-1588-f-dismiss",
        "ITEM-1588-F",
        "dismissed",
        None,
        "seasonal stock",
        AS_OF,
        "excess",
        None,
        60.0,
        20.0,
        1500.0,
        "seeder",
        None,
        "2026-10-02T08:00:00+00",
    ),
]

# item_code -> the money_tied_up of the pair's row in the stand-in's
# excess list.  E is the stop the stand-in does not list.
LISTED = {
    "ITEM-1588-A": 5000.0,
    "ITEM-1588-B": 4200.0,
    "ITEM-1588-C": 3100.0,
    "ITEM-1588-D": 2600.0,
    "ITEM-1588-F": 1500.0,
}


class FakeAcumaticaClient:
    """The fake for the route module's AcumaticaClient: records every
    call and answers "written" with the seeded item_sites values as
    the ERP's before values and the requested values as the
    read-back (the way test 719's fake does)."""

    calls: list[dict] = []

    def write_item_warehouse_parameters(
        self, *, item_code: str, site_code: str, reorder_point: float, max_qty: float
    ) -> dict:
        FakeAcumaticaClient.calls.append(
            {
                "item_code": item_code,
                "site_code": site_code,
                "reorder_point": reorder_point,
                "max_qty": max_qty,
            }
        )
        reorder, max_value, _ = ITEMS[item_code][1:4]
        return {
            "outcome": "written",
            "before": {"reorder_point": reorder, "max_qty": max_value},
            "after": {"reorder_point": reorder_point, "max_qty": max_qty},
        }


class FakeOdooClient:
    """The fake for the route module's OdooClient: records every call
    (the proof confirms no Odoo pair, so it must stay empty)."""

    calls: list[dict] = []

    def write_reorder_rule(
        self, *, item_code: str, site_code: str, reorder_point: float, max_qty: float
    ) -> dict:
        FakeOdooClient.calls.append(
            {
                "item_code": item_code,
                "site_code": site_code,
                "reorder_point": reorder_point,
                "max_qty": max_qty,
            }
        )
        reorder, max_value, _ = ITEMS[item_code][1:4]
        return {
            "outcome": "written",
            "before": {"reorder_point": reorder, "max_qty": max_value},
            "after": {"reorder_point": reorder_point, "max_qty": max_qty},
        }


class ExcessRankedStandIn:
    """The stand-in for the route module's ``excess_ranked``:
    answers the listed rows and counts its calls."""

    calls = 0

    def __call__(self):
        ExcessRankedStandIn.calls += 1
        rows = []
        for rank, item_code in enumerate(sorted(LISTED), start=1):
            rows.append(
                {
                    "item_code": item_code,
                    "site_code": SITE,
                    "category": "excess",
                    "on_hand_units": 100.0,
                    "excess_units": 10.0,
                    "money_tied_up": LISTED[item_code],
                    "as_of": AS_OF,
                    "rank": rank,
                }
            )
        return rows


def _seed_item_sites_and_planner_decisions() -> None:
    """Seed item_sites and planner_decisions directly (the way test
    762 seeds them)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item_code, (
                site_id,
                reorder,
                max_qty,
                order_qty,
                source,
            ) in ITEMS.items():
                cur.execute(
                    "INSERT INTO item_sites "
                    "(id, item_code, site_code, unit_cost, reorder_point, "
                    " max_qty, order_qty, source_system) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        site_id,
                        item_code,
                        SITE,
                        1.0,
                        reorder,
                        max_qty,
                        order_qty,
                        source,
                    ),
                )
            for decision_id, item_code, units, money, decided_at in PLANNER_DECISIONS:
                cur.execute(
                    "INSERT INTO planner_decisions "
                    "(id, item_code, site_code, decision, reason, snooze_until, "
                    " as_of, recommended_reorder_point_units, current_reorder_point, "
                    " money_at_stake, decided_by, decided_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        decision_id,
                        item_code,
                        SITE,
                        "accepted",
                        None,
                        None,
                        AS_OF,
                        units,
                        ITEMS[item_code][1],
                        money,
                        "seeder",
                        decided_at,
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _seed_excess_decisions() -> None:
    """Insert the excess decisions straight into excess_decisions,
    every NOT NULL column filled and every decided_at given
    explicitly."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for (
                decision_id,
                item_code,
                decision,
                action,
                reason,
                as_of,
                category,
                dead_reason,
                on_hand_units,
                excess_units,
                money_tied_up,
                decided_by,
                decided_by_name,
                decided_at,
            ) in EXCESS_DECISIONS:
                cur.execute(
                    "INSERT INTO excess_decisions "
                    "(id, item_code, site_code, decision, action, reason, "
                    " as_of, category, dead_reason, on_hand_units, "
                    " excess_units, money_tied_up, decided_by, "
                    " decided_by_name, decided_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s)",
                    (
                        decision_id,
                        item_code,
                        SITE,
                        decision,
                        action,
                        reason,
                        as_of,
                        category,
                        dead_reason,
                        on_hand_units,
                        excess_units,
                        money_tied_up,
                        decided_by,
                        decided_by_name,
                        decided_at,
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _demand_events(cur, item: str, site: str, daily: dict[date, float]) -> None:
    """One demand event per day of ``daily`` (the way test 762 seeds
    its demand events)."""
    cur.executemany(
        "INSERT INTO demand_events (id, business_key, item_code, site_code, "
        "quantity, occurred_on, event_type, source_system) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                f"id-de-1588-{item}-{day.isoformat()}",
                f"de-1588-{item}-{day.isoformat()}",
                item,
                site,
                quantity,
                day.isoformat(),
                "shipment",
                "test",
            )
            for day, quantity in sorted(daily.items(), reverse=True)
        ],
    )


def _lead_times(cur, item: str, received: date) -> None:
    """Three lead-time observations of 8, 10 and 12 days: pooled mean
    10 (the way test 762 seeds its lead-time observations)."""
    for i, lead in enumerate((8, 10, 12)):
        cur.execute(
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, 'test')",
            (
                f"id-lt-1588-{item}-{i}",
                f"lt-1588-{item}-{i}",
                f"SUP-1588-{item}",
                item,
                (received - timedelta(days=lead)).isoformat(),
                received.isoformat(),
            ),
        )


def _item_site(item_code: str) -> tuple:
    """The item-site's (reorder_point, max_qty, order_qty)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reorder_point, max_qty, order_qty FROM item_sites "
                "WHERE item_code = %s AND site_code = %s",
                (item_code, SITE),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    return row


def _audit_rows() -> list[dict]:
    """The raw parameter_writebacks rows, oldest first."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM parameter_writebacks ORDER BY written_at, id")
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _set_synced_at(item_code: str, synced_at: datetime) -> None:
    """Set the item-site's synced_at explicitly."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE item_sites SET synced_at = %s "
                "WHERE item_code = %s AND site_code = %s",
                (synced_at, item_code, SITE),
            )
        conn.commit()
    finally:
        conn.close()


def _null_synced_sites(pairs: set[tuple[str, str]]) -> dict:
    """The stand-in for the record's item-site read
    (``app.recommend.push_record._item_sites``): every pair the
    record asks for, with the confirmed 0 and 0 values and
    ``synced_at`` null — the item-site no sync ever recorded."""
    return {
        pair: {
            "item_code": pair[0],
            "site_code": pair[1],
            "synced_at": None,
            "reorder_point": 0.0,
            "max_qty": 0.0,
        }
        for pair in pairs
    }


def test_issue1588_freeform(client, monkeypatch):
    # The two client classes in the route module are replaced with
    # fakes that record their calls, and the route module's
    # ``excess_ranked`` is replaced with a stand-in that answers the
    # listed rows and counts its calls (the way test 762 replaces
    # its clients).
    FakeAcumaticaClient.calls = []
    FakeOdooClient.calls = []
    ExcessRankedStandIn.calls = 0
    monkeypatch.setattr(parameter_writeback, "AcumaticaClient", FakeAcumaticaClient)
    monkeypatch.setattr(parameter_writeback, "OdooClient", FakeOdooClient)
    monkeypatch.setattr(parameter_writeback, "excess_ranked", ExcessRankedStandIn())
    # The recommendation inputs: the registry defaults, never ambient
    # environment values (the way test 740 pins them).
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # --- The seed: item-sites and decisions (test 762's way). ------
    _seed_item_sites_and_planner_decisions()

    # B's buffer inputs: the demand events and the lead-time
    # observations (test 762's way), then the designation (an admin
    # PUT) and the known zone.
    as_of = date.today()
    daily = {as_of - timedelta(days=offset): 2.0 for offset in range(360)}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _demand_events(cur, "ITEM-1588-B", SITE, daily)
            _lead_times(cur, "ITEM-1588-B", as_of - timedelta(days=30))
        conn.commit()
    finally:
        conn.close()

    response = admin.put(
        BUFFERS_URL,
        json={"item_code": "ITEM-1588-B", "site_code": SITE, "note": "buffer"},
    )
    assert response.status_code == 200, response.text

    response = client.get(ZONES_URL)
    assert response.status_code == 200, response.text
    zones = {row["item_code"]: row for row in response.json()["rows"]}
    assert set(zones) == {"ITEM-1588-B"}
    zone = zones["ITEM-1588-B"]
    assert zone["status"] == "ok"
    expected = buffer_zones(
        zone["adu"],
        zone["dlt_days"],
        zone["order_cycle_days"],
        zone["demand_class"],
        zone["cv"],
        {},
    )
    assert zone["top_of_yellow"] == expected["top_of_yellow"]
    assert zone["top_of_green"] == expected["top_of_green"]
    # B's buffer row is outstanding: its ERP reorder point (25)
    # differs from its top of yellow by more than 1.
    assert abs(zone["top_of_yellow"] - 25.0) > 1

    # --- No stops: with no excess decision at all, the stand-in is
    #     never called and the preview is the decision and buffer
    #     rows, as before. -------------------------------------------
    response = client.get(PREVIEW_URL)
    assert response.status_code == 200, response.text
    payload = response.json()
    rows = payload["rows"]
    assert [(row["item_code"], row["basis"]) for row in rows] == [
        ("ITEM-1588-D", "decision"),
        ("ITEM-1588-E", "decision"),
        ("ITEM-1588-F", "decision"),
        ("ITEM-1588-C", "decision"),
        ("ITEM-1588-B", "ddmrp"),
    ]
    assert payload["count"] == len(rows) == 5
    assert payload["writable_count"] == 5
    assert ExcessRankedStandIn.calls == 0

    # --- The excess decisions, inserted straight into
    #     excess_decisions. ------------------------------------------
    _seed_excess_decisions()
    assert ExcessRankedStandIn.calls == 0

    # --- The preview: the excess rows join, the stop pairs win. ----
    response = client.get(PREVIEW_URL)
    assert response.status_code == 200, response.text
    payload = response.json()
    rows = payload["rows"]
    by_pair = {(row["item_code"], row["site_code"]): row for row in rows}

    # The row shape every row carries (the pinned key set of the
    # issue 715 proof, basis included).
    for row in rows:
        assert set(row.keys()) == {
            "item_code",
            "site_code",
            "decision_id",
            "decided_at",
            "money_at_stake",
            "source_system",
            "current_reorder_point",
            "current_max_qty",
            "current_order_qty",
            "proposed_reorder_point",
            "proposed_max_qty",
            "writable",
            "blocked_reason",
            "basis",
        }
        assert row["site_code"] == SITE

    # The order: the decision rows keep their order and come first,
    # the buffer rows follow them (B's is gone — the stop wins),
    # and the excess rows follow them, ordered by item_code then
    # site_code.
    assert [(row["item_code"], row["basis"]) for row in rows] == [
        ("ITEM-1588-D", "decision"),
        ("ITEM-1588-E", "decision"),
        ("ITEM-1588-F", "decision"),
        ("ITEM-1588-A", "excess"),
        ("ITEM-1588-B", "excess"),
    ]
    assert payload["count"] == len(rows) == 5
    assert payload["writable_count"] == 5
    # The excess list, read once per call.
    assert ExcessRankedStandIn.calls == 1

    # The stop: the listed accepted stop with reorder point 25 and
    # max 60 is listed once, with basis "excess", proposed 0 and 0,
    # and the decision's id and time and the listed money.
    row_a = by_pair[("ITEM-1588-A", SITE)]
    assert row_a["basis"] == "excess"
    assert row_a["decision_id"] == "ed-1588-a"
    assert datetime.fromisoformat(row_a["decided_at"]) == datetime(
        2026, 10, 2, 9, 30, tzinfo=UTC
    )
    assert row_a["money_at_stake"] == 5000.0
    assert row_a["source_system"] == "acumatica"
    assert row_a["current_reorder_point"] == 25.0
    assert row_a["current_max_qty"] == 60.0
    assert row_a["current_order_qty"] == 20.0
    assert row_a["proposed_reorder_point"] == 0.0
    assert row_a["proposed_max_qty"] == 0.0
    assert row_a["writable"] is True
    assert row_a["blocked_reason"] is None

    # The precedence: the listed stop that also has an outstanding
    # accepted decision and an outstanding "ok" buffer is listed
    # once, as "excess".
    b_rows = [row for row in rows if row["item_code"] == "ITEM-1588-B"]
    assert len(b_rows) == 1
    row_b = b_rows[0]
    assert row_b["basis"] == "excess"
    assert row_b["decision_id"] == "ed-1588-b"
    assert datetime.fromisoformat(row_b["decided_at"]) == datetime(
        2026, 10, 2, 10, 0, tzinfo=UTC
    )
    assert row_b["money_at_stake"] == 4200.0
    assert row_b["proposed_reorder_point"] == 0.0
    assert row_b["proposed_max_qty"] == 0.0

    # At 0 and 0: the listed stop C is not listed, and neither is
    # its decision row.
    assert ("ITEM-1588-C", SITE) not in by_pair

    # Not a stop: the accepted "reduce" (D, listed or not) and the
    # stop the stand-in does not list (E) keep their decision rows,
    # and so does the stop followed by a later dismissal (F), which
    # is not listed.
    for item_code, decision_id in (
        ("ITEM-1588-D", "pd-1588-d"),
        ("ITEM-1588-E", "pd-1588-e"),
        ("ITEM-1588-F", "pd-1588-f"),
    ):
        row = by_pair[(item_code, SITE)]
        assert row["basis"] == "decision"
        assert row["decision_id"] == decision_id

    # --- The confirm: a listed stop is written through the shared
    #     code, as for every row. ------------------------------------
    response = admin.post(
        CONFIRM_URL,
        json={"items": [{"item_code": "ITEM-1588-A", "site_code": SITE}]},
    )
    assert response.status_code == 200, response.text
    confirm = response.json()
    assert confirm["written"] == 1
    assert confirm["unchanged"] == 0
    assert confirm["failed"] == 0
    assert confirm["skipped"] == 0
    (result,) = confirm["results"]
    assert result == {
        "item_code": "ITEM-1588-A",
        "site_code": SITE,
        "outcome": "written",
        "reason": None,
        "before": {"reorder_point": 25.0, "max_qty": 60.0, "order_qty": 20.0},
        "after": {"reorder_point": 0.0, "max_qty": 0.0, "order_qty": None},
    }

    # The writer was called with 0 and 0; the Odoo fake was not
    # called.
    assert FakeAcumaticaClient.calls == [
        {
            "item_code": "ITEM-1588-A",
            "site_code": SITE,
            "reorder_point": 0.0,
            "max_qty": 0.0,
        }
    ]
    assert FakeOdooClient.calls == []

    # One audit row: basis "excess" and the excess decision's id.
    (audit,) = _audit_rows()
    assert audit["item_code"] == "ITEM-1588-A"
    assert audit["site_code"] == SITE
    assert audit["source_system"] == "acumatica"
    assert audit["decision_id"] == "ed-1588-a"
    assert audit["basis"] == "excess"
    assert audit["before_reorder_point"] == 25.0
    assert audit["before_max_qty"] == 60.0
    assert audit["after_reorder_point"] == 0.0
    assert audit["after_max_qty"] == 0.0
    assert audit["requested_reorder_point"] == 0.0
    assert audit["requested_max_qty"] == 0.0
    assert audit["outcome"] == "written"
    assert audit["error"] is None
    assert audit["written_by"] == "dev-admin-1"

    # The item-site takes the after values: 0 and 0, order_qty
    # null.
    assert _item_site("ITEM-1588-A") == (0.0, 0.0, None)

    # The preview then no longer lists the pair: at 0 and 0 there
    # is nothing left to stop.
    response = client.get(PREVIEW_URL)
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    assert [(row["item_code"], row["basis"]) for row in rows] == [
        ("ITEM-1588-D", "decision"),
        ("ITEM-1588-E", "decision"),
        ("ITEM-1588-F", "decision"),
        ("ITEM-1588-B", "excess"),
    ]

    # --- The record: the excess push is read back unchanged once
    #     item_sites.synced_at is later than the push and the values
    #     are 0 and 0; the other two kinds answer as they did
    #     before. ------------------------------------------------------
    _set_synced_at("ITEM-1588-A", audit["written_at"] + timedelta(seconds=1))
    response = client.get(RECORD_URL)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "reorder_points": {"pushed": 0, "read": 0, "unchanged": 0, "rate": None},
        "buffers": {"pushed": 0, "read": 0, "unchanged": 0, "rate": None},
        "excess": {"pushed": 1, "read": 1, "unchanged": 1, "rate": 1.0},
    }

    # --- The record: a null item_sites.synced_at is never read
    #     back, and the record still answers — the push counts as
    #     pushed, never as read, and the rate stays null. ----------
    monkeypatch.setattr(push_record_module, "_item_sites", _null_synced_sites)
    response = client.get(RECORD_URL)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "reorder_points": {"pushed": 0, "read": 0, "unchanged": 0, "rate": None},
        "buffers": {"pushed": 0, "read": 0, "unchanged": 0, "rate": None},
        "excess": {"pushed": 1, "read": 0, "unchanged": 0, "rate": None},
    }
