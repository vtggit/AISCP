r"""Issue #1118 proof: supplier order minimums behind a Bearer-gated API.

Everything is exercised against the test database through the app's
test client with the two development stub users: the admin stub
(``dev-secret-token:admin``, sub ``dev-admin-1``, roles admin+user,
username ``admin``) stores and deletes minimums, and the non-admin
stub (role ``user``, sub ``dev-user-1``, username ``developer``)
reads them and gets refused the mutations.

Proved here:

  * the migration — the 0041 revision (the one that creates the
    table, found by walking the Alembic history; the proof never
    names it) creates ``supplier_order_minimums`` with exactly the
    documented shape (bigint identity ``id`` primary key, text
    ``source_system`` and ``supplier_code`` not null with CHECKs that
    neither is empty, numeric ``amount`` not null with ``amount > 0``,
    timestamptz ``updated_at`` not null defaulting to now(), and text
    ``updated_by`` not null, UNIQUE on (``source_system``,
    ``supplier_code``)); the migration history has exactly one head
    and the database's ``alembic_version`` equals the shared
    ``CURRENT_HEAD`` pin; the database itself refuses a zero amount,
    a negative amount, an empty ``source_system``, an empty
    ``supplier_code`` and a second row for a stored pair; the
    downgrade drops the table and the upgrade back to head
    re-creates it cleanly;
  * an unauthenticated request to the list answers 401;
  * the admin's ``PUT /api/supplier-order-minimums`` of
    acumatica / V-100 / 2500 answers the stored row — the two strings
    exactly as sent, ``amount`` 2500, an integer ``id``, ``updated_by``
    the caller's username and an ``updated_at`` that parses with a
    UTC offset — and a second PUT of the same pair replaces the
    amount in place: still one row, same id; the same supplier code
    in another system (odoo / V-100 / 100) is another row with a
    different id;
  * the list orders the rows by ``source_system`` then
    ``supplier_code`` in Unicode code point order — a capital letter
    sorts before a small one — with the route doing the sorting, not
    the database's collation;
  * a signed-in non-admin reads the same list with 200 and gets 403
    on a valid PUT and on a DELETE of a stored id — the exact
    message the purchase-budgets routes refuse with — and the list
    is unchanged afterwards;
  * without a token the list, a PUT with a body that would be
    refused and a DELETE each answer 401;
  * the refused input, each 422 in FastAPI's HTTPValidationError
    envelope (a ``detail`` list of error objects) for the admin with
    the table unchanged afterwards: a body without ``source_system``;
    ``source_system`` empty, 65 characters long, with a leading
    space, with a tab inside, the number 5 or null; ``supplier_code``
    empty, 256 characters long, with a trailing space, containing
    U+0000, a lone surrogate sent as the JSON string "\ud800" or the
    number 5; ``amount`` 0, -1, the string "10", true, null, missing
    or 1000000000001; six raw JSON bodies — amounts Infinity, NaN
    and 1e400, a supplier_code sent as the JSON string "\ud800"
    (a lone surrogate), and a lone surrogate sent as a JSON key
    (whose missing-field 422s would echo the whole body, keys
    included, and could not be rendered — the raw-body guard must
    refuse it before body-model validation) — each answering 422
    and never 500, with the unstoreable value refused rather than
    echoed back; and a
    DELETE whose id is 0, -1, abc or 9223372036854775808;
  * the largest amount, 1000000000000, is accepted with 200;
  * the admin's DELETE of the odoo row's id is 204 with an empty
    body, the list no longer holds it, the same DELETE again answers
    404 and so does an id that was never stored;
  * the identity requirement: the proof also runs as a NON-ADMIN
    (role ``user``, asserted via ``GET /api/auth/me``) in a second
    pass after a full reload (a fresh ``TestClient`` booted the
    production way — the token the page would take from the URL hash
    as ``#access_token=...``, with ``GET /api/auth/config`` answering
    publicly and ``GET /api/auth/me`` resolving the viewer): the
    admin-only controls are refused with 403 (nothing stored or
    deleted) while the page still renders — the minimums the page
    lists answer 200 — and a request without a token still gets 401.
"""

import ast
import io
import os
import tokenize
from datetime import UTC, datetime

import psycopg2
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from app.repositories.supplier_order_minimums_postgres_repository import (
    SupplierOrderMinimumPostgresRepository,
)
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject
ADMIN_USERNAME = "admin"  # the admin stub user's username
ADMIN_DETAIL = "Role 'admin' is required to perform this action."

URL = "/api/supplier-order-minimums"
_TABLE = "supplier_order_minimums"
_COLUMNS = {
    "id",
    "source_system",
    "supplier_code",
    "amount",
    "updated_at",
    "updated_by",
}
_UNIQ = ["source_system", "supplier_code"]
# The largest integer the bigint id column holds.
_BIGINT_MAX = 9223372036854775807


def _alembic_revision() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _pg_tables() -> set[str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def _table_columns() -> dict:
    """The table's columns as (data_type, max_length, nullability, default, identity)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "is_nullable, column_default, is_identity "
                "FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (_TABLE,),
            )
            return {r[0]: (r[1], r[2], r[3], r[4], r[5]) for r in cur.fetchall()}
    finally:
        conn.close()


def _unique_column_sets() -> list[list[str]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT con.conname, a.attname "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_attribute a ON a.attrelid = con.conrelid "
                "AND a.attnum = ANY(con.conkey) "
                "WHERE rel.relname = %s AND con.contype = 'u' "
                "ORDER BY con.conname, a.attnum",
                (_TABLE,),
            )
            by_name: dict[str, list[str]] = {}
            for name, att in cur.fetchall():
                by_name.setdefault(name, []).append(att)
            return [by_name[name] for name in sorted(by_name)]
    finally:
        conn.close()


def _check_constraints() -> dict[str, str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT con.conname, pg_get_constraintdef(con.oid) "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "WHERE rel.relname = %s AND con.contype = 'c' "
                "ORDER BY con.conname",
                (_TABLE,),
            )
            return {name: definition for name, definition in cur.fetchall()}
    finally:
        conn.close()


def _creates_table(source: str, table: str) -> bool:
    """True only when ``source`` holds a real ``op.create_table`` call.

    The call's first argument must be the string literal naming
    ``table`` — single- or double-quoted, any white space (line
    breaks included) allowed between the parenthesis and the string.
    A name that merely appears in a comment, a docstring, a
    ``down_revision`` string, a foreign key, a differently named
    table or another call (such as ``op.drop_table``) never matches,
    nor does an expression that merely begins with the literal
    (a concatenation or an operation building a different name).
    """
    tokens: list = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            tokens.append(token)
    except (
        tokenize.TokenError,
        IndentationError,
        SyntaxError,
        ValueError,
        UnicodeDecodeError,
    ):
        pass
    skip = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
    }
    for index, token in enumerate(tokens):
        if token.type != tokenize.NAME or token.string != "op":
            continue
        # ``x.op.create_table`` is a call on ``x.op``, not on ``op``.
        if index and tokens[index - 1].string == ".":
            continue
        window = tokens[index : index + 4]
        if [(t.type, t.string) for t in window] != [
            (tokenize.NAME, "op"),
            (tokenize.OP, "."),
            (tokenize.NAME, "create_table"),
            (tokenize.OP, "("),
        ]:
            continue
        # The first argument must be the string literal itself: an
        # unbroken run of string tokens (implicit concatenation is a
        # single literal expression) that ends exactly at the
        # argument's comma or the call's closing parenthesis.
        # Anything else — an operation, a call, a tuple, a prefixed
        # string — is not a plain string literal and never matches.
        literals: list[str] = []
        ends_argument = False
        position = index + 4
        while position < len(tokens):
            argument = tokens[position]
            position += 1
            if argument.type in skip:
                continue
            if argument.type == tokenize.STRING:
                literals.append(argument.string)
                continue
            ends_argument = argument.type == tokenize.OP and argument.string in (
                ",",
                ")",
            )
            break
        if not literals or not ends_argument:
            continue
        try:
            value = ast.literal_eval(" ".join(literals))
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            continue
        if isinstance(value, str) and value == table:
            return True
    return False


def _creating_revision():
    """The revision that creates the table, found by walking the history.

    The proof never names the revision: the Alembic history is walked
    and the one revision whose module creates the table is the one
    this proof is about.
    """
    script = ScriptDirectory(_MIGRATIONS_DIR)
    for rev in script.walk_revisions():
        path = getattr(rev.module, "__file__", None)
        if not path:
            continue
        # A revision file that is not valid UTF-8 must not crash the
        # walk: it cannot hold the call, so read it leniently.
        with open(path, "rb") as fh:
            source = fh.read().decode("utf-8", errors="replace")
        if _creates_table(source, _TABLE):
            return rev
    raise AssertionError(f"no revision creates {_TABLE}")


def _expect_db_rejection(
    conn,
    sql: str,
    params: tuple,
    expected: type[psycopg2.Error],
    label: str,
) -> None:
    """Run one statement in a savepoint; it must fail with ``expected``.

    The savepoint keeps the surrounding (rolled-back) transaction
    usable after each violation, and nothing here is ever committed.
    """
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT issue1118")
        try:
            cur.execute(sql, params)
        except psycopg2.Error as exc:
            assert type(exc) is expected, f"{label}: {type(exc).__name__}"
            cur.execute("ROLLBACK TO SAVEPOINT issue1118")
        else:
            raise AssertionError(f"{label}: the database accepted the row")
        finally:
            cur.execute("RELEASE SAVEPOINT issue1118")


def test_issue1118_freeform(client):
    repo = SupplierOrderMinimumPostgresRepository()

    # -- The migration: the history has exactly one head, the database
    #    sits on the shared pin, and the revision that creates the
    #    table is found by walking the history (never named here). ----
    assert _alembic_revision() == CURRENT_HEAD
    heads = ScriptDirectory(_MIGRATIONS_DIR).get_heads()
    creating = _creating_revision()
    assert list(heads) == [CURRENT_HEAD]
    assert creating.revision in {
        rev.revision for rev in ScriptDirectory(_MIGRATIONS_DIR).walk_revisions()
    }
    # The creating revision builds on a real earlier revision.
    script = ScriptDirectory(_MIGRATIONS_DIR)
    assert creating.down_revision is not None
    assert script.get_revision(creating.down_revision) is not None

    # -- The table has exactly the documented shape. -------------------
    columns = _table_columns()
    assert set(columns) == _COLUMNS
    assert columns["id"] == ("bigint", None, "NO", None, "YES")
    assert columns["source_system"] == ("text", None, "NO", None, "NO")
    assert columns["supplier_code"] == ("text", None, "NO", None, "NO")
    assert columns["amount"] == ("numeric", None, "NO", None, "NO")
    assert columns["updated_at"] == (
        "timestamp with time zone",
        None,
        "NO",
        "now()",
        "NO",
    )
    assert columns["updated_by"] == ("text", None, "NO", None, "NO")
    assert _unique_column_sets() == [_UNIQ]
    checks = _check_constraints()
    assert set(checks) == {
        "ck_supplier_order_minimums_amount",
        "ck_supplier_order_minimums_source_system",
        "ck_supplier_order_minimums_supplier_code",
    }
    assert "amount > (0)::numeric" in checks["ck_supplier_order_minimums_amount"]
    assert "source_system <> ''" in checks["ck_supplier_order_minimums_source_system"]
    assert "supplier_code <> ''" in checks["ck_supplier_order_minimums_supplier_code"]

    # -- The database's own rules: nothing below is ever committed. ----
    conn = get_connection()
    try:
        insert_sql = (
            "INSERT INTO supplier_order_minimums "
            "(source_system, supplier_code, amount, updated_by) "
            "VALUES (%s, %s, %s, %s)"
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("acumatica", "V-1", 0, "admin"),
            psycopg2.errors.CheckViolation,
            "zero amount",
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("acumatica", "V-1", -5, "admin"),
            psycopg2.errors.CheckViolation,
            "negative amount",
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("", "V-1", 5, "admin"),
            psycopg2.errors.CheckViolation,
            "empty source_system",
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("acumatica", "", 5, "admin"),
            psycopg2.errors.CheckViolation,
            "empty supplier_code",
        )
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT issue1118")
            cur.execute(insert_sql, ("acumatica", "V-1", 5, "admin"))
            cur.execute("RELEASE SAVEPOINT issue1118")
        _expect_db_rejection(
            conn,
            insert_sql,
            ("acumatica", "V-1", 7, "admin"),
            psycopg2.errors.UniqueViolation,
            "second row for a stored pair",
        )
    finally:
        conn.rollback()
        conn.close()

    # -- The downgrade drops the table; the upgrade back to head
    #    re-creates it cleanly (the target is the walked revision's
    #    down revision, still dynamic — never a named literal). --------
    cfg = AlembicConfig(os.path.join(_BACKEND_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", _MIGRATIONS_DIR)
    alembic_command.downgrade(cfg, creating.down_revision)
    assert _alembic_revision() == creating.down_revision
    assert _TABLE not in _pg_tables()
    alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == CURRENT_HEAD
    assert _table_columns() == columns

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon = TestClient(app)

    # -- Empty at first: the list answers 200 with []. ------------------
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == []

    # -- Store: the admin's PUT answers the stored row. ------------------
    response = admin.put(
        URL,
        json={"source_system": "acumatica", "supplier_code": "V-100", "amount": 2500},
    )
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["source_system"] == "acumatica"
    assert row["supplier_code"] == "V-100"
    assert row["amount"] == 2500
    assert isinstance(row["id"], int)
    assert row["updated_by"] == ADMIN_USERNAME
    parsed = datetime.fromisoformat(row["updated_at"])
    assert parsed.utcoffset() is not None
    assert abs((datetime.now(UTC) - parsed).total_seconds()) < 120
    first_id = row["id"]
    # The stored text is exactly the string that was sent.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_system, supplier_code "
                "FROM supplier_order_minimums WHERE id = %s",
                (first_id,),
            )
            assert cur.fetchone() == ("acumatica", "V-100")
    finally:
        conn.close()
    # The list then holds exactly that row.
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == [row]

    # -- Replace: the same pair with a new amount keeps its id. ---------
    response = admin.put(
        URL,
        json={"source_system": "acumatica", "supplier_code": "V-100", "amount": 3000.5},
    )
    assert response.status_code == 200, response.text
    row2 = response.json()
    assert row2["id"] == first_id
    assert row2["amount"] == 3000.5
    assert row2["updated_by"] == ADMIN_USERNAME
    assert datetime.fromisoformat(row2["updated_at"]) >= parsed
    # The table holds one row.
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1

    # -- The same code in another system is another row. -----------------
    response = admin.put(
        URL,
        json={"source_system": "odoo", "supplier_code": "V-100", "amount": 100},
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] != first_id
    odoo_id = response.json()["id"]

    # -- Order: code point order, a capital letter before a small one. --
    assert (
        admin.put(
            URL,
            json={"source_system": "acumatica", "supplier_code": "a-2", "amount": 10},
        ).status_code
        == 200
    )
    assert (
        admin.put(
            URL,
            json={"source_system": "acumatica", "supplier_code": "B-1", "amount": 20},
        ).status_code
        == 200
    )
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    assert [(r["source_system"], r["supplier_code"]) for r in response.json()] == [
        ("acumatica", "B-1"),
        ("acumatica", "V-100"),
        ("acumatica", "a-2"),
        ("odoo", "V-100"),
    ]

    # -- A signed-in non-admin reads the list and is refused the writes.
    before = admin.get(URL).json()
    assert before == response.json()
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == before
    response = viewer.put(
        URL,
        json={"source_system": "acumatica", "supplier_code": "V-100", "amount": 5},
    )
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    response = viewer.delete(f"{URL}/{first_id}")
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    assert admin.get(URL).json() == before

    # -- No token: 401 on the list, a PUT with a refused body and a
    #    DELETE — even a DELETE with a bad id. --------------------------
    response = anon.get(URL)
    assert response.status_code == 401, response.text
    assert response.headers.get("www-authenticate") == "Bearer"
    assert (
        anon.put(
            URL,
            json={"source_system": 5, "supplier_code": "V-100", "amount": -1},
        ).status_code
        == 401
    )
    assert anon.delete(f"{URL}/{first_id}").status_code == 401
    assert anon.delete(f"{URL}/abc").status_code == 401

    # -- Refused input: each 422, in the HTTPValidationError envelope
    #    (a detail list of error objects), and nothing is written. ------
    bad_bodies = (
        ({"supplier_code": "V-100", "amount": 5}, "missing source_system"),
        (
            {"source_system": "", "supplier_code": "V-100", "amount": 5},
            "empty source_system",
        ),
        (
            {"source_system": "s" * 65, "supplier_code": "V-100", "amount": 5},
            "65-char source_system",
        ),
        (
            {"source_system": " s", "supplier_code": "V-100", "amount": 5},
            "leading-space source_system",
        ),
        (
            {"source_system": "s\ts", "supplier_code": "V-100", "amount": 5},
            "tab-inside source_system",
        ),
        (
            {"source_system": 5, "supplier_code": "V-100", "amount": 5},
            "number source_system",
        ),
        (
            {"source_system": None, "supplier_code": "V-100", "amount": 5},
            "null source_system",
        ),
        (
            {"source_system": "acumatica", "supplier_code": "", "amount": 5},
            "empty supplier_code",
        ),
        (
            {"source_system": "acumatica", "supplier_code": "s" * 256, "amount": 5},
            "256-char supplier_code",
        ),
        (
            {"source_system": "acumatica", "supplier_code": "V ", "amount": 5},
            "trailing-space supplier_code",
        ),
        (
            {"source_system": "acumatica", "supplier_code": "V\x00100", "amount": 5},
            "NUL supplier_code",
        ),
        (
            {"source_system": "acumatica", "supplier_code": 5, "amount": 5},
            "number supplier_code",
        ),
        (
            {"source_system": "acumatica", "supplier_code": "V-100", "amount": 0},
            "zero amount",
        ),
        (
            {"source_system": "acumatica", "supplier_code": "V-100", "amount": -1},
            "negative amount",
        ),
        (
            {"source_system": "acumatica", "supplier_code": "V-100", "amount": "10"},
            "string amount",
        ),
        (
            {"source_system": "acumatica", "supplier_code": "V-100", "amount": True},
            "boolean amount",
        ),
        (
            {"source_system": "acumatica", "supplier_code": "V-100", "amount": None},
            "null amount",
        ),
        (
            {"source_system": "acumatica", "supplier_code": "V-100"},
            "missing amount",
        ),
        (
            {
                "source_system": "acumatica",
                "supplier_code": "V-100",
                "amount": 1000000000001,
            },
            "above-cap amount",
        ),
    )
    for body, label in bad_bodies:
        response = admin.put(URL, json=body)
        assert response.status_code == 422, f"{label}: {response.text}"
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail, f"{label}: {response.text}"
    # A non-finite amount cannot ride in a serialized dict (the
    # serializer refuses it), so it goes as the raw JSON text a hostile
    # client can send: the Infinity and NaN tokens and a number so
    # large it overflows to one on parse, and a supplier_code sent as the
    # JSON string "\\ud800" (a lone surrogate — the only way a hostile
    # client can send one).  The lone surrogate also goes as a JSON key:
    # the body model's own missing-field errors would echo the whole
    # body, keys included, and that 422 could not be rendered (a 500) —
    # the raw-body guard must refuse it before body-model validation.
    # Each answers 422 — never a 500 — in the HTTPValidationError
    # envelope.
    raw_bodies = (
        '{"source_system": "acumatica", "supplier_code": "V-100", "amount": Infinity}',
        '{"source_system": "acumatica", "supplier_code": "\\ud800", "amount": 5}',
        '{"source_system": "acumatica", "supplier_code": "V-100", "amount": NaN}',
        '{"source_system": "acumatica", "supplier_code": "V-100", "amount": 1e400}',
        '{"\\ud800": 5}',
        '{"\\ud800": Infinity}',
    )
    for raw in raw_bodies:
        response = admin.put(
            URL,
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422, f"{raw}: {response.text}"
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail, f"{raw}: {response.text}"
        # The unstoreable value is refused, not echoed: the envelope
        # renders (the unfixed 422 could not) and the lone surrogate
        # never rides back in the answer.
        assert "\ud800" not in response.text, f"{raw}: {response.text}"
    # A DELETE whose id is not an integer from 1 to the bigint maximum
    # is a 422 too, and deletes nothing.
    for bad_id in ("0", "-1", "abc", "9223372036854775808"):
        response = admin.delete(f"{URL}/{bad_id}")
        assert response.status_code == 422, f"{bad_id}: {response.text}"
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail, f"{bad_id}: {response.text}"
    # Nothing was written and the stored rows are untouched.
    assert admin.get(URL).json() == before
    assert len(repo.list()) == len(before)

    # -- The largest amount is accepted: 1e12 stores fine. ---------------
    response = admin.put(
        URL,
        json={
            "source_system": "acumatica",
            "supplier_code": "V-100",
            "amount": 1000000000000,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["amount"] == 1000000000000
    assert response.json()["id"] == first_id

    # -- Remove: the odoo row is 204 and gone; a second DELETE is 404,
    #    and so is an id that was never stored. --------------------------
    response = admin.delete(f"{URL}/{odoo_id}")
    assert response.status_code == 204, response.text
    assert response.content == b""
    rows = admin.get(URL).json()
    assert all(r["id"] != odoo_id for r in rows)
    assert len(rows) == 3
    response = admin.delete(f"{URL}/{odoo_id}")
    assert response.status_code == 404, response.text
    assert admin.delete(f"{URL}/{_BIGINT_MAX}").status_code == 404

    # -- Identity, second pass after a full reload: the page boots the
    #    production way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the admin-only controls are
    #    refused 403 while the page still renders for the viewer. ------
    viewer_reloaded = TestClient(
        app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
    )
    assert TestClient(app).get("/api/auth/config").status_code == 200
    me = viewer_reloaded.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == VIEWER_SUB
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]
    # The admin stub's identity is the other one: sub and username.
    admin_me = admin.get("/api/auth/me")
    assert admin_me.status_code == 200, admin_me.text
    assert admin_me.json()["user"]["sub"] == ADMIN_SUB
    assert admin_me.json()["user"]["username"] == ADMIN_USERNAME
    # The admin-only controls are absent for the viewer: 403, nothing
    # stored or deleted.
    before = admin.get(URL).json()
    response = viewer_reloaded.put(
        URL,
        json={"source_system": "acumatica", "supplier_code": "V-100", "amount": 1},
    )
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    response = viewer_reloaded.delete(f"{URL}/{first_id}")
    assert response.status_code == 403, response.text
    assert admin.get(URL).json() == before
    # The page still renders: the minimums it lists answer 200 for the
    # viewer, and a request without a token still gets 401.
    response = viewer_reloaded.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == before
    assert TestClient(app).get(URL).status_code == 401
