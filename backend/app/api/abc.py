"""Read-only API for the ABC classification of the item-sites."""

from datetime import date

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.db.connection import get_cursor
from app.recommend.abc import abc_for_all
from app.stats.demand import default_as_of

router = APIRouter(prefix="/api/abc", tags=["abc"])


class AbcRow(BaseModel):
    """One item-site's ABC row (an ``app.recommend.abc.classify`` row).

    ``annual_value`` is a year of the item-site's daily demand at its
    unit cost (``365 * d * unit_cost`` of its ``"ok"`` recommendation)
    and null when the item-site is unclassified; ``rank`` is 1-based
    among the classified item-sites (null when unclassified),
    ``share_before`` the cumulative share of the annual values ranked
    before the item-site (null when unclassified) and ``abc_class``
    the ``"A"`` / ``"B"`` / ``"C"`` class (null when unclassified).
    """

    item_code: str
    site_code: str
    annual_value: float | None = None
    rank: int | None = None
    share_before: float | None = None
    abc_class: str | None = None


class AbcCounts(BaseModel):
    """How many item-sites landed in each class (and how many did not)."""

    A: int
    B: int
    C: int
    unclassified: int


class AbcResponse(BaseModel):
    """The ABC classification of the item-sites as of ``as_of``.

    ``as_of`` is the query value when one was given, else the shared
    default as-of date (``app.stats.demand.default_as_of``); it is
    never null. ``total_annual_value`` is the sum of the classified
    item-sites' annual values (0.0 when there are none), ``counts``
    the :class:`AbcCounts` of the rows and ``rows`` the
    ``app.recommend.abc.abc_for_all`` output.
    """

    as_of: date
    total_annual_value: float
    counts: AbcCounts
    rows: list[AbcRow]


@router.get("", response_model=AbcResponse)
def get_abc(as_of: date | None = Query(default=None)) -> dict:
    """The ABC classification of the item-sites, ranked by annual value.

    ``rows`` is the ``app.recommend.abc.abc_for_all`` output built from
    one ``recommendations_for_all`` call: the classified item-sites in
    rank order, the unclassified ones after them by ``item_code`` then
    ``site_code``.
    """
    if as_of is None:
        with get_cursor() as cur:
            as_of = default_as_of(cur)
    rows = abc_for_all(as_of)
    counts = {"A": 0, "B": 0, "C": 0, "unclassified": 0}
    total = 0.0
    for row in rows:
        counts[
            row["abc_class"] if row["abc_class"] is not None else "unclassified"
        ] += 1
        if row["annual_value"] is not None:
            total += row["annual_value"]
    return {
        "as_of": as_of,
        "total_annual_value": float(total),
        "counts": counts,
        "rows": rows,
    }
