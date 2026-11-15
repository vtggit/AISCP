"""PostgreSQL repository for erp_standing_grants — standing write grants.

The table holds one row per standing grant, in force or revoked, and a
unique index over ``process`` where ``revoked_at`` is null allows one
grant in force per process.  ``create`` therefore never pre-checks
whether the process is already covered: the insert itself is the
check, and a second in-force grant for the same process is refused by
the index — even when two creates race — and surfaces as
:class:`StandingGrantAlreadyInForce` instead of a 500.  ``revoke``
sets ``revoked_at`` (now) and ``revoked_by`` (the caller's username)
in one statement that only touches grants still in force: a second
revoker of the same grant gets
:class:`StandingGrantAlreadyRevoked`, and a grant that was never
stored is a :class:`LookupError`.  ``list`` returns every row, in
force and revoked, newest first by ``id`` descending.  ``created_at``
is written by the database (the ``now()`` default) and the revocation
times by the application as aware UTC datetimes; both come back as
ISO-8601 strings, and the value columns come back from ``NUMERIC`` as
``Decimal`` and are returned as ``float``.  ``in_force`` is derived:
true while ``revoked_at`` is null.
"""

from datetime import UTC, datetime

import psycopg2.errors

from app.db.connection import get_cursor


class StandingGrantAlreadyInForce(Exception):
    """A grant for the process is already in force.

    The unique index over ``process`` where ``revoked_at`` is null
    refused the insert, so nothing was written.
    """

    def __init__(self, process: str) -> None:
        super().__init__(process)
        self.process = process


class StandingGrantAlreadyRevoked(Exception):
    """The grant was revoked earlier; revoking it again is refused."""

    def __init__(self, row_id: int) -> None:
        super().__init__(row_id)
        self.row_id = row_id


def _row_to_dict(row) -> dict:
    d = dict(row)
    for field in ("max_document_value", "max_daily_value"):
        if d.get(field) is not None:
            d[field] = float(d[field])
    for field in ("created_at", "revoked_at"):
        if d.get(field) is not None and isinstance(d[field], datetime):
            d[field] = d[field].isoformat()
    d["in_force"] = d.get("revoked_at") is None
    return d


class ErpStandingGrantPostgresRepository:
    """PostgreSQL repository for the erp_standing_grants table."""

    def list(self) -> list[dict]:
        """Every stored grant, in force and revoked, newest first."""
        with get_cursor() as cur:
            cur.execute("SELECT * FROM erp_standing_grants ORDER BY id DESC")
            return [_row_to_dict(r) for r in cur.fetchall()]

    def in_force_for(self, process: str) -> dict | None:
        """The grant in force for one process, or None while none is.

        The one in-force read of the running grant: the unique index
        over ``process`` where ``revoked_at`` is null allows at most
        one such row, so the read is a plain select.  The monetary
        columns come back as the exact Decimals the NUMERIC columns
        hold — the grant's limits are money and the caller compares
        them exactly, so no float re-shaping here.
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM erp_standing_grants "
                "WHERE process = %s AND revoked_at IS NULL",
                (process,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        for field in ("created_at", "revoked_at"):
            if d.get(field) is not None and isinstance(d[field], datetime):
                d[field] = d[field].isoformat()
        d["in_force"] = True
        return d

    def create(
        self,
        process: str,
        max_document_value: float,
        max_daily_value: float,
        max_data_age_hours: int,
        created_by: str,
    ) -> dict:
        """Store one standing grant and return the stored row.

        The insert is the check that the process is not already
        covered by an in-force grant: a violation of the unique index
        raises :class:`StandingGrantAlreadyInForce` and writes
        nothing.
        """
        try:
            with get_cursor() as cur:
                cur.execute(
                    "INSERT INTO erp_standing_grants "
                    "(process, max_document_value, max_daily_value, "
                    "max_data_age_hours, created_by) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING *",
                    (
                        process,
                        max_document_value,
                        max_daily_value,
                        max_data_age_hours,
                        created_by,
                    ),
                )
                return _row_to_dict(cur.fetchone())
        except psycopg2.errors.UniqueViolation:
            raise StandingGrantAlreadyInForce(process) from None

    def revoke(self, row_id: int, revoked_by: str) -> dict:
        """Revoke one grant by id and return the revoked row.

        Raises :class:`LookupError` when no row has that id and
        :class:`StandingGrantAlreadyRevoked` when the row is already
        revoked.  The UPDATE only touches grants still in force, so
        two revokers racing never double-write the row.
        """
        now = datetime.now(UTC)
        with get_cursor() as cur:
            cur.execute(
                "UPDATE erp_standing_grants "
                "SET revoked_at = %s, revoked_by = %s "
                "WHERE id = %s AND revoked_at IS NULL RETURNING *",
                (now, revoked_by, row_id),
            )
            row = cur.fetchone()
            if row is not None:
                return _row_to_dict(row)
            cur.execute(
                "SELECT revoked_at FROM erp_standing_grants WHERE id = %s",
                (row_id,),
            )
            if cur.fetchone() is None:
                raise LookupError(row_id)
            raise StandingGrantAlreadyRevoked(row_id)
