"""Client for AIDW's OData v4 feed.

See :mod:`app.feed.aidw_client` for the paged, fail-closed reader used to
pull entity sets from the feed.
"""

from app.feed.aidw_client import (
    AidwFeedConfigError,
    AidwFeedError,
    iter_entities,
)

__all__ = [
    "AidwFeedConfigError",
    "AidwFeedError",
    "iter_entities",
]
