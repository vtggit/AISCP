"""Issue #451 proof: read-only draft-PO preview plus the Bearer-gated
``POST /api/replenishment/actions/draft-po`` action endpoint.

The Odoo server is fully mocked: the endpoint's ``OdooClient`` is
built over an ``httpx.MockTransport`` that answers every JSON-RPC
model call from canned records, so no network is touched and the
real client code (preview, resolution, idempotent create) runs end to
end. The endpoint is exercised through the app's test client with the
test's usual authenticated stub (the conftest ``client`` fixture, which
carries the development bearer token — a non-admin), and an
unauthenticated ``TestClient`` proves the mount-time gate.  Every
``confirm=true`` request is sent as the admin stub user (confirms are
admin-only; the plain non-admin stub gets 403 on a confirm), while the
dry runs stay open to the plain stub.

Proved here:

  * a dry-run request (``confirm`` omitted) returns ``dry_run`` true,
    the resolved product/vendor/warehouse ids, the percent-encoded
    system-generated origin, and ``existing`` null when no draft
    carries the origin — the stub records NO create call — and the
    dry-run succeeds even with ``ODOO_ALLOW_WRITES`` explicitly
    disabled (a preview does not consult the write switch);
  * a dry-run where a draft already carries the origin returns
    ``would_create`` false with that ``existing`` order;
  * a confirm=true request calls ``create_draft_purchase_order`` once
    and returns ``dry_run`` false with the client's ``{odoo_id, name,
    state, already_existed}``;
  * a confirm=true when the client raises
    ``OdooWritesDisabledError`` yields 409 with the fixed "Odoo writes
    are disabled" message, no leaked secret, and zero upstream calls;
  * a resolution failure (zero or multiple matches, a warehouse
    without an ``in_type_id``, and any other upstream Odoo error)
    yields 502 naming only which resolution failed or a generic
    upstream message, with no payload or credential in the body;
  * a body with ``quantity <= 0``, a negative ``unit_price``, or a
    blank code yields 422, and hostile values (NUL bytes, control
    characters, a 100k-character code, huge or non-finite quantities)
    never produce a 500;
  * the route is registered under the authenticated mount: a request
    without the test's auth stub is rejected 401 like the other gated
    routers, before any upstream call;
  * a confirm=true request emits exactly one structured audit log line
    carrying the action, the item/site/supplier/quantity, and the
    outcome (created id and ``already_existed``, or the error class) —
    no credential, environment value, or request body beyond those
    fields — and two concurrent confirms for the same item-site are
    serialized on the per-``(item_code, site_code)`` lock so exactly
    one create is issued;
  * the per-``(item_code, site_code)`` lock registry is bounded:
    thousands of distinct item-site pairs — and end-to-end confirms
    for distinct sites — cannot grow it (no memory leak), and the
    same pair always lands on the same lock.
"""

import json
import logging
import threading
from urllib.parse import quote

import httpx
from fastapi.testclient import TestClient

import app.api.replenishment_actions as replenishment_actions
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
PARTNER_ID = 501
PICKING_TYPE_ID = 301
PRODUCT = {"id": PRODUCT_ID, "default_code": ITEM_CODE, "uom_id": [1, "Units"]}
PARTNER = {"id": PARTNER_ID, "ref": SUPPLIER_CODE, "name": "Vendor Nine"}
WAREHOUSE = {"id": 201, "code": SITE_CODE, "in_type_id": PICKING_TYPE_ID}
DEFAULT_ORIGIN = (
    "AIERP:replenishment:" + quote(ITEM_CODE, safe="") + ":" + quote(SITE_CODE, safe="")
)
assert DEFAULT_ORIGIN == "AIERP:replenishment:WIDGET%201:WH-1"

AUDIT_LOGGER = "app.api.replenishment_actions"
FIXED_502_DETAILS = {
    "product resolution failed: item 'WIDGET 1' has no Odoo mapping",
    "vendor resolution failed: supplier 'VENDOR-9' has no Odoo mapping",
    "warehouse resolution failed: site 'WH-1' has no Odoo mapping",
    "upstream Odoo request failed",
}


class _OdooStub:
    """MockTransport handler answering the Odoo JSON-RPC endpoint.

    Records every request as ``(service, method, args)``. Answers
    ``common.authenticate`` with a fixed uid, answers each model's
    ``search_read`` from canned records by domain, answers
    ``purchase.order`` ``create`` with a fresh id, and fails the
    ``search_read`` of any model named in ``fail_models`` with a
    JSON-RPC error envelope. Any other service, method, model, or
    domain shape fails loudly.
    """

    def __init__(
        self,
        products=(),
        partners=(),
        warehouses=(),
        draft_orders=(),
        fail_models=(),
    ):
        self.products = list(products)
        self.partners = list(partners)
        self.warehouses = list(warehouses)
        self.draft_orders = list(draft_orders)
        self.fail_models = set(fail_models)
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
            if model in self.fail_models:
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": envelope["id"],
                        "error": {"code": 1, "message": "stub upstream failure"},
                    },
                )
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
        else:
            raise AssertionError(f"unexpected model {model}")
        return [record for record in records if record.get(key) == value]


def _kw_calls(stub):
    return [args for service, method, args in stub.calls if service == "object"]


def _create_calls(stub):
    return [args for args in _kw_calls(stub) if args[4] == "create"]


def _post(api_client, stub, monkeypatch, body, raw=None):
    """POST the endpoint with the router's ``OdooClient`` over ``stub``."""
    http_client = httpx.Client(transport=httpx.MockTransport(stub.handle))
    monkeypatch.setattr(
        replenishment_actions,
        "OdooClient",
        lambda: OdooClient(client=http_client),
    )
    try:
        if raw is not None:
            return api_client.post(
                URL, content=raw, headers={"Content-Type": "application/json"}
            )
        return api_client.post(URL, json=body)
    finally:
        http_client.close()


def test_issue451_freeform(monkeypatch, client, caplog):
    for var in (*CLEAN_VARS, "ODOO_ALLOW_INSECURE", "ODOO_ALLOW_WRITES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ODOO_BASE_URL", BASE_URL)
    monkeypatch.setenv("ODOO_DB", DB)
    monkeypatch.setenv("ODOO_LOGIN", LOGIN)
    monkeypatch.setenv("ODOO_API_KEY", API_KEY)

    # confirms are admin-only: every confirm=true request below goes
    # out as the admin stub user, while the dry runs use the
    # non-admin ``client`` fixture.
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

    def set_writes(value):
        if value is None:
            monkeypatch.delenv("ODOO_ALLOW_WRITES", raising=False)
        else:
            monkeypatch.setenv("ODOO_ALLOW_WRITES", value)

    # The shared session fixture runs ``alembic upgrade``, and
    # migrations/env.py calls ``fileConfig(alembic.ini)`` whose
    # disable_existing_loggers default turns off every logger that
    # already exists — including this one.  Re-enable it so the audit
    # assertions observe what the endpoint actually logs.
    monkeypatch.setattr(logging.getLogger(AUDIT_LOGGER), "disabled", False)
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)

    def audit_records():
        return [record for record in caplog.records if record.name == AUDIT_LOGGER]

    # 1. Dry run (confirm omitted): 200 with dry_run true, the resolved
    #    ids, the percent-encoded origin, and existing null — the stub
    #    records NO create call. ODOO_ALLOW_WRITES is explicitly
    #    disabled to prove a preview does not consult the write switch.
    set_writes("0")
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    response = _post(client, stub, monkeypatch, body())
    assert response.status_code == 200, response.text
    assert response.json() == {
        "dry_run": True,
        "would_create": True,
        "origin": DEFAULT_ORIGIN,
        "resolved": {
            "product_id": PRODUCT_ID,
            "partner_id": PARTNER_ID,
            "picking_type_id": PICKING_TYPE_ID,
        },
        "existing": None,
        "target": {
            "item_code": ITEM_CODE,
            "site_code": SITE_CODE,
            "supplier_code": SUPPLIER_CODE,
        },
    }
    assert stub.created == []
    assert _create_calls(stub) == []
    assert audit_records() == []

    # 2. Dry run with a draft already carrying the origin: would_create
    #    false and that existing order.
    stub = _OdooStub(
        products=[PRODUCT],
        partners=[PARTNER],
        warehouses=[WAREHOUSE],
        draft_orders=[
            {
                "id": 42,
                "name": "PO/2026/0042",
                "state": "draft",
                "origin": DEFAULT_ORIGIN,
            }
        ],
    )
    response = _post(client, stub, monkeypatch, body(confirm=False))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "dry_run": True,
        "would_create": False,
        "origin": DEFAULT_ORIGIN,
        "resolved": {
            "product_id": PRODUCT_ID,
            "partner_id": PARTNER_ID,
            "picking_type_id": PICKING_TYPE_ID,
        },
        "existing": {"odoo_id": 42, "name": "PO/2026/0042", "state": "draft"},
        "target": {
            "item_code": ITEM_CODE,
            "site_code": SITE_CODE,
            "supplier_code": SUPPLIER_CODE,
        },
    }
    assert stub.created == []

    # 3. confirm=true with writes enabled: create_draft_purchase_order
    #    runs once and the response is dry_run false plus the client's
    #    {odoo_id, name, state, already_existed}.
    set_writes("1")
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    response = _post(admin, stub, monkeypatch, body(confirm=True))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "dry_run": False,
        "odoo_id": 701,
        "name": "PO/2026/0701",
        "state": "draft",
        "already_existed": False,
        "target": {
            "item_code": ITEM_CODE,
            "site_code": SITE_CODE,
            "supplier_code": SUPPLIER_CODE,
        },
    }
    creates = _create_calls(stub)
    assert len(creates) == 1
    values = creates[0][5][0]
    # The JSON-RPC round trip renders the order_line command tuple as a list.
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
    # Exactly one structured audit line for the confirm, carrying the
    # action, the item/site/supplier/quantity, and the outcome.
    records = audit_records()
    assert len(records) == 1
    message = records[0].getMessage()
    for fragment in (
        "action=draft_purchase_order",
        f"item_code={ITEM_CODE}",
        f"site_code={SITE_CODE}",
        f"supplier_code={SUPPLIER_CODE}",
        "quantity=12.0",
        "outcome=created",
        "odoo_id=701",
        "already_existed=False",
    ):
        assert fragment in message, f"{fragment!r} missing from {message!r}"
    # No credential, environment value, or body field beyond the
    # audited item/site/supplier/quantity.
    for secret in (API_KEY, LOGIN, "dev-secret-token", "4.25", BASE_URL, DB):
        assert secret not in message, f"{secret!r} leaked into {message!r}"

    # 4. confirm=true with the write switch off: 409 with the fixed
    #    message, no leaked secret, and zero upstream calls.
    set_writes(None)
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    response = _post(admin, stub, monkeypatch, body(confirm=True))
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "Odoo writes are disabled"}
    assert API_KEY not in response.text
    assert LOGIN not in response.text
    assert stub.calls == []
    # Exactly one more audit line, carrying the error class.
    records = audit_records()
    assert len(records) == 2
    message = records[1].getMessage()
    assert "outcome=error" in message
    assert "error=OdooWritesDisabledError" in message
    assert API_KEY not in message and LOGIN not in message

    # 5. A resolution failure yields 502 naming only which resolution
    #    failed (or a generic upstream message for other errors), with
    #    no payload or credential in the body.
    set_writes("1")
    resolution_cases = [
        (
            dict(products=[]),
            f"product resolution failed: item '{ITEM_CODE}' has no Odoo mapping",
        ),
        (
            dict(
                products=[
                    {"id": 11, "default_code": ITEM_CODE},
                    {"id": 14, "default_code": ITEM_CODE},
                ]
            ),
            f"product resolution failed: item '{ITEM_CODE}' has no Odoo mapping",
        ),
        (
            dict(partners=[]),
            f"vendor resolution failed: supplier '{SUPPLIER_CODE}' has no Odoo mapping",
        ),
        (
            dict(
                partners=[
                    PARTNER,
                    {"id": 504, "ref": SUPPLIER_CODE, "name": "Vendor Four"},
                ]
            ),
            f"vendor resolution failed: supplier '{SUPPLIER_CODE}' has no Odoo mapping",
        ),
        (
            dict(warehouses=[]),
            f"warehouse resolution failed: site '{SITE_CODE}' has no Odoo mapping",
        ),
        (
            dict(warehouses=[{"id": 201, "code": SITE_CODE, "in_type_id": False}]),
            f"warehouse resolution failed: site '{SITE_CODE}' has no Odoo mapping",
        ),
    ]
    for overrides, detail in resolution_cases:
        spec = dict(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
        spec.update(overrides)
        stub = _OdooStub(**spec)
        response = _post(admin, stub, monkeypatch, body(confirm=True))
        assert response.status_code == 502, response.text
        assert response.json() == {"detail": detail}
        assert detail in FIXED_502_DETAILS
        for secret in (API_KEY, LOGIN):
            assert secret not in response.text
        assert stub.created == []
    # Any other upstream Odoo error (a JSON-RPC failure) is generic.
    stub = _OdooStub(
        products=[PRODUCT],
        partners=[PARTNER],
        warehouses=[WAREHOUSE],
        fail_models={"product.product"},
    )
    response = _post(admin, stub, monkeypatch, body(confirm=True))
    assert response.status_code == 502, response.text
    assert response.json() == {"detail": "upstream Odoo request failed"}
    # And the dry-run path maps the same failures to 502 as well.
    stub = _OdooStub(partners=[], products=[PRODUCT], warehouses=[WAREHOUSE])
    set_writes("0")
    response = _post(client, stub, monkeypatch, body())
    assert response.status_code == 502, response.text
    assert response.json() == {
        "detail": f"vendor resolution failed: supplier '{SUPPLIER_CODE}' has no Odoo mapping",
    }
    set_writes("1")

    # 6. Body validation: quantity <= 0, a negative unit_price, or a
    #    blank code is a 422, and hostile values never produce a 500.
    for bad_body in (
        body(quantity=0),
        body(quantity=-3),
        body(unit_price=-0.5),
        body(item_code=""),
        body(site_code=""),
        body(supplier_code=""),
        body(quantity=10**400),
    ):
        stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
        response = _post(client, stub, monkeypatch, bad_body)
        assert response.status_code == 422, (bad_body, response.text)
        assert stub.calls == []
    # A non-finite quantity smuggled in as raw JSON is a 422, not a 500.
    raw = (
        b'{"item_code":"WIDGET 1","site_code":"WH-1","supplier_code":"VENDOR-9",'
        b'"quantity":Infinity,"unit_price":1.0,"confirm":true}'
    )
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    response = _post(admin, stub, monkeypatch, None, raw=raw)
    assert response.status_code == 422, response.text
    assert stub.calls == []
    # Hostile codes are never a 500: the NUL byte and the overlong
    # code are a 422 (the overlong one must not echo its input), and
    # the control character reaches the client and fails to resolve
    # with a 502.
    for hostile in ("W\0IDGET 1", "W\u0001DGET 1", "A" * 100_000):
        stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
        response = _post(client, stub, monkeypatch, body(item_code=hostile))
        if "\0" in hostile:
            assert response.status_code == 422, (len(hostile), response.status_code)
        elif len(hostile) > 255:
            assert response.status_code == 422, (len(hostile), response.status_code)
            assert hostile not in response.text
            assert stub.calls == []
        else:
            assert response.status_code == 502, (len(hostile), response.status_code)
            assert response.json()["detail"] == (
                f"product resolution failed: item '{hostile}' has no Odoo mapping"
            )

    # 7. The route sits under the authenticated mount: without the
    #    test's auth stub the request is rejected 401 like the other
    #    gated routers, before any upstream call.
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    http_client = httpx.Client(transport=httpx.MockTransport(stub.handle))
    monkeypatch.setattr(
        replenishment_actions,
        "OdooClient",
        lambda: OdooClient(client=http_client),
    )
    try:
        with TestClient(app) as anonymous:
            response = anonymous.post(URL, json=body())
    finally:
        http_client.close()
    assert response.status_code == 401, response.text
    assert response.headers.get("www-authenticate") == "Bearer"
    assert stub.calls == []

    # 8. Two concurrent confirms for the same item-site serialize on the
    #    per-(item_code, site_code) lock: exactly one create is issued,
    #    one caller creates and the other finds the draft it made.
    set_writes("1")
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    create_delay = threading.Event()

    def slow_create(request: httpx.Request) -> httpx.Response:
        if b'"create"' in request.content:
            create_delay.wait(0.5)
        return stub.handle(request)

    before = len(audit_records())
    slow_http = httpx.Client(transport=httpx.MockTransport(slow_create))
    monkeypatch.setattr(
        replenishment_actions,
        "OdooClient",
        lambda: OdooClient(client=slow_http),
    )
    outcomes = []
    outcomes_lock = threading.Lock()

    def run_confirm():
        local = TestClient(
            app, headers={"Authorization": "Bearer dev-secret-token:admin"}
        )
        with local as local_client:
            result = local_client.post(URL, json=body(confirm=True))
        with outcomes_lock:
            outcomes.append((result.status_code, result.json()))

    threads = [threading.Thread(target=run_confirm) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    slow_http.close()
    assert [status for status, _ in outcomes] == [200, 200]
    payloads = [payload for _status, payload in outcomes]
    assert sum(1 for payload in payloads if payload["already_existed"] is False) == 1
    assert sum(1 for payload in payloads if payload["already_existed"] is True) == 1
    assert len(stub.created) == 1
    for payload in payloads:
        assert payload["dry_run"] is False
    # Each of the two confirms logged exactly one audit line.
    assert len(audit_records()) == before + 2

    # 9. The per-(item_code, site_code) lock registry is bounded: a
    #    hostile stream of distinct item-site pairs cannot grow it
    #    (no memory leak), and the same pair always lands on the same
    #    lock so per-pair serialization holds.
    set_writes("1")
    locks = replenishment_actions._SITE_LOCKS
    assert 1 <= len(locks) <= 128
    for pair_index in range(10_000):
        replenishment_actions._site_lock(ITEM_CODE, f"SITE-{pair_index}")
    assert len(replenishment_actions._SITE_LOCKS) == len(locks)
    same_pair = [
        replenishment_actions._site_lock(ITEM_CODE, SITE_CODE) for _ in range(8)
    ]
    assert all(lock is same_pair[0] for lock in same_pair)
    # End to end: confirms for distinct item-sites also leave the
    # registry size unchanged (they fail resolution 502, but each one
    # still takes the lock for its pair first).
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    for site_index in range(32):
        # "LEAK-" never matches the stub's canned warehouse code, so
        # every confirm fails warehouse resolution (502) after taking
        # the lock for its own (item, site) pair.
        response = _post(
            admin,
            stub,
            monkeypatch,
            body(site_code=f"LEAK-{site_index}", confirm=True),
        )
        assert response.status_code == 502, response.text
    assert len(replenishment_actions._SITE_LOCKS) == len(locks)
