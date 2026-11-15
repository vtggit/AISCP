"""Read-only API for reorder point drift per item and site."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from app.api.planner_decisions import PlannerDecision
from app.api.recommendations import OrderQuantity, ServiceLevelSource
from app.db.connection import get_cursor
from app.recommend.drift import drift_ranked
from app.recommend.export import drift_csv
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.planner_decisions_postgres_repository import (
    PlannerDecisionPostgresRepository,
)
from app.stats.demand import default_as_of

router = APIRouter(prefix="/api/drift", tags=["drift"])

_repo = PlannerDecisionPostgresRepository()

_NUL = chr(0)


def _reject_nul_bytes(*values: str | None) -> None:
    for value in values:
        if value is not None and _NUL in value:
            raise HTTPException(
                status_code=422,
                detail="drift filters must not contain NUL bytes",
            )


class DriftRow(BaseModel):
    """One item-site's reorder point drift row.

    ``item_description`` is the item's description from the
    ``inventory_items`` read model, looked up by ``item_code``
    (null when the item is not in the read model).
    """

    item_code: str
    site_code: str
    as_of: date
    category: str
    rank: int | None = None
    direction: str
    units_gap: float
    money_at_stake: float | None = None
    unit_cost: float | None = None
    notes: list[str]
    money_basis: str
    reorder_point: float
    reorder_point_units: int
    safety_stock: float
    arithmetic: str
    current_reorder_point: float | None = None
    mu_l: float | None = None
    sigma_l: float | None = None
    k: float | None = None
    units_short_per_cycle: float | None = None
    order_qty: float | None = None
    cycles_per_year: float | None = None
    units_short_per_year: float | None = None
    item_description: str | None = None


class DriftOtherBasis(BaseModel):
    """The other demand basis' whole-unit results of the recommendation.

    The drift row's view of the recommendation's ``other_basis``:
    ``basis`` is ``"history"`` or ``"forecast"`` — the basis that is
    not the recommendation's active one — and ``reorder_point_units``
    and ``safety_stock_units`` are that basis' whole-unit results, the
    same values the recommendation's ``other_basis`` carries for them.
    """

    basis: str
    reorder_point_units: int
    safety_stock_units: int


class DriftDecisionRow(DriftRow):
    """A drift row enriched with the item-site's current planner decision.

    ``decision`` is the item-site's current decision from
    ``planner_decisions`` (the latest row by ``decided_at``, ``id``
    breaking ties — the same row ``GET /api/drift/decisions`` reports
    as current) with every stored column, or null when the item-site
    has none.  ``decision_lapsed`` is true only while the current
    decision is a dismissal whose ``recommended_reorder_point_units``
    differs from the row's ``reorder_point_units`` by at least the
    larger of 1 and 10% of that recommended value; it is false for
    every other case, including rows with no decision.

    ``demand_basis`` names the basis (the ``replenishment.demand_basis``
    value) the recommendation's reorder point was built on, and
    ``other_basis`` holds the other basis' whole-unit results (see
    ``DriftOtherBasis``) — null when the recommendation's
    ``other_basis`` is.  ``order_quantity`` is the recommendation's
    recommended order quantity (see ``OrderQuantity``) — null when
    the recommendation's is.  ``service_level_source`` names where the
    recommendation's service level came from (see
    ``ServiceLevelSource``) and ``service_level`` is that service
    level — the recommendation's ``inputs["service_level"]`` — null
    when the recommendation carries no ``service_level_source`` or no
    ``inputs`` ``service_level``.
    """

    decision: PlannerDecision | None = None
    decision_lapsed: bool = False
    demand_basis: str | None = None
    other_basis: DriftOtherBasis | None = None
    order_quantity: OrderQuantity | None = None
    service_level_source: ServiceLevelSource | None = None
    service_level: float | None = None


_LAPSE_FRACTION = 0.10


def _decision_lapsed(row: dict, decision: dict) -> bool:
    """Whether a dismissal has lapsed against its own recommendation.

    A dismissal lapses when the row's ``reorder_point_units`` differs
    from the decision's ``recommended_reorder_point_units`` by at least
    the larger of 1 and 10% of ``recommended_reorder_point_units``.
    Only dismissals lapse: an accepted decision never does, and a
    snoozed row's visibility depends only on its ``snooze_until``, so
    the lapse threshold never applies to it.
    """
    if decision["decision"] != "dismissed":
        return False
    recommended = float(decision["recommended_reorder_point_units"])
    threshold = max(1.0, _LAPSE_FRACTION * recommended)
    return abs(float(row["reorder_point_units"]) - recommended) >= threshold


def _decision_hides_row(decision: dict | None, lapsed: bool) -> bool:
    """Whether the default list (``include_decided`` false) leaves the
    row out.  Only a not-yet-lapsed dismissal and a snooze whose
    ``snooze_until`` is after today hide their row; accepted, lapsed,
    undecided, and snoozed-until-today-or-earlier rows stay listed."""
    if decision is None:
        return False
    kind = decision["decision"]
    if kind == "dismissed":
        return not lapsed
    if kind == "snoozed":
        snooze_until = decision["snooze_until"]
        return snooze_until is not None and snooze_until > date.today()
    return False


def _enriched_rows(rows: list[dict]) -> list[tuple[dict, bool]]:
    """Attach each row's current decision (one query per request).

    Returns ``(row, hidden)`` pairs: the row dict gains ``decision``
    (every stored column, or null) and ``decision_lapsed``, and
    ``hidden`` says whether the default list leaves the row out.  The
    current decisions are read once for the whole request, never one
    query per row, and every item-site's current decision is read even
    when it sits past the first page of
    ``GET /api/drift/decisions``.
    """
    current = {
        (decision["item_code"], decision["site_code"]): decision
        for decision in _repo.current_all()
    }
    pairs: list[tuple[dict, bool]] = []
    for row in rows:
        decision = current.get((row["item_code"], row["site_code"]))
        lapsed = _decision_lapsed(row, decision) if decision is not None else False
        pairs.append(
            (
                {
                    **row,
                    "decision": (
                        PlannerDecision.model_validate(decision)
                        if decision is not None
                        else None
                    ),
                    "decision_lapsed": lapsed,
                },
                _decision_hides_row(decision, lapsed),
            )
        )
    return pairs


def _item_descriptions() -> dict[str, str | None]:
    """One inventory_items read per request, keyed by item_code.

    The value is the item's stored description (null when it is
    null), so a row's item missing from the read model looks up to
    null rather than failing the request.
    """
    return {
        item["item_code"]: item.get("description")
        for item in InventoryItemPostgresRepository().list_all()
    }


@router.get("", response_model=list[DriftDecisionRow])
def list_drift(
    limit: int = Query(default=20, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    direction: Literal["over", "under"] | None = Query(default=None),
    category: Literal["drift", "no_reorder_point"] | None = Query(default=None),
    as_of: date | None = Query(default=None),
    include_decided: bool = Query(default=False),
) -> list[dict]:
    """List reorder point drift ranked by money at stake, drift rows first.

    Every row carries its current planner decision (``decision``, or
    null) and ``decision_lapsed``.  While ``include_decided`` is false,
    a row whose current decision is a not-yet-lapsed dismissal, or a
    snooze whose ``snooze_until`` is after today, is left out of the
    list; with ``include_decided`` true every row is listed.  Every
    row also carries ``item_description``, the item's description
    from the ``inventory_items`` read model (null when the item is
    not in it), from one read per request.  Rows are left out before
    ``offset`` and ``limit`` are applied, and every row keeps the
    ``rank`` that ``drift_ranked`` gave it.
    """
    try:
        rows = drift_ranked(as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if direction is not None:
        rows = [row for row in rows if row["direction"] == direction]
    if category is not None:
        rows = [row for row in rows if row["category"] == category]
    enriched = _enriched_rows(rows)
    descriptions = _item_descriptions()
    if include_decided:
        paged = [row for row, _hidden in enriched][offset : offset + limit]
    else:
        paged = [row for row, hidden in enriched if not hidden][offset : offset + limit]
    return [
        {**row, "item_description": descriptions.get(row["item_code"])} for row in paged
    ]


@router.get("/export.csv")
def export_drift(
    direction: Literal["over", "under"] | None = Query(default=None),
    category: Literal["drift", "no_reorder_point"] | None = Query(default=None),
    as_of: date | None = Query(default=None),
) -> Response:
    """Export every matching drift row as a CSV attachment (no limit)."""
    if as_of is None:
        with get_cursor() as cur:
            as_of = default_as_of(cur)
    try:
        rows = drift_ranked(as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if direction is not None:
        rows = [row for row in rows if row["direction"] == direction]
    if category is not None:
        rows = [row for row in rows if row["category"] == category]
    return Response(
        content=drift_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="drift-{as_of.isoformat()}.csv"'
        },
    )


@router.get("/by-key", response_model=DriftDecisionRow)
def get_drift_by_key(
    item_code: str = Query(..., max_length=64),
    site_code: str = Query(..., max_length=64),
) -> dict:
    """Fetch one item-site's drift row with its current planner decision.

    The row is always returned with its decision and is never hidden by
    a dismissal or a snooze; 404 when the item-site has no "ok"
    recommendation.
    """
    _reject_nul_bytes(item_code, site_code)
    try:
        rows = drift_ranked()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    for row, _hidden in _enriched_rows(rows):
        if row["item_code"] == item_code and row["site_code"] == site_code:
            return row
    raise HTTPException(status_code=404, detail="no ok recommendation for item site")
