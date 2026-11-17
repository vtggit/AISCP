"""PostgreSQL repository for forecast_override_proposals.

One row per forecast override that stored a correction proposal: the
correction ``GET /api/forecast/by-key?include_correction=true``
proposed for the item-site when the override was stored (issue
#1365), the ``ForecastCorrection`` object in JSON, with every key.
``insert`` stores one proposal with the stored override's id in one
INSERT and ``for_overrides`` answers the stored corrections of a
batch of overrides in one SELECT.  There is no update and no delete,
the way the overrides have none [rule: OVR-2]: a proposal stands as
it was proposed, and it goes away with its override (the foreign
key's ON DELETE CASCADE).
"""

import json

import psycopg2
import psycopg2.extras

from app.db.connection import get_connection


class ForecastOverrideProposalPostgresRepository:
    """PostgreSQL repository for the forecast_override_proposals table."""

    def insert(self, override_id: int, proposed_correction: dict) -> dict:
        """Store one proposal with its override's id and return the
        stored row.

        The correction is stored as jsonb (``%s::jsonb`` with
        ``json.dumps``, the way the excess decision proposals store
        their remedies), so it stands as it was proposed: a later
        change of the forecast never changes the stored row.
        Committed on its own.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO forecast_override_proposals "
                    "(override_id, proposed_correction) "
                    "VALUES (%s, %s::jsonb) RETURNING *",
                    (override_id, json.dumps(proposed_correction, default=str)),
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def for_overrides(self, override_ids: list[int]) -> dict[int, dict]:
        """The stored correction of every override id given, in one
        read.

        Answered as a dict of override id to stored correction (the
        ``proposed_correction`` jsonb, as stored), and ``{}`` for an
        empty list: an override whose proposal was never stored — no
        correction was proposed, or storing it failed — is simply
        absent.
        """
        if not override_ids:
            return {}
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                placeholders = ", ".join(["%s"] * len(override_ids))
                cur.execute(
                    "SELECT override_id, proposed_correction "
                    "FROM forecast_override_proposals "
                    f"WHERE override_id IN ({placeholders})",
                    tuple(override_ids),
                )
                return {
                    row["override_id"]: row["proposed_correction"]
                    for row in cur.fetchall()
                }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
