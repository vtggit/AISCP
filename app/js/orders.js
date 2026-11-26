// AIERP planner screen — the Order plan view (#/orders).
//
// #/orders shows the supply plan — GET /api/supply-plan?horizon_days=<n>
// through app/js/api.js: what to order, from whom and by when, fitted
// to each supplier's ordering terms. It is fetched only while the view
// is active — on view activation, on the Refresh button
// ([data-testid="orders-refresh"]) and when the horizon select
// ([data-testid="orders-horizon"], "7 days" (the default), "14 days"
// or "30 days") changes. A request-sequence guard renders only the
// newest answer, and a skeleton shows while a fetch is out.
//
// The summary ([data-testid="orders-summary"]) reads "<line_count>
// lines (a count of exactly 1 reads "1 line") · <total_value as
// whole dollars> across <n> suppliers", where
// n counts the groups with a non-null supplier_code. One block per
// supplier group ([data-testid="orders-supplier"]), in the answer's
// order: a heading with the supplier's name (the supplier_code when
// the name is null) and its vendor_code, "<line_count> lines (a
// count of exactly 1 reads "1 line") · <total_value>", and the
// lines' table ([data-testid="orders-lines"])
// with the columns Item and Site, Due ("Now" when due_in_days is 0,
// else "in <n> days"), Order by, Arrives, Quantity (the fitted
// quantity, a note per adjustment — "raised to minimum", "rounded to
// lot", "capped at maximum" — and "short of need" on a line whose
// short_of_need is true, which also carries data-short="true"), Value
// (whole dollars, an em dash when null) and Why (the arithmetic, as
// text — and, after it, the line's supplier reason when the plan
// carries one, see below). The group without supplier terms (null
// supplier_code) is headed "No supplier terms" and comes last, as
// the answer orders it.
//
// With no lines the screen reads "Nothing to order in the next <n>
// days."; a failed fetch shows "The order plan could not be loaded."
// without a page error. Every API value is inserted as text, never as
// markup. Sign-out resets the view to its first-load state.
//
// The release control (admin only): the admin role is the shared
// Auth identity's, read without the screen's own request — the shell
// gates [data-admin-only] elements from the sign-in's
// /api/auth/me answer (the same roles check the Work queue and Push
// to ERP run), and the screen keeps a zero-size [data-admin-only]
// probe in its first-load render until the sign-in's role lands on
// it; an identity that is not resolved before the release control
// renders fails closed (no control at all). For a resolved admin,
// each supplier block whose group source_system is "acumatica"
// gains a "Release to Acumatica" button
// ([data-testid="orders-release"]); a block whose source_system is
// "odoo" shows instead a note
// ([data-testid="orders-release-note"]) reading "Odoo lines are
// released through Odoo draft purchase orders."; the null-supplier
// block shows neither, and a non-admin sees neither either. A
// block whose supplier_code is blank shows no control at all:
// its Release button could not name the supplier, and a button
// that could not send would be dead.
//
// Release preview: the button POSTs /api/supply-plan/release
// through app/js/api.js with {"horizon_days": <the screen's
// horizon>, "supplier_codes": [<that block's supplier_code>]}
// (the block's ticked top-ups as "top_ups", the chosen top-ups'
// note) and no confirm; the preview's request body is kept with the
// block's release state, and the answer's panel
// ([data-testid="orders-release-panel"]) lists each returned
// group's site, its line count and its total (whole dollars), each
// unreleasable line's item and reason, and the "Confirm release"
// ([data-testid="orders-release-confirm"]) and "Cancel"
// ([data-testid="orders-release-cancel"]) buttons. With no groups
// it reads "Nothing to release for this supplier." and has no
// Confirm. Confirm sends the preview's own body with "confirm":
// true added — whatever has been ticked, unticked or chosen since
// the preview, nothing else in the body changes — and the panel
// then shows each group's outcome
// ([data-testid="orders-release-outcome"]): "created" reads "Draft
// PO <order_nbr> created on hold", "exists" "Already released as
// <order_nbr>", "error" "Failed: <error>". Both buttons are
// disabled while a request is out, so a double click sends one
// request; Cancel closes the panel and sends nothing. Ticking or
// unticking one of the block's top-ups closes the block's release
// the same way — the panel goes, no request is sent — whether the
// panel is open or the preview's request is still out, in which
// case the preview's answer is dropped when it lands and the admin
// previews again; choosing another horizon does it for every block,
// and a tick in one block leaves the other blocks' panels open. A
// confirm in flight holds the block still: from the click on its
// Confirm button until the confirm answers, the horizon select,
// the Refresh button and every top-up checkbox of that block are
// disabled, and their change and click handlers do nothing — a
// preview in flight disables none of them (the block's busy flag
// covers the preview, so the confirm carries its own marker), the
// other blocks' top-up checkboxes stay live and a tick in one
// never touches the block that is confirming, and when the confirm
// answers the block shows its outcome or error exactly as before,
// and the controls are enabled again. A
// 403 shows
// "Only an admin can release orders.", a 409 or 503 the server's
// detail, any other failure "The release could not be completed." —
// each in [data-testid="orders-release-error"], and none changes
// the plan view, which is never refetched by the release. The
// per-supplier panel state lives in module state
// (state.release[supplier_code]) and survives a full re-render.
//
// The transfer suggestions section (issue #856): below the last
// supplier block, a section ([data-testid="orders-transfers"])
// headed "Transfer suggestions" holding the "Show transfer
// suggestions" button ([data-testid="orders-transfers-show"]), for
// every signed-in user, admin or not. The section sends
// GET /api/supply-plan/transfers through app/js/api.js only when
// that button is clicked — activating the view, the Refresh button
// and the horizon select send no transfers request — and once an
// answer has landed the button reads "Refresh transfer
// suggestions". The answer's summary
// ([data-testid="orders-transfers-summary"]) reads "<count>
// transfers · <total_value as whole dollars>"; the table
// ([data-testid="orders-transfers-lines"]) has one row per
// suggestion in the answer's order, with the columns Item, From,
// To, Quantity, Value (whole dollars, an em dash when null — the
// route's decimal string read as a number, the way the release
// groups' value is read) and Why (the arithmetic, as text). With
// no suggestions the section reads "No transfers suggested: no
// site holds surplus another site needs."; a failed request shows
// "Transfer suggestions could not be loaded." in
// [data-testid="orders-transfers-error"] without a page error and
// the supplier blocks unchanged. The section's answer, error and
// pending flag live in module state and survive a full re-render
// of #orders-root; sign-out clears them and an answer arriving
// after sign-out is discarded.
//
// The transfer marks (issue #862): once a transfers answer has
// landed, every plan line whose item_code and site_code equal a
// suggestion's item_code and to_site carries data-transfer="true"
// on its row and a note
// ([data-testid="orders-line-transfer"]) in its Quantity cell — one
// "transfer of <quantity> from site <from_site>" per matching
// suggestion, in the answer's order, joined by "; ", then " could
// cover this" when the suggestions' total stands at or above the
// line's quantity, else " covers part of this". Before an answer
// lands, while a transfers refresh is out the door, and on an
// empty or failed answer, no row is marked; the marks re-derive
// from module state on every render — a horizon change or a
// Refresh re-applies them with no new transfers request — and a
// later answer replaces them. They are notes only: no quantity,
// total or request body changes.
//
// The transfer release control (issue #883): admin only — the same
// role read the supplier release uses, the shared Auth identity's,
// off the screen's probe, failing closed (no control at all for a
// non-admin or an unresolved identity). For a resolved admin, the
// transfers table always gains a last column headed "Release",
// whatever the rows: every row whose suggestion source_system is
// "acumatica" carries a "Release" button
// ([data-testid="orders-transfer-release"]) in that cell, every
// other row's cell is empty, and every other column and cell reads
// as before. The button POSTs /api/supply-plan/transfers/release
// with exactly {"transfers": [{"item_code", "from_site",
// "to_site"}]} for that row — no quantity, no confirm — and the
// answer's panel ([data-testid="orders-transfer-panel"], below the
// table) lists each returned group's "<from_site> → <to_site>", its
// line count and each line's item and quantity, each unreleasable
// entry's item and reason, and the "Confirm transfer"
// ([data-testid="orders-transfer-confirm"]) and "Cancel"
// ([data-testid="orders-transfer-cancel"]) buttons. With no groups
// it reads "Nothing to release for this suggestion." and has no
// Confirm. Only one panel is open at a time; a newer preview
// replaces it. Confirm resends the same body with "confirm": true
// and the panel then shows each group's outcome
// ([data-testid="orders-transfer-outcome"]): "created" reads
// "Transfer <reference_nbr> created on hold", "exists" "Already
// released as <reference_nbr>", "error" "Failed: <error>". Both
// buttons are disabled while a request is out, so a double click
// sends one request; Cancel closes the panel and sends nothing. A
// 403 shows "Only an admin can release transfers.", a 409 or 503
// the server's detail, any other failure "The transfer could not be
// released." — each in [data-testid="orders-transfer-error"], and
// none changes the plan or the transfers table, which the release
// never refetches. The panel's state is module state (one panel at
// a time) and survives a full re-render; sign-out clears it and an
// answer arriving after sign-out is discarded.
//
// The "Download CSV" button (issue #876): in the bar, next to
// Refresh ([data-testid="orders-download"]), shown only while a
// plan answer with at least one line is loaded — hidden on the
// first-load skeleton, on the empty note, on the load error and
// while a refresh is out the door — and enabled for every
// signed-in user, admin or not. A click builds the file from the
// loaded plan answer (no request of any kind) and saves it through
// a Blob and an object URL as
// "order-plan-<as_of>-<horizon_days>d.csv". The header row is
// "Supplier,Vendor code,Supplier code,Item,Site,Due in days,Order
// by,Arrives,Quantity,Unit cost,Value,Short of need,Why"; one row
// per plan line, in the screen's order, the group without supplier
// terms last. The supplier is the group's name, else its supplier
// code, else "No supplier terms"; "Short of need" reads "yes" or
// "no"; a null number is an empty cell and "Why" is the line's
// arithmetic (an empty cell when null). Fields are
// comma-separated and rows end with CRLF; a field containing a
// comma, a double quote, a CR or an LF is wrapped in double quotes
// with inner double quotes doubled; a text field starting with
// "=", "+", "-", "@", a tab or a CR gets a leading single quote so
// a spreadsheet never runs it as a formula; numbers are plain
// numbers without thousands separators.
//
// In the supplier blocks, the Due column reads "in 1 day" for a
// due of 1 day (it read "in 1 days"); "Now" for 0 and "in <n>
// days" otherwise.
//
// The supplier risk badge (issue #1060): whenever the screen
// fetches GET /api/supply-plan it sends one GET
// /api/supplier-risk beside it, through app/js/api.js, under its
// own request-sequence guard — a late answer from an older fetch or
// from before sign-out is dropped, and sign-out clears it. The
// supply-plan read, its order and its count are unchanged. In a
// supplier block's head (.op-supplier-head), after the title and
// the vendor code, a span ([data-testid="orders-supplier-risk"],
// class op-supplier-risk) shows when the risk answer rates that
// block's supplier_code "high" or "medium": it reads "High risk"
// with the class is-high or "Medium risk" with is-medium, and its
// title lists the true signs — Late deliveries, Variable lead
// times, Only supplier of <N> item(s), the word item when N is 1 —
// joined by a comma and a space. A supplier rated "low", one
// missing from the answer, the block without supplier terms, a
// failed or malformed risk answer, and the time before the answer
// arrives all show no badge; the block's title, vendor code,
// totals and lines are exactly as before. Every value is escaped,
// and the screen sends no new POST, PUT or DELETE (the release flow
// is untouched).
//
// The supplier reason (issue #1077): a plan line whose
// supplier_reason is a non-empty string shows it in its Why cell
// (.op-cell-why), after the arithmetic text — one element
// ([data-testid="orders-line-supplier-reason"], class
// op-supplier-reason) holding the reason, escaped. A line whose
// supplier_reason is missing, null, empty or not a string shows no
// such element, and its Why cell reads exactly the arithmetic, as
// before. Every other cell, the supplier blocks, the risk badges,
// the release flow and its requests are unchanged, and the screen
// sends no new request — no new POST, PUT or DELETE.
//
// The buffer source badge (issue #1542): a plan line whose
// source is exactly the string "buffer" shows, in its Item
// and Site cell (.op-cell-pair), right after the item and
// site text, one element ([data-testid="orders-line-source"],
// class op-line-source) reading "Buffer" with the title
// "Planned from its DDMRP buffer's order signal". A line
// whose source is anything else — "reorder_point",
// missing, null or another value — shows no such element,
// and its cell reads exactly as before. Every other cell,
// the Why text, the supplier blocks, the CSV download, the
// release flow and the screen's requests are unchanged, and
// the screen sends no new request.
//
// The supplier minimum line and the short badge (issue #1167): a
// supplier block whose group carries a `minimum` that is a usable
// number (a number or a numeric string) shows, right after its
// totals line, a line ([data-testid="orders-supplier-minimum"],
// class op-supplier-minimum) reading "Supplier minimum
// <minimum>", dollars and cents with thousands separators; a group
// without a usable minimum — and the block without supplier terms —
// shows no line and renders exactly as before. When the group's
// `shortfall` is a usable number above 0, the block's head
// (.op-supplier-head) also shows, after the risk badge, a span
// ([data-testid="orders-supplier-short"], class op-supplier-short)
// reading "Below minimum by <shortfall>", dollars and cents; a
// shortfall of 0, a missing or unusable shortfall, and the block
// without supplier terms show no badge. Every value is inserted as
// text.
//
// The suggested top-ups (issue #1167): when a valid plan answer
// lands with at least one block whose `shortfall` is a usable
// number above 0, the screen sends exactly one GET
// /api/supply-plan/top-ups?horizon_days=<n> through app/js/api.js,
// where n is the horizon that plan read asked for, under its own
// request-sequence guard: an answer that lands after a newer plan
// read (a refresh or a horizon change) or after sign-out is
// dropped, and sign-out clears the stored answer. A plan answer
// with no such block sends no top-ups read. Each block with a
// shortfall above 0 ends, after its lines' table, with a section
// ([data-testid="orders-top-ups"], class op-top-ups) headed
// "Suggested top-ups": while the read is out it reads "Looking for
// top-ups…"; a failed read or a malformed answer (anything but an
// object with a suppliers array) makes it read "Top-ups could not
// be loaded." without a page error; otherwise it shows the answer's
// entry whose supplier_code and source_system equal the block's, as
// a table ([data-testid="orders-top-ups-lines"]) with the columns
// Item and Site, Pulled forward, Order by, Quantity, Value and
// Holding cost, one row ([data-testid="orders-top-up"]) per entry
// of its `top_ups` in the answer's order (the item and site as the
// plan's lines show them, the top-up's `due_in_days` as "1 day" or
// "<n> days", `order_by`, `quantity` in whole units, and `value`
// and `holding_cost` in dollars and cents), then a line
// ([data-testid="orders-top-ups-outcome"]) reading "Adding these
// reaches the minimum." when `reaches_minimum` is true, "Still
// short by <still_short in dollars and cents> after these." when it
// is false and the entry has top-ups, and "Nothing due in the next
// <lookahead_days> days can top this order up." when the entry has
// none; a short block with no entry in the answer reads "No
// top-ups suggested." When the entry carries a `suggestion` of
// exactly "wait", "add_top_ups" or "order_anyway" and a string
// `suggestion_reason`, one line
// ([data-testid="orders-top-ups-suggestion"], class
// op-top-ups-suggestion) reads, right after the section's heading
// and before its table, "Suggested: <label>.
// <suggestion_reason>" — the label "Wait", "Add the top-ups" or
// "Order anyway" in a strong element of class
// op-top-ups-suggestion-label; an entry without both fields, or
// with any other suggestion, shows no such line, and its section
// reads exactly as before. Every value is inserted as text. The
// plan
// read and the supplier-risk read, their order and their counts,
// the plan's lines, the release flow and the CSV download are
// unchanged, and the screen sends no new POST, PUT or DELETE.
// The chosen top-ups (issue #1205): for an admin — the same shared
// Auth identity's role the release control reads, failing closed
// like it — each row of a block's top-ups table
// ([data-testid="orders-top-up"]) starts with a checkbox
// ([data-testid="orders-top-up-choose"]), labelled for assistive
// technology "Add <item_code> at <site_code> to this order",
// unticked at first; a non-admin's table carries no checkbox and no
// extra column, exactly as before. The ticked rows are kept in
// module state beside the block's release state, by the block's
// supplier_code and each row's item_code and site_code: they
// survive a full re-render, and they clear when a new plan answer
// or a new top-ups answer lands — one the screen does not already
// hold, for the suggestions may have changed (an answer the screen
// already holds, as a view re-activation re-reads, moves nothing) —
// and on sign-out. While at least one row of a block is ticked, a
// line ([data-testid="orders-top-ups-chosen"]) follows the outcome
// line: "With the chosen top-ups: <total>", the block's total_value
// plus the ticked rows' value in dollars and cents, continuing " —
// meets the minimum." when the total stands at or above the
// block's minimum, else " — still short by <minimum minus total>."
// — and with nothing ticked it is absent. The release control's
// preview adds the block's ticked rows to the body as "top_ups", a
// list of {"supplier_code", "item_code", "site_code"} in the
// table's order; with no row ticked the body is byte-for-byte what
// it was, with no top_ups key, and "Confirm release" sends the
// preview's own body — kept with the block's release state — with
// "confirm": true added, whatever has moved since. Ticking or
// unticking one of the block's top-ups, or choosing another horizon
// (every block then), closes the block's release the way "Cancel"
// does, with no request. The preview panel shows the
// returned groups' lines and each unreleasable entry's item and
// reason exactly as before, so a kept top-up appears among the
// group's lines and a top-up the suggestions no longer hold reads
// "not a suggested top-up". The plan read, the supplier-risk read
// and the top-ups read, with their order and their counts, the CSV
// download, the transfer suggestions and their release, and every
// other request are unchanged.
(function (global) {
  'use strict';

  var LIST_PATH = '/api/supply-plan';
  var RISK_PATH = '/api/supplier-risk';
  var ROOT_ID = 'orders-root';
  var VIEW_ID = 'view-orders';
  var APP_ID = 'app';

  var EM_DASH = '\u2014';
  var MIDDLE_DOT = '\u00b7';
  var RELEASE_PATH = '/api/supply-plan/release';

  var GENERIC_ERROR = 'The order plan could not be loaded.';
  var NO_TERMS_HEADING = 'No supplier terms';
  var SHORT_NOTE = 'short of need';
  var RELEASE_BUTTON_LABEL = 'Release to Acumatica';
  var RELEASE_403 = 'Only an admin can release orders.';
  var RELEASE_FAILED = 'The release could not be completed.';
  var NOTHING_TO_RELEASE = 'Nothing to release for this supplier.';
  var ODOO_NOTE = 'Odoo lines are released through Odoo draft purchase orders.';

  var TRANSFERS_PATH = '/api/supply-plan/transfers';
  var TRANSFERS_TITLE = 'Transfer suggestions';
  var TRANSFERS_SHOW_LABEL = 'Show transfer suggestions';
  var TRANSFERS_REFRESH_LABEL = 'Refresh transfer suggestions';
  var TRANSFERS_FAILED = 'Transfer suggestions could not be loaded.';
  var TRANSFERS_EMPTY = 'No transfers suggested: no site holds surplus another site needs.';
  var TRANSFER_RELEASE_PATH = '/api/supply-plan/transfers/release';
  var TRANSFER_RELEASE_403 = 'Only an admin can release transfers.';
  var TRANSFER_RELEASE_FAILED = 'The transfer could not be released.';
  var NOTHING_TO_TRANSFER_RELEASE = 'Nothing to release for this suggestion.';

  var TOP_UPS_PATH = '/api/supply-plan/top-ups';
  var TOP_UPS_TITLE = 'Suggested top-ups';
  var TOP_UPS_LOOKING = 'Looking for top-ups\u2026';
  var TOP_UPS_FAILED = 'Top-ups could not be loaded.';
  var TOP_UPS_NONE = 'No top-ups suggested.';
  var TOP_UPS_REACHES = 'Adding these reaches the minimum.';
  // The suggestion tokens an entry can carry, and the label each
  // one renders; any other suggestion carries no line. A
  // null-prototype table, so a reserved token ("__proto__") is a
  // miss, never a prototype read.
  var TOP_UPS_SUGGESTIONS = Object.create(null);
  TOP_UPS_SUGGESTIONS.wait = 'Wait';
  TOP_UPS_SUGGESTIONS.add_top_ups = 'Add the top-ups';
  TOP_UPS_SUGGESTIONS.order_anyway = 'Order anyway';

  var DOWNLOAD_LABEL = 'Download CSV';
  var CSV_HEADER =
    'Supplier,Vendor code,Supplier code,Item,Site,Due in days,Order by,Arrives,Quantity,Unit cost,Value,Short of need,Why';

  // The shared Auth identity's role, as resolved by the shell's
  // sign-in: only a positive /api/auth/me answer with "admin" in the
  // user's roles enables the release control; anything else — and an
  // identity not resolved yet — fails closed.
  var ROLE_UNRESOLVED = 'unresolved';
  var ROLE_ADMIN = 'admin';
  var ROLE_OTHER = 'other';

  // The outcome tokens the release answer carries, and the outcome
  // line each one renders.
  var OUTCOME_CLASS = {
    created: 'op-release-outcome--created',
    exists: 'op-release-outcome--exists',
    error: 'op-release-outcome--error',
  };

  // The outcome tokens the transfer release answer carries, and the
  // outcome line each one renders.
  var TRANSFER_OUTCOME_CLASS = {
    created: 'op-transfer-outcome--created',
    exists: 'op-transfer-outcome--exists',
    error: 'op-transfer-outcome--error',
  };

  // The adjustment tokens the fitting step can raise, and the note each
  // one renders. An unknown token renders no note.
  var ADJUSTMENT_NOTES = {
    raised_to_minimum: 'raised to minimum',
    rounded_to_lot: 'rounded to lot',
    capped_at_maximum: 'capped at maximum',
  };

  // The horizons the select offers, in display order; 7 days is the
  // default and matches the server's own default.
  var HORIZONS = [
    { days: 7, label: '7 days' },
    { days: 14, label: '14 days' },
    { days: 30, label: '30 days' },
  ];
  var DEFAULT_HORIZON_DAYS = 7;

  var rootEl = null;

  var state = {
    data: null, // the newest successful answer
    loading: false, // a fetch is out
    error: null, // the newest failed load: { status, message }
    horizonDays: DEFAULT_HORIZON_DAYS,
    role: ROLE_UNRESOLVED, // the shared Auth identity's role
    // supplier_code -> that block's release control state; the table is
    // null-prototype, so a reserved supplier_code ("__proto__",
    // "constructor") is a key of the table, never a read of (or a
    // write onto) the table's own prototype
    release: Object.create(null),
    // supplier_code -> that block's chosen top-ups, beside the
    // block's release state: a null-prototype table keyed by each
    // ticked row's item_code and site_code (topUpKey) — the rows the
    // admin ticked in the block's top-ups table, surviving a full
    // re-render
    chosenTopUps: Object.create(null),
    transfers: null, // the newest successful transfers answer
    transfersLoading: false, // a transfers fetch is out
    transfersError: null, // the newest failed transfers request's message
    transferRelease: null, // the open transfer release panel (one at a time)
    risk: null, // the newest valid supplier-risk answer
    riskLoading: false, // a risk fetch is out
    topUps: null, // the newest valid top-ups answer
    topUpsLoading: false, // a top-ups fetch is out
    topUpsError: null, // the newest failed top-ups read's message
  };

  // The request-sequence guard: only the newest fetch's answer renders.
  var seq = 0;
  // The release generation: a reset (sign-out) invalidates a release
  // answer still out the door, so it can neither render into the
  // fresh session's state nor flip its buttons.
  var releaseGen = 0;
  // The transfers sequence: a reset (sign-out) invalidates a
  // transfers answer still out the door, so it can neither render
  // into the fresh session's state nor flip its button.
  var transfersSeq = 0;
  // The supplier-risk sequence: a reset (sign-out) or a newer plan
  // read invalidates a risk answer still out the door, so it can
  // neither render into the fresh state nor badge a stale block.
  var riskSeq = 0;
  // The top-ups sequence: a reset (sign-out) or a newer plan read
  // invalidates a top-ups answer still out the door, so it can
  // neither render into the fresh state nor table a stale block.
  var topUpsSeq = 0;
  // The last valid top-ups answer that settled, kept beside the
  // stored answer (which a new plan cycle clears before the next
  // answer lands): an answer the screen already holds has moved no
  // suggestions, and the chosen top-ups stay with it.
  var topUpsPrev = null;
  // The transfer release generation: a reset (sign-out) invalidates
  // a transfer release answer still out the door, so it can neither
  // render into the fresh session's state nor flip its buttons.
  var transferReleaseGen = 0;

  /* ------------------------------ helpers ------------------------------ */

  var esc = window.Shared.esc;

  function isNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }

  // A usable finite number, or NaN: a numeric string ("3", "0") counts
  // as its number; null, blank or non-numeric values never do.
  function asNumber(value) {
    if (typeof value === 'number') {
      return isFinite(value) ? value : NaN;
    }
    if (typeof value === 'string') {
      var text = value.trim();
      if (text === '') {
        return NaN;
      }
      var parsed = Number(text);
      return isFinite(parsed) ? parsed : NaN;
    }
    return NaN;
  }

  // Whole dollars with separators (18720.4 -> "$18,720"); an em dash
  // when the server carries no usable figure.
  function wholeDollars(value) {
    if (typeof value === 'string') {
      value = value === '' ? NaN : Number(value);
    }
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return '$' + Math.round(value).toLocaleString('en-US');
  }

  // Whole units (12 -> "12"); an em dash without a usable figure.
  function wholeNumber(value) {
    if (typeof value === 'string') {
      value = value === '' ? NaN : Number(value);
    }
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return Math.round(value).toLocaleString('en-US');
  }

  // Dollars and cents with separators (2500 -> "$2,500.00"); an
  // em dash when the server carries no usable figure.
  function dollarsCents(value) {
    var number = asNumber(value);
    if (isNaN(number)) {
      return EM_DASH;
    }
    return (
      '$' +
      number.toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    );
  }

  // The word for a plan line count: a count of exactly 1 reads
  // "line", every other count reads "lines". Numeric strings coerce
  // to their number first (asNumber), and a count without a usable
  // figure keeps the plural.
  function linesWord(count) {
    return asNumber(count) === 1 ? 'line' : 'lines';
  }

  function nonEmptyString(value) {
    return typeof value === 'string' && value !== '' ? value : null;
  }

  // "item / site", keeping whatever the line carries; an em dash when
  // it carries nothing at all.
  function pairText(itemCode, siteCode) {
    var item = nonEmptyString(itemCode);
    var site = nonEmptyString(siteCode);
    if (item && site) {
      return item + ' / ' + site;
    }
    return item || site || EM_DASH;
  }

  // The Due cell: "Now" at 0, "in 1 day" at 1, else "in <n> days".
  // The days are Math.round(asNumber(...)), so a numeric string
  // ("3") carries the same meaning as its number.
  function dueText(dueInDays) {
    var days = Math.round(asNumber(dueInDays));
    if (isNaN(days)) {
      return EM_DASH;
    }
    if (days === 0) {
      return 'Now';
    }
    if (days === 1) {
      return 'in 1 day';
    }
    return 'in ' + days + ' days';
  }

  function dateText(value) {
    return nonEmptyString(value) || EM_DASH;
  }

  function groupsOf(data) {
    return data && Array.isArray(data.suppliers) ? data.suppliers : [];
  }

  function groupLines(group) {
    return group && Array.isArray(group.lines) ? group.lines : [];
  }

  // The groups the summary counts: a supplier_code the answer carries
  // — anything but a null. An empty string is a carried code: it
  // counts, and it is not the no-terms group; only a null code marks
  // the group without supplier terms.
  function hasSupplier(group) {
    return group.supplier_code !== null && group.supplier_code !== undefined;
  }

  // A well-formed answer: an object (never an array or a bare value)
  // with a suppliers array. Anything else is a failed load.
  function answerValid(payload) {
    return (
      payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      Array.isArray(payload.suppliers)
    );
  }

  /* ------------------------------ loading ------------------------------ */

  function load() {
    seq += 1;
    var mySeq = seq;
    state.loading = true;
    state.error = null;
    render();
    global.Api.get(LIST_PATH + '?horizon_days=' + encodeURIComponent(state.horizonDays)).then(
      function (payload) {
        if (mySeq !== seq) {
          return; // a newer fetch is out: this answer is stale
        }
        state.loading = false;
        if (!answerValid(payload)) {
          state.error = { status: 0, message: GENERIC_ERROR };
          abortTopUps(); // a failed plan read carries no top-ups
        } else {
          // A new plan answer — one the screen does not already
          // hold — may have moved the suggestions: the chosen
          // top-ups, keyed to the held answer's rows, clear with
          // it. An answer the screen already holds (a view
          // re-activation's re-read) moves nothing.
          var planMoved = JSON.stringify(payload) !== JSON.stringify(state.data);
          state.data = payload;
          if (planMoved) {
            clearChosenTopUps();
          }
          resetTopUpsForPlan(payload); // the top-ups read, its own guard
        }
        render();
      },
      function (error) {
        if (mySeq !== seq) {
          return;
        }
        state.loading = false;
        state.error = {
          status: error && typeof error.status === 'number' ? error.status : 0,
          message: GENERIC_ERROR,
        };
        abortTopUps(); // a failed plan read carries no top-ups
        render();
      }
    );
    loadRisk(); // one risk read beside the plan read, its own guard
  }

  /* --------------------------- supplier risk --------------------------- */

  // A well-formed risk answer: an object (never an array or a bare
  // value) with a suppliers array. Anything else is a failed read —
  // the blocks keep rendering, without badges.
  function riskAnswerValid(payload) {
    return (
      payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      Array.isArray(payload.suppliers)
    );
  }

  // The risk read: one GET /api/supplier-risk per plan read, under
  // its own request-sequence guard — a late answer from an older
  // read or from before sign-out is dropped. A failed or malformed
  // answer clears the stored answer (no badge), without a page
  // error; the plan itself is never touched by it.
  function loadRisk() {
    riskSeq += 1;
    var mySeq = riskSeq;
    state.riskLoading = true;
    global.Api.get(RISK_PATH).then(
      function (payload) {
        if (mySeq !== riskSeq) {
          return; // an older read or the sign-out: this answer is stale
        }
        state.riskLoading = false;
        state.risk = riskAnswerValid(payload) ? payload : null;
        render();
      },
      function () {
        if (mySeq !== riskSeq) {
          return;
        }
        state.riskLoading = false;
        state.risk = null;
        render();
      }
    );
  }

  // The entry's sole-source count as a whole number: the answer
  // carries it as a number or a numeric string; 0 when it carries no
  // usable figure.
  function riskSoleSourceItems(entry) {
    var items = entry.sole_source_items;
    if (typeof items === 'string') {
      items = items === '' ? NaN : Number(items);
    }
    if (!isNumber(items)) {
      return 0;
    }
    return Math.round(items);
  }

  // The entry's true signs in display order — the late sign, the
  // variable sign and the sole-source sign with its count (the word
  // item singular when the count is 1); an empty list with no sign.
  function riskSigns(entry) {
    var signs = [];
    if (entry.late === true) {
      signs.push('Late deliveries');
    }
    if (entry.variable === true) {
      signs.push('Variable lead times');
    }
    if (entry.sole_source === true) {
      var items = riskSoleSourceItems(entry);
      signs.push('Only supplier of ' + items + (items === 1 ? ' item' : ' items'));
    }
    return signs;
  }

  // The badge of one supplier block: nothing for the block without
  // supplier terms, before a valid risk answer has landed, for a
  // supplier missing from the answer or rated "low" — and "High
  // risk" (is-high) or "Medium risk" (is-medium) when the answer
  // rates the block's supplier_code high or medium, its title the
  // true signs joined by a comma and a space.
  function supplierRiskBadgeHtml(group) {
    if (!hasSupplier(group) || !riskAnswerValid(state.risk)) {
      return '';
    }
    var code = group.supplier_code;
    var suppliers = state.risk.suppliers;
    for (var i = 0; i < suppliers.length; i++) {
      var entry = suppliers[i];
      if (!entry || typeof entry !== 'object') {
        continue; // a malformed entry never breaks the blocks
      }
      var entryCode = entry.supplier_code;
      if (entryCode === null || entryCode === undefined) {
        continue;
      }
      if (String(entryCode) !== String(code)) {
        continue;
      }
      if (entry.risk !== 'high' && entry.risk !== 'medium') {
        return ''; // rated low (or unrateable): no badge
      }
      var level =
        entry.risk === 'high'
          ? { text: 'High risk', cls: 'is-high' }
          : { text: 'Medium risk', cls: 'is-medium' };
      return (
        '<span class="op-supplier-risk ' + level.cls + '" data-testid="orders-supplier-risk"' +
        ' title="' + esc(riskSigns(entry).join(', ')) + '">' +
        esc(level.text) +
        '</span>'
      );
    }
    return '';
  }

  // The group's usable minimum, or NaN: a number or a numeric
  // string; null, blank or non-numeric values never do.
  function groupMinimum(group) {
    return asNumber(group.minimum);
  }

  // The group's usable shortfall, or NaN: the same reading as the
  // minimum — a number or a numeric string.
  function groupShortfall(group) {
    return asNumber(group.shortfall);
  }

  // The minimum line of one supplier block: right after the
  // totals' line, "Supplier minimum <minimum in dollars and
  // cents>", only when the group carries a usable minimum —
  // otherwise nothing, and the block reads exactly as before. The
  // block without supplier terms never carries the line.
  function supplierMinimumLineHtml(group) {
    if (!hasSupplier(group)) {
      return '';
    }
    var minimum = groupMinimum(group);
    if (isNaN(minimum)) {
      return '';
    }
    return (
      '<p class="op-supplier-minimum" data-testid="orders-supplier-minimum">' +
      'Supplier minimum ' +
      esc(dollarsCents(minimum)) +
      '</p>'
    );
  }

  // The short badge of one supplier block: in the block's head,
  // after the risk badge, "Below minimum by <shortfall in dollars
  // and cents>" when the group's shortfall is a usable number above
  // 0 — a shortfall of 0, a missing or unusable shortfall, and the
  // block without supplier terms all show no badge, exactly as
  // before.
  function supplierShortBadgeHtml(group) {
    if (!hasSupplier(group)) {
      return '';
    }
    var shortfall = groupShortfall(group);
    if (isNaN(shortfall) || shortfall <= 0) {
      return '';
    }
    return (
      '<span class="op-supplier-short" data-testid="orders-supplier-short">' +
      'Below minimum by ' +
      esc(dollarsCents(shortfall)) +
      '</span>'
    );
  }

  /* ----------------------------- top-ups ------------------------------- */

  // A well-formed top-ups answer: an object (never an array or a
  // bare value) with a suppliers array. Anything else is a failed
  // read — the blocks keep rendering, without top-ups.
  function topUpsAnswerValid(payload) {
    return (
      payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      Array.isArray(payload.suppliers)
    );
  }

  // The plan answer carries a short block: at least one group whose
  // shortfall is a usable number above 0.
  function planHasShortBlock(data) {
    var groups = groupsOf(data);
    for (var i = 0; i < groups.length; i++) {
      var group = groups[i];
      if (!group || typeof group !== 'object') {
        continue; // a malformed group never breaks the screen
      }
      var shortfall = groupShortfall(group);
      if (!isNaN(shortfall) && shortfall > 0) {
        return true;
      }
    }
    return false;
  }

  // The top-ups read invalidates itself whenever the plan moves on:
  // a newer plan read (a refresh or a horizon change) or a sign-out
  // belongs to a different horizon or session, so the stored answer
  // — and any read still out the door — is dropped.
  function abortTopUps() {
    topUpsSeq += 1;
    state.topUps = null;
    state.topUpsLoading = false;
    state.topUpsError = null;
  }

  // The top-ups read: one GET
  // /api/supply-plan/top-ups?horizon_days=<n> through app/js/api.js,
  // sent only when a valid plan answer lands with at least one
  // short block, where n is the horizon that plan read asked for —
  // never on a plan answer without a short block. Under its own
  // request-sequence guard: a late answer from an older plan read
  // or from before sign-out is dropped. A failed or malformed
  // answer lands as the generic message, without a page error.
  function sendTopUps() {
    topUpsSeq += 1;
    var mySeq = topUpsSeq;
    state.topUpsLoading = true;
    state.topUpsError = null;
    global.Api.get(
      TOP_UPS_PATH + '?horizon_days=' + encodeURIComponent(state.horizonDays)
    ).then(
      function (payload) {
        if (mySeq !== topUpsSeq) {
          return; // an older plan read or the sign-out: this answer is stale
        }
        state.topUpsLoading = false;
        if (!topUpsAnswerValid(payload)) {
          state.topUps = null;
          state.topUpsError = TOP_UPS_FAILED;
        } else {
          // A new top-ups answer — one the screen does not already
          // hold — may have moved the suggestions: the chosen
          // top-ups clear with it. An answer the screen already
          // holds moves nothing.
          var topUpsMoved = JSON.stringify(payload) !== JSON.stringify(topUpsPrev);
          state.topUps = payload;
          topUpsPrev = payload;
          if (topUpsMoved) {
            clearChosenTopUps();
          }
        }
        render();
      },
      function () {
        if (mySeq !== topUpsSeq) {
          return;
        }
        state.topUpsLoading = false;
        state.topUps = null;
        state.topUpsError = TOP_UPS_FAILED;
        render();
      }
    );
  }

  // A landed plan answer re-runs the top-ups read: the older read —
  // and its answer — belong to a past horizon, and a plan without a
  // short block sends nothing at all.
  function resetTopUpsForPlan(payload) {
    abortTopUps();
    if (answerValid(payload) && planHasShortBlock(payload)) {
      sendTopUps();
    }
  }

  // The answer's entry for one block: the entry whose supplier_code
  // and source_system equal the block's; null when the answer
  // carries none.
  function topUpsEntryFor(group) {
    if (!hasSupplier(group) || !topUpsAnswerValid(state.topUps)) {
      return null;
    }
    var code = group.supplier_code;
    if (code === null || code === undefined) {
      return null;
    }
    var suppliers = state.topUps.suppliers;
    for (var i = 0; i < suppliers.length; i++) {
      var entry = suppliers[i];
      if (!entry || typeof entry !== 'object') {
        continue; // a malformed entry never breaks the blocks
      }
      var entryCode = entry.supplier_code;
      if (entryCode === null || entryCode === undefined) {
        continue;
      }
      if (String(entryCode) !== String(code)) {
        continue;
      }
      var entrySystem = entry.source_system;
      var groupSystem = group.source_system;
      var entryText =
        entrySystem === null || entrySystem === undefined ? null : String(entrySystem);
      var groupText =
        groupSystem === null || groupSystem === undefined ? null : String(groupSystem);
      if (entryText !== groupText) {
        continue;
      }
      return entry;
    }
    return null;
  }

  // The row's identity in the chosen table: the item code and the
  // site code as plain strings, joined with a unit separator a code
  // itself never carries. Null and undefined read as the empty
  // string, the way the row's data attributes do.
  function topUpKey(itemCode, siteCode) {
    var item = itemCode === null || itemCode === undefined ? '' : String(itemCode);
    var site = siteCode === null || siteCode === undefined ? '' : String(siteCode);
    return item + '\u0000' + site;
  }

  // The chosen top-ups, cleared: a new plan answer or a new
  // top-ups answer has landed (the suggestions may have changed),
  // or the session is over. A fresh null-prototype table, so a
  // reserved supplier_code is a key, never a prototype read.
  function clearChosenTopUps() {
    state.chosenTopUps = Object.create(null);
  }

  // The block's ticked rows of one entry, in the table's (the
  // answer's) order: the entry's top-ups whose item and site the
  // block's chosen table holds.
  function chosenTopUpsOf(group, entry) {
    var out = [];
    if (!hasSupplier(group) || !entry || typeof entry !== 'object') {
      return out;
    }
    var table = state.chosenTopUps[group.supplier_code];
    if (!table) {
      return out;
    }
    var topUps = Array.isArray(entry.top_ups) ? entry.top_ups : [];
    for (var i = 0; i < topUps.length; i++) {
      var topUp = topUps[i];
      if (!topUp || typeof topUp !== 'object') {
        continue; // a malformed top-up never breaks the line
      }
      if (table[topUpKey(topUp.item_code, topUp.site_code)]) {
        out.push(topUp);
      }
    }
    return out;
  }

  // The Pulled forward cell: the top-up's due_in_days as "1 day" at
  // 1, "<n> days" otherwise; an em dash without a usable figure.
  function pulledForwardText(dueInDays) {
    var days = Math.round(asNumber(dueInDays));
    if (isNaN(days)) {
      return EM_DASH;
    }
    return days === 1 ? '1 day' : days + ' days';
  }

  // The row's marker for an admin: the row's checkbox, at the head
  // of the row's first cell, labelled for assistive technology
  // "Add <item_code> at <site_code> to this order", unticked at
  // first — ticked when the block's chosen table holds the row's
  // item and site. The markers carry the row's item and site, read
  // back as strings. A non-admin's row carries no checkbox and
  // reads exactly as before.
  function topUpsChooseHtml(group, topUp) {
    var item = nonEmptyString(topUp.item_code) || '';
    var site = nonEmptyString(topUp.site_code) || '';
    var label =
      'Add ' +
      (item !== '' ? item : EM_DASH) +
      ' at ' +
      (site !== '' ? site : EM_DASH) +
      ' to this order';
    var table = state.chosenTopUps[group.supplier_code];
    var checked = !!(table && table[topUpKey(item, site)]);
    // While the block's own confirm is out the door the checkbox
    // holds still; the other blocks' stay live (their marker is
    // not set).
    var held = blockConfirming(String(group.supplier_code));
    return (
      '<input type="checkbox" class="op-top-up-choose" data-testid="orders-top-up-choose"' +
      ' data-item="' + esc(item) + '" data-site="' + esc(site) + '"' +
      ' aria-label="' + esc(label) + '"' +
      (checked ? ' checked' : '') +
      (held ? ' disabled' : '') +
      '>'
    );
  }

  // The entry's rows, in the answer's order: the item and site as
  // the plan's lines show them, the pulled-forward days, the
  // order-by date, the quantity in whole units, the value and the
  // holding cost in dollars and cents — and, for an admin (the
  // same role the release control reads, failing closed like it),
  // the choose checkbox at the head of the row's first cell, so
  // the row starts with it and a non-admin's table carries no
  // checkbox and no extra column, exactly as before.
  function topUpsTableHtml(group, entry) {
    var admin = state.role === ROLE_ADMIN;
    var topUps = Array.isArray(entry.top_ups) ? entry.top_ups : [];
    var rows = [];
    for (var i = 0; i < topUps.length; i++) {
      var topUp = topUps[i];
      if (!topUp || typeof topUp !== 'object') {
        continue; // a malformed top-up never breaks the table
      }
      rows.push(
        '<tr class="op-row" data-testid="orders-top-up">' +
        '<td class="op-cell-pair">' +
        (admin ? topUpsChooseHtml(group, topUp) : '') +
        esc(pairText(topUp.item_code, topUp.site_code)) +
        '</td>' +
        '<td class="op-cell-due">' + esc(pulledForwardText(topUp.due_in_days)) + '</td>' +
        '<td class="op-cell-date">' + esc(dateText(topUp.order_by)) + '</td>' +
        '<td class="op-cell-qty">' + esc(wholeNumber(topUp.quantity)) + '</td>' +
        '<td class="op-cell-money">' + esc(dollarsCents(topUp.value)) + '</td>' +
        '<td class="op-cell-money">' + esc(dollarsCents(topUp.holding_cost)) + '</td>' +
        '</tr>'
      );
    }
    return (
      '<div class="op-top-ups-region">' +
      '<table class="op-table op-top-ups-table" data-testid="orders-top-ups-lines">' +
      '<thead><tr>' +
      '<th scope="col">Item and Site</th>' +
      '<th scope="col">Pulled forward</th>' +
      '<th scope="col">Order by</th>' +
      '<th scope="col">Quantity</th>' +
      '<th scope="col">Value</th>' +
      '<th scope="col">Holding cost</th>' +
      '</tr></thead>' +
      '<tbody>' +
      rows.join('') +
      '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  // The running line under the outcome, while at least one row of
  // the block is ticked: "With the chosen top-ups: <total>", the
  // block's total_value plus the ticked rows' value in dollars and
  // cents, continuing " \u2014 meets the minimum." when the total
  // stands at or above the block's minimum, else " \u2014 still
  // short by <minimum minus total>." — and nothing with no row
  // ticked.
  function topUpsChosenLineHtml(group, entry) {
    var chosen = chosenTopUpsOf(group, entry);
    if (chosen.length === 0) {
      return ''; // nothing ticked: no line
    }
    var total = asNumber(group.total_value);
    for (var i = 0; i < chosen.length; i++) {
      var value = asNumber(chosen[i].value);
      if (!isNaN(value)) {
        total += value;
      }
    }
    var text = 'With the chosen top-ups: ' + dollarsCents(total);
    var minimum = groupMinimum(group);
    if (!isNaN(total) && !isNaN(minimum)) {
      text +=
        total >= minimum
          ? ' ' + EM_DASH + ' meets the minimum.'
          : ' ' + EM_DASH + ' still short by ' + dollarsCents(minimum - total) + '.';
    }
    return (
      '<p class="op-top-ups-chosen" data-testid="orders-top-ups-chosen">' +
      esc(text) +
      '</p>'
    );
  }

  // The outcome line under the entry's rows: the minimum is
  // reached, the remaining shortfall in dollars and cents, or —
  // with no top-up due at all — the answer's lookahead in days.
  function topUpsOutcomeHtml(entry) {
    var topUps = Array.isArray(entry.top_ups) ? entry.top_ups : [];
    var text;
    if (entry.reaches_minimum === true) {
      text = TOP_UPS_REACHES;
    } else if (topUps.length > 0) {
      text = 'Still short by ' + dollarsCents(entry.still_short) + ' after these.';
    } else {
      text =
        'Nothing due in the next ' +
        wholeNumber(state.topUps.lookahead_days) +
        ' days can top this order up.';
    }
    return (
      '<p class="op-top-ups-outcome" data-testid="orders-top-ups-outcome">' +
      esc(text) +
      '</p>'
    );
  }

  // The suggestion line, right after the section's heading and
  // before its table: "Suggested: <label>.
  // <suggestion_reason>", the label in a strong of class
  // op-top-ups-suggestion-label — when the entry carries a
  // suggestion of exactly "wait", "add_top_ups" or
  // "order_anyway" and a string suggestion_reason. Any other
  // entry — a missing or non-string reason, any other
  // suggestion, no suggestion at all — shows no line, and the
  // section reads exactly as before. Every value is inserted as
  // text.
  function topUpsSuggestionHtml(entry) {
    var suggestion = entry.suggestion;
    if (typeof suggestion !== 'string') {
      return ''; // no usable suggestion: no line
    }
    var label = TOP_UPS_SUGGESTIONS[suggestion];
    if (label === undefined || typeof entry.suggestion_reason !== 'string') {
      return ''; // an unknown suggestion or a missing reason: no line
    }
    return (
      '<p class="op-top-ups-suggestion" data-testid="orders-top-ups-suggestion">' +
      'Suggested: <strong class="op-top-ups-suggestion-label">' +
      esc(label) +
      '</strong>. ' +
      esc(entry.suggestion_reason) +
      '</p>'
    );
  }

  // The top-ups section of one short block: after the lines' table,
  // the "Suggested top-ups" heading and — while the read is out,
  // the looking line; on a failed or malformed read, the failure
  // line; otherwise the block's entry as a table and its outcome,
  // or the "no top-ups" line when the answer carries none for it.
  function topUpsSectionHtml(group) {
    if (!hasSupplier(group)) {
      return ''; // the block without supplier terms never carries it
    }
    var shortfall = groupShortfall(group);
    if (isNaN(shortfall) || shortfall <= 0) {
      return ''; // not short: no section, exactly as before
    }
    // The section carries the block's supplier code so a checkbox
    // change reads its block back, the way the release control
    // reads its own.
    var html =
      '<section class="op-top-ups" data-testid="orders-top-ups"' +
      ' data-supplier-code="' + esc(group.supplier_code) + '">' +
      '<h3 class="op-top-ups-title">' + esc(TOP_UPS_TITLE) + '</h3>';
    if (state.topUpsLoading) {
      html +=
        '<p class="op-top-ups-status" data-testid="orders-top-ups-status">' +
        esc(TOP_UPS_LOOKING) +
        '</p>';
    } else if (state.topUpsError) {
      html +=
        '<p class="op-top-ups-error" data-testid="orders-top-ups-error">' +
        esc(TOP_UPS_FAILED) +
        '</p>';
    } else if (topUpsAnswerValid(state.topUps)) {
      var entry = topUpsEntryFor(group);
      if (entry) {
        html +=
          topUpsSuggestionHtml(entry) +
          topUpsTableHtml(group, entry) +
          topUpsOutcomeHtml(entry) +
          topUpsChosenLineHtml(group, entry);
      } else {
        html +=
          '<p class="op-top-ups-empty" data-testid="orders-top-ups-empty">' +
          esc(TOP_UPS_NONE) +
          '</p>';
      }
    } else {
      html +=
        '<p class="op-top-ups-error" data-testid="orders-top-ups-error">' +
        esc(TOP_UPS_FAILED) +
        '</p>';
    }
    html += '</section>';
    return html;
  }

  /* ---------------------------- transfers ------------------------------ */

  // A well-formed transfers answer: an object (never an array or a
  // bare value) with a suggestions array. Anything else is a failed
  // load.
  function transfersAnswerValid(payload) {
    return (
      payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      Array.isArray(payload.suggestions)
    );
  }

  // The on-demand fetch: GET /api/supply-plan/transfers through
  // app/js/api.js, sent only when the section's button is clicked —
  // never on view activation, the plan's Refresh or a horizon
  // change. A stale answer (a newer click, or the sign-out's reset)
  // is discarded; a failure lands in module state as the generic
  // message, without a page error.
  function loadTransfers() {
    if (state.transfersLoading) {
      return; // a request is already out: one request per click
    }
    transfersSeq += 1;
    var mySeq = transfersSeq;
    state.transfersLoading = true;
    state.transfersError = null;
    render();
    global.Api.get(TRANSFERS_PATH).then(
      function (payload) {
        if (mySeq !== transfersSeq) {
          return; // a reset or a newer request happened: discard
        }
        state.transfersLoading = false;
        if (!transfersAnswerValid(payload)) {
          state.transfersError = TRANSFERS_FAILED;
        } else {
          state.transfers = payload;
        }
        render();
      },
      function () {
        if (mySeq !== transfersSeq) {
          return;
        }
        state.transfersLoading = false;
        state.transfersError = TRANSFERS_FAILED;
        render();
      }
    );
  }

  // The item-site index of a landed transfers answer: item_code,
  // then the receiving site (the suggestion's to_site), then the
  // suggestions that receive there, in the answer's order. Plain
  // null-prototype objects at every level: a hostile item code or
  // site ("__proto__", "constructor") is a key, never a property of
  // the index's own prototype.
  function makeTransferIndex(suggestions) {
    var index = Object.create(null);
    if (!Array.isArray(suggestions)) {
      return index;
    }
    for (var i = 0; i < suggestions.length; i++) {
      var suggestion = suggestions[i];
      if (!suggestion || typeof suggestion !== 'object') {
        continue; // a malformed suggestion never breaks the marks
      }
      var item = suggestion.item_code;
      var toSite = suggestion.to_site;
      if (
        typeof item !== 'string' ||
        item === '' ||
        typeof toSite !== 'string' ||
        toSite === ''
      ) {
        continue; // only a carried item and receiving site can match a line
      }
      if (!index[item]) {
        index[item] = Object.create(null);
      }
      var sites = index[item];
      if (!sites[toSite]) {
        sites[toSite] = [];
      }
      sites[toSite].push(suggestion);
    }
    return index;
  }

  // The marks the plan rows carry from a landed transfers answer:
  // null while the section is pending, has failed or has no answer
  // yet (no row is marked then), else the answer's item-site index.
  // They re-derive from module state on every render — a horizon
  // change or a plan Refresh re-applies them with no new transfers
  // request — and a later answer replaces them.
  function lineTransferMarks() {
    if (state.transfersLoading || state.transfersError || !state.transfers) {
      return null;
    }
    var suggestions = Array.isArray(state.transfers.suggestions)
      ? state.transfers.suggestions
      : [];
    if (suggestions.length === 0) {
      return null;
    }
    return makeTransferIndex(suggestions);
  }

  // The suggestions that receive at a line's item-site, from the
  // marks' index; null when the line matches none.
  function transferSuggestionsForLine(marks, line) {
    if (!marks) {
      return null;
    }
    if (typeof line.item_code !== 'string' || line.item_code === '') {
      return null;
    }
    if (typeof line.site_code !== 'string' || line.site_code === '') {
      return null;
    }
    var item = marks[line.item_code];
    if (!item) {
      return null;
    }
    return item[line.site_code] || null;
  }

  // The Quantity cell's note for a line the answer could cover: one
  // "transfer of <quantity> from site <from_site>" per matching
  // suggestion, in the answer's order, joined by "; ", then " could
  // cover this" when the suggestions' total stands at or above the
  // line's quantity (an equal total covers), else " covers part of
  // this".
  function transferNoteText(line, suggestions) {
    var total = 0;
    var parts = [];
    for (var i = 0; i < suggestions.length; i++) {
      var suggestion = suggestions[i];
      var quantity = asNumber(suggestion.quantity);
      if (!isNaN(quantity)) {
        total += quantity;
      }
      var fromSite = nonEmptyString(suggestion.from_site) || EM_DASH;
      parts.push('transfer of ' + wholeNumber(suggestion.quantity) + ' from site ' + fromSite);
    }
    var lineQuantity = asNumber(line.quantity);
    var covered = !isNaN(lineQuantity) && total >= lineQuantity;
    return parts.join('; ') + (covered ? ' could cover this' : ' covers part of this');
  }

  /* ------------------------------ release ------------------------------ */

  // The per-supplier release control state (AC: module state, keyed by
  // supplier_code, surviving a full re-render of #orders-root).
  //   answered — a preview or confirm answer has landed (the panel's
  //              group listing is the answer's, not a guess)
  //   busy     — a preview or confirm request is out (buttons disabled)
  //   confirming — the confirm's request is out: the block's own
  //              marker (busy covers the preview too) — while it is
  //              set the horizon select, the Refresh button and the
  //              block's top-up checkboxes are disabled, and their
  //              change and click handlers do nothing
  //   body     — the preview's request body, kept with the state so
  //              the confirm sends it back with only "confirm": true
  //   confirmed — a confirm answer has landed (the outcome lines show)
  //   error    — the newest failed request's message, or null
  function freshRelease() {
    return {
      answered: false,
      busy: false,
      confirming: false,
      body: null,
      confirmed: false,
      groups: [],
      unreleasable: [],
      error: null,
    };
  }

  // The panel is up once it has something to show: an answered preview
  // (even an empty one) or a failed request's error.
  function releasePanelOpen(rel) {
    return !!rel && (rel.answered || !!rel.error);
  }

  // The block's own confirm marker: the confirm's request is out
  // the door. A preview in flight sets no marker — it disables
  // none of the horizon select, the Refresh button or the block's
  // top-up checkboxes.
  function blockConfirming(supplierCode) {
    var rel = state.release[supplierCode];
    return !!(rel && rel.confirming);
  }

  // Any block's confirm out the door: the horizon select and the
  // Refresh button hold still while one is, whatever block it is.
  function anyConfirming() {
    for (var code in state.release) {
      if (Object.prototype.hasOwnProperty.call(state.release, code)) {
        var rel = state.release[code];
        if (rel && rel.confirming) {
          return true;
        }
      }
    }
    return false;
  }

  function groupValue(group) {
    return group && typeof group === 'object' ? group.value : null;
  }

  function groupLinesOf(group) {
    return group && Array.isArray(group.lines) ? group.lines.length : 0;
  }

  function groupSite(group) {
    var site = nonEmptyString(group && group.site_code);
    return site || EM_DASH;
  }

  // The outcome line of a confirmed group; an em dash for an outcome
  // the answer does not name.
  function outcomeText(group) {
    var outcome = group && typeof group.outcome === 'string' ? group.outcome : null;
    var orderNbr = nonEmptyString(group && group.order_nbr) || EM_DASH;
    if (outcome === 'created') {
      return 'Draft PO ' + orderNbr + ' created on hold';
    }
    if (outcome === 'exists') {
      return 'Already released as ' + orderNbr;
    }
    if (outcome === 'error') {
      return 'Failed: ' + (nonEmptyString(group.error) || EM_DASH);
    }
    return EM_DASH;
  }

  function outcomeClass(group) {
    var outcome = group && typeof group.outcome === 'string' ? group.outcome : null;
    return Object.prototype.hasOwnProperty.call(OUTCOME_CLASS, outcome)
      ? OUTCOME_CLASS[outcome]
      : '';
  }

  // The error of a failed release request: a 403 is the admin gate, a
  // 409 or 503 carries the server's own detail, everything else the
  // generic message.
  function releaseErrorMessage(error) {
    var status = error && typeof error.status === 'number' ? error.status : 0;
    if (status === 403) {
      return RELEASE_403;
    }
    if (status === 409 || status === 503) {
      var detail = error && typeof error.message === 'string' ? error.message : '';
      return detail !== '' ? detail : RELEASE_FAILED;
    }
    return RELEASE_FAILED;
  }

  // The block's chosen top-ups for the release body, in the
  // table's (the answer's) order: one entry per ticked row of the
  // block — the block's supplier code and the row's item and site.
  // None when no row of the block is ticked, when the block is not
  // in the held plan, or when the held top-ups answer carries no
  // entry for it (no rows then to tick).
  function releaseChosenTopUps(supplierCode) {
    var out = [];
    var table = state.chosenTopUps[supplierCode];
    if (!table || !state.data || !answerValid(state.data)) {
      return out;
    }
    var group = null;
    var groups = groupsOf(state.data);
    for (var i = 0; i < groups.length; i++) {
      var candidate = groups[i];
      if (!candidate || typeof candidate !== 'object') {
        continue; // a malformed group never breaks the body
      }
      if (candidate.supplier_code === null || candidate.supplier_code === undefined) {
        continue;
      }
      if (String(candidate.supplier_code) !== String(supplierCode)) {
        continue;
      }
      group = candidate;
      break;
    }
    if (!group) {
      return out;
    }
    var entry = topUpsEntryFor(group);
    if (!entry) {
      return out;
    }
    var topUps = Array.isArray(entry.top_ups) ? entry.top_ups : [];
    for (var j = 0; j < topUps.length; j++) {
      var topUp = topUps[j];
      if (!topUp || typeof topUp !== 'object') {
        continue; // a malformed top-up never breaks the body
      }
      var item = nonEmptyString(topUp.item_code) || '';
      var site = nonEmptyString(topUp.site_code) || '';
      if (table[topUpKey(item, site)]) {
        out.push({ supplier_code: supplierCode, item_code: item, site_code: site });
      }
    }
    return out;
  }

  // The preview body: the screen's horizon and that block's supplier
  // code; the block's ticked top-ups, in the table's order, as
  // "top_ups" — absent with no row ticked, so the body stays
  // byte-for-byte what it was. Kept with the block's release state
  // when the preview sends it, the confirm's body is that body with
  // only "confirm": true added.
  function releaseBody(supplierCode) {
    var body = {
      horizon_days: state.horizonDays,
      supplier_codes: [supplierCode],
    };
    var topUps = releaseChosenTopUps(supplierCode);
    if (topUps.length > 0) {
      body.top_ups = topUps;
    }
    return body;
  }

  // The confirm body: the preview's own body, kept with the block's
  // release state, with only "confirm": true added — whatever has
  // been ticked, unticked or chosen since the preview, nothing else
  // in the body changes. A state without a preview body (no Confirm
  // renders without one) falls back to the fields the release always
  // sends, with the flag.
  function confirmBodyOf(supplierCode, rel) {
    var body = null;
    var held = rel && rel.body && typeof rel.body === 'object' ? rel.body : null;
    if (held && !Array.isArray(held)) {
      body = {};
      for (var key in held) {
        if (Object.prototype.hasOwnProperty.call(held, key)) {
          body[key] = held[key];
        }
      }
    }
    if (!body) {
      body = {
        horizon_days: state.horizonDays,
        supplier_codes: [supplierCode],
      };
    }
    body.confirm = true;
    return body;
  }

  // One release request, the body built by the caller: the preview
  // passes its own body (kept with the state), the confirm the
  // preview's body with "confirm": true added. The state's identity
  // guard drops an answer that lands after a reset or after the
  // block's release was closed (a moved tick or a horizon change).
  function sendRelease(supplierCode, body, confirm) {
    var rel = state.release[supplierCode];
    var myGen = releaseGen;
    global.Api.request(RELEASE_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(
      function (data) {
        if (myGen !== releaseGen || state.release[supplierCode] !== rel) {
          return; // a reset or a closed release: this answer belongs to a past state
        }
        rel.busy = false;
        rel.confirming = false;
        rel.answered = true;
        if (confirm) {
          rel.confirmed = true;
        }
        rel.groups = data && Array.isArray(data.groups) ? data.groups : [];
        rel.unreleasable =
          data && Array.isArray(data.unreleasable) ? data.unreleasable : [];
        render();
      },
      function (error) {
        if (myGen !== releaseGen || state.release[supplierCode] !== rel) {
          return; // a reset or a closed release: this answer belongs to a past state
        }
        rel.busy = false;
        rel.confirming = false;
        rel.error = releaseErrorMessage(error);
        render();
      }
    );
  }

  // The preview: one POST without confirm, its body kept with the
  // block's release state for the confirm; the panel opens on the
  // answer (or on the failure's error).
  function startPreview(supplierCode) {
    if (state.role !== ROLE_ADMIN || !supplierCode) {
      return; // fail closed, and nothing to release for
    }
    var rel = state.release[supplierCode] || (state.release[supplierCode] = freshRelease());
    if (rel.busy) {
      return; // a request is already out: one request per click pair
    }
    rel.busy = true;
    rel.error = null;
    rel.confirmed = false;
    rel.body = releaseBody(supplierCode); // the preview's body, kept with the state
    render();
    sendRelease(supplierCode, rel.body, false);
  }

  // The confirm: the preview's own body, kept with the state, with
  // only "confirm": true added — the screen's choosing since the
  // preview never changes the body.
  function doConfirm(supplierCode) {
    if (state.role !== ROLE_ADMIN || !supplierCode) {
      return;
    }
    var rel = state.release[supplierCode];
    if (!rel || rel.busy || rel.groups.length === 0) {
      return;
    }
    rel.busy = true;
    rel.confirming = true; // the confirm's own marker: the block holds still
    rel.error = null;
    render();
    sendRelease(supplierCode, confirmBodyOf(supplierCode, rel), true);
  }

  // Cancel closes the panel and sends nothing; it is a no-op while a
  // request is out (its button is disabled then).
  function cancelRelease(supplierCode) {
    if (!supplierCode) {
      return;
    }
    var rel = state.release[supplierCode];
    if (!rel || rel.busy) {
      return;
    }
    delete state.release[supplierCode];
    render();
  }

  // The block's release, closed by a moved choosing: a tick of (or
  // untick of) one of the block's top-ups clears the block's release
  // state the way "Cancel" closes it — the panel goes, no request is
  // sent — and it also applies while a preview request is still out
  // the door, in which case the deleted state is what makes the
  // request's own guard drop the preview's answer when it lands; the
  // admin previews again. A tick in one block leaves the other
  // blocks' release state, and their open panels, untouched.
  function closeRelease(supplierCode) {
    if (!supplierCode) {
      return;
    }
    delete state.release[supplierCode];
  }

  // The block's supplier code, from the release control's own marker:
  // the value the server carried, read back as a string.
  function releaseCodeOf(element) {
    var wrap = element && element.closest ? element.closest('[data-supplier-code]') : null;
    return wrap ? wrap.getAttribute('data-supplier-code') : null;
  }

  /* ------------------------- transfer release -------------------------- */

  // The transfer release control state (AC: module state, one panel
  // at a time, surviving a full re-render of #orders-root):
  //   item/from/to — the suggestion the open panel belongs to, as
  //                     the server carried it (the request body's)
  //   answered  — a preview or confirm answer has landed (the panel's
  //                 group listing is the answer's, not a guess)
  //   busy      — a preview or confirm request is out (buttons disabled)
  //   confirmed — a confirm answer has landed (the outcome lines show)
  //   error     — the newest failed request's message, or null
  function freshTransferRelease() {
    return {
      item: null,
      from: null,
      to: null,
      answered: false,
      busy: false,
      confirmed: false,
      groups: [],
      unreleasable: [],
      error: null,
    };
  }

  // The panel is up once it has something to show: an answered
  // preview (even an empty one) or a failed request's error.
  function transferReleasePanelOpen(rel) {
    return !!rel && (rel.answered || !!rel.error);
  }

  // The outcome line of a confirmed group; an em dash for an outcome
  // the answer does not name.
  function transferOutcomeText(group) {
    var outcome = group && typeof group.outcome === 'string' ? group.outcome : null;
    var referenceNbr = nonEmptyString(group && group.reference_nbr) || EM_DASH;
    if (outcome === 'created') {
      return 'Transfer ' + referenceNbr + ' created on hold';
    }
    if (outcome === 'exists') {
      return 'Already released as ' + referenceNbr;
    }
    if (outcome === 'error') {
      return 'Failed: ' + (nonEmptyString(group.error) || EM_DASH);
    }
    return EM_DASH;
  }

  function transferOutcomeClass(group) {
    var outcome = group && typeof group.outcome === 'string' ? group.outcome : null;
    return Object.prototype.hasOwnProperty.call(TRANSFER_OUTCOME_CLASS, outcome)
      ? TRANSFER_OUTCOME_CLASS[outcome]
      : '';
  }

  // The error of a failed transfer release request: a 403 is the
  // admin gate, a 409 or 503 carries the server's own detail,
  // everything else the generic message.
  function transferReleaseErrorMessage(error) {
    var status = error && typeof error.status === 'number' ? error.status : 0;
    if (status === 403) {
      return TRANSFER_RELEASE_403;
    }
    if (status === 409 || status === 503) {
      var detail = error && typeof error.message === 'string' ? error.message : '';
      return detail !== '' ? detail : TRANSFER_RELEASE_FAILED;
    }
    return TRANSFER_RELEASE_FAILED;
  }

  // The release body: exactly the one suggestion the row named —
  // the item, the giving site and the receiving site, never a
  // quantity; confirm adds only the confirm flag.
  function transferReleaseBody(rel, confirm) {
    var body = {
      transfers: [
        {
          item_code: rel.item,
          from_site: rel.from,
          to_site: rel.to,
        },
      ],
    };
    if (confirm) {
      body.confirm = true;
    }
    return body;
  }

  function sendTransferRelease(rel, confirm) {
    var myGen = transferReleaseGen;
    global.Api.request(TRANSFER_RELEASE_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(transferReleaseBody(rel, confirm)),
    }).then(
      function (data) {
        if (myGen !== transferReleaseGen || state.transferRelease !== rel) {
          return; // a reset happened: this answer belongs to a past session
        }
        rel.busy = false;
        rel.answered = true;
        if (confirm) {
          rel.confirmed = true;
        }
        rel.groups = data && Array.isArray(data.groups) ? data.groups : [];
        rel.unreleasable =
          data && Array.isArray(data.unreleasable) ? data.unreleasable : [];
        render();
      },
      function (error) {
        if (myGen !== transferReleaseGen || state.transferRelease !== rel) {
          return;
        }
        rel.busy = false;
        rel.error = transferReleaseErrorMessage(error);
        render();
      }
    );
  }

  // The preview: one POST without confirm; the panel opens on the
  // answer (or on the failure's error), replacing any panel that was
  // open. A role the identity does not resolve to admin fails closed.
  function startTransferPreview(item, from, to) {
    if (state.role !== ROLE_ADMIN) {
      return; // fail closed
    }
    var open = state.transferRelease;
    if (open && open.busy) {
      return; // a request is already out: one request at a time
    }
    var rel = freshTransferRelease();
    rel.item = item;
    rel.from = from;
    rel.to = to;
    rel.busy = true;
    state.transferRelease = rel;
    render();
    sendTransferRelease(rel, false);
  }

  function confirmTransferRelease() {
    if (state.role !== ROLE_ADMIN) {
      return;
    }
    var rel = state.transferRelease;
    if (!rel || rel.busy || rel.groups.length === 0) {
      return;
    }
    rel.busy = true;
    rel.error = null;
    render();
    sendTransferRelease(rel, true);
  }

  // Cancel closes the panel and sends nothing; it is a no-op while a
  // request is out (its button is disabled then).
  function cancelTransferRelease() {
    var rel = state.transferRelease;
    if (!rel || rel.busy) {
      return;
    }
    state.transferRelease = null;
    render();
  }

  /* ------------------------------ rendering ---------------------------- */

  function render() {
    if (!rootEl) {
      return;
    }
    // The re-render replaces the scroll container (.op-body) whole,
    // and a fresh element opens scrolled to the top — so every
    // re-render (a busy button, an answer landing, the plan's
    // Refresh) would yank a reader back to the top of the lists.
    // Carry the scroll position over: the lists stay where the
    // reader left them, and a control clicked at the bottom of the
    // lists re-renders in place.
    var scrollEl = rootEl.querySelector('.op-body');
    var scrollTop = scrollEl ? scrollEl.scrollTop : 0;
    rootEl.innerHTML = shellHtml();
    var nextScrollEl = rootEl.querySelector('.op-body');
    if (nextScrollEl && scrollTop !== 0) {
      nextScrollEl.scrollTop = scrollTop;
    }
  }

  // The answer has no lines: its own line_count is 0 (a numeric "0"
  // included). The number of groups never decides this — a group list
  // whose lines are all empty still means nothing to order.
  function answerHasNoLines(data) {
    return asNumber(data && data.line_count) === 0;
  }

  // The shell gates [data-admin-only] elements from the sign-in's
  // /api/auth/me answer. This zero-size probe sits in the render until
  // the sign-in's role lands on it: the shell leaves it hidden for a
  // non-admin and un-hides it for an admin — the screen's only read of
  // the shared identity, no request of its own.
  function roleProbeHtml() {
    if (state.role !== ROLE_UNRESOLVED) {
      return '';
    }
    return '<span class="op-role-probe" data-admin-only hidden aria-hidden="true"></span>';
  }

  function shellHtml() {
    var probe = roleProbeHtml();
    // First load (no answer, no failure yet) and every in-flight
    // fetch of it: the skeleton.
    if (!state.data && !state.error) {
      return '<div class="op">' + probe + skeletonHtml() + '</div>';
    }
    var noLines = answerHasNoLines(state.data);
    var html = '<div class="op">' + probe + barHtml();
    if (state.data && !state.loading && !state.error && !noLines) {
      html += summaryHtml();
    }
    var body;
    if (state.loading) {
      body = skeletonHtml();
    } else if (state.error) {
      body = errorHtml();
    } else if (noLines) {
      body = emptyHtml();
    } else {
      body = groupsHtml();
    }
    // The transfer suggestions section lives below the last
    // supplier block (below the empty note when the plan carries no
    // lines): its answer, error and pending flag are module state,
    // so it survives the plan's re-renders untouched. It never
    // renders while the plan itself has failed.
    if (state.data && !state.error) {
      body += transfersSectionHtml();
    }
    html += '<div class="op-body">' + body + '</div></div>';
    return html;
  }

  function barHtml() {
    var options = HORIZONS.map(function (horizon) {
      return (
        '<option value="' + horizon.days + '"' +
        (state.horizonDays === horizon.days ? ' selected' : '') + '>' +
        esc(horizon.label) +
        '</option>'
      );
    }).join('');
    // The Download CSV button shows only while a plan answer with
    // at least one line is loaded: off on the first-load skeleton
    // (no bar then), the empty note, the load error and while a
    // refresh is out the door. Every signed-in user sees it, admin
    // or not — the file is built from the loaded answer, so there
    // is nothing to gate.
    var showDownload =
      state.data && !state.loading && !state.error && !answerHasNoLines(state.data);
    return (
      '<div class="op-bar">' +
      '<label class="op-horizon-label" for="orders-horizon">Horizon</label>' +
      '<select class="op-horizon" id="orders-horizon" data-testid="orders-horizon"' +
      (state.loading || anyConfirming() ? ' disabled' : '') + '>' +
      options +
      '</select>' +
      '<button type="button" class="btn btn-secondary op-refresh"' +
      ' data-testid="orders-refresh"' +
      (state.loading || anyConfirming() ? ' disabled' : '') +
      '>Refresh</button>' +
      (showDownload
        ? '<button type="button" class="btn btn-secondary op-download"' +
          ' data-testid="orders-download"' +
          '>' + esc(DOWNLOAD_LABEL) + '</button>'
        : '') +
      '</div>'
    );
  }

  function summaryHtml() {
    var suppliers = groupsOf(state.data);
    var withSupplier = 0;
    for (var i = 0; i < suppliers.length; i++) {
      if (suppliers[i] && typeof suppliers[i] === 'object' && hasSupplier(suppliers[i])) {
        withSupplier += 1;
      }
    }
    var text =
      wholeNumber(state.data.line_count) +
      ' ' +
      linesWord(state.data.line_count) +
      ' ' +
      MIDDLE_DOT +
      ' ' +
      wholeDollars(state.data.total_value) +
      ' across ' +
      withSupplier +
      ' suppliers';
    return (
      '<p class="op-summary" data-testid="orders-summary">' +
      esc(text) +
      '</p>'
    );
  }

  function skeletonHtml() {
    return (
      '<div class="op-skel-wrap" data-testid="orders-skeleton" aria-hidden="true">' +
      '<div class="skel skel-line"></div>' +
      '<div class="skel skel-line skel-wide"></div>' +
      '<div class="skel op-skel-block"></div>' +
      '</div>'
    );
  }

  function errorHtml() {
    return (
      '<div class="op-error" role="alert" data-testid="orders-error">' +
      '<p class="op-error-msg" data-testid="orders-error-msg">' +
        esc(state.error.message) +
      '</p>' +
      '<button type="button" class="btn btn-secondary" data-testid="orders-retry">Retry</button>' +
      '</div>'
    );
  }

  function emptyHtml() {
    return (
      '<p class="op-empty" data-testid="orders-empty">' +
      esc('Nothing to order in the next ' + state.horizonDays + ' days.') +
      '</p>'
    );
  }

  // The release control of one supplier block: nothing for a
  // non-admin or an unresolved identity (fail closed), nothing for the
  // group without supplier terms, the note for "odoo", the button (and
  // the open panel) for "acumatica" — and no control at all for a
  // group whose supplier_code is blank: a Release button without a
  // code could not send, so it renders none.
  function releaseControlHtml(group) {
    if (state.role !== ROLE_ADMIN || !hasSupplier(group)) {
      return '';
    }
    var source = nonEmptyString(group.source_system);
    var code = nonEmptyString(group.supplier_code);
    if (source === 'odoo') {
      return (
        '<p class="op-release-note" data-testid="orders-release-note">' +
        esc(ODOO_NOTE) +
        '</p>'
      );
    }
    if (source !== 'acumatica' || code === null) {
      return '';
    }
    var rel = state.release[code];
    var html =
      '<div class="op-release" data-supplier-code="' + esc(code) + '">' +
      '<button type="button" class="btn btn-primary op-release-btn"' +
      ' data-testid="orders-release"' +
      (rel && rel.busy ? ' disabled' : '') +
      '>' + esc(RELEASE_BUTTON_LABEL) + '</button>' +
      (releasePanelOpen(rel) ? releasePanelHtml(code, rel) : '') +
      '</div>';
    return html;
  }

  // The preview/confirm panel of one supplier, from module state only:
  // the group listing (site, line count, total), the outcome lines of
  // a confirmed release, the unreleasable lines' item and reason, the
  // newest error, and the buttons — disabled while a request is out.
  function releasePanelHtml(code, rel) {
    var html =
      '<div class="op-release-panel" data-testid="orders-release-panel">';
    if (rel.groups.length > 0) {
      html += '<ul class="op-release-groups">';
      for (var i = 0; i < rel.groups.length; i++) {
        var group = rel.groups[i];
        if (!group || typeof group !== 'object') {
          continue; // a malformed group never breaks the panel
        }
        html +=
          '<li class="op-release-group">' +
          '<span class="op-release-group-site">' + esc(groupSite(group)) + '</span>' +
          '<span class="op-release-group-lines">' +
          esc(
            wholeNumber(groupLinesOf(group)) +
            ' ' +
            linesWord(groupLinesOf(group))
          ) +
          '</span>' +
          '<span class="op-release-group-value">' +
          esc(wholeDollars(groupValue(group))) +
          '</span>';
        if (rel.confirmed) {
          html +=
            '<span class="op-release-outcome ' + outcomeClass(group) + '"' +
            ' data-testid="orders-release-outcome">' +
            esc(outcomeText(group)) +
            '</span>';
        }
        html += '</li>';
      }
      html += '</ul>';
    } else if (rel.answered) {
      html +=
        '<p class="op-release-none" data-testid="orders-release-none">' +
        esc(NOTHING_TO_RELEASE) +
        '</p>';
    }
    if (rel.unreleasable.length > 0) {
      html +=
        '<ul class="op-release-unreleasable" data-testid="orders-release-unreleasable">';
      for (var j = 0; j < rel.unreleasable.length; j++) {
        var item = rel.unreleasable[j];
        if (!item || typeof item !== 'object') {
          continue;
        }
        html +=
          '<li class="op-release-unreleasable-item">' +
          esc(nonEmptyString(item.item_code) || EM_DASH) +
          ' ' + EM_DASH + ' ' +
          esc(nonEmptyString(item.reason) || EM_DASH) +
          '</li>';
      }
      html += '</ul>';
    }
    html += '<div class="op-release-actions">';
    if (rel.groups.length > 0) {
      html +=
        '<button type="button" class="btn btn-primary op-release-confirm"' +
        ' data-testid="orders-release-confirm"' +
        (rel.busy ? ' disabled' : '') +
        '>Confirm release</button>';
    }
    html +=
      '<button type="button" class="btn btn-secondary op-release-cancel"' +
      ' data-testid="orders-release-cancel"' +
      (rel.busy ? ' disabled' : '') +
      '>Cancel</button>' +
      '</div>';
    if (rel.error) {
      html +=
        '<p class="op-release-error" role="alert" data-testid="orders-release-error">' +
        esc(rel.error) +
        '</p>';
    }
    html += '</div>';
    return html;
  }

  function groupsHtml() {
    // The marks the rows carry from a landed transfers answer,
    // derived once per render from module state — no new
    // transfers request.
    var marks = lineTransferMarks();
    var groups = groupsOf(state.data);
    var blocks = [];
    for (var i = 0; i < groups.length; i++) {
      var group = groups[i];
      if (!group || typeof group !== 'object') {
        continue; // a malformed group never breaks the screen
      }
      blocks.push(groupHtml(group, marks));
    }
    return blocks.join('');
  }

  // One block per supplier group, in the answer's order: the heading
  // (the name, the supplier_code when the name is null, "No supplier
  // terms" for the group without supplier terms), the group's totals
  // and the lines' table.
  function groupHtml(group, marks) {
    var heading;
    if (!hasSupplier(group)) {
      heading = NO_TERMS_HEADING;
    } else {
      heading =
        nonEmptyString(group.name) ||
        nonEmptyString(group.supplier_code) ||
        EM_DASH;
    }
    var vendor = nonEmptyString(group.vendor_code);
    return (
      '<section class="op-supplier" data-testid="orders-supplier">' +
      '<header class="op-supplier-head">' +
      '<h2 class="op-supplier-title">' + esc(heading) + '</h2>' +
      (vendor ? '<span class="op-supplier-vendor">' + esc(vendor) + '</span>' : '') +
      supplierRiskBadgeHtml(group) +
      supplierShortBadgeHtml(group) +
      '</header>' +
      '<p class="op-supplier-totals" data-testid="orders-supplier-totals">' +
      esc(
        wholeNumber(group.line_count) +
        ' ' +
        linesWord(group.line_count) +
        ' ' +
        MIDDLE_DOT +
        ' ' +
        wholeDollars(group.total_value)
      ) +
      '</p>' +
      supplierMinimumLineHtml(group) +
      releaseControlHtml(group) +
      groupTableHtml(group, marks) +
      topUpsSectionHtml(group) +
      '</section>'
    );
  }

  function groupTableHtml(group, marks) {
    var lines = groupLines(group);
    var rows = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (!line || typeof line !== 'object') {
        continue; // a malformed line never breaks the table
      }
      rows.push(lineHtml(line, marks));
    }
    return (
      '<div class="op-lines-region">' +
      '<table class="op-table" data-testid="orders-lines">' +
      '<thead><tr>' +
      '<th scope="col">Item and Site</th>' +
      '<th scope="col">Due</th>' +
      '<th scope="col">Order by</th>' +
      '<th scope="col">Arrives</th>' +
      '<th scope="col">Quantity</th>' +
      '<th scope="col">Value</th>' +
      '<th scope="col">Why</th>' +
      '</tr></thead>' +
      '<tbody>' + rows.join('') + '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  function lineHtml(line, marks) {
    var short = line.short_of_need === true;
    var transfer = transferSuggestionsForLine(marks, line);
    return (
      '<tr class="op-row"' +
      (short ? ' data-short="true"' : '') +
      (transfer ? ' data-transfer="true"' : '') +
      '>' +
      '<td class="op-cell-pair">' +
      esc(pairText(line.item_code, line.site_code)) +
      bufferSourceHtml(line) +
      '</td>' +
      '<td class="op-cell-due">' + esc(dueText(line.due_in_days)) + '</td>' +
      '<td class="op-cell-date">' + esc(dateText(line.order_by)) + '</td>' +
      '<td class="op-cell-date">' + esc(dateText(line.arrives_on)) + '</td>' +
      '<td class="op-cell-qty">' + quantityHtml(line, transfer) + '</td>' +
      '<td class="op-cell-money">' + esc(wholeDollars(line.value)) + '</td>' +
      '<td class="op-cell-why">' + esc(line.arithmetic) + supplierReasonHtml(line) + '</td>' +
      '</tr>'
    );
  }

  // The supplier reason in the Why cell (issue #1077): one element
  // after the arithmetic text when the line's supplier_reason is a
  // non-empty string (holding it, escaped) — a missing, null, empty
  // or non-string reason leaves the cell exactly as before.
  function supplierReasonHtml(line) {
    var reason = line.supplier_reason;
    if (typeof reason !== 'string' || reason === '') {
      return '';
    }
    return (
      '<span class="op-supplier-reason" data-testid="orders-line-supplier-reason">' +
      esc(reason) +
      '</span>'
    );
  }

  // The buffer source badge in the Item and Site cell
  // (issue #1542): one element right after the item and
  // site text when the line's source is exactly the string
  // "buffer" — the line the plan drew from its DDMRP
  // buffer's order signal. Any other source ("reorder_point",
  // missing, null or another value) leaves the cell exactly
  // as before.
  function bufferSourceHtml(line) {
    if (line.source !== 'buffer') {
      return '';
    }
    return (
      '<span class="op-line-source" data-testid="orders-line-source"' +
      ' title="Planned from its DDMRP buffer\'s order signal">Buffer</span>'
    );
  }

  // The Quantity cell: the fitted quantity, then a note per adjustment
  // ("raised to minimum", "rounded to lot", "capped at maximum") and
  // "short of need" on a short line, and the transfer note when a
  // landed transfers answer receives at the line's item-site.
  function quantityHtml(line, transfer) {
    var notes = [];
    var adjustments = Array.isArray(line.adjustments) ? line.adjustments : [];
    for (var i = 0; i < adjustments.length; i++) {
      var token = adjustments[i];
      var label =
        Object.prototype.hasOwnProperty.call(ADJUSTMENT_NOTES, token)
          ? ADJUSTMENT_NOTES[token]
          : null;
      if (label) {
        notes.push('<span class="op-note">' + esc(label) + '</span>');
      }
    }
    if (line.short_of_need === true) {
      notes.push('<span class="op-note op-note--short">' + esc(SHORT_NOTE) + '</span>');
    }
    if (transfer) {
      notes.push(
        '<span class="op-note op-note--transfer" data-testid="orders-line-transfer">' +
        esc(transferNoteText(line, transfer)) +
        '</span>'
      );
    }
    return (
      '<span class="op-cell-qty-num">' + esc(wholeNumber(line.quantity)) + '</span>' +
      notes.join('')
    );
  }

  // The transfer suggestions section, below the last supplier
  // block: the heading and the button (the "Refresh" label once an
  // answer has landed), the in-flight note, the newest failed
  // request's error, and the answer's summary and table — or the
  // no-suggestions note. Every API value is inserted as text, never
  // as markup; the section is not role-gated — every signed-in user
  // sees it.
  function transfersSectionHtml() {
    var label =
      state.transfers !== null ? TRANSFERS_REFRESH_LABEL : TRANSFERS_SHOW_LABEL;
    var html =
      '<section class="op-transfers" data-testid="orders-transfers">' +
      '<header class="op-transfers-head">' +
      '<h2 class="op-transfers-title">' + esc(TRANSFERS_TITLE) + '</h2>' +
      '<button type="button" class="btn btn-secondary op-transfers-btn"' +
      ' data-testid="orders-transfers-show"' +
      (state.transfersLoading ? ' disabled' : '') +
      '>' + esc(label) + '</button>' +
      '</header>';
    if (state.transfersLoading) {
      html +=
        '<p class="op-transfers-loading" data-testid="orders-transfers-loading">' +
        esc('Loading transfer suggestions\u2026') +
        '</p>';
    }
    if (state.transfersError) {
      html +=
        '<p class="op-transfers-error" role="alert" data-testid="orders-transfers-error">' +
        esc(state.transfersError) +
        '</p>';
    }
    if (!state.transfersLoading && !state.transfersError && state.transfers) {
      html += transfersAnswerHtml(state.transfers);
    }
    // The release panel (admin only) sits below the table: module
    // state, so it survives the section's re-renders; one at a time.
    html += transferReleasePanelHtml();
    return html + '</section>';
  }

  // The answer's body: the no-suggestions note when the answer
  // carries none, else the summary and the rows' table (one row per
  // suggestion, in the answer's order).
  function transfersAnswerHtml(data) {
    var suggestions = Array.isArray(data.suggestions) ? data.suggestions : [];
    if (suggestions.length === 0) {
      return (
        '<p class="op-transfers-empty" data-testid="orders-transfers-empty">' +
        esc(TRANSFERS_EMPTY) +
        '</p>'
      );
    }
    var html =
      '<p class="op-transfers-summary" data-testid="orders-transfers-summary">' +
      esc(transfersSummaryText(data)) +
      '</p>';
    var rows = [];
    for (var i = 0; i < suggestions.length; i++) {
      var suggestion = suggestions[i];
      if (!suggestion || typeof suggestion !== 'object') {
        continue; // a malformed suggestion never breaks the table
      }
      rows.push(transferRowHtml(suggestion));
    }
    html +=
      '<div class="op-transfers-region">' +
      '<table class="op-table op-transfers-table" data-testid="orders-transfers-lines">' +
      '<thead><tr>' +
      '<th scope="col">Item</th>' +
      '<th scope="col">From</th>' +
      '<th scope="col">To</th>' +
      '<th scope="col">Quantity</th>' +
      '<th scope="col">Value</th>' +
      '<th scope="col">Why</th>' +
      (state.role === ROLE_ADMIN ? '<th scope="col">Release</th>' : '') +
      '</tr></thead>' +
      '<tbody>' +
      rows.join('') +
      '</tbody>' +
      '</table>' +
      '</div>';
    return html;
  }

  // The summary: "<count> transfers · <total as whole dollars>". The
  // count is the answer's suggestion_count (its suggestions' length
  // when the count is not a usable figure); total_value is the
  // route's decimal string, read as a number like the release
  // groups' value.
  function transfersSummaryText(data) {
    var count = asNumber(data.suggestion_count);
    if (isNaN(count)) {
      count = Array.isArray(data.suggestions) ? data.suggestions.length : 0;
    }
    return (
      wholeNumber(count) +
      ' transfers ' +
      MIDDLE_DOT +
      ' ' +
      wholeDollars(data.total_value)
    );
  }

  // One row per suggestion: the item, the giving and receiving
  // sites, the quantity, the value (whole dollars, an em dash when
  // the answer carries none) and the arithmetic as text.
  function transferRowHtml(suggestion) {
    var why =
      typeof suggestion.arithmetic === 'string' && suggestion.arithmetic !== ''
        ? suggestion.arithmetic
        : EM_DASH;
    return (
      '<tr class="op-transfers-row">' +
      '<td class="op-cell-pair">' +
      esc(nonEmptyString(suggestion.item_code) || EM_DASH) +
      '</td>' +
      '<td class="op-transfers-site">' +
      esc(nonEmptyString(suggestion.from_site) || EM_DASH) +
      '</td>' +
      '<td class="op-transfers-site">' +
      esc(nonEmptyString(suggestion.to_site) || EM_DASH) +
      '</td>' +
      '<td class="op-cell-qty-num">' + esc(wholeNumber(suggestion.quantity)) + '</td>' +
      '<td class="op-cell-money">' + esc(wholeDollars(suggestion.value)) + '</td>' +
      '<td class="op-cell-why">' + esc(why) + '</td>' +
      transferReleaseCellHtml(suggestion) +
      '</tr>'
    );
  }

  // The last column's cell (admin only, the column itself being
  // admin-only): the Release button for an "acumatica" suggestion,
  // an empty cell for every other row. The button's markers carry the
  // suggestion's item and sites, escaped and read back as strings.
  function transferReleaseCellHtml(suggestion) {
    if (state.role !== ROLE_ADMIN) {
      return ''; // no column at all for a non-admin
    }
    var rel = state.transferRelease;
    var source = nonEmptyString(suggestion.source_system);
    if (source !== 'acumatica') {
      return '<td class="op-transfer-release-cell"></td>';
    }
    var item = nonEmptyString(suggestion.item_code) || '';
    var from = nonEmptyString(suggestion.from_site) || '';
    var to = nonEmptyString(suggestion.to_site) || '';
    return (
      '<td class="op-transfer-release-cell">' +
      '<button type="button" class="btn btn-primary op-transfer-release"' +
      ' data-testid="orders-transfer-release"' +
      (rel && rel.busy ? ' disabled' : '') +
      ' data-item="' + esc(item) + '"' +
      ' data-from="' + esc(from) + '"' +
      ' data-to="' + esc(to) + '"' +
      '>Release</button>' +
      '</td>'
    );
  }

  // The preview/confirm panel, below the table, from module state
  // only: the group listing (the sites' pair, the line count and each
  // line's item and quantity), the outcome lines of a confirmed
  // release, the unreleasable entries' item and reason, the newest
  // error, and the buttons — disabled while a request is out.
  // Every API value is inserted as text, never as markup.
  function transferReleasePanelHtml() {
    var rel = state.transferRelease;
    if (!transferReleasePanelOpen(rel)) {
      return '';
    }
    var html = '<div class="op-transfer-panel" data-testid="orders-transfer-panel">';
    if (rel.groups.length > 0) {
      html += '<ul class="op-transfer-panel-groups">';
      for (var i = 0; i < rel.groups.length; i++) {
        var group = rel.groups[i];
        if (!group || typeof group !== 'object') {
          continue; // a malformed group never breaks the panel
        }
        var fromSite = nonEmptyString(group.from_site) || EM_DASH;
        var toSite = nonEmptyString(group.to_site) || EM_DASH;
        var lines = Array.isArray(group.lines) ? group.lines : [];
        html +=
          '<li class="op-transfer-panel-group">' +
          '<span class="op-transfer-panel-sites">' +
          esc(fromSite + ' \u2192 ' + toSite) +
          '</span>' +
          '<span class="op-transfer-panel-count">' +
          esc(lines.length + ' ' + linesWord(lines.length)) +
          '</span>';
        for (var j = 0; j < lines.length; j++) {
          var line = lines[j];
          if (!line || typeof line !== 'object') {
            continue; // a malformed line never breaks the panel
          }
          html +=
            '<span class="op-transfer-panel-line">' +
            '<span class="op-transfer-panel-line-item">' +
            esc(nonEmptyString(line.item_code) || EM_DASH) +
            '</span>' +
            '<span class="op-transfer-panel-line-qty">' +
            esc(wholeNumber(line.quantity)) +
            '</span>' +
            '</span>';
        }
        if (rel.confirmed) {
          html +=
            '<span class="op-transfer-outcome ' + transferOutcomeClass(group) + '"' +
            ' data-testid="orders-transfer-outcome">' +
            esc(transferOutcomeText(group)) +
            '</span>';
        }
        html += '</li>';
      }
      html += '</ul>';
    } else if (rel.answered) {
      html +=
        '<p class="op-transfer-panel-none" data-testid="orders-transfer-none">' +
        esc(NOTHING_TO_TRANSFER_RELEASE) +
        '</p>';
    }
    if (rel.unreleasable.length > 0) {
      html += '<ul class="op-transfer-panel-unreleasable">';
      for (var k = 0; k < rel.unreleasable.length; k++) {
        var entry = rel.unreleasable[k];
        if (!entry || typeof entry !== 'object') {
          continue;
        }
        html +=
          '<li class="op-transfer-panel-unreleasable-item">' +
          esc(nonEmptyString(entry.item_code) || EM_DASH) +
          ' ' + EM_DASH + ' ' +
          esc(nonEmptyString(entry.reason) || EM_DASH) +
          '</li>';
      }
      html += '</ul>';
    }
    html += '<div class="op-transfer-panel-actions">';
    if (rel.groups.length > 0) {
      html +=
        '<button type="button" class="btn btn-primary op-transfer-confirm"' +
        ' data-testid="orders-transfer-confirm"' +
        (rel.busy ? ' disabled' : '') +
        '>Confirm transfer</button>';
    }
    html +=
      '<button type="button" class="btn btn-secondary op-transfer-cancel"' +
      ' data-testid="orders-transfer-cancel"' +
      (rel.busy ? ' disabled' : '') +
      '>Cancel</button>' +
      '</div>';
    if (rel.error) {
      html +=
        '<p class="op-transfer-panel-error" role="alert" data-testid="orders-transfer-error">' +
        esc(rel.error) +
        '</p>';
    }
    html += '</div>';
    return html;
  }

  /* --------------------------------- CSV --------------------------------- */

  // The CSV text cell: an empty cell for null and undefined, the
  // string otherwise; a hostile non-string (a number, a boolean)
  // carries its plain string form.
  function csvText(value) {
    if (value === null || value === undefined) {
      return '';
    }
    return typeof value === 'string' ? value : String(value);
  }

  // The CSV number cell: an empty cell for null and for anything
  // that is not a usable number, a plain number (no thousands
  // separators) otherwise.
  function csvNumber(value) {
    var number = asNumber(value);
    return isNaN(number) ? '' : String(number);
  }

  // The spreadsheet formula guard: a text field that starts with
  // "=", "+", "-", "@", a tab or a CR would run as a formula when
  // the file is opened in a spreadsheet; a leading single quote
  // keeps it text.
  function csvGuard(text) {
    var first = text.charAt(0);
    if (
      first === '=' ||
      first === '+' ||
      first === '-' ||
      first === '@' ||
      first === '\t' ||
      first === '\r'
    ) {
      return "'" + text;
    }
    return text;
  }

  // One CSV field: a field containing a comma, a double quote, a CR
  // or an LF is wrapped in double quotes, inner double quotes
  // doubled; an empty field stays an empty cell.
  function csvField(text) {
    if (/[",\r\n]/.test(text)) {
      return '"' + text.replace(/"/g, '""') + '"';
    }
    return text;
  }

  // A text column's cell: the formula guard, then the quoting.
  function csvTextField(value) {
    var text = csvText(value);
    if (text !== '') {
      text = csvGuard(text);
    }
    return csvField(text);
  }

  // The plan's CSV document: the header row, then one row per plan
  // line in the screen's order — the groups with supplier terms in
  // the answer's order, the group without supplier terms last —
  // every row, the header included, ending with CRLF.
  function planCsvText(data) {
    var groups = groupsOf(data);
    var ordered = [];
    for (var i = 0; i < groups.length; i++) {
      var termsGroup = groups[i];
      if (termsGroup && typeof termsGroup === 'object' && hasSupplier(termsGroup)) {
        ordered.push(termsGroup);
      }
    }
    for (var j = 0; j < groups.length; j++) {
      var noTermsGroup = groups[j];
      if (noTermsGroup && typeof noTermsGroup === 'object' && !hasSupplier(noTermsGroup)) {
        ordered.push(noTermsGroup);
      }
    }
    var rows = [CSV_HEADER];
    for (var k = 0; k < ordered.length; k++) {
      var group = ordered[k];
      var supplier =
        nonEmptyString(group.name) ||
        nonEmptyString(group.supplier_code) ||
        NO_TERMS_HEADING;
      var lines = groupLines(group);
      for (var m = 0; m < lines.length; m++) {
        var line = lines[m];
        if (!line || typeof line !== 'object') {
          continue; // a malformed line never breaks the export
        }
        rows.push(
          [
            csvTextField(supplier),
            csvTextField(group.vendor_code),
            csvTextField(group.supplier_code),
            csvTextField(line.item_code),
            csvTextField(line.site_code),
            csvNumber(line.due_in_days),
            csvTextField(line.order_by),
            csvTextField(line.arrives_on),
            csvNumber(line.quantity),
            csvNumber(line.unit_cost),
            csvNumber(line.value),
            line.short_of_need === true ? 'yes' : 'no',
            csvTextField(line.arithmetic),
          ].join(',')
        );
      }
    }
    return rows.join('\r\n') + '\r\n';
  }

  // The "Download CSV" action: the file is built from the loaded
  // plan answer — no request — and saved through a Blob and an
  // object URL, the same temporary-<a download> click the API
  // download uses, with the anchor removed and the URL revoked
  // afterwards.
  function downloadPlanCsv() {
    if (!state.data) {
      return;
    }
    var asOf = state.data.as_of;
    var asOfText = asOf === null || asOf === undefined ? '' : String(asOf);
    var filename = 'order-plan-' + asOfText + '-' + state.horizonDays + 'd.csv';
    var blob = new Blob([planCsvText(state.data)], { type: 'text/csv' });
    var url = URL.createObjectURL(blob);
    var anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  /* ------------------------------ events ------------------------------- */

  // A checkbox's change: the tick into (or out of) the block's
  // chosen table, by the block's supplier code (the top-ups
  // section's marker) and the row's item and site (the checkbox's
  // own markers); the moved tick closes the block's release, the
  // way "Cancel" does it — no request, and the preview's answer is
  // dropped when it lands if the preview's request is still out —
  // then a re-render: the running line follows. An identity that is
  // not resolved to admin fails closed: the checkbox renders for an
  // admin only.
  // A block whose own confirm is out the door does nothing: its
  // checkboxes are disabled then, and this guard stands behind the
  // disabled attribute — a tick in another block's checkbox is not
  // held, and it never touches the confirming block's release.
  function chooseTopUpRow(checkbox) {
    if (state.role !== ROLE_ADMIN) {
      return;
    }
    var wrap = checkbox.closest ? checkbox.closest('[data-supplier-code]') : null;
    if (!wrap) {
      return;
    }
    var code = wrap.getAttribute('data-supplier-code');
    if (code === null || code === '') {
      return;
    }
    if (blockConfirming(code)) {
      return; // a held confirm: the block's checkboxes do nothing
    }
    var item = checkbox.getAttribute('data-item') || '';
    var site = checkbox.getAttribute('data-site') || '';
    var table = state.chosenTopUps[code];
    if (!table) {
      table = state.chosenTopUps[code] = Object.create(null);
    }
    var key = topUpKey(item, site);
    if (checkbox.checked === true) {
      table[key] = true;
    } else {
      delete table[key];
    }
    closeRelease(code); // the moved tick closes the block's release
    render();
  }

  function onRootChange(event) {
    var target = event.target;
    if (!target || !target.getAttribute) {
      return;
    }
    if (target.getAttribute('data-testid') === 'orders-top-up-choose') {
      chooseTopUpRow(target);
      return;
    }
    if (target.getAttribute('data-testid') !== 'orders-horizon') {
      return;
    }
    if (anyConfirming()) {
      return; // a held confirm: the horizon change does nothing
    }
    var days = parseInt(target.value, 10);
    if (!isFinite(days) || days <= 0) {
      return;
    }
    state.horizonDays = days;
    state.release = Object.create(null); // a new horizon: every block's preview is stale
    load();
  }

  function onRootClick(event) {
    if (!event.target || !event.target.closest) {
      return;
    }
    if (event.target.closest('[data-testid="orders-release"]')) {
      startPreview(releaseCodeOf(event.target));
      return;
    }
    if (event.target.closest('[data-testid="orders-release-confirm"]')) {
      doConfirm(releaseCodeOf(event.target));
      return;
    }
    if (event.target.closest('[data-testid="orders-release-cancel"]')) {
      cancelRelease(releaseCodeOf(event.target));
      return;
    }
    if (event.target.closest('[data-testid="orders-download"]')) {
      downloadPlanCsv();
      return;
    }
    if (event.target.closest('[data-testid="orders-refresh"]')) {
      if (anyConfirming()) {
        return; // a held confirm: the refresh does nothing
      }
      load();
      return;
    }
    if (event.target.closest('[data-testid="orders-transfers-show"]')) {
      loadTransfers();
      return;
    }
    if (event.target.closest('[data-testid="orders-transfer-release"]')) {
      var transferBtn = event.target.closest('[data-testid="orders-transfer-release"]');
      startTransferPreview(
        transferBtn.getAttribute('data-item'),
        transferBtn.getAttribute('data-from'),
        transferBtn.getAttribute('data-to')
      );
      return;
    }
    if (event.target.closest('[data-testid="orders-transfer-confirm"]')) {
      confirmTransferRelease();
      return;
    }
    if (event.target.closest('[data-testid="orders-transfer-cancel"]')) {
      cancelTransferRelease();
      return;
    }
    if (event.target.closest('[data-testid="orders-retry"]')) {
      load();
    }
  }

  /* ------------------------------ lifecycle ---------------------------- */

  function viewActive() {
    var section = document.getElementById(VIEW_ID);
    return section ? section.classList.contains('is-active') : false;
  }

  // The sign-in's role, read off the probe the shell just gated. Only
  // a probe that is in the DOM can have been gated, so a missing probe
  // means the identity is not resolved: the release control stays off.
  function captureRole() {
    if (state.role !== ROLE_UNRESOLVED) {
      return; // already resolved for this session
    }
    var probe = rootEl ? rootEl.querySelector('.op-role-probe') : null;
    if (!probe) {
      return; // not resolved: fail closed
    }
    state.role = probe.hasAttribute('hidden') ? ROLE_OTHER : ROLE_ADMIN;
    render();
  }

  function onAppVisibility() {
    var appEl = document.getElementById(APP_ID);
    if (!appEl) {
      return;
    }
    if (appEl.hidden) {
      // Signed out: the identity is not resolved any more, and a stale
      // role must not outlive the session. Fail closed; the probe comes
      // back so the next sign-in's role can be read.
      if (state.role !== ROLE_UNRESOLVED) {
        state.role = ROLE_UNRESOLVED;
        render();
      }
      return;
    }
    captureRole();
    if (viewActive()) {
      activate();
    }
  }

  function activate() {
    if (!rootEl) {
      return;
    }
    if (!rootEl.firstElementChild) {
      render();
    }
    var appEl = document.getElementById(APP_ID);
    if (appEl && appEl.hidden) {
      // The shell is not signed in yet (Auth.bootstrap has not
      // completed): no request, the skeleton stays put.
      return;
    }
    load();
  }

  function reset() {
    seq += 1; // any answer still out the door belongs to a past session
    releaseGen += 1; // any release answer out the door belongs to a past session
    transfersSeq += 1; // any transfers answer out the door belongs to a past session
    transferReleaseGen += 1; // any transfer release answer out the door is past
    riskSeq += 1; // any risk answer out the door belongs to a past session
    topUpsSeq += 1; // any top-ups answer out the door belongs to a past session
    state.data = null;
    state.loading = false;
    state.error = null;
    state.horizonDays = DEFAULT_HORIZON_DAYS;
    state.role = ROLE_UNRESOLVED; // the identity is unknown again
    state.release = Object.create(null);
    state.chosenTopUps = Object.create(null); // the ticks are the session's
    topUpsPrev = null; // the previous answer is the session's
    state.transfers = null;
    state.transfersLoading = false;
    state.transfersError = null;
    state.transferRelease = null; // the panel's state is cleared
    state.risk = null;
    state.riskLoading = false;
    state.topUps = null;
    state.topUpsLoading = false;
    state.topUpsError = null;
    render();
  }

  (function boot() {
    rootEl = document.getElementById(ROOT_ID);
    if (!rootEl) {
      return;
    }
    rootEl.addEventListener('change', onRootChange);
    rootEl.addEventListener('click', onRootClick);
    render();

    var appEl = document.getElementById(APP_ID);
    if (!appEl) {
      return;
    }
    // Sign-in shows #app: when it does and the orders view is the
    // active one, the plan loads then — the pre-sign-in activation
    // (the shell shows the hash's view before Auth.bootstrap has
    // completed) could not fetch.
    var observer = new MutationObserver(function () {
      onAppVisibility();
    });
    observer.observe(appEl, { attributes: true, attributeFilter: ['hidden'] });
    if (!appEl.hidden) {
      onAppVisibility();
    }
  })();

  global.Orders = {
    activate: activate,
    reset: reset,
  };
})(window);
