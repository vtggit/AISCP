"""Issue #1599 proof: the sba interval starts at the first demand's
position.

The smoothed interval in ``explain_sba`` starts at the first non-zero
quantity's position counted from 1 (``first_index + 1`` as a float),
not at 1; everything else in the method is unchanged:

* a late first demand — ``[0, 0, 0, 4, 0, 0, 0, 4]`` with the default
  alpha 0.1: the interval starts at 4 (the first non-zero's position)
  and the second gap of 4 leaves it at ``0.1*4 + 0.9*4 = 4.0`` while
  the size stays at ``0.1*4 + 0.9*4 = 4.0``, so the forecast is
  ``(1 - 0.05) * 4 / 4 = 0.95`` per bucket;
* one demand — ``[0, 0, 6]``: a single non-zero keeps the interval at
  its position counted from 1 (3) and the size at the quantity, so the
  forecast is ``(1 - 0.05) * 6 / 3 = 1.9`` per bucket;
* an early first demand is unchanged — ``[2, 0, 2, 0, 2]`` starts its
  interval at 1 exactly as before: ``1 -> 0.1*2 + 0.9*1 = 1.1 ->
  0.1*2 + 0.9*1.1 = 1.19`` with the size at 2, so the forecast is
  ``(1 - 0.05) * 2 / 1.19``;
* no demand — ``[0, 0, 0]`` forecasts zeros and leaves the
  intermediates null;
* ``FORMULAS["sba"]`` says the interval starts at the first non-zero
  quantity's position, counted from 1;
* a hostile history with a huge integer quantity (``10**400``), whose
  float conversion overflows, still raises the catchable
  ``ValueError`` (never a crash/500) on ``explain_sba`` and
  ``forecast_sba`` alike.

The proof reads the representation strictly —
``rep["intermediates"]["final_interval"]``, never ``.get`` with a
default — and checks that ``forecast_sba(history, h)`` equals
``explain_sba(history, h)["result"]`` for each history above.
"""

import pytest

from app.forecast import explain_sba, forecast_sba
from app.forecast.methods import FORMULAS


def test_issue1599_freeform():
    # A late first demand: the interval starts at the first
    # non-zero's position (4); the gap of 4 leaves it at 4.0 and the
    # size at 4.0, so the forecast is 0.95 per bucket.
    rep = explain_sba([0, 0, 0, 4, 0, 0, 0, 4], 3)
    assert rep["intermediates"]["final_interval"] == pytest.approx(4.0, abs=1e-9)
    assert rep["intermediates"]["final_size"] == pytest.approx(4.0, abs=1e-9)
    assert rep["result"] == pytest.approx([0.95, 0.95, 0.95], abs=1e-9)

    # One demand: a single non-zero's interval is its position counted
    # from 1 (3), so the forecast is (1 - 0.05) * 6 / 3 = 1.9.
    rep = explain_sba([0, 0, 6], 2)
    assert rep["intermediates"]["first_non_zero_index"] == 2
    assert rep["intermediates"]["final_interval"] == pytest.approx(3.0, abs=1e-9)
    assert rep["result"] == pytest.approx([1.9, 1.9], abs=1e-9)

    # An early first demand is unchanged: 1 -> 1.1 -> 1.19, size 2.
    rep = explain_sba([2, 0, 2, 0, 2], 1)
    assert rep["intermediates"]["final_interval"] == pytest.approx(1.19, abs=1e-9)
    assert rep["result"][0] == pytest.approx(0.95 * 2 / 1.19, abs=1e-9)

    # No demand: zeros, and the interval stays null.
    rep = explain_sba([0, 0, 0], 2)
    assert rep["result"] == [0.0, 0.0]
    assert rep["intermediates"]["final_interval"] is None

    # The formula text names the new start and drops the old one.
    assert "counted from 1" in FORMULAS["sba"]
    assert "starts at 1)" not in FORMULAS["sba"]

    # A huge int overflows the float conversion: it must surface as
    # the catchable ValueError, not a crash (500) at the API edge.
    with pytest.raises(ValueError):
        explain_sba([0, 0, 10**400], 2)
    with pytest.raises(ValueError):
        forecast_sba([10**400, 1, 1], 2)

    # forecast_sba agrees with explain_sba on every history above.
    for history, horizon in (
        ([0, 0, 0, 4, 0, 0, 0, 4], 3),
        ([0, 0, 6], 2),
        ([2, 0, 2, 0, 2], 1),
        ([0, 0, 0], 2),
    ):
        assert forecast_sba(history, horizon) == explain_sba(history, horizon)["result"]
