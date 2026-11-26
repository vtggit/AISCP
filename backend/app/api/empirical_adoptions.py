"""Empirical adoptions: one stored adoption per item-site.

``PUT /api/empirical-adoptions`` (admin only) stores the adoption of
one item-site, or replaces the stored one when the pair already has
it: the pair must be a row of ``item_sites``, validated at the
application level (the table has no foreign key to ``item_sites``),
and a pair that is not in ``item_sites`` answers 404.  The stored row
records ``adopted_by`` as the token subject and ``adopted_by_name``
as its display name, exactly as planner decisions record
``decided_by`` and ``decided_by_name``.

``GET /api/empirical-adoptions`` (any authenticated user) lists every
stored adoption ordered by item_code then site_code, and
``DELETE /api/empirical-adoptions?item_code=…&site_code=…`` (admin
only) withdraws one: 204 when a stored adoption was removed, 404
when the pair had none.
"""

from datetime import datetime
from typing import Self

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.api.validation import require_storable_subject
from app.auth.authorization import require_role
from app.auth.display_name import display_name
from app.auth.models import AuthUser
from app.repositories.empirical_adoptions_postgres_repository import (
    EmpiricalAdoptionPostgresRepository,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)

router = APIRouter(prefix="/api/empirical-adoptions", tags=["empirical-adoptions"])

_repo = EmpiricalAdoptionPostgresRepository()
_item_sites = ItemSitePostgresRepository()

_NUL = chr(0)


class EmpiricalAdoption(BaseModel):
    """One stored empirical adoption for an item-site."""

    item_code: str
    site_code: str
    adopted_by: str
    adopted_by_name: str | None = None
    adopted_at: datetime
    note: str | None = None


class EmpiricalAdoptionBody(BaseModel):
    """The body of PUT /api/empirical-adoptions.

    Every business rule is a model validation, so a rejected body
    answers 422 in FastAPI's HTTPValidationError envelope, exactly like
    a malformed one.
    """

    item_code: str = Field(..., max_length=64)
    site_code: str = Field(..., max_length=64)
    note: str | None = None

    @model_validator(mode="after")
    def _check_business_rules(self) -> Self:
        for value in (self.item_code, self.site_code, self.note):
            if value is not None and _NUL in value:
                raise ValueError("must not contain NUL bytes")
        return self


class _AdoptionKey(BaseModel):
    """The DELETE query pair; a NUL byte in either code answers 422 in
    FastAPI's HTTPValidationError envelope (raised in the dependency
    below)."""

    item_code: str = Field(..., max_length=64)
    site_code: str = Field(..., max_length=64)

    @model_validator(mode="after")
    def _check_business_rules(self) -> Self:
        for value in (self.item_code, self.site_code):
            if _NUL in value:
                raise ValueError("must not contain NUL bytes")
        return self


def adoption_key(
    item_code: str = Query(..., max_length=64),
    site_code: str = Query(..., max_length=64),
) -> tuple[str, str]:
    """The query pair of DELETE /api/empirical-adoptions.

    Both codes are required (one without the other, or a NUL byte in
    either, answers 422 in FastAPI's HTTPValidationError envelope).
    """
    try:
        _AdoptionKey(item_code=item_code, site_code=site_code)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    return item_code, site_code


def _require_item_site(item_code: str, site_code: str) -> None:
    """The pair must be a row of item_sites.

    empirical_adoptions has no foreign key to item_sites (and no
    ON DELETE CASCADE): the item-site's existence is validated here at
    the application level, and a pair that is not in item_sites
    answers 404.
    """
    if _item_sites.get_by_key(item_code, site_code) is None:
        raise HTTPException(status_code=404, detail="item site not found")


@router.get("", response_model=list[EmpiricalAdoption])
def list_empirical_adoptions() -> list[dict]:
    """List every stored adoption, ordered by item_code then site_code.

    Open to any authenticated user (the router is mounted behind the
    app's authentication gate).
    """
    return _repo.list_all()


@router.put(
    "",
    response_model=EmpiricalAdoption,
    responses={404: {"description": "The item-site is not in item_sites."}},
)
def upsert_empirical_adoption(
    body: EmpiricalAdoptionBody,
    user: AuthUser = Depends(require_role("admin")),
) -> dict:
    """Store the adoption of one item-site, or replace the stored one.

    Admin only.  The pair must be a row of item_sites, validated at
    the application level (a pair that is not in item_sites answers
    404).  A second adoption for the same pair replaces the stored
    row — its note, its adopter and the adoption time — without
    adding a second row.  The stored row records adopted_by as the
    token subject and adopted_by_name as its display name, exactly as
    planner decisions record decided_by and decided_by_name.
    """
    require_storable_subject(user)
    _require_item_site(body.item_code, body.site_code)
    return _repo.upsert(
        item_code=body.item_code,
        site_code=body.site_code,
        adopted_by=user.sub,
        adopted_by_name=display_name(user),
        note=body.note,
    )


@router.delete(
    "",
    status_code=204,
    dependencies=[Depends(require_role("admin"))],
    responses={404: {"description": "The pair has no stored adoption."}},
)
def delete_empirical_adoption(
    key: tuple[str, str] = Depends(adoption_key),
) -> None:
    """Withdraw the stored adoption of one item-site.

    Admin only.  Answers 204 when a stored adoption was removed and
    404 when the pair had none.
    """
    item_code, site_code = key
    if not _repo.delete(item_code, site_code):
        raise HTTPException(
            status_code=404, detail="no empirical adoption for item site"
        )
