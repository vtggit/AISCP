"""Read-only API for open purchase lines to expedite."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.recommend.expedite import expedite_list

router = APIRouter(prefix="/api/expedite", tags=["expedite"])


class ExpediteLine(BaseModel):
    """One open purchase line to expedite.

    ``order_type``, ``order_nbr`` and ``line_nbr`` come from the line's
    business key ``OrderType:OrderNbr:LineNbr`` (null when the key is
    too short to carry them). ``open_value`` is null without an
    item-site unit cost, ``days_late`` is null when the line is not
    late, and ``d``, ``days_of_cover`` and ``projected_stockout_on``
    are null when the item-site has no "ok" recommendation with daily
    demand above 0.
    """

    order_type: str | None = None
    order_nbr: str | None = None
    line_nbr: str | None = None
    item_code: str
    site_code: str
    supplier_code: str | None = None
    ordered_on: date | None = None
    promised_on: date
    expected_on: date
    promise_is_default: bool
    open_units: float
    open_value: float | None = None
    days_late: int | None = None
    on_hand_units: float
    d: float | None = None
    days_of_cover: float | None = None
    projected_stockout_on: date | None = None
    reasons: list[Literal["late", "after_stockout"]]
    supplier_on_time_rate: float | None = None
    vendor_code: str | None = None
    supplier_name: str | None = None
    item_description: str | None = None


class ExpediteResponse(BaseModel):
    """Open purchase lines to expedite for the as-of date.

    ``lines`` rank by soonest projected stockout (no projected date
    last), then larger days late, then larger open value, then order
    and line number; ``stale_excluded`` counts the undated lines and
    the lines promised more than 90 days before ``as_of`` that are
    left out of the list.
    """

    as_of: str
    lines: list[ExpediteLine]
    stale_excluded: int


@router.get("", response_model=ExpediteResponse, response_model_exclude_unset=True)
def list_expedite(as_of: date | None = Query(default=None)) -> dict:
    """Open purchase lines to expedite, ranked by stockout urgency.

    Every open line with ``open_units`` above 0 that is not more than
    90 days stale is checked: a line is listed when its expected
    arrival (the promise, or the order date plus the supplier's mean
    lead time for the ERP's default promise) is before ``as_of``
    (``late``) or after the item-site's projected stockout date
    (``after_stockout``). ``as_of`` defaults to the server's current
    date; one after today answers 422.
    """
    try:
        return expedite_list(as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
