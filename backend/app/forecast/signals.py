"""Stored demand signals and their withdrawals (issue #963).

``create_signal(kind, item_code, site_code, start_date, end_date,
quantity, reason, user)`` stores one row of kind ``promotion``,
``deal`` or ``event`` in ``demand_signals`` for one item-site date
window and returns it.  ``start_date`` must be after the shared
default as-of date (a signal cannot lift demand that has already
happened) and ``end_date`` must not be before ``start_date`` or past
the last day of the 12 forecast periods at that as-of date — the
same 30-day horizon the forecast list uses: the horizon is the
``HORIZON_BUCKETS`` constant (12) and the bucket length the
``BUCKET_DAYS`` constant, both imported, no new literals.

Signals are append-only: the ``demand_signals_guard`` trigger refuses
every UPDATE and DELETE of a row.  ``withdraw_signal(signal_id,
reason, user)`` stores one ``withdrawal`` row — null quantity,
``withdraws_id`` the signal's id — carrying the signal's item-site
and dates and returns it; the signal row stays in the history.
Refused with 409 when the id is already a withdrawal, when the
signal has already been withdrawn, and when the signal's window has
passed — its ``end_date`` on or before the shared default as-of
date.  The partial unique index on ``withdraws_id`` (at most one row
per non-null ``withdraws_id``) keeps two concurrent withdrawals from
storing two rows: the second insert is refused and the caller
answers 409.

Refusals raise ``HTTPException`` (a sentence in ``detail``, nothing
stored):

* a ``kind`` other than "promotion", "deal" or "event";
* a quantity that is not above 0, not a finite number, or above
  1,000,000,000;
* a reason that is empty, only spaces, or longer than 1,000
  characters — the reason is stored trimmed;
* a ``start_date`` on or before the shared default as-of date;
* an ``end_date`` before ``start_date``, or after the last day of
  the 12 forecast periods at that as-of date;
* an item-site that has no statistical forecast: one with no demand
  events, or whose demand class is ``no_demand``.

``list_signals(item_code=None, site_code=None, include_withdrawn=
False, limit=100)`` returns the signal rows — never a withdrawal row
as its own entry — newest first (highest ``id`` first), optionally
for one item-site (both codes together, or neither).  Each row
carries the signal's columns plus ``withdrawn`` (true when a
withdrawal row exists for it), ``withdrawn_at``,
``withdrawn_by_name`` and ``withdrawn_reason`` (the withdrawal's
values, null while the signal stands).  The default list omits
withdrawn signals; ``include_withdrawn`` lists them too.

``created_by`` is the caller's token subject; ``created_by_name`` is
the caller's display name as the decision tables store it, through
the shared ``app.auth.display_name.display_name`` helper.  The code,
reason and subject validation and the statistical-forecast check are
the ones ``app.forecast.overrides`` applies to its own rows,
reused here, not copied.

``counted_signals(pairs)`` is the batched read the effective
forecast applies: every pair's counted signals — its stored rows
of kind ``promotion``, ``deal`` or ``event`` no ``withdrawal``
row names, as ``(id, start_date, end_date, quantity)`` — from one
query whatever the number of pairs, a pair with nothing counted
mapped to an empty list, so every requested pair is a key of the
result.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
from psycopg2 import errors as pgerrors

from app.auth.display_name import display_name
from app.auth.models import AuthUser
from app.db.connection import get_connection, get_cursor
from app.forecast.overrides import (
    _has_statistical_forecast,
    _refuse,
    _validate_codes,
    _validate_reason,
    _validate_subject,
)
from app.forecast.snapshots import HORIZON_BUCKETS
from app.stats.demand import BUCKET_DAYS, default_as_of

_SIGNAL_KINDS = ("promotion", "deal", "event")
_MAX_QUANTITY = 1_000_000_000
# The last day of the 12 forecast periods at an as-of date is the
# as-of date plus one full horizon.
_HORIZON_SPAN = BUCKET_DAYS * HORIZON_BUCKETS
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500

_SELECT_COLUMNS = (
    "id, kind, item_code, site_code, start_date, end_date, quantity, "
    "reason, created_by_name, created_at"
)


def _validate_signal_kind(kind: object) -> None:
    """The kind: one of promotion, deal or event — a withdrawal is
    stored by the withdrawal, never created directly."""
    if not isinstance(kind, str) or kind not in _SIGNAL_KINDS:
        raise _refuse(422, "The kind must be one of promotion, deal or event.")


def _validate_signal_quantity(quantity: object) -> float:
    """A finite number above 0 and at most 1,000,000,000."""
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
        raise _refuse(422, "The demand signal quantity must be a finite number.")
    if not math.isfinite(quantity):
        raise _refuse(422, "The demand signal quantity must be a finite number.")
    if quantity <= 0:
        raise _refuse(422, "The demand signal quantity must be above 0.")
    if quantity > _MAX_QUANTITY:
        raise _refuse(
            422,
            f"The demand signal quantity must be at most {_MAX_QUANTITY:,} units.",
        )
    return float(quantity)


def _row(row: psycopg2.extras.RealDictRow, withdrawn: bool) -> dict:
    """A stored row in the list shape: the signal's columns plus the
    withdrawal's values (null while the signal stands)."""
    return {
        "id": row["id"],
        "kind": row["kind"],
        "item_code": row["item_code"],
        "site_code": row["site_code"],
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "quantity": None if row["quantity"] is None else float(row["quantity"]),
        "reason": row["reason"],
        "created_by_name": row["created_by_name"],
        "created_at": row["created_at"],
        "withdrawn": withdrawn,
        "withdrawn_at": None,
        "withdrawn_by_name": None,
        "withdrawn_reason": None,
    }


def create_signal(
    kind: object,
    item_code: object,
    site_code: object,
    start_date: object,
    end_date: object,
    quantity: object,
    reason: object,
    user: AuthUser,
) -> dict:
    """Store one signal row (promotion, deal or event) and return it.

    ``start_date`` must be after the shared default as-of date and
    ``end_date`` not before ``start_date`` and not past the last day
    of the 12 forecast periods at that as-of date.  Every refusal is
    a 422 with a sentence in ``detail`` and nothing is stored.
    """
    _validate_codes(item_code, site_code)
    _validate_signal_kind(kind)
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        raise _refuse(422, "The start and end dates must be dates.")
    if end_date < start_date:
        raise _refuse(422, "The end date must not be before the start date.")
    stored_quantity = _validate_signal_quantity(quantity)
    trimmed_reason = _validate_reason(reason, what="demand signal")
    _validate_subject(user)

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            as_of = default_as_of(cur)
        if start_date <= as_of:
            raise _refuse(
                422,
                "The start date must be after the current as-of date; a "
                "signal cannot lift demand that has already happened.",
            )
        if end_date > as_of + timedelta(days=_HORIZON_SPAN):
            raise _refuse(
                422,
                f"The end date is beyond the {HORIZON_BUCKETS} forecast "
                "periods of the current horizon.",
            )
        if not _has_statistical_forecast(item_code, site_code):
            raise _refuse(
                422,
                "The item site has no statistical forecast to signal.",
            )
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO demand_signals "
                "(kind, item_code, site_code, start_date, end_date, "
                "quantity, reason, created_by, created_by_name) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING " + _SELECT_COLUMNS,
                (
                    kind,
                    item_code,
                    site_code,
                    start_date,
                    end_date,
                    stored_quantity,
                    trimmed_reason,
                    user.sub,
                    display_name(user),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return _row(row, withdrawn=False)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def withdraw_signal(
    signal_id: object,
    reason: object,
    user: AuthUser,
) -> dict:
    """Store one ``withdrawal`` row carrying the signal's item-site
    and dates, and return it.

    The signal row stays in the history.  Unknown ids answer 404.
    Refused with 409 — the stored rows unchanged — when the id is
    already a withdrawal, when the signal has already been
    withdrawn, and when the signal's window has passed: its
    ``end_date`` on or before the shared default as-of date.  The
    partial unique index on ``withdraws_id`` keeps two concurrent
    withdrawals from storing two rows; the second answers 409.  A
    blank reason (or one over 1,000 characters) is refused with 422
    and nothing is stored.
    """
    if not isinstance(signal_id, int) or isinstance(signal_id, bool) or signal_id < 1:
        raise _refuse(404, "No demand signal has that id.")
    # ``id`` is an integer (int4) identity column: an id that does not
    # fit cannot exist, so it is unknown — never a database error.
    if signal_id > 2**31 - 1:
        raise _refuse(404, "No demand signal has that id.")
    trimmed_reason = _validate_reason(reason, what="withdrawal")
    _validate_subject(user)

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            as_of = default_as_of(cur)
            cur.execute(
                "SELECT " + _SELECT_COLUMNS + " FROM demand_signals " "WHERE id = %s",
                (signal_id,),
            )
            signal = cur.fetchone()
        if signal is None:
            raise _refuse(404, "No demand signal has that id.")
        if signal["kind"] == "withdrawal":
            raise _refuse(409, "The given row is a withdrawal and cannot be withdrawn.")
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM demand_signals WHERE withdraws_id = %s",
                (signal_id,),
            )
            already = cur.fetchone()
        if already is not None:
            raise _refuse(409, "The signal has already been withdrawn.")
        if signal["end_date"] <= as_of:
            raise _refuse(
                409,
                "The signal's window has already passed; it can no longer "
                "be withdrawn.",
            )
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO demand_signals "
                    "(kind, item_code, site_code, start_date, end_date, "
                    "quantity, withdraws_id, reason, created_by, "
                    "created_by_name) "
                    "VALUES ('withdrawal', %s, %s, %s, %s, NULL, %s, %s, "
                    "%s, %s) RETURNING " + _SELECT_COLUMNS,
                    (
                        signal["item_code"],
                        signal["site_code"],
                        signal["start_date"],
                        signal["end_date"],
                        signal_id,
                        trimmed_reason,
                        user.sub,
                        display_name(user),
                    ),
                )
                row = cur.fetchone()
        except pgerrors.UniqueViolation:
            # A concurrent withdrawal stored its row first: the
            # partial unique index on withdraws_id keeps the history
            # to one withdrawal per signal.
            raise _refuse(409, "The signal has already been withdrawn.")
        conn.commit()
        # The stored withdrawal row, in the list shape: the
        # withdrawal itself is never withdrawn.
        return _row(row, withdrawn=False)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_signals(
    item_code: str | None = None,
    site_code: str | None = None,
    include_withdrawn: bool = False,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict]:
    """The stored signal rows, newest first (highest ``id`` first).

    ``item_code`` and ``site_code`` select one item-site when both
    are given (giving exactly one of the two is refused with 422);
    with neither they select every signal row.  A withdrawal row is
    never listed as its own entry: it only shows up beside the
    signal it withdrew, as that signal's ``withdrawn`` and
    ``withdrawn_*`` values.  The default list omits withdrawn
    signals; ``include_withdrawn=True`` lists them too.  ``limit``
    (default 100, 1 to 500) pages the list.
    """
    if (item_code is None) != (site_code is None):
        raise _refuse(
            422,
            "The item_code and site_code filters must be given together.",
        )
    if item_code is not None:
        _validate_codes(item_code, site_code)
    if not isinstance(include_withdrawn, bool):
        raise _refuse(422, "The include_withdrawn filter must be a boolean.")
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise _refuse(422, f"The limit must be an integer from 1 to {_MAX_LIMIT}.")
    if not 1 <= limit <= _MAX_LIMIT:
        raise _refuse(422, f"The limit must be an integer from 1 to {_MAX_LIMIT}.")

    sql = (
        "SELECT s.id, s.kind, s.item_code, s.site_code, s.start_date, "
        "s.end_date, s.quantity, s.reason, s.created_by_name, s.created_at, "
        "w.created_at AS withdrawn_at, "
        "w.created_by_name AS withdrawn_by_name, w.reason AS withdrawn_reason "
        "FROM demand_signals s "
        "LEFT JOIN demand_signals w ON w.withdraws_id = s.id "
        "WHERE s.kind <> 'withdrawal'"
    )
    params: list[object] = []
    if item_code is not None:
        sql += " AND s.item_code = %s AND s.site_code = %s"
        params.extend([item_code, site_code])
    if not include_withdrawn:
        sql += " AND w.id IS NULL"
    sql += " ORDER BY s.id DESC LIMIT %s"
    params.append(limit)

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.commit()
        return [_list_row(row) for row in rows]
    finally:
        conn.close()


def _list_row(row: psycopg2.extras.RealDictRow) -> dict:
    """A list row: the signal's columns plus the withdrawal's values
    (null while the signal stands)."""
    return {
        "id": row["id"],
        "kind": row["kind"],
        "item_code": row["item_code"],
        "site_code": row["site_code"],
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "quantity": None if row["quantity"] is None else float(row["quantity"]),
        "reason": row["reason"],
        "created_by_name": row["created_by_name"],
        "created_at": row["created_at"],
        "withdrawn": row["withdrawn_at"] is not None,
        "withdrawn_at": row["withdrawn_at"],
        "withdrawn_by_name": row["withdrawn_by_name"],
        "withdrawn_reason": row["withdrawn_reason"],
    }


def counted_signals(
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], list[tuple[int, date, date, float]]]:
    """The counted signals of many item-sites, read with one query.

    An item-site's counted signals are its stored rows of kind
    ``promotion``, ``deal`` or ``event`` no ``withdrawal`` row names —
    a withdrawn signal counts for nothing, its row staying in the
    history.  Every pair's counted signals come from a single query,
    whatever how many pairs are asked for; a pair with nothing counted
    maps to an empty list, so every requested pair is a key of the
    result.
    """
    unique_pairs = list(dict.fromkeys(pairs))
    if not unique_pairs:
        return {}
    if len(unique_pairs) == 1:
        scope = "AND s.item_code = %s AND s.site_code = %s"
        params: list[object] = list(unique_pairs[0])
    else:
        scope = "AND (s.item_code, s.site_code) IN %s"
        params = [tuple(unique_pairs)]
    with get_cursor() as cur:
        cur.execute(
            "SELECT s.item_code AS item_code, s.site_code AS site_code, "
            "s.id AS id, s.start_date AS start_date, s.end_date AS end_date, "
            "s.quantity AS quantity "
            "FROM demand_signals AS s "
            "WHERE s.kind IN ('promotion', 'deal', 'event') "
            "AND NOT EXISTS (SELECT 1 FROM demand_signals AS w "
            "WHERE w.withdraws_id = s.id AND w.kind = 'withdrawal') "
            + scope
            + " ORDER BY s.id",
            params,
        )
        rows = cur.fetchall()
    counted: dict[tuple[str, str], list[tuple[int, date, date, float]]] = {
        pair: [] for pair in unique_pairs
    }
    for row in rows:
        counted[(row["item_code"], row["site_code"])].append(
            (
                int(row["id"]),
                row["start_date"],
                row["end_date"],
                float(row["quantity"]),
            )
        )
    return counted
