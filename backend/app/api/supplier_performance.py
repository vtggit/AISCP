"""Read-only API for supplier performance over observed PO receipts."""

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.recommend.supplier_performance import (
    DEFAULT_WINDOW_DAYS,
    supplier_performance,
)

router = APIRouter(prefix="/api/suppliers", tags=["supplier-performance"])


class SupplierPerformanceEntry(BaseModel):
    """One supplier's on-time and lead-time performance in the window."""

    supplier_code: str
    receipts: int
    with_promise: int
    default_promise: int
    on_time: int
    late: int
    on_time_rate: float | None
    avg_days_late: float | None
    max_days_late: int | None
    avg_lead_time_days: float
    p90_lead_time_days: int
    last_received_on: str
    vendor_code: str | None = None
    supplier_name: str | None = None


class SupplierPerformanceResponse(BaseModel):
    """Per-supplier performance for the window ending on as_of."""

    as_of: str
    window_days: int
    suppliers: list[SupplierPerformanceEntry]


@router.get(
    "/performance",
    response_model=SupplierPerformanceResponse,
    response_model_exclude_unset=True,
)
def get_supplier_performance(
    window_days: int = Query(default=DEFAULT_WINDOW_DAYS),
    as_of: date | None = Query(default=None),
) -> dict:
    """Per-supplier on-time and lead-time performance over the window.

    The window is the ``window_days`` days ending on ``as_of`` (the
    server's current date when omitted).  A ``window_days`` outside 1
    to 1825, or an ``as_of`` after today, answers 422.  Entries are
    ordered by ``on_time_rate`` ascending with nulls last, then
    ``late`` descending, then ``supplier_code``.
    """
    try:
        return supplier_performance(as_of=as_of, window_days=window_days)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
