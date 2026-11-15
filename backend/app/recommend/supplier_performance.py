"""Supplier performance over observed purchase-order receipts.

``supplier_performance`` reads ``lead_time_observations`` exactly once
through the ``LeadTimeObservationPostgresRepository`` (a single
unfiltered ``list_rows`` call) and writes nothing.  The window is the
``window_days`` days ending on ``as_of`` (the server's current date
when the caller supplies none); a receipt is in the window when its
``received_on`` falls in that range, start day included.

One entry per ``supplier_code`` with at least one in-window receipt.
A receipt counts as promised only when its ``promised_on`` is set and
later than its ``ordered_on``; a promise made on the order day (or
before it) is a default, not a promise, and a receipt without an
``ordered_on`` is neither a promise nor a default (its promise cannot
be shown to lie after the order).

  * ``receipts`` — the in-window receipts;
  * ``with_promise`` — those carrying a real promise;
  * ``default_promise`` — those whose ``promised_on`` is set but on
    or before ``ordered_on``;
  * ``on_time`` — the real promises received on or before
    ``promised_on``;
  * ``late`` — the real promises received after ``promised_on``;
  * ``on_time_rate`` — ``on_time / with_promise`` rounded to 3 places,
    or null when nothing in the window carried a real promise;
  * ``avg_days_late`` — the mean of ``received_on - promised_on`` in
    days over the late receipts, 1 place, or null;
  * ``max_days_late`` — the worst lateness in whole days, or null;
  * ``avg_lead_time_days`` — the mean of ``received_on - ordered_on``
    in days, 1 place, over the receipts that have an ``ordered_on``
    (0.0 when none do);
  * ``p90_lead_time_days`` — the nearest-rank 90th percentile of
    ``received_on - ordered_on`` (the ``ceil(0.9 * n)``-th smallest
    value), an integer (0 when no receipt has an ``ordered_on``);
  * ``last_received_on`` — the newest ``received_on``, ISO date.

Entries are ordered by ``on_time_rate`` ascending with nulls last,
then ``late`` descending, then ``supplier_code``.  The answer is
``{"as_of", "window_days", "suppliers"}`` with ``as_of`` the resolved
ISO date.  When the suppliers read model (read once through
``SupplierPostgresRepository.list_all``) carries at least one row,
every entry also gains ``vendor_code`` and ``supplier_name``, looked
up by ``supplier_code`` — both null for a supplier missing from the
read model, which is still listed, never dropped.

A ``window_days`` outside 1 to 1825, or an ``as_of`` after today,
raises ``ValueError``.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from app.repositories.lead_time_observations_postgres_repository import (
    LeadTimeObservationPostgresRepository,
)
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)

DEFAULT_WINDOW_DAYS = 365
MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 1825

_repo = LeadTimeObservationPostgresRepository()
_suppliers_repo = SupplierPostgresRepository()


def _as_date(value):
    """Coerce a row date column (date or datetime) to a date."""
    if isinstance(value, datetime):
        return value.date()
    return value


def _today() -> date:
    """The server's current date; tests substitute a fixed date here."""
    return date.today()


def _window_start(as_of: date, window_days: int) -> date:
    """First day of the window: ``as_of`` minus ``window_days - 1``.

    Clamped to ``date.min`` when the subtraction is not representable
    (an ``as_of`` near the first calendar day); the window simply
    starts on the first representable date.
    """
    try:
        return as_of - timedelta(days=window_days - 1)
    except OverflowError:
        return date.min


def _suppliers_by_code(suppliers: list) -> dict:
    """supplier_code -> (vendor_code, name) over the suppliers read.

    Non-dict rows and rows without a supplier_code are skipped; a
    duplicate supplier_code keeps the first row (the read is ordered
    by vendor_code, so this is a stable, deterministic choice).
    """
    by_code: dict = {}
    for row in suppliers:
        if not isinstance(row, dict):
            continue
        supplier_code = row.get("supplier_code")
        if supplier_code is None or supplier_code in by_code:
            continue
        by_code[supplier_code] = (row.get("vendor_code"), row.get("name"))
    return by_code


def _validate(as_of: date | None, window_days: int) -> date:
    """Validate the caller's inputs and resolve the as-of date."""
    if not isinstance(window_days, int) or isinstance(window_days, bool):
        raise ValueError("window_days must be an integer")
    if not MIN_WINDOW_DAYS <= window_days <= MAX_WINDOW_DAYS:
        raise ValueError(
            f"window_days must be between {MIN_WINDOW_DAYS} and {MAX_WINDOW_DAYS}"
        )
    if as_of is not None:
        as_of = _as_date(as_of)
        if not isinstance(as_of, date):
            raise ValueError("as_of must be a date")
        if as_of > _today():
            raise ValueError("as_of must not be after today")
    return as_of if as_of is not None else _today()


def _entry(supplier_code: str, receipts: list[dict]) -> dict:
    """The performance entry for one supplier's in-window receipts."""
    with_promise = 0
    default_promise = 0
    on_time = 0
    late_days: list[int] = []
    for row in receipts:
        promised_on = _as_date(row.get("promised_on"))
        if promised_on is None:
            continue
        ordered_on = _as_date(row.get("ordered_on"))
        if ordered_on is None:
            # No order date, so promised_on cannot be shown to lie
            # after it: the receipt is neither a real promise nor a
            # default.
            continue
        if promised_on <= ordered_on:
            default_promise += 1
            continue
        with_promise += 1
        days_late = (_as_date(row["received_on"]) - promised_on).days
        if days_late <= 0:
            on_time += 1
        else:
            late_days.append(days_late)

    lead_times = sorted(
        (_as_date(row["received_on"]) - _as_date(row["ordered_on"])).days
        for row in receipts
        if row.get("ordered_on") is not None
    )
    last_received_on = max(_as_date(row["received_on"]) for row in receipts)
    if lead_times:
        rank = max(1, math.ceil(0.9 * len(lead_times)))
        avg_lead_time_days = round(sum(lead_times) / len(lead_times), 1)
        p90_lead_time_days = int(lead_times[rank - 1])
    else:
        # No receipt carries an ordered_on: nothing to average, so the
        # lead-time fields fall back to 0 instead of crashing.
        avg_lead_time_days = 0.0
        p90_lead_time_days = 0

    on_time_rate = round(on_time / with_promise, 3) if with_promise else None
    if late_days:
        avg_days_late = round(sum(late_days) / len(late_days), 1)
        max_days_late = max(late_days)
    else:
        avg_days_late = None
        max_days_late = None

    return {
        "supplier_code": supplier_code,
        "receipts": len(receipts),
        "with_promise": with_promise,
        "default_promise": default_promise,
        "on_time": on_time,
        "late": len(late_days),
        "on_time_rate": on_time_rate,
        "avg_days_late": avg_days_late,
        "max_days_late": max_days_late,
        "avg_lead_time_days": avg_lead_time_days,
        "p90_lead_time_days": p90_lead_time_days,
        "last_received_on": last_received_on.isoformat(),
    }


def _sort_key(entry: dict):
    """on_time_rate ascending with nulls last, then late descending,
    then supplier_code."""
    rate = entry["on_time_rate"]
    return (
        rate is None,
        rate if rate is not None else 0.0,
        -entry["late"],
        entry["supplier_code"],
    )


def supplier_performance(
    as_of: date | None = None, window_days: int = DEFAULT_WINDOW_DAYS
) -> dict:
    """Per-supplier on-time and lead-time performance over the window."""
    resolved_as_of = _validate(as_of, window_days)
    window_start = _window_start(resolved_as_of, window_days)

    # The suppliers read model, once: the vendor code and display
    # name of every supplier that has an entry, looked up by
    # supplier_code.
    suppliers_by_code = _suppliers_by_code(_suppliers_repo.list_all())

    by_supplier: dict[str, list[dict]] = {}
    for row in _repo.list_rows(10**9, 0):
        received_on = _as_date(row.get("received_on"))
        supplier_code = row.get("supplier_code")
        if received_on is None or supplier_code is None:
            continue
        if window_start <= received_on <= resolved_as_of:
            by_supplier.setdefault(supplier_code, []).append(row)

    entries = [_entry(code, rows) for code, rows in by_supplier.items()]
    entries.sort(key=_sort_key)
    if suppliers_by_code:
        # The read model carries at least one supplier: every entry
        # gains the vendor code and display name, null when the
        # supplier is missing from it — never dropped, never a
        # failure.
        for entry in entries:
            vendor_code, supplier_name = suppliers_by_code.get(
                entry["supplier_code"], (None, None)
            )
            entry["vendor_code"] = vendor_code
            entry["supplier_name"] = supplier_name
    return {
        "as_of": resolved_as_of.isoformat(),
        "window_days": window_days,
        "suppliers": entries,
    }
