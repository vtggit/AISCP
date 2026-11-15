"""Issue #213 proof: the seventh OData entity set,
``StockoutDecisions``.

``backend/app/api/odata.py`` publishes the stockout decisions set
under ``/odata``, after Drift, Excess, Stockout, Changes, Decisions
and ExcessDecisions, behind the same HTTP Basic machine credential:

* ``GET /odata/`` lists the eight entity sets in that order and
  ``GET /odata/$metadata`` declares the ``StockoutDecisions``
  EntityType, seventh, keyed by ``item_code`` and ``site_code`` with
  exactly: ``decision``, ``action`` and ``status`` (``Edm.String``),
  ``reason``, ``snooze_until`` and ``as_of`` (the two dates
  ``Edm.Date``), ``category`` (``Edm.String``),
  ``p_stockout_at_decision`` and ``p_stockout_now`` (``Edm.Double``,
  the latter null when the item-site is no longer listed) and
  ``money_at_risk`` (``Edm.Double``) and ``decided_at``
  (``Edm.DateTimeOffset``) — and no ``decided_by``,
* ``GET /odata/StockoutDecisions`` returns one row per item-site that
  has a stockout decision: the item-site's current decision (the
  latest by ``decided_at``, ``id`` breaking ties, exactly what
  ``GET /api/stockout/decisions`` reports as current) with ``status``
  ``applied`` for an accepted decision whose item-site is no longer in
  the current stockout rows (``stockout_ranked()``), ``outstanding``
  for every other accepted decision, and the decision itself
  otherwise; ``p_stockout_at_decision`` is the stored ``p_stockout``,
  ``p_stockout_now`` the item-site's current ``p_stockout`` (null when
  it is no longer listed) and ``money_at_risk`` the stored value,
* the set supports the same ``$top``, ``$skip``, ``$orderby``,
  ``$format`` and date ``$filter`` options as the other sets
  (``$filter=decided_at gt <timestamp>`` keeps only the later
  decisions, as do the date filters),
* the stockout ranked rows are computed once per request, the route
  answers 401 with the realm without the machine credential, nothing
  changes data, and the other six sets are unchanged.

Four item-sites are seeded on fixed past dates: 5/day demand (ACC,
OUT, DIS, SNO) from 2025-09-01 through 2026-08-31 (the latest demand
event, so the default as-of is 2026-08-31) and 9/10/11-day lead-time
observations, so the lead time is 10 days with a standard deviation
of 1 and the stockout risk prices the on-hand position against a
mean lead-time demand of 50 units and a standard deviation of 5.
Every on-hand position is below 50, so every site is listed with
category "likely".  The decisions are recorded through
``POST /api/stockout/decisions``; their stored ``decided_at`` values
are then moved to fixed past timestamps (the append-only rows are the
planner's record) so the ``decided_at`` filter is deterministic, and
the on-hand stock is then raised: ACC above the acceptable risk so it
leaves the stockout list, OUT still listed with a lower position.
"""

import base64
import statistics
import xml.etree.ElementTree as ElementTree
from datetime import UTC, date, datetime, timedelta

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
SITE_CODE = "SITE-213"
FIRST_DAY = date(2025, 9, 1)  # demand from here ...
DEMAND_DAYS = 365  # ... through 2026-08-31
DAILY_DEMAND = 5.0
AS_OF = "2026-08-31"  # the latest seeded demand event
AS_OF_STAMP = "2026-08-31 00:00:00+00"
ADMIN_SUB = "dev-admin-1"
ODATA_USER = "odata-machine-213"
ODATA_PASSWORD = "feed-secret-213"
REALM = 'Basic realm="AIERP OData"'

ACC_ITEM = "ITEM-213-ACC"  # accepted; on hand raised above the risk -> applied
OUT_ITEM = "ITEM-213-OUT"  # accepted; stays listed -> outstanding
DIS_ITEM = "ITEM-213-DIS"  # dismissed
SNO_ITEM = "ITEM-213-SNO"  # snoozed until tomorrow

# item_code -> (on_hand at decision time, unit_cost)
ITEMS = {
    ACC_ITEM: (45.0, 2.0),
    OUT_ITEM: (40.0, 3.0),
    DIS_ITEM: (30.0, 0.5),
    SNO_ITEM: (20.0, 4.0),
}

# The on-hand stock after the decisions: ACC is raised above the
# acceptable stockout risk (p 0.0228, below 0.05) so it leaves the
# stockout list; OUT moves to 45 but stays listed (p 0.8413).
ON_HAND_AFTER = {
    ACC_ITEM: 60.0,
    OUT_ITEM: 45.0,
}

# The fixed past timestamps the stored decisions are moved to.
DECIDED_AT = {
    DIS_ITEM: "2026-09-01T09:00:00+00:00",
    SNO_ITEM: "2026-09-01T10:00:00+00:00",
    OUT_ITEM: "2026-09-02T10:00:00+00:00",
    ACC_ITEM: "2026-09-03T10:00:00+00:00",
}
# A timestamp between SNO's and OUT's decisions: the gt filter keeps
# only the two later decisions (OUT and ACC).
FILTER_TS = "2026-09-01T12:00:00Z"

# The exact property name -> EDM type map the StockoutDecisions
# EntityType must declare (key properties first in the document, the
# rest in the row's order).
STOCKOUT_DECISION_PROPERTIES = {
    "item_code": "Edm.String",
    "site_code": "Edm.String",
    "decision": "Edm.String",
    "action": "Edm.String",
    "status": "Edm.String",
    "reason": "Edm.String",
    "snooze_until": "Edm.Date",
    "as_of": "Edm.Date",
    "category": "Edm.String",
    "p_stockout_at_decision": "Edm.Double",
    "p_stockout_now": "Edm.Double",
    "money_at_risk": "Edm.Double",
    "decided_at": "Edm.DateTimeOffset",
}
NULLABLE_PROPERTIES = {
    "action",
    "reason",
    "snooze_until",
    "p_stockout_now",
    "money_at_risk",
}

UNSUPPORTED_BODY = {
    "error": {"code": "BadRequest", "message": "unsupported query option"}
}


def _p_stockout(position: float) -> float:
    """The seeded stockout probability of one position.

    Mirrors ``assess_stockout`` for the seeded statistics: d 5,
    sd_d 0, lt 10, sd_lt 1, so mu 50 and sigma 5, p_stockout
    ``1 - Phi((position - mu) / sigma)`` rounded to 4 decimals.
    """
    z = (position - 50.0) / 5.0
    return round(1.0 - statistics.NormalDist().cdf(z), 4)


def _expected_units_short(position: float) -> float:
    """The seeded expected units short of one position.

    Mirrors ``assess_stockout`` for the seeded statistics (sigma
    5): ``sigma * (pdf(z) - z * (1 - Phi(z)))`` with
    ``z = (position - 50) / 5``, rounded to 2 decimals.
    """
    z = (position - 50.0) / 5.0
    normal = statistics.NormalDist()
    return round(5.0 * (normal.pdf(z) - z * (1.0 - normal.cdf(z))), 2)


def _basic(user: str, password: str) -> str:
    """An HTTP Basic Authorization header value for user/password."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def _seed() -> None:
    """The fixed-date rows: four item-sites, one listed row each."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            demand_rows = []
            lead_time_rows = []
            item_site_rows = []
            on_hand_rows = []
            for item_code, (on_hand, unit_cost) in ITEMS.items():
                suffix = item_code.split("-")[2].lower()
                demand_rows.extend(
                    (
                        f"id-de-213-{suffix}-{day_index}",
                        f"de-213-{suffix}-{day_index}",
                        item_code,
                        SITE_CODE,
                        DAILY_DEMAND,
                        (FIRST_DAY + timedelta(days=day_index)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for day_index in range(DEMAND_DAYS)
                )
                lead_time_rows.extend(
                    (
                        f"id-lt-213-{suffix}-{index}",
                        f"lt-213-{suffix}-{index}",
                        f"SUP-213-{suffix}",
                        item_code,
                        ordered.isoformat(),
                        received.isoformat(),
                        "test",
                    )
                    for index, (ordered, received) in enumerate(
                        (
                            (date(2026, 5, 1), date(2026, 5, 10)),  # 9 days
                            (date(2026, 5, 5), date(2026, 5, 15)),  # 10 days
                            (date(2026, 5, 9), date(2026, 5, 20)),  # 11 days
                        ),
                        start=1,
                    )
                )
                item_site_rows.append(
                    (
                        f"id-is-213-{suffix}",
                        item_code,
                        SITE_CODE,
                        unit_cost,
                        None,
                        None,
                        "test",
                    )
                )
                on_hand_rows.append(
                    (
                        f"id-oh-213-{suffix}",
                        item_code,
                        SITE_CODE,
                        on_hand,
                        AS_OF_STAMP,
                        "test",
                    )
                )
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                demand_rows,
            )
            cur.executemany(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, ordered_on, "
                "received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                lead_time_rows,
            )
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, reorder_point, "
                "order_qty, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                item_site_rows,
            )
            cur.executemany(
                "INSERT INTO on_hand "
                "(id, item_code, site_code, quantity_on_hand, as_of, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                on_hand_rows,
            )
        conn.commit()
    finally:
        conn.close()


def _post_decision(client, item_code: str, decision: str, **kwargs) -> dict:
    """One decision through the real API; returns the stored row."""
    body = {
        "item_code": item_code,
        "site_code": SITE_CODE,
        "decision": decision,
        **kwargs,
    }
    response = client.post("/api/stockout/decisions", json=body)
    assert response.status_code == 201, (item_code, response.status_code, response.text)
    row = response.json()
    assert row["item_code"] == item_code
    assert row["site_code"] == SITE_CODE
    assert row["decision"] == decision
    assert row["as_of"] == AS_OF
    assert row["category"] == "likely"
    on_hand, unit_cost = ITEMS[item_code]
    assert row["on_hand_units"] == on_hand
    assert row["open_supply_units"] == 0.0
    assert row["p_stockout"] == _p_stockout(on_hand)
    assert row["expected_units_short"] == _expected_units_short(on_hand)
    assert row["money_at_risk"] == round(_expected_units_short(on_hand) * unit_cost, 2)
    assert row["decided_by"] == ADMIN_SUB
    return row


def _move_decided_at(decision_id: str, decided_at: str) -> None:
    """Move a stored decision's timestamp (the append-only rows are the
    planner's record; the proof simulates the clock)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE stockout_decisions SET decided_at = %s WHERE id = %s",
                (decided_at, decision_id),
            )
            assert cur.rowcount == 1, f"no decision {decision_id} to move"
        conn.commit()
    finally:
        conn.close()


def _set_on_hand(item_code: str, quantity: float) -> None:
    """Raise the item-site's on-hand stock (the proof simulates the
    stock being raised)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE on_hand SET quantity_on_hand = %s "
                "WHERE item_code = %s AND site_code = %s",
                (quantity, item_code, SITE_CODE),
            )
            assert cur.rowcount == 1, f"no on_hand row for {item_code}"
        conn.commit()
    finally:
        conn.close()


def _row_counts() -> dict[str, int]:
    """The row counts of every table the routes read or write."""
    tables = (
        "demand_events",
        "lead_time_observations",
        "item_sites",
        "on_hand",
        "stockout_decisions",
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


def test_issue213_freeform(client, admin_token, monkeypatch):
    monkeypatch.setenv("AIERP_ODATA_USER", ODATA_USER)
    monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
    good = {"Authorization": _basic(ODATA_USER, ODATA_PASSWORD)}
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    _seed()

    # -- Decisions recorded through POST /api/stockout/decisions. ------
    accepted = _post_decision(
        admin_client,
        ACC_ITEM,
        "accepted",
        action="expedite",
        reason="cover the gap",
    )
    outstanding = _post_decision(
        admin_client, OUT_ITEM, "accepted", action="raise_order"
    )
    dismissed = _post_decision(
        admin_client, DIS_ITEM, "dismissed", reason="demand moved to a newer SKU"
    )
    snoozed = _post_decision(admin_client, SNO_ITEM, "snoozed", snooze_until=tomorrow)
    assert snoozed["snooze_until"] == tomorrow

    # -- The stored decisions move to fixed past timestamps. -----------
    _move_decided_at(dismissed["id"], DECIDED_AT[DIS_ITEM])
    _move_decided_at(snoozed["id"], DECIDED_AT[SNO_ITEM])
    _move_decided_at(outstanding["id"], DECIDED_AT[OUT_ITEM])
    _move_decided_at(accepted["id"], DECIDED_AT[ACC_ITEM])

    # -- The on-hand stock is raised: ACC leaves the list, OUT stays. ---
    for item_code, quantity in ON_HAND_AFTER.items():
        _set_on_hand(item_code, quantity)

    counts = _row_counts()

    # 1. The service document lists the eight entity sets in order.
    service = client.get("/odata/", headers=good)
    assert service.status_code == 200
    assert service.headers["content-type"] == "application/json"
    document = service.json()
    assert document["@odata.context"] == "$metadata"
    assert document["value"] == [
        {"name": name, "kind": "EntitySet", "url": name} for name in SET_NAMES
    ]

    # 2. $metadata declares the StockoutDecisions EntityType, seventh,
    #    keyed by item_code and site_code, with exactly the properties
    #    above and no decided_by.
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
    stockout_decisions = next(
        element
        for element in entity_types
        if element.get("Name") == "StockoutDecisions"
    )
    key_refs = [
        ref.get("Name")
        for ref in stockout_decisions.findall(f"{{{EDM}}}Key/{{{EDM}}}PropertyRef")
    ]
    assert key_refs == ["item_code", "site_code"]
    properties = {
        prop.get("Name"): prop
        for prop in stockout_decisions.findall(f"{{{EDM}}}Property")
    }
    assert {
        name: prop.get("Type") for name, prop in properties.items()
    } == STOCKOUT_DECISION_PROPERTIES
    assert "decided_by" not in properties
    assert [
        prop.get("Name") for prop in stockout_decisions.findall(f"{{{EDM}}}Property")
    ] == list(STOCKOUT_DECISION_PROPERTIES)
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

    # 3. The collection: one row per item-site with a decision, in key
    #    order; every row carries exactly the declared properties.
    response = client.get("/odata/StockoutDecisions", headers=good)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body["@odata.context"] == "$metadata#StockoutDecisions"
    rows = body["value"]
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        (code, SITE_CODE) for code in (ACC_ITEM, DIS_ITEM, OUT_ITEM, SNO_ITEM)
    ]
    for row in rows:
        assert set(row) == set(STOCKOUT_DECISION_PROPERTIES), row
    by_code = {row["item_code"]: row for row in rows}

    # The current stockout rows (every row, decisions included) are the
    # reference for p_stockout_now: ACC is no longer listed at all.
    response = client.get(
        "/api/stockout",
        params={"include_decided": "true", "limit": "1000"},
    )
    assert response.status_code == 200, response.text
    current_rows = {row["item_code"]: row for row in response.json()}
    assert ACC_ITEM not in current_rows
    assert set(current_rows) == {DIS_ITEM, OUT_ITEM, SNO_ITEM}

    # ACC: accepted and its on-hand stock raised above the acceptable
    # risk, so it left the stockout list: applied, with no current
    # p_stockout.
    acc = by_code[ACC_ITEM]
    assert acc["decision"] == "accepted"
    assert acc["action"] == "expedite"
    assert acc["status"] == "applied"
    assert acc["reason"] == "cover the gap"
    assert acc["snooze_until"] is None
    assert acc["as_of"] == AS_OF
    assert acc["category"] == "likely"
    assert acc["p_stockout_at_decision"] == accepted["p_stockout"]
    assert acc["p_stockout_at_decision"] == _p_stockout(ITEMS[ACC_ITEM][0])
    assert acc["p_stockout_now"] is None
    assert acc["money_at_risk"] == accepted["money_at_risk"]
    assert acc["money_at_risk"] == round(
        _expected_units_short(ITEMS[ACC_ITEM][0]) * 2.0, 2
    )
    assert datetime.fromisoformat(acc["decided_at"]) == datetime(
        2026, 9, 3, 10, 0, 0, tzinfo=UTC
    )

    # OUT: accepted and still listed: outstanding, with its current
    # p_stockout (0.8413 after the stock raise, not the stored 0.9772).
    out = by_code[OUT_ITEM]
    assert out["decision"] == "accepted"
    assert out["action"] == "raise_order"
    assert out["status"] == "outstanding"
    assert out["reason"] is None
    assert out["snooze_until"] is None
    assert out["as_of"] == AS_OF
    assert out["category"] == "likely"
    assert out["p_stockout_at_decision"] == outstanding["p_stockout"]
    assert out["p_stockout_at_decision"] == _p_stockout(ITEMS[OUT_ITEM][0])
    assert out["p_stockout_now"] == current_rows[OUT_ITEM]["p_stockout"]
    assert out["p_stockout_now"] == _p_stockout(ON_HAND_AFTER[OUT_ITEM])
    assert out["p_stockout_now"] != out["p_stockout_at_decision"]
    assert out["money_at_risk"] == outstanding["money_at_risk"]
    assert out["money_at_risk"] == round(
        _expected_units_short(ITEMS[OUT_ITEM][0]) * 3.0, 2
    )
    assert datetime.fromisoformat(out["decided_at"]) == datetime(
        2026, 9, 2, 10, 0, 0, tzinfo=UTC
    )

    # DIS: dismissed: the decision itself, still listed with its
    # unchanged current p_stockout.
    dis = by_code[DIS_ITEM]
    assert dis["decision"] == "dismissed"
    assert dis["action"] is None
    assert dis["status"] == "dismissed"
    assert dis["reason"] == "demand moved to a newer SKU"
    assert dis["category"] == "likely"
    assert dis["p_stockout_at_decision"] == dismissed["p_stockout"]
    assert dis["p_stockout_now"] == current_rows[DIS_ITEM]["p_stockout"]
    assert dis["p_stockout_now"] == _p_stockout(ITEMS[DIS_ITEM][0])
    assert dis["money_at_risk"] == dismissed["money_at_risk"]
    assert dis["money_at_risk"] == round(
        _expected_units_short(ITEMS[DIS_ITEM][0]) * 0.5, 2
    )
    assert datetime.fromisoformat(dis["decided_at"]) == datetime(
        2026, 9, 1, 9, 0, 0, tzinfo=UTC
    )

    # SNO: snoozed until tomorrow: the decision itself, still listed.
    sno = by_code[SNO_ITEM]
    assert sno["decision"] == "snoozed"
    assert sno["status"] == "snoozed"
    assert sno["snooze_until"] == tomorrow
    assert sno["category"] == "likely"
    assert sno["p_stockout_at_decision"] == snoozed["p_stockout"]
    assert sno["p_stockout_now"] == current_rows[SNO_ITEM]["p_stockout"]
    assert sno["p_stockout_now"] == _p_stockout(ITEMS[SNO_ITEM][0])
    assert sno["money_at_risk"] == snoozed["money_at_risk"]
    assert sno["money_at_risk"] == round(
        _expected_units_short(ITEMS[SNO_ITEM][0]) * 4.0, 2
    )
    assert datetime.fromisoformat(sno["decided_at"]) == datetime(
        2026, 9, 1, 10, 0, 0, tzinfo=UTC
    )

    # 4. $filter=decided_at gt <a timestamp between two decisions> keeps
    #    only the later ones.
    response = client.get(
        "/odata/StockoutDecisions",
        params={"$filter": f"decided_at gt {FILTER_TS}"},
        headers=good,
    )
    assert response.status_code == 200, response.text
    filtered = response.json()["value"]
    assert [row["item_code"] for row in filtered] == [ACC_ITEM, OUT_ITEM]
    assert filtered == [acc, out]

    # The date filter on the Edm.Date as_of works the same way: eq keeps
    # every row (all decided against the same snapshot), gt keeps none.
    response = client.get(
        "/odata/StockoutDecisions",
        params={"$filter": f"as_of eq {AS_OF}"},
        headers=good,
    )
    assert response.status_code == 200
    assert [row["item_code"] for row in response.json()["value"]] == [
        ACC_ITEM,
        DIS_ITEM,
        OUT_ITEM,
        SNO_ITEM,
    ]
    response = client.get(
        "/odata/StockoutDecisions",
        params={"$filter": f"as_of gt {AS_OF}"},
        headers=good,
    )
    assert response.status_code == 200
    assert response.json()["value"] == []

    # 5. The same $orderby, $top, $skip and $format options as the other
    #    sets.
    response = client.get(
        "/odata/StockoutDecisions",
        params={"$orderby": "decided_at asc"},
        headers=good,
    )
    assert response.status_code == 200
    assert [row["item_code"] for row in response.json()["value"]] == [
        DIS_ITEM,
        SNO_ITEM,
        OUT_ITEM,
        ACC_ITEM,
    ]
    for value in ("status desc", "unknown_property asc"):
        response = client.get(
            "/odata/StockoutDecisions", params={"$orderby": value}, headers=good
        )
        assert response.status_code == 400
        assert response.json() == UNSUPPORTED_BODY

    def walk(params: dict) -> list:
        pages: list[list[dict]] = []
        response = client.get("/odata/StockoutDecisions", params=params, headers=good)
        assert response.status_code == 200
        body = response.json()
        while True:
            pages.append(body["value"])
            if "@odata.nextLink" not in body:
                return pages
            follow = client.get(body["@odata.nextLink"], headers=good)
            assert follow.status_code == 200
            body = follow.json()

    assert walk({"$top": "1"}) == [[acc], [dis], [out], [sno]]
    assert walk({"$top": "2", "$orderby": "status asc"}) == [
        [acc, dis],
        [out, sno],
    ]
    response = client.get(
        "/odata/StockoutDecisions", params={"$skip": "2"}, headers=good
    )
    assert response.status_code == 200
    assert response.json()["value"] == [out, sno]
    response = client.get(
        "/odata/StockoutDecisions", params={"$format": "json"}, headers=good
    )
    assert response.status_code == 200
    assert response.json()["value"] == [acc, dis, out, sno]
    for params in ({"$select": "item_code"}, {"$top": "0"}, {"$skip": "-1"}):
        response = client.get("/odata/StockoutDecisions", params=params, headers=good)
        assert response.status_code == 400

    # 6. The stockout ranked rows are computed once per request.
    calls = 0
    real_ranked = odata.stockout_ranked

    def counting_ranked(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_ranked(*args, **kwargs)

    odata.stockout_ranked = counting_ranked
    try:
        response = client.get("/odata/StockoutDecisions", headers=good)
        assert response.status_code == 200
        assert response.json()["value"] == [acc, dis, out, sno]
    finally:
        odata.stockout_ranked = real_ranked
    assert calls == 1

    # 7. The other six sets are unchanged: the Stockout set reflects the
    #    current ranked rows (ACC left the list after its stock raise)
    #    and the Decisions and ExcessDecisions sets have no rows of
    #    their own.
    response = client.get("/odata/Stockout", headers=good)
    assert response.status_code == 200, response.text
    stockout_rows = response.json()["value"]
    assert [row["item_code"] for row in stockout_rows] == [
        DIS_ITEM,
        OUT_ITEM,
        SNO_ITEM,
    ]
    out_stockout = next(row for row in stockout_rows if row["item_code"] == OUT_ITEM)
    assert out_stockout["p_stockout"] == current_rows[OUT_ITEM]["p_stockout"]
    response = client.get("/odata/Decisions", headers=good)
    assert response.status_code == 200
    assert response.json()["value"] == []
    response = client.get("/odata/ExcessDecisions", headers=good)
    assert response.status_code == 200
    assert response.json()["value"] == []

    # 8. The route answers 401 with the realm without the machine
    #    credential; a valid user Bearer token alone is not one.
    with TestClient(app) as open_client:
        missing = open_client.get("/odata/StockoutDecisions")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == REALM
    bearer = client.get("/odata/StockoutDecisions")
    assert bearer.status_code == 401
    assert bearer.headers["www-authenticate"] == REALM
    wrong = client.get(
        "/odata/StockoutDecisions",
        headers={"Authorization": _basic(ODATA_USER, "wrong-password")},
    )
    assert wrong.status_code == 401
    assert wrong.headers["www-authenticate"] == REALM

    # 9. Nothing in the routes changes data.
    assert _row_counts() == counts
