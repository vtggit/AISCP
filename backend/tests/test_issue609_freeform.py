"""Issue #609 proof: the two freeform proofs no longer freeze the vision.

``docs/PRODUCT-VISION.md`` has moved on since the #444 and #502 proofs
were written: "Execution: release and tracking" now sits between
"Aggregate planning" and "Comparative-research method", so a proof that
pins the heading directly after aggregate planning — or, in #502, the
whole file byte for byte — freezes the document against any such
growth. This test checks that the two amended proofs no longer do:

  * the document contains "## Execution: release and tracking"
    between "## Aggregate planning" and "## Comparative-research
    method";
  * neither proof file contains the whole-file pin (``EXPECTED_DOC``)
    or the directly-after placement assertion (``headings[index + 1]``);
  * the two amended proofs pass when run in-process, with this
    suite's fixtures, on their own node ids.
"""

import os
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOC = BACKEND_ROOT.parent / "docs" / "PRODUCT-VISION.md"
PROOF_FILES = (
    BACKEND_ROOT / "tests" / "test_issue444_freeform.py",
    BACKEND_ROOT / "tests" / "test_issue502_freeform.py",
)
NODE_IDS = (
    "tests/test_issue444_freeform.py::test_issue444_freeform",
    "tests/test_issue502_freeform.py::test_issue502_freeform",
)
_ENV_KEYS = (
    "AIERP_TEST_DB",
    "DB_HOST",
    "DB_PORT",
    "DB_USER",
    "DB_PASSWORD",
    "DB_NAME",
    "AUTH_MODE",
    "AUTH_DEV_TOKEN",
)


def _heading_index(doc: str, prefix: str) -> int:
    """The index of the first level-2 heading line starting with *prefix*."""
    headings = [line for line in doc.splitlines() if line.startswith("## ")]
    for index, heading in enumerate(headings):
        if heading.startswith(prefix):
            return index
    raise AssertionError(f"no level-2 heading starts with {prefix!r}")


def _assert_proofs_pass() -> None:
    """Run the two amended proofs in-process on a scratch database.

    The suite's session fixture creates and drops the test database
    once per pytest session, and a nested pytest session would drop
    this session's database mid-run. Pointing the fixture at a
    scratch database for the duration of the nested run — and
    restoring the fixture's database name and the environment
    afterwards — keeps the outer session's database untouched.
    """
    import tests.conftest as suite_conftest

    saved_db_name = suite_conftest._TEST_DB_NAME
    saved_env = {key: os.environ.get(key) for key in _ENV_KEYS}
    scratch = f"{saved_db_name}_issue609"
    try:
        suite_conftest._TEST_DB_NAME = scratch
        os.environ["AIERP_TEST_DB"] = scratch
        exit_code = pytest.main(
            [f"{BACKEND_ROOT / node_id}" for node_id in NODE_IDS]
            + ["-p", "no:cacheprovider", "-q"]
        )
    finally:
        suite_conftest._TEST_DB_NAME = saved_db_name
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert (
        exit_code == pytest.ExitCode.OK
    ), "the amended #444 and #502 proofs must pass in-process"


def test_issue609_freeform():
    doc = DOC.read_text(encoding="utf-8")

    # 1. "## Execution: release and tracking" sits between
    #    "## Aggregate planning" and "## Comparative-research method".
    aggregate = _heading_index(doc, "## Aggregate planning")
    execution = _heading_index(doc, "## Execution: release and tracking")
    comparative = _heading_index(doc, "## Comparative-research method")
    assert aggregate < execution < comparative, (
        "'## Execution: release and tracking' must sit between "
        "'## Aggregate planning' and '## Comparative-research method'"
    )

    # 2. The two proofs no longer freeze the document: no whole-file
    #    pin and no directly-after placement assertion.
    for path in PROOF_FILES:
        text = path.read_text(encoding="utf-8")
        assert (
            "EXPECTED_DOC" not in text
        ), f"{path.name} still pins the whole vision file"
        assert "headings[index + 1]" not in text, (
            f"{path.name} still asserts the heading directly after the "
            "aggregate planning heading"
        )

    # 3. The two amended proofs pass in-process, with this suite's
    #    fixtures, on their own node ids.
    _assert_proofs_pass()
