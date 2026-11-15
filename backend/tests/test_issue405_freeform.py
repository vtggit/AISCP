"""Issue #405 proof: OdooClient.read_item_sites with the server fully mocked.

With the Odoo server fully mocked (httpx.MockTransport answers every
/jsonrpc POST, so no network), the transport is stubbed so each model's
search_read is answered by this test. It checks:

  * a product stocked in two warehouses becoming two rows with the
    same item_code, master_lead_time_days and unit_cost but different
    site_code, each carrying source_system "odoo";
  * master_lead_time_days taken from the delay of the first seller_ids
    entry (not the second seller's delay) and null for a product with
    no seller_ids;
  * unit_cost the product's standard_price as a float, and null when
    standard_price is not a finite number (Infinity);
  * a product with no default_code, and a location resolving to no
    warehouse (or to a warehouse with no code), contributing no row;
  * the same (item_code, site_code) reached from two internal
    locations of one warehouse yielding exactly one row (pairs are
    deduplicated);
  * the stock.quant domain restricting to internal locations with only
    the product_id and location_id fields, and the product, location,
    warehouse and supplierinfo reads each issued once over the ids
    seen (an id in [...] domain), each omitted entirely when no id is
    seen;
  * ODOO_CA_BUNDLE isolation: a hostile bundle path exported by the
    host environment is removed before any read, so the result never
    depends on the ambient environment;
  * no writes: the mock answers search_read only and fails loudly on
    any other method.
"""

import json
import os

import httpx

from app.feed.odoo_client import OdooClient
from app.models.item_sites import ItemSiteUpsert

BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"

QUANTS = [
    # P-11 at location 201 (warehouse 31, SITE-A).
    {"id": 701, "product_id": 11, "location_id": 201},
    # P-11 again, from a second internal location (202) of the same
    # warehouse 31: the same (P-11, SITE-A) pair, deduplicated.
    {"id": 702, "product_id": 11, "location_id": 202},
    # P-11 at location 203 (warehouse 32, SITE-B): the second site row
    # for P-11 - same item, cost and lead time, different site_code.
    {"id": 703, "product_id": 11, "location_id": 203},
    # P-12 at SITE-A (unit_cost null: standard_price not finite).
    {"id": 704, "product_id": 12, "location_id": 201},
    # P-14 at SITE-B (no seller_ids -> master_lead_time_days null).
    {"id": 705, "product_id": 14, "location_id": 203},
    # P-13 holds stock but has no default_code -> no row.
    {"id": 706, "product_id": 13, "location_id": 201},
    # P-11 at location 204, which resolves to no warehouse -> no row.
    {"id": 707, "product_id": 11, "location_id": 204},
    # P-11 at location 205, which resolves to warehouse 33, a
    # warehouse with no code -> no row.
    {"id": 708, "product_id": 11, "location_id": 205},
    # Hostile shapes: no usable product or location id -> skipped
    # without error and without contributing a pair.
    {"id": 709, "product_id": False, "location_id": 201},
    {"id": 710, "location_id": 201},
]

PRODUCTS = [
    # Two sellers: the primary (first) seller is 51, so P-11's lead
    # time must be 51's delay (14), never 52's (7.5).
    {
        "id": 11,
        "default_code": "P-11",
        "standard_price": 12.5,
        "seller_ids": [[51, "Vendor One"], [52, "Vendor Two"]],
    },
    # standard_price is not a finite number -> unit_cost null.
    {
        "id": 12,
        "default_code": "P-12",
        "standard_price": float("inf"),
        "seller_ids": [[52, "Vendor Two"]],
    },
    # No default_code -> contributes no pair at all.
    {
        "id": 13,
        "default_code": False,
        "standard_price": 5.0,
        "seller_ids": [[51, "Vendor One"]],
    },
    # No seller_ids -> master_lead_time_days null.
    {
        "id": 14,
        "default_code": "P-14",
        "standard_price": 3.25,
        "seller_ids": False,
    },
]

LOCATIONS = [
    {"id": 201, "warehouse_id": 31},
    {"id": 202, "warehouse_id": 31},
    {"id": 203, "warehouse_id": 32},
    {"id": 204, "warehouse_id": False},
    {"id": 205, "warehouse_id": 33},
]

WAREHOUSES = [
    {"id": 31, "code": "SITE-A"},
    {"id": 32, "code": "SITE-B"},
    {"id": 33, "code": False},
]

SUPPLIERINFO = [
    {"id": 51, "delay": 14},
    {"id": 52, "delay": 7.5},
]

EXPECTED = [
    {
        "item_code": "P-11",
        "site_code": "SITE-A",
        "unit_cost": 12.5,
        "reorder_point": None,
        "safety_stock": None,
        "max_qty": None,
        "order_qty": None,
        "master_lead_time_days": 14.0,
        "source_system": "odoo",
    },
    {
        "item_code": "P-11",
        "site_code": "SITE-B",
        "unit_cost": 12.5,
        "reorder_point": None,
        "safety_stock": None,
        "max_qty": None,
        "order_qty": None,
        "master_lead_time_days": 14.0,
        "source_system": "odoo",
    },
    {
        "item_code": "P-12",
        "site_code": "SITE-A",
        "unit_cost": None,
        "reorder_point": None,
        "safety_stock": None,
        "max_qty": None,
        "order_qty": None,
        "master_lead_time_days": 7.5,
        "source_system": "odoo",
    },
    {
        "item_code": "P-14",
        "site_code": "SITE-B",
        "unit_cost": 3.25,
        "reorder_point": None,
        "safety_stock": None,
        "max_qty": None,
        "order_qty": None,
        "master_lead_time_days": None,
        "source_system": "odoo",
    },
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
        "product.supplierinfo": SUPPLIERINFO,
        "stock.warehouse.orderpoint": [],
    }


class _OdooServer:
    """MockTransport handler answering each model's search_read by name.

    Records every request; an unknown model, an extra call, a write,
    or a wrong authenticate phase fails the test loudly. Responses are
    serialized with allow_nan so the non-finite standard_price
    (Infinity) crosses the wire; the client must still map it to a
    null unit_cost.
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
                {"jsonrpc": "2.0", "id": body["id"], "result": result},
                allow_nan=True,
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def test_issue405_freeform(monkeypatch):
    # 0. ODOO_CA_BUNDLE isolation: the client re-reads the environment
    #    at call time, so a hostile bundle path exported by the host
    #    environment must never reach the config check. _set_env
    #    removes the variable; the hostile value proves the removal
    #    happens before any read.
    monkeypatch.setenv("ODOO_CA_BUNDLE", "/nonexistent/odoo-ca-bundle.pem")
    _set_env(monkeypatch)
    assert "ODOO_CA_BUNDLE" not in os.environ
    server = _OdooServer(9)
    with OdooClient(client=server.client()) as client:
        rows = client.read_item_sites()

    # 1. The rows match exactly: one per distinct (item_code,
    #    site_code) pair, each shaped for ItemSiteUpsert with
    #    reorder_point, safety_stock, max_qty and order_qty left
    #    unset (null).
    assert rows == EXPECTED
    for row in rows:
        upsert = ItemSiteUpsert(**row)
        assert upsert.reorder_point is None
        assert upsert.safety_stock is None
        assert upsert.max_qty is None
        assert upsert.order_qty is None

    # 2. P-11 is stocked in two warehouses: two rows with the same
    #    item_code, master_lead_time_days and unit_cost but different
    #    site_code, each with source_system "odoo".
    p11 = [row for row in rows if row["item_code"] == "P-11"]
    assert len(p11) == 2
    assert {row["site_code"] for row in p11} == {"SITE-A", "SITE-B"}
    assert {row["unit_cost"] for row in p11} == {12.5}
    assert {row["master_lead_time_days"] for row in p11} == {14.0}
    assert all(row["source_system"] == "odoo" for row in p11)

    # 3. master_lead_time_days: the delay of the FIRST seller_ids
    #    entry. P-11's primary seller is 51 (delay 14), not its second
    #    seller 52 (delay 7.5); P-12's primary seller is 52 (delay
    #    7.5); P-14 has no seller_ids -> null.
    by_pair = {(row["item_code"], row["site_code"]): row for row in rows}
    assert by_pair[("P-11", "SITE-A")]["master_lead_time_days"] == 14.0
    assert by_pair[("P-12", "SITE-A")]["master_lead_time_days"] == 7.5
    assert by_pair[("P-14", "SITE-B")]["master_lead_time_days"] is None

    # 4. unit_cost: standard_price as a float, null when it is not a
    #    finite number (P-12's standard_price is Infinity).
    assert by_pair[("P-11", "SITE-A")]["unit_cost"] == 12.5
    assert by_pair[("P-11", "SITE-B")]["unit_cost"] == 12.5
    assert by_pair[("P-14", "SITE-B")]["unit_cost"] == 3.25
    assert by_pair[("P-12", "SITE-A")]["unit_cost"] is None

    # 5. No rows for: P-13 (no default_code), P-11 at location 204 (no
    #    resolvable warehouse), P-11 at location 205 (warehouse 33 has
    #    no code), or the hostile quants 709/710 (no usable product or
    #    location id).
    assert {row["item_code"] for row in rows} == {"P-11", "P-12", "P-14"}
    assert {row["site_code"] for row in rows} == {"SITE-A", "SITE-B"}

    # 6. The stock.quant read restricts to internal locations, asks
    #    only for the product and location refs, and is issued once.
    quant_info = server.models["stock.quant"]
    assert quant_info["calls"] == 1
    assert quant_info["domains"] == [[["location_id.usage", "=", "internal"]]]
    assert quant_info["fields"] == [["product_id", "location_id"]]
    assert quant_info["kwargs"] == [
        {"fields": ["product_id", "location_id"], "offset": 0}
    ]

    # 7. The product, location, warehouse and supplierinfo reads are
    #    each exactly one search_read over the ids actually seen (an
    #    id in [...] domain), not one call per quant.
    product_info = server.models["product.product"]
    assert product_info["calls"] == 1
    assert product_info["domains"] == [[["id", "in", [11, 12, 13, 14]]]]
    assert product_info["fields"] == [["default_code", "standard_price", "seller_ids"]]
    assert product_info["kwargs"] == [
        {"fields": ["default_code", "standard_price", "seller_ids"], "offset": 0}
    ]

    location_info = server.models["stock.location"]
    assert location_info["calls"] == 1
    assert location_info["domains"] == [[["id", "in", [201, 202, 203, 204, 205]]]]
    assert location_info["fields"] == [["warehouse_id"]]
    assert location_info["kwargs"] == [{"fields": ["warehouse_id"], "offset": 0}]

    warehouse_info = server.models["stock.warehouse"]
    assert warehouse_info["calls"] == 1
    assert warehouse_info["domains"] == [[["id", "in", [31, 32, 33]]]]
    assert warehouse_info["fields"] == [["code"]]
    assert warehouse_info["kwargs"] == [{"fields": ["code"], "offset": 0}]

    supplier_info = server.models["product.supplierinfo"]
    assert supplier_info["calls"] == 1
    assert supplier_info["domains"] == [[["id", "in", [51, 52]]]]
    assert supplier_info["fields"] == [["delay"]]
    assert supplier_info["kwargs"] == [{"fields": ["delay"], "offset": 0}]

    # 8. No internal stock at all: [] with the product, location,
    #    warehouse and supplierinfo reads omitted entirely (no id is
    #    seen), and still no write (the mock answers search_read only).
    empty = _OdooServer(9)
    empty.records["stock.quant"] = []
    with OdooClient(client=empty.client()) as client:
        assert client.read_item_sites() == []
    assert empty.models["stock.quant"]["calls"] == 1
    for model in (
        "product.product",
        "stock.location",
        "stock.warehouse",
        "product.supplierinfo",
    ):
        assert empty.models[model]["calls"] == 0
