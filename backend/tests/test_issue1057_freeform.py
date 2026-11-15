"""Issue #1057 proof: the supplier risk variable sign needs evidence.

AC-1 — the rating: ``variable`` is true only when the supplier's
lead-time stats are not low confidence (at least 3 used
observations) and the rounded ``lead_time_cv`` is not null and at
least ``supplier_risk.lead_time_cv_ceiling`` (stored value, else
the 0.6 default); otherwise false.  The level: ``high`` when the
supplier is ``late`` or ``variable`` and also ``sole_source``,
``medium`` when it is ``late`` or ``variable`` without being
``sole_source``, and ``low`` when it is neither ``late`` nor
``variable``, whether or not it is ``sole_source``.  The default
ceiling is 0.6 with the unchanged range and description; the
module and route descriptions say so and
``backend/openapi.json`` follows.  Unchanged: every field, the
order (high, medium, low, then ``sole_source_items`` descending,
then ``supplier_code``), the ``late`` and ``sole_source`` signs
and the 0.85 on-time floor.

AC-2 — the proof seeds lead-time observations (``promised_on`` set
to the receipt day, so every promise is on time) and suppliers the
way test_issue1050_freeform.py does (fixed dates relative to the
server's date, raw INSERTs) and reads the route through the app's
test client:

  * Sole source alone is low — supplier S-STEADY is on time with
    steady 15-day lead times and the only supplier of item
    ONLY-STEADY.  It answers "low" with ``sole_source`` true.
  * Reliability plus sole source is high — supplier S-VAR has
    widely varying lead times over 8 receipts (10 and 70 days
    alternating, cv 0.80) and is the only supplier of item
    ONLY-VAR.  It answers "high".
  * Reliability alone is medium — supplier S-VAR-B carries the
    same varying pattern on SHARED-VAR, sharing all its items with
    S-PARTNER.  It answers "medium".
  * Too few receipts — supplier S-FEW has 2 receipts of very
    different lead times (5 and 95 days, displayed cv 1.27) and
    answers ``variable`` false: the stats are low confidence.
  * The default — with no stored value, S-CV55's displayed cv
    0.55 is not variable and S-CV65's 0.65 is.

AC-3 — the flag follows the same rounded lead_time_cv the route
returns: S-AT's unrounded cv (~0.5987) displays as exactly 0.60,
at the 0.6 default ceiling, and is variable, while S-BELOW's
displayed 0.50 is not.
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

import app.recommend.supplier_risk as risk_module
from app.api.supplier_risk import get_supplier_risk
from app.db.connection import get_connection, get_cursor
from app.main import app
from app.recommend.supplier_risk import supplier_risk
from app.settings.registry import get_setting

URL = "/api/supplier-risk"

_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

FLOOR_KEY = "supplier_risk.on_time_floor"
CEILING_KEY = "supplier_risk.lead_time_cv_ceiling"

LEAD_SQL = (
    "INSERT INTO lead_time_observations (id, business_key, supplier_code, "
    "item_code, ordered_on, promised_on, received_on, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
SUPPLIER_SQL = (
    "INSERT INTO suppliers (id, supplier_code, vendor_code, name, status, "
    "source_system) VALUES (%s, %s, %s, %s, %s, %s)"
)

ENTRY_FIELDS = {
    "supplier_code",
    "receipts",
    "lead_time_mean_days",
    "lead_time_std_days",
    "lead_time_cv",
    "on_time_rate",
    "sole_source_items",
    "sole_source_item_codes",
    "late",
    "variable",
    "sole_source",
    "risk",
    "vendor_code",
    "supplier_name",
}


def _flat(text: str) -> str:
    """The text with every run of whitespace folded to one space."""
    return re.sub(r"\s+", " ", text or "")


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

    Every receipt lies inside both the 730-day window and the
    Suppliers screen's default 365-day window, and every promise is
    the receipt day itself, so every on_time_rate is 1.0 (no
    supplier is late).

    S-STEADY (item ONLY-STEADY, sole source): 6 steady 15-day
    receipts — sole source alone is low.
    S-VAR (item ONLY-VAR, sole source): 8 receipts alternating 10
    and 70 days (mean 40, sample stdev ~32.07, cv 0.80) — variable
    plus sole source is high.
    S-VAR-B (item SHARED-VAR, shared with S-PARTNER): the same
    varying pattern — variable alone is medium.
    S-FEW (item SHARED-FEW, shared with S-PARTNER): only 2
    receipts, 5 and 95 days (displayed cv 1.27) — low confidence,
    not variable.
    S-CV55 (item ONLY-CV55, sole source): 4 receipts alternating
    52 and 148 (mean 100, cv 0.55) — not variable at the 0.6
    default ceiling.
    S-CV65 (item ONLY-CV65, sole source): 4 receipts alternating
    44 and 156 (mean 100, cv 0.65) — variable at the 0.6 default
    ceiling.
    S-AT (item ONLY-AT, sole source): 8 receipts alternating 44
    and 156 (mean 100, unrounded cv ~0.5987) — the displayed
    lead_time_cv is exactly 0.60, at the ceiling: variable.
    S-BELOW (item ONLY-BELOW, sole source): 4 receipts alternating
    57 and 143 (mean 100, cv 0.50) — not variable at the 0.6
    default ceiling.
    S-PARTNER (items SHARED-VAR and SHARED-FEW, both shared): 4
    steady 12-day receipts — no sign.
    """
    for i in range(6):
        received = today - timedelta(days=151 - 20 * i)
        _receipt(cur, "steady", i, "S-STEADY", "ONLY-STEADY", 15, received, received)

    for i in range(8):
        received = today - timedelta(days=211 - 20 * i)
        lead = 10 if i % 2 == 0 else 70
        _receipt(cur, "var", i, "S-VAR", "ONLY-VAR", lead, received, received)

    for i in range(8):
        received = today - timedelta(days=231 - 20 * i)
        lead = 10 if i % 2 == 0 else 70
        _receipt(cur, "varb", i, "S-VAR-B", "SHARED-VAR", lead, received, received)

    for i, (item, days_ago) in enumerate(
        (
            ("SHARED-VAR", 241),
            ("SHARED-VAR", 211),
            ("SHARED-FEW", 181),
            ("SHARED-FEW", 151),
        )
    ):
        received = today - timedelta(days=days_ago)
        _receipt(cur, f"partner-{i}", i, "S-PARTNER", item, 12, received, received)

    for i, lead in enumerate((5, 95)):
        received = today - timedelta(days=261 - 20 * i)
        _receipt(cur, "few", i, "S-FEW", "SHARED-FEW", lead, received, received)

    for i in range(4):
        received = today - timedelta(days=281 - 20 * i)
        lead = 52 if i % 2 == 0 else 148
        _receipt(cur, "cv55", i, "S-CV55", "ONLY-CV55", lead, received, received)

    for i in range(4):
        received = today - timedelta(days=301 - 20 * i)
        lead = 44 if i % 2 == 0 else 156
        _receipt(cur, "cv65", i, "S-CV65", "ONLY-CV65", lead, received, received)

    for i in range(8):
        received = today - timedelta(days=181 - 20 * i)
        lead = 44 if i % 2 == 0 else 156
        _receipt(cur, "at", i, "S-AT", "ONLY-AT", lead, received, received)

    for i in range(4):
        received = today - timedelta(days=121 - 20 * i)
        lead = 57 if i % 2 == 0 else 143
        _receipt(cur, "below", i, "S-BELOW", "ONLY-BELOW", lead, received, received)

    for supplier, vendor, name in (
        ("S-AT", "V-AT", "At Vendor"),
        ("S-BELOW", "V-BELOW", "Below Vendor"),
        ("S-CV55", "V-CV55", "Cv55 Vendor"),
        ("S-CV65", "V-CV65", "Cv65 Vendor"),
        ("S-FEW", "V-FEW", "Few Vendor"),
        ("S-PARTNER", "V-PARTNER", "Partner Vendor"),
        ("S-STEADY", "V-STEADY", "Steady Vendor"),
        ("S-VAR", "V-VAR", "Var Vendor"),
        ("S-VAR-B", "V-VARB", "Var B Vendor"),
    ):
        cur.execute(
            SUPPLIER_SQL,
            (f"id-sup-{supplier}", supplier, vendor, name, "Active", "test"),
        )


def _entry(body: list[dict], code: str) -> dict:
    entry = next((e for e in body if e["supplier_code"] == code), None)
    assert entry is not None, (code, body)
    return entry


def test_issue1057_freeform(client):
    today = date.today()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur, today)
        conn.commit()
    finally:
        conn.close()

    # The default ceiling: 0.6, with the unchanged range and
    # description, and the unchanged 0.85 on-time floor.
    ceiling = get_setting(CEILING_KEY)
    assert ceiling is not None
    assert ceiling.default == 0.6
    assert ceiling.min == 0
    assert ceiling.max == 5
    assert (
        ceiling.description
        == "Above this lead-time variation (standard deviation over mean) a supplier counts as variable."
    )
    floor = get_setting(FLOOR_KEY)
    assert floor is not None
    assert floor.default == 0.85

    # Documented: the module and route descriptions say so, and
    # backend/openapi.json follows.
    schema = json.loads(_OPENAPI.read_text())
    assert "/api/supplier-risk" in schema["paths"]
    assert "SupplierRiskResponse" in schema["components"]["schemas"]
    assert "SupplierRiskEntry" in schema["components"]["schemas"]
    route_description = schema["paths"]["/api/supplier-risk"]["get"]["description"]
    for doc in (
        risk_module.__doc__,
        get_supplier_risk.__doc__,
        route_description,
    ):
        flat = _flat(doc)
        assert "at least three used observations" in flat, flat
        assert "0.6" in flat, flat
        assert "0.60 is variable" in flat, flat

    # The default: with no stored value the ceiling is the
    # registry default.
    with get_cursor() as cur:
        cur.execute("SELECT key FROM settings " "WHERE scope = 'system' AND owner = ''")
        assert cur.fetchall() == []

    # Access: no token answers 401.
    anon = TestClient(app)
    resp = anon.get(URL)
    assert resp.status_code == 401, resp.text

    # The signed-in read (the conftest client carries the bare
    # development token, role "user"): the rating.
    resp = client.get(URL)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The window's as-of: the same one lead_time_stats_for_all
    # resolves — the newest seeded receipt, 41 days ago.
    assert body["as_of"] == (today - timedelta(days=41)).isoformat()
    suppliers = body["suppliers"]
    # The order: high, then medium, then low, then
    # sole_source_items descending, then supplier_code.
    assert [e["supplier_code"] for e in suppliers] == [
        "S-AT",
        "S-CV65",
        "S-VAR",
        "S-VAR-B",
        "S-BELOW",
        "S-CV55",
        "S-STEADY",
        "S-FEW",
        "S-PARTNER",
    ], "high before medium before low"
    for entry in suppliers:
        assert set(entry) == ENTRY_FIELDS, entry

    # Sole source alone is low: on time with steady lead times,
    # the only supplier of one item.
    steady = _entry(suppliers, "S-STEADY")
    assert steady["risk"] == "low"
    assert steady["late"] is False
    assert steady["variable"] is False
    assert steady["sole_source"] is True
    assert steady["sole_source_items"] == 1
    assert steady["receipts"] == 6
    assert steady["on_time_rate"] == 1.0
    assert steady["lead_time_mean_days"] == 15.0
    assert steady["lead_time_std_days"] == 0.0
    assert steady["lead_time_cv"] == 0.0
    assert steady["vendor_code"] == "V-STEADY"
    assert steady["supplier_name"] == "Steady Vendor"

    # Reliability plus sole source is high: widely varying lead
    # times over 8 receipts, the only supplier of one item.
    var = _entry(suppliers, "S-VAR")
    assert var["risk"] == "high"
    assert var["late"] is False
    assert var["variable"] is True
    assert var["sole_source"] is True
    assert var["sole_source_items"] == 1
    assert var["receipts"] == 8
    assert var["on_time_rate"] == 1.0
    assert var["lead_time_mean_days"] == 40.0
    assert abs(var["lead_time_std_days"] - 32.071349029490925) < 1e-9
    assert var["lead_time_cv"] == 0.8

    # Reliability alone is medium: the same varying supplier
    # sharing all its items with another supplier.
    varb = _entry(suppliers, "S-VAR-B")
    assert varb["risk"] == "medium"
    assert varb["late"] is False
    assert varb["variable"] is True
    assert varb["sole_source"] is False
    assert varb["sole_source_items"] == 0
    assert varb["receipts"] == 8
    assert varb["on_time_rate"] == 1.0
    assert varb["lead_time_mean_days"] == 40.0
    assert varb["lead_time_cv"] == 0.8

    # Too few receipts: 2 receipts of very different lead times
    # (displayed cv 1.27, far above the ceiling) are low
    # confidence — not variable, and the level stays low.
    few = _entry(suppliers, "S-FEW")
    assert few["risk"] == "low"
    assert few["late"] is False
    assert few["variable"] is False
    assert few["sole_source"] is False
    assert few["sole_source_items"] == 0
    assert few["receipts"] == 2
    assert few["on_time_rate"] == 1.0
    assert few["lead_time_mean_days"] == 50.0
    assert abs(few["lead_time_std_days"] - 63.63961030678928) < 1e-9
    assert few["lead_time_cv"] == 1.27

    # The default: with no stored value, a variation of 0.55 is
    # not variable and one of 0.65 is.
    cv55 = _entry(suppliers, "S-CV55")
    assert cv55["lead_time_mean_days"] == 100.0
    assert cv55["lead_time_cv"] == 0.55
    assert cv55["variable"] is False
    assert cv55["risk"] == "low"
    cv65 = _entry(suppliers, "S-CV65")
    assert cv65["lead_time_mean_days"] == 100.0
    assert cv65["lead_time_cv"] == 0.65
    assert cv65["variable"] is True
    assert cv65["risk"] == "high"

    # The flag follows the same rounded lead_time_cv the route
    # returns: S-AT's unrounded cv (~0.5987) displays as exactly
    # 0.60 — at the 0.6 default ceiling, variable — while
    # S-BELOW's displayed 0.50 is not.
    at = _entry(suppliers, "S-AT")
    assert at["lead_time_mean_days"] == 100.0
    assert abs(at["lead_time_std_days"] - 59.86651818838306) < 1e-9
    assert at["lead_time_cv"] == 0.6
    assert at["variable"] is True
    assert at["risk"] == "high"
    below = _entry(suppliers, "S-BELOW")
    assert below["lead_time_mean_days"] == 100.0
    assert abs(below["lead_time_std_days"] - 49.652123150307816) < 1e-9
    assert below["lead_time_cv"] == 0.5
    assert below["variable"] is False
    assert below["risk"] == "low"

    # S-PARTNER: steady, sharing both of its items — no sign.
    partner = _entry(suppliers, "S-PARTNER")
    assert partner["risk"] == "low"
    assert partner["late"] is False
    assert partner["variable"] is False
    assert partner["sole_source"] is False
    assert partner["sole_source_items"] == 0

    # The rating module itself answers the very same entries, not
    # just the serialized response.
    assert supplier_risk()["suppliers"] == suppliers
