"""Issue #1089 proof: the sole-source, short-of-stock items on the
triage list.

``GET /api/exceptions`` gains the second sources as its thirteenth
source, listed "second_sources" when it fails: the route calls
``app.recommend.second_source.second_sources`` once per call (the
name imported into ``app.api.exceptions``, the stand-in this proof
replaces).  One row per entry whose ``supplier_risk`` is "high",
whose ``candidates`` hold no candidate with a ``basis`` of "terms"
and whose ``bridge_short_units`` is a number above 0: kind
"sole_source_short", severity "high" when the entry has no
candidates at all and "medium" otherwise, the key
"sole_source_short:<item_code>", the entry's ``item_code`` and
``site_code`` null, title "Sole source, short of stock", the detail
"<item_code> comes only from <name>, rated high risk, and no other
supplier has terms for it. Holding <bridge_units> units would cover
<bridge_days> days; on hand is short by <bridge_short_units>."
(the name the entry's ``supplier_name``, else its
``supplier_code``) plus " Suppliers of similar items to approach:
<N>." when the entry has N candidates, or " No supplier of similar
items found." when it has none, view "#/suppliers" and
``detected_on`` the answer's ``as_of``.  The row is validated
against the exceptions schema before it is returned: the answer
parses as the route's ``ExceptionResponse`` with the row's kind and
required fields fixed.  The kind stands directly after
"supplier_risk_high" in ``_KIND_ORDER`` — the last kind.  A failure
of the candidates read lists "second_sources" in ``sources_failed``
with the other sources still answering, status 200; an entry with a
"terms" candidate, an entry rated "low" and an entry whose
``bridge_short_units`` is 0 or null contribute no row.  Every other
kind, source and field is unchanged.

Everything is read through the app's test client the way
``test_issue1068_freeform`` reads the triage list, with
``second_sources`` in ``app.api.exceptions`` replaced by a stand-in
answering fixed entries:

  * raised, none — a "high" entry for ``ITEM-A`` from ``S-ONLY``
    named "Only Co", with no candidates, ``bridge_units`` 60,
    ``bridge_days`` 60 and ``bridge_short_units`` 45, gives one
    ``sole_source_short`` row with severity "high", the key
    "sole_source_short:ITEM-A", ``item_code`` "ITEM-A", a null
    ``site_code``, the title, the view, ``detected_on`` (the
    stand-in's ``as_of``) and the detail "ITEM-A comes only from
    Only Co, rated high risk, and no other supplier has terms for
    it. Holding 60 units would cover 60 days; on hand is short by
    45. No supplier of similar items found.";
  * raised, similar — the same entry with two "similar" candidates
    gives severity "medium" and a detail ending "Suppliers of
    similar items to approach: 2.";
  * not raised — an entry with a "terms" candidate, an entry rated
    "low", an entry with ``bridge_short_units`` 0 and one with it
    null give no ``sole_source_short`` row;
  * a failure — with the stand-in raising, "second_sources" is in
    ``sources_failed`` and the answer is 200;
  * the order — the kind stands directly after
    "supplier_risk_high" in ``_KIND_ORDER``.
"""

import app.api.exceptions as exceptions

URL = "/api/exceptions"

# The stand-in's fixed answer as-of: every row's detected_on is this
# date, whatever the other sources report.
AS_OF = "2026-09-30"


def _terms_candidate(supplier_code="S-TERMS", supplier_name="Terms Co"):
    """A candidate with a ``basis`` of "terms": one that always keeps
    its entry off the list."""
    return {
        "supplier_code": supplier_code,
        "supplier_name": supplier_name,
        "source_system": "odoo",
        "lead_time_days": 5,
        "min_order_qty": 10,
        "risk": "low",
        "basis": "terms",
        "similar_items": None,
    }


def _similar_candidate(supplier_code, supplier_name, similar_items=4):
    """A candidate with a ``basis`` of "similar"."""
    return {
        "supplier_code": supplier_code,
        "supplier_name": supplier_name,
        "source_system": None,
        "lead_time_days": None,
        "min_order_qty": None,
        "risk": "low",
        "basis": "similar",
        "similar_items": similar_items,
    }


def _entry(
    item_code,
    supplier_code,
    supplier_name,
    supplier_risk,
    candidates,
    bridge_units=60,
    bridge_days=60,
    bridge_short_units=45,
):
    """One fixed entry, in the shape ``second_sources`` answers."""
    return {
        "item_code": item_code,
        "description": None,
        "supplier_code": supplier_code,
        "supplier_name": supplier_name,
        "late": False,
        "variable": False,
        "supplier_risk": supplier_risk,
        "candidates": candidates,
        "daily_demand": 2.0,
        "bridge_days": bridge_days,
        "bridge_basis": "qualification_days",
        "bridge_units": bridge_units,
        "on_hand_units": 15,
        "bridge_short_units": bridge_short_units,
        "bridge_arithmetic": None,
    }


class _StandIn:
    """The stand-in for second_sources: every call is counted, the
    fixed answer is handed back unchanged."""

    calls = 0

    def __init__(self, items):
        self._answer = {"as_of": AS_OF, "items": items}

    def __call__(self, *args, **kwargs):
        _StandIn.calls += 1
        return self._answer


def _answer_rows(body):
    return [row for row in body["rows"] if row["kind"] == "sole_source_short"]


def test_issue1089_freeform(client, monkeypatch):
    # -- Raised, none: the high entry without candidates — one row,
    #    every field as stated. ------------------------------------
    _StandIn.calls = 0
    monkeypatch.setattr(
        exceptions,
        "second_sources",
        _StandIn(
            [
                _entry(
                    "ITEM-A",
                    "S-ONLY",
                    "Only Co",
                    "high",
                    candidates=[],
                )
            ]
        ),
    )
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "second_sources" not in body["sources_failed"]
    assert _StandIn.calls == 1, "second_sources is called once per call"
    flagged = _answer_rows(body)
    assert len(flagged) == 1
    row = flagged[0]
    assert row["key"] == "sole_source_short:ITEM-A"
    assert row["severity"] == "high"
    assert row["item_code"] == "ITEM-A"
    assert row["site_code"] is None
    assert row["title"] == "Sole source, short of stock"
    assert row["view"] == "#/suppliers"
    assert row["detected_on"] == AS_OF
    assert row["detail"] == (
        "ITEM-A comes only from Only Co, rated high risk, and no "
        "other supplier has terms for it. Holding 60 units would "
        "cover 60 days; on hand is short by 45. No supplier of "
        "similar items found."
    )

    # -- The schema: the row is validated against the exceptions
    #    schema before it is returned — the answer parses as the
    #    route's response model with the row's kind and required
    #    fields fixed. ----------------------------------------------
    parsed = exceptions.ExceptionResponse.model_validate(body)
    parsed_rows = [r for r in parsed.rows if r.kind == "sole_source_short"]
    assert len(parsed_rows) == 1
    assert parsed_rows[0].kind == "sole_source_short"
    assert parsed_rows[0].key == "sole_source_short:ITEM-A"

    # -- Raised, similar: two similar candidates — medium severity,
    #    the approach count in the detail. --------------------------
    monkeypatch.setattr(
        exceptions,
        "second_sources",
        _StandIn(
            [
                _entry(
                    "ITEM-A",
                    "S-ONLY",
                    "Only Co",
                    "high",
                    candidates=[
                        _similar_candidate("S-SIM-1", "Similar One"),
                        _similar_candidate("S-SIM-2", "Similar Two"),
                    ],
                )
            ]
        ),
    )
    body = client.get(URL).json()
    flagged = _answer_rows(body)
    assert len(flagged) == 1
    assert flagged[0]["severity"] == "medium"
    assert flagged[0]["detail"].endswith("Suppliers of similar items to approach: 2.")

    # -- Not raised: a terms candidate, a low rating, a zero and a
    #    null bridge short give no row. ------------------------------
    monkeypatch.setattr(
        exceptions,
        "second_sources",
        _StandIn(
            [
                _entry(
                    "ITEM-B",
                    "S-TERMS-HOLDER",
                    "Terms Co",
                    "high",
                    candidates=[_terms_candidate()],
                ),
                _entry(
                    "ITEM-C",
                    "S-LOW",
                    "Low Risk Co",
                    "low",
                    candidates=[],
                ),
                _entry(
                    "ITEM-D",
                    "S-HELD",
                    "Held Stock Co",
                    "high",
                    candidates=[],
                    bridge_short_units=0,
                ),
                _entry(
                    "ITEM-E",
                    "S-NULL",
                    "Null Bridge Co",
                    "high",
                    candidates=[],
                    bridge_short_units=None,
                ),
            ]
        ),
    )
    body = client.get(URL).json()
    assert "second_sources" not in body["sources_failed"]
    assert _answer_rows(body) == []

    # -- A failure: the stand-in raises — status 200,
    #    "second_sources" in sources_failed, no row of the kind. ----
    def _raising_second_sources(*args, **kwargs):
        raise RuntimeError("the second-source candidates are down")

    monkeypatch.setattr(exceptions, "second_sources", _raising_second_sources)
    response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert "second_sources" in body["sources_failed"]
    assert _answer_rows(body) == []

    # -- The order: the kind stands directly after
    #    supplier_risk_high — the last kind. ------------------------
    assert (
        exceptions._KIND_ORDER["sole_source_short"]
        == exceptions._KIND_ORDER["supplier_risk_high"] + 1
    )
