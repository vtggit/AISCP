"""Issue #935 proof: the drift of the S&OP review since the newest
stored sign-off.

The proof seeds the projection's inputs the way
``test_issue928_freeform`` does (importing its seed, its direct
sign-off insert statement and its constants) and signs in as admin
and planner the way ``test_issue881_freeform`` does — development-mode
tokens ``dev-secret-token:admin`` and ``dev-secret-token:user``.

AC-1 — ``GET /api/sop/review`` answers ``since_signoff``, built in
``app.sop.review`` from the newest stored sign-off of ANY month (the
``sop_signoffs`` row first under ``created_at DESC, id DESC`` — the
same order ``list_newest`` answers — read with its figures by the new
repository read ``latest_with_figures``): its ``signoff_id``,
``signed_month`` and ``signed_at`` are the row's ``id``, ``month`` and
``created_at``, its ``signed_by`` the row's ``actor_name`` (its
``actor`` when that is missing or blank) and its ``signed_as_of`` the
row's figures' ``as_of`` (null when absent); its ``months`` hold one
entry per month that appears in both the review's ``months`` and the
stored figures' ``months`` (in the review's order, a month in only one
of them left out), each with the current minus stored value of
``demand_units``, ``demand_value``, ``supply_value``,
``planned_receipts_value``, ``total_spend`` and ``budget`` — a delta
is null when either value is null, missing or not a finite number,
and a stored ``figures`` without a usable ``months`` (null or not an
object included) answers an empty ``months`` with ``signed_as_of``
null.  The field is null without any sign-off, the newest row of any
month wins, and a second sign-off's stored figures carry
``since_signoff`` holding only the deltas — never a key named
``figures``.  The review route still writes nothing (the
``sop_signoffs`` row count is unchanged) and makes no ERP call (an
Acumatica client class replaced by one that raises on construction is
never reached), every other field and the sign-off routes are
unchanged, and no token is a 401.
"""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

import app.aggregate.projection as projection_module
import app.sop.review as review_module
from app.db.connection import get_connection
from app.feed import acumatica_client as acumatica_client_module
from app.main import app
from tests.test_issue928_freeform import (
    ADMIN_TOKEN,
    BUDGET_AMOUNT,
    FIXED_TODAY,
    I1,
    LIST,
    OPEN_LINE_SQL,
    PLANNER_TOKEN,
    REVIEW,
    REVIEW_MONTH,
    S1,
    SIGNOFF_SQL,
    SYNC_AS_OF,
    _seed,
    _signoff_count,
)

# The six delta fields of one since_signoff month entry, beside the
# review month field each one diffs.
DELTA_KEYS = (
    "demand_units_delta",
    "demand_value_delta",
    "supply_value_delta",
    "planned_receipts_value_delta",
    "total_spend_delta",
    "budget_delta",
)
FIELDS = (
    "demand_units",
    "demand_value",
    "supply_value",
    "planned_receipts_value",
    "total_spend",
    "budget",
)
MONTHS_3 = ["2026-10", "2026-11", "2026-12"]
# Future created_at stamps, each later than the one before: every
# directly inserted row becomes the newest row overall when inserted.
PREV_MONTH_CREATED_AT = "2027-01-01T00:00:00+00:00"
OLD_SHAPE_CREATED_AT = [
    "2027-01-01T00:00:01+00:00",
    "2027-01-01T00:00:02+00:00",
    "2027-01-01T00:00:03+00:00",
    "2027-01-01T00:00:04+00:00",
    "2027-01-01T00:00:05+00:00",
    "2027-01-01T00:00:06+00:00",
]


def _insert_signoff(
    month: str,
    figures: object,
    actor: str,
    actor_name: str | None,
    created_at: str,
    note: str = "direct",
) -> int:
    """One sign-off row inserted directly, bypassing the sign-off
    route — the way an older review shape would have been stored."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                SIGNOFF_SQL,
                (month, note, json.dumps(figures), actor, actor_name, created_at),
            )
            row_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return row_id


def test_issue935_freeform(client, monkeypatch) -> None:
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

    # --- Seed, as test_issue928_freeform does. -------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    planner = TestClient(app, headers={"Authorization": f"Bearer {PLANNER_TOKEN}"})
    anon = TestClient(app)

    # --- No sign-off: since_signoff is null. ---------------------------
    response = client.get(REVIEW, params={"months": 3})
    assert response.status_code == 200, response.text
    assert response.json()["since_signoff"] is None
    assert _signoff_count() == 0

    def _review(months: int = 3) -> dict:
        response = admin.get(REVIEW, params={"months": months})
        assert response.status_code == 200, response.text
        return response.json()

    # --- No change: straight after a sign-off, every delta is 0. ------
    response = admin.post(LIST, json={"month": REVIEW_MONTH, "note": "baseline"})
    assert response.status_code == 201, response.text
    first = response.json()
    assert _signoff_count() == 1
    review = _review()
    since = review["since_signoff"]
    assert since is not None
    assert since["signoff_id"] == first["id"]
    assert since["signed_month"] == REVIEW_MONTH
    assert since["signed_at"] == first["created_at"]
    assert since["signed_by"] == "admin", "the signer's actor_name"
    assert since["signed_as_of"] == FIXED_TODAY.isoformat()
    assert [m["month"] for m in since["months"]] == MONTHS_3
    # Nothing changed since the signing, so every delta is 0 — or null
    # where either value is not a finite number (only the first month
    # has a budget, so its budget_delta is the only defined one there).
    stored = admin.get(f"{LIST}/{first['id']}").json()["figures"]
    stored_by_month = {m["month"]: m for m in stored["months"]}
    current_by_month = {m["month"]: m for m in review["months"]}
    for entry in since["months"]:
        assert set(entry) == {"month", *DELTA_KEYS, "signal_units_delta"}
        for key, field in zip(DELTA_KEYS, FIELDS):
            label = entry["month"]
            if (
                current_by_month[label][field] is None
                or stored_by_month[label][field] is None
            ):
                assert entry[key] is None, (label, key)
            else:
                assert entry[key] == 0, (label, key)

    # --- After a change: the first month's budget rose by 100. --------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE purchase_budgets SET amount = %s WHERE month = %s",
                (BUDGET_AMOUNT + 100.0, REVIEW_MONTH),
            )
        conn.commit()
    finally:
        conn.close()
    since = _review()["since_signoff"]
    assert since["months"][0]["budget_delta"] == 100
    assert since["months"][1]["budget_delta"] is None, "no budget in either"

    # --- After a change: open supply the test computes itself from the
    #     two answers — the current review and the stored figures. ----
    stored = admin.get(f"{LIST}/{first['id']}").json()["figures"]
    stored_by_month = {m["month"]: m for m in stored["months"]}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                OPEN_LINE_SQL,
                (
                    "id-ol-935-1",
                    "bk-935-1",
                    I1,
                    S1,
                    "SUP-928-1",
                    "po",
                    500.0,
                    "2026-11-15",
                    "2026-09-21",
                    SYNC_AS_OF,
                    "test",
                ),
            )
        conn.commit()
    finally:
        conn.close()
    after = _review()
    after_by_month = {m["month"]: m for m in after["months"]}
    since = after["since_signoff"]
    assert [m["month"] for m in since["months"]] == MONTHS_3
    for entry in since["months"]:
        label = entry["month"]
        assert entry["supply_value_delta"] == (
            after_by_month[label]["supply_value"]
            - stored_by_month[label]["supply_value"]
        ), label
        assert entry["total_spend_delta"] == (
            after_by_month[label]["total_spend"] - stored_by_month[label]["total_spend"]
        ), label
    assert since["months"][1]["supply_value_delta"] == pytest.approx(
        1250.0
    ), "500 units at 2.5, promised into 2026-11"

    # --- The newest row wins — and the stored figures stay flat. ------
    response = admin.post(LIST, json={"month": REVIEW_MONTH, "note": "second"})
    assert response.status_code == 201, response.text
    second = response.json()
    assert second["id"] > first["id"]
    assert _review()["since_signoff"]["signoff_id"] == second["id"]
    # The second sign-off's stored figures carry since_signoff of the
    # row as it stood before the signing — and that value holds only
    # the deltas, never a key named "figures".
    nested = second["figures"]["since_signoff"]
    assert nested is not None
    assert nested["signoff_id"] == first["id"]
    assert "figures" not in nested
    for entry in nested["months"]:
        assert "figures" not in entry
    assert _signoff_count() == 2

    # --- Overlap only: a sign-off of months 1, a review of months 3. --
    response = admin.post(
        LIST, json={"month": REVIEW_MONTH, "months": 1, "note": "one month"}
    )
    assert response.status_code == 201, response.text
    third = response.json()
    assert [m["month"] for m in third["figures"]["months"]] == [REVIEW_MONTH]
    since = _review()["since_signoff"]
    assert since["signoff_id"] == third["id"]
    assert [m["month"] for m in since["months"]] == [
        REVIEW_MONTH
    ], "the sign-off's one month, in the review's order"

    # --- Another month's row: only the overlap with the review. ------
    prev_id = _insert_signoff(
        "2026-09",
        {
            "as_of": "2026-09-02",
            "months": [
                {"month": "2026-09", "demand_units": 7, "budget": 1000.0},
                {"month": REVIEW_MONTH, "budget": 4000.0},
            ],
        },
        "other",
        "Other Planner",
        PREV_MONTH_CREATED_AT,
    )
    since = _review()["since_signoff"]
    assert since["signoff_id"] == prev_id
    assert since["signed_month"] == "2026-09"
    assert since["signed_by"] == "Other Planner"
    assert since["signed_as_of"] == "2026-09-02"
    assert [m["month"] for m in since["months"]] == [
        REVIEW_MONTH
    ], "2026-09 is in the stored figures only, so it is left out"
    entry = since["months"][0]
    assert entry["budget_delta"] == (BUDGET_AMOUNT + 100.0) - 4000.0
    for key in DELTA_KEYS:
        if key != "budget_delta":
            assert entry[key] is None, key

    # --- Older shapes: rows of earlier review shapes answer, never
    #     error.  Each inserted row is the newest one overall. --------
    # (a) a month without budget: its budget_delta is null.
    old_id = _insert_signoff(
        REVIEW_MONTH,
        {
            "as_of": "2026-10-02",
            "months": [
                {
                    "month": REVIEW_MONTH,
                    "demand_units": 100,
                    "demand_value": 10.5,
                    "supply_value": 1.5,
                    "planned_receipts_value": 2.5,
                    "total_spend": 3.5,
                }
            ],
        },
        "old",
        "Old Shape A",
        OLD_SHAPE_CREATED_AT[0],
    )
    body = _review()
    since = body["since_signoff"]
    assert since["signoff_id"] == old_id
    assert [m["month"] for m in since["months"]] == [REVIEW_MONTH]
    entry = since["months"][0]
    assert entry["budget_delta"] is None, "no budget in the stored month"
    current = body["months"][0]
    assert entry["demand_units_delta"] == current["demand_units"] - 100
    assert entry["demand_value_delta"] == current["demand_value"] - 10.5
    assert entry["supply_value_delta"] == current["supply_value"] - 1.5
    assert entry["planned_receipts_value_delta"] == (
        current["planned_receipts_value"] - 2.5
    )
    assert entry["total_spend_delta"] == current["total_spend"] - 3.5
    # (b) a string where a number belongs: that delta is null.
    old_id = _insert_signoff(
        REVIEW_MONTH,
        {
            "as_of": "2026-10-02",
            "months": [
                {
                    "month": REVIEW_MONTH,
                    "demand_units": "a lot",
                    "budget": BUDGET_AMOUNT + 100.0,
                }
            ],
        },
        "old",
        "Old Shape B",
        OLD_SHAPE_CREATED_AT[1],
    )
    body = _review()
    since = body["since_signoff"]
    assert since["signoff_id"] == old_id
    entry = since["months"][0]
    assert entry["demand_units_delta"] is None, "a string is not a finite number"
    assert entry["budget_delta"] == 0.0
    # (c) no months at all: an empty months.
    old_id = _insert_signoff(
        REVIEW_MONTH,
        {"as_of": "2026-10-02"},
        "old",
        "Old Shape C",
        OLD_SHAPE_CREATED_AT[2],
    )
    body = _review()
    since = body["since_signoff"]
    assert since["signoff_id"] == old_id
    assert since["months"] == []
    # (d) no as_of: signed_as_of is null; an empty actor_name falls
    #     back to the actor.
    old_id = _insert_signoff(
        REVIEW_MONTH,
        {"months": [{"month": REVIEW_MONTH, "budget": 5000.0}]},
        "fallback-actor",
        "",
        OLD_SHAPE_CREATED_AT[3],
    )
    body = _review()
    since = body["since_signoff"]
    assert since["signoff_id"] == old_id
    assert since["signed_as_of"] is None, "no as_of in the stored figures"
    assert (
        since["signed_by"] == "fallback-actor"
    ), "an empty actor_name falls back to the actor"
    assert since["months"][0]["budget_delta"] == 100.0
    # (e) figures null: an object with months [] and signed_as_of null.
    old_id = _insert_signoff(
        REVIEW_MONTH, None, "old", "Old Shape E", OLD_SHAPE_CREATED_AT[4]
    )
    body = _review()
    since = body["since_signoff"]
    assert since["signoff_id"] == old_id
    assert since["months"] == []
    assert since["signed_as_of"] is None
    # (f) figures not an object: the same answer.
    old_id = _insert_signoff(
        REVIEW_MONTH,
        [REVIEW_MONTH, 1],
        "old",
        "Old Shape F",
        OLD_SHAPE_CREATED_AT[5],
    )
    body = _review()
    since = body["since_signoff"]
    assert since["signoff_id"] == old_id
    assert since["months"] == []
    assert since["signed_as_of"] is None

    # --- Read-only: a review writes nothing and calls no ERP. --------
    signoffs_before = _signoff_count()

    def _no_erp_client(self, *args, **kwargs):
        raise AssertionError("a review must not call the ERP")

    monkeypatch.setattr(
        acumatica_client_module.AcumaticaClient, "__init__", _no_erp_client
    )
    assert admin.get(REVIEW, params={"months": 3}).status_code == 200
    assert planner.get(REVIEW, params={"months": 1}).status_code == 200
    assert _signoff_count() == signoffs_before, "the review writes no row"

    # --- Unauthenticated: no token is a 401. ---------------------------
    assert anon.get(REVIEW, params={"months": 3}).status_code == 401
