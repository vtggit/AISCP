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

``GET /api/forecast/accuracy/corrections`` reports the realised
value of the stored corrections, for any signed-in user and with
the same ``as_of`` and ``origin`` parameters: the same
completed-bucket comparison restricted to the buckets with a
stored ``proposed_quantity`` (a null proposal is left out), with
``before_total`` (each bucket's effective quantity — the
forecast before the corrections), ``after_total`` (its proposed
quantity), ``before_wape`` / ``after_wape`` (each the absolute
difference of the before and the after figure from the actuals,
over the actuals) and ``before_bias`` / ``after_bias`` (each the
before or the after total minus the actuals, over the actuals),
all null when the actuals are 0, and ``value_added``
(``before_wape`` minus ``after_wape``, positive when the
corrections would have moved the forecast nearer the actuals)
overall and per item-site, ordered by ``actual_total``
descending.

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

Both read routes take ``include_correction``, a boolean query
parameter, false by default.  While it is false each answers
exactly as without the parameter, key for key: no row carries a
``correction`` key and neither of the two correction reads — the
stored system settings and the bias exception's measure — runs.
While it is true and at least one row is answered, those two reads
run once each, before any correction is worked out: the stored
system settings (the one read the exceptions list makes) and, on
them, the bias exception's measure (the very read the exceptions
list makes for its forecast bias rows, over the window its setting
names, with no as-of of its own).  Every answered row then carries
``correction``, a ``ForecastCorrection`` or null: the item-site's
measured bias (the exception's detected date, the measure's
compared buckets, totals and bias, and the factor the measured
actual total over the effective one) and one
``ForecastCorrectionPeriod`` — a proposed quantity — per forecast
bucket without an override day and without a demand signal.  Null
covers an item-site whose bias does not persist as a forecast-bias
exception, one the measure has no entry or no finite totals above 0
for, and one with no such bucket left.  With no row answered
neither read runs, a read that raises never fails the answer
(every row's ``correction`` is then null, still 200 with the rows)
and the routes write nothing.
"""

from __future__ import annotations

import contextlib
import math
from datetime import date, datetime
from typing import Literal, Self

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, PrivateAttr, model_serializer, model_validator

from app.api.exceptions import (
    FORECAST_BIAS_MIN_PERIODS_KEY,
    FORECAST_BIAS_THRESHOLD_KEY,
    _forecast_bias_accuracy,
    _forecast_rows,
    _stored_system_settings,
)
from app.auth.authorization import require_role
from app.auth.models import AuthUser
from app.db.connection import get_cursor
from app.ddmrp.zones import setting_value
from app.forecast.accuracy import (
    correction_accuracy,
    realised_accuracy,
    signal_accuracy,
)
from app.forecast.history import history_for_pair
from app.forecast.listing import build_forecast_row, forecast_rows
from app.forecast.overrides import (
    _CURRENT_OVERRIDE_SELECT,
    create_override,
    effective_for_all,
    list_overrides,
    withdraw_override,
)
from app.forecast.snapshots import capture_snapshot, list_snapshots
from app.repositories.forecast_override_proposals_postgres_repository import (
    ForecastOverrideProposalPostgresRepository,
)
from app.stats.demand import demand_stats_for_pair

router = APIRouter(prefix="/api/forecast", tags=["forecast"])

# The corrections proposed with the overrides stored through the
# override route below (issue #1365), the way the excess decision
# proposals ride on their module-level repository.
_override_proposal_repo = ForecastOverrideProposalPostgresRepository()

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


class ForecastCorrectionPeriod(BaseModel):
    """One forecast bucket the correction proposes a quantity for.

    ``period_start`` and ``period_end`` are the bucket's own
    boundaries, ``quantity`` its statistical forecast and
    ``proposed_quantity`` the correction's proposal for it —
    ``max(0, round(quantity x factor, 2))``, the factor of its
    row's ``ForecastCorrection``.
    """

    period_start: date
    period_end: date
    quantity: float
    proposed_quantity: float


class ForecastCorrection(BaseModel):
    """The correction proposed for one forecast row.

    ``measured_on`` is the forecast-bias exception's detected date
    (the measure's as-of), ``compared_buckets`` how many of the
    item-site's latest forecast periods the measure compared,
    ``effective_total`` and ``actual_total`` the measure's totals
    over them, ``effective_bias`` the measure's bias over them and
    ``factor`` the actual total over the effective total,
    unrounded — the factor every period is proposed at.  ``periods``
    carries one ``ForecastCorrectionPeriod`` per forecast bucket
    without an override day and without a demand signal, in the
    forecast's order, and ``reason`` the one line of text for the
    proposal.
    """

    measured_on: date
    compared_buckets: int
    effective_total: float
    actual_total: float
    effective_bias: float
    factor: float
    periods: list[ForecastCorrectionPeriod]
    reason: str


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
    correction: ForecastCorrection | None = None
    # The route's include_correction parameter for this row: read
    # off the answered row's dict (a key the validation ignores) by
    # the validator below and kept as private state, so it is part
    # of neither the model's fields nor its schema, let alone the
    # answer.
    _include_correction: bool = PrivateAttr(default=False)

    @model_validator(mode="wrap")
    def _remember_include_correction(cls, value, handler):
        """Remember the route's include_correction parameter from
        the answered row's dict, so the serializer can drop the
        correction key while it is false."""
        if isinstance(value, dict):
            include_correction = value.get("include_correction", False)
        else:
            include_correction = getattr(value, "_include_correction", False)
        row = handler(value)
        row._include_correction = bool(include_correction)
        return row

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        """The row's fields, without ``correction`` while the
        route's ``include_correction`` parameter is false (the key
        stays out of the answer, exactly as before the correction
        existed)."""
        data = handler(self)
        if not self._include_correction:
            data.pop("correction", None)
        return data


class ForecastRowDetail(ForecastRow):
    """A forecast row plus the history buckets it was built from."""

    history: list[HistoryBucket]


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="forecast not found"
    )


def _positive_finite(value: object) -> bool:
    """Whether the value is a finite number above 0 (a bool is not
    a number here, and neither is a nan or an infinity)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0


def _percent(value: float) -> str:
    """The factor's percent as the reason spells it: 55.6, 333.3
    and 200 — one decimal place, trailing zeros dropped."""
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _measure_entries(measure: object) -> dict[tuple[object, object], dict]:
    """The measure's item-site entries by (item_code, site_code).

    A measure with no item_sites list, or entries that are not
    dicts, contributes nothing — never a failed answer."""
    entries: dict[tuple[object, object], dict] = {}
    if isinstance(measure, dict):
        item_sites = measure.get("item_sites")
        if isinstance(item_sites, list):
            for entry in item_sites:
                if isinstance(entry, dict):
                    entries[(entry.get("item_code"), entry.get("site_code"))] = entry
    return entries


def _row_correction(
    row: dict,
    exception_by_pair: dict[tuple[object, object], object],
    entry_by_pair: dict[tuple[object, object], dict],
) -> dict | None:
    """One answered row's correction, or null.

    Only the item-sites the exceptions list's forecast-bias rule
    answers a row for carry one, and its figures come from the
    measure's item-site entry with the same codes: ``factor`` the
    entry's actual total over its effective total — null when either
    is not a finite number above 0, there is nothing to scale — and
    one period per forecast bucket with no override day and no
    demand signal, in the forecast's order, its proposed quantity
    ``max(0, round(quantity x factor, 2))``; null again when no
    bucket is left.  ``reason`` is the exception's own detail, first
    letter upper-cased, and the percent of its forecast each such
    period is proposed at.  The rule reads no acknowledgement or
    snooze of the exception, and writes nothing.
    """
    key = (row.get("item_code"), row.get("site_code"))
    exception_row = exception_by_pair.get(key)
    if exception_row is None:
        return None
    entry = entry_by_pair.get(key)
    if entry is None:
        return None
    effective_total = entry.get("effective_total")
    actual_total = entry.get("actual_total")
    if not _positive_finite(effective_total) or not _positive_finite(actual_total):
        return None
    factor = float(actual_total) / float(effective_total)
    if not math.isfinite(factor):
        return None
    periods: list[dict] = []
    forecast = row.get("forecast")
    if isinstance(forecast, list):
        for bucket in forecast:
            if not isinstance(bucket, dict):
                continue
            if bucket.get("override_days") != 0 or bucket.get("signal_quantity") != 0:
                continue
            quantity = bucket.get("quantity")
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
                continue
            quantity = float(quantity)
            proposed = max(0.0, round(quantity * factor, 2))
            if not math.isfinite(quantity) or not math.isfinite(proposed):
                return None
            periods.append(
                {
                    "period_start": bucket.get("bucket_start"),
                    "period_end": bucket.get("bucket_end"),
                    "quantity": quantity,
                    "proposed_quantity": proposed,
                }
            )
    if not periods:
        return None
    detail = exception_row.detail
    reason = (
        f"{detail[:1].upper()}{detail[1:]}, so each period without an "
        "override or a demand signal is proposed at "
        f"{_percent(factor * 100)}% of its forecast."
    )
    return {
        "measured_on": exception_row.detected_on,
        "compared_buckets": entry.get("compared_buckets"),
        "effective_total": float(effective_total),
        "actual_total": float(actual_total),
        "effective_bias": entry.get("effective_bias"),
        "factor": factor,
        "periods": periods,
        "reason": reason,
    }


def _corrections_for(answered: list[dict]) -> None:
    """The two correction reads, once each, and the corrections in place.

    The stored system settings (the one read the exceptions list
    makes) are read first; the two bounds then come from them
    through ``setting_value`` (a missing or out-of-range stored
    value the registry default), and the bias exception's measure on
    the stored settings — the very read the exceptions list makes
    for its forecast bias rows — once, before any correction is
    worked out.  The item-sites whose bias persists are the ones the
    exceptions list's own ``_forecast_rows`` answers a row for at
    the two bounds.  Anything raised while the reads run or the
    corrections are worked out is caught: every answered row's
    ``correction`` is then null and the route still answers 200.
    """
    try:
        stored = _stored_system_settings()
        threshold = setting_value(stored, FORECAST_BIAS_THRESHOLD_KEY)
        min_periods = setting_value(stored, FORECAST_BIAS_MIN_PERIODS_KEY)
        measure = _forecast_bias_accuracy(stored)
        exception_by_pair = {
            (exception_row.item_code, exception_row.site_code): exception_row
            for exception_row in _forecast_rows(measure, threshold, min_periods)
        }
        entry_by_pair = _measure_entries(measure)
        corrections = [
            _row_correction(row, exception_by_pair, entry_by_pair) for row in answered
        ]
    except Exception:
        corrections = [None] * len(answered)
    for row, correction in zip(answered, corrections):
        row["correction"] = correction


@router.get("", response_model=list[ForecastRow])
def list_forecasts(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, le=1_000_000_000),
    demand_class: (
        Literal["no_demand", "smooth", "erratic", "intermittent", "lumpy"] | None
    ) = Query(default=None),
    as_of: date | None = Query(default=None),
    include_correction: bool = Query(default=False),
) -> list[dict]:
    """List one row per item-site that has a forecast, for any signed-in user.

    Ordered by ``total_forecast`` descending, then ``item_code`` and
    ``site_code``; item-sites whose demand class is ``no_demand`` are
    left out. ``demand_class`` filters the list, ``as_of`` sets the date
    (the shared ``app.stats.demand`` default when absent), and
    ``limit`` / ``offset`` page it.

    While ``include_correction`` is false (the default) the answer
    is exactly that, key for key: no row carries a ``correction``
    key and neither the stored system settings nor the bias
    exception's measure is read.  While it is true and at least one
    row is answered, those two reads run once each, before any
    correction is worked out: the stored system settings (the one
    read the exceptions list makes) and, on them, the bias
    exception's measure (the very read the exceptions list makes for
    its forecast bias rows, over the window its setting names, with
    no as-of of its own).  The two bounds come from the stored
    settings through ``setting_value`` (a missing or out-of-range
    stored value the registry default), and the item-sites whose
    bias persists are the ones the exceptions list's own
    forecast-bias rule answers a row for.  Every answered row then
    carries ``correction``, a ``ForecastCorrection`` or null:
    ``measured_on`` the exception's detected date,
    ``compared_buckets``, ``effective_total``, ``actual_total`` and
    ``effective_bias`` the measure's item-site entry's own,
    ``factor`` the entry's actual total over its effective total
    (unrounded), ``periods`` one ``ForecastCorrectionPeriod`` per
    forecast bucket without an override day and without a demand
    signal, in order, its proposed quantity ``max(0, round(quantity
    x factor, 2))``, and ``reason`` the exception's own detail,
    first letter upper-cased, and the percent of its forecast each
    such period is proposed at.  Null covers an item-site whose
    bias does not persist, one the measure has no entry or no
    finite totals above 0 for, and one with no such bucket left.
    A read that raises never fails the list: every answered row's
    ``correction`` is then null and the route still answers 200
    with the rows it answers without the parameter; the rule reads
    no acknowledgement or snooze of the exception, and nothing is
    written.
    """
    _reject_nul_bytes(demand_class)
    # The list on a fixed query budget, whatever the number of
    # item-sites: the effective forecast beside the statistical one
    # [rule: OVR-3] [rule: OVR-4].
    rows = forecast_rows(as_of=as_of, demand_class=demand_class)
    # The parameter rides on every answered row as private state
    # the row's serializer reads: a fresh dict per row, so a row
    # built anywhere else keeps the parameter false and dumps
    # today's keys.
    answered = [
        {**row, "include_correction": include_correction}
        for row in rows[offset : offset + limit]
    ]
    if include_correction and answered:
        _corrections_for(answered)
    return answered


@router.get("/by-key", response_model=ForecastRowDetail)
def get_forecast_by_key(
    item_code: str = Query(..., max_length=64),
    site_code: str = Query(..., max_length=64),
    as_of: date | None = Query(default=None),
    include_correction: bool = Query(default=False),
) -> dict:
    """One item-site's forecast row plus the ``history`` it was built from.

    Answers 404 when the item-site has no demand events, or when its
    demand class is ``no_demand`` — before either correction read
    runs.

    ``include_correction`` (false by default) carries the list's
    ``correction`` over to this row.  While it is false the answer
    is exactly today's, key for key: no ``correction`` key and
    neither the stored system settings nor the bias exception's
    measure is read.  While it is true, those two reads run once
    each, before the correction is worked out, and the row carries
    ``correction``, a ``ForecastCorrection`` (the row's item-site's
    measured bias — the exception's detected date, the measure's
    compared buckets, totals and bias and the factor the measured
    actual total over the effective one — and one
    ``ForecastCorrectionPeriod`` per forecast bucket without an
    override day and without a demand signal, its proposed quantity
    ``max(0, round(quantity x factor, 2))``) or null exactly as the
    list decides it; a read that raises never fails the answer —
    the route still answers 200 with the row, ``correction`` null.
    Nothing is written.
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
    row = {**row, "history": history, "include_correction": include_correction}
    if include_correction:
        _corrections_for([row])
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


class CorrectionAccuracyItemSite(BaseModel):
    """One item-site's correction measures.

    Only the compared buckets with a stored
    ``proposed_quantity`` are summed in: ``before_total`` is each
    bucket's effective quantity (the forecast before the
    corrections), ``after_total`` its proposed quantity,
    ``before_wape`` and ``after_wape`` the sums of their absolute
    differences from the actuals over ``actual_total`` (null when it
    is 0), ``before_bias`` and ``after_bias`` ``before_total`` or
    ``after_total`` minus ``actual_total``, over ``actual_total``
    (null likewise), and ``value_added`` ``before_wape`` minus
    ``after_wape`` — positive when the corrections would have moved
    the forecast nearer the actuals.
    """

    item_code: str
    site_code: str
    compared_buckets: int
    actual_total: float
    before_total: float
    after_total: float
    before_wape: float | None
    after_wape: float | None
    before_bias: float | None
    after_bias: float | None
    value_added: float | None


class CorrectionAccuracyResult(BaseModel):
    """The realised value of the stored corrections.

    Only completed snapshot buckets (``bucket_end`` on or before
    ``as_of`` — the shared default as-of date when absent) with a
    stored ``proposed_quantity`` are compared with the actual demand
    of their range, the same way ``GET /api/forecast/accuracy``
    compares; a bucket whose proposal is null is left out.
    ``item_sites`` carries one entry per item-site with the same
    measures, ordered by ``actual_total`` descending, then
    ``item_code`` and ``site_code``.
    """

    as_of: date
    compared_buckets: int
    actual_total: float
    before_total: float
    after_total: float
    before_wape: float | None
    after_wape: float | None
    before_bias: float | None
    after_bias: float | None
    value_added: float | None
    item_sites: list[CorrectionAccuracyItemSite]


@router.get("/accuracy/corrections", response_model=CorrectionAccuracyResult)
def get_forecast_accuracy_corrections(
    as_of: date | None = Query(default=None),
    origin: Literal["live", "backfill"] | None = Query(default=None),
) -> dict:
    """The realised value of the stored corrections, any signed-in user.

    Only completed snapshot buckets with a stored
    ``proposed_quantity`` (``bucket_end`` on or before ``as_of`` —
    the shared default as-of date when absent, because demand after
    the latest demand date is unknown, not zero) are compared with
    the actual demand of their range; a bucket whose proposal is
    null is left out.  ``origin`` (``live`` or ``backfill``) keeps
    only snapshots of that origin.  ``item_sites`` is ordered by
    ``actual_total`` descending, then ``item_code`` and
    ``site_code``.  A malformed ``as_of`` or ``origin`` answers 422
    in FastAPI's validation envelope.
    """
    return correction_accuracy(as_of=as_of, origin=origin)


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


def proposed_correction(
    item_code: str, site_code: str, period_start: date
) -> dict | None:
    """The correction proposed for the item-site's ``period_start``.

    The ``correction`` the by-key route answers for the item-site
    (``include_correction`` true, its shared default as-of date), when
    one of its periods starts on ``period_start``: the
    ``ForecastCorrection`` in JSON, with every key.  None when there
    is no correction, when no period starts on the date, or when
    working it out fails anywhere, the by-key answer's 404 among
    them: the override is then stored as usual, with no proposal row.
    """
    try:
        row = get_forecast_by_key(
            item_code=item_code,
            site_code=site_code,
            as_of=None,
            include_correction=True,
        )
        correction = row.get("correction")
        if correction is None:
            return None
        for period in correction.get("periods", []):
            if isinstance(period, dict) and period.get("period_start") == period_start:
                return ForecastCorrection.model_validate(correction).model_dump(
                    mode="json"
                )
        return None
    except Exception:
        return None


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
    # The proposal is worked out before the override is stored: the
    # correction the admin was shown, while the new override's period
    # is still in it.
    proposal = proposed_correction(body.item_code, body.site_code, body.period_start)
    stored = create_override(
        body.item_code,
        body.site_code,
        body.period_start,
        body.quantity,
        body.reason,
        user,
    )
    if proposal is not None:
        with contextlib.suppress(Exception):
            _override_proposal_repo.insert(stored["id"], proposal)
    return stored


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


def _as_date(value: object) -> date | None:
    """The date behind a stored period start, or None.

    The proposals store their dates as ISO text (the correction's
    JSON form); an out-of-band row may hold anything, and anything
    that is not a date stays uncounted, never a failed read.
    """
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _finite(value: object) -> float | None:
    """The finite number behind a stored quantity, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _proposed_period(proposal: object, period_start: date) -> dict | None:
    """The override's own period in its stored proposal, or None."""
    if not isinstance(proposal, dict):
        return None
    periods = proposal.get("periods")
    if not isinstance(periods, list):
        return None
    for period in periods:
        if (
            isinstance(period, dict)
            and _as_date(period.get("period_start")) == period_start
        ):
            return period
    return None


class ForecastOverrideProposalCounts(BaseModel):
    """The current overrides' proposals, counted.

    ``overrides`` the number of current overrides — each period's
    highest-``id`` stored row, when it is an ``override`` [rule:
    OVR-2] [rule: OVR-9] [rule: OVR-13]; ``proposed`` those whose
    stored proposal carries a period for the override's own
    ``period_start`` (the override's proposed period); ``taken``
    those of them whose ``quantity``, rounded to 2 decimals, equals
    that period's ``proposed_quantity``; ``same_direction`` those of
    them for which the override's ``quantity`` minus the period's
    ``quantity`` and the period's ``proposed_quantity`` minus its
    ``quantity`` are both above 0 or both below 0; and
    ``without_proposal`` the current overrides with no stored
    proposal, or none for their period — every override stored before
    the proposals existed among them.  ``proposed`` plus
    ``without_proposal`` equals ``overrides``.
    """

    overrides: int
    proposed: int
    taken: int
    same_direction: int
    without_proposal: int


@router.get("/overrides/proposals", response_model=ForecastOverrideProposalCounts)
def forecast_override_proposal_counts() -> dict:
    """The proposals counted over the current forecast overrides.

    Any signed-in user may read the counts [rule: OVR-10].  The
    current overrides are the very rows the effective forecast
    counts: each period's highest-``id`` stored row, when it is an
    ``override`` [rule: OVR-2] [rule: OVR-9] [rule: OVR-13], read in
    one query; ``overrides`` their number.  Of them, ``proposed``
    carry a stored proposal with a period for the override's own
    ``period_start`` (the override's proposed period); ``taken``
    those of them whose ``quantity``, rounded to 2 decimals, equals
    that period's ``proposed_quantity``; ``same_direction`` those of
    them for which the override's ``quantity`` minus the period's
    ``quantity`` and the period's ``proposed_quantity`` minus its
    ``quantity`` are both above 0 or both below 0; and
    ``without_proposal`` the current overrides with no stored
    proposal, or none for their period — every override stored before
    the proposals existed among them, so ``proposed`` plus
    ``without_proposal`` equals ``overrides``.  Their stored
    proposals come from one read of the proposal store.  The route
    writes nothing.
    """
    with get_cursor() as cur:
        cur.execute(_CURRENT_OVERRIDE_SELECT.format(scope=""), ())
        overrides = cur.fetchall()
    stored = _override_proposal_repo.for_overrides([row["id"] for row in overrides])
    counts = {
        "overrides": len(overrides),
        "proposed": 0,
        "taken": 0,
        "same_direction": 0,
        "without_proposal": 0,
    }
    for row in overrides:
        period = _proposed_period(stored.get(row["id"]), row["period_start"])
        if period is None:
            counts["without_proposal"] += 1
            continue
        counts["proposed"] += 1
        quantity = _finite(row.get("quantity"))
        period_quantity = _finite(period.get("quantity"))
        proposed_quantity = _finite(period.get("proposed_quantity"))
        if quantity is None or period_quantity is None or proposed_quantity is None:
            continue
        if round(quantity, 2) == proposed_quantity:
            counts["taken"] += 1
        override_delta = quantity - period_quantity
        proposal_delta = proposed_quantity - period_quantity
        if (override_delta > 0 and proposal_delta > 0) or (
            override_delta < 0 and proposal_delta < 0
        ):
            counts["same_direction"] += 1
    return counts
