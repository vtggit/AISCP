"""Cross-ERP code mappings: AIERP codes to target-system codes.

``GET /api/erp-mappings`` lists the stored mappings — ordered by kind
then source code, optionally narrowed by ``kind`` and
``target_system`` query filters — and is open to any signed-in user,
as is ``GET /api/erp-mappings/suggestions``: the suggested item
mappings, an Acumatica ``source_code`` and an Odoo ``target_code``
matched by code (the same item code held in ``item_sites`` under both
systems, at different sites) or by description (equal after trimming
and ignoring case), each pair its own row, the ambiguous ones flagged,
already-mapped codes left out, capped by an optional ``limit`` (1 to
1000, default 200, out of range 422) — and it writes nothing.
``PUT /api/erp-mappings`` stores one mapping: a new row, or the stored
target code of the existing one on the (kind, target_system,
source_code) unique key, and ``DELETE /api/erp-mappings/{id}`` removes
one by id; both need the admin role, a signed-in non-admin getting 403
exactly as the settings routes refuse a SYSTEM setting.  A kind outside
item/site/vendor, a target_system other than odoo, or a blank,
NUL-containing or over-255-character code answers 422 — in the PUT body
and in the GET query filters alike.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth.dependencies import require_authenticated_user
from app.auth.models import AuthUser
from app.models.erp_code_mappings import ErpMappingIn, ErpMappingOut
from app.recommend.item_matching import suggest_item_mappings
from app.repositories.erp_code_mappings_postgres_repository import (
    ErpCodeMappingPostgresRepository,
)

router = APIRouter(prefix="/api/erp-mappings", tags=["erp-mappings"])

_repo = ErpCodeMappingPostgresRepository()


def require_any_authenticated_user(
    current_user: AuthUser = Depends(require_authenticated_user),
) -> AuthUser:
    """The erp-mappings routes' own authentication dependency.

    The erp-mappings routes are open to any signed-in user (the
    admin-role check for PUT and DELETE happens in the handler, where it
    can tell the read-only GET apart, as in the settings routes): the
    dependency answers 401 without a valid Bearer token even when the
    router is mounted without the app's include-time gate, and passes
    the authenticated user through unchanged.
    """
    return current_user


def require_admin(
    current_user: AuthUser = Depends(require_authenticated_user),
) -> AuthUser:
    """PUT and DELETE need the admin role, exactly as a SYSTEM setting.

    The user is taken from ``require_authenticated_user`` (401 without a
    valid Bearer token, so this dependency answers 401 even when the
    router is mounted without the app's include-time gate), and a
    signed-in non-admin is refused with the 403 the settings routes use
    when they refuse a SYSTEM setting.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Role 'admin' is required to perform this action.",
        )
    return current_user


@router.get("", response_model=list[ErpMappingOut])
def list_mappings(
    kind: Literal["item", "site", "vendor"] | None = Query(default=None),
    target_system: Literal["odoo"] | None = Query(default=None),
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> list[dict]:
    """Every stored mapping, ordered by kind then source code.

    ``kind`` (item, site or vendor) and ``target_system`` (odoo) narrow
    the result when given; a value outside the allowed ones answers 422
    with the same rules as the PUT body, not an empty result.
    """
    return _repo.list(kind=kind, target_system=target_system)


class ErpMappingSuggestion(BaseModel):
    """One item-mapping suggestion as served by
    ``GET /api/erp-mappings/suggestions``.

    ``source_code`` is the Acumatica code and ``target_code`` the Odoo
    code, the way ``erp_code_mappings`` stores ``kind`` ``item`` with
    ``target_system`` ``odoo``.  ``matched_on`` is ``code`` (the same
    item code held in ``item_sites`` under both systems, at different
    sites) or ``description`` (the descriptions equal after trimming
    and ignoring case); ``ambiguous`` is true when one code's
    description matched several codes on the other side.  The
    descriptions are the stored values, null when the code has no
    ``inventory_items`` row under its side.
    """

    source_code: str
    target_code: str
    matched_on: Literal["code", "description"]
    source_description: str | None
    target_description: str | None
    ambiguous: bool


@router.get("/suggestions", response_model=list[ErpMappingSuggestion])
def suggest_mappings(
    limit: int = Query(default=200, ge=1, le=1000),
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> list[dict]:
    """The suggested item mappings, read-only, for any signed-in user.

    A code held in ``item_sites`` under both ``acumatica`` and ``odoo``
    at different sites suggests itself (``matched_on`` ``code``); a
    code in ``inventory_items`` under ``acumatica`` and a different
    code there under ``odoo`` whose descriptions are equal after
    trimming and ignoring case suggest the pair (``matched_on``
    ``description``).  A null, empty or only-spaces description never
    matches — no similarity score, no partial match, no unit of
    measure.  One code whose description matched several on the other
    side answers one suggestion per pair, each ``ambiguous`` true.  A
    code already mapped — a stored item mapping to ``odoo`` as its
    ``source_code``, or any mapping's ``target_code`` — is left out.
    The answer is ordered by ``matched_on`` with ``code`` first, then
    ``source_code``, then ``target_code``; ``limit`` (1 to 1000,
    default 200) caps it, and out of range answers 422.  The route
    writes nothing.
    """
    return suggest_item_mappings(limit=limit)


@router.put("", response_model=ErpMappingOut)
def store_mapping(
    body: ErpMappingIn,
    current_user: AuthUser = Depends(require_admin),
) -> dict:
    """Store one mapping; needs the admin role (403 otherwise).

    A new (kind, target_system, source_code) inserts a row; an existing
    one updates its target code and updated_at instead of creating a
    duplicate.  Returns the stored row.
    """
    return _repo.upsert(
        kind=body.kind,
        target_system=body.target_system,
        source_code=body.source_code,
        target_code=body.target_code,
    )


@router.delete("/{mapping_id}", status_code=204)
def delete_mapping(
    mapping_id: int,
    current_user: AuthUser = Depends(require_admin),
) -> None:
    """Remove one mapping by id; needs the admin role (403 otherwise).

    204 with no body when a row was removed, 404 when no mapping has
    the id.
    """
    if not _repo.delete(mapping_id):
        raise HTTPException(status_code=404, detail=f"Unknown mapping: {mapping_id}")
    return None
