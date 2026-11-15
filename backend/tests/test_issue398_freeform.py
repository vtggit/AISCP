"""Issue #398 proof: OdooClient.read_demand_events with the server fully mocked.

With the Odoo server fully mocked (httpx.MockTransport answers every
/jsonrpc POST, so no network), the transport is stubbed so each model's
search_read is answered by this test. It checks:

  * two done customer-bound moves of different products and warehouses
    becoming two rows, each shaped for DemandEventUpsert with
    business_key the namespaced identifier odoo:{db}:stock.move:{move_id}
    ({db} the configured Odoo database identifier, {move_id} the
    stock.move id), item_code the product's default_code, site_code the
    source location's warehouse code, event_type "shipment",
    source_system "odoo", and occurred_on the date part of the move's
    date;
  * a move's quantity carried as a positive float (a negative value
    stored as its absolute value), and each move kept as its own row —
    two moves of the same product and site are NOT summed, unlike
    on-hand;
  * a move whose product has no default_code, one whose location
    resolves to a warehouse with no code (and to no warehouse at all),
    one with a zero or non-numeric quantity, one with an unparseable or
    missing date, and one with no id, all skipped with no error;
  * the domain sent to stock.move requiring state = done and a customer
    destination, the product_id/location_id/quantity/date fields, and
    the product/location/warehouse reads each issued once over the ids
    seen (an id in [...] domain), not once per move;
  * search_read accepting an order parameter and forwarding it verbatim
    (the demand reader invokes it with order='id asc' on every read, so
    offset paging is stable), omitting it from the wire shape when not
    given, and a malformed order raising before any request;
  * no moves at all returning [] with no product/location/warehouse
    reads, and the method performing reads only (the mock fails loudly
    on any non-search_read call, so no write can slip through).
"""

import json

import httpx

from app.feed.odoo_client import OdooClient
from app.models.demand_events import DemandEventUpsert

BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"

MOVES = [
    # Kept: product 11 (P-1), source location 101 -> warehouse 21
    # (SITE-A). A negative quantity is stored as its absolute value.
    {
        "id": 501,
        "product_id": 11,
        "location_id": 101,
        "quantity": -3.5,
        "date": "2026-10-12 09:15:00",
    },
    # Kept: product 12 (P-2), source location 103 -> warehouse 22
    # (SITE-B). A T-separated datetime; only the date part is kept.
    {
        "id": 502,
        "product_id": 12,
        "location_id": 103,
        "quantity": 2,
        "date": "2026-10-14T17:45:30",
    },
    # Kept: same product and site as move 501 — a second, separate row;
    # demand moves are never summed (unlike on-hand).
    {
        "id": 503,
        "product_id": 11,
        "location_id": 101,
        "quantity": 3.5,
        "date": "2026-10-12",
    },
    # Skipped, no error: product 13 has no default_code.
    {
        "id": 504,
        "product_id": 13,
        "location_id": 101,
        "quantity": 4,
        "date": "2026-10-10",
    },
    # Skipped, no error: location 104 resolves to warehouse 23, no code.
    {
        "id": 505,
        "product_id": 11,
        "location_id": 104,
        "quantity": 4,
        "date": "2026-10-10",
    },
    # Skipped, no error: location 105 resolves to no warehouse.
    {
        "id": 506,
        "product_id": 11,
        "location_id": 105,
        "quantity": 4,
        "date": "2026-10-10",
    },
    # Skipped, no error: zero quantity.
    {
        "id": 507,
        "product_id": 11,
        "location_id": 101,
        "quantity": 0,
        "date": "2026-10-10",
    },
    # Skipped, no error: non-numeric quantity.
    {
        "id": 508,
        "product_id": 11,
        "location_id": 101,
        "quantity": "4",
        "date": "2026-10-10",
    },
    # Skipped, no error: unparseable date.
    {
        "id": 509,
        "product_id": 11,
        "location_id": 101,
        "quantity": 4,
        "date": "October 2026",
    },
    # Skipped, no error: missing date.
    {"id": 510, "product_id": 11, "location_id": 101, "quantity": 4},
    # Skipped, no error: no id, so no stable business_key is possible.
    {"product_id": 11, "location_id": 101, "quantity": 4, "date": "2026-10-10"},
]

PRODUCTS = [
    {"id": 11, "default_code": "P-1"},
    {"id": 12, "default_code": "P-2"},
    {"id": 13, "default_code": False},
]

LOCATIONS = [
    {"id": 101, "warehouse_id": 21},
    {"id": 103, "warehouse_id": 22},
    {"id": 104, "warehouse_id": 23},
    {"id": 105, "warehouse_id": False},
]

WAREHOUSES = [
    {"id": 21, "code": "SITE-A"},
    {"id": 22, "code": "SITE-B"},
    {"id": 23, "code": False},
]

EXPECTED = [
    {
        "business_key": f"odoo:{DB}:stock.move:501",
        "item_code": "P-1",
        "site_code": "SITE-A",
        "quantity": 3.5,
        "occurred_on": "2026-10-12",
        "event_type": "shipment",
        "source_system": "odoo",
    },
    {
        "business_key": f"odoo:{DB}:stock.move:502",
        "item_code": "P-2",
        "site_code": "SITE-B",
        "quantity": 2.0,
        "occurred_on": "2026-10-14",
        "event_type": "shipment",
        "source_system": "odoo",
    },
    {
        "business_key": f"odoo:{DB}:stock.move:503",
        "item_code": "P-1",
        "site_code": "SITE-A",
        "quantity": 3.5,
        "occurred_on": "2026-10-12",
        "event_type": "shipment",
        "source_system": "odoo",
    },
]


def _set_env(monkeypatch):
    monkeypatch.setenv("ODOO_BASE_URL", BASE_URL)
    monkeypatch.setenv("ODOO_DB", DB)
    monkeypatch.setenv("ODOO_LOGIN", LOGIN)
    monkeypatch.setenv("ODOO_API_KEY", API_KEY)
    monkeypatch.delenv("ODOO_ALLOW_INSECURE", raising=False)


class _OdooServer:
    """MockTransport handler answering each model's search_read by name.

    Records every request; unknown models, extra calls, a write, or a
    wrong authenticate phase fail the test loudly.
    """

    def __init__(self, uid, records_by_model):
        self.uid = uid
        self.records = dict(records_by_model)
        self.models = {
            model: {"calls": 0, "domains": [], "fields": [], "kwargs": []}
            for model in records_by_model
        }
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        self.bodies.append(body)
        service = body["params"]["service"]
        method = body["params"]["method"]
        args = body["params"]["args"]
        result = None
        if service == "common" and method == "authenticate":
            assert args == [DB, LOGIN, API_KEY, {}]
            result = self.uid
        else:
            assert (
                service == "object" and method == "execute_kw"
            ), f"unexpected call: {service}.{method}"
            model = args[3]
            assert args[4] == "search_read", f"unexpected method {args[4]}"
            info = self.models[model]
            info["calls"] += 1
            info["domains"].append(args[5][0])
            info["fields"].append(args[6]["fields"])
            info["kwargs"].append(args[6])
            result = self.records[model]
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": result}
        )

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def test_issue398_freeform(monkeypatch):
    # 1. Two done customer-bound moves of different products and
    #    warehouses become two rows (plus a third kept move of the first
    #    product and site), each shaped for DemandEventUpsert:
    #    business_key the namespaced odoo:{db}:stock.move:{move_id}
    #    identifier, item_code the default_code, site_code the warehouse
    #    code, event_type "shipment", source_system "odoo", occurred_on
    #    the date part of the move's date. A negative quantity is
    #    carried as its positive float absolute value; unresolvable or
    #    invalid moves are skipped with no error.
    _set_env(monkeypatch)
    server = _OdooServer(
        9,
        {
            "stock.move": MOVES,
            "product.product": PRODUCTS,
            "stock.location": LOCATIONS,
            "stock.warehouse": WAREHOUSES,
        },
    )
    with OdooClient(client=server.client()) as client:
        rows = client.read_demand_events()
    assert rows == EXPECTED
    for row in rows:
        DemandEventUpsert(**row)

    # 2. A negative quantity is stored as its absolute value. Every kept
    #    row carries its quantity as a positive float - checked for every
    #    row, not just the first, since == cannot tell 2 from 2.0 and an
    #    integer input must come out as a float, not pass through as-is.
    by_key = {row["business_key"]: row for row in rows}
    first = by_key[f"odoo:{DB}:stock.move:501"]
    assert first["quantity"] == 3.5
    assert isinstance(first["quantity"], float)
    for row in rows:
        assert isinstance(row["quantity"], float)
        assert row["quantity"] > 0
    # Move 502's stored quantity is the integer 2; its row carries the
    #    float 2.0, so an implementation that passed the int through
    #    as-is (where 2 == 2.0 would hide it) fails here.
    integer_input_row = by_key[f"odoo:{DB}:stock.move:502"]
    assert integer_input_row["quantity"] == 2.0
    assert isinstance(integer_input_row["quantity"], float)
    # Each move is its own row: the two P-1/SITE-A moves are NOT summed
    # into 7.0, unlike on-hand.
    p1_rows = [row for row in rows if row["item_code"] == "P-1"]
    assert [row["quantity"] for row in p1_rows] == [3.5, 3.5]
    assert [row["occurred_on"] for row in p1_rows] == ["2026-10-12", "2026-10-12"]

    # 3. The business_key is a namespaced, globally unique composite:
    #    source system, the configured database identifier, the entity
    #    type, and the move id — never a bare id that could collide with
    #    a key from another system, database, or entity.
    for row, move_id in zip(rows, (501, 502, 503)):
        assert row["business_key"] == f"odoo:{DB}:stock.move:{move_id}"
        assert row["business_key"].split(":") == [
            "odoo",
            DB,
            "stock.move",
            str(move_id),
        ]
    assert len({row["business_key"] for row in rows}) == len(rows)

    # 4. The stock.move read requires state = done and a customer
    #    destination, reads exactly the four fields, and is issued with
    #    order='id asc' for stable offset paging.
    move_info = server.models["stock.move"]
    assert move_info["calls"] == 1
    assert move_info["domains"] == [
        [["state", "=", "done"], ["location_dest_id.usage", "=", "customer"]]
    ]
    assert move_info["fields"] == [["product_id", "location_id", "quantity", "date"]]
    assert move_info["kwargs"] == [
        {
            "fields": ["product_id", "location_id", "quantity", "date"],
            "offset": 0,
            "order": "id asc",
        }
    ]

    # 5. The product, location and warehouse lookups are each exactly one
    #    search_read over the ids actually seen (id in [...]), not one
    #    call per move.
    assert server.models["product.product"]["calls"] == 1
    assert server.models["product.product"]["domains"] == [[["id", "in", [11, 12, 13]]]]
    assert server.models["product.product"]["fields"] == [["default_code"]]
    assert server.models["product.product"]["kwargs"] == [
        {"fields": ["default_code"], "offset": 0, "order": "id asc"}
    ]
    assert server.models["stock.location"]["calls"] == 1
    assert server.models["stock.location"]["domains"] == [
        [["id", "in", [101, 103, 104, 105]]]
    ]
    assert server.models["stock.location"]["fields"] == [["warehouse_id"]]
    assert server.models["stock.location"]["kwargs"] == [
        {"fields": ["warehouse_id"], "offset": 0, "order": "id asc"}
    ]
    assert server.models["stock.warehouse"]["calls"] == 1
    assert server.models["stock.warehouse"]["domains"] == [[["id", "in", [21, 22, 23]]]]
    assert server.models["stock.warehouse"]["fields"] == [["code"]]
    assert server.models["stock.warehouse"]["kwargs"] == [
        {"fields": ["code"], "offset": 0, "order": "id asc"}
    ]
    assert len(server.requests) == 5

    # 6. search_read accepts an order parameter and forwards it verbatim.
    _set_env(monkeypatch)
    server = _OdooServer(9, {"product.product": [{"id": 1}]})
    with OdooClient(client=server.client()) as client:
        got = client.search_read(
            "product.product",
            [],
            ["id"],
            limit=25,
            offset=40,
            order="id asc",
        )
    assert got == [{"id": 1}]
    kwargs = server.bodies[1]["params"]["args"][6]
    assert kwargs == {
        "fields": ["id"],
        "limit": 25,
        "offset": 40,
        "order": "id asc",
    }

    # 7. Without order, the wire shape is unchanged (no "order" key), so
    #    existing callers are untouched.
    _set_env(monkeypatch)
    server = _OdooServer(9, {"product.product": [{"id": 1}]})
    with OdooClient(client=server.client()) as client:
        client.search_read("product.product", [], ["id"])
    kwargs = server.bodies[1]["params"]["args"][6]
    assert kwargs == {"fields": ["id"], "offset": 0}

    # 8. A malformed order (blank, non-string, control character, or not
    #    a column/direction list) raises before any request.
    _set_env(monkeypatch)
    server = _OdooServer(9, {"product.product": [{"id": 1}]})
    with OdooClient(client=server.client()) as client:
        for bad in ("", "   ", 7, "id; drop", "id\x00asc"):
            try:
                client.search_read("product.product", [], ["id"], order=bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"order {bad!r} must raise ValueError")
    assert server.requests == []

    # 9. No moves at all: [] and no product/location/warehouse reads.
    _set_env(monkeypatch)
    server = _OdooServer(9, {"stock.move": []})
    with OdooClient(client=server.client()) as client:
        assert client.read_demand_events() == []
    assert len(server.requests) == 2
    assert len(server.bodies) == 2
