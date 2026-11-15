"""Issue #1184 proof: the old database name is gone from the backend.

The Odoo database the issue 815 proof seeded was renamed to
``demo``, and the proof here walks every file under the backend
root (the directory that holds ``tests/``) — skipping
``__pycache__`` directories and files that are not UTF-8 text —
and asserts that no file still carries the old name in any case.
The old name is built from two string parts below, so this test's
own text never contains it and can never falsify itself.
"""

import os
from pathlib import Path

# The old name, in two parts: this file's own text must never
# contain it whole, or the proof would flag itself.
_OLD_NAME = "gar" + "rison"

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _backend_files():
    """Every file under the backend root, ``__pycache__` pruned."""
    files = []
    for dirpath, dirnames, filenames in os.walk(_BACKEND_ROOT):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for filename in filenames:
            files.append(Path(dirpath) / filename)
    return sorted(files)


def _utf8_text(path):
    """The file's text, or None when it is not UTF-8."""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None


def test_issue1184_freeform():
    old = _OLD_NAME.lower()
    offenders = []
    text_files = 0
    for path in _backend_files():
        text = _utf8_text(path)
        if text is None:
            continue  # not UTF-8 text: out of scope for the proof
        text_files += 1
        if old in text.lower():
            offenders.append(str(path))
    # The walk really saw the tree (it always has text files).
    assert text_files > 0
    assert not offenders, f"old database name still present in: {', '.join(offenders)}"
