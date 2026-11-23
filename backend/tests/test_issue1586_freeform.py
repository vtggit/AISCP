"""Issue #1586 proof: a DDMRP-managed item-site's decision row is never
listed or written.

The shared preview-row construction of
``GET /api/parameter-writeback/preview`` and
``POST /api/parameter-writeback/confirm``: a designated item-site whose
zone row from ``app.api.ddmrp.ddmrp_zone_rows()`` has status "ok" is
DDMRP-managed, so its decision row is never listed or written, whether
or not its buffer row is outstanding.  Before this fix only a pair
whose buffer row was outstanding lost its decision row, so a
designated "ok" pair whose ERP values already sat on the zone tops
still listed (and wrote) its accepted decision.

The setup is the way test 762 seeds its data: ``item_sites``,
``planner_decisions``, demand events and lead-time observations are
inserted directly in the test database, the buffers are designated the
way test 740 designates them (an admin ``PUT /api/ddmrp/buffers``),
and the two client classes in the route module are replaced with
fakes that record their calls.

Proved here, every field of every row read strictly:

  * the pushed buffer — a designated pair with an "ok" zone, its
    ``item_sites`` values equal to its top of yellow and top of green
    (its buffer row is not outstanding), and an accepted decision
    recommending a reorder point 10 units above the top of yellow:
    the preview lists no row for the pair, and confirming the pair
    answers "skipped" / "not outstanding", calls no writer and stores
    no audit row;
  * the outstanding buffer — the same pair, its reorder point then
    set 5 units below the top of yellow, is listed once, with
    ``basis`` "ddmrp";
  * not "ok" — a designated pair with no demand (its zone row is not
    "ok") and an outstanding accepted decision is listed with
    ``basis`` "decision";
  * not designated — a plain outstanding accepted decision is listed
    with ``basis`` "decision".
"""

from datetime import date, timedelta

import psycopg2.extras
from fastapi.testclient import TestClient

import app.api.parameter_writeback as parameter_writeback
from app.db.connection import get_connection
from app.main import app

PREVIEW_URL = "/api/parameter-writeback/preview"
CONFIRM_URL = "/api/parameter-writeback/confirm"
ZONES_URL = "/api/ddmrp/zones"
BUFFERS_URL = "/api/ddmrp/buffers"

SITE = "SITE-1586"
ADMIN_TOKEN = "dev-secret-token:admin"
AS_OF = date(2026, 10, 1)

# item_code -> (item_sites id, reorder_point, max_qty, order_qty,
# source_system).  A is the DDMRP-managed pair (its values are set to
# its zone tops once the zones are read); B is designated but has no
# demand (its zone row is not "ok"); C is a plain, non-designated
# item-site.
ITEMS = {
    "ITEM-1586-A": ("is-1586-a", 25.0, 60.0, 20.0, "acumatica"),
    "ITEM-1586-B": ("is-1586-b", 25.0, 60.0, 20.0, "acumatica"),
    "ITEM-1586-C": ("is-1586-c", 80.0, 120.0, 20.0, "acumatica"),
}

# decision id -> (item_code, decision, recommended units, money,
# decided_at).  A's recommended units are a placeholder, replaced with
# its top of yellow plus 10 once the zones are read; B and C are
# outstanding by the summary's rule (25 vs 35, 80 vs 100).
DECISIONS = {
    "pd-1586-a": ("ITEM-1586-A", "accepted", 100, 5000.0, "2026-10-01T09:00:00+00"),
    "pd-1586-b": ("ITEM-1586-B", "accepted", 35, 3000.0, "2026-10-01T10:00:00+00"),
    "pd-1586-c": ("ITEM-1586-C", "accepted", 100, 2000.0, "2026-10-01T11:00:00+00"),
}


class FakeAcumaticaClient:
    """The fake for the route module's AcumaticaClient: records every
    call (the proof confirms one pair and it must skip, so it must
    stay empty)."""

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
                f"id-de-1586-{item}-{day.isoformat()}",
                f"de-1586-{item}-{day.isoformat()}",
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
                f"id-lt-1586-{item}-{i}",
                f"lt-1586-{item}-{i}",
                f"SUP-1586-{item}",
                item,
                (received - timedelta(days=lead)).isoformat(),
                received.isoformat(),
            ),
        )


def _set_a_recommendation(recommended_units: int) -> None:
    """A's accepted decision recommends ``recommended_units`` (an
    integer, the column's type)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE planner_decisions "
                "SET recommended_reorder_point_units = %s "
                "WHERE id = 'pd-1586-a'",
                (recommended_units,),
            )
        conn.commit()
    finally:
        conn.close()


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


def _audit_rows() -> list[dict]:
    """The raw parameter_writebacks rows, oldest first."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM parameter_writebacks ORDER BY written_at, id")
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def test_issue1586_freeform(client, monkeypatch):
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

    # A's demand and lead times (tests 740's way); B has none, so its
    # zone row is not "ok", and C is not designated at all.
    as_of = date.today()
    daily = {as_of - timedelta(days=offset): 2.0 for offset in range(360)}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _demand_events(cur, "ITEM-1586-A", SITE, daily)
            _lead_times(cur, "ITEM-1586-A", as_of - timedelta(days=30))
        conn.commit()
    finally:
        conn.close()

    # A and B are designated the way test 740 designates them (an
    # admin PUT); C is not.
    for item in ("ITEM-1586-A", "ITEM-1586-B"):
        response = admin.put(
            BUFFERS_URL, json={"item_code": item, "site_code": SITE, "note": "buffer"}
        )
        assert response.status_code == 200, response.text

    # The known zones of the designated pairs, as of the default
    # as-of date (the way test 740 reads them).
    response = client.get(ZONES_URL)
    assert response.status_code == 200, response.text
    zones = {row["item_code"]: row for row in response.json()["rows"]}
    assert set(zones) == {"ITEM-1586-A", "ITEM-1586-B"}
    assert zones["ITEM-1586-A"]["status"] == "ok"
    assert zones["ITEM-1586-B"]["status"] == "no_demand"
    top_of_yellow = zones["ITEM-1586-A"]["top_of_yellow"]
    top_of_green = zones["ITEM-1586-A"]["top_of_green"]

    # A is the pushed buffer: its decision recommends 10 units above
    # its top of yellow (outstanding by the summary's rule) and its
    # item-site already sits on the zone tops (its buffer row is not
    # outstanding).
    _set_a_recommendation(top_of_yellow + 10)
    _set_item_site("ITEM-1586-A", float(top_of_yellow), float(top_of_green))

    # The expected decision rows, every field pinned (B's money at
    # stake 3000 orders it before C's 2000).
    row_b = {
        "item_code": "ITEM-1586-B",
        "site_code": SITE,
        "decision_id": "pd-1586-b",
        "decided_at": "2026-10-01T10:00:00Z",
        "money_at_stake": 3000.0,
        "source_system": "acumatica",
        "current_reorder_point": 25.0,
        "current_max_qty": 60.0,
        "current_order_qty": 20.0,
        "proposed_reorder_point": 35.0,
        "proposed_max_qty": 55.0,  # 35.0 + 20.0
        "writable": True,
        "blocked_reason": None,
        "basis": "decision",
    }
    row_c = {
        "item_code": "ITEM-1586-C",
        "site_code": SITE,
        "decision_id": "pd-1586-c",
        "decided_at": "2026-10-01T11:00:00Z",
        "money_at_stake": 2000.0,
        "source_system": "acumatica",
        "current_reorder_point": 80.0,
        "current_max_qty": 120.0,
        "current_order_qty": 20.0,
        "proposed_reorder_point": 100.0,
        "proposed_max_qty": 120.0,  # 100.0 + 20.0
        "writable": True,
        "blocked_reason": None,
        "basis": "decision",
    }

    # --- The pushed buffer: its decision row is never listed. -------
    response = client.get(PREVIEW_URL)
    assert response.status_code == 200, response.text
    payload = response.json()
    rows = payload["rows"]
    # The DDMRP-managed pair lists no row: not a buffer row (not
    # outstanding) and not its decision row (its zone row is "ok").
    assert rows == [row_b, row_c]
    assert ("ITEM-1586-A", SITE) not in {
        (row["item_code"], row["site_code"]) for row in rows
    }
    assert payload["count"] == 2
    assert payload["writable_count"] == 2

    # Confirming the pair answers "skipped" / "not outstanding",
    # calls no writer and stores no audit row.
    response = admin.post(
        CONFIRM_URL, json={"items": [{"item_code": "ITEM-1586-A", "site_code": SITE}]}
    )
    assert response.status_code == 200, response.text
    confirm = response.json()
    assert confirm["written"] == 0
    assert confirm["unchanged"] == 0
    assert confirm["failed"] == 0
    assert confirm["skipped"] == 1
    (result,) = confirm["results"]
    assert result == {
        "item_code": "ITEM-1586-A",
        "site_code": SITE,
        "outcome": "skipped",
        "reason": "not outstanding",
        "before": None,
        "after": None,
    }
    assert FakeAcumaticaClient.calls == []
    assert FakeOdooClient.calls == []
    assert _audit_rows() == []

    # --- The outstanding buffer: the same pair, now listed once. ----
    _set_item_site("ITEM-1586-A", float(top_of_yellow - 5), float(top_of_green))

    response = client.get(PREVIEW_URL)
    assert response.status_code == 200, response.text
    payload = response.json()
    rows = payload["rows"]
    # The decision rows keep their order and come first, the buffer
    # rows follow them; A's buffer row is listed once, with basis
    # "ddmrp" — its decision row is still never listed.
    assert rows == [
        row_b,
        row_c,
        {
            "item_code": "ITEM-1586-A",
            "site_code": SITE,
            "decision_id": None,
            "decided_at": None,
            "money_at_stake": None,
            "source_system": "acumatica",
            "current_reorder_point": float(top_of_yellow - 5),
            "current_max_qty": float(top_of_green),
            "current_order_qty": 20.0,
            "proposed_reorder_point": float(top_of_yellow),
            "proposed_max_qty": float(top_of_green),
            "writable": True,
            "blocked_reason": None,
            "basis": "ddmrp",
        },
    ]
    a_rows = [row for row in rows if row["item_code"] == "ITEM-1586-A"]
    assert len(a_rows) == 1
    assert a_rows[0]["basis"] == "ddmrp"
    assert payload["count"] == 3
    assert payload["writable_count"] == 3
