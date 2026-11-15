"""PostgreSQL repository for ddmrp_adjustments.

A DDMRP adjustment factor scales the average daily usage of one
item-site (item_code and site_code both set) or of every stored
buffer (both null) between start_on and end_on (inclusive).  The
rows carry no uniqueness constraint on the window: the API keeps
the windows of one scope disjoint — its store takes a transactional
advisory lock on the scope, and the overlap check and the insert
hold one transaction (an overlapping window of the same scope
answers 409), so a scope holds at most one row whose window contains
a given date, and the zone rows read exactly one active factor per
scope per as-of date.

created_by stores the token subject and created_by_name the
caller's display name (null when the caller has none), the way
ddmrp_buffers records designated_by and designated_by_name.
created_at is left to the column's server default (now()).
"""

import hashlib
import struct
from datetime import date

import psycopg2.extras

from app.db.connection import get_connection, get_cursor


class OverlappingAdjustmentError(Exception):
    """The new window overlaps a stored window of the same scope.

    Carries the stored overlapping row (its id, its codes and its
    window) so the API can answer 409 with it — never with database
    text.
    """

    def __init__(self, conflict: dict):
        super().__init__("ddmrp_adjustment_overlap")
        self.conflict = conflict


def _scope_lock_key(item_code: str | None, site_code: str | None) -> int:
    """The advisory-lock key of one adjustment scope.

    The first four bytes of the SHA-256 of the scope's NUL-joined
    encoding, read as a big-endian signed 32-bit integer — the same
    value in every process and interpreter, always within
    PostgreSQL's ``int4`` range, and different for different scopes
    in practice, the way the draft-PO lock joins its pair (the
    all-buffer scope joins two empty codes, which an item-site scope
    cannot: its codes are never empty).
    """
    joined = "\x00\x00" if item_code is None else f"{item_code}\x00{site_code}"
    digest = hashlib.sha256(joined.encode()).digest()
    return struct.unpack(">i", digest[:4])[0]


class DdmrpAdjustmentPostgresRepository:
    """PostgreSQL repository for the ddmrp_adjustments table."""

    def list_all(self) -> list[dict]:
        """Every stored adjustment, ordered by start_on, with the
        all-buffer rows (both codes null) first on equal dates."""
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM ddmrp_adjustments "
                "ORDER BY start_on, (item_code IS NOT NULL), end_on, id"
            )
            return [dict(row) for row in cur.fetchall()]

    def list_active_on(self, day: date) -> list[dict]:
        """Every stored adjustment whose window contains ``day``
        (start_on and end_on inclusive), newest start first."""
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM ddmrp_adjustments "
                "WHERE start_on <= %s AND end_on >= %s "
                "ORDER BY start_on DESC, end_on DESC",
                (day, day),
            )
            return [dict(row) for row in cur.fetchall()]

    def insert(
        self,
        *,
        id: str,
        item_code: str | None,
        site_code: str | None,
        start_on: date,
        end_on: date,
        factor: float,
        note: str | None,
        created_by: str,
        created_by_name: str | None,
    ) -> dict:
        """Store one adjustment and return the stored row.

        created_by stores the token subject and created_by_name the
        caller's display name (null when the caller has none);
        created_at is left to the column's server default (now()).
        The window's disjointness within a scope is enforced by
        ``insert_if_disjoint`` (one transaction, serialized on the
        scope's advisory lock), never by this table.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO ddmrp_adjustments "
                    "(id, item_code, site_code, start_on, end_on, factor, "
                    "note, created_by, created_by_name) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING *",
                    (
                        id,
                        item_code,
                        site_code,
                        start_on,
                        end_on,
                        factor,
                        note,
                        created_by,
                        created_by_name,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def insert_if_disjoint(
        self,
        *,
        id: str,
        item_code: str | None,
        site_code: str | None,
        start_on: date,
        end_on: date,
        factor: float,
        note: str | None,
        created_by: str,
        created_by_name: str | None,
    ) -> dict:
        """Store one adjustment unless a stored window of the same
        scope overlaps [start_on, end_on] (start_on and end_on
        inclusive).

        The overlap check and the insert hold one transaction, and
        the store first takes a transactional advisory lock on the
        scope's key: a concurrent store of the same scope that
        commits first is seen as the conflict by the one that waited
        on the lock, so the check can never race the insert and the
        windows of one scope never overlap — across processes, not
        just within one.

        Returns the stored row, and raises
        ``OverlappingAdjustmentError`` — carrying the stored
        overlapping row — when the window overlaps.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (_scope_lock_key(item_code, site_code),),
                )
                if item_code is None:
                    cur.execute(
                        "SELECT id, item_code, site_code, start_on, end_on "
                        "FROM ddmrp_adjustments "
                        "WHERE item_code IS NULL AND site_code IS NULL "
                        "AND start_on <= %s AND end_on >= %s "
                        "LIMIT 1",
                        (end_on, start_on),
                    )
                else:
                    cur.execute(
                        "SELECT id, item_code, site_code, start_on, end_on "
                        "FROM ddmrp_adjustments "
                        "WHERE item_code = %s AND site_code = %s "
                        "AND start_on <= %s AND end_on >= %s "
                        "LIMIT 1",
                        (item_code, site_code, end_on, start_on),
                    )
                conflict = cur.fetchone()
                if conflict is not None:
                    conn.rollback()
                    raise OverlappingAdjustmentError(dict(conflict))
                cur.execute(
                    "INSERT INTO ddmrp_adjustments "
                    "(id, item_code, site_code, start_on, end_on, factor, "
                    "note, created_by, created_by_name) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING *",
                    (
                        id,
                        item_code,
                        site_code,
                        start_on,
                        end_on,
                        factor,
                        note,
                        created_by,
                        created_by_name,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def delete(self, adjustment_id: str) -> bool:
        """Remove the stored adjustment with id ``adjustment_id``.

        Returns True when a stored adjustment was removed and False
        when there is no such adjustment.
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM ddmrp_adjustments WHERE id = %s",
                    (adjustment_id,),
                )
                removed = cur.rowcount > 0
            conn.commit()
            return removed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
