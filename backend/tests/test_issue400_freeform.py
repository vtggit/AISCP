"""Issue #400 proof: OdooClient.read_lead_time_observations with the server fully mocked.

With the Odoo server fully mocked (httpx.MockTransport answers every
/jsonrpc POST, so no network), the transport is stubbed so each model's
search_read is answered by this test. It checks:

  * a done supplier receipt resolving end to end: business_key a
    namespaced identifier carrying "odoo", the ODOO_DB database name
    and the stock.move id (not a bare id), item_code the product's
    default_code, supplier_code the vendor's ref, site_code the
    destination warehouse code, ordered_on the date part of the PO's
    date_order, received_on the date part of the move's date,
    source_system "odoo", and no lead_time_days key (the database
    computes it);
  * a vendor with a blank ref falling back to the vendor's name for
    supplier_code;
  * a receipt whose destination resolves to no warehouse (and one that
    resolves to a warehouse with no code) still emitting its row with
    a null site_code;
  * a move with no purchase_line_id, one whose product has no
    default_code, and one with an unparseable or missing date, all
    skipped with no error, together with a receipt that precedes its
    PO's order date (the receipt_before_order skip), a PO without a
    date_order, and a vendor with neither a usable ref nor a name;
  * two receipts of the same purchase line keeping their own
    stock.move business keys, so the business_key-only conflict on
    lead_time_observations stores both observations instead of folding
    one into the other;
  * the domain sent to stock.move requiring state = done and a
    supplier source, and the purchase-line, order, partner, product,
    location and warehouse reads each issued once over the ids seen
    (an id in [...] domain), not once per move;
  * no moves at all returning [] with no purchase-line, order,
    partner, product, location or warehouse reads, and the method
    performing reads only (the mock fails loudly on any
    non-search_read call, so no write can slip through).
"""

import json

import httpx

from app.feed.odoo_client import OdooClient
from app.models.lead_time_observations import LeadTimeObservationUpsert

BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"

MOVES = [
    # Kept: product 11 (P-1), destination 201 -> warehouse 31 (SITE-A),
    # purchase line 31 -> PO 41 (vendor 51, ref V-51), ordered
    # 2026-10-01 10:00:00, received 2026-10-12 09:15:00.
    {
        "id": 601,
        "product_id": 11,
        "location_dest_id": 201,
        "date": "2026-10-12 09:15:00",
        "purchase_line_id": 31,
    },
    # Kept: same purchase line 31 (same PO, same vendor, same order
    # date) but a different stock.move receipt -> its own row with its
    # own business_key; the business_key-only table conflict stores
    # both observations.
    {
        "id": 602,
        "product_id": 11,
        "location_dest_id": 201,
        "date": "2026-10-20 11:30:00",
        "purchase_line_id": 31,
    },
    # Kept: vendor 52 has a blank ref, so supplier_code falls back to
    # the vendor's name.
    {
        "id": 603,
        "product_id": 12,
        "location_dest_id": 203,
        "date": "2026-10-05 08:00:00",
        "purchase_line_id": 32,
    },
    # Kept: destination 204 resolves to no warehouse -> site_code
    # null, the row is still emitted.
    {
        "id": 604,
        "product_id": 11,
        "location_dest_id": 204,
        "date": "2026-10-08T08:30:00",
        "purchase_line_id": 33,
    },
    # Kept: destination 205 resolves to warehouse 33, which has no
    # code -> site_code null, the row is still emitted.
    {
        "id": 605,
        "product_id": 12,
        "location_dest_id": 205,
        "date": "2026-10-09",
        "purchase_line_id": 33,
    },
    # Skipped, no error: no purchase_line_id.
    {
        "id": 606,
        "product_id": 11,
        "location_dest_id": 201,
        "date": "2026-10-10",
        "purchase_line_id": False,
    },
    # Skipped, no error: product 13 has no default_code.
    {
        "id": 607,
        "product_id": 13,
        "location_dest_id": 201,
        "date": "2026-10-10",
        "purchase_line_id": 31,
    },
    # Skipped, no error: unparseable date.
    {
        "id": 608,
        "product_id": 11,
        "location_dest_id": 201,
        "date": "October 2026",
        "purchase_line_id": 31,
    },
    # Skipped, no error: missing date.
    {"id": 609, "product_id": 11, "location_dest_id": 201, "purchase_line_id": 31},
    # Skipped, no error: received_on (2026-09-01) precedes ordered_on
    # (2026-10-01) -> the receipt_before_order skip.
    {
        "id": 610,
        "product_id": 11,
        "location_dest_id": 201,
        "date": "2026-09-01",
        "purchase_line_id": 34,
    },
    # Skipped, no error: vendor 54 has neither a usable ref nor a name.
    {
        "id": 611,
        "product_id": 11,
        "location_dest_id": 201,
        "date": "2026-10-10",
        "purchase_line_id": 35,
    },
    # Skipped, no error: PO 46 has no date_order.
    {
        "id": 612,
        "product_id": 11,
        "location_dest_id": 201,
        "date": "2026-10-10",
        "purchase_line_id": 36,
    },
]

PURCHASE_LINES = [
    {"id": 31, "order_id": 41},
    {"id": 32, "order_id": 42},
    {"id": 33, "order_id": 42},
    {"id": 34, "order_id": 44},
    {"id": 35, "order_id": 45},
    {"id": 36, "order_id": 46},
]

ORDERS = [
    {"id": 41, "date_order": "2026-10-01 10:00:00", "partner_id": 51},
    {"id": 42, "date_order": "2026-09-20 14:30:00", "partner_id": 52},
    {"id": 44, "date_order": "2026-10-01", "partner_id": 51},
    {"id": 45, "date_order": "2026-09-15", "partner_id": 54},
    {"id": 46, "date_order": False, "partner_id": 51},
]

PARTNERS = [
    {"id": 51, "ref": "V-51", "name": "Acme Fasteners"},
    # Blank (whitespace-only) ref: supplier_code must fall back to the
    # vendor's name.
    {"id": 52, "ref": "   ", "name": "Globex Metals"},
    # Neither a usable ref nor a name.
    {"id": 54, "ref": False, "name": False},
]

PRODUCTS = [
    {"id": 11, "default_code": "P-1"},
    {"id": 12, "default_code": "P-2"},
    {"id": 13, "default_code": False},
]

LOCATIONS = [
    {"id": 201, "warehouse_id": 31},
    {"id": 203, "warehouse_id": 32},
    {"id": 204, "warehouse_id": False},
    {"id": 205, "warehouse_id": 33},
]

WAREHOUSES = [
    {"id": 31, "code": "SITE-A"},
    {"id": 32, "code": "SITE-B"},
    {"id": 33, "code": False},
]

EXPECTED = [
    {
        "business_key": f"odoo:{DB}:stock.move:601",
        "supplier_code": "V-51",
        "item_code": "P-1",
        "site_code": "SITE-A",
        "ordered_on": "2026-10-01",
        "received_on": "2026-10-12",
        "source_system": "odoo",
    },
    {
        "business_key": f"odoo:{DB}:stock.move:602",
        "supplier_code": "V-51",
        "item_code": "P-1",
        "site_code": "SITE-A",
        "ordered_on": "2026-10-01",
        "received_on": "2026-10-20",
        "source_system": "odoo",
    },
    {
        "business_key": f"odoo:{DB}:stock.move:603",
        "supplier_code": "Globex Metals",
        "item_code": "P-2",
        "site_code": "SITE-B",
        "ordered_on": "2026-09-20",
        "received_on": "2026-10-05",
        "source_system": "odoo",
    },
    {
        "business_key": f"odoo:{DB}:stock.move:604",
        "supplier_code": "Globex Metals",
        "item_code": "P-1",
        "site_code": None,
        "ordered_on": "2026-09-20",
        "received_on": "2026-10-08",
        "source_system": "odoo",
    },
    {
        "business_key": f"odoo:{DB}:stock.move:605",
        "supplier_code": "Globex Metals",
        "item_code": "P-2",
        "site_code": None,
        "ordered_on": "2026-09-20",
        "received_on": "2026-10-09",
        "source_system": "odoo",
    },
]

SKIPPED_MOVE_IDS = (606, 607, 608, 609, 610, 611, 612)


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


def test_issue400_freeform(monkeypatch):
    _set_env(monkeypatch)
    server = _OdooServer(
        9,
        {
            "stock.move": MOVES,
            "purchase.order.line": PURCHASE_LINES,
            "purchase.order": ORDERS,
            "res.partner": PARTNERS,
            "product.product": PRODUCTS,
            "stock.location": LOCATIONS,
            "stock.warehouse": WAREHOUSES,
        },
    )
    with OdooClient(client=server.client()) as client:
        rows = client.read_lead_time_observations()

    # 1. The kept receipts map end to end, each shaped for
    #    LeadTimeObservationUpsert and carrying no lead_time_days key
    #    (the database computes it from received_on - ordered_on).
    assert rows == EXPECTED
    for row in rows:
        assert "lead_time_days" not in row
        LeadTimeObservationUpsert(**row)

    # 2. The business_key is a namespaced, globally unique composite:
    #    source system, the ODOO_DB database name, the entity type and
    #    the stock.move id - never a bare id that could collide with
    #    another source's key.
    for row, move_id in zip(rows, (601, 602, 603, 604, 605)):
        assert row["business_key"] == f"odoo:{DB}:stock.move:{move_id}"
        assert row["business_key"].split(":") == [
            "odoo",
            DB,
            "stock.move",
            str(move_id),
        ]
    assert len({row["business_key"] for row in rows}) == len(rows)

    by_key = {row["business_key"]: row for row in rows}

    # 3. supplier_code: the vendor ref where it is a non-empty string
    #    (601/602/604... vendor 51), the vendor name for the blank-ref
    #    vendor 52 (603/604/605).
    assert by_key[f"odoo:{DB}:stock.move:601"]["supplier_code"] == "V-51"
    assert by_key[f"odoo:{DB}:stock.move:603"]["supplier_code"] == "Globex Metals"

    # 4. site_code: the destination warehouse code where it resolves,
    #    null (the row still emitted) where it does not.
    assert by_key[f"odoo:{DB}:stock.move:601"]["site_code"] == "SITE-A"
    assert by_key[f"odoo:{DB}:stock.move:603"]["site_code"] == "SITE-B"
    assert by_key[f"odoo:{DB}:stock.move:604"]["site_code"] is None
    assert by_key[f"odoo:{DB}:stock.move:605"]["site_code"] is None

    # 5. ordered_on / received_on carry the date part only, never the
    #    time of the PO's date_order or the move's date.
    assert by_key[f"odoo:{DB}:stock.move:601"]["ordered_on"] == "2026-10-01"
    assert by_key[f"odoo:{DB}:stock.move:601"]["received_on"] == "2026-10-12"
    assert by_key[f"odoo:{DB}:stock.move:603"]["ordered_on"] == "2026-09-20"
    assert by_key[f"odoo:{DB}:stock.move:603"]["received_on"] == "2026-10-05"

    # 6. Two receipts of the same purchase line (moves 601 and 602 both
    #    sit on line 31) keep their own stock.move business keys, so
    #    the business_key-only conflict on lead_time_observations
    #    stores both observations instead of folding one into the
    #    other.
    first = by_key[f"odoo:{DB}:stock.move:601"]
    second = by_key[f"odoo:{DB}:stock.move:602"]
    assert first["supplier_code"] == second["supplier_code"]
    assert first["item_code"] == second["item_code"]
    assert first["ordered_on"] == second["ordered_on"]
    assert first["business_key"] != second["business_key"]

    # 7. The skipped moves produced no rows and no error: no
    #    purchase_line_id (606), product without a default_code (607),
    #    unparseable date (608), missing date (609), receipt before
    #    order (610), vendor without a usable code (611), PO without a
    #    date_order (612).
    skipped = {f"odoo:{DB}:stock.move:{move_id}" for move_id in SKIPPED_MOVE_IDS}
    assert skipped.isdisjoint({row["business_key"] for row in rows})

    # 8. The stock.move read requires state = done and a supplier
    #    source, and reads exactly the four fields.
    move_info = server.models["stock.move"]
    assert move_info["calls"] == 1
    assert move_info["domains"] == [
        [["state", "=", "done"], ["location_id.usage", "=", "supplier"]]
    ]
    # 8a. The domain pins the supplier *source* location of a
    #     receipt, never its destination: supplier stock flows out
    #     of a supplier location, so the source usage must be supplier
    #     and the destination usage must not be constrained.
    move_domain = move_info["domains"][0]
    assert ["location_id.usage", "=", "supplier"] in move_domain
    assert ["location_dest_id.usage", "=", "supplier"] not in move_domain
    assert move_info["fields"] == [
        ["product_id", "location_dest_id", "date", "purchase_line_id"]
    ]
    assert move_info["kwargs"] == [
        {
            "fields": ["product_id", "location_dest_id", "date", "purchase_line_id"],
            "offset": 0,
            "order": "id asc",
        }
    ]

    # 9. The purchase-line, order, partner, product, location and
    #    warehouse reads are each exactly one search_read over the ids
    #    actually seen (id in [...]), not one call per move.
    line_info = server.models["purchase.order.line"]
    assert line_info["calls"] == 1
    assert line_info["domains"] == [[["id", "in", [31, 32, 33, 34, 35, 36]]]]
    assert line_info["fields"] == [["order_id"]]

    order_info = server.models["purchase.order"]
    assert order_info["calls"] == 1
    assert order_info["domains"] == [[["id", "in", [41, 42, 44, 45, 46]]]]
    assert order_info["fields"] == [["date_order", "partner_id"]]

    partner_info = server.models["res.partner"]
    assert partner_info["calls"] == 1
    assert partner_info["domains"] == [[["id", "in", [51, 52, 54]]]]
    assert partner_info["fields"] == [["ref", "name"]]

    product_info = server.models["product.product"]
    assert product_info["calls"] == 1
    assert product_info["domains"] == [[["id", "in", [11, 12, 13]]]]
    assert product_info["fields"] == [["default_code"]]

    location_info = server.models["stock.location"]
    assert location_info["calls"] == 1
    assert location_info["domains"] == [[["id", "in", [201, 203, 204, 205]]]]
    assert location_info["fields"] == [["warehouse_id"]]

    warehouse_info = server.models["stock.warehouse"]
    assert warehouse_info["calls"] == 1
    assert warehouse_info["domains"] == [[["id", "in", [31, 32, 33]]]]
    assert warehouse_info["fields"] == [["code"]]

    # 1 + 6 model reads: authenticate, stock.move, purchase line,
    # order, partner, product, location and warehouse.
    assert len(server.requests) == 8

    # 10. No moves at all: [] and no purchase-line, order, partner,
    #     product, location or warehouse reads (each omitted when no id
    #     is seen).
    _set_env(monkeypatch)
    server = _OdooServer(9, {"stock.move": []})
    with OdooClient(client=server.client()) as client:
        assert client.read_lead_time_observations() == []
    assert len(server.requests) == 2
    assert len(server.bodies) == 2
