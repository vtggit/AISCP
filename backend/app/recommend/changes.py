"""Reorder-point changes between a since date and an as-of date.

``reorder_point`` is the exact formula ``app.recommend.reorder.recommend``
uses, exposed as a pure function. ``shapley_contributions`` splits the
reorder-point difference between two input sets (``d``, ``sd_d``, ``lt``
and ``sd_lt``) into one Shapley value per input: each value is the
marginal effect of switching that one input from ``then`` to ``now``,
averaged over every subset of the other three inputs with the weight
``|S|! * (3 - |S|)! / 4!``, so the four values sum to the reorder-point
difference and an unchanged input gets exactly 0.

``recommendation_changes`` compares ``recommendations_for_all`` at two
dates (``since`` defaults to ``as_of - 30 days``), pairs the rows by
``(item_code, site_code)`` and returns one row per key whose status
changed or whose ``reorder_point_units`` moved, priced with the
item-site's ``unit_cost`` and ordered by the money at stake.
"""

from __future__ import annotations

import itertools
import math
from datetime import date, timedelta

from app.recommend.reorder import (
    _item_sites_by_key,
    _resolve_as_of,
    recommendations_for_all,
)

INPUT_KEYS = ("d", "sd_d", "lt", "sd_lt")
CONTRIBUTION_KEYS = (
    "demand_level",
    "demand_variability",
    "lead_time",
    "lead_time_variability",
)
MASTER_NOTE = "master lead time used"
MASTER_NOTE_NOW = "master lead time used now, not then"
MASTER_NOTE_THEN = "master lead time used then, not now"
SINCE_DEFAULT_DAYS = 30


def reorder_point(d: float, sd_d: float, lt: float, sd_lt: float, z: float) -> float:
    """The exact reorder point ``app.recommend.reorder.recommend`` computes.

    ``d * lt + z * sqrt(lt * sd_d**2 + d**2 * sd_lt**2)``.
    """
    return d * lt + z * math.sqrt(lt * sd_d**2 + d**2 * sd_lt**2)


def _marginal(before: float, after: float, weight: float) -> float:
    """``weight * (after - before)`` of one reorder-point evaluation.

    A switch that leaves the value unchanged (including an unchanged
    infinity, where ``after - before`` would be ``inf - inf = NaN``)
    contributes exactly 0.0.
    """
    if after == before:
        return 0.0
    if math.isnan(before) and math.isnan(after):
        return 0.0
    return weight * (after - before)


def shapley_contributions(then: dict, now: dict, z: float) -> dict:
    """Shapley value of each input for the reorder-point change.

    ``then`` and ``now`` each hold the keys ``d``, ``sd_d``, ``lt`` and
    ``sd_lt``. Returns the keys ``"demand_level"``,
    ``"demand_variability"``, ``"lead_time"`` and
    ``"lead_time_variability"`` (for ``d``, ``sd_d``, ``lt`` and
    ``sd_lt`` respectively). Each value is the sum, over every subset
    ``S`` of the other three inputs switched from ``then`` to ``now``,
    of ``|S|! * (3 - |S|)! / 4!`` times the marginal effect of switching
    the input itself. An input whose value is unchanged gets exactly
    0.0 (its switch is a no-op even when the shared value is infinite).
    When the reorder-point difference
    ``reorder_point(**now, z=z) - reorder_point(**then, z=z)`` is
    finite, the last changed input in canonical order carries the
    residual against the left-to-right sum of the others, so the four
    values sum to the exact difference (the weighted marginals alone
    round to a few ulps off for large values).
    """
    raw: dict[str, float] = {}
    for key, name in zip(INPUT_KEYS, CONTRIBUTION_KEYS):
        if then[key] == now[key]:
            raw[name] = 0.0
            continue
        others = [other for other in INPUT_KEYS if other != key]
        total = 0.0
        for size in range(len(others) + 1):
            weight = (
                math.factorial(size)
                * math.factorial(len(others) - size)
                / math.factorial(len(INPUT_KEYS))
            )
            for subset in itertools.combinations(others, size):
                base = {
                    other: (now[other] if other in subset else then[other])
                    for other in others
                }
                base[key] = then[key]
                before = reorder_point(**base, z=z)
                base[key] = now[key]
                after = reorder_point(**base, z=z)
                total += _marginal(before, after, weight)
        raw[name] = total
    target = reorder_point(**now, z=z) - reorder_point(**then, z=z)
    if math.isfinite(target) and all(
        math.isfinite(raw[name]) for name in CONTRIBUTION_KEYS
    ):
        residual = None
        for name in reversed(CONTRIBUTION_KEYS):
            if raw[name] != 0.0:
                residual = name
                break
        if residual is not None:
            prefix = 0.0
            for name in CONTRIBUTION_KEYS:
                if name == residual:
                    break
                prefix += raw[name]
            raw[residual] = target - prefix
    return {name: raw[name] for name in CONTRIBUTION_KEYS}


def _row_inputs(row: dict | None) -> dict | None:
    """The row's ``{d, sd_d, lt, sd_lt}`` inputs, None without them."""
    if row is None or "inputs" not in row:
        return None
    return {key: row["inputs"][key] for key in INPUT_KEYS}


def _uses_master(row: dict | None) -> bool:
    """True when the row's notes contain ``"master lead time used"``."""
    return row is not None and MASTER_NOTE in row.get("notes", [])


def _change_row(
    key: tuple[str, str],
    since: date,
    as_of: date,
    then_row: dict | None,
    now_row: dict | None,
    item_site: dict | None,
) -> dict:
    """One change row pairing a key's since and as-of recommendations."""
    item_code, site_code = key
    status_then = then_row["status"] if then_row is not None else "absent"
    status_now = now_row["status"] if now_row is not None else "absent"
    ok_then = then_row is not None and status_then == "ok"
    ok_now = now_row is not None and status_now == "ok"
    reorder_point_then = then_row["reorder_point"] if ok_then else None
    reorder_point_now = now_row["reorder_point"] if ok_now else None
    units_then = then_row["reorder_point_units"] if ok_then else None
    units_now = now_row["reorder_point_units"] if ok_now else None
    change_units = units_now - units_then if (ok_then and ok_now) else None
    inputs_then = _row_inputs(then_row)
    inputs_now = _row_inputs(now_row)
    contributions: dict | None = None
    if ok_then and ok_now:
        contributions = shapley_contributions(
            inputs_then, inputs_now, now_row["inputs"]["z"]
        )
    primary_driver: str | None = None
    if contributions is not None:
        primary_driver = max(contributions, key=lambda name: abs(contributions[name]))
    master_then = _uses_master(then_row)
    master_now = _uses_master(now_row)
    notes: list[str] = []
    if master_now and not master_then:
        notes.append(MASTER_NOTE_NOW)
    elif master_then and not master_now:
        notes.append(MASTER_NOTE_THEN)
    unit_cost = item_site["unit_cost"] if item_site is not None else None
    money: float | None = None
    if change_units is not None and unit_cost is not None and float(unit_cost) > 0:
        money = abs(change_units) * float(unit_cost)
    return {
        "item_code": item_code,
        "site_code": site_code,
        "as_of": as_of,
        "since": since,
        "status_then": status_then,
        "status_now": status_now,
        "reorder_point_then": reorder_point_then,
        "reorder_point_now": reorder_point_now,
        "reorder_point_units_then": units_then,
        "reorder_point_units_now": units_now,
        "change_units": change_units,
        "inputs_then": inputs_then,
        "inputs_now": inputs_now,
        "contributions": contributions,
        "primary_driver": primary_driver,
        "master_lead_time_then": master_then,
        "master_lead_time_now": master_now,
        "notes": notes,
        "unit_cost": unit_cost,
        "money": money,
    }


def _sort_key(row: dict) -> tuple:
    """Money descending first; rows without money by the absolute
    ``change_units`` descending (None last); then item and site."""
    if row["money"] is not None:
        return (0, -row["money"], row["item_code"], row["site_code"])
    if row["change_units"] is None:
        return (1, 0.0, row["item_code"], row["site_code"])
    return (1, -abs(row["change_units"]), row["item_code"], row["site_code"])


def recommendation_changes(
    as_of: date | None = None, since: date | None = None
) -> list[dict]:
    """Reorder-point changes between ``since`` and ``as_of``.

    ``as_of`` is resolved with
    ``app.recommend.reorder._resolve_as_of`` and ``since`` defaults to
    ``as_of - 30 days``; a ``since`` not earlier than ``as_of`` raises
    ``ValueError``. Calls ``recommendations_for_all`` at both dates,
    pairs the rows by ``(item_code, site_code)`` (a key present on only
    one date has status ``"absent"`` on the other) and returns one row
    per key whose status differs or whose ``reorder_point_units`` differ
    while both statuses are ``"ok"``. Each row carries both statuses,
    the exact reorder points and their unit ceilings (None unless that
    status is ok), ``change_units`` (None unless both are ok), both
    input sets, the Shapley ``contributions`` with the as-of row's
    ``z`` (else None), the ``primary_driver`` (largest absolute
    contribution, None without contributions), whether each row used
    the master lead time (with a note when the two differ), the
    item-site's ``unit_cost`` and ``money`` (``abs(change_units)``
    times ``unit_cost`` when both exist and the cost is positive, else
    None). Ordered by ``money`` descending, then the rows without
    ``money`` by absolute ``change_units`` descending (None last), then
    ``item_code`` and ``site_code``.
    """
    resolved_as_of = _resolve_as_of(as_of)
    if since is None:
        since = resolved_as_of - timedelta(days=SINCE_DEFAULT_DAYS)
    if since >= resolved_as_of:
        raise ValueError(
            f"since ({since.isoformat()}) must be earlier than as_of "
            f"({resolved_as_of.isoformat()})"
        )
    then_by_key = {
        (row["item_code"], row["site_code"]): row
        for row in recommendations_for_all(since)
    }
    now_by_key = {
        (row["item_code"], row["site_code"]): row
        for row in recommendations_for_all(resolved_as_of)
    }
    item_sites = _item_sites_by_key()
    rows = []
    for key in sorted(set(then_by_key) | set(now_by_key)):
        then_row = then_by_key.get(key)
        now_row = now_by_key.get(key)
        status_then = then_row["status"] if then_row is not None else "absent"
        status_now = now_row["status"] if now_row is not None else "absent"
        changed = status_then != status_now or (
            status_then == "ok"
            and status_now == "ok"
            and then_row["reorder_point_units"] != now_row["reorder_point_units"]
        )
        if not changed:
            continue
        rows.append(
            _change_row(
                key,
                since,
                resolved_as_of,
                then_row,
                now_row,
                item_sites.get(key),
            )
        )
    rows.sort(key=_sort_key)
    return rows
