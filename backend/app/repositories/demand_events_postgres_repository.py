"""PostgreSQL repository for demand_events."""

from datetime import UTC, datetime
from uuid import uuid4

from app.db.connection import get_connection, get_cursor


def _row_to_dict(row) -> dict:
    d = dict(row)
    value = d.get("synced_at")
    if isinstance(value, datetime):
        d["synced_at"] = value.isoformat()
    return d


class DemandEventPostgresRepository:
    """PostgreSQL repository for the demand_events table."""

    def upsert_many(self, rows: list[dict]) -> dict:
        """Insert each row or, when a row with the same business_key
        already exists, update every other column and set synced_at to now.

        Every row is written in one transaction. The insert-or-update
        decision is made atomically by ON CONFLICT, so concurrent calls
        racing on the same new key serialize on the unique constraint and
        resolve as one insert plus one update instead of a unique
        violation. Returns the counts {"inserted": n, "updated": m}.
        """
        inserted = 0
        updated = 0
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                for row in rows:
                    occurred_on = row.get("occurred_on")
                    if isinstance(occurred_on, datetime):
                        occurred_on = occurred_on.date()
                    now = datetime.now(UTC)
                    cur.execute(
                        "INSERT INTO demand_events "
                        "(id, business_key, item_code, site_code, quantity, "
                        "occurred_on, event_type, source_system, synced_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (business_key) DO UPDATE SET "
                        "item_code = EXCLUDED.item_code, "
                        "site_code = EXCLUDED.site_code, "
                        "quantity = EXCLUDED.quantity, "
                        "occurred_on = EXCLUDED.occurred_on, "
                        "event_type = EXCLUDED.event_type, "
                        "source_system = EXCLUDED.source_system, "
                        "synced_at = %s "
                        "RETURNING (xmax = 0) AS inserted",
                        (
                            str(uuid4()),
                            row.get("business_key"),
                            row.get("item_code"),
                            row.get("site_code"),
                            row.get("quantity"),
                            occurred_on,
                            row.get("event_type"),
                            row.get("source_system"),
                            now,
                            now,
                        ),
                    )
                    if cur.fetchone()[0]:
                        inserted += 1
                    else:
                        updated += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"inserted": inserted, "updated": updated}

    def list_rows(self, limit: int, offset: int, **filters) -> list[dict]:
        """Rows ordered by business_key with optional filters on item_code,
        site_code, and inclusive occurred_on DATE bounds (occurred_from,
        occurred_to)."""
        clauses: list[str] = []
        values: list = []
        for key in ("item_code", "site_code"):
            if filters.get(key) is not None:
                clauses.append(f"{key} = %s")
                values.append(filters[key])
        if filters.get("occurred_from") is not None:
            clauses.append("occurred_on >= %s")
            values.append(filters["occurred_from"])
        if filters.get("occurred_to") is not None:
            clauses.append("occurred_on <= %s")
            values.append(filters["occurred_to"])
        sql = "SELECT * FROM demand_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY business_key LIMIT %s OFFSET %s"
        with get_cursor() as cur:
            cur.execute(sql, (*values, limit, offset))
            return [_row_to_dict(r) for r in cur.fetchall()]

    def get_by_business_key(self, business_key: str) -> dict | None:
        """The single row for one business_key, or None."""
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM demand_events WHERE business_key = %s",
                (business_key,),
            )
            row = cur.fetchone()
            return _row_to_dict(row) if row else None
