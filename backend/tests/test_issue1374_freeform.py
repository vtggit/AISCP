"""Issue #1374 proof: the nine backend-only API modules wear their parity mark.

Each of the nine machine-facing modules under ``app/api`` — the demand,
lead-time, on-hand, item, OData and change feeds that the ERP or the
forecast/recommendation pipeline fills, not this codebase's own write
routes — carries exactly one ``# parity: backend-only <reason>`` line
between its module docstring and its first import, one blank line on
each side, the reason word for word as pinned below.  Nothing else in
those files moved: each still compiles, each module docstring is the
base-commit one (its first line pinned as a literal), and the FastAPI
app still serves exactly the path set ``backend/openapi.json`` lists —
no route, model or schema change, so the schema file and the reference
docs stand as they are.
"""

import ast
import json
import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
API_DIR = BACKEND_ROOT / "app" / "api"
OPENAPI_PATH = BACKEND_ROOT / "openapi.json"

TRACKER = re.compile(r"#\s*parity:\s*backend-only\b\s*(.*)")

# filename -> (the tracker's reason, the module docstring's first line)
FILES = {
    "demand_events.py": (
        "raw demand events, the input the forecast reads",
        "Read-only API for demand_events.",
    ),
    "demand_stats.py": (
        "demand statistics the forecast and the reorder points read",
        "Read-only API for demand statistics per item and site.",
    ),
    "inventory_items.py": (
        "item master data kept in step with the ERP, the system of record",
        "CRUD API for inventory_items.",
    ),
    "item_sites.py": (
        "item-site master data kept in step with the ERP, the system of record",
        "Read-only API for item_sites.",
    ),
    "lead_time_observations.py": (
        "raw lead-time observations, the input the lead-time statistics read",
        "Read-only API for lead_time_observations.",
    ),
    "lead_time_stats.py": (
        "lead-time statistics the reorder points and the supplier figures read",
        "Read-only API for lead time statistics per item and supplier.",
    ),
    "odata.py": (
        "the OData feed for BI tools and other machine consumers",
        "Machine-readable OData feed for the ranked recommendation sets.",
    ),
    "on_hand.py": (
        "on-hand balances the recommendations read",
        "Read-only API for on_hand.",
    ),
    "recommendation_changes.py": (
        "reorder-point changes for machine consumers and the CSV export",
        "Read-only API for reorder-point changes between two dates.",
    ),
}


def test_issue1374_freeform(client):
    """The nine parity marks sit where the tracker says, and nothing else moved."""
    for filename, (reason, docstring_first_line) in FILES.items():
        path = API_DIR / filename
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()

        # Exactly one line matches the tracker's pattern, and its
        # captured reason is the pinned reason, word for word.
        matches = [index for index, line in enumerate(lines) if TRACKER.search(line)]
        assert len(matches) == 1, (filename, matches)
        (comment_index,) = matches
        assert TRACKER.search(lines[comment_index]).group(1) == reason, filename

        # The file still compiles and its module docstring is the
        # base-commit one: the first line, pinned from the file as it
        # stands, is the docstring's first line.
        compile(text, str(path), "exec")
        tree = ast.parse(text)
        docstring = ast.get_docstring(tree)
        assert docstring is not None, filename
        assert docstring.splitlines()[0] == docstring_first_line, filename

        # The mark sits between the docstring's closing line and the
        # first import, with exactly one blank line on each side.
        first_statement = tree.body[0]
        assert (
            isinstance(first_statement, ast.Expr)
            and isinstance(first_statement.value, ast.Constant)
            and isinstance(first_statement.value.value, str)
        ), filename
        docstring_end = first_statement.end_lineno  # 1-based closing line
        first_import = min(
            node.lineno
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        )
        assert comment_index == docstring_end + 1, filename  # 0-based
        assert lines[docstring_end] == "", filename  # blank line before
        assert lines[comment_index + 1] == "", filename  # blank line after
        assert first_import == docstring_end + 4, filename

    # The app still serves exactly the path set the schema file lists.
    response = client.get("/openapi.json")
    assert response.status_code == 200
    live_paths = set(response.json()["paths"])
    schema_paths = set(json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))["paths"])
    assert live_paths == schema_paths
