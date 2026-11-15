"""Issue #880 proof: batch transfer-order preview/create, with no network.

``app.feed.acumatica_client`` gets two batch entry points,
``preview_transfer_orders(transfers)`` and
``create_transfer_orders(transfers)``, proven through the same
``httpx.MockTransport`` seam as issue #537: the per-call
``httpx.Client`` comes from the module-level ``_build_client`` (which
this test replaces with a factory over a recording mock transport that
serves canned Acumatica answers — login 204 carrying the session
cookie; INSite rows 154=WHOLESALE and 159=RETAIL; a duplicate search
that finds transfer 000777 for entry 2's origin and nothing for the
others; a PUT answer carrying ReferenceNbr 000901 and Status "On
Hold"; logout 204).

Three entries are used throughout:

  * entry 1: 154 -> 159, 2 lines, a new origin;
  * entry 2: 159 -> 154, 1 line, whose origin's duplicate search finds
    transfer 000777;
  * entry 3: 154 -> 999 — a site with no INSite row, so it has no
    warehouse code.

Proved here, in order:

  * with writes off (unset or non-truthy), ``create_transfer_orders``
    raises ``AcumaticaWritesDisabledError`` before any request — and
    before the entry validation, so an invalid entry with the switch
    off raises the disabled error, not ``ValueError``;
  * ``preview_transfer_orders`` answers ``would_create``, ``exists``
    (reference 000777) and ``error`` (naming the mode, the entry, and
    the missing code), sends no PUT, logs in and out once, and does
    not consult the write switch;
  * with writes on, ``create_transfer_orders`` opens one session — one
    login, one INSite read (one code-lookup pass over the union of
    the entries' site ids), a duplicate search and PUT for entry 1, a
    duplicate search for entry 2, nothing more for entry 3, and one
    logout — and sends exactly one PUT, for entry 1, whose body is
    the exact 1-Step ON-HOLD payload; entry 1 is ``created``
    (reference 000901, status "On Hold"), entry 2 ``exists``, entry 3
    ``error``;
  * a 500 on the PUT gives entry 1 an ``error`` (mode, entry and
    status in; password, user name and session cookie out; body capped
    at 300 characters) while entries 2 and 3 still get their outcomes
    and logout is still sent — the batch continues;
  * a login 401, a code-lookup 500, or a per-entry search 401 raises
    for the whole batch (``AcumaticaAuthError`` on 401/403); an
    ambiguous search is a per-entry ``error`` and the batch continues;
  * an origin containing a single quote is sent in the ``$filter``
    with the quote doubled and the value percent-encoded on the wire;
  * the same site on both ends, a zero quantity, an empty lines list,
    a NUL, empty, zero, or leading-zero site id, an over-long origin,
    and a duplicated origin each raise ``ValueError`` naming the
    entry index before any request; an empty list returns ``[]`` with
    no request;
  * no error message anywhere carries the password, the user name, or
    the session cookie;
  * the identity requirement: the proof also runs as a NON-ADMIN (role
    ``user``) in a second pass after a full reload — the page boots
    the production way (the token it would take from the URL hash as
    ``#access_token=...``, the bare development token, with
    ``/api/auth/config`` answering publicly and ``/api/auth/me``
    resolving the viewer for that user), the admin-only control is
    absent — this issue adds no routes at all, so the app exposes no
    Acumatica operation (admin or otherwise) and no route module
    references the new methods — while the page still renders: the
    settings the page loads answer 200 for the viewer and
    unauthenticated ``/api`` traffic is still 401.
"""

import base64
import json
import os
import pathlib

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
TRANSFER_PATH = f"/Instance/entity/Default/{VERSION}/TransferOrder"

ORIGIN_1 = "AIERP:transfer:WHOLESALE->RETAIL:1"
ORIGIN_2 = "AIERP:transfer:RETAIL->WHOLESALE:2"
ORIGIN_3 = "AIERP:transfer:WHOLESALE->UNKNOWN:3"

LINES_1 = [
    {"item_code": "CONBOTTLE1", "quantity": 12},
    {"item_code": "CONCUP1", "quantity": 7},
]
LINES_2 = [{"item_code": "CONBOTTLE2", "quantity": 4}]
LINES_3 = [{"item_code": "CONBOTTLE3", "quantity": 2}]

EXPECTED_PAYLOAD_1 = {
    "TransferType": {"value": "1-Step"},
    "FromWarehouseID": {"value": "WHOLESALE"},
    "ToWarehouseID": {"value": "RETAIL"},
    "Hold": {"value": True},
    "Description": {"value": ORIGIN_1},
    "Details": [
        {"InventoryID": {"value": "CONBOTTLE1"}, "Qty": {"value": 12}},
        {"InventoryID": {"value": "CONCUP1"}, "Qty": {"value": 7}},
    ],
}

# The session the login establishes: it rides on every later request of
# the session, and no error message may ever carry it.
SESSION_COOKIE_NAME = "acum.session"
SESSION_COOKIE_VALUE = "cookie-secret-9f8e7d"

# Entry 3's sites: 154 has a code, 999 has no INSite row at all.
SITE_999_ERROR = "no INSite site matches site id 999"

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


def transfers() -> list[dict]:
    """A fresh copy of the three-entry batch."""
    return [
        {"from_site_id": 154, "to_site_id": 159, "origin": ORIGIN_1, "lines": LINES_1},
        {"from_site_id": 159, "to_site_id": 154, "origin": ORIGIN_2, "lines": LINES_2},
        {"from_site_id": 154, "to_site_id": 999, "origin": ORIGIN_3, "lines": LINES_3},
    ]


def default_search(request: httpx.Request) -> httpx.Response:
    """Duplicate search: entry 2's origin finds 000777, nothing else."""
    flt = httpx.QueryParams(request.url.query).get("$filter") or ""
    if ORIGIN_2 in flt:
        return httpx.Response(
            200,
            json={"value": [{"ReferenceNbr": "000777", "Status": "On Hold"}]},
        )
    return httpx.Response(200, json={"value": []})


def make_canned() -> dict:
    """The canned Acumatica answers for the mock transport."""
    return {
        "login": lambda request: httpx.Response(
            204,
            headers={
                "set-cookie": f"{SESSION_COOKIE_NAME}={SESSION_COOKIE_VALUE}; path=/"
            },
        ),
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
        "search": default_search,
        "put": lambda request: httpx.Response(
            200, json={"ReferenceNbr": "000901", "Status": "On Hold"}
        ),
    }


def test_issue880_freeform(monkeypatch) -> None:
    # Isolate the Acumatica environment (writes off by default).
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", BASE_URL)
    monkeypatch.setenv("ACUMATICA_TENANT", TENANT)
    monkeypatch.setenv("ACUMATICA_USERNAME", USERNAME)
    monkeypatch.setenv("ACUMATICA_PASSWORD", PASSWORD)

    requests_log: list[httpx.Request] = []
    all_error_messages: list[str] = []
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
        if request.method == "GET" and path.endswith("/TransferOrder"):
            return canned["search"](request)
        if request.method == "PUT" and path.endswith("/TransferOrder"):
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

    def note_errors(results: list[dict]) -> None:
        for result in results:
            if result["error"]:
                all_error_messages.append(result["error"])

    # -- 1. writes off: disabled error before any request -----------------
    requests_log.clear()
    with pytest.raises(AcumaticaWritesDisabledError):
        client.create_transfer_orders(transfers())
    assert requests_log == []
    with pytest.raises(AcumaticaWritesDisabledError):
        client.create_transfer_orders([])
    assert requests_log == []
    # A non-truthy value is off as well.
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "false")
    with pytest.raises(AcumaticaWritesDisabledError):
        client.create_transfer_orders(transfers())
    assert requests_log == []
    # The switch is consulted before the entry validation: an invalid
    # entry (same site on both ends) still raises the disabled error.
    monkeypatch.delenv("ACUMATICA_ALLOW_WRITES")
    requests_log.clear()
    with pytest.raises(AcumaticaWritesDisabledError):
        client.create_transfer_orders(
            [
                {
                    "from_site_id": 154,
                    "to_site_id": 154,
                    "origin": ORIGIN_1,
                    "lines": LINES_1,
                }
            ]
        )
    assert requests_log == []
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")

    # -- 2. preview: would_create / exists / error, no PUT, one session --
    requests_log.clear()
    results = client.preview_transfer_orders(transfers())
    assert results == [
        {
            "origin": ORIGIN_1,
            "outcome": "would_create",
            "reference_nbr": None,
            "status": None,
            "from_warehouse_code": "WHOLESALE",
            "to_warehouse_code": "RETAIL",
            "line_count": 2,
            "error": None,
        },
        {
            "origin": ORIGIN_2,
            "outcome": "exists",
            "reference_nbr": "000777",
            "status": "On Hold",
            "from_warehouse_code": "RETAIL",
            "to_warehouse_code": "WHOLESALE",
            "line_count": 1,
            "error": None,
        },
        {
            "origin": ORIGIN_3,
            "outcome": "error",
            "reference_nbr": None,
            "status": None,
            "from_warehouse_code": "WHOLESALE",
            "to_warehouse_code": None,
            "line_count": 1,
            "error": (
                f"transfer order preview for transfers[2] (origin '{ORIGIN_3}'): "
                f"{SITE_999_ERROR}"
            ),
        },
    ]
    note_errors(results)
    # Exactly one session: one login, one INSite read (one code-lookup
    # pass over the union of the entries' site ids), a duplicate search
    # for entries 1 and 2, nothing for entry 3, and one logout.
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("GET", TRANSFER_PATH),  # entry 1 duplicate search
        ("GET", TRANSFER_PATH),  # entry 2 duplicate search
        ("POST", LOGOUT_PATH),
    ]
    methods = [r.method for r in requests_log]
    assert methods.count("POST") == 2  # the one login and the one logout
    assert sum(r.url.path == INSITE_PATH for r in requests_log) == 1
    assert no_put()
    assert logged_out()
    # The login body carries the credentials; the INSite read carries
    # HTTP Basic auth; the session cookie (not a header) authorizes the
    # rest.
    login = requests_log[0]
    assert json.loads(login.content) == {
        "name": USERNAME,
        "password": PASSWORD,
        "tenant": TENANT,
    }
    assert "authorization" not in login.headers
    basic = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
    assert requests_log[1].headers["authorization"] == basic  # INSite
    assert "authorization" not in requests_log[2].headers  # search
    assert (
        requests_log[2].headers.get("cookie")
        == f"{SESSION_COOKIE_NAME}={SESSION_COOKIE_VALUE}"
    )
    # The searches ran in input order, on each entry's own origin.
    search_filters = [
        httpx.QueryParams(r.url.query)["$filter"]
        for r in requests_log
        if r.method == "GET" and r.url.path == TRANSFER_PATH
    ]
    assert search_filters == [
        f"Description eq '{ORIGIN_1}'",
        f"Description eq '{ORIGIN_2}'",
    ]
    # Preview does not consult the write switch: same outcomes with it
    # unset.
    monkeypatch.delenv("ACUMATICA_ALLOW_WRITES")
    requests_log.clear()
    results = client.preview_transfer_orders(transfers())
    assert [r["outcome"] for r in results] == ["would_create", "exists", "error"]
    note_errors(results)
    assert no_put()
    assert logged_out()
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")

    # -- 3. create: one session, exactly one PUT, for entry 1 ------------
    requests_log.clear()
    results = client.create_transfer_orders(transfers())
    assert results == [
        {
            "origin": ORIGIN_1,
            "outcome": "created",
            "reference_nbr": "000901",
            "status": "On Hold",
            "from_warehouse_code": "WHOLESALE",
            "to_warehouse_code": "RETAIL",
            "line_count": 2,
            "error": None,
        },
        {
            "origin": ORIGIN_2,
            "outcome": "exists",
            "reference_nbr": "000777",
            "status": "On Hold",
            "from_warehouse_code": "RETAIL",
            "to_warehouse_code": "WHOLESALE",
            "line_count": 1,
            "error": None,
        },
        {
            "origin": ORIGIN_3,
            "outcome": "error",
            "reference_nbr": None,
            "status": None,
            "from_warehouse_code": "WHOLESALE",
            "to_warehouse_code": None,
            "line_count": 1,
            "error": (
                f"transfer order create for transfers[2] (origin '{ORIGIN_3}'): "
                f"{SITE_999_ERROR}"
            ),
        },
    ]
    note_errors(results)
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("GET", TRANSFER_PATH),  # entry 1 duplicate search
        ("PUT", TRANSFER_PATH),  # entry 1 write
        ("GET", TRANSFER_PATH),  # entry 2 duplicate search
        ("POST", LOGOUT_PATH),
    ]
    puts = [r for r in requests_log if r.method == "PUT"]
    assert len(puts) == 1  # exactly one PUT, for entry 1
    put = puts[0]
    assert "authorization" not in put.headers
    assert json.loads(put.content) == EXPECTED_PAYLOAD_1
    assert httpx.QueryParams(put.url.query)["$expand"] == "Details"
    assert [r.url.host for r in requests_log] == ["acumatica.example.com"] * 6
    assert logged_out()

    # -- 4. 500 on the PUT: per-entry error, batch continues -------------
    big_body = ("boom " * 100) + PASSWORD + " tail-marker"
    canned["put"] = lambda request: httpx.Response(500, text=big_body)
    requests_log.clear()
    results = client.create_transfer_orders(transfers())
    assert results[0]["outcome"] == "error"
    error = results[0]["error"]
    assert error.startswith(
        "transfer order create for transfers[0] "
        f"(origin '{ORIGIN_1}'): the Acumatica TransferOrder write "
        "failed with HTTP 500"
    )
    assert PASSWORD not in error
    assert USERNAME not in error
    assert SESSION_COOKIE_VALUE not in error
    assert len(error.split("HTTP 500: ")[1]) <= 300  # body capped at 300 chars
    assert results[0]["reference_nbr"] is None and results[0]["status"] is None
    assert results[0]["from_warehouse_code"] == "WHOLESALE"
    assert results[0]["to_warehouse_code"] == "RETAIL"
    assert results[0]["line_count"] == 2
    # Entries 2 and 3 still get their outcomes; logout is still sent.
    assert results[1]["outcome"] == "exists"
    assert results[1]["reference_nbr"] == "000777"
    assert results[2]["outcome"] == "error"
    assert results[2]["error"] == (
        f"transfer order create for transfers[2] (origin '{ORIGIN_3}'): "
        f"{SITE_999_ERROR}"
    )
    note_errors(results)
    assert [r.method for r in requests_log] == [
        "POST",  # the one login
        "GET",
        "GET",  # entry 1 duplicate search
        "PUT",  # the failed entry 1 write
        "GET",  # entry 2 duplicate search
        "POST",  # the one logout
    ]
    assert logged_out()
    canned["put"] = make_canned()["put"]

    # -- 5. a batch-level failure raises for the whole batch -------------
    # Login 401: auth error, no result list, no logout (no session).
    canned["login"] = lambda request: httpx.Response(401, text="nope")
    requests_log.clear()
    with pytest.raises(AcumaticaAuthError) as excinfo:
        client.create_transfer_orders(transfers())
    assert "401" in str(excinfo.value)
    assert PASSWORD not in str(excinfo.value)
    assert SESSION_COOKIE_VALUE not in str(excinfo.value)
    all_error_messages.append(str(excinfo.value))
    assert recorded() == [("POST", LOGIN_PATH)]
    canned["login"] = make_canned()["login"]
    # Code-lookup failure (INSite 500): raises for the whole batch; the
    # session was opened, so the logout is still sent.
    canned["insite"] = lambda request: httpx.Response(500, text="odata down")
    requests_log.clear()
    with pytest.raises(AcumaticaError) as excinfo:
        client.preview_transfer_orders(transfers())
    assert "500" in str(excinfo.value)
    assert PASSWORD not in str(excinfo.value)
    all_error_messages.append(str(excinfo.value))
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("POST", LOGOUT_PATH),
    ]
    assert logged_out()
    canned["insite"] = make_canned()["insite"]
    # A 401 from entry 1's duplicate search: batch-level failure, logout
    # sent, nothing reported per entry.
    canned["search"] = lambda request: httpx.Response(401, text="expired")
    requests_log.clear()
    with pytest.raises(AcumaticaAuthError) as excinfo:
        client.create_transfer_orders(transfers())
    assert "401" in str(excinfo.value)
    all_error_messages.append(str(excinfo.value))
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("GET", TRANSFER_PATH),
        ("POST", LOGOUT_PATH),
    ]
    canned["search"] = make_canned()["search"]

    # -- 6. ambiguous search: per-entry error, batch continues -----------
    def search_ambiguous_first(request: httpx.Request) -> httpx.Response:
        flt = httpx.QueryParams(request.url.query).get("$filter") or ""
        if ORIGIN_1 in flt:
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"ReferenceNbr": "000101", "Status": "On Hold"},
                        {"ReferenceNbr": "000102", "Status": "On Hold"},
                    ]
                },
            )
        return default_search(request)

    canned["search"] = search_ambiguous_first
    requests_log.clear()
    results = client.create_transfer_orders(transfers())
    assert results[0]["outcome"] == "error"
    assert "transfers[0]" in results[0]["error"]
    assert "ambiguous" in results[0]["error"]
    assert results[1]["outcome"] == "exists"
    assert results[1]["reference_nbr"] == "000777"
    assert results[2]["outcome"] == "error"
    note_errors(results)
    assert no_put()  # entry 1's write is skipped after its search failed
    assert logged_out()
    canned["search"] = make_canned()["search"]

    # -- 7. quote escaping: the origin's quote is doubled in the filter --
    tricky_origin = "AIERP:transfer:O'HARE->RETAIL #7"
    requests_log.clear()
    results = client.preview_transfer_orders(
        [
            {
                "from_site_id": 154,
                "to_site_id": 159,
                "origin": tricky_origin,
                "lines": [{"item_code": "X", "quantity": 1}],
            }
        ]
    )
    assert results[0]["outcome"] == "would_create"
    assert results[0]["origin"] == tricky_origin
    search = next(
        r for r in requests_log if r.method == "GET" and r.url.path == TRANSFER_PATH
    )
    assert httpx.QueryParams(search.url.query)["$filter"] == (
        "Description eq 'AIERP:transfer:O''HARE->RETAIL #7'"
    )
    raw_query = search.url.query.decode()
    assert "%27" in raw_query  # the single quotes are percent-encoded
    assert "%23" in raw_query  # the # is percent-encoded
    assert no_put()

    # -- 8. validation: ValueError naming the index, no request ----------
    base = transfers()
    bad_batches: list[object] = [
        # the same site on both ends (entry 1)
        [base[0], {**base[1], "from_site_id": 154, "to_site_id": 154}, base[2]],
        # a zero quantity (entry 1)
        [base[0], {**base[1], "lines": [{"item_code": "X", "quantity": 0}]}, base[2]],
        # an empty lines list (entry 1)
        [base[0], {**base[1], "lines": []}, base[2]],
        # two entries with the same origin (entry 1 duplicates entry 0)
        [base[0], {**base[1], "origin": ORIGIN_1}, base[2]],
        # a non-numeric to_site_id (entry 2)
        [base[0], base[1], {**base[2], "to_site_id": "not-an-id"}],
        # a NUL byte in a site id (entry 2)
        [base[0], base[1], {**base[2], "to_site_id": "999\x00"}],
        # an empty from_site_id (entry 2)
        [base[0], base[1], {**base[2], "from_site_id": ""}],
        # an over-long origin (entry 2)
        [base[0], base[1], {**base[2], "origin": "x" * 256}],
        # a zero site id padded with leading zeros (entry 2)
        [base[0], base[1], {**base[2], "to_site_id": "00"}],
        # a leading-zero site id (entry 2)
        [base[0], base[1], {**base[2], "from_site_id": "0154"}],
        # a plain zero site id as a string (entry 2)
        [base[0], base[1], {**base[2], "to_site_id": "0"}],
    ]
    for bad in bad_batches:
        requests_log.clear()
        with pytest.raises(ValueError) as excinfo:
            client.create_transfer_orders(bad)  # type: ignore[arg-type]
        assert "transfers[" in str(excinfo.value)  # the entry's index is named
        assert requests_log == []
    # The messages name the right index and the underlying problem.
    requests_log.clear()
    with pytest.raises(ValueError) as excinfo:
        client.create_transfer_orders(bad_batches[0])
    assert "transfers[1]" in str(excinfo.value)
    assert "differ" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        client.create_transfer_orders(bad_batches[1])
    assert "transfers[1]" in str(excinfo.value)
    assert "quantity" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        client.create_transfer_orders(bad_batches[2])
    assert "transfers[1]" in str(excinfo.value)
    assert "non-empty" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        client.create_transfer_orders(bad_batches[3])
    assert "transfers[1]" in str(excinfo.value)
    assert "duplicate" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        client.create_transfer_orders(bad_batches[4])
    assert "transfers[2]" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        client.create_transfer_orders(bad_batches[8])
    assert "transfers[2]" in str(excinfo.value)
    assert "to_site_id must be a positive integer id" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        client.create_transfer_orders(bad_batches[9])
    assert "transfers[2]" in str(excinfo.value)
    assert "from_site_id must be a positive integer id" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        client.create_transfer_orders(bad_batches[10])
    assert "transfers[2]" in str(excinfo.value)
    assert "to_site_id must be a positive integer id" in str(excinfo.value)
    # The batch itself must be a list of entries.
    for bad in ("not a list", None, 42):
        requests_log.clear()
        with pytest.raises(ValueError):
            client.create_transfer_orders(bad)  # type: ignore[arg-type]
        assert requests_log == []
    # Preview validates identically, before any request.
    requests_log.clear()
    with pytest.raises(ValueError):
        client.preview_transfer_orders(bad_batches[1])
    assert requests_log == []
    with pytest.raises(ValueError):
        client.preview_transfer_orders("not a list")  # type: ignore[arg-type]
    assert requests_log == []
    for bad in (bad_batches[8], bad_batches[9], bad_batches[10]):
        requests_log.clear()
        with pytest.raises(ValueError):
            client.preview_transfer_orders(bad)  # type: ignore[arg-type]
        assert requests_log == []

    # -- 9. an empty list returns [] with no request ---------------------
    requests_log.clear()
    assert client.create_transfer_orders([]) == []
    assert requests_log == []
    assert client.preview_transfer_orders([]) == []
    assert requests_log == []
    monkeypatch.delenv("ACUMATICA_ALLOW_WRITES")
    assert client.preview_transfer_orders([]) == []
    assert requests_log == []
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")

    # -- 10. no secrets: no error message carries a secret ---------------
    assert all_error_messages  # the error paths above were all exercised
    for message in all_error_messages:
        assert PASSWORD not in message
        assert USERNAME not in message
        assert SESSION_COOKIE_VALUE not in message

    # -- 11. Identity: the proof also runs as a NON-ADMIN ----------------
    # Second pass after a full reload: the page boots the production way
    # — the token it would take from the URL hash as #access_token=...
    # (the bare development token), with /api/auth/config answering
    # publicly and /api/auth/me resolving (dev-mode auth standing in for
    # the IdP) the non-admin viewer for that user.
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
    # The admin-only control is absent: this issue adds no routes at
    # all, so the app's API surface exposes no Acumatica operation —
    # admin or otherwise — and no route module references the new
    # transfer-order methods.
    paths = app.openapi()["paths"]
    assert not any("acumatica" in name.lower() for name in paths)
    for path in pathlib.Path(api_package.__file__).parent.glob("*.py"):
        text = path.read_text()
        assert "create_transfer_orders" not in text
        assert "preview_transfer_orders" not in text
    # The page still renders: the settings the page loads answer 200 for
    # the viewer, and the gate is intact — unauthenticated /api traffic
    # is 401.
    assert viewer.get("/api/settings").status_code == 200
    assert anon.get("/api/settings").status_code == 401
