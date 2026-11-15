"""Issue #66 proof: the served app fails closed on authentication.

Every route registered in ``create_app()`` except ``GET /api/health``
requires an authenticated user through
``app.auth.dependencies.require_authenticated_user`` (applied once at
router level, so the module-level ``app`` — the object
``uvicorn app.main:app`` serves — IS the protected app).  The gate
extracts and validates the Bearer token itself and never accepts the
synthetic anonymous principal that ``get_current_user`` returns when
``AUTH_ENABLED`` is false.

Every assertion below runs against ``app.main.app`` itself.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main as app_main
from app.auth import dependencies as auth_dependencies
from tests import conftest as tests_conftest

DEV_TOKEN = "dev-secret-token"
DEV_BEARER = {"Authorization": f"Bearer {DEV_TOKEN}"}
AUTH_DETAIL = "Authentication required. Provide a valid Bearer token."
GATED_PATHS = ("/api/drift", "/api/drift/export.csv", "/api/item-sites")


def _unauth_client() -> TestClient:
    """A TestClient with no default credentials, aimed at the served app."""
    return TestClient(app_main.app)


def _assert_401_body(response) -> None:
    assert response.json()["detail"] == AUTH_DETAIL
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def _effective_api_routes(application):
    """Yield (path, dependant) for every route the served app will dispatch.

    Handles both the classic flat route list and the lazy ``_IncludedRouter``
    layout used by newer FastAPI releases, so the gate check always runs on
    the routes the app actually serves.
    """

    def walk(node):
        if type(node).__name__ == "_IncludedRouter":
            for candidate in node.effective_candidates():
                yield from walk(candidate)
            return
        dependant = getattr(node, "dependant", None)
        path = getattr(node, "path", None)
        if dependant is not None and path is not None:
            yield path, dependant

    for route in application.routes:
        yield from walk(route)


def _source_router_routes(application):
    """Yield the routes of every router the app includes, before merging.

    These are the routers as written in the API modules: if any of them
    carried the gate, it would be applied per route and the effective
    context would show it twice.
    """

    def walk(node):
        if type(node).__name__ == "_IncludedRouter":
            yield from node.original_router.routes
            for candidate in node.effective_candidates():
                yield from walk(candidate)

    for route in application.routes:
        yield from walk(route)


def test_issue66_freeform(client, monkeypatch):
    served_app = app_main.app  # exactly what `uvicorn app.main:app` serves

    # -- AC-2: unauthenticated requests to any gated route return 401 -------
    with _unauth_client() as unauth:
        for path in GATED_PATHS:
            response = unauth.get(path)
            assert response.status_code == 401
            _assert_401_body(response)

        # -- AC-2: the same requests with the development bearer token 200 --
        for path in GATED_PATHS:
            assert unauth.get(path, headers=DEV_BEARER).status_code == 200
            # ...and through the shared `client` fixture, which authenticates
            # the way a real caller does.
            assert client.get(path).status_code == 200

        # -- AC-2: GET /api/health stays open without a token ---------------
        health = unauth.get("/api/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

    # -- Review fix: require_authenticated_user IS the applied gate ---------
    # Identity check (not a name match) against the served app's own route
    # table: every /api route except the liveness probe must carry exactly
    # one dependency whose callable is the gate function itself.
    api_routes = [
        (path, dependant)
        for path, dependant in _effective_api_routes(served_app)
        if path.startswith("/api")
    ]
    assert api_routes, "served app exposes no /api routes"
    assert any(path == "/api/health" for path, _ in api_routes)

    for path, dependant in api_routes:
        gate = [
            sub
            for sub in dependant.dependencies
            if sub.call is auth_dependencies.require_authenticated_user
        ]
        if path == "/api/health" or path == "/api/auth/config":
            assert not gate, f"liveness probe must stay open: {path}"
        else:
            assert len(gate) == 1, f"gate must apply exactly once on {path}"

    # Applied once at include time, not per route: the routers as written
    # carry no gate of their own.
    for route in _source_router_routes(served_app):
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        assert getattr(route, "path", None) == "/api/auth/me" or not any(
            sub.call is auth_dependencies.require_authenticated_user
            for sub in dependant.dependencies
        ), f"gate duplicated onto a source route: {getattr(route, 'path', '?')}"

    # -- AC-10/AC-11: the gate fails closed with AUTH_ENABLED false ---------
    # Patched exactly where app.auth.dependencies reads the flag.
    monkeypatch.setattr(auth_dependencies, "AUTH_ENABLED", False)
    assert auth_dependencies.AUTH_ENABLED is False

    with _unauth_client() as unauth:
        # No header at all: the synthetic anonymous principal is NOT accepted.
        response = unauth.get("/api/drift")
        assert response.status_code == 401
        _assert_401_body(response)

        # Malformed headers (anything but `Bearer <token>`).
        assert (
            unauth.get("/api/drift", headers={"Authorization": "Basic abc"}).status_code
            == 401
        )
        assert (
            unauth.get("/api/drift", headers={"Authorization": "Bearer"}).status_code
            == 401
        )
        assert (
            unauth.get("/api/drift", headers={"Authorization": "Token abc"}).status_code
            == 401
        )

        # Wrong bearer token.
        assert (
            unauth.get(
                "/api/drift", headers={"Authorization": "Bearer wrong-token"}
            ).status_code
            == 401
        )

        # The development bearer token still authenticates.
        assert unauth.get("/api/drift", headers=DEV_BEARER).status_code == 200

    # -- Hostile Authorization values must 401, never 500 --------------------
    hostile_values = (
        None,
        "",
        "Bearer",
        "Bearer ",
        "Bearer\x00",
        "\x00Bearer",
        "Bearer " + "x" * 100_000,
        "Bearer a\nb",
        "Bearer 123",
        "Bearer \u00e9\u00fc",
        "bearer wrong",
    )
    for value in hostile_values:
        with pytest.raises(HTTPException) as excinfo:
            auth_dependencies.require_authenticated_user(authorization=value)
        assert excinfo.value.status_code == 401
        assert excinfo.value.detail == AUTH_DETAIL
        assert excinfo.value.headers == {"WWW-Authenticate": "Bearer"}

    # -- Review fix: an empty AIERP_TEST_DB value must not crash the fixture --
    # The session fixture resolves its database name through this resolver; a
    # zero-length name would make CREATE DATABASE a syntax error.
    assert tests_conftest._TEST_DB_NAME, "session fixture DB name is empty"
    monkeypatch.setenv("AIERP_TEST_DB", "")
    assert tests_conftest._test_db_name() == "aierp_test_db"
