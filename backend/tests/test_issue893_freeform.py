"""Issue #893 proof: 1-Step transfers and the status readback.

``AcumaticaClient._transfer_order_payload`` writes the
``TransferType`` "1-Step" (proven on the PUT body a created
transfer order carries), and the client gains
``read_transfer_order_statuses(reference_nbrs)`` — a read-only
status read over the reference numbers — proven through the same
recording ``httpx.MockTransport`` seam as issue #880 (the per-call
``httpx.Client`` comes from the module-level ``_build_client``,
which this test replaces with a factory over a recording mock
transport that serves canned Acumatica answers: login 204 carrying
the session cookie; INSite rows 154=WHOLESALE and 159=RETAIL; an
empty duplicate search; a PUT answer carrying ReferenceNbr 000901
and Status "On Hold"; status answers for the requested reference
numbers; logout 204).

Proved here, in order:

  * the write: a created transfer order's PUT body carries
    ``"TransferType": {"value": "1-Step"}`` — the exact 1-Step
    ON-HOLD payload, nothing else changed;
  * the read: 25 reference numbers (one of them carrying a single
    quote) go out in two ``GET {origin}/entity/Default/{version}/
    TransferOrder`` requests — 20 and 5 — with the quote doubled in
    the ``$filter`` and percent-encoded on the wire, and the answer
    maps each returned number to its status (a number the ERP does
    not return is absent); the transport records no PUT and the
    session is one login and one logout;
  * an empty list answers ``{}`` with no request of any kind, and a
    list over 100, a NUL, an empty entry, or a non-list raises
    ``ValueError`` before any request;
  * the job: ``app.supply.transfer_release.
    refresh_transfer_release_statuses()`` (not a route) with stored
    rows "created" (000901), "exists" (000777), "error" (000555),
    one "created" already "Released" (000321) and one "created"
    without a reference number — the job asks only for 000901 and
    000777, stores "On Hold" and "Released" with a check time on
    those rows, leaves the rest unchanged, and answers
    ``{"checked": 2, "updated": 2}``; with 150 in-flight reference
    numbers the same job calls the read twice, 100 and 50;
  * not configured: with Acumatica unconfigured the job answers
    ``"skipped": "not configured"`` and changes nothing;
  * the audit: ``GET /api/supply-plan/transfers/release/audit``
    rows carry the stored ``erp_status`` and
    ``erp_status_checked_at``;
  * the migration: the database has one head, equal to
    ``CURRENT_HEAD`` imported from ``tests/migration_head.py`` (the
    proof never writes the revision name as a double-quoted
    literal), and ``transfer_releases`` has the two new nullable
    columns;
  * the identity requirement: the proof also runs as a NON-ADMIN
    (role ``user``) in a second pass after a full reload — the page
    boots the production way (the token it would take from the URL
    hash as ``#access_token=...``, the bare development token, with
    ``/api/auth/config`` answering publicly and ``/api/auth/me``
    resolving the viewer for that user), the admin-only control is
    absent — the admin-only transfer release audit is refused for
    the viewer and the refresh job is not a route at all — while the
    page still renders: the settings the page loads answer 200 for
    the viewer and unauthenticated ``/api`` traffic is still 401.
"""

import json
import os
import pathlib

import httpx
import psycopg2
import pytest
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.api as api_package
import app.feed.acumatica_client as acumatica_client
from app.db.connection import get_connection_params, get_cursor
from app.feed.acumatica_client import AcumaticaClient
from app.main import app
from app.supply.transfer_release import refresh_transfer_release_statuses
from tests.migration_head import CURRENT_HEAD

BASE_URL = "https://acumatica.example.com/Instance"
TENANT = "AIERP"
USERNAME = "svc-aierp"
PASSWORD = "s3cret-password!"
VERSION = "25.200.001"

LOGIN_PATH = "/Instance/entity/auth/login"
LOGOUT_PATH = "/Instance/entity/auth/logout"
INSITE_PATH = f"/Instance/odatav4/{TENANT}/INSite"
TRANSFER_PATH = f"/Instance/entity/Default/{VERSION}/TransferOrder"
AUDIT = "/api/supply-plan/transfers/release/audit"

ORIGIN = "AIERP transfer 2026-11-01 site WHOLESALE to RETAIL"
LINES = [{"item_code": "CONBOTTLE1", "quantity": 12}]

# The 25 reference numbers of the read: 24 plain, one carrying a
# single quote, and one the ERP never returns.
QUOTED_REF = "O'HARE-77"
MISSING_REF = "TR893-0007"
REFS = [f"TR893-{i:04d}" for i in range(1, 25)] + [QUOTED_REF]

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

ADMIN_TOKEN = "dev-secret-token:admin"
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

_MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations"
)

# The session the login establishes: it rides on every later request
# of the session, and no error message may ever carry it.
SESSION_COOKIE_NAME = "acum.session"
SESSION_COOKIE_VALUE = "cookie-secret-9f8e7d"


def _canned_status(reference_nbr: str) -> str:
    """The canned Acumatica status of one reference number."""
    if reference_nbr == QUOTED_REF:
        return "Canceled"
    return "Released" if int(reference_nbr.rsplit("-", 1)[1]) % 2 else "On Hold"


def _numbers_from_filter(filter_text: str) -> list[str]:
    """The reference numbers of one status-read $filter.

    One ``ReferenceNbr eq '<nbr>'`` term per number, the single
    quotes of every number doubled inside the OData literal.
    """
    numbers = []
    for term in filter_text.split(" or "):
        literal = term.split(" eq ", 1)[1]
        assert literal.startswith("'") and literal.endswith("'")
        numbers.append(literal[1:-1].replace("''", "'"))
    return numbers


def test_issue893_freeform(monkeypatch, client) -> None:
    # --- Isolate the Acumatica environment (writes on). ----------------
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", BASE_URL)
    monkeypatch.setenv("ACUMATICA_TENANT", TENANT)
    monkeypatch.setenv("ACUMATICA_USERNAME", USERNAME)
    monkeypatch.setenv("ACUMATICA_PASSWORD", PASSWORD)
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")

    requests_log: list[httpx.Request] = []

    def status_read(request: httpx.Request) -> httpx.Response:
        flt = httpx.QueryParams(request.url.query).get("$filter") or ""
        numbers = _numbers_from_filter(flt)
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "ReferenceNbr": nbr,
                        "Status": _canned_status(nbr),
                    }
                    for nbr in numbers
                    if nbr != MISSING_REF
                ]
            },
        )

    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(request)
        path = request.url.path
        if request.method == "POST" and path == LOGIN_PATH:
            return httpx.Response(
                204,
                headers={
                    "set-cookie": (
                        f"{SESSION_COOKIE_NAME}={SESSION_COOKIE_VALUE}; path=/"
                    )
                },
            )
        if request.method == "POST" and path == LOGOUT_PATH:
            return httpx.Response(204)
        if request.method == "GET" and path == INSITE_PATH:
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"SiteID": 154, "SiteCD": "WHOLESALE"},
                        {"SiteID": 159, "SiteCD": "RETAIL"},
                    ]
                },
            )
        if request.method == "GET" and path == TRANSFER_PATH:
            flt = httpx.QueryParams(request.url.query).get("$filter") or ""
            if "ReferenceNbr eq" in flt:
                return status_read(request)
            return httpx.Response(200, json={"value": []})
        if request.method == "PUT" and path == TRANSFER_PATH:
            return httpx.Response(
                200, json={"ReferenceNbr": "000901", "Status": "On Hold"}
            )
        return httpx.Response(404, text="unexpected request")

    # _build_client is replaced: one httpx.Client over a recording
    # httpx.MockTransport, per call — the issue 880 seam.
    monkeypatch.setattr(
        acumatica_client,
        "_build_client",
        lambda ca_bundle, timeout: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    acumatica = AcumaticaClient()

    def recorded() -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in requests_log]

    # -- 1. the write: the created order's PUT body is 1-Step -----------
    requests_log.clear()
    results = acumatica.create_transfer_orders(
        [{"from_site_id": 154, "to_site_id": 159, "origin": ORIGIN, "lines": LINES}]
    )
    assert results[0]["outcome"] == "created"
    assert results[0]["reference_nbr"] == "000901"
    assert results[0]["status"] == "On Hold"
    puts = [r for r in requests_log if r.method == "PUT"]
    assert len(puts) == 1
    body = json.loads(puts[0].content)
    assert body["TransferType"] == {"value": "1-Step"}
    assert body == {
        "TransferType": {"value": "1-Step"},
        "FromWarehouseID": {"value": "WHOLESALE"},
        "ToWarehouseID": {"value": "RETAIL"},
        "Hold": {"value": True},
        "Description": {"value": ORIGIN},
        "Details": [{"InventoryID": {"value": "CONBOTTLE1"}, "Qty": {"value": 12}}],
    }
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", INSITE_PATH),
        ("GET", TRANSFER_PATH),  # the duplicate search
        ("PUT", TRANSFER_PATH),  # the write
        ("POST", LOGOUT_PATH),
    ]

    # -- 2. the read: 25 numbers in two GETs (20 and 5), no PUT ---------
    requests_log.clear()
    answer = acumatica.read_transfer_order_statuses(REFS)
    assert answer == {nbr: _canned_status(nbr) for nbr in REFS if nbr != MISSING_REF}
    assert MISSING_REF not in answer  # absent, not null
    gets = [
        r for r in requests_log if r.method == "GET" and r.url.path == TRANSFER_PATH
    ]
    assert len(gets) == 2
    assert (
        _numbers_from_filter(httpx.QueryParams(gets[0].url.query)["$filter"])
        == REFS[:20]
    )
    assert (
        _numbers_from_filter(httpx.QueryParams(gets[1].url.query)["$filter"])
        == REFS[20:]
    )
    # The quote in the number is doubled in the OData literal and
    # percent-encoded on the wire.
    second_filter = httpx.QueryParams(gets[1].url.query)["$filter"]
    assert "ReferenceNbr eq 'O''HARE-77'" in second_filter  # quote doubled
    assert "%27%27" in gets[1].url.query.decode("ascii")  # percent-encoded
    assert not any(r.method == "PUT" for r in requests_log)  # a read, never a write
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", TRANSFER_PATH),  # the first 20
        ("GET", TRANSFER_PATH),  # the last 5
        ("POST", LOGOUT_PATH),
    ]

    # -- 3. empty: no request at all; hostile input: ValueError ---------
    requests_log.clear()
    assert acumatica.read_transfer_order_statuses([]) == {}
    assert requests_log == []
    for bad in (
        ["TR1"] * 101,  # over the 100-number limit
        ["TR\x001"],  # a NUL
        [""],  # empty
        [None],  # non-string
        "TR1",  # a bare string
        42,  # not a list
    ):
        requests_log.clear()
        with pytest.raises(ValueError):
            acumatica.read_transfer_order_statuses(bad)  # type: ignore[arg-type]
        assert requests_log == []

    # -- 4. the job: the fake client, the real rows ---------------------
    def insert_release(
        outcome: str, reference_nbr: str | None, erp_status: str | None = None
    ) -> int:
        conn = psycopg2.connect(**get_connection_params())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO transfer_releases "
                    "(origin, from_site_code, to_site_code, outcome, "
                    "reference_nbr, erp_status, line_count, lines, actor) "
                    "VALUES (%s, 'WHOLESALE', 'RETAIL', %s, %s, %s, 1, "
                    "'[]'::jsonb, 'developer') RETURNING id",
                    (
                        f"{ORIGIN} {reference_nbr or 'none'}",
                        outcome,
                        reference_nbr,
                        erp_status,
                    ),
                )
                row_id = cur.fetchone()[0]
            conn.commit()
            return row_id
        finally:
            conn.close()

    def erp_state(row_id: int) -> tuple:
        with get_cursor() as cur:
            cur.execute(
                "SELECT erp_status, erp_status_checked_at "
                "FROM transfer_releases WHERE id = %s",
                (row_id,),
            )
            row = cur.fetchone()
        return row["erp_status"], row["erp_status_checked_at"]

    created_row = insert_release("created", "000901")
    exists_row = insert_release("exists", "000777")
    error_row = insert_release("error", "000555")
    released_row = insert_release("created", "000321", erp_status="Released")
    noref_row = insert_release("created", None)

    read_calls: list[list[str]] = []

    def fake_read(self, reference_nbrs):
        read_calls.append(list(reference_nbrs))
        return {
            nbr: ("Released" if nbr == "000777" else "On Hold")
            for nbr in reference_nbrs
        }

    monkeypatch.setattr(AcumaticaClient, "read_transfer_order_statuses", fake_read)

    # The five rows: the job asks only for 000901 and 000777.
    result = refresh_transfer_release_statuses()
    assert result == {"checked": 2, "updated": 2}
    assert read_calls == [["000777", "000901"]]
    status, checked_at = erp_state(created_row)
    assert status == "On Hold"
    assert checked_at is not None  # the check time
    assert erp_state(exists_row)[0] == "Released"
    assert erp_state(exists_row)[1] is not None
    assert erp_state(error_row) == (None, None)  # "error" is never read
    assert erp_state(released_row) == (
        "Released",
        None,
    )  # already Released: unchanged, no check time
    assert erp_state(noref_row) == (None, None)  # no reference number

    # 150 in-flight reference numbers: the read is called twice,
    # 100 and 50.
    with get_cursor() as cur:
        cur.execute(
            "UPDATE transfer_releases SET erp_status = 'Released', "
            "erp_status_checked_at = now() WHERE id = %s",
            (created_row,),
        )
    for i in range(1, 151):
        insert_release("created", f"TR893-{i:04d}")
    read_calls.clear()
    result = refresh_transfer_release_statuses()
    assert result == {"checked": 150, "updated": 150}
    assert len(read_calls) == 2
    assert len(read_calls[0]) == 100
    assert len(read_calls[1]) == 50
    assert sorted(n for call in read_calls for n in call) == sorted(
        f"TR893-{i:04d}" for i in range(1, 151)
    )

    # -- 5. the audit rows carry the stored erp_status ------------------
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    audit = admin.get(AUDIT, params={"limit": 200})
    assert audit.status_code == 200, audit.text
    by_ref = {row["reference_nbr"]: row for row in audit.json()}
    assert by_ref["000777"]["erp_status"] == "Released"
    assert isinstance(by_ref["000777"]["erp_status_checked_at"], str)
    assert by_ref["000777"]["erp_status_checked_at"]
    assert by_ref["000901"]["erp_status"] == "Released"
    assert isinstance(by_ref["000901"]["erp_status_checked_at"], str)
    assert by_ref["000555"]["erp_status"] is None
    assert by_ref["000555"]["erp_status_checked_at"] is None
    assert by_ref["TR893-0001"]["erp_status"] == "On Hold"
    assert isinstance(by_ref["TR893-0001"]["erp_status_checked_at"], str)

    # -- 6. not configured: skipped, nothing changed --------------------
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    fresh_row = insert_release("created", "000888")
    read_calls.clear()
    result = refresh_transfer_release_statuses()
    assert result == {"checked": 0, "updated": 0, "skipped": "not configured"}
    assert read_calls == []  # no client call
    assert erp_state(fresh_row) == (None, None)  # changed nothing

    # -- 7. the migration: one head, the two new columns ----------------
    script_dir = ScriptDirectory(_MIGRATIONS_DIR)
    assert list(script_dir.get_heads()) == [CURRENT_HEAD]
    with get_cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        assert cur.fetchone()["version_num"] == CURRENT_HEAD
        cur.execute(
            "SELECT column_name, is_nullable, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'transfer_releases' "
            "AND column_name IN ('erp_status', 'erp_status_checked_at')"
        )
        columns = {
            row["column_name"]: (row["is_nullable"].lower(), row["data_type"])
            for row in cur.fetchall()
        }
    assert columns == {
        "erp_status": ("yes", "text"),
        "erp_status_checked_at": ("yes", "timestamp with time zone"),
    }

    # -- 8. Identity: a second pass after a full reload, as the
    # NON-ADMIN (role "user").  The page is booted the production
    # way: the token it would take from the URL hash as
    # #access_token=... is the development token (role "user") sent
    # as its Bearer token; GET /api/auth/config answers publicly and
    # GET /api/auth/me resolves the viewer.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon = TestClient(app)
    assert anon.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == "dev-user-1"
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]
    # The admin-only control is absent: the transfer release audit is
    # admin-only and refused for the viewer, and this issue's refresh
    # job is not a route at all — no route module exposes it.
    assert viewer.get(AUDIT, params={"limit": 200}).status_code == 403
    for path in pathlib.Path(api_package.__file__).parent.glob("*.py"):
        assert "refresh_transfer_release_statuses" not in path.read_text()
    # The page still renders: the settings the page loads answer 200
    # for the viewer, and the gate is intact — unauthenticated /api
    # traffic is still 401.
    assert viewer.get("/api/settings").status_code == 200
    assert anon.get("/api/settings").status_code == 401
