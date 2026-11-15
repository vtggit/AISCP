"""Issue #1091 proof: the item lead time bridge of the second-source
entries.

The proof replaces ``supplier_risk`` and ``recommendations_for_all``
in ``app.recommend.second_source`` with stand-ins answering fixed
values and seeds the terms, suppliers and inventory items the way
``tests/test_issue1083_freeform.py`` does: ``S-ONLY`` rated ``high``
and the sole source of ``A-ITEM``, ``B-ITEM`` and ``C-ITEM``;
``A-ITEM`` also has terms from ``S-ZERO`` (lead time 0); ``B-ITEM``
has terms from ``S-ALT`` (lead time 14); ``C-ITEM`` has no other
terms, and no two item codes share their first three characters, so
no similar candidates appear.  The recommendations stand-in answers
each item at two sites with ``inputs.d`` 2 and 2, ``inputs.lt`` 15.3
and 12, and ``on_hand_units`` 10 and 10.  Read through the app's
test client:

  * Item lead time — ``A-ITEM``'s only terms candidate has a lead
    time of 0, so the bridge is the item's own lead time, the
    largest ``inputs.lt`` above 0 (15.3), rounded up to a whole
    number of days: ``bridge_days`` 16, ``bridge_basis``
    ``item_lead_time``, ``bridge_units`` 64, ``bridge_short_units``
    44 and ``bridge_arithmetic`` ``4.0 a day x 16 days = 64 units;
    on hand 20, short 44``.
  * Candidate kept — ``B-ITEM``'s terms candidate has a lead time
    of 14, so ``bridge_days`` 14, ``bridge_basis``
    ``candidate_lead_time``.
  * Qualification kept — ``C-ITEM`` has no terms candidate, so
    ``bridge_days`` 60 (the registry default), ``bridge_basis``
    ``qualification_days``.
  * No lead time — with every ``inputs.lt`` of ``A-ITEM`` answered
    as null, ``A-ITEM`` has ``bridge_days`` 60 and ``bridge_basis``
    ``qualification_days``.
  * Failure — with the recommendations stand-in raising, the route
    answers 200 and the bridge fields stay null.
  * Once — each stand-in is called exactly once per request.
"""

import app.recommend.second_source as second_source
from app.db.connection import get_connection
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)

URL = "/api/second-sources"
AS_OF = "2026-11-08"

ITEM_A = "A-ITEM"
ITEM_B = "B-ITEM"
ITEM_C = "C-ITEM"
ONLY = "S-ONLY"
ZERO = "S-ZERO"
ALT = "S-ALT"

ONLY_NAME = "Only Source Co."
ZERO_NAME = "Zero Lead Co."
ALT_NAME = "Alt Source Inc."
DESC_A = "Widget A"
DESC_B = "Widget B"
DESC_C = "Widget C"

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


class _RiskStandIn:
    """The stand-in for ``supplier_risk`` in
    ``app.recommend.second_source``: every call is counted, the fixed
    ratings are handed back unchanged."""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return {
            "as_of": AS_OF,
            "suppliers": [
                _entry(
                    ONLY,
                    ONLY_NAME,
                    "high",
                    late=True,
                    codes=[ITEM_A, ITEM_B, ITEM_C],
                ),
                _entry(ZERO, ZERO_NAME, "low"),
                _entry(ALT, ALT_NAME, "medium"),
            ],
        }


def _recommendation_rows(item_a_lts: tuple = (15.3, 12)) -> list:
    """The fixed recommendations rows: every item at two sites,
    ``inputs.d`` 2 and 2, ``on_hand_units`` 10 and 10; ``A-ITEM``'s
    ``inputs.lt`` from ``item_a_lts`` (null for the no-lead-time
    scenario), the other items' ``inputs.lt`` 15.3 and 12."""
    lts = {ITEM_A: item_a_lts, ITEM_B: (15.3, 12), ITEM_C: (15.3, 12)}
    rows = []
    for item_code in (ITEM_A, ITEM_B, ITEM_C):
        for site_code, lead_time in (
            ("SITE-1", lts[item_code][0]),
            ("SITE-2", lts[item_code][1]),
        ):
            rows.append(
                {
                    "item_code": item_code,
                    "site_code": site_code,
                    "inputs": {"d": 2.0, "lt": lead_time},
                    "on_hand_units": 10,
                }
            )
    return rows


class _RecommendationsStandIn:
    """The stand-in for ``recommendations_for_all`` in
    ``app.recommend.second_source``: every call is counted, the fixed
    rows are handed back unchanged, or the read raises when told to
    fail."""

    def __init__(self):
        self.calls = 0
        self.rows = _recommendation_rows()
        self.raise_on_read = False

    def __call__(self):
        self.calls += 1
        if self.raise_on_read:
            raise RuntimeError("the recommendations read failed")
        return self.rows


def _seed(cur) -> None:
    """The terms and the suppliers, raw INSERTs the way the issue
    1083 proof seeds them: ``A-ITEM`` from ``S-ONLY`` and ``S-ZERO``
    (lead time 0); ``B-ITEM`` from ``S-ONLY`` and ``S-ALT`` (lead
    time 14); ``C-ITEM`` from ``S-ONLY`` only."""
    terms = [
        (
            "id-st-1091-a-only",
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
            "id-st-1091-a-zero",
            f"{ITEM_A}:terms-zero",
            "acumatica",
            ITEM_A,
            ZERO,
            5,
            None,
            None,
            0,
            False,
        ),
        (
            "id-st-1091-b-only",
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
        (
            "id-st-1091-b-alt",
            f"{ITEM_B}:terms-alt",
            "acumatica",
            ITEM_B,
            ALT,
            5,
            None,
            None,
            14,
            False,
        ),
        (
            "id-st-1091-c-only",
            f"{ITEM_C}:terms-only",
            "acumatica",
            ITEM_C,
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
        ("id-sup-1091-only", ONLY, "V-S-ONLY", ONLY_NAME, "active", "acumatica"),
        ("id-sup-1091-zero", ZERO, "V-S-ZERO", ZERO_NAME, "active", "acumatica"),
        ("id-sup-1091-alt", ALT, "V-S-ALT", ALT_NAME, "active", "acumatica"),
    ]
    for row in suppliers:
        cur.execute(SUPPLIER_SQL, row)


def test_issue1091_freeform(client, monkeypatch) -> None:
    risk = _RiskStandIn()
    recs = _RecommendationsStandIn()
    monkeypatch.setattr(second_source, "supplier_risk", risk)
    monkeypatch.setattr(second_source, "recommendations_for_all", recs)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    # The inventory items, the way the issue 1083 proof seeds them.
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
            {
                "item_code": ITEM_C,
                "description": DESC_C,
                "uom": "EA",
                "source_system": "acumatica",
            },
        ]
    ) == {"inserted": 3, "updated": 0}

    # --- Item lead time: A-ITEM's bridge from the item's lead time. -
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] == AS_OF
    entries = body["items"]
    assert [e["item_code"] for e in entries] == [ITEM_A, ITEM_B, ITEM_C]
    entry_a, entry_b, entry_c = entries

    # A-ITEM: its only terms candidate holds a lead time of 0, so
    # the bridge is the item's own lead time — the largest inputs.lt
    # above 0 (15.3), rounded up to 16 days.
    assert entry_a["daily_demand"] == 4.0
    assert entry_a["bridge_days"] == 16
    assert entry_a["bridge_basis"] == "item_lead_time"
    assert entry_a["bridge_units"] == 64
    assert entry_a["on_hand_units"] == 20
    assert entry_a["bridge_short_units"] == 44
    assert (
        entry_a["bridge_arithmetic"]
        == "4.0 a day x 16 days = 64 units; on hand 20, short 44"
    )
    assert [c["supplier_code"] for c in entry_a["candidates"]] == [ZERO]
    assert entry_a["candidates"][0]["lead_time_days"] == 0
    assert entry_a["candidates"][0]["basis"] == "terms"

    # --- Candidate kept: B-ITEM's bridge from its terms candidate. --
    assert entry_b["daily_demand"] == 4.0
    assert [c["supplier_code"] for c in entry_b["candidates"]] == [ALT]
    assert entry_b["candidates"][0]["lead_time_days"] == 14
    assert entry_b["candidates"][0]["basis"] == "terms"
    assert entry_b["bridge_days"] == 14
    assert entry_b["bridge_basis"] == "candidate_lead_time"

    # --- Qualification kept: C-ITEM has no terms candidate at all. -
    assert entry_c["candidates"] == []
    assert entry_c["daily_demand"] == 4.0
    assert entry_c["bridge_days"] == 60
    assert entry_c["bridge_basis"] == "qualification_days"

    # No two item codes share their first three characters, so no
    # similar candidates appear.
    for entry in entries:
        assert all(c["basis"] != "similar" for c in entry["candidates"])

    # --- Once: each stand-in is called exactly once per request. ----
    assert risk.calls == 1, "supplier_risk is read once per request"
    assert recs.calls == 1, "recommendations_for_all is read once per request"

    # --- No lead time: A-ITEM's inputs.lt answered null everywhere. -
    recs.rows = _recommendation_rows(item_a_lts=(None, None))
    response = client.get(URL)
    assert response.status_code == 200, response.text
    entry_a = response.json()["items"][0]
    assert entry_a["bridge_days"] == 60
    assert entry_a["bridge_basis"] == "qualification_days"
    assert risk.calls == 2
    assert recs.calls == 2

    # --- Failure: the recommendations read raises, the route still 200.
    recs.rows = _recommendation_rows()
    recs.raise_on_read = True
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [e["item_code"] for e in body["items"]] == [ITEM_A, ITEM_B, ITEM_C]
    for entry in body["items"]:
        assert entry["daily_demand"] is None
        assert entry["bridge_units"] is None
        assert entry["bridge_short_units"] is None
        assert entry["bridge_arithmetic"] is None
        assert entry["on_hand_units"] is None
    # The candidates survive the failure.
    assert [c["supplier_code"] for c in body["items"][0]["candidates"]] == [ZERO]
    assert [c["supplier_code"] for c in body["items"][1]["candidates"]] == [ALT]
    assert body["items"][2]["candidates"] == []
    assert body["items"][0]["bridge_basis"] == "qualification_days"
    assert body["items"][1]["bridge_basis"] == "candidate_lead_time"
    assert body["items"][2]["bridge_basis"] == "qualification_days"
    assert risk.calls == 3
    assert recs.calls == 3
