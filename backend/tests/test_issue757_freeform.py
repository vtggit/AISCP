"""Issue #757 proof: OdooClient.read_open_sales_lines and its sync slot.

With the Odoo server fully mocked (httpx.MockTransport answers every
/jsonrpc POST, so no network), the transport is stubbed the way
test_issue405_freeform.py does it: it answers ``authenticate`` and
``search_read`` only, and any write fails loudly. It checks:

  * the line read: exactly one ``sale.order.line`` search_read with
    the stated domain (``order_id.state`` in ``sale``/``done``/
    ``cancel``, ``display_type`` = False, ``is_service`` = False) and
    the stated fields — and the domain contains no ``commitment_date``
    or ``expected_date`` recency filter, openness being decided by the
    parent order state alone;
  * the rows: a line of 15 ordered and 12 delivered maps to
    ``open_units`` 3; a fully delivered line is kept with
    ``open_units`` 0; a line whose parent order state is ``"done"``
    and a line whose parent order state is ``"cancel"`` each map to
    ``open_units`` 0, so a line that left the confirmed/open state is
    zeroed on the next sync instead of being left stale (the read
    model is upsert-only);
  * ``ship_on``: the date part of the order's ``commitment_date``
    when set, else of its ``expected_date``, else null;
  * a line whose product has no ``default_code`` and a line whose
    warehouse has no ``code`` contribute no row; every row carries
    ``source_system`` ``"odoo"``, ``order_type`` ``"SO"``, the
    namespaced ``odoo:<db>:sale.order.line:<id>`` business key, and
    the given ``as_of``;
  * the lookups: one search_read each on ``product.product``
    (``default_code``), ``stock.warehouse`` (``code``) and
    ``sale.order`` (``state``, ``commitment_date``,
    ``expected_date``), each over the ids actually seen (an ``id in
    [...]`` domain) — and none of them called at all when no line is
    read;
  * the sync: with a stub client and stub repositories, the sixth
    repository receives the reader's exact list (the same list
    object) and the result carries ``"open_sales_lines"``; a failing
    sixth read leaves every repository unwritten.
"""

import json
import re

import httpx
import pytest

from app.feed.odoo_client import OdooClient, OdooError
from app.sync.odoo import run_sync

BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"
AS_OF = "2026-10-29T09:00:00Z"
AS_OF_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

LINE_DOMAIN = [
    ["order_id.state", "in", ["sale", "done", "cancel"]],
    ["display_type", "=", False],
    ["is_service", "=", False],
]
LINE_FIELDS = [
    "order_id",
    "product_id",
    "product_uom_qty",
    "qty_delivered",
    "warehouse_id",
    "product_uom_id",
]

LINES = [
    # 701: 15 ordered, 12 delivered, parent order 401 in state
    # "sale" -> open_units 3; ship_on from the commitment_date.
    {
        "id": 701,
        "order_id": 401,
        "product_id": 11,
        "warehouse_id": 21,
        "product_uom_qty": 15,
        "qty_delivered": 12,
    },
    # 702: fully delivered (10 of 10), parent order 402 in state
    # "sale" -> kept with open_units 0; the order has no
    # commitment_date, so ship_on falls back to the expected_date.
    {
        "id": 702,
        "order_id": 402,
        "product_id": 11,
        "warehouse_id": 21,
        "product_uom_qty": 10,
        "qty_delivered": 10,
    },
    # 703: product 12 has no default_code -> contributes no row.
    {
        "id": 703,
        "order_id": 401,
        "product_id": 12,
        "warehouse_id": 21,
        "product_uom_qty": 5,
        "qty_delivered": 0,
    },
    # 704: warehouse 22 has no code -> contributes no row.
    {
        "id": 704,
        "order_id": 401,
        "product_id": 11,
        "warehouse_id": 22,
        "product_uom_qty": 5,
        "qty_delivered": 0,
    },
    # 705: parent order 403 in state "done" -> open_units 0.
    {
        "id": 705,
        "order_id": 403,
        "product_id": 13,
        "warehouse_id": 21,
        "product_uom_qty": 7,
        "qty_delivered": 2,
    },
    # 706: parent order 404 in state "cancel" -> open_units 0; the
    # order has neither commitment_date nor expected_date, so ship_on
    # is null.
    {
        "id": 706,
        "order_id": 404,
        "product_id": 13,
        "warehouse_id": 21,
        "product_uom_qty": 4,
        "qty_delivered": 1,
    },
]

PRODUCTS = [
    {"id": 11, "default_code": "WIDGET-1"},
    # No default_code -> its lines contribute no row.
    {"id": 12, "default_code": False},
    {"id": 13, "default_code": "GADGET-3"},
]

WAREHOUSES = [
    {"id": 21, "code": "WH-1"},
    # No code -> its lines contribute no row.
    {"id": 22, "code": False},
]

ORDERS = [
    {
        "id": 401,
        "state": "sale",
        "commitment_date": "2026-11-05 08:30:00",
        "expected_date": "2026-11-01",
    },
    {
        "id": 402,
        "state": "sale",
        "commitment_date": False,
        "expected_date": "2026-11-18",
    },
    {
        "id": 403,
        "state": "done",
        "commitment_date": "2026-10-30",
        "expected_date": "2026-10-25",
    },
    {
        "id": 404,
        "state": "cancel",
        "commitment_date": False,
        "expected_date": False,
    },
]

EXPECTED = [
    {
        "business_key": f"odoo:{DB}:sale.order.line:701",
        "item_code": "WIDGET-1",
        "site_code": "WH-1",
        "order_type": "SO",
        "open_units": 3.0,
        "ship_on": "2026-11-05",
        "as_of": AS_OF,
        "source_system": "odoo",
    },
    {
        "business_key": f"odoo:{DB}:sale.order.line:702",
        "item_code": "WIDGET-1",
        "site_code": "WH-1",
        "order_type": "SO",
        "open_units": 0.0,
        "ship_on": "2026-11-18",
        "as_of": AS_OF,
        "source_system": "odoo",
    },
    {
        "business_key": f"odoo:{DB}:sale.order.line:705",
        "item_code": "GADGET-3",
        "site_code": "WH-1",
        "order_type": "SO",
        "open_units": 0.0,
        "ship_on": "2026-10-30",
        "as_of": AS_OF,
        "source_system": "odoo",
    },
    {
        "business_key": f"odoo:{DB}:sale.order.line:706",
        "item_code": "GADGET-3",
        "site_code": "WH-1",
        "order_type": "SO",
        "open_units": 0.0,
        "ship_on": None,
        "as_of": AS_OF,
        "source_system": "odoo",
    },
]


def _records_by_model(lines):
    return {
        "sale.order.line": lines,
        "product.product": PRODUCTS,
        "stock.warehouse": WAREHOUSES,
        "sale.order": ORDERS,
    }


class _OdooServer:
    """MockTransport handler answering authenticate and search_read only.

    Records every request per model; an unknown model, an extra call,
    a write (any method other than search_read), or a wrong
    authenticate phase fails the test loudly.
    """

    def __init__(self, uid, records_by_model):
        self.uid = uid
        self.records = records_by_model
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
            assert args[4] == "search_read", f"write or unknown method: {args[4]}"
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


TABLES = (
    "item_sites",
    "on_hand",
    "demand_events",
    "lead_time_observations",
    "open_purchase_lines",
    "open_sales_lines",
)

SALES_ROWS = [
    {
        "business_key": f"odoo:{DB}:sale.order.line:901",
        "item_code": "WIDGET-1",
        "site_code": "WH-1",
        "order_type": "SO",
        "open_units": 3.0,
        "ship_on": "2026-11-20",
        "as_of": AS_OF,
        "source_system": "odoo",
    }
]

CANNED = {
    "item_sites": [],
    "on_hand": [],
    "demand_events": [],
    "lead_time_observations": [],
    "open_purchase_lines": [],
    "open_sales_lines": SALES_ROWS,
}

_READ_TABLE = {
    "read_item_sites": "item_sites",
    "read_on_hand": "on_hand",
    "read_demand_events": "demand_events",
    "read_lead_time_observations": "lead_time_observations",
    "read_open_purchase_lines": "open_purchase_lines",
    "read_open_sales_lines": "open_sales_lines",
}


class _StubRepository:
    """Stands in for a Postgres repository: records the rows it was handed."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def upsert_many(self, rows):
        self.calls.append(rows)
        return {"inserted": len(rows), "updated": 0, "table": self.table}


class _StubClient:
    """Stands in for OdooClient: canned rows, every read recorded.

    ``read_on_hand``, ``read_open_purchase_lines`` and
    ``read_open_sales_lines`` record the ``as_of`` the runner passed
    them. A reader named in ``fail_on`` raises ``OdooError`` instead
    of returning rows.
    """

    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.read_order = []
        self.as_of_passed = {}

    def _read(self, name, as_of=None, records_as_of=False):
        self.read_order.append(name)
        if self.fail_on is not None and name == self.fail_on:
            raise OdooError(f"stub {name} failed")
        if records_as_of:
            self.as_of_passed[name] = as_of
        return CANNED[_READ_TABLE[name]]

    def read_item_sites(self):
        return self._read("read_item_sites")

    def read_on_hand(self, as_of=None):
        return self._read("read_on_hand", as_of=as_of, records_as_of=True)

    def read_demand_events(self):
        return self._read("read_demand_events")

    def read_lead_time_observations(self):
        return self._read("read_lead_time_observations")

    def read_open_purchase_lines(self, as_of=None):
        return self._read("read_open_purchase_lines", as_of=as_of, records_as_of=True)

    def read_open_sales_lines(self, as_of=None):
        return self._read("read_open_sales_lines", as_of=as_of, records_as_of=True)


def test_issue757_freeform(monkeypatch):
    monkeypatch.setenv("ODOO_BASE_URL", BASE_URL)
    monkeypatch.setenv("ODOO_DB", DB)
    monkeypatch.setenv("ODOO_LOGIN", LOGIN)
    monkeypatch.setenv("ODOO_API_KEY", API_KEY)
    monkeypatch.delenv("ODOO_CA_BUNDLE", raising=False)
    monkeypatch.delenv("ODOO_ALLOW_INSECURE", raising=False)

    server = _OdooServer(9, _records_by_model(LINES))
    with OdooClient(client=server.client()) as client:
        rows = client.read_open_sales_lines(as_of=AS_OF)

    # 1. The line read: exactly one sale.order.line search_read with
    #    the stated domain and fields, and no write anywhere.
    line_info = server.models["sale.order.line"]
    assert line_info["calls"] == 1
    assert line_info["domains"] == [LINE_DOMAIN]
    assert line_info["fields"] == [LINE_FIELDS]
    assert line_info["kwargs"] == [{"fields": LINE_FIELDS, "offset": 0}]
    # The domain carries no commitment_date or expected_date recency
    # filter: the parent order state alone decides which lines are
    # read.
    line_domain = line_info["domains"][0]
    assert not any(
        isinstance(term, list)
        and term
        and term[0] in ("commitment_date", "expected_date")
        for term in line_domain
    )

    # 2. The rows: a line of 15 ordered and 12 delivered maps to
    #    open_units 3; a fully delivered line is kept with open_units
    #    0; a line whose parent order state is "done" and one whose
    #    parent order state is "cancel" each map to open_units 0.
    assert rows == EXPECTED
    by_key = {row["business_key"]: row for row in rows}
    assert by_key[f"odoo:{DB}:sale.order.line:701"]["open_units"] == 3.0
    assert by_key[f"odoo:{DB}:sale.order.line:702"]["open_units"] == 0.0
    assert by_key[f"odoo:{DB}:sale.order.line:705"]["open_units"] == 0.0
    assert by_key[f"odoo:{DB}:sale.order.line:706"]["open_units"] == 0.0

    # 3. ship_on: the date part of the order's commitment_date when
    #    set (701), else of its expected_date (702), else null (706).
    assert by_key[f"odoo:{DB}:sale.order.line:701"]["ship_on"] == "2026-11-05"
    assert by_key[f"odoo:{DB}:sale.order.line:702"]["ship_on"] == "2026-11-18"
    assert by_key[f"odoo:{DB}:sale.order.line:706"]["ship_on"] is None

    # 4. Left out: 703 (product 12 has no default_code) and 704
    #    (warehouse 22 has no code) contribute no row.
    assert set(by_key) == {
        f"odoo:{DB}:sale.order.line:701",
        f"odoo:{DB}:sale.order.line:702",
        f"odoo:{DB}:sale.order.line:705",
        f"odoo:{DB}:sale.order.line:706",
    }
    assert all(row["source_system"] == "odoo" for row in rows)
    assert all(row["as_of"] == AS_OF for row in rows)
    assert all(row["order_type"] == "SO" for row in rows)

    # 5. The lookups: one search_read each on product.product,
    #    stock.warehouse and sale.order, each over the ids actually
    #    seen (an id in [...] domain).
    product_info = server.models["product.product"]
    assert product_info["calls"] == 1
    assert product_info["domains"] == [[["id", "in", [11, 12, 13]]]]
    assert product_info["fields"] == [["default_code", "uom_id"]]
    assert product_info["kwargs"] == [
        {"fields": ["default_code", "uom_id"], "offset": 0}
    ]

    warehouse_info = server.models["stock.warehouse"]
    assert warehouse_info["calls"] == 1
    assert warehouse_info["domains"] == [[["id", "in", [21, 22]]]]
    assert warehouse_info["fields"] == [["code"]]
    assert warehouse_info["kwargs"] == [{"fields": ["code"], "offset": 0}]

    order_info = server.models["sale.order"]
    assert order_info["calls"] == 1
    assert order_info["domains"] == [[["id", "in", [401, 402, 403, 404]]]]
    assert order_info["fields"] == [["state", "commitment_date", "expected_date"]]
    assert order_info["kwargs"] == [
        {"fields": ["state", "commitment_date", "expected_date"], "offset": 0}
    ]

    # 6. No lines: the product, warehouse and order lookups are not
    #    called at all (no id is seen), and still no write.
    empty = _OdooServer(9, _records_by_model([]))
    with OdooClient(client=empty.client()) as client:
        assert client.read_open_sales_lines(as_of=AS_OF) == []
    assert empty.models["sale.order.line"]["calls"] == 1
    for model in ("product.product", "stock.warehouse", "sale.order"):
        assert empty.models[model]["calls"] == 0

    # 7. Omitted as_of: the reader re-derives the current UTC time
    #    instead of stamping a supplied value.
    default_server = _OdooServer(9, _records_by_model(LINES))
    with OdooClient(client=default_server.client()) as client:
        default_rows = client.read_open_sales_lines()
    assert default_rows
    assert all(row["as_of"] != AS_OF for row in default_rows)
    assert all(AS_OF_RE.fullmatch(row["as_of"]) for row in default_rows)

    # 8. The sync: with a stub client and stub repositories, the sixth
    #    repository receives the reader's exact list (the same list
    #    object, unreshaped), read after the other five, and the
    #    result carries "open_sales_lines" alongside the other five
    #    tables.
    stub = _StubClient()
    repos = {table: _StubRepository(table) for table in TABLES}
    repo_kwargs = {f"{table}_repo": repos[table] for table in TABLES}
    result = run_sync(client=stub, as_of=AS_OF, **repo_kwargs)
    assert stub.read_order == [
        "read_item_sites",
        "read_on_hand",
        "read_demand_events",
        "read_lead_time_observations",
        "read_open_purchase_lines",
        "read_open_sales_lines",
    ]
    assert stub.as_of_passed["read_open_sales_lines"] == AS_OF
    assert repos["open_sales_lines"].calls == [SALES_ROWS]
    assert repos["open_sales_lines"].calls[0] is SALES_ROWS
    assert result["open_sales_lines"] == {
        "inserted": len(SALES_ROWS),
        "updated": 0,
        "table": "open_sales_lines",
    }
    assert set(result) == set(TABLES)

    # 9. A failing sixth read still leaves every repository unwritten.
    broken = _StubClient(fail_on="read_open_sales_lines")
    broken_repos = {table: _StubRepository(table) for table in TABLES}
    with pytest.raises(OdooError, match="read_open_sales_lines"):
        run_sync(
            client=broken,
            as_of=AS_OF,
            **{f"{table}_repo": broken_repos[table] for table in TABLES},
        )
    for table in TABLES:
        assert broken_repos[table].calls == []
