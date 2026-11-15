"""Issue #469 proof: draft-PO routes translate codes to Odoo targets.

The Odoo client is stubbed at the module level — a stand-in class
records the exact ``item_code`` / ``site_code`` / ``supplier_code`` it
is called with and answers (or fails) from canned behaviors, so the
real route code runs end to end without any network.  The endpoint is
exercised through the app's test client authenticated by the usual
stub users: the conftest ``client`` fixture carries the development
bearer token, which is a NON-ADMIN (role ``user``), and the admin-only
confirms go out as the admin stub user (``dev-secret-token:admin``,
sub ``dev-admin-1``).  Mappings are stored for target system ``odoo``
directly through the M1 repository
(``ErpCodeMappingPostgresRepository``).

Proved here:

  * the migration — ``0019_draft_po_actions_targets`` (revision after
    ``0018_erp_code_mappings``) is the head and adds nullable text
    ``target_item_code`` / ``target_site_code`` / ``target_supplier_code``
    to ``draft_po_actions``; its downgrade drops the three columns and
    the upgrade back to head re-adds them cleanly;
  * with item, site and vendor mappings stored for target ``odoo``, a
    dry-run and a confirm each call the client with the three TARGET
    codes (and nothing else) and return them under ``target`` — the
    origin tag follows the target codes too;
  * with no mappings the client receives the request's codes
    unchanged and ``target`` echoes them;
  * a mix (item mapped, site and vendor unmapped) translates only the
    item;
  * two different source items mapped to the same Odoo item at the
    same Odoo site call the client with the same target codes — one
    open draft, ``already_existed`` on the second confirm;
  * an unmapped item whose product resolution fails answers 502 with
    the detail naming the request's code and "has no Odoo mapping",
    and a mapped item answers 502 with the detail naming the TARGET
    code and "mapped item … not found in Odoo" — the same wording for
    vendor (supplier) and warehouse (site); the details are fixed
    strings, so no credential or Odoo payload ever appears in them;
  * the 409 (writes disabled) and 422 (client ValueError) paths are
    unchanged in status, body and audit row;
  * a confirm audit row records both the request's codes and the
    ``target_*`` codes; a dry-run writes no audit row;
  * a NUL-containing code answers 422, never a 500, with no client
    call and no audit row.
"""

import os
from urllib.parse import quote

import psycopg2
import psycopg2.extras
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.api.replenishment_actions as replenishment_actions
from app.db.connection import get_connection
from app.feed.odoo_client import OdooError, OdooWritesDisabledError
from app.main import app
from app.repositories.erp_code_mappings_postgres_repository import (
    ErpCodeMappingPostgresRepository,
)
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

URL = "/api/replenishment/actions/draft-po"
AUDIT_URL = "/api/replenishment/actions/draft-po/audit"

ADMIN_TOKEN = "dev-secret-token:admin"
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject

PREV_HEAD = "0018_erp_code_mappings"
HEAD = "0022_lead_time_promised_on"
TARGET_COLUMNS = ("target_item_code", "target_site_code", "target_supplier_code")

# Part 1: everything mapped.
SRC_ITEM, TGT_ITEM = "SRC-ITEM", "ODOO-1001"
SRC_SITE, TGT_SITE = "SRC-WH", "ODOO-WH-7"
SRC_VENDOR, TGT_VENDOR = "SRC-VND", "ODOO-V-9"
# Part 2: nothing mapped.
UNM_ITEM, UNM_SITE, UNM_VENDOR = "U-ITEM", "U-WH", "U-VND"
# Part 3: only the item mapped.
MIX_ITEM, TGT_MIX_ITEM = "M-ITEM", "ODOO-M-1"
MIX_SITE, MIX_VENDOR = "M-WH", "M-VND"
# Part 4: two source items onto one Odoo item/site.
DUP_ITEM_A, DUP_ITEM_B = "DUP-A", "DUP-B"
DUP_SITE_A, DUP_SITE_B = "DUP-WH-A", "DUP-WH-B"
DUP_VENDOR, TGT_DUP_VENDOR = "DUP-VND", "ODOO-D-V"
TGT_DUP_ITEM, TGT_DUP_SITE = "ODOO-D-1", "ODOO-D-WH"
# Part 5: resolution failures (F* = the failing request code).
F1_ITEM = "F1-ITEM"
F2_ITEM, TGT_F2_ITEM = "F2-ITEM", "ODOO-F2"
F3_VENDOR = "F3-VND"
F4_VENDOR, TGT_F4_VENDOR = "F4-VND", "ODOO-F4"
F5_SITE = "F5-WH"
F6_SITE, TGT_F6_SITE = "F6-WH", "ODOO-F6"

RESOLVED = {"product_id": 11, "partner_id": 501, "picking_type_id": 301}
SECRET = "s3cret-odoo-api-key"  # must never appear in a 502 detail


def _default_origin(item_code: str, site_code: str) -> str:
    return (
        "AIERP:replenishment:"
        + quote(item_code, safe="")
        + ":"
        + quote(site_code, safe="")
    )


def _ok_preview(**kwargs):
    """A canned preview answer built from the codes it was called with."""
    return {
        "resolved": RESOLVED,
        "origin": _default_origin(kwargs["item_code"], kwargs["site_code"]),
        "existing": None,
    }


def _ok_create(**kwargs):
    return {
        "odoo_id": 701,
        "name": "PO/2026/0701",
        "state": "draft",
        "already_existed": False,
    }


def _already_existed(**kwargs):
    """A canned confirm answer: the draft already carries the origin."""
    return {
        "odoo_id": 701,
        "name": "PO/2026/0701",
        "state": "draft",
        "already_existed": True,
    }


def _fail_product(**kwargs):
    # The message names the code the client was actually called with
    # (plus a secret that must never leak into the 502 detail).
    raise OdooError(
        f"no product matches item code '{kwargs['item_code']}' (token={SECRET})"
    )


def _fail_vendor(**kwargs):
    raise OdooError(f"no vendor matches supplier code '{kwargs['supplier_code']}'")


def _fail_warehouse(**kwargs):
    raise OdooError(f"no warehouse matches site code '{kwargs['site_code']}'")


class _StubClient:
    """A stand-in OdooClient recording the codes it is called with."""

    def __init__(self, preview=_ok_preview, create=_ok_create):
        self._preview = preview
        self._create = create
        self.calls = []
        self.closes = 0

    def preview_draft_purchase_order(self, **kwargs):
        self.calls.append(
            (
                "preview",
                kwargs["item_code"],
                kwargs["site_code"],
                kwargs["supplier_code"],
            )
        )
        return self._preview(**kwargs)

    def create_draft_purchase_order(self, **kwargs):
        self.calls.append(
            (
                "create",
                kwargs["item_code"],
                kwargs["site_code"],
                kwargs["supplier_code"],
            )
        )
        return self._create(**kwargs)

    def close(self):
        self.closes += 1


def _install(monkeypatch, preview=_ok_preview, create=_ok_create) -> _StubClient:
    stub = _StubClient(preview=preview, create=create)
    monkeypatch.setattr(replenishment_actions, "OdooClient", lambda: stub)
    return stub


def _payload(item, site, vendor, **overrides):
    payload = {
        "item_code": item,
        "site_code": site,
        "supplier_code": vendor,
        "quantity": 5,
        "unit_price": 2.5,
    }
    payload.update(overrides)
    return payload


def _target(item, site, vendor):
    return {"item_code": item, "site_code": site, "supplier_code": vendor}


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


def _draft_po_columns() -> dict:
    """draft_po_actions columns as (data_type, is_nullable)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'draft_po_actions'"
            )
            return {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    finally:
        conn.close()


def _audit_rows() -> list:
    """Every stored audit row, insertion order (created_at, id)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM draft_po_actions ORDER BY created_at, id")
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _assert_null_422(response, field: str) -> None:
    """A NUL-containing code is a 422 naming the field, never a 500."""
    assert response.status_code == 422, response.text
    body = response.json()
    assert isinstance(body["detail"], list)
    messages = [error["msg"] for error in body["detail"]]
    assert any(
        f"{field} must not contain NUL bytes" in message for message in messages
    ), messages


def test_issue469_freeform(monkeypatch, client):
    repo = ErpCodeMappingPostgresRepository()
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    def reset():
        """Drop every mapping and audit row the test has written."""
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE erp_code_mappings, draft_po_actions")
            conn.commit()
        finally:
            conn.close()

    def store(kind, source, target):
        repo.upsert(
            kind=kind, target_system="odoo", source_code=source, target_code=target
        )

    # -- AC-1: all three mapped: the client is called with the TARGET
    #    codes only, and both responses carry them under `target`. -----
    reset()
    store("item", SRC_ITEM, TGT_ITEM)
    store("site", SRC_SITE, TGT_SITE)
    store("vendor", SRC_VENDOR, TGT_VENDOR)

    stub = _install(monkeypatch)
    response = client.post(URL, json=_payload(SRC_ITEM, SRC_SITE, SRC_VENDOR))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "dry_run": True,
        "would_create": True,
        "origin": _default_origin(TGT_ITEM, TGT_SITE),
        "resolved": RESOLVED,
        "existing": None,
        "target": _target(TGT_ITEM, TGT_SITE, TGT_VENDOR),
    }
    assert stub.calls == [("preview", TGT_ITEM, TGT_SITE, TGT_VENDOR)]
    # A dry run writes no audit row.
    assert _audit_rows() == []

    stub = _install(monkeypatch)
    response = admin.post(
        URL, json=_payload(SRC_ITEM, SRC_SITE, SRC_VENDOR, confirm=True)
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "dry_run": False,
        "odoo_id": 701,
        "name": "PO/2026/0701",
        "state": "draft",
        "already_existed": False,
        "target": _target(TGT_ITEM, TGT_SITE, TGT_VENDOR),
    }
    assert stub.calls == [("create", TGT_ITEM, TGT_SITE, TGT_VENDOR)]
    assert stub.closes == 1
    rows = _audit_rows()
    assert len(rows) == 1
    row = rows[0]
    # The row records the request's codes AND the translated codes.
    assert row["actor_sub"] == ADMIN_SUB
    assert row["item_code"] == SRC_ITEM
    assert row["site_code"] == SRC_SITE
    assert row["supplier_code"] == SRC_VENDOR
    assert row["target_item_code"] == TGT_ITEM
    assert row["target_site_code"] == TGT_SITE
    assert row["target_supplier_code"] == TGT_VENDOR
    assert row["quantity"] == 5.0
    assert row["unit_price"] == 2.5
    assert row["outcome"] == "created"
    assert row["odoo_id"] == 701
    assert row["odoo_name"] == "PO/2026/0701"
    assert row["error_class"] is None
    # The audit listing serves the target codes alongside the request's.
    response = admin.get(AUDIT_URL)
    assert response.status_code == 200, response.text
    listed = response.json()
    assert len(listed) == 1
    assert listed[0]["item_code"] == SRC_ITEM
    assert listed[0]["target_item_code"] == TGT_ITEM
    assert listed[0]["target_site_code"] == TGT_SITE
    assert listed[0]["target_supplier_code"] == TGT_VENDOR

    # -- AC-1: no mappings: the client receives the request's codes
    #    unchanged and `target` echoes them. ----------------------------
    reset()
    stub = _install(monkeypatch)
    response = client.post(URL, json=_payload(UNM_ITEM, UNM_SITE, UNM_VENDOR))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["dry_run"] is True
    assert data["would_create"] is True
    assert data["origin"] == _default_origin(UNM_ITEM, UNM_SITE)
    assert data["target"] == _target(UNM_ITEM, UNM_SITE, UNM_VENDOR)
    assert stub.calls == [("preview", UNM_ITEM, UNM_SITE, UNM_VENDOR)]
    assert _audit_rows() == []

    stub = _install(monkeypatch)
    response = admin.post(
        URL, json=_payload(UNM_ITEM, UNM_SITE, UNM_VENDOR, confirm=True)
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["dry_run"] is False
    assert data["target"] == _target(UNM_ITEM, UNM_SITE, UNM_VENDOR)
    assert stub.calls == [("create", UNM_ITEM, UNM_SITE, UNM_VENDOR)]
    rows = _audit_rows()
    assert len(rows) == 1
    row = rows[0]
    # Unmapped: the recorded target codes are the request's codes.
    assert row["item_code"] == UNM_ITEM
    assert row["target_item_code"] == UNM_ITEM
    assert row["target_site_code"] == UNM_SITE
    assert row["target_supplier_code"] == UNM_VENDOR
    assert row["outcome"] == "created"

    # -- AC-1: a mix — item mapped, site and vendor unmapped: only the
    #    item is translated. ---------------------------------------------
    reset()
    store("item", MIX_ITEM, TGT_MIX_ITEM)
    stub = _install(monkeypatch)
    response = client.post(URL, json=_payload(MIX_ITEM, MIX_SITE, MIX_VENDOR))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["target"] == _target(TGT_MIX_ITEM, MIX_SITE, MIX_VENDOR)
    assert data["origin"] == _default_origin(TGT_MIX_ITEM, MIX_SITE)
    assert stub.calls == [("preview", TGT_MIX_ITEM, MIX_SITE, MIX_VENDOR)]
    assert _audit_rows() == []

    stub = _install(monkeypatch)
    response = admin.post(
        URL, json=_payload(MIX_ITEM, MIX_SITE, MIX_VENDOR, confirm=True)
    )
    assert response.status_code == 200, response.text
    assert response.json()["target"] == _target(TGT_MIX_ITEM, MIX_SITE, MIX_VENDOR)
    assert stub.calls == [("create", TGT_MIX_ITEM, MIX_SITE, MIX_VENDOR)]
    rows = _audit_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["item_code"] == MIX_ITEM
    assert row["target_item_code"] == TGT_MIX_ITEM
    assert row["site_code"] == MIX_SITE
    assert row["target_site_code"] == MIX_SITE
    assert row["supplier_code"] == MIX_VENDOR
    assert row["target_supplier_code"] == MIX_VENDOR

    # -- AC-1: idempotency follows the Odoo codes — two source items
    #    mapped to the same Odoo item at the same Odoo site call the
    #    client with the same target codes (one open draft). ------------
    reset()
    store("item", DUP_ITEM_A, TGT_DUP_ITEM)
    store("item", DUP_ITEM_B, TGT_DUP_ITEM)
    store("site", DUP_SITE_A, TGT_DUP_SITE)
    store("site", DUP_SITE_B, TGT_DUP_SITE)
    store("vendor", DUP_VENDOR, TGT_DUP_VENDOR)

    stub = _install(monkeypatch)
    response = admin.post(
        URL, json=_payload(DUP_ITEM_A, DUP_SITE_A, DUP_VENDOR, confirm=True)
    )
    assert response.status_code == 200, response.text
    assert response.json()["already_existed"] is False
    assert response.json()["target"] == _target(
        TGT_DUP_ITEM, TGT_DUP_SITE, TGT_DUP_VENDOR
    )
    # The second source item translates to the very same target codes,
    # so Odoo finds the first confirm's draft and no second create is
    # issued.
    stub = _install(monkeypatch, create=_already_existed)
    response = admin.post(
        URL, json=_payload(DUP_ITEM_B, DUP_SITE_B, DUP_VENDOR, confirm=True)
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["already_existed"] is True
    assert data["odoo_id"] == 701
    assert data["target"] == _target(TGT_DUP_ITEM, TGT_DUP_SITE, TGT_DUP_VENDOR)
    assert stub.calls == [("create", TGT_DUP_ITEM, TGT_DUP_SITE, TGT_DUP_VENDOR)]
    rows = _audit_rows()
    assert [row["outcome"] for row in rows] == ["created", "already_existed"]
    assert rows[1]["item_code"] == DUP_ITEM_B
    assert rows[1]["target_item_code"] == TGT_DUP_ITEM

    # -- AC-1: resolution failures — 502, detail names the code and
    #    whether it was mapped; the detail is a fixed string. -----------
    # Unmapped item (dry run): the request's code is named, no row.
    reset()
    stub = _install(monkeypatch, preview=_fail_product)
    response = client.post(URL, json=_payload(F1_ITEM, "F1-WH", "F1-VND"))
    assert response.status_code == 502, response.text
    assert response.json() == {
        "detail": f"product resolution failed: item '{F1_ITEM}' has no Odoo mapping"
    }
    assert SECRET not in response.text
    assert stub.calls == [("preview", F1_ITEM, "F1-WH", "F1-VND")]
    assert _audit_rows() == []

    # Mapped item (confirm): the TARGET code is named.
    store("item", F2_ITEM, TGT_F2_ITEM)
    stub = _install(monkeypatch, create=_fail_product)
    response = admin.post(URL, json=_payload(F2_ITEM, "F2-WH", "F2-VND", confirm=True))
    assert response.status_code == 502, response.text
    assert response.json() == {
        "detail": (
            f"product resolution failed: mapped item '{TGT_F2_ITEM}' "
            "not found in Odoo"
        )
    }
    assert SECRET not in response.text
    assert stub.calls == [("create", TGT_F2_ITEM, "F2-WH", "F2-VND")]
    rows = _audit_rows()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["error_class"] == "OdooError"
    assert rows[0]["item_code"] == F2_ITEM
    assert rows[0]["target_item_code"] == TGT_F2_ITEM

    # Same wording for vendor (unmapped and mapped).
    stub = _install(monkeypatch, create=_fail_vendor)
    response = admin.post(
        URL, json=_payload("F3-ITEM", "F3-WH", F3_VENDOR, confirm=True)
    )
    assert response.status_code == 502, response.text
    assert response.json() == {
        "detail": (
            f"vendor resolution failed: supplier '{F3_VENDOR}' " "has no Odoo mapping"
        )
    }
    assert stub.calls == [("create", "F3-ITEM", "F3-WH", F3_VENDOR)]

    store("vendor", F4_VENDOR, TGT_F4_VENDOR)
    stub = _install(monkeypatch, create=_fail_vendor)
    response = admin.post(
        URL, json=_payload("F4-ITEM", "F4-WH", F4_VENDOR, confirm=True)
    )
    assert response.status_code == 502, response.text
    assert response.json() == {
        "detail": (
            f"vendor resolution failed: mapped supplier '{TGT_F4_VENDOR}' "
            "not found in Odoo"
        )
    }
    assert stub.calls == [("create", "F4-ITEM", "F4-WH", TGT_F4_VENDOR)]

    # Same wording for warehouse (unmapped and mapped).
    stub = _install(monkeypatch, create=_fail_warehouse)
    response = admin.post(
        URL, json=_payload("F5-ITEM", F5_SITE, "F5-VND", confirm=True)
    )
    assert response.status_code == 502, response.text
    assert response.json() == {
        "detail": (
            f"warehouse resolution failed: site '{F5_SITE}' " "has no Odoo mapping"
        )
    }
    assert stub.calls == [("create", "F5-ITEM", F5_SITE, "F5-VND")]

    store("site", F6_SITE, TGT_F6_SITE)
    stub = _install(monkeypatch, create=_fail_warehouse)
    response = admin.post(
        URL, json=_payload("F6-ITEM", F6_SITE, "F6-VND", confirm=True)
    )
    assert response.status_code == 502, response.text
    assert response.json() == {
        "detail": (
            f"warehouse resolution failed: mapped site '{TGT_F6_SITE}' "
            "not found in Odoo"
        )
    }
    assert stub.calls == [("create", "F6-ITEM", TGT_F6_SITE, "F6-VND")]

    # -- AC-1: the 409 and 422 paths are unchanged. ----------------------
    # 409: writes disabled — fixed message, one error audit row.
    reset()

    def _fail_writes(**kwargs):
        raise OdooWritesDisabledError(
            "Odoo writes are disabled; set ODOO_ALLOW_WRITES to 1, "
            "true, or yes to enable purchase order creation"
        )

    stub = _install(monkeypatch, create=_fail_writes)
    response = admin.post(URL, json=_payload("C-ITEM", "C-WH", "C-VND", confirm=True))
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "Odoo writes are disabled"}
    rows = _audit_rows()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["error_class"] == "OdooWritesDisabledError"

    # 422: a client ValueError on a confirm — the client's message, one
    # error audit row.
    reset()

    def _fail_quantity(**kwargs):
        raise ValueError("quantity must be a positive finite number")

    stub = _install(monkeypatch, create=_fail_quantity)
    response = admin.post(
        URL, json=_payload("C2-ITEM", "C2-WH", "C2-VND", confirm=True)
    )
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "quantity must be a positive finite number"}
    rows = _audit_rows()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["error_class"] == "ValueError"

    # 422: a client ValueError on a dry run — no audit row.
    reset()

    def _fail_preview_value(**kwargs):
        raise ValueError("item_code must be a non-empty string")

    stub = _install(monkeypatch, preview=_fail_preview_value)
    response = client.post(URL, json=_payload("C3-ITEM", "C3-WH", "C3-VND"))
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "item_code must be a non-empty string"}
    assert _audit_rows() == []

    # -- Hostile codes: NUL bytes answer 422, never a 500. ---------------
    reset()
    stub = _install(monkeypatch)
    response = client.post(URL, json=_payload("NUL\x00ITEM", "NUL-WH", "NUL-VND"))
    _assert_null_422(response, "item_code")
    assert stub.calls == []  # no client call at all
    assert _audit_rows() == []

    stub = _install(monkeypatch)
    response = admin.post(
        URL, json=_payload("NUL2-ITEM", "NUL2\x00WH", "NUL2-VND", confirm=True)
    )
    _assert_null_422(response, "site_code")
    assert stub.calls == []
    assert _audit_rows() == []

    # -- AC-1: the migration upgrades and downgrades cleanly. ------------
    cfg = _alembic_config()
    assert _alembic_revision() == CURRENT_HEAD
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert list(heads) == [CURRENT_HEAD]
    columns = _draft_po_columns()
    for column in TARGET_COLUMNS:
        assert columns[column] == ("text", "YES"), column
    try:
        alembic_command.downgrade(cfg, PREV_HEAD)
        assert _alembic_revision() == PREV_HEAD
        columns = _draft_po_columns()
        # The downgrade drops exactly the three target columns.
        for column in TARGET_COLUMNS:
            assert column not in columns, column
        assert "item_code" in columns  # the rest of the table stays
    finally:
        alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == CURRENT_HEAD
    columns = _draft_po_columns()
    for column in TARGET_COLUMNS:
        assert columns[column] == ("text", "YES"), column
