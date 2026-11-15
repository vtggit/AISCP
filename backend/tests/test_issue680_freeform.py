"""Issue #680 proof: Test 221's snooze date survives a midnight.

The #221 proof pins its snooze date in the module-level
``SNOOZE_UNTIL`` at import time.  A pin of today+1 dies the moment
the suite runs across midnight (the date is then in the past for
the endpoint's "must be in the future" rule), so the proof fails
for no reason.  #680 re-pins that one line to today+7, inside
every endpoint's maximum snooze (30 days for stockout decisions,
90 for the others), with enough slack on both sides that the pin
survives a midnight: it is still at least 2 days ahead, and at
most 30 days ahead, of the day the check runs.

This proof imports the #221 test as a module (importlib, by its
file path next to this file) and checks the pin against
``date.today()``: ``date.fromisoformat(module.SNOOZE_UNTIL)`` must
be at least 2 days after, and at most 30 days after, the day this
test runs.

It also re-executes the pinned line with ``date.today()`` forced
within 7 days of ``date.max``: the pin's date arithmetic must not
overflow there (an unguarded ``today + 7 days`` raises
``OverflowError`` inside the last 7 days of the proleptic
calendar), and the pinned date must remain valid — never before
``today`` and never more than 7 days after it.
"""

import importlib.util
from datetime import date, timedelta
from pathlib import Path

PIN_PATH = Path(__file__).with_name("test_issue221_freeform.py")

SPEC = importlib.util.spec_from_file_location("issue221_freeform", PIN_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

SNOOZE_DAYS = 7


def _pinned_line() -> str:
    """The module-level ``SNOOZE_UNTIL = ...`` assignment in #221."""
    return next(
        line
        for line in PIN_PATH.read_text(encoding="utf-8").splitlines()
        if line.startswith("SNOOZE_UNTIL = ")
    )


def _fake_date(today: date) -> type[date]:
    """A ``date`` subclass whose ``today()`` answers the given day."""

    class _PinnedToday(date):
        @classmethod
        def today(cls) -> date:
            return today

    return _PinnedToday


def test_issue680_freeform():
    snooze_until = date.fromisoformat(MODULE.SNOOZE_UNTIL)
    today = date.today()
    assert 2 <= (snooze_until - today).days <= 30

    line = _pinned_line()
    for offset in (8, 7, 1):
        fake_today = date.max - timedelta(days=offset)
        namespace = {"date": _fake_date(fake_today), "timedelta": timedelta}
        exec(compile(line, str(PIN_PATH), "exec"), namespace)
        value = date.fromisoformat(namespace["SNOOZE_UNTIL"])
        assert fake_today <= value <= date.max
        assert (value - fake_today).days <= SNOOZE_DAYS
