"""PostgreSQL repository for excess_decision_proposals.

One row per excess decision that stored a remedy proposal: the
remedy ``GET /api/excess?include_remedy=true`` proposed for the
item-site's ranked row when the decision was recorded (issue #1336),
the ``ExcessRemedy`` object in JSON, with every key.  ``insert``
stores one proposal with the stored decision's id in one INSERT and
``for_decisions`` answers the stored remedies of a batch of decisions
in one SELECT.  There is no update and no delete: a proposal stands
as it was proposed, and it goes away with its decision (the foreign
key's ON DELETE CASCADE).
"""

import json

import psycopg2
import psycopg2.extras

from app.db.connection import get_connection


class ExcessDecisionProposalPostgresRepository:
    """PostgreSQL repository for the excess_decision_proposals table."""

    def insert(self, decision_id: str, proposed_remedy: dict) -> dict:
        """Store one proposal with its decision's id and return the
        stored row.

        The remedy is stored as jsonb (``%s::jsonb`` with
        ``json.dumps``, the way the S&OP sign-offs store their
        figures), so it stands as it was proposed: a later change of
        the excess data never changes the stored row.  Committed on
        its own.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO excess_decision_proposals "
                    "(decision_id, proposed_remedy) "
                    "VALUES (%s, %s::jsonb) RETURNING *",
                    (decision_id, json.dumps(proposed_remedy, default=str)),
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def for_decisions(self, decision_ids: list[str]) -> dict[str, dict]:
        """The stored remedy of every decision id given, in one read.

        Answered as a dict of decision id to stored remedy (the
        ``proposed_remedy`` jsonb, as stored), and ``{}`` for an empty
        list: a decision whose proposal was never stored — no remedy
        was proposed, or storing it failed — is simply absent.
        """
        if not decision_ids:
            return {}
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                placeholders = ", ".join(["%s"] * len(decision_ids))
                cur.execute(
                    "SELECT decision_id, proposed_remedy "
                    "FROM excess_decision_proposals "
                    f"WHERE decision_id IN ({placeholders})",
                    tuple(decision_ids),
                )
                return {
                    row["decision_id"]: row["proposed_remedy"] for row in cur.fetchall()
                }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
