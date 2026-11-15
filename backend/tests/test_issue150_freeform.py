"""Issue #150 proof: the supported ``$filter`` shapes on the OData
ranked collections.

``backend/app/api/odata.py`` collection routes (``GET /odata/Drift``,
``GET /odata/Excess`` and ``GET /odata/Stockout``) accept a ``$filter``
in exactly four shapes — ``<P> gt <L>``, ``<P> ge <L>``, ``<P> eq <L>``
and ``<P> gt <L> or <P> eq <L>`` with the same property both times —
where ``<P>`` is a property the set's EntityType declares as
``Edm.Date`` or ``Edm.DateTimeOffset`` and ``<L>`` an ISO date or
timestamp.  For an ``Edm.Date`` property the comparison uses the
literal's date part, rows whose property is null never match, the
filter applies before ordering and paging, and ``@odata.nextLink``
carries the ``$filter`` forward.  Every other ``$filter`` (another
operator, another property, ``and``, a function, a quoted or malformed
literal) answers 400 with the existing OData v4 error envelope, and
``$select`` / ``$expand`` stay unsupported.

The rows are seeded (through ``app.api.odata._RANKED_FUNCTIONS``, the
seam the endpoints read the ranked rows from — the real pipeline
resolves one shared as-of per call, so two as-of dates cannot coexist
in a single response otherwise) under the machine credential:

  * the ITEM-A and ITEM-C rows under the newer as-of 2026-03-31,
  * the ITEM-B row under the older as-of 2026-01-31,

in key-tuple order ITEM-A, ITEM-B, ITEM-C.  So:

  * ``as_of gt 2026-01-31`` returns only the newer snapshot's rows,
  * ``as_of gt 2026-03-31 or as_of eq 2026-03-31`` returns the newer
    rows, and so does the timestamp form ``as_of eq 2026-03-31T00:00:00Z``
    (and fractional / offset spellings),
  * ``as_of eq 2026-01-31`` returns only the older rows,
  * ``as_of ge`` behaves as the inclusive form, and a timestamp
    literal on the date property compares by its date part,
  * a paged request keeps the ``$filter`` in its ``@odata.nextLink``
    (and the walk proves the filter applies before paging: page two
    is the second newer row, not the older one),
  * the nullable Stockout date property ``open_supply_due_by`` proves
    rows whose property is null never match,
  * ``$filter=item_code eq 'X'``, ``$filter=as_of lt 2026-01-01`` and
    ``$filter=as_of gt notadate`` each answer 400 with the OData v4
    error envelope, as do ``and``, another operator in the ``or``
    shape, another property, a function, quoted or calendar-invalid
    literals, stray tokens, hostile spellings (huge, NUL, control
    characters, unicode digits) — never a 500 — and ``$select`` /
    ``$expand`` stay unsupported,
  * the routes answer 401 with the realm without the machine
    credential.
"""

import base64
from datetime import date

from fastapi.testclient import TestClient

import app.api.odata as odata
from app.api.drift import DriftRow
from app.api.excess import ExcessRow
from app.api.stockout import StockoutRow
from app.main import app

OLDER = date(2026, 1, 31)
NEWER = date(2026, 3, 31)
OLDER_ISO = OLDER.isoformat()
NEWER_ISO = NEWER.isoformat()

ODATA_USER = "odata-machine-150"
ODATA_PASSWORD = "feed-secret-150"
REALM = 'Basic realm="AIERP OData"'

SETS = ("Drift", "Excess", "Stockout")
MODELS = {"Drift": DriftRow, "Excess": ExcessRow, "Stockout": StockoutRow}

UNSUPPORTED_BODY = {
    "error": {"code": "BadRequest", "message": "unsupported query option"}
}

# The (item_code, site_code, as_of) of the three seeded rows per set,
# in key-tuple order: ITEM-A and ITEM-C carry the newer snapshot date,
# ITEM-B the older one.
SEED = (
    ("ITEM-A", "SITE-1", NEWER),
    ("ITEM-B", "SITE-1", OLDER),
    ("ITEM-C", "SITE-1", NEWER),
)

# Stockout extras: the nullable Edm.Date property open_supply_due_by,
# set on ITEM-A and ITEM-C and null on ITEM-B.
STOCKOUT_DUE_BY = {
    "ITEM-A": date(2026, 3, 5),
    "ITEM-C": date(2026, 3, 6),
}


def _basic(user: str, password: str) -> str:
    """An HTTP Basic Authorization header value for user/password."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def _seed_rows(set_name: str) -> list[dict]:
    """The raw rows the set's ranked function answers with."""
    rows = []
    for index, (item_code, site_code, as_of) in enumerate(SEED, start=1):
        values: dict = {name: None for name in MODELS[set_name].model_fields}
        values.update(
            item_code=item_code,
            site_code=site_code,
            as_of=as_of,
            rank=index,
            notes=["seeded note"],
        )
        if set_name == "Stockout" and item_code in STOCKOUT_DUE_BY:
            values["open_supply_due_by"] = STOCKOUT_DUE_BY[item_code]
        rows.append(values)
    return rows


def _json_row(set_name: str, index: int) -> dict:
    """The row the endpoints expose for the index-th seeded row: every
    declared property, dates as ISO strings, notes joined as a string."""
    item_code, site_code, as_of = SEED[index - 1]
    row = {name: None for name in MODELS[set_name].model_fields}
    row.update(
        item_code=item_code,
        site_code=site_code,
        rank=index,
        as_of=as_of.isoformat(),
        notes="seeded note",
    )
    if set_name == "Stockout" and item_code in STOCKOUT_DUE_BY:
        row["open_supply_due_by"] = STOCKOUT_DUE_BY[item_code].isoformat()
    return row


ALL_ROWS = {
    set_name: [_json_row(set_name, index) for index in (1, 2, 3)] for set_name in SETS
}
NEWER_ROWS = {
    set_name: [ALL_ROWS[set_name][0], ALL_ROWS[set_name][2]] for set_name in SETS
}
OLDER_ROWS = {set_name: [ALL_ROWS[set_name][1]] for set_name in SETS}


def test_issue150_freeform(client, monkeypatch):
    monkeypatch.setenv("AIERP_ODATA_USER", ODATA_USER)
    monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
    for set_name in SETS:
        monkeypatch.setitem(
            odata._RANKED_FUNCTIONS,
            set_name,
            (lambda set_name=set_name: _seed_rows(set_name)),
        )
    good = {"Authorization": _basic(ODATA_USER, ODATA_PASSWORD)}

    def get(set_name: str, params: dict | None = None):
        return client.get(f"/odata/{set_name}", params=params, headers=good)

    def body_for(set_name: str) -> dict:
        return {"@odata.context": f"$metadata#{set_name}"}

    # 1. Without a filter the set answers all three rows in key-tuple
    #    order, so the filter assertions below really filter.
    for set_name in SETS:
        response = get(set_name)
        assert response.status_code == 200
        assert response.json() == {
            **body_for(set_name),
            "value": ALL_ROWS[set_name],
        }

    # 2. as_of gt <older> returns only the newer snapshot's rows.
    for set_name in SETS:
        response = get(set_name, {"$filter": f"as_of gt {OLDER_ISO}"})
        assert response.status_code == 200
        assert response.json() == {
            **body_for(set_name),
            "value": NEWER_ROWS[set_name],
        }

    # 3. as_of gt <newer> or as_of eq <newer> returns the newer rows,
    #    and so does the timestamp form of the newer date (with a
    #    fractional part and a zone offset, and plain).
    or_filter = f"as_of gt {NEWER_ISO} or as_of eq {NEWER_ISO}"
    for set_name in SETS:
        for value in (
            or_filter,
            f"as_of eq {NEWER_ISO}T00:00:00Z",
            f"as_of eq {NEWER_ISO}T00:00:00.250Z",
            f"as_of eq {NEWER_ISO}T08:00:00+08:00",
        ):
            response = get(set_name, {"$filter": value})
            assert response.status_code == 200
            assert response.json() == {
                **body_for(set_name),
                "value": NEWER_ROWS[set_name],
            }

    # 4. as_of eq <older> returns only the older rows; ge is the
    #    inclusive form; a timestamp literal on the date property
    #    compares by its date part.
    for set_name in SETS:
        response = get(set_name, {"$filter": f"as_of eq {OLDER_ISO}"})
        assert response.status_code == 200
        assert response.json() == {
            **body_for(set_name),
            "value": OLDER_ROWS[set_name],
        }
    for set_name in SETS:
        response = get(set_name, {"$filter": f"as_of ge {OLDER_ISO}"})
        assert response.status_code == 200
        assert response.json() == {
            **body_for(set_name),
            "value": ALL_ROWS[set_name],
        }
        response = get(set_name, {"$filter": f"as_of gt {OLDER_ISO}T12:00:00Z"})
        assert response.status_code == 200
        assert response.json()["value"] == NEWER_ROWS[set_name]
        # The offset-minute boundary stays valid: +23:59 is a legal
        # offset and the date property compares by the literal's date
        # part (2026-01-31), so only the newer rows answer.
        response = get(set_name, {"$filter": f"as_of gt {OLDER_ISO}T00:00:00+23:59"})
        assert response.status_code == 200
        assert response.json()["value"] == NEWER_ROWS[set_name]

    # 5. A paged request keeps the $filter in its @odata.nextLink, on
    #    the same origin with $skip advanced; the walk proves the
    #    filter applies before paging (page two is the second newer
    #    row with no further link, not the older one and not an empty
    #    page).
    for set_name in SETS:
        first = get(set_name, {"$top": "1", "$filter": f"as_of gt {OLDER_ISO}"})
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["value"] == [ALL_ROWS[set_name][0]]
        assert first_body["@odata.nextLink"] == (
            f"http://testserver/odata/{set_name}?$top=1"
            f"&$filter=as_of%20gt%20{OLDER_ISO}&$skip=1"
        )
        second = client.get(first_body["@odata.nextLink"], headers=good)
        assert second.status_code == 200
        second_body = second.json()
        assert second_body["value"] == [ALL_ROWS[set_name][2]]
        assert "@odata.nextLink" not in second_body

    # 6. Rows whose (nullable) property is null never match: the
    #    Stockout open_supply_due_by filter sees only ITEM-A and
    #    ITEM-C, never the null ITEM-B.
    response = get("Stockout", {"$filter": "open_supply_due_by gt 2026-03-04"})
    assert response.status_code == 200
    assert response.json()["value"] == [
        ALL_ROWS["Stockout"][0],
        ALL_ROWS["Stockout"][2],
    ]
    response = get("Stockout", {"$filter": "open_supply_due_by eq 2026-03-05"})
    assert response.status_code == 200
    assert response.json()["value"] == [ALL_ROWS["Stockout"][0]]
    response = get("Stockout", {"$filter": "open_supply_due_by gt 2026-03-07"})
    assert response.status_code == 200
    assert response.json() == {
        **body_for("Stockout"),
        "value": [],
    }

    # 7. Any other $filter keeps the existing OData v4 400 envelope:
    #    another operator, another property, and, a function, a quoted
    #    or calendar-invalid literal, a bad time, an out-of-range
    #    offset (minutes of 60 or more), stray tokens, the wrong
    #    shape for the or-form.
    for set_name in SETS:
        for value in (
            "item_code eq 'X'",
            "as_of lt 2026-01-01",
            "as_of gt notadate",
            "as_of ne 2026-01-01",
            f"as_of gt {OLDER_ISO} and as_of lt 2026-12-31",
            f"as_of ge {OLDER_ISO} or as_of eq {NEWER_ISO}",
            f"as_of gt {OLDER_ISO} or item_code eq {NEWER_ISO}",
            "year(as_of) eq 2026",
            "as_of eq '2026-01-31'",
            "as_of eq 2026-02-30",
            "as_of gt 2026-01-31T25:00:00Z",
            "as_of gt 2026-01-31T00:00:00+00:75",
            "as_of gt 2026-01-31T00:00:00-01:60",
            "as_of gt 2026-01-31+99:00",
            f"as_of gt {OLDER_ISO} extra",
            "",
            "as_of",
            "as_of gt",
            "gt 2026-01-31",
            "2026-01-31",
        ):
            response = client.get(
                f"/odata/{set_name}", params={"$filter": value}, headers=good
            )
            assert response.status_code == 400
            assert response.status_code != 422
            assert response.json() == UNSUPPORTED_BODY

    # 8. Hostile spellings never answer 500 (or 422): huge, NUL and
    #    control characters, unicode digits.
    for value in (
        "x" * 10_000,
        "as_of gt " + "\u0000",
        "as_of gt 2026-01-31\u0000",
        "as_of gt \u00015",
        "\u0665\u0666\u0667\u0668-\u0661\u0662-\u0663\u0664",
    ):
        response = client.get("/odata/Drift", params={"$filter": value}, headers=good)
        assert response.status_code == 400
        assert response.status_code != 422
        assert response.json() == UNSUPPORTED_BODY

    # 9. $select and $expand stay unsupported.
    for option in ("$select", "$expand"):
        response = client.get(
            "/odata/Drift", params={option: "item_code"}, headers=good
        )
        assert response.status_code == 400
        assert response.json() == UNSUPPORTED_BODY

    # 10. Without the machine credential the filtered route answers
    #     401 with the realm, as before.
    with TestClient(app) as anonymous:
        response = anonymous.get(
            "/odata/Drift", params={"$filter": f"as_of gt {OLDER_ISO}"}
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == REALM
