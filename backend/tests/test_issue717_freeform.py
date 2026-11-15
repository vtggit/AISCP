"""Issue #717 proof: ``OdooClient.write_reorder_rule``.

With the Odoo server fully mocked (``httpx.MockTransport`` answers every
JSON-RPC call and records it, so no network) and the write switch driven
by the ``ODOO_ALLOW_WRITES`` environment variable, this proves the
fail-closed reorder rule write:

  * with ``ODOO_ALLOW_WRITES`` unset (and set to non-enabling values)
    the call raises ``OdooWritesDisabledError`` and the stubbed
    transport is never called — not even for authentication — and the
    check precedes configuration validation (missing or invalid Odoo
    config still yield the writes-disabled error, not a config error);
  * bad inputs (empty or non-string codes, non-finite, non-numeric or
    bool quantities, a negative reorder point, or a max below the
    reorder point) each raise ``ValueError`` with zero calls;
  * when both values already equal the request (absolute difference
    under 1e-6) the outcome is "unchanged" with the rule's values as
    both ``before`` and ``after``, and no ``write`` is issued;
  * a real change issues exactly one ``write`` on the rule's id with
    exactly the two quantity fields, followed by a ``read`` of that
    same id, and returns outcome "written" with the right ``before``
    and ``after``; the orderpoint lookup requests and returns the rule
    id, and both the write and the read-back use that same id;
  * a read-back that does not match raises
    ``OdooWriteVerificationError`` naming the item, the site and the
    field that did not match;
  * a read-back transport or JSON-RPC failure raises the plain
    ``OdooError`` — a distinct error separate from
    ``OdooWriteVerificationError`` — and is not reported as a
    successful verification;
  * no rule and two rules each raise an ``OdooError`` naming the
    item/site, secret-free, with no ``write`` and no ``create``;
  * a missing (or ambiguous) product or warehouse raises
    ``OdooError`` naming which, before any write;
  * an orderpoint whose finite ``product_min_qty`` and
    ``product_max_qty`` differ by more than the float range (the
    difference overflows to inf) leaves a null ``order_qty`` in
    ``read_item_sites`` — no row ever carries a non-finite value;
  * no error message contains the API key, the login, or a response
    body, and no stub ever sees ``create``, ``unlink`` or any other
    object method beyond the read and the single write.
"""

import json
import math

import httpx

from app.feed.odoo_client import (
    OdooAuthError,
    OdooClient,
    OdooError,
    OdooWritesDisabledError,
    OdooWriteVerificationError,
)

BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"
UID = 9

CONFIG_VARS = ("ODOO_BASE_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY")

ITEM_CODE = "WIDGET-1"
SITE_CODE = "WH-1"
PRODUCT = {"id": 11, "default_code": ITEM_CODE}
WAREHOUSE = {"id": 201, "code": SITE_CODE}
RULE_ID = 900
RULE = {
    "id": RULE_ID,
    "product_id": PRODUCT["id"],
    "warehouse_id": WAREHOUSE["id"],
    "product_min_qty": 10.0,
    "product_max_qty": 50.0,
}

ALLOWED_METHODS = {"search_read", "write", "read"}
FORBIDDEN_METHODS = {"create", "unlink", "button_confirm", "button_cancel"}


class _OdooStub:
    """MockTransport handler answering the Odoo JSON-RPC endpoint.

    Records every request as ``(service, method, args)``. Answers
    ``common.authenticate`` with a fixed uid, answers the product,
    warehouse, location, quant and orderpoint ``search_read`` calls
    from canned records by domain — product and warehouse answer both
    their equality shape and the ``id in`` shape that
    ``read_item_sites`` uses — and answers ``stock.warehouse.
    orderpoint`` ``write`` by applying the quantity fields to the
    rule and returning True, and answers ``read`` from the
    (write-updated) rule unless a read-back failure mode is armed
    (``read_values`` overrides the returned fields, ``read_error``
    makes the read return a JSON-RPC error body, and
    ``read_transport_error`` makes the read raise a transport
    error). Fails loudly on any other model, domain shape, or method.
    """

    def __init__(
        self,
        *,
        products=None,
        warehouses=None,
        rules=None,
        quants=None,
        locations=None,
        read_values=None,
        read_error=None,
        read_transport_error=False,
    ):
        self.products = list(products if products is not None else [PRODUCT])
        self.warehouses = list(warehouses if warehouses is not None else [WAREHOUSE])
        self.rules = [dict(rule) for rule in (rules if rules is not None else [RULE])]
        self.quants = list(quants or [])
        self.locations = list(locations or [])
        self.read_values = read_values
        self.read_error = read_error
        self.read_transport_error = read_transport_error
        self.calls = []
        self.writes = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{BASE_URL}/jsonrpc", f"request to {request.url}"
        assert request.method == "POST"
        body = json.loads(request.content)
        service = body["params"]["service"]
        method = body["params"]["method"]
        args = body["params"]["args"]
        self.calls.append((service, method, args))
        headers = {"Content-Type": "application/json"}
        if service == "object" and method == "execute_kw" and args[4] == "read":
            if self.read_transport_error:
                raise httpx.ConnectError("simulated read-back connection reset")
            if self.read_error is not None:
                envelope = {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "error": {
                        "code": 200,
                        "message": self.read_error,
                        "data": {"name": "OdooServerException", "args": []},
                    },
                }
                return httpx.Response(
                    200, content=json.dumps(envelope).encode("utf-8"), headers=headers
                )
        if service == "common" and method == "authenticate":
            assert args == [DB, LOGIN, API_KEY, {}], f"unexpected authenticate {args}"
            result = UID
        elif service == "object" and method == "execute_kw":
            result = self._execute(args)
        else:
            raise AssertionError(f"unexpected call {service}.{method}")
        envelope = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        return httpx.Response(
            200, content=json.dumps(envelope).encode("utf-8"), headers=headers
        )

    def _execute(self, args):
        db, uid, api_key, model, kw_method, payload = args[:6]
        assert db == DB and uid == UID and api_key == API_KEY
        if kw_method == "search_read":
            return self._search(model, payload[0])
        if kw_method == "write":
            return self._write(model, payload)
        if kw_method == "read":
            return self._read(model, payload)
        raise AssertionError(f"forbidden or unexpected method {kw_method!r} on {model}")

    def _search(self, model, domain):
        if model == "product.product":
            ((key, op, value),) = domain
            if key == "default_code":
                assert op == "=", f"unexpected domain {domain}"
                return [
                    dict(r) for r in self.products if r.get("default_code") == value
                ]
            assert key == "id" and op == "in", f"unexpected domain {domain}"
            return [dict(r) for r in self.products if r.get("id") in value]
        if model == "stock.warehouse":
            ((key, op, value),) = domain
            if key == "code":
                assert op == "=", f"unexpected domain {domain}"
                return [dict(r) for r in self.warehouses if r.get("code") == value]
            assert key == "id" and op == "in", f"unexpected domain {domain}"
            return [dict(r) for r in self.warehouses if r.get("id") in value]
        if model == "stock.location":
            ((key, op, value),) = domain
            assert key == "id" and op == "in", f"unexpected domain {domain}"
            return [dict(r) for r in self.locations if r.get("id") in value]
        if model == "stock.quant":
            ((key, op, value),) = domain
            assert (
                key == "location_id.usage" and op == "=" and value == "internal"
            ), f"unexpected domain {domain}"
            return list(self.quants)
        if model == "stock.warehouse.orderpoint":
            assert domain[0] == ["active", "=", True], f"unexpected domain {domain}"
            if len(domain) == 3:
                assert domain[1][0] == "product_id" and domain[1][1] == "="
                assert domain[2][0] == "warehouse_id" and domain[2][1] == "="
                product_id = domain[1][2]
                warehouse_id = domain[2][2]
                return [
                    dict(rule)
                    for rule in self.rules
                    if rule.get("active", True)
                    and rule.get("product_id") == product_id
                    and rule.get("warehouse_id") == warehouse_id
                ]
            assert len(domain) == 2, f"unexpected domain {domain}"
            assert domain[1][0] == "product_id" and domain[1][1] == "in"
            return [
                dict(rule)
                for rule in self.rules
                if rule.get("active", True) and rule.get("product_id") in domain[1][2]
            ]
        raise AssertionError(f"unexpected model {model}")

    def _write(self, model, payload):
        assert model == "stock.warehouse.orderpoint", f"write on {model}"
        ids, values = payload
        assert isinstance(ids, list) and ids, "write ids must be a non-empty list"
        for rule_id in ids:
            assert isinstance(rule_id, int) and not isinstance(rule_id, bool)
            rule = next((r for r in self.rules if r.get("id") == rule_id), None)
            assert rule is not None, f"write of missing orderpoint {rule_id}"
            assert set(values) <= {"product_min_qty", "product_max_qty"}
            for field, value in values.items():
                rule[field] = value
        self.writes.append((list(ids), dict(values)))
        return True

    def _read(self, model, payload):
        assert model == "stock.warehouse.orderpoint", f"read on {model}"
        ids, fields = payload
        assert isinstance(ids, list) and ids
        out = []
        for rule_id in ids:
            rule = next((r for r in self.rules if r.get("id") == rule_id), None)
            assert rule is not None, f"read of missing orderpoint {rule_id}"
            record = {
                key: rule.get(key)
                for key in ("id", "product_min_qty", "product_max_qty")
            }
            if self.read_values is not None:
                record.update(self.read_values)
            if fields is not None:
                record = {
                    key: value
                    for key, value in record.items()
                    if key in fields or key == "id"
                }
            out.append(record)
        return out


class _StubbedClient:
    """An ``OdooClient`` over the stub transport; closes both on exit."""

    def __init__(self, stub):
        self.stub = stub
        self._http = httpx.Client(transport=httpx.MockTransport(stub.handle))
        self.client = OdooClient(client=self._http)

    def __enter__(self):
        return self.client

    def __exit__(self, *exc_info):
        self.client.close()
        self._http.close()
        return None


def _call(client, **overrides):
    kwargs = dict(
        item_code=ITEM_CODE,
        site_code=SITE_CODE,
        reorder_point=12.0,
        max_qty=60.0,
    )
    kwargs.update(overrides)
    return client.write_reorder_rule(**kwargs)


def _expect_raises_on(client, exc_type, **overrides):
    try:
        _call(client, **overrides)
    except exc_type as exc:
        return exc
    raise AssertionError(f"{exc_type.__name__} was not raised")


def _expect_result(stub, **overrides):
    with _StubbedClient(stub) as client:
        return _call(client, **overrides)


def _expect_failure(stub, exc_type, **overrides):
    with _StubbedClient(stub) as client:
        return _expect_raises_on(client, exc_type, **overrides)


def _object_calls(stub, model=None, kw_method=None):
    out = []
    for service, method, args in stub.calls:
        if service != "object" or method != "execute_kw":
            continue
        if model is not None and args[3] != model:
            continue
        if kw_method is not None and args[4] != kw_method:
            continue
        out.append(args)
    return out


def _kw_methods(stub):
    return [args[4] for service, _method, args in stub.calls if service == "object"]


def _search_domains(stub, model):
    return [args[5][0] for args in _object_calls(stub, model, "search_read")]


def _write_payloads(stub):
    return [
        (args[5][0], args[5][1])
        for args in _object_calls(stub, "stock.warehouse.orderpoint", "write")
    ]


def _read_payloads(stub):
    return [
        (args[5][0], args[5][1])
        for args in _object_calls(stub, "stock.warehouse.orderpoint", "read")
    ]


def _sequence(stub):
    return [
        (
            service,
            method,
            args[3] if service == "object" else None,
            args[4] if service == "object" else None,
        )
        for service, method, args in stub.calls
    ]


def _assert_secret_free(message):
    assert API_KEY not in message, f"API key leaked into {message!r}"
    assert LOGIN not in message, f"login leaked into {message!r}"
    assert DB not in message, f"database name leaked into {message!r}"


def test_issue717_freeform(monkeypatch):
    for var in (
        *CONFIG_VARS,
        "ODOO_CA_BUNDLE",
        "ODOO_TIMEOUT_SECONDS",
        "ODOO_ALLOW_INSECURE",
        "ODOO_ALLOW_WRITES",
    ):
        monkeypatch.delenv(var, raising=False)

    def set_config(allow_writes):
        monkeypatch.delenv("ODOO_ALLOW_WRITES", raising=False)
        if allow_writes is not None:
            monkeypatch.setenv("ODOO_ALLOW_WRITES", allow_writes)
        for var in CONFIG_VARS:
            monkeypatch.setenv(
                var,
                {
                    "ODOO_BASE_URL": BASE_URL,
                    "ODOO_DB": DB,
                    "ODOO_LOGIN": LOGIN,
                    "ODOO_API_KEY": API_KEY,
                }[var],
            )

    def clear_config():
        for var in CONFIG_VARS:
            monkeypatch.delenv(var, raising=False)

    stubbed = []
    disabled_stubs = []

    # 1. The exception contract: OdooWriteVerificationError subclasses
    #    OdooError and is not an authentication or writes-disabled error.
    assert issubclass(OdooWriteVerificationError, OdooError)
    assert not issubclass(OdooWriteVerificationError, OdooAuthError)
    assert not issubclass(OdooWritesDisabledError, OdooWriteVerificationError)

    # 2. Writes off: unset and every non-enabling value raise
    #    OdooWritesDisabledError with the transport never called (no
    #    authenticate, no search_read, no write).
    set_config(None)
    disabled_stub = _OdooStub()
    stubbed.append(disabled_stub)
    disabled_stubs.append(disabled_stub)
    with _StubbedClient(disabled_stub) as client:
        for value in (None, "0", "false", "no", "FALSE", " 0 ", "yesno", "2"):
            set_config(value)
            exc = _expect_raises_on(client, OdooWritesDisabledError)
            message = str(exc)
            assert "ODOO_ALLOW_WRITES" in message
            _assert_secret_free(message)
    assert disabled_stub.calls == []
    assert disabled_stub.writes == []

    # 3. The disabled check precedes configuration validation: with no
    #    Odoo config at all, and with invalid config, the error is still
    #    the writes-disabled one (not a config error) with no request.
    clear_config()
    monkeypatch.setenv("ODOO_ALLOW_WRITES", "0")
    no_config_stub = _OdooStub()
    stubbed.append(no_config_stub)
    disabled_stubs.append(no_config_stub)
    exc = _expect_failure(no_config_stub, OdooWritesDisabledError)
    assert isinstance(exc, OdooWritesDisabledError)
    assert no_config_stub.calls == []

    bad_config_stub = _OdooStub()
    stubbed.append(bad_config_stub)
    disabled_stubs.append(bad_config_stub)
    clear_config()
    monkeypatch.setenv("ODOO_BASE_URL", "not a url")
    monkeypatch.setenv("ODOO_DB", DB)
    monkeypatch.setenv("ODOO_LOGIN", LOGIN)
    monkeypatch.setenv("ODOO_API_KEY", API_KEY)
    exc = _expect_failure(bad_config_stub, OdooWritesDisabledError)
    assert isinstance(exc, OdooWritesDisabledError)
    assert bad_config_stub.calls == []

    # 4. Bad input: ValueError with zero calls, writes enabled.
    set_config("1")
    bad_inputs = [
        dict(item_code=""),
        dict(item_code=None),
        dict(item_code=123),
        dict(site_code=""),
        dict(site_code=None),
        dict(site_code=b"WH-1"),
        dict(reorder_point=None),
        dict(reorder_point="12"),
        dict(reorder_point=True),
        dict(reorder_point=float("nan")),
        dict(reorder_point=float("inf")),
        dict(reorder_point=-0.5),
        dict(reorder_point=10**400),
        dict(max_qty=None),
        dict(max_qty="60"),
        dict(max_qty=False),
        dict(max_qty=float("nan")),
        dict(max_qty=float("-inf")),
        dict(max_qty=-1.0),
        dict(reorder_point=12.0, max_qty=11.9),
    ]
    for overrides in bad_inputs:
        stub = _OdooStub()
        stubbed.append(stub)
        exc = _expect_failure(stub, ValueError, **overrides)
        assert isinstance(exc, ValueError)
        _assert_secret_free(str(exc))
        assert stub.calls == []
        assert stub.writes == []

    # 5. Already equal: outcome "unchanged" with no write call, within
    #    the 1e-6 tolerance; the lookup requested the rule id and the
    #    two quantity fields over the active-only domain.
    equal_stub = _OdooStub()
    stubbed.append(equal_stub)
    result = _expect_result(equal_stub, reorder_point=10.0, max_qty=50.0)
    assert result == {
        "outcome": "unchanged",
        "before": {"reorder_point": 10.0, "max_qty": 50.0},
        "after": {"reorder_point": 10.0, "max_qty": 50.0},
    }
    assert equal_stub.writes == []
    assert "write" not in _kw_methods(equal_stub)
    assert "read" not in _kw_methods(equal_stub)
    assert _sequence(equal_stub) == [
        ("common", "authenticate", None, None),
        ("object", "execute_kw", "product.product", "search_read"),
        ("object", "execute_kw", "stock.warehouse", "search_read"),
        ("object", "execute_kw", "stock.warehouse.orderpoint", "search_read"),
    ]
    assert _search_domains(equal_stub, "product.product") == [
        [["default_code", "=", ITEM_CODE]]
    ]
    assert _search_domains(equal_stub, "stock.warehouse") == [
        [["code", "=", SITE_CODE]]
    ]
    (op_call,) = _object_calls(equal_stub, "stock.warehouse.orderpoint", "search_read")
    assert op_call[5][0] == [
        ["active", "=", True],
        ["product_id", "=", PRODUCT["id"]],
        ["warehouse_id", "=", WAREHOUSE["id"]],
    ]
    assert op_call[6]["fields"] == ["id", "product_min_qty", "product_max_qty"]

    near_stub = _OdooStub()
    stubbed.append(near_stub)
    result = _expect_result(
        near_stub, reorder_point=10.0 + 0.5e-6, max_qty=50.0 - 0.5e-6
    )
    assert result["outcome"] == "unchanged"
    assert result["after"] == result["before"]
    assert near_stub.writes == []
    assert "write" not in _kw_methods(near_stub)

    # 6. A real change: exactly one write on the rule id with exactly
    #    the two fields, followed by a read of that same id; outcome
    #    "written" with the right before and after.
    change_stub = _OdooStub()
    stubbed.append(change_stub)
    result = _expect_result(change_stub)
    assert result == {
        "outcome": "written",
        "before": {"reorder_point": 10.0, "max_qty": 50.0},
        "after": {"reorder_point": 12.0, "max_qty": 60.0},
    }
    assert len(change_stub.writes) == 1
    write_ids, write_values = _write_payloads(change_stub)[0]
    assert write_ids == [RULE_ID]
    assert write_values == {"product_min_qty": 12.0, "product_max_qty": 60.0}
    assert set(write_values) == {"product_min_qty", "product_max_qty"}
    assert _read_payloads(change_stub) == [
        ([RULE_ID], ["id", "product_min_qty", "product_max_qty"])
    ]
    assert _sequence(change_stub) == [
        ("common", "authenticate", None, None),
        ("object", "execute_kw", "product.product", "search_read"),
        ("object", "execute_kw", "stock.warehouse", "search_read"),
        ("object", "execute_kw", "stock.warehouse.orderpoint", "search_read"),
        ("object", "execute_kw", "stock.warehouse.orderpoint", "write"),
        ("object", "execute_kw", "stock.warehouse.orderpoint", "read"),
    ]
    assert "create" not in _kw_methods(change_stub)

    # 7. A read-back that does not match: OdooWriteVerificationError
    #    naming the item, the site and the field; the write had been
    #    attempted, but the failure is not reported as success.
    mismatch_min_stub = _OdooStub(read_values={"product_min_qty": 10.0})
    stubbed.append(mismatch_min_stub)
    exc = _expect_failure(mismatch_min_stub, OdooWriteVerificationError)
    message = str(exc)
    assert ITEM_CODE in message and SITE_CODE in message
    assert "product_min_qty" in message
    _assert_secret_free(message)
    assert len(mismatch_min_stub.writes) == 1

    mismatch_max_stub = _OdooStub(read_values={"product_max_qty": 55.0})
    stubbed.append(mismatch_max_stub)
    exc = _expect_failure(mismatch_max_stub, OdooWriteVerificationError)
    message = str(exc)
    assert ITEM_CODE in message and SITE_CODE in message
    assert "product_max_qty" in message
    _assert_secret_free(message)
    assert len(mismatch_max_stub.writes) == 1

    mismatch_both_stub = _OdooStub(
        read_values={"product_min_qty": 99.0, "product_max_qty": 98.0}
    )
    stubbed.append(mismatch_both_stub)
    exc = _expect_failure(mismatch_both_stub, OdooWriteVerificationError)
    assert "product_min_qty" in str(exc)  # the first field is named
    assert len(mismatch_both_stub.writes) == 1

    # 8. A read-back transport or JSON-RPC failure: a distinct error
    #    separate from OdooWriteVerificationError, not reported as a
    #    successful verification.
    api_fail_stub = _OdooStub(read_error="simulated server error")
    stubbed.append(api_fail_stub)
    exc = _expect_failure(api_fail_stub, OdooError)
    assert isinstance(exc, OdooError)
    assert not isinstance(exc, OdooWriteVerificationError)
    assert not isinstance(exc, OdooAuthError)
    _assert_secret_free(str(exc))
    assert len(api_fail_stub.writes) == 1  # written, never verified ok

    transport_fail_stub = _OdooStub(read_transport_error=True)
    stubbed.append(transport_fail_stub)
    exc = _expect_failure(transport_fail_stub, OdooError)
    assert isinstance(exc, OdooError)
    assert not isinstance(exc, OdooWriteVerificationError)
    assert "ConnectError" in str(exc)
    _assert_secret_free(str(exc))
    assert len(transport_fail_stub.writes) == 1

    # 9. Rule problems: no rule and two rules each raise OdooError
    #    naming the item/site, secret-free, with no write and no
    #    create — the error is raised before any write is attempted.
    no_rule_stub = _OdooStub(rules=[])
    stubbed.append(no_rule_stub)
    exc = _expect_failure(no_rule_stub, OdooError)
    message = str(exc)
    assert "no reorder rule" in message
    assert f"{ITEM_CODE}/{SITE_CODE}" in message
    _assert_secret_free(message)
    assert no_rule_stub.writes == []
    assert "write" not in _kw_methods(no_rule_stub)
    assert "create" not in _kw_methods(no_rule_stub)

    two_rules_stub = _OdooStub(
        rules=[
            dict(RULE),
            {**RULE, "id": 901, "product_min_qty": 1.0, "product_max_qty": 2.0},
        ]
    )
    stubbed.append(two_rules_stub)
    exc = _expect_failure(two_rules_stub, OdooError)
    message = str(exc)
    assert "more than one reorder rule" in message
    assert f"{ITEM_CODE}/{SITE_CODE}" in message
    _assert_secret_free(message)
    assert two_rules_stub.writes == []
    assert "write" not in _kw_methods(two_rules_stub)
    assert "create" not in _kw_methods(two_rules_stub)

    # 10. Missing or ambiguous product or warehouse: OdooError naming
    #     which, before any write.
    missing_product_stub = _OdooStub(products=[])
    stubbed.append(missing_product_stub)
    exc = _expect_failure(missing_product_stub, OdooError)
    assert "product" in str(exc).lower()
    assert ITEM_CODE in str(exc)
    _assert_secret_free(str(exc))
    assert missing_product_stub.writes == []
    assert "write" not in _kw_methods(missing_product_stub)

    ambiguous_product_stub = _OdooStub(products=[PRODUCT, {**PRODUCT, "id": 12}])
    stubbed.append(ambiguous_product_stub)
    exc = _expect_failure(ambiguous_product_stub, OdooError)
    assert "product" in str(exc).lower()
    _assert_secret_free(str(exc))
    assert ambiguous_product_stub.writes == []

    missing_warehouse_stub = _OdooStub(warehouses=[])
    stubbed.append(missing_warehouse_stub)
    exc = _expect_failure(missing_warehouse_stub, OdooError)
    assert "warehouse" in str(exc).lower()
    assert SITE_CODE in str(exc)
    _assert_secret_free(str(exc))
    assert missing_warehouse_stub.writes == []
    assert "write" not in _kw_methods(missing_warehouse_stub)

    ambiguous_warehouse_stub = _OdooStub(
        warehouses=[WAREHOUSE, {**WAREHOUSE, "id": 202}]
    )
    stubbed.append(ambiguous_warehouse_stub)
    exc = _expect_failure(ambiguous_warehouse_stub, OdooError)
    assert "warehouse" in str(exc).lower()
    _assert_secret_free(str(exc))
    assert ambiguous_warehouse_stub.writes == []

    # 11. Across every scenario: the disabled stubs saw zero requests,
    #     and no stub ever saw create, unlink, or any other object
    #     method beyond search_read, write, and read.
    for stub in disabled_stubs:
        assert stub.calls == []
    for stub in stubbed:
        methods = set(_kw_methods(stub))
        assert methods <= ALLOWED_METHODS, f"unexpected methods {methods}"
        assert not (methods & FORBIDDEN_METHODS)

    # 12. Review defect: an orderpoint whose finite product_min_qty
    #     and product_max_qty differ by more than the float range
    #     (max minus min overflows to inf) must not yield a
    #     non-finite order_qty in read_item_sites — the row keeps its
    #     finite reorder_point and max_qty and a null order_qty, and
    #     no row value is non-finite.
    overflow_min = -1.7976931348623157e308
    overflow_max = 1.7976931348623157e308
    overflow_stub = _OdooStub(
        products=[{**PRODUCT, "standard_price": 1.0, "seller_ids": False}],
        warehouses=[WAREHOUSE],
        rules=[
            {
                "id": RULE_ID,
                "product_id": PRODUCT["id"],
                "warehouse_id": WAREHOUSE["id"],
                "product_min_qty": overflow_min,
                "product_max_qty": overflow_max,
            }
        ],
        quants=[{"id": 801, "product_id": PRODUCT["id"], "location_id": 201}],
        locations=[{"id": 201, "warehouse_id": WAREHOUSE["id"]}],
    )
    stubbed.append(overflow_stub)
    with _StubbedClient(overflow_stub) as client:
        rows = client.read_item_sites()
    assert len(rows) == 1
    row = rows[0]
    assert row["item_code"] == ITEM_CODE
    assert row["site_code"] == SITE_CODE
    assert row["reorder_point"] == overflow_min
    assert row["max_qty"] == overflow_max
    assert row["order_qty"] is None  # the overflowed difference is not inf
    for value in row.values():
        if isinstance(value, float):
            assert math.isfinite(value), f"non-finite row value {value!r}"
    assert set(_kw_methods(overflow_stub)) <= ALLOWED_METHODS
