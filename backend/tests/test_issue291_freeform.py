"""Issue #291 proof: display names beside the subject columns.

The proof covers, in one test, against the test database (migrated to
the new head):

* the migration — ``alembic_version`` holds ``0012_display_names`` and
  the five new nullable columns exist beside their subject columns;
* the name resolution — a decision recorded by a token with username
  ``jason`` returns ``decided_by_name`` ``jason`` with ``decided_by``
  still the token subject, a token with no username but an email
  returns the email, and a token with neither returns the subject;
  the resolver truncates a display name to the column's length and
  treats an empty username as absent;
* every response that carries ``decided_by`` also carries
  ``decided_by_name`` — the three decision POSTs and the three
  ``GET .../decisions`` history lists;
* the settings routes — ``GET``, ``PUT`` and ``DELETE /api/settings``
  entries carry ``updated_by_name`` beside ``updated_by``, and the
  ``settings_changes`` rows appended by the store and the reset hold
  ``changed_by_name`` beside ``changed_by``;
* a row written without a name (inserted directly, simulating a row
  written before this change) comes back with a null name: a planner
  decision in its history list and a stored setting in the settings
  list.

The tokens with the identities the development mode cannot express are
validated the production way: ``app.auth.dependencies.validate_token``
is patched so the presented Bearer token selects the identity, while
the development tokens keep resolving to the identities the
development mode hands out.
"""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.auth.display_name import display_name
from app.auth.models import AuthUser
from app.db.connection import get_connection
from app.main import app
from tests.migration_head import CURRENT_HEAD

SITE = "SITE-291"
DRIFT_ITEM = "ITEM-291-DRIFT"
EXCESS_ITEM = "ITEM-291-EXCESS"
STOCKOUT_ITEM = "ITEM-291-STOCKOUT"
LEGACY_ITEM = "ITEM-291-LEGACY"
FIRST_DAY = date(2025, 9, 1)
DEMAND_DAYS = 365  # through 2026-08-31, the latest seeded demand event
AS_OF_STAMP = "2026-08-31T00:00:00+00"

JASON_TOKEN = "token-291-jason"
EMAIL_TOKEN = "token-291-email"
BARE_TOKEN = "token-291-bare"
JASON_SUB = "sub-291-jason"
EMAIL_SUB = "sub-291-email"
BARE_SUB = "sub-291-bare"
LEGACY_SUB = "legacy-sub-291"

SYS_KEY = "decisions.require_action_choice"
USR_KEY = "queue.default_filter"


def _token_clients(monkeypatch) -> dict[str, TestClient]:
    """Patch token validation so the proof's tokens select identities
    the development mode cannot express; the development tokens keep
    resolving to their development identities."""
    import app.auth.dependencies as dependencies

    users = {
        JASON_TOKEN: AuthUser(
            sub=JASON_SUB,
            username="jason",
            email="jason@corp.local",
            roles=["admin", "user"],
        ),
        EMAIL_TOKEN: AuthUser(
            sub=EMAIL_SUB,
            username=None,
            email="email-only@corp.local",
            roles=["admin", "user"],
        ),
        BARE_TOKEN: AuthUser(
            sub=BARE_SUB,
            username=None,
            email=None,
            roles=["admin", "user"],
        ),
        "dev-secret-token": AuthUser(
            sub="dev-user-1",
            username="developer",
            email="developer@aicrm.local",
            roles=["user"],
        ),
        "dev-secret-token:user": AuthUser(
            sub="dev-user-1",
            username="developer",
            email="developer@aicrm.local",
            roles=["user"],
        ),
        "dev-secret-token:admin": AuthUser(
            sub="dev-admin-1",
            username="admin",
            email="admin@aicrm.local",
            roles=["admin", "user"],
        ),
    }
    monkeypatch.setattr(dependencies, "validate_token", lambda token: users.get(token))
    return {
        token: TestClient(app, headers={"Authorization": f"Bearer {token}"})
        for token in (JASON_TOKEN, EMAIL_TOKEN, BARE_TOKEN)
    }


def _seed() -> None:
    """One drift item-site, one dead (never-moved) excess item-site and
    one stockout-risk item-site, all on fixed dates."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for prefix, item_code, daily_demand in (
                ("drift", DRIFT_ITEM, 10.0),
                ("stockout", STOCKOUT_ITEM, 2.0),
            ):
                cur.executemany(
                    "INSERT INTO demand_events "
                    "(id, business_key, item_code, site_code, quantity, "
                    "occurred_on, event_type, source_system) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            f"id-de-291-{prefix}-{day_index}",
                            f"de-291-{prefix}-{day_index}",
                            item_code,
                            SITE,
                            daily_demand,
                            (FIRST_DAY + timedelta(days=day_index)).isoformat(),
                            "shipment",
                            "test",
                        )
                        for day_index in range(DEMAND_DAYS)
                    ],
                )
            cur.executemany(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, ordered_on, "
                "received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    # 9, 10 and 10 days for the drift item ...
                    (
                        "id-lt-291-drift-1",
                        "lt-291-drift-1",
                        "SUP-291",
                        DRIFT_ITEM,
                        "2026-05-01",
                        "2026-05-10",
                        "test",
                    ),
                    (
                        "id-lt-291-drift-2",
                        "lt-291-drift-2",
                        "SUP-291",
                        DRIFT_ITEM,
                        "2026-05-05",
                        "2026-05-15",
                        "test",
                    ),
                    (
                        "id-lt-291-drift-3",
                        "lt-291-drift-3",
                        "SUP-291",
                        DRIFT_ITEM,
                        "2026-05-09",
                        "2026-05-19",
                        "test",
                    ),
                    # 8, 10 and 12 days for the stockout item (mean 10).
                    (
                        "id-lt-291-stockout-1",
                        "lt-291-stockout-1",
                        "SUP-291",
                        STOCKOUT_ITEM,
                        "2026-05-01",
                        "2026-05-09",
                        "test",
                    ),
                    (
                        "id-lt-291-stockout-2",
                        "lt-291-stockout-2",
                        "SUP-291",
                        STOCKOUT_ITEM,
                        "2026-05-05",
                        "2026-05-15",
                        "test",
                    ),
                    (
                        "id-lt-291-stockout-3",
                        "lt-291-stockout-3",
                        "SUP-291",
                        STOCKOUT_ITEM,
                        "2026-05-08",
                        "2026-05-20",
                        "test",
                    ),
                ),
            )
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, reorder_point, "
                "order_qty, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    (
                        "id-is-291-drift",
                        DRIFT_ITEM,
                        SITE,
                        2.0,
                        120.0,
                        80.0,
                        "test",
                    ),
                    (
                        "id-is-291-excess",
                        EXCESS_ITEM,
                        SITE,
                        1.5,
                        None,
                        None,
                        "test",
                    ),
                    (
                        "id-is-291-stockout",
                        STOCKOUT_ITEM,
                        SITE,
                        1.5,
                        None,
                        None,
                        "test",
                    ),
                ),
            )
            cur.executemany(
                "INSERT INTO on_hand "
                "(id, item_code, site_code, quantity_on_hand, as_of, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    # No demand at all -> dead (never_moved), all of the
                    # on hand in excess.
                    (
                        "id-oh-291-excess",
                        EXCESS_ITEM,
                        SITE,
                        40.0,
                        AS_OF_STAMP,
                        "test",
                    ),
                    # 5 on hand against a 20-unit mean lead-time demand
                    # -> listed with stockout risk.
                    (
                        "id-oh-291-stockout",
                        STOCKOUT_ITEM,
                        SITE,
                        5.0,
                        AS_OF_STAMP,
                        "test",
                    ),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_legacy_decision(decision_id: str) -> None:
    """A planner decision inserted without a name: the row a write
    before this change would have left behind (decided_by_name null)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO planner_decisions "
                "(id, item_code, site_code, decision, reason, snooze_until, "
                "as_of, recommended_reorder_point_units, current_reorder_point, "
                "money_at_stake, decided_by, decided_at) "
                "VALUES (%s, %s, %s, 'accepted', NULL, NULL, %s, %s, %s, %s, "
                "%s, now() - interval '1 day')",
                (
                    decision_id,
                    LEGACY_ITEM,
                    SITE,
                    "2026-08-31",
                    100,
                    95.0,
                    100.0,
                    LEGACY_SUB,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_legacy_setting() -> None:
    """A stored setting inserted without a name (updated_by_name
    null): the row a write before this change would have left."""
    import psycopg2.extras

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings "
                "(scope, owner, key, value, updated_by, updated_at) "
                "VALUES ('user', %s, %s, %s, %s, now())",
                (BARE_SUB, USR_KEY, psycopg2.extras.Json("decided"), BARE_SUB),
            )
        conn.commit()
    finally:
        conn.close()


def _setting_entries(client: TestClient) -> dict[str, dict]:
    resp = client.get("/api/settings")
    assert resp.status_code == 200, resp.text
    return {entry["key"]: entry for entry in resp.json()}


def _change_rows() -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT key, old_value, new_value, changed_by, changed_by_name "
                "FROM settings_changes ORDER BY id ASC"
            )
            rows = [
                {
                    "key": r[0],
                    "old_value": r[1],
                    "new_value": r[2],
                    "changed_by": r[3],
                    "changed_by_name": r[4],
                }
                for r in cur.fetchall()
            ]
    finally:
        conn.close()
    return rows


def test_issue291_freeform(client, monkeypatch):
    # -- AC-1: the resolver picks username, else email, else sub, and
    #    truncates to the column's length. ------------------------------
    jason = AuthUser(sub=JASON_SUB, username="jason", email="jason@corp.local")
    assert display_name(jason) == "jason"
    email_only = AuthUser(sub=EMAIL_SUB, username=None, email="email-only@corp.local")
    assert display_name(email_only) == "email-only@corp.local"
    bare = AuthUser(sub=BARE_SUB, username=None, email=None)
    assert display_name(bare) == BARE_SUB
    # An empty username is absent, not a name.
    assert display_name(AuthUser(sub="s1", username="", email="a@b.c")) == "a@b.c"
    # A hostilely long username is truncated to the column's length.
    long_name = display_name(AuthUser(sub="s2", username="x" * 300, email="a@b.c"))
    assert long_name == "x" * 255 and len(long_name) == 255

    tokens = _token_clients(monkeypatch)
    _seed()

    # -- AC-1: the migration is the head and every new write fills the
    #    name column. ----------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            assert cur.fetchone()[0] == CURRENT_HEAD
    finally:
        conn.close()

    # A decision recorded by the token with username "jason" returns
    # decided_by_name "jason" with decided_by still the subject.
    drift_post = tokens[JASON_TOKEN].post(
        "/api/drift/decisions",
        json={"item_code": DRIFT_ITEM, "site_code": SITE, "decision": "accepted"},
    )
    assert drift_post.status_code == 201, drift_post.text
    drift_row = drift_post.json()
    assert drift_row["decided_by"] == JASON_SUB
    assert drift_row["decided_by_name"] == "jason"

    # A token with no username but an email returns the email.
    excess_post = tokens[EMAIL_TOKEN].post(
        "/api/excess/decisions",
        json={
            "item_code": EXCESS_ITEM,
            "site_code": SITE,
            "decision": "dismissed",
            "reason": "dead stock, write it off",
        },
    )
    assert excess_post.status_code == 201, excess_post.text
    excess_row = excess_post.json()
    assert excess_row["decided_by"] == EMAIL_SUB
    assert excess_row["decided_by_name"] == "email-only@corp.local"

    # A token with neither returns the subject.
    stockout_post = tokens[BARE_TOKEN].post(
        "/api/stockout/decisions",
        json={
            "item_code": STOCKOUT_ITEM,
            "site_code": SITE,
            "decision": "accepted",
            "action": "expedite",
        },
    )
    assert stockout_post.status_code == 201, stockout_post.text
    stockout_row = stockout_post.json()
    assert stockout_row["decided_by"] == BARE_SUB
    assert stockout_row["decided_by_name"] == BARE_SUB

    # The names are stored beside the subjects in the rows themselves.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT decided_by, decided_by_name FROM planner_decisions "
                "WHERE id = %s",
                (drift_row["id"],),
            )
            assert cur.fetchone() == (JASON_SUB, "jason")
            cur.execute(
                "SELECT decided_by, decided_by_name FROM excess_decisions "
                "WHERE id = %s",
                (excess_row["id"],),
            )
            assert cur.fetchone() == (EMAIL_SUB, "email-only@corp.local")
            cur.execute(
                "SELECT decided_by, decided_by_name FROM stockout_decisions "
                "WHERE id = %s",
                (stockout_row["id"],),
            )
            assert cur.fetchone() == (BARE_SUB, BARE_SUB)
    finally:
        conn.close()

    # -- AC-2: every response that carries decided_by also carries
    #    decided_by_name: the three GET .../decisions history lists. ---
    for url, item_code, expected_name in (
        ("/api/drift/decisions", DRIFT_ITEM, "jason"),
        ("/api/excess/decisions", EXCESS_ITEM, "email-only@corp.local"),
        ("/api/stockout/decisions", STOCKOUT_ITEM, BARE_SUB),
    ):
        resp = client.get(url, params={"item_code": item_code, "site_code": SITE})
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 1, (url, rows)
        assert rows[0]["decided_by_name"] == expected_name, (url, rows)

    # -- AC-3: a row written without a name comes back with a null
    #    name. -----------------------------------------------------------
    _insert_legacy_decision("id-pd-291-legacy")
    resp = client.get(
        "/api/drift/decisions",
        params={"item_code": LEGACY_ITEM, "site_code": SITE},
    )
    assert resp.status_code == 200, resp.text
    legacy_rows = resp.json()
    assert len(legacy_rows) == 1, legacy_rows
    assert legacy_rows[0]["decided_by"] == LEGACY_SUB
    assert legacy_rows[0]["decided_by_name"] is None

    # -- AC-2: GET/PUT/DELETE /api/settings entries carry
    #    updated_by_name beside updated_by; the change rows hold
    #    changed_by_name beside changed_by. ------------------------------
    # The admin (username "jason") stores the system setting.
    put = tokens[JASON_TOKEN].put(f"/api/settings/{SYS_KEY}", json={"value": False})
    assert put.status_code == 200, put.text
    put_entry = put.json()
    assert put_entry["value"] is False
    assert put_entry["source"] == "system"
    assert put_entry["updated_by"] == JASON_SUB
    assert put_entry["updated_by_name"] == "jason"
    assert put_entry["updated_at"] is not None

    # GET agrees with PUT.
    entries = _setting_entries(tokens[JASON_TOKEN])
    sys_entry = entries[SYS_KEY]
    assert sys_entry["updated_by"] == JASON_SUB
    assert sys_entry["updated_by_name"] == "jason"

    # The store's change row carries the same pair.
    changes = _change_rows()
    assert len(changes) == 1, changes
    assert changes[0]["key"] == SYS_KEY
    assert changes[0]["old_value"] is None
    assert changes[0]["new_value"] is False
    assert changes[0]["changed_by"] == JASON_SUB
    assert changes[0]["changed_by_name"] == "jason"

    # The admin resets it: the DELETE entry reports the default with a
    # null updated_by_name, and the reset's change row keeps the name.
    delete = tokens[JASON_TOKEN].delete(f"/api/settings/{SYS_KEY}")
    assert delete.status_code == 200, delete.text
    delete_entry = delete.json()
    assert delete_entry["value"] is True
    assert delete_entry["source"] == "default"
    assert delete_entry["updated_by"] is None
    assert delete_entry["updated_by_name"] is None
    changes = _change_rows()
    assert len(changes) == 2, changes
    assert changes[1]["key"] == SYS_KEY
    assert changes[1]["old_value"] is False
    assert changes[1]["new_value"] is None
    assert changes[1]["changed_by"] == JASON_SUB
    assert changes[1]["changed_by_name"] == "jason"

    # A stored setting written without a name (a pre-change row) comes
    # back in the settings list with a null updated_by_name.
    _insert_legacy_setting()
    entries = _setting_entries(tokens[BARE_TOKEN])
    usr_entry = entries[USR_KEY]
    assert usr_entry["value"] == "decided"
    assert usr_entry["source"] == "user"
    assert usr_entry["updated_by"] == BARE_SUB
    assert usr_entry["updated_by_name"] is None
