"""PostgreSQL repository for erp_source_currencies.

One row per (source_system, currency_code) pair: the distinct
uppercase ISO-4217 currency codes the source system reports in, with
``seen_at`` the newest sync that saw the code (the table's ``now()``
server default on insert, refreshed by a replace that keeps the code).
A sync replaces the source's whole set in one transaction: the codes
the sync no longer reports are dropped, the ones it still reports keep
their row (with ``seen_at`` refreshed), and the new set is what the
table stores for that source afterwards.
"""

from app.db.connection import get_connection, get_cursor


def _normalized_codes(codes: object) -> list[str]:
    """The uppercase ISO-4217 codes of *codes*, deduplicated and
    sorted: non-strings and empty (or whitespace-only) values
    contribute nothing, and 'usd' and 'USD' normalize to one code."""
    normalized: set[str] = set()
    for code in codes or []:
        if isinstance(code, str):
            value = code.strip().upper()
            if value:
                normalized.add(value)
    return sorted(normalized)


class ErpSourceCurrencyPostgresRepository:
    """PostgreSQL repository for the erp_source_currencies table."""

    def replace_for(self, source_system: str, codes: list[str]) -> dict:
        """Replace the codes stored for *source_system* with *codes*.

        Every stored row of the source is deleted and the normalized
        codes (uppercase ISO-4217, deduplicated, sorted) are
        re-inserted — all in one transaction, so a failure leaves the
        stored set exactly as it was.  A code the source stored before
        and still reports comes back with a fresh ``seen_at``.
        Returns the counts ``{"removed": n, "stored": m}``.
        """
        normalized = _normalized_codes(codes)
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM erp_source_currencies " "WHERE source_system = %s",
                    (source_system,),
                )
                removed = cur.rowcount
                for code in normalized:
                    cur.execute(
                        "INSERT INTO erp_source_currencies "
                        "(source_system, currency_code) "
                        "VALUES (%s, %s)",
                        (source_system, code),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"removed": removed, "stored": len(normalized)}

    def list_all(self) -> list[dict]:
        """Every stored row, ordered by source system then code.

        Each row carries ``source_system``, ``currency_code`` and the
        ``seen_at`` timestamp (a timezone-aware datetime).
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT source_system, currency_code, seen_at "
                "FROM erp_source_currencies "
                "ORDER BY source_system, currency_code"
            )
            return [dict(row) for row in cur.fetchall()]
