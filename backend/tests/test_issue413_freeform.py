"""Issue #413 proof: the Odoo read-then-write sync runner.

``run_sync`` in ``app.sync.odoo`` is proven with a stub Odoo client and
stub repositories (no network, no database):

  * a successful run: each of the six stub readers returns a distinct
    canned list, each stub repository records the exact list it was
    handed (the same list object, unreshaped), and ``run_sync`` returns
    the six repositories' return values under the six table names;
  * read-then-write ordering: a stub client whose
    ``read_open_purchase_lines`` raises ``OdooError`` makes ``run_sync``
    propagate it and leaves every stub repository's ``upsert_many``
    uncalled (recorded call count zero);
  * one shared snapshot: the ``as_of`` the runner passes to
    ``read_on_hand``, ``read_open_purchase_lines`` and
    ``read_open_sales_lines`` is identical within a run, is a non-empty
    ISO-8601 string, and equals the normalized ``as_of`` — a datetime
    ``as_of`` reaches the readers as its UTC ``YYYY-MM-DDTHH:MM:SSZ``
    rendering, and an omitted ``as_of`` becomes one start-time default
    shared by all three readers;
  * the real ``OdooClient`` readers stamp the supplied ``as_of`` on their
    rows instead of re-deriving ``now()`` (the JSON-RPC transport is
    fully mocked, so still no network);
  * client ownership: a passed-in client is never closed by the runner,
    while a default client is built through the runner's
    ``OdooClient`` and closed before returning — on both the success and
    the failure path;
  * a non-ISO (or otherwise unvalidated) string ``as_of`` is rejected
    with ``ValueError`` before any read, so it never reaches a reader or
    a repository;
  * a failing ``close()`` on a runner-built client never masks the
    in-flight read error: the ``OdooError`` still propagates unchanged
    (with no chained close error), while a close failure with no
    in-flight error still surfaces on the success path.
"""

import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

import app.sync.odoo as odoo_sync
from app.feed.odoo_client import OdooClient, OdooError
from app.sync.odoo import run_sync

AS_OF = "2026-10-15T08:00:00Z"
DATETIME_AS_OF = datetime(2026, 10, 15, 8, 0, 0, tzinfo=UTC)
AS_OF_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

TABLES = (
    "item_sites",
    "on_hand",
    "demand_events",
    "lead_time_observations",
    "open_purchase_lines",
    "open_sales_lines",
)

ITEM_SITE_ROWS = [
    {
        "item_code": "WIDGET-1",
        "site_code": "WH-1",
        "unit_cost": 4.25,
        "master_lead_time_days": 7.0,
        "source_system": "odoo",
    }
]

ON_HAND_ROWS = [
    {
        "item_code": "WIDGET-1",
        "site_code": "WH-1",
        "quantity_on_hand": 12.0,
        "as_of": AS_OF,
        "source_system": "odoo",
    }
]

DEMAND_EVENT_ROWS = [
    {
        "business_key": "odoo:acme_prod:stock.move:21",
        "item_code": "WIDGET-1",
        "site_code": "WH-1",
        "quantity": 3.0,
        "occurred_on": "2026-10-01",
        "event_type": "shipment",
        "source_system": "odoo",
    }
]

LEAD_TIME_OBSERVATION_ROWS = [
    {
        "business_key": "odoo:acme_prod:stock.move:31",
        "item_code": "WIDGET-1",
        "site_code": "WH-1",
        "supplier_code": "VENDOR-9",
        "ordered_on": "2026-09-01",
        "received_on": "2026-09-12",
        "source_system": "odoo",
    }
]

OPEN_PURCHASE_LINE_ROWS = [
    {
        "business_key": "odoo:acme_prod:purchase.order.line:41",
        "item_code": "WIDGET-1",
        "site_code": "WH-1",
        "supplier_code": "VENDOR-9",
        "order_type": "purchase",
        "open_units": 6.0,
        "promised_on": "2026-11-02",
        "ordered_on": "2026-10-05",
        "as_of": AS_OF,
        "source_system": "odoo",
    }
]

OPEN_SALES_LINE_ROWS = [
    {
        "business_key": "odoo:acme_prod:sale.order.line:51",
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
    "item_sites": ITEM_SITE_ROWS,
    "on_hand": ON_HAND_ROWS,
    "demand_events": DEMAND_EVENT_ROWS,
    "lead_time_observations": LEAD_TIME_OBSERVATION_ROWS,
    "open_purchase_lines": OPEN_PURCHASE_LINE_ROWS,
    "open_sales_lines": OPEN_SALES_LINE_ROWS,
}


class _StubRepository:
    """Stands in for a Postgres repository: records the rows it was handed."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def upsert_many(self, rows):
        self.calls.append(rows)
        return {"inserted": len(rows), "updated": 0, "table": self.table}


def _repo_kwargs(repos):
    return {f"{table}_repo": repos[table] for table in TABLES}


def _fresh_repos():
    return {table: _StubRepository(table) for table in TABLES}


_READ_TABLE = {
    "read_item_sites": "item_sites",
    "read_on_hand": "on_hand",
    "read_demand_events": "demand_events",
    "read_lead_time_observations": "lead_time_observations",
    "read_open_purchase_lines": "open_purchase_lines",
    "read_open_sales_lines": "open_sales_lines",
}


class _StubClient:
    """Stands in for OdooClient: canned rows, every read recorded.

    The six readers return their table's canned list (the same list
    object on every call, so identity proves the runner never copied or
    reshaped a row list). ``read_on_hand``, ``read_open_purchase_lines``
    and ``read_open_sales_lines`` record the ``as_of`` the runner passed
    them. A reader named in ``fail_on`` raises ``OdooError`` instead of
    returning rows.
    """

    def __init__(self, fail_on=None, close_error=None):
        self.fail_on = fail_on
        self.close_error = close_error
        self.read_order = []
        self.as_of_passed = {}
        self.close_count = 0

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

    def close(self):
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


BASE_URL = "https://odoo.acme.example"
DB = "acme_prod"
LOGIN = "aierp@acme.example"
API_KEY = "s3cret-odoo-api-key"

ON_HAND_RECORDS = {
    "stock.quant": [
        {"id": 71, "product_id": 11, "location_id": 101, "quantity": 12},
    ],
    "product.product": [{"id": 11, "default_code": "WIDGET-1"}],
    "stock.location": [{"id": 101, "warehouse_id": 201}],
    "stock.warehouse": [{"id": 201, "code": "WH-1"}],
}

OPEN_PURCHASE_LINE_RECORDS = {
    "purchase.order.line": [
        {
            "id": 601,
            "product_id": 11,
            "product_qty": 10,
            "qty_received": 4,
            "date_planned": "2026-11-02 09:30:00",
            "order_id": 401,
        },
    ],
    "purchase.order": [
        {
            "id": 401,
            "date_order": "2026-10-05",
            "partner_id": 501,
            "picking_type_id": 301,
        },
    ],
    "stock.picking.type": [{"id": 301, "warehouse_id": 201}],
    "stock.warehouse": [{"id": 201, "code": "WH-1"}],
    "res.partner": [{"id": 501, "ref": "VENDOR-9", "name": "Vendor Nine"}],
    "product.product": [{"id": 11, "default_code": "WIDGET-1"}],
}


def _odoo_server(records_by_model):
    """MockTransport handler answering authenticate and search_read only."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        service = body["params"]["service"]
        method = body["params"]["method"]
        args = body["params"]["args"]
        if service == "common" and method == "authenticate":
            assert args == [DB, LOGIN, API_KEY, {}]
            result = 9
        else:
            assert (
                service == "object" and method == "execute_kw"
            ), f"unexpected call {service}.{method}"
            model = args[3]
            assert args[4] == "search_read", f"unexpected method {args[4]}"
            assert model in records_by_model, f"unexpected model {model}"
            result = records_by_model[model]
        return httpx.Response(
            200,
            content=json.dumps(
                {"jsonrpc": "2.0", "id": body["id"], "result": result}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    return handler


def _mock_client(records_by_model):
    return OdooClient(
        client=httpx.Client(
            transport=httpx.MockTransport(_odoo_server(records_by_model))
        )
    )


def _install_default_client(monkeypatch, fail_on=None, close_error=None):
    """Point the runner's OdooClient at a counting stub; track instances."""
    instances = []

    class _DefaultStubClient(_StubClient):
        def __init__(self):
            super().__init__(fail_on=fail_on, close_error=close_error)
            instances.append(self)

    monkeypatch.setattr(odoo_sync, "OdooClient", _DefaultStubClient)
    return instances


def test_issue413_freeform(monkeypatch):
    monkeypatch.setenv("ODOO_BASE_URL", BASE_URL)
    monkeypatch.setenv("ODOO_DB", DB)
    monkeypatch.setenv("ODOO_LOGIN", LOGIN)
    monkeypatch.setenv("ODOO_API_KEY", API_KEY)
    monkeypatch.delenv("ODOO_CA_BUNDLE", raising=False)
    monkeypatch.delenv("ODOO_ALLOW_INSECURE", raising=False)

    # 1. A successful run: the six stub readers each return a distinct
    #    canned list, every stub repository records the exact list it was
    #    handed (the same list object), and run_sync returns the six
    #    repositories' return values under the six table names. A
    #    datetime as_of is normalized once to its UTC ISO-8601 rendering
    #    and the same value reaches all three as_of-taking readers.
    client = _StubClient()
    repos = _fresh_repos()
    result = run_sync(client=client, as_of=DATETIME_AS_OF, **_repo_kwargs(repos))

    assert len({id(rows) for rows in CANNED.values()}) == 6
    assert client.read_order == [
        "read_item_sites",
        "read_on_hand",
        "read_demand_events",
        "read_lead_time_observations",
        "read_open_purchase_lines",
        "read_open_sales_lines",
    ]
    for table in TABLES:
        assert repos[table].calls == [CANNED[table]]
        assert repos[table].calls[0] is CANNED[table]
    assert result == {
        table: {"inserted": len(CANNED[table]), "updated": 0, "table": table}
        for table in TABLES
    }
    assert len({id(value) for value in result.values()}) == 6
    # The runner does not close a client it did not build.
    assert client.close_count == 0

    # 2. One shared snapshot: the as_of passed to read_on_hand,
    #    read_open_purchase_lines and read_open_sales_lines is identical
    #    within the run, is a non-empty ISO-8601 string, and equals the
    #    normalized as_of (a datetime as_of rendered as its UTC
    #    YYYY-MM-DDTHH:MM:SSZ).
    snapshot_on_hand = client.as_of_passed["read_on_hand"]
    snapshot_open_po = client.as_of_passed["read_open_purchase_lines"]
    snapshot_open_so = client.as_of_passed["read_open_sales_lines"]
    assert snapshot_on_hand == snapshot_open_po == snapshot_open_so
    assert isinstance(snapshot_on_hand, str) and snapshot_on_hand
    assert AS_OF_RE.fullmatch(snapshot_on_hand)
    assert (
        snapshot_on_hand
        == AS_OF
        == DATETIME_AS_OF.astimezone(UTC).isoformat().replace("+00:00", "Z")
    )

    # 3. A failed extract never half-writes: a stub client whose
    #    read_open_purchase_lines raises OdooError makes run_sync
    #    propagate it, and every stub repository's upsert_many stays
    #    uncalled (recorded call count zero).
    broken = _StubClient(fail_on="read_open_purchase_lines")
    broken_repos = _fresh_repos()
    with pytest.raises(OdooError, match="read_open_purchase_lines"):
        run_sync(client=broken, as_of=AS_OF, **_repo_kwargs(broken_repos))
    assert broken.read_order == [
        "read_item_sites",
        "read_on_hand",
        "read_demand_events",
        "read_lead_time_observations",
        "read_open_purchase_lines",
    ]
    for table in TABLES:
        assert broken_repos[table].calls == []

    # 4. Omitted as_of: the runner derives one start-time snapshot, and
    #    the same non-empty ISO-8601 string (not two re-derivations)
    #    reaches all three as_of-taking readers within the run's own
    #    window.
    default_repos = _fresh_repos()
    started = datetime.now(UTC)
    default_client = _StubClient()
    result = run_sync(client=default_client, **_repo_kwargs(default_repos))
    finished = datetime.now(UTC)
    assert result == {
        table: {"inserted": len(CANNED[table]), "updated": 0, "table": table}
        for table in TABLES
    }
    first = default_client.as_of_passed["read_on_hand"]
    second = default_client.as_of_passed["read_open_purchase_lines"]
    third = default_client.as_of_passed["read_open_sales_lines"]
    assert first == second == third
    assert isinstance(first, str) and first
    assert AS_OF_RE.fullmatch(first)
    snapped = datetime.strptime(first, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert started - timedelta(seconds=1) <= snapped <= finished

    # 5. The real readers stamp the supplied as_of on their rows instead
    #    of re-deriving now(): with the JSON-RPC transport mocked, a
    #    supplied as_of arrives on every emitted row untouched.
    with _mock_client(ON_HAND_RECORDS) as odoo:
        rows = odoo.read_on_hand(as_of=AS_OF)
    assert rows == [
        {
            "item_code": "WIDGET-1",
            "site_code": "WH-1",
            "quantity_on_hand": 12.0,
            "as_of": AS_OF,
            "source_system": "odoo",
        }
    ]

    with _mock_client(OPEN_PURCHASE_LINE_RECORDS) as odoo:
        rows = odoo.read_open_purchase_lines(as_of=AS_OF)
    assert rows == [
        {
            "business_key": f"odoo:{DB}:purchase.order.line:601",
            "item_code": "WIDGET-1",
            "site_code": "WH-1",
            "supplier_code": "VENDOR-9",
            "order_type": "purchase",
            "open_units": 6.0,
            "promised_on": "2026-11-02",
            "ordered_on": "2026-10-05",
            "as_of": AS_OF,
            "source_system": "odoo",
        }
    ]

    # The counterfactual that a one-snapshot run must never trigger:
    # with no as_of supplied the reader re-derives now(), which differs
    # from the supplied value.
    with _mock_client(ON_HAND_RECORDS) as odoo:
        rows = odoo.read_on_hand()
    assert rows and rows[0]["as_of"] != AS_OF
    assert AS_OF_RE.fullmatch(rows[0]["as_of"])

    # 6. Client ownership: with no client passed, the runner builds its
    #    default OdooClient and closes it before returning — on the
    #    success path and the failure path alike.
    instances = _install_default_client(monkeypatch)
    default_repos = _fresh_repos()
    result = run_sync(as_of=AS_OF, **_repo_kwargs(default_repos))
    assert len(instances) == 1
    assert instances[0].close_count == 1
    assert result == {
        table: {"inserted": len(CANNED[table]), "updated": 0, "table": table}
        for table in TABLES
    }

    instances = _install_default_client(monkeypatch, fail_on="read_open_purchase_lines")
    failing_repos = _fresh_repos()
    with pytest.raises(OdooError):
        run_sync(as_of=AS_OF, **_repo_kwargs(failing_repos))
    assert len(instances) == 1
    assert instances[0].close_count == 1
    for table in TABLES:
        assert failing_repos[table].calls == []

    # 7. A non-ISO or otherwise unvalidated string as_of is rejected
    #    before any read: ValueError, zero readers called, no snapshot
    #    value handed to any reader, and no repository written.
    for bad_as_of in ("banana", "2026-13-45T99:99:99Z", "\x00"):
        bad_client = _StubClient()
        bad_repos = _fresh_repos()
        with pytest.raises(ValueError, match="ISO-8601"):
            run_sync(client=bad_client, as_of=bad_as_of, **_repo_kwargs(bad_repos))
        assert bad_client.read_order == []
        assert bad_client.as_of_passed == {}
        for table in TABLES:
            assert bad_repos[table].calls == []

    # The validator must still accept a well-formed ISO-8601 string
    # as_of, and that exact value reaches all three as_of-taking readers.
    string_client = _StubClient()
    string_repos = _fresh_repos()
    result = run_sync(client=string_client, as_of=AS_OF, **_repo_kwargs(string_repos))
    assert result == {
        table: {"inserted": len(CANNED[table]), "updated": 0, "table": table}
        for table in TABLES
    }
    assert string_client.as_of_passed["read_on_hand"] == AS_OF
    assert string_client.as_of_passed["read_open_purchase_lines"] == AS_OF
    assert string_client.as_of_passed["read_open_sales_lines"] == AS_OF

    # 8. A failing close() on a runner-built client never masks the
    #    in-flight read error: the OdooError still propagates unchanged
    #    (no chained close error), the client was still closed, and no
    #    repository was written.
    instances = _install_default_client(
        monkeypatch,
        fail_on="read_open_purchase_lines",
        close_error=RuntimeError("close blew up"),
    )
    mask_repos = _fresh_repos()
    with pytest.raises(OdooError, match="read_open_purchase_lines") as excinfo:
        run_sync(as_of=AS_OF, **_repo_kwargs(mask_repos))
    assert excinfo.value.__context__ is None
    assert len(instances) == 1
    assert instances[0].close_count == 1
    for table in TABLES:
        assert mask_repos[table].calls == []

    # A close failure with no in-flight error still surfaces on the
    # success path, after every write has already happened.
    instances = _install_default_client(
        monkeypatch, close_error=RuntimeError("close blew up")
    )
    close_repos = _fresh_repos()
    with pytest.raises(RuntimeError, match="close blew up"):
        run_sync(as_of=AS_OF, **_repo_kwargs(close_repos))
    assert instances[0].close_count == 1
    for table in TABLES:
        assert len(close_repos[table].calls) == 1
