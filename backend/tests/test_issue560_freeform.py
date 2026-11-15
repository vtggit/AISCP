"""Issue #560 proof: tracking the released plan POs in Acumatica.

Proved with no network, through the ``httpx.MockTransport`` seam the
earlier client tests use (the module-level ``_build_client`` is
replaced with a factory over a recording mock transport serving the
verbatim PO003072 body and its variants — login 204; the bare-array
PurchaseOrder rows; logout 204), with the ``plan_releases`` read
(``app.aggregate.tracking._load_release_rows``) monkeypatched and
``today`` fixed at 2026-11-10:

  * parsing: ``read_purchase_orders(["PO003072"])`` returns status
    "On Hold", hold true, date "2026-10-21", promised_on
    "2026-11-05", total 66931.41, and the one AACOMPUT01 / WHOLESALE
    / 273.0 / received 0.0 / promised "2026-11-07" line, from the
    verbatim body with its envelope keys ignored, on the entity URL
    with the ``$filter`` ``OrderNbr eq 'PO003072'``; an order the ERP
    did not return is absent, an empty list makes no request, a
    redirect is not followed, and hostile numbers (a quote that is
    doubled on the wire, a non-string entry, a bare string) never
    reach a 500;
  * chunking: 30 order numbers make exactly two GETs (25 + 5) in one
    session, the chunks in input order;
  * classification as of 2026-11-10, with variants of the body: hold
    true -> ``awaiting_approval`` with ``days_waiting`` 20; hold false
    with the line promised 2026-11-07 and nothing received ->
    ``late`` with ``days_late`` 3; 100 of 273 received, promised in
    the future -> ``partially_received``; the line completed (or fully
    received) -> ``received`` (``days_waiting`` stays 20 while the
    order is on hold, and null once it is off hold); status
    ``Canceled`` (or every line cancelled) -> ``cancelled``; an order
    the ERP did not return -> ``missing``; the newest row per order
    number wins, and ``pending`` / ``error`` / number-less rows are
    ignored;
  * sorting and counts: the order list is sorted late, then
    awaiting_approval, partially_received, open, received, cancelled,
    missing — then oldest release first — and the counts carry the
    fixed keys ``awaiting_approval``, ``late``, ``partly_received``
    and ``received`` and nothing else;
  * the route: ``GET /api/aggregate/releases`` with the test Bearer
    returns that payload (``days_waiting`` / ``days_late`` present as
    null where not applicable), an unauthenticated request gets 401,
    incomplete configuration gives 503, a client auth failure gives
    502, a failed second chunk gives 502 with no partial order data
    (and the client itself never merges the first chunk), and with no
    released rows the result is 200 with empty orders and zero counts
    — and no client call — taking precedence over the 503;
  * identity, as a NON-ADMIN (role ``user``, the token the production
    page would carry in the URL hash): the viewer still gets the
    releases payload (the route carries no admin-only control) while
    the admin-only audit route still refuses them 403, and the gate
    stays intact for unauthenticated traffic.
"""

import json
import os
import re
from datetime import date
from decimal import Decimal

import httpx
import pytest

import app.aggregate.tracking as tracking
import app.feed.acumatica_client as acumatica_client
from app.feed.acumatica_client import AcumaticaClient, AcumaticaError
from tests.test_issue565_freeform import VERBATIM_PO003072

BASE_URL = "https://acumatica.example.com/Instance"
TENANT = "AIERP"
USERNAME = "svc-aierp"
PASSWORD = "s3cret-password!"
VERSION = "25.200.001"

LOGIN_PATH = "/Instance/entity/auth/login"
LOGOUT_PATH = "/Instance/entity/auth/logout"
PO_PATH = f"/Instance/entity/Default/{VERSION}/PurchaseOrder"

TODAY = date(2026, 11, 10)

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

# The verbatim PO003072 body as the live Acumatica entity API returns
# it (issue #565): the contract-based REST shape, the order date in
# "Date", the order's promised date in "PromisedOn", each line's
# promised date in "Promised", every field enveloped as
# {"value": ...} and the full envelope metadata on order and lines —
# derived from the verbatim response quoted in the #565 proof.
PO003072 = json.loads(VERBATIM_PO003072)[0]

EXPECTED_LINE = {
    "item_code": "AACOMPUT01",
    "warehouse_code": "WHOLESALE",
    "order_qty": 273.0,
    "received_qty": 0.0,
    "completed": False,
    "cancelled": False,
    "promised": "2026-11-07",
}


def po_line(
    *,
    item="AACOMPUT01",
    warehouse="WHOLESALE",
    qty=273.0,
    received=0.0,
    completed=False,
    cancelled=False,
    promised="2026-11-07T00:00:00",
) -> dict:
    """A variant of the verbatim line, in the same REST shape."""
    return {
        "id": "PO-LINE-VARIANT",
        "rowNumber": 0,
        "note": None,
        "custom": {},
        "InventoryID": {"value": item},
        "WarehouseID": {"value": warehouse},
        "OrderQty": {"value": qty},
        "QtyOnReceipts": {"value": received},
        "Completed": {"value": completed},
        "Cancelled": {"value": cancelled},
        "Promised": {"value": promised},
    }


def po_body(
    nbr="PO003072",
    *,
    status="On Hold",
    hold=True,
    order_date="2026-10-21T00:00:00",
    promised_on="2026-11-05T00:00:00",
    total=66931.41,
    line: dict | None = None,
) -> dict:
    """A variant of the verbatim PO003072 body for ``nbr``."""
    row = {
        "id": f"PO-{nbr.replace('PO', '')}",
        "rowNumber": 0,
        "note": None,
        "custom": {},
        "_links": {
            "self": {"href": f"{BASE_URL}/entity/Default/{VERSION}/PurchaseOrder"}
        },
        "OrderNbr": {"value": nbr},
        "Status": {"value": status},
        "Hold": {"value": hold},
        "Date": {"value": order_date},
        "PromisedOn": {"value": promised_on},
        "OrderTotal": {"value": total},
        "Details": [line if line is not None else PO003072["Details"][0]],
    }
    return row


_UNSET = object()


def release_row(
    nbr: str,
    *,
    row_id: int,
    created_at: str,
    outcome: str = "created",
    month: str = "2026-10",
    site_code: str = "WHOLESALE",
    supplier_code: str = "AACOMPUT",
    value: Decimal = Decimal("66931.41"),
    actor: str = "admin",
    order_nbr: object = _UNSET,
) -> dict:
    """One plan_releases row, newest-first audit shape.

    ``order_nbr`` defaults to ``nbr``; pass ``None`` for a number-less row.
    """
    return {
        "id": row_id,
        "month": month,
        "site_code": site_code,
        "supplier_code": supplier_code,
        "origin": f"AIERP plan {month} site {site_code} vendor {supplier_code}",
        "warehouse_code": site_code,
        "vendor_code": supplier_code,
        "order_nbr": nbr if order_nbr is _UNSET else order_nbr,
        "outcome": outcome,
        "error": None,
        "line_count": 1,
        "value": value,
        "lines": [
            {
                "item_code": "AACOMPUT01",
                "quantity": 273,
                "unit_cost": "245.17",
                "value": "66931.41",
            }
        ],
        "actor": actor,
        "created_at": created_at,
        "updated_at": created_at,
    }


class _FixedDate(date):
    """date with today() fixed at 2026-11-10 (the route's default)."""

    @classmethod
    def today(cls) -> date:
        return cls(2026, 11, 10)


def _filter_nbrs(request: httpx.Request) -> list[str]:
    params = httpx.QueryParams(request.url.query)
    return re.findall(r"OrderNbr eq '([^']*)'", params.get("$filter") or "")


def test_issue560_freeform(monkeypatch) -> None:
    # Isolate the Acumatica environment; the write switch is NOT set —
    # reads must not consult it.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", BASE_URL)
    monkeypatch.setenv("ACUMATICA_TENANT", TENANT)
    monkeypatch.setenv("ACUMATICA_USERNAME", USERNAME)
    monkeypatch.setenv("ACUMATICA_PASSWORD", PASSWORD)

    requests_log: list[httpx.Request] = []
    canned: dict = {
        "login_status": 204,
        "po": lambda request: httpx.Response(200, json=[]),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/entity/auth/login"):
            return httpx.Response(canned["login_status"])
        if request.method == "POST" and path.endswith("/entity/auth/logout"):
            return httpx.Response(204)
        if request.method == "GET" and path.endswith("/PurchaseOrder"):
            return canned["po"](request)
        return httpx.Response(404, text="unexpected request")

    # _build_client is replaced: one httpx.Client over a recording
    # httpx.MockTransport, per call — no network.
    monkeypatch.setattr(
        acumatica_client,
        "_build_client",
        lambda ca_bundle, timeout: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    # The route's default "today" is fixed at 2026-11-10 too.
    monkeypatch.setattr(tracking, "date", _FixedDate)
    client = AcumaticaClient()

    def map_po(mapping: dict[str, dict]) -> None:
        """Serve the mapped bodies for whatever numbers the filter names."""

        def serve(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[mapping[nbr] for nbr in _filter_nbrs(request) if nbr in mapping],
            )

        canned["po"] = serve

    def po_requests() -> list[httpx.Request]:
        return [r for r in requests_log if r.method == "GET" and r.url.path == PO_PATH]

    def one_session() -> None:
        assert [(r.method, r.url.path) for r in requests_log] == [
            ("POST", LOGIN_PATH),
            ("GET", PO_PATH),
            ("POST", LOGOUT_PATH),
        ]
        assert all(r.url.host == "acumatica.example.com" for r in requests_log)

    # ------------------------------------------------------------------
    # AC-1 / AC-2: parsing the verbatim PO003072 body
    # ------------------------------------------------------------------
    canned["po"] = lambda request: httpx.Response(200, json=[PO003072])
    requests_log.clear()
    result = client.read_purchase_orders(["PO003072"])
    assert result == {
        "PO003072": {
            "status": "On Hold",
            "hold": True,
            "date": "2026-10-21",
            "promised_on": "2026-11-05",
            "total": 66931.41,
            "lines": [EXPECTED_LINE],
        }
    }
    assert len(po_requests()) == 1
    assert httpx.QueryParams(po_requests()[0].url.query)["$filter"] == (
        "OrderNbr eq 'PO003072'"
    )
    one_session()

    # An order the ERP did not return is simply absent.
    map_po({})
    requests_log.clear()
    assert client.read_purchase_orders(["PO003072", "PO999999"]) == {}
    one_session()

    # An empty list returns {} with no request at all.
    requests_log.clear()
    assert client.read_purchase_orders([]) == {}
    assert requests_log == []

    # Redirects are never followed: a 307 away is a failure, not a hop.
    canned["po"] = lambda request: httpx.Response(
        307, headers={"Location": "https://elsewhere.example.com/Instance"}
    )
    requests_log.clear()
    with pytest.raises(AcumaticaError, match="HTTP 307"):
        client.read_purchase_orders(["PO003072"])
    assert all(r.url.host == "acumatica.example.com" for r in requests_log)

    # Hostile numbers never 500: a quote is doubled in the OData
    # literal, and bad argument shapes fail closed before any request.
    map_po({})
    requests_log.clear()
    assert client.read_purchase_orders(["PO'003072"]) == {}
    assert httpx.QueryParams(po_requests()[0].url.query)["$filter"] == (
        "OrderNbr eq 'PO''003072'"
    )
    requests_log.clear()
    for hostile in ("PO003072", ["PO003072", 123], ["PO003072", ""]):
        with pytest.raises(ValueError):
            client.read_purchase_orders(hostile)
    assert requests_log == []

    # ------------------------------------------------------------------
    # AC-2: chunking — 30 numbers make exactly two GETs (25 + 5) in
    # one session
    # ------------------------------------------------------------------
    nbrs = [f"PO{n:06d}" for n in range(3072, 3102)]
    map_po({nbr: po_body(nbr) for nbr in nbrs})
    requests_log.clear()
    result = client.read_purchase_orders(nbrs)
    assert set(result) == set(nbrs)
    assert result["PO003072"]["total"] == 66931.41
    assert len(po_requests()) == 2
    first_chunk, second_chunk = (_filter_nbrs(r) for r in po_requests())
    assert first_chunk == nbrs[:25]
    assert second_chunk == nbrs[25:]
    assert [(r.method, r.url.path) for r in requests_log] == [
        ("POST", LOGIN_PATH),
        ("GET", PO_PATH),
        ("GET", PO_PATH),
        ("POST", LOGOUT_PATH),
    ]

    # ------------------------------------------------------------------
    # AC-2: classification, as of 2026-11-10, with variants of the body
    # ------------------------------------------------------------------
    def run_tracking(rows: list[dict], mapping: dict[str, dict]) -> dict:
        monkeypatch.setattr(tracking, "_load_release_rows", lambda: rows)
        map_po(mapping)
        requests_log.clear()
        return tracking.release_tracking(today=TODAY)

    # hold true -> awaiting_approval with days_waiting 20
    result = run_tracking(
        [release_row("PO003072", row_id=1, created_at="2026-10-21T09:00:00+00:00")],
        {"PO003072": PO003072},
    )
    assert result["as_of"] == "2026-11-10"
    assert result["counts"] == {
        "awaiting_approval": 1,
        "late": 0,
        "partly_received": 0,
        "received": 0,
    }
    [order] = result["orders"]
    assert order == {
        "order_nbr": "PO003072",
        "state": "awaiting_approval",
        "month": "2026-10",
        "site_code": "WHOLESALE",
        "supplier_code": "AACOMPUT",
        "warehouse_code": "WHOLESALE",
        "vendor_code": "AACOMPUT",
        "released_at": "2026-10-21T09:00:00+00:00",
        "actor": "admin",
        "value": Decimal("66931.41"),
        "status": "On Hold",
        "hold": True,
        "promised_on": "2026-11-05",
        "days_waiting": 20,  # 2026-11-10 minus the ERP date 2026-10-21
        "days_late": None,
        "lines": [EXPECTED_LINE],
        "expected_on": "2026-11-07",
        "promise_is_default": False,
    }

    # hold false, promised 2026-11-07, nothing received -> late, days_late 3
    result = run_tracking(
        [release_row("PO003073", row_id=2, created_at="2026-10-22T09:00:00+00:00")],
        {"PO003073": po_body("PO003073", hold=False)},
    )
    [order] = result["orders"]
    assert order["state"] == "late"
    assert order["days_late"] == 3  # 2026-11-10 minus promised 2026-11-07
    assert order["days_waiting"] is None
    assert result["counts"] == {
        "awaiting_approval": 0,
        "late": 1,
        "partly_received": 0,
        "received": 0,
    }

    # 100 of 273 received, promised in the future -> partially_received
    result = run_tracking(
        [release_row("PO003074", row_id=3, created_at="2026-10-22T09:00:00+00:00")],
        {
            "PO003074": po_body(
                "PO003074",
                hold=False,
                line=po_line(received=100.0, promised="2026-12-01T00:00:00"),
            )
        },
    )
    [order] = result["orders"]
    assert order["state"] == "partially_received"
    assert order["days_waiting"] is None
    assert order["days_late"] is None
    assert order["lines"][0]["received_qty"] == 100.0
    assert result["counts"]["partly_received"] == 1

    # the line completed -> received; days_waiting stays non-null while
    # the order is on hold, regardless of state (AC-5)
    result = run_tracking(
        [release_row("PO003075", row_id=4, created_at="2026-10-22T09:00:00+00:00")],
        {"PO003075": po_body("PO003075", line=po_line(completed=True, received=273.0))},
    )
    [order] = result["orders"]
    assert order["state"] == "received"
    assert order["days_waiting"] == 20
    assert order["days_late"] is None
    assert result["counts"] == {
        "awaiting_approval": 0,
        "late": 0,
        "partly_received": 0,
        "received": 1,
    }
    # fully received by quantity, off hold -> received, days_waiting null
    result = run_tracking(
        [release_row("PO003075", row_id=4, created_at="2026-10-22T09:00:00+00:00")],
        {
            "PO003075": po_body(
                "PO003075",
                hold=False,
                line=po_line(received=273.0, promised="2026-11-07T00:00:00"),
            )
        },
    )
    [order] = result["orders"]
    assert order["state"] == "received"
    assert order["days_waiting"] is None

    # status Canceled -> cancelled (as does every line cancelled)
    result = run_tracking(
        [release_row("PO003076", row_id=5, created_at="2026-10-22T09:00:00+00:00")],
        {"PO003076": po_body("PO003076", status="Canceled")},
    )
    assert result["orders"][0]["state"] == "cancelled"
    result = run_tracking(
        [release_row("PO003076", row_id=5, created_at="2026-10-22T09:00:00+00:00")],
        {"PO003076": po_body("PO003076", hold=False, line=po_line(cancelled=True))},
    )
    assert result["orders"][0]["state"] == "cancelled"

    # an order the ERP did not return -> missing
    result = run_tracking(
        [release_row("PO003077", row_id=6, created_at="2026-10-22T09:00:00+00:00")],
        {},
    )
    [order] = result["orders"]
    assert order["state"] == "missing"
    assert order["status"] is None
    assert order["hold"] is None
    assert order["promised_on"] is None
    assert order["lines"] == []
    assert order["days_waiting"] is None
    assert order["days_late"] is None

    # the newest row per order number wins; pending / error / number-less
    # rows are not tracked and never reach the ERP
    result = run_tracking(
        [
            release_row(
                "PO003078",
                row_id=8,
                created_at="2026-10-25T09:00:00+00:00",
                month="2026-10",
            ),
            release_row(
                "PO003078",
                row_id=7,
                created_at="2026-09-25T09:00:00+00:00",
                month="2026-09",
                outcome="exists",
            ),
            release_row(
                "PO003999",
                row_id=9,
                created_at="2026-10-26T09:00:00+00:00",
                outcome="error",
            ),
            release_row(
                "PO003999",
                row_id=10,
                created_at="2026-10-26T09:00:00+00:00",
                outcome="pending",
            ),
            release_row(
                "PO003999",
                row_id=11,
                created_at="2026-10-26T09:00:00+00:00",
                order_nbr=None,
            ),
        ],
        {
            "PO003078": po_body(
                "PO003078",
                hold=False,
                line=po_line(promised="2026-12-01T00:00:00"),
            )
        },
    )
    [order] = result["orders"]
    assert order["order_nbr"] == "PO003078"
    assert order["month"] == "2026-10"  # the newest row's identity
    assert order["released_at"] == "2026-10-25T09:00:00+00:00"
    assert len(po_requests()) == 1
    assert httpx.QueryParams(po_requests()[0].url.query)["$filter"] == (
        "OrderNbr eq 'PO003078'"
    )

    # ------------------------------------------------------------------
    # AC-2: sorting by state priority, then oldest release first, and
    # the fixed counts keys
    # ------------------------------------------------------------------
    rows = [
        release_row("PO003101", row_id=1, created_at="2026-10-05T09:00:00+00:00"),
        release_row("PO003102", row_id=2, created_at="2026-10-20T09:00:00+00:00"),
        release_row("PO003103", row_id=3, created_at="2026-10-10T09:00:00+00:00"),
        release_row("PO003104", row_id=4, created_at="2026-10-11T09:00:00+00:00"),
        release_row("PO003105", row_id=5, created_at="2026-10-12T09:00:00+00:00"),
        release_row("PO003106", row_id=6, created_at="2026-10-13T09:00:00+00:00"),
        release_row("PO003107", row_id=7, created_at="2026-10-14T09:00:00+00:00"),
        release_row("PO003108", row_id=8, created_at="2026-10-15T09:00:00+00:00"),
    ]
    mapping = {
        "PO003101": po_body(
            "PO003101", hold=False, line=po_line(promised="2026-11-01T00:00:00")
        ),
        "PO003102": po_body(
            "PO003102", hold=False, line=po_line(promised="2026-11-04T00:00:00")
        ),
        "PO003103": po_body("PO003103"),
        "PO003104": po_body(
            "PO003104",
            hold=False,
            line=po_line(received=50.0, promised="2026-12-01T00:00:00"),
        ),
        "PO003105": po_body(
            "PO003105", hold=False, line=po_line(promised="2026-12-01T00:00:00")
        ),
        "PO003106": po_body("PO003106", line=po_line(completed=True, received=273.0)),
        "PO003107": po_body("PO003107", status="Canceled"),
    }
    result = run_tracking(rows, mapping)
    assert [order["order_nbr"] for order in result["orders"]] == [
        "PO003101",  # late, oldest release first within the state
        "PO003102",  # late
        "PO003103",  # awaiting_approval
        "PO003104",  # partially_received
        "PO003105",  # open
        "PO003106",  # received
        "PO003107",  # cancelled
        "PO003108",  # missing (not returned by the ERP)
    ]
    assert [order["state"] for order in result["orders"]] == [
        "late",
        "late",
        "awaiting_approval",
        "partially_received",
        "open",
        "received",
        "cancelled",
        "missing",
    ]
    assert result["counts"] == {
        "awaiting_approval": 1,
        "late": 2,
        "partly_received": 1,
        "received": 1,
    }
    assert set(result["counts"]) == {
        "awaiting_approval",
        "late",
        "partly_received",
        "received",
    }

    # ------------------------------------------------------------------
    # AC-1 / AC-2 / AC-3 / AC-4 / AC-5 / AC-6: the route
    # ------------------------------------------------------------------
    from fastapi.testclient import TestClient

    from app.main import app

    dev_token = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")
    viewer_token = f"{dev_token}:user"
    signed_headers = {"Authorization": f"Bearer {dev_token}"}
    single_rows = [
        release_row("PO003072", row_id=1, created_at="2026-10-21T09:00:00+00:00")
    ]

    # The test Bearer gets the payload; days_waiting / days_late are
    # present as null where not applicable (AC-5).
    monkeypatch.setattr(tracking, "_load_release_rows", lambda: single_rows)
    map_po({"PO003072": PO003072})
    with TestClient(app, headers=signed_headers) as signed:
        response = signed.get("/api/aggregate/releases")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["as_of"] == "2026-11-10"
        [order] = body["orders"]
        assert order["order_nbr"] == "PO003072"
        assert order["state"] == "awaiting_approval"
        assert order["days_waiting"] == 20
        assert order["days_late"] is None
        assert order["value"] == "66931.41"
        assert set(order) == {
            "order_nbr",
            "state",
            "month",
            "site_code",
            "supplier_code",
            "warehouse_code",
            "vendor_code",
            "released_at",
            "actor",
            "value",
            "status",
            "hold",
            "promised_on",
            "days_waiting",
            "days_late",
            "lines",
            "expected_on",
            "promise_is_default",
        }
        assert body["counts"] == {
            "awaiting_approval": 1,
            "late": 0,
            "partly_received": 0,
            "received": 0,
        }
        assert set(body["counts"]) == {
            "awaiting_approval",
            "late",
            "partly_received",
            "received",
        }

    # Unauthenticated gets 401.
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/aggregate/releases").status_code == 401

    # Incomplete configuration gives 503 (rows exist).
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    with TestClient(app, headers=signed_headers) as signed:
        response = signed.get("/api/aggregate/releases")
        assert response.status_code == 503
        assert response.json()["detail"] == "Acumatica configuration is incomplete"

    # AC-4: with no released rows the empty state takes precedence over
    # the 503 configuration error.
    monkeypatch.setattr(tracking, "_load_release_rows", lambda: [])
    with TestClient(app, headers=signed_headers) as signed:
        response = signed.get("/api/aggregate/releases")
        assert response.status_code == 200
        assert response.json() == {
            "as_of": "2026-11-10",
            "orders": [],
            "counts": {
                "awaiting_approval": 0,
                "late": 0,
                "partly_received": 0,
                "received": 0,
            },
        }
    monkeypatch.setenv("ACUMATICA_BASE_URL", BASE_URL)
    monkeypatch.setenv("ACUMATICA_TENANT", TENANT)
    monkeypatch.setenv("ACUMATICA_USERNAME", USERNAME)
    monkeypatch.setenv("ACUMATICA_PASSWORD", PASSWORD)

    # No released rows (configuration complete): 200 empty, no client
    # call of any kind.
    monkeypatch.setattr(tracking, "_load_release_rows", lambda: [])
    requests_log.clear()
    with TestClient(app, headers=signed_headers) as signed:
        response = signed.get("/api/aggregate/releases")
        assert response.status_code == 200
        assert response.json() == {
            "as_of": "2026-11-10",
            "orders": [],
            "counts": {
                "awaiting_approval": 0,
                "late": 0,
                "partly_received": 0,
                "received": 0,
            },
        }
    assert requests_log == []

    # A client auth failure gives 502.
    monkeypatch.setattr(tracking, "_load_release_rows", lambda: single_rows)
    map_po({"PO003072": PO003072})
    canned["login_status"] = 401
    with TestClient(app, headers=signed_headers) as signed:
        response = signed.get("/api/aggregate/releases")
        assert response.status_code == 502
        assert "rejected with HTTP 401" in response.json()["detail"]
    canned["login_status"] = 204

    # AC-3 / AC-6: a failed second chunk — no partial results merged.
    chunk_rows = [
        release_row(
            nbr,
            row_id=index + 1,
            created_at=f"2026-10-{(index % 28) + 1:02d}T09:00:00+00:00",
        )
        for index, nbr in enumerate(nbrs)
    ]
    monkeypatch.setattr(tracking, "_load_release_rows", lambda: chunk_rows)

    def flaky_po(request: httpx.Request) -> httpx.Response:
        chunk = _filter_nbrs(request)
        if "PO003097" in chunk:  # the second chunk
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=[po_body(nbr) for nbr in chunk])

    canned["po"] = flaky_po
    requests_log.clear()
    with pytest.raises(AcumaticaError, match="HTTP 500"):
        client.read_purchase_orders(nbrs)  # never the first chunk's answer
    with TestClient(app, headers=signed_headers) as signed:
        response = signed.get("/api/aggregate/releases")
        assert response.status_code == 502
        payload = response.json()
        assert set(payload) == {"detail"}
        assert "orders" not in payload
        assert "counts" not in payload
        assert "as_of" not in payload

    # ------------------------------------------------------------------
    # Identity: the route is open to any signed-in user. A NON-ADMIN
    # (role "user") gets the payload — no admin-only control on the
    # route — while the admin-only audit route still refuses them, and
    # the gate still answers 401 to unauthenticated traffic.
    # ------------------------------------------------------------------
    monkeypatch.setattr(tracking, "_load_release_rows", lambda: single_rows)
    map_po({"PO003072": PO003072})
    with TestClient(app, headers={"Authorization": f"Bearer {viewer_token}"}) as viewer:
        me = viewer.get("/api/auth/me")
        assert me.status_code == 200, me.text
        user = me.json()["user"]
        assert user["roles"] == ["user"]
        assert "admin" not in user["roles"]
        response = viewer.get("/api/aggregate/releases")
        assert response.status_code == 200, response.text
        assert response.json()["orders"][0]["state"] == "awaiting_approval"
        assert response.json()["orders"][0]["days_waiting"] == 20
        # The admin-only control is intact where it belongs.
        assert viewer.get("/api/aggregate/release/audit").status_code == 403
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/aggregate/releases").status_code == 401
