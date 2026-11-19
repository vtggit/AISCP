// AIERP planner screen — the Settings view (#/settings).
//
// #/settings lists every registered setting in two cards — System
// (scope "system") and Personal (scope "user") — one row per setting
// (docs/design/planner-screen.md, "Settings"), and below them a third
// card — "ERP code mappings" ([data-testid="erp-mappings"]) — lists
// the stored cross-ERP code mappings, one row per mapping in the order
// the API returns them: the kind (Item / Site / Vendor), the AIERP
// code, the Odoo code and when the mapping was last updated; with no
// mappings an empty note. Whenever at least one mapping is stored, a
// header row labels the four columns directly above the rows — the
// rows and the header share the same fixed column template, so each
// label sits above its values — and is hidden with the list empty or
// at narrow widths, where the row stacks its values left-aligned and
// each code cell keeps its aria-label ("AIERP code: <code>" / "Odoo
// code: <code>", the values as text) so the codes stay identifiable.
// Whether the viewer is an admin is read from
// GET /api/auth/me roles exactly as queue.js does. For an admin the
// card carries an add form ([data-testid="erp-mapping-form"] — a kind
// select of item/site/vendor, a labelled AIERP-code input and a
// labelled Odoo-code input, each limited to 255 characters, and a Save
// button) that PUTs {kind, target_system: "odoo", source_code,
// target_code} and then shows the stored row (a second save of the same
// kind and AIERP code updates that row instead of adding one), and each
// row a Delete button that DELETEs /api/erp-mappings/{id} and removes
// the row; Save stays disabled while either code is blank or a request
// is out, and a failed save or delete shows the response's detail
// inline and leaves the card usable. For a non-admin the card is
// read-only — no form and no Delete — and reads "Only an admin can
// change this." A failed load shows an inline error with a Retry.
// Below the stored mappings, a "Suggested matches" section
// ([data-testid="erp-suggestions"]) lists the API's suggested item
// mappings for every signed-in user, one row per suggestion
// ([data-testid="erp-suggestion-row", with data-source-code and
// data-target-code]): the AIERP code, the Odoo code, both descriptions
// (an em dash when null) and why it matched — "Same code" or "Same
// description"; a suggestion the API flags as ambiguous also reads
// "More than one possible match". The section is fetched with the card:
// once when the card loads, and again after each successful confirm,
// under its own request-sequence guard — the card's own requests are
// unchanged. With no suggestions it reads "No suggested matches."; a
// failed fetch reads "Suggested matches could not be loaded." and the
// rest of the card works as before. For an admin the section first
// reads "Confirming tells AIERP these are the same item. Draft purchase
// orders use this match too, so check it first." and each row carries a
// "Confirm match" button ([data-testid="erp-suggestion-confirm"], no
// code attributes of its own, so a locator by code finds exactly the
// row) that PUTs {kind: "item", target_system: "odoo", source_code,
// target_code} to /api/erp-mappings — nothing is sent until a person
// presses it, the button stays disabled while the request is out, so a
// double click sends one request — and a success re-loads the stored
// mappings and the suggestions while a failure shows the response's
// detail inline and leaves the row in place. For a non-admin the
// section shows the list, with no buttons and no warning.
// Below it a fourth card — "Purchasing budget"
// ([data-testid="purchase-budgets"]) — rendered under #settings-root,
// after the .settings wrap (a sibling of it, not a child of it, so
// the wrap keeps exactly the System, Personal and mappings cards) —
// lists the stored per-month
// purchasing budgets, one row per stored month in the order the API
// returns them: the month ("Nov 2026"), the amount in whole dollars
// with thousands separators ("$250,001") and when the budget was last
// updated ("Updated 2026-10-17 by jason"); with no budgets an empty
// note. Whenever at least one budget is stored, a header row labels
// the three columns directly above the rows — the rows and the header
// share the same fixed column template, so each label sits above its
// values — and is hidden with the list empty or at narrow widths,
// where the row stacks its values left-aligned. Whether the viewer is
// an admin is read from GET /api/auth/me roles exactly as the
// mappings card does. For an admin the card carries a set form
// ([data-testid="purchase-budget-form"] — a labelled month input and
// a labelled amount input, and a Save button) that PUTs
// {"amount": <number>} to /api/purchase-budgets/{month} and then
// reloads the list; an empty month, an empty amount or a negative
// amount sends nothing and shows an inline message, and a failed save
// or delete shows the response's detail inline and leaves the form's
// values. Each row carries a Delete button that DELETEs
// /api/purchase-budgets/{month} and reloads the list. For a non-admin
// the card is read-only — no form and no Delete — and reads "Only an
// admin can change this." A failed load shows an inline error with a
// Retry. Below the stored rows (or the empty note), the proposals
// section ([data-testid="purchase-budget-proposals"]) shows the
// plan's proposed budgets, read from
// GET /api/purchase-budgets/proposals beside the card's other load
// reads: the note
// ([data-testid="purchase-budget-proposals-note"]) reads "Proposed
// from the plan's committed and planned spend, plus
// <margin times 100, at most one decimal>%" and one row per used
// month in the answer's order
// ([data-testid="purchase-budget-proposal", with data-month]) — the
// month formatted like the stored rows', "Proposed
// $<proposed_budget>" and "Spend $<total_spend>" in the stored
// rows' amount format — reusing the stored rows' column template
// where it fits, so at 390x844 the page does not scroll sideways.
// For an admin each row also carries a Use button
// ([data-testid="purchase-budget-use"]) that sets the set form's
// month and amount to the row's month and proposed budget, sends no
// request and leaves the form showing them; for a non-admin there is
// no Use button. The answer is usable only when it is an object
// whose margin is a finite number and whose months is an array; a
// month entry is used only when its month is a YYYY-MM string and
// its proposed_budget and total_spend are finite numbers — any other
// entry is skipped, and with no entry left the section is left out.
// A failed read (any status or a network error) or an unusable body
// leaves the proposals out and the card renders exactly as before:
// the read runs under its own catch, which answers null, beside the
// roles read and the budgets read, and it is re-read with every
// reload of the card, after a save or a delete too. Below it a fifth
// card —
// "Intermittent and lumpy items"
// ([data-testid="intermittent-impact"]) — rendered under #settings-root,
// after the purchasing budget card, shows the dollar impact of the
// empirical reorder points, fetched separately from
// GET /api/recommendations/intermittent-impact. The summary line
// ([data-testid="intermittent-impact-summary"]) carries the number of
// item-sites and the reorder-point value at unit cost, now and with the
// empirical reorder points — dollars with two decimals and thousands
// separators, the change signed "+" or the minus sign U+2212 before the
// "$" — and the switch line
// ([data-testid="intermittent-impact-switch"]) reads "The switch is on."
// or "The switch is off: these are shown for review only.". The table
// ([data-testid="intermittent-impact-table"]) has one row per item-site,
// the largest absolute dollar change first and a row without a dollar
// change last: Item, Site, Class, ERP reorder point, Empirical reorder
// point and Change ($) — the reorder points as plain whole numbers with
// thousands separators, the change in the signed dollar format, a null
// value shown as "—". When the read carries the rows' stored adoptions
// (a boolean adopted per row), the table gains a last "Adopted" column
// — "Yes" when the row's adopted is true, "No" otherwise — and, for a
// signed-in admin (the same GET /api/auth/me roles check), a final
// action column whose header carries no text: an "Adopt" button
// (class ii-adopt) on a row that is not adopted and a "Withdraw" button
// (class ii-withdraw) on an adopted one. Adopt sends
// PUT /api/empirical-adoptions with the row's item_code and site_code,
// Withdraw sends the DELETE with the pair as query parameters; the
// buttons stay disabled while a request is out, a success re-fetches
// the impact read, and a failure shows "Could not save: <message>" in
// [data-testid="intermittent-impact-msg"] with the table unchanged.
// When the summary counts item-sites without a unit
// cost, a note ([data-testid="intermittent-impact-nocost"]) says so.
// The table scrolls inside its own container, so the page never scrolls
// sideways. A non-200 answer, a network failure or a 200 whose body is
// missing a required field or carries a wrong type renders no card and
// no error message; every other card is untouched.
// Below it a sixth card — “Service levels and their cost”
// ([data-testid="service-curve"]) — rendered under #settings-root,
// after the intermittent-impact card, shows the safety-stock cost
// curve of the service levels, fetched separately from
// GET /api/service-levels/curve. The mode line
// ([data-testid="service-curve-mode"]) reads "One service level for
// every class." with the by-class switch off and "A service level per
// ABC class." with it on, and the rate line
// ([data-testid="service-curve-rate"]) reads "Holding cost at
// <rate>% of unit cost a year." with the rate as a whole percent.
// With the answer's service_target "cycle", the target line
// ([data-testid="service-curve-target"]) under the rate line reads
// "The levels are cycle service levels: the chance of not running out
// in a replenishment cycle."; with "fill_rate" it reads "The levels
// are fill-rate targets: the share of demand served straight from
// stock; safety stock is solved to meet them." With the field missing
// or any other value there is no such line, and the answer is still
// accepted.
// One block per class entry
// ([data-testid="service-curve-class"]), in the answer's order,
// carries the heading "Class <A|B|C> · <item_sites> item-sites · now
// <current level as a percent>" and a table of the class' points —
// one [data-testid="service-curve-point"] row each, in the
// answer's order: Service level, Safety stock (units), Value,
// Holding cost a year, Extra cost a year and Fill rate. The level is
// a percent with at most one decimal (0.995 reads 99.5% and 0.95
// reads 95%), the units whole numbers with thousands separators, the
// money dollars with two decimals, the extra cost the point's
// holding_cost_delta with a leading "+" (an em dash when null) and
// the fill rate the point's expected_fill_rate as a percent with two
// decimals (0.9979 reads 99.79%) — an em dash when the field is
// null, missing or not a number between 0 and 1. The row whose point
// is current carries the class is-current and reads "(now)" after its
// level. A class whose fill_rate_item_sites is a whole number below
// its item-sites gains, under its heading, the note
// ([data-testid="service-curve-fill-note"]) reading "Fill rate from
// <fill_rate_item_sites> of <item_sites> item-sites with an order
// quantity."; a class where they are equal, or where the field is
// missing, has no such line. A class with no item-sites shows the
// note ([data-testid="service-curve-empty"]) instead of a table. The
// card sends no write; a non-200 answer, a network failure or a 200
// whose body is missing a required field or carries a wrong type
// renders the note ([data-testid="service-curve-error"]) and no
// class blocks, and while the read is out the card shows a short
// skeleton. After its last class block, and only while the card
// shows its classes, the suggestion block
// ([data-testid="service-curve-suggest"]): the title
// ([data-testid="service-curve-suggest-title"]), the labelled target
// input ([data-testid="service-curve-suggest-target"], "Target fill
// rate (%)", from 50 to 99.9 in steps of 0.1), the button
// ([data-testid="service-curve-suggest-go"], "Suggest levels") and
// the note ([data-testid="service-curve-suggest-note"]) that
// nothing is changed by it. A click on the button, or an Enter in
// the input, with a value from 50 to 99.9 sends one GET
// /api/service-levels/suggestion?target_fill_rate=<the value
// divided by 100, at most four decimals, no trailing zeros>, and
// the answer reads in
// ([data-testid="service-curve-suggest-result"]) as the current
// line and the suggested line, the suggested line's last words the
// holding-cost delta — below zero "<amount> less", above zero
// "<amount> more", zero "the same", the amount without a sign —
// with a null suggested (and a highest that has a fill rate) the
// out-of-reach line instead of it, and with the current
// combination's fill rate null the single nothing-to-measure line.
// A refused value shows
// ([data-testid="service-curve-suggest-invalid"]) and sends
// nothing; a non-200 answer, a network failure or a body without
// the current and highest combinations and their levels shows
// ([data-testid="service-curve-suggest-error"]) and no result.
// Only the answer to the latest ask is shown; when the card reads
// its curve again the lines are cleared and any answer still out
// is dropped, the typed value kept — and sign-out clears the typed
// value too. No element of the block carries role="alert", and the
// block sends no request other than the GET.
// Below it a seventh card — "Demand basis: history or forecast"
// ([data-testid="demand-basis"]) — rendered under #settings-root,
// after the service-curve card (its next element sibling), shows
// which basis the reorder points stood on and how the stored
// history compared with the forecast against what sold, fetched
// separately from GET
// /api/forecast/accuracy/basis?bucket_index=1. The current line
// ([data-testid="demand-basis-current"]) reads "Reorder points use
// the history." when the screen's own GET /api/settings answer
// (entryByKey) carries the replenishment.demand_basis entry with
// the value "history" and "Reorder points use the forecast." with
// "forecast"; without the entry, or with any other value, the card
// shows no such line. With compared_buckets 0 it shows only the
// note ([data-testid="demand-basis-none"]) "No period with a
// stored history quantity has completed yet." and nothing after
// it; otherwise the summary line
// ([data-testid="demand-basis-summary"]) "One period ahead, over
// <compared_buckets, thousands-separated> periods:
// <history_wape as a percent to one decimal> error on the history,
// <forecast_wape as a percent to one decimal> on the forecast."
// and the verdict ([data-testid="demand-basis-verdict"]) — "The
// forecast was <value_added times 100, absolute, to one decimal>
// points nearer what sold." for a positive value_added, "The
// history was <the same> points nearer what sold." for a negative
// one and "Both were as near as each other." for 0. Below the
// verdict, a proposal line
// ([data-testid="demand-basis-proposal"], the verdict's next
// element sibling) reads "Proposed: switch reorder points to the
// forecast. The exceptions inbox shows the same proposal; an admin
// makes the switch with the demand basis setting above." — with
// "the history" in place of "the forecast" for the mirror case —
// and shows only when the answer's compared_buckets is at least the
// screen's own exceptions.basis_switch_min_periods entry value
// (entryByKey) and either the screen's own
// replenishment.demand_basis entry holds "history" with
// value_added times 100 at least the screen's own
// exceptions.basis_switch_margin entry value, which proposes the
// forecast, or it holds "forecast" with minus value_added times 100
// at least the margin, which proposes the history; without either
// threshold entry or with a non-finite threshold value, with the
// basis entry missing or holding any other value, there is no such
// line. The table
// (table.demand-basis-table, [data-testid="demand-basis-table"])
// inside its own sideways-scroll container
// (div.demand-basis-table-wrap) reads Item, Site, Periods, History
// error, Forecast error, Difference and carries one
// [data-testid="demand-basis-row"] for each of the first 10
// entries of item_sites, in the answer's order: the codes,
// compared_buckets thousands-separated, the two wapes as percents
// to one decimal and value_added times 100 to one decimal with a
// leading "+" above 0 and the minus sign U+2212 below 0, followed
// by " pts" — a null or non-numeric value as an em dash. With more
// than 10 entries the more line
// ([data-testid="demand-basis-more"]) reads "The 10 item-sites
// with the most demand of <item_sites.length,
// thousands-separated>.". A usable answer is an object whose
// compared_buckets is a whole number of at least 0 and whose
// item_sites is an array; with compared_buckets above 0 the
// history_wape, forecast_wape and value_added must be finite
// numbers as well. Anything else, a non-200 answer or a network
// failure renders no card and no message, and the card is absent
// while its read is out — every other card is untouched, and the
// card sends no request other than its GET.
//
// Every call goes through app/js/api.js:
//   * GET /api/settings — every registered setting with its effective
//     value for the caller;
//   * PUT /api/settings/{key} (body {"value": ...}) — a change;
//   * DELETE /api/settings/{key} — a reset to the default.
//   * GET /api/erp-mappings — the stored cross-ERP code mappings;
//   * GET /api/erp-mappings/suggestions — the suggested item mappings,
//     the "Suggested matches" section's own read, for every signed-in
//     user;
//   * PUT /api/erp-mappings (body {kind, target_system, source_code,
//     target_code}) — an admin stores one mapping;
//   * DELETE /api/erp-mappings/{id} — an admin removes one;
//   * GET /api/purchase-budgets — the stored per-month budgets;
//   * GET /api/purchase-budgets/proposals — the proposed budgets of
//     the projected spend horizon (the margin and one entry per
//     month), the purchasing budget card's proposals read, sent
//     beside the card's other load reads (the roles read and the
//     budgets read) and re-sent on every reload of the card, after a
//     save or a delete too; it has its own catch that answers null,
//     so a failed read (any status or a network error) or an
//     unusable body leaves the proposals out and the card renders
//     as before; a usable body is an object whose margin is a finite
//     number and whose months is an array, a month entry used only
//     when its month is a YYYY-MM string and its proposed_budget and
//     total_spend are finite numbers, any other entry skipped, and
//     with no entry left the proposals left out;
//   * PUT /api/purchase-budgets/{month} (body {"amount": <number>}) —
//     an admin stores one month's budget;
//   * DELETE /api/purchase-budgets/{month} — an admin removes one;
//   * GET /api/recommendations/intermittent-impact — the dollar impact
//     of the empirical reorder points (a summary plus one row per
//     item-site), the intermittent-impact card's own read;
//   * GET /api/service-levels/curve — the service-level cost curve
//     per ABC class (the by-class switch, the annual holding rate
//     and the classes' points), the service-curve card's own read;
//   * GET /api/service-levels/suggestion?target_fill_rate=… — the
//     cheapest per-class levels reaching a target fill rate (the
//     current and highest combinations, the suggested one or null
//     and the holding-cost delta), the suggestion block's own read;
//   * GET /api/forecast/accuracy/basis?bucket_index=1 — the realised
//     value of the stored history basis against the forecast (the
//     compared buckets, the two wapes, the value added and one
//     measure per item-site), the demand-basis card's own read;
//   * PUT /api/empirical-adoptions (body {item_code, site_code}) — an
//     admin adopts one item-site's empirical reorder point;
//   * DELETE /api/empirical-adoptions?item_code=…&site_code=… — an
//     admin withdraws one;
//   * GET /api/auth/me — the admin role, read exactly as queue.js does.
//
// Each row shows the setting's description, its control — a switch
// (role="switch", aria-checked) for a boolean, one aria-pressed button
// per allowed value for an enum (To decide, Decided and All for
// queue.default_filter), a number input with a Save button and the
// allowed range ("Between <min> and <max>") for a number —, where the
// value comes from ("Default" or
// "Set by <updated_by_name> on <date>", falling back to <updated_by>
// when the name is null, empty or whitespace only) and a "Reset to
// default" button
// while the value differs from the default. A change or a reset
// updates the row from the response without a page load; a failed
// change shows its `detail` inline under that row and the previous
// value stays. When `editable` is false the control and the reset are
// disabled and the row reads "Only an admin can change this." A
// number save that is blank, not a number or out of range sends no
// request and shows "Enter a number between <min> and <max>." under
// the row; a valid one goes through the same change path (a PUT of
// {"value": <number>}) as a switch or a choice.
//
// Every value from the API is inserted as text (textContent), never as
// HTML. The view scrolls inside the page, never the page.
(function (global) {
  'use strict';

  var LIST_PATH = '/api/settings';
  var MAPPINGS_PATH = '/api/erp-mappings';
  var BUDGETS_PATH = '/api/purchase-budgets';
  var PROPOSALS_PATH = '/api/purchase-budgets/proposals';
  var IMPACT_PATH = '/api/recommendations/intermittent-impact';
  var CURVE_PATH = '/api/service-levels/curve';
  var SUGGESTION_PATH = '/api/service-levels/suggestion';
  var BASIS_PATH = '/api/forecast/accuracy/basis?bucket_index=1';
  var ADOPTIONS_PATH = '/api/empirical-adoptions';
  var ME_PATH = '/api/auth/me';

  // Display labels for the enum values of queue.default_filter; the raw
  // value is the label for any other enum value.
  var ENUM_LABELS = {
    'queue.default_filter': {
      open: 'To decide',
      decided: 'Decided',
      all: 'All',
    },
  };

  var ADMIN_NOTE = 'Only an admin can change this.';
  var RESET_LABEL = 'Reset to default';
  var DEFAULT_SOURCE = 'Default';
  // The inline row message when a change or reset fails and the answer
  // carries no detail of its own.
  var CHANGE_FAILED_MESSAGE = 'The change could not be saved. Try again.';
  var MAPPINGS_TITLE = 'ERP code mappings';
  var MAPPINGS_EMPTY_NOTE = 'No ERP code mappings yet.';
  // The inline card message when a save or delete fails and the answer
  // carries no detail of its own.
  var MAPPING_FAILED_MESSAGE = 'The mapping could not be saved. Try again.';
  // Display labels for the mapping kinds; the raw value is the label
  // for any other kind.
  var MAPPING_KIND_LABELS = { item: 'Item', site: 'Site', vendor: 'Vendor' };
  var SUGGESTIONS_PATH = '/api/erp-mappings/suggestions';
  var SUGGESTIONS_TITLE = 'Suggested matches';
  var SUGGESTIONS_EMPTY_NOTE = 'No suggested matches.';
  // The section message when the suggestions read fails.
  var SUGGESTIONS_LOAD_ERROR = 'Suggested matches could not be loaded.';
  // The admin's warning, above the rows: confirming stores a match and
  // stored matches are what draft purchase orders use.
  var SUGGESTION_CONFIRM_WARNING =
    'Confirming tells AISCP these are the same item. Draft purchase orders use this match too, so check it first.';
  // A suggestion the API flags as ambiguous: more than one side could
  // match the code.
  var SUGGESTION_AMBIGUOUS_NOTE = 'More than one possible match';
  // A null description reads as an em dash.
  var EM_DASH = '\u2014';
  // Display labels for matched_on; isSuggestion admits only the two.
  var SUGGESTION_REASON_LABELS = { code: 'Same code', description: 'Same description' };
  var BUDGETS_TITLE = 'Purchasing budget';
  var BUDGETS_EMPTY_NOTE = 'No purchasing budget set.';
  var IMPACT_TITLE = 'Intermittent and lumpy items';
  var CURVE_TITLE = 'Service levels and their cost';
  // The mode line, by the replenishment.service_level_by_class
  // switch.
  var CURVE_MODE_SINGLE = 'One service level for every class.';
  var CURVE_MODE_BY_CLASS = 'A service level per ABC class.';
  // The target line under the rate line, by the answer's
  // service_target — shown only for the two known values; with the
  // field missing or any other value the card shows no such line.
  var CURVE_TARGET_CYCLE =
    'The levels are cycle service levels: the chance of not running out in a replenishment cycle.';
  var CURVE_TARGET_FILL_RATE =
    'The levels are fill-rate targets: the share of demand served straight from stock; safety stock is solved to meet them.';
  // The note for a class with no item-sites, in place of its table.
  var CURVE_EMPTY_NOTE = 'No item-sites in this class.';
  // The note on a failed or malformed read, in place of the class
  // blocks; it carries no role="alert" — the issue699 proof asserts
  // #settings-root holds none.
  var CURVE_LOAD_ERROR = 'Service levels could not be loaded.';
  // The suggestion block, under the class blocks: its title, its
  // input's label, its button and its note, the classes it writes
  // in its lines and the bounds the target input takes.
  var CURVE_SUGGEST_TITLE = 'Cheapest levels for a fill-rate target';
  var CURVE_SUGGEST_LABEL = 'Target fill rate (%)';
  var CURVE_SUGGEST_GO = 'Suggest levels';
  var CURVE_SUGGEST_NOTE =
    'This changes nothing: the levels are set in the settings on this screen.';
  var CURVE_SUGGEST_CLASSES = ['A', 'B', 'C'];
  var CURVE_SUGGEST_TARGET_MIN = 50;
  var CURVE_SUGGEST_TARGET_MAX = 99.9;
  // The line for a refused target: empty, below 50 or above 99.9.
  var CURVE_SUGGEST_INVALID = 'Enter a target from 50 to 99.9.';
  // The line on a failed or malformed suggestion read; like the
  // card's own load error it carries no role="alert".
  var CURVE_SUGGEST_ERROR = 'The suggestion could not be loaded.';
  // The single line when the current combination's fill rate is
  // null: no item-site has a recommended order quantity, so no fill
  // rate can be worked out.
  var CURVE_SUGGEST_NOPART =
    'A fill rate cannot be worked out: no item-site has a recommended order quantity.';
  // The points' table columns, the cells' order.
  var CURVE_COLUMNS = [
    'Service level',
    'Safety stock (units)',
    'Value',
    'Holding cost a year',
    'Extra cost a year',
    'Fill rate',
  ];
  // The demand-basis card (issue #1394): its title, the settings the
  // current line and the proposal line read from, the proposal
  // texts, the none line for a compared_buckets of 0 and the table's
  // columns.
  var BASIS_TITLE = 'Demand basis: history or forecast';
  var BASIS_SETTING_KEY = 'replenishment.demand_basis';
  var BASIS_MARGIN_KEY = 'exceptions.basis_switch_margin';
  var BASIS_MIN_PERIODS_KEY = 'exceptions.basis_switch_min_periods';
  var BASIS_PROPOSED_FORECAST =
    'Proposed: switch reorder points to the forecast. The exceptions inbox shows the same proposal; an admin makes the switch with the demand basis setting above.';
  var BASIS_PROPOSED_HISTORY =
    'Proposed: switch reorder points to the history. The exceptions inbox shows the same proposal; an admin makes the switch with the demand basis setting above.';
  var BASIS_CURRENT_HISTORY = 'Reorder points use the history.';
  var BASIS_CURRENT_FORECAST = 'Reorder points use the forecast.';
  var BASIS_NONE_LINE = 'No period with a stored history quantity has completed yet.';
  // The table shows the first 10 item-sites of the answer, in the
  // answer's order; the more line counts the rest.
  var BASIS_MAX_ROWS = 10;
  var BASIS_COLUMNS = ['Item', 'Site', 'Periods', 'History error', 'Forecast error', 'Difference'];
  // The inline card message after a failed adopt or withdraw: the
  // request's own message follows the prefix.
  var IMPACT_SAVE_FAILED_PREFIX = 'Could not save: ';
  // The inline card message when a save or delete fails and the answer
  // carries no detail of its own.
  var BUDGET_FAILED_MESSAGE = 'The budget could not be saved. Try again.';
  // The inline form messages for a save that must not go out.
  var BUDGET_MONTH_REQUIRED = 'Choose a month.';
  var BUDGET_AMOUNT_REQUIRED = 'Enter an amount.';
  var BUDGET_AMOUNT_NOT_A_NUMBER = 'The amount must be a number.';
  var BUDGET_AMOUNT_NEGATIVE = "The amount can't be negative.";
  // The short month names of "Nov 2026" (January is index 0).
  var MONTH_ABBREV = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  var state = {
    loading: false,
    error: null,
    entries: [], // the GET /api/settings rows, as served
    updating: {}, // key: true while its PUT or DELETE is in flight
    errors: {}, // key: the message of the last failed change
    mappings: newMappingsState(), // the ERP code mappings card's state
    budgets: newBudgetsState(), // the purchasing budget card's state
    impact: newImpactState(), // the intermittent-impact card's state
    curve: newCurveState(), // the service-curve card's state
    basis: newBasisState(), // the demand-basis card's state
  };
  var loadSeq = 0;
  var mappingsLoadSeq = 0;
  var suggestionsLoadSeq = 0;
  var budgetsLoadSeq = 0;
  var impactLoadSeq = 0;
  var curveLoadSeq = 0;
  var curveSuggestSeq = 0; // the suggestion block's request sequence
  var basisLoadSeq = 0; // the demand-basis card's request sequence
  var rootEl = null;

  // The ERP code mappings card's state: the rows as served, whether the
  // viewer is an admin (the GET /api/auth/me roles check queue.js uses),
  // one in-flight flag shared by the save and the delete, and the add
  // form's draft — kept across re-loads so a view switch does not lose
  // what the planner typed.
  function newMappingsState() {
    return {
      loading: false,
      error: null,
      rows: [],
      isAdmin: false,
      busy: false,
      saveError: null,
      draft: { kind: 'item', source: '', target: '' },
      // The "Suggested matches" section of the same card: its rows as
      // served, its own in-flight flag (a confirm) and the message of
      // the last failed confirm.
      suggestions: newSuggestionsState(),
    };
  }

  // The suggestions section's state: its own load flag, error and rows,
  // one in-flight flag for the confirm and the last failed confirm's
  // message — kept out of the card's busy flag, so a confirm in flight
  // does not touch the add form's or the Delete buttons' state.
  function newSuggestionsState() {
    return {
      loading: false,
      error: null,
      rows: [],
      busy: false,
      confirmError: null,
    };
  }

  // The purchasing budget card's state: the rows as served, the
  // validated proposals answer (null while the read is out, after a
  // failed or unusable read and after a reset), whether the viewer is
  // an admin (the same GET /api/auth/me roles check), one in-flight
  // flag shared by the save and the delete, and the set form's draft
  // — kept across re-loads so a view switch does not lose what the
  // planner typed.
  function newBudgetsState() {
    return {
      loading: false,
      error: null,
      rows: [],
      proposals: null,
      isAdmin: false,
      busy: false,
      saveError: null,
      draft: { month: '', amount: '' },
    };
  }

  // The intermittent-impact card's state: the validated payload as
  // served (the summary plus the item-site rows) or null, whether the
  // viewer is an admin (the same GET /api/auth/me roles check), one
  // in-flight flag shared by the adopt and the withdraw, and the last
  // failed save's message — a failed or invalid read renders no card
  // and no message.
  function newImpactState() {
    return {
      loading: false,
      data: null,
      isAdmin: false,
      busy: false,
      saveError: null,
    };
  }

  // The service-curve card's state: the validated payload as served
  // (the mode, the rate and the classes' points) or null — a failed
  // or invalid read renders the error note and no class blocks.
  // The suggestion block's state: the typed target (kept across
  // re-renders and across curve re-reads; sign-out clears it), the
  // latest answered suggestion payload (null while a read is out,
  // after a failure, a reset or a curve re-read) and the refused-
  // and failed-lines' flags.
  function newCurveSuggestState() {
    return {
      target: '',
      result: null,
      invalid: false,
      error: false,
    };
  }

  function newCurveState() {
    return {
      loading: false,
      data: null,
      suggest: newCurveSuggestState(),
    };
  }

  // The demand-basis card's state: the validated payload as served
  // (the compared buckets, the two wapes, the value added and the
  // item-site measures) or null — a failed or invalid read renders
  // no card and no message, and the card is absent while the read
  // is out.
  function newBasisState() {
    return {
      loading: false,
      data: null,
    };
  }

  /* ------------------------------ helpers ------------------------------ */

  function isEntry(value) {
    return !!value && typeof value === 'object' && typeof value.key === 'string' && value.key !== '';
  }

  function entryByKey(key) {
    for (var i = 0; i < state.entries.length; i++) {
      if (state.entries[i].key === key) {
        return state.entries[i];
      }
    }
    return null;
  }

  // "Default" or "Set by <who> on <date>", where <who> is
  // updated_by_name, falling back to updated_by when the name is
  // absent, null, empty or whitespace only (rows written before names
  // were recorded).
  function sourceText(entry) {
    if (entry.source === 'default' || entry.source === null || entry.source === undefined) {
      return DEFAULT_SOURCE;
    }
    var by = sourceBy(entry);
    var date = formatDate(entry.updated_at);
    if (by === '' && date === '') {
      return DEFAULT_SOURCE;
    }
    var text = 'Set by';
    if (by !== '') {
      text += ' ' + by;
    }
    if (date !== '') {
      text += ' on ' + date;
    }
    return text;
  }

  // The person the source line names: updated_by_name, falling back
  // to updated_by when the name is absent, null, empty or whitespace
  // only (rows written before names were recorded).
  function sourceBy(entry) {
    var name =
      entry.updated_by_name === null || entry.updated_by_name === undefined
        ? ''
        : String(entry.updated_by_name);
    if (name.trim() !== '') {
      return name;
    }
    return entry.updated_by === null || entry.updated_by === undefined ? '' : String(entry.updated_by);
  }

  // "2026-10-09" from an API timestamp; the raw text when it does not
  // carry a parseable date; '' when absent.
  function formatDate(value) {
    var text = value === null || value === undefined ? '' : String(value);
    if (text === '') {
      return '';
    }
    var match = /^(\d{4}-\d{2}-\d{2})/.exec(text);
    return match ? match[1] : text;
  }

  function enumLabel(key, value) {
    var labels = ENUM_LABELS[key];
    var text = String(value);
    if (labels && Object.prototype.hasOwnProperty.call(labels, text)) {
      return labels[text];
    }
    return text;
  }

  // Every value from the API reaches the page through textContent — as
  // text, never as HTML.
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = text;
    }
    return node;
  }

  function text(value) {
    return value === null || value === undefined ? '' : String(value);
  }

  // A finite number: the bounds a number row validates against (and
  // shows) and the value it saves must both be finite to be usable.
  function finiteNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }

  /* ------------------------------- render ------------------------------ */

  function render() {
    if (!rootEl) {
      return;
    }
    rootEl.textContent = '';
    // A failed settings-list read keeps its own error card, as before.
    if (state.error) {
      rootEl.appendChild(renderError());
      return;
    }
    var wrap = el('div', 'settings');
    if (state.loading) {
      renderLoading(wrap);
    } else {
      var cards = cardsFor(state.entries);
      for (var i = 0; i < cards.length; i++) {
        wrap.appendChild(renderCard(cards[i]));
      }
    }
    // The third card — ERP code mappings — after System and Personal,
    // driven by its own load state.
    wrap.appendChild(renderMappingsCard());
    rootEl.appendChild(wrap);
    // The fourth card — Purchasing budget — after the mappings card,
    // driven by its own load state. It sits under #settings-root,
    // after the .settings wrap (a sibling of it, not a child of it),
    // so the wrap's child list stays the System, Personal and
    // mappings cards — the issue475 proof reads it and must keep
    // passing unchanged.
    rootEl.appendChild(renderBudgetsCard());
    // The fifth card — Intermittent and lumpy items — after the budget
    // card, driven by its own load state. A failed or invalid read
    // renders no card at all, so the append is conditional.
    var impactCard = renderImpactCard();
    if (impactCard) {
      rootEl.appendChild(impactCard);
    }
    // The sixth card — Service levels and their cost — after the
    // intermittent-impact card, driven by its own load state: a short
    // skeleton while its read is out, the error note on a failed or
    // malformed read, and the per-class blocks otherwise.
    rootEl.appendChild(renderCurveCard());
    // The seventh card — Demand basis: history or forecast — after
    // the service-curve card, driven by its own load state: absent
    // while its read is out and on a failed or malformed read, so
    // the append is conditional.
    var basisCard = renderBasisCard();
    if (basisCard) {
      rootEl.appendChild(basisCard);
    }
  }

  function cardsFor(entries) {
    var system = [];
    var user = [];
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i];
      if (entry.scope === 'system') {
        system.push(entry);
      } else if (entry.scope === 'user') {
        user.push(entry);
      }
    }
    return [
      { title: 'System', scope: 'system', entries: system },
      { title: 'Personal', scope: 'user', entries: user },
    ];
  }

  function renderCard(card) {
    var article = el('article', 'card settings-card');
    article.setAttribute('data-scope', card.scope);
    article.setAttribute('aria-label', card.title);
    article.appendChild(el('h2', 'settings-card-title', card.title));
    for (var i = 0; i < card.entries.length; i++) {
      article.appendChild(renderRow(card.entries[i]));
    }
    return article;
  }

  function renderRow(entry) {
    var disabled = !!state.updating[entry.key] || entry.editable !== true;

    var row = el('div', 'settings-row');
    row.setAttribute('data-key', entry.key);

    var head = el('div', 'settings-row-head');
    head.appendChild(el('p', 'settings-desc', text(entry.description)));
    head.appendChild(el('span', 'settings-source', sourceText(entry)));
    row.appendChild(head);

    var controls = el('div', 'settings-row-controls');
    if (entry.type === 'boolean') {
      controls.appendChild(renderSwitch(entry, disabled));
    } else if (entry.type === 'enum') {
      controls.appendChild(renderChoices(entry, disabled));
    } else if (entry.type === 'number') {
      controls.appendChild(renderNumberForm(entry, disabled));
    }
    if (entry.source !== 'default' && entry.source !== null && entry.source !== undefined) {
      var resetButton = el('button', 'btn btn-secondary settings-reset', RESET_LABEL);
      resetButton.type = 'button';
      resetButton.setAttribute('data-key', entry.key);
      resetButton.disabled = disabled;
      controls.appendChild(resetButton);
    }
    row.appendChild(controls);

    if (entry.editable !== true) {
      row.appendChild(el('p', 'settings-row-note', ADMIN_NOTE));
    }

    var errorText = state.errors[entry.key];
    if (errorText) {
      var error = el('p', 'settings-row-error', errorText);
      error.setAttribute('role', 'alert');
      row.appendChild(error);
    }
    return row;
  }

  function renderSwitch(entry, disabled) {
    var on = entry.value === true;
    var switchButton = el('button', 'settings-switch');
    switchButton.type = 'button';
    switchButton.setAttribute('role', 'switch');
    switchButton.setAttribute('aria-checked', on ? 'true' : 'false');
    switchButton.setAttribute('data-key', entry.key);
    switchButton.setAttribute('aria-label', text(entry.description) || entry.key);
    switchButton.appendChild(el('span', 'settings-switch-knob'));
    switchButton.disabled = disabled;
    return switchButton;
  }

  function renderChoices(entry, disabled) {
    var group = el('div', 'settings-choices');
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', text(entry.description) || entry.key);
    var allowed = Array.isArray(entry.allowed) ? entry.allowed : [];
    for (var i = 0; i < allowed.length; i++) {
      var value = allowed[i];
      var button = el('button', 'settings-choice', enumLabel(entry.key, value));
      button.type = 'button';
      button.setAttribute('aria-pressed', entry.value === value ? 'true' : 'false');
      button.setAttribute('data-key', entry.key);
      button.setAttribute('data-value', text(value));
      button.disabled = disabled;
      group.appendChild(button);
    }
    return group;
  }

  // The number control: a form holding a number input (step "any",
  // the entry's min and max as attributes, the current value, the
  // description as aria-label), a Save button and the allowed range.
  // The form carries no per-row listener of its own: the delegated
  // submit listener (boot) validates the value and sends it through
  // changeValue, so the in-flight flag, the row error on failure and
  // the row update from the response work exactly as for a switch or
  // a choice.
  function renderNumberForm(entry, disabled) {
    var form = el('form', 'settings-number');
    form.setAttribute('data-key', entry.key);
    form.noValidate = true;

    var input = el('input', 'settings-number-input');
    input.type = 'number';
    input.step = 'any';
    if (finiteNumber(entry.min)) {
      input.setAttribute('min', text(entry.min));
    }
    if (finiteNumber(entry.max)) {
      input.setAttribute('max', text(entry.max));
    }
    input.value = text(entry.value);
    input.setAttribute('aria-label', text(entry.description));
    input.disabled = disabled;
    form.appendChild(input);

    var save = el('button', 'btn btn-secondary settings-number-save', 'Save');
    save.type = 'submit';
    save.disabled = disabled;
    form.appendChild(save);

    if (finiteNumber(entry.min) && finiteNumber(entry.max)) {
      form.appendChild(
        el(
          'span',
          'settings-number-range',
          'Between ' + text(entry.min) + ' and ' + text(entry.max)
        )
      );
    }
    return form;
  }

  function renderLoading(wrap) {
    var titles = ['System', 'Personal'];
    for (var i = 0; i < titles.length; i++) {
      var card = el('article', 'card settings-card');
      card.setAttribute('aria-label', titles[i]);
      card.appendChild(el('h2', 'settings-card-title', titles[i]));
      for (var j = 0; j < 2; j++) {
        var row = el('div', 'settings-row');
        row.appendChild(el('span', 'settings-row-skel'));
        card.appendChild(row);
      }
      wrap.appendChild(card);
    }
  }

  function renderError() {
    var wrap = el('div', 'settings');
    var card = el('article', 'card settings-card');
    card.appendChild(el('h2', 'settings-card-title', 'Settings'));
    var box = el('div', 'settings-load-error');
    box.setAttribute('role', 'alert');
    box.appendChild(el('p', 'settings-load-error-msg', state.error));
    var retry = el('button', 'btn btn-secondary settings-retry', 'Retry');
    retry.type = 'button';
    box.appendChild(retry);
    card.appendChild(box);
    wrap.appendChild(card);
    return wrap;
  }

  /* --------------------- erp code mappings ---------------------------- */

  // A code as served: the API types it as a string, but a purely
  // numeric code (a site's "154") can arrive as a JSON number — both
  // shapes are codes, so both stay. An empty string or a NaN is not a
  // code.
  function isCode(value) {
    if (typeof value === 'string') {
      return value !== '';
    }
    return typeof value === 'number' && isFinite(value);
  }

  // A well-formed mapping row as served: an id, a non-blank kind, and
  // the two codes as strings or numbers. Anything else is dropped, as
  // with the settings entries.
  function isMapping(value) {
    return (
      !!value &&
      typeof value === 'object' &&
      (typeof value.id === 'number' || (typeof value.id === 'string' && value.id !== '')) &&
      typeof value.kind === 'string' &&
      value.kind !== '' &&
      isCode(value.source_code) &&
      isCode(value.target_code)
    );
  }

  // The row with its codes as strings: the upsert's and the delete's
  // matches compare codes as strings, so a numeric code (a site's 154)
  // normalizes once, at the two entry points — the load and the saved
  // row the PUT response carries. A served "__proto__" key is dropped:
  // row[key] = ... with that key would run the prototype setter and
  // re-point the row's prototype at the served object, whose fields
  // the row would then inherit.
  function normalizeMapping(value) {
    var row = {};
    for (var key in value) {
      if (key !== '__proto__' && Object.prototype.hasOwnProperty.call(value, key)) {
        row[key] = value[key];
      }
    }
    row.source_code = String(value.source_code);
    row.target_code = String(value.target_code);
    return row;
  }

  function mappingKindLabel(kind) {
    var key = String(kind).toLowerCase();
    return Object.prototype.hasOwnProperty.call(MAPPING_KIND_LABELS, key)
      ? MAPPING_KIND_LABELS[key]
      : String(kind);
  }

  // A well-formed suggestion as served: the two codes, one of the two
  // matched_on values, a boolean ambiguous, and a description per side
  // (a string or null). Anything else is dropped, as with the mappings.
  function isSuggestion(value) {
    return (
      !!value &&
      typeof value === 'object' &&
      isCode(value.source_code) &&
      isCode(value.target_code) &&
      (value.matched_on === 'code' || value.matched_on === 'description') &&
      typeof value.ambiguous === 'boolean' &&
      (value.source_description === null || typeof value.source_description === 'string') &&
      (value.target_description === null || typeof value.target_description === 'string')
    );
  }

  // The row with its codes as strings: the data attributes and the
  // confirm's body carry codes as strings, so a numeric code
  // normalizes once, at the load.
  function normalizeSuggestion(value) {
    return {
      source_code: String(value.source_code),
      target_code: String(value.target_code),
      matched_on: value.matched_on,
      source_description: value.source_description,
      target_description: value.target_description,
      ambiguous: value.ambiguous,
    };
  }

  function suggestionReasonLabel(matchedOn) {
    return Object.prototype.hasOwnProperty.call(SUGGESTION_REASON_LABELS, matchedOn)
      ? SUGGESTION_REASON_LABELS[matchedOn]
      : text(matchedOn);
  }

  function mappingSaveDisabled() {
    var mappings = state.mappings;
    return (
      mappings.busy ||
      mappings.draft.source.trim() === '' ||
      mappings.draft.target.trim() === ''
    );
  }

  // Toggle the Save button in place — a re-render would rebuild the
  // inputs and steal focus mid-typing.
  function syncMappingSave() {
    if (!rootEl) {
      return;
    }
    var save = rootEl.querySelector('#erp-mapping-save');
    if (save) {
      save.disabled = mappingSaveDisabled();
    }
  }

  function renderMappingsCard() {
    var mappings = state.mappings;
    var card = el('article', 'card settings-card erp-mappings-card');
    card.setAttribute('data-testid', 'erp-mappings');
    card.setAttribute('aria-label', MAPPINGS_TITLE);
    card.appendChild(el('h2', 'settings-card-title', MAPPINGS_TITLE));
    if (mappings.loading) {
      var row = el('div', 'settings-row');
      row.appendChild(el('span', 'settings-row-skel'));
      card.appendChild(row);
      return card;
    }
    if (mappings.error) {
      // A failed read: the same inline error shape as the settings
      // list, with its own Retry.
      var box = el('div', 'settings-load-error');
      box.setAttribute('role', 'alert');
      box.appendChild(el('p', 'settings-load-error-msg', mappings.error));
      var retry = el('button', 'btn btn-secondary erp-mappings-retry', 'Retry');
      retry.type = 'button';
      box.appendChild(retry);
      card.appendChild(box);
      return card;
    }
    if (mappings.isAdmin) {
      card.appendChild(renderMappingsForm());
      if (mappings.saveError) {
        var error = el('p', 'settings-row-error', mappings.saveError);
        error.setAttribute('role', 'alert');
        card.appendChild(error);
      }
    }
    // The header row labels the columns directly above the rows, in
    // the rows' own column template; it is not shown while the list is
    // empty (there is nothing to label).
    if (mappings.rows.length > 0) {
      card.appendChild(renderMappingsHead());
    }
    for (var i = 0; i < mappings.rows.length; i++) {
      card.appendChild(renderMappingRow(mappings.rows[i], mappings.isAdmin));
    }
    if (mappings.rows.length === 0) {
      card.appendChild(el('p', 'settings-row-note erp-mappings-empty', MAPPINGS_EMPTY_NOTE));
    }
    // The "Suggested matches" section: below the stored mappings, for
    // every signed-in user, driven by its own load state.
    card.appendChild(renderSuggestionsSection(mappings.isAdmin));
    if (!mappings.isAdmin) {
      card.appendChild(el('p', 'settings-row-note', ADMIN_NOTE));
    }
    return card;
  }

  function renderMappingsForm() {
    var draft = state.mappings.draft;

    var form = el('form', 'erp-mappings-form settings-row');
    form.setAttribute('data-testid', 'erp-mapping-form');
    form.noValidate = true;

    var fields = el('div', 'erp-mappings-fields');

    var kindField = el('div', 'erp-mapping-field');
    var kindLabel = el('label', 'erp-mapping-label', 'Kind');
    kindLabel.setAttribute('for', 'erp-mapping-kind');
    var kindSelect = el('select', 'erp-mapping-select');
    kindSelect.id = 'erp-mapping-kind';
    kindSelect.setAttribute('data-testid', 'erp-mapping-kind');
    var kinds = [
      { value: 'item', label: 'Item' },
      { value: 'site', label: 'Site' },
      { value: 'vendor', label: 'Vendor' },
    ];
    for (var i = 0; i < kinds.length; i++) {
      var option = el('option', null, kinds[i].label);
      option.value = kinds[i].value;
      kindSelect.appendChild(option);
    }
    kindSelect.value =
      kinds.some(function (kind) {
        return kind.value === draft.kind;
      }) && draft.kind
        ? draft.kind
        : 'item';
    kindField.appendChild(kindLabel);
    kindField.appendChild(kindSelect);
    fields.appendChild(kindField);

    var sourceField = el('div', 'erp-mapping-field');
    var sourceLabel = el('label', 'erp-mapping-label', 'AISCP code');
    sourceLabel.setAttribute('for', 'erp-mapping-source');
    var sourceInput = el('input', 'erp-mapping-input');
    sourceInput.id = 'erp-mapping-source';
    sourceInput.setAttribute('type', 'text');
    sourceInput.setAttribute('maxlength', '255');
    sourceInput.setAttribute('autocomplete', 'off');
    sourceInput.setAttribute('spellcheck', 'false');
    sourceInput.setAttribute('data-testid', 'erp-mapping-source');
    sourceInput.value = draft.source;
    sourceField.appendChild(sourceLabel);
    sourceField.appendChild(sourceInput);
    fields.appendChild(sourceField);

    var targetField = el('div', 'erp-mapping-field');
    var targetLabel = el('label', 'erp-mapping-label', 'Odoo code');
    targetLabel.setAttribute('for', 'erp-mapping-target');
    var targetInput = el('input', 'erp-mapping-input');
    targetInput.id = 'erp-mapping-target';
    targetInput.setAttribute('type', 'text');
    targetInput.setAttribute('maxlength', '255');
    targetInput.setAttribute('autocomplete', 'off');
    targetInput.setAttribute('spellcheck', 'false');
    targetInput.setAttribute('data-testid', 'erp-mapping-target');
    targetInput.value = draft.target;
    targetField.appendChild(targetLabel);
    targetField.appendChild(targetInput);
    fields.appendChild(targetField);

    form.appendChild(fields);

    var save = el('button', 'btn btn-primary erp-mapping-save', 'Save');
    save.id = 'erp-mapping-save';
    save.type = 'button';
    save.setAttribute('data-testid', 'erp-mapping-save');
    save.disabled = mappingSaveDisabled();
    form.appendChild(save);
    return form;
  }

  // The header row that labels the row's columns: it reuses the
  // row's cell classes — and hence the CSS column template — so each
  // label lines up with its values, and it carries no aria-labels of
  // its own (the row's code cells do).
  function renderMappingsHead() {
    var head = el('div', 'erp-mapping-head');
    head.setAttribute('data-testid', 'erp-mapping-head');
    head.appendChild(el('span', 'erp-mapping-kind', 'Kind'));
    head.appendChild(el('span', 'erp-mapping-code', 'AISCP code'));
    head.appendChild(el('span', 'erp-mapping-code', 'Odoo code'));
    head.appendChild(el('span', 'erp-mapping-updated', 'Updated'));
    return head;
  }

  function renderMappingRow(mapping, isAdmin) {
    var mappings = state.mappings;

    var row = el('div', 'settings-row erp-mapping-row');
    row.setAttribute('data-testid', 'erp-mapping-row');
    row.setAttribute('data-mapping-id', String(mapping.id));

    // One cell per column of the shared template; the code cells are
    // labelled for assistive tech — the value inserted as text, never
    // as HTML — so a code stays identifiable when the header is
    // hidden (narrow).
    row.appendChild(el('span', 'erp-mapping-kind', mappingKindLabel(mapping.kind)));

    var sourceCell = el('span', 'erp-mapping-code', text(mapping.source_code));
    sourceCell.setAttribute('aria-label', 'AISCP code: ' + text(mapping.source_code));
    row.appendChild(sourceCell);

    var targetCell = el('span', 'erp-mapping-code erp-mapping-target-code', text(mapping.target_code));
    targetCell.setAttribute('aria-label', 'Odoo code: ' + text(mapping.target_code));
    row.appendChild(targetCell);

    var updated = formatDate(mapping.updated_at);
    row.appendChild(el('span', 'erp-mapping-updated', updated !== '' ? 'Updated ' + updated : 'Updated'));

    if (isAdmin) {
      var deleteButton = el('button', 'btn btn-secondary erp-mapping-delete', 'Delete');
      deleteButton.type = 'button';
      deleteButton.setAttribute('data-id', String(mapping.id));
      deleteButton.disabled = mappings.busy;
      row.appendChild(deleteButton);
    }
    return row;
  }

  // The "Suggested matches" section: the heading, the admin's warning
  // above the rows, one row per suggestion (an empty note with none, a
  // load error on a failed fetch) and, after a failed confirm, the
  // response's detail inline — the rows stay.
  function renderSuggestionsSection(isAdmin) {
    var suggestions = state.mappings.suggestions;

    var section = el('section', 'erp-suggestions');
    section.setAttribute('data-testid', 'erp-suggestions');
    section.appendChild(el('h3', 'erp-suggestions-title', SUGGESTIONS_TITLE));
    if (isAdmin) {
      var warning = el('p', 'erp-suggestions-warning', SUGGESTION_CONFIRM_WARNING);
      warning.setAttribute('role', 'note');
      section.appendChild(warning);
    }
    if (suggestions.loading) {
      var row = el('div', 'settings-row');
      row.appendChild(el('span', 'settings-row-skel'));
      section.appendChild(row);
      return section;
    }
    if (suggestions.error) {
      // The section's own error shape: no role of its own, so a failed
      // suggestions read never reads as the card's (or another card's)
      // error state to a viewer of the rest of the page.
      var error = el('p', 'erp-suggestions-error', suggestions.error);
      section.appendChild(error);
      return section;
    }
    for (var i = 0; i < suggestions.rows.length; i++) {
      section.appendChild(renderSuggestionRow(suggestions.rows[i], isAdmin));
    }
    if (suggestions.rows.length === 0) {
      section.appendChild(el('p', 'settings-row-note erp-suggestions-empty', SUGGESTIONS_EMPTY_NOTE));
    }
    if (suggestions.confirmError) {
      var confirmError = el(
        'p',
        'settings-row-error erp-suggestions-confirm-error',
        suggestions.confirmError
      );
      confirmError.setAttribute('role', 'alert');
      section.appendChild(confirmError);
    }
    return section;
  }

  function renderSuggestionRow(suggestion, isAdmin) {
    var suggestions = state.mappings.suggestions;

    var row = el('div', 'settings-row erp-suggestion-row');
    row.setAttribute('data-testid', 'erp-suggestion-row');
    row.setAttribute('data-source-code', suggestion.source_code);
    row.setAttribute('data-target-code', suggestion.target_code);

    // Every value as text: the AIERP code, the Odoo code, both
    // descriptions — an em dash when the API answers null — and why it
    // matched; the ambiguous flag adds its note.
    row.appendChild(el('span', 'erp-suggestion-code', suggestion.source_code));
    row.appendChild(el('span', 'erp-suggestion-code', suggestion.target_code));
    row.appendChild(
      el(
        'span',
        'erp-suggestion-desc',
        suggestion.source_description === null ? EM_DASH : suggestion.source_description
      )
    );
    row.appendChild(
      el(
        'span',
        'erp-suggestion-desc',
        suggestion.target_description === null ? EM_DASH : suggestion.target_description
      )
    );
    row.appendChild(el('span', 'erp-suggestion-reason', suggestionReasonLabel(suggestion.matched_on)));
    if (suggestion.ambiguous) {
      row.appendChild(el('span', 'erp-suggestion-ambiguous', SUGGESTION_AMBIGUOUS_NOTE));
    }
    if (isAdmin) {
      // The Confirm button carries no code attributes of its own, so a
      // locator by code finds exactly one element: the row.
      var confirm = el('button', 'btn btn-secondary erp-suggestion-confirm', 'Confirm match');
      confirm.type = 'button';
      confirm.setAttribute('data-testid', 'erp-suggestion-confirm');
      confirm.disabled = suggestions.busy;
      row.appendChild(confirm);
    }
    return row;
  }

  function startMappingsLoad() {
    var mappings = state.mappings;
    if (mappings.loading) {
      return;
    }
    mappings.loading = true;
    mappings.error = null;
    mappings.rows = [];
    mappings.saveError = null;
    render();
    var seq = ++mappingsLoadSeq;
    // The suggestions load with the card — once per card load (the
    // initial load and each Retry), and again after each successful
    // confirm, which re-loads the card — under their own sequence
    // guard. The card's own requests are unchanged.
    startSuggestionsLoad();
    // The same /api/auth/me roles check queue.js uses: only a signed-in
    // user whose roles include "admin" gets the add form and the
    // Delete; a failed read leaves the card read-only, as the queue
    // does.
    var meCall = global.Api.get(ME_PATH).then(
      function (me) {
        return !!(
          me &&
          me.authenticated &&
          me.user &&
          Array.isArray(me.user.roles) &&
          me.user.roles.indexOf('admin') !== -1
        );
      },
      function () {
        return false;
      }
    );
    Promise.all([meCall, global.Api.get(MAPPINGS_PATH)]).then(
      function (results) {
        if (seq !== mappingsLoadSeq) {
          return;
        }
        var rows = Array.isArray(results[1]) ? results[1] : [];
        var valid = [];
        for (var i = 0; i < rows.length; i++) {
          if (isMapping(rows[i])) {
            valid.push(normalizeMapping(rows[i]));
          }
        }
        mappings.isAdmin = results[0];
        mappings.rows = valid;
        mappings.loading = false;
        render();
      },
      function (error) {
        if (seq !== mappingsLoadSeq) {
          return;
        }
        mappings.loading = false;
        mappings.error =
          error && error.message
            ? String(error.message)
            : 'The mappings could not be loaded. Please try again.';
        render();
      }
    );
  }

  // The section's own read, fetched with the card: a failed fetch
  // leaves the section's error in place and the rest of the card
  // working as before; a late answer (a newer load superseded it) is
  // discarded by the sequence guard.
  function startSuggestionsLoad() {
    var suggestions = state.mappings.suggestions;
    suggestions.loading = true;
    suggestions.error = null;
    suggestions.rows = [];
    suggestions.confirmError = null;
    render();
    var seq = ++suggestionsLoadSeq;
    global.Api.get(SUGGESTIONS_PATH).then(
      function (answer) {
        if (seq !== suggestionsLoadSeq) {
          return;
        }
        var rows = [];
        if (Array.isArray(answer)) {
          for (var i = 0; i < answer.length; i++) {
            if (isSuggestion(answer[i])) {
              rows.push(normalizeSuggestion(answer[i]));
            }
          }
        }
        suggestions.loading = false;
        suggestions.rows = rows;
        render();
      },
      function () {
        if (seq !== suggestionsLoadSeq) {
          return;
        }
        suggestions.loading = false;
        suggestions.error = SUGGESTIONS_LOAD_ERROR;
        render();
      }
    );
  }

  // The stored row the save response carries: a row with the same kind
  // and AIERP code is updated in place (a second save of the same kind
  // and code updates that row instead of adding one), anything else is
  // appended.
  function upsertMappingRow(stored) {
    var rows = state.mappings.rows;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].kind === stored.kind && rows[i].source_code === stored.source_code) {
        rows[i] = stored;
        return;
      }
    }
    rows.push(stored);
  }

  // Save PUTs {kind, target_system, source_code, target_code}; a 200
  // whose body is the stored row shows it (and clears the codes), a
  // failed save or a 200 without the row shows the detail inline and
  // leaves the card usable — the codes stay, the Save re-enables.
  function saveMapping() {
    var mappings = state.mappings;
    if (
      !mappings.isAdmin ||
      mappings.busy ||
      mappings.loading ||
      mappings.error !== null ||
      mappingSaveDisabled()
    ) {
      return;
    }
    var kind = mappings.draft.kind;
    var sourceCode = mappings.draft.source.trim();
    var targetCode = mappings.draft.target.trim();
    mappings.busy = true;
    mappings.saveError = null;
    render();
    global.Api.request(MAPPINGS_PATH, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        kind: kind,
        target_system: 'odoo',
        source_code: sourceCode,
        target_code: targetCode,
      }),
    }).then(
      function (stored) {
        mappings.busy = false;
        if (isMapping(stored)) {
          upsertMappingRow(normalizeMapping(stored));
          mappings.draft.source = '';
          mappings.draft.target = '';
        } else {
          mappings.saveError = MAPPING_FAILED_MESSAGE;
        }
        render();
      },
      function (error) {
        mappings.busy = false;
        mappings.saveError = mappingErrorMessage(error);
        render();
      }
    );
  }

  function deleteMapping(id) {
    var mappings = state.mappings;
    if (mappings.busy || mappings.loading || mappings.error !== null) {
      return;
    }
    mappings.busy = true;
    mappings.saveError = null;
    render();
    global.Api.request(MAPPINGS_PATH + '/' + encodeURIComponent(String(id)), {
      method: 'DELETE',
    }).then(
      function () {
        mappings.busy = false;
        mappings.rows = mappings.rows.filter(function (row) {
          return String(row.id) !== String(id);
        });
        render();
      },
      function (error) {
        mappings.busy = false;
        mappings.saveError = mappingErrorMessage(error);
        render();
      }
    );
  }

  // The inline card message after a failed save or delete: the
  // response's detail when an HTTP error answer carried one — api.js
  // puts a string detail into error.message verbatim and any other
  // detail as its JSON — the fixed message otherwise.
  function mappingErrorMessage(error) {
    var normalized = normalizeRequestError(error);
    if (
      normalized.status !== null &&
      normalized.message !== '' &&
      !/^Request failed \(\d+\)\.$/.test(normalized.message)
    ) {
      return normalized.message;
    }
    return MAPPING_FAILED_MESSAGE;
  }

  // Confirming a suggestion stores it as a mapping the same way the add
  // form does: PUT /api/erp-mappings with {kind: "item", target_system:
  // "odoo", source_code, target_code} — these are item suggestions.
  // Nothing is sent until a person presses the button; the button stays
  // disabled while the request is out, so a double click sends one
  // request. A success re-loads the card — the stored mappings and the
  // suggestions — and a failure shows the response's detail inline and
  // leaves the row in place.
  function confirmSuggestion(sourceCode, targetCode) {
    var mappings = state.mappings;
    var suggestions = mappings.suggestions;
    if (
      !mappings.isAdmin ||
      mappings.busy ||
      mappings.loading ||
      mappings.error !== null ||
      suggestions.busy
    ) {
      return;
    }
    suggestions.busy = true;
    suggestions.confirmError = null;
    render();
    global.Api.request(MAPPINGS_PATH, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        kind: 'item',
        target_system: 'odoo',
        source_code: sourceCode,
        target_code: targetCode,
      }),
    }).then(
      function () {
        suggestions.busy = false;
        // Both lists reload: the stored mappings and the suggestions.
        startMappingsLoad();
      },
      function (error) {
        suggestions.busy = false;
        suggestions.confirmError = mappingErrorMessage(error);
        render();
      }
    );
  }


  /* --------------------- purchasing budgets -------------------------- */

  // A well-formed budget row as served: a non-blank month and a
  // finite amount. Anything else is dropped, as with the mappings.
  function isBudget(value) {
    return (
      !!value &&
      typeof value === 'object' &&
      typeof value.month === 'string' &&
      value.month !== '' &&
      typeof value.amount === 'number' &&
      isFinite(value.amount)
    );
  }

  // "Nov 2026" from a stored "YYYY-MM" month; the raw text when it is
  // not a parseable month.
  function formatBudgetMonth(month) {
    var text = String(month);
    var match = /^(\d{4})-(\d{2})$/.exec(text);
    if (!match) {
      return text;
    }
    var monthNumber = parseInt(match[2], 10);
    if (monthNumber < 1 || monthNumber > 12) {
      return text;
    }
    return MONTH_ABBREV[monthNumber - 1] + ' ' + match[1];
  }

  // "$250,001" — the amount in whole dollars with thousands
  // separators (250000.5 rounds to 250001).
  function formatBudgetAmount(amount) {
    return '$' + Math.round(amount).toLocaleString('en-US');
  }

  // "Updated 2026-10-17 by jason" — the date and the person fall away
  // independently when the API does not carry them.
  function budgetUpdatedText(row) {
    var date = formatDate(row.updated_at);
    var by = row.updated_by === null || row.updated_by === undefined ? '' : String(row.updated_by);
    if (date !== '' && by.trim() !== '') {
      return 'Updated ' + date + ' by ' + by;
    }
    if (date !== '') {
      return 'Updated ' + date;
    }
    if (by.trim() !== '') {
      return 'Updated by ' + by;
    }
    return 'Updated';
  }

  // One month entry of the proposals answer, as used: the month a
  // YYYY-MM string (the API's own month pattern) and the proposed
  // budget and the total spend finite numbers. Any other entry is
  // skipped by the caller.
  function isProposalMonth(value) {
    return (
      !!value &&
      typeof value === 'object' &&
      typeof value.month === 'string' &&
      /^\d{4}-(0[1-9]|1[0-2])$/.test(value.month) &&
      typeof value.proposed_budget === 'number' &&
      isFinite(value.proposed_budget) &&
      typeof value.total_spend === 'number' &&
      isFinite(value.total_spend)
    );
  }

  // A usable proposals answer: an object whose margin is a finite
  // number and whose months is an array, with at least one usable
  // month entry. Anything less answers null — the card renders as
  // before, with no proposals section.
  function usableProposals(body) {
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      return null;
    }
    if (typeof body.margin !== 'number' || !isFinite(body.margin)) {
      return null;
    }
    if (!Array.isArray(body.months)) {
      return null;
    }
    var months = [];
    for (var i = 0; i < body.months.length; i++) {
      var entry = body.months[i];
      if (isProposalMonth(entry)) {
        months.push({
          month: entry.month,
          proposed_budget: entry.proposed_budget,
          total_spend: entry.total_spend,
        });
      }
    }
    if (months.length === 0) {
      return null;
    }
    return { margin: body.margin, months: months };
  }

  // "5" for 0.05 and "12.5" for 0.125 — the margin as a percent with
  // at most one decimal (no trailing .0).
  function proposalMarginPercent(margin) {
    var text = (margin * 100).toFixed(1);
    if (text.charAt(text.length - 2) === '.' && text.charAt(text.length - 1) === '0') {
      return text.slice(0, -2);
    }
    return text;
  }

  function renderBudgetsCard() {
    var budgets = state.budgets;
    var card = el('article', 'card settings-card purchase-budgets-card');
    card.setAttribute('data-testid', 'purchase-budgets');
    card.setAttribute('aria-label', BUDGETS_TITLE);
    card.appendChild(el('h2', 'settings-card-title', BUDGETS_TITLE));
    if (budgets.loading) {
      var row = el('div', 'settings-row');
      row.appendChild(el('span', 'settings-row-skel'));
      card.appendChild(row);
      return card;
    }
    if (budgets.error) {
      // A failed read: the same inline error shape as the settings
      // list, with its own Retry.
      var box = el('div', 'settings-load-error');
      box.setAttribute('role', 'alert');
      box.appendChild(el('p', 'settings-load-error-msg', budgets.error));
      var retry = el('button', 'btn btn-secondary purchase-budgets-retry', 'Retry');
      retry.type = 'button';
      box.appendChild(retry);
      card.appendChild(box);
      return card;
    }
    if (budgets.isAdmin) {
      card.appendChild(renderBudgetsForm());
      if (budgets.saveError) {
        var error = el('p', 'settings-row-error', budgets.saveError);
        error.setAttribute('role', 'alert');
        card.appendChild(error);
      }
    }
    // The header row labels the columns directly above the rows, in
    // the rows' own column template; it is not shown while the list is
    // empty (there is nothing to label).
    if (budgets.rows.length > 0) {
      card.appendChild(renderBudgetsHead());
    }
    for (var i = 0; i < budgets.rows.length; i++) {
      card.appendChild(renderBudgetRow(budgets.rows[i], budgets.isAdmin));
    }
    if (budgets.rows.length === 0) {
      card.appendChild(el('p', 'settings-row-note purchase-budgets-empty', BUDGETS_EMPTY_NOTE));
    }
    if (budgets.proposals) {
      card.appendChild(renderBudgetProposals(budgets.isAdmin));
    }
    if (!budgets.isAdmin) {
      card.appendChild(el('p', 'settings-row-note', ADMIN_NOTE));
    }
    return card;
  }

  // The header row that labels the row's columns: it reuses the
  // row's cell classes — and hence the CSS column template — so each
  // label lines up with its values.
  function renderBudgetsHead() {
    var head = el('div', 'purchase-budget-head');
    head.setAttribute('data-testid', 'purchase-budget-head');
    head.appendChild(el('span', 'purchase-budget-month', 'Month'));
    head.appendChild(el('span', 'purchase-budget-amount', 'Budget'));
    head.appendChild(el('span', 'purchase-budget-updated', 'Updated'));
    return head;
  }

  function renderBudgetRow(row, isAdmin) {
    var budgets = state.budgets;

    var budgetRow = el('div', 'settings-row purchase-budget-row');
    budgetRow.setAttribute('data-testid', 'purchase-budget-row');
    budgetRow.setAttribute('data-month', row.month);

    budgetRow.appendChild(el('span', 'purchase-budget-month', formatBudgetMonth(row.month)));
    budgetRow.appendChild(el('span', 'purchase-budget-amount', formatBudgetAmount(row.amount)));
    budgetRow.appendChild(el('span', 'purchase-budget-updated', budgetUpdatedText(row)));

    if (isAdmin) {
      var deleteButton = el('button', 'btn btn-secondary purchase-budget-delete', 'Delete');
      deleteButton.type = 'button';
      deleteButton.setAttribute('data-month', row.month);
      deleteButton.disabled = budgets.busy;
      budgetRow.appendChild(deleteButton);
    }
    return budgetRow;
  }

  // The proposals section: the note and one row per used month, in
  // the answer's order. Every node is built with textContent, so no
  // answer value ever becomes an HTML string. It sits in the card
  // after the stored rows (or the empty note) and before the
  // non-admin note.
  function renderBudgetProposals(isAdmin) {
    var proposals = state.budgets.proposals;

    var block = el('div', 'purchase-budget-proposals');
    block.setAttribute('data-testid', 'purchase-budget-proposals');
    var note = el(
      'p',
      'settings-row-note purchase-budget-proposals-note',
      "Proposed from the plan's committed and planned spend, plus " +
        proposalMarginPercent(proposals.margin) +
        '%.'
    );
    note.setAttribute('data-testid', 'purchase-budget-proposals-note');
    block.appendChild(note);
    for (var i = 0; i < proposals.months.length; i++) {
      block.appendChild(renderBudgetProposalRow(proposals.months[i], isAdmin));
    }
    return block;
  }

  // One proposals row, reusing the stored rows' column template where
  // it fits: the month formatted like the stored rows', the proposed
  // budget and the total spend in the stored rows' amount format, and
  // — for an admin — the Use button where the Delete sits.
  function renderBudgetProposalRow(entry, isAdmin) {
    var budgets = state.budgets;

    var proposalRow = el('div', 'settings-row purchase-budget-row purchase-budget-proposal');
    proposalRow.setAttribute('data-testid', 'purchase-budget-proposal');
    proposalRow.setAttribute('data-month', entry.month);

    proposalRow.appendChild(el('span', 'purchase-budget-month', formatBudgetMonth(entry.month)));
    proposalRow.appendChild(
      el('span', 'purchase-budget-amount', 'Proposed ' + formatBudgetAmount(entry.proposed_budget))
    );
    proposalRow.appendChild(
      el('span', 'purchase-budget-updated', 'Spend ' + formatBudgetAmount(entry.total_spend))
    );

    if (isAdmin) {
      var useButton = el('button', 'btn btn-secondary purchase-budget-use', 'Use');
      useButton.type = 'button';
      useButton.setAttribute('data-testid', 'purchase-budget-use');
      useButton.setAttribute('data-month', entry.month);
      useButton.disabled = budgets.busy;
      proposalRow.appendChild(useButton);
    }
    return proposalRow;
  }

  function renderBudgetsForm() {
    var draft = state.budgets.draft;

    var form = el('form', 'purchase-budgets-form settings-row');
    form.setAttribute('data-testid', 'purchase-budget-form');
    form.noValidate = true;

    var fields = el('div', 'purchase-budgets-fields');

    var monthField = el('div', 'purchase-budget-field');
    var monthLabel = el('label', 'purchase-budget-label', 'Month');
    monthLabel.setAttribute('for', 'purchase-budget-month');
    var monthInput = el('input', 'purchase-budget-input');
    monthInput.id = 'purchase-budget-month';
    monthInput.setAttribute('type', 'month');
    monthInput.setAttribute('data-testid', 'purchase-budget-month');
    monthInput.value = draft.month;
    monthField.appendChild(monthLabel);
    monthField.appendChild(monthInput);
    fields.appendChild(monthField);

    var amountField = el('div', 'purchase-budget-field');
    var amountLabel = el('label', 'purchase-budget-label', 'Amount');
    amountLabel.setAttribute('for', 'purchase-budget-amount');
    var amountInput = el('input', 'purchase-budget-input');
    amountInput.id = 'purchase-budget-amount';
    amountInput.setAttribute('type', 'number');
    amountInput.setAttribute('min', '0');
    amountInput.setAttribute('step', '0.01');
    amountInput.setAttribute('data-testid', 'purchase-budget-amount');
    amountInput.value = draft.amount;
    amountField.appendChild(amountLabel);
    amountField.appendChild(amountInput);
    fields.appendChild(amountField);

    form.appendChild(fields);

    // Save stays enabled while the inputs are invalid: a click with an
    // empty month, an empty amount or a negative amount must send
    // nothing but still show its inline message. It disables only
    // while a request is out.
    var save = el('button', 'btn btn-primary purchase-budget-save', 'Save');
    save.id = 'purchase-budget-save';
    save.type = 'button';
    save.setAttribute('data-testid', 'purchase-budget-save');
    save.disabled = state.budgets.busy;
    form.appendChild(save);
    return form;
  }

  function startBudgetsLoad() {
    var budgets = state.budgets;
    if (budgets.loading) {
      return;
    }
    budgets.loading = true;
    budgets.error = null;
    budgets.rows = [];
    budgets.saveError = null;
    render();
    var seq = ++budgetsLoadSeq;
    // The same /api/auth/me roles check the mappings card uses: only a
    // signed-in user whose roles include "admin" gets the set form and
    // the Delete; a failed read leaves the card read-only, as the
    // mappings card does.
    var meCall = global.Api.get(ME_PATH).then(
      function (me) {
        return !!(
          me &&
          me.authenticated &&
          me.user &&
          Array.isArray(me.user.roles) &&
          me.user.roles.indexOf('admin') !== -1
        );
      },
      function () {
        return false;
      }
    );
    // The proposals' own read, beside the roles read and the budgets
    // read: its own catch answers null, so a failed read (any status
    // or a network error) or an unusable body leaves the proposals
    // out and the card renders as before.
    var proposalsCall = global.Api.get(PROPOSALS_PATH).then(
      function (body) {
        return usableProposals(body);
      },
      function () {
        return null;
      }
    );
    Promise.all([meCall, global.Api.get(BUDGETS_PATH), proposalsCall]).then(
      function (results) {
        if (seq !== budgetsLoadSeq) {
          return;
        }
        var rows = Array.isArray(results[1]) ? results[1] : [];
        var valid = [];
        for (var i = 0; i < rows.length; i++) {
          if (isBudget(rows[i])) {
            valid.push(rows[i]);
          }
        }
        budgets.isAdmin = results[0];
        budgets.rows = valid;
        budgets.proposals = results[2];
        budgets.loading = false;
        render();
      },
      function (error) {
        if (seq !== budgetsLoadSeq) {
          return;
        }
        budgets.loading = false;
        budgets.error =
          error && error.message
            ? String(error.message)
            : 'The budgets could not be loaded. Please try again.';
        render();
      }
    );
  }

  // Save validates locally first — an empty month, an empty amount or
  // a negative amount sends nothing and shows its message inline —
  // then PUTs {"amount": <number>} to the month's path and reloads
  // the list. A failed save shows the detail inline and leaves the
  // form's values.
  function saveBudget() {
    var budgets = state.budgets;
    if (!budgets.isAdmin || budgets.busy || budgets.loading || budgets.error !== null) {
      return;
    }
    var month = budgets.draft.month.trim();
    var amountText = budgets.draft.amount.trim();
    if (month === '') {
      budgets.saveError = BUDGET_MONTH_REQUIRED;
      render();
      return;
    }
    if (amountText === '') {
      budgets.saveError = BUDGET_AMOUNT_REQUIRED;
      render();
      return;
    }
    var amount = Number(amountText);
    if (!isFinite(amount)) {
      budgets.saveError = BUDGET_AMOUNT_NOT_A_NUMBER;
      render();
      return;
    }
    if (amount < 0) {
      budgets.saveError = BUDGET_AMOUNT_NEGATIVE;
      render();
      return;
    }
    budgets.busy = true;
    budgets.saveError = null;
    render();
    global.Api.request(BUDGETS_PATH + '/' + encodeURIComponent(month), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount: amount }),
    }).then(
      function () {
        budgets.busy = false;
        startBudgetsLoad();
      },
      function (error) {
        budgets.busy = false;
        budgets.saveError = budgetErrorMessage(error);
        render();
      }
    );
  }

  // Delete DELETEs the month's path and reloads the list; a failed
  // delete shows the detail inline and leaves the card usable.
  function deleteBudget(month) {
    var budgets = state.budgets;
    if (budgets.busy || budgets.loading || budgets.error !== null) {
      return;
    }
    budgets.busy = true;
    budgets.saveError = null;
    render();
    global.Api.request(BUDGETS_PATH + '/' + encodeURIComponent(String(month)), {
      method: 'DELETE',
    }).then(
      function () {
        budgets.busy = false;
        startBudgetsLoad();
      },
      function (error) {
        budgets.busy = false;
        budgets.saveError = budgetErrorMessage(error);
        render();
      }
    );
  }

  // The Use button of a proposals row: it sets the set form's month
  // and amount to the row's month and proposed budget and re-renders,
  // so the form shows them. It sends no request.
  function useBudgetProposal(month) {
    var budgets = state.budgets;
    if (!budgets.isAdmin || !budgets.proposals) {
      return;
    }
    for (var i = 0; i < budgets.proposals.months.length; i++) {
      var entry = budgets.proposals.months[i];
      if (entry.month === month) {
        budgets.draft.month = entry.month;
        budgets.draft.amount = String(entry.proposed_budget);
        render();
        return;
      }
    }
  }

  // The inline card message after a failed save or delete: the
  // response's detail when an HTTP error answer carried one — api.js
  // puts a string detail into error.message verbatim and any other
  // detail as its JSON — the fixed message otherwise.
  function budgetErrorMessage(error) {
    var normalized = normalizeRequestError(error);
    if (
      normalized.status !== null &&
      normalized.message !== '' &&
      !/^Request failed \(\d+\)\.$/.test(normalized.message)
    ) {
      return normalized.message;
    }
    return BUDGET_FAILED_MESSAGE;
  }

  /* ----------------- intermittent and lumpy items ------------------- */

  // A finite whole number: the summary's counts and the row's empirical
  // units. A boolean is not a number here.
  function isWholeNumber(value) {
    return typeof value === 'number' && isFinite(value) && Math.floor(value) === value;
  }

  // A finite number: the summary's dollar figures and the row's deltas.
  function isPlainNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }

  // A well-formed item-site row as served: the three identifying
  // strings, the unit cost and the ERP reorder point null or a finite
  // number, the empirical units a whole number, the units delta a
  // finite number and the dollar change null or a finite number.
  function isImpactRow(value) {
    return (
      !!value &&
      typeof value === 'object' &&
      typeof value.item_code === 'string' &&
      typeof value.site_code === 'string' &&
      typeof value.demand_class === 'string' &&
      (value.unit_cost === null || isPlainNumber(value.unit_cost)) &&
      (value.current_reorder_point === null || isPlainNumber(value.current_reorder_point)) &&
      isWholeNumber(value.empirical_reorder_point_units) &&
      isPlainNumber(value.reorder_point_units_delta) &&
      (value.dollar_delta === null || isPlainNumber(value.dollar_delta)) &&
      // adopted is optional: the endpoint carries it only when the
      // result read the stored adoptions. When present it is a boolean.
      (value.adopted === undefined ||
        value.adopted === null ||
        typeof value.adopted === 'boolean')
    );
  }

  // A well-formed impact answer as served: the summary's six fields
  // with their types and every item-site row well-formed. Anything
  // less is a failure — no card and no message, as a non-200 answer.
  function isImpactPayload(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return false;
    }
    var summary = value.summary;
    if (!summary || typeof summary !== 'object' || Array.isArray(summary)) {
      return false;
    }
    if (!isWholeNumber(summary.item_sites)) {
      return false;
    }
    if (!isPlainNumber(summary.current_value)) {
      return false;
    }
    if (!isPlainNumber(summary.empirical_value)) {
      return false;
    }
    if (!isPlainNumber(summary.dollar_delta)) {
      return false;
    }
    if (!isWholeNumber(summary.item_sites_without_unit_cost)) {
      return false;
    }
    if (typeof summary.switch_on !== 'boolean') {
      return false;
    }
    if (!Array.isArray(value.item_sites)) {
      return false;
    }
    for (var i = 0; i < value.item_sites.length; i++) {
      if (!isImpactRow(value.item_sites[i])) {
        return false;
      }
    }
    return true;
  }

  // "$115,281.00" — dollars with two decimals and thousands
  // separators.
  function formatDollars(value) {
    return '$' + value.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  // "+$1,615,212.00" or "−$23,822.25" — the signed dollar change of the
  // summary and of the Change ($) column: "+" for zero or a gain, the
  // minus sign U+2212 (not a hyphen) for a loss, placed before the "$".
  function formatSignedDollars(value) {
    var sign = value < 0 ? '\u2212$' : '+$';
    return sign + Math.abs(value).toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  // "75" or "1,234" — a reorder point as a plain whole number with
  // thousands separators: no dollar sign, no decimals.
  function formatUnits(value) {
    return value.toLocaleString('en-US');
  }

  // The rows in display order: the largest absolute dollar change
  // first, the rows without a dollar change after all of them (the
  // sort is stable, so a tie keeps the API's item_code / site_code
  // order).
  function orderedImpactRows(rows) {
    var ordered = rows.slice();
    ordered.sort(function (left, right) {
      var leftNull = left.dollar_delta === null;
      var rightNull = right.dollar_delta === null;
      if (leftNull || rightNull) {
        return leftNull === rightNull ? 0 : leftNull ? 1 : -1;
      }
      return Math.abs(right.dollar_delta) - Math.abs(left.dollar_delta);
    });
    return ordered;
  }

  // The read carries the rows' stored adoptions when any row has a
  // boolean adopted: the endpoint includes the field only when the
  // result read the stored adoptions, so without it the table renders
  // exactly as before — no Adopted column, no action column.
  function hasAdoptedInfo(rows) {
    for (var i = 0; i < rows.length; i++) {
      if (typeof rows[i].adopted === 'boolean') {
        return true;
      }
    }
    return false;
  }

  // The card: the heading, the summary line, the switch line, the
  // table, the message of a failed adopt or withdraw and — when the
  // summary counts them — the no-unit-cost note. A failed or invalid
  // read renders nothing at all: no card, no error message, and every
  // other card is untouched.
  function renderImpactCard() {
    var impact = state.impact;
    if (!impact.loading && impact.data === null) {
      return null;
    }
    var card = el('article', 'card settings-card intermittent-impact-card');
    card.setAttribute('data-testid', 'intermittent-impact');
    card.setAttribute('aria-label', IMPACT_TITLE);
    card.appendChild(el('h2', 'settings-card-title', IMPACT_TITLE));
    if (impact.loading) {
      var row = el('div', 'settings-row');
      row.appendChild(el('span', 'settings-row-skel'));
      card.appendChild(row);
      return card;
    }
    var summary = impact.data.summary;
    var summaryLine = el(
      'p',
      'intermittent-impact-summary',
      summary.item_sites +
        ' item-sites. Reorder-point value at unit cost: ' +
        formatDollars(summary.current_value) +
        ' now, ' +
        formatDollars(summary.empirical_value) +
        ' with the empirical reorder points (' +
        formatSignedDollars(summary.dollar_delta) +
        ').'
    );
    summaryLine.setAttribute('data-testid', 'intermittent-impact-summary');
    card.appendChild(summaryLine);
    var switchLine = el(
      'p',
      'intermittent-impact-switch',
      summary.switch_on === true
        ? 'The switch is on.'
        : 'The switch is off: these are shown for review only.'
    );
    switchLine.setAttribute('data-testid', 'intermittent-impact-switch');
    card.appendChild(switchLine);
    card.appendChild(renderImpactTable(orderedImpactRows(impact.data.item_sites)));
    if (impact.saveError !== null) {
      var message = el('p', 'intermittent-impact-msg', impact.saveError);
      message.setAttribute('data-testid', 'intermittent-impact-msg');
      message.setAttribute('role', 'status');
      card.appendChild(message);
    }
    if (summary.item_sites_without_unit_cost > 0) {
      var note = el(
        'p',
        'intermittent-impact-nocost',
        summary.item_sites_without_unit_cost +
          ' item-sites have no unit cost and are not in the dollar figures.'
      );
      note.setAttribute('data-testid', 'intermittent-impact-nocost');
      card.appendChild(note);
    }
    return card;
  }

  // The table in its own horizontal scroll container, so a narrow page
  // never scrolls sideways.
  function renderImpactTable(rows) {
    var impact = state.impact;
    var showAdopted = hasAdoptedInfo(rows);
    var showActions = showAdopted && impact.isAdmin;
    var wrap = el('div', 'intermittent-impact-table-wrap');
    var table = el('table', 'intermittent-impact-table');
    table.setAttribute('data-testid', 'intermittent-impact-table');

    var headRow = el('tr');
    var columns = [
      'Item',
      'Site',
      'Class',
      'ERP reorder point',
      'Empirical reorder point',
      'Change ($)',
    ];
    if (showAdopted) {
      columns.push('Adopted');
    }
    for (var i = 0; i < columns.length; i++) {
      headRow.appendChild(el('th', null, columns[i]));
    }
    if (showActions) {
      // The action column's header carries no text: its buttons are
      // labelled by their own text.
      var actionHead = el('th', null);
      actionHead.setAttribute('aria-label', 'Actions');
      headRow.appendChild(actionHead);
    }
    var thead = el('thead');
    thead.appendChild(headRow);
    table.appendChild(thead);

    var tbody = el('tbody');
    for (var j = 0; j < rows.length; j++) {
      tbody.appendChild(renderImpactRow(rows[j], showAdopted, showActions));
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  // One row per item-site: a null value shows "—", every cell text.
  // The adoption status ("Yes"/"No") and the admin-only action button
  // render only when the read carries them.
  function renderImpactRow(row, showAdopted, showActions) {
    var impact = state.impact;
    var tr = el('tr');
    tr.appendChild(el('td', 'intermittent-impact-item', text(row.item_code)));
    tr.appendChild(el('td', 'intermittent-impact-site', text(row.site_code)));
    tr.appendChild(el('td', 'intermittent-impact-class', text(row.demand_class)));
    tr.appendChild(
      el(
        'td',
        'intermittent-impact-erp',
        row.current_reorder_point === null
          ? '\u2014'
          : formatUnits(row.current_reorder_point)
      )
    );
    tr.appendChild(
      el('td', 'intermittent-impact-empirical', formatUnits(row.empirical_reorder_point_units))
    );
    tr.appendChild(
      el(
        'td',
        'intermittent-impact-change',
        row.dollar_delta === null ? '\u2014' : formatSignedDollars(row.dollar_delta)
      )
    );
    if (showAdopted) {
      tr.appendChild(
        el('td', 'intermittent-impact-adopted', row.adopted === true ? 'Yes' : 'No')
      );
    }
    if (showActions) {
      var adopted = row.adopted === true;
      var actionButton = el(
        'button',
        adopted ? 'btn btn-secondary ii-withdraw' : 'btn btn-secondary ii-adopt',
        adopted ? 'Withdraw' : 'Adopt'
      );
      actionButton.type = 'button';
      actionButton.setAttribute(
        'data-testid',
        adopted ? 'intermittent-impact-withdraw' : 'intermittent-impact-adopt'
      );
      actionButton.setAttribute('data-item-code', text(row.item_code));
      actionButton.setAttribute('data-site-code', text(row.site_code));
      actionButton.disabled = impact.busy;
      var actionCell = el('td', 'intermittent-impact-action');
      actionCell.appendChild(actionButton);
      tr.appendChild(actionCell);
    }
    return tr;
  }

  // The card's separate read, the way the purchasing budget card reads
  // its own data: its own state, its own sequence and its own
  // re-render. A non-200 answer, a network failure or a 200 with a
  // malformed body all end with no card and no message; an answer that
  // arrives after a reset (a stale sequence) is dropped.
  function startImpactLoad() {
    var impact = state.impact;
    if (impact.loading) {
      return;
    }
    impact.loading = true;
    impact.data = null;
    render();
    var seq = ++impactLoadSeq;
    // The same /api/auth/me roles check the mappings card uses: only a
    // signed-in user whose roles include "admin" gets the Adopt and
    // Withdraw buttons; a failed read leaves the card read-only, as the
    // mappings card does.
    var meCall = global.Api.get(ME_PATH).then(
      function (me) {
        return !!(
          me &&
          me.authenticated &&
          me.user &&
          Array.isArray(me.user.roles) &&
          me.user.roles.indexOf('admin') !== -1
        );
      },
      function () {
        return false;
      }
    );
    Promise.all([meCall, global.Api.get(IMPACT_PATH)]).then(
      function (results) {
        if (seq !== impactLoadSeq) {
          return;
        }
        impact.loading = false;
        impact.isAdmin = results[0];
        if (isImpactPayload(results[1])) {
          impact.data = results[1];
        }
        render();
      },
      function () {
        if (seq !== impactLoadSeq) {
          return;
        }
        impact.loading = false;
        impact.data = null;
        render();
      }
    );
  }

  // Adopt PUTs the row's pair to /api/empirical-adoptions; withdraw
  // DELETEs the same path with the pair as query parameters. One
  // in-flight flag is shared by both, so every button stays disabled
  // while a request is out. A success re-fetches the impact read — the
  // adoption state is the endpoint's to report — and a failure shows
  // the message inline and leaves the table as it was.
  function adoptImpactRow(itemCode, siteCode) {
    sendImpactAdoption(itemCode, siteCode, true);
  }

  function withdrawImpactRow(itemCode, siteCode) {
    sendImpactAdoption(itemCode, siteCode, false);
  }

  function sendImpactAdoption(itemCode, siteCode, isAdopt) {
    var impact = state.impact;
    if (impact.busy || impact.loading || impact.data === null) {
      return;
    }
    impact.busy = true;
    impact.saveError = null;
    render();
    var path;
    var options;
    if (isAdopt) {
      path = ADOPTIONS_PATH;
      options = {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          item_code: String(itemCode),
          site_code: String(siteCode),
        }),
      };
    } else {
      path =
        ADOPTIONS_PATH +
        '?item_code=' +
        encodeURIComponent(String(itemCode)) +
        '&site_code=' +
        encodeURIComponent(String(siteCode));
      options = { method: 'DELETE' };
    }
    global.Api.request(path, options).then(
      function () {
        impact.busy = false;
        startImpactLoad();
      },
      function (error) {
        impact.busy = false;
        impact.saveError = impactErrorMessage(error);
        render();
      }
    );
  }

  // The inline card message after a failed adopt or withdraw: the
  // request's own message after the fixed prefix — api.js puts an HTTP
  // answer's string detail into error.message verbatim.
  function impactErrorMessage(error) {
    var message = error && error.message ? String(error.message) : 'the request failed.';
    return IMPACT_SAVE_FAILED_PREFIX + message;
  }

  /* ------------------- service levels and their cost ------------------- */

  // A service level: a finite fraction strictly inside (0, 1) — the
  // level's z is its normal inverse cdf, finite only there.
  function isCurveLevel(value) {
    return isPlainNumber(value) && value > 0 && value < 1;
  }

  // A well-formed point of one class' curve: the level inside (0, 1),
  // the safety stock whole units of at least 0, the value and the
  // yearly holding cost finite numbers, the delta to the previous
  // point's holding cost null or a finite number and the current flag
  // a boolean.
  function isCurvePoint(value) {
    return (
      !!value &&
      typeof value === 'object' &&
      !Array.isArray(value) &&
      isCurveLevel(value.level) &&
      isWholeNumber(value.safety_stock_units) &&
      value.safety_stock_units >= 0 &&
      isPlainNumber(value.safety_stock_value) &&
      isPlainNumber(value.holding_cost) &&
      (value.holding_cost_delta === null || isPlainNumber(value.holding_cost_delta)) &&
      typeof value.current === 'boolean'
    );
  }

  // A well-formed class entry of the curve: the class label, the
  // counted item-sites, the class' current level and its points.
  function isCurveClass(value) {
    if (
      !value ||
      typeof value !== 'object' ||
      Array.isArray(value) ||
      typeof value.abc_class !== 'string' ||
      !isWholeNumber(value.item_sites) ||
      value.item_sites < 0 ||
      !isCurveLevel(value.current_level) ||
      !Array.isArray(value.points)
    ) {
      return false;
    }
    for (var i = 0; i < value.points.length; i++) {
      if (!isCurvePoint(value.points[i])) {
        return false;
      }
    }
    return true;
  }

  // A well-formed curve answer as served: the by-class switch, the
  // annual holding rate of at least 0 and every class entry
  // well-formed. Anything less is a failure — the error note, as a
  // non-200 answer.
  function isCurvePayload(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return false;
    }
    if (typeof value.by_class !== 'boolean') {
      return false;
    }
    if (!isPlainNumber(value.holding_rate) || value.holding_rate < 0) {
      return false;
    }
    if (!Array.isArray(value.classes)) {
      return false;
    }
    for (var i = 0; i < value.classes.length; i++) {
      if (!isCurveClass(value.classes[i])) {
        return false;
      }
    }
    return true;
  }

  // "95%" or "99.5%" — the level as a percent with at most one
  // decimal: 0.995 reads 99.5% and 0.95 reads 95%.
  function formatPercent(level) {
    return Math.round(level * 1000) / 10 + '%';
  }

  // "25" — the holding rate as a whole percent (0.25 → 25).
  function holdingRatePercent(rate) {
    return Math.round(rate * 100);
  }

  // "+$908.25" or "—" — the extra cost a year over the previous
  // point's holding cost, the first point's null delta as an em dash.
  function formatCurveDelta(delta) {
    if (delta === null) {
      return EM_DASH;
    }
    return formatSignedDollars(delta);
  }

  // "99.79%" — the point's expected fill rate as a percent with two
  // decimals; "—" when the field is null, missing or not a number
  // between 0 and 1 (both ends included).
  function formatCurveFillRate(rate) {
    if (!isPlainNumber(rate) || rate < 0 || rate > 1) {
      return EM_DASH;
    }
    return (rate * 100).toFixed(2) + '%';
  }

  // A well-formed demand-basis answer as served: compared_buckets a
  // whole number of at least 0, item_sites an array of objects and —
  // with compared_buckets above 0 — the history_wape, forecast_wape
  // and value_added finite numbers. Anything less is a failure: no
  // card and no message, as a non-200 answer.
  function isBasisPayload(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return false;
    }
    if (!isWholeNumber(value.compared_buckets) || value.compared_buckets < 0) {
      return false;
    }
    if (!Array.isArray(value.item_sites)) {
      return false;
    }
    if (value.compared_buckets > 0) {
      if (!isPlainNumber(value.history_wape) || !isPlainNumber(value.forecast_wape)) {
        return false;
      }
      if (!isPlainNumber(value.value_added)) {
        return false;
      }
    }
    for (var i = 0; i < value.item_sites.length; i++) {
      var entry = value.item_sites[i];
      if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
        return false;
      }
    }
    return true;
  }

  // "1,200" — the compared buckets with thousands separators; an
  // em dash when the value is null or not a finite number.
  function formatBasisPeriods(value) {
    if (!isPlainNumber(value)) {
      return EM_DASH;
    }
    return value.toLocaleString('en-US');
  }

  // "25.0%" — a wape as a percent to one decimal; an em dash when
  // the value is null or not a finite number.
  function formatBasisWape(wape) {
    if (!isPlainNumber(wape)) {
      return EM_DASH;
    }
    return (wape * 100).toFixed(1) + '%';
  }

  // "+10.0 pts" or "−5.0 pts" — value_added times 100 to one
  // decimal, a leading "+" above 0, the minus sign U+2212 (not a
  // hyphen) below, " pts" after; "0.0 pts" for 0; an em dash when
  // the value is null or not a finite number.
  function formatBasisDifference(valueAdded) {
    if (!isPlainNumber(valueAdded)) {
      return EM_DASH;
    }
    var points = Math.abs(valueAdded * 100).toFixed(1);
    if (valueAdded > 0) {
      return '+' + points + ' pts';
    }
    if (valueAdded < 0) {
      return '\u2212' + points + ' pts';
    }
    return points + ' pts';
  }

  // The verdict line: the nearer basis and how much nearer — the
  // value added in points (times 100, absolute, one decimal) — or
  // the tie.
  function basisVerdictText(valueAdded) {
    var points = Math.abs(valueAdded * 100).toFixed(1);
    if (valueAdded > 0) {
      return 'The forecast was ' + points + ' points nearer what sold.';
    }
    if (valueAdded < 0) {
      return 'The history was ' + points + ' points nearer what sold.';
    }
    return 'Both were as near as each other.';
  }

  // The current line's text by the screen's own
  // replenishment.demand_basis entry — null without the entry or
  // with any value but the two known ones, so the card shows no
  // such line.
  function basisCurrentText() {
    var entry = entryByKey(BASIS_SETTING_KEY);
    if (entry && entry.value === 'history') {
      return BASIS_CURRENT_HISTORY;
    }
    if (entry && entry.value === 'forecast') {
      return BASIS_CURRENT_FORECAST;
    }
    return null;
  }

  // The proposal line's text: the exceptions inbox shows the same
  // proposal — the basis the measured value added points to — and
  // the card offers it for the admin's demand basis setting. The
  // answer's compared_buckets must be at least the screen's own
  // exceptions.basis_switch_min_periods value and either the
  // screen's own replenishment.demand_basis entry must hold
  // "history" with value_added times 100 at least the screen's own
  // exceptions.basis_switch_margin value (it proposes the forecast)
  // or it must hold "forecast" with minus value_added times 100 at
  // least the margin (it proposes the history). Null — no line —
  // without either threshold entry or with a non-finite threshold
  // value, with the basis entry missing or holding any other value,
  // or when the margin is not met.
  function basisProposalText(data) {
    var marginEntry = entryByKey(BASIS_MARGIN_KEY);
    if (!marginEntry || !isPlainNumber(marginEntry.value)) {
      return null;
    }
    var minPeriodsEntry = entryByKey(BASIS_MIN_PERIODS_KEY);
    if (!minPeriodsEntry || !isPlainNumber(minPeriodsEntry.value)) {
      return null;
    }
    var basisEntry = entryByKey(BASIS_SETTING_KEY);
    if (!basisEntry || (basisEntry.value !== 'history' && basisEntry.value !== 'forecast')) {
      return null;
    }
    if (!(data.compared_buckets >= minPeriodsEntry.value)) {
      return null;
    }
    var points = data.value_added * 100;
    if (basisEntry.value === 'history' && points >= marginEntry.value) {
      return BASIS_PROPOSED_FORECAST;
    }
    if (basisEntry.value === 'forecast' && -points >= marginEntry.value) {
      return BASIS_PROPOSED_HISTORY;
    }
    return null;
  }

  // The target line's text by the answer's service_target — the
  // cycle service level or the fill-rate target; null when the field
  // is missing or carries any other value, so the card shows no such
  // line and the answer is still accepted.
  function curveTargetText(target) {
    if (target === 'cycle') {
      return CURVE_TARGET_CYCLE;
    }
    if (target === 'fill_rate') {
      return CURVE_TARGET_FILL_RATE;
    }
    return null;
  }

  // The card: the heading, the mode line, the rate line, the target
  // line (only for a known service_target) and one block per class
  // entry in the answer's order — the class' heading, its points'
  // table, or the empty note for a class with no item-sites. A failed
  // or malformed read renders the heading and the error note, and no
  // class blocks; while the read is out, a short skeleton. The card is
  // read-only: it sends no write.
  function renderCurveCard() {
    var curve = state.curve;
    var card = el('article', 'card settings-card service-curve-card');
    card.setAttribute('data-testid', 'service-curve');
    card.setAttribute('aria-label', CURVE_TITLE);
    card.appendChild(el('h2', 'settings-card-title', CURVE_TITLE));
    if (curve.loading) {
      var row = el('div', 'settings-row');
      row.appendChild(el('span', 'settings-row-skel'));
      card.appendChild(row);
      return card;
    }
    if (curve.data === null) {
      var note = el('p', 'service-curve-error', CURVE_LOAD_ERROR);
      note.setAttribute('data-testid', 'service-curve-error');
      card.appendChild(note);
      return card;
    }
    var data = curve.data;
    var modeLine = el(
      'p',
      'service-curve-mode',
      data.by_class ? CURVE_MODE_BY_CLASS : CURVE_MODE_SINGLE
    );
    modeLine.setAttribute('data-testid', 'service-curve-mode');
    card.appendChild(modeLine);
    var rateLine = el(
      'p',
      'service-curve-rate',
      'Holding cost at ' + holdingRatePercent(data.holding_rate) + '% of unit cost a year.'
    );
    rateLine.setAttribute('data-testid', 'service-curve-rate');
    card.appendChild(rateLine);
    var targetText = curveTargetText(data.service_target);
    if (targetText !== null) {
      var targetLine = el('p', 'service-curve-target', targetText);
      targetLine.setAttribute('data-testid', 'service-curve-target');
      card.appendChild(targetLine);
    }
    for (var i = 0; i < data.classes.length; i++) {
      card.appendChild(renderCurveClass(data.classes[i]));
    }
    card.appendChild(renderCurveSuggest());
    return card;
  }

  // The card: the heading, the current line (only for the two known
  // values of the screen's own replenishment.demand_basis entry),
  // the none line for a compared_buckets of 0 — and nothing after
  // it — or, with periods compared, the summary line, the verdict,
  // the proposal line (only when the answer's measures and the
  // screen's own settings entries propose a switch), the item-site
  // table and the more line for more than 10 entries. A failed or
  // malformed read, like the read being out, renders no card at
  // all, so the caller appends it only when present.
  function renderBasisCard() {
    var basis = state.basis;
    if (basis.loading || basis.data === null) {
      return null;
    }
    var data = basis.data;
    var card = el('article', 'card settings-card demand-basis-card');
    card.setAttribute('data-testid', 'demand-basis');
    card.setAttribute('aria-label', BASIS_TITLE);
    card.appendChild(el('h2', 'settings-card-title', BASIS_TITLE));
    var currentText = basisCurrentText();
    if (currentText !== null) {
      var currentLine = el('p', 'demand-basis-current', currentText);
      currentLine.setAttribute('data-testid', 'demand-basis-current');
      card.appendChild(currentLine);
    }
    if (data.compared_buckets === 0) {
      var noneLine = el('p', 'demand-basis-none', BASIS_NONE_LINE);
      noneLine.setAttribute('data-testid', 'demand-basis-none');
      card.appendChild(noneLine);
      return card;
    }
    var summaryLine = el(
      'p',
      'demand-basis-summary',
      'One period ahead, over ' +
        data.compared_buckets.toLocaleString('en-US') +
        ' periods: ' +
        formatBasisWape(data.history_wape) +
        ' error on the history, ' +
        formatBasisWape(data.forecast_wape) +
        ' on the forecast.'
    );
    summaryLine.setAttribute('data-testid', 'demand-basis-summary');
    card.appendChild(summaryLine);
    var verdictLine = el('p', 'demand-basis-verdict', basisVerdictText(data.value_added));
    verdictLine.setAttribute('data-testid', 'demand-basis-verdict');
    card.appendChild(verdictLine);
    var proposalText = basisProposalText(data);
    if (proposalText !== null) {
      var proposalLine = el('p', 'demand-basis-proposal', proposalText);
      proposalLine.setAttribute('data-testid', 'demand-basis-proposal');
      card.appendChild(proposalLine);
    }
    card.appendChild(renderBasisTable(data));
    if (data.item_sites.length > BASIS_MAX_ROWS) {
      var moreLine = el(
        'p',
        'demand-basis-more',
        'The 10 item-sites with the most demand of ' +
          data.item_sites.length.toLocaleString('en-US') +
          '.'
      );
      moreLine.setAttribute('data-testid', 'demand-basis-more');
      card.appendChild(moreLine);
    }
    return card;
  }

  // The item-site table inside its own sideways-scroll container:
  // one row for each of the first 10 entries of item_sites, in the
  // answer's order.
  function renderBasisTable(data) {
    var wrap = el('div', 'demand-basis-table-wrap');
    var table = el('table', 'demand-basis-table');
    table.setAttribute('data-testid', 'demand-basis-table');
    var thead = el('thead');
    var headRow = el('tr');
    for (var h = 0; h < BASIS_COLUMNS.length; h++) {
      headRow.appendChild(el('th', null, BASIS_COLUMNS[h]));
    }
    thead.appendChild(headRow);
    table.appendChild(thead);
    var tbody = el('tbody');
    var rows = Math.min(BASIS_MAX_ROWS, data.item_sites.length);
    for (var i = 0; i < rows; i++) {
      tbody.appendChild(renderBasisRow(data.item_sites[i]));
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  // One item-site's row: the codes, the compared buckets with
  // thousands separators, the two wapes as percents to one decimal
  // and the difference in points — a null or non-numeric value as
  // an em dash.
  function renderBasisRow(entry) {
    var row = el('tr', 'demand-basis-row');
    row.setAttribute('data-testid', 'demand-basis-row');
    row.appendChild(el('td', 'demand-basis-item', basisCodeText(entry.item_code)));
    row.appendChild(el('td', 'demand-basis-site', basisCodeText(entry.site_code)));
    row.appendChild(el('td', 'demand-basis-periods', formatBasisPeriods(entry.compared_buckets)));
    row.appendChild(el('td', 'demand-basis-history', formatBasisWape(entry.history_wape)));
    row.appendChild(el('td', 'demand-basis-forecast', formatBasisWape(entry.forecast_wape)));
    row.appendChild(el('td', 'demand-basis-difference', formatBasisDifference(entry.value_added)));
    return row;
  }

  // A code as text; an em dash when it is missing or not a string.
  function basisCodeText(value) {
    if (typeof value !== 'string' || value.trim() === '') {
      return EM_DASH;
    }
    return value;
  }

  // One class' block: the heading — the class label, the counted
  // item-sites and the class' current level as a percent — and, for a
  // class with item-sites, its points' table; a class without
  // item-sites shows the empty note instead. A class whose
  // fill_rate_item_sites is a whole number below its item-sites also
  // gains the fill note under its heading; a class where they are
  // equal, or where the field is missing, has no such line.
  function renderCurveClass(entry) {
    var block = el('section', 'service-curve-class');
    block.setAttribute('data-testid', 'service-curve-class');
    block.appendChild(
      el(
        'h3',
        'service-curve-class-title',
        'Class ' +
          entry.abc_class +
          ' \u00B7 ' +
          entry.item_sites +
          ' item-sites \u00B7 now ' +
          formatPercent(entry.current_level)
      )
    );
    if (isWholeNumber(entry.fill_rate_item_sites) && entry.fill_rate_item_sites < entry.item_sites) {
      var fillNote = el(
        'p',
        'service-curve-fill-note',
        'Fill rate from ' +
          entry.fill_rate_item_sites +
          ' of ' +
          entry.item_sites +
          ' item-sites with an order quantity.'
      );
      fillNote.setAttribute('data-testid', 'service-curve-fill-note');
      block.appendChild(fillNote);
    }
    if (entry.item_sites === 0) {
      var note = el('p', 'service-curve-empty', CURVE_EMPTY_NOTE);
      note.setAttribute('data-testid', 'service-curve-empty');
      block.appendChild(note);
      return block;
    }
    var wrap = el('div', 'service-curve-table-wrap');
    var table = el('table', 'service-curve-table');
    var thead = el('thead');
    var headRow = el('tr');
    for (var h = 0; h < CURVE_COLUMNS.length; h++) {
      headRow.appendChild(el('th', null, CURVE_COLUMNS[h]));
    }
    thead.appendChild(headRow);
    table.appendChild(thead);
    var tbody = el('tbody');
    for (var i = 0; i < entry.points.length; i++) {
      tbody.appendChild(renderCurvePoint(entry.points[i]));
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    block.appendChild(wrap);
    return block;
  }

  // One point's row: the level as a percent ("(now)" after it on the
  // current point, whose row carries is-current), the safety stock as
  // whole units, the value and the yearly holding cost as dollars, the
  // extra cost the delta with its sign and the expected fill rate as
  // a percent with two decimals (an em dash when it is not a number
  // between 0 and 1).
  function renderCurvePoint(point) {
    var isCurrent = point.current === true;
    var row = el('tr', isCurrent ? 'service-curve-row is-current' : 'service-curve-row');
    row.setAttribute('data-testid', 'service-curve-point');
    row.appendChild(
      el('td', 'service-curve-level', formatPercent(point.level) + (isCurrent ? ' (now)' : ''))
    );
    row.appendChild(el('td', 'service-curve-units', formatUnits(point.safety_stock_units)));
    row.appendChild(el('td', 'service-curve-value', formatDollars(point.safety_stock_value)));
    row.appendChild(el('td', 'service-curve-holding', formatDollars(point.holding_cost)));
    row.appendChild(el('td', 'service-curve-delta', formatCurveDelta(point.holding_cost_delta)));
    row.appendChild(el('td', 'service-curve-fill', formatCurveFillRate(point.expected_fill_rate)));
    return row;
  }

  // The card's separate read, the way the intermittent-impact card
  // reads its own data: its own state, its own sequence and its own
  // re-render. A non-200 answer, a network failure or a 200 with a
  // malformed body all end with the error note and no class blocks;
  // an answer that arrives after a reset (a stale sequence) is
  // dropped, and sign-out's reset bumps the sequence and clears the
  // card's state.
  function startCurveLoad() {
    var curve = state.curve;
    if (curve.loading) {
      return;
    }
    curve.loading = true;
    curve.data = null;
    // The suggestion block re-reads with the card: the result, the
    // invalid line and the error line are cleared and any answer
    // still out is dropped; the typed value is kept.
    curve.suggest.result = null;
    curve.suggest.invalid = false;
    curve.suggest.error = false;
    curveSuggestSeq += 1;
    render();
    var seq = ++curveLoadSeq;
    global.Api.get(CURVE_PATH).then(
      function (payload) {
        if (seq !== curveLoadSeq) {
          return;
        }
        curve.loading = false;
        curve.data = isCurvePayload(payload) ? payload : null;
        render();
      },
      function () {
        if (seq !== curveLoadSeq) {
          return;
        }
        curve.loading = false;
        curve.data = null;
        render();
      }
    );
  }


  // The demand-basis card's separate read, the way the curve card
  // reads its own data: its own state, its own sequence and its own
  // re-render. A non-200 answer, a network failure or a 200 with a
  // malformed body all end with no card and no message; an answer
  // that arrives after a newer load has started (a stale sequence)
  // is ignored, and sign-out's reset bumps the sequence and clears
  // the card's state. The card is absent while the read is out.
  function startBasisLoad() {
    var basis = state.basis;
    if (basis.loading) {
      return;
    }
    basis.loading = true;
    basis.data = null;
    render();
    var seq = ++basisLoadSeq;
    global.Api.get(BASIS_PATH).then(
      function (payload) {
        if (seq !== basisLoadSeq) {
          return;
        }
        basis.loading = false;
        basis.data = isBasisPayload(payload) ? payload : null;
        render();
      },
      function () {
        if (seq !== basisLoadSeq) {
          return;
        }
        basis.loading = false;
        basis.data = null;
        render();
      }
    );
  }


  /* ------------------- suggestion of the levels ------------------- */

  // The levels of one combination of per-class service levels: one
  // per class A, B and C, each a service level strictly inside
  // (0, 1) — the level the card writes as a percent.
  function isSuggestionLevels(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return false;
    }
    for (var i = 0; i < CURVE_SUGGEST_CLASSES.length; i++) {
      var key = CURVE_SUGGEST_CLASSES[i];
      if (!Object.prototype.hasOwnProperty.call(value, key) || !isCurveLevel(value[key])) {
        return false;
      }
    }
    return true;
  }

  // A well-formed combination: the three classes' levels, its yearly
  // holding cost and its expected fill rate (null when no item-site
  // takes part, a fraction between 0 and 1 otherwise).
  function isSuggestionCombination(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return false;
    }
    if (!isSuggestionLevels(value.levels)) {
      return false;
    }
    if (!isPlainNumber(value.holding_cost)) {
      return false;
    }
    if (
      value.expected_fill_rate !== null &&
      (!isPlainNumber(value.expected_fill_rate) ||
        value.expected_fill_rate < 0 ||
        value.expected_fill_rate > 1)
    ) {
      return false;
    }
    return true;
  }

  // A well-formed suggestion answer as served: the requested target
  // and the current and highest combinations (each carrying its
  // levels), the suggested combination or null and the holding-cost
  // delta (null with no suggestion). Anything less — a body without
  // the current and highest objects that each carry levels — is a
  // failure, the error line.
  function isSuggestionPayload(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return false;
    }
    if (
      !isPlainNumber(value.target_fill_rate) ||
      value.target_fill_rate <= 0 ||
      value.target_fill_rate > 1
    ) {
      return false;
    }
    if (!isSuggestionCombination(value.current) || !isSuggestionCombination(value.highest)) {
      return false;
    }
    if (value.suggested !== null && !isSuggestionCombination(value.suggested)) {
      return false;
    }
    if (value.holding_cost_delta !== null && !isPlainNumber(value.holding_cost_delta)) {
      return false;
    }
    return true;
  }

  // "0.97", "0.999", "0.9725" — the target percent divided by 100,
  // written with at most four decimals and no trailing zeros.
  function suggestionQueryParam(targetPercent) {
    var fraction = Math.round((targetPercent / 100) * 10000) / 10000;
    return String(fraction);
  }

  // "A 95%, B 92%, C 97%" — the combination's levels in the class
  // order, a level the way the card writes its levels.
  function suggestionLevelsText(levels) {
    var parts = [];
    for (var i = 0; i < CURVE_SUGGEST_CLASSES.length; i++) {
      var key = CURVE_SUGGEST_CLASSES[i];
      parts.push(key + ' ' + formatPercent(levels[key]));
    }
    return parts.join(', ');
  }

  // The suggested line's last words, the holding-cost delta: below
  // zero "<amount> less", above zero "<amount> more", zero "the
  // same" — the amount as dollars without its sign.
  function suggestionDeltaTail(delta) {
    if (!isPlainNumber(delta) || delta === 0) {
      return 'the same';
    }
    return (delta < 0 ? formatDollars(-delta) + ' less' : formatDollars(delta) + ' more');
  }

  // The answer in words: the current line and, by the answer, the
  // suggested line with the delta's last words, the out-of-reach
  // line for a null suggested (the target the answer's
  // target_fill_rate, the highest levels' fill rate and cost) or the
  // nothing-to-measure line alone when the current combination's
  // fill rate is null. Fill rates the way the card's Fill rate
  // column writes them (a missing one as an em dash), money the way
  // the card writes money.
  function renderSuggestResult(result) {
    var resultEl = el('div', 'service-curve-suggest-result');
    resultEl.setAttribute('data-testid', 'service-curve-suggest-result');

    if (result.current.expected_fill_rate === null) {
      var nopart = el('p', 'service-curve-suggest-nopart', CURVE_SUGGEST_NOPART);
      nopart.setAttribute('data-testid', 'service-curve-suggest-nopart');
      resultEl.appendChild(nopart);
      return resultEl;
    }

    var currentLine = el(
      'p',
      'service-curve-suggest-current',
      'Now: ' +
        suggestionLevelsText(result.current.levels) +
        '. Fill rate ' +
        formatCurveFillRate(result.current.expected_fill_rate) +
        ', holding cost ' +
        formatDollars(result.current.holding_cost) +
        ' a year.'
    );
    currentLine.setAttribute('data-testid', 'service-curve-suggest-current');
    resultEl.appendChild(currentLine);

    var suggested = result.suggested;
    if (suggested === null) {
      var none = el(
        'p',
        'service-curve-suggest-none',
        'No combination of levels reaches ' +
          formatPercent(result.target_fill_rate) +
          '. The highest levels (' +
          suggestionLevelsText(result.highest.levels) +
          ') give ' +
          formatCurveFillRate(result.highest.expected_fill_rate) +
          ' for ' +
          formatDollars(result.highest.holding_cost) +
          ' a year.'
      );
      none.setAttribute('data-testid', 'service-curve-suggest-none');
      resultEl.appendChild(none);
    } else {
      var suggestedLine = el(
        'p',
        'service-curve-suggest-suggested',
        'Suggested: ' +
          suggestionLevelsText(suggested.levels) +
          '. Fill rate ' +
          formatCurveFillRate(suggested.expected_fill_rate) +
          ', holding cost ' +
          formatDollars(suggested.holding_cost) +
          ' a year, ' +
          suggestionDeltaTail(result.holding_cost_delta) +
          '.'
      );
      suggestedLine.setAttribute('data-testid', 'service-curve-suggest-suggested');
      resultEl.appendChild(suggestedLine);
    }
    return resultEl;
  }

  // The block: the title, the labelled target input (the typed value
  // kept across re-renders), the button and the note — and, by the
  // latest ask, the result, the refused-input line or the failed-
  // read line. None of it carries role="alert".
  function renderCurveSuggest() {
    var suggest = state.curve.suggest;

    var block = el('section', 'service-curve-suggest');
    block.setAttribute('data-testid', 'service-curve-suggest');

    var title = el('h3', 'service-curve-suggest-title', CURVE_SUGGEST_TITLE);
    title.setAttribute('data-testid', 'service-curve-suggest-title');
    block.appendChild(title);

    var label = el('label', 'service-curve-suggest-label', CURVE_SUGGEST_LABEL);
    label.setAttribute('for', 'service-curve-suggest-target');
    block.appendChild(label);

    var input = el('input', 'service-curve-suggest-input');
    input.id = 'service-curve-suggest-target';
    input.setAttribute('type', 'number');
    input.setAttribute('min', String(CURVE_SUGGEST_TARGET_MIN));
    input.setAttribute('max', String(CURVE_SUGGEST_TARGET_MAX));
    input.setAttribute('step', '0.1');
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('data-testid', 'service-curve-suggest-target');
    input.value = suggest.target;
    block.appendChild(input);

    var go = el('button', 'btn btn-primary service-curve-suggest-go', CURVE_SUGGEST_GO);
    go.id = 'service-curve-suggest-go';
    go.type = 'button';
    go.setAttribute('data-testid', 'service-curve-suggest-go');
    block.appendChild(go);

    var note = el('p', 'service-curve-suggest-note', CURVE_SUGGEST_NOTE);
    note.setAttribute('data-testid', 'service-curve-suggest-note');
    block.appendChild(note);

    if (suggest.result) {
      block.appendChild(renderSuggestResult(suggest.result));
    }
    if (suggest.invalid) {
      var invalid = el('p', 'service-curve-suggest-invalid', CURVE_SUGGEST_INVALID);
      invalid.setAttribute('data-testid', 'service-curve-suggest-invalid');
      block.appendChild(invalid);
    }
    if (suggest.error) {
      var error = el('p', 'service-curve-suggest-error', CURVE_SUGGEST_ERROR);
      error.setAttribute('data-testid', 'service-curve-suggest-error');
      block.appendChild(error);
    }
    return block;
  }

  function restoreSuggestFocus() {
    var inputEl = rootEl
      ? rootEl.querySelector('[data-testid="service-curve-suggest-target"]')
      : null;
    if (inputEl) {
      inputEl.focus();
    }
  }

  // One ask: a click on the button or an Enter in the input with a
  // value from 50 to 99.9 sends the one GET — a refused value shows
  // the invalid line and sends nothing. The read is guarded by its
  // own sequence: only the answer to the latest ask is shown, an
  // earlier answer that arrives later is dropped, and a curve
  // re-read or a sign-out (both bump the sequence) drops any answer
  // still out. While a read is out no result is shown, and after an
  // Enter the focus comes back to the input when the answer is
  // shown.
  function askSuggestion() {
    var suggest = state.curve.suggest;
    var value = Number(suggest.target);
    if (!isFinite(value) || value < CURVE_SUGGEST_TARGET_MIN || value > CURVE_SUGGEST_TARGET_MAX) {
      suggest.invalid = true;
      suggest.error = false;
      render();
      return;
    }
    var inputEl = rootEl
      ? rootEl.querySelector('[data-testid="service-curve-suggest-target"]')
      : null;
    var restoreFocus = !!(inputEl && document.activeElement === inputEl);
    suggest.result = null;
    suggest.invalid = false;
    suggest.error = false;
    var seq = ++curveSuggestSeq;
    global.Api.get(SUGGESTION_PATH + '?target_fill_rate=' + suggestionQueryParam(value)).then(
      function (payload) {
        if (seq !== curveSuggestSeq) {
          return;
        }
        suggest.result = isSuggestionPayload(payload) ? payload : null;
        suggest.invalid = false;
        suggest.error = suggest.result === null;
        render();
        if (restoreFocus) {
          restoreSuggestFocus();
        }
      },
      function () {
        if (seq !== curveSuggestSeq) {
          return;
        }
        suggest.result = null;
        suggest.invalid = false;
        suggest.error = true;
        render();
        if (restoreFocus) {
          restoreSuggestFocus();
        }
      }
    );
  }

  /* -------------------------------- load ------------------------------- */

  function startLoad() {
    if (state.loading) {
      return;
    }
    state.loading = true;
    state.error = null;
    state.entries = [];
    render();
    var seq = ++loadSeq;
    global.Api.get(LIST_PATH).then(
      function (entries) {
        if (seq !== loadSeq) {
          return;
        }
        var rows = [];
        if (Array.isArray(entries)) {
          for (var i = 0; i < entries.length; i++) {
            if (isEntry(entries[i])) {
              rows.push(entries[i]);
            }
          }
        }
        state.entries = rows;
        state.loading = false;
        render();
      },
      function (error) {
        if (seq !== loadSeq) {
          return;
        }
        state.loading = false;
        state.error =
          error && error.message
            ? String(error.message)
            : 'The settings could not be loaded. Please try again.';
        render();
      }
    );
  }

  /* ------------------------------- changes ----------------------------- */

  // The submitted text of a number row as a finite number within
  // [min, max] — a bound applies only when the entry carries it — or
  // null when the text is blank, not a number or out of range: those
  // are refused locally, with no request.
  function parseNumberValue(raw, entry) {
    var trimmed = text(raw).trim();
    if (trimmed === '') {
      return null;
    }
    var value = Number(trimmed);
    if (!isFinite(value)) {
      return null;
    }
    if (finiteNumber(entry.min) && value < entry.min) {
      return null;
    }
    if (finiteNumber(entry.max) && value > entry.max) {
      return null;
    }
    return value;
  }

  // The row error for a refused number save: the entry's bounds when
  // it carries them, a plain number ask otherwise.
  function numberRejectMessage(entry) {
    if (finiteNumber(entry.min) && finiteNumber(entry.max)) {
      return (
        'Enter a number between ' + text(entry.min) + ' and ' + text(entry.max) + '.'
      );
    }
    return 'Enter a number.';
  }

  // A change PUTs the new value; a reset DELETEs the key. Both replace
  // the row from the response — the previous entry stays in state while
  // the request is in flight, so a failed change or reset ends with the
  // previous value restored and the control and its Reset enabled again
  // (unless the entry was not editable before) and the message inline.
  function changeValue(key, newValue) {
    if (state.updating[key] || !entryByKey(key)) {
      return;
    }
    state.updating[key] = true;
    state.errors[key] = null;
    render();
    requestSetting(key, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: newValue }),
    });
  }

  function resetValue(key) {
    if (state.updating[key] || !entryByKey(key)) {
      return;
    }
    state.updating[key] = true;
    state.errors[key] = null;
    render();
    requestSetting(key, { method: 'DELETE' });
  }

  // Error normalization for an Api.request rejection: the status and
  // the message derive from error.status and error.message — never
  // from error.detail, which the HTTP layer does not carry.
  function normalizeRequestError(error) {
    var status = error && typeof error.status === 'number' ? error.status : null;
    var message = error && error.message ? String(error.message) : '';
    return { status: status, message: message };
  }

  // The inline message under a row after a failed change or reset: the
  // response's detail when an HTTP error answer carried one — api.js
  // puts a string detail into error.message verbatim and any other
  // detail as its JSON — the fixed message otherwise: an HTTP answer
  // without a detail (api.js' "Request failed (<status>)"), a 200 whose
  // body is not the setting entry, or no HTTP answer at all.
  function changeErrorMessage(error) {
    var normalized = normalizeRequestError(error);
    if (
      normalized.status !== null &&
      normalized.message !== '' &&
      !/^Request failed \(\d+\)\.$/.test(normalized.message)
    ) {
      return normalized.message;
    }
    return CHANGE_FAILED_MESSAGE;
  }

  // A 200 updates the row only when its body is a setting entry for
  // the key: an object carrying key, value and source. Anything else
  // ends the change as a failure.
  function isEntryFor(key, candidate) {
    return (
      !!candidate &&
      typeof candidate === 'object' &&
      typeof candidate.key === 'string' &&
      candidate.key === key &&
      candidate.value !== undefined &&
      candidate.value !== null &&
      candidate.source !== undefined &&
      candidate.source !== null
    );
  }

  function requestSetting(key, options) {
    return global.Api.request('/api/settings/' + encodeURIComponent(key), options).then(
      function (updated) {
        if (isEntryFor(key, updated)) {
          replaceEntry(updated);
        } else {
          state.errors[key] = CHANGE_FAILED_MESSAGE;
        }
        finishChange(key);
      },
      function (error) {
        state.errors[key] = changeErrorMessage(error);
        finishChange(key);
      }
    );
  }

  // The end of a change or reset, success or failure: the in-flight
  // flag comes off and the row re-renders. On a failure state still
  // holds the previous entry, so the value it showed before the change
  // is back and the control and its Reset are enabled again — unless
  // the entry was not editable before the change.
  function finishChange(key) {
    delete state.updating[key];
    render();
  }

  function replaceEntry(updated) {
    for (var i = 0; i < state.entries.length; i++) {
      if (state.entries[i].key === updated.key) {
        state.entries[i] = updated;
        return;
      }
    }
    state.entries.push(updated);
  }

  /* -------------------------------- boot ------------------------------- */

  function settingsActive() {
    var active = document.querySelector('.view.is-active');
    return !!active && active.getAttribute('data-view') === 'settings';
  }

  function activate() {
    var appEl = document.getElementById('app');
    if (!appEl || appEl.hidden || !settingsActive()) {
      return;
    }
    startLoad();
    startMappingsLoad();
    startBudgetsLoad();
    startImpactLoad();
    startCurveLoad();
    startBasisLoad();
  }

  function reset() {
    loadSeq += 1;
    mappingsLoadSeq += 1;
    suggestionsLoadSeq += 1;
    budgetsLoadSeq += 1;
    impactLoadSeq += 1;
    curveLoadSeq += 1;
    curveSuggestSeq += 1;
    basisLoadSeq += 1;
    state.loading = false;
    state.error = null;
    state.entries = [];
    state.updating = {};
    state.errors = {};
    state.mappings = newMappingsState();
    state.budgets = newBudgetsState();
    state.impact = newImpactState();
    state.curve = newCurveState();
    state.basis = newBasisState();
    if (rootEl) {
      rootEl.textContent = '';
    }
  }

  (function boot() {
    rootEl = document.getElementById('settings-root');
    if (!rootEl) {
      return;
    }

    rootEl.addEventListener('click', function (event) {
      var target = event.target;
      if (!target || !target.closest) {
        return;
      }
      var button = target.closest('button');
      if (!button || button.disabled) {
        return;
      }
      if (button.classList.contains('erp-mappings-retry')) {
        startMappingsLoad();
        return;
      }
      if (button.id === 'erp-mapping-save') {
        saveMapping();
        return;
      }
      if (button.classList.contains('erp-mapping-delete')) {
        deleteMapping(button.getAttribute('data-id'));
        return;
      }
      if (button.classList.contains('erp-suggestion-confirm')) {
        // The button carries no code attributes: the row is the
        // locator's target, and it carries the codes.
        var suggestionRowEl = button.closest('[data-testid="erp-suggestion-row"]');
        if (suggestionRowEl) {
          confirmSuggestion(
            suggestionRowEl.getAttribute('data-source-code'),
            suggestionRowEl.getAttribute('data-target-code')
          );
        }
        return;
      }
      if (button.classList.contains('purchase-budgets-retry')) {
        startBudgetsLoad();
        return;
      }
      if (button.id === 'purchase-budget-save') {
        saveBudget();
        return;
      }
      if (button.classList.contains('purchase-budget-delete')) {
        deleteBudget(button.getAttribute('data-month'));
        return;
      }
      if (button.classList.contains('purchase-budget-use')) {
        useBudgetProposal(button.getAttribute('data-month'));
        return;
      }
      if (button.classList.contains('ii-adopt')) {
        adoptImpactRow(
          button.getAttribute('data-item-code'),
          button.getAttribute('data-site-code')
        );
        return;
      }
      if (button.classList.contains('ii-withdraw')) {
        withdrawImpactRow(
          button.getAttribute('data-item-code'),
          button.getAttribute('data-site-code')
        );
        return;
      }
      if (button.classList.contains('settings-retry')) {
        startLoad();
        return;
      }
      if (button.id === 'service-curve-suggest-go') {
        askSuggestion();
        return;
      }
      var key = button.getAttribute('data-key');
      if (!key) {
        return;
      }
      if (button.classList.contains('settings-reset')) {
        resetValue(key);
        return;
      }
      if (button.classList.contains('settings-switch')) {
        var entry = entryByKey(key);
        if (!entry) {
          return;
        }
        changeValue(key, entry.value === true ? false : true);
        return;
      }
      if (button.classList.contains('settings-choice')) {
        changeValue(key, button.getAttribute('data-value'));
      }
    });

    rootEl.addEventListener('input', function (event) {
      var target = event.target;
      if (!target || !target.id) {
        return;
      }
      var draft = state.mappings.draft;
      if (target.id === 'erp-mapping-kind') {
        draft.kind = target.value === 'site' || target.value === 'vendor' ? target.value : 'item';
        syncMappingSave();
      } else if (target.id === 'erp-mapping-source') {
        draft.source = target.value;
        syncMappingSave();
      } else if (target.id === 'erp-mapping-target') {
        draft.target = target.value;
        syncMappingSave();
      } else if (target.id === 'purchase-budget-month') {
        state.budgets.draft.month = target.value;
      } else if (target.id === 'purchase-budget-amount') {
        state.budgets.draft.amount = target.value;
      } else if (target.id === 'service-curve-suggest-target') {
        state.curve.suggest.target = target.value;
      }
    });

    // The number rows' forms submit here, never through a per-row
    // listener: a Save click and an Enter in the input both fire the
    // form's submit, so both take the same path — a refused value
    // shows the row error without a request, a valid one goes to
    // changeValue exactly like a switch or a choice.
    rootEl.addEventListener('submit', function (event) {
      var target = event.target;
      if (!target || !target.classList || !target.classList.contains('settings-number')) {
        return;
      }
      event.preventDefault();
      var key = target.getAttribute('data-key');
      var entry = key ? entryByKey(key) : null;
      if (!entry) {
        return;
      }
      var input = target.querySelector('.settings-number-input');
      var value = parseNumberValue(input ? input.value : '', entry);
      if (value === null) {
        state.errors[key] = numberRejectMessage(entry);
        render();
        return;
      }
      changeValue(key, value);
    });

    // The suggestion block's ask: an Enter in the target input takes
    // the same path as a click on the button — a refused value shows
    // the invalid line without a request, a valid one sends the GET.
    rootEl.addEventListener('keydown', function (event) {
      var target = event.target;
      if (!target || target.id !== 'service-curve-suggest-target') {
        return;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        askSuggestion();
      }
    });

    var appEl = document.getElementById('app');
    if (!appEl) {
      return;
    }
    // The settings load once the signed-in shell is visible and the
    // settings view is the active one; the hashchange handler (app.js)
    // calls activate() on every switch to #/settings.
    var observer = new MutationObserver(function () {
      if (!appEl.hidden && settingsActive()) {
        activate();
      }
    });
    observer.observe(appEl, { attributes: true, attributeFilter: ['hidden'] });
    if (!appEl.hidden && settingsActive()) {
      activate();
    }
  })();

  global.Settings = {
    activate: activate,
    reset: reset,
  };
})(window);
