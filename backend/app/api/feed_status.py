"""Read-only API for the freshness of the OData feed tables.

``GET /api/feed/status`` reports, per feed table, the newest
``synced_at`` and the row count, the newest and oldest of those (a gap
between the two shows a partial sync), and the newest
``demand_events.occurred_on`` as the data horizon.  The router is
mounted with the app's ``dependencies=auth_gate`` in ``app.main`` like
the other data routers: 401 without a token, 200 for any signed-in
role.  The route itself carries no admin-only control.
"""

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from app.feed.status import feed_status

router = APIRouter(prefix="/api/feed", tags=["feed-status"])


class FeedTableStatus(BaseModel):
    """Freshness of one feed table."""

    name: str
    synced_at: str | None
    rows: int


class FeedStatusResponse(BaseModel):
    """Freshness of the five feed tables and the data horizon."""

    tables: list[FeedTableStatus]
    synced_at: str | None
    oldest_synced_at: str | None
    data_through: date | None


@router.get("/status", response_model=FeedStatusResponse)
def get_feed_status() -> dict:
    """Freshness of the five OData feed tables (read-only, no admin gate)."""
    return feed_status()
