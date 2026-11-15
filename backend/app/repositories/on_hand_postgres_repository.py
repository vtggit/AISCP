"""PostgreSQL repository for on_hand."""

from datetime import UTC, datetime
from uuid import uuid4

from app.db.connection import get_connection, get_cursor


def _refused_by_source(cur, item_code, site_code, source_system, as_of) -> bool:
    """Whether the conflict for the pair was refused by the source
    guard: the stored row is fresh enough for the offered as_of (so
    the as_of condition alone would not have skipped it) and carries
    a different source_system.  A stale snapshot — or no stored row
    at all — is not a refusal."""
    cur.execute(
        "SELECT (%s >= as_of) AND (source_system IS DISTINCT FROM %s) "
        "AS refused FROM on_hand "
        "WHERE item_code = %s AND site_code = %s",
        (as_of, source_system, item_code, site_code),
    )
    row = cur.fetchone()
    return bool(row and row[0])


def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("as_of", "synced_at"):
        value = d.get(key)
        if isinstance(value, datetime):
            d[key] = value.isoformat()
    return d


class OnHandPostgresRepository:
    """PostgreSQL repository for the on_hand table."""

    def upsert_many(self, rows: list[dict]) -> dict:
        """Insert each row or, when a row with the same (item_code,
        site_code) already exists, update every other column and set
        synced_at to now.

        Every row is written in one transaction. The insert-or-update
        decision is made atomically by ON CONFLICT, so concurrent calls
        racing on the same new key serialize on the unique constraint and
        resolve as one insert plus one update instead of a unique
        violation. Last-fresh-wins applies by as_of: a conflict whose
        incoming as_of is older than the stored row's as_of leaves the
        stored row untouched, so an older ERP snapshot can never overwrite
        a newer current on-hand row; an equal as_of re-applies the same
        snapshot and is idempotent. The update also runs only when the
        stored row's source_system matches the offered one (IS NOT
        DISTINCT FROM, so a null matches a null), joined with the as_of
        condition by AND: a conflict stored under another source_system
        is refused, leaves the stored row untouched, and the offer counts
        in neither "inserted" nor "updated" — and then in "refused".  A
        stale snapshot is never a refusal and its behaviour is unchanged.
        Returns the counts {"inserted": n, "updated": m}; "refused", the
        number of refused rows, is present only when it is above 0.
        """
        inserted = 0
        updated = 0
        refused = 0
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                for row in rows:
                    now = datetime.now(UTC)
                    cur.execute(
                        "INSERT INTO on_hand "
                        "(id, item_code, site_code, quantity_on_hand, as_of, "
                        "source_system, synced_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (item_code, site_code) DO UPDATE SET "
                        "quantity_on_hand = EXCLUDED.quantity_on_hand, "
                        "as_of = EXCLUDED.as_of, "
                        "source_system = EXCLUDED.source_system, "
                        "synced_at = %s "
                        "WHERE on_hand.source_system "
                        "IS NOT DISTINCT FROM EXCLUDED.source_system "
                        "AND EXCLUDED.as_of >= on_hand.as_of "
                        "RETURNING (xmax = 0) AS inserted",
                        (
                            str(uuid4()),
                            row.get("item_code"),
                            row.get("site_code"),
                            row.get("quantity_on_hand"),
                            row.get("as_of"),
                            row.get("source_system"),
                            now,
                            now,
                        ),
                    )
                    fetched = cur.fetchone()
                    if fetched is None:
                        # The update was skipped and the stored row is
                        # untouched: either a stale snapshot (older as_of
                        # than the stored row), which counts in neither,
                        # as before, or a conflict stored under another
                        # source_system, refused by the guard.
                        if _refused_by_source(
                            cur,
                            row.get("item_code"),
                            row.get("site_code"),
                            row.get("source_system"),
                            row.get("as_of"),
                        ):
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

    def list_rows(self, limit: int, offset: int, **filters) -> list[dict]:
        """Rows ordered by (item_code, site_code) with optional filters on
        item_code and site_code."""
        clauses: list[str] = []
        values: list = []
        for key in ("item_code", "site_code"):
            if filters.get(key) is not None:
                clauses.append(f"{key} = %s")
                values.append(filters[key])
        sql = "SELECT * FROM on_hand"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY item_code, site_code LIMIT %s OFFSET %s"
        with get_cursor() as cur:
            cur.execute(sql, (*values, limit, offset))
            return [_row_to_dict(r) for r in cur.fetchall()]

    def get_row(self, item_code: str, site_code: str) -> dict | None:
        """The single row for one (item_code, site_code), or None."""
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM on_hand " "WHERE item_code = %s AND site_code = %s",
                (item_code, site_code),
            )
            row = cur.fetchone()
            return _row_to_dict(row) if row else None
