"""PostgreSQL repository for erp_code_mappings — cross-ERP code mappings.

A mapping row binds one source code to the code of the same thing in
one target system, per (kind, target_system, source_code) — the
table's unique key, so ``upsert`` updates the stored target code (and
bumps ``updated_at``) on an existing triple instead of inserting a
duplicate.  ``created_at`` and ``updated_at`` are written by the
application as aware UTC datetimes and returned as ISO-8601 strings,
as the other repositories do.
"""

from datetime import UTC, datetime

from app.db.connection import get_cursor


def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("created_at", "updated_at"):
        if d.get(key) and isinstance(d[key], datetime):
            d[key] = d[key].isoformat()
    return d


class ErpCodeMappingPostgresRepository:
    """PostgreSQL repository for the erp_code_mappings table."""

    def list(
        self,
        kind: str | None = None,
        target_system: str | None = None,
    ) -> list[dict]:
        """Every stored mapping, ordered by kind then source_code.

        ``kind`` and/or ``target_system`` narrow the result when given;
        both None lists every mapping.
        """
        sql = "SELECT * FROM erp_code_mappings"
        clauses: list[str] = []
        params: list[str] = []
        if kind is not None:
            clauses.append("kind = %s")
            params.append(kind)
        if target_system is not None:
            clauses.append("target_system = %s")
            params.append(target_system)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY kind, source_code"
        with get_cursor() as cur:
            cur.execute(sql, tuple(params))
            return [_row_to_dict(r) for r in cur.fetchall()]

    def get_target(
        self,
        kind: str,
        target_system: str,
        source_code: str,
    ) -> str | None:
        """The stored target code for one mapping, or None when unmapped."""
        with get_cursor() as cur:
            cur.execute(
                "SELECT target_code FROM erp_code_mappings "
                "WHERE kind = %s AND target_system = %s AND source_code = %s",
                (kind, target_system, source_code),
            )
            row = cur.fetchone()
            return None if row is None else row["target_code"]

    def upsert(
        self,
        kind: str,
        target_system: str,
        source_code: str,
        target_code: str,
    ) -> dict:
        """Store one mapping and return the stored row.

        A new (kind, target_system, source_code) inserts a row with both
        timestamps at now; an existing one updates its target_code and
        bumps updated_at — the id and created_at are kept, so no
        duplicate row is ever created.
        """
        now = datetime.now(UTC)
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO erp_code_mappings "
                "(kind, target_system, source_code, target_code, created_at, "
                "updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (kind, target_system, source_code) DO UPDATE "
                "SET target_code = EXCLUDED.target_code, updated_at = %s "
                "RETURNING *",
                (kind, target_system, source_code, target_code, now, now, now),
            )
            return _row_to_dict(cur.fetchone())

    def delete(self, mapping_id: int) -> bool:
        """Delete one mapping by id; True when a row was deleted."""
        with get_cursor() as cur:
            cur.execute("DELETE FROM erp_code_mappings WHERE id = %s", (mapping_id,))
            return cur.rowcount > 0
