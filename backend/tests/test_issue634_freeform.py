"""Issue #634 proof: the 0.2.0 release cut.

Every place that states the version agrees on 0.2.0 and nothing else
moves:

  * the VERSION file reads 0.2.0,
  * app.config.APP_VERSION and app.main.APP_VERSION are "0.2.0",
  * the FastAPI application's version is "0.2.0",
  * backend/openapi.json's info.version is "0.2.0",
  * CHANGELOG.md has exactly one "## [0.2.0] - 2026-10-25" heading,
    preceded by the "## [Unreleased]" heading.
"""

import json
import re
from pathlib import Path

from app.config import APP_VERSION as CONFIG_APP_VERSION
from app.main import APP_VERSION as MAIN_APP_VERSION
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_HEADING = re.compile(r"^## \[0\.2\.0\] - 2026-10-25$")


def test_issue634_freeform() -> None:
    assert (REPO_ROOT / "VERSION").read_text().strip() == "0.2.0"
    assert CONFIG_APP_VERSION == "0.2.0"
    assert MAIN_APP_VERSION == "0.2.0"
    assert app.version == "0.2.0"

    schema = json.loads((REPO_ROOT / "backend" / "openapi.json").read_text())
    assert schema["info"]["version"] == "0.2.0"

    lines = (REPO_ROOT / "CHANGELOG.md").read_text().splitlines()
    release_indices = [i for i, line in enumerate(lines) if RELEASE_HEADING.match(line)]
    assert len(release_indices) == 1
    release_index = release_indices[0]
    earlier_headings = [i for i in range(release_index) if lines[i].startswith("## ")]
    assert earlier_headings, "no heading precedes the 0.2.0 release heading"
    assert lines[earlier_headings[-1]] == "## [Unreleased]"
