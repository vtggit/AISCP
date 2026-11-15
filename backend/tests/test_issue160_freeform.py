"""Issue #160 proof: the ``basicAuth`` OpenAPI security scheme for ``/odata``.

``backend/app/api/odata.py`` advertises the machine Basic credential to
OpenAPI consumers: the OData router declares
``fastapi.security.HTTPBasic(scheme_name="basicAuth", auto_error=False)``
as a dependency beside the real gate.  The schema then lists the
``basicAuth`` security scheme (type ``http``, scheme ``basic``) beside
``bearerAuth`` and every ``/odata`` operation (the service document,
``$metadata`` and each entity-set collection) declares
``security: [{"basicAuth": []}]``, while the ``/api`` operations keep
declaring ``bearerAuth`` only.  The dependency documents the scheme
only: the machine-credential check, its 401 with
``WWW-Authenticate: Basic realm="AIERP OData"`` and every other OData
response stay exactly as they were — ``GET /odata/Drift`` without
credentials still answers 401 with that header and with the machine
credential still answers 200.
"""

import base64

from fastapi.testclient import TestClient

ODATA_USER = "odata-machine-160"
ODATA_PASSWORD = "feed-secret-160"
REALM = 'Basic realm="AIERP OData"'
HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def test_issue160_freeform(monkeypatch):
    from app.main import app

    # -- AC-1: basicAuth is listed beside bearerAuth in the schema. --
    schema = app.openapi()
    schemes = schema["components"]["securitySchemes"]
    assert schemes["basicAuth"] == {"type": "http", "scheme": "basic"}
    assert schemes["bearerAuth"] == {"type": "http", "scheme": "bearer"}

    odata_paths = [path for path in schema["paths"] if path.startswith("/odata")]
    assert sorted(odata_paths) == sorted(
        [
            "/odata/",
            "/odata/$metadata",
            "/odata/Drift",
            "/odata/Excess",
            "/odata/Stockout",
            "/odata/Changes",
            "/odata/Decisions",
            "/odata/ExcessDecisions",
            "/odata/StockoutDecisions",
            "/odata/Forecast",
        ]
    )
    for path in odata_paths:
        for method, operation in schema["paths"][path].items():
            if method not in HTTP_METHODS:
                continue
            assert operation["security"] == [
                {"basicAuth": []}
            ], f"{method.upper()} {path} must declare basicAuth only"
            assert all(
                "bearerAuth" not in entry for entry in operation["security"]
            ), f"{method.upper()} {path} must not list bearerAuth"

    # -- AC-1: the /api operations keep declaring bearerAuth only. --
    api_paths = [path for path in schema["paths"] if path.startswith("/api/")]
    assert api_paths
    for path in api_paths:
        for method, operation in schema["paths"][path].items():
            if method not in HTTP_METHODS:
                continue
            assert all(
                "basicAuth" not in entry for entry in operation.get("security", [])
            ), f"{method.upper()} {path} must not list basicAuth"
    drift_get = schema["paths"]["/api/drift"]["get"]
    assert drift_get["security"] == [{"bearerAuth": []}]
    assert all("basicAuth" not in entry for entry in drift_get["security"])

    # -- AC-2: the runtime answers are unchanged. ------------------------
    monkeypatch.setenv("AIERP_ODATA_USER", ODATA_USER)
    monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
    token = base64.b64encode(f"{ODATA_USER}:{ODATA_PASSWORD}".encode()).decode("ascii")
    with TestClient(app) as client:
        anonymous = client.get("/odata/Drift")
        assert anonymous.status_code == 401
        assert anonymous.headers["WWW-Authenticate"] == REALM

        machine = client.get(
            "/odata/Drift", headers={"Authorization": f"Basic {token}"}
        )
        assert machine.status_code == 200, machine.text
