"""Issue #1459 proof: the planned DDMRP adjustment-factor proposals.

* the setting — ``ddmrp.adjustment_proposal_threshold`` is registered
  system-scoped, a number defaulting to 0.25 with bounds 0.05 to 5,
  with the contract's description, listed directly before
  ``ddmrp.adu_window_days`` in ``GET /api/settings``;
* the proposals — ``app.api.ddmrp.adjustment_proposals`` (beside
  ``ddmrp_zone_rows`` in the route module, so no import cycle arises
  with the route) reads the buffered item-sites' zone rows once, the
  forecast rows (``app.forecast.listing.forecast_rows``, imported by
  name) once at the same as-of and the stored adjustments once: with
  the two stand-ins in place — ``app.api.ddmrp.ddmrp_zone_rows``
  answering two buffered item-sites, A with status "ok" and
  ``adu_base`` 10, and B with status "no_demand", and
  ``app.api.ddmrp.forecast_rows`` answering A's forecast with three
  30-day periods whose ``effective_quantity`` is 300, 450 and 210 —
  the default answer proposes A's second period (``forecast_daily``
  15, factor 1.5) and A's third (``forecast_daily`` 7, factor 0.7),
  in that order, with null ``existing_factor`` — the first period
  (factor 1.0, inside the 0.25 share) is not proposed and neither is
  B; with the threshold stored as 0.4 (an admin ``PUT
  /api/settings/ddmrp.adjustment_proposal_threshold``, removed
  afterwards with the reset), only the second period is proposed;
  a stored adjustment of A covering the second period's start gives
  it its factor while an all-buffer adjustment covering the third
  period's start gives it its factor (the pair's own adjustment
  wins where both scopes cover the date);
* the route — ``GET /api/ddmrp/adjustments/proposals`` answers 422
  for ``?periods=0`` and ``?periods=13`` (and for non-numeric, empty
  and huge values), 401 without a token, 200 for the ``user`` role,
  and its answer equals ``adjustment_proposals(3)``;
* nothing written — ``ddmrp_adjustments`` has the same rows before
  and after the proposals are computed and served;
* the schema — ``backend/openapi.json`` carries the path with its
  ``periods`` query parameter (integer, 1 to 12, default 3), the
  200 response on the ``DdmrpAdjustmentProposalsResponse`` envelope
  and the ``DdmrpAdjustmentProposal`` row model with exactly the
  contract's fields, and the module docstring describes the route;
* the identity requirement: the proof also runs as a NON-ADMIN (role
  ``user``) — a second pass in the same test after a full reload,
  with the page booted the production way (the token it would take
  from the URL hash as ``#access_token=...`` — the development
  token, role "user" — sent as its Bearer token), ``GET
  /api/auth/config`` answering publicly and ``GET /api/auth/me``
  resolving the viewer, sees the admin-only controls (the
  ``POST``/``DELETE`` of ``/api/ddmrp/adjustments``) absent for that
  caller (403) while the page still renders (200), and no token is
  still a 401.
"""

import json
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.ddmrp as ddmrp_api
from app.db.connection import get_cursor
from app.main import app

_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"
PROPOSALS_URL = "/api/ddmrp/adjustments/proposals"
ADJUSTMENTS_URL = "/api/ddmrp/adjustments"
SETTING_KEY = "ddmrp.adjustment_proposal_threshold"
SETTING_URL = f"/api/settings/{SETTING_KEY}"

ADMIN_TOKEN = "dev-secret-token:admin"
USER_TOKEN = "dev-secret-token:user"
VIEWER_TOKEN = "dev-secret-token"  # the exact development token: role "user"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

AS_OF = date(2026, 10, 1)
ITEM_A = "ITEM-1459A"  # buffered, status "ok", adu_base 10
SITE_A = "SITE-1459-1"
ITEM_B = "ITEM-1459B"  # buffered, status "no_demand"
SITE_B = "SITE-1459-2"

# The three 30-day forecast periods of A: the first starts the day
# after the as-of date.
P1_START = AS_OF + timedelta(days=1)
P1_END = AS_OF + timedelta(days=30)
P2_START = AS_OF + timedelta(days=31)
P2_END = AS_OF + timedelta(days=60)
P3_START = AS_OF + timedelta(days=61)
P3_END = AS_OF + timedelta(days=90)

# A's own stored adjustment: covers the second period's start (and
# only it); factor 1.25.
OWN_START = P2_START
OWN_END = P2_END
OWN_FACTOR = 1.25
# The all-buffer stored adjustment: covers both the second and the
# third period's start; factor 0.8.  Where both scopes cover a date
# (the second period's start), the pair's own adjustment wins.
ALL_START = P2_START
ALL_END = P3_END
ALL_FACTOR = 0.8

EXPECTED_P2 = {
    "item_code": ITEM_A,
    "site_code": SITE_A,
    "start_on": P2_START,
    "end_on": P2_END,
    "adu_base": 10.0,
    "forecast_daily": 15.0,
    "factor": 1.5,
    "existing_factor": None,
}
EXPECTED_P3 = {
    "item_code": ITEM_A,
    "site_code": SITE_A,
    "start_on": P3_START,
    "end_on": P3_END,
    "adu_base": 10.0,
    "forecast_daily": 7.0,
    "factor": 0.7,
    "existing_factor": None,
}


def _jsonable(value):
    """The value as the route would serialize it (dates as ISO text)."""
    return json.loads(json.dumps(value, default=str))


def _adjustment_rows():
    with get_cursor() as cur:
        cur.execute("SELECT * FROM ddmrp_adjustments ORDER BY start_on, id")
        return [dict(row) for row in cur.fetchall()]


def test_issue1459_freeform(client, admin_token, user_token, monkeypatch):
    # -- 1. The setting is registered with the contract's shape. -----
    resp = client.get("/api/settings")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    entry = next(e for e in body if e["key"] == SETTING_KEY)
    assert entry["scope"] == "system"
    assert entry["type"] == "number"
    assert entry["default"] == 0.25
    assert entry["min"] == 0.05
    assert entry["max"] == 5
    assert entry["description"] == (
        "How far a period's forecast daily demand must be from a "
        "buffer's average daily usage, as a share (0.25 = 25%), "
        "before a planned adjustment factor is proposed for that "
        "period."
    )
    # Directly before ddmrp.adu_window_days in the key list.
    keys = [e["key"] for e in body]
    assert keys.index(SETTING_KEY) + 1 == keys.index("ddmrp.adu_window_days")

    # A system setting is not editable for a signed-in non-admin.
    resp = client.put(SETTING_URL, json={"value": 0.4})
    assert resp.status_code == 403, resp.text

    # -- 2. The two stand-ins. ---------------------------------------
    zone_calls: list[date | None] = []
    forecast_calls: list[date | None] = []
    list_all_calls: list[None] = []

    def fake_zone_rows(as_of: date | None = None):
        zone_calls.append(as_of)
        return AS_OF, [
            {
                "item_code": ITEM_A,
                "site_code": SITE_A,
                "status": "ok",
                "adu_base": 10.0,
                "adjustment_factor": 1.0,
                "adu": 10.0,
            },
            {
                "item_code": ITEM_B,
                "site_code": SITE_B,
                "status": "no_demand",
                "adu_base": 0.0,
                "adjustment_factor": 1.0,
                "adu": 0.0,
            },
        ]

    def fake_forecast_rows(
        as_of: date | None = None,
        demand_class: str | None = None,
        *,
        include_history_mean: bool = False,
    ):
        forecast_calls.append(as_of)
        return [
            {
                "item_code": ITEM_A,
                "site_code": SITE_A,
                "as_of": AS_OF,
                "demand_class": "smooth",
                "total_forecast": 960.0,
                "forecast": [
                    {
                        "bucket_start": P1_START,
                        "bucket_end": P1_END,
                        "quantity": 300.0,
                        "effective_quantity": 300.0,
                        "override_days": 0,
                    },
                    {
                        "bucket_start": P2_START,
                        "bucket_end": P2_END,
                        "quantity": 450.0,
                        "effective_quantity": 450.0,
                        "override_days": 0,
                    },
                    {
                        "bucket_start": P3_START,
                        "bucket_end": P3_END,
                        "quantity": 210.0,
                        "effective_quantity": 210.0,
                        "override_days": 0,
                    },
                ],
            }
        ]

    original_list_all = ddmrp_api._adjustments_repo.list_all

    def counting_list_all():
        list_all_calls.append(None)
        return original_list_all()

    monkeypatch.setattr(ddmrp_api, "ddmrp_zone_rows", fake_zone_rows)
    monkeypatch.setattr(ddmrp_api, "forecast_rows", fake_forecast_rows)
    monkeypatch.setattr(ddmrp_api._adjustments_repo, "list_all", counting_list_all)

    # -- 3. The answer with the defaults (threshold 0.25, 3 periods).
    direct = ddmrp_api.adjustment_proposals()
    assert direct["as_of"] == AS_OF
    assert direct["threshold"] == 0.25
    assert direct["periods"] == 3
    # Only A's second and third periods: the first (factor 1.0) is
    # inside the 0.25 share and B ("no_demand", adu_base 0) is never
    # proposed — in start_on order.
    assert direct["proposals"] == [EXPECTED_P2, EXPECTED_P3]
    # Each source read once, the forecasts at the zone rows' as-of.
    assert zone_calls == [None]
    assert forecast_calls == [AS_OF]
    assert len(list_all_calls) == 1

    # The route answers the very same thing.
    resp = client.get(PROPOSALS_URL)
    assert resp.status_code == 200, resp.text
    assert resp.json() == _jsonable(direct)

    # -- 4. The threshold, stored as 0.4 by an admin, removed after.
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    resp = admin_client.put(SETTING_URL, json={"value": 0.4})
    assert resp.status_code == 200, resp.text
    assert resp.json()["value"] == 0.4
    assert resp.json()["source"] == "system"

    direct = ddmrp_api.adjustment_proposals()
    assert direct["threshold"] == 0.4
    # Only the second period (share 0.5) still clears 0.4.
    assert direct["proposals"] == [EXPECTED_P2]

    resp = admin_client.delete(SETTING_URL)
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "default"
    direct = ddmrp_api.adjustment_proposals()
    assert direct["threshold"] == 0.25
    assert direct["proposals"] == [EXPECTED_P2, EXPECTED_P3]

    # -- 5. existing_factor: stored adjustments covering the starts.
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO ddmrp_adjustments "
            "(id, item_code, site_code, start_on, end_on, factor, "
            "note, created_by, created_by_name) "
            "VALUES (%s, %s, %s, %s, %s, %s, NULL, 'dev-admin-1', 'admin')",
            (
                str(uuid4()),
                ITEM_A,
                SITE_A,
                OWN_START,
                OWN_END,
                OWN_FACTOR,
            ),
        )
        cur.execute(
            "INSERT INTO ddmrp_adjustments "
            "(id, item_code, site_code, start_on, end_on, factor, "
            "note, created_by, created_by_name) "
            "VALUES (%s, NULL, NULL, %s, %s, %s, NULL, 'dev-admin-1', 'admin')",
            (str(uuid4()), ALL_START, ALL_END, ALL_FACTOR),
        )

    direct = ddmrp_api.adjustment_proposals(3)
    assert direct["proposals"] == [
        {**EXPECTED_P2, "existing_factor": OWN_FACTOR},
        {**EXPECTED_P3, "existing_factor": ALL_FACTOR},
    ]
    # The pair's own adjustment wins where both scopes cover the date
    # (the all-buffer window also covers the second period's start).

    # -- 6. Nothing is written: the stored rows are untouched by the
    #       proposals, directly or through the route.
    before = _adjustment_rows()
    direct = ddmrp_api.adjustment_proposals(3)
    resp = client.get(PROPOSALS_URL)
    assert resp.status_code == 200, resp.text
    assert resp.json() == _jsonable(direct)
    assert _adjustment_rows() == before

    # -- 7. The route's access and validation rules. ------------------
    for bad in (0, 13, "0", "13", "", "abc", "1e300", 10**30, "1 2"):
        resp = client.get(PROPOSALS_URL, params={"periods": bad})
        assert resp.status_code == 422, (bad, resp.text)

    user_client = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    resp = user_client.get(PROPOSALS_URL)
    assert resp.status_code == 200, resp.text
    assert resp.json() == _jsonable(ddmrp_api.adjustment_proposals(3))

    anon = TestClient(app)
    resp = anon.get(PROPOSALS_URL)
    assert resp.status_code == 401, resp.text
    assert resp.headers.get("www-authenticate") == "Bearer"

    # -- 8. The schema carries the route the contract check expects.
    spec = json.loads(_OPENAPI.read_text())
    get = spec["paths"][PROPOSALS_URL]["get"]
    assert get["operationId"] == (
        "get_ddmrp_adjustment_proposals_api_ddmrp_adjustments_proposals_get"
    )
    periods_param = next(p for p in get["parameters"] if p["name"] == "periods")
    assert periods_param["in"] == "query"
    assert periods_param["required"] is False
    assert periods_param["schema"] == {
        "type": "integer",
        "maximum": 12,
        "minimum": 1,
        "default": 3,
        "title": "Periods",
    }
    row_ref = get["responses"]["200"]["content"]["application/json"]["schema"]
    assert row_ref["$ref"].endswith("/DdmrpAdjustmentProposalsResponse")
    envelope = spec["components"]["schemas"]["DdmrpAdjustmentProposalsResponse"]
    assert set(envelope["properties"]) == {
        "as_of",
        "threshold",
        "periods",
        "proposals",
    }
    proposal = spec["components"]["schemas"]["DdmrpAdjustmentProposal"]
    assert set(proposal["properties"]) == {
        "item_code",
        "site_code",
        "start_on",
        "end_on",
        "adu_base",
        "forecast_daily",
        "factor",
        "existing_factor",
    }
    assert proposal["properties"]["existing_factor"]["anyOf"] == [
        {"type": "number"},
        {"type": "null"},
    ]
    assert "ddmrp.adjustment_proposal_threshold" in get["description"]
    # The module docstring describes the route.
    assert f"GET {PROPOSALS_URL}?periods=3" in (ddmrp_api.__doc__ or "")

    # -- 9. Identity: the proof also runs as a NON-ADMIN (role
    #       "user").  Second pass in the same test after a full
    #       reload: the page boots the production way (the token it
    #       would take from the URL hash as #access_token=... is the
    #       development token, role "user", sent as its Bearer
    #       token); /api/auth/config answers publicly and
    #       /api/auth/me resolves the viewer; the admin-only controls
    #       are absent for that caller (403) while the page still
    #       renders, and no token is still a 401.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["user"]["sub"] == VIEWER_SUB
    assert me_body["user"]["roles"] == ["user"]
    assert "admin" not in me_body["user"]["roles"]
    assert "admin" not in me_body["user"]["groups"]

    resp = viewer.post(
        ADJUSTMENTS_URL,
        json={"start_on": "2027-01-01", "end_on": "2027-01-31", "factor": 1.1},
    )
    assert resp.status_code == 403, resp.text
    resp = viewer.delete(f"{ADJUSTMENTS_URL}/{uuid4()}")
    assert resp.status_code == 403, resp.text

    # The proposals page still renders for the viewer.
    resp = viewer.get(PROPOSALS_URL)
    assert resp.status_code == 200, resp.text
    assert resp.json() == _jsonable(ddmrp_api.adjustment_proposals(3))

    resp = anon_reloaded.get(PROPOSALS_URL)
    assert resp.status_code == 401, resp.text
    assert resp.headers.get("www-authenticate") == "Bearer"
