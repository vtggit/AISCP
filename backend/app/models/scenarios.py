"""Pydantic models for scenarios — stored what-if lever sets.

A scenario is a named set of simulation levers of the kind
``POST /api/scenarios/simulate`` accepts: a target ``service_level``
(a probability strictly between 0 and 1) and positive finite
``demand_multiplier`` / ``lead_time_multiplier`` numbers.  A null lever
means "keep the baseline value", as in the simulate endpoint.  A
scenario stores only its levers, never a result snapshot.

The lever *ranges* are not enforced here: a given lever is validated by
the engine's ``app.scenario.simulate._validate_levers`` and the API
answers 422 on an out-of-range lever.  These models only shape the
payload: ``name`` is a non-blank string of at most 200 characters.
"""

from pydantic import BaseModel, Field, field_validator


def _check_not_blank(value: str | None) -> str | None:
    """Reject a null, empty or whitespace-only name (a 422 on the API).

    An explicit ``null`` is rejected too: a stored name is never null.
    An *omitted* field never reaches this check — Pydantic does not
    validate defaults — so ``ScenarioUpdate`` leaves an omitted
    ``name`` unchanged.
    """
    if value is None:
        raise ValueError("name must not be null")
    if value.strip() == "":
        raise ValueError("name must not be blank")
    return value


class ScenarioCreate(BaseModel):
    """Payload for creating a scenario.

    ``name`` is a non-blank string of at most 200 characters; every
    lever is optional — a null (or omitted) lever keeps the baseline
    value, as in ``POST /api/scenarios/simulate``.
    """

    name: str = Field(min_length=1, max_length=200)
    service_level: float | None = None
    demand_multiplier: float | None = None
    lead_time_multiplier: float | None = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        return _check_not_blank(value)


class ScenarioUpdate(BaseModel):
    """Payload for partially updating a scenario; every field optional.

    An omitted field is left unchanged; an explicit null for a lever
    clears it to the baseline value (the column stores null).  A
    ``name`` that is given must be a non-blank string, as on create —
    an explicit null is rejected (a 422), because a stored name is
    never null.
    """

    name: str | None = Field(default=None, max_length=200)
    service_level: float | None = None
    demand_multiplier: float | None = None
    lead_time_multiplier: float | None = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str | None) -> str | None:
        return _check_not_blank(value)


class ScenarioResponse(BaseModel):
    """Response model for a stored scenario."""

    id: str
    name: str
    service_level: float | None = None
    demand_multiplier: float | None = None
    lead_time_multiplier: float | None = None
    created_at: str
    updated_at: str
