"""The work scorecard: how often the planner acts on the proposals.

``GET /api/work/scorecard`` (the router's prefix is ``/api/work``; the
router is included in ``app.main`` behind the app's include-time
authentication gate, like every other API router) answers
``{"processes": [...]}`` — the eight work processes, in this order:
``reorder_points``, ``excess``, ``stockout``, ``exceptions``,
``forecast_overrides``, ``sop_signoff``, ``purchase_budget`` and
``ddmrp_adjustment``.  It is open to any signed-in user — the gate
answers 401 without a token — and it writes nothing.

Each entry is ``{key, step, decisions, with_proposal, at_proposal,
without_proposal, rate}``, computed by calling the named source
function directly (not over HTTP):

* ``reorder_points`` — "Accept or dismiss a recommended reorder
  point" — from ``app.api.planner_decisions.decision_acceptance()``:
  ``with_proposal`` and ``decisions`` are ``accepted.count +
  dismissed.count``, ``at_proposal`` is ``accepted.count`` and
  ``without_proposal`` is 0.
* ``excess`` — "Decide what to do with excess or dead stock" — from
  ``app.api.excess_decisions.excess_proposal_counts()``: ``decisions``
  is ``accepted``, ``with_proposal`` is ``total.proposed``,
  ``at_proposal`` is ``total.chosen`` and ``without_proposal`` is
  ``without_proposal``.
* ``stockout`` — "Decide how to cover a stockout risk" — from
  ``app.api.stockout_decisions.stockout_proposal_counts()``: mapped
  the same way as ``excess``.
* ``exceptions`` — "Acknowledge an exception" — from
  ``app.api.exceptions.exception_proposal_counts()``: ``decisions`` is
  ``acknowledged``, ``with_proposal`` is ``proposed``, ``at_proposal``
  is null and ``without_proposal`` is ``without_proposal``.
* ``forecast_overrides`` — "Override a period's forecast" — from
  ``app.api.forecast.forecast_override_proposal_counts()``:
  ``decisions`` is ``overrides``, ``with_proposal`` is ``proposed``,
  ``at_proposal`` is ``taken`` and ``without_proposal`` is
  ``without_proposal``.
* ``sop_signoff`` — "Sign the month off" — from
  ``app.api.sop.signoff_proposals``, given the route's own caller:
  ``decisions`` is ``signoffs``, ``with_proposal`` is
  ``proposed + not_proposed``, ``at_proposal`` is ``proposed`` and
  ``without_proposal`` is ``without_proposal``.
* ``purchase_budget`` — "Set each month's purchase budget" — from
  ``app.api.purchase_budgets._record_count()``: ``decisions`` is
  ``saved``, ``with_proposal`` is ``with_proposal``, ``at_proposal``
  is ``taken`` and ``without_proposal`` is ``saved - with_proposal``.
* ``ddmrp_adjustment`` — "Enter planned adjustment factors for a
  ramp" — from ``app.api.ddmrp.get_ddmrp_adjustment_record()``:
  mapped the same way as ``purchase_budget``.

``rate`` is ``at_proposal / with_proposal`` rounded to 4 decimals, or
null when ``with_proposal`` is 0 or ``at_proposal`` is null.

A failed source — one whose function raises — keeps its ``key`` and
``step`` and answers with its five numbers null; the other entries
still answer and the route answers 200.  A source answer that violates
the scorecard invariants — a count that is not a non-negative integer,
an ``at_proposal`` above its ``with_proposal``, or a ``with_proposal``
plus ``without_proposal`` that exceeds ``decisions`` (a ``saved``
below its ``with_proposal`` among them) — answers the same single
failed-entry shape with null counts, so the scorecard never emits
contradictory numbers, and the violation is logged.
"""

import logging
from collections.abc import Callable

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.ddmrp import get_ddmrp_adjustment_record
from app.api.exceptions import exception_proposal_counts
from app.api.excess_decisions import excess_proposal_counts
from app.api.forecast import forecast_override_proposal_counts
from app.api.planner_decisions import decision_acceptance
from app.api.purchase_budgets import _record_count
from app.api.sop import signoff_proposals
from app.api.stockout_decisions import stockout_proposal_counts
from app.auth.dependencies import require_authenticated_user
from app.auth.models import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/work", tags=["work"])


def require_any_authenticated_user(
    current_user: AuthUser = Depends(require_authenticated_user),
) -> AuthUser:
    """The scorecard route's own authentication dependency.

    ``GET /api/work/scorecard`` is open to any authenticated user:
    the dependency answers 401 without a valid Bearer token even when
    the router is mounted without the app's include-time gate, and
    passes the authenticated user through unchanged.
    """
    return current_user


class ScorecardProcess(BaseModel):
    """One work process of the scorecard.

    A failed or inconsistent source keeps ``key`` and ``step`` and
    answers its five numbers null.
    """

    key: str
    step: str
    decisions: int | None = None
    with_proposal: int | None = None
    at_proposal: int | None = None
    without_proposal: int | None = None
    rate: float | None = None


class WorkScorecard(BaseModel):
    """The answer of GET /api/work/scorecard."""

    processes: list[ScorecardProcess]


def _map_reorder_points(answer: dict) -> tuple:
    """reorder_points: accepted + dismissed over the current decisions."""
    accepted = answer["accepted"]["count"]
    dismissed = answer["dismissed"]["count"]
    return accepted + dismissed, accepted + dismissed, accepted, 0


def _map_proposal_counts(answer: dict) -> tuple:
    """excess and stockout: the accepted decisions' stored proposals."""
    return (
        answer["accepted"],
        answer["total"]["proposed"],
        answer["total"]["chosen"],
        answer["without_proposal"],
    )


def _map_exception_counts(answer: dict) -> tuple:
    """exceptions: the acknowledgement events; the proposal was not a
    decision, so ``at_proposal`` stays null."""
    return answer["acknowledged"], answer["proposed"], None, answer["without_proposal"]


def _map_override_counts(answer: dict) -> tuple:
    """forecast_overrides: the current overrides' stored proposals."""
    return (
        answer["overrides"],
        answer["proposed"],
        answer["taken"],
        answer["without_proposal"],
    )


def _map_signoff_counts(answer: dict) -> tuple:
    """sop_signoff: the signed months' stored sign-off proposal."""
    return (
        answer["signoffs"],
        answer["proposed"] + answer["not_proposed"],
        answer["proposed"],
        answer["without_proposal"],
    )


def _map_record_counts(answer: dict) -> tuple:
    """purchase_budget and ddmrp_adjustment: the stored records."""
    return (
        answer["saved"],
        answer["with_proposal"],
        answer["taken"],
        answer["saved"] - answer["with_proposal"],
    )


def _log_violation(key: str, values: tuple, reason: str) -> bool:
    """Log one invariant violation and mark the entry failed."""
    decisions, with_proposal, at_proposal, without_proposal = values
    logger.warning(
        "work scorecard: source %s violates the scorecard invariants (%s) — "
        "answering null (decisions=%r, with_proposal=%r, at_proposal=%r, "
        "without_proposal=%r)",
        key,
        reason,
        decisions,
        with_proposal,
        at_proposal,
        without_proposal,
    )
    return False


def _invariants_hold(key: str, values: tuple) -> bool:
    """The scorecard invariants of one mapped source answer.

    ``decisions``, ``with_proposal`` and ``without_proposal`` are
    non-negative integers; ``at_proposal`` is null or a non-negative
    integer not above ``with_proposal``; and ``with_proposal`` plus
    ``without_proposal`` never exceeds ``decisions`` — the two disjoint
    parts of the decisions cannot outnumber them (a ``saved`` below
    its ``with_proposal`` lands here through its negative
    ``without_proposal``).
    """
    decisions, with_proposal, at_proposal, without_proposal = values
    for name, value in (
        ("decisions", decisions),
        ("with_proposal", with_proposal),
        ("without_proposal", without_proposal),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return _log_violation(
                key, values, f"{name} is not a non-negative integer: {value!r}"
            )
    if at_proposal is not None and (
        isinstance(at_proposal, bool)
        or not isinstance(at_proposal, int)
        or at_proposal < 0
        or at_proposal > with_proposal
    ):
        return _log_violation(key, values, f"at_proposal is invalid: {at_proposal!r}")
    if with_proposal + without_proposal > decisions:
        return _log_violation(
            key, values, "with_proposal + without_proposal exceeds decisions"
        )
    return True


def _failed_entry(key: str, step: str) -> dict:
    """The single failed-entry shape: key and step, five numbers null."""
    return {
        "key": key,
        "step": step,
        "decisions": None,
        "with_proposal": None,
        "at_proposal": None,
        "without_proposal": None,
        "rate": None,
    }


def _rate(with_proposal: int, at_proposal: int | None) -> float | None:
    """at_proposal over with_proposal, rounded to 4 decimals; null when
    with_proposal is 0 or at_proposal is null."""
    if at_proposal is None or with_proposal == 0:
        return None
    return round(at_proposal / with_proposal, 4)


def _entry(
    key: str, step: str, mapper: Callable[[dict], tuple], fetch: Callable[[], dict]
) -> dict:
    """One scorecard entry: call the source, map it, check it.

    A source whose function raises, or whose answer violates the
    scorecard invariants, answers the failed-entry shape; the other
    entries are unaffected.
    """
    try:
        values = mapper(fetch())
    except Exception as exc:
        logger.warning("work scorecard: source %s failed: %s", key, exc)
        return _failed_entry(key, step)
    if not _invariants_hold(key, values):
        return _failed_entry(key, step)
    decisions, with_proposal, at_proposal, without_proposal = values
    return {
        "key": key,
        "step": step,
        "decisions": decisions,
        "with_proposal": with_proposal,
        "at_proposal": at_proposal,
        "without_proposal": without_proposal,
        "rate": _rate(with_proposal, at_proposal),
    }


@router.get("/scorecard", response_model=WorkScorecard)
def scorecard(
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """The work scorecard: the eight processes, in their order.

    Open to any signed-in user (the router is mounted behind the
    app's include-time gate, which answers 401 without a valid Bearer
    token) and a pure read — it writes nothing and calls no ERP.
    Each entry is computed by calling its source function directly
    (not over HTTP) and mapping its answer as the module docstring
    states; ``rate`` is ``at_proposal / with_proposal`` rounded to 4
    decimals, null when ``with_proposal`` is 0 or ``at_proposal`` is
    null.  A failed source — one that raises, or one whose answer
    violates the scorecard invariants — keeps its ``key`` and
    ``step`` with its five numbers null; the other entries still
    answer and the route answers 200.
    """
    return {
        "processes": [
            _entry(
                "reorder_points",
                "Accept or dismiss a recommended reorder point",
                _map_reorder_points,
                lambda: decision_acceptance(),
            ),
            _entry(
                "excess",
                "Decide what to do with excess or dead stock",
                _map_proposal_counts,
                lambda: excess_proposal_counts(),
            ),
            _entry(
                "stockout",
                "Decide how to cover a stockout risk",
                _map_proposal_counts,
                lambda: stockout_proposal_counts(),
            ),
            _entry(
                "exceptions",
                "Acknowledge an exception",
                _map_exception_counts,
                lambda: exception_proposal_counts(),
            ),
            _entry(
                "forecast_overrides",
                "Override a period's forecast",
                _map_override_counts,
                lambda: forecast_override_proposal_counts(),
            ),
            _entry(
                "sop_signoff",
                "Sign the month off",
                _map_signoff_counts,
                lambda: signoff_proposals(current_user),
            ),
            _entry(
                "purchase_budget",
                "Set each month's purchase budget",
                _map_record_counts,
                lambda: _record_count(),
            ),
            _entry(
                "ddmrp_adjustment",
                "Enter planned adjustment factors for a ramp",
                _map_record_counts,
                lambda: get_ddmrp_adjustment_record(),
            ),
        ],
    }
