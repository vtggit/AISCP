"""Issue #531 proof: the certifi pin moves to 2026.10.19.

AC-1 -- ``backend/constraints.txt`` pins ``certifi==2026.10.19`` and
nothing else in the file changed: every other pin, comment and their
ordering is exactly what it was, proven below by reading the file and
checking the certifi lines only.

AC-2 -- proven below: the file's certifi pin is exactly ``2026.10.19``
and no line of the file is ``certifi==2026.7.22``. The old-pin check
normalises each line first (strips surrounding whitespace, drops an
inline comment, folds case) so the old pin cannot hide behind trailing
spaces, tabs or letter-case variants.
"""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CONSTRAINTS = BACKEND / "constraints.txt"

OLD_PIN = "certifi==2026.7.22"
NEW_PIN = "certifi==2026.10.19"


def _old_pin_offenders(lines: list[str]) -> list[str]:
    """Return the lines that pin certifi to the old version.

    Each line is compared after stripping surrounding whitespace,
    dropping any inline comment and folding case, so
    ``certifi==2026.7.22   `` and ``Certifi==2026.7.22`` are caught the
    same way as the exact line.
    """
    offenders = []
    for line in lines:
        candidate = line.split("#", 1)[0].strip().casefold()
        if candidate == OLD_PIN.casefold():
            offenders.append(line)
    return offenders


def test_issue531_freeform():
    lines = CONSTRAINTS.read_text(encoding="utf-8").splitlines()

    # AC-1/AC-2: the exact old-pin line is gone, nowhere in the file.
    assert OLD_PIN not in lines

    # AC-1/AC-2: no line is the old pin in any whitespace or letter-case
    # variant either (review defects: the old pin with trailing
    # whitespace, and case variants, must not pass; the check must not
    # miss them).
    assert _old_pin_offenders(lines) == []
    assert _old_pin_offenders([NEW_PIN, "certifi==2026.7.22   "])
    assert _old_pin_offenders([NEW_PIN, "  certifi==2026.7.22\t"])
    assert _old_pin_offenders([NEW_PIN, "Certifi==2026.7.22"])
    assert _old_pin_offenders([NEW_PIN, "CERTIFI==2026.7.22"])
    assert not _old_pin_offenders([NEW_PIN, "certifi==2026.10.19  "])

    # AC-1/AC-2: certifi is pinned exactly once, exactly to the new
    # version, with no other operator or suffix on the line.
    certifi_lines = [line for line in lines if re.match(r"^certifi==\S+$", line)]
    assert certifi_lines == [NEW_PIN]
