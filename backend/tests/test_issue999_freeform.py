"""Issue #999 proof: the upserts refuse another ERP's row.

Against the migrated test database, calling the three real
repositories directly (item_sites, on_hand and inventory_items), so
the guards in the sync runners are not involved.  For each of the
three repositories:

* refused: with a row stored under acumatica, upsert_many offering
  the same key under odoo with different values leaves the stored
  row exactly as it was, source_system included, and answers
  {"inserted": 0, "updated": 0, "refused": 1};
* its own source updates: the same offer under acumatica updates the
  row and answers {"inserted": 0, "updated": 1}, with no "refused"
  key;
* a new key inserts: an offer of a new key under odoo answers
  {"inserted": 1, "updated": 0} and stores the row under odoo;
* a mixed batch: one call offering one refused row, one update and
  one insert answers each count once, and the other two rows are
  stored;
* a null source: a stored row with a null source_system is updated by
  an offer with a null one and refused for an offer under odoo;
* stale on hand: an on_hand offer under the same source with an
  older as_of still behaves as it did — counted in neither, row
  untouched — and has no "refused" key.

The test reads no statement counts and starts no threads, because
the database condition itself is the proof.
"""

from app.db.connection import get_connection
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository


def _inventory_row(item_code: str):
    """The stored inventory_items row for one item_code, every
    column, or None."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, item_code, description, uom, source_system, "
                "created_at, updated_at FROM inventory_items "
                "WHERE item_code = %s",
                (item_code,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def test_issue999_freeform(client):
    # ------------------------------------------------------------------
    # item_sites
    # ------------------------------------------------------------------
    sites = ItemSitePostgresRepository()

    # 1. Store a row under acumatica.
    assert sites.upsert_many(
        [
            {
                "item_code": "IS-A",
                "site_code": "S",
                "unit_cost": 10.0,
                "reorder_point": 5.0,
                "source_system": "acumatica",
            }
        ]
    ) == {"inserted": 1, "updated": 0}
    stored = sites.get_by_key("IS-A", "S")

    # 2. Refused: the same key offered under odoo with different
    #    values leaves the stored row exactly as it was,
    #    source_system included.
    assert sites.upsert_many(
        [
            {
                "item_code": "IS-A",
                "site_code": "S",
                "unit_cost": 99.0,
                "reorder_point": 42.0,
                "source_system": "odoo",
            }
        ]
    ) == {"inserted": 0, "updated": 0, "refused": 1}
    assert sites.get_by_key("IS-A", "S") == stored

    # 3. Its own source updates: the same offer under acumatica
    #    updates the row, with no "refused" key.
    answer = sites.upsert_many(
        [
            {
                "item_code": "IS-A",
                "site_code": "S",
                "unit_cost": 99.0,
                "reorder_point": 42.0,
                "source_system": "acumatica",
            }
        ]
    )
    assert answer == {"inserted": 0, "updated": 1}
    assert "refused" not in answer
    updated = sites.get_by_key("IS-A", "S")
    assert updated["unit_cost"] == 99.0
    assert updated["reorder_point"] == 42.0
    assert updated["source_system"] == "acumatica"

    # 4. A new key inserts under odoo.
    assert sites.upsert_many(
        [
            {
                "item_code": "IS-B",
                "site_code": "S",
                "unit_cost": 3.0,
                "source_system": "odoo",
            }
        ]
    ) == {"inserted": 1, "updated": 0}
    assert sites.get_by_key("IS-B", "S")["source_system"] == "odoo"

    # 5. A mixed batch: one refused row, one update and one insert
    #    in one call answer each count once, and the other two rows
    #    are stored.
    before = sites.get_by_key("IS-A", "S")
    assert sites.upsert_many(
        [
            {
                "item_code": "IS-A",
                "site_code": "S",
                "unit_cost": 88.0,
                "source_system": "odoo",
            },
            {
                "item_code": "IS-B",
                "site_code": "S",
                "unit_cost": 4.0,
                "source_system": "odoo",
            },
            {
                "item_code": "IS-C",
                "site_code": "S",
                "unit_cost": 7.0,
                "source_system": "odoo",
            },
        ]
    ) == {"inserted": 1, "updated": 1, "refused": 1}
    assert sites.get_by_key("IS-A", "S") == before
    assert sites.get_by_key("IS-B", "S")["unit_cost"] == 4.0
    assert sites.get_by_key("IS-C", "S")["unit_cost"] == 7.0
    assert sites.get_by_key("IS-C", "S")["source_system"] == "odoo"

    # 6. A null source: a stored row with a null source_system is
    #    updated by an offer with a null one and refused for an offer
    #    under odoo.
    assert sites.upsert_many(
        [
            {
                "item_code": "IS-D",
                "site_code": "S",
                "unit_cost": 2.0,
                "source_system": None,
            }
        ]
    ) == {"inserted": 1, "updated": 0}
    null_stored = sites.get_by_key("IS-D", "S")
    assert null_stored["source_system"] is None
    answer = sites.upsert_many(
        [
            {
                "item_code": "IS-D",
                "site_code": "S",
                "unit_cost": 4.0,
                "source_system": None,
            }
        ]
    )
    assert answer == {"inserted": 0, "updated": 1}
    assert "refused" not in answer
    assert sites.get_by_key("IS-D", "S")["unit_cost"] == 4.0
    assert sites.get_by_key("IS-D", "S")["source_system"] is None
    null_stored = sites.get_by_key("IS-D", "S")
    assert sites.upsert_many(
        [
            {
                "item_code": "IS-D",
                "site_code": "S",
                "unit_cost": 9.0,
                "source_system": "odoo",
            }
        ]
    ) == {"inserted": 0, "updated": 0, "refused": 1}
    assert sites.get_by_key("IS-D", "S") == null_stored

    # ------------------------------------------------------------------
    # on_hand
    # ------------------------------------------------------------------
    hand = OnHandPostgresRepository()

    # 7. Store a row under acumatica.
    assert hand.upsert_many(
        [
            {
                "item_code": "OH-A",
                "site_code": "S",
                "quantity_on_hand": 100.0,
                "as_of": "2026-11-01T00:00:00Z",
                "source_system": "acumatica",
            }
        ]
    ) == {"inserted": 1, "updated": 0}
    stored = hand.get_row("OH-A", "S")

    # 8. Refused: the same key offered under odoo with different
    #    values (and a fresh as_of) leaves the stored row exactly as
    #    it was, source_system included.
    assert hand.upsert_many(
        [
            {
                "item_code": "OH-A",
                "site_code": "S",
                "quantity_on_hand": 55.0,
                "as_of": "2026-11-05T00:00:00Z",
                "source_system": "odoo",
            }
        ]
    ) == {"inserted": 0, "updated": 0, "refused": 1}
    assert hand.get_row("OH-A", "S") == stored

    # 9. Its own source updates: the same offer under acumatica
    #    updates the row, with no "refused" key.
    answer = hand.upsert_many(
        [
            {
                "item_code": "OH-A",
                "site_code": "S",
                "quantity_on_hand": 55.0,
                "as_of": "2026-11-05T00:00:00Z",
                "source_system": "acumatica",
            }
        ]
    )
    assert answer == {"inserted": 0, "updated": 1}
    assert "refused" not in answer
    updated = hand.get_row("OH-A", "S")
    assert updated["quantity_on_hand"] == 55.0
    assert updated["source_system"] == "acumatica"

    # 10. A new key inserts under odoo.
    assert hand.upsert_many(
        [
            {
                "item_code": "OH-B",
                "site_code": "S",
                "quantity_on_hand": 3.0,
                "as_of": "2026-11-05T00:00:00Z",
                "source_system": "odoo",
            }
        ]
    ) == {"inserted": 1, "updated": 0}
    assert hand.get_row("OH-B", "S")["source_system"] == "odoo"

    # 11. A mixed batch: one refused row, one update and one insert
    #     in one call answer each count once, and the other two rows
    #     are stored.
    before = hand.get_row("OH-A", "S")
    assert hand.upsert_many(
        [
            {
                "item_code": "OH-A",
                "site_code": "S",
                "quantity_on_hand": 77.0,
                "as_of": "2026-11-05T00:00:00Z",
                "source_system": "odoo",
            },
            {
                "item_code": "OH-B",
                "site_code": "S",
                "quantity_on_hand": 88.0,
                "as_of": "2026-11-05T00:00:00Z",
                "source_system": "odoo",
            },
            {
                "item_code": "OH-C",
                "site_code": "S",
                "quantity_on_hand": 1.0,
                "as_of": "2026-11-05T00:00:00Z",
                "source_system": "odoo",
            },
        ]
    ) == {"inserted": 1, "updated": 1, "refused": 1}
    assert hand.get_row("OH-A", "S") == before
    assert hand.get_row("OH-B", "S")["quantity_on_hand"] == 88.0
    assert hand.get_row("OH-C", "S")["quantity_on_hand"] == 1.0
    assert hand.get_row("OH-C", "S")["source_system"] == "odoo"

    # 12. A null source: a stored row with a null source_system is
    #     updated by an offer with a null one and refused for an offer
    #     under odoo.
    assert hand.upsert_many(
        [
            {
                "item_code": "OH-D",
                "site_code": "S",
                "quantity_on_hand": 5.0,
                "as_of": "2026-11-01T00:00:00Z",
                "source_system": None,
            }
        ]
    ) == {"inserted": 1, "updated": 0}
    null_stored = hand.get_row("OH-D", "S")
    assert null_stored["source_system"] is None
    answer = hand.upsert_many(
        [
            {
                "item_code": "OH-D",
                "site_code": "S",
                "quantity_on_hand": 9.0,
                "as_of": "2026-11-05T00:00:00Z",
                "source_system": None,
            }
        ]
    )
    assert answer == {"inserted": 0, "updated": 1}
    assert "refused" not in answer
    assert hand.get_row("OH-D", "S")["quantity_on_hand"] == 9.0
    assert hand.get_row("OH-D", "S")["source_system"] is None
    null_stored = hand.get_row("OH-D", "S")
    assert hand.upsert_many(
        [
            {
                "item_code": "OH-D",
                "site_code": "S",
                "quantity_on_hand": 2.0,
                "as_of": "2026-11-05T00:00:00Z",
                "source_system": "odoo",
            }
        ]
    ) == {"inserted": 0, "updated": 0, "refused": 1}
    assert hand.get_row("OH-D", "S") == null_stored

    # 13. Stale on hand: an offer under the same source with an
    #     older as_of still behaves as it did — counted in neither,
    #     row untouched — and has no "refused" key.
    before = hand.get_row("OH-A", "S")
    answer = hand.upsert_many(
        [
            {
                "item_code": "OH-A",
                "site_code": "S",
                "quantity_on_hand": 3.0,
                "as_of": "2026-11-02T00:00:00Z",
                "source_system": "acumatica",
            }
        ]
    )
    assert answer == {"inserted": 0, "updated": 0}
    assert "refused" not in answer
    assert hand.get_row("OH-A", "S") == before

    # ------------------------------------------------------------------
    # inventory_items
    # ------------------------------------------------------------------
    items = InventoryItemPostgresRepository()

    # 14. Store a row under acumatica.
    assert items.upsert_many(
        [
            {
                "item_code": "II-A",
                "description": "Acme bolt",
                "uom": "EA",
                "source_system": "acumatica",
            }
        ]
    ) == {"inserted": 1, "updated": 0}
    stored = _inventory_row("II-A")

    # 15. Refused: the same key offered under odoo with different
    #     values leaves the stored row exactly as it was,
    #     source_system included.
    assert items.upsert_many(
        [
            {
                "item_code": "II-A",
                "description": "Odoo bolt",
                "uom": "BOX",
                "source_system": "odoo",
            }
        ]
    ) == {"inserted": 0, "updated": 0, "refused": 1}
    assert _inventory_row("II-A") == stored

    # 16. Its own source updates: the same offer under acumatica
    #     updates the row, with no "refused" key.
    answer = items.upsert_many(
        [
            {
                "item_code": "II-A",
                "description": "Odoo bolt",
                "uom": "BOX",
                "source_system": "acumatica",
            }
        ]
    )
    assert answer == {"inserted": 0, "updated": 1}
    assert "refused" not in answer
    updated = _inventory_row("II-A")
    assert updated[2] == "Odoo bolt"
    assert updated[3] == "BOX"
    assert updated[4] == "acumatica"

    # 17. A new key inserts under odoo.
    assert items.upsert_many(
        [
            {
                "item_code": "II-B",
                "description": "Bee nut",
                "uom": "EA",
                "source_system": "odoo",
            }
        ]
    ) == {"inserted": 1, "updated": 0}
    assert _inventory_row("II-B")[4] == "odoo"

    # 18. A mixed batch: one refused row, one update and one insert
    #     in one call answer each count once, and the other two rows
    #     are stored.
    before = _inventory_row("II-A")
    assert items.upsert_many(
        [
            {
                "item_code": "II-A",
                "description": "Held name",
                "uom": "CASE",
                "source_system": "odoo",
            },
            {
                "item_code": "II-B",
                "description": "Bee nut v2",
                "uom": "EA",
                "source_system": "odoo",
            },
            {
                "item_code": "II-C",
                "description": "Cee pin",
                "uom": "EA",
                "source_system": "odoo",
            },
        ]
    ) == {"inserted": 1, "updated": 1, "refused": 1}
    assert _inventory_row("II-A") == before
    assert _inventory_row("II-B")[2] == "Bee nut v2"
    assert _inventory_row("II-C")[2] == "Cee pin"
    assert _inventory_row("II-C")[4] == "odoo"

    # 19. A null source: a stored row with a null source_system is
    #     updated by an offer with a null one and refused for an offer
    #     under odoo.
    assert items.upsert_many(
        [
            {
                "item_code": "II-D",
                "description": "Dee washer",
                "uom": "EA",
                "source_system": None,
            }
        ]
    ) == {"inserted": 1, "updated": 0}
    null_stored = _inventory_row("II-D")
    assert null_stored[4] is None
    answer = items.upsert_many(
        [
            {
                "item_code": "II-D",
                "description": "Dee washer v2",
                "uom": "EA",
                "source_system": None,
            }
        ]
    )
    assert answer == {"inserted": 0, "updated": 1}
    assert "refused" not in answer
    assert _inventory_row("II-D")[2] == "Dee washer v2"
    assert _inventory_row("II-D")[4] is None
    null_stored = _inventory_row("II-D")
    assert items.upsert_many(
        [
            {
                "item_code": "II-D",
                "description": "Odoo washer",
                "uom": "EA",
                "source_system": "odoo",
            }
        ]
    ) == {"inserted": 0, "updated": 0, "refused": 1}
    assert _inventory_row("II-D") == null_stored
