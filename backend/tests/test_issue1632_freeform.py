"""Issue #1632 proof: the confirm checks a draft before reusing it.

The Odoo server is fully mocked exactly the way
``test_issue446_freeform.py`` fakes it: the endpoint's ``OdooClient``
is built over an ``httpx.MockTransport`` answering every JSON-RPC call
from canned records (the same stub shape the issue #451 proof uses),
the codes are left unmapped (the M1 translation passes them through),
the write switch (``ODOO_ALLOW_WRITES``) is on, and every confirm goes
out as the development-mode admin token.  The confirm path of
``POST /api/replenishment/actions/draft-po`` now reads the existing
draft's lines with one ``search_read`` of ``purchase.order.line``
(domain ``[["order_id", "=", <the draft's id>]]``, the fields
``product_id``, ``product_qty`` and ``partner_id``) and reuses the
draft only when it has exactly one line whose product is the resolved
product, whose vendor is the resolved vendor, and whose quantity
equals the confirm's within 0.005; anything else answers 409 with the
conflict message naming the draft and creates nothing.

Proved here (every response field is read strictly —
``body["already_existed"]``, never a defaulted ``.get``):

  * reuse: with an existing draft holding one line of 12 units of the
    same product from the same vendor, a confirm of 12 answers 200
    with ``already_existed`` true, the draft's id and name, one
    ``already_existed`` audit row, and no ``create`` call — and a
    confirm of 12.004 reuses the same way (within the 0.005
    tolerance);
  * quantity conflict: a confirm of 15 against that draft answers 409
    with exactly the issue's message naming the draft, one ``error``
    audit row with ``error_class`` ``OdooDraftConflictError``, and no
    ``create``, ``write`` or ``unlink`` call;
  * a draft whose line names another vendor, a draft whose line names
    another product, and a draft with two lines each answer the same
    409 to a confirm of 12;
  * with no existing draft one order is created as today (200,
    ``already_existed`` false, exactly one ``create`` with the exact
    values, one ``created`` audit row).
"""

import json
from urllib.parse import quote

import httpx
import psycopg2
import psycopg2.extras
from fastapi.testclient import TestClient

import app.api.replenishment_actions as replenishment_actions
from app.db.connection import get_connection
from app.feed.odoo_client import OdooClient
from app.main import app

BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"
UID = 9

CONFIG_VARS = ("ODOO_BASE_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY")
CLEAN_VARS = (*CONFIG_VARS, "ODOO_CA_BUNDLE", "ODOO_TIMEOUT_SECONDS")

URL = "/api/replenishment/actions/draft-po"
ITEM_CODE = "WIDGET 1"  # space forces percent-encoding in the origin
SITE_CODE = "WH-1"
SUPPLIER_CODE = "VENDOR-9"
PRODUCT_ID = 11
OTHER_PRODUCT_ID = 12
PARTNER_ID = 501
OTHER_PARTNER_ID = 502
PICKING_TYPE_ID = 301
PRODUCT = {"id": PRODUCT_ID, "default_code": ITEM_CODE, "uom_id": [1, "Units"]}
OTHER_PRODUCT = {
    "id": OTHER_PRODUCT_ID,
    "default_code": "OTHER-9",
    "uom_id": [1, "Units"],
}
PARTNER = {"id": PARTNER_ID, "ref": SUPPLIER_CODE, "name": "Vendor Nine"}
OTHER_PARTNER = {"id": OTHER_PARTNER_ID, "ref": "VENDOR-3", "name": "Vendor Three"}
WAREHOUSE = {"id": 201, "code": SITE_CODE, "in_type_id": PICKING_TYPE_ID}
DEFAULT_ORIGIN = (
    "AIERP:replenishment:" + quote(ITEM_CODE, safe="") + ":" + quote(SITE_CODE, safe="")
)
assert DEFAULT_ORIGIN == "AIERP:replenishment:WIDGET%201:WH-1"
DRAFT_NAME = "PO/2026/0042"


def conflict_detail(name):
    return (
        f"Draft purchase order {name} already exists for this item-site "
        "with a different supplier, item or quantity. Change or cancel it "
        "in Odoo, then confirm again."
    )


class _OdooStub:
    """MockTransport handler answering the Odoo JSON-RPC endpoint.

    Records every request as ``(service, method, args)``.  Answers
    ``common.authenticate`` with a fixed uid, answers each model's
    ``search_read`` from canned records by domain — including the
    ``purchase.order.line`` read of an existing draft's lines — and
    answers ``purchase.order`` ``create`` with a fresh id plus the
    line the create carries.  Any other service, method, model, or
    domain shape fails loudly.
    """

    def __init__(
        self,
        products=(),
        partners=(),
        warehouses=(),
        draft_orders=(),
        order_lines=(),
    ):
        self.products = list(products)
        self.partners = list(partners)
        self.warehouses = list(warehouses)
        self.draft_orders = list(draft_orders)
        self.order_lines = list(order_lines)
        self.created = []
        self.calls = []
        self._next_id = 700

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{BASE_URL}/jsonrpc", f"request to {request.url}"
        envelope = json.loads(request.content)
        params = envelope["params"]
        service, method, args = params["service"], params["method"], params["args"]
        self.calls.append((service, method, args))
        if service == "common" and method == "authenticate":
            assert args[0] == DB
            return self._result(envelope["id"], UID)
        if service == "object" and method == "execute_kw":
            model, kw_method, payload = args[3], args[4], args[5]
            if kw_method == "search_read":
                return self._result(envelope["id"], self._search(model, payload[0]))
            if kw_method == "create" and model == "purchase.order":
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
                for _op, _sequence, line_values in values.get("order_line", []):
                    self.order_lines.append(
                        {
                            "order_id": self._next_id,
                            "product_id": line_values["product_id"],
                            "product_qty": line_values["product_qty"],
                            "partner_id": values["partner_id"],
                        }
                    )
                return self._result(envelope["id"], self._next_id)
        raise AssertionError(
            f"forbidden or unexpected call {service}.{method} {args[3:]}"
        )

    @staticmethod
    def _result(request_id, result):
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": request_id, "result": result}
        )

    def _search(self, model, domain):
        key, op, value = domain[0]
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
        elif model == "purchase.order.line":
            assert key == "order_id"
            return [
                record for record in self.order_lines if record.get("order_id") == value
            ]
        else:
            raise AssertionError(f"unexpected model {model}")
        return [record for record in records if record.get(key) == value]


def test_issue1632_freeform(monkeypatch, client):
    for var in (*CLEAN_VARS, "ODOO_ALLOW_INSECURE", "ODOO_ALLOW_WRITES"):
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

    # confirms are admin-only: every confirm below goes out as the
    # development-mode admin stub user.
    admin = TestClient(app, headers={"Authorization": "Bearer dev-secret-token:admin"})

    def body(**overrides):
        payload = {
            "item_code": ITEM_CODE,
            "site_code": SITE_CODE,
            "supplier_code": SUPPLIER_CODE,
            "quantity": 12,
            "unit_price": 4.25,
        }
        payload.update(overrides)
        return payload

    def post(stub, **overrides):
        """POST a confirm with the router's ``OdooClient`` over ``stub``."""
        http_client = httpx.Client(transport=httpx.MockTransport(stub.handle))
        monkeypatch.setattr(
            replenishment_actions,
            "OdooClient",
            lambda: OdooClient(client=http_client),
        )
        try:
            return admin.post(URL, json=body(confirm=True, **overrides))
        finally:
            http_client.close()

    def create_calls(stub):
        return [
            args
            for service, method, args in stub.calls
            if service == "object" and args[4] == "create"
        ]

    def line_reads(stub):
        return [
            args
            for service, method, args in stub.calls
            if service == "object"
            and method == "execute_kw"
            and args[3] == "purchase.order.line"
        ]

    def kw_methods(stub):
        return {args[4] for service, method, args in stub.calls if service == "object"}

    def stored_rows():
        """Every stored audit row, insertion order (created_at, id)."""
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM draft_po_actions ORDER BY created_at, id")
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    target_codes = {
        "item_code": ITEM_CODE,
        "site_code": SITE_CODE,
        "supplier_code": SUPPLIER_CODE,
    }

    # -- 1. Reuse: the existing draft holds one line of 12 units of the
    #    same product from the same vendor.  A confirm of 12 reuses the
    #    draft: 200, already_existed true, the draft's id and name, one
    #    already_existed audit row, no create call, and the one line
    #    read with the exact domain and fields. -------------------------
    stub = _OdooStub(
        products=[PRODUCT],
        partners=[PARTNER],
        warehouses=[WAREHOUSE],
        draft_orders=[
            {
                "id": 42,
                "name": DRAFT_NAME,
                "state": "draft",
                "origin": DEFAULT_ORIGIN,
            }
        ],
        order_lines=[
            {
                "order_id": 42,
                "product_id": PRODUCT_ID,
                "product_qty": 12,
                "partner_id": PARTNER_ID,
            }
        ],
    )
    before = len(stored_rows())
    response = post(stub)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["dry_run"] is False
    assert payload["already_existed"] is True
    assert payload["odoo_id"] == 42
    assert payload["name"] == DRAFT_NAME
    assert payload["state"] == "draft"
    assert payload["target"] == target_codes
    assert create_calls(stub) == []
    assert stub.created == []
    reads = line_reads(stub)
    assert len(reads) == 1
    assert reads[0][4] == "search_read"
    assert reads[0][5][0] == [["order_id", "=", 42]]
    assert reads[0][6]["fields"] == ["product_id", "product_qty", "partner_id"]
    rows = stored_rows()[before:]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "already_existed"
    assert rows[0]["odoo_id"] == 42
    assert rows[0]["odoo_name"] == DRAFT_NAME
    assert rows[0]["error_class"] is None

    # 1b. Within tolerance: a confirm of 12.004 reuses the same way.
    before = len(stored_rows())
    response = post(stub, quantity=12.004)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["dry_run"] is False
    assert payload["already_existed"] is True
    assert payload["odoo_id"] == 42
    assert payload["name"] == DRAFT_NAME
    assert payload["state"] == "draft"
    assert create_calls(stub) == []
    assert stub.created == []
    rows = stored_rows()[before:]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "already_existed"
    assert rows[0]["odoo_id"] == 42
    assert rows[0]["odoo_name"] == DRAFT_NAME
    assert rows[0]["error_class"] is None

    # 2. Quantity conflict: a confirm of 15 answers 409 with exactly
    #    the issue's message naming the draft, one error audit row with
    #    error_class OdooDraftConflictError, and no create, write or
    #    unlink call (only search_reads so far). ------------------------
    before = len(stored_rows())
    response = post(stub, quantity=15)
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": conflict_detail(DRAFT_NAME)}
    assert DRAFT_NAME in response.json()["detail"]
    assert create_calls(stub) == []
    assert stub.created == []
    assert kw_methods(stub) <= {"search_read"}
    rows = stored_rows()[before:]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["error_class"] == "OdooDraftConflictError"
    assert rows[0]["odoo_id"] is None
    assert rows[0]["odoo_name"] is None

    # 3. Different vendor: the draft's line names another vendor — the
    #    same 409 to a confirm of 12. -----------------------------------
    stub = _OdooStub(
        products=[PRODUCT],
        partners=[PARTNER, OTHER_PARTNER],
        warehouses=[WAREHOUSE],
        draft_orders=[
            {
                "id": 43,
                "name": "PO/2026/0043",
                "state": "draft",
                "origin": DEFAULT_ORIGIN,
            }
        ],
        order_lines=[
            {
                "order_id": 43,
                "product_id": PRODUCT_ID,
                "product_qty": 12,
                "partner_id": OTHER_PARTNER_ID,
            }
        ],
    )
    before = len(stored_rows())
    response = post(stub)
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": conflict_detail("PO/2026/0043")}
    assert create_calls(stub) == []
    assert stub.created == []
    assert kw_methods(stub) <= {"search_read"}
    rows = stored_rows()[before:]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["error_class"] == "OdooDraftConflictError"

    # 4. Different product: the draft's line names another product —
    #    the same 409 to a confirm of 12. --------------------------------
    stub = _OdooStub(
        products=[PRODUCT, OTHER_PRODUCT],
        partners=[PARTNER],
        warehouses=[WAREHOUSE],
        draft_orders=[
            {
                "id": 44,
                "name": "PO/2026/0044",
                "state": "draft",
                "origin": DEFAULT_ORIGIN,
            }
        ],
        order_lines=[
            {
                "order_id": 44,
                "product_id": OTHER_PRODUCT_ID,
                "product_qty": 12,
                "partner_id": PARTNER_ID,
            }
        ],
    )
    before = len(stored_rows())
    response = post(stub)
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": conflict_detail("PO/2026/0044")}
    assert create_calls(stub) == []
    assert stub.created == []
    assert kw_methods(stub) <= {"search_read"}
    rows = stored_rows()[before:]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["error_class"] == "OdooDraftConflictError"

    # 5. Two lines: a draft with two lines answers the same 409 to a
    #    confirm of 12. ---------------------------------------------------
    stub = _OdooStub(
        products=[PRODUCT],
        partners=[PARTNER],
        warehouses=[WAREHOUSE],
        draft_orders=[
            {
                "id": 45,
                "name": "PO/2026/0045",
                "state": "draft",
                "origin": DEFAULT_ORIGIN,
            }
        ],
        order_lines=[
            {
                "order_id": 45,
                "product_id": PRODUCT_ID,
                "product_qty": 6,
                "partner_id": PARTNER_ID,
            },
            {
                "order_id": 45,
                "product_id": PRODUCT_ID,
                "product_qty": 6,
                "partner_id": PARTNER_ID,
            },
        ],
    )
    before = len(stored_rows())
    response = post(stub)
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": conflict_detail("PO/2026/0045")}
    assert create_calls(stub) == []
    assert stub.created == []
    assert kw_methods(stub) <= {"search_read"}
    rows = stored_rows()[before:]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["error_class"] == "OdooDraftConflictError"

    # 6. No existing draft: one order is created as today — 200,
    #    already_existed false, exactly one create with the exact
    #    values, one created audit row. ----------------------------------
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    before = len(stored_rows())
    response = post(stub)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["dry_run"] is False
    assert payload["already_existed"] is False
    assert payload["odoo_id"] == 701
    assert payload["name"] == "PO/2026/0701"
    assert payload["state"] == "draft"
    assert payload["target"] == target_codes
    creates = create_calls(stub)
    assert len(creates) == 1
    values = creates[0][5][0]
    assert values == {
        "partner_id": PARTNER_ID,
        "picking_type_id": PICKING_TYPE_ID,
        "origin": DEFAULT_ORIGIN,
        "order_line": [
            [
                0,
                0,
                {
                    "product_id": PRODUCT_ID,
                    "product_qty": 12.0,
                    "price_unit": 4.25,
                    "uom_id": 1,
                },
            ]
        ],
    }
    rows = stored_rows()[before:]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "created"
    assert rows[0]["odoo_id"] == 701
    assert rows[0]["odoo_name"] == "PO/2026/0701"
    assert rows[0]["error_class"] is None
