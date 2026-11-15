"""Per-month purchase budgets: the monthly purchasing cap.

``GET /api/purchase-budgets`` lists the stored budgets in month order
and is open to any signed-in user.  ``PUT /api/purchase-budgets/{month}``
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
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from app.api.erp_mappings import (
    require_admin,
    require_any_authenticated_user,
)
from app.auth.models import AuthUser
from app.models.purchase_budgets import (
    MONTH_PATTERN,
    PurchaseBudgetIn,
    PurchaseBudgetOut,
)
from app.repositories.purchase_budgets_postgres_repository import (
    PurchaseBudgetPostgresRepository,
)

router = APIRouter(prefix="/api/purchase-budgets", tags=["purchase-budgets"])

_repo = PurchaseBudgetPostgresRepository()


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
