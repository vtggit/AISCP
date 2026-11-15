"""Issue #212 proof: the sixth OData entity set, ``ExcessDecisions``.

``backend/app/api/odata.py`` publishes the excess decisions set under
``/odata``, after Drift, Excess, Stockout, Changes and Decisions,
behind the same HTTP Basic machine credential:

* ``GET /odata/`` lists the eight entity sets in that order and
  ``GET /odata/$metadata`` declares the ``ExcessDecisions`` EntityType
  keyed by ``item_code`` and ``site_code`` with exactly:
  ``decision``, ``action`` and ``status`` (``Edm.String``),
  ``reason``, ``snooze_until`` and ``as_of`` (the two dates
  ``Edm.Date``), ``category`` (``Edm.String``),
  ``excess_units_at_decision`` and ``excess_units_now`` (``Edm.Double``,
  the latter null when the item-site is no longer listed) and
  ``money_tied_up`` (``Edm.Double``) and ``decided_at``
  (``Edm.DateTimeOffset``) — and no ``decided_by``,
* ``GET /odata/ExcessDecisions`` returns one row per item-site that has
  an excess decision: the item-site's current decision (the latest by
  ``decided_at``, ``id`` breaking ties, exactly what
  ``GET /api/excess/decisions`` reports as current) with ``status``
  ``applied`` for an accepted decision whose item-site is no longer in
  the current dead-and-excess rows (``excess_ranked()``),
  ``outstanding`` for every other accepted decision, and the decision
  itself otherwise; ``excess_units_at_decision`` is the stored
  ``excess_units``, ``excess_units_now`` the item-site's current
  ``excess_units`` (null when it is no longer listed) and
  ``money_tied_up`` the stored value,
* the set supports the same ``$top``, ``$skip``, ``$orderby``,
  ``$format`` and date ``$filter`` options as the other sets
  (``$filter=decided_at gt <timestamp>`` keeps only the later
  decisions, as do the date filters),
* the dead-and-excess ranked rows are computed once per request, the
  route answers 401 with the realm without the machine credential,
  nothing changes data, and the other five sets are unchanged.

Four item-sites are seeded on fixed past dates: 2/day demand (ACC,
OUT) and no demand at all (DIS, SNO) from 2025-07-06 through
2026-06-30 (the latest demand event, so the default as-of is
2026-06-30), with on-hand stock on each site, so every site is listed
— the two demand sites as "excess", the two never-moved sites as
"dead" (never_moved).  The decisions are recorded through
``POST /api/excess/decisions``; their stored ``decided_at`` values are
then moved to fixed past timestamps (the append-only rows are the
planner's record) so the ``decided_at`` filter is deterministic, and
the on-hand stock is then moved: ACC below its justified cover so it
leaves the dead-and-excess list, OUT still above it so it stays.
"""

import base64
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
SITE_CODE = "SITE-212"
FIRST_DAY = date(2025, 7, 6)  # demand from here ...
DEMAND_DAYS = 360  # ... through 2026-06-30
AS_OF = "2026-06-30"  # the latest seeded demand event
AS_OF_STAMP = "2026-06-30 00:00:00+00"
ADMIN_SUB = "dev-admin-1"
ODATA_USER = "odata-machine-212"
ODATA_PASSWORD = "feed-secret-212"
REALM = 'Basic realm="AIERP OData"'

ACC_ITEM = "ITEM-212-ACC"  # excess, accepted; on hand reduced below cover -> applied
OUT_ITEM = "ITEM-212-OUT"  # excess, accepted; stays listed -> outstanding
DIS_ITEM = "ITEM-212-DIS"  # dead, dismissed
SNO_ITEM = "ITEM-212-SNO"  # dead, snoozed until tomorrow

# item_code -> (on_hand, unit_cost, daily demand or None)
ITEMS = {
    ACC_ITEM: (1000.0, 2.0, 2.0),
    OUT_ITEM: (500.0, 1.0, 1.0),
    DIS_ITEM: (200.0, 1.0, None),
    SNO_ITEM: (100.0, 1.0, None),
}

EXCESS_UNITS = {
    ACC_ITEM: 280.0,  # 1000 - ceil(720 in-window units)
    OUT_ITEM: 140.0,  # 500 - ceil(360 in-window units)
    DIS_ITEM: 200.0,  # dead: the whole on hand
    SNO_ITEM: 100.0,  # dead: the whole on hand
}

MONEY_TIED_UP = {
    ACC_ITEM: 560.0,
    OUT_ITEM: 140.0,
    DIS_ITEM: 200.0,
    SNO_ITEM: 100.0,
}

# The on-hand stock after the decisions: ACC drops below its justified
# cover (720) so it leaves the dead-and-excess list, OUT stays above
# its (360) so it stays listed with 90 excess units.
ON_HAND_AFTER = {
    ACC_ITEM: 700.0,
    OUT_ITEM: 450.0,
}

# The fixed past timestamps the stored decisions are moved to.
DECIDED_AT = {
    DIS_ITEM: "2026-07-01T09:00:00+00:00",
    SNO_ITEM: "2026-07-01T10:00:00+00:00",
    OUT_ITEM: "2026-07-02T10:00:00+00:00",
    ACC_ITEM: "2026-07-03T10:00:00+00:00",
}
# A timestamp between SNO's and OUT's decisions: the gt filter keeps
# only the two later decisions (OUT and ACC).
FILTER_TS = "2026-07-01T12:00:00Z"

# The exact property name -> EDM type map the ExcessDecisions EntityType
# must declare (key properties first in the document, the rest in the
# row's order).
EXCESS_DECISION_PROPERTIES = {
    "item_code": "Edm.String",
    "site_code": "Edm.String",
    "decision": "Edm.String",
    "action": "Edm.String",
    "status": "Edm.String",
    "reason": "Edm.String",
    "snooze_until": "Edm.Date",
    "as_of": "Edm.Date",
    "category": "Edm.String",
    "excess_units_at_decision": "Edm.Double",
    "excess_units_now": "Edm.Double",
    "money_tied_up": "Edm.Double",
    "decided_at": "Edm.DateTimeOffset",
}
NULLABLE_PROPERTIES = {
    "action",
    "reason",
    "snooze_until",
    "excess_units_now",
    "money_tied_up",
}

UNSUPPORTED_BODY = {
    "error": {"code": "BadRequest", "message": "unsupported query option"}
}


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
            item_site_rows = []
            on_hand_rows = []
            for item_code, (on_hand, unit_cost, daily_demand) in ITEMS.items():
                suffix = item_code.split("-")[2].lower()
                if daily_demand is not None:
                    demand_rows.extend(
                        (
                            f"id-de-212-{suffix}-{day_index}",
                            f"de-212-{suffix}-{day_index}",
                            item_code,
                            SITE_CODE,
                            daily_demand,
                            (FIRST_DAY + timedelta(days=day_index)).isoformat(),
                            "shipment",
                            "test",
                        )
                        for day_index in range(DEMAND_DAYS)
                    )
                item_site_rows.append(
                    (
                        f"id-is-212-{suffix}",
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
                        f"id-oh-212-{suffix}",
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
    response = client.post("/api/excess/decisions", json=body)
    assert response.status_code == 201, (item_code, response.status_code, response.text)
    row = response.json()
    assert row["item_code"] == item_code
    assert row["site_code"] == SITE_CODE
    assert row["decision"] == decision
    assert row["as_of"] == AS_OF
    on_hand, _unit_cost, daily_demand = ITEMS[item_code]
    assert row["on_hand_units"] == on_hand
    assert row["excess_units"] == EXCESS_UNITS[item_code]
    assert row["money_tied_up"] == MONEY_TIED_UP[item_code]
    assert row["category"] == ("excess" if daily_demand is not None else "dead")
    assert row["dead_reason"] == (None if daily_demand is not None else "never_moved")
    assert row["decided_by"] == ADMIN_SUB
    return row


def _move_decided_at(decision_id: str, decided_at: str) -> None:
    """Move a stored decision's timestamp (the append-only rows are the
    planner's record; the proof simulates the clock)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE excess_decisions SET decided_at = %s WHERE id = %s",
                (decided_at, decision_id),
            )
            assert cur.rowcount == 1, f"no decision {decision_id} to move"
        conn.commit()
    finally:
        conn.close()


def _set_on_hand(item_code: str, quantity: float) -> None:
    """Move the item-site's on-hand stock (the proof simulates the
    stock being reduced)."""
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
    tables = ("demand_events", "item_sites", "on_hand", "excess_decisions")
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


def test_issue212_freeform(client, admin_token, monkeypatch):
    monkeypatch.setenv("AIERP_ODATA_USER", ODATA_USER)
    monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
    good = {"Authorization": _basic(ODATA_USER, ODATA_PASSWORD)}
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    _seed()

    # -- Decisions recorded through POST /api/excess/decisions. ----------
    accepted = _post_decision(
        admin_client,
        ACC_ITEM,
        "accepted",
        action="stop_replenishing",
        reason="dispose the surplus",
    )
    outstanding = _post_decision(admin_client, OUT_ITEM, "accepted", action="reduce")
    dismissed = _post_decision(
        admin_client, DIS_ITEM, "dismissed", reason="superseded by a newer SKU"
    )
    snoozed = _post_decision(admin_client, SNO_ITEM, "snoozed", snooze_until=tomorrow)
    assert snoozed["snooze_until"] == tomorrow

    # -- The stored decisions move to fixed past timestamps. --------------
    _move_decided_at(dismissed["id"], DECIDED_AT[DIS_ITEM])
    _move_decided_at(snoozed["id"], DECIDED_AT[SNO_ITEM])
    _move_decided_at(outstanding["id"], DECIDED_AT[OUT_ITEM])
    _move_decided_at(accepted["id"], DECIDED_AT[ACC_ITEM])

    # -- The on-hand stock moves: ACC leaves the list, OUT stays. ----------
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

    # 2. $metadata declares the ExcessDecisions EntityType, sixth, keyed
    #    by item_code and site_code, with exactly the properties above
    #    and no decided_by.
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
    excess_decisions = next(
        element for element in entity_types if element.get("Name") == "ExcessDecisions"
    )
    key_refs = [
        ref.get("Name")
        for ref in excess_decisions.findall(f"{{{EDM}}}Key/{{{EDM}}}PropertyRef")
    ]
    assert key_refs == ["item_code", "site_code"]
    properties = {
        prop.get("Name"): prop
        for prop in excess_decisions.findall(f"{{{EDM}}}Property")
    }
    assert {
        name: prop.get("Type") for name, prop in properties.items()
    } == EXCESS_DECISION_PROPERTIES
    assert "decided_by" not in properties
    assert [
        prop.get("Name") for prop in excess_decisions.findall(f"{{{EDM}}}Property")
    ] == list(EXCESS_DECISION_PROPERTIES)
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
    response = client.get("/odata/ExcessDecisions", headers=good)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body["@odata.context"] == "$metadata#ExcessDecisions"
    rows = body["value"]
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        (code, SITE_CODE) for code in (ACC_ITEM, DIS_ITEM, OUT_ITEM, SNO_ITEM)
    ]
    for row in rows:
        assert set(row) == set(EXCESS_DECISION_PROPERTIES), row
    by_code = {row["item_code"]: row for row in rows}

    # ACC: accepted and its on-hand stock reduced below the justified
    # cover, so it left the dead-and-excess list: applied, with no
    # current excess units.
    acc = by_code[ACC_ITEM]
    assert acc["decision"] == "accepted"
    assert acc["action"] == "stop_replenishing"
    assert acc["status"] == "applied"
    assert acc["reason"] == "dispose the surplus"
    assert acc["snooze_until"] is None
    assert acc["as_of"] == AS_OF
    assert acc["category"] == "excess"
    assert acc["excess_units_at_decision"] == EXCESS_UNITS[ACC_ITEM]
    assert acc["excess_units_now"] is None
    assert acc["money_tied_up"] == MONEY_TIED_UP[ACC_ITEM]
    assert datetime.fromisoformat(acc["decided_at"]) == datetime(
        2026, 7, 3, 10, 0, 0, tzinfo=UTC
    )

    # OUT: accepted and still listed: outstanding, with its current
    # excess units (90 after the stock reduction, not the stored 140).
    out = by_code[OUT_ITEM]
    assert out["decision"] == "accepted"
    assert out["action"] == "reduce"
    assert out["status"] == "outstanding"
    assert out["reason"] is None
    assert out["snooze_until"] is None
    assert out["as_of"] == AS_OF
    assert out["category"] == "excess"
    assert out["excess_units_at_decision"] == EXCESS_UNITS[OUT_ITEM]
    assert out["excess_units_now"] == 90.0
    assert out["money_tied_up"] == MONEY_TIED_UP[OUT_ITEM]
    assert datetime.fromisoformat(out["decided_at"]) == datetime(
        2026, 7, 2, 10, 0, 0, tzinfo=UTC
    )

    # DIS: dismissed: the decision itself, still listed with its
    # unchanged excess units.
    dis = by_code[DIS_ITEM]
    assert dis["decision"] == "dismissed"
    assert dis["action"] is None
    assert dis["status"] == "dismissed"
    assert dis["reason"] == "superseded by a newer SKU"
    assert dis["category"] == "dead"
    assert dis["excess_units_at_decision"] == EXCESS_UNITS[DIS_ITEM]
    assert dis["excess_units_now"] == EXCESS_UNITS[DIS_ITEM]
    assert dis["money_tied_up"] == MONEY_TIED_UP[DIS_ITEM]
    assert datetime.fromisoformat(dis["decided_at"]) == datetime(
        2026, 7, 1, 9, 0, 0, tzinfo=UTC
    )

    # SNO: snoozed until tomorrow: the decision itself.
    sno = by_code[SNO_ITEM]
    assert sno["decision"] == "snoozed"
    assert sno["status"] == "snoozed"
    assert sno["snooze_until"] == tomorrow
    assert sno["category"] == "dead"
    assert sno["excess_units_at_decision"] == EXCESS_UNITS[SNO_ITEM]
    assert sno["excess_units_now"] == EXCESS_UNITS[SNO_ITEM]
    assert datetime.fromisoformat(sno["decided_at"]) == datetime(
        2026, 7, 1, 10, 0, 0, tzinfo=UTC
    )

    # 4. $filter=decided_at gt <a timestamp between two decisions> keeps
    #    only the later ones.
    response = client.get(
        "/odata/ExcessDecisions",
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
        "/odata/ExcessDecisions",
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
        "/odata/ExcessDecisions",
        params={"$filter": f"as_of gt {AS_OF}"},
        headers=good,
    )
    assert response.status_code == 200
    assert response.json()["value"] == []

    # 5. The same $orderby, $top, $skip and $format options as the other
    #    sets.
    response = client.get(
        "/odata/ExcessDecisions",
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
            "/odata/ExcessDecisions", params={"$orderby": value}, headers=good
        )
        assert response.status_code == 400
        assert response.json() == UNSUPPORTED_BODY

    def walk(params: dict) -> list:
        pages: list[list[dict]] = []
        response = client.get("/odata/ExcessDecisions", params=params, headers=good)
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
    response = client.get("/odata/ExcessDecisions", params={"$skip": "2"}, headers=good)
    assert response.status_code == 200
    assert response.json()["value"] == [out, sno]
    response = client.get(
        "/odata/ExcessDecisions", params={"$format": "json"}, headers=good
    )
    assert response.status_code == 200
    assert response.json()["value"] == [acc, dis, out, sno]
    for params in ({"$select": "item_code"}, {"$top": "0"}, {"$skip": "-1"}):
        response = client.get("/odata/ExcessDecisions", params=params, headers=good)
        assert response.status_code == 400

    # 6. The dead-and-excess ranked rows are computed once per request.
    calls = 0
    real_ranked = odata.excess_ranked

    def counting_ranked(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_ranked(*args, **kwargs)

    odata.excess_ranked = counting_ranked
    try:
        response = client.get("/odata/ExcessDecisions", headers=good)
        assert response.status_code == 200
        assert response.json()["value"] == [acc, dis, out, sno]
    finally:
        odata.excess_ranked = real_ranked
    assert calls == 1

    # 7. The other five sets are unchanged: the Excess set reflects the
    #    current dead-and-excess rows (ACC left the list after its stock
    #    reduction) and the Decisions set still has no rows of its own.
    response = client.get("/odata/Excess", headers=good)
    assert response.status_code == 200, response.text
    excess_rows = response.json()["value"]
    assert [row["item_code"] for row in excess_rows] == [
        DIS_ITEM,
        OUT_ITEM,
        SNO_ITEM,
    ]
    out_excess = next(row for row in excess_rows if row["item_code"] == OUT_ITEM)
    assert out_excess["excess_units"] == 90.0
    response = client.get("/odata/Decisions", headers=good)
    assert response.status_code == 200
    assert response.json()["value"] == []

    # 8. The route answers 401 with the realm without the machine
    #    credential; a valid user Bearer token alone is not one.
    with TestClient(app) as open_client:
        missing = open_client.get("/odata/ExcessDecisions")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == REALM
    bearer = client.get("/odata/ExcessDecisions")
    assert bearer.status_code == 401
    assert bearer.headers["www-authenticate"] == REALM
    wrong = client.get(
        "/odata/ExcessDecisions",
        headers={"Authorization": _basic(ODATA_USER, "wrong-password")},
    )
    assert wrong.status_code == 401
    assert wrong.headers["www-authenticate"] == REALM

    # 9. Nothing in the routes changes data.
    assert _row_counts() == counts
