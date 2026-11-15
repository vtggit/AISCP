"""Issue #444 proof: the aggregate planning section of the vision.

``docs/PRODUCT-VISION.md`` gains a section with the exact heading
``## Aggregate planning (operator review pending 2026-10-16; proposed by
Claude up the planning stack)``, placed after the "Scenario planning
(what-if)" section and before "Comparative-research method". The test
checks the section body for:

  * what it is: a read-only, aggregate view of the plan — the item-site
    numbers rolled up to a grouping dimension and time buckets, showing
    per group and bucket projected demand against projected supply and
    the projected on-hand that results, so a planner sees where the
    portfolio is short or long over the horizon before acting on any
    single item-site; it writes nothing, stores nothing, and makes no
    decision;
  * the grouping dimension: site to begin with (the warehouse/location
    already on every item-site), and a product family or category when
    the ERP source provides one — keyed by (group, bucket), never
    inventing a hierarchy the source does not carry;
  * the horizon and buckets: a fixed number of forward monthly buckets
    from an as-of date (default six months), each bucket a calendar
    month;
  * what it reports, per group and bucket: projected demand on the
    active demand basis (history or forecast, the same basis the
    recommendations use) summed over the group's item-sites for the
    days in the bucket; projected supply from open purchase-line
    quantities whose promised date falls in the bucket, from the
    open-purchase-supply data already ingested; projected on-hand at
    the end of the bucket (starting on-hand rolled forward: prior
    on-hand plus cumulative supply minus cumulative demand); a short
    coverage signal (projected on-hand below zero in a bucket = a
    projected shortfall; a large positive projected on-hand relative to
    demand = projected excess); the dollar view at the item-site
    ``unit_cost``, item-sites without a unit cost contributing zero and
    counted separately, exactly as scenario planning already does;
  * the projected-excess threshold: not fixed in this vision — an
    instance setting to be decided at the Aggregate planning screen
    slice with the reference dataset in view — while the shortfall
    signal (projected on-hand below zero) remains normative as written;
  * the scope boundaries: it aggregates only the item-sites the
    recommendations already consider (the same intermittent/lumpy
    exclusions), reuses the existing demand-basis, on-hand and
    open-purchase-supply reads with no new data source, and changes no
    stored recommendation, decision, or setting; the existing
    recommendation / drift / excess / stockout / demand / scenario
    behaviours are unchanged;
  * the slice ladder (for later slices, not built here): this vision;
    then a read-only aggregate-projection endpoint in the backend;
    then an Aggregate planning screen in the planner (the group×bucket
    grid with the coverage signal and the dollar view); then, up the
    stack, applying a saved scenario's levers to the aggregate view and
    multi-echelon roll-up across sites.

AC-2 is proven directly: the section claims no write, no stored
artifact, and no decision — every write/store/save/persist/create/
update/insert/mutate/commit/record/decide/determine token in the
section must sit in a negated fine clause (clauses split at commas:
``writes nothing``, ``stores nothing``, ``makes no decision``) or in
one of the exact phrasings the vision itself mandates (``changes no
stored recommendation, decision, or setting``). A negation governs
only its own fine clause: it cannot reach across a comma, so a
negation in an earlier comma-clause cannot over-scope a following
claim, and a negation in a later clause or in a mandated phrase
cannot shield a separate claim — both failure modes are asserted
against below. The projected-demand basis is described as the active
demand basis, history or forecast, never unconditionally as observed
history.

Both checks are negation-aware, so a negated statement can neither
satisfy a required-phrase presence check nor slip past the AC-2
blocklist; the presence check is asserted against negated samples and
the blocklist against common affirmative wording, below.
"""

import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[2] / "docs" / "PRODUCT-VISION.md"
HEADING = (
    "## Aggregate planning (operator direction 2026-10-16; "
    "method defaults decided by Claude under delegation)"
)

# Fine clause boundaries for the presence checks and the AC-2
# blocklist: commas split, so a negation in an earlier comma-clause
# does not govern the phrase or claim in this one.
_CLAUSE_FINE = re.compile(r"[.!?;:,—]")
# Coarse clause boundaries for the observed-history mention check:
# commas do not split.
_CLAUSE_COARSE = re.compile(r"[.!?;:—]")

NEGATION_RE = re.compile(
    r"\b(?:"
    r"no|none|not|never|nothing|nor|neither|without|cannot|can't"
    r"|won't|isn't|aren't|wasn't|weren't|don't|doesn't|didn't"
    r"|hadn't|wouldn't|shouldn't|mustn't"
    r")\b"
)

# Common write / stored-artifact / decision wording. Any occurrence
# outside a negated fine clause or a mandated phrasing is a claim the
# vision must not make (AC-2).
WRITE_STORE_DECISION_RE = re.compile(
    r"\b(?:"
    r"write|writes|writing|written"
    r"|store|stores|stored|storing"
    r"|save|saves|saved|saving"
    r"|persist|persists|persisted|persisting"
    r"|create|creates|created|creating|creation"
    r"|update|updates|updated|updating"
    r"|insert|inserts|inserted|inserting"
    r"|mutate|mutates|mutated|mutating"
    r"|commit|commits|committed|committing"
    r"|record|records|recorded|recording"
    r"|decide|decides|decided|deciding|decision|decisions"
    r"|determine|determines|determined|determining"
    r")\b"
)

# Exact mandated phrasings the vision requires; the write/decision
# wording inside them refers to a later slice, to this document, or
# to the negation the phrasing itself carries, not to a claim of
# aggregate planning. They are matched verbatim, so a negation inside
# a mandated phrase cannot leak across commas into a following claim.
ALLOWED_PHRASES = (
    "changes no stored recommendation, decision, or setting",
    "to be decided at the aggregate planning screen slice",
    "a saved scenario's levers",
    "remains normative as written",
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
    """One top-level '- **name...**' bullet of the flattened section body.

    The bold bullet name may be closed with a period
    ("**Scope boundaries.**") or a colon ("**Scope boundaries:**");
    both spellings of an AC-compliant document are accepted, so the
    lookup never crashes on a period/colon mismatch.
    """
    marker = f" - **{name.rstrip('.:')}"
    index = body.index(marker)
    rest = body[index + len(marker) :]
    end = rest.find(" - **")
    return rest if end == -1 else rest[:end]


def _occurrences(text: str, phrase: str) -> list[int]:
    """Start indexes of every occurrence of *phrase* in lowercased *text*."""
    needle = phrase.lower()
    indexes: list[int] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index == -1:
            return indexes
        indexes.append(index)
        start = index + 1


def _clause_before(text: str, index: int, boundaries: re.Pattern[str]) -> str:
    """The clause text immediately before *index* in lowercased *text*."""
    before = text[:index]
    matches = list(boundaries.finditer(before))
    return before[matches[-1].end() :] if matches else before


def _stated(flat_text: str, phrase: str) -> bool:
    """True when *phrase* is stated in *flat_text*, not merely negated.

    Some occurrence of the phrase must carry no negation marker in its
    preceding fine clause: "it does not <phrase>" — any other negated
    statement of the phrase — does not count as stating it. For a
    phrase that is itself a negated claim ("makes no decision"), the
    same rule rejects a double negation.
    """
    text = flat_text.lower()
    for index in _occurrences(text, phrase):
        if NEGATION_RE.search(_clause_before(text, index, _CLAUSE_FINE)) is None:
            return True
    return False


def _requires_phrases(flat_text: str, phrases: tuple[str, ...]) -> None:
    for phrase in phrases:
        assert phrase.lower() in flat_text.lower(), f"missing phrase: {phrase!r}"
        assert _stated(
            flat_text, phrase
        ), f"phrase is only stated in a negated form: {phrase!r}"


def _clause_span(text: str, start: int, end: int, boundaries: re.Pattern[str]) -> str:
    """The clause of lowercased *text* spanning [start, end)."""
    before = text[:start]
    opens = list(boundaries.finditer(before))
    clause_start = opens[-1].end() if opens else 0
    close = boundaries.search(text, end)
    clause_end = close.start() if close else len(text)
    return text[clause_start:clause_end]


def _forbidden_claims(flat_body: str) -> list[str]:
    """Every write / stored-artifact / decision claim in *flat_body*.

    A token is a claim unless its fine clause (clauses split at
    commas) is negated ("writes nothing", "stores nothing", "makes no
    decision") or the token sits inside one of the exact phrasings
    ALLOWED_PHRASES mandates. A negation in a different comma-clause
    — before or after the token — does not govern it.
    """
    text = flat_body.lower()
    claims: list[str] = []
    for match in WRITE_STORE_DECISION_RE.finditer(text):
        clause = _clause_span(text, match.start(), match.end(), _CLAUSE_FINE)
        if NEGATION_RE.search(clause) is not None:
            continue
        if any(
            index <= match.start() and match.end() <= index + len(phrase)
            for phrase in ALLOWED_PHRASES
            for index in _occurrences(text, phrase)
        ):
            continue
        claims.append(match.group(0))
    return claims


def _unconditional_mentions(flat_body: str, phrases: tuple[str, ...]) -> list[str]:
    """Phrases with an occurrence no earlier negation in the clause covers.

    "observed history" is acceptable only when the vision explicitly
    negates or qualifies it ("never unconditionally as observed
    history"); a bare mention describes the demand basis as that
    phrase unconditionally. Only the coarse-clause text before the
    mention can govern it: a trailing negation ("the observed
    history, not the forecast") does not shield an affirmative use.
    """
    text = flat_body.lower()
    found: list[str] = []
    for phrase in phrases:
        for index in _occurrences(text, phrase):
            before = text[:index]
            opens = list(_CLAUSE_COARSE.finditer(before))
            clause_start = opens[-1].end() if opens else 0
            if NEGATION_RE.search(text[clause_start:index]) is None:
                found.append(phrase)
                break
    return found


def test_issue444_freeform():
    doc = DOC.read_text(encoding="utf-8")

    # 1. The exact heading, exactly once.
    assert (
        doc.count(HEADING) == 1
    ), "the aggregate planning heading must appear exactly once"

    # 2. Placement: directly after "Scenario planning (what-if)" and
    #    before "Comparative-research method" (later sections may sit
    #    in between). The heading must be a line-start heading: a
    #    mid-line occurrence is not a heading, and the check fails
    #    cleanly on one (no ValueError from list.index).
    heading_lines = [line for line in doc.splitlines() if line == HEADING]
    assert (
        len(heading_lines) == 1
    ), "the aggregate planning heading must be exactly one line-start line"
    headings = [line for line in doc.splitlines() if line.startswith("## ")]
    index = headings.index(HEADING)
    assert headings[index - 1].startswith("## Scenario planning (what-if)")
    comparative_index = next(
        (
            i
            for i, heading in enumerate(headings)
            if heading.startswith("## Comparative-research method")
        ),
        -1,
    )
    assert comparative_index > index, (
        "'## Comparative-research method' must come after the aggregate "
        "planning heading"
    )

    body = _flat(_section_body(doc, HEADING))

    # 3. What it is: a read-only, aggregate view of the plan, showing
    #    per group and bucket projected demand against projected supply
    #    and the resulting projected on-hand; writes nothing, stores
    #    nothing, makes no decision.
    _requires_phrases(
        body,
        (
            "read-only, aggregate view of the plan",
            "rolls the item-site numbers up to a grouping dimension and time buckets",
            "per group and bucket, projected demand against projected supply "
            "and the projected on-hand that results",
            "so a planner sees where the portfolio is short or long over the "
            "horizon before acting on any single item-site",
            "writes nothing",
            "stores nothing",
            "makes no decision",
        ),
    )

    # 4. The grouping dimension.
    grouping = _top_bullet(body, "The grouping dimension.")
    _requires_phrases(
        grouping,
        (
            "site to begin with",
            "the warehouse/location already on every item-site",
            "a product family or category when the ERP source provides one",
            "keyed by (group, bucket)",
            "never invents a hierarchy the source does not carry",
        ),
    )

    # 5. The horizon and buckets.
    horizon = _top_bullet(body, "The horizon and buckets.")
    _requires_phrases(
        horizon,
        (
            "a fixed number of forward monthly buckets from an as-of date",
            "default six months",
            "each bucket a calendar month",
        ),
    )

    # 6. What it reports, per group and bucket.
    reports = _top_bullet(body, "What it reports, per group and bucket.")
    # 6a. Projected demand: the active demand basis, summed over the
    #     group's item-sites for the days in the bucket.
    _requires_phrases(
        reports,
        (
            "projected demand",
            "the active demand basis",
            "history or forecast",
            "the same basis the recommendations use",
            "summed over the item-sites in the group for the days in the bucket",
        ),
    )
    # 6b. Projected supply: open purchase lines promised in the bucket.
    _requires_phrases(
        reports,
        (
            "projected supply",
            "open purchase-line quantities whose promised date falls in the bucket",
            "from the open-purchase-supply data already ingested",
        ),
    )
    # 6c. Projected on-hand: starting on-hand rolled forward.
    _requires_phrases(
        reports,
        (
            "projected on-hand at the end of the bucket",
            "the group's starting on-hand rolled forward",
            "prior on-hand plus cumulative supply minus cumulative demand",
        ),
    )
    # 6d. The coverage signal and the unfixed projected-excess threshold.
    _requires_phrases(
        reports,
        (
            "a short coverage signal",
            "projected on-hand below zero in a bucket = a projected shortfall",
            "a large positive projected on-hand relative to demand = projected excess",
            "the projected-excess threshold is not fixed in this vision",
            "an instance setting to be decided at the Aggregate planning "
            "screen slice with the reference dataset in view",
            "the shortfall signal (projected on-hand below zero) remains "
            "normative as written",
        ),
    )
    # 6e. The dollar view, exactly as scenario planning already does.
    _requires_phrases(
        reports,
        (
            "sums each quantity at the item-site `unit_cost`",
            "item-sites without a unit cost contributing zero and counted separately",
            "exactly as scenario planning already does",
        ),
    )

    # 7. Scope boundaries.
    scope = _top_bullet(body, "Scope boundaries.")
    _requires_phrases(
        scope,
        (
            "aggregates only the item-sites the recommendations already consider",
            "the same intermittent/lumpy exclusions",
            "reuses the existing demand-basis, on-hand and open-purchase-supply reads",
            "no new data source",
            "changes no stored recommendation, decision, or setting",
            "the existing recommendation / drift / excess / stockout / demand / "
            "scenario behaviours are unchanged",
        ),
    )

    # 9. AC-2: the section claims no write, no stored artifact, and no
    #    decision. Common write/stored/decision wording is a claim
    #    unless it is negated or one of the mandated phrasings.
    _requires_phrases(body, ("read-only",))

    # 10. AC-2: the projected-demand basis is the active demand basis —
    #     history or forecast — never unconditionally observed history.
    _requires_phrases(body, ("the active demand basis", "history or forecast"))
    mentions = _unconditional_mentions(body, ("observed history", "observed demand"))
    assert not mentions, (
        "the projected-demand basis must not be unconditionally described as "
        f"observed history or observed demand: {', '.join(mentions)}"
    )

    # 11. The presence check must not accept a negated statement.
    assert _stated(
        _flat("Aggregate planning is a read-only, aggregate view of the plan."),
        "a read-only, aggregate view of the plan",
    ), "affirmative statement must satisfy the presence check"
    assert not _stated(
        _flat(
            "Aggregate planning does not provide a read-only, aggregate "
            "view of the plan."
        ),
        "a read-only, aggregate view of the plan",
    ), "a negated statement must not satisfy the presence check"
    assert not _stated(
        _flat(
            "The vision does not state that aggregate planning makes no " "decision."
        ),
        "makes no decision",
    ), "a double negation must not satisfy the presence check"

    # 12. The AC-2 blocklist catches common affirmative
    #     write/stored/decision wording, and passes the mandated
    #     negations and phrasings.
    for claim in (
        "it writes the grid to disk",
        "the endpoint stores a projection",
        "the view saves a snapshot",
        "it persists the roll-up",
        "the screen creates a stored artifact",
        "it updates the aggregate on every sync",
        "the endpoint records the projection",
        "aggregate planning decides the threshold",
        "it determines the reorder quantities",
    ):
        found = _forbidden_claims(claim)
        assert found, f"the AC-2 blocklist missed a claim: {claim!r}"
    for allowed in (
        "it writes nothing, stores nothing, and makes no decision.",
        "changes no stored recommendation, decision, or setting",
        "an instance setting to be decided at the aggregate planning " "screen slice",
        "applying a saved scenario's levers to the aggregate view",
        "remains normative as written",
    ):
        found = _forbidden_claims(allowed)
        assert not found, f"the AC-2 blocklist flagged a mandated phrasing: {allowed!r}"

    # 13. Review defect: a negation in an earlier comma-clause
    #     over-scoped the coarse clause and missed an affirmative
    #     write/store claim in the claim's own clause.
    found = _forbidden_claims(
        "it writes nothing, and the endpoint stores the projection"
    )
    assert "stores" in found, (
        "a negation in an earlier comma-clause over-scoped and missed an "
        f"affirmative claim: {found!r}"
    )

    # 14. Review defect: _top_bullet crashed on a period/colon mismatch
    #     — an AC-compliant document may close the bold bullet name
    #     with either a period or a colon.
    for spelled in (
        "intro. - **Scope boundaries.** the scope content - **Other.** tail",
        "intro. - **Scope boundaries:** the scope content - **Other.** tail",
    ):
        bullet = _top_bullet(spelled, "Scope boundaries.")
        assert "the scope content" in bullet
        assert "tail" not in bullet

    # 15. Review defect: a negation later in the sentence — or inside a
    #     mandated multi-clause phrase — shielded a separate
    #     affirmative claim in the same coarse clause.
    found = _forbidden_claims("it stores the projection, which is not allowed")
    assert "stores" in found, (
        "a trailing negation shielded an affirmative claim: " f"{found!r}"
    )
    found = _forbidden_claims(
        "changes no stored recommendation, decision, or setting, "
        "and records the outcome"
    )
    assert "records" in found, (
        "a mandated-phrase negation leaked into a following claim: " f"{found!r}"
    )
