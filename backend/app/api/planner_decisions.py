"""Append-only planner decisions on reorder point drift rows.

``POST /api/drift/decisions`` records what a planner did about one
item-site's current drift row (the same rows ``GET /api/drift/by-key``
reads), ``GET /api/drift/decisions`` lists the stored decisions, and
``GET /api/drift/decisions/summary`` summarises every item-site's
current decision as accepted / applied / outstanding / dismissed /
snoozed buckets.
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

from app.api.validation import require_storable_subject
from app.auth.authorization import require_role
from app.auth.dependencies import require_authenticated_user
from app.auth.display_name import display_name
from app.auth.models import AuthUser
from app.recommend.drift import drift_ranked
from app.repositories.planner_decisions_postgres_repository import (
    PlannerDecisionPostgresRepository,
)

router = APIRouter(prefix="/api/drift", tags=["planner-decisions"])

_repo = PlannerDecisionPostgresRepository()

_NUL = chr(0)
_MAX_REASON_LENGTH = 1000
_MAX_SNOOZE_DAYS = 90


class PlannerDecision(BaseModel):
    """One stored planner decision on an item-site's drift row."""

    id: str
    item_code: str
    site_code: str
    decision: str
    reason: str | None = None
    snooze_until: date | None = None
    as_of: date
    recommended_reorder_point_units: int
    current_reorder_point: float | None = None
    money_at_stake: float | None = None
    decided_by: str
    decided_by_name: str | None = None
    decided_at: datetime


class DecisionBody(BaseModel):
    """The body of POST /api/drift/decisions.

    Every business rule is a model validation, so a rejected body
    answers 422 in FastAPI's HTTPValidationError envelope, exactly like
    a malformed one.
    """

    item_code: str = Field(..., max_length=64)
    site_code: str = Field(..., max_length=64)
    decision: Literal["accepted", "dismissed", "snoozed"]
    reason: str | None = Field(default=None, max_length=_MAX_REASON_LENGTH)
    snooze_until: date | None = None

    @model_validator(mode="after")
    def _check_business_rules(self) -> Self:
        for value in (self.item_code, self.site_code, self.reason):
            if value is not None and _NUL in value:
                raise ValueError("must not contain NUL bytes")
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
    """The query filters of GET /api/drift/decisions.

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


def require_any_authenticated_user(
    current_user: AuthUser = Depends(require_authenticated_user),
) -> AuthUser:
    """The summary route's own authentication dependency.

    ``GET /api/drift/decisions/summary`` is open to any
    authenticated user: the dependency answers 401 without a valid
    Bearer token even when the router is mounted without the
    app's include-time gate, and passes the authenticated user
    through unchanged.
    """
    return current_user


@router.post(
    "/decisions",
    status_code=201,
    response_model=PlannerDecision,
    responses={404: {"description": "No current drift row exists for the item-site."}},
)
def create_planner_decision(
    body: DecisionBody,
    user: AuthUser = Depends(require_role("admin")),
) -> dict:
    """Record a planner decision for one item-site's current drift row.

    The decision snapshots the current ranked drift row: its as_of, its
    reorder_point_units (stored as recommended_reorder_point_units), its
    current_reorder_point and its money_at_stake, with decided_by set to
    the token subject.  Decisions are append-only: a new row is stored
    on every accepted POST, earlier decisions are never changed or
    deleted, and duplicate POSTs are accepted as separate append-only duplicates without requiring an Idempotency-Key header.
    """
    require_storable_subject(user)
    try:
        rows = drift_ranked()
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
            status_code=404, detail="no current drift row for item site"
        )
    return _repo.insert(
        id=str(uuid4()),
        item_code=row["item_code"],
        site_code=row["site_code"],
        decision=body.decision,
        reason=body.reason,
        snooze_until=body.snooze_until,
        as_of=row["as_of"],
        recommended_reorder_point_units=row["reorder_point_units"],
        current_reorder_point=row["current_reorder_point"],
        money_at_stake=row["money_at_stake"],
        decided_by=user.sub,
        decided_by_name=display_name(user),
    )


class _MoneyBucket(BaseModel):
    """One money-bearing summary bucket.

    ``count`` is the number of current decisions in the bucket,
    ``money_at_stake`` sums their snapshotted ``money_at_stake``
    (decisions whose snapshot is null contribute nothing) and
    ``without_money`` counts those null-snapshot decisions.
    """

    count: int
    money_at_stake: float
    without_money: int


class _CountBucket(BaseModel):
    """A summary bucket that only carries a count."""

    count: int


class DecisionSummary(BaseModel):
    """The response of GET /api/drift/decisions/summary."""

    accepted: _MoneyBucket
    applied: _MoneyBucket
    outstanding: _MoneyBucket
    dismissed: _CountBucket
    snoozed: _CountBucket


class _DirectionBucket(BaseModel):
    """One raise/lower direction bucket.

    ``accepted`` counts the current accepted decisions in the
    direction and ``dismissed`` the current dismissed ones.  A
    decision whose stored ``current_reorder_point`` equals its
    ``recommended_reorder_point_units`` counts in neither direction,
    and a snoozed decision counts in none.
    """

    accepted: int
    dismissed: int


class DecisionAcceptance(BaseModel):
    """The response of GET /api/drift/decisions/acceptance.

    ``accepted`` and ``dismissed`` are the current decisions with that
    decision (count, summed stored ``money_at_stake``, and the count
    of the null snapshots), ``snoozed`` is ``{count}``, ``rate`` and
    ``money_rate`` are the accepted share of the counts and of the
    money (each rounded to 4 decimals, null when its denominator is
    0), and ``raise`` (the stored ``current_reorder_point`` is null or
    below the recommended value) and ``lower`` (the stored value above
    it) each carry the accepted and dismissed counts of that
    direction.
    """

    accepted: _MoneyBucket
    dismissed: _MoneyBucket
    snoozed: _CountBucket
    rate: float | None
    money_rate: float | None
    raise_: _DirectionBucket = Field(alias="raise")
    lower: _DirectionBucket


@router.get(
    "/decisions/summary",
    response_model=DecisionSummary,
    dependencies=[Depends(require_any_authenticated_user)],
)
def decision_summary() -> dict:
    """Summarise every item-site's current planner decision.

    Open to any authenticated user.  The summary reads each
    item-site's current decision (the latest by decided_at, id
    breaking ties) together with the item_sites reorder points in a
    fixed number of queries whatever the number of item-sites, and
    answers with ``accepted``, ``applied``, ``outstanding``,
    ``dismissed`` and ``snoozed`` buckets.

    An accepted decision is applied when the item-site's current
    ``item_sites.reorder_point`` is not null and differs from the
    decision's ``recommended_reorder_point_units`` by at most 1; every
    other accepted decision is outstanding, including one whose
    item-site row is missing.  ``accepted`` therefore equals
    ``applied`` plus ``outstanding`` in count and in
    ``money_at_stake``.  ``money_at_stake`` sums the decisions'
    snapshotted money at stake and ``without_money`` counts the
    decisions whose snapshot is null.
    """
    row = _repo.decision_summary()
    return {
        "accepted": {
            "count": row["accepted_count"],
            "money_at_stake": float(row["accepted_money"]),
            "without_money": row["accepted_without_money"],
        },
        "applied": {
            "count": row["applied_count"],
            "money_at_stake": float(row["applied_money"]),
            "without_money": row["applied_without_money"],
        },
        "outstanding": {
            "count": row["outstanding_count"],
            "money_at_stake": float(row["outstanding_money"]),
            "without_money": row["outstanding_without_money"],
        },
        "dismissed": {"count": row["dismissed_count"]},
        "snoozed": {"count": row["snoozed_count"]},
    }


def _money_total(rows: list[dict]) -> float:
    """The summed stored money at stake of one bucket; a null snapshot
    adds nothing."""
    return float(
        sum(row["money_at_stake"] for row in rows if row["money_at_stake"] is not None)
    )


def _money_bucket(rows: list[dict], total: float) -> dict:
    """The ``{count, money_at_stake, without_money}`` of one bucket of
    current decisions."""
    return {
        "count": len(rows),
        "money_at_stake": total,
        "without_money": sum(1 for row in rows if row["money_at_stake"] is None),
    }


def _is_raise(row: dict) -> bool:
    """The stored ``current_reorder_point`` is null or below the
    decision's ``recommended_reorder_point_units``."""
    current = row["current_reorder_point"]
    return current is None or current < row["recommended_reorder_point_units"]


def _is_lower(row: dict) -> bool:
    """The stored ``current_reorder_point`` is above the decision's
    ``recommended_reorder_point_units``."""
    current = row["current_reorder_point"]
    return current is not None and current > row["recommended_reorder_point_units"]


def _direction(accepted: list[dict], dismissed: list[dict], is_direction) -> dict:
    """The ``{accepted, dismissed}`` counts of one raise/lower
    direction over the current decisions of those two kinds only."""
    return {
        "accepted": sum(1 for row in accepted if is_direction(row)),
        "dismissed": sum(1 for row in dismissed if is_direction(row)),
    }


@router.get(
    "/decisions/acceptance",
    response_model=DecisionAcceptance,
    dependencies=[Depends(require_any_authenticated_user)],
)
def decision_acceptance() -> dict:
    """The acceptance view over every item-site's current decision.

    Open to any authenticated user (the same guard as
    ``GET /api/drift/decisions/summary``) and a pure read: it answers
    without writing anything.  It reads the current decision of every
    item-site (the latest by decided_at, id breaking ties) with one
    call to ``current_decisions()``.

    ``accepted`` and ``dismissed`` are the current decisions with that
    decision: ``count`` is their number, ``money_at_stake`` sums their
    stored ``money_at_stake`` (a null adds nothing) and
    ``without_money`` counts the nulls.  ``snoozed`` is ``{count}``
    over the current snoozed decisions.

    ``rate`` is ``accepted.count / (accepted.count +
    dismissed.count)`` rounded to 4 decimals, null when both counts
    are 0.  ``money_rate`` is ``accepted.money_at_stake /
    (accepted.money_at_stake + dismissed.money_at_stake)`` rounded to
    4 decimals, null when that sum is 0.

    A decision is a raise when its stored ``current_reorder_point`` is
    null or below its ``recommended_reorder_point_units``, and a cut
    (``lower``) when the stored value is above the recommended one.
    A decision whose two values are equal counts in neither
    direction, only in the totals, and a snoozed decision counts in
    neither.
    """
    rows = _repo.current_decisions()
    accepted = [row for row in rows if row["decision"] == "accepted"]
    dismissed = [row for row in rows if row["decision"] == "dismissed"]
    snoozed = [row for row in rows if row["decision"] == "snoozed"]

    accepted_money = _money_total(accepted)
    dismissed_money = _money_total(dismissed)
    rate_denominator = len(accepted) + len(dismissed)
    money_denominator = accepted_money + dismissed_money

    return {
        "accepted": _money_bucket(accepted, accepted_money),
        "dismissed": _money_bucket(dismissed, dismissed_money),
        "snoozed": {"count": len(snoozed)},
        "rate": (
            round(len(accepted) / rate_denominator, 4) if rate_denominator else None
        ),
        "money_rate": (
            round(accepted_money / money_denominator, 4) if money_denominator else None
        ),
        "raise": _direction(accepted, dismissed, _is_raise),
        "lower": _direction(accepted, dismissed, _is_lower),
    }


@router.get("/decisions", response_model=list[PlannerDecision])
def list_planner_decisions(
    filters: tuple[str | None, str | None] = Depends(decision_filters),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    """List stored planner decisions, newest first.

    With both item_code and site_code, the full decision history of that
    item-site; without either, the current decision (the latest by
    decided_at, id breaking ties) of every item-site that has one.
    Results are ordered by decided_at DESC, id DESC.
    """
    item_code, site_code = filters
    if item_code is not None:
        return _repo.history(item_code, site_code, limit, offset)
    return _repo.current(limit, offset)
