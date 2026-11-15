"""Issue #716 proof: writing item-warehouse parameters, with no network.

``app.feed.acumatica_client`` is structured like ``odoo_client``: the
per-call ``httpx.Client`` comes from the module-level ``_build_client``
(which this test replaces with a factory over an ``httpx.MockTransport``
that records every request and serves canned Acumatica answers — login
204; the ``ItemWarehouse`` row read from a small stateful mock (the
canned row CONBOTTLE1/WHOLESALE at ReorderPoint 25, MaxQty 100, all
override flags off — a flags PUT switches the flags on, a values PUT
writes the values, unless the mock is told to ignore either); logout
204).

Proved here, in order:

  * with ``ACUMATICA_ALLOW_WRITES`` off (unset, or ``false``),
    ``write_item_warehouse_parameters`` raises
    ``AcumaticaWritesDisabledError`` and makes zero requests;
  * with writes on, hostile inputs raise ``ValueError`` with zero
    requests: max below reorder point, a negative value, NaN, an
    infinity, a huge finite int, a bool, and an empty or non-string
    code;
  * an already-equal row (and a null-valued row, treated as 0.0 for the
    before state and the unchanged comparison regardless of the
    override flag state) returns outcome ``unchanged`` with exactly
    login, GET, logout and no PUT;
  * a real change makes exactly login, GET, PUT (flags only, no
    values), PUT (values only, no flags), GET, logout, in that order —
    every one of the GET, both PUTs and the re-read going to
    ``/entity/Default/<version>/ItemWarehouse`` (the REST contract
    endpoint, never the ``odatav4`` feed) with the item/site
    ``$filter`` (quotes doubled the way ``_odata_escape`` does) and
    ``$top=1`` — and returns outcome ``written`` with the right before
    and after;
  * a read-back that does not take (the mock ignores the values, or
    ignores the flags) raises ``AcumaticaWriteVerificationError``
    naming the item, the site and the field that did not take, with
    logout still sent;
  * a row-less answer raises ``AcumaticaError`` naming the item and
    the site, with logout sent;
  * a non-2xx on the write is handled the way the existing write paths
    handle it (status in, at most 300 characters of the body, never
    the password; a 401 is ``AcumaticaAuthError``), with logout still
    sent;
  * the identity requirement: the proof also runs as a NON-ADMIN (role
    ``user``) in a second pass after a full reload — the page boots the
    production way (the token it would take from the URL hash as
    ``#access_token=...``, the bare development token, with
    ``/api/auth/config`` answering publicly and ``/api/auth/me``
    resolving the viewer for that user), the admin-only control is
    absent — this issue adds no routes at all, so the API surface
    exposes no Acumatica operation (admin or otherwise) and no route
    module calls the new method — while the page still renders: the
    settings the page loads answer 200 for the viewer and
    unauthenticated ``/api`` traffic is still 401.
"""

import json
import os
import pathlib

import httpx
import pytest

import app.feed.acumatica_client as acumatica_client
from app.feed.acumatica_client import (
    AcumaticaAuthError,
    AcumaticaClient,
    AcumaticaError,
    AcumaticaWritesDisabledError,
    AcumaticaWriteVerificationError,
)

BASE_URL = "https://acumatica.example.com/Instance"
TENANT = "AIERP"
USERNAME = "svc-aierp"
PASSWORD = "s3cret-password!"
VERSION = "25.200.001"

LOGIN_PATH = "/Instance/entity/auth/login"
LOGOUT_PATH = "/Instance/entity/auth/logout"
ITEM_WAREHOUSE_PATH = f"/Instance/entity/Default/{VERSION}/ItemWarehouse"

ITEM_CODE = "CONBOTTLE1"
SITE_CODE = "WHOLESALE"

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


_ROW_UNSET = object()


def make_row(
    *,
    reorder_point: float | None = 25.0,
    max_qty: float | None = 100.0,
    override_replenishment: bool = False,
    override_reorder_point: bool = False,
    override_max_qty: bool = False,
) -> dict:
    """One enveloped ItemWarehouse row for the mock transport."""
    return {
        "InventoryID": {"value": ITEM_CODE},
        "WarehouseID": {"value": SITE_CODE},
        "ReorderPoint": {"value": reorder_point},
        "MaxQty": {"value": max_qty},
        "OverrideReplenishmentSettings": {"value": override_replenishment},
        "OverrideReorderPoint": {"value": override_reorder_point},
        "OverrideMaxQty": {"value": override_max_qty},
    }


def test_issue716_freeform(monkeypatch) -> None:
    # Isolate the Acumatica environment.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", BASE_URL)
    monkeypatch.setenv("ACUMATICA_TENANT", TENANT)
    monkeypatch.setenv("ACUMATICA_USERNAME", USERNAME)
    monkeypatch.setenv("ACUMATICA_PASSWORD", PASSWORD)

    requests_log: list[httpx.Request] = []
    state = {
        "row": make_row(),
        "put_values": True,
        "put_flags": True,
        "get_status": None,
        "put_status": None,
        "put_body": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(request)
        path = request.url.path
        if request.method == "POST" and path == LOGIN_PATH:
            return httpx.Response(204)
        if request.method == "POST" and path == LOGOUT_PATH:
            return httpx.Response(204)
        if path == ITEM_WAREHOUSE_PATH:
            if request.method == "GET":
                if state["get_status"] is not None:
                    return httpx.Response(state["get_status"], text="read refused")
                if state["row"] is None:
                    return httpx.Response(200, json={"value": []})
                return httpx.Response(200, json={"value": [state["row"]]})
            if request.method == "PUT":
                if state["put_status"] is not None:
                    return httpx.Response(state["put_status"], text=state["put_body"])
                body = json.loads(request.content)
                if "OverrideReorderPoint" in body and state["put_flags"]:
                    state["row"]["OverrideReplenishmentSettings"] = {"value": True}
                    state["row"]["OverrideReorderPoint"] = {"value": True}
                    state["row"]["OverrideMaxQty"] = {"value": True}
                if "ReorderPoint" in body and state["put_values"]:
                    state["row"]["ReorderPoint"] = {
                        "value": body["ReorderPoint"]["value"]
                    }
                    state["row"]["MaxQty"] = {"value": body["MaxQty"]["value"]}
                return httpx.Response(200, json={})
        return httpx.Response(404, text="unexpected request")

    # _build_client is replaced: one httpx.Client over a recording
    # httpx.MockTransport, per call.
    def fake_build_client(ca_bundle, timeout):
        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(acumatica_client, "_build_client", fake_build_client)
    client = AcumaticaClient()

    def recorded() -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in requests_log]

    def logged_out() -> bool:
        return (
            requests_log
            and requests_log[-1].method == "POST"
            and requests_log[-1].url.path == LOGOUT_PATH
        )

    def write(**overrides) -> dict:
        kwargs = dict(
            item_code=ITEM_CODE,
            site_code=SITE_CODE,
            reorder_point=30.0,
            max_qty=150.0,
        )
        kwargs.update(overrides)
        return client.write_item_warehouse_parameters(**kwargs)

    def reset(row: object = _ROW_UNSET, **flags) -> None:
        state["row"] = make_row() if row is _ROW_UNSET else row
        state["put_values"] = True
        state["put_flags"] = True
        state["get_status"] = None
        state["put_status"] = None
        state["put_body"] = None
        state.update(flags)

    # -- 1. writes off: disabled error, zero requests ---------------------
    requests_log.clear()
    with pytest.raises(AcumaticaWritesDisabledError):
        write()
    assert requests_log == []
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "false")  # truthy only
    requests_log.clear()
    with pytest.raises(AcumaticaWritesDisabledError):
        write()
    assert requests_log == []
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")

    # -- 2. hostile inputs: ValueError before any request -----------------
    bad_calls = (
        dict(reorder_point=10.0, max_qty=5.0),  # max below reorder point
        dict(reorder_point=-1.0, max_qty=5.0),  # a negative value
        dict(reorder_point=float("nan"), max_qty=5.0),  # NaN
        dict(reorder_point=1.0, max_qty=float("nan")),
        dict(reorder_point=1.0, max_qty=float("inf")),
        dict(reorder_point=10**400, max_qty=5.0),  # a huge finite int
        dict(reorder_point=1.0, max_qty=10**400),
        dict(reorder_point=True, max_qty=5.0),  # a bool is not a number
        dict(reorder_point=1.0, max_qty=True),
        dict(item_code="", site_code=SITE_CODE),  # an empty code
        dict(item_code=ITEM_CODE, site_code=""),
        dict(item_code=None, site_code=SITE_CODE),
        dict(item_code=123, site_code=SITE_CODE),
    )
    for overrides in bad_calls:
        requests_log.clear()
        with pytest.raises(ValueError):
            write(**overrides)
        assert requests_log == []

    # -- 3. already equal: unchanged, no PUT -------------------------------
    reset(make_row(reorder_point=30.0, max_qty=150.0))
    requests_log.clear()
    result = write()
    assert result == {
        "outcome": "unchanged",
        "before": {"reorder_point": 30.0, "max_qty": 150.0},
        "after": {"reorder_point": 30.0, "max_qty": 150.0},
    }
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", ITEM_WAREHOUSE_PATH),
        ("POST", LOGOUT_PATH),
    ]
    assert not any(r.method == "PUT" for r in requests_log)

    # A null ReorderPoint/MaxQty is 0.0 for the before state and the
    # unchanged comparison, regardless of the override flag state.
    null_row = make_row(
        reorder_point=None,
        max_qty=None,
        override_reorder_point=True,
        override_max_qty=True,
    )
    reset(null_row)
    requests_log.clear()
    result = write(reorder_point=0, max_qty=0)
    assert result == {
        "outcome": "unchanged",
        "before": {"reorder_point": 0.0, "max_qty": 0.0},
        "after": {"reorder_point": 0.0, "max_qty": 0.0},
    }
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", ITEM_WAREHOUSE_PATH),
        ("POST", LOGOUT_PATH),
    ]
    assert not any(r.method == "PUT" for r in requests_log)

    # -- 4. a real change: the exact sequence, bodies, and result ----------
    reset(make_row(reorder_point=25.0, max_qty=100.0))
    requests_log.clear()
    result = write()
    assert result == {
        "outcome": "written",
        "before": {"reorder_point": 25.0, "max_qty": 100.0},
        "after": {"reorder_point": 30.0, "max_qty": 150.0},
    }
    assert recorded() == [
        ("POST", LOGIN_PATH),
        ("GET", ITEM_WAREHOUSE_PATH),
        ("PUT", ITEM_WAREHOUSE_PATH),
        ("PUT", ITEM_WAREHOUSE_PATH),
        ("GET", ITEM_WAREHOUSE_PATH),
        ("POST", LOGOUT_PATH),
    ]
    # The GET, both PUTs and the re-read each go to the REST contract
    # endpoint /entity/Default/<version>/ItemWarehouse; no request path
    # contains /odatav4/ (the read-only feed cannot be written).
    for request in requests_log[1:5]:
        assert request.url.path == ITEM_WAREHOUSE_PATH
        assert request.url.path.endswith(f"/entity/Default/{VERSION}/ItemWarehouse")
        assert "/odatav4/" not in request.url.path
    assert json.loads(requests_log[0].content) == {
        "name": USERNAME,
        "password": PASSWORD,
        "tenant": TENANT,
    }
    first_get_params = httpx.QueryParams(requests_log[1].url.query)
    assert first_get_params["$filter"] == (
        f"InventoryID eq '{ITEM_CODE}' and WarehouseID eq '{SITE_CODE}'"
    )
    assert first_get_params["$top"] == "1"
    # the re-read is the same request
    assert httpx.QueryParams(requests_log[4].url.query) == first_get_params
    # PUT 1: the override flags only, no values.
    flags_put = json.loads(requests_log[2].content)
    assert flags_put == {
        "InventoryID": {"value": ITEM_CODE},
        "WarehouseID": {"value": SITE_CODE},
        "OverrideReplenishmentSettings": {"value": True},
        "OverrideReorderPoint": {"value": True},
        "OverrideMaxQty": {"value": True},
    }
    assert "ReorderPoint" not in flags_put
    assert "MaxQty" not in flags_put
    # PUT 2: the values only, no flags.
    values_put = json.loads(requests_log[3].content)
    assert values_put == {
        "InventoryID": {"value": ITEM_CODE},
        "WarehouseID": {"value": SITE_CODE},
        "ReorderPoint": {"value": 30.0},
        "MaxQty": {"value": 150.0},
    }
    assert not any(key.startswith("Override") for key in values_put)

    # A hostile item code reaches the wire with its quote doubled.
    reset(make_row(reorder_point=25.0, max_qty=100.0))
    requests_log.clear()
    result = write(item_code="O'NEILL")
    assert result["outcome"] == "written"
    assert httpx.QueryParams(requests_log[1].url.query)["$filter"] == (
        "InventoryID eq 'O''NEILL' and WarehouseID eq 'WHOLESALE'"
    )
    # and both PUTs carry the same code in their payloads.
    for put_index in (2, 3):
        put_body = json.loads(requests_log[put_index].content)
        assert put_body["InventoryID"] == {"value": "O'NEILL"}
        assert put_body["WarehouseID"] == {"value": SITE_CODE}

    # -- 5. a read-back that does not take: verification error -------------
    # The mock ignores the values: the read-back still carries the old
    # values, so the write is refused even though both PUTs were 2xx.
    reset(make_row(reorder_point=25.0, max_qty=100.0), put_values=False)
    requests_log.clear()
    with pytest.raises(AcumaticaWriteVerificationError) as excinfo:
        write()
    message = str(excinfo.value)
    assert ITEM_CODE in message
    assert SITE_CODE in message
    assert "ReorderPoint" in message
    assert "MaxQty" in message
    assert PASSWORD not in message
    assert logged_out()
    # ... and the mock ignores the flags: the read-back carries the new
    # values but the override flags did not take.
    reset(make_row(reorder_point=25.0, max_qty=100.0), put_flags=False)
    requests_log.clear()
    with pytest.raises(AcumaticaWriteVerificationError) as excinfo:
        write()
    message = str(excinfo.value)
    assert ITEM_CODE in message
    assert SITE_CODE in message
    assert "OverrideReorderPoint" in message
    assert "OverrideMaxQty" in message
    assert logged_out()

    # -- 6. no row: the explicit error, logout sent ------------------------
    reset(None)
    requests_log.clear()
    with pytest.raises(AcumaticaError) as excinfo:
        write()
    message = str(excinfo.value)
    assert ITEM_CODE in message
    assert SITE_CODE in message
    assert not isinstance(excinfo.value, AcumaticaWriteVerificationError)
    assert [r.method for r in requests_log] == ["POST", "GET", "POST"]
    assert logged_out()

    # -- 7. non-2xx on the write: as the existing write paths --------------
    # A 500 on the first PUT: the status and at most 300 characters of the
    # body, never the password, and logout is still sent.
    reset(make_row(reorder_point=25.0, max_qty=100.0))
    state["put_status"] = 500
    state["put_body"] = ("boom " * 100) + PASSWORD + " tail-marker"
    requests_log.clear()
    with pytest.raises(AcumaticaError) as excinfo:
        write()
    message = str(excinfo.value)
    assert "500" in message
    assert PASSWORD not in message
    assert USERNAME not in message
    assert len(message.split("HTTP 500: ")[1]) <= 300  # body capped at 300
    assert logged_out()
    # A 401 mid-session is an auth failure, logout still sent.
    reset(make_row(reorder_point=25.0, max_qty=100.0))
    state["get_status"] = 401
    requests_log.clear()
    with pytest.raises(AcumaticaAuthError) as excinfo:
        write()
    assert "401" in str(excinfo.value)
    assert logged_out()

    # -- 8. Identity: the proof also runs as a NON-ADMIN -------------------
    # Second pass after a full reload: the page boots the production way —
    # the token it would take from the URL hash as #access_token=... (the
    # bare development token), with /api/auth/config answering publicly
    # and /api/auth/me resolving (dev-mode auth standing in for the IdP)
    # the non-admin viewer for that user.
    from fastapi.testclient import TestClient

    import app.api as api_package
    from app.main import app

    dev_token = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")
    viewer = TestClient(app, headers={"Authorization": f"Bearer {dev_token}"})
    anon = TestClient(app)
    assert anon.get("/api/auth/config").status_code == 200  # public, pre-token
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == "dev-user-1"
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]
    # The admin-only control is absent: this issue adds no routes at all —
    # the writeback is a client method only, so the app's API surface
    # exposes no Acumatica operation (admin or otherwise); the confirm
    # route of #719 is the method's only caller now, so exactly one
    # route module mentions it — parameter_writeback.py.
    paths = app.openapi()["paths"]
    assert not any("acumatica" in name.lower() for name in paths)
    route_files = sorted(
        path for path in pathlib.Path(api_package.__file__).parent.glob("*.py")
    )
    assert route_files
    assert [
        path.name
        for path in route_files
        if "write_item_warehouse_parameters" in path.read_text()
    ] == ["parameter_writeback.py"]
    # The page still renders: the settings the page loads answer 200 for
    # the viewer, and the gate is intact — unauthenticated /api traffic is
    # still 401.
    assert viewer.get("/api/settings").status_code == 200
    assert anon.get("/api/settings").status_code == 401
