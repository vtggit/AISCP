"""Issue #308 proof: forecast methods and method selection for 30-day buckets.

One test, no database: the five pure forecast methods in
``app.forecast.methods`` are proven against hand-computed numbers, each
``explain_*`` arithmetic representation is proven to carry the frozen
field set, and ``app.forecast.select.choose_forecast`` is proven through
the candidate, backtest, tie and eligibility rules with worked examples
whose errors were computed by hand.

* ``mean`` on ``[2, 4, 6, 8]`` is ``(2 + 4 + 6 + 8) / 4 = 5`` per
  bucket; with 14 buckets only the last 12 are used, so on
  ``1..14`` it is ``(3 + ... + 14) / 12 = 102 / 12 = 8.5``;
* ``ses`` with alpha 0.5 on ``[2, 4, 6, 8]``: the level runs
  ``2 -> 0.5*4 + 0.5*2 = 3 -> 0.5*6 + 0.5*3 = 4.5 -> 0.5*8 + 0.5*4.5
  = 6.25``;
* ``damped_trend`` with alpha 0.5, beta 0.5, phi 0.5 on the same
  history: level/trend run ``(2, 2) -> (4.5, 1.75) -> (6.6875,
  1.53125)``, so the steps are ``6.6875 + 0.5*1.53125 = 7.453125``,
  ``6.6875 + 0.75*1.53125 = 7.8359375`` and ``6.6875 + 0.875*1.53125
  = 8.02734375``;
* the never-negative clamp: ``damped_trend`` with alpha 0.9, beta 0.9
  on the falling ``[8, 4, 2, 1]`` lands at level 0.62504 with trend
  -1.952424, whose first step ``0.62504 + 0.9*(-1.952424)`` is
  negative, so the forecast is ``[0.0, 0.0, 0.0]``;
* ``seasonal`` reproduces a repeating 12-bucket pattern exactly on
  three cycles (level = the pattern's mean 4.75, each index is the
  pattern value over 4.75) and refuses fewer than 24 buckets;
* ``sba`` on ``[0, 0, 4, 0, 0, 0, 2, 0, 0, 0, 0, 6]`` with alpha 0.2
  smooths size ``4 -> 3.6 -> 4.08`` and interval ``1 -> 1.6 -> 2.28``
  at the three non-zero buckets, so the forecast is
  ``(1 - 0.1) * 4.08 / 2.28 = 153/95``;
* ``choose_forecast`` returns ``None`` for ``no_demand``, ``mean``
  with no backtest under 12 buckets, ``seasonal`` for a strongly
  seasonal 36-bucket history (backtest MAE 0, baseline 13/6),
  ``damped_trend`` or ``ses`` for a trending history, only ``mean`` or
  ``sba`` for the lumpy class, and a deterministic non-seasonal winner
  when the seasonal candidate's backtest prefixes are too short;
* the worked backtest example ``[4]*11 + [8]`` (smooth): every
  candidate forecasts 4 for the first five backtest buckets and 4
  against the final 8, so all scores are ``(5*0 + 4) / 6 = 2/3`` — the
  tie goes to ``mean``, and the backtest reports wape
  ``4 / 28 = 1/7`` and bias ``(24 - 28) / 28 = -1/7``;
* the second worked example ``[2]*6 + [5]*6`` (smooth) is won by
  ``damped_trend`` with alpha 0.6, beta 0.1 (first two backtest
  errors hand-checked: ``|5 - 2| = 3`` and ``|5 - 3.962| = 1.038``),
  beating the mean baseline ``20417/9240``;
* hostile histories (non-numeric, NaN, empty, string, huge) raise
  ``ValueError`` instead of crashing or producing NaN.
"""

import math

import pytest

from app.forecast import (
    ARITHMETIC_FIELDS,
    choose_forecast,
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

# A repeating 12-bucket pattern; sum 57, mean 4.75.
PATTERN = [10, 0, 5, 2, 8, 1, 3, 6, 9, 4, 7, 2]
PATTERN_MEAN = 4.75

# The lumpy worked history: spikes of 5 and 9, all else zero.
LUMPY = [0, 0, 5, 0, 0, 0, 0, 0, 9, 0, 0, 0]

# The sba worked history: non-zero at buckets 2 (qty 4), 6 (qty 2)
# and 11 (qty 6).
SBA_HIST = [0, 0, 4, 0, 0, 0, 2, 0, 0, 0, 0, 6]

RESULT_KEYS = {
    "method",
    "params",
    "forecast",
    "history_buckets",
    "arithmetic",
    "backtest",
}
BACKTEST_KEYS = {"mae", "baseline_mae", "wape", "bias"}


def test_issue308_freeform():
    approx = pytest.approx

    # ------------------------------------------------------------------
    # AC-1: the five methods, hand-computed values, ValueError, clamp.
    # ------------------------------------------------------------------

    # mean: the average of the last 12 (all of them when fewer),
    # repeated. (2+4+6+8)/4 = 5; on 14 buckets only 3..14 count:
    # 102/12 = 8.5.
    assert forecast_mean([2, 4, 6, 8], 3) == [5.0, 5.0, 5.0]
    assert forecast_mean(list(range(1, 15)), 2) == [8.5, 8.5]
    for value in forecast_mean([2, 4, 6, 8], 3):
        assert isinstance(value, float)
        assert value >= 0.0

    # ses: level 2 -> 3 -> 4.5 -> 6.25 with alpha 0.5.
    assert forecast_ses([2, 4, 6, 8], 3, 0.5) == [6.25, 6.25, 6.25]
    # alpha 1.0 collapses to the last quantity.
    assert forecast_ses([2, 4, 6, 8], 2, 1.0) == [8.0, 8.0]

    # damped_trend: level/trend (2,2) -> (4.5,1.75) -> (6.6875,1.53125);
    # damping sums 0.5, 0.75, 0.875 with phi 0.5.
    assert forecast_damped_trend([2, 4, 6, 8], 3, 0.5, 0.5, 0.5) == [
        7.453125,
        7.8359375,
        8.02734375,
    ]
    # It needs at least 2 quantities.
    with pytest.raises(ValueError):
        forecast_damped_trend([5], 3, 0.5, 0.5)

    # The never-negative clamp on a falling trend: the first step is
    # 0.62504 + 0.9 * (-1.952424) < 0, so every bucket is 0.
    assert forecast_damped_trend([8, 4, 2, 1], 3, 0.9, 0.9) == [0.0, 0.0, 0.0]

    # seasonal: a repeating 12-bucket pattern over three cycles is
    # reproduced exactly (level 4.75, indices pattern[p] / 4.75, and
    # the buckets continue the cycle from its start); 24 buckets
    # (two whole seasons) also work and refuse 23.
    assert forecast_seasonal(PATTERN * 3, 12) == [float(p) for p in PATTERN]
    assert forecast_seasonal(PATTERN * 2, 3) == [10.0, 0.0, 5.0]
    with pytest.raises(ValueError):
        forecast_seasonal((PATTERN * 3)[:23], 3)
    # A zero overall mean gives zeros.
    assert forecast_seasonal([0.0] * 24, 2) == [0.0, 0.0]

    # sba: size 4 -> 0.2*2 + 0.8*4 = 3.6 -> 0.2*6 + 0.8*3.6 = 4.08,
    # interval 1 -> 0.2*4 + 0.8*1 = 1.6 -> 0.2*5 + 0.8*1.6 = 2.28,
    # forecast (1 - 0.1) * 4.08 / 2.28 = 153/95. A history with no
    # non-zero quantity gives zeros.
    assert forecast_sba(SBA_HIST, 2, 0.2) == approx([153 / 95, 153 / 95])
    assert forecast_sba([0.0] * 12, 3) == [
        0.0,
        0.0,
        0.0,
    ]
    # A single non-zero: size = the quantity, interval = 1, so
    # (1 - 0.05) * 7 / 1 with the default alpha 0.1.
    assert forecast_sba([0, 0, 7], 1) == approx([0.95 * 7])

    # Empty history and h below 1 raise ValueError on every method.
    for fn, args in (
        (forecast_mean, ([], 3)),
        (forecast_ses, ([], 3)),
        (forecast_ses, ([1, 2, 3], 0)),
        (forecast_ses, ([1, 2, 3], -2)),
        (forecast_damped_trend, ([], 3)),
        (forecast_damped_trend, ([1, 2], 0)),
        (forecast_seasonal, ([], 3)),
        (forecast_seasonal, ([1.0] * 24, 0)),
        (forecast_sba, ([], 3)),
        (forecast_sba, ([1.0], 0)),
    ):
        if fn is forecast_ses:
            payload = (args[0], args[1], 0.5)
        elif fn in (forecast_damped_trend,):
            payload = (args[0], args[1], 0.5, 0.5)
        elif fn is forecast_seasonal or fn is forecast_sba:
            payload = (args[0], args[1])
        else:
            payload = args
        with pytest.raises(ValueError):
            fn(*payload)

    # ------------------------------------------------------------------
    # AC-5: the frozen-field arithmetic representations.
    # ------------------------------------------------------------------

    rep = explain_mean([2, 4, 6, 8], 3)
    assert set(rep) == set(ARITHMETIC_FIELDS)
    assert rep["method"] == "mean"
    assert rep["inputs"] == {"history": [2.0, 4.0, 6.0, 8.0], "horizon": 3}
    assert rep["intermediates"]["window"] == [2.0, 4.0, 6.0, 8.0]
    assert rep["intermediates"]["mean"] == 5.0
    assert rep["result"] == forecast_mean([2, 4, 6, 8], 3)
    assert isinstance(rep["formula"], str) and rep["formula"]

    rep = explain_ses([2, 4, 6, 8], 3, 0.5)
    assert set(rep) == set(ARITHMETIC_FIELDS)
    assert rep["inputs"]["alpha"] == 0.5
    assert rep["intermediates"] == {"final_level": 6.25}
    assert rep["result"] == forecast_ses([2, 4, 6, 8], 3, 0.5)

    rep = explain_damped_trend([2, 4, 6, 8], 3, 0.5, 0.5, 0.5)
    assert set(rep) == set(ARITHMETIC_FIELDS)
    assert rep["intermediates"] == {
        "final_level": 6.6875,
        "final_trend": 1.53125,
        "damping_sums": [0.5, 0.75, 0.875],
    }
    assert rep["result"] == forecast_damped_trend([2, 4, 6, 8], 3, 0.5, 0.5, 0.5)

    rep = explain_seasonal(PATTERN * 3, 12)
    assert set(rep) == set(ARITHMETIC_FIELDS)
    assert rep["inputs"]["season"] == 12
    assert rep["intermediates"]["seasons_used"] == 3
    assert rep["intermediates"]["overall_mean"] == approx(PATTERN_MEAN)
    assert rep["intermediates"]["level"] == approx(PATTERN_MEAN)
    assert rep["intermediates"]["indices"] == approx(
        [p / PATTERN_MEAN for p in PATTERN]
    )
    assert rep["result"] == forecast_seasonal(PATTERN * 3, 12)

    rep = explain_sba(SBA_HIST, 2, 0.2)
    assert set(rep) == set(ARITHMETIC_FIELDS)
    assert rep["intermediates"]["first_non_zero_index"] == 2
    assert rep["intermediates"]["final_size"] == approx(4.08)
    assert rep["intermediates"]["final_interval"] == approx(2.28)
    assert rep["result"] == forecast_sba(SBA_HIST, 2, 0.2)

    rep = explain_sba([0.0] * 12, 2)
    assert rep["intermediates"]["first_non_zero_index"] is None
    assert rep["result"] == [0.0, 0.0]

    # ------------------------------------------------------------------
    # AC-2: choose_forecast — result shape and the rule cases.
    # ------------------------------------------------------------------

    # no_demand gets no forecast at all.
    assert choose_forecast([0.0] * 12, "no_demand") is None
    assert choose_forecast([1.0, 2.0, 3.0], "no_demand") is None

    # Fewer than 12 buckets: mean, no backtest. The mean of 1..11 is
    # 66/11 = 6.
    short = choose_forecast(list(range(1, 12)), "smooth")
    assert short["method"] == "mean"
    assert short["params"] == {}
    assert short["history_buckets"] == 11
    assert short["backtest"] is None
    assert short["forecast"] == [6.0] * 12
    assert short["arithmetic"].startswith("mean: ")
    assert "mean=6" in short["arithmetic"]

    # Result shape: the frozen key set, horizon-many forecasts, and the
    # backtest key set whenever a backtest ran.
    assert set(short) == RESULT_KEYS
    assert len(short["forecast"]) == 12

    # A strongly seasonal 36-bucket history picks seasonal with a
    # perfect backtest: its six one-bucket-ahead errors are all 0
    # while the mean's window always averages to 4.75, giving errors
    # 1.75, 1.25, 4.25, 0.75, 2.25, 2.75 — baseline 13/6.
    seasonal_pick = choose_forecast(PATTERN * 3, "smooth")
    assert seasonal_pick["method"] == "seasonal"
    assert seasonal_pick["params"] == {}
    assert seasonal_pick["history_buckets"] == 36
    assert seasonal_pick["forecast"] == [float(p) for p in PATTERN]
    backtest = seasonal_pick["backtest"]
    assert set(backtest) == BACKTEST_KEYS
    assert backtest["mae"] == 0.0
    assert backtest["baseline_mae"] == approx(13 / 6)
    assert backtest["wape"] == 0.0
    assert backtest["bias"] == 0.0
    assert seasonal_pick["arithmetic"].startswith("seasonal: ")
    assert "level=4.75" in seasonal_pick["arithmetic"]
    # A custom horizon changes only the forecast length.
    assert len(choose_forecast(PATTERN * 3, "smooth", horizon=5)["forecast"]) == 5

    # A trending history is won by damped_trend or ses, and the winner
    # must beat the mean baseline.
    trending = choose_forecast(list(range(5, 17)), "smooth")
    assert trending["method"] in ("damped_trend", "ses")
    assert trending["backtest"]["mae"] < trending["backtest"]["baseline_mae"]
    assert all(value >= 0.0 for value in trending["forecast"])

    # Lumpy and intermittent: only mean or sba can be picked.
    lumpy = choose_forecast(LUMPY, "lumpy")
    assert lumpy["method"] in ("mean", "sba")
    assert lumpy["params"] == {}
    assert lumpy["backtest"]["mae"] < lumpy["backtest"]["baseline_mae"] or (
        lumpy["method"] == "mean"
    )
    intermittent = choose_forecast(LUMPY, "intermittent")
    assert intermittent["method"] in ("mean", "sba")

    # ------------------------------------------------------------------
    # AC-2 + the tie rule, worked example #1: [4]*11 + [8] (smooth).
    # Every candidate forecasts 4 for the first five backtest buckets
    # (all-zero errors) and 4 against the final actual of 8, so every
    # score is exactly (5*0 + 4)/6 = 2/3. The tie goes to the earlier
    # method: mean. wape is 4/28 = 1/7 and bias (24-28)/28 = -1/7.
    # ------------------------------------------------------------------
    tie = choose_forecast([4] * 11 + [8], "smooth")
    assert tie["method"] == "mean"
    assert tie["params"] == {}
    assert tie["forecast"] == [52 / 12] * 12
    backtest = tie["backtest"]
    assert backtest["mae"] == approx(4 / 6)
    assert backtest["baseline_mae"] == approx(4 / 6)
    assert backtest["wape"] == approx(1 / 7)
    assert backtest["bias"] == approx(-1 / 7)
    assert tie["arithmetic"].startswith("mean: ")
    assert "mean=4.333333333" in tie["arithmetic"]

    # Worked example #2: [2]*6 + [5]*6 (smooth). The damped_trend
    # backtest errors start |5 - 2| = 3 (a flat prefix: level 2, trend
    # 0) and |5 - 3.962| = 1.038 (level 3.8, trend 0.18 after the first
    # 5), and the full score 0.79911518... beats the mean baseline
    # 20417/9240 = 2.20963203... — a non-trivial winner.
    step = choose_forecast([2] * 6 + [5] * 6, "smooth")
    assert step["method"] == "damped_trend"
    assert step["params"] == {"alpha": 0.6, "beta": 0.1}
    backtest = step["backtest"]
    assert backtest["mae"] == approx(0.7991151800577123)
    assert backtest["baseline_mae"] == approx(20417 / 9240)
    assert backtest["wape"] == approx(0.15982303601154246)
    assert backtest["bias"] == approx(-0.12360016398845748)
    assert backtest["mae"] < backtest["baseline_mae"]
    assert step["arithmetic"].startswith("damped_trend: ")
    assert "alpha=0.6" in step["arithmetic"]
    assert "beta=0.1" in step["arithmetic"]

    # ------------------------------------------------------------------
    # AC-4 / AC-6: candidates that cannot be backtested are excluded,
    # never raised; the selection stays deterministic.
    # ------------------------------------------------------------------

    # 24 buckets: seasonal is a candidate (>= 24) but its six backtest
    # prefixes (18..23 buckets) are all too short, so it is not
    # eligible and must not be picked — even though the data is
    # perfectly seasonal. The mean's window is a full cycle at every
    # step (sum 57, mean 4.75): errors 1.75, 1.25, 4.25, 0.75, 2.25,
    # 2.75, so the winner is mean with mae 13/6.
    n24 = choose_forecast(PATTERN * 2, "smooth")
    assert n24["method"] == "mean"
    assert n24["backtest"] is not None
    assert n24["backtest"]["mae"] == approx(13 / 6)
    assert n24 == choose_forecast(PATTERN * 2, "smooth")

    # 28 buckets: the two earliest prefixes (22, 23) are still too
    # short for seasonal, so it stays excluded; the mean's sliding
    # window keeps summing to 57, giving errors 2.25, 2.75, 5.25,
    # 4.75, 0.25, 2.75 — mae 18/6 = 3.
    n28 = choose_forecast(PATTERN * 2 + PATTERN[:4], "smooth")
    assert n28["method"] in ("mean", "ses", "damped_trend")
    assert n28["method"] == "mean"
    assert n28["backtest"]["mae"] == approx(3.0)
    assert n28 == choose_forecast(PATTERN * 2 + PATTERN[:4], "smooth")

    # 29 buckets: the first prefix has 23 buckets — still ineligible.
    n29 = choose_forecast(PATTERN * 2 + PATTERN[:5], "smooth")
    assert n29["method"] in ("mean", "ses", "damped_trend")
    assert n29["backtest"]["mae"] == approx(19 / 6)
    assert n29 == choose_forecast(PATTERN * 2 + PATTERN[:5], "smooth")

    # 30 buckets: every backtest prefix has 24+ buckets, so seasonal
    # is eligible — and it wins with a perfect backtest.
    n30 = choose_forecast(PATTERN * 2 + PATTERN[:6], "smooth")
    assert n30["method"] == "seasonal"
    assert n30["backtest"]["mae"] == 0.0

    # ------------------------------------------------------------------
    # Hostile input: ValueError (catchable), never a crash or NaN.
    # ------------------------------------------------------------------
    for hostile in (
        [],  # empty
        "123456789012",  # a string is not a list of quantities
        [1.0] * 11 + ["x"],  # non-numeric entry
        [float("nan")] + [1.0] * 11,  # NaN
        [float("inf")] + [1.0] * 11,  # infinite
        [None] + [1.0] * 11,  # None entry
        [1.0, True] + [1.0] * 10,  # a boolean is not a quantity
    ):
        with pytest.raises(ValueError):
            choose_forecast(hostile, "smooth")

    with pytest.raises(ValueError):
        choose_forecast([1.0] * 12, "not-a-class")
    with pytest.raises(ValueError):
        choose_forecast([1.0] * 12, "smooth", horizon=0)

    # A huge-but-finite quantity is forecast, not a 500: the winner is
    # one of the smooth candidates and every value stays finite.
    huge = choose_forecast([1e300] * 12, "smooth")
    assert huge["method"] in ("mean", "ses", "damped_trend", "seasonal")
    assert all(math.isfinite(v) and v >= 0.0 for v in huge["forecast"])
