"""PostgreSQL repository for plan_releases — the release audit.

Every confirmed release inserts one row per (site, supplier) group,
written ``pending`` before the Acumatica batch call, then updates each
row to the batch's per-group outcome.  Rows are never deleted;
``list_newest`` lists them newest first for the audit route.

The release confirm additionally serializes on a session-level
PostgreSQL advisory lock (``pg_try_advisory_lock`` on the fixed key
``RELEASE_LOCK_KEY``) taken on its own connection and held for the
duration of the release — so the "one confirm at a time" guarantee
holds across several API worker processes, not just one: a second
confirm that cannot take the lock is refused 409 by the route.
"""

import json
from datetime import datetime
from decimal import Decimal

from app.db.connection import get_connection, get_cursor

# The fixed advisory-lock key the release confirm serializes on.
RELEASE_LOCK_KEY = 538

# The outcomes whose rows tie a stored line snapshot to a PO that
# exists in Acumatica under the row's origin: created (the write
# landed), exists (an order carried the origin then) or aborted (a
# mid-batch auth or transport failure after the write landed).
_SNAPSHOT_OUTCOMES = ("created", "exists", "aborted")


def _json_default(obj: object) -> str:
    """The exact decimal spelling of a Decimal in the line snapshot.

    The snapshot is the exact record of what was sent, so its
    monetary fields are stored as exact decimal strings — never
    floating-point numbers — and any other non-serializable shape
    fails the insert instead of being silently re-shaped.
    """
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")


def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("created_at", "updated_at"):
        value = d.get(key)
        if isinstance(value, datetime):
            d[key] = value.isoformat()
    return d


def acquire_release_lock():
    """Try to take the release advisory lock on its own connection.

    Runs ``pg_try_advisory_lock(RELEASE_LOCK_KEY)`` on a fresh
    connection and returns that connection — the lock is held by the
    session until the connection is closed or unlocked, so the caller
    must hold it for the whole release and pass it back to
    :func:`release_lock` in a ``finally``.  Returns ``None`` when
    another session already holds the lock; nothing is acquired.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (RELEASE_LOCK_KEY,))
            acquired = bool(cur.fetchone()[0])
        if not acquired:
            conn.close()
            return None
        return conn
    except Exception:
        conn.close()
        raise


def release_lock(conn) -> None:
    """Unlock the release advisory lock and close its connection.

    Best effort: an unlock failure never replaces the caller's
    exception, and the connection is closed either way — the session
    end would release the lock with it.
    """
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (RELEASE_LOCK_KEY,))
            conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


class PlanReleasePostgresRepository:
    """PostgreSQL repository for the plan_releases table."""

    def insert_pending(
        self,
        *,
        month: str,
        site_code: str,
        supplier_code: str,
        origin: str,
        lines: list[dict],
        value: Decimal,
        actor: str,
    ) -> int:
        """Insert one ``pending`` release row and return its id.

        The row stores the group's identity (month, site, supplier,
        origin tag), the exact line snapshot sent to Acumatica (jsonb),
        its line count and value, and the confirming actor.  It is
        committed on its own, so the row is visible to other sessions
        — including the Acumatica batch call — while the release is in
        flight.
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO plan_releases "
                    "(month, site_code, supplier_code, origin, outcome, "
                    "line_count, value, lines, actor) "
                    "VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s::jsonb, %s) "
                    "RETURNING id",
                    (
                        month,
                        site_code,
                        supplier_code,
                        origin,
                        len(lines),
                        value,
                        json.dumps(lines, default=_json_default),
                        actor,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
            return row[0]
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_outcome(
        self,
        release_id: int,
        *,
        outcome: str,
        order_nbr: str | None = None,
        warehouse_code: str | None = None,
        vendor_code: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Set one row's outcome, order number, codes and error by id.

        Bumps ``updated_at`` to now.  Returns True when a row was
        updated.
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE plan_releases SET outcome = %s, order_nbr = %s, "
                    "warehouse_code = %s, vendor_code = %s, error = %s, "
                    "updated_at = now() WHERE id = %s",
                    (
                        outcome,
                        order_nbr,
                        warehouse_code,
                        vendor_code,
                        error,
                        release_id,
                    ),
                )
                updated = cur.rowcount > 0
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return updated

    def list_snapshots_for_origins(self, origins: list[str]) -> dict[str, list]:
        """The stored line snapshots of the rows that tie an origin to a
        PO that exists in Acumatica.

        For each origin in ``origins`` (an empty list returns ``{}``),
        the ``lines`` snapshot of every stored row with that origin and
        an outcome in ``created``, ``exists`` or ``aborted`` — the
        outcomes that leave an order carrying the origin behind — in
        id order.  A confirm that is answered ``exists`` by Acumatica
        must not accept it when the current plan's snapshot differs
        from any of them.
        """
        if not origins:
            return {}
        with get_cursor() as cur:
            cur.execute(
                "SELECT origin, lines FROM plan_releases "
                "WHERE origin = ANY(%s) AND outcome = ANY(%s) "
                "ORDER BY id",
                (origins, list(_SNAPSHOT_OUTCOMES)),
            )
            rows = cur.fetchall()
        result: dict[str, list] = {}
        for row in rows:
            result.setdefault(row["origin"], []).append(row["lines"])
        return result

    def list_readback_order_nbrs(self) -> list[str]:
        """The distinct order numbers the readback must read.

        Every stored row whose outcome is ``created`` or ``exists``,
        whose ``order_nbr`` is non-null, and whose ``erp_status`` is
        null or neither ``Completed``, ``Closed`` nor ``Canceled`` —
        the rows whose order may have moved on in Acumatica since it
        was last read (a completed, closed or canceled order needs no
        more reads).  Distinct, in ascending order-number order.
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT DISTINCT order_nbr FROM plan_releases "
                "WHERE outcome IN ('created', 'exists') "
                "AND order_nbr IS NOT NULL "
                "AND (erp_status IS NULL "
                "OR erp_status NOT IN ('Completed', 'Closed', 'Canceled')) "
                "ORDER BY 1"
            )
            return [row["order_nbr"] for row in cur.fetchall()]

    def mark_erp_readbacks(self, found: dict[str, dict]) -> int:
        """Store the read-back purchase-order records; rows updated.

        For every order number in ``found`` (an empty mapping updates
        nothing) sets ``erp_status`` to the record's ``status``,
        ``erp_lines`` to the record's ``lines`` — exactly as returned
        — and ``erp_checked_at`` to now on every row carrying that
        order number.  One transaction, committed together.
        """
        if not found:
            return 0
        conn = get_connection()
        try:
            updated = 0
            with conn.cursor() as cur:
                for order_nbr, record in found.items():
                    cur.execute(
                        "UPDATE plan_releases SET erp_status = %s, "
                        "erp_lines = %s::jsonb, erp_checked_at = now() "
                        "WHERE order_nbr = %s",
                        (
                            record["status"],
                            json.dumps(record["lines"]),
                            order_nbr,
                        ),
                    )
                    updated += cur.rowcount
            conn.commit()
            return updated
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_newest(self) -> list[dict]:
        """Every stored release row, newest first.

        Ordered by ``created_at DESC, id DESC`` so equal timestamps
        list deterministically (insertion order reversed); every
        column of the row comes back (``created_at`` and ``updated_at``
        as ISO-8601 strings, ``value`` the exact ``Decimal`` the
        ``NUMERIC`` column stores, ``lines`` the stored snapshot with
        its monetary fields as exact decimal strings).
        """
        with get_cursor() as cur:
            cur.execute("SELECT * FROM plan_releases ORDER BY created_at DESC, id DESC")
            return [_row_to_dict(r) for r in cur.fetchall()]
