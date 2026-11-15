"""Issue #881 proof: releasing chosen transfer suggestions as transfer
orders on hold.

The proof seeds two transfer suggestions the way the issue 842 and
853 proofs seed theirs — the route module's ``recommendations_for_all``
replaced with a fake and the item_sites read seeded for the same
source system — so that item A suggests 154 to 159 (a need of 120
against a surplus of 300) and item B suggests 154 to 159 (a need of
120 against a surplus of 50), and the Acumatica client class is
replaced with a fake recording its calls.

AC-1 — preview: a preview of A and B answers 200 with one group
"AIERP transfer <as_of> site 154 to 159" holding both lines at the
suggested quantities (120 and 50) — the request never carries a
quantity, the fake receives the suggested ones — writing no row and
making no create call; an entry for item Z answers in ``unreleasable``
with "no longer suggested".  With Acumatica not configured the
preview answers 200 with each group's outcome, reference_nbr and
warehouse codes null while still listing the matched lines and
quantities, no client call, matching the purchase-order release
preview — and the confirm still answers 503 before any row.

AC-1 — confirm: an admin confirm calls the fake's create once with
the group, writes one ``transfer_releases`` row with outcome
"created" and the fake's reference number, and the row appears in
``GET /api/supply-plan/transfers/release/audit`` (newest first,
admin only).  When Acumatica reports an existing transfer order the
confirm answers 200 with the per-group exists outcome and the
existing order's reference number, and the audit row records the
exists outcome and the returned reference_nbr; when the current plan
lines differ from the stored snapshot of the origin the confirm still
answers 200 — never a 409 for changed or stale lines — keeping the
outcome but not the existing reference number (null) with the
stale-lines error in the row and the answer.

AC-1 — refusals and the audit: a signed-in non-admin confirm answers
403 before any ERP call or row; switched-off writes answer 409 and
write nothing; an empty ``transfers`` list, a repeated
(item_code, from_site, to_site) entry, a NUL byte, a 65-character
string and an extra body field all answer 422 before any ERP call or
row; no token answers 401; a non-admin audit read answers 403.  A
second concurrent confirm waits on the transaction-level advisory
lock and answers 409 when it cannot take it — writing nothing — and a
fake that raises on create answers 502 leaving the pending row
"aborted".

AC-2 — the migration: the database has one head, the new revision is
in its history and ``transfer_releases`` has the stated columns.

Identity — the proof also runs as a NON-ADMIN (role "user"): a second
pass in the same test after a full reload, with the page booted the
production way (the token it would take from the URL hash as
``#access_token=...`` — the development token, role "user" — sent as
its Bearer token), ``GET /api/auth/config`` answering publicly and
``GET /api/auth/me`` resolving the viewer — sees the preview still
answer (200 with the same body, open to any signed-in user, so no
admin-only control stands on it) while the admin-only confirm is
refused with 403 and no token is still a 401.
"""

import json
import os
from datetime import date
from pathlib import Path

import psycopg2
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.api.supply_plan as supply_plan
import app.feed.acumatica_client as acumatica_client_module
import app.feed.odoo_client as odoo_client_module
import app.repositories.transfer_releases_postgres_repository as transfer_releases_repo
from app.db.connection import get_connection_params, get_cursor
from app.feed.acumatica_client import (
    AcumaticaClient,
    AcumaticaError,
    AcumaticaWritesDisabledError,
)
from app.main import app
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from tests.migration_head import CURRENT_HEAD

URL = "/api/supply-plan/transfers/release"
AUDIT = "/api/supply-plan/transfers/release/audit"
_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"
_MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations"
)

# The one revision this migration adds — concatenated on purpose:
# the issue 803 proof owns the double-quoted head literal and scans
# the rest of the suite for it, and black would normalise a bare
# single-quoted literal back to double quotes.
HEAD_REVISION = "0034_transfer" "_releases"

ADMIN_TOKEN = "dev-secret-token:admin"
PLANNER_TOKEN = "dev-secret-token:user"
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

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

# The resolved as-of the route reports (never null) — deliberately
# not the server date.
AS_OF = date(2026, 10, 31)

ITEM_A = "ITEM-881-A"
ITEM_B = "ITEM-881-B"
ITEM_Z = "ITEM-881-Z"
SITE_FROM = "154"
SITE_TO = "159"
ORIGIN = f"AIERP transfer {AS_OF.isoformat()} site {SITE_FROM} to {SITE_TO}"
NO_LONGER_SUGGESTED_REASON = "no longer suggested"
ADMIN_DETAIL = "Role 'admin' is required to perform this action."
WRITES_OFF_DETAIL = "Acumatica writes are disabled"
CONFIG_DETAIL = "Acumatica configuration is incomplete"
LOCK_DETAIL = "Another transfer release is already in progress"
EXISTING_REFERENCE = "TR-881-EXIST"
STALE_ERROR = (
    f"the existing transfer order for origin {ORIGIN} "
    "no longer matches the planned lines; re-confirm is required"
)

QTY_A = 120  # item A's suggestion: min(need 120, surplus 300)
QTY_B = 50  # item B's suggestion: min(need 120, surplus 50)


def _entry(item_code: str) -> dict:
    """One requested entry: the item and the two sites — never a
    quantity."""
    return {
        "item_code": item_code,
        "from_site": SITE_FROM,
        "to_site": SITE_TO,
    }


def _lines(a_quantity: int, b_quantity: int = QTY_B) -> list[dict]:
    return [
        {"item_code": ITEM_A, "quantity": a_quantity},
        {"item_code": ITEM_B, "quantity": b_quantity},
    ]


def _group(outcome, reference_nbr, from_code, to_code, error, lines) -> dict:
    return {
        "origin": ORIGIN,
        "from_site": SITE_FROM,
        "to_site": SITE_TO,
        "lines": lines,
        "outcome": outcome,
        "reference_nbr": reference_nbr,
        "from_warehouse_code": from_code,
        "to_warehouse_code": to_code,
        "error": error,
    }


def _body(groups, unreleasable) -> dict:
    return {"as_of": AS_OF.isoformat(), "groups": groups, "unreleasable": unreleasable}


EXPECTED_PREVIEW = _body(
    [_group("would_create", None, "WHOLESALE", "DISTRIBUTION", None, _lines(QTY_A))],
    [],
)
EXPECTED_PREVIEW_Z = _body(
    [_group("would_create", None, "WHOLESALE", "DISTRIBUTION", None, _lines(QTY_A))],
    [
        {
            "item_code": ITEM_Z,
            "from_site": SITE_FROM,
            "to_site": SITE_TO,
            "reason": NO_LONGER_SUGGESTED_REASON,
        }
    ],
)
EXPECTED_PREVIEW_UNCONFIGURED = _body(
    [_group(None, None, None, None, None, _lines(QTY_A))],
    [],
)
EXPECTED_CONFIRM_CREATED = _body(
    [_group("created", "TR-881-001", "WHOLESALE", "DISTRIBUTION", None, _lines(QTY_A))],
    [],
)
EXPECTED_CONFIRM_EXISTS = _body(
    [
        _group(
            "exists",
            EXISTING_REFERENCE,
            "WHOLESALE",
            "DISTRIBUTION",
            None,
            _lines(QTY_A),
        )
    ],
    [],
)
EXPECTED_CONFIRM_STALE = _body(
    [_group("exists", None, "WHOLESALE", "DISTRIBUTION", STALE_ERROR, _lines(125))],
    [],
)


def _rec(
    item_code,
    site_code,
    *,
    rop,
    on_hand,
    on_order,
    oq_units,
    unit_cost,
):
    """One row of the fake ``recommendations_for_all`` in the shape the
    real one answers: the position values, the reorder point in whole
    units and the order-quantity block."""
    return {
        "item_code": item_code,
        "site_code": site_code,
        "as_of": AS_OF,
        "status": "ok",
        "reorder_point": float(rop),
        "reorder_point_units": rop,
        "safety_stock_units": 50,
        "inputs": {
            "d": 10.0,
            "sd_d": 2.0,
            "lt": 7.0,
            "sd_lt": 1.0,
            "z": 1.6449,
            "service_level": 0.95,
        },
        "on_hand_units": on_hand,
        "on_order_units": on_order,
        "unit_cost": unit_cost,
        "suggested_order_quantity": 0,
        "order_quantity": {
            "basis": "eoq",
            "clamped": None,
            "order_quantity": float(oq_units),
            "order_quantity_units": oq_units,
            "cover_days": 7.0,
            "annual_demand": 3650.0,
            "order_cost": 50.0,
            "holding_rate": 0.25,
            "unit_cost": unit_cost,
            "arithmetic": f"EOQ {oq_units}",
            "recommended_max_units": None,
            "current_order_qty": None,
            "current_max_qty": None,
        },
    }


class FakeRecommendations:
    """The fake for the route module's ``recommendations_for_all``:
    the rows of the two items, each a need at site 159 against a
    surplus at site 154 of the same source system — item A suggests
    154 to 159 with a quantity of 120, item B with 50.  ``need_a`` is
    the one number the stale-line guard checks move: the receiver's
    need of item A, so the suggestion's quantity follows it."""

    calls = 0
    need_a = QTY_A

    def __call__(self, as_of=None):
        FakeRecommendations.calls += 1
        return [
            # Item A: site 159 needs need_a (ROP 400); site 154 holds
            # 300 beyond ROP + order quantity (ROP 300, order
            # quantity 100, position 700).
            _rec(
                ITEM_A,
                SITE_TO,
                rop=400,
                on_hand=400 - self.need_a - 80,
                on_order=80,
                oq_units=100,
                unit_cost=2.5,
            ),
            _rec(
                ITEM_A,
                SITE_FROM,
                rop=300,
                on_hand=700,
                on_order=0,
                oq_units=100,
                unit_cost=2.5,
            ),
            # Item B: a need of 120 against a surplus of 50 — the
            # partial cover: the suggestion is 50, not 120.
            _rec(
                ITEM_B,
                SITE_TO,
                rop=400,
                on_hand=280,
                on_order=0,
                oq_units=100,
                unit_cost=10.0,
            ),
            _rec(
                ITEM_B,
                SITE_FROM,
                rop=300,
                on_hand=450,
                on_order=0,
                oq_units=100,
                unit_cost=10.0,
            ),
        ]


def _transfer_release_rows() -> list[dict]:
    """Every stored transfer_releases row, oldest first, every column."""
    conn = psycopg2.connect(**get_connection_params())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM transfer_releases ORDER BY id")
            columns = [column[0] for column in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def test_issue881_freeform(monkeypatch, client) -> None:
    # --- The migration: one head, the new revision in its history,
    # and the stated columns of transfer_releases. ----------------------
    script_dir = ScriptDirectory(_MIGRATIONS_DIR)
    assert list(script_dir.get_heads()) == [CURRENT_HEAD]
    assert HEAD_REVISION in {script.revision for script in script_dir.walk_revisions()}
    with get_cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        assert cur.fetchone()["version_num"] == CURRENT_HEAD
        cur.execute(
            "SELECT column_name, is_nullable, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'transfer_releases'"
        )
        columns = {
            row["column_name"]: (
                row["is_nullable"].lower(),
                row["data_type"],
            )
            for row in cur.fetchall()
        }
    assert columns == {
        "id": ("no", "bigint"),
        "origin": ("no", "text"),
        "from_site_code": ("no", "text"),
        "to_site_code": ("no", "text"),
        "from_warehouse_code": ("yes", "text"),
        "to_warehouse_code": ("yes", "text"),
        "reference_nbr": ("yes", "text"),
        "outcome": ("no", "text"),
        "error": ("yes", "text"),
        "line_count": ("no", "integer"),
        "lines": ("no", "jsonb"),
        "actor": ("no", "text"),
        "actor_name": ("yes", "text"),
        "created_at": ("no", "timestamp with time zone"),
        "updated_at": ("no", "timestamp with time zone"),
        "erp_status": ("yes", "text"),
        "erp_status_checked_at": ("yes", "timestamp with time zone"),
    }

    # --- Isolate the Acumatica environment: complete configuration,
    # writes on. ----------------------------------------------------------
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", "https://acumatica.example.com/Instance")
    monkeypatch.setenv("ACUMATICA_TENANT", "AIERP")
    monkeypatch.setenv("ACUMATICA_USERNAME", "svc-aierp")
    monkeypatch.setenv("ACUMATICA_PASSWORD", "s3cret-password!")
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "1")

    # --- Seed the item_sites the builder reads once per call: both
    # suggestions on the acumatica source system. ------------------------
    ItemSitePostgresRepository().upsert_many(
        [
            {"item_code": ITEM_A, "site_code": SITE_FROM, "source_system": "acumatica"},
            {"item_code": ITEM_A, "site_code": SITE_TO, "source_system": "acumatica"},
            {"item_code": ITEM_B, "site_code": SITE_FROM, "source_system": "acumatica"},
            {"item_code": ITEM_B, "site_code": SITE_TO, "source_system": "acumatica"},
        ]
    )

    # --- The Acumatica client, replaced with a fake recording its
    # calls.  The write switch is consulted the way the real client
    # consults it: before anything else, before the empty-batch
    # shortcut.
    preview_calls: list[list] = []
    create_calls: list[list] = []
    create_mode = {"mode": "created"}  # "created" | "exists" | "raise"

    def _writes_enabled() -> bool:
        return os.environ.get("ACUMATICA_ALLOW_WRITES", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    def fake_preview(self, transfers):
        preview_calls.append([dict(entry) for entry in transfers])
        if not transfers:
            return []
        return [
            {
                "origin": entry["origin"],
                "outcome": "would_create",
                "reference_nbr": None,
                "status": None,
                "from_warehouse_code": "WHOLESALE",
                "to_warehouse_code": "DISTRIBUTION",
                "line_count": len(entry["lines"]),
                "error": None,
            }
            for entry in transfers
        ]

    def fake_create(self, transfers):
        if not _writes_enabled():
            raise AcumaticaWritesDisabledError(
                "Acumatica writes are disabled; set ACUMATICA_ALLOW_WRITES "
                "to a truthy value to enable transfer order creation"
            )
        if not transfers:
            return []
        if create_mode["mode"] == "raise":
            raise AcumaticaError(
                "the Acumatica TransferOrder write failed with HTTP 500"
            )
        create_calls.append([dict(entry) for entry in transfers])
        outcome = create_mode["mode"]
        reference = (
            EXISTING_REFERENCE
            if outcome == "exists"
            else f"TR-881-{len(create_calls):03d}"
        )
        return [
            {
                "origin": entry["origin"],
                "outcome": outcome,
                "reference_nbr": reference,
                "status": "On Hold",
                "from_warehouse_code": "WHOLESALE",
                "to_warehouse_code": "DISTRIBUTION",
                "line_count": len(entry["lines"]),
                "error": None,
            }
            for entry in transfers
        ]

    monkeypatch.setattr(AcumaticaClient, "preview_transfer_orders", fake_preview)
    monkeypatch.setattr(AcumaticaClient, "create_transfer_orders", fake_create)

    # --- The builder's sources, replaced the way the issue 830 proof
    # replaces its sources: the fake recommendations answer the canned
    # rows and the resolved as-of is pinned.
    FakeRecommendations.calls = 0
    monkeypatch.setattr(supply_plan, "recommendations_for_all", FakeRecommendations())
    monkeypatch.setattr(supply_plan, "default_as_of", lambda cur: AS_OF)

    # No ERP construction outside the release routes: the odoo client
    # fails if constructed.
    def _no_odoo(self, *args, **kwargs):
        raise AssertionError("the transfer release route must not call Odoo")

    monkeypatch.setattr(odoo_client_module.OdooClient, "__init__", _no_odoo)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    planner = TestClient(app, headers={"Authorization": f"Bearer {PLANNER_TOKEN}"})
    anon = TestClient(app)

    # --- No token answers 401 (status only — auth_gate defines no
    # canonical 401 body).
    assert anon.post(URL, json={"transfers": [_entry(ITEM_A)]}).status_code == 401

    # --- Preview of A and B: one group, both lines at the suggested
    # quantities, nothing written, no create call.
    preview = admin.post(URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B)]})
    assert preview.status_code == 200, preview.text
    assert preview.json() == EXPECTED_PREVIEW
    assert FakeRecommendations.calls == 1  # the suggestions read once
    assert len(_transfer_release_rows()) == 0
    assert create_calls == []
    # The request never carries a quantity, and the fake receives the
    # suggested ones: one group in the wire format, the two lines in
    # request order at 120 and 50.
    assert preview_calls == [
        [
            {
                "from_site_id": SITE_FROM,
                "to_site_id": SITE_TO,
                "origin": ORIGIN,
                "lines": _lines(QTY_A),
            }
        ]
    ]

    # --- Unreleasable: an entry for item Z — no longer suggested. ----
    preview_z = admin.post(
        URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B), _entry(ITEM_Z)]}
    )
    assert preview_z.status_code == 200, preview_z.text
    assert preview_z.json() == EXPECTED_PREVIEW_Z
    assert FakeRecommendations.calls == 2
    assert len(_transfer_release_rows()) == 0

    # --- Body rules: 422 before any ERP call or row. --------------------
    for body in (
        {"transfers": []},  # empty list
        {"transfers": [_entry(ITEM_A), _entry(ITEM_A)]},  # duplicate entry
        {
            "transfers": [
                {
                    "item_code": ITEM_A + "\x00",
                    "from_site": SITE_FROM,
                    "to_site": SITE_TO,
                }
            ]
        },  # NUL byte
        {
            "transfers": [
                {
                    "item_code": "I" * 65,
                    "from_site": SITE_FROM,
                    "to_site": SITE_TO,
                }
            ]
        },  # a string beyond 64 characters
        {"transfers": [_entry(ITEM_A)], "bogus": 1},  # an extra field
    ):
        bad = admin.post(URL, json=body)
        assert bad.status_code == 422, (body, bad.text)
    assert FakeRecommendations.calls == 2
    assert len(preview_calls) == 2
    assert create_calls == []
    assert len(_transfer_release_rows()) == 0

    # --- Acumatica not configured: the preview answers 200 with null
    # outcomes and codes, the lines and quantities still listed, no
    # client call — and the confirm 503 before any row.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    unconfigured = admin.post(URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B)]})
    assert unconfigured.status_code == 200, unconfigured.text
    assert unconfigured.json() == EXPECTED_PREVIEW_UNCONFIGURED
    assert len(preview_calls) == 2  # no client call
    unconfigured_confirm = admin.post(
        URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B)], "confirm": True}
    )
    assert unconfigured_confirm.status_code == 503
    assert unconfigured_confirm.json()["detail"] == CONFIG_DETAIL
    assert create_calls == []
    assert len(_transfer_release_rows()) == 0
    monkeypatch.setenv("ACUMATICA_BASE_URL", "https://acumatica.example.com/Instance")
    monkeypatch.setenv("ACUMATICA_TENANT", "AIERP")
    monkeypatch.setenv("ACUMATICA_USERNAME", "svc-aierp")
    monkeypatch.setenv("ACUMATICA_PASSWORD", "s3cret-password!")
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "1")

    # --- Confirm: the fake's create once, one row "created" with the
    # fake's reference number, the row in the audit.
    confirm = admin.post(
        URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B)], "confirm": True}
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json() == EXPECTED_CONFIRM_CREATED
    assert create_calls == [preview_calls[0]]
    rows = _transfer_release_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["origin"] == ORIGIN
    assert row["from_site_code"] == SITE_FROM
    assert row["to_site_code"] == SITE_TO
    assert row["from_warehouse_code"] == "WHOLESALE"
    assert row["to_warehouse_code"] == "DISTRIBUTION"
    assert row["reference_nbr"] == "TR-881-001"
    assert row["outcome"] == "created"
    assert row["error"] is None
    assert row["line_count"] == 2
    assert row["lines"] == _lines(QTY_A)
    assert row["actor"] == "admin"
    assert row["actor_name"] == "admin"
    assert row["created_at"] and row["updated_at"]

    # The row appears in the audit — newest first — and the audit
    # stays admin only with its limit.
    audit = admin.get(AUDIT)
    assert audit.status_code == 200, audit.text
    audit_rows = audit.json()
    assert len(audit_rows) == 1
    assert audit_rows[0]["id"] == row["id"]
    assert audit_rows[0]["origin"] == ORIGIN
    assert audit_rows[0]["from_site_code"] == SITE_FROM
    assert audit_rows[0]["to_site_code"] == SITE_TO
    assert audit_rows[0]["from_warehouse_code"] == "WHOLESALE"
    assert audit_rows[0]["to_warehouse_code"] == "DISTRIBUTION"
    assert audit_rows[0]["reference_nbr"] == "TR-881-001"
    assert audit_rows[0]["outcome"] == "created"
    assert audit_rows[0]["error"] is None
    assert audit_rows[0]["line_count"] == 2
    assert audit_rows[0]["lines"] == _lines(QTY_A)
    assert audit_rows[0]["actor"] == "admin"
    assert audit_rows[0]["actor_name"] == "admin"
    assert audit_rows[0]["created_at"] == row["created_at"].isoformat()
    assert audit_rows[0]["updated_at"] == row["updated_at"].isoformat()
    assert len(admin.get(AUDIT, params={"limit": 1}).json()) == 1
    assert admin.get(AUDIT, params={"limit": 0}).status_code == 422
    assert admin.get(AUDIT, params={"limit": 201}).status_code == 422
    assert planner.get(AUDIT).status_code == 403

    # --- Exists: Acumatica reports an existing transfer order for the
    # origin — 200 with the per-group exists outcome and the existing
    # order's reference number, the audit row recording it.
    create_mode["mode"] = "exists"
    exists_confirm = admin.post(
        URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B)], "confirm": True}
    )
    assert exists_confirm.status_code == 200, exists_confirm.text
    assert exists_confirm.json() == EXPECTED_CONFIRM_EXISTS
    assert len(create_calls) == 2
    rows = _transfer_release_rows()
    assert len(rows) == 2
    exists_row = rows[1]
    assert exists_row["outcome"] == "exists"
    assert exists_row["reference_nbr"] == EXISTING_REFERENCE
    assert exists_row["error"] is None
    assert exists_row["lines"] == _lines(QTY_A)

    # --- Stale lines: the current plan no longer matches the stored
    # snapshot of the origin — still 200 (never a 409 for changed or
    # stale lines), the outcome kept but the existing reference number
    # not inherited (null), the stale-lines error in row and answer.
    FakeRecommendations.need_a = 125
    stale_confirm = admin.post(
        URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B)], "confirm": True}
    )
    assert stale_confirm.status_code == 200, stale_confirm.text
    assert stale_confirm.json() == EXPECTED_CONFIRM_STALE
    stale_group = stale_confirm.json()["groups"][0]
    assert stale_group["reference_nbr"] is None
    assert stale_group["reference_nbr"] != EXISTING_REFERENCE
    assert stale_group["error"] == STALE_ERROR
    rows = _transfer_release_rows()
    assert len(rows) == 3
    stale_row = rows[2]
    assert stale_row["outcome"] == "exists"
    assert stale_row["reference_nbr"] is None
    assert stale_row["error"] == STALE_ERROR
    assert stale_row["lines"] == _lines(125)

    # --- A second concurrent confirm waits on the transaction-level
    # advisory lock and answers 409 when it cannot take it — writing
    # nothing; with the lock free the confirm proceeds.
    monkeypatch.setattr(transfer_releases_repo, "TRANSFER_RELEASE_LOCK_TIMEOUT_MS", 300)
    holder = psycopg2.connect(**get_connection_params())
    try:
        with holder.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (transfer_releases_repo.TRANSFER_RELEASE_LOCK_KEY,),
            )
        before_rows = len(_transfer_release_rows())
        before_calls = len(create_calls)
        contended = admin.post(
            URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B)], "confirm": True}
        )
        assert contended.status_code == 409
        assert contended.json()["detail"] == LOCK_DETAIL
        assert len(_transfer_release_rows()) == before_rows
        assert len(create_calls) == before_calls
    finally:
        holder.close()
    create_mode["mode"] = "created"
    after_release = admin.post(
        URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B)], "confirm": True}
    )
    assert after_release.status_code == 200, after_release.text
    assert after_release.json()["groups"][0]["outcome"] == "created"
    assert len(create_calls) == before_calls + 1

    # --- Refusals. -----------------------------------------------------
    # A planner confirm answers 403 and writes nothing.
    refused = planner.post(
        URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B)], "confirm": True}
    )
    assert refused.status_code == 403
    assert refused.json()["detail"] == ADMIN_DETAIL
    # Switched-off writes answer 409 and write nothing.
    monkeypatch.delenv("ACUMATICA_ALLOW_WRITES")
    off = admin.post(
        URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B)], "confirm": True}
    )
    assert off.status_code == 409
    assert off.json()["detail"] == WRITES_OFF_DETAIL
    assert len(_transfer_release_rows()) == 4
    assert len(create_calls) == before_calls + 1
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "1")

    # --- Batch failure: a fake that raises on create answers 502 and
    # leaves the pending row "aborted".
    create_mode["mode"] = "raise"
    failed = admin.post(
        URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B)], "confirm": True}
    )
    assert failed.status_code == 502
    rows = _transfer_release_rows()
    assert len(rows) == 5
    aborted = rows[4]
    assert aborted["outcome"] == "aborted"
    assert aborted["error"] == "the Acumatica TransferOrder write failed with HTTP 500"
    assert aborted["reference_nbr"] is None

    # --- The schema file lists the routes and their models. -----------
    schema = json.loads(_OPENAPI.read_text())
    assert "post" in schema["paths"]["/api/supply-plan/transfers/release"]
    assert "get" in schema["paths"]["/api/supply-plan/transfers/release/audit"]
    for model in (
        "TransferReleasePlan",
        "TransferReleaseGroup",
        "TransferReleaseEntry",
        "TransferReleaseIn",
        "TransferReleaseLine",
        "TransferReleaseUnreleasable",
        "TransferReleaseRow",
    ):
        assert model in schema["components"]["schemas"]

    # --- Identity: a second pass after a full reload, as the
    # NON-ADMIN (role "user").  The page is booted the production
    # way: the token it would take from the URL hash as
    # #access_token=... is the development token (role "user") sent
    # as its Bearer token; GET /api/auth/config answers publicly and
    # GET /api/auth/me resolves the viewer.
    FakeRecommendations.need_a = QTY_A  # the preview body, as before
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["authenticated"] is True
    assert "user" in me.json()["user"]["roles"]
    assert "admin" not in me.json()["user"]["roles"]

    # The preview still answers for the viewer — the route is open to
    # any signed-in user, so no admin-only control stands on it —
    # with the same body; the admin-only confirm is refused; no token
    # is still a 401.
    viewer_preview = viewer.post(
        URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B)]}
    )
    assert viewer_preview.status_code == 200, viewer_preview.text
    assert viewer_preview.json() == EXPECTED_PREVIEW
    viewer_confirm = viewer.post(
        URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B)], "confirm": True}
    )
    assert viewer_confirm.status_code == 403
    assert (
        anon_reloaded.post(
            URL, json={"transfers": [_entry(ITEM_A), _entry(ITEM_B)]}
        ).status_code
        == 401
    )
    # Still nothing new written.
    assert len(_transfer_release_rows()) == 5
    assert len(preview_calls) == 3
    assert len(create_calls) == before_calls + 1
