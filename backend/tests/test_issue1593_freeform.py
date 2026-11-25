"""Issue #1593 proof: the draft-PO create line names its unit.

The Odoo server is fully mocked the way
``test_issue445_freeform.py`` fakes it (an ``httpx.MockTransport``
answers every JSON-RPC call from canned records, so no network is
touched) with the write switch (``ODOO_ALLOW_WRITES``) on, and the
route part is set up the way ``test_issue451_freeform.py`` sets it up
(the endpoint's ``OdooClient`` is built over the stub transport, the
confirm goes out as the development-mode admin token).

Proved here:

  * the unit is sent: with a product whose ``uom_id`` is
    ``[7, "Units"]`` the one create's line is exactly
    ``[0, 0, {"product_id": 11, "product_qty": 24,
    "price_unit": 4.25, "uom_id": 7}]`` — the payload is read
    strictly (``line["uom_id"]``, never a defaulted ``.get``);
  * the read: the create's product request asked the fields
    ``["id", "uom_id"]`` and the requests are the same models in the
    same order a create makes on main, with no added request;
  * no unit: with a product whose ``uom_id`` is ``false`` the call
    raises ``OdooError`` with the message "item '<item code>' has no
    unit of measure in Odoo" — a message that does not begin with
    ``the product matching``, ``no product matches`` or ``multiple
    products match`` — before the idempotency search: the fake
    recorded no ``create`` and no ``purchase.order`` search;
  * the route's reason: through the confirm route the same unit-less
    product answers status 502 with the reason
    ``upstream Odoo request failed`` and never a text saying the item
    has no Odoo mapping;
  * unchanged: ``preview_draft_purchase_order`` and
    ``write_reorder_rule`` still request the product fields
    ``["id"]`` only.
"""

import json
from urllib.parse import quote

import httpx
from fastapi.testclient import TestClient

import app.api.replenishment_actions as replenishment_actions
from app.feed.odoo_client import OdooClient, OdooError
from app.main import app

BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"
UID = 9

CONFIG_VARS = ("ODOO_BASE_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY")
CLEAN_VARS = (
    *CONFIG_VARS,
    "ODOO_CA_BUNDLE",
    "ODOO_TIMEOUT_SECONDS",
    "ODOO_ALLOW_INSECURE",
    "ODOO_ALLOW_WRITES",
)

URL = "/api/replenishment/actions/draft-po"
ITEM_CODE = "WIDGET-1"
SITE_CODE = "WH-1"
SUPPLIER_CODE = "VENDOR-9"
PRODUCT_ID = 11
PARTNER_ID = 501
PICKING_TYPE_ID = 301
UNIT_ID = 7
UNIT_NAME = "Units"
PRODUCT = {
    "id": PRODUCT_ID,
    "default_code": ITEM_CODE,
    "uom_id": [UNIT_ID, UNIT_NAME],
}
PRODUCT_NO_UNIT = {
    "id": PRODUCT_ID,
    "default_code": ITEM_CODE,
    "uom_id": False,
}
PARTNER = {"id": PARTNER_ID, "ref": SUPPLIER_CODE, "name": "Vendor Nine"}
WAREHOUSE = {"id": 201, "code": SITE_CODE, "in_type_id": PICKING_TYPE_ID}
REORDER_RULE = {
    "id": 901,
    "active": True,
    "product_id": PRODUCT_ID,
    "warehouse_id": 201,
    "product_min_qty": 5.0,
    "product_max_qty": 50.0,
}
DEFAULT_TAG = (
    "AIERP:replenishment:" + quote(ITEM_CODE, safe="") + ":" + quote(SITE_CODE, safe="")
)


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
        orderpoints=(),
    ):
        self.products = list(products)
        self.partners = list(partners)
        self.warehouses = list(warehouses)
        self.draft_orders = list(draft_orders)
        self.orderpoints = list(orderpoints)
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
                "stock.warehouse": self.warehouses,
            }.get(model)
            assert pool is not None, f"unexpected in-domain model {model}"
            return [record for record in pool if record.get("id") in value]
        assert op == "=", f"unexpected operator {op!r}"
        if model == "product.product":
            assert key == "default_code"
            records = self.products
        elif model == "res.partner":
            assert key in ("ref", "name")
            records = self.partners
        elif model == "stock.warehouse":
            assert key == "code"
            records = self.warehouses
        elif model == "stock.warehouse.orderpoint":
            assert key == "active"
            records = self.orderpoints
        elif model == "purchase.order":
            pool = self.draft_orders + self.created
            if key == "id":
                return [record for record in pool if record.get("id") == value]
            assert key == "origin"
            assert len(domain) == 2 and domain[1] == ["state", "=", "draft"]
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


def _create_call(client, **overrides):
    kwargs = dict(
        item_code=ITEM_CODE,
        site_code=SITE_CODE,
        supplier_code=SUPPLIER_CODE,
        quantity=24,
        unit_price=4.25,
    )
    kwargs.update(overrides)
    return client.create_draft_purchase_order(**kwargs)


def _object_calls(stub, model, kw_method):
    return [
        args
        for service, method, args in stub.calls
        if service == "object"
        and method == "execute_kw"
        and args[3] == model
        and args[4] == kw_method
    ]


def test_issue1593_freeform(monkeypatch):
    for var in CLEAN_VARS:
        monkeypatch.delenv(var, raising=False)
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
    monkeypatch.setenv("ODOO_ALLOW_WRITES", "1")

    # 1. The unit is sent: the one create's line carries the product's
    #    base unit id, and the read asked ["id", "uom_id"] — the same
    #    models in the same order a create makes on main, no added
    #    request.
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    with _StubbedClient(stub) as client:
        result = _create_call(client)
    assert result == {
        "odoo_id": 701,
        "name": "PO/2026/0701",
        "state": "draft",
        "already_existed": False,
    }
    assert len(stub.created) == 1
    created = stub.created[0]
    assert created["partner_id"] == PARTNER_ID
    assert created["picking_type_id"] == PICKING_TYPE_ID
    assert created["origin"] == DEFAULT_TAG
    line = created["order_line"][0]
    values = line[2]
    assert line == [0, 0, values]
    assert values["product_id"] == PRODUCT_ID
    assert values["product_qty"] == 24
    assert values["price_unit"] == 4.25
    assert values["uom_id"] == UNIT_ID
    assert line == [
        0,
        0,
        {
            "product_id": PRODUCT_ID,
            "product_qty": 24,
            "price_unit": 4.25,
            "uom_id": UNIT_ID,
        },
    ]
    assert len(_object_calls(stub, "purchase.order", "create")) == 1
    product_reads = _object_calls(stub, "product.product", "search_read")
    assert len(product_reads) == 1
    assert product_reads[0][6]["fields"] == ["id", "uom_id"]
    sequence = [
        (
            service,
            method,
            args[3] if service == "object" else None,
            args[4] if service == "object" else None,
        )
        for service, method, args in stub.calls
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

    # 2. No unit: a product whose uom_id is false fails closed with the
    #    unit message — before the idempotency search and before any
    #    create — and the message does not begin with a resolution
    #    prefix.
    no_unit_stub = _OdooStub(
        products=[PRODUCT_NO_UNIT], partners=[PARTNER], warehouses=[WAREHOUSE]
    )
    with _StubbedClient(no_unit_stub) as client:
        try:
            _create_call(client)
        except OdooError as exc:
            message = str(exc)
        else:
            raise AssertionError("OdooError was not raised")
    assert message == f"item '{ITEM_CODE}' has no unit of measure in Odoo"
    for prefix in (
        "the product matching",
        "no product matches",
        "multiple products match",
    ):
        assert not message.startswith(prefix)
    assert no_unit_stub.created == []
    assert _object_calls(no_unit_stub, "purchase.order", "search_read") == []
    assert _object_calls(no_unit_stub, "purchase.order", "create") == []
    sequence = [
        (
            service,
            method,
            args[3] if service == "object" else None,
            args[4] if service == "object" else None,
        )
        for service, method, args in no_unit_stub.calls
    ]
    assert sequence == [
        ("common", "authenticate", None, None),
        ("object", "execute_kw", "product.product", "search_read"),
    ]

    # 3. The route's reason: through the confirm route (set up the way
    #    the issue #451 proof sets it up) the same unit-less product
    #    answers 502 with the generic upstream reason and never a text
    #    saying the item has no Odoo mapping.
    stub = _OdooStub(
        products=[PRODUCT_NO_UNIT], partners=[PARTNER], warehouses=[WAREHOUSE]
    )
    http_client = httpx.Client(transport=httpx.MockTransport(stub.handle))
    monkeypatch.setattr(
        replenishment_actions,
        "OdooClient",
        lambda: OdooClient(client=http_client),
    )
    try:
        with TestClient(
            app, headers={"Authorization": "Bearer dev-secret-token:admin"}
        ) as admin:
            response = admin.post(
                URL,
                json={
                    "item_code": ITEM_CODE,
                    "site_code": SITE_CODE,
                    "supplier_code": SUPPLIER_CODE,
                    "quantity": 24,
                    "unit_price": 4.25,
                    "confirm": True,
                },
            )
    finally:
        http_client.close()
    assert response.status_code == 502, response.text
    assert response.json() == {"detail": "upstream Odoo request failed"}
    assert "no Odoo mapping" not in response.text
    assert stub.created == []
    assert _object_calls(stub, "purchase.order", "search_read") == []

    # 4. Unchanged: the preview and the reorder-rule write still read
    #    the product with the fields ["id"] only.
    preview_stub = _OdooStub(
        products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE]
    )
    with _StubbedClient(preview_stub) as client:
        preview = client.preview_draft_purchase_order(
            item_code=ITEM_CODE,
            site_code=SITE_CODE,
            supplier_code=SUPPLIER_CODE,
        )
    assert preview == {
        "resolved": {
            "product_id": PRODUCT_ID,
            "partner_id": PARTNER_ID,
            "picking_type_id": PICKING_TYPE_ID,
        },
        "origin": DEFAULT_TAG,
        "existing": None,
    }
    assert preview_stub.created == []
    preview_product_reads = _object_calls(
        preview_stub, "product.product", "search_read"
    )
    assert len(preview_product_reads) == 1
    assert preview_product_reads[0][6]["fields"] == ["id"]

    rule_stub = _OdooStub(
        products=[PRODUCT],
        partners=[PARTNER],
        warehouses=[WAREHOUSE],
        orderpoints=[REORDER_RULE],
    )
    with _StubbedClient(rule_stub) as client:
        written = client.write_reorder_rule(
            item_code=ITEM_CODE,
            site_code=SITE_CODE,
            reorder_point=5.0,
            max_qty=50.0,
        )
    assert written == {
        "outcome": "unchanged",
        "before": {"reorder_point": 5.0, "max_qty": 50.0},
        "after": {"reorder_point": 5.0, "max_qty": 50.0},
    }
    rule_product_reads = _object_calls(rule_stub, "product.product", "search_read")
    assert len(rule_product_reads) == 1
    assert rule_product_reads[0][6]["fields"] == ["id"]
