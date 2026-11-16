# AISCP

**AI supply chain planning over the ERPs you already run.**

ERPs record inventory transactions faithfully, but they do not tell a planner what to do
next: what to reorder and how much, what is quietly dying, what will stock out behind a lead
time that changed months ago. AISCP is a decision layer over the system of record. It reads
the ERP's own history and recommends, with the inputs and the arithmetic behind every number.
It does not own items, stock or transactions, and the ERP stays authoritative. Nothing is
written to the ERP without a grant: a person's approval of that one write, or a standing grant
an admin created for the release of purchase orders or transfers, within its limits. What it
writes lands on hold, so the ERP's own approval still applies.

AISCP is not an ERP. It was built under the codename aierp, which the guidance keys
(`AIERP-...`), the product vision, the configuration and the service names still use
(AIERP-D-226).

The full product intent is in [docs/PRODUCT-VISION.md](docs/PRODUCT-VISION.md).

## What it does today

**Replenishment**

- Demand statistics in twelve 30-day buckets, with each item-site classified as smooth,
  erratic, intermittent, lumpy or no demand (`backend/app/stats/demand.py`).
- Lead-time statistics from purchase receipts, with an outlier fence
  (`backend/app/stats/lead_time.py`). A setting chooses whether the lead time behind safety
  stock comes from every supplier's receipts for the item (the default) or only its main
  supplier's.
- Recommended safety stock and reorder point at a stated service level, method and inputs
  shown (`backend/app/recommend/reorder.py`). On-order supply counts open purchase lines,
  released orders still awaiting ERP approval and transfers in flight.
- A recommended order quantity (the economic order quantity, kept between 7 and 365 days of
  demand) and a recommended max (`backend/app/recommend/order_quantity.py`).
- ABC classes by annual consumption value, and service levels by class behind a switch that
  is off by default (`backend/app/recommend/abc.py`).
- Reorder points for intermittent and lumpy item-sites from the demand in every past
  lead-time span: shown for review, and used as the recommendation when a switch is on or an
  admin adopts the item-site (`backend/app/recommend/empirical.py`,
  `backend/app/api/empirical_adoptions.py`).
- Service levels and their cost: per ABC class, the safety stock, yearly holding cost and
  expected fill rate of each level from 90% to 99.5%, and the cheapest levels for a fill-rate
  target. A setting makes the levels fill-rate targets instead of cycle service levels
  (`backend/app/recommend/service_curve.py`).
- Drift: where the ERP's current reorder point no longer fits, ranked by money at stake
  (`backend/app/recommend/drift.py`).
- Recommendation changes: why a recommended reorder point moved since an earlier date, split
  across demand level, demand variability, lead time and lead-time variability
  (`backend/app/recommend/changes.py`).
- Dead and excess stock, ranked by money tied up (`backend/app/recommend/excess.py`).
- Stockout risk inside the replenishment window, counting open purchase supply
  (`backend/app/recommend/stockout.py`).
- CSV exports of the drift, excess, stockout and change lists, and a planning sheet with the
  recommended and current parameters of every item-site (`backend/app/recommend/export.py`).

**Planner decisions**

- Accept, dismiss (with a reason) or snooze each drift, excess and stockout row; decisions
  are append-only history (`backend/app/api/planner_decisions.py`,
  `excess_decisions.py`, `stockout_decisions.py`).
- An accepted decision is shown as applied once the ERP data reflects it, and a summary
  totals accepted, applied and outstanding decisions with their money
  (`backend/app/api/decision_summary.py`).

**Demand planning**

- A statistical forecast per item-site for the next 12 buckets, chosen by backtest among
  mean, exponential smoothing, damped trend, seasonal and Syntetos-Boylan methods
  (`backend/app/forecast/`).
- Forecast accuracy: backtest error with every forecast, and realised accuracy against stored
  forecast snapshots, with the effect of overrides and of demand signals measured separately
  (`backend/app/forecast/accuracy.py`, `snapshots.py`).
- Planner overrides of a forecast period, with a required reason, and withdrawals
  (`backend/app/forecast/overrides.py`).
- Demand signals: known extra demand (a promotion, deal or event) for one item-site and date
  window, added on top of the forecast and counted in the reorder point and the aggregate
  projection. Signals are append-only; a withdrawal is a new row with a reason
  (`backend/app/forecast/signals.py`).
- A system setting chooses whether reorder points are built on demand history (the default)
  or on the forecast (`backend/app/forecast/basis.py`, `backend/app/settings/registry.py`).

**Scenarios, aggregate planning and S&OP**

- What-if simulation: re-run the reorder recommendation under a different service level (one
  for all, or one per ABC class), demand or lead time and see the change per item-site and in
  money. Simulation writes nothing; lever sets can be saved as named scenarios
  (`backend/app/scenario/simulate.py`, `backend/app/api/scenarios.py`).
- Aggregate projection: monthly projected demand, supply and on-hand by site, with the
  planned orders the recommended policy would place (`backend/app/aggregate/projection.py`).
- Spend against a monthly purchase budget, and deferral candidates for over-budget months
  (`backend/app/aggregate/spend.py`, `deferral.py`, `backend/app/api/purchase_budgets.py`).
- The monthly S&OP review: the horizon's demand, supply, planned receipts, projected on-hand
  and spend by month, with forecast accuracy, demand signals and saved scenarios against the
  plan, on one page. An admin signs the month off, and the sign-off stores the figures agreed
  (`backend/app/sop/review.py`, `backend/app/api/sop.py`).

**Supply planning**

- The order plan: which item-sites are due for an order within a horizon (7 days by default),
  from which supplier and by when, each quantity fitted to the supplier's minimum, lot size and
  maximum with the arithmetic shown (`backend/app/api/supply_plan.py`,
  `backend/app/supply/terms.py`). Supplier ordering terms are read from both ERPs.
- When an item has terms from more than one supplier, the plan buys from a lower-risk one and
  says why (a setting, on by default).
- Supplier order minimums: an admin stores a minimum order value per supplier, the plan's
  supplier groups carry the minimum and the shortfall, and a read-only route suggests top-ups
  from the lines due next (`backend/app/api/supplier_order_minimums.py`,
  `GET /api/supply-plan/top-ups`).
- The order plan screen shows a supplier's minimum when present, a below-minimum badge, and
  the suggested top-ups for supplier groups that are short (`app/js/orders.js`).
- For an admin, the order plan screen shows a checkbox on each suggested top-up row and a
  running line showing whether the chosen top-ups meet the minimum; the release preview sends
  the ticked top-ups, and the confirm sends that preview's body with `confirm: true` added; a
  tick change or a horizon change closes the preview without a request (`app/js/orders.js`).
- Transfer suggestions: where one site holds more of an item than it needs and another site of
  the same ERP is short, move the stock before buying (`backend/app/supply/transfers.py`).

**DDMRP buffers**

- An admin designates the buffered item-sites. Buffer zones come from average daily usage and
  lead time, with the buffer factors as settings; the plan gives each buffer's net flow, order
  signal and on-hand alert, counting qualified demand from open sales orders
  (`backend/app/ddmrp/`, `backend/app/api/ddmrp.py`).
- Suggested buffer candidates, planned adjustment factors for seasonal or promotional ramps,
  and each buffer's time in each zone from a daily status snapshot.

**Exceptions**

- One triage list of what needs attention today, each row with a severity, one sentence of
  detail and the screen that explains it: DDMRP buffers, stockout risk, expedite lines,
  forecast bias, releases and transfers that failed or are still waiting in the ERP, failed
  parameter pushes, a missing S&OP sign-off, ERP code clashes, currency mismatches, cross-ERP
  opportunities, high-risk suppliers, sole-source items short of stock, orders below a
  supplier's minimum, and standing grant runs that failed, sent nothing, held documents back,
  or have not run recently. A source that fails never hides the others. The list records the day
  each exception was first seen, and an admin can acknowledge or snooze one
  (`backend/app/api/exceptions.py`).

**Suppliers**

- Delivery performance per supplier: on-time rate against real promises, lateness and
  lead-time spread (`backend/app/recommend/supplier_performance.py`).
- The expedite list: open purchase lines that are late or due after the item runs out, ranked
  by urgency (`backend/app/recommend/expedite.py`).
- A risk rating per supplier (High, Medium or Low) from late deliveries, variable lead times
  and sole sourcing, with the thresholds as settings
  (`backend/app/recommend/supplier_risk.py`).
- Second sources for each sole-source item: other suppliers with terms for it, suppliers of
  similar items, and the stock to hold until another supplier could deliver
  (`backend/app/recommend/second_source.py`).
- An Order minimums section on the Suppliers screen lets an admin store, change and remove a
  supplier's minimum order value, and lists the stored minimums by supplier, source, amount
  and last update (`app/js/suppliers.js`).

**Several ERPs**

- Every recommendation names the ERP it came from, and the Work queue tags and filters its
  rows by ERP. When two ERPs report the same item at the same site, the first one keeps it,
  the other's rows are not loaded, and the clash is recorded (`backend/app/sync/clashes.py`).
- Suggested matches of the same item across ERPs, by the same code or the same description,
  which an admin confirms as ERP code mappings (`backend/app/recommend/item_matching.py`).
- Matched items compared side by side, flagging an item short in one ERP while the other holds
  excess, with the units that could move (`backend/app/recommend/cross_erp.py`). AISCP does
  not move stock between ERPs.
- The currency each ERP reports in, checked against a reporting-currency setting. AISCP does
  not convert currencies (`backend/app/api/erp_currencies.py`).

**Acting in the ERP**

- Release a month's planned orders, or a supplier's order-plan lines, to Acumatica as purchase
  orders on hold, one per site and supplier: preview first, then an admin confirms; releases
  are idempotent by origin and every one is recorded in an audit table
  (`backend/app/aggregate/release.py`, `backend/app/supply/release.py`). A supplier's
  order-plan release can name at most 200 top-up entries as sent from the top-up suggestions for the
  named suppliers; each held top-up is added to its site and supplier group, creating that
  group when the supplier has no due line there, and one the suggestions do not hold is listed
  as unreleasable.
- Release tracking: where each released order stands in the ERP (open, late, awaiting
  approval, partly received, received, cancelled, missing), with lateness judged by the
  expected date (`backend/app/aggregate/tracking.py`).
- Release chosen transfer suggestions to Acumatica as transfer orders on hold, with the same
  preview, confirm and audit; their status is read back from Acumatica
  (`backend/app/supply/transfer_release.py`).
- Push accepted reorder points and DDMRP buffer levels into the ERP that owns the item-site
  (Acumatica item-warehouse settings or Odoo reorder rules): preview first, then an admin
  confirms; every write is read back and audited (`backend/app/api/parameter_writeback.py`).
- Draft a single purchase order in Odoo from the Work queue: preview first, then an admin
  confirms; every confirmed action is audited (`backend/app/api/replenishment_actions.py`).
- ERP code mappings translate AISCP item, site and vendor codes to Odoo codes
  (`backend/app/api/erp_mappings.py`).
- Standing grants: an admin creates or revokes a standing grant for `order_plan_release` or
  `transfer_release`, with per-document and daily value and data-age limits; a system switch
  can stop standing-grant writes. The Releases screen has a standing-grants section that shows
  the standing grants and their runs to every signed-in user, and an admin creates and revokes
  grants there. A standing-grant run of the order plan sends the due lines
  to Acumatica on hold under the grant, within its limits and a horizon-days setting, and a
  standing-grant run of transfers sends the transfer suggestions to Acumatica on hold under
  the grant, within its limits, and each run is recorded in `standing_grant_runs`
  (`backend/app/api/erp_standing_grants.py`, `backend/app/supply/standing_release.py`,
  `backend/app/settings/registry.py`). Any signed-in user can read the stored runs, newest
  first, with an optional process filter and limit (`GET /api/standing-grants/runs`). The
  order-plan run leaves out a group holding a line whose unit cost is not above 0, with the
  reason `no_value`, before its limits. The transfer run leaves out a group holding a
  suggestion whose value is null or not above 0, with the reason `no_value`, before its
  limits and reducing no allowance, and its left-out value is the summed value of its
  suggestions above 0.
- Writes to either ERP are off unless switched on for the instance (`ACUMATICA_ALLOW_WRITES`,
  `ODOO_ALLOW_WRITES`).

**Guidance**

- AISCP holds the guidance it is built under, published whole by an admin
  (`PUT /api/guidance`) or by the deployment (`python -m app.guidance_publish`). Every version
  is kept, and any signed-in user can read the current guidance, an item's history and what a
  stamp names (`backend/app/api/guidance.py`, `backend/app/guidance_publish.py`).

**Data and operations**

- Feed status: when each feed table was last synced and the latest demand date
  (`backend/app/feed/status.py`).
- An OData v4 feed for Excel and other machine consumers (see "API surface").
- Settings at system (admin) and personal scope, with every change on an append-only trail
  (`backend/app/settings/registry.py`).

## Where the data comes from

AISCP keeps its own PostgreSQL read model: items, item-sites, demand events, lead-time
observations, on hand, open purchase and sales lines, suppliers, supplier ordering terms and
the currencies each ERP reports in. A sync fills it, reading every source first and writing
only after all reads succeed, so a failed read never leaves a half-written sync. Each row
records the ERP it came from, and an item-site one ERP already holds is never overwritten by
another (`backend/app/sync/clashes.py`).

- **Acumatica, through AIDW.** [AIDW](https://github.com/vtggit/AIDW) ingests the ERP over
  OData and serves it as an OData feed. AISCP reads that feed
  (`backend/app/feed/aidw_client.py`) and maps nine of the ERP's entity sets into its read
  model (`backend/app/sync/acumatica.py`). The ERP fields used are documented in
  [docs/FEED-ENTITIES.md](docs/FEED-ENTITIES.md).
- **Odoo.** A direct connector reads item-sites with their reorder rules, on hand, demand,
  receipts and open purchase and sales lines, and, where Odoo allows it, supplier terms,
  product names and company currencies (`backend/app/feed/odoo_client.py`,
  `backend/app/sync/odoo.py`).
- **Acumatica, directly.** Releases, release tracking, transfer orders and their status, the
  parameter write-back and the ledger currencies talk to Acumatica's own entity and OData
  APIs (`backend/app/feed/acumatica_client.py`, `backend/app/sync/currencies.py`).

The sync runners are Python functions (`run_sync`), and so are the jobs that follow a sync:
the Acumatica currency read, the transfer-order status refresh
(`backend/app/supply/transfer_release.py`), and the daily captures of the forecast snapshot,
the DDMRP status snapshot and the exceptions' first-seen dates. The repository does not ship
a scheduler or command-line entry point for them; scheduling is done by the deployment.

## The planner screen

A static single-page app in `app/` (plain HTML, CSS and JavaScript, served by nginx). The
views are:

- **Overview**: value tiles, the three decision lists, released orders, value by ABC class,
  today's exceptions, this week's supply plan, and the decisions that need attention, largest
  money first.
- **Exceptions**: the triage list across every planning signal, with acknowledge and snooze
  for an admin.
- **Work queue**: the reorder-point, excess and stockout lists with a detail pane to record
  a decision, each row's ERP and an ERP filter when there is more than one ERP, CSV
  downloads, and the Odoo draft purchase order action.
- **Demand**: the forecast list, history and forecast chart, method and accuracy, overrides,
  demand signals, and the empirical reorder point of intermittent and lumpy item-sites.
- **Scenario**: the what-if levers, per-class service levels and saved scenarios.
- **Aggregate**: the monthly projection by site, spend against budget, deferral candidates,
  and the release of planned orders to the ERP.
- **S&OP**: the monthly review, its sign-off, past sign-offs and what moved since the last
  one, saved scenarios against the plan, and the demand signals.
- **Across ERPs**: matched items side by side in each ERP, the short-here, excess-there
  opportunities and the currency each ERP reports in.
- **Order plan**: what to order, from whom and by when, with supplier risk badges, supplier
  minimums and below-minimum badges, suggested top-ups, with an admin's chosen top-ups
  included in the release, a release per supplier, transfer suggestions and their release,
  and a CSV download.
- **Releases**: where each order released to the ERP stands, and the released transfer
  orders.
- **Suppliers**: delivery performance, the risk rating with the items each supplier alone
  supplies, second sources, each supplier's ordering terms, and the order minimums, which an
  admin stores, changes and removes there.
- **Expedite**: open purchase lines that are late or will arrive after the item runs out.
- **Push to ERP**: accepted reorder points and buffer levels the ERP does not hold yet, the
  push, and the history of every write.
- **Buffers**: the DDMRP buffers with their zones, net flow, order signals and on-hand alerts,
  suggested buffers, planned adjustments and buffer performance.
- **Decision log**: every recorded decision across the three lists, newest first.
- **Settings**: system and personal settings, ERP code mappings with suggested matches,
  purchase budgets, the intermittent and lumpy items with their adoption, and the cost of
  each service level.
- **Guidance**: the guidance AISCP is built under, by type and area, with each item's history
  and a find box that also looks up a stamp.

The sidebar says when the feed last synced, and the header the date the data runs through
(`app/js/feedstatus.js`).

Sign-in uses an OpenID Connect provider such as Keycloak, with the authorization-code flow
and PKCE (`app/js/auth.js`). In development mode the screen asks for a development token
instead. The design is specified in [docs/design/planner-screen.md](docs/design/planner-screen.md).

## API surface

Every `/api` route except the health probe and the sign-in configuration needs a Bearer
token (`backend/app/main.py`). Reads are open to any signed-in user, except the audit lists of
releases, transfer releases and draft purchase orders, which need the `admin` role. These
changes need the `admin` role too: recording decisions, forecast overrides and
snapshots, demand signals, system settings, purchase budgets, ERP code mappings, supplier
order minimums, DDMRP buffers and adjustments, empirical adoptions, exception
acknowledgements, S&OP sign-offs, publishing guidance, inventory-item changes, and confirming
any write to an ERP. Personal settings and saved scenarios are open to any signed-in user.

| Group | Prefix |
|---|---|
| Health | `/api/health` |
| Sign-in | `/api/auth` |
| Read model | `/api/inventory-items`, `/api/item-sites`, `/api/demand-events`, `/api/lead-time-observations`, `/api/on-hand`, `/api/suppliers`, `/api/supplier-terms` |
| Statistics | `/api/demand-stats`, `/api/lead-time-stats`, `/api/abc` |
| Recommendations | `/api/recommendations` (also the planning sheet and the intermittent impact), `/api/drift`, `/api/recommendation-changes`, `/api/excess`, `/api/stockout`, `/api/empirical-adoptions`, `/api/service-levels` |
| Decisions | `/api/drift/decisions`, `/api/excess/decisions`, `/api/stockout/decisions`, `/api/decisions/summary` |
| Forecast | `/api/forecast` (forecasts, snapshots, accuracy, overrides), `/api/demand-signals` |
| Scenarios | `/api/scenarios` |
| Aggregate | `/api/aggregate` (projection, spend, deferrals, release, release audit, releases) |
| S&OP | `/api/sop` (review, scenarios, sign-offs) |
| Purchase budgets | `/api/purchase-budgets` |
| Supply plan | `/api/supply-plan` (plan, summary, top-ups, release, transfers, transfer release and its audit), `/api/supplier-order-minimums` |
| Suppliers | `/api/suppliers/performance`, `/api/supplier-risk`, `/api/second-sources`, `/api/expedite` |
| DDMRP | `/api/ddmrp` (buffers, zones, plan, candidates, adjustments, performance) |
| Exceptions | `/api/exceptions` (the list and acknowledgements) |
| Several ERPs | `/api/erp-sources`, `/api/erp-mappings` (with suggested matches), `/api/cross-erp/items`, `/api/erp-currencies` |
| ERP actions | `/api/replenishment/actions/draft-po`, `/api/parameter-writeback` (preview, confirm, history) |
| Settings | `/api/settings` |
| Guidance | `/api/guidance` (current guidance, item history, stamps) |
| Feed status | `/api/feed/status` |
| OData | `/odata` |

The OData feed publishes eight read-only entity sets (Drift, Excess, Stockout, Changes,
Decisions, ExcessDecisions, StockoutDecisions, Forecast) with `$filter`, `$orderby`, `$top`
and `$skip`. It uses its own HTTP Basic machine credential, not a user token
(`backend/app/api/odata.py`).

The full schema is committed as [backend/openapi.json](backend/openapi.json).

## Running it

The repository ships two container images and no compose file:

- `backend/Dockerfile`: the API. Its entrypoint, `backend/start.sh`, waits for PostgreSQL,
  runs `alembic upgrade head`, then starts uvicorn on port 9000.
- `app/Dockerfile`: the planner screen on nginx, port 8080. `app/nginx.conf` proxies
  `/api/` to a host named `backend`, so the two containers must share a network where the
  API is reachable under that name.

To run the backend without containers, against a PostgreSQL you provide:

```
cd backend
pip install -r requirements.txt -c constraints.txt
alembic upgrade head
uvicorn app.main:app
```

Configuration comes from environment variables. Set values for your own environment; none
are committed.

- **Database:** `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.
- **Sign-in:** `AUTH_MODE` (`development` or `production`), `AUTH_DEV_TOKEN`,
  `AUTH_DEV_ROLES`, `AUTH_ENABLED`, `AUTH_ISSUER`, `AUTH_CLIENT_ID`, `AUTH_AUDIENCE`,
  `AUTH_JWKS_URL`, `AUTH_ALGORITHMS`, `AUTH_JWKS_CACHE_TTL`, `AUTH_ROLE_CLAIMS`,
  `AUTH_GROUP_CLAIMS`.
- **OData feed credential:** `AIERP_ODATA_USER`, `AIERP_ODATA_PASSWORD`.
- **AIDW feed:** `AIDW_FEED_BASE_URL`, `AIDW_FEED_API_KEY`, `AIDW_FEED_CA_BUNDLE`,
  `AIDW_FEED_TIMEOUT_SECONDS`.
- **Acumatica:** `ACUMATICA_BASE_URL`, `ACUMATICA_TENANT`, `ACUMATICA_USERNAME`,
  `ACUMATICA_PASSWORD`, `ACUMATICA_ENDPOINT_VERSION`, `ACUMATICA_CA_BUNDLE`,
  `ACUMATICA_TIMEOUT_SECONDS`, `ACUMATICA_ALLOW_INSECURE`, `ACUMATICA_ALLOW_WRITES`.
- **Odoo:** `ODOO_BASE_URL`, `ODOO_DB`, `ODOO_LOGIN`, `ODOO_API_KEY`, `ODOO_CA_BUNDLE`,
  `ODOO_TIMEOUT_SECONDS`, `ODOO_ALLOW_INSECURE`, `ODOO_ALLOW_WRITES`.
- **Calculation:** `AIERP_SERVICE_LEVEL`, `AIERP_ORDER_COST`, `AIERP_HOLDING_RATE`,
  `EXCESS_COVER_MONTHS`.
- **Other:** `CORS_ORIGINS`, `LOG_LEVEL`, `LOG_FORMAT`.

Database migrations live in `backend/migrations/versions/` (Alembic).

## Tests and CI

- **Backend tests** (pytest) are in `backend/tests/` and need a PostgreSQL database. CI runs
  them against a PostgreSQL service with `AUTH_MODE=development`, after checking that the
  migrations apply cleanly.
- **Frontend tests** (Playwright) are in `app/tests/`. Without `BASE_URL` set, the
  Playwright config serves `app/` itself; with it set, the specs run against that address.
- **Merge-gate tests** are in `tests/ca_gate/`.

The workflows in `.github/workflows/`:

- `backend-ci.yml`: release metadata check, black and ruff, shellcheck, dependency audit
  (pip-audit) and secret scan (gitleaks), an OpenAPI sync check, and the backend test suite.
- `codeagent-merge-gate.yml`: proves each pull request against its stated acceptance
  criteria, running the named backend tests or Playwright specs.
- `codeagent-gate-revalidate.yml`: a daily re-validation of the merge gate.
- `docs-check.yml`: the check a Markdown-only pull request gets, for unclosed code fences,
  broken relative links, private IP addresses, literal secrets, and routes, settings, or
  environment variables named in the doc that do not exist in the code
  (`scripts/docs_check.py`).

Dependencies are pinned. `backend/constraints.txt` fixes every package version for the
image and for CI; take upgrades on purpose with `scripts/refresh_constraints.sh` and review
the diff. After changing a route or model, regenerate the API schema with
`python3 scripts/export_openapi.py`; CI fails if it is out of date.

## Project layout

```
backend/
  app/
    api/            HTTP routers
    auth/           token validation and roles
    feed/           AIDW, Acumatica and Odoo clients, feed status
    sync/           ERP-to-read-model sync runners, the code-clash guard, currencies
    stats/          demand and lead-time statistics
    recommend/      reorder points, order quantities, ABC classes, drift, changes, excess,
                    stockout, expedite, service-level cost, supplier performance and risk,
                    second sources, cross-ERP comparison and item matching, CSV export
    forecast/       forecasting, accuracy, snapshots, overrides, demand signals
    scenario/       what-if simulation
    aggregate/      projection, spend, deferrals, release, release tracking
    supply/         ordering terms, transfer suggestions, order-plan and transfer releases,
                    standing-grant order-plan runs
    ddmrp/          DDMRP buffer zones, plan and status snapshots
    sop/            the S&OP review
    settings/       the settings registry
    models/ repositories/ db/ observability/
    guidance_publish.py   the guidance publish command
    units.py        the shared whole-unit rounding
  migrations/       Alembic migrations
  tests/            backend tests
  openapi.json      committed API schema
app/                planner screen (index.html, css/, js/, tests/)
docs/               vision, demo walkthrough, feed entities, design
config/             build-engine product configuration
scripts/            OpenAPI export, release metadata check, docs check, constraints refresh,
                    merge gate
tests/ca_gate/      merge-gate tests
```

## Deeper documentation

- [docs/PRODUCT-VISION.md](docs/PRODUCT-VISION.md): what the product is for, and every
  decided calculation method and default.
- [docs/DEMO.md](docs/DEMO.md): a 20-minute guided demo of the product.
- [docs/FEED-ENTITIES.md](docs/FEED-ENTITIES.md): the ERP entity sets and fields AISCP reads.
- [docs/design/planner-screen.md](docs/design/planner-screen.md): the planner screen
  specification.
- [docs/design/design-system.md](docs/design/design-system.md): the shared design system.
- [docs/branch-protection.md](docs/branch-protection.md): how the merge gate is enforced.
- [docs/reference/SETTINGS.md](docs/reference/SETTINGS.md) and
  [docs/reference/API.md](docs/reference/API.md): the settings and API references,
  generated by `scripts/generate_reference_docs.py` — do not edit by hand.

## License — free to view and use, not for redistribution or modification

AISCP is published under the **AISCP Evaluation and Reference License**, the license AIDW and
AICRM use. See [LICENSE](LICENSE) for the full terms; the summary below does not replace them.

- ✅ **View and use** — you may view this repository and use the software for your own internal
  purposes, free of charge.
- ❌ **No redistribution** — do not distribute, publish, mirror, sublicense, sell, rent, lease, or
  offer it as a hosted or managed service.
- ❌ **No modification** — do not modify it or create derivative works.
- ❌ **Keep the notices** — do not remove or alter the license or copyright notices.
- ⚠️ **ERP writes are yours** — the software can write to the ERP it is connected to: orders and
  transfers on hold, planning parameters, and writes under a standing grant. Writes stay off
  until they are switched on, and you are responsible for switching them on, for every
  standing grant and for every write.
- ⚠️ **Use at your own risk** — the software is provided "as is", with no warranty and no
  support, maintenance or service commitment. It is not meant for production use, and Virtual
  Technology Group is not liable for any damage, data loss or business interruption it causes.

This is a **proprietary** license, **not** an open-source license. All rights are reserved by
Virtual Technology Group LLC.

> Note on forks: GitHub lets users fork a public repository, and the License permits that only to
> the extent the hosting platform's terms require it. A fork grants no further rights: the License
> still does not permit redistribution or modification.
