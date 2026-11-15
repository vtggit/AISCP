"""Issue #410 proof: OdooClient.read_open_purchase_lines, fully mocked.

With the Odoo server fully mocked (httpx.MockTransport answers every
/jsonrpc POST, so no network), the transport is stubbed so each model's
search_read is answered by this test. It checks:

  * an open confirmed line resolving end to end: business_key the
    namespaced odoo:<db>:purchase.order.line:<id> (not a bare id),
    item_code the product's default_code, site_code the receiving
    warehouse code, supplier_code the vendor ref, order_type
    "purchase", open_units product_qty - qty_received, promised_on the
    date part of date_planned, ordered_on the date part of date_order,
    and source_system "odoo";
  * a vendor with a blank ref falling back to name, and a line whose
    date_planned is missing giving promised_on null;
  * a line whose qty_received is missing being kept with open_units
    equal to product_qty (treated as 0.0), not skipped;
  * a line whose product has no default_code, one whose order has no
    resolvable warehouse code, and one whose open_units is zero or
    negative, all skipped (never errors);
  * the purchase.order.line domain requiring a confirmed order (and
    not attempting a field-to-field comparison), with the still-open
    filter applied in Python: the mock answers the line read with every
    line, fully received and over-received ones included;
  * as_of passed through when given, else matching
    YYYY-MM-DDTHH:MM:SSZ;
  * each secondary read (purchase.order, stock.picking.type,
    stock.warehouse, res.partner, product.product) issued exactly once
    over the ids seen (an id in [...] domain), each omitted entirely
    when no id is seen;
  * no writes: the mock answers search_read only and fails loudly on
    any other method.
"""

import json
import re

import httpx

from app.feed.odoo_client import OdooClient
from app.models.open_purchase_lines import OpenPurchaseLineUpsert

BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"
AS_OF = "2026-10-16T00:00:00Z"
AS_OF_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

LINES = [
    # 601: the open confirmed line resolving end to end.
    {
        "id": 601,
        "product_id": 11,
        "product_qty": 10,
        "qty_received": 4,
        "date_planned": "2026-11-02 09:30:00",
        "order_id": 401,
    },
    # 602: vendor 61 has a blank ref -> supplier_code falls back to
    # the vendor's name; date_planned missing -> promised_on null.
    {
        "id": 602,
        "product_id": 12,
        "product_qty": 5,
        "qty_received": 0,
        "date_planned": False,
        "order_id": 402,
    },
    # 603: qty_received missing (no key at all) -> treated as 0.0, so
    # open_units equals product_qty and the line is kept.
    {
        "id": 603,
        "product_id": 14,
        "product_qty": 3,
        "date_planned": "2026-11-10",
        "order_id": 401,
    },
    # 604: product 13 has no default_code -> skipped.
    {
        "id": 604,
        "product_id": 13,
        "product_qty": 7,
        "qty_received": 0,
        "date_planned": "2026-11-12",
        "order_id": 401,
    },
    # 605: order 403's picking type resolves to warehouse 33, which
    # has no code -> skipped.
    {
        "id": 605,
        "product_id": 11,
        "product_qty": 8,
        "qty_received": 0,
        "date_planned": "2026-11-13",
        "order_id": 403,
    },
    # 606: fully received (product_qty == qty_received) -> open_units
    # zero -> skipped by the Python still-open filter.
    {
        "id": 606,
        "product_id": 11,
        "product_qty": 6,
        "qty_received": 6,
        "date_planned": "2026-11-14",
        "order_id": 401,
    },
    # 607: over-received -> open_units negative -> skipped.
    {
        "id": 607,
        "product_id": 11,
        "product_qty": 2,
        "qty_received": 5,
        "date_planned": "2026-11-15",
        "order_id": 401,
    },
]

ORDERS = [
    {
        "id": 401,
        "date_order": "2026-10-01 14:00:00",
        "partner_id": 60,
        "picking_type_id": 41,
    },
    {
        "id": 402,
        "date_order": "2026-10-05",
        "partner_id": 61,
        "picking_type_id": 41,
    },
    # Order 403: its picking type 42 belongs to warehouse 33, which
    # has no code -> every line of this order is skipped.
    {
        "id": 403,
        "date_order": "2026-10-06",
        "partner_id": 60,
        "picking_type_id": 42,
    },
]

PICKING_TYPES = [
    {"id": 41, "warehouse_id": 31},
    {"id": 42, "warehouse_id": 33},
]

WAREHOUSES = [
    {"id": 31, "code": "SITE-A"},
    {"id": 33, "code": False},
]

PARTNERS = [
    {"id": 60, "ref": "V-60", "name": "Vendor Sixty"},
    # Blank ref -> supplier_code falls back to the name.
    {"id": 61, "ref": "   ", "name": "Vendor Sixty-One"},
]

PRODUCTS = [
    {"id": 11, "default_code": "P-11"},
    {"id": 12, "default_code": "P-12"},
    # No default_code -> lines of this product are skipped.
    {"id": 13, "default_code": False},
    {"id": 14, "default_code": "P-14"},
]

EXPECTED = [
    {
        "business_key": f"odoo:{DB}:purchase.order.line:601",
        "item_code": "P-11",
        "site_code": "SITE-A",
        "supplier_code": "V-60",
        "order_type": "purchase",
        "open_units": 6.0,
        "promised_on": "2026-11-02",
        "ordered_on": "2026-10-01",
        "as_of": AS_OF,
        "source_system": "odoo",
    },
    {
        "business_key": f"odoo:{DB}:purchase.order.line:602",
        "item_code": "P-12",
        "site_code": "SITE-A",
        "supplier_code": "Vendor Sixty-One",
        "order_type": "purchase",
        "open_units": 5.0,
        "promised_on": None,
        "ordered_on": "2026-10-05",
        "as_of": AS_OF,
        "source_system": "odoo",
    },
    {
        "business_key": f"odoo:{DB}:purchase.order.line:603",
        "item_code": "P-14",
        "site_code": "SITE-A",
        "supplier_code": "V-60",
        "order_type": "purchase",
        "open_units": 3.0,
        "promised_on": "2026-11-10",
        "ordered_on": "2026-10-01",
        "as_of": AS_OF,
        "source_system": "odoo",
    },
]


def _records_by_model(lines):
    return {
        "purchase.order.line": lines,
        "purchase.order": ORDERS,
        "stock.picking.type": PICKING_TYPES,
        "stock.warehouse": WAREHOUSES,
        "res.partner": PARTNERS,
        "product.product": PRODUCTS,
    }


class _OdooServer:
    """MockTransport handler answering each model's search_read by name.

    Records every request; an unknown model, a write, or any method
    other than search_read fails the test loudly. The line read always
    returns the full line list regardless of the domain: applying the
    confirmed-order filter and the still-open filter is the client's
    job, never the mock's.
    """

    def __init__(self, uid, lines=None):
        self.uid = uid
        self.records = _records_by_model(LINES if lines is None else lines)
        self.models = {
            model: {"calls": 0, "domains": [], "fields": [], "kwargs": []}
            for model in self.records
        }
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
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


def test_issue410_freeform(monkeypatch):
    monkeypatch.setenv("ODOO_BASE_URL", BASE_URL)
    monkeypatch.setenv("ODOO_DB", DB)
    monkeypatch.setenv("ODOO_LOGIN", LOGIN)
    monkeypatch.setenv("ODOO_API_KEY", API_KEY)
    monkeypatch.delenv("ODOO_CA_BUNDLE", raising=False)
    monkeypatch.delenv("ODOO_ALLOW_INSECURE", raising=False)

    # 1. With as_of given: exactly the three open lines are returned,
    #    each one row shaped for OpenPurchaseLineUpsert, with as_of
    #    passed through untouched.
    server = _OdooServer(9)
    with OdooClient(client=server.client()) as client:
        rows = client.read_open_purchase_lines(as_of=AS_OF)

    assert rows == EXPECTED
    for row in rows:
        upsert = OpenPurchaseLineUpsert(**row)
        assert upsert.source_system == "odoo"
        assert upsert.order_type == "purchase"

    by_key = {row["business_key"]: row for row in rows}

    # 2. business_key is the namespaced
    #    odoo:<db>:purchase.order.line:<id>, never a bare id, and only
    #    the open lines 601/602/603 are present: 604 (no default_code),
    #    605 (no resolvable warehouse code), 606 (open_units zero) and
    #    607 (open_units negative) are skipped, not errors.
    assert set(by_key) == {
        f"odoo:{DB}:purchase.order.line:{line_id}" for line_id in (601, 602, 603)
    }
    for key in by_key:
        assert re.fullmatch(rf"odoo:{DB}:purchase\.order\.line:\d+", key)

    # 3. The end-to-end line 601: every field from the right source.
    line_601 = by_key[f"odoo:{DB}:purchase.order.line:601"]
    assert line_601["item_code"] == "P-11"
    assert line_601["site_code"] == "SITE-A"
    assert line_601["supplier_code"] == "V-60"
    assert line_601["open_units"] == 6.0
    assert line_601["promised_on"] == "2026-11-02"
    assert line_601["ordered_on"] == "2026-10-01"

    # 4. Vendor 61's blank ref falls back to the name (line 602), and
    #    the missing date_planned gives promised_on null.
    line_602 = by_key[f"odoo:{DB}:purchase.order.line:602"]
    assert line_602["supplier_code"] == "Vendor Sixty-One"
    assert line_602["promised_on"] is None
    assert line_602["open_units"] == 5.0

    # 5. Line 603's missing qty_received is treated as 0.0: the line
    #    is kept with open_units equal to product_qty, not skipped.
    line_603 = by_key[f"odoo:{DB}:purchase.order.line:603"]
    assert line_603["open_units"] == 3.0

    # 6. The purchase.order.line read: exactly one domain term,
    #    requiring a confirmed order. The single "in" term over
    #    order_id.state is not a field-to-field comparison (an Odoo
    #    domain cannot compare two fields); the still-open test
    #    (product_qty > qty_received) is applied in Python, proven by
    #    the mock answering this one read with every line - fully
    #    received 606 and over-received 607 included - and the rows
    #    above containing neither.
    line_info = server.models["purchase.order.line"]
    assert line_info["calls"] == 1
    assert line_info["domains"] == [[["order_id.state", "in", ["purchase", "done"]]]]
    domain = line_info["domains"][0]
    assert len(domain) == 1
    assert domain[0][1] == "in"
    assert line_info["fields"] == [
        ["product_id", "product_qty", "qty_received", "date_planned", "order_id"]
    ]
    assert line_info["kwargs"] == [
        {
            "fields": [
                "product_id",
                "product_qty",
                "qty_received",
                "date_planned",
                "order_id",
            ],
            "offset": 0,
        }
    ]

    # 7. Each secondary read is exactly one search_read over the ids
    #    actually seen (an id in [...] domain).
    order_info = server.models["purchase.order"]
    assert order_info["calls"] == 1
    assert order_info["domains"] == [[["id", "in", [401, 402, 403]]]]
    assert order_info["fields"] == [["date_order", "partner_id", "picking_type_id"]]
    assert order_info["kwargs"] == [
        {"fields": ["date_order", "partner_id", "picking_type_id"], "offset": 0}
    ]

    picking_info = server.models["stock.picking.type"]
    assert picking_info["calls"] == 1
    assert picking_info["domains"] == [[["id", "in", [41, 42]]]]
    assert picking_info["fields"] == [["warehouse_id"]]
    assert picking_info["kwargs"] == [{"fields": ["warehouse_id"], "offset": 0}]

    warehouse_info = server.models["stock.warehouse"]
    assert warehouse_info["calls"] == 1
    assert warehouse_info["domains"] == [[["id", "in", [31, 33]]]]
    assert warehouse_info["fields"] == [["code"]]
    assert warehouse_info["kwargs"] == [{"fields": ["code"], "offset": 0}]

    partner_info = server.models["res.partner"]
    assert partner_info["calls"] == 1
    assert partner_info["domains"] == [[["id", "in", [60, 61]]]]
    assert partner_info["fields"] == [["ref", "name"]]
    assert partner_info["kwargs"] == [{"fields": ["ref", "name"], "offset": 0}]

    product_info = server.models["product.product"]
    assert product_info["calls"] == 1
    assert product_info["domains"] == [[["id", "in", [11, 12, 13, 14]]]]
    assert product_info["fields"] == [["default_code"]]
    assert product_info["kwargs"] == [{"fields": ["default_code"], "offset": 0}]

    # 8. Without as_of: every row carries the current UTC time as
    #    YYYY-MM-DDTHH:MM:SSZ.
    server2 = _OdooServer(9)
    with OdooClient(client=server2.client()) as client:
        rows2 = client.read_open_purchase_lines()
    assert len(rows2) == 3
    for row in rows2:
        assert AS_OF_RE.fullmatch(row["as_of"])

    # 9. No open confirmed lines at all: [] with every secondary read
    #    omitted entirely (no id is seen), and still no write (the
    #    mock answers search_read only).
    empty = _OdooServer(9, lines=[])
    with OdooClient(client=empty.client()) as client:
        assert client.read_open_purchase_lines(as_of=AS_OF) == []
    assert empty.models["purchase.order.line"]["calls"] == 1
    for model in (
        "purchase.order",
        "stock.picking.type",
        "stock.warehouse",
        "res.partner",
        "product.product",
    ):
        assert empty.models[model]["calls"] == 0
