// AIERP planner screen — the Expedite view (#/expedite).
//
// #/expedite lists the open purchase lines worth chasing: the lines
// GET /api/expedite answers with, in the answer's order — the server
// ranks them by stockout urgency. Above the table a row of reason
// chips ([data-testid="expedite-filter"]) filters the table without a
// new request: "All" (the default, applied only on first load),
// "Late" and "After stockout". Each chip shows its number of lines,
// counted from the answer's reasons. A real Refresh button
// ([data-testid="expedite-refresh"]) re-fetches, and so does
// activating the view; while a fetch is out a skeleton shows, and a
// request-sequence guard renders only the newest answer. When a new
// answer arrives the selected chip filter is preserved and
// re-applied to the new lines.
//
// The table ([data-testid="expedite-table"]) scrolls within its own
// region: Order (order_nbr " / " line_nbr), Item (item_description,
// with the item_code beneath it in the secondary ink token; just the
// code when there is no description), Site, Supplier (the name, with
// the vendor code beneath it in the secondary ink token; the vendor
// code without a name; the supplier code without either — followed by
// "· <n>% on time" when the answer carries a supplier_on_time_rate,
// below 80% in the risk colour tokens),
// Promised, Late ("<n> d" when days_late is set, else
// empty), Open units and Value (whole dollars with separators, an
// em dash when the value is null), Cover ("<n> d", else an em dash),
// Stockout on (the date, else an em dash — when "after_stockout" is
// in the line's reasons it gets a leading ▼ marker and the risk
// colour tokens) and Why (a "Late" and/or an "After stockout" badge
// per reason). Below the table, when the answer's stale_excluded is
// above 0, a note says how many lines promised more than 90 days ago
// are not shown.
//
// The supplier risk badge (issue #1071): every load of the screen
// sends one GET /api/supplier-risk beside the GET /api/expedite
// read, through app/js/api.js, under its own request-sequence
// guard — a late answer from an older load or from before sign-out
// is dropped, and sign-out clears it. The expedite read, its order
// and its count are unchanged. In a row's supplier cell
// (.ex-cell-supplier), after the name, the vendor code and the
// on-time rate, a span ([data-testid="expedite-supplier-risk"],
// class ex-supplier-risk) shows when the risk answer rates that
// row's supplier_code "high" or "medium": it reads "High risk" with
// the class is-high or "Medium risk" with is-medium, and its title
// lists the true signs — Late deliveries, Variable lead times, Only
// supplier of <N> item(s), the word item when N is 1 — joined by a
// comma and a space. A supplier rated "low", one missing from the
// answer, a row without a supplier_code, a failed or malformed risk
// answer, and the time before the answer arrives all show no badge;
// the name, the vendor code and the on-time rate of every row are
// exactly as before. Every value is escaped, and the screen sends
// no new POST, PUT or DELETE.
//
// With no lines an empty note shows ("Nothing to chase today."); a
// 422 shows the server's detail inline with a Retry; any other
// failure shows "The expedite list could not be loaded." with a
// Retry that re-fetches. Every API value is inserted as text, never
// as markup. Sign-out resets the view to its first-load state.
(function (global) {
  'use strict';

  var LIST_PATH = '/api/expedite';
  var RISK_PATH = '/api/supplier-risk';
  var ROOT_ID = 'expedite-root';
  var VIEW_ID = 'view-expedite';
  var APP_ID = 'app';

  var EM_DASH = '\u2014';
  var GENERIC_ERROR = 'The expedite list could not be loaded.';

  // The on-time rate below this gets the risk colour tokens.
  var RISK_RATE = 0.8;

  // minimumFractionDigits must be pinned with maximumFractionDigits:
  // for currency style the minimum defaults to the currency's (2 for
  // USD), and a maximum below the minimum makes the constructor throw
  // a RangeError at script load, aborting the whole script before
  // window.Expedite is published.
  var DOLLAR_FORMATTER = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });

  // The chips, in display order. ``key`` is the chip's identity and
  // the ``reasons`` entry it matches.
  var CHIPS = [
    { key: 'all', label: 'All' },
    { key: 'late', label: 'Late' },
    { key: 'after_stockout', label: 'After stockout' },
  ];

  var REASON_LABEL = {
    late: 'Late',
    after_stockout: 'After stockout',
  };

  var rootEl = null;

  var state = {
    data: null, // the newest successful answer: { as_of, lines, stale_excluded }
    loading: false, // a fetch is out
    error: null, // the newest failed load: { status, message }
    filter: 'all', // the selected chip; "all" is the first-load default
    risk: null, // the newest valid supplier-risk answer
    riskLoading: false, // a risk fetch is out
  };

  // The request-sequence guard: only the newest fetch's answer renders.
  var seq = 0;
  // The supplier-risk sequence: a reset (sign-out) or a newer
  // expedite read invalidates a risk answer still out the door, so
  // it can neither render into the fresh state nor badge a stale
  // row.
  var riskSeq = 0;

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

  // A whole figure with separators (647 -> "647"); an em dash when
  // the server carries no usable figure.
  function wholeNumber(value) {
    if (typeof value === 'string') {
      value = value === '' ? NaN : Number(value);
    }
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return Math.round(value).toLocaleString('en-US');
  }

  // Whole dollars with separators (1941.0 -> "$1,941"); an em dash
  // when the value is null or unusable.
  function wholeDollars(value) {
    if (value === null || value === undefined || value === '') {
      return EM_DASH;
    }
    var n = Number(value);
    if (!isNumber(n)) {
      return EM_DASH;
    }
    return DOLLAR_FORMATTER.format(Math.round(n));
  }

  function nonEmptyString(value) {
    return typeof value === 'string' && value !== '' ? value : null;
  }

  // "<n> d" for a finite days figure, else an em dash. 2.0 -> "2 d",
  // 3.7 -> "3.7 d".
  function daysText(value) {
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return String(value) + ' d';
  }

  function linesOf(data) {
    return data && Array.isArray(data.lines) ? data.lines : [];
  }

  function hasReason(line, reason) {
    return (
      line &&
      Array.isArray(line.reasons) &&
      line.reasons.indexOf(reason) !== -1
    );
  }

  function chipCount(key, data) {
    var lines = linesOf(data);
    if (key === 'all') {
      return lines.length;
    }
    var n = 0;
    for (var i = 0; i < lines.length; i++) {
      if (hasReason(lines[i], key)) {
        n += 1;
      }
    }
    return n;
  }

  function staleCount(data) {
    if (!data || typeof data.stale_excluded !== 'number' || !isFinite(data.stale_excluded)) {
      return 0;
    }
    return Math.max(0, Math.round(data.stale_excluded));
  }

  // The visible { line, id } pairs. ``id`` is the line's index in the
  // answer's full lines array — the row's identity.
  function visibleLines() {
    var lines = linesOf(state.data);
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (state.filter === 'all' || (line && hasReason(line, state.filter))) {
        out.push({ line: line, id: i });
      }
    }
    return out;
  }

  // A well-formed answer: an object (never an array or a bare value)
  // with a lines array. Anything else is a failed load.
  function answerValid(payload) {
    return (
      payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      Array.isArray(payload.lines)
    );
  }

  /* ------------------------------ loading ------------------------------ */

  function load() {
    seq += 1;
    var mySeq = seq;
    state.loading = true;
    state.error = null;
    render();
    global.Api.get(LIST_PATH).then(
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
    loadRisk(); // one risk read beside the expedite read, its own guard
  }

  /* --------------------------- supplier risk --------------------------- */

  // A well-formed risk answer: an object (never an array or a bare
  // value) with a suppliers array. Anything else is a failed read —
  // the rows keep rendering, without badges.
  function riskAnswerValid(payload) {
    return (
      payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      Array.isArray(payload.suppliers)
    );
  }

  // The risk read: one GET /api/supplier-risk per expedite read,
  // under its own request-sequence guard — a late answer from an
  // older read or from before sign-out is dropped. A failed or
  // malformed answer clears the stored answer (no badge), without a
  // page error; the expedite list itself is never touched by it.
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

  // The badge of one row's supplier cell: nothing for the row
  // without a supplier_code, before a valid risk answer has landed,
  // for a supplier missing from the answer or rated "low" — and
  // "High risk" (is-high) or "Medium risk" (is-medium) when the
  // answer rates the row's supplier_code high or medium, its title
  // the true signs joined by a comma and a space.
  function supplierRiskBadgeHtml(line) {
    var code = nonEmptyString(line.supplier_code);
    if (!code || !riskAnswerValid(state.risk)) {
      return '';
    }
    var suppliers = state.risk.suppliers;
    for (var i = 0; i < suppliers.length; i++) {
      var entry = suppliers[i];
      if (!entry || typeof entry !== 'object') {
        continue; // a malformed entry never breaks the rows
      }
      var entryCode = entry.supplier_code;
      if (entryCode === null || entryCode === undefined) {
        continue;
      }
      if (String(entryCode) !== code) {
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
        '<span class="ex-supplier-risk ' + level.cls + '" data-testid="expedite-supplier-risk"' +
        ' title="' + esc(riskSigns(entry).join(', ')) + '">' +
        esc(level.text) +
        '</span>'
      );
    }
    return '';
  }

  /* ------------------------------ rendering ---------------------------- */

  function render() {
    if (!rootEl) {
      return;
    }
    rootEl.innerHTML = shellHtml();
  }

  function shellHtml() {
    // First load (no answer, no failure yet) and every in-flight
    // fetch of it: the skeleton.
    if (!state.data && !state.error) {
      return '<div class="ex">' + skeletonHtml() + '</div>';
    }
    var body;
    if (state.loading) {
      body = skeletonHtml();
    } else if (state.error) {
      body = errorHtml();
    } else {
      var visible = visibleLines();
      if (visible.length === 0) {
        // No lines at all ("All"): the first-load note. Lines exist
        // but the selected chip matches none: the chip's own note,
        // its label lower-cased.
        body =
          state.filter === 'all' || linesOf(state.data).length === 0
            ? emptyHtml()
            : emptyHtml(chipLabel(state.filter));
      } else {
        body = tableHtml(visible);
      }
    }
    return (
      '<div class="ex">' +
      barHtml() +
      '<div class="ex-body">' +
      body +
      (state.error || state.loading ? '' : staleHtml()) +
      '</div>' +
      '</div>'
    );
  }

  function barHtml() {
    var chips = CHIPS.map(function (chip) {
      var on = state.filter === chip.key;
      return (
        '<button type="button" class="ex-chip' + (on ? ' is-on' : '') + '" ' +
        'aria-pressed="' + (on ? 'true' : 'false') + '" data-state="' + esc(chip.key) + '">' +
        esc(chip.label) + ' ' + chipCount(chip.key, state.data) +
        '</button>'
      );
    }).join('');
    return (
      '<div class="ex-bar">' +
      '<div class="ex-chips" role="group" aria-label="Expedite reason filter" data-testid="expedite-filter">' +
        chips +
      '</div>' +
      '<button type="button" class="btn btn-secondary ex-refresh" data-testid="expedite-refresh"' +
        (state.loading ? ' disabled' : '') +
      '>Refresh</button>' +
      '</div>'
    );
  }

  function skeletonHtml() {
    return (
      '<div class="ex-skel-wrap" data-testid="expedite-skeleton" aria-hidden="true">' +
      '<div class="skel skel-line"></div>' +
      '<div class="skel skel-line skel-wide"></div>' +
      '<div class="skel ex-skel-block"></div>' +
      '</div>'
    );
  }

  function errorHtml() {
    return (
      '<div class="ex-error" role="alert" data-testid="expedite-error">' +
      '<p class="ex-error-msg" data-testid="expedite-error-msg">' +
        esc(state.error.message) +
      '</p>' +
      '<button type="button" class="btn btn-secondary" data-testid="expedite-retry">Retry</button>' +
      '</div>'
    );
  }

  // The chip's display label (its empty note lower-cases it).
  function chipLabel(key) {
    for (var i = 0; i < CHIPS.length; i++) {
      if (CHIPS[i].key === key) {
        return CHIPS[i].label;
      }
    }
    return key;
  }

  function emptyHtml(label) {
    var message =
      label && label !== ''
        ? 'No lines are ' + label.toLowerCase() + '.'
        : 'Nothing to chase today.';
    return (
      '<p class="ex-empty" data-testid="expedite-empty">' +
      esc(message) +
      '</p>'
    );
  }

  // The stale note: how many lines promised more than 90 days ago the
  // answer left out.
  function staleHtml() {
    var n = staleCount(state.data);
    if (n <= 0) {
      return '';
    }
    return (
      '<p class="ex-stale" data-testid="expedite-stale">' +
      esc(n) +
      ' line(s) promised more than 90 days ago are not shown.' +
      '</p>'
    );
  }

  function tableHtml(entries) {
    var rows = [];
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i];
      var line = entry && entry.line;
      if (!line || typeof line !== 'object') {
        continue; // a malformed row never breaks the table
      }
      rows.push(lineHtml(line, entry.id));
    }
    return (
      '<div class="ex-table-region">' +
      '<table class="ex-table" data-testid="expedite-table">' +
      '<thead><tr>' +
      '<th scope="col">Order</th>' +
      '<th scope="col">Item</th>' +
      '<th scope="col">Site</th>' +
      '<th scope="col">Supplier</th>' +
      '<th scope="col">Promised</th>' +
      '<th scope="col">Late</th>' +
      '<th scope="col">Open units</th>' +
      '<th scope="col">Value</th>' +
      '<th scope="col">Cover</th>' +
      '<th scope="col">Stockout on</th>' +
      '<th scope="col">Why</th>' +
      '</tr></thead>' +
      '<tbody>' + rows.join('') + '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  // The Item cell: item_description on the primary line, with the
  // item_code beneath it in the secondary ink token; just the code
  // when there is no description; an em dash without either.
  function itemHtml(line) {
    var description = nonEmptyString(line.item_description);
    var code = nonEmptyString(line.item_code);
    if (!description) {
      return esc(code || EM_DASH);
    }
    return (
      '<span class="ex-item-desc">' + esc(description) + '</span>' +
      '<span class="ex-item-code">' + esc(code || EM_DASH) + '</span>'
    );
  }

  function lineHtml(line, id) {
    var reasons = Array.isArray(line.reasons) ? line.reasons : [];
    var badges = [];
    for (var i = 0; i < reasons.length; i++) {
      var reason = reasons[i];
      if (typeof reason === 'string' && REASON_LABEL[reason]) {
        badges.push(
          '<span class="ex-badge ex-badge--' + esc(reason) + '" data-testid="expedite-badge">' +
          esc(REASON_LABEL[reason]) +
          '</span>'
        );
      }
    }
    return (
      '<tr class="ex-row" data-row-id="' + id + '" data-testid="expedite-row">' +
      '<td class="ex-cell-order">' +
        esc((nonEmptyString(line.order_nbr) || EM_DASH) + ' / ' + (lineNbrText(line.line_nbr) || EM_DASH)) +
      '</td>' +
      '<td class="ex-cell-item">' + itemHtml(line) + '</td>' +
      '<td class="ex-cell-site">' + esc(nonEmptyString(line.site_code) || EM_DASH) + '</td>' +
      '<td class="ex-cell-supplier">' + supplierHtml(line) + '</td>' +
      '<td class="ex-cell-promised">' + esc(nonEmptyString(line.promised_on) || EM_DASH) + '</td>' +
      '<td class="ex-cell-late">' + esc(lateText(line.days_late)) + '</td>' +
      '<td class="ex-cell-units">' + esc(wholeNumber(line.open_units)) + '</td>' +
      '<td class="ex-cell-value">' + esc(wholeDollars(line.open_value)) + '</td>' +
      '<td class="ex-cell-cover">' + esc(daysText(line.days_of_cover)) + '</td>' +
      '<td class="ex-cell-stockout">' + stockoutHtml(line) + '</td>' +
      '<td class="ex-cell-why">' + badges.join('') + '</td>' +
      '</tr>'
    );
  }

  // The line number as display text (1 -> "1"), null when the answer
  // carries no usable number.
  function lineNbrText(value) {
    if (value === null || value === undefined) {
      return null;
    }
    var text = String(value);
    return text === '' ? null : text;
  }

  // "<n> d" when days_late is set, else empty.
  function lateText(daysLate) {
    if (isNumber(daysLate) && daysLate > 0) {
      return String(daysLate) + ' d';
    }
    return '';
  }

  // The supplier cell: the name first, with the vendor code beneath
  // it in the secondary ink token; without a name the vendor code,
  // then the supplier code; an em dash without either — followed by
  // the on-time rate when the answer carries one. Below the risk
  // rate the rate part gets the risk colour tokens.
  function supplierHtml(line) {
    var name = nonEmptyString(line.supplier_name);
    var vendorCode = nonEmptyString(line.vendor_code);
    var code = nonEmptyString(line.supplier_code);
    var html;
    if (name) {
      html = '<span class="ex-supplier-name">' + esc(name) + '</span>';
      if (vendorCode) {
        html += '<span class="ex-supplier-code">' + esc(vendorCode) + '</span>';
      }
    } else if (vendorCode) {
      html = esc(vendorCode);
    } else if (code) {
      html = esc(code);
    } else {
      html = esc(EM_DASH);
    }
    var rate = line.supplier_on_time_rate;
    if (isNumber(rate)) {
      var risk = rate < RISK_RATE;
      html +=
        '<span class="ex-supplier-rate' + (risk ? ' ex-supplier-rate--risk' : '') + '">' +
        esc('\u00b7 ' + Math.round(rate * 100) + '% on time') +
        '</span>';
    }
    html += supplierRiskBadgeHtml(line);
    return html;
  }

  // The stockout date: an em dash without a projection; when
  // "after_stockout" is in the reasons the date gets a leading ▼
  // marker and the risk colour tokens.
  function stockoutHtml(line) {
    var date = nonEmptyString(line.projected_stockout_on);
    if (!date) {
      return esc(EM_DASH);
    }
    if (hasReason(line, 'after_stockout')) {
      return (
        '<span class="ex-stockout-date ex-stockout-date--risk">' +
        esc('\u25bc ' + date) +
        '</span>'
      );
    }
    return '<span class="ex-stockout-date">' + esc(date) + '</span>';
  }

  /* ------------------------------ events ------------------------------- */

  function onRootClick(event) {
    var chip = event.target.closest ? event.target.closest('.ex-chip') : null;
    if (chip) {
      state.filter = chip.getAttribute('data-state') || 'all';
      render();
      return;
    }
    if (event.target.closest ? event.target.closest('[data-testid="expedite-refresh"]') : null) {
      load();
      return;
    }
    if (event.target.closest ? event.target.closest('[data-testid="expedite-retry"]') : null) {
      load();
    }
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
    load();
  }

  function reset() {
    seq += 1; // any answer still out the door belongs to a past session
    riskSeq += 1; // and so does any risk answer
    state.data = null;
    state.risk = null;
    state.riskLoading = false;
    state.loading = false;
    state.error = null;
    state.filter = 'all'; // the "All" default is a first-load one
    render();
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
    // Sign-in shows #app: when it does and the expedite view is the
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

  global.Expedite = {
    activate: activate,
    reset: reset,
  };
})(window);
