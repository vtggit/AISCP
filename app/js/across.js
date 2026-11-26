// AIERP planner screen — the Across ERPs view (#/across).
//
// #/across puts the same item in each ERP side by side, and shows where
// one is short while the other holds too much: GET /api/cross-erp/items
// through app/js/api.js, fetched only while the view is active — on
// view activation and on the Refresh button
// ([data-testid="across-refresh"]). A request-sequence guard renders
// only the newest answer, and a skeleton shows while a fetch is out.
//
// The count above the list
// ([data-testid="across-count"]) reads "1 item short in one ERP with
// excess in the other" when exactly one row is an opportunity,
// otherwise "N items short in one ERP with excess in the other" with
// N thousands-separated, or "No item is short in one ERP with excess
// in the other." when no row is an opportunity. Below it, the two
// limits sentences always read: "AIERP does not convert currencies.
// Values assume both ERPs report in the same currency." and "AIERP
// shows the opportunity. It does not create a move between ERPs."
//
// One card per row ([data-testid="across-item"]), the rows with
// opportunity true first and the others after, keeping the answer's
// order within each group. A card is headed by the row's description,
// or by the codes when there is none, and an opportunity row carries
// [data-testid="across-opportunity"] reading "<short ERP> short,
// <excess ERP> has excess" — the ERP names read from the sides at
// short_side and excess_side. The word carries the meaning; the colour
// is only a second signal.
// With the row's suggested_move_units a number above 0, the head
// gains one element after the tag ([data-testid="across-move"])
// reading "Up to <N> units could move from <excess ERP> to <short
// ERP>" — N a whole number with thousands separators, each ERP named
// the way the tag names it. With the field missing, null, 0 or not a
// number, or the row not an opportunity, no element shows.
//
// Each card shows the two sides of the row's sides side by side, each
// headed by the ERP's name ("Acumatica", "Odoo", or otherwise the code
// as text) and its item code, with the side's total on hand and one
// row per site: site, on hand, daily demand, days of cover, reorder
// point, suggested order and the state — "Stockout likely",
// "Stockout at risk" or "Excess <n> units" when those apply, else an
// em dash. A null figure reads an em dash. A side with no sites reads
// "No stock record in this ERP." The site table scrolls inside its own
// region, and at 390x844 the two sides stack.
//
// Each load also sends GET /api/erp-currencies beside the items,
// under the same request-sequence guard, and sign-out clears the
// answer. Once the currency answer has arrived, each side's head
// gains one line after the side's title
// ([data-testid="across-side-currency"]): "Reports in <codes>", the
// side's stored codes comma-and-space joined, "Currency not reported"
// for a side with no stored codes or a null source_system, and — with
// the class is-mismatch — "Reports in <codes>, not
// <reporting_currency>" when any of the codes differs from the
// reporting currency, ignoring case. A failed or malformed currency
// read shows no line, and the rest of the screen is exactly as before.
//
// With no rows the screen reads "No items are matched across ERPs
// yet." with a link to #/settings. A failed fetch shows "The
// comparison could not be loaded." with a "Try again" button that
// sends the request again, and the rest of the app works as before.
//
// The screen is read-only: it has no button that writes, and it sends
// no POST, PUT or DELETE. Every API value is escaped or set as text,
// the colours come only from tokens.css, the answer lives in module
// state, sign-out clears it, and an answer arriving after sign-out is
// discarded.
(function (global) {
  'use strict';

  var VIEW_ID = 'view-across';
  var ROOT_ID = 'across-root';
  var APP_ID = 'app';
  var LIST_PATH = '/api/cross-erp/items';
  var CURRENCIES_PATH = '/api/erp-currencies';

  var EM_DASH = '\u2014';
  var GENERIC_ERROR = 'The comparison could not be loaded.';
  var COUNT_ONE = ' item short in one ERP with excess in the other';
  var COUNT_MANY = ' items short in one ERP with excess in the other';
  var COUNT_ZERO = 'No item is short in one ERP with excess in the other.';
  var LIMIT_CURRENCY =
    'AISCP does not convert currencies. Values assume both ERPs report in the same currency.';
  var LIMIT_MOVE =
    'AISCP shows the opportunity. It does not create a move between ERPs.';
  var EMPTY_NOTE = 'No items are matched across ERPs yet.';
  var NO_STOCK = 'No stock record in this ERP.';

  // The ERP names the side heads read; anything else keeps its code as
  // text.
  var ERP_NAMES = {
    acumatica: 'Acumatica',
    odoo: 'Odoo',
  };

  var rootEl = null;

  var state = {
    data: null, // the newest successful answer
    loading: false, // a fetch is out
    error: null, // the newest failed load: { status, message }
    currencies: null, // the newest valid currency answer, null: none
  };

  // The request-sequence guard: only the newest fetch's answer renders.
  var seq = 0;

  /* ------------------------------ helpers ------------------------------ */

  var esc = window.Shared.esc;

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

  // A value the screen can show as its own text: a non-empty string,
  // or a finite number through String — a numeric site code is data the
  // server sent, not a missing value.
  function displayValue(value) {
    if (typeof value === 'string') {
      return value !== '' ? value : null;
    }
    if (typeof value === 'number' && isFinite(value)) {
      return String(value);
    }
    return null;
  }

  // Whole units with separators (221273 -> "221,273"); an em dash
  // without a usable figure.
  function wholeNumber(value) {
    var number = asNumber(value);
    if (!isFinite(number)) {
      return EM_DASH;
    }
    return Math.round(number).toLocaleString('en-US');
  }

  // A unit figure that may carry decimals (daily demand, days of
  // cover): whole figures with separators, fractional ones as their
  // own text; an em dash without a usable figure.
  function figureText(value) {
    var number = asNumber(value);
    if (!isFinite(number)) {
      return EM_DASH;
    }
    if (Math.round(number) === number) {
      return Math.round(number).toLocaleString('en-US');
    }
    return String(number);
  }

  // The ERP name a side head reads: "Acumatica", "Odoo", or otherwise
  // the code as text; a side that carries no code (no sites) falls
  // back to its item code, and an em dash when it carries nothing.
  function sideName(side) {
    if (!side || typeof side !== 'object') {
      return EM_DASH;
    }
    var code = displayValue(side.source_system);
    if (code) {
      var lower = code.toLowerCase();
      if (ERP_NAMES[lower]) {
        return ERP_NAMES[lower];
      }
      return code;
    }
    return displayValue(side.item_code) || EM_DASH;
  }

  // A well-formed answer: an object with a rows array (never an array
  // or a bare value). Anything else is a failed load.
  function answerValid(payload) {
    return (
      payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      Array.isArray(payload.rows)
    );
  }

  // A well-formed currency answer: an object with a non-empty string
  // reporting_currency and a sources array. Anything else is a failed
  // read: no line shows, and the rest of the screen is untouched.
  function currenciesAnswerValid(payload) {
    return (
      payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      typeof payload.reporting_currency === 'string' &&
      payload.reporting_currency !== '' &&
      Array.isArray(payload.sources)
    );
  }

  /* ------------------------------ loading ------------------------------ */

  function load() {
    seq += 1;
    var mySeq = seq;
    state.loading = true;
    state.error = null;
    state.currencies = null; // a new read is out: the old line is gone
    render();
    global.Api.get(LIST_PATH).then(
      function (payload) {
        if (mySeq !== seq) {
          return; // a newer fetch is out, or signed out: this answer is stale
        }
        state.loading = false;
        if (!answerValid(payload)) {
          state.error = { status: 0, message: GENERIC_ERROR };
        } else {
          state.data = payload;
        }
        render();
      },
      function () {
        if (mySeq !== seq) {
          return;
        }
        state.loading = false;
        state.error = { status: 0, message: GENERIC_ERROR };
        render();
      }
    );
    // Beside the items, the currencies the sources report in: the same
    // read on every load, under the same sequence guard, so a late
    // answer from an older load or from before sign-out is dropped. A
    // failed or malformed answer shows no line, and the rest of the
    // screen is exactly as before.
    global.Api.get(CURRENCIES_PATH).then(
      function (payload) {
        if (mySeq !== seq) {
          return; // a newer fetch is out, or signed out: this answer is stale
        }
        var answer = currenciesAnswerValid(payload) ? payload : null;
        if (answer === state.currencies) {
          return; // the line already shows (or does not): nothing to change
        }
        state.currencies = answer;
        render();
      },
      function () {
        if (mySeq !== seq) {
          return;
        }
        // The read failed: the line does not show, and the screen is
        // exactly as before — nothing to change.
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
    if (!state.data && !state.error) {
      return (
        '<div class="across">' +
        barHtml() +
        '<div class="across-body">' +
        skeletonHtml() +
        '</div></div>'
      );
    }
    var body;
    if (state.loading) {
      body = skeletonHtml();
    } else if (state.error) {
      body = errorHtml();
    } else {
      body = contentHtml();
    }
    return (
      '<div class="across">' +
      barHtml() +
      '<div class="across-body">' +
      body +
      '</div></div>'
    );
  }

  function barHtml() {
    return (
      '<div class="across-bar">' +
      '<button type="button" class="btn btn-secondary across-refresh" data-testid="across-refresh">Refresh</button>' +
      '</div>'
    );
  }

  function skeletonHtml() {
    return (
      '<div class="across-skel" aria-hidden="true">' +
      '<div class="skel skel-line"></div>' +
      '<div class="skel skel-line skel-wide"></div>' +
      '<div class="skel across-skel-block"></div>' +
      '</div>'
    );
  }

  function errorHtml() {
    return (
      '<div class="across-error" role="alert">' +
      '<p class="across-error-msg" data-testid="across-error">' +
      esc(GENERIC_ERROR) +
      '</p>' +
      '<button type="button" class="btn btn-primary across-retry" data-testid="across-retry">Try again</button>' +
      '</div>'
    );
  }

  // The rows the list renders: the opportunities first, then the
  // others, keeping the answer's order within each group.
  function orderedRows(payload) {
    var rows = payload.rows;
    var opportunities = [];
    var others = [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (row && typeof row === 'object' && row.opportunity) {
        opportunities.push(row);
      } else {
        others.push(row);
      }
    }
    return opportunities.concat(others);
  }

  function contentHtml() {
    var rows = orderedRows(state.data);
    return (
      countHtml(rows) +
      limitsHtml() +
      (rows.length > 0
        ? '<section class="across-list" aria-label="Items across ERPs">' +
          rows.map(itemHtml).join('') +
          '</section>'
        : emptyHtml())
    );
  }

  function countHtml(rows) {
    var opportunities = 0;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i] && rows[i].opportunity) {
        opportunities += 1;
      }
    }
    var text;
    if (opportunities === 1) {
      text = '1' + COUNT_ONE;
    } else if (opportunities > 1) {
      text = opportunities.toLocaleString('en-US') + COUNT_MANY;
    } else {
      text = COUNT_ZERO;
    }
    return '<p class="across-count" data-testid="across-count">' + esc(text) + '</p>';
  }

  function limitsHtml() {
    return (
      '<div class="across-limits" data-testid="across-limits">' +
      '<p>' + esc(LIMIT_CURRENCY) + '</p>' +
      '<p>' + esc(LIMIT_MOVE) + '</p>' +
      '</div>'
    );
  }

  function emptyHtml() {
    return (
      '<div class="across-empty" data-testid="across-empty">' +
      '<p class="across-empty-text">' + esc(EMPTY_NOTE) + '</p>' +
      '<a class="across-empty-link" href="#/settings">Set up the ERP code mappings</a>' +
      '</div>'
    );
  }

  // The card head's title: the description, or the codes when there is
  // none.
  function itemTitle(row) {
    var description = displayValue(row.description);
    if (description) {
      return description;
    }
    var sides = Array.isArray(row.sides) ? row.sides : [];
    var codes = [];
    for (var i = 0; i < sides.length; i++) {
      var code = displayValue(sides[i] && sides[i].item_code);
      if (code) {
        codes.push(code);
      }
    }
    return codes.length > 0 ? codes.join(' / ') : EM_DASH;
  }

  // The opportunity tag's text: "<short ERP> short, <excess ERP> has
  // excess", the ERP names read from the sides the row names.
  function opportunityTagText(row) {
    var sides = Array.isArray(row.sides) ? row.sides : [];
    var shortSide =
      typeof row.short_side === 'number' ? sides[row.short_side] : undefined;
    var excessSide =
      typeof row.excess_side === 'number' ? sides[row.excess_side] : undefined;
    if (!shortSide || !excessSide) {
      return null;
    }
    return sideName(shortSide) + ' short, ' + sideName(excessSide) + ' has excess';
  }

  // The move line's text, for the head of an opportunity row: "Up to
  // <N> units could move from <excess ERP> to <short ERP>" — N the
  // row's suggested_move_units as a whole number with thousands
  // separators, each ERP named the way the tag names it. null when
  // the row is not an opportunity, the field is missing, null, 0 or
  // not a number, or the sides the row names do not resolve.
  function moveText(row) {
    if (!row || typeof row !== 'object' || !row.opportunity) {
      return null;
    }
    var units = asNumber(row.suggested_move_units);
    if (!isFinite(units) || units <= 0) {
      return null;
    }
    var sides = Array.isArray(row.sides) ? row.sides : [];
    var shortSide =
      typeof row.short_side === 'number' ? sides[row.short_side] : undefined;
    var excessSide =
      typeof row.excess_side === 'number' ? sides[row.excess_side] : undefined;
    if (!shortSide || !excessSide) {
      return null;
    }
    return (
      'Up to ' +
      wholeNumber(units) +
      ' units could move from ' +
      sideName(excessSide) +
      ' to ' +
      sideName(shortSide)
    );
  }

  // "" while the move line does not show, else the head's element
  // after the opportunity tag.
  function moveHtml(row) {
    var text = moveText(row);
    if (text === null) {
      return '';
    }
    return (
      '<p class="across-move" data-testid="across-move">' +
      esc(text) +
      '</p>'
    );
  }

  function itemHtml(row) {
    if (!row || typeof row !== 'object') {
      return '';
    }
    var sides = Array.isArray(row.sides) ? row.sides : [];
    var tagText = row.opportunity ? opportunityTagText(row) : null;
    return (
      '<article class="across-item" data-testid="across-item">' +
      '<header class="across-item-head">' +
      '<h3 class="across-item-title">' + esc(itemTitle(row)) + '</h3>' +
      (tagText
        ? '<span class="across-opportunity" data-testid="across-opportunity">' +
          esc(tagText) +
          '</span>'
        : '') +
      moveHtml(row) +
      '</header>' +
      '<div class="across-sides">' +
      sides.map(sideHtml).join('') +
      '</div>' +
      '</article>'
    );
  }

  // The stored codes of a side's source system, in the currency
  // answer's order (the API orders its sources by source system then
  // code); [] when the side carries no source system or the system
  // stores no codes. null while the answer has not arrived (or the
  // read failed): the line does not show then.
  function sideCurrencyCodes(side) {
    if (!state.currencies) {
      return null;
    }
    var system = displayValue(side.source_system);
    if (!system) {
      return [];
    }
    var key = system.toLowerCase();
    var codes = [];
    var sources = state.currencies.sources;
    for (var i = 0; i < sources.length; i++) {
      var entry = sources[i];
      if (!entry || typeof entry !== 'object') {
        continue;
      }
      var entrySystem = displayValue(entry.source_system);
      if (!entrySystem || entrySystem.toLowerCase() !== key) {
        continue;
      }
      var code = displayValue(entry.currency_code);
      if (code) {
        codes.push(code);
      }
    }
    return codes;
  }

  // The side's currency line, after the side's title: "Reports in
  // <codes>" the stored codes comma-and-space joined, the same with
  // ", not <reporting_currency>" and the class is-mismatch when any of
  // the codes differs from the reporting currency ignoring case, and
  // "Currency not reported" for a side with no stored codes or a null
  // source_system. "" while the answer has not arrived or failed.
  function sideCurrencyHtml(side) {
    var codes = sideCurrencyCodes(side);
    if (codes === null) {
      return '';
    }
    if (codes.length === 0) {
      return currencyLineHtml('Currency not reported', false);
    }
    var reporting = state.currencies.reporting_currency;
    var text = 'Reports in ' + codes.join(', ');
    var mismatch = false;
    var reportingLower = reporting.toLowerCase();
    for (var i = 0; i < codes.length; i++) {
      if (codes[i].toLowerCase() !== reportingLower) {
        mismatch = true;
        break;
      }
    }
    if (mismatch) {
      text = text + ', not ' + reporting;
    }
    return currencyLineHtml(text, mismatch);
  }

  function currencyLineHtml(text, mismatch) {
    return (
      '<p class="across-side-currency' +
      (mismatch ? ' is-mismatch' : '') +
      '" data-testid="across-side-currency">' +
      esc(text) +
      '</p>'
    );
  }

  function sideHtml(side) {
    if (!side || typeof side !== 'object') {
      return '';
    }
    var sites = Array.isArray(side.sites) ? side.sites : [];
    return (
      '<section class="across-side">' +
      '<header class="across-side-head">' +
      '<h4 class="across-side-title">' +
      esc(sideName(side)) +
      ' <span class="across-side-code">' + esc(displayValue(side.item_code) || EM_DASH) +
      '</span></h4>' +
      sideCurrencyHtml(side) +
      '<p class="across-side-total">On hand ' + wholeNumber(side.on_hand_units) + '</p>' +
      '</header>' +
      (sites.length > 0 ? siteTableHtml(sites) : sideEmptyHtml()) +
      '</section>'
    );
  }

  function sideEmptyHtml() {
    return (
      '<p class="across-side-empty" data-testid="across-side-empty">' +
      esc(NO_STOCK) +
      '</p>'
    );
  }

  function siteTableHtml(sites) {
    return (
      '<div class="across-site-region">' +
      '<table class="across-table">' +
      '<thead><tr>' +
      '<th scope="col">Site</th>' +
      '<th scope="col">On hand</th>' +
      '<th scope="col">Daily demand</th>' +
      '<th scope="col">Days of cover</th>' +
      '<th scope="col">Reorder point</th>' +
      '<th scope="col">Suggested order</th>' +
      '<th scope="col">State</th>' +
      '</tr></thead>' +
      '<tbody>' +
      sites.map(siteRowHtml).join('') +
      '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  function siteRowHtml(site) {
    if (!site || typeof site !== 'object') {
      return '';
    }
    var stateText;
    var stateClass = '';
    if (site.stockout_category === 'likely') {
      stateText = 'Stockout likely';
      stateClass = ' is-stockout';
    } else if (site.stockout_category === 'at_risk') {
      stateText = 'Stockout at risk';
      stateClass = ' is-stockout';
    } else {
      var excess = asNumber(site.excess_units);
      if (isFinite(excess) && excess > 0) {
        stateText = 'Excess ' + wholeNumber(excess) + ' units';
        stateClass = ' is-excess';
      } else {
        stateText = EM_DASH;
      }
    }
    return (
      '<tr>' +
      '<td>' + esc(displayValue(site.site_code) || EM_DASH) + '</td>' +
      '<td>' + esc(wholeNumber(site.on_hand_units)) + '</td>' +
      '<td>' + esc(figureText(site.daily_demand)) + '</td>' +
      '<td>' + esc(figureText(site.days_of_cover)) + '</td>' +
      '<td>' + esc(wholeNumber(site.reorder_point_units)) + '</td>' +
      '<td>' + esc(wholeNumber(site.suggested_order_quantity)) + '</td>' +
      '<td class="across-state' + stateClass + '">' + esc(stateText) + '</td>' +
      '</tr>'
    );
  }

  /* ------------------------------ events ------------------------------- */

  function onRootClick(event) {
    var target = event.target;
    if (!target || typeof target.closest !== 'function') {
      return;
    }
    var control = target.closest(
      '[data-testid="across-refresh"], [data-testid="across-retry"]'
    );
    if (!control || !rootEl.contains(control)) {
      return;
    }
    load();
  }

  /* ------------------------------ shell hooks -------------------------- */

  function viewActive() {
    var view = document.getElementById(VIEW_ID);
    return !!view && view.classList.contains('is-active');
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
    state.data = null;
    state.loading = false;
    state.error = null;
    state.currencies = null;
    render();
  }

  function onAppVisibility() {
    var appEl = document.getElementById(APP_ID);
    if (!appEl || appEl.hidden) {
      return;
    }
    if (viewActive()) {
      activate();
    }
  }

  (function boot() {
    rootEl = document.getElementById(ROOT_ID);
    if (!rootEl) {
      return;
    }
    rootEl.addEventListener('click', onRootClick);
    render();

    var appEl = document.getElementById(APP_ID);
    if (!appEl) {
      return;
    }
    // Sign-in shows #app: when it does and Across ERPs is the active
    // view, the comparison loads then — the pre-sign-in activation
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

  global.Across = {
    activate: activate,
    reset: reset,
  };
})(window);
