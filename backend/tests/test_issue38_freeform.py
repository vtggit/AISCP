"""Issue #38 proof: on-hand lookup moves from path segments to query params.

A path segment can never contain ``/``, so an item code like ``AB/12`` was
unfetchable through ``GET /api/on-hand/{item_code}/{site_code}``. The lookup
is now ``GET /api/on-hand/by-key?item_code=...&site_code=...`` with required
query parameters.

Against the test database:
  * a row stored through the repository with item code ``AB/12`` is returned
    by ``/api/on-hand/by-key?item_code=AB%2F12&site_code=1``,
  * a missing (item_code, site_code) pair answers 404,
  * both query parameters are required (missing answers 422),
  * the list route is unchanged with its item_code/site_code filters still
    applied, and the old path route is gone.
"""

from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository


def test_issue38_freeform(client):
    repo = OnHandPostgresRepository()

    # 1. Store a row whose item code contains a slash, through the
    #    repository (the sync path, not a write route).
    assert repo.upsert_many(
        [
            {
                "item_code": "AB/12",
                "site_code": "1",
                "quantity_on_hand": 3.5,
                "as_of": "2026-10-01T08:00:00Z",
                "source_system": "dynamics",
            }
        ]
    ) == {"inserted": 1, "updated": 0}

    # 2. The percent-encoded slash round-trips: by-key returns the stored
    #    row for the exact key.
    one = client.get("/api/on-hand/by-key?item_code=AB%2F12&site_code=1")
    assert one.status_code == 200, one.text
    body = one.json()
    assert body["item_code"] == "AB/12"
    assert body["site_code"] == "1"
    assert body["quantity_on_hand"] == 3.5
    assert body["id"]
    assert body["synced_at"]

    # 3. A missing (item_code, site_code) pair answers 404.
    missing = client.get(
        "/api/on-hand/by-key", params={"item_code": "AB/12", "site_code": "9"}
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "on-hand row not found"

    # 4. Both query parameters are required.
    assert client.get("/api/on-hand/by-key").status_code == 422
    assert (
        client.get("/api/on-hand/by-key", params={"item_code": "AB/12"}).status_code
        == 422
    )

    # 5. The list route is unchanged and its filters are still applied:
    #    decoy rows make each filter's removal observable. Without the
    #    item_code filter the decoy ZZ/99@1 would leak into (a) and (c);
    #    without the site_code filter the decoy AB/12@2 would leak into
    #    (b) and (c). A route with either filter removed fails a strict
    #    assertion, so the check can no longer pass with filtering gone.
    assert repo.upsert_many(
        [
            {
                "item_code": "AB/12",
                "site_code": "2",
                "quantity_on_hand": 1.0,
                "as_of": "2026-10-01T09:00:00Z",
                "source_system": "dynamics",
            },
            {
                "item_code": "ZZ/99",
                "site_code": "1",
                "quantity_on_hand": 2.0,
                "as_of": "2026-10-01T09:00:00Z",
                "source_system": "dynamics",
            },
        ]
    ) == {"inserted": 2, "updated": 0}

    listed = client.get("/api/on-hand", params={"item_code": "AB/12"})
    assert listed.status_code == 200, listed.text
    assert [(row["item_code"], row["site_code"]) for row in listed.json()] == [
        ("AB/12", "1"),
        ("AB/12", "2"),
    ]

    listed_both = client.get(
        "/api/on-hand", params={"item_code": "AB/12", "site_code": "1"}
    )
    assert listed_both.status_code == 200, listed_both.text
    assert [(row["item_code"], row["site_code"]) for row in listed_both.json()] == [
        ("AB/12", "1")
    ]

    listed_site = client.get("/api/on-hand", params={"site_code": "1"})
    assert listed_site.status_code == 200, listed_site.text
    assert [(row["item_code"], row["site_code"]) for row in listed_site.json()] == [
        ("AB/12", "1"),
        ("ZZ/99", "1"),
    ]

    # 6. The old path route is gone: no path segments for the key anymore.
    assert client.get("/api/on-hand/AB%2F12/1").status_code == 404
