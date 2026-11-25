# Changelog

All notable changes to AISCP (codename aierp) are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Everything merged since the 0.2.0 version change. Issue numbers are given where known; the
code is the authority for details.

### Added

**Replenishment**
- A recommended order quantity (the economic order quantity, kept between 7 and 365 days of
  demand) and a recommended max on every recommendation, on the drift rows and in the Work
  queue (#645, #647, #648).
- Service level, order cost and holding rate as numeric system settings, edited on the
  Settings screen (#652, #654).
- ABC classes by annual consumption value (`GET /api/abc`) and a "Value by class" card on the
  Overview (#657, #670, #672).
- Service levels by ABC class behind a switch that is off by default, with the class and level
  on every reorder-point row, in the Work queue and in the drift CSV export (#659, #661, #665,
  #667).
- Empirical reorder points for intermittent and lumpy item-sites, from the demand in every past
  lead-time span: shown for review on the Demand screen, and used as the recommendation when a
  switch is on or an admin adopts the item-site. A Settings card shows what they change in
  money (#681, #682, #684, #692, #697, #699, #707, #708, #709).
- A planning-parameters CSV sheet (`GET /api/recommendations/export.csv`), and download buttons
  for the CSV exports on the Work queue (#693, #694).
- Item descriptions in the Work queue rows and detail pane (#638).
- A per-class service-level lever on the what-if simulation and the Scenario screen (#676,
  #679).
- The Releases screen notes when a promised date is only the ERP's default (#644).
- The stockout list can include one proposed remedy per row, looked for in the order of a new
  `decisions.remedy_order` setting (#1313).
- The Work queue's Stockout risk detail pane now shows a Proposed remedy section for a selected
  Stockout risk row that has remedy data, with the remedy's proposal and reason or "No remedy
  proposed." when the remedy is not usable (#1315).
- Stockout decisions now store the remedy proposed for their item-site row when one is proposed,
  and `GET /api/stockout/proposals` counts the accepted decisions that proposed or chose each
  remedy kind (#1321).
- The Overview's Stockout risk card now shows how proposed remedies fared, with the accepted
  decisions that took a remedy, by remedy kind, and accepted decisions before proposals were kept
  (#1323).
- The Work queue's Excess and dead stock detail pane now shows a Proposed action section for a
  selected Excess and dead stock row that has remedy data, with the proposed action and reason or
  "No action proposed." when the remedy is not usable (#1335).
- Excess decisions now store the proposed action for their item-site row when one is proposed,
  and `GET /api/excess/proposals` counts the accepted decisions that proposed or chose each
  action kind (#1336).
- The Overview's Excess and dead stock card now shows how proposed actions fared, with the
  accepted decisions that took an action, by action kind, and accepted decisions before proposals
  were kept (#1338).
- A read-only route, `GET /api/drift/decisions/acceptance`, for any authenticated user, answers how
  often current planner decisions accept the recommended reorder point, with accepted, dismissed
  and snoozed counts, money at stake, acceptance rates, and raise/lower direction counts (#1412).
- The Overview's Reorder points card now shows how often the recommended reorder point is accepted,
  with accepted and decided counts, the acceptance rate, raise/lower direction counts, and, when
  measured, the share of money at stake, or that none are measured yet (#1413).
- A data stamp for the reorder recommendations, read in one query, reports the version of each input
  table, so a computed list can be told stale from current (#1463).
- The reorder recommendations are kept across requests in a process-wide cache keyed by the as-of
  date and the data stamp, so a repeated read whose inputs have not changed answers without
  computing (#1464).
- A read-only route, `GET /api/work/scorecard`, for any signed-in user, answers for eight work
  processes how many decisions were made, how many had a proposal, how many took the proposal as
  it stood and how many had none, with the share taken (#1485).
- A "Work scorecard" card on the Overview shows, for each process, how many decisions were made, how
  many had a proposal, and how often a decision took the proposal as it stood (#1494).
- The Work scorecard now includes order and transfer releases, with an automatic count of the releases
  made by a standing grant (#1495).
- The Work scorecard now shows, for release rows, how many decisions were made by a standing grant and
  how many by a person (#1499).
- `GET /api/work/scorecard` now answers, for order releases, how many were read back from the ERP
  and how many of those came back unchanged (#1514).
- The Work scorecard now shows, for order releases, how many released orders the ERP still holds
  unchanged, as a count and a percentage of the released orders read back (#1519).
- Release tracking lines now carry the stored source of the release row's line for the same item, when
  one was stored (#1559).
- The Work scorecard now shows, for orders on a buffer's signal, how many were read back from the ERP
  and how many of those came back unchanged (#1563).
- The Work scorecard now shows, for short orders, how many decisions were made, how many took the
  suggested decision, and how many were made by a standing grant (#1583).
- Odoo draft purchase order lines now include the item's base unit of measure, and a product without
  one fails before the order is created (#1593).

**Parameter write-back**
- Accepted reorder points pushed into the ERP that owns the item-site (Acumatica
  item-warehouse settings or Odoo reorder rules): preview, admin confirm, every write read back,
  and an audit of every attempt (#715, #716, #717, #718, #719).
- Odoo item-sites carry their current reorder rule, so drift has an ERP value to compare (#714).
- The Push to ERP screen, with each row's source: a decision or a DDMRP buffer (#728, #775).
- A read-only route, `GET /api/parameter-writeback/record`, for any signed-in user, answers, for pushed
  reorder points and buffer levels, how many were read back from the ERP and how many of those came
  back unchanged, with pushed, read, unchanged and rate for each kind (#1526).
- The Push to ERP screen now shows, for pushed reorder points and buffer levels, how many the ERP still
  holds unchanged, as a count and a percentage of the values read back, or that none have been read
  back yet (#1529).
- The Overview's Reorder points card now shows, for pushed reorder points, how many the ERP still holds
  unchanged, as a count and a percentage of the values read back, or that none have been read back yet
  (#1534).
- Parameter write-back pushes now store their basis, and the record counts them by it, with decision
  pushes as reorder-point, DDMRP pushes as buffer, and excess pushes counted in neither kind (#1587).
- Excess stops now appear in the Push to ERP preview and confirm, pushing their item-sites to a reorder
  point and max quantity of 0, and the push record reports them as excess (#1588).
- The Push to ERP screen now labels excess rows as stop replenishing, counts them in the summary, and
  reports how many of their pushed values the ERP still holds unchanged, as a count and a percentage of
  the values read back, or that none have been read back yet (#1589).
- Parameter write-back pushes now store their audit row as pending before the ERP call and set the
  row's outcome after the write, so a row still pending means the result is unknown (#1604).

**DDMRP**
- Buffer designations by an admin, buffer zones from average daily usage and lead time with the
  buffer factors as settings, net flow with order signals and on-hand alerts, and the Buffers
  screen (#739, #740, #742, #744).
- Qualified demand from open sales-order lines, read from Acumatica (`SOLine`) and Odoo (#751,
  #757, #759).
- Suggested buffer candidates, planned adjustment factors, and buffer performance from a daily
  status snapshot, on the Buffers screen (#778, #779, #780, #784).
- Buffer levels (top of yellow and top of green) pushed through the parameter write-back
  (#762).
- Proposed planned adjustment factors, read-only (`GET /api/ddmrp/adjustments/proposals`): one for each
  buffered item-site and coming forecast period whose forecast daily demand differs from the buffer's
  average daily usage by `ddmrp.adjustment_proposal_threshold` (25% by default) or more (#1459).
- The Buffers screen's Planned adjustments section now lists proposed adjustment factors from the
  forecast, showing the forecast and base daily usage and, when present, the current factor, and an
  admin can use a proposal to fill the adjustment add form without sending a request (#1468).
- Each saved adjustment records the factor proposed for its item-site and window at that moment, and
  `GET /api/ddmrp/adjustments/record` counts how many were saved at, below or above the proposal
  (#1475).
- The Buffers screen's Planned adjustments section now shows how often an adjustment was saved at
  the proposed factor, with how many were saved below or above it (#1481).
- The Releases screen now shows a Buffer badge after the item code on release lines ordered on a DDMRP
  buffer's order signal (#1560).
- The DDMRP green zone now uses the supplier minimum order quantity from the item's supplier terms, and
  `GET /api/ddmrp/zones` answers the value used as `moq` (#1602).

**Exceptions inbox**
- One triage list of what needs attention (`GET /api/exceptions`), from DDMRP buffers,
  stockout risk, expedite lines, failed releases and failed parameter pushes, and the
  Exceptions screen with a Signal filter (#795, #800, #820).
- Acknowledge or snooze an exception (admin), first-seen dates with a New badge and a New-only
  filter, and an "Exceptions today" card on the Overview (#796, #805, #809, #813).
- More sources: forecast bias (thresholds as settings), releases and transfer orders still
  waiting in the ERP, failed transfers, a month without an S&OP sign-off, ERP code clashes,
  currency mismatches, cross-ERP opportunities, high-risk suppliers and sole-source items short
  of stock (#818, #822, #865, #899, #903, #955, #995, #1019, #1032, #1068, #1089).
- Orders below a supplier's minimum that must be ordered within a configurable number of days
  (default two) now appear in the Exceptions inbox, high when due today and medium otherwise
  (#1169).
- Standing grant runs that failed, sent nothing, held documents back, or have not run for more
  than 26 hours now appear in the Exceptions inbox (#1208).
- `GET /api/exceptions` can include a `proposal` on each row, from the item-site's current
  stockout decision when it already answers the exception, behind an `include_proposal` query
  parameter that is off by default (#1344).
- The Exceptions screen asks for those proposals and shows, under a row's detail, the recorded
  decision that already answers the exception; acknowledging stays an admin's click (#1347).
- Exception acknowledgements now keep the proposal the exceptions list would have shown for the
  row at that moment, and `GET /api/exceptions/proposals` counts the stored acknowledgement
  events by kind and whether they had a proposal or a snooze (#1350).
- The Overview's "Exceptions today" card now shows how many of the stored acknowledgements had a
  proposed answer, as a count and a percentage, or that none are measured yet (#1353).
- The forecast bias exception now measures the bias one period ahead over each item-site's latest
  forecast periods, at least 30 days apart, as many as the new system setting
  `exceptions.forecast_bias_periods` says (default 6), so old long-range forecasts no longer decide
  it; the detail's period count now counts those distinct periods (#1357).
- The Exceptions inbox proposes switching the demand basis when the basis not in use was nearer
  what sold, one period ahead, by at least a set margin (5 points by default) over enough compared
  periods (60 by default); an admin makes the switch in Settings (#1397).
- The demand-basis switch proposal now shows under the Settings card's verdict, and the Exceptions
  screen labels its rows "Demand basis to review" (#1401).
- The Exceptions inbox now logs a warning when a source fails, naming the source and the exception
  class (#1596).

**Supply planning and transfers**
- Supplier ordering terms per item from Acumatica (`POVendorInventory`) and Odoo, shown on the
  Suppliers screen (#825, #827, #869).
- The order plan (`GET /api/supply-plan`): what to order within a horizon, from whom and by
  when, each quantity fitted to the supplier's minimum, lot size and maximum with its
  arithmetic, and the Order plan screen with a CSV download (#830, #839, #841, #876).
- Release of a supplier's order-plan lines to Acumatica as on-hold purchase orders, through the
  existing release path and audit (#842, #851).
- Site-to-site transfer suggestions before buying, on the Order plan screen with the plan lines
  a transfer could cover (#853, #856, #862).
- Release of chosen transfer suggestions to Acumatica as on-hold transfer orders: preview, admin
  confirm, idempotent by origin, audited (#880, #881, #883).
- Transfer-order status read back from Acumatica; a transfer in flight counts as on order at
  the receiving site and is deducted at the sending one; released transfer orders on the
  Releases screen (#893, #897, #919).
- A supply-plan summary and a "Supply this week" card on the Overview (#914, #917).
- Standing grants for order-plan and transfer releases, listed for any signed-in user and
  created or revoked by an admin (`/api/standing-grants`), with a system switch that stops all
  standing-grant writes (#1173).
- A standing-grant run of the order plan sends its due lines to Acumatica as on-hold purchase
  orders within the grant's document, daily and data-age limits, with a horizon setting for how
  far ahead it looks, and records every run (#1198).
- A read-only route, `GET /api/standing-grants/runs`, answers the stored standing-grant runs
  newest first, with a limit and a process filter, for any signed-in user (#1210).
- The Releases screen now shows standing grants and their runs, with whether the standing-grants
  switch is on and, for an admin, controls to create or revoke a grant (#1211).
- A standing-grant run of transfer releases sends its due transfer suggestions to Acumatica as
  on-hold transfer orders within the grant's document, daily and data-age limits, and records
  every run (#1243).
- A standing-grant run of transfer releases now holds back a document when a suggestion has no
  value or a value of 0 or below, before the document and daily limits, reduces no allowance for
  it, and reports the document's left-out value as the sum of its suggestions above 0 (#1302).
- A read-only route, `GET /api/standing-grants/record`, answers the stored standing-grant runs per
  process, with how often a run sent everything that was due, outcome counts, documents and value sent,
  and the latest run, for any signed-in user (#1502).
- Released purchase orders are read back from Acumatica, storing the returned status, lines, and check
  time on their release rows (#1508).
- A "Standing grants" card on the Overview shows, for the order-plan and transfer releases, how many
  runs sent everything that was due and what the last run held back for a person (#1503).
- By default, the order plan plans each buffered item-site from its DDMRP buffer's order signal instead
  of its reorder point, with a `source` field on every line and the
  `supply_plan.plan_buffers_by_signal` system setting to turn the behavior off (#1540).
- The Order plan screen now shows a Buffer badge in the item and site cell of each plan line planned
  from a DDMRP buffer's order signal (#1542).
- Released order-plan lines now record their source — buffer, reorder point, or top-up — in release
  previews, confirmed releases, stored release rows and the release audit, while aggregate release
  lines and older rows omit it (#1549).
- Each supplier entry in the top-ups answer (`GET /api/supply-plan/top-ups`) now carries an order
  suggestion and reason — wait, add top-ups, or order anyway — based on whether a line is due within
  the order-minimum due-days setting and whether the top-ups reach the minimum (#1567).
- The Order plan screen now shows a suggested choice in each short order's Suggested top-ups section —
  Wait, Add the top-ups, or Order anyway — with the reason, when a suggestion is available (#1569).
- Confirmed order-plan releases, by a person or a standing grant, now record, for each short supplier
  released, the top-up suggestion, the choice made (add top-ups or order anyway), and the actor
  (#1582).

**Monthly S&OP review**
- The S&OP review (`GET /api/sop/review`) and its screen: demand, supply, planned receipts,
  projected on-hand and spend against budget by month, with forecast accuracy (#927, #929).
- Sign-offs that store the figures agreed (admin), past sign-offs with their figures, and what
  moved since the last sign-off (#928, #931, #933, #935, #938).
- Saved scenarios compared with the plan (`GET /api/sop/scenarios`) (#941, #945).
- The S&OP review proposes signing the month off when no value figure moved more than a set change
  since the last sign-off (`sop.signoff_proposal_max_change`, 5% by default), and says which figure
  moved most (#1403).
- The S&OP status card shows the sign-off proposal, saying whether the month is proposed for
  sign-off and, when it is not, which value figure moved most since the last sign-off or that none
  can be compared (#1407).
- Each month's proposed purchase budget (`GET /api/purchase-budgets/proposals`): the plan's
  committed and planned spend plus the `sop.budget_proposal_margin` share (5% by default),
  rounded up to three significant figures, beside the stored budget (#1414).
- The purchasing budget card on Settings now shows each month's proposed budget and the month's
  spend beside the stored rows and, for an admin, a Use button that fills the set form without
  sending a request (#1430).
- Saving a purchase budget now records the proposal that stood at that moment, and the proposals
  read reports how many months were last saved at the proposal, below it or above it (#1432).
- A read-only route, `GET /api/sop/signoffs/proposals`, for any signed-in user, answers how often
  the months admins signed off were proposed, with signed, proposed, not proposed and
  without-proposal counts and one entry per signed month, newest first (#1435).
- The purchasing budget card on Settings now shows how many of the months with a proposal were last
  saved at the proposal, with counts below and above it, or that none have been saved yet (#1442).
- The S&OP status card now shows how many of the sign-offs compared with a proposal were signed
  while proposed, or that none have been compared yet (#1448).

**Demand signals**
- Demand signals (`/api/demand-signals`): known extra demand from a promotion, deal or event
  for one item-site and date window, entered by an admin on the Demand screen, append-only with
  withdrawals, and added on top of the forecast (#963, #964, #965).
- Signals counted in the reorder point and in the months of the aggregate projection, on either
  demand basis (#970, #971).
- Signals in the S&OP review: each month's signal units and the list of current signals (#975,
  #977).
- Forecast accuracy measures the effect of overrides and of signals separately
  (`GET /api/forecast/accuracy/signals`), shown on the Demand screen and in the S&OP review
  (#979, #980, #984, #985).
- The forecast list and by-key reads take `include_correction`, off by default. With it every row
  carries a `correction`: where the forecast bias exception finds the item-site's bias persists, a
  proposed quantity for each forecast period without an override or a demand signal (the forecast
  scaled by the measured actual over forecast), with the figures it rests on and a one-line reason;
  otherwise null (#1358).
- Forecast overrides now keep the correction proposed for their period when one is proposed, and
  `GET /api/forecast/overrides/proposals` counts how often the current overrides follow their
  stored proposals (#1365).
- The Demand screen shows the proposed correction beside the forecast: the reason, and each
  period's forecast and proposed quantity (#1359).
- A "Forecast overrides" card on the Overview: how often an override took the proposed quantity or
  moved the same way, and how many overrides had no proposal (#1369).
- Each live forecast snapshot keeps the correction proposed for every period that had one, so the
  corrections can be measured against what sold once their periods complete; a backfill keeps none
  (#1375).
- Realised accuracy measures the stored corrections once their periods complete: the forecast's
  error and bias as it was and as corrected, overall and per item-site, and whether the corrections
  would have moved it nearer what sold (`GET /api/forecast/accuracy/corrections`) (#1376).
- The Demand screen shows how the proposed corrections would have done, in a line under the demand
  signals line: the periods compared, the error as forecast and if corrected, and the difference in
  points (#1378).
- Each forecast snapshot also keeps what the history basis expected for every period, the
  item-site's mean daily demand times the period's days, in live captures and backfills alike, so
  the history basis can be measured beside the forecast (#1382).
- Realised accuracy measures the history basis beside the forecast once their periods complete:
  each one's error and bias against what sold, overall and per item-site, at one horizon or all,
  and which was nearer (`GET /api/forecast/accuracy/basis`) (#1389).
- The Settings screen shows a "Demand basis: history or forecast" card: which basis reorder points
  use, each basis's error one period ahead over the periods compared, which was nearer what sold,
  and the ten item-sites with the most demand (#1394).

**Several ERPs**
- An ERP's sync never overwrites another ERP's item-site or item name: the first holder keeps
  it, the database enforces it, and the clash is recorded (#994, #996, #999).
- Every recommendation names its source ERP, with a filter; `GET /api/erp-sources` lists the
  ERPs AISCP holds; the Work queue shows each row's ERP with an ERP selector (#1000, #1002,
  #1003).
- Suggested item matches across ERPs by the same code or description, confirmed by an admin on
  the Settings screen (#1006, #1009).
- The Across ERPs screen (`GET /api/cross-erp/items`): each matched item's ERPs side by side,
  the items short in one ERP with excess in the other, and the units that could move, from the
  short sites' gap up to their reorder point (#1010, #1018, #1022, #1039, #1040, #1045).
- The currency each ERP reports in, read from Odoo and Acumatica (`GET /api/erp-currencies`),
  a reporting-currency setting, and each side's currency on the Across ERPs screen (#1019,
  #1026, #1027, #1038).
- Odoo open purchase, open sales and demand line quantities are converted to the item's base unit
  before they are used (#1592).

**Supplier risk**
- A risk rating per supplier (`GET /api/supplier-risk`): Medium for late deliveries or variable
  lead times, High when the supplier is also the only source of an item, Low otherwise, with
  the thresholds as settings; on the Suppliers screen with the items each supplier alone
  supplies (#1050, #1051, #1057, #1064, #1066).
- Risk badges on the Order plan and Expedite screens (#1060, #1071).
- The order plan prefers a lower-risk supplier when an item has terms from more than one, and
  says why on the line; a setting, on by default (#1076, #1077).
- A setting to take the lead time behind safety stock from the item's main supplier only
  (#1056).
- A Request column on the Expedite screen opens a draft of the expedite request for a line, with
  copy and close controls, and sends nothing (#1307).

**Second sources**
- For each sole-source item (`GET /api/second-sources`): the other suppliers with terms for
  it, up to three suppliers of similar items, and the stock to hold until one could deliver,
  with the similarity and qualification days as settings (#1081, #1082, #1083, #1091).
- The Second sources section on the Suppliers screen, with a summary and a filter for items
  short of stock (#1087, #1094).

**Service levels and their cost**
- Per ABC class, the safety stock, its value, its yearly holding cost and the expected fill rate
  of each service level from 90% to 99.5% (`GET /api/service-levels/curve`), on a Settings
  card (#1096, #1098, #1100, #1105).
- A setting that makes the service levels fill-rate targets, with safety stock solved to meet
  them; the curve and the card say which meaning is in force (#1103, #1107, #1109, #1114).
- The cheapest per-class levels for a fill-rate target (`GET /api/service-levels/suggestion`),
  shown on the card (#1115, #1122).
- One on-hand snapshot a day: a maintenance entry point, `capture_on_hand_snapshot()`, stores the
  current `on_hand` rows in `on_hand_snapshots` under the UTC date of the latest `as_of`, replacing
  that date's rows and leaving every other date untouched (#1511).
- The share of days in stock per ABC class, from the daily on-hand snapshots (#1522).
- The Settings service-level card shows the share of days in stock per class (#1523).

**Supplier order minimums**
- A minimum order value per supplier, stored by an admin (`/api/supplier-order-minimums`)
  (#1118).
- The order plan's supplier groups carry the minimum and the shortfall (#1133).
- Top-up suggestions for an order below its supplier's minimum, from the lines due next within
  a look-ahead setting, with the cost of holding each one early (`GET /api/supply-plan/top-ups`,
  read-only) (#1145).
- The Order plan shows a supplier's minimum, a below-minimum badge and the suggested top-ups
  (#1167).
- The Suppliers screen shows supplier order minimums and lets an admin save or remove one
  (#1170).
- The order-plan release body accepts chosen top-ups, which are added to the release when still
  suggested and listed as unreleasable otherwise (#1191).
- The order-plan release body accepts at most 200 top-ups as sent, and a body with more than 200
  is rejected with 422 even when repeats would leave 200 or fewer distinct top-ups (#1225).
- The Order plan lets an admin tick suggested top-ups, shows a running total against the supplier
  minimum, and sends the chosen top-ups in the order-plan release (#1205).
- The order-plan release confirm now sends the preview's own body with only confirm added, and a
  change to a top-up or the horizon closes the preview without sending a request (#1237).

**Guidance in the product**
- Publishing the whole guidance into AISCP, which keeps every version: `PUT /api/guidance`
  (admin) and a backend command for the deployment, `python -m app.guidance_publish` (#1124,
  #1127).
- The current guidance, an item's history and what a stamp names (`GET /api/guidance` and two
  read routes), and the Guidance screen with each item's history, a find box that also looks
  up a stamp, and formatted statements (#1125, #1126, #1128, #1129, #1130, #1151).
- The Work queue and Decision log now show a status line when a list is cut at 1,000 rows, naming the
  full lists in the log (#1600).
- The Overview's to-decide labels and the Demand view's forecast list now say when their lists are cut
  at 1,000 rows (#1601).

**Documentation and CI**
- Backend line coverage in CI, reported and not enforced: the total, each package and the
  least-covered modules on the test job's summary, and `coverage.xml` as an artifact.
- A 20-minute demo walkthrough (`docs/DEMO.md`) (#915).
- The product vision gained new sections and is kept as a register of keyed guidance items
  (#1121).
- The merge gate's Playwright run serves the frontend root (#1080).
- The settings and API references are generated from the code and committed as
  `docs/reference/SETTINGS.md` and `docs/reference/API.md`, linked from the README (#1174).
- The Odoo test data now uses a neutral database name, and a backend test asserts that the old
  Odoo database name is absent from the backend (#1184).
- The docs check now reports routes, settings, and environment variables that a changed Markdown
  file names but the code does not define (#1175).
- The order plan's run holds back an order it cannot value (#1301).
- The nine backend-only API modules are marked with a comment naming the data each serves (#1374).
- A smoke e2e suite now signs in and opens every screen from the app's view table against the real
  backend, failing if a title is missing, API traffic does not settle, or a page error, failed API
  request, or 5xx response is recorded (#1575).

### Changed
- The product is published as AISCP, AI supply chain planning: the planning layer over the ERPs a
  company already runs, not an ERP. The codename aierp stays in the guidance keys, the product
  vision, the configuration and the service names; the name AIERP is kept for a full ERP
  environment planned much later as a separate product (AIERP-D-226).
- Released purchase orders still awaiting ERP approval count as on-order supply in the
  replenishment recommendations too, not only in the aggregate projection, so the order plan
  does not buy them again (#860).
- Draft purchase-order confirms in Odoo hold a per-item-site PostgreSQL advisory lock, so two
  confirms for one item-site cannot both create one, even across several API workers (#446).
- The decision summary and the exceptions list compute the recommendations once per request
  (#910, #913).
- The license is the AISCP Evaluation and Reference License, the one AIDW and AICRM use: free
  to view and to use for your own internal purposes, with no redistribution, modification or
  hosting, no warranty, and a clause that the user is responsible for every write to the ERP.
  It replaces a notice that granted no license at all.
- The planner screen now shows AISCP instead of AIERP in the page title, sign-in heading, header
  brand, guidance texts, Across ERPs notes, and Settings ERP-mapping texts (#1230).
- The Releases screen now shows a failed standing-grant run's Sent cell as the orders and value
  it tried to send, wraps long reason and revoke messages, and keeps the releases table at least
  four rows tall while the Transfer orders and Standing grants sections scroll inside themselves
  (#1233).
- The Releases screen's Released cell now reads an order released by a standing grant as by
  standing grant #<id> instead of the actor (#1310).
- On narrow screens the Demand screen now scrolls its view vertically, keeps the forecast list at a
  fixed height, and shows the detail pane at its full height below the list (#1387).
- The exceptions list now reads each shared input once per call, so `GET /api/exceptions` answers
  faster without changing its rows (#1410).
- The exceptions list now runs its reads on one database connection per call instead of one per
  query, so `GET /api/exceptions` answers faster (#1418).
- The exceptions list copies the rows it reads more cheaply, so `GET /api/exceptions` answers
  faster again; the rows it answers are unchanged (#1420).
- The expedite list takes each item's daily demand from the last day the feed has delivered, not
  from today, so days not delivered yet no longer count as zero demand; lateness still counts from
  today, and `GET /api/exceptions` computes the recommendations once (#1424).
- The order plan's top-ups answer faster: `GET /api/supply-plan/top-ups` computes the reorder
  recommendations once per call instead of three times, and its answer is unchanged (#1438).
- The DDMRP plan now reads open purchase and sales-order lines only for the buffered item-sites it
  plans, not for every item-site in the feed (#1548).
- Order plan builds now use one database connection for the whole build, and DDMRP zone rows read
  demand statistics only for the designated buffered item-sites (#1562).
- The app now pools database connections while serving requests, bounded by `DB_POOL_SIZE` (default
  10), so repeated requests reuse connections and the process never holds more than the bound (#1570).

### Fixed
- Release tracking counted a promised date that was only the ERP's default as lateness; it now
  judges lateness by the expected date, as Expedite does (#643).
- The drift CSV export's `safety_stock_units` column shows whole units (#674).
- A computed quantity that floating-point error put just above a whole number no longer rounds
  up one unit too many (#763).
- Each Odoo purchase line keeps its own identity on the Expedite list (#815).
- Open purchase lines are read from each ERP's newest sync, so one ERP's sync no longer hides
  another's lines (#871).
- The 0.2.0 entry left out item descriptions on the Expedite and Releases screens (#628) and on
  the drift, excess and stockout lists (#637), both merged before the version change.
- A clipboard copy on the Expedite screen that settles after the open draft has changed is
  dropped, so it no longer updates the status, focus, or selection of the draft that is open when
  it settles (#1322).
- Spec 638, the Work queue's proving test, no longer fails now and then under load: its checks of
  the item/site cell and the detail pane retry until they read the row the list re-rendered; the
  product is unchanged (#1470).
- A designated DDMRP item-site with an ok zone no longer gets a reorder-point decision row in the Push
  to ERP preview or confirm, whether or not its buffer row is outstanding (#1586).
- The Order plan screen now disables the horizon select and Refresh button, and the top-up checkboxes
  in a supplier's block, while that block's release confirm is in flight, leaving top-up checkboxes in
  other blocks enabled, so the confirm's outcome or error is shown when it answers (#1594).
- The SBA intermittent-demand forecast now starts its smoothed interval at the first non-zero
  quantity's position, counted from 1, instead of at 1 (#1599).
- A returned pooled database connection is no longer reachable through its handle, and a cursor that
  cannot be opened no longer masks the original error (#1610).
- Fixed a database connection pool stall when the connection factory changed while idle connections
  from an earlier factory were still open (#1611).
- The database connection pool no longer blocks the garbage collector or loses a pooled connection when
  the collector finalizes it before close, keeping the pool's open-connection count exact (#1612).

### Security
- `opentelemetry-api` is pinned to 1.45.2, which fixes CVE-2026-126360 (#1180).
- Admin access is now granted only by the admin role, and an admin group no longer grants admin rights
  (#1595).

## [0.2.0] - 2026-10-25

Everything built since the 0.1.0 repository was created. Issue numbers are given where known;
the code is the authority for details.

### Added

**Foundation**
- Product bootstrap: FastAPI backend, Alembic migrations, the proving-test harness, the
  CodeAgent contract gate and CI.
- Read models for item-sites, demand events, lead-time observations, on hand and open purchase
  lines, filled by the sync from the AIDW feed (Acumatica) and by a direct Odoo connector.
- Bearer-token authentication on every route except the health probe, Keycloak sign-in (PKCE)
  for the planner screen, and admin-only routes for every change to shared data.
- Pinned backend dependencies (`backend/constraints.txt`).

**Replenishment**
- Demand statistics with demand classes, lead-time statistics with an outlier fence, and
  recommended safety stock and reorder points, each with its inputs and arithmetic.
- Drift (the ERP's reorder point against the recommendation), recommendation changes explained
  by input, dead and excess stock, and stockout risk counting open purchase supply, with CSV
  exports.
- Planner decisions (accept, dismiss with a reason, snooze) for drift, excess and stockout
  rows, applied-tracking and a decision summary.

**Demand planning**
- Per-item-site forecasts chosen by backtest, forecast accuracy (backtest and realised against
  snapshots), planner overrides with a reason, and a setting for history or forecast as the
  reorder basis.

**Scenarios and aggregate planning**
- What-if simulation (service level, demand and lead-time levers) and saved scenarios.
- The monthly aggregate projection by site with the planned orders the policy would place,
  monthly purchase budgets (#503), spend against budget (#508, #512, #513) and deferral
  candidates for over-budget months (#520, #529).

**Execution**
- Release of a month's planned orders to Acumatica as on-hold purchase orders: preview, admin
  confirm, idempotent by origin, audited (#534, #537, #538, #542, #543, #556, #558).
- Release tracking and the Releases screen: late, awaiting approval (including pending
  approval), partly received, open, received, cancelled, missing (#560, #565, #567, #573, #574).
- Released orders awaiting ERP approval count as on-order supply, so they are never planned
  twice (#577).
- Draft purchase orders in Odoo from the Work queue, audited, behind a write switch.
- An Overview card for released orders (#583).
- Supplier delivery performance: on-time rate, lateness and lead-time spread per supplier, with
  default promised dates (on or before the order date) not counted as promises (#603, #604,
  #605, #613).
- The expedite list and screen: open purchase lines that are late or due after the item runs
  out, ranked by urgency (#607, #608).
- Supplier names on the Suppliers, Expedite and Releases screens, and item descriptions in the
  expedite and release answers, from the ERP master data (#617, #618, #619, #627).

**Data and operations**
- Feed status (`GET /api/feed/status`) and real "Synced" / "Data through" labels in the planner
  (#572, #581).
- An OData v4 feed for Excel and other machine consumers, on its own credential.
- A Docs check for Markdown-only pull requests (#601), and backend CI that runs when `docs/`
  changes (#616).

### Changed
- Inventory-item create, update and delete require the admin role (#586).
- Defaults copied from another product were replaced with AISCP's (#589).
- `backend/run_tests.sh` starts its own test database container (#590).

### Fixed
- The bootstrap entry above named a `supplier_lead_times` resource; the read model that shipped
  is `lead_time_observations`.

## [0.1.0] - 2026-08-25

### Added
- Initial repository: product vision, licence, and the CodeAgent product configuration.
