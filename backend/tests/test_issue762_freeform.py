"""Issue #762 proof: the DDMRP buffer rows of the parameter writeback.

The shared preview-row construction of
``GET /api/parameter-writeback/preview`` and
``POST /api/parameter-writeback/confirm`` gains a second source, read
once per call: every designated DDMRP buffer whose zone row from
``app.api.ddmrp.ddmrp_zone_rows()`` has status "ok" — proposed
reorder point the top of yellow, proposed max quantity the top of
green.  ``item_sites``, ``planner_decisions``, demand events and
lead-time observations are seeded directly in the test database the
way tests 719 and 740 seed theirs, the buffers are designated the
way test 740 designates them (an admin
``PUT /api/ddmrp/buffers``), and the two client classes in the route
module are replaced with fakes the way test 719 replaces them.

Proved here:

  * a buffer row — a buffered pair whose ERP reorder point differs
    from its top of yellow appears with ``basis`` "ddmrp", the
    proposed reorder point equal to the top of yellow, the proposed
    max equal to the top of green, and null ``decision_id``
    (``decided_at`` and ``money_at_stake`` null too, the current
    values and the writable/blocked rules as for decision rows);
  * already in place — a buffered pair whose ERP values already
    match within 1 does not appear (a non-"ok" designated pair
    never appears either);
  * precedence — a pair with both an accepted decision and a buffer
    appears once, as "ddmrp";
  * ordering — the decision rows keep their order and come first,
    the buffer rows follow them, ordered by item_code then
    site_code; ``count`` and ``writable_count`` include the buffer
    rows;
  * confirm — with the ERP client classes replaced by fakes,
    confirming a buffer row calls the writer with the top of yellow
    and the top of green, stores an audit row with a null
    ``decision_id``, updates ``item_sites``, and the preview no
    longer lists the pair — the response shape unchanged.
"""

from datetime import date, timedelta

import psycopg2.extras
from fastapi.testclient import TestClient

import app.api.parameter_writeback as parameter_writeback
from app.db.connection import get_connection
from app.ddmrp.zones import buffer_zones
from app.main import app

PREVIEW_URL = "/api/parameter-writeback/preview"
CONFIRM_URL = "/api/parameter-writeback/confirm"
ZONES_URL = "/api/ddmrp/zones"
BUFFERS_URL = "/api/ddmrp/buffers"

SITE = "SITE-762"
ADMIN_TOKEN = "dev-secret-token:admin"
AS_OF = date(2026, 10, 1)

# item_code -> (item_sites id, reorder_point, max_qty, order_qty,
# source_system).  A is the outstanding buffer row; B is set to its
# zone tops after the zones are read (already in place); C carries
# both an accepted decision and a buffer (precedence); D is a plain
# accepted decision; E is designated but has no demand (non-"ok").
ITEMS = {
    "ITEM-762-A": ("is-762-a", 25.0, 60.0, 20.0, "acumatica"),
    "ITEM-762-B": ("is-762-b", 25.0, 60.0, 20.0, "acumatica"),
    "ITEM-762-C": ("is-762-c", 25.0, 60.0, 20.0, "acumatica"),
    "ITEM-762-D": ("is-762-d", 80.0, 120.0, 20.0, "acumatica"),
    "ITEM-762-E": ("is-762-e", 25.0, 60.0, 20.0, "acumatica"),
}

# decision id -> (item_code, decision, recommended units, money,
# decided_at)
DECISIONS = {
    # ITEM-762-C: accepted and outstanding (25 vs 55), but the buffer
    # row must win — the pair appears once, as "ddmrp".
    "pd-762-c": ("ITEM-762-C", "accepted", 55, 4000.0, "2026-10-01T09:00:00+00"),
    # ITEM-762-D: accepted and outstanding (80 vs 100) -> the one
    # "decision" row.
    "pd-762-d": ("ITEM-762-D", "accepted", 100, 3000.0, "2026-10-01T10:00:00+00"),
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


def _seed_item_sites_and_decisions() -> None:
    """Seed item_sites and planner_decisions directly (the way test
    719 seeds them)."""
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
            for decision_id, (
                item_code,
                decision,
                units,
                money,
                decided_at,
            ) in DECISIONS.items():
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
                        decision,
                        None,
                        None,
                        AS_OF,
                        units,
                        80.0,
                        money,
                        "seeder",
                        decided_at,
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _demand_events(cur, item: str, site: str, daily: dict[date, float]) -> None:
    """One demand event per day of ``daily`` (the way test 740 seeds
    its demand events)."""
    cur.executemany(
        "INSERT INTO demand_events (id, business_key, item_code, site_code, "
        "quantity, occurred_on, event_type, source_system) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                f"id-de-762-{item}-{day.isoformat()}",
                f"de-762-{item}-{day.isoformat()}",
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
    10 (the way test 740 seeds its lead-time observations)."""
    for i, lead in enumerate((8, 10, 12)):
        cur.execute(
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, 'test')",
            (
                f"id-lt-762-{item}-{i}",
                f"lt-762-{item}-{i}",
                f"SUP-762-{item}",
                item,
                (received - timedelta(days=lead)).isoformat(),
                received.isoformat(),
            ),
        )


def _set_item_site(item_code: str, reorder_point: float, max_qty: float) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE item_sites SET reorder_point = %s, max_qty = %s "
                "WHERE item_code = %s AND site_code = %s",
                (reorder_point, max_qty, item_code, SITE),
            )
        conn.commit()
    finally:
        conn.close()


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


def _zone_tops(zones: dict[str, dict], item_code: str) -> tuple[int, int]:
    """The (top_of_yellow, top_of_green) of one "ok" zone row, checked
    against the pure zone arithmetic of app.ddmrp.zones.buffer_zones
    (the way test 740 checks it)."""
    zone = zones[item_code]
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
    return zone["top_of_yellow"], zone["top_of_green"]


def test_issue762_freeform(client, monkeypatch):
    # The two client classes in the route module are replaced with
    # fakes that record their calls (the way test 719 replaces them).
    FakeAcumaticaClient.calls = []
    FakeOdooClient.calls = []
    monkeypatch.setattr(parameter_writeback, "AcumaticaClient", FakeAcumaticaClient)
    monkeypatch.setattr(parameter_writeback, "OdooClient", FakeOdooClient)
    # The recommendation inputs: the registry defaults, never ambient
    # environment values (the way test 740 pins them).
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # --- The seed: item-sites and decisions (test 719's way). --------
    _seed_item_sites_and_decisions()

    # The as-of date: the demand statistics' default as-of date is the
    # earlier of today and the latest demand event, so the latest
    # event seeded on that day is the as-of date the routes answer
    # as of.
    as_of = date.today()
    daily = {as_of - timedelta(days=offset): 2.0 for offset in range(360)}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item in ("ITEM-762-A", "ITEM-762-B", "ITEM-762-C"):
                _demand_events(cur, item, SITE, daily)
                # Three 8/10/12-day observations, received 30 days
                # before the as-of date (the way test 740 seeds its
                # lead times).
                _lead_times(cur, item, as_of - timedelta(days=30))
        conn.commit()
    finally:
        conn.close()

    # Designate the four buffers the way test 740 designates them
    # (an admin PUT); E has no demand, so its zone is "no_demand".
    for item in ("ITEM-762-A", "ITEM-762-B", "ITEM-762-C", "ITEM-762-E"):
        response = admin.put(
            BUFFERS_URL, json={"item_code": item, "site_code": SITE, "note": "buffer"}
        )
        assert response.status_code == 200, response.text

    # The known zones of the designated pairs, as of the default
    # as-of date (the way test 740 reads them).
    response = client.get(ZONES_URL)
    assert response.status_code == 200, response.text
    zones = {row["item_code"]: row for row in response.json()["rows"]}
    assert set(zones) == {
        "ITEM-762-A",
        "ITEM-762-B",
        "ITEM-762-C",
        "ITEM-762-E",
    }
    tops = {
        item: _zone_tops(zones, item)
        for item in ("ITEM-762-A", "ITEM-762-B", "ITEM-762-C")
    }
    assert zones["ITEM-762-E"]["status"] == "no_demand"

    # B is already in place: its ERP values are its zone tops, within
    # 1 of the proposal.
    top_of_yellow_b, top_of_green_b = tops["ITEM-762-B"]
    _set_item_site("ITEM-762-B", float(top_of_yellow_b), float(top_of_green_b))

    # --- The preview: the buffer rows join the decision rows. --------
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

    # The ordering: the decision rows keep their order and come
    # first, the buffer rows follow them, ordered by item_code then
    # site_code (D's decision row, then A and C's buffer rows).
    assert [(row["item_code"], row["basis"]) for row in rows] == [
        ("ITEM-762-D", "decision"),
        ("ITEM-762-A", "ddmrp"),
        ("ITEM-762-C", "ddmrp"),
    ]

    # The totals include the buffer rows.
    assert payload["count"] == len(rows) == 3
    assert payload["writable_count"] == 3

    # A buffer row: the buffered pair whose ERP reorder point (25)
    # differs from its top of yellow appears with basis "ddmrp", the
    # proposed values the zone tops, and decision_id null.
    row_a = by_pair[("ITEM-762-A", SITE)]
    top_of_yellow_a, top_of_green_a = tops["ITEM-762-A"]
    assert row_a["basis"] == "ddmrp"
    assert row_a["decision_id"] is None
    assert row_a["decided_at"] is None
    assert row_a["money_at_stake"] is None
    assert row_a["source_system"] == "acumatica"
    assert row_a["current_reorder_point"] == 25.0
    assert row_a["current_max_qty"] == 60.0
    assert row_a["current_order_qty"] == 20.0
    assert row_a["proposed_reorder_point"] == float(top_of_yellow_a)
    assert row_a["proposed_max_qty"] == float(top_of_green_a)
    assert row_a["writable"] is True
    assert row_a["blocked_reason"] is None

    # The decision row keeps its shape and basis "decision".
    row_d = by_pair[("ITEM-762-D", SITE)]
    assert row_d["basis"] == "decision"
    assert row_d["decision_id"] == "pd-762-d"
    assert row_d["proposed_reorder_point"] == 100.0
    assert row_d["proposed_max_qty"] == 120.0  # 100.0 + 20.0

    # Precedence: the pair with both an accepted decision and a
    # buffer appears once, as "ddmrp".
    c_rows = [row for row in rows if row["item_code"] == "ITEM-762-C"]
    assert len(c_rows) == 1
    assert c_rows[0]["basis"] == "ddmrp"

    # Already in place (B, its ERP values its zone tops) and the
    # non-"ok" designated pair (E, no demand) do not appear.
    assert ("ITEM-762-B", SITE) not in by_pair
    assert ("ITEM-762-E", SITE) not in by_pair

    # --- The confirm: a buffer row is written like a decision row. --
    response = admin.post(
        CONFIRM_URL, json={"items": [{"item_code": "ITEM-762-A", "site_code": SITE}]}
    )
    assert response.status_code == 200, response.text
    confirm = response.json()
    assert confirm["written"] == 1
    assert confirm["unchanged"] == 0
    assert confirm["failed"] == 0
    assert confirm["skipped"] == 0
    (result,) = confirm["results"]
    assert result == {
        "item_code": "ITEM-762-A",
        "site_code": SITE,
        "outcome": "written",
        "reason": None,
        "before": {"reorder_point": 25.0, "max_qty": 60.0, "order_qty": 20.0},
        "after": {
            "reorder_point": float(top_of_yellow_a),
            "max_qty": float(top_of_green_a),
            "order_qty": float(top_of_green_a - top_of_yellow_a),
        },
    }

    # The writer was called with the top of yellow and the top of
    # green; the Odoo fake was not called.
    assert len(FakeAcumaticaClient.calls) == 1
    call = FakeAcumaticaClient.calls[0]
    assert call["item_code"] == "ITEM-762-A"
    assert call["site_code"] == SITE
    assert call["reorder_point"] == float(top_of_yellow_a)
    assert call["max_qty"] == float(top_of_green_a)
    assert FakeOdooClient.calls == []

    # One audit row, with a null decision_id.
    (audit,) = _audit_rows()
    assert audit["item_code"] == "ITEM-762-A"
    assert audit["site_code"] == SITE
    assert audit["source_system"] == "acumatica"
    assert audit["decision_id"] is None
    assert audit["before_reorder_point"] == 25.0
    assert audit["before_max_qty"] == 60.0
    assert audit["after_reorder_point"] == float(top_of_yellow_a)
    assert audit["after_max_qty"] == float(top_of_green_a)
    assert audit["requested_reorder_point"] == float(top_of_yellow_a)
    assert audit["requested_max_qty"] == float(top_of_green_a)
    assert audit["outcome"] == "written"
    assert audit["error"] is None
    assert audit["written_by"] == "dev-admin-1"

    # The item-site holds the after values, and the preview no longer
    # lists the pair: the decision row and C's buffer row remain, in
    # that order.
    assert _item_site("ITEM-762-A") == (
        float(top_of_yellow_a),
        float(top_of_green_a),
        float(top_of_green_a - top_of_yellow_a),
    )
    response = client.get(PREVIEW_URL)
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    assert [(row["item_code"], row["basis"]) for row in rows] == [
        ("ITEM-762-D", "decision"),
        ("ITEM-762-C", "ddmrp"),
    ]
