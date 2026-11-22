"""Site-grouped roll-forward projection of the recommendable portfolio.

``GET /api/aggregate/projection`` answers with the site groups of
``app.aggregate.projection.aggregate_projection`` over consecutive
calendar-month buckets starting from the month of ``as_of`` (the
current server date when omitted).  ``GET /api/aggregate/spend``
answers with the monthly spend view of the same projection against
the stored per-month purchase budgets (
``app.aggregate.spend.spend_against_budget``): per month the summed
``committed_spend`` (every group's projected supply value) and
``planned_spend`` (its planned receipts value), their total, the
stored budget of the month (null without one), the remaining amount
and whether the total spend exceeds the budget.  ``GET
/api/aggregate/deferrals`` answers with the deferral candidates of
the over-budget months (
``app.aggregate.deferral.deferral_candidates``): for each month whose
total spend exceeds its stored budget, the planned orders arriving in
that month, ordered with the orders that would not stock out first by
cover descending then value descending and then the stock-out ones by
cover descending, suggested in that order while the suggested value
is still below the overrun (an order that would stock out, or has
value 0, is never suggested), with the overrun, the suggested value,
the still-uncovered amount and the estimate of the following month's
total spend and budget status after the suggested deferrals — a
deferral is estimated one month at a time and later months are not
re-planned.  It accepts no pagination parameters: every candidate of
the requested month(s) comes back in a single response.  The three
routes are read-only: they perform a fixed number of reads and write
nothing, and the excess threshold is not applied.  All are Bearer-
authenticated by the mount-time gate like the other routers.

422 when ``as_of`` is after the current server date (the projection
rolls forward from the current on-hand, so a future as-of is invalid),
when ``months`` is outside 1 to 12 inclusive, or when a lever — the
optional ``service_level``, ``demand_multiplier`` or
``lead_time_multiplier`` query parameters, with exactly the meaning
``POST /api/scenarios/simulate`` gives them — is out of range or its
scaled inputs overflow the reorder formula.

``POST /api/aggregate/release`` takes ``{"month": "YYYY-MM",
"confirm": false}`` and answers with the release plan of
``app.aggregate.release.plan_release`` — the baseline plan's planned
orders of the month (no levers), grouped one draft PO per (site,
supplier) pair with each group's origin tag, lines and value, the
orders whose item-site has no supplier in its purchase-line history as
``unreleasable`` and the total value — with the Acumatica outcome of
each group on top.  A bad month, a missing body or an extra field is
422, as is a ``ValueError`` from ``plan_release``.  A preview
(``confirm`` false or absent, any signed-in user) answers the plan
with ``acumatica_available`` false and null outcomes and codes when
the Acumatica configuration is incomplete (no client call), and
otherwise calls ``preview_draft_purchase_orders`` once, copying each
group's ``outcome``, ``warehouse_code``, ``vendor_code``, ``order_nbr``
and ``error``; an Acumatica auth or transport failure is 502.  A
confirm (``confirm`` true) is checked in order — a non-admin gets 403
(``require_admin``), a second confirm while one is running gets 409
(the whole release holds a PostgreSQL advisory lock, ``pg_try_advisory_
lock`` on a fixed key, on its own connection held for the duration and
released in a ``finally``, so the guarantee holds across several API
worker processes), incomplete Acumatica configuration gets 503,
switched-off writes get 409 saying writes are off — and then writes
one ``pending`` ``plan_releases`` row per group (the caller's username
as actor, the group's exact lines), calls
``create_draft_purchase_orders`` once with every group, and updates
each row to the group's outcome (``created``, ``exists`` or ``error``)
with its order number, codes and error, returning the plan with each
group's outcome.  An Acumatica auth or transport failure that aborts
the batch is 502 and marks the release's rows ``aborted``, so the
audit always shows every release attempt; an ``exists`` outcome is
never returned when the current plan's lines differ from the stored
snapshot of the same origin — the release then fails with a conflict
and the group's row is ``error`` instead.  The drafts are created on
hold: committing the spend still requires releasing the hold under
Acumatica's own approval process.  A confirm with no releasable groups
is 200 with empty groups and calls Acumatica not at all.  The plan's
monetary fields — each line's ``unit_cost`` and ``value``, each
group's ``value`` and the plan's ``total_value`` — are exact decimals
(the planned orders' values, never binary floats), rendered as
decimal strings in the JSON body.  ``GET
/api/aggregate/release/audit`` is admin only (403 otherwise) and lists
the stored rows newest first.
The preview and confirm body of the release is the shared
function ``release_groups(plan, confirm, current_user)`` — the
confirm half of it is the standalone ``confirm_release_groups(plan,
actor)``, one confirm path for a person and for a grant, the
standing-grant run confirming through it with
``standing-grant:<id>`` as its actor: the
order-plan release route ``POST /api/supply-plan/release`` (in
``app.api.supply_plan``) feeds it the plan of
``app.supply.release.order_plan_release`` — same lock, same
``plan_releases`` rows, same audit.
``GET /api/aggregate/releases`` answers the tracking view of
``app.aggregate.tracking.release_tracking``: the newest stored
``plan_releases`` row per released order number (outcomes ``created``
and ``exists`` with an order number), read from Acumatica in one
session and classified as of today.  It is open to any signed-in
user behind the mount-time gate: with nothing released it answers
200 with no orders and the zero counts — and no Acumatica call —
even when the Acumatica configuration is incomplete; incomplete
configuration otherwise gets 503, and an Acumatica auth or transport
failure gets 502.  No partial results of a failed read are ever
merged into the answer, and nothing is written.  Every order also
carries ``expected_on`` — the earliest expected date of the lines
that are active, not completed and short of receipt, as an ISO
date, null when there is no such line — and
``promise_is_default`` — true when that earliest line's promise is
the ERP default, false when it is the supplier's own date, null
with ``expected_on``; both are judged from the supplier performance
read once per call, like Expedite, and ``days_late`` counts from
the earliest late line's expected date.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, model_serializer

from app.aggregate import tracking
from app.aggregate.deferral import deferral_candidates
from app.aggregate.projection import aggregate_projection
from app.aggregate.release import (
    exact_decimal,
    plan_release,
    round_half_up_cents,
)
from app.aggregate.spend import spend_against_budget
from app.api.erp_mappings import require_admin
from app.auth.dependencies import require_authenticated_user
from app.auth.models import AuthUser
from app.feed.acumatica_client import (
    AcumaticaAuthError,
    AcumaticaClient,
    AcumaticaConfigError,
    AcumaticaError,
    AcumaticaWritesDisabledError,
)
from app.repositories.plan_releases_postgres_repository import (
    PlanReleasePostgresRepository,
    acquire_release_lock,
    release_lock,
)

router = APIRouter(prefix="/api/aggregate", tags=["aggregate"])
_repo = PlanReleasePostgresRepository()


def require_any_authenticated_user(
    current_user: AuthUser = Depends(require_authenticated_user),
) -> AuthUser:
    """The release routes' own authentication dependency.

    The router is mounted with the app's include-time gate, so the
    route itself must not re-declare the gate (it would apply twice);
    this wrapper — the same pattern the replenishment and
    purchase-budgets routes use — takes the authenticated user the
    gate has already resolved and passes it through unchanged.
    """
    return current_user


class ProjectionGroupKey(BaseModel):
    """The grouping dimension and its code."""

    dimension: str
    code: str


class ProjectionBucket(BaseModel):
    """One calendar-month bucket of one site's projection."""

    month: str
    projected_demand_units: int
    projected_supply_units: float
    projected_on_hand_units: float
    projected_shortfall: bool
    projected_demand_value: float
    projected_supply_value: float
    signal_units: float
    planned_receipts_units: int
    planned_receipts_value: float
    planned_on_hand_units: float
    planned_shortfall: bool


class ProjectionGroup(BaseModel):
    """One site's projection: its buckets and its item-sites without a unit cost."""

    group: ProjectionGroupKey
    buckets: list[ProjectionBucket]
    item_sites_without_unit_cost: int


@router.get("/projection", response_model=list[ProjectionGroup])
def projection(
    as_of: date | None = Query(default=None),
    months: int = Query(default=6, ge=1, le=12),
    service_level: float | None = Query(default=None),
    demand_multiplier: float | None = Query(default=None),
    lead_time_multiplier: float | None = Query(default=None),
) -> list[dict]:
    """Project demand, supply and on-hand by site over the next months.

    Read-only.  422 when ``as_of`` is after the current server date or
    when ``months`` is outside 1 to 12 inclusive, and 422 when a
    given lever is out of range (``service_level`` must be a number
    strictly between 0 and 1, each multiplier a positive finite
    number) or a scaled input overflows the reorder formula.
    """
    try:
        return aggregate_projection(
            as_of=as_of,
            months=months,
            service_level=service_level,
            demand_multiplier=demand_multiplier,
            lead_time_multiplier=lead_time_multiplier,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class SpendMonth(BaseModel):
    """One calendar month of the spend view against the stored budget."""

    month: str
    committed_spend: float
    planned_spend: float
    total_spend: float
    budget: float | None
    remaining: float | None
    over_budget: bool


class SpendAgainstBudget(BaseModel):
    """The monthly spend of the projection against the stored budgets."""

    months: list[SpendMonth]
    item_sites_without_unit_cost: int


@router.get("/spend", response_model=SpendAgainstBudget)
def spend(
    as_of: date | None = Query(default=None),
    months: int = Query(default=6, ge=1, le=12),
    service_level: float | None = Query(default=None),
    demand_multiplier: float | None = Query(default=None),
    lead_time_multiplier: float | None = Query(default=None),
) -> dict:
    """The projected spend by month against the stored purchase budgets.

    Read-only.  ``committed_spend`` is every site group's projected
    supply value of the month, ``planned_spend`` its planned receipts
    value and ``total_spend`` the two together; ``budget`` is the
    stored budget of the month (null without one), ``remaining`` the
    budget minus the total spend and ``over_budget`` true only when a
    budget exists and the total spend exceeds it.  422 when
    ``as_of`` is after the current server date or when ``months`` is
    outside 1 to 12 inclusive, and 422 when a given lever is out of
    range (``service_level`` must be a number strictly between 0 and
    1, each multiplier a positive finite number) or a scaled input
    overflows the reorder formula.
    """
    try:
        return spend_against_budget(
            as_of=as_of,
            months=months,
            service_level=service_level,
            demand_multiplier=demand_multiplier,
            lead_time_multiplier=lead_time_multiplier,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class DeferralCandidate(BaseModel):
    """One planned order arriving in an over-budget month."""

    item_code: str
    site_code: str
    quantity: int
    value: float
    cover_days_if_deferred: float | None
    would_stock_out: bool
    suggested: bool
    cumulative_suggested_value: float


class DeferralMonth(BaseModel):
    """One over-budget month and the deferrals estimated for it."""

    month: str
    overrun: float
    suggested_value: float
    uncovered: float
    next_month: str
    next_month_total_after: float | None
    next_month_over_after: bool
    candidates: list[DeferralCandidate]


class DeferralCandidates(BaseModel):
    """The deferral candidates of the over-budget months of the horizon."""

    months: list[DeferralMonth]


@router.get("/deferrals", response_model=DeferralCandidates)
def deferrals(
    as_of: date | None = Query(default=None),
    months: int = Query(default=6, ge=1, le=12),
    service_level: float | None = Query(default=None),
    demand_multiplier: float | None = Query(default=None),
    lead_time_multiplier: float | None = Query(default=None),
) -> dict:
    """The deferral candidates of the over-budget months of the horizon.

    Read-only, no pagination: every candidate of the requested
    month(s) comes back in a single response.  For each month the
    spend view flags over budget, the planned orders arriving in the
    month are ordered with the orders that would not stock out first,
    by cover descending then value descending, then the stock-out ones
    by cover descending, and suggested in that order while the
    suggested value is still below the overrun — an order that would
    stock out, or has value 0, is never suggested; a deferral is
    estimated one month at a time and later months are not re-planned.
    422 when ``as_of`` is after the current server date or when
    ``months`` is outside 1 to 12 inclusive, and 422 when a given
    lever is out of range (``service_level`` must be a number strictly
    between 0 and 1, each multiplier a positive finite number) or a
    scaled input overflows the reorder formula.
    """
    try:
        return deferral_candidates(
            as_of=as_of,
            months=months,
            service_level=service_level,
            demand_multiplier=demand_multiplier,
            lead_time_multiplier=lead_time_multiplier,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class ReleaseIn(BaseModel):
    """The release body: the month and the confirm flag.

    Any extra field is refused with 422, like every other body in the
    app; a missing body is 422 by request validation.
    """

    model_config = ConfigDict(extra="forbid")

    month: str
    confirm: bool = False


class ReleaseLine(BaseModel):
    """One line of one release group, exactly as sent to Acumatica.

    The monetary fields are exact decimals — the planned order's
    values, never binary floats — rendered as decimal strings in the
    JSON body.  ``source`` is the order plan line's source —
    ``buffer`` or ``reorder_point`` — or ``top_up`` for a chosen
    top-up's line; a line with no source — the aggregate release's
    lines and the snapshots stored before the field — leaves it out
    of the answer, the way the plan line's null ``supplier_reason``
    is left out.
    """

    item_code: str
    quantity: int
    unit_cost: Decimal
    value: Decimal
    source: str | None = None

    @model_serializer(mode="wrap")
    def _serialize_line(self, handler):
        """The line's fields, without ``source`` while it is null —
        the aggregate release's lines and a snapshot stored before
        the field answer exactly as before."""
        data = handler(self)
        if data.get("source") is None:
            data.pop("source", None)
        return data


class ReleaseGroup(BaseModel):
    """One (site, supplier) group of the release plan.

    ``outcome``, ``warehouse_code``, ``vendor_code``, ``order_nbr`` and
    ``error`` are null in the bare plan and after a preview with an
    incomplete Acumatica configuration; the preview fills them from
    the client's per-order result, a confirm from its batch.
    """

    site_code: str
    supplier_code: str
    origin: str
    lines: list[ReleaseLine]
    value: Decimal
    outcome: str | None = None
    warehouse_code: str | None = None
    vendor_code: str | None = None
    order_nbr: str | None = None
    error: str | None = None


class UnreleasableItem(BaseModel):
    """One planned order whose item-site has no supplier in history."""

    item_code: str
    site_code: str
    reason: str


class ReleasePlan(BaseModel):
    """The release plan with the per-group Acumatica outcomes on top."""

    month: str
    groups: list[ReleaseGroup]
    unreleasable: list[UnreleasableItem]
    total_value: Decimal
    acumatica_available: bool | None = None


class PlanReleaseRow(BaseModel):
    """One stored plan_releases audit row, every column of it."""

    id: int
    month: str
    site_code: str
    supplier_code: str
    origin: str
    warehouse_code: str | None
    vendor_code: str | None
    order_nbr: str | None
    outcome: str
    error: str | None
    line_count: int
    value: Decimal
    lines: list[ReleaseLine]
    actor: str
    created_at: str
    updated_at: str


def _acumatica_config_complete() -> bool:
    """Whether the Acumatica configuration is complete enough to call.

    Runs the client's own call-time configuration check (no request):
    True when ``ACUMATICA_BASE_URL``, ``ACUMATICA_TENANT``,
    ``ACUMATICA_USERNAME`` and ``ACUMATICA_PASSWORD`` are all present
    and valid, False otherwise.
    """
    try:
        AcumaticaClient()._load_config()
        return True
    except AcumaticaConfigError:
        return False


def _client_orders(groups: list[dict]) -> list[dict]:
    """The release groups as the Acumatica batch function's orders.

    The plan's exact decimals are rendered in the client's wire
    format, which validates ints and floats only: whole quantities as
    ints, unit costs as the floats they name.
    """
    return [
        {
            "site_id": group["site_code"],
            "vendor_id": group["supplier_code"],
            "origin": group["origin"],
            "lines": [
                {
                    "item_code": line["item_code"],
                    "quantity": int(line["quantity"]),
                    "unit_cost": (
                        float(line["unit_cost"])
                        if isinstance(line["unit_cost"], Decimal)
                        else line["unit_cost"]
                    ),
                }
                for line in group["lines"]
            ],
        }
        for group in groups
    ]


def _canonical_lines(lines: list[dict]) -> tuple:
    """One line snapshot as a comparable value.

    The monetary fields compare as exact decimals — the stored jsonb
    snapshot keeps them as decimal strings while the live plan carries
    Decimals — and the quantity as a whole number, so a changed line
    is detected across both shapes.  Both sides first get the same
    half-up rounding to two decimal places the release applies to
    unit costs, so a snapshot recorded before that rounding —
    unrounded unit costs and values — compares equal to a current
    plan that differs from it only by rounding; a field that is not a
    number at all compares as itself and can never equal a canonical
    one.
    """

    def _money(value: object) -> object:
        try:
            return round_half_up_cents(exact_decimal(value))
        except (ValueError, InvalidOperation):
            return value

    def _units(value: object) -> object:
        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else value
        if isinstance(value, float):
            return int(value) if value.is_integer() else value
        return value

    return tuple(
        (
            line["item_code"],
            _units(line["quantity"]),
            _money(line["unit_cost"]),
            _money(line["value"]),
        )
        for line in lines
    )


def _apply_results(groups: list[dict], results: list[dict]) -> None:
    """Copy each client per-order result onto its group, in order."""
    for group, result in zip(groups, results):
        group["outcome"] = result["outcome"]
        group["warehouse_code"] = result["warehouse_code"]
        group["vendor_code"] = result["vendor_code"]
        group["order_nbr"] = result["order_nbr"]
        group["error"] = result["error"]


def release_groups(plan: dict, confirm: bool, current_user: AuthUser) -> dict:
    """The shared preview and confirm body of the release routes.

    ``plan`` is a plan dict of the shape ``plan_release`` returns —
    ``{"month", "groups", "unreleasable", "total_value"}`` — and the
    body answers it with the Acumatica outcome of each group on top.
    ``release_plan`` feeds it the plan of ``plan_release(month)``; the
    order-plan release route ``POST /api/supply-plan/release`` feeds
    it the plan of ``app.supply.release.order_plan_release``, whose
    groups carry the order-plan origin tag instead of the month's.

    Preview (``confirm`` false or absent, any signed-in user): the
    plan with ``acumatica_available`` false and null outcomes and
    codes when the Acumatica configuration is incomplete (no client
    call), otherwise ``preview_draft_purchase_orders`` called once and
    each group's ``outcome``, ``warehouse_code``, ``vendor_code``,
    ``order_nbr`` and ``error`` copied from the per-order results; an
    Acumatica auth or transport failure is 502.

    Confirm (``confirm`` true): a non-admin gets 403
    (``require_admin``) and the confirm half then runs as
    :func:`confirm_release_groups` with the caller's username (or
    subject) as its actor — the standing-grant run confirms through
    the very same function, so a person and a grant share one confirm
    path.
    """
    if confirm:
        require_admin(current_user)
        return confirm_release_groups(plan, current_user.username or current_user.sub)
    if not _acumatica_config_complete():
        return {**plan, "acumatica_available": False}
    client = AcumaticaClient()
    try:
        results = client.preview_draft_purchase_orders(_client_orders(plan["groups"]))
    except AcumaticaAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except AcumaticaConfigError:
        # The configuration changed between the check and the
        # call: report it as unavailable, not as a failure.
        return {**plan, "acumatica_available": False}
    except AcumaticaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    _apply_results(plan["groups"], results)
    return {**plan, "acumatica_available": True}


def confirm_release_groups(plan: dict, actor: str) -> dict:
    """The confirm half of the release body, one path for a person and
    for a grant.

    ``plan`` is the plan dict to release — the same shape
    :func:`release_groups` takes — and ``actor`` the actor the
    release's ``plan_releases`` rows record (the caller's username or
    subject for a person's confirm, ``standing-grant:<id>`` for the
    standing-grant run's).  Checked in order with nothing written to
    Acumatica before every check passes: the whole release holds the
    PostgreSQL advisory lock on its own connection (a second confirm
    while one is running gets 409), released in a ``finally`` so the
    guarantee holds across several API worker processes; incomplete
    Acumatica configuration gets 503; switched-off writes get 409
    saying writes are off.  Otherwise one ``pending`` ``plan_releases``
    row per group is written (``actor`` as its actor, the group's
    exact lines), ``create_draft_purchase_orders`` is called once with
    every group — the drafts are created on hold, committing the spend
    still requires releasing the hold under Acumatica's own approval
    process — and each row is updated to the group's outcome
    (``created``, ``exists`` or ``error``) with its order number, codes
    and error, returning the plan with each group's outcome.  An
    Acumatica auth or transport failure that aborts the batch is 502
    and marks the release's rows ``aborted``, so the audit always shows
    every release attempt.  An ``exists`` outcome is refused — 409, the
    group's row ``error`` — when the current plan's lines differ from
    the stored snapshot of the same origin — a difference of rounding
    alone is not a difference, the stored line's ``unit_cost`` and
    ``value`` are rounded the same half-up way before the comparison —
    so a changed plan is never reported as already bought.  A confirm
    with no releasable groups is 200 with empty groups and calls
    Acumatica not at all.
    """
    if not plan["groups"]:
        # Nothing to release: no lock, no configuration consultation,
        # no call of any kind.
        return {**plan, "acumatica_available": _acumatica_config_complete()}
    lock_conn = acquire_release_lock()
    if lock_conn is None:
        raise HTTPException(
            status_code=409, detail="Another release is already in progress"
        )
    try:
        if not _acumatica_config_complete():
            raise HTTPException(
                status_code=503, detail="Acumatica configuration is incomplete"
            )
        client = AcumaticaClient()
        try:
            # The write switch, consulted the way the client
            # consults it — before any request, no write.
            client.create_draft_purchase_orders([])
        except AcumaticaWritesDisabledError:
            raise HTTPException(
                status_code=409, detail="Acumatica writes are disabled"
            ) from None
        release_ids = [
            _repo.insert_pending(
                month=plan["month"],
                site_code=group["site_code"],
                supplier_code=group["supplier_code"],
                origin=group["origin"],
                lines=group["lines"],
                value=group["value"],
                actor=actor,
            )
            for group in plan["groups"]
        ]
        try:
            results = client.create_draft_purchase_orders(
                _client_orders(plan["groups"])
            )
        except AcumaticaAuthError as exc:
            for release_id in release_ids:
                _repo.update_outcome(release_id, outcome="aborted", error=str(exc))
            raise HTTPException(status_code=502, detail=str(exc)) from None
        except AcumaticaError as exc:
            for release_id in release_ids:
                _repo.update_outcome(release_id, outcome="aborted", error=str(exc))
            raise HTTPException(status_code=502, detail=str(exc)) from None
        except ValueError as exc:
            for release_id in release_ids:
                _repo.update_outcome(release_id, outcome="error", error=str(exc))
            raise HTTPException(status_code=422, detail=str(exc)) from None
        # An ``exists`` outcome is only accepted when the current
        # plan's snapshot matches every stored snapshot of the same
        # origin — otherwise the existing PO was planned with other
        # lines and the release fails with a conflict instead.
        stale: set[int] = set()
        snapshots_by_origin: dict[str, list] = {}
        for index, (group, result) in enumerate(zip(plan["groups"], results)):
            if result["outcome"] != "exists":
                continue
            if group["origin"] not in snapshots_by_origin:
                snapshots_by_origin[group["origin"]] = _repo.list_snapshots_for_origins(
                    [group["origin"]]
                ).get(group["origin"], [])
            current = _canonical_lines(group["lines"])
            if any(
                _canonical_lines(stored) != current
                for stored in snapshots_by_origin[group["origin"]]
            ):
                stale.add(index)
        for index, (
            group,
            release_id,
            result,
        ) in enumerate(zip(plan["groups"], release_ids, results)):
            if index in stale:
                _repo.update_outcome(
                    release_id,
                    outcome="error",
                    error=(
                        f"the existing PO for origin {group['origin']} "
                        "no longer matches the planned lines; "
                        "re-confirm is required"
                    ),
                )
            else:
                _repo.update_outcome(
                    release_id,
                    outcome=result["outcome"],
                    order_nbr=result["order_nbr"],
                    warehouse_code=result["warehouse_code"],
                    vendor_code=result["vendor_code"],
                    error=result["error"],
                )
        if stale:
            raise HTTPException(
                status_code=409,
                detail=(
                    "the planned lines no longer match the existing "
                    "Acumatica PO(s) for "
                    + ", ".join(sorted(plan["groups"][i]["origin"] for i in stale))
                    + "; the release requires re-confirmation"
                ),
            )
        _apply_results(plan["groups"], results)
        return {**plan, "acumatica_available": True}
    finally:
        release_lock(lock_conn)


@router.post("/release", response_model=ReleasePlan)
def release_plan(
    body: ReleaseIn,
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """Preview or confirm the release of one month's planned orders.

    Preview (``confirm`` false or absent, any signed-in user): the
    plan of ``plan_release(month)`` with ``acumatica_available`` false
    and null outcomes and codes when the Acumatica configuration is
    incomplete (no client call), otherwise
    ``preview_draft_purchase_orders`` called once and each group's
    ``outcome``, ``warehouse_code``, ``vendor_code``, ``order_nbr`` and
    ``error`` copied from the per-order results; an Acumatica auth or
    transport failure is 502.  422 for a bad month or a
    ``plan_release`` ``ValueError``.

    Confirm (``confirm`` true), checked in order with nothing written
    to Acumatica before every check passes: a non-admin gets 403
    (``require_admin``); the whole release then holds the PostgreSQL
    advisory lock on its own connection (a second confirm while one is
    running gets 409), released in a ``finally`` so the guarantee holds
    across several API worker processes; incomplete Acumatica
    configuration gets 503; switched-off writes get 409 saying writes
    are off.  Otherwise one ``pending`` ``plan_releases`` row per
    group is written (the caller's username as actor, the group's
    exact lines), ``create_draft_purchase_orders`` is called once with
    every group — the drafts are created on hold, committing the spend
    still requires releasing the hold under Acumatica's own approval
    process — and each row is updated to the group's outcome
    (``created``, ``exists`` or ``error``) with its order number, codes
    and error, returning the plan with each group's outcome.  An
    Acumatica auth or transport failure that aborts the batch is 502
    and marks the release's rows ``aborted``, so the audit always shows
    every release attempt.  An ``exists`` outcome is refused — 409, the
    group's row ``error`` — when the current plan's lines differ from
    the stored
    snapshot of the same origin — a difference of rounding alone is
    not a difference, the stored line's ``unit_cost`` and ``value``
    are rounded the same half-up way before the comparison — so a
    changed plan is never reported as already bought.  A confirm with no releasable groups is
    200 with empty groups and calls Acumatica not at all.
    """
    if body.confirm:
        # The confirm is refused before the plan is built, so a
        # non-admin with a bad month still gets the 403; the same
        # check stands on its own in release_groups for the
        # order-plan route, which has no month of its own.
        require_admin(current_user)
    try:
        plan = plan_release(body.month)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return release_groups(plan, body.confirm, current_user)


@router.get("/release/audit", response_model=list[PlanReleaseRow])
def list_plan_releases(
    current_user: AuthUser = Depends(require_admin),
) -> list[dict]:
    """Every stored release row, newest first; admin only.

    ADMIN-ONLY, checked via ``require_admin`` exactly like the confirm:
    a signed-in non-admin is refused with 403 and without a token with
    401.  Rows are ordered by ``created_at DESC, id DESC`` so equal
    timestamps list deterministically, and every column of the row is
    returned (``created_at`` and ``updated_at`` as ISO-8601).
    """
    return _repo.list_newest()


class ReleaseTrackingLine(BaseModel):
    """One order line as Acumatica reports it.

    ``order_qty`` and ``received_qty`` are the ordered and the on-
    receipt (``QtyOnReceipts``) quantities, and ``promised`` the
    line's promised date as an ISO calendar date, or null.
    """

    item_code: str
    warehouse_code: str | None
    order_qty: float
    received_qty: float
    completed: bool
    cancelled: bool
    promised: str | None
    item_description: str | None = None


class ReleaseTrackingOrder(BaseModel):
    """One released order with its tracking state as of today.

    ``state`` is one of ``late``, ``awaiting_approval``,
    ``partially_received``, ``open``, ``received``, ``cancelled``,
    ``missing``, in that priority order.  ``days_waiting`` is the
    whole days since the order's ERP date while it is on hold — null
    when it is not on hold — and ``days_late`` is today minus the
    earliest late line's expected date — null unless the state is
    ``late``.  ``expected_on`` is the earliest expected date of the
    lines that are active, not completed and short of receipt, as an
    ISO date, null when there is no such line, and
    ``promise_is_default`` is true when that earliest line's promise
    is the ERP default, false when it is the supplier's own date —
    both null together with ``expected_on``, judged from the
    supplier performance read like Expedite (absent from the
    order, and from the body, only when that read is refused — an
    as_of after the server's today, which the route never asks
    for).  ``value`` is the
    release row's exact decimal, rendered as a decimal string in the
    JSON body.
    """

    order_nbr: str
    state: str
    month: str | None
    site_code: str | None
    supplier_code: str | None
    warehouse_code: str | None
    vendor_code: str | None
    released_at: str | None
    actor: str | None
    value: Decimal | None
    status: str | None
    hold: bool | None
    promised_on: str | None
    days_waiting: int | None
    days_late: int | None
    lines: list[ReleaseTrackingLine]
    supplier_name: str | None = None
    expected_on: str | None = None
    promise_is_default: bool | None = None


class ReleaseTrackingCounts(BaseModel):
    """The fixed state counts of the tracking view.

    Exactly these four keys, each an integer, no more and no fewer:
    ``partly_received`` counts the ``partially_received`` state.
    """

    awaiting_approval: int
    late: int
    partly_received: int
    received: int


class ReleaseTrackingView(BaseModel):
    """The tracking view of every released order as of ``as_of``."""

    as_of: str
    orders: list[ReleaseTrackingOrder]
    counts: ReleaseTrackingCounts


@router.get(
    "/releases",
    response_model=ReleaseTrackingView,
    response_model_exclude_unset=True,
)
def release_tracking_view(
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """Where the released plan POs stand in Acumatica, as of today.

    Open to any signed-in user behind the mount-time gate, like the
    release preview (the admin-only audit route stays admin only).
    The newest ``plan_releases`` row per released order number —
    outcomes ``created`` and ``exists`` with an order number — is
    read from Acumatica in one session and classified as of today:
    the orders come back sorted by state priority (``late`` first,
    then ``awaiting_approval``, ``partially_received``, ``open``,
    ``received``, ``cancelled``, ``missing``) then oldest release
    first, with the fixed counts keys ``awaiting_approval``,
    ``late``, ``partly_received`` and ``received``.  With nothing
    released the answer is 200 with no orders and zero counts — and
    no Acumatica call — taking precedence over the 503; incomplete
    Acumatica configuration otherwise gets 503 and an Acumatica auth
    or transport failure 502, with no partial results of a failed
    read ever merged into the answer.  Nothing is written.

    Every order line in the response carries ``item_description`` —
    null when the item is missing from the inventory items read
    model, which is every item when the read model is empty — so the
    ``exclude_unset`` response never drops the field from the body.
    Every order also carries ``expected_on`` and
    ``promise_is_default`` — judged from the supplier performance
    read once per call, like Expedite, and null together when the
    order has no line that is active, not completed and short of
    receipt — so the ``exclude_unset`` response never drops them,
    except when the performance read is refused — an as_of after
    the server's today, which this route never answers.
    """
    try:
        result = tracking.release_tracking()
    except AcumaticaConfigError:
        raise HTTPException(
            status_code=503, detail="Acumatica configuration is incomplete"
        ) from None
    except AcumaticaAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except AcumaticaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    # The aggregate leaves item_description off every line when the
    # inventory items read model is empty; set it (null for the
    # unknown item) so the exclude_unset response body still carries
    # the field instead of dropping it entirely.
    for order in result["orders"]:
        for line in order["lines"]:
            if isinstance(line, dict):
                line.setdefault("item_description", None)
    return result
