"""Issue #1642 proof: one shared storable-subject check for the routers.

``app/api/validation.py`` defines, exactly once, the check the five
routers — stockout_decisions, excess_decisions, planner_decisions,
empirical_adoptions and ddmrp — each carried privately as a
``_DecidedBy`` / ``_AdoptedBy`` / ``_DesignatedBy`` model plus a
``_require_storable_*`` function: the token subject is stored in a
String(255) column, so it must fit and may hold no NUL byte.  The
shared check is ``require_storable_subject(user)``: it validates
``user.sub`` through one private model whose ``value`` field is at
most 255 characters and whose model validator raises
``ValueError("must not contain NUL bytes")`` for a value holding a
NUL byte; on a validation error it raises
``RequestValidationError`` from the pydantic error, otherwise it
returns None.

Proved:

* with a 255-character subject the check returns None; with 256
  characters it raises ``RequestValidationError`` whose single error
  has ``loc`` ``("value",)`` and the message "String should have at
  most 255 characters"; with a NUL byte the single error's message
  is "Value error, must not contain NUL bytes";
* a source scan of ``app/api`` finds none of the five private models
  or functions, and each of the five modules imports
  ``require_storable_subject`` from ``app.api.validation``;
* through ``POST /api/drift/decisions`` with the route's admin
  dependency overridden to answer a user whose subject holds a NUL
  byte, the answer is 422 with that error and no decision is stored.
"""

import importlib
from pathlib import Path

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

import app.api.validation as validation
from app.api import planner_decisions
from app.auth.models import AuthUser
from app.db.connection import get_cursor
from app.main import app

API_DIR = Path(__file__).resolve().parents[1] / "app" / "api"

FIVE_MODULES = (
    "stockout_decisions",
    "excess_decisions",
    "planner_decisions",
    "empirical_adoptions",
    "ddmrp",
)

GONE_NAMES = (
    "_DecidedBy",
    "_AdoptedBy",
    "_DesignatedBy",
    "_require_storable_decided_by",
    "_require_storable_adopted_by",
    "_require_storable_designated_by",
)


def _subject_errors(user: AuthUser) -> list[dict]:
    """The check raises RequestValidationError; its single error."""
    with pytest.raises(RequestValidationError) as excinfo:
        validation.require_storable_subject(user)
    errors = excinfo.value.errors()
    assert len(errors) == 1, errors
    return errors


def _drift_post_admin_dependency():
    """The require_role("admin") closure of POST /api/drift/decisions."""
    route = next(
        candidate
        for candidate in planner_decisions.router.routes
        if isinstance(candidate, APIRoute)
        and candidate.path == "/api/drift/decisions"
        and "POST" in candidate.methods
    )
    return next(
        dependant.call
        for dependant in route.dependant.dependencies
        if dependant.call.__module__ == "app.auth.authorization"
    )


def test_issue1642_freeform(client) -> None:
    # --------------------------------------------------------------
    # AC-1: require_storable_subject, called directly.
    # --------------------------------------------------------------
    assert validation.require_storable_subject(AuthUser(sub="a" * 255)) is None

    errors = _subject_errors(AuthUser(sub="a" * 256))
    assert errors[0]["loc"] == ("value",)
    assert errors[0]["msg"] == "String should have at most 255 characters"

    errors = _subject_errors(AuthUser(sub="subject\x00byte"))
    assert errors[0]["msg"] == "Value error, must not contain NUL bytes"

    # --------------------------------------------------------------
    # AC-2: the five routers carry the check nowhere any more and
    # import the shared one from app.api.validation.
    # --------------------------------------------------------------
    for path in sorted(API_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for name in GONE_NAMES:
            assert name not in source, (path.name, name)

    for module_name in FIVE_MODULES:
        module = importlib.import_module(f"app.api.{module_name}")
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert (
            "from app.api.validation import require_storable_subject" in source
        ), module_name
        assert (
            getattr(module, "require_storable_subject")
            is validation.require_storable_subject
        ), module_name

    # --------------------------------------------------------------
    # Through POST /api/drift/decisions: the route's admin dependency
    # answers a user whose subject holds a NUL byte, and the answer
    # is 422 with that error — no decision is stored.
    # --------------------------------------------------------------
    admin_check = _drift_post_admin_dependency()
    app.dependency_overrides[admin_check] = lambda: AuthUser(
        sub="subject\x00byte", roles=["admin"]
    )
    try:
        response = client.post(
            "/api/drift/decisions",
            json={"item_code": "I-1642", "site_code": "159", "decision": "accepted"},
        )
    finally:
        app.dependency_overrides.pop(admin_check, None)
    assert admin_check not in app.dependency_overrides

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, list) and len(detail) == 1, response.text
    assert detail[0]["msg"] == "Value error, must not contain NUL bytes"

    with get_cursor() as cur:
        cur.execute("SELECT count(*) AS total FROM planner_decisions")
        stored = cur.fetchone()["total"]
    assert stored == 0
