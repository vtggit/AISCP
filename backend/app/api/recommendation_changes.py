"""Read-only API for reorder-point changes between two dates."""

# parity: backend-only reorder-point changes for machine consumers and the CSV export

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from app.db.connection import get_cursor
from app.recommend.changes import recommendation_changes
from app.recommend.export import changes_csv
from app.stats.demand import default_as_of

router = APIRouter(
    prefix="/api/recommendation-changes", tags=["recommendation-changes"]
)

PrimaryDriver = Literal[
    "demand_level",
    "demand_variability",
    "lead_time",
    "lead_time_variability",
]


class ChangeInputs(BaseModel):
    """The demand and lead-time inputs behind one recommendation."""

    d: float
    sd_d: float
    lt: float
    sd_lt: float


class ChangeContributions(BaseModel):
    """Shapley contribution of each input to the reorder-point change."""

    demand_level: float
    demand_variability: float
    lead_time: float
    lead_time_variability: float


class RecommendationChangeRow(BaseModel):
    """One item-site's reorder-point change between since and as_of.

    ``contributions`` (with ``primary_driver``) and the ``inputs_then`` /
    ``inputs_now`` input sets are null when either side is not an "ok"
    recommendation; the reorder point, unit ceiling and ``change_units``
    are null on the side that is not "ok", and ``money`` is null without
    a positive unit cost and ``change_units``.
    """

    rank: int
    item_code: str
    site_code: str
    as_of: date
    since: date
    status_then: str
    status_now: str
    reorder_point_then: float | None = None
    reorder_point_now: float | None = None
    reorder_point_units_then: int | None = None
    reorder_point_units_now: int | None = None
    change_units: int | None = None
    inputs_then: ChangeInputs | None = None
    inputs_now: ChangeInputs | None = None
    contributions: ChangeContributions | None = None
    primary_driver: PrimaryDriver | None = None
    master_lead_time_then: bool
    master_lead_time_now: bool
    notes: list[str]
    unit_cost: float | None = None
    money: float | None = None


def _ranked(as_of: date | None, since: date | None) -> list[dict]:
    """The change rows for the dates; 400 with the message on a bad ``since``."""
    try:
        return recommendation_changes(as_of=as_of, since=since)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _filtered(rows: list[dict], primary_driver: PrimaryDriver | None) -> list[dict]:
    """The rows whose primary driver matches the filter."""
    if primary_driver is not None:
        rows = [row for row in rows if row["primary_driver"] == primary_driver]
    return rows


def _with_rank(rows: list[dict]) -> list[dict]:
    """Each row with its 1-based rank in the given order."""
    return [{**row, "rank": rank} for rank, row in enumerate(rows, start=1)]


@router.get("", response_model=list[RecommendationChangeRow])
def list_recommendation_changes(
    limit: int = Query(default=20, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    as_of: date | None = Query(default=None),
    since: date | None = Query(default=None),
    primary_driver: PrimaryDriver | None = Query(default=None),
) -> list[dict]:
    """List reorder-point changes between since and as_of, ranked by money at stake."""
    rows = _with_rank(_filtered(_ranked(as_of, since), primary_driver))
    return rows[offset : offset + limit]


@router.get("/export.csv")
def export_recommendation_changes(
    as_of: date | None = Query(default=None),
    since: date | None = Query(default=None),
    primary_driver: PrimaryDriver | None = Query(default=None),
) -> Response:
    """Export every matching change row as a CSV attachment (no limit)."""
    rows = _with_rank(_filtered(_ranked(as_of, since), primary_driver))
    if rows:
        export_as_of = rows[0]["as_of"]
    elif as_of is not None:
        export_as_of = as_of
    else:
        with get_cursor() as cur:
            export_as_of = default_as_of(cur)
    return Response(
        content=changes_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="recommendation-changes-{export_as_of.isoformat()}.csv"'
            )
        },
    )
