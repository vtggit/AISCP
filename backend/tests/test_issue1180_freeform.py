"""Issue #1180 proof: the opentelemetry-api pin in the constraints file.

The backend image and CI install with ``-c constraints.txt`` so every
environment gets the same exact versions; a drift in that single pin is
enough to change what the image builds. This proof reads
``backend/constraints.txt`` and checks the opentelemetry-api entry
directly: it must be pinned exactly to 1.45.2 and no line of the file may
still pin the old 1.45.1 release.
"""

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CONSTRAINTS = BACKEND / "constraints.txt"


def test_issue1180_freeform():
    lines = [
        line
        for line in CONSTRAINTS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert "opentelemetry-api==1.45.2" in lines
    assert "opentelemetry-api==1.45.1" not in lines
