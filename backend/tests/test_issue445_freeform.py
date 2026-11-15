"""Issue #445 proof: ``OdooClient.create_draft_purchase_order``.

With the Odoo server fully mocked (``httpx.MockTransport`` answers every
JSON-RPC call, so no network) and the write switch driven by the
``ODOO_ALLOW_WRITES`` environment variable, this proves the fail-closed
draft purchase order create:

  * with ``ODOO_ALLOW_WRITES`` unset (and set to ``"0"``/``"false"`` and
    other non-enabling values) the call raises ``OdooWritesDisabledError``
    and the stubbed transport is never called — not even for
    authentication — and the check precedes configuration validation
    (missing and invalid Odoo config still yield the writes-disabled
    error, not a config error); the error message carries no
    environment values, credentials, or request payloads, and
    ``OdooWritesDisabledError`` subclasses ``OdooError`` without being
    an authentication error;
  * with writes enabled, the happy path resolves the product by
    ``default_code``, the vendor by ``ref``, and the warehouse by
    ``code`` to its ``in_type_id``, checks the origin tag for an
    existing draft order, and calls ``purchase.order`` ``create``
    exactly once with ``partner_id``, ``picking_type_id``, the origin
    tag, and a single ``order_line`` ``(0, 0, {product_id,
    product_qty, price_unit})``, then returns
    ``{odoo_id, name, state: "draft", already_existed: False}`` read
    back by id;
  * the default origin tag percent-encodes each code
    (``urllib.parse.quote(code, safe="")``), so two different
    ``(item_code, site_code)`` pairs never share a tag even when a
    code contains a colon, a given ``origin`` overrides the default as
    an opaque key, and a second call with the same pair returns the
    created order with ``already_existed`` True and issues no further
    create;
  * a vendor whose ``ref`` is blank resolves by ``name``;
  * an idempotent call where a draft ``purchase.order`` already
    carries the origin tag returns it with ``already_existed`` True
    and issues no ``create`` (a confirmed order with the same tag does
    not match);
  * a non-positive ``quantity`` and a negative ``unit_price`` (and
    other non-finite, non-numeric, or bool values) each raise
    ``ValueError`` before any call;
  * an unresolved product, vendor, and warehouse (and a warehouse
    with no ``in_type_id``) each raise an error naming which, with
    zero or multiple matches failing closed before any ``create``;
  * the module's readers stay fail-closed on hostile quantities: a
    huge integer quantity (too large to convert to a float) does not
    crash ``read_on_hand`` or ``read_demand_events`` — the offending
    row is skipped — and ``read_on_hand`` never emits a non-finite
    sum (finite quants whose total overflows the float range are
    dropped instead of emitting a non-finite ``quantity_on_hand``);
  * the method never calls ``button_confirm``, ``button_cancel``,
    ``write``, or ``unlink``.
"""

import json
import math
from urllib.parse import quote

import httpx

from app.feed.odoo_client import (
    OdooAuthError,
    OdooClient,
    OdooError,
    OdooWritesDisabledError,
)

BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"
UID = 9

CONFIG_VARS = ("ODOO_BASE_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY")

ITEM_CODE = "WIDGET-1"
SITE_CODE = "WH-1"
SUPPLIER_CODE = "VENDOR-9"
PRODUCT = {"id": 11, "default_code": ITEM_CODE}
PARTNER = {"id": 501, "ref": SUPPLIER_CODE, "name": "Vendor Nine"}
WAREHOUSE = {"id": 201, "code": SITE_CODE, "in_type_id": 301}
DEFAULT_TAG = (
    "AIERP:replenishment:" + quote(ITEM_CODE, safe="") + ":" + quote(SITE_CODE, safe="")
)

FORBIDDEN_METHODS = {"button_confirm", "button_cancel", "write", "unlink"}
ALLOWED_METHODS = {"search_read", "create"}


class _OdooStub:
    """MockTransport handler answering the Odoo JSON-RPC endpoint.

    Records every request as ``(service, method, args)``. Answers
    ``common.authenticate`` with a fixed uid, answers each model's
    ``search_read`` from canned records by domain, answers
    ``purchase.order`` ``create`` with a fresh id (the record's
    ``name``/``state`` default to ``"PO/2026/<id>"`` and ``"draft"``),
    and fails loudly on any other service, method, model, or domain
    shape.
    """

    def __init__(
        self,
        products=(),
        partners=(),
        warehouses=(),
        draft_orders=(),
        locations=(),
        quants=(),
        moves=(),
    ):
        self.products = list(products)
        self.partners = list(partners)
        self.warehouses = list(warehouses)
        self.draft_orders = list(draft_orders)
        self.locations = list(locations)
        self.quants = list(quants)
        self.moves = list(moves)
        self.created = []
        self.calls = []
        self._next_id = 700

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{BASE_URL}/jsonrpc", f"request to {request.url}"
        assert request.method == "POST"
        body = json.loads(request.content)
        service = body["params"]["service"]
        method = body["params"]["method"]
        args = body["params"]["args"]
        self.calls.append((service, method, args))
        if service == "common" and method == "authenticate":
            assert args == [DB, LOGIN, API_KEY, {}], f"unexpected authenticate {args}"
            result = UID
        elif service == "object" and method == "execute_kw":
            result = self._execute(args)
        else:
            raise AssertionError(f"unexpected call {service}.{method}")
        envelope = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        return httpx.Response(
            200,
            content=json.dumps(envelope).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def _execute(self, args):
        db, uid, api_key, model, kw_method, payload = args[:6]
        assert db == DB and uid == UID and api_key == API_KEY
        if kw_method == "search_read":
            return self._search(model, payload[0])
        if kw_method == "create":
            assert model == "purchase.order", f"create on {model}"
            values = payload[0]
            self._next_id += 1
            self.created.append(
                {
                    "id": self._next_id,
                    "name": f"PO/2026/{self._next_id:04d}",
                    "state": "draft",
                    **values,
                }
            )
            return self._next_id
        raise AssertionError(f"forbidden or unexpected method {kw_method!r} on {model}")

    def _search(self, model, domain):
        key, op, value = domain[0]
        if op == "in":
            assert key == "id", f"unexpected in-domain key {key!r}"
            pool = {
                "product.product": self.products,
                "stock.location": self.locations,
                "stock.warehouse": self.warehouses,
            }.get(model)
            assert pool is not None, f"unexpected in-domain model {model}"
            return [record for record in pool if record.get("id") in value]
        assert op == "=", f"unexpected operator {op!r}"
        if model == "stock.quant":
            assert domain == [["location_id.usage", "=", "internal"]]
            return self.quants
        if model == "stock.move":
            assert domain == [
                ["state", "=", "done"],
                ["location_dest_id.usage", "=", "customer"],
            ]
            return self.moves
        if model == "product.product":
            assert key == "default_code"
            records = self.products
        elif model == "res.partner":
            assert key in ("ref", "name")
            records = self.partners
        elif model == "stock.warehouse":
            assert key == "code"
            records = self.warehouses
        elif model == "purchase.order":
            pool = self.draft_orders + self.created
            if key == "id":
                return [record for record in pool if record.get("id") == value]
            assert (
                key == "origin"
                and len(domain) == 2
                and domain[1]
                == [
                    "state",
                    "=",
                    "draft",
                ]
            )
            return [
                record
                for record in pool
                if record.get("origin") == value and record.get("state") == "draft"
            ]
        else:
            raise AssertionError(f"unexpected model {model}")
        return [record for record in records if record.get(key) == value]


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
        supplier_code=SUPPLIER_CODE,
        quantity=12,
        unit_price=4.25,
    )
    kwargs.update(overrides)
    return client.create_draft_purchase_order(**kwargs)


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


def _create_values(stub):
    return [args[5][0] for args in _object_calls(stub, "purchase.order", "create")]


def test_issue445_freeform(monkeypatch):
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

    # 1. The exception contract: OdooWritesDisabledError subclasses
    #    OdooError and is not an authentication error.
    assert issubclass(OdooWritesDisabledError, OdooError)
    assert not issubclass(OdooWritesDisabledError, OdooAuthError)
    assert not issubclass(OdooAuthError, OdooWritesDisabledError)

    # 2. Writes disabled: unset and every non-enabling value raise
    #    OdooWritesDisabledError with the transport never called (no
    #    authenticate, no search_read, no create).
    set_config(None)
    disabled_stub = _OdooStub(
        products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE]
    )
    stubbed.append(disabled_stub)
    disabled_stubs.append(disabled_stub)
    with _StubbedClient(disabled_stub) as client:
        for value in (None, "0", "false", "no", "FALSE", " 0 ", "yesno", "2"):
            set_config(value)
            exc = _expect_raises_on(client, OdooWritesDisabledError)
            message = str(exc)
            assert "ODOO_ALLOW_WRITES" in message
            assert API_KEY not in message
            assert LOGIN not in message
            assert DB not in message
    assert disabled_stub.calls == []

    # 3. The disabled check precedes configuration validation: with no
    #    Odoo config at all, and with invalid config, the error is still
    #    the writes-disabled one (not a config error) with no request.
    clear_config()
    monkeypatch.setenv("ODOO_ALLOW_WRITES", "0")
    no_config_stub = _OdooStub()
    stubbed.append(no_config_stub)
    disabled_stubs.append(no_config_stub)
    assert isinstance(
        _expect_failure(no_config_stub, OdooWritesDisabledError),
        OdooWritesDisabledError,
    )
    assert no_config_stub.calls == []

    bad_config_stub = _OdooStub()
    stubbed.append(bad_config_stub)
    disabled_stubs.append(bad_config_stub)
    clear_config()
    monkeypatch.setenv("ODOO_BASE_URL", "not a url")
    monkeypatch.setenv("ODOO_DB", DB)
    monkeypatch.setenv("ODOO_LOGIN", LOGIN)
    monkeypatch.setenv("ODOO_API_KEY", API_KEY)
    assert isinstance(
        _expect_failure(bad_config_stub, OdooWritesDisabledError),
        OdooWritesDisabledError,
    )
    assert bad_config_stub.calls == []

    # 4. Happy path: product by default_code, vendor by ref, warehouse by
    #    code to its in_type_id; one create with the exact values; the
    #    result read back by id; no other object method ever posted.
    set_config("1")
    happy_stub = _OdooStub(
        products=[{"id": 12, "default_code": "OTHER-9"}, PRODUCT],
        partners=[{"id": 502, "ref": "VENDOR-OTHER", "name": "Vendor Other"}, PARTNER],
        warehouses=[{"id": 202, "code": "WH-OTHER", "in_type_id": 302}, WAREHOUSE],
    )
    stubbed.append(happy_stub)
    result = _expect_result(happy_stub)
    assert DEFAULT_TAG == "AIERP:replenishment:WIDGET-1:WH-1"
    assert result == {
        "odoo_id": 701,
        "name": "PO/2026/0701",
        "state": "draft",
        "already_existed": False,
    }
    assert happy_stub.created == [
        {
            "id": 701,
            "name": "PO/2026/0701",
            "state": "draft",
            "partner_id": 501,
            "picking_type_id": 301,
            "origin": DEFAULT_TAG,
            "order_line": [
                [0, 0, {"product_id": 11, "product_qty": 12, "price_unit": 4.25}]
            ],
        }
    ]
    sequence = [
        (
            service,
            method,
            args[3] if service == "object" else None,
            args[4] if service == "object" else None,
        )
        for service, method, args in happy_stub.calls
    ]
    assert sequence == [
        ("common", "authenticate", None, None),
        ("object", "execute_kw", "product.product", "search_read"),
        ("object", "execute_kw", "res.partner", "search_read"),
        ("object", "execute_kw", "stock.warehouse", "search_read"),
        ("object", "execute_kw", "purchase.order", "search_read"),
        ("object", "execute_kw", "purchase.order", "create"),
        ("object", "execute_kw", "purchase.order", "search_read"),
    ]
    assert _search_domains(happy_stub, "product.product") == [
        [["default_code", "=", ITEM_CODE]]
    ]
    assert _search_domains(happy_stub, "res.partner") == [[["ref", "=", SUPPLIER_CODE]]]
    assert _search_domains(happy_stub, "stock.warehouse") == [
        [["code", "=", SITE_CODE]]
    ]
    po_searches = _search_domains(happy_stub, "purchase.order")
    assert po_searches == [
        [["origin", "=", DEFAULT_TAG], ["state", "=", "draft"]],
        [["id", "=", 701]],
    ]
    idempotency_call = _object_calls(happy_stub, "purchase.order", "search_read")[0]
    readback_call = _object_calls(happy_stub, "purchase.order", "search_read")[1]
    assert idempotency_call[6]["fields"] == ["id", "name", "state"]
    assert readback_call[6]["fields"] == ["id", "name", "state"]
    assert _create_values(happy_stub) == [
        {
            "partner_id": 501,
            "picking_type_id": 301,
            "origin": DEFAULT_TAG,
            "order_line": [
                [0, 0, {"product_id": 11, "product_qty": 12, "price_unit": 4.25}]
            ],
        }
    ]

    # 5. The write switch is case-insensitive and whitespace-trimmed:
    #    " YES " enables the path end to end.
    set_config(" YES ")
    trimmed_stub = _OdooStub(
        products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE]
    )
    stubbed.append(trimmed_stub)
    result = _expect_result(trimmed_stub)
    assert result == {
        "odoo_id": 701,
        "name": "PO/2026/0701",
        "state": "draft",
        "already_existed": False,
    }

    # 6. A vendor whose ref is blank resolves by name; unit_price 0 is
    #    accepted (non-negative).
    set_config("true")
    blank_ref_stub = _OdooStub(
        products=[PRODUCT],
        partners=[
            PARTNER,
            {"id": 502, "ref": "", "name": "Acme Trading"},
            {"id": 503, "ref": None, "name": "No Ref At All"},
        ],
        warehouses=[WAREHOUSE],
    )
    stubbed.append(blank_ref_stub)
    result = _expect_result(
        blank_ref_stub, supplier_code="Acme Trading", quantity=3, unit_price=0
    )
    assert result["already_existed"] is False
    assert _search_domains(blank_ref_stub, "res.partner") == [
        [["ref", "=", "Acme Trading"]],
        [["name", "=", "Acme Trading"]],
    ]
    assert _create_values(blank_ref_stub)[0]["partner_id"] == 502

    # 7. Idempotency: a draft purchase.order already carrying the origin
    #    tag is returned with already_existed True and no create; a
    #    confirmed order with the same tag, and a draft order with a
    #    different tag, do not match.
    idempotent_stub = _OdooStub(
        products=[PRODUCT],
        partners=[PARTNER],
        warehouses=[WAREHOUSE],
        draft_orders=[
            {
                "id": 750,
                "name": "PO/2026/0750",
                "state": "draft",
                "origin": DEFAULT_TAG,
            },
            {
                "id": 751,
                "name": "PO/2026/0751",
                "state": "purchase",
                "origin": DEFAULT_TAG,
            },
            {
                "id": 752,
                "name": "PO/2026/0752",
                "state": "draft",
                "origin": "SOME:OTHER:TAG",
            },
        ],
    )
    stubbed.append(idempotent_stub)
    result = _expect_result(idempotent_stub)
    assert result == {
        "odoo_id": 750,
        "name": "PO/2026/0750",
        "state": "draft",
        "already_existed": True,
    }
    assert idempotent_stub.created == []
    assert "create" not in _kw_methods(idempotent_stub)

    # 8. The default origin tag percent-encodes each code, so two
    #    different (item_code, site_code) pairs never share a tag even
    #    when a code contains a colon; a given origin overrides the
    #    default as an opaque key; a repeat of the same pair returns
    #    the created order without a second create.
    tag_colon_a = (
        "AIERP:replenishment:" + quote("A:B", safe="") + ":" + quote("C", safe="")
    )
    tag_colon_b = (
        "AIERP:replenishment:" + quote("A", safe="") + ":" + quote("B:C", safe="")
    )
    assert tag_colon_a == "AIERP:replenishment:A%3AB:C"
    assert tag_colon_b == "AIERP:replenishment:A:B%3AC"
    assert tag_colon_a != tag_colon_b
    colon_stub = _OdooStub(
        products=[{"id": 13, "default_code": "A:B"}],
        partners=[PARTNER],
        warehouses=[{"id": 203, "code": "C", "in_type_id": 303}],
    )
    stubbed.append(colon_stub)
    with _StubbedClient(colon_stub) as client:
        first = _call(
            client,
            item_code="A:B",
            site_code="C",
            quantity=1,
            unit_price=0.5,
            origin="CUSTOM:TAG:1",
        )
        second = _call(
            client, item_code="A:B", site_code="C", quantity=1, unit_price=0.5
        )
        third = _call(
            client, item_code="A:B", site_code="C", quantity=1, unit_price=0.5
        )
    assert first == {
        "odoo_id": 701,
        "name": "PO/2026/0701",
        "state": "draft",
        "already_existed": False,
    }
    assert second == {
        "odoo_id": 702,
        "name": "PO/2026/0702",
        "state": "draft",
        "already_existed": False,
    }
    assert third == {
        "odoo_id": 702,
        "name": "PO/2026/0702",
        "state": "draft",
        "already_existed": True,
    }
    assert [values["origin"] for values in _create_values(colon_stub)] == [
        "CUSTOM:TAG:1",
        tag_colon_a,
    ]
    assert len(colon_stub.created) == 2
    assert len(_object_calls(colon_stub, "purchase.order", "create")) == 2

    # 9. Invalid inputs raise ValueError before any call (writes enabled):
    #    non-positive/non-finite/non-numeric quantity, and negative or
    #    non-numeric unit_price (bools are not numbers here).
    bad_quantities = [
        0,
        -5,
        float("inf"),
        float("-inf"),
        float("nan"),
        True,
        "12",
        None,
        10**400,
    ]
    for quantity in bad_quantities:
        stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
        stubbed.append(stub)
        exc = _expect_failure(stub, ValueError, quantity=quantity)
        assert isinstance(exc, ValueError)
        assert stub.calls == []
    bad_unit_prices = [-0.01, float("-inf"), float("nan"), False, "4.25", None]
    for unit_price in bad_unit_prices:
        stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
        stubbed.append(stub)
        exc = _expect_failure(stub, ValueError, unit_price=unit_price)
        assert isinstance(exc, ValueError)
        assert stub.calls == []

    # 10. Unresolved or ambiguous resolution fails closed, naming which
    #     one, before any create: zero or multiple products, vendors,
    #     warehouses, and a warehouse without an in_type_id.
    resolution_cases = [
        (dict(products=[{"id": 12, "default_code": "OTHER-9"}]), "product"),
        (
            dict(
                products=[
                    {"id": 11, "default_code": ITEM_CODE},
                    {"id": 14, "default_code": ITEM_CODE},
                ]
            ),
            "product",
        ),
        (dict(partners=[]), "vendor"),
        (
            dict(
                partners=[
                    {"id": 501, "ref": SUPPLIER_CODE, "name": "Vendor Nine"},
                    {"id": 504, "ref": SUPPLIER_CODE, "name": "Vendor Four"},
                ]
            ),
            "vendor",
        ),
        (dict(warehouses=[]), "warehouse"),
        (
            dict(
                warehouses=[
                    WAREHOUSE,
                    {"id": 204, "code": SITE_CODE, "in_type_id": 304},
                ]
            ),
            "warehouse",
        ),
        (
            dict(warehouses=[{"id": 201, "code": SITE_CODE, "in_type_id": False}]),
            "in_type_id",
        ),
        (dict(warehouses=[{"id": 201, "code": SITE_CODE}]), "in_type_id"),
    ]
    for overrides, named in resolution_cases:
        spec = dict(
            products=[PRODUCT],
            partners=[PARTNER],
            warehouses=[WAREHOUSE],
        )
        spec.update(overrides)
        stub = _OdooStub(**spec)
        stubbed.append(stub)
        exc = _expect_failure(stub, OdooError)
        message = str(exc)
        assert named in message, f"{named!r} not named in {message!r}"
        assert not isinstance(exc, OdooAuthError)
        assert not isinstance(exc, OdooWritesDisabledError)
        assert stub.created == []
        assert "create" not in _kw_methods(stub)

    # Review defects in this module's readers: a huge integer quantity
    # (10**400, too large to convert to a float) must not crash
    # read_on_hand or read_demand_events — the offending rows are
    # skipped and the finite rows still map — and read_on_hand must
    # never emit a non-finite sum: two finite 1.7e308 quants for one
    # (item, site) overflow the float range, so that key is dropped
    # rather than emitted with quantity_on_hand inf.
    reader_stub = _OdooStub(
        products=[PRODUCT, {"id": 15, "default_code": "BIG-1"}],
        partners=[PARTNER],
        warehouses=[WAREHOUSE],
        locations=[{"id": 100, "warehouse_id": 201}],
        quants=[
            {"id": 601, "product_id": 11, "location_id": 100, "quantity": 10**400},
            {"id": 602, "product_id": 11, "location_id": 100, "quantity": 12.5},
            {"id": 603, "product_id": 15, "location_id": 100, "quantity": 1.7e308},
            {"id": 604, "product_id": 15, "location_id": 100, "quantity": 1.7e308},
        ],
        moves=[
            {
                "id": 605,
                "product_id": 11,
                "location_id": 100,
                "quantity": 10**400,
                "date": "2026-05-01 10:00:00",
            },
            {
                "id": 606,
                "product_id": 11,
                "location_id": 100,
                "quantity": -3,
                "date": "2026-05-02 08:30:00",
            },
        ],
    )
    stubbed.append(reader_stub)
    with _StubbedClient(reader_stub) as client:
        on_hand = client.read_on_hand(as_of="2026-06-01T00:00:00Z")
        events = client.read_demand_events()
    assert on_hand == [
        {
            "item_code": ITEM_CODE,
            "site_code": SITE_CODE,
            "quantity_on_hand": 12.5,
            "as_of": "2026-06-01T00:00:00Z",
            "source_system": "odoo",
        }
    ]
    assert all(math.isfinite(row["quantity_on_hand"]) for row in on_hand)
    assert events == [
        {
            "business_key": f"odoo:{DB}:stock.move:606",
            "item_code": ITEM_CODE,
            "site_code": SITE_CODE,
            "quantity": 3.0,
            "occurred_on": "2026-05-02",
            "event_type": "shipment",
            "source_system": "odoo",
        }
    ]
    assert all(math.isfinite(row["quantity"]) for row in events)

    # 11. Across every scenario: the disabled stubs saw zero requests,
    #     and no stub ever saw button_confirm, button_cancel, write, or
    #     unlink (only search_read and at most the one create).
    for stub in disabled_stubs:
        assert stub.calls == []
    for stub in stubbed:
        methods = set(_kw_methods(stub))
        assert methods <= ALLOWED_METHODS, f"unexpected methods {methods}"
        assert not (methods & FORBIDDEN_METHODS)
