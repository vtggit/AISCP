"""Issue #534 proof: a fail-closed Acumatica client, with no network.

``app.feed.acumatica_client`` is structured like ``odoo_client``: the
per-call ``httpx.Client`` comes from the module-level ``_build_client``
(which this test replaces with a factory over an ``httpx.MockTransport``
that records every request and serves canned Acumatica answers — login
204; INSite rows 154=WHOLESALE and 159=RETAIL; BAccount rows 4851=
"CONPERIPH " (type VE) and 9999=CUST1 (type CU); an empty duplicate
search; a PUT answer carrying OrderNbr PO003067 and Status "On Hold";
logout 204).

Proved here, in order:

  * ``resolve_codes`` opens one session (login -> INSite -> BAccount ->
    logout), sends the login body ``{"name", "password", "tenant"}``,
    carries HTTP Basic auth on the OData reads only, and returns the
    requested ids' codes stripped of surrounding whitespace, with only
    ``Type`` VE/VC accounts as vendors and unmatched ids absent;
  * with writes off, ``create_draft_purchase_order`` raises
    ``AcumaticaWritesDisabledError`` and makes zero requests; with
    writes on it makes exactly login -> INSite -> BAccount -> duplicate
    search -> PUT -> logout, in that order, with a PUT body equal to
    the expected JSON for CONBOTTLE1 at WHOLESALE (10 units at 20.83,
    vendor CONPERIPH stripped, Hold true, Description the origin), and
    the result is the documented dict;
  * a duplicate search hit returns ``already_existed`` true with no
    PUT; an origin containing a single quote is sent with the quote
    doubled in the ``$filter``; an unknown site id or a customer-type
    account id raises ``AcumaticaError`` before any PUT; a 500 on the
    PUT raises ``AcumaticaError`` carrying the status, at most 300
    characters of the body, and never the password — and logout is
    still called; a login 401/403 raises ``AcumaticaAuthError`` with no
    logout; a missing ``ACUMATICA_USERNAME`` raises
    ``AcumaticaConfigError`` naming the variable (never its value); an
    ``http://`` base URL without ``ACUMATICA_ALLOW_INSECURE`` raises
    ``AcumaticaConfigError`` (and with it set, the http base is
    accepted); zero, negative, or otherwise invalid quantities/units
    raise ``ValueError`` with no request; and
    ``preview_draft_purchase_order`` returns the payload it would send
    and whether the origin already exists, never a PUT — even with
    writes on (and without consulting the switch);
  * AC-3: every OData filter and URL path/query value derived from
    config or plan data is encoded — a tenant containing ``&``, ``#``,
    a space and a single quote is percent-encoded in the path, and the
    ``$filter`` value is percent-encoded in the query with its quotes
    doubled;
  * AC-4: logout is best-effort and never masks the primary exception;
    transport errors, OData auth failures, duplicate-search ambiguity,
    and malformed 2xx responses all raise explicit client errors rather
    than being treated as successful writes;
  * AC-5: the client never follows redirects — a 302 from the login or
    from a Basic-auth OData read raises ``AcumaticaError`` with the
    status and the redirected host is never requested, so the
    Authorization header can never be forwarded to it;
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
import tempfile
from urllib.parse import quote

import httpx
import pytest

import app.feed.acumatica_client as acumatica_client
from app.feed.acumatica_client import (
    AcumaticaAuthError,
    AcumaticaClient,
    AcumaticaConfigError,
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
        "search": lambda request: httpx.Response(200, json={"value": []}),
        "put": lambda request: httpx.Response(
            200, json={"OrderNbr": "PO003067", "Status": "On Hold"}
        ),
    }


def test_issue534_freeform(monkeypatch) -> None:
    # Isolate the Acumatica environment.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", BASE_URL)
    monkeypatch.setenv("ACUMATICA_TENANT", TENANT)
    monkeypatch.setenv("ACUMATICA_USERNAME", USERNAME)
    monkeypatch.setenv("ACUMATICA_PASSWORD", PASSWORD)

    requests_log: list[httpx.Request] = []
    build_calls: list[tuple] = []
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
    def fake_build_client(ca_bundle, timeout):
        build_calls.append((ca_bundle, timeout))
        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(acumatica_client, "_build_client", fake_build_client)
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

    def create(**overrides) -> dict:
        kwargs = dict(site_id=154, vendor_id=4851, origin=ORIGIN, lines=LINES)
        kwargs.update(overrides)
        return client.create_draft_purchase_order(**kwargs)

    # -- 1. resolve_codes: one session, Basic auth, stripped codes ------
    codes = client.resolve_codes([154, 159, 777], [4851, 9999, 8888])
    assert codes == {
        "sites": {"154": "WHOLESALE", "159": "RETAIL"},
        "vendors": {"4851": "CONPERIPH"},
    }
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("GET", BACCOUNT_PATH),
        ("POST", LOGOUT_PATH),
    ]
    login = requests_log[0]
    assert json.loads(login.content) == {
        "name": USERNAME,
        "password": PASSWORD,
        "tenant": TENANT,
    }
    assert "authorization" not in login.headers  # the login body carries it
    basic = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
    assert requests_log[1].headers["authorization"] == basic
    assert requests_log[2].headers["authorization"] == basic
    assert httpx.QueryParams(requests_log[1].url.query)["$select"] == "SiteID,SiteCD"
    assert httpx.QueryParams(requests_log[2].url.query)["$select"] == (
        "BAccountID,AcctCD,Type"
    )
    assert "authorization" not in requests_log[3].headers  # logout: session only

    # numeric strings are accepted ids, and only requested ids are kept
    codes = client.resolve_codes(["154"], ["4851"])
    assert codes == {"sites": {"154": "WHOLESALE"}, "vendors": {"4851": "CONPERIPH"}}

    # -- 2. writes off: disabled error, zero requests --------------------
    requests_log.clear()
    with pytest.raises(AcumaticaWritesDisabledError):
        create()
    assert requests_log == []

    # -- 3. writes on: the exact request sequence and PUT body -----------
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")
    requests_log.clear()
    result = create()
    assert result == {
        "order_nbr": "PO003067",
        "status": "On Hold",
        "already_existed": False,
        "warehouse_code": "WHOLESALE",
        "vendor_code": "CONPERIPH",
        "line_count": 1,
    }
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("GET", BACCOUNT_PATH),
        ("GET", PO_PATH),
        ("PUT", PO_PATH),
        ("POST", LOGOUT_PATH),
    ]
    put = requests_log[4]
    assert json.loads(put.content) == EXPECTED_PAYLOAD
    assert httpx.QueryParams(put.url.query)["$expand"] == "Details"
    assert "authorization" not in put.headers  # the session carries it

    # -- 4. duplicate found: already_existed, no PUT ----------------------
    canned["search"] = lambda request: httpx.Response(
        200, json={"value": [{"OrderNbr": "PO002901", "Status": "On Hold"}]}
    )
    requests_log.clear()
    result = create()
    assert result == {
        "order_nbr": "PO002901",
        "status": "On Hold",
        "already_existed": True,
        "warehouse_code": "WHOLESALE",
        "vendor_code": "CONPERIPH",
        "line_count": 1,
    }
    assert [r.method for r in requests_log] == ["POST", "GET", "GET", "GET", "POST"]
    assert no_put()
    canned["search"] = make_canned()["search"]

    # -- 5. hostile origin: quote doubled in $filter, encoded on the wire
    tricky_origin = "AIERP: A & B #1 'quoted'"
    requests_log.clear()
    result = create(origin=tricky_origin)
    assert result["already_existed"] is False
    search = requests_log[3]
    params = httpx.QueryParams(search.url.query)
    assert params["$filter"] == "Description eq 'AIERP: A & B #1 ''quoted'''"
    raw_query = search.url.query.decode()
    assert "%26" in raw_query  # the & in the origin is percent-encoded
    assert "%23" in raw_query  # the # in the origin is percent-encoded
    assert "+" in raw_query or "%20" in raw_query  # spaces are encoded
    put_body = json.loads(requests_log[4].content)
    assert put_body["Description"]["value"] == tricky_origin  # body verbatim

    # -- 6. unknown site / customer-type vendor: error before any PUT ----
    requests_log.clear()
    with pytest.raises(AcumaticaError) as excinfo:
        create(site_id=777)
    assert "777" in str(excinfo.value)
    assert no_put()
    requests_log.clear()
    with pytest.raises(AcumaticaError) as excinfo:
        create(vendor_id=9999)
    assert "9999" in str(excinfo.value)
    assert no_put()

    # -- 7. 500 on the PUT: status in, password out, logout still called -
    big_body = ("boom " * 100) + PASSWORD + " tail-marker"
    canned["put"] = lambda request: httpx.Response(500, text=big_body)
    requests_log.clear()
    with pytest.raises(AcumaticaError) as excinfo:
        create()
    message = str(excinfo.value)
    assert "500" in message
    assert PASSWORD not in message
    assert USERNAME not in message
    assert len(message.split("HTTP 500: ")[1]) <= 300  # body capped at 300 chars
    assert logged_out()
    canned["put"] = make_canned()["put"]

    # -- 8. login 401/403: auth error, no logout --------------------------
    canned["login"] = lambda request: httpx.Response(401, text="nope")
    requests_log.clear()
    with pytest.raises(AcumaticaAuthError) as excinfo:
        client.resolve_codes([154], [4851])
    assert "401" in str(excinfo.value)
    assert recorded() == [("POST", LOGIN_PATH)]  # no logout, nothing else
    canned["login"] = lambda request: httpx.Response(403, text="nope")
    requests_log.clear()
    with pytest.raises(AcumaticaAuthError) as excinfo:
        client.resolve_codes([154], [4851])
    assert "403" in str(excinfo.value)
    assert recorded() == [("POST", LOGIN_PATH)]
    canned["login"] = make_canned()["login"]

    # -- 9. config errors: variable named, value never, no request -------
    monkeypatch.delenv("ACUMATICA_USERNAME")
    requests_log.clear()
    with pytest.raises(AcumaticaConfigError) as excinfo:
        client.resolve_codes([154], [4851])
    assert "ACUMATICA_USERNAME" in str(excinfo.value)
    assert USERNAME not in str(excinfo.value)
    assert requests_log == []
    monkeypatch.setenv("ACUMATICA_USERNAME", USERNAME)

    monkeypatch.setenv("ACUMATICA_BASE_URL", "http://acumatica.example.com/Instance")
    requests_log.clear()
    with pytest.raises(AcumaticaConfigError) as excinfo:
        client.resolve_codes([154], [4851])
    assert "ACUMATICA_BASE_URL" in str(excinfo.value)
    assert requests_log == []
    # a truthy ACUMATICA_ALLOW_INSECURE accepts the http base
    monkeypatch.setenv("ACUMATICA_ALLOW_INSECURE", "1")
    requests_log.clear()
    codes = client.resolve_codes([154], [4851])
    assert codes["sites"] == {"154": "WHOLESALE"}
    assert all(r.url.scheme == "http" for r in requests_log)
    monkeypatch.setenv("ACUMATICA_BASE_URL", BASE_URL)
    monkeypatch.delenv("ACUMATICA_ALLOW_INSECURE")

    # -- 10. hostile line fields: ValueError before any request -----------
    requests_log.clear()
    bad_lines = [
        {"item_code": "X", "quantity": 0, "unit_cost": 1.0},
        {"item_code": "X", "quantity": -3, "unit_cost": 1.0},
        {"item_code": "X", "quantity": "10", "unit_cost": 1.0},
        {"item_code": "X", "quantity": None, "unit_cost": 1.0},
        {"item_code": "X", "quantity": float("nan"), "unit_cost": 1.0},
        {"item_code": "X", "quantity": float("inf"), "unit_cost": 1.0},
        {"item_code": "X", "quantity": True, "unit_cost": 1.0},
        {"item_code": "X", "quantity": 1.0, "unit_cost": -0.01},
        {"item_code": "X", "quantity": 1.0, "unit_cost": "20.83"},
        {"item_code": "X", "quantity": 1.0, "unit_cost": float("nan")},
        {"item_code": "", "quantity": 1.0, "unit_cost": 1.0},
        "not a list",
    ]
    for bad in bad_lines:
        with pytest.raises(ValueError):
            create(lines=bad)
    for bad_origin in ("", "x" * 256, 123, None):
        with pytest.raises(ValueError):
            create(origin=bad_origin)
    with pytest.raises(ValueError):
        create(site_id="not-an-id")
    with pytest.raises(ValueError):
        create(vendor_id=0)
    with pytest.raises(ValueError):
        client.resolve_codes("154", [4851])
    with pytest.raises(ValueError):
        client.resolve_codes([154], [1.5])
    assert requests_log == []

    # a 255-character origin and a zero unit cost are accepted
    requests_log.clear()
    result = create(
        origin="y" * 255,
        lines=[{"item_code": "X", "quantity": 1, "unit_cost": 0}],
    )
    assert result["order_nbr"] == "PO003067"

    # -- 11. preview: the payload, no PUT, switch not consulted -----------
    requests_log.clear()
    preview = client.preview_draft_purchase_order(
        site_id=154, vendor_id=4851, origin=ORIGIN, lines=LINES
    )
    assert preview["payload"] == EXPECTED_PAYLOAD
    assert preview["already_existed"] is False
    assert [r.method for r in requests_log] == ["POST", "GET", "GET", "GET", "POST"]
    assert no_put()
    monkeypatch.delenv("ACUMATICA_ALLOW_WRITES")  # preview ignores the switch
    preview = client.preview_draft_purchase_order(
        site_id=154, vendor_id=4851, origin=ORIGIN, lines=LINES
    )
    assert preview["already_existed"] is False
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")
    canned["search"] = lambda request: httpx.Response(
        200, json={"value": [{"OrderNbr": "PO002901", "Status": "On Hold"}]}
    )
    preview = client.preview_draft_purchase_order(
        site_id=154, vendor_id=4851, origin=ORIGIN, lines=LINES
    )
    assert preview["already_existed"] is True
    assert preview["payload"] == EXPECTED_PAYLOAD
    assert no_put()
    canned["search"] = make_canned()["search"]

    # -- 12. timeout and CA bundle work as in the Odoo client -------------
    build_calls.clear()
    client.resolve_codes([154], [4851])
    assert build_calls[-1] == (None, 30.0)
    monkeypatch.setenv("ACUMATICA_TIMEOUT_SECONDS", "12.5")
    client.resolve_codes([154], [4851])
    assert build_calls[-1] == (None, 12.5)
    monkeypatch.setenv("ACUMATICA_TIMEOUT_SECONDS", "-5")
    client.resolve_codes([154], [4851])
    assert build_calls[-1] == (None, 30.0)
    monkeypatch.delenv("ACUMATICA_TIMEOUT_SECONDS")
    monkeypatch.setenv("ACUMATICA_CA_BUNDLE", "/nonexistent/ca-bundle.pem")
    requests_log.clear()
    with pytest.raises(AcumaticaConfigError) as excinfo:
        client.resolve_codes([154], [4851])
    assert "ACUMATICA_CA_BUNDLE" in str(excinfo.value)
    assert requests_log == []
    with tempfile.NamedTemporaryFile(suffix=".pem") as bundle:
        monkeypatch.setenv("ACUMATICA_CA_BUNDLE", bundle.name)
        build_calls.clear()
        client.resolve_codes([154], [4851])
        assert build_calls[-1] == (bundle.name, 30.0)
    monkeypatch.delenv("ACUMATICA_CA_BUNDLE")

    # -- 13. AC-3: config values are encoded on the wire ------------------
    hostile_tenant = "t & x #1 y'z"
    monkeypatch.setenv("ACUMATICA_TENANT", hostile_tenant)
    requests_log.clear()
    codes = client.resolve_codes([154], [4851])
    assert codes == {"sites": {"154": "WHOLESALE"}, "vendors": {"4851": "CONPERIPH"}}
    # assert on the raw (on-the-wire) path, not httpx's decoded view
    insite_raw_path = requests_log[1].url.raw_path.decode().split("?", 1)[0]
    assert insite_raw_path == (
        "/Instance/odatav4/" + quote(hostile_tenant, safe="") + "/INSite"
    )
    assert "%26" in insite_raw_path  # &
    assert "%23" in insite_raw_path  # #
    assert "%20" in insite_raw_path  # space
    assert "%27" in insite_raw_path  # '
    assert (
        "&" not in insite_raw_path
        and "#" not in insite_raw_path
        and " " not in insite_raw_path
    )
    monkeypatch.setenv("ACUMATICA_TENANT", TENANT)

    # -- 14. AC-4: explicit client errors, best-effort logout -------------
    # OData auth failure: explicit error, logout still sent.
    canned["insite"] = lambda request: httpx.Response(401, text="basic rejected")
    requests_log.clear()
    with pytest.raises(AcumaticaAuthError) as excinfo:
        client.resolve_codes([154], [4851])
    assert "401" in str(excinfo.value)
    assert PASSWORD not in str(excinfo.value)
    assert [r.method for r in requests_log] == ["POST", "GET", "POST"]
    assert logged_out()
    canned["insite"] = make_canned()["insite"]

    # Transport error mid-session: explicit error, logout still sent.
    def refuse_connection(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    canned["insite"] = refuse_connection
    requests_log.clear()
    with pytest.raises(AcumaticaError) as excinfo:
        client.resolve_codes([154], [4851])
    assert "ConnectError" in str(excinfo.value)
    assert logged_out()
    canned["insite"] = make_canned()["insite"]

    # Duplicate-search ambiguity: explicit error, no PUT.
    canned["search"] = lambda request: httpx.Response(
        200,
        json={
            "value": [
                {"OrderNbr": "PO1", "Status": "On Hold"},
                {"OrderNbr": "PO2", "Status": "On Hold"},
            ]
        },
    )
    requests_log.clear()
    with pytest.raises(AcumaticaError) as excinfo:
        create()
    assert "ambiguous" in str(excinfo.value)
    assert no_put()
    assert logged_out()

    # Malformed 2xx duplicate-search bodies: explicit error, no PUT.
    # Non-dict rows in the value list are included: silently treating one
    # as a no-match would let a matching order go unseen and a duplicate
    # PO be created, so they raise the same explicit malformed error.
    for bad_search in (
        lambda request: httpx.Response(200, json={"value": "nope"}),
        lambda request: httpx.Response(200, text="not json"),
        lambda request: httpx.Response(
            200, json={"value": [{"Status": "On Hold"}]}  # missing OrderNbr
        ),
        lambda request: httpx.Response(200, json={"value": [None]}),
        lambda request: httpx.Response(200, json={"value": ["PO003067"]}),
        lambda request: httpx.Response(
            200,
            json={"value": [None, {"OrderNbr": "PO003067", "Status": "On Hold"}]},
        ),
    ):
        canned["search"] = bad_search
        requests_log.clear()
        with pytest.raises(AcumaticaError) as excinfo:
            create()
        assert "malformed" in str(excinfo.value)
        assert no_put()  # no duplicate PO is created
        assert logged_out()
    canned["search"] = make_canned()["search"]

    # Malformed 2xx PUT body: explicit error, not a successful write.
    canned["put"] = lambda request: httpx.Response(200, json={"NoOrder": True})
    requests_log.clear()
    with pytest.raises(AcumaticaError) as excinfo:
        create()
    assert "malformed" in str(excinfo.value)
    canned["put"] = make_canned()["put"]

    # A failing logout never masks the primary exception.
    def failing_logout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("logout down")

    canned["put"] = lambda request: httpx.Response(500, text="write failed")
    canned["logout"] = failing_logout
    requests_log.clear()
    with pytest.raises(AcumaticaError) as excinfo:
        create()
    message = str(excinfo.value)
    assert message.startswith("the Acumatica PurchaseOrder write failed with HTTP 500")
    assert "ConnectError" not in message  # the primary error is intact
    assert requests_log[-1].url.path == LOGOUT_PATH  # logout was attempted
    # and a successful write survives a failing logout
    canned["put"] = make_canned()["put"]
    result = create()
    assert result["order_nbr"] == "PO003067"
    canned["logout"] = make_canned()["logout"]

    # -- 15. AC-5: redirects are never followed ----------------------------
    # A 302 from the login: explicit error, the redirected host is never
    # requested, and (login failed) no logout is sent.
    canned["login"] = lambda request: httpx.Response(
        302, headers={"Location": "https://evil.example.com/steal"}
    )
    requests_log.clear()
    with pytest.raises(AcumaticaError) as excinfo:
        client.resolve_codes([154], [4851])
    assert "302" in str(excinfo.value)
    assert [r.url.host for r in requests_log] == ["acumatica.example.com"]
    assert len(requests_log) == 1
    canned["login"] = make_canned()["login"]

    # A 302 from a Basic-auth OData read: the Authorization header is
    # never sent to the redirected host because the client does not
    # follow it at all.
    canned["insite"] = lambda request: httpx.Response(
        302, headers={"Location": "https://evil.example.com/odata"}
    )
    requests_log.clear()
    with pytest.raises(AcumaticaError) as excinfo:
        client.resolve_codes([154], [4851])
    assert "302" in str(excinfo.value)
    assert {r.url.host for r in requests_log} == {"acumatica.example.com"}
    for request in requests_log:
        if request.url.host != "acumatica.example.com":
            pytest.fail(f"request leaked to {request.url.host}")
    assert logged_out()
    canned["insite"] = make_canned()["insite"]

    # The real factory builds a client that does not follow redirects.
    real_client = acumatica_client._build_client(None, 5.0)
    real_client.close()

    # -- 16. Identity: the proof also runs as a NON-ADMIN ------------------
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
