"""Issue #1043 proof: the cross-ERP opportunity's names and units.

The ``cross_erp_opportunity`` detail of ``GET /api/exceptions``
reads the sides' source systems by name — "Acumatica" for
``acumatica`` and "Odoo" for ``odoo``, compared ignoring case, any
other non-empty string as stored, "an ERP" when the side stores
none — and, when the comparison row's ``suggested_move_units`` is
an integer above 0, carries the "Up to <N> units could move."
sentence before the last one, N with thousands separators.  With
the figure 0 or null the sentence is left out and the rest reads as
before, with the names.

Proved against the test database through the app's test client:
``cross_erp_items`` in ``app.api.exceptions`` is replaced with a
stand-in answering one fixed opportunity row (the short side
``SHORT-1``, the excess side ``EXC-1``) and one item mapping is
stored so the source runs.  The detail reads:

  * names and units — the short side under ``acumatica``, the
    excess side under ``odoo`` and ``suggested_move_units`` 1200:
    "Acumatica is short of SHORT-1, while Odoo holds excess of
    EXC-1. Up to 1,200 units could move. AIERP does not move stock
    between ERPs.";
  * no units — the same row with ``suggested_move_units`` 0, and
    again with it null: "Acumatica is short of SHORT-1, while Odoo
    holds excess of EXC-1. AIERP does not move stock between
    ERPs.";
  * other names — a short side under ``sap`` and an excess side
    with a null ``source_system``: the detail starts "sap is short
    of" and names "an ERP" as holding the excess.
"""

from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
from app.main import app

ADMIN_TOKEN = "dev-secret-token:admin"

URL = "/api/exceptions"
MAPPINGS_URL = "/api/erp-mappings"

AS_OF_STR = "2026-10-02"


def _answer(suggested_move_units, short_system, excess_system):
    """The stand-in's fixed answer: one opportunity row, the short
    side ``SHORT-1`` under *short_system*, the excess side ``EXC-1``
    under *excess_system*."""
    return {
        "as_of": AS_OF_STR,
        "rows": [
            {
                "source_code": "SHORT-1",
                "target_code": "EXC-1",
                "description": None,
                "opportunity": True,
                "short_side": 0,
                "excess_side": 1,
                "suggested_move_units": suggested_move_units,
                "sides": [
                    {
                        "source_system": short_system,
                        "item_code": "SHORT-1",
                        "sites": [],
                        "on_hand_units": 0.0,
                    },
                    {
                        "source_system": excess_system,
                        "item_code": "EXC-1",
                        "sites": [],
                        "on_hand_units": 0.0,
                    },
                ],
            }
        ],
    }


def _stand_in(answer):
    """The stand-in for cross_erp_items: answers *answer*."""

    def _fixed(as_of=None):
        return answer

    return _fixed


def _opportunity_detail(body):
    """The one cross_erp_opportunity row's detail, or a failure when
    the source listed no such row (or more than one)."""
    rows = [row for row in body["rows"] if row["kind"] == "cross_erp_opportunity"]
    assert len(rows) == 1
    return rows[0]["detail"]


def test_issue1043_freeform(client, monkeypatch):
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # -- The one stored item mapping: the source runs. -------------------
    response = admin.put(
        MAPPINGS_URL,
        json={
            "kind": "item",
            "target_system": "odoo",
            "source_code": "SHORT-1",
            "target_code": "EXC-1",
        },
    )
    assert response.status_code == 200, response.text

    # -- Names and units: acumatica short, odoo excess, 1200 units. -----
    monkeypatch.setattr(
        exceptions, "cross_erp_items", _stand_in(_answer(1200, "acumatica", "odoo"))
    )
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "across" not in body["sources_failed"]
    assert _opportunity_detail(body) == (
        "Acumatica is short of SHORT-1, while Odoo holds excess of "
        "EXC-1. Up to 1,200 units could move. AIERP does not move "
        "stock between ERPs."
    )

    # -- No units: suggested_move_units 0 — no "Up to" sentence. --------
    monkeypatch.setattr(
        exceptions, "cross_erp_items", _stand_in(_answer(0, "acumatica", "odoo"))
    )
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert _opportunity_detail(response.json()) == (
        "Acumatica is short of SHORT-1, while Odoo holds excess of "
        "EXC-1. AIERP does not move stock between ERPs."
    )

    # -- No units: suggested_move_units null — no "Up to" sentence. -----
    monkeypatch.setattr(
        exceptions, "cross_erp_items", _stand_in(_answer(None, "acumatica", "odoo"))
    )
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert _opportunity_detail(response.json()) == (
        "Acumatica is short of SHORT-1, while Odoo holds excess of "
        "EXC-1. AIERP does not move stock between ERPs."
    )

    # -- Other names: a short side under sap and an excess side with a
    #    null source_system — as stored and "an ERP". -------------------
    monkeypatch.setattr(
        exceptions, "cross_erp_items", _stand_in(_answer(1200, "sap", None))
    )
    response = client.get(URL)
    assert response.status_code == 200, response.text
    detail = _opportunity_detail(response.json())
    assert detail.startswith("sap is short of")
    assert detail == (
        "sap is short of SHORT-1, while an ERP holds excess of "
        "EXC-1. Up to 1,200 units could move. AIERP does not move "
        "stock between ERPs."
    )
