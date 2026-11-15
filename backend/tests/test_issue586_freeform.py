"""Issue #586 proof: the inventory-items write routes need the admin role.

AC-1/AC-3 — ``POST``, ``PUT`` and ``DELETE /api/inventory-items`` in
``app.api.inventory_items`` each take ``current_user: AuthUser =
Depends(require_admin)``, the same dependency
``app.api.purchase_budgets`` uses, so a signed-in non-admin is refused
with 403 (``Role 'admin' is required to perform this action.''``)
before any repository mutation is attempted — the dependency raises
before the handler runs, and the row is created, changed or removed
for nobody but the admin.  An admin gets exactly today's behaviour:
201 with the InventoryItemResponse body for a create, 200 with the
updated row for an update, 204 for a delete, 404 for a missing id.
The ``GET`` routes stay open to any signed-in user: the list and
single-item reads answer 200 for the non-admin alike.

AC-2 — proven here with the ``client``, ``admin_headers`` and
``user_headers`` fixtures: with ``user_headers`` the ``POST``, ``PUT``
and ``DELETE`` each return 403 and a following ``GET`` shows the item
unchanged (absent for the ``POST``); with ``admin_headers`` the
``POST`` returns the existing success status and body shape, the
``PUT`` changes the item and the ``DELETE`` removes it; with
``user_headers`` the list and single-item ``GET``s still return 200.

Identity (CodeAgent#750): the criteria concern roles, so this proof
also runs as a NON-ADMIN (role ``user``) in a second pass after a full
reload: the page boots the production way (the token it would take
from the URL hash as ``#access_token=...``, the bare development
token, with ``/api/auth/config`` answering publicly and
``/api/auth/me`` resolving the viewer for that user) — the admin-only
write control is absent for the viewer (each write refused 403,
nothing changes) while the page still renders (the reads it loads
answer 200), and unauthenticated /api traffic is still 401.
"""

import os

from fastapi.testclient import TestClient

from app.main import app

CREATE_BODY = {
    "item_code": "ISS586-1",
    "description": "issue 586 fixture",
    "uom": "EA",
    "source_system": "acumatica",
}

RESPONSE_FIELDS = {
    "id",
    "item_code",
    "description",
    "uom",
    "source_system",
    "created_at",
    "updated_at",
}

ADMIN_DETAIL = {"detail": "Role 'admin' is required to perform this action."}


def test_issue586_freeform(client, admin_headers, user_headers):
    # -- AC-2: the signed-in non-admin is refused 403. ---------------
    # POST: refused, and nothing was created.
    response = client.post(
        "/api/inventory-items",
        json={**CREATE_BODY, "item_code": "ISS586-2"},
        headers=user_headers,
    )
    assert response.status_code == 403, response.text
    assert response.json() == ADMIN_DETAIL
    listed = client.get("/api/inventory-items", headers=user_headers)
    assert listed.status_code == 200, listed.text
    assert "ISS586-2" not in {row["item_code"] for row in listed.json()}

    # The admin gets exactly today's behaviour: 201 with the
    # existing success body shape.
    created = client.post(
        "/api/inventory-items", json=CREATE_BODY, headers=admin_headers
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == RESPONSE_FIELDS
    assert body["item_code"] == "ISS586-1"
    assert body["description"] == "issue 586 fixture"
    item_id = body["id"]

    # PUT: refused, and a following GET shows the item unchanged.
    response = client.put(
        f"/api/inventory-items/{item_id}",
        json={"description": "user tried"},
        headers=user_headers,
    )
    assert response.status_code == 403, response.text
    assert response.json() == ADMIN_DETAIL
    fetched = client.get(f"/api/inventory-items/{item_id}", headers=user_headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["description"] == "issue 586 fixture"

    # DELETE: refused, and a following GET shows the item still there.
    response = client.delete(f"/api/inventory-items/{item_id}", headers=user_headers)
    assert response.status_code == 403, response.text
    assert response.json() == ADMIN_DETAIL
    fetched = client.get(f"/api/inventory-items/{item_id}", headers=user_headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["id"] == item_id

    # -- AC-2: the admin mutates: the PUT changes, the DELETE removes.
    updated = client.put(
        f"/api/inventory-items/{item_id}",
        json={"description": "renamed"},
        headers=admin_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["id"] == item_id
    assert updated.json()["description"] == "renamed"

    deleted = client.delete(f"/api/inventory-items/{item_id}", headers=admin_headers)
    assert deleted.status_code == 204, deleted.text
    assert (
        client.get(f"/api/inventory-items/{item_id}", headers=admin_headers).status_code
        == 404
    )

    # -- AC-2: the GETs stay open to any signed-in user. --------------
    created = client.post(
        "/api/inventory-items", json=CREATE_BODY, headers=admin_headers
    )
    item_id = created.json()["id"]
    listed = client.get("/api/inventory-items", headers=user_headers)
    assert listed.status_code == 200, listed.text
    assert any(row["id"] == item_id for row in listed.json())
    fetched = client.get(f"/api/inventory-items/{item_id}", headers=user_headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["item_code"] == "ISS586-1"

    # -- AC-3: every 403 above left the repository untouched — the ---
    # dependency raises before the handler runs, so no mutation is
    # ever attempted for the non-admin (evidenced by the unchanged /
    # absent rows on every following GET).

    # -- Identity: the proof also runs as a NON-ADMIN. ----------------
    # Second pass after a full reload: the page boots the production
    # way — the token it would take from the URL hash as
    # #access_token=... (the bare development token), with
    # /api/auth/config answering publicly and /api/auth/me resolving
    # (dev-mode auth standing in for the IdP) the non-admin viewer for
    # that user.
    dev_token = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")
    viewer = TestClient(app, headers={"Authorization": f"Bearer {dev_token}"})
    anon = TestClient(app)
    assert anon.get("/api/auth/config").status_code == 200  # public, pre-token
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == "dev-user-1"
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]

    # The admin-only control is absent for the viewer: each write
    # route is refused 403 and nothing changes ...
    response = viewer.post(
        "/api/inventory-items",
        json={**CREATE_BODY, "item_code": "ISS586-9"},
    )
    assert response.status_code == 403, response.text
    assert "ISS586-9" not in {
        row["item_code"] for row in viewer.get("/api/inventory-items").json()
    }
    response = viewer.put(
        f"/api/inventory-items/{item_id}", json={"description": "viewer tried"}
    )
    assert response.status_code == 403, response.text
    fetched = viewer.get(f"/api/inventory-items/{item_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["description"] == "issue 586 fixture"
    assert viewer.delete(f"/api/inventory-items/{item_id}").status_code == 403
    assert viewer.get(f"/api/inventory-items/{item_id}").status_code == 200

    # ... while the page still renders: the reads it loads answer 200
    # for the viewer, and the gate is intact — unauthenticated /api
    # traffic is still 401.
    assert viewer.get("/api/inventory-items").status_code == 200
    assert viewer.get(f"/api/inventory-items/{item_id}").status_code == 200
    assert anon.post("/api/inventory-items", json=CREATE_BODY).status_code == 401
    assert anon.get("/api/inventory-items").status_code == 401
