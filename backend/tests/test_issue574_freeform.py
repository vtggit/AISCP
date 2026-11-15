"""Issue #574 proof: "Pending Approval" orders classify as awaiting.

The ERP reports a purchase order it has not approved with ``Status``
"Pending Approval" and ``Hold`` false. The release tracking view must
treat such an order exactly like an on-hold one: state
``awaiting_approval`` in the same precedence position (after
``missing``, ``cancelled`` and ``received``, before ``late``), with
``days_waiting`` counted from the order's ERP date and ``days_late``
null, while an order on hold keeps today's behaviour.

Proved with no network, through the same ``httpx.MockTransport`` seam
and released-order fakes as the #565 proof: the module-level
``_build_client`` is replaced with a factory over a recording mock
transport serving the verbatim PO003077 body — loaded from
``backend/tests/fixtures/po003077_pending_approval.json``, never
restated here — and its value-only variants (login 204; the bare-array
PurchaseOrder rows; logout 204), and ``app.aggregate.tracking.
_load_release_rows`` is monkeypatched to one released-order row for
PO003077, with ``today`` fixed at 2026-10-22:

  * the loaded body (status "Pending Approval", hold false): state
    ``awaiting_approval``, ``days_waiting`` 1, ``days_late`` null,
    counts awaiting_approval 1 and late 0;
  * the same body with ``Status`` changed to "Open": state ``late``
    with ``days_late`` 1;
  * the same body with ``Hold`` set to true and ``Status`` "On Hold":
    ``awaiting_approval`` with ``days_waiting`` 1;
  * review defects: the same body with the order's ``Date`` blanked
    stays ``awaiting_approval`` with ``days_waiting`` null, and the
    same body with ``Status`` "Open" and one line's ``Promised``
    blanked is still ``late`` with ``days_late`` 1 (the blank-promised
    line is skipped by the late-line scan) — a blank date never
    crashes the classification, answered through
    ``tracking._classify_order`` on the parsed-shape record with the
    order date and every line's promised date blanked.
"""

import json
import pathlib
from datetime import date

import httpx

import app.aggregate.tracking as tracking
import app.feed.acumatica_client as acumatica_client

BASE_URL = "https://acumatica.example.com/Instance"
TENANT = "AIERP"
USERNAME = "svc-aierp"
PASSWORD = "s3cret-password!"
VERSION = "25.200.001"

LOGIN_PATH = "/Instance/entity/auth/login"
LOGOUT_PATH = "/Instance/entity/auth/logout"
PO_PATH = f"/Instance/entity/Default/{VERSION}/PurchaseOrder"

TODAY = date(2026, 10, 22)

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

# The verbatim PO003077 response, loaded from the captured fixture on
# disk — never restated in this module.
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "po003077_pending_approval.json"
LOADED_ROWS = json.loads(FIXTURE.read_text(encoding="utf-8"))
LOADED_ROW = LOADED_ROWS[0]


def po_variant(**changes) -> dict:
    """The loaded row with values changed, field names untouched."""
    row = json.loads(json.dumps(LOADED_ROW))
    for name, value in changes.items():
        row[name] = {"value": value}
    return row


def release_row(nbr: str) -> dict:
    """One plan_releases row (newest-first audit shape) for ``nbr``."""
    return {
        "id": 1,
        "month": "2026-10",
        "site_code": "WHOLESALE",
        "supplier_code": "OFFICEMAX",
        "origin": "AIERP plan 2026-10 site WHOLESALE vendor OFFICEMAX",
        "warehouse_code": "WHOLESALE",
        "vendor_code": "OFFICEMAX",
        "order_nbr": nbr,
        "outcome": "created",
        "error": None,
        "line_count": 3,
        "value": "8447.0",
        "lines": [
            {
                "item_code": "CONPAPERST",
                "quantity": 1479,
                "unit_cost": "4.65",
                "value": "6871.00",
            },
            {
                "item_code": "CONPEN100",
                "quantity": 212,
                "unit_cost": "6.68",
                "value": "1417.00",
            },
            {
                "item_code": "CONPENC100",
                "quantity": 126,
                "unit_cost": "1.26",
                "value": "159.00",
            },
        ],
        "actor": "admin",
        "created_at": "2026-10-21T09:00:00+00:00",
        "updated_at": "2026-10-21T09:00:00+00:00",
    }


def test_issue574_freeform(monkeypatch) -> None:
    # Isolate the Acumatica environment; the write switch is NOT set —
    # reads must not consult it.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", BASE_URL)
    monkeypatch.setenv("ACUMATICA_TENANT", TENANT)
    monkeypatch.setenv("ACUMATICA_USERNAME", USERNAME)
    monkeypatch.setenv("ACUMATICA_PASSWORD", PASSWORD)

    requests_log: list[httpx.Request] = []
    canned: dict = {"body": list(LOADED_ROWS)}

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

    # The released-order fake: one plan_releases row for PO003077.
    monkeypatch.setattr(
        tracking, "_load_release_rows", lambda: [release_row("PO003077")]
    )

    # The loaded body (status "Pending Approval", hold false):
    # awaiting_approval, days_waiting 1 (2026-10-22 minus the ERP date
    # 2026-10-21), days_late null.
    canned["body"] = list(LOADED_ROWS)
    requests_log.clear()
    result = tracking.release_tracking(today=TODAY)
    assert result["as_of"] == "2026-10-22"
    assert result["counts"] == {
        "awaiting_approval": 1,
        "late": 0,
        "partly_received": 0,
        "received": 0,
    }
    [order] = result["orders"]
    assert order["order_nbr"] == "PO003077"
    assert order["state"] == "awaiting_approval"
    assert order["status"] == "Pending Approval"
    assert order["hold"] is False
    assert order["days_waiting"] == 1
    assert order["days_late"] is None
    assert [(r.method, r.url.path) for r in requests_log] == [
        ("POST", LOGIN_PATH),
        ("GET", PO_PATH),
        ("POST", LOGOUT_PATH),
    ]

    # The same body with Status changed to "Open": late, days_late 1
    # (2026-10-22 minus the lines' promised 2026-10-21).
    canned["body"] = [po_variant(Status="Open")]
    requests_log.clear()
    result = tracking.release_tracking(today=TODAY)
    assert result["counts"] == {
        "awaiting_approval": 0,
        "late": 1,
        "partly_received": 0,
        "received": 0,
    }
    [order] = result["orders"]
    assert order["state"] == "late"
    assert order["days_late"] == 1
    assert order["days_waiting"] is None
    assert order["hold"] is False

    # The same body with Hold set to true and Status "On Hold": the
    # on-hold behaviour is unchanged — awaiting_approval, days_waiting
    # 1.
    canned["body"] = [po_variant(Hold=True, Status="On Hold")]
    requests_log.clear()
    result = tracking.release_tracking(today=TODAY)
    assert result["counts"]["awaiting_approval"] == 1
    [order] = result["orders"]
    assert order["state"] == "awaiting_approval"
    assert order["days_waiting"] == 1
    assert order["days_late"] is None
    assert order["hold"] is True

    # ------------------------------------------------------------------
    # Review defects: blank dates never crash the classification
    # ------------------------------------------------------------------

    # The same body with the order's Date blanked: an awaiting order
    # whose ERP date is empty answers null days_waiting — no crash.
    canned["body"] = [po_variant(Date="")]
    requests_log.clear()
    result = tracking.release_tracking(today=TODAY)
    assert result["counts"] == {
        "awaiting_approval": 1,
        "late": 0,
        "partly_received": 0,
        "received": 0,
    }
    [order] = result["orders"]
    assert order["state"] == "awaiting_approval"
    assert order["days_waiting"] is None
    assert order["days_late"] is None

    # The same body with Status "Open" and the first line's Promised
    # blanked: the late-line scan skips the blank-promised line, and
    # the order is still late through the other two lines.
    open_blank_line = po_variant(Status="Open")
    open_blank_line["Details"][0]["Promised"] = {"value": ""}
    canned["body"] = [open_blank_line]
    requests_log.clear()
    result = tracking.release_tracking(today=TODAY)
    assert result["counts"]["late"] == 1
    [order] = result["orders"]
    assert order["state"] == "late"
    assert order["days_late"] == 1
    assert order["days_waiting"] is None

    # The parsed-shape record of the loaded body with the order date
    # and every line's promised date blanked: _classify_order answers
    # awaiting_approval with null days_waiting and days_late rather
    # than crashing — the late-line scan and the awaiting days_waiting
    # both parse the blank dates.
    erp = {
        "order_nbr": LOADED_ROW["OrderNbr"]["value"],
        "status": LOADED_ROW["Status"]["value"],
        "hold": LOADED_ROW["Hold"]["value"],
        "date": "",
        "promised_on": LOADED_ROW["PromisedOn"]["value"],
        "total": LOADED_ROW["OrderTotal"]["value"],
        "lines": [
            {
                "item_code": line["InventoryID"]["value"],
                "warehouse_code": line["WarehouseID"]["value"],
                "order_qty": line["OrderQty"]["value"],
                "received_qty": line["QtyOnReceipts"]["value"],
                "completed": line["Completed"]["value"],
                "cancelled": line["Cancelled"]["value"],
                "promised": "",
            }
            for line in LOADED_ROW["Details"]
        ],
    }
    order = tracking._classify_order(release_row("PO003077"), erp, TODAY)
    assert order["order_nbr"] == "PO003077"
    assert order["state"] == "awaiting_approval"
    assert order["days_waiting"] is None
    assert order["days_late"] is None
