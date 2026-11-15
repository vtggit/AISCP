"""Issue #395 proof: OdooClient.read_on_hand with the server fully mocked.

With the Odoo server fully mocked (httpx.MockTransport answers every
/jsonrpc POST, so no network), the transport is stubbed so each model's
search_read is answered by this test. It checks:

  * two quants of the same product in two internal locations of one
    warehouse summing into one row, and quants of that product in a second
    warehouse giving a second row, each with item_code the product's
    default_code, site_code the warehouse's code, and source_system "odoo";
  * a negative quantity preserved in the sum, and a pair of quants that
    cancel to exactly zero still emitted as a zero row;
  * a quant whose product has no default_code, and a quant whose location
    resolves to a warehouse with no code (and to no warehouse at all),
    both skipped with no error and without collapsing into another key;
  * as_of passed through when given, and matching YYYY-MM-DDTHH:MM:SSZ
    when None;
  * the domain sent to stock.quant restricting to internal locations, and
    the product/location/warehouse reads each issued once for the ids seen
    (counted by the mock, an id in [...] domain), not once per quant;
  * no quants at all returning [] with no product/location/warehouse reads.
"""

import json
import re

import httpx

from app.feed.odoo_client import OdooClient
from app.models.on_hand import OnHandUpsert

BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"

AS_OF = "2026-10-15T08:30:00Z"
AS_OF_NONE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

QUANTS = [
    # W-1 / SITE-A: 10.5 + 4 = 14.5 (two quants, two internal locations,
    # one warehouse, one row).
    {"id": 1, "product_id": 11, "location_id": 101, "quantity": 10.5},
    {"id": 2, "product_id": 11, "location_id": 102, "quantity": 4},
    # W-1 / SITE-B: second warehouse, second row.
    {"id": 3, "product_id": 11, "location_id": 103, "quantity": 3},
    # G-9 / SITE-A: negative quantity preserved in the sum.
    {"id": 4, "product_id": 12, "location_id": 101, "quantity": -7},
    # G-9 / SITE-B: a pair cancelling to exactly zero, still emitted.
    {"id": 5, "product_id": 12, "location_id": 103, "quantity": 2.5},
    {"id": 6, "product_id": 12, "location_id": 103, "quantity": -2.5},
    # Skipped, no error: product 13 has no default_code.
    {"id": 7, "product_id": 13, "location_id": 101, "quantity": 5},
    # Skipped, no error: warehouse 23 has no code.
    {"id": 8, "product_id": 11, "location_id": 104, "quantity": 2},
    # Skipped, no error: location 105 resolves to no warehouse.
    {"id": 9, "product_id": 12, "location_id": 105, "quantity": 1},
    # Skipped, no error: location 199 resolves to no location record.
    {"id": 10, "product_id": 11, "location_id": 199, "quantity": 1},
]

PRODUCTS = [
    {"id": 11, "default_code": "W-1"},
    {"id": 12, "default_code": "G-9"},
    {"id": 13, "default_code": False},
]

LOCATIONS = [
    {"id": 101, "warehouse_id": 21},
    {"id": 102, "warehouse_id": 21},
    {"id": 103, "warehouse_id": 22},
    {"id": 104, "warehouse_id": 23},
    {"id": 105, "warehouse_id": False},
    # No record for 199.
]

WAREHOUSES = [
    {"id": 21, "code": "SITE-A"},
    {"id": 22, "code": "SITE-B"},
    {"id": 23, "code": False},
]


def _expected_rows(as_of):
    return [
        {
            "item_code": "W-1",
            "site_code": "SITE-A",
            "quantity_on_hand": 14.5,
            "as_of": as_of,
            "source_system": "odoo",
        },
        {
            "item_code": "W-1",
            "site_code": "SITE-B",
            "quantity_on_hand": 3.0,
            "as_of": as_of,
            "source_system": "odoo",
        },
        {
            "item_code": "G-9",
            "site_code": "SITE-A",
            "quantity_on_hand": -7.0,
            "as_of": as_of,
            "source_system": "odoo",
        },
        {
            "item_code": "G-9",
            "site_code": "SITE-B",
            "quantity_on_hand": 0.0,
            "as_of": as_of,
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

    Records every request; unknown models, extra calls, or a wrong
    authenticate phase fail the test loudly.
    """

    def __init__(self, uid, records_by_model):
        self.uid = uid
        self.records = dict(records_by_model)
        self.models = {
            model: {"calls": 0, "domains": [], "fields": []}
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
            result = self.records[model]
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": result}
        )

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _full_server():
    return _OdooServer(
        9,
        {
            "stock.quant": QUANTS,
            "product.product": PRODUCTS,
            "stock.location": LOCATIONS,
            "stock.warehouse": WAREHOUSES,
        },
    )


def test_issue395_freeform(monkeypatch):
    # 1. as_of passed through when given: two quants of the same product in
    #    two internal locations of one warehouse sum into one row; quants of
    #    that product in a second warehouse give a second row; negative
    #    quantities are preserved and a pair cancelling to exactly zero is
    #    still emitted as a zero row; unresolvable quants are skipped with
    #    no error. Every row is shaped for OnHandUpsert with
    #    source_system "odoo".
    _set_env(monkeypatch)
    server = _full_server()
    with OdooClient(client=server.client()) as client:
        rows = client.read_on_hand(as_of=AS_OF)
    expected = _expected_rows(AS_OF)
    assert rows == expected
    for row in rows:
        OnHandUpsert(**row)
    by_key = {(row["item_code"], row["site_code"]): row for row in rows}
    assert by_key[("W-1", "SITE-A")]["quantity_on_hand"] == 14.5
    assert by_key[("W-1", "SITE-B")]["quantity_on_hand"] == 3.0
    assert by_key[("G-9", "SITE-A")]["quantity_on_hand"] == -7.0
    assert by_key[("G-9", "SITE-B")]["quantity_on_hand"] == 0.0

    # 2. The stock.quant read restricts to internal locations with the
    #    product/location/quantity fields.
    assert server.models["stock.quant"]["calls"] == 1
    assert server.models["stock.quant"]["domains"] == [
        [["location_id.usage", "=", "internal"]]
    ]
    assert server.models["stock.quant"]["fields"] == [
        ["product_id", "location_id", "quantity"]
    ]

    # 3. The product, location and warehouse lookups are each exactly one
    #    search_read over the ids actually seen (id in [...]), not one call
    #    per quant: 10 quants produced 4 reads total (plus authenticate).
    assert server.models["product.product"]["calls"] == 1
    assert server.models["product.product"]["domains"] == [[["id", "in", [11, 12, 13]]]]
    assert server.models["product.product"]["fields"] == [["default_code"]]
    assert server.models["stock.location"]["calls"] == 1
    assert server.models["stock.location"]["domains"] == [
        [["id", "in", [101, 102, 103, 104, 105, 199]]]
    ]
    assert server.models["stock.location"]["fields"] == [["warehouse_id"]]
    assert server.models["stock.warehouse"]["calls"] == 1
    assert server.models["stock.warehouse"]["domains"] == [[["id", "in", [21, 22, 23]]]]
    assert server.models["stock.warehouse"]["fields"] == [["code"]]
    assert len(server.requests) == 5

    # 4. as_of matching YYYY-MM-DDTHH:MM:SSZ when None, with the same rows.
    _set_env(monkeypatch)
    server = _full_server()
    with OdooClient(client=server.client()) as client:
        rows = client.read_on_hand()
    assert len(rows) == len(_expected_rows(AS_OF))
    for row, expected_row in zip(rows, _expected_rows(AS_OF)):
        assert AS_OF_NONE_RE.fullmatch(row["as_of"])
        assert {k: v for k, v in row.items() if k != "as_of"} == {
            k: v for k, v in expected_row.items() if k != "as_of"
        }

    # 5. No quants at all: [] and no product/location/warehouse reads.
    _set_env(monkeypatch)
    server = _OdooServer(9, {"stock.quant": []})
    with OdooClient(client=server.client()) as client:
        assert client.read_on_hand(as_of=AS_OF) == []
    assert len(server.requests) == 2
    assert len(server.bodies) == 2
