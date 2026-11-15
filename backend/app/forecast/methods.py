"""Forecast methods for 30-day demand buckets (issue #308, demand planning step 1).

Five pure functions, standard library only. Each takes ``history`` — a
list of 30-day bucket quantities, oldest first, in the same buckets
``app.stats.demand`` uses — and a horizon ``h``, and returns ``h``
floats, never negative. Each raises ``ValueError`` for an empty history
or an ``h`` below 1; ``forecast_damped_trend`` additionally needs at
least 2 quantities and ``forecast_seasonal`` at least 2 * season (24
buckets by default). A forecast value that arithmetic pushes negative
(a falling damped trend) is clamped to 0.

Every method also has an ``explain_`` twin returning the stable
arithmetic representation: a dict with exactly the fields in
``ARITHMETIC_FIELDS`` (method, inputs, formula, intermediates,
result), so the arithmetic behind a forecast can be written out and
asserted on, not just the resulting numbers.
"""

from __future__ import annotations

import math

MEAN_WINDOW = 12
SEASON = 12
MAX_SEASONS = 3

# The frozen field set of every arithmetic representation.
ARITHMETIC_FIELDS = frozenset(
    {"method", "inputs", "formula", "intermediates", "result"}
)

FORMULAS = {
    "mean": (
        "forecast = (sum of the last 12 buckets, or of all of them when "
        "fewer) / (number of buckets used), repeated for each of the h buckets"
    ),
    "ses": (
        "level starts at history[0]; for each later bucket "
        "level = alpha * quantity + (1 - alpha) * level; "
        "forecast = final level, repeated for each of the h buckets"
    ),
    "damped_trend": (
        "level starts at history[0], trend at history[1] - history[0]; for "
        "each later bucket level = alpha * quantity + (1 - alpha) * "
        "(previous_level + phi * previous_trend) and trend = beta * "
        "(level - previous_level) + (1 - beta) * phi * previous_trend; "
        "step k ahead = level + (phi + phi**2 + ... + phi**k) * trend"
    ),
    "seasonal": (
        "over the last whole seasons (at most 3): index[p] = mean quantity "
        "at position p / overall mean of the buckets used; level = mean of "
        "the last season buckets; step k ahead = level * index of the "
        "position that bucket falls on; a zero overall mean gives zeros"
    ),
    "sba": (
        "starting from the first non-zero quantity, the demand size and the "
        "interval between non-zero quantities are each smoothed with alpha "
        "(the interval starts at 1) at every non-zero quantity; "
        "forecast = (1 - alpha / 2) * size / interval, repeated; a history "
        "with no non-zero quantity gives zeros"
    ),
}


def _fail(method: str, message: str) -> ValueError:
    return ValueError(f"{method}: {message}")


def validate_history(history, method: str, min_buckets: int = 1) -> list[float]:
    """Coerce ``history`` to a list of finite floats.

    Raises ``ValueError`` when it is not a list/tuple of finite numbers,
    when it is empty, or when it has fewer than ``min_buckets`` buckets.
    """
    if not isinstance(history, (list, tuple)):
        raise _fail(method, "history must be a list of bucket quantities")
    if len(history) == 0:
        raise _fail(method, "history must not be empty")
    if len(history) < min_buckets:
        raise _fail(
            method,
            f"history needs at least {min_buckets} buckets, got {len(history)}",
        )
    values: list[float] = []
    for quantity in history:
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            raise _fail(method, "history quantities must be numbers")
        value = float(quantity)
        if not math.isfinite(value):
            raise _fail(method, "history quantities must be finite")
        values.append(value)
    return values


def validate_horizon(h, method: str) -> int:
    """``h`` must be an integer of at least 1 (ValueError otherwise)."""
    if isinstance(h, bool) or not isinstance(h, int):
        raise _fail(method, "horizon must be an integer")
    if h < 1:
        raise _fail(method, "horizon must be at least 1")
    return h


def _smoothing_param(name: str, value, method: str) -> float:
    """A smoothing parameter (alpha, beta): a finite number in [0, 1]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(method, f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise _fail(method, f"{name} must be finite")
    if not 0.0 <= value <= 1.0:
        raise _fail(method, f"{name} must be between 0 and 1")
    return value


def _phi(value, method: str) -> float:
    """The damping factor phi: a finite number in (0, 1]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(method, "phi must be a number")
    value = float(value)
    if not math.isfinite(value) or not 0.0 < value <= 1.0:
        raise _fail(method, "phi must be between 0 and 1")
    return value


def _season(value, method: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _fail(method, "season must be an integer of at least 1")
    return value


def _representation(
    method: str,
    history: list[float],
    horizon: int,
    formula: str,
    intermediates: dict,
    result: list[float],
    extra_inputs: dict | None = None,
) -> dict:
    """Build the frozen-field arithmetic representation for a method."""
    inputs: dict = {"history": history, "horizon": horizon}
    if extra_inputs:
        inputs.update(extra_inputs)
    return {
        "method": method,
        "inputs": inputs,
        "formula": formula,
        "intermediates": intermediates,
        "result": result,
    }


def explain_mean(history, h) -> dict:
    """Arithmetic representation for ``forecast_mean``."""
    values = validate_history(history, "mean")
    horizon = validate_horizon(h, "mean")
    window = values[-MEAN_WINDOW:]
    mean = sum(window) / len(window)
    result = [max(0.0, mean)] * horizon
    return _representation(
        "mean",
        values,
        horizon,
        FORMULAS["mean"],
        {"window": window, "mean": mean},
        result,
    )


def forecast_mean(history, h) -> list[float]:
    """The average of the last 12 buckets (of all when fewer), repeated."""
    return explain_mean(history, h)["result"]


def explain_ses(history, h, alpha) -> dict:
    """Arithmetic representation for ``forecast_ses``."""
    values = validate_history(history, "ses")
    horizon = validate_horizon(h, "ses")
    alpha = _smoothing_param("alpha", alpha, "ses")
    level = values[0]
    for quantity in values[1:]:
        level = alpha * quantity + (1.0 - alpha) * level
    result = [max(0.0, level)] * horizon
    return _representation(
        "ses",
        values,
        horizon,
        FORMULAS["ses"],
        {"final_level": level},
        result,
        {"alpha": alpha},
    )


def forecast_ses(history, h, alpha) -> list[float]:
    """Simple exponential smoothing: the final level, repeated."""
    return explain_ses(history, h, alpha)["result"]


def explain_damped_trend(history, h, alpha, beta, phi: float = 0.9) -> dict:
    """Arithmetic representation for ``forecast_damped_trend``."""
    values = validate_history(history, "damped_trend", min_buckets=2)
    horizon = validate_horizon(h, "damped_trend")
    alpha = _smoothing_param("alpha", alpha, "damped_trend")
    beta = _smoothing_param("beta", beta, "damped_trend")
    phi = _phi(phi, "damped_trend")
    level, trend = values[0], values[1] - values[0]
    for quantity in values[2:]:
        previous_level, previous_trend = level, trend
        level = alpha * quantity + (1.0 - alpha) * (
            previous_level + phi * previous_trend
        )
        trend = beta * (level - previous_level) + (1.0 - beta) * phi * previous_trend
    damping_sums: list[float] = []
    running = 0.0
    for k in range(1, horizon + 1):
        running += phi**k
        damping_sums.append(running)
    result = [max(0.0, level + weight * trend) for weight in damping_sums]
    return _representation(
        "damped_trend",
        values,
        horizon,
        FORMULAS["damped_trend"],
        {
            "final_level": level,
            "final_trend": trend,
            "damping_sums": damping_sums,
        },
        result,
        {"alpha": alpha, "beta": beta, "phi": phi},
    )


def forecast_damped_trend(history, h, alpha, beta, phi: float = 0.9) -> list[float]:
    """Exponential smoothing with a damped trend (needs 2+ buckets)."""
    return explain_damped_trend(history, h, alpha, beta, phi)["result"]


def explain_seasonal(history, h, season: int = SEASON) -> dict:
    """Arithmetic representation for ``forecast_seasonal``."""
    season = _season(season, "seasonal")
    values = validate_history(history, "seasonal", min_buckets=2 * season)
    horizon = validate_horizon(h, "seasonal")
    n = len(values)
    seasons_used = min(MAX_SEASONS, n // season)
    used = values[-seasons_used * season :]
    overall_mean = sum(used) / len(used)
    if overall_mean == 0.0:
        indices = [0.0] * season
        level = 0.0
        result = [0.0] * horizon
    else:
        indices = [
            sum(used[j * season + position] for j in range(seasons_used))
            / seasons_used
            / overall_mean
            for position in range(season)
        ]
        level = sum(values[-season:]) / season
        result = [
            max(0.0, level * indices[(k - 1 + len(used)) % season])
            for k in range(1, horizon + 1)
        ]
    return _representation(
        "seasonal",
        values,
        horizon,
        FORMULAS["seasonal"],
        {
            "seasons_used": seasons_used,
            "overall_mean": overall_mean,
            "level": level,
            "indices": indices,
        },
        result,
        {"season": season},
    )


def forecast_seasonal(history, h, season: int = SEASON) -> list[float]:
    """Level times a seasonal index of ``season`` buckets (needs 24+)."""
    return explain_seasonal(history, h, season)["result"]


def explain_sba(history, h, alpha: float = 0.1) -> dict:
    """Arithmetic representation for ``forecast_sba``."""
    values = validate_history(history, "sba")
    horizon = validate_horizon(h, "sba")
    alpha = _smoothing_param("alpha", alpha, "sba")
    non_zero = [(i, q) for i, q in enumerate(values) if q != 0]
    if not non_zero:
        return _representation(
            "sba",
            values,
            horizon,
            FORMULAS["sba"],
            {
                "first_non_zero_index": None,
                "final_size": None,
                "final_interval": None,
            },
            [0.0] * horizon,
            {"alpha": alpha},
        )
    first_index, first_quantity = non_zero[0]
    size = float(first_quantity)
    interval = 1.0
    previous_index = first_index
    for index, quantity in non_zero[1:]:
        gap = index - previous_index
        interval = alpha * gap + (1.0 - alpha) * interval
        size = alpha * quantity + (1.0 - alpha) * size
        previous_index = index
    value = (1.0 - alpha / 2.0) * size / interval
    result = [max(0.0, value)] * horizon
    return _representation(
        "sba",
        values,
        horizon,
        FORMULAS["sba"],
        {
            "first_non_zero_index": first_index,
            "final_size": size,
            "final_interval": interval,
        },
        result,
        {"alpha": alpha},
    )


def forecast_sba(history, h, alpha: float = 0.1) -> list[float]:
    """Syntetos-Boylan approximation for intermittent/lumpy demand."""
    return explain_sba(history, h, alpha)["result"]
