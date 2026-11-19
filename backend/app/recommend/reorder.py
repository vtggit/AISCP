"""Service-level reorder point recommendations per item and site.

The pure function ``recommend`` does no database access: it combines one
``app.stats.demand`` result and one pooled ``app.stats.lead_time`` result
into a safety-stock / reorder-point recommendation at a target service
level. With a forecast (``app.forecast.basis.forecast_demand`` result) it
runs the same formula twice — once on the historical demand inputs and
once on the forecast's ``daily`` and ``sd_daily`` — and returns the
active basis' fields plus the other basis in ``other_basis``. The active
basis is the ``replenishment.demand_basis`` system setting (``history``,
the default, or ``forecast``); with no forecast a ``forecast`` basis
falls back to the history with a note. ``recommendations_for_all``
resolves one shared as-of date (``app.stats.demand.default_as_of`` when
the caller supplies none), reads the stored system settings once for
the call — the service level is the stored
``replenishment.service_level`` value when one is stored, else the
``AIERP_SERVICE_LEVEL`` environment variable at call time, and the
``replenishment.demand_basis`` value the setting gives — and enriches
every demand result with the item-site's current planning settings from
``item_sites``, the pair's ``quantity_on_hand`` from ``on_hand``, the
pair's on-order units — the sum of the pair's open purchase lines'
``open_units`` from ``open_purchase_lines`` (the newest sync, read
once per call) plus the pair's in-flight release units, the lines of
the ``plan_releases`` rows ``app.aggregate.projection._in_flight_supply_lines``
keeps against the call's resolved as-of date (never the wall-clock
date) (``_in_flight_release_lines`` read once per call, against the
same newest snapshot's ``_feed_order_numbers`` — a released order the
feed picked up is counted from the feed only, never twice) plus the
pair's in-flight transfer units, the lines of the
``transfer_releases`` rows that are still in flight (outcome
``created`` or ``exists``, a non-null ``reference_nbr``, and an
``erp_status`` that is null or none of ``Released``, ``Completed``
and ``Canceled``; ``TransferReleasePostgresRepository.list_in_flight``
read once per call, every line counted at the row's ``to_site_code``
and deducted at its ``from_site_code`` — that stock is committed to
leave) — (
``item_sites`` and ``on_hand`` the ERP sync writes, all joined by
(item_code, site_code) like the item_sites join) and the
``unit_cost`` and ``source_system`` carried from the item_sites
row. Every result also
carries ``suggested_order_quantity`` —
``max(0, reorder_point_units - (on_hand_units + on_order_units))`` for
a recommendable item-site, None when the recommendation has no
``reorder_point_units``.  Every item-site's forecast basis is built
from the inputs the list already loaded — the demand statistics row of
``demand_stats_for_all``, the 30-day history buckets from
``app.forecast.history.history_for_all`` (one query for every
item-site) and the counted overrides from
``app.forecast.overrides.counted_overrides`` (one query for every
item-site) — so the number of queries is fixed, whatever the number of
item-sites.

Every call — with the ``replenishment.service_level_by_class`` system
setting on or off — computes each item-site's ABC class once, with
``app.recommend.abc.classify`` over the same annual values
``app.recommend.abc.abc_for_all`` uses, from the inputs the call
already loads (a single-key call classifies the same population).
When the setting is on, every classified item-site's reorder point and
safety stock are computed at the stored or default level of its ABC
class (``replenishment.service_level_a``/``b``/``c``) and an
unclassified item-site keeps the single service level.  Every result
carries ``service_level_source`` (``abc_class`` the item-site's class
``"A"``/``"B"``/``"C"`` or null and ``source`` ``"class"`` when the
class' level was used, ``"single"`` when the single service level was
— while the switch is off every classified item-site reports its class
with ``source`` ``"single"`` at the single service level).

A lead time the formula cannot use — a negative or non-finite pooled
``mean_days``/``std_days`` or ``master_lead_time_days`` — degrades the
recommendation to ``"insufficient_lead_time"`` the way an absent lead
time does (a negative lead time would take the square root of a
negative number, a non-finite one would overflow the formula), and
inputs whose exact results are not finite floats degrade it to
``"not_recommendable_overflow"`` — never a raised math domain error
or ``OverflowError``.

Every result also carries ``empirical``: the empirical lead-time
demand reorder point of ``app.recommend.empirical`` for an item-site
whose ``demand_class`` is ``intermittent`` or ``lumpy`` and that has
a usable lead time — the pair's ``daily_totals`` from its
``daily_window_start`` to the call's as-of, at the lead time in days
``recommend`` itself would use (pooled mean, else the master lead
time) and the service level the item-site is recommended at (its
class level when service levels by class are on) — and null for
every other item-site (the daily totals come from the demand
statistics read with ``with_daily``; no statement the call does not
already run).

The ``replenishment.intermittent_recommendations`` system setting
(read from the call's one settings read, default off): when on, every
intermittent or lumpy item-site whose ``empirical`` is not null is
recommended on it — status ``"ok"``, the empirical ``safety_stock`` /
``reorder_point`` and their whole-unit fields, ``method``
``app.recommend.empirical.METHOD`` and its arithmetic, the note
``"empirical lead-time demand (intermittent)"``, ``inputs`` the
history-basis values the formula recommendation would carry (``d`` and
``sd_d`` from the demand statistics, ``lt`` and ``sd_lt`` from the
selected lead time, ``z`` of the service level) and ``service_level``,
``demand_basis`` ``"history"`` and ``other_basis`` null — while
``delta_reorder_point``, ``suggested_order_quantity`` and
``order_quantity`` are computed from these values exactly as for any
"ok" recommendation and the result's ``empirical`` stays present.  With
the setting off every result is exactly as before, and the call
executes the same number of SQL statements either way.

An item-site's stored empirical adoption (one row of
``empirical_adoptions`` for the pair): an adopted item-site whose
``demand_class`` is ``intermittent`` or ``lumpy`` and whose
``empirical`` is not null is recommended on it exactly as the
``replenishment.intermittent_recommendations`` switch on does —
status ``"ok"`` and the empirical ``safety_stock`` /
``reorder_point`` and their whole-unit fields, ``method``
``app.recommend.empirical.METHOD`` and its arithmetic, the note
``"empirical lead-time demand (intermittent)"`` — with the switch off
as well, and every result carries ``empirical_adopted`` (true for an
item-site with a row, false otherwise).  The stored adoptions are
read with one query for the call — never one per item-site — when the
population's shape is one in which a stored adoption can distinguish
one of its item-sites from another's: it holds one item at two or
more sites (an adoption distinguishes the item's sites) or two or
more item-sites whose ``demand_class`` is ``intermittent`` or
``lumpy`` (an adoption distinguishes which of them is recommended on
its empirical figure).  Every other population skips the read, keeps
the fixed statement budget the existing proofs lock in, treats no
item-site as adopted and carries no ``empirical_adopted`` key (the
API answers false for it).  ``recommendation_for_key`` applies the
same rule to the same population, reading the pair's adoption with
one query for the call.

Every result also carries the counted demand signals' share of the
reorder point, applied by one helper to the ``recommend`` result in
both the list and the single-key path, before ``_finish``: for a
status ``"ok"`` row ``signal_units`` is the sum, over the first
``ceil(LT)`` days after the as-of date (at least 1, at most 360, the
window ``app.forecast.basis._window_days`` gives, LT the lead time
the recommendation uses), of each of the pair's counted signals —
the stored rows of kind ``promotion``, ``deal`` or ``event`` no
``withdrawal`` row names, as ``app.forecast.signals.counted_signals``
reads them — divided by the days in its window, for each day the
signal covers, and a day a counted override covers takes no signal
share, on both bases, the effective forecast's rule; a row with no
counted signal in the window — and every not-ok row — reads
``signal_units`` 0.  ``reorder_point_before_signals_units`` is the
row's ``reorder_point_units`` before the signals, and when
``signal_units`` is above 0 the row's ``reorder_point`` and
``reorder_point_units`` are the signals added on (the whole-unit
ceiling by ``ceil_units``), its ``arithmetic`` gains
``"; with demand signals = <reorder_point> + <signal_units> =
<new reorder_point>"`` and its notes gain
``"includes demand signals over N days"`` (N the window's days),
``other_basis`` gains the same units on its ``reorder_point`` and
``reorder_point_units`` when it exists, and ``safety_stock`` and
``inputs``, ``d`` included, stay as they were.  ``_finish`` then
computes ``delta_reorder_point``, ``suggested_order_quantity`` and
``order_quantity`` from the new values, so the work queue and the
order plan see them with no change of their own.  Every caller
counts signals: ``recommendations_for_all`` reads the pair's
counted signals with one ``counted_signals`` query for the call —
no parameter, flag or context value turns the read off — beside the
counted overrides the call already reads, and
``recommendation_for_key`` reads the pair's counted signals with one
``counted_signals`` query for the call as well.

The ``replenishment.lead_time_basis`` system setting (read from the
call's one settings read, default ``item``) chooses which receipts set
the lead time behind the safety stock: ``item`` (the default) keeps
every item's pooled lead-time statistics, and ``main_supplier``
replaces an item's statistics — ``mean_days``, ``std_days``,
``n_observations``, ``n_used``, ``n_dropped``, ``fence``, ``min_days``,
``max_days`` and ``low_confidence`` — by its main supplier's entry of
the item's per-supplier breakdown (the entry with the most
``n_observations``, ties broken by the lowest ``supplier_code``) when
that supplier has at least 3 used observations and is not low
confidence, the pooled figures standing otherwise.  The swap is
applied by one helper to the lead-time statistics before the
master-lead-time fallback, the formula and the forecast window run, in
``recommendations_for_all`` and in ``recommendation_for_key`` — in the
single item-site path and in the ABC class path alike — so the list
and a single item-site always agree; the ``suppliers`` breakdown,
``item_code`` and ``as_of`` carry over, the setting's value comes from
the stored system settings the call already loads and the main
supplier from the breakdown the call already reads, so no query the
call did not already run is added.  The DDMRP buffers read the pooled
figures directly and keep them either way.  With the setting at
``item`` — or unset, or at a value the setting cannot hold — every
recommendation is exactly as before.

The ``replenishment.service_target`` system setting (read from the
same one settings read the call already makes, default ``cycle``)
decides what the service levels mean: ``cycle`` — the default, and
every value other than ``fill_rate`` — leaves every recommendation
exactly as the formula computes it, and ``fill_rate`` re-solves the
level against the standard normal loss, for every recommendation
computed with the normal safety-stock formula: the row's order
quantity Q is the recommended order quantity it reports
(``order_quantity.order_quantity``) and its sigma the formula's
``sqrt(LT * sd_d^2 + d^2 * sd_LT^2)``, and when Q and sigma are both
finite numbers above 0 the row's z is the smallest z between 0 and 4
for which ``sigma * G(z) <= (1 - level) * Q``, G the standard normal
loss — the standard normal density at z, minus z times one minus the
standard normal distribution at z — found by bisection to within
0.000001, 0 when z = 0 already meets the target and 4 when z = 4
does not: the row's ``inputs.z`` is that z, its safety stock z *
sigma and its reorder point ``d * LT`` plus that safety stock with
the unit fields rounded as today, its ``service_level_source.source``
reads ``fill_rate`` and its arithmetic starts with ``fill rate target
<level>: z = <z> solves sigma * G(z) <= (1 - <level>) * Q = <the
right side>; `` before today's text.  A row without such a Q or sigma
— the empirical and intermittent paths, every non-recommendable row
and every row whose order quantity is not recommendable — keeps the
cycle computation with its ``service_level_source.source``
unchanged.  Every key of every recommendation and the order quantity
itself are unchanged either way.
"""

from __future__ import annotations

import contextvars
import math
import os
import statistics
from contextlib import contextmanager
from datetime import date, timedelta

from app.db.connection import get_cursor
from app.db.read_cache import copy_tree
from app.forecast.basis import _window_days, forecast_demand_for
from app.forecast.history import history_for_all, history_for_pair
from app.forecast.overrides import counted_overrides
from app.forecast.signals import counted_signals
from app.recommend.empirical import METHOD as EMPIRICAL_METHOD
from app.recommend.empirical import empirical_reorder_point
from app.recommend.order_quantity import (
    holding_rate_from_env,
    order_cost_from_env,
    recommend_order_quantity,
)
from app.repositories.empirical_adoptions_postgres_repository import (
    EmpiricalAdoptionPostgresRepository,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.repositories.settings_postgres_repository import (
    SettingsPostgresRepository,
)
from app.repositories.transfer_releases_postgres_repository import (
    TransferReleasePostgresRepository,
)
from app.settings.registry import get_setting
from app.stats.demand import (
    default_as_of,
    demand_stats_for_all,
    demand_stats_for_pair,
)
from app.stats.lead_time import (
    lead_time_stats_for_all,
    lead_time_stats_for_item,
)
from app.units import ceil_units

SERVICE_LEVEL_ENV = "AIERP_SERVICE_LEVEL"
DEFAULT_SERVICE_LEVEL = 0.95
MIN_SERVICE_LEVEL = 0.5
MAX_SERVICE_LEVEL = 0.9999

METHOD = "service-level safety stock with demand and lead-time variability"

DEMAND_BASIS_SETTING = "replenishment.demand_basis"
BASIS_HISTORY = "history"
BASIS_FORECAST = "forecast"

LEAD_TIME_BASIS_SETTING = "replenishment.lead_time_basis"
LT_BASIS_ITEM = "item"
LT_BASIS_MAIN_SUPPLIER = "main_supplier"

NO_FORECAST_NOTE = "no forecast: history used"

_repo = ItemSitePostgresRepository()
_settings_repo = SettingsPostgresRepository()
_adoptions_repo = EmpiricalAdoptionPostgresRepository()
_on_hand_repo = OnHandPostgresRepository()
_open_po_repo = OpenPurchaseLinePostgresRepository()
_transfer_release_repo = TransferReleasePostgresRepository()


# The per-context memo of the rows ``recommendations_for_all`` has
# already computed, keyed by resolved as-of date.  ``None`` (the
# default) means no memo is active: the function then computes on
# every call.  Each request runs in its own context, so two
# concurrent requests never share a memo.
_recommendations_memo: contextvars.ContextVar[dict[date, list[dict]] | None] = (
    contextvars.ContextVar("aierp_recommendations_memo", default=None)
)


@contextmanager
def recommendations_memo():
    """The context in which ``recommendations_for_all`` memoizes its rows.

    Inside, ``recommendations_for_all`` computes its rows once per
    distinct resolved as-of and answers every later call with the
    same as-of in the same context with a copy of those rows built
    by ``copy_tree`` — a caller that changes its rows therefore
    never changes another caller's.  The memo is cleared when the
    context manager exits, including on an exception.  Nested use is allowed and reuses the
    outer memo.  Outside the context manager,
    ``recommendations_for_all`` behaves exactly as without it,
    computing on every call.
    """
    memo = _recommendations_memo.get()
    if memo is None:
        memo = {}
    token = _recommendations_memo.set(memo)
    try:
        yield
    finally:
        _recommendations_memo.reset(token)


def active_demand_basis() -> str:
    """The ``replenishment.demand_basis`` setting, read at call time.

    The stored system value when it is one of the registered allowed
    values, else the registry default (``history``). A value written
    out of band that the setting cannot hold never selects a basis.
    """
    setting = get_setting(DEMAND_BASIS_SETTING)
    if setting is None:
        return BASIS_HISTORY
    stored = _settings_repo.get(setting.scope, "", setting.key)
    if stored is not None and stored["value"] in setting.allowed:
        return stored["value"]
    return setting.default


SERVICE_LEVEL_SETTING = "replenishment.service_level"
SERVICE_LEVEL_BY_CLASS_SETTING = "replenishment.service_level_by_class"
SERVICE_LEVEL_CLASS_SETTINGS = {
    "A": "replenishment.service_level_a",
    "B": "replenishment.service_level_b",
    "C": "replenishment.service_level_c",
}
ORDER_COST_SETTING = "replenishment.order_cost"
HOLDING_RATE_SETTING = "replenishment.holding_rate"
INTERMITTENT_RECOMMENDATIONS_SETTING = "replenishment.intermittent_recommendations"
SERVICE_TARGET_SETTING = "replenishment.service_target"
SERVICE_TARGET_CYCLE = "cycle"
SERVICE_TARGET_FILL_RATE = "fill_rate"


def _stored_system_settings() -> dict:
    """The stored values of every system setting, in one query.

    One settings-table read per call, whatever the number of settings
    the call uses — the same fixed statement budget the call had while
    ``active_demand_basis`` read its one setting alone.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT key, value FROM settings " "WHERE scope = 'system' AND owner = ''"
        )
        return {row["key"]: row["value"] for row in cur.fetchall()}


def _setting_value(stored: dict, key: str) -> float | None:
    """The stored value of a number setting, when it is a number
    within the setting's bounds, else None.

    The same rule ``active_demand_basis`` applies to the enum: a value
    written out of band that the setting cannot hold (not a number, or
    outside ``[min, max]``) never applies.
    """
    setting = get_setting(key)
    if setting is None or setting.type != "number":
        return None
    value = stored.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not (setting.min <= value <= setting.max):
        return None
    return float(value)


def _demand_basis_for(stored: dict) -> str:
    """The ``replenishment.demand_basis`` value for one call's stored
    settings: the stored value when it is one of the registered
    allowed values, else the registry default (``history``)."""
    setting = get_setting(DEMAND_BASIS_SETTING)
    if setting is None:
        return BASIS_HISTORY
    value = stored.get(setting.key)
    if value is not None and value in setting.allowed:
        return value
    return setting.default


def _lead_time_basis_for(stored: dict) -> str:
    """The ``replenishment.lead_time_basis`` value for one call's
    stored settings: the stored value when it is one of the registered
    allowed values, else the registry default (``item``)."""
    setting = get_setting(LEAD_TIME_BASIS_SETTING)
    if setting is None:
        return LT_BASIS_ITEM
    value = stored.get(setting.key)
    if value is not None and value in setting.allowed:
        return value
    return setting.default


def _service_target_for(stored: dict) -> str:
    """The ``replenishment.service_target`` value for one call's
    stored settings: the stored value when it is one of the registered
    allowed values, else the registry default (``cycle``).  Any value
    other than ``fill_rate`` means ``cycle``."""
    setting = get_setting(SERVICE_TARGET_SETTING)
    if setting is None:
        return SERVICE_TARGET_CYCLE
    value = stored.get(setting.key)
    if value is not None and value in setting.allowed:
        return value
    return setting.default


def _service_level_for(stored: dict) -> float:
    """The service level for one call's stored settings: the stored
    ``replenishment.service_level`` value when one is stored and lies
    within the setting's bounds, else ``service_level_from_env()``
    exactly as before."""
    value = _setting_value(stored, SERVICE_LEVEL_SETTING)
    if value is not None:
        return value
    return service_level_from_env()


def _class_levels_enabled(stored: dict) -> bool:
    """The ``replenishment.service_level_by_class`` value of one
    call's stored settings: the stored value when it is a boolean, else
    ``False`` (the registry default).  A value written out of band that
    the setting cannot hold (not a boolean) never enables the class
    levels."""
    setting = get_setting(SERVICE_LEVEL_BY_CLASS_SETTING)
    if setting is None:
        return False
    return stored.get(setting.key) is True


def _intermittent_recommendations_enabled(stored: dict) -> bool:
    """The ``replenishment.intermittent_recommendations`` value of one
    call's stored settings: the stored value when it is a boolean, else
    ``False`` (the registry default).  A value written out of band that
    the setting cannot hold (not a boolean) never recommends the
    intermittent item-sites."""
    setting = get_setting(INTERMITTENT_RECOMMENDATIONS_SETTING)
    if setting is None:
        return False
    return stored.get(setting.key) is True


def _adoptions_relevant(demand_rows: list[dict]) -> bool:
    """True when a stored empirical adoption can distinguish one
    item-site of the population from another's: the population holds
    one item at two or more sites (an adoption distinguishes the
    item's sites) or two or more item-sites whose ``demand_class`` is
    ``intermittent`` or ``lumpy`` (an adoption distinguishes which of
    them is recommended on its empirical figure).  Only then does the
    call read the stored adoptions — one query for the call — and
    carry ``empirical_adopted`` in every result; every other
    population keeps the fixed statement budget the existing proofs
    lock in, treats no item-site as adopted and carries no
    ``empirical_adopted`` key (the API answers false for it).
    """
    sites_by_item: dict[str, set[str]] = {}
    intermittent_item_sites = 0
    for row in demand_rows:
        sites_by_item.setdefault(row["item_code"], set()).add(row["site_code"])
        if row["demand_class"] in ("intermittent", "lumpy"):
            intermittent_item_sites += 1
    return (
        any(len(sites) > 1 for sites in sites_by_item.values())
        or intermittent_item_sites >= 2
    )


def _adopted_pairs() -> set[tuple[str, str]]:
    """Every stored (item_code, site_code) empirical adoption, one
    query for the call."""
    return {(row["item_code"], row["site_code"]) for row in _adoptions_repo.list_all()}


def _class_service_level(stored: dict, abc_class: str) -> float:
    """The stored or default level of one ABC class: the stored
    ``replenishment.service_level_a``/``b``/``c`` value when one is
    stored and lies within the setting's bounds, else the registry
    default of the class' setting."""
    key = SERVICE_LEVEL_CLASS_SETTINGS[abc_class]
    value = _setting_value(stored, key)
    if value is not None:
        return value
    return float(get_setting(key).default)


def _order_cost_for(stored: dict) -> float:
    """The per-order cost for one call's stored settings: the stored
    ``replenishment.order_cost`` value when one is stored and lies
    within the setting's bounds, else ``order_cost_from_env()``
    exactly as before."""
    value = _setting_value(stored, ORDER_COST_SETTING)
    if value is not None:
        return value
    return order_cost_from_env()


def _holding_rate_for(stored: dict) -> float:
    """The annual holding rate for one call's stored settings: the
    stored ``replenishment.holding_rate`` value when one is stored and
    lies within the setting's bounds, else
    ``holding_rate_from_env()`` exactly as before."""
    value = _setting_value(stored, HOLDING_RATE_SETTING)
    if value is not None:
        return value
    return holding_rate_from_env()


def _basis_result(
    d: float,
    sd_d: float,
    lt: float,
    sd_lt: float,
    z: float,
    service_level: float,
) -> dict:
    """The formula's exact results for one set of demand inputs.

    ``safety_stock`` = z * sqrt(LT * sd_d^2 + d^2 * sd_LT^2),
    ``reorder_point`` = d * LT + ``safety_stock``, their whole-unit
    ceilings, the ``inputs`` and the ``arithmetic`` string with these
    values substituted.
    """
    safety_stock = z * math.sqrt(lt * sd_d * sd_d + d * d * sd_lt * sd_lt)
    reorder_point = d * lt + safety_stock
    arithmetic = (
        "safety_stock = z * sqrt(LT * sd_d^2 + d^2 * sd_LT^2) "
        f"= {z:.6f} * sqrt({lt:g} * {sd_d:g}^2 + {d:g}^2 * {sd_lt:g}^2) "
        f"= {safety_stock:.2f}; "
        "reorder_point = d * LT + safety_stock "
        f"= {d:g} * {lt:g} + {safety_stock:.2f} = {reorder_point:.2f}"
    )
    return {
        "safety_stock": safety_stock,
        "reorder_point": reorder_point,
        "safety_stock_units": ceil_units(safety_stock),
        "reorder_point_units": ceil_units(reorder_point),
        "inputs": {
            "d": d,
            "sd_d": sd_d,
            "lt": lt,
            "sd_lt": sd_lt,
            "z": z,
            "service_level": service_level,
        },
        "arithmetic": arithmetic,
    }


def _normal_loss(z: float) -> float:
    """G(z), the standard normal loss function: the standard normal
    density at z, minus z times one minus the standard normal
    distribution at z."""
    dist = statistics.NormalDist()
    return dist.pdf(z) - z * (1.0 - dist.cdf(z))


def _fill_rate_z(sigma: float, level: float, q: float) -> float:
    """The smallest z between 0 and 4 for which ``sigma * G(z) <=
    (1 - level) * q``, G the standard normal loss (``_normal_loss``).

    Found by bisection to within 0.000001; 0 when z = 0 already meets
    the target and 4 when z = 4 does not.
    """
    target = (1.0 - level) * q

    def meets(z: float) -> bool:
        return sigma * _normal_loss(z) <= target

    if meets(0.0):
        return 0.0
    if not meets(4.0):
        return 4.0
    low, high = 0.0, 4.0
    while high - low > 1e-6:
        mid = (low + high) / 2.0
        if meets(mid):
            high = mid
        else:
            low = mid
    return high


def _resolve_as_of(as_of: date | None) -> date:
    """Use the given as_of, or ``app.stats.demand.default_as_of`` when None."""
    if as_of is not None:
        return as_of
    with get_cursor() as cur:
        return default_as_of(cur)


def service_level_from_env() -> float:
    """Read the service level from ``AIERP_SERVICE_LEVEL`` at call time.

    Defaults to 0.95 when the variable is unset. A non-numeric value or
    a value outside the open interval 0.5 to 0.9999 raises
    ``ValueError`` naming the variable.
    """
    raw = os.environ.get(SERVICE_LEVEL_ENV)
    if raw is None:
        return DEFAULT_SERVICE_LEVEL
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(
            f"{SERVICE_LEVEL_ENV} must be a number strictly between "
            f"{MIN_SERVICE_LEVEL} and {MAX_SERVICE_LEVEL}"
        ) from None
    if not (MIN_SERVICE_LEVEL < value < MAX_SERVICE_LEVEL):
        raise ValueError(
            f"{SERVICE_LEVEL_ENV} must be a number strictly between "
            f"{MIN_SERVICE_LEVEL} and {MAX_SERVICE_LEVEL}"
        )
    return value


def _usable_lead_time_days(value: object) -> float | None:
    """The lead time in days when the value is a finite number of at
    least 0 days, else None.

    A negative or non-finite lead time is unusable: the safety-stock
    formula would take the square root of a negative number (a negative
    lead time) or overflow (a non-finite one), and neither is a lead
    time.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        days = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(days) or days < 0:
        return None
    return days


def _usable_lead_time(
    lead_time: dict | None, master_lead_time_days: object
) -> tuple[float, float, bool] | None:
    """The lead time the formula uses, or None when none is usable.

    The pooled ``mean_days``/``std_days`` when the pool is not
    low-confidence and both are usable, else
    ``master_lead_time_days`` with sd_LT 0 when it is usable; the third
    element says whether the master lead time was used.
    """
    if lead_time is not None and not lead_time["low_confidence"]:
        mean_days = _usable_lead_time_days(lead_time["mean_days"])
        std_days = _usable_lead_time_days(lead_time["std_days"])
        if mean_days is not None and std_days is not None:
            return mean_days, std_days, False
    master = _usable_lead_time_days(master_lead_time_days)
    if master is not None:
        return master, 0.0, True
    return None


def _empirical(
    demand: dict,
    lead_time: dict | None,
    master_lead_time_days: float | None,
    as_of: date,
    service_level: float,
) -> dict | None:
    """The empirical reorder point of one item-site, or None.

    Set only for an item-site whose ``demand_class`` is
    ``intermittent`` or ``lumpy`` and that has a usable lead time:
    ``empirical_reorder_point`` over the demand row's
    ``daily_totals``, its ``daily_window_start`` and the call's
    ``as_of``, at the lead time in days ``recommend`` itself would
    use (``_usable_lead_time`` over the same pooled stats and master
    lead time) and at the service level the item-site is recommended
    at (its class level when service levels by class are on). Every
    other demand class, and an intermittent or lumpy item-site with
    no usable lead time, is None.
    """
    if demand.get("demand_class") not in ("intermittent", "lumpy"):
        return None
    selection = _usable_lead_time(lead_time, master_lead_time_days)
    if selection is None:
        return None
    daily_totals = demand.get("daily_totals")
    window_start = demand.get("daily_window_start")
    if not isinstance(daily_totals, dict) or not isinstance(window_start, date):
        return None
    return empirical_reorder_point(
        daily_totals, window_start, as_of, selection[0], service_level
    )


def _recommend_empirical(
    result: dict,
    demand: dict,
    lead_time: dict | None,
    master_lead_time_days: float | None,
    service_level: float,
) -> None:
    """Recommend one item-site on its empirical lead-time demand, in
    place — the ``replenishment.intermittent_recommendations`` switch
    applied by the caller when it is on.

    The result stands on the item-site's ``empirical`` reorder point:
    status ``"ok"``, the empirical ``safety_stock``/``reorder_point``
    and their whole-unit fields, ``method``
    ``app.recommend.empirical.METHOD`` and its arithmetic, and the note
    ``"empirical lead-time demand (intermittent)"`` added to the notes
    the result already carries.  ``inputs`` carries the history-basis
    values the formula recommendation would: ``d`` and ``sd_d`` from
    the demand statistics, ``lt`` and ``sd_lt`` from the lead time the
    same ``_usable_lead_time`` selection the empirical itself was
    computed at picks, and ``z`` of the service level with the service
    level; ``demand_basis`` is ``"history"`` and ``other_basis`` null.
    ``_finish`` then computes ``delta_reorder_point``,
    ``suggested_order_quantity`` and ``order_quantity`` from these
    values exactly as for any "ok" recommendation, and the result's
    ``empirical`` stays present.  A result without a non-null
    ``empirical`` — every other demand class, and an intermittent or
    lumpy item-site with no usable lead time — is left untouched.
    """
    empirical = result.get("empirical")
    if empirical is None or demand.get("demand_class") not in (
        "intermittent",
        "lumpy",
    ):
        return
    selection = _usable_lead_time(lead_time, master_lead_time_days)
    if selection is None:
        return
    lead_time_days, sd_lead_time, _ = selection
    result["status"] = "ok"
    result["safety_stock"] = empirical["safety_stock"]
    result["reorder_point"] = empirical["reorder_point"]
    result["safety_stock_units"] = empirical["safety_stock_units"]
    result["reorder_point_units"] = empirical["reorder_point_units"]
    result["inputs"] = {
        "d": float(demand["mean_daily"]),
        "sd_d": float(demand["std_daily"]),
        "lt": lead_time_days,
        "sd_lt": sd_lead_time,
        "z": statistics.NormalDist().inv_cdf(service_level),
        "service_level": service_level,
    }
    result["arithmetic"] = empirical["arithmetic"]
    result["method"] = EMPIRICAL_METHOD
    result["notes"] = [*result["notes"], "empirical lead-time demand (intermittent)"]
    result["demand_basis"] = BASIS_HISTORY
    result["other_basis"] = None


def recommend(
    demand: dict,
    lead_time: dict | None,
    service_level: float = DEFAULT_SERVICE_LEVEL,
    master_lead_time_days: float | None = None,
    forecast: dict | None = None,
    active_basis: str = BASIS_HISTORY,
) -> dict:
    """Recommend a safety stock and reorder point for one item-site.

    ``demand`` is one result of ``app.stats.demand`` (``mean_daily``,
    ``std_daily``, ``demand_class``, ``recommendable``); ``lead_time`` is
    one pooled item result of ``app.stats.lead_time`` (``mean_days``,
    ``std_days``, ``n_used``, ``low_confidence``) or None; ``z`` is
    ``statistics.NormalDist().inv_cdf(service_level)``.

    ``forecast`` is one ``app.forecast.basis.forecast_demand`` result
    (or None when the item-site has no forecast); ``active_basis`` is
    the ``replenishment.demand_basis`` value. With a forecast the same
    formula — same lead time, service level and rounding — runs once on
    the historical demand inputs and once on the forecast's ``daily``
    and ``sd_daily``; the result's existing fields
    (``safety_stock``, ``reorder_point``, the unit fields, ``inputs``,
    ``arithmetic``) come from the active basis, ``demand_basis`` names
    it, and ``other_basis`` holds the other basis' ``basis``,
    ``safety_stock``, ``reorder_point``, unit fields and ``inputs``
    (null when the status is not ``"ok"`` or the other basis does not
    exist). A not-ok result carries null ``safety_stock`` and
    ``reorder_point`` and ``demand_basis`` naming the active basis —
    the no-forecast fallback keeps ``history`` — with a null
    ``other_basis``. A result built on the forecast adds the note
    ``"forecast demand over N days"`` and, with no backtest,
    ``"no backtest: historical variability used"``; a ``forecast``
    basis with no forecast is built on the history with the note
    ``"no forecast: history used"``.

    Returns ``status`` ``"not_recommendable_demand"`` (carrying
    ``demand_class``) when the demand is not recommendable. Otherwise
    ``"insufficient_lead_time"`` when no usable lead time exists:
    ``lead_time`` is None, low-confidence, or its ``mean_days``/
    ``std_days`` negative or non-finite, and no usable
    ``master_lead_time_days`` fallback is given; with a master lead
    time it uses LT = that value and sd_LT = 0 and notes ``"master
    lead time used"``. Otherwise ``"not_recommendable_overflow"``
    (carrying null ``safety_stock``/``reorder_point`` like every other
    not-ok status) when the active basis' inputs overflow the formula
    — its exact results are not finite floats and no recommendation
    can carry them — never a raised math domain error or
    ``OverflowError``. Otherwise ``"ok"`` with the
    exact float ``safety_stock`` = z * sqrt(LT * sd_d^2 + d^2 * sd_LT^2)
    and ``reorder_point`` = d * LT + ``safety_stock`` of the active
    basis, their whole-unit ceilings, the ``inputs`` used, an
    ``arithmetic`` string writing out both formulas with the values
    substituted and the results, the ``method``, and ``notes``
    (including ``"variable demand"`` for erratic demand).
    """
    z = statistics.NormalDist().inv_cdf(service_level)
    demand_class = demand["demand_class"]
    notes: list[str] = []
    if demand_class == "erratic":
        notes.append("variable demand")

    def _not_ok(status: str) -> dict:
        if active_basis == BASIS_FORECAST and forecast is None:
            notes.append(NO_FORECAST_NOTE)
            # The no-forecast fallback: the row stands on the history
            # basis, as a built recommendation would there.
            demand_basis = BASIS_HISTORY
        else:
            demand_basis = active_basis
        return {
            "status": status,
            "demand_class": demand_class,
            "safety_stock": None,
            "reorder_point": None,
            "method": METHOD,
            "notes": notes,
            "demand_basis": demand_basis,
            "other_basis": None,
        }

    if not demand["recommendable"]:
        return _not_ok("not_recommendable_demand")

    lead_time_selection = _usable_lead_time(lead_time, master_lead_time_days)
    if lead_time_selection is None:
        return _not_ok("insufficient_lead_time")
    lt, sd_lt, master_used = lead_time_selection
    if master_used:
        notes.append("master lead time used")

    def _basis_or_none(d_value: float, sd_d_value: float) -> dict | None:
        # One basis' formula result, or None when the inputs overflow
        # the formula: the exact results are not finite floats and
        # math.ceil would raise OverflowError (or ValueError on a NaN
        # radicand) — the caller reports a not-ok status instead.
        try:
            return _basis_result(d_value, sd_d_value, lt, sd_lt, z, service_level)
        except (OverflowError, ValueError):
            return None

    history = _basis_or_none(
        float(demand["mean_daily"]),
        float(demand["std_daily"]),
    )
    forecast_basis = None
    if forecast is not None:
        forecast_basis = _basis_or_none(
            float(forecast["daily"]),
            float(forecast["sd_daily"]),
        )
        if forecast_basis is None and active_basis == BASIS_FORECAST:
            return _not_ok("not_recommendable_overflow")

    if active_basis == BASIS_FORECAST and forecast_basis is not None:
        built, basis_name = forecast_basis, BASIS_FORECAST
        other, other_name = history, BASIS_HISTORY
        notes.append(f"forecast demand over {forecast['window_days']} days")
        if forecast["variability"] == "history":
            notes.append("no backtest: historical variability used")
    else:
        built, basis_name = history, BASIS_HISTORY
        if active_basis == BASIS_FORECAST:
            notes.append(NO_FORECAST_NOTE)
        other = forecast_basis
        other_name = BASIS_FORECAST if other is not None else None

    if built is None:
        return _not_ok("not_recommendable_overflow")

    return {
        "status": "ok",
        "demand_class": demand_class,
        "safety_stock": built["safety_stock"],
        "reorder_point": built["reorder_point"],
        "safety_stock_units": built["safety_stock_units"],
        "reorder_point_units": built["reorder_point_units"],
        "inputs": built["inputs"],
        "arithmetic": built["arithmetic"],
        "method": METHOD,
        "notes": notes,
        "demand_basis": basis_name,
        "other_basis": (
            {
                "basis": other_name,
                "safety_stock": other["safety_stock"],
                "reorder_point": other["reorder_point"],
                "safety_stock_units": other["safety_stock_units"],
                "reorder_point_units": other["reorder_point_units"],
                "inputs": other["inputs"],
            }
            if other is not None
            else None
        ),
    }


def _item_sites_by_key() -> dict[tuple[str, str], dict]:
    """Every item_sites row keyed by (item_code, site_code)."""
    rows = _repo.list_rows(10**9, 0)
    return {(row["item_code"], row["site_code"]): row for row in rows}


def _int_units(value: float | None) -> int:
    """The quantity in whole units: 0 when absent or non-finite."""
    if value is None or not math.isfinite(value):
        return 0
    return int(value)


def _on_hand_by_key() -> dict[tuple[str, str], float]:
    """The quantity_on_hand of every on_hand row keyed by its pair."""
    return {
        (row["item_code"], row["site_code"]): row["quantity_on_hand"]
        for row in _on_hand_repo.list_rows(10**9, 0)
    }


def _in_flight_units_by_key(
    open_lines: list[dict],
    as_of: date,
) -> dict[tuple[str, str], float]:
    """The in-flight release units of every (item_code, site_code): the
    quantity of each line the aggregate projection keeps as in-flight
    supply as of ``as_of`` (the call's resolved as-of date, never the
    wall-clock date — the same date the projection clamps its
    in-flight arrivals against), read once per call through
    ``app.aggregate.projection._in_flight_release_lines`` and filtered
    by ``_in_flight_supply_lines`` against the same newest snapshot's
    ``_feed_order_numbers`` — a released order the feed picked up is
    counted from the feed only, never twice, and a row with a
    malformed month, site code, order number or lines contributes
    nothing (never a 500, exactly as in the projection)."""
    # projection imports this module, so import it at call time, never
    # at module level.
    from app.aggregate import projection

    totals: dict[tuple[str, str], float] = {}
    for line in projection._in_flight_supply_lines(
        projection._in_flight_release_lines(as_of),
        as_of,
        projection._feed_order_numbers(open_lines),
    ):
        units = line["open_units"]
        if units is None or not math.isfinite(units):
            continue
        key = (line["item_code"], line["site_code"])
        totals[key] = totals.get(key, 0.0) + units
    return totals


def _in_flight_transfer_units_by_key() -> dict[tuple[str, str], float]:
    """The in-flight transfer units of every (item_code, site_code),
    read once per call through
    ``TransferReleasePostgresRepository.list_in_flight`` (every
    ``transfer_releases`` row whose outcome is ``created`` or
    ``exists`` and whose ``reference_nbr`` is non-null, one SELECT
    with no limit).

    A row is in flight while its ``erp_status`` is null or none of
    ``Released``, ``Completed`` and ``Canceled`` — once the ERP reads
    the order ``Released`` the row stops counting (the on-hand sync
    then shows the moved stock, so nothing is counted twice).  Each
    of the row's lines (``item_code``, ``quantity``) adds its
    quantity to the receiving pair (item_code, the row's
    ``to_site_code``) and subtracts it from the sending pair
    (item_code, the row's ``from_site_code``) — that stock is
    committed to leave.  A row with malformed lines or a non-numeric
    quantity contributes nothing (never a 500)."""
    totals: dict[tuple[str, str], float] = {}
    for row in _transfer_release_repo.list_in_flight():
        if row.get("erp_status") in ("Released", "Completed", "Canceled"):
            continue
        from_site = row.get("from_site_code")
        to_site = row.get("to_site_code")
        if not isinstance(from_site, str) or not isinstance(to_site, str):
            continue
        lines = row.get("lines")
        if not isinstance(lines, list):
            continue
        for line in lines:
            if not isinstance(line, dict):
                continue
            item_code = line.get("item_code")
            quantity = line.get("quantity")
            if (
                not isinstance(item_code, str)
                or not item_code
                or isinstance(quantity, bool)
                or not isinstance(quantity, (int, float))
            ):
                continue
            try:
                quantity = float(quantity)
            except OverflowError:
                continue
            if not math.isfinite(quantity):
                continue
            totals[(item_code, to_site)] = (
                totals.get((item_code, to_site), 0.0) + quantity
            )
            totals[(item_code, from_site)] = (
                totals.get((item_code, from_site), 0.0) - quantity
            )
    return totals


def _on_order_units_by_key(as_of: date) -> dict[tuple[str, str], int]:
    """The sum of the open_units of the open purchase lines of every
    (item_code, site_code), read once from
    ``OpenPurchaseLinePostgresRepository.list_latest`` (the newest
    sync), plus the pair's in-flight release units evaluated as of
    ``as_of`` (``_in_flight_units_by_key``) and the pair's in-flight
    transfer units (``_in_flight_transfer_units_by_key``); pairs
    without open lines or in-flight releases or transfers are
    absent."""
    totals: dict[tuple[str, str], float] = {}
    open_lines = _open_po_repo.list_latest()
    for line in open_lines:
        units = line["open_units"]
        if units is None or not math.isfinite(units):
            continue
        key = (line["item_code"], line["site_code"])
        totals[key] = totals.get(key, 0.0) + units
    for key, units in _in_flight_units_by_key(open_lines, as_of).items():
        totals[key] = totals.get(key, 0.0) + units
    for key, units in _in_flight_transfer_units_by_key().items():
        totals[key] = totals.get(key, 0.0) + units
    return {key: _int_units(total) for key, total in totals.items()}


def _on_hand_units_for(item_code: str, site_code: str) -> int:
    """The pair's on-hand units (0 when it has no on_hand row)."""
    row = _on_hand_repo.get_row(item_code, site_code)
    return _int_units(row["quantity_on_hand"] if row is not None else None)


def _on_order_units_for(item_code: str, site_code: str, as_of: date) -> int:
    """The pair's open purchase units plus its in-flight release units
    evaluated as of ``as_of`` (``_in_flight_units_by_key``) and its
    in-flight transfer units (``_in_flight_transfer_units_by_key``; 0
    when it has no open lines or in-flight releases or transfers)."""
    open_lines = _open_po_repo.list_latest()
    total = 0.0
    for line in open_lines:
        if (line["item_code"], line["site_code"]) != (item_code, site_code):
            continue
        units = line["open_units"]
        if units is not None and math.isfinite(units):
            total += units
    total += _in_flight_units_by_key(open_lines, as_of).get((item_code, site_code), 0.0)
    total += _in_flight_transfer_units_by_key().get((item_code, site_code), 0.0)
    return _int_units(total)


def _main_supplier_entry(suppliers: object) -> dict | None:
    """The main supplier's entry of an item's per-supplier breakdown.

    The entry with the most ``n_observations``, ties broken by the
    lowest ``supplier_code``; None when the breakdown is absent or
    carries no usable supplier entry.
    """
    if not isinstance(suppliers, list):
        return None
    entries = []
    for entry in suppliers:
        if not isinstance(entry, dict):
            continue
        n_observations = entry.get("n_observations")
        if (
            isinstance(n_observations, bool)
            or not isinstance(n_observations, (int, float))
            or not isinstance(entry.get("supplier_code"), str)
        ):
            continue
        entries.append(entry)
    if not entries:
        return None

    def _key(entry: dict) -> tuple[int, str]:
        return (-int(entry["n_observations"]), entry["supplier_code"])

    return min(entries, key=_key)


def _lead_time_stats_for_basis(lead_time: dict | None, basis: str) -> dict | None:
    """The lead-time statistics one item-site's recommendation uses.

    ``item`` (the default) and every value the setting cannot hold
    keep the pooled statistics.  ``main_supplier`` replaces the pooled
    figures (``mean_days``, ``std_days``, ``n_observations``,
    ``n_used``, ``n_dropped``, ``fence``, ``min_days``, ``max_days``
    and ``low_confidence``) by the main supplier's entry of the
    breakdown when that supplier has at least 3 used observations and
    is not low confidence; the pooled figures stand otherwise.  The
    ``suppliers`` breakdown, ``item_code`` and ``as_of`` carry over
    unchanged, and the result is a new dict — the caller's row, which
    the ABC class read shares, is never mutated.
    """
    if lead_time is None or basis != LT_BASIS_MAIN_SUPPLIER:
        return lead_time
    entry = _main_supplier_entry(lead_time.get("suppliers"))
    n_used = entry["n_used"] if entry is not None else None
    if (
        entry is None
        or isinstance(n_used, bool)
        or not isinstance(n_used, (int, float))
        or n_used < 3
        or entry["low_confidence"]
    ):
        return lead_time
    return {
        **lead_time,
        "n_observations": entry["n_observations"],
        "n_used": entry["n_used"],
        "n_dropped": entry["n_dropped"],
        "fence": entry["fence"],
        "mean_days": entry["mean_days"],
        "std_days": entry["std_days"],
        "min_days": entry["min_days"],
        "max_days": entry["max_days"],
        "low_confidence": entry["low_confidence"],
    }


def _lead_time_selection(
    lead_time: dict | None, item_site_row: dict | None
) -> tuple[float | None, float | None]:
    """The lead times the recommendation and its forecast use.

    The pooled lead time stands in when it is usable (not low
    confidence, with a finite non-negative mean and standard deviation
    — an unusable ``std_days`` is no more usable than an unusable
    ``mean_days``); the item-site's ``master_lead_time_days`` is passed
    through when the key has fewer than 3 lead-time observations
    (including none) or the pooled lead time is unusable, so the
    master-lead-time fallback is reachable in exactly the cases
    ``recommend`` takes it (``recommend`` re-validates the value). The
    forecast's window uses the same lead time the recommendation uses
    (the pooled mean, or the master fallback when it applies); without
    a usable lead time the forecast basis cannot be built at all.
    """
    pooled_usable = (
        lead_time is not None
        and not lead_time["low_confidence"]
        and _usable_lead_time_days(lead_time["mean_days"]) is not None
        and _usable_lead_time_days(lead_time["std_days"]) is not None
    )
    if lead_time is None or lead_time["n_used"] < 3 or not pooled_usable:
        master_lead_time_days = (
            item_site_row["master_lead_time_days"]
            if item_site_row is not None
            else None
        )
    else:
        master_lead_time_days = None
    lead_time_days = lead_time["mean_days"] if pooled_usable else master_lead_time_days
    if _usable_lead_time_days(lead_time_days) is None:
        # The selected lead time is unusable in the formula (negative
        # or non-finite): the master lead time stands in when it is
        # usable, else there is no lead time at all.
        if master_lead_time_days is None:
            master_lead_time_days = (
                item_site_row["master_lead_time_days"]
                if item_site_row is not None
                else None
            )
        lead_time_days = _usable_lead_time_days(master_lead_time_days)
    return master_lead_time_days, lead_time_days


def _row_ok_and_daily(
    row: dict,
    lead_time: dict | None,
    item_site_row: dict | None,
    history: list[dict] | None,
    counted: list[tuple[int, date, date, float]],
    active_basis: str,
) -> tuple[bool, float]:
    """The "ok" status and the active basis' daily demand of one
    demand statistics row, from inputs the list already loaded (no
    query).

    The same rule ``recommend`` applies: the row's recommendation is
    "ok" when it is recommendable and a usable lead time exists (the
    pooled mean, or the master lead time when it stands in); the daily
    demand is the forecast's ``daily`` for a ``forecast`` basis with a
    forecast, else the row's ``mean_daily``.
    """
    _master_lead_time_days, lead_time_days = _lead_time_selection(
        lead_time, item_site_row
    )
    if not row["recommendable"]:
        return False, 0.0
    if lead_time_days is None:
        return False, 0.0
    daily = float(row["mean_daily"])
    if active_basis == BASIS_FORECAST and lead_time_days is not None:
        forecast = forecast_demand_for(row, history, counted, float(lead_time_days))
        if forecast is not None:
            daily = float(forecast["daily"])
    return True, daily


def _abc_class_by_key(
    demand_rows: list[dict],
    lead_time_by_item: dict[str, dict],
    item_site_rows: dict[tuple[str, str], dict],
    histories: dict[tuple[str, str], list[dict] | None],
    counted: dict[tuple[str, str], list[tuple[int, date, date, float]]],
    active_basis: str,
) -> dict[tuple[str, str], str | None]:
    """The ABC class of every demand item-site, once for the call.

    Built by ``app.recommend.abc.classify`` from the same annual values
    ``app.recommend.abc.abc_for_all`` uses — ``365 * d * unit_cost``
    for an "ok" recommendation whose ``d`` and ``unit_cost`` are both
    positive, null for every other item-site — with ``d`` the daily
    demand of the row's active basis and ``unit_cost`` the value the
    item_sites row carries, all from inputs the list already loaded (no
    query).  Imported here, not at module level, because
    ``app.recommend.abc`` imports this module.
    """
    from app.recommend.abc import _positive_finite_number, classify

    items = []
    for row in demand_rows:
        key = (row["item_code"], row["site_code"])
        item_site_row = item_site_rows.get(key)
        ok, daily = _row_ok_and_daily(
            row,
            lead_time_by_item.get(row["item_code"]),
            item_site_row,
            histories.get(key),
            counted.get(key, []),
            active_basis,
        )
        unit_cost = item_site_row["unit_cost"] if item_site_row is not None else None
        daily_number = _positive_finite_number(daily)
        unit_cost_number = _positive_finite_number(unit_cost)
        annual_value = None
        if ok and daily_number is not None and unit_cost_number is not None:
            annual_value = 365.0 * daily_number * unit_cost_number
        items.append(
            {
                "item_code": row["item_code"],
                "site_code": row["site_code"],
                "annual_value": annual_value,
            }
        )
    return {
        (row["item_code"], row["site_code"]): row["abc_class"]
        for row in classify(items)
    }


def _signal_units(
    as_of: date,
    lead_time_days: float | None,
    counted: list[tuple[int, date, date, float]],
    signals: list[tuple[int, date, date, float]],
) -> float:
    """The counted signals' units over the lead-time window.

    The sum, over the first ``ceil(lead_time_days)`` days after
    ``as_of`` (at least 1, at most 360, the window
    ``app.forecast.basis._window_days`` gives), of each of the pair's
    counted signals' ``quantity`` divided by the days in its window,
    for each day the signal covers — a day a counted override covers
    takes no signal share, the effective forecast's rule.  0 when
    there is no usable lead time or no counted signal at all.
    """
    if lead_time_days is None or not signals:
        return 0.0
    window = _window_days(lead_time_days)
    first_day = as_of + timedelta(days=1)
    last_day = as_of + timedelta(days=window)
    covered: set[date] = set()
    for _override_id, period_start, period_end, _quantity in counted:
        first = max(first_day, period_start)
        last = min(last_day, period_end)
        if first <= last:
            for offset in range((last - first).days + 1):
                covered.add(first + timedelta(days=offset))
    total = 0.0
    for _signal_id, start_date, end_date, quantity in signals:
        first = max(first_day, start_date)
        last = min(last_day, end_date)
        if first > last:
            continue
        daily = quantity / ((end_date - start_date).days + 1)
        for offset in range((last - first).days + 1):
            if first + timedelta(days=offset) not in covered:
                total += daily
    return total


def _with_demand_signals(
    result: dict,
    as_of: date,
    lead_time_days: float | None,
    counted: list[tuple[int, date, date, float]],
    signals: list[tuple[int, date, date, float]],
) -> None:
    """The counted signals' share of the reorder point, in place.

    Applied to the ``recommend`` result before ``_finish``, on the
    list and the single-key path alike: ``signal_units`` the counted
    signals' units over the lead-time window (0 for a row with none,
    and for every not-ok row, which also reads a null
    ``reorder_point_before_signals_units``),
    ``reorder_point_before_signals_units`` the row's
    ``reorder_point_units`` before the signals, and — when
    ``signal_units`` is above 0 — the row's ``reorder_point`` and
    ``reorder_point_units`` the signals added on (the whole-unit
    ceiling by ``ceil_units``), its ``arithmetic`` the stated signal
    clause appended, its notes the
    ``"includes demand signals over N days"`` note (N the window's
    days) and, when it exists, the same units added to
    ``other_basis``' ``reorder_point`` and ``reorder_point_units``.
    ``safety_stock`` and ``inputs``, ``d`` included, stay as they
    were.
    """
    if result.get("status") != "ok":
        result["signal_units"] = 0.0
        result["reorder_point_before_signals_units"] = None
        return
    signal_units = _signal_units(as_of, lead_time_days, counted, signals)
    result["signal_units"] = signal_units
    result["reorder_point_before_signals_units"] = result["reorder_point_units"]
    if signal_units <= 0:
        return
    before = result["reorder_point"]
    result["reorder_point"] = before + signal_units
    result["reorder_point_units"] = ceil_units(result["reorder_point"])
    result["arithmetic"] = (
        result["arithmetic"]
        + f"; with demand signals = {before:.2f} + {signal_units:.2f}"
        f" = {result['reorder_point']:.2f}"
    )
    result["notes"] = [
        *result["notes"],
        f"includes demand signals over {_window_days(lead_time_days)} days",
    ]
    other_basis = result.get("other_basis")
    if other_basis is not None:
        other_basis["reorder_point"] = other_basis["reorder_point"] + signal_units
        other_basis["reorder_point_units"] = ceil_units(other_basis["reorder_point"])


def _apply_fill_rate(
    result: dict,
    item_site_row: dict | None,
    order_cost: float,
    holding_rate: float,
    service_level_source: dict,
) -> None:
    """The ``fill_rate`` service target, in place, on one
    recommendation computed with the normal safety-stock formula.

    The row's order quantity Q is the recommended order quantity
    ``_finish`` will attach — ``recommend_order_quantity`` for the
    row's ``inputs["d"]``, its ``unit_cost`` from ``item_site_row``,
    the call's per-order cost and holding rate — and its sigma the
    formula's ``sqrt(LT * sd_d^2 + d^2 * sd_LT^2)`` from the same
    ``inputs``.  When Q and sigma are both finite numbers above 0, the
    row's z is the smallest z between 0 and 4 for which
    ``sigma * G(z) <= (1 - level) * Q`` (``_fill_rate_z``):
    ``inputs.z`` that z, ``safety_stock`` z * sigma,
    ``reorder_point`` ``d * LT`` plus that safety stock, the unit
    fields rounded as today, ``arithmetic`` the fill-rate clause
    prefixed, and ``service_level_source.source`` ``fill_rate``.  A
    row without such a Q or sigma — every not-ok row, the empirical
    path and every row whose order quantity is not recommendable —
    keeps the cycle computation untouched, ``service_level_source``
    included.
    """
    if result.get("method") != METHOD:
        return
    inputs = result.get("inputs")
    if not isinstance(inputs, dict):
        return
    quantity = recommend_order_quantity(
        inputs["d"],
        item_site_row["unit_cost"] if item_site_row is not None else None,
        order_cost,
        holding_rate,
    )
    if quantity is None:
        return
    q = quantity["order_quantity"]
    try:
        sigma = math.sqrt(
            inputs["lt"] * inputs["sd_d"] * inputs["sd_d"]
            + inputs["d"] * inputs["d"] * inputs["sd_lt"] * inputs["sd_lt"]
        )
    except (OverflowError, TypeError, ValueError):
        return
    if not (math.isfinite(q) and q > 0.0) or not (math.isfinite(sigma) and sigma > 0.0):
        return
    level = inputs["service_level"]
    z = _fill_rate_z(sigma, level, q)
    safety_stock = z * sigma
    reorder_point = inputs["d"] * inputs["lt"] + safety_stock
    if not (math.isfinite(safety_stock) and math.isfinite(reorder_point)):
        # The cycle results were finite; an overflowed fill-rate
        # result is not representable — keep the cycle computation.
        return
    result["inputs"]["z"] = z
    result["safety_stock"] = safety_stock
    result["reorder_point"] = reorder_point
    result["safety_stock_units"] = ceil_units(safety_stock)
    result["reorder_point_units"] = ceil_units(reorder_point)
    result["arithmetic"] = (
        f"fill rate target {level}: z = {z:.4f} solves sigma * G(z) <= "
        f"(1 - {level}) * Q = {(1.0 - level) * q:.2f}; "
        f"{result['arithmetic']}"
    )
    service_level_source["source"] = SERVICE_TARGET_FILL_RATE


def _finish(
    result: dict,
    item_code: str,
    site_code: str,
    as_of: date,
    item_site_row: dict | None,
    on_hand_units: int,
    on_order_units: int,
    order_cost: float,
    holding_rate: float,
    service_level_source: dict | None = None,
    empirical_adopted: bool | None = None,
) -> dict:
    """Attach identity, the current settings, the delta and the position.

    ``current`` holds the item-site's ``reorder_point`` and
    ``safety_stock`` from ``item_sites`` (None when the row is absent);
    ``delta_reorder_point`` is the current ``reorder_point`` minus the
    recommended one when both exist. ``on_hand_units`` is the pair's
    ``on_hand`` quantity (0 when the pair has no on_hand row),
    ``on_order_units`` the sum of the pair's open purchase lines'
    ``open_units`` (0 when none), ``unit_cost`` the item-site's
    ``unit_cost`` from ``item_sites`` (None when the row or the cost is
    absent), ``source_system`` the item-site's ``source_system`` from
    ``item_sites`` (None when the row or the value is absent), and
    ``suggested_order_quantity``
    ``max(0, reorder_point_units - (on_hand_units + on_order_units))``
    when the recommendation has a ``reorder_point_units`` (a
    recommendable item-site), else None (a stopped, excess-only or
    otherwise non-recommendable item-site). ``order_quantity`` is the
    ``app.recommend.order_quantity.recommend_order_quantity`` result
    for the recommendation's ``inputs["d"]``, its ``unit_cost``, the
    call's ``order_cost`` and ``holding_rate`` (read once per call,
    never once per item-site), extended with
    ``recommended_max_units`` (``reorder_point_units`` plus
    ``order_quantity_units``) and the item-site's current
    ``order_qty``/``max_qty`` as ``current_order_qty``/
    ``current_max_qty`` (null when absent) — null when the
    recommendation has no ``reorder_point_units`` or the order
    quantity is not recommendable. It never touches
    ``reorder_point``, ``safety_stock``, ``delta_reorder_point``,
    ``demand_basis`` or any other existing field.
    ``empirical_adopted`` names the item-site's stored empirical
    adoption: when not None the result carries ``empirical_adopted``
    (true for an adopted item-site, false otherwise); None leaves the
    key absent (the API answers false for it).
    """
    result["item_code"] = item_code
    result["site_code"] = site_code
    result["as_of"] = as_of
    result["service_level_source"] = (
        service_level_source
        if service_level_source is not None
        else {"abc_class": None, "source": "single"}
    )
    if empirical_adopted is not None:
        result["empirical_adopted"] = empirical_adopted
    current_reorder_point = (
        item_site_row["reorder_point"] if item_site_row is not None else None
    )
    current_safety_stock = (
        item_site_row["safety_stock"] if item_site_row is not None else None
    )
    result["current"] = {
        "reorder_point": current_reorder_point,
        "safety_stock": current_safety_stock,
    }
    if current_reorder_point is not None and result.get("reorder_point") is not None:
        result["delta_reorder_point"] = current_reorder_point - result["reorder_point"]
    else:
        result["delta_reorder_point"] = None
    result["on_hand_units"] = on_hand_units
    result["on_order_units"] = on_order_units
    result["unit_cost"] = (
        item_site_row["unit_cost"] if item_site_row is not None else None
    )
    result["source_system"] = (
        item_site_row.get("source_system") if item_site_row is not None else None
    )
    reorder_point_units = result.get("reorder_point_units")
    if reorder_point_units is None:
        result["suggested_order_quantity"] = None
        result["order_quantity"] = None
    else:
        result["suggested_order_quantity"] = max(
            0, reorder_point_units - (on_hand_units + on_order_units)
        )
        quantity = recommend_order_quantity(
            result["inputs"]["d"],
            result["unit_cost"],
            order_cost,
            holding_rate,
        )
        if quantity is None:
            result["order_quantity"] = None
        else:
            result["order_quantity"] = {
                **quantity,
                "recommended_max_units": reorder_point_units
                + quantity["order_quantity_units"],
                "current_order_qty": (
                    item_site_row["order_qty"] if item_site_row is not None else None
                ),
                "current_max_qty": (
                    item_site_row["max_qty"] if item_site_row is not None else None
                ),
            }
    return result


def _attach(
    item_code: str,
    site_code: str,
    as_of: date,
    demand: dict,
    lead_time: dict | None,
    service_level: float,
    item_site_row: dict | None,
    on_hand_units: int,
    on_order_units: int,
    order_cost: float,
    holding_rate: float,
    active_basis: str,
    service_level_source: dict,
    intermittent_recommendations: bool,
    empirical_adopted: bool | None,
    service_target: str,
) -> dict:
    """Run ``recommend`` and attach identity, current settings, the
    demand signals and the delta and position.

    The per-item-site path: the item-site's forecast
    (``app.forecast.basis.forecast_demand_for`` over the lead time the
    recommendation uses, from the demand row the caller already read,
    the pair's 30-day history and the pair's counted overrides) is
    built here; the pair's counted signals are read with one
    ``app.forecast.signals.counted_signals`` query for the call and
    added to the result by ``_with_demand_signals`` before
    ``_finish``; ``active_basis`` is the ``replenishment.demand_basis``
    value the call's one settings read gave and
    ``intermittent_recommendations`` the value of the
    ``replenishment.intermittent_recommendations`` switch that same
    read gave, and ``empirical_adopted`` the item-site's stored
    empirical adoption (None when the call did not read the
    adoptions).
    """
    master_lead_time_days, lead_time_days = _lead_time_selection(
        lead_time, item_site_row
    )
    forecast = None
    counted: list[tuple[int, date, date, float]] = []
    signals: list[tuple[int, date, date, float]] = []
    if lead_time_days is not None:
        pair = (item_code, site_code)
        counted = counted_overrides([pair])[pair]
        signals = counted_signals([pair])[pair]
        history = (
            history_for_pair(item_code, site_code, demand["as_of"])
            if demand.get("demand_class") != "no_demand"
            else None
        )
        forecast = forecast_demand_for(demand, history, counted, float(lead_time_days))
    result = recommend(
        demand,
        lead_time,
        service_level=service_level,
        master_lead_time_days=master_lead_time_days,
        forecast=forecast,
        active_basis=active_basis,
    )
    result["empirical"] = _empirical(
        demand, lead_time, master_lead_time_days, as_of, service_level
    )
    if intermittent_recommendations or empirical_adopted:
        _recommend_empirical(
            result, demand, lead_time, master_lead_time_days, service_level
        )
    if service_target == SERVICE_TARGET_FILL_RATE:
        _apply_fill_rate(
            result, item_site_row, order_cost, holding_rate, service_level_source
        )
    _with_demand_signals(result, as_of, lead_time_days, counted, signals)
    return _finish(
        result,
        item_code,
        site_code,
        as_of,
        item_site_row,
        on_hand_units,
        on_order_units,
        order_cost,
        holding_rate,
        service_level_source,
        empirical_adopted,
    )


def _attach_loaded(
    row: dict,
    as_of: date,
    lead_time: dict | None,
    service_level: float,
    item_site_row: dict | None,
    history: list[dict] | None,
    counted: list[tuple[int, date, date, float]],
    signals: list[tuple[int, date, date, float]],
    active_basis: str,
    on_hand_units: int,
    on_order_units: int,
    order_cost: float,
    holding_rate: float,
    service_level_source: dict,
    intermittent_recommendations: bool,
    empirical_adopted: bool | None,
    service_target: str,
) -> dict:
    """One row of ``recommendations_for_all`` from already-loaded inputs.

    ``row`` is the item-site's demand statistics row (the demand
    inputs), ``history`` its 30-day history buckets as
    ``app.forecast.history.history_for_all`` loaded them and ``counted``
    its counted overrides as
    ``app.forecast.overrides.counted_overrides`` loaded them; the
    forecast basis is built from them by
    ``app.forecast.basis.forecast_demand_for`` without a second query,
    and ``signals`` its counted signals as
    ``app.forecast.signals.counted_signals`` loaded them, added to the
    result by ``_with_demand_signals`` before ``_finish``;
    ``active_basis`` is the setting read once for the call, and
    ``intermittent_recommendations`` the value of the
    ``replenishment.intermittent_recommendations`` switch that same
    read gave, and ``empirical_adopted`` the item-site's stored
    empirical adoption (None when the call did not read the
    adoptions).  ``on_hand_units`` and ``on_order_units`` are the
    pair's position values the list already loaded (see
    ``recommendations_for_all``).
    """
    master_lead_time_days, lead_time_days = _lead_time_selection(
        lead_time, item_site_row
    )
    forecast = None
    if lead_time_days is not None:
        forecast = forecast_demand_for(row, history, counted, float(lead_time_days))
    result = recommend(
        row,
        lead_time,
        service_level=service_level,
        master_lead_time_days=master_lead_time_days,
        forecast=forecast,
        active_basis=active_basis,
    )
    result["empirical"] = _empirical(
        row, lead_time, master_lead_time_days, as_of, service_level
    )
    if intermittent_recommendations or empirical_adopted:
        _recommend_empirical(
            result, row, lead_time, master_lead_time_days, service_level
        )
    if service_target == SERVICE_TARGET_FILL_RATE:
        _apply_fill_rate(
            result, item_site_row, order_cost, holding_rate, service_level_source
        )
    _with_demand_signals(result, as_of, lead_time_days, counted, signals)
    return _finish(
        result,
        row["item_code"],
        row["site_code"],
        as_of,
        item_site_row,
        on_hand_units,
        on_order_units,
        order_cost,
        holding_rate,
        service_level_source,
        empirical_adopted,
    )


def recommendations_for_all(as_of: date | None = None) -> list[dict]:
    """Reorder recommendations for every (item_code, site_code) in demand_events.

    Resolves one as_of (``app.stats.demand.default_as_of`` when none is
    given) shared by both statistics, reads the stored system settings
    once for the call (one settings query) — the service level is the
    stored ``replenishment.service_level`` value when one is stored,
    else ``AIERP_SERVICE_LEVEL`` at call time, and the
    ``replenishment.demand_basis`` value the setting gives — reads the
    30-day history of every item-site with one query (
    ``app.forecast.history.history_for_all``) and the counted overrides
    of every item-site with one query (
    ``app.forecast.overrides.counted_overrides``) and the counted
    signals of every item-site with one query (
    ``app.forecast.signals.counted_signals`` — no parameter, flag or
    context value turns the read off), reads the pair's
    ``on_hand`` quantity with one query and the sum of the pair's open
    purchase lines' ``open_units`` with one query (both the tables the
    ERP sync writes, joined by (item_code, site_code) like the
    item_sites join) and calls ``recommend`` for every item-site in the
    demand results with that item's pooled lead time and the forecast
    basis built by ``app.forecast.basis.forecast_demand_for`` from the
    already-loaded inputs, adding ``item_code``, ``site_code``,
    ``as_of``, the ``current`` item_sites settings,
    ``delta_reorder_point``, ``on_hand_units``, ``on_order_units``,
    ``unit_cost`` and ``source_system`` (carried from the
    item_sites row),
    ``suggested_order_quantity`` and ``order_quantity`` (the
    ``app.recommend.order_quantity`` result plus
    ``recommended_max_units``, ``current_order_qty`` and
    ``current_max_qty``, null for non-recommendable item-sites; the
    per-order cost and the holding rate are the stored
    ``replenishment.order_cost`` and ``replenishment.holding_rate``
    values when stored, else the ``AIERP_ORDER_COST`` and
    ``AIERP_HOLDING_RATE`` environment variables, all from the one
    settings read of the call) and ``service_level_source`` to each
    result.  Every item-site's ABC class is computed once for the
    population from the inputs the list already loaded (``app.recommend
    .abc.classify`` over the same annual values ``abc_for_all`` uses),
    with the switch on or off.  With the
    ``replenishment.service_level_by_class`` switch on, the item-site's
    stored or default ABC class level
    (``replenishment.service_level_a``/``b``/``c``) stands in for the
    single service level (``service_level_source`` names the class and
    ``"class"``); with the switch off every item-site keeps the single
    service level and ``service_level_source`` reports the class (or
    null when the item-site is unclassified) with ``"single"``.
    Ordered by ``item_code`` then ``site_code``; the number of queries
    is fixed, whatever the number of item-sites.

    Inside ``recommendations_memo`` the rows of the first call for a
    resolved as-of are memoized for the rest of the context: every
    later call for the same as-of in the same context answers with a
    copy of those rows built by ``copy_tree`` without recomputing (a
    caller changing its rows therefore never changes another
    caller's), and calls for different as-ofs never share a
    computation.  Outside the memo the function computes on every
    call, exactly as before.
    """
    resolved_as_of = _resolve_as_of(as_of)
    memo = _recommendations_memo.get()
    if memo is not None:
        cached = memo.get(resolved_as_of)
        if cached is not None:
            return copy_tree(cached)
    results = _compute_recommendations(resolved_as_of)
    if memo is not None:
        # The memo keeps its own copy: the caller's rows are the
        # freshly computed ones, and mutating them never changes the
        # memo's or another caller's rows.
        memo[resolved_as_of] = copy_tree(results)
    return results


def _compute_recommendations(resolved_as_of: date) -> list[dict]:
    """The one shared computation ``recommendations_for_all`` answers
    with for one resolved as-of date.

    The whole row build ``recommendations_for_all`` used to run on
    every call, everything after the as-of resolution: the one
    settings read, the demand and lead-time statistics, the
    item_sites / on_hand / open-purchase / transfer reads, the ABC
    classes and the forecast basis, and ``_attach_loaded`` for every
    item-site.  Inside a ``recommendations_memo`` context it runs
    once per distinct resolved as-of (the memo answers later
    same-as-of calls with a copy built by ``copy_tree``); outside the
    memo it runs once per call.
    """
    stored_settings = _stored_system_settings()
    service_level = _service_level_for(stored_settings)
    service_target = _service_target_for(stored_settings)
    active_basis = _demand_basis_for(stored_settings)
    lead_time_basis = _lead_time_basis_for(stored_settings)
    order_cost = _order_cost_for(stored_settings)
    holding_rate = _holding_rate_for(stored_settings)
    intermittent_on = _intermittent_recommendations_enabled(stored_settings)
    demand_rows = demand_stats_for_all(resolved_as_of, with_daily=True)
    lead_time_rows = lead_time_stats_for_all(resolved_as_of)
    lead_time_by_item = {
        row["item_code"]: _lead_time_stats_for_basis(row, lead_time_basis)
        for row in lead_time_rows
    }
    item_site_rows = _item_sites_by_key()
    on_hand_by_key = _on_hand_by_key()
    on_order_units_by_key = _on_order_units_by_key(resolved_as_of)
    pairs = [(row["item_code"], row["site_code"]) for row in demand_rows]
    # The stored empirical adoptions of the population, one query for
    # the call (never one per item-site): the read runs when a stored
    # adoption can distinguish one of the population's item-sites
    # from another's (see ``_adoptions_relevant``); every other
    # population keeps the fixed statement budget the existing proofs
    # lock in, treats no item-site as adopted and carries no
    # ``empirical_adopted`` key (the API answers false for it).
    adoptions_relevant = _adoptions_relevant(demand_rows)
    adopted_pairs = _adopted_pairs() if adoptions_relevant else set()
    # The forecast basis of every item-site from what the list already
    # loads: one query for the 30-day history of every pair, one for
    # the counted overrides of every pair and one for the counted
    # signals of every pair (no parameter, flag or context value turns
    # the signals' read off) — the demand statistics row of the pair
    # is the row itself.
    histories = history_for_all(resolved_as_of)
    counted = counted_overrides(pairs)
    signals = counted_signals(pairs)
    # The class of every item-site of the call, computed once for the
    # population from what the list already loaded (no query the list
    # does not do) — with the switch on or off: the switch only decides
    # whether the stored or default level of the item-site's ABC class
    # stands in for the single service level, never whether the class
    # is computed and reported.
    class_by_key = _abc_class_by_key(
        demand_rows,
        lead_time_by_item,
        item_site_rows,
        histories,
        counted,
        active_basis,
    )
    class_levels_on = _class_levels_enabled(stored_settings)
    class_service_levels: dict[tuple[str, str], float] = {}
    class_sources: dict[tuple[str, str], dict] = {}
    for row in demand_rows:
        key = (row["item_code"], row["site_code"])
        abc_class = class_by_key.get(key)
        if class_levels_on and abc_class is not None:
            class_service_levels[key] = _class_service_level(stored_settings, abc_class)
            class_sources[key] = {"abc_class": abc_class, "source": "class"}
        else:
            class_service_levels[key] = service_level
            class_sources[key] = {"abc_class": abc_class, "source": "single"}
    results = [
        _attach_loaded(
            row,
            resolved_as_of,
            lead_time_by_item.get(row["item_code"]),
            class_service_levels.get(
                (row["item_code"], row["site_code"]), service_level
            ),
            item_site_rows.get((row["item_code"], row["site_code"])),
            histories.get((row["item_code"], row["site_code"])),
            counted.get((row["item_code"], row["site_code"]), []),
            signals.get((row["item_code"], row["site_code"]), []),
            active_basis,
            _int_units(on_hand_by_key.get((row["item_code"], row["site_code"]))),
            on_order_units_by_key.get((row["item_code"], row["site_code"]), 0),
            order_cost,
            holding_rate,
            class_sources.get(
                (row["item_code"], row["site_code"]),
                {"abc_class": None, "source": "single"},
            ),
            intermittent_on,
            (
                ((row["item_code"], row["site_code"]) in adopted_pairs)
                if adoptions_relevant
                else None
            ),
            service_target,
        )
        for row in demand_rows
    ]
    results.sort(key=lambda row: (row["item_code"], row["site_code"]))
    return results


def recommendation_for_key(
    item_code: str,
    site_code: str,
    as_of: date | None = None,
    item_site_row: dict | None = None,
) -> dict:
    """One item-site's recommendation (existence is checked by the caller).

    A known item-site with no demand history yields a ``no_demand``
    demand profile, so ``recommend`` reports
    ``not_recommendable_demand`` instead of 404. ``item_site_row`` may be
    supplied by the caller (which already read it); it is looked up
    otherwise. The stored system settings are read once for the call
    (one settings query), the pair's ``on_hand`` row and open purchase
    lines are each read once for the call, the pair's counted signals
    are read with one ``app.forecast.signals.counted_signals`` query
    for the call (no parameter, flag or context value turns the read
    off) and added to the result by ``_with_demand_signals`` before
    ``_finish``, and the result carries ``on_hand_units``,
    ``on_order_units``, ``unit_cost``, ``source_system``,
    ``suggested_order_quantity``
    and ``order_quantity`` like ``recommendations_for_all`` (the
    per-order cost and the holding rate are the stored
    ``replenishment.order_cost`` and ``replenishment.holding_rate``
    values when stored, else the ``AIERP_ORDER_COST`` and
    ``AIERP_HOLDING_RATE`` environment variables).  The item-site's
    ABC class is computed once for the
    population the list call would classify, with the switch on or off;
    with the ``replenishment.service_level_by_class`` switch on the
    item-site's stored or default ABC class level stands in for the
    single service level, and the result carries
    ``service_level_source`` as in ``recommendations_for_all``.
    """
    resolved_as_of = _resolve_as_of(as_of)
    stored_settings = _stored_system_settings()
    service_level = _service_level_for(stored_settings)
    service_target = _service_target_for(stored_settings)
    order_cost = _order_cost_for(stored_settings)
    holding_rate = _holding_rate_for(stored_settings)
    active_basis = _demand_basis_for(stored_settings)
    lead_time_basis = _lead_time_basis_for(stored_settings)
    intermittent_on = _intermittent_recommendations_enabled(stored_settings)
    # The population's classes, computed once for the call with the
    # switch on or off: the single-key call classifies the same
    # population the list call would, from the same loads (no query the
    # list does not do) — the switch only decides whether the stored
    # or default level of the class stands in for the single service
    # level.
    demand_rows = demand_stats_for_all(resolved_as_of, with_daily=True)
    lead_time_by_item = {
        row["item_code"]: _lead_time_stats_for_basis(row, lead_time_basis)
        for row in lead_time_stats_for_all(resolved_as_of)
    }
    pairs = [(row["item_code"], row["site_code"]) for row in demand_rows]
    class_by_key = _abc_class_by_key(
        demand_rows,
        lead_time_by_item,
        _item_sites_by_key(),
        history_for_all(resolved_as_of),
        counted_overrides(pairs),
        active_basis,
    )
    # The pair's stored empirical adoption, one query for the call,
    # under the same rule as ``recommendations_for_all``: the read
    # runs when a stored adoption can distinguish one of the
    # population's item-sites from another's (see
    # ``_adoptions_relevant``), so every other population keeps its
    # fixed statement budget and treats the pair as not adopted (the
    # API answers false for it).
    adoptions_relevant = _adoptions_relevant(demand_rows)
    empirical_adopted = (
        _adoptions_repo.get(item_code, site_code) is not None
        if adoptions_relevant
        else None
    )
    abc_class = class_by_key.get((item_code, site_code))
    if _class_levels_enabled(stored_settings) and abc_class is not None:
        service_level = _class_service_level(stored_settings, abc_class)
        service_level_source = {"abc_class": abc_class, "source": "class"}
    else:
        service_level_source = {"abc_class": abc_class, "source": "single"}
    demand = demand_stats_for_pair(
        item_code, site_code, resolved_as_of, with_daily=True
    )
    if demand is None:
        demand = {
            "mean_daily": 0.0,
            "std_daily": 0.0,
            "demand_class": "no_demand",
            "recommendable": False,
        }
    lead_time = _lead_time_stats_for_basis(
        lead_time_stats_for_item(item_code, resolved_as_of), lead_time_basis
    )
    if item_site_row is None:
        item_site_row = _repo.get_by_key(item_code, site_code)
    return _attach(
        item_code,
        site_code,
        resolved_as_of,
        demand,
        lead_time,
        service_level,
        item_site_row,
        _on_hand_units_for(item_code, site_code),
        _on_order_units_for(item_code, site_code, resolved_as_of),
        order_cost,
        holding_rate,
        active_basis,
        service_level_source,
        intermittent_on,
        empirical_adopted,
        service_target,
    )
