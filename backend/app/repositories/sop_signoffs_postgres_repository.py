"""PostgreSQL repository for sop_signoffs — the S&OP sign-offs.

One row per sign-off, written by ``POST /api/sop/signoffs``: ``month``
the signed review month, ``note`` the signer's note (null when the
request carried a blank one), ``figures`` the exact S&OP review answer
the route computed on the server at sign-off time (jsonb) and
``actor``/``actor_name`` the caller's username (or subject) and display
name.  Rows are append-only: ``create`` inserts and no read changes or
removes a row — a second sign-off of a month writes a second row,
leaving the first untouched.  ``list_newest`` and ``latest_for_month``
answer the row without its figures and order by ``created_at DESC,
id DESC`` so equal-timestamp rows list deterministically (the higher
id first); ``get_by_id`` and ``latest_with_figures`` are the reads
that answer the figures (``latest_with_figures`` the newest row of
any month under the same order); ``latest_proposals_per_month``
answers, for the latest sign-off of each month under that same
order, the month and the stored ``figures -> 'signoff_proposal'``
(``None`` when the key is absent) in one query that reads no other
part of the figures.
"""

import json
from datetime import datetime

import psycopg2.extras

from app.db.connection import get_connection, get_cursor

# The columns the list and latest reads answer without figures.
_ROW_WITHOUT_FIGURES = "id, month, note, actor, actor_name, created_at"


def _row_to_dict(row) -> dict:
    """One stored row, with ``created_at`` as an ISO-8601 string."""
    d = dict(row)
    value = d.get("created_at")
    if isinstance(value, datetime):
        d["created_at"] = value.isoformat()
    return d


class SopSignoffsPostgresRepository:
    """PostgreSQL repository for the sop_signoffs table."""

    def create(
        self,
        *,
        month: str,
        note: str | None,
        figures: dict,
        actor: str,
        actor_name: str | None,
    ) -> dict:
        """Insert one sign-off row and return the stored row.

        ``figures`` is the exact S&OP review answer the route computed
        on the server — stored as jsonb, it stands as it did before
        the signing, so a later budget or projection change never
        changes the stored row.  ``note`` is null when the request
        carried a blank one.  Committed on its own.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO sop_signoffs "
                    "(month, note, figures, actor, actor_name) "
                    "VALUES (%s, %s, %s::jsonb, %s, %s) RETURNING *",
                    (
                        month,
                        note,
                        json.dumps(figures, default=str),
                        actor,
                        actor_name,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
            return _row_to_dict(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_newest(self, month: str | None = None, limit: int = 50) -> list[dict]:
        """The newest stored rows without their figures, at most
        ``limit`` of them.

        ``month`` narrows the list to one review month when given.
        Ordered by ``created_at DESC, id DESC`` so equal-timestamp
        rows list deterministically (insertion order reversed).
        """
        with get_cursor() as cur:
            if month is None:
                cur.execute(
                    f"SELECT {_ROW_WITHOUT_FIGURES} FROM sop_signoffs "
                    "ORDER BY created_at DESC, id DESC LIMIT %s",
                    (limit,),
                )
            else:
                cur.execute(
                    f"SELECT {_ROW_WITHOUT_FIGURES} FROM sop_signoffs "
                    "WHERE month = %s ORDER BY created_at DESC, id DESC LIMIT %s",
                    (month, limit),
                )
            return [_row_to_dict(r) for r in cur.fetchall()]

    def get_by_id(self, signoff_id: int) -> dict | None:
        """The stored row with that id — the one read that answers
        the figures — or ``None`` when no row carries it."""
        with get_cursor() as cur:
            cur.execute("SELECT * FROM sop_signoffs WHERE id = %s", (signoff_id,))
            row = cur.fetchone()
        return None if row is None else _row_to_dict(row)

    def latest_for_month(self, month: str) -> dict | None:
        """The newest stored row of ``month`` without its figures, or
        ``None`` when the month carries no sign-off.

        The first row of the month under ``created_at DESC, id DESC``
        — the row the S&OP review reports as ``latest_signoff``.
        """
        with get_cursor() as cur:
            cur.execute(
                f"SELECT {_ROW_WITHOUT_FIGURES} FROM sop_signoffs "
                "WHERE month = %s ORDER BY created_at DESC, id DESC LIMIT 1",
                (month,),
            )
            row = cur.fetchone()
        return None if row is None else _row_to_dict(row)

    def latest_with_figures(self) -> dict | None:
        """The newest stored row of any month, with its figures,
        or ``None`` when no sign-off has been stored at all.

        The first row under ``created_at DESC, id DESC`` — the same
        order ``list_newest`` answers — the row the S&OP review
        reports as ``since_signoff``.
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM sop_signoffs "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            )
            row = cur.fetchone()
        return None if row is None else _row_to_dict(row)

    def latest_proposals_per_month(self) -> list[dict]:
        """The latest stored sign-off of each signed month, as one
        ``{month, proposal}`` entry per month, newest month first.

        The latest sign-off of a month is the first row of the month
        under ``created_at DESC, id DESC`` — the ``id`` breaking equal
        timestamps.  ``proposal`` is the stored ``figures ->
        'signoff_proposal'``: ``None`` when the key is absent or
        stored as null, the stored object otherwise.  One query — and
        it reads only that key of the figures, never the rest.
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (month) month, "
                "figures -> 'signoff_proposal' AS proposal "
                "FROM sop_signoffs "
                "ORDER BY month DESC, created_at DESC, id DESC"
            )
            return [dict(row) for row in cur.fetchall()]
