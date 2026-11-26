"""The DDMRP plan over the "ok" DDMRP buffer-zone rows.

The plan prices every designated item-site whose DDMRP zone row
(``GET /api/ddmrp/zones``) is "ok" against its buffer zones, from the
pair's on-hand position and its open purchase supply.  The qualified
demand is the pair's open sales-order demand from ``open_sales_lines``
(read once per call from ``list_latest`` with the pairs of the "ok"
zone rows; whether the feed carries any open sales-order lines at all
is answered by ``has_lines``), limited to the lines with
``open_units`` above 0: ``qualified_due`` is the sum of the pair's
lines with ``ship_on`` at or before the as-of date (a null
``ship_on`` counts as due on the as-of date), and
``qualified_spike`` is the sum of the day's total of the pair's lines
with ``ship_on`` after the as-of date and no later than the as-of
date plus the row's ``dlt_days`` rounded up to whole days, summed per
``ship_on`` date: a day whose total is at least 50% of the row's
``top of red`` counts its whole total, and the lines of a day that
does not qualify count for nothing, a line with ``open_units`` of 0
or below skipped before the sums so it never lowers a day's total;
``qualified_demand`` is the sum of the two and ``net_flow`` is the
on hand plus the open supply minus it.  When the feed carries no open
sales-order lines at all, every row keeps the note "no open
sales-order demand in the feed", the qualified demand is 0 and the
two part fields stay out of the response; otherwise that note is
replaced by "qualified demand <q> units: <d> due, <s> spike", each
part the float sum rounded to the nearest whole unit (halves up) and
<q> the sum of the two rounded parts.  The open supply
sums the ``open_units`` of the pair's ``open_purchase_lines`` rows
(read once from ``list_latest`` with the pairs of the "ok" zone
rows) with ``open_units`` above 0 whose
``promised_on`` is null or no more than 90 days before the as-of date:
future ``promised_on`` dates count, a promise exactly 90 days before
the as-of date counts, and the excluded lines are counted in the
stale note, plus the pair's in-flight release units —
``app.recommend.reorder._in_flight_units_by_key`` over the reorder
module's own full open-purchase read with the plan's own as-of date
(a released order the feed picked up is counted from the feed only,
never twice) — and its in-flight transfer units —
``app.recommend.reorder._in_flight_transfer_units_by_key``, added at
the receiving site and taken from the sending site — each helper
once per call, skipped with the plan's other feed reads when there is
no "ok" zone row, and outside the stale window.  A row whose
in-flight release units are above 0 gains the note "<r> units
released, not yet in the feed"; a row whose in-flight transfer units
are above 0 gains "in-flight transfers add <t> units" (below 0,
"in-flight transfers take <t> units", the absolute value), each
number rounded to the nearest whole unit (halves up), and the notes
come after the stale note and before the demand note.
``planning_zone`` is "red" at or below the top of red,
"yellow" at or below the top of yellow, "green" at or below the top
of green and "over" above; ``order_signal`` is true at or below the
top of yellow, and ``suggested_order_units`` is the ceiling of the top
of green minus the net flow when the signal, else 0.
``planning_priority_pct`` is 100 times the net flow over the top of
green, rounded to one decimal place (null when the top of green is 0),
and the rows are ordered by it ascending, nulls last, then item_code
and site_code.  ``on_hand_status`` is "stocked_out" at or below 0,
"critical" below half the top of red, "low" below the top of red and
"ok" otherwise, and ``on_hand_pct_of_red`` is 100 times the on hand
over the top of red, rounded to one decimal place (null when the top
of red is 0).  Nothing is written.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from pydantic import BaseModel, model_serializer

from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.repositories.open_sales_lines_postgres_repository import (
    OpenSalesLinePostgresRepository,
)
from app.units import ceil_units

#: Open purchase lines promised more than this many days before the
#: as-of date are stale and excluded from the open supply.
STALE_WINDOW_DAYS = 90

#: The note of a plan row whose pair has no on-hand record (read 0).
NO_ON_HAND_NOTE = "no on-hand record; treated as 0"

#: The note of every plan row whose feed carries no open sales-order
#: lines at all: the qualified demand is 0.
NO_DEMAND_NOTE = "no open sales-order demand in the feed"

_on_hand_repo = OnHandPostgresRepository()
_open_po_repo = OpenPurchaseLinePostgresRepository()
_open_so_repo = OpenSalesLinePostgresRepository()


class DdmrpPlanRow(BaseModel):
    """One plan row: an "ok" DDMRP buffer-zone row of
    ``GET /api/ddmrp/zones`` enriched with the on-hand position, the
    open supply, the qualified demand, the net flow and the planning
    decision fields."""

    item_code: str
    site_code: str
    status: str
    adu: float
    adu_window_days: float
    dlt_days: float | None
    dlt_source: str | None
    demand_class: str | None
    cv: float | None
    order_cycle_days: float | None
    lead_time_category: str | None
    lead_time_factor: float | None
    variability_category: str | None
    variability_factor: float | None
    red_base: float | None
    red_safety: float | None
    red: float | None
    yellow: float | None
    green: float | None
    top_of_red: int | None
    top_of_yellow: int | None
    top_of_green: int | None
    arithmetic: str | None
    on_hand: float
    open_supply: float
    qualified_demand: float
    qualified_due: float | None = None
    qualified_spike: float | None = None
    net_flow: float
    planning_zone: str
    order_signal: bool
    suggested_order_units: int
    planning_priority_pct: float | None
    on_hand_status: str
    on_hand_pct_of_red: float | None
    notes: list[str]

    @model_serializer(mode="wrap")
    def _serialize_row(self, handler):
        """The row's fields, without ``qualified_due`` and
        ``qualified_spike`` when the feed carried no open sales-order
        lines at all (then the two part fields stay out of the
        response, exactly as before the feed existed)."""
        data = handler(self)
        for field in ("qualified_due", "qualified_spike"):
            if data.get(field) is None:
                data.pop(field, None)
        return data


class DdmrpPlanSummary(BaseModel):
    """The plan's counts: the rows with an order signal and the rows
    per on-hand status (the "ok" rows count in none of them)."""

    signals: int
    stocked_out: int
    critical: int
    low: int


class DdmrpPlanResponse(BaseModel):
    """The DDMRP plan of every designated item-site with an "ok" zone
    row, as of one date, with the summary counts."""

    as_of: date
    rows: list[DdmrpPlanRow]
    summary: DdmrpPlanSummary


def open_supply_for(lines: list[dict], as_of: date) -> tuple[float, int]:
    """The open supply of one item-site's open purchase lines, as of
    ``as_of``.

    Sums ``open_units`` over the lines with ``open_units`` above 0
    whose ``promised_on`` is null or no more than
    ``STALE_WINDOW_DAYS`` days before the as-of date: future
    ``promised_on`` dates count and a promise exactly
    ``STALE_WINDOW_DAYS`` days before the as-of date counts.  A line
    with ``open_units`` of 0 or less is neither counted nor stale.
    Returns the sum and the number of stale lines excluded.
    """
    cutoff = as_of - timedelta(days=STALE_WINDOW_DAYS)
    units = 0.0
    stale = 0
    for line in lines:
        open_units = float(line["open_units"])
        if open_units <= 0:
            continue
        promised_on = line["promised_on"]
        if promised_on is not None and promised_on < cutoff:
            stale += 1
        else:
            units += open_units
    return units, stale


def _round_half_up(value: float) -> int:
    """A non-negative quantity in units, rounded to the nearest whole
    unit, halves up."""
    return int(math.floor(value + 0.5))


def _qualified_demand_note(due: float, spike: float) -> str:
    """The demand note of a plan row whose feed carries open
    sales-order lines: "qualified demand <q> units: <d> due, <s>
    spike", where <d> and <s> are the two float sums rounded to the
    nearest whole unit (halves up) and <q> is the sum of the two
    rounded values."""
    due_units = _round_half_up(due)
    spike_units = _round_half_up(spike)
    return (
        f"qualified demand {due_units + spike_units} units: "
        f"{due_units} due, {spike_units} spike"
    )


def qualified_demand_for(
    lines: list[dict], as_of: date, dlt_days: float | None, top_of_red: int
) -> tuple[float, float]:
    """The qualified demand of one item-site's open sales lines, as of
    ``as_of``.

    Sums the ``open_units`` of the lines above 0: ``qualified_due``
    the lines with ``ship_on`` at or before the as-of date (a null
    ``ship_on`` counts as due on the as-of date), ``qualified_spike``
    the day's total of the lines with ``ship_on`` after the as-of
    date and no later than the as-of date plus the row's ``dlt_days``
    rounded up to whole days, summed per ``ship_on`` date: a day
    whose total is at least 50% of the row's ``top of red`` counts
    its whole total, and the lines of a day that does not qualify
    count for nothing; a line with ``open_units`` of 0 or below is
    skipped before the sums, so it never lowers a day's total.
    Returns the two sums.
    """
    horizon = (
        as_of + timedelta(days=math.ceil(dlt_days)) if dlt_days is not None else as_of
    )
    due = 0.0
    day_totals: dict[date, float] = {}
    for line in lines:
        open_units = float(line["open_units"])
        if open_units <= 0:
            continue
        ship_on = line["ship_on"]
        if ship_on is None or ship_on <= as_of:
            due += open_units
        elif ship_on <= horizon:
            day_totals[ship_on] = day_totals.get(ship_on, 0.0) + open_units
    # Double the day's total rather than halving the top of red:
    # 0.5 * top_of_red converts a large odd top of red to float,
    # rounding the threshold down a half unit and qualifying a day a
    # half unit below the true half.
    spike = sum(total for total in day_totals.values() if total * 2 >= top_of_red)
    return due, spike


def _planning_zone(
    net_flow: float, top_of_red: int, top_of_yellow: int, top_of_green: int
) -> str:
    """The planning zone of a net flow against the whole-unit tops:
    "red" at or below the top of red, "yellow" at or below the top of
    yellow, "green" at or below the top of green, else "over"."""
    if net_flow <= top_of_red:
        return "red"
    if net_flow <= top_of_yellow:
        return "yellow"
    if net_flow <= top_of_green:
        return "green"
    return "over"


def _on_hand_status(on_hand: float, top_of_red: int) -> str:
    """The on-hand status of an on-hand quantity against the top of
    red: "stocked_out" at or below 0, "critical" below half the top of
    red, "low" below the top of red, else "ok"."""
    if on_hand <= 0:
        return "stocked_out"
    if on_hand < 0.5 * top_of_red:
        return "critical"
    if on_hand < top_of_red:
        return "low"
    return "ok"


def plan_ddmrp(as_of: date, zone_rows: list[dict]) -> dict:
    """The DDMRP plan from the zone rows of ``ddmrp_zone_rows``, as of
    ``as_of``.

    Keeps the "ok" rows and enriches each with the fields of
    ``DdmrpPlanRow``: the on hand from ``on_hand`` (0 with the note
    ``"no on-hand record; treated as 0"`` when the pair has none), the
    open supply of the pair's ``open_purchase_lines`` rows and the
    qualified demand of the pair's ``open_sales_lines`` rows — both
    feeds read once per call from ``list_latest`` with ``pairs`` set
    to the sorted distinct ``(item_code, site_code)`` of the zone rows
    whose status is "ok" (neither feed read when there is no such
    row), the purchase rows with the stale note when any line is
    excluded plus the pair's in-flight release units (
    ``app.recommend.reorder._in_flight_units_by_key`` over the
    reorder module's own full open-purchase read, with the plan's
    own as-of date) and its in-flight transfer units (
    ``app.recommend.reorder._in_flight_transfer_units_by_key``,
    added at the receiving site and taken from the sending site) —
    each helper once per call, neither read when there is no "ok"
    zone row, the stale window not applying to them — and the sales
    rows grouped by item-site: the lines with
    ``open_units`` above 0, ``qualified_due`` the ones due at or
    before the as-of date — a null ``ship_on`` counts as due — and
    ``qualified_spike`` the day's total of the ones after the as-of
    date and within the row's ``dlt_days`` rounded up to whole days,
    summed per ``ship_on`` date, counting only the days whose total
    is at least 50% of the row's top of red — whether the feed
    carries any open sales-order lines at all is answered by
    ``OpenSalesLinePostgresRepository.has_lines``, never by the
    filtered read — the net flow, the planning zone, the order
    signal, the suggested order units, the planning priority
    percentage, the on-hand status and the on-hand percentage of the
    top of red.  When the feed carries no open sales-order lines at
    all every row
    keeps the note ``"no open sales-order demand in the feed"`` and
    the two part fields stay out of the response; otherwise that note
    is replaced by ``"qualified demand <q> units: <d> due, <s>
    spike"``.  The rows are ordered by ``planning_priority_pct``
    ascending (nulls last), then item_code and site_code, and the
    response carries the summary counts.  Nothing is written.
    """
    pairs = tuple(
        sorted(
            {
                (row["item_code"], row["site_code"])
                for row in zone_rows
                if row["status"] == "ok"
            }
        )
    )
    on_hand_by_key = {
        (row["item_code"], row["site_code"]): row
        for row in _on_hand_repo.list_rows(10**9, 0)
    }
    lines_by_key: dict[tuple[str, str], list[dict]] = {}
    sales_lines: list[dict] = []
    in_flight_release: dict[tuple[str, str], float] = {}
    in_flight_transfer: dict[tuple[str, str], float] = {}
    if pairs:
        for line in _open_po_repo.list_latest(pairs=pairs):
            lines_by_key.setdefault((line["item_code"], line["site_code"]), []).append(
                line
            )
        sales_lines = _open_so_repo.list_latest(pairs=pairs)
        feed_has_lines = _open_so_repo.has_lines()
        # The in-flight supply the plan's own feed reads never see:
        # the reorder module's own helpers, each once per call — the
        # release units over the reorder module's own full open-
        # purchase read with the plan's own as-of date, the transfer
        # units added at the receiving site and taken from the
        # sending site.  Imported at call time: the reorder layer
        # and the aggregate projection import each other.
        from app.recommend import reorder

        in_flight_release = reorder._in_flight_units_by_key(
            reorder._open_po_repo.list_latest(), as_of
        )
        in_flight_transfer = reorder._in_flight_transfer_units_by_key()
    else:
        # No "ok" zone row: neither feed nor in-flight helper is read.
        feed_has_lines = False
    sales_by_key: dict[tuple[str, str], list[dict]] = {}
    for line in sales_lines:
        sales_by_key.setdefault((line["item_code"], line["site_code"]), []).append(line)

    rows: list[dict] = []
    for zone_row in zone_rows:
        if zone_row["status"] != "ok":
            continue
        row = dict(zone_row)
        key = (row["item_code"], row["site_code"])

        on_hand_row = on_hand_by_key.get(key)
        notes: list[str] = []
        if on_hand_row is None:
            on_hand = 0.0
            notes.append(NO_ON_HAND_NOTE)
        else:
            on_hand = float(on_hand_row["quantity_on_hand"])

        open_supply, stale = open_supply_for(lines_by_key.get(key, []), as_of)
        release_units = in_flight_release.get(key, 0.0)
        transfer_units = in_flight_transfer.get(key, 0.0)
        # The in-flight supply joins the open purchase units: no
        # stale window applies to it.
        open_supply = open_supply + release_units + transfer_units
        if stale:
            notes.append(f"{stale} stale open purchase lines excluded")
        if release_units > 0:
            notes.append(
                f"{_round_half_up(release_units)} units released, not yet in the feed"
            )
        if transfer_units > 0:
            notes.append(
                f"in-flight transfers add {_round_half_up(transfer_units)} units"
            )
        elif transfer_units < 0:
            notes.append(
                f"in-flight transfers take {_round_half_up(-transfer_units)} units"
            )

        if feed_has_lines:
            qualified_due, qualified_spike = qualified_demand_for(
                sales_by_key.get(key, []), as_of, row["dlt_days"], row["top_of_red"]
            )
            qualified_demand = qualified_due + qualified_spike
            notes.append(_qualified_demand_note(qualified_due, qualified_spike))
        else:
            qualified_due = None
            qualified_spike = None
            qualified_demand = 0.0
            notes.append(NO_DEMAND_NOTE)

        net_flow = on_hand + open_supply - qualified_demand
        top_of_red = row["top_of_red"]
        top_of_yellow = row["top_of_yellow"]
        top_of_green = row["top_of_green"]
        order_signal = net_flow <= top_of_yellow

        update = {
            "on_hand": on_hand,
            "open_supply": open_supply,
            "qualified_demand": qualified_demand,
            "qualified_due": qualified_due,
            "qualified_spike": qualified_spike,
            "net_flow": net_flow,
            "planning_zone": _planning_zone(
                net_flow, top_of_red, top_of_yellow, top_of_green
            ),
            "order_signal": order_signal,
            "suggested_order_units": (
                ceil_units(top_of_green - net_flow) if order_signal else 0
            ),
            "planning_priority_pct": (
                round(100.0 * net_flow / top_of_green, 1) if top_of_green else None
            ),
            "on_hand_status": _on_hand_status(on_hand, top_of_red),
            "on_hand_pct_of_red": (
                round(100.0 * on_hand / top_of_red, 1) if top_of_red else None
            ),
            "notes": notes,
        }
        if not feed_has_lines:
            # The feed carries no open sales-order lines at all: the
            # two part fields stay out of the response, exactly as
            # before the feed existed.
            update.pop("qualified_due")
            update.pop("qualified_spike")
        row.update(update)
        rows.append(row)

    rows.sort(
        key=lambda row: (
            row["planning_priority_pct"] is None,
            (
                row["planning_priority_pct"]
                if row["planning_priority_pct"] is not None
                else 0.0
            ),
            row["item_code"],
            row["site_code"],
        )
    )

    summary = {
        "signals": sum(1 for row in rows if row["order_signal"]),
        "stocked_out": sum(1 for row in rows if row["on_hand_status"] == "stocked_out"),
        "critical": sum(1 for row in rows if row["on_hand_status"] == "critical"),
        "low": sum(1 for row in rows if row["on_hand_status"] == "low"),
    }
    return {"as_of": as_of, "rows": rows, "summary": summary}
