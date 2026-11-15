"""Issue #227 proof: the public auth config endpoint and the caller's identity.

The proof covers, in one test, with ``TestClient(app.main.app)``:

* the public config route — ``GET /api/auth/config`` answers 200 without
  any token (which also proves the router is registered in ``app.main``
  WITHOUT the include-time ``auth_gate``, since the gate would answer
  401), with exactly the seven fields ``authEnabled``, ``authMode``,
  ``issuer``, ``clientId``, ``audience``, ``jwksUrl`` and
  ``algorithms``, each carrying the corresponding value from
  ``app.auth.config`` (``AUTH_ENABLED``, ``AUTH_MODE``, ``AUTH_ISSUER``,
  ``AUTH_CLIENT_ID``, ``AUTH_AUDIENCE``, ``AUTH_JWKS_URL`` and
  ``AUTH_ALGORITHMS``);
* the config values are read from ``app.auth.config`` at request time,
  so rebinding a module attribute (``AUTH_ISSUER``, ``AUTH_ENABLED``) is
  served on the next request;
* the config body never includes a secret, in any mode — with
  ``AUTH_DEV_TOKEN`` set to a known value and ``AUTH_DEV_ROLES`` to
  known roles, neither value appears anywhere in the response body,
  neither in development mode nor in production mode, and the body
  still carries exactly the seven fields;
* the identity route — ``GET /api/auth/me`` depends on
  ``require_authenticated_user``: it answers 401 with the gate's
  detail and ``WWW-Authenticate: Bearer`` header without a valid
  token; with the admin token fixture it answers 200 with
  ``authenticated`` true and ``admin`` among the user's normalised
  roles (groups normalised too); with the user token fixture it
  answers 200 with ``admin`` among neither the roles nor the groups;
* nothing changes data — the table row counts are unchanged by every
  request above;
* the identity requirement — a role "user" caller (the development
  token suffixed ``:user``, validated the production way through the
  Bearer header).

The identity requirement is met at the API boundary (this issue adds
no frontend page: the planner screen is still a placeholder, so there
is no page with a token in the URL hash to boot): after the admin
pass, the test performs a second pass in the same test after a full
reload (a fresh ``TestClient``), as the non-admin viewer, and asserts
the admin-only control is absent for that caller while the page still
renders — ``GET /api/auth/config`` answers 200 with the seven fields
and ``GET /api/auth/me`` answers 200 with the viewer's own identity,
whose roles and groups carry no ``admin``.
"""

import psycopg2.sql
from fastapi.testclient import TestClient

from app.auth import config as auth_config
from app.auth.dependencies import require_authenticated_user
from app.db.connection import get_connection
from app.main import app

CONFIG_FIELDS = {
    "authEnabled",
    "authMode",
    "issuer",
    "clientId",
    "audience",
    "jwksUrl",
    "algorithms",
}

SENTINEL_DEV_TOKEN = "issue227-super-secret-dev-token"
SENTINEL_DEV_ROLES = ["issue227-secret-role"]


def _effective_dependants(application, target_path):
    """Yield the effective dependant of every route serving target_path.

    Mirrors the layout handling used elsewhere in the suite so the
    check runs on the routes the served app actually dispatches.
    """

    def walk(node):
        if type(node).__name__ == "_IncludedRouter":
            for candidate in node.effective_candidates():
                yield from walk(candidate)
            return
        if (
            getattr(node, "path", None) == target_path
            and getattr(node, "dependant", None) is not None
        ):
            yield node.dependant

    for route in application.routes:
        yield from walk(route)


def _table_counts() -> dict[str, int]:
    """Row count of every public table (excluding alembic_version)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
            tables = [row[0] for row in cur.fetchall()]
            counts = {}
            for table in tables:
                cur.execute(
                    psycopg2.sql.SQL("SELECT count(*) FROM {}").format(
                        psycopg2.sql.Identifier(table)
                    )
                )
                counts[table] = cur.fetchone()[0]
            return counts
    finally:
        conn.close()


def test_issue227_freeform(admin_token, user_token, monkeypatch):
    before = _table_counts()
    anon = TestClient(app)

    # -- GET /api/auth/config without a token: 200, exactly the seven
    #    fields, each read from app.auth.config. The 200 without any
    #    token also proves the router is mounted in app.main WITHOUT
    #    the include-time auth_gate (the gate would answer 401). -----
    config_resp = anon.get("/api/auth/config")
    assert config_resp.status_code == 200, config_resp.text
    body = config_resp.json()
    assert set(body) == CONFIG_FIELDS
    assert body["authEnabled"] is auth_config.AUTH_ENABLED
    assert body["authMode"] == auth_config.AUTH_MODE
    assert body["issuer"] == auth_config.AUTH_ISSUER
    assert body["clientId"] == auth_config.AUTH_CLIENT_ID
    assert body["audience"] == auth_config.AUTH_AUDIENCE
    assert body["jwksUrl"] == auth_config.AUTH_JWKS_URL
    assert body["algorithms"] == auth_config.AUTH_ALGORITHMS

    # -- Registration: the router is mounted in app.main WITHOUT the
    #    include-time auth_gate (config stays public) and /me protects
    #    itself with require_authenticated_user exactly once. ---------
    for dependant in _effective_dependants(app, "/api/auth/config"):
        assert not [
            sub
            for sub in dependant.dependencies
            if sub.call is require_authenticated_user
        ], "GET /api/auth/config must stay public"
    for dependant in _effective_dependants(app, "/api/auth/me"):
        assert [
            sub
            for sub in dependant.dependencies
            if sub.call is require_authenticated_user
        ], "GET /api/auth/me must depend on require_authenticated_user"

    # -- GET /api/auth/me without a token: 401, fails closed. ---------
    me_anon = anon.get("/api/auth/me")
    assert me_anon.status_code == 401, me_anon.text
    assert me_anon.json()["detail"] == (
        "Authentication required. Provide a valid Bearer token."
    )
    assert me_anon.headers.get("www-authenticate") == "Bearer"

    # -- A malformed Authorization header is 401 too. ------------------
    me_malformed = anon.get(
        "/api/auth/me", headers={"Authorization": "Token dev-secret-token"}
    )
    assert me_malformed.status_code == 401

    # -- With the admin token fixture: 200, authenticated, admin among
    #    the normalised roles (groups normalised too). ----------------
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    me_admin = admin_client.get("/api/auth/me")
    assert me_admin.status_code == 200, me_admin.text
    admin_body = me_admin.json()
    assert admin_body["authenticated"] is True
    assert "admin" in admin_body["user"]["roles"]
    assert admin_body["user"] == {
        "sub": "dev-admin-1",
        "username": "admin",
        "email": "admin@aicrm.local",
        "roles": ["admin", "user"],
        "groups": [],
        "raw_claims": {"mode": "development"},
    }

    # -- With the user token fixture: 200, authenticated, no admin. ----
    user_client = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    me_user = user_client.get("/api/auth/me")
    assert me_user.status_code == 200, me_user.text
    user_body = me_user.json()
    assert user_body["authenticated"] is True
    assert "admin" not in user_body["user"]["roles"]
    assert "admin" not in user_body["user"]["groups"]
    assert user_body["user"] == {
        "sub": "dev-user-1",
        "username": "developer",
        "email": "developer@aicrm.local",
        "roles": ["user"],
        "groups": [],
        "raw_claims": {"mode": "development"},
    }

    # -- The config body never includes a secret, in any mode. --------
    original_dev_token = auth_config.AUTH_DEV_TOKEN
    original_dev_roles = auth_config.AUTH_DEV_ROLES
    original_mode = auth_config.AUTH_MODE
    monkeypatch.setattr(auth_config, "AUTH_DEV_TOKEN", SENTINEL_DEV_TOKEN)
    monkeypatch.setattr(auth_config, "AUTH_DEV_ROLES", SENTINEL_DEV_ROLES)

    secret_dev = anon.get("/api/auth/config")
    assert secret_dev.status_code == 200, secret_dev.text
    assert set(secret_dev.json()) == CONFIG_FIELDS
    assert SENTINEL_DEV_TOKEN not in secret_dev.text
    for role in SENTINEL_DEV_ROLES:
        assert role not in secret_dev.text

    monkeypatch.setattr(auth_config, "AUTH_MODE", "production")
    secret_prod = anon.get("/api/auth/config")
    assert secret_prod.status_code == 200, secret_prod.text
    prod_body = secret_prod.json()
    assert set(prod_body) == CONFIG_FIELDS
    assert prod_body["authMode"] == "production"
    assert SENTINEL_DEV_TOKEN not in secret_prod.text
    for role in SENTINEL_DEV_ROLES:
        assert role not in secret_prod.text
    # Restore the configuration so the identity fixtures stay valid.
    monkeypatch.setattr(auth_config, "AUTH_DEV_TOKEN", original_dev_token)
    monkeypatch.setattr(auth_config, "AUTH_DEV_ROLES", original_dev_roles)
    monkeypatch.setattr(auth_config, "AUTH_MODE", original_mode)

    # -- The values are read from app.auth.config at request time. ----
    monkeypatch.setattr(
        auth_config, "AUTH_ISSUER", "https://issuer.example/realms/issue227"
    )
    monkeypatch.setattr(auth_config, "AUTH_ENABLED", False)
    dynamic = anon.get("/api/auth/config").json()
    assert dynamic["issuer"] == "https://issuer.example/realms/issue227"
    assert dynamic["authEnabled"] is False

    # -- Nothing changes data: the endpoints read only. ----------------
    assert before == _table_counts()

    # -- Identity, viewer pass: a fresh TestClient (a full reload) as
    #    the role "user" caller. The admin-only control is absent for
    #    that caller while the page still renders: the public config
    #    answers 200 with the seven fields and the identity answers 200
    #    with the viewer's own identity — no admin in roles or groups.
    viewer_client = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    viewer_config = viewer_client.get("/api/auth/config")
    assert viewer_config.status_code == 200, viewer_config.text
    assert set(viewer_config.json()) == CONFIG_FIELDS
    viewer_me = viewer_client.get("/api/auth/me")
    assert viewer_me.status_code == 200, viewer_me.text
    viewer_body = viewer_me.json()
    assert viewer_body["authenticated"] is True
    assert viewer_body["user"]["roles"] == ["user"]
    assert "admin" not in viewer_body["user"]["roles"]
    assert "admin" not in viewer_body["user"]["groups"]
    assert before == _table_counts()
