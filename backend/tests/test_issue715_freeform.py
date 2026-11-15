"""Issue #715 proof: GET /api/parameter-writeback/preview.

The preview lists the item-sites whose current planner decision is
"accepted" but not yet applied to the ERP — outstanding by the
decision summary's rule: the item-site's ``item_sites.reorder_point``
is null or differs from the decision's
``recommended_reorder_point_units`` by more than 1 — in ONE query
whatever the number of item-sites, and proposes the values a
writeback would push.  ``item_sites`` and ``planner_decisions`` are
seeded directly in the test database (fixed decided_at values pin the
"latest decision per item-site" selection).

Proved here:

  * the rows — an accepted Acumatica decision whose ERP reorder point
    is 3 below the recommendation appears, writable, with
    ``proposed_max_qty`` = proposed + ``order_qty``; an accepted Odoo
    decision with a null ``order_qty`` and a ``max_qty`` below the
    proposal appears with ``proposed_max_qty`` equal to the proposal;
    an accepted decision whose ERP reorder point is within 1 does not
    appear; an item-site whose latest decision is "dismissed", after
    an earlier "accepted" one, does not appear; an accepted decision
    with no ``item_sites`` row appears with ``writable`` false and
    ``blocked_reason`` "no item_sites row"; a row with
    ``source_system`` "aidw-feed" appears with "unsupported source
    system";
  * the totals — ``count`` and ``writable_count``;
  * the ordering — ``money_at_stake`` descending with nulls last, then
    ``item_code`` and ``site_code`` (a money tie resolves on
    item_code);
  * unauthenticated access is 401;
  * no ERP call — the ERP clients are patched to fail if called, and
    nothing is written;
  * the identity requirement — the proof also runs as a NON-ADMIN
    (role "user"): a second pass after a full reload (a fresh
    TestClient booted the production way — the token the page would
    take from the URL hash as ``#access_token=...``, with
    ``GET /api/auth/config`` answering publicly and
    ``GET /api/auth/me`` resolving the viewer) sees the admin-only
    control absent (the preview answers 200 for the viewer) while the
    page still renders, and no token is still a 401.
"""

from datetime import UTC, date, datetime, timezone

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.feed.acumatica_client import AcumaticaClient
from app.feed.odoo_client import OdooClient
from app.main import app

PREVIEW_URL = "/api/parameter-writeback/preview"

SITE = "SITE-715"
AS_OF = date(2026, 10, 1)

# item_code -> (item_sites id, reorder_point, max_qty, order_qty,
# source_system); ITEM-715-E has no item_sites row at all.
ITEM_SITES = {
    "ITEM-715-A": ("is-715-a", 97.0, 150.0, 25.0, "acumatica"),
    "ITEM-715-B": ("is-715-b", 52.0, 40.0, None, "odoo"),
    "ITEM-715-C": ("is-715-c", 101.0, 160.0, 30.0, "acumatica"),
    "ITEM-715-D": ("is-715-d", 80.0, 120.0, 20.0, "acumatica"),
    "ITEM-715-F": ("is-715-f", 80.0, 120.0, 20.0, "aidw-feed"),
}

# decision id -> (item_code, decision, recommended units, money,
# decided_at)
DECISIONS = {
    "pd-715-a": ("ITEM-715-A", "accepted", 100, 5000.0, "2026-10-01T09:00:00+00"),
    "pd-715-b": ("ITEM-715-B", "accepted", 50, 2000.0, "2026-10-01T10:00:00+00"),
    "pd-715-c": ("ITEM-715-C", "accepted", 100, 9000.0, "2026-10-01T11:00:00+00"),
    # ITEM-715-D: the accepted one is earlier, the dismissal is the
    # current decision, so D must not appear even though its ERP
    # reorder point (80) is far from the recommendation (100).
    "pd-715-d1": ("ITEM-715-D", "accepted", 100, 8000.0, "2026-10-02T09:00:00+00"),
    "pd-715-d2": ("ITEM-715-D", "dismissed", 100, 8000.0, "2026-10-03T09:00:00+00"),
    # ITEM-715-E: accepted, no item_sites row, null money.
    "pd-715-e": ("ITEM-715-E", "accepted", 60, None, "2026-10-01T12:00:00+00"),
    # ITEM-715-F: ties ITEM-715-B's money (2000.0) -> item_code order.
    "pd-715-f": ("ITEM-715-F", "accepted", 100, 2000.0, "2026-10-01T13:00:00+00"),
}


def _seed() -> None:
    """Seed item_sites and planner_decisions directly in the database."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item_code, (
                site_id,
                reorder,
                max_qty,
                order_qty,
                source,
            ) in ITEM_SITES.items():
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
                        "not now" if decision == "dismissed" else None,
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


def _stored_rows() -> list[dict]:
    """The raw planner_decisions rows, for the no-write assertion."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM planner_decisions ORDER BY item_code, id")
            rows = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT id FROM item_sites ORDER BY item_code")
            rows += [f"is:{r[0]}" for r in cur.fetchall()]
    finally:
        conn.close()
    return rows


def _explode(*args, **kwargs):
    raise AssertionError("the preview must not call the ERP clients")


def test_issue715_freeform(monkeypatch, client):
    # No ERP call: patch both ERP clients to fail if called, on any
    # of their public methods.
    for client_cls in (AcumaticaClient, OdooClient):
        for name in dir(client_cls):
            if name.startswith("_") or not callable(getattr(client_cls, name)):
                continue
            monkeypatch.setattr(client_cls, name, _explode)

    _seed()
    before = _stored_rows()

    # --- The endpoint. ---------------------------------------------
    response = client.get(PREVIEW_URL)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload.keys()) == {"rows", "count", "writable_count"}
    rows = payload["rows"]
    by_item = {row["item_code"]: row for row in rows}

    # The ordering: money descending with nulls last, then item_code
    # and site_code (B and F tie at 2000.0; B sorts before F).
    assert [row["item_code"] for row in rows] == [
        "ITEM-715-A",
        "ITEM-715-B",
        "ITEM-715-F",
        "ITEM-715-E",
    ]

    # The totals.
    assert payload["count"] == len(rows) == 4
    assert payload["writable_count"] == 2

    # The row fields every row carries.
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

    # AC-3 rows: the accepted Acumatica decision 3 below the
    # recommendation appears, writable, proposed max = proposed +
    # order_qty.
    row_a = by_item["ITEM-715-A"]
    assert row_a["decision_id"] == "pd-715-a"
    assert datetime.fromisoformat(row_a["decided_at"]) == datetime(
        2026, 10, 1, 9, 0, tzinfo=UTC
    )
    assert row_a["money_at_stake"] == 5000.0
    assert row_a["source_system"] == "acumatica"
    assert row_a["current_reorder_point"] == 97.0
    assert row_a["current_max_qty"] == 150.0
    assert row_a["current_order_qty"] == 25.0
    assert row_a["proposed_reorder_point"] == 100.0
    assert row_a["proposed_max_qty"] == 125.0  # 100.0 + 25.0
    assert row_a["writable"] is True
    assert row_a["blocked_reason"] is None

    # AC-3 rows: the accepted Odoo decision with a null order_qty and
    # a max_qty (40) below the proposal (50) appears with
    # proposed_max_qty equal to the proposal.
    row_b = by_item["ITEM-715-B"]
    assert row_b["decision_id"] == "pd-715-b"
    assert row_b["money_at_stake"] == 2000.0
    assert row_b["source_system"] == "odoo"
    assert row_b["current_reorder_point"] == 52.0
    assert row_b["current_max_qty"] == 40.0
    assert row_b["current_order_qty"] is None
    assert row_b["proposed_reorder_point"] == 50.0
    assert row_b["proposed_max_qty"] == 50.0  # 40.0 < 50.0 -> the proposal
    assert row_b["writable"] is True
    assert row_b["blocked_reason"] is None

    # AC-3 rows: the "aidw-feed" row appears, not writable, blocked by
    # the unsupported source system.
    row_f = by_item["ITEM-715-F"]
    assert row_f["decision_id"] == "pd-715-f"
    assert row_f["money_at_stake"] == 2000.0
    assert row_f["source_system"] == "aidw-feed"
    assert row_f["current_reorder_point"] == 80.0
    assert row_f["current_max_qty"] == 120.0
    assert row_f["current_order_qty"] == 20.0
    assert row_f["proposed_reorder_point"] == 100.0
    assert row_f["proposed_max_qty"] == 120.0  # 100.0 + 20.0
    assert row_f["writable"] is False
    assert row_f["blocked_reason"] == "unsupported source system"

    # AC-3 rows: the accepted decision with no item_sites row appears
    # with every current value null, writable false and
    # "no item_sites row".
    row_e = by_item["ITEM-715-E"]
    assert row_e["decision_id"] == "pd-715-e"
    assert row_e["money_at_stake"] is None
    assert row_e["source_system"] is None
    assert row_e["current_reorder_point"] is None
    assert row_e["current_max_qty"] is None
    assert row_e["current_order_qty"] is None
    assert row_e["proposed_reorder_point"] == 60.0
    assert row_e["proposed_max_qty"] == 60.0  # nothing to keep -> the proposal
    assert row_e["writable"] is False
    assert row_e["blocked_reason"] == "no item_sites row"

    # AC-3 rows: the applied decision (ERP reorder point 101 within 1
    # of 100) and the item-site whose latest decision is "dismissed"
    # after an earlier "accepted" one do not appear — even though
    # both carry the biggest money at stake (9000 / 8000).
    assert "ITEM-715-C" not in by_item
    assert "ITEM-715-D" not in by_item
    assert {row["item_code"] for row in rows} == {
        "ITEM-715-A",
        "ITEM-715-B",
        "ITEM-715-F",
        "ITEM-715-E",
    }

    # --- No side effects: nothing written, no ERP call. -------------
    assert _stored_rows() == before

    # --- Unauthenticated access: 401. --------------------------------
    with TestClient(app) as anonymous:
        response = anonymous.get(PREVIEW_URL)
    assert response.status_code == 401, response.text
    assert response.headers.get("www-authenticate") == "Bearer"

    # --- Identity, second pass after a full reload: the page boots the
    #    production way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the non-admin viewer; the admin-only
    #    control is absent (the preview answers 200 for the viewer)
    #    while the page still renders, and no token is still a 401.
    dev_token = "dev-secret-token"  # the bare token from #access_token=...
    viewer = TestClient(app, headers={"Authorization": f"Bearer {dev_token}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == "dev-user-1"
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]

    # The admin-only control is absent: the preview is readable by the
    # non-admin viewer, with the same rows and totals the page renders.
    response = viewer.get(PREVIEW_URL)
    assert response.status_code == 200, response.text
    assert response.json() == payload
    # The gate is intact: unauthenticated /api traffic is still 401.
    assert anon_reloaded.get(PREVIEW_URL).status_code == 401
    assert _stored_rows() == before
