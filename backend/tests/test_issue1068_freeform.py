"""Issue #1068 proof: the high-risk suppliers on the triage list.

``GET /api/exceptions`` gains the supplier risks as its twelfth
source, listed "supplier_risk" when it fails: the route calls
``app.recommend.supplier_risk.supplier_risk`` once per call (the
name imported into ``app.api.exceptions``, the stand-in this proof
replaces).  One row per entry whose ``risk`` is "high": kind
"supplier_risk_high" (severity medium), the key
"supplier_risk_high:<supplier_code>", ``item_code`` and
``site_code`` null, title "High-risk supplier", the detail
"<name> is the only supplier of <N> item(s) and <reasons>." (the
name the entry's ``supplier_name``, else its ``supplier_code``, N
the entry's ``sole_source_items``, written "item" when it is 1, and
the reasons "delivers late" when the entry is ``late`` and "has
variable lead times" when it is ``variable``, joined by " and "
when both), view "#/suppliers" and ``detected_on`` the answer's
``as_of``.  The kind stands directly after "cross_erp_opportunity"
in ``_KIND_ORDER`` — the last kind.  A failure of the rating lists
"supplier_risk" in ``sources_failed`` with the other sources still
answering, status 200; a "medium" or "low" entry contributes no
row.  Every other kind, source and field is unchanged.

Everything is read through the app's test client the way
``test_issue1032_freeform`` reads the triage list, with
``supplier_risk`` in ``app.api.exceptions`` replaced by a stand-in
answering fixed entries:

  * raised — a "high" entry for ``4870`` named "Westerly Good
    Foods", variable but not late, with 12 sole-source items, gives
    one ``supplier_risk_high`` row with the key, severity, null
    codes, title, view, ``detected_on`` (the stand-in's ``as_of``)
    and the detail "Westerly Good Foods is the only supplier of 12
    items and has variable lead times.";
  * both reasons — a "high" entry that is late and variable, with
    1 sole-source item and no display name, reads "5123 is the only
    supplier of 1 item and delivers late and has variable lead
    times." (the name falls back to the supplier code);
  * not raised — "medium" and "low" entries give no
    ``supplier_risk_high`` row;
  * a failure — with the stand-in raising, "supplier_risk" is in
    ``sources_failed`` and the answer is 200;
  * the order — the kind stands directly after
    "cross_erp_opportunity" in ``_KIND_ORDER``.
"""

import app.api.exceptions as exceptions

URL = "/api/exceptions"

# The stand-in's fixed answer as-of: every row's detected_on is this
# date, whatever the other sources report.
AS_OF = "2026-09-30"


def _entry(
    supplier_code,
    supplier_name,
    risk,
    late,
    variable,
    sole_source_items,
):
    """One fixed entry, in the shape ``supplier_risk`` answers."""
    return {
        "supplier_code": supplier_code,
        "receipts": 6,
        "lead_time_mean_days": 20.0,
        "lead_time_std_days": 12.65,
        "lead_time_cv": 0.63,
        "on_time_rate": 0.4 if late else 0.9,
        "sole_source_items": sole_source_items,
        "sole_source_item_codes": [
            f"ITEM-{supplier_code}-{i}" for i in range(sole_source_items)
        ],
        "late": late,
        "variable": variable,
        "sole_source": sole_source_items > 0,
        "risk": risk,
        "vendor_code": f"V-{supplier_code}",
        "supplier_name": supplier_name,
    }


class _StandIn:
    """The stand-in for supplier_risk: every call is counted, the
    fixed answer is handed back unchanged."""

    calls = 0

    def __init__(self, suppliers):
        self._answer = {"as_of": AS_OF, "suppliers": suppliers}

    def __call__(self, *args, **kwargs):
        _StandIn.calls += 1
        return self._answer


def _answer_rows(body):
    return [row for row in body["rows"] if row["kind"] == "supplier_risk_high"]


def test_issue1068_freeform(client, monkeypatch):
    # -- Raised: the high entry, variable but not late, 12
    #    sole-source items — one row, every field as stated. -------
    _StandIn.calls = 0
    monkeypatch.setattr(
        exceptions,
        "supplier_risk",
        _StandIn(
            [
                _entry(
                    "4870",
                    "Westerly Good Foods",
                    "high",
                    late=False,
                    variable=True,
                    sole_source_items=12,
                )
            ]
        ),
    )
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "supplier_risk" not in body["sources_failed"]
    assert _StandIn.calls == 1, "supplier_risk is called once per call"
    flagged = _answer_rows(body)
    assert len(flagged) == 1
    row = flagged[0]
    assert row["key"] == "supplier_risk_high:4870"
    assert row["severity"] == "medium"
    assert row["item_code"] is None
    assert row["site_code"] is None
    assert row["title"] == "High-risk supplier"
    assert row["view"] == "#/suppliers"
    assert row["detected_on"] == AS_OF
    assert row["detail"] == (
        "Westerly Good Foods is the only supplier of 12 items and "
        "has variable lead times."
    )

    # -- Both reasons: late and variable, 1 sole-source item, no
    #    display name — the name is the supplier code. -------------
    monkeypatch.setattr(
        exceptions,
        "supplier_risk",
        _StandIn(
            [
                _entry(
                    "5123",
                    None,
                    "high",
                    late=True,
                    variable=True,
                    sole_source_items=1,
                )
            ]
        ),
    )
    body = client.get(URL).json()
    flagged = _answer_rows(body)
    assert len(flagged) == 1
    assert flagged[0]["key"] == "supplier_risk_high:5123"
    assert flagged[0]["detail"] == (
        "5123 is the only supplier of 1 item and delivers late and "
        "has variable lead times."
    )

    # -- Not raised: medium and low entries give no row. -----------
    monkeypatch.setattr(
        exceptions,
        "supplier_risk",
        _StandIn(
            [
                _entry(
                    "6001",
                    "Steady State Mills",
                    "medium",
                    late=True,
                    variable=False,
                    sole_source_items=0,
                ),
                _entry(
                    "6002",
                    "Quiet Corner Grocers",
                    "low",
                    late=False,
                    variable=False,
                    sole_source_items=3,
                ),
            ]
        ),
    )
    body = client.get(URL).json()
    assert "supplier_risk" not in body["sources_failed"]
    assert _answer_rows(body) == []

    # -- A failure: the stand-in raises — status 200, "supplier_risk"
    #    in sources_failed, no row of the kind. ----------------------
    def _raising_supplier_risk(*args, **kwargs):
        raise RuntimeError("the supplier risk rating is down")

    monkeypatch.setattr(exceptions, "supplier_risk", _raising_supplier_risk)
    response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert "supplier_risk" in body["sources_failed"]
    assert _answer_rows(body) == []

    # -- The order: the kind stands directly after
    #    cross_erp_opportunity — the last kind. ---------------------
    assert (
        exceptions._KIND_ORDER["supplier_risk_high"]
        == exceptions._KIND_ORDER["cross_erp_opportunity"] + 1
    )
