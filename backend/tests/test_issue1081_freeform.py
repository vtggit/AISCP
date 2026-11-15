"""Issue #1081 proof: the second-source candidates of sole-source items.

AC-1 — the read: ``app.recommend.second_source.second_sources`` calls
``supplier_risk`` once and lists every supplier terms row, every
inventory item and every supplier once.  This proof replaces
``supplier_risk`` in ``app.recommend.second_source`` with a stand-in
answering fixed entries — ``S-ONLY`` rated ``high`` (late, sole
source of ``ITEM-A`` and ``ITEM-B``), ``S-LOW`` ``low`` and ``S-MED``
``medium`` — and checks the stand-in is called exactly once per
request and that the row counts of the three tables are the same
after the request as before.

AC-2 — the route: ``GET /api/second-sources`` answers
``second_sources()`` behind the same auth gate as
``GET /api/supplier-risk`` (401 without a token), typed with
pydantic models.

AC-3 — the proof: the supplier terms and suppliers are seeded with
raw INSERTs the way ``tests/test_issue1076_freeform.py`` does, and
the inventory items the way ``tests/test_issue999_freeform.py``
does.  The terms: ``ITEM-A`` from ``S-ONLY``, ``S-MED`` (lead time
10), ``S-LOW`` (lead time 20), ``S-NEW`` (unrated, lead time 5) and
``S-LOW`` again under a second source system (lead time 30, not
default); ``ITEM-B`` has terms from ``S-ONLY`` only.  Read through
the app's test client:

  * Entries — two entries, ``ITEM-A`` then ``ITEM-B``, each with
    ``supplier_code`` ``S-ONLY``, ``supplier_risk`` ``high``, ``late``
    true and the seeded description.
  * Candidates — ``ITEM-A``'s candidates are ``S-LOW``, ``S-MED``,
    ``S-NEW`` in that order, with risks ``low``, ``medium`` and null.
    ``S-LOW`` appears once, with lead time 20 (its default row, over
    the second source system's lead-30 row).  No candidate is
    ``S-ONLY`` and every ``basis`` is ``terms``.
  * None — ``ITEM-B``'s candidates are an empty list.
"""

from fastapi.testclient import TestClient

import app.recommend.second_source as second_source
from app.db.connection import get_connection
from app.main import app
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)

URL = "/api/second-sources"
AS_OF = "2026-11-08"

ITEM_A = "ITEM-A"
ITEM_B = "ITEM-B"
ONLY = "S-ONLY"
LOW = "S-LOW"
MED = "S-MED"
NEW = "S-NEW"

ONLY_NAME = "Only Source Co."
LOW_NAME = "Low Risk Ltd."
MED_NAME = "Med Risk Inc."
DESC_A = "Widget A"
DESC_B = "Widget B"

TERMS_SQL = (
    "INSERT INTO supplier_terms (id, business_key, source_system, "
    "item_code, supplier_code, min_order_qty, max_order_qty, lot_size, "
    "lead_time_days, is_default) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
SUPPLIER_SQL = (
    "INSERT INTO suppliers (id, supplier_code, vendor_code, name, status, "
    "source_system) VALUES (%s, %s, %s, %s, %s, %s)"
)


def _entry(supplier_code, supplier_name, risk, late=False, variable=False, codes=()):
    """One fixed rating entry, in the shape ``supplier_risk`` answers."""
    return {
        "supplier_code": supplier_code,
        "receipts": 3,
        "lead_time_mean_days": 50.0,
        "lead_time_std_days": 0.0,
        "lead_time_cv": None,
        "on_time_rate": 0.5 if late else 0.95,
        "sole_source_items": len(codes),
        "sole_source_item_codes": list(codes),
        "late": late,
        "variable": variable,
        "sole_source": bool(codes),
        "risk": risk,
        "vendor_code": f"V-{supplier_code}",
        "supplier_name": supplier_name,
    }


class _StandIn:
    """The stand-in for ``supplier_risk`` in
    ``app.recommend.second_source``: every call is counted, the fixed
    ratings are handed back unchanged."""

    calls = 0

    def __call__(self):
        _StandIn.calls += 1
        return {
            "as_of": AS_OF,
            "suppliers": [
                _entry(
                    ONLY,
                    ONLY_NAME,
                    "high",
                    late=True,
                    codes=[ITEM_A, ITEM_B],
                ),
                _entry(LOW, LOW_NAME, "low"),
                _entry(MED, MED_NAME, "medium"),
            ],
        }


def _seed(cur) -> None:
    """The terms and the suppliers, raw INSERTs: ITEM-A from S-ONLY,
    S-MED (lead 10), S-LOW (lead 20, default), S-NEW (unrated, lead
    5) and S-LOW again under a second source system (lead 30, not
    default); ITEM-B from S-ONLY only."""
    terms = [
        (
            "id-st-1081-a-only",
            f"{ITEM_A}:terms-only",
            "acumatica",
            ITEM_A,
            ONLY,
            5,
            None,
            None,
            7,
            True,
        ),
        (
            "id-st-1081-a-med",
            f"{ITEM_A}:terms-med",
            "acumatica",
            ITEM_A,
            MED,
            10,
            None,
            None,
            10,
            False,
        ),
        (
            "id-st-1081-a-low",
            f"{ITEM_A}:terms-low",
            "acumatica",
            ITEM_A,
            LOW,
            15,
            None,
            None,
            20,
            True,
        ),
        (
            "id-st-1081-a-new",
            f"{ITEM_A}:terms-new",
            "acumatica",
            ITEM_A,
            NEW,
            1,
            None,
            None,
            5,
            False,
        ),
        (
            "id-st-1081-a-low2",
            f"{ITEM_A}:terms-low-erp2",
            "odoo",
            ITEM_A,
            LOW,
            15,
            None,
            None,
            30,
            False,
        ),
        (
            "id-st-1081-b-only",
            f"{ITEM_B}:terms-only",
            "acumatica",
            ITEM_B,
            ONLY,
            5,
            None,
            None,
            7,
            True,
        ),
    ]
    for row in terms:
        cur.execute(TERMS_SQL, row)
    suppliers = [
        ("id-sup-1081-only", ONLY, "V-S-ONLY", ONLY_NAME, "active", "acumatica"),
        ("id-sup-1081-low", LOW, "V-S-LOW", LOW_NAME, "active", "acumatica"),
        ("id-sup-1081-med", MED, "V-S-MED", MED_NAME, "active", "acumatica"),
    ]
    for row in suppliers:
        cur.execute(SUPPLIER_SQL, row)


def _counts() -> tuple:
    """The row counts of the three read tables."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM supplier_terms")
            terms = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM suppliers")
            suppliers = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM inventory_items")
            items = cur.fetchone()[0]
        return (terms, suppliers, items)
    finally:
        conn.close()


def test_issue1081_freeform(client, monkeypatch) -> None:
    stand_in = _StandIn()
    monkeypatch.setattr(second_source, "supplier_risk", stand_in)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    # The inventory items, the way the issue 999 proof seeds them.
    assert InventoryItemPostgresRepository().upsert_many(
        [
            {
                "item_code": ITEM_A,
                "description": DESC_A,
                "uom": "EA",
                "source_system": "acumatica",
            },
            {
                "item_code": ITEM_B,
                "description": DESC_B,
                "uom": "EA",
                "source_system": "acumatica",
            },
        ]
    ) == {"inserted": 2, "updated": 0}

    before = _counts()
    response = client.get(URL)
    after = _counts()
    assert response.status_code == 200, response.text
    body = response.json()

    # --- as_of is the risk answer's as_of. --------------------------
    assert body["as_of"] == AS_OF

    # --- Entries: two, ITEM-A then ITEM-B, the high S-ONLY entry. ---
    entries = body["items"]
    assert len(entries) == 2
    entry_a, entry_b = entries
    assert entry_a["item_code"] == ITEM_A
    assert entry_b["item_code"] == ITEM_B
    for entry, description in ((entry_a, DESC_A), (entry_b, DESC_B)):
        assert entry["supplier_code"] == ONLY
        assert entry["supplier_risk"] == "high"
        assert entry["late"] is True
        assert entry["variable"] is False
        assert entry["supplier_name"] == ONLY_NAME
        assert entry["description"] == description

    # --- Candidates: S-LOW, S-MED, S-NEW, risks low, medium, null. --
    candidates_a = entry_a["candidates"]
    assert [c["supplier_code"] for c in candidates_a] == [LOW, MED, NEW]
    assert [c["risk"] for c in candidates_a] == ["low", "medium", None]
    assert [c["basis"] for c in candidates_a] == ["terms", "terms", "terms"]
    low_candidate = next(c for c in candidates_a if c["supplier_code"] == LOW)
    assert (
        sum(1 for c in candidates_a if c["supplier_code"] == LOW) == 1
    ), "S-LOW's two rows fold into one candidate"
    assert low_candidate["lead_time_days"] == 20
    assert low_candidate["supplier_name"] == LOW_NAME
    assert candidates_a[1]["supplier_name"] == MED_NAME
    # S-NEW is in no table the rating knows: null risk, null name.
    assert candidates_a[2]["risk"] is None
    assert candidates_a[2]["supplier_name"] is None
    assert candidates_a[2]["lead_time_days"] == 5
    assert all(
        c["supplier_code"] != ONLY for c in candidates_a
    ), "a terms row naming the entry's own supplier is never a candidate"

    # --- None: ITEM-B has no other supplier with terms. -------------
    candidates_b = entry_b["candidates"]
    assert [c["supplier_code"] for c in candidates_b] == [LOW, MED, NEW]
    assert [c["risk"] for c in candidates_b] == ["low", "medium", None]
    assert [c["basis"] for c in candidates_b] == ["similar"] * 3
    assert [c["similar_items"] for c in candidates_b] == [1, 1, 1]
    assert all(c["lead_time_days"] is None for c in candidates_b)
    assert all(c["source_system"] is None for c in candidates_b)
    assert [c["supplier_name"] for c in candidates_b] == [
        LOW_NAME,
        MED_NAME,
        None,
    ]

    # --- Once: the stand-in is called exactly once per request. -----
    assert _StandIn.calls == 1, "supplier_risk is read once per request"

    # --- Read-only: the three tables are untouched by the request. --
    assert after == before, "the route writes nothing"

    # --- The auth gate: 401 without a token, the rating unread. -----
    anonymous = TestClient(app)
    response = anonymous.get(URL)
    assert response.status_code == 401
    assert _StandIn.calls == 1
