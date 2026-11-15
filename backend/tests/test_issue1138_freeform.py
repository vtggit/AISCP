r"""Issue #1138 proof: finding the creating revision is a precise call match.

The #1118 proof found the migration that creates
``supplier_order_minimums`` by walking the Alembic history and taking
the first revision whose source text merely contains both
``create_table`` and the table name.  A plain text match also fires
for a revision that only *names* the table — in a docstring, in a
``down_revision`` string, in a foreign key, in a comment (even a
comment that spells the call itself) — and for a different call such
as ``op.drop_table`` or a call creating a differently named table.

This proof checks the replacement, ``_creates_table``, imported from
the #1118 proof module:

  * it is true for a real ``op.create_table`` call whose first
    argument is the table's string literal — single- or
    double-quoted, with a line break between the parenthesis and the
    string;
  * it is false for a source that only names the table in a docstring,
    in a ``down_revision`` string, in a foreign key, in a comment
    (including a comment containing the text of the call itself), for
    a call that creates a differently named table, and for
    ``op.drop_table`` of the table;
  * an expression that merely begins with the table's string literal
    — an implicit concatenation or an operation building a different
    name — never matches;
  * the real history still resolves: ``_creating_revision()`` returns
    the one revision whose module creates the table, its
    ``down_revision`` is the revision before it in the walk and its
    own source passes ``_creates_table``;
  * a revision file that is not valid UTF-8 never crashes the walk:
    it is read leniently, cannot be the revision that creates the
    table, and the walk ends in the plain assertion.
"""

import os
import shutil
import tempfile

import pytest
from alembic.script import ScriptDirectory

import tests.test_issue1118_freeform as issue1118
from tests.test_issue1118_freeform import _creates_table, _creating_revision

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")
_TABLE = "supplier_order_minimums"


def _revision_source(rev) -> str:
    with open(rev.module.__file__, encoding="utf-8") as fh:
        return fh.read()


def test_issue1138_freeform():
    # -- A real call whose first argument is the table's literal. -----
    # Double-quoted with a line break between the parenthesis and the
    # string, and single-quoted on one line: both match.
    assert _creates_table(
        f'op.create_table(\n "{_TABLE}",',
        _TABLE,
    )
    assert _creates_table(
        f"op.create_table('{_TABLE}', sa.Column(...))",
        _TABLE,
    )

    # -- Naming the table is not creating it. ---------------------------
    for source in (
        # The table name only in a docstring.
        f'"""Create the {_TABLE} table."""\n',
        # The table name only in a down_revision string.
        f'down_revision = "{_TABLE}"\n',
        # The table name only in a foreign key.
        f'sa.ForeignKeyConstraint(["x"], ["{_TABLE}.id"])\n',
        # A different table: the literal does not name the table.
        f'op.create_table("{_TABLE}_history")\n',
        # Another call: dropping is not creating.
        f'op.drop_table("{_TABLE}")\n',
        # The table name only in a comment.
        f"# the {_TABLE} table lives in its own revision\n",
        # A comment containing the text of the call itself.
        f'# op.create_table("{_TABLE}",\n',
        f'def upgrade():\n    # the old op.create_table("{_TABLE}", is gone\n    pass\n',
        # An expression that only begins with the literal: the
        # concatenation (or the operation) builds a different name,
        # so the first argument is not the table's string literal.
        f'op.create_table("{_TABLE}" "_history", sa.Column(...))',
        f"op.create_table('{_TABLE}' '_history', sa.Column(...))",
        f'op.create_table("{_TABLE}" + "_history")',
    ):
        assert not _creates_table(source, _TABLE), source

    # -- The real history still resolves to the creating revision. -----
    script = ScriptDirectory(_MIGRATIONS_DIR)
    walk = [rev.revision for rev in script.walk_revisions()]
    creating = _creating_revision()
    # It is a revision of the real history, and its down_revision is
    # the revision before it: the next one the head-to-base walk
    # reaches.
    assert creating.revision in walk
    index = walk.index(creating.revision)
    assert index + 1 < len(walk)
    assert creating.down_revision == walk[index + 1]
    # Its own source passes the precise check, and no other revision
    # in the history does: it is the one that creates the table.
    assert _creates_table(_revision_source(creating), _TABLE)
    for rev in script.walk_revisions():
        if rev.revision == creating.revision:
            continue
        path = getattr(rev.module, "__file__", None)
        if not path:
            continue
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        assert not _creates_table(source, _TABLE), rev.revision

    # -- A revision file that is not valid UTF-8 never crashes the
    #    walk: the source is read leniently, the file cannot hold the
    #    call, and the walk ends in the plain assertion — not a
    #    decode error. ---------------------------------------------
    bad_dir = tempfile.mkdtemp(prefix="issue1138_history_")
    try:
        with open(os.path.join(bad_dir, "0001_bad.py"), "wb") as fh:
            fh.write(
                b"revision = '0001bad'\n"
                b"down_revision = None\n"
                b"\n"
                b"def upgrade():\n"
                b"    # a non-UTF-8 byte here: \xff\xfe\n"
                b"    pass\n"
            )
        saved_dir = issue1118._MIGRATIONS_DIR
        issue1118._MIGRATIONS_DIR = bad_dir
        try:
            with pytest.raises(AssertionError):
                _creating_revision()
        finally:
            issue1118._MIGRATIONS_DIR = saved_dir
    finally:
        shutil.rmtree(bad_dir, ignore_errors=True)
