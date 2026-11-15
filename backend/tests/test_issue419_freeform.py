"""Issue #419 proof: the scenario planning (what-if) section of the vision.

``docs/PRODUCT-VISION.md`` gains a section with the exact heading
``## Scenario planning (what-if) (operator direction 2026-10-16; method
defaults decided by Claude under delegation)``, placed after the
"Demand planning" section and before "Comparative-research method".
The test checks the section body for:

  * what it is: a read-only simulation that re-runs the reorder
    recommendation under one or more overridden assumptions and reports,
    for each item-site and for the portfolio, what changes against the
    live baseline; it writes nothing and makes no decision — a
    planner's "what if" before acting;
  * the baseline: the recommendation each item-site has today, built
    from the active demand basis, the stored service level and the
    observed lead-time statistics. Because the active demand basis can
    be the forecast, the baseline definition names both the history and
    the forecast basis and never unconditionally describes the baseline
    demand as observed;
  * the levers (all optional; an omitted lever keeps the baseline
    value): a target service level, a probability strictly between 0
    and 1 used as ``z = NormalDist().inv_cdf(service_level)``; a
    demand multiplier, a positive number scaling both the mean daily
    demand and its standard deviation; and a lead-time multiplier, a
    positive number scaling both the mean lead time and its standard
    deviation;
  * one formula for baseline and scenario —
    ``safety_stock = z * sqrt(LT * sd_d^2 + d^2 * sd_LT^2)`` and
    ``reorder_point = d * LT + safety_stock`` — so a scenario differs
    from the baseline only by the overridden inputs;
  * what it reports: per item-site the baseline and scenario
    ``reorder_point`` and ``safety_stock`` with their whole-unit values
    and the signed difference, plus a portfolio summary with the total
    change in reorder-point units and safety-stock units, the number of
    item-sites whose reorder point rises, falls, and is unchanged, and
    the dollar impact (the reorder-point-unit change times the
    item-site's ``unit_cost``, item-sites without a unit cost
    contributing zero and counted separately);
  * the scope boundaries: only recommendable item-sites are simulated
    (the same intermittent/lumpy exclusions as recommendations), a
    scenario is never stored and never changes a stored recommendation,
    decision, or setting, and the existing recommendation, drift,
    excess, stockout and demand behaviours are untouched.
"""

import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[2] / "docs" / "PRODUCT-VISION.md"
HEADING = (
    "## Scenario planning (what-if) (operator direction 2026-10-16; "
    "method defaults decided by Claude under delegation)"
)


def _flat(text: str) -> str:
    """Collapse every whitespace run to a single space."""
    return re.sub(r"\s+", " ", text)


def _section_body(doc: str, heading: str) -> str:
    """From just after *heading* to the next level-2 heading."""
    start = doc.index(heading) + len(heading)
    rest = doc[start:]
    match = re.search(r"^## ", rest, flags=re.MULTILINE)
    return rest[: match.start()] if match else rest


def _top_bullet(body: str, name: str) -> str:
    """One top-level '- **name...**' bullet of the flattened section body."""
    marker = f" - **{name}"
    index = body.index(marker)
    rest = body[index + len(marker) :]
    end = rest.find(" - **")
    return rest if end == -1 else rest[:end]


def _has(flat_text: str, phrases: tuple[str, ...]) -> None:
    for phrase in phrases:
        assert phrase.lower() in flat_text.lower(), f"missing phrase: {phrase!r}"


def _has_exact(flat_text: str, phrases: tuple[str, ...]) -> None:
    for phrase in phrases:
        assert phrase in flat_text, f"missing phrase: {phrase!r}"


def test_issue419_freeform():
    doc = DOC.read_text(encoding="utf-8")

    # 1. The exact heading, exactly once.
    assert (
        doc.count(HEADING) == 1
    ), "the scenario planning heading must appear exactly once"

    # 2. Placement: directly after "Demand planning", directly before
    #    "Comparative-research method".
    headings = [line for line in doc.splitlines() if line.startswith("## ")]
    index = headings.index(HEADING)
    assert headings[index - 1].startswith("## Demand planning")
    assert headings[index + 1].startswith("## Aggregate planning")

    body = _flat(_section_body(doc, HEADING))

    # 3. What it is: a read-only simulation, measured against the live
    #    baseline, writing nothing and deciding nothing.
    _has(
        body,
        (
            "read-only simulation",
            "re-runs the reorder recommendation",
            "one or more overridden assumptions",
            "for each item-site and for the portfolio",
            "what changes against the live baseline",
            "writes nothing",
            "makes no decision",
            "what if",
        ),
    )

    # 4. The baseline: the recommendation each item-site has today, on
    #    the active demand basis, the stored service level and the
    #    observed lead-time statistics.
    baseline = _top_bullet(body, "The baseline.")
    _has(
        baseline,
        (
            "the recommendation each item-site has today",
            "active demand basis",
            "stored service level",
            "observed lead-time statistics",
        ),
    )
    # AC-2: the baseline must stay consistent with the active demand
    # basis, which can be the forecast: both bases are named and the
    # baseline is never unconditionally described as observed demand.
    _has(baseline, ("history", "forecast"))
    assert (
        "observed demand" not in baseline.lower()
    ), "the baseline must not unconditionally describe its demand as observed"

    # 5. The levers: all optional, an omitted lever keeps the baseline
    #    value; the three levers with their exact semantics.
    _has(
        body,
        (
            "all optional",
            "an omitted lever keeps the baseline value",
            "service level",
            "strictly between 0 and 1",
            "demand multiplier",
            "scaling both the mean daily demand and its standard deviation",
            "lead-time multiplier",
            "scaling both the mean lead time and its standard deviation",
        ),
    )
    _has_exact(body, ("z = NormalDist().inv_cdf(service_level)",))

    # 6. One formula for baseline and scenario.
    _has(
        body,
        (
            "the same reorder formula is used for baseline and scenario",
            "differs from the baseline only by the overridden inputs",
        ),
    )
    _has_exact(
        body,
        (
            "safety_stock = z * sqrt(LT * sd_d^2 + d^2 * sd_LT^2)",
            "reorder_point = d * LT + safety_stock",
        ),
    )

    # 7. What it reports: per item-site, and the portfolio summary.
    reports = _top_bullet(body, "What it reports.")
    _has(
        reports,
        (
            "baseline and scenario",
            "reorder_point",
            "safety_stock",
            "whole-unit values",
            "signed difference",
        ),
    )
    _has(
        body,
        (
            "portfolio summary",
            "total change in reorder-point units and safety-stock units",
            "rises, falls, and is unchanged",
            "dollar impact",
            "unit_cost",
            "without a unit cost contributing zero and counted separately",
        ),
    )

    # 8. Scope boundaries.
    _has(
        body,
        (
            "only recommendable item-sites are simulated",
            "intermittent and lumpy exclusions",
            "never stored",
            "never changes a stored recommendation, decision, or setting",
            "recommendation, drift, excess, stockout and demand behaviours are untouched",
        ),
    )
