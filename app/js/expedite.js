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

// The Request column (issue #1307): the table's twelfth column,
// after Why. Each row's cell (.ex-cell-request, never wrapping)
// holds one button ([data-testid="expedite-draft-open"]) reading
// "Draft request", its accessible name "Draft request for
// <order>", <order> the row's Order cell text. Every signed-in
// user gets it; the screen has no admin-only control, and
// drafting changes nothing. The button opens that row's draft in
// a panel ([data-testid="expedite-draft"]), the first child of
// .ex-body, above the table's scrolling region and outside it, so
// it stays in view however far the table scrolls sideways: a
// heading (h2, "Expedite request for <order>"), a note ("As of
// <as_of>. Nothing is sent from here: copy the text into your own
// email or chat.", <as_of> the answer's as_of, an em dash when
// missing), a read-only text box (a readonly textarea, labelled
// "Expedite request text") holding the draft, a Copy button and a
// Close button, and a status line (role="status"), empty when the
// panel opens. The draft is plain text the row's own fields build
// and nothing else — no contact person, no price, no date or
// figure the row does not carry. Its eleven lines, in order: the
// greeting ("Hello <supplier_name>," the name when it is a
// non-empty string, else "Hello,"), an empty line, "Please
// expedite this order line:", the order ("Order: <order_nbr>,
// line <line_nbr>", each part as the Order cell shows it, an em
// dash when missing; an Odoo line — order_type containing a ".",
// the Odoo model name, whose order_nbr and line_nbr are the
// database name and record id — reads "Order: [add the order
// reference]" instead, they mean nothing to a supplier), the item
// ("Item: <item_code> (<item_description>)", the code alone
// without a description), the quantity ("Open quantity:
// <open_units>", en-US separators, up to three decimals, an em
// dash when the answer carries no usable figure; no unit follows,
// the answer carries no unit of measure), the date ("Promised
// for: <promised_on>" when promise_is_default is false, the date
// is the supplier's own promise; otherwise the ERP's date is only
// a default, so "Expected by: <expected_on>", the date days_late
// counts from — either way the line ends with " (<n> days late)"
// when days_late is above 0, " (1 day late)" for 1; the dates as
// the answer gives them, YYYY-MM-DD, an em dash when missing), an
// empty line, the need ("We need it by <projected_stockout_on>."
// when "after_stockout" is in the reasons and the row carries the
// date, else "We need it as soon as possible." — a late row's
// draft never quotes its stockout date, the line is already
// overdue; it states only the date the line is needed by, never
// that the item will run out or how much stock is on hand —
// after one space it goes on: "Please reply with the earliest
// date you can deliver."), an empty line and "Thank you."; joined
// by a newline, no trailing newline, no trailing spaces. Opening
// a draft moves focus to Copy; Close removes the panel and moves
// focus to that row's Draft request button. One draft at a time:
// another row's button replaces it. The open draft is kept in
// state by the row's identity — its index in the answer's lines,
// its data-row-id today; a re-render, such as the supplier-risk
// answer landing, keeps it open with the same text. The index
// holds only within one answer, so the draft closes on every new
// expedite read (Refresh, Retry, the view activated again), on
// choosing a chip and on sign-out. Copy writes the text box's
// value to the clipboard with one navigator.clipboard.writeText
// call; where the clipboard is absent (a page served over plain
// http) or the call rejects, the text box is focused, its whole
// text selected and document.execCommand('copy') called once. The
// status reads "Copied." on success, "Could not copy
// automatically. The text is selected, ready to copy." on
// failure, written into the panel in place without a re-render
// (focus stays) and kept in state, so a later re-render shows it;
// closing the panel or opening another draft empties it. If the
// open draft changes while the clipboard call is still out —
// another row's draft opens, the panel closes, a chip is chosen,
// a new read, sign-out — the call's result is dropped: no status,
// no focus move, no selection, no fallback copy. Opening,
// copying and closing a draft send no request of any kind, open
// no window and navigate nowhere; the expedite read and the
// supplier-risk read are unchanged, and the screen still sends
// nothing but GETs. Every value is escaped.
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

  // The draft's open quantity: en-US separators, up to three
  // decimals (250 -> "250", 1200 -> "1,200", 2.5 -> "2.5"). No
  // currency style: the answer carries no unit of measure.
  var OPEN_UNITS_FORMATTER = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 3,
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
    draft: null, // the open draft's row: the line's index in the answer's lines
    draftStatus: '', // the draft's status line; '' when the panel opens
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
  // The draft sequence: the number that advances every time the
  // open draft changes (a row's draft opens, the panel closes, a
  // chip is chosen, a new read, sign-out) and never on a plain
  // re-render. Copy notes it when pressed, and a clipboard call
  // that settles after it has moved on is dropped.
  var draftSeq = 0;

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

  // The Order cell's text: order_nbr " / " line_nbr, an em dash for
  // a missing part. The Order cell, the draft button's accessible
  // name and the draft's heading all read it the same way.
  function orderCellText(line) {
    return (
      (nonEmptyString(line.order_nbr) || EM_DASH) +
      ' / ' +
      (lineNbrText(line.line_nbr) || EM_DASH)
    );
  }

  // The draft's open quantity: en-US separators, up to three
  // decimals (250 -> "250", 1200 -> "1,200", 2.5 -> "2.5"); an em
  // dash when the answer carries no usable figure.
  function openUnitsText(value) {
    if (typeof value === 'string') {
      value = value === '' ? NaN : Number(value);
    }
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return OPEN_UNITS_FORMATTER.format(value);
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
    // The draft's index holds only within one answer: a new read is
    // a new answer, so the open draft closes.
    setDraft(null);
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
    // A re-render (the supplier-risk answer landing) rebuilds the
    // panel with the same text: focus that was inside it returns to
    // Copy, where it was before the rebuild.
    var active = document.activeElement;
    var focusBackOnCopy = !!(
      active &&
      rootEl.contains(active) &&
      active.closest &&
      active.closest('[data-testid="expedite-draft"]')
    );
    rootEl.innerHTML = shellHtml();
    if (focusBackOnCopy) {
      var copy = rootEl.querySelector('[data-testid="expedite-draft-copy"]');
      if (copy) {
        copy.focus();
      }
    }
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
      draftPanelHtml() +
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

  /* ---------------------------- the draft ---------------------------- */

  // The draft's order line: "Order: <order_nbr>, line <line_nbr>",
  // each part as the Order cell shows it, an em dash when a part is
  // missing. An Odoo line — an order_type containing a ".", the
  // Odoo model name (#815) — carries the database name and record
  // id, which mean nothing to a supplier: a placeholder instead.
  function draftOrderLine(line) {
    var orderType = typeof line.order_type === 'string' ? line.order_type : '';
    if (orderType.indexOf('.') !== -1) {
      return 'Order: [add the order reference]';
    }
    return (
      'Order: ' +
      (nonEmptyString(line.order_nbr) || EM_DASH) +
      ', line ' +
      (lineNbrText(line.line_nbr) || EM_DASH)
    );
  }

  // The draft's item line: the code with the description in
  // parentheses, the code alone without one, an em dash without a
  // usable code.
  function draftItemLine(line) {
    var code = nonEmptyString(line.item_code) || EM_DASH;
    var description = nonEmptyString(line.item_description);
    return description ? 'Item: ' + code + ' (' + description + ')' : 'Item: ' + code;
  }

  // The draft's date line: "Promised for: <promised_on>" when
  // promise_is_default is false — the supplier's own promise;
  // otherwise the ERP's date is only a default (#613), so "Expected
  // by: <expected_on>", the date days_late counts from. Either way
  // the line ends with the lateness: " (<n> days late)" above 0,
  // " (1 day late)" for 1.
  function draftDateLine(line) {
    var label;
    var date;
    if (line.promise_is_default === false) {
      label = 'Promised for: ';
      date = nonEmptyString(line.promised_on) || EM_DASH;
    } else {
      label = 'Expected by: ';
      date = nonEmptyString(line.expected_on) || EM_DASH;
    }
    var text = label + date;
    var daysLate = line.days_late;
    if (isNumber(daysLate) && daysLate > 0) {
      text += ' (' + String(daysLate) + (daysLate === 1 ? ' day late' : ' days late') + ')';
    }
    return text;
  }

  // The draft's need line: the date the line is needed by, the
  // stockout date, when "after_stockout" is in the reasons and the
  // row carries one — a late row's draft never quotes its stockout
  // date, the line is already overdue. It states only the date the
  // line is needed by: never that the item will run out, never how
  // much stock is on hand. After one space, the reply ask.
  function draftNeedLine(line) {
    var base = 'We need it as soon as possible.';
    if (hasReason(line, 'after_stockout')) {
      var date = nonEmptyString(line.projected_stockout_on);
      if (date) {
        base = 'We need it by ' + date + '.';
      }
    }
    return base + ' Please reply with the earliest date you can deliver.';
  }

  // The draft: plain text the row's own fields build and nothing
  // else — no contact person, no price, no date or figure the row
  // does not carry. Eleven lines joined by a newline, no trailing
  // newline, no trailing spaces.
  function draftTextFor(line) {
    var supplier = nonEmptyString(line.supplier_name);
    var lines = [
      supplier ? 'Hello ' + supplier + ',' : 'Hello,',
      '',
      'Please expedite this order line:',
      draftOrderLine(line),
      draftItemLine(line),
      'Open quantity: ' + openUnitsText(line.open_units),
      draftDateLine(line),
      '',
      draftNeedLine(line),
      '',
      'Thank you.',
    ];
    return lines.join('\n');
  }

  // The draft panel: the first child of .ex-body, above the
  // table's scrolling region and outside it, so it stays in view
  // however far the table scrolls sideways. One draft at a time,
  // kept in state by the row's index in the answer's lines.
  function draftPanelHtml() {
    if (state.draft === null || state.loading || state.error || !state.data) {
      return '';
    }
    var lines = linesOf(state.data);
    var index = state.draft;
    if (typeof index !== 'number' || index < 0 || index >= lines.length) {
      return '';
    }
    var line = lines[index];
    if (!line || typeof line !== 'object') {
      return '';
    }
    var asOf = nonEmptyString(state.data.as_of) || EM_DASH;
    var order = orderCellText(line);
    return (
      '<div class="ex-draft" data-testid="expedite-draft">' +
      '<h2 class="ex-draft-title" data-testid="expedite-draft-title">Expedite request for ' +
        esc(order) +
      '</h2>' +
      '<p class="ex-draft-note" data-testid="expedite-draft-note">As of ' + esc(asOf) +
        '. Nothing is sent from here: copy the text into your own email or chat.</p>' +
      '<label class="ex-draft-label" for="expedite-draft-textarea">Expedite request text</label>' +
      '<textarea id="expedite-draft-textarea" class="ex-draft-text" ' +
        'data-testid="expedite-draft-text" rows="11" readonly>' +
        esc(draftTextFor(line)) +
      '</textarea>' +
      '<div class="ex-draft-actions">' +
      '<button type="button" class="btn btn-secondary ex-draft-copy" data-testid="expedite-draft-copy">Copy</button>' +
      '<button type="button" class="btn btn-secondary ex-draft-close" data-testid="expedite-draft-close">Close</button>' +
      '</div>' +
      '<p class="ex-draft-status" role="status" data-testid="expedite-draft-status">' +
        esc(state.draftStatus) +
      '</p>' +
      '</div>'
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
      '<th scope="col">Request</th>' +
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
      '<td class="ex-cell-order">' + esc(orderCellText(line)) + '</td>' +
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
      '<td class="ex-cell-request">' +
        '<button type="button" class="btn btn-secondary ex-draft-open" ' +
        'data-testid="expedite-draft-open" ' +
        'aria-label="Draft request for ' + esc(orderCellText(line)) + '">' +
        'Draft request</button>' +
      '</td>' +
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
      // A chip choice is a new view of the answer's lines: the open
      // draft closes with it.
      setDraft(null);
      render();
      return;
    }
    if (event.target.closest ? event.target.closest('[data-testid="expedite-refresh"]') : null) {
      load();
      return;
    }
    if (event.target.closest ? event.target.closest('[data-testid="expedite-retry"]') : null) {
      load();
      return;
    }
    var open = event.target.closest ? event.target.closest('[data-testid="expedite-draft-open"]') : null;
    if (open) {
      var row = open.closest('tr.ex-row');
      var index = row ? Number(row.getAttribute('data-row-id')) : NaN;
      if (isFinite(index)) {
        openDraft(index);
      }
      return;
    }
    if (event.target.closest ? event.target.closest('[data-testid="expedite-draft-copy"]') : null) {
      copyDraft();
      return;
    }
    if (event.target.closest ? event.target.closest('[data-testid="expedite-draft-close"]') : null) {
      closeDraft();
    }
  }

  // The one helper every place that opens or closes the draft
  // goes through: it sets the open draft (or null), empties the
  // status and advances the draft sequence, so a clipboard call
  // still out the door for the previous draft is dropped when it
  // settles. A plain re-render never comes through here.
  function setDraft(index) {
    draftSeq += 1;
    state.draft = index;
    state.draftStatus = '';
  }

  // The draft buttons: opening a row's draft replaces whatever is
  // open (one draft at a time) and moves focus to Copy; Close
  // removes the panel and moves focus to that row's Draft request
  // button. Neither sends a request.
  function openDraft(index) {
    setDraft(index);
    render();
    var copy = rootEl.querySelector('[data-testid="expedite-draft-copy"]');
    if (copy) {
      copy.focus();
    }
  }

  function closeDraft() {
    var index = state.draft;
    setDraft(null);
    render();
    if (typeof index !== 'number') {
      return;
    }
    var button = rootEl.querySelector(
      'tr[data-row-id="' + index + '"] [data-testid="expedite-draft-open"]'
    );
    if (button) {
      button.focus();
    }
  }

  // Copy: the text box's value to the clipboard with one
  // navigator.clipboard.writeText call, noted against the draft
  // sequence when pressed. Where the clipboard is absent (a page
  // served over plain http) or the call rejects, the fallback
  // focuses the text box on the page at that moment — a re-render
  // may have replaced the one Copy was pressed in — selects its
  // whole text and calls document.execCommand('copy') once; the
  // text stays selected. If the open draft changes before the call
  // settles, the result is dropped — no status, no focus move, no
  // selection, no fallback. Otherwise the status lands in the panel
  // in place — no re-render, so focus stays where it is — and in
  // state, for the next re-render.
  function copyDraft() {
    var box = rootEl.querySelector('[data-testid="expedite-draft-text"]');
    if (!box) {
      return;
    }
    // The draft's identity when Copy was pressed: a call that
    // settles after the counter has moved on is dropped.
    var mySeq = draftSeq;
    var finish = function (ok) {
      if (mySeq !== draftSeq) {
        return; // the draft moved on: drop the result
      }
      setDraftStatus(
        ok
          ? 'Copied.'
          : 'Could not copy automatically. The text is selected, ready to copy.'
      );
    };
    var fallback = function () {
      if (mySeq !== draftSeq) {
        return; // the draft moved on: drop the result
      }
      // The text box on the page at that moment, not the one Copy
      // was pressed in: a re-render may have replaced it.
      var fresh = rootEl.querySelector('[data-testid="expedite-draft-text"]');
      if (!fresh) {
        return;
      }
      fresh.focus();
      fresh.select();
      var ok = false;
      try {
        ok = document.execCommand('copy') === true;
      } catch (err) {
        ok = false; // the text stays selected: the user copies it
      }
      finish(ok);
    };
    var clipboard = global.navigator && global.navigator.clipboard;
    if (clipboard && typeof clipboard.writeText === 'function') {
      clipboard.writeText(box.value).then(
        function () {
          finish(true);
        },
        fallback
      );
    } else {
      fallback();
    }
  }

  // The status line, in place: state for the next re-render, text
  // for now. The panel is not re-rendered, so focus is untouched.
  function setDraftStatus(message) {
    state.draftStatus = message;
    var status = rootEl && rootEl.querySelector('[data-testid="expedite-draft-status"]');
    if (status) {
      status.textContent = message;
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
    setDraft(null);
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
