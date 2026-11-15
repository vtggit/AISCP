"""The forecast list on a fixed query budget (issue #374).

``forecast_rows(as_of=None, demand_class=None)`` returns every
item-site's forecast row — the effective forecast beside the
statistical one [rule: OVR-3] [rule: OVR-4] — in the order
``app.api.forecast.list_forecasts`` returns them today:
``total_forecast`` descending, then ``item_code`` and then
``site_code``.  Item-sites whose demand class is ``no_demand`` are
left out, and an item-site with no usable forecast is simply absent.
``demand_class`` filters the list and the default ``as_of`` is the
shared ``app.stats.demand`` default.

The number of SQL statements executed is fixed and does not grow with
the number of item-sites: the demand statistics come from
``app.stats.demand.demand_stats_for_all``, the 30-day history buckets
of every item-site from ``app.forecast.history.history_for_all`` — one
query for all of them, equal to ``history_for_pair`` for each pair by
the same complete-bucket rule — and the counted overrides of every
row from one ``app.forecast.overrides.effective_for_all`` call (one
query in total).  ``build_forecast_row`` and ``forecast_boundaries``
are the row and the 12 horizon bucket boundaries the row's ``forecast``
is built on; ``app.api.forecast`` reuses them for the per-item-site
``by-key`` path.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.forecast.history import history_for_all
from app.forecast.overrides import effective_for_all
from app.forecast.select import choose_forecast
from app.forecast.snapshots import HORIZON_BUCKETS
from app.stats.demand import BUCKET_DAYS, demand_stats_for_all


def forecast_boundaries(as_of: date) -> list[dict]:
    """The 12 horizon bucket boundaries, the first starting the day after ``as_of``."""
    return [
        {
            "bucket_start": as_of + timedelta(days=BUCKET_DAYS * k + 1),
            "bucket_end": as_of + timedelta(days=BUCKET_DAYS * (k + 1)),
        }
        for k in range(HORIZON_BUCKETS)
    ]


def build_forecast_row(
    item_code: str,
    site_code: str,
    demand_class: str,
    as_of: date,
    history: list[dict] | None,
) -> dict | None:
    """One response row for an item-site, or None when it has no forecast."""
    if demand_class == "no_demand" or history is None:
        return None
    try:
        chosen = choose_forecast(
            [bucket["quantity"] for bucket in history],
            demand_class,
            horizon=HORIZON_BUCKETS,
        )
    except ValueError:
        return None
    if chosen is None:
        return None
    forecast = [
        {**boundary, "quantity": quantity}
        for boundary, quantity in zip(forecast_boundaries(as_of), chosen["forecast"])
    ]
    return {
        "item_code": item_code,
        "site_code": site_code,
        "as_of": as_of,
        "demand_class": demand_class,
        "method": chosen["method"],
        "params": chosen["params"],
        "history_buckets": chosen["history_buckets"],
        "total_forecast": sum(chosen["forecast"]),
        "forecast": forecast,
        "backtest": chosen["backtest"],
        "arithmetic": chosen["arithmetic"],
    }


def forecast_rows(
    as_of: date | None = None,
    demand_class: str | None = None,
) -> list[dict]:
    """Every item-site's forecast row, the effective beside the statistical.

    One row per item-site that has a forecast — item-sites whose
    demand class is ``no_demand`` are left out — ordered by
    ``total_forecast`` descending, then ``item_code`` and ``site_code``.
    ``demand_class`` filters the list and ``as_of`` sets the date (the
    shared ``app.stats.demand`` default when absent).  Every row
    carries the statistical forecast's ``total_forecast`` and
    ``forecast`` beside the effective forecast's
    ``total_effective_forecast``, ``overridden_buckets`` and the
    buckets' ``effective_quantity`` / ``override_days``.

    The statement count is fixed, whatever the number of item-sites:
    the demand statistics from ``demand_stats_for_all``, every
    item-site's 30-day history buckets from one ``history_for_all``
    query and the counted overrides of every row from one
    ``effective_for_all`` query.
    """
    histories = history_for_all(as_of)
    rows: list[dict] = []
    for stats in demand_stats_for_all(as_of):
        if demand_class is not None and stats["demand_class"] != demand_class:
            continue
        row = build_forecast_row(
            stats["item_code"],
            stats["site_code"],
            stats["demand_class"],
            stats["as_of"],
            histories.get((stats["item_code"], stats["site_code"])),
        )
        if row is not None:
            rows.append(row)
    rows = effective_for_all(rows)
    rows.sort(
        key=lambda row: (-row["total_forecast"], row["item_code"], row["site_code"])
    )
    return rows
