# AIERP Product Vision

*Operator steering for the panel and the auto-answerer. This document is authoritative:
when a question has no recorded fact and no standing decision, answer from the
comparative-research method below rather than deferring to a human.*

---

## North star

**AIERP makes the inventory decisions that the ERP records but never makes.**

Every mid-market ERP has an excellent transactional inventory module: receipts, issues,
transfers, adjustments, counts — recorded with high fidelity and full auditability. What
none of them do well is tell a planner **what to do next**: what to reorder and how much,
what is quietly dying, what will stock out behind a lead time that changed three months
ago, whether the safety-stock number set at go-live is still defensible.

Those decisions get made in spreadsheets, by one experienced person, from memory. When
that person is busy, on holiday, or leaves, the decisions get worse — and nothing in the
system notices.

AIERP is the **decision layer** over the system of record. It does not replace the ERP,
does not own the transactions, and does not become the source of truth for stock.

## What AIERP is NOT

- **Not a system of record.** The ERP owns items, quantities, transactions, and costs.
  AIERP reads them and recommends; the ERP remains authoritative and unmodified unless a
  human approves a specific write-back.
- **Not an accounting product.** General ledger, tax determination, revenue recognition,
  and payroll are explicitly out of scope. They carry regulatory liability that requires
  specialist ownership, and they are not where this product earns its keep.
- **Not ERP-specific.** The data substrate is AIDW, which ingests any OData-speaking ERP
  on a schedule. Adding a source is configuration, not a port.
- **Not a black box.** Every recommendation states its inputs, its arithmetic, and its
  confidence. A planner who cannot audit a number will not act on it — correctly.

## The wedge

**Replenishment intelligence: reorder points and quantities that maintain themselves.**

The failure this addresses is near-universal in mid-market implementations: min/max or
reorder-point values are set during go-live, are never revisited, and silently rot as
demand, lead times, and supplier reliability drift. The result is the signature symptom —
**simultaneous stockouts of fast movers and years of cover on slow ones**, in the same
warehouse, on the same day.

Concretely, the first capabilities are:

1. **Demand and lead-time truth from history** — actual consumption and actual
   supplier lead times (including their variability), computed from ERP transaction
   history rather than from static master-data fields.
2. **Recomputed reorder points and quantities**, with the arithmetic shown and the
   assumptions named, at a stated service level.
3. **Drift detection** — which items' current settings no longer match their behaviour,
   ranked by the money at stake, so a planner fixes the twenty that matter rather than
   reviewing four thousand.
4. **Dead and excess identification** — what has not moved, what has more cover than any
   plausible demand justifies, and what that capital is worth.
5. **Stockout foresight** — what will run out inside the replenishment window, given
   real lead times rather than the field somebody typed in 2019.

Each capability must be defensible to a skeptical planner in one screen: the number, the
inputs behind it, and the reason it changed.

## Standing decisions

- **Read-first.** AIERP reads ERP data and recommends. Any write-back to the ERP is an
  explicit, per-action, human-approved capability — never a default, never a bulk
  automatic action.
- **AIDW is the substrate.** Ingest, scheduling, credentials, and the OData feed already
  exist and are proven against a live ERP. AIERP builds on that rather than re-solving it.
- **Explainability is a hard requirement**, not a feature. Every recommendation carries
  its inputs and its arithmetic.
- **Exportable by design.** Planners live in Excel. Recommendations must be consumable
  there (the AIDW OData feed is the mechanism) rather than trapped behind a UI.
- **No live customer data** until an instance policy exists. Development and
  demonstration run against the in-house ERP instance and synthetic or vendor demo data.
- **Deployable inside the customer's boundary** (operator decision, 2026-10-30). AIERP must run
  wholly inside a customer's own environment: no outbound call except to the ERP it is
  configured for, and no dependence on a hosted model or service outside that boundary. A
  feature that needs an outside service is not built until it can be switched off without
  breaking anything else.
- **Keeps working on last-synced data during an ERP outage** (operator decision, 2026-10-30).
  Every screen and recommendation reads AIERP's own synced copy, so planners keep working when
  the ERP is unreachable. Writes back to the ERP fail closed and are audited, and nothing
  presents stale data as fresh: the data's as-of date is always shown.

## Replenishment defaults (operator decision, 2026-10-01)

These are DECIDED. Panels and the auto-answerer apply them as written; every default
below is an instance setting with this value, not a constant buried in code.

- **The anchor failure.** Reorder settings are set at go-live and never revisited, and
  they rot as demand, lead times and supplier reliability drift. Every wedge capability
  is judged by whether it surfaces and repairs that.
- **What counts as demand.** Stock that leaves to satisfy a need: customer shipments and
  production or kit issues. Transfers between warehouses, inventory adjustments and
  customer returns are NOT demand. Returns are tracked separately and never netted
  against demand.
- **Calculation defaults.** Cycle service level 95% (z = 1.645). Demand history is
  twelve 30-day buckets ending on the as-of date (see "Demand buckets and classification"). A supplier-item needs at least 3 observed lead times;
  with fewer, the ERP's master-data lead time is used and the recommendation is flagged
  low-confidence.
- **The as-of date.** Every history window ends at the latest transaction date present in
  the synced data, or today if that is earlier. On a live source the two are the same day; on
  a demo or frozen dataset the window stays on the data instead of running past its end.
- **Demand buckets and classification** (decided by Claude under operator delegation,
  2026-10-02). Demand is summed into twelve consecutive 30-day buckets ending on the as-of
  date; an item first seen more recently uses only its complete buckets since then (at least
  one). The daily figures the formula needs are derived from the buckets: daily mean = bucket
  mean / 30, daily standard deviation = bucket standard deviation / sqrt(30). Each item-site is
  classified with the documented Syntetos-Boylan scheme: ADI (buckets / buckets with demand)
  and CV^2 (squared coefficient of variation of the non-zero buckets); ADI < 1.32 and
  CV^2 < 0.49 is smooth, ADI < 1.32 and CV^2 >= 0.49 is erratic, ADI >= 1.32 and CV^2 < 0.49
  is intermittent, otherwise lumpy; no demand in the window is no_demand. Smooth and erratic
  items get a recommendation (erratic ones carry a "variable demand" note); intermittent, lumpy
  and no_demand items are flagged and receive no number. Evidence: on the reference ERP's demo
  data, daily buckets classified all 95 active item-sites as intermittent (no recommendation at
  all), weekly left 3 usable, and 30-day buckets give 65 smooth and 5 erratic.
- **Lead-time statistics.** Lead times come from receipts in the 24 months up to the as-of
  date. Before the mean and standard deviation are computed, observations above the upper
  quartile plus 3 times the interquartile range (Q3 + 3 * IQR, the documented "far out"
  fence) are dropped and counted, so one purchase order left open for years cannot inflate
  safety stock. An item's lead time pools all of its suppliers' observations (the preferred
  vendor is not synced yet); per-supplier statistics are still computed and shown.
- **Safety stock and reorder point method.** The documented service-level method with
  both demand and lead-time variability: safety stock SS = z * sqrt(LT * sd_d^2 +
  d^2 * sd_LT^2) and reorder point ROP = d * LT + SS, where d and sd_d are daily demand
  mean and standard deviation and LT and sd_LT are lead-time mean and standard deviation
  in days. Every result names this method. Items classified intermittent, lumpy or no_demand are flagged as unsuitable for
  this method and receive no number, rather than a misleading one.
- **Money at stake.** Over-protected items: (current reorder point - recommended reorder
  point) * unit cost, when positive. Under-protected items: expected units short per
  year at the current reorder point * unit cost. Units short per replenishment cycle come
  from the normal loss function over lead-time demand; cycles per year = annual demand /
  current order quantity; when the order quantity is missing, one 30-day bucket of demand
  is assumed and the row says so (decided by Claude under operator delegation, 2026-10-02).
  Units short per year are capped at the annual demand (365 * d): without the cap a tiny
  reorder point combined with a tiny order quantity implies more shortage than there is demand.
  Items with no reorder point set are not ranked; they are listed separately for the planner
  to confirm whether they are intentionally non-stocked (made to order, special order). Both
  decided by Claude under operator delegation, 2026-10-02.
  The ranked drift list orders items by this amount.

## Dead and excess defaults (decided by Claude under operator delegation, 2026-10-05)

- **Window.** One trailing window of `EXCESS_COVER_MONTHS` months (default 12; one month is
  30 days) ending at the same as-of date the drift list uses.
- **Dead stock.** Stock on hand greater than zero with no demand in the window. It is
  `never_moved` when the item-site has no demand at all, `stopped` when its last demand is
  older than the window. All of it is counted as tied up.
- **Excess stock.** Stock on hand above what the window justifies: justified units =
  max(window demand, the recommended reorder point). Excess units = on hand - justified,
  listed only when positive, with months of cover (on hand / average monthly window
  demand) shown beside it.
- **Money tied up.** Excess units * unit cost. An item-site with no unit cost is still
  listed, flagged "unit cost missing", without a value, and ranked last.
- **Ranking.** By money tied up, largest first. Every row carries its inputs and its
  arithmetic.
- Measured on the reference demo data when decided: 228 item-sites with stock on hand; 95
  never demanded and 41 last moving more than 12 months ago; 7 without a unit cost.


## Stockout foresight defaults (decided by Claude under operator delegation, 2026-10-05)

- **Window.** The replenishment window is the item's real lead time: the pooled observed
  lead-time mean and standard deviation from "Lead-time statistics" (or the master-data lead
  time with the low-confidence flag when there are fewer than 3 observations), at the same
  as-of date the drift and excess lists use.
- **Which items.** Only item-sites whose reorder recommendation is computable (demand class
  smooth or erratic, status ok): the method needs the same daily demand mean and spread. The
  others are counted and reported as excluded, never given a number.
- **Probability of a stockout inside the window.** Lead-time demand is treated as normal with
  mean d * LT and standard deviation sqrt(LT * sd_d^2 + d^2 * sd_LT^2), the variance the
  safety-stock formula already uses. P(stockout) = 1 - Phi((on hand - d * LT) / that standard
  deviation). An item-site is **at risk** when P > 1 - service level (5% at the default 95%),
  which is exactly "on hand is below the recommended reorder point"; it is **likely** to run
  out before a replenishment ordered today arrives when P > 50%.
- **What each row shows.** P(stockout), the projected run-out date (as-of + on hand / d days),
  days of cover, the recommended reorder point, expected units short in the window (the normal
  loss function, as in "Money at stake"), and money at risk = expected units short * unit cost
  (no value, flagged "unit cost missing", when the cost is unknown).
- **Open supply.** Open purchase orders are not synced yet, so an item-site with an order
  already placed can be listed; every row says "open purchase orders not included" until they
  are.
- **Ranking.** By P(stockout), highest first, then money at risk. Every row carries its inputs
  and its arithmetic.
- **Evidence.** On the reference ERP's demo data at as-of 2026-06-30: 77 item-sites suit the
  method, 30 are at risk (exactly the 30 below their recommended reorder point) and 10 are
  likely to run out inside the lead time.

## Open purchase supply (decided by Claude under operator delegation, 2026-10-05)

- **Source.** Purchase-order lines from the ERP feed (`POLine`). Every goods line for a stock
  item at a site is stored with its open quantity in base units (`BaseOpenQty`), 0 once the
  line is completed or cancelled, so a line that closes in the ERP stops counting on the next
  sync. Each sync stamps its rows with the sync's as-of date; readers use only the rows of the
  latest sync, so a line that vanishes from the source does not count either.
- **What counts as supply.** Open lines (not completed, not cancelled, open quantity above 0)
  for stock items. Service lines, drop-ship orders (they never enter stock) and blanket orders
  (an agreement, not a delivery) are excluded.
- **Timing.** In stockout foresight, a line counts when its promised date falls on or before
  the end of the replenishment window (as-of + the lead time). A line more than 90 days past
  its promised date is stale and does not count; a line with no promised date does not count.
  Both are counted and named on the row so a planner can chase them.
- **Effect.** Stock position = on hand + the counted open supply; P(stockout) and expected
  units short use the position instead of on hand alone. Each row names the counted supply
  (units and the latest promised date) instead of "open purchase orders not included".
- **Evidence.** On the reference ERP's demo data all 20 open purchase lines are service lines
  or a 2015 blanket order, so the at-risk list is unchanged there (30 at risk, 10 likely). The
  rules exist for a live ERP, where open replenishment orders are routine and a list that
  ignored them would flag items already on order.
## Recommendation changes (decided by Claude under operator delegation, 2026-10-06)

The wedge promises that each number is defensible "in one screen", which includes the reason it changed. A planner who sees a recommended reorder point move from 140 to 190 needs to know whether demand rose, lead times stretched, or either one became more variable, before acting on it.

- **What is compared.** The recommendation at the as-of date against the recommendation at an earlier date, `since`, both computed by the same method at the same service level. The default `since` is the as-of date minus 30 days, one demand bucket, so the comparison shows what the latest month of history changed. `since` must be earlier than the as-of date.
- **Which item-sites are listed.** Every item-site whose recommended whole-unit reorder point differs between the two dates. An item-site whose status changed, for example from `ok` to `not_recommendable_demand` or to `insufficient_lead_time`, is listed with both statuses and no attribution, since there is no pair of numbers to compare.
- **Attribution method.** The reorder point is ROP = d * LT + z * sqrt(LT * sd_d^2 + d^2 * sd_LT^2), a function of four inputs:
  - demand level (d);
  - demand variability (sd_d);
  - lead time (LT);
  - lead-time variability (sd_LT).

  The change in the exact (unrounded) reorder point is split among the four inputs with the Shapley value. This is the documented cooperative-game decomposition: each input's contribution is its average marginal effect over every order in which the inputs could switch from their earlier values to their current ones, which takes 16 evaluations of the formula. The four contributions sum exactly to the total change, with no unexplained residual. That is why this method was chosen over one-at-a-time sensitivity, which leaves an interaction term a planner cannot interpret. The input with the largest absolute contribution is named the primary driver.
- **What a row shows.**
  - The reorder point then and now, both exact and in whole units.
  - The change in units.
  - The four inputs then and now.
  - The four contributions.
  - The primary driver.
  - Whether the master-data lead time was used on either date. A change in that flag is itself a reason, so the row notes it.
- **Ranking.** By the money the change represents: the absolute change in whole-unit reorder point times the item-site's unit cost. Rows without a positive unit cost follow, ordered by the absolute change in units.
- **Surfaces.** An API list and a CSV export, like drift, excess and stockout, and later an OData collection for Excel.

## Planner decisions (decided by Claude under operator delegation, 2026-10-07)

The north star says decisions get made "from memory" and that "nothing in the system notices" when they get worse. A ranked drift list that forgets what a planner already decided has the same flaw. On each visit the planner re-reads the twenty rows they handled last week, and when that planner is away nobody can tell which recommendations were acted on. Planner decisions give AIERP that memory, and they are the record that any later screen or approved write-back acts on.

- **What a decision is about.** One item-site's reorder-point drift recommendation, the money-ranked core of the wedge. Excess and stockout decisions can follow on the same model later.
- **The three decisions.**
  - `accepted`: the planner will change the ERP's reorder point to the recommended value. The row stays listed until the change is seen.
  - `dismissed`: the planner disagrees. A reason is required, because a dismissal without a reason cannot be reviewed.
  - `snoozed`: not now. It needs a `snooze_until` date at most 90 days ahead, and the row returns on that date.
- **What is recorded.** The decision, the reason, the snooze date, the recommendation it was made against (as-of date, recommended whole-unit reorder point, the ERP's reorder point at that moment, money at stake), who decided (the token subject) and when. Decisions are append-only history; the latest decision for an item-site is its current one. Nothing is written to the ERP. This is the read-first standing decision.
- **Who may decide.** For now, users with the `admin` role, since `admin` is the only role allowed to change data. Any authenticated user can read decisions. A dedicated planner role can replace `admin` here later without changing the model.
- **Effect on the drift list.** Each row carries its current decision. A dismissed row, or a row snoozed until a future date, is hidden by default, and `include_decided=true` shows every row. An accepted row stays listed with its decision so the planner can see the change is still outstanding.
- **When a dismissal stops applying.** A dismissal holds only for the recommendation it was made against. When the recommended whole-unit reorder point moves away from the dismissed value by at least the larger of 1 unit and 10% of that value, the row returns, with its earlier dismissal shown. The planner disagreed with a number, not with the item-site forever.
- **Applied.** An accepted decision counts as applied when the ERP's current reorder point for the item-site equals the accepted recommended value to within 1 unit. This is computed when read, from the synced item-site data, so applying a change needs no extra step. It also gives the product its own measure of value: the money at stake on recommendations that were accepted and then applied.
- **Surfaces.** An API to record and list decisions, the decision on each drift row, and a summary of accepted, applied and outstanding decisions with their money. Later, an OData collection for Excel.

## Excess and stockout decisions (decided by Claude under operator delegation, 2026-10-08)

Planner decisions on reorder-point drift (above) gave AIERP a memory for one list. The excess and stockout lists need the same memory, with one difference: accepting a drift recommendation means "set the reorder point to this number", but accepting an excess or stockout row does not by itself say what the planner will do. So each acceptance names an action.

- **Excess and dead stock.** One decision per item-site listed by the dead-and-excess list.
  - `accepted` names an action:
    - `stop_replenishing`: stop buying it;
    - `reduce`: use it up, transfer it or return it;
    - `dispose`: scrap or sell it. AIERP records the intent, and carries out only `stop_replenishing`, through the parameter write-back (AIERP-D-231); any write-down belongs to finance, inside the accounting boundary above.
  - `dismissed` requires a reason, for example seasonal stock or strategic spares.
  - `snoozed` lasts at most 90 days.
  - An accepted decision counts as **applied** when the item-site no longer appears in the dead-and-excess list. A dismissal lapses when the row's excess units grow by at least the larger of 1 unit and 10% of the units it was dismissed at.
- **Stockout risk.** One decision per item-site listed as at risk.
  - `accepted` names an action:
    - `expedite`: speed up an open order;
    - `raise_order`: place or increase an order;
    - `transfer`: move stock from another site.
  - `dismissed` requires a reason.
  - `snoozed` lasts at most 30 days, because a stockout is urgent.
  - An accepted decision counts as **applied** when the item-site is no longer at risk. A dismissal lapses when the row's stockout probability rises by at least 10 percentage points over the value it was dismissed at.
- **Shared rules.** These follow the drift decisions:
  - append-only history, where the latest decision per item-site is the current one;
  - a snapshot of the row the decision was made against;
  - the token subject as the decider;
  - `admin`-only writes for now, and reads by any authenticated user;
  - nothing written to the ERP.

  Each kind has its own table and API (`/api/excess/decisions` and `/api/stockout/decisions`), so the live drift decisions, their summary and the OData `Decisions` set do not change.
- **Order of work.** First the record-and-list API for each kind; then the decision on each list row, with default hiding; then an OData set for each, for Excel.

## Demand planning (operator decision, 2026-10-11; method defaults decided by Claude under delegation)

The replenishment wedge reads the past: twelve 30-day buckets, their average and their spread. That assumes next quarter looks like last year. It misses trend and season, it gives no recommendation at all for intermittent and lumpy items, and it has nowhere for a planner to say "this will change". Demand planning adds the forward view, in four steps the operator approved:

1. a statistical forecast per item-site;
2. forecast accuracy, measured and shown;
3. planner overrides with reasons;
4. replenishment that can read the forecast instead of the historical average.

- **Grain and horizon.** One forecast per item-site, in the same 30-day buckets the demand statistics use, for the next 12 buckets. History: up to the last 36 buckets when the item-site has them. Only complete buckets count, as for the demand class: a bucket the item-site existed for only part of would read the days before its first demand as zero demand and pull a young item-site's forecast down (decided 2026-10-12, under the operator's delegation of method choices). An item-site with less than one complete bucket still gets one. Twelve 30-day buckets cover 360 days, so a "season" of 12 buckets drifts about five days a year against the calendar. That is accepted and stated on the forecast.
- **Methods.** Each is simple enough to write out as arithmetic:
  - `mean`: the average of the last 12 buckets. This is today's behaviour and the baseline every other method must beat.
  - `ses`: simple exponential smoothing.
  - `damped_trend`: exponential smoothing with a damped trend.
  - `seasonal`: a level multiplied by a seasonal index of 12 buckets. It needs at least 24 buckets of history to compute. Because the backtest makes each of its 6 forecasts from the history before it, an item-site needs 30 buckets before `seasonal` can be chosen.
  - `sba`: the Syntetos-Boylan approximation, for intermittent and lumpy demand.
- **Which method an item-site gets.**
  - The demand class limits the candidates: smooth and erratic items choose among `mean`, `ses`, `damped_trend` and `seasonal`; intermittent and lumpy items choose between `mean` and `sba`.
  - Among the candidates, the method with the lowest mean absolute error wins a backtest: one-bucket-ahead forecasts over the last 6 buckets, each made using only the history before it.
  - A tie goes to the simpler method, in the order listed above.
  - An item-site with fewer than 12 buckets of history gets `mean`. An item-site with no demand gets no forecast.
  - A forecast is never negative.
- **Not a black box.** Every forecast states its method, the parameters it chose, how many buckets of history it used, its backtest error against the baseline's, and its arithmetic.
- **Accuracy, two ways.**
  - *Backtest accuracy* comes with every forecast: the weighted absolute percentage error (total absolute error over total actual demand) and the bias (total forecast minus total actual, over total actual) across the 6 backtest buckets.
  - *Realised accuracy* is measured on what actually happened. Each sync stores the forecast it would have given for the coming buckets. When a bucket completes, the stored forecast is compared with the actual demand and reported per item-site and overall. This is the number that says whether the forecast deserves trust.
    - A stored forecast is a *snapshot*: one per as-of date, never changed afterwards. A snapshot the sync stores is `live`. A snapshot computed afterwards for an earlier date, using only the history up to that date, is `backfill`. The two are kept apart, so a hindcast is never mistaken for what the system said at the time.
    - Only a completed bucket is compared. Demand after the latest demand date is unknown, not zero.
    - Realised accuracy is shown in two cuts, each labelled (delegated decision, 2026-10-13). *One period ahead* is the forecast for the next 30 days against what happened; it is directly comparable with the backtest. *All horizons* is everything compared, 1 to 12 periods ahead, and weights a period by how many snapshots covered it.
- **Planner overrides.**
  - A planner may replace one bucket's forecast for an item-site with their own quantity. A reason is required, because an override without a reason cannot be reviewed.
  - Overrides are append-only history, and the latest one for a bucket applies.
  - An override never changes the statistical forecast underneath it. Accuracy is measured for both, so the product can show whether overrides made the forecast better or worse.
  - For now a planner is a user with the `admin` role, as for planner decisions.
  - An override applies to a fixed calendar period. Forecast buckets are counted from the day the forecast is computed, so recomputing later must never move an override to a different period.
    - An override is stored with its dates, the first and last day of the period, never with the bucket's position. A request names the period by its first day, which must be the first day of a current forecast bucket (delegated decision, 2026-10-13).
    - On a later day the buckets have moved and may only partly cover an override's dates. The effective forecast is then worked out by the day: each day of a bucket takes one thirtieth of the override that covers it, or of the bucket's statistical forecast when none does, and where two overrides cover the same day the one stored last applies. The bucket states how many of its days are overridden, so a partly covered bucket is never shown as fully overridden or as untouched (delegated decision, 2026-10-13).
    - When a period has completed its override can no longer be changed or withdrawn. It stays in the history and is compared with the actual demand.
  - Withdrawing an override (operator decision, 2026-10-11): a withdrawal is a new row with a reason. The earlier override stays in the history, and the bucket's effective forecast returns to the statistical one.
  - No forecast, no override (operator decision, 2026-10-11): an override for an item-site with no statistical forecast is rejected. A manual forecast for a new item is a separate, later feature, not a side effect of overrides.
  - Near-simultaneous overrides (operator decision, 2026-10-11): both are stored and the one stored last applies. The result never depends on a clock tie.
- **Replenishment.**
  - Which demand the reorder point is built on is a system setting, `replenishment.demand_basis`: `history` (the default) or `forecast`.
  - On `forecast`, the daily demand is the effective forecast (override, else statistical) over the lead-time window, and the demand variability is the forecast's backtest error rather than the historical spread.
    - The lead-time window is the first whole days of the lead time after the as-of date (the lead time rounded up, at least 1 day and at most 360). Each day takes one day's share of its forecast period's effective quantity, and the daily demand is the window's total divided by its days (delegated decision, 2026-10-13).
    - The backtest error becomes a daily standard deviation as 1.2533 times the mean absolute error, divided by the square root of 30. For errors that are roughly normal the standard deviation is 1.2533 times the mean absolute error, and the error is measured per 30-day period, as the historical spread is. A forecast with no backtest uses the historical spread and says so (delegated decision, 2026-10-13).
  - The default stays `history`: a reorder point that moves because the method changed, without anyone deciding so, would break the trust the wedge depends on. An admin switches the basis after reviewing realised accuracy.
  - Whichever basis is active, each recommendation shows what the other basis would give.
  - Intermittent and lumpy items are not recommended by this formula. Their `sba` forecast is shown, and the section "Intermittent and lumpy demand" below covers how they get a reorder point.
- **Surfaces.** An API for forecasts, accuracy and overrides; a Demand view in the planner screen (a list of item-sites and a detail pane with history, forecast, accuracy and the override); later, an OData collection for Excel.
- **Later, not now.** The consensus and scenario layer on top of the forecast. Promotions, events and sales signals moved up: see "Demand signals" below.

## Scenario planning (what-if) (operator direction 2026-10-16; method defaults decided by Claude under delegation)

A recommendation answers what to order today; a planner often needs to know what a different assumption would do before changing anything. Scenario planning is a read-only simulation. It re-runs the reorder recommendation under one or more overridden assumptions and reports, for each item-site and for the portfolio, what changes against the live baseline. It writes nothing and makes no decision; it is a planner's "what if" before acting.

- **The baseline.** The recommendation each item-site has today, built from the active demand basis (the observed history, or the effective forecast when `replenishment.demand_basis` is `forecast`), the stored service level, and the observed lead-time statistics. A scenario is always measured against this live baseline, never against another scenario.
- **The levers (all optional; an omitted lever keeps the baseline value).**
  - A target **service level**: a probability strictly between 0 and 1, used as `z = NormalDist().inv_cdf(service_level)`.
  - A **demand multiplier**: a positive number scaling both the mean daily demand and its standard deviation.
  - A **lead-time multiplier**: a positive number scaling both the mean lead time and its standard deviation.
- **One formula, two inputs.** The same reorder formula is used for baseline and scenario — `safety_stock = z * sqrt(LT * sd_d^2 + d^2 * sd_LT^2)`, `reorder_point = d * LT + safety_stock` — so a scenario differs from the baseline only by the overridden inputs.
- **What it reports.** Per item-site: the baseline and scenario `reorder_point`, `safety_stock` and their whole-unit values, and the signed difference. And a portfolio summary: the total change in reorder-point units and safety-stock units, the number of item-sites whose reorder point rises, falls, and is unchanged, and the dollar impact — the sum over item-sites of the reorder-point-unit change times the item-site's `unit_cost`, item-sites without a unit cost contributing zero and counted separately.
- **Scope boundaries.** Only recommendable item-sites are simulated (the same intermittent and lumpy exclusions as recommendations). A scenario is never stored and never changes a stored recommendation, decision, or setting; the existing recommendation, drift, excess, stockout and demand behaviours are untouched.

## Aggregate planning (operator direction 2026-10-16; method defaults decided by Claude under delegation)

A recommendation answers one item-site and a scenario asks what an assumption would do to its numbers; before acting, a planner also needs the portfolio view. Aggregate planning is a read-only, aggregate view of the plan. It rolls the item-site numbers up to a grouping dimension and time buckets and shows, per group and bucket, projected demand against projected supply and the projected on-hand that results — so a planner sees where the portfolio is short or long over the horizon before acting on any single item-site. It writes nothing, stores nothing, and makes no decision.

- **The grouping dimension.** Site to begin with (the warehouse/location already on every item-site), and a product family or category when the ERP source provides one — the aggregate is keyed by (group, bucket) and never invents a hierarchy the source does not carry.
- **The horizon and buckets.** A fixed number of forward monthly buckets from an as-of date (default six months), each bucket a calendar month.
- **What it reports, per group and bucket.** Projected demand: the active demand basis — history or forecast, the same basis the recommendations use — summed over the item-sites in the group for the days in the bucket. Projected supply: open purchase-line quantities whose promised date falls in the bucket, from the open-purchase-supply data already ingested. Projected on-hand at the end of the bucket: the group's starting on-hand rolled forward — prior on-hand plus cumulative supply minus cumulative demand. And a short coverage signal: projected on-hand below zero in a bucket = a projected shortfall; a large positive projected on-hand relative to demand = projected excess. The projected-excess threshold is not fixed in this vision: it is an instance setting to be decided at the Aggregate planning screen slice with the reference dataset in view, while the shortfall signal (projected on-hand below zero) remains normative as written. The dollar view sums each quantity at the item-site `unit_cost`, item-sites without a unit cost contributing zero and counted separately, exactly as scenario planning already does.
- **Scope boundaries.** It aggregates only the item-sites the recommendations already consider (the same intermittent/lumpy exclusions), reuses the existing demand-basis, on-hand and open-purchase-supply reads with no new data source, and changes no stored recommendation, decision, or setting; the existing recommendation / drift / excess / stockout / demand / scenario behaviours are unchanged.
- **Planned supply (decided by Claude under delegation, 2026-10-18):** next to the as-is figures, the projection carries what the recommended policy would order. It is a periodic-review order-up-to (R, S) policy: at the start of each month, an item-site whose inventory position (on-hand, plus open purchase lines not yet received, plus planned orders not yet arrived) is below S = its reorder point plus that month's demand orders up to S, and the order arrives after its lead time. Planned on-hand is the as-is on-hand plus cumulative planned receipts. The first version triggered on the reorder point alone. That produced false shortfalls every other month wherever the reorder point is below a month's demand, and the live check on the reference data caught it (#490).
- **Scenario levers on the aggregate (shipped 2026-10-18):** the projection takes the Scenario screen's three levers with the same transform, and the Aggregate screen runs any saved scenario.
- **Financial reconciliation, next (recommended by Claude, operator-delegated 2026-10-19):** the S&OP loop closes with money. Per month, committed spend (the open purchase lines at cost, by promised month) and planned spend (planned receipts at cost, counted in the month received: the open-to-buy convention) are compared with a purchasing budget per month. The budget is a SYSTEM setting that only an admin edits. The next rung is spend-to-budget comparison only: the comparison shows the remaining budget or the overrun. Recommending which planned orders to defer with the least service impact is a follow-up, not part of this update. It reads only the projection and the stored budget, and it writes nothing else.
- **Multi-echelon (deferred, measured 2026-10-19):** the reference data carries no supply network, since nothing says which site replenishes which. Inter-site rebalancing found 3 opportunities across the 21 items stocked at more than one site, worth about $45k, almost all of it one item. It returns when a customer's data carries a network.

## Execution: release and tracking (operator direction 2026-10-21; method defaults decided by Claude under delegation)

The plan only matters once it becomes purchase orders, and those orders only matter until the goods arrive. The execution stage closes that loop. A month's planned orders are released into the ERP as purchase orders, and each released order is followed until it is received. The operator chose this stage on 2026-10-21 and approved the first live release (17 orders) the same day. The defaults below were decided under delegation.

- **How it meets the read-first decision.** A release is a previewed, human-confirmed action: a planner previews a month, and an admin confirms the exact set shown. Every order is created **On Hold**, so it is not sent to a vendor until the ERP's own approval workflow lets it through. Writing also needs the instance switch (`ACUMATICA_ALLOW_WRITES`). With the switch off, preview still works and confirm refuses. Nothing is released automatically.
- **One purchase order per site and vendor** for the month's planned orders, with lines at the planned quantity and the item-site's unit cost. Unit cost is rounded half-up to cents, as the ERP does, so the audit matches the order.
- **Idempotent by origin.** Each order carries an origin key (plan month, site and vendor). A repeated confirm finds the existing order and reports "Already in ERP" instead of creating a second one. If the plan for an origin that was already released has changed since, that group is refused as a conflict and recorded as an error, never reported as already in the ERP. Only one confirm runs at a time, and a second one gets 409. Every confirm is recorded in the `plan_releases` audit (outcome, order number, lines, value and actor), including releases that a failure aborts.
- **Tracking states, in precedence order:**
  - missing (the ERP no longer has it);
  - cancelled;
  - received (every active line complete or fully received);
  - awaiting approval (On Hold or Pending Approval);
  - late (an approved line past its expected date and not fully received). The expected date is
    the supplier's promise; a promise on or before the order date is only the ERP's default, so
    the expected date is then the order date plus the supplier's average lead time, the same
    rule Expedite uses (AIERP#643);
  - partly received;
  - open.

  Days waiting count from the order date while awaiting approval, and days late from the earliest late line's expected date.
- **In-flight supply.** The ERP's purchase-line feed shows an order only once it is approved (On Hold and Pending Approval orders are left out). Until then, a released order counts as on-order supply from the release audit, arriving in its plan month, so the plan never proposes it twice. From the order number's first appearance in the feed it counts from the feed alone.
- **Where it shows.** The Aggregate screen's Release to ERP panel (preview and confirm), the Releases screen (every released order, its state and lines), and an Overview card (counts and the oldest wait).
- **Not in this stage.** Changing or cancelling an order already in the ERP, approving it on the ERP's behalf, and receiving goods all stay in the ERP.

## Order quantities (wedge capability 2; method defaults decided by Claude under delegation, 2026-10-26)

The wedge promises recomputed reorder points **and quantities**. Until now a recommendation carried
a reorder point and only the shortfall below it, so at the reorder point itself it suggested
nothing. The ERP's own order quantity and max were synced but never compared with anything. The
defaults below were decided under delegation.

- **Order quantity** is the economic order quantity, EOQ = sqrt(2 * D * S / (h * c)). D is annual
  demand (365 times the recommendation's daily demand, on its active demand basis), S is the cost
  of placing one order, h is the annual holding-cost rate, and c is the item-site's unit cost.
- **Named assumptions, shown with the arithmetic:** S defaults to $50 an order and h to 25% a
  year. An instance overrides them with `AIERP_ORDER_COST` and `AIERP_HOLDING_RATE`, the way the
  service level is set today. Editing them on the Settings screen needs numeric settings, which is
  a later slice.
- **Bounds:** the quantity stays between 7 and 365 days of demand, so a very cheap or very
  expensive item never gets an absurd lot. A result that hit a bound says so.
- **No unit cost:** with no positive unit cost the quantity is 30 days of demand, the same
  assumption drift already makes, and it is labelled as assumed.
- **Recommended max** is the recommended reorder point plus the order quantity: the min/max form
  the ERP stores. Each recommendation shows it next to the ERP's current order quantity and max.
- **Not changed by this:** the shortfall figure (`suggested_order_quantity`), drift ranking, the
  aggregate projection and the release lines.
- **Planned orders keep the monthly order-up-to policy** (decided by Claude under delegation,
  2026-10-26). The aggregate plan reviews each item-site once a month and orders up to the reorder
  point plus that month's demand: a periodic-review (R, S) policy. The order quantity is for the
  ERP's continuous reorder-point settings, which trigger an order whenever stock falls to the
  reorder point. Using it in the monthly plan would mix the two policies, and it would move the
  projected spend without anyone deciding so.
- **Not in this stage:** supplier minimums, pack sizes and price breaks (the feed does not carry
  them yet), and quantity discounts.

## Planning assumptions and ABC classes (method defaults decided by Claude under delegation, 2026-10-26)

The assumptions behind every reorder point and order quantity are visible and can be changed
by an admin without a redeploy.

- **Numeric system settings** (admin only, Settings screen):
  - `replenishment.service_level`: default 0.95, between 0.5 and 0.9999;
  - `replenishment.order_cost`: default $50 an order, between 0.01 and 100,000;
  - `replenishment.holding_rate`: default 25% a year, between 0.01 and 1.

  A stored value wins. With none stored, the instance's environment value (`AIERP_SERVICE_LEVEL`,
  `AIERP_ORDER_COST`, `AIERP_HOLDING_RATE`) applies, and then the default. This is the order in
  which the planning code reads them.
- **ABC classes:**
  - **Annual value:** each recommendable item-site's annual consumption value is 365 times its
    daily demand (on the active demand basis) times its unit cost. Item-sites are ranked from
    highest to lowest value.
  - **Classes:** A while the share of total value before the item-site is below 80%, B while it is
    below 95%, and C after that. The item-site that crosses a boundary belongs to the higher class.
  - **Unclassified:** an item-site with no recommendation, no unit cost or no demand has no class.
  - **Where it shows:** `GET /api/abc` lists the classes.
- **Service level by class:** this is off by default (`replenishment.service_level_by_class`).
  - **When on:** A uses 98%, B 95% and C 90%, each its own setting.
  - **Unclassified item-sites:** they keep the single service level.
  - **What every recommendation says:** which level it used and why
    (`service_level_source`: the class, and "class" or "single").
  - **Why off by default:** turning it on raises the safety stock of A items and lowers it on C
    items, which changes money at stake across the board. That is a policy choice for the business
    to make, not a default.
- **Where it shows:**
  - the Work queue's Reorder points detail states the service level and class next to the order
    quantity;
  - the reorder-point CSV export carries the order quantity, max, class and service level as its
    last columns.

## Intermittent and lumpy demand (method decided by Claude under delegation, 2026-10-27)

The reorder-point formula assumes demand that is roughly normal from day to day. Intermittent and
lumpy item-sites, which have demand in only some months, break that assumption, so they had no
recommendation. On the reference data that is 21 of the 94 item-sites with demand, and several of
them have no reorder point in the ERP at all.

- **Method: empirical lead-time demand.** It takes every overlapping span of L days in the
  item-site's demand window, where L is the lead time rounded up to whole days. The window is the
  same 360 days the demand statistics use, or from the item-site's first demand when that is
  later. The span totals are sorted, and:
  - the reorder point is the total at the service level, for example the 95% quantile;
  - the safety stock is the amount by which that exceeds the mean span total, and never below 0.

  It needs no distribution. A planner can check it in one sentence: "in 95% of past 10-day spans,
  demand was at most this".
- **What it does not do yet:**
  - Lead-time variability is not included, because the span length is the mean lead time.
  - With fewer than about 20 demand events in the window, the quantile is coarse. The result
    states how many spans had any demand, so a thin history is visible.
- **Rollout.**
  - The empirical reorder point is first shown alongside, and the item-site's status stays "not
    recommendable", so nothing downstream moves.
  - Using it as the recommendation, so that it flows into drift, stockout and the plan, is a
    system setting, off by default, for the same reason as service levels by class
    (`replenishment.intermittent_recommendations`, #692).
  - **Per-item adoption (operator decision, 2026-10-28).** The switch is too blunt on its own:
    on the reference data one item-site is $861k of a +$1.6M change. So an admin can also
    adopt the empirical figure one item-site at a time. An adopted item-site is recommended on
    it whether or not the switch is on. The Settings card shows each item-site's dollar
    change and whether it is adopted, and the record keeps who adopted it and when
    (#707, #708, #709).

## Parameter write-back (operator decision, 2026-10-28; method decided by Claude under delegation)

A planner's accepted reorder-point decision changes nothing until someone retypes it into the ERP.
The write-back closes that gap: an admin pushes accepted values into the ERP that owns the
item-site. It covers BOTH ERPs (operator decision): Acumatica and Odoo.

- **What is written.** Each accepted drift decision that is still outstanding, meaning the ERP's
  reorder point differs from the accepted value by more than 1 unit.
  - **Reorder point:** the accepted value.
  - **Max quantity:** the accepted reorder point plus the item-site's current order quantity
    (max − min), so the order size does not change. When there is no order quantity, the max is
    the current max if that is not below the new reorder point, and the new reorder point otherwise.
  - **Safety stock:** not written. In both ERPs it does not drive a min/max reorder.
- **How it is written.**
  - **Acumatica:** the `ItemWarehouse` entity, whose `ReorderPoint` is the item-warehouse
    minimum. It takes two requests: one turns on the overrides, and a second sets the values.
    Values sent in the same request as the overrides are silently ignored (proven on the test
    tenant, on both seasonal and non-seasonal items).
  - **Odoo:** the product-warehouse reorder rule (`stock.warehouse.orderpoint` min and max). AIERP
    never creates a rule, and it refuses when there are several rules for the pair.
- **Safeguards.**
  - Writes stay off unless each ERP's write switch is set.
  - Every write is read back, and a mismatch is a failure, never a silent success.
  - Every attempt (written, unchanged or failed) is stored with who, before, requested and after.
  - A value already in place is not rewritten.
  - After a verified write, AIERP's own copy of the item-site is updated, so the decision reads
    "applied" at once.
- **Odoo current values.** AIERP also reads Odoo's reorder rules, so Odoo item-sites have a
  current reorder point to compare against (#714). Before that, Odoo's side of drift was blank.
- **How often a pushed value stays in the ERP.** To show how often a pushed value is changed again
  in the ERP, AIERP takes, for each item-site, its latest push that left the ERP holding the pushed
  values (outcome written or unchanged). Once a sync has read the item-site after that push, the
  push is read back. It is unchanged while the ERP's reorder point and max both equal the values
  read back after the write, to within 0.000001, and changed otherwise, including when the ERP no
  longer holds a value. An item-site that no sync has read since its push is not read back yet.
  Pushes of an accepted reorder-point decision and of DDMRP buffer levels are counted apart. AIERP
  keeps no history of the ERP's values between syncs, so a value changed and changed back between
  two syncs counts as unchanged.
- **A buffered item-site is DDMRP-managed** (decided by Claude under delegation, 2026-11-23). A designated item-site whose buffer zones can be computed (its zone row's status is ok) is written from its buffer: the top of yellow as the reorder point and the top of green as the max. Its accepted reorder-point decision is never listed or written, whether or not the buffer's levels are already in the ERP, so the two pushes never undo each other. A designated item-site whose zones cannot be computed keeps its decision push.
- **Stop replenishing is carried out in the ERP** (decided by Claude under delegation, 2026-11-23). For an item-site whose current excess decision is accepted with the action `stop_replenishing`, while the item-site is on the dead-and-excess list, the parameter write-back proposes a reorder point of 0 and a max of 0 in the ERP that owns it, so the ERP's own replenishment raises no more orders. An admin confirms each push, as for every parameter push (AIERP-D-2). The push is outstanding while the ERP's reorder point or max is above 0: an item-site whose values are 0 or not set has nothing to stop. While the decision stands and the item-site is listed, this push replaces the item-site's reorder-point and buffer pushes, so neither undoes it; once the item-site leaves the list, or a later decision replaces this one, they return. Open orders are not cancelled (AIERP-D-101). The actions `reduce` and `dispose` change nothing in the ERP here: a transfer is released from the transfer suggestions, and scrapping, selling, returns and any write-down stay in the ERP and finance (AIERP-D-50). Each push records what it carried out (an accepted reorder-point decision, buffer levels, or a stop), and the record of pushes counts the three apart; a stop push is unchanged while the ERP holds 0 and 0.
- **Slices:** #714–#719, then the planner screen.

## Demand-driven replenishment (DDMRP) (operator direction, 2026-10-28; method defaults decided by Claude under delegation)

Next after the parameter write-back. The method is the published Demand Driven MRP practice:
strategic buffers, buffer zones sized from average daily usage and lead time, net-flow
pull ordering, and on-hand execution alerts. AIERP implements the method. It makes no claim of
certification, and it sits alongside the reorder-point recommendations as a second planning mode
for the item-sites a planner chooses to buffer.

- **Positioning.**
  - **Who chooses:** an admin designates the buffered item-sites (decoupling points). Nothing is
    buffered by default.
  - **Suggestions:** AIERP suggests candidates without applying them: item-sites with steady
    demand (A or B class) and a lead time long enough for a buffer to matter.
  - **Scope of v1:** purchased item-sites only. AIERP has no bills of materials, so there is no
    multi-level decoupling yet.
- **Inputs, all already in AIERP.**
  - **Average daily usage (ADU):** actual demand over a rolling past window, 90 days by default
    (a system setting).
  - **Decoupled lead time (DLT):** the observed supplier lead time, falling back to the
    master lead time.
  - **Order cycle:** the cover days of the recommended order quantity.
- **Buffer profile factors** (system settings; defaults chosen inside the published ranges):
  - **Lead-time factor by lead-time category:** short (DLT under 15 days) 0.8, medium (15 to
    45 days) 0.5, long (over 45 days) 0.3.
  - **Variability factor by demand variability:** low (CV under 0.5) 0.25, medium (0.5 to 1.0)
    0.5, high (over 1.0, and every intermittent or lumpy item-site) 0.75.
- **Zones.**
  - **Red:** a base of ADU × DLT × lead-time factor, plus a safety part of that base × the
    variability factor.
  - **Yellow:** ADU × DLT.
  - **Green:** the largest of ADU × order cycle, the red base, and the minimum order quantity: the
    item's supplier minimum in the supplier terms the order plan uses for the item-site's source
    system (the default supplier, otherwise the lowest supplier code), 0 when there is none.
  - **Thresholds:** top of red, top of yellow (red + yellow) and top of green (red + yellow +
    green), each in whole units, each with its arithmetic shown.
- **Planning.**
  - **Net flow position:** on hand plus open purchase supply minus qualified demand.
  - **Qualified demand:** past-due and due-today sales-order demand, plus order spikes within
    the DLT horizon (a single order above 50% of the red zone). No open sales orders reach AIERP
    yet, so v1 states "no open sales-order demand in the feed" on every row. Feeding open sales
    orders from both ERPs is its own slice.
  - **Order signal:** when the net flow position is at or below the top of yellow, the suggested
    order is the top of green minus the net flow position, rounded up to whole units.
  - **Priority:** the net flow position as a % of the top of green, lowest first.
- **Execution.** On-hand status is on hand as a % of the top of red: "stocked out" at 0 or
  below, "critical" under 50%, "low" under 100%, otherwise "ok". These are the alerts a planner
  works first.
- **Recalculation and ERP fit.**
  - **Dynamic buffers:** zones recompute on every read from the rolling ADU, so they follow
    demand after each daily sync. Planned adjustment factors (seasonal or promotional ramps) are a
    later slice.
  - **ERP fit:** a min/max ERP cannot hold the zones directly. The documented approximation is
    top of yellow as the reorder point and top of green as the max. That is pushed through the
    parameter write-back, which is a later slice, and only for designated item-sites.
- **Ordering on a buffer's signal** (operator decision, 2026-11-22). With the system setting
  `supply_plan.plan_buffers_by_signal` on (the default), the order plan plans each designated
  item-site from its DDMRP buffer instead of its reorder point. When the buffer's net flow position
  is at or below the top of yellow, the item-site's line is due now, for the top of green minus the
  net flow position, rounded up to whole units (AIERP-D-151), and fitted to the supplier's terms
  like any line. When the net flow position is above the top of yellow, the item-site has no line,
  whatever its reorder point says. Every line says whether a buffer or a reorder point produced it.
  A buffer line is released like any other line, under AIERP-D-2. With the setting off, or when the
  buffers cannot be read, every item-site is planned from its reorder point.
- **Slices, in order:**
  - buffer designations and settings;
  - buffer zones (read-only API);
  - net flow position and order signals;
  - the execution alerts;
  - the planner view;
  - the open sales orders feed;
  - pushing buffer parameters through the write-back.

## Monthly S&OP review (operator direction, 2026-11-02; method defaults decided by Claude under delegation)

The Aggregate planning screen answers "what do the numbers say". This capability adds the process
around them: once a month the people who own demand, supply and money look at one page, agree the
plan, and the agreement is recorded with the figures they agreed to.

- **One page, no new numbers.** The review is a read over what already exists: the aggregate
  projection, spend against budget, realised forecast accuracy and saved scenarios. It computes no
  second projection and has no method of its own, so it can never disagree with the screens it
  summarises.
- **The review month is the current calendar month**, by the server date, and the default horizon is
  three months. Only the current review month can be signed off.
- **A sign-off freezes the figures.** The server computes the review at the moment of signing and
  stores that answer with the row. A client never supplies figures, so "what did we agree" stays
  answerable after later syncs move the numbers.
- **Sign-offs are append-only and admin-only.** A month can be signed off more than once, for
  example after a re-plan; the newest row is the current agreement and earlier rows stay as history.
  There is no route to edit or delete one. Reading them is open to any signed-in user.
- **A sign-off changes nothing else.** It is a record of agreement. It releases no order, sets no
  budget, adopts no scenario and changes no recommendation, and the review makes no ERP call of any
  kind. Acting on the agreement stays with the existing release, budget and scenario controls.

## Demand signals (operator direction, 2026-11-03; method defaults decided by Claude under delegation)

A forecast built from history cannot know about next month's promotion or a large order a salesperson expects to close. People do know. This capability lets them say so, keeps what they said separate from the statistics, and carries it into the plan.

- **What a signal is.** Known extra demand for one item-site over a stated window: a first and last day, the extra units in total, a kind (`promotion`, `deal` or `event`) and a required reason. An admin enters it, as for overrides. The extra units must be above zero. A known drop in demand is left to overrides for now.
- **It adds, and never replaces.** The statistical forecast stays as it is. The effective forecast is the override where there is one, else the statistical quantity, plus the signal's share. The units are spread evenly over the window's days. A day covered by an override takes no signal share, because an override states the planner's whole number for that period. Several signals on one day add together.
- **Only the future.** A signal's first day must be after the as-of date and its last day inside the forecast horizon. Past demand is already in the history. An item-site with no statistical forecast is refused, as for overrides.
- **Append-only, like overrides.** A signal cannot be edited or deleted. A withdrawal is a new row with a reason, and the signal stays in the history. A window that has fully passed can no longer be withdrawn.
- **It reaches the plan on either demand basis.** `replenishment.demand_basis` decides how the underlying demand is estimated, and its default stays `history`. A signal is a decision a person made, not a change of method, so the reorder point and the aggregate projection count it on both bases. It lands in the months its window covers, without being counted twice.
- **Measured.** The statistical forecast stays measured on its own, so the product can show whether people's signals made the forecast better or worse.
- **A CRM feed is a later decision.** The `deal` kind is entered by a person for now. CRM deals in this product family carry no item lines, so a live feed needs a data-model change there and an API credential, and those are operator decisions. Pipeline value is not turned into units by probability weighting.
- **Read-only toward the ERP.** Signals change AIERP's own forecast and plan. Reaching the ERP still goes through the existing preview, confirm and audit controls.

## Several ERPs, one planner (operator direction, 2026-11-05; method defaults decided by Claude under delegation)

A company that bought another company, or that runs different ERPs in different regions, plans each one separately today. A vendor's add-on covers only that vendor's ERP. This capability lets one planner see and act on stock held in several ERPs from one place, starting with Acumatica and Odoo.

- **What this proves first.** One screen where every row says which ERP it came from, the same item held in two ERPs is recognised as one item, and a planner can see one ERP running short of an item the other holds too much of. This is a proof over the two connectors AIERP already has, not yet a general connector framework.
- **Which ERP a row came from.** Every planning row carries its source ERP, and a planner can filter by it. Totals across ERPs stay available beside the split.
- **An item-site belongs to one ERP.** An item-site is still identified by its item and site codes. Today, when two ERPs use the same item and site codes, the later sync silently overwrites the earlier one. That is never acceptable. The first ERP to hold a pair keeps it, the other ERP's rows for that pair are not loaded, and the clash is shown to the planner. A person resolves it, because only a person knows whether the two are really the same stock.
- **Matching the same item across ERPs.** A person decides. Codes usually differ between ERPs, so AIERP suggests matches where the codes or descriptions agree exactly after trimming and ignoring case, and an admin confirms each one. A suggestion is never applied on its own, and there is no fuzzy matching for now, because a wrong match would quietly mix two items' figures. A confirmed match is stored in the existing cross-ERP code mappings, which draft purchase orders already read.
- **Comparing across ERPs.** For a matched item, the planner sees each ERP's on-hand, demand, cover and recommendation side by side, plus a flag when one side is short and the other holds excess. The two ERPs keep their own recommendations. They are not merged into one plan, because each one still buys and holds its own stock.
- **Moving stock between ERPs is not part of this.** AIERP shows the opportunity, but a move between ERPs is two documents in two systems, and no release creates it yet.
- **One currency for now.** AIERP does not convert currencies. Money is summed across ERPs on the assumption that they all report in the same currency. That limit is stated on the screen, and AIERP warns when it can see that the ERPs' currencies differ.
- **Writing back is unchanged.** Each ERP's writes keep their own switch, and every write still goes through preview, confirm and audit. Nothing here adds a write to either ERP.

## Service levels and their cost (operator direction, 2026-11-09; method defaults decided by Claude under delegation)

A planner knows the service level in force. What nobody can say from the ERP is what one more point of service would cost, class by class, or which levels to choose. This capability answers both, read-only.

- **The curve.** For each ABC class AIERP lists the service levels 90, 92, 95, 97, 98, 99 and 99.5 percent, plus the class's current level, with the safety stock each one needs in units and in money, its holding cost a year at the holding rate, and the step up from the level before. Safety stock is z times sigma for each item-site, with sigma the square root of LT times sd_d squared plus d squared times sd_LT squared, from the same inputs the reorder recommendation shows.
- **The fill rate to expect.** Each level also states the share of demand served straight from stock: 1 minus the expected units short per cycle (sigma times the standard normal loss at z) times the cycles a year (yearly demand over the order quantity), over the yearly demand. An item-site without a recommended order quantity takes no part, and the class says how many do.
- **What a level means is a setting.** `replenishment.service_target` is `cycle` by default: a level is the chance of not running out in a replenishment cycle. Set to `fill_rate`, a level is the share of demand served from stock, and each item-site's safety stock is solved to meet it: the smallest z between 0 and 4 whose expected shortage fits. The curve follows the setting, so its rows are always the stock the recommendations would hold.
- **One level for all, or one per class.** One service level for every class (95 percent) unless the per-class switch is on (A 98, B 95, C 90). These are settings, as every judgement call is.
- **The suggestion.** For an overall fill-rate target, AIERP names the combination of one level per class that reaches it at the lowest holding cost, next to what the current levels cost and achieve. The search is exhaustive over the ladder, so the answer is exact and its arithmetic is the curve's own. Nothing is applied: an admin changes the settings.
- **Nothing here writes to the ERP.** Changing a level is a settings change in AIERP, and the recommendations follow at the next read.
- **What the service reached is measured from.** To show the service the levels reach, AIERP keeps one on-hand snapshot a day: after each sync, every item-site's on-hand as the ERP reported it, with that row's own as-of time, stored under the UTC date of the latest as-of time, a second capture of the same date replacing the first. The measure reads only these snapshots: for each ABC class, the share of item-site days in stock (on hand above zero) among the item-sites with demand in the window, beside the class's service level. It is a share of days in stock, not a fill rate: demand the ERP never recorded, such as a sale lost while out of stock, is not counted. Nothing is rebuilt backwards from stock movements, because a history rebuilt from the movements the feed carries would drift wherever one is missing.

## Supplier order minimums (decided by Claude under operator delegation, 2026-11-11)

The order plan says what to buy from each supplier this week. The decision the ERP never makes comes next: the order is below the supplier's minimum, so do we wait, order anyway, or add something, and if so what? Buyers answer that from memory. AIERP answers it from the items that are due next.

- **The minimum is a fact AIERP is told, never one it guesses.** A minimum order value for a supplier of one source system, an amount in the same money as the order plan's values (AIERP does not convert currencies), entered by an admin. A supplier without one has no minimum. Reading it from the ERP comes later, where the ERP holds one.
- **The order plan says when an order is short.** Each supplier's group shows its value, the minimum, and the shortfall.
- **Top-ups come from what is due next.** For a short order AIERP suggests more lines from the same supplier: items not yet due, the soonest to reach its reorder point first, each fitted to the supplier's ordering terms, until the minimum is met. It looks no further ahead than a setting allows (30 days by default) and never suggests an item the excess list holds. Every suggestion states the days until the item would have been due and what holding it early costs.
- **When the minimum cannot be reached, AIERP says so,** and by how much, rather than padding the order.
- **Nothing is added silently.** Top-ups are suggestions. A release includes them only when the planner chose them, and it is previewed, confirmed and audited like every other release.
- **The inbox notices.** An order that is short of its minimum while one of its lines must be ordered within two days is an exception.
- **AIERP suggests the choice for a short order** (operator direction, 2026-11-22; method decided by Claude under delegation). For each supplier order below its minimum, AIERP suggests one choice and says why. While none of the order's lines must be ordered within the days the inbox uses (`exceptions.order_minimum_due_days`, 2 by default), it suggests waiting, since more lines fall due before any must go out. Once one must, it suggests adding the top-ups when they reach the minimum, and ordering anyway when they cannot. The holding cost of the top-ups is shown but does not decide, because AIERP does not know the cost of waiting or of a short order. The suggestion is advice: what a release includes is unchanged (AIERP-D-187). Each release of a short order will record the suggestion beside what was chosen, so the scorecard can measure how often they match.

## The exceptions inbox (operator direction, 2026-10-29; method defaults decided by Claude under delegation)

AIERP computes many signals: a buffer in the red, a likely stockout, a late order, a release that failed. Each lives on its own screen. A planner should not have to open every screen to learn what needs attention today. The inbox is the one triage list.

- **One list, read-only.** Every source of attention feeds it: the DDMRP buffers, stockout risk, expedite lines, releases and transfers that failed or are waiting in the ERP, failed parameter pushes, forecast bias, a missing S&OP sign-off, ERP code clashes, currency mismatches, opportunities across ERPs, high-risk suppliers and sole-source items short of stock. A new kind of signal becomes a new source of this list, not a second place to look.
- **Each row says what, where and how bad.** A kind, a severity (high or medium), the item, order or supplier it is about, and one sentence with the numbers.
- **A failing source never hides the others.** Each source is read on its own. One that fails is named in the answer, and the rest still list.
- **Stored ERP error text is never repeated here.** A failed release reads "release to the supplier failed"; the error itself stays with the release.
- **New, acknowledged, snoozed.** AIERP records the day it first saw each exception and marks the ones first seen today. An admin can acknowledge an exception, with an optional date to snooze it until; a snoozed exception leaves the list and the counts until that date.
- **The thresholds are settings.** How large a forecast bias must be (50 percent, over at least 3 compared periods), over how many of an item-site's latest forecast periods the bias is measured (6, each forecast one period ahead and at least 30 days after the one before, live and backfill snapshots alike), how many days a released order may wait for approval in the ERP (3), and the day of the month after which a missing S&OP sign-off appears (10).
- **In the app first.** Email or chat notices come later (operator, 2026-10-29). Until then nothing about an exception leaves AIERP.

## Supplier risk (operator direction, 2026-11-07 and 2026-11-08; method defaults decided by Claude under delegation)

A planner needs to see which suppliers put stock at risk, from what the suppliers actually did, not from a rating someone typed in.

- **Three signs, from data AIERP already reads.** *Late*: the supplier's on-time rate is below a floor (85 percent). *Variable*: the standard deviation of its lead time over the mean is at or above a ceiling (0.6), judged only when at least three receipts could be used. *Sole source*: for at least one item, every receipt in the last 730 days came from this supplier.
- **The rating.** High when a supplier is late or variable and is also the sole source of an item. Medium when it is late or variable without being a sole source. Low otherwise: depending on one reliable supplier is not a risk sign by itself. The floor and the ceiling are settings; both were calibrated on real receipts, where the first rule rated most suppliers high and told the planner nothing.
- **Shown where the decision is made.** The Suppliers screen lists the rating with its reasons and the items that depend on the supplier. The order plan and the expedite list mark a high-risk supplier. A high-risk supplier is an exception in the inbox.
- **Which lead time safety stock uses is a setting.** Every supplier's receipts for the item (the default), or only those of the supplier the item is received from most.
- **The order plan prefers the safer supplier** (operator, 2026-11-08). When an item has purchasing terms from more than one supplier and the default one is rated high or medium, the plan buys from a strictly lower-rated one and says why on the line. It is a setting, on by default. When the rating cannot be read the plan behaves as if the setting were off, and a release is previewed and confirmed as always.
- **Nothing here writes to the ERP.** AIERP does not change a default vendor; it chooses per order line and shows the reason.

## Second sources (operator direction, 2026-11-09; method defaults decided by Claude under delegation)

Knowing that an item depends on one supplier is half an answer. The other half: who else could supply it, and how much stock covers the time it takes to switch.

- **Who else.** For every sole-source item AIERP lists, first, the suppliers that already have purchasing terms for the item but have not delivered it, and then up to three suppliers of similar items. Each candidate carries its own risk rating.
- **Similar means the same item family.** Two items are similar when their codes start with the same characters: three by default, a setting, and 0 turns the suggestion off. It follows how the ERP's item codes are built; matching on description words was tried on real data and found too little.
- **Bridge stock.** What to hold until a second source can deliver is the item's daily demand times the bridge days. The bridge days are a supplier's own lead time when one with terms for the item states it; the item's observed lead time when suppliers with terms exist but state none; and the days needed to qualify a new supplier (60, a setting) when no other supplier has terms for the item. A stored lead time of 0 means the ERP holds none, never same-day delivery. The result is compared with the stock on hand, and the arithmetic is shown.
- **The inbox notices only the urgent ones.** A sole-source item is an exception when its supplier is high risk, no other supplier has terms for it and its stock is short of the bridge; the severity is high when there is no candidate at all.
- **Read-only.** AIERP says whom to approach and what to hold. It creates no supplier, no terms and no order.

## Settings: system and personal (operator direction, 2026-10-09; the scope rule decided by Claude under delegation)

- **A judgement call is a default plus a setting, never a constant in the code.** Where reasonable teams could want different behaviour (a threshold, a default action, an opening view), AIERP ships a stated default and lets it be changed.
- **Two scopes.** A *system* setting is policy: one value for everyone, changed only by an admin. A *personal* setting is one user's own and changes only how the product looks to that user; today that is the Work queue's opening filter.
- **Which scope a new setting gets.** System when it changes a number, a recommendation, a rule or anything sent to an ERP. Personal when it is a display preference.
- **Every change is on record.** A stored value names who set it and when, and an append-only trail keeps every change and every reset with the value before and after.
- **Safe by construction.** Only registered keys exist, each with a type, its allowed values and a default. A stored value out of range, or a settings read that fails, falls back to the default.
- **The first one set the pattern** (operator, 2026-10-09): a planner must choose an action before accepting an excess or stockout decision. That is the default, and a system setting can relax it.

## Guidance in the product (operator direction, 2026-11-11; method defaults decided by Claude under delegation)

This document steers what AIERP becomes. The people who use and audit AIERP should be able to read it where they work, see how it changed, and trace a decision back to the words it rested on. The guidance is kept as a register of keyed items; this section says what the product does with it.

- **The product shows its own guidance** (operator, 2026-11-11). There is a place in AIERP's screens where the guidance the product is built under can be read.
- **Changes to guidance can be traced** (operator, 2026-11-11). AIERP keeps the audit information needed to know what changed in the guidance, when, and who decided it.
- **A decision points at its guidance with a stamp** (operator, 2026-11-11). Where a decision rests on guidance, its source is stamped on it and can be traced to the guidance: a key and a short hash of the item's words, not sentences of reasoning.
- **What the screen shows.** Every item in force, by type and by area, with its key, its stamp, its statement, who decided it and when. Retired items are listed apart. Anyone signed in can read it.
- **Guidance is published into AIERP, never fetched.** An admin, or the deployment acting as one, publishes the register after it changes. AIERP makes no call out to read it, and it says when and from what source the guidance it shows was published.
- **AIERP checks what it is given.** A publication is taken whole or not at all. Every item's hash is worked out again from its words, and a publication with a wrong hash, an unknown type or a missing decision record is refused.
- **Every version is kept.** Publishing never deletes. An item whose words changed gets a new version, and the old one stays readable with the days it was current. An item that is no longer published stays readable too.
- **Guidance is read in the product and changed in the register.** The screen has no editing. An item changes only through a reviewed change to the register, by someone who holds the authority its type asks for.

## Comparative-research method (when no decision exists)

Answer by researching what **publicly documented practices across multiple established
products in this category converge on**, scaled honestly to our current stage — then
state that as the DECIDED TARGET. Identify the expected capability and the established
domain concepts; design the implementation, UX, terminology, defaults, and data model
**independently**. No single named product is the design ground truth, and matching a
specific product is never the goal. Where an established inventory-management method
exists in the public literature (service-level safety stock, EOQ and its critiques,
ABC/XYZ classification, forecast-error-based buffers), prefer the documented method over
an invented one, and state which method a number came from.

**When products and the discipline's practice disagree, the practice wins** (decided by
Claude on 2026-11-13, from the operator's question whether to "design toward business best
practices"). Where inventory and supply-chain management has an established, published
practice for a decision (a method or standard with a citable source, such as the ASCM
(APICS) body of knowledge, DDMRP as the Demand Driven Institute publishes it, or
Syntetos-Boylan for intermittent demand), AIERP designs to that practice, names it in the
decision, and, where businesses reasonably run it differently, ships it as the default with
a setting. When what established products converge on differs from that practice, the
practice wins and the decision says so. When the data AIERP holds shows that a practice
does not fit, the measurement decides, and the decision records the practice, the
measurement and the reason.

## Hard boundaries

- Never answer around a governance blocker, and never assume authority to write to a
  customer's ERP.
- Never present a recommendation without its inputs and arithmetic.
- Never claim parity with, or position against, a named product (legal wording rule).
- Never move accounting-adjacent scope (GL, tax, revenue recognition, payroll) into the
  product without an explicit operator decision recorded here first.
