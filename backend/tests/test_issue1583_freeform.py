"""Issue #1583 proof: the short order entry of the work scorecard.

``app.api.work.short_order_counts()`` runs one SELECT through
``app.db.connection.get_cursor``, over ``short_order_releases`` —
the record of the released short orders, one row per short supplier
a confirmed order-plan release went out for: ``released`` is the
number of rows, ``followed`` the rows whose ``chosen`` equals their
``suggested``, and ``automatic`` the rows whose ``actor`` starts
with ``standing-grant:``.

The scorecard gains ``short_order`` — "Decide: wait, order anyway,
or add the top-ups" — right after ``buffer_order``, twelve in all:
``decisions`` and ``with_proposal`` are ``released``, ``at_proposal``
is ``followed``, ``without_proposal`` is 0, ``automatic`` is
``automatic`` and ``rate`` is ``followed`` over ``released``, null
while ``released`` is 0.

Proved here, against the test database migrated to head, with the
rows inserted straight into ``short_order_releases``, every NOT NULL
column filled:

* with no rows, ``short_order_counts()`` answers ``released`` 0,
  ``followed`` 0 and ``automatic`` 0, and the entry answers
  ``decisions`` 0, ``with_proposal`` 0, ``at_proposal`` 0,
  ``without_proposal`` 0, ``automatic`` 0 and ``rate`` null, right
  after ``buffer_order`` in the twelve keys in order;
* the four rows — ``add_top_ups`` chosen as suggested by a planner,
  ``order_anyway`` chosen as suggested by a planner, ``wait``
  suggested with ``order_anyway`` chosen by a planner, and
  ``add_top_ups`` suggested with ``order_anyway`` chosen by
  ``standing-grant:7`` — make ``short_order_counts()`` answer
  ``released`` 4, ``followed`` 2 and ``automatic`` 1, and the entry
  answer ``decisions`` 4, ``with_proposal`` 4, ``at_proposal`` 2,
  ``without_proposal`` 0, ``automatic`` 1 and ``rate`` 0.5;
* the other entries answer the same with and without the rows, and
  the route writes nothing to ``short_order_releases``.

Every field of the entry is read strictly (``entry["at_proposal"]``,
never ``.get`` with a default), and the rows the proof inserts are
deleted in a ``finally``.
"""

from fastapi.testclient import TestClient

from app.api.work import short_order_counts
from app.db.connection import get_connection, get_cursor
from app.main import app

URL = "/api/work/scorecard"
# The planner's token: the role "user" development token.
PLANNER_TOKEN = "dev-secret-token:user"

KEYS = [
    "reorder_points",
    "excess",
    "stockout",
    "exceptions",
    "forecast_overrides",
    "sop_signoff",
    "purchase_budget",
    "ddmrp_adjustment",
    "order_release",
    "buffer_order",
    "short_order",
    "transfer_release",
]

# The four rows: (suggested, chosen, actor).
ROWS = [
    ("add_top_ups", "add_top_ups", "dev-user-1"),
    ("order_anyway", "order_anyway", "dev-user-1"),
    ("wait", "order_anyway", "dev-user-1"),
    ("add_top_ups", "order_anyway", "standing-grant:7"),
]


def _insert(suggested: str, chosen: str, actor: str) -> int:
    """One short_order_releases row, every NOT NULL column filled."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO short_order_releases "
                "(as_of, supplier_code, source_system, minimum, shortfall, "
                "suggested, suggestion_reason, chosen, actor) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id",
                (
                    "2026-11-20",
                    "S1583",
                    "acumatica",
                    "700.00",
                    "550.00",
                    suggested,
                    "the proof's fixed reason",
                    chosen,
                    actor,
                ),
            )
            row_id = cur.fetchone()[0]
        conn.commit()
        return row_id
    finally:
        conn.close()


def _short_order_row_count() -> int:
    """The row count of the table the route must not write."""
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM short_order_releases")
        return cur.fetchone()["count"]


def test_issue1583_freeform(client) -> None:
    inserted: list[int] = []
    try:
        planner = TestClient(app, headers={"Authorization": f"Bearer {PLANNER_TOKEN}"})

        # -- With no rows: the entry is all zero and its rate null. -----------
        assert short_order_counts() == {"released": 0, "followed": 0, "automatic": 0}
        response = planner.get(URL)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"processes"}
        keys = [entry["key"] for entry in body["processes"]]
        assert keys == KEYS
        assert keys.index("short_order") == keys.index("buffer_order") + 1
        processes = {entry["key"]: entry for entry in body["processes"]}
        entry = processes["short_order"]
        assert entry["step"] == "Decide: wait, order anyway, or add the top-ups"
        assert (
            entry["decisions"],
            entry["with_proposal"],
            entry["at_proposal"],
            entry["without_proposal"],
            entry["automatic"],
            entry["rate"],
        ) == (0, 0, 0, 0, 0, None)
        others_without_rows = {
            key: other for key, other in processes.items() if key != "short_order"
        }

        # -- The four rows, inserted straight into the table. ------------------
        for suggested, chosen, actor in ROWS:
            inserted.append(_insert(suggested, chosen, actor))

        # -- The counts over the four rows. -------------------------------------
        assert short_order_counts() == {"released": 4, "followed": 2, "automatic": 1}

        # -- The route with the rows: the entry counts them. -------------------
        count_before = _short_order_row_count()
        response = planner.get(URL)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"processes"}
        keys = [entry["key"] for entry in body["processes"]]
        assert keys == KEYS
        processes = {entry["key"]: entry for entry in body["processes"]}
        entry = processes["short_order"]
        assert entry["step"] == "Decide: wait, order anyway, or add the top-ups"
        assert (
            entry["decisions"],
            entry["with_proposal"],
            entry["at_proposal"],
            entry["without_proposal"],
            entry["automatic"],
            entry["rate"],
        ) == (4, 4, 2, 0, 1, 0.5)

        # -- The other entries answer the same with and without the rows. ------
        for key, before in others_without_rows.items():
            assert processes[key] == before, key

        # -- Nothing written: the row count after the route. -------------------
        assert _short_order_row_count() == count_before
    finally:
        if inserted:
            with get_cursor() as cur:
                cur.execute(
                    "DELETE FROM short_order_releases WHERE id = ANY(%s)", (inserted,)
                )
