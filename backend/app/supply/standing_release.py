"""The standing-grant run of the order plan: the due lines, released
by the grant in force instead of a person's confirm.

``run_order_plan_grant(now=None)`` runs one execution of the
``order_plan_release`` process under the grant in force for it —
``now`` the aware UTC time the run is started (the current time when
omitted) — and answers the run's audit row as a dict: ``id``,
``process`` and ``grant_id``, ``started_at`` (ISO-8601), ``outcome``
and ``reason``, ``documents`` and ``value`` (the count and the summed
value of the groups sent, as a decimal string) and ``left_out`` (the
groups the limits left out).

One run at a time: the run takes the runs' own PostgreSQL advisory
lock — its own key, never the release confirm's — on its own
connection and holds it for the whole run; a run that cannot take it
answers ``{"outcome": "busy"}`` and records nothing.  The switch and
the grant: while the stored system value of
``erp.standing_grants_enabled`` is false the run answers
``{"outcome": "off"}`` — a stored value that is not a boolean counts
as the default, on — and while no grant is in force for
``order_plan_release`` it answers ``{"outcome": "no_grant"}``;
neither records anything or reads anything more.  The data's age:
each of the five feed tables must hold Acumatica rows — a table with
none records a ``skipped`` run naming it — and the oldest of the
five's newest Acumatica ``synced_at`` more than the grant's
``max_data_age_hours`` before ``now`` records a ``skipped`` run
naming the grant's hours.  The plan: the plan of
``order_plan_release(<days>, None)`` for every supplier the plan
holds — ``<days>`` the stored ``erp.standing_grants_horizon_days``
when it is a whole number from 1 to 60, else the default 1 — with no
top-ups of any kind; a plan with no groups records ``nothing_due``
and calls nothing.  The limits: the plan's groups in the plan's
order — a group holding a line whose ``unit_cost`` is not above 0
(a line the builder gave no unit cost for, which the plan writes as
``Decimal(0)``, one priced at 0 or at a cost that rounds to 0.00,
or one priced below 0) is left out with the reason ``no_value`` —
its ``left_out`` value the summed ``value`` of the lines whose
``unit_cost`` is above 0 — before either limit is consulted and
reducing no allowance, a group whose ``value`` is above the grant's
``max_document_value`` is left out with the reason
``document_limit``, and each remaining group is sent when its value
fits what is left of the day's allowance — the grant's
``max_daily_value`` minus the summed ``value`` of the process's runs
on the UTC date of ``now`` — else it is left out with the reason
``daily_limit``; a group left out goes into ``left_out`` as
``{"site_code", "supplier_code", "value", "reason"}`` with the value
a decimal string, and a run in which every group was left out
records ``skipped`` with the reason "every due document is over a
limit" while no group was left out for ``no_value`` and the reason
"every due document is over a limit or has no value" when one was.
The send: the chosen groups
go out through the very same confirm path a person's release uses —
``app.api.aggregate.confirm_release_groups`` with the plan's
``month`` and ``unreleasable``, the chosen groups and their summed
``total_value``, the actor ``standing-grant:<id>`` — a success
records ``released``; an ``HTTPException`` records ``failed`` with
its ``detail`` as the reason; any other exception while sending
records ``failed`` with the reason "<class name>: <message>" and is
then raised again.  In every case that found a grant in force one
``standing_grant_runs`` row is written — ``started_at`` equal to
``now`` — and in every send case the groups' count and value are
what the row's ``documents`` and ``value`` hold, and what count
toward the day.  After the confirm answers, the run records the
release's short suppliers through
``app.supply.short_orders.record_short_orders`` with the grant's
horizon days, the confirm's answer and the actor
``standing-grant:<id>`` — a failure to record is logged as a warning
inside it and changes nothing of the run's outcome or its row.

``run_transfer_grant(now=None)`` runs one execution of the
``transfer_release`` process under the grant in force for it —
``now`` the aware UTC time the run is started (the current time
when omitted) — and answers the run's audit row as the same dict
``run_order_plan_grant`` answers.  One run at a time: the runs'
own advisory lock (the same key 540 the order plan's run takes) on
its own connection, held for the whole run, so one standing-grant
run runs at a time whatever its process; a run that cannot take it
answers ``{"outcome": "busy"}`` and records nothing.  The switch
(``off`` while the stored system value of
``erp.standing_grants_enabled`` is false, a stored value that is
not a boolean counting as the default on) and the grant (
``no_grant`` while no grant is in force for ``transfer_release``,
even while a grant is in force for ``order_plan_release``) as the
order plan's run; neither records anything or reads anything more.
The data's age: the same check as the order plan's run, the same
reasons, and in each case the run sends nothing — a feed table
with no Acumatica row records a ``skipped`` run naming it, data
older than the grant's ``max_data_age_hours`` records a ``skipped``
run naming the grant's hours.  The plan: the suggestions read once
with ``app.api.supply_plan.build_transfer_suggestions()`` — the
as-of is therefore the one a person's confirm resolves
(``default_as_of``), never one made from ``now``, so a grant's
order and a person's order for the same two sites on the same data
carry the same origin tag — and
``app.supply.transfer_release.transfer_release_plan(entries,
suggestions=<that read>)`` with one entry
``{"item_code", "from_site", "to_site"}`` per suggestion, in the
suggestions' order; the Odoo suggestions come back ``unreleasable``
and are never sent, a plan with no groups records ``nothing_due``
and calls nothing, and any exception while reading the suggestions
or building the plan records ``failed`` with the reason "<class
name>: <message>", ``documents`` 0 and ``value`` 0, and is raised
again.  The value of a document: each group of the plan is one
document, its value the summed ``value`` of the suggestions its
lines were matched to, each found by its ``item_code`` and the
group's ``from_site`` and ``to_site``.  The limits: the plan's
groups in the plan's order — a group holding a suggestion whose
``value`` is null or not above 0 is left out with the reason
``no_value``, before either limit is consulted and reducing no
allowance (its ``left_out`` value the summed ``value`` of its
suggestions whose value is above 0), a group whose value is above
the grant's ``max_document_value`` with ``document_limit``, and each remaining
group is sent when its value fits what is left of the day's
allowance — the grant's ``max_daily_value`` minus the summed
``value`` of the ``transfer_release`` runs whose ``started_at``
falls on the UTC date of ``now`` (``order_plan_release`` runs
never count) — else it is left out with ``daily_limit``; each
group left out goes into ``left_out`` as ``{"from_site",
"to_site", "value", "reason"}`` with the value a decimal string,
and a run in which every group was left out records ``skipped``
with the reason "every suggested transfer order is over a limit or
has no value".  The send: the lines of the chosen groups, group by
group in the plan's order, as the entries of
``app.api.supply_plan.confirm_transfer_release`` — the very same
confirm path a person's release uses, the same suggestions read,
the actor ``standing-grant:<id>``, no actor name, the route's cap
of 50 entries not applying — a success records ``released``; an
``HTTPException`` records ``failed`` with its ``detail`` as the
reason (the 409 of the transfer release's lock among them); any
other exception while sending records ``failed`` with the reason
"<class name>: <message>" and is raised again.  In every case
``documents`` and ``value`` are the count and the summed value of
the chosen groups, and they count toward the day.  Every run that
found a grant in force writes exactly one ``standing_grant_runs``
row — the process ``transfer_release``, ``started_at`` ``now``,
the grant's id — and the run answers that row.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException

from app.api.aggregate import confirm_release_groups
from app.api.supply_plan import build_transfer_suggestions, confirm_transfer_release
from app.db.connection import get_cursor
from app.feed.status import FEED_TABLES
from app.repositories.erp_standing_grants_postgres_repository import (
    ErpStandingGrantPostgresRepository,
)
from app.repositories.standing_grant_runs_postgres_repository import (
    StandingGrantRunPostgresRepository,
    acquire_grant_run_lock,
    release_grant_run_lock,
)
from app.supply.release import order_plan_release
from app.supply.short_orders import record_short_orders
from app.supply.transfer_release import transfer_release_plan

# The processes the runs execute, the switch and the horizon setting
# the order plan's run consults, and the horizon's default.
_PROCESS = "order_plan_release"
_TRANSFER_PROCESS = "transfer_release"
_ENABLED_KEY = "erp.standing_grants_enabled"
_HORIZON_KEY = "erp.standing_grants_horizon_days"
_HORIZON_DEFAULT = 1

_grants = ErpStandingGrantPostgresRepository()
_runs = StandingGrantRunPostgresRepository()


def _as_aware_utc(value: datetime) -> datetime:
    """An aware UTC copy of ``value``.

    A naive value is pinned to UTC — the run's times are UTC instants
    and ``astimezone`` on a naive value would presume the server's
    local timezone and shift the instant instead; an aware value is
    converted.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _stored_system_settings() -> dict:
    """The stored values of every system setting, in one query."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT key, value FROM settings " "WHERE scope = 'system' AND owner = ''"
        )
        return {row["key"]: row["value"] for row in cur.fetchall()}


def _switch_on(stored: dict) -> bool:
    """Whether the standing-grants switch is on.

    The stored system value applies when it is a boolean — a value
    written out of band that the setting cannot hold (not a boolean)
    counts as the default, on.
    """
    value = stored.get(_ENABLED_KEY)
    if not isinstance(value, bool):
        return True
    return value


def _horizon_days(stored: dict) -> int:
    """The stored horizon, in whole days, or the default.

    The stored value applies when it is a whole number from 1 to 60
    — a value written out of band that the setting cannot hold (not
    a whole number, or outside the bounds) never applies.
    """
    value = stored.get(_HORIZON_KEY)
    if isinstance(value, bool):
        return _HORIZON_DEFAULT
    if (
        isinstance(value, (int, float))
        and float(value).is_integer()
        and 1 <= int(value) <= 60
    ):
        return int(value)
    return _HORIZON_DEFAULT


def _newest_acumatica_synced_at(table: str) -> datetime | None:
    """The newest Acumatica ``synced_at`` of one feed table, or None.

    ``table`` is one of the fixed five feed-table names, never caller
    input, so the (deliberately unparameterised) table name in the SQL
    is out of hostile reach.  The instant is normalised to aware UTC
    so the five tables' values stay mutually comparable.
    """
    with get_cursor() as cur:
        cur.execute(
            f"SELECT MAX(synced_at) AS newest FROM {table} "
            "WHERE source_system = 'acumatica'"
        )
        newest = cur.fetchone()["newest"]
    if newest is None:
        return None
    return _as_aware_utc(newest)


def _run_row(
    process: str,
    now: datetime,
    grant: dict,
    outcome: str,
    reason: str | None,
    documents: int,
    value: Decimal,
    left_out: list[dict],
) -> dict:
    """Write the run's one audit row and answer it as a dict.

    The answer is the row's own columns: ``id``, ``process`` and
    ``grant_id``, ``started_at`` as ISO-8601, ``outcome`` and
    ``reason``, ``documents`` and ``value`` (the exact decimal string)
    and ``left_out``.
    """
    row = _runs.record(
        process=process,
        grant_id=grant["id"],
        started_at=now,
        outcome=outcome,
        reason=reason,
        documents=documents,
        value=value,
        left_out=left_out,
    )
    return {
        "id": row["id"],
        "process": row["process"],
        "grant_id": row["grant_id"],
        "started_at": row["started_at"].isoformat(),
        "outcome": row["outcome"],
        "reason": row["reason"],
        "documents": row["documents"],
        "value": str(row["value"]),
        "left_out": row["left_out"],
    }


def run_order_plan_grant(now: datetime | None = None) -> dict:
    """Run the order plan under the grant in force and answer the row.

    See the module docstring for the full contract: one run at a time
    (``busy`` while another session holds the runs' lock), the switch
    (``off``) and the grant (``no_grant``), the data's age
    (``skipped``), the plan of ``order_plan_release`` for every
    supplier (``nothing_due`` with no groups), the grant's limits
    (``no_value``, ``document_limit`` and ``daily_limit`` left out,
    ``skipped`` "every due document is over a limit" when every
    group is, the reason gaining "or has no value" when one was left
    out for ``no_value``) and the send through
    ``confirm_release_groups`` as
    ``standing-grant:<id>`` (``released``, or ``failed`` with the
    refusal's reason — and the re-raise after the row is written when
    the send raised something else).  After the confirm answers, the
    run records the release's short suppliers through
    ``app.supply.short_orders.record_short_orders`` with the grant's
    horizon days, the answer and the actor ``standing-grant:<id>`` —
    a failure to record is logged inside it and changes nothing of
    the run's outcome or its row.  Every run that found a grant in
    force writes exactly one ``standing_grant_runs`` row.
    """
    if now is None:
        now = datetime.now(UTC)
    now = _as_aware_utc(now)
    lock_conn = acquire_grant_run_lock()
    if lock_conn is None:
        # Another session is in its run: nothing is read beyond this
        # and nothing is recorded.
        return {"outcome": "busy"}
    try:
        stored = _stored_system_settings()
        if not _switch_on(stored):
            return {"outcome": "off"}
        grant = _grants.in_force_for(_PROCESS)
        if grant is None:
            return {"outcome": "no_grant"}

        # The data's age: every feed table must hold Acumatica rows,
        # and the oldest of the five must be within the grant's hours.
        newest = {table: _newest_acumatica_synced_at(table) for table in FEED_TABLES}
        for table in FEED_TABLES:
            if newest[table] is None:
                return _run_row(
                    _PROCESS,
                    now,
                    grant,
                    "skipped",
                    f"no synced Acumatica data in {table}",
                    0,
                    Decimal(0),
                    [],
                )
        oldest = min(newest.values())
        if now - oldest > timedelta(hours=grant["max_data_age_hours"]):
            return _run_row(
                _PROCESS,
                now,
                grant,
                "skipped",
                f"the synced Acumatica data is older than "
                f"{grant['max_data_age_hours']} hours",
                0,
                Decimal(0),
                [],
            )

        # The plan: every supplier the plan holds, no top-ups.
        plan = order_plan_release(_horizon_days(stored), None)
        if not plan["groups"]:
            return _run_row(
                _PROCESS,
                now,
                grant,
                "nothing_due",
                None,
                0,
                Decimal(0),
                [],
            )

        # The limits: the plan's groups in the plan's order — a
        # group holding a line whose unit cost is not above 0 (the
        # builder gave it no cost, or it is at 0, at a cost that
        # rounds to 0.00, or below 0) first, then the per-document
        # ceiling, then the day's allowance.
        max_document_value = grant["max_document_value"]
        day = now.date()
        day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        allowance = grant["max_daily_value"] - _runs.sent_value_on(
            _PROCESS, day_start, day_end
        )
        chosen: list[dict] = []
        left_out: list[dict] = []
        for group in plan["groups"]:
            if any(line["unit_cost"] <= 0 for line in group["lines"]):
                # Left out before either limit is consulted, and
                # reducing no allowance: its value the summed value
                # of the lines that do carry a cost above 0.
                left_out.append(
                    {
                        "site_code": group["site_code"],
                        "supplier_code": group["supplier_code"],
                        "value": str(
                            sum(
                                (
                                    line["value"]
                                    for line in group["lines"]
                                    if line["unit_cost"] > 0
                                ),
                                Decimal(0),
                            )
                        ),
                        "reason": "no_value",
                    }
                )
                continue
            value = group["value"]
            entry = {
                "site_code": group["site_code"],
                "supplier_code": group["supplier_code"],
                "value": str(value),
            }
            if value > max_document_value:
                left_out.append({**entry, "reason": "document_limit"})
            elif value > allowance:
                left_out.append({**entry, "reason": "daily_limit"})
            else:
                chosen.append(group)
                allowance -= value
        if not chosen:
            reason = (
                "every due document is over a limit or has no value"
                if any(entry["reason"] == "no_value" for entry in left_out)
                else "every due document is over a limit"
            )
            return _run_row(
                _PROCESS,
                now,
                grant,
                "skipped",
                reason,
                0,
                Decimal(0),
                left_out,
            )

        # The send: the chosen groups as one plan, through the same
        # confirm path a person's release uses.
        sent_value = sum((group["value"] for group in chosen), Decimal(0))
        send_plan = {
            "month": plan["month"],
            "groups": chosen,
            "unreleasable": plan["unreleasable"],
            "total_value": sent_value,
        }
        actor = f"standing-grant:{grant['id']}"
        try:
            answer = confirm_release_groups(send_plan, actor)
            # The short-order record of the release the run went out
            # for: a failure to record is logged inside it and
            # changes nothing of the run's outcome.
            record_short_orders(_horizon_days(stored), answer, actor)
        except HTTPException as exc:
            return _run_row(
                _PROCESS,
                now,
                grant,
                "failed",
                str(exc.detail),
                len(chosen),
                sent_value,
                left_out,
            )
        except Exception as exc:
            _run_row(
                _PROCESS,
                now,
                grant,
                "failed",
                f"{type(exc).__name__}: {exc}",
                len(chosen),
                sent_value,
                left_out,
            )
            raise
        return _run_row(
            _PROCESS,
            now,
            grant,
            "released",
            None,
            len(chosen),
            sent_value,
            left_out,
        )
    finally:
        release_grant_run_lock(lock_conn)


def run_transfer_grant(now: datetime | None = None) -> dict:
    """Run the transfer release under the grant in force and answer
    the row.

    See the module docstring for the full contract: one run at a
    time (``busy`` while another session holds the runs' lock), the
    switch (``off``) and the grant (``no_grant`` while no grant is
    in force for ``transfer_release``, even while a grant is in
    force for ``order_plan_release``), the data's age (``skipped``,
    the same reasons as the order plan's run, no send), the plan —
    the suggestions read once with ``build_transfer_suggestions()``
    (the as-of a person's confirm resolves, never one made from
    ``now``) and one entry per suggestion (``nothing_due`` with no
    groups, ``failed`` "<class name>: <message>" re-raised on a
    failing read) — the document values (the summed values of the
    matched suggestions), the grant's limits (``no_value``,
    ``document_limit`` and ``daily_limit`` left out, ``skipped``
    "every suggested transfer order is over a limit or has no value"
    when every group is) and the send of the chosen groups' lines
    through ``confirm_transfer_release`` as
    ``standing-grant:<id>`` (``released``, or ``failed`` with the
    refusal's reason — and the re-raise after the row is written
    when the send raised something else).  Every run that found a
    grant in force writes exactly one ``standing_grant_runs`` row,
    the process ``transfer_release``, ``started_at`` ``now``, the
    grant's id.
    """
    if now is None:
        now = datetime.now(UTC)
    now = _as_aware_utc(now)
    lock_conn = acquire_grant_run_lock()
    if lock_conn is None:
        # Another session is in its run: nothing is read beyond this
        # and nothing is recorded.
        return {"outcome": "busy"}
    try:
        stored = _stored_system_settings()
        if not _switch_on(stored):
            return {"outcome": "off"}
        grant = _grants.in_force_for(_TRANSFER_PROCESS)
        if grant is None:
            return {"outcome": "no_grant"}

        # The data's age: the same check and the same reasons as the
        # order plan's run; neither case sends anything.
        newest = {table: _newest_acumatica_synced_at(table) for table in FEED_TABLES}
        for table in FEED_TABLES:
            if newest[table] is None:
                return _run_row(
                    _TRANSFER_PROCESS,
                    now,
                    grant,
                    "skipped",
                    f"no synced Acumatica data in {table}",
                    0,
                    Decimal(0),
                    [],
                )
        oldest = min(newest.values())
        if now - oldest > timedelta(hours=grant["max_data_age_hours"]):
            return _run_row(
                _TRANSFER_PROCESS,
                now,
                grant,
                "skipped",
                f"the synced Acumatica data is older than "
                f"{grant['max_data_age_hours']} hours",
                0,
                Decimal(0),
                [],
            )

        # The plan: the suggestions read once — the as-of a person's
        # confirm resolves, never one made from now — one entry per
        # suggestion, in the suggestions' order.
        try:
            suggestions = build_transfer_suggestions()
            entries = [
                {
                    "item_code": suggestion["item_code"],
                    "from_site": suggestion["from_site"],
                    "to_site": suggestion["to_site"],
                }
                for suggestion in suggestions["suggestions"]
            ]
            plan = transfer_release_plan(entries, suggestions=suggestions)
        except Exception as exc:
            _run_row(
                _TRANSFER_PROCESS,
                now,
                grant,
                "failed",
                f"{type(exc).__name__}: {exc}",
                0,
                Decimal(0),
                [],
            )
            raise
        if not plan["groups"]:
            return _run_row(
                _TRANSFER_PROCESS,
                now,
                grant,
                "nothing_due",
                None,
                0,
                Decimal(0),
                [],
            )

        # The value of a document: the summed value of the
        # suggestions the group's lines were matched to, each found
        # by its item_code and the group's two sites.
        value_by_key = {
            (
                suggestion["item_code"],
                suggestion["from_site"],
                suggestion["to_site"],
            ): suggestion["value"]
            for suggestion in suggestions["suggestions"]
        }

        # The limits: the plan's groups in the plan's order — no
        # value first, then the per-document ceiling, then the
        # day's allowance: this process's runs on the UTC date of
        # now (order_plan_release runs never count toward it).
        day = now.date()
        day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        allowance = grant["max_daily_value"] - _runs.sent_value_on(
            _TRANSFER_PROCESS, day_start, day_end
        )
        chosen: list[dict] = []
        chosen_values: list[Decimal] = []
        left_out: list[dict] = []
        for group in plan["groups"]:
            values = [
                value_by_key[(line["item_code"], group["from_site"], group["to_site"])]
                for line in group["lines"]
            ]
            entry = {"from_site": group["from_site"], "to_site": group["to_site"]}
            if any(value is None or value <= 0 for value in values):
                # Left out before either limit is consulted, and
                # reducing no allowance: its value the summed value
                # of the suggestions whose value is above 0.
                total = sum(
                    (value for value in values if value is not None and value > 0),
                    Decimal(0),
                )
                left_out.append({**entry, "value": str(total), "reason": "no_value"})
                continue
            value = sum(values, Decimal(0))
            if value > grant["max_document_value"]:
                left_out.append(
                    {**entry, "value": str(value), "reason": "document_limit"}
                )
            elif value > allowance:
                left_out.append({**entry, "value": str(value), "reason": "daily_limit"})
            else:
                chosen.append(group)
                chosen_values.append(value)
                allowance -= value
        if not chosen:
            return _run_row(
                _TRANSFER_PROCESS,
                now,
                grant,
                "skipped",
                "every suggested transfer order is over a limit or has no value",
                0,
                Decimal(0),
                left_out,
            )

        # The send: the chosen groups' lines, group by group in the
        # plan's order, through the same confirm path a person's
        # release uses — the same suggestions read, the grant as
        # actor, no actor name, the route's 50-entry cap not
        # applying.
        send_entries = [
            {
                "item_code": line["item_code"],
                "from_site": group["from_site"],
                "to_site": group["to_site"],
            }
            for group in chosen
            for line in group["lines"]
        ]
        sent_value = sum(chosen_values, Decimal(0))
        try:
            confirm_transfer_release(
                send_entries, f"standing-grant:{grant['id']}", suggestions=suggestions
            )
        except HTTPException as exc:
            return _run_row(
                _TRANSFER_PROCESS,
                now,
                grant,
                "failed",
                str(exc.detail),
                len(chosen),
                sent_value,
                left_out,
            )
        except Exception as exc:
            _run_row(
                _TRANSFER_PROCESS,
                now,
                grant,
                "failed",
                f"{type(exc).__name__}: {exc}",
                len(chosen),
                sent_value,
                left_out,
            )
            raise
        return _run_row(
            _TRANSFER_PROCESS,
            now,
            grant,
            "released",
            None,
            len(chosen),
            sent_value,
            left_out,
        )
    finally:
        release_grant_run_lock(lock_conn)
