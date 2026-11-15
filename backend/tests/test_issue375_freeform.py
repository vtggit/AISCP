"""Issue #375 proof: the eighth OData entity set, ``Forecast``.

Against the test database (demand events inserted for the test and one
override stored through ``POST /api/forecast/overrides``):

* ``GET /odata/`` lists the eight entity sets in order — ``Forecast``
  after ``Drift``, ``Excess``, ``Stockout``, ``Changes``, ``Decisions``,
  ``ExcessDecisions`` and ``StockoutDecisions`` — and
  ``GET /odata/$metadata`` declares the ``Forecast`` EntityType,
  eighth, keyed by ``item_code``, ``site_code`` and ``period`` with
  exactly: ``item_code``, ``site_code``, ``demand_class`` and
  ``method`` (``Edm.String``), ``as_of``, ``bucket_start`` and
  ``bucket_end`` (``Edm.Date``), ``period`` and ``override_days``
  (``Edm.Int32``) and ``quantity``, ``effective_quantity`` and
  ``backtest_mae`` (``Edm.Double``),
* ``GET /odata/Forecast`` returns one row per item-site per forecast
  bucket, ordered by item code, site code and period, behind the same
  machine credential and with the same ``$top``, ``$skip``,
  ``$orderby``, ``$format`` and date ``$filter`` options as the other
  sets: the 13-history-bucket item-site gives 12 rows, periods 1 to
  12, whose ``quantity`` and ``effective_quantity`` equal the buckets
  of its ``GET /api/forecast/by-key`` row; the overridden period
  carries the override's effective quantity and ``override_days``
  while its ``quantity`` stays the statistical value [rule: OVR-4];
  ``backtest_mae`` is the winning method's backtest MAE — null for
  the young item-site whose forecast ran no backtest; the rows come
  from ``forecast_rows()``, computed once per request;
* ``$filter=bucket_start ge <date>`` keeps only the later periods,
  and ``$top``/``$skip`` page through the rows with
  ``@odata.nextLink``;
* the route answers 401 without the machine credential.
"""

import base64
import xml.etree.ElementTree as ElementTree
from datetime import date, timedelta

from fastapi.testclient import TestClient

import app.api.odata as odata
from app.db.connection import get_connection
from app.main import app

EDM = "http://docs.oasis-open.org/odata/ns/edm"
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
SITE = "SITE-375"
ITEM_A = "ITEM-375-A"  # 13 history buckets: smooth, mean, backtest ran
ITEM_B = "ITEM-375-B"  # 2 history buckets: mean, no backtest
AS_OF = date(2026, 9, 30)  # the last seeded demand day, the default as-of
FIXED_TODAY = date(2026, 10, 2)
A_FIRST_DAY = AS_OF - timedelta(days=389)  # 390 days: 13 whole 30-day buckets
B_FIRST_DAY = AS_OF - timedelta(days=59)  # 60 days: 2 whole 30-day buckets
OVERRIDE_QUANTITY = 90.0
P1_START = AS_OF + timedelta(days=1)  # the first forecast period
FILTER_START = AS_OF + timedelta(days=61)  # period 3's bucket_start

ODATA_USER = "odata-machine-375"
ODATA_PASSWORD = "feed-secret-375"
REALM = 'Basic realm="AIERP OData"'

# The exact property name -> EDM type map the Forecast EntityType must
# declare, and the order the properties appear in the document (key
# properties first, the rest in the row's order).
FORECAST_PROPERTIES = {
    "item_code": "Edm.String",
    "site_code": "Edm.String",
    "as_of": "Edm.Date",
    "demand_class": "Edm.String",
    "method": "Edm.String",
    "period": "Edm.Int32",
    "bucket_start": "Edm.Date",
    "bucket_end": "Edm.Date",
    "quantity": "Edm.Double",
    "effective_quantity": "Edm.Double",
    "override_days": "Edm.Int32",
    "backtest_mae": "Edm.Double",
}
FORECAST_PROPERTY_ORDER = [
    "item_code",
    "site_code",
    "period",
    "as_of",
    "demand_class",
    "method",
    "bucket_start",
    "bucket_end",
    "quantity",
    "effective_quantity",
    "override_days",
    "backtest_mae",
]
NULLABLE_PROPERTIES = {"backtest_mae"}


def _fixed_date():
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    return _FixedDate


def _basic(user: str, password: str) -> str:
    """An HTTP Basic Authorization header value for user/password."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def _seed() -> None:
    """Constant 2.0/day demand: 13 buckets for ITEM_A, 2 for ITEM_B."""
    events = [
        (
            f"id-de-375-{item}-{offset}",
            f"de-375-{item}-{offset}",
            item,
            SITE,
            2.0,
            (first + timedelta(days=offset)).isoformat(),
            "shipment",
            "test",
        )
        for item, first, days in (
            (ITEM_A, A_FIRST_DAY, 390),
            (ITEM_B, B_FIRST_DAY, 60),
        )
        for offset in range(days)
    ]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                events,
            )
        conn.commit()
    finally:
        conn.close()


def _by_key(client, item_code: str) -> dict:
    """The item-site's ``GET /api/forecast/by-key`` row."""
    response = client.get(
        "/api/forecast/by-key",
        params={"item_code": item_code, "site_code": SITE},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_issue375_freeform(client, monkeypatch, admin_headers):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.setattr("app.forecast.history.date", _fixed_date())
    monkeypatch.setenv("AIERP_ODATA_USER", ODATA_USER)
    monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
    good = {"Authorization": _basic(ODATA_USER, ODATA_PASSWORD)}
    _seed()

    # 1. One override stored for ITEM_A's first forecast period.
    response = client.post(
        "/api/forecast/overrides",
        headers=admin_headers,
        json={
            "item_code": ITEM_A,
            "site_code": SITE,
            "period_start": P1_START.isoformat(),
            "quantity": OVERRIDE_QUANTITY,
            "reason": "promo spike",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["applies"] is True

    # 2. The service document lists the eight sets in order, Forecast
    #    eighth; $metadata declares the Forecast EntityType keyed by
    #    item_code, site_code and period with exactly the properties.
    service = client.get("/odata/", headers=good)
    assert service.status_code == 200
    document = service.json()
    assert document["@odata.context"] == "$metadata"
    assert document["value"] == [
        {"name": name, "kind": "EntitySet", "url": name} for name in SET_NAMES
    ]
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
    forecast = next(
        element for element in entity_types if element.get("Name") == "Forecast"
    )
    key_refs = [
        ref.get("Name")
        for ref in forecast.findall(f"{{{EDM}}}Key/{{{EDM}}}PropertyRef")
    ]
    assert key_refs == ["item_code", "site_code", "period"]
    properties = {
        prop.get("Name"): prop for prop in forecast.findall(f"{{{EDM}}}Property")
    }
    assert {name: prop.get("Type") for name, prop in properties.items()} == (
        FORECAST_PROPERTIES
    )
    assert [prop.get("Name") for prop in forecast.findall(f"{{{EDM}}}Property")] == (
        FORECAST_PROPERTY_ORDER
    )
    assert {
        name for name, prop in properties.items() if prop.get("Nullable") == "true"
    } == NULLABLE_PROPERTIES
    container = schema.find(f"{{{EDM}}}EntityContainer")
    assert container is not None
    entity_sets = container.findall(f"{{{EDM}}}EntitySet")
    assert [entity_set.get("Name") for entity_set in entity_sets] == list(SET_NAMES)
    assert [entity_set.get("EntityType") for entity_set in entity_sets] == [
        f"AIERP.{name}" for name in SET_NAMES
    ]

    # 3. The collection: one row per item-site per forecast bucket,
    #    ordered by item code, site code and period; each row carries
    #    exactly the declared properties, and the buckets equal the
    #    by-key row's buckets.
    by_key_a = _by_key(client, ITEM_A)
    by_key_b = _by_key(client, ITEM_B)
    assert len(by_key_a["forecast"]) == 12
    assert len(by_key_b["forecast"]) == 12
    response = client.get("/odata/Forecast", headers=good)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body["@odata.context"] == "$metadata#Forecast"
    rows = body["value"]
    assert len(rows) == 24
    assert [(row["item_code"], row["site_code"], row["period"]) for row in rows] == [
        (item, SITE, period) for item in (ITEM_A, ITEM_B) for period in range(1, 13)
    ]
    for row in rows:
        assert set(row) == set(FORECAST_PROPERTIES)
        assert row["as_of"] == AS_OF.isoformat()
        assert row["demand_class"] == "smooth"
        assert row["method"] == "mean"
    for item, by_key in ((ITEM_A, by_key_a), (ITEM_B, by_key_b)):
        item_rows = [row for row in rows if row["item_code"] == item]
        assert [row["period"] for row in item_rows] == list(range(1, 13))
        for row, bucket in zip(item_rows, by_key["forecast"]):
            assert row["bucket_start"] == bucket["bucket_start"]
            assert row["bucket_end"] == bucket["bucket_end"]
            assert row["quantity"] == bucket["quantity"]
            assert row["effective_quantity"] == bucket["effective_quantity"]
            assert row["override_days"] == bucket["override_days"]
    # The statistical forecast of the constant 2.0/day demand: 60.0 a
    # bucket; the overridden period keeps it in ``quantity`` [rule:
    # OVR-4] and carries the override in the effective forecast.
    a_rows = [row for row in rows if row["item_code"] == ITEM_A]
    assert all(row["quantity"] == 60.0 for row in a_rows)
    assert a_rows[0]["bucket_start"] == P1_START.isoformat()
    assert a_rows[0]["effective_quantity"] == OVERRIDE_QUANTITY
    assert a_rows[0]["override_days"] == 30
    assert all(row["override_days"] == 0 for row in a_rows[1:])
    # ITEM_A ran a backtest (13 history buckets); ITEM_B did not (2),
    # so its backtest_mae is null.
    assert all(row["backtest_mae"] == 0.0 for row in a_rows)
    assert all(
        row["backtest_mae"] is None for row in rows if row["item_code"] == ITEM_B
    )

    # 4. The rows come from forecast_rows(), computed once per request.
    calls = {"n": 0}
    real_rows = odata.forecast_rows

    def counting_rows(*args, **kwargs):
        calls["n"] += 1
        return real_rows(*args, **kwargs)

    monkeypatch.setattr(odata, "forecast_rows", counting_rows)
    client.get("/odata/Forecast", headers=good)
    assert calls["n"] == 1
    client.get("/odata/Forecast", headers=good)
    assert calls["n"] == 2

    # 5. A date $filter on bucket_start keeps only the later periods.
    response = client.get(
        "/odata/Forecast",
        params={"$filter": f"bucket_start ge {FILTER_START.isoformat()}"},
        headers=good,
    )
    assert response.status_code == 200, response.text
    filtered = response.json()["value"]
    assert [(row["item_code"], row["period"]) for row in filtered] == [
        (item, period) for item in (ITEM_A, ITEM_B) for period in range(3, 13)
    ]
    assert all(row["bucket_start"] >= FILTER_START.isoformat() for row in filtered)
    # The same filter with $format=json and $orderby accepted.
    response = client.get(
        "/odata/Forecast",
        params={
            "$filter": f"bucket_start ge {FILTER_START.isoformat()}",
            "$format": "json",
            "$orderby": "period asc",
        },
        headers=good,
    )
    assert response.status_code == 200, response.text
    ordered = response.json()["value"]
    assert [(row["period"], row["item_code"]) for row in ordered] == [
        (period, item) for period in range(3, 13) for item in (ITEM_A, ITEM_B)
    ]

    # 6. $top and $skip page through the rows, nextLink while rows
    #    remain.
    response = client.get("/odata/Forecast", params={"$top": "5"}, headers=good)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["period"] for row in body["value"]] == [1, 2, 3, 4, 5]
    assert all(row["item_code"] == ITEM_A for row in body["value"])
    assert "@odata.nextLink" in body
    response = client.get(
        "/odata/Forecast", params={"$top": "5", "$skip": "5"}, headers=good
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["period"] for row in body["value"]] == [6, 7, 8, 9, 10]
    assert "@odata.nextLink" in body
    response = client.get(
        "/odata/Forecast", params={"$top": "5", "$skip": "20"}, headers=good
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["period"] for row in body["value"]] == [9, 10, 11, 12]
    assert all(row["item_code"] == ITEM_B for row in body["value"])
    assert "@odata.nextLink" not in body
    # A page past the end: the empty value, no nextLink.
    response = client.get(
        "/odata/Forecast", params={"$top": "5", "$skip": "24"}, headers=good
    )
    assert response.status_code == 200, response.text
    assert response.json()["value"] == []

    # 7. Without the machine credential the route answers 401.
    with TestClient(app) as anonymous:
        response = anonymous.get("/odata/Forecast")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == REALM
