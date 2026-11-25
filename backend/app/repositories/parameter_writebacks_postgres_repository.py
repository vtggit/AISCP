"""PostgreSQL repository for parameter_writebacks — the append-only
ERP parameter writeback audit.

A parameter writeback row records one attempt to write the reorder
point and max quantity of one item-site back to its source ERP
system.  ``insert`` stores one row with a new uuid4 id and returns it
(the parameter values are stored exactly as given — no rounding or
reformatting — so a value such as 0.1000 round-trips exactly);
``insert_pending`` stores one row the same way, except that its
``outcome`` is always "pending", its before and after values are
null, and its ``error`` is null — a given ``outcome`` raises
``ValueError``; ``update_outcome`` sets the outcome, the before and
after values and the error on the one stored row with the given id
whose outcome is still "pending", returning True when it changed the
row and False otherwise — ``written_at`` is not touched, and the
row's outcome changes once, from "pending"; ``list`` returns the
stored rows newest first (written_at DESC, id DESC), optionally
narrowed by item_code and/or site_code, paged by limit and offset.

``basis`` is what the writeback's values carried — "decision" or
"ddmrp" the two values the confirm stores from the preview row,
"excess" the one the check constraint admits for a writeback that
carries neither.  A caller that leaves ``basis`` out gets it
derived from the given ``decision_id``: "decision" when the
``decision_id`` is not null, "ddmrp" otherwise.

The table is append-only: a row is never deleted — there is
deliberately no delete method, and the migration's downgrade
rewrites no row away, so the audit rows survive a rollback — and a
row's outcome changes once, from "pending": ``insert_pending``
stores the row before the ERP call, ``update_outcome`` sets the
writer's answer or error on the same row after it, and a row whose
outcome is still "pending" is a write whose result is unknown.
"""

import uuid

import psycopg2.extras

from app.db.connection import get_connection, get_cursor

# Every column insert() may be given, besides the generated id.
_FIELDS = frozenset(
    {
        "item_code",
        "site_code",
        "source_system",
        "decision_id",
        "basis",
        "before_reorder_point",
        "before_max_qty",
        "after_reorder_point",
        "after_max_qty",
        "requested_reorder_point",
        "requested_max_qty",
        "outcome",
        "error",
        "written_by",
        "written_by_name",
        "written_at",
    }
)


class ParameterWritebacksPostgresRepository:
    """PostgreSQL repository for the parameter_writebacks table."""

    def insert(self, **fields) -> dict:
        """Store one audit row and return the stored row.

        The row gets a new uuid4 id.  written_at is left to the
        column's server default (now()) unless it is given.  Every
        other value is stored exactly as given, with no rounding or
        reformatting.  A given basis is stored exactly as given; one
        left out is derived from the given decision_id — "decision"
        when it is not null, "ddmrp" otherwise.
        """
        if "basis" not in fields:
            fields = dict(fields)
            fields["basis"] = (
                "decision" if fields.get("decision_id") is not None else "ddmrp"
            )
        unknown = set(fields) - _FIELDS
        if unknown:
            raise ValueError(
                f"unknown parameter_writebacks field(s): {sorted(unknown)}"
            )
        record = {"id": str(uuid.uuid4()), **fields}
        columns = ", ".join(record)
        placeholders = ", ".join(["%s"] * len(record))
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"INSERT INTO parameter_writebacks ({columns}) "
                    f"VALUES ({placeholders}) RETURNING *",
                    tuple(record.values()),
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def insert_pending(self, **fields) -> dict:
        """Store one audit row with outcome "pending" and return it.

        Stored exactly the way ``insert`` stores a row — a new uuid4
        id, every other value exactly as given, written_at left to
        the column's server default unless it is given — except that
        the outcome is "pending", the before and after values are
        null, and the error is null: the row is the confirm's record
        of an attempt that has not been answered by the ERP yet.  A
        given outcome is never stored: it raises ``ValueError``
        instead, because the outcome of a row stored this way is set
        later, by ``update_outcome``.
        """
        if "outcome" in fields:
            raise ValueError("outcome is set by insert_pending and may not be given")
        fields = dict(fields)
        fields["outcome"] = "pending"
        fields["before_reorder_point"] = None
        fields["before_max_qty"] = None
        fields["after_reorder_point"] = None
        fields["after_max_qty"] = None
        fields["error"] = None
        return self.insert(**fields)

    def update_outcome(
        self,
        writeback_id: str,
        *,
        outcome: str,
        before_reorder_point: float | None,
        before_max_qty: float | None,
        after_reorder_point: float | None,
        after_max_qty: float | None,
        error: str | None,
    ) -> bool:
        """Set the outcome, the before and after values and the error
        on the one stored row with the given id.

        The update touches only the row whose outcome is still
        "pending" — the writer's answer or error sets a row's
        outcome once, from "pending" — and leaves ``written_at``
        untouched.  Returns True when it changed a row, False when
        the row is not there or its outcome was already set.
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE parameter_writebacks "
                    "SET outcome = %s, before_reorder_point = %s, "
                    "before_max_qty = %s, after_reorder_point = %s, "
                    "after_max_qty = %s, error = %s "
                    "WHERE id = %s AND outcome = 'pending'",
                    (
                        outcome,
                        before_reorder_point,
                        before_max_qty,
                        after_reorder_point,
                        after_max_qty,
                        error,
                        writeback_id,
                    ),
                )
                changed = cur.rowcount == 1
            conn.commit()
            return changed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list(
        self,
        limit: int = 100,
        offset: int = 0,
        item_code: str | None = None,
        site_code: str | None = None,
    ) -> list[dict]:
        """The stored audit rows, newest first.

        Ordered by written_at DESC with id DESC breaking ties (two
        rows may share a written_at), narrowed to the given item_code
        and/or site_code when given, and paged by limit and offset.
        """
        sql = "SELECT * FROM parameter_writebacks"
        clauses: list[str] = []
        params: list = []
        if item_code is not None:
            clauses.append("item_code = %s")
            params.append(item_code)
        if site_code is not None:
            clauses.append("site_code = %s")
            params.append(site_code)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY written_at DESC, id DESC LIMIT %s OFFSET %s"
        params.extend((limit, offset))
        with get_cursor() as cur:
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]
