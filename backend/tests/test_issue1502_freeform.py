"""Issue #1502 proof: the standing-grant runs' record, per process.

``GET /api/standing-grants/record`` (the router
``app.api.erp_standing_grants``, prefix ``/api/standing-grants``,
included in ``app.main`` behind the app's include-time
authentication gate like every other API router) answers
``{"processes": [...]}`` — one entry for ``order_plan_release`` and
then one for ``transfer_release``, each ``{process, runs, released,
sent_everything, nothing_due, skipped, failed, rate,
documents_sent, value_sent, latest}``: ``runs`` the number of the
process's stored runs, ``released``, ``nothing_due``, ``skipped``
and ``failed`` those runs counted by outcome,
``sent_everything`` the ``released`` runs whose ``left_out`` is
empty, ``rate`` ``sent_everything / (runs - nothing_due)`` rounded
to 4 decimals, null while ``runs - nothing_due`` is 0,
``documents_sent`` the sum of the runs' ``documents`` and
``value_sent`` the sum of the runs' ``value``, a number rounded to
2 decimal places with ``decimal.ROUND_HALF_UP``; ``latest`` the
process's newest run (by ``started_at`` descending, the greater
``id`` breaking a tie) — ``{started_at, outcome, reason,
documents, value, held_back, held_back_value}`` with
``started_at`` an ISO-8601 string with its UTC offset, ``value`` a
number, ``held_back`` the number of the entries of its
``left_out`` and ``held_back_value`` the sum of their values, a
number rounded to 2 decimal places with
``decimal.ROUND_HALF_UP`` — or null while the process has no run,
the entry then answering ``runs`` 0, every count 0, ``rate`` null,
``documents_sent`` 0, ``value_sent`` 0 and ``latest`` null.  The
read is one SELECT over ``standing_grant_runs`` through
``app.db.connection.get_cursor`` (at most two), the route is open
to any signed-in user, like ``GET /api/standing-grants/runs``,
answers 401 without a token and writes nothing; every other route
of the module is unchanged.

Proved here, on the test database migrated to head:

* the setup — the proof inserts two grants into
  ``erp_standing_grants``, one per process, every NOT NULL column
  filled, every CHECK constraint met, both revoked (``revoked_at``
  and ``revoked_by`` set) so no grant in force is touched; the
  route's answer is read before any run is inserted and answers the
  zero state for both processes — ``runs`` 0, every count 0,
  ``rate`` null, ``documents_sent`` 0, ``value_sent`` 0 and
  ``latest`` null, ``order_plan_release`` first, then
  ``transfer_release``;
* the runs — the proof inserts runs into
  ``standing_grant_runs``, every NOT NULL column filled: for
  ``order_plan_release`` a ``released`` run (documents 2, value
  300.00, ``left_out`` empty) at 2099-01-01T00:00:00Z, a
  ``released`` run (documents 1, value 100.00, one
  ``left_out`` entry of value "50.25", reason
  ``document_limit``) at 2099-01-02, a ``nothing_due`` run at
  2099-01-03 and a ``skipped`` run (reason "every due document is
  over a limit", two ``left_out`` entries, "1000.00"
  ``document_limit`` and "20.00" ``daily_limit``) at
  2099-01-04; for ``transfer_release`` one ``failed`` run (reason
  "the send raised") at 2099-01-05;
* the answer after, for the ``user`` role — ``order_plan_release``:
  ``runs`` rose by 4, ``released`` by 2, ``sent_everything`` by 1,
  ``nothing_due`` by 1, ``skipped`` by 1, ``failed`` by 0,
  ``documents_sent`` by 3 and ``value_sent`` by 400.00, the exact
  number ``decimal.ROUND_HALF_UP`` at 2 places gives the 300.00 +
  100.00 of the inserted runs; its ``latest`` is the 2099-01-04
  run — outcome ``skipped``, that reason, documents 0, value 0,
  ``held_back`` 2, ``held_back_value`` the exact number
  ``decimal.ROUND_HALF_UP`` at 2 places gives the 1000.00 + 20.00
  of the two left-out entries, 1020.00, and ``started_at``
  "2099-01-04T00:00:00+00:00"; ``transfer_release``: ``runs`` and
  ``failed`` rose by 1, its ``latest`` the failed run,
  ``started_at`` "2099-01-05T00:00:00+00:00", ``held_back`` 0 and
  ``held_back_value`` 0; and for both entries ``rate`` equals
  ``round(sent_everything / (runs - nothing_due), 4)`` taken from
  that entry's own numbers;
* access — the route answers 401 without a token, and the read
  writes nothing: the row count of ``standing_grant_runs`` is the
  same before and after the route's calls;
* the rounding the record's money answers use — ``value_sent`` and
  ``held_back_value`` go through the route's one rounding helper,
  and the proof pins it on values whose answer is not exact: a
  half-cent tie rounds up (``decimal.ROUND_HALF_UP``, not
  ``ROUND_HALF_EVEN``, ``ROUND_HALF_DOWN``, ``ROUND_DOWN`` or
  ``ROUND_FLOOR``), a tail below the half stays down (not
  ``ROUND_UP`` or ``ROUND_CEILING``) and the precision is 2 places
  (1, 3 or 4 would move a tie probe) — no other mode or precision
  passes;
* the record's ``latest`` keeps full microsecond precision near
  ``datetime.max`` — the route's own selection is probed on
  synthetic rows, without touching the database: a run started one
  microsecond before ``9999-12-31T23:59:59.999999+00:00`` (the
  float a ``timestamp()`` would make of the two is the same
  number) is not the newest even when its id is the greater, and a
  true tie of the same instant is still broken by the greater id.

Identity — the proof also runs as a NON-ADMIN (role ``user``): a
second pass in the same test after a full reload, with the page
booted the production way (the token it would take from the URL
hash as ``#access_token=...`` — the development token, role
"user" — sent as its Bearer token), ``GET /api/auth/config``
answering publicly and ``GET /api/auth/me`` resolving the viewer;
the record has no admin-only controls, so it answers the viewer
200 with the full answer while the page still renders, and a
request without a token still gets 401.

Every row the proof inserted is deleted at its end.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi.testclient import TestClient

from app.api.erp_standing_grants import _record_process, _round_money
from app.db.connection import get_connection
from app.main import app

URL = "/api/standing-grants/record"
VIEWER_TOKEN = "dev-secret-token:user"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

ORDER_PROCESS = "order_plan_release"
TRANSFER_PROCESS = "transfer_release"
PROCESSES = [ORDER_PROCESS, TRANSFER_PROCESS]

ENTRY_FIELDS = {
    "process",
    "runs",
    "released",
    "sent_everything",
    "nothing_due",
    "skipped",
    "failed",
    "rate",
    "documents_sent",
    "value_sent",
    "latest",
}
LATEST_FIELDS = {
    "started_at",
    "outcome",
    "reason",
    "documents",
    "value",
    "held_back",
    "held_back_value",
}

# The precision and the rounding mode the record's money answers are
# rounded with — the proof's expected values are computed with the
# same explicit mode and precision, so a different mode or precision
# cannot pass.
TWO_PLACES = Decimal("0.01")

# The exact rounded values the inserted runs give: 300.00 + 100.00
# sent, 1000.00 + 20.00 held back by the 2099-01-04 skipped run.
EXPECTED_VALUE_SENT_DELTA = float(
    (Decimal("300.00") + Decimal("100.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
)
EXPECTED_HELD_BACK_VALUE = float(
    (Decimal("1000.00") + Decimal("20.00")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
)


def _is_number(value: object) -> bool:
    """True for a JSON number: an int or float, never a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _insert_grant(process: str) -> int:
    """Store one revoked grant directly and return its id.

    Every NOT NULL column is filled — ``process``, the two values,
    ``max_data_age_hours``, ``created_at`` and ``created_by`` — and
    every CHECK constraint is met; ``revoked_at`` and ``revoked_by``
    are set, so the grant is not in force and no grant in force is
    touched.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO erp_standing_grants "
                "(process, max_document_value, max_daily_value, "
                "max_data_age_hours, created_at, created_by, "
                "revoked_at, revoked_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    process,
                    "1000",
                    "10000",
                    24,
                    datetime(2098, 12, 31, 0, 0, 0, tzinfo=UTC),
                    "issue1502-proof",
                    datetime(2098, 12, 31, 12, 0, 0, tzinfo=UTC),
                    "issue1502-proof",
                ),
            )
            grant_id = cur.fetchone()[0]
        conn.commit()
        return grant_id
    finally:
        conn.close()


def _insert_run(
    process: str,
    grant_id: int,
    started_at: datetime,
    outcome: str,
    reason: str | None,
    documents: int,
    value: str,
    left_out: list[dict],
) -> int:
    """Store one run row directly, every NOT NULL column filled,
    and return its id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO standing_grant_runs "
                "(process, grant_id, started_at, outcome, reason, "
                "documents, value, left_out) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
                "RETURNING id",
                (
                    process,
                    grant_id,
                    started_at,
                    outcome,
                    reason,
                    documents,
                    value,
                    json.dumps(left_out),
                ),
            )
            run_id = cur.fetchone()[0]
        conn.commit()
        return run_id
    finally:
        conn.close()


def _run_row_count() -> int:
    """The row count of standing_grant_runs."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM standing_grant_runs")
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def _delete_runs(run_ids: list[int]) -> None:
    """Delete the run rows the proof inserted, by id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for run_id in run_ids:
                cur.execute("DELETE FROM standing_grant_runs WHERE id = %s", (run_id,))
        conn.commit()
    finally:
        conn.close()


def _delete_grants(grant_ids: list[int]) -> None:
    """Delete the grant rows the proof inserted, by id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for grant_id in grant_ids:
                cur.execute(
                    "DELETE FROM erp_standing_grants WHERE id = %s", (grant_id,)
                )
        conn.commit()
    finally:
        conn.close()


def _synthetic_run(run_id: int, started_at: datetime, outcome: str) -> dict:
    """One run row, shaped as the record's SELECT answers it, for
    probing the record's own latest-selection without touching the
    database."""
    return {
        "id": run_id,
        "process": ORDER_PROCESS,
        "started_at": started_at,
        "outcome": outcome,
        "reason": None,
        "documents": 0,
        "value": Decimal("0"),
        "left_out": [],
    }


def _assert_entry_shape(entry: dict) -> None:
    """The entry answers exactly the documented fields, the money
    fields as numbers and ``rate`` a number or null."""
    assert set(entry) == ENTRY_FIELDS
    for key in (
        "runs",
        "released",
        "sent_everything",
        "nothing_due",
        "skipped",
        "failed",
        "documents_sent",
    ):
        assert isinstance(entry[key], int) and not isinstance(entry[key], bool), key
    assert _is_number(entry["value_sent"]), entry
    assert entry["rate"] is None or _is_number(entry["rate"]), entry
    if entry["latest"] is not None:
        assert set(entry["latest"]) == LATEST_FIELDS, entry["latest"]
        assert _is_number(entry["latest"]["value"]), entry["latest"]
        assert _is_number(entry["latest"]["held_back_value"]), entry["latest"]
        assert isinstance(entry["latest"]["documents"], int), entry["latest"]
        assert isinstance(entry["latest"]["held_back"], int), entry["latest"]
        # The instant carries its UTC offset.
        started = datetime.fromisoformat(entry["latest"]["started_at"])
        assert started.utcoffset() is not None


def test_issue1502_freeform(client) -> None:
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    unauthenticated = TestClient(app)

    grant_ids: list[int] = []
    run_ids: list[int] = []
    try:
        # -- The setup: two grants, one per process, both revoked —
        #    every NOT NULL column filled, every CHECK met, no grant
        #    in force touched. ---------------------------------------
        grant_ids = [
            _insert_grant(ORDER_PROCESS),
            _insert_grant(TRANSFER_PROCESS),
        ]

        # -- The answer before any run is inserted: the zero state for
        #    both processes, in the documented order. ----------------
        response = viewer.get(URL)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"processes"}
        assert [entry["process"] for entry in body["processes"]] == PROCESSES
        before = {entry["process"]: entry for entry in body["processes"]}
        for process in PROCESSES:
            entry = before[process]
            _assert_entry_shape(entry)
            assert entry["runs"] == 0
            assert entry["released"] == 0
            assert entry["sent_everything"] == 0
            assert entry["nothing_due"] == 0
            assert entry["skipped"] == 0
            assert entry["failed"] == 0
            assert entry["rate"] is None
            assert entry["documents_sent"] == 0
            assert entry["value_sent"] == 0
            assert entry["latest"] is None

        # -- Access: without a token the route answers 401. ----------
        assert unauthenticated.get(URL).status_code == 401

        # -- The runs, inserted directly, every NOT NULL column
        #    filled. ---------------------------------------------------
        run_ids.append(
            _insert_run(
                ORDER_PROCESS,
                grant_ids[0],
                datetime(2099, 1, 1, 0, 0, 0, tzinfo=UTC),
                "released",
                None,
                2,
                "300.00",
                [],
            )
        )
        run_ids.append(
            _insert_run(
                ORDER_PROCESS,
                grant_ids[0],
                datetime(2099, 1, 2, 0, 0, 0, tzinfo=UTC),
                "released",
                None,
                1,
                "100.00",
                [
                    {
                        "site_code": "154",
                        "supplier_code": "S-1502-1",
                        "value": "50.25",
                        "reason": "document_limit",
                    }
                ],
            )
        )
        run_ids.append(
            _insert_run(
                ORDER_PROCESS,
                grant_ids[0],
                datetime(2099, 1, 3, 0, 0, 0, tzinfo=UTC),
                "nothing_due",
                None,
                0,
                "0",
                [],
            )
        )
        run_ids.append(
            _insert_run(
                ORDER_PROCESS,
                grant_ids[0],
                datetime(2099, 1, 4, 0, 0, 0, tzinfo=UTC),
                "skipped",
                "every due document is over a limit",
                0,
                "0",
                [
                    {
                        "site_code": "154",
                        "supplier_code": "S-1502-1",
                        "value": "1000.00",
                        "reason": "document_limit",
                    },
                    {
                        "site_code": "159",
                        "supplier_code": "S-1502-2",
                        "value": "20.00",
                        "reason": "daily_limit",
                    },
                ],
            )
        )
        run_ids.append(
            _insert_run(
                TRANSFER_PROCESS,
                grant_ids[1],
                datetime(2099, 1, 5, 0, 0, 0, tzinfo=UTC),
                "failed",
                "the send raised",
                0,
                "0",
                [],
            )
        )

        # The row count before the route's calls.
        count_before = _run_row_count()

        # -- The answer after, for the user role. ---------------------
        response = viewer.get(URL)
        assert response.status_code == 200, response.text
        after_body = response.json()
        assert set(after_body) == {"processes"}
        assert [entry["process"] for entry in after_body["processes"]] == PROCESSES
        after = {entry["process"]: entry for entry in after_body["processes"]}
        for process in PROCESSES:
            _assert_entry_shape(after[process])

        order = after[ORDER_PROCESS]
        transfer = after[TRANSFER_PROCESS]

        def _delta(entry: dict, key: str):
            return entry[key] - before[entry["process"]][key]

        # order_plan_release: the four runs, by outcome and in total.
        assert _delta(order, "runs") == 4
        assert _delta(order, "released") == 2
        assert _delta(order, "sent_everything") == 1
        assert _delta(order, "nothing_due") == 1
        assert _delta(order, "skipped") == 1
        assert _delta(order, "failed") == 0
        assert _delta(order, "documents_sent") == 3
        # The exact rounded value: 300.00 + 100.00, ROUND_HALF_UP at
        # 2 places — 400.00, no other mode or precision included.
        assert EXPECTED_VALUE_SENT_DELTA == 400.00
        assert _delta(order, "value_sent") == EXPECTED_VALUE_SENT_DELTA

        # Its latest is the 2099-01-04 skipped run: the two left-out
        # groups held back, their values 1000.00 + 20.00 = 1020.00 —
        # the exact number ROUND_HALF_UP at 2 places gives.
        latest = order["latest"]
        assert latest is not None
        assert latest["started_at"] == "2099-01-04T00:00:00+00:00"
        assert latest["outcome"] == "skipped"
        assert latest["reason"] == "every due document is over a limit"
        assert latest["documents"] == 0
        assert latest["value"] == 0
        assert latest["held_back"] == 2
        assert EXPECTED_HELD_BACK_VALUE == 1020.00
        assert latest["held_back_value"] == EXPECTED_HELD_BACK_VALUE

        # transfer_release: the one failed run, nothing held back.
        assert _delta(transfer, "runs") == 1
        assert _delta(transfer, "failed") == 1
        assert _delta(transfer, "released") == 0
        assert _delta(transfer, "sent_everything") == 0
        assert _delta(transfer, "nothing_due") == 0
        assert _delta(transfer, "skipped") == 0
        assert _delta(transfer, "documents_sent") == 0
        assert _delta(transfer, "value_sent") == 0
        latest = transfer["latest"]
        assert latest is not None
        assert latest["started_at"] == "2099-01-05T00:00:00+00:00"
        assert latest["outcome"] == "failed"
        assert latest["reason"] == "the send raised"
        assert latest["documents"] == 0
        assert latest["value"] == 0
        assert latest["held_back"] == 0
        assert latest["held_back_value"] == 0

        # For both entries: the rate from the entry's own numbers —
        # null only while runs - nothing_due is 0.
        for entry in (order, transfer):
            denominator = entry["runs"] - entry["nothing_due"]
            if denominator == 0:
                assert entry["rate"] is None
            else:
                assert entry["rate"] == round(entry["sent_everything"] / denominator, 4)

        # -- The rounding the record's money answers use, pinned on
        #    its own: the sums above are exact at 2 places, so only
        #    probes whose answer is not exact tell the mode and the
        #    precision apart.  _round_money is the helper
        #    value_sent and held_back_value are rounded with. -----
        # A half-cent tie rounds UP (ROUND_HALF_UP — ROUND_HALF_EVEN,
        # ROUND_HALF_DOWN, ROUND_DOWN and ROUND_FLOOR would leave
        # it), a tail below the half stays down (ROUND_UP and
        # ROUND_CEILING would move it) and the precision is 2
        # places (1, 3 or 4 places moves a tie probe); no other mode
        # or precision passes all of them.
        assert _round_money(Decimal("400.005")) == 400.01
        assert _round_money(Decimal("1020.005")) == 1020.01
        assert _round_money(Decimal("400.004")) == 400.00
        assert _round_money(Decimal("1020.006")) == 1020.01
        assert _round_money(Decimal("0.005")) == 0.01

        # -- The record's latest keeps full microsecond precision
        #    near datetime.max, over the route's own selection on
        #    synthetic rows — nothing written.  A .timestamp() float
        #    reads 9999-12-31T23:59:59.999999+00:00 and one
        #    microsecond earlier as the same number, so the instant
        #    itself — not that float — must order the runs, and the
        #    greater id breaks only a true tie of the same instant.
        max_instant = datetime.max.replace(tzinfo=UTC)
        edge_entry = _record_process(
            ORDER_PROCESS,
            [
                _synthetic_run(1, max_instant, "released"),
                _synthetic_run(2, max_instant - timedelta(microseconds=1), "failed"),
            ],
        )
        assert edge_entry["latest"]["started_at"] == max_instant.isoformat()
        assert edge_entry["latest"]["outcome"] == "released"
        tied_instant = datetime(2099, 1, 6, 0, 0, 0, tzinfo=UTC)
        tied_entry = _record_process(
            ORDER_PROCESS,
            [
                _synthetic_run(1, tied_instant, "failed"),
                _synthetic_run(2, tied_instant, "released"),
            ],
        )
        assert tied_entry["latest"]["outcome"] == "released"

        # -- Identity, second pass in the same test after a full
        #    reload: the page boots the production way (the token it
        #    would take from the URL hash as #access_token=... — the
        #    development token, role "user" — sent as its Bearer
        #    token), /api/auth/config answers publicly and
        #    /api/auth/me resolves the viewer; the record has no
        #    admin-only controls, so it answers the viewer 200 with
        #    the full answer while the page still renders. -----------
        viewer_reloaded = TestClient(
            app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
        )
        assert TestClient(app).get("/api/auth/config").status_code == 200
        me = viewer_reloaded.get("/api/auth/me")
        assert me.status_code == 200, me.text
        user = me.json()["user"]
        assert user["sub"] == VIEWER_SUB
        assert user["roles"] == ["user"]
        assert "admin" not in user["roles"]
        response = viewer_reloaded.get(URL)
        assert response.status_code == 200, response.text
        assert response.json() == after_body
        # And the gate is intact: a request without a token still
        # gets 401.
        assert TestClient(app).get(URL).status_code == 401

        # -- Nothing written: the row count after the route's calls. --
        assert _run_row_count() == count_before
    finally:
        _delete_runs(run_ids)
        _delete_grants(grant_ids)
