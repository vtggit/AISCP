"""PostgreSQL repository for open_purchase_lines."""

from datetime import UTC, datetime
from uuid import uuid4

from app.db.connection import get_connection, get_cursor
from app.db.read_cache import cached_read_method

# The newest sync of each source system: the inner subquery computes
# the greatest as_of per source system over every row of the table,
# and the outer query keeps the rows on that sync.
_LATEST_SQL = (
    "SELECT latest.id, latest.business_key, latest.item_code, "
    "latest.site_code, latest.supplier_code, latest.order_type, "
    "latest.open_units, latest.promised_on, latest.ordered_on, "
    "latest.as_of, latest.source_system, latest.synced_at "
    "FROM ( "
    "SELECT p.*, MAX(p.as_of) OVER (PARTITION BY p.source_system) "
    "AS source_max_as_of "
    "FROM open_purchase_lines AS p "
    ") AS latest "
    "WHERE latest.as_of = latest.source_max_as_of"
)
# The pair filter of ``list_latest(pairs=...)``: only the rows of the
# given (item_code, site_code) pairs, applied after the newest-sync
# selection, so a pair whose rows are all in an older sync of its
# source system gets none.
_PAIRS_SQL = " AND (latest.item_code, latest.site_code) IN %s"
_ORDER_SQL = " ORDER BY latest.item_code, latest.site_code, " "latest.business_key"


def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("as_of", "synced_at"):
        value = d.get(key)
        if isinstance(value, datetime):
            d[key] = value.isoformat()
    return d


class OpenPurchaseLinePostgresRepository:
    """PostgreSQL repository for the open_purchase_lines table."""

    def upsert_many(self, rows: list[dict]) -> dict:
        """Insert each row or, when a row with the same business_key
        already exists, update every other column and set synced_at to
        now.

        Every row is written in one transaction. The insert-or-update
        decision is made atomically by ON CONFLICT, so concurrent calls
        racing on the same new key serialize on the unique constraint and
        resolve as one insert plus one update instead of a unique
        violation. Last-fresh-wins applies by as_of: a conflict whose
        incoming as_of is older than the stored row's as_of leaves the
        stored row untouched, so an older ERP snapshot can never overwrite
        a newer current open purchase line; an equal as_of re-applies the
        same snapshot and is idempotent. Returns the counts {"inserted":
        n, "updated": m}; rows rejected as stale count in neither.
        """
        inserted = 0
        updated = 0
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                for row in rows:
                    now = datetime.now(UTC)
                    cur.execute(
                        "INSERT INTO open_purchase_lines "
                        "(id, business_key, item_code, site_code, supplier_code, "
                        "order_type, open_units, promised_on, ordered_on, as_of, "
                        "source_system, synced_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (business_key) DO UPDATE SET "
                        "item_code = EXCLUDED.item_code, "
                        "site_code = EXCLUDED.site_code, "
                        "supplier_code = EXCLUDED.supplier_code, "
                        "order_type = EXCLUDED.order_type, "
                        "open_units = EXCLUDED.open_units, "
                        "promised_on = EXCLUDED.promised_on, "
                        "ordered_on = EXCLUDED.ordered_on, "
                        "as_of = EXCLUDED.as_of, "
                        "source_system = EXCLUDED.source_system, "
                        "synced_at = %s "
                        "WHERE EXCLUDED.as_of >= open_purchase_lines.as_of "
                        "RETURNING (xmax = 0) AS inserted",
                        (
                            str(uuid4()),
                            row.get("business_key"),
                            row.get("item_code"),
                            row.get("site_code"),
                            row.get("supplier_code"),
                            row.get("order_type"),
                            row.get("open_units"),
                            row.get("promised_on"),
                            row.get("ordered_on"),
                            row.get("as_of"),
                            row.get("source_system"),
                            now,
                            now,
                        ),
                    )
                    fetched = cur.fetchone()
                    if fetched is None:
                        # Stale snapshot (older as_of than the stored
                        # row): the update was skipped and the row is
                        # untouched.
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
        return {"inserted": inserted, "updated": updated}

    @cached_read_method
    def list_latest(self, pairs=None) -> list[dict]:
        """The newest sync of each source system: for every
        source_system, the rows whose as_of equals that source system's
        greatest as_of, as dicts ordered by
        (item_code, site_code, business_key). Dates come back as ``date``
        and timestamps as ISO strings; an empty table returns ``[]``.

        With ``pairs`` — a tuple of ``(item_code, site_code)`` tuples —
        only the rows of those pairs are answered, in the same order
        and shape: the pair filter is in the SQL, so the other pairs'
        rows are never fetched, and the newest sync of each source
        system is still chosen over every row of the table, so a pair
        whose rows are all in an older sync of its source system gets
        none. An empty tuple answers ``[]`` without a query.  The pairs
        are hashable, so a ``read_once()`` block caches each distinct
        ``pairs``.
        """
        if pairs is None:
            sql = _LATEST_SQL + _ORDER_SQL
            params = None
        else:
            pair_tuples = tuple(pairs)
            if not pair_tuples:
                return []
            sql = _LATEST_SQL + _PAIRS_SQL + _ORDER_SQL
            params = (pair_tuples,)
        with get_cursor() as cur:
            cur.execute(sql, params)
            return [_row_to_dict(r) for r in cur.fetchall()]
