"""Parameter writebacks: the preview, the confirm, and the audit.

``GET /api/parameter-writeback/preview`` lists the item-sites whose
writeback is outstanding, with the values a writeback would push.
The rows come from two sources, each read once per call: every
item-site whose current planner decision is "accepted" but outstanding
by the decision summary's rule (the ERP reorder point is null or
differs from the recommendation by more than 1) — a row with
``basis`` "decision", whose proposed reorder point is the decision's
``recommended_reorder_point_units`` and whose proposed max quantity
keeps the item's order quantity and never drops below the reorder
point — and every designated DDMRP buffer whose zone row from
``app.api.ddmrp.ddmrp_zone_rows()`` has status "ok" (a row with
``basis`` "ddmrp", ``decision_id``, ``decided_at`` and
``money_at_stake`` null), outstanding when the pair's
``item_sites.reorder_point`` or ``max_qty`` is null or differs from
the proposal (the top of yellow, the top of green) by more than 1.
A designated item-site whose zone row is "ok" is DDMRP-managed,
so its decision row is never listed or written, whether or not its
buffer row is outstanding.  The decision rows keep their order and
the buffer rows follow them, ordered by item_code then site_code.
A row is writable only when the item-site's source system is
"acumatica" or "odoo".

``POST /api/parameter-writeback/confirm`` (admin only — the same
``require_role("admin")`` the planner decisions use) writes the
outstanding preview rows (the accepted decisions and the DDMRP
buffers) back to their ERPs: the body carries 1
to 200 item-site pairs, no duplicate pair (a NUL byte, an empty
list, more than 200 items, or a duplicate answers 422), and every
pair is checked against the same preview rows the preview route
builds — shared code, not copied.  A pair with no preview row
answers "skipped" / "not outstanding" and a row that is not writable
answers "skipped" with its blocked_reason — both store no audit row
and make no ERP call.  Every other pair writes the row's proposed
values through the writer for its source system (the Acumatica
item-warehouse parameter writer on ``AcumaticaClient`` for
"acumatica", ``OdooClient().write_reorder_rule`` for "odoo"): a
write that returns
"written" or "unchanged" stores one audit row — with the preview
row's ``basis`` on it, the row's written, unchanged or failed
audit alike — and sets the
item-site's ``reorder_point``/``max_qty`` to the after values, with
``order_qty`` derived as max minus reorder point when that is
greater than 0, null otherwise; a writer error (``AcumaticaError``
or ``OdooError``) stores one audit row with outcome "failed" and the
exception's class name and message, leaves the item-site untouched,
answers the stable public reason "writer_error" — never the
exception's class name, message, or provider internals — and
processing continues with the next pair.  The response carries the
per-pair results in request order — each with the item-site's before
and after values as JSON numbers — and the outcome counts.

``GET /api/parameter-writeback/history`` lists the stored
parameter-writeback audit rows (the append-only
``parameter_writebacks`` table) newest first, optionally narrowed by
item_code and/or site_code, paged by limit and offset.  The parameter
values come back as exact decimals — the stored binary floats are
rendered through their shortest round-trip representation — so a
value such as 0.1000 round-trips exactly through the audit record;
the ``error`` value comes back sanitized: a failed write's ERP
exception, recorded internally as "ClassName: message", answers the
stable public reason "writer_error" instead of the exception's class
name, message, or provider internals.

``GET /api/parameter-writeback/record`` measures what the
writeback confirm pushed and what the ERPs kept: over the
append-only ``parameter_writebacks`` audit, an item-site is one
(item_code, site_code) pair and for each item-site only one row
counts — the latest of its rows whose outcome is "written" or
"unchanged" and whose ``after_reorder_point`` and
``after_max_qty`` are both not null, the latest the greatest
``written_at``, ties broken by the greatest ``id`` (a "failed"
row never counts and never hides an earlier push) — the basis
decides the kind: a reorder-point push when that row's ``basis``
is "decision", a buffer push when it is "ddmrp", and a push of any
other basis (such as "excess") counted in neither kind.  The push
is read back when the item-site's ``item_sites`` row exists and its
``synced_at`` is
later than the push's ``written_at``, and is unchanged when the
row's ``reorder_point`` and ``max_qty`` are both not null and
each differs by no more than 0.000001 from the push's after
values, every other read-back push being changed.  The answer is
``{reorder_points, buffers}``, each ``{pushed, read, unchanged,
rate}`` with ``rate`` ``unchanged / read`` rounded to 4 places,
null when ``read`` is 0.  The measure's limits are the sync's:
AISCP keeps no history of the ERP's values between syncs, so a
value changed and changed back between two syncs counts as
unchanged, and an item-site that no sync lists any more is never
read back.

The preview, history and record are open to any authenticated
user (401 without a valid Bearer token); the confirm answers 403
to a non-admin before any repository read, ERP write, or audit
row.  The preview, history and record read only — they call no
ERP client and write nothing.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError, model_validator

import app.feed.acumatica_client as _acumatica_client_module
import app.feed.odoo_client as _odoo_client_module
from app.api.ddmrp import ddmrp_zone_rows
from app.auth.authorization import require_role
from app.auth.dependencies import require_authenticated_user
from app.auth.display_name import display_name
from app.auth.models import AuthUser
from app.db.connection import get_cursor
from app.feed.acumatica_client import AcumaticaClient, AcumaticaError
from app.feed.odoo_client import OdooClient, OdooError
from app.recommend.push_record import push_record
from app.repositories.parameter_writeback_postgres_repository import (
    ParameterWritebackPostgresRepository,
)
from app.repositories.parameter_writebacks_postgres_repository import (
    ParameterWritebacksPostgresRepository,
)

router = APIRouter(prefix="/api/parameter-writeback", tags=["parameter-writeback"])

_repo = ParameterWritebackPostgresRepository()
_writebacks = ParameterWritebacksPostgresRepository()

_WRITABLE_SOURCE_SYSTEMS = frozenset({"acumatica", "odoo"})
_BLOCKED_NO_ITEM_SITES_ROW = "no item_sites row"
_BLOCKED_UNSUPPORTED_SOURCE_SYSTEM = "unsupported source system"

_NUL = chr(0)

_MAX_CONFIRM_ITEMS = 200
_CONFIRM_FAILED_REASON = "writer_error"


class ParameterWritebackRow(BaseModel):
    """One preview row: the outstanding accepted decision, the
    item-site's current parameters and the proposed writeback values."""

    item_code: str
    site_code: str
    decision_id: str | None = None
    decided_at: datetime | None = None
    money_at_stake: float | None = None
    source_system: str | None = None
    current_reorder_point: float | None = None
    current_max_qty: float | None = None
    current_order_qty: float | None = None
    proposed_reorder_point: float
    proposed_max_qty: float
    writable: bool
    blocked_reason: str | None = None
    basis: Literal["decision", "ddmrp"]


class ParameterWritebackPreviewResponse(BaseModel):
    """The response of GET /api/parameter-writeback/preview: the rows
    (money at stake descending, nulls last, then item and site) and
    the totals."""

    rows: list[ParameterWritebackRow]
    count: int
    writable_count: int


class ParameterWriteback(BaseModel):
    """One parameter-writeback audit row (append-only).

    The parameter values are carried as exact decimals: the binary
    floats of the Float columns are rendered through their shortest
    round-trip representation, so 0.1000 comes back exactly 0.1 —
    never a 17-digit binary-float artifact.
    """

    id: str
    item_code: str
    site_code: str
    source_system: str
    decision_id: str | None = None
    before_reorder_point: Decimal | None = None
    before_max_qty: Decimal | None = None
    after_reorder_point: Decimal | None = None
    after_max_qty: Decimal | None = None
    requested_reorder_point: Decimal
    requested_max_qty: Decimal
    outcome: str
    error: str | None = None
    written_by: str
    written_by_name: str | None = None
    written_at: datetime


class ParameterWritebackConfirmItem(BaseModel):
    """One requested item-site pair of the confirm body.

    A NUL byte in either code, or a code over 64 characters, answers
    422 in FastAPI's HTTPValidationError envelope (raised in the
    validator below) instead of reaching the database."""

    item_code: str = Field(..., max_length=64)
    site_code: str = Field(..., max_length=64)

    @model_validator(mode="after")
    def _check_business_rules(self) -> Self:
        for value in (self.item_code, self.site_code):
            if _NUL in value:
                raise ValueError("must not contain NUL bytes")
        return self


class ParameterWritebackConfirmBody(BaseModel):
    """The body of POST /api/parameter-writeback/confirm.

    1 to 200 item-site pairs, no duplicate pair — an empty list, more
    than 200 items, or a duplicate (item_code, site_code) pair
    answers 422 in FastAPI's HTTPValidationError envelope (raised in
    the field constraint and the validator below)."""

    items: list[ParameterWritebackConfirmItem] = Field(
        ..., min_length=1, max_length=_MAX_CONFIRM_ITEMS
    )

    @model_validator(mode="after")
    def _check_no_duplicate_pairs(self) -> Self:
        seen: set[tuple[str, str]] = set()
        for item in self.items:
            pair = (item.item_code, item.site_code)
            if pair in seen:
                raise ValueError("duplicate item_code/site_code pair")
            seen.add(pair)
        return self


class ParameterWritebackConfirmValues(BaseModel):
    """The before or after values of one confirm result: the
    item-site's parameter fields, mirroring the preview's numeric
    shape, serialized as JSON numbers — ``order_qty`` is null when
    the derived value (max minus reorder point) is not positive."""

    reorder_point: float | None
    max_qty: float | None
    order_qty: float | None = None


class ParameterWritebackConfirmResult(BaseModel):
    """One confirm result, in request order.

    ``reason`` is null for a written or unchanged write, "not
    outstanding" or the row's blocked_reason for a skipped pair, and
    the stable public reason "writer_error" for a failed write —
    never the exception's class name, message, or provider
    internals.  ``before`` and ``after`` are null for a skipped or
    failed pair."""

    item_code: str
    site_code: str
    outcome: Literal["written", "unchanged", "failed", "skipped"]
    reason: str | None = None
    before: ParameterWritebackConfirmValues | None = None
    after: ParameterWritebackConfirmValues | None = None


class ParameterWritebackConfirmResponse(BaseModel):
    """The response of POST /api/parameter-writeback/confirm: the
    per-pair results in request order and the outcome counts."""

    results: list[ParameterWritebackConfirmResult]
    written: int
    unchanged: int
    failed: int
    skipped: int


class ParameterWritebackPushRecordKind(BaseModel):
    """One kind of push in the writeback record: the counted
    pushes of that kind (``pushed``), how many of those the ERPs
    read back (``read``), how many of the read ones the ERPs
    kept unchanged (``unchanged``) and ``rate``, ``unchanged /
    read`` rounded to 4 places, null when ``read`` is 0."""

    pushed: int
    read: int
    unchanged: int
    rate: float | None = None


class ParameterWritebackPushRecord(BaseModel):
    """The response of GET /api/parameter-writeback/record: the
    measure of the counted pushes per kind.  The basis decides the
    kind: a counted push is a reorder-point push when its
    ``basis`` is "decision" and a buffer push when its ``basis`` is
    "ddmrp"; a push of any other basis (such as "excess") is
    counted in neither kind.  AISCP keeps no history of the ERP's
    values between syncs, so a value changed and changed back
    between two syncs counts as unchanged, and an item-site that
    no sync lists any more is never read back."""

    reorder_points: ParameterWritebackPushRecordKind
    buffers: ParameterWritebackPushRecordKind


class _HistoryFilters(BaseModel):
    """The history route's optional codes; a NUL byte in either
    answers 422 in FastAPI's HTTPValidationError envelope (raised in
    the dependency below) instead of reaching the database."""

    item_code: str | None = Field(default=None, max_length=64)
    site_code: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _check_business_rules(self) -> Self:
        for value in (self.item_code, self.site_code):
            if value is not None and _NUL in value:
                raise ValueError("must not contain NUL bytes")
        return self


def history_filters(
    item_code: str | None = Query(default=None, max_length=64),
    site_code: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> tuple[str | None, str | None, int, int]:
    """The query parameters of GET /api/parameter-writeback/history.

    item_code and site_code are optional filters (a NUL byte in
    either, or a code over 64 characters, answers 422 in FastAPI's
    HTTPValidationError envelope); limit is 1-1000 (default 100) and
    offset is at least 0 (default 0).
    """
    try:
        _HistoryFilters(item_code=item_code, site_code=site_code)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    return item_code, site_code, limit, offset


def require_any_authenticated_user(
    current_user: AuthUser = Depends(require_authenticated_user),
) -> AuthUser:
    """The preview route's own authentication dependency.

    ``GET /api/parameter-writeback/preview`` is open to any
    authenticated user: the dependency answers 401 without a valid
    Bearer token even when the router is mounted without the app's
    include-time gate, and passes the authenticated user through
    unchanged.
    """
    return current_user


def _proposed_max_qty(
    proposed_reorder_point: float,
    current_max_qty: float | None,
    current_order_qty: float | None,
) -> float:
    """The proposed max quantity of one row.

    It keeps the item's order quantity and never drops below the
    reorder point: proposed reorder point plus the current order
    quantity when that quantity is greater than 0; otherwise the
    current max quantity when it is not null and at least the proposed
    reorder point; otherwise the proposed reorder point itself.
    """
    if current_order_qty is not None and current_order_qty > 0:
        return proposed_reorder_point + current_order_qty
    if current_max_qty is not None and current_max_qty >= proposed_reorder_point:
        return current_max_qty
    return proposed_reorder_point


def _exact(value: float | None) -> Decimal | None:
    """The exact decimal of one stored binary float.

    repr is the shortest decimal string that round-trips to the same
    double, so the audit value keeps its exact decimal face (0.1
    stays 0.1) instead of the 17-digit binary expansion.
    """
    return None if value is None else Decimal(repr(value))


def _preview_rows() -> list[dict]:
    """The current preview rows, in the preview's order.

    The shared row construction both routes build on: the preview
    route answers these rows as-is, and the confirm route checks the
    requested pairs against them — so a confirm can never see a
    different picture of what is outstanding than the preview shows
    (shared, not copied).

    Two sources, each read once per call: the outstanding accepted
    decisions (``basis`` "decision") and the DDMRP buffer rows
    (``basis`` "ddmrp", ``_ddmrp_buffer_rows`` below).  A designated
    item-site whose zone row is "ok" is DDMRP-managed, so its
    decision row is never listed or written, whether or not its
    buffer row is outstanding.  The decision rows keep their order
    and the buffer rows follow them, ordered by item_code then
    site_code.
    """
    decision_rows: list[dict] = []
    for row in _repo.outstanding_accepted():
        proposed_reorder_point = float(row["recommended_reorder_point_units"])
        current_order_qty = row["order_qty"]
        current_max_qty = row["max_qty"]
        source_system = row["source_system"]
        writable = source_system in _WRITABLE_SOURCE_SYSTEMS
        if writable:
            blocked_reason = None
        elif row["site_id"] is None:
            blocked_reason = _BLOCKED_NO_ITEM_SITES_ROW
        else:
            blocked_reason = _BLOCKED_UNSUPPORTED_SOURCE_SYSTEM
        decision_rows.append(
            {
                "item_code": row["item_code"],
                "site_code": row["site_code"],
                "decision_id": row["decision_id"],
                "decided_at": row["decided_at"],
                "money_at_stake": row["money_at_stake"],
                "source_system": source_system,
                "current_reorder_point": row["reorder_point"],
                "current_max_qty": current_max_qty,
                "current_order_qty": current_order_qty,
                "proposed_reorder_point": proposed_reorder_point,
                "proposed_max_qty": _proposed_max_qty(
                    proposed_reorder_point, current_max_qty, current_order_qty
                ),
                "writable": writable,
                "blocked_reason": blocked_reason,
                "basis": "decision",
            }
        )
    buffer_rows, ok_pairs = _ddmrp_buffer_rows()
    return [
        row
        for row in decision_rows
        if (row["item_code"], row["site_code"]) not in ok_pairs
    ] + buffer_rows


def _item_sites_by_pair(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """The item_sites rows of the given pairs, keyed by (item_code,
    site_code) — a pair with no item_sites row simply has no entry,
    and the preview treats it exactly like the decision rows' LEFT
    JOIN does: null current values, no source system and
    "no item_sites row"."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT item_code, site_code, reorder_point, max_qty, "
            "order_qty, source_system "
            "FROM item_sites "
            "WHERE (item_code, site_code) IN "
            "(SELECT * FROM UNNEST(%s::text[], %s::text[]))",
            (
                [item_code for item_code, _ in pairs],
                [site_code for _, site_code in pairs],
            ),
        )
        return {
            (row["item_code"], row["site_code"]): dict(row) for row in cur.fetchall()
        }


def _ddmrp_buffer_rows() -> tuple[list[dict], set[tuple[str, str]]]:
    """The DDMRP buffer rows of the preview, in item-then-site
    order, and the set of the "ok" pairs.

    The preview's second source, read once per call: every designated
    item-site whose zone row from
    ``app.api.ddmrp.ddmrp_zone_rows()`` has status "ok", with the
    values a writeback would push — the top of yellow as the
    proposed reorder point and the top of green as the proposed max
    quantity.  A buffer row appears only when the pair is
    outstanding: the pair's ``item_sites.reorder_point`` or
    ``max_qty`` is null, or differs from the proposal by more than
    1.  A row carries the same fields as a decision row, with
    ``decision_id``, ``decided_at`` and ``money_at_stake`` null and
    ``basis`` "ddmrp"; its source system, current values and
    writable/blocked rules are computed exactly as for decision
    rows.  The second value is the set of the ``(item_code,
    site_code)`` of every zone row whose status is "ok", outstanding
    or not: a designated item-site whose zone row is "ok" is
    DDMRP-managed, so its decision row is never listed or written,
    whether or not its buffer row is outstanding.
    """
    _, zone_rows = ddmrp_zone_rows()
    ok_zones = [zone for zone in zone_rows if zone["status"] == "ok"]
    ok_pairs = {(zone["item_code"], zone["site_code"]) for zone in ok_zones}
    if not ok_zones:
        return [], ok_pairs
    item_sites = _item_sites_by_pair(
        [(zone["item_code"], zone["site_code"]) for zone in ok_zones]
    )
    rows: list[dict] = []
    for zone in ok_zones:
        key = (zone["item_code"], zone["site_code"])
        site = item_sites.get(key)
        reorder_point = site["reorder_point"] if site is not None else None
        max_qty = site["max_qty"] if site is not None else None
        order_qty = site["order_qty"] if site is not None else None
        source_system = site["source_system"] if site is not None else None
        proposed_reorder_point = float(zone["top_of_yellow"])
        proposed_max_qty = float(zone["top_of_green"])
        outstanding = (
            reorder_point is None or abs(reorder_point - proposed_reorder_point) > 1
        ) or (max_qty is None or abs(max_qty - proposed_max_qty) > 1)
        if not outstanding:
            continue
        writable = source_system in _WRITABLE_SOURCE_SYSTEMS
        if writable:
            blocked_reason = None
        elif site is None:
            blocked_reason = _BLOCKED_NO_ITEM_SITES_ROW
        else:
            blocked_reason = _BLOCKED_UNSUPPORTED_SOURCE_SYSTEM
        rows.append(
            {
                "item_code": zone["item_code"],
                "site_code": zone["site_code"],
                "decision_id": None,
                "decided_at": None,
                "money_at_stake": None,
                "source_system": source_system,
                "current_reorder_point": reorder_point,
                "current_max_qty": max_qty,
                "current_order_qty": order_qty,
                "proposed_reorder_point": proposed_reorder_point,
                "proposed_max_qty": proposed_max_qty,
                "writable": writable,
                "blocked_reason": blocked_reason,
                "basis": "ddmrp",
            }
        )
    return rows, ok_pairs


@router.get(
    "/preview",
    response_model=ParameterWritebackPreviewResponse,
    dependencies=[Depends(require_any_authenticated_user)],
)
def preview() -> dict:
    """Preview the parameter writeback of the outstanding accepted
    decisions.

    Open to any authenticated user.  Every row is the item-site's
    current accepted decision that the ERP has not applied yet, with
    the proposed values a writeback would push; ``count`` is the
    number of rows and ``writable_count`` the number of them whose
    source system is "acumatica" or "odoo".  Reads only: no ERP client
    call, nothing written.
    """
    rows = _preview_rows()
    return {
        "rows": rows,
        "count": len(rows),
        "writable_count": sum(1 for row in rows if row["writable"]),
    }


def _derived_order_qty(reorder_point: float, max_qty: float) -> float | None:
    """The item-site's derived order quantity of an after state:
    max minus reorder point when that is greater than 0, null
    otherwise."""
    order_qty = max_qty - reorder_point
    return order_qty if order_qty > 0 else None


def _apply_item_site(
    item_code: str,
    site_code: str,
    reorder_point: float,
    max_qty: float,
) -> None:
    """Set the item-site's after-write parameters.

    ``reorder_point`` and ``max_qty`` take the ERP's after values;
    ``order_qty`` is derived as max minus reorder point when that is
    greater than 0, null otherwise.
    """
    with get_cursor() as cur:
        cur.execute(
            "UPDATE item_sites "
            "SET reorder_point = %s, max_qty = %s, order_qty = %s "
            "WHERE item_code = %s AND site_code = %s",
            (
                reorder_point,
                max_qty,
                _derived_order_qty(reorder_point, max_qty),
                item_code,
                site_code,
            ),
        )


def _writer_error_classes() -> tuple[type[Exception], ...]:
    """The ERP writer's exception classes, the ones the confirm route
    records in the audit ``error`` column as "ClassName: message":
    every class defined in the two ERP client modules that derives
    from ``AcumaticaError`` or ``OdooError``."""
    classes: list[type[Exception]] = []
    for module in (_acumatica_client_module, _odoo_client_module):
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, (AcumaticaError, OdooError))
                and value.__module__ == module.__name__
            ):
                classes.append(value)
    return tuple(sorted(classes, key=lambda cls: cls.__name__))


def _public_error(stored_error: str | None) -> str | None:
    """The ``error`` value the public history route answers.

    The confirm route records a failed write's ERP exception in the
    audit table as "ClassName: message" for the internal audit; the
    public history route must not expose the exception's class name,
    message, or provider internals, so a stored value recorded with
    one of the writer's exception classes answers the stable public
    reason "writer_error".  Any other stored value comes back
    unchanged.
    """
    if stored_error is None:
        return None
    for cls in _writer_error_classes():
        if stored_error.startswith(f"{cls.__name__}: "):
            return _CONFIRM_FAILED_REASON
    return stored_error


def _writer_for(source_system: str):
    """The bound write method for one source system: the Acumatica
    item-warehouse parameter writer on a fresh ``AcumaticaClient()``
    for "acumatica", and ``write_reorder_rule`` on a fresh
    ``OdooClient()`` for "odoo"."""
    if source_system == "acumatica":
        return AcumaticaClient().write_item_warehouse_parameters
    return OdooClient().write_reorder_rule


def _confirm_pair(row: dict, user: AuthUser) -> dict:
    """One writable preview row: the ERP write, the audit row and the
    item-site update, in that order.

    The writer for the row's source system is called with the row's
    proposed values: the Acumatica item-warehouse parameter writer on
    ``AcumaticaClient()`` for "acumatica" and
    ``OdooClient().write_reorder_rule`` for "odoo".  A write that
    returns "written" or "unchanged" stores one audit row (the
    outcome, the ERP's before and after values, the requested
    values, the row's ``basis`` — passed explicitly from the
    preview row — the decision id, null for a DDMRP buffer row, and
    the
    token subject and display name, as the planner decisions record
    them) and sets the item-site's
    ``reorder_point`` and ``max_qty`` to the after values with
    ``order_qty`` derived as max minus reorder point when that is
    greater than 0, null otherwise.  A writer error
    (``AcumaticaError`` or ``OdooError`` — writes disabled, auth, a
    missing rule, a failed read-back, and so on) stores one audit row
    with outcome "failed" and the exception's class name and message,
    leaves the item-site untouched, and answers the stable public
    reason "writer_error" — never the exception's class name,
    message, or provider internals — so the caller can continue with
    the next pair.
    """
    item_code = row["item_code"]
    site_code = row["site_code"]
    proposed_reorder_point = row["proposed_reorder_point"]
    proposed_max_qty = row["proposed_max_qty"]
    before = {
        "reorder_point": row["current_reorder_point"],
        "max_qty": row["current_max_qty"],
        "order_qty": row["current_order_qty"],
    }
    writer = _writer_for(row["source_system"])
    try:
        result = writer(
            item_code=item_code,
            site_code=site_code,
            reorder_point=proposed_reorder_point,
            max_qty=proposed_max_qty,
        )
    except (AcumaticaError, OdooError) as exc:
        _writebacks.insert(
            item_code=item_code,
            site_code=site_code,
            source_system=row["source_system"],
            decision_id=row["decision_id"],
            basis=row["basis"],
            before_reorder_point=None,
            before_max_qty=None,
            after_reorder_point=None,
            after_max_qty=None,
            requested_reorder_point=proposed_reorder_point,
            requested_max_qty=proposed_max_qty,
            outcome="failed",
            error=f"{type(exc).__name__}: {exc}",
            written_by=user.sub,
            written_by_name=display_name(user),
        )
        return {
            "item_code": item_code,
            "site_code": site_code,
            "outcome": "failed",
            "reason": _CONFIRM_FAILED_REASON,
            "before": None,
            "after": None,
        }
    after_reorder_point = float(result["after"]["reorder_point"])
    after_max_qty = float(result["after"]["max_qty"])
    _writebacks.insert(
        item_code=item_code,
        site_code=site_code,
        source_system=row["source_system"],
        decision_id=row["decision_id"],
        basis=row["basis"],
        before_reorder_point=float(result["before"]["reorder_point"]),
        before_max_qty=float(result["before"]["max_qty"]),
        after_reorder_point=after_reorder_point,
        after_max_qty=after_max_qty,
        requested_reorder_point=proposed_reorder_point,
        requested_max_qty=proposed_max_qty,
        outcome=result["outcome"],
        error=None,
        written_by=user.sub,
        written_by_name=display_name(user),
    )
    _apply_item_site(item_code, site_code, after_reorder_point, after_max_qty)
    return {
        "item_code": item_code,
        "site_code": site_code,
        "outcome": result["outcome"],
        "reason": None,
        "before": before,
        "after": {
            "reorder_point": after_reorder_point,
            "max_qty": after_max_qty,
            "order_qty": _derived_order_qty(after_reorder_point, after_max_qty),
        },
    }


@router.post(
    "/confirm",
    response_model=ParameterWritebackConfirmResponse,
)
def confirm(
    body: ParameterWritebackConfirmBody,
    user: AuthUser = Depends(require_role("admin")),
) -> dict:
    """Write the outstanding accepted decisions back to their ERPs.

    Admin only — the same ``require_role("admin")`` the planner
    decisions use: a non-admin answers 403 before any repository
    read, ERP write, or audit row.  The body carries 1 to 200
    item-site pairs, no duplicate pair; a NUL byte in any code, an
    empty list, more than 200 items, or a duplicate answers 422 in
    FastAPI's HTTPValidationError envelope.

    Every pair is checked, in request order, against the same
    preview rows the preview route builds (shared code, not copied):
    a pair with no preview row answers "skipped" / "not outstanding"
    and a row that is not writable answers "skipped" with its
    blocked_reason — both store no audit row and make no ERP call.
    Every other pair writes the row's proposed values to its source
    ERP; a "written" or "unchanged" write stores one audit row and
    sets the item-site's parameters to the after values, and a
    writer error stores one audit row with outcome "failed", leaves
    the item-site untouched, and answers the stable public reason
    "writer_error".

    The response carries the per-pair results in request order —
    each with the item-site's before and after values (reorder_point,
    max_qty, order_qty — order_qty null when the derived value is
    not positive) as JSON numbers — and the outcome counts.
    """
    preview_rows = _preview_rows()
    by_pair = {(row["item_code"], row["site_code"]): row for row in preview_rows}
    results: list[dict] = []
    for item in body.items:
        row = by_pair.get((item.item_code, item.site_code))
        if row is None or not row["writable"]:
            results.append(
                {
                    "item_code": item.item_code,
                    "site_code": item.site_code,
                    "outcome": "skipped",
                    "reason": (
                        row["blocked_reason"] if row is not None else "not outstanding"
                    ),
                    "before": None,
                    "after": None,
                }
            )
        else:
            results.append(_confirm_pair(row, user))
    counts = {"written": 0, "unchanged": 0, "failed": 0, "skipped": 0}
    for result in results:
        counts[result["outcome"]] += 1
    return {
        "results": results,
        "written": counts["written"],
        "unchanged": counts["unchanged"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
    }


@router.get(
    "/history",
    response_model=list[ParameterWriteback],
    dependencies=[Depends(require_any_authenticated_user)],
)
def history(
    filters: tuple[str | None, str | None, int, int] = Depends(history_filters),
) -> list[dict]:
    """List the stored parameter-writeback audit rows, newest first.

    Open to any authenticated user.  The rows come from the
    append-only parameter_writebacks table, ordered by written_at
    DESC, id DESC, narrowed by item_code and/or site_code when given,
    and paged by limit (1-1000, default 100) and offset (default 0).
    The parameter values are exact decimals: 0.1000 round-trips as
    0.1.  A failed write's ERP exception, recorded internally as
    "ClassName: message", comes back as the stable public reason
    "writer_error" — the exception's class name, message, and
    provider internals are never exposed.  Reads only: nothing
    written.
    """
    item_code, site_code, limit, offset = filters
    return [
        {
            "id": row["id"],
            "item_code": row["item_code"],
            "site_code": row["site_code"],
            "source_system": row["source_system"],
            "decision_id": row["decision_id"],
            "before_reorder_point": _exact(row["before_reorder_point"]),
            "before_max_qty": _exact(row["before_max_qty"]),
            "after_reorder_point": _exact(row["after_reorder_point"]),
            "after_max_qty": _exact(row["after_max_qty"]),
            "requested_reorder_point": _exact(row["requested_reorder_point"]),
            "requested_max_qty": _exact(row["requested_max_qty"]),
            "outcome": row["outcome"],
            "error": _public_error(row["error"]),
            "written_by": row["written_by"],
            "written_by_name": row["written_by_name"],
            "written_at": row["written_at"],
        }
        for row in _writebacks.list(
            limit=limit, offset=offset, item_code=item_code, site_code=site_code
        )
    ]


@router.get(
    "/record",
    response_model=ParameterWritebackPushRecord,
    dependencies=[Depends(require_any_authenticated_user)],
)
def record() -> dict:
    """The record of the parameter writebacks: what was pushed and
    what the ERPs kept.

    Open to any authenticated user, through the same
    ``require_any_authenticated_user`` dependency as the history
    route.  No parameters.  The answer is
    ``app.recommend.push_record.push_record()``:
    ``{reorder_points, buffers}``, each ``{pushed, read, unchanged,
    rate}`` — ``pushed`` the counted item-sites whose one counted
    push (the latest of the item-site's "written" or "unchanged"
    audit rows with both after values stored, the greatest
    ``written_at``, ties broken by the greatest ``id``; a "failed"
    row never counts and never hides an earlier push) is of that
    kind — the basis decides the kind: a reorder-point push when
    that row's ``basis`` is "decision", a buffer push when it is
    "ddmrp", and a push of any other basis (such as "excess")
    counted in neither kind; ``read`` how many of
    those are read back (the item-site's ``item_sites`` row exists
    and its ``synced_at`` is later than the push's ``written_at``);
    ``unchanged`` how many of the read ones are unchanged (the row's
    ``reorder_point`` and ``max_qty`` are both not null and each
    differs by no more than 0.000001 from the push's after values);
    and ``rate`` ``unchanged / read`` rounded to 4 places, null when
    ``read`` is 0.  The measure's limits are the sync's: AISCP keeps
    no history of the ERP's values between syncs, so a value changed
    and changed back between two syncs counts as unchanged, and an
    item-site that no sync lists any more is never read back.
    Reads only: nothing written, no ERP call.
    """
    return push_record()
