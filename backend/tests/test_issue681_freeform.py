"""Issue #681 proof: the empirical lead-time demand reorder point.

AC-1 — ``app.recommend.empirical`` is a new, database-free module:
``METHOD = "empirical lead-time demand"`` and one pure function,
``empirical_reorder_point(daily_totals, window_start, as_of,
lead_time_days, service_level)``.  The days run from ``window_start``
through ``as_of`` inclusive, a day's demand is
``daily_totals.get(day, 0.0)``, ``L = max(1, ceil(lead_time_days))``
and the ``n`` spans are the sums of the ``L`` days starting at each
start day from the first through the (number of days - L + 1)-th.
The reorder point is the ``ceil(service_level * n)``-th smallest span
total (k clamped to 1..n), floored at 0.0; ``mean_lead_time_demand``
is the mean of the span totals and ``safety_stock`` is
``max(0, reorder point - mean)``, each with a whole-unit
``math.ceil`` twin and a one-line ``arithmetic`` of the formula with
the numbers substituted.  A service level not strictly between 0 and
1 raises ``ValueError``; a negative, NaN or infinite lead time — and
a window of fewer than L days — answers ``None``.

AC-2 — this proof.  The window is 2026-01-01 through 2026-01-20
(20 days) with demand 6 on 2026-01-05 and 9 on 2026-01-12, and
``lead_time_days`` 2.4 (L = 3, n = 18 spans: twelve 0s, three 6s and
three 9s, mean 2.5).  At service level 0.95 (k = ceil(17.1) = 18)
the reorder point is 9.0 (9 units), the mean lead-time demand 2.5
and the safety stock 6.5 (7 units), over 18 spans, 6 of them with
demand, in a 3-day window, with the arithmetic line exactly
"reorder point = the 95% quantile of 18 overlapping 3-day demand
totals = 9; mean 3-day demand = 2.50; safety stock = max(0, 9 -
2.50) = 6.50".  At 0.80 (k = 15) the reorder point is 6.0 and at
0.50 (k = 9) it is 0.0 with safety stock 0.0.  A 2026-01-01 through
2026-01-02 window (2 days, fewer than L = 3) returns None; the lead
times NaN, infinity and -1 each return None; the service levels 0,
1 and 1.2 each raise ValueError; and the result carries exactly the
eleven documented keys.  A stored non-finite demand is the day's
demand verbatim, never coerced to 0.0: over the 3-day window
2026-01-05 through 2026-01-07 (L = 3, one span) a NaN day makes the
mean lead-time demand NaN, the reorder point 0.0 and no span count
as having demand, while a stored infinity makes the reorder point
and the mean infinity.
"""

import math
from datetime import date

import pytest

from app.recommend.empirical import METHOD, empirical_reorder_point

WINDOW_START = date(2026, 1, 1)
WINDOW_END = date(2026, 1, 20)
DAILY_TOTALS = {date(2026, 1, 5): 6.0, date(2026, 1, 12): 9.0}
LEAD_TIME_DAYS = 2.4

EXPECTED_KEYS = {
    "method",
    "window_days",
    "spans",
    "spans_with_demand",
    "service_level",
    "reorder_point",
    "reorder_point_units",
    "mean_lead_time_demand",
    "safety_stock",
    "safety_stock_units",
    "arithmetic",
}

EXPECTED_ARITHMETIC = (
    "reorder point = the 95% quantile of 18 overlapping 3-day demand "
    "totals = 9; mean 3-day demand = 2.50; safety stock = max(0, 9 - "
    "2.50) = 6.50"
)


def test_issue681_freeform():
    result = empirical_reorder_point(
        DAILY_TOTALS, WINDOW_START, WINDOW_END, LEAD_TIME_DAYS, 0.95
    )
    assert set(result) == EXPECTED_KEYS
    assert result["method"] == METHOD == "empirical lead-time demand"
    assert result["window_days"] == 3
    assert result["spans"] == 18
    assert result["spans_with_demand"] == 6
    assert result["service_level"] == 0.95
    assert result["reorder_point"] == 9.0
    assert result["reorder_point_units"] == 9
    assert result["mean_lead_time_demand"] == 2.5
    assert result["safety_stock"] == 6.5
    assert result["safety_stock_units"] == 7
    assert result["arithmetic"] == EXPECTED_ARITHMETIC

    at_eighty = empirical_reorder_point(
        DAILY_TOTALS, WINDOW_START, WINDOW_END, LEAD_TIME_DAYS, 0.80
    )
    assert at_eighty["reorder_point"] == 6.0

    at_fifty = empirical_reorder_point(
        DAILY_TOTALS, WINDOW_START, WINDOW_END, LEAD_TIME_DAYS, 0.50
    )
    assert at_fifty["reorder_point"] == 0.0
    assert at_fifty["safety_stock"] == 0.0

    short_window = empirical_reorder_point(
        DAILY_TOTALS, date(2026, 1, 1), date(2026, 1, 2), LEAD_TIME_DAYS, 0.95
    )
    assert short_window is None

    for bad_lead_time in (math.nan, math.inf, -1):
        assert (
            empirical_reorder_point(
                DAILY_TOTALS, WINDOW_START, WINDOW_END, bad_lead_time, 0.95
            )
            is None
        )

    for bad_service_level in (0, 1, 1.2):
        with pytest.raises(ValueError):
            empirical_reorder_point(
                DAILY_TOTALS,
                WINDOW_START,
                WINDOW_END,
                LEAD_TIME_DAYS,
                bad_service_level,
            )

    # Review defect fix: a non-finite stored value is the day's demand
    # verbatim (daily_totals.get(day, 0.0)), never coerced to 0.0.  The
    # window is exactly L = 3 days, so there is a single span total;
    # with the NaN day it is NaN (a coerced zero would make it 9.0:
    # reorder point 9.0, mean 9.0, one span with demand).
    nan_totals = {
        date(2026, 1, 5): float("nan"),
        date(2026, 1, 6): 6.0,
        date(2026, 1, 7): 3.0,
    }
    nan_result = empirical_reorder_point(
        nan_totals, date(2026, 1, 5), date(2026, 1, 7), LEAD_TIME_DAYS, 0.95
    )
    assert nan_result is not None
    assert math.isnan(nan_result["mean_lead_time_demand"])
    assert nan_result["reorder_point"] == 0.0
    assert nan_result["spans_with_demand"] == 0

    inf_totals = {date(2026, 1, 5): float("inf")}
    inf_result = empirical_reorder_point(
        inf_totals, date(2026, 1, 5), date(2026, 1, 7), LEAD_TIME_DAYS, 0.95
    )
    assert inf_result is not None
    assert inf_result["reorder_point"] == math.inf
    assert inf_result["mean_lead_time_demand"] == math.inf
