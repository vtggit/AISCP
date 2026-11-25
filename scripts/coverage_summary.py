#!/usr/bin/env python3
"""Turn the backend's coverage.xml into a Markdown summary for the CI job page.

    python3 scripts/coverage_summary.py backend/coverage.xml >> "$GITHUB_STEP_SUMMARY"

It prints the total, the coverage of each package under ``app/`` and the least-covered
modules, so a reader can see where the suite is thin without downloading the report.
Coverage is reported here, not enforced. A missing or unreadable report prints a short
note and exits 0, so this step never hides the test step's own result.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

LEAST_COVERED = 15  # modules listed in the "least covered" table
MIN_STATEMENTS = 50  # smaller modules are left out of that table


def _pct(covered: int, total: int) -> str:
    return f"{100.0 * covered / total:.1f}%" if total else "n/a"


def summarise(path: str) -> str:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        return (
            f"## Backend coverage\n\nNo coverage report ({exc.__class__.__name__}).\n"
        )

    files: dict[str, tuple[int, int]] = {}
    for cls in root.iter("class"):
        # coverage.py names files relative to the measured package (app/)
        name = (cls.get("filename") or "").replace("\\", "/").removeprefix("app/")
        lines = cls.findall("./lines/line")
        hit = sum(1 for line in lines if int(line.get("hits", "0")) > 0)
        files[name] = (hit, len(lines))

    covered = sum(h for h, _ in files.values())
    total = sum(n for _, n in files.values())
    out = [
        "## Backend coverage",
        "",
        f"**{_pct(covered, total)}** of {total:,} statements in `app/` "
        f"({covered:,} run by the suite, {len(files)} modules). Reported, not enforced.",
        "",
        "| Package | Coverage | Statements |",
        "|---|---:|---:|",
    ]
    packages: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for name, (hit, n) in files.items():
        parts = name.split("/")
        package = f"app/{parts[0]}" if len(parts) > 1 else "app/ (top-level modules)"
        packages[package][0] += hit
        packages[package][1] += n
    ranked = sorted(packages.items(), key=lambda kv: kv[1][0] / max(kv[1][1], 1))
    for package, (hit, n) in ranked:
        if n:
            out.append(f"| `{package}` | {_pct(hit, n)} | {n:,} |")

    small = [(name, hit, n) for name, (hit, n) in files.items() if n >= MIN_STATEMENTS]
    small.sort(key=lambda row: (row[1] / row[2], -row[2]))
    out += [
        "",
        f"Least covered modules (at least {MIN_STATEMENTS} statements):",
        "",
        "| Module | Coverage | Statements not run |",
        "|---|---:|---:|",
    ]
    for name, hit, n in small[:LEAST_COVERED]:
        out.append(f"| `app/{name}` | {_pct(hit, n)} | {n - hit:,} |")
    out += [
        "",
        "The full report is the `backend-coverage` artifact (`coverage.xml`).",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: coverage_summary.py <coverage.xml>", file=sys.stderr)
        return 2
    print(summarise(argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
