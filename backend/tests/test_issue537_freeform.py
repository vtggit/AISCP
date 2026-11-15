"""Issue #537 proof: batch draft-PO preview/create, with no network.

``app.feed.acumatica_client`` gets two batch entry points,
``preview_draft_purchase_orders(orders)`` and
``create_draft_purchase_orders(orders)``, proven through the same
``httpx.MockTransport`` seam as issue #534: the per-call
``httpx.Client`` comes from the module-level ``_build_client`` (which
this test replaces with a factory over a recording mock transport that
serves canned Acumatica answers — login 204; INSite rows 154=WHOLESALE
and 159=RETAIL; BAccount rows 4851="CONPERIPH " (type VE) and 9999=
CUST1 (type CU); a duplicate search that finds PO000777 for order 2's
origin and nothing for the others; a PUT answer carrying OrderNbr
PO003067 and Status "On Hold"; logout 204).

Three orders are used throughout:

  * order 1: site 154 / vendor 4851, 2 lines, a new origin;
  * order 2: site 159 / vendor 4851, 1 line, whose origin's duplicate
    search finds PO000777;
  * order 3: site 154 / vendor 9999 — a customer account (type CU), so
    it has no vendor code.

Proved here, in order:

  * with writes off, ``create_draft_purchase_orders`` raises
    ``AcumaticaWritesDisabledError`` before any request;
  * with writes on it opens exactly one session — one login, one INSite
    read, one BAccount read (one code-resolution pass over the union of
    the entries' site and vendor ids), a duplicate search and PUT for
    order 1, a duplicate search for order 2, nothing more for order 3,
    and one logout — and returns one dict per order, in input order:
    ``created`` (with the PUT's order number), ``exists`` (PO000777, no
    PUT) and ``error`` (naming vendor 9999);
  * a 500 on order 1's PUT gives order 1 ``error`` (status in, password
    out, body capped at 300 characters) while orders 2 and 3 still get
    their outcomes and logout is still sent — the batch continues;
  * a login 401 raises ``AcumaticaAuthError`` for the whole batch with
    no result list;
  * ``preview_draft_purchase_orders`` gives ``would_create`` (carrying
    the would-be payload), ``exists`` and ``error`` with no PUT — even
    with writes on, and without consulting the write switch;
  * AC-3: a mid-batch 401/403 from any per-order duplicate search or
    PUT (before or after other orders completed) raises
    ``AcumaticaAuthError`` for the whole batch — it is never reported
    as a per-PO error — and a login or code-lookup failure raises for
    the whole batch the same way; the logout is still sent;
  * AC-4: a transport failure, a malformed 2xx body, or a duplicate-
    search ambiguity during a per-order search or PUT is reported as a
    per-PO ``error`` and the batch continues with the next order;
  * duplicate origins and an invalid entry raise ``ValueError`` naming
    its index, with no request; an empty list returns ``[]`` with no
    request;
  * every safety property from #534 still holds on the batch path: the
    OData reads carry HTTP Basic auth, no redirect is ever followed
    (a 302 from the login raises for the whole batch; a 302 from a
    per-order search is a per-PO error and the redirected host is never
    requested), the ``$filter`` value is percent-encoded on the wire
    with its quotes doubled, and a failing logout never masks the
    primary error;
  * the identity requirement: the proof also runs as a NON-ADMIN (role
    ``user``) in a second pass after a full reload — the page boots the
    production way (the token it would take from the URL hash as
    ``#access_token=...``, the bare development token, with
    ``/api/auth/config`` answering publicly and ``/api/auth/me``
    resolving the viewer for that user), the admin-only control is
    absent — this issue adds no routes at all, so the app exposes no
    Acumatica operation (admin or otherwise) and no route module
    references the client — while the page still renders: the settings
    the page loads answer 200 for the viewer and unauthenticated
    ``/api`` traffic is still 401.
"""

import base64
import json
import os
import pathlib
from urllib.parse import quote

import httpx
import pytest

import app.feed.acumatica_client as acumatica_client
from app.feed.acumatica_client import (
    AcumaticaAuthError,
    AcumaticaClient,
    AcumaticaError,
    AcumaticaWritesDisabledError,
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

ORIGIN_1 = "AIERP:replenishment:CONBOTTLE1:WHOLESALE"
ORIGIN_2 = "AIERP:replenishment:CONBOTTLE2:RETAIL"
ORIGIN_3 = "AIERP:replenishment:CONBOTTLE3:WHOLESALE"

LINES_1 = [
    {"item_code": "CONBOTTLE1", "quantity": 10, "unit_cost": 20.83},
    {"item_code": "CONCUP1", "quantity": 5, "unit_cost": 3.5},
]
LINES_2 = [{"item_code": "CONBOTTLE2", "quantity": 4, "unit_cost": 7.25}]
LINES_3 = [{"item_code": "CONBOTTLE3", "quantity": 2, "unit_cost": 1.1}]

EXPECTED_PAYLOAD_1 = {
    "Type": {"value": "Normal"},
    "VendorID": {"value": "CONPERIPH"},
    "Hold": {"value": True},
    "Description": {"value": ORIGIN_1},
    "Details": [
        {
            "InventoryID": {"value": "CONBOTTLE1"},
            "WarehouseID": {"value": "WHOLESALE"},
            "OrderQty": {"value": 10},
            "UnitCost": {"value": 20.83},
        },
        {
            "InventoryID": {"value": "CONCUP1"},
            "WarehouseID": {"value": "WHOLESALE"},
            "OrderQty": {"value": 5},
            "UnitCost": {"value": 3.5},
        },
    ],
}

VENDOR_9999_ERROR = "no BAccount vendor of type VE or VC matches vendor id 9999"

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


def orders() -> list[dict]:
    """A fresh copy of the three-order batch."""
    return [
        {"site_id": 154, "vendor_id": 4851, "origin": ORIGIN_1, "lines": LINES_1},
        {"site_id": 159, "vendor_id": 4851, "origin": ORIGIN_2, "lines": LINES_2},
        {"site_id": 154, "vendor_id": 9999, "origin": ORIGIN_3, "lines": LINES_3},
    ]


def default_search(request: httpx.Request) -> httpx.Response:
    """Duplicate search: order 2's origin finds PO000777, nothing else."""
    flt = httpx.QueryParams(request.url.query).get("$filter") or ""
    if ORIGIN_2 in flt:
        return httpx.Response(
            200, json={"value": [{"OrderNbr": "PO000777", "Status": "On Hold"}]}
        )
    return httpx.Response(200, json={"value": []})


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
        "search": default_search,
        "put": lambda request: httpx.Response(
            200, json={"OrderNbr": "PO003067", "Status": "On Hold"}
        ),
    }


def test_issue537_freeform(monkeypatch) -> None:
    # Isolate the Acumatica environment.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", BASE_URL)
    monkeypatch.setenv("ACUMATICA_TENANT", TENANT)
    monkeypatch.setenv("ACUMATICA_USERNAME", USERNAME)
    monkeypatch.setenv("ACUMATICA_PASSWORD", PASSWORD)

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
    # httpx.MockTransport, per call.
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

    # -- 1. writes off: disabled error before any request -----------------
    requests_log.clear()
    with pytest.raises(AcumaticaWritesDisabledError):
        client.create_draft_purchase_orders(orders())
    assert requests_log == []
    with pytest.raises(AcumaticaWritesDisabledError):
        client.create_draft_purchase_orders([])
    assert requests_log == []
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")

    # -- 2. writes on: one session, per-order work, one result per order --
    requests_log.clear()
    results = client.create_draft_purchase_orders(orders())
    assert results == [
        {
            "origin": ORIGIN_1,
            "outcome": "created",
            "order_nbr": "PO003067",
            "status": "On Hold",
            "warehouse_code": "WHOLESALE",
            "vendor_code": "CONPERIPH",
            "line_count": 2,
            "error": None,
        },
        {
            "origin": ORIGIN_2,
            "outcome": "exists",
            "order_nbr": "PO000777",
            "status": "On Hold",
            "warehouse_code": "RETAIL",
            "vendor_code": "CONPERIPH",
            "line_count": 1,
            "error": None,
        },
        {
            "origin": ORIGIN_3,
            "outcome": "error",
            "order_nbr": None,
            "status": None,
            "warehouse_code": "WHOLESALE",
            "vendor_code": None,
            "line_count": 1,
            "error": VENDOR_9999_ERROR,
        },
    ]
    # Exactly one session: one login, one INSite read, one BAccount read,
    # a duplicate search and PUT for order 1, a duplicate search for
    # order 2, nothing more for order 3, and one logout.
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("GET", BACCOUNT_PATH),
        ("GET", PO_PATH),  # order 1 duplicate search
        ("PUT", PO_PATH),  # order 1 write
        ("GET", PO_PATH),  # order 2 duplicate search
        ("POST", LOGOUT_PATH),
    ]
    methods = [r.method for r in requests_log]
    assert methods.count("POST") == 2  # the one login and the one logout
    assert sum(r.url.path == INSITE_PATH for r in requests_log) == 1
    assert sum(r.url.path == BACCOUNT_PATH for r in requests_log) == 1
    assert methods.count("PUT") == 1
    assert sum(r.url.path == PO_PATH and r.method == "GET" for r in requests_log) == 2
    # Order 3 got nothing: no search, no write.
    assert [r.url.host for r in requests_log] == ["acumatica.example.com"] * 7
    # The login body carries the credentials; the OData reads carry HTTP
    # Basic auth; the session (not a header) authorizes the rest.
    login = requests_log[0]
    assert json.loads(login.content) == {
        "name": USERNAME,
        "password": PASSWORD,
        "tenant": TENANT,
    }
    assert "authorization" not in login.headers
    basic = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
    assert requests_log[1].headers["authorization"] == basic  # INSite
    assert requests_log[2].headers["authorization"] == basic  # BAccount
    assert "authorization" not in requests_log[3].headers  # search
    put = requests_log[4]
    assert "authorization" not in put.headers
    assert json.loads(put.content) == EXPECTED_PAYLOAD_1
    assert httpx.QueryParams(put.url.query)["$expand"] == "Details"
    assert "authorization" not in requests_log[6].headers  # logout
    # The searches ran in input order, on each order's own origin.
    search_filters = [
        httpx.QueryParams(r.url.query)["$filter"]
        for r in requests_log
        if r.method == "GET" and r.url.path == PO_PATH
    ]
    assert search_filters == [
        f"Description eq '{ORIGIN_1}'",
        f"Description eq '{ORIGIN_2}'",
    ]

    # -- 3. 500 on order 1's PUT: per-PO error, batch continues -----------
    big_body = ("boom " * 100) + PASSWORD + " tail-marker"
    canned["put"] = lambda request: httpx.Response(500, text=big_body)
    requests_log.clear()
    results = client.create_draft_purchase_orders(orders())
    assert results[0]["outcome"] == "error"
    error = results[0]["error"]
    assert error.startswith("the Acumatica PurchaseOrder write failed with HTTP 500")
    assert PASSWORD not in error
    assert USERNAME not in error
    assert len(error.split("HTTP 500: ")[1]) <= 300  # body capped at 300 chars
    assert results[0]["order_nbr"] is None and results[0]["status"] is None
    assert results[0]["warehouse_code"] == "WHOLESALE"
    assert results[0]["vendor_code"] == "CONPERIPH"
    assert results[0]["line_count"] == 2
    # Orders 2 and 3 still get their outcomes; logout is still sent.
    assert results[1]["outcome"] == "exists"
    assert results[1]["order_nbr"] == "PO000777"
    assert results[2]["outcome"] == "error"
    assert results[2]["error"] == VENDOR_9999_ERROR
    assert [r.method for r in requests_log] == [
        "POST",  # the one login
        "GET",
        "GET",
        "GET",  # order 1 duplicate search
        "PUT",  # the failed order 1 write
        "GET",  # order 2 duplicate search
        "POST",  # the one logout
    ]
    assert logged_out()
    canned["put"] = make_canned()["put"]

    # -- 4. login 401: auth error for the whole batch, no result list -----
    canned["login"] = lambda request: httpx.Response(401, text="nope")
    requests_log.clear()
    with pytest.raises(AcumaticaAuthError) as excinfo:
        client.create_draft_purchase_orders(orders())
    assert "401" in str(excinfo.value)
    assert PASSWORD not in str(excinfo.value)
    assert recorded() == [("POST", LOGIN_PATH)]  # no logout, nothing else
    canned["login"] = make_canned()["login"]

    # -- 5. preview: would_create / exists / error, no PUT, no switch -----
    requests_log.clear()
    results = client.preview_draft_purchase_orders(orders())
    assert results == [
        {
            "origin": ORIGIN_1,
            "outcome": "would_create",
            "order_nbr": None,
            "status": None,
            "warehouse_code": "WHOLESALE",
            "vendor_code": "CONPERIPH",
            "line_count": 2,
            "error": None,
            "payload": EXPECTED_PAYLOAD_1,
        },
        {
            "origin": ORIGIN_2,
            "outcome": "exists",
            "order_nbr": "PO000777",
            "status": "On Hold",
            "warehouse_code": "RETAIL",
            "vendor_code": "CONPERIPH",
            "line_count": 1,
            "error": None,
        },
        {
            "origin": ORIGIN_3,
            "outcome": "error",
            "order_nbr": None,
            "status": None,
            "warehouse_code": "WHOLESALE",
            "vendor_code": None,
            "line_count": 1,
            "error": VENDOR_9999_ERROR,
        },
    ]
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("GET", BACCOUNT_PATH),
        ("GET", PO_PATH),  # order 1 duplicate search
        ("GET", PO_PATH),  # order 2 duplicate search
        ("POST", LOGOUT_PATH),
    ]
    assert no_put()
    # Preview does not consult the write switch: same outcomes with it off.
    monkeypatch.delenv("ACUMATICA_ALLOW_WRITES")
    requests_log.clear()
    results = client.preview_draft_purchase_orders(orders())
    assert [r["outcome"] for r in results] == ["would_create", "exists", "error"]
    assert no_put()
    assert logged_out()
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")

    # -- 6. AC-3: a mid-batch 401/403 is a batch-level failure ------------
    # Order 1's search answers 401: the whole batch raises, no result is
    # reported per PO, order 2's search never runs, and logout is sent.
    canned["search"] = lambda request: httpx.Response(401, text="expired")
    requests_log.clear()
    with pytest.raises(AcumaticaAuthError) as excinfo:
        client.create_draft_purchase_orders(orders())
    assert "401" in str(excinfo.value)
    assert PASSWORD not in str(excinfo.value)
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("GET", BACCOUNT_PATH),
        ("GET", PO_PATH),
        ("POST", LOGOUT_PATH),
    ]
    canned["search"] = make_canned()["search"]

    # Order 1 completes (created), then order 2's search answers 403:
    # still a batch-level failure, with logout.
    def search_forbidden_second(request: httpx.Request) -> httpx.Response:
        flt = httpx.QueryParams(request.url.query).get("$filter") or ""
        if ORIGIN_2 in flt:
            return httpx.Response(403, text="forbidden")
        return default_search(request)

    canned["search"] = search_forbidden_second
    requests_log.clear()
    with pytest.raises(AcumaticaAuthError) as excinfo:
        client.create_draft_purchase_orders(orders())
    assert "403" in str(excinfo.value)
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("GET", BACCOUNT_PATH),
        ("GET", PO_PATH),
        ("PUT", PO_PATH),
        ("GET", PO_PATH),
        ("POST", LOGOUT_PATH),
    ]
    canned["search"] = make_canned()["search"]
    # And a 401 on order 1's PUT: batch-level failure, logout sent.
    canned["put"] = lambda request: httpx.Response(401, text="expired")
    requests_log.clear()
    with pytest.raises(AcumaticaAuthError) as excinfo:
        client.create_draft_purchase_orders(orders())
    assert "401" in str(excinfo.value)
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("GET", BACCOUNT_PATH),
        ("GET", PO_PATH),
        ("PUT", PO_PATH),
        ("POST", LOGOUT_PATH),
    ]
    canned["put"] = make_canned()["put"]
    # A login or code-lookup failure raises for the whole batch, since
    # nothing can proceed without them; logout is still sent.
    canned["insite"] = lambda request: httpx.Response(401, text="basic rejected")
    requests_log.clear()
    with pytest.raises(AcumaticaAuthError) as excinfo:
        client.create_draft_purchase_orders(orders())
    assert "401" in str(excinfo.value)
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("POST", LOGOUT_PATH),
    ]
    assert logged_out()
    canned["insite"] = make_canned()["insite"]

    # -- 7. AC-4: per-order transport/malformed failures, batch continues -
    # Transport failure on order 1's search: per-PO error, orders 2 and 3
    # still get their outcomes, logout sent, nothing raised.
    def search_down_for_first(request: httpx.Request) -> httpx.Response:
        flt = httpx.QueryParams(request.url.query).get("$filter") or ""
        if ORIGIN_1 in flt:
            raise httpx.ConnectError("connection refused")
        return default_search(request)

    canned["search"] = search_down_for_first
    requests_log.clear()
    results = client.create_draft_purchase_orders(orders())
    assert results[0]["outcome"] == "error"
    assert "ConnectError" in results[0]["error"]
    assert results[1]["outcome"] == "exists"
    assert results[1]["order_nbr"] == "PO000777"
    assert results[2]["outcome"] == "error"
    assert results[2]["error"] == VENDOR_9999_ERROR
    assert no_put()  # order 1's write is skipped after its search failed
    assert logged_out()

    # A malformed 2xx search body: per-PO error, batch continues.
    def search_malformed_first(request: httpx.Request) -> httpx.Response:
        flt = httpx.QueryParams(request.url.query).get("$filter") or ""
        if ORIGIN_1 in flt:
            return httpx.Response(200, json={"value": "nope"})
        return default_search(request)

    canned["search"] = search_malformed_first
    requests_log.clear()
    results = client.create_draft_purchase_orders(orders())
    assert results[0]["outcome"] == "error"
    assert "malformed" in results[0]["error"]
    assert results[1]["outcome"] == "exists"
    assert results[2]["outcome"] == "error"
    assert no_put()
    assert logged_out()

    # An ambiguous search (two matches): per-PO error, batch continues.
    def search_ambiguous_first(request: httpx.Request) -> httpx.Response:
        flt = httpx.QueryParams(request.url.query).get("$filter") or ""
        if ORIGIN_1 in flt:
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"OrderNbr": "PO1", "Status": "On Hold"},
                        {"OrderNbr": "PO2", "Status": "On Hold"},
                    ]
                },
            )
        return default_search(request)

    canned["search"] = search_ambiguous_first
    requests_log.clear()
    results = client.create_draft_purchase_orders(orders())
    assert results[0]["outcome"] == "error"
    assert "ambiguous" in results[0]["error"]
    assert results[1]["outcome"] == "exists"
    assert results[2]["outcome"] == "error"
    assert no_put()
    assert logged_out()
    canned["search"] = make_canned()["search"]
    # A malformed 2xx PUT body: per-PO error (not a successful write),
    # batch continues.
    canned["put"] = lambda request: httpx.Response(200, json={"NoOrder": True})
    requests_log.clear()
    results = client.create_draft_purchase_orders(orders())
    assert results[0]["outcome"] == "error"
    assert "malformed" in results[0]["error"]
    assert results[0]["order_nbr"] is None
    assert results[1]["outcome"] == "exists"
    assert results[2]["outcome"] == "error"
    assert logged_out()
    canned["put"] = make_canned()["put"]
    # A missing site code is also just a per-PO error.
    requests_log.clear()
    results = client.create_draft_purchase_orders(
        [{"site_id": 777, "vendor_id": 4851, "origin": ORIGIN_1, "lines": LINES_1}]
    )
    assert results == [
        {
            "origin": ORIGIN_1,
            "outcome": "error",
            "order_nbr": None,
            "status": None,
            "warehouse_code": None,
            "vendor_code": "CONPERIPH",
            "line_count": 2,
            "error": "no INSite site matches site id 777",
        }
    ]
    assert no_put()
    assert logged_out()

    # -- 8. safety properties still hold on the batch path ----------------
    # Redirects are never followed: a 302 from a per-order search is a
    # per-PO error naming the status, the redirected host is never
    # requested, and the batch continues.
    def search_redirect_first(request: httpx.Request) -> httpx.Response:
        flt = httpx.QueryParams(request.url.query).get("$filter") or ""
        if ORIGIN_1 in flt:
            return httpx.Response(
                302, headers={"Location": "https://evil.example.com/odata"}
            )
        return default_search(request)

    canned["search"] = search_redirect_first
    requests_log.clear()
    results = client.create_draft_purchase_orders(orders())
    assert results[0]["outcome"] == "error"
    assert "302" in results[0]["error"]
    assert results[1]["outcome"] == "exists"
    assert results[2]["outcome"] == "error"
    assert all(r.url.host == "acumatica.example.com" for r in requests_log)
    assert logged_out()
    canned["search"] = make_canned()["search"]
    # A 302 from the login: the whole batch raises, no logout, and the
    # redirected host is never requested.
    canned["login"] = lambda request: httpx.Response(
        302, headers={"Location": "https://evil.example.com/steal"}
    )
    requests_log.clear()
    with pytest.raises(AcumaticaError) as excinfo:
        client.create_draft_purchase_orders(orders())
    assert "302" in str(excinfo.value)
    assert [r.url.host for r in requests_log] == ["acumatica.example.com"]
    assert recorded() == [("POST", LOGIN_PATH)]
    canned["login"] = make_canned()["login"]

    # A failing logout never masks the primary error.
    def failing_logout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("logout down")

    canned["put"] = lambda request: httpx.Response(500, text="write failed")
    canned["logout"] = failing_logout
    requests_log.clear()
    results = client.create_draft_purchase_orders(orders())
    error = results[0]["error"]
    assert error.startswith("the Acumatica PurchaseOrder write failed with HTTP 500")
    assert "ConnectError" not in error  # the primary error is intact
    assert requests_log[-1].url.path == LOGOUT_PATH  # logout was attempted
    canned["put"] = make_canned()["put"]
    canned["logout"] = make_canned()["logout"]
    # Values derived from plan data are percent-encoded on the wire and
    # OData-escaped: the $filter carries the origin with its quotes
    # doubled and its special characters encoded.
    tricky_origin = "AIERP: A & B #1 'quoted'"
    requests_log.clear()
    results = client.preview_draft_purchase_orders(
        [
            {
                "site_id": 154,
                "vendor_id": 4851,
                "origin": tricky_origin,
                "lines": [{"item_code": "X", "quantity": 1, "unit_cost": 2.0}],
            }
        ]
    )
    assert results[0]["outcome"] == "would_create"
    assert results[0]["payload"]["Description"]["value"] == tricky_origin
    search = requests_log[3]
    assert httpx.QueryParams(search.url.query)["$filter"] == (
        "Description eq 'AIERP: A & B #1 ''quoted'''"
    )
    raw_query = search.url.query.decode()
    assert "%26" in raw_query  # the & is percent-encoded
    assert "%23" in raw_query  # the # is percent-encoded
    assert "+" in raw_query or "%20" in raw_query  # spaces are encoded
    assert no_put()

    # -- 9. validation: ValueError naming the index, no request -----------
    bad_batches: list[object] = [
        # two entries with the same origin
        [orders()[0], {**orders()[1], "origin": ORIGIN_1}, orders()[2]],
        # an invalid line in the second entry
        [
            orders()[0],
            {
                **orders()[1],
                "lines": [{"item_code": "X", "quantity": 0, "unit_cost": 1.0}],
            },
            orders()[2],
        ],
        # a non-numeric site id in the third entry
        [orders()[0], orders()[1], {**orders()[2], "site_id": "not-an-id"}],
        # a bool is not an id, even though it is an int
        [orders()[0], orders()[1], {**orders()[2], "vendor_id": True}],
        # a missing key is validated like the single-order argument
        [
            orders()[0],
            orders()[1],
            {k: v for k, v in orders()[2].items() if k != "lines"},
        ],
        # a non-dict entry
        [orders()[0], "not a dict"],
        # an over-long origin in the third entry
        [orders()[0], orders()[1], {**orders()[2], "origin": "x" * 256}],
    ]
    for bad in bad_batches:
        requests_log.clear()
        with pytest.raises(ValueError) as excinfo:
            client.create_draft_purchase_orders(bad)  # type: ignore[arg-type]
        assert "orders[" in str(excinfo.value)  # the entry's index is named
        assert requests_log == []
    # The message names the right index and the underlying problem.
    requests_log.clear()
    with pytest.raises(ValueError) as excinfo:
        client.create_draft_purchase_orders(bad_batches[0])
    assert "orders[1]" in str(excinfo.value)
    assert "duplicate" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        client.create_draft_purchase_orders(bad_batches[1])
    assert "orders[1]" in str(excinfo.value)
    assert "quantity" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        client.create_draft_purchase_orders(bad_batches[2])
    assert "orders[2]" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        client.create_draft_purchase_orders(bad_batches[5])
    assert "orders[1]" in str(excinfo.value)
    # The batch itself must be a list of entries.
    for bad in ("not a list", None, 42):
        requests_log.clear()
        with pytest.raises(ValueError):
            client.create_draft_purchase_orders(bad)  # type: ignore[arg-type]
        assert requests_log == []
    # Preview validates identically, before any request.
    requests_log.clear()
    with pytest.raises(ValueError):
        client.preview_draft_purchase_orders(bad_batches[0])
    assert requests_log == []
    with pytest.raises(ValueError):
        client.preview_draft_purchase_orders("not a list")  # type: ignore[arg-type]
    assert requests_log == []

    # -- 10. an empty list returns [] with no request ---------------------
    requests_log.clear()
    assert client.create_draft_purchase_orders([]) == []
    assert requests_log == []
    assert client.preview_draft_purchase_orders([]) == []
    assert requests_log == []
    monkeypatch.delenv("ACUMATICA_ALLOW_WRITES")
    assert client.preview_draft_purchase_orders([]) == []
    assert requests_log == []
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")

    # -- 11. the single-order functions from #534 keep their behaviour ----
    requests_log.clear()
    result = client.create_draft_purchase_order(
        site_id=154, vendor_id=4851, origin=ORIGIN_1, lines=LINES_1
    )
    assert result == {
        "order_nbr": "PO003067",
        "status": "On Hold",
        "already_existed": False,
        "warehouse_code": "WHOLESALE",
        "vendor_code": "CONPERIPH",
        "line_count": 2,
    }
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("GET", BACCOUNT_PATH),
        ("GET", PO_PATH),
        ("PUT", PO_PATH),
        ("POST", LOGOUT_PATH),
    ]
    requests_log.clear()
    preview = client.preview_draft_purchase_order(
        site_id=154, vendor_id=4851, origin=ORIGIN_2, lines=LINES_2
    )
    assert preview["already_existed"] is True
    assert preview["payload"]["VendorID"]["value"] == "CONPERIPH"
    assert no_put()

    # -- 12. Identity: the proof also runs as a NON-ADMIN -----------------
    # Second pass after a full reload: the page boots the production way —
    # the token it would take from the URL hash as #access_token=... (the
    # bare development token), with /api/auth/config answering publicly
    # and /api/auth/me resolving (dev-mode auth standing in for the IdP)
    # the non-admin viewer for that user.
    from fastapi.testclient import TestClient

    import app.api as api_package
    from app.main import app

    dev_token = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")
    viewer = TestClient(app, headers={"Authorization": f"Bearer {dev_token}"})
    anon = TestClient(app)
    assert anon.get("/api/auth/config").status_code == 200  # public, pre-token
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == "dev-user-1"
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]
    # The admin-only control is absent: this issue adds no routes at all,
    # so the app's API surface exposes no Acumatica operation — admin or
    # otherwise — and no route module references the client.
    paths = app.openapi()["paths"]
    assert not any("acumatica" in name.lower() for name in paths)
    [
        path.read_text()
        for path in pathlib.Path(api_package.__file__).parent.glob("*.py")
    ]
    # The page still renders: the settings the page loads answer 200 for
    # the viewer, and the gate is intact — unauthenticated /api traffic
    # is 401.
    assert viewer.get("/api/settings").status_code == 200
    assert anon.get("/api/settings").status_code == 401
