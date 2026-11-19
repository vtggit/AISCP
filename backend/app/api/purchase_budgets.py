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
``item_sites_without_unit_cost`` on top.  It is read-only, open to any
signed-in user like the list, and its ``months`` query parameter runs
from 1 to 12 and defaults to 6, a value outside that range answering
422 before anything is read.  ``PUT /api/purchase-budgets/{month}``
stores one month's budget — a new row, or the stored amount of the
existing one on the ``month`` unique key, with ``updated_at`` and
``updated_by`` (the caller's username) bumped — and
``DELETE /api/purchase-budgets/{month}`` removes one; both need the
admin role, a signed-in non-admin getting 403 exactly as the
erp-mappings routes refuse.  A month outside ``YYYY-MM`` with a month
of 01 to 12, a missing amount, a negative or non-finite amount or one
above 1,000,000,000,000 answers 422 before anything is written — and
for the admin-only routes the 401/403 from authentication and
authorization come first.
"""

import json
import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel

from app.aggregate.spend import spend_against_budget
from app.api.erp_mappings import (
    require_admin,
    require_any_authenticated_user,
)
from app.auth.models import AuthUser
from app.ddmrp.zones import setting_value
from app.models.purchase_budgets import (
    MONTH_PATTERN,
    PurchaseBudgetIn,
    PurchaseBudgetOut,
)
from app.repositories.purchase_budgets_postgres_repository import (
    PurchaseBudgetPostgresRepository,
)
from app.sop.review import _stored_system_settings

# The system setting that grows a month's projected spend into the
# proposed budget.
PROPOSAL_MARGIN_KEY = "sop.budget_proposal_margin"

router = APIRouter(prefix="/api/purchase-budgets", tags=["purchase-budgets"])

_repo = PurchaseBudgetPostgresRepository()


def propose_budget(total_spend: float, margin: float) -> float:
    """The proposed budget of one month from its projected spend.

    The rule: ``total_spend * (1 + margin)`` rounded up to three
    significant figures — divide the amount by the step that leaves
    three significant digits and round the quotient up — and 0 for a
    total of 0.  The proof's examples:

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


class BudgetProposals(BaseModel):
    """The proposed budgets of the projected spend horizon."""

    margin: float
    item_sites_without_unit_cost: int
    months: list[ProposalMonth]


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
    item_sites_without_unit_cost, months}``: one entry per month of
    the spend answer, in its order, with ``month``,
    ``committed_spend``, ``planned_spend``, ``total_spend`` and
    ``budget`` (the stored budget, or null) copied from it,
    ``proposed_budget`` ``propose_budget``'s round-up of the total
    spend grown by the margin and ``difference`` the stored budget
    minus the proposal, null without a budget.  Read-only: it writes
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
    }


@router.put("/{month}", response_model=PurchaseBudgetOut)
def upsert_budget(
    month: Annotated[str, Path(pattern=MONTH_PATTERN)],
    body: PurchaseBudgetIn,
    current_user: AuthUser = Depends(require_admin),
    _finite_amount: None = Depends(_reject_non_finite_amount),
) -> dict:
    """Store one month's budget; needs the admin role (403 otherwise).

    A new month inserts a row; an existing one updates its amount,
    updated_at and updated_by — ``updated_by`` is the caller's
    username — instead of creating a duplicate.  The month in the path
    must be ``YYYY-MM`` with a month of 01 to 12 and the body's
    ``amount`` a finite number between 0 and 1,000,000,000,000
    inclusive; anything else answers 422 before anything is written.
    Returns the stored row.
    """
    return _repo.upsert(
        month=month,
        amount=body.amount,
        updated_by=current_user.username or "",
    )


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
