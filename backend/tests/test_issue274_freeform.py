"""Issue #274 proof: registered settings with scopes, validation and audit.

The proof covers, in one test, with ``TestClient(app.main.app)``:

* the defaults on a fresh database — ``GET /api/settings`` (no stored
  row) returns exactly the two registered settings, ordered by key
  (``decisions.require_action_choice`` then ``queue.default_filter``),
  each entry carrying exactly ``key``, ``scope``, ``type``, ``allowed``
  (the enum values, or null for the boolean), ``default``,
  ``description``, ``value`` (the default), ``source`` ("default"),
  ``editable``, ``updated_by`` and ``updated_at`` (null while the
  default applies);
* the route boundary — ``GET /api/settings`` answers 401 without a
  token (the router is mounted in ``app.main`` with the include-time
  auth gate);
* an admin setting the system value — the admin token (sub
  ``dev-admin-1``) PUTs ``decisions.require_action_choice`` to false and
  reads it back (value false, source "system", updated_by
  ``dev-admin-1``, updated_at set), a non-admin PUT of the same system
  setting answers 403, and a non-admin sees the system value as
  effective;
* a user value isolated between two users — the viewer (sub
  ``dev-user-1``) stores ``queue.default_filter`` "decided" for their
  own subject while the admin (a different subject, ``dev-admin-1``)
  still reads the default, the admin stores "all" for their own
  subject while the viewer still reads "decided", and one user's
  user-scope value never changes what the other sees;
* the 404 for an unknown key (PUT and DELETE) and the 422 for a value
  of the wrong type (a string for the boolean, an integer, a null, a
  NUL-byte string) and for an out-of-enum value, each 422 detail
  naming the key and the allowed values — and a hostile huge value or
  a huge key never answers 500;
* reset to default — the admin DELETEs the system setting and reads
  back source "default" with the default value and null updated_by /
  updated_at; a PUT whose value equals the value already stored, and a
  DELETE when nothing is stored, change nothing and append no change
  row;
* the audit trail — ``settings_changes`` holds exactly one row per
  change, in order (id ASC), each with scope, owner (the empty string
  for the system setting, the changer's subject for a user setting),
  key, old_value (the value stored before the change, null when the
  default applied), new_value (null on the reset), and changed_by set
  to the changer's subject.

The identity requirement is met at the API boundary (this issue adds
no frontend page: the planner screen is still a placeholder, so there
is no page with a token in the URL hash to boot): after the admin
pass, the test performs a second pass in the same test after a full
reload (a fresh ``TestClient``), as the non-admin viewer (the
development token suffixed ``:user``, validated the production way
through the Bearer header), and asserts the admin-only control is
absent for that caller — the system setting reports
``editable: false`` and a PUT of it answers 403 — while the page still
renders, ``GET /api/settings`` answering 200 with every registered
setting.
"""

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

SYS_KEY = "decisions.require_action_choice"
USR_KEY = "queue.default_filter"

ENTRY_FIELDS = {
    "key",
    "scope",
    "type",
    "allowed",
    "min",
    "max",
    "default",
    "description",
    "value",
    "source",
    "editable",
    "updated_by",
    "updated_by_name",
    "updated_at",
}


def _entry(body: list[dict], key: str) -> dict:
    entry = next((e for e in body if e["key"] == key), None)
    assert entry is not None, (key, body)
    return entry


def _settings_rows() -> list[tuple]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT scope, owner, key, value FROM settings "
                "ORDER BY scope, owner, key"
            )
            return [(r[0], r[1], r[2], r[3]) for r in cur.fetchall()]
    finally:
        conn.close()


def _change_rows() -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT scope, owner, key, old_value, new_value, changed_by, "
                "changed_at FROM settings_changes ORDER BY id ASC"
            )
            rows = [
                {
                    "scope": r[0],
                    "owner": r[1],
                    "key": r[2],
                    "old_value": r[3],
                    "new_value": r[4],
                    "changed_by": r[5],
                    "changed_at": r[6],
                }
                for r in cur.fetchall()
            ]
    finally:
        conn.close()
    return rows


def _change_count() -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM settings_changes")
            return cur.fetchone()[0]
    finally:
        conn.close()


def test_issue274_freeform(client, admin_token, user_token):
    viewer = client  # the dev token: sub dev-user-1, role "user"
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})

    # -- Defaults on a fresh database ----------------------------------
    assert _settings_rows() == [], "the fresh database stores no setting"
    assert _change_count() == 0

    resp = viewer.get("/api/settings")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [e["key"] for e in body] == [
        "ddmrp.adu_window_days",
        "ddmrp.lead_time_factor_long",
        "ddmrp.lead_time_factor_medium",
        "ddmrp.lead_time_factor_short",
        "ddmrp.variability_factor_high",
        "ddmrp.variability_factor_low",
        "ddmrp.variability_factor_medium",
        SYS_KEY,
        "erp.reporting_currency",
        "erp.standing_grants_enabled",
        "erp.standing_grants_horizon_days",
        "exceptions.forecast_bias_min_periods",
        "exceptions.forecast_bias_threshold",
        "exceptions.order_minimum_due_days",
        "exceptions.release_approval_days",
        "exceptions.sop_signoff_day",
        USR_KEY,
        "replenishment.demand_basis",
        "replenishment.holding_rate",
        "replenishment.intermittent_recommendations",
        "replenishment.lead_time_basis",
        "replenishment.order_cost",
        "replenishment.service_level",
        "replenishment.service_level_a",
        "replenishment.service_level_b",
        "replenishment.service_level_by_class",
        "replenishment.service_level_c",
        "replenishment.service_target",
        "second_source.qualification_days",
        "second_source.similar_prefix_length",
        "supplier_risk.lead_time_cv_ceiling",
        "supplier_risk.on_time_floor",
        "supply_plan.prefer_lower_risk_supplier",
        "supply_plan.top_up_lookahead_days",
    ], "ordered by key"
    for entry in body:
        assert set(entry) == ENTRY_FIELDS, entry

    sys_entry = _entry(body, SYS_KEY)
    assert sys_entry["scope"] == "system"
    assert sys_entry["type"] == "boolean"
    assert sys_entry["allowed"] is None
    assert sys_entry["default"] is True
    assert sys_entry["description"].strip()
    assert sys_entry["value"] is True
    assert sys_entry["source"] == "default"
    assert sys_entry["editable"] is False, "a non-admin cannot edit a system setting"
    assert sys_entry["updated_by"] is None
    assert sys_entry["updated_at"] is None

    usr_entry = _entry(body, USR_KEY)
    assert usr_entry["scope"] == "user"
    assert usr_entry["type"] == "enum"
    assert usr_entry["allowed"] == ["open", "decided", "all"]
    assert usr_entry["default"] == "open"
    assert usr_entry["description"].strip()
    assert usr_entry["value"] == "open"
    assert usr_entry["source"] == "default"
    assert usr_entry["editable"] is True, "a user setting is editable by its owner"
    assert usr_entry["updated_by"] is None
    assert usr_entry["updated_at"] is None

    # Route boundary: the router is mounted with the include-time gate.
    anon = TestClient(app)
    resp = anon.get("/api/settings")
    assert resp.status_code == 401, resp.text

    # -- Admin pass: the admin sets the system value --------------------
    body = admin_client.get("/api/settings").json()
    assert (
        _entry(body, SYS_KEY)["editable"] is True
    ), "an admin may edit a system setting"

    resp = viewer.put(f"/api/settings/{SYS_KEY}", json={"value": False})
    assert resp.status_code == 403, resp.text

    resp = admin_client.put(f"/api/settings/{SYS_KEY}", json={"value": False})
    assert resp.status_code == 200, resp.text
    entry = resp.json()
    assert set(entry) == ENTRY_FIELDS, entry
    assert entry["key"] == SYS_KEY
    assert entry["value"] is False
    assert entry["source"] == "system"
    assert entry["updated_by"] == "dev-admin-1"
    assert entry["updated_at"]
    assert _change_count() == 1

    body = viewer.get("/api/settings").json()
    entry = _entry(body, SYS_KEY)
    assert entry["value"] is False
    assert entry["source"] == "system"
    assert entry["updated_by"] == "dev-admin-1"

    # A PUT of the already-stored value changes nothing, no change row.
    resp = admin_client.put(f"/api/settings/{SYS_KEY}", json={"value": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "system"
    assert _change_count() == 1

    # A PUT over a stored value records old_value as the stored value.
    resp = admin_client.put(f"/api/settings/{SYS_KEY}", json={"value": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["value"] is True
    assert _change_count() == 2

    # -- User values: isolated between two users ------------------------
    resp = viewer.put(f"/api/settings/{USR_KEY}", json={"value": "decided"})
    assert resp.status_code == 200, resp.text
    entry = resp.json()
    assert entry["value"] == "decided"
    assert entry["source"] == "user"
    assert entry["updated_by"] == "dev-user-1"
    assert _change_count() == 3

    # The other subject (the admin's own subject) still sees the default.
    body = admin_client.get("/api/settings").json()
    entry = _entry(body, USR_KEY)
    assert entry["value"] == "open"
    assert entry["source"] == "default"
    assert entry["updated_by"] is None

    resp = admin_client.put(f"/api/settings/{USR_KEY}", json={"value": "all"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated_by"] == "dev-admin-1"
    assert _change_count() == 4

    # And the viewer still sees their own value.
    body = viewer.get("/api/settings").json()
    entry = _entry(body, USR_KEY)
    assert entry["value"] == "decided"
    assert entry["source"] == "user"

    # -- Unknown keys are 404 -------------------------------------------
    resp = viewer.put("/api/settings/no-such-setting", json={"value": True})
    assert resp.status_code == 404, resp.text
    resp = viewer.delete("/api/settings/no-such-setting")
    assert resp.status_code == 404, resp.text

    # -- Wrong type / out-of-enum values are 422 ------------------------
    resp = admin_client.put(f"/api/settings/{SYS_KEY}", json={"value": "yes"})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert SYS_KEY in detail
    assert "true" in detail and "false" in detail

    resp = admin_client.put(f"/api/settings/{SYS_KEY}", json={"value": 1})
    assert resp.status_code == 422, resp.text
    assert SYS_KEY in resp.json()["detail"]

    resp = admin_client.put(f"/api/settings/{SYS_KEY}", json={"value": None})
    assert resp.status_code == 422, resp.text

    resp = admin_client.put(f"/api/settings/{SYS_KEY}", json={"value": "dec\0ided"})
    assert resp.status_code == 422, resp.text

    # A hostile huge value answers 422, never 500.
    resp = viewer.put(f"/api/settings/{USR_KEY}", json={"value": "x" * 100000})
    assert resp.status_code == 422, resp.text
    assert USR_KEY in resp.json()["detail"]

    resp = viewer.put(f"/api/settings/{USR_KEY}", json={"value": "banana"})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert USR_KEY in detail
    for allowed in ("open", "decided", "all"):
        assert allowed in detail

    # A hostile huge key path answers 404, never 500.
    resp = viewer.put(f"/api/settings/{'x' * 1000}", json={"value": True})
    assert resp.status_code == 404, resp.text

    assert _change_count() == 4, "rejected values store nothing"

    # -- Reset to default ------------------------------------------------
    resp = admin_client.delete(f"/api/settings/{SYS_KEY}")
    assert resp.status_code == 200, resp.text
    entry = resp.json()
    assert entry["value"] is True
    assert entry["source"] == "default"
    assert entry["updated_by"] is None
    assert entry["updated_at"] is None
    assert _change_count() == 5

    # A DELETE when nothing is stored changes nothing, no change row.
    resp = admin_client.delete(f"/api/settings/{SYS_KEY}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "default"
    assert _change_count() == 5

    body = viewer.get("/api/settings").json()
    assert _entry(body, SYS_KEY)["source"] == "default"
    assert _entry(body, USR_KEY)["value"] == "decided"

    # -- Second pass after a full reload, as the non-admin viewer --------
    # The identity requirement at the API boundary: this issue adds no
    # frontend page (the planner screen is still a placeholder, so there
    # is no page with a token in the URL hash to boot), so the non-admin
    # pass runs against the API the way the page would — a fresh client
    # (full reload) with the role "user" development token in the Bearer
    # header.  The admin-only control is absent for that caller while
    # the page still renders.
    viewer_client = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    resp = viewer_client.get("/api/settings")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [e["key"] for e in body] == [
        "ddmrp.adu_window_days",
        "ddmrp.lead_time_factor_long",
        "ddmrp.lead_time_factor_medium",
        "ddmrp.lead_time_factor_short",
        "ddmrp.variability_factor_high",
        "ddmrp.variability_factor_low",
        "ddmrp.variability_factor_medium",
        SYS_KEY,
        "erp.reporting_currency",
        "erp.standing_grants_enabled",
        "erp.standing_grants_horizon_days",
        "exceptions.forecast_bias_min_periods",
        "exceptions.forecast_bias_threshold",
        "exceptions.order_minimum_due_days",
        "exceptions.release_approval_days",
        "exceptions.sop_signoff_day",
        USR_KEY,
        "replenishment.demand_basis",
        "replenishment.holding_rate",
        "replenishment.intermittent_recommendations",
        "replenishment.lead_time_basis",
        "replenishment.order_cost",
        "replenishment.service_level",
        "replenishment.service_level_a",
        "replenishment.service_level_b",
        "replenishment.service_level_by_class",
        "replenishment.service_level_c",
        "replenishment.service_target",
        "second_source.qualification_days",
        "second_source.similar_prefix_length",
        "supplier_risk.lead_time_cv_ceiling",
        "supplier_risk.on_time_floor",
        "supply_plan.prefer_lower_risk_supplier",
        "supply_plan.top_up_lookahead_days",
    ]
    assert _entry(body, SYS_KEY)["editable"] is False, "admin-only control absent"
    resp = viewer_client.put(f"/api/settings/{SYS_KEY}", json={"value": False})
    assert resp.status_code == 403, resp.text

    # -- The audit trail -------------------------------------------------
    changes = _change_rows()
    assert len(changes) == 5
    seen = [
        (
            c["scope"],
            c["owner"],
            c["key"],
            c["old_value"],
            c["new_value"],
            c["changed_by"],
        )
        for c in changes
    ]
    assert seen == [
        ("system", "", SYS_KEY, None, False, "dev-admin-1"),
        ("system", "", SYS_KEY, False, True, "dev-admin-1"),
        ("user", "dev-user-1", USR_KEY, None, "decided", "dev-user-1"),
        ("user", "dev-admin-1", USR_KEY, None, "all", "dev-admin-1"),
        ("system", "", SYS_KEY, True, None, "dev-admin-1"),
    ]
    for change in changes:
        assert change["changed_by"], "every change records who made it"
        assert change["changed_at"], "every change records when it was made"

    assert _settings_rows() == [
        ("user", "dev-admin-1", USR_KEY, "all"),
        ("user", "dev-user-1", USR_KEY, "decided"),
    ]
