"""CRUD proof for inventory_items — the idiom anchor for the deterministic lanes."""


def test_inventory_item_round_trips(client, admin_headers):
    created = client.post(
        "/api/inventory-items",
        json={
            "item_code": "WIDGET-1",
            "description": "test widget",
            "uom": "EA",
            "source_system": "acumatica",
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    fetched = client.get(f"/api/inventory-items/{item_id}")
    assert fetched.status_code == 200
    assert fetched.json()["item_code"] == "WIDGET-1"

    updated = client.put(
        f"/api/inventory-items/{item_id}",
        json={"description": "renamed"},
        headers=admin_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "renamed"

    listed = client.get("/api/inventory-items")
    assert listed.status_code == 200
    assert any(r["id"] == item_id for r in listed.json())

    deleted = client.delete(f"/api/inventory-items/{item_id}", headers=admin_headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/inventory-items/{item_id}").status_code == 404
