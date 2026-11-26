"""Issue #467 proof: persistent audit of draft-PO confirms.

The Odoo server is fully mocked: the endpoint's ``OdooClient`` is built
over an ``httpx.MockTransport`` that answers every JSON-RPC model call
from canned records (the same stub shape the issue #451 proof uses), so
no network is touched and the real client code runs end to end.  The
endpoint and the audit route are exercised through the app's test
client authenticated by the usual stub user — the conftest ``client``
fixture carries the development bearer token, which is a NON-ADMIN
(role ``user``), so the whole proof runs as a non-admin and the
admin-only confirm goes out as the admin stub user
(``dev-secret-token:admin``, sub ``dev-admin-1``).

Proved here:

  * the migration — ``0017_draft_po_actions`` (revision after
    ``0016_scenarios``) creates ``draft_po_actions``
    with exactly the documented columns and nullability (bigint
    identity ``id`` primary key, timestamptz ``created_at`` not null
    defaulting to now(), String(255) ``actor_sub`` not null, text
    ``actor_name`` nullable, text item/site/supplier not null, text
    target_item/target_site/target_supplier nullable — added by
    ``0019_draft_po_actions_targets`` — double precision
    ``quantity``/``unit_price`` not null, text ``outcome`` not null
    with the created/already_existed/error check, integer ``odoo_id``
    nullable, text ``odoo_name``/``error_class`` nullable), and its
    downgrade drops the table and the upgrade back to head re-creates
    it cleanly;
  * a ``confirm=true`` whose client result has ``already_existed``
    false writes one ``created`` row — ``actor_sub`` equal to the
    stub user's subject, ``actor_name`` equal to ``display_name`` of
    that user, the request's item/site/supplier/quantity/unit_price,
    and the returned ``odoo_id``/``odoo_name``; a result with
    ``already_existed`` true writes one ``already_existed`` row;
  * a client raising ``OdooWritesDisabledError`` (409), an
    ``OdooError`` (502) and a ``ValueError`` (422) each write one
    ``error`` row with ``error_class`` equal to that exception's
    class name while the HTTP status and body are unchanged from
    before (the fixed 409 message, the fixed 502 detail, and the
    client's ``ValueError`` message), and the existing structured log
    line stays (one log line per confirm, created or error class);
  * a ``confirm=false`` dry run writes no row, for the non-admin stub
    user and the admin alike;
  * a signed-in non-admin's ``confirm=true`` is refused 403 before any
    Odoo call (zero upstream requests) and writes no audit row, while
    its ``confirm=false`` dry run still succeeds;
  * if the audit insert itself fails the request answers 500 — the
    draft PO already created stays a draft, and a retry is idempotent
    (it finds the first attempt's draft, ``already_existed`` true) and
    records its own row;
  * ``GET /api/replenishment/actions/draft-po/audit`` — on the same
    Bearer-gated router: 401 without a token and 403 for a signed-in
    non-admin — returns the rows newest first, ordered by
    ``created_at`` DESC then ``id`` DESC (two rows with equal
    ``created_at`` are listed ``id`` DESC), honours ``limit``,
    defaults to 50, and rejects ``limit`` 0 and 201 (and hostile
    values) with 422, never a 500;
  * no stored column contains the Odoo API key or any ``ODOO_*``
    value;
  * the identity requirement: the proof runs as a NON-ADMIN (role
    ``user``, asserted via ``GET /api/auth/me``), and a second pass
    after a full reload (a fresh ``TestClient`` booted the production
    way — the token the page would take from the URL hash as
    ``#access_token=...``, with ``GET /api/auth/config`` answering
    publicly and ``GET /api/auth/me`` resolving the viewer) sees the
    admin-only control refused with 403 (no row, no Odoo call) while
    the page still renders: the dry-run preview, the settings and the
    audit-free data the page loads all answer 200, and a request
    without a token still gets 401.
"""

import json
import logging
import os
from datetime import datetime
from urllib.parse import quote

import httpx
import psycopg2
import psycopg2.extras
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.api.replenishment_actions as replenishment_actions
from app.db.connection import get_connection
from app.feed.odoo_client import OdooClient
from app.main import app
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"
UID = 9

CONFIG_VARS = ("ODOO_BASE_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY")
CLEAN_VARS = (*CONFIG_VARS, "ODOO_CA_BUNDLE", "ODOO_TIMEOUT_SECONDS")

URL = "/api/replenishment/actions/draft-po"
AUDIT_URL = "/api/replenishment/actions/draft-po/audit"
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

ADMIN_TOKEN = "dev-secret-token:admin"
USER_TOKEN = "dev-secret-token:user"
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
ADMIN_NAME = "admin"  # display_name of the admin stub user
USER_SUB = "dev-user-1"  # the non-admin stub user's subject
USER_NAME = "developer"  # display_name of the non-admin stub user

PREV_REVISION = "0016_scenarios"
HEAD_REVISION = "0022_lead_time_promised_on"

AUDIT_COLUMNS = {
    "id",
    "created_at",
    "actor_sub",
    "actor_name",
    "item_code",
    "site_code",
    "supplier_code",
    "target_item_code",
    "target_site_code",
    "target_supplier_code",
    "quantity",
    "unit_price",
    "outcome",
    "odoo_id",
    "odoo_name",
    "error_class",
}

# column -> (data_type, character_maximum_length, is_nullable)
EXPECTED_SHAPE = {
    "id": ("bigint", None, "NO"),
    "created_at": ("timestamp with time zone", None, "NO"),
    "actor_sub": ("character varying", 255, "NO"),
    "actor_name": ("text", None, "YES"),
    "item_code": ("text", None, "NO"),
    "site_code": ("text", None, "NO"),
    "supplier_code": ("text", None, "NO"),
    "target_item_code": ("text", None, "YES"),
    "target_site_code": ("text", None, "YES"),
    "target_supplier_code": ("text", None, "YES"),
    "quantity": ("double precision", None, "NO"),
    "unit_price": ("double precision", None, "NO"),
    "outcome": ("text", None, "NO"),
    "odoo_id": ("integer", None, "YES"),
    "odoo_name": ("text", None, "YES"),
    "error_class": ("text", None, "YES"),
}


class _OdooStub:
    """MockTransport handler answering the Odoo JSON-RPC endpoint.

    Records every request as ``(service, method, args)``.  Answers
    ``common.authenticate`` with a fixed uid, answers each model's
    ``search_read`` from canned records by domain, answers
    ``purchase.order`` ``create`` with a fresh id, and fails the
    ``search_read`` of any model named in ``fail_models`` with a
    JSON-RPC error envelope.  Any other service, method, model, or
    domain shape fails loudly.
    """

    def __init__(
        self,
        products=(),
        partners=(),
        warehouses=(),
        draft_orders=(),
        order_lines=(),
        fail_models=(),
    ):
        self.products = list(products)
        self.partners = list(partners)
        self.warehouses = list(warehouses)
        self.draft_orders = list(draft_orders)
        self.order_lines = list(order_lines)
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


def _alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(os.path.join(_BACKEND_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_BACKEND_ROOT, "migrations"))
    return cfg


def _alembic_revision() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _table_exists() -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'draft_po_actions'"
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def _table_shape() -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'draft_po_actions' ORDER BY ordinal_position"
            )
            return {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}
    finally:
        conn.close()


def _primary_key_columns() -> list:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                "AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = 'draft_po_actions'::regclass "
                "AND i.indisprimary ORDER BY array_position(i.indkey, a.attnum)"
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _check_constraints() -> list:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'draft_po_actions'::regclass AND contype = 'c' "
                "ORDER BY conname"
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _created_at_default() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'draft_po_actions' AND column_name = 'created_at'"
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _stored_rows() -> list:
    """Every stored audit row, insertion order (created_at, id)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM draft_po_actions ORDER BY created_at, id")
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _stored_count() -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM draft_po_actions")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _db_order() -> list:
    """The ids in the documented order: created_at DESC, id DESC."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM draft_po_actions ORDER BY created_at DESC, id DESC"
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _seed_row(created_at: str, index: int) -> None:
    """One direct-seed row (the equal-timestamp ordering proof)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO draft_po_actions "
                "(created_at, actor_sub, actor_name, item_code, site_code, "
                "supplier_code, quantity, unit_price, outcome, odoo_id, "
                "odoo_name, error_class) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    created_at,
                    f"seed-{index}",
                    "Seed",
                    "SEED-ITEM",
                    "SEED-SITE",
                    "SEED-VENDOR",
                    1.0,
                    1.0,
                    "created",
                    None,
                    None,
                    None,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def test_issue467_freeform(monkeypatch, client, caplog):
    for var in (*CLEAN_VARS, "ODOO_ALLOW_INSECURE", "ODOO_ALLOW_WRITES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ODOO_BASE_URL", BASE_URL)
    monkeypatch.setenv("ODOO_DB", DB)
    monkeypatch.setenv("ODOO_LOGIN", LOGIN)
    monkeypatch.setenv("ODOO_API_KEY", API_KEY)

    # The confirm is admin-only: confirms go out as the admin stub
    # user, dry runs and the identity passes use the non-admin stubs.
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    user = TestClient(app, headers={"Authorization": f"Bearer {USER_TOKEN}"})
    anon = TestClient(app)

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

    def post(api_client, stub, payload, raw=None):
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
            return api_client.post(URL, json=payload)
        finally:
            http_client.close()

    # -- Identity: the proof runs as a NON-ADMIN. ------------------------
    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["sub"] == USER_SUB
    assert me.json()["user"]["roles"] == ["user"]
    assert me.json()["user"].get("groups") == []

    # -- Migration: 0017 is the head and the table has the documented
    #    shape; the downgrade drops it and the upgrade back re-creates
    #    it cleanly. ------------------------------------------------------
    assert _alembic_revision() == CURRENT_HEAD
    assert _table_exists()
    assert _primary_key_columns() == ["id"]
    assert _table_shape() == EXPECTED_SHAPE
    assert _created_at_default() == "now()"
    assert "ck_draft_po_actions_outcome" in _check_constraints()

    cfg = _alembic_config()
    try:
        alembic_command.downgrade(cfg, PREV_REVISION)
        assert _alembic_revision() == PREV_REVISION
        assert not _table_exists()
    finally:
        alembic_command.upgrade(cfg, "head")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert heads[0] == CURRENT_HEAD
    assert _alembic_revision() == CURRENT_HEAD
    assert _table_exists()

    # The session fixture (and the in-test downgrade/upgrade above)
    # runs alembic, whose env calls ``fileConfig(alembic.ini)``: the
    # disable_existing_loggers default turns off every logger that
    # already exists — including this one — and the root logger's
    # handlers are replaced, dropping the capture handler pytest
    # attached.  Restore both after the migration work so the
    # structured-log assertions observe what the endpoint logs.
    monkeypatch.setattr(logging.getLogger(AUDIT_LOGGER), "disabled", False)
    if caplog.handler not in logging.getLogger().handlers:
        logging.getLogger().addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)

    def audit_records():
        return [record for record in caplog.records if record.name == AUDIT_LOGGER]

    # -- confirm=true, already_existed false: one ``created`` row with
    #    the actor, the request's fields, and the returned odoo id/name;
    #    the response body is unchanged and the structured log line
    #    stays. -----------------------------------------------------------
    set_writes("1")
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    before_count = _stored_count()
    before_logs = len(audit_records())
    response = post(admin, stub, body(confirm=True))
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
    assert len(stub.created) == 1
    assert _stored_count() == before_count + 1
    rows = _stored_rows()[before_count:]
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == AUDIT_COLUMNS
    assert row["actor_sub"] == ADMIN_SUB
    assert row["actor_name"] == ADMIN_NAME
    assert row["item_code"] == ITEM_CODE
    assert row["site_code"] == SITE_CODE
    assert row["supplier_code"] == SUPPLIER_CODE
    assert row["quantity"] == 12.0
    assert row["unit_price"] == 4.25
    assert row["outcome"] == "created"
    assert row["odoo_id"] == 701
    assert row["odoo_name"] == "PO/2026/0701"
    assert row["error_class"] is None
    assert isinstance(row["created_at"], datetime)
    assert (
        row["created_at"].tzinfo is not None
    ), f"created_at not timezone-aware: {row['created_at']}"
    # The existing structured log line stays: exactly one new audit
    # line, with the outcome, and no secret in it.
    records = audit_records()[before_logs:]
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
    for secret in (API_KEY, LOGIN, BASE_URL, DB, ADMIN_TOKEN):
        assert secret not in message, f"{secret!r} leaked into {message!r}"

    # -- confirm=true, already_existed true: one ``already_existed``
    #    row with the existing order's id/name. ---------------------------
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
        order_lines=[
            {
                "order_id": 42,
                "product_id": PRODUCT_ID,
                "product_qty": 12,
                "partner_id": PARTNER_ID,
            }
        ],
    )
    before_count = _stored_count()
    response = post(admin, stub, body(confirm=True))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "dry_run": False,
        "odoo_id": 42,
        "name": "PO/2026/0042",
        "state": "draft",
        "already_existed": True,
        "target": {
            "item_code": ITEM_CODE,
            "site_code": SITE_CODE,
            "supplier_code": SUPPLIER_CODE,
        },
    }
    assert stub.created == []
    rows = _stored_rows()[before_count:]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "already_existed"
    assert rows[0]["odoo_id"] == 42
    assert rows[0]["odoo_name"] == "PO/2026/0042"
    assert rows[0]["error_class"] is None
    assert rows[0]["actor_sub"] == ADMIN_SUB

    # -- confirm=true, client errors: one ``error`` row per path with
    #    error_class = the exception's class name; status and body are
    #    unchanged. --------------------------------------------------------
    # OdooWritesDisabledError -> 409, no upstream call at all.
    set_writes(None)
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    before_count = _stored_count()
    before_logs = len(audit_records())
    response = post(admin, stub, body(confirm=True))
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "Odoo writes are disabled"}
    assert stub.calls == []
    rows = _stored_rows()[before_count:]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["error_class"] == "OdooWritesDisabledError"
    assert rows[0]["odoo_id"] is None
    assert rows[0]["odoo_name"] is None
    message = audit_records()[before_logs:][0].getMessage()
    assert "outcome=error" in message
    assert "error=OdooWritesDisabledError" in message

    # OdooError (a resolution failure) -> 502 with the fixed detail.
    set_writes("1")
    stub = _OdooStub(partners=[PARTNER], warehouses=[WAREHOUSE])  # no product
    before_count = _stored_count()
    before_logs = len(audit_records())
    response = post(admin, stub, body(confirm=True))
    assert response.status_code == 502, response.text
    assert response.json() == {
        "detail": f"product resolution failed: item '{ITEM_CODE}' has no Odoo mapping"
    }
    rows = _stored_rows()[before_count:]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["error_class"] == "OdooError"
    message = audit_records()[before_logs:][0].getMessage()
    assert "error=OdooError" in message

    # ValueError (a non-finite quantity smuggled in as raw JSON) -> 422
    # with the client's message as the body detail.
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    before_count = _stored_count()
    before_logs = len(audit_records())
    raw = (
        b'{"item_code":"WIDGET 1","site_code":"WH-1","supplier_code":"VENDOR-9",'
        b'"quantity":Infinity,"unit_price":1.0,"confirm":true}'
    )
    response = post(admin, stub, None, raw=raw)
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "quantity must be a positive finite number"}
    rows = _stored_rows()[before_count:]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["error_class"] == "ValueError"
    message = audit_records()[before_logs:][0].getMessage()
    assert "error=ValueError" in message

    # -- confirm=false dry runs write no row (non-admin and admin). ------
    set_writes("0")
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    before_count = _stored_count()
    response = post(client, stub, body())
    assert response.status_code == 200, response.text
    assert response.json()["dry_run"] is True
    assert _stored_count() == before_count
    response = post(admin, stub, body(confirm=False))
    assert response.status_code == 200, response.text
    assert response.json()["dry_run"] is True
    assert _stored_count() == before_count

    # -- Admin-only confirm: a signed-in non-admin's confirm=true is a
    #    403 before any Odoo call, with no audit row; its dry run still
    #    succeeds. ---------------------------------------------------------
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    before_count = _stored_count()
    response = post(user, stub, body(confirm=True))
    assert response.status_code == 403, response.text
    assert response.json() == {
        "detail": "Role 'admin' is required to perform this action."
    }
    assert stub.calls == []
    assert _stored_count() == before_count
    set_writes("1")
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    response = post(user, stub, body(confirm=False))
    assert response.status_code == 200, response.text
    assert response.json()["dry_run"] is True
    assert _stored_count() == before_count

    # -- GET audit: Bearer-gated, ADMIN-ONLY, newest first, honours
    #    limit, rejects 0/201 with 422 (never a 500). ----------------------
    set_writes("1")
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])

    response = anon.get(AUDIT_URL)
    assert response.status_code == 401, response.text
    assert response.headers.get("www-authenticate") == "Bearer"
    response = user.get(AUDIT_URL)
    assert response.status_code == 403, response.text
    assert response.json() == {
        "detail": "Role 'admin' is required to perform this action."
    }
    for bad_limit in ("0", "201", "-5", "999999999999", "abc"):
        response = admin.get(AUDIT_URL, params={"limit": bad_limit})
        assert response.status_code == 422, (bad_limit, response.text)

    expected_ids = _db_order()
    assert len(expected_ids) >= 4  # the five confirms above, minus none
    response = admin.get(AUDIT_URL)
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == len(expected_ids)
    assert [row["id"] for row in rows] == expected_ids
    for row in rows:
        assert set(row) == AUDIT_COLUMNS
        datetime.fromisoformat(row["created_at"])  # ISO-8601, parseable
    # limit honoured: the newest three only.
    response = admin.get(AUDIT_URL, params={"limit": 3})
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == expected_ids[:3]
    response = admin.get(AUDIT_URL, params={"limit": 1})
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == expected_ids[:1]

    # -- Default limit 50, and equal created_at ordered id DESC. ----------
    # 60 direct seeds: 58 distinct minutes plus two rows sharing one
    # timestamp; all earlier than the rows the confirms stored above.
    tied_stamp = "2026-10-01T00:58:00+00:00"
    for index in range(58):
        _seed_row(f"2026-10-01T00:{index:02d}:00+00:00", index)
    _seed_row(tied_stamp, 58)
    _seed_row(tied_stamp, 59)
    expected_ids = _db_order()
    assert len(expected_ids) == 60 + 5

    # Default limit (no parameter) answers the newest 50 of them.
    response = admin.get(AUDIT_URL)
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 50
    assert [row["id"] for row in rows] == expected_ids[:50]
    # limit 200 (the bound) answers every row, newest first.
    response = admin.get(AUDIT_URL, params={"limit": 200})
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 65
    assert [row["id"] for row in rows] == expected_ids
    # The two equal-timestamp rows sit in the response id DESC.
    tied = [row for row in rows if row["created_at"] == tied_stamp]
    assert len(tied) == 2
    assert tied[0]["id"] > tied[1]["id"]

    # -- No stored column carries the Odoo API key or any ODOO_* value. ---
    odoo_values = (BASE_URL, DB, LOGIN, API_KEY)
    for row in _stored_rows():
        for value in row.values():
            if value is None:
                continue
            text = value if isinstance(value, str) else str(value)
            for secret in (*odoo_values, ADMIN_TOKEN, "dev-secret-token"):
                assert secret not in text, f"{secret!r} stored in {row['id']!r}"

    # -- If the audit insert itself fails the request is 500; the draft
    #    PO already created stays a draft, and a retry is idempotent and
    #    records its own row. ----------------------------------------------
    real_create = replenishment_actions._actions_repo.create
    fail = [True]

    def flaky_create(**kwargs):
        if fail[0]:
            raise RuntimeError("audit insert failed")
        return real_create(**kwargs)

    monkeypatch.setattr(replenishment_actions._actions_repo, "create", flaky_create)
    set_writes("1")
    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    before_count = _stored_count()
    # A client that answers the 500 instead of re-raising it.
    flaky = TestClient(
        app,
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        raise_server_exceptions=False,
    )
    response = post(flaky, stub, body(confirm=True))
    assert response.status_code == 500, response.text
    # The Odoo create happened (the draft exists) but no audit row.
    assert len(stub.created) == 1
    assert stub.created[0]["state"] == "draft"
    assert _stored_count() == before_count
    # The retry finds the first attempt's draft and records its row.
    fail[0] = False
    response = post(flaky, stub, body(confirm=True))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "dry_run": False,
        "odoo_id": stub.created[0]["id"],
        "name": stub.created[0]["name"],
        "state": "draft",
        "already_existed": True,
        "target": {
            "item_code": ITEM_CODE,
            "site_code": SITE_CODE,
            "supplier_code": SUPPLIER_CODE,
        },
    }
    assert len(stub.created) == 1  # no second create
    rows = _stored_rows()[before_count:]
    assert len(rows) == 1
    assert rows[0]["item_code"] == ITEM_CODE
    assert rows[0]["outcome"] == "already_existed"
    assert rows[0]["odoo_id"] == stub.created[0]["id"]
    assert rows[0]["odoo_name"] == stub.created[0]["name"]

    # -- Identity, second pass after a full reload: the page boots the
    #    production way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the admin-only control is
    #    refused 403 (no row, no Odoo call) while the page still
    #    renders for the viewer, and no token is still a 401. -------------
    viewer = TestClient(app, headers={"Authorization": "Bearer dev-secret-token"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["sub"] == USER_SUB
    assert me.json()["user"]["roles"] == ["user"]
    assert "admin" not in me.json()["user"]["roles"]
    assert "admin" not in me.json()["user"]["groups"]

    stub = _OdooStub(products=[PRODUCT], partners=[PARTNER], warehouses=[WAREHOUSE])
    before_count = _stored_count()
    set_writes("1")
    response = post(viewer, stub, body(confirm=True))
    assert response.status_code == 403, response.text
    assert response.json() == {
        "detail": "Role 'admin' is required to perform this action."
    }
    assert stub.calls == []
    assert _stored_count() == before_count
    # The page still renders for the viewer: the dry-run preview and the
    # data the page loads both answer 200.
    response = post(viewer, stub, body(confirm=False))
    assert response.status_code == 200, response.text
    assert response.json()["dry_run"] is True
    assert viewer.get("/api/settings").status_code == 200
    # The audit list is the admin-only control: 403 for the viewer, and
    # 401 without a token (the route is Bearer-gated like the others).
    assert viewer.get(AUDIT_URL).status_code == 403
    response = anon_reloaded.get(AUDIT_URL)
    assert response.status_code == 401, response.text
    assert response.headers.get("www-authenticate") == "Bearer"
    assert _stored_count() == before_count
