"""Issue #763 proof: the shared tolerant unit ceiling.

The proof covers, in one test, without the database:

* the helper: ``app.units.ceil_units`` is ``math.ceil(round(x, 9))`` —
  ``ceil_units(sum([0.7] * 10))`` is 7 (the float sum overshoots 7),
  ``ceil_units(7.0)`` is 7, ``ceil_units(7.000001)`` is 8 (a real
  overshoot still ceilings up), ``ceil_units(6.2)`` is 7 and
  ``ceil_units(0.0)`` is 0;
* the reorder point: ``app.recommend.reorder.recommend`` given inputs
  whose exact reorder point is an integer (0.28 * 50 = 14) but whose
  float sum overshoots it (14.000000000000002) returns
  ``reorder_point_units`` equal to that integer (14, not 15);
* the DDMRP zones: a deterministic case that derives the red / yellow /
  green and top quantities in exact arithmetic from the ``ddmrp.*``
  registry default factors (adu 0.7, lead time 10 days, short lead-time
  and low variability factors, no order cycle): the exact red (7) and
  the exact red + yellow (14) are integers, and ``buffer_zones``
  returns ``top_of_red`` 7 and ``top_of_yellow`` 14 — the shared
  tolerant ceiling gives the exact integer when the exact sum is
  integral — with ``top_of_green`` the tolerant ceiling of the exact
  red + yellow + green (98/5 -> 20);
* the call sites: each named module (``app.recommend.reorder``,
  ``app.recommend.empirical``, ``app.recommend.order_quantity``,
  ``app.ddmrp.zones``, ``app.ddmrp.plan``) imports and uses
  ``ceil_units``, read from the source — and the non-quantity
  ceilings (the empirical lead-time span and quantile rank, the plan's
  projection arrival days) still use ``math.ceil``.
"""

import inspect
from fractions import Fraction

from app.ddmrp import plan as ddmrp_plan
from app.ddmrp import zones as ddmrp_zones
from app.ddmrp.zones import buffer_zones
from app.recommend import empirical, order_quantity, reorder
from app.recommend.reorder import recommend
from app.settings.registry import get_setting
from app.units import ceil_units


def test_issue763_freeform():
    # The helper: a float sum within 1e-9 of an integer rounds to that
    # integer before the ceiling; every other value behaves as math.ceil.
    assert ceil_units(sum([0.7] * 10)) == 7
    assert ceil_units(7.0) == 7
    assert ceil_units(7.000001) == 8
    assert ceil_units(6.2) == 7
    assert ceil_units(0.0) == 0

    # The reorder point: the exact reorder point is the integer 14
    # (0.28 * 50, zero demand and lead-time variability), but the float
    # sum d * lt overshoots it — the tolerant ceiling must still return
    # the integer.
    result = recommend(
        {
            "mean_daily": 0.28,
            "std_daily": 0.0,
            "demand_class": "smooth",
            "recommendable": True,
        },
        {"mean_days": 50.0, "std_days": 0.0, "n_used": 5, "low_confidence": False},
    )
    assert result["status"] == "ok"
    assert Fraction(str(0.28)) * 50 == 14
    assert result["reorder_point"] > 14
    assert result["reorder_point_units"] == 14
    assert result["safety_stock_units"] == 0

    # The DDMRP zones, derived exactly from the registry default
    # factors: adu 0.7, lead time 10 days (short), low variability
    # (smooth demand, cv 0.3), no order cycle (green = red base, MOQ 0).
    lead_factor = Fraction(str(get_setting("ddmrp.lead_time_factor_short").default))
    variability_factor = Fraction(
        str(get_setting("ddmrp.variability_factor_low").default)
    )
    adu = Fraction(7, 10)
    dlt = Fraction(10)
    red_base = adu * dlt * lead_factor
    red_exact = red_base + red_base * variability_factor
    yellow_exact = adu * dlt
    green_exact = max(red_base, Fraction(0))
    top_of_yellow_exact = red_exact + yellow_exact
    top_of_green_exact = top_of_yellow_exact + green_exact
    assert red_exact.denominator == 1
    assert top_of_yellow_exact.denominator == 1

    zones = buffer_zones(0.7, 10, None, "smooth", 0.3)
    # The shared tolerant ceiling returns the exact integer when the
    # exact sum is integral (the float sums overshoot 7 and 14).
    assert zones["top_of_red"] == int(red_exact)
    assert zones["top_of_yellow"] == int(top_of_yellow_exact)
    assert zones["top_of_green"] == ceil_units(float(top_of_green_exact))

    # The call sites: each named module imports and uses ceil_units,
    # read from the source.
    for module in (reorder, empirical, order_quantity, ddmrp_zones, ddmrp_plan):
        source = inspect.getsource(module)
        assert "from app.units import ceil_units" in source
        assert "ceil_units(" in source
        assert module.ceil_units is ceil_units

    # The non-quantity ceilings stay on math.ceil: the empirical
    # lead-time span and quantile rank, the plan's projection arrival
    # days.
    empirical_source = inspect.getsource(empirical)
    assert "window_days = max(1, math.ceil(lead))" in empirical_source
    assert "k = max(1, min(spans, math.ceil(service * spans)))" in empirical_source
    assert "timedelta(days=math.ceil(dlt_days))" in inspect.getsource(ddmrp_plan)
