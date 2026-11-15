"""Issue #382 proof: the effective measures in the realised accuracy.

``realised_accuracy`` (issue #336) compares the completed buckets of
the stored forecast snapshots with what actually happened.  This issue
adds the effective measures [rule: OVR-4] beside the statistical
ones: every measures block (``overall``, each ``by_horizon`` entry
and each ``item_sites`` entry) gains ``effective_total`` (the sum of
the compared buckets' effective quantities), ``effective_wape`` and
``effective_bias`` (computed like ``wape`` and ``bias`` with the
effective quantity in place of the statistical one),
``overridden_buckets`` (how many compared buckets had at least one
overridden day) and ``value_added`` (``wape`` minus
``effective_wape``, null when either is null).  A stored row whose
``effective_quantity`` is null — a row written before migration
0015 — counts as effective equal to its statistical quantity and as
not overridden, as #381 reads it.  ``forecast_total``, ``wape`` and
``bias`` stay the statistical forecast's, as do the compared
buckets, the actuals, the filters, the orders and
``compared_buckets``.

The proof covers, in one test, against the test database (demand
events and snapshot rows inserted for the test — the snapshot is one
direct insert, one per item-site per bucket, for the date 2026-08-01,
origin ``live``; every statistical bucket is 30.0, method
``mean``):

* the item-sites, with the two completed buckets (horizons 1 and 2 —
  the third bucket has not completed by the shared default as-of
  date, 2026-09-30, the last demand day) each compared against
  their actuals:
  * ``near`` — the override moved the forecast nearer the actuals:
    statistical 30.0, effective 60.0 (30 overridden days), actual
    60.0 per bucket; ``effective_wape`` (0.0) below ``wape`` (0.5)
    and ``value_added`` positive (0.5);
  * ``far`` — the override moved the forecast further away:
    statistical 30.0, effective 10.0 (30 overridden days), actual
    60.0 per bucket; ``value_added`` negative (0.5 - 100/120);
  * ``plain`` — no override (effective 30.0, 0 overridden days),
    actual 30.0 per bucket: the effective measures equal the
    statistical ones, ``value_added`` 0 and ``overridden_buckets`` 0;
  * ``legacy`` — stored the old way: ``effective_quantity`` null
    beside a stored non-null ``override_days`` (7) on both compared
    buckets: read as effective equal to statistical and as NOT
    overridden, so the effective measures equal the statistical
    ones, ``value_added`` 0 and ``overridden_buckets`` 0;
  * ``dry`` — no actual demand at all (no demand events after the
    snapshot): ``effective_wape``, ``effective_bias`` and
    ``value_added`` null (and the statistical ``wape`` and ``bias``
    null), ``effective_total`` 80.0 and ``overridden_buckets`` 2;
* the ``overall`` and the two ``by_horizon`` entries carrying the
  new measures, hand-computed below;
* ``forecast_total``, ``wape`` and ``bias`` unchanged from what the
  statistical measures were before this issue, at every level;
* the ``origin`` filter (``live`` keeps everything here,
  ``backfill`` keeps nothing — the nothing-to-compare answer carries
  the new totals 0, the new ratios and ``value_added`` null and
  ``overridden_buckets`` 0) and the ``bucket_index`` filter
  (horizon 1 alone, and combined with ``origin``) carrying the new
  measures;
* the response through ``GET /api/forecast/accuracy`` carrying the
  five fields at every level (``overall``, every ``by_horizon``
  entry, every ``item_sites`` entry).
"""

from datetime import date, timedelta

import psycopg2
import psycopg2.extras
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

# The last demand day across all seeded events — the shared default
# as-of date (never later than today).
LAST = date(2026, 9, 30)
LAST_STR = LAST.isoformat()
# The snapshot date: 60 days before the last demand day.
SNAP = date(2026, 8, 1)
# The first seeded demand day, 400 days before the snapshot date.
HIST_START = SNAP - timedelta(days=400)

SITE = "SITE-382"
# The override moved the forecast nearer the actuals.
ITEM_NEAR = "ITEM-382-A"
# The override moved the forecast further away.
ITEM_FAR = "ITEM-382-B"
# No override at all.
ITEM_PLAIN = "ITEM-382-C"
# Stored the old way: both effective columns null (a stored,
# non-null override_days on the compared buckets must be ignored).
ITEM_LEGACY = "ITEM-382-D"
# No actual demand after the snapshot.
ITEM_DRY = "ITEM-382-E"

# Every statistical bucket of the snapshot (mean of constant history).
BUCKET = 30.0


def _seed_demand() -> None:
    """Demand events for the four demand-carrying item-sites.

    ``near`` and ``far``: 2.0 every day from HIST_START through LAST
    (60.0 per 30-day bucket after the snapshot).  ``plain`` and
    ``legacy``: 1.0 every day from HIST_START through LAST (30.0 per
    bucket).  ``dry`` has no demand events at all — its actuals are
    0.  The events through LAST make LAST the shared default as-of
    date.
    """
    events = []

    def add(item: str, day: date, quantity: float, tag: str) -> None:
        events.append(
            (
                f"id-de-382-{tag}",
                f"de-382-{tag}",
                item,
                SITE,
                quantity,
                day.isoformat(),
                "shipment",
                "test",
            )
        )

    for offset in range((LAST - HIST_START).days + 1):
        day = HIST_START + timedelta(days=offset)
        add(ITEM_NEAR, day, 2.0, f"near{offset}")
        add(ITEM_FAR, day, 2.0, f"far{offset}")
        add(ITEM_PLAIN, day, 1.0, f"plain{offset}")
        add(ITEM_LEGACY, day, 1.0, f"legacy{offset}")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                events,
            )
        conn.commit()
    finally:
        conn.close()


def _snapshot_rows() -> None:
    """The 60 snapshot rows: 5 item-sites x 12 buckets, origin live.

    Every statistical bucket is BUCKET (30.0), method ``mean``.  The
    effective columns per bucket:
    * near:   buckets 1-2 (60.0, 30 days overridden), rest (30.0, 0);
    * far:    buckets 1-2 (10.0, 30 days overridden), rest (30.0, 0);
    * plain:  every bucket (30.0, 0);
    * legacy: buckets 1-2 (null, 7 — the 7 must be ignored, the row
              reads as not overridden), rest (null, null);
    * dry:    buckets 1-2 (40.0, 30 days overridden), rest (30.0, 0).
    One transaction: the one-snapshot-per-date guard trigger allows
    a date's own batch insert (the rows share the transaction's xid).
    """
    effective = {
        ITEM_NEAR: {1: (60.0, 30), 2: (60.0, 30)},
        ITEM_FAR: {1: (10.0, 30), 2: (10.0, 30)},
        ITEM_PLAIN: {},
        ITEM_LEGACY: {1: (None, 7), 2: (None, 7)},
        ITEM_DRY: {1: (40.0, 30), 2: (40.0, 30)},
    }
    rows = []
    for item in (ITEM_NEAR, ITEM_FAR, ITEM_PLAIN, ITEM_LEGACY, ITEM_DRY):
        for index in range(1, 13):
            if item == ITEM_LEGACY:
                quantity, days = effective[item].get(index, (None, None))
            else:
                quantity, days = effective[item].get(index, (BUCKET, 0))
            rows.append(
                (
                    f"fs-382-{item}-{index}",
                    SNAP,
                    "live",
                    item,
                    SITE,
                    index,
                    SNAP + timedelta(days=30 * (index - 1) + 1),
                    SNAP + timedelta(days=30 * index),
                    BUCKET,
                    quantity,
                    days,
                    "mean",
                    "smooth",
                    13,
                )
            )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO forecast_snapshots "
                "(id, as_of, origin, item_code, site_code, bucket_index, "
                "bucket_start, bucket_end, quantity, effective_quantity, "
                "override_days, method, demand_class, history_buckets) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s)",
                rows,
            )
        conn.commit()
    finally:
        conn.close()


def _stored_rows() -> list[dict]:
    """Every stored snapshot row, deterministically ordered."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM forecast_snapshots WHERE as_of = %s "
                "ORDER BY item_code, site_code, bucket_index",
                (SNAP,),
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


# ----------------------------------------------------------------------
# The hand-computed measures.  Every compared bucket (horizons 1 and 2
# only — bucket 3 ends after LAST and is not compared):
#
#   near:   f 30 vs 60, eff 60  -> |f-a| 30, |eff-a| 0,  overridden
#   far:    f 30 vs 60, eff 10  -> |f-a| 30, |eff-a| 50, overridden
#   plain:  f 30 vs 30, eff 30  -> 0, 0,  not overridden
#   legacy: f 30 vs 30 (null effective reads as 30, NOT overridden,
#            whatever the stored override_days) -> 0, 0
#   dry:    f 30 vs 0,  eff 40  -> 30, 40, overridden, actuals 0
#
#   overall (10 buckets): forecast 300, actual 360,
#     wape (6x30)/360 = 0.5,          bias (300-360)/360,
#     effective 340 (120+20+60+60+80), eff_wape (100+80)/360 = 0.5,
#     eff_bias (340-360)/360, overridden 2+2+0+0+2 = 6,
#     value_added 0.5 - 0.5 = 0.0
#
#   each horizon (5 buckets): forecast 150, actual 180,
#     wape (3x30)/180 = 0.5,          bias (150-180)/180,
#     effective 170 (60+10+30+30+40), eff_wape (50+40)/180 = 0.5,
#     eff_bias (170-180)/180, overridden 3, value_added 0.0
#
#   horizon 1 alone (one bucket per item-site):
#     near   30 vs 60, eff 60 -> wape 30/60,  eff_wape 0
#     far    30 vs 60, eff 10 -> wape 30/60,  eff_wape 50/60
#     plain  30 vs 30, eff 30 -> 0
#     legacy 30 vs 30 (as statistical) -> 0
#     dry    30 vs 0,  eff 40 -> nulls, actual 0
# ----------------------------------------------------------------------

_NEAR_SITES = {
    "item_code": ITEM_NEAR,
    "site_code": SITE,
    "method": "mean",
    "compared_buckets": 2,
    "forecast_total": 2 * BUCKET,
    "actual_total": 4 * BUCKET,
    "wape": 60.0 / (4 * BUCKET),
    "bias": (2 * BUCKET - 4 * BUCKET) / (4 * BUCKET),
    "effective_total": 4 * BUCKET,
    "effective_wape": 0.0,
    "effective_bias": 0.0,
    "overridden_buckets": 2,
    "value_added": 60.0 / (4 * BUCKET),
}
_FAR_SITES = {
    "item_code": ITEM_FAR,
    "site_code": SITE,
    "method": "mean",
    "compared_buckets": 2,
    "forecast_total": 2 * BUCKET,
    "actual_total": 4 * BUCKET,
    "wape": 60.0 / (4 * BUCKET),
    "bias": (2 * BUCKET - 4 * BUCKET) / (4 * BUCKET),
    "effective_total": 2 * 10.0,
    "effective_wape": 100.0 / (4 * BUCKET),
    "effective_bias": (2 * 10.0 - 4 * BUCKET) / (4 * BUCKET),
    "overridden_buckets": 2,
    "value_added": 60.0 / (4 * BUCKET) - 100.0 / (4 * BUCKET),
}
_PLAIN_SITES = {
    "item_code": ITEM_PLAIN,
    "site_code": SITE,
    "method": "mean",
    "compared_buckets": 2,
    "forecast_total": 2 * BUCKET,
    "actual_total": 2 * BUCKET,
    "wape": 0.0,
    "bias": 0.0,
    "effective_total": 2 * BUCKET,
    "effective_wape": 0.0,
    "effective_bias": 0.0,
    "overridden_buckets": 0,
    "value_added": 0.0,
}
_LEGACY_SITES = {
    "item_code": ITEM_LEGACY,
    "site_code": SITE,
    "method": "mean",
    "compared_buckets": 2,
    "forecast_total": 2 * BUCKET,
    "actual_total": 2 * BUCKET,
    "wape": 0.0,
    "bias": 0.0,
    # The null effective columns read as statistical, not overridden.
    "effective_total": 2 * BUCKET,
    "effective_wape": 0.0,
    "effective_bias": 0.0,
    "overridden_buckets": 0,
    "value_added": 0.0,
}
_DRY_SITES = {
    "item_code": ITEM_DRY,
    "site_code": SITE,
    "method": "mean",
    "compared_buckets": 2,
    "forecast_total": 2 * BUCKET,
    "actual_total": 0.0,
    "wape": None,
    "bias": None,
    "effective_total": 2 * 40.0,
    "effective_wape": None,
    "effective_bias": None,
    "overridden_buckets": 2,
    "value_added": None,
}
_OVERALL = {
    "forecast_total": 10 * BUCKET,
    "actual_total": 12 * BUCKET,
    "wape": 6 * 30.0 / (12 * BUCKET),
    "bias": (10 * BUCKET - 12 * BUCKET) / (12 * BUCKET),
    "effective_total": 4 * BUCKET + 2 * 10.0 + 2 * BUCKET + 2 * BUCKET + 2 * 40.0,
    "effective_wape": (50.0 + 50.0 + 40.0 + 40.0) / (12 * BUCKET),
    "effective_bias": (
        4 * BUCKET + 2 * 10.0 + 2 * BUCKET + 2 * BUCKET + 2 * 40.0 - 12 * BUCKET
    )
    / (12 * BUCKET),
    "overridden_buckets": 6,
    "value_added": 6 * 30.0 / (12 * BUCKET)
    - (50.0 + 50.0 + 40.0 + 40.0) / (12 * BUCKET),
}
_HORIZON = {
    "compared_buckets": 5,
    "forecast_total": 5 * BUCKET,
    "actual_total": 6 * BUCKET,
    "wape": 3 * 30.0 / (6 * BUCKET),
    "bias": (5 * BUCKET - 6 * BUCKET) / (6 * BUCKET),
    "effective_total": 2 * BUCKET + 10.0 + BUCKET + BUCKET + (BUCKET + 10.0),
    "effective_wape": (50.0 + 40.0) / (6 * BUCKET),
    "effective_bias": (
        2 * BUCKET + 10.0 + BUCKET + BUCKET + (BUCKET + 10.0) - 6 * BUCKET
    )
    / (6 * BUCKET),
    "overridden_buckets": 3,
    "value_added": 3 * 30.0 / (6 * BUCKET) - (50.0 + 40.0) / (6 * BUCKET),
}
_BASELINE = {
    "as_of": LAST_STR,
    "compared_buckets": 10,
    "overall": _OVERALL,
    "by_horizon": [
        {"bucket_index": 1, **_HORIZON},
        {"bucket_index": 2, **_HORIZON},
    ],
    # Ordered by actual_total descending, then item_code, site_code.
    "item_sites": [
        _NEAR_SITES,
        _FAR_SITES,
        _PLAIN_SITES,
        _LEGACY_SITES,
        _DRY_SITES,
    ],
}
# Horizon 1 alone: one compared bucket per item-site.
_ONE_HORIZON_OVERALL = {
    "forecast_total": 5 * BUCKET,
    "actual_total": 6 * BUCKET,
    "wape": 3 * 30.0 / (6 * BUCKET),
    "bias": (5 * BUCKET - 6 * BUCKET) / (6 * BUCKET),
    "effective_total": 2 * BUCKET + 10.0 + BUCKET + BUCKET + (BUCKET + 10.0),
    "effective_wape": (50.0 + 40.0) / (6 * BUCKET),
    "effective_bias": (
        2 * BUCKET + 10.0 + BUCKET + BUCKET + (BUCKET + 10.0) - 6 * BUCKET
    )
    / (6 * BUCKET),
    "overridden_buckets": 3,
    "value_added": 3 * 30.0 / (6 * BUCKET) - (50.0 + 40.0) / (6 * BUCKET),
}
_ONE_HORIZON_ITEM_SITES = [
    {
        "item_code": ITEM_NEAR,
        "site_code": SITE,
        "method": "mean",
        "compared_buckets": 1,
        "forecast_total": BUCKET,
        "actual_total": 2 * BUCKET,
        "wape": 30.0 / (2 * BUCKET),
        "bias": (BUCKET - 2 * BUCKET) / (2 * BUCKET),
        "effective_total": 2 * BUCKET,
        "effective_wape": 0.0,
        "effective_bias": 0.0,
        "overridden_buckets": 1,
        "value_added": 30.0 / (2 * BUCKET),
    },
    {
        "item_code": ITEM_FAR,
        "site_code": SITE,
        "method": "mean",
        "compared_buckets": 1,
        "forecast_total": BUCKET,
        "actual_total": 2 * BUCKET,
        "wape": 30.0 / (2 * BUCKET),
        "bias": (BUCKET - 2 * BUCKET) / (2 * BUCKET),
        "effective_total": 10.0,
        "effective_wape": 50.0 / (2 * BUCKET),
        "effective_bias": (10.0 - 2 * BUCKET) / (2 * BUCKET),
        "overridden_buckets": 1,
        "value_added": 30.0 / (2 * BUCKET) - 50.0 / (2 * BUCKET),
    },
    {
        "item_code": ITEM_PLAIN,
        "site_code": SITE,
        "method": "mean",
        "compared_buckets": 1,
        "forecast_total": BUCKET,
        "actual_total": BUCKET,
        "wape": 0.0,
        "bias": 0.0,
        "effective_total": BUCKET,
        "effective_wape": 0.0,
        "effective_bias": 0.0,
        "overridden_buckets": 0,
        "value_added": 0.0,
    },
    {
        "item_code": ITEM_LEGACY,
        "site_code": SITE,
        "method": "mean",
        "compared_buckets": 1,
        "forecast_total": BUCKET,
        "actual_total": BUCKET,
        "wape": 0.0,
        "bias": 0.0,
        "effective_total": BUCKET,
        "effective_wape": 0.0,
        "effective_bias": 0.0,
        "overridden_buckets": 0,
        "value_added": 0.0,
    },
    {
        "item_code": ITEM_DRY,
        "site_code": SITE,
        "method": "mean",
        "compared_buckets": 1,
        "forecast_total": BUCKET,
        "actual_total": 0.0,
        "wape": None,
        "bias": None,
        "effective_total": BUCKET + 10.0,
        "effective_wape": None,
        "effective_bias": None,
        "overridden_buckets": 1,
        "value_added": None,
    },
]


def _empty_result(as_of: str) -> dict:
    """The nothing-to-compare result with the new fields.

    The new totals 0, the new ratios and ``value_added`` null,
    ``overridden_buckets`` 0.
    """
    return {
        "as_of": as_of,
        "compared_buckets": 0,
        "overall": {
            "forecast_total": 0,
            "actual_total": 0,
            "wape": None,
            "bias": None,
            "effective_total": 0,
            "effective_wape": None,
            "effective_bias": None,
            "overridden_buckets": 0,
            "value_added": None,
        },
        "by_horizon": [],
        "item_sites": [],
    }


# The pre-#382 measures: the statistical forecast's four fields.
_STATISTICAL_KEYS = ("forecast_total", "actual_total", "wape", "bias")


def _statistical(block: dict) -> dict:
    """Only the statistical fields of a measures block."""
    return {key: block[key] for key in _STATISTICAL_KEYS}


def test_issue382_freeform(client, user_headers):
    _seed_demand()
    _snapshot_rows()
    # The whole proof runs as a NON-ADMIN (role "user"); ``client`` is
    # requested for its per-test table truncation only.
    viewer = TestClient(app, headers=user_headers)

    # ------------------------------------------------------------------
    # The stored rows: 5 item-sites x 12 buckets, the effective
    # columns as designed — including the legacy row whose
    # effective_quantity is null beside a stored override_days of 7.
    # ------------------------------------------------------------------
    rows = _stored_rows()
    assert len(rows) == 60
    assert all(row["origin"] == "live" for row in rows)
    assert all(row["quantity"] == BUCKET for row in rows)
    assert all(row["method"] == "mean" for row in rows)
    by_key = {(r["item_code"], r["bucket_index"]): r for r in rows}
    assert by_key[(ITEM_NEAR, 1)]["effective_quantity"] == 60.0
    assert by_key[(ITEM_NEAR, 1)]["override_days"] == 30
    assert by_key[(ITEM_NEAR, 3)]["effective_quantity"] == BUCKET
    assert by_key[(ITEM_NEAR, 3)]["override_days"] == 0
    assert by_key[(ITEM_FAR, 2)]["effective_quantity"] == 10.0
    assert by_key[(ITEM_FAR, 2)]["override_days"] == 30
    assert by_key[(ITEM_PLAIN, 1)]["effective_quantity"] == BUCKET
    assert by_key[(ITEM_PLAIN, 1)]["override_days"] == 0
    # Stored the old way: both new columns null on the tail buckets,
    # and a null effective_quantity beside a stored override_days of
    # 7 on the compared buckets.
    assert by_key[(ITEM_LEGACY, 5)]["effective_quantity"] is None
    assert by_key[(ITEM_LEGACY, 5)]["override_days"] is None
    assert by_key[(ITEM_LEGACY, 1)]["effective_quantity"] is None
    assert by_key[(ITEM_LEGACY, 1)]["override_days"] == 7
    assert by_key[(ITEM_DRY, 1)]["effective_quantity"] == 40.0
    assert by_key[(ITEM_DRY, 1)]["override_days"] == 30

    # ------------------------------------------------------------------
    # No as_of — the shared default as-of date (the last demand day;
    # demand after it is unknown, not zero) — exactly the two
    # completed buckets per item-site compared (10 compared), and the
    # hand-computed measures, statistical and effective.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == _BASELINE

    # The five new fields at every level: overall, every by_horizon
    # entry, every item_sites entry.
    for block in (body["overall"], *body["by_horizon"], *body["item_sites"]):
        for field in (
            "effective_total",
            "effective_wape",
            "effective_bias",
            "overridden_buckets",
            "value_added",
        ):
            assert field in block, (field, block)

    # The by_horizon entries carry the new measures (hand-computed
    # above): effective 170 (60+10+30+30+40), effective_wape
    # (50+40)/180 = 0.5, overridden 3, value_added 0.0.
    for entry in body["by_horizon"]:
        assert entry["effective_total"] == 2 * BUCKET + 10.0 + BUCKET + BUCKET + (
            BUCKET + 10.0
        )
        assert entry["effective_wape"] == (50.0 + 40.0) / (6 * BUCKET)
        assert entry["overridden_buckets"] == 3
        assert entry["value_added"] == 0.0

    # near: the override moved the forecast nearer the actuals —
    # effective_wape below wape, value_added positive, both
    # hand-computed.
    near = body["item_sites"][0]
    assert near["effective_wape"] < near["wape"]
    assert near["value_added"] > 0
    assert near["effective_wape"] == 0.0
    assert near["value_added"] == 60.0 / (4 * BUCKET)

    # far: the override moved the forecast further away —
    # value_added negative.
    far = body["item_sites"][1]
    assert far["value_added"] < 0
    assert far["value_added"] == 60.0 / (4 * BUCKET) - 100.0 / (4 * BUCKET)

    # plain: no override — the effective measures equal the
    # statistical ones, value_added 0, overridden_buckets 0.
    plain = body["item_sites"][2]
    assert _statistical(plain) == {
        "forecast_total": plain["effective_total"],
        "actual_total": plain["actual_total"],
        "wape": plain["effective_wape"],
        "bias": plain["effective_bias"],
    }
    assert plain["value_added"] == 0.0
    assert plain["overridden_buckets"] == 0

    # legacy: the row with a null effective_quantity (beside a stored
    # override_days of 7) reads as effective equal to statistical and
    # as NOT overridden.
    legacy = body["item_sites"][3]
    assert _statistical(legacy) == {
        "forecast_total": legacy["effective_total"],
        "actual_total": legacy["actual_total"],
        "wape": legacy["effective_wape"],
        "bias": legacy["effective_bias"],
    }
    assert legacy["overridden_buckets"] == 0
    assert legacy["value_added"] == 0.0

    # dry: zero actual demand — effective_wape, effective_bias and
    # value_added null (and the statistical wape and bias null).
    dry = body["item_sites"][4]
    assert dry["effective_wape"] is None
    assert dry["effective_bias"] is None
    assert dry["value_added"] is None
    assert dry["wape"] is None
    assert dry["bias"] is None

    # The statistical measures are unchanged from what they were
    # before this issue, at every level: the statistical forecast's
    # rule [rule: OVR-4].
    assert _statistical(body["overall"]) == {
        "forecast_total": 10 * BUCKET,
        "actual_total": 12 * BUCKET,
        "wape": 6 * 30.0 / (12 * BUCKET),
        "bias": (10 * BUCKET - 12 * BUCKET) / (12 * BUCKET),
    }
    for entry in body["by_horizon"]:
        assert _statistical(entry) == _statistical(_HORIZON)
    assert [_statistical(entry) for entry in body["item_sites"]] == [
        _statistical(expected)
        for expected in (
            _NEAR_SITES,
            _FAR_SITES,
            _PLAIN_SITES,
            _LEGACY_SITES,
            _DRY_SITES,
        )
    ]

    # ------------------------------------------------------------------
    # The origin filter — live keeps everything here (the only stored
    # snapshot is live); backfill keeps nothing, and the
    # nothing-to-compare answer carries the new totals 0, the new
    # ratios and value_added null and overridden_buckets 0.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy", params={"origin": "live"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == _BASELINE

    resp = viewer.get("/api/forecast/accuracy", params={"origin": "backfill"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == _empty_result(LAST_STR)

    # ------------------------------------------------------------------
    # The bucket_index filter — horizon 1 alone, and combined with the
    # origin filter — carrying the new measures.
    # ------------------------------------------------------------------
    for params in ({"bucket_index": 1}, {"bucket_index": 1, "origin": "live"}):
        resp = viewer.get("/api/forecast/accuracy", params=params)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["as_of"] == LAST_STR
        assert body["compared_buckets"] == 5
        assert body["overall"] == _ONE_HORIZON_OVERALL
        assert body["by_horizon"] == [{"bucket_index": 1, **_HORIZON}]
        assert body["item_sites"] == _ONE_HORIZON_ITEM_SITES
