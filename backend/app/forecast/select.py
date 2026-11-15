"""Choosing a forecast method for one item-site's 30-day buckets (issue #308).

``choose_forecast(history, demand_class, horizon=12)`` picks the method
an item-site gets, the documented way: the demand class limits the
candidates (smooth/erratic: mean, ses, damped_trend and seasonal — the
last only with 24+ buckets; intermittent/lumpy: mean and sba), each
candidate is scored by a backtest (one-bucket-ahead forecasts over the
last 6 buckets, each made from the history before it, scored by mean
absolute error), and the lowest score wins. A tie goes to the earlier
method in the order above, then to the smaller parameters. A candidate
that cannot be run on every backtest prefix (seasonal with a prefix
shorter than its 24 buckets) is excluded for that prefix — and so from
selection — rather than raising. Fewer than 12 buckets of history gets
``mean`` with no backtest; ``no_demand`` gets no forecast (None).

The result is a dict with exactly the keys method, params, forecast
(the ``horizon`` quantities), history_buckets, arithmetic (a string
writing out the winning method's formula with its values) and backtest
(mae, baseline_mae — the mean method's score — wape and bias, or None
when no backtest ran).
"""

from __future__ import annotations

from app.forecast import methods
from app.forecast.methods import (
    explain_damped_trend,
    explain_mean,
    explain_sba,
    explain_seasonal,
    explain_ses,
)

DEMAND_CLASSES = ("no_demand", "smooth", "erratic", "intermittent", "lumpy")
MEAN_MIN_BUCKETS = 12
SEASONAL_MIN_BUCKETS = 24
BACKTEST_BUCKETS = 6

SES_ALPHAS = (0.1, 0.2, 0.3, 0.4, 0.5)
DAMPED_TREND_ALPHAS = (0.2, 0.4, 0.6)
DAMPED_TREND_BETAS = (0.1, 0.3)

# Minimum bucket counts for a backtest prefix a method can run on.
_MIN_PREFIX_BUCKETS = {
    "mean": 1,
    "ses": 1,
    "damped_trend": 2,
    "seasonal": methods.SEASON * 2,
    "sba": 1,
}


def _run(method: str, params: dict, history: list[float], horizon: int) -> dict:
    """The arithmetic representation for one method on one history."""
    if method == "mean":
        return explain_mean(history, horizon)
    if method == "ses":
        return explain_ses(history, horizon, params["alpha"])
    if method == "damped_trend":
        return explain_damped_trend(history, horizon, params["alpha"], params["beta"])
    if method == "seasonal":
        return explain_seasonal(history, horizon)
    return explain_sba(history, horizon)


def _candidates(demand_class: str, n_buckets: int) -> list[tuple[str, dict]]:
    """The candidate (method, params) pairs, in tie-break order.

    Methods first (mean, ses, damped_trend, seasonal / sba), parameters
    smallest first, so the earlier pair wins an exact score tie.
    """
    if demand_class in ("smooth", "erratic"):
        candidates: list[tuple[str, dict]] = [("mean", {})]
        for alpha in SES_ALPHAS:
            candidates.append(("ses", {"alpha": alpha}))
        for alpha in DAMPED_TREND_ALPHAS:
            for beta in DAMPED_TREND_BETAS:
                candidates.append(("damped_trend", {"alpha": alpha, "beta": beta}))
        if n_buckets >= SEASONAL_MIN_BUCKETS:
            candidates.append(("seasonal", {}))
        return candidates
    return [("mean", {}), ("sba", {})]


def _backtest_forecasts(
    method: str, params: dict, history: list[float]
) -> list[float] | None:
    """The candidate's one-bucket-ahead forecasts over the last 6 buckets.

    None when the candidate is not eligible for this history: it cannot
    be run on every backtest prefix (seasonal needs 24 buckets before
    each forecast), so it produces no backtest score.
    """
    forecasts: list[float] = []
    for index in range(len(history) - BACKTEST_BUCKETS, len(history)):
        prefix = history[:index]
        if len(prefix) < _MIN_PREFIX_BUCKETS[method]:
            return None
        forecasts.append(_run(method, params, prefix, 1)["result"][0])
    return forecasts


def _fmt(value) -> str:
    """A stable, exact rendering of numbers inside the arithmetic string."""
    if isinstance(value, float):
        return f"{value:.10g}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(item) for item in value) + "]"
    return str(value)


def _arithmetic_string(rep: dict) -> str:
    """The winning method's formula written out with its values."""
    parts = [f"{rep['method']}: {rep['formula']}"]
    for name, value in rep["inputs"].items():
        if name != "history":
            parts.append(f"{name}={_fmt(value)}")
    for name, value in rep["intermediates"].items():
        parts.append(f"{name}={_fmt(value)}")
    parts.append(f"forecast={_fmt(rep['result'])}")
    return "; ".join(parts)


def _result(
    method: str,
    params: dict,
    rep: dict,
    history_buckets: int,
    backtest: dict | None,
) -> dict:
    return {
        "method": method,
        "params": params,
        "forecast": rep["result"],
        "history_buckets": history_buckets,
        "arithmetic": _arithmetic_string(rep),
        "backtest": backtest,
    }


def choose_forecast(history, demand_class: str, horizon: int = 12) -> dict | None:
    """Pick the forecast for one item-site's 30-day buckets.

    See the module docstring for the candidate, backtest and tie rules.
    Raises ``ValueError`` for an unknown ``demand_class``, a non-list
    (or empty, or non-numeric) ``history``, or a ``horizon`` below 1.
    """
    if demand_class not in DEMAND_CLASSES:
        raise ValueError(
            f"unknown demand_class {demand_class!r}; "
            f"expected one of {', '.join(DEMAND_CLASSES)}"
        )
    if demand_class == "no_demand":
        return None
    values = methods.validate_history(history, "choose_forecast")
    methods.validate_horizon(horizon, "choose_forecast")
    n = len(values)
    if n < MEAN_MIN_BUCKETS:
        rep = explain_mean(values, horizon)
        return _result("mean", {}, rep, n, None)

    actuals = values[n - BACKTEST_BUCKETS :]
    scored: list[tuple[str, dict, float, list[float], list[float]]] = []
    for method, params in _candidates(demand_class, n):
        forecasts = _backtest_forecasts(method, params, values)
        if forecasts is None:
            continue
        errors = [abs(f - a) for f, a in zip(forecasts, actuals)]
        scored.append(
            (
                method,
                params,
                sum(errors) / BACKTEST_BUCKETS,
                forecasts,
                errors,
            )
        )

    baseline_mae = next(score for method, _, score, _, _ in scored if method == "mean")
    best = scored[0]
    for candidate in scored[1:]:
        if candidate[2] < best[2]:
            best = candidate
    method, params, mae, forecasts, errors = best
    total_actual = sum(actuals)
    backtest = {
        "mae": mae,
        "baseline_mae": baseline_mae,
        "wape": sum(errors) / total_actual if total_actual else None,
        "bias": (
            (sum(forecasts) - total_actual) / total_actual if total_actual else None
        ),
    }
    rep = _run(method, params, values, horizon)
    return _result(method, params, rep, n, backtest)
