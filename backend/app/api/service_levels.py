"""Read-only API for the service level curve and suggestion of the ABC classes."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.recommend.service_curve import (
    service_level_curve,
    service_level_suggestion,
)

router = APIRouter(prefix="/api/service-levels", tags=["service-levels"])


class ServiceLevelPoint(BaseModel):
    """One service level's point of a class' curve.

    ``level`` is the service level (the z of the level is
    ``statistics.NormalDist().inv_cdf(level)``), ``safety_stock_units``
    the sum over the class' counted item-sites of z times sigma, each
    rounded up to a whole number, ``safety_stock_value`` the sum of z
    times sigma times ``unit_cost`` rounded to 2 places,
    ``holding_cost`` that value times the holding rate rounded to 2
    places and ``holding_cost_delta`` the difference to the previous
    point's ``holding_cost`` (null for the first point);
    ``expected_fill_rate`` is 1 minus the sum over the taking-part
    item-sites (the counted ones whose ``order_quantity`` is an object
    whose ``order_quantity`` is a finite number above 0, its Q) of
    sigma times G(z) times the yearly demand D (365 times
    ``inputs.d``) divided by Q, divided by the sum of their D, where
    G(z) is the standard normal loss function — the standard normal
    density at z minus z times one minus the standard normal
    distribution at z — clamped to between 0 and 1 and rounded to 4
    places (null when no item-site of the class takes part or their D
    sums to 0); ``current`` is true only at the class' current level.
    """

    level: float
    safety_stock_units: int
    safety_stock_value: float
    holding_cost: float
    holding_cost_delta: float | None = None
    expected_fill_rate: float | None = None
    current: bool


class ServiceLevelClass(BaseModel):
    """One ABC class' entry of the curve.

    ``abc_class`` is ``"A"``, ``"B"`` or ``"C"``, ``item_sites`` the
    class' counted item-sites (0 for a class with none, whose points'
    sums are 0), ``fill_rate_item_sites`` the class' counted
    item-sites that take part in the fill rate (the ones whose
    ``order_quantity`` is an object whose ``order_quantity`` is a
    finite number above 0; 0 when none takes part, whose points'
    ``expected_fill_rate`` is null) and ``current_level`` the class'
    current service level (its stored or default class level with the
    ``replenishment.service_level_by_class`` switch on, the single
    service level with it off).
    """

    abc_class: str
    item_sites: int
    fill_rate_item_sites: int = 0
    current_level: float
    points: list[ServiceLevelPoint]


class ServiceLevelCurveResponse(BaseModel):
    """The safety-stock cost curve of the service levels, per ABC class.

    ``as_of`` is the first counted item-site's as-of (null when no row
    counts), ``by_class`` the value of the
    ``replenishment.service_level_by_class`` switch,
    ``service_target`` the value of the
    ``replenishment.service_target`` setting (``fill_rate`` when the
    stored value is ``fill_rate``, ``cycle`` otherwise — including
    when nothing is stored), ``holding_rate`` the annual holding rate
    the costs are computed at and ``classes`` one entry per class
    ``"A"``, ``"B"``, ``"C"``, in that order.
    """

    as_of: date | None = None
    by_class: bool
    service_target: Literal["cycle", "fill_rate"]
    holding_rate: float
    classes: list[ServiceLevelClass]


@router.get("/curve", response_model=ServiceLevelCurveResponse)
def get_service_level_curve() -> dict:
    """The safety-stock cost curve of the service levels, per ABC class.

    The ``app.recommend.service_curve.service_level_curve`` answer,
    built from one ``recommendations_for_all`` read (no as-of) and one
    stored system settings read: for each ABC class, one point per
    ladder level (0.90, 0.92, 0.95, 0.97, 0.98, 0.99 and 0.995 plus
    the class' current level when that is not already one of them)
    with the safety stock in units and in money, its yearly holding
    cost and the difference to the previous point's, and the expected
    fill rate at the level (null when no item-site of the class takes
    part in it); the class' ``fill_rate_item_sites`` counts the
    taking-part item-sites.
    """
    return service_level_curve()


class ServiceLevelSuggestionLevels(BaseModel):
    """The levels of one combination of service levels: one per class.

    ``A``, ``B`` and ``C`` are the service level of the class ``A``,
    ``B`` and ``C`` item-sites in the combination — each taken from
    that class' ladder as the curve builds it (0.90, 0.92, 0.95,
    0.97, 0.98, 0.99 and 0.995 plus the class' current level when
    that is not already one of them).
    """

    A: float
    B: float
    C: float


class ServiceLevelSuggestionCombination(BaseModel):
    """One combination of per-class service levels.

    ``levels`` holds the level of each of the classes ``A``, ``B``
    and ``C``; ``holding_cost`` is the sum of the three classes'
    curve-point ``holding_cost`` at those levels, rounded to 2
    places; ``expected_fill_rate`` is 1 minus the sum over the
    taking-part rows of all three classes of each row's expected
    shortage at its class' level (the term the curve's
    ``expected_fill_rate`` sums), divided by the sum of those rows'
    yearly demand, clamped to between 0 and 1 and rounded to 4
    places (null when no row takes part).  Both follow the service
    target in force, exactly as the curve does.
    """

    levels: ServiceLevelSuggestionLevels
    expected_fill_rate: float | None = None
    holding_cost: float


class ServiceLevelSuggestionResponse(BaseModel):
    """The suggestion: the cheapest combination of per-class service
    levels whose expected fill rate reaches the target.

    ``as_of``, ``service_target`` and ``holding_rate`` as the curve
    reports them; ``target_fill_rate`` the requested target (a number
    from 0.5 to 0.999); ``current`` the combination of the classes'
    current levels; ``highest`` the combination of each class'
    highest ladder level; ``suggested`` the combination with the
    lowest holding cost among those whose expected fill rate is at
    least the target, a tie going to the lower level for A, then for
    B, then for C, and null when no combination reaches the target or
    no row takes part; and ``holding_cost_delta`` the suggested
    holding cost minus the current one rounded to 2 places (null when
    ``suggested`` is null).  The target comparison uses the unrounded
    aggregate before rounding; the rounded 4-place
    ``expected_fill_rate`` is the response display value only.
    """

    as_of: date | None = None
    service_target: Literal["cycle", "fill_rate"]
    holding_rate: float
    target_fill_rate: float
    current: ServiceLevelSuggestionCombination
    highest: ServiceLevelSuggestionCombination
    suggested: ServiceLevelSuggestionCombination | None = None
    holding_cost_delta: float | None = None


@router.get("/suggestion", response_model=ServiceLevelSuggestionResponse)
def get_service_level_suggestion(
    target_fill_rate: Annotated[
        float,
        Query(
            ge=0.5,
            le=0.999,
            description=(
                "The expected fill rate the suggestion must reach: a number "
                "from 0.5 to 0.999."
            ),
        ),
    ],
) -> dict:
    """The cheapest combination of per-class service levels reaching a
    target expected fill rate.

    Built from one ``recommendations_for_all`` read (no as-of) and
    one stored system settings read — the same reads the curve takes,
    once — it answers ``as_of``, ``service_target`` and
    ``holding_rate`` as the curve reports them, ``current`` (the
    combination of the classes' current levels) and ``highest`` (each
    class' highest ladder level), and ``suggested``: the combination
    with the lowest holding cost among those whose expected fill rate
    is at least ``target_fill_rate``, a tie going to the lower level
    for A, then for B, then for C (null when no combination reaches
    the target or no row takes part), with
    ``holding_cost_delta`` the difference to the current holding
    cost.  A combination is one ladder level for each of the classes
    ``A``, ``B`` and ``C``; its sums are worked out once per class
    and ladder level, so no row is solved again for each of the
    combinations.
    """
    return service_level_suggestion(target_fill_rate)
