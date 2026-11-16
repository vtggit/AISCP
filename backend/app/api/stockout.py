"""Read-only API for stockout risk per item and site."""

from contextlib import nullcontext
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, PrivateAttr, model_serializer, model_validator

from app.api.stockout_decisions import StockoutDecision
from app.api.supply_plan import build_supply_plan, build_transfer_suggestions
from app.db.connection import get_cursor
from app.recommend.expedite import expedite_list
from app.recommend.export import stockout_csv
from app.recommend.reorder import recommendations_memo
from app.recommend.stockout import stockout_ranked
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.settings_postgres_repository import SettingsPostgresRepository
from app.repositories.stockout_decisions_postgres_repository import (
    StockoutDecisionPostgresRepository,
)
from app.settings.registry import get_setting
from app.stats.demand import default_as_of

router = APIRouter(prefix="/api/stockout", tags=["stockout"])

_repo = StockoutDecisionPostgresRepository()
_settings_repo = SettingsPostgresRepository()

# The 7-day horizon the remedy's order plan is built at: the default
# of GET /api/supply-plan, the Order plan screen and the exceptions
# inbox.
_PLAN_HORIZON_DAYS = 7

# The system setting that names the order a row's remedy is looked
# for, read at call time like every other stored system setting.
REMEDY_ORDER_SETTING = "decisions.remedy_order"


class StockoutRow(BaseModel):
    """One item-site's stockout risk row.

    ``open_supply_units`` is the open purchase supply due within the
    lead time and ``open_supply_due_by`` the latest due date counted in
    it (null when nothing counts). ``unit_cost`` and ``money_at_risk``
    are null when the item-site has no unit cost, and
    ``days_of_cover`` / ``projected_stockout_on`` are null when there is
    no demand to cover.  ``item_description`` is the item's
    description from the ``inventory_items`` read model, looked up
    by ``item_code`` (null when the item is not in the read model).
    """

    rank: int
    category: Literal["likely", "at_risk"]
    item_code: str
    site_code: str
    on_hand_units: float
    open_supply_units: float
    open_supply_due_by: date | None = None
    unit_cost: float | None = None
    lead_time_days: float
    lead_time_demand: float
    p_stockout: float
    days_of_cover: float | None = None
    projected_stockout_on: date | None = None
    recommended_reorder_point_units: int | None = None
    expected_units_short: float
    money_at_risk: float | None = None
    as_of: date
    notes: list[str]
    arithmetic: str
    item_description: str | None = None


class StockoutRemedy(BaseModel):
    """The one remedy proposed for a stockout row.

    ``kind`` is the remedy's kind — one of the stockout decision's own
    action values (``expedite``, ``transfer`` or ``raise_order``).
    The other keys carry that kind's candidate for the row's
    item-site: the expedite line's ``order_type``, ``order_nbr``,
    ``line_nbr``, ``expected_on``, ``supplier_code`` and
    ``supplier_name`` (``units`` the line's ``open_units``); the
    transfer suggestion's ``from_site`` (``units`` its ``quantity``);
    or the order plan line's ``order_by`` and its group's
    ``supplier_code`` and ``name`` (``units`` the line's ``quantity``).
    Every key that does not apply to the kind is null, and ``reason``
    is the one line of text for why the remedy fits the row.
    """

    kind: Literal["expedite", "transfer", "raise_order"]
    order_type: str | None = None
    order_nbr: str | None = None
    line_nbr: str | None = None
    expected_on: date | None = None
    from_site: str | None = None
    supplier_code: str | None = None
    supplier_name: str | None = None
    order_by: date | None = None
    units: int | float | None = None
    reason: str


def _filtered(
    rows: list[dict], category: Literal["likely", "at_risk"] | None
) -> list[dict]:
    """The rows matching the filter."""
    if category is not None:
        rows = [row for row in rows if row["category"] == category]
    return rows


def _ranked(as_of: date | None) -> list[dict]:
    try:
        return stockout_ranked(as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class StockoutDecisionRow(StockoutRow):
    """A stockout row enriched with the item-site's current decision.

    ``decision`` is the item-site's current decision from
    ``stockout_decisions`` (the latest row by ``decided_at``, ``id``
    breaking ties — the same row ``GET /api/stockout/decisions``
    reports as current) with every stored column, or null when the
    item-site has none.  ``decision_lapsed`` is true only for a current
    dismissal that the row has outgrown: the row's ``p_stockout``
    exceeds the decision's ``p_stockout`` by at least 0.10.  A snooze
    never lapses, and neither does anything else (accepted decisions,
    or no decision at all).
    """

    decision: StockoutDecision | None = None
    decision_lapsed: bool = False
    remedy: StockoutRemedy | None = None
    # The route's include_remedy parameter for this row: read off the
    # answered row's dict (a key the validation ignores) by the
    # validator below and kept as private state, so it is part of
    # neither the model's fields nor its schema, let alone the answer.
    _include_remedy: bool = PrivateAttr(default=False)

    @model_validator(mode="wrap")
    def _remember_include_remedy(cls, value, handler):
        """Remember the route's include_remedy parameter from the
        answered row's dict, so the serializer can drop the remedy key
        while it is false."""
        if isinstance(value, dict):
            include_remedy = value.get("include_remedy", False)
        else:
            include_remedy = getattr(value, "_include_remedy", False)
        row = handler(value)
        row._include_remedy = bool(include_remedy)
        return row

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        """The row's fields, without ``remedy`` while the route's
        ``include_remedy`` parameter is false (the key stays out of
        the answer, exactly as before the remedy existed)."""
        data = handler(self)
        if not self._include_remedy:
            data.pop("remedy", None)
        return data


_LAPSE_THRESHOLD = Decimal("0.10")


def _decision_lapsed(row: dict, decision: dict) -> bool:
    """Whether a dismissal has lapsed against the row it decided on.

    A dismissal lapses when the row's ``p_stockout`` exceeds the
    decision's ``p_stockout`` by at least 0.10.  The comparison is
    exact decimal arithmetic, because a plain float subtraction can
    land just under the threshold although the decimal difference is
    exactly 0.10 (``0.6915 - 0.5915`` is ``0.09999999999999998``):
    the boundary lapses.  Only dismissals lapse: an accepted decision
    never does, and a snoozed row's visibility depends only on its
    ``snooze_until``, so the lapse threshold never applies to it.
    """
    if decision["decision"] != "dismissed":
        return False
    stored_p_stockout = Decimal(str(decision["p_stockout"]))
    return Decimal(str(row["p_stockout"])) - stored_p_stockout >= _LAPSE_THRESHOLD


def _decision_hides_row(decision: dict | None, lapsed: bool) -> bool:
    """Whether the default list (``include_decided`` false) leaves the
    row out.  Only a not-yet-lapsed dismissal and a snooze whose
    ``snooze_until`` is after today (the server's UTC date) hide their
    row; accepted, lapsed, undecided, and snoozed-until-today-or-earlier
    rows stay listed."""
    if decision is None:
        return False
    kind = decision["decision"]
    if kind == "dismissed":
        return not lapsed
    if kind == "snoozed":
        snooze_until = decision["snooze_until"]
        return snooze_until is not None and snooze_until > datetime.now(UTC).date()
    return False


def _enriched_rows(rows: list[dict]) -> list[tuple[dict, bool]]:
    """Attach each row's current decision (one query per request).

    Returns ``(row, hidden)`` pairs: the row dict gains ``decision``
    (every stored column, or null) and ``decision_lapsed``, and
    ``hidden`` says whether the default list leaves the row out.  The
    current decisions are read once for the whole request, never one
    query per row, and every item-site's current decision is read even
    when it sits past the first page of
    ``GET /api/stockout/decisions``.
    """
    current = {
        (decision["item_code"], decision["site_code"]): decision
        for decision in _repo.current_all()
    }
    pairs: list[tuple[dict, bool]] = []
    for row in rows:
        decision = current.get((row["item_code"], row["site_code"]))
        lapsed = _decision_lapsed(row, decision) if decision is not None else False
        pairs.append(
            (
                {
                    **row,
                    "decision": (
                        StockoutDecision.model_validate(decision)
                        if decision is not None
                        else None
                    ),
                    "decision_lapsed": lapsed,
                },
                _decision_hides_row(decision, lapsed),
            )
        )
    return pairs


def _item_descriptions() -> dict[str, str | None]:
    """One inventory_items read per request, keyed by item_code.

    The value is the item's stored description (null when it is
    null), so a row's item missing from the read model looks up to
    null rather than failing the request.
    """
    return {
        item["item_code"]: item.get("description")
        for item in InventoryItemPostgresRepository().list_all()
    }


def _remedy_order() -> tuple[str, ...]:
    """The effective ``decisions.remedy_order``, its kinds in order.

    The stored system value when it is one of the setting's allowed
    values, else the registry default — the way
    ``replenishment.demand_basis`` is read: a value written out of
    band that the setting cannot hold never names an order.
    """
    setting = get_setting(REMEDY_ORDER_SETTING)
    if setting is None:
        value = "expedite,transfer,raise_order"
    else:
        stored = _settings_repo.get(setting.scope, "", setting.key)
        if stored is not None and stored["value"] in setting.allowed:
            value = stored["value"]
        else:
            value = setting.default
    return tuple(value.split(","))


def _remedy_reads(page_as_of: date) -> dict[str, tuple[dict | None, bool]]:
    """The three remedy reads, each exactly once.

    ``expedite_list`` with no as-of, so it keeps its own default
    (today), the way the exceptions inbox reads it; the transfer
    suggestions and the 7-day order plan at the rows' as-of.  A read
    that raises is recorded as failed (its payload None) so the route
    still answers 200 with every row it returns.
    """
    reads: dict[str, tuple[dict | None, bool]] = {}
    for kind, read in (
        ("expedite", lambda: expedite_list()),
        ("transfer", lambda: build_transfer_suggestions(page_as_of)),
        ("raise_order", lambda: build_supply_plan(_PLAN_HORIZON_DAYS, page_as_of)),
    ):
        try:
            reads[kind] = (read(), True)
        except Exception:
            reads[kind] = (None, False)
    return reads


def _expedite_remedy(row: dict, answer: dict) -> dict | None:
    """The row's expedite candidate: the first line of the expedite
    list, in the list's own order, of the row's item-site whose order
    number and line number are both present."""
    for line in answer["lines"]:
        if (
            line["item_code"] != row["item_code"]
            or line["site_code"] != row["site_code"]
        ):
            continue
        if line["order_nbr"] is None or line["line_nbr"] is None:
            continue
        if "late" in line["reasons"]:
            reason = (
                f"The open order was expected {line['expected_on'].isoformat()} "
                "and is overdue."
            )
        else:
            reason = (
                f"The open order is expected {line['expected_on'].isoformat()}, "
                f"after the projected stockout on "
                f"{line['projected_stockout_on'].isoformat()}."
            )
        return {
            "kind": "expedite",
            "order_type": line["order_type"],
            "order_nbr": line["order_nbr"],
            "line_nbr": line["line_nbr"],
            "expected_on": line["expected_on"],
            "from_site": None,
            "supplier_code": line["supplier_code"],
            "supplier_name": line.get("supplier_name"),
            "order_by": None,
            "units": line["open_units"],
            "reason": reason,
        }
    return None


def _transfer_remedy(row: dict, answer: dict) -> dict | None:
    """The row's transfer candidate: the first of the transfer
    suggestions, in their own order, of the row's item into the row's
    site."""
    for suggestion in answer["suggestions"]:
        if (
            suggestion["item_code"] != row["item_code"]
            or suggestion["to_site"] != row["site_code"]
        ):
            continue
        return {
            "kind": "transfer",
            "order_type": None,
            "order_nbr": None,
            "line_nbr": None,
            "expected_on": None,
            "from_site": suggestion["from_site"],
            "supplier_code": None,
            "supplier_name": None,
            "order_by": None,
            "units": suggestion["quantity"],
            "reason": (
                f"No open order to expedite; site {suggestion['from_site']} holds "
                f"{suggestion['from_site_surplus']} units beyond its reorder point "
                "plus one order quantity, and this site is "
                f"{suggestion['to_site_need']} units below its reorder point."
            ),
        }
    return None


def _raise_order_remedy(row: dict, answer: dict) -> dict | None:
    """The row's raise-order candidate: the order plan's line of the
    row's item-site, its group's supplier (both null for the group
    without a supplier)."""
    for group in answer["suppliers"]:
        for line in group["lines"]:
            if (
                line["item_code"] != row["item_code"]
                or line["site_code"] != row["site_code"]
            ):
                continue
            return {
                "kind": "raise_order",
                "order_type": None,
                "order_nbr": None,
                "line_nbr": None,
                "expected_on": None,
                "from_site": None,
                "supplier_code": group["supplier_code"],
                "supplier_name": group["name"],
                "order_by": line["order_by"],
                "units": line["quantity"],
                "reason": (
                    "No open order to expedite and no transfer suggested for "
                    "this site."
                ),
            }
    return None


_REMEDY_KINDS = {
    "expedite": _expedite_remedy,
    "transfer": _transfer_remedy,
    "raise_order": _raise_order_remedy,
}


def _row_remedy(row: dict, order: tuple[str, ...], reads: dict) -> dict | None:
    """The row's remedy: the three kinds tried in the order the
    effective ``decisions.remedy_order`` names, the first that holds a
    candidate for the row's item-site proposed.  A kind whose read
    raised stops the search with no remedy, so a remedy later in the
    order is never proposed because an earlier one could not be
    checked; a row an earlier kind already served keeps its remedy.
    """
    for kind in order:
        answer, ok = reads[kind]
        if not ok:
            return None
        remedy = _REMEDY_KINDS[kind](row, answer)
        if remedy is not None:
            return remedy
    return None


@router.get("", response_model=list[StockoutDecisionRow])
def list_stockout(
    limit: int = Query(default=20, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    category: Literal["likely", "at_risk"] | None = Query(default=None),
    as_of: date | None = Query(default=None),
    include_decided: bool = Query(default=False),
    include_remedy: bool = Query(default=False),
) -> list[dict]:
    """List stockout risk ranked by stockout probability, likely first.

    Every row carries its current stockout decision (``decision``, or
    null) and ``decision_lapsed``.  While ``include_decided`` is false,
    a row whose current decision is a not-yet-lapsed dismissal, or a
    snooze whose ``snooze_until`` is after today (the server's UTC
    date), is left out of the list; accepted, lapsed, undecided and
    expired-snooze rows stay listed, and with ``include_decided`` true
    every row is listed.  Every row also carries
    ``item_description``, the item's description from the
    ``inventory_items`` read model (null when the item is not in
    it), from one read per request.  Rows are left out after the
    ``category`` filter and before ``offset`` and ``limit`` are
    applied, and every row keeps the ``rank`` that ``stockout_ranked``
    gave it.

    While ``include_remedy`` is false (the default) the answer is
    exactly that, key for key: no row carries a ``remedy`` key and the
    expedite list, the transfer suggestions and the order plan are not
    read.  While it is true and at least one row is on the page, the
    expedite list (with its own default as-of) and the transfer
    suggestions and the 7-day order plan (both at the rows' as-of) are
    read once each, the ranking and the three reads inside one
    ``recommendations_memo`` so the recommendations are computed at
    most once per as-of; every returned row then carries
    ``remedy``, a ``StockoutRemedy`` proposed by the first kind that
    holds a candidate for the row's item-site — the kinds tried in the
    order the effective ``decisions.remedy_order`` system setting
    names, expedite, transfer and raise an order by default — or null
    when no kind does.  A kind whose read raised stops the search at
    that kind, so a remedy later in the order is never proposed
    because an earlier one could not be checked, and a read that
    raises never fails the list: the route still answers 200 with
    every row it returns.  With no row on the page none of the three
    is read.
    """
    memo = recommendations_memo() if include_remedy else nullcontext()
    with memo:
        rows = _filtered(_ranked(as_of), category)
        enriched = _enriched_rows(rows)
        descriptions = _item_descriptions()
        if include_decided:
            paged = [row for row, _hidden in enriched][offset : offset + limit]
        else:
            paged = [row for row, hidden in enriched if not hidden][
                offset : offset + limit
            ]
        answered = [
            {
                **row,
                "item_description": descriptions.get(row["item_code"]),
                "include_remedy": include_remedy,
            }
            for row in paged
        ]
        if include_remedy and answered:
            reads = _remedy_reads(answered[0]["as_of"])
            order = _remedy_order()
            for row in answered:
                row["remedy"] = _row_remedy(row, order, reads)
    return answered


@router.get("/export.csv")
def export_stockout(
    category: Literal["likely", "at_risk"] | None = Query(default=None),
    as_of: date | None = Query(default=None),
) -> Response:
    """Export every matching stockout row as a CSV attachment (no limit)."""
    rows = _filtered(_ranked(as_of), category)
    if rows:
        export_as_of = rows[0]["as_of"]
    elif as_of is not None:
        export_as_of = as_of
    else:
        with get_cursor() as cur:
            export_as_of = default_as_of(cur)
    return Response(
        content=stockout_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="stockout-{export_as_of.isoformat()}.csv"'
        },
    )
