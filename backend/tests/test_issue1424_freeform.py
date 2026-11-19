"""Issue #1424 proof: the expedite list's two defaults, split.

AC-1 — with no as-of, ``app.recommend.expedite.expedite_list`` keeps
today for the stale window, the lateness and the stockout projection
and the answer's ``as_of`` is today, but reads the recommendations
with ``recommendations_for_all`` with no argument (the shared
default as-of, so a demand window never counts days the feed has not
delivered yet) and the supplier performance with
``supplier_performance`` with no argument (its own default, today).
With an explicit ``as_of`` both reads take that date, as before.  The
module and function docstrings say which date each read uses.

AC-2 — the passages of ``app.api.exceptions`` that said the expedite
list keeps its own default (today) for its recommendations now say
that the expedite list reads the recommendations at the shared
default, so one exceptions call computes them once, while its
lateness still counts from today; no code of the module changed.

AC-3 — this proof uses the real clock and seeds, on the test
database, steady daily demand for one item-site (10.0 a day for
360 days) ending 2 days before today, the item-site's on-hand
quantity (30.0) and one open purchase line expected yesterday (50.0
units promised yesterday, ordered 20 days before today).  It checks:
the no-as-of list (``expedite_list()`` lists the line as late, its
``as_of`` is today and its ``d`` equals the ``inputs.d`` of the
item-site's row of ``recommendations_for_all()``, which is above the
``inputs.d`` of the row of ``recommendations_for_all(today)``), the
explicit list (``expedite_list(as_of=today)`` lists the line with the
``d`` of ``recommendations_for_all(today)``), the exceptions call
(``_collect()`` with one counting wrapper on
``app.recommend.reorder._compute_recommendations`` and one on
``app.recommend.supplier_performance._validate``, which each
computation calls once: the recommendations are computed exactly
once, the supplier performance exactly once, and the answer's
expedite rows equal those built from ``expedite_list()``) and the
route (``GET /api/expedite`` without ``as_of`` answers 200 and its
body equals ``expedite_list()``).
"""

import json
from datetime import UTC, date, datetime, timedelta

from app.api import exceptions as exceptions_api
from app.db.connection import get_connection
from app.recommend import reorder
from app.recommend import supplier_performance as supplier_performance_module
from app.recommend.expedite import expedite_list
from app.recommend.reorder import recommendations_for_all

ITEM = "I1424"
SITE = "S1424"
SUPPLIER = "SUP-1424"
DEMAND_QUANTITY = 10.0
DEMAND_DAYS = 360
ON_HAND_UNITS = 30.0
OPEN_UNITS = 50.0

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
ITEM_SITE_SQL = (
    "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
    "reorder_point, safety_stock, max_qty, order_qty, "
    "master_lead_time_days, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
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


def _as_json(value) -> object:
    """The value normalized the way the route's JSON answers it (dates
    as their ISO strings)."""
    return json.loads(json.dumps(value, default=str))


def _seed(cur, today: date) -> None:
    """The one item-site's position: steady daily demand for
    ``DEMAND_DAYS`` days ending 2 days before today (so the shared
    default as-of is 2 days before today), the on-hand quantity and
    the open line expected yesterday (promised yesterday, ordered
    20 days before today, so it is listed 1 day late)."""
    first_day = today - timedelta(days=DEMAND_DAYS + 1)
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-1424-{i}",
                f"de-1424-{i}",
                ITEM,
                SITE,
                DEMAND_QUANTITY,
                (first_day + timedelta(days=i)).isoformat(),
                "shipment",
                "test1424",
            )
            for i in range(DEMAND_DAYS)
        ],
    )
    now = datetime.now(UTC).isoformat()
    cur.execute(
        ITEM_SITE_SQL,
        (
            "id-is-1424",
            ITEM,
            SITE,
            2.0,
            None,
            None,
            None,
            None,
            7.0,
            "test1424",
        ),
    )
    cur.execute(
        ON_HAND_SQL,
        ("id-oh-1424", ITEM, SITE, ON_HAND_UNITS, now, "test1424"),
    )
    cur.execute(
        OPEN_LINE_SQL,
        (
            "id-ol-1424",
            "PO:1424:1",
            ITEM,
            SITE,
            SUPPLIER,
            "PO",
            OPEN_UNITS,
            (today - timedelta(days=1)).isoformat(),
            (today - timedelta(days=20)).isoformat(),
            now,
            "test1424",
        ),
    )


def _d_of(rows: list[dict]) -> float:
    """The item-site's row of one ``recommendations_for_all`` answer,
    its ``inputs.d`` (the row is the "ok" one of the population)."""
    row = next(
        row for row in rows if row["item_code"] == ITEM and row["site_code"] == SITE
    )
    assert row["status"] == "ok"
    return row["inputs"]["d"]


def test_issue1424_freeform(client, monkeypatch) -> None:
    # The real clock: today is whatever the server says now.
    today = date.today()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur, today)
        conn.commit()
    finally:
        conn.close()

    # --- The no-as-of list: today for the window and the flags, the
    # shared default (2 days before today) for the recommendations.
    answer = expedite_list()
    assert answer["as_of"] == today.isoformat()
    assert answer["stale_excluded"] == 0
    assert len(answer["lines"]) == 1
    line = answer["lines"][0]
    assert (line["item_code"], line["site_code"]) == (ITEM, SITE)
    assert (line["order_type"], line["order_nbr"], line["line_nbr"]) == (
        "PO",
        "1424",
        "1",
    )
    assert line["supplier_code"] == SUPPLIER
    assert line["expected_on"] == today - timedelta(days=1)
    assert line["days_late"] == 1
    assert line["reasons"] == ["late"]

    d_default = _d_of(recommendations_for_all())
    assert line["d"] == d_default
    # The shared default (2 days before today) counts only delivered
    # days; today's window counts the 2 days the feed has not
    # delivered yet, so its daily demand is lower.
    d_today = _d_of(recommendations_for_all(today))
    assert d_default > d_today

    # --- The explicit as-of: both reads take it, as before.
    explicit = expedite_list(as_of=today)
    assert explicit["as_of"] == today.isoformat()
    assert len(explicit["lines"]) == 1
    assert explicit["lines"][0]["reasons"] == ["late"]
    assert explicit["lines"][0]["d"] == d_today

    # --- The exceptions call: one recommendations computation and
    # one supplier performance computation.
    computed: list[date] = []
    real_compute = reorder._compute_recommendations

    def counting_compute(resolved_as_of: date) -> list[dict]:
        computed.append(resolved_as_of)
        return real_compute(resolved_as_of)

    monkeypatch.setattr(reorder, "_compute_recommendations", counting_compute)

    validations: list[tuple] = []
    real_validate = supplier_performance_module._validate

    def counting_validate(as_of, window_days):
        validations.append((as_of, window_days))
        return real_validate(as_of, window_days)

    monkeypatch.setattr(supplier_performance_module, "_validate", counting_validate)

    computed.clear()
    validations.clear()
    _, rows, _ = exceptions_api._collect()
    # The one computation runs at the shared default — the DDMRP
    # plan's read, which the stockout ranking and the expedite list
    # (no as-of: its own default read) answer from the memo — and the
    # one supplier performance computation is the expedite list's
    # (no as-of: its own default, today), which the supplier risk
    # rating and the second-source candidates answer from the read
    # cache.
    assert len(computed) == 1
    assert computed == [today - timedelta(days=2)]
    assert len(validations) == 1

    # The answer's expedite rows equal those built from
    # expedite_list().
    _, expected_expedite = exceptions_api._expedite_rows(expedite_list())
    actual_expedite = [
        row for row in rows if row.kind in ("expedite_after_stockout", "expedite_late")
    ]
    assert actual_expedite == expected_expedite
    assert len(actual_expedite) == 1
    assert actual_expedite[0].kind == "expedite_late"
    assert actual_expedite[0].detail == f"{OPEN_UNITS:g} units, 1 days late"
    assert actual_expedite[0].detected_on == today

    # --- The route: GET /api/expedite without as_of.
    response = client.get("/api/expedite")
    assert response.status_code == 200, response.text
    assert response.json() == _as_json(expedite_list())
