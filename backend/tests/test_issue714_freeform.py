"""Issue #714 proof: read_item_sites reorder rules, server fully mocked.

With the Odoo server fully mocked (httpx.MockTransport answers every
/jsonrpc POST, so no network), the transport is stubbed so each model's
search_read is answered by this test: it answers authenticate and
search_read only, and any write fails loudly. The stock covers four
(product, warehouse) pairs:

  * pair A: one reorder rule, min 5 and max 20;
  * pair B: one reorder rule, min 8 and max 8;
  * pair C: two reorder rules (ambiguous);
  * pair D: no reorder rule.

It checks:

  * pair A's row reads reorder_point 5.0, max_qty 20.0 and order_qty
    15.0;
  * pair B's row reads 8.0 and 8.0 and a null order_qty (max minus min
    is not greater than 0);
  * pair C's and pair D's rows read all three as null (an ambiguous or
    missing rule leaves the row unchanged);
  * every row carries a null safety_stock;
  * exactly one stock.warehouse.orderpoint search_read, with the
    active-only domain over the product ids actually seen (ascending)
    and the product_id, warehouse_id, product_min_qty and
    product_max_qty fields;
  * empty stock: with no stock at all, the orderpoint call is never
    made;
  * no writes: the mock answers search_read only and fails loudly on
    any other method.
"""

import json

import httpx

from app.feed.odoo_client import OdooClient
from app.models.item_sites import ItemSiteUpsert

BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"

QUANTS = [
    # Pair A: product 11 at location 201 (warehouse 31, SITE-A).
    {"id": 801, "product_id": 11, "location_id": 201},
    # Pair B: product 12 at location 202 (warehouse 32, SITE-B).
    {"id": 802, "product_id": 12, "location_id": 202},
    # Pair C: product 13 at location 203 (warehouse 33, SITE-C).
    {"id": 803, "product_id": 13, "location_id": 203},
    # Pair D: product 14 at location 204 (warehouse 34, SITE-D).
    {"id": 804, "product_id": 14, "location_id": 204},
]

PRODUCTS = [
    {"id": 11, "default_code": "P-A", "standard_price": 1.0, "seller_ids": False},
    {"id": 12, "default_code": "P-B", "standard_price": 2.0, "seller_ids": False},
    {"id": 13, "default_code": "P-C", "standard_price": 3.0, "seller_ids": False},
    {"id": 14, "default_code": "P-D", "standard_price": 4.0, "seller_ids": False},
]

LOCATIONS = [
    {"id": 201, "warehouse_id": 31},
    {"id": 202, "warehouse_id": 32},
    {"id": 203, "warehouse_id": 33},
    {"id": 204, "warehouse_id": 34},
]

WAREHOUSES = [
    {"id": 31, "code": "SITE-A"},
    {"id": 32, "code": "SITE-B"},
    {"id": 33, "code": "SITE-C"},
    {"id": 34, "code": "SITE-D"},
]

ORDERPOINTS = [
    # Pair A: exactly one rule, min 5 and max 20.
    {
        "id": 91,
        "product_id": 11,
        "warehouse_id": 31,
        "product_min_qty": 5,
        "product_max_qty": 20,
    },
    # Pair B: exactly one rule, min 8 and max 8 (max - min is 0, so
    # order_qty stays null).
    {
        "id": 92,
        "product_id": 12,
        "warehouse_id": 32,
        "product_min_qty": 8,
        "product_max_qty": 8,
    },
    # Pair C: two rules for the same pair (ambiguous) -> the row keeps
    # all three fields null.
    {
        "id": 93,
        "product_id": 13,
        "warehouse_id": 33,
        "product_min_qty": 1,
        "product_max_qty": 2,
    },
    {
        "id": 94,
        "product_id": 13,
        "warehouse_id": 33,
        "product_min_qty": 3,
        "product_max_qty": 4,
    },
    # Pair D: no rule at all.
]


def _set_env(monkeypatch):
    monkeypatch.setenv("ODOO_BASE_URL", BASE_URL)
    monkeypatch.setenv("ODOO_DB", DB)
    monkeypatch.setenv("ODOO_LOGIN", LOGIN)
    monkeypatch.setenv("ODOO_API_KEY", API_KEY)
    monkeypatch.delenv("ODOO_CA_BUNDLE", raising=False)
    monkeypatch.delenv("ODOO_ALLOW_INSECURE", raising=False)


def _records_by_model():
    return {
        "stock.quant": QUANTS,
        "product.product": PRODUCTS,
        "stock.location": LOCATIONS,
        "stock.warehouse": WAREHOUSES,
        "stock.warehouse.orderpoint": ORDERPOINTS,
    }


class _OdooServer:
    """MockTransport handler answering authenticate and search_read only.

    Records every request; an unknown model, an extra call, a write,
    or a wrong authenticate phase fails the test loudly.
    """

    def __init__(self, uid):
        self.uid = uid
        self.records = _records_by_model()
        self.models = {
            model: {"calls": 0, "domains": [], "fields": [], "kwargs": []}
            for model in self.records
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
            assert model in self.models, f"unexpected model {model}"
            assert args[4] == "search_read", f"unexpected method {args[4]}"
            info = self.models[model]
            info["calls"] += 1
            info["domains"].append(args[5][0])
            info["fields"].append(args[6]["fields"])
            info["kwargs"].append(args[6])
            result = self.records[model]
        return httpx.Response(
            200,
            content=json.dumps(
                {"jsonrpc": "2.0", "id": body["id"], "result": result}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def test_issue714_freeform(monkeypatch):
    _set_env(monkeypatch)
    server = _OdooServer(9)
    with OdooClient(client=server.client()) as client:
        rows = client.read_item_sites()

    # 1. One row per (product, warehouse) pair, each shaped for
    #    ItemSiteUpsert, each with a null safety_stock and
    #    source_system "odoo".
    by_pair = {(row["item_code"], row["site_code"]): row for row in rows}
    assert set(by_pair) == {
        ("P-A", "SITE-A"),
        ("P-B", "SITE-B"),
        ("P-C", "SITE-C"),
        ("P-D", "SITE-D"),
    }
    for row in rows:
        upsert = ItemSiteUpsert(**row)
        assert row["safety_stock"] is None
        assert upsert.safety_stock is None
        assert row["source_system"] == "odoo"

    # 2. Pair A: exactly one rule (min 5, max 20) -> reorder_point
    #    5.0, max_qty 20.0 and order_qty 20.0 - 5.0 = 15.0.
    pair_a = by_pair[("P-A", "SITE-A")]
    assert pair_a["reorder_point"] == 5.0
    assert pair_a["max_qty"] == 20.0
    assert pair_a["order_qty"] == 15.0

    # 3. Pair B: exactly one rule (min 8, max 8) -> 8.0 and 8.0, and a
    #    null order_qty (max - min is not greater than 0).
    pair_b = by_pair[("P-B", "SITE-B")]
    assert pair_b["reorder_point"] == 8.0
    assert pair_b["max_qty"] == 8.0
    assert pair_b["order_qty"] is None

    # 4. Pair C: two rules (ambiguous) -> all three null.
    pair_c = by_pair[("P-C", "SITE-C")]
    assert pair_c["reorder_point"] is None
    assert pair_c["max_qty"] is None
    assert pair_c["order_qty"] is None

    # 5. Pair D: no rule -> all three null.
    pair_d = by_pair[("P-D", "SITE-D")]
    assert pair_d["reorder_point"] is None
    assert pair_d["max_qty"] is None
    assert pair_d["order_qty"] is None

    # 6. Exactly one stock.warehouse.orderpoint search_read, with the
    #    active-only domain over the product ids actually seen
    #    (ascending) and the four fields.
    orderpoint_info = server.models["stock.warehouse.orderpoint"]
    assert orderpoint_info["calls"] == 1
    assert orderpoint_info["domains"] == [
        [["active", "=", True], ["product_id", "in", [11, 12, 13, 14]]]
    ]
    assert orderpoint_info["fields"] == [
        ["product_id", "warehouse_id", "product_min_qty", "product_max_qty"]
    ]
    assert orderpoint_info["kwargs"] == [
        {
            "fields": [
                "product_id",
                "warehouse_id",
                "product_min_qty",
                "product_max_qty",
            ],
            "offset": 0,
        }
    ]

    # 7. No internal stock at all: [] with the orderpoint call never
    #    made (no product id is seen), and still no write (the mock
    #    answers search_read only).
    empty = _OdooServer(9)
    empty.records["stock.quant"] = []
    with OdooClient(client=empty.client()) as client:
        assert client.read_item_sites() == []
    assert empty.models["stock.warehouse.orderpoint"]["calls"] == 0
    for model in (
        "product.product",
        "stock.location",
        "stock.warehouse",
    ):
        assert empty.models[model]["calls"] == 0
