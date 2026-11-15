"""Issue #1083 proof: the bridge stock of the second-source entries.

The proof replaces ``supplier_risk`` and ``recommendations_for_all``
in ``app.recommend.second_source`` with stand-ins answering fixed
values and seeds the terms, suppliers and inventory items the way
``tests/test_issue1081_freeform.py`` does: ``S-ONLY`` rated ``high``
and the sole source of ``ITEM-A`` and ``ITEM-B``; ``ITEM-A`` also has
terms from ``S-ZERO`` (rated ``low``, lead time 0) and from
``S-ALT`` (rated ``medium``, lead time 14); ``ITEM-B`` has no other
terms.  The recommendations stand-in answers ``ITEM-A`` at two sites
with ``inputs.d`` 2.5 and 1.5 and ``on_hand_units`` 20 and 10, and
``ITEM-B`` at one site with ``inputs.d`` 1 and ``on_hand_units``
100.  Read through the app's test client:

  * Lead time — ``ITEM-A`` has ``daily_demand`` 4.0,
    ``bridge_days`` 14, ``bridge_basis`` ``candidate_lead_time``,
    ``bridge_units`` 56, ``on_hand_units`` 30,
    ``bridge_short_units`` 26 and ``bridge_arithmetic`` ``4.0 a day
    x 14 days = 56 units; on hand 30, short 26``.
  * Zero skipped — ``S-ZERO`` stays first among ``ITEM-A``'s
    candidates, but its lead time of 0 is not used for the bridge.
  * Qualification — ``ITEM-B`` has no terms candidate with a lead
    time, so ``bridge_days`` is the
    ``second_source.qualification_days`` setting (the registry
    default 60 while nothing is stored), ``bridge_basis``
    ``qualification_days``, ``bridge_units`` 60 and
    ``bridge_short_units`` 0 (100 on hand); with the setting stored
    90, ``bridge_days`` 90 and ``bridge_units`` 90.
  * No demand — with the recommendations stand-in answering no
    ``ITEM-B`` row, ``ITEM-B`` has ``daily_demand``,
    ``bridge_units``, ``bridge_short_units`` and
    ``bridge_arithmetic`` null and ``on_hand_units`` 0.
  * Failure — with the recommendations stand-in raising, the route
    answers 200, both entries keep their candidates, and their
    ``daily_demand``, ``bridge_units``, ``bridge_short_units``,
    ``bridge_arithmetic`` and ``on_hand_units`` are null.
  * Once — each stand-in is called exactly once per request.
"""

import app.recommend.second_source as second_source
from app.db.connection import get_connection
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.settings_postgres_repository import (
    SettingsPostgresRepository,
)

URL = "/api/second-sources"
AS_OF = "2026-11-08"

ITEM_A = "ITEM-A"
ITEM_B = "ITEM-B"
ONLY = "S-ONLY"
ZERO = "S-ZERO"
ALT = "S-ALT"

ONLY_NAME = "Only Source Co."
ZERO_NAME = "Zero Lead Co."
ALT_NAME = "Alt Source Inc."
DESC_A = "Widget A"
DESC_B = "Widget B"

QUALIFICATION_KEY = "second_source.qualification_days"

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
                    codes=[ITEM_A, ITEM_B],
                ),
                _entry(ZERO, ZERO_NAME, "low"),
                _entry(ALT, ALT_NAME, "medium"),
            ],
        }


def _recommendation_rows(with_item_b: bool = True) -> list:
    """The fixed recommendations rows: ITEM-A at two sites (inputs.d
    2.5 and 1.5, on hand 20 and 10), ITEM-B at one site (inputs.d 1,
    on hand 100) unless the scenario drops it."""
    rows = [
        {
            "item_code": ITEM_A,
            "site_code": "SITE-1",
            "inputs": {"d": 2.5},
            "on_hand_units": 20,
        },
        {
            "item_code": ITEM_A,
            "site_code": "SITE-2",
            "inputs": {"d": 1.5},
            "on_hand_units": 10,
        },
    ]
    if with_item_b:
        rows.append(
            {
                "item_code": ITEM_B,
                "site_code": "SITE-1",
                "inputs": {"d": 1.0},
                "on_hand_units": 100,
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
    """The terms and the suppliers, raw INSERTs: ITEM-A from S-ONLY,
    S-ZERO (lead time 0) and S-ALT (lead time 14); ITEM-B from S-ONLY
    only."""
    terms = [
        (
            "id-st-1083-a-only",
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
            "id-st-1083-a-zero",
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
            "id-st-1083-a-alt",
            f"{ITEM_A}:terms-alt",
            "acumatica",
            ITEM_A,
            ALT,
            5,
            None,
            None,
            14,
            False,
        ),
        (
            "id-st-1083-b-only",
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
        ("id-sup-1083-only", ONLY, "V-S-ONLY", ONLY_NAME, "active", "acumatica"),
        ("id-sup-1083-zero", ZERO, "V-S-ZERO", ZERO_NAME, "active", "acumatica"),
        ("id-sup-1083-alt", ALT, "V-S-ALT", ALT_NAME, "active", "acumatica"),
    ]
    for row in suppliers:
        cur.execute(SUPPLIER_SQL, row)


def test_issue1083_freeform(client, monkeypatch) -> None:
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

    # The inventory items, the way the issue 1081 proof seeds them.
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

    # --- Lead time: ITEM-A's bridge from its first positive lead time.
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] == AS_OF
    entries = body["items"]
    assert [e["item_code"] for e in entries] == [ITEM_A, ITEM_B]
    entry_a, entry_b = entries

    assert entry_a["daily_demand"] == 4.0
    assert entry_a["bridge_days"] == 14
    assert entry_a["bridge_basis"] == "candidate_lead_time"
    assert entry_a["bridge_units"] == 56
    assert entry_a["on_hand_units"] == 30
    assert entry_a["bridge_short_units"] == 26
    assert (
        entry_a["bridge_arithmetic"]
        == "4.0 a day x 14 days = 56 units; on hand 30, short 26"
    )

    # --- Zero skipped: S-ZERO stays first, its 0 never used. --------
    candidates_a = entry_a["candidates"]
    assert candidates_a[0]["supplier_code"] == ZERO
    assert candidates_a[0]["lead_time_days"] == 0
    assert candidates_a[0]["basis"] == "terms"
    assert [c["supplier_code"] for c in candidates_a] == [ZERO, ALT]
    assert (
        entry_a["bridge_days"] != 0
    ), "a lead time of 0 means the ERP holds none and never counts"

    # --- Qualification: ITEM-B's bridge from the registry default. --
    assert entry_b["daily_demand"] == 1.0
    assert entry_b["bridge_days"] == 60
    assert entry_b["bridge_basis"] == "qualification_days"
    assert entry_b["bridge_units"] == 60
    assert entry_b["on_hand_units"] == 100
    assert entry_b["bridge_short_units"] == 0
    assert (
        entry_b["bridge_arithmetic"]
        == "1.0 a day x 60 days = 60 units; on hand 100, short 0"
    )

    # --- Once: each stand-in is called exactly once per request. ----
    assert risk.calls == 1, "supplier_risk is read once per request"
    assert recs.calls == 1, "recommendations_for_all is read once per request"

    # --- Stored setting: 90 days to qualify. -------------------------
    SettingsPostgresRepository().store(
        scope="system",
        owner="",
        key=QUALIFICATION_KEY,
        value=90,
        updated_by="dev-user-1",
        updated_by_name=None,
        old_value=None,
    )
    response = client.get(URL)
    assert response.status_code == 200, response.text
    entry_b = response.json()["items"][1]
    assert entry_b["bridge_days"] == 90
    assert entry_b["bridge_basis"] == "qualification_days"
    assert entry_b["bridge_units"] == 90
    assert entry_b["bridge_short_units"] == 0
    assert risk.calls == 2
    assert recs.calls == 2

    # --- No demand: no ITEM-B row in the recommendations read. -------
    recs.rows = _recommendation_rows(with_item_b=False)
    response = client.get(URL)
    assert response.status_code == 200, response.text
    entry_b = response.json()["items"][1]
    assert entry_b["daily_demand"] is None
    assert entry_b["bridge_units"] is None
    assert entry_b["bridge_short_units"] is None
    assert entry_b["bridge_arithmetic"] is None
    assert entry_b["on_hand_units"] == 0
    # The bridge days still come from the stored setting.
    assert entry_b["bridge_days"] == 90
    assert entry_b["bridge_basis"] == "qualification_days"
    assert risk.calls == 3
    assert recs.calls == 3

    # --- Failure: the recommendations read raises, the route still 200.
    recs.raise_on_read = True
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [e["item_code"] for e in body["items"]] == [ITEM_A, ITEM_B]
    for entry in body["items"]:
        assert entry["candidates"], "the candidates survive the failure"
        assert entry["daily_demand"] is None
        assert entry["bridge_units"] is None
        assert entry["bridge_short_units"] is None
        assert entry["bridge_arithmetic"] is None
        assert entry["on_hand_units"] is None
    assert body["items"][0]["candidates"][0]["supplier_code"] == ZERO
    assert risk.calls == 4
    assert recs.calls == 4
