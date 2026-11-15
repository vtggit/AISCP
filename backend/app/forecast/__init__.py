"""Statistical forecasts over 30-day demand buckets (issue #308).

Pure computation only (no database access): the five forecast methods
and their stable arithmetic representations in ``app.forecast.methods``,
and the method chooser with its backtest in ``app.forecast.select``.
"""

from app.forecast.methods import (
    ARITHMETIC_FIELDS,
    explain_damped_trend,
    explain_mean,
    explain_sba,
    explain_seasonal,
    explain_ses,
    forecast_damped_trend,
    forecast_mean,
    forecast_sba,
    forecast_seasonal,
    forecast_ses,
)
from app.forecast.select import (
    BACKTEST_BUCKETS,
    DEMAND_CLASSES,
    MEAN_MIN_BUCKETS,
    SEASONAL_MIN_BUCKETS,
    choose_forecast,
)

__all__ = [
    "ARITHMETIC_FIELDS",
    "BACKTEST_BUCKETS",
    "DEMAND_CLASSES",
    "MEAN_MIN_BUCKETS",
    "SEASONAL_MIN_BUCKETS",
    "choose_forecast",
    "explain_damped_trend",
    "explain_mean",
    "explain_sba",
    "explain_seasonal",
    "explain_ses",
    "forecast_damped_trend",
    "forecast_mean",
    "forecast_sba",
    "forecast_seasonal",
    "forecast_ses",
]
