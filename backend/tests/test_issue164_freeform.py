"""Issue #164 proof: the fourth OData entity set, ``Changes``.

``backend/app/api/odata.py`` publishes the fourth ranked recommendation
set under ``/odata`` behind the same HTTP Basic machine credential:

* ``GET /odata/`` lists the eight entity sets (Drift, Excess, Stockout,
  Changes, Decisions, ExcessDecisions, StockoutDecisions and
  Forecast) and ``GET /odata/$metadata`` declares the
  ``Changes``
  EntityType keyed by ``item_code`` and ``site_code`` with exactly the
  flattened properties: ``rank``, ``item_code``, ``site_code``,
  ``status_then``, ``status_now``, ``reorder_point_units_then``,
  ``reorder_point_units_now``, ``change_units``, ``primary_driver``,
  ``unit_cost`` and ``money`` (``Edm.Double``), ``since`` and
  ``as_of`` (``Edm.Date``), the four ``contributions`` values
  (``demand_level``, ``demand_variability``, ``lead_time``,
  ``lead_time_variability``) and the ``inputs_then`` / ``inputs_now``
  values (``d_then``, ``d_now``, ``lt_then``, ``lt_now``), and
  ``notes`` joined with ``"; "``;
* ``GET /odata/Changes`` returns the
  ``app.recommend.changes.recommendation_changes`` rows — one per
  ``item_code``/``site_code``, without any dedup step — each with its
  1-based ``rank`` in the order that function returns them (money at
  stake first) and flattened exactly as above: contributions and
  inputs null when absent, empty ``notes`` as ``""`` (never null),
  ``unit_cost`` and ``money`` as JSON numbers (a Decimal source value
  converted with ``float()``, a missing value null),
* the set supports the same ``$top``, ``$skip``, ``$orderby``,
  ``$format`` and date ``$filter`` options as the other sets: without
  ``$orderby`` the rows sort by the key tuple (item_code, site_code),
  ``$orderby`` accepts declared properties ascending only (``desc``
  answers 400), ``$orderby=rank`` gives the money order, and both
  orders are stable under ``$top``/``$skip`` paging,
* a ``ValueError`` from ``recommendation_changes`` answers 400 in the
  OData v4 error format, the route answers 401 with the realm without
  the machine credential, and nothing changes data; the JSON of
  ``Drift``, ``Excess`` and ``Stockout`` is unchanged.

The real-data rows are seeded on fixed past calendar dates (every date
in the past, as-of 2026-06-30 — the latest demand event — so the
default as-of resolution is deterministic): one item-site with
10/day demand from 2025-06-01 through 2026-05-31, 20/day demand
through June 2026, and pooled 10/10/12-day lead times, so the as-of
2026-06-30 snapshot (12 buckets, the June step included) recommends
a different reorder point than the since 2026-05-31 snapshot (12
buckets, steady) — 203 units against 126.  The
ordering, paging and null-flattening checks additionally run against
synthetic raw rows seeded through ``app.api.odata.recommendation_changes``,
the seam the endpoint reads the change rows from.
"""

import base64
import xml.etree.ElementTree as ElementTree
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import app.api.odata as odata
from app.api.drift import DriftRow
from app.api.excess import ExcessRow
from app.api.stockout import StockoutRow
from app.db.connection import get_connection
from app.main import app
from app.recommend.changes import recommendation_changes

EDM = "http://docs.oasis-open.org/odata/ns/edm"
AS_OF_ISO = "2026-06-30"
SINCE_ISO = "2026-05-31"
FIRST_DAY = date(2025, 6, 1)  # 10/day demand from here ...

ITEM_CODE = "ITEM-C1"
SITE_CODE = "SITE-A"
UNIT_COST = 4.0

ODATA_USER = "odata-machine-164"
ODATA_PASSWORD = "feed-secret-164"
REALM = 'Basic realm="AIERP OData"'

SET_NAMES = (
    "Drift",
    "Excess",
    "Stockout",
    "Changes",
    "Decisions",
    "ExcessDecisions",
    "StockoutDecisions",
    "Forecast",
)

# The exact property name -> EDM type map the Changes EntityType must
# declare (key properties first in the document, the rest in the
# flattened row's order).
CHANGES_PROPERTIES = {
    "item_code": "Edm.String",
    "site_code": "Edm.String",
    "rank": "Edm.Int32",
    "status_then": "Edm.String",
    "status_now": "Edm.String",
    "reorder_point_units_then": "Edm.Int32",
    "reorder_point_units_now": "Edm.Int32",
    "change_units": "Edm.Int32",
    "primary_driver": "Edm.String",
    "unit_cost": "Edm.Double",
    "money": "Edm.Double",
    "since": "Edm.Date",
    "as_of": "Edm.Date",
    "demand_level": "Edm.Double",
    "demand_variability": "Edm.Double",
    "lead_time": "Edm.Double",
    "lead_time_variability": "Edm.Double",
    "d_then": "Edm.Double",
    "d_now": "Edm.Double",
    "lt_then": "Edm.Double",
    "lt_now": "Edm.Double",
    "notes": "Edm.String",
}

UNSUPPORTED_BODY = {
    "error": {"code": "BadRequest", "message": "unsupported query option"}
}
BAD_TOP_BODY = {
    "error": {
        "code": "BadRequest",
        "message": "the $top query option must be an integer between 1 and 1000",
        "target": "$top",
    }
}
BAD_SKIP_BODY = {
    "error": {
        "code": "BadRequest",
        "message": "the $skip query option must be an integer of at least 0",
        "target": "$skip",
    }
}


def _basic(user: str, password: str) -> str:
    """An HTTP Basic Authorization header value for user/password."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def _seed() -> None:
    """The fixed-date rows: one item-site whose reorder point changes.

    10/day demand from FIRST_DAY through 2026-05-31 and 20/day demand
    through June 2026, with pooled 10/10/12-day lead times: both
    snapshots span 12 buckets, but only the as-of 2026-06-30 window
    contains the June step, so its recommended reorder point moves.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        f"id-de-164-{day_index}",
                        f"de-164-{day_index}",
                        ITEM_CODE,
                        SITE_CODE,
                        10.0,
                        (FIRST_DAY + timedelta(days=day_index)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for day_index in range(365)
                ]
                + [
                    (
                        f"id-de-164-june-{day_index}",
                        f"de-164-june-{day_index}",
                        ITEM_CODE,
                        SITE_CODE,
                        20.0,
                        (date(2026, 6, 1) + timedelta(days=day_index)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for day_index in range(30)
                ],
            )
            cur.executemany(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, ordered_on, "
                "received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-lt-164-1",
                        "lt-164-1",
                        "SUP-164",
                        ITEM_CODE,
                        "2026-02-01",
                        "2026-02-11",
                        "test",
                    ),
                    (
                        "id-lt-164-2",
                        "lt-164-2",
                        "SUP-164",
                        ITEM_CODE,
                        "2026-02-05",
                        "2026-02-15",
                        "test",
                    ),
                    (
                        "id-lt-164-3",
                        "lt-164-3",
                        "SUP-164",
                        ITEM_CODE,
                        "2026-02-08",
                        "2026-02-20",
                        "test",
                    ),
                ],
            )
            cur.execute(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, reorder_point, "
                "order_qty, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ("id-is-164", ITEM_CODE, SITE_CODE, UNIT_COST, 150.0, 100.0, "test"),
            )
        conn.commit()
    finally:
        conn.close()


def _row_counts() -> dict[str, int]:
    """The row counts of every table the routes read."""
    tables = (
        "demand_events",
        "lead_time_observations",
        "item_sites",
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            counts = {}
            for table in tables:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                counts[table] = cur.fetchone()[0]
            return counts
    finally:
        conn.close()


def _raw_row(
    item_code: str,
    site_code: str,
    *,
    status_then: str,
    status_now: str,
    units_then: int | None,
    units_now: int | None,
    change_units: int | None,
    unit_cost: Decimal | None,
    money: float | None,
    contributions: dict | None,
    inputs_then: dict | None,
    inputs_now: dict | None,
    notes: list[str],
) -> dict:
    """One raw change row, shaped as recommendation_changes returns it."""
    return {
        "item_code": item_code,
        "site_code": site_code,
        "as_of": date(2026, 6, 30),
        "since": date(2026, 5, 31),
        "status_then": status_then,
        "status_now": status_now,
        "reorder_point_then": None,
        "reorder_point_now": None,
        "reorder_point_units_then": units_then,
        "reorder_point_units_now": units_now,
        "change_units": change_units,
        "inputs_then": inputs_then,
        "inputs_now": inputs_now,
        "contributions": contributions,
        "primary_driver": (
            max(contributions, key=lambda name: abs(contributions[name]))
            if contributions is not None
            else None
        ),
        "master_lead_time_then": False,
        "master_lead_time_now": False,
        "notes": notes,
        "unit_cost": unit_cost,
        "money": money,
    }


def test_issue164_freeform(client, monkeypatch):
    monkeypatch.setenv("AIERP_ODATA_USER", ODATA_USER)
    monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
    good = {"Authorization": _basic(ODATA_USER, ODATA_PASSWORD)}
    _seed()
    counts = _row_counts()

    # 1. The service document lists the eight entity sets.
    service = client.get("/odata/", headers=good)
    assert service.status_code == 200
    assert service.headers["content-type"] == "application/json"
    document = service.json()
    assert document["@odata.context"] == "$metadata"
    assert document["value"] == [
        {"name": name, "kind": "EntitySet", "url": name} for name in SET_NAMES
    ]

    # 2. $metadata declares the Changes EntityType with exactly the
    #    flattened properties, keyed by item_code and site_code.
    metadata = client.get("/odata/$metadata", headers=good)
    assert metadata.status_code == 200
    assert metadata.headers["content-type"].startswith("application/xml")
    root = ElementTree.fromstring(metadata.text)
    assert root.tag == f"{{{EDM}}}Edm"
    schema = root.find(f"{{{EDM}}}Schema")
    assert schema is not None
    assert schema.get("Namespace") == "AIERP"
    entity_types = schema.findall(f"{{{EDM}}}EntityType")
    assert [element.get("Name") for element in entity_types] == list(SET_NAMES)
    changes = next(
        element for element in entity_types if element.get("Name") == "Changes"
    )
    key_refs = [
        ref.get("Name") for ref in changes.findall(f"{{{EDM}}}Key/{{{EDM}}}PropertyRef")
    ]
    assert key_refs == ["item_code", "site_code"]
    properties = {
        prop.get("Name"): prop.get("Type")
        for prop in changes.findall(f"{{{EDM}}}Property")
    }
    assert properties == CHANGES_PROPERTIES
    container = schema.find(f"{{{EDM}}}EntityContainer")
    assert container is not None
    entity_sets = container.findall(f"{{{EDM}}}EntitySet")
    assert [entity_set.get("Name") for entity_set in entity_sets] == list(SET_NAMES)
    assert [entity_set.get("EntityType") for entity_set in entity_sets] == [
        f"AIERP.{name}" for name in SET_NAMES
    ]

    # 3. GET /odata/Changes: the one changed item-site, its rank, and
    #    its contributions and inputs flattened.
    raw = recommendation_changes()
    assert len(raw) == 1
    change = raw[0]
    assert (change["item_code"], change["site_code"]) == (ITEM_CODE, SITE_CODE)
    # The reorder point really changed between the two snapshots.
    assert change["status_then"] == "ok"
    assert change["status_now"] == "ok"
    assert change["reorder_point_units_then"] == 126
    assert change["reorder_point_units_now"] == 203
    assert change["change_units"] == 77
    assert change["unit_cost"] == UNIT_COST
    assert change["money"] == 308.0

    expected = {
        "rank": 1,
        "item_code": ITEM_CODE,
        "site_code": SITE_CODE,
        "status_then": "ok",
        "status_now": "ok",
        "reorder_point_units_then": 126,
        "reorder_point_units_now": 203,
        "change_units": 77,
        "primary_driver": "demand_variability",
        "unit_cost": 4.0,
        "money": 308.0,
        "since": SINCE_ISO,
        "as_of": AS_OF_ISO,
        "demand_level": change["contributions"]["demand_level"],
        "demand_variability": change["contributions"]["demand_variability"],
        "lead_time": 0.0,
        "lead_time_variability": 0.0,
        "d_then": change["inputs_then"]["d"],
        "d_now": change["inputs_now"]["d"],
        "lt_then": change["inputs_then"]["lt"],
        "lt_now": change["inputs_now"]["lt"],
        "notes": "",
    }
    response = client.get("/odata/Changes", headers=good)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body == {"@odata.context": "$metadata#Changes", "value": [expected]}
    row = body["value"][0]
    # AC-5: unit_cost and money are JSON numbers (float()), not strings
    # or null.
    assert isinstance(row["unit_cost"], float)
    assert isinstance(row["money"], float)
    # The unchanged lead-time inputs carry no contribution ...
    assert row["lead_time"] == 0.0
    assert row["lead_time_variability"] == 0.0
    # ... the four contributions sum to the reorder-point difference,
    # and the primary driver is the largest absolute contribution.
    contributions = change["contributions"]
    total = (
        row["demand_level"]
        + row["demand_variability"]
        + row["lead_time"]
        + row["lead_time_variability"]
    )
    assert total == pytest.approx(
        change["reorder_point_now"] - change["reorder_point_then"], abs=1e-9
    )
    assert row["primary_driver"] == max(
        contributions, key=lambda name: abs(contributions[name])
    )
    # The inputs are the exact demand/lead-time inputs of the two
    # snapshots: the since window is steady 10 units a day (12 buckets
    # of 300), the as-of window holds 3900 units over its 12 buckets
    # (the June step); the pooled lead time is 32/3 days in both.
    assert row["d_then"] == 10.0
    assert row["d_now"] == 3900.0 / 12 / 30
    assert row["lt_then"] == row["lt_now"] == 32.0 / 3
    assert row["d_then"] != row["d_now"]
    # AC-3: empty notes serialize as "", not null.
    assert change["notes"] == []
    assert row["notes"] == ""
    # AC-6: one row per (item_code, site_code), no dedup step — the
    # source's row count is the feed's row count, keys unique.
    assert len(body["value"]) == len(recommendation_changes())
    assert {(r["item_code"], r["site_code"]) for r in body["value"]} == {
        (ITEM_CODE, SITE_CODE)
    }

    # 4. The same date $filter options as the other sets: the row is
    #    kept by the AC's filter and by the since / eq spellings,
    #    dropped by a filter it does not satisfy, and a non-date
    #    property is unsupported.
    for value in (
        "as_of gt 2026-06-29 or as_of eq 2026-06-29",
        "as_of gt 2026-06-30 or as_of eq 2026-06-30",
        f"since eq {SINCE_ISO}",
    ):
        response = client.get("/odata/Changes", params={"$filter": value}, headers=good)
        assert response.status_code == 200
        assert response.json() == {
            "@odata.context": "$metadata#Changes",
            "value": [expected],
        }
    response = client.get(
        "/odata/Changes",
        params={"$filter": "as_of gt 2026-07-01 or as_of eq 2026-07-01"},
        headers=good,
    )
    assert response.status_code == 200
    assert response.json() == {"@odata.context": "$metadata#Changes", "value": []}
    response = client.get(
        "/odata/Changes", params={"$filter": "item_code eq 'X'"}, headers=good
    )
    assert response.status_code == 400
    assert response.json() == UNSUPPORTED_BODY

    # 5. The other query options behave as for the other sets:
    #    $format=json is accepted and ignored, $select / $expand and
    #    out-of-bounds $top / $skip answer the existing 400s.
    response = client.get("/odata/Changes", params={"$format": "json"}, headers=good)
    assert response.status_code == 200
    assert response.json() == {
        "@odata.context": "$metadata#Changes",
        "value": [expected],
    }
    for option in ("$select", "$expand"):
        response = client.get(
            "/odata/Changes", params={option: "item_code"}, headers=good
        )
        assert response.status_code == 400
        assert response.json() == UNSUPPORTED_BODY
    for raw_top in ("0", "1001", "abc", "\u0000"):
        response = client.get("/odata/Changes", params={"$top": raw_top}, headers=good)
        assert response.status_code == 400
        assert response.status_code != 422
        assert response.json() == BAD_TOP_BODY
    response = client.get("/odata/Changes", params={"$skip": "-1"}, headers=good)
    assert response.status_code == 400
    assert response.json() == BAD_SKIP_BODY

    # 6. The JSON of the other three sets is unchanged: the declared
    #    properties per row, as before.
    declared = {
        "Drift": set(DriftRow.model_fields),
        "Excess": set(ExcessRow.model_fields),
        "Stockout": set(StockoutRow.model_fields),
    }
    for set_name in ("Drift", "Excess", "Stockout"):
        response = client.get(f"/odata/{set_name}", headers=good)
        assert response.status_code == 200
        body = response.json()
        assert body["@odata.context"] == f"$metadata#{set_name}"
        for row in body["value"]:
            assert set(row) == declared[set_name]

    # 7. Without the machine credential the route answers 401 with the
    #    realm, as does a wrong password.
    with TestClient(app) as anonymous:
        response = anonymous.get("/odata/Changes")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == REALM
    wrong = client.get(
        "/odata/Changes",
        headers={"Authorization": _basic(ODATA_USER, "wrong-password")},
    )
    assert wrong.status_code == 401
    assert wrong.headers["www-authenticate"] == REALM

    # 8. A ValueError from recommendation_changes answers 400 in the
    #    OData v4 error format.
    def _boom(as_of=None, since=None):
        raise ValueError("since (2026-06-30) must be earlier than as_of (2026-06-01)")

    monkeypatch.setattr(odata, "recommendation_changes", _boom)
    response = client.get("/odata/Changes", headers=good)
    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "BadRequest",
            "message": "since (2026-06-30) must be earlier than as_of (2026-06-01)",
        }
    }

    # 9. Ordering, paging and null flattening, against synthetic raw
    #    rows through the seam the endpoint reads: the source order is
    #    ITEM-B (rank 1, most money), ITEM-A (rank 2), ITEM-C (rank 3,
    #    no money), while the key-tuple order is ITEM-A, ITEM-B,
    #    ITEM-C.
    row_b = _raw_row(
        "ITEM-B",
        "SITE-2",
        status_then="ok",
        status_now="ok",
        units_then=100,
        units_now=200,
        change_units=100,
        unit_cost=Decimal("3.00"),
        money=300.0,
        contributions={
            "demand_level": 10.0,
            "demand_variability": 20.0,
            "lead_time": -30.0,
            "lead_time_variability": 0.0,
        },
        inputs_then={"d": 10.0, "sd_d": 1.0, "lt": 5.0, "sd_lt": 0.5},
        inputs_now={"d": 12.0, "sd_d": 1.5, "lt": 8.0, "sd_lt": 0.75},
        notes=["lead time rose", "demand up"],
    )
    row_a = _raw_row(
        "ITEM-A",
        "SITE-1",
        status_then="ok",
        status_now="ok",
        units_then=200,
        units_now=190,
        change_units=-10,
        unit_cost=Decimal("5.00"),
        money=50.0,
        contributions={
            "demand_level": -12.0,
            "demand_variability": 0.0,
            "lead_time": 2.0,
            "lead_time_variability": 0.0,
        },
        inputs_then={"d": 8.0, "sd_d": 0.5, "lt": 10.0, "sd_lt": 0.25},
        inputs_now={"d": 8.0, "sd_d": 0.75, "lt": 10.0, "sd_lt": 0.25},
        notes=[],
    )
    row_c = _raw_row(
        "ITEM-C",
        "SITE-1",
        status_then="absent",
        status_now="ok",
        units_then=None,
        units_now=12,
        change_units=None,
        unit_cost=None,
        money=None,
        contributions=None,
        inputs_then=None,
        inputs_now=None,
        notes=[],
    )
    monkeypatch.setattr(odata, "recommendation_changes", lambda: [row_b, row_a, row_c])

    flat_b = {
        "rank": 1,
        "item_code": "ITEM-B",
        "site_code": "SITE-2",
        "status_then": "ok",
        "status_now": "ok",
        "reorder_point_units_then": 100,
        "reorder_point_units_now": 200,
        "change_units": 100,
        "primary_driver": "lead_time",
        "unit_cost": 3.0,
        "money": 300.0,
        "since": SINCE_ISO,
        "as_of": AS_OF_ISO,
        "demand_level": 10.0,
        "demand_variability": 20.0,
        "lead_time": -30.0,
        "lead_time_variability": 0.0,
        "d_then": 10.0,
        "d_now": 12.0,
        "lt_then": 5.0,
        "lt_now": 8.0,
        "notes": "lead time rose; demand up",
    }
    flat_a = {
        "rank": 2,
        "item_code": "ITEM-A",
        "site_code": "SITE-1",
        "status_then": "ok",
        "status_now": "ok",
        "reorder_point_units_then": 200,
        "reorder_point_units_now": 190,
        "change_units": -10,
        "primary_driver": "demand_level",
        "unit_cost": 5.0,
        "money": 50.0,
        "since": SINCE_ISO,
        "as_of": AS_OF_ISO,
        "demand_level": -12.0,
        "demand_variability": 0.0,
        "lead_time": 2.0,
        "lead_time_variability": 0.0,
        "d_then": 8.0,
        "d_now": 8.0,
        "lt_then": 10.0,
        "lt_now": 10.0,
        "notes": "",
    }
    flat_c = {
        "rank": 3,
        "item_code": "ITEM-C",
        "site_code": "SITE-1",
        "status_then": "absent",
        "status_now": "ok",
        "reorder_point_units_then": None,
        "reorder_point_units_now": 12,
        "change_units": None,
        "primary_driver": None,
        "unit_cost": None,
        "money": None,
        "since": SINCE_ISO,
        "as_of": AS_OF_ISO,
        "demand_level": None,
        "demand_variability": None,
        "lead_time": None,
        "lead_time_variability": None,
        "d_then": None,
        "d_now": None,
        "lt_then": None,
        "lt_now": None,
        "notes": "",
    }

    response = client.get("/odata/Changes", headers=good)
    assert response.status_code == 200
    rows = response.json()["value"]
    # AC-6: the feed passes the source rows through one per key, in
    #    the default key-tuple order (not the ranked order).
    assert rows == [flat_a, flat_b, flat_c]
    assert len(rows) == 3
    assert {(r["item_code"], r["site_code"]) for r in rows} == {
        ("ITEM-A", "SITE-1"),
        ("ITEM-B", "SITE-2"),
        ("ITEM-C", "SITE-1"),
    }
    # AC-5 / AC-3: the Decimal unit cost is a JSON number, the missing
    #    values null, the empty notes "".
    assert isinstance(flat_b["unit_cost"], float)
    assert flat_c["unit_cost"] is None
    assert flat_c["money"] is None
    assert flat_c["demand_level"] is None
    assert flat_c["d_then"] is None
    assert flat_c["lt_now"] is None
    assert flat_a["notes"] == ""
    assert flat_b["notes"] == "lead time rose; demand up"

    # AC-4: $orderby=rank (with or without asc) gives the money order;
    #    desc and undeclared properties answer 400, as for the other
    #    sets.
    for value in ("rank", "rank asc"):
        response = client.get(
            "/odata/Changes", params={"$orderby": value}, headers=good
        )
        assert response.status_code == 200
        assert response.json()["value"] == [flat_b, flat_a, flat_c]
    for value in ("rank desc", "unknown_property asc"):
        response = client.get(
            "/odata/Changes", params={"$orderby": value}, headers=good
        )
        assert response.status_code == 400
        assert response.json() == UNSUPPORTED_BODY

    # AC-4: both orders are stable under $top / $skip paging.
    def walk(params: dict) -> list:
        pages: list[list[dict]] = []
        response = client.get("/odata/Changes", params=params, headers=good)
        assert response.status_code == 200
        body = response.json()
        while True:
            pages.append(body["value"])
            if "@odata.nextLink" not in body:
                return pages
            follow = client.get(body["@odata.nextLink"], headers=good)
            assert follow.status_code == 200
            body = follow.json()

    assert walk({"$top": "1"}) == [[flat_a], [flat_b], [flat_c]]
    assert walk({"$top": "1", "$orderby": "rank"}) == [[flat_b], [flat_a], [flat_c]]
    response = client.get("/odata/Changes", params={"$skip": "2"}, headers=good)
    assert response.status_code == 200
    assert response.json()["value"] == [flat_c]
    response = client.get(
        "/odata/Changes",
        params={"$skip": "1", "$orderby": "rank"},
        headers=good,
    )
    assert response.status_code == 200
    assert response.json()["value"] == [flat_a, flat_c]

    # 10. Nothing in the routes changes data.
    assert _row_counts() == counts
