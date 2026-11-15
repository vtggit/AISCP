"""The safety-stock cost curve of the service levels, per ABC class.

``service_level_curve`` reads the reorder recommendations once (``app
.recommend.reorder.recommendations_for_all`` without an as-of) and the
stored system settings once (that module's ``_stored_system_settings``)
and answers, for each ABC class, how much safety stock — in units, in
money and in yearly holding cost — each candidate service level would
hold. It writes nothing and calls no ERP.

The class of an item-site is computed exactly as ``app.recommend
.abc.abc_for_all`` does: every recommendation row is classified with
``app.recommend.abc.classify``, priced with that module's
``_annual_value`` (a year of its daily demand at its unit cost for an
``"ok"`` recommendation, unclassified otherwise). A row counts for the
curve when its ``status`` is ``"ok"``, its class is ``"A"``, ``"B"`` or
``"C"``, its ``unit_cost`` is a number above 0 and its ``inputs``
carry ``d``, ``sd_d``, ``lt`` and ``sd_lt`` as finite numbers of at
least 0; its sigma is the square root of ``lt * sd_d^2 + d^2 * sd_lt^2``.

The fill rate of a point uses the counted rows that take part: a
counted row takes part when its ``order_quantity`` is an object whose
``order_quantity`` is a finite number above 0 — that number is its Q,
and the row's yearly demand D is 365 times ``inputs.d``.  At a level
with z, the row's expected units short per cycle are sigma times
G(z), where G(z) is the standard normal loss function — the standard
normal density at z minus z times one minus the standard normal
distribution at z, both from ``statistics.NormalDist()``.  The
class' point ``expected_fill_rate`` is 1 minus the sum over the
taking-part rows of sigma times G(z) times D divided by Q, divided by
the sum of their D, clamped to between 0 and 1 and rounded to 4
places; it is null when no row of the class takes part or when their
D sums to 0.  The class' ``fill_rate_item_sites`` is the number of
its rows that take part.

The ladder of one class is 0.90, 0.92, 0.95, 0.97, 0.98, 0.99 and
0.995 plus the class' current level when that is not already one of
them, ascending; the z of a level is
``statistics.NormalDist().inv_cdf(level)``. At one level the class'
``safety_stock_units`` is the sum over its counted rows of z times
sigma, each rounded up to a whole number, and the
``safety_stock_value`` is the sum of z times sigma times
``unit_cost``, rounded to 2 places; the ``holding_cost`` is that value
times the holding rate, rounded to 2 places, and the
``holding_cost_delta`` the difference to the previous point's
``holding_cost`` (null for the first point).  The point at the class'
current level carries ``current`` true.

The mode changes the z of a taking-part row: when the answer's
``service_target`` is ``fill_rate``, at each level a counted row that
takes part and whose sigma is above 0 is computed with its own z in
place of the level's z — the z that ``app.recommend
.reorder._fill_rate_z(sigma, level, Q)`` returns, the rule the
reorder recommendations follow in that mode — in its share of
``safety_stock_units`` (that z times sigma, rounded up), in its share
of the ``safety_stock_value`` (that z times sigma times its unit
cost) and in its part of the expected shortage (sigma times G(that z)
times D divided by Q).  A counted row that does not take part, or
whose sigma is 0, keeps the level's z exactly; with ``service_target``
``cycle`` — which includes nothing stored — the answer is exactly the
cycle computation above.

The current level of a class is, with the
``replenishment.service_level_by_class`` switch on, that class' stored
or default level (``_class_service_level`` of the reorder module);
with it off every class' level is the single service level
(``_service_level_for``).  The holding rate is ``_holding_rate_for``.

``service_level_suggestion(target_fill_rate)`` reads what the curve
reads, once, and answers the cheapest combination of per-class service
levels whose expected fill rate reaches the target.  A combination is
one level for each of the classes A, B and C, each taken from that
class' ladder as the curve builds it (the seven base levels plus the
class' current level).  Its holding cost is the sum of the three
classes' curve-point holding costs at those levels, rounded to 2
places.  Its expected fill rate is 1 minus the sum over the
taking-part rows of all three classes of each row's expected shortage
at its class' level (the term the curve's ``expected_fill_rate``
sums), divided by the sum of those rows' yearly demand, clamped to
between 0 and 1 and rounded to 4 places; it is null when no row takes
part.  Both follow the service target in force, exactly as the curve
does, and the target comparison uses the unrounded aggregate before
rounding.  The sums a combination needs are worked out once for each
class and ladder level, so the combinations are compared from those
sums and no row is solved again for each combination; it writes
nothing and calls no ERP.
"""

from __future__ import annotations

import math
import statistics

from app.recommend.abc import _annual_value, classify
from app.recommend.reorder import (
    SERVICE_TARGET_FILL_RATE,
    _class_levels_enabled,
    _class_service_level,
    _fill_rate_z,
    _holding_rate_for,
    _service_level_for,
    _service_target_for,
    _stored_system_settings,
    recommendations_for_all,
)

CLASSES = ("A", "B", "C")
BASE_LEVELS = (0.90, 0.92, 0.95, 0.97, 0.98, 0.99, 0.995)


def _finite_non_negative(value: object) -> float | None:
    """The value as a finite number of at least 0, else None.

    Only real numbers count — an ``int`` or ``float`` that is finite
    and not negative.  Anything else (``None``, bools, strings — even
    ones that would parse, such as ``"5"`` — and other non-numeric
    values) is not a finite number and simply does not count; nothing
    ever raises, not even an int too large to convert to a float.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _sigma(inputs: object) -> float | None:
    """The row's sigma, or None when the inputs do not count.

    ``sqrt(lt * sd_d^2 + d^2 * sd_lt^2)`` when ``inputs`` is a mapping
    whose ``d``, ``sd_d``, ``lt`` and ``sd_lt`` are all finite numbers
    of at least 0 and the radicand stays finite; None otherwise — a
    row whose inputs overflow the formula is never counted, never a
    raised math domain error or ``OverflowError``.  When ``d * d``
    alone overflows, ``d^2 * sd_lt^2`` is computed as
    ``(d * sd_lt)^2`` instead: ``d * sd_lt`` staying finite is
    exactly when ``d^2 * sd_lt^2`` is finite, so a finite radicand
    is never rejected for that intermediate overflow.
    """
    if not isinstance(inputs, dict):
        return None
    daily = _finite_non_negative(inputs.get("d"))
    sd_daily = _finite_non_negative(inputs.get("sd_d"))
    lead_time = _finite_non_negative(inputs.get("lt"))
    sd_lead_time = _finite_non_negative(inputs.get("sd_lt"))
    if daily is None or sd_daily is None or lead_time is None or sd_lead_time is None:
        return None
    daily_squared = daily * daily
    if math.isfinite(daily_squared):
        # The bit-exact path the curve always took.
        sd_lt_term = daily_squared * sd_lead_time * sd_lead_time
    else:
        # d * d overflowed, but d^2 * sd_lt^2 = (d * sd_lt)^2 can
        # still be finite: compute it as the square of d * sd_lt.
        product = daily * sd_lead_time
        if not math.isfinite(product):
            return None
        sd_lt_term = product * product
    radicand = lead_time * sd_daily * sd_daily + sd_lt_term
    if not math.isfinite(radicand):
        return None
    return math.sqrt(radicand)


def _fill_rate_quantity(order_quantity: object) -> float | None:
    """The row's fill-rate quantity Q, or None when the row does not
    take part.

    ``order_quantity["order_quantity"]`` as a finite number above 0
    when ``order_quantity`` is an object (a mapping) carrying one
    there; None otherwise — ``None``, non-mappings, bools and
    strings (even ones that would parse, such as ``"100"``), NaN,
    infinities and numbers at or below 0 all do not take part;
    nothing ever raises, not even an int too large to convert to a
    float.
    """
    if not isinstance(order_quantity, dict):
        return None
    value = order_quantity.get("order_quantity")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _row_z(
    service_target: str,
    z: float,
    level: float,
    sigma: float,
    quantity: float | None,
) -> float:
    """The z one counted row is computed with at the level.

    The level's z, exactly as the curve has always computed it —
    except when the answer's ``service_target`` is ``fill_rate`` and
    the row takes part in the fill rate (its Q is a finite number
    above 0) with a sigma above 0: then the row is computed with the
    z the reorder recommendations follow in that mode,
    ``app.recommend.reorder._fill_rate_z(sigma, level, Q)``.
    """
    if (
        service_target == SERVICE_TARGET_FILL_RATE
        and quantity is not None
        and sigma > 0
    ):
        return _fill_rate_z(sigma, level, quantity)
    return z


def _fill_rate_terms(
    normal: statistics.NormalDist,
    items: list[tuple[float, float, float, float]],
    scale: float,
) -> tuple[float, float, bool]:
    """The taking-part rows' scaled sums at one level, and whether a
    row's G(z) does not stay finite.

    ``items`` are (sigma, yearly demand ``D``, order quantity ``Q``
    and the z the row is computed with); ``scale`` is one number above
    0 dividing every ``D`` — the quotient of the two sums is
    unchanged, and with the scale the largest ``D`` every scaled
    demand stays at most 1.  Returns ``(shortage, demand,
    loss_nonfinite)``: ``shortage`` the sum over the rows with a
    finite ``D`` of sigma times ``G(z)`` times ``D`` divided by
    ``scale`` divided by ``Q`` (an infinite row term stays infinite),
    ``demand`` the sum of their ``D`` divided by ``scale``, and
    ``loss_nonfinite`` true when any such row's ``G(z)`` is not
    finite — the caller's rate is then null.  Nothing ever raises.
    """
    shortage = 0.0
    demand = 0.0
    for sigma, yearly, quantity, row_z in items:
        if not math.isfinite(yearly):
            continue
        loss = normal.pdf(row_z) - row_z * (1.0 - normal.cdf(row_z))
        if not math.isfinite(loss):
            return shortage, demand, True
        shortage += (sigma * loss * (yearly / scale)) / quantity
        demand += yearly / scale
    return shortage, demand, False


def _expected_fill_rate(
    normal: statistics.NormalDist,
    items: list[tuple[float, float, float, float]],
) -> float | None:
    """The class' expected fill rate at the level, or None.

    1 minus the sum over the taking-part rows (``sigma``, yearly
    demand ``D``, order quantity ``Q`` and the z the row is computed
    with) of ``sigma`` times ``G(z)`` times ``D`` divided by ``Q``,
    divided by the sum of their ``D``, where ``G(z)`` is the standard
    normal loss function — the standard normal density at ``z`` minus
    ``z`` times one minus the standard normal distribution at ``z``,
    both from ``statistics.NormalDist()``.  The rate is clamped to
    between 0 and 1 and rounded to 4 places: an overflow — one row's
    sigma times ``G(z)`` times ``D`` over ``Q``, or the sum of the
    rows, not staying finite — drives the unclamped rate to negative
    infinity, so the answer is the clamped number 0.0, never None.
    None only when no row takes part, when their ``D`` sums to 0, or
    when a row's ``G(z)`` does not stay finite; nothing ever raises.
    """
    if not items:
        return None
    # Scale every D and every term by the largest yearly demand: the
    # quotient of the two sums is unchanged and every scaled demand
    # stays at most 1, so the overflow can only come from one row's
    # sigma times G(z) over Q — an infinite shortage that clamps the
    # rate to 0.0 (the unclamped rate is then negative infinity,
    # never a number that must be dropped).
    max_yearly = 0.0
    for _, yearly, _, _ in items:
        if math.isfinite(yearly) and yearly > max_yearly:
            max_yearly = yearly
    if max_yearly <= 0:
        return None
    shortage, demand, loss_nonfinite = _fill_rate_terms(normal, items, max_yearly)
    if loss_nonfinite:
        return None
    rate = 1.0 - shortage / demand
    return round(min(1.0, max(0.0, rate)), 4)


def _level_terms(
    normal: statistics.NormalDist,
    service_target: str,
    class_rows: list[tuple[float, float, float, float | None]],
    level: float,
) -> tuple[list[int], list[float], list[tuple[float, float, float, float]]]:
    """One class' counted rows at one level, as their terms.

    Returns ``(unit_terms, value_terms, level_items)``: ``unit_terms``
    the ``ceil(row_z * sigma)`` of every counted row whose terms stay
    finite, ``value_terms`` the ``row_z * sigma * unit_cost`` of those
    rows, and ``level_items`` the taking-part rows (sigma, yearly
    demand, order quantity, the z the row is computed with) — the
    level's z, or the row's own ``_fill_rate_z`` in fill-rate mode.
    """
    z = normal.inv_cdf(level)
    unit_terms: list[int] = []
    value_terms: list[float] = []
    level_items: list[tuple[float, float, float, float]] = []
    for unit_cost, sigma, yearly, quantity in class_rows:
        row_z = _row_z(service_target, z, level, sigma, quantity)
        if quantity is not None:
            level_items.append((sigma, yearly, quantity, row_z))
        z_sigma = row_z * sigma
        value_term = z_sigma * unit_cost
        if not (math.isfinite(z_sigma) and math.isfinite(value_term)):
            continue
        unit_terms.append(math.ceil(z_sigma))
        value_terms.append(value_term)
    return unit_terms, value_terms, level_items


def _counted_rows(
    rows: list[dict],
) -> tuple[object, dict[str, list[tuple[float, float, float, float | None]]]]:
    """The counted rows per class, and the first counted row's as-of.

    The class of every recommendation row is computed exactly as
    ``abc_for_all`` does — ``classify`` over the ``_annual_value`` of
    each row — and a row counts when its ``status`` is ``"ok"``, its
    class is ``"A"``, ``"B"`` or ``"C"``, its ``unit_cost`` is a
    number above 0 and its ``inputs`` carry ``d``, ``sd_d``, ``lt``
    and ``sd_lt`` as finite numbers of at least 0.  Returns
    ``(as_of, counted)``: ``as_of`` the FIRST counted row's as-of
    (null when the first counted row carries a null as-of — never a
    later row's — and null when no row counts), and ``counted``
    (unit cost, sigma, yearly demand, fill-rate quantity) of every
    counted row of the class — the yearly demand is 365 times the
    row's daily demand and the fill-rate quantity is its order
    quantity Q (None when the row does not take part in the fill
    rate).
    """
    # The class of every recommendation row, exactly as abc_for_all
    # does: classify over the _annual_value of each row.
    items = [
        {
            "item_code": row["item_code"],
            "site_code": row["site_code"],
            "annual_value": _annual_value(row),
        }
        for row in rows
    ]
    class_by_key = {
        (classified["item_code"], classified["site_code"]): classified["abc_class"]
        for classified in classify(items)
    }
    # (unit cost, sigma, yearly demand, fill-rate quantity) of every
    # counted row of the class: the yearly demand is 365 times the
    # row's daily demand and the fill-rate quantity is its order
    # quantity Q (None when the row does not take part in the fill
    # rate).
    counted: dict[str, list[tuple[float, float, float, float | None]]] = {
        cls: [] for cls in CLASSES
    }
    as_of = None
    for row in rows:
        if row.get("status") != "ok":
            continue
        abc_class = class_by_key.get((row.get("item_code"), row.get("site_code")))
        if abc_class not in CLASSES:
            continue
        # A counted row needs a number above 0 for the unit cost —
        # like the inputs, a numeric string is not a number and does
        # not count.
        unit_cost = _finite_non_negative(row.get("unit_cost"))
        if unit_cost is None or unit_cost <= 0:
            continue
        inputs = row.get("inputs")
        sigma = _sigma(inputs)
        if sigma is None:
            continue
        # as_of is the FIRST counted row's, taken once — null when the
        # first counted row carries a null as_of (never a later
        # row's) and null when no row counts.
        if not any(counted.values()):
            as_of = row.get("as_of")
        # sigma is not None: the inputs are a dict whose d, sd_d, lt
        # and sd_lt are all finite numbers of at least 0, so the
        # yearly demand 365 * d is one float multiplication that
        # stays finite — if 365 * d overflowed, d^2 in the sigma's
        # radicand would be infinite already and the row would not
        # count.
        daily = _finite_non_negative(inputs.get("d"))
        counted[abc_class].append(
            (
                unit_cost,
                sigma,
                365.0 * (daily if daily is not None else 0.0),
                _fill_rate_quantity(row.get("order_quantity")),
            )
        )
    return as_of, counted


def _stored_answer() -> dict:
    """The settings read shared by the curve and the suggestion.

    One stored system settings read (``_stored_system_settings`` of
    the reorder module) and the values derived from it: ``by_class``
    (the ``replenishment.service_level_by_class`` value),
    ``service_target`` (the ``replenishment.service_target`` value —
    ``fill_rate`` when the stored value is ``fill_rate``, ``cycle``
    otherwise, including when nothing is stored), ``holding_rate``
    (the annual holding rate the costs are computed at) and
    ``current_levels`` — the class' stored or default level with
    ``by_class`` on, the single service level with it off.
    """
    stored = _stored_system_settings()
    by_class = _class_levels_enabled(stored)
    return {
        "by_class": by_class,
        "service_target": _service_target_for(stored),
        "holding_rate": _holding_rate_for(stored),
        "current_levels": {
            cls: (
                _class_service_level(stored, cls)
                if by_class
                else _service_level_for(stored)
            )
            for cls in CLASSES
        },
    }


def service_level_curve() -> dict:
    """The safety-stock cost curve of the service levels, per ABC class.

    One ``recommendations_for_all`` read (no as-of) and one stored
    system settings read (``_stored_system_settings`` of the reorder
    module); it writes nothing and calls no ERP.  Answers ``as_of``
    (the first counted row's, null when no row counts), ``by_class``
    (the ``replenishment.service_level_by_class`` value),
    ``service_target`` (the ``replenishment.service_target`` value —
    ``fill_rate`` when the stored value is ``fill_rate``, ``cycle``
    otherwise, including when nothing is stored) and ``holding_rate``
    and ``classes`` — one entry per class ``"A"``, ``"B"``, ``"C"``,
    in that order, each with ``abc_class``,
    ``item_sites`` (its counted rows), ``fill_rate_item_sites``
    (its counted rows that take part in the fill rate — the ones
    whose ``order_quantity`` is an object whose ``order_quantity`` is
    a finite number above 0), ``current_level`` and ``points`` — one
    per ladder level, each with ``level``, ``safety_stock_units``,
    ``safety_stock_value``, ``holding_cost``, ``holding_cost_delta``,
    ``expected_fill_rate`` (null when no row of the class takes part
    or their yearly demand sums to 0) and ``current``; a class with
    no counted row has ``item_sites`` 0, ``fill_rate_item_sites`` 0
    and points whose sums are 0 and whose ``expected_fill_rate`` is
    null.
    """
    rows = recommendations_for_all()
    answer = _stored_answer()
    by_class = answer["by_class"]
    service_target = answer["service_target"]
    holding_rate = answer["holding_rate"]
    current_levels = answer["current_levels"]
    as_of, counted = _counted_rows(rows)
    normal = statistics.NormalDist()
    classes: list[dict] = []
    for cls in CLASSES:
        current_level = current_levels[cls]
        levels = sorted(set(BASE_LEVELS) | {current_level})
        # The taking-part rows of the class: (sigma, yearly demand,
        # order quantity) of the counted rows whose order quantity is
        # a finite number above 0.
        fill_items = [
            (sigma, yearly, quantity)
            for unit_cost, sigma, yearly, quantity in counted[cls]
            if quantity is not None
        ]
        points: list[dict] = []
        previous_holding = None
        for level in levels:
            unit_terms, value_terms, level_items = _level_terms(
                normal, service_target, counted[cls], level
            )
            total = sum(value_terms)
            if math.isfinite(total):
                value = round(total, 2)
            else:
                # The finite terms sum to more than the largest
                # float: answer 0.0 (and no units) rather than a
                # non-finite value.
                value = 0.0
                unit_terms = []
            holding_cost = round(value * holding_rate, 2)
            delta = (
                None
                if previous_holding is None
                else round(holding_cost - previous_holding, 2)
            )
            points.append(
                {
                    "level": level,
                    "safety_stock_units": int(sum(unit_terms)),
                    "safety_stock_value": value,
                    "holding_cost": holding_cost,
                    "holding_cost_delta": delta,
                    "expected_fill_rate": _expected_fill_rate(normal, level_items),
                    "current": level == current_level,
                }
            )
            previous_holding = holding_cost
        classes.append(
            {
                "abc_class": cls,
                "item_sites": len(counted[cls]),
                "fill_rate_item_sites": len(fill_items),
                "current_level": current_level,
                "points": points,
            }
        )
    return {
        "as_of": as_of,
        "by_class": by_class,
        "service_target": service_target,
        "holding_rate": holding_rate,
        "classes": classes,
    }


def service_level_suggestion(target_fill_rate: float) -> dict:
    """The cheapest combination of per-class service levels reaching a
    target expected fill rate.

    Reads what the curve reads, once — one
    ``recommendations_for_all`` read (no as-of) and one stored system
    settings read — and answers ``as_of``, ``service_target`` and
    ``holding_rate`` as the curve reports them, ``target_fill_rate``
    and the combinations ``current`` (the classes' current levels),
    ``highest`` (each class' highest ladder level) and ``suggested``
    (the combination with the lowest holding cost among those whose
    expected fill rate is at least the target, a tie going to the
    lower level for A, then for B, then for C; null when no
    combination reaches the target or no row takes part) plus
    ``holding_cost_delta`` — the suggested holding cost minus the
    current one rounded to 2 places, null when ``suggested`` is null.
    A combination is one level for each of the classes A, B and C,
    each from that class' ladder; its holding cost is the sum of the
    three classes' curve-point holding costs at those levels, rounded
    to 2 places, and its expected fill rate is 1 minus the sum over
    the taking-part rows of all three classes of each row's expected
    shortage at its class' level, divided by the sum of their yearly
    demand, clamped to between 0 and 1 and rounded to 4 places (null
    when no row takes part) — the target comparison uses the
    unrounded aggregate before rounding, and both follow the service
    target in force, exactly as the curve does.  The sums a
    combination needs are worked out once for each class and ladder
    level, so the combinations are compared from those sums and no
    row is solved again for each combination; it writes nothing and
    calls no ERP.
    """
    rows = recommendations_for_all()
    answer = _stored_answer()
    service_target = answer["service_target"]
    holding_rate = answer["holding_rate"]
    current_levels = answer["current_levels"]
    as_of, counted = _counted_rows(rows)
    normal = statistics.NormalDist()
    # The one scale dividing every taking-part row's yearly demand:
    # the largest such demand over all three classes (1 when none),
    # so every scaled demand stays at most 1 and the quotient of the
    # shortage and demand sums — of one class or of all three — is
    # unchanged by the scaling.
    max_yearly = 0.0
    for cls in CLASSES:
        for _unit_cost, _sigma, yearly, quantity in counted[cls]:
            if quantity is not None and math.isfinite(yearly) and yearly > max_yearly:
                max_yearly = yearly
    scale = max_yearly if max_yearly > 0 else 1.0
    # The sums a combination needs, once per class and ladder level:
    # the ladder, the curve-point holding cost of each level, the
    # scaled expected-shortage sum of each level, whether a taking-
    # part row's G(z) does not stay finite at the level, and the
    # class' scaled demand sum (level-independent).
    ladders: list[list[float]] = []
    holdings: list[list[float]] = []
    shortages: list[list[float]] = []
    losses_bad: list[list[bool]] = []
    demands: list[float] = []
    for cls in CLASSES:
        current_level = current_levels[cls]
        levels = sorted(set(BASE_LEVELS) | {current_level})
        class_rows = counted[cls]
        demand = 0.0
        for _unit_cost, _sigma, yearly, quantity in class_rows:
            if quantity is not None and math.isfinite(yearly):
                demand += yearly / scale
        class_holdings: list[float] = []
        class_shortages: list[float] = []
        class_losses_bad: list[bool] = []
        for level in levels:
            _unit_terms, value_terms, level_items = _level_terms(
                normal, service_target, class_rows, level
            )
            total = sum(value_terms)
            value = round(total, 2) if math.isfinite(total) else 0.0
            class_holdings.append(round(value * holding_rate, 2))
            if level_items:
                shortage, _level_demand, loss_bad = _fill_rate_terms(
                    normal, level_items, scale
                )
            else:
                shortage, loss_bad = 0.0, False
            class_shortages.append(shortage)
            class_losses_bad.append(loss_bad)
        ladders.append(levels)
        holdings.append(class_holdings)
        shortages.append(class_shortages)
        losses_bad.append(class_losses_bad)
        demands.append(demand)

    def combination(index: tuple[int, int, int]) -> tuple[float, float | None]:
        """The combination's holding cost and unrounded expected fill
        rate — None when no row takes part, their yearly demand sums
        to 0 or a taking-part row's G(z) does not stay finite; an
        infinite shortage clamps the rate to 0.0, as the curve does.
        """
        ia, ib, ic = index
        holding = round(holdings[0][ia] + holdings[1][ib] + holdings[2][ic], 2)
        if losses_bad[0][ia] or losses_bad[1][ib] or losses_bad[2][ic]:
            return holding, None
        demand_total = demands[0] + demands[1] + demands[2]
        if demand_total <= 0:
            return holding, None
        shortage_total = shortages[0][ia] + shortages[1][ib] + shortages[2][ic]
        rate = 1.0 - shortage_total / demand_total
        return holding, min(1.0, max(0.0, rate))

    def body(index: tuple[int, int, int]) -> dict:
        holding, rate = combination(index)
        return {
            "levels": {
                "A": ladders[0][index[0]],
                "B": ladders[1][index[1]],
                "C": ladders[2][index[2]],
            },
            "expected_fill_rate": None if rate is None else round(rate, 4),
            "holding_cost": holding,
        }

    current_index = tuple(
        ladders[n].index(current_levels[cls]) for n, cls in enumerate(CLASSES)
    )
    highest_index = (
        len(ladders[0]) - 1,
        len(ladders[1]) - 1,
        len(ladders[2]) - 1,
    )
    current_body = body(current_index)
    # The ladders are ascending, so walking A, then B, then C in
    # order and replacing only on a strictly lower holding cost
    # leaves the tie to the lower level for A, then for B, then for
    # C.
    best: tuple[float, tuple[int, int, int]] | None = None
    for ia in range(len(ladders[0])):
        for ib in range(len(ladders[1])):
            for ic in range(len(ladders[2])):
                holding, rate = combination((ia, ib, ic))
                if rate is None or rate < target_fill_rate:
                    continue
                if best is None or holding < best[0]:
                    best = (holding, (ia, ib, ic))
    suggested_body = None
    delta = None
    if best is not None:
        suggested_body = body(best[1])
        delta = round(best[0] - current_body["holding_cost"], 2)
    return {
        "as_of": as_of,
        "service_target": service_target,
        "holding_rate": holding_rate,
        "target_fill_rate": target_fill_rate,
        "current": current_body,
        "highest": body(highest_index),
        "suggested": suggested_body,
        "holding_cost_delta": delta,
    }
