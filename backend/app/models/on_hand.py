"""Pydantic models for on_hand — current on-hand inventory by item and site.

Each row is the latest quantity the ERP reported for one item at one site,
keyed by (item_code, site_code). Rows are synced from the ERP by that key,
which is the idempotency key of the sync; as_of is the snapshot time the
ERP data is current as of, and last-fresh-wins: a snapshot with an older
as_of never overwrites a newer stored row. quantity_on_hand may be
negative: ERPs report negative stock and the read model must not hide it.
"""

from pydantic import BaseModel


class OnHandUpsert(BaseModel):
    """Describes one on_hand row as supplied by the sync source."""

    item_code: str
    site_code: str
    quantity_on_hand: float
    as_of: str
    source_system: str | None = None


class OnHandResponse(OnHandUpsert):
    """Response model for a stored on_hand row."""

    id: str
    synced_at: str
