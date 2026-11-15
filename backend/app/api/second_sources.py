"""Read-only API for the second-source candidates of sole-source items.

``GET /api/second-sources`` answers ``app.recommend.second_source.
second_sources``: one entry per item code of each risk entry's
``sole_source_item_codes`` (``supplier_risk`` the risk entry's
``risk``), and one candidate per other supplier with a terms row for
the item, followed by at most three similar candidates — suppliers
with a terms row for a different item whose code starts with the same
first N characters as the entry's item code, N the whole-number part
of the stored ``second_source.similar_prefix_length`` setting (the
registry default when nothing is stored).  When one supplier holds
several rows for the item, the candidate is the row marked
``is_default``, then the one with the shortest ``lead_time_days``,
then the lowest ``source_system``; a terms row naming the entry's own
supplier is never a candidate, and the entry's own supplier and its
terms candidates are never similar candidates.  Terms candidates are
ordered ``risk`` ``low``, then ``medium``, then ``high``, then null,
then ``lead_time_days`` ascending with null last, then
``supplier_code``; the similar candidates follow them, ranked
``risk`` ``low``, then ``medium``, then ``high``, then null, then
``similar_items`` descending, then ``supplier_code``.  Entries are
ordered ``supplier_risk`` ``high``, then ``medium``, then ``low``,
then ``item_code``, then ``supplier_code``.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.recommend.second_source import second_sources

router = APIRouter(prefix="/api", tags=["second-sources"])


class SecondSourceCandidate(BaseModel):
    """One other supplier's terms row, or a similar-item supplier, for
    a sole-source item."""

    supplier_code: str
    supplier_name: str | None = None
    source_system: str | None = None
    lead_time_days: float | None = None
    min_order_qty: float | None = None
    risk: Literal["high", "medium", "low"] | None = None
    basis: Literal["terms", "similar"]
    similar_items: int | None = None


class SecondSourceEntry(BaseModel):
    """One sole-source item of one risk entry, with its candidates and
    its bridge stock."""

    item_code: str
    description: str | None = None
    supplier_code: str
    supplier_name: str | None = None
    late: bool
    variable: bool
    supplier_risk: Literal["high", "medium", "low"]
    candidates: list[SecondSourceCandidate] = []
    daily_demand: float | None = None
    bridge_days: float | None = None
    bridge_basis: Literal["candidate_lead_time", "item_lead_time", "qualification_days"]
    bridge_units: int | None = None
    on_hand_units: float | None = None
    bridge_short_units: float | None = None
    bridge_arithmetic: str | None = None


class SecondSourceResponse(BaseModel):
    """Second-source candidates for the sole-source items, as of the
    risk answer's as-of."""

    as_of: str
    items: list[SecondSourceEntry]


@router.get(
    "/second-sources",
    response_model=SecondSourceResponse,
)
def get_second_sources() -> dict:
    """Second-source candidates for the sole-source items.

    Open to any signed-in user behind the app's auth gate (401
    without a valid Bearer token).  ``second_sources`` reads the
    supplier risk rating once, the stored
    ``second_source.similar_prefix_length`` once, every supplier
    terms row once, every inventory item once and every supplier
    once, and writes nothing and calls no ERP.  One entry per item
    code of each risk entry's ``sole_source_item_codes``
    (``supplier_code``, ``supplier_name``, ``late``, ``variable`` and
    ``supplier_risk`` from the risk entry, ``description`` the item's
    inventory item description, null when it has none), and one
    candidate per other supplier with a terms row for the item: when
    one supplier holds several rows, the candidate is the row marked
    ``is_default``, then the one with the shortest ``lead_time_
    days``, then the lowest ``source_system``; a terms candidate
    carries ``supplier_name`` — the name in the suppliers table, else
    the risk entry's ``supplier_name``, else null — ``source_
    system``, ``lead_time_days`` and ``min_order_qty`` from the
    chosen row, ``risk`` that supplier's risk entry's ``risk``, null
    when the rating has no entry for it, ``basis`` ``terms`` and
    ``similar_items`` null.  They are followed by at most three
    similar candidates — suppliers with a terms row for a different
    item whose code starts with the same first N characters as the
    entry's item code, N the whole-number part of the stored
    ``second_source.similar_prefix_length`` (the registry default
    when nothing is stored; 0 turns similar suggestions off), the
    entry's own supplier and the entry's terms candidates never among
    them — carrying ``supplier_name`` found the same way, ``risk``
    null when unrated, ``basis`` ``similar``, ``similar_items`` the
    number of distinct such item codes, and null ``source_system``,
    ``lead_time_days`` and ``min_order_qty``, ranked ``risk``
    ``low``, then ``medium``, then ``high``, then null, then
    ``similar_items`` descending, then ``supplier_code``.  Entries
    are ordered ``supplier_risk`` ``high``, then ``medium``, then
    ``low``, then ``item_code``, then ``supplier_code``.

    Every entry also carries its bridge stock: ``daily_demand`` the
    sum of the ``inputs.d`` of ``recommendations_for_all``'s rows of
    the item whose ``inputs.d`` is a number, rounded to 2 places,
    null when there is no such row; ``bridge_days`` the
    ``lead_time_days`` of the first terms candidate with a lead time
    above 0 (a lead time of 0 means the ERP holds none and never
    counts), ``bridge_basis`` ``candidate_lead_time``, else — while
    the entry has a terms candidate at all — the largest
    ``inputs.lt`` of ``recommendations_for_all``'s rows of the item
    that is a number above 0, rounded up to a whole number of days,
    ``bridge_basis`` ``item_lead_time``, else the stored
    ``second_source.qualification_days`` (the registry default when
    nothing is stored), ``bridge_basis`` ``qualification_days``;
    ``bridge_units`` ``daily_demand`` times ``bridge_days``, rounded
    up to a whole number; ``on_hand_units`` the sum of those rows'
    ``on_hand_units``, 0 when there is none; ``bridge_short_units``
    ``bridge_units`` minus ``on_hand_units``, never below 0; and
    ``bridge_arithmetic`` the sentence ``<daily_demand> a day x
    <bridge_days> days = <bridge_units> units; on hand
    <on_hand_units>, short <bridge_short_units>`` — the bridge
    fields null, ``on_hand_units`` null too, when the recommendations
    read raised.
    """
    return second_sources()
