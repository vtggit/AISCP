"""Issue #391 proof: the fail-closed Odoo JSON-RPC client.

With the Odoo server fully mocked (httpx.MockTransport answers every
/jsonrpc POST), proves:

  * authenticate() posts common.authenticate with [db, login, api_key, {}]
    to the configured origin's /jsonrpc and returns the uid the mock gives,
  * an authenticate() that returns false, a non-integer result, a
    {"error": ...} body, or a transport failure raises OdooAuthError with a
    message that names the failure class and contains no API key, no login,
    and no response body,
  * search_read() posts object.execute_kw carrying the model, domain,
    fields, limit and offset, returns the mock's records, and reuses the
    cached uid on a second call without re-authenticating,
  * a base URL with a path, and an http:// base URL without
    ODOO_ALLOW_INSECURE, raise OdooConfigError before any request, while
    the same http:// URL with ODOO_ALLOW_INSECURE=1 is accepted,
  * a {"error": {...}} body and a transport failure during search_read
    raise OdooError naming the model, with neither the API key nor the
    body in the message,
  * read_products() maps two products (uom_id [1, "Units"] and uom_id
    false) to the InventoryItemCreate shape with source_system "odoo" and
    skips a product whose default_code is false or empty, and an
    authenticate-phase failure surfaces as OdooAuthError.
"""

import json

import httpx
import pytest

from app.feed.odoo_client import (
    OdooAuthError,
    OdooClient,
    OdooConfigError,
    OdooError,
)
from app.models.inventory_items import InventoryItemCreate

BASE_URL = "https://odoo.acme.example"
INSECURE_BASE_URL = "http://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"
BODY_MARKER = "odoo-error-body-marker"


def _set_env(
    monkeypatch,
    base_url=BASE_URL,
    db=DB,
    login=LOGIN,
    api_key=API_KEY,
    allow_insecure=None,
):
    monkeypatch.setenv("ODOO_BASE_URL", base_url)
    for name, value in (
        ("ODOO_DB", db),
        ("ODOO_LOGIN", login),
        ("ODOO_API_KEY", api_key),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    if allow_insecure is None:
        monkeypatch.delenv("ODOO_ALLOW_INSECURE", raising=False)
    else:
        monkeypatch.setenv("ODOO_ALLOW_INSECURE", allow_insecure)


def _result_response(result):
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


def _error_response():
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": 200,
                "message": BODY_MARKER,
                "data": {"message": BODY_MARKER, "debug": "traceback"},
            },
        },
    )


def _transport_failure(_body):
    raise httpx.ConnectError("simulated network reset")


class _RpcServer:
    """MockTransport handler recording each /jsonrpc POST it answers."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(json.loads(request.content))
        entry = self.script.pop(0)
        if callable(entry):
            return entry(self.bodies[-1])
        return entry

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _capture(fn, expected_type):
    """Call fn() and return the expected_type exception it must raise."""
    try:
        fn()
    except expected_type as exc:
        return exc
    except Exception as exc:
        pytest.fail(
            f"expected {expected_type.__name__}, " f"got {type(exc).__name__}: {exc}"
        )
    pytest.fail(f"expected {expected_type.__name__}, but no exception was raised")


def test_issue391_freeform(monkeypatch):
    # 1. authenticate() posts common.authenticate with [db, login, api_key,
    #    {}] to the origin's /jsonrpc and returns the uid the mock gives.
    _set_env(monkeypatch)
    server = _RpcServer([_result_response(7)])
    with OdooClient(client=server.client()) as client:
        assert client.authenticate() == 7
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request.method == "POST"
    assert request.url.scheme == "https"
    assert request.url.host == "odoo.acme.example"
    assert request.url.path == "/jsonrpc"
    body = server.bodies[0]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "call"
    assert body["params"]["service"] == "common"
    assert body["params"]["method"] == "authenticate"
    assert body["params"]["args"] == [DB, LOGIN, API_KEY, {}]

    # 2. authenticate() returning false raises OdooAuthError without the
    #    API key or the login in the message.
    _set_env(monkeypatch)
    server = _RpcServer([_result_response(False)])
    with OdooClient(client=server.client()) as client:
        exc = _capture(client.authenticate, OdooAuthError)
    message = str(exc)
    assert API_KEY not in message
    assert LOGIN not in message

    # 3. authenticate() returning a non-integer result raises OdooAuthError.
    _set_env(monkeypatch)
    server = _RpcServer([_result_response("uid?")])
    with OdooClient(client=server.client()) as client:
        exc = _capture(client.authenticate, OdooAuthError)
    assert API_KEY not in str(exc)

    # 4. A {"error": ...} body during authenticate() raises OdooAuthError
    #    with the body kept out of the message.
    _set_env(monkeypatch)
    server = _RpcServer([_error_response()])
    with OdooClient(client=server.client()) as client:
        exc = _capture(client.authenticate, OdooAuthError)
    message = str(exc)
    assert API_KEY not in message
    assert LOGIN not in message
    assert BODY_MARKER not in message

    # 5. A transport failure during authenticate() raises OdooAuthError
    #    naming the failure class, with no credentials and no body.
    _set_env(monkeypatch)
    server = _RpcServer([_transport_failure])
    with OdooClient(client=server.client()) as client:
        exc = _capture(client.authenticate, OdooAuthError)
    message = str(exc)
    assert "ConnectError" in message
    assert API_KEY not in message
    assert LOGIN not in message
    assert "simulated network reset" not in message

    # 6. search_read() posts object.execute_kw carrying db, the cached uid,
    #    the API key, the model, the domain, and only the given fields,
    #    limit and offset; it returns the mock's records.
    _set_env(monkeypatch)
    records = [{"id": 1}, {"id": 2, "name": "Widget"}]
    server = _RpcServer([_result_response(9), _result_response(records)])
    with OdooClient(client=server.client()) as client:
        got = client.search_read(
            "product.product",
            [("default_code", "=", "W-1")],
            ["name", "active"],
            limit=25,
            offset=40,
        )
    assert got == records
    assert len(server.requests) == 2
    auth_body = server.bodies[0]
    assert auth_body["params"]["args"] == [DB, LOGIN, API_KEY, {}]
    exec_body = server.bodies[1]
    assert exec_body["params"]["service"] == "object"
    assert exec_body["params"]["method"] == "execute_kw"
    (
        db_arg,
        uid_arg,
        key_arg,
        model_arg,
        method_arg,
        args_arg,
        kwargs_arg,
    ) = exec_body["params"]["args"]
    assert db_arg == DB
    assert uid_arg == 9
    assert key_arg == API_KEY
    assert model_arg == "product.product"
    assert method_arg == "search_read"
    assert args_arg == [[["default_code", "=", "W-1"]]]
    assert kwargs_arg == {"fields": ["name", "active"], "limit": 25, "offset": 40}

    # 7. A second search_read() call reuses the cached uid without
    #    re-authenticating (no further common.authenticate request).
    _set_env(monkeypatch)
    server = _RpcServer(
        [
            _result_response(9),
            _result_response([{"id": 1}]),
            _result_response([{"id": 3}]),
        ]
    )
    with OdooClient(client=server.client()) as client:
        client.search_read("product.product", [], ["id"])
        got2 = client.search_read("product.product", [], ["id"])
    assert got2 == [{"id": 3}]
    assert len(server.requests) == 3
    assert all(body["params"]["method"] != "authenticate" for body in server.bodies[1:])
    exec_body2 = server.bodies[2]
    db2, uid2, key2, model2, method2, args2, kwargs2 = exec_body2["params"]["args"]
    assert (db2, uid2, key2) == (DB, 9, API_KEY)
    assert (model2, method2) == ("product.product", "search_read")
    assert args2 == [[]]
    assert kwargs2 == {"fields": ["id"], "offset": 0}

    # 8. A base URL with a path raises OdooConfigError before any request.
    _set_env(monkeypatch, base_url=f"{BASE_URL}/v16")
    server = _RpcServer([_result_response(1)])
    with OdooClient(client=server.client()) as client:
        exc = _capture(client.authenticate, OdooConfigError)
    assert "ODOO_BASE_URL" in str(exc)
    assert server.requests == []

    # 9. An http:// base URL without ODOO_ALLOW_INSECURE raises
    #    OdooConfigError before any request.
    _set_env(monkeypatch, base_url=INSECURE_BASE_URL)
    server = _RpcServer([_result_response(1)])
    with OdooClient(client=server.client()) as client:
        _capture(client.authenticate, OdooConfigError)
    assert server.requests == []

    # 10. A missing required value raises OdooConfigError before any
    #     request, naming the variable and never its value.
    _set_env(monkeypatch, db=None)
    server = _RpcServer([_result_response(1)])
    with OdooClient(client=server.client()) as client:
        exc = _capture(client.authenticate, OdooConfigError)
    assert "ODOO_DB" in str(exc)
    assert server.requests == []

    # 11. A model that is not a dotted lowercase identifier, or a fields
    #     entry that is not a non-empty string, raises before any request.
    _set_env(monkeypatch)
    server = _RpcServer([_result_response(1)])
    with OdooClient(client=server.client()) as client:
        _capture(lambda: client.search_read("Product.Product", [], ["id"]), ValueError)
        _capture(
            lambda: client.search_read("product.product", [], ["id", ""]), ValueError
        )
    assert server.requests == []

    # 12. The same http:// URL with ODOO_ALLOW_INSECURE=1 is accepted.
    _set_env(monkeypatch, base_url=INSECURE_BASE_URL, allow_insecure="1")
    server = _RpcServer([_result_response(3)])
    with OdooClient(client=server.client()) as client:
        assert client.authenticate() == 3
    assert len(server.requests) == 1
    assert server.requests[0].url.scheme == "http"
    assert server.requests[0].url.host == "odoo.acme.example"

    # 13. A {"error": ...} body during search_read raises OdooError naming
    #     the model, with neither the API key nor the body in the message.
    _set_env(monkeypatch)
    server = _RpcServer([_result_response(4), _error_response()])
    with OdooClient(client=server.client()) as client:
        exc = _capture(
            lambda: client.search_read("product.product", [], ["id"]), OdooError
        )
    assert type(exc) is OdooError
    message = str(exc)
    assert "product.product" in message
    assert API_KEY not in message
    assert LOGIN not in message
    assert BODY_MARKER not in message
    assert len(server.requests) == 2

    # 14. A transport failure during search_read raises OdooError naming
    #     the model and the failure class, with no credentials or body.
    _set_env(monkeypatch)
    server = _RpcServer([_result_response(5), _transport_failure])
    with OdooClient(client=server.client()) as client:
        exc = _capture(lambda: client.search_read("res.partner", [], ["id"]), OdooError)
    assert type(exc) is OdooError
    message = str(exc)
    assert "res.partner" in message
    assert "ConnectError" in message
    assert API_KEY not in message
    assert LOGIN not in message
    assert "simulated network reset" not in message

    # 15. read_products() maps products with a non-empty default_code to
    #     the InventoryItemCreate shape with source_system "odoo", takes
    #     the uom display name from the [id, name] pair (null when absent),
    #     and skips products whose default_code is false or empty.
    _set_env(monkeypatch)
    products = [
        {"id": 1, "default_code": "W-1", "name": "Widget", "uom_id": [1, "Units"]},
        {"id": 2, "default_code": "G-9", "name": "Gadget", "uom_id": False},
        {"id": 3, "default_code": False, "name": "No Code", "uom_id": [2, "Kg"]},
        {"id": 4, "default_code": "", "name": "Empty Code", "uom_id": [2, "Kg"]},
    ]
    server = _RpcServer([_result_response(6), _result_response(products)])
    with OdooClient(client=server.client()) as client:
        items = client.read_products()
    assert items == [
        {
            "item_code": "W-1",
            "description": "Widget",
            "uom": "Units",
            "source_system": "odoo",
        },
        {
            "item_code": "G-9",
            "description": "Gadget",
            "uom": None,
            "source_system": "odoo",
        },
    ]
    assert len(server.requests) == 2
    args = server.bodies[1]["params"]["args"]
    assert args[3:6] == ["product.product", "search_read", [[]]]
    assert args[6] == {"fields": ["default_code", "name", "uom_id"], "offset": 0}
    for item in items:
        InventoryItemCreate(**item)

    # 16. An authenticate-phase failure during read_products() surfaces as
    #     OdooAuthError naming the failure class, with no credentials.
    _set_env(monkeypatch)
    server = _RpcServer([_transport_failure])
    with OdooClient(client=server.client()) as client:
        exc = _capture(client.read_products, OdooAuthError)
    message = str(exc)
    assert "ConnectError" in message
    assert API_KEY not in message
    assert LOGIN not in message
