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

import re
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
from app.models.erp_standing_grants import (
    ErpStandingGrantIn,
    ErpStandingGrantOut,
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
