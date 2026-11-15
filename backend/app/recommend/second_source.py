"""Second-source candidates for the sole-source items of the risk rating.

``second_sources`` reads the supplier risk rating exactly once
(``app.recommend.supplier_risk.supplier_risk``), reads the stored
values of the ``second_source.similar_prefix_length`` and
``second_source.qualification_days`` settings in the one stored-
settings read it performs, calls
``app.recommend.reorder.recommendations_for_all`` once per call,
without an as-of (a raised read answers the entries with null bridge
fields), and reads everything else in three reads it performs once
each: every supplier terms row through
``SupplierTermsPostgresRepository().list()``, every inventory item
through ``InventoryItemPostgresRepository().list_all()`` and every
supplier through ``SupplierPostgresRepository().list_all()``.  It
writes nothing and calls no ERP.

One entry per item code of each risk entry's
``sole_source_item_codes`` — the items the risk entry's supplier is
the sole source of:

  * ``item_code`` — the item code;
  * ``description`` — the item's inventory item description, null
    when the item has no inventory item row;
  * ``supplier_code``, ``supplier_name``, ``late`` and ``variable`` —
    the risk entry's;
  * ``supplier_risk`` — the risk entry's ``risk``;
  * ``candidates`` — one candidate per other ``supplier_code`` with a
    terms row for the item (a terms row naming the entry's own
    supplier is never a candidate), followed by at most three
    similar candidates, the empty list when neither exists;
  * ``daily_demand`` — the sum of the ``inputs.d`` of the
    recommendations read's rows of the item whose ``inputs.d`` is a
    number, rounded to 2 places, null when there is no such row (and
    null when the recommendations read raised);
  * ``bridge_days`` — the ``lead_time_days`` of the first terms
    candidate in candidate order with a lead time a number above 0
    (a lead time of 0 means the ERP holds none and never counts),
    with ``bridge_basis`` ``candidate_lead_time``, else — while the
    entry has a terms candidate at all — the largest ``inputs.lt``
    of the recommendations read's rows of the item that is a number
    above 0, rounded up to a whole number of days, with ``bridge_
    basis`` ``item_lead_time``, else the whole-number part of the
    stored ``second_source.qualification_days`` (the registry default
    when nothing is stored) with ``bridge_basis``
    ``qualification_days``;
  * ``bridge_units`` — ``daily_demand`` times ``bridge_days``,
    rounded up to a whole number, null when ``daily_demand`` is
    null;
  * ``on_hand_units`` — the sum of the ``on_hand_units`` of those
    rows, 0 when there is none (null when the recommendations read
    raised);
  * ``bridge_short_units`` — ``bridge_units`` minus
    ``on_hand_units``, never below 0, null when ``bridge_units`` is
    null; and
  * ``bridge_arithmetic`` — the sentence
    ``<daily_demand> a day x <bridge_days> days = <bridge_units>
    units; on hand <on_hand_units>, short <bridge_short_units>``,
    null when ``bridge_units`` is null.

When one supplier holds several rows for the item, the candidate is
the row marked ``is_default``, then the one with the shortest
``lead_time_days``, then the lowest ``source_system``.  Each terms
candidate carries ``supplier_code``; ``supplier_name`` — the name in
the suppliers table, else the risk entry's ``supplier_name``, else
null; ``source_system``, ``lead_time_days`` and ``min_order_qty``
from the chosen row; ``risk`` — that supplier's risk entry's
``risk``, null when the rating has no entry for it; ``basis``
``terms``; and ``similar_items`` null.

A similar candidate is a supplier with at least one terms row — in
the one terms read — for a different item whose code starts with the
same first N characters as the entry's ``item_code``, N the
whole-number part of the stored ``second_source.similar_prefix_
length`` (the registry default when nothing is stored); with N at 0,
or an item code shorter than N, the item gains no similar
candidates.  The entry's own supplier and the entry's terms
candidates are never similar candidates.  It carries
``supplier_code``; ``supplier_name`` — found the same way as for a
terms candidate; ``risk`` — that supplier's risk entry's ``risk``,
null when unrated; ``basis`` ``similar``; ``similar_items`` — the
number of distinct such item codes; and ``source_system``,
``lead_time_days`` and ``min_order_qty`` null.

Terms candidates are ordered ``risk`` ``low``, then ``medium``, then
``high``, then null, then ``lead_time_days`` ascending with null
last, then ``supplier_code``; the similar candidates follow them, at
most three, ranked ``risk`` ``low``, then ``medium``, then ``high``,
then null, then ``similar_items`` descending, then ``supplier_
code``.  Entries are ordered ``supplier_risk`` ``high``, then
``medium``, then ``low``, then ``item_code``, then ``supplier_
code``.

The answer is ``{"as_of", "items"}`` with ``as_of`` the risk
answer's ``as_of``.
"""

from __future__ import annotations

import math
import numbers

from app.recommend.reorder import recommendations_for_all
from app.recommend.supplier_risk import supplier_risk
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.settings_postgres_repository import (
    SettingsPostgresRepository,
)
from app.repositories.supplier_terms_postgres_repository import (
    SupplierTermsPostgresRepository,
)
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)
from app.settings.registry import get_setting

# How many leading characters of an item code make two items similar.
SIMILAR_PREFIX_LENGTH_SETTING = "second_source.similar_prefix_length"

# The days to qualify a new supplier and receive a first delivery:
# the bridge days of an item with no supplier on terms to switch to.
QUALIFICATION_DAYS_SETTING = "second_source.qualification_days"

# Entry order: high first, then medium, then low.  A risk level the
# rating never answers sorts after low, never crashing the sort.
ENTRY_RISK_RANK = {"high": 0, "medium": 1, "low": 2}

# Candidate order: low first, then medium, then high, then unrated
# (null or a level the rating never answers).
CANDIDATE_RISK_RANK = {"low": 0, "medium": 1, "high": 2}

_UNRANKED_RISK = 3


def _risk_rank(risk) -> int:
    """The candidate's risk rank: low, medium, high, then null."""
    if risk is None:
        return _UNRANKED_RISK
    return CANDIDATE_RISK_RANK.get(risk, _UNRANKED_RISK)


def _chosen_row(rows: list) -> dict:
    """One supplier's terms row for the item: the row marked
    ``is_default``, then the one with the shortest ``lead_time_days``
    (null last), then the lowest ``source_system``."""
    return min(
        rows,
        key=lambda row: (
            not bool(row.get("is_default")),
            row.get("lead_time_days") is None,
            row.get("lead_time_days") or 0,
            row.get("source_system") is None,
            row.get("source_system") or "",
        ),
    )


def _number(value: object) -> float | None:
    """A usable number as a finite float — an int, float or Decimal,
    never a bool, never non-finite — None for anything else."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _whole_text(value: object) -> str:
    """A number's text, a whole number's trailing ``.0`` dropped."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return str(value)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return str(value)


def _stored_second_source_settings() -> dict:
    """The stored rows of the second_source settings, in one read:
    key to row, a key absent while nothing is stored for it."""
    stored: dict = {}
    wanted = (SIMILAR_PREFIX_LENGTH_SETTING, QUALIFICATION_DAYS_SETTING)
    for row in SettingsPostgresRepository().stored_rows():
        if isinstance(row, dict) and row.get("key") in wanted:
            stored.setdefault(row.get("key"), row)
    return stored


def _stored_setting_value(stored: dict, key: str) -> float | None:
    """The stored value of *key* from the one read: a number within
    the setting's bounds, or None when nothing is stored or the
    stored value is not a number the setting can hold (not a number,
    or outside its bounds)."""
    setting = get_setting(key)
    if setting is None:
        return None
    row = stored.get(key)
    value = _number(row.get("value")) if isinstance(row, dict) else None
    if value is None or not (setting.min <= value <= setting.max):
        return None
    return value


def _similar_prefix_length(stored: dict) -> int:
    """The stored ``second_source.similar_prefix_length``, its whole-
    number part: the registry default when nothing is stored or the
    stored value is not a number the setting can hold."""
    setting = get_setting(SIMILAR_PREFIX_LENGTH_SETTING)
    if setting is None:
        return 0
    value = _stored_setting_value(stored, SIMILAR_PREFIX_LENGTH_SETTING)
    return int(setting.default if value is None else value)


def _qualification_days(stored: dict) -> int:
    """The stored ``second_source.qualification_days``, its whole-
    number part: the registry default when nothing is stored or the
    stored value is not a number the setting can hold."""
    setting = get_setting(QUALIFICATION_DAYS_SETTING)
    if setting is None:
        return 0
    value = _stored_setting_value(stored, QUALIFICATION_DAYS_SETTING)
    return int(setting.default if value is None else value)


def _all_recommendations() -> list | None:
    """``recommendations_for_all`` once per call, without an as-of:
    None when the read raises, the entries then answering without a
    bridge."""
    try:
        rows = recommendations_for_all()
    except Exception:
        return None
    return rows if isinstance(rows, list) else []


def _demand_by_item(recommendations: list | None) -> dict:
    """Per item code, from the one recommendations read: the sum of
    the rows' ``inputs.d`` (the rows whose ``inputs.d`` is a number),
    rounded to 2 places (the item absent when no such row), and the
    sum of those rows' ``on_hand_units`` (0 when there is none)."""
    daily: dict = {}
    on_hand: dict = {}
    for row in recommendations or []:
        if not isinstance(row, dict):
            continue
        item_code = row.get("item_code")
        if not isinstance(item_code, str) or not item_code:
            continue
        inputs = row.get("inputs")
        d = _number(inputs.get("d")) if isinstance(inputs, dict) else None
        if d is None:
            continue
        daily[item_code] = daily.get(item_code, 0.0) + d
        held = _number(row.get("on_hand_units"))
        if held is not None:
            on_hand[item_code] = on_hand.get(item_code, 0) + held
    return {
        code: (round(total, 2), on_hand.get(code, 0)) for code, total in daily.items()
    }


def _item_lead_time_by_item(recommendations: list | None) -> dict:
    """Per item code, from the one recommendations read: the largest
    ``inputs.lt`` of the item's rows that is a number above 0 (the
    item absent when no such row)."""
    lead_times: dict = {}
    for row in recommendations or []:
        if not isinstance(row, dict):
            continue
        item_code = row.get("item_code")
        if not isinstance(item_code, str) or not item_code:
            continue
        inputs = row.get("inputs")
        lead = _number(inputs.get("lt")) if isinstance(inputs, dict) else None
        if lead is None or lead <= 0:
            continue
        if lead > lead_times.get(item_code, 0.0):
            lead_times[item_code] = lead
    return lead_times


def _similar_candidates(
    item_code: str,
    own_code,
    prefix_length: int,
    rows_by_item: dict,
    risk_by_code: dict,
    supplier_name_by_code: dict,
    similar_items_by_item: dict,
    terms_codes: set,
) -> list:
    """The entry's similar candidates: the suppliers with at least one
    terms row — in the one terms read — for a different item whose
    code starts with the same first ``prefix_length`` characters as
    the entry's item code, the entry's own supplier and the entry's
    terms candidates never among them.  At most three, ranked
    ``risk`` ``low``, then ``medium``, then ``high``, then null, then
    ``similar_items`` descending, then ``supplier_code``.

    ``similar_items_by_item`` caches the per-item scan of the read.
    """
    if prefix_length <= 0 or len(item_code) < prefix_length:
        return []
    prefix = item_code[:prefix_length]
    if item_code not in similar_items_by_item:
        items_by_supplier: dict = {}
        for other_code, suppliers_of_code in rows_by_item.items():
            if (
                not isinstance(other_code, str)
                or other_code == item_code
                or not other_code.startswith(prefix)
            ):
                continue
            for supplier_code in suppliers_of_code:
                items_by_supplier.setdefault(supplier_code, set()).add(other_code)
        similar_items_by_item[item_code] = items_by_supplier
    similar = []
    for supplier_code, codes in similar_items_by_item[item_code].items():
        if supplier_code == own_code or supplier_code in terms_codes:
            continue
        risk_entry_of_code = risk_by_code.get(supplier_code)
        risk = (
            risk_entry_of_code.get("risk")
            if isinstance(risk_entry_of_code, dict)
            else None
        )
        # The name: the suppliers table, else the risk
        # entry's supplier_name, else null.
        name = supplier_name_by_code.get(supplier_code)
        if name is None and isinstance(risk_entry_of_code, dict):
            name = risk_entry_of_code.get("supplier_name")
        similar.append(
            {
                "supplier_code": supplier_code,
                "supplier_name": name,
                "source_system": None,
                "lead_time_days": None,
                "min_order_qty": None,
                "risk": risk,
                "basis": "similar",
                "similar_items": len(codes),
            }
        )
    # low, medium, high, then null; the similar items descending;
    # then the supplier code.
    similar.sort(
        key=lambda candidate: (
            _risk_rank(candidate["risk"]),
            -candidate["similar_items"],
            candidate["supplier_code"],
        )
    )
    return similar[:3]


def second_sources() -> dict:
    """The second-source candidates of every sole-source item, with
    each item's bridge stock."""
    risk_answer = supplier_risk()

    stored = _stored_second_source_settings()
    prefix_length = _similar_prefix_length(stored)
    qualification_days = _qualification_days(stored)

    recommendations = _all_recommendations()

    terms_rows = SupplierTermsPostgresRepository().list()
    items = InventoryItemPostgresRepository().list_all()
    suppliers = SupplierPostgresRepository().list_all()

    # The per-item bridge demand of that one recommendations read.
    demand_by_item = _demand_by_item(recommendations)
    # The per-item lead time of that one recommendations read.
    item_lead_by_item = _item_lead_time_by_item(recommendations)

    risk_by_code: dict = {}
    for entry in risk_answer.get("suppliers") or []:
        if not isinstance(entry, dict):
            continue
        code = entry.get("supplier_code")
        if code is not None:
            risk_by_code[code] = entry

    # The suppliers table names: supplier_code -> name, the first row
    # of a duplicate code winning (the read is ordered by
    # vendor_code, a stable, deterministic choice).
    supplier_name_by_code: dict = {}
    for supplier in suppliers:
        if not isinstance(supplier, dict):
            continue
        code = supplier.get("supplier_code")
        if code is None or code in supplier_name_by_code:
            continue
        supplier_name_by_code[code] = supplier.get("name")

    description_by_code: dict = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        code = item.get("item_code")
        if code is None or code in description_by_code:
            continue
        description_by_code[code] = item.get("description")

    # The similar candidates' scan of that one read, cached per item
    # code.
    similar_items_by_item: dict = {}

    # The terms rows per item, per supplier, from that one read: a row
    # without an item code or a supplier code is no candidate row.
    rows_by_item: dict = {}
    for row in terms_rows:
        item_code = row.get("item_code")
        supplier_code = row.get("supplier_code")
        if item_code is None or supplier_code is None:
            continue
        rows_by_item.setdefault(item_code, {}).setdefault(supplier_code, []).append(row)

    entries = []
    for risk_entry in risk_answer.get("suppliers") or []:
        if not isinstance(risk_entry, dict):
            continue
        own_code = risk_entry.get("supplier_code")
        for item_code in risk_entry.get("sole_source_item_codes") or []:
            if not isinstance(item_code, str) or not item_code:
                continue
            candidates = []
            for supplier_code, rows in rows_by_item.get(item_code, {}).items():
                # A terms row naming the entry's own supplier is
                # never a candidate.
                if supplier_code == own_code:
                    continue
                row = _chosen_row(rows)
                risk_entry_of_code = risk_by_code.get(supplier_code)
                risk = (
                    risk_entry_of_code.get("risk")
                    if isinstance(risk_entry_of_code, dict)
                    else None
                )
                # The name: the suppliers table, else the risk
                # entry's supplier_name, else null.
                name = supplier_name_by_code.get(supplier_code)
                if name is None and isinstance(risk_entry_of_code, dict):
                    name = risk_entry_of_code.get("supplier_name")
                candidates.append(
                    {
                        "supplier_code": supplier_code,
                        "supplier_name": name,
                        "source_system": row.get("source_system"),
                        "lead_time_days": row.get("lead_time_days"),
                        "min_order_qty": row.get("min_order_qty"),
                        "risk": risk,
                        "basis": "terms",
                        "similar_items": None,
                    }
                )
            # low, medium, high, then null; the lead time ascending
            # with null last; then the supplier code.
            candidates.sort(
                key=lambda candidate: (
                    _risk_rank(candidate["risk"]),
                    candidate["lead_time_days"] is None,
                    candidate["lead_time_days"] or 0,
                    candidate["supplier_code"],
                )
            )
            # The similar candidates follow the terms candidates,
            # which keep their order.
            candidates.extend(
                _similar_candidates(
                    item_code,
                    own_code,
                    prefix_length,
                    rows_by_item,
                    risk_by_code,
                    supplier_name_by_code,
                    similar_items_by_item,
                    {c["supplier_code"] for c in candidates},
                )
            )
            # The bridge days: the first terms candidate with a lead
            # time above 0 — a lead time of 0 means the ERP holds
            # none, so it never counts — else, while the entry has a
            # terms candidate at all, the item's own lead time from
            # that one recommendations read — the largest
            # inputs.lt above 0, rounded up — else the
            # qualification days.
            bridge_days = None
            bridge_basis = "qualification_days"
            for candidate in candidates:
                if candidate["basis"] != "terms":
                    continue
                lead = _number(candidate["lead_time_days"])
                if lead is not None and lead > 0:
                    bridge_days = candidate["lead_time_days"]
                    bridge_basis = "candidate_lead_time"
                    break
            if bridge_days is None:
                item_lead = item_lead_by_item.get(item_code)
                if item_lead is not None and any(
                    c["basis"] == "terms" for c in candidates
                ):
                    bridge_days = math.ceil(item_lead)
                    bridge_basis = "item_lead_time"
            if bridge_days is None:
                bridge_days = qualification_days
            # The bridge stock of that one recommendations read: the
            # bridge fields are null when the read raised; a null
            # daily_demand keeps on_hand_units (0 when there is no
            # row) and nulls the rest.
            if recommendations is None:
                daily_demand = None
                on_hand_units = None
                bridge_units = None
                bridge_short_units = None
                bridge_arithmetic = None
            else:
                pair = demand_by_item.get(item_code)
                daily_demand, on_hand_units = pair if pair is not None else (None, 0)
                if daily_demand is None:
                    bridge_units = None
                    bridge_short_units = None
                    bridge_arithmetic = None
                else:
                    days = _number(bridge_days)
                    product = daily_demand * days if days is not None else None
                    if days is None or not math.isfinite(product):
                        bridge_units = None
                        bridge_short_units = None
                        bridge_arithmetic = None
                    else:
                        bridge_units = math.ceil(product)
                        bridge_short_units = max(bridge_units - on_hand_units, 0)
                        bridge_arithmetic = (
                            f"{daily_demand} a day x "
                            f"{_whole_text(bridge_days)} days = "
                            f"{bridge_units} units; on hand "
                            f"{_whole_text(on_hand_units)}, short "
                            f"{_whole_text(bridge_short_units)}"
                        )
            entries.append(
                {
                    "item_code": item_code,
                    "description": description_by_code.get(item_code),
                    "supplier_code": own_code,
                    "supplier_name": risk_entry.get("supplier_name"),
                    "late": risk_entry.get("late"),
                    "variable": risk_entry.get("variable"),
                    "supplier_risk": risk_entry.get("risk"),
                    "candidates": candidates,
                    "daily_demand": daily_demand,
                    "bridge_days": bridge_days,
                    "bridge_basis": bridge_basis,
                    "bridge_units": bridge_units,
                    "on_hand_units": on_hand_units,
                    "bridge_short_units": bridge_short_units,
                    "bridge_arithmetic": bridge_arithmetic,
                }
            )

    # high, medium, low; then the item code, then the supplier code.
    entries.sort(
        key=lambda entry: (
            ENTRY_RISK_RANK.get(entry["supplier_risk"], _UNRANKED_RISK),
            entry["item_code"],
            entry["supplier_code"] or "",
        )
    )
    return {"as_of": risk_answer.get("as_of"), "items": entries}
