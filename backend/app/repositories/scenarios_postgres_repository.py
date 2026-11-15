"""PostgreSQL repository for scenarios — stored what-if lever sets.

A scenario row stores a name and the three simulation levers (each
nullable: a null lever means "keep the baseline value", as in
``POST /api/scenarios/simulate``).  Nothing else is stored — a
scenario carries no result snapshot.  ``created_at`` and ``updated_at``
are written by the application as aware UTC datetimes and returned as
ISO-8601 strings, as the other repositories do.
"""

from datetime import UTC, datetime
from uuid import uuid4

from app.db.connection import get_cursor

_LEVER_COLUMNS = ("service_level", "demand_multiplier", "lead_time_multiplier")


def _generate_id() -> str:
    return str(uuid4())


def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("created_at", "updated_at"):
        if d.get(key) and isinstance(d[key], datetime):
            d[key] = d[key].isoformat()
    return d


class ScenarioPostgresRepository:
    """PostgreSQL repository for the scenarios table."""

    def list_all(self) -> list[dict]:
        """Every scenario, newest ``created_at`` first (id breaking ties)."""
        with get_cursor() as cur:
            cur.execute("SELECT * FROM scenarios ORDER BY created_at DESC, id DESC")
            return [_row_to_dict(r) for r in cur.fetchall()]

    def get_by_id(self, scenario_id: str) -> dict | None:
        """The single scenario row for one id, or None."""
        with get_cursor() as cur:
            cur.execute("SELECT * FROM scenarios WHERE id = %s", (scenario_id,))
            row = cur.fetchone()
            return _row_to_dict(row) if row else None

    def create(self, data: dict) -> dict:
        """Store one new scenario and return the stored row.

        ``data`` holds ``name`` and the three levers (null when not
        given).  Both timestamps are set to now.
        """
        now = datetime.now(UTC)
        new_id = _generate_id()
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO scenarios "
                "(id, name, service_level, demand_multiplier, "
                "lead_time_multiplier, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (
                    new_id,
                    data.get("name"),
                    data.get("service_level"),
                    data.get("demand_multiplier"),
                    data.get("lead_time_multiplier"),
                    now,
                    now,
                ),
            )
            return _row_to_dict(cur.fetchone())

    def update(self, scenario_id: str, data: dict) -> dict | None:
        """Partially update one scenario and return the stored row.

        ``data`` holds only the fields the caller set (an explicit null
        for a lever is a set field that clears it to the baseline
        value); an omitted field is left unchanged.  Every real change
        bumps ``updated_at``.  Returns None when no scenario has the id;
        an empty ``data`` returns the current row unchanged.
        """
        fields = [k for k in ("name", *_LEVER_COLUMNS) if k in data]
        if not fields:
            return self.get_by_id(scenario_id)
        assignments = ", ".join(f"{f} = %s" for f in fields)
        values = [data[f] for f in fields]
        values.append(datetime.now(UTC))
        values.append(scenario_id)
        with get_cursor() as cur:
            cur.execute(
                f"UPDATE scenarios SET {assignments}, updated_at = %s "
                "WHERE id = %s RETURNING *",
                tuple(values),
            )
            row = cur.fetchone()
            return _row_to_dict(row) if row else None

    def delete(self, scenario_id: str) -> bool:
        """Delete one scenario by id; True when a row was deleted."""
        with get_cursor() as cur:
            cur.execute("DELETE FROM scenarios WHERE id = %s", (scenario_id,))
            return cur.rowcount > 0
