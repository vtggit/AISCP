"""Per-month purchase budgets: the monthly purchasing cap.

``GET /api/purchase-budgets`` lists the stored budgets in month order
and is open to any signed-in user.  ``GET
/api/purchase-budgets/proposals`` proposes a budget for each month of
the projected spend horizon: the spend answer of
``spend_against_budget(months=months)`` — read once, like the list —
beside, for every month of it in its order, the ``proposed_budget``
(``propose_budget``'s round-up of the month's ``total_spend`` grown by
the margin of the system setting ``sop.budget_proposal_margin`` — the
stored value, read once through
``app.sop.review._stored_system_settings`` and resolved with
``app.ddmrp.zones.setting_value``, the registry default 0.05 while
nothing is stored — to three significant figures) and the
``difference`` (the stored ``budget`` minus the proposal, null without
a budget), with the margin and the spend's
``item_sites_without_unit_cost`` on top, and ``record`` measuring the
record rows the saves append — beside it: over the latest record row
of each month that still has a stored budget, the record read once
with ``latest_per_month`` and the stored budgets read once,
``saved`` counts those rows, ``with_proposal`` those whose
``proposed_amount`` is not null, and of those, ``taken`` the amount
within 0.005 of the proposal, ``below`` the amount under it by more
and ``above`` the amount over it by more — so ``taken + below +
above = with_proposal``.  It is read-only, open to any signed-in user
like the list, and its ``months`` query parameter runs from 1 to 12
and defaults to 6, a value outside that range answering 422 before
anything is read.  ``PUT /api/purchase-budgets/{month}`` stores one
month's budget — a new row, or the stored amount of the existing one
on the ``month`` unique key, with ``updated_at`` and ``updated_by``
(the caller's username) bumped — and appends one row of
``purchase_budget_proposals`` beside it, in the same database
transaction: the saved amount and the caller's username, the month's
proposal at that moment — computed the way the proposals route
computes it, over a 12-month horizon: ``propose_budget`` of the
month's ``total_spend`` of ``spend_against_budget(months=12)`` grown
by the margin, the margin stored too — and a null proposal with a
null margin when the month is outside that horizon, or when the
spend read raises.  A failed append rolls the budget save back and
answers 500, so a budget is never stored without its record row;
either way the save answers exactly as without the record, and
``DELETE /api/purchase-budgets/{month}`` — which removes one budget
and appends nothing, removing no record row — needs the admin role
like the store, a signed-in non-admin getting 403 exactly as the
erp-mappings routes refuse.  A month outside ``YYYY-MM`` with a month
of 01 to 12, a missing amount, a negative or non-finite amount or one
above 1,000,000,000,000 answers 422 before anything is written — and
for the admin-only routes the 401/403 from authentication and
authorization come first.
"""

import json
import math
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel

from app.aggregate.spend import spend_against_budget
from app.api.erp_mappings import (
    require_admin,
    require_any_authenticated_user,
)
from app.auth.models import AuthUser
from app.db.connection import get_cursor
from app.ddmrp.zones import setting_value
from app.models.purchase_budgets import (
    MONTH_PATTERN,
    PurchaseBudgetIn,
    PurchaseBudgetOut,
)
from app.repositories.purchase_budget_proposals_postgres_repository import (
    PurchaseBudgetProposalPostgresRepository,
)
from app.repositories.purchase_budget_proposals_postgres_repository import (
    _insert as _insert_record,
)
from app.repositories.purchase_budgets_postgres_repository import (
    PurchaseBudgetPostgresRepository,
)
from app.repositories.purchase_budgets_postgres_repository import (
    _row_to_dict as _budget_row,
)
from app.sop.review import _stored_system_settings

# The system setting that grows a month's projected spend into the
# proposed budget.
PROPOSAL_MARGIN_KEY = "sop.budget_proposal_margin"

router = APIRouter(prefix="/api/purchase-budgets", tags=["purchase-budgets"])

_repo = PurchaseBudgetPostgresRepository()
_record_repo = PurchaseBudgetProposalPostgresRepository()


def propose_budget(total_spend: float, margin: float) -> float:
    """The proposed budget of one month from its projected spend.

    The rule: ``total_spend * (1 + margin)`` rounded up to three
    significant figures — divide the amount by the step that leaves
    three significant digits and round the quotient up — and 0 for a
    total of 0, or whenever the margin shrinks the grown total to
    zero or below (a margin of -1 or less, for instance): the proposal
    is never negative, and the round-up's ``log10`` of a non-positive
    number would raise.  The proof's examples:

    - ``propose_budget(123456.78, 0.05)`` is 130000
      (129629.619 up to the next thousand),
    - ``propose_budget(100000, 0)`` is 100000 (already on a step),
    - ``propose_budget(999.5, 0)`` is 1000,
    - ``propose_budget(1000, 0.05)`` is 1050,
    - ``propose_budget(0, 0.05)`` is 0.

    The round-up never adds a step that float arithmetic merely leaves
    a hair above: the quotient is rounded to 9 decimals before it is
    rounded up, so a product a hair above a whole step —
    1000.0000000000002, for instance — still proposes the step (1000),
    not the next one (1010).
    """
    if total_spend == 0:
        return 0.0
    amount = total_spend * (1 + margin)
    if amount <= 0:
        return 0.0
    step = 10.0 ** (math.floor(math.log10(amount)) - 2)
    steps = math.ceil(round(amount / step, 9))
    return steps * step


async def _reject_non_finite_amount(request: Request) -> None:
    """Refuse a non-finite ``amount`` with a 422, never a 500.

    A JSON ``Infinity``/``NaN`` token — or a number so large it
    overflows to one on parse, such as 1e400 — is a non-finite
    float.  The body model's ge/le constraints would reject it,
    but FastAPI's 422 envelope echoes the value back as the
    error's ``input`` and the response serializer (which renders
    with ``allow_nan=False``) cannot write it — the rejection
    would become a 500.  This guard reads the (cached) raw body
    and refuses any non-finite number in it with a plain 422
    instead; a body without one passes through to the body
    model's own validation.
    """
    raw = await request.body()
    if not raw:
        return
    try:
        payload = json.loads(raw)
    except ValueError:
        return
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail="amount must be a finite number",
        ) from None


class ProposalMonth(BaseModel):
    """One month of the proposals: the spend beside its proposed budget."""

    month: str
    committed_spend: float
    planned_spend: float
    total_spend: float
    proposed_budget: float
    budget: float | None
    difference: float | None


class BudgetProposalRecord(BaseModel):
    """The record measure of the proposals' answer.

    Over the latest record row of each month that still has a stored
    budget: ``saved`` counts those rows, ``with_proposal`` those
    whose ``proposed_amount`` is not null, and of those, ``taken``
    the amount within 0.005 of the proposal, ``below`` the amount
    under it by more and ``above`` the amount over it by more — so
    ``taken + below + above = with_proposal``.
    """

    saved: int
    with_proposal: int
    taken: int
    below: int
    above: int


class BudgetProposals(BaseModel):
    """The proposed budgets of the projected spend horizon.

    ``record`` measures the record rows the saves of
    ``PUT /api/purchase-budgets/{month}`` append beside the stored
    budgets, the way ``_record_count`` counts them.
    """

    margin: float
    item_sites_without_unit_cost: int
    months: list[ProposalMonth]
    record: BudgetProposalRecord


@router.get("", response_model=list[PurchaseBudgetOut])
def list_budgets(
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> list[dict]:
    """Every stored budget, in month order.

    Open to any signed-in user (the admin role is only needed for the
    mutations); the router's include-time gate answers 401 without a
    valid Bearer token.
    """
    return _repo.list()


def _proposal_for_saved_month(month: str) -> tuple[float | None, float | None]:
    """The proposal the save of ``month`` records, at the moment of it.

    Computed the way ``GET /api/purchase-budgets/proposals`` computes
    it, over a 12-month horizon: the margin of
    ``sop.budget_proposal_margin`` — the stored settings read once
    through ``_stored_system_settings``, resolved with
    ``setting_value`` — and ``propose_budget`` of the month's
    ``total_spend`` of ``spend_against_budget(months=12)``.
    ``(None, None)`` when the month is outside that horizon, or when
    the settings or the spend read raises: the save then still stores
    its budget, with the null proposal and the null margin.
    """
    try:
        stored = _stored_system_settings()
        margin = setting_value(stored, PROPOSAL_MARGIN_KEY)
        spend = spend_against_budget(months=12)
        for row in spend["months"]:
            if row["month"] == month:
                return propose_budget(row["total_spend"], margin), margin
        return None, None
    except Exception:
        return None, None


def _record_count() -> dict:
    """The ``record`` measure of the proposals' answer.

    Over the latest record row of each month that still has a stored
    budget — the record read once with ``latest_per_month`` and the
    stored budgets read once: ``saved`` counts those rows,
    ``with_proposal`` those whose ``proposed_amount`` is not null,
    and of those, ``taken`` the amount within 0.005 of the proposal,
    ``below`` the amount under it by more and ``above`` the amount
    over it by more — so ``taken + below + above = with_proposal``.
    """
    budgets = {row["month"] for row in _repo.list()}
    saved = with_proposal = taken = below = above = 0
    for record in _record_repo.latest_per_month():
        if record["month"] not in budgets:
            continue
        saved += 1
        proposed = record["proposed_amount"]
        if proposed is None:
            continue
        with_proposal += 1
        difference = record["amount"] - proposed
        if abs(difference) <= 0.005:
            taken += 1
        elif difference < 0:
            below += 1
        else:
            above += 1
    return {
        "saved": saved,
        "with_proposal": with_proposal,
        "taken": taken,
        "below": below,
        "above": above,
    }


@router.get("/proposals", response_model=BudgetProposals)
def budget_proposals(
    months: int = Query(default=6, ge=1, le=12),
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """Propose a budget for each month of the projected spend.

    Open to any signed-in user, like ``GET /api/purchase-budgets``;
    the router's include-time gate answers 401 without a valid Bearer
    token.  ``months`` runs from 1 to 12 and defaults to 6; a value
    outside that range answers 422 before anything is read.

    The spend is read once through
    ``spend_against_budget(months=months)`` and the stored system
    settings once through ``app.sop.review._stored_system_settings``;
    the margin is ``sop.budget_proposal_margin`` of them, resolved with
    ``app.ddmrp.zones.setting_value`` (the registry default 0.05 while
    nothing is stored).  The answer is ``{margin,
    item_sites_without_unit_cost, months, record}``: one entry per
    month of the spend answer, in its order, with ``month``,
    ``committed_spend``, ``planned_spend``, ``total_spend`` and
    ``budget`` (the stored budget, or null) copied from it,
    ``proposed_budget`` ``propose_budget``'s round-up of the total
    spend grown by the margin and ``difference`` the stored budget
    minus the proposal, null without a budget, and ``record`` the
    measure ``_record_count`` of the record rows the saves append:
    over the latest record row of each month that still has a stored
    budget, ``saved`` counts those rows, ``with_proposal`` those
    whose ``proposed_amount`` is not null, and of those, ``taken``
    the amount within 0.005 of the proposal, ``below`` the amount
    under it by more and ``above`` the amount over it by more — so
    ``taken + below + above = with_proposal``.  Read-only: it writes
    nothing.
    """
    stored = _stored_system_settings()
    margin = setting_value(stored, PROPOSAL_MARGIN_KEY)
    spend = spend_against_budget(months=months)
    proposed_months = []
    for row in spend["months"]:
        proposed = propose_budget(row["total_spend"], margin)
        budget = row["budget"]
        proposed_months.append(
            {
                "month": row["month"],
                "committed_spend": row["committed_spend"],
                "planned_spend": row["planned_spend"],
                "total_spend": row["total_spend"],
                "proposed_budget": proposed,
                "budget": budget,
                "difference": None if budget is None else budget - proposed,
            }
        )
    return {
        "margin": margin,
        "item_sites_without_unit_cost": spend["item_sites_without_unit_cost"],
        "months": proposed_months,
        "record": _record_count(),
    }


@router.put("/{month}", response_model=PurchaseBudgetOut)
def upsert_budget(
    month: Annotated[str, Path(pattern=MONTH_PATTERN)],
    body: PurchaseBudgetIn,
    current_user: AuthUser = Depends(require_admin),
    _finite_amount: None = Depends(_reject_non_finite_amount),
) -> dict:
    """Store one month's budget, with its record row; needs the admin
    role (403 otherwise).

    A new month inserts a budget row; an existing one updates its
    amount, updated_at and updated_by — ``updated_by`` is the
    caller's username — instead of creating a duplicate.  Beside the
    budget it appends one row of ``purchase_budget_proposals``, in
    the same database transaction: the saved amount and the caller's
    username, the month's proposal at that moment (the way
    ``GET /api/purchase-budgets/proposals`` computes it, over a
    12-month horizon — ``propose_budget`` of the month's
    ``total_spend`` of ``spend_against_budget(months=12)`` grown by
    the ``sop.budget_proposal_margin`` margin, the margin stored
    too), a null proposal with a null margin when the month is
    outside that horizon, or when the spend read raises.  A failed
    append rolls the budget save back and answers 500, so a budget is
    never stored without its record row.  The month in the path must
    be ``YYYY-MM`` with a month of 01 to 12 and the body's ``amount``
    a finite number between 0 and 1,000,000,000,000 inclusive;
    anything else answers 422 before anything is written.  Returns
    the stored budget row, exactly as before the record.
    """
    saved_by = current_user.username or ""
    now = datetime.now(UTC)
    proposed_amount, margin = _proposal_for_saved_month(month)
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO purchase_budgets "
            "(month, amount, updated_at, updated_by) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (month) DO UPDATE "
            "SET amount = EXCLUDED.amount, updated_at = %s, updated_by = %s "
            "RETURNING *",
            (month, body.amount, now, saved_by, now, saved_by),
        )
        saved = _budget_row(cur.fetchone())
        _insert_record(cur, month, body.amount, proposed_amount, margin, saved_by)
    return saved


@router.delete("/{month}", status_code=204)
def delete_budget(
    month: Annotated[str, Path(pattern=MONTH_PATTERN)],
    current_user: AuthUser = Depends(require_admin),
) -> None:
    """Remove one month's budget; needs the admin role (403 otherwise).

    204 with no body when a row was removed, 404 when the month has no
    budget.
    """
    if not _repo.delete(month):
        raise HTTPException(
            status_code=404,
            detail=f"Unknown purchase budget: {month}",
        )
    return None
