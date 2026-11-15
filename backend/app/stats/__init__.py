"""Statistics over ERP-sourced demand data.

Pure computation (no database access) plus the thin readers that pull
daily aggregates from the ``demand_events`` table.
"""

from app.stats.demand import (
    DEFAULT_WINDOW_DAYS,
    METHOD,
    compute_demand_stats,
    demand_stats_for_all,
    demand_stats_for_pair,
    resolve_as_of,
)

__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "METHOD",
    "compute_demand_stats",
    "demand_stats_for_all",
    "demand_stats_for_pair",
    "resolve_as_of",
]
