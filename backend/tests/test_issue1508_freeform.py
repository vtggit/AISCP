"""Issue #1508 proof: the plan release PO readback.

``plan_releases`` gains the Acumatica readback of its released
purchase orders — ``erp_status``, ``erp_lines`` (jsonb) and
``erp_checked_at`` — added by the migration this proof migrates to
head, and the maintenance entry point
``app.supply.release_readback.refresh_plan_release_readbacks()``
(not a route) reads the rows whose outcome is ``created`` or
``exists``, whose ``order_nbr`` is non-null, and whose ``erp_status``
is null or neither ``Completed``, ``Closed`` nor ``Canceled``, in
chunks of at most 100 of their distinct order numbers through
``AcumaticaClient.read_purchase_orders``, storing for every number the
ERP returns the answer's ``status``, its ``lines`` exactly as returned
and the check instant — a number the ERP does not return leaves its
rows unchanged.

Proved here, against the test database migrated to head:

  * the seam: Acumatica is configured through the environment (the
    way the transfer status proof configures it) and
    ``AcumaticaClient.read_purchase_orders`` is replaced on the class
    (the way the release tracking proof replaces it), recording the
    numbers of each call;
  * the rows: ``created`` with number A, ``exists`` with B, ``error``
    with C, ``created`` with D whose ``erp_status`` is ``Closed``, and
    ``created`` without a number, every number unique to this proof;
  * the first run: the fake answers only A, status "Open" with two
    lines — one call asks for A and B, A's row holds that status,
    those lines and a check time no earlier than the run's start,
    every other row is unchanged, and the answer is
    ``{"checked": 2, "updated": 1}``;
  * the second run: the fake answers A with one line and B with
    status "Canceled" — one call asks for A and B, both rows hold
    their answers, and the answer is ``{"checked": 2, "updated": 2}``;
  * the third run: one call asks for A only (B is Canceled, D is
    Closed, C is an error, and the numberless row carries nothing),
    and the answer is ``{"checked": 1, "updated": 1}``;
  * the many numbers: with the rows above deleted, 150 ``created``
    rows make two calls, of 100 and 50 numbers, each number once;
  * not configured: with the environment cleared the job answers the
    skip, makes no call and changes no row;
  * the migration: the database has one head, equal to
    ``CURRENT_HEAD`` imported from ``tests/migration_head.py`` (the
    proof never writes the revision name as a literal), and
    ``plan_releases`` has the three new nullable columns;
  * the identity requirement: the proof also runs as a NON-ADMIN
    (role ``user``) in a second pass after a full reload — the page
    boots the production way (the token it would take from the URL
    hash as ``#access_token=...``, the bare development token, with
    ``/api/auth/config`` answering publicly and ``/api/auth/me``
    resolving the viewer for that user), the admin-only control is
    absent — the admin-only plan release audit is refused for the
    viewer and the readback job is not a route at all — while the
    page still renders: the settings the page loads answer 200 for
    the viewer and unauthenticated ``/api`` traffic is still 401.

Every row the proof inserts is deleted in a ``finally``.
"""

import os
import pathlib
from datetime import UTC, datetime, timezone

import psycopg2
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.api as api_package
from app.db.connection import get_connection_params, get_cursor
from app.feed.acumatica_client import AcumaticaClient
from app.main import app
from app.supply.release_readback import refresh_plan_release_readbacks
from tests.migration_head import CURRENT_HEAD

BASE_URL = "https://acumatica.example.com/Instance"
TENANT = "AIERP"
USERNAME = "svc-aierp"
PASSWORD = "s3cret-password!"

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

ADMIN_TOKEN = "dev-secret-token:admin"
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

_MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations"
)
AUDIT = "/api/aggregate/release/audit"

# The proof's order numbers: every one unique to this proof.
NBR_A = "PO1508-0001"
NBR_B = "PO1508-0002"
NBR_C = "PO1508-0003"
NBR_D = "PO1508-0004"


def _line(item_code: str, quantity: int) -> dict:
    """One ERP order line, in the shape the client answers with."""
    return {
        "item_code": item_code,
        "warehouse_code": "154",
        "order_qty": quantity,
        "received_qty": 0,
        "completed": False,
        "cancelled": False,
        "promised": "2026-12-01",
    }


def _record(status: str, lines: list[dict]) -> dict:
    """One ERP purchase-order answer, in the shape the client returns."""
    return {
        "status": status,
        "hold": False,
        "date": "2026-11-20",
        "promised_on": "2026-12-01",
        "total": 100.0,
        "lines": lines,
    }


def _insert(outcome: str, order_nbr: str | None, erp_status: str | None = None) -> int:
    """One plan_releases row, every NOT NULL column filled."""
    conn = psycopg2.connect(**get_connection_params())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO plan_releases "
                "(month, site_code, supplier_code, origin, outcome, "
                "order_nbr, erp_status, line_count, value, lines, actor) "
                "VALUES ('2026-11', '154', '6995', %s, %s, %s, %s, 1, "
                '100.00, \'[{"item_code": "MMM", "quantity": 10, '
                '"unit_cost": "10.00", "value": "100.00"}]\'::jsonb, '
                "'developer') RETURNING id",
                (
                    f"AIERP plan 2026-11 site 154 vendor 6995 {order_nbr or 'none'}",
                    outcome,
                    order_nbr,
                    erp_status,
                ),
            )
            row_id = cur.fetchone()[0]
        conn.commit()
        return row_id
    finally:
        conn.close()


def _row_state(row_id: int) -> tuple:
    """Every column of one row, in column order, for a before/after
    comparison — a row the readback leaves unchanged compares equal."""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM plan_releases WHERE id = %s", (row_id,))
        row = cur.fetchone()
        return tuple(row.values())


def _erp_state(row_id: int) -> tuple:
    """The readback columns of one row: (erp_status, erp_lines,
    erp_checked_at)."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT erp_status, erp_lines, erp_checked_at "
            "FROM plan_releases WHERE id = %s",
            (row_id,),
        )
        row = cur.fetchone()
        return row["erp_status"], row["erp_lines"], row["erp_checked_at"]


def test_issue1508_freeform(monkeypatch, client) -> None:
    # --- Isolate the Acumatica environment (configured). ----------------
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", BASE_URL)
    monkeypatch.setenv("ACUMATICA_TENANT", TENANT)
    monkeypatch.setenv("ACUMATICA_USERNAME", USERNAME)
    monkeypatch.setenv("ACUMATICA_PASSWORD", PASSWORD)

    # The seam: read_purchase_orders replaced on the class, recording
    # the numbers of each call.
    answers: dict[str, dict] = {}
    read_calls: list[list[str]] = []

    def fake_read(self, order_nbrs):
        read_calls.append(list(order_nbrs))
        return {nbr: answers[nbr] for nbr in order_nbrs if nbr in answers}

    monkeypatch.setattr(AcumaticaClient, "read_purchase_orders", fake_read)

    inserted: list[int] = []
    try:
        # The five rows: A created, B exists, C error, D created
        # already Closed, and a created row without a number.
        row_a = _insert("created", NBR_A)
        row_b = _insert("exists", NBR_B)
        row_c = _insert("error", NBR_C)
        row_d = _insert("created", NBR_D, erp_status="Closed")
        row_e = _insert("created", None)
        inserted.extend([row_a, row_b, row_c, row_d, row_e])
        before = {row_id: _row_state(row_id) for row_id in inserted}

        # -- 1. the first run: the fake answers only A ------------------
        lines_two = [_line("MMM", 10), _line("QQQ", 4)]
        answers[NBR_A] = _record("Open", lines_two)
        run_start = datetime.now(UTC)
        read_calls.clear()
        result = refresh_plan_release_readbacks()
        assert result == {"checked": 2, "updated": 1}
        assert read_calls == [[NBR_A, NBR_B]]  # one call, A and B, ascending
        status, erp_lines, checked_at = _erp_state(row_a)
        assert status == "Open"
        assert erp_lines == lines_two  # exactly as returned
        assert checked_at is not None  # the check time
        assert checked_at >= run_start  # no earlier than the run's start
        for row_id in (row_b, row_c, row_d, row_e):
            assert _row_state(row_id) == before[row_id]  # unchanged

        # -- 2. the second run: A with one line, B Canceled -------------
        lines_one = [_line("MMM", 6)]
        lines_b = [_line("NNN", 2)]
        answers[NBR_A] = _record("Open", lines_one)
        answers[NBR_B] = _record("Canceled", lines_b)
        read_calls.clear()
        result = refresh_plan_release_readbacks()
        assert result == {"checked": 2, "updated": 2}
        assert read_calls == [[NBR_A, NBR_B]]  # one call, both rows
        status, erp_lines, checked_at = _erp_state(row_a)
        assert status == "Open"
        assert erp_lines == lines_one
        assert checked_at is not None
        status, erp_lines, checked_at = _erp_state(row_b)
        assert status == "Canceled"
        assert erp_lines == lines_b
        assert checked_at is not None

        # -- 3. the third run: only A is still to be read ---------------
        read_calls.clear()
        result = refresh_plan_release_readbacks()
        assert result == {"checked": 1, "updated": 1}
        assert read_calls == [[NBR_A]]  # one call, A only
        status, _erp_lines, checked_at = _erp_state(row_a)
        assert status == "Open"
        assert checked_at is not None

        # -- 4. the many numbers: 150 rows, two calls (100 and 50) ------
        with get_cursor() as cur:
            cur.execute("DELETE FROM plan_releases WHERE id = ANY(%s)", (inserted,))
        inserted.clear()
        for i in range(1, 151):
            inserted.append(_insert("created", f"PO1508-{1000 + i:04d}"))
        answers.clear()
        answers.update(
            {
                f"PO1508-{1000 + i:04d}": _record("Open", [_line("MMM", 1)])
                for i in range(1, 151)
            }
        )
        read_calls.clear()
        result = refresh_plan_release_readbacks()
        assert result == {"checked": 150, "updated": 150}
        assert len(read_calls) == 2
        assert len(read_calls[0]) == 100
        assert len(read_calls[1]) == 50
        seen = [nbr for call in read_calls for nbr in call]
        assert len(seen) == 150 == len(set(seen))  # each number once
        assert seen == sorted(seen)  # ascending, in chunk order

        # -- 5. not configured: the skip, no call, nothing changed ------
        for var in ACUMATICA_VARS:
            monkeypatch.delenv(var, raising=False)
        states_before = {row_id: _row_state(row_id) for row_id in inserted}
        read_calls.clear()
        result = refresh_plan_release_readbacks()
        assert result == {"checked": 0, "updated": 0, "skipped": "not configured"}
        assert read_calls == []  # no client call
        for row_id in inserted:
            assert _row_state(row_id) == states_before[row_id]  # no row changed

        # -- 6. the migration: one head, the three new columns ----------
        script_dir = ScriptDirectory(_MIGRATIONS_DIR)
        assert list(script_dir.get_heads()) == [CURRENT_HEAD]
        with get_cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            assert cur.fetchone()["version_num"] == CURRENT_HEAD
            cur.execute(
                "SELECT column_name, is_nullable, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'plan_releases' "
                "AND column_name IN ('erp_status', 'erp_lines', 'erp_checked_at')"
            )
            columns = {
                row["column_name"]: (row["is_nullable"].lower(), row["data_type"])
                for row in cur.fetchall()
            }
        assert columns == {
            "erp_status": ("yes", "text"),
            "erp_lines": ("yes", "jsonb"),
            "erp_checked_at": ("yes", "timestamp with time zone"),
        }

        # -- 7. Identity: a second pass after a full reload, as the
        # NON-ADMIN (role "user").  The page is booted the production
        # way: the token it would take from the URL hash as
        # #access_token=... is the development token (role "user") sent
        # as its Bearer token; GET /api/auth/config answers publicly
        # and GET /api/auth/me resolves the viewer for that user.
        viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
        anon = TestClient(app)
        config = anon.get("/api/auth/config")
        assert config.status_code == 200
        me = viewer.get("/api/auth/me")
        assert me.status_code == 200, me.text
        user = me.json()["user"]
        assert user["roles"] == ["user"]
        assert "admin" not in user["roles"]
        assert "admin" not in user["groups"]
        # The admin-only control is absent: the plan release audit is
        # admin-only and refused for the viewer, and this issue's
        # readback job is not a route at all — no route module exposes
        # it.
        assert viewer.get(AUDIT).status_code == 403
        for path in pathlib.Path(api_package.__file__).parent.glob("*.py"):
            assert "refresh_plan_release_readbacks" not in path.read_text()
        # The page still renders: the settings the page loads answer
        # 200 for the viewer, and the gate is intact — unauthenticated
        # /api traffic is still 401.
        assert viewer.get("/api/settings").status_code == 200
        assert anon.get("/api/settings").status_code == 401
    finally:
        if inserted:
            with get_cursor() as cur:
                cur.execute("DELETE FROM plan_releases WHERE id = ANY(%s)", (inserted,))
