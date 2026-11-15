"""ABC classification of the recommendable item-sites.

The value of one item-site is a year of its daily demand at its unit
cost: ``365 * d * unit_cost`` with ``d`` the daily demand of the
recommendation's ``inputs`` and ``unit_cost`` the value carried from
the item_sites row. An item-site earns a value only from an ``"ok"``
recommendation whose ``d`` and ``unit_cost`` are both positive;
every other recommendation (not ok, or a missing, non-positive or
non-finite ``d`` or ``unit_cost``) leaves the item-site unclassified.

``classify`` applies the classic ABC (Pareto) cut to a list of
``{item_code, site_code, annual_value}`` items: the valued items are
ranked by ``annual_value`` descending (ties by ``item_code``, then
``site_code``) and cut at the cumulative-share thresholds ``A_SHARE``
(0.80) and ``B_SHARE`` (0.95) — an item-site whose cumulative share
*before* itself is below ``A_SHARE`` is an ``"A"``, below ``B_SHARE``
a ``"B"``, otherwise a ``"C"``.
"""

from __future__ import annotations

import math
from datetime import date

from app.recommend.reorder import recommendations_for_all

A_SHARE = 0.80
B_SHARE = 0.95


def _positive_finite_number(value: object) -> float | None:
    """The value as a finite number > 0, else None.

    Non-numeric values (including None, bools, NaN, infinity and
    strings that do not parse) never raise — they simply are not a
    positive number.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _annual_value(recommendation: dict) -> float | None:
    """The item-site's annual value, null when it is unclassifiable.

    ``365 * inputs["d"] * unit_cost`` for an ``"ok"`` recommendation
    whose ``d`` and ``unit_cost`` are both positive, else None.
    """
    if recommendation.get("status") != "ok":
        return None
    inputs = recommendation.get("inputs") or {}
    daily = _positive_finite_number(inputs.get("d"))
    unit_cost = _positive_finite_number(recommendation.get("unit_cost"))
    if daily is None or unit_cost is None:
        return None
    return 365.0 * daily * unit_cost


def classify(items: list[dict]) -> list[dict]:
    """ABC-classify the item-sites of ``items`` by annual value.

    ``items`` are ``{item_code, site_code, annual_value}`` dicts;
    ``annual_value`` is null (or not positive, not numeric or not
    finite) for an item-site without a value. Returns one dict per
    input — the classified ones first in rank order, then the
    unclassified ones ordered by ``item_code``, then ``site_code`` —
    with exactly these keys:

    * ``item_code`` / ``site_code`` — the pair;
    * ``annual_value`` — the value as a float, null when not positive;
    * ``rank`` — 1-based among the classified, null when unclassified;
    * ``share_before`` — the cumulative share of the annual values
      ranked before this item-site (0.0 for the first), null when
      unclassified;
    * ``abc_class`` — ``"A"`` while ``share_before`` is below
      ``A_SHARE``, ``"B"`` while it is below ``B_SHARE``, ``"C"``
      otherwise; null when unclassified.
    """
    classified: list[dict] = []
    unclassified: list[dict] = []
    for item in items:
        value = _positive_finite_number(item.get("annual_value"))
        row = {
            "item_code": item["item_code"],
            "site_code": item["site_code"],
            "annual_value": value,
        }
        (classified if value is not None else unclassified).append(row)

    total = sum(row["annual_value"] for row in classified)
    classified.sort(
        key=lambda row: (-row["annual_value"], row["item_code"], row["site_code"])
    )
    unclassified.sort(key=lambda row: (row["item_code"], row["site_code"]))

    results: list[dict] = []
    cumulative = 0.0
    for rank, row in enumerate(classified, start=1):
        share_before = cumulative / total
        abc_class = (
            "A" if share_before < A_SHARE else "B" if share_before < B_SHARE else "C"
        )
        results.append(
            {
                "item_code": row["item_code"],
                "site_code": row["site_code"],
                "annual_value": row["annual_value"],
                "rank": rank,
                "share_before": share_before,
                "abc_class": abc_class,
            }
        )
        cumulative += row["annual_value"]
    for row in unclassified:
        results.append(
            {
                "item_code": row["item_code"],
                "site_code": row["site_code"],
                "annual_value": None,
                "rank": None,
                "share_before": None,
                "abc_class": None,
            }
        )
    return results


def abc_for_all(as_of: date | None = None) -> list[dict]:
    """The ABC classification of every item-site, live.

    Builds the ``{item_code, site_code, annual_value}`` items from ONE
    ``recommendations_for_all(as_of)`` call — an ``"ok"``
    recommendation prices its item-site at ``365 * inputs["d"] *
    unit_cost`` when both are positive, every other recommendation
    leaves the item-site unclassified — and returns ``classify(...)``.
    """
    items = [
        {
            "item_code": recommendation["item_code"],
            "site_code": recommendation["site_code"],
            "annual_value": _annual_value(recommendation),
        }
        for recommendation in recommendations_for_all(as_of)
    ]
    return classify(items)
