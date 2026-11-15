"""Issue #1019 proof: the ERP source currencies and their warning.

AC-1 — the ``erp_source_currencies`` table (migration
``0040_erp_source_currencies``): one row per
(``source_system``, ``currency_code``) pair, the pair of text columns
the primary key, ``seen_at`` a not-null ``TIMESTAMPTZ`` with a
``now()`` server default.  ``OdooClient.read_company_currencies()``
answers the distinct currency codes of ``res.company``, read through
one ``search_read`` with the ``currency_id`` field, normalized to
uppercase ISO-4217, deduplicated and sorted; a company with no
currency is ignored.  In the Odoo runner, ``run_sync`` reads it as an
optional ninth, the way ``read_supplier_terms``: a client without the
method and a client whose read raises ``OdooError`` skip it — nothing
stored changes, no ``currencies`` result key — while the six read
models still store; a client that answers codes stores exactly those
codes for ``odoo`` in one transaction through
``ErpSourceCurrencyPostgresRepository.replace_for`` and the result
gains ``currencies``, the list of codes.  The ``erp.reporting_currency``
system setting (type enum, allowed USD, EUR, GBP, CAD, AUD, JPY, CHF
and MXN, default USD) is the currency every ERP is assumed to report
in.

AC-2/AC-3 — the warning: ``GET /api/exceptions`` reads
``erp_source_currencies`` once per call, through the real repository
against the test database (no fake of the route module).  Each stored
code that differs from the setting, ignoring case, is one row of kind
"erp_currency_mismatch" (severity high), key
"erp_currency_mismatch:<source_system>:<currency_code>", null
``item_code`` and ``site_code``, title "ERP reports in another
currency", detail "<source_system> reports in <currency_code>, but
values are added up as <setting>. AIERP does not convert currencies.",
view "#/settings" and ``detected_on`` the date of ``seen_at``.  A
failed read adds "currencies" to ``sources_failed`` with status 200,
the other sources still answering.  The kind stands directly after
"erp_code_clash" in ``_KIND_ORDER``.

AC-4 — 'usd' and 'USD' are the same currency: the reader normalizes
before the codes are persisted, the repository normalizes before it
stores, and the warning compares ignoring case, so a lower-case
``usd`` stored straight in the table yields no row against the
default setting.

Identity — the triage list is open to any signed-in user while the
acknowledgement is admin-only, so the pass also runs as a NON-ADMIN
(role ``user``): a second pass in the same test after a full reload
(a fresh TestClient) boots the page the production way — the
development token the page would hold from its ``#access_token=...``
fragment sent as its Bearer token, ``GET /api/auth/config`` (public),
then ``GET /api/auth/me`` — and asserts the admin-only control is
absent (a PUT of the acknowledgement answers 403 with the unchanged
detail) while the page still renders (the triage list answers 200
with the currency row before and after the refusal).
"""

import httpx
from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
from app.db.connection import get_connection
from app.feed.odoo_client import OdooClient, OdooError
from app.main import app
from app.repositories.erp_code_clashes_postgres_repository import (
    ErpCodeClashPostgresRepository,
)
from app.repositories.erp_source_currencies_postgres_repository import (
    ErpSourceCurrencyPostgresRepository,
)
from app.sync.odoo import run_sync
from tests.migration_head import CURRENT_HEAD

URL = "/api/exceptions"
SETTINGS_URL = "/api/settings"
REPORTING_CURRENCY_KEY = "erp.reporting_currency"
ADMIN_ONLY_DETAIL = "Role 'admin' is required to perform this action."

AS_OF = "2026-11-05T09:00:00Z"
# The fixed seen_at the warning checks pin their rows to, so
# detected_on (the date of seen_at) is asserted exactly.
SEEN_AT = "2026-11-05T08:30:00+00:00"

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
        "business_key": "odoo:acme_prod:item_site:1019",
        "item_code": "ITEM-1019",
        "site_code": "SITE-1019",
        "source_system": "odoo",
    },
]

CANNED = {
    "item_sites": ITEM_SITE_ROWS,
    "on_hand": [],
    "demand_events": [],
    "lead_time_observations": [],
    "open_purchase_lines": [],
    "open_sales_lines": [],
}

# The res.company rows the stubbed search_read answers: "usd" and
# "USD" are one code, company 4 has no currency and is ignored.
COMPANIES = [
    {"id": 1, "currency_id": [11, "usd"]},
    {"id": 2, "currency_id": [11, "USD"]},
    {"id": 3, "currency_id": [12, "eur"]},
    {"id": 4, "currency_id": False},
    {"id": 5, "currency_id": [13, "GBP"]},
]


class _StubRepository:
    """Stands in for a Postgres repository: records the rows it was handed."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def upsert_many(self, rows):
        self.calls.append(rows)
        return {"inserted": len(rows), "updated": 0, "table": self.table}


class _BaseStub:
    """Stands in for a pre-#1019 OdooClient: the six read models only,
    no read_company_currencies, so the runner's optional read skips."""

    def __init__(self):
        self.read_order = []

    def _read(self, name):
        self.read_order.append(name)
        return CANNED[name]

    def read_item_sites(self):
        return self._read("item_sites")

    def read_on_hand(self, as_of=None):
        return self._read("on_hand")

    def read_demand_events(self):
        return self._read("demand_events")

    def read_lead_time_observations(self):
        return self._read("lead_time_observations")

    def read_open_purchase_lines(self, as_of=None):
        return self._read("open_purchase_lines")

    def read_open_sales_lines(self, as_of=None):
        return self._read("open_sales_lines")


class _CurrencyStub(_BaseStub):
    """The six reads plus read_company_currencies.

    Answers ``codes`` (the same list object on every call); a stub
    built with ``fail`` raises OdooError from the read instead."""

    def __init__(self, codes, fail=False):
        super().__init__()
        self.codes = codes
        self.fail = fail

    def read_company_currencies(self):
        self.read_order.append("read_company_currencies")
        if self.fail:
            raise OdooError("stub read_company_currencies failed")
        return self.codes


def _mismatch_rows(body):
    return [row for row in body["rows"] if row["kind"] == "erp_currency_mismatch"]


def test_issue1019_freeform(monkeypatch, client, admin_token, user_token):
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    repo = ErpSourceCurrencyPostgresRepository()

    # -- The column: after the migrations, the table exists, the
    #    primary key is the pair of text columns, and seen_at is a
    #    not-null timestamptz with a now() server default. ----------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_system, currency_code, seen_at "
                "FROM erp_source_currencies LIMIT 0"
            )
            cur.execute(
                "SELECT a.attname "
                "FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indrelid "
                "JOIN pg_attribute a "
                "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                "WHERE c.relname = 'erp_source_currencies' "
                "AND i.indisprimary "
                "ORDER BY a.attnum"
            )
            primary_key = [row[0] for row in cur.fetchall()]
            cur.execute(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'erp_source_currencies' "
                "ORDER BY ordinal_position"
            )
            columns = [
                dict(
                    zip(
                        ("column_name", "data_type", "is_nullable", "column_default"),
                        row,
                    )
                )
                for row in cur.fetchall()
            ]
            cur.execute("SELECT version_num FROM alembic_version")
            migration_version = cur.fetchone()[0]
    finally:
        conn.close()
    assert primary_key == ["source_system", "currency_code"]
    assert [c["column_name"] for c in columns] == [
        "source_system",
        "currency_code",
        "seen_at",
    ]
    assert columns[0]["data_type"] == "text"
    assert columns[0]["is_nullable"] == "NO"
    assert columns[1]["data_type"] == "text"
    assert columns[1]["is_nullable"] == "NO"
    assert columns[2]["data_type"] == "timestamp with time zone"
    assert columns[2]["is_nullable"] == "NO"
    assert columns[2]["column_default"] == "now()"
    # The head migration is the one that created the table.
    assert migration_version == CURRENT_HEAD

    # -- The read: sorted distinct codes from a stubbed search_read,
    #    one search_read on res.company with the currency_id field,
    #    a company with no currency ignored, 'usd'/'USD' one code. ---
    search_reads = []

    def fake_search_read(model, domain, fields, limit=None, offset=0, order=None):
        search_reads.append((model, list(domain), list(fields)))
        return COMPANIES

    odoo_client = OdooClient(client=httpx.Client())
    monkeypatch.setattr(odoo_client, "search_read", fake_search_read)
    assert odoo_client.read_company_currencies() == ["EUR", "GBP", "USD"]
    assert search_reads == [("res.company", [], ["currency_id"])]
    odoo_client.close()

    # -- Stored: a fake client answering ["EUR", "USD"] stores both
    #    for odoo, and the result's currencies lists them. ----------
    stub = _CurrencyStub(["EUR", "USD"])
    repos = {table: _StubRepository(table) for table in TABLES}
    repo_kwargs = {f"{table}_repo": repos[table] for table in TABLES}
    result = run_sync(client=stub, as_of=AS_OF, currencies_repo=repo, **repo_kwargs)
    assert stub.read_order == [
        "item_sites",
        "on_hand",
        "demand_events",
        "lead_time_observations",
        "open_purchase_lines",
        "open_sales_lines",
        "read_company_currencies",
    ]
    assert result["currencies"] == ["EUR", "USD"]
    assert {
        (row["source_system"], row["currency_code"]) for row in repo.list_all()
    } == {("odoo", "EUR"), ("odoo", "USD")}

    # A later sync answering ["USD"] leaves only USD.
    stub = _CurrencyStub(["USD"])
    repos = {table: _StubRepository(table) for table in TABLES}
    repo_kwargs = {f"{table}_repo": repos[table] for table in TABLES}
    result = run_sync(client=stub, as_of=AS_OF, currencies_repo=repo, **repo_kwargs)
    assert result["currencies"] == ["USD"]
    stored = repo.list_all()
    assert [(row["source_system"], row["currency_code"]) for row in stored] == [
        ("odoo", "USD")
    ]

    # -- Skipped: a client without the method and one whose read
    #    raises OdooError leave the stored codes as they were, add no
    #    currencies key, and still store the item sites. -------------
    for skip_stub, attempted in (
        (_BaseStub(), False),
        (_CurrencyStub(["EUR"], fail=True), True),
    ):
        repos = {table: _StubRepository(table) for table in TABLES}
        repo_kwargs = {f"{table}_repo": repos[table] for table in TABLES}
        result = run_sync(client=skip_stub, as_of=AS_OF, **repo_kwargs)
        assert "currencies" not in result
        assert ("read_company_currencies" in skip_stub.read_order) is attempted
        for table in TABLES:
            assert repos[table].calls == [CANNED[table]], table
        assert [
            (row["source_system"], row["currency_code"]) for row in repo.list_all()
        ] == [("odoo", "USD")]

    # -- The warning: real route, real repositories, test database. ---
    # EUR stored, the default setting (USD): exactly one row, with
    # every field the criterion states.
    repo.replace_for("odoo", ["EUR"])
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE erp_source_currencies SET seen_at = %s "
                "WHERE source_system = 'odoo' AND currency_code = 'EUR'",
                (SEEN_AT,),
            )
        conn.commit()
    finally:
        conn.close()
    resp = client.get(URL)
    assert resp.status_code == 200
    rows = _mismatch_rows(resp.json())
    assert len(rows) == 1
    row = rows[0]
    assert row["severity"] == "high"
    assert row["key"] == "erp_currency_mismatch:odoo:EUR"
    assert row["item_code"] is None
    assert row["site_code"] is None
    assert row["title"] == "ERP reports in another currency"
    assert row["detail"] == (
        "odoo reports in EUR, but values are added up as USD. "
        "AIERP does not convert currencies."
    )
    assert row["view"] == "#/settings"
    assert row["detected_on"] == "2026-11-05"
    assert "currencies" not in resp.json()["sources_failed"]

    # Only USD stored: no row.
    repo.replace_for("odoo", ["USD"])
    assert _mismatch_rows(client.get(URL).json()) == []

    # A lower-case 'usd' stored straight in the table (past the
    # normalization): the comparison ignores case, still no row.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM erp_source_currencies WHERE source_system = 'odoo'"
            )
            cur.execute(
                "INSERT INTO erp_source_currencies (source_system, currency_code) "
                "VALUES ('odoo', 'usd')"
            )
        conn.commit()
    finally:
        conn.close()
    assert _mismatch_rows(client.get(URL).json()) == []

    # The setting changed to EUR through the settings route: EUR
    # answers no row and USD answers one, against the new setting.
    put = admin.put(f"{SETTINGS_URL}/{REPORTING_CURRENCY_KEY}", json={"value": "EUR"})
    assert put.status_code == 200, put.text
    assert put.json()["value"] == "EUR"
    assert put.json()["source"] == "system"
    repo.replace_for("odoo", ["EUR"])
    assert _mismatch_rows(client.get(URL).json()) == []
    repo.replace_for("odoo", ["USD"])
    body = client.get(URL).json()
    rows = _mismatch_rows(body)
    assert len(rows) == 1
    assert rows[0]["key"] == "erp_currency_mismatch:odoo:USD"
    assert rows[0]["detail"] == (
        "odoo reports in USD, but values are added up as EUR. "
        "AIERP does not convert currencies."
    )
    deleted = admin.delete(f"{SETTINGS_URL}/{REPORTING_CURRENCY_KEY}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["value"] == "USD"
    assert deleted.json()["source"] == "default"

    # -- A failed read: the repository raises — status 200,
    #    "currencies" in sources_failed, another source's row still
    #    answers (an open code clash, stored through its own real
    #    repository). --------------------------------------------------
    class RaisingCurrenciesRepo:
        """The fake for the route module's currencies repository: its
        read raises, and it must never be written."""

        list_calls = 0

        def list_all(self):
            RaisingCurrenciesRepo.list_calls += 1
            raise RuntimeError("the erp_source_currencies read is down")

        def replace_for(self, source_system, codes):
            raise AssertionError("the exceptions route must not store currencies")

    ErpCodeClashPostgresRepository().record_clash(
        "ITEM-1019", "SITE-1019", "acumatica", "odoo"
    )
    monkeypatch.setattr(
        exceptions, "_erp_source_currencies_repo", RaisingCurrenciesRepo()
    )
    resp = client.get(URL)
    assert resp.status_code == 200
    body = resp.json()
    assert "currencies" in body["sources_failed"]
    assert RaisingCurrenciesRepo.list_calls == 1
    clash_rows = [row for row in body["rows"] if row["kind"] == "erp_code_clash"]
    assert len(clash_rows) == 1
    assert clash_rows[0]["key"] == "erp_code_clash:odoo:ITEM-1019/SITE-1019"

    # The real repository answers again for the following reads.
    monkeypatch.setattr(exceptions, "_erp_source_currencies_repo", repo)

    # -- The order: the kind stands directly after erp_code_clash. ----
    assert (
        exceptions._KIND_ORDER["erp_currency_mismatch"]
        == exceptions._KIND_ORDER["erp_code_clash"] + 1
    )

    # --- Identity: the non-admin pass, after a full reload -------------
    # The triage list is open to any signed-in user; only the
    # acknowledgement is admin-only.  A fresh client (full reload)
    # carries the development token the page would hold from its
    # #access_token=... fragment: sub dev-user-1, role "user" — a
    # non-admin.
    repo.replace_for("odoo", ["EUR"])
    viewer = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})

    config = viewer.get("/api/auth/config")
    assert config.status_code == 200
    assert config.json()["authEnabled"] is True

    me = viewer.get("/api/auth/me")
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["authenticated"] is True
    assert me_body["user"]["sub"] == "dev-user-1"
    assert me_body["user"]["roles"] == ["user"]
    assert "admin" not in me_body["user"]["roles"]

    # The page still renders for the non-admin: the triage list
    # answers the currency row (the setting is back to the USD
    # default, EUR stored).
    page = viewer.get(URL)
    assert page.status_code == 200
    rows = _mismatch_rows(page.json())
    assert len(rows) == 1
    assert rows[0]["key"] == "erp_currency_mismatch:odoo:EUR"

    # The admin-only control is absent: the acknowledgement is
    # refused, body and status unchanged.
    ack = viewer.put(f"{URL}/ack", json={"key": "erp_currency_mismatch:odoo:EUR"})
    assert ack.status_code == 403
    assert ack.json() == {"detail": ADMIN_ONLY_DETAIL}

    # ...and the page still renders after the refusal, row unchanged.
    again = viewer.get(URL)
    assert again.status_code == 200
    assert _mismatch_rows(again.json()) == rows
