"""Issue #1026 proof: the ERP source currencies route.

AC-1 — the route: ``GET /api/erp-currencies`` answers
``{"reporting_currency": <code>, "sources": [...]}``.  The
``reporting_currency`` is the ``erp.reporting_currency`` system
setting, resolved exactly as ``GET /api/exceptions`` resolves it —
the pass checks it against the real stored setting (the registry
default ``USD`` while nothing is stored, ``EUR`` once the admin stores
``EUR`` through the settings route) — reusing
``_reporting_currency`` and ``_stored_system_settings`` from
``app.api.exceptions``, not a copy.  ``sources`` holds one entry per
stored row of ``erp_source_currencies`` — ``source_system``,
``currency_code`` and ``seen_at`` (ISO-8601) — ordered by source
system then code, read once per call through the real
``ErpSourceCurrencyPostgresRepository.list_all()`` against the test
database (no fake of the route module: a counting stand-in only counts
the reads and proves the route never writes), and empty while no row
is stored.  Open to any signed-in user, a non-admin included; without
a valid Bearer token it answers 401 with the unchanged gate body.
The response is a typed pydantic model and
``backend/openapi.json`` lists the path.

AC-2 — the rows: with ``odoo`` storing ``USD`` and ``EUR`` (seeded
the way ``tests/test_issue1019_freeform.py`` seeds the table,
through the real repository's ``replace_for``), the answer lists both
under ``odoo``, ordered ``EUR`` then ``USD``, each with a ``seen_at``
that parses as ISO-8601, and ``reporting_currency`` reads ``USD``.
With ``erp.reporting_currency`` stored as ``EUR`` it reads ``EUR``.
With no stored rows, ``sources`` is an empty list.  A non-admin
signed-in user gets 200 and a request with no token gets 401.

Identity — the route is open to any signed-in user and answers 401
without a token, so the pass also runs as a NON-ADMIN (role ``user``):
a second pass in the same test after a full reload (a fresh
TestClient) boots the page the production way — the development token
the page would hold from its ``#access_token=...`` fragment sent as
its Bearer token, ``GET /api/auth/config`` (public), then
``GET /api/auth/me`` — and asserts the admin-only control is absent
(the ``erp.reporting_currency`` system setting the page reads is
admin-only to change: a non-admin PUT answers 403 with the unchanged
detail) while the page still renders (the route answers 200 with the
same reporting currency and rows before and after the refusal).
"""

from datetime import datetime

from fastapi.testclient import TestClient

import app.api.erp_currencies as erp_currencies
from app.main import app
from app.repositories.erp_source_currencies_postgres_repository import (
    ErpSourceCurrencyPostgresRepository,
)

URL = "/api/erp-currencies"
SETTINGS_URL = "/api/settings"
REPORTING_CURRENCY_KEY = "erp.reporting_currency"
ADMIN_ONLY_DETAIL = "Role 'admin' is required to perform this action."
GATE_DETAIL = "Authentication required. Provide a valid Bearer token."


def _sources(body):
    return [(s["source_system"], s["currency_code"]) for s in body["sources"]]


class _CountingRepo:
    """Stands in for the route module's repository: counts its reads
    and proves the route never stores — the rows it serves come from
    the real repository."""

    def __init__(self, inner):
        self.inner = inner
        self.list_calls = 0

    def list_all(self):
        self.list_calls += 1
        return self.inner.list_all()

    def replace_for(self, source_system, codes):
        raise AssertionError("the erp-currencies route must not store currencies")


def test_issue1026_freeform(monkeypatch, client, admin_token, user_token):
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    anon = TestClient(app)
    repo = ErpSourceCurrencyPostgresRepository()

    # -- The rows: odoo stores USD and EUR, and the answer lists both
    #    under odoo, ordered EUR then USD, each with an ISO-8601
    #    seen_at, while reporting_currency reads the default USD. ----
    repo.replace_for("odoo", ["USD", "EUR"])
    body = client.get(URL).json()
    assert set(body) == {"reporting_currency", "sources"}
    assert body["reporting_currency"] == "USD"
    assert _sources(body) == [("odoo", "EUR"), ("odoo", "USD")]
    for source in body["sources"]:
        assert set(source) == {"source_system", "currency_code", "seen_at"}
        parsed = datetime.fromisoformat(source["seen_at"])
        assert parsed.tzinfo is not None

    # One read of the stored rows per call — and no writes: a counting
    # stand-in serves the answer and the route never calls replace_for.
    counting = _CountingRepo(repo)
    monkeypatch.setattr(erp_currencies, "_repo", counting)
    again = client.get(URL).json()
    assert counting.list_calls == 1
    assert again == body

    # -- The setting: stored as EUR through the settings route, the
    #    answer reads EUR — and back to the default once reset. ------
    put = admin.put(f"{SETTINGS_URL}/{REPORTING_CURRENCY_KEY}", json={"value": "EUR"})
    assert put.status_code == 200, put.text
    assert put.json()["value"] == "EUR"
    assert put.json()["source"] == "system"
    body = client.get(URL).json()
    assert body["reporting_currency"] == "EUR"
    assert _sources(body) == [("odoo", "EUR"), ("odoo", "USD")]
    deleted = admin.delete(f"{SETTINGS_URL}/{REPORTING_CURRENCY_KEY}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["value"] == "USD"
    assert deleted.json()["source"] == "default"
    assert client.get(URL).json()["reporting_currency"] == "USD"

    # -- Empty: no stored rows, and sources is an empty list. --------
    repo.replace_for("odoo", [])
    body = client.get(URL).json()
    assert body["reporting_currency"] == "USD"
    assert body["sources"] == []

    # -- Access: a request with no token answers 401 with the
    #    unchanged gate body. ------------------------------------------
    response = anon.get(URL)
    assert response.status_code == 401
    assert response.json() == {"detail": GATE_DETAIL}

    # --- Identity: the non-admin pass, after a full reload -------------
    # The route is open to any signed-in user; the token in the page's
    # #access_token=... fragment is the development token for a
    # non-admin (sub dev-user-1, role "user").  A fresh client (full
    # reload) boots the page the production way: the public
    # /api/auth/config, then /api/auth/me.
    repo.replace_for("odoo", ["USD", "EUR"])

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

    # The page renders for the non-admin: a signed-in user who is not
    # an admin gets the whole answer, 200.
    page = viewer.get(URL)
    assert page.status_code == 200
    page_body = page.json()
    assert page_body["reporting_currency"] == "USD"
    assert _sources(page_body) == [("odoo", "EUR"), ("odoo", "USD")]

    # The admin-only control is absent: the reporting-currency setting
    # the page reads is a system setting, and a non-admin is refused
    # the change, body and status unchanged.
    refused = viewer.put(
        f"{SETTINGS_URL}/{REPORTING_CURRENCY_KEY}", json={"value": "EUR"}
    )
    assert refused.status_code == 403
    assert refused.json() == {"detail": ADMIN_ONLY_DETAIL}

    # ...and the page still renders after the refusal, answer
    # unchanged — the setting the non-admin could not change still
    # reads USD.
    again = viewer.get(URL)
    assert again.status_code == 200
    assert again.json() == page_body
