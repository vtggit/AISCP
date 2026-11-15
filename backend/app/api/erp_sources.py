"""The ERP sources that store item-sites, and their footprint.

``GET /api/erp-sources`` answers — with one read of ``item_sites`` and
no writes — which source systems store item-sites, how many item-sites
each stores and the newest ``synced_at`` of its rows, plus the
item-sites themselves ordered by (item_code, site_code).  It is open to
any signed-in user, mounted behind the app's authentication gate the
same way as the erp-mappings router, and answers 401 without a valid
Bearer token.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.erp_mappings import require_any_authenticated_user
from app.auth.models import AuthUser
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)


class ErpSource(BaseModel):
    """One source system's footprint in item_sites.

    ``source_system`` is the source's code or null, ``item_sites`` the
    number of item-sites it stores and ``last_synced_at`` the newest
    ``synced_at`` of its rows.
    """

    source_system: str | None
    item_sites: int
    last_synced_at: str


class ErpSourceItemSite(BaseModel):
    """One item-site: the (item_code, site_code) pair and the source
    system that stores it (a code or null)."""

    item_code: str
    site_code: str
    source_system: str | None


class ErpSourcesResponse(BaseModel):
    """The answer of ``GET /api/erp-sources``.

    ``sources`` is ordered by ``item_sites`` descending, then
    ``source_system`` with null last; ``item_sites`` by ``item_code``,
    then ``site_code``.
    """

    sources: list[ErpSource]
    item_sites: list[ErpSourceItemSite]


router = APIRouter(prefix="/api/erp-sources", tags=["erp-sources"])

_repo = ItemSitePostgresRepository()


@router.get("", response_model=ErpSourcesResponse)
def list_erp_sources(
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """The source systems that store item-sites, and the item-sites.

    ``sources`` lists each source system (a code or null) with the
    number of item-sites it stores and the newest ``synced_at`` of its
    rows, ordered by the count descending, then source system with null
    last; ``item_sites`` lists every row as (item_code, site_code,
    source_system), ordered by item_code then site_code.  One read of
    ``item_sites`` — one SQL statement, no writes — feeds both; with no
    item-sites, both lists are empty.
    """
    return _repo.source_summary()
