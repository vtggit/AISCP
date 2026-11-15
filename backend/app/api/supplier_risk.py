"""Read-only API for the supplier risk rating.

The ``variable`` sign needs evidence — the supplier's lead-time
stats must not be low confidence (at least three used observations) —
and follows the rounded ``lead_time_cv`` the route returns, so at
the 0.6 default ceiling a displayed 0.60 is variable and a displayed
0.50 is not.  ``risk`` is high when ``late`` or ``variable`` with a
sole source, medium when ``late`` or ``variable`` without one, and
low when neither ``late`` nor ``variable``, whether or not sole
source.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.recommend.supplier_risk import supplier_risk

router = APIRouter(prefix="/api", tags=["supplier-risk"])


class SupplierRiskEntry(BaseModel):
    """One supplier's risk signs over the 730-day window."""

    supplier_code: str
    receipts: int
    lead_time_mean_days: float | None
    lead_time_std_days: float | None
    lead_time_cv: float | None
    on_time_rate: float | None
    sole_source_items: int
    sole_source_item_codes: list[str] = []
    late: bool
    variable: bool
    sole_source: bool
    risk: Literal["high", "medium", "low"]
    vendor_code: str | None = None
    supplier_name: str | None = None


class SupplierRiskResponse(BaseModel):
    """Per-supplier risk for the 730-day window ending on as_of."""

    as_of: str
    suppliers: list[SupplierRiskEntry]


@router.get(
    "/supplier-risk",
    response_model=SupplierRiskResponse,
)
def get_supplier_risk() -> dict:
    """Per-supplier risk: the late, variable and sole-source signs.

    Open to any signed-in user behind the app's auth gate (401
    without a valid Bearer token).  The lead-time figures come from
    the same 730-day window ``lead_time_stats_for_all`` uses; the
    on-time rate is the Suppliers screen's default-window figure;
    the thresholds are the stored ``supplier_risk.on_time_floor``
    (0.85 by default) and ``supplier_risk.lead_time_cv_ceiling``
    (0.6 by default) settings.  ``variable`` needs evidence — the
    supplier's lead-time stats must not be low confidence (at least
    three used observations) — and follows the same rounded
    ``lead_time_cv`` this route returns: at the 0.6 default ceiling
    a displayed 0.60 is variable and a displayed 0.50 is not.
    ``risk`` is high when the supplier is ``late`` or ``variable``
    and also ``sole_source``, medium when it is ``late`` or
    ``variable`` without being ``sole_source``, and low when it is
    neither ``late`` nor ``variable``, whether or not it is
    ``sole_source``.  Entries are ordered high, then medium, then
    low, then ``sole_source_items`` descending, then
    ``supplier_code``.  The route writes nothing and calls no ERP.
    """
    return supplier_risk()
