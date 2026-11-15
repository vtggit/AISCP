# AIERP demo walkthrough

A 20-minute guided tour of AIERP for someone who plans inventory. It follows one story: what needs attention today, why, what to order or move, and how the decision reaches the ERP and comes back.

Numbers change with every daily sync, so the figures below are examples. Read the live ones off the screen.

## Before you start

- Sign in as an **admin**. Release controls, ERP pushes and acknowledgements are admin-only, and a planner sees read-only screens.
- Check the sidebar footer: "Synced today 07:0x UTC" means the overnight feed ran. The header's "Data through" date is the newest demand date in the feed.
- Every write to the ERP is **preview first, confirm second**. Nothing reaches the ERP until an admin clicks Confirm, and every confirm is audited.

## The tour

### Overview: the morning glance

Start here. Point out three things:

- **Money at stake**: accepted planning decisions and how much of that money has actually reached the ERP.
- **The three decision lists** (Reorder points, Excess and dead stock, Stockout risk), each with its count to decide.
- **Released orders**: what AIERP sent to the ERP and where it stands, for example "Awaiting approval 16, oldest waiting 12 d".

### Exceptions: one inbox for everything that needs attention

Every signal from every screen, ranked by severity:

- buffers in the red, likely stockouts, late purchase lines;
- forecasts that keep missing;
- releases still waiting for approval in the ERP, and failed releases or pushes;
- suppliers rated High risk, and items whose only supplier is High risk, with no other supplier on terms and too little stock to bridge to a new one.

Show the **Signal** filter and **New only**: the inbox remembers what it has already shown you. An admin can **Acknowledge** or **Snooze 7 days**. Every row links to the screen that explains it.

### Work queue: decide

Open a row in **Reorder points**. The current ERP value sits next to AIERP's recommendation, with the money at stake and the reasoning: demand, lead time, service level and the arithmetic. Accept, dismiss or snooze it. The decision lands in the **Decision log**.

### Demand: why the numbers are what they are

The forecast basis per item-site (smooth, intermittent, erratic, lumpy), the backtest error, and the realised accuracy against what actually happened. Overrides are admin-only and logged.

**Demand signals** are for what history can't know: next month's promotion, or a large order a salesperson expects. Open an item-site and use **Add signal** (admin) to give a kind, first and last day, total extra units and a reason. The units are spread evenly over the window and added on top of the forecast. The statistical forecast underneath stays as it was. A day covered by an override takes no share of the signal, because the override already states the planner's whole number.

The signal reaches the reorder point and the aggregate projection whichever demand basis is set. The order plan moves only if the item's position falls below the new reorder point, so pick an item with little stock to show that step. A signal can't be edited or deleted. **Withdraw** adds a row with a reason and the signal stays in the history, so anything you enter stays on the demo system afterwards.

Under the realised accuracy, a **Demand signals** line says whether people's signals helped: the error with and without them, on the periods that carried one. It stays hidden until a daily snapshot has stored a standing signal and that forecast period has completed, so expect at least a month before it appears. A demo system with no such period shows nothing here, and that is correct.

### Order plan: what to buy, from whom, by when

- One block per supplier, each line fitted to that supplier's minimum, lot size and maximum. The **Why** column shows the arithmetic, for example "raw 329 (shortfall below reorder point): 329".
- **Download CSV** hands the plan to a buyer.
- **Release to Acumatica** (admin) previews the draft purchase orders, then **Confirm** creates them **on hold** in the ERP for a buyer to approve.
- Orders already released but not yet approved count as on order, so tomorrow's plan does not buy them again.
- When an item has terms from more than one supplier, the plan buys from the lower-risk one and says why in **Why**, for example "Chosen over Net Jenn Toy Development (High risk: late deliveries, variable lead times)". A setting turns this off, and the release still goes through preview and confirm.

### Transfers: move before you buy

At the bottom of the Order plan, **Show transfer suggestions**. Where one site holds more than it needs and another is short of the same item, AIERP suggests a move. The order-plan line it would cover is marked "transfer of N from site X could cover this".

**Release** (admin) previews the transfer, and **Confirm transfer** creates a one-step transfer order on hold in Acumatica. AIERP reads its status back every night. Until Acumatica releases it, the move counts as in flight: incoming at one site, committed at the other.

### Releases and Expedite: closing the loop

- **Releases**: every order AIERP sent, matched against what the ERP has received.
- **Expedite**: open purchase lines that are late, or will land after a projected stockout.

### Suppliers: who delivers

On-time rate, lateness and lead time per supplier. **Supplier terms** shows the minimums, lot sizes and lead times behind the order plan's adjustments.

**Supplier risk** rates each supplier High, Medium or Low, with the reasons. A supplier is a risk when it delivers late (its on-time rate is below a setting, 85% by default) or its lead times vary widely (their variation is at or above a setting, 0.6 by default, judged on at least 3 receipts). That makes it Medium, and High when it is also the only supplier of some item, because then nothing covers for it. Being the only supplier alone does not raise the rating. The **Order plan** and **Expedite** mark each High or Medium supplier with a badge, so the risk shows where the order is placed and where it is chased. **Show items** on a risk row lists the items that supplier alone supplies. On the demo data, 8 of 17 suppliers are High.

**Second sources** turns that list into action. For each item only one supplier has delivered, it suggests who else could supply it: first the suppliers that already have terms for the item, then up to three that supply items with the same code prefix. It also says how much stock to hold until another supplier could deliver. That is the item's daily demand times the alternative's lead time, the item's own lead time when the alternative has none on file, or 60 days to qualify a new supplier when there is no alternative at all. It shows what is short against stock on hand. On the demo data, 72 of 75 such items have a candidate, and 6 are short. Tick **Only items short of stock** to work through those.

### S&OP review: the monthly agreement

One page for the monthly demand, supply and money meeting. It calculates nothing new: the figures come from the same projection and spend behind the Aggregate screen, and the same forecast accuracy as the Demand screen.

- **The month table**: demand, open supply, planned receipts, projected on hand and spend against budget, for the next 3, 6 or 12 months. A site under **Short as is** runs out without new orders. Compare it with **Short with plan** to see what the planned orders fix. **From demand signals** shows how many of a month's demand units are something a person said, not a statistic.
- **Forecast accuracy**: the error overall and one month ahead, and the bias. Separate lines show how far planner overrides and demand signals each moved the error, once there is something to measure.
- **Show demand signals**: every signal that hasn't been withdrawn, by start date, so the meeting can see what has been promised, by whom and for when. It's read-only here. Adding and withdrawing stay on the Demand screen.
- **Compare saved scenarios**: the newest five saved scenarios against the plan, with the change in demand and spend. Save one on the Scenario screen first, or this section stays empty.
- **Sign off** (admin): records that the month was reviewed and stores that day's figures. It changes nothing in the plan or the ERP. A sign-off can't be edited or deleted, so it stays on the demo system afterwards.
- **Since the last sign-off**: once a sign-off exists, what has moved since then, month by month, including the units from demand signals. That row is blank against a sign-off made before signals appeared on this screen.
- **Show sign-offs**: past sign-offs, each with the figures it agreed to, so "what did we agree last month?" has an answer after the numbers have moved.

Once a team has signed off at least once, a month that passes day 10 with no sign-off appears in Exceptions as **S&OP sign-off due**. The day is a setting.

### Across ERPs: one planner over Acumatica and Odoo

AIERP reads Acumatica and Odoo side by side. Each ERP keeps its own codes, sites and figures. AIERP never adds one ERP's stock to another's.

- **Work queue**: each row carries a tag naming the ERP it came from (a row whose item-site no ERP has stored shows none), and the ERP selector in the tab bar (starting on **All ERPs**) narrows the queue to one of them.
- **Settings, Suggested matches**: items that look like the same thing in both ERPs, matched by the same code or by the same description. **Confirm** (admin) stores the match. Nothing is matched without a person pressing it.
- **Across ERPs**: each matched item with both ERPs side by side: on hand, daily demand, days of cover, reorder point and suggested order per site. An item short in one ERP while the other holds excess comes first, tagged with which ERP is short, with how many units could move: the short sites' gap up to their reorder point, capped by the other ERP's excess. Each side also says which currency its ERP reports in. The screen only shows the opportunity. It does not create a move between ERPs.
- **Exceptions**: **Short in one ERP, excess in the other** lists each such item, with the units, and links to Across ERPs. **Same codes in two ERPs** appears when two ERPs report the same item at the same site, and AIERP keeps the first one's figures. **ERP reports in another currency** appears when an ERP's companies report in a currency other than the reporting-currency setting (USD by default). AIERP does not convert currencies.

On the demo system, CONTRAIN1 (Stamina In-Motion Elliptical Trainer) is matched to an Odoo test item, ELLIP-STAMINA, that holds 40 units with no sales. Acumatica site 154 is likely to stock out, so the screen shows a real opportunity of up to 32 units. The Odoo side is seeded test data.

### Buffers and Push to ERP

- **Buffers**: DDMRP decoupling points, zones and order signals.
- **Push to ERP**: accepted reorder-point changes written back to Acumatica or Odoo, with preview, confirm and audit.

### Settings

Every judgement call is a default plus a setting: service levels, forecast-bias thresholds, how long a release may wait for approval, the S&OP sign-off day, DDMRP factors, the supplier-risk thresholds, whether the plan prefers a lower-risk supplier, the second-source settings (how many leading characters make two item codes similar, and the days to qualify a new supplier), and which receipts set the lead time behind safety stock: every supplier's for the item (the default) or only its main supplier's, the one it is received from most.

**Service levels and their cost** is the card to open when someone asks what a higher service level would cost. For each ABC class it lists the service levels from 90% to 99.5%, with the safety stock each one needs in units and in value, its holding cost a year, the extra cost of each step up, and the fill rate to expect, which is the share of demand served straight from stock. The level in use is marked "(now)". On the demo data, class A at 95% carries about $570,000 a year in holding cost for a fill rate near 97%, and 99% costs about $235,000 a year more for a fill rate of about 99.5%. The levels can be one for every class or one per class. A line on the card says what they mean. By default they are cycle service levels: the chance of not running out in a replenishment cycle. A setting turns them into fill-rate targets, and safety stock and reorder points are then solved to meet the target.

## Talking points

- **Explainable:** every number shows its inputs and arithmetic.
- **Safe by construction:** preview before confirm, admin-only writes, on-hold documents, idempotent releases and an audit of every attempt.
- **Over the ERP you already run:** AIERP reads Acumatica and Odoo, side by side on one screen, and writes back only what a person confirmed.
