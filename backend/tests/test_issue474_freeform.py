"""Issue #474 proof: the draft-PO request codes are bounded at 255
characters by request validation.

The draft-PO route's ``item_code``, ``site_code`` and ``supplier_code``
are now ``Field(min_length=1, max_length=255)`` on the request model,
and a route dependency re-checks the bound during request validation —
before the body is model-validated — so an overlong code is a 422
before any mapping lookup, Odoo call or audit row.  The 422 detail
carries the field name and the bound but never the full overlong
input (the standard FastAPI 422 envelope would echo it in ``input``);
every other 422 (blank code, NUL byte, quantity, unit_price, malformed
body) keeps the standard FastAPI envelope.

The endpoint is exercised through the app's test client with the
Odoo client stubbed (recording every preview/create call) and the
mapping repository stubbed (recording every lookup), so "no Odoo
call" and "no mapping lookup" are direct assertions on the stubs.

Proved here:

  * a 256-character ``item_code``, ``site_code`` or ``supplier_code``
    is a 422 — the standard envelope minus the ``input`` echo — for
    both a dry run (non-admin) and an admin confirm, with no mapping
    lookup, no Odoo call and no audit row, and the full overlong
    value absent from the response body;
  * a 255-character code is still accepted: the request reaches the
    stubbed client with the 255-character codes and answers 200;
  * an ordinary request still returns 200 with its ``target``;
  * the identity requirement: the proof runs as a NON-ADMIN (role
    ``user``, asserted via ``GET /api/auth/me``), and a second pass
    after a full reload (a fresh ``TestClient`` booted the production
    way — the token the page would take from the URL hash as
    ``#access_token=...``, with ``GET /api/auth/config`` answering
    publicly and ``GET /api/auth/me`` resolving the viewer) sees the
    admin-only control absent — a confirm is refused 403 before any
    Odoo call and without an audit row, and the audit list is 403 —
    while the page still renders: the dry-run preview and the
    settings the page loads answer 200, and a request without a token
    still gets 401, even with an overlong body;
  * the bound check enforces the auth gate itself: with the router
    mounted WITHOUT the app's mount-time gate, an unauthenticated
    overlong body is a 401 (never a 422) before any mapping lookup or
    Odoo call, an authenticated overlong body keeps the 422 without
    the input echo, and an ordinary request still answers 200.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.replenishment_actions as replenishment_actions
from app.main import app

URL = "/api/replenishment/actions/draft-po"
AUDIT_URL = URL + "/audit"
ADMIN_TOKEN = "dev-secret-token:admin"
# The bare development token: the token the page would take from the
# URL hash as #access_token=... — a non-admin (role "user").
VIEWER_TOKEN = "dev-secret-token"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject
CODE_FIELDS = ("item_code", "site_code", "supplier_code")
ADMIN_DETAIL = "Role 'admin' is required to perform this action."


class _RecordingOdoo:
    """The stubbed Odoo client: records every call, canned answers."""

    def __init__(self) -> None:
        self.preview_calls: list[dict] = []
        self.create_calls: list[dict] = []
        self.closed = False

    def preview_draft_purchase_order(self, **kwargs) -> dict:
        self.preview_calls.append(kwargs)
        return {
            "existing": None,
            "origin": "AIERP:replenishment:stub",
            "resolved": {
                "product_id": 11,
                "partner_id": 501,
                "picking_type_id": 301,
            },
        }

    def create_draft_purchase_order(self, **kwargs) -> dict:
        self.create_calls.append(kwargs)
        return {
            "odoo_id": 901,
            "name": "PO/2026/0901",
            "state": "draft",
            "already_existed": False,
        }

    def close(self) -> None:
        self.closed = True


class _RecordingMappings:
    """The stubbed mapping repository: records every lookup, unmapped."""

    def __init__(self) -> None:
        self.lookups: list[tuple[str, str, str]] = []

    def get_target(self, kind: str, target_system: str, code: str):
        self.lookups.append((kind, target_system, code))
        return None


def test_issue474_freeform(monkeypatch, client) -> None:
    # Isolate the Odoo environment: the stubbed client never reads it,
    # but nothing else may either.
    for var in (
        "ODOO_BASE_URL",
        "ODOO_DB",
        "ODOO_LOGIN",
        "ODOO_API_KEY",
        "ODOO_CA_BUNDLE",
        "ODOO_TIMEOUT_SECONDS",
        "ODOO_ALLOW_INSECURE",
        "ODOO_ALLOW_WRITES",
    ):
        monkeypatch.delenv(var, raising=False)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # The Odoo client is stubbed with a recording factory: the route
    # builds it via the module global, so every construction and call
    # is observable.
    clients: list[_RecordingOdoo] = []

    def fake_odoo_client() -> _RecordingOdoo:
        record = _RecordingOdoo()
        clients.append(record)
        return record

    monkeypatch.setattr(replenishment_actions, "OdooClient", fake_odoo_client)

    # The mapping repository is stubbed with a recording fake: every
    # get_target lookup is observed, and every code stays unmapped
    # (passes through unchanged).
    mappings = _RecordingMappings()
    monkeypatch.setattr(replenishment_actions, "_mappings_repo", mappings)

    def body(**overrides) -> dict:
        payload = {
            "item_code": "WIDGET 1",
            "site_code": "WH-1",
            "supplier_code": "VENDOR-9",
            "quantity": 12,
            "unit_price": 4.25,
        }
        payload.update(overrides)
        return payload

    def audit_count() -> int:
        response = admin.get(AUDIT_URL)
        assert response.status_code == 200, response.text
        return len(response.json())

    def overlong_detail(field: str) -> dict:
        # The standard FastAPI 422 envelope minus the input echo.
        return {
            "detail": [
                {
                    "type": "string_too_long",
                    "loc": ["body", field],
                    "msg": "String should have at most 255 characters",
                    "ctx": {"max_length": 255},
                }
            ]
        }

    # 1. A 256-character code in each of the three fields is a 422 for
    #    both a dry run (non-admin) and an admin confirm — no mapping
    #    lookup, no Odoo call, no audit row — and the full overlong
    #    value never appears in the response body.
    before_lookups = len(mappings.lookups)
    before_clients = len(clients)
    before_audit = audit_count()
    for field in CODE_FIELDS:
        overlong = "X" * 256
        response = client.post(URL, json=body(**{field: overlong}))
        assert response.status_code == 422, (field, response.text)
        assert response.json() == overlong_detail(field), response.text
        assert overlong not in response.text, field
        response = admin.post(URL, json=body(confirm=True, **{field: overlong}))
        assert response.status_code == 422, (field, response.text)
        assert response.json() == overlong_detail(field), response.text
        assert overlong not in response.text, field
    assert len(mappings.lookups) == before_lookups
    assert len(clients) == before_clients  # the client was never built
    assert all(not c.preview_calls and not c.create_calls for c in clients)
    assert audit_count() == before_audit

    # 2. A 255-character code is still accepted: the request reaches
    #    the stubbed client with the 255-character codes and answers
    #    200 (dry run, non-admin).
    accepted = {
        "item_code": "A" * 255,
        "site_code": "B" * 255,
        "supplier_code": "C" * 255,
    }
    response = client.post(URL, json=body(**accepted))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["target"] == accepted
    assert len(clients) == before_clients + 1
    assert clients[-1].preview_calls == [
        {
            "item_code": "A" * 255,
            "site_code": "B" * 255,
            "supplier_code": "C" * 255,
        }
    ]
    assert clients[-1].create_calls == []
    # The stubbed repository saw exactly the three unmapped lookups.
    assert mappings.lookups[before_lookups:] == [
        ("item", "odoo", "A" * 255),
        ("site", "odoo", "B" * 255),
        ("vendor", "odoo", "C" * 255),
    ]

    # 3. An ordinary request still returns 200 with its target.
    response = client.post(URL, json=body())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["target"] == {
        "item_code": "WIDGET 1",
        "site_code": "WH-1",
        "supplier_code": "VENDOR-9",
    }
    assert audit_count() == before_audit

    # 4. Identity, second pass after a full reload: the page boots the
    #    production way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the non-admin viewer; the admin-only
    #    control is absent — confirm 403, audit list 403, no Odoo
    #    call, no audit row — while the page still renders.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == VIEWER_SUB
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]

    before_lookups = len(mappings.lookups)
    before_clients = len(clients)
    before_audit = audit_count()
    # The overlong bound still applies to the viewer, before anything
    # else (validation precedes the role check).
    overlong = "Y" * 256
    response = viewer.post(URL, json=body(confirm=True, item_code=overlong))
    assert response.status_code == 422, response.text
    assert response.json() == overlong_detail("item_code"), response.text
    assert overlong not in response.text
    # The admin-only control is absent: a confirm is refused 403
    # before any Odoo call and without an audit row.
    response = viewer.post(URL, json=body(confirm=True))
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    # The audit list is the other admin-only control: 403 for the
    # viewer, 401 without a token.
    assert viewer.get(AUDIT_URL).status_code == 403
    assert anon_reloaded.get(AUDIT_URL).status_code == 401
    assert len(mappings.lookups) == before_lookups
    assert len(clients) == before_clients
    assert audit_count() == before_audit
    # The page still renders: the dry-run preview and the settings the
    # page loads both answer 200 for the viewer.
    response = viewer.post(URL, json=body())
    assert response.status_code == 200, response.text
    assert response.json()["dry_run"] is True
    assert viewer.get("/api/settings").status_code == 200
    # A request without a token is a 401 before anything else — even
    # with an overlong body (the gate still precedes validation).
    response = anon_reloaded.post(URL, json=body(item_code="Z" * 256))
    assert response.status_code == 401, response.text
    assert "Z" * 256 not in response.text

    # 5. The bound check enforces the auth gate itself: with the
    #    router mounted WITHOUT the app's mount-time gate (its own
    #    gate is the endpoint's auth dependency), an unauthenticated
    #    overlong body is a 401 — never a 422 — before any mapping
    #    lookup or Odoo call; an authenticated overlong body keeps
    #    the 422 without the input echo, and an ordinary request
    #    still answers 200.
    bare = FastAPI()
    bare.include_router(replenishment_actions.router)
    before_lookups = len(mappings.lookups)
    before_clients = len(clients)
    before_audit = audit_count()
    overlong = "Q" * 256
    with TestClient(bare) as bare_anonymous:
        response = bare_anonymous.post(URL, json=body(item_code=overlong))
        assert response.status_code == 401, response.text
        assert response.headers.get("www-authenticate") == "Bearer"
        assert overlong not in response.text
        response = bare_anonymous.post(URL, json=body())
        assert response.status_code == 401, response.text
    with TestClient(
        bare, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
    ) as bare_viewer:
        response = bare_viewer.post(URL, json=body(item_code=overlong))
        assert response.status_code == 422, response.text
        assert response.json() == overlong_detail("item_code"), response.text
        assert overlong not in response.text
        response = bare_viewer.post(URL, json=body())
        assert response.status_code == 200, response.text
        assert response.json()["dry_run"] is True
    # Only the authenticated 200 dry run ran translation lookups —
    # the 401s and the 422 made none.
    assert mappings.lookups[before_lookups:] == [
        ("item", "odoo", "WIDGET 1"),
        ("site", "odoo", "WH-1"),
        ("vendor", "odoo", "VENDOR-9"),
    ]
    # Only the authenticated 200 dry run built a client.
    assert len(clients) == before_clients + 1
    assert clients[-1].preview_calls == [
        {
            "item_code": "WIDGET 1",
            "site_code": "WH-1",
            "supplier_code": "VENDOR-9",
        }
    ]
    assert clients[-1].create_calls == []
    assert audit_count() == before_audit
