"""Issue #1050 proof: the supplier risk rating — GET /api/supplier-risk.

AC-1 — the route: open to any signed-in user behind the app's auth
gate the way the supplier-performance router is mounted (401 without
a valid Bearer token), answering ``{"as_of", "suppliers"}`` with the
730-day window's as-of (the same one ``lead_time_stats_for_all``
resolves — the newest seeded receipt date here, 21 days ago), writing nothing and
calling no ERP.  Per supplier: ``receipts`` (the window's
``n_observations``), ``lead_time_mean_days`` / ``lead_time_std_days``
(the outlier fenced ``mean_days`` / ``std_days`` from one
``compute_lead_time_stats`` over all the supplier's observations),
``lead_time_cv`` (standard deviation over the mean, 2 places, null
when either is null or the mean is 0), ``on_time_rate`` (the figure
the Suppliers screen shows by default — ``supplier_performance``
with its default window, matched by ``supplier_code``, null when the
supplier has no entry there), and ``sole_source_items`` (the item
codes whose observations in the window all come from this one
supplier).  The signs: ``late`` when ``on_time_rate`` is not null
and below ``supplier_risk.on_time_floor`` (stored value, else the
0.85 default), ``variable`` when the supplier's lead-time stats are
not low confidence (at least 3 used observations) and the rounded
``lead_time_cv`` is not null and at least
``supplier_risk.lead_time_cv_ceiling`` (stored value, else the 0.6
default), ``sole_source`` when ``sole_source_items`` is above 0.
The level: ``high`` when ``late`` or ``variable`` and also
``sole_source``, ``medium`` when ``late`` or ``variable`` without
being ``sole_source``, and ``low`` when neither ``late`` nor
``variable``, whether or not ``sole_source``.  Entries are ordered
high, then
medium, then low, then ``sole_source_items`` descending, then
``supplier_code``; every entry carries ``vendor_code`` and
``supplier_name`` looked up the way ``supplier_performance`` does
(both null when unknown).  The response models are typed pydantic
models and ``backend/openapi.json`` lists the path.

AC-2 — the proof seeds lead-time observations (with ``promised_on``
where needed) and suppliers in the test database the way the
supplier-performance tests do (fixed dates relative to the server's
date, raw INSERTs) and reads the route through the app's test
client:

  * High — supplier S-HIGH delivers late on most promised receipts
    (8 of 10 real promises one day late: ``on_time_rate`` 0.2, below
    the 0.85 floor), its lead times alternate 8 and 32 days (mean
    20, sample stdev ~12.65, cv 0.63 above the 0.6 ceiling) and
    it alone supplies item ONLY-1.  It answers ``risk`` "high" with
    ``late``, ``variable`` and ``sole_source`` true and
    ``sole_source_items`` 1.
  * Low — supplier S-MED is on time with steady 14-day lead times
    and alone supplies item ONLY-2: sole source alone is low.  It
    answers "low" with only ``sole_source`` true.
  * Low — suppliers S-LOW-A and S-LOW-B are both on time and
    steady, and both supply item SHARED-1.  Each answers "low" with
    ``sole_source_items`` 0.
  * The setting — with ``supplier_risk.on_time_floor`` stored as
    0.1, S-HIGH's 0.2 rate is no longer below it: it is no longer
    ``late`` (its other two signs keep it "high").
  * The order — high comes before low, and within low
    ``sole_source_items`` descending, then ``supplier_code`` (S-HIGH,
    S-MED, then S-LOW-A and S-LOW-B by code).
  * Access — a non-admin signed-in user (the conftest client's
    role "user" development token) gets 200, and no token gets 401.

Identity — the rating route carries no admin-only control, so the
pass also runs as a NON-ADMIN (role "user") in a second pass after a
full reload (a fresh ``TestClient``): the page boots the production
way (the token it would take from the URL hash as
``#access_token=...`` — the development token, role "user" — sent as
its Bearer token), ``GET /api/auth/config`` answers publicly and
``GET /api/auth/me`` resolves the viewer for that user, the
admin-only control is absent — the route answers 200 with the very
same rating body — while unauthenticated /api traffic is still 401.

Regression proofs — the typed response model accepts a null
lead-time mean and standard deviation (a supplier without
usable lead times must not 500 the route), and when the
suppliers read model is empty the entries still carry
``vendor_code`` and ``supplier_name``, both null — never
omitted from the response.
"""

import json
import os
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.supplier_risk import SupplierRiskEntry
from app.db.connection import get_connection
from app.main import app
from app.recommend.supplier_risk import supplier_risk

URL = "/api/supplier-risk"
ADMIN_TOKEN = "dev-secret-token:admin"
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

FLOOR_KEY = "supplier_risk.on_time_floor"

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

    S-HIGH (item ONLY-1, sole source): 10 receipts received 201 down
    to 21 days ago, lead times 8 and 32 days alternating (mean 20,
    sample stdev sqrt(160) ~ 12.65, cv 0.63 above the 0.6 ceiling);
    the first 8 real promises land one day late, the last 2 on time
    — on_time_rate 0.2, below the 0.85 floor.
    S-MED (item ONLY-2, sole source): 6 receipts, every promise on
    time, steady 14-day lead times.
    S-LOW-A and S-LOW-B (item SHARED-1, shared): 4 receipts each,
    every promise on time, steady 10- and 12-day lead times.
    Every receipt lies inside both the 730-day window and the
    Suppliers screen's default 365-day window.
    """
    for i in range(10):
        received = today - timedelta(days=201 - 20 * i)
        lead = 8 if i % 2 == 0 else 32
        # The first eight promises are one day early (one day late
        # when the receipt lands); the last two promise the receipt
        # day itself (on time).
        promised = received - timedelta(days=1 if i <= 7 else 0)
        _receipt(cur, "high", i, "S-HIGH", "ONLY-1", lead, received, promised)

    for i in range(6):
        received = today - timedelta(days=151 - 20 * i)
        _receipt(cur, "med", i, "S-MED", "ONLY-2", 14, received, received)

    for i in range(4):
        received = today - timedelta(days=121 - 20 * i)
        _receipt(cur, "lowa", i, "S-LOW-A", "SHARED-1", 10, received, received)

    for i in range(4):
        received = today - timedelta(days=111 - 20 * i)
        _receipt(cur, "lowb", i, "S-LOW-B", "SHARED-1", 12, received, received)

    for supplier, vendor, name in (
        ("S-HIGH", "V-HIGH", "High Vendor"),
        ("S-MED", "V-MED", "Med Vendor"),
        ("S-LOW-A", "V-LOW-A", "Low A Vendor"),
        ("S-LOW-B", "V-LOW-B", "Low B Vendor"),
    ):
        cur.execute(
            SUPPLIER_SQL,
            (f"id-sup-{supplier}", supplier, vendor, name, "Active", "test"),
        )


def _entry(body: list[dict], code: str) -> dict:
    entry = next((e for e in body if e["supplier_code"] == code), None)
    assert entry is not None, (code, body)
    return entry


def test_issue1050_freeform(client):
    today = date.today()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur, today)
        conn.commit()
    finally:
        conn.close()

    # The schema: backend/openapi.json lists the path and its models.
    schema = json.loads(_OPENAPI.read_text())
    assert "/api/supplier-risk" in schema["paths"]
    assert "SupplierRiskResponse" in schema["components"]["schemas"]
    assert "SupplierRiskEntry" in schema["components"]["schemas"]

    # The typed model accepts a null lead-time mean and
    # standard deviation: a supplier without usable lead
    # times must not 500 the route.
    null_entry = SupplierRiskEntry(
        supplier_code="S-NULL",
        receipts=3,
        lead_time_mean_days=None,
        lead_time_std_days=None,
        lead_time_cv=None,
        on_time_rate=None,
        sole_source_items=0,
        late=False,
        variable=False,
        sole_source=False,
        risk="low",
    )
    assert null_entry.lead_time_mean_days is None
    assert null_entry.lead_time_std_days is None

    # Access: no token answers 401.
    anon = TestClient(app)
    resp = anon.get(URL)
    assert resp.status_code == 401, resp.text

    # The signed-in non-admin read (the conftest client carries the
    # bare development token, role "user"): the rating.
    resp = client.get(URL)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The window's as-of: the same one lead_time_stats_for_all
    # resolves — the earlier of today and the latest receipt (the
    # newest seeded receipt, received 21 days ago).
    assert body["as_of"] == (today - timedelta(days=21)).isoformat()
    suppliers = body["suppliers"]
    # The order: high, then medium, then low, then
    # sole_source_items descending, then supplier_code.
    assert [e["supplier_code"] for e in suppliers] == [
        "S-HIGH",
        "S-MED",
        "S-LOW-A",
        "S-LOW-B",
    ], "high before medium before low"
    for entry in suppliers:
        assert set(entry) == ENTRY_FIELDS, entry

    # High: late on most promises, variable lead times, sole source
    # of ONLY-1 — all three signs.
    high = _entry(suppliers, "S-HIGH")
    assert high["risk"] == "high"
    assert high["late"] is True
    assert high["variable"] is True
    assert high["sole_source"] is True
    assert high["sole_source_items"] == 1
    assert high["receipts"] == 10
    assert high["on_time_rate"] == 0.2
    assert high["lead_time_mean_days"] == 20.0
    assert abs(high["lead_time_std_days"] - 12.649110640673518) < 1e-9
    assert high["lead_time_cv"] == 0.63
    assert high["vendor_code"] == "V-HIGH"
    assert high["supplier_name"] == "High Vendor"

    # Low: on time and steady, sole source of ONLY-2 — sole source
    # alone is low.
    med = _entry(suppliers, "S-MED")
    assert med["risk"] == "low"
    assert med["late"] is False
    assert med["variable"] is False
    assert med["sole_source"] is True
    assert med["sole_source_items"] == 1
    assert med["receipts"] == 6
    assert med["on_time_rate"] == 1.0
    assert med["lead_time_mean_days"] == 14.0
    assert med["lead_time_std_days"] == 0.0
    assert med["lead_time_cv"] == 0.0
    assert med["vendor_code"] == "V-MED"
    assert med["supplier_name"] == "Med Vendor"

    # Low: on time and steady, SHARED-1 shared — no sign.
    for code, vendor, mean in (
        ("S-LOW-A", "V-LOW-A", 10.0),
        ("S-LOW-B", "V-LOW-B", 12.0),
    ):
        low = _entry(suppliers, code)
        assert low["risk"] == "low"
        assert low["late"] is False
        assert low["variable"] is False
        assert low["sole_source"] is False
        assert low["sole_source_items"] == 0
        assert low["receipts"] == 4
        assert low["on_time_rate"] == 1.0
        assert low["lead_time_mean_days"] == mean
        assert low["lead_time_std_days"] == 0.0
        assert low["lead_time_cv"] == 0.0
        assert low["vendor_code"] == vendor

    # The setting: with the floor stored at 0.1, S-HIGH's 0.2 rate
    # is no longer below it — no longer late; its other two signs
    # keep it high.
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    resp = admin_client.put(f"/api/settings/{FLOOR_KEY}", json={"value": 0.1})
    assert resp.status_code == 200, resp.text
    assert resp.json()["key"] == FLOOR_KEY
    assert resp.json()["value"] == 0.1
    assert resp.json()["source"] == "system"

    body = client.get(URL).json()
    high = _entry(body["suppliers"], "S-HIGH")
    assert high["late"] is False, "below the stored 0.1 floor S-HIGH is no longer late"
    assert high["variable"] is True
    assert high["sole_source"] is True
    assert high["risk"] == "high"
    assert [e["supplier_code"] for e in body["suppliers"]] == [
        "S-HIGH",
        "S-MED",
        "S-LOW-A",
        "S-LOW-B",
    ]

    # Identity, as a NON-ADMIN (role "user"), in a second pass after
    # a full reload: the page boots the production way — the public
    # auth config, then identity with the token the page would take
    # from the URL hash — and the admin-only control is absent: the
    # route carries no admin gate, so the viewer reads the very same
    # rating while unauthenticated /api traffic is still 401.
    viewer_client = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    config = viewer_client.get("/api/auth/config")
    assert config.status_code == 200
    assert config.json()["authEnabled"] is True

    me = viewer_client.get("/api/auth/me")
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["authenticated"] is True
    assert me_body["user"]["sub"] == "dev-user-1"
    assert me_body["user"]["roles"] == ["user"]
    assert "admin" not in me_body["user"]["roles"]

    resp = viewer_client.get(URL)
    assert resp.status_code == 200, resp.text
    assert resp.json() == body, "the non-admin reads the same rating"
    assert TestClient(app).get(URL).status_code == 401

    # Empty suppliers read model: the rating still lists
    # every supplier, and every entry still carries
    # vendor_code and supplier_name, both null — never
    # omitted from the response.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM suppliers")
        conn.commit()
    finally:
        conn.close()

    body = client.get(URL).json()
    assert [e["supplier_code"] for e in body["suppliers"]] == [
        "S-HIGH",
        "S-MED",
        "S-LOW-A",
        "S-LOW-B",
    ]
    for entry in body["suppliers"]:
        assert set(entry) == ENTRY_FIELDS, entry
        assert entry["vendor_code"] is None
        assert entry["supplier_name"] is None

    # The rating module itself carries the keys on every entry,
    # read model empty or not — not just the serialized response.
    for entry in supplier_risk()["suppliers"]:
        assert "vendor_code" in entry and "supplier_name" in entry, entry
        assert entry["vendor_code"] is None
        assert entry["supplier_name"] is None
