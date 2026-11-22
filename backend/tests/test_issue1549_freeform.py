"""Issue #1549 proof: every release line carries its source.

AC-1 — the release lines: each line of a release group of
``app.supply.release.order_plan_release`` gains a ``source`` — a due
line the plan line's own source (``buffer`` or ``reorder_point``) and
a chosen top-up's line ``top_up`` — while the lines' other fields and
their order, the groups, the unreleasable list and the total answer
exactly as before this slice.  Proved here read through the app's
test client the way ``tests/test_issue1191_freeform.py`` proves the
top-ups: the same names stood in —
``app.api.supply_plan.build_supply_plan`` (one Acumatica supplier
with three plan lines at one site — B from its buffer's order signal
and R and S from their reorder points — and, at the look-ahead
horizon, the chosen top-up T as the supplier's one extra due line,
the way 1191's stand-in holds its chosen top-ups),
``app.supply.release._data_as_of`` (a fixed data date, never the
server clock) and the Acumatica client's
``preview_draft_purchase_orders`` and ``create_draft_purchase_orders``
(recording fakes, the way 1191 and 842 replace them).

AC-2 — the line model: ``ReleaseLine`` gains ``source: str | None =
None`` and a null source stays out of the answer — the
``model_serializer(mode="wrap")`` the way the plan line's null
``supplier_reason`` does in ``app.api.supply_plan`` — so two things
answer exactly as before: the aggregate release
(``POST /api/aggregate/release``, previewed here for one month's
planned orders with ``planned_orders`` stood in the way
``tests/test_issue538_freeform.py`` stands it in) whose lines carry
no ``source`` key, and the older row — a ``plan_releases`` row
inserted here with lines that carry no source — which the audit reads
back without the key on its lines, while every line with a source
shows it: the order plan release's preview and confirm and the rows
the audit answers for releases stored from now on.  ``backend/
openapi.json`` carries the field and the Acumatica order is
unchanged: every order the client was given, preview and confirm,
carries lines of exactly ``item_code``, ``quantity`` and
``unit_cost``, no source.

AC-4 — the preview (an admin, ``confirm`` false): the group's lines
are B, R, S and T in item order, with the sources ``buffer``,
``reorder_point``, ``reorder_point`` and ``top_up``, each line's
item, quantity, unit cost and value as before this slice.

AC-5 — the confirm (an admin, ``confirm`` true, the writes switched
on the way 538's proof switches them on): the stored
``plan_releases`` row's lines hold the four lines with the same
sources, and ``GET /api/aggregate/release/audit`` answers that row
with the four sources.  Every row the proof inserts is deleted in a
``finally``.
"""

import json
import os
from datetime import date
from decimal import Decimal

import psycopg2
from fastapi.testclient import TestClient

import app.aggregate.release as aggregate_release
import app.api.supply_plan as supply_plan
import app.supply.release as release_module
from app.db.connection import get_connection_params
from app.feed.acumatica_client import (
    AcumaticaClient,
    AcumaticaWritesDisabledError,
)
from app.main import app
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)

URL = "/api/supply-plan/release"
MONTH_URL = "/api/aggregate/release"
AUDIT = "/api/aggregate/release/audit"

ACUMATICA_VARS = (
    "ACUMATICA_BASE_URL",
    "ACUMATICA_TENANT",
    "ACUMATICA_USERNAME",
    "ACUMATICA_PASSWORD",
    "ACUMATICA_ENDPOINT_VERSION",
    "ACUMATICA_CA_BUNDLE",
    "ACUMATICA_TIMEOUT_SECONDS",
    "ACUMATICA_ALLOW_INSECURE",
    "ACUMATICA_ALLOW_WRITES",
)

ADMIN_TOKEN = "dev-secret-token:admin"

# The release's as-of: the fixed answer of the release module's
# _data_as_of seam — never the server date.
AS_OF = date(2026, 11, 10)
AS_OF_STR = AS_OF.isoformat()
AS_OF_MONTH = "2026-11"

SUPPLIER = "S-1549"
SITE = "154"
ITEM_B = "B"
ITEM_R = "R"
ITEM_S = "S"
ITEM_T = "T"
ORIGIN = f"AIERP order plan {AS_OF_STR} site {SITE} vendor {SUPPLIER}"
TOP_UP_T = {"supplier_code": SUPPLIER, "item_code": ITEM_T, "site_code": SITE}

# The release's four lines: the due lines B, R and S in the stand-in
# plan's order, then the chosen top-up T — items in order, the
# monetary fields exact decimal strings, and the new source.
LINE_B = {
    "item_code": ITEM_B,
    "quantity": 4,
    "unit_cost": "100.0",
    "value": "400.0",
    "source": "buffer",
}
LINE_R = {
    "item_code": ITEM_R,
    "quantity": 2,
    "unit_cost": "50.0",
    "value": "100.0",
    "source": "reorder_point",
}
LINE_S = {
    "item_code": ITEM_S,
    "quantity": 3,
    "unit_cost": "25.0",
    "value": "75.0",
    "source": "reorder_point",
}
LINE_T = {
    "item_code": ITEM_T,
    "quantity": 6,
    "unit_cost": "10.0",
    "value": "60.0",
    "source": "top_up",
}
FOUR_LINES = [dict(LINE_B), dict(LINE_R), dict(LINE_S), dict(LINE_T)]
GROUP_VALUE = "635.0"

# The wire format the release body sends to Acumatica: the same four
# lines, exactly item_code, quantity and unit_cost — no source.
WIRE_ORDER = {
    "site_id": SITE,
    "vendor_id": SUPPLIER,
    "origin": ORIGIN,
    "lines": [
        {"item_code": ITEM_B, "quantity": 4, "unit_cost": 100.0},
        {"item_code": ITEM_R, "quantity": 2, "unit_cost": 50.0},
        {"item_code": ITEM_S, "quantity": 3, "unit_cost": 25.0},
        {"item_code": ITEM_T, "quantity": 6, "unit_cost": 10.0},
    ],
}


def _body(outcome, order_nbr):
    """One expected order-plan release answer for the one group."""
    return {
        "month": AS_OF_MONTH,
        "groups": [
            {
                "site_code": SITE,
                "supplier_code": SUPPLIER,
                "origin": ORIGIN,
                "lines": FOUR_LINES,
                "value": GROUP_VALUE,
                "outcome": outcome,
                "warehouse_code": "WHOLESALE",
                "vendor_code": "CONPERIPH",
                "order_nbr": order_nbr,
                "error": None,
            }
        ],
        "unreleasable": [],
        "total_value": GROUP_VALUE,
        "acumatica_available": True,
    }


EXPECTED_PREVIEW = _body("would_create", None)
EXPECTED_CONFIRM = _body("created", "PO-1549-001")


def _stand_in_line(item_code, due_in_days, quantity, unit_cost, value, source):
    """One line of the stand-in plan, every field the two consumers
    read (the release's and top_ups_by_supplier's) filled."""
    return {
        "item_code": item_code,
        "site_code": SITE,
        "position_units": 0,
        "reorder_point_units": 10,
        "daily_demand": 10.0,
        "due_in_days": due_in_days,
        "order_by": AS_OF,
        "lead_time_days": 10,
        "arrives_on": AS_OF,
        "raw_quantity": quantity,
        "quantity": quantity,
        "adjustments": None,
        "short_of_need": None,
        "terms": None,
        "unit_cost": unit_cost,
        "value": value,
        "arithmetic": None,
        "supplier_reason": None,
        "source": source,
    }


def _stand_in_plan(horizon_days):
    """One fixed plan per horizon: at 7, the supplier of acumatica
    with a minimum of 1000.00 and a shortfall of 600.00 and the due
    lines B (buffer), R and S (reorder point) at site 154; at 30, the
    same plus the supplier's top-up candidate T (due in 12 days, 6
    units at 10.00) — the way 1191's stand-in holds its chosen
    top-ups at the look-ahead horizon."""
    lines = [
        _stand_in_line(ITEM_B, 0, 4, 100.0, Decimal("400.00"), "buffer"),
        _stand_in_line(ITEM_R, 1, 2, 50.0, Decimal("100.00"), "reorder_point"),
        _stand_in_line(ITEM_S, 2, 3, 25.0, Decimal("75.00"), "reorder_point"),
    ]
    if horizon_days == 30:
        lines.append(
            _stand_in_line(ITEM_T, 12, 6, 10.0, Decimal("60.00"), "reorder_point")
        )
    total_value = sum(line["value"] for line in lines)
    return {
        "as_of": AS_OF,
        "horizon_days": horizon_days,
        "line_count": len(lines),
        "total_value": total_value,
        "suppliers": [
            {
                "supplier_code": SUPPLIER,
                "source_system": "acumatica",
                "vendor_code": "V-1549",
                "name": "Acme Peripherals",
                "line_count": len(lines),
                "total_value": total_value,
                "minimum": Decimal("1000.00"),
                "shortfall": Decimal("600.00"),
                "lines": lines,
            }
        ],
    }


def _shift_month(delta: int) -> str:
    """The YYYY-MM of the current month shifted by ``delta`` months."""
    today = date.today()
    index = today.year * 12 + (today.month - 1) + delta
    year, month = divmod(index, 12)
    return f"{year:04d}-{month + 1:02d}"


def _plan_release_rows() -> list[dict]:
    """Every stored plan_releases row, oldest first, every column."""
    conn = psycopg2.connect(**get_connection_params())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM plan_releases ORDER BY id")
            columns = [column[0] for column in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def test_issue1549_freeform(monkeypatch, client) -> None:
    # Isolate the Acumatica environment, complete configuration,
    # writes on — the way 538's proof switches the writes on.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", "https://acumatica.example.com/Instance")
    monkeypatch.setenv("ACUMATICA_TENANT", "AIERP")
    monkeypatch.setenv("ACUMATICA_USERNAME", "svc-aierp")
    monkeypatch.setenv("ACUMATICA_PASSWORD", "s3cret-password!")
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "1")

    # --- The Acumatica client, replaced with fakes recording their
    # calls, the way 1191 and 842 replace it.
    preview_calls: list[list] = []
    create_calls: list[list] = []

    def _writes_enabled() -> bool:
        return os.environ.get("ACUMATICA_ALLOW_WRITES", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    def fake_preview(self, orders):
        preview_calls.append([dict(entry) for entry in orders])
        if not orders:
            return []
        return [
            {
                "origin": entry["origin"],
                "outcome": "would_create",
                "order_nbr": None,
                "status": None,
                "warehouse_code": "WHOLESALE",
                "vendor_code": "CONPERIPH",
                "line_count": len(entry["lines"]),
                "error": None,
            }
            for entry in orders
        ]

    def fake_create(self, orders):
        if not _writes_enabled():
            raise AcumaticaWritesDisabledError(
                "Acumatica writes are disabled; set ACUMATICA_ALLOW_WRITES "
                "to a truthy value to enable purchase order creation"
            )
        if not orders:
            return []
        create_calls.append([dict(entry) for entry in orders])
        return [
            {
                "origin": entry["origin"],
                "outcome": "created",
                "order_nbr": f"PO-1549-{len(create_calls):03d}",
                "status": "On Hold",
                "warehouse_code": "WHOLESALE",
                "vendor_code": "CONPERIPH",
                "line_count": len(entry["lines"]),
                "error": None,
            }
            for entry in orders
        ]

    monkeypatch.setattr(AcumaticaClient, "preview_draft_purchase_orders", fake_preview)
    monkeypatch.setattr(AcumaticaClient, "create_draft_purchase_orders", fake_create)

    # --- The seams, the names 1191 stands in for: the excess list
    # answers nothing; the release's data as-of answers a fixed date;
    # the plan builder is a stand-in recording each call's horizon
    # (and as-of) and answering one fixed plan per horizon.
    build_calls: list[tuple] = []

    def fake_build_supply_plan(horizon_days, as_of=None):
        build_calls.append((horizon_days, as_of))
        return _stand_in_plan(horizon_days)

    monkeypatch.setattr(supply_plan, "excess_ranked", lambda as_of=None: [])
    monkeypatch.setattr(release_module, "_data_as_of", lambda: AS_OF)
    monkeypatch.setattr(supply_plan, "build_supply_plan", fake_build_supply_plan)

    # The aggregate release's seams, the way 538 stands them in: one
    # planned order of the tested month and its supplier from the
    # purchase-line history.
    month = _shift_month(0)
    month_origin = f"AIERP plan {month} site 159 vendor 4851"

    def planned(item, site, placed, quantity, unit_cost):
        return {
            "item_code": item,
            "site_code": site,
            "placed_month": placed,
            "arrival_month": placed,
            "quantity": quantity,
            "unit_cost": unit_cost,
            "value": quantity * (unit_cost if unit_cost is not None else 0),
            "d": 1.0,
            "month_end_on_hand": 0,
        }

    def fake_planned_orders(
        as_of=None,
        months=6,
        service_level=None,
        demand_multiplier=None,
        lead_time_multiplier=None,
    ):
        return [planned("A", "159", month, 10, Decimal("2.0"))]

    history = [
        {
            "item_code": "A",
            "site_code": "159",
            "supplier_code": "4851",
            "ordered_on": date(2026, 7, 15),
            "business_key": "A-1",
        },
    ]

    def fake_list_latest(self):
        return [dict(row) for row in history]

    monkeypatch.setattr(aggregate_release, "planned_orders", fake_planned_orders)
    monkeypatch.setattr(
        OpenPurchaseLinePostgresRepository, "list_latest", fake_list_latest
    )

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    before_ids = {row["id"] for row in _plan_release_rows()}
    try:
        # --- The preview (an admin, confirm false): the group's
        # lines are B, R, S and T in item order, with the sources
        # buffer, reorder_point, reorder_point and top_up — and
        # everything else of the answer as before this slice.
        preview = admin.post(
            URL, json={"supplier_codes": [SUPPLIER], "top_ups": [TOP_UP_T]}
        )
        assert preview.status_code == 200, preview.text
        assert preview.json() == EXPECTED_PREVIEW
        group = preview.json()["groups"][0]
        assert [line["item_code"] for line in group["lines"]] == [
            ITEM_B,
            ITEM_R,
            ITEM_S,
            ITEM_T,
        ]
        assert [line["source"] for line in group["lines"]] == [
            "buffer",
            "reorder_point",
            "reorder_point",
            "top_up",
        ]
        # The stand-in was asked for the release's own horizon build,
        # then top_ups_by_supplier's horizon build at the release's
        # as-of and its look-ahead build (the default 30).
        assert build_calls == [(7, AS_OF), (7, AS_OF), (30, AS_OF)]
        assert preview_calls == [[WIRE_ORDER]]

        # --- The confirm (an admin, confirm true, writes on): the
        # group comes back created, and the stored row's lines hold
        # the four lines with the same sources.
        confirm = admin.post(
            URL,
            json={"supplier_codes": [SUPPLIER], "top_ups": [TOP_UP_T], "confirm": True},
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json() == EXPECTED_CONFIRM
        assert create_calls == [[WIRE_ORDER]]
        assert build_calls == [(7, AS_OF), (7, AS_OF), (30, AS_OF)] * 2

        rows = _plan_release_rows()
        assert len(rows) == 1
        row = rows[0]
        assert row["month"] == AS_OF_MONTH
        assert row["site_code"] == SITE
        assert row["supplier_code"] == SUPPLIER
        assert row["origin"] == ORIGIN
        assert row["outcome"] == "created"
        assert row["order_nbr"] == "PO-1549-001"
        assert row["warehouse_code"] == "WHOLESALE"
        assert row["vendor_code"] == "CONPERIPH"
        assert row["line_count"] == 4
        assert row["value"] == Decimal(GROUP_VALUE)
        assert row["lines"] == FOUR_LINES
        assert row["actor"] == "admin"
        assert row["error"] is None

        # --- An older row: one inserted with lines that carry no
        # source — the snapshot stored before this slice.
        old_origin = f"AIERP order plan 2026-10-01 site {SITE} vendor S-OLD"
        old_line = {
            "item_code": "X",
            "quantity": 10,
            "unit_cost": "2.0",
            "value": "20.0",
        }
        conn = psycopg2.connect(**get_connection_params())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO plan_releases "
                    "(month, site_code, supplier_code, origin, outcome, "
                    "line_count, value, lines, actor) "
                    "VALUES (%s, %s, %s, %s, 'created', %s, %s, %s::jsonb, %s) "
                    "RETURNING id",
                    (
                        AS_OF_MONTH,
                        SITE,
                        "S-OLD",
                        old_origin,
                        1,
                        Decimal("20.0"),
                        json.dumps([old_line]),
                        "admin",
                    ),
                )
                assert cur.fetchone() is not None
            conn.commit()
        finally:
            conn.close()

        # --- The audit: the stored row answers with the four
        # sources, and the older row reads back with no source key on
        # its lines.
        audit = admin.get(AUDIT)
        assert audit.status_code == 200, audit.text
        audit_rows = {entry["origin"]: entry for entry in audit.json()}
        assert set(audit_rows) == {ORIGIN, old_origin}
        confirmed = audit_rows[ORIGIN]
        assert confirmed["lines"] == FOUR_LINES
        assert [line["source"] for line in confirmed["lines"]] == [
            "buffer",
            "reorder_point",
            "reorder_point",
            "top_up",
        ]
        assert confirmed["value"] == GROUP_VALUE
        assert confirmed["line_count"] == 4
        assert confirmed["actor"] == "admin"
        older = audit_rows[old_origin]
        assert older["lines"] == [old_line]
        for line in older["lines"]:
            assert "source" not in line

        # --- The aggregate release: a preview of one month's planned
        # orders answers lines with no source key — the null source
        # stays out of the response.
        month_response = admin.post(MONTH_URL, json={"month": month})
        assert month_response.status_code == 200, month_response.text
        month_body = month_response.json()
        assert month_body["month"] == month
        assert month_body["acumatica_available"] is True
        assert month_body["groups"] == [
            {
                "site_code": "159",
                "supplier_code": "4851",
                "origin": month_origin,
                "lines": [
                    {
                        "item_code": "A",
                        "quantity": 10,
                        "unit_cost": "2.0",
                        "value": "20.0",
                    }
                ],
                "value": "20.0",
                "outcome": "would_create",
                "warehouse_code": "WHOLESALE",
                "vendor_code": "CONPERIPH",
                "order_nbr": None,
                "error": None,
            }
        ]
        assert month_body["unreleasable"] == []
        assert month_body["total_value"] == "20.0"
        for line in month_body["groups"][0]["lines"]:
            assert "source" not in line
        # A preview writes no row.
        assert [row["id"] for row in _plan_release_rows()] == sorted(
            before_ids | {row["id"] for row in rows} | {older["id"]}
        )
    finally:
        # Every row the proof inserted is deleted.
        leftover = [
            row["id"] for row in _plan_release_rows() if row["id"] not in before_ids
        ]
        if leftover:
            conn = psycopg2.connect(**get_connection_params())
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM plan_releases WHERE id = ANY(%s)", (leftover,)
                    )
                conn.commit()
            finally:
                conn.close()

    # The Acumatica order is unchanged: every order the client was
    # given — the preview's, the confirm's and the month preview's —
    # carries lines of exactly item_code, quantity and unit_cost, no
    # source.
    for orders in [*preview_calls, *create_calls]:
        for order in orders:
            assert set(order) == {"site_id", "vendor_id", "origin", "lines"}
            for line in order["lines"]:
                assert set(line) == {"item_code", "quantity", "unit_cost"}
    assert preview_calls[-1] == [
        {
            "site_id": "159",
            "vendor_id": "4851",
            "origin": month_origin,
            "lines": [{"item_code": "A", "quantity": 10, "unit_cost": 2.0}],
        }
    ]
    assert create_calls == [[WIRE_ORDER]]
