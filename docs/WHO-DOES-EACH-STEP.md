# Who does each step of a planning process

AIERP-D-224 asks for this list, in force since 2026-11-13. It applies the company direction CO-N-1, that the software does the work people used to do, to AIERP-N-1: AIERP makes the inventory decisions that the ERP records but never makes. Each step a person still does says why, and what would let AIERP take it on.

Each step is marked as one of:
- **AIERP**: AIERP does it on its own;
- **proposed**: AIERP proposes it, and a planner or an admin approves;
- **planner**: a person does it, in AIERP or in the ERP.

A step marked **not built yet** is decided in the guidance but is not on main.

A step left to a planner names why, and the evidence that would let AIERP propose it. A proposed step names the evidence that would let AIERP do it unasked: its measured accuracy against the planner's own changes, whether it can be undone, and the money at stake. A new capability starts with AIERP doing the work and asks a planner only where this list says so.

- **Who approves.** Every change in AIERP needs the `admin` role, except personal settings and saved scenarios (AIERP-D-45). A planner role can replace it later.
- **Writes to the ERP.** A step that writes to the ERP does so only under AIERP-D-2: either a person approves that one write (an admin previews it and confirms it), or a standing grant allows it.
  - **Creating a grant:** an admin creates a standing grant for the release of the order plan's purchase orders, or of transfers (`/api/standing-grants`, #1173).
  - **Its limits:** each grant sets a limit per order, a limit per day, and the oldest synced data it may act on.
  - **Stopping them:** one switch (`erp.standing_grants_enabled`) stops every grant.
  - **Where they are managed:** the Releases screen shows the standing grants and their runs; an admin creates and revokes grants there (#1211).
  - **What runs today:** under a grant for the order plan, AIERP releases the due lines on its own after each sync, and records every run with what it sent and what it held back (#1198). Under a grant for transfers, AIERP sends the suggested transfer orders that pass the grant's limits to Acumatica on hold on its own after each sync, and records every run with what it sent and what it held back (#1243). A standing grant run that failed, sent nothing, held back, or stopped running shows in Exceptions (#1208).

  A parameter push always needs a person's approval of each write. Writes to either ERP also stay off until the instance switches them on (`ACUMATICA_ALLOW_WRITES`, `ODOO_ALLOW_WRITES`).
- **The data.** Every process reads AIERP's own synced copy of the ERP. The sync and the jobs after it (the forecast snapshot, the buffer status snapshot, the transfer-order status read, the standing-grant runs of the order plan and transfers and the exceptions' first-seen dates) run without a person, on the schedule the deployment sets.
- **The measures.** AIERP-D-225 asks AIERP to show how its answers did against the planner's changes. Today three answers are measured that way: the forecast, by realised accuracy with the effect of overrides and of demand signals measured apart; the stockout remedies, where the Overview's Stockout risk card shows how often accepted decisions took the proposed remedy, by kind (#1321, #1323); and the excess actions, where the Overview's Excess and dead stock card shows the same for the proposed action (#1336, #1338). `GET /api/decisions/summary` counts accepted, applied, dismissed and snoozed decisions with their money. The other measures named below are not built yet.

State on 2026-11-16. Update this file when a step changes hands. Each process section starts with a comment line, which does not render, naming the guidance areas it covers, so that documentation updates for a change in one of those areas reach the section; keep it when you edit the section.

## Replenishment settings
<!-- docs-follow areas: replenishment-defaults, recommendation-changes, service-levels-and-their-cost, intermittent-and-lumpy-demand, planning-assumptions-and-abc-classes, parameter-write-back -->

| Step | Done by | Why not AIERP (yet) | What would move it |
|---|---|---|---|
| Compute demand and lead-time statistics, and classify each item-site's demand | AIERP | — | — |
| Recommend safety stock, reorder point, order quantity and max, with the inputs and the arithmetic | AIERP | — | — |
| Rank drift (where the ERP's reorder point no longer fits) by money at stake, and explain why a recommendation moved | AIERP | — | — |
| Choose the service levels | proposed: a planner names a target fill rate, AIERP suggests the cheapest level per ABC class, and an admin sets the levels | A service level trades holding cost against service, and it moves every reorder point. AIERP applies nothing (AIERP-D-181). | Accuracy: the fill rate reached against the target, which AIERP does not measure yet (it shows the fill rate to expect). Undo: a setting resets at once, and every change is on the settings trail. Money: the suggestion states the change in holding cost a year. |
| Set the other assumptions: order cost, holding rate, what a service level means, and which receipts set the lead time | planner (admin) | The two costs are the company's own figures, which AIERP does not read. The other two are policy choices. | Cost figures read from where the company keeps them, so that AIERP can propose values. Not built. |
| Use an empirical reorder point for an intermittent or lumpy item-site | proposed: AIERP shows it for review; an admin adopts it per item-site, or turns on `replenishment.intermittent_recommendations` (off) | On the reference data one item-site carried over half of the money the switch moved (AIERP-D-125). | Accuracy: the stockouts and excess of adopted item-sites against their record before (not measured). Undo: an admin withdraws the adoption. Money: the Settings card shows each item-site's change in reorder-point value. |
| Decide each drift row: accept, dismiss with a reason, or snooze | proposed: AIERP recommends the value; an admin decides | The accepted value is the one pushed to the ERP, so a person decides it (AIERP-D-43). | Accuracy: the share of rows accepted against dismissed, by money; the decision summary counts them and `GET /api/drift/decisions/acceptance` reports the rate and money rate, shown on the Overview's Reorder points card (#1413). Undo: a later decision replaces the current one, and nothing reaches the ERP before the push. Money: each row's money at stake. |
| Push accepted reorder points and maxes into the ERP that owns the item-site (`POST /api/parameter-writeback/confirm`) | proposed: an admin selects the rows on Push to ERP and confirms | Writes to the ERP. AIERP-D-2 needs a person's approval of each parameter push; no standing grant may cover one. | Only a change to AIERP-D-2 by the operator. Accuracy: how often a pushed value is changed again in the ERP (not measured). Undo: the audit keeps each value before the write, but a reversal is made in the ERP by hand. Money: each row's money at stake. |
| Track: an accepted row reads "applied" once the ERP's reorder point is within 1 unit of it; a failed push is an exception | AIERP | — | — |

## Excess
<!-- docs-follow areas: dead-and-excess-defaults, excess-and-stockout-decisions -->

| Step | Done by | Why not AIERP (yet) | What would move it |
|---|---|---|---|
| Find dead and excess stock against the cover window (`EXCESS_COVER_MONTHS`, 12 months by default), price it and rank it by money tied up | AIERP | — | — |
| Decide each row: accept with an action (stop replenishing, reduce, dispose), dismiss with a reason, or snooze | proposed: AIERP proposes one action per row from what it holds (#1331): stop replenishing while stock is on order or the ERP's reorder point or max is above what demand justifies, dispose of dead stock whose demand stopped, reduce other excess; dead stock that never moved gets no proposal. The Work queue shows it in the row's detail (#1335); an admin chooses the action and decides | The proposal presses no action: with `decisions.require_action_choice` on (the default) a person chooses each action (AIERP-D-212). AIERP cannot tell long-dead stock from a new item or a strategic spare, and seasonal stock, return terms and resale value stay with the planner. | Accuracy: how often planners take the proposed action, by kind, shown on the Overview's Excess and dead stock card (#1336, #1338). Undo: a later decision replaces the current one. Money: the row's money tied up. |
| Carry out the action: stop buying, use up, move, return, scrap or sell the stock, and any write-down | planner, in the ERP and in finance | AIERP records the intent only, and a write-down belongs to finance (AIERP-D-50). | AIERP proposing the ERP change each action needs, approved under AIERP-D-2. Not built. A site's surplus already feeds the transfer suggestions. |
| Track: an accepted row reads "applied" once it leaves the list; a dismissal lapses when the excess grows by 10% (at least 1 unit) | AIERP | — | — |

## Stockout risk
<!-- docs-follow areas: stockout-foresight-defaults, open-purchase-supply, excess-and-stockout-decisions -->

| Step | Done by | Why not AIERP (yet) | What would move it |
|---|---|---|---|
| Work out each item-site's chance of running out within its lead time, counting on hand and the open purchase supply due in it, and rank by that chance | AIERP | — | — |
| List the open purchase lines to expedite: late, or arriving after the item runs out | AIERP | — | — |
| Raise likely and at-risk stockouts and the expedite lines in Exceptions | AIERP | — | — |
| Decide each row: accept with an action (expedite, raise an order, transfer), dismiss with a reason, or snooze for up to 30 days | proposed: AIERP proposes one remedy per row from what it already lists for the item-site, an open line to expedite, a transfer suggestion or an order-plan line, tried in the order the `decisions.remedy_order` setting holds (#1313); the Work queue shows it in the row's detail (#1315); an admin chooses the action and decides | The proposal presses no action: with `decisions.require_action_choice` on (the default) a person chooses each action (AIERP-D-212), and a dismissal or a snooze rests on facts AIERP does not hold. | Accuracy: how often planners take the proposed action, by kind, shown on the Overview's Stockout risk card (#1321, #1323). Undo: a later decision replaces the current one. Money: the row's money at risk. |
| Expedite an order with the supplier | planner, outside AIERP | It is a conversation with the supplier, and AIERP-D-8 allows no outbound call except to the ERP. | AIERP now drafts the request (the order line, the date needed, the units at risk) for the planner to send. |
| Track: an accepted row reads "applied" once the item-site is no longer at risk; a dismissal lapses when the chance rises by 10 points | AIERP | — | — |

Raising an order or moving stock uses the release steps of the order plan and of transfers below.

## The order plan and its release
<!-- docs-follow areas: order-quantities, execution-release-and-tracking, aggregate-planning -->

| Step | Done by | Why not AIERP (yet) | What would move it |
|---|---|---|---|
| Find the item-sites due for an order within the horizon (7 days by default) and the date to order by | AIERP | — | — |
| Choose the supplier: the default one, or a lower-risk one with the reason on the line (`supply_plan.prefer_lower_risk_supplier`, on by default) | AIERP | — | — |
| Fit each quantity to the supplier's minimum, lot size and maximum, with the arithmetic | AIERP | — | — |
| Release the due lines and the named top-ups the top-up suggestions hold to Acumatica as purchase orders on hold, one per site and supplier | AIERP under a standing grant for the order plan: after each sync, every supplier's due lines, within the grant's limits (`erp.standing_grants_horizon_days` days ahead, 1 by default; #1198). Without a grant it is proposed: an admin previews the orders and confirms them, one supplier at a time (`POST /api/supply-plan/release`). The admin can tick top-ups in the top-ups table, and the release body sends the ticked rows as `top_ups` from the top-up suggestions; held ones join their site/supplier group, unheld ones are unreleasable. The confirm sends the preview's request body with `confirm: true` added; ticking or unticking that block's top-ups, or choosing another horizon, closes the preview without a request. | Without a grant, AIERP-D-2 needs a person's approval of each release. Under a grant, a person decides on whatever the grant holds back: an order over its limit per order or per day, or every order when the synced data is older than the grant allows. | Accuracy: how often a run sends everything that is due, and how often a planner changes an order a run sent (neither is measured yet). Undo: the order lands on hold, so the ERP's approval still applies. Money: each order's value, within the grant's limits. |
| Release a month's planned orders from the Aggregate screen (`POST /api/aggregate/release`) | proposed: anyone signed in previews the month; an admin confirms | Writes to the ERP, as above. | AIERP-D-2 would allow a standing grant, but the planned grants name only order-plan and transfer releases (#1173). Undo: on hold, as above. Money: the month's total value. |
| Release Odoo lines: one draft purchase order per item-site, from the Work queue (`POST /api/replenishment/actions/draft-po`) | proposed: AIERP fills in the quantity and the unit cost; an admin types the supplier, previews and creates the draft | Writes to the ERP (AIERP-D-2). The order plan releases Acumatica lines only, and the screen does not fill in the supplier. | A release of Odoo lines per supplier from the order plan. Accuracy: not measured. Undo: the order stays a draft in Odoo until someone confirms it there. Money: the quantity times the unit price. |
| Approve the on-hold order in the ERP | planner, in the ERP | AIERP-D-2 keeps the ERP's own approval on everything AIERP releases. Changing, cancelling and receiving an order stay in the ERP too (AIERP-D-101). | Not planned: the hold is the ERP's own control. |
| Count a released order as on-order supply until the ERP's feed shows it, so the plan never proposes it twice | AIERP | — | — |
| Track each released Acumatica order (open, late, awaiting approval, partly received, received, cancelled or missing), and raise a failed release, or one waiting in the ERP for `exceptions.release_approval_days` (3 days) or more, in Exceptions | AIERP | — | — |
| Raise a standing-grant run that failed, sent nothing, held orders back or has not run for a day, in Exceptions (#1208) | AIERP | — | — |

The Releases screen tracks the Acumatica releases. Odoo draft orders are not on it. The Released cell reads an order released under a standing grant as "by standing grant #<id>".

## Transfers
<!-- docs-follow areas: execution-release-and-tracking, several-erps-one-planner -->

| Step | Done by | Why not AIERP (yet) | What would move it |
|---|---|---|---|
| Suggest moves between sites of one ERP: the largest need first, from the site holding the most beyond its reorder point plus one order quantity, and mark the plan lines a move could cover | AIERP | — | — |
| Release a chosen suggestion to Acumatica as a transfer order on hold (`POST /api/supply-plan/transfers/release`) | AIERP under a standing grant, within its limits; otherwise an admin previews and confirms; the quantity is always AIERP's current suggestion | Writes to the ERP. Under AIERP-D-2 each release needs a person's approval or a standing grant. | A standing grant for transfer releases (#1173 onward) now releases within its limits. Accuracy: how often suggestions are released as suggested (not measured). Undo: the order lands on hold, and no stock moves until the hold is released in Acumatica. Money: the suggestion's value at the receiving site's unit cost. |
| Release the hold in Acumatica and move the stock | planner, in the ERP | AIERP-D-2 keeps the ERP's own control on everything AIERP releases. | Not planned: the hold is the ERP's own control. |
| Move stock between Odoo sites | planner, in Odoo | Only Acumatica transfers are released. Odoo suggestions are listed without a release. | A transfer release for Odoo, under AIERP-D-2. Not built. |
| Move stock between two ERPs | planner, in both ERPs | A move between ERPs is two documents in two systems, and no release creates it (AIERP-D-174). AIERP shows the opportunity and the units that could move. | A release that writes both documents, each under AIERP-D-2. Not built. |
| Track: read each transfer order's status back from Acumatica, count a move in flight as on order at the receiving site and take it off the sending one, and raise failed moves and those still waiting in the ERP in Exceptions | AIERP | — | — |

## The forecast and its overrides
<!-- docs-follow areas: demand-planning, demand-signals -->

| Step | Done by | Why not AIERP (yet) | What would move it |
|---|---|---|---|
| Forecast each item-site 12 periods ahead, choosing the method by backtest | AIERP | — | — |
| Store the forecast as a snapshot each day | AIERP (an admin may also store one, `POST /api/forecast/snapshots`) | — | — |
| Measure accuracy: the backtest with every forecast, and realised accuracy against the snapshots, with overrides, demand signals, and stored corrections (`GET /api/forecast/accuracy/corrections`) measured apart | AIERP | — | — |
| Raise a forecast biased by 50% or more in Exceptions, measured one period ahead over the item-site's latest 6 periods, at least 3 of them measured (the thresholds and the window are settings) | AIERP | — | — |
| Override a period's forecast, with a reason, or withdraw an override | planner (admin) | An override carries what the planner knows and the history does not (AIERP-D-64). | AIERP's proposed correction where its own bias persists, measured against the overrides planners make. The forecast reads carry that correction since #1358 (`include_correction=true`), and since #1365 each override keeps the correction proposed for its period, when there was one; `GET /api/forecast/overrides/proposals` counts how often overrides take or follow it. Since #1375 each live forecast snapshot keeps the proposed quantity of every corrected period, and since #1376 `GET /api/forecast/accuracy/corrections` measures those proposals against what sold once their periods complete; the Demand screen shows that measure since #1378. The Demand screen shows it beside the forecast since #1359. The Overview shows how often overrides take or follow it in a "Forecast overrides" card since #1369. Realised accuracy already shows whether overrides helped. |
| Enter a demand signal (a promotion, deal or event), or withdraw one | planner (admin) | No promotion or deal feed reaches AIERP. A CRM feed needs a data-model change there and a credential, both operator decisions (AIERP-D-167). | A feed of deals or promotions with item lines, from which AIERP proposes signals. `GET /api/forecast/accuracy/signals` already measures whether signals moved the forecast nearer the actuals. |
| Choose whether reorder points use the history or the forecast (`replenishment.demand_basis`, history by default) | proposed: AIERP proposes the switch in Exceptions and on the Settings screen when the basis not in use was nearer what sold, one period ahead, by at least a margin over enough compared periods (`exceptions.basis_switch_margin`, 5 points, and `exceptions.basis_switch_min_periods`, 60; #1397, #1401); an admin switches the setting | The switch moves every reorder point, so an admin makes it (AIERP-D-74). | Accuracy: the realised error of each basis one period ahead, which the Settings screen shows beside the setting (#1382, #1389, #1394); how often admins take the proposal is not measured yet. Undo: switching back is one setting change, kept on the settings trail. Money: the change in reorder-point value a switch would make is not shown yet. |

## DDMRP buffers
<!-- docs-follow areas: demand-driven-replenishment -->

| Step | Done by | Why not AIERP (yet) | What would move it |
|---|---|---|---|
| Suggest buffer candidates: class A or B item-sites with demand and a lead time of a week or more | AIERP | — | — |
| Designate the buffered item-sites | proposed: an admin designates each one, from the suggestions or not | Where to buffer is a positioning choice, and nothing is buffered by default (AIERP-D-138). | Accuracy: how often admins designate the suggestions, and each buffer's time in each zone against the item-site's record before (not compared yet). Undo: an admin removes a designation, and nothing reaches the ERP before a push. Money: each candidate's annual consumption value. |
| Size the zones from average daily usage and lead time, and resize them on every read (the buffer factors are settings) | AIERP | — | — |
| Enter planned adjustment factors for a seasonal or promotional ramp | planner (admin) | A ramp comes from what people know about a season or a promotion. | AIERP proposing a factor from a demand signal or a seasonal forecast, measured against the factors planners enter. |
| Compute net flow (counting open sales orders), order signals and on-hand alerts, and raise stocked-out and critical buffers in Exceptions | AIERP | — | — |
| Order on a buffer's signal | planner, in the ERP | The Buffers screen shows the quantity to order, but no release is built from it: the order plan works from reorder points, not buffers. Pushed buffer levels let the ERP's own min/max reordering act on them. | Buffer signals as order-plan lines, released like the others under AIERP-D-2. Not built. |
| Push the buffer levels into the ERP: top of yellow as the reorder point, top of green as the max | proposed: an admin selects the rows on Push to ERP and confirms | Writes to the ERP. AIERP-D-2 needs a person's approval of each parameter push; no standing grant may cover one. | Only a change to AIERP-D-2 by the operator. Accuracy: not measured. Undo: the audit keeps each value before the write, but a reversal is made in the ERP by hand. Money: buffer rows carry no money at stake yet. |
| Store each buffer's status daily and show its time in each zone | AIERP | — | — |

## The monthly S&OP review
<!-- docs-follow areas: monthly-s-op-review, scenario-planning, aggregate-planning -->

| Step | Done by | Why not AIERP (yet) | What would move it |
|---|---|---|---|
| Build the review: demand, signals, supply, planned receipts, projected on-hand and spend by month, forecast accuracy, saved scenarios against the plan, and what moved since the last sign-off | AIERP | — | — |
| Set each month's purchase budget | proposed: AIERP proposes each month's budget (`GET /api/purchase-budgets/proposals`, #1414): the plan's committed and planned spend plus `sop.budget_proposal_margin` (5%), rounded up to three significant figures; the Settings budget card shows the proposals and, for an admin, a Use button that fills the set form with that month and proposed budget (#1430); an admin sets the budget | The budget is a finance decision that AIERP is told. | Accuracy: how often admins set the budget AIERP proposed: `GET /api/purchase-budgets/proposals` counts the months last saved at the proposal, below it or above it (#1432). Undo: an admin changes or deletes a month's budget at any time. Money: the month's committed and planned spend, beside the proposal. |
| Suggest which planned orders to defer in a month over budget: the most cover first, never one that would stock out | AIERP | — | — |
| Defer the orders | planner | A month's release takes every planned order, so no release leaves the suggested ones out. | A release that leaves out the chosen deferrals, under AIERP-D-2. Not built. |
| Ask what-if questions and save them as scenarios | planner (any signed-in user); AIERP runs the simulation | A scenario is a question a person asks. | AIERP proposing scenarios from its own signals, such as a high-risk supplier or a lasting forecast bias, measured against the scenarios planners save. |
| Agree the plan and sign the month off | proposed: AIERP proposes the sign-off in the review when no value figure moved more than a set change since the last sign-off (`sop.signoff_proposal_max_change`, 5%; #1403), and the S&OP screen's status card shows it (#1407); an admin signs the month off | The sign-off records that the people who own demand, supply and money agreed. It changes nothing else (AIERP-D-160). | Accuracy: how often admins sign off a month AIERP proposed: `GET /api/sop/signoffs/proposals` counts the months whose latest sign-off stored a proposal as proposed, not proposed, or without a proposal (#1435). Undo: sign-offs are append-only, and a later sign-off of the same month supersedes the earlier one (AIERP-D-159). Money: the spend against budget of the months agreed, which the review shows. |
| Raise a month not signed off by day 10 (`exceptions.sop_signoff_day`) in Exceptions | AIERP | — | — |

## Supplier risk and second sources
<!-- docs-follow areas: supplier-risk, second-sources -->

| Step | Done by | Why not AIERP (yet) | What would move it |
|---|---|---|---|
| Measure each supplier's deliveries: on-time rate against real promises, lateness and lead-time spread | AIERP | — | — |
| Rate each supplier High, Medium or Low from late deliveries, variable lead times and sole sourcing (the thresholds are settings) | AIERP | — | — |
| Mark high- and medium-risk suppliers on the order plan and the expedite list, and raise high-risk suppliers in Exceptions. The order plan's choice of a lower-risk supplier is listed with the order plan. | AIERP | — | — |
| Change an item's default vendor in the ERP | planner, in the ERP | AIERP chooses the supplier per order line and does not change a default vendor (AIERP-D-201). | A proposed vendor change under AIERP-D-2, once AIERP measures how often its lower-risk choice is released unchanged. |
| List second sources for each sole-source item: suppliers with terms that have not delivered it, then up to three suppliers of similar items, each with its rating | AIERP | — | — |
| Work out the bridge stock to hold until another supplier could deliver, compare it with on hand, and raise the urgent shortfalls in Exceptions | AIERP | — | — |
| Approach a second source, agree terms or qualify a new supplier | planner, outside AIERP | A commercial step: AIERP creates no supplier, terms or order (AIERP-D-206). | AIERP already names whom to approach. To propose more, it needs the outcome of each approach, which nothing records yet. |
| Hold the bridge stock | planner | AIERP shows the shortfall but turns it into no order and no reorder point. | A bridge-stock line on the order plan, released under AIERP-D-2, measured against what planners order for sole-source items. Not built. |

## Order minimums
<!-- docs-follow areas: supplier-order-minimums -->

| Step | Done by | Why not AIERP (yet) | What would move it |
|---|---|---|---|
| Store a supplier's minimum order value (`PUT /api/supplier-order-minimums`) | planner (admin, on the Suppliers screen's Order minimums form, or through the API) | AIERP is told a minimum and never guesses one (AIERP-D-183). | Reading the minimum from the ERP where the ERP holds one, which AIERP-D-183 leaves for later. AIERP would then do this step itself. |
| Show each supplier group's value, minimum and shortfall | AIERP, in `GET /api/supply-plan` and on the Order plan screen | — | — |
| Suggest top-ups: the same supplier's lines due next, up to `supply_plan.top_up_lookahead_days` (30 days) ahead, none on the excess list, each with its holding cost; or say how short the order stays | AIERP, in `GET /api/supply-plan/top-ups` and on the Order plan screen | — | — |
| Decide: wait, order anyway, or add the top-ups | planner | The choice weighs the holding cost of ordering early, which AIERP shows, against the cost of waiting or of a short order, which it does not know. | The planners' choices recorded beside AIERP's suggestions, which the Order plan screen shows, and a supplier's charge for a short order where the ERP holds one. |
| Release an order with the chosen top-ups | AIERP, in `POST /api/supply-plan/release` (the body's `top_ups` are passed through, at most 200 entries as sent) | An admin confirms it, and a release under a standing grant adds none (AIERP-D-187). | — |
| Raise an order below its minimum, with a line that must go out within `exceptions.order_minimum_due_days` (default 2 days), in Exceptions | AIERP, in `GET /api/exceptions` | — | — |

## The exceptions inbox
<!-- docs-follow areas: the-exceptions-inbox -->

| Step | Done by | Why not AIERP (yet) | What would move it |
|---|---|---|---|
| Gather every signal into one list: its kind, its severity, the item, order or supplier, and one sentence with the numbers. A failing source never hides the others. | AIERP | — | — |
| Record the day each exception was first seen, and mark the new ones | AIERP | — | — |
| Acknowledge an exception, or snooze it to a date | proposed: AIERP names the recorded decision that already answers an exception (#1344): the item-site's current accepted stockout decision, recorded on or after the day the exception was first seen, answers its stockout and buffer rows, and one that chose to expedite answers its expedite rows. The Exceptions screen shows it under the row's detail (#1347); an admin acknowledges or snoozes | An acknowledgement says a person has seen the exception and owns what follows, so AIERP acknowledges nothing on its own. The other kinds have no answering record yet, such as a later success after a failed release or push. | Accuracy: how often the rows admins acknowledge had an answer AIERP named at that moment, counted from the acknowledgement history (#1350) and shown on the Overview's Exceptions today card (#1353); rows AIERP shows as answered that stay unacknowledged are not counted yet. Undo: an admin removes an acknowledgement, and a snooze ends on its date. Money: none; an acknowledgement changes no plan. |
| Send notices by email or chat | not built yet | In the app first: nothing about an exception leaves AIERP until then (AIERP-D-195). | The operator's decision on notices (AIERP-D-195). |

Each exception links to the screen that explains it. Acting on it is a step of that process, in its own table above.
