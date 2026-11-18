"""Issue #502 proof: the aggregate planning section records the decided method.

AC-1 — ``docs/PRODUCT-VISION.md``'s aggregate planning heading is
exactly ``## Aggregate planning (operator direction 2026-10-16; method
defaults decided by Claude under delegation)``, the old "operator
review pending 2026-10-16; proposed by Claude up the planning stack"
heading is gone, the section still sits between "Scenario planning
(what-if)" and "Comparative-research method", and nothing else in the
section changed: the introductory paragraph and the first four bullets
(The grouping dimension, The horizon and buckets, What it reports, per
group and bucket, Scope boundaries) are byte for byte what they were,
and the slice-ladder bullet is replaced by exactly four bullets, in
this order, with the wording the issue gives each:

  * Planned supply (decided by Claude under delegation, 2026-10-18) —
    the periodic-review order-up-to (R, S) policy next to the as-is
    figures: monthly review, order up to ``S =`` the reorder point
    plus that month's demand whenever the inventory position (on-hand
    plus open purchase lines not yet received plus planned orders not
    yet arrived) is below ``S``, the order arriving after its lead
    time, planned on-hand the as-is on-hand plus cumulative planned
    receipts; the reorder-point-only trigger is named for the false
    shortfalls it produced (#490);
  * Scenario levers on the aggregate (shipped 2026-10-18) — the
    projection takes the Scenario screen's three levers with the same
    transform, and the Aggregate screen runs any saved scenario;
  * Financial reconciliation, next (recommended by Claude,
    operator-delegated 2026-10-19) — per month, committed spend (open
    purchase lines at cost, by promised month) and planned spend
    (planned receipts at cost, counted in the month received: the
    open-to-buy convention) compared with a purchasing budget per
    month; the budget is a SYSTEM setting that only an admin edits;
    it reads only the projection and the stored budget and writes
    nothing else;
  * Multi-echelon (deferred, measured 2026-10-19) — the reference data
    carries no supply network; inter-site rebalancing found 3
    opportunities across the 21 items stocked at more than one site,
    worth about $45k, almost all of it one item; it returns when a
    customer's data carries a network.

AC-2/AC-3 — the financial reconciliation bullet states that the next
rung is spend-to-budget comparison only, and the only mention of
recommending which planned orders to defer (with the least service
impact) is explicitly labeled "a follow-up, not part of this update";
no bare "defer" appears anywhere else in the section.

Identity — the budget is named "a SYSTEM setting that only an admin
edits", so the pass also runs as a NON-ADMIN (role ``user``): the
conftest ``client`` carries the development token (sub ``dev-user-1``,
role ``user``) — the same token the page would hold from its
``#access_token=...`` fragment sends as its Bearer token — and the
page boots the production way against the real endpoints:
``GET /api/auth/config`` (public), then ``GET /api/auth/me`` and the
page's own data call ``GET /api/settings``. As the non-admin the
admin-only control is absent — every system-scope setting lists
``editable`` false, and PUT and DELETE on the system setting
``replenishment.demand_basis`` answer 403 with the unchanged detail
"Role 'admin' is required to perform this action." — while the page
still renders: ``GET /api/settings`` answers 200 with all its
settings before and after the refusals. As the admin stub user
(``dev-secret-token:admin``) the same PUT succeeds and the reset
restores the default, so the control is admin-only rather than
missing.

Review defects — the proof also carries an assertion for each of the
three review defects in the prior version: (1) the heading placement
check crashed with ValueError when the heading's only occurrence was
not at line start; it now fails cleanly, asserted against a mid-line
sample; (2) the observed-history check was shielded by a trailing
negation ("the observed history, not the forecast"); only a negation
before the mention governs it, asserted against trailing and leading
samples; (3) the four replacement bullets were not asserted on their
own; each is now asserted by its exact lead-in and the wording the
issue mandates, in the order the issue gives.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_issue444_freeform import _unconditional_mentions

DOC = Path(__file__).resolve().parents[2] / "docs" / "PRODUCT-VISION.md"

NEW_HEADING = (
    "## Aggregate planning (operator direction 2026-10-16; "
    "method defaults decided by Claude under delegation)"
)
OLD_HEADING = (
    "## Aggregate planning (operator review pending 2026-10-16; "
    "proposed by Claude up the planning stack)"
)

EXPECTED_INTRO = """A recommendation answers one item-site and a scenario asks what an assumption would do to its numbers; before acting, a planner also needs the portfolio view. Aggregate planning is a read-only, aggregate view of the plan. It rolls the item-site numbers up to a grouping dimension and time buckets and shows, per group and bucket, projected demand against projected supply and the projected on-hand that results — so a planner sees where the portfolio is short or long over the horizon before acting on any single item-site. It writes nothing, stores nothing, and makes no decision."""

EXPECTED_BULLETS = (
    """- **The grouping dimension.** Site to begin with (the warehouse/location already on every item-site), and a product family or category when the ERP source provides one — the aggregate is keyed by (group, bucket) and never invents a hierarchy the source does not carry.""",
    """- **The horizon and buckets.** A fixed number of forward monthly buckets from an as-of date (default six months), each bucket a calendar month.""",
    """- **What it reports, per group and bucket.** Projected demand: the active demand basis — history or forecast, the same basis the recommendations use — summed over the item-sites in the group for the days in the bucket. Projected supply: open purchase-line quantities whose promised date falls in the bucket, from the open-purchase-supply data already ingested. Projected on-hand at the end of the bucket: the group's starting on-hand rolled forward — prior on-hand plus cumulative supply minus cumulative demand. And a short coverage signal: projected on-hand below zero in a bucket = a projected shortfall; a large positive projected on-hand relative to demand = projected excess. The projected-excess threshold is not fixed in this vision: it is an instance setting to be decided at the Aggregate planning screen slice with the reference dataset in view, while the shortfall signal (projected on-hand below zero) remains normative as written. The dollar view sums each quantity at the item-site `unit_cost`, item-sites without a unit cost contributing zero and counted separately, exactly as scenario planning already does.""",
    """- **Scope boundaries.** It aggregates only the item-sites the recommendations already consider (the same intermittent/lumpy exclusions), reuses the existing demand-basis, on-hand and open-purchase-supply reads with no new data source, and changes no stored recommendation, decision, or setting; the existing recommendation / drift / excess / stockout / demand / scenario behaviours are unchanged.""",
    """- **Planned supply (decided by Claude under delegation, 2026-10-18):** next to the as-is figures, the projection carries what the recommended policy would order. It is a periodic-review order-up-to (R, S) policy: at the start of each month, an item-site whose inventory position (on-hand, plus open purchase lines not yet received, plus planned orders not yet arrived) is below S = its reorder point plus that month's demand orders up to S, and the order arrives after its lead time. Planned on-hand is the as-is on-hand plus cumulative planned receipts. The first version triggered on the reorder point alone. That produced false shortfalls every other month wherever the reorder point is below a month's demand, and the live check on the reference data caught it (#490).""",
    """- **Scenario levers on the aggregate (shipped 2026-10-18):** the projection takes the Scenario screen's three levers with the same transform, and the Aggregate screen runs any saved scenario.""",
    """- **Financial reconciliation, next (recommended by Claude, operator-delegated 2026-10-19):** the S&OP loop closes with money. Per month, committed spend (the open purchase lines at cost, by promised month) and planned spend (planned receipts at cost, counted in the month received: the open-to-buy convention) are compared with a purchasing budget per month. The budget is a SYSTEM setting that only an admin edits. The next rung is spend-to-budget comparison only: the comparison shows the remaining budget or the overrun. Recommending which planned orders to defer with the least service impact is a follow-up, not part of this update. It reads only the projection and the stored budget, and it writes nothing else.""",
    """- **Multi-echelon (deferred, measured 2026-10-19):** the reference data carries no supply network, since nothing says which site replenishes which. Inter-site rebalancing found 3 opportunities across the 21 items stocked at more than one site, worth about $45k, almost all of it one item. It returns when a customer's data carries a network.""",
)

FINANCIAL_LEAD_IN = (
    "Financial reconciliation, next (recommended by Claude, "
    "operator-delegated 2026-10-19):"
)
LABELED_DEFERRAL = (
    "Recommending which planned orders to defer with the least service "
    "impact is a follow-up, not part of this update."
)

# The four replacement rungs, each by its exact lead-in and the wording
# the issue mandates, in the order the issue gives. Asserted on their
# own: the bulk bullet equality must not be the only thing holding
# the replacement bullets in place.
REPLACEMENT_RUNGS = (
    (
        "Planned supply (decided by Claude under delegation, 2026-10-18)",
        (
            "next to the as-is figures, the projection carries what the "
            "recommended policy would order",
            "It is a periodic-review order-up-to (R, S) policy",
            "at the start of each month, an item-site whose inventory "
            "position (on-hand, plus open purchase lines not yet received, "
            "plus planned orders not yet arrived) is below S = its reorder "
            "point plus that month's demand orders up to S",
            "and the order arrives after its lead time",
            "Planned on-hand is the as-is on-hand plus cumulative " "planned receipts",
            "The first version triggered on the reorder point alone",
            "That produced false shortfalls every other month wherever "
            "the reorder point is below a month's demand",
            "the live check on the reference data caught it (#490)",
        ),
    ),
    (
        "Scenario levers on the aggregate (shipped 2026-10-18)",
        (
            "the projection takes the Scenario screen's three levers with "
            "the same transform",
            "the Aggregate screen runs any saved scenario",
        ),
    ),
    (
        "Financial reconciliation, next (recommended by Claude, "
        "operator-delegated 2026-10-19)",
        (
            "the S&OP loop closes with money",
            "committed spend (the open purchase lines at cost, by " "promised month)",
            "planned spend (planned receipts at cost, counted in the "
            "month received: the open-to-buy convention)",
            "are compared with a purchasing budget per month",
            "The budget is a SYSTEM setting that only an admin edits",
            "The next rung is spend-to-budget comparison only",
            "the comparison shows the remaining budget or the overrun",
            "Recommending which planned orders to defer with the least "
            "service impact is a follow-up, not part of this update",
            "It reads only the projection and the stored budget, and "
            "it writes nothing else",
        ),
    ),
    (
        "Multi-echelon (deferred, measured 2026-10-19)",
        (
            "the reference data carries no supply network, since nothing "
            "says which site replenishes which",
            "Inter-site rebalancing found 3 opportunities across the 21 "
            "items stocked at more than one site, worth about $45k, "
            "almost all of it one item",
            "It returns when a customer's data carries a network",
        ),
    ),
)

ADMIN_TOKEN = "dev-secret-token:admin"
SYSTEM_SETTING_KEY = "replenishment.demand_basis"
ADMIN_ONLY_DETAIL = "Role 'admin' is required to perform this action."


def _flat(text: str) -> str:
    """Collapse every whitespace run to a single space."""
    return re.sub(r"\s+", " ", text)


def _section_body(doc: str, heading: str) -> str:
    """From just after *heading* to the next level-2 heading."""
    start = doc.index(heading) + len(heading)
    rest = doc[start:]
    match = re.search(r"^## ", rest, flags=re.MULTILINE)
    return rest[: match.start()] if match else rest


def _top_bullets(body: str) -> list[tuple[str, str]]:
    """The top-level bullets of a flattened section body, in order.

    Each bullet is ``(lead_in, text)``: the bold lead-in as written
    (its trailing period or colon included) and the bullet's text,
    which keeps its leading space.
    """
    bullets = []
    for part in body.split(" - **")[1:]:
        close = part.index("**")
        bullets.append((part[:close], part[close + 2 :]))
    return bullets


def _bullet(body: str, lead_in: str) -> str:
    """The text of the bullet whose lead-in is *lead_in*."""
    for lead, text in _top_bullets(body):
        if lead.rstrip(".:") == lead_in.rstrip(".:"):
            return text
    raise AssertionError(f"no bullet named {lead_in!r}")


def _heading_neighbors(doc: str, heading: str) -> tuple[str, str]:
    """The level-2 heading lines directly before and after *heading*.

    The heading must sit at line start exactly once: a mid-line
    occurrence is not a heading, and the check fails cleanly on one
    (AssertionError, never a ValueError from ``list.index``).
    """
    line_hits = [line for line in doc.splitlines() if line == heading]
    assert len(line_hits) == 1, (
        f"{heading!r} must be exactly one line-start heading line, "
        f"found {len(line_hits)}"
    )
    headings = [line for line in doc.splitlines() if line.startswith("## ")]
    index = headings.index(heading)
    before = headings[:index]
    after = headings[index + 1 :]
    assert before and after, f"{heading!r} must sit between two other level-2 headings"
    return before[-1], after[0]


def _admin_client() -> TestClient:
    """A client as the admin stub user (role ``admin``)."""
    from app.main import app

    return TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})


def test_issue502_freeform(client):
    # ---- AC-1: the section records the decided method ----------------
    doc = DOC.read_text(encoding="utf-8")
    assert doc.count(NEW_HEADING) == 1, "the new heading must appear exactly once"
    assert OLD_HEADING not in doc, "the old heading must be gone"

    before, _ = _heading_neighbors(doc, NEW_HEADING)
    assert before.startswith("## Scenario planning (what-if)")
    # "Comparative-research method" comes later in the document than
    # the new heading; sections may now sit in between.
    headings = [line for line in doc.splitlines() if line.startswith("## ")]
    comparative_index = next(
        (
            i
            for i, heading in enumerate(headings)
            if heading.startswith("## Comparative-research method")
        ),
        -1,
    )
    assert comparative_index > headings.index(
        NEW_HEADING
    ), "'## Comparative-research method' must come after the new heading"

    # Review defect: the placement check crashed (ValueError from
    # list.index) when the heading's only occurrence was not at line
    # start. It must fail cleanly instead, so a mid-line occurrence
    # can never hide behind a crash.
    mid_line = doc.replace(NEW_HEADING, "see " + NEW_HEADING)
    with pytest.raises(AssertionError):
        _heading_neighbors(mid_line, NEW_HEADING)

    body = _flat(_section_body(doc, NEW_HEADING)).strip()

    # The introductory paragraph is unchanged.
    intro = body.split(" - **")[0].strip()
    assert intro == EXPECTED_INTRO, "the introductory paragraph changed"

    # Exactly the eight bullets, in order, byte for byte: the first four
    # unchanged, the slice-ladder bullet replaced by the four new rungs.
    bullets = [f"- **{lead}**{text}" for lead, text in _top_bullets(body)]
    assert bullets == list(EXPECTED_BULLETS), (
        "the section's bullets must be the four unchanged bullets followed "
        "by the four replacement rungs, in this order, word for word"
    )
    assert "The slice ladder" not in body

    # Review defect: the four replacement bullets were not asserted on
    # their own. Each rung is asserted by its exact lead-in and the
    # wording the issue mandates, in the order the issue gives.
    for lead, phrases in REPLACEMENT_RUNGS:
        rung = _bullet(body, lead)
        for phrase in phrases:
            assert phrase in rung, f"{lead!r} missing {phrase!r}"
    starts = [body.index(f" - **{lead}") for lead, _ in REPLACEMENT_RUNGS]
    assert starts == sorted(
        starts
    ), "the replacement rungs must appear in the order the issue gives"

    # ---- AC-2/AC-3: the financial reconciliation bullet --------------
    financial = _bullet(body, FINANCIAL_LEAD_IN)
    assert "The next rung is spend-to-budget comparison only" in financial
    assert "a purchasing budget per month" in financial
    assert "The budget is a SYSTEM setting that only an admin edits." in financial
    assert LABELED_DEFERRAL in financial
    # No mention of deferring planned orders anywhere outside the
    # labeled follow-up sentence ("deferred" in the multi-echelon lead-in
    # labels that rung itself as a deferred capability).
    remaining = body.replace(LABELED_DEFERRAL, "").lower()
    assert re.search(r"\bdefer\b", remaining) is None, (
        "a mention of deferring planned orders outside the labeled "
        "follow-up sentence"
    )

    # Review defect: the observed-history check was shielded by a
    # trailing negation — "the observed history, not the forecast"
    # read as negated. Only a negation before the mention governs it.
    shielded = _unconditional_mentions(
        _flat("projected demand uses the observed history, not the forecast"),
        ("observed history",),
    )
    assert shielded == ["observed history"], (
        "a trailing negation shielded an affirmative observed-history "
        f"mention: {shielded!r}"
    )
    for qualified in (
        "the active demand basis, history or forecast, never "
        "unconditionally as observed history",
        "the basis is not the observed history",
    ):
        assert (
            _unconditional_mentions(_flat(qualified), ("observed history",)) == []
        ), f"a negated mention must not be flagged: {qualified!r}"
    assert (
        _unconditional_mentions(body, ("observed history", "observed demand")) == []
    ), "the section unconditionally describes the demand basis"

    # ---- Identity: the non-admin pass --------------------------------
    # The conftest client carries the development token: sub dev-user-1,
    # role "user" — a non-admin. Boot the way the page boots: the public
    # auth config, then identity and the page's own data call with the
    # token the page would hold.
    config = client.get("/api/auth/config")
    assert config.status_code == 200
    assert config.json()["authEnabled"] is True

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["authenticated"] is True
    assert me_body["user"]["sub"] == "dev-user-1"
    assert me_body["user"]["roles"] == ["user"]
    assert "admin" not in me_body["user"]["roles"]

    # The page still renders for the non-admin: its data call answers
    # with every registered setting, the system ones not editable by
    # this caller — the admin-only control is absent.
    page = client.get("/api/settings")
    assert page.status_code == 200
    entries = page.json()
    assert {entry["key"] for entry in entries} == {
        "ddmrp.adu_window_days",
        "ddmrp.lead_time_factor_long",
        "ddmrp.lead_time_factor_medium",
        "ddmrp.lead_time_factor_short",
        "ddmrp.variability_factor_high",
        "ddmrp.variability_factor_low",
        "ddmrp.variability_factor_medium",
        "decisions.remedy_order",
        "decisions.require_action_choice",
        "erp.reporting_currency",
        "erp.standing_grants_enabled",
        "erp.standing_grants_horizon_days",
        "exceptions.basis_switch_margin",
        "exceptions.basis_switch_min_periods",
        "exceptions.forecast_bias_min_periods",
        "exceptions.forecast_bias_periods",
        "exceptions.forecast_bias_threshold",
        "exceptions.order_minimum_due_days",
        "exceptions.release_approval_days",
        "exceptions.sop_signoff_day",
        "queue.default_filter",
        "replenishment.demand_basis",
        "replenishment.holding_rate",
        "replenishment.intermittent_recommendations",
        "replenishment.lead_time_basis",
        "replenishment.order_cost",
        "replenishment.service_level",
        "replenishment.service_level_a",
        "replenishment.service_level_b",
        "replenishment.service_level_by_class",
        "replenishment.service_level_c",
        "replenishment.service_target",
        "second_source.qualification_days",
        "second_source.similar_prefix_length",
        "supplier_risk.lead_time_cv_ceiling",
        "supplier_risk.on_time_floor",
        "supply_plan.prefer_lower_risk_supplier",
        "supply_plan.top_up_lookahead_days",
    }
    for entry in entries:
        if entry["scope"] == "system":
            assert entry["editable"] is False, entry["key"]
        else:
            assert entry["editable"] is True, entry["key"]
    system_entry = next(e for e in entries if e["key"] == SYSTEM_SETTING_KEY)
    assert system_entry["scope"] == "system"
    assert system_entry["editable"] is False
    assert system_entry["value"] == "history"  # the registry default

    # The admin-only control is refused, body and status unchanged.
    put = client.put(f"/api/settings/{SYSTEM_SETTING_KEY}", json={"value": "forecast"})
    assert put.status_code == 403
    assert put.json() == {"detail": ADMIN_ONLY_DETAIL}
    delete = client.delete(f"/api/settings/{SYSTEM_SETTING_KEY}")
    assert delete.status_code == 403
    assert delete.json() == {"detail": ADMIN_ONLY_DETAIL}

    # ...and the page still renders after the refusals, value unchanged.
    again = client.get("/api/settings")
    assert again.status_code == 200
    before = {e["key"]: (e["editable"], e["value"]) for e in entries}
    after = {e["key"]: (e["editable"], e["value"]) for e in again.json()}
    assert before == after

    # Control: the same write succeeds as the admin, so the control is
    # admin-only rather than missing; the reset restores the default.
    admin = _admin_client()
    granted = admin.put(
        f"/api/settings/{SYSTEM_SETTING_KEY}", json={"value": "forecast"}
    )
    assert granted.status_code == 200
    granted_entry = granted.json()
    assert granted_entry["key"] == SYSTEM_SETTING_KEY
    assert granted_entry["value"] == "forecast"
    assert granted_entry["source"] == "system"
    assert granted_entry["editable"] is True
    reset = admin.delete(f"/api/settings/{SYSTEM_SETTING_KEY}")
    assert reset.status_code == 200
    assert reset.json()["source"] == "default"
