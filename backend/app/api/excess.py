"""Read-only API for excess and dead stock per item and site."""

from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from app.api.excess_decisions import ExcessDecision
from app.db.connection import get_cursor
from app.recommend.excess import excess_ranked
from app.recommend.export import excess_csv
from app.repositories.excess_decisions_postgres_repository import (
    ExcessDecisionPostgresRepository,
)
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.stats.demand import default_as_of

router = APIRouter(prefix="/api/excess", tags=["excess"])

_repo = ExcessDecisionPostgresRepository()


class ExcessRow(BaseModel):
    """One item-site's excess or dead stock row.

    ``dead_reason`` is null for ``excess`` rows, which have no
    ``dead_reason`` at all in the underlying ranked row.
    ``item_description`` is the item's description from the
    ``inventory_items`` read model, looked up by ``item_code``
    (null when the item is not in the read model).
    """

    rank: int
    category: Literal["dead", "excess"]
    dead_reason: Literal["never_moved", "stopped"] | None = None
    item_code: str
    site_code: str
    on_hand_units: float
    unit_cost: float | None = None
    last_demand_on: date | None = None
    demand_window_units: float
    months_of_cover: float | None = None
    justified_units: float
    excess_units: float
    money_tied_up: float | None = None
    as_of: date
    notes: list[str]
    arithmetic: str
    item_description: str | None = None


def _filtered(
    rows: list[dict],
    category: Literal["dead", "excess"] | None,
    dead_reason: Literal["never_moved", "stopped"] | None,
) -> list[dict]:
    """The rows matching the filters, applied in that order."""
    if category is not None:
        rows = [row for row in rows if row["category"] == category]
    if dead_reason is not None:
        rows = [row for row in rows if row.get("dead_reason") == dead_reason]
    return rows


def _ranked(as_of: date | None) -> list[dict]:
    try:
        return excess_ranked(as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class ExcessDecisionRow(ExcessRow):
    """An excess row enriched with the item-site's current decision.

    ``decision`` is the item-site's current decision from
    ``excess_decisions`` (the latest row by ``decided_at``, ``id``
    breaking ties — the same row ``GET /api/excess/decisions`` reports
    as current) with every stored column, or null when the item-site
    has none.  ``decision_lapsed`` is true only for a current
    dismissal that the row has outgrown: the row's ``excess_units``
    exceed the decision's ``excess_units`` by at least the larger of 1
    and 10% of the decision's ``excess_units``.  A snooze never lapses,
    and neither does anything else (accepted decisions, or no decision
    at all).
    """

    decision: ExcessDecision | None = None
    decision_lapsed: bool = False


_LAPSE_MINIMUM_UNITS = 1.0
_LAPSE_FRACTION = 0.10


def _decision_lapsed(row: dict, decision: dict) -> bool:
    """Whether a dismissal has lapsed against the row it decided on.

    A dismissal lapses when the row's ``excess_units`` exceed the
    decision's ``excess_units`` by at least the larger of 1 and 10% of
    the decision's ``excess_units``.  Only dismissals lapse: an
    accepted decision never does, and a snoozed row's visibility
    depends only on its ``snooze_until``, so the lapse threshold never
    applies to it.
    """
    if decision["decision"] != "dismissed":
        return False
    stored_excess_units = float(decision["excess_units"])
    threshold = max(_LAPSE_MINIMUM_UNITS, _LAPSE_FRACTION * stored_excess_units)
    return float(row["excess_units"]) - stored_excess_units >= threshold


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

    Returns ``(row, hidden)`` pairs: the row dict gains ``dead_reason``
    explicit (null for excess rows), ``decision`` (every stored column,
    or null) and ``decision_lapsed``, and ``hidden`` says whether the
    default list leaves the row out.  The current decisions are read
    once for the whole request, never one query per row, and every
    item-site's current decision is read even when it sits past the
    first page of ``GET /api/excess/decisions``.
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
                    "dead_reason": row.get("dead_reason"),
                    "decision": (
                        ExcessDecision.model_validate(decision)
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


@router.get("", response_model=list[ExcessDecisionRow])
def list_excess(
    limit: int = Query(default=20, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    category: Literal["dead", "excess"] | None = Query(default=None),
    dead_reason: Literal["never_moved", "stopped"] | None = Query(default=None),
    as_of: date | None = Query(default=None),
    include_decided: bool = Query(default=False),
) -> list[dict]:
    """List excess and dead stock ranked by money tied up, unknown money last.

    Every row carries its current excess decision (``decision``, or
    null) and ``decision_lapsed``.  While ``include_decided`` is false,
    a row whose current decision is a not-yet-lapsed dismissal, or a
    snooze whose ``snooze_until`` is after today (the server's UTC
    date), is left out of the list; accepted, lapsed, undecided and
    expired-snooze rows stay listed, and with ``include_decided`` true
    every row is listed.  Every row also carries
    ``item_description``, the item's description from the
    ``inventory_items`` read model (null when the item is not in
    it), from one read per request.  Rows are left out after the
    ``category`` and ``dead_reason`` filters and before ``offset``
    and ``limit`` are applied, and every row keeps the ``rank`` that
    ``excess_ranked`` gave it.
    """
    rows = _filtered(_ranked(as_of), category, dead_reason)
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
def export_excess(
    category: Literal["dead", "excess"] | None = Query(default=None),
    dead_reason: Literal["never_moved", "stopped"] | None = Query(default=None),
    as_of: date | None = Query(default=None),
) -> Response:
    """Export every matching excess/dead row as a CSV attachment (no limit)."""
    rows = _filtered(_ranked(as_of), category, dead_reason)
    if rows:
        export_as_of = rows[0]["as_of"]
    elif as_of is not None:
        export_as_of = as_of
    else:
        with get_cursor() as cur:
            export_as_of = default_as_of(cur)
    return Response(
        content=excess_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="excess-{export_as_of.isoformat()}.csv"'
        },
    )
