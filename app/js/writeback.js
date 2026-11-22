// AIERP planner screen — the Push to ERP view (#/writeback).
//
// #/writeback lists the accepted reorder points the ERP does not have
// yet — GET /api/parameter-writeback/preview through app/js/api.js —
// and the audit of every write so far —
// GET /api/parameter-writeback/history?limit=50. Both are fetched on
// view activation and on the Refresh button
// ([data-testid="writeback-refresh"]); a request-sequence guard
// renders only the newest answers, and a skeleton shows while a fetch
// is out. Whether the viewer is an admin is read from
// GET /api/auth/me's roles, exactly as the Work queue does: an
// unknown or failed read fails closed — the admin-only controls
// render only once /api/auth/me positively says the user's roles
// include "admin".
//
// The summary line ([data-testid="writeback-summary"]) reads
// "<count> outstanding · <writable_count> ready to push", gaining
// " · <n> from buffers" whenever any row's basis is "ddmrp" (n the
// number of such rows) and nothing otherwise. The outstanding table
// ([data-testid="writeback-table"]) scrolls within its own region,
// one row per preview row in the answer's order:
// Select (admin only, and only on writable rows,
// [data-testid="writeback-select"]), Item and Site, ERP ("Acumatica"
// for "acumatica", "Odoo" for "odoo", the raw value otherwise, an em
// dash without one), Reorder point and Max as "<current> →
// <proposed>" in whole units (an em dash for a null current value),
// Money at stake (whole dollars, an em dash when null), Status
// ("Ready" when writable, the blocked_reason otherwise) and Source
// ("Decision" for a "decision" basis, "DDMRP buffer" for a "ddmrp"
// basis, an em dash without one or for anything else), the value
// inserted as text.
//
// The writeback record block ([data-testid="writeback-record"], a
// div directly after the bar and before the outstanding pane)
// measures what the confirmed pushes did to the ERPs:
// GET /api/parameter-writeback/record, no query, fetched through
// app/js/api.js beside the preview and the history — on activation
// and on the Refresh button, and again after a confirmed push. The
// preview's and the history's own Retry buttons do not start it, a
// re-render never does, it keeps its own state and its own
// request-sequence guard, and reset() bumps the sequence and clears
// the state. Whether the record read is out never changes the
// Refresh button's disabled state. While the read is out, after a
// non-200 answer or a network failure, and after a 200 whose body is
// malformed (not an object holding the objects reorder_points and
// buffers, or either of them with pushed, read or unchanged not
// whole numbers of at least 0, unchanged above read, or read above
// pushed), the block is absent and nothing says why; the answer's
// rate field is never read. Each line is a p with the class wb-note,
// reorder points first, then buffers; a kind whose pushed is 0 has
// no line, and with no line at all the block is absent. With read
// above 0 a line reads "<kind> still as pushed in the ERP:
// <unchanged> of <read> read back (<pct>%)" with pct
// Math.round(100 * unchanged / read) worked out from the counts;
// with read 0 it reads "<kind> pushed: <pushed>; none read back by a
// sync yet", <kind> "Reorder points" or "Buffer levels".
//
// Pushing (admin only): the "Push selected to ERP" button
// ([data-testid="writeback-push"]) is disabled until at least one row
// is selected, and pressing it does not write — it opens the inline
// confirm panel ([data-testid="writeback-confirm-panel"]) reading
// "Write <n> reorder point(s) into the ERP? This changes the ERP's
// planning parameters.". Cancel closes the panel and sends nothing;
// Confirm ([data-testid="writeback-confirm"], disabled while the
// request is out) sends ONE POST /api/parameter-writeback/confirm
// carrying the selected item/site pairs. The result list
// ([data-testid="writeback-results"]) then shows one line per
// result — item, site, an outcome badge (Written, Unchanged, Failed
// or Skipped) followed by the result's reason when it has one — and
// both lists are fetched again. A 403 shows "Only an admin can push
// to the ERP.", any other failure "The push could not be completed.";
// neither clears the selection. A non-admin sees no Select column, no
// Push button, and a note reading "Only an admin can push to the
// ERP." instead.
//
// The history table ([data-testid="writeback-history"]) has one row
// per history row, newest first: When (written_at as a local date and
// time), Item, Site and ERP, Reorder point "<before> → <after>", the
// outcome badge, By (written_by_name, else written_by) and the error,
// or empty. An empty preview reads "Nothing to push: every accepted
// reorder point is in the ERP.", an empty history "No writes yet."; a
// failed preview load shows "The push preview could not be loaded."
// and a failed history load "The write history could not be loaded.",
// each with its own Retry. Every API value is inserted as text, never
// as markup. Sign-out resets the view to its first-load state.
(function (global) {
  'use strict';

  var PREVIEW_PATH = '/api/parameter-writeback/preview';
  var HISTORY_PATH = '/api/parameter-writeback/history?limit=50';
  var RECORD_PATH = '/api/parameter-writeback/record';
  var CONFIRM_PATH = '/api/parameter-writeback/confirm';
  var ME_PATH = '/api/auth/me';

  var ROOT_ID = 'writeback-root';
  var VIEW_ID = 'view-writeback';
  var APP_ID = 'app';

  var EM_DASH = '\u2014';
  var ARROW = ' \u2192 ';
  var MIDDLE_DOT = '\u00b7';
  var ADMIN_ONLY_NOTE = 'Only an admin can push to the ERP.';
  var PREVIEW_ERROR = 'The push preview could not be loaded.';
  var HISTORY_ERROR = 'The write history could not be loaded.';
  var PUSH_403 = 'Only an admin can push to the ERP.';
  var PUSH_FAILED = 'The push could not be completed.';
  var PREVIEW_EMPTY = 'Nothing to push: every accepted reorder point is in the ERP.';
  var HISTORY_EMPTY = 'No writes yet.';

  var ERP_LABEL = {
    acumatica: 'Acumatica',
    odoo: 'Odoo',
  };

  var OUTCOME_LABEL = {
    written: 'Written',
    unchanged: 'Unchanged',
    failed: 'Failed',
    skipped: 'Skipped',
  };

  // minimumFractionDigits must be pinned with maximumFractionDigits:
  // for currency style the minimum defaults to the currency's (2 for
  // USD), and a maximum below the minimum makes the constructor throw
  // a RangeError at script load, aborting the whole script before
  // window.Writeback is published.
  var DOLLAR_FORMATTER = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });

  var rootEl = null;

  function freshState() {
    return {
      isAdmin: false, // positive /api/auth/me answer only (fail closed)
      preview: null, // the newest successful preview answer
      historyRows: null, // the newest successful history answer
      record: null, // the newest valid record answer
      previewLoading: false, // a preview fetch is out
      historyLoading: false, // a history fetch is out
      recordLoading: false, // a record fetch is out
      previewError: null, // the newest failed preview load: { status, message }
      historyError: null, // the newest failed history load: { status, message }
      selected: {}, // pair key -> true; survives re-loads
      confirmOpen: false, // the inline confirm panel is open
      pushing: false, // the confirm request is out
      pushResults: null, // the newest confirm results
      pushError: null, // the newest failed push's message
    };
  }

  var state = freshState();

  // The request-sequence guards: only the newest fetch of each kind
  // renders; anything still out the door after a newer fetch (or after
  // a reset) belongs to the past.
  var previewSeq = 0;
  var historySeq = 0;
  var recordSeq = 0;
  var meSeq = 0;
  // The push generation: a reset (sign-out) invalidates a confirm
  // response still out the door, so it can neither render into the
  // fresh session's state nor trigger a follow-up fetch.
  var pushGen = 0;

  /* ------------------------------ helpers ------------------------------ */

  function esc(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function isFiniteNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }

  function nonEmptyString(value) {
    return typeof value === 'string' && value !== '' ? value : null;
  }

  function pad2(n) {
    return (n < 10 ? '0' : '') + n;
  }

  // Whole units with separators (30 -> "30"); an em dash when the
  // server carries no usable figure.
  function wholeNumber(value) {
    var n;
    if (typeof value === 'string') {
      value = value === '' ? NaN : Number(value);
    }
    n = value;
    if (!isFiniteNumber(n)) {
      return EM_DASH;
    }
    return Math.round(n).toLocaleString('en-US');
  }

  // Whole dollars with separators (1200.5 -> "$1,201"); an em dash
  // when the value is null or unusable.
  function wholeDollars(value) {
    if (value === null || value === undefined || value === '') {
      return EM_DASH;
    }
    var n = Number(value);
    if (!isFiniteNumber(n)) {
      return EM_DASH;
    }
    return DOLLAR_FORMATTER.format(Math.round(n));
  }

  // "<current> → <proposed>" in whole units; either side is an em
  // dash when it is null or unusable.
  function paramText(current, proposed) {
    return wholeNumber(current) + ARROW + wholeNumber(proposed);
  }

  // "Acumatica" for "acumatica", "Odoo" for "odoo", the raw value for
  // anything else, an em dash when the answer carries no source. The
  // look-up is hasOwnProperty-guarded: a bare bracket read would hand
  // Object.prototype back for a "__proto__" source, and the cell would
  // read "[object Object]" instead of the raw value.
  function erpLabel(source) {
    if (source === null || source === undefined || source === '') {
      return EM_DASH;
    }
    if (Object.prototype.hasOwnProperty.call(ERP_LABEL, source)) {
      return ERP_LABEL[source];
    }
    return String(source);
  }

  // The "Source" cell: "Decision" for a "decision" basis, "DDMRP
  // buffer" for a "ddmrp" basis, an em dash when the basis is missing
  // or anything else.
  function sourceText(basis) {
    if (basis === 'decision') {
      return 'Decision';
    }
    if (basis === 'ddmrp') {
      return 'DDMRP buffer';
    }
    return EM_DASH;
  }

  // written_at as a local date and time ("YYYY-MM-DD HH:MM").
  function dateTimeText(value) {
    if (value === null || value === undefined || value === '') {
      return EM_DASH;
    }
    var date = value instanceof Date ? value : new Date(value);
    if (isNaN(date.getTime())) {
      return EM_DASH;
    }
    return (
      date.getFullYear() +
      '-' +
      pad2(date.getMonth() + 1) +
      '-' +
      pad2(date.getDate()) +
      ' ' +
      pad2(date.getHours()) +
      ':' +
      pad2(date.getMinutes())
    );
  }

  // The "By" cell: written_by_name, else written_by, else an em dash.
  function byText(row) {
    var name = nonEmptyString(row.written_by_name);
    if (name) {
      return name;
    }
    return nonEmptyString(row.written_by) || EM_DASH;
  }

  // The "Item, Site and ERP" cell of a history row.
  function itemSiteErpText(row) {
    var item = nonEmptyString(row.item_code) || EM_DASH;
    var site = nonEmptyString(row.site_code) || EM_DASH;
    return item + ' / ' + site + ' ' + MIDDLE_DOT + ' ' + erpLabel(row.source_system);
  }

  function pairKey(itemCode, siteCode) {
    return String(itemCode) + '\u0001' + String(siteCode);
  }

  // Both hasOwnProperty-guarded: a bare bracket read would hand
  // Object.prototype back for a "__proto__" outcome, and the badge would
  // read "[object Object]" instead of the raw value.
  function outcomeLabel(outcome) {
    if (outcome === null || outcome === undefined) {
      return EM_DASH;
    }
    if (Object.prototype.hasOwnProperty.call(OUTCOME_LABEL, outcome)) {
      return OUTCOME_LABEL[outcome];
    }
    return String(outcome);
  }

  function outcomeClass(outcome) {
    if (Object.prototype.hasOwnProperty.call(OUTCOME_LABEL, outcome)) {
      return outcome;
    }
    return 'other';
  }

  // The record's counts: whole numbers of at least 0, unchanged no
  // greater than read and read no greater than pushed. The answer's
  // rate field is never read: the percentage is worked out from the
  // counts.
  function recordCountOk(value) {
    return (
      typeof value === 'number' &&
      isFinite(value) &&
      value >= 0 &&
      Math.floor(value) === value
    );
  }

  function recordKindOk(kind) {
    return (
      !!kind &&
      typeof kind === 'object' &&
      !Array.isArray(kind) &&
      recordCountOk(kind.pushed) &&
      recordCountOk(kind.read) &&
      recordCountOk(kind.unchanged) &&
      kind.unchanged <= kind.read &&
      kind.read <= kind.pushed
    );
  }

  // The record body: an object holding the objects reorder_points and
  // buffers, each passing the count checks; anything else is
  // malformed, and the block stays absent.
  function recordOk(payload) {
    return (
      !!payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      recordKindOk(payload.reorder_points) &&
      recordKindOk(payload.buffers)
    );
  }

  // One record line's text: a kind whose pushed is 0 has no line
  // (null); with read above 0 the read-back counts and their own
  // percentage, with read 0 the not-read-back note.
  function recordLineText(label, kind) {
    if (kind.pushed === 0) {
      return null;
    }
    if (kind.read > 0) {
      var pct = Math.round((100 * kind.unchanged) / kind.read);
      return (
        label +
        ' still as pushed in the ERP: ' +
        kind.unchanged +
        ' of ' +
        kind.read +
        ' read back (' +
        pct +
        '%)'
      );
    }
    return label + ' pushed: ' + kind.pushed + '; none read back by a sync yet';
  }

  /* ------------------------------ state reads -------------------------- */

  function rowsOf(preview) {
    if (!preview || !Array.isArray(preview.rows)) {
      return [];
    }
    return preview.rows;
  }

  function selectedPairs() {
    // In the preview's row order: the selected rows the button pushes.
    var out = [];
    var rows = rowsOf(state.preview);
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!row || typeof row !== 'object') {
        continue;
      }
      var key = pairKey(row.item_code, row.site_code);
      if (Object.prototype.hasOwnProperty.call(state.selected, key)) {
        out.push({ item_code: row.item_code, site_code: row.site_code });
      }
    }
    return out;
  }

  function selectedCount() {
    return selectedPairs().length;
  }

  function summaryText() {
    var preview = state.preview;
    if (!preview) {
      return null;
    }
    var rows = rowsOf(preview);
    var count = isFiniteNumber(preview.count)
      ? Math.round(preview.count)
      : rows.length;
    var writable;
    if (isFiniteNumber(preview.writable_count)) {
      writable = Math.round(preview.writable_count);
    } else {
      writable = 0;
      for (var i = 0; i < rows.length; i++) {
        if (rows[i] && rows[i].writable === true) {
          writable += 1;
        }
      }
    }
    var text = count + ' outstanding ' + MIDDLE_DOT + ' ' + writable + ' ready to push';
    var buffered = 0;
    for (var b = 0; b < rows.length; b++) {
      if (rows[b] && rows[b].basis === 'ddmrp') {
        buffered += 1;
      }
    }
    if (buffered > 0) {
      text += ' ' + MIDDLE_DOT + ' ' + buffered + ' from buffers';
    }
    return text;
  }

  /* ------------------------------ loading ------------------------------ */

  // GET /api/auth/me for the admin role, exactly as the Work queue
  // reads it: only a positive answer with "admin" in the user's roles
  // enables the push controls; any failure fails closed.
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
        // other failure leaves the push controls hidden.
        state.isAdmin = false;
        render();
      }
    );
  }

  function loadPreview() {
    previewSeq += 1;
    var mySeq = previewSeq;
    state.previewLoading = true;
    state.previewError = null;
    render();
    global.Api.get(PREVIEW_PATH).then(
      function (payload) {
        if (mySeq !== previewSeq) {
          return; // a newer fetch is out: this answer is stale
        }
        state.previewLoading = false;
        if (
          !payload ||
          typeof payload !== 'object' ||
          Array.isArray(payload) ||
          !Array.isArray(payload.rows)
        ) {
          state.previewError = { status: 0, message: PREVIEW_ERROR };
        } else {
          state.preview = payload;
        }
        render();
      },
      function (error) {
        if (mySeq !== previewSeq) {
          return;
        }
        state.previewLoading = false;
        var status = error && typeof error.status === 'number' ? error.status : 0;
        state.previewError = { status: status, message: PREVIEW_ERROR };
        render();
      }
    );
  }

  function loadHistory() {
    historySeq += 1;
    var mySeq = historySeq;
    state.historyLoading = true;
    state.historyError = null;
    render();
    global.Api.get(HISTORY_PATH).then(
      function (payload) {
        if (mySeq !== historySeq) {
          return;
        }
        state.historyLoading = false;
        if (!Array.isArray(payload)) {
          state.historyError = { status: 0, message: HISTORY_ERROR };
        } else {
          state.historyRows = payload;
        }
        render();
      },
      function (error) {
        if (mySeq !== historySeq) {
          return;
        }
        state.historyLoading = false;
        var status = error && typeof error.status === 'number' ? error.status : 0;
        state.historyError = { status: status, message: HISTORY_ERROR };
        render();
      }
    );
  }

  // GET /api/parameter-writeback/record, no query: beside the
  // preview and the history, on activation and on Refresh (loadAll)
  // and after a confirmed push (reloadLists). The preview's and the
  // history's own Retry buttons do not start it. The answer is kept
  // only when it passes recordOk; while the read is out, after a
  // non-200 answer or a network failure, and after a malformed 200,
  // state.record is null — the block is absent, and nothing says why.
  function loadRecord() {
    recordSeq += 1;
    var mySeq = recordSeq;
    state.recordLoading = true;
    state.record = null;
    render();
    global.Api.get(RECORD_PATH).then(
      function (payload) {
        if (mySeq !== recordSeq) {
          return; // a newer fetch is out: this answer is stale
        }
        state.recordLoading = false;
        state.record = recordOk(payload) ? payload : null;
        render();
      },
      function () {
        if (mySeq !== recordSeq) {
          return;
        }
        state.recordLoading = false;
        state.record = null;
        render();
      }
    );
  }

  // Activation and Refresh both fetch everything again.
  function loadAll() {
    loadMe();
    loadPreview();
    loadHistory();
    loadRecord();
  }

  // After a confirmed push: both lists are fetched again.
  function reloadLists() {
    loadPreview();
    loadHistory();
    loadRecord();
  }

  /* ------------------------------ pushing ------------------------------ */

  function openConfirm() {
    if (state.pushing || !state.isAdmin || selectedCount() === 0) {
      return;
    }
    state.confirmOpen = true;
    state.pushError = null;
    render();
  }

  function cancelConfirm() {
    if (state.pushing) {
      return;
    }
    state.confirmOpen = false;
    render();
  }

  function doPush() {
    if (state.pushing || !state.isAdmin) {
      return;
    }
    var items = selectedPairs();
    if (items.length === 0) {
      return;
    }
    state.pushing = true;
    state.pushError = null;
    render();
    var myGen = pushGen;
    global.Api.request(CONFIRM_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: items }),
    }).then(
      function (data) {
        if (myGen !== pushGen) {
          return; // a reset happened: this answer belongs to a past session
        }
        state.pushing = false;
        state.confirmOpen = false;
        state.pushResults =
          data && Array.isArray(data.results) ? data.results : [];
        render();
        reloadLists();
      },
      function (error) {
        if (myGen !== pushGen) {
          return;
        }
        state.pushing = false;
        state.confirmOpen = false;
        var status = error && typeof error.status === 'number' ? error.status : 0;
        // The selection is kept either way: nothing was written on a
        // 403 or a failure, so the rows may still be pushed.
        state.pushError = status === 403 ? PUSH_403 : PUSH_FAILED;
        render();
      }
    );
  }

  /* ------------------------------ events ------------------------------- */

  function onRootClick(event) {
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }
    if (target.closest('[data-testid="writeback-refresh"]')) {
      loadAll();
      return;
    }
    if (target.closest('[data-testid="writeback-preview-retry"]')) {
      loadPreview();
      return;
    }
    if (target.closest('[data-testid="writeback-history-retry"]')) {
      loadHistory();
      return;
    }
    if (target.closest('[data-testid="writeback-push"]')) {
      openConfirm();
      return;
    }
    if (target.closest('[data-testid="writeback-cancel"]')) {
      cancelConfirm();
      return;
    }
    if (target.closest('[data-testid="writeback-confirm"]')) {
      doPush();
    }
  }

  function onRootChange(event) {
    var target = event.target;
    if (!target || target.tagName !== 'INPUT' || !target.closest) {
      return;
    }
    if (!target.closest('[data-testid="writeback-select"]')) {
      return;
    }
    // The checkbox carries the row's pair key verbatim: item_code and
    // site_code need not be strings, and a key re-derived from
    // string-only attributes would not be the key the row was rendered
    // with, so the selection — and the push — would silently miss the
    // row.
    var key = target.getAttribute('data-key');
    if (!key) {
      return;
    }
    if (target.checked) {
      state.selected[key] = true;
    } else {
      delete state.selected[key];
    }
    render();
  }

  /* ------------------------------ rendering ---------------------------- */

  function render() {
    if (!rootEl) {
      return;
    }
    rootEl.innerHTML = shellHtml();
  }

  // The record block: directly after the bar and before the
  // outstanding pane, one p.wb-note line per kind — reorder points
  // first, then buffers. Absent while the read is out, after a failed
  // or malformed load, and when no kind has a line.
  function recordHtml() {
    if (!state.record) {
      return '';
    }
    var lines = [];
    var reorderText = recordLineText('Reorder points', state.record.reorder_points);
    if (reorderText !== null) {
      lines.push(
        '<p class="wb-note" data-testid="writeback-record-reorder-points">' +
        esc(reorderText) +
        '</p>'
      );
    }
    var bufferText = recordLineText('Buffer levels', state.record.buffers);
    if (bufferText !== null) {
      lines.push(
        '<p class="wb-note" data-testid="writeback-record-buffers">' +
        esc(bufferText) +
        '</p>'
      );
    }
    if (lines.length === 0) {
      return '';
    }
    return '<div class="wb-record" data-testid="writeback-record">' + lines.join('') + '</div>';
  }

  function shellHtml() {
    return (
      '<div class="wb">' +
      barHtml() +
      recordHtml() +
      outstandingPaneHtml() +
      historyPaneHtml() +
      '</div>'
    );
  }

  function barHtml() {
    var summary = summaryText();
    return (
      '<div class="wb-bar">' +
      (summary
        ? '<p class="wb-summary" data-testid="writeback-summary">' +
          esc(summary) +
          '</p>'
        : '') +
      '<button type="button" class="btn btn-secondary wb-refresh" ' +
      'data-testid="writeback-refresh"' +
      (state.previewLoading || state.historyLoading ? ' disabled' : '') +
      '>Refresh</button>' +
      '</div>'
    );
  }

  function skeletonHtml() {
    return (
      '<div class="wb-skel-wrap" aria-hidden="true">' +
      '<div class="skel skel-line"></div>' +
      '<div class="skel skel-line skel-wide"></div>' +
      '<div class="skel wb-skel-block"></div>' +
      '</div>'
    );
  }

  function errorHtml(testid, retryTestid, message) {
    return (
      '<div class="wb-error" role="alert" data-testid="' + testid + '">' +
      '<p class="wb-error-msg">' + esc(message) + '</p>' +
      '<button type="button" class="btn btn-secondary" data-testid="' +
      retryTestid +
      '">Retry</button>' +
      '</div>'
    );
  }

  // The outstanding pane: the preview table (or its skeleton, error
  // and empty note) scrolling in its own region, and below it either
  // the admin push bar or the non-admin note.
  function outstandingPaneHtml() {
    var inner;
    if (state.previewLoading) {
      inner = skeletonHtml();
    } else if (state.previewError) {
      inner = errorHtml(
        'writeback-preview-error',
        'writeback-preview-retry',
        state.previewError.message
      );
    } else if (state.preview && rowsOf(state.preview).length === 0) {
      inner =
        '<p class="wb-empty" data-testid="writeback-empty">' +
        esc(PREVIEW_EMPTY) +
        '</p>';
    } else if (state.preview) {
      inner = previewTableHtml(rowsOf(state.preview));
    } else {
      inner = skeletonHtml();
    }
    return (
      '<div class="wb-pane wb-outstanding">' +
      '<div class="wb-table-region">' + inner + '</div>' +
      (state.isAdmin ? pushBarHtml() : adminNoteHtml()) +
      '</div>'
    );
  }

  function previewTableHtml(rows) {
    var body = [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!row || typeof row !== 'object') {
        continue; // a malformed row never breaks the table
      }
      body.push(previewRowHtml(row));
    }
    return (
      '<table class="wb-table" data-testid="writeback-table">' +
      '<thead><tr>' +
      (state.isAdmin ? '<th scope="col">Select</th>' : '') +
      '<th scope="col">Item and Site</th>' +
      '<th scope="col">ERP</th>' +
      '<th scope="col">Reorder point</th>' +
      '<th scope="col">Max</th>' +
      '<th scope="col">Money at stake</th>' +
      '<th scope="col">Status</th>' +
      '<th scope="col">Source</th>' +
      '</tr></thead>' +
      '<tbody>' +
      body.join('') +
      '</tbody>' +
      '</table>'
    );
  }

  function previewRowHtml(row) {
    var itemCode = nonEmptyString(row.item_code) || EM_DASH;
    var siteCode = nonEmptyString(row.site_code) || EM_DASH;
    // The row's own pair key: item_code and site_code need not be
    // strings, so the checkbox carries the key itself (see
    // onRootChange) rather than values that would not round-trip.
    var key = pairKey(row.item_code, row.site_code);
    // The Select cell is admin-only, and the checkbox writable-rows-only:
    // a row the ERP writer cannot reach has nothing to select.
    var selectable = state.isAdmin && row.writable === true;
    var checked =
      selectable && Object.prototype.hasOwnProperty.call(state.selected, key);
    return (
      '<tr class="wb-row">' +
      (state.isAdmin
        ? '<td class="wb-cell-select">' +
          (selectable
            ? '<input type="checkbox" data-testid="writeback-select" ' +
              'data-key="' +
              esc(key) +
              '"' +
              (checked ? ' checked' : '') +
              ' aria-label="Select ' +
              esc(itemCode) +
              ' / ' +
              esc(siteCode) +
              '">'
            : '') +
          '</td>'
        : '') +
      '<td class="wb-cell-item">' + esc(itemCode + ' / ' + siteCode) + '</td>' +
      '<td class="wb-cell-erp">' + esc(erpLabel(row.source_system)) + '</td>' +
      '<td class="wb-cell-params">' +
      esc(paramText(row.current_reorder_point, row.proposed_reorder_point)) +
      '</td>' +
      '<td class="wb-cell-params">' +
      esc(paramText(row.current_max_qty, row.proposed_max_qty)) +
      '</td>' +
      '<td class="wb-cell-money">' + esc(wholeDollars(row.money_at_stake)) + '</td>' +
      '<td class="wb-cell-status">' +
      (row.writable === true
        ? '<span class="wb-badge wb-badge--ready">Ready</span>'
        : esc(nonEmptyString(row.blocked_reason) || EM_DASH)) +
      '</td>' +
      '<td class="wb-cell-source">' + esc(sourceText(row.basis)) + '</td>' +
      '</tr>'
    );
  }

  // The admin push bar: the Push button, the inline confirm panel
  // (open state), the last push's error (failed state) and the last
  // push's per-pair results.
  function pushBarHtml() {
    var n = selectedCount();
    return (
      '<div class="wb-pushbar">' +
      '<button type="button" class="btn btn-primary wb-push" ' +
      'data-testid="writeback-push"' +
      (n === 0 || state.pushing ? ' disabled' : '') +
      '>Push selected to ERP</button>' +
      (state.confirmOpen ? confirmPanelHtml(n) : '') +
      (state.pushError
        ? '<p class="wb-push-error" role="alert" data-testid="writeback-push-error">' +
          esc(state.pushError) +
          '</p>'
        : '') +
      (state.pushResults ? resultsHtml(state.pushResults) : '') +
      '</div>'
    );
  }

  function confirmPanelHtml(n) {
    return (
      '<div class="wb-confirm" data-testid="writeback-confirm-panel">' +
      '<p class="wb-confirm-msg">' +
      esc(
        'Write ' +
          n +
          " reorder point(s) into the ERP? This changes the ERP's planning parameters."
      ) +
      '</p>' +
      '<div class="wb-confirm-actions">' +
      '<button type="button" class="btn btn-primary" data-testid="writeback-confirm"' +
      (state.pushing ? ' disabled' : '') +
      '>Confirm</button>' +
      '<button type="button" class="btn btn-secondary" data-testid="writeback-cancel"' +
      (state.pushing ? ' disabled' : '') +
      '>Cancel</button>' +
      '</div>' +
      '</div>'
    );
  }

  function resultsHtml(results) {
    var lines = [];
    for (var i = 0; i < results.length; i++) {
      var result = results[i];
      if (!result || typeof result !== 'object') {
        continue; // a malformed result never breaks the list
      }
      var item = nonEmptyString(result.item_code) || EM_DASH;
      var site = nonEmptyString(result.site_code) || EM_DASH;
      lines.push(
        '<li class="wb-result">' +
        '<span class="wb-result-pair">' +
        esc(item + ' / ' + site) +
        '</span>' +
        '<span class="wb-badge wb-badge--' +
        esc(outcomeClass(result.outcome)) +
        '">' +
        esc(outcomeLabel(result.outcome)) +
        '</span>' +
        (nonEmptyString(result.reason)
          ? '<span class="wb-result-reason">' +
            esc(result.reason) +
            '</span>'
          : '') +
        '</li>'
      );
    }
    return '<ul class="wb-results" data-testid="writeback-results">' + lines.join('') + '</ul>';
  }

  // The non-admin note: the push controls are absent.
  function adminNoteHtml() {
    return (
      '<p class="wb-note" data-testid="writeback-admin-note">' +
      esc(ADMIN_ONLY_NOTE) +
      '</p>'
    );
  }

  // The history pane: the audit table (or its skeleton, error and
  // empty note) scrolling in its own region.
  function historyPaneHtml() {
    var inner;
    if (state.historyLoading) {
      inner = skeletonHtml();
    } else if (state.historyError) {
      inner = errorHtml(
        'writeback-history-error',
        'writeback-history-retry',
        state.historyError.message
      );
    } else if (state.historyRows && state.historyRows.length === 0) {
      inner =
        '<p class="wb-empty" data-testid="writeback-history-empty">' +
        esc(HISTORY_EMPTY) +
        '</p>';
    } else if (state.historyRows) {
      inner = historyTableHtml(state.historyRows);
    } else {
      inner = skeletonHtml();
    }
    return (
      '<div class="wb-pane wb-history">' +
      '<div class="wb-table-region">' + inner + '</div>' +
      '</div>'
    );
  }

  function historyTableHtml(rows) {
    var body = [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!row || typeof row !== 'object') {
        continue; // a malformed row never breaks the table
      }
      body.push(
        '<tr class="wb-hrow">' +
        '<td class="wb-cell-when">' + esc(dateTimeText(row.written_at)) + '</td>' +
        '<td class="wb-cell-item">' + esc(itemSiteErpText(row)) + '</td>' +
        '<td class="wb-cell-params">' +
        esc(paramText(row.before_reorder_point, row.after_reorder_point)) +
        '</td>' +
        '<td class="wb-cell-outcome">' +
        '<span class="wb-badge wb-badge--' +
        esc(outcomeClass(row.outcome)) +
        '">' +
        esc(outcomeLabel(row.outcome)) +
        '</span>' +
        '</td>' +
        '<td class="wb-cell-by">' + esc(byText(row)) + '</td>' +
        '<td class="wb-cell-error">' + esc(nonEmptyString(row.error) || '') + '</td>' +
        '</tr>'
      );
    }
    return (
      '<table class="wb-table" data-testid="writeback-history">' +
      '<thead><tr>' +
      '<th scope="col">When</th>' +
      '<th scope="col">Item, Site and ERP</th>' +
      '<th scope="col">Reorder point</th>' +
      '<th scope="col">Outcome</th>' +
      '<th scope="col">By</th>' +
      '<th scope="col">Error</th>' +
      '</tr></thead>' +
      '<tbody>' +
      body.join('') +
      '</tbody>' +
      '</table>'
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
    if (!viewActive()) {
      return;
    }
    if (state.pushing) {
      // A confirm request is out the door: activating the view again
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
    previewSeq += 1; // any answer still out the door belongs to a past session
    historySeq += 1;
    recordSeq += 1;
    meSeq += 1;
    pushGen += 1;
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
    // Sign-in shows #app: when it does and the writeback view is the
    // active one, the lists load then — the pre-sign-in activation
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

  global.Writeback = {
    activate: activate,
    reset: reset,
  };
})(window);
