"""Cross-ERP item comparison: a mapped item's two sides, side by side.

``GET /api/cross-erp/items`` (any signed-in user; the router is
mounted behind the app's authentication gate) answers one row per
stored ``erp_code_mappings`` row of kind ``item``, ordered by
``source_code``: the codes, the item description (the
``inventory_items`` description of either code, the source's first,
else null) and the two sides — the source code's item-sites and the
target code's item-sites under ``odoo`` — each side's sites carrying
that item-site's own on hand, daily demand, days of cover,
recommendation status, reorder point and suggested order quantity,
stockout category and excess units.  The row's ``opportunity`` flag
is true when one side has a site at stockout risk (a
``stockout_category``) and the other side has a site with excess
units above 0, ``short_side`` and ``excess_side`` naming the sides'
indexes in ``sides``; with the flag, ``suggested_move_units`` is the
smaller of the need, the short side's non-null-stockout sites summed
over their gap — the site's ``reorder_point_units`` minus its
``on_hand_units``, never below 0, a site with a null reorder point
counting 0 — and the excess side's sites summed over their
``excess_units``, rounded down to a whole number and never below 0,
and null while the flag is false.

The route writes nothing and calls no ERP; the figures come from
``app.recommend.reorder.recommendations_for_all``,
``app.recommend.stockout.stockout_ranked`` and
``app.recommend.excess.excess_ranked`` — the same functions the other
screens read — each called once per request inside one
``recommendations_memo``, so every figure agrees with those screens,
and no figure from one side is added into the other's plan.  The
answer is ``{"as_of": <the shared default as-of date>, "rows": [...]}``;
with no mapping stored the rows are empty and the status is 200.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.recommend.cross_erp import cross_erp_items

router = APIRouter(prefix="/api/cross-erp", tags=["cross-erp"])


class CrossErpSite(BaseModel):
    """One item-site's figures on one side of the comparison.

    ``on_hand_units`` is the stored on hand (0 when there is no
    on-hand row).  ``daily_demand`` is the item-site's
    recommendation's ``inputs.d``, ``days_of_cover`` the on hand over
    the demand rounded to one decimal (null when the demand is 0 or
    null), and ``status``, ``reorder_point_units`` and
    ``suggested_order_quantity`` the recommendation's own fields —
    each null when the item-site has no recommendation.
    ``stockout_category`` is the item-site's ``stockout_ranked``
    category (null when it has no row there) and ``excess_units`` its
    ``excess_ranked`` excess units (0 when it has no row there).
    """

    site_code: str
    on_hand_units: float
    daily_demand: float | None = None
    days_of_cover: float | None = None
    status: str | None = None
    reorder_point_units: int | None = None
    suggested_order_quantity: int | None = None
    stockout_category: Literal["likely", "at_risk"] | None = None
    excess_units: float


class CrossErpSide(BaseModel):
    """One side of the comparison: one code's item-sites.

    ``source_system`` is the source of that side's item-sites (null
    when the side has none), ``sites`` the side's site entries ordered
    by ``site_code`` and ``on_hand_units`` the sum over the sites (0
    with no sites).
    """

    source_system: str | None = None
    item_code: str
    sites: list[CrossErpSite]
    on_hand_units: float


class CrossErpRow(BaseModel):
    """One stored item mapping compared side by side.

    ``description`` is the ``inventory_items`` description of either
    code (the source's first, else null).  ``sides`` is exactly two
    entries — the source code's item-sites first, then the target
    code's item-sites under ``odoo``.  ``opportunity`` is true when
    one side has a site whose ``stockout_category`` is not null and
    the other side has a site whose ``excess_units`` is above 0;
    ``short_side`` and ``excess_side`` then read that side's index in
    ``sides`` and ``suggested_move_units`` is the smaller of the
    need, the short side's non-null-stockout sites summed over their
    gap — the site's ``reorder_point_units`` minus its
    ``on_hand_units``, never below 0, a site with a null reorder
    point counting 0 — and the excess side's sites summed over their
    ``excess_units``, rounded down to a whole number and never below
    0.  Otherwise ``opportunity`` is false and all three are null.
    """

    source_code: str
    target_code: str
    description: str | None = None
    sides: list[CrossErpSide]
    opportunity: bool
    short_side: int | None = None
    excess_side: int | None = None
    suggested_move_units: int | None = None


class CrossErpItemsResponse(BaseModel):
    """The cross-ERP item comparison answer.

    ``as_of`` is the shared default as-of date; ``rows`` is one entry
    per stored mapping of kind ``item``, ordered by ``source_code``
    (empty — still a 200 — while no mapping is stored).
    """

    as_of: date
    rows: list[CrossErpRow]


@router.get("/items", response_model=CrossErpItemsResponse)
def list_cross_erp_items() -> dict:
    """The stored item mappings compared side by side, read-only.

    Open to any signed-in user (the router is mounted behind the
    app's authentication gate: a non-admin signed-in user gets 200
    and no token 401).  The route writes nothing and calls no ERP;
    the figures come from the same shared functions the other screens
    read, each called once per request inside one
    ``recommendations_memo``, so every figure agrees with those
    screens.  With the ``opportunity`` flag,
    ``suggested_move_units`` is the smaller of the need, the short
    side's non-null-stockout sites summed over their gap — the
    site's ``reorder_point_units`` minus its ``on_hand_units``,
    never below 0, a site with a null reorder point counting 0 — and
    the excess side's sites summed over their ``excess_units``,
    rounded down to a whole number and never below 0; without the
    flag it is null.  With no mapping stored the answer is the
    shared default as-of date and empty rows, with status 200.
    """
    return cross_erp_items()
