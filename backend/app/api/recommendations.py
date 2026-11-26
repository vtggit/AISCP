"""Read-only API for reorder point recommendations per item and site."""

import math
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse

from app.api.validation import reject_nul_bytes
from app.db.connection import get_cursor
from app.recommend.export import planning_parameters_csv
from app.recommend.reorder import (
    INTERMITTENT_RECOMMENDATIONS_SETTING,
    recommendation_for_key,
    recommendations_for_all,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.settings_postgres_repository import (
    SettingsPostgresRepository,
)
from app.settings.registry import get_setting
from app.stats.demand import default_as_of

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])
_repo = ItemSitePostgresRepository()
_settings_repo = SettingsPostgresRepository()


class CurrentSettings(BaseModel):
    """The item-site's current planning settings from item_sites."""

    reorder_point: float | None = None
    safety_stock: float | None = None


class RecommendationInputs(BaseModel):
    """The inputs substituted into the recommendation formulas."""

    d: float
    sd_d: float
    lt: float
    sd_lt: float
    z: float
    service_level: float


class OtherBasis(BaseModel):
    """The reorder point the other demand basis would give.

    ``basis`` is ``"history"`` or ``"forecast"`` — the basis that is
    not the active one; the rest are that basis' exact results and
    inputs.
    """

    basis: str
    safety_stock: float
    reorder_point: float
    safety_stock_units: int
    reorder_point_units: int
    inputs: RecommendationInputs


class OrderQuantity(BaseModel):
    """The recommended order quantity of a recommendable item-site.

    ``basis`` is ``"eoq"`` (the economic order quantity from the
    item-site's ``unit_cost``, per-order cost and annual holding rate)
    or ``"assumed_30_days"`` (30 days of demand when the unit cost is
    absent or not positive); ``clamped`` is ``"min_cover"`` or
    ``"max_cover"`` when the quantity was clamped to 7 or 365 days of
    demand, else null. ``order_quantity`` is the exact float after
    clamping, ``order_quantity_units`` its whole-unit ceiling (at
    least 1) and ``cover_days`` the days of demand those units cover.
    ``recommended_max_units`` is the recommended reorder point in
    whole units plus ``order_quantity_units``; ``current_order_qty``
    and ``current_max_qty`` are the item-site's current ``order_qty``
    and ``max_qty`` from item_sites (null when absent).
    """

    basis: str
    clamped: str | None = None
    order_quantity: float
    order_quantity_units: int
    cover_days: float
    annual_demand: float
    order_cost: float
    holding_rate: float
    unit_cost: float | None = None
    arithmetic: str
    recommended_max_units: int
    current_order_qty: float | None = None
    current_max_qty: float | None = None


class ServiceLevelSource(BaseModel):
    """Where the recommendation's service level came from.

    ``abc_class`` is the item-site's ABC class (``"A"``, ``"B"`` or
    ``"C"``) when the ``replenishment.service_level_by_class`` switch
    is on and the item-site is classified, else null; ``source`` is
    ``"class"`` when the class' stored or default level was used,
    ``"single"`` when the single service level was (the switch off, or
    an unclassified item-site).
    """

    abc_class: str | None = None
    source: str = "single"


class EmpiricalReorderPoint(BaseModel):
    """The empirical lead-time demand reorder point of one item-site.

    The eleven keys of the
    ``app.recommend.empirical.empirical_reorder_point`` result: the
    service-level quantile of the overlapping lead-time-day demand
    spans of the pair's daily totals, from the demand row's
    ``daily_window_start`` to the as-of. ``window_days`` is the lead
    time in whole days, ``spans`` the number of overlapping spans and
    ``spans_with_demand`` how many of them are positive;
    ``reorder_point`` its whole-unit ``reorder_point_units``,
    ``mean_lead_time_demand`` the mean span total and
    ``safety_stock`` (with its whole-unit ``safety_stock_units``)
    ``max(0, reorder_point - mean_lead_time_demand)``.
    """

    method: str
    window_days: int
    spans: int
    spans_with_demand: int
    service_level: float
    reorder_point: float
    reorder_point_units: int
    mean_lead_time_demand: float
    safety_stock: float
    safety_stock_units: int
    arithmetic: str


class RecommendationResponse(BaseModel):
    """One item-site's reorder point recommendation.

    ``demand_basis`` names the basis (the ``replenishment.demand_basis``
    value) the other fields were built on; ``other_basis`` holds the
    other basis' reorder point and is null when the recommendation's
    status is not ``ok`` or the other basis does not exist.
    ``service_level_source`` names where the service level came from
    (the item-site's ABC class when the
    ``replenishment.service_level_by_class`` switch is on and the
    item-site is classified, else the single service level).
    ``on_hand_units`` is the item-site's ``on_hand`` quantity (0 when
    it has no on_hand row), ``on_order_units`` the sum of the item-site's
    open purchase lines' ``open_units`` (0 when none), ``unit_cost`` the
    ``unit_cost`` carried from the item_sites row (null when absent),
    ``source_system`` the ``source_system`` stored on the item_sites
    row (null when absent) and ``suggested_order_quantity``
    ``max(0, reorder_point_units - (on_hand_units + on_order_units))``
    for a recommendable item-site, null when the recommendation has no
    ``reorder_point_units`` (a stopped, excess-only or otherwise
    non-recommendable item-site). ``order_quantity`` is the
    item-site's recommended order quantity (an
    :class:`OrderQuantity`) for a recommendable item-site, null
    otherwise.  ``empirical_adopted`` is true when the item-site has
    a stored empirical adoption (one row of ``empirical_adoptions``
    for the pair) and false otherwise — an adopted item-site is
    recommended on its ``empirical`` reorder point with the
    ``replenishment.intermittent_recommendations`` switch off as on.

    ``signal_units`` is the item-site's counted demand signals' units
    over the lead-time window (0 when none, and 0 on a not-ok row)
    and ``reorder_point_before_signals_units`` the row's
    ``reorder_point_units`` before those units were added to
    ``reorder_point`` (null on a not-ok row).

    ``on_hand_units``, ``on_order_units`` and ``suggested_order_quantity``
    are strict whole units: a true ``int`` or null only. An integer-
    valued float or a ``Decimal`` is not a unit count and is rejected
    rather than coerced.
    """

    item_code: str
    site_code: str
    as_of: date
    status: str
    demand_class: str
    method: str
    notes: list[str]
    current: CurrentSettings
    delta_reorder_point: float | None = None
    safety_stock: float | None = None
    reorder_point: float | None = None
    safety_stock_units: int | None = None
    reorder_point_units: int | None = None
    signal_units: float = 0.0
    reorder_point_before_signals_units: int | None = None
    inputs: RecommendationInputs | None = None
    arithmetic: str | None = None
    demand_basis: str | None = None
    other_basis: OtherBasis | None = None
    on_hand_units: int | None = None
    on_order_units: int | None = None
    unit_cost: float | None = None
    source_system: str | None = None
    suggested_order_quantity: int | None = None
    order_quantity: OrderQuantity | None = None
    empirical: EmpiricalReorderPoint | None = None
    empirical_adopted: bool = False
    service_level_source: ServiceLevelSource = Field(
        default_factory=ServiceLevelSource,
    )

    @field_validator(
        "on_hand_units",
        "on_order_units",
        "reorder_point_before_signals_units",
        "suggested_order_quantity",
        mode="before",
    )
    @classmethod
    def _whole_units(cls, value: object) -> int | None:
        """Whole units only: a real ``int`` or null.

        A float (even one with an integer value) or a ``Decimal`` is
        not a unit count — the service layer computes ints, so anything
        else is a bug upstream and must not be silently coerced.
        """
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("must be a whole number of units (int) or null")
        return value


class IntermittentImpactRow(BaseModel):
    """One intermittent or lumpy item-site's empirical reorder point
    impact, as served by ``GET /api/recommendations/intermittent-impact``.

    ``item_code`` and ``site_code`` name the item-site,
    ``demand_class`` the result's ``"intermittent"`` or ``"lumpy"``
    and ``unit_cost`` the unit cost carried from item_sites (null
    when absent). ``current_reorder_point`` the result's
    ``current.reorder_point`` (null when absent),
    ``empirical_reorder_point_units`` the
    ``empirical.reorder_point_units``,
    ``reorder_point_units_delta`` the empirical units minus the
    current reorder point (a null current counts as 0),
    ``dollar_delta`` that delta times the unit cost, null when the
    unit cost is absent or not positive, and ``adopted`` whether the
    item-site has a stored empirical adoption (the result's
    ``empirical_adopted``) — carried by the endpoint only when the
    ``recommendations_for_all`` call read the stored adoptions and
    absent from the row otherwise: a value the call did not read is
    not masked as false.
    """

    item_code: str
    site_code: str
    demand_class: str
    unit_cost: float | None = None
    current_reorder_point: float | None = None
    empirical_reorder_point_units: int
    reorder_point_units_delta: float
    dollar_delta: float | None = None
    adopted: bool = False


class IntermittentImpactSummary(BaseModel):
    """The summary of the empirical reorder point impact.

    ``item_sites`` the number of rows, ``current_value`` and
    ``empirical_value`` the sums of current reorder point times unit
    cost and of empirical units times unit cost over the rows with a
    positive unit cost (each rounded to cents), ``dollar_delta``
    ``empirical_value`` minus ``current_value``,
    ``item_sites_without_unit_cost`` the rows whose unit cost is
    absent, zero or negative and ``switch_on`` the stored or default
    value of the ``replenishment.intermittent_recommendations``
    switch.
    """

    item_sites: int
    current_value: float
    empirical_value: float
    dollar_delta: float
    item_sites_without_unit_cost: int
    switch_on: bool


class IntermittentImpactResponse(BaseModel):
    """The response of ``GET /api/recommendations/intermittent-impact``:
    the summary plus one row per intermittent or lumpy item-site whose
    ``empirical`` reorder point is not null, ordered by ``item_code``
    then ``site_code``.  The rows describe what the
    ``replenishment.intermittent_recommendations`` switch does, not
    whether it is on: the same rows are served with the switch on or
    off.
    """

    summary: IntermittentImpactSummary
    item_sites: list[IntermittentImpactRow]


@router.get("", response_model=list[RecommendationResponse])
def list_recommendations(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    item_code: str | None = Query(default=None, max_length=64),
    site_code: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=64),
    source_system: str | None = Query(default=None, max_length=64),
    as_of: date | None = Query(default=None),
) -> list[dict]:
    """List reorder recommendations per (item_code, site_code), ordered by item_code then site_code."""
    reject_nul_bytes(
        item_code,
        site_code,
        status,
        source_system,
        detail="recommendation filters must not contain NUL bytes",
    )
    try:
        rows = recommendations_for_all(as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if item_code is not None:
        rows = [row for row in rows if row["item_code"] == item_code]
    if site_code is not None:
        rows = [row for row in rows if row["site_code"] == site_code]
    if status is not None:
        rows = [row for row in rows if row["status"] == status]
    if source_system is not None:
        rows = [row for row in rows if row["source_system"] == source_system]
    return rows[offset : offset + limit]


@router.get("/export.csv")
def export_planning_parameters(
    as_of: date | None = Query(default=None),
) -> Response:
    """Export every recommendation row as a CSV attachment (no limit)."""
    if as_of is None:
        with get_cursor() as cur:
            as_of = default_as_of(cur)
    try:
        rows = recommendations_for_all(as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(
        content=planning_parameters_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="planning-parameters-{as_of.isoformat()}.csv"'
            )
        },
    )


@router.get("/by-key", response_model=RecommendationResponse)
def get_recommendation_by_key(
    item_code: str = Query(..., max_length=64),
    site_code: str = Query(..., max_length=64),
    as_of: date | None = Query(default=None),
) -> dict:
    """Fetch one item-site's recommendation; 404 when the item-site is not in item_sites."""
    reject_nul_bytes(
        item_code, site_code, detail="recommendation filters must not contain NUL bytes"
    )
    row = _repo.get_by_key(item_code, site_code)
    if row is None:
        raise HTTPException(status_code=404, detail="item site not found")
    try:
        return recommendation_for_key(item_code, site_code, as_of, item_site_row=row)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _intermittent_switch_on() -> bool:
    """The stored or default value of the
    ``replenishment.intermittent_recommendations`` switch: the stored
    system value when one is stored and is a boolean, else the
    registry default — the value ``recommendations_for_all`` applies
    (a value written out of band that the setting cannot hold never
    turns the switch on).
    """
    setting = get_setting(INTERMITTENT_RECOMMENDATIONS_SETTING)
    if setting is None:
        return False
    stored = _settings_repo.get(setting.scope, "", setting.key)
    if stored is not None and isinstance(stored["value"], bool):
        return stored["value"]
    return bool(setting.default)


def _exact_decimal(value: object) -> Decimal | None:
    """The value as an exact finite ``Decimal``, else None.

    A ``Decimal`` passes through untouched (the shape the database
    numeric column returns); ``int`` and finite ``float`` convert via
    their shortest decimal representation — the value the caller
    meant, not the binary-float artifact.  bools, NaN, infinities and
    non-numeric values never raise — they simply are not a number.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float) and math.isfinite(value):
        number = Decimal(str(value))
    else:
        return None
    return number if number.is_finite() else None


def _dollar_product(units: object, cost: Decimal) -> Decimal | None:
    """``units`` times ``cost`` in exact decimal arithmetic, else None
    when the units are not a number."""
    number = _exact_decimal(units)
    if number is None:
        return None
    return number * cost


@router.get("/intermittent-impact", response_model=IntermittentImpactResponse)
def intermittent_impact(
    as_of: date | None = Query(default=None),
) -> dict | JSONResponse:
    """The dollar impact of the empirical reorder points, per item-site.

    One row per result of ``recommendations_for_all(as_of)`` whose
    ``demand_class`` is ``"intermittent"`` or ``"lumpy"`` and whose
    ``empirical`` is not null, ordered by ``item_code`` then
    ``site_code``.  The rows describe what the
    ``replenishment.intermittent_recommendations`` switch does, not
    whether it is on: the same rows are served with the switch on or
    off, and the summary's ``switch_on`` reports the switch' stored or
    default value.  Read-only: nothing is written.  Served through a
    ``JSONResponse`` (not the ``response_model``) so that a row
    carries ``adopted`` only when the result carries
    ``empirical_adopted`` — a value the call did not read is not
    masked as false.
    """
    try:
        rows = recommendations_for_all(as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    item_sites = []
    current_value = Decimal(0)
    empirical_value = Decimal(0)
    without_unit_cost = 0
    candidates = [
        row
        for row in rows
        if row.get("demand_class") in ("intermittent", "lumpy")
        and row.get("empirical") is not None
    ]
    for row in sorted(
        candidates,
        key=lambda candidate: (candidate["item_code"], candidate["site_code"]),
    ):
        unit_cost = row.get("unit_cost")
        current = row.get("current") or {}
        current_reorder_point = current.get("reorder_point")
        baseline_units = 0 if current_reorder_point is None else current_reorder_point
        empirical_units = row["empirical"]["reorder_point_units"]
        delta = empirical_units - baseline_units
        cost = _exact_decimal(unit_cost)
        if cost is not None and cost > 0:
            product = _dollar_product(delta, cost)
            dollar_delta = None
            if product is not None:
                try:
                    dollar_delta = float(product)
                except OverflowError:
                    dollar_delta = None
            current_value += _dollar_product(baseline_units, cost)
            empirical_value += _dollar_product(empirical_units, cost)
        else:
            dollar_delta = None
            without_unit_cost += 1
        item_site_row = {
            "item_code": row["item_code"],
            "site_code": row["site_code"],
            "demand_class": row["demand_class"],
            "unit_cost": unit_cost,
            "current_reorder_point": current_reorder_point,
            "empirical_reorder_point_units": empirical_units,
            "reorder_point_units_delta": delta,
            "dollar_delta": dollar_delta,
        }
        # The row carries adopted only when the result carries
        # empirical_adopted — a value the call did not read is not
        # masked as false.
        adopted = row.get("empirical_adopted")
        if adopted is not None:
            item_site_row["adopted"] = adopted
        item_sites.append(item_site_row)
    return JSONResponse(
        jsonable_encoder(
            {
                "summary": {
                    "item_sites": len(item_sites),
                    "current_value": float(round(current_value, 2)),
                    "empirical_value": float(round(empirical_value, 2)),
                    "dollar_delta": float(
                        round(empirical_value, 2) - round(current_value, 2)
                    ),
                    "item_sites_without_unit_cost": without_unit_cost,
                    "switch_on": _intermittent_switch_on(),
                },
                "item_sites": item_sites,
            }
        )
    )
