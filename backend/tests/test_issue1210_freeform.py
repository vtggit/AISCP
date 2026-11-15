r"""Issue #1210 proof: the standing-grant runs, read behind the same Bearer gate.

Everything is exercised against the test database through the app's
test client with the two development stub users the way
``test_issue1173_freeform.py`` does: the admin stub
(``dev-secret-token:admin``) creates the grants through the #1173
routes and the proof inserts ``standing_grant_runs`` rows directly —
with times relative to each grant's ``created_at``, never a fixed
date — while the non-admin stub (role ``user``) reads the same
answer.

Proved here:

  * the empty answer — with no runs stored the route answers 200
    with ``[]``;
  * the order — three runs of ``order_plan_release`` and one of
    ``transfer_release``, inserted out of order, come back newest
    first; two of them share a ``started_at`` and the higher ``id``
    of those two comes first;
  * the shape — a ``released`` run with ``documents`` 1, ``value``
    562.50 and one ``left_out`` entry (``daily_limit``, value
    "1001.00") answers exactly the documented fields, its ``value``
    and its entry's ``value`` the numbers 562.5 and 1001.0, its
    ``started_at`` parsing with a UTC offset and its ``reason`` null;
    a ``skipped`` run answers its reason;
  * the filter and the limit — ``process=transfer_release`` answers
    only the transfer run, ``limit=2`` the two newest runs, and
    ``limit`` 0, 201 and ``abc`` and ``process=aggregate_release``
    each answer 422 in FastAPI's HTTPValidationError envelope, never
    a 500;
  * who may — a signed-in non-admin reads the same answer with 200
    and a request without a token answers 401;
  * nothing written — the runs table is the same after every
    request;
  * the null left_out — a run row stored with a null ``left_out``
    (no left-out groups) is answered with the empty list, a 200,
    never a 500;
  * the identity requirement: the proof also runs as a NON-ADMIN
    (role ``user``, asserted via ``GET /api/auth/me``) in a second
    pass after a full reload (a fresh ``TestClient`` booted the
    production way — the token the page would take from the URL hash
    as ``#access_token=...``, with ``GET /api/auth/config`` answering
    publicly and ``GET /api/auth/me`` resolving the viewer): the
    admin-only controls are refused with 403 (nothing created) while
    the page still renders — the runs it lists answer 200 — and a
    request without a token still gets 401.
"""

import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject
ADMIN_USERNAME = "admin"  # the admin stub user's username
ADMIN_DETAIL = "Role 'admin' is required to perform this action."

URL = "/api/standing-grants"
RUNS_URL = f"{URL}/runs"
_TABLE = "standing_grant_runs"

ORDER_PROCESS = "order_plan_release"
TRANSFER_PROCESS = "transfer_release"
SITE_A = "154"
SUPPLIER_A = "S-1210-1"
SKIP_REASON = "the data is stale"


def _grant_body(
    process: str,
    max_document_value: float,
    max_daily_value: float,
    max_data_age_hours: int,
) -> dict:
    """A valid body for POST /api/standing-grants."""
    return {
        "process": process,
        "max_document_value": max_document_value,
        "max_daily_value": max_daily_value,
        "max_data_age_hours": max_data_age_hours,
    }


def _run_rows() -> list[dict]:
    """Every standing_grant_runs row, oldest first, every column."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {_TABLE} ORDER BY id")
            columns = [c[0] for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
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
    left_out: list[dict] | None,
) -> int:
    """Store one run row directly and return its id.

    The left-out entries hold their values as decimal strings, the
    way the running grant stores them; a ``None`` stores a JSON
    ``null`` in the ``left_out`` column instead of a list.
    """
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
            row_id = cur.fetchone()[0]
        conn.commit()
        return row_id
    finally:
        conn.close()


def _is_number(value: object) -> bool:
    """True for a JSON number: an int or float, never a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def test_issue1210_freeform(client) -> None:
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    unauthenticated = TestClient(app)

    # -- The empty answer: no runs stored yet. ----------------------
    response = admin.get(RUNS_URL)
    assert response.status_code == 200, response.text
    assert response.json() == []
    assert _run_rows() == []

    # -- The grants, created through the #1173 routes. --------------
    created = admin.post(URL, json=_grant_body(ORDER_PROCESS, 5000, 20000, 26))
    assert created.status_code == 201, created.text
    order_grant = created.json()
    created = admin.post(URL, json=_grant_body(TRANSFER_PROCESS, 3000, 9000, 24))
    assert created.status_code == 201, created.text
    transfer_grant = created.json()
    # Each run's time is relative to its grant's created_at — the
    # instant the route answered with a UTC offset, never a fixed
    # date.
    order_created_at = datetime.fromisoformat(order_grant["created_at"])
    transfer_created_at = datetime.fromisoformat(transfer_grant["created_at"])
    assert order_created_at.utcoffset() is not None
    assert transfer_created_at.utcoffset() is not None

    # -- The runs, inserted directly out of order.  The two order
    #    runs that share a started_at: the later insert holds the
    #    higher id and comes first in the answer. -------------------
    tied_earlier = _insert_run(
        ORDER_PROCESS,
        order_grant["id"],
        order_created_at + timedelta(hours=3),
        "skipped",
        SKIP_REASON,
        0,
        "0",
        [],
    )
    released = _insert_run(
        ORDER_PROCESS,
        order_grant["id"],
        order_created_at + timedelta(hours=1),
        "released",
        None,
        1,
        "562.50",
        [
            {
                "site_code": SITE_A,
                "supplier_code": SUPPLIER_A,
                "value": "1001.00",
                "reason": "daily_limit",
            }
        ],
    )
    tied_later = _insert_run(
        ORDER_PROCESS,
        order_grant["id"],
        order_created_at + timedelta(hours=3),
        "nothing_due",
        None,
        0,
        "0",
        [],
    )
    transfer = _insert_run(
        TRANSFER_PROCESS,
        transfer_grant["id"],
        transfer_created_at + timedelta(hours=10),
        "released",
        None,
        2,
        "1563.50",
        [],
    )
    stored = _run_rows()
    assert [row["id"] for row in stored] == [
        tied_earlier,
        released,
        tied_later,
        transfer,
    ]

    # -- The order: newest first, the higher id first on a tie. -----
    response = admin.get(RUNS_URL)
    assert response.status_code == 200, response.text
    runs = response.json()
    assert [row["id"] for row in runs] == [transfer, tied_later, tied_earlier, released]

    # -- The shape: exactly the documented fields. -------------------
    row = next(r for r in runs if r["id"] == released)
    assert set(row) == {
        "id",
        "process",
        "grant_id",
        "started_at",
        "outcome",
        "reason",
        "documents",
        "value",
        "left_out",
    }
    assert row["id"] == released
    assert row["process"] == ORDER_PROCESS
    assert row["grant_id"] == order_grant["id"]
    # The instant comes back as an ISO-8601 string with its UTC
    # offset and is the one the row was stamped with.
    started = datetime.fromisoformat(row["started_at"])
    assert started.utcoffset() is not None
    assert started == order_created_at + timedelta(hours=1)
    assert row["outcome"] == "released"
    assert row["reason"] is None
    assert row["documents"] == 1
    # The value and the entry's value are the numbers the stored
    # decimal strings read as.
    assert _is_number(row["value"])
    assert row["value"] == 562.5
    assert row["left_out"] == [
        {
            "site_code": SITE_A,
            "supplier_code": SUPPLIER_A,
            "value": 1001.0,
            "reason": "daily_limit",
        }
    ]
    assert _is_number(row["left_out"][0]["value"])
    # The skipped run answers its reason.
    skipped = next(r for r in runs if r["id"] == tied_earlier)
    assert skipped["outcome"] == "skipped"
    assert skipped["reason"] == SKIP_REASON
    assert _run_rows() == stored

    # -- The filter and the limit. -----------------------------------
    response = admin.get(RUNS_URL, params={"process": TRANSFER_PROCESS})
    assert response.status_code == 200, response.text
    filtered = response.json()
    assert [row["id"] for row in filtered] == [transfer]
    assert all(row["process"] == TRANSFER_PROCESS for row in filtered)
    response = admin.get(RUNS_URL, params={"process": ORDER_PROCESS})
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [
        tied_later,
        tied_earlier,
        released,
    ]
    response = admin.get(RUNS_URL, params={"limit": 2})
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [transfer, tied_later]

    # The refused query, each 422 in FastAPI's HTTPValidationError
    # envelope, never a 500.
    refused = (
        {"limit": 0},
        {"limit": 201},
        {"limit": "abc"},
        {"process": "aggregate_release"},
    )
    for params in refused:
        response = admin.get(RUNS_URL, params=params)
        assert response.status_code == 422, f"{params}: {response.text}"
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail, f"{params}: {response.text}"
    assert _run_rows() == stored

    # -- Who may: the non-admin reads the same answer; without a
    #    token the route answers 401. --------------------------------
    admin_answer = admin.get(RUNS_URL).json()
    response = viewer.get(RUNS_URL)
    assert response.status_code == 200, response.text
    assert response.json() == admin_answer
    assert unauthenticated.get(RUNS_URL).status_code == 401
    assert _run_rows() == stored

    # -- Identity, second pass after a full reload: the page boots the
    #    production way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the admin-only controls are
    #    refused 403 while the page still renders. --------------------
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
    # The admin stub's identity is the other one: sub and username.
    admin_me = admin.get("/api/auth/me")
    assert admin_me.status_code == 200, admin_me.text
    assert admin_me.json()["user"]["sub"] == ADMIN_SUB
    assert admin_me.json()["user"]["username"] == ADMIN_USERNAME
    # The admin-only control is absent for the viewer: the create is
    # refused with the exact message and nothing is created.
    response = viewer_reloaded.post(URL, json=_grant_body(ORDER_PROCESS, 100, 1000, 24))
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    response = viewer_reloaded.post(f"{URL}/{order_grant['id']}/revoke")
    assert response.status_code == 403, response.text
    # The page still renders: the runs it lists answer 200 with the
    # same body the admin reads, the grants list answers 200, and a
    # request without a token still gets 401.
    response = viewer_reloaded.get(RUNS_URL)
    assert response.status_code == 200, response.text
    assert response.json() == admin_answer
    assert viewer_reloaded.get(URL).status_code == 200
    assert TestClient(app).get(RUNS_URL).status_code == 401
    assert _run_rows() == stored

    # -- A run stored with a JSON null left_out: the route serves
    #    the empty list, a 200, never a 500. ------------------------
    null_left_out = _insert_run(
        ORDER_PROCESS,
        order_grant["id"],
        order_created_at + timedelta(hours=11),
        "nothing_due",
        None,
        0,
        "0",
        None,
    )
    stored = _run_rows()
    response = admin.get(RUNS_URL)
    assert response.status_code == 200, response.text
    runs = response.json()
    # The null-left_out run is the newest and the answer holds the
    # documented shape for it, its left_out the empty list.
    assert [row["id"] for row in runs] == [
        null_left_out,
        transfer,
        tied_later,
        tied_earlier,
        released,
    ]
    row = next(r for r in runs if r["id"] == null_left_out)
    assert set(row) == {
        "id",
        "process",
        "grant_id",
        "started_at",
        "outcome",
        "reason",
        "documents",
        "value",
        "left_out",
    }
    assert row["left_out"] == []
    assert isinstance(row["left_out"], list)
    started = datetime.fromisoformat(row["started_at"])
    assert started.utcoffset() is not None
    assert started == order_created_at + timedelta(hours=11)
    assert row["outcome"] == "nothing_due"
    assert row["reason"] is None
    assert row["documents"] == 0
    assert _is_number(row["value"])
    assert row["value"] == 0
    # The non-admin reads the same answer, and the read writes
    # nothing.
    response = viewer.get(RUNS_URL)
    assert response.status_code == 200, response.text
    assert response.json() == runs
    assert _run_rows() == stored
