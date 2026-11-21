"""ERP standing grants: the standing permission to write to the ERP.

``GET /api/standing-grants`` lists every stored grant — in force and
revoked — newest first (by ``id`` descending), each row an object
with ``id``, ``process``, ``max_document_value``, ``max_daily_value``,
``max_data_age_hours``, ``created_at`` and ``created_by``, ``revoked_at``
(an ISO-8601 string, null while in force) and ``revoked_by``, and
``in_force`` (true while ``revoked_at`` is null); it is open to any
signed-in user, and ``GET /api/standing-grants/runs`` answers the
stored standing-grant runs — ``id``, ``process``, ``grant_id``,
``started_at`` an ISO-8601 string with its UTC offset, ``outcome``
and ``reason`` (null when the run has none), ``documents`` and
``value`` and ``left_out`` the groups the limits left out, in their
stored order, an order-plan run's entry its ``site_code``,
``supplier_code``, ``value`` the number its stored decimal string
reads as and ``reason``, a transfer run's entry its ``from_site``,
``to_site``, ``value`` and ``reason`` — newest first by
``started_at`` descending, the greater ``id`` breaking a tie, at
most ``limit`` (an integer from 1 to 200, default 50) of them,
narrowed to one process's runs by a ``process`` that must be one of
the two names; it is open to any signed-in user and never
writes.  ``POST /api/standing-grants`` stores a new grant —
``process``, ``max_document_value``, ``max_daily_value`` and
``max_data_age_hours``, with ``created_by`` the caller's username —
and ``POST /api/standing-grants/{id}/revoke`` sets ``revoked_at`` to
now and ``revoked_by`` to the caller's username; both need the admin
role, a signed-in non-admin getting 403 exactly as the
supplier-order-minimums routes refuse, and a 401 without a valid
token.  No route deletes a grant and no route changes a grant's
limits: changing a limit means revoking the grant and creating a new
one.

``GET /api/standing-grants/record`` answers the record of the
stored standing-grant runs, per process — ``{"processes": [...]}``
with one entry for ``order_plan_release`` and then one for
``transfer_release``, each ``{process, runs, released,
sent_everything, nothing_due, skipped, failed, rate,
documents_sent, value_sent, latest}``: ``runs`` the number of the
process's stored runs, ``released``, ``nothing_due``, ``skipped``
and ``failed`` those runs counted by outcome,
``sent_everything`` the ``released`` runs whose ``left_out`` is
empty, ``rate`` ``sent_everything / (runs - nothing_due)`` rounded
to 4 decimals, null while ``runs - nothing_due`` is 0,
``documents_sent`` the sum of the runs' ``documents`` and
``value_sent`` the sum of the runs' ``value``, a number rounded to
2 decimal places with ``decimal.ROUND_HALF_UP`` — and
``latest`` the process's newest run, by ``started_at`` descending,
the greater ``id`` breaking a tie, or null while the process has no
run; it is ``{started_at, outcome, reason, documents, value,
held_back, held_back_value}`` with ``started_at`` an ISO-8601
string with its UTC offset, ``value`` a number, ``held_back`` the
number of the entries of its ``left_out`` and
``held_back_value`` the sum of their values, a number rounded to 2
decimal places with ``decimal.ROUND_HALF_UP``.  A process with no
run answers ``runs`` 0, every count 0, ``rate`` null,
``documents_sent`` 0, ``value_sent`` 0 and ``latest`` null.  The
read is one SELECT over ``standing_grant_runs`` through
``app.db.connection.get_cursor`` (at most two), the route is open
to any signed-in user, like ``GET /api/standing-grants/runs``,
answers 401 without a valid token and writes nothing.

Creating a grant when one for the process is already in force answers
409 and writes nothing — the unique index over ``process`` where
``revoked_at`` is null is the check, so even two concurrent creates
for the same process can never both succeed — and a revoke of an id
that was never stored answers 404 while a revoke of an already
revoked grant answers 409.  A body whose ``process`` is not one of
the two names, whose values are not finite numbers above 0 and at
most 1,000,000,000,000 — including a string, a boolean, a null, a
missing value and a raw body whose number is ``Infinity``, ``NaN`` or
1e400, refused the way the supplier-order-minimums route refuses it —
whose ``max_daily_value`` is below ``max_document_value``, whose
``max_data_age_hours`` is not a whole number from 1 to 168 (a
boolean or 2.5 among them) or that names a field the body does not
define, and a revoke whose ``id`` is not an integer from 1 to
9223372036854775807, all answer 422 before anything is written, in
FastAPI's HTTPValidationError envelope, never a 500.  For the
admin-only routes the 401/403 from authentication and authorization
come before any validation — even a body that is not valid JSON at
all: FastAPI decodes a JSON request body before it resolves the
route's dependencies, so without the
``standing_grants_validation_guard`` registered on the app such a
body would answer 422 before the 401/403 could run; the guard
re-runs the admin check on the two write paths and answers the 401
or 403 instead, passing every other request (and a signed-in admin
on these paths) through to the default 422 handler.
"""

import json
import re
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
)
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError

from app.api.erp_mappings import (
    require_admin,
    require_any_authenticated_user,
)
from app.api.supplier_order_minimums import _reject_unstoreable_body
from app.auth.dependencies import require_authenticated_user
from app.auth.models import AuthUser
from app.db.connection import get_cursor
from app.models.erp_standing_grants import (
    ErpStandingGrantIn,
    ErpStandingGrantOut,
    ErpStandingGrantRecordOut,
    ErpStandingGrantRunOut,
)
from app.repositories.erp_standing_grants_postgres_repository import (
    ErpStandingGrantPostgresRepository,
    StandingGrantAlreadyInForce,
    StandingGrantAlreadyRevoked,
)
from app.repositories.standing_grant_runs_postgres_repository import (
    StandingGrantRunPostgresRepository,
)

router = APIRouter(prefix="/api/standing-grants", tags=["standing-grants"])

_repo = ErpStandingGrantPostgresRepository()
_runs_repo = StandingGrantRunPostgresRepository()

# The largest integer the bigint id column holds.
BIGINT_MAX = 9_223_372_036_854_775_807

# The two write paths of this router: the create and the revoke.
_WRITE_PATH = re.compile(r"^/api/standing-grants(?:/[^/]+/revoke)?$")


async def standing_grants_validation_guard(
    request: Request,
    exc: RequestValidationError,
) -> Response:
    """The 401/403 the writes owe comes before the body's 422.

    FastAPI decodes a JSON request body before it resolves the
    route's dependencies, so a body that is not valid JSON would
    answer 422 before the auth gate or the admin check could run —
    but for the writes the 401 or 403 comes before any validation.
    This handler (registered on the app for
    RequestValidationError) re-runs the admin check when the request
    is one of the two writes and, when the caller is not signed in or
    is a signed-in non-admin, answers with that 401 or 403 instead
    of the 422.  Every other request, and a signed-in admin on these
    paths, passes through to the default handler, which answers the
    422 exactly as before.
    """
    if request.method != "POST" or _WRITE_PATH.fullmatch(request.url.path) is None:
        return await request_validation_exception_handler(request, exc)
    try:
        require_admin(
            current_user=require_authenticated_user(
                authorization=request.headers.get("authorization")
            )
        )
    except HTTPException as auth_error:
        return await http_exception_handler(request, auth_error)
    return await request_validation_exception_handler(request, exc)


@router.get("", response_model=list[ErpStandingGrantOut])
def list_grants(
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> list[dict]:
    """Every stored grant, in force and revoked, newest first.

    Open to any signed-in user (the admin role is only needed for the
    mutations); the router's include-time gate answers 401 without a
    valid Bearer token.
    """
    return _repo.list()


@router.get("/runs", response_model=list[ErpStandingGrantRunOut])
def list_grant_runs(
    limit: int = Query(default=50, ge=1, le=200),
    process: Literal["order_plan_release", "transfer_release"] | None = Query(
        default=None
    ),
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> list[dict]:
    """The stored standing-grant runs, newest first, at most ``limit``.

    Open to any signed-in user, like the grants list; the router's
    include-time gate answers 401 without a valid Bearer token.
    ``limit`` is an integer from 1 to 200, default 50, and
    ``process`` one of the two release names, narrowing the answer to
    that process's runs when given; anything else answers 422 in
    FastAPI's HTTPValidationError envelope, never a 500.  Each run is
    the stored row: ``id``, ``process`` and ``grant_id``,
    ``started_at`` with its UTC offset, ``outcome`` and ``reason``
    (null when the run has none), ``documents`` and ``value`` and
    ``left_out`` in their stored order.  The route never writes.
    """
    return _runs_repo.list(limit=limit, process=process)


# The two processes the record's entries cover, in the answer's order.
_RECORD_PROCESSES = ("order_plan_release", "transfer_release")

# The precision the record's money answers are rounded to.
_TWO_PLACES = Decimal("0.01")


def _round_money(amount: Decimal) -> float:
    """The amount as a number, to 2 decimal places.

    The rounding mode and the precision are the explicit
    ``decimal.ROUND_HALF_UP`` at ``_TWO_PLACES`` — ``value_sent`` and
    ``held_back_value`` are rounded no other way.
    """
    return float(amount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def _left_out_entries(left_out: object) -> list:
    """The entries of a run's stored ``left_out`` — a list, never a raise.

    The column is JSONB and the app stores a JSON array of entries,
    but a hand-written row may store a JSON ``null`` or a raw string
    instead: a ``null`` or an unparseable or non-array value reads as
    no entries, so the record answers 200, never a 500.
    """
    if left_out is None:
        return []
    if isinstance(left_out, str):
        try:
            left_out = json.loads(left_out)
        except ValueError:
            return []
    return left_out if isinstance(left_out, list) else []


def _entry_value(entry: object) -> Decimal:
    """The value of one left-out entry: the number its stored decimal
    string reads as — 0 for an entry that names none or a value that
    does not read as a number."""
    if not isinstance(entry, dict):
        return Decimal(0)
    value = entry.get("value")
    if value is None or isinstance(value, bool):
        return Decimal(0)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _documents_count(documents: object) -> int:
    """The run's ``documents``: a non-negative whole number, else 0."""
    if isinstance(documents, bool):
        return 0
    return documents if isinstance(documents, int) else 0


def _run_value(value: object) -> Decimal:
    """The run's ``value``: the exact Decimal the NUMERIC column
    holds, else the number the value reads as, else 0."""
    if value is None or isinstance(value, bool):
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)


# The instant a run whose stored ``started_at`` does not read as a
# datetime orders before every run that does.
_EARLIEST_INSTANT = datetime.min.replace(tzinfo=UTC)


def _started_instant(row: dict) -> tuple:
    """The ordering key of a run for the record's ``latest``: its
    ``started_at`` as an aware datetime, the greater ``id`` breaking
    a tie — a key that compares, never a raise.

    The instant is compared as a datetime, never as its
    ``timestamp()`` float: near ``datetime.max`` that float loses
    the microseconds a run may differ by, so a one-microsecond step
    would read as a tie and the id would decide the newest run.
    """
    started_at = row["started_at"]
    if isinstance(started_at, datetime):
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        return (started_at, row["id"])
    return (_EARLIEST_INSTANT, row["id"])


def _started_at_string(started_at: object) -> str:
    """The run's ``started_at`` as the ISO-8601 string with its UTC
    offset — the aware datetime's own spelling."""
    if isinstance(started_at, datetime):
        return started_at.isoformat()
    return str(started_at)


def _record_process(process: str, rows: list) -> dict:
    """One process's entry of the record, over its stored run rows."""
    released = nothing_due = skipped = failed = 0
    sent_everything = 0
    documents_sent = 0
    value_sent = Decimal(0)
    latest_row = None
    for row in rows:
        outcome = row["outcome"]
        if outcome == "released":
            released += 1
            if not _left_out_entries(row["left_out"]):
                sent_everything += 1
        elif outcome == "nothing_due":
            nothing_due += 1
        elif outcome == "skipped":
            skipped += 1
        elif outcome == "failed":
            failed += 1
        documents_sent += _documents_count(row["documents"])
        value_sent += _run_value(row["value"])
        if latest_row is None or _started_instant(row) > _started_instant(latest_row):
            latest_row = row
    runs = len(rows)
    rate = (
        round(sent_everything / (runs - nothing_due), 4) if runs - nothing_due else None
    )
    latest: dict | None = None
    if latest_row is not None:
        left_out = _left_out_entries(latest_row["left_out"])
        latest = {
            "started_at": _started_at_string(latest_row["started_at"]),
            "outcome": latest_row["outcome"],
            "reason": latest_row["reason"],
            "documents": _documents_count(latest_row["documents"]),
            "value": float(_run_value(latest_row["value"])),
            "held_back": len(left_out),
            "held_back_value": _round_money(
                sum((_entry_value(entry) for entry in left_out), Decimal(0))
            ),
        }
    return {
        "process": process,
        "runs": runs,
        "released": released,
        "sent_everything": sent_everything,
        "nothing_due": nothing_due,
        "skipped": skipped,
        "failed": failed,
        "rate": rate,
        "documents_sent": documents_sent,
        "value_sent": _round_money(value_sent),
        "latest": latest,
    }


def _grant_record() -> dict:
    """The record of the stored standing-grant runs, per process.

    One SELECT over ``standing_grant_runs`` through
    ``app.db.connection.get_cursor`` (at most two, never more)
    buckets the stored runs by their process and answers the two
    entries — ``order_plan_release`` first, then
    ``transfer_release`` — whatever rows the table holds, the empty
    entry for a process with no run.  Read-only: it writes nothing.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, process, started_at, outcome, reason, documents, "
            "value, left_out FROM standing_grant_runs"
        )
        rows = cur.fetchall()
    by_process: dict[str, list] = {process: [] for process in _RECORD_PROCESSES}
    for row in rows:
        bucket = by_process.get(row["process"])
        if bucket is not None:
            bucket.append(dict(row))
    return {
        "processes": [
            _record_process(process, by_process[process])
            for process in _RECORD_PROCESSES
        ]
    }


@router.get("/record", response_model=ErpStandingGrantRecordOut)
def grant_record(
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """The record of the standing-grant runs, per process.

    ``{"processes": [...]}`` — one entry for ``order_plan_release``
    and then one for ``transfer_release``, each ``{process, runs,
    released, sent_everything, nothing_due, skipped, failed, rate,
    documents_sent, value_sent, latest}`` as the module documents:
    ``runs`` the process's stored runs, the four outcomes counted
    from them, ``sent_everything`` the ``released`` runs whose
    ``left_out`` is empty, ``rate`` ``sent_everything / (runs -
    nothing_due)`` rounded to 4 decimals, null while ``runs -
    nothing_due`` is 0, ``documents_sent`` the sum of the runs'
    ``documents`` and ``value_sent`` the sum of the runs'
    ``value``, a number rounded to 2 decimal places with
    ``decimal.ROUND_HALF_UP``; ``latest`` the process's newest run
    (by ``started_at`` descending, the greater ``id`` breaking a
    tie) — ``{started_at, outcome, reason, documents, value,
    held_back, held_back_value}`` with ``held_back_value`` the sum
    of the values of its ``left_out`` entries, a number rounded to 2
    decimal places with ``decimal.ROUND_HALF_UP`` — or null while
    the process has no run, the entry then answering ``runs`` 0,
    every count 0, ``rate`` null, ``documents_sent`` 0,
    ``value_sent`` 0 and ``latest`` null.  Open to any signed-in
    user, like the runs list — the router's include-time gate
    answers 401 without a valid Bearer token — and the route never
    writes.
    """
    return _grant_record()


@router.post("", status_code=201, response_model=ErpStandingGrantOut)
def create_grant(
    body: ErpStandingGrantIn,
    current_user: AuthUser = Depends(require_admin),
    _storeable_body: None = Depends(_reject_unstoreable_body),
) -> dict:
    """Store one standing grant; needs the admin role (403 otherwise).

    The body's ``process`` (one of the two release names), its finite
    values above 0 (at most 1,000,000,000,000, the daily one not
    below the per-document one) and its whole ``max_data_age_hours``
    (1 to 168) must validate, or the body answers 422 before anything
    is written.  ``created_by`` is the caller's username.  When a
    grant for the process is already in force the unique index refuses
    the insert — even against a racing create — and the route answers
    409 with nothing written.  Returns the stored row.
    """
    try:
        return _repo.create(
            process=body.process,
            max_document_value=body.max_document_value,
            max_daily_value=body.max_daily_value,
            max_data_age_hours=body.max_data_age_hours,
            created_by=current_user.username or "",
        )
    except StandingGrantAlreadyInForce:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A standing grant for {body.process} is already in "
                "force; revoke it and create a new one"
            ),
        ) from None


@router.post("/{id}/revoke", response_model=ErpStandingGrantOut)
def revoke_grant(
    id: Annotated[int, Path(ge=1, le=BIGINT_MAX)],  # noqa: A002 - the path is /{id}
    current_user: AuthUser = Depends(require_admin),
) -> dict:
    """Revoke one grant by id; needs the admin role (403 otherwise).

    ``revoked_at`` is set to now and ``revoked_by`` to the caller's
    username, answered with the revoked row.  ``id`` is an integer
    from 1 to 9223372036854775807, the largest the bigint column
    holds; anything else answers 422.  404 when no row has that id,
    409 when the row is already revoked.
    """
    try:
        return _repo.revoke(id, revoked_by=current_user.username or "")
    except LookupError:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown standing grant: {id}",
        ) from None
    except StandingGrantAlreadyRevoked:
        raise HTTPException(
            status_code=409,
            detail="The standing grant is already revoked",
        ) from None
