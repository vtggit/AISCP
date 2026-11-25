"""Issue #1596 proof: a failed exceptions source is logged.

AC-1 — in ``GET /api/exceptions`` (the route in
``app/api/exceptions.py``), every ``except Exception`` block that
adds a source name to ``sources_failed`` also logs one warning
through the module's ``logger``:
``logger.warning("exceptions: source %s failed: %s", <name>,
type(exc).__name__)`` — the same name it adds, for each of the
sixteen sources.  The block that catches the failure of the stored
system settings read at the start of the route adds no name and logs
the same warning with the name ``settings``.  The warning carries the
exception's class name only: the exception's message and its
traceback are never logged, because a source's message can carry an
ERP's internals.

AC-2 — the proof seeds the data and makes a source fail the way
``test_issue1019_freeform.py`` does: a fake repository is set on the
route module, its read raises.  With the ``currencies`` source made
to raise ``RuntimeError("secret-text")`` the route answers 200 with
``"currencies"`` in ``sources_failed`` and the log of
``app.api.exceptions`` holds exactly one WARNING record whose message
is ``exceptions: source currencies failed: RuntimeError``; the text
``secret-text`` appears nowhere in the captured log and the record
carries no exception info.  With ``currencies`` and one more of the
sixteen sources both failing, there are exactly two such records, one
naming each, in the order the names appear in ``sources_failed``.
With no source failing, ``app.api.exceptions`` logs no WARNING
record.  The proof reads the source of the route and shows that each
of the sixteen names is passed to a ``logger.warning`` call in the
block that appends it.  The answer is read strictly:
``body["sources_failed"]``, never ``.get`` with a default.
"""

import ast
import logging
from pathlib import Path
from uuid import uuid4

import app.api.exceptions as exceptions
from app.db.connection import get_connection
from app.repositories.erp_source_currencies_postgres_repository import (
    ErpSourceCurrencyPostgresRepository,
)

URL = "/api/exceptions"

# The sixteen sources of the exceptions list, in the order the route
# gathers them (and so in the order their names appear in
# sources_failed when several fail).
SOURCES = [
    "ddmrp",
    "stockout",
    "expedite",
    "releases",
    "transfers",
    "pushes",
    "sop",
    "clashes",
    "currencies",
    "across",
    "supplier_risk",
    "second_sources",
    "order_minimums",
    "forecast",
    "standing_grants",
    "basis",
]

WARNING_FORMAT = "exceptions: source %s failed: %s"

EXCEPTIONS_LOGGER = "app.api.exceptions"


class _LogCapture(logging.Handler):
    """A handler that keeps every record it is given."""

    def __init__(self):
        super().__init__(level=logging.NOTSET)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class _RaisingCurrenciesRepo:
    """The fake for the route module's currencies repository: its
    read raises RuntimeError("secret-text"), and it must never be
    written — the way test_issue1019_freeform.py makes the source
    fail."""

    list_calls = 0

    def list_all(self):
        _RaisingCurrenciesRepo.list_calls += 1
        raise RuntimeError("secret-text")

    def replace_for(self, source_system, codes):
        raise AssertionError("the exceptions route must not store currencies")


class _RaisingClashesRepo:
    """The fake for the route module's code-clash repository: its
    read raises, the second failing source of the two-source pass."""

    def list_open(self, limit=200):
        raise RuntimeError("another-secret")


def _seed(client_repo):
    """The data seeded the way test_issue1019_freeform.py seeds it:
    one item site in the database and one stored ERP source currency
    (EUR, against the default USD reporting setting) through the real
    repository."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO item_sites (id, item_code, site_code) "
                "VALUES (%s, %s, %s)",
                (str(uuid4()), "ITEM-1596", "SITE-1596"),
            )
        conn.commit()
    finally:
        conn.close()
    client_repo.replace_for("odoo", ["EUR"])


def _currency_rows(body):
    return [row for row in body["rows"] if row["kind"] == "erp_currency_mismatch"]


def _get_with_capture(client, capture):
    """One GET of the route while every record of the module's logger
    is kept: the handler is attached to the app.api.exceptions logger
    for the call and removed again afterwards.  The session's Alembic
    upgrade runs fileConfig(alembic.ini), whose
    disable_existing_loggers default left this logger disabled, so
    the flag is lifted for the call and restored after it."""
    log = logging.getLogger(EXCEPTIONS_LOGGER)
    old_level = log.level
    old_disabled = log.disabled
    log.setLevel(logging.WARNING)
    log.disabled = False
    log.addHandler(capture)
    try:
        resp = client.get(URL)
    finally:
        log.removeHandler(capture)
        log.disabled = old_disabled
        log.setLevel(old_level)
    return resp


def _warning_records(capture):
    return [record for record in capture.records if record.levelno == logging.WARNING]


def _captured_text(capture):
    """Everything the captured log says: the message of every record,
    its exception text and its arguments."""
    parts = []
    for record in capture.records:
        parts.append(record.getMessage())
        parts.append(str(getattr(record, "exc_text", None) or ""))
        parts.append(str(record.args))
    return "\n".join(parts)


def _record_name(record):
    message = record.getMessage()
    assert message.startswith("exceptions: source ")
    assert message.endswith(" failed: RuntimeError")
    return message[len("exceptions: source ") : -len(" failed: RuntimeError")]


def _appended_name(handler):
    """The name one except handler appends to sources_failed, if any."""
    for node in ast.walk(handler):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "sources_failed"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node.args[0].value
    return None


def _warning_name(handler):
    """The source name the logger.warning call of one except handler
    passes, if any: the call must use the fixed format string and
    pass the name as a literal argument."""
    for node in ast.walk(handler):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "warning"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            continue
        args = list(node.args)
        if (
            len(args) == 3
            and isinstance(args[0], ast.Constant)
            and args[0].value == WARNING_FORMAT
            and isinstance(args[1], ast.Constant)
            and isinstance(args[1].value, str)
        ):
            return args[1].value
    return None


def _source_blocks():
    """The except blocks of the route's source: the module's source
    (the source of the route) is read and parsed, and every except
    handler that appends a name to sources_failed is mapped to the
    name its logger.warning call passes in that same handler."""
    source = Path(exceptions.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    blocks = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            name = _appended_name(handler)
            if name is None:
                continue
            assert name not in blocks, name
            blocks[name] = _warning_name(handler)
    return blocks


def test_issue1596_freeform(client, monkeypatch):
    repo = ErpSourceCurrencyPostgresRepository()
    _seed(repo)

    # -- Quiet when healthy: no source failing, no WARNING record. --
    capture = _LogCapture()
    resp = _get_with_capture(client, capture)
    assert resp.status_code == 200
    body = resp.json()
    assert body["sources_failed"] == []
    # The seeded data is answered: one currency-mismatch row.
    assert [row["key"] for row in _currency_rows(body)] == [
        "erp_currency_mismatch:odoo:EUR"
    ]
    assert _warning_records(capture) == []
    assert capture.records == []

    # -- One warning per failed source: the currencies source raises
    #    RuntimeError("secret-text") the way test_issue1019 makes it
    #    fail. --------------------------------------------------------
    monkeypatch.setattr(
        exceptions, "_erp_source_currencies_repo", _RaisingCurrenciesRepo()
    )
    capture = _LogCapture()
    resp = _get_with_capture(client, capture)
    assert resp.status_code == 200
    body = resp.json()
    assert "currencies" in body["sources_failed"]
    assert _RaisingCurrenciesRepo.list_calls == 1

    warnings = _warning_records(capture)
    assert len(warnings) == 1
    record = warnings[0]
    assert record.getMessage() == ("exceptions: source currencies failed: RuntimeError")
    # No message: the secret never reaches the log, in the message,
    # in any argument or in exception text.
    assert "secret-text" not in _captured_text(capture)
    # No exception info on the record.
    assert record.exc_info is None
    assert not getattr(record, "exc_text", None)

    # -- Two sources: currencies and the clashes source both fail —
    #    exactly two such records, one naming each, in the order the
    #    names appear in sources_failed. -------------------------------
    monkeypatch.setattr(exceptions, "_erp_code_clashes_repo", _RaisingClashesRepo())
    capture = _LogCapture()
    resp = _get_with_capture(client, capture)
    assert resp.status_code == 200
    body = resp.json()
    failed = body["sources_failed"]
    assert "currencies" in failed
    assert "clashes" in failed

    warnings = _warning_records(capture)
    assert len(warnings) == 2
    record_names = [_record_name(record) for record in warnings]
    assert sorted(record_names) == ["clashes", "currencies"]
    # One naming each, in the order the names appear in sources_failed.
    assert record_names == [
        name for name in failed if name in ("clashes", "currencies")
    ]
    # No message: neither secret reaches the log.
    assert "secret-text" not in _captured_text(capture)
    assert "another-secret" not in _captured_text(capture)
    for record in warnings:
        assert record.exc_info is None
        assert not getattr(record, "exc_text", None)

    # -- Every block: the route's source shows each of the sixteen
    #    names passed to a logger.warning call in the block that
    #    appends it. ----------------------------------------------------
    blocks = _source_blocks()
    assert sorted(blocks) == sorted(SOURCES)
    for name in SOURCES:
        assert blocks[name] == name
