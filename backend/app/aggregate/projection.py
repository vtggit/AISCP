"""Site-grouped roll-forward projection of the recommendable portfolio.

``aggregate_projection`` projects the portfolio's demand, supply and
on-hand forward over consecutive calendar-month buckets, grouped by
``site_code``.

The projected set and the per item-site daily demand ``d`` are reused
from ``app.recommend.reorder.recommendations_for_all``: only the
recommendable item-sites (``status "ok"``) enter a group — the same
intermittent/lumpy exclusions the recommendation already applies — and
each carries the active basis' daily demand ``inputs.d``.  The
rolled-forward on-hand of a group starts from the group's current
``on_hand`` total (one read) and every bucket applies
``prior + supply - demand``; supply is the sum of the group's open
purchase lines' ``open_units`` (one read) whose ``promised_on`` falls
in the bucket's month — a line promised outside the window contributes
to no bucket.  Demand is the group's summed ``d x days-in-month``
ceiled to whole units.  The dollar view sums each item-site's units at
its ``item_sites.unit_cost`` exactly as scenario planning does: an
item-site without a unit cost contributes zero and is counted in
``item_sites_without_unit_cost``.

The in-flight releases join the supply: one ``plan_releases`` read
(through the module-level ``_in_flight_release_lines``, on its own
connection) lists the
stored release rows, and a row is in-flight when its ``outcome`` is
"created" or "exists", its ``erp_status`` is null or none of
``FINAL_ERP_STATUSES`` (a finished or canceled release is not in
flight), its ``order_nbr`` is non-null and no row of
the newest ``open_purchase_lines`` snapshot carries that order number
(the ``business_key``'s second ``:``-separated part) — a released
order the feed already picked up is counted from the feed only, never
twice.  Each line of an in-flight row (``item_code``, ``quantity``,
``unit_cost``, at the row's ``site_code``) is on-order supply for
that item-site: it arrives on the first day of the row's ``month``
(the projection's start date when that day is earlier), is valued at
``quantity x unit_cost`` and from there behaves exactly like an open
purchase line — it counts in the monthly supply and its value lands
in the bucket's supply value, the review walk no longer plans an
order for that need, and
``app.aggregate.spend.spend_against_budget`` reports it as committed
spend (``planned_orders``, ``plan_release`` and
``deferral_candidates`` see the same supply).  A row whose item-site
is not recommendable is ignored, and a row with a malformed month or
snapshot contributes nothing (never a 500).

The stored demand signals lift the demand: the projected
item-sites' counted signals (``app.forecast.signals.counted_signals``)
and counted overrides (``app.forecast.overrides.counted_overrides``)
are each read once — one batched query each, on the normal
connection path like the setup's other reads — whatever the number
of item-sites.  Each item-site's signal day share is, for each day
after the resolved as-of date that a counted signal's window covers
and no counted override covers, the signal's quantity over the days
of its window (the stated quantity spread evenly over its window; a
day a counted override covers takes no signal share — the override
wins the day).  The month's units, ``signal_units(month)``, are the
item-site's day shares falling in that calendar month — days outside
the horizon (the buckets) count for nothing — and the bucket carries
the sum over its site group's item-sites as ``signal_units``.  In
the as-is roll each month's ``projected_demand_units`` and
``projected_demand_value`` include ``signal_units(month)`` at the
item-site's ``unit_cost`` by the existing rule, and
``projected_on_hand_units`` rolls subtracting that same demand
total.  A ``demand_multiplier`` never scales a signal (a signal is a
stated quantity), and a lever-scaled ``lt`` is the ``lt`` the
monthly review's cover window uses.  With no signals stored every
value is exactly what it is without them, field for field, with no
floating-point difference.

Each recommendable item-site also runs a monthly review over the
same buckets: the item-site's own start-of-month on-hand starts from
its current on-hand (0 when absent), and at the start of each bucket
month the inventory position is that start-of-month on-hand plus the
item-site's open purchase lines and in-flight release lines not yet
received in an earlier bucket (a line counts as received only in the
bucket of its ``promised_on`` month, exactly as the existing supply
rule; lines with no
``promised_on``, or promised before the first bucket or after the
last, stay on order throughout) plus its planned orders placed in
earlier buckets that have not yet arrived.  When the position is
strictly below the order-up-to level, ``S = rop + d x
days-in-month + extra + cover`` with ``rop`` the recommendation's
``reorder_point_units`` — its
``reorder_point_before_signals_units`` when the recommendation
carries it, because the projection counts the demand signals by
month itself and must not take them through the reorder point as
well — ``extra`` the review month's extra safety stock
``z x (sqrt((lt + days) x sd_d^2 + d^2 x sd_lt^2) - sqrt(lt x
sd_d^2 + d^2 x sd_lt^2))`` (``days`` the bucket month's days and
``d``, ``sd_d``, ``lt``, ``sd_lt`` and ``z`` the recommendation's
``inputs``, each non-finite or missing value taken as 0, as ``d``
and ``lt`` are, the scaled inputs and the scenario's ``z'`` for
every item-site when a lever is given, and 0 when no lever is given
and the recommendation's ``method`` is not
``app.recommend.reorder.METHOD`` — 0 when ``sd_d`` is 0, both roots
then holding the ``d^2 x sd_lt^2`` term alone) — and ``cover`` the
sum of the item-site's signal day shares
from the first day of that month through the last day of that month
plus ``ceil(lt)`` further days — the demand this order has to carry
until the next review's order can arrive; because the position
already holds the earlier planned orders, a signal an earlier
month's order carries does not raise a later order again — (``d`` is
``inputs.d`` and ``lt`` is ``inputs.lt`` in days, each non-finite or
missing value taken as 0, lever-scaled when a lever is given), one
planned order is placed for ``ceil(S - position)``
units; it arrives on the first day of the bucket month plus
``ceil(lt)`` days and counts as a receipt in the bucket whose month
contains that date, or in no bucket when the date is after the last
bucket (it then stays on order).  The item-site's next
start-of-month on-hand is this one plus that bucket's open-line
receipts plus that bucket's planned arrivals minus ``d x
days-in-month`` minus the item-site's ``signal_units(month)``.  The
method: a periodic-review order-up-to (R, S) policy — monthly
review, order-up-to the reorder point plus one month's demand plus
the review month's extra safety stock plus the signal cover,
arriving after the lead time.  Per site
group and bucket the new fields are ``planned_receipts_units`` (the
sum of the group's planned orders arriving in that bucket),
``planned_receipts_value`` (the same units at each item-site's
``unit_cost``, 0 for an item-site without one),
``planned_on_hand_units`` (that bucket's ``projected_on_hand_units``
plus the group's cumulative ``planned_receipts_units`` through that
bucket) and ``planned_shortfall`` (true when
``planned_on_hand_units`` is below zero); every existing field, value,
bound and order is unchanged.

The three optional levers ``service_level``, ``demand_multiplier``
and ``lead_time_multiplier`` carry exactly the meaning
``app.scenario.simulate.simulate_reorder`` gives them (``service_level``
a number strictly between 0 and 1, each multiplier a positive finite
number) and are validated with
``app.scenario.simulate._validate_levers`` before anything is read.
With any lever given every recommendable item-site's inputs are scaled
— ``d' = d x demand_multiplier``, ``sd_d' = sd_d x
demand_multiplier``, ``lt' = lt x lead_time_multiplier``, ``sd_lt' =
sd_lt x lead_time_multiplier`` (a lever not given counts as 1.0) — the
service level is the given one or else the baseline's
``inputs.service_level`` with ``z' =
NormalDist().inv_cdf(service_level')``, and the scenario reorder point
``_basis_result(d', sd_d', lt', sd_lt', z', service_level')["reorder_point_units"]``
replaces the recommendation's ``reorder_point_units`` (and the
review month's extra safety stock takes ``d'``, ``sd_d'``, ``lt'``,
``sd_lt'`` and ``z'`` for every item-site); the projection
then uses ``d'`` in place of ``d`` everywhere it uses ``d`` (the as-is
projected demand and its dollar value, the planned review's demand and
order-up-to level) and ``lt'`` in place of ``lt`` for planned
arrivals.  With no lever given the output is exactly what it is
without the levers, field for field; a scaled input that overflows the
formula raises ``ValueError`` (422 at the route), never a 500.

The computation is read-only and a fixed number of reads — one
``recommendations_for_all``, one ``on_hand`` read, one
``open_purchase_lines`` read, one ``counted_signals`` read, one
``counted_overrides`` read and one ``plan_releases`` read (the
in-flight supply) — whatever the number of item-sites or buckets, and
it changes no existing recommendation / drift / excess / stockout /
demand / scenario behaviour.  The excess threshold is NOT applied
here.

``planned_orders`` returns every planned order that monthly review
places within the horizon, in placement order (bucket by bucket in
month order, the site's item-sites in recommendation order, the sites
in ``site_code`` order).  Each entry carries ``item_code``,
``site_code``, ``placed_month`` (the "YYYY-MM" of the month the order
is placed), ``arrival_month`` (the "YYYY-MM" of the month the order
arrives, or None when it arrives after the horizon), ``quantity`` (the
ordered units), ``unit_cost`` (the item-site's ``unit_cost``, or None
when it has none), ``value`` (the quantity at that unit cost, 0
without one), ``d`` (the item-site's daily demand the review uses,
lever-scaled when a lever is given) and ``month_end_on_hand`` (the
item-site's own planned on-hand at the end of the arrival month after
that month's receipts and demand, exactly as the review rolls it, or
None when the arrival is outside the horizon).  It takes the same
arguments with the same validation and the same ``ValueError``s and is
produced by the same review walk ``aggregate_projection`` uses,
refactored into the shared internal helpers ``_projection_setup`` (the
validation, the six reads and the review state) and ``_monthly_review``
(the walk itself), so the two can never disagree; ``aggregate_projection``'s
output is exactly what it was, field for field.  Both public functions
are thin wrappers: each runs ``_projection_setup`` once and feeds the
already-built setup to the private helper that computes its view from a
setup — ``_aggregate_projection_from_setup`` for the projection,
``_planned_orders_from_setup`` for the orders — each walking
``_monthly_review`` over its own copy of the setup's review state, so a
setup is never mutated and the two consumers of one shared setup (as
``app.aggregate.deferral.deferral_candidates`` does: one setup feeding
both the spend view and the planned-order list) can never interfere.
"""

from __future__ import annotations

import bisect
import calendar
import datetime
import math
import re
import statistics
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import psycopg2.extras
from psycopg2 import connect as _psycopg2_connect

from app.db.connection import get_connection_params
from app.forecast.overrides import counted_overrides
from app.forecast.signals import counted_signals
from app.recommend.reorder import (
    METHOD,
    _basis_result,
    recommendations_for_all,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.scenario.simulate import _validate_levers

_on_hand_repo = OnHandPostgresRepository()
_open_po_repo = OpenPurchaseLinePostgresRepository()

MIN_MONTHS = 1
MAX_MONTHS = 12
DEFAULT_MONTHS = 6


def _finite(value: float | None) -> float:
    """The value as a finite float: 0.0 when absent or non-finite."""
    if value is None or not math.isfinite(value):
        return 0.0
    return float(value)


def _planned_arrival_index(
    year: int,
    month: int,
    lt: float,
    month_index: dict[tuple[int, int], int],
    last_month: tuple[int, int],
    bucket_index: int,
) -> int | None:
    """The bucket index whose month contains the planned order's
    arrival date (first day of the bucket month plus ``ceil(lt)``
    days), or None when the date is after the last bucket (the order
    then stays on order).  A date landing before the current bucket
    (only a negative ``lt`` can do that) counts as arriving in the
    current bucket.  A lead time so large that its arrival date is
    not a representable date (only a finite ``lt`` with a huge
    magnitude does that) is unambiguously outside the window: a
    positive one counts as arriving after the last bucket, a
    negative one as arriving in the current bucket."""
    try:
        arrival = date(year, month, 1) + timedelta(days=math.ceil(lt))
    except OverflowError:
        return None if lt > 0 else bucket_index
    arrival_key = (arrival.year, arrival.month)
    arrival_index = month_index.get(arrival_key)
    if arrival_index is None:
        arrival_index = bucket_index if arrival_key <= last_month else None
    if arrival_index is not None and arrival_index < bucket_index:
        arrival_index = bucket_index
    return arrival_index


def _bucket_months(as_of: date, months: int) -> list[tuple[int, int, str]]:
    """The (year, month, "YYYY-MM") of each consecutive calendar-month
    bucket, starting from the month of ``as_of``."""
    buckets: list[tuple[int, int, str]] = []
    for offset in range(months):
        index = as_of.year * 12 + (as_of.month - 1) + offset
        year, month = divmod(index, 12)
        buckets.append((year, month + 1, f"{year:04d}-{month + 1:02d}"))
    return buckets


# The release outcomes whose rows carry an order that exists in
# Acumatica but the open-purchase feed has not (yet) picked up:
# created (the draft-PO write landed) or exists (an order carried
# the origin when the batch ran).
_IN_FLIGHT_OUTCOMES = ("created", "exists")

# The erp statuses after which a release row is no longer in
# flight: exactly the three
# PlanReleasePostgresRepository.list_readback_order_nbrs stops
# reading the order for, so a row carrying one contributes no
# on-order supply.
FINAL_ERP_STATUSES = ("Completed", "Closed", "Canceled")

# A release row's month: the "YYYY-MM" of a real calendar month.
_RELEASE_MONTH_RE = re.compile(r"([0-9]{4})-([0-9]{2})")


def _release_month_first_day(month: object) -> date | None:
    """The first day of a release row's ``month`` ("YYYY-MM"), or
    None when the value is not a real calendar month (a hostile
    month can never raise)."""
    if not isinstance(month, str):
        return None
    match = _RELEASE_MONTH_RE.fullmatch(month)
    if match is None:
        return None
    year, month_number = int(match[1]), int(match[2])
    try:
        return date(year, month_number, 1)
    except ValueError:
        return None


def _release_number(value: object) -> float:
    """One release snapshot line's quantity or unit cost as a finite
    float: 0.0 when the value is absent or not a finite number (a
    bool, a malformed or non-finite string, another shape, or a
    magnitude that overflows ``float``) — a hostile snapshot can
    never raise here (never a 500)."""
    if isinstance(value, bool):
        return 0.0
    try:
        if isinstance(value, str):
            number = float(Decimal(value.strip()))
        elif isinstance(value, (int, float, Decimal)):
            number = float(value)
        else:
            return 0.0
    except (InvalidOperation, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _feed_order_numbers(open_lines: list[dict]) -> set:
    """The newest open-purchase snapshot's order numbers: each line's
    ``business_key`` second ``:``-separated part (a key without at
    least one ``:`` contributes nothing)."""
    numbers = set()
    for line in open_lines:
        business_key = line.get("business_key")
        if not isinstance(business_key, str):
            continue
        parts = business_key.split(":")
        if len(parts) >= 2:
            numbers.add(parts[1])
    return numbers


def _in_flight_supply_lines(
    release_rows: list,
    start: date,
    feed_order_numbers: set,
) -> list[dict]:
    """The on-order supply lines of the in-flight release rows.

    A row is in-flight when its ``outcome`` is "created" or "exists",
    its ``erp_status`` is null or none of ``FINAL_ERP_STATUSES`` (a
    finished or canceled release is not in flight), its ``order_nbr``
    is non-null and ``feed_order_numbers`` does not carry that order
    number — a released order the feed already picked up is counted
    from the feed only, never twice.  Each line
    of an in-flight row (``item_code``, ``quantity``, ``unit_cost``,
    at the row's ``site_code``) contributes one supply line arriving
    on the first day of the row's ``month`` — ``start``, the
    projection's start date, when that day is earlier — valued at
    ``quantity x unit_cost``; from there the line behaves exactly
    like an open purchase line.  A row whose item-site is not
    recommendable is ignored by the setup's groups and review state,
    and a row with a malformed month, a non-string site code or a
    non-list snapshot contributes nothing — nothing here can raise on
    a hostile row (never a 500).
    """
    supply: list[dict] = []
    for row in release_rows:
        if not isinstance(row, dict):
            continue
        if row.get("outcome") not in _IN_FLIGHT_OUTCOMES:
            continue
        if row.get("erp_status") in FINAL_ERP_STATUSES:
            continue
        order_nbr = row.get("order_nbr")
        if order_nbr is None:
            continue
        if isinstance(order_nbr, str) and order_nbr in feed_order_numbers:
            continue
        site_code = row.get("site_code")
        if not isinstance(site_code, str):
            continue
        first_day = _release_month_first_day(row.get("month"))
        if first_day is None:
            continue
        lines = row.get("lines")
        if not isinstance(lines, list):
            continue
        promised_on = first_day if first_day >= start else start
        for line in lines:
            if not isinstance(line, dict):
                continue
            item_code = line.get("item_code")
            if not isinstance(item_code, str):
                continue
            supply.append(
                {
                    "item_code": item_code,
                    "site_code": site_code,
                    "open_units": _release_number(line.get("quantity")),
                    "unit_cost": _release_number(line.get("unit_cost")),
                    "promised_on": promised_on,
                }
            )
    return supply


def _in_flight_release_lines(as_of: date | None) -> list[dict]:
    """The one read behind the in-flight release supply: every stored
    plan-release row (newest first, ``created_at DESC, id DESC``),
    whatever its outcome.

    The setup turns the in-flight rows — the ones
    ``_in_flight_supply_lines`` keeps — into on-order supply lines
    and joins them to the open purchase lines: they count in the
    monthly supply and its value, the review walk no longer plans an
    order for that need, and
    ``app.aggregate.spend.spend_against_budget`` reports them as
    committed spend (``planned_orders``, ``plan_release`` and
    ``deferral_candidates`` see the same supply through the same
    setup).  ``as_of`` is the projection's as-of (the current server
    date when None); the setup resolves it and clamps the rows'
    arrival against the start date.

    The read runs on its own connection, opened through the
    import-time binding of ``psycopg2.connect`` with the same
    parameters ``app.db.connection.get_connection_params`` resolves:
    the setup's other reads are the ones the fixed-statement
    proofs instrument by patching ``psycopg2.connect`` at call time,
    and this read is outside that measurement.
    It issues one read and writes nothing.
    """
    conn = _psycopg2_connect(**get_connection_params())
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM plan_releases ORDER BY created_at DESC, id DESC")
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _signal_day_shares(
    resolved_as_of: date,
    signals: list[tuple[int, date, date, float]],
    overrides: list[tuple[int, date, date, float]],
) -> list[tuple[date, float]]:
    """The signal day shares of one item-site, in day order.

    Each day after the resolved as-of date that a counted signal's
    window covers and no counted override covers takes the signal's
    quantity over the days of its window — the stated quantity spread
    evenly over its window; a day several counted signals cover takes
    the sum of their shares, and a day a counted override covers
    takes no signal share at all (the override wins the day).  A
    signal with a non-finite or non-positive quantity, or a window
    that ends before it starts, contributes nothing (a hostile row
    can never raise here — never a 500).
    A resolved as-of at the last representable date has no day after
    it, so it gives no shares at all.
    """
    day_share: dict[date, float] = {}
    try:
        first_after_as_of = resolved_as_of + timedelta(days=1)
    except OverflowError:
        # date.max + 1 overflows: there is no day after the last
        # representable date, so no day share exists.
        return []
    for _signal_id, start, end, quantity in signals:
        if not (math.isfinite(quantity) and quantity > 0):
            continue
        window_days = (end - start).days + 1
        if window_days <= 0:
            continue
        share = quantity / window_days
        first = max(start, first_after_as_of)
        if first > end:
            continue
        for offset in range((end - first).days + 1):
            day = first + timedelta(days=offset)
            if any(
                period_start <= day <= period_end
                for _override_id, period_start, period_end, _override_qty in overrides
            ):
                continue
            day_share[day] = day_share.get(day, 0.0) + share
    return [(day, share) for day, share in sorted(day_share.items())]


def _shares_in(
    days: list[date],
    prefix: list[float],
    start: date,
    end: date,
) -> float:
    """The prefix-summed share of the days d with start <= d <= end
    (0.0 when the window is empty or holds no share day)."""
    if end < start:
        return 0.0
    lo = bisect.bisect_left(days, start)
    hi = bisect.bisect_right(days, end)
    return prefix[hi] - prefix[lo]


def _signal_bucket_shares(
    shares: list[tuple[date, float]],
    buckets: list[tuple[int, int, str]],
    lt: float,
) -> tuple[list[float], list[float]]:
    """The day shares summed per bucket month.

    Returns ``(signal_units, cover)`` with one value per bucket in
    bucket order: ``signal_units`` the sum of the shares falling in
    the bucket's calendar month — the month's signal units, days
    outside the horizon (the buckets) counting for nothing — and
    ``cover`` the sum of the shares from the first day of that month
    through the last day of that month plus ``ceil(lt)`` further
    days: the demand a start-of-month order has to carry until the
    next review's order can arrive (a lead time whose window end is
    not a representable date reaches ``date.max``).
    """
    if not shares:
        return [0.0] * len(buckets), [0.0] * len(buckets)
    days = [day for day, _share in shares]
    prefix = [0.0]
    for _day, share in shares:
        prefix.append(prefix[-1] + share)
    try:
        cover_extension = timedelta(days=math.ceil(lt))
    except OverflowError:
        cover_extension = None
    signal_units: list[float] = []
    cover: list[float] = []
    for year, month, _label in buckets:
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        signal_units.append(_shares_in(days, prefix, first, last))
        if cover_extension is None:
            cover_end = date.max
        else:
            try:
                cover_end = last + cover_extension
            except OverflowError:
                cover_end = date.max
        cover.append(_shares_in(days, prefix, first, cover_end))
    return signal_units, cover


def _extra_safety_stock_by_bucket(
    d: float,
    sd_d: float,
    lt: float,
    sd_lt: float,
    z: float,
    buckets: list[tuple[int, int, str]],
) -> list[float]:
    """The review month's extra safety stock of one item-site, one
    value per bucket: ``z x (sqrt((lt + days) x sd_d^2 + d^2 x
    sd_lt^2) - sqrt(lt x sd_d^2 + d^2 x sd_lt^2))`` with ``days``
    the bucket month's days — the safety stock the order-up-to level
    gains covering the review month on top of the lead time, the
    ``d^2 x sd_lt^2`` term inside both roots, never added twice.
    0.0 for every bucket when ``z`` is 0 (no service-level safety
    stock) or ``sd_d`` is 0 (both roots then hold the ``d^2 x
    sd_lt^2`` term alone).  Raises ``OverflowError`` or
    ``ValueError`` when a radicand overflows the float range or is
    negative — the caller reports the hostile inputs (never a 500).
    """
    if z == 0.0 or sd_d == 0.0:
        return [0.0] * len(buckets)
    lead_term = d * d * sd_lt * sd_lt
    base = lt * sd_d * sd_d + lead_term
    extras: list[float] = []
    for year, month, _label in buckets:
        days = calendar.monthrange(year, month)[1]
        extended = (lt + days) * sd_d * sd_d + lead_term
        extra = z * (math.sqrt(extended) - math.sqrt(base))
        if not math.isfinite(extra):
            raise OverflowError("the extra safety stock overflowed")
        extras.append(extra)
    return extras


def _projection_setup(
    as_of: date | None,
    months: int,
    service_level: float | None,
    demand_multiplier: float | None,
    lead_time_multiplier: float | None,
):
    """Validate the arguments, perform the six reads (the
    recommendations, the counted signals and counted overrides of the
    projected item-sites, the on-hand, the open purchase lines and
    the in-flight release rows) and build the monthly-review state of
    every recommendable item-site, shared by
    ``aggregate_projection``, ``planned_orders`` and
    ``app.aggregate.deferral.deferral_candidates`` (through the
    setup-taking helpers) so none of them can ever disagree.

    The validation is exactly ``aggregate_projection``'s (the same
    ``ValueError``s): the levers are validated with
    ``app.scenario.simulate._validate_levers`` before anything is
    read, ``months`` must be an integer between 1 and 12 inclusive and
    ``as_of`` must not be after the current server date.  With any
    lever given every recommendable item-site's inputs are scaled
    exactly as ``app.scenario.simulate.simulate_reorder`` scales them
    and its ``rop`` is the scenario reorder point of the scaled
    inputs; a scaled input that overflows the formula raises
    ``ValueError`` (never a 500).

    Returns ``(buckets, month_index, site_reviews, site_meta,
    open_lines, in_flight_lines)``: ``buckets`` is
    ``_bucket_months(resolved_as_of, months)`` (``resolved_as_of`` the
    current server date when ``as_of`` is None), ``month_index`` maps
    a ``(year, month)`` to its bucket index, ``site_reviews`` maps a
    site code (in ``site_code`` order) to the monthly-review state of
    its item-sites in recommendation order — each review carrying its
    ``item_code`` / ``site_code``, its ``rop`` / ``d`` / ``lt`` (each
    non-finite or missing value taken as 0, the inputs lever-scaled
    when a lever is given), its per-bucket ``extra_by_bucket``
    review-month extra safety stock (0.0 for an item-site that gets
    none — no lever and a ``method`` other than
    ``app.recommend.reorder.METHOD`` — or ``sd_d`` 0), its
    ``unit_cost`` (0.0 when the item-site
    has none) and its raw ``unit_cost_raw`` (None when it has none),
    its own start-of-month ``on_hand`` (its current on-hand, 0 when
    absent), its supply lines (the open lines plus the in-flight
    release lines of its item-site), its placed orders and its
    signal day shares summed per bucket — ``signal_units_by_bucket``
    (the month's signal units, days outside the horizon counting for
    nothing) and ``signal_cover_by_bucket`` (the shares from the
    first day of the bucket month through its last day plus
    ``ceil(lt)`` further days, the demand a start-of-month order
    carries until the next review's order can arrive) — and
    ``site_meta`` the per-group aggregates the as-is roll needs
    (``member_keys``, ``unit_cost_by_key``, ``without_unit_cost`` and
    ``prior_on_hand``).  ``in_flight_lines`` is the on-order supply
    of the in-flight release rows, one dict per snapshot line
    (``item_code``, ``site_code``, ``open_units``, ``unit_cost`` and
    ``promised_on`` — the first day of the row's ``month``, the
    projection's start date when that day is earlier), joined to the
    open lines in the as-is supply roll and in every review.
    """
    _validate_levers(service_level, demand_multiplier, lead_time_multiplier)
    if not isinstance(months, int) or isinstance(months, bool):
        raise ValueError(
            f"months must be an integer between {MIN_MONTHS} and {MAX_MONTHS}"
        )
    if not (MIN_MONTHS <= months <= MAX_MONTHS):
        raise ValueError(f"months must be between {MIN_MONTHS} and {MAX_MONTHS}")
    today = date.today()
    if as_of is None:
        resolved_as_of = today
    elif not isinstance(as_of, datetime.date) or isinstance(as_of, datetime.datetime):
        raise ValueError("as_of must be a date")
    else:
        resolved_as_of = as_of
    if resolved_as_of > today:
        raise ValueError("as_of cannot be after the current server date")

    levers_given = any(
        lever is not None
        for lever in (service_level, demand_multiplier, lead_time_multiplier)
    )
    demand_factor = float(demand_multiplier) if demand_multiplier is not None else 1.0
    lead_factor = (
        float(lead_time_multiplier) if lead_time_multiplier is not None else 1.0
    )

    recommendations = recommendations_for_all(as_of=as_of)
    # The demand signals and the forecast overrides of the projected
    # item-sites (the status "ok" ones): one batched read each, on the
    # normal connection path like the setup's other reads.  The
    # signals' day shares lift the buckets' demand and the monthly
    # review's order-up-to level; the overrides win the days they
    # cover, so a signal never takes an overridden day.
    projected_pairs = sorted(
        {
            (rec["item_code"], rec["site_code"])
            for rec in recommendations
            if rec["status"] == "ok"
        }
    )
    signals_by_key = counted_signals(projected_pairs)
    overrides_by_key = counted_overrides(projected_pairs)
    on_hand_by_key = {
        (row["item_code"], row["site_code"]): row["quantity_on_hand"]
        for row in _on_hand_repo.list_rows(10**9, 0)
    }
    open_lines = _open_po_repo.list_latest()
    in_flight_lines = _in_flight_supply_lines(
        _in_flight_release_lines(as_of),
        resolved_as_of,
        _feed_order_numbers(open_lines),
    )
    supply_lines = open_lines + in_flight_lines

    groups: dict[str, list[dict]] = {}
    for recommendation in recommendations:
        if recommendation["status"] != "ok":
            continue
        groups.setdefault(recommendation["site_code"], []).append(recommendation)

    buckets = _bucket_months(resolved_as_of, months)
    month_index = {
        (year, month): index for index, (year, month, _label) in enumerate(buckets)
    }
    site_reviews: dict[str, list[dict]] = {}
    site_meta: dict[str, dict] = {}
    for site_code in sorted(groups):
        members = groups[site_code]
        member_keys = {(rec["item_code"], rec["site_code"]) for rec in members}
        unit_cost_by_key = {
            (rec["item_code"], rec["site_code"]): rec["unit_cost"] for rec in members
        }
        without_unit_cost = sum(1 for rec in members if rec["unit_cost"] is None)
        prior_on_hand = sum(_finite(on_hand_by_key.get(key)) for key in member_keys)
        reviews = []
        for rec in members:
            key = (rec["item_code"], rec["site_code"])
            inputs = rec.get("inputs") or {}
            if levers_given:
                d = _finite(inputs.get("d")) * demand_factor
                sd_d = _finite(inputs.get("sd_d")) * demand_factor
                lt = _finite(inputs.get("lt")) * lead_factor
                sd_lt = _finite(inputs.get("sd_lt")) * lead_factor
                scenario_level = (
                    float(service_level)
                    if service_level is not None
                    else _finite(inputs.get("service_level"))
                )
                z = statistics.NormalDist().inv_cdf(scenario_level)
                try:
                    rop = _basis_result(d, sd_d, lt, sd_lt, z, scenario_level)[
                        "reorder_point_units"
                    ]
                    extra_by_bucket = _extra_safety_stock_by_bucket(
                        d, sd_d, lt, sd_lt, z, buckets
                    )
                except (OverflowError, ValueError) as exc:
                    # A hostile lever (e.g. a huge multiplier) overflows
                    # the formula: report it as an out-of-range lever,
                    # never 500.
                    raise ValueError(
                        f"item site {rec['item_code']} at {rec['site_code']}: "
                        "the scaled inputs overflow the reorder formula"
                    ) from exc
            else:
                # The projection counts the demand signals by month in
                # a later slice, so its order-up-to level takes the
                # pre-signal reorder point — never the signals' share
                # the reorder point carries.
                rop = _finite(
                    rec.get(
                        "reorder_point_before_signals_units",
                        rec.get("reorder_point_units"),
                    )
                )
                d = _finite(inputs.get("d"))
                lt = _finite(inputs.get("lt"))
                # The review month's extra safety stock takes the
                # recommendation's own inputs, and only when the
                # recommendation's ``method`` is the service-level
                # method — an empirical recommendation gets none.
                extra_by_bucket = [0.0] * len(buckets)
                if rec.get("method") == METHOD:
                    try:
                        extra_by_bucket = _extra_safety_stock_by_bucket(
                            d,
                            _finite(inputs.get("sd_d")),
                            lt,
                            _finite(inputs.get("sd_lt")),
                            _finite(inputs.get("z")),
                            buckets,
                        )
                    except (OverflowError, ValueError):
                        # Hostile stored inputs can never 500: keep
                        # the order-up-to level without the extra.
                        extra_by_bucket = [0.0] * len(buckets)
            shares = _signal_day_shares(
                resolved_as_of,
                signals_by_key.get(key, []),
                overrides_by_key.get(key, []),
            )
            (
                signal_units_by_bucket,
                signal_cover_by_bucket,
            ) = _signal_bucket_shares(shares, buckets, lt)
            reviews.append(
                {
                    "item_code": rec["item_code"],
                    "site_code": rec["site_code"],
                    "rop": rop,
                    "d": d,
                    "lt": lt,
                    "extra_by_bucket": extra_by_bucket,
                    "signal_units_by_bucket": signal_units_by_bucket,
                    "signal_cover_by_bucket": signal_cover_by_bucket,
                    "unit_cost": _finite(unit_cost_by_key[key]),
                    "unit_cost_raw": unit_cost_by_key[key],
                    "on_hand": _finite(on_hand_by_key.get(key)),
                    "lines": [
                        (line["promised_on"], _finite(line["open_units"]))
                        for line in supply_lines
                        if (line["item_code"], line["site_code"]) == key
                    ],
                    "orders": [],
                }
            )
        site_reviews[site_code] = reviews
        site_meta[site_code] = {
            "member_keys": member_keys,
            "unit_cost_by_key": unit_cost_by_key,
            "without_unit_cost": without_unit_cost,
            "prior_on_hand": prior_on_hand,
        }
    return (
        buckets,
        month_index,
        site_reviews,
        site_meta,
        open_lines,
        in_flight_lines,
    )


def _monthly_review(
    reviews: list[dict],
    buckets: list[tuple[int, int, str]],
    month_index: dict[tuple[int, int], int],
):
    """Run the periodic-review order-up-to (R, S) walk of every review
    over the buckets: at the start of each bucket month the review's
    inventory position is its own start-of-month on-hand plus its open
    purchase lines and its in-flight release lines not yet received in
    an earlier bucket (a line counts
    as received only in the bucket of its ``promised_on`` month,
    exactly as the existing supply rule; lines with no ``promised_on``,
    or promised before the first bucket or after the last, stay on
    order throughout) plus its planned orders placed in earlier buckets
    that have not yet arrived, and one planned order of ``ceil(S -
    position)`` units is placed when the position is strictly below
    the order-up-to level ``rop + d x days-in-month + extra +
    cover``, with ``extra`` the review month's extra safety stock
    the setup computed per bucket and ``cover`` the sum of the
    review's signal day shares from the first day of the bucket month
    through its last day plus ``ceil(lt)`` further days — the demand
    the order has to carry until the next
    review's order can arrive; a day share an earlier month's order
    already carries stays in the position and does not raise a later
    order again — the order arriving on the first day of the bucket
    month plus ``ceil(lt)`` days and counted as a receipt in the
    bucket whose month contains that date, or in no bucket when the
    date is after the last one (it then stays on order).

    This is the walk ``aggregate_projection`` uses for its
    ``planned_receipts_*`` / ``planned_on_hand_units`` /
    ``planned_shortfall`` fields and ``planned_orders`` uses for its
    entries: both call this helper, so the two can never disagree.  It
    mutates each review's ``orders`` and ``on_hand`` in place — the
    review's ``on_hand`` is its own planned on-hand, rolled forward as
    ``prior + receipts - d x days-in-month - signal_units(month)``
    with receipts the bucket's open-line receipts plus its planned
    arrivals and ``signal_units(month)`` the review's signal day
    shares falling in the bucket's calendar month.

    Returns ``(per_bucket, placed)``: ``per_bucket`` has one
    ``(planned_units, planned_value)`` per bucket in bucket order
    (``planned_value`` at each review's ``unit_cost``) and ``placed``
    one record per placed order, in placement order, each
    ``(review, placed_index, arrival_index, quantity,
    month_end_on_hand)`` with ``arrival_index`` None when the order
    arrives after the last bucket and ``month_end_on_hand`` the
    review's own planned on-hand at the end of the arrival month after
    that month's receipts and demand, exactly as the walk rolls it, or
    None when the arrival is outside the horizon.
    """
    placed: list[list] = []
    per_bucket: list[tuple[int, float]] = []
    first_month = (buckets[0][0], buckets[0][1])
    last_month = (buckets[-1][0], buckets[-1][1])
    for bucket_index, (year, month, _label) in enumerate(buckets):
        days = calendar.monthrange(year, month)[1]
        planned_units = 0
        planned_value = 0.0
        for review in reviews:
            position = review["on_hand"]
            for promised_on, units in review["lines"]:
                if promised_on is None or (
                    (promised_on.year, promised_on.month) < first_month
                    or (promised_on.year, promised_on.month) >= (year, month)
                ):
                    position += units
            for arrival_index, units in review["orders"]:
                if arrival_index is None or arrival_index >= bucket_index:
                    position += units
            # The order-up-to level carries the review month's extra
            # safety stock and the review's signal day shares of that
            # month plus the ceil(lt) days after it: the demand this
            # order has to carry until the next review's order can
            # arrive (a day share an earlier month's order already
            # carries stays in the position and does not raise a
            # later order again).
            up_to = (
                review["rop"]
                + review["d"] * days
                + review["extra_by_bucket"][bucket_index]
                + review["signal_cover_by_bucket"][bucket_index]
            )
            if position < up_to:
                quantity = math.ceil(up_to - position)
                arrival_index = _planned_arrival_index(
                    year,
                    month,
                    review["lt"],
                    month_index,
                    last_month,
                    bucket_index,
                )
                review["orders"].append((arrival_index, quantity))
                placed.append([review, bucket_index, arrival_index, quantity, None])
            receipts = 0.0
            for promised_on, units in review["lines"]:
                if promised_on is not None and (
                    (promised_on.year, promised_on.month) == (year, month)
                ):
                    receipts += units
            for arrival_index, units in review["orders"]:
                if arrival_index == bucket_index:
                    receipts += units
                    planned_units += units
                    planned_value += units * review["unit_cost"]
            review["on_hand"] += (
                receipts
                - review["d"] * days
                - review["signal_units_by_bucket"][bucket_index]
            )
        # The orders that arrive in this bucket have their
        # month-end on-hand: this bucket's own planned on-hand after
        # its receipts and demand.
        for record in placed:
            if record[2] == bucket_index:
                record[4] = record[0]["on_hand"]
        per_bucket.append((planned_units, planned_value))
    return per_bucket, placed


def _copy_review_state(
    site_reviews: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """A fresh copy of the setup's monthly-review state, site for
    site and review for review.

    ``_monthly_review`` mutates each review's ``orders`` and
    ``on_hand`` in place, so every consumer of a setup walks its own
    copy: the setup itself — and any other consumer of the same setup
    — is never mutated.
    """
    return {
        site_code: [{**review, "orders": list(review["orders"])} for review in reviews]
        for site_code, reviews in site_reviews.items()
    }


def _aggregate_projection_from_setup(setup: tuple) -> list[dict]:
    """The site-grouped roll-forward projection of an already-built
    setup (``_projection_setup``'s return value), field for field
    what ``aggregate_projection`` computes: each group's as-is roll
    (the first bucket starts from the group's current on-hand total,
    each bucket ``prior + supply - demand``) and its dollar view at
    each item-site's ``unit_cost`` (an in-flight release line at its
    own), plus the monthly-review fields of
    ``_monthly_review`` over a copy of the setup's review state, so
    the setup itself is never mutated.
    """
    (
        buckets,
        month_index,
        site_reviews,
        site_meta,
        open_lines,
        in_flight_lines,
    ) = setup
    reviews_by_site = _copy_review_state(site_reviews)
    result: list[dict] = []
    for site_code in site_reviews:
        meta = site_meta[site_code]
        member_keys = meta["member_keys"]
        unit_cost_by_key = meta["unit_cost_by_key"]
        prior_on_hand = meta["prior_on_hand"]
        reviews = reviews_by_site[site_code]
        per_bucket, _placed = _monthly_review(reviews, buckets, month_index)
        bucket_rows: list[dict] = []
        cumulative_planned = 0
        for bucket_index, (year, month, label) in enumerate(buckets):
            days = calendar.monthrange(year, month)[1]
            demand_units = 0.0
            demand_value = 0.0
            bucket_signal_units = 0.0
            for review in reviews:
                units = review["d"] * days
                # The signal's stated quantity, at the item-site's
                # unit cost by the existing rule: a demand multiplier
                # never scales it.
                share = review["signal_units_by_bucket"][bucket_index]
                demand_units += units + share
                demand_value += (units + share) * review["unit_cost"]
                bucket_signal_units += share
            supply_units = 0.0
            supply_value = 0.0
            for line in open_lines:
                key = (line["item_code"], line["site_code"])
                if key not in member_keys:
                    continue
                promised_on = line["promised_on"]
                if promised_on is None or (
                    (promised_on.year, promised_on.month) != (year, month)
                ):
                    continue
                units = _finite(line["open_units"])
                supply_units += units
                supply_value += units * _finite(unit_cost_by_key.get(key))
            for line in in_flight_lines:
                key = (line["item_code"], line["site_code"])
                if key not in member_keys:
                    continue
                promised_on = line["promised_on"]
                if promised_on is None or (
                    (promised_on.year, promised_on.month) != (year, month)
                ):
                    continue
                units = _finite(line["open_units"])
                supply_units += units
                supply_value += units * _finite(line["unit_cost"])
            demand_units_whole = math.ceil(demand_units)
            on_hand = prior_on_hand + supply_units - demand_units_whole
            prior_on_hand = on_hand
            # The members' monthly review of this bucket, run once for
            # the whole horizon by the shared walk (
            # ``_monthly_review``, the same one ``planned_orders``
            # uses): one planned order per item-site when its position
            # is strictly below the order-up-to level.
            planned_units, planned_value = per_bucket[bucket_index]
            cumulative_planned += planned_units
            planned_on_hand = on_hand + cumulative_planned
            bucket_rows.append(
                {
                    "month": label,
                    "projected_demand_units": demand_units_whole,
                    "projected_supply_units": supply_units,
                    "projected_on_hand_units": on_hand,
                    "projected_shortfall": on_hand < 0,
                    "projected_demand_value": demand_value,
                    "projected_supply_value": supply_value,
                    "signal_units": bucket_signal_units,
                    "planned_receipts_units": planned_units,
                    "planned_receipts_value": planned_value,
                    "planned_on_hand_units": planned_on_hand,
                    "planned_shortfall": planned_on_hand < 0,
                }
            )
        result.append(
            {
                "group": {"dimension": "site", "code": site_code},
                "buckets": bucket_rows,
                "item_sites_without_unit_cost": meta["without_unit_cost"],
            }
        )
    return result


def _planned_orders_from_setup(setup: tuple) -> list[dict]:
    """The planned orders of an already-built setup
    (``_projection_setup``'s return value), entry for entry and in
    placement order what ``planned_orders`` returns:
    ``_monthly_review`` over a copy of the setup's review state per
    site, the sites in site_code order and their item-sites in
    recommendation order, then the stable sort on the placement bucket
    that restores the order the review places the orders: bucket by
    bucket in month order, the sites in site_code order, the
    item-sites in recommendation order.  The setup itself is never
    mutated.
    """
    (
        buckets,
        month_index,
        site_reviews,
        _site_meta,
        _open_lines,
        _in_flight_lines,
    ) = setup
    placed: list[tuple] = []
    for reviews in _copy_review_state(site_reviews).values():
        _per_bucket, site_placed = _monthly_review(reviews, buckets, month_index)
        placed.extend(site_placed)
    placed.sort(key=lambda record: record[1])
    orders: list[dict] = []
    for (
        review,
        placed_index,
        arrival_index,
        quantity,
        month_end_on_hand,
    ) in placed:
        raw_unit_cost = review["unit_cost_raw"]
        orders.append(
            {
                "item_code": review["item_code"],
                "site_code": review["site_code"],
                "placed_month": buckets[placed_index][2],
                "arrival_month": (
                    buckets[arrival_index][2] if arrival_index is not None else None
                ),
                "quantity": quantity,
                "unit_cost": (
                    raw_unit_cost
                    if raw_unit_cost is not None and math.isfinite(raw_unit_cost)
                    else None
                ),
                "value": quantity * _finite(raw_unit_cost),
                "d": review["d"],
                "month_end_on_hand": month_end_on_hand,
            }
        )
    return orders


def aggregate_projection(
    as_of: date | None = None,
    months: int = DEFAULT_MONTHS,
    service_level: float | None = None,
    demand_multiplier: float | None = None,
    lead_time_multiplier: float | None = None,
) -> list[dict]:
    """Project the recommendable portfolio forward, grouped by site.

    ``as_of`` is passed through to ``recommendations_for_all`` as
    given (its natural demand basis — the earlier of the current
    server date and the latest demand event — when None, the same the
    recommendations and scenario endpoints use); the resolved date
    (the current server date when ``as_of`` is None) names the month
    the buckets start from; ``months`` (default 6) is the number of
    consecutive calendar-month buckets.

    The three levers carry exactly the meaning
    ``app.scenario.simulate.simulate_reorder`` gives them and are
    validated with ``app.scenario.simulate._validate_levers`` before
    anything is read: with any lever given, every recommendable
    item-site's inputs are scaled — ``d' = d x demand_multiplier``,
    ``sd_d' = sd_d x demand_multiplier``, ``lt' = lt x
    lead_time_multiplier``, ``sd_lt' = sd_lt x lead_time_multiplier``
    (a lever not given counts as 1.0) — the service level is the
    given one or else the baseline's ``inputs.service_level`` with
    ``z' = NormalDist().inv_cdf(service_level')``, and the scenario
    reorder point
    ``_basis_result(d', sd_d', lt', sd_lt', z', service_level')["reorder_point_units"]``
    replaces the recommendation's ``reorder_point_units``; the
    projection then uses ``d'`` in place of ``d`` everywhere it uses
    ``d`` (the as-is projected demand and its dollar value, the
    planned review's demand and order-up-to level) and ``lt'`` in
    place of ``lt`` for planned arrivals.  With no lever given the
    output is exactly what it is without the levers, field for field.

    Returns a list of groups ordered by ``site_code``, each
    ``{"group": {"dimension": "site", "code": <site>}, "buckets": [...],
    "item_sites_without_unit_cost": n}``; each bucket (ordered by
    month) carries ``month`` ("YYYY-MM"), ``projected_demand_units``
    (the ceiling of the group's summed ``d x days-in-month``),
    ``projected_supply_units`` (the group's open lines' ``open_units``
    promised in that month), ``projected_on_hand_units`` (the
    rolled-forward on-hand: the first bucket starts from the group's
    current ``on_hand`` total and each bucket is
    ``prior + supply - demand``), ``projected_shortfall`` (true when
    the rolled on-hand is below zero), the dollar view
    ``projected_demand_value`` / ``projected_supply_value`` (each
    item-site's units at its ``item_sites.unit_cost``, zero for an
    item-site without one), ``signal_units`` (the sum of the group's
    item-sites' signal day shares falling in the bucket's calendar
    month — days outside the horizon count for nothing, and a day a
    counted override covers takes no signal share — included in
    ``projected_demand_units`` and ``projected_demand_value``) and
    the monthly-review fields
    ``planned_receipts_units`` (the sum of the group's planned orders
    arriving in the bucket), ``planned_receipts_value`` (those units
    at each item-site's ``unit_cost``, zero for an item-site without
    one), ``planned_on_hand_units`` (``projected_on_hand_units`` plus
    the group's cumulative ``planned_receipts_units`` through the
    bucket) and ``planned_shortfall`` (true when
    ``planned_on_hand_units`` is below zero).

    The monthly review is a periodic-review order-up-to (R, S)
    policy: at the start of each bucket month an item-site places a
    planned order for ``ceil(S - position)`` units whenever its
    inventory position is strictly below the order-up-to level (the
    reorder point before the signals, plus that month's demand, plus
    the review month's extra safety stock —
    ``z x (sqrt((lt + days) x sd_d^2 + d^2 x sd_lt^2) - sqrt(lt x
    sd_d^2 + d^2 x sd_lt^2))``, ``days`` the bucket month's days and
    the inputs as the module docstring gives them — plus ``cover``,
    the sum of the item-site's signal day shares from the first day
    of the month through its last day plus ``ceil(lt)`` further days,
    the demand the order carries until the next review's order can
    arrive), ``S = rop + d x days-in-month + extra + cover``, and
    the order arrives on the first of the month plus ``ceil(lt)``
    days.

    Raises ``ValueError`` when ``months`` is outside 1 to 12
    inclusive, when ``as_of`` is after the current server date (the
    projection rolls forward from the current on-hand, so a future
    as-of is invalid), when a given lever is out of range, or when a
    scaled input overflows the reorder formula (never a 500).  It
    performs a fixed number of reads and writes nothing.
    """
    return _aggregate_projection_from_setup(
        _projection_setup(
            as_of,
            months,
            service_level,
            demand_multiplier,
            lead_time_multiplier,
        )
    )


def planned_orders(
    as_of: date | None = None,
    months: int = DEFAULT_MONTHS,
    service_level: float | None = None,
    demand_multiplier: float | None = None,
    lead_time_multiplier: float | None = None,
) -> list[dict]:
    """Every planned order the projection's monthly review places
    within the horizon, in placement order.

    The arguments carry exactly the meaning
    ``aggregate_projection`` gives them and are validated with exactly
    its validation (the same ``ValueError``s); the orders come from
    the same review walk ``aggregate_projection`` uses (
    ``app.aggregate.projection._monthly_review`` over the same review
    state its ``_projection_setup`` builds), so the two can never
    disagree.

    Returns one entry per planned order, in the order the review
    places them — bucket by bucket in month order, the site's
    item-sites in recommendation order, the sites in ``site_code``
    order: ``item_code`` and ``site_code``, ``placed_month`` (the
    "YYYY-MM" of the month the order is placed), ``arrival_month``
    (the "YYYY-MM" of the month the order arrives, or None when it
    arrives after the horizon), ``quantity`` (the ordered units),
    ``unit_cost`` (the item-site's ``unit_cost``, or None when it has
    none), ``value`` (the quantity at that unit cost, 0 without one),
    ``d`` (the item-site's daily demand the review uses, lever-scaled
    when a lever is given) and ``month_end_on_hand`` (the item-site's
    own planned on-hand at the end of the arrival month after that
    month's receipts and demand, exactly as the review rolls it, or
    None when the arrival is outside the horizon).  It performs the
    same fixed number of reads as ``aggregate_projection`` and writes
    nothing.
    """
    return _planned_orders_from_setup(
        _projection_setup(
            as_of,
            months,
            service_level,
            demand_multiplier,
            lead_time_multiplier,
        )
    )
