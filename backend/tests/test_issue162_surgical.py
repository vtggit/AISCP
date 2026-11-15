"""Proving test for issue #162: bearerAuth security scheme in OpenAPI."""

from fastapi.testclient import TestClient


def test_issue162_surgical(admin_token):
    from app.main import app as app_instance

    # --- OpenAPI schema checks ---
    schema = app_instance.openapi()

    # bearerAuth is declared in securitySchemes
    schemes = schema["components"]["securitySchemes"]
    assert "bearerAuth" in schemes, "bearerAuth missing from components.securitySchemes"
    assert schemes["bearerAuth"] == {"type": "http", "scheme": "bearer"}

    # Check security on every operation
    for path, methods in schema["paths"].items():
        for method, op in methods.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            if path.startswith("/api/"):
                if path == "/api/health" and method == "get":
                    assert (
                        "security" not in op
                    ), "GET /api/health must have no security key"
                else:
                    assert op.get("security") == [{"bearerAuth": []}], (
                        f"{method.upper()} {path} must have "
                        'security == [{"bearerAuth": []}]'
                    )
            elif path.startswith("/odata"):
                sec = op.get("security", [])
                for entry in sec:
                    assert (
                        "bearerAuth" not in entry
                    ), f"{method.upper()} {path} must not list bearerAuth"

    # --- Runtime behaviour (no default credentials) ---
    client = TestClient(app_instance)

    # Without token: 401
    resp = client.get("/api/inventory-items")
    assert resp.status_code == 401

    # With valid Bearer token: 200
    resp = client.get(
        "/api/inventory-items",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    # With suffixed token (token:admin): 401
    resp = client.get(
        "/api/inventory-items",
        headers={"Authorization": f"Bearer {admin_token}:admin"},
    )
    assert resp.status_code == 401

    # Health endpoint open: 200 without token
    resp = client.get("/api/health")
    assert resp.status_code == 200
