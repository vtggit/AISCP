"""Issue #188 proof: the fifth OData entity set, ``Decisions``.

``backend/app/api/odata.py`` publishes the planner decisions set under
``/odata``, after Drift, Excess, Stockout and Changes, behind the same
HTTP Basic machine credential:

* ``GET /odata/`` lists the eight entity sets in that order and
  ``GET /odata/$metadata`` declares the ``Decisions`` EntityType
  keyed by ``item_code`` and ``site_code`` with exactly:
  ``decision``, ``status``, ``reason``, ``snooze_until`` and
  ``as_of`` (the two dates ``Edm.Date``),
  ``recommended_reorder_point_units`` (``Edm.Int32``),
  ``reorder_point_at_decision``, ``erp_reorder_point`` and
  ``money_at_stake`` (``Edm.Double``) and ``decided_at``
  (``Edm.DateTimeOffset``) — and no ``decided_by``,
* ``GET /odata/Decisions`` returns one row per item-site that has a
  planner decision: the item-site's current decision (the latest by
  ``decided_at``, ``id`` breaking ties, exactly what
  ``GET /api/drift/decisions`` reports as current) with ``status``
  ``applied`` for an accepted decision whose item-site's current
  ``item_sites.reorder_point`` is not null and within 1 of
  ``recommended_reorder_point_units``, ``outstanding`` for every
  other accepted decision, and the decision itself otherwise;
  ``reorder_point_at_decision`` is the stored
  ``current_reorder_point`` and ``erp_reorder_point`` the item-site's
  current ``item_sites.reorder_point`` (null when the item-site row
  is missing — such an accepted decision is ``outstanding``),
* an item-site dismissed after it was accepted shows only its
  dismissal, with status ``dismissed``,
* the set supports the same ``$top``, ``$skip``, ``$orderby``,
  ``$format`` and date ``$filter`` options as the other sets
  (``$filter=decided_at gt <timestamp>`` keeps only the later
  decisions, as do the date filters),
* the rows are read with a fixed number of queries whatever the
  number of item-sites, the route answers 401 with the realm without
  the machine credential, nothing changes data, and the other four
  sets are unchanged.

Six item-sites are seeded on fixed past dates: 10/day demand from
2025-09-01 through 2026-08-31 (the latest demand event, so the
default as-of is 2026-08-31), pooled 9/10/10-day lead times and a
reorder point above the recommended ~106.16 units (whole-unit
reorder_point_units 107 for every site), so every site has an
"over" drift row; one of the six has no item_sites row at all, so
its ``erp_reorder_point`` is null.  The decisions are recorded through
``POST /api/drift/decisions``; their stored ``decided_at`` values are
then moved to fixed past timestamps (the append-only rows are the
planner's record) so the ``decided_at`` filter is deterministic.
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
SITE_CODE = "SITE-188"
FIRST_DAY = date(2025, 9, 1)
DEMAND_DAYS = 365  # 10/day through 2026-08-31
AS_OF = "2026-08-31"  # the latest seeded demand event
UNIT_COST = 2.0
RECOMMENDED_UNITS = 107  # whole-unit recommended reorder point
DECIDED_BY = "dev-admin-1"
ODATA_USER = "odata-machine-188"
ODATA_PASSWORD = "feed-secret-188"
REALM = 'Basic realm="AIERP OData"'

# item_code -> current reorder point; every site sits above the
# recommended point, so every site has an "over" drift row.
ITEMS = {
    "A-188": 210.0,  # accepted; the point is then set to the recommended value: applied
    "B-188": 200.0,  # accepted and left unchanged: outstanding
    "C-188": 190.0,  # accepted, then dismissed: shows the dismissal only
    "D-188": 180.0,  # snoozed: status snoozed
    "E-188": 170.0,  # undecided: no row in the set
}
# Accepted, but with no item_sites row: erp_reorder_point null,
# outstanding.
NO_ITEM_SITE = "F-188"

# The exact property name -> EDM type map the Decisions EntityType must
# declare (key properties first in the document, the rest in the
# row's order).
DECISION_PROPERTIES = {
    "item_code": "Edm.String",
    "site_code": "Edm.String",
    "decision": "Edm.String",
    "status": "Edm.String",
    "reason": "Edm.String",
    "snooze_until": "Edm.Date",
    "as_of": "Edm.Date",
    "recommended_reorder_point_units": "Edm.Int32",
    "reorder_point_at_decision": "Edm.Double",
    "erp_reorder_point": "Edm.Double",
    "money_at_stake": "Edm.Double",
    "decided_at": "Edm.DateTimeOffset",
}
NULLABLE_PROPERTIES = {
    "reason",
    "snooze_until",
    "reorder_point_at_decision",
    "erp_reorder_point",
    "money_at_stake",
}

UNSUPPORTED_BODY = {
    "error": {"code": "BadRequest", "message": "unsupported query option"}
}

# The fixed past timestamps the stored decisions are moved to.
DECIDED_AT = {
    "F-188": "2026-09-01T09:00:00+00:00",
    "B-188": "2026-09-01T10:00:00+00:00",
    "D-188": "2026-09-01T12:00:00+00:00",
    "C-188-accepted": "2026-09-02T10:00:00+00:00",
    "C-188-dismissed": "2026-09-05T10:00:00+00:00",
    "A-188": "2026-09-10T10:00:00+00:00",
}


def _basic(user: str, password: str) -> str:
    """An HTTP Basic Authorization header value for user/password."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def _seed() -> None:
    """The fixed-date rows: five item-sites, one drift row each."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            demand_rows = []
            lead_time_rows = []
            item_site_rows = []
            for item_code in (*ITEMS, NO_ITEM_SITE):
                reorder_point = ITEMS.get(item_code)
                suffix = item_code.split("-")[0].lower()
                demand_rows.extend(
                    (
                        f"id-de-188-{suffix}-{day_index}",
                        f"de-188-{suffix}-{day_index}",
                        item_code,
                        SITE_CODE,
                        10.0,
                        (FIRST_DAY + timedelta(days=day_index)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for day_index in range(DEMAND_DAYS)
                )
                lead_time_rows.extend(
                    (
                        f"id-lt-188-{suffix}-{index}",
                        f"lt-188-{suffix}-{index}",
                        f"SUP-188-{suffix}",
                        item_code,
                        ordered.isoformat(),
                        received.isoformat(),
                        "test",
                    )
                    for index, (ordered, received) in enumerate(
                        (
                            (date(2026, 5, 1), date(2026, 5, 10)),  # 9 days
                            (date(2026, 5, 5), date(2026, 5, 15)),  # 10 days
                            (date(2026, 5, 9), date(2026, 5, 19)),  # 10 days
                        ),
                        start=1,
                    )
                )
                if reorder_point is not None:
                    item_site_rows.append(
                        (
                            f"id-is-188-{suffix}",
                            item_code,
                            SITE_CODE,
                            UNIT_COST,
                            reorder_point,
                            80.0,
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
    response = client.post("/api/drift/decisions", json=body)
    assert response.status_code == 201, (item_code, response.status_code, response.text)
    row = response.json()
    assert row["item_code"] == item_code
    assert row["site_code"] == SITE_CODE
    assert row["decision"] == decision
    assert row["as_of"] == AS_OF
    assert row["recommended_reorder_point_units"] == RECOMMENDED_UNITS
    assert row["current_reorder_point"] == ITEMS.get(item_code)
    assert row["decided_by"] == DECIDED_BY
    return row


def _move_decided_at(decision_id: str, decided_at: str) -> None:
    """Move a stored decision's timestamp (the append-only rows are the
    planner's record; the proof simulates the clock)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE planner_decisions SET decided_at = %s WHERE id = %s",
                (decided_at, decision_id),
            )
            assert cur.rowcount == 1, f"no decision {decision_id} to move"
        conn.commit()
    finally:
        conn.close()


def _set_reorder_point(item_code: str, reorder_point: float) -> None:
    """Set the item-site's current item_sites.reorder_point."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE item_sites SET reorder_point = %s "
                "WHERE item_code = %s AND site_code = %s",
                (reorder_point, item_code, SITE_CODE),
            )
            assert cur.rowcount == 1, f"no item_sites row for {item_code}"
        conn.commit()
    finally:
        conn.close()


def _row_counts() -> dict[str, int]:
    """The row counts of every table the routes read or write."""
    tables = (
        "demand_events",
        "lead_time_observations",
        "item_sites",
        "planner_decisions",
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


def test_issue188_freeform(client, admin_token, monkeypatch):
    monkeypatch.setenv("AIERP_ODATA_USER", ODATA_USER)
    monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
    good = {"Authorization": _basic(ODATA_USER, ODATA_PASSWORD)}
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    _seed()

    # -- Decisions recorded through POST /api/drift/decisions. ---------
    accepted_a = _post_decision(admin_client, "A-188", "accepted", reason="looks right")
    accepted_b = _post_decision(admin_client, "B-188", "accepted")
    accepted_c = _post_decision(
        admin_client, "C-188", "accepted", reason="seems plausible"
    )
    dismissed_c = _post_decision(
        admin_client, "C-188", "dismissed", reason="revisit next quarter"
    )
    snoozed_d = _post_decision(admin_client, "D-188", "snoozed", snooze_until=tomorrow)
    assert snoozed_d["snooze_until"] == tomorrow
    accepted_f = _post_decision(admin_client, NO_ITEM_SITE, "accepted")

    # -- The stored decisions move to fixed past timestamps. -----------
    _move_decided_at(accepted_f["id"], DECIDED_AT["F-188"])
    _move_decided_at(accepted_b["id"], DECIDED_AT["B-188"])
    _move_decided_at(snoozed_d["id"], DECIDED_AT["D-188"])
    _move_decided_at(accepted_c["id"], DECIDED_AT["C-188-accepted"])
    _move_decided_at(dismissed_c["id"], DECIDED_AT["C-188-dismissed"])
    _move_decided_at(accepted_a["id"], DECIDED_AT["A-188"])

    # -- A's item-site reorder point is then set to the recommended
    #    value: its accepted decision becomes applied. ------------------
    _set_reorder_point("A-188", float(RECOMMENDED_UNITS))

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

    # 2. $metadata declares the Decisions EntityType, fifth, keyed by
    #    item_code and site_code, with exactly the properties above and
    #    no decided_by.
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
    decisions = next(
        element for element in entity_types if element.get("Name") == "Decisions"
    )
    key_refs = [
        ref.get("Name")
        for ref in decisions.findall(f"{{{EDM}}}Key/{{{EDM}}}PropertyRef")
    ]
    assert key_refs == ["item_code", "site_code"]
    properties = {
        prop.get("Name"): prop for prop in decisions.findall(f"{{{EDM}}}Property")
    }
    assert {
        name: prop.get("Type") for name, prop in properties.items()
    } == DECISION_PROPERTIES
    assert "decided_by" not in properties
    assert [
        prop.get("Name") for prop in decisions.findall(f"{{{EDM}}}Property")
    ] == list(DECISION_PROPERTIES)
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
    #    order; the undecided site has no row.
    response = client.get("/odata/Decisions", headers=good)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body["@odata.context"] == "$metadata#Decisions"
    rows = body["value"]
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        (code, SITE_CODE) for code in ("A-188", "B-188", "C-188", "D-188", NO_ITEM_SITE)
    ]
    for row in rows:
        assert set(row) == set(DECISION_PROPERTIES), row
    by_code = {row["item_code"]: row for row in rows}
    assert "E-188" not in by_code  # undecided: no row in the set

    # A: accepted and the point set to the recommended value: applied.
    a = by_code["A-188"]
    assert a["decision"] == "accepted"
    assert a["status"] == "applied"
    assert a["reason"] == "looks right"
    assert a["snooze_until"] is None
    assert a["as_of"] == AS_OF
    assert a["recommended_reorder_point_units"] == RECOMMENDED_UNITS
    assert a["reorder_point_at_decision"] == ITEMS["A-188"]  # the snapshot
    assert a["erp_reorder_point"] == float(RECOMMENDED_UNITS)  # the current point
    assert a["money_at_stake"] == accepted_a["money_at_stake"]
    assert datetime.fromisoformat(a["decided_at"]) == datetime(
        2026, 9, 10, 10, 0, 0, tzinfo=UTC
    )

    # B: accepted and left unchanged (200 vs the recommended 107):
    # outstanding.
    b = by_code["B-188"]
    assert b["decision"] == "accepted"
    assert b["status"] == "outstanding"
    assert b["reason"] is None
    assert b["reorder_point_at_decision"] == ITEMS["B-188"]
    assert b["erp_reorder_point"] == ITEMS["B-188"]
    assert b["money_at_stake"] == accepted_b["money_at_stake"]
    assert datetime.fromisoformat(b["decided_at"]) == datetime(
        2026, 9, 1, 10, 0, 0, tzinfo=UTC
    )

    # C: dismissed after it was accepted: only the dismissal, with
    # status dismissed.
    c = by_code["C-188"]
    assert c["decision"] == "dismissed"
    assert c["status"] == "dismissed"
    assert c["reason"] == "revisit next quarter"
    assert c["snooze_until"] is None
    assert c["reorder_point_at_decision"] == ITEMS["C-188"]
    assert c["erp_reorder_point"] == ITEMS["C-188"]
    assert c["money_at_stake"] == dismissed_c["money_at_stake"]
    assert datetime.fromisoformat(c["decided_at"]) == datetime(
        2026, 9, 5, 10, 0, 0, tzinfo=UTC
    )

    # D: snoozed: the status is the decision itself.
    d = by_code["D-188"]
    assert d["decision"] == "snoozed"
    assert d["status"] == "snoozed"
    assert d["snooze_until"] == tomorrow
    assert d["reorder_point_at_decision"] == ITEMS["D-188"]
    assert d["erp_reorder_point"] == ITEMS["D-188"]
    assert d["money_at_stake"] == snoozed_d["money_at_stake"]
    assert datetime.fromisoformat(d["decided_at"]) == datetime(
        2026, 9, 1, 12, 0, 0, tzinfo=UTC
    )

    # F: accepted with no item_sites row: erp_reorder_point null,
    # outstanding, and the snapshot's current point null too.
    f = by_code[NO_ITEM_SITE]
    assert f["decision"] == "accepted"
    assert f["status"] == "outstanding"
    assert f["reorder_point_at_decision"] is None
    assert f["erp_reorder_point"] is None
    assert f["money_at_stake"] is None
    assert datetime.fromisoformat(f["decided_at"]) == datetime(
        2026, 9, 1, 9, 0, 0, tzinfo=UTC
    )

    # 4. The rows are the current decisions exactly as
    #    GET /api/drift/decisions reports them.
    current_api = client.get("/api/drift/decisions", params={"limit": 1000})
    assert current_api.status_code == 200, current_api.text
    api_rows = {(row["item_code"], row["site_code"]): row for row in current_api.json()}
    assert set(api_rows) == {
        (code, SITE_CODE) for code in ("A-188", "B-188", "C-188", "D-188", NO_ITEM_SITE)
    }
    for code, row in by_code.items():
        api_row = api_rows[(code, SITE_CODE)]
        assert row["decision"] == api_row["decision"]
        assert row["reason"] == api_row["reason"]
        assert row["snooze_until"] == api_row["snooze_until"]
        assert row["as_of"] == api_row["as_of"]
        assert (
            row["recommended_reorder_point_units"]
            == api_row["recommended_reorder_point_units"]
        )
        assert row["reorder_point_at_decision"] == api_row["current_reorder_point"]
        assert row["money_at_stake"] == api_row["money_at_stake"]
        assert datetime.fromisoformat(row["decided_at"]) == datetime.fromisoformat(
            api_row["decided_at"]
        )

    # 5. $filter on decided_at (Edm.DateTimeOffset): the timestamp sits
    #    between C's accept (09-02) and C's dismissal (09-05), so only
    #    the later decisions are kept.
    response = client.get(
        "/odata/Decisions",
        params={"$filter": "decided_at gt 2026-09-03T00:00:00Z"},
        headers=good,
    )
    assert response.status_code == 200, response.text
    filtered = response.json()["value"]
    assert [row["item_code"] for row in filtered] == ["A-188", "C-188"]
    assert {row["status"] for row in filtered} == {"applied", "dismissed"}
    # A date filter works on the Edm.Date properties too: only D's
    # snooze runs past today.
    response = client.get(
        "/odata/Decisions",
        params={"$filter": f"snooze_until gt {today}"},
        headers=good,
    )
    assert response.status_code == 200, response.text
    assert [row["item_code"] for row in response.json()["value"]] == ["D-188"]

    # 6. The same $top / $skip / $orderby / $format options as the
    #    other sets.
    response = client.get(
        "/odata/Decisions", params={"$orderby": "decided_at"}, headers=good
    )
    assert response.status_code == 200, response.text
    assert [row["item_code"] for row in response.json()["value"]] == [
        NO_ITEM_SITE,
        "B-188",
        "D-188",
        "C-188",
        "A-188",
    ]
    for value in ("decided_at desc", "not_a_property asc"):
        response = client.get(
            "/odata/Decisions", params={"$orderby": value}, headers=good
        )
        assert response.status_code == 400
        assert response.json() == UNSUPPORTED_BODY
    response = client.get("/odata/Decisions", params={"$top": "1"}, headers=good)
    assert response.status_code == 200
    body = response.json()
    assert [row["item_code"] for row in body["value"]] == ["A-188"]
    assert "@odata.nextLink" in body
    follow = client.get(body["@odata.nextLink"], headers=good)
    assert follow.status_code == 200
    # The page size carries forward: the next page is B alone.
    assert [row["item_code"] for row in follow.json()["value"]] == ["B-188"]
    response = client.get("/odata/Decisions", params={"$skip": "1"}, headers=good)
    assert response.status_code == 200
    assert [row["item_code"] for row in response.json()["value"]] == [
        "B-188",
        "C-188",
        "D-188",
        NO_ITEM_SITE,
    ]
    response = client.get("/odata/Decisions", params={"$skip": "4"}, headers=good)
    assert response.status_code == 200
    assert [row["item_code"] for row in response.json()["value"]] == [NO_ITEM_SITE]
    response = client.get("/odata/Decisions", params={"$format": "json"}, headers=good)
    assert response.status_code == 200
    assert len(response.json()["value"]) == 5
    response = client.get(
        "/odata/Decisions", params={"$select": "decision"}, headers=good
    )
    assert response.status_code == 400
    assert response.json() == UNSUPPORTED_BODY

    # 7. The rows are read with a fixed number of queries: one
    #    repository read (one query) per request, whatever the row
    #    count.
    original = odata._repo.current_decisions
    calls = {"count": 0}

    def counting_current_decisions():
        calls["count"] += 1
        return original()

    monkeypatch.setattr(odata._repo, "current_decisions", counting_current_decisions)
    response = client.get("/odata/Decisions", headers=good)
    assert response.status_code == 200
    assert len(response.json()["value"]) == 5
    assert calls["count"] == 1
    response = client.get("/odata/Decisions", params={"$top": "1"}, headers=good)
    assert response.status_code == 200
    assert calls["count"] == 2

    # 8. The route answers 401 with the realm without the machine
    #    credential; a user Bearer token is not a machine credential.
    with TestClient(app) as open_client:
        missing = open_client.get("/odata/Decisions")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == REALM
    bearer = client.get("/odata/Decisions")
    assert bearer.status_code == 401
    assert bearer.headers["www-authenticate"] == REALM

    # 9. The other four sets are unchanged and still served.
    drift = client.get("/odata/Drift", headers=good)
    assert drift.status_code == 200
    assert [row["item_code"] for row in drift.json()["value"]] == [
        *ITEMS,
        NO_ITEM_SITE,
    ]

    # 10. Nothing in the routes changes data.
    assert _row_counts() == counts
