# Planner screen: design specification

Approved by the operator on 2026-10-08. The interactive prototype this spec describes is
`planner-screen.dc.html` in this folder, and it is the visual reference for every planner-screen
build. When this document and an issue's acceptance criteria disagree, the acceptance criteria
win, and the disagreement is a bug in this document.

## Why this design

AIERP's first user interface is built against four failures seen elsewhere:

- **Long pages with endless scrolling.** The page itself never scrolls. Lists scroll inside their
  own pane, and a row's detail opens beside the list, not below it.
- **Screens put together piecemeal.** There is one application shell, one navigation, one set of
  components and one set of tokens (`app/css/tokens.css`). A new screen reuses them; it does not
  invent its own buttons, colors or spacing.
- **A dated, unpolished look.** One typeface family, a restrained palette, generous spacing,
  tabular numbers and consistent radii.
- **A confusing workflow.** The Overview says what matters, one click takes the user to the row,
  and the reason and the decision sit side by side.

It serves two readers equally: a **planner** triaging rows every day, and a **manager** who
wants to see the value of the decisions taken.

## The application shell

- A fixed-height layout (`height: 100vh`, never taller) with two columns: a dark sidebar, 232 px
  wide, and the main area.
- **Sidebar**, top to bottom: the AIERP mark and name; the navigation buttons Overview, Work queue
  (with a count of rows still to decide), Decision log and Settings; and at the bottom, the ERP feed status
  ("Synced today 07:30 UTC") and the signed-in user.
- **Main area header**: the page title, a one-line subtitle, and on the right the as-of date and
  service level.
- Below the header, each page fills the remaining height. Only designated panes scroll.
- At widths of 900 px and below, the sidebar becomes a top bar and the list and detail panes
  stack. That is the only time the page scrolls.
- **Narrow screens (900 px and below)**: every multi-column grid becomes one column, including
  the Overview's value tiles and list cards and any figure tiles. Nothing may clip a figure,
  overflow its box, or scroll the page sideways. Each screen's proof checks this at 390×844.

## Overview (the manager's page)

1. **Three value tiles** in a row:
   - accepted money at stake, with the count of accepted decisions;
   - money applied in the ERP, in the accent color, with a progress bar and "N% of accepted
     money has reached the ERP";
   - accepted money still outstanding, in the risk color, with the count of accepted changes
     not yet seen in the ERP.

   The source is `GET /api/decisions/summary` (`total`).
2. **Three list cards**, one each for Reorder points, Excess and dead stock, and Stockout risk.
   Each shows:
   - its title and a one-line description;
   - "N to decide";
   - accepted money, applied money and the dismissed count;
   - an "Open … list" button that goes to that tab of the work queue.
3. **"Needs a decision, largest money first"**: the five undecided rows with the most money
   across the three lists. Each row shows a kind badge, the item and site, a one-line reason, the
   money, and a chevron. Clicking a row opens the work queue on that row.

## Work queue (the planner's page)

- **Tabs** for Reorder points, Excess and dead stock, and Stockout risk, each with a count of rows
  still to decide. **Filter chips** on the same bar: To decide (the default), Decided, and All.
- **Split panes**:
  - **List pane** (about 60%, left), with a sticky column header (#, Item and description, Site,
    the kind's key figure, its money column, Decision) and a body that scrolls on its own. Rows
    are at least 52 px tall. The selected row is tinted and marked by a 3 px accent bar on its
    left.
  - **Detail pane** (440 px, right; it scrolls on its own if needed):
    - the kind label and rank, the item code as the heading, then the description and site;
    - two figure tiles. Reorder points: ERP reorder point and recommended. Excess: on hand and
      excess units. Stockout: on hand plus open supply, and chance of stockout;
    - a **Why** block: a plain-language sentence, then the inputs as a two-column list, then
      the formula in monospace;
    - a **Decision** block with a status badge:
      - Excess and stockout rows first choose an action from three option buttons. Excess:
        Stop replenishing, Reduce, Dispose. Stockout: Expedite, Raise order, Transfer.
      - Then three buttons. The primary one is Accept ("Accept new reorder point" for reorder
        points, "Accept: <action>" otherwise). The others are Dismiss and Snooze.
      - **Dismiss** opens an inline reason field. A reason is required, and an inline alert
        shows if it is empty.
      - **Snooze** offers 7 days, 14 days and the maximum: 90 days for reorder points and
        excess, 30 for stockout.
      - The row's decision history is listed below.
- Deciding updates the row's badge, the tab count and the Overview figures without a page
  reload.

## Decision log

A table inside a card: List, Item and site, Decision (including the reason or action), Money,
and Status. It scrolls inside its own region.

## Settings

Behaviour that teams reasonably want to differ on has a **default** and is **configurable**,
never hard-coded. Every setting is declared once in a registry with its key, type, default and a
one-sentence description; only registered settings can be stored.

- **Two scopes:**
  - **System** settings apply to everyone and only an `admin` may change them (for example
    `decisions.require_action_choice`).
  - **Personal** settings belong to each user and change only what that user sees (for example
    `queue.default_filter`).
- **Defaults are safe:** a missing or unreadable setting falls back to its default, and every
  screen still works if the settings request fails.
- **Audited:** every change and every reset is recorded with who made it and when.
- **The view** (`#/settings`) has two cards, System and Personal. Each row shows the description, a
  control (a switch for on/off, a segmented choice for options), where the value comes from
  ("Default" or "Set by <user> on <date>") and "Reset to default" when it differs. A non-admin
  sees system settings read-only, with "Only an admin can change this."

| Setting | Scope | Default | Effect |
|---|---|---|---|
| `decisions.require_action_choice` | System | on | Accept on an excess or stockout row stays disabled until an action is chosen (operator decision, 2026-10-09). |
| `queue.default_filter` | Personal | To decide | The filter the Work queue opens with. |

## Status badges

| Badge | Meaning |
|---|---|
| Undecided | neutral grey |
| Accepted (· action) | pale accent |
| Applied | solid accent with white text |
| Dismissed | grey |
| Snoozed | pale amber |

Stockout risk uses the darker amber.

## Tokens

The tokens are in `app/css/tokens.css`; use them, never raw values.

- **Typeface**: IBM Plex Sans for the UI and IBM Plex Mono for figures, both from Google Fonts,
  with tabular numbers throughout. Never Inter, Roboto or Arial.
- **Colors**:
  - ground #EEF1F4; surface #FFFFFF; sidebar #16212C;
  - ink #17212B; secondary text #4A5865; borders #DDE3E9 and #E6EAEE;
  - accent #1D4ED8, used for actions, "applied" and the selection;
  - risk #8A3B05 on #FDE8D3.
- **Contrast**: text meets 4.5:1.
- **Radii**: 8 px for controls, 12 px for cards, 999 px for pills.
- **Spacing**: 8 px steps; pages have 24–28 px padding.
- **Touch targets**: at least 44 px for buttons, tabs and options.

## Accessibility

- Real `<button>`, `<a>`, `<input>` and `<label>` elements.
- Tabs use `role="tablist"`/`role="tab"` with `aria-selected`.
- Filter and option buttons use `aria-pressed`.
- The selected row uses `aria-current`.
- Icon-only controls carry an `aria-label`.
- Never rely on hue alone: blue and amber also differ in lightness.

## Data

| Element | Source |
|---|---|
| Reorder-point rows | `GET /api/drift` |
| Excess rows | `GET /api/excess` |
| Stockout rows | `GET /api/stockout` |
| Each row's `decision` | read from those lists, which already return it |
| Value tiles and list cards | `GET /api/decisions/summary` |
| Stockout risk card's proposed remedy block | `GET /api/stockout/proposals` |
| Recording a decision | `POST /api/drift/decisions`, `POST /api/excess/decisions`, `POST /api/stockout/decisions` |
| History | the matching `GET …/decisions?item_code=&site_code=` |
| Settings | `GET /api/settings`; `PUT` and `DELETE /api/settings/{key}` |

The Excess and dead stock detail's proposed action block is read from the excess list's `remedy` field; the excess list is requested with `include_remedy=true`.

Every call carries the signed-in user's bearer token. Only an `admin` user may record a
decision; the UI hides the decision controls for anyone else and shows the row read-only.
