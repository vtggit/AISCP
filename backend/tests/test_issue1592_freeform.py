"""Issue #1592 proof: line quantities converted to the item's base unit.

With the Odoo server fully mocked (httpx.MockTransport answers every
/jsonrpc POST, so no network), the transport is stubbed so each
model's search_read is answered by this test, and it fails the test on
a model it does not know. Every faked record of a kind the fixture
holds (purchase.order.line, sale.order.line, stock.move,
product.product, uom.uom) is built from
tests/fixtures/odoo_units_sample.json with json.loads; a variant
changes values and never a field name. The other models a read needs
(orders, picking types, warehouses, partners, locations) are faked
the way test_issue410_freeform.py fakes them. The fake answers
``uom.uom`` only for the ids the domain names, and only when the
domain carries ``["active", "in", [True, False]]``; without that
clause it answers unit 1 alone.

The proof reads every row's field strictly (``row["open_units"]``,
never ``.get`` with a default), and checks:

  * purchase: with both fixture lines on confirmed orders, line 21 in
    dozens answers ``open_units`` 120.0 and line 35 in its own unit
    answers 88.0, with exactly one ``uom.uom`` request (the sorted
    ids of the differing lines' units and their products' units, with
    the active clause, and the factor alone) issued after every other
    request;
  * purchase: line 21 with ``qty_received`` 2.0 answers
    ``open_units`` 96.0;
  * purchase: line 21 in its product's unit answers ``open_units``
    10.0 and no ``uom.uom`` request is made;
  * purchase: with the fake holding no unit 3, line 21 is unusable
    and has no row, while line 35 is still answered;
  * sales: a fixture line variant in dozens on a state ``"sale"``
    order answers ``open_units`` 18.0, and the same line on a state
    ``"done"`` order with the fake holding no unit 3 still answers a
    row, with ``open_units`` 0.0;
  * demand: a fixture move variant in dozens answers ``quantity``
    36.0, and the fixture move unchanged answers ``quantity`` 3.0
    with no ``uom.uom`` request;
  * read_on_hand and read_lead_time_observations still request the
    product fields ``["default_code"]``.
"""

import json
from pathlib import Path

import httpx

from app.feed.odoo_client import OdooClient

BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"
AS_OF = "2026-11-25T00:00:00Z"

ACTIVE_CLAUSE = ["active", "in", [True, False]]

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "odoo_units_sample.json").read_text()
)


def _fixture_record(kind: str, index: int = 0) -> dict:
    """A fresh copy of one fixture record, ready to vary by value."""
    return json.loads(json.dumps(FIXTURE[kind][index]))


def _fixture_products() -> list[dict]:
    return [json.loads(json.dumps(record)) for record in FIXTURE["products"]]


def _fixture_units(exclude: int | None = None) -> list[dict]:
    return [
        json.loads(json.dumps(record))
        for record in FIXTURE["units"]
        if record["id"] != exclude
    ]


# The other models a read needs, faked the way
# test_issue410_freeform.py fakes them: plain records.
PO_ORDERS = [
    {
        "id": 10,
        "date_order": "2026-10-01 14:00:00",
        "partner_id": 60,
        "picking_type_id": 41,
    },
    {
        "id": 18,
        "date_order": "2026-10-05",
        "partner_id": 60,
        "picking_type_id": 41,
    },
]
PICKING_TYPES = [{"id": 41, "warehouse_id": 31}]
WAREHOUSES = [{"id": 31, "code": "SITE-A"}]
PARTNERS = [{"id": 60, "ref": "V-60", "name": "Vendor Sixty"}]
LOCATIONS = [{"id": 6, "warehouse_id": 31}]
SALE_WAREHOUSES = [{"id": 1, "code": "WH-1"}]
SALE_ORDERS = [
    {
        "id": 4,
        "state": "sale",
        "commitment_date": "2026-11-05 08:30:00",
        "expected_date": False,
    },
]


def _purchase_records(lines) -> dict:
    return {
        "purchase.order.line": lines,
        "purchase.order": PO_ORDERS,
        "stock.picking.type": PICKING_TYPES,
        "stock.warehouse": WAREHOUSES,
        "res.partner": PARTNERS,
        "product.product": _fixture_products(),
    }


def _sales_records(lines, order) -> dict:
    return {
        "sale.order.line": lines,
        "product.product": _fixture_products(),
        "stock.warehouse": SALE_WAREHOUSES,
        "sale.order": [order],
    }


def _demand_records(moves, products) -> dict:
    return {
        "stock.move": moves,
        "product.product": products,
        "stock.location": LOCATIONS,
        "stock.warehouse": WAREHOUSES,
    }


class _OdooServer:
    """MockTransport handler answering each model's search_read by name.

    Records every request; a model it does not know, or any method
    other than search_read, fails the test loudly. It answers
    ``uom.uom`` only for the ids the domain names, and only when the
    domain carries ``["active", "in", [True, False]]``; without that
    clause it answers unit 1 alone.
    """

    def __init__(self, uid, records_by_model, units):
        self.uid = uid
        self.records = dict(records_by_model)
        self.unit_factors = {record["id"]: record["factor"] for record in units}
        self.models = {
            model: {"calls": 0, "domains": [], "fields": [], "kwargs": []}
            for model in (*self.records, "uom.uom")
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
        if service == "common" and method == "authenticate":
            assert args == [DB, LOGIN, API_KEY, {}]
            result = self.uid
        else:
            assert (
                service == "object" and method == "execute_kw"
            ), f"unexpected call: {service}.{method}"
            model = args[3]
            assert model in self.models, f"unknown model {model}"
            assert args[4] == "search_read", f"unexpected method {args[4]}"
            info = self.models[model]
            info["calls"] += 1
            info["domains"].append(args[5][0])
            info["fields"].append(args[6]["fields"])
            info["kwargs"].append(args[6])
            if model == "uom.uom":
                result = self._unit_records(args[5][0])
            else:
                result = self.records[model]
        return httpx.Response(
            200,
            content=json.dumps(
                {"jsonrpc": "2.0", "id": body["id"], "result": result}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def _unit_records(self, domain):
        ids = None
        for term in domain:
            if (
                isinstance(term, list)
                and len(term) == 3
                and term[0] == "id"
                and term[1] == "in"
            ):
                ids = list(term[2])
        assert ids is not None, f"uom.uom domain names no ids: {domain}"
        if ACTIVE_CLAUSE in domain:
            return [
                {"id": unit_id, "factor": factor}
                for unit_id, factor in sorted(self.unit_factors.items())
                if unit_id in ids
            ]
        if 1 in self.unit_factors:
            return [{"id": 1, "factor": self.unit_factors[1]}]
        return []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def test_issue1592_freeform(monkeypatch):
    monkeypatch.setenv("ODOO_BASE_URL", BASE_URL)
    monkeypatch.setenv("ODOO_DB", DB)
    monkeypatch.setenv("ODOO_LOGIN", LOGIN)
    monkeypatch.setenv("ODOO_API_KEY", API_KEY)
    monkeypatch.delenv("ODOO_CA_BUNDLE", raising=False)
    monkeypatch.delenv("ODOO_ALLOW_INSECURE", raising=False)

    # 1. The dozen line: both fixture purchase lines on confirmed
    #    orders. Line 21 is ordered in dozens (unit 3, factor 12) of a
    #    product whose own unit is Units (unit 1, factor 1): 10.0 x 12
    #    / 1 = 120.0. Line 35's unit is its product's unit, so it is
    #    taken as is: 88.0.
    line21 = _fixture_record("purchase_order_lines", 0)
    line35 = _fixture_record("purchase_order_lines", 1)
    server = _OdooServer(9, _purchase_records([line21, line35]), _fixture_units())
    with OdooClient(client=server.client()) as client:
        rows = client.read_open_purchase_lines(as_of=AS_OF)

    by_key = {row["business_key"]: row for row in rows}
    assert set(by_key) == {
        f"odoo:{DB}:purchase.order.line:21",
        f"odoo:{DB}:purchase.order.line:35",
    }
    row21 = by_key[f"odoo:{DB}:purchase.order.line:21"]
    row35 = by_key[f"odoo:{DB}:purchase.order.line:35"]
    assert row21["open_units"] == 120.0
    assert row21["item_code"] == "FURN_0269"
    assert row21["site_code"] == "SITE-A"
    assert row21["supplier_code"] == "V-60"
    assert row21["order_type"] == "purchase"
    assert row21["promised_on"] == "2026-10-15"
    assert row21["ordered_on"] == "2026-10-01"
    assert row21["as_of"] == AS_OF
    assert row21["source_system"] == "odoo"
    assert row35["open_units"] == 88.0
    assert row35["item_code"] == "FURN_9001"

    # The line read asks for the line's unit as its last field, and
    # the product read these methods make asks default_code and uom_id.
    assert server.models["purchase.order.line"]["fields"] == [
        [
            "product_id",
            "product_qty",
            "qty_received",
            "date_planned",
            "order_id",
            "uom_id",
        ]
    ]
    assert server.models["product.product"]["fields"] == [["default_code", "uom_id"]]

    # Exactly one uom.uom request: the sorted ids of the differing
    # lines' units (3) and their products' units (1), with the active
    # clause, and the factor alone.
    unit_info = server.models["uom.uom"]
    assert unit_info["calls"] == 1
    assert unit_info["domains"] == [
        [["id", "in", [1, 3]], ["active", "in", [True, False]]]
    ]
    assert unit_info["fields"] == [["factor"]]
    assert unit_info["kwargs"] == [{"fields": ["factor"], "offset": 0}]
    # Issued after every other request, which keep their order.
    assert [
        body["params"]["args"][3]
        for body in server.bodies
        if body["params"]["service"] == "object"
    ] == [
        "purchase.order.line",
        "purchase.order",
        "stock.picking.type",
        "stock.warehouse",
        "res.partner",
        "product.product",
        "uom.uom",
    ]

    # 2. Part received: line 21 with qty_received 2.0 (dozens) ->
    #    (10.0 - 2.0) x 12 / 1 = 96.0; line 35 is still 88.0.
    part21 = _fixture_record("purchase_order_lines", 0)
    part21["qty_received"] = 2.0
    server = _OdooServer(9, _purchase_records([part21, line35]), _fixture_units())
    with OdooClient(client=server.client()) as client:
        rows = client.read_open_purchase_lines(as_of=AS_OF)
    by_key = {row["business_key"]: row for row in rows}
    assert by_key[f"odoo:{DB}:purchase.order.line:21"]["open_units"] == 96.0
    assert by_key[f"odoo:{DB}:purchase.order.line:35"]["open_units"] == 88.0

    # 3. No unit request: line 21 in its product's own unit is taken
    #    as is (10.0 - 0.0 = 10.0) and no uom.uom request is made.
    same_unit21 = _fixture_record("purchase_order_lines", 0)
    same_unit21["uom_id"] = [1, "Units"]
    server = _OdooServer(9, _purchase_records([same_unit21, line35]), _fixture_units())
    with OdooClient(client=server.client()) as client:
        rows = client.read_open_purchase_lines(as_of=AS_OF)
    by_key = {row["business_key"]: row for row in rows}
    assert by_key[f"odoo:{DB}:purchase.order.line:21"]["open_units"] == 10.0
    assert server.models["uom.uom"]["calls"] == 0

    # 4. Unusable: the fake holds no unit 3, so line 21's factor is
    #    missing and the line is skipped the way a line with no
    #    quantity is; line 35 is still answered.
    server = _OdooServer(
        9, _purchase_records([line21, line35]), _fixture_units(exclude=3)
    )
    with OdooClient(client=server.client()) as client:
        rows = client.read_open_purchase_lines(as_of=AS_OF)
    by_key = {row["business_key"]: row for row in rows}
    assert f"odoo:{DB}:purchase.order.line:21" not in by_key
    assert by_key[f"odoo:{DB}:purchase.order.line:35"]["open_units"] == 88.0
    # The differing unit still triggers the read; the fake simply
    # answers no unit 3.
    assert server.models["uom.uom"]["calls"] == 1
    assert server.models["uom.uom"]["domains"] == [
        [["id", "in", [1, 3]], ["active", "in", [True, False]]]
    ]

    # 5. A sales line in dozens: the fixture's line, varied to product
    #    31 in Dozens (2.0 ordered, 0.5 delivered) on a state "sale"
    #    order -> (2.0 - 0.5) x 12 / 1 = 18.0.
    sale_line = _fixture_record("sale_order_lines", 0)
    sale_line["product_id"] = [31, "[FURN_0269] Office Chair Black"]
    sale_line["product_uom_id"] = [3, "Dozens"]
    sale_line["product_uom_qty"] = 2.0
    sale_line["qty_delivered"] = 0.5
    server = _OdooServer(
        9, _sales_records([sale_line], SALE_ORDERS[0]), _fixture_units()
    )
    with OdooClient(client=server.client()) as client:
        rows = client.read_open_sales_lines(as_of=AS_OF)
    assert len(rows) == 1
    row = rows[0]
    assert row["business_key"] == f"odoo:{DB}:sale.order.line:8"
    assert row["item_code"] == "FURN_0269"
    assert row["site_code"] == "WH-1"
    assert row["order_type"] == "SO"
    assert row["open_units"] == 18.0
    assert row["ship_on"] == "2026-11-05"
    assert row["source_system"] == "odoo"
    assert server.models["uom.uom"]["calls"] == 1
    assert server.models["uom.uom"]["domains"] == [
        [["id", "in", [1, 3]], ["active", "in", [True, False]]]
    ]
    assert server.models["uom.uom"]["fields"] == [["factor"]]

    # 6. A closed order: the same line on a state "done" order, with
    #    the fake holding no unit 3, still answers a row — a closed
    #    line is zeroed, not skipped.
    closed_line = _fixture_record("sale_order_lines", 0)
    closed_line["product_id"] = [31, "[FURN_0269] Office Chair Black"]
    closed_line["product_uom_id"] = [3, "Dozens"]
    closed_line["product_uom_qty"] = 2.0
    closed_line["qty_delivered"] = 0.5
    closed_order = dict(SALE_ORDERS[0])
    closed_order["state"] = "done"
    server = _OdooServer(
        9, _sales_records([closed_line], closed_order), _fixture_units(exclude=3)
    )
    with OdooClient(client=server.client()) as client:
        rows = client.read_open_sales_lines(as_of=AS_OF)
    assert len(rows) == 1
    row = rows[0]
    assert row["business_key"] == f"odoo:{DB}:sale.order.line:8"
    assert row["open_units"] == 0.0

    # 7. A stock move in dozens: the fixture's move, varied to product
    #    31 in Dozens with quantity 3.0 -> 3.0 x 12 / 1 = 36.0.
    move = _fixture_record("stock_moves", 0)
    move["product_id"] = [31, "[FURN_0269] Office Chair Black"]
    move["uom_id"] = [3, "Dozens"]
    move["quantity"] = 3.0
    server = _OdooServer(
        9, _demand_records([move], _fixture_products()), _fixture_units()
    )
    with OdooClient(client=server.client()) as client:
        rows = client.read_demand_events()
    assert len(rows) == 1
    row = rows[0]
    assert row["business_key"] == f"odoo:{DB}:stock.move:62"
    assert row["item_code"] == "FURN_0269"
    assert row["site_code"] == "SITE-A"
    assert row["quantity"] == 36.0
    assert row["occurred_on"] == "2025-11-16"
    assert row["event_type"] == "shipment"
    assert row["source_system"] == "odoo"
    assert server.models["uom.uom"]["calls"] == 1
    assert server.models["uom.uom"]["domains"] == [
        [["id", "in", [1, 3]], ["active", "in", [True, False]]]
    ]
    assert server.models["uom.uom"]["fields"] == [["factor"]]

    # 8. Taken as is: the fixture's move, unchanged (its unit is its
    #    product's unit), answers quantity 3.0 with no uom.uom
    #    request.
    move = _fixture_record("stock_moves", 0)
    products = _fixture_products()
    for product in products:
        if product["id"] == 23:
            product["default_code"] = "LARGE-DESK"
    server = _OdooServer(9, _demand_records([move], products), _fixture_units())
    with OdooClient(client=server.client()) as client:
        rows = client.read_demand_events()
    assert len(rows) == 1
    row = rows[0]
    assert row["business_key"] == f"odoo:{DB}:stock.move:62"
    assert row["item_code"] == "LARGE-DESK"
    assert row["quantity"] == 3.0
    assert server.models["uom.uom"]["calls"] == 0

    # 9. The other reads: read_on_hand still requests the product
    #    fields ["default_code"] and answers its rows as before.
    quant = {
        "id": 9001,
        "product_id": [31, "[FURN_0269] Office Chair Black"],
        "location_id": [6, "WH/Stock"],
        "quantity": 4.0,
    }
    server = _OdooServer(
        9,
        {
            "stock.quant": [quant],
            "product.product": _fixture_products(),
            "stock.location": LOCATIONS,
            "stock.warehouse": WAREHOUSES,
        },
        _fixture_units(),
    )
    with OdooClient(client=server.client()) as client:
        rows = client.read_on_hand(as_of=AS_OF)
    assert len(rows) == 1
    row = rows[0]
    assert row["item_code"] == "FURN_0269"
    assert row["site_code"] == "SITE-A"
    assert row["quantity_on_hand"] == 4.0
    assert row["as_of"] == AS_OF
    assert row["source_system"] == "odoo"
    assert server.models["product.product"]["fields"] == [["default_code"]]
    assert server.models["product.product"]["kwargs"] == [
        {"fields": ["default_code"], "offset": 0}
    ]
    assert server.models["uom.uom"]["calls"] == 0

    # 10. read_lead_time_observations still requests the product
    #     fields ["default_code"]; the fixture's move carries no
    #     purchase_line_id, so it answers no row.
    receipt = _fixture_record("stock_moves", 0)
    server = _OdooServer(
        9,
        {
            "stock.move": [receipt],
            "product.product": _fixture_products(),
        },
        _fixture_units(),
    )
    with OdooClient(client=server.client()) as client:
        rows = client.read_lead_time_observations()
    assert rows == []
    assert server.models["stock.move"]["fields"] == [
        ["product_id", "location_dest_id", "date", "purchase_line_id"]
    ]
    assert server.models["product.product"]["fields"] == [["default_code"]]
    assert server.models["product.product"]["kwargs"] == [
        {"fields": ["default_code"], "offset": 0, "order": "id asc"}
    ]
    assert server.models["uom.uom"]["calls"] == 0
