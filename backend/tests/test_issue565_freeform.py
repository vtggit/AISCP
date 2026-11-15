"""Issue #565 proof: the PO read uses the ERP's real field names.

The #560 proof hand-typed its PO003072 fixture with the field names
``OrderDate`` / ``PromisedDate`` — which the live Acumatica entity API
does not return. The real names (proven here against the verbatim
response the live instance returned for PO003072, quoted below) are
``Date`` for the order date, ``PromisedOn`` for the order's promised
date, and ``Promised`` for each line's promised date.

Proved with no network, through the ``httpx.MockTransport`` seam the
earlier client tests use (the module-level ``_build_client`` is
replaced with a factory over a recording mock transport serving the
verbatim PO003072 body and its variants — login 204; the bare-array
PurchaseOrder rows; logout 204), with the ``plan_releases`` read
(``app.aggregate.tracking._load_release_rows``) monkeypatched and
``today`` fixed at 2026-11-10:

  * every fixture is built from ``json.loads(VERBATIM_PO003072)`` —
    the variants change values (the ``Hold`` flag) but never field
    names;
  * parsing: ``read_purchase_orders(["PO003072"])`` returns date
    "2026-10-21", promised_on "2026-11-05", and the one AACOMPUT01 /
    WHOLESALE / 273.0 / received 0.0 / promised "2026-11-07" line —
    the order date from ``Date``, the order's promised date from
    ``PromisedOn``, the line's promised date from ``Promised``; the
    numeric-offset timestamps (``2026-11-05T00:00:00-07:00``) keep
    their calendar date part;
  * classification as of 2026-11-10: the verbatim (on-hold) body is
    ``awaiting_approval`` with ``days_waiting`` 20, and with ``Hold``
    set to false it is ``late`` with ``days_late`` 3;
  * the module source of ``acumatica_client.py`` contains neither the
    string "OrderDate" nor the string "PromisedDate" — the wrong
    names are no longer read anywhere.
"""

import json
import pathlib
from datetime import date

import httpx

import app.aggregate.tracking as tracking
import app.feed.acumatica_client as acumatica_client
from app.feed.acumatica_client import AcumaticaClient

BASE_URL = "https://acumatica.example.com/Instance"
TENANT = "AIERP"
USERNAME = "svc-aierp"
PASSWORD = "s3cret-password!"
VERSION = "25.200.001"

LOGIN_PATH = "/Instance/entity/auth/login"
LOGOUT_PATH = "/Instance/entity/auth/logout"
PO_PATH = f"/Instance/entity/Default/{VERSION}/PurchaseOrder"

TODAY = date(2026, 11, 10)

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

# The verbatim PO003072 response, exactly as the live Acumatica entity
# API quoted it: the contract-based REST shape, the order date in
# "Date", the order's promised date in "PromisedOn", each line's
# promised date in "Promised", every field enveloped as {"value": ...}
# and the full envelope metadata on order and lines. The timestamps
# carry the instance's numeric offset.
VERBATIM_PO003072 = r"""
[
  {
    "id": "PO-20261021-003072",
    "rowNumber": 0,
    "note": null,
    "custom": {},
    "_links": {
      "self": {
        "href": "https://acumatica.example.com/Instance/entity/Default/25.200.001/PurchaseOrder"
      }
    },
    "OrderNbr": {"value": "PO003072"},
    "Status": {"value": "On Hold"},
    "Hold": {"value": true},
    "Date": {"value": "2026-10-21T00:00:00-07:00"},
    "PromisedOn": {"value": "2026-11-05T00:00:00-07:00"},
    "OrderTotal": {"value": 66931.41},
    "Details": [
      {
        "id": "PO-20261021-003072-0000000001",
        "rowNumber": 0,
        "note": null,
        "custom": {},
        "InventoryID": {"value": "AACOMPUT01"},
        "WarehouseID": {"value": "WHOLESALE"},
        "OrderQty": {"value": 273.0},
        "QtyOnReceipts": {"value": 0.0},
        "Completed": {"value": false},
        "Cancelled": {"value": false},
        "Promised": {"value": "2026-11-07T00:00:00-07:00"}
      }
    ]
  }
]
"""

VERBATIM_ROWS = json.loads(VERBATIM_PO003072)
VERBATIM_ROW = VERBATIM_ROWS[0]


def po_variant(**changes) -> dict:
    """The verbatim row with values changed, field names untouched."""
    row = json.loads(json.dumps(VERBATIM_ROW))
    for name, value in changes.items():
        row[name] = {"value": value}
    return row


def release_row(nbr: str) -> dict:
    """One plan_releases row (newest-first audit shape) for ``nbr``."""
    return {
        "id": 1,
        "month": "2026-10",
        "site_code": "WHOLESALE",
        "supplier_code": "AACOMPUT",
        "origin": "AIERP plan 2026-10 site WHOLESALE vendor AACOMPUT",
        "warehouse_code": "WHOLESALE",
        "vendor_code": "AACOMPUT",
        "order_nbr": nbr,
        "outcome": "created",
        "error": None,
        "line_count": 1,
        "value": "66931.41",
        "lines": [
            {
                "item_code": "AACOMPUT01",
                "quantity": 273,
                "unit_cost": "245.17",
                "value": "66931.41",
            }
        ],
        "actor": "admin",
        "created_at": "2026-10-21T09:00:00+00:00",
        "updated_at": "2026-10-21T09:00:00+00:00",
    }


def test_issue565_freeform(monkeypatch) -> None:
    # Isolate the Acumatica environment; the write switch is NOT set —
    # reads must not consult it.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", BASE_URL)
    monkeypatch.setenv("ACUMATICA_TENANT", TENANT)
    monkeypatch.setenv("ACUMATICA_USERNAME", USERNAME)
    monkeypatch.setenv("ACUMATICA_PASSWORD", PASSWORD)

    requests_log: list[httpx.Request] = []
    canned: dict = {"body": list(VERBATIM_ROWS)}

    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/entity/auth/login"):
            return httpx.Response(204)
        if request.method == "POST" and path.endswith("/entity/auth/logout"):
            return httpx.Response(204)
        if request.method == "GET" and path.endswith("/PurchaseOrder"):
            return httpx.Response(200, json=canned["body"])
        return httpx.Response(404, text="unexpected request")

    # _build_client is replaced: one httpx.Client over a recording
    # httpx.MockTransport, per call — no network.
    monkeypatch.setattr(
        acumatica_client,
        "_build_client",
        lambda ca_bundle, timeout: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client = AcumaticaClient()

    # ------------------------------------------------------------------
    # Parsing the verbatim body: Date / PromisedOn / Promised, the
    # numeric-offset timestamps keeping their calendar date part
    # ------------------------------------------------------------------
    requests_log.clear()
    result = client.read_purchase_orders(["PO003072"])
    assert result == {
        "PO003072": {
            "status": "On Hold",
            "hold": True,
            "date": "2026-10-21",
            "promised_on": "2026-11-05",
            "total": 66931.41,
            "lines": [
                {
                    "item_code": "AACOMPUT01",
                    "warehouse_code": "WHOLESALE",
                    "order_qty": 273.0,
                    "received_qty": 0.0,
                    "completed": False,
                    "cancelled": False,
                    "promised": "2026-11-07",
                }
            ],
        }
    }
    assert [(r.method, r.url.path) for r in requests_log] == [
        ("POST", LOGIN_PATH),
        ("GET", PO_PATH),
        ("POST", LOGOUT_PATH),
    ]
    assert all(r.url.host == "acumatica.example.com" for r in requests_log)
    assert httpx.QueryParams(requests_log[1].url.query)["$filter"] == (
        "OrderNbr eq 'PO003072'"
    )

    # ------------------------------------------------------------------
    # release_tracking as of 2026-11-10, from the verbatim fixtures
    # ------------------------------------------------------------------
    monkeypatch.setattr(
        tracking, "_load_release_rows", lambda: [release_row("PO003072")]
    )

    # The verbatim (on-hold) body: awaiting_approval, days_waiting 20
    # (2026-11-10 minus the ERP date 2026-10-21).
    canned["body"] = list(VERBATIM_ROWS)
    requests_log.clear()
    result = tracking.release_tracking(today=TODAY)
    assert result["as_of"] == "2026-11-10"
    assert result["counts"] == {
        "awaiting_approval": 1,
        "late": 0,
        "partly_received": 0,
        "received": 0,
    }
    [order] = result["orders"]
    assert order["order_nbr"] == "PO003072"
    assert order["state"] == "awaiting_approval"
    assert order["days_waiting"] == 20
    assert order["days_late"] is None
    assert order["promised_on"] == "2026-11-05"
    assert order["lines"][0]["promised"] == "2026-11-07"
    assert [(r.method, r.url.path) for r in requests_log] == [
        ("POST", LOGIN_PATH),
        ("GET", PO_PATH),
        ("POST", LOGOUT_PATH),
    ]

    # The same verbatim body with Hold set to false: late, days_late 3
    # (2026-11-10 minus the line's promised 2026-11-07).
    canned["body"] = [po_variant(Hold=False)]
    requests_log.clear()
    result = tracking.release_tracking(today=TODAY)
    assert result["counts"]["late"] == 1
    [order] = result["orders"]
    assert order["state"] == "late"
    assert order["days_late"] == 3
    assert order["days_waiting"] is None
    assert order["hold"] is False

    # ------------------------------------------------------------------
    # The wrong names are no longer read anywhere in the client
    # ------------------------------------------------------------------
    source = pathlib.Path(acumatica_client.__file__).read_text(encoding="utf-8")
    assert "OrderDate" not in source
    assert "PromisedDate" not in source
