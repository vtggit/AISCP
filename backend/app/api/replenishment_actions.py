"""Draft purchase order creation for a replenishment item-site.

``POST /api/replenishment/actions/draft-po`` is the operator-facing
action that drafts one Odoo ``purchase.order`` for an
item/site/supplier triple. The route is Bearer-authenticated by the
mount-time gate like the other routers, has no schedule, and is called
by nothing else in this slice. It takes the authenticated user
(``current_user: AuthUser = Depends(require_authenticated_user)``) to
attribute every confirmed action.

Before calling the client the route translates the three codes to
their target-system ``odoo`` codes: each is looked up as kind
``item`` (``item_code``), ``site`` (``site_code``) or ``vendor``
(``supplier_code``) for target system ``odoo`` through the M1
repository's ``get_target``
(``erp_code_mappings_postgres_repository``) — a mapped code is
replaced by its stored ``target_code`` and an unmapped code passes
through unchanged.  The client is called with the translated codes
only, so the origin tag and the idempotency follow the Odoo codes:
two source items mapped to the same Odoo item at the same Odoo site
share one open draft.  Both the dry-run and the confirm responses
carry the codes actually sent to Odoo under ``target``
(``item_code``, ``site_code``, ``supplier_code``), every
``confirm=true`` audit row records the translated codes alongside the
request's codes, and a product, vendor or warehouse resolution failure
still answers 502 but its detail now names the code and whether it was
mapped (e.g. ``"product resolution failed: item '<code>' has no
Odoo mapping"`` when unmapped, ``"product resolution failed: mapped
item '<target>' not found in Odoo"`` when mapped); the 409 (writes
disabled), 422 and generic 502 paths are unchanged, and no credential
or Odoo payload ever appears in a detail.

The JSON body carries the three codes — non-empty strings of at most
255 characters — a ``quantity`` strictly greater than zero, a
``unit_price`` greater than or equal to zero, and a ``confirm`` flag
(default false).  A code longer than 255 characters is refused with
422 by request validation (a route dependency that enforces the auth
gate first and then runs before the body is model-validated — an
unauthenticated overlong body is a 401, never a 422, even when the
router is mounted without the app's mount-time gate), before any
mapping lookup, Odoo call or audit row; its detail names the field
without echoing the overlong input, while every other 422 (blank
code, NUL byte, quantity, unit_price, malformed body) keeps the
standard FastAPI envelope.
With ``confirm`` false the route only
previews through
:meth:`app.feed.odoo_client.OdooClient.preview_draft_purchase_order` —
read-only: only ``search_read`` reads, never a create, and no
consultation of the write switch — and answers ``200`` with
``dry_run`` true, ``would_create``, the origin tag, the resolved ids,
and the existing draft order when one already carries the origin. A
dry run is open to any signed-in user.

With ``confirm`` true the route requires the admin role: a signed-in
non-admin is refused with 403 before any Odoo call and nothing is
recorded. Otherwise it calls
:meth:`app.feed.odoo_client.OdooClient.create_draft_purchase_order`
under a per-``(item_code, site_code)`` lock — the module-level lock
registry for one process plus a per-``(item_code, site_code)``
PostgreSQL session-level advisory lock on its own connection (see
:mod:`app.repositories.draft_po_lock`), so two concurrent confirms for
the same item-site cannot both create across several API worker
processes — and answers ``200`` with ``dry_run`` false and the
client's ``{odoo_id, name, state, already_existed}``.  A confirm that
cannot take the advisory lock within
``draft_po_lock.DRAFT_PO_LOCK_TIMEOUT_MS`` milliseconds answers 409
with the fixed "Another draft order for this item-site is in
progress": the Odoo create is never called and no audit row is
written.

Every ``confirm=true`` request writes exactly one row to the
append-only ``draft_po_actions`` audit table after the Odoo call
settles (via
:class:`app.repositories.draft_po_actions_postgres_repository.DraftPoActionPostgresRepository`)
— except the 409 advisory-lock-timeout path, which never calls Odoo
and writes no row — the actor (token subject and display name), the
request's item/site/supplier/quantity/unit_price, and the outcome —
``created``
(with the Odoo id and name) when the client returned
``already_existed`` false, ``already_existed`` (with the Odoo id and
name) when true, or ``error`` with ``error_class`` set to the raised
exception's class name on the 409 / 502 / 422 paths (the HTTP response
of each path is unchanged).  A ``confirm=false`` dry run writes no
row.  No credential, environment value, or Odoo payload is stored.
If the audit insert itself fails the request answers 500 — a draft PO
already created stays a draft, and a retry is idempotent and records
its own row.  Every ``confirm`` true request also logs one structured
audit line via the app logger (the action, the item/site/supplier/
quantity, and the outcome — the created id and ``already_existed``,
or the error class); no credential, environment value, or request body
beyond those fields is logged.

``GET /api/replenishment/actions/draft-po/audit`` lists the stored
audit rows newest first (``created_at`` DESC, ``id`` DESC) and is
ADMIN-ONLY like the confirm: a signed-in non-admin is refused with
403.  ``limit`` defaults to 50 and is bounded 1..200 (else 422).
"""

import logging
import threading
from typing import Self

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, model_validator

from app.auth.dependencies import require_authenticated_user
from app.auth.display_name import display_name
from app.auth.models import AuthUser
from app.feed.odoo_client import (
    OdooClient,
    OdooError,
    OdooWritesDisabledError,
)
from app.models.draft_po_actions import DraftPoAction
from app.repositories.draft_po_actions_postgres_repository import (
    DraftPoActionPostgresRepository,
)
from app.repositories.draft_po_lock import (
    DraftPoLockTimeout,
    hold_draft_po_lock,
)
from app.repositories.erp_code_mappings_postgres_repository import (
    ErpCodeMappingPostgresRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/replenishment/actions", tags=["replenishment"])

_actions_repo = DraftPoActionPostgresRepository()
_mappings_repo = ErpCodeMappingPostgresRepository()


def require_any_authenticated_user(
    current_user: AuthUser = Depends(require_authenticated_user),
) -> AuthUser:
    """The replenishment routes' own authentication dependency.

    The routes take the authenticated user through this wrapper —
    exactly as ``planner_decisions.py`` does — so the app's
    include-time gate stays the single applied gate: the wrapper
    answers 401 without a valid Bearer token even when the router
    is mounted without the gate, and passes the authenticated user
    through unchanged.
    """
    return current_user


# Per-(target item code, target site code) serialization for confirms,
# kept in a fixed-size stripe of locks: the builtin hash of the pair
# picks the stripe, so two confirms for the same Odoo item-site pair
# always take the same lock and cannot both create, while a stream of
# distinct (even hostile) pairs can never grow the registry — its
# memory footprint is constant no matter how many item-site pairs are
# ever confirmed. The lock is keyed on the translated (Odoo) codes so
# source codes mapped to the same Odoo item-site share the lock as
# well. Distinct pairs may share a stripe; that is harmless
# contention, never lost serialization. The hash only needs to be
# consistent within one process, so the interpreter's hash
# randomization is irrelevant.
_SITE_LOCKS: tuple[threading.Lock, ...] = tuple(threading.Lock() for _ in range(64))


def _site_lock(item_code: str, site_code: str) -> threading.Lock:
    """Return the lock serializing confirms for one Odoo item-site pair."""
    return _SITE_LOCKS[hash((item_code, site_code)) % len(_SITE_LOCKS)]


_NUL = chr(0)


class DraftPoBody(BaseModel):
    """Payload for the draft purchase order action.

    The three codes must be non-empty, NUL-free strings of at most
    255 characters; ``quantity`` must be a number strictly greater
    than zero and ``unit_price`` a number greater than or equal to
    zero (a violation is a 422).
    ``confirm`` defaults to false: the request only previews what a
    confirm would do. The route never accepts a caller-supplied origin.
    """

    item_code: str = Field(min_length=1, max_length=255)
    site_code: str = Field(min_length=1, max_length=255)
    supplier_code: str = Field(min_length=1, max_length=255)
    quantity: float = Field(gt=0)
    unit_price: float = Field(ge=0)
    confirm: bool = False

    @model_validator(mode="after")
    def _check_codes(self) -> Self:
        """Reject a NUL-containing code with a 422, never a 500.

        The codes are read from and written to PostgreSQL (the
        translation lookup and the confirm audit row), where NUL bytes
        are an error; refusing them here keeps a hostile code out
        before any database call.
        """
        for field in ("item_code", "site_code", "supplier_code"):
            if _NUL in getattr(self, field):
                raise ValueError(f"{field} must not contain NUL bytes")
        return self


# The three request code fields and the erp_code_mappings kind each
# one translates as, for target system "odoo".
_CODE_KINDS: tuple[tuple[str, str], ...] = (
    ("item_code", "item"),
    ("site_code", "site"),
    ("supplier_code", "vendor"),
)

# The request's code bound: DraftPoBody's max_length, re-checked below
# during request validation so an overlong code is refused before any
# mapping lookup, Odoo call or audit row.
_MAX_CODE_LENGTH = 255


async def _reject_overlong_codes(
    request: Request,
    current_user: AuthUser = Depends(require_authenticated_user),
) -> None:
    """Refuse overlong request codes with a 422 that never echoes them.

    Runs as a route dependency — request validation — so it fires
    before the body is model-validated and before any mapping lookup,
    Odoo call or audit row.  Its ``require_authenticated_user``
    dependency is resolved first, so the auth gate precedes the bound
    check no matter how the router is mounted: an unauthenticated
    overlong body is a 401, never a 422.  The standard FastAPI 422
    envelope would carry the full caller-controlled value in each
    error's ``input``; the overlong case therefore answers with the
    same envelope minus the ``input``: the field name and the bound,
    never the value.  Bodies that are not JSON objects (malformed, or
    a JSON scalar or array) are left to the standard validation,
    which answers 422 exactly as before.
    """
    try:
        payload = await request.json()
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    errors = []
    for field, _kind in _CODE_KINDS:
        value = payload.get(field)
        if isinstance(value, str) and len(value) > _MAX_CODE_LENGTH:
            errors.append(
                {
                    "type": "string_too_long",
                    "loc": ("body", field),
                    "msg": f"String should have at most {_MAX_CODE_LENGTH} characters",
                    "ctx": {"max_length": _MAX_CODE_LENGTH},
                }
            )
    if errors:
        raise RequestValidationError(errors)


def _translate_codes(
    body: DraftPoBody,
) -> tuple[dict[str, str], dict[str, bool]]:
    """Translate the request's codes to the Odoo target codes.

    Each code is looked up as its kind for target system ``odoo``
    through the M1 repository's ``get_target``: a mapped code is
    replaced by its stored ``target_code`` and an unmapped code passes
    through unchanged.  Returns the translated codes keyed by the
    request's field name, and a flag per field for whether it was
    mapped.
    """
    targets: dict[str, str] = {}
    mapped: dict[str, bool] = {}
    for field, kind in _CODE_KINDS:
        source_code = getattr(body, field)
        target_code = _mappings_repo.get_target(kind, "odoo", source_code)
        targets[field] = target_code if target_code is not None else source_code
        mapped[field] = target_code is not None
    return targets, mapped


def _upstream_detail(
    exc: OdooError, targets: dict[str, str], mapped: dict[str, bool]
) -> str:
    """A fixed 502 message for an Odoo failure.

    Names only which resolution failed (product, vendor, or
    warehouse).  A resolution failure additionally names the code the
    client was called with and whether it was mapped — "item
    '<code>' has no Odoo mapping" when unmapped (the request's code),
    or "mapped item '<target>' not found in Odoo" when mapped (the
    target code), with the same wording for vendor and warehouse
    (supplier and site respectively); anything else gets a generic
    upstream message. No payload, credential, or environment value is
    ever included.
    """
    message = str(exc)
    for resolution, prefixes, field, noun in (
        (
            "product",
            (
                "no product matches",
                "multiple products match",
                "the product matching",
            ),
            "item_code",
            "item",
        ),
        (
            "vendor",
            (
                "no vendor matches",
                "multiple vendors match",
                "the vendor matching",
            ),
            "supplier_code",
            "supplier",
        ),
        (
            "warehouse",
            (
                "no warehouse matches",
                "multiple warehouses match",
                "the warehouse matching",
            ),
            "site_code",
            "site",
        ),
    ):
        if message.startswith(prefixes):
            if mapped[field]:
                return (
                    f"{resolution} resolution failed: mapped {noun} "
                    f"'{targets[field]}' not found in Odoo"
                )
            return (
                f"{resolution} resolution failed: {noun} "
                f"'{targets[field]}' has no Odoo mapping"
            )
    return "upstream Odoo request failed"


def _audit(
    body: DraftPoBody, *, result: dict | None = None, error: Exception | None = None
) -> None:
    """Log one structured audit line for a confirmed draft purchase order.

    The line carries the action, the item/site/supplier/quantity, and
    the outcome — the created id and ``already_existed`` on success, or
    the error class on failure. No credential, environment value, or
    request body beyond those fields is logged.
    """
    if error is not None:
        logger.info(
            "replenishment draft-po confirm action=draft_purchase_order "
            "item_code=%s site_code=%s supplier_code=%s quantity=%s "
            "outcome=error error=%s",
            body.item_code,
            body.site_code,
            body.supplier_code,
            body.quantity,
            type(error).__name__,
        )
        return
    logger.info(
        "replenishment draft-po confirm action=draft_purchase_order "
        "item_code=%s site_code=%s supplier_code=%s quantity=%s "
        "outcome=created odoo_id=%s already_existed=%s",
        body.item_code,
        body.site_code,
        body.supplier_code,
        body.quantity,
        result["odoo_id"],
        result["already_existed"],
    )


def _record_action(
    body: DraftPoBody,
    user: AuthUser,
    targets: dict[str, str],
    *,
    outcome: str,
    odoo_id: int | None = None,
    odoo_name: str | None = None,
    error_class: str | None = None,
) -> None:
    """Append one row to the persistent draft_po_actions audit.

    Stores the actor (the token subject and its display name), the
    request's item/site/supplier/quantity/unit_price, the translated
    target codes the client was called with, and the outcome — the
    Odoo id and name on ``created`` / ``already_existed``, or the
    exception class on ``error``. No credential, environment value, or
    Odoo payload is stored.  When the insert fails the exception
    propagates and the request answers 500: a draft PO already created
    stays a draft, and a retry is idempotent and records its own row.
    """
    _actions_repo.create(
        actor_sub=user.sub,
        actor_name=display_name(user),
        item_code=body.item_code,
        site_code=body.site_code,
        supplier_code=body.supplier_code,
        target_item_code=targets["item_code"],
        target_site_code=targets["site_code"],
        target_supplier_code=targets["supplier_code"],
        quantity=body.quantity,
        unit_price=body.unit_price,
        outcome=outcome,
        odoo_id=odoo_id,
        odoo_name=odoo_name,
        error_class=error_class,
    )


@router.post("/draft-po", dependencies=[Depends(_reject_overlong_codes)])
def draft_po(
    body: DraftPoBody,
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """Preview or draft one purchase order for an item-site-supplier.

    With ``confirm`` false (the default) the route only previews
    through ``OdooClient.preview_draft_purchase_order`` (read-only:
    only ``search_read`` reads, never a create, and no consultation of
    ``ODOO_ALLOW_WRITES``) and answers ``200`` with ``dry_run`` true,
    ``would_create``, the origin tag, the resolved ids, and ``existing``
    — the draft order already carrying the origin, or null. The dry
    run is open to any signed-in user.

    With ``confirm`` true the admin role is required: a signed-in
    non-admin is refused with 403 before any Odoo call and no audit
    row is written.  Otherwise the route calls
    ``OdooClient.create_draft_purchase_order`` under the per-
    ``(target item code, target site code)`` lock and, on its own
    connection, the per-``(target item code, target site code)``
    PostgreSQL advisory lock (so the serialization holds across API
    worker processes) and answers ``200`` with ``dry_run`` false and
    the client's ``{odoo_id, name, state, already_existed}``.  A
    confirm that cannot take the advisory lock within the timeout
    answers 409 with the fixed "Another draft order for this item-site
    is in progress" — the Odoo create is never called and no audit row
    is written.  After the Odoo call settles the route appends one row
    to the append-only ``draft_po_actions`` audit (the actor, the
    request's fields, the translated target codes, and the outcome —
    ``created`` or ``already_existed`` with the Odoo id and name, or
    ``error`` with the exception class); if that insert fails the
    request answers 500.  The origin is never caller-supplied: it is
    always the client's system-generated item-site default, computed
    from the translated codes, so two source codes mapped to the same
    Odoo item-site share one open draft.

    Errors (nothing leaks a secret or the payload): a confirm that
    cannot take the per-item-site advisory lock in time is a 409 with
    the fixed "Another draft order for this item-site is in progress"
    message; a disabled write switch on a confirm is a 409 with the
    fixed "Odoo writes are disabled" message; a resolution failure or any other Odoo error is
    a 502 naming which resolution failed — and, for a resolution
    failure, the code the client was called with and whether it was
    mapped — or a generic upstream message; a client ``ValueError`` is
    a 422.
    """
    if body.confirm and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Role 'admin' is required to perform this action.",
        )
    targets, mapped = _translate_codes(body)
    target_codes = {
        "item_code": targets["item_code"],
        "site_code": targets["site_code"],
        "supplier_code": targets["supplier_code"],
    }
    client = OdooClient()
    try:
        if not body.confirm:
            try:
                preview = client.preview_draft_purchase_order(
                    item_code=targets["item_code"],
                    site_code=targets["site_code"],
                    supplier_code=targets["supplier_code"],
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=str(exc),
                ) from None
            except OdooError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=_upstream_detail(exc, targets, mapped),
                ) from None
            return {
                "dry_run": True,
                "would_create": preview["existing"] is None,
                "origin": preview["origin"],
                "resolved": preview["resolved"],
                "existing": preview["existing"],
                "target": target_codes,
            }
        with _site_lock(targets["item_code"], targets["site_code"]):
            try:
                with hold_draft_po_lock(targets["item_code"], targets["site_code"]):
                    result = client.create_draft_purchase_order(
                        item_code=targets["item_code"],
                        site_code=targets["site_code"],
                        supplier_code=targets["supplier_code"],
                        quantity=body.quantity,
                        unit_price=body.unit_price,
                    )
            except DraftPoLockTimeout:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Another draft order for this item-site is in progress",
                ) from None
            except OdooWritesDisabledError as exc:
                _audit(body, error=exc)
                _record_action(
                    body,
                    current_user,
                    targets,
                    outcome="error",
                    error_class=type(exc).__name__,
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Odoo writes are disabled",
                ) from None
            except OdooError as exc:
                _audit(body, error=exc)
                _record_action(
                    body,
                    current_user,
                    targets,
                    outcome="error",
                    error_class=type(exc).__name__,
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=_upstream_detail(exc, targets, mapped),
                ) from None
            except ValueError as exc:
                _audit(body, error=exc)
                _record_action(
                    body,
                    current_user,
                    targets,
                    outcome="error",
                    error_class=type(exc).__name__,
                )
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=str(exc),
                ) from None
        _audit(body, result=result)
        _record_action(
            body,
            current_user,
            targets,
            outcome="created" if not result["already_existed"] else "already_existed",
            odoo_id=result["odoo_id"],
            odoo_name=result["name"],
        )
        return {"dry_run": False, **result, "target": target_codes}
    finally:
        client.close()


@router.get("/draft-po/audit", response_model=list[DraftPoAction])
def list_draft_po_actions(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> list[dict]:
    """List stored draft-PO confirm audit rows, newest first.

    ADMIN-ONLY, checked via ``current_user.is_admin`` exactly like the
    settings routes: a signed-in non-admin is refused with 403 and
    without a token with 401.  ``limit`` defaults to 50 and is bounded
    1..200 (else 422).  Rows are ordered by ``created_at`` DESC,
    ``id`` DESC, so equal timestamps order deterministically, and every
    column of the row is returned (``created_at`` as ISO-8601).
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Role 'admin' is required to perform this action.",
        )
    return _actions_repo.list_recent(limit)
