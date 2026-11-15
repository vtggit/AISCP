"""Issue #778 proof: the DDMRP buffer candidates.

AC-1/AC-3 — ``GET /api/ddmrp/candidates`` (any signed-in user)
answers, as of the demand statistics' default as-of date (the date
the zones route uses), one row per item-site of
``app.recommend.abc.abc_for_all`` whose ``abc_class`` is "A" or "B"
and which has no row in ``ddmrp_buffers`` — the whole ABC pool,
item-sites not yet designated as decoupling points included: five
item-sites seeded the way ``test_issue740_freeform`` seeds its
item-sites (360 days of steady daily demand ending the day before
today, the unit cost in ``item_sites``, three 8/10/12-day observed
lead times where given and the master lead time in days otherwise)
make one A-class and one B-class item-site with a lead time of at
least 7 days (the candidates), one A-class item-site with a 3-day
master lead time, one A-class item-site already designated (``PUT
/api/ddmrp/buffers`` the way test 739 designates it) and one
C-class item-site.  Only the two candidate item-sites appear,
ordered by ``annual_value`` descending, then item_code and
site_code, each with the pair, its ``abc_class`` and
``annual_value``, its ``demand_class``, its ``adu`` (the windowed
demand over the ``ddmrp.adu_window_days`` window), its ``dlt_days``
with its ``dlt_source`` (the pooled observed lead time where given,
the master lead time otherwise) and a ``reason`` reading "<class>
class, <whole days>-day lead time, <demand_class> demand"; the
3-day-lead-time, the designated and the C-class item-sites are
absent, and the 7 is the module constant
``MIN_CANDIDATE_DLT_DAYS`` (a buffer shorter than a week of lead
time rarely pays for itself).  The route writes nothing (no row in
``ddmrp_buffers``) and no token answers 401.

AC-2 — the shared numbers: once the two candidate item-sites are
designated, ``GET /api/ddmrp/zones`` answers the same ``adu`` and
``dlt_days`` for the same pair, and the designated pairs have left
the candidates.

AC-4 — ``backend/openapi.json`` carries the route and its models,
marks ``demand_class``, ``dlt_days`` and ``dlt_source`` nullable and
states the row ordering (``annual_value`` descending, then
item_code, then site_code).

Identity — the candidates route is open to any signed-in user, so
the proof also runs as a NON-ADMIN (role "user"): a second pass in
the same test after a full reload, with the page booted the
production way (the token it would take from the URL hash as
``#access_token=...`` — the development token, role "user" — sent
as its Bearer token), ``GET /api/auth/config`` answering publicly
and ``GET /api/auth/me`` resolving the viewer, sees the candidates
page still render (200 with the candidate rows) while the admin-only
controls (the ``PUT``/``DELETE`` of ``/api/ddmrp/buffers``) are
absent for that caller (403), and no token is still a 401.
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.ddmrp import MIN_CANDIDATE_DLT_DAYS
from app.db.connection import get_connection
from app.main import app

_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

URL = "/api/ddmrp/candidates"
BUFFERS_URL = "/api/ddmrp/buffers"
ZONES_URL = "/api/ddmrp/zones"

SITE_A = "SITE-778-1"
SITE_B = "SITE-778-2"
SITE_C = "SITE-778-3"
SITE_D = "SITE-778-4"
SITE_E = "SITE-778-5"
ITEM_A = "ITEM-778A"  # A class, 10-day observed lead time -> candidate
ITEM_B = "ITEM-778B"  # A class, 3-day master lead time -> no candidate
ITEM_C = "ITEM-778C"  # A class, already designated -> no candidate
ITEM_D = "ITEM-778D"  # B class, 14-day master lead time -> candidate
ITEM_E = "ITEM-778E"  # C class -> no candidate

MASTER_B = 3.0
MASTER_D = 14.0
MASTER_E = 30.0

# item-site -> (daily demand for 360 days, unit cost): the annual
# values are 730.0 (A), 273.75 (A), 182.5 (A), 91.25 (B) and 45.625
# (C) of the total 1323.125, so the classes are A, A, A, B and C.
SEED = {
    ITEM_A: (SITE_A, 1.0, 2.0, None),
    ITEM_B: (SITE_B, 1.0, 0.75, MASTER_B),
    ITEM_C: (SITE_C, 0.5, 1.0, None),
    ITEM_D: (SITE_D, 0.25, 1.0, MASTER_D),
    ITEM_E: (SITE_E, 0.125, 1.0, MASTER_E),
}

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)


def _item_site(cur, item, site, unit_cost, master=None):
    """The item-site's current planning settings (the way test 740
    seeds its item-sites), with the unit cost and the master lead
    time in days when given."""
    cur.execute(
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, master_lead_time_days, "
        "source_system) VALUES (%s, %s, %s, %s, 25.0, 10.0, %s, 'test')",
        (f"id-is-778-{item}", item, site, unit_cost, master),
    )


def _demand_events(cur, item, site, daily):
    """One demand event per day of ``daily`` for one item (the way
    test 740 seeds its demand events)."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-778-{item}-{day.isoformat()}",
                f"de-778-{item}-{day.isoformat()}",
                item,
                site,
                quantity,
                day.isoformat(),
                "shipment",
                "test",
            )
            for day, quantity in sorted(daily.items(), reverse=True)
        ],
    )


def _lead_times(cur, item, received):
    """Three lead-time observations of 8, 10 and 12 days: pooled mean
    10, sample std 2, not low confidence (the way test 740 seeds its
    lead-time observations)."""
    for i, lead in enumerate((8, 10, 12)):
        cur.execute(
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, 'test')",
            (
                f"id-lt-778-{item}-{i}",
                f"lt-778-{item}-{i}",
                f"SUP-778-{item}",
                item,
                (received - timedelta(days=lead)).isoformat(),
                received.isoformat(),
            ),
        )


def _designations() -> list[tuple]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT item_code, site_code FROM ddmrp_buffers "
                "ORDER BY item_code, site_code"
            )
            return [tuple(r) for r in cur.fetchall()]
    finally:
        conn.close()


def test_issue778_freeform(client, admin_token, user_token, monkeypatch):
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    # The recommendation inputs of the pool: the registry defaults,
    # never ambient environment values.
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    assert MIN_CANDIDATE_DLT_DAYS == 7, "the constant is one week of lead time"

    # The as-of date: the demand statistics' default as-of date is the
    # earlier of today and the latest demand event, so the latest
    # event seeded a day ago is the as-of date both routes answer as
    # of.
    as_of = date.today() - timedelta(days=1)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item, (site, daily, unit_cost, master) in SEED.items():
                _item_site(cur, item, site, unit_cost=unit_cost, master=master)
                _demand_events(
                    cur,
                    item,
                    site,
                    {as_of - timedelta(days=offset): daily for offset in range(360)},
                )
            # Three 8/10/12-day observations, received 30 days before
            # the as-of date (the way test 740 seeds its lead times).
            _lead_times(cur, ITEM_A, as_of - timedelta(days=30))
            _lead_times(cur, ITEM_C, as_of - timedelta(days=30))
        conn.commit()
    finally:
        conn.close()

    # Designate one A-class item-site the way test 739 designates it:
    # it is in the ABC pool but already a buffer, so it is no
    # candidate.
    resp = admin.put(
        BUFFERS_URL, json={"item_code": ITEM_C, "site_code": SITE_C, "note": "buffer"}
    )
    assert resp.status_code == 200, resp.text

    # --- AC-1: the candidates ------------------------------------------
    resp = client.get(URL)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["as_of"] == as_of.isoformat(), "as of the zones route's date"
    rows = data["rows"]

    # Who appears: only the A and B item-sites with a lead time of at
    # least 7 days, ordered by annual value descending (730.0 then
    # 91.25), then item_code and site_code.
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        (ITEM_A, SITE_A),
        (ITEM_D, SITE_D),
    ], "only the A and B item-sites with dlt_days >= 7 appear"
    assert [row["abc_class"] for row in rows] == ["A", "B"]
    assert [row["annual_value"] for row in rows] == [730.0, 91.25]

    row_a, row_d = rows
    # The A candidate: 1.0 units a day in the 90-day window, the
    # pooled observed lead time (8/10/12 -> 10), "smooth" demand.
    assert row_a["demand_class"] == "smooth"
    assert row_a["adu"] == 90.0 / 90
    assert row_a["dlt_days"] == 10.0
    assert row_a["dlt_source"] == "observed"
    assert row_a["reason"] == "A class, 10-day lead time, smooth demand"
    # The B candidate: 0.25 units a day in the window, the master
    # lead time in days (14), "smooth" demand.
    assert row_d["demand_class"] == "smooth"
    assert row_d["adu"] == 22.5 / 90
    assert row_d["dlt_days"] == MASTER_D
    assert row_d["dlt_source"] == "master"
    assert row_d["reason"] == "B class, 14-day lead time, smooth demand"

    # Exclusions: the 3-day-lead-time pair (A class, dlt_days 3 < 7),
    # the designated pair (A class, a row in ddmrp_buffers) and the
    # C-class pair are absent.
    pairs = {(row["item_code"], row["site_code"]) for row in rows}
    assert (ITEM_B, SITE_B) not in pairs, "a 3-day lead time is below 7"
    assert (ITEM_C, SITE_C) not in pairs, "already designated"
    assert (ITEM_E, SITE_E) not in pairs, "C class"

    # No side effects: the call wrote no row to ddmrp_buffers.
    assert _designations() == [(ITEM_C, SITE_C)]

    # Unauthenticated access answers 401.
    anon = TestClient(app)
    assert anon.get(URL).status_code == 401

    # --- Identity: a second pass after a full reload, as the
    #     non-admin (role "user"). --------------------------------------
    # The page boots the production way: the token it would take from
    # the URL hash as #access_token=... is the development token (role
    # "user") sent as its Bearer token; GET /api/auth/config answers
    # publicly and GET /api/auth/me resolves the viewer.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["user"]["sub"] == "dev-user-1"
    assert me_body["user"]["roles"] == ["user"]
    assert "admin" not in me_body["user"]["roles"]

    # The admin-only controls are absent for that caller: the
    # designation writes are refused...
    resp = viewer.put(
        BUFFERS_URL, json={"item_code": ITEM_A, "site_code": SITE_A, "note": "viewer"}
    )
    assert resp.status_code == 403, resp.text
    resp = viewer.delete(BUFFERS_URL, params={"item_code": ITEM_C, "site_code": SITE_C})
    assert resp.status_code == 403, resp.text
    assert _designations() == [(ITEM_C, SITE_C)], "the viewer wrote and removed nothing"

    # ...while the candidates page still renders for it, and no token
    # is still a 401.
    resp = viewer.get(URL)
    assert resp.status_code == 200, resp.text
    assert [(row["item_code"], row["site_code"]) for row in resp.json()["rows"]] == [
        (ITEM_A, SITE_A),
        (ITEM_D, SITE_D),
    ]
    assert anon_reloaded.get(URL).status_code == 401

    # --- AC-2: the shared numbers ---------------------------------------
    # Once the two candidate item-sites are designated, the zones
    # route answers for the same pair...
    for item, site in ((ITEM_A, SITE_A), (ITEM_D, SITE_D)):
        resp = admin.put(
            BUFFERS_URL, json={"item_code": item, "site_code": site, "note": "buffer"}
        )
        assert resp.status_code == 200, resp.text
    zone_rows = {
        (row["item_code"], row["site_code"]): row
        for row in client.get(ZONES_URL).json()["rows"]
    }
    assert zone_rows[(ITEM_A, SITE_A)]["status"] == "ok"
    assert zone_rows[(ITEM_D, SITE_D)]["status"] == "ok"
    assert zone_rows[(ITEM_A, SITE_A)]["adu"] == row_a["adu"]
    assert zone_rows[(ITEM_A, SITE_A)]["dlt_days"] == row_a["dlt_days"]
    assert zone_rows[(ITEM_D, SITE_D)]["adu"] == row_d["adu"]
    assert zone_rows[(ITEM_D, SITE_D)]["dlt_days"] == row_d["dlt_days"]

    # ...and the designated pairs have left the candidates.
    resp = client.get(URL)
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows"] == []
    assert _designations() == [
        (ITEM_A, SITE_A),
        (ITEM_C, SITE_C),
        (ITEM_D, SITE_D),
    ]

    # --- AC-4: the schema file ------------------------------------------
    spec = json.loads(_OPENAPI.read_text(encoding="utf-8"))
    op = spec["paths"][URL]["get"]
    assert (
        op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/DdmrpCandidateResponse"
    )
    row_schema = spec["components"]["schemas"]["DdmrpCandidateRow"]
    # The nullable fields are explicitly nullable...
    assert row_schema["properties"]["demand_class"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert row_schema["properties"]["dlt_days"]["anyOf"] == [
        {"type": "number"},
        {"type": "null"},
    ]
    assert row_schema["properties"]["dlt_source"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    # ...the others are not, and every field is required.
    for field in (
        "item_code",
        "site_code",
        "abc_class",
        "annual_value",
        "adu",
        "reason",
    ):
        assert "anyOf" not in row_schema["properties"][field]
    assert set(row_schema["required"]) == set(row_schema["properties"])
    # ...and the row ordering is stated in the schema (the operation
    # description and the response model's description).
    ordering = "annual_value`` descending, then ``item_code``, then ``site_code``"
    flattened = re.sub(r"\s+", " ", op["description"])
    assert ordering in flattened, "the operation states the row ordering"
    flattened_model = re.sub(
        r"\s+",
        " ",
        spec["components"]["schemas"]["DdmrpCandidateResponse"]["description"],
    )
    assert ordering in flattened_model, "the response model states the row ordering"
