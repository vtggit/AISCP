"""Issue #1038 proof: the Acumatica ledger currencies read and the sync.

AC-1 — the read: ``AcumaticaClient.read_ledger_currencies()`` signs in
the way the client's other reads do (the ``read_purchase_orders``
model: one login, the work, one best-effort logout in a ``finally``),
sends exactly one ``GET {origin}/entity/Default/{version}/Ledger`` with
no ``$select``, and signs out. It answers the sorted, distinct,
uppercased ``Currency`` values of the ledgers whose ``Type`` is
``Actual``: a ledger whose ``Type`` or ``Currency`` is missing or not a
non-empty string contributes nothing — a whitespace-only ``Currency``
is a non-empty string, so it contributes its uppercased value rather
than being treated as empty — and each field may come as a plain value
or as Acumatica's ``{"value": ...}`` wrapper. Proved with
no network, through the ``httpx.MockTransport`` seam the earlier
client tests use (the module-level ``_build_client`` is replaced with a
factory over a recording mock transport — login 204; the bare-array
Ledger rows; logout 204): with ledgers ACTUAL (``Actual``, ``usd``),
BUDGET (``Budget``, ``EUR``) and EUACTUAL (``Actual``, ``EUR``), one
field wrapped in ``{"value": ...}``, the method answers
``["EUR", "USD"]`` and exactly one GET goes to the ``Ledger`` path
between the sign-in and the sign-out; a failed sign-in or a non-200
answer raises ``AcumaticaError`` the way the other reads do.

AC-2 — the step: ``app.sync.currencies.sync_acumatica_currencies``
defaults ``reader`` to a new ``AcumaticaClient().read_ledger_currencies``
and ``repo`` to an ``ErpSourceCurrencyPostgresRepository``; it stores
the codes for ``acumatica`` through
``repo.replace_for("acumatica", codes)`` and answers the codes. A
reader that raises ``AcumaticaError`` — a missing configuration
included — stores nothing, leaves any stored ``acumatica`` rows exactly
as they were, and answers ``None``.

Identity — the triage list is open to any signed-in user while the
acknowledgement is admin-only, so the pass also runs as a NON-ADMIN
(role ``user``): a fresh TestClient boots the page the production way —
the development token the page would hold from its ``#access_token=...``
fragment sent as its Bearer token, ``GET /api/auth/config`` (public),
then ``GET /api/auth/me`` — and asserts the admin-only control is
refused (a PUT of the acknowledgement answers 403 with the unchanged
detail) while the page still renders (the triage list answers 200 with
the currency rows before and after the refusal).
"""

import httpx
import pytest
from fastapi.testclient import TestClient

import app.feed.acumatica_client as acumatica_client
from app.feed.acumatica_client import AcumaticaClient, AcumaticaError
from app.main import app
from app.repositories.erp_source_currencies_postgres_repository import (
    ErpSourceCurrencyPostgresRepository,
)
from app.sync.currencies import sync_acumatica_currencies

BASE_URL = "https://acumatica.example.com/Instance"
TENANT = "AIERP"
USERNAME = "svc-aierp"
PASSWORD = "s3cret-password!"
VERSION = "25.200.001"

LOGIN_PATH = "/Instance/entity/auth/login"
LOGOUT_PATH = "/Instance/entity/auth/logout"
LEDGER_PATH = f"/Instance/entity/Default/{VERSION}/Ledger"

ADMIN_ONLY_DETAIL = "Role 'admin' is required to perform this action."

ACUMATICA_VARS = (
    "ACUMATICA_BASE_URL",
    "ACUMATICA_TENANT",
    "ACUMATICA_USERNAME",
    "ACUMATICA_PASSWORD",
    "ACUMATICA_ENDPOINT_VERSION",
    "ACUMATICA_CA_BUNDLE",
    "ACUMATICA_TIMEOUT_SECONDS",
    "ACUMATICA_ALLOW_INSECURE",
    "ACUMATICA_ALLOW_WRITES",
)

# The three ledgers the entity API answers: ACTUAL (Actual, usd),
# BUDGET (Budget, EUR) and EUACTUAL (Actual, EUR) — the first row's
# Currency and the second row's Type come wrapped as {"value": ...},
# the rest plain, and every row carries the usual envelope metadata.
LEDGER_ROWS = [
    {
        "id": "LEDGER-ACTUAL",
        "rowNumber": 0,
        "note": None,
        "custom": {},
        "_links": {"self": {"href": f"{BASE_URL}/entity/Default/{VERSION}/Ledger"}},
        "LedgerID": "ACTUAL",
        "Description": {"value": "Actual"},
        "Type": "Actual",
        "Currency": {"value": "usd"},
    },
    {
        "id": "LEDGER-BUDGET",
        "rowNumber": 1,
        "note": None,
        "custom": {},
        "LedgerID": "BUDGET",
        "Description": {"value": "Budget"},
        "Type": {"value": "Budget"},
        "Currency": "EUR",
    },
    {
        "id": "LEDGER-EUACTUAL",
        "rowNumber": 2,
        "note": None,
        "custom": {},
        "LedgerID": "EUACTUAL",
        "Description": {"value": "EU Actual"},
        "Type": "Actual",
        "Currency": "EUR",
    },
]


def test_issue1038_freeform(monkeypatch, client, user_token):
    # Isolate the Acumatica environment; the write switch is NOT set —
    # the read must not consult it.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", BASE_URL)
    monkeypatch.setenv("ACUMATICA_TENANT", TENANT)
    monkeypatch.setenv("ACUMATICA_USERNAME", USERNAME)
    monkeypatch.setenv("ACUMATICA_PASSWORD", PASSWORD)

    requests_log: list[httpx.Request] = []
    canned: dict = {
        "login_status": 204,
        "ledger": lambda request: httpx.Response(200, json=LEDGER_ROWS),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/entity/auth/login"):
            return httpx.Response(canned["login_status"])
        if request.method == "POST" and path.endswith("/entity/auth/logout"):
            return httpx.Response(204)
        if request.method == "GET" and path.endswith("/Ledger"):
            return canned["ledger"](request)
        return httpx.Response(404, text="unexpected request")

    # _build_client is replaced: one httpx.Client over a recording
    # httpx.MockTransport, per call — no network.
    monkeypatch.setattr(
        acumatica_client,
        "_build_client",
        lambda ca_bundle, timeout: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    api = AcumaticaClient()

    def ledger_gets() -> list[httpx.Request]:
        return [
            r for r in requests_log if r.method == "GET" and r.url.path == LEDGER_PATH
        ]

    def one_session() -> None:
        assert [(r.method, r.url.path) for r in requests_log] == [
            ("POST", LOGIN_PATH),
            ("GET", LEDGER_PATH),
            ("POST", LOGOUT_PATH),
        ]
        assert all(r.url.host == "acumatica.example.com" for r in requests_log)

    # ------------------------------------------------------------------
    # AC-1: the read — the three ledgers, one field wrapped, answer the
    # sorted, distinct, uppercased currencies of the Actual ledgers,
    # with exactly one GET to the Ledger path between sign-in and
    # sign-out and no $select anywhere on the wire.
    # ------------------------------------------------------------------
    requests_log.clear()
    assert api.read_ledger_currencies() == ["EUR", "USD"]
    assert len(ledger_gets()) == 1
    assert "$select" not in httpx.QueryParams(ledger_gets()[0].url.query)
    one_session()

    # The envelope object whose "value" is the row array is accepted
    # exactly as the other entity reads accept it.
    canned["ledger"] = lambda request: httpx.Response(200, json={"value": LEDGER_ROWS})
    requests_log.clear()
    assert api.read_ledger_currencies() == ["EUR", "USD"]
    assert len(ledger_gets()) == 1
    one_session()
    canned["ledger"] = lambda request: httpx.Response(200, json=LEDGER_ROWS)

    # A ledger whose Type or Currency is missing or not a non-empty
    # string contributes nothing: a null, an empty, an absent and a
    # non-string value all fall out, and a whitespace-only Type is not
    # "Actual".
    hostile = [
        {**LEDGER_ROWS[0], "id": "X1", "Type": None},
        {**LEDGER_ROWS[0], "id": "X2", "Currency": ""},
        {**LEDGER_ROWS[0], "id": "X3", "Type": None, "Currency": None},
        {**LEDGER_ROWS[0], "id": "X4", "Currency": 42},
        {**LEDGER_ROWS[0], "id": "X5", "Type": " "},
        {**LEDGER_ROWS[0], "id": "X6", "Type": {"value": None}},
    ]
    canned["ledger"] = lambda request: httpx.Response(200, json=hostile)
    requests_log.clear()
    assert api.read_ledger_currencies() == []
    assert len(ledger_gets()) == 1
    one_session()

    # A whitespace-only Currency, though, IS a non-empty string: it is
    # not treated as empty and contributes its uppercased value, plain
    # or wrapped.
    blank_currency = [
        {**LEDGER_ROWS[0], "id": "W1", "Currency": "  "},
        {**LEDGER_ROWS[2], "id": "W2", "Currency": {"value": "  "}},
    ]
    canned["ledger"] = lambda request: httpx.Response(200, json=blank_currency)
    requests_log.clear()
    assert api.read_ledger_currencies() == ["  "]
    assert len(ledger_gets()) == 1
    one_session()
    canned["ledger"] = lambda request: httpx.Response(200, json=LEDGER_ROWS)

    # Malformed answers fail closed, like the other reads.
    for malformed in (
        lambda request: httpx.Response(200, text="not json"),
        lambda request: httpx.Response(200, json={"value": "not a list"}),
        lambda request: httpx.Response(200, json=["not a row"]),
    ):
        canned["ledger"] = malformed
        requests_log.clear()
        with pytest.raises(AcumaticaError):
            api.read_ledger_currencies()
        one_session()

    # A failed sign-in raises (a 401/403 and any other non-2xx the way
    # the other reads do), sends no read and sends no logout.
    for status in (401, 403, 500):
        canned["login_status"] = status
        requests_log.clear()
        with pytest.raises(AcumaticaError, match=f"HTTP {status}"):
            api.read_ledger_currencies()
        assert [(r.method, r.url.path) for r in requests_log] == [("POST", LOGIN_PATH)]
    canned["login_status"] = 204

    # A non-200 answer raises and the sign-out still goes out.
    for status in (401, 500):
        canned["ledger"] = lambda request, s=status: httpx.Response(s, text="boom")
        requests_log.clear()
        with pytest.raises(AcumaticaError, match=f"HTTP {status}"):
            api.read_ledger_currencies()
        one_session()
    canned["ledger"] = lambda request: httpx.Response(200, json=LEDGER_ROWS)

    # ------------------------------------------------------------------
    # AC-2: the step — the reader's codes are stored for acumatica
    # through replace_for and answered; the other sources' rows are
    # untouched, and a reader that raises AcumaticaError stores nothing
    # and answers None.
    # ------------------------------------------------------------------
    repo = ErpSourceCurrencyPostgresRepository()

    class _RecordingRepo:
        """The real repository, recording every replace_for it is given."""

        def __init__(self):
            self.replace_calls: list[tuple[str, list[str]]] = []

        def replace_for(self, source_system, codes):
            self.replace_calls.append((source_system, list(codes)))
            return repo.replace_for(source_system, codes)

        def list_all(self):
            return repo.list_all()

    store = _RecordingRepo()

    repo.replace_for("odoo", ["EUR"])
    assert sync_acumatica_currencies(reader=lambda: ["USD"], repo=store) == ["USD"]
    assert store.replace_calls == [("acumatica", ["USD"])]
    assert [(r["source_system"], r["currency_code"]) for r in store.list_all()] == [
        ("acumatica", "USD"),
        ("odoo", "EUR"),
    ]

    def failing_reader():
        raise AcumaticaError("the Acumatica Ledger read failed with HTTP 500")

    before = store.list_all()
    assert sync_acumatica_currencies(reader=failing_reader, repo=store) is None
    assert store.replace_calls == [("acumatica", ["USD"])]
    assert ("acumatica", "USD") in {
        (r["source_system"], r["currency_code"]) for r in store.list_all()
    }
    assert store.list_all() == before

    # A missing configuration (the default reader) is an AcumaticaError
    # too: skipped, nothing stored, None answered.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    assert sync_acumatica_currencies(repo=store) is None
    assert store.replace_calls == [("acumatica", ["USD"])]
    assert store.list_all() == before

    # ------------------------------------------------------------------
    # Identity: the triage list is open to any signed-in user; only
    # the acknowledgement is admin-only. A fresh client (full reload)
    # carries the development token the page would hold from its
    # #access_token=... fragment: sub dev-user-1, role "user" — a
    # non-admin.
    # ------------------------------------------------------------------
    assert sync_acumatica_currencies(reader=lambda: ["EUR"], repo=store) == ["EUR"]
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
    # answers the currency rows for the stored codes against the
    # default USD reporting currency.
    page = viewer.get("/api/exceptions")
    assert page.status_code == 200, page.text
    currency_rows = [
        row for row in page.json()["rows"] if row["kind"] == "erp_currency_mismatch"
    ]
    assert {row["key"] for row in currency_rows} == {
        "erp_currency_mismatch:acumatica:EUR",
        "erp_currency_mismatch:odoo:EUR",
    }

    # The admin-only control is absent: the acknowledgement is
    # refused, body and status unchanged.
    ack = viewer.put(
        "/api/exceptions/ack", json={"key": "erp_currency_mismatch:acumatica:EUR"}
    )
    assert ack.status_code == 403
    assert ack.json() == {"detail": ADMIN_ONLY_DETAIL}

    # ...and the page still renders after the refusal, rows unchanged.
    again = viewer.get("/api/exceptions")
    assert again.status_code == 200
    assert {
        row["key"]
        for row in again.json()["rows"]
        if row["kind"] == "erp_currency_mismatch"
    } == {
        "erp_currency_mismatch:acumatica:EUR",
        "erp_currency_mismatch:odoo:EUR",
    }
