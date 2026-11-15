"""Issue #117 proof: the machine-credential OData feed.

``backend/app/api/odata.py`` publishes the four ranked recommendation
sets and the planner decisions set under ``/odata`` (mounted in
``app.main.create_app`` WITHOUT the user auth gate) for machine
consumers:

* ``GET /odata/`` — the OData service document: 200, Content-Type
  ``application/json``, ``@odata.context`` ``"$metadata"``, and a
  relative entity-set URL for each of Drift, Excess, Stockout,
  Changes, Decisions, ExcessDecisions, StockoutDecisions and
  Forecast,
* ``GET /odata/$metadata`` — an OData v4 CSDL XML document in
  namespace ``AIERP``: one EntityType per set (the scalar fields of
  the matching ranked row, list fields such as ``notes`` joined into
  one ``Edm.String``), each keyed by ``item_code`` and ``site_code``
  — the Forecast set additionally by ``period`` — plus an
  EntityContainer naming the eight EntitySets.

Both routes authenticate with the HTTP Basic machine credential from
``AIERP_ODATA_USER`` / ``AIERP_ODATA_PASSWORD`` (read at call time,
compared in constant time): wrong or missing credentials answer 401
with ``WWW-Authenticate: Basic realm="AIERP OData"``, a valid user
Bearer token is rejected, while either variable unset or empty makes
every route answer 503 "OData feed not configured" (fail closed).
The credential is never logged, and every other API route still
requires the user token as before.
"""

import base64
import logging
import xml.etree.ElementTree as ElementTree

from fastapi.testclient import TestClient

EDM = "http://docs.oasis-open.org/odata/ns/edm"
SET_NAMES = (
    "Drift",
    "Excess",
    "Stockout",
    "Changes",
    "Decisions",
    "ExcessDecisions",
    "StockoutDecisions",
    "Forecast",
)
ODATA_USER = "odata-machine-117"
ODATA_PASSWORD = "feed-secret-117"
REALM = 'Basic realm="AIERP OData"'


def _basic(user: str, password: str) -> str:
    """An HTTP Basic Authorization header value for user/password."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def test_issue117_freeform(monkeypatch, caplog, user_token):
    monkeypatch.setenv("AIERP_ODATA_USER", ODATA_USER)
    monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
    good = {"Authorization": _basic(ODATA_USER, ODATA_PASSWORD)}
    bearer = {"Authorization": f"Bearer {user_token}"}

    from app.main import app

    with TestClient(app) as client:
        # 1. The user token is a valid user credential ...
        assert client.get("/api/stockout", headers=bearer).status_code == 200

        # 2. Service document: 200, application/json, the $metadata
        #    context and a relative URL per entity set.
        service = client.get("/odata/", headers=good)
        assert service.status_code == 200
        assert service.headers["content-type"] == "application/json"
        document = service.json()
        assert document["@odata.context"] == "$metadata"
        assert document["value"] == [
            {"name": name, "kind": "EntitySet", "url": name} for name in SET_NAMES
        ]
        for entry in document["value"]:
            assert not entry["url"].startswith(("/", "http://", "https://"))

        # 3. $metadata: CSDL XML in namespace AIERP, one EntityType per
        #    set keyed by item_code and site_code, notes joined into a
        #    single Edm.String, and a container with the three sets.
        metadata = client.get("/odata/$metadata", headers=good)
        assert metadata.status_code == 200
        assert metadata.headers["content-type"].startswith("application/xml")
        root = ElementTree.fromstring(metadata.text)
        assert root.tag == f"{{{EDM}}}Edm"
        schema = root.find(f"{{{EDM}}}Schema")
        assert schema is not None
        assert schema.get("Namespace") == "AIERP"
        entity_types = schema.findall(f"{{{EDM}}}EntityType")
        assert [element.get("Name") for element in entity_types] == list(SET_NAMES)
        for element in entity_types:
            key_refs = [
                ref.get("Name")
                for ref in element.findall(f"{{{EDM}}}Key/{{{EDM}}}PropertyRef")
            ]
            if element.get("Name") == "Forecast":
                assert key_refs == ["item_code", "site_code", "period"]
            else:
                assert key_refs == ["item_code", "site_code"]
        for element in entity_types[:4]:
            properties = {
                prop.get("Name"): prop for prop in element.findall(f"{{{EDM}}}Property")
            }
            for property_name in ("item_code", "site_code"):
                assert properties[property_name].get("Type") == "Edm.String"
            assert properties["notes"].get("Type") == "Edm.String"
            assert properties["as_of"].get("Type") == "Edm.Date"
        container = schema.find(f"{{{EDM}}}EntityContainer")
        assert container is not None
        entity_sets = container.findall(f"{{{EDM}}}EntitySet")
        assert [entity_set.get("Name") for entity_set in entity_sets] == list(SET_NAMES)
        assert [entity_set.get("EntityType") for entity_set in entity_sets] == [
            f"AIERP.{name}" for name in SET_NAMES
        ]

        # 4. A wrong password: 401 with the realm header, both routes.
        for path in ("/odata/", "/odata/$metadata"):
            wrong = client.get(
                path, headers={"Authorization": _basic(ODATA_USER, "wrong-password")}
            )
            assert wrong.status_code == 401
            assert wrong.headers["www-authenticate"] == REALM

        # 5. Missing credentials: 401 with the realm header, both routes.
        for path in ("/odata/", "/odata/$metadata"):
            missing = client.get(path)
            assert missing.status_code == 401
            assert missing.headers["www-authenticate"] == REALM

        # 6. Malformed or oversized credentials: 401, never a 500.
        for payload in ("!!!not-base64!!!", "A" * 100_000):
            malformed = client.get(
                "/odata/", headers={"Authorization": f"Basic {payload}"}
            )
            assert malformed.status_code == 401

        # 7. A valid user Bearer token alone is not a machine credential.
        rejected = client.get("/odata/", headers=bearer)
        assert rejected.status_code == 401
        assert rejected.headers["www-authenticate"] == REALM

        # 8. The credential never reaches the logs.
        with caplog.at_level(logging.WARNING, logger="app.api.odata"):
            client.get(
                "/odata/",
                headers={"Authorization": _basic(ODATA_USER, "wrong-password")},
            )
        assert ODATA_PASSWORD not in caplog.text
        assert ODATA_USER not in caplog.text

        # 9. Fail closed: AIERP_ODATA_PASSWORD unset -> 503 on both
        #    routes, whatever credentials are presented.
        monkeypatch.delenv("AIERP_ODATA_PASSWORD")
        for headers in (good, None):
            for path in ("/odata/", "/odata/$metadata"):
                not_configured = client.get(path, headers=headers)
                assert not_configured.status_code == 503
                assert not_configured.json()["detail"] == "OData feed not configured"

        # An empty variable is treated the same.
        monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
        monkeypatch.setenv("AIERP_ODATA_USER", "")
        assert client.get("/odata/$metadata", headers=good).status_code == 503

    # 10. Every other API route still requires the user token as before.
    with TestClient(app) as open_client:
        assert open_client.get("/api/health").status_code == 200
        for path in (
            "/api/drift",
            "/api/excess",
            "/api/stockout",
            "/api/inventory-items",
            "/api/item-sites",
            "/api/on-hand",
        ):
            denied = open_client.get(path)
            assert denied.status_code == 401
            assert denied.headers["www-authenticate"] == "Bearer"
        assert open_client.get("/api/stockout", headers=bearer).status_code == 200
