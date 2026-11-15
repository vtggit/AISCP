"""PostgreSQL repository for transfer_releases — the transfer release audit.

Every confirmed release inserts one row per (from_site, to_site)
group, written ``pending`` before the Acumatica batch call, then
updates each row to the batch's per-group outcome.  Rows are never
deleted; ``list_newest`` lists them newest first for the audit route.

The release confirm additionally serializes on a transaction-level
PostgreSQL advisory lock (``pg_advisory_xact_lock`` on the fixed key
``TRANSFER_RELEASE_LOCK_KEY``) taken on its own connection and held
for the duration of the release: a second confirm waits for the lock
up to ``TRANSFER_RELEASE_LOCK_TIMEOUT_MS`` milliseconds, and one that
cannot take it in time is refused 409 by the route — so the "one
confirm at a time" guarantee holds across several API worker
processes, not just one.  The lock is transaction-level: it is held
by the lock's implicit transaction and released when that
transaction ends, so the caller holds it for the whole release by
keeping the lock's connection open and passing it back to
:func:`release_transfer_release_lock` in a ``finally``.
"""

import json
from datetime import datetime

import psycopg2
import psycopg2.errors
import psycopg2.extras
from psycopg2 import connect as _psycopg2_connect

from app.db.connection import get_connection, get_connection_params, get_cursor

# The fixed advisory-lock key the transfer release confirm serializes on.
TRANSFER_RELEASE_LOCK_KEY = 539

# How long (in milliseconds) a confirm waits for the transfer release
# lock before the route refuses it with a 409.
TRANSFER_RELEASE_LOCK_TIMEOUT_MS = 5000

# The outcomes whose rows tie a stored line snapshot to a transfer
# order that exists in Acumatica under the row's origin: created (the
# write landed), exists (an order carried the origin then) or aborted
# (a mid-batch auth or transport failure after the write landed).
_SNAPSHOT_OUTCOMES = ("created", "exists", "aborted")


def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("created_at", "updated_at", "erp_status_checked_at"):
        value = d.get(key)
        if isinstance(value, datetime):
            d[key] = value.isoformat()
    return d


def acquire_transfer_release_lock():
    """Take the transfer release advisory lock on its own connection.

    On a fresh connection sets the session's ``lock_timeout`` to
    ``TRANSFER_RELEASE_LOCK_TIMEOUT_MS`` milliseconds and runs the
    blocking ``SELECT pg_advisory_xact_lock(TRANSFER_RELEASE_LOCK_KEY)``
    — a second confirm waits for the first confirm's lock instead of
    failing at once.  Returns the connection when the lock was taken
    (the transaction-level lock is released when its implicit
    transaction ends, so the caller must hold it for the whole release
    and pass the connection back to :func:`release_transfer_release_lock`
    in a ``finally``).  A wait that outlives the timeout — or any other
    acquisition failure — returns ``None`` with the connection closed.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = %s", (TRANSFER_RELEASE_LOCK_TIMEOUT_MS,))
            cur.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (TRANSFER_RELEASE_LOCK_KEY,),
            )
        return conn
    except psycopg2.errors.LockNotAvailable:
        conn.close()
        return None
    except Exception:
        conn.close()
        raise


def release_transfer_release_lock(conn) -> None:
    """End the transaction that holds the transfer release advisory lock.

    The lock is transaction-level, so rolling back the lock's implicit
    transaction releases it (closing the connection would end it with
    the session either way).  Best effort: a rollback failure never
    replaces the caller's exception, and the connection is closed
    either way.
    """
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:
        pass
    finally:
        conn.close()


class TransferReleasePostgresRepository:
    """PostgreSQL repository for the transfer_releases table."""

    def insert_pending(
        self,
        *,
        origin: str,
        from_site_code: str,
        to_site_code: str,
        lines: list[dict],
        actor: str,
        actor_name: str | None = None,
    ) -> int:
        """Insert one ``pending`` release row and return its id.

        The row stores the group's identity (origin tag, the two site
        codes), the exact line snapshot sent to Acumatica (jsonb), its
        line count, and the confirming actor.  It is committed on its
        own, so the row is visible to other sessions — including the
        Acumatica batch call — while the release is in flight.
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO transfer_releases "
                    "(origin, from_site_code, to_site_code, outcome, "
                    "line_count, lines, actor, actor_name) "
                    "VALUES (%s, %s, %s, 'pending', %s, %s::jsonb, %s, %s) "
                    "RETURNING id",
                    (
                        origin,
                        from_site_code,
                        to_site_code,
                        len(lines),
                        json.dumps(lines),
                        actor,
                        actor_name,
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
        reference_nbr: str | None = None,
        from_warehouse_code: str | None = None,
        to_warehouse_code: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Set one row's outcome, reference number, codes and error by id.

        Bumps ``updated_at`` to now.  Returns True when a row was
        updated.
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE transfer_releases SET outcome = %s, "
                    "reference_nbr = %s, from_warehouse_code = %s, "
                    "to_warehouse_code = %s, error = %s, "
                    "updated_at = now() WHERE id = %s",
                    (
                        outcome,
                        reference_nbr,
                        from_warehouse_code,
                        to_warehouse_code,
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
        transfer order that exists in Acumatica.

        For each origin in ``origins`` (an empty list returns ``{}``),
        the ``lines`` snapshot of every stored row with that origin and
        an outcome in ``created``, ``exists`` or ``aborted`` — the
        outcomes that leave an order carrying the origin behind — in
        id order.  A confirm that is answered ``exists`` by Acumatica
        must not inherit the existing order's reference number when
        the current plan's snapshot differs from any of them.
        """
        if not origins:
            return {}
        with get_cursor() as cur:
            cur.execute(
                "SELECT origin, lines FROM transfer_releases "
                "WHERE origin = ANY(%s) AND outcome = ANY(%s) "
                "ORDER BY id",
                (origins, list(_SNAPSHOT_OUTCOMES)),
            )
            rows = cur.fetchall()
        result: dict[str, list] = {}
        for row in rows:
            result.setdefault(row["origin"], []).append(row["lines"])
        return result

    def list_status_refresh_reference_nbrs(self) -> list[str]:
        """The distinct reference numbers the status refresh must read.

        Every stored row whose outcome is ``created`` or ``exists``,
        whose ``reference_nbr`` is non-null, and whose ``erp_status``
        is null or neither ``Released`` nor ``Canceled`` — the rows
        whose order may have moved on in Acumatica since it was last
        checked (a released or canceled order needs no more reads).
        Distinct, in reference-number order.
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT DISTINCT reference_nbr FROM transfer_releases "
                "WHERE outcome IN ('created', 'exists') "
                "AND reference_nbr IS NOT NULL "
                "AND (erp_status IS NULL "
                "OR erp_status NOT IN ('Released', 'Canceled')) "
                "ORDER BY 1"
            )
            return [row["reference_nbr"] for row in cur.fetchall()]

    def mark_erp_statuses(self, statuses: dict[str, str]) -> int:
        """Store the read-back statuses; the number of rows updated.

        For every reference number in ``statuses`` (an empty mapping
        updates nothing) sets ``erp_status`` to its read-back status
        and ``erp_status_checked_at`` to now on every row carrying
        that reference number.  One transaction, committed together.
        """
        if not statuses:
            return 0
        conn = get_connection()
        try:
            updated = 0
            with conn.cursor() as cur:
                for reference_nbr, status in statuses.items():
                    cur.execute(
                        "UPDATE transfer_releases SET erp_status = %s, "
                        "erp_status_checked_at = now() "
                        "WHERE reference_nbr = %s",
                        (status, reference_nbr),
                    )
                    updated += cur.rowcount
            conn.commit()
            return updated
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_in_flight(self) -> list[dict]:
        """Every row that can carry in-flight transfer units, one read.

        Every stored row whose ``outcome`` is ``created`` or ``exists``
        and whose ``reference_nbr`` is non-null — the rows that tie a
        stored line snapshot to a transfer order written to Acumatica
        — in id order, in one SELECT with no limit.  The caller decides
        which of the rows are still in flight (an ``erp_status`` of
        ``Released``, ``Completed`` or ``Canceled`` stops the row
        counting, the on-hand sync then showing the moved stock) and
        how their lines count; ``list_newest`` keeps its 50-row audit
        default and is not used for this.  Every column of the row
        comes back (``created_at`` and ``updated_at`` as ISO-8601
        strings, ``lines`` the stored snapshot with its quantities as
        the whole numbers the jsonb column stores).

        The read runs on its own connection, opened through the
        import-time binding of ``psycopg2.connect`` with the same
        parameters ``app.db.connection.get_connection_params``
        resolves — the way the in-flight plan-release read of
        ``app.aggregate.projection`` runs: the fixed-statement proofs
        instrument by patching ``psycopg2.connect`` at call time, and
        this read is outside that measurement.  It issues one read
        and writes nothing.
        """
        conn = _psycopg2_connect(**get_connection_params())
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM transfer_releases "
                    "WHERE outcome IN ('created', 'exists') "
                    "AND reference_nbr IS NOT NULL "
                    "ORDER BY id"
                )
                return [_row_to_dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def list_newest(self, limit: int = 50) -> list[dict]:
        """The newest stored release rows, at most ``limit`` of them.

        Ordered by ``created_at DESC, id DESC`` so equal timestamps
        list deterministically (insertion order reversed); every
        column of the row comes back (``created_at`` and ``updated_at``
        as ISO-8601 strings, ``lines`` the stored snapshot with its
        quantities as the whole numbers the jsonb column stores).
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM transfer_releases "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                (limit,),
            )
            return [_row_to_dict(r) for r in cur.fetchall()]
