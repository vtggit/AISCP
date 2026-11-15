"""The shared migration head for the AIERP backend test suite.

Issue #803: nineteen freeform proofs each spelled the current migration
head out as a double-quoted revision literal.  This module is the
single owner of that pin: the proofs import ``CURRENT_HEAD`` from here
instead of naming the revision, and the one literal pin itself lives
only in ``tests/test_issue803_freeform.py``.

``CURRENT_HEAD`` is never a value cached at import time.  It is a
``str`` whose value is derived from Alembic on every access: each
comparison, ``str()``, ``repr()`` or format call rebuilds a fresh
:class:`~alembic.script.ScriptDirectory` over the migrations directory
and calls ``get_current_head()`` on it.  A name bound by
``from tests.migration_head import CURRENT_HEAD`` therefore always
reflects the migration head present in the test environment at the
moment of use, no matter when the importing test module was imported
— it can never go stale.  ``get_current_head()`` raises when the
history has more than one head, so a branched history still fails
loudly (through any use of the pin).
"""

import os

from alembic.script import ScriptDirectory

__all__ = ["CURRENT_HEAD", "current_head"]

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")


def current_head() -> str:
    """Derive the current migration head from Alembic at call time.

    A fresh ``ScriptDirectory`` re-reads the migrations directory, so
    the result reflects the head present now rather than one cached at
    import time.  Raises when the history has more than one head.
    """
    return ScriptDirectory(_MIGRATIONS_DIR).get_current_head()


class _CurrentHead(str):
    """A ``str`` that resolves its value from Alembic on every use.

    Binding this object (e.g. ``from tests.migration_head import
    CURRENT_HEAD``) never snapshots a revision: every comparison or
    string conversion calls :func:`current_head` again, so the bound
    name always agrees with the head Alembic sees at that moment.
    """

    def __new__(cls):
        return super().__new__(cls, "")

    def __eq__(self, other):
        if isinstance(other, str):
            return current_head() == other
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self):
        return hash(current_head())

    def __str__(self):
        return current_head()

    def __repr__(self):
        return f"{type(self).__name__}({current_head()!r})"

    def __format__(self, spec):
        return format(current_head(), spec)


CURRENT_HEAD = _CurrentHead()
