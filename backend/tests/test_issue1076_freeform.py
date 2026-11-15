"""Issue #1076 proof: the lower-risk supplier of the supply plan.

AC-1 — ``supply_plan.prefer_lower_risk_supplier`` is a registered
system setting (boolean, default true): proven in
``tests/test_issue274_freeform.py`` and
``tests/test_issue502_freeform.py``, which list every registered
setting.

AC-2 — the choice: with the setting on,
``app.api.supply_plan.build_supply_plan`` reads
``app.recommend.supplier_risk.supplier_risk`` once per call and keeps
each supplier's ``risk`` by ``supplier_code`` (a raised read builds
the plan exactly as with the setting off); when the default pick's
supplier (the ``is_default`` terms row of the item-site's
``source_system``, else the lowest ``supplier_code``) is rated
``high`` or ``medium`` and another terms row of the same item and
source system names a supplier rated strictly lower (``low`` below
``medium`` below ``high``), the line uses the lowest-rated such row
instead — ties to the row marked default, then the lowest
``supplier_code``; a supplier missing from the rating is never
switched to, and a default pick whose supplier is missing from the
rating, or rated ``low``, is never switched away from.  Everything
follows the chosen row (``supplier_code``, ``source_system``, terms,
lead time, the fitted quantity and the arrival date), the switched
line's ``supplier_reason`` reads ``Chosen over <name> (<Risk> risk:
<signs>)`` — the name the default pick's supplier name from the
suppliers table, else its code, the risk ``High`` or ``Medium``, the
signs ``late deliveries`` and/or ``variable lead times`` joined by
``, "`` — and with the setting off every line is exactly as today
(``supplier_reason`` null, the risk read never happens).

AC-3 — this proof: one due item-site with demand and two terms rows,
seeded the way ``tests/test_issue914_freeform.py`` seeds a plan (fixed
dates, raw INSERTs: 360 days of 0.2 a day for item A at site 159
(d 0.2, sd_d 0), three identical 50-day lead-time observations ->
reorder point 10, 0 on hand -> short by 10, the default EOQ order
quantity 18 units), ``S-RISKY`` marked default with a min order of 10
and ``S-SAFE`` with a min order of 25 (raw 18 fitted: 18 from
``S-RISKY``, 25 raised to ``S-SAFE``'s minimum).  It replaces
``supplier_risk`` in ``app.api.supply_plan`` with a stand-in
answering fixed ratings and reads ``GET /api/supply-plan``:

  * Switched — with ``S-RISKY`` rated ``high`` (late and variable)
    and ``S-SAFE`` rated ``low``, the line's ``supplier_code`` is
    ``S-SAFE``, its quantity follows ``S-SAFE``'s minimum (25, raised
    from the raw 18) and ``supplier_reason`` names ``S-RISKY``'s
    supplier name from the suppliers table with ``High risk: late
    deliveries, variable lead times`` — and ``supplier_risk`` is read
    exactly once per call.
  * Kept — with both rated ``high``, or ``S-SAFE`` missing from the
    rating, or ``S-RISKY`` rated ``low``, the line stays on
    ``S-RISKY`` (quantity 18, its own minimum never raised) and
    ``supplier_reason`` is null.
  * Setting off — with the setting stored false, the line stays on
    ``S-RISKY`` with a null ``supplier_reason`` and the stand-in is
    never called.
  * Failure — with the stand-in raising, the line stays on
    ``S-RISKY`` and the route answers 200.
"""

import json
from datetime import date, timedelta

from fastapi.testclient import TestClient

import app.api.supply_plan as supply_plan
from app.db.connection import get_connection
from app.main import app

URL = "/api/supply-plan"
ADMIN_TOKEN = "dev-secret-token:admin"

# The resolved as-of: the latest demand date, earlier than today.
AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
ARRIVES_ON = (AS_OF + timedelta(days=50)).isoformat()  # 0 due days + 50
FIRST_DAY = AS_OF - timedelta(days=359)
ITEM = "A1076"
SITE = "159"
SOURCE_SYSTEM = "test"
RISKY = "S-RISKY"
SAFE = "S-SAFE"
RISKY_NAME = "Risky Widgets Inc."
SYNC_AS_OF = "2026-09-30T00:00:00+00:00"

SETTING_KEY = "supply_plan.prefer_lower_risk_supplier"
# The switched reason: the default pick's supplier name from the
# suppliers table, its risk, its signs joined by ", ".
SWITCHED_REASON = (
    f"Chosen over {RISKY_NAME} (High risk: late deliveries, " "variable lead times)"
)

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
LEAD_SQL = (
    "INSERT INTO lead_time_observations (id, business_key, supplier_code, "
    "item_code, ordered_on, received_on, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
ITEM_SITE_SQL = (
    "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
    "reorder_point, safety_stock, max_qty, order_qty, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
ON_HAND_SQL = (
    "INSERT INTO on_hand (id, item_code, site_code, quantity_on_hand, "
    "as_of, source_system) VALUES (%s, %s, %s, %s, %s, %s)"
)
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
SETTING_SQL = (
    "INSERT INTO settings (scope, owner, key, value, updated_by) "
    "VALUES ('system', '', %s, %s::jsonb, 'test')"
)


def _entry(supplier_code, supplier_name, risk, late=False, variable=False):
    """One fixed rating entry, in the shape ``supplier_risk`` answers."""
    return {
        "supplier_code": supplier_code,
        "receipts": 3,
        "lead_time_mean_days": 50.0,
        "lead_time_std_days": 0.0,
        "lead_time_cv": None,
        "on_time_rate": 0.5 if late else 0.95,
        "sole_source_items": 0,
        "sole_source_item_codes": [],
        "late": late,
        "variable": variable,
        "sole_source": False,
        "risk": risk,
        "vendor_code": f"V-{supplier_code}",
        "supplier_name": supplier_name,
    }


class _StandIn:
    """The stand-in for ``supplier_risk`` in ``app.api.supply_plan``:
    every call is counted, the fixed ratings are handed back
    unchanged (or the error raised)."""

    calls = 0

    def __init__(self, ratings=None, error=None):
        self._ratings = ratings
        self._error = error

    def __call__(self):
        _StandIn.calls += 1
        if self._error is not None:
            raise self._error
        return {"as_of": AS_OF_STR, "suppliers": self._ratings}


def _seed(cur) -> None:
    """The fixed-date rows, the way the issue 914 proof seeds a plan:
    360 days of 0.2 a day (d 0.2, sd_d 0), three identical 50-day
    lead-time observations (lt 50, sd_lt 0) -> reorder point
    0.2 * 50 = 10; 0 on hand -> short by 10, due now; the default
    EOQ order quantity sqrt(2 * 365 * 0.2 * 50 / (0.25 * 100)) =
    17.09 -> 18 units, the raw quantity (above the suggested 10)."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-1076-{i}",
                f"de-1076-{i}",
                ITEM,
                SITE,
                0.2,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                SOURCE_SYSTEM,
            )
            for i in range(360)
        ],
    )
    base = date(2026, 7, 10)
    for i in range(3):
        ordered = base + timedelta(days=i)
        cur.execute(
            LEAD_SQL,
            (
                f"id-lt-1076-{i}",
                f"lt-1076-{i}",
                RISKY,
                ITEM,
                ordered.isoformat(),
                (ordered + timedelta(days=50)).isoformat(),
                SOURCE_SYSTEM,
            ),
        )
    cur.execute(
        ITEM_SITE_SQL,
        (
            "id-is-1076",
            ITEM,
            SITE,
            100.0,
            10.0,
            0.0,
            None,
            None,
            SOURCE_SYSTEM,
        ),
    )
    cur.execute(
        ON_HAND_SQL,
        ("id-oh-1076", ITEM, SITE, 0.0, SYNC_AS_OF, SOURCE_SYSTEM),
    )
    # The default pick: S-RISKY, min order 10; the other terms row:
    # S-SAFE, min order 25.  Raw 18 fitted: 18 (never raised) from
    # S-RISKY, 25 (raised to the minimum) from S-SAFE.
    cur.execute(
        TERMS_SQL,
        (
            "id-st-1076-risky",
            f"{ITEM}:terms-risky",
            SOURCE_SYSTEM,
            ITEM,
            RISKY,
            10,
            None,
            None,
            None,
            True,
        ),
    )
    cur.execute(
        TERMS_SQL,
        (
            "id-st-1076-safe",
            f"{ITEM}:terms-safe",
            SOURCE_SYSTEM,
            ITEM,
            SAFE,
            25,
            None,
            None,
            None,
            False,
        ),
    )
    cur.execute(
        SUPPLIER_SQL,
        (
            "id-sp-1076-risky",
            RISKY,
            "V-RISKY",
            RISKY_NAME,
            "active",
            SOURCE_SYSTEM,
        ),
    )
    cur.execute(
        SUPPLIER_SQL,
        (
            "id-sp-1076-safe",
            SAFE,
            "V-SAFE",
            "Safe Springs Ltd.",
            "active",
            SOURCE_SYSTEM,
        ),
    )


def _line(quantity, adjustments, min_order, value, arithmetic, reason=None):
    """The plan's one line, everything following the chosen terms row
    (the raw quantity is 18 in every case; the fitted quantity, the
    terms, the value and the arithmetic follow the chosen row)."""
    line = {
        "item_code": ITEM,
        "site_code": SITE,
        "position_units": 0,
        "reorder_point_units": 10,
        # d 0.2 a day, in the demand statistics' float.
        "daily_demand": 0.20000000000000004,
        "due_in_days": 0,
        "order_by": AS_OF_STR,
        "lead_time_days": 50.0,
        "arrives_on": ARRIVES_ON,
        "raw_quantity": 18,
        "quantity": quantity,
        "adjustments": adjustments,
        "short_of_need": False,
        "terms": {
            "min_order_qty": float(min_order),
            "lot_size": None,
            "max_order_qty": None,
        },
        "unit_cost": 100.0,
        "value": value,
        "arithmetic": arithmetic,
    }
    if reason is not None:
        line["supplier_reason"] = reason
    return line


def _body(supplier_code, vendor_code, name, total_value, line):
    """The full expected answer: the one line under the chosen
    supplier's group."""
    return {
        "as_of": AS_OF_STR,
        "horizon_days": 7,
        "line_count": 1,
        "total_value": total_value,
        "suppliers": [
            {
                "supplier_code": supplier_code,
                "source_system": SOURCE_SYSTEM,
                "vendor_code": vendor_code,
                "name": name,
                "line_count": 1,
                "total_value": total_value,
                "lines": [line],
            }
        ],
    }


# The default pick stands: S-RISKY's terms, the raw 18 never raised,
# the value 18 x 100.00, and no supplier_reason at all (null).
KEPT_BODY = _body(
    RISKY,
    "V-RISKY",
    RISKY_NAME,
    "1800.00",
    _line(18, [], 10, "1800.00", "raw 18 (EOQ): 18"),
)
# The switched line: S-SAFE's terms, the raw 18 raised to S-SAFE's
# minimum 25, the value 25 x 100.00, and the reason naming the
# default pick's supplier.
SWITCHED_BODY = _body(
    SAFE,
    "V-SAFE",
    "Safe Springs Ltd.",
    "2500.00",
    _line(
        25,
        ["raised_to_minimum"],
        25,
        "2500.00",
        "raw 18 (EOQ) → raised to minimum 25: 25",
        SWITCHED_REASON,
    ),
)


def _store_setting(cur, value) -> None:
    cur.execute("DELETE FROM settings WHERE key = %s", (SETTING_KEY,))
    cur.execute(SETTING_SQL, (SETTING_KEY, json.dumps(value)))


def test_issue1076_freeform(client, monkeypatch) -> None:
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # --- Switched: S-RISKY high (late and variable), S-SAFE low. ----
    _StandIn.calls = 0
    monkeypatch.setattr(
        supply_plan,
        "supplier_risk",
        _StandIn(
            [
                _entry(RISKY, RISKY_NAME, "high", late=True, variable=True),
                _entry(SAFE, "Safe Springs Ltd.", "low"),
            ]
        ),
    )
    response = admin.get(URL, params={"horizon_days": 7})
    assert response.status_code == 200, response.text
    assert response.json() == SWITCHED_BODY
    assert _StandIn.calls == 1, "supplier_risk is read once per call"
    group = response.json()["suppliers"][0]
    assert group["supplier_code"] == SAFE
    assert group["lines"][0]["supplier_reason"] == SWITCHED_REASON

    # --- Kept: both rated high — no strictly lower row. --------------
    _StandIn.calls = 0
    monkeypatch.setattr(
        supply_plan,
        "supplier_risk",
        _StandIn(
            [
                _entry(RISKY, RISKY_NAME, "high", late=True, variable=True),
                _entry(SAFE, "Safe Springs Ltd.", "high", late=True),
            ]
        ),
    )
    response = admin.get(URL, params={"horizon_days": 7})
    assert response.status_code == 200, response.text
    assert response.json() == KEPT_BODY
    assert _StandIn.calls == 1
    assert response.json()["suppliers"][0]["lines"][0].get("supplier_reason") is None

    # --- Kept: S-SAFE missing from the rating — never switched to. ---
    _StandIn.calls = 0
    monkeypatch.setattr(
        supply_plan,
        "supplier_risk",
        _StandIn([_entry(RISKY, RISKY_NAME, "high", late=True, variable=True)]),
    )
    response = admin.get(URL, params={"horizon_days": 7})
    assert response.status_code == 200, response.text
    assert response.json() == KEPT_BODY
    assert _StandIn.calls == 1
    assert response.json()["suppliers"][0]["lines"][0].get("supplier_reason") is None

    # --- Kept: S-RISKY rated low — never switched away from. ---------
    _StandIn.calls = 0
    monkeypatch.setattr(
        supply_plan,
        "supplier_risk",
        _StandIn(
            [
                _entry(RISKY, RISKY_NAME, "low"),
                _entry(SAFE, "Safe Springs Ltd.", "low"),
            ]
        ),
    )
    response = admin.get(URL, params={"horizon_days": 7})
    assert response.status_code == 200, response.text
    assert response.json() == KEPT_BODY
    assert _StandIn.calls == 1
    assert response.json()["suppliers"][0]["lines"][0].get("supplier_reason") is None

    # --- Setting off: stored false — the line is exactly as today,
    #     and the risk read never happens. ----------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _store_setting(cur, False)
        conn.commit()
    finally:
        conn.close()
    _StandIn.calls = 0
    monkeypatch.setattr(
        supply_plan,
        "supplier_risk",
        _StandIn(
            [
                _entry(RISKY, RISKY_NAME, "high", late=True, variable=True),
                _entry(SAFE, "Safe Springs Ltd.", "low"),
            ]
        ),
    )
    response = admin.get(URL, params={"horizon_days": 7})
    assert response.status_code == 200, response.text
    assert response.json() == KEPT_BODY
    assert _StandIn.calls == 0, "the risk read never happens with the setting off"
    assert response.json()["suppliers"][0]["lines"][0].get("supplier_reason") is None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM settings WHERE key = %s", (SETTING_KEY,))
        conn.commit()
    finally:
        conn.close()

    # --- Failure: the stand-in raising — the plan is built exactly as
    #     with the setting off, the route answers 200. ----------------
    _StandIn.calls = 0
    monkeypatch.setattr(
        supply_plan,
        "supplier_risk",
        _StandIn(error=RuntimeError("the risk rating read failed")),
    )
    response = admin.get(URL, params={"horizon_days": 7})
    assert response.status_code == 200, response.text
    assert response.json() == KEPT_BODY
    assert _StandIn.calls == 1, "the rating is still read once per call"
    assert response.json()["suppliers"][0]["lines"][0].get("supplier_reason") is None
