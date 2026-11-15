"""Cross-kind summary of every item-site's current decision.

``GET /api/decisions/summary`` answers, for each of the three decision
kinds (drift, excess, stockout), the accepted / applied / outstanding /
dismissed / snoozed buckets over every item-site's current decision of
that kind (the latest row by ``decided_at``, ``id`` breaking ties),
plus a ``total`` that sums the three kinds bucket by bucket.

The drift buckets are exactly the buckets of
``GET /api/drift/decisions/summary`` (with the money key named
``money`` and the values taken from the stored ``money_at_stake``).
For excess and stockout an accepted decision is applied when the
item-site is no longer in the current ranked rows (``excess_ranked()``
and ``stockout_ranked()`` respectively), with the money from the
stored ``money_tied_up`` / ``money_at_risk``; every other accepted
decision is outstanding.  In every kind ``accepted`` equals
``applied`` plus ``outstanding`` in count and money, and a decision
whose stored money is null counts in ``without_money`` and adds
nothing to ``money``.

The excess and stockout rows are gathered inside
``app.recommend.reorder.recommendations_memo``: the two rankings' one
``recommendations_for_all`` call each (both at the request's resolved
as-of) share the memo's single computation, so the request runs the
recommendations computation exactly once, while the answer is exactly
the same as without it.

The route is open to any authenticated user (401 without a valid
Bearer token) and never changes data.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import require_authenticated_user
from app.auth.models import AuthUser
from app.recommend.excess import excess_ranked
from app.recommend.reorder import recommendations_memo
from app.recommend.stockout import stockout_ranked
from app.repositories.excess_decisions_postgres_repository import (
    ExcessDecisionPostgresRepository,
)
from app.repositories.planner_decisions_postgres_repository import (
    PlannerDecisionPostgresRepository,
)
from app.repositories.stockout_decisions_postgres_repository import (
    StockoutDecisionPostgresRepository,
)

router = APIRouter(prefix="/api/decisions", tags=["decision-summary"])

_planner_repo = PlannerDecisionPostgresRepository()
_excess_repo = ExcessDecisionPostgresRepository()
_stockout_repo = StockoutDecisionPostgresRepository()


class _MoneyBucket(BaseModel):
    """One money-bearing summary bucket.

    ``count`` is the number of current decisions in the bucket,
    ``money`` sums their stored money (drift: ``money_at_stake``,
    excess: ``money_tied_up``, stockout: ``money_at_risk``; a null
    snapshot contributes nothing) and ``without_money`` counts those
    null-snapshot decisions.
    """

    count: int
    money: float
    without_money: int


class _CountBucket(BaseModel):
    """A summary bucket that only carries a count."""

    count: int


class KindSummary(BaseModel):
    """The summary of one decision kind (drift, excess, stockout or
    total): ``accepted`` equals ``applied`` plus ``outstanding`` in
    count and money."""

    accepted: _MoneyBucket
    applied: _MoneyBucket
    outstanding: _MoneyBucket
    dismissed: _CountBucket
    snoozed: _CountBucket


class DecisionSummaryResponse(BaseModel):
    """The response of GET /api/decisions/summary: the three kinds and
    their bucket-by-bucket sum in ``total``."""

    drift: KindSummary
    excess: KindSummary
    stockout: KindSummary
    total: KindSummary


def require_any_authenticated_user(
    current_user: AuthUser = Depends(require_authenticated_user),
) -> AuthUser:
    """The summary route's own authentication dependency.

    ``GET /api/decisions/summary`` is open to any authenticated user:
    the dependency answers 401 without a valid Bearer token even when
    the router is mounted without the app's include-time gate, and
    passes the authenticated user through unchanged.
    """
    return current_user


def _money_bucket(rows: list[dict], money_field: str) -> dict:
    """The ``{count, money, without_money}`` bucket of a decision list.

    A decision whose stored ``money_field`` is null counts in
    ``without_money`` and adds nothing to ``money``.
    """
    count = 0
    money = 0.0
    without_money = 0
    for row in rows:
        count += 1
        value = row[money_field]
        if value is None:
            without_money += 1
        else:
            money += float(value)
    return {"count": count, "money": money, "without_money": without_money}


def _kind_summary(
    current: list[dict], listed_keys: set[tuple[str, str]], money_field: str
) -> dict:
    """The five buckets of one kind.

    ``current`` is the current decision (the latest by ``decided_at``,
    ``id`` breaking ties) of every item-site that has one; ``listed_keys``
    the item-sites of the current ranked rows.  An accepted decision is
    applied when its item-site is no longer listed, outstanding
    otherwise; every accepted decision lands in exactly one of the two,
    so ``accepted`` equals ``applied`` plus ``outstanding`` in count and
    money.
    """
    accepted = [row for row in current if row["decision"] == "accepted"]
    applied: list[dict] = []
    outstanding: list[dict] = []
    for row in accepted:
        if (row["item_code"], row["site_code"]) in listed_keys:
            outstanding.append(row)
        else:
            applied.append(row)
    return {
        "accepted": _money_bucket(accepted, money_field),
        "applied": _money_bucket(applied, money_field),
        "outstanding": _money_bucket(outstanding, money_field),
        "dismissed": {
            "count": sum(1 for row in current if row["decision"] == "dismissed")
        },
        "snoozed": {"count": sum(1 for row in current if row["decision"] == "snoozed")},
    }


def _sum_kinds(*kinds: dict) -> dict:
    """The bucket-by-bucket sum of the three kinds."""
    total = {}
    for name in ("accepted", "applied", "outstanding"):
        total[name] = {
            "count": sum(kind[name]["count"] for kind in kinds),
            "money": sum(kind[name]["money"] for kind in kinds),
            "without_money": sum(kind[name]["without_money"] for kind in kinds),
        }
    for name in ("dismissed", "snoozed"):
        total[name] = {"count": sum(kind[name]["count"] for kind in kinds)}
    return total


@router.get(
    "/summary",
    response_model=DecisionSummaryResponse,
    dependencies=[Depends(require_any_authenticated_user)],
)
def decision_summary() -> dict:
    """Summarise every item-site's current decision across the three kinds.

    Open to any authenticated user.  The drift buckets are exactly the
    buckets of ``GET /api/drift/decisions/summary`` (with the money key
    named ``money``); the excess and stockout ranked rows are gathered
    inside ``app.recommend.reorder.recommendations_memo``, so their
    one ``recommendations_for_all`` call each (both at the request's
    resolved as-of) share the memo's single computation, and an
    accepted decision of those kinds is applied when its item-site is
    no longer in the current ranked rows, with the money from the
    stored ``money_tied_up`` / ``money_at_risk``.  ``total`` sums the
    three kinds bucket by bucket, and nothing changes data.
    """
    # The drift buckets, exactly as GET /api/drift/decisions/summary
    # defines them: the same current-decision selection and applied
    # tolerance, the money from the stored money_at_stake.
    drift_row = _planner_repo.decision_summary()
    drift = {
        "accepted": {
            "count": drift_row["accepted_count"],
            "money": float(drift_row["accepted_money"]),
            "without_money": drift_row["accepted_without_money"],
        },
        "applied": {
            "count": drift_row["applied_count"],
            "money": float(drift_row["applied_money"]),
            "without_money": drift_row["applied_without_money"],
        },
        "outstanding": {
            "count": drift_row["outstanding_count"],
            "money": float(drift_row["outstanding_money"]),
            "without_money": drift_row["outstanding_without_money"],
        },
        "dismissed": {"count": drift_row["dismissed_count"]},
        "snoozed": {"count": drift_row["snoozed_count"]},
    }

    # The ranked rows of the other two kinds, each computed exactly once
    # per request; a ValueError from the ranked read answers 400, like
    # the other ranked endpoints.
    try:
        with recommendations_memo():
            excess_rows = excess_ranked()
            stockout_rows = stockout_ranked()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    excess = _kind_summary(
        _excess_repo.current_all(),
        {(row["item_code"], row["site_code"]) for row in excess_rows},
        "money_tied_up",
    )
    stockout = _kind_summary(
        _stockout_repo.current_all(),
        {(row["item_code"], row["site_code"]) for row in stockout_rows},
        "money_at_risk",
    )

    return {
        "drift": drift,
        "excess": excess,
        "stockout": stockout,
        "total": _sum_kinds(drift, excess, stockout),
    }
