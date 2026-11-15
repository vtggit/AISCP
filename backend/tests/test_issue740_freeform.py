"""Issue #740 proof: the DDMRP buffer zones.

AC-1 — the seven ``ddmrp.*`` settings are registered (each scope
"system", type "number", with the issue's default, min and max) and
listed by ``GET /api/settings`` with their defaults — each with a
one-sentence description naming what it scales — and an admin ``PUT``
of ``ddmrp.lead_time_factor_short`` to 1.5 (outside its max of 1)
answers 422 and stores nothing.

AC-2 — the pure zone arithmetic of ``app.ddmrp.zones.buffer_zones``
(no database access): with the default settings, adu 10, dlt 20,
cycle 30, "smooth" and cv 0.3 reads medium/0.5 and low/0.25 with
``red_base`` 100, ``red_safety`` 25, ``red`` 125, ``yellow`` 200,
``green`` 300 and whole-unit tops 125, 325 and 625 behind a one-line
``arithmetic`` string; dlt 10 categorizes "short", dlt 60 "long" (and
dlt 15/45 the "medium" boundary); "lumpy" and "intermittent"
categorize "high" whatever the cv; the cv boundaries (null, below 0.5,
0.5 to 1.0 inclusive, above) read "low", "low", "medium" and "high";
a null order cycle leaves ``green`` = ``red_base``; a stored setting
value applies, and an out-of-bounds or non-numeric stored value falls
back to the default.

AC-3/AC-4 — against the live app and the migrated test database, two
designated item-sites (``PUT /api/ddmrp/buffers`` the way test 739
designates them), demand events and lead-time observations seeded the
way test 692 seeds them, and one undesignated item-site:
``GET /api/ddmrp/zones`` (any signed-in user) answers, as of the
demand statistics' default as-of date, only the two designated rows,
ordered by item_code then site_code; the "ok" row's ADU equals the
sum of its windowed demand events divided by 90 — and, after the
admin stores a non-integer 90.5 window, divided by the whole 90.5,
never truncated — its DLT the pooled
observed lead time (``dlt_source`` "observed") and its zone fields
the ``buffer_zones`` values; the designated pair with no demand reads
"no_demand" with its DLT from ``master_lead_time_days``
(``dlt_source`` "master") and null zone fields; the undesignated
item-site never appears; every statistic is read once per call,
whatever the number of rows (the statement count is unchanged when a
third designation joins); the route writes nothing; and no token
answers 401.

Identity — the zones route is open to any signed-in user, so the
proof also runs as a NON-ADMIN (role "user"): a second pass in the
same test after a full reload, with the page booted the production
way (the token it would take from the URL hash as ``#access_token=...``
— the development token, role "user" — sent as its Bearer token),
``GET /api/auth/config`` answering publicly and ``GET /api/auth/me``
resolving the viewer, sees the zones page still render (200 with
every designated row) while the admin-only controls (the
``PUT``/``DELETE`` of ``/api/ddmrp/buffers``) are absent for that
caller (403), and no token is still a 401.
"""

import math
from datetime import date, timedelta

import psycopg2
from fastapi.testclient import TestClient

from app.api.ddmrp import list_ddmrp_zones
from app.db.connection import get_connection
from app.ddmrp.zones import ZONE_FIELDS, buffer_zones
from app.main import app

URL = "/api/ddmrp/zones"
BUFFERS_URL = "/api/ddmrp/buffers"

SITE_A = "SITE-740-1"
SITE_B = "SITE-740-2"
SITE_C = "SITE-740-3"
SITE_D = "SITE-740-4"
ITEM_A = "ITEM-740A"  # designated: demand + observed lead time -> "ok"
ITEM_B = "ITEM-740B"  # designated: no demand, master lead time -> "no_demand"
ITEM_C = "ITEM-740C"  # undesignated: demand, must never appear
ITEM_D = "ITEM-740D"  # designated later, for the statement-count check

MASTER_B = 12.0
MASTER_D = 20.0

# The seven registered settings: key -> (default, min, max).
SETTINGS = {
    "ddmrp.adu_window_days": (90, 7, 365),
    "ddmrp.lead_time_factor_short": (0.8, 0.01, 1),
    "ddmrp.lead_time_factor_medium": (0.5, 0.01, 1),
    "ddmrp.lead_time_factor_long": (0.3, 0.01, 1),
    "ddmrp.variability_factor_low": (0.25, 0, 1.5),
    "ddmrp.variability_factor_medium": (0.5, 0, 1.5),
    "ddmrp.variability_factor_high": (0.75, 0, 1.5),
}

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)


def _seed(cur, sql, params):
    cur.execute(sql, params)


def _item_site(cur, item, site, master=None):
    """The item-site's current planning settings (the way test 692
    seeds its item-sites), with the master lead time when given."""
    _seed(
        cur,
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, master_lead_time_days, "
        "source_system) VALUES (%s, %s, %s, 1.0, 25.0, 10.0, %s, 'test')",
        (f"id-is-740-{item}", item, site, master),
    )


def _demand_events(cur, item, site, daily):
    """One demand event per day of ``daily`` for one item (the way
    test 692 seeds its demand events)."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-740-{item}-{day.isoformat()}",
                f"de-740-{item}-{day.isoformat()}",
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
    10, sample std 2, not low confidence (the way test 692 seeds its
    lead-time observations)."""
    for i, lead in enumerate((8, 10, 12)):
        _seed(
            cur,
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, 'test')",
            (
                f"id-lt-740-{item}-{i}",
                f"lt-740-{item}-{i}",
                f"SUP-740-{item}",
                item,
                (received - timedelta(days=lead)).isoformat(),
                received.isoformat(),
            ),
        )


class _CountingCursor:
    """A cursor that counts its execute calls, delegating the rest."""

    def __init__(self, real, counter, queries):
        self._real = real
        self._counter = counter
        self._queries = queries

    def execute(self, query, *args, **kwargs):
        self._counter[0] += 1
        self._queries.append(query)
        return self._real.execute(query, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._real.close()
        return False


class _CountingConnection:
    """A connection whose cursors count their execute calls."""

    def __init__(self, real, counter, queries):
        self._real = real
        self._counter = counter
        self._queries = queries

    def cursor(self, *args, **kwargs):
        return _CountingCursor(
            self._real.cursor(*args, **kwargs), self._counter, self._queries
        )

    def __getattr__(self, name):
        return getattr(self._real, name)


def _measure(fn):
    """Run fn with psycopg2.connect wrapped; (result, statements, SQL)."""
    counter = [0]
    queries: list[str] = []
    real_connect = psycopg2.connect

    def counting_connect(*args, **kwargs):
        return _CountingConnection(real_connect(*args, **kwargs), counter, queries)

    psycopg2.connect = counting_connect
    try:
        result = fn()
    finally:
        psycopg2.connect = real_connect
    return result, counter[0], queries


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


def _settings_entry(body: list[dict], key: str) -> dict:
    entry = next((e for e in body if e["key"] == key), None)
    assert entry is not None, (key, body)
    return entry


def test_issue740_freeform(client, admin_token, user_token, monkeypatch):
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    # The recommendation inputs of the "ok" row: the registry
    # defaults, never ambient environment values.
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # --- AC-1: the seven settings are listed with their defaults ------
    body = client.get("/api/settings").json()
    for key, (default, min_value, max_value) in SETTINGS.items():
        entry = _settings_entry(body, key)
        assert entry["scope"] == "system"
        assert entry["type"] == "number"
        assert entry["default"] == default
        assert entry["min"] == min_value
        assert entry["max"] == max_value
        assert entry["allowed"] is None
        assert entry["value"] == default, "a fresh database stores no setting"
        assert entry["source"] == "default"
        assert entry["editable"] is False, "a non-admin cannot edit a system setting"
        description = entry["description"]
        assert description.strip(), "each setting names what it scales"
        assert description.endswith("."), "the description is one sentence"

    # An out-of-bounds value answers 422 and stores nothing.
    resp = admin.put("/api/settings/ddmrp.lead_time_factor_short", json={"value": 1.5})
    assert resp.status_code == 422, resp.text
    assert "ddmrp.lead_time_factor_short" in resp.json()["detail"]
    entry = _settings_entry(
        client.get("/api/settings").json(), "ddmrp.lead_time_factor_short"
    )
    assert entry["source"] == "default"
    assert entry["value"] == 0.8

    # --- AC-2: the zone arithmetic, with the default settings ---------
    zone = buffer_zones(10, 20, 30, "smooth", 0.3, {})
    assert tuple(zone) == ZONE_FIELDS
    assert zone["lead_time_category"] == "medium"
    assert zone["lead_time_factor"] == 0.5
    assert zone["variability_category"] == "low"
    assert zone["variability_factor"] == 0.25
    assert zone["red_base"] == 100.0
    assert zone["red_safety"] == 25.0
    assert zone["red"] == 125.0
    assert zone["yellow"] == 200.0
    assert zone["green"] == 300.0
    assert zone["top_of_red"] == 125
    assert zone["top_of_yellow"] == 325
    assert zone["top_of_green"] == 625
    assert all(
        isinstance(zone[top], int)
        for top in ("top_of_red", "top_of_yellow", "top_of_green")
    )
    assert zone["arithmetic"] == (
        "red_base = adu * dlt * lead_time_factor = 10 * 20 * 0.5 = 100; "
        "red_safety = red_base * variability_factor = 100 * 0.25 = 25; "
        "red = red_base + red_safety = 100 + 25 = 125; "
        "yellow = adu * dlt = 10 * 20 = 200; "
        "green = max(adu * order_cycle_days, red_base, moq) "
        "= max(10 * 30, 100, 0) = 300; "
        "top_of_red = ceil(red) = 125; "
        "top_of_yellow = ceil(red + yellow) = 325; "
        "top_of_green = ceil(red + yellow + green) = 625"
    )
    assert "\n" not in zone["arithmetic"], "the arithmetic is one line"

    # The lead-time boundaries: short below 15, medium 15 to 45, long
    # above.
    assert buffer_zones(10, 10, 30, "smooth", 0.3, {})["lead_time_category"] == "short"
    assert buffer_zones(10, 15, 30, "smooth", 0.3, {})["lead_time_category"] == "medium"
    assert buffer_zones(10, 45, 30, "smooth", 0.3, {})["lead_time_category"] == "medium"
    assert buffer_zones(10, 60, 30, "smooth", 0.3, {})["lead_time_category"] == "long"

    # The variability category: intermittent and lumpy are "high"
    # whatever the cv; otherwise the cv decides, null cv reads "low".
    for demand_class, cv, category in (
        ("lumpy", 0.3, "high"),
        ("intermittent", 0.3, "high"),
        ("smooth", 0.3, "low"),
        ("smooth", 0.49, "low"),
        ("smooth", 0.5, "medium"),
        ("smooth", 1.0, "medium"),
        ("smooth", 1.01, "high"),
        ("smooth", None, "low"),
    ):
        zone = buffer_zones(10, 20, 30, demand_class, cv, {})
        assert zone["variability_category"] == category

    # A null order cycle leaves green = red_base (the moq is 0).
    null_cycle = buffer_zones(10, 20, None, "smooth", 0.3, {})
    assert null_cycle["green"] == null_cycle["red_base"]

    # A stored setting value applies; an out-of-bounds or non-numeric
    # stored value falls back to the registry default.
    overridden = buffer_zones(
        10,
        20,
        30,
        "smooth",
        0.3,
        {
            "ddmrp.lead_time_factor_medium": 0.9,
            "ddmrp.variability_factor_low": 0.1,
        },
    )
    assert overridden["lead_time_factor"] == 0.9
    assert overridden["variability_factor"] == 0.1
    hostile = buffer_zones(
        10,
        20,
        30,
        "smooth",
        0.3,
        {
            "ddmrp.lead_time_factor_medium": 5.0,
            "ddmrp.variability_factor_low": "low",
        },
    )
    assert hostile["lead_time_factor"] == 0.5
    assert hostile["variability_factor"] == 0.25

    # --- AC-3/AC-4: the route ------------------------------------------
    # The as-of date: the demand statistics' default as-of date is the
    # earlier of today and the latest demand event, so the latest event
    # seeded a day ago is the as-of date the route answers as of.
    as_of = date.today() - timedelta(days=1)

    # Item A: 2.0 units a day for 360 days ending on the as-of date
    # (the way test 692 seeds its smooth item-site): 12 non-zero
    # buckets of 60 each -> "smooth", cv2 0 -> cv 0.  Item C: 5 days of
    # 3 units a day ending five days before it (undesignated).
    daily_a = {as_of - timedelta(days=offset): 2.0 for offset in range(360)}
    daily_c = {as_of - timedelta(days=offset): 3.0 for offset in range(5, 10)}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _item_site(cur, ITEM_A, SITE_A)
            _item_site(cur, ITEM_B, SITE_B, master=MASTER_B)
            _item_site(cur, ITEM_C, SITE_C)
            _demand_events(cur, ITEM_A, SITE_A, daily_a)
            _demand_events(cur, ITEM_C, SITE_C, daily_c)
            # Three 8/10/12-day observations, received 30 days before
            # the as-of date (the way test 692 seeds its lead times).
            _lead_times(cur, ITEM_A, as_of - timedelta(days=30))
        conn.commit()
    finally:
        conn.close()

    # Designate the two item-sites the way test 739 designates them.
    resp = admin.put(
        BUFFERS_URL, json={"item_code": ITEM_A, "site_code": SITE_A, "note": "buffer"}
    )
    assert resp.status_code == 200, resp.text
    resp = admin.put(
        BUFFERS_URL, json={"item_code": ITEM_B, "site_code": SITE_B, "note": "buffer"}
    )
    assert resp.status_code == 200, resp.text
    assert _designations() == [
        (ITEM_A, SITE_A),
        (ITEM_B, SITE_B),
    ]

    resp = client.get(URL)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["as_of"] == as_of.isoformat()
    rows = data["rows"]
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        (ITEM_A, SITE_A),
        (ITEM_B, SITE_B),
    ], "only the two designated rows appear, ordered by item then site"
    row_a, row_b = rows

    # The "ok" row: ADU is the windowed sum over the 90 days ending on
    # the as-of date, divided by 90; the DLT is the pooled observed
    # lead time; the zones are the buffer_zones values.
    window_start = as_of - timedelta(days=89)
    windowed_sum = sum(
        quantity for day, quantity in daily_a.items() if window_start <= day <= as_of
    )
    assert windowed_sum == 180.0
    assert row_a["status"] == "ok"
    assert row_a["adu"] == windowed_sum / 90
    assert row_a["adu"] == 2.0
    assert row_a["adu_window_days"] == 90
    assert row_a["dlt_days"] == 10.0
    assert row_a["dlt_source"] == "observed"
    assert row_a["demand_class"] == "smooth"
    assert row_a["cv"] == 0.0
    assert row_a["lead_time_category"] == "short"
    assert row_a["lead_time_factor"] == 0.8
    assert row_a["variability_category"] == "low"
    assert row_a["variability_factor"] == 0.25
    assert row_a["red_base"] == 16.0
    assert row_a["red_safety"] == 4.0
    assert row_a["red"] == 20.0
    assert row_a["yellow"] == 20.0
    assert row_a["order_cycle_days"] is not None
    assert row_a["green"] == max(
        row_a["adu"] * row_a["order_cycle_days"], row_a["red_base"], 0.0
    )
    assert row_a["top_of_red"] == math.ceil(row_a["red"])
    assert row_a["top_of_yellow"] == math.ceil(row_a["red"] + row_a["yellow"])
    assert row_a["top_of_green"] == math.ceil(
        row_a["red"] + row_a["yellow"] + row_a["green"]
    )
    assert row_a["arithmetic"]
    assert "\n" not in row_a["arithmetic"]
    # The recommendation's order quantity (d 2.0, unit cost 1.0, the
    # default order cost 50 and holding rate 0.25: EOQ 540.37 -> 541
    # units covering 270.5 days) is what the green zone uses.
    assert row_a["order_cycle_days"] == 541.0 / 2.0
    assert row_a["green"] == 541.0
    assert row_a["top_of_green"] == 581

    # The designated pair with no demand: "no_demand", its DLT from the
    # master lead time, null zone fields.
    assert row_b["status"] == "no_demand"
    assert row_b["adu"] == 0.0
    assert row_b["adu_window_days"] == 90
    assert row_b["dlt_days"] == MASTER_B
    assert row_b["dlt_source"] == "master"
    assert row_b["demand_class"] is None
    assert row_b["cv"] is None
    assert row_b["order_cycle_days"] is None
    for field in ZONE_FIELDS:
        assert row_b[field] is None, field

    # The route writes nothing: the designations are unchanged.
    assert _designations() == [(ITEM_A, SITE_A), (ITEM_B, SITE_B)]

    # A non-integer window: the stored value is the whole divisor,
    # never truncated to its integer part.  The window start keeps
    # whole days (the sub-day part of the offset is dropped), so the
    # 90.5 days ending on the as-of date hold the same 90 whole days
    # as the 90-day window — but the ADU divides by the full 90.5.
    resp = admin.put("/api/settings/ddmrp.adu_window_days", json={"value": 90.5})
    assert resp.status_code == 200, resp.text
    assert resp.json()["value"] == 90.5
    row_a = next(
        row for row in client.get(URL).json()["rows"] if row["item_code"] == ITEM_A
    )
    assert row_a["adu_window_days"] == 90.5
    window_start = as_of - timedelta(days=90.5 - 1)
    windowed_905 = sum(
        quantity for day, quantity in daily_a.items() if window_start <= day <= as_of
    )
    assert windowed_905 == 180.0
    assert row_a["adu"] == windowed_905 / 90.5
    assert row_a["adu"] != windowed_905 / 90, "the divisor is not truncated"

    # Every statistic is read once per call, whatever the number of
    # rows: a third designated item-site joins, the statement count is
    # unchanged.
    _, count_before, _ = _measure(lambda: list_ddmrp_zones())
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _item_site(cur, ITEM_D, SITE_D, master=MASTER_D)
            _demand_events(
                cur,
                ITEM_D,
                SITE_D,
                {as_of - timedelta(days=offset): 3.0 for offset in (1, 2)},
            )
        conn.commit()
    finally:
        conn.close()
    resp = admin.put(
        BUFFERS_URL, json={"item_code": ITEM_D, "site_code": SITE_D, "note": "buffer"}
    )
    assert resp.status_code == 200, resp.text
    data = _measure(lambda: list_ddmrp_zones())[0]
    assert len(data["rows"]) == 3
    _, count_after, _ = _measure(lambda: list_ddmrp_zones())
    assert count_before > 0
    assert count_after == count_before, "one read per statistic, whatever the rows"

    # No token answers 401.
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
        BUFFERS_URL, json={"item_code": ITEM_C, "site_code": SITE_C, "note": "viewer"}
    )
    assert resp.status_code == 403, resp.text
    resp = viewer.delete(BUFFERS_URL, params={"item_code": ITEM_A, "site_code": SITE_A})
    assert resp.status_code == 403, resp.text
    assert _designations() == [
        (ITEM_A, SITE_A),
        (ITEM_B, SITE_B),
        (ITEM_D, SITE_D),
    ], "the viewer wrote and removed nothing"

    # ...while the zones page still renders for it, and no token is
    # still a 401.
    resp = viewer.get(URL)
    assert resp.status_code == 200, resp.text
    assert [(row["item_code"], row["site_code"]) for row in resp.json()["rows"]] == [
        (ITEM_A, SITE_A),
        (ITEM_B, SITE_B),
        (ITEM_D, SITE_D),
    ]
    assert anon_reloaded.get(URL).status_code == 401
