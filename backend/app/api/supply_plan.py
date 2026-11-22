"""The supply plan: what to order, by when, from whom, and in what quantity.

``GET /api/supply-plan?horizon_days=`` answers, for any signed-in user,
which item-sites are due for an order within the horizon (1 to 60 days,
default 7), how much to order once the raw quantity is fitted to the
supplier's ordering terms (``app.supply.terms.fit_to_terms``), and when
the order must be placed so that it arrives by the due date.

One read per call: ``recommendations_for_all`` (the recommendations),
``item_sites`` (each pair's ``source_system``), the
``supplier_terms`` repository, the ``suppliers`` table and the
``supplier_order_minimums`` repository (the stored minimum of each
group's (``source_system``, ``supplier_code``) pair, keyed exactly as
stored — a raised read fails the plan with a 500, never a plan whose
groups carry no minimum).  A group whose pair has a stored minimum
answers ``minimum`` (the stored amount) and ``shortfall`` (the
minimum minus the group's ``total_value`` when the total is below it,
0 when it is not) — both rounded to cents half-up, the way the
lines' values are, and serialized the way the group's
``total_value`` is; every other group, the group without a supplier
included, carries neither key in the response, exactly as a line
leaves out a null ``supplier_reason``, and a minimum whose pair has
no group in the plan changes nothing.  The as-of is always the
resolved as-of date (``app.stats.demand.default_as_of``: the latest
transaction date in the synced data, or today when that is earlier) —
never null, even when no item-site is due.

The ``supply_plan.prefer_lower_risk_supplier`` system setting (default
true) switches the line's supplier when it can lower the risk: with
the setting on, ``build_supply_plan`` reads
``app.recommend.supplier_risk.supplier_risk`` once per call and keeps
each supplier's ``risk`` by ``supplier_code`` — a raised read builds
the plan exactly as with the setting off — and when the default
pick's supplier (the ``is_default`` terms row of the item-site's
``source_system``, else the lowest ``supplier_code``) is rated
``high`` or ``medium`` and another terms row of the same item and
source system names a supplier rated strictly lower (``low`` below
``medium`` below ``high``), the line uses the lowest-rated such row
instead — ties to the row marked default, then the lowest
``supplier_code``; a supplier missing from the rating is never
switched to, and a default pick whose supplier is missing from the
rating, or rated ``low``, is never switched away from.  Everything
follows the chosen row: the line's ``supplier_code``,
``source_system``, terms, lead time, fitted quantity and arrival date
all come from it, as they do from the default pick today.  The
switched line's ``supplier_reason`` reads ``Chosen over <name>
(<Risk> risk: <signs>)`` — the name the default pick's supplier name
from the suppliers table, else its code, the risk ``High`` or
``Medium``, and the signs ``late deliveries`` and/or ``variable lead
times``, joined by ``", "``; while the default pick stands the
``supplier_reason`` is null.  With the setting off every line is
exactly as today, the risk read never happens, and every line's
``supplier_reason`` is null.

The ``supply_plan.plan_buffers_by_signal`` system setting (default
true) plans a designated item-site from its DDMRP buffer's order
signal instead of its reorder point: with the setting on (the stored
value, else the default), ``build_supply_plan`` reads the DDMRP plan
once per call through the module's ``_buffer_rows`` —
``app.api.ddmrp.ddmrp_zone_rows`` with the plan's as-of, then
``app.ddmrp.plan.plan_ddmrp`` with the as-of the zone rows answer and
those rows, answering ``{(item_code, site_code): row}`` for every row
of that plan's ``rows`` — and a raised read builds the plan exactly
as with the setting off, the way a raised risk read does.  A
designated item-site is one in those rows; when the plan's input has
a recommendation for it with status "ok", the item-site is planned by
its buffer, not its reorder point: with the row's ``order_signal``
true it has one line — ``due_in_days`` 0 and ``order_by`` the as-of
date, ``raw_quantity`` the row's ``suggested_order_units`` fitted to
the supplier's terms exactly as every line's raw quantity is,
``position_units`` the row's ``net_flow`` rounded down to whole
units, ``reorder_point_units`` the row's ``top_of_yellow``, and the
arithmetic's label "buffer signal: top of green <top of green> minus
net flow <net flow>, rounded up", the net flow rounded to one decimal
place the way ``_format_number`` writes numbers — while the daily
demand, supplier choice, terms, lead time, arrival, unit cost, value
and grouping are those of any line; with the ``order_signal`` false
the item-site has no line, whatever its reorder point says, and every
other item-site is planned exactly as today.  Every line's
``source`` reads ``"buffer"`` for a line from a buffer's signal and
``"reorder_point"`` for every other line, and the supplier groups'
lines carry it too.  With the setting off every line is exactly as
today, with ``source`` ``"reorder_point"``, and ``_buffer_rows`` is
never called.

``GET /api/supply-plan/top-ups?horizon_days=`` answers, for any
signed-in user behind the same gate, how the supplier groups of the
``horizon_days`` order plan (1 to 60, default 7, exactly as
``GET /api/supply-plan`` takes it; anything else 422) that have a
stored minimum and a shortfall above 0 are topped up: the answer's
``as_of`` is the plan's, plus ``horizon_days``, ``lookahead_days`` —
the effective ``supply_plan.top_up_lookahead_days`` setting, the
stored value rounded down to a whole number of days, else the default
30 — and ``holding_rate``, the effective
``replenishment.holding_rate`` the recommendations use.  One entry per
such supplier group, in the plan's group order, with the group's
``supplier_code``, ``source_system``, ``vendor_code``, ``name``,
``minimum``, ``total_value`` and ``shortfall`` as the plan reports
them, then ``top_ups``, ``top_up_value``, ``reaches_minimum`` and
``still_short``.  The candidates are the lines of the order plan built
for ``lookahead_days`` (``build_supply_plan(lookahead_days)`` with the
same as-of) of the same ``supplier_code`` and ``source_system`` whose
``due_in_days`` is above ``horizon_days``, whose ``value`` is not null
and whose (``item_code``, ``site_code``) is not on the excess list
(``app.recommend.excess.excess_ranked(as_of)`` read once per call);
they are taken by ``due_in_days``, then ``item_code``, then
``site_code``, each added until the sum of the values taken is at
least the shortfall — the candidate that reaches it included, none
after it.  A top-up carries the look-ahead line's ``item_code``,
``site_code``, ``due_in_days``, ``order_by``, ``quantity``,
``unit_cost`` and ``value`` plus ``holding_cost``, the value ×
``holding_rate`` × ``due_in_days`` / 365 rounded to two places half
up; ``top_up_value`` is the top-ups' values summed (0 with none),
``reaches_minimum`` true when ``total_value`` plus ``top_up_value`` is
at least the minimum, and ``still_short`` the minimum minus the two
when that is above 0, else 0 — the money serialized the way the plan
serializes the group's ``total_value``, rounded to two places half up.
The route is read-only.  The builder's body runs inside one
``with`` statement — one
``app.recommend.reorder.recommendations_memo`` block, one
``app.db.read_cache.read_once`` block and one
``app.db.connection.one_connection`` block: the two order
plans' ``recommendations_for_all`` calls and the excess
ranking's own read share the one memo, so the recommendations
are computed once per as-of, each read the cached readers serve
is read once, and the call's cursors ride the one connection;
a caller already inside any of the blocks shares the outer one
(the nested use each already allows), so the answer — for the
route and for the release — is the same as without the blocks.

``POST /api/supply-plan/release`` takes ``{"horizon_days": 7,
"supplier_codes": ["..."], "confirm": false}`` — ``horizon_days``
1 to 60 (default 7), ``supplier_codes`` a non-empty list of up to 50
codes of 1 to 64 characters without NUL bytes, any extra field 422 —
and answers with the existing ``ReleasePlan`` model: the plan of
``app.supply.release.order_plan_release(horizon_days,
supplier_codes)`` — the builder's lines of ``horizon_days``, kept for
the named suppliers, one group per (site, supplier) of the
``acumatica`` lines with the origin tag "AIERP order plan <as_of>
site <site_code> vendor <supplier_code>" (the as-of is the builder's
resolved as-of, so the tag — and the plan's ``month``, the as-of's
``YYYY-MM`` — is stable across the preview and the confirm, never
regenerated from the server date at confirm time), the named
suppliers' lines from another source system as ``unreleasable`` —
run through the shared ``release_groups`` body of
``app.api.aggregate``: any signed-in user may preview, the confirm is
admin only (403), the release holds the advisory lock (a second
confirm 409), incomplete Acumatica configuration 503, switched-off
writes 409, one ``plan_releases`` row per group with its outcome,
and an aborted batch 502 with the rows ``aborted`` — the releases
appear in ``GET /api/aggregate/release/audit`` and the exceptions
inbox unchanged, being ordinary ``plan_releases`` rows.  A supplier
code no line of the plan carries is simply absent: 200 with the
groups the named suppliers hold, never a 422.

``GET /api/supply-plan/transfers`` answers, for any signed-in user
behind the same gate, which item-site of an item should move stock
to which other item-site of the same source system: per item-site
(the recommendation with status "ok" and a numeric
``order_quantity.order_quantity_units``) the position — on hand
plus on order — against the reorder point and the order quantity,
a need when the reorder point stands above the position, a surplus
when the position stands above the reorder point plus one order
quantity, and per item, across the item-sites of the same
``source_system``, the largest need served first from the largest
surplus first — each match the smaller of the two remainders in
whole units, a site never giving to itself.  Each suggestion is
``item_code``, ``source_system``, ``from_site``, ``to_site``,
``quantity``, ``unit_cost`` (the receiving item-site's), ``value``
(the quantity times the unit cost to cents, null without a cost),
``to_site_need``, ``from_site_surplus`` and ``arithmetic`` — one
line of text naming both numbers — and the suggestions are ordered
by ``value`` descending (the nulls last), then ``item_code``.  One
read per call: ``recommendations_for_all`` and the item_sites read
(each pair's ``source_system``); the as-of is the resolved as-of
date, never null.

``GET /api/supply-plan/summary`` answers, for any signed-in user
behind the same gate, the one-page summary of the 7-day supply
plan: ``{"as_of", "horizon_days": 7, "line_count", "total_value",
"supplier_count", "transfer_count", "transfer_value",
"transfers_in_flight", "transfers_awaiting_release"}``.
``line_count``, ``total_value`` and ``supplier_count`` are the
7-day order plan's ``line_count``, ``total_value`` and the number
of its supplier groups with a non-null ``supplier_code``, and
``transfer_count`` and ``transfer_value`` are the transfer
suggestions' ``suggestion_count`` and ``total_value`` — both from
the same builders ``GET /api/supply-plan`` and
``GET /api/supply-plan/transfers`` use, the as-of the plan's
resolved as-of, never null.  ``transfers_in_flight`` is the number
of the rows ``TransferReleasePostgresRepository.list_in_flight``
returns whose ``erp_status`` is null or none of "Released",
"Completed" and "Canceled", and ``transfers_awaiting_release`` how
many of those are at least the
``exceptions.release_approval_days`` system setting (default 3)
old by ``updated_at`` (a missing or out-of-range stored value
falls back to the registry default, the exact cutoff inclusive, as
the exceptions inbox' transfer window is).  The answer is built
inside one ``app.recommend.reorder.recommendations_memo``: the
plan and the suggestions read their ``recommendations_for_all``
through the same memo, so the recommendations are computed once
per request.

``POST /api/supply-plan/transfers/release`` takes
``{"transfers": [{"item_code", "from_site", "to_site"}], "confirm":
false}`` — ``transfers`` a non-empty list of up to 50 entries, each
string 1 to 64 characters without NUL bytes, one entry per distinct
suggestion (a repeated (item_code, from_site, to_site) is 422), any
extra field 422 — and answers the transfer release plan of
``app.supply.transfer_release.transfer_release_plan``: the current
transfer suggestions, read once through the same builder
``GET /api/supply-plan/transfers`` uses, each requested entry matched
by (item_code, from_site, to_site) with the suggestion's quantity
(the request never carries one), one group per (from_site, to_site)
with the origin tag "AIERP transfer <as_of> site <from_site> to
<to_site>", the unmatched entries ``unreleasable`` as "no longer
suggested" and the non-Acumatica suggestions "only Acumatica
transfers are released here".  The preview (no ``confirm``, any
signed-in user) calls the client's transfer preview once with
every group and answers the plan with each group's outcome, reference
number and warehouse codes on top — all null with an incomplete
Acumatica configuration, no client call — writing nothing.  The
confirm is admin only (403 otherwise, before any ERP call or row)
and holds the transfer release's advisory lock (a second confirm
waits, 409 when it cannot take it), then resolves the as-of
snapshot — so the audit row records the same snapshot the release
used — and, before any row, answers 503 on incomplete Acumatica
configuration and 409 on switched-off writes.  Otherwise one
``pending`` ``transfer_releases`` row per group is written,
the client's transfer create is called once with every group —
the orders are created ON HOLD, moving the stock still requires
releasing the hold under Acumatica's own process — and each row is
updated to the group's outcome (``created`` or ``exists``) with its
reference number, warehouse codes and error; an ``exists`` outcome
carries the existing order's reference number only when the current
plan's lines match every stored snapshot of the same origin, a
changed plan keeping the outcome but not the reference number with
the stale-lines error instead, and an auth or transport failure
that aborts the batch is 502 with the rows ``aborted``.  The
releases appear in ``GET /api/supply-plan/transfers/release/audit``
(admin only, ``limit`` 1 to 200 default 50, newest first).

"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    constr,
    field_validator,
    model_serializer,
    model_validator,
)

from app.api.aggregate import (
    ReleasePlan,
    _acumatica_config_complete,
    release_groups,
    require_any_authenticated_user,
)
from app.api.ddmrp import _stored_system_settings, ddmrp_zone_rows
from app.api.erp_mappings import require_admin
from app.auth.models import AuthUser
from app.db.connection import get_cursor, one_connection
from app.db.read_cache import read_once
from app.ddmrp.plan import plan_ddmrp
from app.ddmrp.zones import setting_value
from app.feed.acumatica_client import (
    AcumaticaAuthError,
    AcumaticaConfigError,
    AcumaticaError,
    AcumaticaWritesDisabledError,
)
from app.recommend.excess import excess_ranked
from app.recommend.reorder import (
    _holding_rate_for,
    recommendations_for_all,
    recommendations_memo,
)
from app.recommend.supplier_risk import supplier_risk
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.supplier_order_minimums_postgres_repository import (
    SupplierOrderMinimumPostgresRepository,
)
from app.repositories.supplier_terms_postgres_repository import (
    SupplierTermsPostgresRepository,
)
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)
from app.repositories.transfer_releases_postgres_repository import (
    TransferReleasePostgresRepository,
    acquire_transfer_release_lock,
    release_transfer_release_lock,
)
from app.settings.registry import get_setting
from app.stats.demand import default_as_of
from app.supply.release import order_plan_release
from app.supply.terms import fit_to_terms
from app.supply.transfer_release import (
    create_transfer_release,
    preview_transfer_release,
    transfer_release_plan,
    transfer_write_probe,
)
from app.supply.transfers import suggest_transfers
from app.units import ceil_units

router = APIRouter(prefix="/api/supply-plan", tags=["supply-plan"])

_item_site_repo = ItemSitePostgresRepository()
_supplier_order_minimum_repo = SupplierOrderMinimumPostgresRepository()
_supplier_terms_repo = SupplierTermsPostgresRepository()
_supplier_repo = SupplierPostgresRepository()
_transfer_release_repo = TransferReleasePostgresRepository()

# The whole table in one read, the way the recommendation layer reads it.
_ALL_ROWS = 10**9

# The system setting that switches a plan line to the lower-risk
# supplier of the item's terms rows (default true).
_PREFER_LOWER_RISK_SETTING = "supply_plan.prefer_lower_risk_supplier"

# The system setting that plans a designated item-site from its
# DDMRP buffer's order signal instead of its reorder point
# (default true).
_PLAN_BUFFERS_BY_SIGNAL_SETTING = "supply_plan.plan_buffers_by_signal"

# The system setting the top-ups route reads the order plan ahead by,
# in whole days (default 30).
_TOP_UP_LOOKAHEAD_SETTING = "supply_plan.top_up_lookahead_days"

# The risk level's rank for the lower-risk switch: low the safest,
# then medium, then high the riskiest — a line switches only to a
# supplier of a strictly lower rank.
_RISK_RANK = {"low": 0, "medium": 1, "high": 2}


class SupplyPlanTerms(BaseModel):
    """A plan line's supplier ordering terms (null when the line has no
    supplier): ``min_order_qty``, ``lot_size`` and ``max_order_qty``,
    each a number or null."""

    min_order_qty: float | None = None
    lot_size: float | None = None
    max_order_qty: float | None = None


class SupplyPlanLine(BaseModel):
    """One item-site's line of the supply plan.

    ``position_units`` is on hand plus on order, ``reorder_point_units``
    the recommendation's reorder point in whole units and
    ``daily_demand`` the recommendation's ``inputs.d``.  ``due_in_days``
    is 0 when the position is at or below the reorder point, else the
    days of demand left above it; ``order_by`` is the as-of plus the due
    days and ``arrives_on`` the ``order_by`` plus the lead time in whole
    days (the supplier terms' ``lead_time_days`` when set, else the
    recommendation's ``inputs.lt``).  ``raw_quantity`` is the larger of
    the recommended order quantity and the suggested order quantity;
    ``quantity``, ``adjustments`` and ``short_of_need`` are the raw
    quantity fitted to the line's ``terms`` with
    ``app.supply.terms.fit_to_terms``.  ``value`` is the quantity times
    the item-site's unit cost, rounded to cents (null without a cost),
    and ``arithmetic`` is the one line of text showing the fitting steps.
    ``supplier_reason`` is null while the line's default supplier pick
    stands, and reads ``Chosen over <name> (<Risk> risk: <signs>)``
    when the ``supply_plan.prefer_lower_risk_supplier`` setting
    switched the line to the lower-risk terms row — the name the
    default pick's supplier name from the suppliers table, else its
    code, the risk ``High`` or ``Medium``, and the signs ``late
    deliveries`` and/or ``variable lead times``.  ``source`` reads
    ``"buffer"`` for a line from a DDMRP buffer's order signal (the
    ``supply_plan.plan_buffers_by_signal`` setting) and
    ``"reorder_point"`` for every other line; every line carries it.
    """

    item_code: str
    site_code: str
    position_units: int
    reorder_point_units: int
    daily_demand: float
    due_in_days: int
    order_by: date
    lead_time_days: float | None = None
    arrives_on: date | None = None
    raw_quantity: int
    quantity: int
    adjustments: list[str]
    short_of_need: bool
    terms: SupplyPlanTerms | None = None
    unit_cost: float | None = None
    value: Decimal | None = None
    arithmetic: str
    supplier_reason: str | None = None
    source: str = "reorder_point"

    @model_serializer(mode="wrap")
    def _serialize_line(self, handler):
        """The line's fields, without ``supplier_reason`` while the
        default pick stands (the field stays out of the response,
        exactly as before the lower-risk supplier switch existed)."""
        data = handler(self)
        if data.get("supplier_reason") is None:
            data.pop("supplier_reason", None)
        return data


class SupplyPlanSupplierGroup(BaseModel):
    """The plan lines of one supplier, ordered by ``due_in_days`` then
    ``item_code`` then ``site_code``.

    ``vendor_code`` and ``name`` come from the suppliers table (null
    when the supplier is not stored).  ``total_value`` is the group's
    lines' values summed (a line without a unit cost contributes zero)
    and is 0 when every line's value is null.  ``minimum`` is the
    stored order minimum of the group's (``source_system``,
    ``supplier_code``) pair — the pair's row of
    ``supplier_order_minimums``, listed once per plan build and
    compared exactly as stored — and ``shortfall`` the minimum minus
    the group's ``total_value`` when the total is below the minimum
    and 0 when it is not; both rounded to cents half-up, the way the
    lines' values are, and serialized the way ``total_value`` is.
    Both are null for every group without a stored minimum — the
    group without a supplier included — and are left out of the
    response, exactly as a line leaves out a null
    ``supplier_reason``.  The lines with no supplier form a single
    final group whose ``supplier_code`` and ``source_system`` are
    null, spanning every source system.
    """

    supplier_code: str | None = None
    source_system: str | None = None
    vendor_code: str | None = None
    name: str | None = None
    line_count: int
    total_value: Decimal
    minimum: Decimal | None = None
    shortfall: Decimal | None = None
    lines: list[SupplyPlanLine]

    @model_serializer(mode="wrap")
    def _serialize_group(self, handler):
        """The group's fields, without ``minimum`` and ``shortfall``
        while the group has no stored minimum (the fields stay out of
        the response, exactly as a line leaves out a null
        ``supplier_reason``)."""
        data = handler(self)
        if data.get("minimum") is None:
            data.pop("minimum", None)
            data.pop("shortfall", None)
        return data


class SupplyPlanResponse(BaseModel):
    """The supply plan as of the resolved as-of date (never null), the
    lines due within ``horizon_days``, grouped by supplier — the
    groups in the order of their earliest ``due_in_days``, then
    ``total_value`` descending, the no-supplier group last.

    ``line_count`` is the plan's lines overall and ``total_value`` their
    values summed (0 when every line's value is null).
    """

    as_of: date
    horizon_days: int
    line_count: int
    total_value: Decimal
    suppliers: list[SupplyPlanSupplierGroup]


class SupplyPlanTopUp(BaseModel):
    """One line of the look-ahead plan taken to top up a supplier group
    that is below its stored minimum: the look-ahead line's
    ``item_code``, ``site_code``, ``due_in_days``, ``order_by``,
    ``quantity``, ``unit_cost`` and ``value``, plus ``holding_cost`` —
    the value held until the due date at the effective
    ``replenishment.holding_rate`` the recommendations use:
    ``value`` × ``holding_rate`` × ``due_in_days`` / 365, rounded to
    two places half up."""

    item_code: str
    site_code: str
    due_in_days: int
    order_by: date
    quantity: int
    unit_cost: float | None = None
    value: Decimal
    holding_cost: Decimal


class SupplyPlanTopUpSupplier(BaseModel):
    """One supplier group of the order plan that has a stored minimum
    and a shortfall above 0, in the plan's group order:
    ``supplier_code``, ``source_system``, ``vendor_code``, ``name``,
    ``minimum``, ``total_value`` and ``shortfall`` as the plan's group
    has them, then the top-up — the look-ahead plan's lines of the same
    supplier and source system due after the horizon with a value and
    not on the excess list, taken by ``due_in_days``, then
    ``item_code``, then ``site_code``, each added until the sum of the
    values taken is at least the shortfall, the line that reaches it
    included and none after it.  ``top_up_value`` is the top-ups'
    values summed (0 with none), ``reaches_minimum`` is true when
    ``total_value`` plus ``top_up_value`` is at least the ``minimum``
    and ``still_short`` the minimum minus the two when that is above
    0, else 0 — the money serialized the way the plan serializes the
    group's ``total_value``, rounded to two places half up."""

    supplier_code: str | None = None
    source_system: str | None = None
    vendor_code: str | None = None
    name: str | None = None
    minimum: Decimal
    total_value: Decimal
    shortfall: Decimal
    top_ups: list[SupplyPlanTopUp]
    top_up_value: Decimal
    reaches_minimum: bool
    still_short: Decimal


class SupplyPlanTopUpsResponse(BaseModel):
    """The top-ups of the order plan as of the plan's resolved as-of
    date (never null), for the ``horizon_days`` horizon (1 to 60,
    default 7), for any signed-in user.

    ``lookahead_days`` is the effective
    ``supply_plan.top_up_lookahead_days`` setting — the stored value
    rounded down to a whole number of days, else the default — and
    ``holding_rate`` the effective ``replenishment.holding_rate`` the
    recommendations use.  ``suppliers`` is one entry per supplier group
    of the ``horizon_days`` order plan that has a stored minimum and a
    shortfall above 0, in the plan's group order.  Read-only."""

    as_of: date
    horizon_days: int
    lookahead_days: int
    holding_rate: float
    suppliers: list[SupplyPlanTopUpSupplier]


class TransferSuggestion(BaseModel):
    """One suggested inter-site transfer.

    ``from_site`` gives ``quantity`` to ``to_site`` of the same item
    and ``source_system``: ``quantity`` the smaller of the receiver's
    remaining need and the donor's remaining surplus, in whole units,
    ``unit_cost`` the receiving item-site's, ``value`` the quantity
    times the unit cost to cents (null without a cost),
    ``to_site_need`` the receiver's full need and
    ``from_site_surplus`` the donor's full surplus.  ``arithmetic`` is
    the one line of text naming both numbers, e.g. "site 159 needs 120
    (ROP 400 \u2212 position 280); site 154 holds 300 beyond ROP +
    order quantity; move 120".
    """

    item_code: str
    source_system: str
    from_site: str
    to_site: str
    quantity: int
    unit_cost: float | None = None
    value: Decimal | None = None
    to_site_need: int
    from_site_surplus: int
    arithmetic: str


class TransferSuggestionsResponse(BaseModel):
    """The transfer suggestions as of the resolved as-of date (never
    null), ordered by ``value`` descending (the suggestions without a
    value last), then ``item_code``.

    ``suggestion_count`` is the suggestions' count and ``total_value``
    their values summed (0 when every suggestion's value is null).
    """

    as_of: date
    suggestion_count: int
    total_value: Decimal
    suggestions: list[TransferSuggestion]


class SupplyPlanSummaryResponse(BaseModel):
    """The one-page summary of the 7-day supply plan (the plan's
    resolved as-of, never null).

    ``horizon_days`` is the plan's horizon (7); ``line_count`` and
    ``total_value`` are the plan's own, the way
    ``SupplyPlanResponse`` answers them for the same builder's
    call, and ``supplier_count`` is the plan's supplier groups with
    a non-null ``supplier_code`` (the no-supplier group never
    counts).  ``transfer_count`` and ``transfer_value`` are the
    transfer suggestions' ``suggestion_count`` and ``total_value``,
    the way ``TransferSuggestionsResponse`` answers them for the
    same builder's call.  ``transfers_in_flight`` is the number of
    the rows ``TransferReleasePostgresRepository.list_in_flight``
    returns whose ``erp_status`` is null or none of "Released",
    "Completed" and "Canceled" — the rows still awaiting the ERP's
    release — and ``transfers_awaiting_release`` how many of those
    are at least the ``exceptions.release_approval_days`` system
    setting (default 3) old by ``updated_at``, the exact cutoff
    inclusive, a missing or out-of-range stored value falling back
    to the registry default.
    """

    as_of: date
    horizon_days: int
    line_count: int
    total_value: Decimal
    supplier_count: int
    transfer_count: int
    transfer_value: Decimal
    transfers_in_flight: int
    transfers_awaiting_release: int


_BASIS_LABELS = {
    "eoq": "EOQ",
    "assumed_30_days": "assumed 30 days",
}


def _number(value) -> float | None:
    """A finite real number as a float, else None.

    None, bools, non-numeric values and non-finite numbers are not
    numbers — stored data the route cannot hold is skipped, never a 500.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
    else:
        return None
    return number if math.isfinite(number) else None


def _format_number(value: float | None) -> str:
    """A term value for the arithmetic line: whole numbers without a
    decimal point (25.0 reads "25", 2.5 reads "2.5")."""
    number = _number(value)
    if number is None:
        return "0"
    if number.is_integer():
        return str(int(number))
    return str(number)


def _arithmetic(
    raw: int,
    label: str,
    terms: dict | None,
    adjustments: list[str],
    quantity: int,
) -> str:
    """The line's one line of arithmetic: the raw quantity with the label
    naming the input that set it — the order quantity's basis label, or
    "shortfall below reorder point" when the suggested quantity exceeds
    the order quantity's units — then the fitted steps: raised to the
    minimum, rounded to the lot (named whenever a lot is set, the
    rounding step was run even when it kept the quantity), capped at the
    maximum — and the fitted quantity, e.g. "raw 246 (EOQ) → raised to
    minimum 300 → rounded to lot 25: 300"."""
    parts = [f"raw {raw} ({label})"]
    if terms is not None:
        if "raised_to_minimum" in adjustments:
            parts.append(
                f"raised to minimum {_format_number(terms.get('min_order_qty'))}"
            )
        if terms.get("lot_size") is not None and _number(terms.get("lot_size")) > 0:
            parts.append(f"rounded to lot {_format_number(terms.get('lot_size'))}")
        if "capped_at_maximum" in adjustments:
            parts.append(
                f"capped at maximum {_format_number(terms.get('max_order_qty'))}"
            )
    return " → ".join(parts) + f": {quantity}"


def _pick_terms_row(rows: list[dict], source_system: str | None) -> dict | None:
    """The item's supplier terms row for the source system: the row whose
    ``is_default`` is true, else the row with the lowest
    ``supplier_code``; None when the item has no terms row for the
    source system."""
    candidates = [row for row in rows if row.get("source_system") == source_system]
    if not candidates:
        return None
    for row in candidates:
        if row.get("is_default"):
            return row
    return min(candidates, key=lambda row: str(row.get("supplier_code") or ""))


def _prefer_lower_risk_supplier_enabled(stored: dict) -> bool:
    """The ``supply_plan.prefer_lower_risk_supplier`` value of one
    call's stored settings: the stored value when it is a boolean,
    else the registry default (true).  A value written out of band
    that the setting cannot hold (not a boolean) never applies — the
    default does."""
    setting = get_setting(_PREFER_LOWER_RISK_SETTING)
    if setting is None:
        return True
    value = stored.get(setting.key)
    if isinstance(value, bool):
        return value
    return bool(setting.default)


def _plan_buffers_by_signal_enabled(stored: dict) -> bool:
    """The ``supply_plan.plan_buffers_by_signal`` value of one call's
    stored settings: the stored value when it is a boolean, else the
    registry default (true).  A value written out of band that the
    setting cannot hold (not a boolean) never applies — the default
    does."""
    setting = get_setting(_PLAN_BUFFERS_BY_SIGNAL_SETTING)
    if setting is None:
        return True
    value = stored.get(setting.key)
    if isinstance(value, bool):
        return value
    return bool(setting.default)


def _top_up_lookahead_days_for(stored: dict) -> int:
    """The top-up look-ahead for one call's stored settings: the stored
    ``supply_plan.top_up_lookahead_days`` value, rounded down to a whole
    number of days, when one is stored and lies within the setting's
    bounds, else the registry default.

    The same rule the recommendations apply to a stored number setting:
    a value written out of band that the setting cannot hold (not a
    number, or outside ``[min, max]``) never applies — the default
    does."""
    setting = get_setting(_TOP_UP_LOOKAHEAD_SETTING)
    if setting is None:
        return 30
    value = stored.get(setting.key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return int(setting.default)
    if not (setting.min <= value <= setting.max):
        return int(setting.default)
    return int(value)


def _top_up_holding_cost(
    value: Decimal, holding_rate: float, due_in_days: int
) -> Decimal:
    """One top-up's ``holding_cost``: the value held until the due date
    at the effective holding rate — ``value`` × ``holding_rate`` ×
    ``due_in_days`` / 365, rounded to two places half up."""
    return (
        Decimal(str(value))
        * Decimal(str(holding_rate))
        * Decimal(due_in_days)
        / Decimal(365)
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _risk_by_supplier_code() -> dict | None:
    """The per-supplier risk rating keyed by ``supplier_code`` —
    ``supplier_risk`` read once per call — or None when the rating
    cannot be read: a raised read, and an answer without the expected
    shape, build the plan exactly as with the setting off, never a
    failure.  Every kept entry carries the supplier's ``risk`` (and
    the ``late`` / ``variable`` signs the reason names)."""
    try:
        answer = supplier_risk()
    except Exception:
        return None
    if not isinstance(answer, dict):
        return None
    suppliers = answer.get("suppliers")
    if not isinstance(suppliers, list):
        return None
    risk_by_code: dict = {}
    for entry in suppliers:
        if not isinstance(entry, dict):
            continue
        code = entry.get("supplier_code")
        if not isinstance(code, str) or not code:
            continue
        if entry.get("risk") not in _RISK_RANK:
            continue
        risk_by_code[code] = entry
    return risk_by_code


def _buffer_rows(as_of: date) -> dict | None:
    """The DDMRP plan of one plan build, keyed by (``item_code``,
    ``site_code``) — the buffer read the
    ``supply_plan.plan_buffers_by_signal`` setting turns on, read once
    per call: ``ddmrp_zone_rows`` with the plan's as-of, then
    ``plan_ddmrp`` with the as-of the zone rows answer and those rows,
    answering for every row of that plan's ``rows``.  A raised read
    answers None — the plan is then built exactly as with the setting
    off, the way a raised risk read does, never a failure."""
    try:
        answered_as_of, zone_rows = ddmrp_zone_rows(as_of)
        plan = plan_ddmrp(answered_as_of, zone_rows)
    except Exception:
        return None
    return {(row["item_code"], row["site_code"]): row for row in plan["rows"]}


def _lower_risk_terms_row(
    rows: list[dict],
    source_system: str | None,
    default_row: dict,
    risk_by_code: dict,
) -> tuple[dict, dict | None]:
    """The terms row the line buys from under the lower-risk switch:
    the default pick, or — when the default pick's supplier is rated
    ``high`` or ``medium`` — the lowest-rated other terms row of the
    same item and source system, ties to the row marked default, then
    the lowest ``supplier_code``.  Returns the chosen row and the
    default pick's rating entry — None while the default pick stands:
    a supplier missing from the rating is never switched to, and a
    default pick whose supplier is missing from the rating, or rated
    ``low``, is never switched away from."""
    default_code = default_row.get("supplier_code")
    default_entry = (
        risk_by_code.get(default_code) if isinstance(default_code, str) else None
    )
    default_rank = (
        _RISK_RANK.get(default_entry.get("risk"))
        if isinstance(default_entry, dict)
        else None
    )
    if default_rank is None or default_rank == 0:
        return default_row, None
    best_row = None
    best_key = None
    for row in rows:
        if row is default_row or row.get("source_system") != source_system:
            continue
        entry = risk_by_code.get(row.get("supplier_code"))
        if not isinstance(entry, dict):
            continue
        rank = _RISK_RANK.get(entry.get("risk"))
        if rank is None or rank >= default_rank:
            continue
        key = (
            rank,
            0 if row.get("is_default") is True else 1,
            str(row.get("supplier_code") or ""),
        )
        if best_key is None or key < best_key:
            best_row = row
            best_key = key
    if best_row is None:
        return default_row, None
    return best_row, default_entry


def _lower_risk_reason(
    default_row: dict,
    default_entry: dict,
    suppliers_by_code: dict,
) -> str:
    """The switched line's ``supplier_reason``: ``Chosen over <name>
    (<Risk> risk: <signs>)`` — the name the default pick's supplier
    name from the suppliers table, else its code, the risk ``High`` or
    ``Medium``, and the signs ``late deliveries`` and/or ``variable
    lead times``, joined by ``", "``."""
    supplier_code = default_row.get("supplier_code")
    name = None
    if isinstance(supplier_code, str):
        supplier = suppliers_by_code.get(supplier_code)
        if isinstance(supplier, dict):
            name = supplier.get("name")
    if not name:
        name = supplier_code if isinstance(supplier_code, str) else "unknown"
    risk_label = str(default_entry.get("risk")).capitalize()
    signs = [
        sign
        for flag, sign in (
            (default_entry.get("late"), "late deliveries"),
            (default_entry.get("variable"), "variable lead times"),
        )
        if flag
    ]
    if not signs:
        signs = ["late deliveries"]
    return f"Chosen over {name} ({risk_label} risk: {', '.join(signs)})"


def _buffer_plan_line(
    recommendation: dict,
    as_of: date,
    buffer_row: dict,
    source_system_by_pair: dict[tuple[str, str], str | None],
    terms_by_item: dict[str, list[dict]],
    risk_by_code: dict | None,
    suppliers_by_code: dict,
) -> dict | None:
    """One plan line from one DDMRP buffer's order signal, or None
    when the signal is off: the designated item-site is planned by its
    buffer, not its reorder point — ``due_in_days`` 0 and ``order_by``
    the as-of date, ``raw_quantity`` the row's
    ``suggested_order_units`` fitted to the supplier's terms exactly
    as every line's raw quantity is, ``position_units`` the row's
    ``net_flow`` rounded down to whole units,
    ``reorder_point_units`` the row's ``top_of_yellow``, and the
    arithmetic's label "buffer signal: top of green <top of green>
    minus net flow <net flow>, rounded up" — the net flow rounded to
    one decimal place, the way ``_format_number`` writes numbers —
    while the daily demand, supplier choice, terms, lead time,
    arrival, unit cost, value and grouping are those of any line."""
    if not buffer_row.get("order_signal"):
        return None
    item_code = buffer_row.get("item_code")
    site_code = buffer_row.get("site_code")
    if not isinstance(item_code, str) or not isinstance(site_code, str):
        return None

    inputs = recommendation.get("inputs") or {}
    daily = _number(inputs.get("d"))

    source_system = source_system_by_pair.get((item_code, site_code))
    terms_row = _pick_terms_row(terms_by_item.get(item_code, []), source_system)
    supplier_reason = None
    if terms_row is not None and risk_by_code is not None:
        default_row = terms_row
        terms_row, default_entry = _lower_risk_terms_row(
            terms_by_item.get(item_code, []),
            source_system,
            default_row,
            risk_by_code,
        )
        if default_entry is not None:
            supplier_reason = _lower_risk_reason(
                default_row, default_entry, suppliers_by_code
            )
    terms = None
    supplier_code = None
    line_source_system = None
    lead_time = None
    if terms_row is not None:
        supplier_code = terms_row.get("supplier_code")
        line_source_system = terms_row.get("source_system")
        terms = {
            "min_order_qty": _number(terms_row.get("min_order_qty")),
            "lot_size": _number(terms_row.get("lot_size")),
            "max_order_qty": _number(terms_row.get("max_order_qty")),
        }
        lead_time = _number(terms_row.get("lead_time_days"))
    if lead_time is None:
        lead_time = _number(inputs.get("lt"))

    net_flow = _number(buffer_row.get("net_flow"))
    top_of_yellow = _number(buffer_row.get("top_of_yellow"))
    top_of_green = _number(buffer_row.get("top_of_green"))
    suggested = _number(buffer_row.get("suggested_order_units"))
    raw = math.ceil(suggested) if suggested is not None else 0
    label = (
        "buffer signal: top of green "
        f"{_format_number(top_of_green)} minus net flow "
        f"{_format_number(round(net_flow, 1) if net_flow is not None else None)}, "
        "rounded up"
    )

    order_by = as_of
    arrives_on = (
        order_by + timedelta(days=math.ceil(lead_time))
        if lead_time is not None
        else None
    )

    fitted = fit_to_terms(raw, terms)
    quantity = fitted["quantity"]

    unit_cost = _number(recommendation.get("unit_cost"))
    value = None
    if unit_cost is not None:
        value = (Decimal(str(unit_cost)) * quantity).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    return {
        "item_code": item_code,
        "site_code": site_code,
        "position_units": int(math.floor(net_flow)) if net_flow is not None else 0,
        "reorder_point_units": int(top_of_yellow) if top_of_yellow is not None else 0,
        "daily_demand": daily if daily is not None else 0.0,
        "due_in_days": 0,
        "order_by": order_by,
        "lead_time_days": lead_time,
        "arrives_on": arrives_on,
        "raw_quantity": raw,
        "quantity": quantity,
        "adjustments": fitted["adjustments"],
        "short_of_need": fitted["short_of_need"],
        "terms": terms,
        "unit_cost": unit_cost,
        "value": value,
        "arithmetic": _arithmetic(raw, label, terms, fitted["adjustments"], quantity),
        "supplier_code": supplier_code,
        "source_system": line_source_system,
        "supplier_reason": supplier_reason,
        "source": "buffer",
    }


def _plan_line(
    recommendation: dict,
    as_of: date,
    horizon_days: int,
    source_system_by_pair: dict[tuple[str, str], str | None],
    terms_by_item: dict[str, list[dict]],
    risk_by_code: dict | None,
    suppliers_by_code: dict,
    buffer_rows: dict | None = None,
) -> dict | None:
    """One plan line from one recommendation, or None when the
    item-site is not in the plan: not status "ok" without a numeric
    recommended order quantity, not due (no positive daily demand above
    the reorder point), or due beyond the horizon.

    With ``buffer_rows`` — the DDMRP plan's rows keyed by (item_code,
    site_code), the ``supply_plan.plan_buffers_by_signal`` setting on
    — a designated item-site whose recommendation has status "ok" is
    instead planned by its buffer: ``_buffer_plan_line`` answers its
    one line, or none when the buffer's ``order_signal`` is off,
    whatever the item-site's reorder point says."""
    if recommendation.get("status") != "ok":
        return None
    item_code = recommendation.get("item_code")
    site_code = recommendation.get("site_code")
    if not isinstance(item_code, str) or not isinstance(site_code, str):
        return None
    if buffer_rows is not None:
        buffer_row = buffer_rows.get((item_code, site_code))
        if buffer_row is not None:
            return _buffer_plan_line(
                recommendation,
                as_of,
                buffer_row,
                source_system_by_pair,
                terms_by_item,
                risk_by_code,
                suppliers_by_code,
            )
    order_quantity = recommendation.get("order_quantity")
    raw_units = (
        _number(order_quantity.get("order_quantity_units")) if order_quantity else None
    )
    if raw_units is None:
        return None

    on_hand = _number(recommendation.get("on_hand_units"))
    on_order = _number(recommendation.get("on_order_units"))
    reorder_point = _number(recommendation.get("reorder_point_units"))
    if on_hand is None or on_order is None or reorder_point is None:
        return None
    position = on_hand + on_order
    inputs = recommendation.get("inputs") or {}
    daily = _number(inputs.get("d"))
    if position <= reorder_point:
        due = 0
    elif daily is not None and daily > 0:
        due = ceil_units((position - reorder_point) / daily)
    else:
        return None
    if due > horizon_days:
        return None

    suggested = _number(recommendation.get("suggested_order_quantity"))
    raw = max(
        math.ceil(raw_units),
        math.ceil(suggested) if suggested is not None and suggested > 0 else 0,
    )

    source_system = source_system_by_pair.get((item_code, site_code))
    terms_row = _pick_terms_row(terms_by_item.get(item_code, []), source_system)
    supplier_reason = None
    if terms_row is not None and risk_by_code is not None:
        default_row = terms_row
        terms_row, default_entry = _lower_risk_terms_row(
            terms_by_item.get(item_code, []),
            source_system,
            default_row,
            risk_by_code,
        )
        if default_entry is not None:
            supplier_reason = _lower_risk_reason(
                default_row, default_entry, suppliers_by_code
            )
    terms = None
    supplier_code = None
    line_source_system = None
    lead_time = None
    if terms_row is not None:
        supplier_code = terms_row.get("supplier_code")
        line_source_system = terms_row.get("source_system")
        terms = {
            "min_order_qty": _number(terms_row.get("min_order_qty")),
            "lot_size": _number(terms_row.get("lot_size")),
            "max_order_qty": _number(terms_row.get("max_order_qty")),
        }
        lead_time = _number(terms_row.get("lead_time_days"))
    if lead_time is None:
        lead_time = _number(inputs.get("lt"))

    order_by = as_of + timedelta(days=due)
    arrives_on = (
        order_by + timedelta(days=math.ceil(lead_time))
        if lead_time is not None
        else None
    )

    fitted = fit_to_terms(raw, terms)
    quantity = fitted["quantity"]

    unit_cost = _number(recommendation.get("unit_cost"))
    value = None
    if unit_cost is not None:
        value = (Decimal(str(unit_cost)) * quantity).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    label = (
        "shortfall below reorder point"
        if suggested is not None and suggested > raw_units
        else _BASIS_LABELS.get(order_quantity.get("basis"), "recommended")
    )
    return {
        "item_code": item_code,
        "site_code": site_code,
        "position_units": int(position),
        "reorder_point_units": int(reorder_point),
        "daily_demand": daily if daily is not None else 0.0,
        "due_in_days": due,
        "order_by": order_by,
        "lead_time_days": lead_time,
        "arrives_on": arrives_on,
        "raw_quantity": raw,
        "quantity": quantity,
        "adjustments": fitted["adjustments"],
        "short_of_need": fitted["short_of_need"],
        "terms": terms,
        "unit_cost": unit_cost,
        "value": value,
        "arithmetic": _arithmetic(raw, label, terms, fitted["adjustments"], quantity),
        "supplier_code": supplier_code,
        "source_system": line_source_system,
        "supplier_reason": supplier_reason,
        "source": "reorder_point",
    }


def _group_value(lines: list[dict]) -> Decimal:
    """The group's lines' values summed to 0 (a line without a value
    contributes nothing)."""
    return sum(
        (line["value"] for line in lines if line["value"] is not None),
        Decimal(0),
    )


def _supplier_group(
    key: tuple[str | None, str | None],
    lines: list[dict],
    suppliers_by_code: dict[str | None, dict],
    minimums_by_pair: dict[tuple[str | None, str | None], object],
) -> dict:
    supplier_code, source_system = key
    supplier = (
        suppliers_by_code.get(supplier_code) if supplier_code is not None else None
    )
    total_value = _group_value(lines)
    # The group's stored order minimum, keyed by (source_system,
    # supplier_code) compared exactly as stored — a stored pair never
    # matches a group of another case, system or code — and the
    # shortfall of the total against it, both rounded to cents
    # half-up the way the lines' values are.  Null for every group
    # without a stored minimum: the keys stay out of the response.
    minimum = None
    shortfall = None
    amount = minimums_by_pair.get((source_system, supplier_code))
    if amount is not None:
        minimum = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        shortfall = (
            minimum - total_value if total_value < minimum else Decimal(0)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "supplier_code": supplier_code,
        "source_system": source_system,
        "vendor_code": supplier.get("vendor_code") if supplier else None,
        "name": supplier.get("name") if supplier else None,
        "line_count": len(lines),
        "total_value": total_value,
        "minimum": minimum,
        "shortfall": shortfall,
        "lines": [
            {
                field: line[field]
                for field in (
                    "item_code",
                    "site_code",
                    "position_units",
                    "reorder_point_units",
                    "daily_demand",
                    "due_in_days",
                    "order_by",
                    "lead_time_days",
                    "arrives_on",
                    "raw_quantity",
                    "quantity",
                    "adjustments",
                    "short_of_need",
                    "terms",
                    "unit_cost",
                    "value",
                    "arithmetic",
                    "supplier_reason",
                    "source",
                )
            }
            for line in lines
        ],
    }


def build_supply_plan(horizon_days: int, as_of: date | None = None) -> dict:
    """The ``GET /api/supply-plan`` builder, shared with the release.

    The plan's lines as a plain dict of the ``SupplyPlanResponse``
    shape (``as_of``, ``horizon_days``, ``line_count``, ``total_value``,
    ``suppliers``): the algorithm is the one the route documents — the
    due item-sites of the horizon, the raw quantity fitted to the
    item's supplier terms of the pair's ``source_system``, one supplier
    group per (supplier, source system) — and, with the
    ``supply_plan.plan_buffers_by_signal`` system setting on (the
    default), a designated item-site whose recommendation has status
    "ok" is planned by its DDMRP buffer's order signal instead of its
    reorder point (the DDMRP plan read once per call through
    ``_buffer_rows``, a raised read building the plan exactly as with
    the setting off) — and the resolved as-of (``default_as_of``) the
    as-of, unless ``as_of`` is given, in which case that date is the
    as-of and ``default_as_of`` is never consulted.  Read-only.  The
    release route
    ``POST /api/supply-plan/release`` builds its plan from the same
    lines — passing the latest transaction date in the synced data as
    ``as_of`` so the release's origin tag never names a server date —
    so preview, confirm and the GET route never disagree on what is
    due.

    The body runs inside one ``app.db.connection.one_connection``
    block: every ``get_cursor`` of one build — the plan's own reads
    and the buffer read's — rides the one connection, opened at the
    block's first use and closed when it exits.  A caller already
    inside such a block (``top_ups_by_supplier``, the exceptions
    inbox) shares that block's connection; the nested block opens
    nothing.
    """
    with one_connection():
        return _build_supply_plan(horizon_days, as_of)


def _build_supply_plan(horizon_days: int, as_of: date | None = None) -> dict:
    with get_cursor() as cur:
        if as_of is None:
            as_of = default_as_of(cur)

    recommendations = recommendations_for_all(as_of)

    source_system_by_pair = {
        (row["item_code"], row["site_code"]): row.get("source_system")
        for row in _item_site_repo.list_rows(_ALL_ROWS, 0)
    }
    terms_by_item: dict[str, list[dict]] = {}
    for row in _supplier_terms_repo.list():
        terms_by_item.setdefault(str(row.get("item_code") or ""), []).append(row)
    suppliers_by_code = {
        row.get("supplier_code"): row for row in _supplier_repo.list_all()
    }
    # The stored order minimums, keyed by (source_system,
    # supplier_code) exactly as stored: the group's ``minimum`` and
    # ``shortfall`` answer only for the exact stored pair, and the
    # read is the one of the call — a raised read fails the plan,
    # never a plan without the keys.
    minimums_by_pair = {
        (row.get("source_system"), row.get("supplier_code")): row.get("amount")
        for row in _supplier_order_minimum_repo.list()
    }

    # The lower-risk supplier switch: the stored setting, and — only
    # with the setting on — the per-supplier risk rating, read once;
    # a raised read builds the plan exactly as with the setting off.
    stored_settings = _stored_system_settings()
    risk_by_code = (
        _risk_by_supplier_code()
        if _prefer_lower_risk_supplier_enabled(stored_settings)
        else None
    )
    # The buffer read: only with the
    # supply_plan.plan_buffers_by_signal setting on — the DDMRP plan's
    # rows keyed by (item_code, site_code), read once per call; a
    # raised read builds the plan exactly as with the setting off.
    buffer_rows = None
    if _plan_buffers_by_signal_enabled(stored_settings):
        try:
            buffer_rows = _buffer_rows(as_of)
        except Exception:
            buffer_rows = None

    lines = [
        line
        for line in (
            _plan_line(
                recommendation,
                as_of,
                horizon_days,
                source_system_by_pair,
                terms_by_item,
                risk_by_code,
                suppliers_by_code,
                buffer_rows,
            )
            for recommendation in recommendations
        )
        if line is not None
    ]
    lines.sort(
        key=lambda line: (line["due_in_days"], line["item_code"], line["site_code"])
    )

    groups: dict[tuple[str | None, str | None], list[dict]] = {}
    for line in lines:
        groups.setdefault((line["supplier_code"], line["source_system"]), []).append(
            line
        )

    supplier_groups = [
        _supplier_group(key, group_lines, suppliers_by_code, minimums_by_pair)
        for key, group_lines in sorted(
            (entry for entry in groups.items() if entry[0][0] is not None),
            key=lambda entry: (
                min(line["due_in_days"] for line in entry[1]),
                -_group_value(entry[1]),
            ),
        )
    ]
    # The lines with no supplier form a single final group spanning
    # every source system, its supplier_code and source_system null.
    if (None, None) in groups:
        supplier_groups.append(
            _supplier_group(
                (None, None), groups[(None, None)], suppliers_by_code, minimums_by_pair
            )
        )

    return {
        "as_of": as_of,
        "horizon_days": horizon_days,
        "line_count": len(lines),
        "total_value": _group_value(lines),
        "suppliers": supplier_groups,
    }


@router.get("", response_model=SupplyPlanResponse)
def supply_plan(
    horizon_days: int = Query(default=7, ge=1, le=60),
) -> SupplyPlanResponse:
    """The supply plan for the next ``horizon_days`` days (1 to 60,
    default 7), for any signed-in user.

    An item-site is in the plan when its recommendation has status "ok"
    with a numeric recommended order quantity and it is due within the
    horizon: due in 0 days when its position (on hand plus on order) is
    at or below its reorder point, else in the whole days of daily
    demand left above the reorder point (a pair with no positive daily
    demand above the point is not due).  The raw quantity is the larger
    of the recommended and the suggested order quantity; it is fitted to
    the item's supplier terms — the ``is_default`` supplier of the
    item-site's ``source_system``, else the lowest ``supplier_code`` —
    with ``app.supply.terms.fit_to_terms``.  With the
    ``supply_plan.prefer_lower_risk_supplier`` system setting on (the
    default), a line whose default pick's supplier is rated ``high`` or
    ``medium`` by ``app.recommend.supplier_risk.supplier_risk`` (read
    once per call) instead buys from the lowest-rated other terms row
    of the same item and source system (``low`` below ``medium`` below
    ``high``; ties to the row marked default, then the lowest
    ``supplier_code``) — everything of the line following the chosen
    row, and the switched line's ``supplier_reason`` naming the
    default pick's supplier, its risk and its signs.  With the
    ``supply_plan.plan_buffers_by_signal`` system setting on (the
    default), a designated item-site — one of the DDMRP plan's rows,
    the plan read once per call — whose recommendation has status "ok"
    is instead planned by its buffer's order signal: with the signal
    on, one line due now (``due_in_days`` 0, ``order_by`` the as-of),
    its raw quantity the buffer's suggested order units fitted to the
    supplier's terms, its ``position_units`` the buffer's net flow
    rounded down to whole units, its ``reorder_point_units`` the top
    of yellow, and its ``source`` reading ``"buffer"`` — the
    arithmetic's label naming the buffer signal; with the signal off
    the item-site has no line, whatever its reorder point says.
    Every other line's ``source`` reads ``"reorder_point"``.
    """
    return SupplyPlanResponse(**build_supply_plan(horizon_days))


def top_ups_by_supplier(horizon_days: int, as_of: date | None = None) -> dict:
    """The ``GET /api/supply-plan/top-ups`` answer as a dict: the
    plan's ``as_of``, ``horizon_days``, ``lookahead_days`` — the
    effective ``supply_plan.top_up_lookahead_days`` setting, the
    stored value rounded down to a whole number of days, else the
    default 30 — ``holding_rate``, the effective
    ``replenishment.holding_rate`` the recommendations use, and
    ``suppliers``.

    ``suppliers`` is one entry per supplier group of the
    ``horizon_days`` order plan that has a stored minimum and a
    shortfall above 0 — the groups ``GET /api/supply-plan`` reports
    with its ``minimum`` and ``shortfall``, in the plan's group order —
    each answered with the lines of the order plan built for
    ``lookahead_days`` at the same as-of that can top the group up:
    the group's same ``supplier_code`` and ``source_system`` lines
    whose ``due_in_days`` is above the horizon, whose ``value`` is not
    null and whose (``item_code``, ``site_code``) is not on the excess
    list (``excess_ranked`` read once per call).  The candidates are
    taken by ``due_in_days``, then ``item_code``, then
    ``site_code``, each added until the sum of the values taken is at
    least the shortfall — the candidate that reaches it included, none
    after it — each a top-up with the look-ahead line's
    ``item_code``, ``site_code``, ``due_in_days``, ``order_by``,
    ``quantity``, ``unit_cost`` and ``value`` plus ``holding_cost``
    (the value × the effective
    ``replenishment.holding_rate`` the recommendations use ×
    ``due_in_days`` / 365, rounded to two places half up).
    ``top_up_value`` is the top-ups' values summed (0 with none),
    ``reaches_minimum`` true when ``total_value`` plus
    ``top_up_value`` is at least the stored minimum and ``still_short``
    the minimum minus the two when that is above 0, else 0.

    The horizon plan is built with ``build_supply_plan(horizon_days,
    as_of)`` — ``as_of`` None letting the builder resolve the as-of —
    and the look-ahead plan is built for ``lookahead_days`` at the
    horizon plan's as-of, both through this module's own
    ``build_supply_plan`` and ``excess_ranked`` names.

    The body runs inside one ``with`` statement — one
    ``app.recommend.reorder.recommendations_memo`` block, one
    ``app.db.read_cache.read_once`` block and one
    ``app.db.connection.one_connection`` block: the two order plans'
    ``recommendations_for_all`` calls and the excess ranking's own
    read share the one memo, so the recommendations are computed once
    per as-of, each read the cached readers serve is read once, and
    the call's cursors ride the one connection.  A caller already
    inside any of the blocks shares the outer one (the nested use
    each already allows), so the answer — for the route
    ``GET /api/supply-plan/top-ups`` and for the release
    (``app.supply.release``) — is the same as without the blocks.
    Read-only.
    """
    with recommendations_memo(), read_once(), one_connection():
        plan = build_supply_plan(horizon_days, as_of)
        as_of = plan["as_of"]
        stored = _stored_system_settings()
        lookahead_days = _top_up_lookahead_days_for(stored)
        holding_rate = _holding_rate_for(stored)
        lookahead_plan = build_supply_plan(lookahead_days, as_of=as_of)
        excess_pairs = {
            (row.get("item_code"), row.get("site_code")) for row in excess_ranked(as_of)
        }

        suppliers: list[dict] = []
        for group in plan["suppliers"]:
            minimum = group["minimum"]
            shortfall = group["shortfall"]
            if minimum is None or shortfall is None or not shortfall > 0:
                continue
            key = (group["supplier_code"], group["source_system"])
            candidates = [
                line
                for plan_group in lookahead_plan["suppliers"]
                if (
                    plan_group["supplier_code"],
                    plan_group["source_system"],
                )
                == key
                for line in plan_group["lines"]
                if line["due_in_days"] > horizon_days
                and line["value"] is not None
                and (line["item_code"], line["site_code"]) not in excess_pairs
            ]
            candidates.sort(
                key=lambda line: (
                    line["due_in_days"],
                    line["item_code"],
                    line["site_code"],
                )
            )
            top_ups: list[dict] = []
            top_up_value = Decimal(0)
            for line in candidates:
                top_ups.append(line)
                top_up_value += line["value"]
                if top_up_value >= shortfall:
                    break

            total_value = group["total_value"]
            top_up_value = top_up_value.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            still_short = minimum - total_value - top_up_value
            suppliers.append(
                {
                    "supplier_code": group["supplier_code"],
                    "source_system": group["source_system"],
                    "vendor_code": group["vendor_code"],
                    "name": group["name"],
                    "minimum": minimum,
                    "total_value": total_value,
                    "shortfall": shortfall,
                    "top_ups": [
                        {
                            "item_code": line["item_code"],
                            "site_code": line["site_code"],
                            "due_in_days": line["due_in_days"],
                            "order_by": line["order_by"],
                            "quantity": line["quantity"],
                            "unit_cost": line["unit_cost"],
                            "value": line["value"],
                            "holding_cost": _top_up_holding_cost(
                                line["value"], holding_rate, line["due_in_days"]
                            ),
                        }
                        for line in top_ups
                    ],
                    "top_up_value": top_up_value,
                    "reaches_minimum": total_value + top_up_value >= minimum,
                    "still_short": (
                        still_short if still_short > 0 else Decimal(0)
                    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                }
            )

        return {
            "as_of": as_of,
            "horizon_days": horizon_days,
            "lookahead_days": lookahead_days,
            "holding_rate": holding_rate,
            "suppliers": suppliers,
        }


@router.get("/top-ups", response_model=SupplyPlanTopUpsResponse)
def supply_plan_top_ups(
    horizon_days: int = Query(default=7, ge=1, le=60),
) -> SupplyPlanTopUpsResponse:
    """The top-ups of the order plan (1 to 60 horizon days, default 7),
    for any signed-in user.

    The supplier groups of the ``horizon_days`` order plan that have a
    stored minimum and a shortfall above 0 — the groups
    ``GET /api/supply-plan`` reports with its ``minimum`` and
    ``shortfall``, in the plan's group order — each answered with the
    lines of the order plan built for
    ``supply_plan.top_up_lookahead_days`` (the stored value rounded
    down to a whole number of days, else the default) at the same
    as-of that can top the group up: the group's same
    ``supplier_code`` and ``source_system`` lines whose
    ``due_in_days`` is above the horizon, whose ``value`` is not null
    and whose (``item_code``, ``site_code``) is not on the excess list
    (``excess_ranked`` read once per call).  The candidates are taken
    by ``due_in_days``, then ``item_code``, then ``site_code``, each
    added until the sum of the values taken is at least the shortfall —
    the candidate that reaches it included, none after it — each a
    top-up with the look-ahead line's ``item_code``, ``site_code``,
    ``due_in_days``, ``order_by``, ``quantity``, ``unit_cost`` and
    ``value`` plus ``holding_cost`` (the value × the effective
    ``replenishment.holding_rate`` the recommendations use ×
    ``due_in_days`` / 365, rounded to two places half up).
    ``top_up_value`` is the top-ups' values summed (0 with none),
    ``reaches_minimum`` true when ``total_value`` plus
    ``top_up_value`` is at least the stored minimum and ``still_short``
    the minimum minus the two when that is above 0, else 0.  Read-only.
    """
    return SupplyPlanTopUpsResponse(**top_ups_by_supplier(horizon_days))


def build_transfer_suggestions(as_of: date | None = None) -> dict:
    """The ``GET /api/supply-plan/transfers`` builder.

    The suggestions as a plain dict of the
    ``TransferSuggestionsResponse`` shape (``as_of``,
    ``suggestion_count``, ``total_value``, ``suggestions``): the
    ``app.supply.transfers.suggest_transfers`` match over the
    ``recommendations_for_all`` rows and the item_sites read (each
    pair's ``source_system``) — one read of each — the resolved as-of
    (``default_as_of``) the as-of, unless ``as_of`` is given, in which
    case that date is the as-of and ``default_as_of`` is never
    consulted.  The suggestions are ordered by value descending (the
    nulls last), then item_code; ``total_value`` is the non-null
    values summed to cents and 0 when every suggestion's value is
    null.  Read-only.
    """
    with get_cursor() as cur:
        if as_of is None:
            as_of = default_as_of(cur)

    recommendations = recommendations_for_all(as_of)
    source_system_by_pair = {
        (row["item_code"], row["site_code"]): row.get("source_system")
        for row in _item_site_repo.list_rows(_ALL_ROWS, 0)
    }
    suggestions = suggest_transfers(recommendations, source_system_by_pair)
    suggestions.sort(
        key=lambda suggestion: (
            suggestion["value"] is None,
            -suggestion["value"] if suggestion["value"] is not None else Decimal(0),
            suggestion["item_code"],
        )
    )
    return {
        "as_of": as_of,
        "suggestion_count": len(suggestions),
        "total_value": sum(
            (
                suggestion["value"]
                for suggestion in suggestions
                if suggestion["value"] is not None
            ),
            Decimal(0),
        ),
        "suggestions": suggestions,
    }


@router.get("/transfers", response_model=TransferSuggestionsResponse)
def transfer_suggestions() -> TransferSuggestionsResponse:
    """The inter-site transfer suggestions, for any signed-in user.

    Per item-site (the recommendation with status "ok" and a numeric
    recommended order quantity): the position — on hand plus on order —
    against the reorder point and the order quantity, a need when the
    reorder point stands above the position, a surplus when the
    position stands above the reorder point plus one order quantity.
    Per item, across the item-sites of the same source system, the
    largest need is served first, from the largest surplus first — each
    match the smaller of the two remainders in whole units, both
    reduced by it, a site never giving to itself.  The suggestions are
    ordered by value descending (the ones without a value last), then
    item_code.
    """
    return TransferSuggestionsResponse(**build_transfer_suggestions())


# The summary's fixed horizon: the 7-day plan the page answers.
_SUMMARY_HORIZON_DAYS = 7

# The system setting the summary's awaiting-release window reads,
# with the registry default (3) standing in for a missing or
# out-of-range stored value.
_RELEASE_APPROVAL_DAYS_KEY = "exceptions.release_approval_days"

# The ERP statuses that stop an in-flight transfer release row
# counting: once the ERP reads the order in one of these the
# on-hand sync shows the moved stock, so the row stops awaiting.
_TRANSFER_RELEASED_ERP_STATUSES = ("Released", "Completed", "Canceled")


def _aware_utc(value: object) -> datetime | None:
    """A row timestamp (datetime or ISO string) as an aware UTC
    datetime, or None when it carries no timestamp.

    A naive timestamp is taken as UTC; the comparison window is a
    UTC timestamp, never the instance time zone.  A hostile value
    (not a datetime, not an ISO string) is no timestamp, never a
    500.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@router.get("/summary", response_model=SupplyPlanSummaryResponse)
def supply_plan_summary() -> SupplyPlanSummaryResponse:
    """The one-page summary of the 7-day supply plan, for any
    signed-in user.

    ``line_count``, ``total_value`` and ``supplier_count`` are the
    7-day order plan's ``line_count``, ``total_value`` and the
    number of its supplier groups with a non-null
    ``supplier_code`` — the plan ``GET
    /api/supply-plan?horizon_days=7`` answers, built by the same
    ``build_supply_plan`` call — and ``transfer_count`` and
    ``transfer_value`` are the transfer suggestions'
    ``suggestion_count`` and ``total_value``, the suggestions
    ``GET /api/supply-plan/transfers`` answers, built by the same
    ``build_transfer_suggestions`` call.  The two builders run
    inside one ``recommendations_memo``: the first reads its
    ``recommendations_for_all`` through the memo and the second
    answers from it, so the recommendations are computed exactly
    once per request.  ``transfers_in_flight`` counts the rows
    ``TransferReleasePostgresRepository.list_in_flight`` returns
    whose ``erp_status`` is null or none of "Released", "Completed"
    and "Canceled", and ``transfers_awaiting_release`` how many of
    those are at least the
    ``exceptions.release_approval_days`` system setting (default 3)
    old by ``updated_at`` — a missing or out-of-range stored value
    falls back to the registry default, the exact cutoff inclusive,
    as the exceptions inbox' transfer window is.
    """
    with recommendations_memo():
        plan = build_supply_plan(_SUMMARY_HORIZON_DAYS)
        transfers = build_transfer_suggestions()
        release_rows = _transfer_release_repo.list_in_flight()
        try:
            stored_settings = _stored_system_settings()
        except Exception:
            stored_settings = None
        approval_days = setting_value(stored_settings, _RELEASE_APPROVAL_DAYS_KEY)
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=approval_days)
        in_flight = [
            row
            for row in release_rows
            if isinstance(row, dict)
            and row.get("erp_status") not in _TRANSFER_RELEASED_ERP_STATUSES
        ]
        awaiting_release = 0
        for row in in_flight:
            updated_at = _aware_utc(row.get("updated_at"))
            if updated_at is not None and updated_at <= cutoff:
                awaiting_release += 1
    return SupplyPlanSummaryResponse(
        as_of=plan["as_of"],
        horizon_days=plan["horizon_days"],
        line_count=plan["line_count"],
        total_value=plan["total_value"],
        supplier_count=sum(
            1 for group in plan["suppliers"] if group["supplier_code"] is not None
        ),
        transfer_count=transfers["suggestion_count"],
        transfer_value=transfers["total_value"],
        transfers_in_flight=len(in_flight),
        transfers_awaiting_release=awaiting_release,
    )


_NUL = chr(0)


class OrderPlanTopUpEntry(BaseModel):
    """One chosen top-up of the order-plan release body: the supplier,
    the item and the site — exactly the keys the top-up suggestions
    hold.  Each field is 1 to 64 characters without NUL bytes, and any
    extra field is refused with 422, like every other body in the app.
    """

    model_config = ConfigDict(extra="forbid")

    supplier_code: constr(min_length=1, max_length=64)
    item_code: constr(min_length=1, max_length=64)
    site_code: constr(min_length=1, max_length=64)


class OrderPlanReleaseIn(BaseModel):
    """The order-plan release body.

    ``horizon_days`` is the supply-plan horizon (1 to 60, default 7),
    ``supplier_codes`` the non-empty list of up to 50 supplier codes —
    each 1 to 64 characters, no NUL bytes — ``top_ups`` the (default
    empty) list of at most 200 chosen top-ups — each exactly
    ``supplier_code``, ``item_code`` and ``site_code``, each 1 to 64
    characters without NUL bytes, repeated entries counting once, and
    each entry's ``supplier_code`` one of ``supplier_codes`` (a code
    that is not is refused with 422, naming that code) — and
    ``confirm`` the preview/confirm flag (default false).  Any extra
    field is refused with 422, like every other body in the app; a
    missing body is 422 by request validation.
    """

    model_config = ConfigDict(extra="forbid")

    horizon_days: int = Field(default=7, ge=1, le=60)
    supplier_codes: list[constr(min_length=1, max_length=64)] = Field(
        min_length=1, max_length=50
    )
    top_ups: list[OrderPlanTopUpEntry] = Field(default_factory=list, max_length=200)
    confirm: bool = False

    @field_validator("supplier_codes")
    @classmethod
    def _no_nul_bytes(cls, codes: list[str]) -> list[str]:
        if any(_NUL in code for code in codes):
            raise ValueError("a supplier code must not contain NUL bytes")
        return codes

    @model_validator(mode="after")
    def _top_ups_named_and_counted(self) -> OrderPlanReleaseIn:
        named = set(self.supplier_codes)
        seen: set[tuple[str, str, str]] = set()
        for entry in self.top_ups:
            for field in ("supplier_code", "item_code", "site_code"):
                if _NUL in getattr(entry, field):
                    raise ValueError(f"{field} must not contain NUL bytes")
            key = (entry.supplier_code, entry.item_code, entry.site_code)
            if key in seen:
                continue  # repeated entries count once
            if entry.supplier_code not in named:
                raise ValueError(
                    f"top-up supplier code {entry.supplier_code!r} is not "
                    "one of the named supplier codes"
                )
            seen.add(key)
        if len(seen) > 200:
            raise ValueError("at most 200 top-ups may be named")
        return self


@router.post("/release", response_model=ReleasePlan)
def release_order_plan(
    body: OrderPlanReleaseIn,
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """Preview or confirm the release of the due supply-plan lines.

    The plan is ``app.supply.release.order_plan_release(horizon_days,
    supplier_codes, top_ups)`` — the lines the ``GET /api/supply-plan``
    builder produces for ``horizon_days``, kept for the named
    suppliers, one group per (site, supplier) of the ``acumatica``
    lines with its origin tag "AIERP order plan <as_of> site
    <site_code> vendor <supplier_code>" (the as-of is the builder's
    resolved as-of, never regenerated at confirm time) — and the
    body's ``top_ups`` are passed through to it: each named top-up the
    ``GET /api/supply-plan/top-ups`` suggestions hold at the release's
    as-of joins its (site, supplier) group as a line — the group
    created, with the same origin tag a due line's group would carry,
    when the supplier has no due line at that site — and a named
    top-up the suggestions do not hold adds nothing and is listed in
    ``unreleasable`` as "not a suggested top-up".  The answer is the
    shared ``release_groups`` body of ``app.api.aggregate``: a preview
    (``confirm`` false or absent, any signed-in user) with the
    Acumatica outcome of each group on top, and a confirm (admin only
    — 403 otherwise — the advisory lock, 409 on a second confirm, 503
    on incomplete Acumatica configuration, 409 on switched-off writes,
    one ``plan_releases`` row per group with its outcome, a 502 marking
    the rows aborted) exactly as ``POST /api/aggregate/release`` does
    it.  A named supplier's lines from another source system come back
    as ``unreleasable``; a code no line of the plan carries is simply
    absent — 200 with the groups the named suppliers hold, never a
    422.
    """
    try:
        plan = order_plan_release(
            body.horizon_days,
            body.supplier_codes,
            [
                (entry.supplier_code, entry.item_code, entry.site_code)
                for entry in body.top_ups
            ],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return release_groups(plan, body.confirm, current_user)


def _canonical_transfer_lines(lines: list[dict]) -> tuple:
    """One transfer line snapshot as a comparable value.

    The quantities compare as whole numbers — a whole quantity stored
    as a JSON number reads the same from the stored jsonb snapshot as
    from the live plan — so a changed line is detected across both
    shapes.
    """

    def _units(value: object) -> object:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            number = float(value)
            return int(number) if number.is_integer() else value
        return value

    return tuple((line["item_code"], _units(line["quantity"])) for line in lines)


def _apply_transfer_results(groups: list[dict], results: list[dict]) -> None:
    """Copy each client per-order result onto its group, in order."""
    for group, result in zip(groups, results):
        group["outcome"] = result["outcome"]
        group["reference_nbr"] = result["reference_nbr"]
        group["from_warehouse_code"] = result["from_warehouse_code"]
        group["to_warehouse_code"] = result["to_warehouse_code"]
        group["error"] = result["error"]


class TransferReleaseEntry(BaseModel):
    """One chosen transfer suggestion: the item and the two sites.

    The request never carries a quantity — the quantity comes from
    the suggestion the entry matches.  Any extra field is refused
    with 422, like every other body in the app.
    """

    model_config = ConfigDict(extra="forbid")

    item_code: constr(min_length=1, max_length=64)
    from_site: constr(min_length=1, max_length=64)
    to_site: constr(min_length=1, max_length=64)


class TransferReleaseIn(BaseModel):
    """The transfer release body.

    ``transfers`` is a non-empty list of up to 50 chosen transfer
    suggestions — each string 1 to 64 characters without NUL bytes,
    one entry per distinct suggestion (two entries with the same
    (item_code, from_site, to_site) are refused) — and ``confirm``
    the preview/confirm flag (default false).  Any extra field is
    refused with 422, like every other body in the app; a missing
    body is 422 by request validation.
    """

    model_config = ConfigDict(extra="forbid")

    transfers: list[TransferReleaseEntry] = Field(min_length=1, max_length=50)
    confirm: bool = False

    @model_validator(mode="after")
    def _no_nul_no_duplicates(self) -> TransferReleaseIn:
        seen: set[tuple[str, str, str]] = set()
        for entry in self.transfers:
            for field in ("item_code", "from_site", "to_site"):
                if _NUL in getattr(entry, field):
                    raise ValueError(f"{field} must not contain NUL bytes")
            key = (entry.item_code, entry.from_site, entry.to_site)
            if key in seen:
                raise ValueError(
                    "one entry per distinct suggestion: (item_code, "
                    "from_site, to_site) is repeated"
                )
            seen.add(key)
        return self


class TransferReleaseLine(BaseModel):
    """One line of one transfer release group, exactly as sent to
    Acumatica: the item and the suggested quantity in whole units."""

    item_code: str
    quantity: int


class TransferReleaseGroup(BaseModel):
    """One (from_site, to_site) group of the transfer release plan.

    ``outcome``, ``reference_nbr``, ``from_warehouse_code``,
    ``to_warehouse_code`` and ``error`` are null in the bare plan and
    in the preview with an incomplete Acumatica configuration; the
    preview fills them from the client's per-order result, a confirm
    from its batch — and for an ``exists`` outcome whose current plan
    lines no longer match the stored snapshot of the origin, the
    group keeps the outcome but not the existing order's reference
    number (null) and carries the stale-lines error instead.
    """

    origin: str
    from_site: str
    to_site: str
    lines: list[TransferReleaseLine]
    outcome: str | None = None
    reference_nbr: str | None = None
    from_warehouse_code: str | None = None
    to_warehouse_code: str | None = None
    error: str | None = None


class TransferReleaseUnreleasable(BaseModel):
    """One requested entry that is not released: the entry's item and
    sites with the reason — no longer suggested, or a source system
    other than Acumatica."""

    item_code: str
    from_site: str
    to_site: str
    reason: str


class TransferReleasePlan(BaseModel):
    """The transfer release plan with the per-group Acumatica outcomes
    on top.

    ``as_of`` is the as-of snapshot the current suggestions were read
    at (never null), ``groups`` the (from_site, to_site) groups with
    their lines in request order, and ``unreleasable`` the requested
    entries that are no longer suggested or come from a source system
    other than Acumatica.
    """

    as_of: date
    groups: list[TransferReleaseGroup]
    unreleasable: list[TransferReleaseUnreleasable]


class TransferReleaseRow(BaseModel):
    """One stored transfer_releases audit row, every column of it."""

    id: int
    origin: str
    from_site_code: str
    to_site_code: str
    from_warehouse_code: str | None
    to_warehouse_code: str | None
    reference_nbr: str | None
    outcome: str
    error: str | None
    line_count: int
    lines: list[TransferReleaseLine]
    actor: str
    actor_name: str | None
    created_at: str
    updated_at: str
    erp_status: str | None
    erp_status_checked_at: str | None


def confirm_transfer_release(
    entries: list[dict],
    actor: str,
    actor_name: str | None = None,
    suggestions: dict | None = None,
) -> dict:
    """The confirm half of the transfer release: one path for a
    person and for a standing grant.

    The route's confirm, moved out of
    ``release_transfer_suggestions`` so
    ``app.supply.standing_release.run_transfer_grant`` sends its
    chosen groups through the very same path.  Holds the transfer
    release's advisory lock — a second confirm waits, 409 when it
    cannot take it — and, under the lock, builds the plan: without
    ``suggestions`` the as-of snapshot is resolved after the lock is
    taken (the snapshot the audit row records, the one the release's
    suggestions and origin tag use) and the plan is
    ``transfer_release_plan(entries, as_of=...)``; with
    ``suggestions`` — the read of the shape
    ``build_transfer_suggestions`` answers — no as-of is resolved and
    the plan is ``transfer_release_plan(entries,
    suggestions=...)``.  A plan with no groups is answered as it is,
    with no configuration check and no call of any kind.  Otherwise,
    before any row: incomplete Acumatica configuration 503,
    switched-off writes 409.  Then one ``pending``
    ``transfer_releases`` row per group — ``actor`` its ``actor``,
    ``actor_name`` its ``actor_name`` — and the client's transfer
    create once with every group, each row updated to the group's
    outcome with its reference number, warehouse codes and error; an
    ``exists`` outcome carries the existing order's reference number
    only when the current plan's lines match every stored snapshot of
    the same origin (a changed plan keeps the outcome but not the
    reference number, with the stale-lines error in the row and the
    answer, the confirm still answering 200, never a 409), an
    Acumatica auth or transport failure that aborts the batch is 502
    with the rows ``aborted``, a batch the client refuses 422 with
    the rows ``error`` — the lock released in a ``finally``.
    """
    lock_conn = acquire_transfer_release_lock()
    if lock_conn is None:
        raise HTTPException(
            status_code=409, detail="Another transfer release is already in progress"
        )
    try:
        if suggestions is None:
            # The as-of snapshot the audit row records is resolved
            # after the lock is taken — the same snapshot the
            # release's suggestions and origin tag use, never a
            # snapshot taken before the wait.
            with get_cursor() as cur:
                as_of = default_as_of(cur)
            plan = transfer_release_plan(entries, as_of=as_of)
        else:
            # The caller's read: no as-of is resolved, the caller's
            # snapshot is the one the plan and the origin tag use.
            plan = transfer_release_plan(entries, suggestions=suggestions)
        if not plan["groups"]:
            # Nothing to release: no configuration consultation, no
            # call of any kind.
            return plan
        if not _acumatica_config_complete():
            raise HTTPException(
                status_code=503, detail="Acumatica configuration is incomplete"
            )
        try:
            # The write switch, consulted the way the client
            # consults it — before any request, no write.
            transfer_write_probe()
        except AcumaticaWritesDisabledError:
            raise HTTPException(
                status_code=409, detail="Acumatica writes are disabled"
            ) from None
        release_ids = [
            _transfer_release_repo.insert_pending(
                origin=group["origin"],
                from_site_code=group["from_site"],
                to_site_code=group["to_site"],
                lines=group["lines"],
                actor=actor,
                actor_name=actor_name,
            )
            for group in plan["groups"]
        ]
        try:
            results = create_transfer_release(plan["groups"])
        except AcumaticaAuthError as exc:
            for release_id in release_ids:
                _transfer_release_repo.update_outcome(
                    release_id, outcome="aborted", error=str(exc)
                )
            raise HTTPException(status_code=502, detail=str(exc)) from None
        except AcumaticaError as exc:
            for release_id in release_ids:
                _transfer_release_repo.update_outcome(
                    release_id, outcome="aborted", error=str(exc)
                )
            raise HTTPException(status_code=502, detail=str(exc)) from None
        except ValueError as exc:
            for release_id in release_ids:
                _transfer_release_repo.update_outcome(
                    release_id, outcome="error", error=str(exc)
                )
            raise HTTPException(status_code=422, detail=str(exc)) from None
        # An ``exists`` outcome is only accepted as the existing
        # order's when the current plan's snapshot matches every
        # stored snapshot of the same origin — otherwise the existing
        # transfer order was planned with other lines and the group
        # keeps the outcome but not the reference number.  The local
        # snapshot lookup serves only this stale-line guard; the
        # outcome itself is the ERP-reported one.
        stale: set[int] = set()
        stale_error: dict[int, str] = {}
        snapshots_by_origin: dict[str, list] = {}
        for index, (group, result) in enumerate(zip(plan["groups"], results)):
            if result["outcome"] != "exists":
                continue
            if group["origin"] not in snapshots_by_origin:
                snapshots_by_origin[group["origin"]] = (
                    _transfer_release_repo.list_snapshots_for_origins(
                        [group["origin"]]
                    ).get(group["origin"], [])
                )
            current = _canonical_transfer_lines(group["lines"])
            if any(
                _canonical_transfer_lines(stored) != current
                for stored in snapshots_by_origin[group["origin"]]
            ):
                stale_error[index] = (
                    f"the existing transfer order for origin {group['origin']} "
                    "no longer matches the planned lines; "
                    "re-confirm is required"
                )
                stale.add(index)
        for index, (group, release_id, result) in enumerate(
            zip(plan["groups"], release_ids, results)
        ):
            if index in stale:
                _transfer_release_repo.update_outcome(
                    release_id,
                    outcome="exists",
                    reference_nbr=None,
                    from_warehouse_code=result["from_warehouse_code"],
                    to_warehouse_code=result["to_warehouse_code"],
                    error=stale_error[index],
                )
            else:
                _transfer_release_repo.update_outcome(
                    release_id,
                    outcome=result["outcome"],
                    reference_nbr=result["reference_nbr"],
                    from_warehouse_code=result["from_warehouse_code"],
                    to_warehouse_code=result["to_warehouse_code"],
                    error=result["error"],
                )
        _apply_transfer_results(plan["groups"], results)
        for index in stale:
            plan["groups"][index]["reference_nbr"] = None
            plan["groups"][index]["error"] = stale_error[index]
        return plan
    finally:
        release_transfer_release_lock(lock_conn)


@router.post("/transfers/release", response_model=TransferReleasePlan)
def release_transfer_suggestions(
    body: TransferReleaseIn,
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """Preview or confirm the release of the chosen transfer suggestions.

    The plan is
    ``app.supply.transfer_release.transfer_release_plan`` over the
    current suggestions — the suggestions the
    ``GET /api/supply-plan/transfers`` builder produces, read once,
    each requested entry matched by (item_code, from_site, to_site)
    with the suggestion's quantity, one group per (from_site,
    to_site) with the origin tag "AIERP transfer <as_of> site
    <from_site> to <to_site>" (in the preview the as-of is the
    builder's resolved as-of; in the confirm it is the snapshot
    resolved after the release's advisory lock is taken, so the audit
    row records the same snapshot the release used) — the unmatched
    entries as ``unreleasable`` ("no longer suggested") and the
    non-Acumatica suggestions ("only Acumatica transfers are released
    here").

    A preview (``confirm`` false or absent, any signed-in user) calls
    the client's transfer preview once with every group and answers
    the plan with each group's outcome, reference number and warehouse
    codes on top — all null with an incomplete Acumatica
    configuration, no client call — writing nothing.

    A confirm (``confirm`` true) is admin only (403 otherwise, before
    any ERP call or row) and holds the transfer release's advisory
    lock — a second confirm waits, 409 when it cannot take it — then
    resolves the as-of snapshot, builds the plan and, before any
    row: incomplete Acumatica configuration 503, switched-off writes
    409.  Otherwise one ``pending`` ``transfer_releases`` row per
    group is written (the caller's username and display name as the
    actor), and the client's transfer create is called once with
    every group — the orders are created ON HOLD, moving the stock
    still requires releasing the hold under Acumatica's own process —
    and
    each row is updated to the group's outcome (``created`` or
    ``exists``) with its reference number, warehouse codes and error,
    returning the plan with each group's outcome.  An ``exists``
    outcome carries the existing order's reference number only when
    the current plan's lines match every stored snapshot of the same
    origin — a changed plan keeps the outcome but not the reference
    number, with the stale-lines error in the row and the answer, and
    the confirm still answers 200, never a 409.  An Acumatica auth or
    transport failure that aborts the batch is 502 and marks the
    release's rows ``aborted``, so the audit always shows every
    release attempt.  A confirm with no releasable groups is 200
    with empty groups and calls Acumatica not at all.
    """
    entries = [entry.model_dump() for entry in body.transfers]
    if not body.confirm:
        plan = transfer_release_plan(entries)
        if not _acumatica_config_complete():
            return plan
        try:
            results = preview_transfer_release(plan["groups"])
        except AcumaticaAuthError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        except AcumaticaConfigError:
            # The configuration changed between the check and the
            # call: report it as unavailable, not as a failure.
            return plan
        except AcumaticaError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        _apply_transfer_results(plan["groups"], results)
        return plan
    require_admin(current_user)
    return confirm_transfer_release(
        entries,
        current_user.username or current_user.sub,
        actor_name=current_user.display_name,
    )


@router.get("/transfers/release/audit", response_model=list[TransferReleaseRow])
def list_transfer_releases(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: AuthUser = Depends(require_admin),
) -> list[dict]:
    """The newest stored transfer release rows, at most ``limit`` of
    them (1 to 200, default 50); admin only.

    ADMIN-ONLY, checked via ``require_admin`` exactly like the confirm:
    a signed-in non-admin is refused with 403 and without a token with
    401, before any row is read.
    """
    return _transfer_release_repo.list_newest(limit)
