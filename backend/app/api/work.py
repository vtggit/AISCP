"""The work scorecard: how often the planner acts on the proposals.

``GET /api/work/scorecard`` (the router's prefix is ``/api/work``; the
router is included in ``app.main`` behind the app's include-time
authentication gate, like every other API router) answers
``{"processes": [...]}`` — the eleven work processes, in this order:
``reorder_points``, ``excess``, ``stockout``, ``exceptions``,
``forecast_overrides``, ``sop_signoff``, ``purchase_budget``,
``ddmrp_adjustment``, ``order_release``, ``buffer_order``,
``short_order`` and ``transfer_release``.  It
is open to any signed-in user — the gate answers 401 without a token
— and it writes nothing.

Each entry is ``{key, step, decisions, with_proposal, at_proposal,
without_proposal, automatic, rate}``, computed by calling the named
source function directly (not over HTTP):

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
* ``order_release`` — "Release purchase orders to the ERP" — from
  this module's own ``order_release_counts()``: ``decisions`` is
  ``released``, ``with_proposal`` is ``read``, ``at_proposal`` is
  ``unchanged``, ``without_proposal`` is 0 and ``automatic`` is
  ``automatic``.
* ``buffer_order`` — "Order on a buffer's signal" — from this
  module's own ``buffer_order_counts()``: mapped the same way as
  ``order_release`` — ``decisions`` is ``released``,
  ``with_proposal`` is ``read``, ``at_proposal`` is ``unchanged``,
  ``without_proposal`` is 0 and ``automatic`` is ``automatic`` —
  over the ``created`` rows' stored lines whose ``source`` is
  exactly ``"buffer"``.
* ``short_order`` — "Decide: wait, order anyway, or add the
  top-ups" — from this module's own ``short_order_counts()``:
  ``decisions`` and ``with_proposal`` are ``released``,
  ``at_proposal`` is ``followed``, ``without_proposal`` is 0 and
  ``automatic`` is ``automatic``.
* ``transfer_release`` — "Release transfers to the ERP" — from
  ``transfer_release_counts()``: ``decisions`` and ``with_proposal``
  are ``released``, ``at_proposal`` is null, ``without_proposal`` is
  0 and ``automatic`` is ``automatic``.

``order_release_counts`` and ``transfer_release_counts`` each run one
SELECT through ``app.db.connection.get_cursor``, over ``plan_releases``
and ``transfer_releases`` respectively: ``released`` is the number of
rows whose ``outcome`` is ``created``, and ``automatic`` the number of
those whose ``actor`` starts with ``standing-grant:`` — the releases a
standing grant fired rather than a person.  Rows whose outcome is
``pending``, ``exists``, ``error`` or ``aborted`` are not counted.
``order_release_counts`` also answers ``read`` — the created rows
whose ``erp_status`` is not null, the ones the Acumatica readback has
answered — and ``unchanged`` — the read rows for which
``app.supply.release_readback.release_unchanged`` of the row's
``lines``, ``erp_status`` and ``erp_lines`` is True: the ERP read the
order back with the same items and quantities as the released side.
``buffer_order_counts`` runs the same SELECT over the same rows and
answers its four counts over their stored lines whose ``source`` is
exactly ``"buffer"`` — the lines the buffers' signals ordered, not
the aggregate releases nor the releases stored before the source was
written: ``released`` is their number, ``automatic`` the number of
those in the rows whose ``actor`` starts with ``standing-grant:``,
``read`` the number of those in the rows whose ``erp_status`` is not
null, and ``unchanged`` the read lines for which
``app.supply.release_readback.line_unchanged`` of the line's
``item_code``, its ``quantity``, the row's ``erp_status`` and
``erp_lines`` is True; a row whose ``lines`` is not a list counts
nothing.  ``short_order_counts`` runs one SELECT through
``app.db.connection.get_cursor``, over ``short_order_releases`` —
the record of the released short orders: ``released`` is the number
of rows, ``followed`` the rows whose ``chosen`` equals their
``suggested``, and ``automatic`` the rows whose ``actor`` starts
with ``standing-grant:``.
For the eight existing entries ``automatic`` is null; for the four
release entries it is the count above.

``rate`` is ``at_proposal / with_proposal`` rounded to 4 decimals, or
null when ``with_proposal`` is 0 or ``at_proposal`` is null.

A failed source — one whose function raises, or one that breaks an
invariant — keeps its ``key`` and ``step`` and answers with its six
numbers null: ``decisions``, ``with_proposal``, ``at_proposal``,
``without_proposal``, ``rate`` and ``automatic``; the other entries
still answer and the route answers 200.  A source answer that violates
the scorecard invariants — a count that is not a non-negative integer,
an ``at_proposal`` above its ``with_proposal``, a ``with_proposal``
plus ``without_proposal`` that exceeds ``decisions`` (a ``saved``
below its ``with_proposal`` among them), or an ``automatic`` that is
not null and is not a non-negative integer (a bool is not one) no
greater than ``decisions`` — answers the same single failed-entry
shape with null counts, so the scorecard never emits contradictory
numbers, and the violation is logged.
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
from app.db.connection import get_cursor
from app.supply.release_readback import line_unchanged, release_unchanged

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/work", tags=["work"])


def order_release_counts() -> dict:
    """The scorecard's ``order_release`` counts, over ``plan_releases``.

    One SELECT through ``app.db.connection.get_cursor``, over the rows
    whose ``outcome`` is ``created``: ``released`` is their number and
    ``automatic`` the number of those whose ``actor`` starts with
    ``standing-grant:`` — the releases a standing grant fired rather
    than a person.  ``read`` is the number of the created rows whose
    ``erp_status`` is not null — the ones the Acumatica readback has
    answered — and ``unchanged`` the read rows for which
    ``app.supply.release_readback.release_unchanged`` of the row's
    ``lines``, ``erp_status`` and ``erp_lines`` is True.  Rows whose
    outcome is ``pending``, ``exists``, ``error`` or ``aborted`` are
    not counted.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT actor, lines, erp_status, erp_lines "
            "FROM plan_releases WHERE outcome = 'created'"
        )
        rows = cur.fetchall()
    released = len(rows)
    automatic = 0
    read = 0
    unchanged = 0
    for row in rows:
        if (row["actor"] or "").startswith("standing-grant:"):
            automatic += 1
        if row["erp_status"] is None:
            continue
        read += 1
        if release_unchanged(row["lines"], row["erp_status"], row["erp_lines"]):
            unchanged += 1
    return {
        "released": released,
        "automatic": automatic,
        "read": read,
        "unchanged": unchanged,
    }


def buffer_order_counts() -> dict:
    """The scorecard's ``buffer_order`` counts, over the stored lines
    of the ``plan_releases`` rows ``order_release_counts`` counts.

    One SELECT through ``app.db.connection.get_cursor``, over the
    rows whose ``outcome`` is ``created``: the four counts are over
    the rows' stored lines whose ``source`` is exactly
    ``"buffer"`` — the lines the buffers' signals ordered, not the
    aggregate releases nor the releases stored before the source was
    written — ``released`` is their number, ``automatic`` the number
    of those in the rows whose ``actor`` starts with
    ``standing-grant:``, ``read`` the number of those in the rows
    whose ``erp_status`` is not null, and ``unchanged`` the read
    lines for which
    ``app.supply.release_readback.line_unchanged`` of the line's
    ``item_code``, its ``quantity``, the row's ``erp_status`` and
    ``erp_lines`` is True.  A row whose ``lines`` is not a list
    counts nothing.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT actor, lines, erp_status, erp_lines "
            "FROM plan_releases WHERE outcome = 'created'"
        )
        rows = cur.fetchall()
    released = 0
    automatic = 0
    read = 0
    unchanged = 0
    for row in rows:
        lines = row["lines"]
        if lines is not None and not isinstance(lines, list):
            continue
        for line in lines or []:
            if not isinstance(line, dict) or line.get("source") != "buffer":
                continue
            released += 1
            if (row["actor"] or "").startswith("standing-grant:"):
                automatic += 1
            if row["erp_status"] is None:
                continue
            read += 1
            if line_unchanged(
                line.get("item_code"),
                line.get("quantity"),
                row["erp_status"],
                row["erp_lines"],
            ):
                unchanged += 1
    return {
        "released": released,
        "automatic": automatic,
        "read": read,
        "unchanged": unchanged,
    }


def short_order_counts() -> dict:
    """The scorecard's ``short_order`` counts, over
    ``short_order_releases``.

    One SELECT through ``app.db.connection.get_cursor``:
    ``released`` is the number of rows, ``followed`` the rows whose
    ``chosen`` equals their ``suggested``, and ``automatic`` the
    rows whose ``actor`` starts with ``standing-grant:``.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS released, "
            "COUNT(*) FILTER (WHERE chosen = suggested) AS followed, "
            "COUNT(*) FILTER (WHERE actor LIKE 'standing-grant:%') "
            "AS automatic "
            "FROM short_order_releases"
        )
        row = cur.fetchone()
    return {
        "released": row["released"],
        "followed": row["followed"],
        "automatic": row["automatic"],
    }


def transfer_release_counts() -> dict:
    """The scorecard's ``transfer_release`` counts, over
    ``transfer_releases``.

    One SELECT through ``app.db.connection.get_cursor``, the way
    ``order_release_counts`` counts ``plan_releases``: ``released`` is
    the number of rows whose ``outcome`` is ``created``, and
    ``automatic`` the number of those whose ``actor`` starts with
    ``standing-grant:``.  Rows whose outcome is ``pending``,
    ``exists``, ``error`` or ``aborted`` are not counted.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS released, "
            "COUNT(*) FILTER (WHERE actor LIKE 'standing-grant:%') "
            "AS automatic "
            "FROM transfer_releases WHERE outcome = 'created'"
        )
        row = cur.fetchone()
    return {"released": row["released"], "automatic": row["automatic"]}


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
    answers its six numbers null.
    """

    key: str
    step: str
    decisions: int | None = None
    with_proposal: int | None = None
    at_proposal: int | None = None
    without_proposal: int | None = None
    automatic: int | None = None
    rate: float | None = None


class WorkScorecard(BaseModel):
    """The answer of GET /api/work/scorecard."""

    processes: list[ScorecardProcess]


def _map_reorder_points(answer: dict) -> tuple:
    """reorder_points: accepted + dismissed over the current decisions."""
    accepted = answer["accepted"]["count"]
    dismissed = answer["dismissed"]["count"]
    return accepted + dismissed, accepted + dismissed, accepted, 0, None


def _map_proposal_counts(answer: dict) -> tuple:
    """excess and stockout: the accepted decisions' stored proposals."""
    return (
        answer["accepted"],
        answer["total"]["proposed"],
        answer["total"]["chosen"],
        answer["without_proposal"],
        None,
    )


def _map_exception_counts(answer: dict) -> tuple:
    """exceptions: the acknowledgement events; the proposal was not a
    decision, so ``at_proposal`` stays null."""
    return (
        answer["acknowledged"],
        answer["proposed"],
        None,
        answer["without_proposal"],
        None,
    )


def _map_override_counts(answer: dict) -> tuple:
    """forecast_overrides: the current overrides' stored proposals."""
    return (
        answer["overrides"],
        answer["proposed"],
        answer["taken"],
        answer["without_proposal"],
        None,
    )


def _map_signoff_counts(answer: dict) -> tuple:
    """sop_signoff: the signed months' stored sign-off proposal."""
    return (
        answer["signoffs"],
        answer["proposed"] + answer["not_proposed"],
        answer["proposed"],
        answer["without_proposal"],
        None,
    )


def _map_record_counts(answer: dict) -> tuple:
    """purchase_budget and ddmrp_adjustment: the stored records."""
    return (
        answer["saved"],
        answer["with_proposal"],
        answer["taken"],
        answer["saved"] - answer["with_proposal"],
        None,
    )


def _map_order_release_counts(answer: dict) -> tuple:
    """order_release: the releases to the ERP — the decision is the
    release and the proposal the readback: ``decisions`` is
    ``released``, ``with_proposal`` is ``read``, ``at_proposal`` is
    ``unchanged`` and ``without_proposal`` is 0, while ``automatic``
    is the share the standing grants fired."""
    return (
        answer["released"],
        answer["read"],
        answer["unchanged"],
        0,
        answer["automatic"],
    )


def _map_short_order_counts(answer: dict) -> tuple:
    """short_order: the released short orders — the release is the
    decision and the suggestion its proposal: ``decisions`` and
    ``with_proposal`` are ``released``, ``at_proposal`` is
    ``followed`` and ``without_proposal`` is 0, while ``automatic``
    is the share the standing grants fired."""
    return (
        answer["released"],
        answer["released"],
        answer["followed"],
        0,
        answer["automatic"],
    )


def _map_transfer_release_counts(answer: dict) -> tuple:
    """transfer_release: the releases to the ERP; the release itself
    is the decision — ``decisions`` and ``with_proposal`` are
    ``released``, ``at_proposal`` stays null and
    ``without_proposal`` is 0 — while ``automatic`` is the share the
    standing grants fired."""
    return answer["released"], answer["released"], None, 0, answer["automatic"]


def _log_violation(key: str, values: tuple, reason: str) -> bool:
    """Log one invariant violation and mark the entry failed."""
    decisions, with_proposal, at_proposal, without_proposal, automatic = values
    logger.warning(
        "work scorecard: source %s violates the scorecard invariants (%s) — "
        "answering null (decisions=%r, with_proposal=%r, at_proposal=%r, "
        "without_proposal=%r, automatic=%r)",
        key,
        reason,
        decisions,
        with_proposal,
        at_proposal,
        without_proposal,
        automatic,
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
    ``without_proposal``); and ``automatic``, when not null, is a
    non-negative integer (a bool is not one) no greater than
    ``decisions``.
    """
    decisions, with_proposal, at_proposal, without_proposal, automatic = values
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
    if automatic is not None and (
        isinstance(automatic, bool)
        or not isinstance(automatic, int)
        or automatic < 0
        or automatic > decisions
    ):
        return _log_violation(key, values, f"automatic is invalid: {automatic!r}")
    return True


def _failed_entry(key: str, step: str) -> dict:
    """The single failed-entry shape: key and step, six numbers null."""
    return {
        "key": key,
        "step": step,
        "decisions": None,
        "with_proposal": None,
        "at_proposal": None,
        "without_proposal": None,
        "automatic": None,
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
    decisions, with_proposal, at_proposal, without_proposal, automatic = values
    return {
        "key": key,
        "step": step,
        "decisions": decisions,
        "with_proposal": with_proposal,
        "at_proposal": at_proposal,
        "without_proposal": without_proposal,
        "automatic": automatic,
        "rate": _rate(with_proposal, at_proposal),
    }


@router.get("/scorecard", response_model=WorkScorecard)
def scorecard(
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """The work scorecard: the twelve processes, in their order.

    Open to any signed-in user (the router is mounted behind the
    app's include-time gate, which answers 401 without a valid Bearer
    token) and a pure read — it writes nothing and calls no ERP.
    Each entry is computed by calling its source function directly
    (not over HTTP) and mapping its answer as the module docstring
    states; ``rate`` is ``at_proposal / with_proposal`` rounded to 4
    decimals, null when ``with_proposal`` is 0 or ``at_proposal`` is
    null.  A failed source — one that raises, or one whose answer
    violates the scorecard invariants — keeps its ``key`` and
    ``step`` with its six numbers null; the other entries still
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
            _entry(
                "order_release",
                "Release purchase orders to the ERP",
                _map_order_release_counts,
                lambda: order_release_counts(),
            ),
            _entry(
                "buffer_order",
                "Order on a buffer's signal",
                _map_order_release_counts,
                lambda: buffer_order_counts(),
            ),
            _entry(
                "short_order",
                "Decide: wait, order anyway, or add the top-ups",
                _map_short_order_counts,
                lambda: short_order_counts(),
            ),
            _entry(
                "transfer_release",
                "Release transfers to the ERP",
                _map_transfer_release_counts,
                lambda: transfer_release_counts(),
            ),
        ],
    }
