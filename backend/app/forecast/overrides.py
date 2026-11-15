"""Planner forecast overrides and withdrawals (issue #339).

``create_override(item_code, site_code, period_start, quantity, reason,
user)`` stores one ``override`` row in ``forecast_overrides`` for one
forecast period and returns it [rule: OVR-1].  ``period_start`` must be
the first day of one of the 12 forecast periods at the shared default
as-of date — the same 30-day horizon buckets the forecast list uses,
counted from the day after the as-of date — and the stored
``period_end`` is that period's last day.  The horizon is the
``forecast.horizon_buckets`` parameter (12): the existing
``HORIZON_BUCKETS`` constant is imported, no new literal.

Overrides are append-only [rule: OVR-2]: a new override for the same
item-site and period is a new row, and the row with the highest
database-generated ``id`` is the current one [rule: OVR-13].
``created_at`` is never used to order rows, so two rows with the same
or out-of-order timestamps still have one definite winner, and both are
stored.  ``withdraw_override(item_code, site_code, period_start,
reason, user)`` stores one ``withdrawal`` row with the stored
period's dates and returns it; the earlier rows stay [rule: OVR-9].
A completed period — its ``period_end`` on or before the shared
default as-of date — is closed: a withdrawal for it is refused and its
rows stay in the history [rule: OVR-12].

Refusals raise ``HTTPException`` (422 with a sentence in ``detail``,
409 for the two conflict cases) and store nothing:

* a reason that is empty, only spaces, or longer than 1,000
  characters [rule: OVR-5] — the reason is stored trimmed;
* a quantity that is negative or not a finite number; zero is
  allowed [rule: OVR-6];
* a ``period_start`` that is not the first day of one of the 12
  current forecast periods — a period beyond the horizon, a period
  that has completed, or a date inside a period [rule: OVR-7];
* an item-site that has no statistical forecast: one with no demand
  events, or whose demand class is ``no_demand`` [rule: OVR-8];
* a withdrawal with nothing to withdraw — no stored row, or the
  current row of the period is a withdrawal — and a withdrawal for a
  completed period (both 409) [rule: OVR-12].

``list_overrides(item_code=None, site_code=None, limit=100,
offset=0)`` returns the stored rows newest first (highest ``id``
first), optionally for one item-site (both codes together, or
neither).  Every row carries every column plus ``applies`` — true
only for an ``override`` row that is the current row of its period.
``created_by`` is the caller's token subject; ``created_by_name`` is
the caller's display name as the decision tables store it, through
the shared ``app.auth.display_name.display_name`` helper (the
token's username, else its email, else its subject, truncated to the
column width) and is never null.

``effective_buckets(item_code, site_code, buckets)`` returns the
given forecast buckets with the effective forecast added: each
bucket's ``quantity`` stays the statistical forecast, never changed
[rule: OVR-4], while ``effective_quantity`` is the sum of the
bucket's day shares and ``override_days`` how many of the bucket's
days an override covers.  The counted overrides of an item-site are
the stored periods whose current row — the highest ``id`` of the
period [rule: OVR-13] — is an ``override`` [rule: OVR-2]; a period
whose current row is a withdrawal counts for nothing, whatever older
rows it has [rule: OVR-9].  Each day of a bucket takes one day's
share of the override that covers it — the override's quantity
divided by the number of days in its period — and a day no override
covers takes one day's share of the bucket's statistical quantity
[rule: OVR-3]; where two counted overrides cover the same day, the
one whose current row has the higher ``id`` applies [rule: OVR-13].
With no day covered the effective quantity is the statistical
quantity exactly, and with every day covered by one override whose
period is exactly the bucket it is that override's quantity exactly.
Each bucket also gains ``signal_quantity``: each day of a bucket
no counted override covers takes, for each of the item-site's
counted signals — a stored row of kind ``promotion``, ``deal``
or ``event`` no ``withdrawal`` row names, from
``app.forecast.signals.counted_signals`` — whose window covers
that day, the signal's quantity divided by the number of days in
its window; a day an override covers takes no signal share.
``signal_quantity`` is the sum of those added shares and
``effective_quantity`` the existing effective quantity plus
``signal_quantity`` — a bucket with ``signal_quantity`` 0 keeps
the existing ``effective_quantity`` exactly, computed by the
existing code path.  ``quantity`` and ``override_days`` never
change.
``effective_for_all(rows)`` does the same for many item-site rows
with one query in total, not one per item-site, and each row gains
``total_effective_forecast`` (the sum of its buckets' effective
quantities) and ``overridden_buckets`` (how many of its buckets have
at least one overridden day); the rows' counted signals are read
with one query in total, not one per item-site.  Each row also
gains ``total_signal_quantity``, the sum of its buckets'
``signal_quantity``; ``total_effective_forecast`` stays the sum
of the buckets' ``effective_quantity``.  ``counted_overrides(pairs)``
is the public batched form of the counted-override rule — every
pair's counted overrides from one query, a pair with nothing
counted mapped to an empty list — and
``effective_buckets_for(buckets, counted)`` applies the day-share
rule to already-loaded counted overrides without a query.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
from fastapi import HTTPException

from app.auth.display_name import display_name
from app.auth.models import AuthUser
from app.db.connection import get_connection, get_cursor
from app.forecast.snapshots import HORIZON_BUCKETS
from app.stats.demand import BUCKET_DAYS, default_as_of, demand_stats_for_pair

_NUL = chr(0)
_MAX_REASON_LENGTH = 1000
_MAX_CODE_LENGTH = 64
_MAX_SUBJECT_LENGTH = 255

_SELECT_COLUMNS = (
    "id, item_code, site_code, period_start, period_end, kind, quantity, "
    "reason, created_by, created_by_name, created_at"
)


def _refuse(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def _validate_codes(item_code: object, site_code: object) -> None:
    """Both codes: a string, non-empty, at most 64 chars, no NUL bytes."""
    for name, value in (("item_code", item_code), ("site_code", site_code)):
        if not isinstance(value, str) or not value:
            raise _refuse(422, f"The {name} must be a non-empty string.")
        if _NUL in value:
            raise _refuse(422, f"The {name} must not contain NUL bytes.")
        if len(value) > _MAX_CODE_LENGTH:
            raise _refuse(
                422, f"The {name} must be at most {_MAX_CODE_LENGTH} characters."
            )


def _validate_reason(reason: object, *, what: str) -> str:
    """The reason, trimmed: a non-blank string of at most 1,000 characters."""
    if not isinstance(reason, str):
        raise _refuse(422, "The reason must be a string.")
    if _NUL in reason:
        raise _refuse(422, "The reason must not contain NUL bytes.")
    if len(reason) > _MAX_REASON_LENGTH:
        raise _refuse(
            422, f"The reason must be at most {_MAX_REASON_LENGTH} characters."
        )
    trimmed = reason.strip()
    if not trimmed:
        raise _refuse(
            422, f"The {what} requires a reason that is not empty or only spaces."
        )
    return trimmed


def _validate_quantity(quantity: object) -> float:
    """A finite, non-negative number; zero is allowed [rule: OVR-6]."""
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
        raise _refuse(422, "The override quantity must be a finite number.")
    if not math.isfinite(quantity):
        raise _refuse(422, "The override quantity must be a finite number.")
    if quantity < 0:
        raise _refuse(422, "The override quantity must not be negative.")
    return float(quantity)


def _validate_subject(user: AuthUser) -> None:
    """The token subject is stored in created_by (String(255)); a hostile
    token whose subject would not fit is refused instead of a database error."""
    if not isinstance(user.sub, str) or not user.sub or _NUL in user.sub:
        raise _refuse(422, "The caller's token subject cannot be stored.")
    if len(user.sub) > _MAX_SUBJECT_LENGTH:
        raise _refuse(422, "The caller's token subject is too long to be stored.")


def _period_boundaries(as_of: date, period_start: date) -> tuple[date, date]:
    """The stored (period_start, period_end) when ``period_start`` is
    the first day of one of the 12 forecast periods at ``as_of``.

    The periods are the horizon buckets the forecast list uses: period
    1 runs from the day after ``as_of`` through ``as_of + 30`` days,
    and each later period follows for 30 more days.  Anything else is
    refused [rule: OVR-7]: a period that has completed (on or before
    ``as_of``), a date inside a period, or a period beyond the horizon.
    """
    offset_days = (period_start - as_of).days
    if offset_days <= 0:
        raise _refuse(
            422,
            f"The period has already completed; overrides are only accepted "
            f"for one of the {HORIZON_BUCKETS} current forecast periods.",
        )
    if offset_days > BUCKET_DAYS * HORIZON_BUCKETS:
        raise _refuse(
            422,
            f"The period is beyond the {HORIZON_BUCKETS} forecast periods of "
            "the current horizon.",
        )
    period_index = (offset_days - 1) // BUCKET_DAYS
    if offset_days != BUCKET_DAYS * period_index + 1:
        raise _refuse(
            422,
            "The period start must be the first day of one of the "
            f"{HORIZON_BUCKETS} forecast periods.",
        )
    return (
        period_start,
        as_of + timedelta(days=BUCKET_DAYS * (period_index + 1)),
    )


def _has_statistical_forecast(item_code: str, site_code: str) -> bool:
    """True when the item-site has a statistical forecast [rule: OVR-8].

    A pair with no demand events at all, or whose demand class is
    ``no_demand``, has no forecast to replace and nothing to compare
    an override with.
    """
    stats = demand_stats_for_pair(item_code, site_code)
    return stats is not None and stats["demand_class"] != "no_demand"


def _row(row: psycopg2.extras.RealDictRow, applies: bool) -> dict:
    return {
        "id": row["id"],
        "item_code": row["item_code"],
        "site_code": row["site_code"],
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "kind": row["kind"],
        "quantity": None if row["quantity"] is None else float(row["quantity"]),
        "reason": row["reason"],
        "created_by": row["created_by"],
        "created_by_name": row["created_by_name"],
        "created_at": row["created_at"],
        "applies": applies,
    }


def create_override(
    item_code: object,
    site_code: object,
    period_start: object,
    quantity: object,
    reason: object,
    user: AuthUser,
) -> dict:
    """Store one ``override`` row and return it [rule: OVR-1].

    ``period_start`` must be the first day of one of the 12 forecast
    periods at the shared default as-of date; the stored
    ``period_end`` is that period's last day.  Every refusal is a 422
    with a sentence in ``detail`` and nothing is stored.  A new
    override for the same item-site and period is a new row
    [rule: OVR-2].
    """
    _validate_codes(item_code, site_code)
    if not isinstance(period_start, date):
        raise _refuse(422, "The period start must be a date.")
    trimmed_reason = _validate_reason(reason, what="override")
    stored_quantity = _validate_quantity(quantity)
    _validate_subject(user)

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            as_of = default_as_of(cur)
        period_start, period_end = _period_boundaries(as_of, period_start)
        if not _has_statistical_forecast(item_code, site_code):
            raise _refuse(
                422,
                "The item site has no statistical forecast to override.",
            )
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO forecast_overrides "
                "(item_code, site_code, period_start, period_end, kind, "
                "quantity, reason, created_by, created_by_name) "
                "VALUES (%s, %s, %s, %s, 'override', %s, %s, %s, %s) "
                "RETURNING " + _SELECT_COLUMNS,
                (
                    item_code,
                    site_code,
                    period_start,
                    period_end,
                    stored_quantity,
                    trimmed_reason,
                    user.sub,
                    display_name(user),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        # The new row has the highest id of its period: it is the
        # current row of the period it overrides [rule: OVR-2].
        return _row(row, applies=True)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def withdraw_override(
    item_code: object,
    site_code: object,
    period_start: object,
    reason: object,
    user: AuthUser,
) -> dict:
    """Store one ``withdrawal`` row with the stored period's dates [rule: OVR-9].

    The earlier rows stay in the history.  Refused with 409 when the
    current row of the period is not an override (there is nothing to
    withdraw) and when the period has completed — its
    ``period_end`` is on or before the shared default as-of date
    [rule: OVR-12]; its rows stay.  A blank reason (or one over
    1,000 characters) is refused with 422 and nothing is stored.
    """
    _validate_codes(item_code, site_code)
    if not isinstance(period_start, date):
        raise _refuse(422, "The period start must be a date.")
    trimmed_reason = _validate_reason(reason, what="withdrawal")
    _validate_subject(user)

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            as_of = default_as_of(cur)
            cur.execute(
                "SELECT " + _SELECT_COLUMNS + " FROM forecast_overrides "
                "WHERE item_code = %s AND site_code = %s AND period_start = %s "
                "ORDER BY id DESC LIMIT 1",
                (item_code, site_code, period_start),
            )
            current = cur.fetchone()
        if current is None or current["kind"] != "override":
            raise _refuse(
                409,
                "There is no current override to withdraw for this period.",
            )
        if current["period_end"] <= as_of:
            raise _refuse(
                409,
                "The period has already completed; its override can no "
                "longer be withdrawn.",
            )
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO forecast_overrides "
                "(item_code, site_code, period_start, period_end, kind, "
                "quantity, reason, created_by, created_by_name) "
                "VALUES (%s, %s, %s, %s, 'withdrawal', NULL, %s, %s, %s) "
                "RETURNING " + _SELECT_COLUMNS,
                (
                    item_code,
                    site_code,
                    current["period_start"],
                    current["period_end"],
                    trimmed_reason,
                    user.sub,
                    display_name(user),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        # The withdrawal is the current row of the period, so no row
        # of the period applies: the effective forecast returns to the
        # statistical forecast [rule: OVR-9].
        return _row(row, applies=False)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_overrides(
    item_code: str | None = None,
    site_code: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """The stored override rows, newest first (highest ``id`` first).

    ``item_code`` and ``site_code`` select one item-site when both are
    given (giving exactly one of the two is refused with 422); with
    neither they select every stored row.  ``limit`` (1 to 1000) and
    ``offset`` page the list.  Each row carries every column plus
    ``applies`` — true only for an ``override`` row that is the
    current row of its period (the highest ``id`` of the period).
    """
    if (item_code is None) != (site_code is None):
        raise _refuse(
            422,
            "The item_code and site_code filters must be given together.",
        )
    if item_code is not None:
        _validate_codes(item_code, site_code)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise _refuse(422, "The limit must be an integer from 1 to 1000.")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise _refuse(422, "The offset must be a non-negative integer.")

    sql = (
        "SELECT " + _SELECT_COLUMNS + ", ROW_NUMBER() OVER ("
        "PARTITION BY item_code, site_code, period_start ORDER BY id DESC"
        ") AS _rank FROM forecast_overrides"
    )
    params: list[object] = []
    if item_code is not None:
        sql += " WHERE item_code = %s AND site_code = %s"
        params.extend([item_code, site_code])
    sql += " ORDER BY id DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.commit()
        return [
            _row(
                row,
                applies=row["kind"] == "override" and row["_rank"] == 1,
            )
            for row in rows
        ]
    finally:
        conn.close()


_CURRENT_OVERRIDE_SELECT = (
    "SELECT fo.item_code AS item_code, fo.site_code AS site_code, "
    "fo.id AS id, fo.period_start AS period_start, "
    "fo.period_end AS period_end, fo.quantity AS quantity "
    "FROM forecast_overrides AS fo "
    "JOIN (SELECT item_code, site_code, period_start, MAX(id) AS current_id "
    "      FROM forecast_overrides {scope} "
    "      GROUP BY item_code, site_code, period_start) AS current "
    "ON current.item_code = fo.item_code AND current.site_code = fo.site_code "
    "AND fo.id = current.current_id "
    "WHERE fo.kind = 'override' ORDER BY fo.id"
)


def _counted_overrides(
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], list[tuple[int, date, date, float]]]:
    """The counted overrides of many item-sites, read with one query.

    An item-site's counted overrides are the stored periods
    (``period_start``) whose current row — the highest ``id`` of the
    period [rule: OVR-13] — is an ``override`` [rule: OVR-2]; a
    period whose current row is a withdrawal counts for nothing,
    whatever older rows it has [rule: OVR-9].  Every pair's counted
    overrides come from a single query, whatever how many pairs are
    asked for.
    """
    unique_pairs = list(dict.fromkeys(pairs))
    if not unique_pairs:
        return {}
    if len(unique_pairs) == 1:
        scope = "WHERE item_code = %s AND site_code = %s"
        params: list[object] = list(unique_pairs[0])
    else:
        scope = "WHERE (item_code, site_code) IN %s"
        params = [tuple(unique_pairs)]
    with get_cursor() as cur:
        cur.execute(_CURRENT_OVERRIDE_SELECT.format(scope=scope), params)
        rows = cur.fetchall()
    counted: dict[tuple[str, str], list[tuple[int, date, date, float]]] = {
        pair: [] for pair in unique_pairs
    }
    for row in rows:
        counted[(row["item_code"], row["site_code"])].append(
            (
                int(row["id"]),
                row["period_start"],
                row["period_end"],
                float(row["quantity"]),
            )
        )
    return counted


def _override_effective(
    bucket: dict,
    overrides: list[tuple[int, date, date, float]],
) -> tuple[float, int, dict[date, tuple[float, int]]]:
    """One bucket's override day shares.

    ``overrides`` are the counted overrides, in ascending ``id``
    order: ``(id, period_start, period_end, quantity)``.  Each day of
    the bucket takes one day's share of the override that covers it —
    the override's quantity over the number of days in its period —
    and a day no override covers takes one day's share of the
    bucket's statistical quantity [rule: OVR-3].  Where two counted
    overrides cover the same day, the one with the higher ``id``
    applies [rule: OVR-13].  Returns the sum of the bucket's day
    shares — with no day covered the statistical quantity exactly,
    with every day covered by one override whose period is exactly
    the bucket that override's quantity exactly — the number of the
    bucket's days an override covers, from 0 to the bucket's length,
    and those covered days, date by date.
    """
    bucket_start = bucket["bucket_start"]
    bucket_end = bucket["bucket_end"]
    length = (bucket_end - bucket_start).days + 1
    day_share: dict[date, tuple[float, int]] = {}
    for _override_id, period_start, period_end, quantity in overrides:
        period_days = (period_end - period_start).days + 1
        first = max(bucket_start, period_start)
        last = min(bucket_end, period_end)
        if first <= last:
            for offset in range((last - first).days + 1):
                day_share[first + timedelta(days=offset)] = (
                    quantity,
                    period_days,
                )
    override_days = len(day_share)
    shares: dict[tuple[float, int], int] = {}
    for share in day_share.values():
        shares[share] = shares.get(share, 0) + 1
    quantity = bucket["quantity"]
    if override_days == 0:
        effective_quantity = quantity
    elif override_days == length and len(shares) == 1:
        override_quantity, period_days = next(iter(shares))
        effective_quantity = (
            override_quantity
            if period_days == length
            else length * (override_quantity / period_days)
        )
    else:
        uncovered = length - override_days
        effective_quantity = uncovered * (quantity / length) if uncovered else 0.0
        for (override_quantity, period_days), count in shares.items():
            effective_quantity += count * (override_quantity / period_days)
    return effective_quantity, override_days, day_share


def _signal_shares(
    bucket: dict,
    covered: dict[date, tuple[float, int]],
    signals: list[tuple[int, date, date, float]],
) -> float:
    """The counted signals' shares of the bucket's uncovered days.

    ``signals`` are the item-site's counted signals:
    ``(id, start_date, end_date, quantity)``.  Each day of the bucket
    no counted override covers (the ``covered`` days) takes, for each
    signal whose window covers that day, the signal's quantity
    divided by the number of days in its window; a day an override
    covers takes no signal share.  Returns the sum of those added
    shares — 0.0 while no counted signal lifts an uncovered day.
    """
    if not signals:
        return 0.0
    bucket_start = bucket["bucket_start"]
    bucket_end = bucket["bucket_end"]
    total = 0.0
    for _signal_id, start_date, end_date, quantity in signals:
        first = max(bucket_start, start_date)
        last = min(bucket_end, end_date)
        if first > last:
            continue
        daily = quantity / ((end_date - start_date).days + 1)
        for offset in range((last - first).days + 1):
            if first + timedelta(days=offset) not in covered:
                total += daily
    return total


def _effective_buckets(
    buckets: list[dict],
    overrides: list[tuple[int, date, date, float]],
) -> list[dict]:
    """The given buckets with their effective forecast added.

    ``overrides`` are the counted overrides, in ascending ``id``
    order: ``(id, period_start, period_end, quantity)``.  Each day of
    a bucket takes one day's share of the override that covers it —
    the override's quantity over the number of days in its period —
    and a day no override covers takes one day's share of the
    bucket's statistical quantity [rule: OVR-3].  Where two counted
    overrides cover the same day, the one with the higher ``id``
    applies [rule: OVR-13].  ``quantity`` is never changed
    [rule: OVR-4]; ``effective_quantity`` is the sum of the bucket's
    day shares and ``override_days`` the number of the bucket's days
    an override covers, from 0 to the bucket's length.  With no day
    covered the effective quantity equals the statistical quantity
    exactly, and with every day covered by one override whose period
    is exactly the bucket it equals that override's quantity exactly.
    """
    effective: list[dict] = []
    for bucket in buckets:
        effective_quantity, override_days, _covered = _override_effective(
            bucket, overrides
        )
        effective.append(
            {
                **bucket,
                "effective_quantity": effective_quantity,
                "override_days": override_days,
            }
        )
    return effective


def _effective_buckets_with_signals(
    buckets: list[dict],
    overrides: list[tuple[int, date, date, float]],
    signals: list[tuple[int, date, date, float]],
) -> list[dict]:
    """The given buckets with the effective forecast and the counted
    signals' shares added.

    The day-share rule is exactly the one ``effective_buckets`` and
    ``effective_for_all`` apply [rule: OVR-3] [rule: OVR-4]
    [rule: OVR-11], with the counted signals beside it: each bucket
    keeps its statistical ``quantity`` unchanged and gains
    ``signal_quantity`` — the sum of the signal shares of the days no
    counted override covers — and ``effective_quantity`` the existing
    effective quantity plus ``signal_quantity``, which a bucket with
    ``signal_quantity`` 0 keeps exactly, computed by the existing
    code path with no floating-point difference.  ``override_days``
    is how many of the bucket's days an override covers, from 0 to
    the bucket's length; a counted signal never changes it.
    """
    effective: list[dict] = []
    for bucket in buckets:
        effective_quantity, override_days, covered = _override_effective(
            bucket, overrides
        )
        signal_quantity = _signal_shares(bucket, covered, signals)
        if signal_quantity:
            effective_quantity += signal_quantity
        effective.append(
            {
                **bucket,
                "effective_quantity": effective_quantity,
                "override_days": override_days,
                "signal_quantity": signal_quantity,
            }
        )
    return effective


def _with_effective_row(row: dict, counted: dict, signals: dict) -> dict:
    """One row with its buckets' effective forecast and its row totals."""
    pair = (row["item_code"], row["site_code"])
    buckets = _effective_buckets_with_signals(
        row["forecast"], counted[pair], signals[pair]
    )
    row["forecast"] = buckets
    row["total_effective_forecast"] = sum(
        bucket["effective_quantity"] for bucket in buckets
    )
    row["total_signal_quantity"] = sum(bucket["signal_quantity"] for bucket in buckets)
    row["overridden_buckets"] = sum(
        1 for bucket in buckets if bucket["override_days"] > 0
    )
    return row


def effective_buckets(
    item_code: str, site_code: str, buckets: list[dict]
) -> list[dict]:
    """The given forecast buckets with the effective forecast added.

    Each bucket keeps its statistical ``quantity`` unchanged
    [rule: OVR-4] and gains ``effective_quantity`` (the sum of the
    bucket's day shares) and ``override_days`` (how many of the
    bucket's days an override covers, from 0 to the bucket's length).
    The item-site's counted overrides — the stored periods whose
    current row, the highest ``id`` of the period, is an ``override``
    [rule: OVR-2] [rule: OVR-9] [rule: OVR-13] — are read with one
    query.  Each day of a bucket takes one day's share of the
    override that covers it, the override's quantity over the days of
    its period; a day no override covers takes one day's share of the
    bucket's statistical quantity [rule: OVR-3] [rule: OVR-11]; where
    two counted overrides cover the same day, the one with the higher
    ``id`` applies.  With no day covered the effective quantity
    equals the statistical quantity exactly, and with every day
    covered by one override whose period is exactly the bucket it
    equals that override's quantity exactly.  Each bucket also gains
    ``signal_quantity`` — each day no counted override covers takes,
    for each of the item-site's counted signals (one
    ``app.forecast.signals.counted_signals`` query) whose window
    covers that day, the signal's quantity over the days of its
    window — and ``effective_quantity`` the existing effective
    quantity plus ``signal_quantity``.
    """
    pair = (item_code, site_code)
    counted = counted_overrides([pair])
    # Imported at call time: app.forecast.signals imports this
    # module's validators, so a module-level import would cycle.
    from app.forecast.signals import counted_signals

    signals = counted_signals([pair])
    return _effective_buckets_with_signals(buckets, counted[pair], signals[pair])


def effective_for_all(rows: list[dict]) -> list[dict]:
    """The effective forecast added to every row, two queries in total.

    ``rows`` are forecast rows as built for one as-of date (each
    carrying ``item_code``, ``site_code`` and its forecast buckets).
    Each row's buckets gain ``effective_quantity`` and
    ``override_days`` beside the never-changed ``quantity``
    [rule: OVR-4], and each row gains ``total_effective_forecast`` —
    the sum of its buckets' effective quantities — and
    ``overridden_buckets``, how many of its buckets have at least one
    overridden day.  All the rows' counted overrides are read with
    one query in total, not one per item-site, and the rows' counted
    signals (``app.forecast.signals.counted_signals``) with one
    query in total as well; each
    row also gains ``total_signal_quantity``, the sum of its
    buckets' ``signal_quantity``, and ``total_effective_forecast``
    stays the sum of the buckets' ``effective_quantity``.
    """
    pairs = [(row["item_code"], row["site_code"]) for row in rows]
    counted = _counted_overrides(pairs)
    # Imported at call time: app.forecast.signals imports this
    # module's validators, so a module-level import would cycle.
    from app.forecast.signals import counted_signals

    signals = counted_signals(pairs)
    return [_with_effective_row(row, counted, signals) for row in rows]


def counted_overrides(
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], list[tuple[int, date, date, float]]]:
    """The counted overrides of many item-sites, read with one query.

    The public batched form of the rule ``effective_buckets`` and
    ``effective_for_all`` apply [rule: OVR-2] [rule: OVR-9]
    [rule: OVR-13]: an item-site's counted overrides are its stored
    periods whose current row — the highest ``id`` of the period — is
    an ``override``; a period whose current row is a withdrawal counts
    for nothing.  Every pair's counted overrides come from a single
    query, whatever how many pairs are asked for; a pair with nothing
    counted maps to an empty list, so every requested pair is a key of
    the result.
    """
    return _counted_overrides(pairs)


def effective_buckets_for(
    buckets: list[dict],
    counted: list[tuple[int, date, date, float]],
) -> list[dict]:
    """The effective forecast added to the given buckets, no query.

    ``counted`` is one item-site's counted overrides, as
    ``counted_overrides`` returns them; the day-share rule is exactly
    the one ``effective_buckets`` and ``effective_for_all`` apply
    [rule: OVR-3] [rule: OVR-4] [rule: OVR-11]: each bucket keeps its
    statistical ``quantity`` unchanged and gains ``effective_quantity``
    (the sum of the bucket's day shares) and ``override_days`` (how
    many of the bucket's days an override covers, from 0 to the
    bucket's length).
    """
    return _effective_buckets(buckets, counted)
