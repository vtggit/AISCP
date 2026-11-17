"""Read-only API for excess and dead stock per item and site."""

import math
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, PrivateAttr, model_serializer, model_validator

from app.api.excess_decisions import ExcessDecision
from app.db.connection import get_cursor
from app.recommend.excess import excess_ranked
from app.recommend.export import excess_csv
from app.repositories.excess_decisions_postgres_repository import (
    ExcessDecisionPostgresRepository,
)
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.stats.demand import default_as_of

router = APIRouter(prefix="/api/excess", tags=["excess"])

_repo = ExcessDecisionPostgresRepository()
_item_site_repo = ItemSitePostgresRepository()
_open_po_repo = OpenPurchaseLinePostgresRepository()


class ExcessRow(BaseModel):
    """One item-site's excess or dead stock row.

    ``dead_reason`` is null for ``excess`` rows, which have no
    ``dead_reason`` at all in the underlying ranked row.
    ``item_description`` is the item's description from the
    ``inventory_items`` read model, looked up by ``item_code``
    (null when the item is not in the read model).
    """

    rank: int
    category: Literal["dead", "excess"]
    dead_reason: Literal["never_moved", "stopped"] | None = None
    item_code: str
    site_code: str
    on_hand_units: float
    unit_cost: float | None = None
    last_demand_on: date | None = None
    demand_window_units: float
    months_of_cover: float | None = None
    justified_units: float
    excess_units: float
    money_tied_up: float | None = None
    as_of: date
    notes: list[str]
    arithmetic: str
    item_description: str | None = None


class ExcessRemedy(BaseModel):
    """The one remedy proposed for an excess row.

    ``kind`` is the remedy's kind — one of the excess decision's own
    action values (``stop_replenishing``, ``reduce`` or ``dispose``).
    ``on_order_units`` is the sum of the open purchase lines'
    ``open_units`` for the row's item-site that are finite numbers
    above 0 (0 when no line counts, whatever the lines' promised
    dates); ``current_reorder_point`` and ``current_max_qty`` the
    row's item-site's ``item_sites`` levels (each null when the
    item-site has no ``item_sites`` row or the value is null or not a
    finite number); ``reason`` is the one line of text for why the
    remedy fits the row.
    """

    kind: Literal["stop_replenishing", "reduce", "dispose"]
    on_order_units: float
    current_reorder_point: float | None = None
    current_max_qty: float | None = None
    reason: str


def _filtered(
    rows: list[dict],
    category: Literal["dead", "excess"] | None,
    dead_reason: Literal["never_moved", "stopped"] | None,
) -> list[dict]:
    """The rows matching the filters, applied in that order."""
    if category is not None:
        rows = [row for row in rows if row["category"] == category]
    if dead_reason is not None:
        rows = [row for row in rows if row.get("dead_reason") == dead_reason]
    return rows


def _ranked(as_of: date | None) -> list[dict]:
    try:
        return excess_ranked(as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class ExcessDecisionRow(ExcessRow):
    """An excess row enriched with the item-site's current decision.

    ``decision`` is the item-site's current decision from
    ``excess_decisions`` (the latest row by ``decided_at``, ``id``
    breaking ties — the same row ``GET /api/excess/decisions`` reports
    as current) with every stored column, or null when the item-site
    has none.  ``decision_lapsed`` is true only for a current
    dismissal that the row has outgrown: the row's ``excess_units``
    exceed the decision's ``excess_units`` by at least the larger of 1
    and 10% of the decision's ``excess_units``.  A snooze never lapses,
    and neither does anything else (accepted decisions, or no decision
    at all).
    """

    decision: ExcessDecision | None = None
    decision_lapsed: bool = False
    remedy: ExcessRemedy | None = None
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


_LAPSE_MINIMUM_UNITS = 1.0
_LAPSE_FRACTION = 0.10


def _decision_lapsed(row: dict, decision: dict) -> bool:
    """Whether a dismissal has lapsed against the row it decided on.

    A dismissal lapses when the row's ``excess_units`` exceed the
    decision's ``excess_units`` by at least the larger of 1 and 10% of
    the decision's ``excess_units``.  Only dismissals lapse: an
    accepted decision never does, and a snoozed row's visibility
    depends only on its ``snooze_until``, so the lapse threshold never
    applies to it.
    """
    if decision["decision"] != "dismissed":
        return False
    stored_excess_units = float(decision["excess_units"])
    threshold = max(_LAPSE_MINIMUM_UNITS, _LAPSE_FRACTION * stored_excess_units)
    return float(row["excess_units"]) - stored_excess_units >= threshold


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

    Returns ``(row, hidden)`` pairs: the row dict gains ``dead_reason``
    explicit (null for excess rows), ``decision`` (every stored column,
    or null) and ``decision_lapsed``, and ``hidden`` says whether the
    default list leaves the row out.  The current decisions are read
    once for the whole request, never one query per row, and every
    item-site's current decision is read even when it sits past the
    first page of ``GET /api/excess/decisions``.
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
                    "dead_reason": row.get("dead_reason"),
                    "decision": (
                        ExcessDecision.model_validate(decision)
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


def _finite_number(value) -> float | None:
    """The value as a finite float, or None when it is null, not
    numeric, or not a finite number (NaN or infinite)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _write_number(value: float) -> str:
    """A number written with at most two decimals: 42.25, 875.5,
    1500000 and 0."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _level_text(value: float | None) -> str:
    """A level written in a reason: its number, or ``none`` when it is
    null."""
    return "none" if value is None else _write_number(value)


def _remedy_reads() -> tuple[dict | None, dict | None]:
    """The two remedy reads, each exactly once.

    ``_item_site_repo.list_rows(10**9, 0)`` — every ``item_sites``
    row, keyed by (item_code, site_code) to the row's ``reorder_point``
    and ``max_qty`` (each None when it is null or not a finite
    number); and ``_open_po_repo.list_latest()`` — the open purchase
    lines of each ERP's newest sync, keyed by (item_code, site_code)
    to the sum of the lines' ``open_units`` that are finite numbers
    above 0 (0 when no line counts, whatever the lines' promised
    dates).  Both are the ERP sync's own current synced copies,
    whatever the rows' as-of.  A read that raises is recorded as
    failed (None) so the route still answers 200 with every row it
    returns.
    """
    try:
        levels = {
            (row.get("item_code"), row.get("site_code")): (
                _finite_number(row.get("reorder_point")),
                _finite_number(row.get("max_qty")),
            )
            for row in _item_site_repo.list_rows(10**9, 0)
        }
    except Exception:
        levels = None
    on_order: dict[tuple[str | None, str | None], float] | None = {}
    try:
        for line in _open_po_repo.list_latest():
            units = _finite_number(line.get("open_units"))
            if units is not None and units > 0:
                key = (line.get("item_code"), line.get("site_code"))
                on_order[key] = on_order.get(key, 0.0) + units
    except Exception:
        on_order = None
    return levels, on_order


def _above_level(value: float | None, justified_units: float) -> bool:
    """Whether a level is above the justified units: a null level is
    not above them, and a level equal to the justified units is not
    above them either."""
    return value is not None and value > justified_units


def _row_remedy(row: dict, levels: dict | None, on_order: dict | None) -> dict | None:
    """The remedy the first rule that applies proposes for the row.

    The rules, in order: stop replenishing while ``on_order_units`` is
    above 0; else stop replenishing while the ERP's reorder point or
    max quantity is above the row's ``justified_units`` (0 for a dead
    row, so any level above zero — a level equal to the justified
    units is not above it); else dispose a dead row whose
    ``dead_reason`` is ``stopped``; else no proposal for a dead row
    whose ``dead_reason`` is ``never_moved``; else reduce an
    ``excess`` row.  Every remedy carries the ``on_order_units``,
    ``current_reorder_point`` and ``current_max_qty`` the reads give
    for the row's item-site, whatever its kind.  Or None when a read
    failed (the stop-replenishing rules cannot be checked) or no rule
    applies.  The route writes nothing and calls no ERP.
    """
    if levels is None or on_order is None:
        return None
    key = (row["item_code"], row["site_code"])
    current_reorder_point, current_max_qty = levels.get(key, (None, None))
    on_order_units = on_order.get(key, 0.0)
    justified_units = float(row["justified_units"])
    if on_order_units > 0:
        kind = "stop_replenishing"
        reason = (
            f"{_write_number(on_order_units)} units are still on order, "
            "adding to the excess. Stop buying it first."
        )
    elif _above_level(current_reorder_point, justified_units) or _above_level(
        current_max_qty, justified_units
    ):
        kind = "stop_replenishing"
        reason = (
            "The ERP still reorders it: its reorder point "
            f"({_level_text(current_reorder_point)}) or max "
            f"({_level_text(current_max_qty)}) is above the "
            f"{_write_number(justified_units)} units its demand justifies. "
            "Stop buying it first."
        )
    elif row["category"] == "dead" and row.get("dead_reason") == "stopped":
        kind = "dispose"
        reason = (
            f"No demand since {row['last_demand_on'].isoformat()}. "
            "Scrap or sell it: the planner decides, and any write-down "
            "belongs to finance."
        )
    elif row["category"] == "dead" and row.get("dead_reason") == "never_moved":
        return None
    elif row["category"] == "excess":
        kind = "reduce"
        reason = (
            f"Still in demand (last on {row['last_demand_on'].isoformat()}), "
            f"but {_write_number(float(row['excess_units']))} units are "
            f"above the {_write_number(justified_units)} units its demand "
            "justifies. Use it up, transfer it or return it."
        )
    else:
        return None
    return {
        "kind": kind,
        "on_order_units": on_order_units,
        "current_reorder_point": current_reorder_point,
        "current_max_qty": current_max_qty,
        "reason": reason,
    }


def proposed_remedy(row: dict) -> dict | None:
    """The remedy one ranked excess row is proposed, as JSON.

    The exact remedy ``GET /api/excess`` answers for the row while
    ``include_remedy`` is true: the two reads through
    ``_remedy_reads`` and ``_row_remedy`` — answered as the
    ``ExcessRemedy`` object in JSON, with every key.  Or ``None``
    when no remedy is proposed, and ``None`` when anything in the
    work raises: the remedy is a side note of the decision, never a
    reason to fail it.
    """
    try:
        levels, on_order = _remedy_reads()
        remedy = _row_remedy(row, levels, on_order)
        if remedy is None:
            return None
        return ExcessRemedy.model_validate(remedy).model_dump(mode="json")
    except Exception:
        return None


@router.get("", response_model=list[ExcessDecisionRow])
def list_excess(
    limit: int = Query(default=20, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    category: Literal["dead", "excess"] | None = Query(default=None),
    dead_reason: Literal["never_moved", "stopped"] | None = Query(default=None),
    as_of: date | None = Query(default=None),
    include_decided: bool = Query(default=False),
    include_remedy: bool = Query(default=False),
) -> list[dict]:
    """List excess and dead stock ranked by money tied up, unknown money last.

    Every row carries its current excess decision (``decision``, or
    null) and ``decision_lapsed``.  While ``include_decided`` is false,
    a row whose current decision is a not-yet-lapsed dismissal, or a
    snooze whose ``snooze_until`` is after today (the server's UTC
    date), is left out of the list; accepted, lapsed, undecided and
    expired-snooze rows stay listed, and with ``include_decided`` true
    every row is listed.  Every row also carries
    ``item_description``, the item's description from the
    ``inventory_items`` read model (null when the item is not in
    it), from one read per request.  Rows are left out after the
    ``category`` and ``dead_reason`` filters and before ``offset``
    and ``limit`` are applied, and every row keeps the ``rank`` that
    ``excess_ranked`` gave it.

    While ``include_remedy`` is false (the default) the answer is
    exactly that, key for key: no row carries a ``remedy`` key and
    neither the ``item_sites`` levels nor the open purchase lines are
    read.  While it is true and at least one row is on the page, the
    ``item_sites`` levels (every ``item_sites`` row) and the open
    purchase lines (each ERP's newest sync) are read once each, before
    any remedy is worked out; every returned row then carries
    ``remedy``, an ``ExcessRemedy`` proposed by the first rule that
    applies — stop replenishing while units of the item are still on
    order, stop replenishing while the ERP's reorder point or max
    quantity is above the row's justified units (0 for a dead row, so
    any level above zero — a level equal to the justified units is
    not above it), dispose a dead row whose demand stopped, or reduce
    an excess row — or null for a dead row that never moved and no
    stop-replenishing rule caught.  A read that raises is caught and
    leaves every row's ``remedy`` null, because the
    stop-replenishing rules cannot be checked: the route still
    answers 200 with every row it returns, and with no row on the
    page neither read runs.
    """
    rows = _filtered(_ranked(as_of), category, dead_reason)
    enriched = _enriched_rows(rows)
    descriptions = _item_descriptions()
    if include_decided:
        paged = [row for row, _hidden in enriched][offset : offset + limit]
    else:
        paged = [row for row, hidden in enriched if not hidden][offset : offset + limit]
    answered = [
        {
            **row,
            "item_description": descriptions.get(row["item_code"]),
            "include_remedy": include_remedy,
        }
        for row in paged
    ]
    if include_remedy and answered:
        levels, on_order = _remedy_reads()
        for row in answered:
            try:
                row["remedy"] = _row_remedy(row, levels, on_order)
            except Exception:
                row["remedy"] = None
    return answered


@router.get("/export.csv")
def export_excess(
    category: Literal["dead", "excess"] | None = Query(default=None),
    dead_reason: Literal["never_moved", "stopped"] | None = Query(default=None),
    as_of: date | None = Query(default=None),
) -> Response:
    """Export every matching excess/dead row as a CSV attachment (no limit)."""
    rows = _filtered(_ranked(as_of), category, dead_reason)
    if rows:
        export_as_of = rows[0]["as_of"]
    elif as_of is not None:
        export_as_of = as_of
    else:
        with get_cursor() as cur:
            export_as_of = default_as_of(cur)
    return Response(
        content=excess_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="excess-{export_as_of.isoformat()}.csv"'
        },
    )
