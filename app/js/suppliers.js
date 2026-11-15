// AIERP planner screen — the Suppliers view (#/suppliers).
//
// #/suppliers shows how reliably each supplier delivers by the
// promised date: one row per supplier, in the order GET
// /api/suppliers/performance?window_days=<n> returns them. Above the
// table a window select ([data-testid="suppliers-window"]) offers
// "Last 90 days", "Last 180 days", "Last 365 days" (the default) and
// "Last 730 days"; choosing a window re-fetches with it, and so does
// activating the view; while a fetch is out a skeleton shows, and a
// request-sequence guard renders only the newest answer.
//
// The table ([data-testid="suppliers-table"]) scrolls within its own
// region: Supplier (supplier_name, with vendor_code beneath it in the
// secondary ink token; the vendor code without a name; the supplier
// code as today without either), Receipts, On time (the rate as a
// whole percent with "(<on_time> of <with_promise>)" after it,
// "No promised dates" when the rate is null; below 80% the figure
// gets a leading ▼ marker and the risk colour tokens), Late, Avg
// days late, Max days late (an em dash when null), Avg lead time (d,
// one decimal), P90 lead time (d) and Last receipt. With no suppliers
// an empty note shows ("No receipts in this window."); a 422 shows
// the server's detail inline with a Retry; any other failure shows
// "Supplier performance could not be loaded." with a Retry. Every
// API value is inserted as text, never as markup. Sign-out resets
// the view to its first-load state.
//
// Below the table the "Supplier terms" section
// ([data-testid="suppliers-terms"]) offers a select
// ([data-testid="suppliers-terms-supplier"]) — first "Choose a
// supplier" (an empty value), then one option per row of the current
// performance answer that has a non-empty supplier_code, in the
// table's order, each labelled with the supplier name, else the
// vendor code, else the supplier code, valued by the supplier_code —
// and a "Show terms" button ([data-testid="suppliers-terms-show"]).
// The section is not shown while the current answer carries no such
// rows. Only a click with a supplier chosen sends GET
// /api/supplier-terms?supplier_code=<code> (URL-encoded) through
// app/js/api.js; activating the view, changing the window and a
// click with nothing chosen send nothing. The answer renders as a
// table ([data-testid="suppliers-terms-table"]) — one row per terms
// row in the answer's order, columns Item, Minimum, Lot size,
// Maximum, Lead time (d) and Default — with null numbers as em
// dashes, numbers without trailing zeros, "Yes" where is_default is
// true and a caption "<n> items for <the option's text>" (the
// option's text is captured with the answer, so the caption tracks
// the fetched answer, not the select's current choice); the rows
// never carry the performance table's sp-row class. An empty answer
// reads "No ordering terms are stored for this supplier."; a failed
// request shows "Supplier terms could not be loaded." in
// [data-testid="suppliers-terms-error"] and leaves the performance
// table unchanged. The chosen supplier, the answer, the error and a
// pending flag live in module state and survive a re-render (a
// window change); sign-out clears them, and an answer arriving after
// sign-out or after a newer click is discarded.
//
// Between the performance table and the terms the "Supplier risk"
// section ([data-testid="suppliers-risk"]) shows the 730-day risk
// rating: one read of GET /api/supplier-risk per load of the screen
// (the view's activation), beside the performance read, under its own
// request-sequence guard — a late answer from an older load or from
// before sign-out is dropped, sign-out clears it, and a window change
// re-fetches only the performance answer, leaving the section's state
// alone. The table ([data-testid="suppliers-risk-table"]) carries one
// row per supplier in the answer's order
// ([data-testid="suppliers-risk-row"] with data-supplier-code):
// Supplier (supplier_name, else the supplier_code), Risk (a badge
// [data-testid="suppliers-risk-level"] reading High, Medium or Low,
// with the class is-high, is-medium or is-low), Why (the true signs
// joined by a comma and a space — Late deliveries, Variable lead
// times, Only supplier of <N> item(s), the word item when N is 1 — or
// an em dash with no sign), On time (the rate as a whole percent, or
// an em dash when null) and Lead-time variation (lead_time_cv to 2
// places, or an em dash when null). While the read is out a short
// skeleton shows (never the word "Loading"); a failed or malformed
// answer shows "Supplier risk could not be loaded." in
// [data-testid="suppliers-risk-error"], an empty list "No supplier
// has receipts to rate yet." in
// [data-testid="suppliers-risk-empty"] — a failure here leaves the
// performance table and the terms exactly as they are. Every API
// value is inserted as text, never as markup.
//
// A risk row whose supplier carries a non-empty list of sole-source
// item codes (sole_source_item_codes, a list of strings) offers a
// "Show items" button ([data-testid="suppliers-risk-items-toggle"])
// in its Why cell, after the text: pressing it inserts a row
// directly below the row ([data-testid="suppliers-risk-items"])
// whose single cell spans the table and lists the codes in the
// answer's order, joined by a comma and a space, and the button
// reads "Hide items" with aria-expanded "true"; pressing it again
// removes the row and restores the button. Each row toggles on its
// own — opening one leaves every other row exactly as it is,
// even when two rows carry the same supplier code — and the open
// rows live in the risk section's state, keyed by the row's
// position in the answer: a performance re-render keeps them, while
// a new risk answer, a view re-activation and sign-out close them
// all. A supplier whose list
// is empty, missing or not a list of strings shows no button and its
// row is exactly as before, and the toggle sends no request.
// Below the risk section the "Second sources" section
// ([data-testid="suppliers-second"], heading "Second sources") shows
// the other suppliers of the sole-source items: one read of GET
// /api/second-sources per load of the screen, in the same load call
// as the risk read, under its own request-sequence guard — a late
// answer from an older load or from before sign-out is dropped, and
// sign-out clears it. The table
// ([data-testid="suppliers-second-table"]) carries one row per entry
// of the answer's items array, in the answer's order
// ([data-testid="suppliers-second-row"]): Item (the item_code, the
// description beneath it when present), Only supplier (the
// supplier_name, else the supplier_code, then the level as High
// risk, Medium risk or Low risk, Unrated when the level is null),
// Candidates and Stock to hold. Each candidate is one element
// ([data-testid="suppliers-second-candidate"]) reading
// "<name> (<risk>, <basis>)", in the answer's order: the name the
// supplier_name else the supplier_code, the risk Low risk, Medium
// risk or High risk, Unrated when null, the basis "terms, <N> days"
// for a terms candidate whose lead_time_days is a number above 0,
// "terms" for any other terms candidate, "<N> similar items" (the
// word item when it is 1) for a similar candidate; an entry with an
// empty candidates list shows one element
// ([data-testid="suppliers-second-none"]) reading "No other supplier
// found". The hold cell ([data-testid="suppliers-second-hold"])
// reads "Hold <bridge_units>, short <bridge_short_units>" with a
// title of the bridge_arithmetic when bridge_units is a number, an
// em dash otherwise. While the read is out a short skeleton shows
// (never the word "Loading"); a failed or malformed answer shows
// "Second sources could not be loaded." in
// [data-testid="suppliers-second-error"], an empty items array "No
// item relies on a single supplier." in
// [data-testid="suppliers-second-empty"] — a failure here leaves
// the performance table, the risk section and the terms exactly as
// they are. The screen does not coerce numeric strings:
// lead_time_days and bridge_units count as numbers only when they
// are JSON numbers. Every API value is inserted as text, never as
// markup.
//
// While the section shows its table, a summary line
// ([data-testid="suppliers-second-summary"]) stands directly above
// it, reading "<N> items rely on one supplier; <M> are short of
// stock.": N the number of entries, M the number of entries whose
// bridge_short_units is a JSON number above 0 — the word item with
// "relies" when N is 1, "is short" when M is 1. Beside the summary a
// checkbox ([data-testid="suppliers-second-short-only"], label "Only
// items short of stock", unchecked by default) filters the table to
// the short entries, in the answer's order; toggling it re-renders
// from the answer in hand and sends no request. Checked while no
// entry is short, the table is replaced by the note
// [data-testid="suppliers-second-none-short"] reading "No item is
// short of stock." and the summary stays. The filter lives in the
// section's state, so a re-render (a window change) keeps it and the
// filtered table stays correct; a new second-sources answer (either
// settled path), a view re-activation, the screen reset and sign-out
// uncheck it.

// The screen's last section, below the supplier terms, is the
// "Order minimums" section ([data-testid="suppliers-minimums"]).
// On each load of the screen (the view's activation), in the same
// load call as the risk and second-source reads, the section sends
// one GET /api/supplier-order-minimums, one GET /api/suppliers and
// one GET /api/auth/me, all through app/js/api.js — each with its
// own request-sequence guard: an answer from an older load or from
// before sign-out is dropped, sign-out clears what they stored, and
// a window change reads none of them again. The table
// ([data-testid="suppliers-minimums-table"]) carries one row
// ([data-testid="suppliers-minimum-row"], with data-source-system
// and data-supplier-code) per stored minimum, in the answer's order:
// Supplier (the label of the GET /api/suppliers entry with the same
// source_system and supplier_code — its name, else its vendor_code,
// else its supplier_code — else the minimum's supplier_code when the
// suppliers read failed or is out), Source (the source_system),
// Minimum (the amount in dollars and cents with thousands
// separators, "$2,500.00") and Updated (the first ten characters
// of updated_at, " by " and updated_by). While the list read is out
// a short skeleton shows (never the word "Loading"); an empty list
// reads "No order minimums are stored."
// ([data-testid="suppliers-minimums-empty"]); a failed or
// malformed answer — anything but an array, or an array carrying a
// malformed row — reads "Order minimums could not be loaded."
// ([data-testid="suppliers-minimums-error"]) and renders no rows.
// Whether the viewer is an admin is read from GET /api/auth/me's
// roles exactly as app/js/buffers.js reads it: an unknown or failed
// read fails closed.
//
// For an admin, and only an admin, the section ends with the save
// form ([data-testid="suppliers-minimum-form"]) below the table —
// the supplier select ([data-testid="suppliers-minimum-supplier"])
// with "Choose a supplier" (an empty value) first, then one option
// per GET /api/suppliers entry in its order reading "<label>
// (<source_system>)"; the amount input
// ([data-testid="suppliers-minimum-amount"], type number, step
// 0.01); the "Save minimum" button
// ([data-testid="suppliers-minimum-save"]) and the message line
// ([data-testid="suppliers-minimum-message"]) — and each table row
// ends with a "Remove" button ([data-testid="suppliers-minimum-remove"]).
// Choosing a supplier that already has a stored minimum puts that
// amount in the amount input. Saving with no supplier chosen, or an
// amount that is not a number above 0 and at most
// 1,000,000,000,000, reads "Choose a supplier and an amount above
// 0." in the message and sends nothing; otherwise the button sends
// exactly one PUT /api/supplier-order-minimums with the chosen pair
// and the number, disabled while it is out: a 200 reads "Saved the
// minimum for <label> (<source_system>): <the amount in dollars and
// cents>." and sends one more list read, whose answer replaces the
// table; any other answer reads "The minimum could not be saved."
// and sends nothing more. A Remove click sends exactly one DELETE
// /api/supplier-order-minimums/<the row's id>, disabled while it is
// out: a 204 reads "Removed the minimum for <the row's Supplier
// text> (<source_system>)." and sends one more list read; any other
// answer reads "The minimum could not be removed.". A save or
// remove answer that lands after sign-out is dropped, and the screen
// sends no other POST, PUT or DELETE. If the minimums list read
// fails but the suppliers read succeeds, the admin form stays
// enabled; a failed suppliers read shows "Suppliers could not be
// loaded." in the form, with no select and no button. Every value
// is inserted as text, never as markup.

(function (global) {
  'use strict';

  var LIST_PATH = '/api/suppliers/performance';
  var TERMS_PATH = '/api/supplier-terms';
  var RISK_PATH = '/api/supplier-risk';
  var SECOND_PATH = '/api/second-sources';
  var MIN_PATH = '/api/supplier-order-minimums';
  var SUPPLIERS_PATH = '/api/suppliers';
  var ME_PATH = '/api/auth/me';
  var ROOT_ID = 'suppliers-root';
  var VIEW_ID = 'view-suppliers';
  var APP_ID = 'app';

  var EM_DASH = '\u2014';
  var GENERIC_ERROR = 'Supplier performance could not be loaded.';
  var EMPTY_NOTE = 'No receipts in this window.';
  var TERMS_GENERIC_ERROR = 'Supplier terms could not be loaded.';
  var TERMS_EMPTY_NOTE = 'No ordering terms are stored for this supplier.';
  var RISK_GENERIC_ERROR = 'Supplier risk could not be loaded.';
  var RISK_EMPTY_NOTE = 'No supplier has receipts to rate yet.';
  var SECOND_GENERIC_ERROR = 'Second sources could not be loaded.';
  var SECOND_EMPTY_NOTE = 'No item relies on a single supplier.';
  var SECOND_NONE_NOTE = 'No other supplier found';
  var SECOND_NONE_SHORT_NOTE = 'No item is short of stock.';
  var NO_PROMISED = 'No promised dates';
  var MIN_GENERIC_ERROR = 'Order minimums could not be loaded.';
  var MIN_EMPTY_NOTE = 'No order minimums are stored.';
  var SUPPLIERS_GENERIC_ERROR = 'Suppliers could not be loaded.';
  var MIN_INVALID_MESSAGE = 'Choose a supplier and an amount above 0.';
  var MIN_SAVE_FAILED = 'The minimum could not be saved.';
  var MIN_REMOVE_FAILED = 'The minimum could not be removed.';
  // The largest amount the API stores: 1,000,000,000,000.
  var MIN_AMOUNT_MAX = 1000000000000;

  // The on-time rate below this gets the marker and the risk tokens.
  var RISK_RATE = 0.8;

  // The windows the select offers, in display order; 365 (a year) is
  // the default and matches the server's own default.
  var WINDOWS = [
    { days: 90, label: 'Last 90 days' },
    { days: 180, label: 'Last 180 days' },
    { days: 365, label: 'Last 365 days' },
    { days: 730, label: 'Last 730 days' },
  ];
  var DEFAULT_WINDOW_DAYS = 365;

  var rootEl = null;

  var state = {
    data: null, // the newest successful answer: { as_of, window_days, suppliers }
    loading: false, // a fetch is out
    error: null, // the newest failed load: { status, message }
    windowDays: DEFAULT_WINDOW_DAYS,
  };

  // The Supplier terms section's own state: the chosen supplier's
  // code, the option text its answer was fetched for, the newest
  // answer (an array of terms rows), the newest failure and a
  // pending flag. It survives a re-render (a window change re-fetches
  // only the performance answer); sign-out clears it.
  var termsState = {
    supplierCode: null,
    // The option text the current answer was fetched for: the caption
    // names this supplier, so it tracks the fetched answer rather
    // than the select's current choice.
    label: null,
    answer: null,
    error: null,
    loading: false,
  };

  // The Supplier risk section's own state: the newest answer (an
  // object with a suppliers array), the newest failure and a pending
  // flag. The rating covers the 730-day window, so a window change
  // re-fetches only the performance answer and leaves it alone;
  // sign-out clears it.
  var riskState = {
    answer: null,
    error: null,
    loading: false,
    // The rows (by position in the risk answer's suppliers list)
    // whose item rows are open; a performance re-render keeps them,
    // a new risk answer, a view re-activation or sign-out closes
    // them all. Keyed by position, not by supplier code: two rows
    // can share one code and must still toggle on their own.
    expanded: [],
  };

  // The Second sources section's own state: the newest answer (an
  // object with an items array), the newest failure and a pending
  // flag. It rides the same load as the risk read, so a window
  // change re-fetches only the performance answer and leaves it
  // alone; sign-out clears it.
  var secondState = {
    answer: null,
    error: null,
    loading: false,
    // The "Only items short of stock" filter: client-side only, it
    // re-renders the section's table from the answer in hand and
    // sends no request. A new second-sources answer (either settled
    // path), a view re-activation, the screen reset and sign-out
    // uncheck it; a re-render (a window change) keeps it.
    shortOnly: false,
  };

  // The Order minimums section's own state: the newest answer (an
  // array of stored minimums), the newest failure and a pending
  // flag. The read rides every load of the screen, like the risk
  // and second-sources reads, so a window change re-fetches only
  // the performance answer and leaves it alone; sign-out clears it.
  var minState = {
    answer: null,
    error: null,
    loading: false,
  };

  // The suppliers list the section labels its rows and the form's
  // options from: the newest GET /api/suppliers answer (an array),
  // the newest failure and a pending flag. A failed read leaves the
  // Supplier column showing codes; sign-out clears it.
  var suppliersState = {
    answer: null,
    error: null,
    loading: false,
  };

  // Whether the viewer is an admin, read from GET /api/auth/me's
  // roles exactly as app/js/buffers.js reads them: only a positive
  // answer with "admin" in the user's roles shows the form and the
  // rows' Remove buttons; an unknown or failed read fails closed.
  var minAdmin = false;

  // The admin form's own state: the chosen option's value, the
  // amount input's text, the save/remove pending flags and the
  // message line's text and kind. It survives a re-render (a window
  // change); sign-out clears it.
  var minFormState = {
    chosen: '',
    amountText: '',
    saving: false,
    removingId: null,
    message: null,
  };

  // The request-sequence guard: only the newest fetch's answer renders.
  var seq = 0; // the performance fetches
  var termsSeq = 0; // the terms fetches
  var riskSeq = 0; // the risk fetches
  var secondSeq = 0; // the second-sources fetches
  var minSeq = 0; // the order-minimums list fetches
  var suppliersSeq = 0; // the suppliers fetches
  var meSeq = 0; // the admin reads
  var writeGen = 0; // the save/remove answers: a reset drops them

  /* ------------------------------ helpers ------------------------------ */

  function esc(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function isNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }

  // A whole figure with separators (5 -> "5"); an em dash when the
  // server carries no usable figure.
  function wholeNumber(value) {
    if (typeof value === 'string') {
      value = value === '' ? NaN : Number(value);
    }
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return Math.round(value).toLocaleString('en-US');
  }

  // A figure with exactly one decimal place (4 -> "4.0"); an em dash
  // when the server carries no usable figure.
  function oneDecimal(value) {
    if (typeof value === 'string') {
      value = value === '' ? NaN : Number(value);
    }
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return value.toFixed(1);
  }

  function nonEmptyString(value) {
    return typeof value === 'string' && value !== '' ? value : null;
  }

  // A figure without trailing zeros (300 -> "300", 12.5 -> "12.5");
  // an em dash when the server carries no usable figure.
  function plainNumber(value) {
    var number = value;
    if (typeof number === 'string') {
      number = number === '' ? NaN : Number(number);
    }
    if (typeof number !== 'number' || !isFinite(number)) {
      return EM_DASH;
    }
    return String(number);
  }

  function suppliersOf(data) {
    return data && Array.isArray(data.suppliers) ? data.suppliers : [];
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

  // The On time cell: the rate as a whole percent with its basis,
  // "No promised dates" when the rate is null. Below the risk rate
  // the figure gets the leading marker and the risk colour tokens.
  function rateFigure(supplier) {
    var rate =
      typeof supplier.on_time_rate === 'number' && isFinite(supplier.on_time_rate)
        ? supplier.on_time_rate
        : null;
    if (rate === null) {
      return { text: NO_PROMISED, risk: false };
    }
    var percent = Math.round(rate * 100);
    var basis =
      wholeNumber(supplier.on_time) + ' of ' + wholeNumber(supplier.with_promise);
    return {
      text: percent + '% (' + basis + ')',
      risk: rate < RISK_RATE,
    };
  }

  // A well-formed risk answer: an object (never an array or a bare
  // value) with a suppliers array. Anything else is a failed read.
  function riskAnswerValid(payload) {
    return (
      payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      Array.isArray(payload.suppliers)
    );
  }

  // A well-formed second-sources answer: an object (never an array
  // or a bare value) with an items array. Anything else is a failed
  // read.
  function secondAnswerValid(payload) {
    return (
      payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      Array.isArray(payload.items)
    );
  }

  // The badge for the answer's risk value: High/Medium/Low with its
  // colour class; null when the value is outside the three, which no
  // badge can read.
  function riskLevel(value) {
    if (value === 'high') {
      return { text: 'High', cls: 'is-high' };
    }
    if (value === 'medium') {
      return { text: 'Medium', cls: 'is-medium' };
    }
    if (value === 'low') {
      return { text: 'Low', cls: 'is-low' };
    }
    return null;
  }

  // The row's true signs in display order — the late sign, the
  // variable sign and the sole-source sign with its count (the word
  // item singular when the count is 1); an empty list with no sign.
  function riskSigns(supplier) {
    var signs = [];
    if (supplier.late === true) {
      signs.push('Late deliveries');
    }
    if (supplier.variable === true) {
      signs.push('Variable lead times');
    }
    if (supplier.sole_source === true) {
      var items = soleSourceItems(supplier);
      signs.push('Only supplier of ' + items + (items === 1 ? ' item' : ' items'));
    }
    return signs;
  }

  // The sole-source count as a whole number: the answer carries it as
  // a number or a numeric string; 0 when it carries no usable figure.
  function soleSourceItems(supplier) {
    var items = supplier.sole_source_items;
    if (typeof items === 'string') {
      items = items === '' ? NaN : Number(items);
    }
    if (!isNumber(items)) {
      return 0;
    }
    return Math.round(items);
  }

  // The sole-source item codes for the row's toggle: the answer
  // carries them as a list of strings; null when the list is empty,
  // missing or not a list of strings — that row shows no button.
  function soleSourceItemCodes(supplier) {
    var codes = supplier.sole_source_item_codes;
    if (!Array.isArray(codes) || codes.length === 0) {
      return null;
    }
    for (var i = 0; i < codes.length; i++) {
      if (typeof codes[i] !== 'string') {
        return null;
      }
    }
    return codes;
  }

  // The row's item list is open when the row's position in the risk
  // answer's suppliers list is among the risk state's expanded
  // rows. The position is the key, not the supplier code: two rows
  // can share one code and must still toggle on their own.
  function isRiskItemsExpanded(rowIndex) {
    return riskState.expanded.indexOf(rowIndex) !== -1;
  }

  // The rate as a whole percent ("62%"), an em dash when the answer
  // carries no usable rate.
  function riskRateFigure(supplier) {
    var rate = supplier.on_time_rate;
    if (typeof rate === 'string') {
      rate = rate === '' ? NaN : Number(rate);
    }
    if (!isNumber(rate)) {
      return EM_DASH;
    }
    return Math.round(rate * 100) + '%';
  }

  // The lead-time variation to 2 places, an em dash when the answer
  // carries no usable figure.
  function riskCvFigure(supplier) {
    var cv = supplier.lead_time_cv;
    if (typeof cv === 'string') {
      cv = cv === '' ? NaN : Number(cv);
    }
    if (!isNumber(cv)) {
      return EM_DASH;
    }
    return cv.toFixed(2);
  }

  // The risk level as section text: High risk, Medium risk or Low
  // risk, and Unrated when the answer carries no level (or one no
  // cell can read).
  function secondRiskText(value) {
    if (value === 'high') {
      return 'High risk';
    }
    if (value === 'medium') {
      return 'Medium risk';
    }
    if (value === 'low') {
      return 'Low risk';
    }
    return 'Unrated';
  }

  // The candidate's basis: "terms, <N> days" for a terms candidate
  // whose lead_time_days is a JSON number above 0, "terms" for any
  // other terms candidate, "<N> similar items" (the word item
  // singular when it is 1) for a similar candidate. The screen does
  // not coerce numeric strings: only JSON numbers count.
  function secondBasisText(candidate) {
    if (candidate.basis === 'terms') {
      var lead = candidate.lead_time_days;
      if (typeof lead === 'number' && isFinite(lead) && lead > 0) {
        return 'terms, ' + lead + ' days';
      }
      return 'terms';
    }
    if (candidate.basis === 'similar') {
      var similar = candidate.similar_items;
      if (typeof similar === 'number' && isFinite(similar)) {
        return similar + ' similar item' + (similar === 1 ? '' : 's');
      }
      return 'similar items';
    }
    return 'terms';
  }

  // A second-sources entry is short when its bridge_short_units is a
  // JSON number above 0 — the screen does not coerce numeric
  // strings, and a malformed entry is never short.
  function secondIsShort(entry) {
    if (!entry || typeof entry !== 'object') {
      return false;
    }
    var shortUnits = entry.bridge_short_units;
    return (
      typeof shortUnits === 'number' && isFinite(shortUnits) && shortUnits > 0
    );
  }

  // The summary's M: how many of the answer's entries are short.
  function secondShortCount(items) {
    var count = 0;
    for (var i = 0; i < items.length; i++) {
      if (secondIsShort(items[i])) {
        count += 1;
      }
    }
    return count;
  }

  // The summary line's text: "<N> items rely on one supplier; <M>
  // are short of stock." — "item relies" when N is 1, "is short"
  // when M is 1.
  function secondSummaryText(items) {
    var n = items.length;
    var m = secondShortCount(items);
    return (
      n + (n === 1 ? ' item relies' : ' items rely') +
      ' on one supplier; ' +
      m + (m === 1 ? ' is short of stock' : ' are short of stock') + '.'
    );
  }

  // The summary line with the short-only filter beside it: the line
  // stands directly above the table while the section shows it, and
  // the checkbox renders from the section's state, so a re-render
  // keeps the filter as the user left it.
  function secondSummaryBarHtml(items) {
    return (
      '<div class="sp-second-summary-bar">' +
      '<p class="sp-second-summary" data-testid="suppliers-second-summary">' +
      esc(secondSummaryText(items)) +
      '</p>' +
      '<label class="sp-second-short-only" for="sp-second-short-only">' +
      '<input type="checkbox" id="sp-second-short-only"' +
      ' data-testid="suppliers-second-short-only"' +
      (secondState.shortOnly ? ' checked' : '') + '>' +
      '<span class="sp-second-short-only-text">Only items short of stock</span>' +
      '</label>' +
      '</div>'
    );
  }

  /* --------------------- the order-minimums helpers -------------------- */

  // One key per (source_system, supplier_code) pair: both sides
  // stringified the same way and percent-encoded, so a key can
  // never hold a delimiter of its own. The suppliers label map,
  // the section's rows and the form's options all key off it.
  function supplierKey(entry) {
    return (
      encodeURIComponent(String(entry.source_system)) +
      '/' +
      encodeURIComponent(String(entry.supplier_code))
    );
  }

  // A supplier's label: its name, else its vendor_code, else its
  // supplier_code; an empty string when the entry carries none.
  function supplierLabel(entry) {
    return (
      nonEmptyString(entry.name) ||
      nonEmptyString(entry.vendor_code) ||
      nonEmptyString(entry.supplier_code) ||
      ''
    );
  }

  // The label map of the suppliers answer: one entry per well-formed
  // row, keyed by supplierKey. An empty map while the read is out or
  // failed — the Supplier column then falls back to codes.
  function supplierMap() {
    var map = {};
    if (!Array.isArray(suppliersState.answer)) {
      return map;
    }
    for (var i = 0; i < suppliersState.answer.length; i++) {
      var entry = suppliersState.answer[i];
      if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
        continue; // a malformed entry never breaks the map
      }
      map[supplierKey(entry)] = supplierLabel(entry);
    }
    return map;
  }

  // The suppliers entry a chosen form option stands for, by the
  // option's key; null when the settled list carries no such entry —
  // a choice the list no longer carries is a choice of nothing.
  function chosenSupplierEntry() {
    if (minFormState.chosen === '' || !Array.isArray(suppliersState.answer)) {
      return null;
    }
    for (var i = 0; i < suppliersState.answer.length; i++) {
      var entry = suppliersState.answer[i];
      if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
        continue;
      }
      if (supplierKey(entry) === minFormState.chosen) {
        return entry;
      }
    }
    return null;
  }

  // A well-formed minimums row: an object (never an array or a bare
  // value) carrying every field a cell or the Remove button reads.
  // Anything else is a malformed row.
  function minimumRowValid(row) {
    return (
      row &&
      typeof row === 'object' &&
      !Array.isArray(row) &&
      isNumber(row.id) &&
      typeof row.source_system === 'string' &&
      typeof row.supplier_code === 'string' &&
      isNumber(row.amount) &&
      typeof row.updated_at === 'string' &&
      typeof row.updated_by === 'string'
    );
  }

  // A well-formed list answer: an array of well-formed rows.
  // Anything else — and an array carrying a malformed row — is a
  // failed read.
  function minimumsAnswerValid(payload) {
    if (!Array.isArray(payload)) {
      return false;
    }
    for (var i = 0; i < payload.length; i++) {
      if (!minimumRowValid(payload[i])) {
        return false;
      }
    }
    return true;
  }

  // A dollar figure with cents and thousands separators (2500 ->
  // "$2,500.00"); an em dash when the value is not a finite number.
  function dollarsCents(amount) {
    if (!isNumber(amount)) {
      return EM_DASH;
    }
    return (
      '$' +
      amount.toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    );
  }

  // The amount input's text as a savable number: a finite number
  // above 0 and at most MIN_AMOUNT_MAX; null when the text is empty,
  // not a number, or outside the range.
  function parseAmount(text) {
    if (typeof text !== 'string' || text.trim() === '') {
      return null;
    }
    var value = Number(text);
    if (!isFinite(value) || value <= 0 || value > MIN_AMOUNT_MAX) {
      return null;
    }
    return value;
  }

  // The stored minimum for a chosen form option, by the option's
  // key; null when the settled list carries no such row.
  function storedMinimumFor(chosen) {
    if (chosen === '' || !Array.isArray(minState.answer)) {
      return null;
    }
    for (var i = 0; i < minState.answer.length; i++) {
      var row = minState.answer[i];
      if (row && supplierKey(row) === chosen) {
        return row;
      }
    }
    return null;
  }

  /* ------------------------------ loading ------------------------------ */

  function load() {
    seq += 1;
    var mySeq = seq;
    state.loading = true;
    state.error = null;
    render();
    global.Api.get(LIST_PATH + '?window_days=' + encodeURIComponent(state.windowDays)).then(
      function (payload) {
        if (mySeq !== seq) {
          return; // a newer fetch is out: this answer is stale
        }
        state.loading = false;
        if (!answerValid(payload)) {
          state.error = { status: 0, message: GENERIC_ERROR };
        } else {
          state.data = payload;
        }
        render();
      },
      function (error) {
        if (mySeq !== seq) {
          return;
        }
        state.loading = false;
        var status = error && typeof error.status === 'number' ? error.status : 0;
        if (status === 422) {
          // The server's detail, when it carries one.
          state.error = {
            status: status,
            message:
              error && typeof error.message === 'string' && error.message !== ''
                ? error.message
                : GENERIC_ERROR,
          };
        } else {
          state.error = { status: status, message: GENERIC_ERROR };
        }
        render();
      }
    );
  }

  // The on-demand terms fetch: only a "Show terms" click with a
  // chosen supplier ever sends it. A sequence guard renders only the
  // newest answer — one arriving after sign-out (reset bumped
  // termsSeq) or after a newer click is discarded.
  function loadTerms(supplierCode, label) {
    termsSeq += 1;
    var mySeq = termsSeq;
    termsState.supplierCode = supplierCode;
    termsState.label = null;
    termsState.loading = true;
    termsState.error = null;
    render();
    global.Api.get(TERMS_PATH + '?supplier_code=' + encodeURIComponent(supplierCode)).then(
      function (payload) {
        if (mySeq !== termsSeq) {
          return; // sign-out or a newer click: this answer is stale
        }
        termsState.loading = false;
        if (Array.isArray(payload)) {
          termsState.answer = payload;
          // The caption's supplier line is captured with the answer:
          // it names the supplier this answer was fetched for.
          termsState.label = label;
        } else {
          termsState.answer = null;
          termsState.error = { status: 0, message: TERMS_GENERIC_ERROR };
        }
        render();
      },
      function (error) {
        if (mySeq !== termsSeq) {
          return;
        }
        termsState.loading = false;
        termsState.answer = null;
        termsState.label = null;
        termsState.error = {
          status: error && typeof error.status === 'number' ? error.status : 0,
          message: TERMS_GENERIC_ERROR,
        };
        render();
      }
    );
  }

  // The risk read: one GET /api/supplier-risk per load of the screen
  // (the view's activation), beside the performance read. A sequence
  // guard renders only the newest answer — one arriving after sign-out
  // (reset bumped riskSeq) or after a newer load is discarded. A
  // failed or malformed answer shows the section's own error and
  // leaves the performance table and the terms untouched; a settled
  // read — the answer or a failure — closes every open item list.
  function loadRisk() {
    riskSeq += 1;
    var mySeq = riskSeq;
    riskState.loading = true;
    riskState.error = null;
    render();
    global.Api.get(RISK_PATH).then(
      function (payload) {
        if (mySeq !== riskSeq) {
          return; // sign-out or a newer load: this answer is stale
        }
        riskState.loading = false;
        riskState.expanded = []; // the new risk answer closes every open list
        if (!riskAnswerValid(payload)) {
          riskState.answer = null;
          riskState.error = { status: 0, message: RISK_GENERIC_ERROR };
        } else {
          riskState.answer = payload;
        }
        render();
      },
      function (error) {
        if (mySeq !== riskSeq) {
          return;
        }
        riskState.loading = false;
        riskState.answer = null;
        riskState.error = {
          status: error && typeof error.status === 'number' ? error.status : 0,
          message: RISK_GENERIC_ERROR,
        };
        riskState.expanded = []; // the failed read closes every open list
        render();
      }
    );
  }

  // The second-sources read: one GET /api/second-sources per load of
  // the screen, in the same load call as the risk read. A sequence
  // guard renders only the newest answer — one arriving after
  // sign-out (reset bumped secondSeq) or after a newer load is
  // discarded. A failed or malformed answer shows the section's own
  // error and leaves the performance table, the risk section and
  // the terms untouched.
  function loadSecondSources() {
    secondSeq += 1;
    var mySeq = secondSeq;
    secondState.loading = true;
    secondState.error = null;
    render();
    global.Api.get(SECOND_PATH).then(
      function (payload) {
        if (mySeq !== secondSeq) {
          return; // sign-out or a newer load: this answer is stale
        }
        secondState.loading = false;
        secondState.shortOnly = false; // a new answer unchecks the filter
        if (!secondAnswerValid(payload)) {
          secondState.answer = null;
          secondState.error = { status: 0, message: SECOND_GENERIC_ERROR };
        } else {
          secondState.answer = payload;
        }
        render();
      },
      function (error) {
        if (mySeq !== secondSeq) {
          return;
        }
        secondState.loading = false;
        secondState.shortOnly = false; // so does a failed read
        secondState.answer = null;
        secondState.error = {
          status: error && typeof error.status === 'number' ? error.status : 0,
          message: SECOND_GENERIC_ERROR,
        };
        render();
      }
    );
  }

  // The order-minimums read: one GET /api/supplier-order-minimums
  // per load of the screen (the view's activation), in the same load
  // call as the risk and second-sources reads. A sequence guard
  // renders only the newest answer — one arriving after sign-out
  // (reset bumped minSeq) or after a newer load is discarded. A
  // failed or malformed answer — anything but an array of
  // well-formed rows — shows the section's own error and renders no
  // rows.
  function loadMinimums() {
    minSeq += 1;
    var mySeq = minSeq;
    minState.loading = true;
    minState.error = null;
    render();
    global.Api.get(MIN_PATH).then(
      function (payload) {
        if (mySeq !== minSeq) {
          return; // sign-out or a newer load: this answer is stale
        }
        minState.loading = false;
        if (!minimumsAnswerValid(payload)) {
          minState.answer = null;
          minState.error = { status: 0, message: MIN_GENERIC_ERROR };
        } else {
          minState.answer = payload;
        }
        render();
      },
      function (error) {
        if (mySeq !== minSeq) {
          return;
        }
        minState.loading = false;
        minState.answer = null;
        minState.error = {
          status: error && typeof error.status === 'number' ? error.status : 0,
          message: MIN_GENERIC_ERROR,
        };
        render();
      }
    );
  }

  // The suppliers read: one GET /api/suppliers per load of the
  // screen, beside the order-minimums read. The section labels its
  // rows and the form's options from the answer, in its order; a
  // failed or malformed answer (anything but an array) shows the
  // form's own message and leaves the Supplier column showing codes.
  function loadSuppliers() {
    suppliersSeq += 1;
    var mySeq = suppliersSeq;
    suppliersState.loading = true;
    suppliersState.error = null;
    render();
    global.Api.get(SUPPLIERS_PATH).then(
      function (payload) {
        if (mySeq !== suppliersSeq) {
          return; // sign-out or a newer load: this answer is stale
        }
        suppliersState.loading = false;
        if (!Array.isArray(payload)) {
          suppliersState.answer = null;
          suppliersState.error = {
            status: 0,
            message: SUPPLIERS_GENERIC_ERROR,
          };
        } else {
          suppliersState.answer = payload;
        }
        // A choice the new answer no longer carries is a choice of
        // nothing: clear it so the select and the input agree.
        if (minFormState.chosen !== '' && chosenSupplierEntry() === null) {
          minFormState.chosen = '';
          minFormState.amountText = '';
        }
        render();
      },
      function (error) {
        if (mySeq !== suppliersSeq) {
          return;
        }
        suppliersState.loading = false;
        suppliersState.answer = null;
        suppliersState.error = {
          status: error && typeof error.status === 'number' ? error.status : 0,
          message: SUPPLIERS_GENERIC_ERROR,
        };
        render();
      }
    );
  }

  // The admin read: one GET /api/auth/me per load of the screen,
  // reading the roles exactly as app/js/buffers.js reads them: only
  // a positive answer with "admin" in the user's roles shows the
  // form and the rows' Remove buttons; any failure fails closed.
  function loadMe() {
    meSeq += 1;
    var mySeq = meSeq;
    global.Api.get(ME_PATH).then(
      function (me) {
        if (mySeq !== meSeq) {
          return; // sign-out or a newer load: this answer is stale
        }
        minAdmin =
          !!me &&
          me.authenticated === true &&
          !!me.user &&
          Array.isArray(me.user.roles) &&
          me.user.roles.indexOf('admin') !== -1;
        render();
      },
      function () {
        if (mySeq !== meSeq) {
          return;
        }
        minAdmin = false; // a failed read fails closed
        render();
      }
    );
  }

  /* ------------------------------ rendering ---------------------------- */

  function render() {
    if (!rootEl) {
      return;
    }
    rootEl.innerHTML = shellHtml();
  }

  function shellHtml() {
    // The body: the skeleton on the first load and while a fetch is
    // out; the error, the empty note or the table once settled.
    var body;
    if (!state.data && !state.error) {
      body = skeletonHtml();
    } else if (state.loading) {
      body = skeletonHtml();
    } else if (state.error) {
      body = errorHtml();
    } else if (suppliersOf(state.data).length === 0) {
      body = emptyHtml();
    } else {
      body = tableHtml();
    }
    // The bar and the sections below the body render once a fetch is
    // out or has settled. The first in-flight load renders the risk
    // section and the second-sources section beside the skeleton —
    // each with its own short skeleton while its read is out, never
    // the word "Loading" — and the terms section,
    // which stands empty until a performance answer names suppliers;
    // with nothing out or settled (the pre-sign-in render, the
    // post-sign-out reset) the skeleton stands alone, as before.
    var bar = state.data || state.error ? barHtml() : '';
    var sections =
      state.data || state.error || state.loading
        ? riskSectionHtml() +
          secondSectionHtml() +
          termsSectionHtml() +
          minimumsSectionHtml()
        : '';
    return (
      '<div class="sp">' +
      bar +
      '<div class="sp-body">' + body + '</div>' +
      sections +
      '</div>'
    );
  }

  function barHtml() {
    var options = WINDOWS.map(function (window) {
      return (
        '<option value="' + window.days + '"' +
        (state.windowDays === window.days ? ' selected' : '') + '>' +
        esc(window.label) +
        '</option>'
      );
    }).join('');
    return (
      '<div class="sp-bar">' +
      '<label class="sp-window-label" for="suppliers-window">Window</label>' +
      '<select class="sp-window" id="suppliers-window" data-testid="suppliers-window"' +
      (state.loading ? ' disabled' : '') + '>' +
      options +
      '</select>' +
      '</div>'
    );
  }

  function skeletonHtml() {
    return (
      '<div class="sp-skel-wrap" data-testid="suppliers-skeleton" aria-hidden="true">' +
      '<div class="skel skel-line"></div>' +
      '<div class="skel skel-line skel-wide"></div>' +
      '<div class="skel sp-skel-block"></div>' +
      '</div>'
    );
  }

  function errorHtml() {
    return (
      '<div class="sp-error" role="alert" data-testid="suppliers-error">' +
      '<p class="sp-error-msg" data-testid="suppliers-error-msg">' +
        esc(state.error.message) +
      '</p>' +
      '<button type="button" class="btn btn-secondary" data-testid="suppliers-retry">Retry</button>' +
      '</div>'
    );
  }

  function emptyHtml() {
    return (
      '<p class="sp-empty" data-testid="suppliers-empty">' +
      esc(EMPTY_NOTE) +
      '</p>'
    );
  }

  function tableHtml() {
    var suppliers = suppliersOf(state.data);
    var rows = [];
    for (var i = 0; i < suppliers.length; i++) {
      var supplier = suppliers[i];
      if (!supplier || typeof supplier !== 'object') {
        continue; // a malformed row never breaks the table
      }
      rows.push(supplierHtml(supplier));
    }
    return (
      '<div class="sp-table-region">' +
      '<table class="sp-table" data-testid="suppliers-table">' +
      '<thead><tr>' +
      '<th scope="col">Supplier</th>' +
      '<th scope="col">Receipts</th>' +
      '<th scope="col">On time</th>' +
      '<th scope="col">Late</th>' +
      '<th scope="col">Avg days late</th>' +
      '<th scope="col">Max days late</th>' +
      '<th scope="col">Avg lead time (d)</th>' +
      '<th scope="col">P90 lead time (d)</th>' +
      '<th scope="col">Last receipt</th>' +
      '</tr></thead>' +
      '<tbody>' + rows.join('') + '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  // The supplier cell: the name first, with the vendor code beneath
  // it in the secondary ink token; without a name the vendor code
  // alone; without either the supplier code, as today.
  function supplierIdentityHtml(supplier) {
    var name = nonEmptyString(supplier.supplier_name);
    var vendorCode = nonEmptyString(supplier.vendor_code);
    if (name) {
      var html = '<span class="sp-supplier-name">' + esc(name) + '</span>';
      if (vendorCode) {
        html += '<span class="sp-supplier-code">' + esc(vendorCode) + '</span>';
      }
      return html;
    }
    if (vendorCode) {
      return esc(vendorCode);
    }
    return esc(supplier.supplier_code);
  }

  function supplierHtml(supplier) {
    var rate = rateFigure(supplier);
    return (
      '<tr class="sp-row">' +
      '<td class="sp-cell-supplier">' + supplierIdentityHtml(supplier) + '</td>' +
      '<td class="sp-cell-figure">' + esc(wholeNumber(supplier.receipts)) + '</td>' +
      '<td class="sp-cell-rate">' +
        '<span class="sp-rate' + (rate.risk ? ' sp-rate--risk' : '') + '">' +
        esc((rate.risk ? '\u25bc ' : '') + rate.text) +
        '</span>' +
      '</td>' +
      '<td class="sp-cell-figure">' + esc(wholeNumber(supplier.late)) + '</td>' +
      '<td class="sp-cell-figure">' + esc(oneDecimal(supplier.avg_days_late)) + '</td>' +
      '<td class="sp-cell-figure">' + esc(wholeNumber(supplier.max_days_late)) + '</td>' +
      '<td class="sp-cell-figure">' + esc(oneDecimal(supplier.avg_lead_time_days)) + '</td>' +
      '<td class="sp-cell-figure">' + esc(wholeNumber(supplier.p90_lead_time_days)) + '</td>' +
      '<td class="sp-cell-date">' +
        esc(nonEmptyString(supplier.last_received_on) || EM_DASH) +
      '</td>' +
      '</tr>'
    );
  }

  // The Supplier terms section, below the performance table. It shows
  // only while the current performance answer carries at least one
  // row with a non-empty supplier_code; with no such rows it is not
  // shown. Nothing is fetched until "Show terms" is clicked with a
  // supplier chosen, so the section's select and button render
  // without any terms request.
  function termsSectionHtml() {
    var options = termsOptions();
    if (options.length === 0) {
      return '';
    }
    var optionHtml = ['<option value="">Choose a supplier</option>'];
    for (var i = 0; i < options.length; i++) {
      var option = options[i];
      optionHtml.push(
        '<option value="' + esc(option.code) + '"' +
        (option.code === termsState.supplierCode ? ' selected' : '') + '>' +
        esc(option.text) +
        '</option>'
      );
    }
    var inner =
      '<div class="sp-terms-controls">' +
      '<select class="sp-terms-supplier" data-testid="suppliers-terms-supplier">' +
        optionHtml.join('') +
      '</select>' +
      '<button type="button" class="btn btn-primary" data-testid="suppliers-terms-show">Show terms</button>' +
      '</div>';
    if (termsState.loading) {
      inner +=
        '<p class="sp-terms-note" data-testid="suppliers-terms-pending">Loading terms\u2026</p>';
    } else if (termsState.error) {
      inner +=
        '<p class="sp-terms-error" role="alert" data-testid="suppliers-terms-error">' +
        esc(termsState.error.message) +
        '</p>';
    } else if (termsState.answer !== null) {
      inner += termsAnswerHtml();
    }
    return (
      '<section class="sp-terms" data-testid="suppliers-terms" aria-label="Supplier terms">' +
      '<h2 class="sp-terms-heading">Supplier terms</h2>' +
      inner +
      '</section>'
    );
  }

  // One option per eligible row of the current performance answer, in
  // the table's order: the text is the row's supplier name, else its
  // vendor code, else its supplier code; the value is the
  // supplier_code.
  function termsOptions() {
    var options = [];
    if (!state.data) {
      return options;
    }
    var suppliers = suppliersOf(state.data);
    for (var i = 0; i < suppliers.length; i++) {
      var supplier = suppliers[i];
      if (!supplier || typeof supplier !== 'object') {
        continue;
      }
      var code = nonEmptyString(supplier.supplier_code);
      if (!code) {
        continue;
      }
      options.push({
        code: code,
        text:
          nonEmptyString(supplier.supplier_name) ||
          nonEmptyString(supplier.vendor_code) ||
          code,
      });
    }
    return options;
  }

  // The option text for a supplier code: the row's supplier name,
  // else its vendor code, else its code — the code when the current
  // answer no longer lists the row.
  function termsOptionText(supplierCode) {
    var options = termsOptions();
    for (var i = 0; i < options.length; i++) {
      if (options[i].code === supplierCode) {
        return options[i].text;
      }
    }
    return supplierCode || '';
  }

  function termsAnswerHtml() {
    var rows = [];
    for (var i = 0; i < termsState.answer.length; i++) {
      var row = termsState.answer[i];
      if (!row || typeof row !== 'object') {
        continue; // a malformed row never breaks the table
      }
      rows.push(termsRowHtml(row));
    }
    if (rows.length === 0) {
      return (
        '<p class="sp-terms-empty" data-testid="suppliers-terms-empty">' +
        esc(TERMS_EMPTY_NOTE) +
        '</p>'
      );
    }
    return (
      '<div class="sp-terms-region">' +
      '<table class="sp-terms-table" data-testid="suppliers-terms-table">' +
      '<caption class="sp-terms-caption">' +
        esc(rows.length + ' items for ' + (termsState.label || '')) +
      '</caption>' +
      '<thead><tr>' +
      '<th scope="col">Item</th>' +
      '<th scope="col">Minimum</th>' +
      '<th scope="col">Lot size</th>' +
      '<th scope="col">Maximum</th>' +
      '<th scope="col">Lead time (d)</th>' +
      '<th scope="col">Default</th>' +
      '</tr></thead>' +
      '<tbody>' + rows.join('') + '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  // A terms row: the item code as text, each figure without trailing
  // zeros (an em dash when null) and "Yes" where is_default is true,
  // else an empty cell. The rows never carry the performance table's
  // sp-row class.
  function termsRowHtml(row) {
    return (
      '<tr class="sp-terms-row">' +
      '<td class="sp-terms-item">' + esc(nonEmptyString(row.item_code) || EM_DASH) + '</td>' +
      '<td class="sp-terms-figure">' + esc(plainNumber(row.min_order_qty)) + '</td>' +
      '<td class="sp-terms-figure">' + esc(plainNumber(row.lot_size)) + '</td>' +
      '<td class="sp-terms-figure">' + esc(plainNumber(row.max_order_qty)) + '</td>' +
      '<td class="sp-terms-figure">' + esc(plainNumber(row.lead_time_days)) + '</td>' +
      '<td class="sp-terms-default">' + esc(row.is_default === true ? 'Yes' : '') + '</td>' +
      '</tr>'
    );
  }

  // The Supplier risk section, between the performance body and the
  // supplier terms. While the read is out a short skeleton shows
  // (never the word "Loading"); a failed or malformed answer shows
  // its own error and an empty list its own note — a failure here
  // leaves the performance table and the terms exactly as they are.
  function riskSectionHtml() {
    var inner;
    if (riskState.loading) {
      inner =
        '<div class="sp-risk-skel" aria-hidden="true">' +
        '<div class="skel skel-line"></div>' +
        '<div class="skel sp-risk-skel-block"></div>' +
        '</div>';
    } else if (riskState.error) {
      inner =
        '<p class="sp-risk-error" role="alert" data-testid="suppliers-risk-error">' +
        esc(riskState.error.message) +
        '</p>';
    } else {
      var suppliers =
        riskState.answer && Array.isArray(riskState.answer.suppliers)
          ? riskState.answer.suppliers
          : [];
      if (suppliers.length === 0) {
        inner =
          '<p class="sp-risk-empty" data-testid="suppliers-risk-empty">' +
          esc(RISK_EMPTY_NOTE) +
          '</p>';
      } else {
        var rows = [];
        for (var i = 0; i < suppliers.length; i++) {
          var supplier = suppliers[i];
          if (
            !supplier ||
            typeof supplier !== 'object' ||
            riskLevel(supplier.risk) === null
          ) {
            continue; // a malformed entry never breaks the table
          }
          rows.push(riskRowHtml(supplier, i));
          if (isRiskItemsExpanded(i) && soleSourceItemCodes(supplier)) {
            rows.push(riskItemsRowHtml(supplier));
          }
        }
        inner =
          '<div class="sp-risk-region">' +
          '<table class="sp-risk-table" data-testid="suppliers-risk-table">' +
          '<thead><tr>' +
          '<th scope="col">Supplier</th>' +
          '<th scope="col">Risk</th>' +
          '<th scope="col">Why</th>' +
          '<th scope="col">On time</th>' +
          '<th scope="col">Lead-time variation</th>' +
          '</tr></thead>' +
          '<tbody>' +
          rows.join('') +
          '</tbody>' +
          '</table>' +
          '</div>';
      }
    }
    return (
      '<section class="sp-risk" data-testid="suppliers-risk" aria-label="Supplier risk">' +
      '<h2 class="sp-risk-heading">Supplier risk</h2>' +
      inner +
      '</section>'
    );
  }

  // The row's supplier code as display text: a non-empty string or a
  // finite number, else null — a numeric code must reach the cell and
  // the row's data-supplier-code, not be dropped.
  function supplierCodeText(value) {
    if (typeof value === 'string') {
      return value === '' ? null : value;
    }
    if (isNumber(value)) {
      return String(value);
    }
    return null;
  }

  // One risk row: the supplier name, else the code; the level badge
  // with its colour class; the true signs joined by a comma and a
  // space, an em dash with no sign — with the "Show items" toggle
  // after the text when the answer carries a non-empty list of
  // sole-source item codes for the supplier; the rate as a whole
  // percent and the variation to 2 places, em dashes where the
  // answer carries no figure. Every value inserted as text, never
  // as markup.
  function riskRowHtml(supplier, rowIndex) {
    var level = riskLevel(supplier.risk);
    var code = supplierCodeText(supplier.supplier_code) || '';
    var signs = riskSigns(supplier);
    var why = esc(signs.length ? signs.join(', ') : EM_DASH);
    if (soleSourceItemCodes(supplier)) {
      var expanded = isRiskItemsExpanded(rowIndex);
      why +=
        ' <button type="button" class="sp-risk-items-toggle"' +
        ' data-testid="suppliers-risk-items-toggle" data-row-index="' +
        rowIndex +
        '" aria-expanded="' + (expanded ? 'true' : 'false') + '">' +
        esc(expanded ? 'Hide items' : 'Show items') +
        '</button>';
    }
    return (
      '<tr class="sp-risk-row" data-testid="suppliers-risk-row" data-supplier-code="' +
      esc(code) +
      '">' +
      '<td class="sp-risk-supplier">' +
      esc(nonEmptyString(supplier.supplier_name) || code || EM_DASH) +
      '</td>' +
      '<td class="sp-risk-level-cell">' +
      '<span class="sp-risk-badge ' + level.cls + '" data-testid="suppliers-risk-level">' +
      esc(level.text) +
      '</span>' +
      '</td>' +
      '<td class="sp-risk-why">' + why + '</td>' +
      '<td class="sp-risk-figure">' + esc(riskRateFigure(supplier)) + '</td>' +
      '<td class="sp-risk-figure">' + esc(riskCvFigure(supplier)) + '</td>' +
      '</tr>'
    );
  }

  // The open row's detail: one cell across the table listing the
  // supplier's sole-source item codes in the answer's order, joined
  // by a comma and a space.
  function riskItemsRowHtml(supplier) {
    var codes = soleSourceItemCodes(supplier);
    return (
      '<tr class="sp-risk-items-row" data-testid="suppliers-risk-items">' +
      '<td class="sp-risk-items-cell" colspan="5">' +
      esc(codes.join(', ')) +
      '</td>' +
      '</tr>'
    );
  }

  // The Second sources section, directly after the supplier risk and
  // before the supplier terms: it renders whenever the risk section
  // does. While the read is out a short skeleton shows (never the
  // word "Loading"); a failed or malformed answer shows its own
  // error and an empty items array its own note — a failure here
  // leaves the performance table, the risk section and the terms
  // exactly as they are.
  function secondSectionHtml() {
    var inner;
    if (secondState.loading) {
      inner =
        '<div class="sp-second-skel" aria-hidden="true">' +
        '<div class="skel skel-line"></div>' +
        '<div class="skel sp-second-skel-block"></div>' +
        '</div>';
    } else if (secondState.error) {
      inner =
        '<p class="sp-second-error" role="alert" data-testid="suppliers-second-error">' +
        esc(secondState.error.message) +
        '</p>';
    } else {
      var items =
        secondState.answer && Array.isArray(secondState.answer.items)
          ? secondState.answer.items
          : [];
      if (items.length === 0) {
        inner =
          '<p class="sp-second-empty" data-testid="suppliers-second-empty">' +
          esc(SECOND_EMPTY_NOTE) +
          '</p>';
      } else {
        var shown = items;
        if (secondState.shortOnly) {
          // The filter is client-side: the short entries, in the
          // answer's order — no request goes out.
          shown = [];
          for (var k = 0; k < items.length; k++) {
            if (secondIsShort(items[k])) {
              shown.push(items[k]);
            }
          }
        }
        if (secondState.shortOnly && shown.length === 0) {
          // Checked while no entry is short: the table is replaced
          // by the note, the summary line stays.
          inner =
            secondSummaryBarHtml(items) +
            '<p class="sp-second-none-short"' +
            ' data-testid="suppliers-second-none-short">' +
            esc(SECOND_NONE_SHORT_NOTE) +
            '</p>';
        } else {
          var rows = [];
          for (var i = 0; i < shown.length; i++) {
            var entry = shown[i];
            if (!entry || typeof entry !== 'object') {
              continue; // a malformed entry never breaks the table
            }
            rows.push(secondRowHtml(entry));
          }
          inner =
            secondSummaryBarHtml(items) +
            '<div class="sp-second-region">' +
            '<table class="sp-second-table" data-testid="suppliers-second-table">' +
            '<thead><tr>' +
            '<th scope="col">Item</th>' +
            '<th scope="col">Only supplier</th>' +
            '<th scope="col">Candidates</th>' +
            '<th scope="col">Stock to hold</th>' +
            '</tr></thead>' +
            '<tbody>' +
            rows.join('') +
            '</tbody>' +
            '</table>' +
            '</div>';
        }
      }
    }
    return (
      '<section class="sp-second" data-testid="suppliers-second" aria-label="Second sources">' +
      '<h2 class="sp-second-heading">Second sources</h2>' +
      inner +
      '</section>'
    );
  }

  // The item cell: the item code, the description beneath it in the
  // secondary ink token when present; the em dash when the answer
  // carries no code.
  function secondItemHtml(entry) {
    var code = nonEmptyString(entry.item_code) || EM_DASH;
    var html = '<span class="sp-second-item-code">' + esc(code) + '</span>';
    var description = nonEmptyString(entry.description);
    if (description) {
      html +=
        '<span class="sp-second-item-desc">' + esc(description) + '</span>';
    }
    return html;
  }

  // The only-supplier cell: the supplier name, else the code, then
  // the level as High risk, Medium risk or Low risk — Unrated
  // when the level is null.
  function secondSupplierHtml(entry) {
    var who =
      nonEmptyString(entry.supplier_name) ||
      supplierCodeText(entry.supplier_code) ||
      EM_DASH;
    return esc(who) + ', ' + esc(secondRiskText(entry.supplier_risk));
  }

  // The candidates cell: one element per candidate in the answer's
  // order, each reading "<name> (<risk>, <basis>)" — the name
  // the supplier_name else the supplier_code; an entry with an empty
  // candidates list shows the "no other supplier" note instead.
  function secondCandidatesHtml(entry) {
    var candidates = Array.isArray(entry.candidates) ? entry.candidates : [];
    if (candidates.length === 0) {
      return (
        '<span class="sp-second-none" data-testid="suppliers-second-none">' +
        esc(SECOND_NONE_NOTE) +
        '</span>'
      );
    }
    var parts = [];
    for (var i = 0; i < candidates.length; i++) {
      var candidate = candidates[i];
      if (!candidate || typeof candidate !== 'object') {
        continue; // a malformed candidate never breaks the cell
      }
      var name =
        nonEmptyString(candidate.supplier_name) ||
        supplierCodeText(candidate.supplier_code) ||
        EM_DASH;
      var text =
        name +
        ' (' +
        secondRiskText(candidate.risk) +
        ', ' +
        secondBasisText(candidate) +
        ')';
      parts.push(
        '<span class="sp-second-candidate" data-testid="suppliers-second-candidate">' +
          esc(text) +
        '</span>',
      );
    }
    if (parts.length === 0) {
      return esc(EM_DASH);
    }
    return parts.join(', ');
  }

  // The hold cell: "Hold <bridge_units>, short
  // <bridge_short_units>" with a title of the bridge_arithmetic when
  // bridge_units is a JSON number — the screen does not coerce
  // numeric strings — an em dash otherwise.
  function secondHoldHtml(entry) {
    var units = entry.bridge_units;
    if (typeof units !== 'number' || !isFinite(units)) {
      return (
        '<td class="sp-second-hold" data-testid="suppliers-second-hold">' +
        esc(EM_DASH) +
        '</td>'
      );
    }
    var shortUnits = entry.bridge_short_units;
    var shortText =
      typeof shortUnits === 'number' && isFinite(shortUnits)
        ? shortUnits
        : EM_DASH;
    var arithmetic = nonEmptyString(entry.bridge_arithmetic);
    return (
      '<td class="sp-second-hold" data-testid="suppliers-second-hold"' +
        (arithmetic ? ' title="' + esc(arithmetic) + '"' : '') +
        '>' +
        esc('Hold ' + units + ', short ' + shortText) +
      '</td>'
    );
  }

  // One second-sources row: the item, the only supplier, the
  // candidates and the stock to hold. Every value inserted as text,
  // never as markup.
  function secondRowHtml(entry) {
    return (
      '<tr class="sp-second-row" data-testid="suppliers-second-row">' +
        '<td class="sp-second-item">' + secondItemHtml(entry) + '</td>' +
        '<td class="sp-second-supplier">' + secondSupplierHtml(entry) + '</td>' +
        '<td class="sp-second-candidates">' + secondCandidatesHtml(entry) + '</td>' +
        secondHoldHtml(entry) +
      '</tr>'
    );
  }

  // The Order minimums section, the screen's last section, below
  // the supplier terms. While the list read is out a short
  // skeleton shows (never the word "Loading"); a failed or
  // malformed answer shows its own error and renders no rows, an
  // empty list its own note. For an admin the section ends with
  // the save form and each row carries a Remove button; for anyone
  // else neither shows.
  function minimumsSectionHtml() {
    var inner;
    if (minState.loading) {
      inner =
        '<div class="sp-minimums-skel" aria-hidden="true">' +
        '<div class="skel skel-line"></div>' +
        '<div class="skel sp-minimums-skel-block"></div>' +
        '</div>';
    } else if (minState.error) {
      inner =
        '<p class="sp-minimums-error" role="alert" data-testid="suppliers-minimums-error">' +
        esc(MIN_GENERIC_ERROR) +
        '</p>';
    } else if (!minState.answer || minState.answer.length === 0) {
      inner =
        '<p class="sp-minimums-empty" data-testid="suppliers-minimums-empty">' +
        esc(MIN_EMPTY_NOTE) +
        '</p>';
    } else {
      inner = minimumsTableHtml(minState.answer);
    }
    return (
      '<section class="sp-minimums" data-testid="suppliers-minimums" aria-label="Order minimums">' +
      '<h2 class="sp-minimums-heading" data-testid="suppliers-minimums-heading">Order minimums</h2>' +
      inner +
      (minAdmin ? minimumsFormHtml() : '') +
      '</section>'
    );
  }

  // The section's table: one row per stored minimum, in the
  // answer's order — the supplier's label from the suppliers
  // answer (the minimum's code when the read failed or is out),
  // the source, the amount in dollars and cents and the updated
  // line; for an admin the row ends with its Remove button.
  function minimumsTableHtml(rows) {
    var map = supplierMap();
    var body = [];
    for (var i = 0; i < rows.length; i++) {
      body.push(minimumsRowHtml(rows[i], map));
    }
    return (
      '<div class="sp-minimums-region">' +
      '<table class="sp-minimums-table" data-testid="suppliers-minimums-table">' +
      '<thead><tr>' +
      '<th scope="col">Supplier</th>' +
      '<th scope="col">Source</th>' +
      '<th scope="col">Minimum</th>' +
      '<th scope="col">Updated</th>' +
      (minAdmin ? '<th scope="col"></th>' : '') +
      '</tr></thead>' +
      '<tbody>' +
      body.join('') +
      '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  // One minimums row: the supplier's label (the suppliers answer's
  // label for the pair, else the minimum's supplier_code), the
  // source, the dollar figure and the updated line; for an admin
  // the row ends with its Remove button, disabled while its DELETE
  // is out. Every value inserted as text, never as markup.
  function minimumsRowHtml(row, map) {
    var label = map[supplierKey(row)] || nonEmptyString(row.supplier_code) || '';
    var updated = row.updated_at.slice(0, 10) + ' by ' + row.updated_by;
    var removeCell = minAdmin
      ? '<td class="sp-minimums-remove-cell">' +
        '<button type="button" class="sp-minimums-remove"' +
        ' data-testid="suppliers-minimum-remove" data-id="' +
        esc(row.id) +
        '"' +
        (minFormState.removingId === row.id ? ' disabled' : '') +
        '>Remove</button>' +
        '</td>'
      : '';
    return (
      '<tr class="sp-minimums-row" data-testid="suppliers-minimum-row"' +
      ' data-source-system="' + esc(row.source_system) + '"' +
      ' data-supplier-code="' + esc(row.supplier_code) + '">' +
      '<td class="sp-minimums-supplier">' + esc(label) + '</td>' +
      '<td class="sp-minimums-source">' + esc(row.source_system) + '</td>' +
      '<td class="sp-minimums-amount">' + esc(dollarsCents(row.amount)) + '</td>' +
      '<td class="sp-minimums-updated">' + esc(updated) + '</td>' +
      removeCell +
      '</tr>'
    );
  }

  // The admin's save form, below the table: the supplier select
  // (one option per suppliers entry, in the answer's order, reading
  // "<label> (<source_system>)"), the amount input, the save button
  // and the message line. A failed suppliers read shows its own
  // message with no select and no button; a suppliers read still
  // out shows a short skeleton.
  function minimumsFormHtml() {
    if (suppliersState.error) {
      return (
        '<form class="sp-minimums-form" data-testid="suppliers-minimum-form" novalidate>' +
        '<p class="sp-minimums-message is-error" role="alert"' +
        ' data-testid="suppliers-minimum-message">' +
        esc(SUPPLIERS_GENERIC_ERROR) +
        '</p>' +
        '</form>'
      );
    }
    var message = minFormState.message;
    var messageHtml =
      '<p class="sp-minimums-message' +
      (message ? ' is-' + message.kind : '') +
      '"' +
      (message && message.kind === 'error' ? ' role="alert"' : '') +
      ' data-testid="suppliers-minimum-message">' +
      esc(message ? message.text : '') +
      '</p>';
    if (suppliersState.loading && !Array.isArray(suppliersState.answer)) {
      return (
        '<form class="sp-minimums-form" data-testid="suppliers-minimum-form" novalidate>' +
        '<div class="sp-minimums-skel" aria-hidden="true">' +
        '<div class="skel skel-line"></div>' +
        '</div>' +
        messageHtml +
        '</form>'
      );
    }
    var options = ['<option value="">Choose a supplier</option>'];
    var entries = Array.isArray(suppliersState.answer) ? suppliersState.answer : [];
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i];
      if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
        continue; // a malformed entry never breaks the select
      }
      var value = supplierKey(entry);
      var text =
        supplierLabel(entry) +
        ' (' +
        (nonEmptyString(entry.source_system) || '') +
        ')';
      options.push(
        '<option value="' + esc(value) + '"' +
        (value === minFormState.chosen ? ' selected' : '') + '>' +
        esc(text) +
        '</option>'
      );
    }
    return (
      '<form class="sp-minimums-form" data-testid="suppliers-minimum-form" novalidate>' +
      '<div class="sp-minimums-controls">' +
      '<select class="sp-minimums-supplier-select" data-testid="suppliers-minimum-supplier">' +
      options.join('') +
      '</select>' +
      '<input class="sp-minimums-amount-input" data-testid="suppliers-minimum-amount"' +
      ' type="number" step="0.01" min="0" inputmode="decimal"' +
      ' autocomplete="off" spellcheck="false" aria-label="Minimum amount"' +
      (minFormState.saving ? ' disabled' : '') +
      ' value="' + esc(minFormState.amountText) + '">' +
      '<button type="submit" class="btn btn-primary sp-minimums-save"' +
      ' data-testid="suppliers-minimum-save"' +
      (minFormState.saving ? ' disabled' : '') +
      '>Save minimum</button>' +
      '</div>' +
      messageHtml +
      '</form>'
    );
  }

  /* ------------------------------ events ------------------------------- */

  function onRootChange(event) {
    var target = event.target;
    if (!target || !target.getAttribute) {
      return;
    }
    var testId = target.getAttribute('data-testid');
    if (testId === 'suppliers-window') {
      var days = parseInt(target.value, 10);
      if (!isFinite(days) || days <= 0) {
        return;
      }
      state.windowDays = days;
      load();
      return;
    }
    if (testId === 'suppliers-terms-supplier') {
      // The choice lives in module state so a re-render (a window
      // change) keeps it; nothing is fetched until "Show terms".
      termsState.supplierCode = target.value || null;
      return;
    }
    if (testId === 'suppliers-second-short-only') {
      // The filter is client-side only: it re-renders the section's
      // table from the answer in hand — no request goes out.
      secondState.shortOnly = target.checked === true;
      render();
      return;
    }
    if (testId === 'suppliers-minimum-supplier') {
      // The choice lives in module state so a re-render keeps it;
      // choosing a supplier with a stored minimum puts that amount
      // in the amount input, any other choice clears it. Nothing is
      // sent until "Save minimum" is pressed.
      minFormState.chosen = target.value || '';
      var stored = storedMinimumFor(minFormState.chosen);
      minFormState.amountText = stored ? String(stored.amount) : '';
      render();
      return;
    }
  }

  function onRootClick(event) {
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }
    if (target.closest('[data-testid="suppliers-retry"]')) {
      load();
      return;
    }
    if (target.closest('[data-testid="suppliers-risk-items-toggle"]')) {
      var toggle = target.closest('[data-testid="suppliers-risk-items-toggle"]');
      var rowIndex = parseInt(toggle.getAttribute('data-row-index'), 10);
      if (!isFinite(rowIndex) || rowIndex < 0) {
        return; // no usable row position: nothing to toggle
      }
      toggleRiskItems(rowIndex);
      return;
    }
    if (target.closest('[data-testid="suppliers-minimum-remove"]')) {
      removeMinimum(event);
      return;
    }
    if (target.closest('[data-testid="suppliers-terms-show"]')) {
      var section = rootEl.querySelector('[data-testid="suppliers-terms"]');
      var select = section
        ? section.querySelector('[data-testid="suppliers-terms-supplier"]')
        : null;
      var code = select ? select.value : '';
      if (code === '') {
        return; // nothing chosen: no request goes out
      }
      termsState.supplierCode = code;
      loadTerms(code, termsOptionText(code));
    }
  }

  // The Why cell's "Show items" / "Hide items" toggle: it only
  // re-renders — no request goes out. The open rows live in
  // riskState, keyed by the row's position in the risk answer (two
  // rows can share a supplier code and still toggle on their own),
  // so a performance re-render keeps them; a new risk answer, a view
  // re-activation or sign-out closes them all.
  function toggleRiskItems(rowIndex) {
    var index = riskState.expanded.indexOf(rowIndex);
    if (index === -1) {
      riskState.expanded.push(rowIndex);
    } else {
      riskState.expanded.splice(index, 1);
    }
    render();
  }

  // The amount input's text, tracked on every keystroke so a
  // re-render (a window change) restores it; it sends nothing.
  function onRootInput(event) {
    var target = event.target;
    if (!target || !target.getAttribute) {
      return;
    }
    if (target.getAttribute('data-testid') === 'suppliers-minimum-amount') {
      minFormState.amountText = target.value;
    }
  }

  // The form's submit: the browser's own validation is suppressed
  // (novalidate), the screen's own rules decide in saveMinimum.
  function onRootSubmit(event) {
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }
    if (target.closest('[data-testid="suppliers-minimum-form"]')) {
      event.preventDefault();
      saveMinimum();
    }
  }

  // The form's save: with no supplier chosen, or an amount that is
  // not a number above 0 and at most MIN_AMOUNT_MAX, the message
  // says so and nothing is sent. Otherwise one PUT with the chosen
  // pair and the number, the button disabled while it is out: a 200
  // names the supplier and the amount in the message and re-reads
  // the list, whose answer replaces the table; any other answer
  // fails in the message and sends nothing more. An answer that
  // lands after sign-out is dropped.
  function saveMinimum() {
    if (!minAdmin || minFormState.saving) {
      return;
    }
    var entry = chosenSupplierEntry();
    var amount = entry ? parseAmount(minFormState.amountText) : null;
    if (!entry || amount === null) {
      minFormState.message = { text: MIN_INVALID_MESSAGE, kind: 'error' };
      render();
      return;
    }
    var sourceSystem = String(entry.source_system);
    var supplierCode = String(entry.supplier_code);
    var label = supplierLabel(entry) || supplierCode;
    minFormState.saving = true;
    minFormState.message = null;
    render();
    var myGen = writeGen;
    global.Api.request(MIN_PATH, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source_system: sourceSystem,
        supplier_code: supplierCode,
        amount: amount,
      }),
    }).then(
      function () {
        if (myGen !== writeGen) {
          return; // signed out: this answer belongs to a past session
        }
        minFormState.saving = false;
        minFormState.message = {
          text:
            'Saved the minimum for ' +
            label +
            ' (' +
            sourceSystem +
            '): ' +
            dollarsCents(amount) +
            '.',
          kind: 'ok',
        };
        loadMinimums(); // the answer replaces the table
        render();
      },
      function () {
        if (myGen !== writeGen) {
          return;
        }
        minFormState.saving = false;
        minFormState.message = { text: MIN_SAVE_FAILED, kind: 'error' };
        render();
      }
    );
  }

  // A row's Remove button: one DELETE of the row's id, the button
  // disabled while it is out. A 204 names the row's supplier text
  // and source in the message and re-reads the list; any other
  // answer fails in the message and sends nothing more. An answer
  // that lands after sign-out is dropped.
  function removeMinimum(event) {
    if (!minAdmin || minFormState.removingId !== null) {
      return;
    }
    var button = event.target.closest('[data-testid="suppliers-minimum-remove"]');
    var row = button ? button.closest('[data-testid="suppliers-minimum-row"]') : null;
    if (!row) {
      return;
    }
    var rowId = parseInt(button.getAttribute('data-id'), 10);
    if (!isFinite(rowId) || rowId <= 0) {
      return; // no usable row id: nothing to remove
    }
    var supplierCell = row.querySelector('.sp-minimums-supplier');
    var supplierText = supplierCell ? supplierCell.textContent : '';
    var sourceSystem = row.getAttribute('data-source-system') || '';
    minFormState.removingId = rowId;
    minFormState.message = null;
    render();
    var myGen = writeGen;
    global.Api.request(MIN_PATH + '/' + encodeURIComponent(rowId), {
      method: 'DELETE',
    }).then(
      function () {
        if (myGen !== writeGen) {
          return; // signed out: this answer belongs to a past session
        }
        minFormState.removingId = null;
        minFormState.message = {
          text:
            'Removed the minimum for ' +
            supplierText +
            ' (' +
            sourceSystem +
            ').',
          kind: 'ok',
        };
        loadMinimums(); // the removed row leaves the table
        render();
      },
      function () {
        if (myGen !== writeGen) {
          return;
        }
        minFormState.removingId = null;
        minFormState.message = { text: MIN_REMOVE_FAILED, kind: 'error' };
        render();
      }
    );
  }

  /* ------------------------------ lifecycle ---------------------------- */

  function viewActive() {
    var section = document.getElementById(VIEW_ID);
    return section ? section.classList.contains('is-active') : false;
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
    riskState.expanded = []; // a view re-activation closes every open list
    secondState.shortOnly = false; // and unchecks the second-sources filter
    load();
    loadRisk(); // the risk read rides every load of the screen
    loadSecondSources(); // so does the second-sources read
    loadMinimums(); // so does the order-minimums read
    loadSuppliers(); // the section's labels and options
    loadMe(); // the admin flag, the form's gate
  }

  function reset() {
    seq += 1; // any answer still out the door belongs to a past session
    termsSeq += 1; // and so does any terms answer still out the door
    riskSeq += 1; // and so does any risk answer still out the door
    secondSeq += 1; // and so does any second-sources answer still out
    minSeq += 1; // and so does any order-minimums answer still out
    suppliersSeq += 1; // and so does any suppliers answer still out
    meSeq += 1; // and so does any admin answer still out
    writeGen += 1; // and so are any save or remove answers still out

    state.data = null;
    state.loading = false;
    state.error = null;
    state.windowDays = DEFAULT_WINDOW_DAYS;
    termsState.supplierCode = null;
    termsState.label = null;
    termsState.answer = null;
    termsState.error = null;
    termsState.loading = false;
    riskState.answer = null;
    riskState.error = null;
    riskState.loading = false;
    riskState.expanded = []; // and so do the open item rows
    secondState.answer = null;
    secondState.error = null;
    secondState.loading = false;
    secondState.shortOnly = false; // and so is the short-only filter
    minState.answer = null;
    minState.error = null;
    minState.loading = false;
    suppliersState.answer = null;
    suppliersState.error = null;
    suppliersState.loading = false;
    minAdmin = false; // and the admin flag fails closed
    minFormState.chosen = '';
    minFormState.amountText = '';
    minFormState.saving = false;
    minFormState.removingId = null;
    minFormState.message = null;

    render();
  }

  (function boot() {
    rootEl = document.getElementById(ROOT_ID);
    if (!rootEl) {
      return;
    }
    rootEl.addEventListener('change', onRootChange);
    rootEl.addEventListener('click', onRootClick);
    rootEl.addEventListener('input', onRootInput);
    rootEl.addEventListener('submit', onRootSubmit);
    render();

    var appEl = document.getElementById(APP_ID);
    if (!appEl) {
      return;
    }
    // Sign-in shows #app: when it does and the suppliers view is the
    // active one, the list loads then — the pre-sign-in activation
    // (the shell shows the hash's view before Auth.bootstrap has
    // completed) could not fetch.
    var observer = new MutationObserver(function () {
      if (!appEl.hidden && viewActive()) {
        activate();
      }
    });
    observer.observe(appEl, { attributes: true, attributeFilter: ['hidden'] });
    if (!appEl.hidden && viewActive()) {
      activate();
    }
  })();

  global.Suppliers = {
    activate: activate,
    reset: reset,
  };
})(window);
