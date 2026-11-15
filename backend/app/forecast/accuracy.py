"""Realised accuracy of stored forecast snapshots (issue #336).

``realised_accuracy(as_of=None, origin=None, bucket_index=None)``
compares the buckets of the stored forecast snapshots with what
actually happened.  A snapshot bucket is compared only when it has
*completed* — that is, when its ``bucket_end`` is on or before the
as-of date: demand after the latest demand date is unknown, not zero,
so with no ``as_of`` the shared default as-of date
(``app.stats.demand.default_as_of``) is used, and a bucket that has
only partly elapsed is left out.

For a compared bucket the actual demand is the sum of
``demand_events.quantity`` for that item-site with ``occurred_on``
from ``bucket_start`` to ``bucket_end`` inclusive; no demand in the
range is an actual of 0.

The statistical measures for any set of compared buckets:
``forecast_total`` (the sum of the snapshot quantities),
``actual_total`` (the sum of the actuals), ``wape`` (the sum of the
absolute forecast-minus-actual differences over ``actual_total``,
null when ``actual_total`` is 0) and ``bias`` (``forecast_total``
minus ``actual_total``, over ``actual_total``, null likewise).
The effective measures [rule: OVR-4] report the same with each
bucket's effective forecast — the stored ``effective_quantity``, or
its ``quantity`` when that is null (a row written before migration
0015, which then also counts as not overridden — as
``app.forecast.snapshots`` reads it) — in place of the statistical
one: ``effective_total`` (the sum of the compared buckets'
effective quantities, the signal quantity included),
``effective_wape`` and ``effective_bias`` (computed like ``wape``
and ``bias``) and ``overridden_buckets`` (how many compared buckets
had at least one overridden day).  A bucket's forecast before the
signals is its effective quantity minus its stored
``signal_quantity`` (a null signal reads as 0), and
``value_added`` is ``wape`` minus that before-signals error over
``actual_total`` (null when ``actual_total`` is 0) — the
overrides' effect, measured alone: positive when the overrides moved
the forecast nearer the actuals, the signals' own effect reported by
``signal_accuracy``.  With no signal in the compared buckets every
measure is exactly what it is today: ``value_added`` is ``wape``
minus ``effective_wape``.

``signal_accuracy(as_of=None, origin=None)`` is the realised value
of the stored demand signals: the same completed-bucket comparison,
restricted to the buckets with a stored ``signal_quantity`` above 0
(a null or 0 signal is left out), with ``compared_buckets``,
``actual_total``, ``before_total`` (the sum of each bucket's
effective quantity minus its signal quantity), ``after_total`` (the
sum of its effective quantities), ``signal_total`` (the sum of its
signal quantities), ``before_wape`` and ``after_wape`` (each the
sum of the absolute differences from the actuals, over
``actual_total``, of the before-signals and the effective
forecast, null when ``actual_total`` is 0) and ``value_added``
(``before_wape`` minus ``after_wape`` — positive when the signals
moved the forecast nearer the actuals).  Its result carries
``as_of``, those measures and ``item_sites`` (one entry per
item-site with ``item_code``, ``site_code`` and the same measures,
ordered by ``signal_total`` descending, then ``item_code`` and
``site_code``); with nothing compared, ``compared_buckets`` and the
totals are 0, the ratios and ``value_added`` null, and
``item_sites`` empty.

``origin`` (``live`` or ``backfill``) keeps only snapshots of that
origin, and ``bucket_index`` (1 to 12) keeps only that horizon; with
neither, everything compared is included.

The result of ``realised_accuracy`` is a dict with ``as_of``,
``compared_buckets``,
``overall`` (the measures over everything compared), ``by_horizon``
(one entry per ``bucket_index`` present, in order, each with its
``bucket_index``, ``compared_buckets`` and the measures) and
``item_sites`` (one entry per item-site, each with ``item_code``,
``site_code``, ``method`` from its most recent compared snapshot,
``compared_buckets`` and the measures, ordered by ``actual_total``
descending, then ``item_code`` and ``site_code``).  With nothing to
compare, ``compared_buckets`` is 0, the totals in ``overall`` are 0,
the ratios and ``value_added`` null, ``overridden_buckets`` 0, and
both lists are empty.
"""

from __future__ import annotations

from datetime import date

from app.db.connection import get_cursor
from app.stats.demand import default_as_of

_COMPARE_SQL = (
    "SELECT s.as_of, s.item_code, s.site_code, s.bucket_index, s.method, "
    "s.quantity AS forecast_quantity, "
    "COALESCE(s.effective_quantity, s.quantity) AS effective_quantity, "
    "COALESCE(s.signal_quantity, 0) AS signal_quantity, "
    "CASE WHEN s.effective_quantity IS NULL THEN 0 "
    "ELSE COALESCE(s.override_days, 0) END AS override_days, "
    "COALESCE(d.actual_quantity, 0) AS actual_quantity "
    "FROM forecast_snapshots s "
    "LEFT JOIN LATERAL ("
    "  SELECT SUM(e.quantity) AS actual_quantity "
    "  FROM demand_events e "
    "  WHERE e.item_code = s.item_code "
    "    AND e.site_code = s.site_code "
    "    AND e.occurred_on >= s.bucket_start "
    "    AND e.occurred_on <= s.bucket_end"
    ") d ON TRUE "
    "WHERE s.bucket_end <= %s"
)

_SIGNAL_COMPARE_SQL = (
    "SELECT s.item_code, s.site_code, "
    "COALESCE(s.effective_quantity, s.quantity) AS effective_quantity, "
    "COALESCE(s.signal_quantity, 0) AS signal_quantity, "
    "COALESCE(d.actual_quantity, 0) AS actual_quantity "
    "FROM forecast_snapshots s "
    "LEFT JOIN LATERAL ("
    "  SELECT SUM(e.quantity) AS actual_quantity "
    "  FROM demand_events e "
    "  WHERE e.item_code = s.item_code "
    "    AND e.site_code = s.site_code "
    "    AND e.occurred_on >= s.bucket_start "
    "    AND e.occurred_on <= s.bucket_end"
    ") d ON TRUE "
    "WHERE s.bucket_end <= %s "
    "AND COALESCE(s.signal_quantity, 0) > 0"
)


def _measures(
    forecast_total: float,
    actual_total: float,
    abs_error: float,
    effective_total: float,
    effective_abs_error: float,
    overridden_buckets: int,
    before_signal_total: float,
    before_signal_abs_error: float,
) -> dict:
    """The measures over one set of compared buckets.

    The statistical measures stay the statistical forecast's
    [rule: OVR-4] and the effective ones are the effective
    forecast's, the signal quantity included; ``value_added`` is
    ``wape`` minus the error of the effective forecast before the
    signals — each bucket's effective quantity minus its signal
    quantity, a null signal read as 0 — over the actuals (null when
    ``actual_total`` is 0), positive when the overrides, measured
    alone, moved the forecast nearer the actuals.
    """
    if actual_total:
        wape = abs_error / actual_total
        effective_wape = effective_abs_error / actual_total
        before_signal_wape = before_signal_abs_error / actual_total
        return {
            "forecast_total": float(forecast_total),
            "actual_total": float(actual_total),
            "wape": wape,
            "bias": (forecast_total - actual_total) / actual_total,
            "effective_total": float(effective_total),
            "effective_wape": effective_wape,
            "effective_bias": (effective_total - actual_total) / actual_total,
            "overridden_buckets": overridden_buckets,
            "value_added": wape - before_signal_wape,
        }
    return {
        "forecast_total": float(forecast_total),
        "actual_total": float(actual_total),
        "wape": None,
        "bias": None,
        "effective_total": float(effective_total),
        "effective_wape": None,
        "effective_bias": None,
        "overridden_buckets": overridden_buckets,
        "value_added": None,
    }


def realised_accuracy(
    as_of: date | None = None,
    origin: str | None = None,
    bucket_index: int | None = None,
) -> dict:
    """Compare the completed stored snapshot buckets with actual demand.

    A snapshot bucket is compared only when it has completed — its
    ``bucket_end`` is on or before the as-of date; a partly elapsed
    bucket is left out, and with no ``as_of`` the shared default
    as-of date is used (demand after the latest demand date is
    unknown, not zero).  For a compared bucket the actual is the sum
    of ``demand_events.quantity`` for that item-site with
    ``occurred_on`` from ``bucket_start`` to ``bucket_end`` inclusive
    (0 when there is no demand in the range).  ``origin`` keeps only
    snapshots of that origin and ``bucket_index`` only that horizon.
    """
    with get_cursor() as cur:
        if as_of is None:
            as_of = default_as_of(cur)
        sql = _COMPARE_SQL
        params: list = [as_of]
        if origin is not None:
            sql += " AND s.origin = %s"
            params.append(origin)
        if bucket_index is not None:
            sql += " AND s.bucket_index = %s"
            params.append(bucket_index)
        cur.execute(sql, params)
        rows = cur.fetchall()

    _ZERO = [0.0, 0.0, 0.0, 0, 0.0, 0.0, 0, 0.0, 0.0]
    totals = list(_ZERO)
    horizon_totals: dict[int, list] = {}
    pair_totals: dict[tuple[str, str], list] = {}
    pair_method: dict[tuple[str, str], tuple[date, str]] = {}
    for row in rows:
        forecast = float(row["forecast_quantity"])
        effective = float(row["effective_quantity"])
        signal = float(row["signal_quantity"])
        actual = float(row["actual_quantity"])
        before_signal = effective - signal
        absolute = abs(forecast - actual)
        effective_absolute = abs(effective - actual)
        before_signal_absolute = abs(before_signal - actual)
        overridden = 1 if int(row["override_days"]) > 0 else 0
        pair_key = (row["item_code"], row["site_code"])
        for accumulator in (
            totals,
            horizon_totals.setdefault(row["bucket_index"], list(_ZERO)),
            pair_totals.setdefault(pair_key, list(_ZERO)),
        ):
            accumulator[0] += forecast
            accumulator[1] += actual
            accumulator[2] += absolute
            accumulator[3] += 1
            accumulator[4] += effective
            accumulator[5] += effective_absolute
            accumulator[6] += overridden
            accumulator[7] += before_signal
            accumulator[8] += before_signal_absolute
        previous = pair_method.get(pair_key)
        if previous is None or row["as_of"] > previous[0]:
            pair_method[pair_key] = (row["as_of"], row["method"])

    by_horizon = [
        {
            "bucket_index": index,
            "compared_buckets": accumulator[3],
            **_measures(
                accumulator[0],
                accumulator[1],
                accumulator[2],
                accumulator[4],
                accumulator[5],
                accumulator[6],
                accumulator[7],
                accumulator[8],
            ),
        }
        for index, accumulator in sorted(horizon_totals.items())
    ]
    item_sites = [
        {
            "item_code": pair_key[0],
            "site_code": pair_key[1],
            "method": pair_method[pair_key][1],
            "compared_buckets": accumulator[3],
            **_measures(
                accumulator[0],
                accumulator[1],
                accumulator[2],
                accumulator[4],
                accumulator[5],
                accumulator[6],
                accumulator[7],
                accumulator[8],
            ),
        }
        for pair_key, accumulator in pair_totals.items()
    ]
    item_sites.sort(
        key=lambda entry: (
            -entry["actual_total"],
            entry["item_code"],
            entry["site_code"],
        )
    )
    return {
        "as_of": as_of,
        "compared_buckets": len(rows),
        "overall": _measures(
            totals[0],
            totals[1],
            totals[2],
            totals[4],
            totals[5],
            totals[6],
            totals[7],
            totals[8],
        ),
        "by_horizon": by_horizon,
        "item_sites": item_sites,
    }


def _signal_measures(
    actual_total: float,
    before_total: float,
    after_total: float,
    signal_total: float,
    before_abs_error: float,
    after_abs_error: float,
) -> dict:
    """The signal measures over one set of compared buckets.

    ``before_wape`` and ``after_wape`` are the sums of the absolute
    differences from the actuals, over ``actual_total``, of the
    effective forecast before the signals and after them (null when
    ``actual_total`` is 0), and ``value_added`` is ``before_wape``
    minus ``after_wape`` — positive when the signals moved the
    forecast nearer the actuals.
    """
    if actual_total:
        before_wape = before_abs_error / actual_total
        after_wape = after_abs_error / actual_total
        return {
            "actual_total": float(actual_total),
            "before_total": float(before_total),
            "after_total": float(after_total),
            "signal_total": float(signal_total),
            "before_wape": before_wape,
            "after_wape": after_wape,
            "value_added": before_wape - after_wape,
        }
    return {
        "actual_total": float(actual_total),
        "before_total": float(before_total),
        "after_total": float(after_total),
        "signal_total": float(signal_total),
        "before_wape": None,
        "after_wape": None,
        "value_added": None,
    }


def signal_accuracy(
    as_of: date | None = None,
    origin: str | None = None,
) -> dict:
    """The realised value of the stored demand signals (issue #980).

    Only completed snapshot buckets — their ``bucket_end`` on or
    before the as-of date, the shared default as-of date when
    absent — with a stored ``signal_quantity`` above 0 are compared
    with the actual demand of their range, the same way
    ``realised_accuracy`` compares; a bucket whose signal is null or
    0 is left out.  ``origin`` (``live`` or ``backfill``) keeps only
    snapshots of that origin.  The result carries ``as_of``,
    ``compared_buckets``, the measures — ``actual_total``,
    ``before_total`` (each bucket's effective quantity minus its
    signal quantity), ``after_total`` (its effective quantity),
    ``signal_total`` (its signal quantity), ``before_wape``,
    ``after_wape`` and ``value_added`` — and ``item_sites``, one
    entry per item-site with ``item_code``, ``site_code`` and the
    same measures, ordered by ``signal_total`` descending, then
    ``item_code`` and ``site_code``.  With nothing compared,
    ``compared_buckets`` and the totals are 0, the ratios and
    ``value_added`` null, and ``item_sites`` empty.
    """
    with get_cursor() as cur:
        if as_of is None:
            as_of = default_as_of(cur)
        sql = _SIGNAL_COMPARE_SQL
        params: list = [as_of]
        if origin is not None:
            sql += " AND s.origin = %s"
            params.append(origin)
        cur.execute(sql, params)
        rows = cur.fetchall()

    _ZERO = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0]
    totals = list(_ZERO)
    pair_totals: dict[tuple[str, str], list] = {}
    for row in rows:
        effective = float(row["effective_quantity"])
        signal = float(row["signal_quantity"])
        actual = float(row["actual_quantity"])
        before = effective - signal
        pair_key = (row["item_code"], row["site_code"])
        for accumulator in (
            totals,
            pair_totals.setdefault(pair_key, list(_ZERO)),
        ):
            accumulator[0] += actual
            accumulator[1] += before
            accumulator[2] += effective
            accumulator[3] += signal
            accumulator[4] += abs(before - actual)
            accumulator[5] += abs(effective - actual)
            accumulator[6] += 1
    item_sites = [
        {
            "item_code": pair_key[0],
            "site_code": pair_key[1],
            "compared_buckets": accumulator[6],
            **_signal_measures(*accumulator[:6]),
        }
        for pair_key, accumulator in pair_totals.items()
    ]
    item_sites.sort(
        key=lambda entry: (
            -entry["signal_total"],
            entry["item_code"],
            entry["site_code"],
        )
    )
    return {
        "as_of": as_of,
        "compared_buckets": len(rows),
        **_signal_measures(*totals[:6]),
        "item_sites": item_sites,
    }
