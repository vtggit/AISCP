"""What-if simulation of the reorder recommendations, and the stored
scenarios (named lever sets) that back it.

``POST /api/scenarios/simulate`` re-runs the reorder recommendation
under the given levers and writes nothing.  The other routes are CRUD
over the ``scenarios`` table: a scenario is a named, persisted set of
those same levers (a null lever keeps the baseline value), carrying no
result snapshot.  Every route is Bearer-authenticated by the
mount-time gate like the other routers.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.validation import reject_nul_bytes
from app.models.scenarios import (
    ScenarioCreate,
    ScenarioResponse,
    ScenarioUpdate,
)
from app.repositories.scenarios_postgres_repository import (
    ScenarioPostgresRepository,
)
from app.scenario.simulate import _validate_levers, simulate_reorder

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])

_repo = ScenarioPostgresRepository()


def _validate_given_levers(body: BaseModel) -> None:
    """422 when a given lever is out of range, by the engine's rules.

    The same validation the simulate route applies: a
    ``service_level`` must be a number strictly between 0 and 1, and a
    ``demand_multiplier`` or ``lead_time_multiplier`` must be a positive
    finite number.  A null lever is not given and stays the baseline.
    """
    try:
        _validate_levers(
            body.service_level,
            body.demand_multiplier,
            body.lead_time_multiplier,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class SimulateBody(BaseModel):
    """The levers to apply; an omitted lever keeps the baseline value.

    ``service_level`` must be a number strictly between 0 and 1;
    ``demand_multiplier`` and ``lead_time_multiplier`` must be positive
    finite numbers; ``service_level_by_class`` must map exactly the keys
    "A", "B" and "C", each to a number strictly between 0 and 1.

    ``service_level_by_class`` is deliberately typed ``object``: any
    given value then reaches the engine's ``_validate_levers``, so an
    invalid value answers 422 with a string detail naming the lever,
    never Pydantic's list detail.
    """

    service_level: float | None = None
    demand_multiplier: float | None = None
    lead_time_multiplier: float | None = None
    service_level_by_class: object = None


class SimulateBasis(BaseModel):
    """One basis' reorder point and safety stock with their whole-unit values."""

    reorder_point: float
    reorder_point_units: int
    safety_stock: float
    safety_stock_units: int


class SimulateItemSite(BaseModel):
    """One simulated item-site: baseline vs scenario and the signed unit deltas."""

    item_code: str
    site_code: str
    baseline: SimulateBasis
    scenario: SimulateBasis
    reorder_point_units_delta: int
    safety_stock_units_delta: int


class SimulateLevers(BaseModel):
    """The levers applied; null when a lever was not given."""

    service_level: float | None = None
    demand_multiplier: float | None = None
    lead_time_multiplier: float | None = None


class SimulateSummary(BaseModel):
    """The portfolio summary of the simulation.

    ``levers`` echoes the three single levers applied, each null when
    not given; ``service_level_by_class`` echoes the given per-class
    mapping (null when the lever was not given).
    """

    item_sites: int
    reorder_point_units_delta_total: int
    safety_stock_units_delta_total: int
    reorder_point_up: int
    reorder_point_down: int
    reorder_point_unchanged: int
    dollar_impact: float
    pipeline_dollar_impact: float
    item_sites_without_unit_cost: int
    levers: SimulateLevers
    service_level_by_class: dict[str, float] | None = None


class SimulateResponse(BaseModel):
    """The simulation result: the portfolio summary and one row per simulated item-site."""

    summary: SimulateSummary
    item_sites: list[SimulateItemSite]


@router.post("/simulate", response_model=SimulateResponse)
def simulate(body: SimulateBody) -> dict:
    """Re-run the reorder recommendation under the given levers; writes nothing.

    422 when a lever is out of range (a service level that is not a
    number strictly between 0 and 1, a non-positive or non-finite
    multiplier, or a ``service_level_by_class`` that does not map
    exactly the keys "A", "B" and "C" to numbers strictly between 0 and
    1).
    """
    try:
        return simulate_reorder(
            service_level=body.service_level,
            demand_multiplier=body.demand_multiplier,
            lead_time_multiplier=body.lead_time_multiplier,
            service_level_by_class=body.service_level_by_class,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("", response_model=ScenarioResponse, status_code=status.HTTP_201_CREATED)
def create_scenario(body: ScenarioCreate) -> dict:
    """Create one named scenario and return it with its new id.

    Every lever is optional — a null lever keeps the baseline value, as
    in ``POST /api/scenarios/simulate``.  422 when a lever is out of
    range or the name is blank.
    """
    reject_nul_bytes(body.name, detail="scenarios must not contain NUL bytes")
    _validate_given_levers(body)
    return _repo.create(body.model_dump())


@router.get("", response_model=list[ScenarioResponse])
def list_scenarios() -> list[dict]:
    """List every scenario, newest ``created_at`` first."""
    return _repo.list_all()


@router.get(
    "/{scenario_id}",
    response_model=ScenarioResponse,
    responses={404: {"description": "The scenario does not exist."}},
)
def get_scenario(scenario_id: str) -> dict:
    """Fetch one scenario by id; 404 for an unknown id."""
    reject_nul_bytes(scenario_id, detail="scenarios must not contain NUL bytes")
    row = _repo.get_by_id(scenario_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="scenario not found"
        )
    return row


@router.put(
    "/{scenario_id}",
    response_model=ScenarioResponse,
    responses={404: {"description": "The scenario does not exist."}},
)
def update_scenario(scenario_id: str, body: ScenarioUpdate) -> dict:
    """Partially update one scenario; 404 for an unknown id.

    A documented partial update: an omitted field is left unchanged, an
    explicit null for a lever clears it to the baseline value, and any
    change bumps ``updated_at``.  422 when a given lever is out of
    range or the given name is blank or null.
    """
    reject_nul_bytes(
        scenario_id, body.name, detail="scenarios must not contain NUL bytes"
    )
    _validate_given_levers(body)
    row = _repo.update(scenario_id, body.model_dump(exclude_unset=True))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="scenario not found"
        )
    return row


@router.delete(
    "/{scenario_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "The scenario does not exist."}},
)
def delete_scenario(scenario_id: str) -> None:
    """Delete one scenario by id; 404 for an unknown id."""
    reject_nul_bytes(scenario_id, detail="scenarios must not contain NUL bytes")
    if not _repo.delete(scenario_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="scenario not found"
        )
