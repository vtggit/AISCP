"""Issue #607 proof: open purchase lines to expedite.

``app.recommend.expedite.expedite_list`` reads the latest open
purchase line snapshot (``OpenPurchaseLinePostgresRepository.
list_latest``), the on-hand quantities (``OnHandPostgresRepository``),
the item-site unit costs (``ItemSitePostgresRepository``) and the "ok"
reorder recommendations (``recommendations_for_all``) once each, and
calls ``supplier_performance(as_of=as_of)`` once; it writes nothing.
Every open line with ``open_units`` above 0 is considered; a line
promised more than 90 days before ``as_of`` (the stockout window) or
with a null ``promised_on`` is left out and counted in
``stale_excluded``. The expected date is the promise when
``promised_on`` is later than ``ordered_on``, else ``ordered_on`` plus
the supplier's ``avg_lead_time_days`` rounded to whole days half-up
(10.5 becomes 11), falling back to the promise when the supplier has
no figure (``promise_is_default``). A line is listed when it is
``late`` (the expected date before ``as_of``) or
``after_stockout`` (an "ok" recommendation with ``d`` above 0 and the
expected date after ``as_of`` plus ``floor(on_hand / d)`` days,
``as_of`` itself when the on hand is at or below 0); only flagged
lines are listed, ranked by soonest projected stockout (none last),
then larger days late, then larger open value, then order and line
number. The answer is ``{"as_of", "lines", "stale_excluded"}``; an
``as_of`` after today raises ``ValueError``.

``GET /api/expedite`` (query ``as_of``) serves that dict with response
models behind the app's ``auth_gate`` (401 without a token, 200 for
any signed-in role), and a ``ValueError`` answers 422.

Proven here, in order, with every read monkeypatched to fixed rows
(no database) and the server's "today" (the module's ``_today`` seam
standing in for ``date.today()``) monkeypatched to the fixed
``AS_OF`` 2026-10-23 so the proof never depends on the real clock:

  * late: line RO:PO003077:1 — item AAA at site 154, supplier "4883",
    ordered 2026-10-14, promised 2026-10-21, 647 open units at unit
    cost 3.0, on hand 100 at ``d`` 50: late by 2 days, projected
    stockout 2026-10-25 (before nothing here) so
    ``reasons`` ["late"], ``days_late`` 2, ``days_of_cover`` 2.0 and
    ``open_value`` 1941.0;
  * after the stockout only: line RO:PO003090:1 — item BBB, ordered
    2026-10-20, promised 2026-11-05, on hand 30 at ``d`` 10 (projected
    stockout 2026-10-26): ``reasons`` ["after_stockout"],
    ``days_late`` null;
  * default promise: line RO:PO002990:1 — item CCC (on hand 1000 at
    ``d`` 5), supplier "4870" whose ``avg_lead_time_days`` is 10.0,
    ordered and promised 2026-10-01: ``promise_is_default`` true,
    ``expected_on`` 2026-10-11, ``reasons`` ["late"] and
    ``days_late`` 12; it ranks after the two lines above (its
    projected stockout is later);
  * not in the list: a line promised 2026-10-24 for an item with 60
    days of cover (no flag), a line with ``open_units`` 0, and a line
    promised 2026-07-01 (counted in ``stale_excluded``, together with
    the line whose ``promised_on`` is null);
  * no recommendation: a late line for an item whose only
    recommendation is not "ok" appears with ``d``, ``days_of_cover``
    and ``projected_stockout_on`` null and ranks after every line
    with a projected stockout;
  * the supplier rate: taken from the monkeypatched
    ``supplier_performance`` (called once, with ``as_of``);
  * validation: a future ``as_of`` raises ``ValueError``;
  * the route: through the test client, 401 without a token, 200 with
    the planner token (the bare development token the suite's
    ``client`` carries, role user) and the same body, and 422 for a
    future ``as_of``;
  * identity, as a NON-ADMIN (role ``user``), in a second pass after a
    full reload: the page boots the production way (the token it would
    take from the URL hash as ``#access_token=...`` — the bare
    development token — with ``/api/auth/config`` answering publicly
    and ``/api/auth/me`` resolving the viewer for that user, dev-mode
    auth standing in for the IdP) — the route carries no admin-only
    control, so the non-admin viewer reads the expedited lines and the
    page still renders, while unauthenticated /api traffic is still
    401.
"""

import inspect
import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.api.expedite as expedite_api
import app.recommend.expedite as expedite_module
from app.main import app
from app.recommend.expedite import expedite_list
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)

URL = "/api/expedite"
AS_OF = date(2026, 10, 23)
ROUTE_PARAMS = {"as_of": AS_OF.isoformat()}
FUTURE_AS_OF = AS_OF + timedelta(days=1)


def _open_line(key, item, supplier, ordered, promised, open_units):
    return {
        "id": f"uuid-{key}",
        "business_key": key,
        "item_code": item,
        "site_code": "154",
        "supplier_code": supplier,
        "order_type": key.split(":")[0],
        "open_units": open_units,
        "promised_on": promised,
        "ordered_on": ordered,
        "as_of": "2026-10-23T00:00:00+00:00",
        "source_system": "acumatica",
        "synced_at": "2026-10-23T00:00:00+00:00",
    }


OPEN_LINES = [
    # Late: promised after the order, expected 2026-10-21 < as_of.
    _open_line(
        "RO:PO003077:1", "AAA", "4883", date(2026, 10, 14), date(2026, 10, 21), 647
    ),
    # After the stockout only: promised 2026-11-05, projected stockout
    # 2026-10-26.
    _open_line(
        "RO:PO003090:1", "BBB", "4891", date(2026, 10, 20), date(2026, 11, 5), 120
    ),
    # Default promise: promised on the order day; expected is the
    # order date plus the supplier's 10-day mean lead time.
    _open_line(
        "RO:PO002990:1", "CCC", "4870", date(2026, 10, 1), date(2026, 10, 1), 250
    ),
    # No flag: promised 2026-10-24 (after as_of) for an item with 60
    # days of cover — neither late nor after the stockout.
    _open_line(
        "RO:PO003100:1", "DDD", "4883", date(2026, 10, 20), date(2026, 10, 24), 300
    ),
    # Zero open units: not considered at all (not even stale).
    _open_line(
        "RO:PO003101:1", "EEE", "4883", date(2026, 10, 10), date(2026, 10, 15), 0
    ),
    # Stale: promised more than 90 days before as_of — excluded and
    # counted.
    _open_line("RO:PO002900:1", "FFF", "4883", date(2026, 6, 1), date(2026, 7, 1), 10),
    # Null promised_on: excluded and counted in stale_excluded.
    _open_line("RO:PO003102:1", "GGG", "4883", date(2026, 10, 10), None, 5),
    # Late with no "ok" recommendation: listed with the demand fields
    # null, ranked last (no projected stockout).
    _open_line(
        "RO:PO003050:1", "HHH", "4883", date(2026, 10, 10), date(2026, 10, 15), 100
    ),
]


def _on_hand(item, quantity):
    return {
        "id": f"uuid-onhand-{item}",
        "item_code": item,
        "site_code": "154",
        "quantity_on_hand": quantity,
        "as_of": "2026-10-23T00:00:00+00:00",
        "source_system": "acumatica",
        "synced_at": "2026-10-23T00:00:00+00:00",
    }


ON_HAND = [
    _on_hand("AAA", 100),
    _on_hand("BBB", 30),
    _on_hand("CCC", 1000),
    _on_hand("DDD", 300),
    _on_hand("HHH", 50),
]


def _item_site(item, unit_cost):
    return {
        "id": f"uuid-itemsite-{item}",
        "item_code": item,
        "site_code": "154",
        "unit_cost": unit_cost,
        "reorder_point": None,
        "safety_stock": None,
        "max_qty": None,
        "order_qty": None,
        "master_lead_time_days": None,
        "source_system": "acumatica",
        "synced_at": "2026-10-23T00:00:00+00:00",
    }


ITEM_SITES = [
    _item_site("AAA", 3.0),
    _item_site("BBB", 4.0),
    _item_site("CCC", 1.25),
    _item_site("DDD", 2.0),
    _item_site("HHH", 2.0),
]


def _recommendation(item, d, status="ok"):
    return {
        "status": status,
        "item_code": item,
        "site_code": "154",
        "reorder_point_units": 100,
        "inputs": {
            "d": d,
            "sd_d": 1.0,
            "lt": 14.0,
            "sd_lt": 1.0,
            "z": 1.6448536269514722,
            "service_level": 0.95,
        },
    }


RECOMMENDATIONS = [
    _recommendation("AAA", 50),
    _recommendation("BBB", 10),
    _recommendation("CCC", 5),
    _recommendation("DDD", 5),
    # HHH's only recommendation is not "ok": it counts as no
    # recommendation at all.
    _recommendation("HHH", 8, status="insufficient_lead_time"),
]

# The supplier performance the proof stands in for (monkeypatched):
# "4870" carries the 10-day mean lead time the default promise of
# RO:PO002990:1 adds to its order date, "4891" has no real promise in
# the window (null rate).
SUPPLIER_PERFORMANCE = {
    "as_of": "2026-10-23",
    "window_days": 365,
    "suppliers": [
        {"supplier_code": "4870", "on_time_rate": 0.0, "avg_lead_time_days": 10.0},
        {"supplier_code": "4883", "on_time_rate": 0.8, "avg_lead_time_days": 7.0},
        {"supplier_code": "4891", "on_time_rate": None, "avg_lead_time_days": 5.0},
    ],
}

EXPECTED = {
    "as_of": "2026-10-23",
    "stale_excluded": 2,
    "lines": [
        # 1. Late (expected 2026-10-21 before as_of); the projected
        #    stockout 2026-10-25 is after the expected date, so not
        #    after the stockout. Soonest projected stockout overall.
        {
            "order_type": "RO",
            "order_nbr": "PO003077",
            "line_nbr": "1",
            "item_code": "AAA",
            "site_code": "154",
            "supplier_code": "4883",
            "ordered_on": date(2026, 10, 14),
            "promised_on": date(2026, 10, 21),
            "expected_on": date(2026, 10, 21),
            "promise_is_default": False,
            "open_units": 647.0,
            "open_value": 1941.0,
            "days_late": 2,
            "on_hand_units": 100.0,
            "d": 50.0,
            "days_of_cover": 2.0,
            "projected_stockout_on": date(2026, 10, 25),
            "reasons": ["late"],
            "supplier_on_time_rate": 0.8,
        },
        # 2. After the stockout only (expected 2026-11-05 after the
        #    projected stockout 2026-10-26, but not before as_of).
        {
            "order_type": "RO",
            "order_nbr": "PO003090",
            "line_nbr": "1",
            "item_code": "BBB",
            "site_code": "154",
            "supplier_code": "4891",
            "ordered_on": date(2026, 10, 20),
            "promised_on": date(2026, 11, 5),
            "expected_on": date(2026, 11, 5),
            "promise_is_default": False,
            "open_units": 120.0,
            "open_value": 480.0,
            "days_late": None,
            "on_hand_units": 30.0,
            "d": 10.0,
            "days_of_cover": 3.0,
            "projected_stockout_on": date(2026, 10, 26),
            "reasons": ["after_stockout"],
            "supplier_on_time_rate": None,
        },
        # 3. Default promise: expected 2026-10-01 + 10 days =
        #    2026-10-11, late by 12; its projected stockout
        #    (2027-05-11) is later than both lines above.
        {
            "order_type": "RO",
            "order_nbr": "PO002990",
            "line_nbr": "1",
            "item_code": "CCC",
            "site_code": "154",
            "supplier_code": "4870",
            "ordered_on": date(2026, 10, 1),
            "promised_on": date(2026, 10, 1),
            "expected_on": date(2026, 10, 11),
            "promise_is_default": True,
            "open_units": 250.0,
            "open_value": 312.5,
            "days_late": 12,
            "on_hand_units": 1000.0,
            "d": 5.0,
            "days_of_cover": 200.0,
            "projected_stockout_on": date(2027, 5, 11),
            "reasons": ["late"],
            "supplier_on_time_rate": 0.0,
        },
        # 4. Late with no "ok" recommendation: the demand fields are
        #    null and, with no projected stockout, the line ranks
        #    after every line that has one.
        {
            "order_type": "RO",
            "order_nbr": "PO003050",
            "line_nbr": "1",
            "item_code": "HHH",
            "site_code": "154",
            "supplier_code": "4883",
            "ordered_on": date(2026, 10, 10),
            "promised_on": date(2026, 10, 15),
            "expected_on": date(2026, 10, 15),
            "promise_is_default": False,
            "open_units": 100.0,
            "open_value": 200.0,
            "days_late": 8,
            "on_hand_units": 50.0,
            "d": None,
            "days_of_cover": None,
            "projected_stockout_on": None,
            "reasons": ["late"],
            "supplier_on_time_rate": 0.8,
        },
    ],
}


def _as_json(result):
    """The result with dates as ISO strings, as the route serves it."""
    return {
        "as_of": result["as_of"],
        "stale_excluded": result["stale_excluded"],
        "lines": [
            {
                key: (value.isoformat() if isinstance(value, date) else value)
                for key, value in line.items()
            }
            for line in result["lines"]
        ],
    }


def test_issue607_freeform(monkeypatch, client):
    # -- 1. The computation (every read monkeypatched — no DB) ------
    calls = {
        "lines": 0,
        "on_hand": 0,
        "item_sites": 0,
        "recommendations": 0,
        "recommendations_as_of": None,
        "supplier_as_of": [],
    }

    def fake_list_latest(self):
        calls["lines"] += 1
        return [dict(row) for row in OPEN_LINES]

    def fake_on_hand_list_rows(self, limit, offset, **filters):
        calls["on_hand"] += 1
        return [dict(row) for row in ON_HAND]

    def fake_item_site_list_rows(self, limit, offset, **filters):
        calls["item_sites"] += 1
        return [dict(row) for row in ITEM_SITES]

    def fake_recommendations_for_all(as_of=None):
        calls["recommendations"] += 1
        calls["recommendations_as_of"] = as_of
        return [dict(row) for row in RECOMMENDATIONS]

    def fake_supplier_performance(as_of=None, window_days=365):
        calls["supplier_as_of"].append(as_of)
        return dict(SUPPLIER_PERFORMANCE)

    monkeypatch.setattr(
        OpenPurchaseLinePostgresRepository, "list_latest", fake_list_latest
    )
    monkeypatch.setattr(OnHandPostgresRepository, "list_rows", fake_on_hand_list_rows)
    monkeypatch.setattr(
        ItemSitePostgresRepository, "list_rows", fake_item_site_list_rows
    )
    monkeypatch.setattr(
        expedite_module, "recommendations_for_all", fake_recommendations_for_all
    )
    monkeypatch.setattr(
        expedite_module, "supplier_performance", fake_supplier_performance
    )

    # The proof is clock-free: the server's "today" is the frozen
    # AS_OF, so default as_of resolution and the "after today" check
    # never depend on the real clock.
    monkeypatch.setattr(expedite_module, "_today", lambda: AS_OF)

    result = expedite_list(as_of=AS_OF)
    assert result == EXPECTED

    # The reads: each exactly once, through the existing repositories,
    # with the recommendations and the supplier performance asked for
    # the resolved as_of.
    assert calls["lines"] == 1
    assert calls["on_hand"] == 1
    assert calls["item_sites"] == 1
    assert calls["recommendations"] == 1
    assert calls["recommendations_as_of"] == AS_OF
    assert calls["supplier_as_of"] == [AS_OF]

    by_key = {(line["order_nbr"], line["line_nbr"]): line for line in result["lines"]}

    # Late: 647 x 3.0 = 1941.0, 2 days late, 2.0 days of cover; the
    # projected stockout 2026-10-25 is after the expected date, so
    # the line is not after the stockout.
    late_line = by_key[("PO003077", "1")]
    assert late_line["reasons"] == ["late"]
    assert late_line["days_late"] == 2
    assert late_line["days_of_cover"] == 2.0
    assert late_line["open_value"] == 1941.0
    assert late_line["promise_is_default"] is False
    assert late_line["supplier_on_time_rate"] == 0.8

    # After the stockout only: the expected 2026-11-05 is after the
    # projected stockout 2026-10-26 but not before as_of.
    stockout_line = by_key[("PO003090", "1")]
    assert stockout_line["reasons"] == ["after_stockout"]
    assert stockout_line["days_late"] is None

    # Default promise: the promised date is the order date (the ERP's
    # default, not a commitment), so the expected date is the order
    # date plus the supplier's 10-day mean lead time, late by 12.
    default_line = by_key[("PO002990", "1")]
    assert default_line["promise_is_default"] is True
    assert default_line["expected_on"] == date(2026, 10, 11)
    assert default_line["reasons"] == ["late"]
    assert default_line["days_late"] == 12

    # Not in the list: the no-flag line (promised 2026-10-24 with 60
    # days of cover), the zero-open-units line, and the stale line
    # (promised 2026-07-01, more than 90 days before as_of) — the
    # stale line and the undated line together give stale_excluded 2.
    assert set(by_key) == {
        ("PO003077", "1"),
        ("PO003090", "1"),
        ("PO002990", "1"),
        ("PO003050", "1"),
    }
    assert result["stale_excluded"] == 2

    # No recommendation: the late line for an item without an "ok"
    # recommendation carries null demand fields and ranks after every
    # line with a projected stockout.
    no_recommendation_line = by_key[("PO003050", "1")]
    assert no_recommendation_line["d"] is None
    assert no_recommendation_line["days_of_cover"] is None
    assert no_recommendation_line["projected_stockout_on"] is None
    assert [line["order_nbr"] for line in result["lines"]] == [
        "PO003077",
        "PO003090",
        "PO002990",
        "PO003050",
    ]

    # -- 2. Validation -------------------------------------------------
    # A future as_of (one day past the frozen today) raises.
    with pytest.raises(ValueError):
        expedite_list(as_of=FUTURE_AS_OF)
    # A non-date as_of raises the same way (never a 500 downstream).
    with pytest.raises(ValueError):
        expedite_list(as_of="not-a-date")

    # -- 3. The route ----------------------------------------------------
    # 401 without a token; 200 with the planner token (the bare
    # development token the suite's ``client`` carries, role user) and
    # the same body; 422 for a future as_of (the ValueError answer).
    anon = TestClient(app)
    assert anon.get(URL).status_code == 401

    response = client.get(URL, params=ROUTE_PARAMS)
    assert response.status_code == 200, response.text
    assert response.json() == _as_json(EXPECTED)
    # The route was asked for the fixed AS_OF, not the server's clock:
    # the body echoes it back.
    assert response.json()["as_of"] == AS_OF.isoformat()

    response = client.get(URL, params={"as_of": FUTURE_AS_OF.isoformat()})
    assert response.status_code == 422

    # -- 4. Identity: the proof also runs as a NON-ADMIN ----------------
    # Second pass after a full reload: the page boots the production
    # way — the token it would take from the URL hash as
    # #access_token=... (the bare development token), with
    # /api/auth/config answering publicly and /api/auth/me resolving
    # the non-admin viewer for that user.
    dev_token = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")
    viewer = TestClient(app, headers={"Authorization": f"Bearer {dev_token}"})
    assert anon.get("/api/auth/config").status_code == 200  # public, pre-token
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == "dev-user-1"
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]
    # The admin-only control is absent: the route module references no
    # admin dependency and the route stays in the schema, so the
    # non-admin viewer reads the expedited lines and the page still
    # renders.
    assert "require_admin" not in inspect.getsource(expedite_api)
    assert URL in app.openapi()["paths"]
    response = viewer.get(URL, params=ROUTE_PARAMS)
    assert response.status_code == 200, response.text
    assert response.json() == _as_json(EXPECTED)
    # The gate is intact: unauthenticated /api traffic is still 401.
    assert anon.get(URL).status_code == 401
