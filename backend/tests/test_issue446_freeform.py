"""Issue #446 proof: the per-item-site PostgreSQL advisory lock for
draft-PO confirms.

The confirm path of
``POST /api/replenishment/actions/draft-po`` now serializes, in
addition to its in-process per-pair lock, on a session-level
PostgreSQL advisory lock (``pg_advisory_lock`` on the two-key pair
``(DRAFT_PO_LOCK_CLASS, draft_po_lock_key(item_code, site_code))``)
taken on its own connection for the duration of the Odoo create call,
so the "one confirm at a time per item-site" guarantee holds across
several API worker processes, not just one.  The Odoo server is fully
mocked exactly as in the issue #451 proof: the endpoint's
``OdooClient`` is built over an ``httpx.MockTransport`` answering every
JSON-RPC model call from canned records, the codes are left unmapped
(the M1 translation passes them through), the write switch
(``ODOO_ALLOW_WRITES``) is toggled per phase, and every confirm goes
out as the development-mode admin token.  The test runs against the
real test database (the conftest session fixture), where it holds and
probes advisory locks on its own connections.

Proved here:

  * the key: ``draft_po_lock_key("ITEM", "SITE")`` equals the value
    computed directly from the NUL-joined codes' SHA-256 (first four
    bytes, big-endian signed 32-bit), lies within the signed 32-bit
    range, and differs between ("ITEM", "S1") and ("ITEM", "S2");
  * cross-session serialization: with the test holding
    ``pg_advisory_lock(DRAFT_PO_LOCK_CLASS, key)`` for the target
    item-site on its own connection, a confirm started in a thread is
    still running 0.5 s later and the stub has seen no ``create``
    call; after the test unlocks, the confirm answers 200 with
    ``already_existed`` false and exactly one draft was created;
  * timeout: with ``DRAFT_PO_LOCK_TIMEOUT_MS`` monkeypatched to 200
    and the lock held by the test's connection, a confirm answers 409
    with the fixed "Another draft order for this item-site is in
    progress" — the stub sees no ``create`` and the
    ``draft_po_actions`` row count is unchanged;
  * release: after a successful confirm, and after a confirm that
    answers 502 (an unresolvable item), the test's own
    ``pg_try_advisory_lock(DRAFT_PO_LOCK_CLASS, key)`` succeeds at
    once;
  * a failed lock acquisition leaks no connection: when
    ``SET lock_timeout`` fails, the context manager closes its
    connection and propagates the original error — the block never
    runs, and no DraftPoLockTimeout replaces the error.
"""

import hashlib
import json
import threading
import time
from urllib.parse import quote

import httpx
import psycopg2
from fastapi.testclient import TestClient

import app.api.replenishment_actions as replenishment_actions
from app.db.connection import get_connection
from app.feed.odoo_client import OdooClient
from app.main import app
from app.repositories import draft_po_lock
from app.repositories.draft_po_lock import (
    DRAFT_PO_LOCK_CLASS,
    draft_po_lock_key,
)

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
PRODUCT = {"id": PRODUCT_ID, "default_code": ITEM_CODE}
PARTNER = {"id": PARTNER_ID, "ref": SUPPLIER_CODE, "name": "Vendor Nine"}
WAREHOUSE = {"id": 201, "code": SITE_CODE, "in_type_id": PICKING_TYPE_ID}
DEFAULT_ORIGIN = (
    "AIERP:replenishment:" + quote(ITEM_CODE, safe="") + ":" + quote(SITE_CODE, safe="")
)
assert DEFAULT_ORIGIN == "AIERP:replenishment:WIDGET%201:WH-1"
# The advisory-lock key for the confirm's (unmapped) target pair.
LOCK_KEY = draft_po_lock_key(ITEM_CODE, SITE_CODE)
LOCK_TIMEOUT_DETAIL = "Another draft order for this item-site is in progress"


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


def test_issue446_freeform(monkeypatch, client):
    for var in (*CLEAN_VARS, "ODOO_ALLOW_INSECURE", "ODOO_ALLOW_WRITES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ODOO_BASE_URL", BASE_URL)
    monkeypatch.setenv("ODOO_DB", DB)
    monkeypatch.setenv("ODOO_LOGIN", LOGIN)
    monkeypatch.setenv("ODOO_API_KEY", API_KEY)

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

    def set_writes(value):
        if value is None:
            monkeypatch.delenv("ODOO_ALLOW_WRITES", raising=False)
        else:
            monkeypatch.setenv("ODOO_ALLOW_WRITES", value)

    def post(stub):
        """POST a confirm with the router's ``OdooClient`` over ``stub``."""
        http_client = httpx.Client(transport=httpx.MockTransport(stub.handle))
        monkeypatch.setattr(
            replenishment_actions,
            "OdooClient",
            lambda: OdooClient(client=http_client),
        )
        try:
            return admin.post(URL, json=body(confirm=True))
        finally:
            http_client.close()

    def create_calls(stub):
        return [
            args
            for service, method, args in stub.calls
            if service == "object" and args[4] == "create"
        ]

    def hold_lock():
        """Take the target item-site's advisory lock on a fresh connection."""
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_lock(%s, %s)", (DRAFT_PO_LOCK_CLASS, LOCK_KEY)
            )
        return conn

    def release_lock(conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_unlock(%s, %s)", (DRAFT_PO_LOCK_CLASS, LOCK_KEY)
            )
        conn.close()

    def lock_is_free():
        """The test's own pg_try_advisory_lock, on a fresh connection."""
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(%s, %s)",
                    (DRAFT_PO_LOCK_CLASS, LOCK_KEY),
                )
                acquired = bool(cur.fetchone()[0])
            if acquired:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_unlock(%s, %s)",
                        (DRAFT_PO_LOCK_CLASS, LOCK_KEY),
                    )
            return acquired
        finally:
            conn.close()

    def action_rows():
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM draft_po_actions")
                return cur.fetchone()[0]
        finally:
            conn.close()

    # 1. The key: the value the test computes itself from sha256 the
    #    way the criterion states, the signed 32-bit range, and a
    #    different key for a different site.
    def expected_key(item_code, site_code):
        digest = hashlib.sha256((item_code + "\x00" + site_code).encode()).digest()
        return int.from_bytes(digest[:4], byteorder="big", signed=True)

    assert draft_po_lock_key("ITEM", "SITE") == expected_key("ITEM", "SITE")
    key = draft_po_lock_key("ITEM", "SITE")
    assert -(2**31) <= key <= 2**31 - 1
    assert draft_po_lock_key("ITEM", "S1") != draft_po_lock_key("ITEM", "S2")

    set_writes("1")

    # 2. Cross-session serialization: the test holds the target
    #    item-site's advisory lock on its own connection; a confirm
    #    started in a thread blocks on it — still running after 0.5 s,
    #    no create seen by the stub.  Once the test unlocks, the
    #    confirm proceeds: 200, already_existed false, exactly one
    #    draft created.
    holder = hold_lock()
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    http_client = httpx.Client(transport=httpx.MockTransport(stub.handle))
    monkeypatch.setattr(
        replenishment_actions,
        "OdooClient",
        lambda: OdooClient(client=http_client),
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
            outcomes.append((result.status_code, result))

    thread = threading.Thread(target=run_confirm)
    thread.start()
    time.sleep(0.5)
    assert thread.is_alive(), "the confirm should still be blocked on the lock"
    assert stub.created == []
    assert create_calls(stub) == []
    release_lock(holder)
    thread.join(timeout=30)
    assert not thread.is_alive(), "the confirm did not finish after the unlock"
    http_client.close()
    assert len(outcomes) == 1
    status_code, response = outcomes[0]
    assert status_code == 200, response.text
    payload = response.json()
    assert payload["dry_run"] is False
    assert payload["already_existed"] is False
    assert len(stub.created) == 1
    assert payload["odoo_id"] == stub.created[0]["id"]

    # 3. Timeout: with the wait shortened to 200 ms and the lock held
    #    by the test's connection, the confirm is refused 409 with the
    #    fixed message — the stub sees no create and no
    #    draft_po_actions row is written.
    monkeypatch.setattr(draft_po_lock, "DRAFT_PO_LOCK_TIMEOUT_MS", 200)
    holder = hold_lock()
    before = action_rows()
    try:
        stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
        response = post(stub)
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": LOCK_TIMEOUT_DETAIL}
        assert stub.created == []
        assert create_calls(stub) == []
        assert action_rows() == before
    finally:
        release_lock(holder)

    # 4. Release: after a successful confirm, and after a confirm that
    #    answers 502 (an unresolvable item), the lock is free — the
    #    test's own pg_try_advisory_lock succeeds at once.
    monkeypatch.setattr(draft_po_lock, "DRAFT_PO_LOCK_TIMEOUT_MS", 60000)
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    response = post(stub)
    assert response.status_code == 200, response.text
    assert response.json()["already_existed"] is False
    assert len(stub.created) == 1
    assert lock_is_free()

    stub = _OdooStub(products=[], partners=[PARTNER], warehouses=[WAREHOUSE])
    response = post(stub)
    assert response.status_code == 502, response.text
    assert response.json() == {
        "detail": f"product resolution failed: item '{ITEM_CODE}' has no Odoo mapping"
    }
    assert stub.created == []
    assert lock_is_free()

    # 5. A failed lock acquisition leaks no connection: when
    #    ``SET lock_timeout`` fails, the context manager closes its
    #    connection and propagates the original error (never a
    #    DraftPoLockTimeout, never swallowed), and the block never
    #    runs.
    closed = []

    class LeakCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def execute(self, sql, params=None):
            if sql.startswith("SET lock_timeout"):
                raise psycopg2.OperationalError("lock_timeout rejected")
            raise AssertionError(f"unexpected SQL {sql!r}")

    class LeakConnection:
        def cursor(self):
            return LeakCursor()

        def close(self):
            closed.append(1)

    monkeypatch.setattr(draft_po_lock, "get_connection", lambda: LeakConnection())
    try:
        with draft_po_lock.hold_draft_po_lock("LEAK-ITEM", "LEAK-SITE"):
            raise AssertionError("the block must not run on a failed acquisition")
    except psycopg2.OperationalError:
        pass
    else:
        raise AssertionError("the SET lock_timeout failure must propagate")
    assert closed == [1]
