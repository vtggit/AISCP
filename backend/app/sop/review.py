"""The S&OP review of the horizon months.

``build_sop_review(months)`` summarises the site-grouped roll-forward
projection of ``app.aggregate.projection.aggregate_projection`` for
the ``months`` horizon months (one call, with ``months`` only — no
``as_of`` and no levers, so the buckets start from the month of the
current server date).  Per horizon month, in month order, the view
sums over all site groups of that month's ``projected_demand_units``,
``projected_demand_value``, ``signal_units``, ``projected_supply_units``,
``projected_supply_value``, ``planned_receipts_units``,
``planned_receipts_value``, ``projected_on_hand_units`` and
``planned_on_hand_units`` into ``demand_units``, ``demand_value``,
``signal_units``, ``supply_units``, ``supply_value``,
``planned_receipts_units``, ``planned_receipts_value``,
``projected_on_hand_units`` and ``planned_on_hand_units``;
``signal_units`` stays a float — the signal day shares are
fractional, and the month's sum never truncates them to whole
units; ``sites_short_as_is`` lists the site codes
whose bucket has ``projected_shortfall`` true and ``sites_short_planned``
the site codes whose bucket has ``planned_shortfall`` true (each
sorted ascending); and ``committed_spend``, ``planned_spend``,
``total_spend``, ``budget``, ``remaining`` and ``over_budget`` are
copied unchanged from the matching month of the spend view — the
projection's own groups fed to
``app.aggregate.spend._spend_view`` over the
``app.aggregate.projection._bucket_months`` buckets of the current
server date, so the projection is never computed a second time.

The ``forecast`` block is
``app.forecast.accuracy.realised_accuracy`` with its default
arguments (one call): its top-level ``as_of`` and
``compared_buckets``, its ``overall`` ``wape``, ``bias``,
``effective_wape``, ``effective_bias``, ``value_added`` and
``overridden_buckets``, and the ``wape`` and ``bias`` of its
``by_horizon`` entry with ``bucket_index`` 1 as ``next_month_wape``
and ``next_month_bias`` (null when there is no such entry); a value
the accuracy gives as null stays null.  The answer's ``as_of`` is the
current server date, ``review_month`` the first month of the horizon
("YYYY-MM"), ``horizon_months`` the requested ``months``,
``item_sites_without_unit_cost`` the value ``_spend_view`` returns
and ``latest_signoff`` the newest stored sign-off of the review
month (the ``sop_signoffs`` row first under ``created_at DESC,
id DESC`` — the id breaking equal timestamps) without its
figures, or ``None`` when the month carries no sign-off, and
``since_signoff`` the drift of this review against the newest stored
sign-off of any month (the ``sop_signoffs`` row first under the same
``created_at DESC, id DESC`` order, read with its figures) —
``None`` when no sign-off exists at all.  A stored row is never
rewritten when the review's shape grows, so ``since_signoff`` answers
from whatever the row's ``figures`` carry: its ``signoff_id``,
``signed_month`` and ``signed_at`` are the row's ``id``, ``month``
and ``created_at``, its ``signed_by`` the row's ``actor_name`` (its
``actor`` when that is missing or blank) and its ``signed_as_of``
the row's ``figures``' ``as_of`` (null when absent or not a string);
its ``months`` hold one entry per month that appears in both this
review's ``months`` and the stored ``figures``' ``months``, in the
review's order (a month in only one of them is left out), each with
the current value minus the stored value of the same field as
``demand_units_delta``, ``demand_value_delta``,
``signal_units_delta``, ``supply_value_delta``,
``planned_receipts_value_delta``, ``total_spend_delta`` and
``budget_delta`` — a delta is null when
either value is null, missing or not a finite number, and a stored
``figures`` without a usable ``months`` (null or not an object
included) answers an empty ``months`` and a null ``signed_as_of``.

The builder runs inside
``app.recommend.reorder.recommendations_memo`` so the one projection
call and its recommendation read happen once, and it performs a fixed
number of reads (the sign-off reads aside): it writes nothing and
calls the ERP nowhere.  Every
``ValueError`` the projection raises (``months`` outside 1 to 12
inclusive) propagates unchanged.  With no recommendable item-sites
every horizon month still appears, with zero figures and empty site
lists, and with no stored snapshots the ``forecast`` rates are null —
neither case is an error.

``build_sop_scenarios(months)`` compares the first 5 stored scenarios
(newest first, read once through the scenarios repository) against the
no-lever baseline projection of the same ``months`` horizon: the
baseline and each scenario carry ``demand_value``, ``total_spend``,
``months_over_budget`` and ``months_short_planned`` (the four
``_scenario_figures`` of the projection fed to ``_spend_view`` over the
same buckets), and each scenario its id, name, levers, the deltas
against the baseline and ``error`` (null normally; a scenario whose
levers make the projection raise ``ValueError`` — or that carries a
lever the projection does not accept, such as a non-null
``service_level_by_class`` — answers with ``error`` set and null
figures and deltas, the others still answering).  It runs inside
``recommendations_memo``, writes nothing and calls the ERP nowhere; a
``ValueError`` from the baseline propagates.
"""

from __future__ import annotations

import math
from datetime import date

from app.aggregate.projection import _bucket_months, aggregate_projection
from app.aggregate.spend import _spend_view
from app.forecast.accuracy import realised_accuracy
from app.recommend.reorder import recommendations_memo
from app.repositories.scenarios_postgres_repository import (
    ScenarioPostgresRepository,
)
from app.repositories.sop_signoffs_postgres_repository import (
    SopSignoffsPostgresRepository,
)

# The review's summed fields: the answer's name beside the projection
# bucket field it sums over all site groups of the month.
_SUMMED_FIELDS = (
    ("demand_units", "projected_demand_units"),
    ("demand_value", "projected_demand_value"),
    ("signal_units", "signal_units"),
    ("supply_units", "projected_supply_units"),
    ("supply_value", "projected_supply_value"),
    ("planned_receipts_units", "planned_receipts_units"),
    ("planned_receipts_value", "planned_receipts_value"),
    ("projected_on_hand_units", "projected_on_hand_units"),
    ("planned_on_hand_units", "planned_on_hand_units"),
)

# The since_signoff delta fields: the answer's name beside the review
# month field it diffs, the current value minus the stored one.
_DELTA_FIELDS = (
    ("demand_units_delta", "demand_units"),
    ("demand_value_delta", "demand_value"),
    ("signal_units_delta", "signal_units"),
    ("supply_value_delta", "supply_value"),
    ("planned_receipts_value_delta", "planned_receipts_value"),
    ("total_spend_delta", "total_spend"),
    ("budget_delta", "budget"),
)


def _is_finite_number(value: object) -> bool:
    """True for an int or float that is not a bool and finite —
    null, a string, a bool and the like are not usable values.  An
    int too large for a float still counts as finite: the delta step
    answers null when the difference cannot be represented."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return True


def _delta(current: object, stored: object) -> float | None:
    """The current value minus the stored one of one field, or
    ``None`` when either value is not a finite number or the
    difference is not one — a stored row of an older or hostile
    shape answers null, never an error."""
    if not _is_finite_number(current) or not _is_finite_number(stored):
        return None
    try:
        result = float(current - stored)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _since_signoff(latest: dict | None, months_view: list[dict]) -> dict | None:
    """The drift of the review against the newest stored sign-off of
    any month, or ``None`` when no sign-off exists at all.

    ``months`` holds one entry per month that appears in both the
    review's ``months_view`` and the stored ``figures``' ``months``
    (in the review's order), each with the current minus stored value
    of the delta fields; a stored ``figures`` that is null or not an
    object answers an empty ``months`` and a null ``signed_as_of``.
    """
    if latest is None:
        return None
    figures = latest.get("figures")
    if not isinstance(figures, dict):
        figures = None
    stored_by_month: dict[str, dict] = {}
    if figures is not None:
        raw_months = figures.get("months")
        if isinstance(raw_months, list):
            for entry in raw_months:
                if isinstance(entry, dict):
                    month = entry.get("month")
                    if isinstance(month, str):
                        stored_by_month[month] = entry
    as_of = None
    if figures is not None:
        raw_as_of = figures.get("as_of")
        if isinstance(raw_as_of, str):
            as_of = raw_as_of
    months: list[dict] = []
    for current in months_view:
        stored = stored_by_month.get(current.get("month"))
        if stored is None:
            continue
        entry: dict = {"month": current["month"]}
        for name, field in _DELTA_FIELDS:
            entry[name] = _delta(current.get(field), stored.get(field))
        months.append(entry)
    actor_name = latest.get("actor_name")
    if not isinstance(actor_name, str) or not actor_name:
        actor_name = None
    actor = latest.get("actor")
    if not isinstance(actor, str) or not actor:
        actor = None
    return {
        "signoff_id": latest["id"],
        "signed_month": latest["month"],
        "signed_at": latest["created_at"],
        "signed_by": actor_name or actor,
        "signed_as_of": as_of,
        "months": months,
    }


# The scenarios the comparison reads: the same repository and
# ordering ``GET /api/scenarios`` lists them with, newest first.
_scenarios_repo = ScenarioPostgresRepository()


_signoffs_repo = SopSignoffsPostgresRepository()


def build_sop_review(months: int) -> dict:
    """The S&OP review of the ``months`` horizon months.

    ``months`` carries exactly the meaning ``aggregate_projection``
    gives it (an integer between 1 and 12 inclusive) and is its only
    argument — no ``as_of`` and no levers, so the buckets start from
    the month of the current server date.  See the module docstring
    for the answer's fields.

    Returns ``{"as_of", "review_month", "horizon_months", "months",
    "forecast", "item_sites_without_unit_cost", "latest_signoff",
    "since_signoff"}`` with one ``months``
    entry per horizon month in month order.  Raises ``ValueError``
    when ``months`` is outside 1 to 12 inclusive.
    """
    today = date.today()
    with recommendations_memo():
        groups = aggregate_projection(months=months)
        buckets = _bucket_months(today, months)
        spend = _spend_view(groups, buckets)
        accuracy = realised_accuracy()

    months_view: list[dict] = []
    for spend_month in spend["months"]:
        label = spend_month["month"]
        sums = {name: 0 for name, _field in _SUMMED_FIELDS}
        # The signal day shares are fractional, so the month's
        # signal_units stays a float — 0.0, not 0, with no bucket.
        sums["signal_units"] = 0.0
        short_as_is: list[str] = []
        short_planned: list[str] = []
        for group in groups:
            for bucket in group["buckets"]:
                if bucket["month"] != label:
                    continue
                for name, field in _SUMMED_FIELDS:
                    sums[name] += bucket[field]
                if bucket["projected_shortfall"]:
                    short_as_is.append(group["group"]["code"])
                if bucket["planned_shortfall"]:
                    short_planned.append(group["group"]["code"])
        months_view.append(
            {
                "month": label,
                **sums,
                "sites_short_as_is": sorted(short_as_is),
                "sites_short_planned": sorted(short_planned),
                "committed_spend": spend_month["committed_spend"],
                "planned_spend": spend_month["planned_spend"],
                "total_spend": spend_month["total_spend"],
                "budget": spend_month["budget"],
                "remaining": spend_month["remaining"],
                "over_budget": spend_month["over_budget"],
            }
        )

    latest_signoff = _signoffs_repo.latest_for_month(buckets[0][2])
    since_signoff = _since_signoff(_signoffs_repo.latest_with_figures(), months_view)
    overall = accuracy["overall"]
    next_month = next(
        (entry for entry in accuracy["by_horizon"] if entry["bucket_index"] == 1),
        None,
    )
    return {
        "as_of": today,
        "review_month": buckets[0][2],
        "horizon_months": months,
        "months": months_view,
        "forecast": {
            "as_of": accuracy["as_of"],
            "compared_buckets": accuracy["compared_buckets"],
            "wape": overall["wape"],
            "bias": overall["bias"],
            "effective_wape": overall["effective_wape"],
            "effective_bias": overall["effective_bias"],
            "value_added": overall["value_added"],
            "overridden_buckets": overall["overridden_buckets"],
            "next_month_wape": None if next_month is None else next_month["wape"],
            "next_month_bias": None if next_month is None else next_month["bias"],
        },
        "item_sites_without_unit_cost": spend["item_sites_without_unit_cost"],
        "latest_signoff": latest_signoff,
        "since_signoff": since_signoff,
    }


# The levers ``aggregate_projection`` accepts for one scenario.
_SUPPORTED_LEVERS = (
    "service_level",
    "demand_multiplier",
    "lead_time_multiplier",
)

# The non-lever fields a stored scenario row carries: the identity and
# the audit timestamps.  Beside the supported levers, every other field
# a row carries is a scenario lever, so a row that carries a non-null
# value in any of them — ``service_level_by_class`` or any other lever
# the projection does not accept, known or to come — is unsupported and
# can never be fed to the projection; no persisted lever is ever
# silently ignored.
_SCENARIO_ROW_FIELDS = (
    "id",
    "name",
    "created_at",
    "updated_at",
)

# The stable, user-safe error a scenario row answers when it carries a
# lever the projection does not accept: it names nothing of the row and
# never echoes an exception, so a hostile or forward row can only ever
# produce this one message.
UNSUPPORTED_LEVER_ERROR = "Scenario uses a lever the projection does not support."


def _scenario_figures(groups: list[dict], spend: dict) -> dict:
    """The four comparison figures of one projection over the horizon.

    ``demand_value`` is the sum of ``projected_demand_value`` over every
    bucket of every site group (the horizon months, all site groups),
    ``total_spend`` the spend view's ``total_spend`` summed over the
    horizon, ``months_over_budget`` the count of horizon months whose
    spend view month has ``over_budget`` true and
    ``months_short_planned`` the count of horizon months in which at
    least one site group's bucket has ``planned_shortfall`` true.
    """
    demand_value = sum(
        bucket["projected_demand_value"]
        for group in groups
        for bucket in group["buckets"]
    )
    total_spend = sum(month["total_spend"] for month in spend["months"])
    months_over_budget = sum(1 for month in spend["months"] if month["over_budget"])
    months_short_planned = len(
        {
            bucket["month"]
            for group in groups
            for bucket in group["buckets"]
            if bucket["planned_shortfall"]
        }
    )
    return {
        "demand_value": demand_value,
        "total_spend": total_spend,
        "months_over_budget": months_over_budget,
        "months_short_planned": months_short_planned,
    }


def _one_scenario(row: dict, months: int, buckets: list, baseline: dict) -> dict:
    """One stored scenario compared against the baseline projection.

    The scenario's three supported levers are applied to
    ``aggregate_projection`` exactly the way ``GET /api/aggregate/
    projection`` applies them (``months`` and the scenario's
    ``service_level``, ``demand_multiplier`` and
    ``lead_time_multiplier``).  A ``ValueError`` the projection raises
    for the scenario's levers answers with ``error`` set to the message
    and the figures and deltas null, leaving the other scenarios to
    still answer; a row that carries a non-null value in any field
    beside the supported levers and the row's own ``id``, ``name``,
    ``created_at`` and ``updated_at`` — any lever the projection does
    not accept, whatever its name — answers with the stable
    ``UNSUPPORTED_LEVER_ERROR`` and null figures and deltas: no
    persisted lever is silently ignored, and the request never 422s
    because of the row.
    """
    service_level = row.get("service_level")
    demand_multiplier = row.get("demand_multiplier")
    lead_time_multiplier = row.get("lead_time_multiplier")
    entry = {
        "id": row.get("id"),
        "name": row.get("name"),
        "service_level": service_level,
        "demand_multiplier": demand_multiplier,
        "lead_time_multiplier": lead_time_multiplier,
        "demand_value": None,
        "total_spend": None,
        "months_over_budget": None,
        "months_short_planned": None,
        "demand_value_delta": None,
        "total_spend_delta": None,
        "error": None,
    }
    unsupported = [
        field
        for field in row
        if field not in _SUPPORTED_LEVERS
        and field not in _SCENARIO_ROW_FIELDS
        and row[field] is not None
    ]
    if unsupported:
        entry["error"] = UNSUPPORTED_LEVER_ERROR
        return entry
    try:
        groups = aggregate_projection(
            months=months,
            service_level=service_level,
            demand_multiplier=demand_multiplier,
            lead_time_multiplier=lead_time_multiplier,
        )
    except ValueError as exc:
        entry["error"] = str(exc)
        return entry
    figures = _scenario_figures(groups, _spend_view(groups, buckets))
    entry.update(figures)
    entry["demand_value_delta"] = figures["demand_value"] - baseline["demand_value"]
    entry["total_spend_delta"] = figures["total_spend"] - baseline["total_spend"]
    return entry


def build_sop_scenarios(months: int) -> dict:
    """The S&OP scenarios comparison of the ``months`` horizon months.

    ``months`` carries exactly the meaning ``aggregate_projection``
    gives it (an integer between 1 and 12 inclusive).  The builder runs
    inside ``recommendations_memo`` so the one recommendation read is
    shared by the baseline and every scenario's projection.  It reads
    the stored scenarios once through ``ScenarioPostgresRepository.
    list_all`` (newest first) and compares the first 5 against the
    baseline: ``aggregate_projection`` is called once for the baseline
    (``months`` and no levers) and once per scenario (``months`` and
    that scenario's ``service_level``, ``demand_multiplier`` and
    ``lead_time_multiplier``), each result fed to ``_spend_view`` over
    the same buckets of the current server date.

    Returns ``{"as_of", "horizon_months", "scenarios_total",
    "baseline", "scenarios"}``: ``as_of`` the current server date,
    ``horizon_months`` the requested ``months``, ``scenarios_total``
    every stored scenario (including any beyond the first 5),
    ``baseline`` the four figures of the no-lever projection and
    ``scenarios`` one entry per compared scenario (its id, name and
    levers, the four figures, the deltas against the baseline and
    ``error``).  A scenario whose levers make the projection raise
    ``ValueError`` — or that carries a lever the projection does not
    accept — answers with ``error`` set and null figures and deltas,
    the others still answering; a ``ValueError`` from the baseline
    propagates unchanged (the route answers 422).  With no stored
    scenarios ``scenarios`` is empty, ``scenarios_total`` is 0 and the
    baseline is filled.  It writes nothing and calls the ERP nowhere.
    """
    today = date.today()
    buckets = _bucket_months(today, months)
    with recommendations_memo():
        rows = _scenarios_repo.list_all()
        scenarios_total = len(rows)
        baseline_groups = aggregate_projection(months=months)
        baseline = _scenario_figures(
            baseline_groups, _spend_view(baseline_groups, buckets)
        )
        scenarios = [_one_scenario(row, months, buckets, baseline) for row in rows[:5]]
    return {
        "as_of": today,
        "horizon_months": months,
        "scenarios_total": scenarios_total,
        "baseline": baseline,
        "scenarios": scenarios,
    }
