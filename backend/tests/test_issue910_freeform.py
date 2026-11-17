"""Issue #910 proof: one recommendations computation per exceptions request.

AC-1 — ``app.recommend.reorder`` gains the context manager
``recommendations_memo``, built on a ``contextvars.ContextVar``.
Inside it, ``recommendations_for_all`` computes its rows once per
distinct resolved as-of and answers every later call with the same
as-of in the same context with a deep copy of those rows — a caller
that changes its rows therefore never changes another caller's —
while calls with different as-ofs never share a computation.  The
memo is cleared when the context manager exits, including on an
exception; nested use reuses the outer memo.  Outside the context
manager, ``recommendations_for_all`` behaves exactly as before,
computing on every call.  ``list_exceptions`` and
``capture_exception_first_seen`` gather their sources (through the
shared ``_collect``) inside ``recommendations_memo``; every other
route computes as before, and two concurrent requests never share a
memo because each request runs in its own context.

AC-2 — this proof seeds demand, lead times, item-sites and on-hand
the way ``tests/test_issue645_freeform.py`` does (fixed dates, raw
INSERTs; "today" is pinned the way that test pins its ranking
inputs — on the data date, so the DDMRP plan's resolved as-of, the
stockout ranking's own default and the expedite list's own default
all resolve the same date).  It checks: once inside (three calls of
``recommendations_for_all`` in one memo run the wrapped internal
builder once, all three answers equal), copies (changing a row of
the first answer leaves the second answer, and the memo's own rows,
unchanged), per as-of (two distinct as-of dates in one memo compute
twice), outside (two calls outside the memo compute twice, as
today), cleared (after the memo exits, including after an exception
raised inside it, the next call computes again), and the inbox: with
the real (unfaked) DDMRP, stockout and expedite sources over the
seeded data, one ``GET /api/exceptions`` runs the recommendations
computation exactly once, its JSON equals the JSON of the same
request computed with the memo disabled, and its expedite rows equal
those built from ``expedite_list()`` called with no as-of — their
``days_late`` reflect today, not the data date.  It also checks the
review defect: a finite ``effective_bias`` whose whole percent
overflows the float percent (``abs(bias) * 100`` past the largest
finite float) still answers its exact whole percent in a 200 —
never a failed forecast source, never a 500.

AC-3 — for the one exceptions request the DDMRP plan, the stockout
ranking and the expedite list all pass the same as-of value to
``recommendations_for_all``: the memo-disabled request computes
exactly three times, in source order, all at the same as-of, and the
memoized request computes exactly once.
"""

import copy
import json
from contextlib import contextmanager
from datetime import date, timedelta

from app.api import exceptions as exceptions_api
from app.db.connection import get_connection
from app.recommend import reorder
from app.recommend.expedite import expedite_list
from app.recommend.reorder import recommendations_for_all, recommendations_memo
from app.recommend.stockout import stockout_ranked

# The pinned "today": the data date, so the DDMRP plan's resolved
# as-of, the stockout ranking's own default (
# ``app.stats.demand.default_as_of``) and the expedite list's own
# default all resolve the same date.
TODAY = date(2026, 10, 2)
TODAY_STR = TODAY.isoformat()
FIRST_DAY = TODAY - timedelta(days=359)
S1 = "S1"
I1, I2 = "I910-1", "I910-2"
SYNC_AS_OF = f"{TODAY_STR}T00:00:00+00:00"

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
LEAD_SQL = (
    "INSERT INTO lead_time_observations (id, business_key, supplier_code, "
    "item_code, ordered_on, received_on, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
ITEM_SITE_SQL = (
    "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
    "reorder_point, safety_stock, max_qty, order_qty, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
ON_HAND_SQL = (
    "INSERT INTO on_hand (id, item_code, site_code, quantity_on_hand, "
    "as_of, source_system) VALUES (%s, %s, %s, %s, %s, %s)"
)
OPEN_LINE_SQL = (
    "INSERT INTO open_purchase_lines (id, business_key, item_code, "
    "site_code, supplier_code, order_type, open_units, promised_on, "
    "ordered_on, as_of, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
BUFFER_SQL = (
    "INSERT INTO ddmrp_buffers (item_code, site_code, designated_by, "
    "designated_by_name, note) VALUES (%s, %s, %s, %s, %s)"
)


def _seed(cur) -> None:
    """The fixed-date rows: demand, lead times, item-sites, position.

    I1: 10.0 a day for 360 days (d 10, sd_d 0), LT 10, unit cost 2.0,
    30 on hand, no open lines — a DDMRP buffer critical on hand and
    the stockout-likely row (its position 30 below its 100 lead-time
    demand, no variability).
    I2: 2.0 a day for 360 days (d 2, sd_d 0), LT 5, unit cost 5.0,
    2 on hand, one open line of 12 units promised 5 days before
    today and ordered 20 days before today — a DDMRP buffer critical
    on hand (its open supply lifts it out of stockout risk) and the
    one expedite line, 5 days late.
    """
    for item, suffix, quantity in ((I1, "1", 10.0), (I2, "2", 2.0)):
        cur.executemany(
            DEMAND_SQL,
            [
                (
                    f"id-de-910-{suffix}-{i}",
                    f"de-910-{suffix}-{i}",
                    item,
                    S1,
                    quantity,
                    (FIRST_DAY + timedelta(days=i)).isoformat(),
                    "shipment",
                    "test",
                )
                for i in range(360)
            ],
        )
    for item, suffix, duration, first in (
        (I1, "1", 10, "2026-09-20"),
        (I2, "2", 5, "2026-09-25"),
    ):
        base = date.fromisoformat(first)
        for i in range(3):
            ordered = base + timedelta(days=i)
            cur.execute(
                LEAD_SQL,
                (
                    f"id-lt-910-{suffix}-{i}",
                    f"lt-910-{suffix}-{i}",
                    f"SUP-910-{suffix}",
                    item,
                    ordered.isoformat(),
                    (ordered + timedelta(days=duration)).isoformat(),
                    "test",
                ),
            )
    cur.executemany(
        ITEM_SITE_SQL,
        [
            ("id-is-910-1", I1, S1, 2.0, 120.0, 5.0, 400.0, 50.0, "test"),
            ("id-is-910-2", I2, S1, 5.0, 12.0, 1.0, None, None, "test"),
        ],
    )
    cur.executemany(
        ON_HAND_SQL,
        [
            ("id-oh-910-1", I1, S1, 30.0, SYNC_AS_OF, "test"),
            ("id-oh-910-2", I2, S1, 2.0, SYNC_AS_OF, "test"),
        ],
    )
    # The one open line: I2's, promised 5 days before today (5 days
    # late) and ordered 20 days before today.
    cur.execute(
        OPEN_LINE_SQL,
        (
            "id-ol-910-1",
            "PO:ORD-910-1:1",
            I2,
            S1,
            "SUP-910-2",
            "po",
            12.0,
            (TODAY - timedelta(days=5)).isoformat(),
            (TODAY - timedelta(days=20)).isoformat(),
            SYNC_AS_OF,
            "test",
        ),
    )
    # Both item-sites are DDMRP buffers, so the DDMRP source answers
    # the plan's own buffer rows.
    cur.executemany(
        BUFFER_SQL,
        [
            (I1, S1, "test", None, None),
            (I2, S1, "test", None, None),
        ],
    )


def _as_json(value) -> object:
    """The value normalized the way the route's JSON answers it (dates
    as their ISO strings)."""
    return json.loads(json.dumps(value, default=str))


def test_issue910_freeform(client, monkeypatch) -> None:
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # Pin "today" on the data date, the way test_issue645_freeform
    # pins its ranking inputs: the DDMRP plan's resolved as-of, the
    # stockout ranking's own default and the expedite list's own
    # default all resolve the same date.
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return TODAY

    monkeypatch.setattr("app.stats.demand.date", _FixedDate)
    monkeypatch.setattr("app.recommend.expedite._today", lambda: TODAY)
    monkeypatch.setattr("app.recommend.supplier_performance._today", lambda: TODAY)

    # Count the underlying computations by wrapping the module's
    # internal builder; each entry is the computed as-of.
    computed: list[date] = []
    real_builder = reorder._compute_recommendations

    def counting_builder(resolved_as_of: date) -> list[dict]:
        computed.append(resolved_as_of)
        return real_builder(resolved_as_of)

    monkeypatch.setattr(reorder, "_compute_recommendations", counting_builder)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    # --- AC-1: once inside --------------------------------------------------
    computed.clear()
    with recommendations_memo():
        r1 = recommendations_for_all(TODAY)
        r2 = recommendations_for_all(TODAY)
        r3 = recommendations_for_all(TODAY)
    assert computed == [TODAY]
    assert r1 == r2 == r3
    assert [(row["item_code"], row["site_code"]) for row in r1] == [
        (I1, S1),
        (I2, S1),
    ]

    # Nested use reuses the outer memo.
    computed.clear()
    with recommendations_memo():
        n1 = recommendations_for_all(TODAY)
        with recommendations_memo():
            n2 = recommendations_for_all(TODAY)
        n3 = recommendations_for_all(TODAY)
    assert computed == [TODAY]
    assert n1 == n2 == n3 == r1

    # --- AC-1: copies --------------------------------------------------------
    computed.clear()
    with recommendations_memo():
        first = recommendations_for_all(TODAY)
        second = recommendations_for_all(TODAY)
        pristine = copy.deepcopy(second)
        first[0]["item_code"] = "MUTATED"
        first[0]["notes"].append("mutated")
        assert second == pristine
        # The memo's own rows are the pristine ones still.
        third = recommendations_for_all(TODAY)
        assert third == pristine
    assert first != pristine
    assert first[0]["item_code"] == "MUTATED"

    # --- AC-1: per as-of ------------------------------------------------------
    computed.clear()
    with recommendations_memo():
        a = recommendations_for_all(TODAY)
        b = recommendations_for_all(TODAY - timedelta(days=1))
        c = recommendations_for_all(TODAY)
    assert computed == [TODAY, TODAY - timedelta(days=1)]
    assert a == c
    assert a != b

    # --- AC-1: outside --------------------------------------------------------
    computed.clear()
    x = recommendations_for_all(TODAY)
    y = recommendations_for_all(TODAY)
    assert computed == [TODAY, TODAY]
    assert x == y

    # --- AC-1: cleared (including after an exception) -------------------------
    computed.clear()
    with recommendations_memo():
        recommendations_for_all(TODAY)
    assert computed == [TODAY]
    recommendations_for_all(TODAY)
    assert computed == [TODAY, TODAY]

    computed.clear()
    raised = False
    try:
        with recommendations_memo():
            recommendations_for_all(TODAY)
            raise RuntimeError("inside the memo")
    except RuntimeError:
        raised = True
    assert raised
    assert computed == [TODAY]
    recommendations_for_all(TODAY)
    assert computed == [TODAY, TODAY]

    # --- AC-2 / AC-3: the inbox -----------------------------------------------
    computed.clear()
    response = client.get("/api/exceptions")
    assert response.status_code == 200, response.text
    body = response.json()
    # The one request runs the recommendations computation exactly
    # once, at the one shared as-of.
    assert computed == [TODAY]
    assert body["as_of"] == TODAY_STR
    assert body["sources_failed"] == []
    assert body["counts"] == {"high": 1, "medium": 3, "acknowledged": 0, "new": 0}
    assert len(body["rows"]) == 4

    # The response's DDMRP rows are the real plan's buffer rows.
    computed.clear()
    ddmrp_as_of, ddmrp_plan_rows = exceptions_api._ddmrp_source()
    expected_ddmrp = _as_json(
        [
            row.model_dump()
            for row in exceptions_api._ddmrp_rows(ddmrp_as_of, ddmrp_plan_rows)
        ]
    )
    assert ddmrp_as_of == TODAY
    actual_ddmrp = [
        row
        for row in body["rows"]
        if row["kind"] in ("buffer_stocked_out", "buffer_critical")
    ]
    assert actual_ddmrp == expected_ddmrp
    assert [(row["item_code"], row["site_code"]) for row in actual_ddmrp] == [
        (I1, S1),
        (I2, S1),
    ]

    # The response's stockout rows are the real ranking's rows.
    computed.clear()
    expected_stockout = _as_json(
        [
            row.model_dump()
            for row in exceptions_api._stockout_rows(stockout_ranked(TODAY))
        ]
    )
    actual_stockout = [
        row
        for row in body["rows"]
        if row["kind"] in ("stockout_likely", "stockout_at_risk")
    ]
    assert actual_stockout == expected_stockout
    assert [(row["item_code"], row["site_code"]) for row in actual_stockout] == [
        (I1, S1)
    ]

    # The response's expedite rows are those built from
    # expedite_list() called with no as-of: its own default (today),
    # so the days_late reflect today, not the data date.
    computed.clear()
    expedite_payload = expedite_list()
    expedite_as_of, expected_expedite = exceptions_api._expedite_rows(expedite_payload)
    assert expedite_as_of == TODAY
    actual_expedite = [
        row
        for row in body["rows"]
        if row["kind"] in ("expedite_after_stockout", "expedite_late")
    ]
    assert actual_expedite == _as_json([row.model_dump() for row in expected_expedite])
    assert len(actual_expedite) == 1
    assert actual_expedite[0]["kind"] == "expedite_late"
    assert actual_expedite[0]["item_code"] == I2
    assert actual_expedite[0]["detail"] == "12 units, 5 days late"
    assert actual_expedite[0]["detected_on"] == TODAY_STR

    # The same request with the memo disabled: the three sources
    # compute three times — in source order, all at the same as-of,
    # the DDMRP plan's — and the JSON is exactly the memoized
    # request's JSON.
    @contextmanager
    def _memo_disabled():
        yield

    monkeypatch.setattr("app.api.exceptions.recommendations_memo", _memo_disabled)
    computed.clear()
    response2 = client.get("/api/exceptions")
    assert response2.status_code == 200, response2.text
    assert computed == [TODAY, TODAY, TODAY, TODAY]
    assert response2.json() == body

    # --- Review defect: a finite huge forecast bias ------------------------
    # A finite ``effective_bias`` whose float percent overflows
    # (``abs(bias) * 100`` past the largest finite float) must still
    # answer its exact whole percent — never a failed forecast
    # source, never a 500.
    huge_bias = 1e307
    # The exact integer percent of 1e307: the float's exact value
    # (9.999999999999999860310597603e306) times 100, rounded — a
    # 309-digit whole number a float percent can never hold.
    huge_pct = "9999999999999999860310597603" + "0" * 281
    monkeypatch.setattr(
        "app.api.exceptions.realised_accuracy",
        lambda **kwargs: {
            "as_of": TODAY_STR,
            "compared_buckets": 7,
            "item_sites": [
                {
                    "item_code": "I910-BIAS",
                    "site_code": S1,
                    "compared_buckets": 3,
                    "effective_bias": huge_bias,
                },
                {
                    "item_code": "I910-UBIAS",
                    "site_code": S1,
                    "compared_buckets": 4,
                    "effective_bias": -huge_bias,
                },
            ],
        },
    )
    response3 = client.get("/api/exceptions")
    assert response3.status_code == 200, response3.text
    body3 = response3.json()
    assert "forecast" not in body3["sources_failed"]
    bias_rows = [row for row in body3["rows"] if row["kind"] == "forecast_bias"]
    assert [(row["item_code"], row["site_code"]) for row in bias_rows] == [
        ("I910-BIAS", S1),
        ("I910-UBIAS", S1),
    ]
    assert bias_rows[0]["detail"] == (
        f"forecast over actual by {huge_pct}% across 3 periods"
    )
    assert bias_rows[1]["detail"] == (
        f"forecast under actual by {huge_pct}% across 4 periods"
    )
    assert [row["detected_on"] for row in bias_rows] == [TODAY_STR, TODAY_STR]
