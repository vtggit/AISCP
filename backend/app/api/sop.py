"""The S&OP review of the horizon months and its sign-offs.

``GET /api/sop/review`` answers with the S&OP review of
``app.sop.review.build_sop_review``: per horizon month the summed
demand, the demand-signal units, supply, planned receipts and
on-hand of every site group, the
site codes that run short as-is and under the planned orders, and the
month's spend against the stored purchase budgets — the projection's
groups fed to the spend view over the same buckets, so the projection
is computed once — beside the realised accuracy of the stored
forecast snapshots (its overall rates and the bucket-1, next month,
``wape`` and ``bias``) and ``latest_signoff``, the newest stored
sign-off of the review month without its figures (``None`` when the
month carries none), and ``since_signoff``, the drift of the review
against the newest stored sign-off of any month (the row with its
figures first under ``created_at DESC, id DESC``) — ``None`` when no
sign-off exists at all: the row's ``id``, ``month`` and ``created_at``
as ``signoff_id``, ``signed_month`` and ``signed_at``, its
``actor_name`` (its ``actor`` when that is missing or blank) as
``signed_by``, its figures' ``as_of`` (null when absent) as
``signed_as_of``, and one entry per month that appears in both the
review's ``months`` and the stored figures' ``months`` (in the
review's order) with the current minus stored value of
``demand_units``, ``demand_value``, ``signal_units``,
``supply_value``, ``planned_receipts_value``, ``total_spend`` and
``budget`` (a delta is null when either value is null, missing or
not a finite number; a
stored figures without a usable ``months`` answers an empty
``months``).  ``signoff_proposal`` proposes signing the month off
— null when ``since_signoff`` is null (no earlier sign-off) or
``latest_signoff`` is not null (the review month is already signed
off), else ``proposed`` (true when at least one change was compared
and the largest is at most the effective value of the
``sop.signoff_proposal_max_change`` setting, read through
``setting_value`` from the stored system settings, one read per
review, and answered as ``max_change``), the largest compared
relative change of the value fields ``demand_value``,
``supply_value``, ``planned_receipts_value``, ``total_spend`` and
``budget`` of every ``since_signoff`` month (the absolute delta over
the larger of the current and the stored value's magnitudes, 0 when
both are 0, a null delta or current value left out) as
``largest_change`` with ``largest_field`` and ``largest_month`` where
it was (the first in review-month and field order winning a tie, all
null when nothing was compared), ``compared_values`` the number of
changes compared and ``since_month`` the ``since_signoff``'s signed
month.  A sign-off stores the review answer, the proposal included.
``as_of`` is the current server date,
``review_month`` the first month of the horizon and
``item_sites_without_unit_cost`` the value the spend view returns.
It accepts no pagination parameters: every month of the requested
horizon comes back in a single response.  The route is read-only — it
performs a fixed number of reads and writes nothing, and it makes no
ERP call — and is open to any signed-in user behind the mount-time
Bearer gate like the other routers.

``GET /api/sop/scenarios`` (an optional ``months`` — default 3, bounded
1 to 12 inclusive, out of bounds is 422) answers with the comparison of
the first 5 stored scenarios (newest first, every stored scenario
counted in ``scenarios_total``) against the no-lever baseline projection
of the same horizon: ``as_of`` (the current server date),
``horizon_months`` (the requested ``months``), ``scenarios_total`` and
``baseline``/``scenarios`` (each carrying ``demand_value``,
``total_spend``, ``months_over_budget`` and ``months_short_planned``,
and each scenario its id, name, levers, the deltas against the baseline
and ``error``).  A scenario whose levers make the projection raise
``ValueError`` — or that carries a lever the projection does not accept,
such as a non-null ``service_level_by_class`` — answers with ``error``
set and null figures and deltas, the others still answering; a
``ValueError`` from the baseline is 422.  With no stored scenarios
``scenarios`` is empty, ``scenarios_total`` is 0 and the baseline is
filled (status 200).  It is read-only — it writes nothing and makes no
ERP call — and is open to any signed-in user behind the mount-time
Bearer gate like the review.

``POST /api/sop/signoffs`` stores one sign-off of the current review
month — admin only (a signed-in non-admin is refused 403 by
``require_admin`` before anything is computed or stored).  The body
carries ``month`` (``YYYY-MM`` — malformed is 422), an optional
``note`` (at most 500 characters without NUL bytes, else 422; a blank
one is stored as null) and an optional ``months`` (default 3, bounded
1 to 12 inclusive, like the review's).  The route computes
``build_sop_review(months)`` once on the server and stores that
answer as the row's ``figures`` — a second sign-off of the month
writes a second row, and no earlier row changes — answering 201 with
the stored row.  A ``month`` that is not the review's ``review_month``
is 422 ("Only the current review month <review_month> can be signed
off.") without writing a row, and so are an over-long or NUL ``note``,
an out-of-bounds ``months`` and any extra body field.  It makes no
ERP call, reads no write switch and changes no budget, scenario,
recommendation or release row.

``GET /api/sop/signoffs`` (an optional ``month`` — malformed is 422 —
and a ``limit``, default 50, bounded 1 to 200 inclusive) lists the
stored rows newest first (``created_at DESC, id DESC``) without their
figures, and ``GET /api/sop/signoffs/{id}`` answers one stored row
with its figures, 404 for an unknown id and 422 for a non-integer id.
Both read routes are open to any signed-in user.

``GET /api/sop/signoffs/proposals`` (declared before the dynamic
``GET /api/sop/signoffs/{id}`` so it is reachable) answers the stored
sign-off proposal of every signed month: for the latest sign-off of
each month (``created_at`` newest first, the ``id`` breaking ties) one
``{month, proposed}`` entry in ``months``, newest month first —
``proposed`` true or false when the stored ``figures ->
'signoff_proposal'``'s ``proposed`` is true or false, else null: the
key absent, the value stored as null, or the stored proposal an object
missing ``proposed`` — a month signed before the proposal existed, or
one with no earlier sign-off to compare — beside ``signoffs`` (the
number of signed months), ``proposed`` and ``not_proposed`` (the months
whose stored proposal's ``proposed`` is true and false) and
``without_proposal`` (the months whose stored figures carry no
proposal).  It runs one query that reads only the stored
``signoff_proposal`` key of the latest row of each month, writes
nothing, makes no ERP call, and is open to any signed-in user.
"""

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.erp_mappings import require_admin, require_any_authenticated_user
from app.auth.models import AuthUser
from app.repositories.sop_signoffs_postgres_repository import (
    SopSignoffsPostgresRepository,
)
from app.sop.review import build_sop_review, build_sop_scenarios

router = APIRouter(prefix="/api/sop", tags=["sop"])
_signoffs_repo = SopSignoffsPostgresRepository()

# The review-month shape a sign-off body and a list query must carry.
_MONTH = re.compile(r"\d{4}-\d{2}")
_NUL = "\x00"


class SopReviewMonth(BaseModel):
    """One horizon month of the S&OP review."""

    month: str
    demand_units: int
    demand_value: float
    signal_units: float
    supply_units: float
    supply_value: float
    planned_receipts_units: int
    planned_receipts_value: float
    projected_on_hand_units: float
    planned_on_hand_units: float
    sites_short_as_is: list[str]
    sites_short_planned: list[str]
    committed_spend: float
    planned_spend: float
    total_spend: float
    budget: float | None
    remaining: float | None
    over_budget: bool


class SopReviewForecast(BaseModel):
    """The realised accuracy of the stored forecast snapshots."""

    as_of: date
    compared_buckets: int
    wape: float | None
    bias: float | None
    effective_wape: float | None
    effective_bias: float | None
    value_added: float | None
    overridden_buckets: int
    next_month_wape: float | None
    next_month_bias: float | None


class SopReviewSinceSignoffMonth(BaseModel):
    """One horizon month that appears in both the current review and
    the newest stored sign-off's figures: the current value minus the
    stored one of each delta field, null when either value is null,
    missing or not a finite number."""

    month: str
    demand_units_delta: float | None
    demand_value_delta: float | None
    signal_units_delta: float | None
    supply_value_delta: float | None
    planned_receipts_value_delta: float | None
    total_spend_delta: float | None
    budget_delta: float | None


class SopReviewSinceSignoff(BaseModel):
    """The drift of the current review against the newest stored
    sign-off of any month — the review's ``since_signoff`` field,
    ``None`` when no sign-off exists at all.  ``signed_by`` is the
    row's ``actor_name`` (its ``actor`` when that is missing or blank)
    and ``signed_as_of`` the row's figures' ``as_of`` (null when
    absent); ``months`` holds one entry per month in both the review
    and the stored figures' months, in the review's order."""

    signoff_id: int
    signed_month: str
    signed_at: str
    signed_by: str
    signed_as_of: str | None
    months: list[SopReviewSinceSignoffMonth]


class SopSignoffRow(BaseModel):
    """A stored sign-off without its figures."""

    id: int
    month: str
    note: str | None
    actor: str
    actor_name: str | None
    created_at: str


class SopSignoff(SopSignoffRow):
    """One stored sign-off with its figures — the S&OP review answer
    the route computed on the server at sign-off time."""

    figures: dict


class SopSignoffProposalMonth(BaseModel):
    """The stored proposal of one signed month's latest sign-off.

    ``proposed`` is true or false when the stored
    ``figures -> 'signoff_proposal'``'s ``proposed`` is true or false,
    else null — the key absent, the value stored as null, or the
    stored proposal an object missing ``proposed``.
    """

    month: str
    proposed: bool | None


class SopSignoffsProposals(BaseModel):
    """The stored sign-off proposals of every signed month, newest
    month first."""

    signoffs: int
    proposed: int
    not_proposed: int
    without_proposal: int
    months: list[SopSignoffProposalMonth]


class SopSignoffIn(BaseModel):
    """The sign-off body.

    ``month`` is the review month to sign (``YYYY-MM``), ``note`` an
    optional note (at most 500 characters, no NUL bytes — a blank one
    is stored as null) and ``months`` the review's horizon, default 3,
    bounded 1 to 12 inclusive.  Any extra field is refused with 422,
    like every other body in the app.
    """

    model_config = ConfigDict(extra="forbid")

    month: str
    note: str | None = None
    months: int = Field(default=3, ge=1, le=12)

    @field_validator("month")
    @classmethod
    def _month_shape(cls, value: str) -> str:
        if not _MONTH.fullmatch(value):
            raise ValueError("month must match YYYY-MM")
        return value

    @field_validator("note")
    @classmethod
    def _note_bounds(cls, value: str | None) -> str | None:
        if value is not None:
            if len(value) > 500:
                raise ValueError("note must be at most 500 characters")
            if _NUL in value:
                raise ValueError("note must not contain NUL bytes")
        return value


class SopReviewSignoffProposal(BaseModel):
    """The proposal to sign the review month off: the largest
    relative change of the value figures since the newest stored
    sign-off against the effective value of the
    ``sop.signoff_proposal_max_change`` setting, and where the
    largest change was (all null when nothing was compared)."""

    proposed: bool
    max_change: float
    largest_change: float | None
    largest_field: str | None
    largest_month: str | None
    compared_values: int
    since_month: str


class SopReviewResponse(BaseModel):
    """The S&OP review of the horizon months.

    ``signoff_proposal`` is the proposal to sign the month off,
    ``None`` without an earlier sign-off or while the review month
    is already signed off — see the module docstring for its
    fields."""

    as_of: date
    review_month: str
    horizon_months: int
    months: list[SopReviewMonth]
    forecast: SopReviewForecast
    item_sites_without_unit_cost: int
    latest_signoff: SopSignoffRow | None
    since_signoff: SopReviewSinceSignoff | None
    signoff_proposal: SopReviewSignoffProposal | None


@router.get("/review", response_model=SopReviewResponse)
def sop_review(months: int = Query(default=3, ge=1, le=12)) -> dict:
    """The S&OP review: the horizon's projection, spend and forecast.

    Read-only, no ERP call, any signed-in user.  ``months`` (default
    3) is the number of horizon months, bounded 1 to 12 inclusive —
    out of bounds is 422, as is any ``ValueError`` the builder
    raises.  Per month the figures are summed over all site groups of
    the projection, ``sites_short_as_is`` lists the site codes whose
    bucket has ``projected_shortfall`` true and ``sites_short_planned``
    the ones with ``planned_shortfall`` true (each sorted ascending),
    and the six spend fields are copied unchanged from the matching
    month of the spend view.  ``latest_signoff`` is the newest stored
    sign-off of the review month without its figures (``None`` when
    the month carries none), and ``since_signoff`` the drift of the
    review against the newest stored sign-off of any month (``None``
    when no sign-off exists at all) — see the module docstring for
    its fields.  With no recommendable item-sites every
    month still appears with zero figures and empty site lists, and
    with no stored snapshots the forecast rates are null.
    """
    try:
        return build_sop_review(months)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class SopScenariosBaseline(BaseModel):
    """The baseline (no-lever) comparison figures over the horizon."""

    demand_value: float
    total_spend: float
    months_over_budget: int
    months_short_planned: int


class SopScenariosScenario(BaseModel):
    """One stored scenario compared against the baseline.

    The figures are null when the scenario answers with an ``error``
    (its levers make the projection raise ``ValueError``, or it carries
    a lever the projection does not accept).
    """

    id: str
    name: str
    service_level: float | None = None
    demand_multiplier: float | None = None
    lead_time_multiplier: float | None = None
    demand_value: float | None = None
    total_spend: float | None = None
    months_over_budget: int | None = None
    months_short_planned: int | None = None
    demand_value_delta: float | None = None
    total_spend_delta: float | None = None
    error: str | None = None


class SopScenariosResponse(BaseModel):
    """The S&OP scenarios comparison of the horizon."""

    as_of: date
    horizon_months: int
    scenarios_total: int
    baseline: SopScenariosBaseline
    scenarios: list[SopScenariosScenario]


@router.get("/scenarios", response_model=SopScenariosResponse)
def sop_scenarios(months: int = Query(default=3, ge=1, le=12)) -> dict:
    """The S&OP scenarios comparison of the ``months`` horizon months.

    Read-only, no ERP call, any signed-in user.  ``months`` (default
    3) is bounded 1 to 12 inclusive — out of bounds is 422.  The
    baseline is the no-lever projection and the first 5 stored
    scenarios (newest first) are compared against it: a scenario whose
    levers make the projection raise ``ValueError`` — or that carries a
    lever the projection does not accept — answers with ``error`` set
    and null figures and deltas, the others still answering, and a
    ``ValueError`` from the baseline is 422.  With no stored scenarios
    ``scenarios`` is empty, ``scenarios_total`` is 0 and the baseline
    is filled.
    """
    try:
        return build_sop_scenarios(months)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post(
    "/signoffs",
    response_model=SopSignoff,
    status_code=status.HTTP_201_CREATED,
)
def create_signoff(
    body: SopSignoffIn,
    current_user: AuthUser = Depends(require_admin),
) -> dict:
    """Store one sign-off of the current review month (admin only).

    A signed-in non-admin is refused 403 by ``require_admin`` before
    anything is computed or stored.  The route computes
    ``build_sop_review(months)`` once on the server and stores that
    answer as the row's ``figures`` (they stand as they did before the
    signing — a later budget or projection change never changes the
    stored row), with ``actor`` the caller's username (their subject
    when they have none) and ``actor_name`` their display name.  A
    ``month`` that is not the review's ``review_month`` is 422 —
    "Only the current review month <review_month> can be signed off."
    — without writing a row, and so are a malformed ``month``, an
    over-long or NUL ``note``, an out-of-bounds ``months`` and any
    extra body field.  A blank ``note`` is stored as null.  Signing
    the same month again writes a second row, leaving the earlier one
    untouched.  The route makes no ERP call, reads no write switch and
    changes no budget, scenario, recommendation or release row.
    """
    try:
        figures = build_sop_review(body.months)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if body.month != figures["review_month"]:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Only the current review month {figures['review_month']} "
                "can be signed off."
            ),
        )
    note = None if body.note is None or not body.note.strip() else body.note
    return _signoffs_repo.create(
        month=body.month,
        note=note,
        figures=figures,
        actor=current_user.username or current_user.sub,
        actor_name=current_user.display_name,
    )


@router.get("/signoffs", response_model=list[SopSignoffRow])
def list_signoffs(
    month: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> list[dict]:
    """The stored sign-offs newest first, without their figures.

    Open to any signed-in user.  ``month`` (``YYYY-MM`` — malformed is
    422) narrows the list to one review month, and ``limit`` (default
    50, bounded 1 to 200 inclusive) caps the number of rows.  The rows
    are ordered by ``created_at DESC, id DESC`` — the id breaking
    equal timestamps.
    """
    if month is not None and not _MONTH.fullmatch(month):
        raise HTTPException(status_code=422, detail="month must match YYYY-MM")
    return _signoffs_repo.list_newest(month=month, limit=limit)


@router.get("/signoffs/proposals", response_model=SopSignoffsProposals)
def signoff_proposals(
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """The stored sign-off proposal of every signed month.

    Open to any signed-in user, read-only — one query that reads only
    the stored ``signoff_proposal`` key of the latest sign-off of each
    month (``created_at`` newest first, the ``id`` breaking ties), and
    it writes nothing.  ``months`` carries one ``{month, proposed}``
    entry per signed month, newest month first: ``proposed`` true or
    false when the stored ``figures -> 'signoff_proposal'``'s
    ``proposed`` is true or false, else null — the key absent, the
    value stored as null, or the stored proposal an object missing
    ``proposed`` (a month signed before the proposal existed, or one
    with no earlier sign-off to compare) — and beside them
    ``signoffs``, the number of signed months, ``proposed`` and
    ``not_proposed``, the months whose stored proposal's ``proposed``
    is true and false, and ``without_proposal``, the months whose
    stored figures carry no proposal (a month still counted in
    ``signoffs``).
    """
    proposed = 0
    not_proposed = 0
    without_proposal = 0
    months = []
    for entry in _signoffs_repo.latest_proposals_per_month():
        stored = entry["proposal"]
        value = None
        if isinstance(stored, dict) and isinstance(stored.get("proposed"), bool):
            value = stored["proposed"]
            if value:
                proposed += 1
            else:
                not_proposed += 1
        else:
            without_proposal += 1
        months.append({"month": entry["month"], "proposed": value})
    return {
        "signoffs": len(months),
        "proposed": proposed,
        "not_proposed": not_proposed,
        "without_proposal": without_proposal,
        "months": months,
    }


@router.get("/signoffs/{signoff_id}", response_model=SopSignoff)
def get_signoff(
    signoff_id: int,
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """One stored sign-off with its figures.

    Open to any signed-in user.  A non-integer id is 422 and an
    unknown id 404.  The figures stand as they did before the signing
    — a later budget or projection change never changes the stored
    row.
    """
    row = _signoffs_repo.get_by_id(signoff_id)
    if row is None:
        raise HTTPException(status_code=404, detail="sign-off not found")
    return row
