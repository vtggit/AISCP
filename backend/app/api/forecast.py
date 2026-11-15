"""Read-only forecast API per item-site (issue #309).

``GET /api/forecast`` lists one row per item-site that has a forecast —
item-sites whose demand class is ``no_demand`` are left out — ordered by
``total_forecast`` descending, then ``item_code`` and ``site_code``, and
``GET /api/forecast/by-key`` fetches one item-site's row plus the
``history`` buckets it was built from. Both serve any signed-in user.
The list is built by ``app.forecast.listing.forecast_rows`` on a fixed
number of queries — the demand statistics from ``demand_stats_for_all``,
every item-site's 30-day history from one ``history_for_all``
query and the counted overrides and counted signals of every row
from one ``effective_for_all`` query each — whatever the number
of item-sites.
The buckets, the demand class and the default as-of date all come from
``app.stats.demand`` (through ``app.forecast.history``), so the forecast
rows share the demand stats' 30-day bucket alignment and as-of date.

``POST /api/forecast/snapshots`` (admin only) captures — or fetches,
when the date already has a snapshot — a forecast snapshot: one row
per item-site per forecast bucket of that date, stored in
``forecast_snapshots``.  ``GET /api/forecast/snapshots`` lists the
stored snapshots, newest as-of date first, for any signed-in user.

``GET /api/forecast/accuracy`` reports the realised accuracy of the
stored snapshots for any signed-in user: each completed snapshot
bucket (``bucket_end`` on or before the as-of date — the shared
default as-of date when the parameter is absent) is compared with
the actual demand of its range, and the measures come back overall,
per horizon and per item-site.

``GET /api/forecast/accuracy/signals`` reports the realised value of
the stored demand signals, for any signed-in user and with the same
``as_of`` and ``origin`` parameters: the same completed-bucket
comparison restricted to the buckets with a stored
``signal_quantity`` above 0 (a null or 0 signal is left out), with
``before_total`` (each bucket's effective quantity minus its signal
quantity — the forecast before the signals), ``after_total`` (its
effective quantity), ``signal_total``, ``before_wape`` /
``after_wape`` and ``value_added`` (positive when the signals moved
the forecast nearer the actuals) overall and per item-site, ordered
by ``signal_total`` descending.

``POST /api/forecast/overrides`` (admin only) stores a planner
override — the planner's quantity for one forecast period of one
item-site — and ``POST /api/forecast/overrides/withdrawals`` (admin
only) withdraws the period's current override; both answer 201 with
the stored row.  ``GET /api/forecast/overrides`` lists the stored
rows, newest first, for any signed-in user.  The two forecast read
routes serve the effective forecast beside the statistical one
[rule: OVR-3] [rule: OVR-4]: every forecast bucket carries
``effective_quantity`` and ``override_days`` next to the statistical
``quantity``, which no override ever changes, and every row carries
``total_effective_forecast`` and ``overridden_buckets``.  The
stored demand signals sit beside the overrides: every bucket
also carries ``signal_quantity`` — each day no override covers
takes, for each counted signal (a stored promotion, deal or
event no withdrawal row names) whose window covers that day, the
signal's quantity over the days of its window — and
``effective_quantity`` is the effective quantity plus
``signal_quantity``, while every row carries
``total_signal_quantity``, the sum of its buckets'
``signal_quantity``; ``quantity`` and ``override_days`` no
counted signal ever changes.  The overrides are applied by their
stored dates whatever ``as_of`` is asked for [rule: OVR-11], and
the method, the backtest, the arithmetic, ``total_forecast`` and
the order of the list stay the statistical forecast's.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, model_validator

from app.auth.authorization import require_role
from app.auth.models import AuthUser
from app.forecast.accuracy import realised_accuracy, signal_accuracy
from app.forecast.history import history_for_pair
from app.forecast.listing import build_forecast_row, forecast_rows
from app.forecast.overrides import (
    create_override,
    effective_for_all,
    list_overrides,
    withdraw_override,
)
from app.forecast.snapshots import capture_snapshot, list_snapshots
from app.stats.demand import demand_stats_for_pair

router = APIRouter(prefix="/api/forecast", tags=["forecast"])

_NUL = chr(0)


def _reject_nul_bytes(*values: str | None) -> None:
    for value in values:
        if value is not None and _NUL in value:
            raise HTTPException(
                status_code=422,
                detail="forecast filters must not contain NUL bytes",
            )


class ForecastBucket(BaseModel):
    """One 30-day bucket: its boundaries, its statistical quantity,
    and the effective forecast beside it.

    ``quantity`` is the statistical forecast, never changed by any
    override [rule: OVR-4].  ``effective_quantity`` is the sum of the
    bucket's day shares — one day's share of the counted override
    covering each day, else one day's share of the statistical
    quantity — and ``override_days`` how many of the bucket's days an
    override covers, from 0 to the bucket's length.
    """

    bucket_start: date
    bucket_end: date
    quantity: float
    effective_quantity: float
    override_days: int
    signal_quantity: float


class HistoryBucket(BaseModel):
    """One of the 30-day history buckets a forecast was built from.

    The history carries its three fields only — ``bucket_start``,
    ``bucket_end`` and ``quantity``; the effective forecast applies
    to the forecast buckets, never to the history.
    """

    bucket_start: date
    bucket_end: date
    quantity: float


class ForecastBacktest(BaseModel):
    """The winning method's one-bucket-ahead backtest score."""

    mae: float
    baseline_mae: float
    wape: float | None
    bias: float | None


class ForecastRow(BaseModel):
    """One item-site's forecast: the method, the 12 buckets, the backtest.

    ``total_forecast`` and ``forecast`` are the statistical
    forecast's, unchanged by any override [rule: OVR-4]; beside them,
    ``total_effective_forecast`` is the sum of the buckets'
    ``effective_quantity`` and ``overridden_buckets`` how many of the
    buckets have at least one overridden day.
    """

    item_code: str
    site_code: str
    as_of: date
    demand_class: str
    method: str
    params: dict
    history_buckets: int
    total_forecast: float
    forecast: list[ForecastBucket]
    total_effective_forecast: float
    total_signal_quantity: float
    overridden_buckets: int
    backtest: ForecastBacktest | None
    arithmetic: str


class ForecastRowDetail(ForecastRow):
    """A forecast row plus the history buckets it was built from."""

    history: list[HistoryBucket]


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="forecast not found"
    )


@router.get("", response_model=list[ForecastRow])
def list_forecasts(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, le=1_000_000_000),
    demand_class: (
        Literal["no_demand", "smooth", "erratic", "intermittent", "lumpy"] | None
    ) = Query(default=None),
    as_of: date | None = Query(default=None),
) -> list[dict]:
    """List one row per item-site that has a forecast, for any signed-in user.

    Ordered by ``total_forecast`` descending, then ``item_code`` and
    ``site_code``; item-sites whose demand class is ``no_demand`` are
    left out. ``demand_class`` filters the list, ``as_of`` sets the date
    (the shared ``app.stats.demand`` default when absent), and
    ``limit`` / ``offset`` page it.
    """
    _reject_nul_bytes(demand_class)
    # The list on a fixed query budget, whatever the number of
    # item-sites: the effective forecast beside the statistical one
    # [rule: OVR-3] [rule: OVR-4].
    rows = forecast_rows(as_of=as_of, demand_class=demand_class)
    return rows[offset : offset + limit]


@router.get("/by-key", response_model=ForecastRowDetail)
def get_forecast_by_key(
    item_code: str = Query(..., max_length=64),
    site_code: str = Query(..., max_length=64),
    as_of: date | None = Query(default=None),
) -> dict:
    """One item-site's forecast row plus the ``history`` it was built from.

    Answers 404 when the item-site has no demand events, or when its
    demand class is ``no_demand``.
    """
    _reject_nul_bytes(item_code, site_code)
    stats = demand_stats_for_pair(item_code, site_code, as_of)
    if stats is None:
        raise _not_found()
    history = history_for_pair(stats["item_code"], stats["site_code"], stats["as_of"])
    row = build_forecast_row(
        stats["item_code"],
        stats["site_code"],
        stats["demand_class"],
        stats["as_of"],
        history,
    )
    if row is None:
        raise _not_found()
    # The same effective forecast as the list, one query for this
    # item-site [rule: OVR-3] [rule: OVR-4].
    row = effective_for_all([row])[0]
    row["history"] = history
    return row


class SnapshotCaptureBody(BaseModel):
    """The optional body of POST /api/forecast/snapshots.

    ``as_of`` selects the date to snapshot; absent (or null) the
    shared default as-of date is used. A date later than today is a
    business 422 in FastAPI's validation envelope, exactly like a
    malformed one.
    """

    as_of: date | None = None

    @model_validator(mode="after")
    def _check_as_of(self) -> Self:
        if self.as_of is not None and self.as_of > date.today():
            raise ValueError("as_of must not be later than today")
        return self


class SnapshotResult(BaseModel):
    """The result of a snapshot capture.

    ``created`` is true when rows were stored; for a date that
    already has a snapshot it is false and ``origin``, ``item_sites``
    and ``rows`` describe the stored snapshot.
    """

    as_of: date
    origin: Literal["live", "backfill"]
    created: bool
    item_sites: int
    rows: int


class SnapshotSummary(BaseModel):
    """One stored forecast snapshot: the snapshot-level fields only."""

    as_of: date
    origin: Literal["live", "backfill"]
    item_sites: int
    rows: int
    created_at: datetime


@router.post("/snapshots", response_model=SnapshotResult)
def create_forecast_snapshot(
    body: SnapshotCaptureBody | None = None,
    user: AuthUser = Depends(require_role("admin")),
) -> dict:
    """Capture (or fetch) the forecast snapshot for one as-of date.

    Admin only. The JSON body is optional and may carry ``as_of`` (a
    date); without it the shared default as-of date is captured with
    origin ``live``, and an ``as_of`` given stores origin
    ``backfill``. When the date already has a snapshot nothing is
    stored and the stored snapshot's fields come back with ``created``
    false. A date later than today, or a malformed one, answers 422
    in FastAPI's validation envelope.
    """
    return capture_snapshot(body.as_of if body is not None else None)


@router.get("/snapshots", response_model=list[SnapshotSummary])
def list_forecast_snapshots(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=1_000_000_000),
) -> list[dict]:
    """List the stored forecast snapshots, newest as-of date first.

    Any signed-in user. Each entry carries the snapshot-level fields
    ``as_of``, ``origin``, ``item_sites``, ``rows`` and
    ``created_at``; ``limit`` (default 50, 1 to 500) and ``offset``
    page the list.
    """
    return list_snapshots(limit, offset)


class AccuracyMeasures(BaseModel):
    """The measures over one set of compared snapshot buckets.

    ``forecast_total``, ``wape`` and ``bias`` stay the statistical
    forecast's [rule: OVR-4]; ``effective_total``, ``effective_wape``
    and ``effective_bias`` are the same with each bucket's effective
    quantity — a null stored ``effective_quantity`` reads as its
    ``quantity`` and as not overridden, and the stored
    ``signal_quantity`` (a null one reads as 0) stays in it — in
    place of the statistical one, ``overridden_buckets`` how many
    compared buckets had at least one overridden day, and
    ``value_added`` ``wape`` minus the error of the effective
    forecast before the signals — each bucket's effective quantity
    minus its signal quantity — over the actuals (null when
    ``actual_total`` is 0), positive when the overrides, measured
    alone, moved the forecast nearer the actuals.
    """

    forecast_total: float
    actual_total: float
    wape: float | None
    bias: float | None
    effective_total: float
    effective_wape: float | None
    effective_bias: float | None
    overridden_buckets: int
    value_added: float | None


class AccuracyHorizon(BaseModel):
    """The measures for one horizon (``bucket_index``) plus its count."""

    bucket_index: int
    compared_buckets: int
    forecast_total: float
    actual_total: float
    wape: float | None
    bias: float | None
    effective_total: float
    effective_wape: float | None
    effective_bias: float | None
    overridden_buckets: int
    value_added: float | None


class AccuracyItemSite(BaseModel):
    """One item-site's measures, with its most recent compared method."""

    item_code: str
    site_code: str
    method: str
    compared_buckets: int
    forecast_total: float
    actual_total: float
    wape: float | None
    bias: float | None
    effective_total: float
    effective_wape: float | None
    effective_bias: float | None
    overridden_buckets: int
    value_added: float | None


class AccuracyResult(BaseModel):
    """The realised accuracy of the stored forecast snapshots.

    ``overall`` covers everything compared, ``by_horizon`` carries one
    entry per ``bucket_index`` present, in order, and ``item_sites``
    one entry per item-site (the ``method`` from its most recent
    compared snapshot), ordered by ``actual_total`` descending, then
    ``item_code`` and ``site_code``.  A snapshot bucket is compared
    only when its ``bucket_end`` is on or before ``as_of``; with no
    ``as_of`` the shared default as-of date is used, so demand after
    the latest demand date is unknown, not zero.
    """

    as_of: date
    compared_buckets: int
    overall: AccuracyMeasures
    by_horizon: list[AccuracyHorizon]
    item_sites: list[AccuracyItemSite]


@router.get("/accuracy", response_model=AccuracyResult)
def get_forecast_accuracy(
    as_of: date | None = Query(default=None),
    origin: Literal["live", "backfill"] | None = Query(default=None),
    bucket_index: int | None = Query(default=None, ge=1, le=12),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, le=1_000_000_000),
) -> dict:
    """The realised accuracy of the stored forecast snapshots, any signed-in user.

    Each completed snapshot bucket (``bucket_end`` on or before
    ``as_of`` — the shared default as-of date when absent, because
    demand after the latest demand date is unknown, not zero) is
    compared with the actual demand of its range.  ``origin``
    (``live`` or ``backfill``) keeps only snapshots of that origin
    and ``bucket_index`` (1 to 12) only that horizon.  ``limit``
    (default 100, 1 to 1000) and ``offset`` page ``item_sites`` only;
    ``overall`` and ``by_horizon`` always cover everything compared.
    A malformed ``as_of``, ``origin`` or ``bucket_index`` answers 422
    in FastAPI's validation envelope.
    """
    result = realised_accuracy(as_of=as_of, origin=origin, bucket_index=bucket_index)
    result["item_sites"] = result["item_sites"][offset : offset + limit]
    return result


class SignalAccuracyItemSite(BaseModel):
    """One item-site's signal measures.

    Only the compared buckets with a stored ``signal_quantity``
    above 0 are summed in: ``before_total`` is each bucket's
    effective quantity minus its signal quantity (the forecast
    before the signals), ``after_total`` its effective quantity,
    ``signal_total`` its signal quantity, ``before_wape`` and
    ``after_wape`` the sums of their absolute differences from the
    actuals over ``actual_total`` (null when it is 0), and
    ``value_added`` ``before_wape`` minus ``after_wape`` — positive
    when the signals moved the forecast nearer the actuals.
    """

    item_code: str
    site_code: str
    compared_buckets: int
    actual_total: float
    before_total: float
    after_total: float
    signal_total: float
    before_wape: float | None
    after_wape: float | None
    value_added: float | None


class SignalAccuracyResult(BaseModel):
    """The realised value of the stored demand signals.

    Only completed snapshot buckets (``bucket_end`` on or before
    ``as_of`` — the shared default as-of date when absent) with a
    stored ``signal_quantity`` above 0 are compared with the actual
    demand of their range, the same way ``GET /api/forecast/accuracy``
    compares; a bucket whose signal is null or 0 is left out.
    ``item_sites`` carries one entry per item-site with the same
    measures, ordered by ``signal_total`` descending, then
    ``item_code`` and ``site_code``.
    """

    as_of: date
    compared_buckets: int
    actual_total: float
    before_total: float
    after_total: float
    signal_total: float
    before_wape: float | None
    after_wape: float | None
    value_added: float | None
    item_sites: list[SignalAccuracyItemSite]


@router.get("/accuracy/signals", response_model=SignalAccuracyResult)
def get_forecast_accuracy_signals(
    as_of: date | None = Query(default=None),
    origin: Literal["live", "backfill"] | None = Query(default=None),
) -> dict:
    """The realised value of the stored demand signals, any signed-in user.

    Only completed snapshot buckets with a stored
    ``signal_quantity`` above 0 (``bucket_end`` on or before
    ``as_of`` — the shared default as-of date when absent, because
    demand after the latest demand date is unknown, not zero) are
    compared with the actual demand of their range; a bucket whose
    signal is null or 0 is left out.  ``origin`` (``live`` or
    ``backfill``) keeps only snapshots of that origin.  ``item_sites``
    is ordered by ``signal_total`` descending, then ``item_code``
    and ``site_code``.  A malformed ``as_of`` or ``origin`` answers
    422 in FastAPI's validation envelope.
    """
    return signal_accuracy(as_of=as_of, origin=origin)


class OverrideBody(BaseModel):
    """The body of POST /api/forecast/overrides.

    ``period_start`` must be the first day of one of the 12 forecast
    periods at the shared default as-of date; the stored
    ``period_end`` is that period's last day.  Every business
    refusal (a blank or over-long reason, a negative or non-finite
    quantity, a period outside the current horizon, an item-site
    with no statistical forecast) answers 422 with a sentence in
    ``detail``, and nothing is stored.
    """

    item_code: str
    site_code: str
    period_start: date
    quantity: float
    reason: str


class WithdrawalBody(BaseModel):
    """The body of POST /api/forecast/overrides/withdrawals.

    Withdraws the current override of the period: a new
    ``withdrawal`` row with the stored period's dates.  A blank or
    over-long reason answers 422; a withdrawal with nothing to
    withdraw, or for a completed period, answers 409.
    """

    item_code: str
    site_code: str
    period_start: date
    reason: str


class OverrideRow(BaseModel):
    """One stored forecast override or withdrawal, plus ``applies``.

    ``applies`` is true only for an ``override`` row that is the
    current row of its period (the highest ``id`` for that
    item-site and ``period_start``).  ``created_by_name`` is the
    caller's display name as the decision tables store it, through
    the shared ``app.auth.display_name.display_name`` helper — the
    token's username, else its email, else its subject — never null
    on a row the API stored; it stays nullable only for rows written
    out of band.
    """

    id: int
    item_code: str
    site_code: str
    period_start: date
    period_end: date
    kind: Literal["override", "withdrawal"]
    quantity: float | None
    reason: str
    created_by: str
    created_by_name: str | None
    created_at: datetime
    applies: bool


@router.post(
    "/overrides",
    status_code=status.HTTP_201_CREATED,
    response_model=OverrideRow,
)
def create_forecast_override(
    body: OverrideBody,
    user: AuthUser = Depends(require_role("admin")),
) -> dict:
    """Store a planner override for one forecast period (admin only).

    Admin only [rule: OVR-10].  Stores one ``override`` row —
    quantity, reason (trimmed), the period's two dates, the caller's
    subject and display name — and answers 201 with the stored row.
    ``period_start`` must be the first day of one of the 12 forecast
    periods at the shared default as-of date.  A blank or over-long
    reason, a negative or non-finite quantity, a period outside the
    current horizon, or an item-site with no statistical forecast
    answers 422 with a sentence in ``detail`` and nothing is stored.
    A new override for the same item-site and period is a new row:
    overrides are append-only and the highest ``id`` applies.
    """
    return create_override(
        body.item_code,
        body.site_code,
        body.period_start,
        body.quantity,
        body.reason,
        user,
    )


@router.post(
    "/overrides/withdrawals",
    status_code=status.HTTP_201_CREATED,
    response_model=OverrideRow,
)
def withdraw_forecast_override(
    body: WithdrawalBody,
    user: AuthUser = Depends(require_role("admin")),
) -> dict:
    """Withdraw the current override of one forecast period (admin only).

    Admin only [rule: OVR-10].  Stores one ``withdrawal`` row with
    the stored period's dates and answers 201 with the stored row;
    the earlier rows stay in the history.  A blank or over-long
    reason answers 422 with nothing stored.  A withdrawal with
    nothing to withdraw — no stored row, or the current row of the
    period is a withdrawal — and a withdrawal for a completed period
    (its ``period_end`` on or before the shared default as-of date)
    answer 409 with the stored rows unchanged.
    """
    return withdraw_override(
        body.item_code,
        body.site_code,
        body.period_start,
        body.reason,
        user,
    )


@router.get("/overrides", response_model=list[OverrideRow])
def list_forecast_overrides(
    item_code: str | None = Query(default=None),
    site_code: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, le=1_000_000_000),
) -> list[dict]:
    """List the stored forecast overrides, newest first, any signed-in user.

    Any signed-in user may read overrides [rule: OVR-10].  ``item_code``
    and ``site_code`` select one item-site when both are given —
    giving exactly one of the two answers 422 with a sentence in
    ``detail`` and nothing is returned; with neither they select
    every stored row.  The rows come back newest first (highest
    ``id`` first); each carries every column plus ``applies`` — true
    only for an ``override`` row that is the current row of its
    period.  ``limit`` (default 100, 1 to 1000) and ``offset`` page
    the list.
    """
    return list_overrides(
        item_code=item_code, site_code=site_code, limit=limit, offset=offset
    )
