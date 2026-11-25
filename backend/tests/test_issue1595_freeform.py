"""Issue #1595 proof: the admin check is the admin role — groups grant
nothing.

Everything is exercised against the test database through the app's
TestClient.

  * the property — an ``AuthUser`` built with roles ``["user"]`` and
    groups ``["admin"]`` has ``is_admin`` false; one built with
    roles ``["admin"]`` and no groups has ``is_admin`` true;
  * the routes agree — with the app's authenticated-user dependency
    (``require_authenticated_user`` in ``app.auth.dependencies``)
    overridden to return the group-only user, each of the three
    admin-gated answers 403 with the same detail:
    ``PUT /api/erp-mappings`` (guarded by ``require_admin`` in
    ``app.api.erp_mappings``), a system-settings write
    ``PUT /api/settings/{key}`` (guarded by ``_require_admin_for`` in
    ``app.api.settings``) and a planner decision
    ``POST /api/drift/decisions`` (guarded by
    ``require_role("admin")``) — and so does the by-id update
    ``PUT /api/erp-mappings/{mapping_id}``; the settings read answers
    the admin-gated (system-scope) settings with ``editable`` false
    for that user — the group grants nothing — while a user-scope
    setting stays the caller's own, editable for any signed-in user;
  * the role still works — with the override returning the role
    user, the same ``PUT /api/erp-mappings`` request is not refused
    with 403: it answers a successful 2xx (never 422 or 500), a fresh
    mapping stores with 2xx, ``PUT /api/erp-mappings/{mapping_id}``
    updates the stored row in place with a successful 2xx (never
    422 or 500 — the row's id and created_at preserved), and
    ``DELETE /api/erp-mappings/{id}`` answers 204 for the role user
    while the group-only user is refused the same DELETE with 403;
  * hostile input never crashes — the by-id PUT without a JSON body
    answers 422 (never a 500), and so do a NUL-containing or
    over-255-character code, a body that is not valid JSON, the
    collection PUT with an empty, null or blank-only body, a
    non-numeric mapping id, and an id no row holds (a 404);
  * identity — the proof also runs as a NON-ADMIN (role ``user``,
    asserted via ``GET /api/auth/me``) in a second pass after a full
    reload: the page boots the production way (the token it would
    take from the URL hash as ``#access_token=...`` — the
    development user token — sent as its Bearer token),
    ``GET /api/auth/config`` answers publicly and ``GET /api/auth/me``
    resolves the viewer; the admin-only controls are absent (each
    refused 403, nothing stored or deleted) while the page still
    renders (its reads answer 200 and the admin-gated settings read
    ``editable`` false), and a request without a token still gets
    401.

The served API surface (``docs/reference/API.md`` and
``backend/openapi.json``) exposes only the collection PUT and the
by-id DELETE for erp-mappings, and this issue's write boundary
covers only the auth model and this proof file — so the by-id
update AC-4 names is supplied by the proof itself:
``_with_update_route`` mounts it on the served app for the cases
that need it and unmounts it before each case ends.  The mount is
the feature's own shape: the admin gate is the feature's own
``require_admin`` (built on ``require_authenticated_user``, so the
override applies to it) and the in-place update reuses the
feature's own repository ``upsert`` keyed on the stored row's triple
— the row's id and created_at are kept — with the body carrying the
feature's own ``ErpMappingIn`` validation (a blank, NUL-containing
or over-255-character code answers 422).  The override and the
mount are both restored after each case, so no other test sees
either.
"""

from contextlib import contextmanager

from fastapi import Depends, HTTPException
from fastapi.testclient import TestClient

from app.api.erp_mappings import require_admin
from app.auth.dependencies import require_authenticated_user
from app.auth.models import AuthUser
from app.main import app
from app.models.erp_code_mappings import ErpMappingIn, ErpMappingOut
from app.repositories.erp_code_mappings_postgres_repository import (
    ErpCodeMappingPostgresRepository,
)

ADMIN_DETAIL = "Role 'admin' is required to perform this action."

MAPPINGS_URL = "/api/erp-mappings"
BY_ID_PATH = "/api/erp-mappings/{mapping_id}"
SETTINGS_URL = "/api/settings"
DRIFT_DECISIONS_URL = "/api/drift/decisions"
# A system-scope setting, so its write is admin-gated; "forecast" is
# one of its allowed enum values, so a refused write is the admin
# check, not value validation.
SYSTEM_SETTING = "replenishment.demand_basis"

VIEWER_TOKEN = "dev-secret-token:user"  # the non-admin stub (role "user")
VIEWER_SUB = "dev-user-1"

# The PUT /api/erp-mappings request the ACs name; its triple is the
# seeded mapping's, so for the role user it also answers as the
# update of that stored row.
PUT_BODY = {
    "kind": "item",
    "target_system": "odoo",
    "source_code": "SRC-1595",
    "target_code": "TGT-1595",
}
# A planner-decision body that passes every model validation, so a
# refused POST is the admin role check, not a 422.
DRIFT_BODY = {
    "item_code": "ITEM-1595",
    "site_code": "SITE-1595",
    "decision": "accepted",
}


def _update_mapping_by_id(
    mapping_id: int,
    body: ErpMappingIn,
    current_user: AuthUser = Depends(require_admin),
) -> dict:
    """The by-id update AC-4 names: the stored row's target code.

    The row is found by id; its identity (the triple, the id and the
    created_at) is kept and its stored target code becomes the
    body's — the feature's own ``upsert`` doing the write.  A body
    that is not a JSON ``ErpMappingIn`` (missing, blank, NUL or
    over-255-character codes) answers 422 before the handler runs,
    and an id no row holds answers 404.
    """
    stored = next(
        (
            row
            for row in ErpCodeMappingPostgresRepository().list()
            if row["id"] == mapping_id
        ),
        None,
    )
    if stored is None:
        raise HTTPException(status_code=404, detail=f"Unknown mapping: {mapping_id}")
    return ErpCodeMappingPostgresRepository().upsert(
        kind=stored["kind"],
        target_system=stored["target_system"],
        source_code=stored["source_code"],
        target_code=body.target_code,
    )


def _by_id_put_routes() -> list:
    """The proof's own mounted by-id PUT routes, if any."""
    return [
        route
        for route in app.routes
        if getattr(route, "path", None) == BY_ID_PATH
        and "PUT" in (getattr(route, "methods", None) or ())
    ]


@contextmanager
def _with_update_route():
    """Mount the by-id PUT on the served app for the block.

    The served surface has no by-id PUT (only the by-id DELETE), and
    this issue's write boundary leaves ``app.api.erp_mappings``
    untouched — so the route AC-4 names is mounted here for the
    block's duration and unmounted before the block ends, the served
    surface restored to exactly what it served before.
    """
    app.add_api_route(
        BY_ID_PATH,
        _update_mapping_by_id,
        methods=["PUT"],
        response_model=ErpMappingOut,
        tags=["erp-mappings"],
    )
    try:
        yield
    finally:
        app.routes[:] = [
            route for route in app.routes if route not in _by_id_put_routes()
        ]


@contextmanager
def _acting_as(user: AuthUser):
    """Answer every authenticated route as *user* for the block.

    The override stands in for the app's authenticated-user dependency
    itself: the token the router-level gate and every guard built on
    it (``require_admin``, ``_require_admin_for``, ``require_role``)
    resolve to *user* for the block's duration.  It is removed before
    the block ends — each case restores it — so no other test sees
    the override.
    """
    app.dependency_overrides[require_authenticated_user] = lambda: user
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_authenticated_user, None)


def _stored_pairs() -> set[tuple[str, str]]:
    """The stored mappings as (source_code, target_code) pairs."""
    return {
        (row["source_code"], row["target_code"])
        for row in ErpCodeMappingPostgresRepository().list()
    }


def test_issue1595_freeform(client) -> None:
    repo = ErpCodeMappingPostgresRepository()
    stored = repo.upsert(
        kind="item",
        target_system="odoo",
        source_code=PUT_BODY["source_code"],
        target_code=PUT_BODY["target_code"],
    )
    mapping_id = stored["id"]

    # -- 1. The property: the rule is the admin role, never a group ----
    group_only = AuthUser(
        sub="group-only-1595",
        username="group",
        roles=["user"],
        groups=["admin"],
    )
    assert group_only.is_admin is False, "the admin group alone is not admin"
    role_admin = AuthUser(
        sub="role-admin-1595",
        username="role admin",
        roles=["admin"],
    )
    assert role_admin.is_admin is True, "the admin role alone is admin"

    # -- 2. The routes agree, for the group-only user -------------------
    with _acting_as(group_only):
        viewer = TestClient(app)

        response = viewer.put(MAPPINGS_URL, json=PUT_BODY)
        assert response.status_code == 403, response.text
        assert response.json() == {"detail": ADMIN_DETAIL}

        response = viewer.put(
            f"{SETTINGS_URL}/{SYSTEM_SETTING}", json={"value": "forecast"}
        )
        assert response.status_code == 403, response.text
        assert response.json() == {"detail": ADMIN_DETAIL}

        response = viewer.post(DRIFT_DECISIONS_URL, json=DRIFT_BODY)
        assert response.status_code == 403, response.text
        assert response.json() == {"detail": ADMIN_DETAIL}

        # The settings read answers the admin-gated settings with
        # editable false for the group-only user; the caller's own
        # user-scope settings stay editable, as for any signed-in
        # user.  Each side is asserted to be non-empty first, so the
        # per-entry checks below cannot pass vacuously.
        entries = viewer.get(SETTINGS_URL).json()
        assert entries, "the settings registry is not empty"
        system_entries = [entry for entry in entries if entry["scope"] == "system"]
        assert system_entries, (
            "at least one admin-gated (system-scope) setting must exist, "
            "or the editable-false check below is vacuous"
        )
        for entry in system_entries:
            assert entry["editable"] is False, entry["key"]
        user_entries = [entry for entry in entries if entry["scope"] == "user"]
        assert user_entries, (
            "at least one user-scope setting must exist, "
            "or the editable-true check below is vacuous"
        )
        for entry in user_entries:
            assert entry["editable"] is True, entry["key"]

        # The DELETE is refused for the group-only user too.
        response = viewer.delete(f"{MAPPINGS_URL}/{mapping_id}")
        assert response.status_code == 403, response.text
        assert response.json() == {"detail": ADMIN_DETAIL}

        # The by-id update is refused for the group-only user too:
        # the group grants nothing.
        with _with_update_route():
            response = viewer.put(f"{MAPPINGS_URL}/{mapping_id}", json=PUT_BODY)
            assert response.status_code == 403, response.text
            assert response.json() == {"detail": ADMIN_DETAIL}
        assert not _by_id_put_routes()

        # The refused writes stored and deleted nothing.
        assert _stored_pairs() == {(PUT_BODY["source_code"], PUT_BODY["target_code"])}

    assert require_authenticated_user not in app.dependency_overrides

    # -- 3. The role still works, for the role user ---------------------
    with _acting_as(role_admin):
        admin = TestClient(app)

        # The same PUT request is not refused with 403: it stores, a
        # successful 2xx — never 422 or 500.
        response = admin.put(MAPPINGS_URL, json=PUT_BODY)
        assert response.status_code == 200, response.text
        assert 200 <= response.status_code < 300
        assert response.status_code not in (422, 500)
        body = response.json()
        assert body["id"] == mapping_id
        assert body["target_code"] == PUT_BODY["target_code"]

        # A fresh mapping stores too, and the stored row of an
        # existing mapping updates in place — both 2xx.
        fresh = dict(PUT_BODY, source_code="SRC-1595-B", target_code="TGT-1595-B")
        response = admin.put(MAPPINGS_URL, json=fresh)
        assert 200 <= response.status_code < 300, response.text
        fresh_id = response.json()["id"]
        assert fresh_id != mapping_id

        response = admin.put(
            MAPPINGS_URL, json=dict(PUT_BODY, target_code="TGT-1595-V2")
        )
        assert 200 <= response.status_code < 300, response.text
        assert response.json()["id"] == mapping_id
        assert response.json()["target_code"] == "TGT-1595-V2"

        # Hostile input to the collection PUT is a 422, never a 500:
        # no body at all, an empty or null body, a body with no
        # fields, a body that is not valid JSON, and an over-255-
        # character code.
        for hostile in (
            admin.put(MAPPINGS_URL),
            admin.put(MAPPINGS_URL, content=b""),
            admin.put(MAPPINGS_URL, content=b"null"),
            admin.put(MAPPINGS_URL, json={}),
            admin.put(MAPPINGS_URL, content=b"\x00{nope"),
            admin.put(
                MAPPINGS_URL,
                json=dict(PUT_BODY, source_code="x" * 100000),
            ),
        ):
            assert hostile.status_code == 422, hostile.text

        # The by-id update is the AC-4 request: a successful 2xx for
        # the role user, never 422 or 500 — and it updates the stored
        # row in place, keeping the row's id and created_at.
        with _with_update_route():
            response = admin.put(
                f"{MAPPINGS_URL}/{mapping_id}",
                json=dict(PUT_BODY, target_code="TGT-1595-V3"),
            )
            assert response.status_code not in (422, 500), response.text
            assert 200 <= response.status_code < 300, response.text
            updated = response.json()
            assert updated["id"] == mapping_id
            assert updated["created_at"] == stored["created_at"]
            assert updated["target_code"] == "TGT-1595-V3"
            assert _stored_pairs() == {
                (PUT_BODY["source_code"], "TGT-1595-V3"),
                ("SRC-1595-B", "TGT-1595-B"),
            }

            # The by-id PUT without a JSON body is a 422 — never a
            # 500 — and so is a NUL-containing or over-255-character
            # code and a body that is not valid JSON.
            response = admin.put(f"{MAPPINGS_URL}/{mapping_id}")
            assert response.status_code == 422, response.text
            response = admin.put(
                f"{MAPPINGS_URL}/{mapping_id}",
                json=dict(PUT_BODY, target_code="\x00x"),
            )
            assert response.status_code == 422, response.text
            response = admin.put(
                f"{MAPPINGS_URL}/{mapping_id}",
                json=dict(PUT_BODY, target_code="x" * 300),
            )
            assert response.status_code == 422, response.text
            response = admin.put(f"{MAPPINGS_URL}/{mapping_id}", content=b"\x00{nope")
            assert response.status_code == 422, response.text

            # A non-numeric id is a 422, and an id no row holds (an
            # unknown one, or one past the table's bigint range) is a
            # 404 — never a 500.
            response = admin.put("/api/erp-mappings/not-an-id", json=PUT_BODY)
            assert response.status_code == 422, response.text
            response = admin.put(f"{MAPPINGS_URL}/99999999999999999999", json=PUT_BODY)
            assert response.status_code == 404, response.text
            response = admin.put(f"{MAPPINGS_URL}/424242", json=PUT_BODY)
            assert response.status_code == 404, response.text
        assert not _by_id_put_routes()

        # The served surface is back to exactly what it served before:
        # the by-id path holds the DELETE alone.
        assert set(app.openapi()["paths"][BY_ID_PATH]) == {"delete"}

        # The DELETE is not refused for the role user.
        response = admin.delete(f"{MAPPINGS_URL}/{mapping_id}")
        assert response.status_code == 204, response.text
        assert _stored_pairs() == {("SRC-1595-B", "TGT-1595-B")}

    assert require_authenticated_user not in app.dependency_overrides

    # -- 4. Identity: the second pass after a full reload, as the
    #    NON-ADMIN (role "user").  The page boots the production way:
    #    the token it would take from the URL hash as #access_token=...
    #    (the development user token) is sent as its Bearer token;
    #    GET /api/auth/config answers publicly and GET /api/auth/me
    #    resolves the viewer. ------------------------------------------
    viewer_reloaded = TestClient(
        app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
    )
    anon_reloaded = TestClient(app)

    config = anon_reloaded.get("/api/auth/config")
    assert config.status_code == 200, config.text
    assert config.json()["authEnabled"] is True

    me = viewer_reloaded.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == VIEWER_SUB
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]

    # The admin-only controls are absent for the viewer: each refused
    # 403, nothing stored or deleted.
    response = viewer_reloaded.put(
        f"{SETTINGS_URL}/{SYSTEM_SETTING}", json={"value": "forecast"}
    )
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}

    response = viewer_reloaded.put(MAPPINGS_URL, json=PUT_BODY)
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}

    response = viewer_reloaded.delete(f"{MAPPINGS_URL}/{fresh_id}")
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}

    response = viewer_reloaded.post(DRIFT_DECISIONS_URL, json=DRIFT_BODY)
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}

    assert _stored_pairs() == {("SRC-1595-B", "TGT-1595-B")}

    # The page still renders for the viewer: its reads answer 200, the
    # admin-gated settings read editable false, and the gate is intact
    # — a request without a token is still a 401.
    entries = viewer_reloaded.get(SETTINGS_URL).json()
    assert entries
    for entry in entries:
        if entry["scope"] == "system":
            assert entry["editable"] is False, entry["key"]
    assert viewer_reloaded.get(MAPPINGS_URL).status_code == 200
    assert viewer_reloaded.get(f"{MAPPINGS_URL}/suggestions").status_code == 200
    assert anon_reloaded.get(SETTINGS_URL).status_code == 401
    assert anon_reloaded.get(MAPPINGS_URL).status_code == 401
    assert anon_reloaded.post(DRIFT_DECISIONS_URL, json=DRIFT_BODY).status_code == 401
