"""The order-plan release plan: the due supply-plan lines as draft POs.

``order_plan_release(horizon_days, supplier_codes, top_ups)`` answers
which of the supply plan's due lines — the lines the
``GET /api/supply-plan`` builder
(``app.api.supply_plan.build_supply_plan``) produces for
``horizon_days`` — go out as draft purchase orders in Acumatica for
the named suppliers, as the plan dict of the same shape
``app.aggregate.release.plan_release`` returns: ``{"month", "groups",
"unreleasable", "total_value"}``.

The ``top_ups`` (default empty) are the chosen top-ups — each the
``(supplier_code, item_code, site_code)`` of one entry of the release
body's ``top_ups`` list, repeated entries counted once — and they are
read only when one is named: the suggestions the top-ups route holds
at the release's as-of
(``app.api.supply_plan.top_ups_by_supplier(horizon_days, as_of)``)
are then built, and each named top-up the suggestions hold — the same
``supplier_code``, ``item_code`` and ``site_code`` in a supplier
entry whose ``source_system`` is ``acumatica`` — joins its (site,
supplier) group as a line: the top-up's quantity in whole units, its
unit cost rounded exactly as a due line's is, its value added to the
group's and the plan's value — the group created, with the same
origin tag a due line's group would carry, when the supplier has no
due line at that site, and a group's lines staying ordered by item
code.  A named top-up the suggestions do not hold — no longer
suggested, another source system, or never suggested — adds nothing
and is listed in ``unreleasable`` with its item code, its site code
and the reason ``not a suggested top-up``.  No top-ups named, the
suggestions are never read, nothing new is computed, and the answer is
exactly the due lines'.

The as-of is the latest transaction date in the synced data — the
latest demand ``occurred_on``, else the latest lead-time
``received_on``, the same two dates the shared ``default_as_of``
resolution reads, minus its clamp to the server's ``date.today()`` —
and the plan's ``month`` is its ``YYYY-MM``; so the as-of, the month
and the origin tag are a function of the request and the data alone:
stable for a given release, across the preview and the confirm, and
never regenerated from the server clock at confirm time (when no data
has been synced at all the builder's ``default_as_of`` fallback
answers, the plan is empty, and no origin tag is ever written).
The groups keep only the builder's supplier groups whose
``supplier_code`` is in ``supplier_codes`` (a code no line carries is
simply absent — the answer is 200 with the groups the named suppliers
hold, never an error) and take only the ``acumatica`` ones: one group
per ``(site_code, supplier_code)`` — groups by site then supplier,
lines by item code — each tagged with its origin
``"AIERP order plan <as_of> site <site_code> vendor
<supplier_code>"``, the idempotency tag the draft PO is written under
in Acumatica.  Each line is ``{"item_code", "quantity", "unit_cost",
"value"}``: ``quantity`` the line's fitted quantity in whole units,
``unit_cost`` the line's unit cost rounded half-up to two decimal
places exactly the way ``plan_release`` rounds it —
``Decimal(0)`` when the line has none — and ``value`` the quantity at
that cost.  A named supplier's lines from another source system never
enter a group; they come back as ``unreleasable`` as
``{"item_code", "site_code", "reason": "only Acumatica lines are
released here"}``.  ``total_value`` is the summed value of every line
of every group (0 when there are none).  The function writes nothing.
"""

from collections.abc import Sequence
from decimal import Decimal

from app.aggregate.release import (
    exact_decimal,
    round_half_up_cents,
    whole_units,
)

NOT_ACUMATICA_REASON = "only Acumatica lines are released here"
NOT_SUGGESTED_REASON = "not a suggested top-up"


def _data_as_of():
    """The latest transaction date in the synced data, or None.

    The same two dates the shared ``default_as_of`` resolution reads —
    the latest ``occurred_on`` across the demand events, else the
    latest ``received_on`` across the lead time observations — but
    without the clamp to the server's ``date.today()``: the origin tag
    the release writes the draft POs under is an idempotency key, so
    the as-of it names must be a function of the data, stable for a
    given release across the preview and the confirm, never a date
    regenerated from the server clock.  None when no data has been
    synced at all (the builder's ``default_as_of`` fallback then
    answers, the plan is empty, and no tag is written).
    """
    from app.db.connection import get_cursor

    with get_cursor() as cur:
        cur.execute("SELECT MAX(occurred_on) AS latest FROM demand_events")
        latest = cur.fetchone()["latest"]
        if latest is None:
            cur.execute("SELECT MAX(received_on) AS latest FROM lead_time_observations")
            latest = cur.fetchone()["latest"]
    return latest


def order_plan_release(
    horizon_days: int,
    supplier_codes: list[str] | None = None,
    top_ups: Sequence[tuple[str, str, str]] = (),
) -> dict:
    """The draft-PO release plan of the due supply-plan lines.

    See the module docstring for the full contract: the builder's
    lines of ``horizon_days``, kept for the named suppliers, one group
    per (site, supplier) of the ``acumatica`` lines with its origin
    tag, the other source systems' lines of a named supplier as
    ``unreleasable`` — and the named ``top_ups`` (each the
    ``(supplier_code, item_code, site_code)`` of one chosen top-up,
    repeated entries counted once): each the suggestions
    ``top_ups_by_supplier(horizon_days, as_of)`` hold at the release's
    as-of joins its (site, supplier) group as a line, the rest listed
    in ``unreleasable`` as "not a suggested top-up" — the suggestions
    read only when a top-up is named.  ``supplier_codes=None`` means
    every supplier the plan holds — a list of codes keeps its meaning,
    and a line without a supplier never goes out either way.  Returns
    ``{"month", "groups", "unreleasable", "total_value"}`` and writes
    nothing.
    """
    # Imported here: app.api.supply_plan imports this module's plan
    # for its release route, so the builder must not be imported at
    # module level (it would be a cycle).
    from app.api.supply_plan import build_supply_plan

    # The release's as-of is the latest transaction date in the
    # synced data, passed to the builder explicitly: the builder's
    # own resolution (default_as_of) clamps to the server's
    # date.today(), and a confirm must carry the preview's tag, not a
    # tag rebuilt from the server clock.
    plan = build_supply_plan(horizon_days, _data_as_of())
    as_of = plan["as_of"]
    chosen = None if supplier_codes is None else set(supplier_codes)

    groups: dict[tuple[str, str], dict] = {}
    unreleasable: list[dict] = []
    total_value = Decimal(0)
    for supplier_group in plan["suppliers"]:
        supplier_code = supplier_group.get("supplier_code")
        if supplier_code is None or (
            chosen is not None and supplier_code not in chosen
        ):
            continue
        if supplier_group.get("source_system") == "acumatica":
            for line in supplier_group["lines"]:
                site_code = line["site_code"]
                key = (site_code, supplier_code)
                group = groups.setdefault(
                    key,
                    {
                        "site_code": site_code,
                        "supplier_code": supplier_code,
                        "origin": (
                            f"AIERP order plan {as_of.isoformat()} "
                            f"site {site_code} vendor {supplier_code}"
                        ),
                        "lines": [],
                        "value": Decimal(0),
                    },
                )
                quantity = whole_units(line["quantity"])
                unit_cost = line.get("unit_cost")
                unit_cost = (
                    Decimal(0) if unit_cost is None else exact_decimal(unit_cost)
                )
                if unit_cost.as_tuple().exponent < -2:
                    unit_cost = round_half_up_cents(unit_cost)
                line_value = Decimal(quantity) * unit_cost
                group["lines"].append(
                    {
                        "item_code": line["item_code"],
                        "quantity": quantity,
                        "unit_cost": unit_cost,
                        "value": line_value,
                    }
                )
                group["value"] += line_value
                total_value += line_value
        else:
            for line in supplier_group["lines"]:
                unreleasable.append(
                    {
                        "item_code": line["item_code"],
                        "site_code": line["site_code"],
                        "reason": NOT_ACUMATICA_REASON,
                    }
                )

    # The chosen top-ups — read only when one is named: the
    # suggestions the top-ups route holds at the release's as-of
    # (the horizon plan's), through the module's own name.  The
    # import stays out of the module level, like the builder's
    # above, for the same cycle: app.api.supply_plan imports this
    # module's plan for its release route.
    if top_ups:
        from app.api.supply_plan import top_ups_by_supplier

        held: dict[tuple[str, str, str], dict] = {}
        for supplier_entry in top_ups_by_supplier(horizon_days, as_of)["suppliers"]:
            if supplier_entry.get("source_system") != "acumatica":
                continue
            for top_up_line in supplier_entry["top_ups"]:
                held[
                    (
                        supplier_entry["supplier_code"],
                        top_up_line["item_code"],
                        top_up_line["site_code"],
                    )
                ] = top_up_line
        named: set[tuple[str, str, str]] = set()
        for supplier_code, item_code, site_code in top_ups:
            key = (supplier_code, item_code, site_code)
            if key in named:
                continue  # repeated entries count once
            named.add(key)
            top_up_line = held.get(key)
            if top_up_line is None:
                # The suggestions do not hold the top-up — no longer
                # suggested, another source system, or never
                # suggested: it adds nothing and is listed.
                unreleasable.append(
                    {
                        "item_code": item_code,
                        "site_code": site_code,
                        "reason": NOT_SUGGESTED_REASON,
                    }
                )
                continue
            group = groups.setdefault(
                (site_code, supplier_code),
                {
                    "site_code": site_code,
                    "supplier_code": supplier_code,
                    "origin": (
                        f"AIERP order plan {as_of.isoformat()} "
                        f"site {site_code} vendor {supplier_code}"
                    ),
                    "lines": [],
                    "value": Decimal(0),
                },
            )
            quantity = whole_units(top_up_line["quantity"])
            unit_cost = top_up_line.get("unit_cost")
            unit_cost = Decimal(0) if unit_cost is None else exact_decimal(unit_cost)
            if unit_cost.as_tuple().exponent < -2:
                unit_cost = round_half_up_cents(unit_cost)
            line_value = Decimal(quantity) * unit_cost
            group["lines"].append(
                {
                    "item_code": item_code,
                    "quantity": quantity,
                    "unit_cost": unit_cost,
                    "value": line_value,
                }
            )
            group["value"] += line_value
            total_value += line_value

    release_groups = []
    for key in sorted(groups):
        group = groups[key]
        group["lines"].sort(key=lambda line: line["item_code"])
        release_groups.append(group)
    return {
        "month": as_of.strftime("%Y-%m"),
        "groups": release_groups,
        "unreleasable": unreleasable,
        "total_value": total_value,
    }
