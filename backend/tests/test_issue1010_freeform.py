"""Issue #1010 proof: the cross-ERP item comparison.

``GET /api/cross-erp/items`` answers one row per stored
``erp_code_mappings`` row of kind ``item`` (ordered by
``source_code``), each row the source code's side next to the target
code's side under ``odoo``: the sites (ordered by ``site_code``) with
their own on hand, daily demand, days of cover, recommendation status,
reorder point and suggested order quantity, stockout category and
excess units — the same figures the other screens read, from
``recommendations_for_all``, ``stockout_ranked`` and
``excess_ranked``, each called once per request inside one
``recommendations_memo`` — and the ``opportunity`` flag when one side
is short while the other holds excess (``short_side`` / ``excess_side``
the sides' indexes in ``sides``).  The route writes nothing, calls no
ERP, and is open to any signed-in user behind the app's gate.

Everything is exercised against the test database through the app's
test client with the two development stub users (the admin stub stores
the mappings, the setting and the override; the non-admin stub reads
the comparison), the way test 682 seeds its data: item-sites,
``inventory_items`` and ``on_hand`` through their repositories'
``upsert_many``, demand events and lead-time observations through the
database, and the mappings through ``PUT /api/erp-mappings``.
``date.today`` is pinned (2026-10-02) and the demand ends on
2026-09-30, so the shared default as-of is 2026-09-30 whatever the
calendar says; the replenishment demand basis is the forecast (the
stored system value), so the zero-demand item's overridden first
period gives it ``inputs.d`` 0 while every constant-history item-site
keeps its history figures.

Proved here:

  * with no mapping stored the answer is the shared default as-of
    date and empty rows, with status 200;
  * two sides — ``ACU-1`` at ``154`` under ``acumatica`` (1000 on
    hand) and ``ODO-1`` at ``WH`` under ``odoo`` (5 on hand), mapped
    to each other, answer one row with both sides, each carrying its
    own on hand, daily demand, cover and recommendation;
  * they agree with the other screens — each site's
    ``reorder_point_units`` and ``suggested_order_quantity`` equal
    ``GET /api/recommendations/by-key`` for that item-site (its
    ``status`` and daily demand the by-key row's), its
    ``stockout_category`` equals ``GET /api/stockout`` and its
    ``excess_units`` equals ``GET /api/excess``;
  * the flag — with ``ACU-1`` holding far more than its demand
    justifies and ``ODO-1`` running short, ``opportunity`` is true,
    ``short_side`` is 1 and ``excess_side`` is 0; with both sides
    healthy it is false and both are null; with both sides short it
    is false;
  * not merged — the by-key recommendations of both item-sites are
    the same whether or not the mapping is stored;
  * several sites — an item at two sites lists both in
    ``site_code`` order and the side's ``on_hand_units`` is their
    sum;
  * a missing side — a mapping whose target has no item-sites
    answers an empty ``sites``, ``on_hand_units`` 0, null
    ``source_system`` and ``opportunity`` false, with status 200;
  * zero demand — ``days_of_cover`` is null when ``daily_demand`` is
    0;
  * the rows are ordered by ``source_code`` — the six item mappings
    are stored out of that order and the rows still come back sorted;
  * only items — a ``site`` or ``vendor`` mapping adds no row;
  * nothing written — the seed tables hold the same rows after the
    call as before it;
  * who may read — a non-admin signed-in user gets 200 (the same
    answer as the admin's) and no token gets 401;
  * the identity requirement: the proof also runs as a NON-ADMIN
    (role ``user``, asserted via ``GET /api/auth/me``) in a second
    pass after a full reload (a fresh ``TestClient`` booted the
    production way — the token the page would take from the URL hash
    as ``#access_token=...``, with ``GET /api/auth/config`` answering
    publicly and ``GET /api/auth/me`` resolving the viewer): the page
    has no admin-only control — the viewer reads the whole comparison
    (it renders) — and a request without a token still gets 401.
"""

import statistics
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

URL = "/api/cross-erp/items"
BY_KEY_URL = "/api/recommendations/by-key"
MAPPINGS_URL = "/api/erp-mappings"

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
# The first forecast period at the shared default as-of (the day after
# the as-of through as_of + 30): the override that zeros the
# zero-demand item's forecast window.
PERIOD_START = (AS_OF + timedelta(days=1)).isoformat()

Z95 = statistics.NormalDist().inv_cdf(0.95)  # 1.644854
# d 2.0, sd_d 0.0, LT 10 (pooled 8/10/12-day observations), sd_LT 2.0:
# sigma = sqrt(10 * 0 + 2^2 * 2^2) = 4, safety stock Z95 * 4, reorder
# point 20 + Z95 * 4 -> 27 whole units, 720 units demanded in the
# 360-day excess window (2.0 a day).
REORDER_POINT_UNITS = 27
EXCESS_UNITS_ACU1 = 1000.0 - max(720.0, float(REORDER_POINT_UNITS))

_TABLES = ("erp_code_mappings", "item_sites", "on_hand", "inventory_items")


def _fixed_date():
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    return _FixedDate


DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)


def _demand(cur, item, site, days=360, quantity=2.0):
    """``days`` of ``quantity`` units a day for one pair, ending on the
    as-of (the 360-day statistics window, 12 buckets of 60 each)."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-1010-{item}-{site}-{i}",
                f"de-1010-{item}-{site}-{i}",
                item,
                site,
                quantity,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(days)
        ],
    )


def _lead_times(cur, item):
    """Three lead-time observations of 8, 10 and 12 days: mean 10,
    sample std 2, not low confidence."""
    for i, (ordered, received) in enumerate(
        (
            ("2026-08-22", "2026-08-30"),
            ("2026-08-20", "2026-08-30"),
            ("2026-08-18", "2026-08-30"),
        )
    ):
        cur.execute(
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                f"id-lt-1010-{item}-{i}",
                f"lt-1010-{item}-{i}",
                f"SUP-1010-{item}",
                item,
                ordered,
                received,
                "test",
            ),
        )


# (item_code, site_code, source_system, on_hand) of every item-site.
_ITEM_SITES = [
    # Two sides, one short and one excess (the flag's true case).
    ("ACU-1", "154", "acumatica", 1000.0),
    ("ODO-1", "WH", "odoo", 5.0),
    # Several sites: the source at two sites, the target at one.
    ("ACU-2", "100", "acumatica", 30.0),
    ("ACU-2", "200", "acumatica", 40.0),
    ("ODO-2", "WH", "odoo", 30.0),
    # Both sides healthy (the flag's false case, no risk anywhere).
    ("ACU-H", "154", "acumatica", 30.0),
    ("ODO-H", "WH", "odoo", 30.0),
    # Both sides short (the flag's false case, both at risk).
    ("ACU-S", "154", "acumatica", 5.0),
    ("ODO-S", "WH", "odoo", 5.0),
    # A missing side: the target holds no item-site at all.
    ("ACU-M", "154", "acumatica", 30.0),
    # Zero demand: the forecast's first period is overridden to 0.
    ("ACU-Z", "154", "acumatica", 10.0),
]
# The pairs that carry demand (every item-site but the absent target
# sites): 2.0 a day for 360 days, 360 days ending on the as-of.
_DEMAND_PAIRS = [(item, site) for item, site, _system, _on_hand in _ITEM_SITES]
# The item codes that carry lead-time observations.
_LEAD_TIME_ITEMS = sorted({item for item, _site in _DEMAND_PAIRS})


def _seed() -> None:
    """The rows through the real repositories (mappings through the
    API, below)."""
    ItemSitePostgresRepository().upsert_many(
        [
            {
                "item_code": item,
                "site_code": site,
                "unit_cost": 1.0,
                "source_system": system,
            }
            for item, site, system, _on_hand in _ITEM_SITES
        ]
    )
    OnHandPostgresRepository().upsert_many(
        [
            {
                "item_code": item,
                "site_code": site,
                "quantity_on_hand": on_hand,
                "as_of": AS_OF,
                "source_system": system,
            }
            for item, site, system, on_hand in _ITEM_SITES
        ]
    )
    InventoryItemPostgresRepository().upsert_many(
        [
            # The row's description: the source's, first.
            {
                "item_code": "ACU-1",
                "description": "Oak Desk",
                "source_system": "acumatica",
            },
            {"item_code": "ODO-1", "description": "oak desk", "source_system": "odoo"},
            # The missing-side row falls back to the target's
            # description (the source has no inventory_items row).
            {
                "item_code": "ODO-M",
                "description": "Missing Target Item",
                "source_system": "odoo",
            },
        ]
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item, site in _DEMAND_PAIRS:
                _demand(cur, item, site)
            for item in _LEAD_TIME_ITEMS:
                _lead_times(cur, item)
        conn.commit()
    finally:
        conn.close()


def _table_snapshot() -> dict[str, tuple]:
    """Every row of the seed tables, as sorted tuples for equality."""
    conn = get_connection()
    try:
        snapshot = {}
        with conn.cursor() as cur:
            for table in _TABLES:
                cur.execute(f"SELECT * FROM {table}")
                snapshot[table] = tuple(sorted(tuple(row) for row in cur.fetchall()))
        return snapshot
    finally:
        conn.close()


def _put_mapping(client, kind, source, target):
    response = client.put(
        MAPPINGS_URL,
        json={
            "kind": kind,
            "target_system": "odoo",
            "source_code": source,
            "target_code": target,
        },
    )
    assert response.status_code == 200, response.text


def test_issue1010_freeform(client, monkeypatch):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    monkeypatch.delenv("EXCESS_COVER_MONTHS", raising=False)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon = TestClient(app)

    _seed()

    # -- The answer with no mappings: the shared default as-of date and
    #    empty rows, with status 200. -----------------------------------
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == {"as_of": AS_OF_STR, "rows": []}

    # -- The forecast basis (stored system value) and the override that
    #    zeros the zero-demand item's first period. ----------------------
    response = admin.put(
        "/api/settings/replenishment.demand_basis", json={"value": "forecast"}
    )
    assert response.status_code == 200, response.text
    response = admin.post(
        "/api/forecast/overrides",
        json={
            "item_code": "ACU-Z",
            "site_code": "154",
            "period_start": PERIOD_START,
            "quantity": 0,
            "reason": "zero the first period",
        },
    )
    assert response.status_code == 201, response.text

    # -- Not merged: the by-key recommendations of both item-sites,
    #    before any mapping is stored. ----------------------------------
    by_key_before = {
        (item, site): admin.get(
            BY_KEY_URL, params={"item_code": item, "site_code": site}
        ).json()
        for item, site in (("ACU-1", "154"), ("ODO-1", "WH"))
    }
    for pair, row in by_key_before.items():
        assert row["status"] == "ok", (pair, row)
        assert row["inputs"]["d"] == 2.0, pair

    # -- Store the mappings: six of kind item, out of source_code order
    #    (so the row-order proof below is not the store's own order),
    #    one site, one vendor. -------------------------------------------
    for source, target in (
        ("ACU-Z", "ODO-Z"),
        ("ACU-1", "ODO-1"),
        ("ACU-S", "ODO-S"),
        ("ACU-M", "ODO-M"),
        ("ACU-2", "ODO-2"),
        ("ACU-H", "ODO-H"),
    ):
        _put_mapping(admin, "item", source, target)
    _put_mapping(admin, "site", "S-1", "WH-S")
    _put_mapping(admin, "vendor", "SUP-1", "SUP-O-1")

    # -- The comparison: one row per stored item mapping, in
    #    source_code order; the seed tables stay untouched. --------------
    snapshot_before = _table_snapshot()
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert _table_snapshot() == snapshot_before  # the route writes nothing
    assert answer["as_of"] == AS_OF_STR
    rows = answer["rows"]
    assert [row["source_code"] for row in rows] == [
        "ACU-1",
        "ACU-2",
        "ACU-H",
        "ACU-M",
        "ACU-S",
        "ACU-Z",
    ]  # kind item only, ordered by source_code (stored out of order above)
    assert [row["target_code"] for row in rows] == [
        "ODO-1",
        "ODO-2",
        "ODO-H",
        "ODO-M",
        "ODO-S",
        "ODO-Z",
    ]
    for row in rows:
        assert set(row) == {
            "source_code",
            "target_code",
            "description",
            "sides",
            "opportunity",
            "short_side",
            "excess_side",
            "suggested_move_units",
        }
        assert len(row["sides"]) == 2  # exactly two sides, source first
        for side in row["sides"]:
            assert set(side) == {
                "source_system",
                "item_code",
                "sites",
                "on_hand_units",
            }
            for site in side["sites"]:
                assert set(site) == {
                    "site_code",
                    "on_hand_units",
                    "daily_demand",
                    "days_of_cover",
                    "status",
                    "reorder_point_units",
                    "suggested_order_quantity",
                    "stockout_category",
                    "excess_units",
                }
    by_source = {row["source_code"]: row for row in rows}

    # -- Two sides: ACU-1 at 154 under acumatica and ODO-1 at WH under
    #    odoo, each side carrying its own on hand, daily demand, cover
    #    and recommendation. ---------------------------------------------
    acu1 = by_source["ACU-1"]
    assert acu1["description"] == "Oak Desk"  # the source's, first
    assert acu1["sides"][0] == {
        "source_system": "acumatica",
        "item_code": "ACU-1",
        "sites": [
            {
                "site_code": "154",
                "on_hand_units": 1000.0,
                "daily_demand": 2.0,
                "days_of_cover": 500.0,
                "status": "ok",
                "reorder_point_units": REORDER_POINT_UNITS,
                "suggested_order_quantity": 0,  # 27 - 1000, floored at 0
                "stockout_category": None,
                "excess_units": EXCESS_UNITS_ACU1,
            }
        ],
        "on_hand_units": 1000.0,
    }
    assert acu1["sides"][1] == {
        "source_system": "odoo",
        "item_code": "ODO-1",
        "sites": [
            {
                "site_code": "WH",
                "on_hand_units": 5.0,
                "daily_demand": 2.0,
                "days_of_cover": 2.5,
                "status": "ok",
                "reorder_point_units": REORDER_POINT_UNITS,
                "suggested_order_quantity": REORDER_POINT_UNITS - 5,
                "stockout_category": "likely",
                "excess_units": 0,
            }
        ],
        "on_hand_units": 5.0,
    }

    # -- They agree with the other screens. -------------------------------
    for (item, site), side in (
        (("ACU-1", "154"), acu1["sides"][0]),
        (("ODO-1", "WH"), acu1["sides"][1]),
    ):
        by_key = admin.get(
            BY_KEY_URL, params={"item_code": item, "site_code": site}
        ).json()
        assert by_key["status"] == "ok"
        entry = side["sites"][0]
        assert entry["status"] == by_key["status"]
        assert entry["reorder_point_units"] == by_key["reorder_point_units"]
        assert entry["suggested_order_quantity"] == by_key["suggested_order_quantity"]
        assert entry["daily_demand"] == by_key["inputs"]["d"]

    stockout_by_key = {
        (row["item_code"], row["site_code"]): row
        for row in admin.get("/api/stockout").json()
    }
    assert ("ODO-1", "WH") in stockout_by_key
    assert (
        acu1["sides"][1]["sites"][0]["stockout_category"]
        == stockout_by_key[("ODO-1", "WH")]["category"]
    )
    assert stockout_by_key[("ODO-1", "WH")]["category"] == "likely"
    assert ("ACU-1", "154") not in stockout_by_key  # the excess side is not short
    assert acu1["sides"][0]["sites"][0]["stockout_category"] is None

    excess_by_key = {
        (row["item_code"], row["site_code"]): row
        for row in admin.get("/api/excess").json()
    }
    assert ("ACU-1", "154") in excess_by_key
    assert (
        acu1["sides"][0]["sites"][0]["excess_units"]
        == excess_by_key[("ACU-1", "154")]["excess_units"]
        == EXCESS_UNITS_ACU1
    )
    assert ("ODO-1", "WH") not in excess_by_key  # the short side holds no excess
    assert acu1["sides"][1]["sites"][0]["excess_units"] == 0

    # -- The flag: ACU-1 holds far more than its demand justifies (the
    #    excess side) while ODO-1 runs short (the short side). -----------
    assert acu1["opportunity"] is True
    assert acu1["short_side"] == 1
    assert acu1["excess_side"] == 0

    # -- Both sides healthy: no risk, no excess, no opportunity. ---------
    healthy = by_source["ACU-H"]
    for side in healthy["sides"]:
        assert side["sites"][0]["stockout_category"] is None
        assert side["sites"][0]["excess_units"] == 0
    assert healthy["opportunity"] is False
    assert healthy["short_side"] is None
    assert healthy["excess_side"] is None

    # -- Both sides short: risk on both, excess on neither. --------------
    both_short = by_source["ACU-S"]
    for side in both_short["sides"]:
        assert side["sites"][0]["stockout_category"] == "likely"
        assert side["sites"][0]["excess_units"] == 0
    assert both_short["opportunity"] is False
    assert both_short["short_side"] is None
    assert both_short["excess_side"] is None

    # -- Several sites: the source item at two sites lists both in
    #    site_code order; the side's on hand is their sum. ---------------
    multi = by_source["ACU-2"]
    assert [site["site_code"] for site in multi["sides"][0]["sites"]] == ["100", "200"]
    assert [site["on_hand_units"] for site in multi["sides"][0]["sites"]] == [
        30.0,
        40.0,
    ]
    assert multi["sides"][0]["on_hand_units"] == 70.0
    assert multi["sides"][0]["source_system"] == "acumatica"
    assert multi["sides"][1] == {
        "source_system": "odoo",
        "item_code": "ODO-2",
        "sites": [
            {
                "site_code": "WH",
                "on_hand_units": 30.0,
                "daily_demand": 2.0,
                "days_of_cover": 15.0,
                "status": "ok",
                "reorder_point_units": REORDER_POINT_UNITS,
                "suggested_order_quantity": 0,
                "stockout_category": None,
                "excess_units": 0,
            }
        ],
        "on_hand_units": 30.0,
    }

    # -- A missing side: the target holds no item-sites. ------------------
    missing = by_source["ACU-M"]
    assert missing["sides"][1] == {
        "source_system": None,
        "item_code": "ODO-M",
        "sites": [],
        "on_hand_units": 0,
    }
    assert missing["opportunity"] is False
    assert missing["short_side"] is None
    assert missing["excess_side"] is None
    assert missing["description"] == "Missing Target Item"  # the target's, then

    # -- Zero demand: days of cover is null when the demand is 0. ---------
    zero = by_source["ACU-Z"]
    zero_site = zero["sides"][0]["sites"][0]
    assert zero_site["daily_demand"] == 0.0
    assert zero_site["days_of_cover"] is None
    assert zero_site["status"] == "ok"
    assert zero_site["reorder_point_units"] == 0
    assert zero_site["suggested_order_quantity"] == 0
    assert zero_site["on_hand_units"] == 10.0
    assert zero_site["stockout_category"] is None
    assert zero_site["excess_units"] == 0
    assert by_source["ACU-Z"]["description"] is None  # neither code has one
    assert zero["opportunity"] is False

    # -- Only items: the site and vendor mappings add no row. -------------
    assert len(rows) == 6
    assert "S-1" not in by_source
    assert "SUP-1" not in by_source

    # -- Not merged: the by-key recommendations of both item-sites are the
    #    same whether or not the mapping is stored. -----------------------
    for pair, before in by_key_before.items():
        item, site = pair
        after = admin.get(
            BY_KEY_URL, params={"item_code": item, "site_code": site}
        ).json()
        assert after == before, pair

    # -- Who may read: a non-admin signed-in user gets 200 (the same
    #    answer); no token gets 401. ---------------------------------------
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == answer
    assert anon.get(URL).status_code == 401

    # -- Identity, second pass after a full reload: the page boots the
    #    production way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the page has no admin-only
    #    control — the viewer reads the whole comparison (it renders) —
    #    and a request without a token still gets 401. --------------------
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
    assert "admin" not in user["groups"]
    response = viewer_reloaded.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == answer
    assert TestClient(app).get(URL).status_code == 401
