"""Issue #1032 proof: the cross-ERP opportunity on the triage list.

``GET /api/exceptions`` gains the cross-ERP opportunities as its
eleventh source, listed "across" when it fails: the stored item
mappings are read once per call through the very same
``ErpCodeMappingPostgresRepository.list(kind="item")`` the
cross-ERP comparison reads, and only when at least one is stored
does the route call ``app.recommend.cross_erp.cross_erp_items``
(once per call).  One row per answer row whose ``opportunity`` is
true: kind "cross_erp_opportunity" (severity medium), the key
"cross_erp_opportunity:<source_code>:<target_code>", the short
side's ``item_code``, ``site_code`` null, title "Short in one ERP,
excess in the other", the detail "<short source> is short of
<short code>, while <excess source> holds excess of <excess code>.
AIERP does not move stock between ERPs." (each source the side's
``source_system``, or "an ERP" when it is null), view "#/across"
and ``detected_on`` the answer's ``as_of``.  The kind stands
directly after "erp_currency_mismatch" in ``_KIND_ORDER`` — the
last kind.  A failure of the mapping read or of
``cross_erp_items`` lists "across" in ``sources_failed`` with the
other sources still answering, status 200; with no item mapping
stored the route calls nothing and adds no row.  Every other kind,
source and field is unchanged.

Everything is exercised against the test database through the app's
test client the way ``test_issue1022_freeform`` seeds its data:
item-sites and ``on_hand`` through their repositories'
``upsert_many``, demand events and lead-time observations through
the database, and the one stored item mapping through
``PUT /api/erp-mappings``.  ``date.today`` is pinned (2026-10-02)
and the demand ends on 2026-09-30, so the shared default as-of is
2026-09-30 whatever the calendar says; the replenishment demand
basis is the forecast (the stored system value), so both
constant-history item-sites keep their history figures (2.0 a day).

Proved here:

  * flagged — the source code ``SHORT-1`` at ``154`` under
    ``acumatica`` runs short (5 on hand) while the target code
    ``EXC-1`` at ``WH`` under ``odoo`` holds excess (1000 on hand),
    the item mapping ``SHORT-1`` to ``EXC-1`` stored: exactly one
    ``cross_erp_opportunity`` row is listed, "across" is not among
    the failed sources, and the row carries the key, severity,
    ``item_code``, null ``site_code``, title, view, ``detected_on``
    and detail above;
  * not flagged — both sides healthy (30 on hand each, as in
    ``test_issue1022_freeform``'s different-code pair): no
    ``cross_erp_opportunity`` row is listed;
  * nothing stored — with no item mapping stored,
    ``cross_erp_items`` is never called (checked by replacing it
    with a counting stand-in) and no such row is listed;
  * a failure — with ``cross_erp_items`` replaced by one that
    raises, "across" is in ``sources_failed`` and the answer is
    200;
  * the order — the kind stands directly after
    "erp_currency_mismatch" in ``_KIND_ORDER``.
"""

from datetime import date, timedelta

from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
from app.db.connection import get_connection
from app.main import app
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository

ADMIN_TOKEN = "dev-secret-token:admin"

URL = "/api/exceptions"
MAPPINGS_URL = "/api/erp-mappings"

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)

# (item_code, site_code, source_system, on_hand): the source code is
# short under acumatica (5 on hand, like the short WH side of
# test_issue1022_freeform) and the target code holds excess under
# odoo (1000 on hand, like the excess 154 side — 280 excess units
# over the 720 units demanded in the 360-day excess window).
_ITEM_SITES = [
    ("SHORT-1", "154", "acumatica", 5.0),
    ("EXC-1", "WH", "odoo", 1000.0),
]
# Both sides healthy: the on hand test_issue1022_freeform's
# different-code pair keeps (no stockout, no excess at 30).
HEALTHY_ON_HAND = 30.0

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)


def _demand(cur, item, site, days=360, quantity=2.0):
    """``days`` of ``quantity`` units a day for one pair, ending on the
    as-of (the 360-day statistics window, 12 buckets of 60 each)."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-1032-{item}-{site}-{i}",
                f"de-1032-{item}-{site}-{i}",
                item,
                site,
                quantity,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(days)
        ],
    )


def _lead_times(cur, item):
    """Three lead-time observations of 8, 10 and 12 days: mean 10,
    sample std 2, not low confidence (as in test_issue1022_freeform)."""
    for i, (ordered, received) in enumerate(
        (
            ("2026-08-22", "2026-08-30"),
            ("2026-08-20", "2026-08-30"),
            ("2026-08-18", "2026-08-30"),
        )
    ):
        cur.execute(
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                f"id-lt-1032-{item}-{i}",
                f"lt-1032-{item}-{i}",
                f"SUP-1032-{item}",
                item,
                ordered,
                received,
                "test",
            ),
        )


def _fixed_date():
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    return _FixedDate


def _seed() -> None:
    """The rows through the real repositories (the mapping through the
    API, below)."""
    ItemSitePostgresRepository().upsert_many(
        [
            {
                "item_code": item,
                "site_code": site,
                "unit_cost": 1.0,
                "source_system": system,
            }
            for item, site, system, _on_hand in _ITEM_SITES
        ]
    )
    OnHandPostgresRepository().upsert_many(
        [
            {
                "item_code": item,
                "site_code": site,
                "quantity_on_hand": on_hand,
                "as_of": AS_OF,
                "source_system": system,
            }
            for item, site, system, on_hand in _ITEM_SITES
        ]
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item, site, _system, _on_hand in _ITEM_SITES:
                _demand(cur, item, site)
            for item, _site, _system, _on_hand in _ITEM_SITES:
                _lead_times(cur, item)
        conn.commit()
    finally:
        conn.close()


def _put_mapping(client, source, target):
    response = client.put(
        MAPPINGS_URL,
        json={
            "kind": "item",
            "target_system": "odoo",
            "source_code": source,
            "target_code": target,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _opportunity_rows(body):
    return [row for row in body["rows"] if row["kind"] == "cross_erp_opportunity"]


def test_issue1032_freeform(client, monkeypatch):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    monkeypatch.delenv("EXCESS_COVER_MONTHS", raising=False)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    _seed()

    # -- The forecast basis (the stored system value). -------------------
    response = admin.put(
        "/api/settings/replenishment.demand_basis", json={"value": "forecast"}
    )
    assert response.status_code == 200, response.text

    # -- The one stored item mapping: the short source code to the
    #    excess target code. ---------------------------------------------
    mapping_id = _put_mapping(admin, "SHORT-1", "EXC-1")

    # -- Flagged: the source code is short under acumatica and the
    #    target code holds excess under odoo. -----------------------------
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "across" not in body["sources_failed"]
    flagged = _opportunity_rows(body)
    assert len(flagged) == 1
    row = flagged[0]
    assert row["key"] == "cross_erp_opportunity:SHORT-1:EXC-1"
    assert row["severity"] == "medium"
    assert row["item_code"] == "SHORT-1"
    assert row["site_code"] is None
    assert row["title"] == "Short in one ERP, excess in the other"
    assert row["view"] == "#/across"
    assert row["detected_on"] == AS_OF_STR
    assert row["detail"] == (
        "Acumatica is short of SHORT-1, while Odoo holds excess of "
        "EXC-1. Up to 22 units could move. AIERP does not move "
        "stock between ERPs."
    )

    # -- Not flagged: both sides healthy — no cross_erp_opportunity row.
    OnHandPostgresRepository().upsert_many(
        [
            {
                "item_code": item,
                "site_code": site,
                "quantity_on_hand": HEALTHY_ON_HAND,
                "as_of": AS_OF,
                "source_system": system,
            }
            for item, site, system, _on_hand in _ITEM_SITES
        ]
    )
    assert _opportunity_rows(client.get(URL).json()) == []

    # -- Nothing stored: with no item mapping stored, cross_erp_items is
    #    never called (the counting stand-in would record it) and no
    #    such row is listed. -----------------------------------------------
    deleted = admin.delete(f"{MAPPINGS_URL}/{mapping_id}")
    assert deleted.status_code == 204, deleted.text

    class CountingCrossErp:
        """The counting stand-in for cross_erp_items: every call is
        recorded, and a call is itself a failure of the proof."""

        calls = 0

        def __call__(self, as_of=None):
            CountingCrossErp.calls += 1
            raise AssertionError(
                "cross_erp_items must not be called with no stored item mapping"
            )

    monkeypatch.setattr(exceptions, "cross_erp_items", CountingCrossErp())
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert CountingCrossErp.calls == 0
    assert _opportunity_rows(response.json()) == []

    # -- A failure: cross_erp_items raises — status 200, "across" in
    #    sources_failed, the other sources still answering. --------------
    _put_mapping(admin, "SHORT-1", "EXC-1")

    def _raising_cross_erp(as_of=None):
        raise RuntimeError("the cross-ERP comparison is down")

    monkeypatch.setattr(exceptions, "cross_erp_items", _raising_cross_erp)
    response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert "across" in body["sources_failed"]
    assert _opportunity_rows(body) == []

    # -- The order: the kind stands directly after erp_currency_mismatch.
    assert (
        exceptions._KIND_ORDER["cross_erp_opportunity"]
        == exceptions._KIND_ORDER["erp_currency_mismatch"] + 1
    )
