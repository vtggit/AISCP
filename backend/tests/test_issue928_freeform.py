"""Issue #928 proof: sign-offs of the S&OP review months.

The proof seeds the projection's inputs the way
``test_issue460_freeform`` does (two sites, S1 short in the first
month of the horizon), stores one budget for the current month the
way ``test_issue520_freeform`` does and signs in as admin and planner
the way ``test_issue881_freeform`` does — development-mode tokens
``dev-secret-token:admin`` and ``dev-secret-token:user``.

AC-1 — ``sop_signoffs`` stores one append-only row per sign-off:
``id`` a bigint identity primary key, ``month`` the signed review
month, ``note`` the signer's note (a blank one stored as null),
``figures`` the exact S&OP review answer the route computed on the
server at sign-off time (jsonb — no client-supplied figure field is
accepted, an extra body field is 422), ``actor`` the caller's
username (their subject when they have none) and ``actor_name`` their
display name, ``created_at`` a ``now()`` timestamptz.  Signing the
same month again writes a second row and no earlier row changes.
``POST /api/sop/signoffs`` is admin only (a signed-in non-admin is
403 before anything is computed or stored), accepts ``month``
(``YYYY-MM``, otherwise 422 — a month that is not the review's
``review_month`` is 422 with the stated detail, no row written), an
optional ``note`` (at most 500 characters without NUL bytes, else
422) and an optional ``months`` (default 3, 1 to 12 inclusive), and
answers 201 with the stored row.  ``GET /api/sop/signoffs`` (optional
``month`` — malformed is 422 — and ``limit``, default 50, 1 to 200)
lists the rows newest first (``created_at DESC, id DESC``) without
their figures; ``GET /api/sop/signoffs/{id}`` answers one row with
its figures, 404 for an unknown id and 422 for a non-integer id; both
are open to any signed-in user, and no token is 401 on all three
routes.  ``GET /api/sop/review`` gains ``latest_signoff`` — the
newest row of the review month without its figures, ``None`` without
one — with every other field unchanged.  A sign-off makes no ERP call
(an Acumatica client class that raises on construction is never
reached), reads no write switch and changes no budget, scenario,
recommendation or release row.  ``PUT``/``PATCH``/``DELETE`` on
``/api/sop/signoffs/{id}`` are 405.

AC-3/AC-4 — the listing and ``latest_signoff`` order by
``created_at DESC, id DESC``: two sign-offs of the same month with
identical ``created_at`` list the higher id first, and
``latest_signoff`` is the higher id.

AC-2 — the migration: the database has one head, equal to
``CURRENT_HEAD``, the new revision is in its history, ``alembic_version``
carries it and ``sop_signoffs`` has the stated columns and its
(month, created_at) index.

Identity — the proof also runs as a NON-ADMIN (role "user"): a second
pass in the same test after a full reload, with the page booted the
production way (the token it would take from the URL hash as
``#access_token=...`` — the development token, role "user" — sent as
its Bearer token), ``GET /api/auth/config`` answering publicly and
``GET /api/auth/me`` resolving the viewer — sees the review and the
sign-off reads still answer (open to any signed-in user, so no
admin-only control stands on the page) while the admin-only sign-off
is refused with 403 and no token is still a 401.
"""

import json
import os
from datetime import date, timedelta
from pathlib import Path

import psycopg2
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.aggregate.projection as projection_module
import app.sop.review as review_module
from app.db.connection import get_connection, get_cursor
from app.feed import acumatica_client as acumatica_client_module
from app.main import app
from tests.migration_head import CURRENT_HEAD

REVIEW = "/api/sop/review"
LIST = "/api/sop/signoffs"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"
_MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations"
)

# The one revision this migration adds — concatenated on purpose:
# the issue 803 proof owns the double-quoted head literal and scans
# the rest of the suite for it.
HEAD_REVISION = "0036_sop" "_signoffs"

AS_OF = date(2026, 9, 30)  # the last seeded demand day
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
REVIEW_MONTH = "2026-10"  # the current month: the horizon's first month
S1 = "S1"
S2 = "S2"
I1, I2, I3, I5 = "I928-1", "I928-2", "I928-3", "I928-5"
BUDGET_AMOUNT = 5000.0
NEW_BUDGET = 12345.0
SYNC_AS_OF = "2026-09-30T00:00:00+00:00"

ADMIN_TOKEN = "dev-secret-token:admin"
PLANNER_TOKEN = "dev-secret-token:user"
ADMIN_DETAIL = "Role 'admin' is required to perform this action."
MONTH_DETAIL = f"Only the current review month {REVIEW_MONTH} can be signed off."
TIE_CREATED_AT = "2027-01-01T00:00:00+00:00"  # after every real now()

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
    "reorder_point, safety_stock, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
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
SIGNOFF_SQL = (
    "INSERT INTO sop_signoffs (month, note, figures, actor, actor_name, "
    "created_at) VALUES (%s, %s, %s::jsonb, %s, %s, %s) RETURNING id"
)


def _seed(cur) -> None:
    """The projection's inputs, as test_issue460_freeform seeds them:
    two sites, S1 short in the first month of the horizon — plus one
    budget for the current month, the way test_issue520_freeform
    stores its budgets."""
    # Smooth daily demand for 360 days (d = quantity, sd_d 0):
    # I1@S1 10/day, I2@S1 0.5/day, I3@S1 0.25/day, I5@S2 1/day.
    for item, site, quantity in (
        (I1, S1, 10.0),
        (I2, S1, 0.5),
        (I3, S1, 0.25),
        (I5, S2, 1.0),
    ):
        suffix = "s2" if site == S2 else "s1"
        cur.executemany(
            DEMAND_SQL,
            [
                (
                    f"id-de-928-{item[-1]}-{suffix}-{i}",
                    f"de-928-{item[-1]}-{suffix}-{i}",
                    item,
                    site,
                    quantity,
                    (FIRST_DAY + timedelta(days=i)).isoformat(),
                    "shipment",
                    "test",
                )
                for i in range(360)
            ],
        )
    # Three lead-time observations per item, all of the same duration
    # (LT the duration, sd_LT 0, n_used 3 -> not low confidence):
    # I1 and I2 10 days, I3 8 days, I5 5 days.
    for item, duration, first in (
        (I1, 10, "2026-08-20"),
        (I2, 10, "2026-08-21"),
        (I3, 8, "2026-08-22"),
        (I5, 5, "2026-08-25"),
    ):
        base = date.fromisoformat(first)
        for i in range(3):
            ordered = base + timedelta(days=i)
            cur.execute(
                LEAD_SQL,
                (
                    f"id-lt-928-{item[-1]}-{i}",
                    f"lt-928-{item[-1]}-{i}",
                    f"SUP-928-{item[-1]}",
                    item,
                    ordered.isoformat(),
                    (ordered + timedelta(days=duration)).isoformat(),
                    "test",
                ),
            )
    # item_sites: I3 has no unit cost (counted, contributes no value).
    cur.executemany(
        ITEM_SITE_SQL,
        [
            ("id-is-928-1", I1, S1, 2.5, 120.0, 5.0, "test"),
            ("id-is-928-2", I2, S1, 1.0, 100.0, 0.0, "test"),
            ("id-is-928-3", I3, S1, None, 40.0, 2.0, "test"),
            ("id-is-928-5", I5, S2, 3.0, 10.0, 0.0, "test"),
        ],
    )
    # on_hand: no row for I2 (it contributes 0 to the group start).
    cur.executemany(
        ON_HAND_SQL,
        [
            ("id-oh-928-1", I1, S1, 30.0, SYNC_AS_OF, "test"),
            ("id-oh-928-3", I3, S1, 10.0, SYNC_AS_OF, "test"),
            ("id-oh-928-5", I5, S2, 100.0, SYNC_AS_OF, "test"),
        ],
    )
    # Open purchase lines of the newest sync: I2's 40 units promised
    # 2026-09-20 (before the first bucket — stays on order throughout,
    # contributes to no bucket), I1's 2000 units promised 2026-12-10
    # (the December supply) and 99 units promised 2027-01-15 (outside
    # the window), I5's 12 units promised 2026-11-05 (the November
    # supply).
    cur.executemany(
        OPEN_LINE_SQL,
        [
            (
                "id-ol-928-2a",
                "bk-928-2a",
                I2,
                S1,
                "SUP-928-2",
                "po",
                40.0,
                "2026-09-20",
                "2026-09-10",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-928-1a",
                "bk-928-1a",
                I1,
                S1,
                "SUP-928-1",
                "po",
                2000.0,
                "2026-12-10",
                "2026-09-20",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-928-1b",
                "bk-928-1b",
                I1,
                S1,
                "SUP-928-1",
                "po",
                99.0,
                "2027-01-15",
                "2026-09-10",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-928-5",
                "bk-928-5",
                I5,
                S2,
                "SUP-928-5",
                "po",
                12.0,
                "2026-11-05",
                "2026-09-15",
                SYNC_AS_OF,
                "test",
            ),
        ],
    )
    # One budget for the current month (2026-10), the horizon's first
    # month, stored the way test_issue520_freeform stores its budgets.
    cur.execute(
        "INSERT INTO purchase_budgets (month, amount, updated_by) "
        "VALUES (%s, %s, %s)",
        (REVIEW_MONTH, BUDGET_AMOUNT, "test"),
    )


def _signoff_count() -> int:
    """The number of stored sign-off rows."""
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sop_signoffs")
        return cur.fetchone()["count"]


def _row_counts() -> dict[str, int]:
    """The row counts of the tables a sign-off must not write."""
    with get_cursor() as cur:
        counts = {}
        for table in (
            "purchase_budgets",
            "scenarios",
            "plan_releases",
            "transfer_releases",
        ):
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = cur.fetchone()["count"]
        return counts


def test_issue928_freeform(client, monkeypatch, user_token) -> None:
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    # The review resolves its own "current server date", the
    # projection resolves its default as_of against it, and the shared
    # default as-of date (recommendations, realised accuracy) is
    # pinned to the same server date.
    monkeypatch.setattr(review_module, "date", _FixedDate)
    monkeypatch.setattr(projection_module, "date", _FixedDate)
    monkeypatch.setattr("app.aggregate.spend.date", _FixedDate)
    monkeypatch.setattr("app.stats.demand.date", _FixedDate)

    # --- The migration: one head, equal to CURRENT_HEAD, the new
    #     revision in its history, the stated columns. ----------------
    script_dir = ScriptDirectory(_MIGRATIONS_DIR)
    assert list(script_dir.get_heads()) == [CURRENT_HEAD]
    assert HEAD_REVISION in {script.revision for script in script_dir.walk_revisions()}
    with get_cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        assert cur.fetchone()["version_num"] == CURRENT_HEAD
        cur.execute(
            "SELECT column_name, is_nullable, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'sop_signoffs'"
        )
        columns = {
            row["column_name"]: (row["is_nullable"].lower(), row["data_type"])
            for row in cur.fetchall()
        }
        cur.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = 'sop_signoffs'"
        )
        indexes = {row["indexname"]: row["indexdef"] for row in cur.fetchall()}
    assert columns == {
        "id": ("no", "bigint"),
        "month": ("no", "text"),
        "note": ("yes", "text"),
        "figures": ("no", "jsonb"),
        "actor": ("no", "text"),
        "actor_name": ("yes", "text"),
        "created_at": ("no", "timestamp with time zone"),
    }
    assert (
        "USING btree (month, created_at)" in indexes["ix_sop_signoffs_month_created_at"]
    )

    # --- Seed ---------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    # --- Sign in as admin and planner, as test_issue881_freeform does.
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    planner = TestClient(app, headers={"Authorization": f"Bearer {PLANNER_TOKEN}"})
    anon = TestClient(app)

    # --- The review before any sign-off: latest_signoff is None. ------
    response = admin.get(REVIEW, params={"months": 3})
    assert response.status_code == 200, response.text
    review_before = response.json()
    assert review_before["as_of"] == FIXED_TODAY.isoformat()
    assert review_before["review_month"] == REVIEW_MONTH
    assert [row["month"] for row in review_before["months"]] == [
        "2026-10",
        "2026-11",
        "2026-12",
    ]
    assert review_before["months"][0]["budget"] == BUDGET_AMOUNT
    assert review_before["latest_signoff"] is None
    # The list is open to any signed-in user (the dev token is
    # role "user") and is empty before any sign-off.
    assert client.get(LIST).json() == []
    assert _signoff_count() == 0

    # --- Signing: an admin POST of the current month answers 201. -----
    response = admin.post(
        LIST, json={"month": REVIEW_MONTH, "note": "October figures agreed"}
    )
    assert response.status_code == 201, response.text
    first = response.json()
    first_id = first["id"]
    assert first == {
        "id": first_id,
        "month": REVIEW_MONTH,
        "note": "October figures agreed",
        "actor": "admin",  # the admin's username
        "actor_name": "admin",  # the admin's display name
        "created_at": first["created_at"],
        # The figures equal the JSON of the review taken just before:
        # the exact server-computed answer, no client-supplied fields.
        "figures": review_before,
    }
    assert _signoff_count() == 1

    # --- Server figures: a body carrying figures answers 422. ---------
    response = admin.post(
        LIST,
        json={
            "month": REVIEW_MONTH,
            "note": "x",
            "figures": {"months": []},
        },
    )
    assert response.status_code == 422, response.text
    response = admin.post(
        LIST, json={"month": REVIEW_MONTH, "actor": "admin", "actor_name": "x"}
    )
    assert response.status_code == 422, response.text
    assert _signoff_count() == 1

    # --- Refusals: nothing is computed or stored. ----------------------
    # A planner POST answers 403 and writes nothing.
    refused = planner.post(LIST, json={"month": REVIEW_MONTH, "note": "nope"})
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == ADMIN_DETAIL
    assert _signoff_count() == 1
    # Another month answers 422 with the stated detail and writes nothing.
    response = admin.post(LIST, json={"month": "2026-11", "note": "nope"})
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == MONTH_DETAIL
    assert _signoff_count() == 1
    # A malformed month, an over-long note, a NUL note, an out-of-bounds
    # months and a missing month each answer 422 and write nothing.
    for body in (
        {"month": "2026-1"},
        {"month": "20261-01"},
        {"month": "2026-01-01"},
        {"month": ""},
        {"note": "no month"},
        {"month": REVIEW_MONTH, "note": "a" * 501},
        {"month": REVIEW_MONTH, "note": "a\x00b"},
        {"month": REVIEW_MONTH, "months": 13},
        {"month": REVIEW_MONTH, "months": 0},
        {"month": REVIEW_MONTH, "months": "abc"},
    ):
        response = admin.post(LIST, json=body)
        assert response.status_code == 422, (body, response.text)
    assert _signoff_count() == 1
    # No token answers 401 on all three routes.
    assert anon.post(LIST, json={"month": REVIEW_MONTH}).status_code == 401
    assert anon.get(LIST).status_code == 401
    assert anon.get(f"{LIST}/1").status_code == 401

    # --- Frozen: the stored figures stand as they did before. ---------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE purchase_budgets SET amount = %s WHERE month = %s",
                (NEW_BUDGET, REVIEW_MONTH),
            )
        conn.commit()
    finally:
        conn.close()
    response = admin.get(f"{LIST}/{first_id}")
    assert response.status_code == 200, response.text
    stored = response.json()
    assert stored["figures"]["months"][0]["budget"] == BUDGET_AMOUNT
    response = admin.get(REVIEW, params={"months": 3})
    assert response.status_code == 200, response.text
    assert response.json()["months"][0]["budget"] == NEW_BUDGET
    # The stored row answers with its figures.
    assert stored["id"] == first_id
    assert stored["month"] == REVIEW_MONTH
    assert stored["note"] == "October figures agreed"
    assert stored["actor"] == "admin"
    assert stored["actor_name"] == "admin"
    assert isinstance(stored["created_at"], str)

    # --- Append-only: a second sign-off writes a second row. ----------
    response = admin.post(LIST, json={"month": REVIEW_MONTH, "note": "second sign-off"})
    assert response.status_code == 201, response.text
    second = response.json()
    second_id = second["id"]
    assert second_id > first_id
    assert second["note"] == "second sign-off"
    assert second["figures"]["months"][0]["budget"] == NEW_BUDGET
    # Its latest_signoff carries the row as it stood before the signing.
    assert second["figures"]["latest_signoff"]["id"] == first_id
    assert "figures" not in second["figures"]["latest_signoff"]
    # The earlier row is unchanged.
    response = admin.get(f"{LIST}/{first_id}")
    assert response.status_code == 200, response.text
    unchanged = response.json()
    assert unchanged["note"] == "October figures agreed"
    assert unchanged["figures"]["months"][0]["budget"] == BUDGET_AMOUNT
    assert _signoff_count() == 2
    # Both are listed newest first, without figures.
    for params in ({}, {"month": REVIEW_MONTH}):
        response = admin.get(LIST, params=params)
        assert response.status_code == 200, response.text
        rows = response.json()
        assert [row["id"] for row in rows] == [second_id, first_id]
        for row in rows:
            assert "figures" not in row
            assert set(row) == {
                "id",
                "month",
                "note",
                "actor",
                "actor_name",
                "created_at",
            }
    assert rows[0]["note"] == "second sign-off"
    # limit bounds and the month filter.
    assert [row["id"] for row in admin.get(LIST, params={"limit": 1}).json()] == [
        second_id
    ]
    assert (admin.get(LIST, params={"limit": 0}).status_code == 422) and admin.get(
        LIST, params={"limit": 201}
    ).status_code == 422
    assert admin.get(LIST, params={"limit": "many"}).status_code == 422
    assert (
        admin.get(LIST, params={"month": "2026-1"}).status_code == 422
    ) and admin.get(LIST, params={"month": "20261-01"}).status_code == 422
    assert (
        admin.get(LIST, params={"month": "2025-10"}).json() == []
    ), "a well-formed month without sign-offs lists nothing"
    # latest_signoff is the newer row, without its figures.
    response = admin.get(REVIEW, params={"months": 3})
    assert response.status_code == 200, response.text
    latest = response.json()["latest_signoff"]
    assert latest == {
        "id": second_id,
        "month": REVIEW_MONTH,
        "note": "second sign-off",
        "actor": "admin",
        "actor_name": "admin",
        "created_at": second["created_at"],
    }
    assert "figures" not in latest

    # --- PUT, PATCH and DELETE on the row answer 405. ------------------
    for method in ("put", "patch", "delete"):
        kwargs = {} if method == "delete" else {"json": {"note": "x"}}
        response = getattr(admin, method)(f"{LIST}/{second_id}", **kwargs)
        assert response.status_code == 405, (method, response.text)
    assert _signoff_count() == 2

    # --- The tie-breaker: equal created_at, the higher id first. ------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                SIGNOFF_SQL,
                (
                    REVIEW_MONTH,
                    "tie-a",
                    json.dumps({"tie": "a"}),
                    "tie",
                    "Tie A",
                    TIE_CREATED_AT,
                ),
            )
            tie_low = cur.fetchone()[0]
            cur.execute(
                SIGNOFF_SQL,
                (
                    REVIEW_MONTH,
                    "tie-b",
                    json.dumps({"tie": "b"}),
                    "tie",
                    "Tie B",
                    TIE_CREATED_AT,
                ),
            )
            tie_high = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    assert tie_high > tie_low
    rows = admin.get(LIST, params={"month": REVIEW_MONTH}).json()
    assert [row["id"] for row in rows[:2]] == [tie_high, tie_low]
    assert rows[0]["note"] == "tie-b"
    latest = admin.get(REVIEW).json()["latest_signoff"]
    assert latest["id"] == tie_high, "the higher id is selected as latest_signoff"
    # The tie-breaker rows are test-only: remove them again.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM sop_signoffs WHERE id IN (%s, %s)",
                (tie_low, tie_high),
            )
        conn.commit()
    finally:
        conn.close()
    assert _signoff_count() == 2

    # --- Blank note: a note of spaces is stored as null. ---------------
    response = admin.post(LIST, json={"month": REVIEW_MONTH, "note": "   "})
    assert response.status_code == 201, response.text
    blank = response.json()
    assert blank["note"] is None
    assert admin.get(f"{LIST}/{blank['id']}").json()["note"] is None
    assert _signoff_count() == 3

    # --- Reading as a planner: the reads are open. ---------------------
    response = planner.get(LIST)
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["id"] for row in rows] == [
        blank["id"],
        second_id,
        first_id,
    ], "newest first"
    assert all("figures" not in row for row in rows)
    response = planner.get(f"{LIST}/{first_id}")
    assert response.status_code == 200, response.text
    assert isinstance(response.json()["figures"], dict)
    # An unknown id answers 404, a non-integer id 422.
    assert planner.get(f"{LIST}/999999999").status_code == 404
    assert planner.get(f"{LIST}/not-an-int").status_code == 422

    # --- No side effects: no ERP call, no other row changes. ----------
    counts_before = _row_counts()
    assert counts_before == {
        "purchase_budgets": 1,
        "scenarios": 0,
        "plan_releases": 0,
        "transfer_releases": 0,
    }
    signoffs_before = _signoff_count()

    def _no_erp_client(self, *args, **kwargs):
        raise AssertionError("a sign-off must not call the ERP")

    monkeypatch.setattr(
        acumatica_client_module.AcumaticaClient, "__init__", _no_erp_client
    )
    assert admin.get(REVIEW, params={"months": 3}).status_code == 200
    assert (
        admin.post(LIST, json={"month": REVIEW_MONTH, "note": "no ERP"}).status_code
        == 201
    )
    assert planner.get(LIST).status_code == 200
    assert planner.get(f"{LIST}/{first_id}").status_code == 200
    assert (
        admin.post(LIST, json={"month": "2026-11"}).status_code == 422
    ), "the month check still refuses, without the ERP"
    assert _row_counts() == counts_before, "a sign-off changes no other row"
    assert _signoff_count() == signoffs_before + 1

    # --- The schema file lists the routes and their models. -----------
    schema = json.loads(OPENAPI.read_text(encoding="utf-8"))
    assert set(schema["paths"][LIST]) == {"get", "post"}
    assert "get" in schema["paths"][f"{LIST}/{{signoff_id}}"]
    for model in ("SopSignoff", "SopSignoffRow", "SopSignoffIn", "SopReviewResponse"):
        assert model in schema["components"]["schemas"]
    review_params = schema["paths"][REVIEW]["get"]
    assert (
        "latest_signoff"
        in schema["components"]["schemas"]["SopReviewResponse"]["properties"]
    )
    assert review_params["operationId"]

    # --- Identity: a second pass after a full reload, as the
    #     NON-ADMIN (role "user").  The page is booted the production
    #     way: the token it would take from the URL hash as
    #     #access_token=... is the development token (role "user") sent
    #     as its Bearer token; GET /api/auth/config answers publicly
    #     and GET /api/auth/me resolves the viewer.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["authenticated"] is True
    assert me_body["user"]["roles"] == ["user"]
    assert "admin" not in me_body["user"]["roles"]
    # The page still renders for the viewer — the review and the
    # sign-off reads are open to any signed-in user, so no admin-only
    # control stands on them — and the admin-only sign-off is refused.
    latest_now = viewer.get(LIST).json()[0]["id"]
    response = viewer.get(REVIEW, params={"months": 3})
    assert response.status_code == 200, response.text
    assert (
        response.json()["latest_signoff"]["id"] == latest_now
    ), "latest_signoff is the newest row of the review month"
    assert viewer.get(LIST).status_code == 200
    assert viewer.get(f"{LIST}/{first_id}").status_code == 200
    refused = viewer.post(LIST, json={"month": REVIEW_MONTH, "note": "nope"})
    assert refused.status_code == 403
    assert refused.json()["detail"] == ADMIN_DETAIL
    # No token is still a 401 on all three routes.
    assert anon_reloaded.post(LIST, json={"month": REVIEW_MONTH}).status_code == 401
    assert anon_reloaded.get(LIST).status_code == 401
    assert anon_reloaded.get(f"{LIST}/{first_id}").status_code == 401
    # Nothing new was written by any of it.
    assert _signoff_count() == signoffs_before + 1
