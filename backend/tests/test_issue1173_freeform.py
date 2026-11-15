r"""Issue #1173 proof: standing grants to write to the ERP, behind a Bearer-gated API.

Everything is exercised against the test database through the app's
test client with the two development stub users: the admin stub
(``dev-secret-token:admin``, sub ``dev-admin-1``, roles admin+user,
username ``admin``) creates and revokes grants, and the non-admin
stub (role ``user``, sub ``dev-user-1``, username ``developer``)
reads them and gets refused the writes.

Proved here:

  * the migration — the revision that creates ``erp_standing_grants``
    (found by walking the Alembic history; the proof never names it
    and never asserts that it is the head or the only head) creates
    the table with exactly the documented shape (bigint identity
    ``id`` primary key, text ``process`` not null, numeric
    ``max_document_value`` and ``max_daily_value`` not null, integer
    ``max_data_age_hours`` not null, timestamptz ``created_at`` not
    null defaulting to now(), text ``created_by`` not null, timestamptz
    ``revoked_at`` null and text ``revoked_by`` null); the database's
    ``alembic_version`` equals the shared ``CURRENT_HEAD`` pin; the
    database itself refuses an unknown process, a zero
    ``max_document_value``, a ``max_daily_value`` below
    ``max_document_value``, a ``max_data_age_hours`` of 0 and of 169,
    a ``revoked_by`` without a ``revoked_at`` and a second row in
    force for a process; a unique index over ``process`` where
    ``revoked_at`` is null allows one grant in force per process; the
    downgrade drops the table and the upgrade back to head re-creates
    it cleanly;
  * an unauthenticated request to the list answers 401;
  * an admin's ``POST /api/standing-grants`` of order_plan_release /
    5000 / 20000 / 26 answers 201 with exactly those values — an
    integer ``id``, ``created_by`` the caller's username, a
    ``created_at`` that parses with a UTC offset, null ``revoked_at``
    and ``revoked_by``, ``in_force`` true — and a second
    order_plan_release answers 409 with nothing written; a
    transfer_release then stores fine and the list holds both, the
    newer first;
  * revoking the order-plan grant answers 200 with ``revoked_by`` the
    caller's username, a ``revoked_at`` with a UTC offset and
    ``in_force`` false; revoking it again answers 409, an id that was
    never stored answers 404, and a new order_plan_release grant can
    then be created;
  * a signed-in non-admin reads the same list with 200 and gets 403
    on a valid POST and on a revoke of a grant in force — the exact
    message the supplier-order-minimums routes refuse with — and the
    403 comes before any validation: a body that would answer 422
    for the admin, with or without a single valid JSON token, and a
    revoke with a malformed JSON body (even of an unknown id) all
    answer the same 403, and the table is unchanged afterwards;
  * without a token the list, a POST and a revoke each answer 401 —
    with a body that is not valid JSON at all, too;
  * the refused input, each 422 in FastAPI's HTTPValidationError
    envelope (a ``detail`` list of error objects) for the admin with
    the table unchanged afterwards: a process of aggregate_release,
    the empty string or the number 5; a ``max_document_value`` of 0,
    -1, the string "10", true, null, missing or 1000000000001; a
    ``max_daily_value`` below ``max_document_value``; a
    ``max_data_age_hours`` of 0, 169, 2.5 or true; an extra field
    ``confirm`` the body does not define; three raw JSON bodies with
    ``Infinity``, ``NaN`` and 1e400 — refused the way the
    supplier-order-minimums route refuses them, each 422 and never
    500 — and a body that is not valid JSON at all; and a revoke of
    an id of 0, abc or 9223372036854775808;
  * the switch — ``get_setting("erp.standing_grants_enabled")`` has
    system scope, type boolean, default true and the stated
    description, and ``GET /api/settings`` lists it;
  * the identity requirement: the proof also runs as a NON-ADMIN
    (role ``user``, asserted via ``GET /api/auth/me``) in a second
    pass after a full reload (a fresh ``TestClient`` booted the
    production way — the token the page would take from the URL hash
    as ``#access_token=...``, with ``GET /api/auth/config`` answering
    publicly and ``GET /api/auth/me`` resolving the viewer): the
    admin-only controls are refused with 403 (nothing created or
    revoked) while the page still renders — the grants it lists
    answer 200 — and a request without a token still gets 401.
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
from app.settings.registry import get_setting
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject
ADMIN_USERNAME = "admin"  # the admin stub user's username
ADMIN_DETAIL = "Role 'admin' is required to perform this action."

URL = "/api/standing-grants"
_TABLE = "erp_standing_grants"
_COLUMNS = {
    "id",
    "process",
    "max_document_value",
    "max_daily_value",
    "max_data_age_hours",
    "created_at",
    "created_by",
    "revoked_at",
    "revoked_by",
}
# The largest integer the bigint id column holds.
_BIGINT_MAX = 9223372036854775807

SETTING_KEY = "erp.standing_grants_enabled"
SETTING_DESCRIPTION = (
    "When off, no standing grant writes to the ERP; every write then "
    "needs a person's approval of that write."
)


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


def _index_defs() -> dict[str, str]:
    """The table's indexes as name -> index definition."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = %s ORDER BY indexname",
                (_TABLE,),
            )
            return {r[0]: r[1] for r in cur.fetchall()}
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


def _row_count() -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {_TABLE}")
            return cur.fetchone()[0]
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
        cur.execute("SAVEPOINT issue1173")
        try:
            cur.execute(sql, params)
        except psycopg2.Error as exc:
            assert type(exc) is expected, f"{label}: {type(exc).__name__}"
            cur.execute("ROLLBACK TO SAVEPOINT issue1173")
        else:
            raise AssertionError(f"{label}: the database accepted the row")
        finally:
            cur.execute("RELEASE SAVEPOINT issue1173")


def _valid_body(**overrides) -> dict:
    body = {
        "process": "order_plan_release",
        "max_document_value": 5000,
        "max_daily_value": 20000,
        "max_data_age_hours": 26,
    }
    body.update(overrides)
    return body


def test_issue1173_freeform(client):
    # -- The migration: the database sits on the shared pin, and the
    #    revision that creates the table is found by walking the
    #    history (never named here; the proof never asserts that it
    #    is the head or the only head). -------------------------------
    assert _alembic_revision() == CURRENT_HEAD
    creating = _creating_revision()
    # The creating revision builds on a real earlier revision.
    script = ScriptDirectory(_MIGRATIONS_DIR)
    assert creating.down_revision is not None
    assert script.get_revision(creating.down_revision) is not None

    # -- The table has exactly the documented shape. -------------------
    columns = _table_columns()
    assert set(columns) == _COLUMNS
    assert columns["id"] == ("bigint", None, "NO", None, "YES")
    assert columns["process"] == ("text", None, "NO", None, "NO")
    assert columns["max_document_value"] == ("numeric", None, "NO", None, "NO")
    assert columns["max_daily_value"] == ("numeric", None, "NO", None, "NO")
    assert columns["max_data_age_hours"] == ("integer", None, "NO", None, "NO")
    assert columns["created_at"] == (
        "timestamp with time zone",
        None,
        "NO",
        "now()",
        "NO",
    )
    assert columns["created_by"] == ("text", None, "NO", None, "NO")
    assert columns["revoked_at"] == (
        "timestamp with time zone",
        None,
        "YES",
        None,
        "NO",
    )
    assert columns["revoked_by"] == ("text", None, "YES", None, "NO")
    checks = _check_constraints()
    assert set(checks) == {
        "ck_erp_standing_grants_process",
        "ck_erp_standing_grants_max_document_value",
        "ck_erp_standing_grants_max_daily_value",
        "ck_erp_standing_grants_max_data_age_hours",
        "ck_erp_standing_grants_revoked",
    }
    assert "order_plan_release" in checks["ck_erp_standing_grants_process"]
    assert "transfer_release" in checks["ck_erp_standing_grants_process"]
    doc_check = checks["ck_erp_standing_grants_max_document_value"]
    assert "max_document_value >" in doc_check
    assert (
        "max_daily_value >= max_document_value"
        in checks["ck_erp_standing_grants_max_daily_value"]
    )
    age_check = checks["ck_erp_standing_grants_max_data_age_hours"]
    assert "max_data_age_hours >= 1" in age_check
    assert "max_data_age_hours <= 168" in age_check
    # revoked_by is null exactly when revoked_at is null.
    revoked_check = checks["ck_erp_standing_grants_revoked"]
    assert "revoked_by" in revoked_check and "revoked_at" in revoked_check
    assert "IS NULL" in revoked_check
    # One grant in force per process: the primary key aside, the table
    # has exactly one index — a unique one over process, limited to
    # the rows whose revoked_at is null.
    indexes = _index_defs()
    guards = {
        name: definition
        for name, definition in indexes.items()
        if "WHERE" in definition
    }
    assert len(guards) == 1, indexes
    index_def = next(iter(guards.values()))
    assert index_def.lstrip().startswith("CREATE UNIQUE INDEX"), index_def
    assert "process" in index_def
    assert "revoked_at IS NULL" in index_def

    # -- The database's own rules: nothing below is ever committed. ----
    conn = get_connection()
    try:
        insert_sql = (
            "INSERT INTO erp_standing_grants "
            "(process, max_document_value, max_daily_value, "
            "max_data_age_hours, created_by) "
            "VALUES (%s, %s, %s, %s, %s)"
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("aggregate_release", 5, 5, 1, "admin"),
            psycopg2.errors.CheckViolation,
            "unknown process",
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("order_plan_release", 0, 5, 1, "admin"),
            psycopg2.errors.CheckViolation,
            "zero max_document_value",
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("order_plan_release", 100, 50, 1, "admin"),
            psycopg2.errors.CheckViolation,
            "max_daily_value below max_document_value",
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("order_plan_release", 5, 5, 0, "admin"),
            psycopg2.errors.CheckViolation,
            "max_data_age_hours 0",
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("order_plan_release", 5, 5, 169, "admin"),
            psycopg2.errors.CheckViolation,
            "max_data_age_hours 169",
        )
        _expect_db_rejection(
            conn,
            "INSERT INTO erp_standing_grants "
            "(process, max_document_value, max_daily_value, "
            "max_data_age_hours, created_by, revoked_by) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("transfer_release", 5, 5, 1, "admin", "admin"),
            psycopg2.errors.CheckViolation,
            "a revoked_by without a revoked_at",
        )
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT issue1173")
            cur.execute(insert_sql, ("order_plan_release", 5, 5, 1, "admin"))
            cur.execute("RELEASE SAVEPOINT issue1173")
        _expect_db_rejection(
            conn,
            insert_sql,
            ("order_plan_release", 7, 7, 1, "admin"),
            psycopg2.errors.UniqueViolation,
            "a second row in force for a process",
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

    # -- Create: the admin's POST answers 201 with the stored row. -----
    response = admin.post(
        URL,
        json=_valid_body(),
    )
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["process"] == "order_plan_release"
    assert row["max_document_value"] == 5000
    assert row["max_daily_value"] == 20000
    assert row["max_data_age_hours"] == 26
    assert isinstance(row["id"], int)
    assert row["created_by"] == ADMIN_USERNAME
    created_at = datetime.fromisoformat(row["created_at"])
    assert created_at.utcoffset() is not None
    assert abs((datetime.now(UTC) - created_at).total_seconds()) < 120
    assert row["revoked_at"] is None
    assert row["revoked_by"] is None
    assert row["in_force"] is True
    order_plan_id = row["id"]
    # The list then holds exactly that row.
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == [row]

    # -- One in force per process: the second grant 409s, writes nothing.
    response = admin.post(URL, json=_valid_body())
    assert response.status_code == 409, response.text
    assert admin.get(URL).json() == [row]
    assert _row_count() == 1

    # -- The other process stores fine; the list holds both, newer first.
    response = admin.post(
        URL,
        json=_valid_body(
            process="transfer_release",
            max_document_value=1000,
            max_daily_value=1000,
            max_data_age_hours=1,
        ),
    )
    assert response.status_code == 201, response.text
    transfer_row = response.json()
    assert transfer_row["process"] == "transfer_release"
    assert transfer_row["max_document_value"] == 1000
    assert transfer_row["max_daily_value"] == 1000
    assert transfer_row["max_data_age_hours"] == 1
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == [transfer_row, row]

    # -- Revoke: the order-plan grant answers 200 with the revocation,
    #    a second revoke 409s, an unknown id 404s, and the process is
    #    free for a new grant. -------------------------------------------
    response = admin.post(f"{URL}/{order_plan_id}/revoke")
    assert response.status_code == 200, response.text
    revoked = response.json()
    assert revoked["id"] == order_plan_id
    assert revoked["revoked_by"] == ADMIN_USERNAME
    revoked_at = datetime.fromisoformat(revoked["revoked_at"])
    assert revoked_at.utcoffset() is not None
    assert revoked["in_force"] is False
    response = admin.post(f"{URL}/{order_plan_id}/revoke")
    assert response.status_code == 409, response.text
    assert admin.post(f"{URL}/{_BIGINT_MAX}/revoke").status_code == 404
    response = admin.post(URL, json=_valid_body())
    assert response.status_code == 201, response.text
    new_order_row = response.json()
    assert new_order_row["in_force"] is True
    assert new_order_row["id"] != order_plan_id
    # The list holds three rows, the newest first.
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == [new_order_row, transfer_row, revoked]

    # -- A signed-in non-admin reads the list and is refused the writes.
    before = admin.get(URL).json()
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == before
    response = viewer.post(
        URL,
        json=_valid_body(
            process="transfer_release",
            max_document_value=2000,
            max_daily_value=3000,
            max_data_age_hours=24,
        ),
    )
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    response = viewer.post(f"{URL}/{new_order_row['id']}/revoke")
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    # The 403 comes before any validation: a body that would answer
    # 422 for the admin — with or without a single valid JSON token —
    # and a revoke with a malformed JSON body (even of an unknown id)
    # are refused with the same 403, never a 422.
    response = viewer.post(URL, json=_valid_body(process=5))
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    for path in (
        URL,
        f"{URL}/{new_order_row['id']}/revoke",
        f"{URL}/abc/revoke",
    ):
        response = viewer.post(
            path,
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 403, f"{path}: {response.text}"
        assert response.json() == {"detail": ADMIN_DETAIL}
    assert admin.get(URL).json() == before

    # -- No token: 401 on the list, a POST and a revoke. ----------------
    response = anon.get(URL)
    assert response.status_code == 401, response.text
    assert response.headers.get("www-authenticate") == "Bearer"
    # A POST whose body would be refused still answers 401 first.
    assert anon.post(URL, json=_valid_body(process=5)).status_code == 401
    assert anon.post(f"{URL}/{new_order_row['id']}/revoke").status_code == 401
    assert anon.post(f"{URL}/abc/revoke").status_code == 401
    # …and with a body that is not valid JSON at all: without a token
    # the 401 still comes before any validation.
    for path in (URL, f"{URL}/{new_order_row['id']}/revoke"):
        response = anon.post(
            path,
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 401, f"{path}: {response.text}"
        assert response.headers.get("www-authenticate") == "Bearer"

    # -- Refused input: each 422, in the HTTPValidationError envelope
    #    (a detail list of error objects), and nothing is written. ------
    before = admin.get(URL).json()
    bad_bodies = (
        (_valid_body(process="aggregate_release"), "an unknown process"),
        (_valid_body(process=""), "an empty process"),
        (_valid_body(process=5), "a numeric process"),
        (_valid_body(max_document_value=0), "a zero max_document_value"),
        (_valid_body(max_document_value=-1), "a negative max_document_value"),
        (_valid_body(max_document_value="10"), "a string max_document_value"),
        (_valid_body(max_document_value=True), "a boolean max_document_value"),
        (_valid_body(max_document_value=None), "a null max_document_value"),
        (
            {
                "process": "order_plan_release",
                "max_daily_value": 20000,
                "max_data_age_hours": 26,
            },
            "a missing max_document_value",
        ),
        (
            _valid_body(max_document_value=1000000000001),
            "an over-large max_document_value",
        ),
        (
            _valid_body(max_document_value=1000, max_daily_value=500),
            "a max_daily_value below max_document_value",
        ),
        (_valid_body(max_data_age_hours=0), "a zero max_data_age_hours"),
        (_valid_body(max_data_age_hours=169), "a 169 max_data_age_hours"),
        (_valid_body(max_data_age_hours=2.5), "a fractional max_data_age_hours"),
        (_valid_body(max_data_age_hours=True), "a boolean max_data_age_hours"),
        (_valid_body(confirm=True), "an extra field the body does not define"),
    )
    for body, label in bad_bodies:
        response = admin.post(URL, json=body)
        assert response.status_code == 422, f"{label}: {response.text}"
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail, f"{label}: {response.text}"
    # Raw bodies whose number is Infinity, NaN or 1e400 — refused the
    # way the supplier-order-minimums route refuses them: each 422 in
    # the HTTPValidationError envelope, never a 500.
    raw_bodies = (
        '{"process": "order_plan_release", "max_document_value": Infinity, "max_daily_value": 20000, "max_data_age_hours": 26}',
        '{"process": "order_plan_release", "max_document_value": NaN, "max_daily_value": 20000, "max_data_age_hours": 26}',
        '{"process": "order_plan_release", "max_document_value": 1e400, "max_daily_value": 20000, "max_data_age_hours": 26}',
    )
    for raw in raw_bodies:
        response = admin.post(
            URL,
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422, f"{raw}: {response.text}"
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail, f"{raw}: {response.text}"
    # A body that is not valid JSON at all is refused with the same
    # 422 envelope for the admin, and never a 500.
    response = admin.post(
        URL,
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, list) and detail, response.text
    # A revoke whose id is not an integer from 1 to the bigint maximum
    # is a 422 too, and revokes nothing.
    for bad_id in ("0", "abc", str(_BIGINT_MAX + 1)):
        response = admin.post(f"{URL}/{bad_id}/revoke")
        assert response.status_code == 422, f"{bad_id}: {response.text}"
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail, f"{bad_id}: {response.text}"
    # Nothing was written and the stored rows are untouched.
    assert admin.get(URL).json() == before
    assert _row_count() == len(before)

    # -- The switch: the setting is registered and listed. --------------
    setting = get_setting(SETTING_KEY)
    assert setting is not None
    assert setting.scope == "system"
    assert setting.type == "boolean"
    assert setting.default is True
    assert setting.description == SETTING_DESCRIPTION
    response = admin.get("/api/settings")
    assert response.status_code == 200, response.text
    entries = response.json()
    keys = [entry["key"] for entry in entries]
    assert SETTING_KEY in keys
    # The lists are ordered by key: right after erp.reporting_currency.
    assert keys[keys.index("erp.reporting_currency") + 1] == SETTING_KEY
    entry = next(e for e in entries if e["key"] == SETTING_KEY)
    assert entry["scope"] == "system"
    assert entry["type"] == "boolean"
    assert entry["value"] is True
    assert entry["default"] is True

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
    # created or revoked.
    before = admin.get(URL).json()
    response = viewer_reloaded.post(
        URL,
        json=_valid_body(
            process="transfer_release",
            max_document_value=2000,
            max_daily_value=3000,
            max_data_age_hours=24,
        ),
    )
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    response = viewer_reloaded.post(f"{URL}/{new_order_row['id']}/revoke")
    assert response.status_code == 403, response.text
    assert admin.get(URL).json() == before
    # The page still renders: the grants it lists answer 200 for the
    # viewer, and a request without a token still gets 401.
    response = viewer_reloaded.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == before
    assert TestClient(app).get(URL).status_code == 401
