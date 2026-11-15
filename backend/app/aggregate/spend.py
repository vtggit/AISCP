"""Monthly spend of the projected portfolio against the stored budgets.

``spend_against_budget`` summarizes the site-grouped roll-forward
projection of ``app.aggregate.projection.aggregate_projection`` as a
monthly spend view against the per-month purchase budgets of
``purchase_budgets``.  Over the same calendar-month horizon the
projection covers — one entry per month in month order, the buckets
``app.aggregate.projection._bucket_months`` gives from the month of
the resolved ``as_of`` (the current server date when ``as_of`` is
None) — every group's ``projected_supply_value`` of that month is
summed into ``committed_spend`` and its ``planned_receipts_value``
into ``planned_spend``; ``total_spend`` is the two together.  The
``budget`` is the stored amount of that ``YYYY-MM`` (or null when no
budget is stored for it), ``remaining`` is ``budget - total_spend``
(null without a budget) and ``over_budget`` is true only when a
budget exists and the total spend exceeds it.  With no groups (an
empty projection) every month of the horizon still appears, with
zero spend.

The projection is called exactly once with the same arguments, and
the budgets are read exactly once through
``PurchaseBudgetPostgresRepository.list``; the computation is
read-only and writes nothing.  Every ``ValueError`` the projection
raises (a future ``as_of``, ``months`` outside 1 to 12, or a lever
out of range or whose scaled inputs overflow the formula)
propagates unchanged.  The month view itself is the private
``_spend_view`` of the projection's groups over the horizon's
buckets, factored out so
``app.aggregate.deferral.deferral_candidates`` can feed it the groups
of a projection setup it already built.
"""

from __future__ import annotations

from datetime import date

from app.aggregate.projection import _bucket_months, aggregate_projection
from app.repositories.purchase_budgets_postgres_repository import (
    PurchaseBudgetPostgresRepository,
)

_budget_repo = PurchaseBudgetPostgresRepository()


def spend_against_budget(
    as_of: date | None = None,
    months: int = 6,
    service_level: float | None = None,
    demand_multiplier: float | None = None,
    lead_time_multiplier: float | None = None,
) -> dict:
    """The monthly spend of the projection against the stored budgets.

    The arguments carry exactly the meaning
    ``app.aggregate.projection.aggregate_projection`` gives them and
    are passed to it exactly once, unchanged; the month buckets are
    the ones its ``_bucket_months`` gives from the month of the
    resolved ``as_of`` (the current server date when ``as_of`` is
    None), so the horizon always matches the projection's.  Every
    ``ValueError`` the projection raises propagates unchanged.

    Returns ``{"months": [...], "item_sites_without_unit_cost": n}``,
    where ``n`` is the sum of the groups'
    ``item_sites_without_unit_cost`` and ``months`` has one entry per
    month of the horizon in month order: ``{"month",
    "committed_spend", "planned_spend", "total_spend", "budget",
    "remaining", "over_budget"}`` with ``committed_spend`` the sum
    over all groups of the month's ``projected_supply_value``,
    ``planned_spend`` the sum of the month's
    ``planned_receipts_value``, ``total_spend`` the two together,
    ``budget`` the stored amount of that month or None, ``remaining``
    ``budget - total_spend`` or None without a budget, and
    ``over_budget`` true only when a budget exists and the total
    spend exceeds it.
    """
    groups = aggregate_projection(
        as_of=as_of,
        months=months,
        service_level=service_level,
        demand_multiplier=demand_multiplier,
        lead_time_multiplier=lead_time_multiplier,
    )
    resolved_as_of = as_of if as_of is not None else date.today()
    return _spend_view(groups, _bucket_months(resolved_as_of, months))


def _spend_view(groups: list[dict], buckets: list[tuple[int, int, str]]) -> dict:
    """The monthly spend view of the given projection groups over the
    given horizon buckets.

    One entry per bucket month in month order: ``month`` ("YYYY-MM"),
    ``committed_spend`` (the sum over all groups of the month's
    ``projected_supply_value``), ``planned_spend`` (the sum of the
    month's ``planned_receipts_value``), ``total_spend`` (the two
    together), ``budget`` (the stored amount of that month or None),
    ``remaining`` (``budget - total_spend`` or None without a budget)
    and ``over_budget`` (true only when a budget exists and the total
    spend exceeds it); with no groups (an empty projection) every
    month of the horizon still appears, with zero spend.  The
    budgets are read exactly once through
    ``PurchaseBudgetPostgresRepository.list`` and nothing is written.
    This is the computation ``spend_against_budget`` performs after
    its one projection call, factored out so
    ``app.aggregate.deferral.deferral_candidates`` can feed it the
    groups of a projection setup it already built.
    """
    stored = {row["month"]: row["amount"] for row in _budget_repo.list()}
    month_rows: list[dict] = []
    for _year, _month, label in buckets:
        committed = 0.0
        planned = 0.0
        for group in groups:
            for bucket in group["buckets"]:
                if bucket["month"] != label:
                    continue
                committed += bucket["projected_supply_value"]
                planned += bucket["planned_receipts_value"]
        total = committed + planned
        budget = stored.get(label)
        month_rows.append(
            {
                "month": label,
                "committed_spend": committed,
                "planned_spend": planned,
                "total_spend": total,
                "budget": budget,
                "remaining": None if budget is None else budget - total,
                "over_budget": budget is not None and total > budget,
            }
        )
    return {
        "months": month_rows,
        "item_sites_without_unit_cost": sum(
            group["item_sites_without_unit_cost"] for group in groups
        ),
    }
