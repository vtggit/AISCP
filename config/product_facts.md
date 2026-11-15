# AIERP Product Facts

*Authoritative statement of what EXISTS today. The panel and the auto-answerer must never
assert infrastructure or capability facts beyond this document. When something here says
"not built", that is a fact, not an omission.*

Last verified: 2026-10-22 (against `main` and the QA instance).

## What exists

- **Backend** (FastAPI, `backend/app/main.py`, 27 routers). Every route except
  `GET /api/health` requires a Bearer token through
  `app.auth.dependencies.require_authenticated_user`, applied once at router level; it fails
  closed regardless of `AUTH_ENABLED`. Routes that change shared data take
  `Depends(require_admin)`. Examples are purchase budgets, system settings, ERP code
  mappings and the ERP release confirm. Planner decisions, forecast overrides and personal
  settings belong to the signed-in user. `AUTH_MODE=development` accepts the instance's
  configured development token (`<token>:<role>` sets the role); `AUTH_MODE=production`
  requires a valid IdP JWT.
- **Read models** (Alembic migrations `0001`–`0021`): `inventory_items`, `item_sites`,
  `demand_events`, `lead_time_observations`, `on_hand`, `open_purchase_lines`, plus planner
  decision history, forecast snapshots and overrides, settings, saved scenarios, ERP code
  mappings, draft-PO action audits, `purchase_budgets` and the `plan_releases` audit.
- **Replenishment**:
  - demand statistics in 30-day buckets with Syntetos–Boylan classification
    (`app/stats/demand.py`);
  - lead-time statistics with an outlier fence (`app/stats/lead_time.py`);
  - reorder-point recommendations (`app/recommend/reorder.py`);
  - money-ranked drift (`app/recommend/drift.py`);
  - recommendation changes, explained by input (`app/recommend/changes.py`);
  - dead and excess stock (`app/recommend/excess.py`);
  - stockout risk that counts open purchase supply (`app/recommend/stockout.py`);
  - CSV exports.
- **Planner decisions**: accept, dismiss with a reason, or snooze each drift, excess and
  stockout row (append-only history). An accepted decision shows as applied once the ERP data
  reflects it. A summary totals accepted, applied and outstanding money.
- **Demand planning** (`app/forecast/`):
  - a per-item-site forecast chosen by backtest across five methods;
  - backtest and realised accuracy (snapshots);
  - planner overrides with a required reason, and withdrawals;
  - a system setting that chooses history or forecast as the reorder basis.
- **Scenarios**: read-only what-if simulation (service level, demand and lead-time levers),
  and saved lever sets (`app/scenario/simulate.py`, `app/api/scenarios.py`).
- **Aggregate planning** (`app/aggregate/`):
  - the monthly projection by site, with the planned orders the policy would place
    (`projection.py`). It counts released-but-not-yet-approved orders as on-order supply
    (#577), so they are never planned twice;
  - spend against monthly purchase budgets (`spend.py`);
  - deferral candidates for over-budget months (`deferral.py`).
- **Write-back to the ERP**. Both paths below are human-confirmed and admin-only, and both are
  off unless the instance switches them on:
  - **Acumatica:** a month's planned orders are released as purchase orders created ON HOLD,
    one per site and vendor. The flow is preview, then an admin confirms. It is idempotent by
    origin, audited in `plan_releases`, and needs `ACUMATICA_ALLOW_WRITES=1`
    (`app/aggregate/release.py`, `app/feed/acumatica_client.py`).
  - **Release tracking:** each released order's state is read back live from the ERP: late,
    awaiting approval (On Hold or Pending Approval), partly received, open, received,
    cancelled or missing (`app/aggregate/tracking.py`, `GET /api/aggregate/releases`).
  - **Odoo:** one draft purchase order at a time from the Work queue, audited, behind
    `ODOO_ALLOW_WRITES`.
- **Feed and sync**:
  - **AIDW feed:** `app.feed.aidw_client.iter_entities` reads the AIDW OData v4 feed
    (`X-Api-Key`, TLS against a configured CA bundle, server paging by `@odata.nextLink`).
  - **Acumatica sync:** `app.sync.acumatica.run_sync()` reads the entity sets and then
    upserts the read models. It never deletes, so an empty or partial feed cannot remove rows.
  - **Odoo:** a direct connector syncs the same five kinds of data (`app/sync/odoo.py`).
  - **Feed status:** `GET /api/feed/status` reports each table's newest `synced_at`, its row
    count, and the newest demand date.
  - **OData out:** an OData v4 feed for Excel serves eight entity sets on its own Basic
    credential (`app/api/odata.py`).
- **Planner screen** (`app/`: static HTML, CSS and JavaScript served by nginx; Keycloak
  sign-in with PKCE). It has eight views:
  - Overview, Work queue, Demand, Scenario;
  - Aggregate (projection, spend vs budget, deferrals, Release to ERP);
  - Releases (release tracking);
  - Decision log, Settings.
- **Idioms**:
  - repositories use raw `psycopg2` through `app.db.connection.get_cursor()`;
  - Pydantic models per entity live in `app/models/`, and routers in `app/api/`;
  - proving tests sit in `backend/tests/`, with a session-scoped throwaway Postgres
    database created and migrated by `backend/tests/conftest.py`. Its `client` fixture
    authenticates with the development token, and `admin_headers` and `user_headers` give
    the two roles;
  - frontend proofs are Playwright specs in `app/tests/` with every `/api/*` call mocked;
  - dependencies are pinned in `backend/constraints.txt`.
- **Gate**: `scripts/ca_gate/` (the full directory — `check_pr_contract.py` plus
  `contract_lib.py` and helpers).
- **A QA instance runs**: the backend, the planner screen and Postgres on the build host,
  deployed from `main` after each merge and authenticated as above.
  - **Daily feed:** AIDW ingests the reference ERP at about 06:00 UTC, and AIERP syncs from
    the AIDW feed at 07:00 UTC.
  - **Volumes (2026-10-22):** 774 item-sites, 28,715 demand events (2013-01 to 2026-10-21),
    6,768 lead-time observations, 778 on-hand rows and 6,646 open purchase lines.
  - **Demo data only:** the data is the reference ERP's vendor demo data, plus simulated
    activity (below).

## What does NOT exist yet

- No customer data of any kind, and none is permitted until an instance policy exists.
- No scheduler or command-line entry point in the repository: `run_sync` is a Python
  function, and the deployment schedules it.
- No automatic action in the ERP: every write is a previewed, admin-confirmed action, and
  approval of released orders stays in the ERP's own workflow.

## The data substrate (external, real, already proven)

- **AIDW** (`vtggit/AIDW`) performs authenticated, scheduled OData ingest from any
  OData-speaking ERP, lands the records, and serves them back out over an OData v4 feed
  with `$filter` / `$select` / `$orderby`, an API key, and TLS. The feed pages
  server-side (1,000 rows per page with `@odata.nextLink`); `$top` caps the whole result,
  so it is never a page size. AIERP consumes that feed rather than re-implementing ingest.
- **AIDW ingests the reference ERP in full.** The six replenishment entity sets
  (`InventoryItem`, `INItemSite`, `INSiteStatus`, `INTran`, `POLine`, `POReceiptLine`) are
  ingested completely. The ERP does no server-driven paging, so AIDW pages itself with
  `$skip` and a key-stable `$orderby`. Datasets whose rows share one modification
  timestamp are re-read in full each daily run; the business-key upsert keeps that
  duplicate-free.
- **Reference ERP**: a live Acumatica 26.1 instance with the vendor SalesDemo dataset (a
  disposable demo company).
  - **OData v4 (DAC):** this surface exposes ~4,866 entity sets, which is where inventory
    master and movement history live.
  - **Generic Inquiries (v3):** this surface exposes only 29 published inquiries, mostly
    financial and CRM BI. Inventory movement history is NOT published as a GI there.
  - **Authentication:** OData uses Basic with a tenant-qualified login (`user@Tenant`).
  - **Contract REST:** the API (`/entity/Default/25.200.001`, tenant SalesDemo) is what the
    release and tracking features call. Its responses are a bare JSON array whose fields
    are wrapped as `{"value": ...}`, unlike OData.
- **Reference-ERP behaviour the product depends on** (all measured 2026-10-21/22):
  - **Hidden POs:** OData `POLine` leaves out purchase orders that are On Hold or Pending
    Approval. A released order reaches AIERP's `open_purchase_lines` only once someone
    approves it in the ERP. Until then AIERP counts it as in-flight supply from
    `plan_releases` (#577).
  - **Approval workflow:** the SalesDemo company runs a PO approval workflow. Removing a
    released order's hold moves it to Pending Approval, not Open.
  - **Lot and serial items:** lot-, serial- and expiry-tracked items get lot and serial
    numbers auto-assigned on receipt and auto-allocated on issue. An expiry-tracked item's
    receipt needs an expiration date. Expired lots stay on hand but cannot be issued.
  - **Unit costs:** the ERP rounds unit cost half-up to cents.
- **Simulated activity.** The demo company's own history stops at 2026-06-30. Since
  2026-07-01, a host-side simulator (outside this repository) posts daily activity on the
  reference ERP:
  - demand issues and replenishment receipts at each item-site's demand rate;
  - receipts against approved AIERP-released orders as they fall due, sometimes late or
    split.

  This is development data only.

## Reference-ERP mapping (operator decision, 2026-10-01)

How the replenishment read model is filled from the reference ERP (Acumatica, OData v4 DAC
entity sets, read through AIDW). Field names below are the ERP's own.

- **Item and warehouse codes.** `item_code` is `InventoryItem.InventoryCD` with surrounding
  whitespace trimmed, joined on `InventoryID`; only stock items (`StkItem` true) are synced.
  `site_code` is the numeric `SiteID` as text (the warehouse master is not synced yet).
- **Quantities** always use `BaseQty`, the quantity in the item's base unit of measure, never
  `Qty`, which is in the transaction's own unit.
- **Demand (`INTran`).** Only lines that take stock out (`InvtMult` = -1), and only for these
  (`DocType`, `TranType`) pairs: (`I`, `INV`) sales shipments, (`M`, `III`) production material
  issues, (`P`, `ASY`) kit assemblies (the -1 lines are the consumed components), and
  (`I`, `III`) plain issues. Receipts, adjustments, transfers, returns, credit memos and
  disassemblies are not demand. `quantity` = the absolute value of `BaseQty`; `occurred_on` =
  the date part of `TranDate`; `business_key` = `DocType:RefNbr:LineNbr`.
- **Lead times.** A `POReceiptLine` with `InvtMult` = +1 and a `PONbr`, joined to the `POLine`
  with the same (`POType`, `PONbr`, `POLineNbr`) = (`OrderType`, `OrderNbr`, `LineNbr`):
  `ordered_on` = date of `POLine.OrderDate`, `received_on` = date of
  `POReceiptLine.ReceiptDate`, `supplier_code` = `VendorID` as text, `business_key` =
  `ReceiptType:ReceiptNbr:LineNbr`. Lines whose receipt date precedes the order date are
  skipped and counted, never stored.
- **Current settings (`INItemSite`).** `reorder_point` = `MinQty`, `safety_stock` =
  `SafetyStock`, `max_qty` = `MaxQty`, `unit_cost` = `AvgCost`, else `LastCost`, else `StdCost`
  (the first that is positive). The reference ERP exposes no order quantity or master lead time
  per item-warehouse here: `order_qty` = `MaxQty - MinQty` when positive, else empty;
  `master_lead_time_days` stays empty, so an item with fewer than 3 observed lead times gets no
  recommendation (flagged), never a guessed one.
- **On hand (`INSiteStatus`).** `QtyOnHand` summed over `SubItemID` per (`InventoryID`,
  `SiteID`); `as_of` = the sync's start time.
- **Open purchase lines (`POLine`).** The `business_key` is `OrderType:OrderNbr:LineNbr`.
  Approved lines stay in the feed after they complete (with zero open units).

## Standing constraints

- AIERP is a **decision layer**: it recommends, and a person decides. The ERP stays the
  system of record. Every write to an ERP is a previewed, admin-confirmed action behind a
  per-instance switch, and adding a new kind of write needs an explicit, human-approved
  capability decision.
- Accounting-adjacent scope — general ledger, tax determination, revenue recognition,
  payroll — is **out of scope** by product decision, not by omission.
- No live customer data until an instance and data policy exists; development runs
  against the reference ERP's demo dataset and its simulated activity.
