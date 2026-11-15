"""The forecast's two demand inputs for one item-site (issue #360).

``forecast_demand(item_code, site_code, as_of, lead_time_days)`` turns
one item-site's effective forecast into the reorder point formula's
demand level and demand variability:

* the **window** is the first ``ceil(lead_time_days)`` days after the
  as-of date, at least 1 day and at most 360,
* each day of the window takes one day's share of the effective
  quantity of the forecast bucket it falls in — the bucket's
  ``effective_quantity`` divided by its length — and ``daily`` is the
  sum of those shares over the window's days,
* with a backtest, ``sd_daily`` is ``1.2533`` times the backtest MAE
  divided by ``sqrt(30)`` and ``variability`` is ``"backtest"``; with
  no backtest, ``sd_daily`` is the historical ``std_daily`` and
  ``variability`` is ``"history"``.

The forecast is built the way ``GET /api/forecast/by-key`` builds it:
the demand stats and the 30-day history buckets from
``app.stats.demand`` / ``app.forecast.history``, the method chosen by
``app.forecast.select.choose_forecast`` (no second copy of the
selection rules here), and the effective forecast from
``app.forecast.overrides`` — the planner's counted overrides beside
the statistical quantities.  It returns None when the item-site has
no forecast: no demand events, a ``no_demand`` demand class, no
history, or a history the selection cannot run on.

``forecast_demand_for(stats, history, counted, lead_time_days)``
(issue #365) builds the exact same result from inputs a caller has
already loaded — the pair's demand statistics row as
``app.stats.demand.demand_stats_for_all`` loads them, its row from
``app.forecast.history.history_for_all`` and its row from
``app.forecast.overrides.counted_overrides`` — without a second query,
so ``app.recommend.reorder.recommendations_for_all`` can build every
item-site's forecast basis from what the list already read.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from app.forecast.history import history_for_pair
from app.forecast.overrides import counted_overrides, effective_buckets_for
from app.forecast.select import choose_forecast
from app.forecast.snapshots import HORIZON_BUCKETS
from app.stats.demand import BUCKET_DAYS, demand_stats_for_pair

# For errors that are roughly normal, the standard deviation is 1.2533
# times the mean absolute error; the error is measured per 30-day
# period, as the historical spread is, so the daily standard deviation
# divides it by sqrt(30) (delegated decision, 2026-10-13).
MAE_TO_STD = 1.2533
MAX_WINDOW_DAYS = 360


def _window_days(lead_time_days: float) -> int:
    """``ceil(lead_time_days)``, at least 1 and at most 360.

    A non-finite or non-numeric lead time (a value that could only
    come from data written out of band) gets the smallest window
    instead of raising.
    """
    try:
        window = int(math.ceil(lead_time_days))
    except (ValueError, OverflowError, TypeError):
        return 1
    return max(1, min(MAX_WINDOW_DAYS, window))


def _choose(history: list[dict], demand_class: str) -> dict | None:
    """The method chosen for the pair's history buckets.

    ``app.forecast.select.choose_forecast`` over the bucket quantities
    with the ``HORIZON_BUCKETS`` horizon; None when the selection
    cannot run on the history or gives no forecast.
    """
    try:
        return choose_forecast(
            [bucket["quantity"] for bucket in history],
            demand_class,
            horizon=HORIZON_BUCKETS,
        )
    except ValueError:
        return None


def _inputs(
    chosen: dict,
    as_of: date,
    counted: list[tuple[int, date, date, float]],
    stats: dict,
    lead_time_days: float,
) -> dict:
    """The formula's two demand inputs of one chosen forecast.

    ``chosen`` is one ``app.forecast.select.choose_forecast`` result
    for the as-of date, ``counted`` the item-site's counted overrides
    as ``app.forecast.overrides.counted_overrides`` returns them (no
    query is issued), ``stats`` the pair's demand statistics row (its
    ``std_daily`` feeds the no-backtest variability) and
    ``lead_time_days`` the window's lead time.
    """
    # The same 12 horizon buckets the forecast API builds, then the
    # effective forecast beside the statistical one.
    buckets = [
        {
            "bucket_start": as_of + timedelta(days=BUCKET_DAYS * k + 1),
            "bucket_end": as_of + timedelta(days=BUCKET_DAYS * (k + 1)),
            "quantity": quantity,
        }
        for k, quantity in enumerate(chosen["forecast"])
    ]
    buckets = effective_buckets_for(buckets, counted)

    window = _window_days(lead_time_days)
    total = 0.0
    for day in range(1, window + 1):
        bucket = buckets[(day - 1) // BUCKET_DAYS]
        length = (bucket["bucket_end"] - bucket["bucket_start"]).days + 1
        total += bucket["effective_quantity"] / length
    daily = total / window

    backtest = chosen["backtest"]
    if backtest is not None:
        sd_daily = MAE_TO_STD * backtest["mae"] / math.sqrt(BUCKET_DAYS)
        variability = "backtest"
    else:
        sd_daily = stats["std_daily"]
        variability = "history"
    return {
        "daily": daily,
        "sd_daily": sd_daily,
        "window_days": window,
        "variability": variability,
    }


def forecast_demand(
    item_code: str,
    site_code: str,
    as_of: date | None,
    lead_time_days: float,
) -> dict | None:
    """The item-site's forecast as the formula's two demand inputs.

    Returns a dict with ``daily`` (the effective forecast over the
    lead-time window, per day), ``sd_daily`` (the demand standard
    deviation per day), ``window_days`` and ``variability``
    (``"backtest"`` when the forecast's backtest supplies the
    variability, else ``"history"``).  Returns None when the
    item-site has no forecast.
    """
    stats = demand_stats_for_pair(item_code, site_code, as_of)
    if stats is None or stats["demand_class"] == "no_demand":
        return None
    resolved_as_of = stats["as_of"]
    history = history_for_pair(item_code, site_code, resolved_as_of)
    if history is None:
        return None
    chosen = _choose(history, stats["demand_class"])
    if chosen is None:
        return None
    counted = counted_overrides([(item_code, site_code)])
    return _inputs(
        chosen,
        resolved_as_of,
        counted[(item_code, site_code)],
        stats,
        lead_time_days,
    )


def forecast_demand_for(
    stats: dict | None,
    history: list[dict] | None,
    counted: list[tuple[int, date, date, float]],
    lead_time_days: float,
) -> dict | None:
    """The item-site's forecast built from already-loaded inputs.

    ``stats`` is the pair's demand statistics row as
    ``app.stats.demand.demand_stats_for_all`` loads them (or None when
    the pair has no demand events), ``history`` the pair's row from
    ``app.forecast.history.history_for_all`` (or None) and ``counted``
    the pair's row from ``app.forecast.overrides.counted_overrides`` —
    none of the three is re-read.  Returns exactly what
    ``forecast_demand`` would return for the pair: a dict with
    ``daily``, ``sd_daily``, ``window_days`` and ``variability``, or
    None when the item-site has no forecast.
    """
    if stats is None or stats["demand_class"] == "no_demand":
        return None
    if history is None:
        return None
    chosen = _choose(history, stats["demand_class"])
    if chosen is None:
        return None
    return _inputs(chosen, stats["as_of"], counted, stats, lead_time_days)
