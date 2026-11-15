"""Issue #1082 proof: the similar-item second-source candidates.

``GET /api/second-sources`` answers ``second_sources`` with the
entry's terms candidates first, then at most three similar
candidates: suppliers with at least one terms row, in the one terms
read, for a different item whose code starts with the same first N
characters as the entry's item code — N the whole-number part of the
stored ``second_source.similar_prefix_length`` setting, the registry
default (3) when nothing is stored, 0 turning similar suggestions
off.  The entry's own supplier and the entry's terms candidates are
never similar candidates; a similar candidate carries
``supplier_name`` found the way a terms candidate finds it, ``risk``
null when unrated, ``basis`` ``similar``, ``similar_items`` the
number of distinct such item codes, and null ``source_system``,
``lead_time_days`` and ``min_order_qty``.  They are ranked ``risk``
``low``, then ``medium``, then ``high``, then null, then
``similar_items`` descending, then ``supplier_code``.

This proof replaces ``supplier_risk`` in ``app.recommend.second_
source`` with a stand-in answering fixed entries — ``S-ONLY`` rated
``high`` and sole source of ``ELE-A``, ``S-LOW`` ``low``, ``S-HIGH``
``high``, ``S-MED`` ``medium``, ``S-NEW`` unrated (no entry) — and
seeds the terms, the suppliers and the inventory items the way
``tests/test_issue1081_freeform.py`` does.  The terms: ``ELE-A``
from ``S-ONLY`` only; ``ELE-B``, ``ELE-C`` and ``ELE-D`` from
``S-LOW``; ``ELE-B`` from ``S-HIGH``; ``ELE-C`` from ``S-NEW``;
``ELE-E`` from ``S-ONLY``; and ``IND-A`` from ``S-OTHER``.  Read
through the app's test client:

  * Similar — ``ELE-A``'s candidates are ``S-LOW`` (``similar_
    items`` 3), ``S-HIGH`` (1) then ``S-NEW`` (1), each with
    ``basis`` ``similar`` and a null ``lead_time_days``; ``S-ONLY``
    and ``S-OTHER`` are not among them.
  * Capped — with a fourth supplier ``S-MED``, rated ``medium``,
    given terms for ``ELE-B``, the list holds three, ``S-LOW``,
    ``S-MED`` and ``S-HIGH``, and ``S-NEW`` drops out.
  * After terms — with ``S-HIGH`` also given terms for ``ELE-A``,
    it appears once as a ``terms`` candidate first, with
    ``similar_items`` null, and is not repeated as similar.
  * Off — with the setting stored 0, ``ELE-A`` has no similar
    candidates.
  * Length — with the setting stored 5, no similar candidates
    appear, because ``ELE-A``'s first five characters match no other
    item code.
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

PREFIX_SETTING = "second_source.similar_prefix_length"

ELE_A = "ELE-A"
ELE_B = "ELE-B"
ELE_C = "ELE-C"
ELE_D = "ELE-D"
ELE_E = "ELE-E"
IND_A = "IND-A"

ONLY = "S-ONLY"
LOW = "S-LOW"
HIGH = "S-HIGH"
NEW = "S-NEW"
MED = "S-MED"
OTHER = "S-OTHER"

ONLY_NAME = "Only Source Co."
LOW_NAME = "Low Risk Ltd."
HIGH_NAME = "High Risk GmbH"
MED_NAME = "Med Risk Inc."
OTHER_NAME = "Other Source Ltd."

DESC_A = "Electro A"
DESC_B = "Electro B"
DESC_C = "Electro C"
DESC_D = "Electro D"
DESC_E = "Electro E"
DESC_I = "Industrial A"

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

TABLES = (
    "supplier_terms",
    "suppliers",
    "inventory_items",
    "settings",
    "settings_changes",
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
    ratings are handed back unchanged — S-ONLY high and sole source
    of ELE-A, S-LOW low, S-HIGH high, S-MED medium, S-NEW unrated."""

    calls = 0

    def __call__(self):
        _StandIn.calls += 1
        return {
            "as_of": AS_OF,
            "suppliers": [
                _entry(ONLY, ONLY_NAME, "high", late=True, codes=[ELE_A]),
                _entry(LOW, LOW_NAME, "low"),
                _entry(HIGH, HIGH_NAME, "high", late=True),
                _entry(MED, MED_NAME, "medium", variable=True),
            ],
        }


def _reset(cur) -> None:
    """An empty slate: the seeded tables and the settings rows."""
    for table in TABLES:
        cur.execute(f"TRUNCATE TABLE {table} CASCADE")


def _seed(cur, extra_terms=(), extra_suppliers=()) -> None:
    """The terms and the suppliers, raw INSERTs: ELE-A from S-ONLY
    only; ELE-B, ELE-C and ELE-D from S-LOW; ELE-B from S-HIGH;
    ELE-C from S-NEW; ELE-E from S-ONLY; and IND-A from S-OTHER,
    plus the scenario's extras.  S-NEW is in no suppliers row."""
    terms = [
        (
            "id-st-1082-a-only",
            f"{ELE_A}:terms-only",
            "acumatica",
            ELE_A,
            ONLY,
            5,
            None,
            None,
            7,
            True,
        ),
        (
            "id-st-1082-b-low",
            f"{ELE_B}:terms-low",
            "acumatica",
            ELE_B,
            LOW,
            5,
            None,
            None,
            10,
            False,
        ),
        (
            "id-st-1082-c-low",
            f"{ELE_C}:terms-low",
            "acumatica",
            ELE_C,
            LOW,
            5,
            None,
            None,
            12,
            False,
        ),
        (
            "id-st-1082-d-low",
            f"{ELE_D}:terms-low",
            "acumatica",
            ELE_D,
            LOW,
            5,
            None,
            None,
            14,
            False,
        ),
        (
            "id-st-1082-b-high",
            f"{ELE_B}:terms-high",
            "acumatica",
            ELE_B,
            HIGH,
            2,
            None,
            None,
            9,
            False,
        ),
        (
            "id-st-1082-c-new",
            f"{ELE_C}:terms-new",
            "acumatica",
            ELE_C,
            NEW,
            1,
            None,
            None,
            6,
            False,
        ),
        (
            "id-st-1082-e-only",
            f"{ELE_E}:terms-only",
            "acumatica",
            ELE_E,
            ONLY,
            5,
            None,
            None,
            8,
            False,
        ),
        (
            "id-st-1082-i-other",
            f"{IND_A}:terms-other",
            "acumatica",
            IND_A,
            OTHER,
            4,
            None,
            None,
            4,
            False,
        ),
    ]
    for row in [*terms, *extra_terms]:
        cur.execute(TERMS_SQL, row)
    suppliers = [
        ("id-sup-1082-only", ONLY, "V-S-ONLY", ONLY_NAME, "active", "acumatica"),
        ("id-sup-1082-low", LOW, "V-S-LOW", LOW_NAME, "active", "acumatica"),
        ("id-sup-1082-high", HIGH, "V-S-HIGH", HIGH_NAME, "active", "acumatica"),
        ("id-sup-1082-other", OTHER, "V-S-OTHER", OTHER_NAME, "active", "acumatica"),
    ]
    for row in [*suppliers, *extra_suppliers]:
        cur.execute(SUPPLIER_SQL, row)


def _store_items() -> None:
    """The inventory items, the way the issue 1081 proof seeds them."""
    assert InventoryItemPostgresRepository().upsert_many(
        [
            {
                "item_code": code,
                "description": description,
                "uom": "EA",
                "source_system": "acumatica",
            }
            for code, description in (
                (ELE_A, DESC_A),
                (ELE_B, DESC_B),
                (ELE_C, DESC_C),
                (ELE_D, DESC_D),
                (ELE_E, DESC_E),
                (IND_A, DESC_I),
            )
        ]
    ) == {"inserted": 6, "updated": 0}


def _store_prefix(value) -> None:
    """The stored ``second_source.similar_prefix_length``."""
    SettingsPostgresRepository().store(
        scope="system",
        owner="",
        key=PREFIX_SETTING,
        value=value,
        updated_by="dev-user-1",
        updated_by_name=None,
        old_value=None,
    )


def _scenario(client, extra_terms=(), extra_suppliers=(), prefix=None) -> dict:
    """One clean scenario: reset, seed, read the route through the
    app's test client, the stand-in read once per request."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _reset(cur)
            _seed(cur, extra_terms, extra_suppliers)
        conn.commit()
    finally:
        conn.close()
    _store_items()
    if prefix is not None:
        _store_prefix(prefix)
    calls_before = _StandIn.calls
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert _StandIn.calls == calls_before + 1, "supplier_risk is read once per request"
    body = response.json()
    assert body["as_of"] == AS_OF
    entries = body["items"]
    assert [e["item_code"] for e in entries] == [
        ELE_A
    ], "S-ONLY sole-sources ELE-A only"
    entry = entries[0]
    assert entry["supplier_code"] == ONLY
    assert entry["supplier_name"] == ONLY_NAME
    assert entry["supplier_risk"] == "high"
    assert entry["late"] is True
    assert entry["variable"] is False
    assert entry["description"] == DESC_A
    return entry


def test_issue1082_freeform(client, monkeypatch) -> None:
    monkeypatch.setattr(second_source, "supplier_risk", _StandIn())

    # --- Similar: the registry default (3) applies on a fresh DB. ---
    entry = _scenario(client)
    candidates = entry["candidates"]
    assert [c["supplier_code"] for c in candidates] == [LOW, HIGH, NEW]
    assert [c["basis"] for c in candidates] == ["similar"] * 3
    assert [c["similar_items"] for c in candidates] == [3, 1, 1]
    assert [c["risk"] for c in candidates] == ["low", "high", None]
    assert all(c["lead_time_days"] is None for c in candidates)
    assert all(c["source_system"] is None for c in candidates)
    assert all(c["min_order_qty"] is None for c in candidates)
    # S-LOW's three ELE terms rows fold into three similar items.
    assert candidates[0]["supplier_name"] == LOW_NAME
    assert candidates[1]["supplier_name"] == HIGH_NAME
    # S-NEW is in no suppliers row and no risk entry: name null.
    assert candidates[2]["risk"] is None
    assert candidates[2]["supplier_name"] is None
    assert ONLY not in {
        c["supplier_code"] for c in candidates
    }, "the entry's own supplier is never a similar candidate"
    assert OTHER not in {
        c["supplier_code"] for c in candidates
    }, "IND-A shares no prefix with ELE-A"

    # --- Capped: a fourth supplier, S-MED rated medium, given ---
    # --- terms for ELE-B — the list holds three and S-NEW drops out.
    entry = _scenario(
        client,
        extra_terms=[
            (
                "id-st-1082-b-med",
                f"{ELE_B}:terms-med",
                "acumatica",
                ELE_B,
                MED,
                3,
                None,
                None,
                11,
                False,
            ),
        ],
        extra_suppliers=[
            ("id-sup-1082-med", MED, "V-S-MED", MED_NAME, "active", "acumatica"),
        ],
    )
    candidates = entry["candidates"]
    assert [c["supplier_code"] for c in candidates] == [LOW, MED, HIGH]
    assert [c["basis"] for c in candidates] == ["similar"] * 3
    assert [c["similar_items"] for c in candidates] == [3, 1, 1]
    assert [c["risk"] for c in candidates] == ["low", "medium", "high"]
    assert NEW not in {
        c["supplier_code"] for c in candidates
    }, "unrated S-NEW drops out of the three"

    # --- After terms: S-HIGH given terms for ELE-A appears once as ---
    # --- a terms candidate first, similar_items null, no repeat. ----
    entry = _scenario(
        client,
        extra_terms=[
            (
                "id-st-1082-a-high",
                f"{ELE_A}:terms-high",
                "acumatica",
                ELE_A,
                HIGH,
                2,
                None,
                None,
                12,
                False,
            ),
        ],
    )
    candidates = entry["candidates"]
    assert [c["supplier_code"] for c in candidates] == [HIGH, LOW, NEW]
    assert sum(1 for c in candidates if c["supplier_code"] == HIGH) == 1
    first = candidates[0]
    assert first["basis"] == "terms"
    assert first["similar_items"] is None
    assert first["lead_time_days"] == 12
    assert first["min_order_qty"] == 2
    assert first["source_system"] == "acumatica"
    assert first["risk"] == "high"
    assert first["supplier_name"] == HIGH_NAME
    assert candidates[1]["basis"] == "similar"
    assert candidates[1]["similar_items"] == 3
    assert candidates[2]["basis"] == "similar"
    assert candidates[2]["similar_items"] == 1

    # --- Off: the setting stored 0 turns similar suggestions off. ---
    entry = _scenario(client, prefix=0)
    assert entry["candidates"] == []

    # --- Length: the setting stored 5 matches no other item code. --
    entry = _scenario(client, prefix=5)
    assert entry["candidates"] == []
