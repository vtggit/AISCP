"""Issue #1621 proof: one shared NUL-byte check for the routers.

``app/api/validation.py`` defines, exactly once, the guard the eleven
routers — demand_events, demand_stats, drift, forecast, item_sites,
lead_time_observations, lead_time_stats, on_hand, recommendations,
scenarios and supplier_terms — each carried privately as ``_NUL``
plus a module-local ``_reject_nul_bytes``.  The shared check is
``reject_nul_bytes(*values, detail)``: when any of the values that is
not None contains a NUL byte it raises FastAPI's ``HTTPException``
with status 422 and the given ``detail``, and otherwise it returns
None.

Proved:

* the check lives in ``app/api/validation.py`` and its ``detail`` is
  a keyword-only argument beside a ``*values``; it returns None with
  no values, with only None values, and with clean values, and it
  raises an ``HTTPException`` of status 422 carrying exactly the
  detail it was given — whichever value holds the NUL byte, and
  whichever detail string the caller passed;
* none of the eleven routers defines ``_NUL`` or
  ``_reject_nul_bytes`` any more, and each holds the shared
  ``reject_nul_bytes`` from ``app.api.validation`` (the same function
  object it now calls);
* through the app's test client, a NUL byte in a filter answers 422
  with the same detail text the router used before its private copy
  was removed — one case per router, the scenarios case twice, once
  in the path and once in the JSON body.
"""

import importlib
import inspect

import pytest
from fastapi import HTTPException

import app.api.validation as validation

ROUTER_MODULES = (
    "demand_events",
    "demand_stats",
    "drift",
    "forecast",
    "item_sites",
    "lead_time_observations",
    "lead_time_stats",
    "on_hand",
    "recommendations",
    "scenarios",
    "supplier_terms",
)

# (route, query params, the detail text the router always used)
NUL_CASES = (
    (
        "/api/demand-events",
        {"item_code": "a\x00b"},
        "demand event filters must not contain NUL bytes",
    ),
    (
        "/api/demand-stats",
        {"item_code": "a\x00b"},
        "demand stats filters must not contain NUL bytes",
    ),
    (
        "/api/drift/by-key",
        {"item_code": "a\x00b", "site_code": "159"},
        "drift filters must not contain NUL bytes",
    ),
    (
        "/api/forecast/by-key",
        {"item_code": "a\x00b", "site_code": "159"},
        "forecast filters must not contain NUL bytes",
    ),
    (
        "/api/item-sites",
        {"item_code": "a\x00b"},
        "item_code and site_code must not contain NUL bytes",
    ),
    (
        "/api/lead-time-observations",
        {"item_code": "a\x00b"},
        "lead time observation filters must not contain NUL bytes",
    ),
    (
        "/api/lead-time-stats",
        {"item_code": "a\x00b"},
        "lead time stats filters must not contain NUL bytes",
    ),
    (
        "/api/on-hand",
        {"item_code": "a\x00b"},
        "on-hand filters must not contain NUL bytes",
    ),
    (
        "/api/recommendations",
        {"item_code": "a\x00b"},
        "recommendation filters must not contain NUL bytes",
    ),
    (
        "/api/supplier-terms",
        {"item_code": "a\x00b"},
        "supplier terms filters must not contain NUL bytes",
    ),
)


def _assert_rejects(*values, detail):
    """The check raises FastAPI's 422 HTTPException carrying ``detail``."""
    with pytest.raises(HTTPException) as excinfo:
        validation.reject_nul_bytes(*values, detail=detail)
    assert excinfo.type is HTTPException
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == detail


def test_issue1621_freeform(client):
    # --------------------------------------------------------------
    # AC-1: the check lives once, in app/api/validation.py, as
    # reject_nul_bytes(*values, detail).
    # --------------------------------------------------------------
    assert validation.__file__.replace("\\", "/").endswith("app/api/validation.py")
    signature = inspect.signature(validation.reject_nul_bytes)
    kinds = [p.kind for p in signature.parameters.values()]
    assert inspect.Parameter.VAR_POSITIONAL in kinds
    assert signature.parameters["detail"].kind is (inspect.Parameter.KEYWORD_ONLY)

    # No values at all, only None values, or clean values: None.
    assert validation.reject_nul_bytes(detail="nobody's home") is None
    assert validation.reject_nul_bytes(None, None, detail="nobody's home") is None
    assert (
        validation.reject_nul_bytes("abc", None, "x\ny", detail="nobody's home") is None
    )

    # A NUL byte in any non-None value: 422 with the given detail,
    # whatever the detail and whichever value holds the byte.
    _assert_rejects("a\x00b", detail="first")
    _assert_rejects(None, "clean", "x\x00y", detail="second")
    _assert_rejects("\x00", detail="the byte alone")

    # --------------------------------------------------------------
    # AC-2: the eleven routers define the check nowhere any more and
    # call the shared one from app.api.validation.
    # --------------------------------------------------------------
    for name in ROUTER_MODULES:
        module = importlib.import_module(f"app.api.{name}")
        assert not hasattr(module, "_NUL"), name
        assert not hasattr(module, "_reject_nul_bytes"), name
        assert getattr(module, "reject_nul_bytes") is validation.reject_nul_bytes, name

    # --------------------------------------------------------------
    # Every route answers a NUL byte exactly as before: 422 with the
    # detail text the router always used.
    # --------------------------------------------------------------
    for route, params, detail in NUL_CASES:
        response = client.get(route, params=params)
        assert response.status_code == 422, (route, response.text)
        assert response.json() == {"detail": detail}, (route, response.text)

    # The scenarios guard its path parameter ...
    response = client.get("/api/scenarios/a%00b")
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "scenarios must not contain NUL bytes"}
    # ... and the name in the JSON body, the way POST /api/scenarios
    # guards it.
    response = client.post("/api/scenarios", json={"name": "a\x00b"})
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "scenarios must not contain NUL bytes"}
