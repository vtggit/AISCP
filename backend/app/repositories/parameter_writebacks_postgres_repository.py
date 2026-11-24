"""PostgreSQL repository for parameter_writebacks — the append-only
ERP parameter writeback audit.

A parameter writeback row records one attempt to write the reorder
point and max quantity of one item-site back to its source ERP
system.  ``insert`` stores one row with a new uuid4 id and returns it
(the parameter values are stored exactly as given — no rounding or
reformatting — so a value such as 0.1000 round-trips exactly);
``list`` returns the stored rows newest first (written_at DESC, id
DESC), optionally narrowed by item_code and/or site_code, paged by
limit and offset.

``basis`` is what the writeback's values carried — "decision" or
"ddmrp" the two values the confirm stores from the preview row,
"excess" the one the check constraint admits for a writeback that
carries neither.  A caller that leaves ``basis`` out gets it
derived from the given ``decision_id``: "decision" when the
``decision_id`` is not null, "ddmrp" otherwise.

The table is append-only: there is deliberately no update or delete
method, and the migration's downgrade is a no-op, so the audit rows
survive a rollback.
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
