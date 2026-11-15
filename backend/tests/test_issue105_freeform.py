"""Issue #105 proof: open_purchase_lines sync table and repository.

Against the test database (every date a fixed past calendar date; the
as-of snapshots are 2026-06-29 and 2026-06-30, never today):
  * an empty table returns [] from list_latest(),
  * a sync's rows come back from list_latest() with every column
    (dates as date, timestamps as ISO strings), ordered by
    (item_code, site_code, business_key),
  * a second upsert_many of the same business_key with a newer as_of and
    open_units 0 replaces the stored row,
  * an incoming row with an older as_of leaves the stored row unchanged
    (and counts in neither summary bucket),
  * after a newer sync omits a line, list_latest() no longer returns
    that line.
"""

from datetime import date

from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)

AS_OF_SYNC_ONE = "2026-06-29T00:00:00+00"
AS_OF_SYNC_TWO = "2026-06-30T00:00:00+00"

EXPECTED_KEYS = {
    "id",
    "business_key",
    "item_code",
    "site_code",
    "supplier_code",
    "order_type",
    "open_units",
    "promised_on",
    "ordered_on",
    "as_of",
    "source_system",
    "synced_at",
}


def _row(business_key, item_code, site_code, open_units, as_of, **overrides):
    """A sync row; overrides fill the nullable columns."""
    row = {
        "business_key": business_key,
        "item_code": item_code,
        "site_code": site_code,
        "supplier_code": None,
        "order_type": "PO",
        "open_units": open_units,
        "promised_on": None,
        "ordered_on": None,
        "as_of": as_of,
        "source_system": "test",
    }
    row.update(overrides)
    return row


def test_issue105_freeform(client):
    repo = OpenPurchaseLinePostgresRepository()

    # 1. An empty table returns [].
    assert repo.list_latest() == []

    # 2. First sync (as-of 2026-06-29): three lines, two of them on the
    #    same (item_code, site_code) so the business_key tiebreak of the
    #    ordering is observable.
    assert repo.upsert_many(
        [
            _row(
                "BK-1",
                "ITEM-1",
                "SITE-1",
                10.0,
                AS_OF_SYNC_ONE,
                supplier_code="SUP-1",
                promised_on="2026-07-05",
                ordered_on="2026-06-20",
            ),
            _row(
                "BK-2",
                "ITEM-1",
                "SITE-1",
                4.0,
                AS_OF_SYNC_ONE,
                supplier_code="SUP-2",
                order_type="STO",
            ),
            _row(
                "BK-3",
                "ITEM-0",
                "SITE-1",
                7.25,
                AS_OF_SYNC_ONE,
                ordered_on="2026-06-25",
            ),
        ]
    ) == {"inserted": 3, "updated": 0}

    latest = repo.list_latest()
    assert [r["business_key"] for r in latest] == ["BK-3", "BK-1", "BK-2"]

    bk1 = next(r for r in latest if r["business_key"] == "BK-1")
    assert set(bk1) == EXPECTED_KEYS
    assert bk1["item_code"] == "ITEM-1"
    assert bk1["site_code"] == "SITE-1"
    assert bk1["supplier_code"] == "SUP-1"
    assert bk1["order_type"] == "PO"
    assert bk1["open_units"] == 10.0
    assert isinstance(bk1["promised_on"], date)
    assert bk1["promised_on"] == date(2026, 7, 5)
    assert isinstance(bk1["ordered_on"], date)
    assert bk1["ordered_on"] == date(2026, 6, 20)
    assert isinstance(bk1["as_of"], str)
    assert bk1["as_of"].startswith("2026-06-29")
    assert bk1["source_system"] == "test"
    assert bk1["id"]
    assert isinstance(bk1["synced_at"], str) and bk1["synced_at"]

    # Nullable columns round-trip as None.
    bk3 = next(r for r in latest if r["business_key"] == "BK-3")
    assert bk3["supplier_code"] is None
    assert bk3["promised_on"] is None
    assert bk3["ordered_on"] == date(2026, 6, 25)
    assert bk3["open_units"] == 7.25

    # 3. Second sync (as-of 2026-06-30): only BK-1, now fully received.
    #    The stored row is replaced by the newer snapshot.
    assert repo.upsert_many(
        [
            _row(
                "BK-1",
                "ITEM-1",
                "SITE-1",
                0.0,
                AS_OF_SYNC_TWO,
                supplier_code="SUP-1",
                promised_on="2026-07-05",
                ordered_on="2026-06-20",
            )
        ]
    ) == {"inserted": 0, "updated": 1}

    latest = repo.list_latest()
    assert [r["business_key"] for r in latest] == ["BK-1"]
    assert latest[0]["open_units"] == 0.0
    assert latest[0]["as_of"].startswith("2026-06-30")

    # 4. An incoming row with an older as_of leaves the stored row
    #    unchanged and counts in neither summary bucket.
    assert repo.upsert_many(
        [
            _row(
                "BK-1",
                "ITEM-1",
                "SITE-1",
                999.0,
                AS_OF_SYNC_ONE,
                supplier_code="SUP-9",
            )
        ]
    ) == {"inserted": 0, "updated": 0}

    latest = repo.list_latest()
    assert [r["business_key"] for r in latest] == ["BK-1"]
    assert latest[0]["open_units"] == 0.0
    assert latest[0]["as_of"].startswith("2026-06-30")
    assert latest[0]["supplier_code"] == "SUP-1"

    # 5. The newer sync omitted BK-2 and BK-3, so list_latest() no
    #    longer returns those lines.
    keys = {r["business_key"] for r in repo.list_latest()}
    assert "BK-2" not in keys
    assert "BK-3" not in keys
