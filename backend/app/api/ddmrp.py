"""DDMRP buffers: one stored buffer designation per item-site.

``PUT /api/ddmrp/buffers`` (admin only) stores the DDMRP buffer
designation of one item-site, or replaces the stored one when the
pair already has it: the pair must be a row of ``item_sites``,
validated at the application level (the table has no foreign key to
``item_sites``), and a pair that is not in ``item_sites`` answers
404.  The stored row records ``designated_by`` as the token subject
and ``designated_by_name`` as its display name, exactly as planner
decisions record ``decided_by`` and ``decided_by_name``.

``GET /api/ddmrp/buffers`` (any authenticated user) lists every
stored designation ordered by item_code then site_code, and
``DELETE /api/ddmrp/buffers?item_code=…&site_code=…`` (admin only)
removes one: 204 when a stored designation was removed, 404 when
the pair had none.  A designation remains visible when its
item-site row is absent; only the admin DELETE removes it.
``GET /api/ddmrp/adjustments`` (any authenticated user) lists every
stored adjustment factor ordered by ``start_on``, with the
all-buffer rows (both codes null) first on equal dates;
``POST /api/ddmrp/adjustments`` (admin only) stores one —
``item_code`` and ``site_code`` both null for every stored buffer or
both set (a row of ``item_sites``, else 404) for one of them,
``start_on`` and ``end_on`` (inclusive, ``end_on`` never before
``start_on``), a ``factor`` (a decimal strictly above 0 and at most
10) and an optional ``note`` — a window that overlaps a stored
adjustment of the same scope (the same pair, or both all-buffer, a
shared boundary date included) answers 409 with a stable
machine-readable detail (the overlap check and the store hold one
transaction, serialized per scope, so a concurrent same-scope window
can never slip through the check), a factor outside (0, 10], a
reversed window, one code without the other, an empty code or a NUL
byte answers 422, and it answers 201 with the stored row,
created_by the token subject and created_by_name its display name;  ``DELETE
/api/ddmrp/adjustments/{id}`` (admin only) removes one: 204 when a
stored adjustment was removed, 404 when there is no such
adjustment.
``GET /api/ddmrp/zones`` (any authenticated user) answers, as of the
demand statistics' default as-of date, with one row per stored
designation ordered by item_code then site_code: the item-site's
average daily usage over the ``ddmrp.adu_window_days`` window, its
lead time (the pooled observed lead time, or the item-site's master
lead time in days when there is no observation), its demand class and
coefficient of variation, its order cycle days, and the DDMRP buffer
zones of ``app.ddmrp.zones.buffer_zones`` — null zone fields for a
row whose status is not "ok" ("no_demand" when its ADU is 0 or it has
no demand statistics, "no_lead_time" when neither lead time source
exists); each row's ``adu`` is its unadjusted ``adu_base`` times its
``adjustment_factor`` (the pair's own item-site adjustment whose
window contains the as-of date, else the all-buffer adjustment whose
window contains it, else 1.0), the zones being the
``buffer_zones`` of the adjusted ADU.  Every statistic is read once
per call, whatever the number of rows, and nothing is written.
``GET /api/ddmrp/plan`` (any authenticated user) answers, as of the
same default as-of date, with the "ok" zone rows of the zones route —
built by the very same shared code (``ddmrp_zone_rows``), keeping
their ``adu_base`` and ``adjustment_factor`` — each enriched with the
pair's ``on_hand`` quantity (0 with the note "no
on-hand record; treated as 0" when the pair has none), its
``open_supply`` (the sum of the ``open_units`` of its
``open_purchase_lines`` rows with ``open_units`` above 0 whose
``promised_on`` is null or no more than 90 days before the as-of
date, future dates included, a stale note when any line is excluded),
its ``qualified_demand`` (0, with the note "no open sales-order
demand in the feed"), its ``net_flow`` (on hand plus open supply
minus qualified demand), its ``planning_zone`` ("red" at or below the
top of red, "yellow" at or below the top of yellow, "green" at or
below the top of green, "over" above), its ``order_signal`` (net flow
at or below the top of yellow), its ``suggested_order_units`` (the
ceiling of the top of green minus the net flow when the signal, else
0), its ``planning_priority_pct`` (100 times the net flow over the
top of green, one decimal place, null when the top of green is 0),
its ``on_hand_status`` ("stocked_out" at or below 0, "critical" below
half the top of red, "low" below the top of red, "ok" otherwise) and
its ``on_hand_pct_of_red`` (100 times the on hand over the top of
red, one decimal place, null when the top of red is 0) — ordered by
the planning priority ascending (nulls last), then item_code and
site_code, with a summary of the signal and on-hand-status counts.
Nothing is written.
``GET /api/ddmrp/performance?days=90`` (any authenticated user)
answers, over the ``days`` days (7 to 730, default 90; anything else
answers 422) ending on the latest ``snapshot_on`` in
``ddmrp_status_snapshots`` (stored by
``app.ddmrp.snapshots.capture_ddmrp_snapshot``), the plan status of each
item-site with a snapshot in the window: its ``days_observed`` and the
percentage of its observed days whose ``planning_zone`` was "red",
"yellow", "green" or "over" and whose ``on_hand_status`` was
"stocked_out" or "critical", each rounded to one decimal place,
ordered by the red percentage descending, then item_code and
site_code, as ``{"from", "to", "rows"}`` (both dates null and the
rows empty when no snapshot row exists).  Nothing is written.
``GET /api/ddmrp/candidates`` (any authenticated user) answers, as of
the same default as-of date, with one row per item-site of
``app.recommend.abc.abc_for_all`` whose ``abc_class`` is "A" or "B"
and which has no stored row in ``ddmrp_buffers`` — every item-site of
the ABC pool, designated or not — whose ``demand_class`` is not
"no_demand", whose ``adu`` (the item-site's average daily usage over
the ``ddmrp.adu_window_days`` window) is above 0 and whose
``dlt_days`` is at least ``MIN_CANDIDATE_DLT_DAYS`` (7: a buffer
shorter than a week of lead time rarely pays for itself); the
``adu``, the ``dlt_days`` with its ``dlt_source`` and the
``demand_class`` of a pool item-site are the very same shared
computation the zones route uses (``_ddmrp_item_site_stats`` over one
``_ddmrp_stat_context`` read).  Each row carries the pair, its
``abc_class`` and ``annual_value``, its ``demand_class``, ``adu``,
``dlt_days`` and ``dlt_source`` and a ``reason`` reading "<class>
class, <whole days>-day lead time, <demand_class> demand", and the
rows are ordered by ``annual_value`` descending, then item_code and
site_code.  Nothing is written.
"""

import math
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Self
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.auth.authorization import require_role
from app.auth.display_name import display_name
from app.auth.models import AuthUser
from app.db.connection import get_cursor
from app.ddmrp.plan import DdmrpPlanResponse, DdmrpPlanRow, plan_ddmrp
from app.ddmrp.zones import (
    ADU_WINDOW_KEY,
    ZONE_FIELDS,
    buffer_zones,
    setting_value,
)
from app.recommend.abc import abc_for_all
from app.recommend.reorder import recommendations_for_all
from app.repositories.ddmrp_adjustments_postgres_repository import (
    DdmrpAdjustmentPostgresRepository,
    OverlappingAdjustmentError,
)
from app.repositories.ddmrp_buffers_postgres_repository import (
    DdmrpBufferPostgresRepository,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.stats.demand import default_as_of, demand_stats_for_all
from app.stats.lead_time import lead_time_stats_for_all

router = APIRouter(prefix="/api/ddmrp", tags=["ddmrp"])

_repo = DdmrpBufferPostgresRepository()
_adjustments_repo = DdmrpAdjustmentPostgresRepository()
_item_sites = ItemSitePostgresRepository()

_NUL = chr(0)
_MAX_DESIGNATED_BY_LENGTH = 255

#: The factor field's OpenAPI shape: a decimal number with an
#: explicit precision and scale (12 significant digits, 6 of them
#: after the point) — the decimal the API validates (strictly above
#: 0, at most 10) and the zone rows apply; the table stores it with
#: full double-precision fidelity.
_FACTOR_DECIMAL_SCHEMA = {
    "type": "number",
    "format": "decimal",
    "precision": 12,
    "scale": 6,
}

#: An item-site is a DDMRP buffer candidate only when its lead
#: time in days reaches this many: a buffer shorter than a week
#: of lead time rarely pays for itself.
MIN_CANDIDATE_DLT_DAYS = 7


class DdmrpBuffer(BaseModel):
    """One stored DDMRP buffer designation for an item-site."""

    item_code: str
    site_code: str
    designated_by: str
    designated_by_name: str | None = None
    designated_at: datetime
    note: str | None = None


class DdmrpBufferBody(BaseModel):
    """The body of PUT /api/ddmrp/buffers.

    Every business rule is a model validation, so a rejected body
    answers 422 in FastAPI's HTTPValidationError envelope, exactly like
    a malformed one.
    """

    item_code: str = Field(..., max_length=64)
    site_code: str = Field(..., max_length=64)
    note: str | None = None

    @model_validator(mode="after")
    def _check_business_rules(self) -> Self:
        for value in (self.item_code, self.site_code, self.note):
            if value is not None and _NUL in value:
                raise ValueError("must not contain NUL bytes")
        return self


class _BufferKey(BaseModel):
    """The DELETE query pair; a NUL byte in either code answers 422 in
    FastAPI's HTTPValidationError envelope (raised in the dependency
    below)."""

    item_code: str = Field(..., max_length=64)
    site_code: str = Field(..., max_length=64)

    @model_validator(mode="after")
    def _check_business_rules(self) -> Self:
        for value in (self.item_code, self.site_code):
            if _NUL in value:
                raise ValueError("must not contain NUL bytes")
        return self


def buffer_key(
    item_code: str = Query(..., max_length=64),
    site_code: str = Query(..., max_length=64),
) -> tuple[str, str]:
    """The query pair of DELETE /api/ddmrp/buffers.

    Both codes are required (one without the other, or a NUL byte in
    either, answers 422 in FastAPI's HTTPValidationError envelope).
    """
    try:
        _BufferKey(item_code=item_code, site_code=site_code)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    return item_code, site_code


class _DesignatedBy(BaseModel):
    value: str = Field(max_length=_MAX_DESIGNATED_BY_LENGTH)

    @model_validator(mode="after")
    def _no_nul(self) -> Self:
        if _NUL in self.value:
            raise ValueError("must not contain NUL bytes")
        return self


def _require_storable_designated_by(user: AuthUser) -> None:
    """The token subject is stored in designated_by (String(255)); a
    hostile token whose subject would not fit answers 422 instead of a
    database error."""
    try:
        _DesignatedBy(value=user.sub)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def _require_item_site(item_code: str, site_code: str) -> None:
    """The pair must be a row of item_sites.

    ddmrp_buffers has no foreign key to item_sites (and no
    ON DELETE CASCADE): the item-site's existence is validated here at
    the application level, and a pair that is not in item_sites
    answers 404.
    """
    if _item_sites.get_by_key(item_code, site_code) is None:
        raise HTTPException(status_code=404, detail="item site not found")


@router.get("/buffers", response_model=list[DdmrpBuffer])
def list_ddmrp_buffers() -> list[dict]:
    """List every stored designation, ordered by item_code then
    site_code.

    Open to any authenticated user (the router is mounted behind the
    app's authentication gate).
    """
    return _repo.list_all()


@router.put(
    "/buffers",
    response_model=DdmrpBuffer,
    responses={404: {"description": "The item-site is not in item_sites."}},
)
def upsert_ddmrp_buffer(
    body: DdmrpBufferBody,
    user: AuthUser = Depends(require_role("admin")),
) -> dict:
    """Store the DDMRP buffer designation of one item-site, or replace
    the stored one.

    Admin only.  The pair must be a row of item_sites, validated at
    the application level (a pair that is not in item_sites answers
    404).  A second designation for the same pair replaces the stored
    row — its note, its designator and the designation time — without
    adding a second row.  The stored row records designated_by as the
    token subject and designated_by_name as its display name, exactly
    as planner decisions record decided_by and decided_by_name.
    """
    _require_storable_designated_by(user)
    _require_item_site(body.item_code, body.site_code)
    return _repo.upsert(
        item_code=body.item_code,
        site_code=body.site_code,
        designated_by=user.sub,
        designated_by_name=display_name(user),
        note=body.note,
    )


@router.delete(
    "/buffers",
    status_code=204,
    dependencies=[Depends(require_role("admin"))],
    responses={404: {"description": "The pair has no stored designation."}},
)
def delete_ddmrp_buffer(
    key: tuple[str, str] = Depends(buffer_key),
) -> None:
    """Remove the stored DDMRP buffer designation of one item-site.

    Admin only.  Answers 204 when a stored designation was removed and
    404 when the pair had none.
    """
    item_code, site_code = key
    if not _repo.delete(item_code, site_code):
        raise HTTPException(
            status_code=404, detail="no DDMRP buffer designation for item site"
        )


# ---------------------------------------------------------------------------
# GET /zones: the DDMRP buffer zones of every designated item-site
# ---------------------------------------------------------------------------


class DdmrpZoneRow(BaseModel):
    """One designated item-site's DDMRP buffer zones.

    The zone fields (those of ``app.ddmrp.zones.buffer_zones``) are
    null for a row whose status is not "ok".  ``adu_base`` is the
    unadjusted ADU and ``adjustment_factor`` the active adjustment
    factor on the as-of date (1.0 when no adjustment window covers
    it); ``adu`` is ``adu_base`` times that factor, and the zone
    fields are the ``buffer_zones`` of the adjusted ADU.
    """

    item_code: str
    site_code: str
    status: str
    adu_base: float
    adjustment_factor: float
    adu: float
    adu_window_days: float
    dlt_days: float | None
    dlt_source: str | None
    demand_class: str | None
    cv: float | None
    order_cycle_days: float | None
    lead_time_category: str | None
    lead_time_factor: float | None
    variability_category: str | None
    variability_factor: float | None
    red_base: float | None
    red_safety: float | None
    red: float | None
    yellow: float | None
    green: float | None
    top_of_red: int | None
    top_of_yellow: int | None
    top_of_green: int | None
    arithmetic: str | None


class DdmrpZoneResponse(BaseModel):
    """The zones of every designated item-site, as of one date."""

    as_of: date
    rows: list[DdmrpZoneRow]


def _stored_system_settings() -> dict:
    """The stored values of every system setting, in one query."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT key, value FROM settings " "WHERE scope = 'system' AND owner = ''"
        )
        return {row["key"]: row["value"] for row in cur.fetchall()}


def _ddmrp_stat_context(as_of: date | None = None) -> dict:
    """The one read of every statistic a DDMRP row is built from.

        The shared loads of ``ddmrp_zone_rows`` and
        ``GET /api/ddmrp/candidates``, read once per call whatever the
        number of rows: the stored system settings, the
        ``ddmrp.adu_window_days`` window, the demand statistics' default
    as-of date (the given ``as_of`` date, or the demand statistics'
        default as-of date (``app.stats.demand.default_as_of``) when none
        is given), the windowed demand totals (the pair's
        ``demand_events.quantity`` sum in the window ending on that date),
        the demand statistics, the pooled
        lead-time statistics, the reorder recommendations and the
        item_sites rows.
    """
    stored = _stored_system_settings()
    window_days = setting_value(stored, ADU_WINDOW_KEY)

    with get_cursor() as cur:
        as_of = as_of if as_of is not None else default_as_of(cur)

    window_start = as_of - timedelta(days=window_days - 1)
    with get_cursor() as cur:
        cur.execute(
            "SELECT item_code, site_code, SUM(quantity) AS total "
            "FROM demand_events "
            "WHERE occurred_on >= %s AND occurred_on <= %s "
            "GROUP BY item_code, site_code",
            (window_start, as_of),
        )
        window_totals = {
            (row["item_code"], row["site_code"]): float(row["total"] or 0.0)
            for row in cur.fetchall()
        }

    return {
        "stored": stored,
        "window_days": window_days,
        "as_of": as_of,
        "window_totals": window_totals,
        "demand_by_pair": {
            (row["item_code"], row["site_code"]): row
            for row in demand_stats_for_all(as_of)
        },
        "lead_time_by_item": {
            row["item_code"]: row for row in lead_time_stats_for_all(as_of)
        },
        "recommendation_by_pair": {
            (row["item_code"], row["site_code"]): row
            for row in recommendations_for_all(as_of)
        },
        "item_site_by_key": {
            (row["item_code"], row["site_code"]): row
            for row in _item_sites.list_rows(10**9, 0)
        },
    }


def _ddmrp_item_site_stats(context: dict, item_code: str, site_code: str) -> dict:
    """The ADU, DLT and demand class of one item-site.

    The shared computation of ``GET /api/ddmrp/zones`` and
    ``GET /api/ddmrp/candidates``, from the one read of
    ``_ddmrp_stat_context``: the ADU is the pair's windowed demand
    total divided by the ``ddmrp.adu_window_days`` window (a
    non-integer window is used whole, never truncated); the DLT is
    the item's pooled observed lead time in days (``dlt_source``
    "observed") or, when the item has no observation in the window,
    the item-site's ``item_sites.master_lead_time_days``
    (``dlt_source`` "master"; both null when neither source exists);
    the demand class is the pair's demand statistics' class (null
    when it has none).
    """
    key = (item_code, site_code)
    adu = context["window_totals"].get(key, 0.0) / context["window_days"]
    demand = context["demand_by_pair"].get(key)
    lead_time = context["lead_time_by_item"].get(item_code)
    if lead_time is not None:
        dlt_days = float(lead_time["mean_days"])
        dlt_source = "observed"
    else:
        item_site = context["item_site_by_key"].get(key)
        master = item_site["master_lead_time_days"] if item_site else None
        dlt_days = float(master) if master is not None else None
        dlt_source = "master" if master is not None else None
    return {
        "adu": adu,
        "dlt_days": dlt_days,
        "dlt_source": dlt_source,
        "demand_class": demand["demand_class"] if demand is not None else None,
    }


def ddmrp_zone_rows(as_of: date | None = None) -> tuple[date, list[dict]]:
    """The DDMRP buffer-zone rows of every designated item-site.

    The shared row builder of ``GET /api/ddmrp/zones`` and
    ``GET /api/ddmrp/plan``: the zones route answers with exactly
    these rows, and the plan route keeps the "ok" ones and enriches
    them with the on-hand and open-supply position.  As of the given
    ``as_of`` date, or the demand statistics' default as-of date
    (``app.stats.demand.default_as_of``) when none is given, one row
    per stored designation, ordered by item_code then
    site_code: the ADU is the sum of the pair's
    ``demand_events.quantity`` in the ``ddmrp.adu_window_days`` days
    ending on that date, divided by that number of days (a
    non-integer window is used whole, never truncated); the DLT is
    the item's pooled observed lead time in days (``dlt_source``
    "observed") or, when the item has no observation in the window,
    the item-site's ``item_sites.master_lead_time_days``
    (``dlt_source`` "master"); the demand class and the coefficient of
    variation (the square root of the statistics' cv2, null when it is
    null) come from the demand statistics and the order cycle days
    from the item-site's recommended order quantity (null when it has
    none).  The status is "no_demand" when the ADU is 0 or the pair
    has no demand statistics, "no_lead_time" when neither DLT source
    exists and "ok" otherwise; only the "ok" rows carry the zone
    fields of ``app.ddmrp.zones.buffer_zones``, the others carry null
    zone fields.  Every row carries ``adu_base`` (the unadjusted ADU)
    and ``adjustment_factor`` (the pair's own item-site adjustment
    whose window contains the as-of date, else the all-buffer
    adjustment whose window contains it, else 1.0), and its ``adu``
    is ``adu_base`` times that factor — the zones are the
    ``buffer_zones`` of the adjusted ADU, so with no adjustment
    stored every number is the unadjusted one.  The ADU, the DLT with its source and the demand
    class are the shared ``_ddmrp_item_site_stats`` computation
    ``GET /api/ddmrp/candidates`` uses, and the plan rows
    ``app.ddmrp.snapshots.capture_ddmrp_snapshot`` stores.  Every
    statistic is read once per call, whatever the number of rows, and
    nothing is written.  Returns the as-of date and the rows.
    """
    context = _ddmrp_stat_context(as_of)
    stored = context["stored"]

    # The active adjustment factor per scope, in one read: a stored
    # window of one scope never overlaps a stored window of the same
    # scope (the API answers 409), so each key holds at most one
    # factor.  The all-buffer rows key on (None, None), their stored
    # codes being null; an empty-string code, should one ever be
    # stored straight in the database, keys on ("", "") and never
    # collides with the all-buffer scope.
    factors: dict[tuple[str | None, str | None], float] = {}
    for adjustment in _adjustments_repo.list_active_on(context["as_of"]):
        key = (adjustment["item_code"], adjustment["site_code"])
        factors.setdefault(key, float(adjustment["factor"]))

    rows = []
    for buffer in _repo.list_all():
        item_code = buffer["item_code"]
        site_code = buffer["site_code"]
        key = (item_code, site_code)

        stats = _ddmrp_item_site_stats(context, item_code, site_code)
        adu_base = stats["adu"]
        factor = factors.get((item_code, site_code), factors.get((None, None), 1.0))
        adu = adu_base * factor
        dlt_days = stats["dlt_days"]
        demand = context["demand_by_pair"].get(key)

        if adu_base == 0.0 or demand is None:
            status = "no_demand"
        elif dlt_days is None:
            status = "no_lead_time"
        else:
            status = "ok"

        cv2 = demand["cv2"] if demand is not None else None
        recommendation = context["recommendation_by_pair"].get(key)
        order_quantity = (
            recommendation.get("order_quantity") if recommendation else None
        )

        row = {
            "item_code": item_code,
            "site_code": site_code,
            "status": status,
            "adu_base": adu_base,
            "adjustment_factor": factor,
            "adu": adu,
            "adu_window_days": context["window_days"],
            "dlt_days": dlt_days,
            "dlt_source": stats["dlt_source"],
            "demand_class": stats["demand_class"],
            "cv": math.sqrt(cv2) if cv2 is not None else None,
            "order_cycle_days": (
                order_quantity["cover_days"] if order_quantity is not None else None
            ),
        }
        if status == "ok":
            row.update(
                buffer_zones(
                    adu,
                    dlt_days,
                    row["order_cycle_days"],
                    stats["demand_class"],
                    row["cv"],
                    stored,
                )
            )
        else:
            for field in ZONE_FIELDS:
                row[field] = None
        rows.append(row)

    return context["as_of"], rows


@router.get("/zones", response_model=DdmrpZoneResponse)
def list_ddmrp_zones() -> dict:
    """The DDMRP buffer zones of every designated item-site.

    Open to any authenticated user (the router is mounted behind the
    app's authentication gate).  As of the demand statistics' default
    as-of date (``app.stats.demand.default_as_of``), one row per
    stored designation, ordered by item_code then site_code: the ADU
    is the sum of the pair's ``demand_events.quantity`` in the
    ``ddmrp.adu_window_days`` days ending on that date, divided by
    that number of days (a non-integer window is used whole, never
    truncated); the DLT is the item's pooled observed lead
    time in days (``dlt_source`` "observed") or, when the item has no
    observation in the window, the item-site's
    ``item_sites.master_lead_time_days`` (``dlt_source`` "master");
    the demand class and the coefficient of variation (the square
    root of the statistics' cv2, null when it is null) come from the
    demand statistics and the order cycle days from the item-site's
    recommended order quantity (null when it has none).  The status
    is "no_demand" when the ADU is 0 or the pair has no demand
    statistics, "no_lead_time" when neither DLT source exists and
    "ok" otherwise; only the "ok" rows carry the zone fields of
    ``app.ddmrp.zones.buffer_zones``, the others carry null zone
    fields.  Every statistic is read once per call, whatever the
    number of rows, and nothing is written.
    """
    as_of, rows = ddmrp_zone_rows()
    return {"as_of": as_of, "rows": rows}


class DdmrpPlanRowWithAdjustment(DdmrpPlanRow):
    """A plan row that keeps the adjustment-factor fields of the
    shared zone row: ``adu_base`` (the unadjusted ADU) and
    ``adjustment_factor`` (the active factor applied to it), so the
    plan route returns them without omission."""

    adu_base: float
    adjustment_factor: float


class DdmrpPlanResponseWithAdjustment(DdmrpPlanResponse):
    """The DDMRP plan, with the adjustment-factor fields on every row."""

    rows: list[DdmrpPlanRowWithAdjustment]


@router.get("/plan", response_model=DdmrpPlanResponseWithAdjustment)
def get_ddmrp_plan() -> dict:
    """The DDMRP plan of every designated item-site with an "ok" zone row.

    Open to any authenticated user (the router is mounted behind the
    app's authentication gate).  As of the demand statistics' default
    as-of date, builds the zone rows with the same shared code
    ``GET /api/ddmrp/zones`` uses (``ddmrp_zone_rows``), keeps the
    "ok" rows and enriches each with the pair's ``on_hand`` quantity
    (0 with the note "no on-hand record; treated as 0" when the pair
    has none), its ``open_supply`` (the sum of the ``open_units`` of
    its ``open_purchase_lines`` rows with ``open_units`` above 0 whose
    ``promised_on`` is null or no more than 90 days before the as-of
    date, future dates included; the note "<n> stale open purchase
    lines excluded" when any line is excluded), its
    ``qualified_demand`` (0, with the note "no open sales-order
    demand in the feed"), its ``net_flow`` (on hand plus open supply
    minus qualified demand), its ``planning_zone`` ("red" at or below
    the top of red, "yellow" at or below the top of yellow, "green"
    at or below the top of green, "over" above), its
    ``order_signal`` (net flow at or below the top of yellow), its
    ``suggested_order_units`` (the ceiling of the top of green minus
    the net flow when the signal, else 0), its
    ``planning_priority_pct`` (100 times the net flow over the top of
    green, rounded to one decimal place, null when the top of green is
    0), its ``on_hand_status`` ("stocked_out" at or below 0,
    "critical" below half the top of red, "low" below the top of red,
    "ok" otherwise) and its ``on_hand_pct_of_red`` (100 times the on
    hand over the top of red, rounded to one decimal place, null when
    the top of red is 0).  The rows are ordered by
    ``planning_priority_pct`` ascending (nulls last), then item_code
    and site_code, and the response carries a ``summary`` of the
    signal and on-hand-status counts.  Nothing is written.
    """
    as_of, zone_rows = ddmrp_zone_rows()
    return plan_ddmrp(as_of, zone_rows)


# ---------------------------------------------------------------------------
# GET /performance: the plan status of each buffer over the captured days
# ---------------------------------------------------------------------------


class DdmrpPerformanceRow(BaseModel):
    """One item-site's observed plan status over the window.

    ``days_observed`` is the number of the window's days the pair has a
    ``ddmrp_status_snapshots`` row for, and the percentages are the
    shares of those observed days in each planning zone (``red_pct``,
    ``yellow_pct``, ``green_pct``, ``over_pct``) and on-hand status
    (``stocked_out_pct``, ``critical_pct``), each rounded to one
    decimal place.
    """

    item_code: str
    site_code: str
    days_observed: int
    red_pct: float
    yellow_pct: float
    green_pct: float
    over_pct: float
    stocked_out_pct: float
    critical_pct: float


class DdmrpPerformanceResponse(BaseModel):
    """The DDMRP plan status of every captured buffer, over one window.

    ``from`` and ``to`` bound the window of ``days`` days ending on the
    latest ``snapshot_on`` (both null when no snapshot row exists), and
    ``rows`` carries one row per item-site with a snapshot in the
    window, ordered by the red share descending, then item_code and
    site_code.
    """

    from_date: date | None = Field(default=None, alias="from")
    to: date | None = None
    rows: list[DdmrpPerformanceRow] = Field(default_factory=list)


def _pct(count: int, total: int) -> float:
    """A share of ``total`` observed days, as a percentage rounded
    to one decimal place."""
    return round(100.0 * count / total, 1)


@router.get("/performance", response_model=DdmrpPerformanceResponse)
def get_ddmrp_performance(
    days: int = Query(default=90, ge=7, le=730),
) -> dict:
    """The plan status of each captured buffer over the last days.

    Open to any authenticated user (the router is mounted behind the
    app's authentication gate).  ``days`` is 7 to 730 (default 90);
    anything else answers 422.  The window is the ``days`` days ending
    on the latest ``snapshot_on`` in ``ddmrp_status_snapshots`` (the
    window's last day), and each row covers one item-site with at
    least one snapshot row in the window: ``item_code``, ``site_code``,
    its ``days_observed`` (the window days it has a snapshot row for)
    and the percentage of its observed days whose ``planning_zone`` was
    "red", "yellow", "green" or "over" and whose ``on_hand_status`` was
    "stocked_out" or "critical", each percentage rounded to one decimal
    place.  The rows are ordered by the red percentage descending, then
    ``item_code`` and ``site_code``.  With no snapshot row at all,
    ``from`` and ``to`` are null and ``rows`` is empty.  The response
    is ``{"from", "to", "rows"}`` and nothing is written.
    """
    with get_cursor() as cur:
        cur.execute("SELECT MAX(snapshot_on) AS latest FROM ddmrp_status_snapshots")
        latest = cur.fetchone()["latest"]
    if latest is None:
        return {"from": None, "to": None, "rows": []}

    window_start = latest - timedelta(days=days - 1)
    with get_cursor() as cur:
        cur.execute(
            "SELECT item_code, site_code, planning_zone, on_hand_status "
            "FROM ddmrp_status_snapshots "
            "WHERE snapshot_on BETWEEN %s AND %s "
            "ORDER BY item_code, site_code, snapshot_on",
            (window_start, latest),
        )
        snapshot_rows = cur.fetchall()

    observed_by_pair: dict[tuple[str, str], list[dict]] = {}
    for row in snapshot_rows:
        key = (row["item_code"], row["site_code"])
        observed_by_pair.setdefault(key, []).append(row)

    rows = []
    for (item_code, site_code), observed in observed_by_pair.items():
        days_observed = len(observed)
        zones = Counter(row["planning_zone"] for row in observed)
        statuses = Counter(row["on_hand_status"] for row in observed)
        rows.append(
            DdmrpPerformanceRow(
                item_code=item_code,
                site_code=site_code,
                days_observed=days_observed,
                red_pct=_pct(zones["red"], days_observed),
                yellow_pct=_pct(zones["yellow"], days_observed),
                green_pct=_pct(zones["green"], days_observed),
                over_pct=_pct(zones["over"], days_observed),
                stocked_out_pct=_pct(statuses["stocked_out"], days_observed),
                critical_pct=_pct(statuses["critical"], days_observed),
            )
        )
    rows.sort(key=lambda row: (-row.red_pct, row.item_code, row.site_code))
    return {
        "from": window_start,
        "to": latest,
        "rows": [row.model_dump() for row in rows],
    }


# ---------------------------------------------------------------------------
# GET /candidates: the A and B class item-sites without a designation
# ---------------------------------------------------------------------------


class DdmrpCandidateRow(BaseModel):
    """One DDMRP buffer candidate item-site.

    An "A" or "B" class item-site of the as-of ABC pool that has no
    stored row in ``ddmrp_buffers``.  ``demand_class`` is null when
    the pair has no demand statistics, and ``dlt_days`` with its
    ``dlt_source`` are null when neither lead time source exists —
    the same nullability as the zones route's shared statistics,
    which the qualification rules keep out of the answered rows.
    """

    item_code: str
    site_code: str
    abc_class: str
    annual_value: float
    demand_class: str | None
    adu: float
    dlt_days: float | None
    dlt_source: str | None
    reason: str


class DdmrpCandidateResponse(BaseModel):
    """The DDMRP buffer candidates, as of one date.

    The rows are ordered by ``annual_value`` descending, then
    ``item_code``, then ``site_code``.
    """

    as_of: date
    rows: list[DdmrpCandidateRow]


@router.get("/candidates", response_model=DdmrpCandidateResponse)
def list_ddmrp_candidates() -> dict:
    """The DDMRP buffer candidates: the "A" and "B" class item-sites
    without a stored designation.

    Open to any authenticated user (the router is mounted behind the
    app's authentication gate).  As of the demand statistics' default
    as-of date (``app.stats.demand.default_as_of``) — the date
    ``GET /api/ddmrp/zones`` answers as of — one row per item-site of
    ``app.recommend.abc.abc_for_all`` whose ``abc_class`` is "A" or
    "B" and which has no stored row in ``ddmrp_buffers``: the whole
    ABC pool, item-sites not yet designated as decoupling points
    included.  For each pool item-site the ``adu`` (the item-site's
    average daily usage over the ``ddmrp.adu_window_days`` window),
    the ``dlt_days`` with its ``dlt_source`` and the ``demand_class``
    are the very same shared computation the zones route uses
    (``_ddmrp_item_site_stats`` over one ``_ddmrp_stat_context``
    read).  An item-site is a candidate only when its
    ``demand_class`` is not "no_demand", its ``adu`` is above 0 and
    its ``dlt_days`` is at least ``MIN_CANDIDATE_DLT_DAYS`` (7: a
    buffer shorter than a week of lead time rarely pays for itself).
    Each row carries the pair, its ``abc_class`` and
    ``annual_value``, its ``demand_class``, ``adu``, ``dlt_days`` and
    ``dlt_source`` and a ``reason`` reading "<class> class, <whole
    days>-day lead time, <demand_class> demand".  The rows are
    ordered by ``annual_value`` descending, then ``item_code``, then
    ``site_code``.  Nothing is written.
    """
    context = _ddmrp_stat_context()
    as_of = context["as_of"]
    designated = {
        (buffer["item_code"], buffer["site_code"]) for buffer in _repo.list_all()
    }

    rows = []
    for pool_row in abc_for_all(as_of):
        if pool_row["abc_class"] not in ("A", "B"):
            continue
        item_code = pool_row["item_code"]
        site_code = pool_row["site_code"]
        if (item_code, site_code) in designated:
            continue
        stats = _ddmrp_item_site_stats(context, item_code, site_code)
        demand_class = stats["demand_class"]
        dlt_days = stats["dlt_days"]
        if demand_class == "no_demand" or stats["adu"] <= 0.0:
            continue
        if dlt_days is None or dlt_days < MIN_CANDIDATE_DLT_DAYS:
            continue
        rows.append(
            {
                "item_code": item_code,
                "site_code": site_code,
                "abc_class": pool_row["abc_class"],
                "annual_value": pool_row["annual_value"],
                "demand_class": demand_class,
                "adu": stats["adu"],
                "dlt_days": dlt_days,
                "dlt_source": stats["dlt_source"],
                "reason": (
                    f"{pool_row['abc_class']} class, "
                    f"{int(dlt_days)}-day lead time, "
                    f"{demand_class} demand"
                ),
            }
        )

    rows.sort(
        key=lambda row: (-row["annual_value"], row["item_code"], row["site_code"])
    )
    return {"as_of": as_of, "rows": rows}


# ---------------------------------------------------------------------------
# /adjustments: the DDMRP adjustment factors
# ---------------------------------------------------------------------------


class DdmrpAdjustment(BaseModel):
    """One stored DDMRP adjustment factor.

    ``item_code`` and ``site_code`` are both null for an adjustment
    that applies to every stored buffer, and both set for one of
    them.  ``factor`` (a decimal strictly above 0 and at most 10)
    scales the average daily usage between ``start_on`` and ``end_on``
    (inclusive).
    """

    id: str
    item_code: str | None
    site_code: str | None
    start_on: date
    end_on: date
    factor: float = Field(json_schema_extra=_FACTOR_DECIMAL_SCHEMA)
    note: str | None
    created_by: str
    created_by_name: str | None
    created_at: datetime


class DdmrpAdjustmentBody(BaseModel):
    """The body of POST /api/ddmrp/adjustments.

    ``item_code`` and ``site_code`` are both absent (an adjustment
    for every stored buffer) or both given (one item-site).  Every
    business rule is a model validation, so a rejected body answers
    422 in FastAPI's HTTPValidationError envelope, exactly like a
    malformed one.
    """

    item_code: str | None = Field(default=None, max_length=64)
    site_code: str | None = Field(default=None, max_length=64)
    start_on: date
    end_on: date
    factor: float = Field(
        gt=0,
        le=10,
        description=(
            "The adjustment factor, a decimal number strictly greater "
            "than 0 and at most 10."
        ),
        json_schema_extra=_FACTOR_DECIMAL_SCHEMA,
    )
    note: str | None = None

    @field_validator("factor", mode="before")
    @classmethod
    def _factor_is_a_decimal_value(cls, value):
        """The factor is a decimal value: a JSON number.

        A string, a boolean or any other non-numeric value answers
        422; the gt/le bounds apply to the number itself.
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("factor must be a decimal number")
        return value

    @model_validator(mode="after")
    def _check_business_rules(self) -> Self:
        if (self.item_code is None) != (self.site_code is None):
            raise ValueError(
                "item_code and site_code must both be given or both be absent"
            )
        for value in (self.item_code, self.site_code):
            if value == "":
                raise ValueError("item_code and site_code must not be empty")
        for value in (self.item_code, self.site_code, self.note):
            if value is not None and _NUL in value:
                raise ValueError("must not contain NUL bytes")
        if self.end_on < self.start_on:
            raise ValueError("end_on must not be before start_on")
        return self


def _overlap_conflict(conflict: dict) -> HTTPException:
    """The 409 of an overlapping same-scope window.

    Machine-resolvable: a stable error structure naming the
    conflicting adjustment, without any database constraint text.
    """
    return HTTPException(
        status_code=409,
        detail={
            "code": "ddmrp_adjustment_overlap",
            "message": (
                "the window overlaps a stored adjustment of the same "
                "scope; start_on and end_on are inclusive, so a shared "
                "boundary date overlaps"
            ),
            "scope": (
                "item_site" if conflict["item_code"] is not None else "all_buffers"
            ),
            "item_code": conflict["item_code"],
            "site_code": conflict["site_code"],
            "overlapping_id": conflict["id"],
            "overlapping_start_on": conflict["start_on"].isoformat(),
            "overlapping_end_on": conflict["end_on"].isoformat(),
        },
    )


@router.get("/adjustments", response_model=list[DdmrpAdjustment])
def list_ddmrp_adjustments() -> list[dict]:
    """List every stored DDMRP adjustment factor.

    Open to any authenticated user (the router is mounted behind the
    app's authentication gate).  The rows are ordered by start_on,
    with the all-buffer rows (both codes null) first on equal dates.
    Nothing is written.
    """
    return _adjustments_repo.list_all()


@router.post(
    "/adjustments",
    status_code=201,
    response_model=DdmrpAdjustment,
    responses={
        404: {"description": "The item-site pair is not in item_sites."},
        409: {
            "description": (
                "The window overlaps a stored adjustment of the same " "scope."
            )
        },
    },
)
def create_ddmrp_adjustment(
    body: DdmrpAdjustmentBody,
    user: AuthUser = Depends(require_role("admin")),
) -> dict:
    """Store one DDMRP adjustment factor.

    Admin only.  With both codes given the pair must be a row of
    item_sites (a pair that is not in item_sites answers 404); with
    both absent the factor applies to every stored buffer.  The
    window [start_on, end_on] is inclusive and may not overlap a
    stored adjustment of the same scope — the same item-site pair, or
    both all-buffer (a shared boundary date overlaps) — which answers
    409 with a stable machine-readable detail; the overlap check and
    the store hold one transaction, serialized per scope, so a
    concurrent same-scope window can never slip through the check.
    The factor is a decimal strictly above 0 and at most 10; a
    factor outside (0, 10], end_on before start_on, one code without
    the other, an empty code or a NUL byte in any field answers 422.
    The stored row records created_by as the token subject and
    created_by_name its display name.  Answers 201 with the stored
    row.
    """
    _require_storable_designated_by(user)
    if body.item_code is not None:
        _require_item_site(body.item_code, body.site_code)
    try:
        return _adjustments_repo.insert_if_disjoint(
            id=str(uuid4()),
            item_code=body.item_code,
            site_code=body.site_code,
            start_on=body.start_on,
            end_on=body.end_on,
            factor=float(body.factor),
            note=body.note,
            created_by=user.sub,
            created_by_name=display_name(user),
        )
    except OverlappingAdjustmentError as conflict:
        raise _overlap_conflict(conflict.conflict)


@router.delete(
    "/adjustments/{adjustment_id}",
    status_code=204,
    dependencies=[Depends(require_role("admin"))],
    responses={404: {"description": "There is no such adjustment."}},
)
def delete_ddmrp_adjustment(adjustment_id: str) -> None:
    """Remove the stored DDMRP adjustment factor with id
    ``adjustment_id``.

    Admin only.  Answers 204 when a stored adjustment was removed and
    404 when there is no such adjustment.
    """
    if not _adjustments_repo.delete(adjustment_id):
        raise HTTPException(status_code=404, detail="no DDMRP adjustment with that id")
