"""Append-only decisions on dead and excess stock rows.

``POST /api/excess/decisions`` records what a human did about one
item-site's current dead-and-excess row (the same rows
``GET /api/excess`` lists, with no filters) and
``GET /api/excess/decisions`` lists the stored decisions.
Decisions are append-only: every POST stores a new row, earlier
decisions are never changed or deleted, and duplicates are accepted as
separate append-only entries.
"""

from datetime import date, datetime, timedelta
from typing import Literal, Self
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.auth.authorization import require_role
from app.auth.display_name import display_name
from app.auth.models import AuthUser
from app.recommend.excess import excess_ranked
from app.repositories.excess_decisions_postgres_repository import (
    ExcessDecisionPostgresRepository,
)

router = APIRouter(prefix="/api/excess", tags=["excess-decisions"])

_repo = ExcessDecisionPostgresRepository()

_NUL = chr(0)
_MAX_REASON_LENGTH = 1000
_MAX_SNOOZE_DAYS = 90
_MAX_DECIDED_BY_LENGTH = 255


class ExcessDecision(BaseModel):
    """One stored excess decision on an item-site's ranked row."""

    id: str
    item_code: str
    site_code: str
    decision: str
    action: str | None = None
    reason: str | None = None
    snooze_until: date | None = None
    as_of: date
    category: str
    dead_reason: str | None = None
    on_hand_units: float
    excess_units: float
    money_tied_up: float | None = None
    decided_by: str
    decided_by_name: str | None = None
    decided_at: datetime


class ExcessDecisionBody(BaseModel):
    """The body of POST /api/excess/decisions.

    Every business rule is a model validation, so a rejected body
    answers 422 in FastAPI's HTTPValidationError envelope, exactly like
    a malformed one.
    """

    item_code: str = Field(..., max_length=64)
    site_code: str = Field(..., max_length=64)
    decision: Literal["accepted", "dismissed", "snoozed"]
    action: Literal["stop_replenishing", "reduce", "dispose"] | None = None
    reason: str | None = Field(default=None, max_length=_MAX_REASON_LENGTH)
    snooze_until: date | None = None

    @model_validator(mode="after")
    def _check_business_rules(self) -> Self:
        for value in (self.item_code, self.site_code, self.reason):
            if value is not None and _NUL in value:
                raise ValueError("must not contain NUL bytes")
        if self.decision == "accepted" and self.action is None:
            raise ValueError("an accepted decision requires an action")
        if self.decision != "accepted" and self.action is not None:
            raise ValueError("action is only valid for an accepted decision")
        if self.decision == "dismissed" and (
            self.reason is None or not self.reason.strip()
        ):
            raise ValueError("a dismissed decision requires a non-blank reason")
        if self.decision == "snoozed" and self.snooze_until is None:
            raise ValueError("a snoozed decision requires snooze_until")
        if self.decision != "snoozed" and self.snooze_until is not None:
            raise ValueError("snooze_until is only valid for a snoozed decision")
        if self.snooze_until is not None:
            today = date.today()
            if self.snooze_until <= today:
                raise ValueError("snooze_until must be after today")
            if self.snooze_until > today + timedelta(days=_MAX_SNOOZE_DAYS):
                raise ValueError(
                    f"snooze_until must be at most {_MAX_SNOOZE_DAYS} days after today"
                )
        return self


class _FilterPair(BaseModel):
    """Both codes together or neither; one without the other is a
    validation error (raised in FastAPI's HTTPValidationError envelope
    by the dependency below)."""

    item_code: str | None = None
    site_code: str | None = None

    @model_validator(mode="after")
    def _check_pair(self) -> Self:
        if (self.item_code is None) != (self.site_code is None):
            raise ValueError("item_code and site_code must be given together")
        return self


def decision_filters(
    item_code: str | None = Query(default=None, max_length=64),
    site_code: str | None = Query(default=None, max_length=64),
) -> tuple[str | None, str | None]:
    """The query filters of GET /api/excess/decisions.

    Both codes together select one item-site's history; neither selects
    the current decision of every item-site that has one.  Giving only
    one of the two answers 422 in FastAPI's HTTPValidationError
    envelope.
    """
    try:
        _FilterPair(item_code=item_code, site_code=site_code)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    return item_code, site_code


class _DecidedBy(BaseModel):
    value: str = Field(max_length=_MAX_DECIDED_BY_LENGTH)

    @model_validator(mode="after")
    def _no_nul(self) -> Self:
        if _NUL in self.value:
            raise ValueError("must not contain NUL bytes")
        return self


def _require_storable_decided_by(user: AuthUser) -> None:
    """The token subject is stored in decided_by (String(255)); a
    hostile token whose subject would not fit answers 422 instead of a
    database error."""
    try:
        _DecidedBy(value=user.sub)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


@router.post(
    "/decisions",
    status_code=201,
    response_model=ExcessDecision,
    responses={404: {"description": "No current excess row exists for the item-site."}},
)
def create_excess_decision(
    body: ExcessDecisionBody,
    user: AuthUser = Depends(require_role("admin")),
) -> dict:
    """Record an excess decision for one item-site's current ranked row.

    The decision snapshots the item-site's current dead-and-excess
    row — the row ``GET /api/excess`` lists with no filters: its as_of,
    its category and dead_reason, its on_hand_units, excess_units and
    money_tied_up, with decided_by set to the token subject.  An
    accepted decision carries an action (stop_replenishing, reduce or
    dispose); a dismissed one a non-blank reason; a snoozed one a
    snooze_until after today and at most 90 days out.  Decisions are
    append-only: a new row is stored on every accepted POST, earlier
    decisions are never changed or deleted, and duplicate POSTs are
    accepted as separate append-only duplicates.
    """
    _require_storable_decided_by(user)
    try:
        rows = excess_ranked()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    row = next(
        (
            candidate
            for candidate in rows
            if candidate["item_code"] == body.item_code
            and candidate["site_code"] == body.site_code
        ),
        None,
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail="no current excess row for item site"
        )
    return _repo.insert(
        id=str(uuid4()),
        item_code=row["item_code"],
        site_code=row["site_code"],
        decision=body.decision,
        action=body.action,
        reason=body.reason,
        snooze_until=body.snooze_until,
        as_of=row["as_of"],
        category=row["category"],
        dead_reason=row.get("dead_reason"),
        on_hand_units=row["on_hand_units"],
        excess_units=row["excess_units"],
        money_tied_up=row["money_tied_up"],
        decided_by=user.sub,
        decided_by_name=display_name(user),
    )


@router.get("/decisions", response_model=list[ExcessDecision])
def list_excess_decisions(
    filters: tuple[str | None, str | None] = Depends(decision_filters),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    """List stored excess decisions, newest first.

    Open to any authenticated user.  With both item_code and site_code,
    the full decision history of that item-site; without either, the
    current decision (the latest by decided_at, id breaking ties) of
    every item-site that has one.  Results are ordered by decided_at
    DESC, id DESC.
    """
    item_code, site_code = filters
    if item_code is not None:
        return _repo.history(item_code, site_code, limit, offset)
    return _repo.current(limit, offset)
