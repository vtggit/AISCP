"""Cross-ERP item comparison: a stored item mapping's two sides.

``cross_erp_items`` answers ``GET /api/cross-erp/items``.  It writes
nothing and calls no ERP: one read of ``erp_code_mappings`` (the
stored rows of kind ``item``), one of ``item_sites``, one of
``on_hand`` and one of ``inventory_items`` feed the rows, and the
figures come from the same shared functions the other screens read —
``app.recommend.reorder.recommendations_for_all``,
``app.recommend.stockout.stockout_ranked`` and
``app.recommend.excess.excess_ranked`` — each called once per call,
inside one ``app.recommend.reorder.recommendations_memo``, the way
``GET /api/exceptions`` does, so every figure agrees with those
screens.  No figure from one side is added into the other's
plan: the per-site figures are keyed by (source_system, item_code,
site_code), so a side's sites carry only the figures stored under
their own source_system — the on hand and excess units 0 when the
stored row is under another source system, the pair's recommendation
and stockout figures carried under the pair's item-site source
system.

The row's ``opportunity`` flag says that one side is short while
the other holds excess: it is true when one side has a site whose
``stockout_category`` is not null and the other side has a site whose
``excess_units`` is above 0, and ``short_side`` and ``excess_side``
then read that side's index in ``sides``.  With the flag,
``suggested_move_units`` is the smaller of two sums, rounded down to
a whole number and never below 0: the need, the short side's sites
whose ``stockout_category`` is not null summed over their gap — the
site's ``reorder_point_units`` minus its ``on_hand_units``, never
below 0, a site with a null reorder point counting 0 — and the
excess side's sites summed over their ``excess_units``.  Without the
flag the flag is false, both sides are null and
``suggested_move_units`` is null too.

A side's ``source_system`` is the source of that side's item-sites —
the single ``source_system`` all of them store — and null when the
side has no item-sites (or when its item-sites do not agree).  The
source side counts the source code's item-sites whose
``source_system`` is not ``odoo`` (a null ``source_system`` counts as
not ``odoo``); the target side counts only the target code's
item-sites under ``odoo``.
"""

import math
from datetime import date

from app.db.connection import get_cursor
from app.recommend.excess import excess_ranked
from app.recommend.reorder import recommendations_for_all, recommendations_memo
from app.recommend.stockout import stockout_ranked
from app.repositories.erp_code_mappings_postgres_repository import (
    ErpCodeMappingPostgresRepository,
)
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository
from app.stats.demand import default_as_of

_TARGET_SIDE_SYSTEM = "odoo"
_ABSENT = object()

_mappings_repo = ErpCodeMappingPostgresRepository()
_inventory_repo = InventoryItemPostgresRepository()
_item_site_repo = ItemSitePostgresRepository()
_on_hand_repo = OnHandPostgresRepository()


def cross_erp_items(as_of: date | None = None) -> dict:
    """The cross-ERP comparison: one row per stored item mapping.

    Every row of ``erp_code_mappings`` of kind ``item`` — ordered by
    ``source_code`` — answers ``source_code``, ``target_code``,
    ``description`` (the ``inventory_items`` description of either
    code, the source's first, else null) and ``sides``: exactly two
    entries, the first for the source code's item-sites whose
    ``source_system`` is not ``odoo`` (a null ``source_system`` counts
    as not ``odoo``) and the second for the target code's item-sites
    under ``odoo``.  Each side
    carries ``source_system``, ``item_code``, ``sites`` (ordered by
    ``site_code``) and ``on_hand_units`` (the sum over the sites, 0
    with no sites).

    Each site entry carries ``site_code``, ``on_hand_units`` (the
    on hand stored under the site's own ``source_system``, 0 when no
    on-hand row is stored under it), ``daily_demand``
    (the recommendation's ``inputs.d``), ``days_of_cover`` (on hand
    over daily demand, rounded to one decimal, null when the demand
    is 0 or null), the recommendation's own ``status``,
    ``reorder_point_units`` and ``suggested_order_quantity`` (each
    null when the item-site has no recommendation),
    ``stockout_category`` (the ``stockout_ranked`` row's category —
    ``likely``, ``at_risk`` — null when the item-site has no row
    there) and ``excess_units`` (the ``excess_ranked`` row's excess
    units stored under the site's own ``source_system``, 0 when there
    is no such row).

    The row's ``opportunity`` is true when one side has a site whose
    ``stockout_category`` is not null and the other side has a site
    whose ``excess_units`` is above 0; ``short_side`` and
    ``excess_side`` then read that side's index in ``sides`` and
    ``suggested_move_units`` is the smaller of the need, the short
    side's non-null-stockout sites summed over their gap — the
    site's ``reorder_point_units`` minus its ``on_hand_units``,
    never below 0, a site with a null reorder point counting 0 —
    and the excess side's sites summed over their ``excess_units``,
    rounded down to a whole number and never below 0.  Otherwise
    ``opportunity`` is false and all three are null.

    The figures come from ``recommendations_for_all``,
    ``stockout_ranked`` and ``excess_ranked`` — the same functions
    the other screens read — each called once, inside one
    ``recommendations_memo``, so every figure agrees with those
    screens.  ``as_of`` is the shared default as-of date when none is
    given.  Returns ``{"as_of": <date>, "rows": [...]}``; with no
    mapping stored the rows are empty, not an error.
    """
    if as_of is None:
        with get_cursor() as cur:
            as_of = default_as_of(cur)

    with recommendations_memo():
        recommendations = recommendations_for_all(as_of)
        stockout_rows = stockout_ranked(as_of)
        excess_rows = excess_ranked(as_of)

    recommendation_by_key = {
        (row["source_system"], row["item_code"], row["site_code"]): row
        for row in recommendations
    }
    recommendation_source_by_pair = {
        (row["item_code"], row["site_code"]): row["source_system"]
        for row in recommendations
    }
    stockout_by_key: dict[tuple[str | None, str, str], str] = {}
    for row in stockout_rows:
        source = recommendation_source_by_pair.get(
            (row["item_code"], row["site_code"]), _ABSENT
        )
        if source is not _ABSENT:
            stockout_by_key[(source, row["item_code"], row["site_code"])] = row[
                "category"
            ]
    on_hand_rows = _on_hand_repo.list_rows(10**9, 0)
    on_hand_source_by_pair = {
        (row["item_code"], row["site_code"]): row["source_system"]
        for row in on_hand_rows
    }
    excess_by_key: dict[tuple[str | None, str, str], float] = {}
    for row in excess_rows:
        source = on_hand_source_by_pair.get(
            (row["item_code"], row["site_code"]), _ABSENT
        )
        if source is not _ABSENT:
            excess_by_key[(source, row["item_code"], row["site_code"])] = row[
                "excess_units"
            ]

    on_hand_by_key = {
        (row["source_system"], row["item_code"], row["site_code"]): row[
            "quantity_on_hand"
        ]
        for row in on_hand_rows
    }
    description_by_code = {
        row["item_code"]: row["description"] for row in _inventory_repo.list_all()
    }

    sites_by_code: dict[str, list[dict]] = {}
    for row in _item_site_repo.list_rows(10**9, 0):
        sites_by_code.setdefault(row["item_code"], []).append(row)

    rows: list[dict] = []
    for mapping in sorted(
        _mappings_repo.list(kind="item"), key=lambda row: row["source_code"]
    ):
        source_code = mapping["source_code"]
        target_code = mapping["target_code"]
        sides = [
            _side(
                source_code,
                sites_by_code,
                on_hand_by_key,
                recommendation_by_key,
                stockout_by_key,
                excess_by_key,
                odoo_only=False,
            ),
            _side(
                target_code,
                sites_by_code,
                on_hand_by_key,
                recommendation_by_key,
                stockout_by_key,
                excess_by_key,
                odoo_only=True,
            ),
        ]
        description = description_by_code.get(source_code)
        if description is None:
            description = description_by_code.get(target_code)
        rows.append(
            {
                "source_code": source_code,
                "target_code": target_code,
                "description": description,
                "sides": sides,
                **_opportunity(sides),
            }
        )
    return {"as_of": as_of, "rows": rows}


def _side(
    item_code: str,
    sites_by_code: dict[str, list[dict]],
    on_hand_by_key: dict[tuple[str | None, str, str], object],
    recommendation_by_key: dict[tuple[str | None, str, str], dict],
    stockout_by_key: dict[tuple[str | None, str, str], str],
    excess_by_key: dict[tuple[str | None, str, str], float],
    *,
    odoo_only: bool,
) -> dict:
    """One side of the comparison: one code's item-sites, its sites
    ordered by ``site_code`` and the side's summed on hand.

    The target side counts only the code's item-sites under
    ``odoo``; the source side counts the code's item-sites whose
    ``source_system`` is not ``odoo`` (a null ``source_system`` counts
    as not ``odoo``).
    """
    rows = [
        row
        for row in sites_by_code.get(item_code, [])
        if (row["source_system"] == _TARGET_SIDE_SYSTEM) == odoo_only
    ]
    rows.sort(key=lambda row: row["site_code"])
    sites = [
        _site(
            row,
            on_hand_by_key,
            recommendation_by_key,
            stockout_by_key,
            excess_by_key,
        )
        for row in rows
    ]
    source_systems = {row["source_system"] for row in rows}
    return {
        "source_system": (
            next(iter(source_systems)) if len(source_systems) == 1 else None
        ),
        "item_code": item_code,
        "sites": sites,
        "on_hand_units": sum(site["on_hand_units"] for site in sites),
    }


def _site(
    row: dict,
    on_hand_by_key: dict[tuple[str | None, str, str], object],
    recommendation_by_key: dict[tuple[str | None, str, str], dict],
    stockout_by_key: dict[tuple[str | None, str, str], str],
    excess_by_key: dict[tuple[str | None, str, str], float],
) -> dict:
    """One site entry: the item-site's own figures, never another
    side's.  The figures are looked up under the item-site's own
    (source_system, item_code, site_code): the on hand and excess
    units are 0 when stored under another source system, and the
    recommendation's fields keep their own names — each null when the
    item-site has no recommendation."""
    key = (row["source_system"], row["item_code"], row["site_code"])
    recommendation = recommendation_by_key.get(key)
    inputs = recommendation.get("inputs") if recommendation is not None else None
    daily_demand = inputs["d"] if inputs is not None else None
    stored_on_hand = on_hand_by_key.get(key)
    on_hand_units = float(stored_on_hand) if stored_on_hand is not None else 0.0
    return {
        "site_code": row["site_code"],
        "on_hand_units": on_hand_units,
        "daily_demand": daily_demand,
        "days_of_cover": (
            round(on_hand_units / daily_demand, 1)
            if daily_demand is not None and daily_demand != 0
            else None
        ),
        "status": recommendation["status"] if recommendation is not None else None,
        "reorder_point_units": (
            recommendation.get("reorder_point_units")
            if recommendation is not None
            else None
        ),
        "suggested_order_quantity": (
            recommendation.get("suggested_order_quantity")
            if recommendation is not None
            else None
        ),
        "stockout_category": stockout_by_key.get(key),
        "excess_units": excess_by_key.get(key, 0),
    }


def _opportunity(sides: list[dict]) -> dict:
    """The row's flag and move figure: one side short while the other
    holds excess.

    ``short_side`` and ``excess_side`` read that side's index in
    ``sides``.  With the flag, ``suggested_move_units`` is the smaller
    of the need — the short side's sites whose ``stockout_category``
    is not null summed over their gap, each the site's
    ``reorder_point_units`` minus its ``on_hand_units``, never below
    0, a site with a null reorder point counting 0 — and the excess
    side's sites summed over their ``excess_units``, rounded down to
    a whole number and never below 0; without it the flag is false
    and all three are null.
    """
    short = [
        any(site["stockout_category"] is not None for site in side["sites"])
        for side in sides
    ]
    excess = [any(site["excess_units"] > 0 for site in side["sites"]) for side in sides]
    if short[0] and excess[1]:
        short_side, excess_side = 0, 1
    elif short[1] and excess[0]:
        short_side, excess_side = 1, 0
    else:
        return {
            "opportunity": False,
            "short_side": None,
            "excess_side": None,
            "suggested_move_units": None,
        }
    need = sum(
        max(0, (site["reorder_point_units"] or 0) - site["on_hand_units"])
        for site in sides[short_side]["sites"]
        if site["stockout_category"] is not None
    )
    excess_units = sum(site["excess_units"] for site in sides[excess_side]["sites"])
    return {
        "opportunity": True,
        "short_side": short_side,
        "excess_side": excess_side,
        "suggested_move_units": max(0, math.floor(min(need, excess_units))),
    }
