"""Issue #1563 proof: the buffer order entry of the work scorecard.

``app.supply.release_readback.line_unchanged(item_code, quantity,
erp_status, erp_lines)`` reads and writes nothing: it sums the
``order_qty`` of the ``erp_lines`` whose ``item_code`` is the
released line's and whose ``cancelled`` is not true, and answers True
only when ``erp_status`` is not ``Canceled`` and the sum differs from
``quantity`` by no more than the module's tolerance — the one-line
judgement of ``release_unchanged``, which compares the whole order.
A null ``erp_lines`` is no lines, an item with no such ERP line
answers False, and unusable data — an ``erp_lines`` that is not a
list, a ``quantity`` that is not a number — answers False, never an
error.

``app.api.work.buffer_order_counts()`` runs one SELECT through
``app.db.connection.get_cursor``, the way ``order_release_counts``
counts the ``plan_releases`` rows whose ``outcome`` is ``created``:
its four counts are over those rows' stored lines whose ``source`` is
exactly ``"buffer"`` — not the aggregate releases, nor the releases
stored before the source was written — ``released`` their number,
``automatic`` the number of those in the rows whose ``actor`` starts
with ``standing-grant:``, ``read`` the number of those in the rows
whose ``erp_status`` is not null, and ``unchanged`` the read lines
for which ``line_unchanged`` of the line's ``item_code``, its
``quantity``, the row's ``erp_status`` and ``erp_lines`` is True; a
row whose ``lines`` is not a list counts nothing.

The scorecard gains ``buffer_order`` — "Order on a buffer's signal" —
right after ``order_release``, twelve in all, mapped the same way as
``order_release``.

Proved here, against the test database migrated to head, with the
rows inserted with their readback columns the way the #1508 proof
inserts them, every NOT NULL column filled:

* the rows, each ``created`` unless named otherwise: P1 by "admin",
  read back "Open", the lines B (``"buffer"``, 4) and R
  (``"reorder_point"``, 2) and the ERP lines B 4 and R 2; P2 by
  "standing-grant:7", read back "Open", the lines C (``"buffer"``, 5)
  and T (``"top_up"``, 3) and the ERP lines C 9 and T 3; P3 by
  "admin", not read back, the line D (``"buffer"``, 6); P4 by
  "admin", read back "Canceled", the line E (``"buffer"``, 7) and the
  ERP line E 7; P5 by "admin", read back "Open", the line G
  (``"buffer"``, 10) and the ERP lines G 6 and G 4; P6 by "admin",
  read back "Open", the line H (``"buffer"``, 8) and the ERP line H 8
  with ``cancelled`` true; P7 ``pending`` with the buffer line F; and
  P8 ``created``, read back "Open", with the line X that carries no
  source;
* the counts: ``buffer_order_counts()`` answers ``released`` 6 (B, C,
  D, E, G and H), ``automatic`` 1 (C), ``read`` 5 (all but D) and
  ``unchanged`` 2 (B and G);
* the route: GET /api/work/scorecard with the planner's token lists
  the twelve keys in order, ``buffer_order`` right after
  ``order_release``, with ``step`` "Order on a buffer's signal",
  ``decisions`` 6, ``with_proposal`` 5, ``at_proposal`` 2,
  ``without_proposal`` 0, ``automatic`` 1 and ``rate`` 0.4 — and the
  ``order_release`` entry answers what ``order_release_counts()``
  answers for the same rows;
* the line check: ``line_unchanged`` answers True for the cases of B
  and G; False for those of C, E and H; False for an item with no ERP
  line, an ``erp_lines`` that is not a list, and a ``quantity`` that
  is not a number — never an error;
* nothing written: the row count of ``plan_releases`` is the same
  before and after the route.

Every row the proof inserts is deleted in a ``finally``.
"""

import json
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.work import buffer_order_counts, order_release_counts
from app.db.connection import get_connection, get_cursor
from app.main import app
from app.supply.release_readback import line_unchanged

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


def _line(item_code: str, quantity: int, source: str | None) -> dict:
    """One stored plan line; without a source the key is absent — the
    releases stored before #1549."""
    line = {
        "item_code": item_code,
        "quantity": quantity,
        "unit_cost": "1.00",
        "value": f"{quantity}.00",
    }
    if source is not None:
        line["source"] = source
    return line


def _erp_line(item_code: str, order_qty, cancelled: bool = False) -> dict:
    """One ERP order line, in the shape the client answers with."""
    return {
        "item_code": item_code,
        "warehouse_code": "154",
        "order_qty": order_qty,
        "received_qty": 0,
        "completed": False,
        "cancelled": cancelled,
        "promised": "2026-12-01",
    }


# The eight rows of the proof: (order_nbr, outcome, lines, actor,
# erp_status, erp_lines), every order number unique to this proof.
NBR_P1 = "PO1563-0001"
NBR_P2 = "PO1563-0002"
NBR_P3 = "PO1563-0003"
NBR_P4 = "PO1563-0004"
NBR_P5 = "PO1563-0005"
NBR_P6 = "PO1563-0006"
NBR_P7 = "PO1563-0007"
NBR_P8 = "PO1563-0008"

LINES_P1 = [_line("B", 4, "buffer"), _line("R", 2, "reorder_point")]
ERP_P1 = [_erp_line("B", 4), _erp_line("R", 2)]
LINES_P2 = [_line("C", 5, "buffer"), _line("T", 3, "top_up")]
ERP_P2 = [_erp_line("C", 9), _erp_line("T", 3)]
LINES_P3 = [_line("D", 6, "buffer")]
LINES_P4 = [_line("E", 7, "buffer")]
ERP_P4 = [_erp_line("E", 7)]
LINES_P5 = [_line("G", 10, "buffer")]
ERP_P5 = [_erp_line("G", 6), _erp_line("G", 4)]
LINES_P6 = [_line("H", 8, "buffer")]
ERP_P6 = [_erp_line("H", 8, cancelled=True)]
LINES_P7 = [_line("F", 5, "buffer")]
LINES_P8 = [_line("X", 5, None)]

ROWS = [
    (NBR_P1, "created", LINES_P1, "admin", "Open", ERP_P1),
    (NBR_P2, "created", LINES_P2, "standing-grant:7", "Open", ERP_P2),
    (NBR_P3, "created", LINES_P3, "admin", None, None),
    (NBR_P4, "created", LINES_P4, "admin", "Canceled", ERP_P4),
    (NBR_P5, "created", LINES_P5, "admin", "Open", ERP_P5),
    (NBR_P6, "created", LINES_P6, "admin", "Open", ERP_P6),
    (NBR_P7, "pending", LINES_P7, "admin", None, None),
    (NBR_P8, "created", LINES_P8, "admin", "Open", None),
]


def _insert(
    order_nbr: str,
    outcome: str,
    lines: list[dict],
    actor: str,
    erp_status: str | None,
    erp_lines: list[dict] | None,
) -> int:
    """One plan_releases row, every NOT NULL column filled, with its
    readback columns set the way the readback stores them."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO plan_releases (month, site_code, "
                "supplier_code, origin, order_nbr, outcome, erp_status, "
                "erp_lines, line_count, value, lines, actor) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id",
                (
                    "2026-11",
                    "154",
                    "6995",
                    f"issue1563 proof {order_nbr}",
                    order_nbr,
                    outcome,
                    erp_status,
                    json.dumps(erp_lines),
                    len(lines),
                    sum((Decimal(line["value"]) for line in lines), Decimal(0)),
                    json.dumps(lines),
                    actor,
                ),
            )
            row_id = cur.fetchone()[0]
        conn.commit()
        return row_id
    finally:
        conn.close()


def _plan_releases_row_count() -> int:
    """The row count of the table the route must not write."""
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM plan_releases")
        return cur.fetchone()["count"]


def test_issue1563_freeform(client) -> None:
    inserted: list[int] = []
    try:
        # The eight rows, every NOT NULL column filled.
        for order_nbr, outcome, lines, actor, erp_status, erp_lines in ROWS:
            inserted.append(
                _insert(order_nbr, outcome, lines, actor, erp_status, erp_lines)
            )

        # -- The counts: the buffer lines of the created rows. ----------------
        assert buffer_order_counts() == {
            "released": 6,  # B, C, D, E, G and H
            "automatic": 1,  # C, in the standing-grant row
            "read": 5,  # all the buffer lines but D's
            "unchanged": 2,  # B and G
        }

        # -- The line check: the one-line judgement, called directly. ---------
        # B and G came back the way they went out.
        assert line_unchanged("B", 4, "Open", ERP_P1) is True
        assert line_unchanged("G", 10, "Open", ERP_P5) is True
        # C's ERP side sums to 9, not 5.
        assert line_unchanged("C", 5, "Open", ERP_P2) is False
        # E's order is Canceled.
        assert line_unchanged("E", 7, "Canceled", ERP_P4) is False
        # H's only ERP line is cancelled.
        assert line_unchanged("H", 8, "Open", ERP_P6) is False
        # An item with no ERP line, and a null erp_lines, is no lines.
        assert line_unchanged("Z", 1, "Open", ERP_P1) is False
        assert line_unchanged("B", 4, "Open", None) is False
        # Unusable data answers False, never an error.
        assert line_unchanged("B", 4, "Open", "not a list") is False
        assert line_unchanged("B", 4, "Open", {"B": 4}) is False
        assert line_unchanged("B", "4", "Open", ERP_P1) is False
        assert line_unchanged("B", None, "Open", ERP_P1) is False
        assert line_unchanged("B", True, "Open", ERP_P1) is False

        # -- The route: the twelve keys in order, with the buffer entry. ------
        count_before = _plan_releases_row_count()
        planner = TestClient(app, headers={"Authorization": f"Bearer {PLANNER_TOKEN}"})
        response = planner.get(URL)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"processes"}
        keys = [entry["key"] for entry in body["processes"]]
        assert keys == KEYS
        assert keys.index("buffer_order") == keys.index("order_release") + 1
        processes = {entry["key"]: entry for entry in body["processes"]}
        entry = processes["buffer_order"]
        assert entry["step"] == "Order on a buffer's signal"
        assert (
            entry["decisions"],
            entry["with_proposal"],
            entry["at_proposal"],
            entry["without_proposal"],
            entry["automatic"],
            entry["rate"],
        ) == (6, 5, 2, 0, 1, 0.4)

        # The order_release entry answers what order_release_counts()
        # answers for the same rows, mapped the documented way.
        release_answer = order_release_counts()
        assert release_answer == {
            "released": 7,  # P1 to P6 and P8
            "automatic": 1,  # P2
            "read": 6,  # all the created rows but P3
            "unchanged": 2,  # P1 and P5
        }
        entry = processes["order_release"]
        assert entry["step"] == "Release purchase orders to the ERP"
        assert entry["decisions"] == release_answer["released"]
        assert entry["with_proposal"] == release_answer["read"]
        assert entry["at_proposal"] == release_answer["unchanged"]
        assert entry["without_proposal"] == 0
        assert entry["automatic"] == release_answer["automatic"]
        assert entry["rate"] == round(
            release_answer["unchanged"] / release_answer["read"], 4
        )

        # -- Nothing written: the row count after the route. -------------------
        assert _plan_releases_row_count() == count_before
    finally:
        if inserted:
            with get_cursor() as cur:
                cur.execute("DELETE FROM plan_releases WHERE id = ANY(%s)", (inserted,))
