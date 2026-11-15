"""Issue #590 proof: run_tests.sh manages its own PostgreSQL with plain docker.

Reads backend/run_tests.sh as text (path relative to this test file) and checks:
  * it contains no `docker compose`, `docker-compose` or `COMPOSE_FILE`,
  * it contains `docker run`, `postgres:15`, `aierp-test-db`, `5433:5432`,
    `pg_isready` and `docker rm -f aierp-test-db`,
  * it still contains `trap cleanup EXIT`,
  * the readiness wait is bounded at a full 30 seconds (30 polls, with the
    last failed poll followed by its one-second sleep before the timeout).

When a `shellcheck` binary is on the path, the script is also checked with it
(expected exit status 0); that check is skipped otherwise.
"""

import shutil
import subprocess
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "run_tests.sh"

_FORBIDDEN_MARKERS = (
    "docker compose",
    "docker-compose",
    "COMPOSE_FILE",
)

_REQUIRED_MARKERS = (
    "docker run",
    "postgres:15",
    "aierp-test-db",
    "5433:5432",
    "pg_isready",
    "docker rm -f aierp-test-db",
)


def _readiness_block(script: str) -> str:
    """Text of the readiness-wait section of the script."""
    start = script.index("pg_isready")
    end = script.index("# Run pytest")
    assert start < end
    return script[start:end]


def test_issue590_freeform():
    script = _SCRIPT_PATH.read_text(encoding="utf-8")

    for marker in _FORBIDDEN_MARKERS:
        assert marker not in script, f"run_tests.sh must not contain {marker!r}"

    for marker in _REQUIRED_MARKERS:
        assert marker in script, f"run_tests.sh must contain {marker!r}"

    assert "trap cleanup EXIT" in script

    block = _readiness_block(script)
    assert "seq 1 30" in block, "readiness wait must poll 30 times"
    assert "sleep 1" in block, "readiness wait must sleep one second per poll"
    # A full 30-second bound: the last failed poll must be followed by its
    # one-second sleep, i.e. the timeout check on iteration 30 comes after
    # the sleep (sleeping only 29 times would give a 29-second timeout).
    assert block.index("sleep 1") < block.index(
        "-eq 30"
    ), "readiness timeout must span 30 seconds, not 29"

    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        return

    result = subprocess.run(
        [shellcheck, str(_SCRIPT_PATH)],
        capture_output=True,
        text=True,
    )
    assert (
        result.returncode == 0
    ), f"shellcheck failed on run_tests.sh:\n{result.stdout}\n{result.stderr}"
