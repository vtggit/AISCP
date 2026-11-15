"""API for stored demand signals and their withdrawals (issue #963).

``POST /api/demand-signals`` (admin only) stores one demand signal —
a promotion, deal or event that lifts demand for one item-site over a
date window — and answers 201 with the stored row.  ``POST
/api/demand-signals/{id}/withdraw`` (admin only) withdraws it: it
stores one ``withdrawal`` row carrying the signal's item-site and
dates and answers 201 with it.  ``GET /api/demand-signals`` (any
signed-in user) lists the signal rows newest first, never a
withdrawal row as its own entry; each row carries its withdrawal's
values when one exists.

Nothing else reads the table yet: the forecast, accuracy,
recommendation, projection and ERP routes are unchanged.
"""

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import BaseModel, ConfigDict

from app.auth.authorization import require_role
from app.auth.models import AuthUser
from app.forecast.signals import create_signal, list_signals, withdraw_signal

router = APIRouter(prefix="/api/demand-signals", tags=["demand-signals"])


class DemandSignalBody(BaseModel):
    """The body of POST /api/demand-signals.

    ``kind`` is "promotion", "deal" or "event"; ``start_date`` after
    the shared default as-of date, ``end_date`` not before
    ``start_date`` and not past the last day of the 12 forecast
    periods at that as-of date; ``quantity`` above 0, finite and at
    most 1,000,000,000; ``reason`` non-blank, at most 1,000
    characters, stored trimmed.  Every business refusal answers 422
    with a sentence in ``detail`` and nothing is stored; extra
    fields answer 422.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str
    item_code: str
    site_code: str
    start_date: date
    end_date: date
    quantity: float
    reason: str


class DemandSignalWithdrawalBody(BaseModel):
    """The body of POST /api/demand-signals/{id}/withdraw.

    A blank or over-1,000-character reason answers 422 with nothing
    stored.  An unknown id answers 404; an id that is itself a
    withdrawal, an already withdrawn signal, and a signal whose
    window has passed each answer 409.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str


class DemandSignalRow(BaseModel):
    """One stored demand signal, or the stored row of its withdrawal.

    A signal row carries ``withdrawn`` false and null
    ``withdrawn_*`` values while it stands; once withdrawn it carries
    the withdrawal's values.  A withdrawal row answers its own POST
    with ``withdrawn`` true and its own values beside it.
    ``created_by_name`` is the caller's display name as the decision
    tables store it, through the shared
    ``app.auth.display_name.display_name`` helper — never null on a
    row the API stored; it stays nullable only for rows written out
    of band.
    """

    id: int
    kind: Literal["promotion", "deal", "event", "withdrawal"]
    item_code: str
    site_code: str
    start_date: date
    end_date: date
    quantity: float | None
    reason: str
    created_by_name: str | None
    created_at: datetime
    withdrawn: bool
    withdrawn_at: datetime | None
    withdrawn_by_name: str | None
    withdrawn_reason: str | None


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DemandSignalRow,
)
def create_demand_signal(
    body: DemandSignalBody,
    user: AuthUser = Depends(require_role("admin")),
) -> dict:
    """Store one demand signal (admin only).

    Stores one row — kind, the item-site, the date window, the
    quantity, the trimmed reason, the caller's subject and display
    name — and answers 201 with the stored row.  A kind other than
    promotion, deal or event; a quantity not above 0, not finite, or
    above 1,000,000,000; a blank or over-1,000-character reason; a
    start date on or before the shared default as-of date; an end
    date before the start date or past the last day of the 12
    forecast periods; and an item-site with no statistical forecast
    each answer 422 with a sentence in ``detail`` and nothing is
    stored.
    """
    return create_signal(
        body.kind,
        body.item_code,
        body.site_code,
        body.start_date,
        body.end_date,
        body.quantity,
        body.reason,
        user,
    )


@router.post(
    "/{signal_id}/withdraw",
    status_code=status.HTTP_201_CREATED,
    response_model=DemandSignalRow,
)
def withdraw_demand_signal(
    body: DemandSignalWithdrawalBody,
    signal_id: int = Path(..., ge=1),
    user: AuthUser = Depends(require_role("admin")),
) -> dict:
    """Withdraw one stored demand signal (admin only).

    Stores one ``withdrawal`` row carrying the signal's item-site
    and dates and answers 201 with it; the signal row stays in the
    history.  An unknown id answers 404.  An id that is itself a
    withdrawal, a signal already withdrawn, and a signal whose
    ``end_date`` is on or before the shared default as-of date each
    answer 409 with the stored rows unchanged.  Two concurrent
    withdrawals store one row; the other answers 409.
    """
    return withdraw_signal(signal_id, body.reason, user)


@router.get("", response_model=list[DemandSignalRow])
def list_demand_signals(
    item_code: str | None = Query(default=None),
    site_code: str | None = Query(default=None),
    include_withdrawn: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    """List the stored demand signals, newest first, any signed-in user.

    ``item_code`` and ``site_code`` select one item-site when both
    are given — giving exactly one of the two answers 422 with a
    sentence in ``detail`` and nothing is returned; with neither
    they select every signal row.  Withdrawal rows are never listed
    as their own entries: each signal row carries ``withdrawn`` and
    the withdrawal's values beside it.  The default list omits
    withdrawn signals; ``include_withdrawn`` lists them too.
    ``limit`` (default 100, 1 to 500) pages the list.
    """
    return list_signals(
        item_code=item_code,
        site_code=site_code,
        include_withdrawn=include_withdrawn,
        limit=limit,
    )
