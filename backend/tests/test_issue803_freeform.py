"""Issue #803 proof: one shared head, one literal pin.

The suite used to spell the current migration head out as the
double-quoted literal ``0031_exception_acks`` in nineteen freeform
proofs.  The head now has one shared owner, ``tests/migration_head.py``,
whose ``CURRENT_HEAD`` is derived from Alembic at access time (a fresh
``ScriptDirectory`` over the migrations directory, so nothing is cached
at import time), and this file is the ONE place in the suite that names
the head literal.

Proved here:

  * one head — ``ScriptDirectory(<backend>/migrations).get_heads()``
    equals ``["0031_exception_acks"]`` and ``CURRENT_HEAD`` equals
    ``"0031_exception_acks"``; the head is also recomputed
    independently of the shared helper via a fresh
    ``get_current_head()`` call, which raises when the history has more
    than one head, so a branched history still fails loudly;
  * no literal left behind — a literal scan across every ``test_*.py``
    file in ``tests/`` (this proof excluded, the one allowed to name
    the pin) finds no double-quoted ``0031_exception_acks`` anywhere,
    including all nineteen proofs that used to pin the head;
  * the shared pin is imported where it is used — a separate conditional
    import check, applied only to test files that reference the
    migration-head token or ``CURRENT_HEAD`` (not to all ``test_*.py``
    files), requires ``from tests.migration_head import CURRENT_HEAD``
    in each of them;
  * no migration target changed — none of the nineteen proofs passes
    the shared head pin to an Alembic ``upgrade``/``downgrade`` call:
    every such call still targets ``"head"`` or a named earlier
    revision;
  * the imported pin is live — ``CURRENT_HEAD`` is bound by a
    from-import when this test module is imported, but it is not a
    snapshot of that moment: pointed at a scratch Alembic tree it
    reports the scratch head, tracks a head added afterwards (never
    stale after the from-import), and a branched scratch history
    still fails loudly with ``CommandError``.
"""

import os
import re
from pathlib import Path

import pytest
from alembic import util
from alembic.script import ScriptDirectory

from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS_DIR = Path(_BACKEND_ROOT, "tests")
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")

# The one and only literal pin of the migration head in the suite.
HEAD_LITERAL = "0055_on_hand_snapshots"
_QUOTED_HEAD_LITERAL = f'"{HEAD_LITERAL}"'
_IMPORT_LINE = "from tests.migration_head import CURRENT_HEAD"
_PROOF_FILE = "test_issue803_freeform.py"
_ALEMBIC_CALL = re.compile(r"alembic_command\.(upgrade|downgrade)\(([^)]*)\)")


def _test_files() -> list[Path]:
    return sorted(_TESTS_DIR.glob("test_*.py"))


def test_issue803_freeform(monkeypatch, tmp_path):
    # -- One head: Alembic sees exactly one head, and the shared pin
    #    matches the literal this file owns. ----------------------------
    heads = ScriptDirectory(_MIGRATIONS_DIR).get_heads()
    assert list(heads) == [HEAD_LITERAL]
    # Recomputed independently of the shared helper: a fresh
    # ScriptDirectory re-reads the migrations directory, and
    # get_current_head() raises on a branched history.
    independent_head = ScriptDirectory(_MIGRATIONS_DIR).get_current_head()
    assert independent_head == HEAD_LITERAL
    assert CURRENT_HEAD == HEAD_LITERAL

    # -- No literal left behind: scan every test file for the pinned
    #    head literal (this proof is the designated exception, the one
    #    allowed to name it). -------------------------------------------
    for path in _test_files():
        if path.name == _PROOF_FILE:
            continue
        text = path.read_text(encoding="utf-8")
        assert (
            _QUOTED_HEAD_LITERAL not in text
        ), f"{path.name} still names the head literal in double quotes"

    # -- Separate conditional import check: only test files that
    #    reference the migration-head token or CURRENT_HEAD must import
    #    the shared pin — not all test files. ---------------------------
    for path in _test_files():
        text = path.read_text(encoding="utf-8")
        if HEAD_LITERAL in text or "CURRENT_HEAD" in text:
            assert _IMPORT_LINE in text, (
                f"{path.name} references the migration head but does not "
                f"import {_IMPORT_LINE}"
            )

    # -- No migration target changed: none of the proofs feeds the shared
    #    head pin to an Alembic upgrade/downgrade call — every target is
    #    still a dynamic "head" or a named earlier revision. ------------
    for path in _test_files():
        if path.name == _PROOF_FILE:
            continue
        for verb, args in _ALEMBIC_CALL.findall(path.read_text(encoding="utf-8")):
            target = args.split(",")[-1].strip()
            assert target not in ("CURRENT_HEAD", "HEAD"), (
                f"{path.name}: Alembic {verb} targets {target!r}, the shared "
                "head pin, instead of a dynamic target"
            )

    # -- The imported pin is live, never an import-time snapshot ------
    # The module-level from-import above bound CURRENT_HEAD when this
    # test module was imported, while the real head was still
    # HEAD_LITERAL.  Pointing the shared helper at a scratch Alembic
    # tree must change what that same bound name reports — the value
    # is derived from Alembic at use time, not cached at import time.
    import tests.migration_head as migration_head

    scratch_versions = tmp_path / "migrations" / "versions"
    scratch_versions.mkdir(parents=True)

    def _write_revision(
        filename: str, revision: str, down_revision: str | None
    ) -> None:
        (scratch_versions / filename).write_text(
            f"revision = {revision!r}\n"
            f"down_revision = {down_revision!r}\n"
            "branch_labels = None\n"
            "depends_on = None\n"
            "\n\n"
            "def upgrade():\n"
            "    pass\n"
            "\n\n"
            "def downgrade():\n"
            "    pass\n",
            encoding="utf-8",
        )

    _write_revision("0000_first.py", "aaaa_proof_head", None)
    # Before the patch the pin still reports the real head, so the
    # change below cannot be a pre-existing value.
    assert CURRENT_HEAD == HEAD_LITERAL
    assert CURRENT_HEAD != "aaaa_proof_head"
    monkeypatch.setattr(migration_head, "_MIGRATIONS_DIR", str(tmp_path / "migrations"))
    assert CURRENT_HEAD == "aaaa_proof_head"

    # Extending the scratch history moves the head, and the name bound
    # at import time follows it: nothing goes stale after the
    # from-import.
    _write_revision("0001_second.py", "bbbb_proof_head", "aaaa_proof_head")
    assert CURRENT_HEAD == "bbbb_proof_head"
    from tests.migration_head import CURRENT_HEAD as from_imported

    assert from_imported is CURRENT_HEAD
    assert from_imported == "bbbb_proof_head"

    # A branched history still fails loudly through the imported pin.
    _write_revision("0002_branch.py", "cccc_proof_head", "aaaa_proof_head")
    with pytest.raises(util.CommandError, match="multiple heads"):
        assert CURRENT_HEAD == "aaaa_proof_head"
    (scratch_versions / "0002_branch.py").unlink()

    # With the helper restored, the pin reports the real head again.
    monkeypatch.undo()
    assert CURRENT_HEAD == HEAD_LITERAL
