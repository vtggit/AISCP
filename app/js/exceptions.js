// AIERP planner screen — the Exceptions view (#/exceptions).
//
// #/exceptions is the triage list of what needs attention today,
// across every planning signal: GET /api/exceptions through
// app/js/api.js, fetched on view activation and on the Refresh
// button ([data-testid="exceptions-refresh"]). The "Show snoozed"
// checkbox ([data-testid="exceptions-show-snoozed"]) refetches with
// include_snoozed=true when ticked, and without it again when
// unticked. A request-sequence guard renders only the newest answer,
// and a skeleton shows while a fetch is out. Whether the viewer is an
// admin is read from GET /api/auth/me's roles, exactly as the Push to
// ERP view does: an unknown or failed read fails closed — the Actions
// column and its buttons render only once /api/auth/me positively
// says the user's roles include "admin".
//
// The summary ([data-testid="exceptions-summary"]) reads
// "<high> high · <medium> medium · <acknowledged> acknowledged" from
// the answer's counts. When the answer's sources_failed is not empty,
// a note ([data-testid="exceptions-sources-failed"]) reads "Some
// signals could not be read: " followed by the source names joined
// with ", " — the rows that did load still render.
// When the answer's counts.new is a number above 0, a line
// ([data-testid="exceptions-new-count"]) reads "<n> new today"
// directly after the summary; otherwise there is no such element.
// The "New only" checkbox ([data-testid="exceptions-new-only"]),
// next to "Show snoozed", filters the current answer client-side to
// the rows whose new is true — it makes no request, unticking
// restores the full list, a refetch keeps the tick, and with
// nothing new the table is replaced by "Nothing new today."
// The "Signal" filter ([data-testid="exceptions-kind-filter"], a
// select aria-labelled "Signal", next to "New only") offers
// "All signals" and then one option per distinct kind in the
// current answer, labelled by the kind map, in the order the kinds
// first appear. Choosing a kind shows only that kind's rows,
// client-side — it makes no request, it combines with "New only"
// (a row shows only when it passes both), and a combination that
// leaves no row replaces the table with "No exceptions match these
// filters."; "All signals" chosen, the screen behaves exactly as
// before, "New only" alone keeping its "Nothing new today." A
// refetch keeps the choice while the answer still carries the kind
// and returns to "All signals" otherwise.
//
// The table ([data-testid="exceptions-table"]) has one row per
// exception ([data-testid="exceptions-row"]), in the answer's order:
// Severity ("High" or "Medium", the raw value on the cell's
// data-severity attribute), Signal (the kind's label — any other kind
// shows its raw text — a row whose new is true shows the "New" badge
// ([data-testid="exceptions-new"]) in the cell, after the label),
// Item and Site ("<item> / <site>", an em dash when both are null),
// Detail, Detected (detected_on), Status ("New"
// while unacknowledged; otherwise "Acknowledged by <name>", or
// "Acknowledged" without a name, followed by " · snoozed until
// <date>" when the row is snoozed) and Open — a link
// ([data-testid="exceptions-open"]) whose href is the row's view,
// rendered only when the view starts with "#/"; any other value
// renders no link.
//
// The Actions column is admin only. An unacknowledged row has
// "Acknowledge" ([data-testid="exceptions-ack"]), which sends
// PUT /api/exceptions/ack with exactly {"key": <key>}; every row has
// "Snooze 7 days" ([data-testid="exceptions-snooze"]), which sends
// the same PUT with snooze_until set to the answer's as_of plus 7
// days (an ISO date); an acknowledged row has "Clear"
// ([data-testid="exceptions-clear"]), which sends
// DELETE /api/exceptions/ack?key=<url-encoded key>. After a success
// the list is fetched again. A 403 shows "Only an admin can
// acknowledge exceptions.", any other failure shows "The change could
// not be saved.", inline in [data-testid="exceptions-action-error"].
// A non-admin sees no Actions column and a note reading "Only an
// admin can acknowledge exceptions."
//
// With no rows the table is replaced by "No exceptions today."; a
// failed fetch shows "Exceptions could not be loaded." Every API
// value is inserted through the DOM APIs as text, never through
// innerHTML. Sign-out resets the view to its first-load state.
(function (global) {
  'use strict';

  var LIST_PATH = '/api/exceptions';
  var ACK_PATH = '/api/exceptions/ack';
  var ME_PATH = '/api/auth/me';

  var ROOT_ID = 'exceptions-root';
  var VIEW_ID = 'view-exceptions';
  var APP_ID = 'app';

  var EM_DASH = '\u2014';
  var MIDDLE_DOT = '\u00b7';

  var EMPTY = 'No exceptions today.';
  var NEW_ONLY_EMPTY = 'Nothing new today.';
  var FILTER_EMPTY = 'No exceptions match these filters.';
  var LOAD_ERROR = 'Exceptions could not be loaded.';
  var ADMIN_NOTE = 'Only an admin can acknowledge exceptions.';
  var ACTION_403 = 'Only an admin can acknowledge exceptions.';
  var ACTION_FAILED = 'The change could not be saved.';
  var SOURCES_FAILED_PREFIX = 'Some signals could not be read: ';

  // hasOwnProperty-guarded look-up: a bare bracket read would hand
  // Object.prototype back for a "__proto__" kind, and the cell would
  // read "[object Object]" instead of the raw value.
  var KIND_LABEL = {
    buffer_stocked_out: 'Buffer stocked out',
    buffer_critical: 'Buffer critical',
    stockout_likely: 'Stockout likely',
    stockout_at_risk: 'Stockout at risk',
    forecast_bias: 'Forecast bias',
    expedite_after_stockout: 'Arrives after stockout',
    expedite_late: 'Late purchase line',
    release_awaiting_approval: 'Awaiting ERP approval',
    transfer_awaiting_release: 'Transfer awaiting release',
    release_failed: 'Release failed',
    transfer_failed: 'Transfer failed',
    erp_push_failed: 'ERP push failed',
    sop_signoff_due: 'S&OP sign-off due',
    erp_code_clash: 'Same codes in two ERPs',
    erp_currency_mismatch: 'ERP reports in another currency',
    cross_erp_opportunity: 'Short in one ERP, excess in the other',
    supplier_risk_high: 'High-risk supplier',
    sole_source_short: 'Sole source, short of stock',
    order_below_minimum: 'Order below supplier minimum',
    standing_grant_run: 'Standing grant run needs attention',
  };

  var COLUMNS = ['Severity', 'Signal', 'Item and Site', 'Detail', 'Detected', 'Status', 'Open'];

  function freshState() {
    return {
      isAdmin: false, // positive /api/auth/me answer only (fail closed)
      answer: null, // the newest GET /api/exceptions answer
      loading: false, // a list fetch is out
      error: null, // the newest failed fetch's message
      showSnoozed: false, // the "Show snoozed" checkbox
      newOnly: false, // the "New only" client-side filter
      kindFilter: '', // the "Signal" kind filter ('' = All signals)
      acting: false, // an action request is out
      actionError: null, // the newest failed action's message
    };
  }

  var state = freshState();

  // The request-sequence guards: only the newest fetch of each kind
  // renders; anything still out the door after a newer fetch (or after
  // a reset) belongs to the past.
  var listSeq = 0;
  var meSeq = 0;
  // The action generation: a reset (sign-out) invalidates an action
  // answer still out the door, so it can neither render into the fresh
  // session's state nor trigger a follow-up fetch.
  var actionGen = 0;

  var rootEl = null;

  /* ------------------------------ helpers ------------------------------ */

  function isFiniteNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }

  function nonEmptyString(value) {
    return typeof value === 'string' && value !== '' ? value : null;
  }

  function pad2(n) {
    return (n < 10 ? '0' : '') + n;
  }

  // One element built by the DOM APIs: a class, an optional text and
  // an optional attribute map. API values reach the page as text or
  // plain attribute values only, never through innerHTML.
  function el(tag, className, text, attrs) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = text;
    }
    if (attrs) {
      for (var name in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, name)) {
          node.setAttribute(name, attrs[name]);
        }
      }
    }
    return node;
  }

  function countOf(counts, key) {
    if (!counts || !isFiniteNumber(counts[key])) {
      return 0;
    }
    return Math.round(counts[key]);
  }

  // The answer's "new today" figure: 0 unless the answer carries a
  // numeric counts.new above 0 — a missing, non-numeric or
  // non-positive field renders no count line.
  function newTodayCount() {
    var counts = state.answer ? state.answer.counts : null;
    if (!counts || !isFiniteNumber(counts.new) || counts.new <= 0) {
      return 0;
    }
    return Math.round(counts.new);
  }

  function kindLabel(kind) {
    if (kind === null || kind === undefined) {
      return EM_DASH;
    }
    if (Object.prototype.hasOwnProperty.call(KIND_LABEL, kind)) {
      return KIND_LABEL[kind];
    }
    return String(kind);
  }

  // The answer's distinct kinds, in the order they first appear
  // among its rows — the Signal filter's options apart from "All
  // signals". A malformed row contributes nothing, and a row whose
  // kind is absent contributes none either.
  function distinctKinds(rows) {
    var kinds = [];
    var seen = {};
    if (!Array.isArray(rows)) {
      return kinds;
    }
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!row || typeof row !== 'object') {
        continue; // a malformed row never breaks the filter
      }
      var kind = row.kind;
      if (kind === null || kind === undefined) {
        continue;
      }
      var key = String(kind);
      if (!Object.prototype.hasOwnProperty.call(seen, key)) {
        seen[key] = true;
        kinds.push(kind);
      }
    }
    return kinds;
  }

  // Does the row pass the Signal filter? "All signals" (the empty
  // choice) passes every row; a chosen kind passes only rows whose
  // kind is that same value.
  function kindMatches(row) {
    if (state.kindFilter === '') {
      return true;
    }
    if (row.kind === null || row.kind === undefined) {
      return false;
    }
    return String(row.kind) === state.kindFilter;
  }

  function severityText(severity) {
    if (severity === 'high') {
      return 'High';
    }
    if (severity === 'medium') {
      return 'Medium';
    }
    return severity === null || severity === undefined ? EM_DASH : String(severity);
  }

  function itemSiteText(row) {
    var item = nonEmptyString(row.item_code);
    var site = nonEmptyString(row.site_code);
    if (!item && !site) {
      return EM_DASH;
    }
    return (item || EM_DASH) + ' / ' + (site || EM_DASH);
  }

  function statusText(row) {
    var text;
    if (row.acknowledged === true) {
      var name = nonEmptyString(row.acknowledged_by_name);
      text = name ? 'Acknowledged by ' + name : 'Acknowledged';
    } else {
      text = 'New';
    }
    // The snooze suffix rides on every snoozed row, acknowledged or
    // not: "New · snoozed until <date>" as much as the
    // "Acknowledged..." forms.
    var snooze = nonEmptyString(row.snooze_until);
    if (snooze) {
      text += ' ' + MIDDLE_DOT + ' snoozed until ' + snooze;
    }
    return text;
  }

  // The answer's as_of (an ISO date) plus 7 days, as an ISO date; the
  // current UTC date plus 7 days when the answer carries no usable
  // as-of, so the button always sends a valid future date.
  function snoozeUntil(answer) {
    var base = null;
    var asOf = answer && answer.as_of;
    if (typeof asOf === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(asOf)) {
      var utc = Date.UTC(
        Number(asOf.slice(0, 4)),
        Number(asOf.slice(5, 7)) - 1,
        Number(asOf.slice(8, 10))
      );
      if (!isNaN(utc)) {
        base = new Date(utc);
      }
    }
    if (!base) {
      var now = new Date();
      base = new Date(
        Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
      );
    }
    base.setTime(base.getTime() + 7 * 86400000);
    return (
      base.getUTCFullYear() +
      '-' +
      pad2(base.getUTCMonth() + 1) +
      '-' +
      pad2(base.getUTCDate())
    );
  }

  // A link only for a planner hash; anything else is not a link.
  function openHref(view) {
    return typeof view === 'string' && view.indexOf('#/') === 0 ? view : null;
  }

  /* ------------------------------ loading ------------------------------ */

  // GET /api/auth/me for the admin role, exactly as the Push to ERP
  // view reads it: only a positive answer with "admin" in the user's
  // roles enables the Actions column; any failure fails closed.
  function loadMe() {
    meSeq += 1;
    var mySeq = meSeq;
    global.Api.get(ME_PATH).then(
      function (me) {
        if (mySeq !== meSeq) {
          return;
        }
        state.isAdmin =
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
        // A 401 already restarted the sign-in flow in api.js; any
        // other failure leaves the Actions column hidden.
        state.isAdmin = false;
        render();
      }
    );
  }

  function listPath() {
    return state.showSnoozed ? LIST_PATH + '?include_snoozed=true' : LIST_PATH;
  }

  function loadList() {
    listSeq += 1;
    var mySeq = listSeq;
    state.loading = true;
    state.error = null;
    render();
    global.Api.get(listPath()).then(
      function (payload) {
        if (mySeq !== listSeq) {
          return; // a newer fetch is out: this answer is stale
        }
        state.loading = false;
        if (
          !payload ||
          typeof payload !== 'object' ||
          Array.isArray(payload) ||
          !Array.isArray(payload.rows)
        ) {
          // The Signal choice has no answer to live on: back to
          // "All signals" so the select and its state agree.
          state.kindFilter = '';
          state.answer = null;
          state.error = LOAD_ERROR;
        } else {
          state.answer = payload;
          // A refetch keeps the Signal choice only while the answer
          // still carries that kind; otherwise it returns to "All
          // signals".
          if (state.kindFilter !== '') {
            var kinds = distinctKinds(payload.rows);
            var stillPresent = false;
            for (var k = 0; k < kinds.length; k++) {
              if (String(kinds[k]) === state.kindFilter) {
                stillPresent = true;
                break;
              }
            }
            if (!stillPresent) {
              state.kindFilter = '';
            }
          }
        }
        render();
      },
      function () {
        if (mySeq !== listSeq) {
          return;
        }
        state.loading = false;
        state.error = LOAD_ERROR;
        render();
      }
    );
  }

  function loadAll() {
    loadMe();
    loadList();
  }

  /* ------------------------------ actions ------------------------------ */

  // "Acknowledge" sends exactly {"key": <key>}; "Snooze 7 days" sends
  // the same PUT with snooze_until set to the answer's as_of plus 7
  // days; "Clear" sends the DELETE with the key as the query
  // parameter. A success fetches the list again; a 403 and any other
  // failure show their message inline.
  function doAction(action, key) {
    if (!state.isAdmin || state.acting || !nonEmptyString(key)) {
      return;
    }
    state.acting = true;
    state.actionError = null;
    render();
    actionGen += 1;
    var myGen = actionGen;

    function succeeded() {
      if (myGen !== actionGen) {
        return;
      }
      state.acting = false;
      state.actionError = null;
      render();
      loadList(); // the stored acknowledgement moved: fetch the list again
    }

    function failed(error) {
      if (myGen !== actionGen) {
        return;
      }
      state.acting = false;
      var status = error && typeof error.status === 'number' ? error.status : 0;
      state.actionError = status === 403 ? ACTION_403 : ACTION_FAILED;
      render();
    }

    if (action === 'clear') {
      global.Api.request(
        ACK_PATH + '?key=' + encodeURIComponent(key),
        { method: 'DELETE' }
      ).then(succeeded, failed);
      return;
    }

    var body = { key: key };
    if (action === 'snooze') {
      body.snooze_until = snoozeUntil(state.answer);
    }
    global.Api.request(ACK_PATH, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(succeeded, failed);
  }

  /* ------------------------------ events ------------------------------ */

  function onRootClick(event) {
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }
    if (target.closest('[data-testid="exceptions-refresh"]')) {
      loadAll();
      return;
    }
    var button = target.closest('button[data-action]');
    if (button) {
      doAction(
        button.getAttribute('data-action'),
        button.getAttribute('data-key')
      );
    }
  }

  function onRootChange(event) {
    var target = event.target;
    if (!target || !target.getAttribute) {
      return;
    }
    if (target.getAttribute('data-testid') === 'exceptions-show-snoozed') {
      state.showSnoozed = target.checked === true;
      loadList();
    }
    if (target.getAttribute('data-testid') === 'exceptions-new-only') {
      // A client-side filter over the current answer: it re-renders
      // the table without any request, and a refetch keeps the tick.
      state.newOnly = target.checked === true;
      render();
    }
    if (target.getAttribute('data-testid') === 'exceptions-kind-filter') {
      // A client-side filter over the current answer: it re-renders
      // the table without any request, and a refetch keeps the choice
      // while the answer still carries the kind.
      state.kindFilter = typeof target.value === 'string' ? target.value : '';
      render();
    }
  }

  /* ------------------------------ rendering ---------------------------- */

  function render() {
    if (!rootEl) {
      return;
    }
    while (rootEl.firstChild) {
      rootEl.removeChild(rootEl.firstChild);
    }
    rootEl.appendChild(shell());
  }

  function shell() {
    var wrap = el('div', 'ex');
    wrap.appendChild(toolbar());

    var failedNote = sourcesFailedNote();
    if (failedNote) {
      wrap.appendChild(failedNote);
    }

    // The non-admin note: the Actions column is absent.
    if (!state.isAdmin) {
      wrap.appendChild(
        el('p', 'ex-note ex-note--admin', ADMIN_NOTE, {
          'data-testid': 'exceptions-admin-note',
        })
      );
    }

    if (state.actionError) {
      wrap.appendChild(
        el('p', 'ex-action-error', state.actionError, {
          'data-testid': 'exceptions-action-error',
        })
      );
    }

    wrap.appendChild(tableRegion());
    return wrap;
  }

  function toolbar() {
    var bar = el('div', 'ex-toolbar');
    if (state.answer) {
      bar.appendChild(
        el('span', 'ex-summary', summaryText(), {
          'data-testid': 'exceptions-summary',
        })
      );
      // The "<n> new today" line: directly after the summary, only
      // when the answer's counts.new is a number above 0.
      var newToday = newTodayCount();
      if (newToday > 0) {
        bar.appendChild(
          el('span', 'ex-new-count', newToday + ' new today', {
            'data-testid': 'exceptions-new-count',
          })
        );
      }
    }
    var label = el('label', 'ex-toggle');
    var checkbox = el(
      'input',
      null,
      null,
      { 'data-testid': 'exceptions-show-snoozed' }
    );
    checkbox.type = 'checkbox';
    checkbox.checked = state.showSnoozed;
    label.appendChild(checkbox);
    label.appendChild(document.createTextNode(' Show snoozed'));
    bar.appendChild(label);
    // The "New only" filter: a client-side tick over the current
    // answer, next to "Show snoozed" — it never makes a request.
    var newOnlyLabel = el('label', 'ex-toggle');
    var newOnlyCheckbox = el(
      'input',
      null,
      null,
      { 'data-testid': 'exceptions-new-only' }
    );
    newOnlyCheckbox.type = 'checkbox';
    newOnlyCheckbox.checked = state.newOnly;
    newOnlyLabel.appendChild(newOnlyCheckbox);
    newOnlyLabel.appendChild(document.createTextNode(' New only'));
    bar.appendChild(newOnlyLabel);
    // The "Signal" filter: "All signals" first, then one option per
    // distinct kind in the current answer, labelled by the kind map,
    // in the order the kinds first appear. Choosing a kind filters
    // the table client-side — it never makes a request.
    var kindFilter = el('select', 'ex-kind-filter', null, {
      'data-testid': 'exceptions-kind-filter',
      'aria-label': 'Signal',
    });
    kindFilter.appendChild(el('option', null, 'All signals', { value: '' }));
    var kinds = distinctKinds(state.answer ? state.answer.rows : null);
    for (var k = 0; k < kinds.length; k++) {
      kindFilter.appendChild(
        el('option', null, kindLabel(kinds[k]), { value: String(kinds[k]) })
      );
    }
    kindFilter.value = state.kindFilter;
    bar.appendChild(kindFilter);
    var refresh = el(
      'button',
      'btn btn-secondary ex-refresh',
      'Refresh',
      { 'data-testid': 'exceptions-refresh' }
    );
    refresh.type = 'button';
    bar.appendChild(refresh);
    return bar;
  }

  function summaryText() {
    var counts = state.answer ? state.answer.counts : null;
    return (
      countOf(counts, 'high') +
      ' high ' +
      MIDDLE_DOT +
      ' ' +
      countOf(counts, 'medium') +
      ' medium ' +
      MIDDLE_DOT +
      ' ' +
      countOf(counts, 'acknowledged') +
      ' acknowledged'
    );
  }

  function sourcesFailedNote() {
    if (state.loading || state.error || !state.answer) {
      return null;
    }
    var failed = state.answer.sources_failed;
    if (!Array.isArray(failed) || failed.length === 0) {
      return null;
    }
    var names = [];
    for (var i = 0; i < failed.length; i++) {
      names.push(String(failed[i]));
    }
    return el(
      'p',
      'ex-note ex-note--failed',
      SOURCES_FAILED_PREFIX + names.join(', '),
      { 'data-testid': 'exceptions-sources-failed' }
    );
  }

  function skeleton() {
    var wrap = el('div', 'ex-skel-wrap', null, { 'aria-hidden': 'true' });
    wrap.appendChild(el('div', 'skel skel-line'));
    wrap.appendChild(el('div', 'skel skel-line skel-wide'));
    wrap.appendChild(el('div', 'skel ex-skel-block'));
    return wrap;
  }

  function tableRegion() {
    var region = el('div', 'ex-scroll');
    var answer = state.answer;
    if (state.loading) {
      region.appendChild(skeleton());
    } else if (state.error) {
      region.appendChild(
        el('p', 'ex-empty', LOAD_ERROR, { 'data-testid': 'exceptions-load-error' })
      );
    } else if (!answer) {
      // Never loaded yet (pre-sign-in activation, or right after a
      // reset): the first-load state is the skeleton.
      region.appendChild(skeleton());
    } else if (state.kindFilter !== '') {
      // The "Signal" filter over the current answer: only the rows
      // of the chosen kind, combined with "New only" — a row shows
      // only when it passes both. No request is involved; when the
      // combination leaves no row the table is replaced by the
      // filter's own empty note.
      var kindRows = [];
      for (var i = 0; i < answer.rows.length; i++) {
        var row = answer.rows[i];
        if (!row || typeof row !== 'object') {
          continue; // a malformed row never breaks the table
        }
        if (row.kind === null || row.kind === undefined) {
          continue;
        }
        if (!kindMatches(row)) {
          continue;
        }
        if (state.newOnly && row.new !== true) {
          continue;
        }
        kindRows.push(row);
      }
      if (kindRows.length === 0) {
        region.appendChild(
          el('p', 'ex-empty', FILTER_EMPTY, {
            'data-testid': 'exceptions-filter-empty',
          })
        );
      } else {
        region.appendChild(table(kindRows));
      }
    } else if (state.newOnly) {
      // The "New only" filter over the current answer: only the rows
      // whose new is true, no request involved. It is honoured even
      // over an answer that carries no rows at all — with nothing
      // new the table is replaced by the filter's own empty note,
      // not the screen's generic one.
      var freshRows = [];
      for (var i = 0; i < answer.rows.length; i++) {
        var row = answer.rows[i];
        if (row && typeof row === 'object' && row.new === true) {
          freshRows.push(row);
        }
      }
      if (freshRows.length === 0) {
        region.appendChild(
          el('p', 'ex-empty', NEW_ONLY_EMPTY, {
            'data-testid': 'exceptions-new-only-empty',
          })
        );
      } else {
        region.appendChild(table(freshRows));
      }
    } else if (answer.rows.length === 0) {
      region.appendChild(
        el('p', 'ex-empty', EMPTY, { 'data-testid': 'exceptions-empty' })
      );
    } else {
      region.appendChild(table(answer.rows));
    }
    return region;
  }

  function table(rows) {
    var tableEl = el('table', 'ex-table', null, {
      'data-testid': 'exceptions-table',
    });

    var headRow = el('tr');
    for (var c = 0; c < COLUMNS.length; c++) {
      headRow.appendChild(el('th', 'ex-th', COLUMNS[c], { scope: 'col' }));
    }
    if (state.isAdmin) {
      headRow.appendChild(el('th', 'ex-th', 'Actions', { scope: 'col' }));
    }
    var thead = el('thead');
    thead.appendChild(headRow);
    tableEl.appendChild(thead);

    var tbody = el('tbody');
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!row || typeof row !== 'object') {
        continue; // a malformed row never breaks the table
      }
      tbody.appendChild(rowEl(row));
    }
    tableEl.appendChild(tbody);
    return tableEl;
  }

  function rowEl(row) {
    var tr = el('tr', 'ex-row', null, { 'data-testid': 'exceptions-row' });

    var severity =
      row.severity === null || row.severity === undefined
        ? ''
        : String(row.severity);
    var severityCell = el('td', 'ex-sev ex-sev--' + (severity === 'high' ? 'high' : severity === 'medium' ? 'medium' : 'other'), severityText(row.severity));
    severityCell.setAttribute('data-severity', severity);
    tr.appendChild(severityCell);

    // The Signal cell: the kind's label, and the "New" badge after
    // the label for a row whose new is true — a missing, false or
    // non-boolean new renders no badge.
    var signalCell = el('td', 'ex-signal');
    signalCell.appendChild(document.createTextNode(kindLabel(row.kind)));
    if (row.new === true) {
      signalCell.appendChild(document.createTextNode(' '));
      signalCell.appendChild(
        el('span', 'exc-new', 'New', { 'data-testid': 'exceptions-new' })
      );
    }
    tr.appendChild(signalCell);
    tr.appendChild(el('td', 'ex-pair', itemSiteText(row)));
    tr.appendChild(
      el('td', 'ex-detail', nonEmptyString(row.detail) || EM_DASH)
    );
    tr.appendChild(
      el(
        'td',
        'ex-detected',
        nonEmptyString(row.detected_on) || EM_DASH
      )
    );
    tr.appendChild(el('td', 'ex-status', statusText(row)));

    var openCell = el('td', 'ex-open');
    var href = openHref(row.view);
    if (href) {
      var link = el('a', 'ex-open-link', 'Open', {
        'data-testid': 'exceptions-open',
      });
      link.setAttribute('href', href);
      openCell.appendChild(link);
    }
    tr.appendChild(openCell);

    if (state.isAdmin) {
      var actionsCell = el('td', 'ex-actions');
      var key = nonEmptyString(row.key) || '';
      if (row.acknowledged !== true) {
        actionsCell.appendChild(
          el(
            'button',
            'btn btn-secondary ex-action',
            'Acknowledge',
            {
              'data-testid': 'exceptions-ack',
              'data-action': 'ack',
              'data-key': key,
            }
          )
        );
      }
      actionsCell.appendChild(
        el(
          'button',
          'btn btn-secondary ex-action',
          'Snooze 7 days',
          {
            'data-testid': 'exceptions-snooze',
            'data-action': 'snooze',
            'data-key': key,
          }
        )
      );
      if (row.acknowledged === true) {
        actionsCell.appendChild(
          el(
            'button',
            'btn btn-secondary ex-action',
            'Clear',
            {
              'data-testid': 'exceptions-clear',
              'data-action': 'clear',
              'data-key': key,
            }
          )
        );
      }
      for (var b = 0; b < actionsCell.children.length; b++) {
        actionsCell.children[b].type = 'button';
      }
      tr.appendChild(actionsCell);
    }

    return tr;
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
    if (!viewActive()) {
      return;
    }
    if (state.acting) {
      // An action request is out the door: activating the view again
      // must not start a second one.
      return;
    }
    var appEl = document.getElementById(APP_ID);
    if (appEl && appEl.hidden) {
      // The shell is not signed in yet (Auth.bootstrap has not
      // completed): no request, the skeleton stays put.
      return;
    }
    loadAll();
  }

  function reset() {
    listSeq += 1; // any answer still out the door belongs to a past session
    meSeq += 1;
    actionGen += 1;
    state = freshState();
    render();
  }

  (function boot() {
    rootEl = document.getElementById(ROOT_ID);
    if (!rootEl) {
      return;
    }
    rootEl.addEventListener('click', onRootClick);
    rootEl.addEventListener('change', onRootChange);
    render();

    var appEl = document.getElementById(APP_ID);
    if (!appEl) {
      return;
    }
    // Sign-in shows #app: when it does and the exceptions view is the
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

  global.Exceptions = {
    activate: activate,
    reset: reset,
  };
})(window);
