"""Issue #975 proof: the demand-signal units of the S&OP review.

The proof seeds the projection's inputs the way
``test_issue927_freeform`` does (importing its seed and constants)
and signs in as the non-admin reader through the shared ``client``
fixture and as admin through a development ``:admin`` token, the way
``test_issue935_freeform`` signs its clients in — and stores a signal
through ``POST /api/demand-signals``.

AC-1 — ``GET /api/sop/review``'s ``months`` entries carry
``signal_units``, the sum over all site groups of the month's
projection bucket ``signal_units`` from the one projection call (no
new query reads signals here), typed and serialized as a float —
``0.0`` with no bucket, the fractional day-share totals kept whole
without integer truncation — and the ``since_signoff.months``
entries carry ``signal_units_delta`` by the same rule as the other
deltas, so a stored row without the field answers null, never an
error.  ``backend/openapi.json`` documents both fields as numbers.

AC-3 — against the test database (date.today pinned to 2026-10-02,
so the horizon is 2026-10, 2026-11 and 2026-12):

  * a signal of 300 units over the whole of the third horizon month
    (2026-12) answers ``signal_units`` 300 (within float rounding)
    for that month and 0 for the others, equal to the sum of that
    month's bucket ``signal_units`` of
    ``GET /api/aggregate/projection?months=3`` over the same data;
  * a sign-off stored first, then the signal, gives
    ``signal_units_delta`` 300 in that month — the current 300 minus
    the stored 0;
  * the sign-off's ``figures`` carry the month's ``signal_units`` as
    they stood when it was signed (0, before the signal);
  * a stored row without ``signal_units``, inserted directly the way
    ``test_issue935_freeform`` does, answers ``signal_units_delta``
    null with status 200;
  * the review stays open to the non-admin (role "user") reader.
"""

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.aggregate.projection as projection_module
import app.sop.review as review_module
from app.db.connection import get_connection
from app.main import app
from tests.test_issue927_freeform import (
    FIXED_TODAY,
    I1,
    S1,
    _seed_projection,
    _seed_snapshot,
)
from tests.test_issue935_freeform import _insert_signoff

ADMIN_TOKEN = "dev-secret-token:admin"
REVIEW = "/api/sop/review"
PROJECTION = "/api/aggregate/projection"
SIGNALS = "/api/demand-signals"
SIGNOFFS = "/api/sop/signoffs"
MONTHS_3 = ["2026-10", "2026-11", "2026-12"]
THIRD_MONTH = "2026-12"
REVIEW_MONTH = "2026-10"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"
# A created_at later than the stored sign-off's real timestamp: the
# directly inserted row is the newest row overall when inserted.
OLD_SHAPE_CREATED_AT = "2027-01-01T00:00:00+00:00"


def test_issue975_freeform(client, monkeypatch) -> None:
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    # The review resolves its own "current server date", the
    # projection resolves its default as_of against it, and the shared
    # default as-of date (recommendations, realised accuracy, the
    # signal window's bounds) is pinned to the same server date.
    monkeypatch.setattr(review_module, "date", _FixedDate)
    monkeypatch.setattr(projection_module, "date", _FixedDate)
    monkeypatch.setattr("app.aggregate.spend.date", _FixedDate)
    monkeypatch.setattr("app.stats.demand.date", _FixedDate)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    def _review() -> dict:
        # The shared client is the non-admin (role "user") reader: the
        # review stays open to it.
        response = client.get(REVIEW, params={"months": 3})
        assert response.status_code == 200, response.text
        return response.json()

    def _signal_units_of(body: dict) -> dict[str, float]:
        return {row["month"]: row["signal_units"] for row in body["months"]}

    # --- No data: every month still answers signal_units — 0.0, a
    #     float, not an integer and not missing. -----------------------
    body = _review()
    assert [row["month"] for row in body["months"]] == MONTHS_3
    for row in body["months"]:
        assert row["signal_units"] == 0
        assert isinstance(row["signal_units"], float), row

    # --- Seed, as test_issue927_freeform does. -------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed_projection(cur)
            _seed_snapshot(cur)
        conn.commit()
    finally:
        conn.close()

    # --- The month: a 300-unit signal over the whole of the third
    #     horizon month.  The sign-off stands first, so its stored
    #     figures carry the signal units as they stood at signing. ----
    response = admin.post(SIGNOFFS, json={"month": REVIEW_MONTH, "note": "baseline"})
    assert response.status_code == 201, response.text
    signoff = response.json()

    body = _review()
    for month, value in _signal_units_of(body).items():
        assert value == 0, month
        assert isinstance(value, float), month

    response = admin.post(
        SIGNALS,
        json={
            "kind": "promotion",
            "item_code": I1,
            "site_code": S1,
            "start_date": "2026-12-01",
            "end_date": "2026-12-31",
            "quantity": 300,
            "reason": "issue975 proof: a December promotion",
        },
    )
    assert response.status_code == 201, response.text

    body = _review()
    by_month = _signal_units_of(body)
    # The whole third horizon month: 300 units, spread over its days;
    # the other months carry no share of it.
    assert by_month[THIRD_MONTH] == pytest.approx(300.0)
    assert isinstance(by_month[THIRD_MONTH], float)
    for month in MONTHS_3[:2]:
        assert by_month[month] == 0, month

    # The value equals the sum of that month's bucket signal_units of
    # the aggregate projection over the same data.
    response = client.get(PROJECTION, params={"months": 3})
    assert response.status_code == 200, response.text
    projected = 0.0
    for group in response.json():
        for bucket in group["buckets"]:
            if bucket["month"] == THIRD_MONTH:
                projected += bucket["signal_units"]
    assert by_month[THIRD_MONTH] == projected

    # --- The delta: the stored 0, then the signal — current 300 minus
    #     the stored 0. --------------------------------------------------
    since = body["since_signoff"]
    assert since is not None
    assert since["signoff_id"] == signoff["id"]
    since_by_month = {entry["month"]: entry for entry in since["months"]}
    assert since_by_month[THIRD_MONTH]["signal_units_delta"] == pytest.approx(300.0)
    for month in MONTHS_3[:2]:
        assert since_by_month[month]["signal_units_delta"] == 0, month

    # --- Frozen: the sign-off's figures carry the month's signal_units
    #     as they stood when it was signed — 0, before the signal. ----
    response = client.get(f"{SIGNOFFS}/{signoff['id']}")
    assert response.status_code == 200, response.text
    figures_by_month = {
        row["month"]: row for row in response.json()["figures"]["months"]
    }
    assert figures_by_month[THIRD_MONTH]["signal_units"] == 0
    for month in MONTHS_3[:2]:
        assert figures_by_month[month]["signal_units"] == 0, month

    # --- Older shape: a stored row without signal_units answers null,
    #     never an error — inserted directly the way
    #     test_issue935_freeform inserts its old-shape rows. ---------
    old_id = _insert_signoff(
        REVIEW_MONTH,
        {
            "as_of": "2026-10-02",
            "months": [
                {"month": month, "demand_units": 100, "demand_value": 10.5}
                for month in MONTHS_3
            ],
        },
        "old",
        "Old Shape A",
        OLD_SHAPE_CREATED_AT,
    )
    body = _review()
    assert body["since_signoff"]["signoff_id"] == old_id
    for entry in body["since_signoff"]["months"]:
        assert entry["signal_units_delta"] is None, entry["month"]

    # --- The schema: both fields documented as numbers. ---------------
    spec = json.loads(OPENAPI.read_text())
    schemas = spec["components"]["schemas"]
    assert schemas["SopReviewMonth"]["properties"]["signal_units"]["type"] == ("number")
    # The delta is nullable: a number or null, never an integer.
    assert schemas["SopReviewSinceSignoffMonth"]["properties"][
        "signal_units_delta"
    ] == {
        "anyOf": [{"type": "number"}, {"type": "null"}],
        "title": "Signal Units Delta",
    }
