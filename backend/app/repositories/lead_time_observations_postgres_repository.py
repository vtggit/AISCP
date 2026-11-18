"""PostgreSQL repository for lead_time_observations."""

from datetime import UTC, datetime
from uuid import uuid4

from app.db.connection import get_connection, get_cursor
from app.db.read_cache import cached_read_method


def _row_to_dict(row) -> dict:
    d = dict(row)
    value = d.get("synced_at")
    if isinstance(value, datetime):
        d["synced_at"] = value.isoformat()
    return d


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    return value


class LeadTimeObservationPostgresRepository:
    """PostgreSQL repository for the lead_time_observations table."""

    def upsert_many(self, rows: list[dict]) -> dict:
        """Insert each row or, when a row with the same business_key
        already exists, update every other column and set synced_at to now.

        Every row is written in one transaction. The insert-or-update
        decision is made atomically by ON CONFLICT, so concurrent calls
        racing on the same new key serialize on the unique constraint and
        resolve as one insert plus one update instead of a unique
        violation. lead_time_days is never written: it is a generated
        column the database computes from received_on - ordered_on.
        Returns the counts {"inserted": n, "updated": m}.
        """
        inserted = 0
        updated = 0
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                for row in rows:
                    now = datetime.now(UTC)
                    cur.execute(
                        "INSERT INTO lead_time_observations "
                        "(id, business_key, supplier_code, item_code, site_code, "
                        "ordered_on, received_on, promised_on, source_system, "
                        "synced_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (business_key) DO UPDATE SET "
                        "supplier_code = EXCLUDED.supplier_code, "
                        "item_code = EXCLUDED.item_code, "
                        "site_code = EXCLUDED.site_code, "
                        "ordered_on = EXCLUDED.ordered_on, "
                        "received_on = EXCLUDED.received_on, "
                        "promised_on = EXCLUDED.promised_on, "
                        "source_system = EXCLUDED.source_system, "
                        "synced_at = %s "
                        "RETURNING (xmax = 0) AS inserted",
                        (
                            str(uuid4()),
                            row.get("business_key"),
                            row.get("supplier_code"),
                            row.get("item_code"),
                            row.get("site_code"),
                            _as_date(row.get("ordered_on")),
                            _as_date(row.get("received_on")),
                            _as_date(row.get("promised_on")),
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

    @cached_read_method
    def list_rows(self, limit: int, offset: int, **filters) -> list[dict]:
        """Rows ordered by business_key with optional filters on
        supplier_code and item_code."""
        clauses: list[str] = []
        values: list = []
        for key in ("supplier_code", "item_code"):
            if filters.get(key) is not None:
                clauses.append(f"{key} = %s")
                values.append(filters[key])
        sql = "SELECT * FROM lead_time_observations"
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
                "SELECT * FROM lead_time_observations WHERE business_key = %s",
                (business_key,),
            )
            row = cur.fetchone()
            return _row_to_dict(row) if row else None
