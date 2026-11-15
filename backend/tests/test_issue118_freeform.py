"""Issue #118 proof: the OData ranked collections.

``backend/app/api/odata.py`` serves ``GET /odata/Drift``,
``GET /odata/Excess`` and ``GET /odata/Stockout`` behind the same HTTP
Basic machine credential as the slice-1 service document and
$metadata, each returning ``{"@odata.context": "$metadata#<Set>",
"value": [...]}`` where every row is the matching ranked row
(``drift_ranked`` / ``excess_ranked`` / ``stockout_ranked``)
restricted to exactly the properties the set's EntityType in
$metadata declares — list fields such as ``notes`` joined into one
string with ``"; "``, and the rows' ``as_of`` included on every row.

Paging and ordering:
  * ``$top`` (1 to 1000, default 1000) and ``$skip`` (0 or more,
    default 0) page the rows; while rows remain the answer adds
    ``@odata.nextLink`` — the same URL with ``$skip`` advanced by the
    page size, on the same origin — and the last page carries no
    link,
  * the rows are ordered by the full key tuple (item_code, site_code)
    when ``$orderby`` is omitted (and that order is not the ranked
    order, so the re-ordering is real), and ``$orderby`` accepts a
    comma-separated list of declared property names each with an
    optional ``asc`` and sorts by it (anything else, including
    ``desc``, answers 400),
  * ``$format=json`` is accepted and ignored.

Errors:
  * ``$select`` and ``$expand`` answer 400 with the exact body
    ``{"error": {"code": "BadRequest", "message":
    "unsupported query option"}}``, as does a ``$filter`` that is not
    one of the shapes issue #150 supports (the ``$filter=x`` probe
    below is such a shape); the supported ``$filter`` shapes are
    covered by ``test_issue150_freeform``,
  * an invalid ``$top`` or ``$skip`` value (empty, huge,
    non-numeric, signed, fractional, control characters, NUL bytes,
    unicode digits) answers 400 with the exact OData v4 error body —
    never FastAPI's default 422,
  * a ValueError from the ranked functions answers 400 as in
    ``/api/excess`` (triggered here through AIERP_SERVICE_LEVEL),
  * the routes answer 401 with the realm without the machine
    credential, and none of the requests changes any row.

The rows are seeded on fixed past calendar dates (every date in the
past, as-of 2026-06-30 — the latest demand event — so the default
as-of resolution is deterministic): three item-sites with 360 days of
steady 10/day demand and pooled 8/10/12-day lead times feed the
Drift and Stockout sets, and two dead item-sites (on hand, no
demand) feed the Excess set.
"""

import base64
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.recommend.drift import drift_ranked
from app.recommend.stockout import stockout_ranked

AS_OF_ISO = "2026-06-30"
FIRST_DAY = date(2025, 7, 6)  # 360 days before AS_OF
AS_OF_STAMP = "2026-06-30 00:00:00+00"

ODATA_USER = "odata-machine-118"
ODATA_PASSWORD = "feed-secret-118"
REALM = 'Basic realm="AIERP OData"'

SETS = ("Drift", "Excess", "Stockout")

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
SERVICE_LEVEL_DETAIL = (
    "AIERP_SERVICE_LEVEL must be a number strictly between 0.5 and 0.9999"
)

# The key-tuple (item_code, site_code) order of each set.  The ranked
# orders differ from these (drift: D2 first by money at stake;
# stockout: D1@A, D2@A, D1@B by p_stockout), which is what makes the
# default re-ordering and the $orderby checks meaningful.
EXPECTED_KEYS = {
    "Drift": [
        ("ITEM-D1", "SITE-A"),
        ("ITEM-D1", "SITE-B"),
        ("ITEM-D2", "SITE-A"),
    ],
    "Excess": [
        ("ITEM-E1", "SITE-B"),
        ("ITEM-E2", "SITE-A"),
    ],
    "Stockout": [
        ("ITEM-D1", "SITE-A"),
        ("ITEM-D1", "SITE-B"),
        ("ITEM-D2", "SITE-A"),
    ],
}


def _basic(user: str, password: str) -> str:
    """An HTTP Basic Authorization header value for user/password."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def _daily_demand(item_code: str, site_code: str) -> list[tuple]:
    """360 daily demand events of 10 units, FIRST_DAY through AS_OF."""
    return [
        (
            f"id-de-118-{item_code}-{site_code}-{day_index}",
            f"de-118-{item_code}-{site_code}-{day_index}",
            item_code,
            site_code,
            10.0,
            (FIRST_DAY + timedelta(days=day_index)).isoformat(),
            "shipment",
            "test",
        )
        for day_index in range(360)
    ]


def _seed() -> None:
    """The fixed-date rows for the three sets."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                _daily_demand("ITEM-D1", "SITE-A")
                + _daily_demand("ITEM-D1", "SITE-B")
                + _daily_demand("ITEM-D2", "SITE-A"),
            )
            # ITEM-D1 and ITEM-D2: 8, 10, 12 days -> mean 10, std 2.
            cur.executemany(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, ordered_on, "
                "received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-lt-118-d1-1",
                        "lt-118-d1-1",
                        "SUP-D1",
                        "ITEM-D1",
                        "2026-02-01",
                        "2026-02-09",
                        "test",
                    ),
                    (
                        "id-lt-118-d1-2",
                        "lt-118-d1-2",
                        "SUP-D1",
                        "ITEM-D1",
                        "2026-02-05",
                        "2026-02-15",
                        "test",
                    ),
                    (
                        "id-lt-118-d1-3",
                        "lt-118-d1-3",
                        "SUP-D1",
                        "ITEM-D1",
                        "2026-02-08",
                        "2026-02-20",
                        "test",
                    ),
                    (
                        "id-lt-118-d2-1",
                        "lt-118-d2-1",
                        "SUP-D2",
                        "ITEM-D2",
                        "2026-03-01",
                        "2026-03-09",
                        "test",
                    ),
                    (
                        "id-lt-118-d2-2",
                        "lt-118-d2-2",
                        "SUP-D2",
                        "ITEM-D2",
                        "2026-03-05",
                        "2026-03-15",
                        "test",
                    ),
                    (
                        "id-lt-118-d2-3",
                        "lt-118-d2-3",
                        "SUP-D2",
                        "ITEM-D2",
                        "2026-03-08",
                        "2026-03-20",
                        "test",
                    ),
                ],
            )
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, reorder_point, "
                "order_qty, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-is-118-d1a",
                        "ITEM-D1",
                        "SITE-A",
                        2.0,
                        150.0,
                        100.0,
                        "test",
                    ),
                    (
                        "id-is-118-d1b",
                        "ITEM-D1",
                        "SITE-B",
                        2.0,
                        150.0,
                        100.0,
                        "test",
                    ),
                    (
                        "id-is-118-d2a",
                        "ITEM-D2",
                        "SITE-A",
                        2.0,
                        200.0,
                        100.0,
                        "test",
                    ),
                    ("id-is-118-e1", "ITEM-E1", "SITE-B", 5.0, None, None, "test"),
                    ("id-is-118-e2", "ITEM-E2", "SITE-A", 5.0, None, None, "test"),
                ],
            )
            cur.executemany(
                "INSERT INTO on_hand "
                "(id, item_code, site_code, quantity_on_hand, as_of, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    ("id-oh-118-d1a", "ITEM-D1", "SITE-A", 60.0, AS_OF_STAMP, "test"),
                    ("id-oh-118-d1b", "ITEM-D1", "SITE-B", 80.0, AS_OF_STAMP, "test"),
                    ("id-oh-118-d2a", "ITEM-D2", "SITE-A", 70.0, AS_OF_STAMP, "test"),
                    ("id-oh-118-e1", "ITEM-E1", "SITE-B", 100.0, AS_OF_STAMP, "test"),
                    ("id-oh-118-e2", "ITEM-E2", "SITE-A", 40.0, AS_OF_STAMP, "test"),
                ],
            )
            # ITEM-D1/SITE-B: one counted line (due 2026-07-05, inside
            # the lead time window), one stale line (promised
            # 2026-03-01, more than 90 days before as-of) and one
            # undated line.
            cur.executemany(
                "INSERT INTO open_purchase_lines "
                "(id, business_key, item_code, site_code, supplier_code, "
                "order_type, open_units, promised_on, ordered_on, as_of, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-op-118-1",
                        "op-118-1",
                        "ITEM-D1",
                        "SITE-B",
                        "SUP-D1",
                        "RO",
                        30.0,
                        "2026-07-05",
                        "2026-06-20",
                        AS_OF_STAMP,
                        "test",
                    ),
                    (
                        "id-op-118-2",
                        "op-118-2",
                        "ITEM-D1",
                        "SITE-B",
                        "SUP-D1",
                        "RO",
                        40.0,
                        "2026-03-01",
                        "2026-02-01",
                        AS_OF_STAMP,
                        "test",
                    ),
                    (
                        "id-op-118-3",
                        "op-118-3",
                        "ITEM-D1",
                        "SITE-B",
                        "SUP-D1",
                        "RO",
                        25.0,
                        None,
                        "2026-06-15",
                        AS_OF_STAMP,
                        "test",
                    ),
                ],
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
        "on_hand",
        "open_purchase_lines",
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


def test_issue118_freeform(client, monkeypatch, user_token):
    monkeypatch.setenv("AIERP_ODATA_USER", ODATA_USER)
    monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
    good = {"Authorization": _basic(ODATA_USER, ODATA_PASSWORD)}

    from app.api.drift import DriftRow
    from app.api.excess import ExcessRow
    from app.api.stockout import StockoutRow
    from app.main import app

    declared = {
        "Drift": set(DriftRow.model_fields),
        "Excess": set(ExcessRow.model_fields),
        "Stockout": set(StockoutRow.model_fields),
    }
    _seed()
    counts = _row_counts()

    # 1. Each set returns its rows, in the full key-tuple order, with
    #    exactly the declared properties, as_of on every row, notes
    #    joined as a string, and no nextLink while no page is full.
    default_rows = {}
    for set_name in SETS:
        response = client.get(f"/odata/{set_name}", headers=good)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        body = response.json()
        assert body["@odata.context"] == f"$metadata#{set_name}"
        assert "@odata.nextLink" not in body
        rows = body["value"]
        assert [(row["item_code"], row["site_code"]) for row in rows] == (
            EXPECTED_KEYS[set_name]
        )
        for row in rows:
            assert set(row) == declared[set_name]
            assert row["as_of"] == AS_OF_ISO
            assert isinstance(row["notes"], str)
        default_rows[set_name] = rows

    # 2. The feed re-orders: the ranked order of the underlying sets is
    #    not the key-tuple order, so the default order really is the
    #    one the feed imposes (total and stable for paging).
    ranked_drift = [(row["item_code"], row["site_code"]) for row in drift_ranked()]
    ranked_stockout = [
        (row["item_code"], row["site_code"]) for row in stockout_ranked()
    ]
    assert ranked_drift != EXPECTED_KEYS["Drift"]
    assert ranked_stockout != EXPECTED_KEYS["Stockout"]
    assert [
        (row["item_code"], row["site_code"]) for row in default_rows["Drift"]
    ] == EXPECTED_KEYS["Drift"]
    assert [
        (row["item_code"], row["site_code"]) for row in default_rows["Stockout"]
    ] == EXPECTED_KEYS["Stockout"]

    # 3. Paging: $top=1 pages of one row, each nextLink (same origin,
    #    $skip advanced by the page size) returns the next row, and
    #    the last page carries no further link.
    for set_name in SETS:
        first = client.get(f"/odata/{set_name}", params={"$top": "1"}, headers=good)
        assert first.status_code == 200
        body = first.json()
        assert body["value"] == [default_rows[set_name][0]]
        assert body["@odata.nextLink"] == (
            f"http://testserver/odata/{set_name}?$top=1&$skip=1"
        )
        cursor = body["@odata.nextLink"]
        seen = 1
        while cursor:
            page = client.get(cursor, headers=good)
            assert page.status_code == 200
            page_body = page.json()
            assert page_body["value"] == [default_rows[set_name][seen]]
            cursor = page_body.get("@odata.nextLink")
            seen += 1
        assert seen == len(default_rows[set_name])

    # 4. $orderby sorts by the listed declared properties.
    for set_name in SETS:
        ordered = client.get(
            f"/odata/{set_name}",
            params={"$orderby": "item_code asc,site_code asc"},
            headers=good,
        )
        assert ordered.status_code == 200
        assert [
            (row["item_code"], row["site_code"]) for row in ordered.json()["value"]
        ] == EXPECTED_KEYS[set_name]
    site_first = client.get(
        "/odata/Drift",
        params={"$orderby": "site_code asc,item_code asc"},
        headers=good,
    )
    assert site_first.status_code == 200
    assert [
        (row["item_code"], row["site_code"]) for row in site_first.json()["value"]
    ] == [
        ("ITEM-D1", "SITE-A"),
        ("ITEM-D2", "SITE-A"),
        ("ITEM-D1", "SITE-B"),
    ]
    # A $orderby page follows the same rules and its nextLink carries
    # the option forward (percent-encoded) on the same origin.
    paged = client.get(
        "/odata/Stockout",
        params={"$orderby": "item_code asc", "$top": "1"},
        headers=good,
    )
    assert paged.status_code == 200
    paged_body = paged.json()
    assert paged_body["value"] == [default_rows["Stockout"][0]]
    assert paged_body["@odata.nextLink"] == (
        "http://testserver/odata/Stockout?$orderby=item_code%20asc" "&$top=1&$skip=1"
    )
    second = client.get(paged_body["@odata.nextLink"], headers=good)
    assert second.status_code == 200
    assert second.json()["value"] == [default_rows["Stockout"][1]]
    # $orderby on the list field sorts by the value the set's
    # EntityType in $metadata declares for it — the single
    # "; "-joined Edm.String the response exposes — with the key
    # tuple breaking ties.
    for set_name in SETS:
        notes_first = client.get(
            f"/odata/{set_name}",
            params={"$orderby": "notes asc"},
            headers=good,
        )
        assert notes_first.status_code == 200
        assert notes_first.json()["value"] == sorted(
            default_rows[set_name],
            key=lambda row: (
                row["notes"],
                row["item_code"],
                row["site_code"],
            ),
        )
    # The ordering itself sorts by the joined string, not the raw
    # list: as lists ["a", "b"] < ["a;"], but as the joined
    # Edm.String values "a;" < "a; b", so the two orders differ.
    from app.api.odata import _ordered_rows

    raw_notes = [
        {"item_code": "ITEM-Z", "site_code": "SITE-Z", "notes": ["a", "b"]},
        {"item_code": "ITEM-A", "site_code": "SITE-Z", "notes": ["a;"]},
    ]
    ordered_notes = _ordered_rows(raw_notes, StockoutRow, ["notes"])
    assert [row["notes"] for row in ordered_notes] == ["a;", "a; b"]
    assert [(row["item_code"], row["site_code"]) for row in ordered_notes] == [
        ("ITEM-A", "SITE-Z"),
        ("ITEM-Z", "SITE-Z"),
    ]
    ordered_keys = _ordered_rows(raw_notes, StockoutRow, None)
    assert [row["item_code"] for row in ordered_keys] == ["ITEM-A", "ITEM-Z"]

    # 5. $select and $expand are unsupported, and so is a $filter that
    #    is not one of the shapes issue #150 supports (x is not).
    for option in ("$filter", "$select", "$expand"):
        response = client.get("/odata/Drift", params={option: "x"}, headers=good)
        assert response.status_code == 400
        assert response.json() == UNSUPPORTED_BODY
    # Unknown options and invalid $orderby shapes are unsupported too.
    for params in (
        {"$count": "true"},
        {"$orderby": "item_code desc"},
        {"$orderby": "item_code DESC"},
        {"$orderby": "unknown_property asc"},
        {"$orderby": "item_code asc desc"},
        {"$orderby": "item_code,,site_code"},
        {"$orderby": "item_code asc,"},
    ):
        response = client.get("/odata/Excess", params=params, headers=good)
        assert response.status_code == 400
        assert response.json() == UNSUPPORTED_BODY

    # 6. $format=json is accepted and ignored.
    for set_name in SETS:
        response = client.get(
            f"/odata/{set_name}", params={"$format": "json"}, headers=good
        )
        assert response.status_code == 200
        assert response.json() == {
            "@odata.context": f"$metadata#{set_name}",
            "value": default_rows[set_name],
        }

    # 7. Invalid $top and $skip values: 400 with the exact OData v4
    #    error body — never FastAPI's default 422.
    for raw_top in (
        "0",
        "1001",
        "abc",
        "",
        "1.5",
        "-1",
        "+5",
        "1_0",
        " 5",
        "1e3",
        "0x5",
        "\u0000",
        "\u00015",
        "9" * 10_000,
        "\u0665",  # arabic-indic digit five
    ):
        for set_name in SETS:
            response = client.get(
                f"/odata/{set_name}", params={"$top": raw_top}, headers=good
            )
            assert response.status_code == 400
            assert response.status_code != 422
            assert response.json() == BAD_TOP_BODY
    for raw_skip in ("-1", "abc", "", "1.5", " 5", "-0", "\u0000"):
        response = client.get(
            "/odata/Stockout", params={"$skip": raw_skip}, headers=good
        )
        assert response.status_code == 400
        assert response.status_code != 422
        assert response.json() == BAD_SKIP_BODY
    # The boundaries are accepted.
    for top in ("1", "1000"):
        response = client.get("/odata/Drift", params={"$top": top}, headers=good)
        assert response.status_code == 200
    response = client.get("/odata/Drift", params={"$skip": "0"}, headers=good)
    assert response.status_code == 200
    assert response.json()["value"] == default_rows["Drift"]
    response = client.get("/odata/Drift", params={"$skip": "999999"}, headers=good)
    assert response.status_code == 200
    assert response.json()["value"] == []
    # A digit string far beyond any page is still a valid $skip: it
    # skips past every row (never a 500 from the huge integer).
    response = client.get(
        "/odata/Stockout", params={"$skip": "9" * 10_000}, headers=good
    )
    assert response.status_code == 200
    assert response.json() == {
        "@odata.context": "$metadata#Stockout",
        "value": [],
    }
    # Leading zeros carry no weight: the same value spelled with zero
    # padding parses identically — no 400, no 422, the same page.
    for set_name in SETS:
        padded = client.get(
            f"/odata/{set_name}", params={"$top": "000000001"}, headers=good
        )
        assert padded.status_code == 200
        assert padded.status_code != 422
        padded_body = padded.json()
        assert padded_body["value"] == [default_rows[set_name][0]]
        assert padded_body["@odata.nextLink"] == (
            f"http://testserver/odata/{set_name}?$top=000000001&$skip=1"
        )
        follow = client.get(padded_body["@odata.nextLink"], headers=good)
        assert follow.status_code == 200
        assert follow.json()["value"] == [default_rows[set_name][1]]
    wide_top = client.get("/odata/Drift", params={"$top": "007"}, headers=good)
    assert wide_top.status_code == 200
    wide_top_body = wide_top.json()
    assert wide_top_body["value"] == default_rows["Drift"]
    assert "@odata.nextLink" not in wide_top_body
    padded_skip = client.get(
        "/odata/Drift", params={"$skip": "000000000000"}, headers=good
    )
    assert padded_skip.status_code == 200
    assert padded_skip.status_code != 422
    assert padded_skip.json() == {
        "@odata.context": "$metadata#Drift",
        "value": default_rows["Drift"],
    }
    padded_skip_one = client.get("/odata/Drift", params={"$skip": "001"}, headers=good)
    assert padded_skip_one.status_code == 200
    assert padded_skip_one.json()["value"] == default_rows["Drift"][1:]
    # Zero padding never disguises an out-of-range value either.
    for set_name in SETS:
        response = client.get(
            f"/odata/{set_name}", params={"$top": "0000000001001"}, headers=good
        )
        assert response.status_code == 400
        assert response.status_code != 422
        assert response.json() == BAD_TOP_BODY
    # …and a huge zero-padded $skip still skips past every row (no 500).
    response = client.get(
        "/odata/Stockout", params={"$skip": "0" + "9" * 10_000}, headers=good
    )
    assert response.status_code == 200
    assert response.json()["value"] == []

    # 8. Without the machine credential the routes answer 401 with the
    #    realm, as does a wrong password or a user Bearer token.
    with TestClient(app) as anonymous:
        for set_name in SETS:
            response = anonymous.get(f"/odata/{set_name}")
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == REALM
    wrong = client.get(
        "/odata/Excess",
        headers={"Authorization": _basic(ODATA_USER, "wrong-password")},
    )
    assert wrong.status_code == 401
    assert wrong.headers["www-authenticate"] == REALM
    bearer = client.get(
        "/odata/Stockout", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert bearer.status_code == 401
    assert bearer.headers["www-authenticate"] == REALM

    # 9. A ValueError from the ranked functions answers 400 as in
    #    /api/excess (invalid AIERP_SERVICE_LEVEL).
    monkeypatch.setenv("AIERP_SERVICE_LEVEL", "not-a-number")
    for set_name in SETS:
        response = client.get(f"/odata/{set_name}", headers=good)
        assert response.status_code == 400
        assert response.json() == {"detail": SERVICE_LEVEL_DETAIL}

    # 10. Nothing in the routes changes data.
    assert _row_counts() == counts
