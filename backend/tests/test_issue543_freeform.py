"""Issue #543 proof: contract-based REST shapes for the draft-PO client.

The live Acumatica entity API does not always wrap the duplicate-search
answer in ``{"value": [...]}`` and returns scalar fields as ``{"value":
...}`` envelopes, so the client must accept the contract-based REST
shapes: a duplicate-search body that is a bare JSON array is the list
of rows (an object with a ``"value"`` array is still accepted as
before), and ``OrderNbr`` and ``Status`` — in each search row and in
the PUT answer object — are read either as a plain non-empty string or
as an object whose ``"value"`` is a plain non-empty string, and are
returned as plain strings. A missing, null, empty, or otherwise typed
field is malformed.

Proved here, with no network, through the ``httpx.MockTransport`` seam
(the module-level ``_build_client`` is replaced with a factory over a
recording mock transport that serves the live-captured bodies verbatim
— login 204; INSite rows 154=WHOLESALE and 159=RETAIL; BAccount rows
4851="CONPERIPH " (type VE) and 9999=CUST1 (type CU); the bare-array
hit for PO000224; the ``[]`` miss; the PUT answer ``{"id": ...,
"OrderNbr": {"value": "PO003067"}, "Status": {"value": "On Hold"}}``;
logout 204):

  * ``preview_draft_purchase_orders`` returns ``exists`` with
    ``order_nbr`` "PO000224" for the bare-array hit and ``would_create``
    for the ``[]`` miss;
  * ``create_draft_purchase_orders`` returns ``created`` with
    ``order_nbr`` "PO003067" and ``status`` "On Hold" for the miss,
    sending the documented PUT body;
  * two rows in a bare array give an ambiguous ``error``; a row with
    ``"OrderNbr": {"value": null}``, a row with ``"OrderNbr":
    {"value": ""}``, and a bare-array body of ``["x"]`` each give a
    malformed ``error`` — a per-order error in the batch, the batch
    continues and the logout is still sent;
  * a PUT answer without an ``OrderNbr`` gives a malformed ``error``;
  * the legacy shapes the #534/#537 tests use (``{"value": [...]}``
    with plain string fields, and the plain-string PUT answer) still
    work;
  * AC-3: the duplicate search accepts a full Acumatica entity
    envelope — a row carrying extra ``id``, ``rowNumber``, ``note``,
    ``custom`` and ``_links`` metadata, bare or wrapped — and rejects
    missing, empty, or non-string ``OrderNbr`` / ``Status`` fields in
    the search rows and in the PUT answer with an explicit
    ``AcumaticaError``;
  * auth errors still abort: a 401 from a duplicate search raises
    ``AcumaticaAuthError`` for the whole batch, and every request
    stays on the configured host.
"""

import json

import httpx
import pytest

import app.feed.acumatica_client as acumatica_client
from app.feed.acumatica_client import (
    AcumaticaAuthError,
    AcumaticaClient,
    AcumaticaError,
)

BASE_URL = "https://acumatica.example.com/Instance"
TENANT = "AIERP"
USERNAME = "svc-aierp"
PASSWORD = "s3cret-password!"
VERSION = "25.200.001"

LOGIN_PATH = "/Instance/entity/auth/login"
LOGOUT_PATH = "/Instance/entity/auth/logout"
INSITE_PATH = f"/Instance/odatav4/{TENANT}/INSite"
BACCOUNT_PATH = f"/Instance/odatav4/{TENANT}/BAccount"
PO_PATH = f"/Instance/entity/Default/{VERSION}/PurchaseOrder"

ORIGIN = "AIERP:replenishment:CONBOTTLE1:WHOLESALE"
LINES = [{"item_code": "CONBOTTLE1", "quantity": 10, "unit_cost": 20.83}]

EXPECTED_PAYLOAD = {
    "Type": {"value": "Normal"},
    "VendorID": {"value": "CONPERIPH"},
    "Hold": {"value": True},
    "Description": {"value": ORIGIN},
    "Details": [
        {
            "InventoryID": {"value": "CONBOTTLE1"},
            "WarehouseID": {"value": "WHOLESALE"},
            "OrderQty": {"value": 10},
            "UnitCost": {"value": 20.83},
        }
    ],
}

# The live-captured duplicate-search bodies (issue #543), verbatim: the
# bare-array hit for PO000224 and the [] miss.
HIT_BODY = [{"OrderNbr": {"value": "PO000224"}, "Status": {"value": "On Hold"}}]
MISS_BODY = []
# The live-captured PUT answer for the created order.
PUT_ANSWER = {
    "id": "PO-20261021-003067",
    "OrderNbr": {"value": "PO003067"},
    "Status": {"value": "On Hold"},
}
# A full Acumatica entity envelope: the required fields plus the extra
# metadata the entity API carries on every row.
ENVELOPE_ROW = {
    "id": "PO-20261021-000224",
    "rowNumber": 0,
    "note": None,
    "custom": {},
    "_links": {"self": {"href": f"{BASE_URL}/entity/Default/{VERSION}/PurchaseOrder"}},
    "OrderNbr": {"value": "PO000224"},
    "Status": {"value": "On Hold"},
}

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


def make_canned() -> dict:
    """The canned Acumatica answers for the mock transport."""
    return {
        "login": lambda request: httpx.Response(204),
        "logout": lambda request: httpx.Response(204),
        "insite": lambda request: httpx.Response(
            200,
            json={
                "value": [
                    {"SiteID": 154, "SiteCD": "WHOLESALE"},
                    {"SiteID": 159, "SiteCD": "RETAIL"},
                ]
            },
        ),
        "baccount": lambda request: httpx.Response(
            200,
            json={
                "value": [
                    {"BAccountID": 4851, "AcctCD": "CONPERIPH ", "Type": "VE"},
                    {"BAccountID": 9999, "AcctCD": "CUST1", "Type": "CU"},
                ]
            },
        ),
        "search": lambda request: httpx.Response(200, json=MISS_BODY),
        "put": lambda request: httpx.Response(200, json=PUT_ANSWER),
    }


def test_issue543_freeform(monkeypatch) -> None:
    # Isolate the Acumatica environment.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", BASE_URL)
    monkeypatch.setenv("ACUMATICA_TENANT", TENANT)
    monkeypatch.setenv("ACUMATICA_USERNAME", USERNAME)
    monkeypatch.setenv("ACUMATICA_PASSWORD", PASSWORD)
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")

    requests_log: list[httpx.Request] = []
    canned = make_canned()

    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/entity/auth/login"):
            return canned["login"](request)
        if request.method == "POST" and path.endswith("/entity/auth/logout"):
            return canned["logout"](request)
        if request.method == "GET" and path.endswith("/INSite"):
            return canned["insite"](request)
        if request.method == "GET" and path.endswith("/BAccount"):
            return canned["baccount"](request)
        if request.method == "GET" and path.endswith("/PurchaseOrder"):
            return canned["search"](request)
        if request.method == "PUT" and path.endswith("/PurchaseOrder"):
            return canned["put"](request)
        return httpx.Response(404, text="unexpected request")

    # _build_client is replaced: one httpx.Client over a recording
    # httpx.MockTransport, per call — no network.
    monkeypatch.setattr(
        acumatica_client,
        "_build_client",
        lambda ca_bundle, timeout: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client = AcumaticaClient()

    def recorded() -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in requests_log]

    def no_put() -> bool:
        return not any(r.method == "PUT" for r in requests_log)

    def logged_out() -> bool:
        return (
            requests_log
            and requests_log[-1].method == "POST"
            and requests_log[-1].url.path == LOGOUT_PATH
        )

    def batch_orders() -> list[dict]:
        return [{"site_id": 154, "vendor_id": 4851, "origin": ORIGIN, "lines": LINES}]

    # -- 1. AC-2: the bare-array hit -> exists, with no PUT ---------------
    canned["search"] = lambda request: httpx.Response(200, json=HIT_BODY)
    requests_log.clear()
    results = client.preview_draft_purchase_orders(batch_orders())
    assert results == [
        {
            "origin": ORIGIN,
            "outcome": "exists",
            "order_nbr": "PO000224",
            "status": "On Hold",
            "warehouse_code": "WHOLESALE",
            "vendor_code": "CONPERIPH",
            "line_count": 1,
            "error": None,
        }
    ]
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("GET", BACCOUNT_PATH),
        ("GET", PO_PATH),
        ("POST", LOGOUT_PATH),
    ]
    assert no_put()
    assert logged_out()

    # -- 2. AC-2: the [] miss -> would_create, carrying the payload -------
    canned["search"] = make_canned()["search"]
    requests_log.clear()
    results = client.preview_draft_purchase_orders(batch_orders())
    assert results == [
        {
            "origin": ORIGIN,
            "outcome": "would_create",
            "order_nbr": None,
            "status": None,
            "warehouse_code": "WHOLESALE",
            "vendor_code": "CONPERIPH",
            "line_count": 1,
            "error": None,
            "payload": EXPECTED_PAYLOAD,
        }
    ]
    assert no_put()
    assert logged_out()
    # Preview does not consult the write switch.
    monkeypatch.delenv("ACUMATICA_ALLOW_WRITES")
    requests_log.clear()
    results = client.preview_draft_purchase_orders(batch_orders())
    assert results[0]["outcome"] == "would_create"
    assert no_put()
    assert logged_out()
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")

    # -- 3. AC-2: the miss + the captured PUT answer -> created -----------
    requests_log.clear()
    results = client.create_draft_purchase_orders(batch_orders())
    assert results == [
        {
            "origin": ORIGIN,
            "outcome": "created",
            "order_nbr": "PO003067",
            "status": "On Hold",
            "warehouse_code": "WHOLESALE",
            "vendor_code": "CONPERIPH",
            "line_count": 1,
            "error": None,
        }
    ]
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("GET", BACCOUNT_PATH),
        ("GET", PO_PATH),
        ("PUT", PO_PATH),
        ("POST", LOGOUT_PATH),
    ]
    put_request = requests_log[4]
    assert json.loads(put_request.content) == EXPECTED_PAYLOAD
    assert httpx.QueryParams(put_request.url.query)["$expand"] == "Details"
    assert logged_out()

    # -- 4. AC-2: two rows in a bare array -> ambiguous per-order error ---
    canned["search"] = lambda request: httpx.Response(
        200,
        json=[
            {"OrderNbr": {"value": "PO000224"}, "Status": {"value": "On Hold"}},
            {"OrderNbr": {"value": "PO000225"}, "Status": {"value": "On Hold"}},
        ],
    )
    requests_log.clear()
    results = client.preview_draft_purchase_orders(batch_orders())
    assert results[0]["outcome"] == "error"
    assert "ambiguous" in results[0]["error"]
    assert results[0]["order_nbr"] is None
    assert results[0]["status"] is None
    assert no_put()
    assert logged_out()

    # -- 5. AC-2: malformed search rows -> malformed per-order error ------
    bad_search_bodies = (
        [{"OrderNbr": {"value": None}, "Status": {"value": "On Hold"}}],
        [{"OrderNbr": {"value": ""}, "Status": {"value": "On Hold"}}],
        ["x"],
    )
    for bad_body in bad_search_bodies:
        canned["search"] = lambda request, body=bad_body: httpx.Response(200, json=body)
        requests_log.clear()
        results = client.preview_draft_purchase_orders(batch_orders())
        assert results[0]["outcome"] == "error"
        assert "malformed" in results[0]["error"]
        assert results[0]["order_nbr"] is None
        assert no_put()
        assert logged_out()
    # The same bodies raise an explicit AcumaticaError on the single-
    # order path — an error, never a no-match.
    for bad_body in bad_search_bodies:
        canned["search"] = lambda request, body=bad_body: httpx.Response(200, json=body)
        requests_log.clear()
        with pytest.raises(AcumaticaError) as excinfo:
            client.preview_draft_purchase_order(
                site_id=154, vendor_id=4851, origin=ORIGIN, lines=LINES
            )
        assert "malformed" in str(excinfo.value)
        assert no_put()
        assert logged_out()
    canned["search"] = make_canned()["search"]

    # -- 6. AC-2: a PUT answer without OrderNbr -> malformed per-order ----
    canned["put"] = lambda request: httpx.Response(
        200,
        json={"id": "PO-20261021-003067", "Status": {"value": "On Hold"}},
    )
    requests_log.clear()
    results = client.create_draft_purchase_orders(batch_orders())
    assert results[0]["outcome"] == "error"
    assert "malformed" in results[0]["error"]
    assert results[0]["order_nbr"] is None
    assert results[0]["status"] is None
    assert logged_out()
    canned["put"] = make_canned()["put"]

    # -- 7. AC-2: the legacy shapes the #534/#537 tests use still work ----
    # {"value": [...]} with plain string fields: hit and miss.
    canned["search"] = lambda request: httpx.Response(
        200,
        json={"value": [{"OrderNbr": "PO002901", "Status": "On Hold"}]},
    )
    requests_log.clear()
    results = client.preview_draft_purchase_orders(batch_orders())
    assert results[0]["outcome"] == "exists"
    assert results[0]["order_nbr"] == "PO002901"
    assert results[0]["status"] == "On Hold"
    assert no_put()
    canned["search"] = lambda request: httpx.Response(200, json={"value": []})
    canned["put"] = lambda request: httpx.Response(
        200, json={"OrderNbr": "PO003067", "Status": "On Hold"}
    )
    requests_log.clear()
    results = client.create_draft_purchase_orders(batch_orders())
    assert results[0]["outcome"] == "created"
    assert results[0]["order_nbr"] == "PO003067"
    assert results[0]["status"] == "On Hold"
    assert logged_out()
    canned["search"] = make_canned()["search"]
    canned["put"] = make_canned()["put"]

    # -- 8. AC-3: the full entity envelope is accepted --------------------
    # Bare array and the legacy wrapper both accept a row carrying the
    # extra metadata; the fields come back as plain strings.
    for envelope_body in ([ENVELOPE_ROW], {"value": [ENVELOPE_ROW]}):
        canned["search"] = lambda request, body=envelope_body: httpx.Response(
            200, json=body
        )
        requests_log.clear()
        results = client.preview_draft_purchase_orders(batch_orders())
        assert results[0]["outcome"] == "exists"
        assert results[0]["order_nbr"] == "PO000224"
        assert results[0]["status"] == "On Hold"
        assert no_put()
        assert logged_out()
    canned["search"] = make_canned()["search"]

    # -- 9. AC-3: missing, empty, or non-string fields are rejected -------
    # Search rows: an explicit AcumaticaError on the single-order path.
    bad_search_rows = (
        [{"Status": {"value": "On Hold"}}],  # OrderNbr missing
        [{"OrderNbr": {"value": "PO000224"}}],  # Status missing
        [{"OrderNbr": "", "Status": "On Hold"}],  # OrderNbr empty
        [{"OrderNbr": "PO000224", "Status": ""}],  # Status empty
        [{"OrderNbr": 224, "Status": "On Hold"}],  # OrderNbr not a string
        [{"OrderNbr": "PO000224", "Status": 7}],  # Status not a string
        [{"OrderNbr": {"value": 224}, "Status": "On Hold"}],  # nested non-string
        [{"OrderNbr": {"value": "PO000224"}, "Status": {"value": 7}}],
        [{"OrderNbr": None, "Status": "On Hold"}],  # OrderNbr null
        [{"OrderNbr": {"value": "PO000224"}, "Status": None}],  # Status null
    )
    for bad_body in bad_search_rows:
        canned["search"] = lambda request, body=bad_body: httpx.Response(200, json=body)
        requests_log.clear()
        with pytest.raises(AcumaticaError) as excinfo:
            client.preview_draft_purchase_order(
                site_id=154, vendor_id=4851, origin=ORIGIN, lines=LINES
            )
        assert "malformed" in str(excinfo.value)
        assert no_put()
        assert logged_out()
    # ... and in the batch they are per-order errors, not batch failures.
    canned["search"] = lambda request: httpx.Response(
        200, json=[{"OrderNbr": 224, "Status": "On Hold"}]
    )
    requests_log.clear()
    results = client.preview_draft_purchase_orders(batch_orders())
    assert results[0]["outcome"] == "error"
    assert "malformed" in results[0]["error"]
    canned["search"] = make_canned()["search"]

    # PUT answers: an explicit AcumaticaError on the single-order path.
    bad_put_answers = (
        {"Status": {"value": "On Hold"}},  # OrderNbr missing
        {"OrderNbr": {"value": "PO003067"}},  # Status missing
        {"OrderNbr": "", "Status": "On Hold"},  # OrderNbr empty
        {"OrderNbr": "PO003067", "Status": 7},  # Status not a string
        {"OrderNbr": {"value": "PO003067"}, "Status": {"value": ""}},
        [{"OrderNbr": "PO003067", "Status": "On Hold"}],  # not an object
        "PO003067",  # not an object
    )
    for bad_body in bad_put_answers:
        canned["put"] = lambda request, body=bad_body: httpx.Response(200, json=body)
        requests_log.clear()
        with pytest.raises(AcumaticaError) as excinfo:
            client.create_draft_purchase_order(
                site_id=154, vendor_id=4851, origin=ORIGIN, lines=LINES
            )
        assert "malformed" in str(excinfo.value)
        assert logged_out()
    canned["put"] = make_canned()["put"]

    # -- 10. auth errors still abort the whole batch ----------------------
    canned["search"] = lambda request: httpx.Response(401, text="expired")
    requests_log.clear()
    with pytest.raises(AcumaticaAuthError) as excinfo:
        client.create_draft_purchase_orders(batch_orders())
    assert "401" in str(excinfo.value)
    assert PASSWORD not in str(excinfo.value)
    assert logged_out()

    # Every request stayed on the configured host: no network, no other
    # host, no redirect target.
    assert [r.url.host for r in requests_log] == ["acumatica.example.com"] * len(
        requests_log
    )
