"""Read-only API for stockout risk per item and site."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from app.api.stockout_decisions import StockoutDecision
from app.db.connection import get_cursor
from app.recommend.export import stockout_csv
from app.recommend.stockout import stockout_ranked
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.stockout_decisions_postgres_repository import (
    StockoutDecisionPostgresRepository,
)
from app.stats.demand import default_as_of

router = APIRouter(prefix="/api/stockout", tags=["stockout"])

_repo = StockoutDecisionPostgresRepository()


class StockoutRow(BaseModel):
    """One item-site's stockout risk row.

    ``open_supply_units`` is the open purchase supply due within the
    lead time and ``open_supply_due_by`` the latest due date counted in
    it (null when nothing counts). ``unit_cost`` and ``money_at_risk``
    are null when the item-site has no unit cost, and
    ``days_of_cover`` / ``projected_stockout_on`` are null when there is
    no demand to cover.  ``item_description`` is the item's
    description from the ``inventory_items`` read model, looked up
    by ``item_code`` (null when the item is not in the read model).
    """

    rank: int
    category: Literal["likely", "at_risk"]
    item_code: str
    site_code: str
    on_hand_units: float
    open_supply_units: float
    open_supply_due_by: date | None = None
    unit_cost: float | None = None
    lead_time_days: float
    lead_time_demand: float
    p_stockout: float
    days_of_cover: float | None = None
    projected_stockout_on: date | None = None
    recommended_reorder_point_units: int | None = None
    expected_units_short: float
    money_at_risk: float | None = None
    as_of: date
    notes: list[str]
    arithmetic: str
    item_description: str | None = None


def _filtered(
    rows: list[dict], category: Literal["likely", "at_risk"] | None
) -> list[dict]:
    """The rows matching the filter."""
    if category is not None:
        rows = [row for row in rows if row["category"] == category]
    return rows


def _ranked(as_of: date | None) -> list[dict]:
    try:
        return stockout_ranked(as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class StockoutDecisionRow(StockoutRow):
    """A stockout row enriched with the item-site's current decision.

    ``decision`` is the item-site's current decision from
    ``stockout_decisions`` (the latest row by ``decided_at``, ``id``
    breaking ties — the same row ``GET /api/stockout/decisions``
    reports as current) with every stored column, or null when the
    item-site has none.  ``decision_lapsed`` is true only for a current
    dismissal that the row has outgrown: the row's ``p_stockout``
    exceeds the decision's ``p_stockout`` by at least 0.10.  A snooze
    never lapses, and neither does anything else (accepted decisions,
    or no decision at all).
    """

    decision: StockoutDecision | None = None
    decision_lapsed: bool = False


_LAPSE_THRESHOLD = Decimal("0.10")


def _decision_lapsed(row: dict, decision: dict) -> bool:
    """Whether a dismissal has lapsed against the row it decided on.

    A dismissal lapses when the row's ``p_stockout`` exceeds the
    decision's ``p_stockout`` by at least 0.10.  The comparison is
    exact decimal arithmetic, because a plain float subtraction can
    land just under the threshold although the decimal difference is
    exactly 0.10 (``0.6915 - 0.5915`` is ``0.09999999999999998``):
    the boundary lapses.  Only dismissals lapse: an accepted decision
    never does, and a snoozed row's visibility depends only on its
    ``snooze_until``, so the lapse threshold never applies to it.
    """
    if decision["decision"] != "dismissed":
        return False
    stored_p_stockout = Decimal(str(decision["p_stockout"]))
    return Decimal(str(row["p_stockout"])) - stored_p_stockout >= _LAPSE_THRESHOLD


def _decision_hides_row(decision: dict | None, lapsed: bool) -> bool:
    """Whether the default list (``include_decided`` false) leaves the
    row out.  Only a not-yet-lapsed dismissal and a snooze whose
    ``snooze_until`` is after today (the server's UTC date) hide their
    row; accepted, lapsed, undecided, and snoozed-until-today-or-earlier
    rows stay listed."""
    if decision is None:
        return False
    kind = decision["decision"]
    if kind == "dismissed":
        return not lapsed
    if kind == "snoozed":
        snooze_until = decision["snooze_until"]
        return snooze_until is not None and snooze_until > datetime.now(UTC).date()
    return False


def _enriched_rows(rows: list[dict]) -> list[tuple[dict, bool]]:
    """Attach each row's current decision (one query per request).

    Returns ``(row, hidden)`` pairs: the row dict gains ``decision``
    (every stored column, or null) and ``decision_lapsed``, and
    ``hidden`` says whether the default list leaves the row out.  The
    current decisions are read once for the whole request, never one
    query per row, and every item-site's current decision is read even
    when it sits past the first page of
    ``GET /api/stockout/decisions``.
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
                        StockoutDecision.model_validate(decision)
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


@router.get("", response_model=list[StockoutDecisionRow])
def list_stockout(
    limit: int = Query(default=20, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    category: Literal["likely", "at_risk"] | None = Query(default=None),
    as_of: date | None = Query(default=None),
    include_decided: bool = Query(default=False),
) -> list[dict]:
    """List stockout risk ranked by stockout probability, likely first.

    Every row carries its current stockout decision (``decision``, or
    null) and ``decision_lapsed``.  While ``include_decided`` is false,
    a row whose current decision is a not-yet-lapsed dismissal, or a
    snooze whose ``snooze_until`` is after today (the server's UTC
    date), is left out of the list; accepted, lapsed, undecided and
    expired-snooze rows stay listed, and with ``include_decided`` true
    every row is listed.  Every row also carries
    ``item_description``, the item's description from the
    ``inventory_items`` read model (null when the item is not in
    it), from one read per request.  Rows are left out after the
    ``category`` filter and before ``offset`` and ``limit`` are
    applied, and every row keeps the ``rank`` that ``stockout_ranked``
    gave it.
    """
    rows = _filtered(_ranked(as_of), category)
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
def export_stockout(
    category: Literal["likely", "at_risk"] | None = Query(default=None),
    as_of: date | None = Query(default=None),
) -> Response:
    """Export every matching stockout row as a CSV attachment (no limit)."""
    rows = _filtered(_ranked(as_of), category)
    if rows:
        export_as_of = rows[0]["as_of"]
    elif as_of is not None:
        export_as_of = as_of
    else:
        with get_cursor() as cur:
            export_as_of = default_as_of(cur)
    return Response(
        content=stockout_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="stockout-{export_as_of.isoformat()}.csv"'
        },
    )
