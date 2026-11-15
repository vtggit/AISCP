"""Issue #1064 proof: the sole-source codes behind the count.

AC-1 — the field: every supplier risk entry gains
``sole_source_item_codes``, the sorted item codes whose observations
in the 730-day window all come from that one supplier.  Its length
always equals ``sole_source_items`` and it is the empty list when
that count is 0.  No new read — the codes come from the same
observations the count is built from — and nothing is written.
Everything else is unchanged: every other field, the levels and the
order (high, then medium, then low, then ``sole_source_items``
descending, then ``supplier_code``).

AC-2 — the proof seeds lead-time observations (with ``promised_on``
set to the receipt day, so every promise is on time) and suppliers
the way test_issue1050_freeform.py does (fixed dates relative to the
server's date, raw INSERTs) and reads the route through the app's
test client:

  * Listed — supplier S-A alone supplies items ITEM-B and ITEM-A and
    shares ITEM-C with S-B.  Its ``sole_source_item_codes`` is
    ``["ITEM-A", "ITEM-B"]`` and its ``sole_source_items`` is 2.
  * Empty — S-B, which supplies only the shared ITEM-C, has an empty
    ``sole_source_item_codes``.
  * Matching counts — for every entry, the list's length equals
    ``sole_source_items``.
  * Hostile input — an observation stored out of band with a NULL
    item code feeds the supplier's lead-time stats (one more
    receipt) but counts for neither the count nor the codes: the
    route still answers 200 and every code is a string, so the NULL
    can never reach the sorted list or the typed model.
"""

from datetime import date, timedelta

from app.db.connection import get_connection

URL = "/api/supplier-risk"

LEAD_SQL = (
    "INSERT INTO lead_time_observations (id, business_key, supplier_code, "
    "item_code, ordered_on, promised_on, received_on, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
SUPPLIER_SQL = (
    "INSERT INTO suppliers (id, supplier_code, vendor_code, name, status, "
    "source_system) VALUES (%s, %s, %s, %s, %s, %s)"
)


def _receipt(cur, prefix, i, supplier, item, lead, received, promised) -> None:
    """One observed PO receipt: ordered ``lead`` days before it was
    received, promised on ``promised``."""
    cur.execute(
        LEAD_SQL,
        (
            f"id-{prefix}-{i}",
            f"bk-{prefix}-{i}",
            supplier,
            item,
            (received - timedelta(days=lead)).isoformat(),
            promised.isoformat(),
            received.isoformat(),
            "test",
        ),
    )


def _seed(cur, today: date) -> None:
    """The fixed-date rows, relative to the server's date.

    S-A (items ITEM-A, ITEM-B and ITEM-C): 2 steady 10-day receipts
    of ITEM-A, 2 steady 20-day receipts of ITEM-B and 2 steady
    30-day receipts of ITEM-C — ITEM-A and ITEM-B are its sole
    sources, ITEM-C is shared with S-B.
    S-B (item ITEM-C): 2 steady 30-day receipts — only the shared
    item, so no sole source.
    Every receipt lies inside the 730-day window and every promise
    is the receipt day itself, so every on_time_rate is 1.0 (no
    supplier is late or variable; both answer "low").
    """
    for i, days_ago in enumerate((11, 21)):
        received = today - timedelta(days=days_ago)
        _receipt(cur, "a-item-a", i, "S-A", "ITEM-A", 10, received, received)

    for i, days_ago in enumerate((31, 41)):
        received = today - timedelta(days=days_ago)
        _receipt(cur, "a-item-b", i, "S-A", "ITEM-B", 20, received, received)

    for i, days_ago in enumerate((51, 61)):
        received = today - timedelta(days=days_ago)
        _receipt(cur, "a-item-c", i, "S-A", "ITEM-C", 30, received, received)

    for i, days_ago in enumerate((71, 81)):
        received = today - timedelta(days=days_ago)
        _receipt(cur, "b-item-c", i, "S-B", "ITEM-C", 30, received, received)

    for supplier, vendor, name in (
        ("S-A", "V-A", "A Vendor"),
        ("S-B", "V-B", "B Vendor"),
    ):
        cur.execute(
            SUPPLIER_SQL,
            (f"id-sup-{supplier}", supplier, vendor, name, "Active", "test"),
        )


def _entry(body: list[dict], code: str) -> dict:
    entry = next((e for e in body if e["supplier_code"] == code), None)
    assert entry is not None, (code, body)
    return entry


def test_issue1064_freeform(client):
    today = date.today()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur, today)
        conn.commit()
    finally:
        conn.close()

    resp = client.get(URL)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The window's as-of: the earlier of today and the latest
    # receipt (the newest seeded receipt, 11 days ago).
    assert body["as_of"] == (today - timedelta(days=11)).isoformat()
    suppliers = body["suppliers"]
    # The order: within "low", sole_source_items descending, then
    # supplier_code — S-A (2 sole sources) before S-B (none).
    assert [e["supplier_code"] for e in suppliers] == ["S-A", "S-B"]

    # Matching counts: for every entry, the list's length equals
    # sole_source_items — and the codes are sorted.
    for entry in suppliers:
        codes = entry["sole_source_item_codes"]
        assert isinstance(codes, list)
        assert all(isinstance(code, str) for code in codes)
        assert codes == sorted(codes)
        assert len(codes) == entry["sole_source_items"], entry

    # Listed: S-A alone supplies ITEM-A and ITEM-B and shares
    # ITEM-C with S-B — both codes, sorted, behind the count of 2.
    a = _entry(suppliers, "S-A")
    assert a["sole_source_item_codes"] == ["ITEM-A", "ITEM-B"]
    assert a["sole_source_items"] == 2
    assert a["sole_source"] is True
    assert a["risk"] == "low"
    assert a["late"] is False
    assert a["variable"] is False
    assert a["receipts"] == 6
    assert a["on_time_rate"] == 1.0
    assert a["vendor_code"] == "V-A"
    assert a["supplier_name"] == "A Vendor"

    # Empty: S-B supplies only the shared ITEM-C — no sole source,
    # so the empty list.
    b = _entry(suppliers, "S-B")
    assert b["sole_source_item_codes"] == []
    assert b["sole_source_items"] == 0
    assert b["sole_source"] is False
    assert b["risk"] == "low"
    assert b["receipts"] == 2
    assert b["on_time_rate"] == 1.0
    assert b["vendor_code"] == "V-B"
    assert b["supplier_name"] == "B Vendor"

    # Hostile input: an observation stored out of band with a NULL
    # item code (the column is NOT NULL by the migration, so the
    # constraint is dropped just for this row and restored on the
    # way out).  The NULL is no item — it must not count as a sole
    # source of S-B, and it must never reach the sorted codes or
    # the typed list of strings (a 500 there).
    conn = get_connection()
    constraint_dropped = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "ALTER TABLE lead_time_observations "
                "ALTER COLUMN item_code DROP NOT NULL"
            )
        conn.commit()
        constraint_dropped = True
        with conn.cursor() as cur:
            received = today - timedelta(days=91)
            cur.execute(
                LEAD_SQL,
                (
                    "id-null-item",
                    "bk-null-item",
                    "S-B",
                    None,
                    (received - timedelta(days=30)).isoformat(),
                    received.isoformat(),
                    received.isoformat(),
                    "test",
                ),
            )
        conn.commit()

        # The route must survive the NULL item code: for every
        # entry the list's length equals sole_source_items and
        # every code is a string, so the NULL can never reach the
        # sorted list or the typed model.
        resp = client.get(URL)
        assert resp.status_code == 200, resp.text
        suppliers = resp.json()["suppliers"]
        for entry in suppliers:
            codes = entry["sole_source_item_codes"]
            assert codes == sorted(codes)
            assert len(codes) == entry["sole_source_items"], entry
            assert all(isinstance(code, str) for code in codes)

        # The NULL-code observation is not an item: S-B's count
        # stays 0 and its list stays empty — it only adds a
        # receipt — while S-A is untouched.
        b = _entry(suppliers, "S-B")
        assert b["sole_source_item_codes"] == []
        assert b["sole_source_items"] == 0
        assert b["sole_source"] is False
        assert b["receipts"] == 3
        a = _entry(suppliers, "S-A")
        assert a["sole_source_item_codes"] == ["ITEM-A", "ITEM-B"]
        assert a["sole_source_items"] == 2
    finally:
        # Restore the migration's NOT NULL constraint no matter
        # what: the NULL row goes first (SET NOT NULL would refuse
        # with it still present), then the constraint comes back —
        # the table is never left looser than the migration built
        # it.
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM lead_time_observations WHERE id = %s",
                ("id-null-item",),
            )
        conn.commit()
        if constraint_dropped:
            with conn.cursor() as cur:
                cur.execute(
                    "ALTER TABLE lead_time_observations "
                    "ALTER COLUMN item_code SET NOT NULL"
                )
            conn.commit()
        conn.close()
