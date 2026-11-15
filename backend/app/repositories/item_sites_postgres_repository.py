"""PostgreSQL repository for item_sites."""

from datetime import UTC, datetime
from uuid import uuid4

from app.db.connection import get_connection, get_cursor

_DATA_COLUMNS = (
    "unit_cost",
    "reorder_point",
    "safety_stock",
    "max_qty",
    "order_qty",
    "master_lead_time_days",
    "source_system",
)


def _row_to_dict(row) -> dict:
    d = dict(row)
    value = d.get("synced_at")
    if isinstance(value, datetime):
        d["synced_at"] = value.isoformat()
    return d


class ItemSitePostgresRepository:
    """PostgreSQL repository for the item_sites table."""

    def upsert_many(self, rows: list[dict]) -> dict:
        """Insert each row or, when (item_code, site_code) already exists,
        update every other column and set synced_at to now — for the row's
        own source system only.

        Every row is written in one transaction. The insert-or-update
        decision is made atomically by ON CONFLICT, so concurrent calls
        racing on the same new key serialize on the unique constraint and
        resolve as one insert plus one update instead of a unique
        violation. The update runs only when the stored row's
        source_system matches the offered one (IS NOT DISTINCT FROM, so a
        null matches a null): a conflict stored under another
        source_system is refused, leaves the stored row untouched, and the
        offer counts in neither "inserted" nor "updated". Returns the
        counts {"inserted": n, "updated": m}; "refused", the number of
        refused rows, is present only when it is above 0.
        """
        inserted = 0
        updated = 0
        refused = 0
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                for row in rows:
                    item_code = row.get("item_code")
                    site_code = row.get("site_code")
                    now = datetime.now(UTC)
                    data_values = tuple(row.get(column) for column in _DATA_COLUMNS)
                    cur.execute(
                        "INSERT INTO item_sites "
                        "(id, item_code, site_code, unit_cost, reorder_point, "
                        "safety_stock, max_qty, order_qty, master_lead_time_days, "
                        "source_system, synced_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (item_code, site_code) DO UPDATE SET "
                        "unit_cost = EXCLUDED.unit_cost, "
                        "reorder_point = EXCLUDED.reorder_point, "
                        "safety_stock = EXCLUDED.safety_stock, "
                        "max_qty = EXCLUDED.max_qty, "
                        "order_qty = EXCLUDED.order_qty, "
                        "master_lead_time_days = EXCLUDED.master_lead_time_days, "
                        "source_system = EXCLUDED.source_system, "
                        "synced_at = %s "
                        "WHERE item_sites.source_system "
                        "IS NOT DISTINCT FROM EXCLUDED.source_system "
                        "RETURNING (xmax = 0) AS inserted",
                        (
                            str(uuid4()),
                            item_code,
                            site_code,
                            *data_values,
                            now,
                            now,
                        ),
                    )
                    fetched = cur.fetchone()
                    if fetched is None:
                        # Another ERP's row: the stored row's
                        # source_system differs from the offered one, so
                        # the update was skipped and the row is untouched.
                        refused += 1
                        continue
                    if fetched[0]:
                        inserted += 1
                    else:
                        updated += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        result = {"inserted": inserted, "updated": updated}
        if refused:
            result["refused"] = refused
        return result

    def source_summary(self) -> dict:
        """The source systems that store item-sites, and the item-sites
        themselves.

        One read of item_sites — one SQL statement, no writes — feeds
        both answers: ``sources`` groups the rows by source_system (a
        null is its own group) with the group's number of item-sites
        and the newest synced_at of its rows, ordered by the count
        descending, then source_system with null last; ``item_sites``
        is every row as (item_code, site_code, source_system), ordered
        by item_code, then site_code.
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT item_code, site_code, source_system, synced_at "
                "FROM item_sites ORDER BY item_code, site_code"
            )
            rows = cur.fetchall()

        item_sites: list[dict] = []
        per_source: dict[str | None, dict] = {}
        for row in rows:
            item_sites.append(
                {
                    "item_code": row["item_code"],
                    "site_code": row["site_code"],
                    "source_system": row["source_system"],
                }
            )
            entry = per_source.setdefault(
                row["source_system"], {"item_sites": 0, "last_synced_at": None}
            )
            entry["item_sites"] += 1
            synced_at = row["synced_at"]
            if entry["last_synced_at"] is None or synced_at > entry["last_synced_at"]:
                entry["last_synced_at"] = synced_at

        sources = [
            {
                "source_system": source_system,
                "item_sites": entry["item_sites"],
                "last_synced_at": (
                    entry["last_synced_at"].isoformat()
                    if entry["last_synced_at"] is not None
                    else None
                ),
            }
            for source_system, entry in per_source.items()
        ]
        sources.sort(
            key=lambda source: (
                -source["item_sites"],
                source["source_system"] is None,
                source["source_system"] or "",
            )
        )
        return {"sources": sources, "item_sites": item_sites}

    def held_by_others(self, source_system: str, pairs: list[tuple[str, str]]) -> dict:
        """For the given (item_code, site_code) pairs, the source_system
        that stores each in item_sites under a different source_system.

        Every pair is answered by one SQL statement, so the held set is
        a single consistent snapshot: pairs stored under
        ``source_system`` itself (or not stored at all) are absent from
        the result, and the answer maps each held pair to the holder.
        """
        if not pairs:
            return {}
        pair_values = [value for pair in pairs for value in pair]
        placeholders = ", ".join("(%s, %s)" for _ in pairs)
        with get_cursor() as cur:
            cur.execute(
                "SELECT item_code, site_code, source_system FROM item_sites "
                "WHERE (item_code, site_code) IN "
                "(SELECT v.item_code, v.site_code "
                f"FROM (VALUES {placeholders}) AS v(item_code, site_code)) "
                "AND source_system <> %s",
                (*pair_values, source_system),
            )
            return {
                (row["item_code"], row["site_code"]): row["source_system"]
                for row in cur.fetchall()
            }

    def list_rows(self, limit: int, offset: int, **filters) -> list[dict]:
        """Rows ordered by (item_code, site_code) with optional key filters."""
        clauses: list[str] = []
        values: list = []
        for key in ("item_code", "site_code"):
            if filters.get(key) is not None:
                clauses.append(f"{key} = %s")
                values.append(filters[key])
        sql = "SELECT * FROM item_sites"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY item_code, site_code LIMIT %s OFFSET %s"
        with get_cursor() as cur:
            cur.execute(sql, (*values, limit, offset))
            return [_row_to_dict(r) for r in cur.fetchall()]

    def get_by_key(self, item_code: str, site_code: str) -> dict | None:
        """The single row for one (item_code, site_code) pair, or None."""
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM item_sites WHERE item_code = %s AND site_code = %s",
                (item_code, site_code),
            )
            row = cur.fetchone()
            return _row_to_dict(row) if row else None
