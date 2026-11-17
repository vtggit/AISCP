"""PostgreSQL repository for exception_ack_events.

One row per stored acknowledgement of one exceptions-list key (issue
#1350): the acknowledgement's key, subject, name, time and snooze, the
acknowledged row's kind, item and site, and the proposal
``GET /api/exceptions?include_snoozed=true&include_proposal=true``
answered for the row at acknowledgement time (the
``ExceptionProposal`` object in JSON, with every key, or SQL null
when nothing answered the row).  ``insert`` appends one event in one
INSERT, and ``counts`` answers the stored events grouped by kind, by
the snooze and by the proposal in one SELECT.  There is no update and
no delete: an event stands as it was stored — a second
acknowledgement of a key appends a second row, and an acknowledgement
cleared by ``DELETE /api/exceptions/ack`` keeps its rows.
"""

import json

import psycopg2
import psycopg2.extras

from app.db.connection import get_connection


class ExceptionAckEventPostgresRepository:
    """PostgreSQL repository for the exception_ack_events table."""

    def insert(
        self,
        *,
        key: str,
        kind: str,
        item_code: str | None,
        site_code: str | None,
        acknowledged_by: str,
        acknowledged_by_name: str | None,
        acknowledged_at,
        snooze_until,
        proposal: dict | None,
    ) -> dict:
        """Append one event for one stored acknowledgement and return
        the stored row.

        The proposal is stored as jsonb (``%s::jsonb`` with
        ``json.dumps``, the way the excess decision proposals store
        their remedy), so it stands as it was proposed: a later change
        of the stockout data never changes the stored row.  A proposal
        of None is stored as SQL null, never as the JSON value null.
        Committed on its own.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO exception_ack_events "
                    "(key, kind, item_code, site_code, acknowledged_by, "
                    "acknowledged_by_name, acknowledged_at, snooze_until, "
                    "proposal) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
                    "RETURNING *",
                    (
                        key,
                        kind,
                        item_code,
                        site_code,
                        acknowledged_by,
                        acknowledged_by_name,
                        acknowledged_at,
                        snooze_until,
                        (
                            json.dumps(proposal, default=str)
                            if proposal is not None
                            else None
                        ),
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

    def counts(self) -> list[dict]:
        """The stored events, grouped, in one read.

        Every stored row, in one SELECT, grouped by ``kind``, by
        whether ``snooze_until`` is set and by whether ``proposal`` is
        set: one dict per group, ``kind`` the group's kind, ``snoozed``
        and ``proposed`` booleans and ``acknowledged`` the group's
        number of rows (an int), in kind order.  ``[]`` for an empty
        table.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT kind, "
                    "(snooze_until IS NOT NULL) AS snoozed, "
                    "(proposal IS NOT NULL) AS proposed, "
                    "COUNT(*) AS acknowledged "
                    "FROM exception_ack_events "
                    "GROUP BY kind, (snooze_until IS NOT NULL), "
                    "(proposal IS NOT NULL) "
                    "ORDER BY kind, snoozed, proposed"
                )
                return [dict(row) for row in cur.fetchall()]
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
