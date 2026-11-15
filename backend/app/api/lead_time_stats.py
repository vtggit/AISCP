"""Read-only API for lead time statistics per item and supplier."""

from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.stats.lead_time import lead_time_stats_for_all, lead_time_stats_for_item

router = APIRouter(prefix="/api/lead-time-stats", tags=["lead-time-stats"])

_NUL = chr(0)


def _reject_nul_bytes(*values: str | None) -> None:
    for value in values:
        if value is not None and _NUL in value:
            raise HTTPException(
                status_code=422,
                detail="lead time stats filters must not contain NUL bytes",
            )


class LeadTimeStats(BaseModel):
    """Outlier-fenced lead time summary over one observation list."""

    n_observations: int
    n_used: int
    n_dropped: int
    fence: float | None
    mean_days: float
    std_days: float
    min_days: float
    max_days: float
    low_confidence: bool
    method: str


class SupplierLeadTimeStats(LeadTimeStats):
    """One supplier's lead time stats for an item."""

    supplier_code: str


class ItemLeadTimeStats(LeadTimeStats):
    """One item's pooled lead time stats with the per-supplier breakdown."""

    item_code: str
    as_of: date
    suppliers: list[SupplierLeadTimeStats]


@router.get("", response_model=list[ItemLeadTimeStats])
def list_lead_time_stats(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, le=1_000_000_000),
    item_code: str | None = Query(default=None, max_length=64),
    as_of: date | None = Query(default=None),
) -> list[dict]:
    """List lead time stats per item (all suppliers pooled), ordered by item_code."""
    _reject_nul_bytes(item_code)
    rows = lead_time_stats_for_all(as_of)
    if item_code is not None:
        rows = [row for row in rows if row["item_code"] == item_code]
    return rows[offset : offset + limit]


@router.get("/by-item", response_model=ItemLeadTimeStats)
def get_lead_time_stats_by_item(
    item_code: str = Query(..., max_length=64),
    as_of: date | None = Query(default=None),
) -> dict:
    """Fetch one item's pooled lead time stats with its per-supplier breakdown;
    404 when the item has no observation in the window."""
    _reject_nul_bytes(item_code)
    row = lead_time_stats_for_item(item_code, as_of)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="lead time stats not found",
        )
    return row
