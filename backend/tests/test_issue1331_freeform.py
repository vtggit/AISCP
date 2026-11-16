"""Issue #1331 proof: an excess row's remedy.

``GET /api/excess`` takes ``include_remedy``, a boolean, false by
default.  While it is false the route answers exactly as before: no
row carries a ``remedy`` key and the ``item_sites`` levels and the
open purchase lines are not read.  While it is true and at least one
row is on the page, the route reads each of the two once —
``_item_site_repo.list_rows(10**9, 0)`` (every ``item_sites`` row:
the row's ``reorder_point`` and ``max_qty`` become the remedy's
``current_reorder_point`` and ``current_max_qty``, each null when the
item-site has no ``item_sites`` row or the value is null or not a
finite number) and ``_open_po_repo.list_latest()`` (the open purchase
lines of each ERP's newest sync: the remedy's ``on_order_units`` is
the sum of the lines' ``open_units`` for the row's item-site that are
finite numbers above 0, whatever their promised dates, 0 when no line
counts) — before it works out any remedy.  Every returned row then
carries ``remedy``, an ``ExcessRemedy`` (``kind`` one of the excess
decision's own action values, ``on_order_units``,
``current_reorder_point``, ``current_max_qty`` and a one-line
``reason``, the levels and the on-order units of the row's item-site
whatever the kind) proposed by the first rule that applies — stop
replenishing while units of the item are still on order; else stop
replenishing while the ERP's reorder point or max quantity is above
the row's justified units (0 for a dead row, so any level above zero
— a level equal to the justified units is not above it); else dispose
a dead row whose ``dead_reason`` is ``stopped``; else no proposal for
a dead row whose ``dead_reason`` is ``never_moved``; else reduce an
``excess`` row — or null when no rule applies.  A read that raises is
caught and leaves every row's ``remedy`` null, because the
stop-replenishing rules cannot be checked: the route still answers 200
with every row it returns, and with no row on the page neither read
runs.  The route writes nothing and calls no ERP; the decision store,
the decision rules and ``POST /api/excess/decisions`` are unchanged.
``ExcessRow`` itself gains nothing, so the OData ``Excess`` set and
the CSV export are unchanged.

Everything is read through the app's test client, signed in the way
test 1313 signs in, with every read of the route replaced by a
stand-in in ``app.api.excess`` that answers fixed data at as-of
2026-11-02 and records its arguments, so no test reads the wall clock
or the database:

* seven excess rows — items A to G, all at site 159, ranks 1 to 7,
  every ``ExcessRow`` field present: A excess with 400 excess units
  over 100 justified, last demand 2026-10-20; B dead and stopped with
  300 units, last demand 2025-03-15; C excess with 700 over 200
  justified, last demand 2026-10-01; D excess with 875.5 over 200
  justified, last demand 2026-09-30; E dead and stopped with 50
  units, last demand 2024-12-01; F dead and never_moved with 20
  units, no last demand and no unit cost; G dead and never_moved with
  10 units, no last demand.  Every dead row has 0 justified units;
* the current-decisions read (``_repo.current_all``) and the
  item-descriptions read (``_item_descriptions``) answer nothing;
* the item_sites levels answer A at 159 with reorder point 20 and
  max 150; B at 159 with 40 and a null max; C at 159 with 75 and
  1500000; D at 159 with 200 and 150 and a safety stock of 900 (which
  no rule reads); D at 160 with 5000 and 9000; F at 159 with a
  reorder point of NaN and a max of 0; G at 159 with a reorder point
  of 5 and a max of NaN.  E has no item_sites row;
* the open purchase lines answer A at 159 with 12.25 open units
  promised 2025-01-10, A at 159 with 30 promised 2026-11-20, A at 159
  with 0, A at 160 with 99, and C at 159 with -5.

Proved here:

  * without the parameter the answer is the stand-in rows, row for
    row, each with ``decision`` null, ``decision_lapsed`` false and
    ``item_description`` null — no row has a ``remedy`` key and
    neither read was called;
  * with it, the same rows each carry their ``remedy`` and exactly
    one key more than without: A stop_replenishing, 42.25 on order
    (its 2025-promised line counts, its 160 and its zero lines do
    not), levels 20 and 150 — its on-order rule comes before its
    levels; B stop_replenishing, 0 on order, levels 40 and null
    (null written ``none``); C stop_replenishing, 0 on order (its -5
    line does not count), levels 75 and 1500000; D reduce, 0 on
    order, levels 200 and 150 (a level equal to the justified units
    is not above them, its 160 levels and its safety stock do not
    count); E dispose, 0 on order, both levels null; F null (it never
    moved and the ERP no longer buys it: its reorder point is not a
    number and its max of 0 is not above its 0 justified units); G
    stop_replenishing, 0 on order, levels 5 and null — a never-moved
    row the ERP still buys gets the stop proposal;
  * the reads — each called exactly once: ``list_rows`` with
    ``(10**9, 0)`` and ``list_latest`` with no argument; with
    ``&include_remedy=true&offset=7`` the answer is empty and neither
    is called again;
  * the failed reads — ``list_rows`` raising, and again
    ``list_latest`` raising, leaves every row's ``remedy`` null, each
    still answering 200;
  * a bad value — ``include_remedy=xyz`` answers 422;
  * the schema — ``include_remedy`` among the route's parameters
    (boolean, default false), the route's description mentions
    ``include_remedy``, the items of the route's 200 answer have a
    ``remedy`` property whose schema is ``ExcessRemedy`` or null,
    ``ExcessRemedy`` has exactly its five keys with ``kind`` allowing
    exactly ``stop_replenishing``, ``reduce`` and ``dispose``,
    ``ExcessDecisionRow``'s properties are ``ExcessRow``'s plus
    ``decision``, ``decision_lapsed`` and ``remedy``, ``ExcessRow``
    has no ``remedy`` property, and ``include_remedy`` is in neither
    ``ExcessDecisionRow``'s fields nor its schema.

Identity — the proof also runs as a NON-ADMIN (role ``user``,
asserted via ``GET /api/auth/me``) in a second pass after a full
reload (a fresh ``TestClient`` booted the production way — the token
the page would take from the URL hash as ``#access_token=...``, with
``GET /api/auth/config`` answering publicly and ``GET /api/auth/me``
resolving the viewer): the route has no admin-only control — the
viewer reads the whole list with remedies (it renders, the same
answer as the admin's) — and a request without a token still gets
401.
"""

from datetime import date

from fastapi.testclient import TestClient

import app.api.excess as excess_api
from app.api.excess import ExcessDecisionRow, ExcessRow
from app.main import app

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

URL = "/api/excess"
SITE = "159"
AS_OF = date(2026, 11, 2)


def _excess_row(
    item,
    rank,
    category,
    dead_reason,
    on_hand,
    unit_cost,
    last_demand_on,
    justified,
    excess,
    window_units,
    months_of_cover,
):
    """One stand-in excess row: every ExcessRow field filled, at the
    fixed as-of, at site 159."""
    return {
        "rank": rank,
        "category": category,
        "dead_reason": dead_reason,
        "item_code": item,
        "site_code": SITE,
        "on_hand_units": on_hand,
        "unit_cost": unit_cost,
        "last_demand_on": last_demand_on,
        "demand_window_units": window_units,
        "months_of_cover": months_of_cover,
        "justified_units": justified,
        "excess_units": excess,
        "money_tied_up": (
            round(excess * unit_cost, 2) if unit_cost is not None else None
        ),
        "as_of": AS_OF,
        "notes": [f"note {item}"],
        "arithmetic": f"{item} arithmetic",
        "item_description": f"Item {item}",
    }


ROWS = [
    _excess_row(
        "A",
        1,
        "excess",
        None,
        1100.0,
        2.5,
        date(2026, 10, 20),
        100.0,
        400.0,
        100.0,
        8.5,
    ),
    _excess_row(
        "B", 2, "dead", "stopped", 300.0, 3.0, date(2025, 3, 15), 0.0, 300.0, 0.0, None
    ),
    _excess_row(
        "C", 3, "excess", None, 900.0, 4.0, date(2026, 10, 1), 200.0, 700.0, 200.0, 6.2
    ),
    _excess_row(
        "D", 4, "excess", None, 1075.5, 5.0, date(2026, 9, 30), 200.0, 875.5, 200.0, 9.1
    ),
    _excess_row(
        "E", 5, "dead", "stopped", 50.0, 6.0, date(2024, 12, 1), 0.0, 50.0, 0.0, None
    ),
    _excess_row("F", 6, "dead", "never_moved", 20.0, None, None, 0.0, 20.0, 0.0, None),
    _excess_row("G", 7, "dead", "never_moved", 10.0, 1.0, None, 0.0, 10.0, 0.0, None),
]


def _item_site_row(item, site, reorder_point, max_qty, safety_stock=0):
    return {
        "item_code": item,
        "site_code": site,
        "reorder_point": reorder_point,
        "max_qty": max_qty,
        "safety_stock": safety_stock,
    }


# E has no item_sites row.  D's safety stock (900) and its levels at
# 160 are there for the rows to ignore.
ITEM_SITE_ROWS = [
    _item_site_row("A", SITE, 20, 150),
    _item_site_row("B", SITE, 40, None),
    _item_site_row("C", SITE, 75, 1500000),
    _item_site_row("D", SITE, 200, 150, safety_stock=900),
    _item_site_row("D", "160", 5000, 9000),
    _item_site_row("F", SITE, float("nan"), 0),
    _item_site_row("G", SITE, 5, float("nan")),
]


def _open_po_line(item, site, open_units, promised_on):
    return {
        "item_code": item,
        "site_code": site,
        "open_units": open_units,
        "promised_on": promised_on,
    }


OPEN_PO_LINES = [
    _open_po_line("A", SITE, 12.25, date(2025, 1, 10)),
    _open_po_line("A", SITE, 30, date(2026, 11, 20)),
    _open_po_line("A", SITE, 0, date(2026, 11, 5)),
    _open_po_line("A", "160", 99, date(2026, 11, 15)),
    _open_po_line("C", SITE, -5, date(2026, 11, 18)),
]

REMEDIES = {
    # A's on-order rule comes before its levels: 12.25 + 30, its
    # zero line and its line at 160 not counting, whatever the
    # promised dates.
    "A": {
        "kind": "stop_replenishing",
        "on_order_units": 42.25,
        "current_reorder_point": 20,
        "current_max_qty": 150,
        "reason": "42.25 units are still on order, adding to the excess. Stop buying it first.",
    },
    "B": {
        "kind": "stop_replenishing",
        "on_order_units": 0,
        "current_reorder_point": 40,
        "current_max_qty": None,
        "reason": (
            "The ERP still reorders it: its reorder point (40) or max "
            "(none) is above the 0 units its demand justifies. Stop buying it first."
        ),
    },
    "C": {
        "kind": "stop_replenishing",
        "on_order_units": 0,
        "current_reorder_point": 75,
        "current_max_qty": 1500000,
        "reason": (
            "The ERP still reorders it: its reorder point (75) or max "
            "(1500000) is above the 200 units its demand justifies. Stop buying it first."
        ),
    },
    "D": {
        "kind": "reduce",
        "on_order_units": 0,
        "current_reorder_point": 200,
        "current_max_qty": 150,
        "reason": (
            "Still in demand (last on 2026-09-30), but 875.5 units are "
            "above the 200 units its demand justifies. Use it up, "
            "transfer it or return it."
        ),
    },
    "E": {
        "kind": "dispose",
        "on_order_units": 0,
        "current_reorder_point": None,
        "current_max_qty": None,
        "reason": (
            "No demand since 2024-12-01. Scrap or sell it: the planner "
            "decides, and any write-down belongs to finance."
        ),
    },
    # F: it never moved, and the ERP no longer buys it: its reorder
    # point is not a number and its max of 0 is not above its 0
    # justified units.
    "F": None,
    "G": {
        "kind": "stop_replenishing",
        "on_order_units": 0,
        "current_reorder_point": 5,
        "current_max_qty": None,
        "reason": (
            "The ERP still reorders it: its reorder point (5) or max "
            "(none) is above the 0 units its demand justifies. Stop buying it first."
        ),
    },
}


class _ExcessRanked:
    """The stand-in ranking: seven fixed rows at the fixed as-of,
    recording its as-of argument."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __call__(self, as_of=None):
        self.calls.append(as_of)
        return self.rows


class _Read:
    """The stand-in for one of the two remedy reads.

    Answers a fixed payload (or raises) and records every call's
    arguments, so a test can see when the read ran and with what.
    """

    def __init__(self, answer, raises=None):
        self.answer = answer
        self.raises = raises
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.answer


def _jsonable(row: dict) -> dict:
    """The answered shape of one stand-in row: dates as ISO strings,
    the way the response serializes them."""
    return {
        key: (value.isoformat() if isinstance(value, date) else value)
        for key, value in row.items()
    }


def _expected_row(standin: dict) -> dict:
    return {
        **standin,
        "decision": None,
        "decision_lapsed": False,
        "item_description": None,
    }


def _expected_remedy_row(standin: dict, remedy) -> dict:
    return {**_expected_row(standin), "remedy": remedy}


def _remedies_of(body: list[dict]) -> dict[str, dict | None]:
    return {row["item_code"]: row["remedy"] for row in body}


def test_issue1331_freeform(client, monkeypatch):
    # -- The stand-ins: every read of the route, in app.api.excess. --
    ranked = _ExcessRanked(ROWS)
    item_sites = _Read(ITEM_SITE_ROWS)
    open_po = _Read(OPEN_PO_LINES)
    monkeypatch.setattr(excess_api, "excess_ranked", ranked)
    monkeypatch.setattr(excess_api._repo, "current_all", lambda: [])
    monkeypatch.setattr(excess_api, "_item_descriptions", lambda: {})
    monkeypatch.setattr(excess_api._item_site_repo, "list_rows", item_sites)
    monkeypatch.setattr(excess_api._open_po_repo, "list_latest", open_po)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    anon = TestClient(app)
    base_params = {"limit": 1000, "include_decided": "true"}
    remedy_params = {**base_params, "include_remedy": "true"}

    # -- Without the parameter: exactly today's answer. -----------------
    response = admin.get(URL, params=base_params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == list("ABCDEFG")
    for standin, row in zip(ROWS, body):
        assert row == _jsonable(_expected_row(standin)), row
        assert "remedy" not in row
    assert ranked.calls == [None]  # the ranking, at the route's as-of (none)
    # Neither remedy read was called.
    assert item_sites.calls == []
    assert open_po.calls == []

    # -- With it: the same rows, each with its remedy and exactly one
    #    key more than without. ------------------------------------------
    response = admin.get(URL, params=remedy_params)
    assert response.status_code == 200, response.text
    remedy_body = response.json()
    assert [row["item_code"] for row in remedy_body] == list("ABCDEFG")
    for standin, row in zip(ROWS, remedy_body):
        assert row == _jsonable(
            _expected_remedy_row(standin, REMEDIES[row["item_code"]])
        ), row
        assert set(row.keys()) == set(ExcessRow.model_fields) | {
            "decision",
            "decision_lapsed",
            "remedy",
        }
    assert _remedies_of(remedy_body) == REMEDIES

    # -- The reads: each once, with the arguments the route must use. ----
    assert item_sites.calls == [((10**9, 0), {})]
    assert open_po.calls == [((), {})]

    # -- The empty page: no row, neither read is called again. ----------
    response = admin.get(URL, params={**remedy_params, "offset": 7})
    assert response.status_code == 200, response.text
    assert response.json() == []
    assert item_sites.calls == [((10**9, 0), {})]
    assert open_po.calls == [((), {})]

    # -- A failed read: list_rows raises, every remedy is null. ----------
    item_sites_down = _Read(None, raises=RuntimeError("the item_sites read is down"))
    monkeypatch.setattr(excess_api._item_site_repo, "list_rows", item_sites_down)
    response = admin.get(URL, params=remedy_params)
    assert response.status_code == 200, response.text
    assert _remedies_of(response.json()) == {item: None for item in "ABCDEFG"}
    assert item_sites_down.calls == [((10**9, 0), {})]
    # The other read was still read once this request.
    assert len(open_po.calls) == 2

    # -- A failed read: list_latest raises (the item_sites read healthy
    #    again).  Every remedy is null. ----------------------------------
    monkeypatch.setattr(excess_api._item_site_repo, "list_rows", item_sites)
    open_po_down = _Read(
        None, raises=RuntimeError("the open purchase lines read is down")
    )
    monkeypatch.setattr(excess_api._open_po_repo, "list_latest", open_po_down)
    response = admin.get(URL, params=remedy_params)
    assert response.status_code == 200, response.text
    assert _remedies_of(response.json()) == {item: None for item in "ABCDEFG"}
    assert open_po_down.calls == [((), {})]
    assert len(item_sites.calls) == 2

    # -- A bad value: include_remedy is a plain boolean query parameter,
    #    a non-boolean answers 422, never 500. ---------------------------
    assert (
        admin.get(URL, params={**base_params, "include_remedy": "xyz"}).status_code
        == 422
    )

    # -- The schema: the parameter, the remedy property and the
    #    ExcessRemedy keys; ExcessRow gains nothing. -----------------------
    spec = app.openapi()
    get_excess = spec["paths"][URL]["get"]
    parameters = {param["name"]: param for param in get_excess["parameters"]}
    include_remedy = parameters["include_remedy"]
    assert include_remedy["in"] == "query"
    assert include_remedy["schema"]["type"] == "boolean"
    assert include_remedy["schema"]["default"] is False
    # The route's description documents the parameter and the field.
    assert "include_remedy" in get_excess["description"]
    assert "remedy" in get_excess["description"]
    for name in (
        "limit",
        "offset",
        "category",
        "dead_reason",
        "as_of",
        "include_decided",
    ):
        assert name in parameters, name
    list_schema = get_excess["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert list_schema["type"] == "array"
    assert list_schema["items"] == {"$ref": "#/components/schemas/ExcessDecisionRow"}
    row_schema = spec["components"]["schemas"]["ExcessDecisionRow"]
    remedy_property = row_schema["properties"]["remedy"]
    assert {"$ref": "#/components/schemas/ExcessRemedy"} in remedy_property.get(
        "anyOf", []
    ), remedy_property
    assert {"type": "null"} in remedy_property.get("anyOf", []), remedy_property
    remedy_schema = spec["components"]["schemas"]["ExcessRemedy"]
    assert set(remedy_schema["properties"]) == {
        "kind",
        "on_order_units",
        "current_reorder_point",
        "current_max_qty",
        "reason",
    }
    assert remedy_schema["properties"]["kind"]["enum"] == [
        "stop_replenishing",
        "reduce",
        "dispose",
    ]
    assert "remedy" not in ExcessRow.model_fields
    # The route's include_remedy parameter is not a property of the
    # row: the row's schema is ExcessRow plus exactly decision,
    # decision_lapsed and remedy, and the parameter is part of neither
    # the model's fields nor its schema (it rides on the answered row's
    # dict as private state the serializer reads).
    assert set(row_schema["properties"]) == set(ExcessRow.model_fields) | {
        "decision",
        "decision_lapsed",
        "remedy",
    }
    assert "include_remedy" not in row_schema["properties"]
    assert "include_remedy" not in ExcessDecisionRow.model_fields
    assert "include_remedy" not in ExcessDecisionRow.model_json_schema()["properties"]

    # -- Identity, second pass after a full reload: the route has no
    #    admin-only control.  The page boots the production way for the
    #    non-admin (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the viewer reads the whole
    #    list with remedies (it renders, the same answer as the
    #    admin's) — and a request without a token still gets 401. -----
    # Both remedy reads healthy again for the viewer's pass.
    monkeypatch.setattr(excess_api._item_site_repo, "list_rows", item_sites)
    monkeypatch.setattr(excess_api._open_po_repo, "list_latest", open_po)
    assert TestClient(app).get("/api/auth/config").status_code == 200
    viewer_reloaded = TestClient(
        app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
    )
    me = viewer_reloaded.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == VIEWER_SUB
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]
    response = viewer_reloaded.get(URL, params=remedy_params)
    assert response.status_code == 200, response.text
    assert response.json() == remedy_body
    assert anon.get(URL, params=remedy_params).status_code == 401
