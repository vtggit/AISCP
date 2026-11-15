"""Issue #618 proof: supplier names on every supplier-bearing answer.

Every answer that carries suppliers reads the suppliers read model
exactly once, through ``SupplierPostgresRepository.list_all``:

  * ``app.recommend.supplier_performance.supplier_performance``:
    every supplier entry gains ``vendor_code`` and
    ``supplier_name``, looked up by ``supplier_code``;
  * ``app.recommend.expedite.expedite_list``: every line gains
    ``vendor_code`` and ``supplier_name``, looked up by the line's
    ``supplier_code``;
  * ``app.aggregate.tracking.release_tracking``: every order gains
    ``supplier_name``, looked up by its ``vendor_code`` — the
    trimmed code the release used.

A supplier missing from the read model carries null ``vendor_code``
and ``supplier_name`` (``supplier_name`` alone for an order) but is
still present — never dropped, never a failure.  The matching
response models serve the new fields as optional strings.

Proven here, in order, with the suppliers read
(``SupplierPostgresRepository.list_all``) monkeypatched to the two
vendors ("6995" / "AAVENDOR" / "Goto Vendor Inc" and "7117" /
"AASERVICES" / "AA Services") plus the existing reads each function
uses, and the server's "today" (the modules' ``_today`` seams)
monkeypatched to the fixed ``AS_OF`` so the proof never depends on
the real clock:

  * supplier performance: for in-window receipts of suppliers "6995"
    and "9999", the "6995" entry carries ``vendor_code`` "AAVENDOR"
    and ``supplier_name`` "Goto Vendor Inc", and "9999" — missing
    from the read model — carries both null but is still listed;
  * expedite: the late line of supplier "7117" carries
    ``vendor_code`` "AASERVICES" and ``supplier_name`` "AA
    Services";
  * release tracking: the order released with ``vendor_code``
    "AAVENDOR" carries ``supplier_name`` "Goto Vendor Inc";
  * the routes: through the test client, the three routes return
    the new fields.

Each function's suppliers read is called exactly once.
"""

from datetime import date

import httpx

import app.aggregate.tracking as tracking
import app.feed.acumatica_client as acumatica_client
import app.recommend.expedite as expedite_module
import app.recommend.supplier_performance as supplier_performance_module
from app.recommend.expedite import expedite_list
from app.recommend.supplier_performance import supplier_performance
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.lead_time_observations_postgres_repository import (
    LeadTimeObservationPostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)

AS_OF = date(2026, 10, 23)
WINDOW_DAYS = 365

URL_PERFORMANCE = "/api/suppliers/performance"
URL_EXPEDITE = "/api/expedite"
URL_RELEASES = "/api/aggregate/releases"

BASE_URL = "https://acumatica.example.com/Instance"
TENANT = "AIERP"
USERNAME = "svc-aierp"
PASSWORD = "s3cret-password!"

ACUMATICA_VARS = (
    "ACUMATICA_BASE_URL",
    "ACUMATICA_TENANT",
    "ACUMATICA_USERNAME",
    "ACUMATICA_PASSWORD",
    "ACUMATICA_ENDPOINT_VERSION",
    "ACUMATICA_CA_BUNDLE",
    "ACUMATICA_TIMEOUT_SECONDS",
    "ACUMATICA_ALLOW_INSECURE",
    "ACUMATICA_ALLOW_WRITES",
)

# The two vendors of the suppliers read model, as (supplier_code,
# vendor_code, name) rows — the padded AcctCD already trimmed.
SUPPLIER_ROWS = [
    {"supplier_code": "6995", "vendor_code": "AAVENDOR", "name": "Goto Vendor Inc"},
    {"supplier_code": "7117", "vendor_code": "AASERVICES", "name": "AA Services"},
]


def _receipt(business_key, supplier, ordered, received, promised):
    return {
        "id": f"uuid-{business_key}",
        "business_key": business_key,
        "supplier_code": supplier,
        "item_code": "W-1",
        "site_code": "1",
        "ordered_on": ordered,
        "received_on": received,
        "promised_on": promised,
        "lead_time_days": None,
        "source_system": "acumatica",
        "synced_at": "2026-10-20T00:00:00+00:00",
    }


# In-window receipts: supplier "6995" (one on-time promise, one
# late by 4) and supplier "9999", missing from the read model.
RECEIPTS = [
    _receipt("R:1:1", "6995", date(2026, 9, 1), date(2026, 9, 12), date(2026, 9, 15)),
    _receipt("R:1:2", "6995", date(2026, 8, 1), date(2026, 8, 20), date(2026, 8, 16)),
    _receipt("R:2:1", "9999", date(2026, 7, 1), date(2026, 7, 10), None),
]


def _open_line(business_key, item, supplier, ordered, promised, open_units):
    return {
        "business_key": business_key,
        "item_code": item,
        "site_code": "154",
        "supplier_code": supplier,
        "ordered_on": ordered,
        "promised_on": promised,
        "open_units": open_units,
    }


# The late open line of supplier "7117": promised 2026-10-20, three
# days before the frozen as_of.
OPEN_LINES = [
    _open_line(
        "RO:PO006180:1", "MMM", "7117", date(2026, 10, 10), date(2026, 10, 20), 10
    ),
]

ITEM_SITES = [
    {"item_code": "MMM", "site_code": "154", "unit_cost": 2.0},
]

# The ERP record of the released order PO006181: open, nothing on
# receipt, promising after the frozen as_of — state "open".
ERP_RECORD = {
    "OrderNbr": {"value": "PO006181"},
    "Status": {"value": "Open"},
    "Hold": {"value": False},
    "Date": {"value": "2026-10-20"},
    "PromisedOn": {"value": "2026-10-25"},
    "OrderTotal": {"value": 123.45},
    "Details": [
        {
            "InventoryID": {"value": "MMM"},
            "WarehouseID": {"value": "154"},
            "OrderQty": {"value": 10.0},
            "QtyOnReceipts": {"value": 0.0},
            "Completed": {"value": False},
            "Cancelled": {"value": False},
            "Promised": {"value": "2026-10-25"},
        }
    ],
}


def _release_row(order_nbr):
    """One plan_releases row released with vendor code "AAVENDOR"."""
    return {
        "id": 1,
        "month": "2026-11",
        "site_code": "154",
        "supplier_code": "6995",
        "origin": "AIERP plan 2026-11 site 154 vendor 6995",
        "warehouse_code": "154",
        "vendor_code": "AAVENDOR",
        "order_nbr": order_nbr,
        "outcome": "created",
        "error": None,
        "line_count": 1,
        "value": "123.45",
        "lines": [
            {
                "item_code": "MMM",
                "quantity": 10,
                "unit_cost": "2.00",
                "value": "20.00",
            },
        ],
        "actor": "admin",
        "created_at": "2026-10-21T09:00:00+00:00",
        "updated_at": "2026-10-21T09:00:00+00:00",
    }


def test_issue618_freeform(monkeypatch, client):
    calls = {
        "suppliers": 0,
        "receipts": 0,
        "open_lines": 0,
        "on_hand": 0,
        "item_sites": 0,
        "recommendations": 0,
        "supplier_performance": 0,
    }

    # -- The suppliers read: the two vendors, once per answer --------
    def fake_list_all(self):
        calls["suppliers"] += 1
        return [dict(row) for row in SUPPLIER_ROWS]

    monkeypatch.setattr(SupplierPostgresRepository, "list_all", fake_list_all)

    # -- 1. Supplier performance ------------------------------------
    # The receipts read, monkeypatched (no database), and the
    # clock-free server "today".
    def fake_receipt_list_rows(self, limit, offset, **filters):
        calls["receipts"] += 1
        return [dict(row) for row in RECEIPTS]

    monkeypatch.setattr(
        LeadTimeObservationPostgresRepository, "list_rows", fake_receipt_list_rows
    )
    monkeypatch.setattr(supplier_performance_module, "_today", lambda: AS_OF)

    calls["suppliers"] = 0
    result = supplier_performance(as_of=AS_OF, window_days=WINDOW_DAYS)
    # The suppliers read model, read exactly once through the
    # existing repository.
    assert calls["suppliers"] == 1
    assert calls["receipts"] == 1
    by_code = {entry["supplier_code"]: entry for entry in result["suppliers"]}

    # "6995" is in the read model: the entry carries the vendor code
    # and the display name, looked up by supplier_code.
    entry_6995 = by_code["6995"]
    assert entry_6995["vendor_code"] == "AAVENDOR"
    assert entry_6995["supplier_name"] == "Goto Vendor Inc"
    assert entry_6995["receipts"] == 2

    # "9999" is missing from the read model: both fields null, the
    # entry still present.
    entry_9999 = by_code["9999"]
    assert entry_9999["vendor_code"] is None
    assert entry_9999["supplier_name"] is None
    assert entry_9999["receipts"] == 1

    # The ordering and the other fields stay as they are: the rate
    # of "6995" (1 of 2 on time) sorts before the null rate of
    # "9999".
    assert [entry["supplier_code"] for entry in result["suppliers"]] == ["6995", "9999"]
    assert entry_6995["on_time_rate"] == 0.5
    assert entry_9999["on_time_rate"] is None

    # -- 2. Expedite --------------------------------------------------
    def fake_open_lines(self):
        calls["open_lines"] += 1
        return [dict(row) for row in OPEN_LINES]

    def fake_on_hand_list_rows(self, limit, offset, **filters):
        calls["on_hand"] += 1
        return []

    def fake_item_site_list_rows(self, limit, offset, **filters):
        calls["item_sites"] += 1
        return [dict(row) for row in ITEM_SITES]

    def fake_recommendations_for_all(as_of=None):
        calls["recommendations"] += 1
        return []

    def fake_supplier_performance(as_of=None, window_days=365):
        calls["supplier_performance"] += 1
        return {
            "as_of": AS_OF.isoformat(),
            "window_days": WINDOW_DAYS,
            "suppliers": [],
        }

    monkeypatch.setattr(
        OpenPurchaseLinePostgresRepository, "list_latest", fake_open_lines
    )
    monkeypatch.setattr(OnHandPostgresRepository, "list_rows", fake_on_hand_list_rows)
    monkeypatch.setattr(
        ItemSitePostgresRepository, "list_rows", fake_item_site_list_rows
    )
    monkeypatch.setattr(
        expedite_module, "recommendations_for_all", fake_recommendations_for_all
    )
    monkeypatch.setattr(
        expedite_module, "supplier_performance", fake_supplier_performance
    )
    monkeypatch.setattr(expedite_module, "_today", lambda: AS_OF)

    calls["suppliers"] = 0
    result = expedite_list(as_of=AS_OF)
    # The suppliers read model, read exactly once — the line's vendor
    # code and display name come from it, not from the performance.
    assert calls["suppliers"] == 1
    [line] = result["lines"]
    assert line["supplier_code"] == "7117"
    assert line["vendor_code"] == "AASERVICES"
    assert line["supplier_name"] == "AA Services"
    # The line is late by 3 days and otherwise unchanged: the promise
    # (2026-10-20) is its expected date, three days before as_of.
    assert line["reasons"] == ["late"]
    assert line["days_late"] == 3
    assert result["stale_excluded"] == 0

    # -- 3. Release tracking ------------------------------------------
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", BASE_URL)
    monkeypatch.setenv("ACUMATICA_TENANT", TENANT)
    monkeypatch.setenv("ACUMATICA_USERNAME", USERNAME)
    monkeypatch.setenv("ACUMATICA_PASSWORD", PASSWORD)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/entity/auth/login"):
            return httpx.Response(204)
        if request.method == "POST" and request.url.path.endswith(
            "/entity/auth/logout"
        ):
            return httpx.Response(204)
        if request.method == "GET" and request.url.path.endswith("/PurchaseOrder"):
            return httpx.Response(200, json=[ERP_RECORD])
        return httpx.Response(404, text="unexpected request")

    monkeypatch.setattr(
        acumatica_client,
        "_build_client",
        lambda ca_bundle, timeout: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(
        tracking, "_load_release_rows", lambda: [_release_row("PO006181")]
    )

    calls["suppliers"] = 0
    result = tracking.release_tracking(today=AS_OF)
    # The suppliers read model, read exactly once; the order's
    # display name is looked up by its vendor code.
    assert calls["suppliers"] == 1
    [order] = result["orders"]
    assert order["order_nbr"] == "PO006181"
    assert order["vendor_code"] == "AAVENDOR"
    assert order["state"] == "open"
    assert order["supplier_name"] == "Goto Vendor Inc"

    # -- 4. The routes -------------------------------------------------
    # Every read above is still monkeypatched, so the routes answer
    # from the same fixed data through the response models.
    response = client.get(
        URL_PERFORMANCE,
        params={"as_of": AS_OF.isoformat(), "window_days": WINDOW_DAYS},
    )
    assert response.status_code == 200, response.text
    by_code = {entry["supplier_code"]: entry for entry in response.json()["suppliers"]}
    assert by_code["6995"]["vendor_code"] == "AAVENDOR"
    assert by_code["6995"]["supplier_name"] == "Goto Vendor Inc"
    assert by_code["9999"]["vendor_code"] is None
    assert by_code["9999"]["supplier_name"] is None

    response = client.get(URL_EXPEDITE, params={"as_of": AS_OF.isoformat()})
    assert response.status_code == 200, response.text
    [line] = response.json()["lines"]
    assert line["supplier_code"] == "7117"
    assert line["vendor_code"] == "AASERVICES"
    assert line["supplier_name"] == "AA Services"

    response = client.get(URL_RELEASES)
    assert response.status_code == 200, response.text
    [order] = response.json()["orders"]
    assert order["vendor_code"] == "AAVENDOR"
    assert order["supplier_name"] == "Goto Vendor Inc"
