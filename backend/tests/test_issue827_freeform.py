"""Issue #827 proof: OdooClient.read_supplier_terms and its sync slot.

With the Odoo server fully mocked (httpx.MockTransport answers every
/jsonrpc POST, so no network), the transport is stubbed the way
test_issue757_freeform.py does it: it answers ``authenticate`` and
``search_read`` only, so any write would fail loudly. It checks:

  * the read: exactly one ``product.supplierinfo`` search_read with
    the stated fields, plus the reads it needs — one
    ``product.product`` read over the ``product_id``s actually seen
    (``default_code``), one over the ``product_tmpl_id``s actually
    seen (``product_tmpl_id``, ``default_code``), and one
    ``res.partner`` read over the partners actually seen
    (``ref``, ``name``);
  * the rows: a line with ``product_id`` set, ``min_qty`` 50,
    ``delay`` 7 and ``sequence`` 1 maps to ``min_order_qty`` 50,
    ``lead_time_days`` 7 and ``is_default`` true, with null
    ``max_order_qty`` and ``lot_size`` (Odoo's supplierinfo carries
    neither); a second line of the same product with ``min_qty`` 0
    and ``sequence`` 5 maps to a null minimum and ``is_default``
    false; a line carrying only ``product_tmpl_id`` on a template
    with exactly one coded variant resolves to that variant's code;
    a line on a template with two coded variants is skipped;
  * ``supplier_code``: the partner's ``ref`` when it is a non-empty
    string, else its ``name`` — the same code
    ``read_open_purchase_lines`` emits for a vendor;
  * the sync: ``run_sync`` with a stub client and stub repositories
    reads the six models first and ``read_supplier_terms`` last,
    stores the rows through the ``supplier_terms`` repository and the
    result carries ``supplier_terms``; a client whose
    ``read_supplier_terms`` raises ``OdooError`` still syncs the six
    read models and its result has no ``supplier_terms`` key; a
    pre-#827 client with no such method behaves the same;
  * read-only: no create or write call is made.
"""

import json

import httpx

from app.feed.odoo_client import OdooClient, OdooError
from app.sync.odoo import run_sync

BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"
AS_OF = "2026-10-31T08:00:00Z"

SUPPLIERINFO_FIELDS = [
    "id",
    "product_id",
    "product_tmpl_id",
    "partner_id",
    "min_qty",
    "delay",
    "sequence",
]

SUPPLIER_LINES = [
    # (a) 801: product 11 (WIDGET-1), min_qty 50, delay 7, sequence 1
    #     -> the product's default line, supplier 51 (ref VEN-1).
    {
        "id": 801,
        "product_id": 11,
        "product_tmpl_id": 21,
        "partner_id": 51,
        "min_qty": 50,
        "delay": 7,
        "sequence": 1,
    },
    # (b) 802: the same product, min_qty 0 (-> null), no delay,
    #     sequence 5 -> not the default line; supplier 52 has no ref,
    #     so its name is the supplier code.
    {
        "id": 802,
        "product_id": 11,
        "product_tmpl_id": 21,
        "partner_id": 52,
        "min_qty": 0,
        "delay": False,
        "sequence": 5,
    },
    # (c) 803: no product_id, only template 31, whose single variant
    #     (22) is coded BRACKET-2 -> resolves to that variant's code.
    {
        "id": 803,
        "product_id": False,
        "product_tmpl_id": 31,
        "partner_id": 52,
        "min_qty": 10,
        "delay": 3,
        "sequence": 10,
    },
    # (d) 804: no product_id, only template 32, which has TWO coded
    #     variants (23 and 24) -> unresolvable, skipped.
    {
        "id": 804,
        "product_id": False,
        "product_tmpl_id": 32,
        "partner_id": 51,
        "min_qty": 5,
        "delay": 2,
        "sequence": 1,
    },
]

PRODUCTS = [
    {"id": 11, "product_tmpl_id": 21, "default_code": "WIDGET-1"},
    # Template 31's only variant, coded.
    {"id": 22, "product_tmpl_id": 31, "default_code": "BRACKET-2"},
    # Template 32's two variants, both coded -> its lines are skipped.
    {"id": 23, "product_tmpl_id": 32, "default_code": "BOLT-4"},
    {"id": 24, "product_tmpl_id": 32, "default_code": "BOLT-5"},
]

PARTNERS = [
    {"id": 51, "ref": "VEN-1", "name": "Vendor One"},
    # No ref -> the name is the supplier code.
    {"id": 52, "ref": False, "name": "Acme Fasteners"},
]

EXPECTED = [
    {
        "business_key": f"odoo:{DB}:product.supplierinfo:801",
        "source_system": "odoo",
        "item_code": "WIDGET-1",
        "supplier_code": "VEN-1",
        "min_order_qty": 50.0,
        "max_order_qty": None,
        "lot_size": None,
        "lead_time_days": 7.0,
        "is_default": True,
    },
    {
        "business_key": f"odoo:{DB}:product.supplierinfo:802",
        "source_system": "odoo",
        "item_code": "WIDGET-1",
        "supplier_code": "Acme Fasteners",
        "min_order_qty": None,
        "max_order_qty": None,
        "lot_size": None,
        "lead_time_days": None,
        "is_default": False,
    },
    {
        "business_key": f"odoo:{DB}:product.supplierinfo:803",
        "source_system": "odoo",
        "item_code": "BRACKET-2",
        "supplier_code": "Acme Fasteners",
        "min_order_qty": 10.0,
        "max_order_qty": None,
        "lot_size": None,
        "lead_time_days": 3.0,
        "is_default": True,
    },
]


class _OdooServer:
    """MockTransport handler answering authenticate and search_read only.

    Records every request per model; an unknown model, a write (any
    ``execute_kw`` method other than ``search_read``), or a wrong
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
    "supplier_terms",
)

CANNED = {
    "item_sites": [],
    "on_hand": [],
    "demand_events": [],
    "lead_time_observations": [],
    "open_purchase_lines": [],
    "open_sales_lines": [],
    "supplier_terms": EXPECTED,
}

_READ_TABLE = {
    "read_item_sites": "item_sites",
    "read_on_hand": "on_hand",
    "read_demand_events": "demand_events",
    "read_lead_time_observations": "lead_time_observations",
    "read_open_purchase_lines": "open_purchase_lines",
    "read_open_sales_lines": "open_sales_lines",
    "read_supplier_terms": "supplier_terms",
}


class _StubRepository:
    """Stands in for a Postgres repository: records the rows it was handed."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def upsert_many(self, rows):
        self.calls.append(rows)
        return {"inserted": len(rows), "updated": 0, "table": self.table}


class _SixReaderClient:
    """Stands in for a pre-#827 OdooClient: the six read models only.

    The six readers return their table's canned list (the same list
    object on every call, so identity proves the runner never copied
    or reshaped a row list). ``read_on_hand``,
    ``read_open_purchase_lines`` and ``read_open_sales_lines`` record
    the ``as_of`` the runner passed them.
    """

    def __init__(self):
        self.read_order = []
        self.as_of_passed = {}

    def _read(self, name, as_of=None, records_as_of=False):
        self.read_order.append(name)
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


class _StubClient(_SixReaderClient):
    """The six reads plus read_supplier_terms.

    A reader named in ``fail_on`` raises ``OdooError`` instead of
    returning rows.
    """

    def __init__(self, fail_on=None):
        super().__init__()
        self.fail_on = fail_on

    def _read(self, name, as_of=None, records_as_of=False):
        if self.fail_on is not None and name == self.fail_on:
            raise OdooError(f"stub {name} failed")
        return super()._read(name, as_of=as_of, records_as_of=records_as_of)

    def read_supplier_terms(self):
        return self._read("read_supplier_terms")


def test_issue827_freeform(monkeypatch):
    monkeypatch.setenv("ODOO_BASE_URL", BASE_URL)
    monkeypatch.setenv("ODOO_DB", DB)
    monkeypatch.setenv("ODOO_LOGIN", LOGIN)
    monkeypatch.setenv("ODOO_API_KEY", API_KEY)
    monkeypatch.delenv("ODOO_CA_BUNDLE", raising=False)
    monkeypatch.delenv("ODOO_ALLOW_INSECURE", raising=False)

    server = _OdooServer(
        9,
        {
            "product.supplierinfo": SUPPLIER_LINES,
            "product.product": PRODUCTS,
            "res.partner": PARTNERS,
        },
    )
    with OdooClient(client=server.client()) as client:
        rows = client.read_supplier_terms()

    # 1. The read: exactly one product.supplierinfo search_read with
    #    the stated fields.
    supplier_info = server.models["product.supplierinfo"]
    assert supplier_info["calls"] == 1
    assert supplier_info["domains"] == [[]]
    assert supplier_info["fields"] == [SUPPLIERINFO_FIELDS]
    assert supplier_info["kwargs"] == [{"fields": SUPPLIERINFO_FIELDS, "offset": 0}]

    # 2. The rows.
    assert rows == EXPECTED
    by_key = {row["business_key"]: row for row in rows}
    # (a) product_id set, min_qty 50, delay 7, sequence 1: the
    #     product's default line, with null max and lot.
    line_a = by_key[f"odoo:{DB}:product.supplierinfo:801"]
    assert line_a["item_code"] == "WIDGET-1"
    assert line_a["supplier_code"] == "VEN-1"
    assert line_a["min_order_qty"] == 50.0
    assert line_a["lead_time_days"] == 7.0
    assert line_a["is_default"] is True
    assert line_a["max_order_qty"] is None
    assert line_a["lot_size"] is None
    # (b) the same product, min_qty 0, sequence 5: a null minimum and
    #     not the default line.
    line_b = by_key[f"odoo:{DB}:product.supplierinfo:802"]
    assert line_b["item_code"] == "WIDGET-1"
    assert line_b["min_order_qty"] is None
    assert line_b["lead_time_days"] is None
    assert line_b["is_default"] is False
    # (c) only product_tmpl_id, on a template with exactly one coded
    #     variant: resolves to that variant's code.
    line_c = by_key[f"odoo:{DB}:product.supplierinfo:803"]
    assert line_c["item_code"] == "BRACKET-2"
    assert line_c["is_default"] is True
    # (d) only product_tmpl_id, on a template with two coded variants:
    #     unresolvable, skipped.
    assert f"odoo:{DB}:product.supplierinfo:804" not in by_key
    assert all(row["source_system"] == "odoo" for row in rows)

    # 3. The reads it needs: one product.product read over the
    #    product_ids actually seen, one over the template ids actually
    #    seen, and one res.partner read over the partners seen.
    product_info = server.models["product.product"]
    assert product_info["calls"] == 2
    assert product_info["domains"] == [
        [["id", "in", [11]]],
        [["product_tmpl_id", "in", [31, 32]]],
    ]
    assert product_info["fields"] == [
        ["default_code"],
        ["product_tmpl_id", "default_code"],
    ]
    assert product_info["kwargs"] == [
        {"fields": ["default_code"], "offset": 0},
        {"fields": ["product_tmpl_id", "default_code"], "offset": 0},
    ]
    partner_info = server.models["res.partner"]
    assert partner_info["calls"] == 1
    assert partner_info["domains"] == [[["id", "in", [51, 52]]]]
    assert partner_info["fields"] == [["ref", "name"]]
    assert partner_info["kwargs"] == [{"fields": ["ref", "name"], "offset": 0}]

    # 4. Read-only: one authenticate plus four search_reads and no
    #    create or write call anywhere.
    assert len(server.requests) == 5
    for body in server.bodies:
        params = body["params"]
        if params["service"] == "common":
            assert params["method"] == "authenticate"
        else:
            assert params["service"] == "object"
            assert params["method"] == "execute_kw"
            assert params["args"][4] == "search_read"

    # 5. The sync: the six models first, read_supplier_terms last; the
    #    supplier_terms repository receives the reader's exact list and
    #    the result carries "supplier_terms".
    stub = _StubClient()
    repos = {table: _StubRepository(table) for table in TABLES}
    result = run_sync(
        client=stub,
        as_of=AS_OF,
        **{f"{table}_repo": repos[table] for table in TABLES},
    )
    assert stub.read_order == [
        "read_item_sites",
        "read_on_hand",
        "read_demand_events",
        "read_lead_time_observations",
        "read_open_purchase_lines",
        "read_open_sales_lines",
        "read_supplier_terms",
    ]
    assert stub.as_of_passed == {
        "read_on_hand": AS_OF,
        "read_open_purchase_lines": AS_OF,
        "read_open_sales_lines": AS_OF,
    }
    for table in TABLES:
        assert repos[table].calls == [CANNED[table]]
    assert repos["supplier_terms"].calls[0] is EXPECTED
    assert result["supplier_terms"] == {
        "inserted": len(EXPECTED),
        "updated": 0,
        "table": "supplier_terms",
    }
    assert set(result) == set(TABLES)

    # 6. A client whose read_supplier_terms raises OdooError: the six
    #    read models still sync and the result has no supplier_terms
    #    key.
    refused = _StubClient(fail_on="read_supplier_terms")
    refused_repos = {table: _StubRepository(table) for table in TABLES}
    refused_result = run_sync(
        client=refused,
        **{f"{table}_repo": refused_repos[table] for table in TABLES},
    )
    for table in TABLES:
        if table != "supplier_terms":
            assert refused_repos[table].calls == [CANNED[table]]
    assert refused_repos["supplier_terms"].calls == []
    assert set(refused_result) == set(TABLES) - {"supplier_terms"}

    # 7. A pre-#827 client with no read_supplier_terms at all: the six
    #    read models still sync and the result has no supplier_terms
    #    key.
    legacy = _SixReaderClient()
    legacy_repos = {table: _StubRepository(table) for table in TABLES}
    legacy_result = run_sync(
        client=legacy,
        **{f"{table}_repo": legacy_repos[table] for table in TABLES},
    )
    assert legacy_repos["supplier_terms"].calls == []
    assert set(legacy_result) == set(TABLES) - {"supplier_terms"}
