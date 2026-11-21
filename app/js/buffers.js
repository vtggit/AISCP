// AIERP planner screen — the Buffers view (#/buffers).
//
// #/buffers shows the DDMRP plan — GET /api/ddmrp/plan through
// app/js/api.js — one row per designated item-site whose buffer zones
// could be computed, and the unsized designations —
// GET /api/ddmrp/zones — the rows whose zones carry a status other
// than "ok". Both are fetched on view activation and on the Refresh
// button ([data-testid="buffers-refresh"]); a request-sequence guard
// renders only the newest answers, and a skeleton shows while a fetch
// is out. Whether the viewer is an admin is read from
// GET /api/auth/me's roles, exactly as the Push to ERP view does: an
// unknown or failed read fails closed.
//
// The summary chips ([data-testid="buffers-filter"]) read "All",
// "Order signals <n>" and "On-hand alerts <n>"; the alert count is the
// plan summary's stocked_out plus critical plus low. Choosing a chip
// filters the table client-side, with no new request.
//
// The table ([data-testid="buffers-table"]) scrolls within its own
// region, one row per plan row in the answer's order: Item and Site,
// Zones (a horizontal bar of red, amber and green segments whose
// widths are proportional to the red, yellow and green zones, a
// marker where the net flow sits against the top of green — capped at
// the bar's end — and the "TOR <n> · TOY <n> · TOG <n>" tops below),
// Net flow (whole units), Zone (a "Red"/"Yellow"/"Green"/"Over"
// badge), Order ("Order <n>" on a signal, an em dash without), On hand
// (whole units, a "Stocked out"/"Critical"/"Low"/"OK" badge) and
// Priority ("<pct>%" of the top of green, an em dash without).
//
// The zone rows whose status is not "ok" are listed below the table
// ([data-testid="buffers-unsized"]) as "<item> / <site>: no demand"
// or "<item> / <site>: no lead time". A note reads "Qualified demand:
// no open sales-order demand in the feed yet — net flow is on hand
// plus open supply." whenever any plan row carries the feed's
// no-open-demand note.
//
// Admin designation controls: the "Add buffer" form
// ([data-testid="buffers-add"]) with item and site code inputs sends
// PUT /api/ddmrp/buffers; each row and each unsized line has a
// "Remove" button ([data-testid="buffers-remove"]) that, after the
// inline confirm reading "Stop buffering <item> / <site>?", sends the
// DELETE with the pair as query parameters. Both refetch afterwards.
// A 404 shows the server's detail inline; any other failure shows
// "The change could not be saved." Non-admins get neither the form
// nor the Remove buttons.
//
// Below the table and the unsized list, three independent sections,
// each fetched through app/js/api.js on activation and on Refresh,
// alongside the plan and the zones:
//   * "Suggested buffers" ([data-testid="buffers-candidates"]) —
//     GET /api/ddmrp/candidates: one row per candidate, in the
//     answer's order (item / site, class, annual value in whole
//     dollars, lead time "<n> d", the reason); for an admin a
//     "Designate" button ([data-testid="buffers-designate"]) per row
//     that sends PUT /api/ddmrp/buffers with the pair and then
//     refetches everything.
//   * "Planned adjustments" ([data-testid="buffers-adjustments"]) —
//     GET /api/ddmrp/adjustments: one line per adjustment (the scope
//     "All buffers" or item / site, the window "<start> – <end>",
//     "×<factor>", the note); for an admin an add form
//     ([data-testid="buffers-adjust-add"]) with optional item and
//     site, start and end dates, factor and note, that sends POST
//     /api/ddmrp/adjustments — a 409, 422 or 404 shows the server's
//     detail inline and keeps the six-field draft — and a Delete per
//     line ([data-testid="buffers-adjust-delete"]) that, after the
//     inline "Delete this adjustment?", sends the DELETE with the
//     id. Any other failure shows "The change could not be saved.".
//     Directly after the stored lines (or their empty note), and
//     before the add form, a proposals block
//     ([data-testid="buffers-adjust-proposals"]) — GET
//     /api/ddmrp/adjustments/proposals, fetched with the sections on
//     activation and Refresh under its own request-sequence guard
//     (proposalsSeq, advanced by the sign-out reset) and with no
//     note and no loading placeholder: a failed read (any status or
//     a network error) or any body that is not an object whose
//     "proposals" is an array with at least one well-formed line
//     (string item_code, site_code, start_on and end_on; finite
//     factor, forecast_daily and adu_base) leaves the block out and
//     the section renders exactly as before. Shown, the block leads
//     "Proposed from the forecast" and lists one line per proposal
//     ([data-testid="buffers-adjust-proposal"]) in the answer's
//     order, reusing the adjustments' list classes: the pair, the
//     window "<start_on> – <end_on>", "×<factor>", "forecast
//     <forecast_daily>/day against <adu_base>/day" (one decimal
//     each) and, when existing_factor is not null, "now
//     ×<existing_factor>". For an admin each line ends with a
//     "Use" button ([data-testid="buffers-adjust-use"]) that types
//     the proposal into the add form's draft — item and site,
//     start and end dates, the factor as String(factor) and the note
//     "Proposed from the forecast: <forecast_daily>/day against
//     <adu_base>/day" — and clears a previous add error, without
//     sending anything; the admin saves with "Add adjustment" as
//     before.
//     After the proposals block (or where the block would sit
//     without it), and before the add form, a record line
//     ([data-testid="buffers-adjust-record"]) — GET
//     /api/ddmrp/adjustments/record, fetched with the sections on
//     activation and Refresh under its own request-sequence guard
//     (recordSeq, advanced by the sign-out reset) and with no note
//     and no loading placeholder: a failed read (any status or a
//     network error) or any body that is not an object whose
//     "saved", "with_proposal", "taken", "below" and "above" are
//     all whole numbers of zero or more, of the number type (a
//     numeric string is not usable), leaves the line out and the
//     section renders exactly as before. Shown, it reuses the note
//     class and reads, in whole numbers with separators, "Saved at
//     the proposal: <taken> of <with_proposal> adjustments saved
//     where a factor was proposed (<below> below it, <above> above
//     it)" for a with_proposal above zero, or "No adjustment has
//     been saved where a factor was proposed yet." without.
//   * "Buffer performance (last 90 days)"
//     ([data-testid="buffers-performance"]) — GET
//     /api/ddmrp/performance: one row per buffer (item / site, days
//     observed, a stacked bar whose red, yellow, green and over
//     segments are proportional to the four percentages, "<n>%
//     stocked out").
// Each section fails on its own: a failed fetch shows that section's
// one-line note ("Suggestions could not be loaded." / "Adjustments
// could not be loaded." / "Performance could not be loaded."), never
// blocks or replaces the main table, and never leaves a loading
// placeholder. A plan row whose zone carries an adjustment_factor
// other than 1 badges its zone bar with "×<factor>".
//
// With no designations the screen reads "No buffers yet. An admin
// chooses which item-sites to buffer."; a failed load shows "The
// buffers could not be loaded." with Retry. Every API value is
// inserted as text, never as markup. Sign-out resets the view to its
// first-load state.
(function (global) {
  'use strict';

  var PLAN_PATH = '/api/ddmrp/plan';
  var ZONES_PATH = '/api/ddmrp/zones';
  var BUFFERS_PATH = '/api/ddmrp/buffers';
  var CANDIDATES_PATH = '/api/ddmrp/candidates';
  var ADJUSTMENTS_PATH = '/api/ddmrp/adjustments';
  var PROPOSALS_PATH = '/api/ddmrp/adjustments/proposals';
  var RECORD_PATH = '/api/ddmrp/adjustments/record';
  var PERFORMANCE_PATH = '/api/ddmrp/performance';
  var ME_PATH = '/api/auth/me';

  var ROOT_ID = 'buffers-root';
  var VIEW_ID = 'view-buffers';
  var APP_ID = 'app';

  var EM_DASH = '\u2014';
  var MIDDLE_DOT = '\u00b7';
  var NO_DEMAND_NOTE = 'no open sales-order demand in the feed';
  var QUALIFIED_NOTE =
    'Qualified demand: no open sales-order demand in the feed yet \u2014 net flow is on hand plus open supply.';
  var EMPTY = 'No buffers yet. An admin chooses which item-sites to buffer.';
  var LOAD_ERROR = 'The buffers could not be loaded.';
  var SAVE_FAILED = 'The change could not be saved.';
  var UNSIZED_TITLE = 'Unsized buffers';
  var CANDIDATES_TITLE = 'Suggested buffers';
  var ADJUSTMENTS_TITLE = 'Planned adjustments';
  var PROPOSALS_LEAD = 'Proposed from the forecast';
  var PERFORMANCE_TITLE = 'Buffer performance (last 90 days)';
  var CANDIDATES_EMPTY =
    'No suggestions: every A and B item-site with a lead time of a week or more is already buffered, or none qualifies.';
  var ADJUSTMENTS_EMPTY = 'No planned adjustments.';
  var PERFORMANCE_EMPTY = 'No history yet: buffer status is captured daily.';
  var CANDIDATES_LOAD_ERROR = 'Suggestions could not be loaded.';
  var ADJUSTMENTS_LOAD_ERROR = 'Adjustments could not be loaded.';
  var PERFORMANCE_LOAD_ERROR = 'Performance could not be loaded.';
  var ALL_BUFFERS_SCOPE = 'All buffers';
  var DELETE_ADJUSTMENT_QUESTION = 'Delete this adjustment?';
  var TIMES = '\u00d7'; // ×
  var EN_DASH = '\u2013'; // –

  // hasOwnProperty-guarded look-ups: a bare bracket read would hand
  // Object.prototype back for a "__proto__" status, and the badge
  // would read "[object Object]" instead of the raw value.
  var ZONE_LABEL = { red: 'Red', yellow: 'Yellow', green: 'Green', over: 'Over' };
  var ZONE_CLASS = { red: 'red', yellow: 'amber', green: 'green', over: 'over' };
  var ON_HAND_LABEL = {
    stocked_out: 'Stocked out',
    critical: 'Critical',
    low: 'Low',
    ok: 'OK',
  };
  var ON_HAND_CLASS = {
    stocked_out: 'stockout',
    critical: 'critical',
    low: 'low',
    ok: 'ok',
  };

  var rootEl = null;

  function freshState() {
    return {
      isAdmin: false, // positive /api/auth/me answer only (fail closed)
      plan: null, // the newest successful plan answer
      zones: null, // the newest successful zones answer
      loading: false, // a plan/zones fetch is out
      error: null, // the newest failed load: { status, message }
      filter: 'all', // 'all' | 'signals' | 'alerts'
      saving: false, // a PUT or DELETE is out
      addError: null, // the last failed add's message
      confirmRemove: null, // { key } the open remove confirm
      removeError: null, // the last failed remove's message
      designateError: null, // the last failed designation's message
      candidates: null, // the newest successful candidates answer
      candidatesLoading: false, // a candidates fetch is out
      candidatesError: null, // the newest failed candidates fetch's note
      adjustments: null, // the newest successful adjustments answer
      adjustmentsLoading: false, // an adjustments fetch is out
      adjustmentsError: null, // the newest failed adjustments fetch's note
      performance: null, // the newest successful performance answer
      performanceLoading: false, // a performance fetch is out
      performanceError: null, // the newest failed performance fetch's note
      adjustDraft: freshAdjustDraft(), // the six in-progress form fields
      adjustSaving: false, // an adjustment POST or DELETE is out
      adjustError: null, // the last failed adjustment add's message
      confirmAdjustDelete: null, // { id } the open adjustment delete confirm
      adjustDeleteError: null, // the last failed adjustment delete's message
      proposals: null, // the newest usable proposals answer (well-formed lines)
      record: null, // the newest usable record answer (whole figures)
    };
  }

  // The "Planned adjustments" add form's draft: six fields, item and
  // site optional. A failed save (the 409 conflict above all) keeps
  // this object, so the re-render re-types the six fields into the
  // form instead of wiping them.
  function freshAdjustDraft() {
    return {
      item_code: '',
      site_code: '',
      start_on: '',
      end_on: '',
      factor: '',
      note: '',
    };
  }

  var state = freshState();

  // The request-sequence guards: only the newest fetch of each kind
  // renders; anything still out the door after a newer fetch (or after
  // a reset) belongs to the past.
  var loadSeq = 0;
  var meSeq = 0;
  // The save generation: a reset (sign-out) invalidates a PUT/DELETE
  // still out the door, so it can neither render into the fresh
  // session's state nor trigger a follow-up fetch.
  var saveGen = 0;
  // The section request-sequence guards: one per new endpoint, so a
  // stale answer from one section's fetch can never render into, or
  // blank, the other sections.
  var candidatesSeq = 0;
  var adjustmentsSeq = 0;
  var performanceSeq = 0;
  var proposalsSeq = 0;
  var recordSeq = 0;

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

  // "<planning_priority_pct>%" with one decimal (16 -> "16.0%"), an em
  // dash when the server carries no figure (top of green is 0).
  function priorityText(value) {
    if (typeof value === 'string') {
      value = value === '' ? NaN : Number(value);
    }
    if (!isFiniteNumber(value)) {
      return EM_DASH;
    }
    return value.toFixed(1) + '%';
  }

  // The adjustment factor, trimmed (1.5 -> "1.5", 2 -> "2"), an em
  // dash when the answer carries no usable figure.
  function factorText(value) {
    if (typeof value === 'string') {
      value = value === '' ? NaN : Number(value);
    }
    if (!isFiniteNumber(value)) {
      return EM_DASH;
    }
    return String(Math.round(value * 10000) / 10000);
  }

  // The performance row's stocked-out share, one decimal
  // (5 -> "5.0% stocked out"), an em dash without.
  function stockedOutText(value) {
    if (typeof value === 'string') {
      value = value === '' ? NaN : Number(value);
    }
    if (!isFiniteNumber(value)) {
      return EM_DASH;
    }
    return value.toFixed(1) + '% stocked out';
  }

  // A zone width for the bar: whole units, never negative, zero when
  // the answer carries no usable figure.
  function zoneValue(value) {
    if (typeof value === 'string') {
      value = value === '' ? NaN : Number(value);
    }
    if (!isFiniteNumber(value) || value < 0) {
      return 0;
    }
    return value;
  }

  function pairKey(itemCode, siteCode) {
    return String(itemCode) + '\u0001' + String(siteCode);
  }

  function pairText(itemCode, siteCode) {
    var item = nonEmptyString(itemCode) || EM_DASH;
    var site = nonEmptyString(siteCode) || EM_DASH;
    return item + ' / ' + site;
  }

  function zoneLabel(zone) {
    if (zone === null || zone === undefined) {
      return EM_DASH;
    }
    if (Object.prototype.hasOwnProperty.call(ZONE_LABEL, zone)) {
      return ZONE_LABEL[zone];
    }
    return String(zone);
  }

  function zoneClass(zone) {
    if (Object.prototype.hasOwnProperty.call(ZONE_CLASS, zone)) {
      return ZONE_CLASS[zone];
    }
    return 'other';
  }

  function onHandLabel(status) {
    if (status === null || status === undefined) {
      return EM_DASH;
    }
    if (Object.prototype.hasOwnProperty.call(ON_HAND_LABEL, status)) {
      return ON_HAND_LABEL[status];
    }
    return String(status);
  }

  function onHandClass(status) {
    if (Object.prototype.hasOwnProperty.call(ON_HAND_CLASS, status)) {
      return ON_HAND_CLASS[status];
    }
    return 'other';
  }

  // The row an inline confirm refers to, by pair key: the plan's rows
  // first, then the zones' (an unsized line has no plan row).
  function rowByKey(key) {
    var rows = planRows().concat(unsizedRows());
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (row && pairKey(row.item_code, row.site_code) === key) {
        return row;
      }
    }
    return null;
  }

  /* ------------------------------ state reads -------------------------- */

  function planRows() {
    if (!state.plan || typeof state.plan !== 'object' || Array.isArray(state.plan)) {
      return [];
    }
    return Array.isArray(state.plan.rows) ? state.plan.rows : [];
  }

  function zoneRows() {
    if (!state.zones || typeof state.zones !== 'object' || Array.isArray(state.zones)) {
      return [];
    }
    return Array.isArray(state.zones.rows) ? state.zones.rows : [];
  }

  function unsizedRows() {
    var rows = zoneRows();
    var out = [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (row && typeof row === 'object' && row.status !== 'ok') {
        out.push(row);
      }
    }
    return out;
  }

  function candidateRows() {
    if (!state.candidates || typeof state.candidates !== 'object' || Array.isArray(state.candidates)) {
      return [];
    }
    return Array.isArray(state.candidates.rows) ? state.candidates.rows : [];
  }

  // The adjustments answer is a bare list (GET /api/ddmrp/adjustments
  // returns the rows, not a { rows } envelope); the envelope form is
  // tolerated as well.
  function adjustmentRows() {
    if (Array.isArray(state.adjustments)) {
      return state.adjustments;
    }
    if (state.adjustments && typeof state.adjustments === 'object') {
      return Array.isArray(state.adjustments.rows) ? state.adjustments.rows : [];
    }
    return [];
  }

  function performanceRows() {
    if (!state.performance || typeof state.performance !== 'object' || Array.isArray(state.performance)) {
      return [];
    }
    return Array.isArray(state.performance.rows) ? state.performance.rows : [];
  }

  function adjustmentById(id) {
    var rows = adjustmentRows();
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (row && typeof row === 'object' && String(row.id) === String(id)) {
        return row;
      }
    }
    return null;
  }

  // The plan summary's counts; derived from the rows when the answer
  // carries no summary.
  function summaryCounts() {
    var rows = planRows();
    var summary = state.plan && typeof state.plan === 'object' ? state.plan.summary : null;
    var read = function (name) {
      if (summary && isFiniteNumber(summary[name])) {
        return Math.round(summary[name]);
      }
      return null;
    };
    var signals = read('signals');
    var stockedOut = read('stocked_out');
    var critical = read('critical');
    var low = read('low');
    if (signals === null) {
      signals = 0;
      for (var i = 0; i < rows.length; i++) {
        if (rows[i] && typeof rows[i] === 'object' && rows[i].order_signal === true) {
          signals += 1;
        }
      }
    }
    if (stockedOut === null) {
      stockedOut = 0;
      for (var j = 0; j < rows.length; j++) {
        if (
          rows[j] &&
          typeof rows[j] === 'object' &&
          rows[j].on_hand_status === 'stocked_out'
        ) {
          stockedOut += 1;
        }
      }
    }
    if (critical === null) {
      critical = 0;
      for (var k = 0; k < rows.length; k++) {
        if (
          rows[k] &&
          typeof rows[k] === 'object' &&
          rows[k].on_hand_status === 'critical'
        ) {
          critical += 1;
        }
      }
    }
    if (low === null) {
      low = 0;
      for (var m = 0; m < rows.length; m++) {
        if (
          rows[m] &&
          typeof rows[m] === 'object' &&
          rows[m].on_hand_status === 'low'
        ) {
          low += 1;
        }
      }
    }
    return {
      signals: signals,
      alerts: stockedOut + critical + low,
    };
  }

  // The qualified-demand note is shown whenever any plan row carries
  // the feed's no-open-demand note.
  function hasQualifiedNote() {
    var rows = planRows();
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!row || typeof row !== 'object' || !Array.isArray(row.notes)) {
        continue;
      }
      if (row.notes.indexOf(NO_DEMAND_NOTE) !== -1) {
        return true;
      }
    }
    return false;
  }

  function visibleRows() {
    var rows = planRows();
    if (state.filter === 'signals') {
      return rows.filter(function (row) {
        return row && typeof row === 'object' && row.order_signal === true;
      });
    }
    if (state.filter === 'alerts') {
      return rows.filter(function (row) {
        return (
          row &&
          typeof row === 'object' &&
          Object.prototype.hasOwnProperty.call(ON_HAND_LABEL, row.on_hand_status) &&
          row.on_hand_status !== 'ok'
        );
      });
    }
    return rows;
  }

  function noDesignations() {
    return planRows().length === 0 && zoneRows().length === 0;
  }

  /* ------------------------------ loading ------------------------------ */

  // GET /api/auth/me for the admin role, exactly as the Push to ERP
  // view reads it: only a positive answer with "admin" in the user's
  // roles enables the designation controls; any failure fails closed.
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
        // other failure leaves the controls hidden.
        state.isAdmin = false;
        render();
      }
    );
  }

  // The plan and the zones load as one: both answers are required for
  // the screen, a request-sequence guard renders only the newest pair,
  // and a skeleton shows while either fetch is out.
  function loadPlanAndZones() {
    loadSeq += 1;
    var mySeq = loadSeq;
    state.loading = true;
    state.error = null;
    state.removeError = null;
    state.confirmRemove = null;
    render();
    var pending = 2;
    var failed = false;
    var failStatus = 0;
    var settle = function () {
      pending -= 1;
      if (pending !== 0 || mySeq !== loadSeq) {
        return; // a newer load is out: this answer is stale
      }
      state.loading = false;
      if (failed) {
        state.error = { status: failStatus, message: LOAD_ERROR };
      }
      render();
    };
    global.Api.get(PLAN_PATH).then(
      function (payload) {
        if (mySeq !== loadSeq) {
          return;
        }
        if (
          !payload ||
          typeof payload !== 'object' ||
          Array.isArray(payload) ||
          !Array.isArray(payload.rows)
        ) {
          failed = true;
          failStatus = 0;
        } else {
          state.plan = payload;
        }
        settle();
      },
      function (error) {
        if (mySeq !== loadSeq) {
          return;
        }
        failed = true;
        failStatus = error && typeof error.status === 'number' ? error.status : 0;
        settle();
      }
    );
    global.Api.get(ZONES_PATH).then(
      function (payload) {
        if (mySeq !== loadSeq) {
          return;
        }
        if (
          !payload ||
          typeof payload !== 'object' ||
          Array.isArray(payload) ||
          !Array.isArray(payload.rows)
        ) {
          failed = true;
          failStatus = 0;
        } else {
          state.zones = payload;
        }
        settle();
      },
      function (error) {
        if (mySeq !== loadSeq) {
          return;
        }
        failed = true;
        failStatus = error && typeof error.status === 'number' ? error.status : 0;
        settle();
      }
    );
  }

  // The three new sections load as three independent fetches: each
  // has its own sequence guard and its own one-line error note, so a
  // failing endpoint never blocks or replaces the main table — and
  // never the other sections.
  function loadSections() {
    loadCandidates();
    loadAdjustments();
    loadPerformance();
    loadProposals();
    loadRecord();
  }

  function loadCandidates() {
    candidatesSeq += 1;
    var mySeq = candidatesSeq;
    state.candidatesLoading = true;
    state.candidatesError = null;
    render();
    global.Api.get(CANDIDATES_PATH).then(
      function (payload) {
        if (mySeq !== candidatesSeq) {
          return; // a newer fetch is out: this answer is stale
        }
        if (
          !payload ||
          typeof payload !== 'object' ||
          Array.isArray(payload) ||
          !Array.isArray(payload.rows)
        ) {
          state.candidatesError = CANDIDATES_LOAD_ERROR;
        } else {
          state.candidates = payload;
        }
        state.candidatesLoading = false;
        render();
      },
      function () {
        if (mySeq !== candidatesSeq) {
          return;
        }
        state.candidatesError = CANDIDATES_LOAD_ERROR;
        state.candidatesLoading = false;
        render();
      }
    );
  }

  function loadAdjustments() {
    adjustmentsSeq += 1;
    var mySeq = adjustmentsSeq;
    state.adjustmentsLoading = true;
    state.adjustmentsError = null;
    render();
    global.Api.get(ADJUSTMENTS_PATH).then(
      function (payload) {
        if (mySeq !== adjustmentsSeq) {
          return;
        }
        var rows = null;
        if (Array.isArray(payload)) {
          rows = payload;
        } else if (
          payload &&
          typeof payload === 'object' &&
          Array.isArray(payload.rows)
        ) {
          rows = payload.rows;
        }
        if (rows === null) {
          state.adjustmentsError = ADJUSTMENTS_LOAD_ERROR;
        } else {
          state.adjustments = payload;
        }
        state.adjustmentsLoading = false;
        render();
      },
      function () {
        if (mySeq !== adjustmentsSeq) {
          return;
        }
        state.adjustmentsError = ADJUSTMENTS_LOAD_ERROR;
        state.adjustmentsLoading = false;
        render();
      }
    );
  }

  function loadPerformance() {
    performanceSeq += 1;
    var mySeq = performanceSeq;
    state.performanceLoading = true;
    state.performanceError = null;
    render();
    global.Api.get(PERFORMANCE_PATH).then(
      function (payload) {
        if (mySeq !== performanceSeq) {
          return;
        }
        if (
          !payload ||
          typeof payload !== 'object' ||
          Array.isArray(payload) ||
          !Array.isArray(payload.rows)
        ) {
          state.performanceError = PERFORMANCE_LOAD_ERROR;
        } else {
          state.performance = payload;
        }
        state.performanceLoading = false;
        render();
      },
      function () {
        if (mySeq !== performanceSeq) {
          return;
        }
        state.performanceError = PERFORMANCE_LOAD_ERROR;
        state.performanceLoading = false;
        render();
      }
    );
  }

  // The proposals' read, beside the sections' own reads: unlike them
  // it carries no note and no loading placeholder — a failed read
  // (any status or a network error) or any body that is not an
  // object whose "proposals" is an array with at least one
  // well-formed line simply leaves the block out, and the section
  // renders exactly as before. Its own sequence guard keeps a stale
  // answer from rendering into a newer load.
  function loadProposals() {
    proposalsSeq += 1;
    var mySeq = proposalsSeq;
    global.Api.get(PROPOSALS_PATH).then(
      function (payload) {
        if (mySeq !== proposalsSeq) {
          return; // a newer read is out: this answer is stale
        }
        var usable = usableProposals(payload);
        if (state.proposals !== usable) {
          state.proposals = usable;
          render();
        }
      },
      function () {
        if (mySeq !== proposalsSeq) {
          return;
        }
        if (state.proposals !== null) {
          state.proposals = null;
          render();
        }
      }
    );
  }

  // The record's read, beside the sections' own reads: unlike them
  // it carries no note and no loading placeholder — a failed read
  // (any status or a network error) or any body that is not an
  // object whose "saved", "with_proposal", "taken", "below" and
  // "above" are all whole numbers of zero or more, of the number
  // type, simply leaves the line out, and the section renders
  // exactly as before. Its own sequence guard keeps a stale answer
  // from rendering into a newer load.
  function loadRecord() {
    recordSeq += 1;
    var mySeq = recordSeq;
    global.Api.get(RECORD_PATH).then(
      function (payload) {
        if (mySeq !== recordSeq) {
          return; // a newer read is out: this answer is stale
        }
        var usable = usableRecord(payload);
        if (state.record !== usable) {
          state.record = usable;
          render();
        }
      },
      function () {
        if (mySeq !== recordSeq) {
          return;
        }
        if (state.record !== null) {
          state.record = null;
          render();
        }
      }
    );
  }

  // A usable record answer: an object whose "saved", "with_proposal",
  // "taken", "below" and "above" are all whole numbers of zero or
  // more, of the number type — a numeric string is not usable.
  // Anything less answers null and the line stays out.
  function usableRecord(body) {
    var fields = ['saved', 'with_proposal', 'taken', 'below', 'above'];
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      return null;
    }
    for (var i = 0; i < fields.length; i++) {
      var value = body[fields[i]];
      if (
        typeof value !== 'number' ||
        !isFinite(value) ||
        value < 0 ||
        Math.round(value) !== value
      ) {
        return null;
      }
    }
    return body;
  }

  // Activation and Refresh both fetch everything again — the plan and
  // the zones, and the three sections alongside them.
  function loadAll() {
    loadMe();
    loadPlanAndZones();
    loadSections();
  }

  // After a stored designation or adjustment changed: everything that
  // could have moved is fetched again — the plan and the zones, and
  // the three sections (a designation leaves the candidate pool; an
  // adjustment moves the plan rows' factors and the adjustments'
  // list).
  function reloadEverything() {
    loadPlanAndZones();
    loadSections();
  }

  /* ------------------------------ saving ------------------------------- */

  // The "Add buffer" form: one PUT with the typed pair.
  function doAdd(itemCode, siteCode) {
    if (!state.isAdmin || state.saving) {
      return;
    }
    state.saving = true;
    state.addError = null;
    render();
    var myGen = saveGen;
    global.Api.request(BUFFERS_PATH, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_code: itemCode, site_code: siteCode }),
    }).then(
      function () {
        if (myGen !== saveGen) {
          return; // a reset happened: this answer belongs to a past session
        }
        state.saving = false;
        render();
        reloadEverything();
      },
      function (error) {
        if (myGen !== saveGen) {
          return;
        }
        state.saving = false;
        var status = error && typeof error.status === 'number' ? error.status : 0;
        state.addError = status === 404 ? error.message : SAVE_FAILED;
        render();
      }
    );
  }

  // The Remove button: the inline confirm opens, no request yet.
  function openRemoveConfirm(key) {
    if (!state.isAdmin || state.saving) {
      return;
    }
    if (!rowByKey(key)) {
      return; // a row that no longer exists cannot be removed
    }
    state.confirmRemove = { key: key };
    state.removeError = null;
    render();
  }

  function cancelRemoveConfirm() {
    if (state.saving) {
      return;
    }
    state.confirmRemove = null;
    state.removeError = null;
    render();
  }

  // The confirm: one DELETE with the pair as query parameters.
  function doRemove(key) {
    if (!state.isAdmin || state.saving) {
      return;
    }
    var row = rowByKey(key);
    if (!row) {
      state.confirmRemove = null;
      state.removeError = null;
      render();
      return;
    }
    state.saving = true;
    state.removeError = null;
    render();
    var myGen = saveGen;
    var path =
      BUFFERS_PATH +
      '?item_code=' +
      encodeURIComponent(String(row.item_code)) +
      '&site_code=' +
      encodeURIComponent(String(row.site_code));
    global.Api.request(path, { method: 'DELETE' }).then(
      function () {
        if (myGen !== saveGen) {
          return; // a reset happened: this answer belongs to a past session
        }
        state.saving = false;
        state.confirmRemove = null;
        state.removeError = null;
        render();
        reloadEverything();
      },
      function (error) {
        if (myGen !== saveGen) {
          return;
        }
        state.saving = false;
        var status = error && typeof error.status === 'number' ? error.status : 0;
        // The confirm stays open with the failure: nothing was
        // removed, so the pair may be retried.
        state.removeError = status === 404 ? error.message : SAVE_FAILED;
        render();
      }
    );
  }

  // The candidates' Designate button: one PUT with the pair, then a
  // full refetch — the designation just left the candidate pool.
  function doDesignate(itemCode, siteCode) {
    if (!state.isAdmin || state.saving) {
      return;
    }
    state.saving = true;
    state.designateError = null;
    render();
    var myGen = saveGen;
    global.Api.request(BUFFERS_PATH, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_code: itemCode, site_code: siteCode }),
    }).then(
      function () {
        if (myGen !== saveGen) {
          return; // a reset happened: this answer belongs to a past session
        }
        state.saving = false;
        render();
        reloadEverything();
      },
      function (error) {
        if (myGen !== saveGen) {
          return;
        }
        state.saving = false;
        var status = error && typeof error.status === 'number' ? error.status : 0;
        state.designateError = status === 404 ? error.message : SAVE_FAILED;
        render();
      }
    );
  }

  // The "Planned adjustments" add form: one POST with the typed
  // window, factor and note; item and site are optional, both or
  // neither (a lone code is the server's 422 to show). A 409, 422 or
  // 404 shows the server's detail inline and keeps the six-field
  // draft; any other failure shows the generic message.
  function doAddAdjustment(draft) {
    if (!state.isAdmin || state.adjustSaving) {
      return;
    }
    // The factor, as typed: a blank (or all-whitespace) field is an
    // invalid draft, never a zero — a bare Number('') would coerce
    // the empty field to 0 and send that to the server.
    var factorRaw = typeof draft.factor === 'string' ? draft.factor.trim() : '';
    var factor = factorRaw === '' ? NaN : Number(factorRaw);
    if (draft.start_on === '' || draft.end_on === '' || !isFiniteNumber(factor)) {
      state.adjustError = SAVE_FAILED;
      render();
      return;
    }
    state.adjustSaving = true;
    state.adjustError = null;
    render();
    var myGen = saveGen;
    global.Api.request(ADJUSTMENTS_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        item_code: draft.item_code === '' ? null : draft.item_code,
        site_code: draft.site_code === '' ? null : draft.site_code,
        start_on: draft.start_on,
        end_on: draft.end_on,
        factor: factor,
        note: draft.note === '' ? null : draft.note,
      }),
    }).then(
      function () {
        if (myGen !== saveGen) {
          return;
        }
        state.adjustSaving = false;
        state.adjustDraft = freshAdjustDraft();
        state.adjustError = null;
        render();
        reloadEverything();
      },
      function (error) {
        if (myGen !== saveGen) {
          return;
        }
        state.adjustSaving = false;
        var status = error && typeof error.status === 'number' ? error.status : 0;
        // The draft stays in state: the re-render below re-types the
        // six fields into the form, so a conflict never wipes them.
        state.adjustError =
          status === 409 || status === 422 || status === 404 ? error.message : SAVE_FAILED;
        render();
      }
    );
  }

  // The adjustments' Delete: the inline confirm opens, no request yet.
  function openAdjustDeleteConfirm(id) {
    if (!state.isAdmin || state.adjustSaving) {
      return;
    }
    if (!adjustmentById(id)) {
      return; // a line that no longer exists cannot be deleted
    }
    state.confirmAdjustDelete = { id: String(id) };
    state.adjustDeleteError = null;
    render();
  }

  function cancelAdjustDeleteConfirm() {
    if (state.adjustSaving) {
      return;
    }
    state.confirmAdjustDelete = null;
    state.adjustDeleteError = null;
    render();
  }

  // The confirm: one DELETE with the adjustment's id.
  function doDeleteAdjustment(id) {
    if (!state.isAdmin || state.adjustSaving) {
      return;
    }
    if (!adjustmentById(id)) {
      state.confirmAdjustDelete = null;
      state.adjustDeleteError = null;
      render();
      return;
    }
    state.adjustSaving = true;
    state.adjustDeleteError = null;
    render();
    var myGen = saveGen;
    global.Api.request(ADJUSTMENTS_PATH + '/' + encodeURIComponent(String(id)), {
      method: 'DELETE',
    }).then(
      function () {
        if (myGen !== saveGen) {
          return;
        }
        state.adjustSaving = false;
        state.confirmAdjustDelete = null;
        state.adjustDeleteError = null;
        render();
        reloadEverything();
      },
      function (error) {
        if (myGen !== saveGen) {
          return;
        }
        state.adjustSaving = false;
        var status = error && typeof error.status === 'number' ? error.status : 0;
        // The confirm stays open with the failure: nothing was
        // removed, so the line may be retried.
        state.adjustDeleteError =
          status === 409 || status === 422 || status === 404 ? error.message : SAVE_FAILED;
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
    if (target.closest('[data-testid="buffers-refresh"]')) {
      loadAll();
      return;
    }
    if (target.closest('[data-testid="buffers-retry"]')) {
      loadPlanAndZones();
      return;
    }
    var chip = target.closest('[data-filter]');
    if (chip && chip.closest('[data-testid="buffers-filter"]')) {
      state.filter = chip.getAttribute('data-filter') || 'all';
      render();
      return;
    }
    if (target.closest('[data-testid="buffers-remove"]')) {
      openRemoveConfirm(target.closest('[data-testid="buffers-remove"]').getAttribute('data-key'));
      return;
    }
    if (target.closest('[data-testid="buffers-remove-confirm"]')) {
      doRemove(
        target.closest('[data-testid="buffers-remove-confirm"]').getAttribute('data-key')
      );
      return;
    }
    if (target.closest('[data-testid="buffers-remove-cancel"]')) {
      cancelRemoveConfirm();
    }
    var designate = target.closest('[data-testid="buffers-designate"]');
    if (designate) {
      doDesignate(designate.getAttribute('data-item'), designate.getAttribute('data-site'));
      return;
    }
    var adjustDelete = target.closest('[data-testid="buffers-adjust-delete"]');
    if (adjustDelete) {
      openAdjustDeleteConfirm(adjustDelete.getAttribute('data-id'));
      return;
    }
    if (target.closest('[data-testid="buffers-adjust-delete-confirm"]')) {
      doDeleteAdjustment(
        target.closest('[data-testid="buffers-adjust-delete-confirm"]').getAttribute('data-id')
      );
      return;
    }
    if (target.closest('[data-testid="buffers-adjust-delete-cancel"]')) {
      cancelAdjustDeleteConfirm();
    }
    var useProposal = target.closest('[data-testid="buffers-adjust-use"]');
    if (useProposal) {
      useProposalAdjustment(useProposal.getAttribute('data-index'));
      return;
    }
  }

  function onRootSubmit(event) {
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }
    if (target.closest('[data-testid="buffers-adjust-add"]')) {
      event.preventDefault();
      readAdjustDraft(target);
      doAddAdjustment(state.adjustDraft);
      return;
    }
    if (!target.closest('[data-testid="buffers-add"]')) {
      return;
    }
    event.preventDefault();
    var itemInput = target.querySelector('input[name="item_code"]');
    var siteInput = target.querySelector('input[name="site_code"]');
    if (!itemInput || !siteInput) {
      return;
    }
    doAdd(itemInput.value, siteInput.value);
  }

  // The adjust form's six fields, read as plain strings: the draft
  // lives in state from here on, so a re-render (a failed save above
  // all) re-types the form exactly as the user left it.
  function readAdjustDraft(form) {
    var fields = ['item_code', 'site_code', 'start_on', 'end_on', 'factor', 'note'];
    var draft = {};
    for (var i = 0; i < fields.length; i++) {
      var input = form.querySelector('input[name="' + fields[i] + '"]');
      draft[fields[i]] = input && typeof input.value === 'string' ? input.value : '';
    }
    state.adjustDraft = draft;
  }

  // A proposals line's Use button: it types the proposal into the
  // add form's draft — item and site, the window, the factor as
  // String(factor) and the note with the line's one-decimal figures
  // — and clears a previous add error. It renders and sends
  // nothing; the admin saves with "Add adjustment" as before.
  function useProposalAdjustment(index) {
    if (!state.isAdmin || !state.proposals) {
      return;
    }
    var proposal = state.proposals[Number(index)];
    if (!isUsableProposal(proposal)) {
      return; // a line that no longer exists cannot be used
    }
    state.adjustDraft = {
      item_code: proposal.item_code,
      site_code: proposal.site_code,
      start_on: proposal.start_on,
      end_on: proposal.end_on,
      factor: String(proposal.factor),
      note:
        PROPOSALS_LEAD +
        ': ' +
        proposal.forecast_daily.toFixed(1) +
        '/day against ' +
        proposal.adu_base.toFixed(1) +
        '/day',
    };
    state.adjustError = null;
    render();
  }

  /* ------------------------------ rendering ---------------------------- */

  function render() {
    if (!rootEl) {
      return;
    }
    rootEl.innerHTML = shellHtml();
  }

  function shellHtml() {
    return (
      '<div class="bf">' +
      barHtml() +
      (hasQualifiedNote() ? noteHtml() : '') +
      '<div class="bf-scroll">' +
      tableRegionHtml() +
      unsizedHtml() +
      (state.isAdmin ? addFormHtml() : '') +
      candidatesSectionHtml() +
      adjustmentsSectionHtml() +
      performanceSectionHtml() +
      '</div>' +
      '</div>'
    );
  }

  function barHtml() {
    var counts = summaryCounts();
    return (
      '<div class="bf-toolbar">' +
      '<div class="bf-chips" data-testid="buffers-filter" role="group" ' +
      'aria-label="Filter buffers">' +
      chipHtml('all', 'All') +
      chipHtml('signals', 'Order signals ' + counts.signals) +
      chipHtml('alerts', 'On-hand alerts ' + counts.alerts) +
      '</div>' +
      '<button type="button" class="btn btn-secondary bf-refresh" ' +
      'data-testid="buffers-refresh"' +
      (state.loading ? ' disabled' : '') +
      '>Refresh</button>' +
      '</div>'
    );
  }

  function chipHtml(filter, label) {
    return (
      '<button type="button" class="bf-chip' +
      (state.filter === filter ? ' is-on' : '') +
      '" data-filter="' +
      esc(filter) +
      '" aria-pressed="' +
      (state.filter === filter ? 'true' : 'false') +
      '">' +
      esc(label) +
      '</button>'
    );
  }

  function noteHtml() {
    return '<p class="bf-note" data-testid="buffers-note">' + esc(QUALIFIED_NOTE) + '</p>';
  }

  function skeletonHtml() {
    return (
      '<div class="bf-skel-wrap" aria-hidden="true">' +
      '<div class="skel skel-line"></div>' +
      '<div class="skel skel-line skel-wide"></div>' +
      '<div class="skel bf-skel-block"></div>' +
      '</div>'
    );
  }

  function errorHtml(testid, retryTestid, message) {
    return (
      '<div class="bf-error" role="alert" data-testid="' + testid + '">' +
      '<p class="bf-error-msg">' + esc(message) + '</p>' +
      '<button type="button" class="btn btn-secondary" data-testid="' +
      retryTestid +
      '">Retry</button>' +
      '</div>'
    );
  }

  function tableRegionHtml() {
    var inner;
    if (state.loading) {
      inner = skeletonHtml();
    } else if (state.error) {
      inner = errorHtml('buffers-error', 'buffers-retry', state.error.message);
    } else if (!state.plan || !state.zones) {
      // Never loaded yet (pre-sign-in activation, or right after a
      // reset): the first-load state is the skeleton.
      inner = skeletonHtml();
    } else if (noDesignations()) {
      inner = '<p class="bf-empty" data-testid="buffers-empty">' + esc(EMPTY) + '</p>';
    } else {
      inner = tableHtml(visibleRows());
    }
    return '<div class="bf-table-region">' + inner + '</div>';
  }

  function tableHtml(rows) {
    var body = [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!row || typeof row !== 'object') {
        continue; // a malformed row never breaks the table
      }
      body.push(planRowHtml(row));
    }
    return (
      '<table class="bf-table" data-testid="buffers-table">' +
      '<thead><tr>' +
      '<th scope="col">Item and Site</th>' +
      '<th scope="col">Zones</th>' +
      '<th scope="col">Net flow</th>' +
      '<th scope="col">Zone</th>' +
      '<th scope="col">Order</th>' +
      '<th scope="col">On hand</th>' +
      '<th scope="col">Priority</th>' +
      '</tr></thead>' +
      '<tbody>' +
      body.join('') +
      '</tbody>' +
      '</table>'
    );
  }

  function zoneBarHtml(row) {
    var red = zoneValue(row.red);
    var yellow = zoneValue(row.yellow);
    var green = zoneValue(row.green);
    var topOfGreen = zoneValue(row.top_of_green);
    var netFlow = zoneValue(row.net_flow);
    var markerStyle;
    if (topOfGreen > 0) {
      var pct = (Math.min(netFlow, topOfGreen) / topOfGreen) * 100;
      if (pct >= 100) {
        // Capped at the bar's end, still visible.
        markerStyle = 'left: calc(100% - 2px);';
      } else {
        markerStyle = 'left: ' + pct.toFixed(2) + '%;';
      }
    } else {
      markerStyle = 'left: 0;';
    }
    return (
      '<div class="bf-barwrap">' +
      '<div class="bf-bar" data-testid="buffers-bar">' +
      '<div class="bf-bar-seg bf-bar-seg--red" style="flex-grow: ' +
      red +
      ';"></div>' +
      '<div class="bf-bar-seg bf-bar-seg--yellow" style="flex-grow: ' +
      yellow +
      ';"></div>' +
      '<div class="bf-bar-seg bf-bar-seg--green" style="flex-grow: ' +
      green +
      ';"></div>' +
      '<span class="bf-bar-marker" style="' + markerStyle + '"></span>' +
      '</div>' +
      '<span class="bf-tops">' +
      esc(
        'TOR ' +
          wholeNumber(row.top_of_red) +
          ' ' +
          MIDDLE_DOT +
          ' TOY ' +
          wholeNumber(row.top_of_yellow) +
          ' ' +
          MIDDLE_DOT +
          ' TOG ' +
          wholeNumber(row.top_of_green)
      ) +
      '</span>' +
      '</div>'
    );
  }

  function removeControlsHtml(row, key) {
    if (!state.isAdmin) {
      return '';
    }
    var pair = pairText(row.item_code, row.site_code);
    if (state.confirmRemove && state.confirmRemove.key === key) {
      return (
        '<span class="bf-confirm">' +
        '<span class="bf-confirm-msg">' +
        esc('Stop buffering ' + pair + '?') +
        '</span>' +
        '<span class="bf-confirm-actions">' +
        '<button type="button" class="btn btn-primary" data-testid="buffers-remove-confirm" ' +
        'data-key="' +
        esc(key) +
        '"' +
        (state.saving ? ' disabled' : '') +
        '>Confirm</button>' +
        '<button type="button" class="btn btn-secondary" data-testid="buffers-remove-cancel" ' +
        'data-key="' +
        esc(key) +
        '"' +
        (state.saving ? ' disabled' : '') +
        '>Cancel</button>' +
        '</span>' +
        (state.removeError
          ? '<span class="bf-remove-error" role="alert" data-testid="buffers-remove-error">' +
            esc(state.removeError) +
            '</span>'
          : '') +
        '</span>'
      );
    }
    return (
      '<button type="button" class="bf-remove" data-testid="buffers-remove" ' +
      'data-key="' +
      esc(key) +
      '"' +
      (state.saving ? ' disabled' : '') +
      '>Remove</button>'
    );
  }

  function planRowHtml(row) {
    var key = pairKey(row.item_code, row.site_code);
    var order =
      row.order_signal === true
        ? 'Order ' + wholeNumber(row.suggested_order_units)
        : EM_DASH;
    return (
      '<tr class="bf-row">' +
      '<td class="bf-cell-item">' +
      '<span class="bf-pair">' +
      esc(pairText(row.item_code, row.site_code)) +
      '</span>' +
      removeControlsHtml(row, key) +
      '</td>' +
      '<td class="bf-cell-zones">' +
      zoneBarHtml(row) +
      adjustmentBadgeHtml(row) +
      '</td>' +
      '<td class="bf-cell-num">' +
      esc(wholeNumber(row.net_flow)) +
      '</td>' +
      '<td>' +
      '<span class="bf-badge bf-badge--zone-' +
      esc(zoneClass(row.planning_zone)) +
      '">' +
      esc(zoneLabel(row.planning_zone)) +
      '</span>' +
      '</td>' +
      '<td class="bf-cell-num">' +
      esc(order) +
      '</td>' +
      '<td class="bf-cell-num">' +
      esc(wholeNumber(row.on_hand)) +
      ' ' +
      '<span class="bf-badge bf-badge--oh-' +
      esc(onHandClass(row.on_hand_status)) +
      '">' +
      esc(onHandLabel(row.on_hand_status)) +
      '</span>' +
      '</td>' +
      '<td class="bf-cell-num">' +
      esc(priorityText(row.planning_priority_pct)) +
      '</td>' +
      '</tr>'
    );
  }

  function unsizedReason(status) {
    if (status === 'no_demand') {
      return 'no demand';
    }
    if (status === 'no_lead_time') {
      return 'no lead time';
    }
    return nonEmptyString(status) || EM_DASH;
  }

  function unsizedHtml() {
    var rows = unsizedRows();
    if (rows.length === 0) {
      return '';
    }
    var lines = [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var key = pairKey(row.item_code, row.site_code);
      lines.push(
        '<li class="bf-unsized-line">' +
        '<span class="bf-pair">' +
        esc(pairText(row.item_code, row.site_code)) +
        '</span>' +
        '<span class="bf-unsized-reason">: ' +
        esc(unsizedReason(row.status)) +
        '</span>' +
        removeControlsHtml(row, key) +
        '</li>'
      );
    }
    return (
      '<div class="bf-unsized" data-testid="buffers-unsized">' +
      '<h2 class="bf-unsized-title">' +
      esc(UNSIZED_TITLE) +
      '</h2>' +
      '<ul class="bf-unsized-list">' +
      lines.join('') +
      '</ul>' +
      '</div>'
    );
  }

  function addFormHtml() {
    return (
      '<form class="bf-add" data-testid="buffers-add">' +
      '<span class="bf-add-label">Add buffer</span>' +
      '<label class="bf-add-field">Item code' +
      '<input type="text" name="item_code" autocomplete="off" spellcheck="false" ' +
      'required maxlength="64"></label>' +
      '<label class="bf-add-field">Site code' +
      '<input type="text" name="site_code" autocomplete="off" spellcheck="false" ' +
      'required maxlength="64"></label>' +
      '<button type="submit" class="btn btn-primary bf-add-submit" ' +
      'data-testid="buffers-add-submit"' +
      (state.saving ? ' disabled' : '') +
      '>Add buffer</button>' +
      (state.addError
        ? '<p class="bf-add-error" role="alert" data-testid="buffers-add-error">' +
          esc(state.addError) +
          '</p>'
        : '') +
      '</form>'
    );
  }

  // The adjustment factor badge next to a plan row's zone bar: shown
  // whenever the row's zone carries a factor other than 1.
  function adjustmentBadgeHtml(row) {
    var factor = row.adjustment_factor;
    if (typeof factor === 'string') {
      factor = factor === '' ? NaN : Number(factor);
    }
    if (!isFiniteNumber(factor) || factor === 1) {
      return '';
    }
    return (
      '<span class="bf-badge bf-badge--adjust" data-testid="buffers-adjust-badge">' +
      esc(TIMES + factorText(factor)) +
      '</span>'
    );
  }

  // --- the three independent sections ----------------------------------

  // Each section renders its own terminal state: the data (a table or
  // the empty note) once a successful answer has landed, its own
  // one-line note when the fetch failed, and nothing while the first
  // fetch is out — never a loading placeholder.
  function sectionShellHtml(testid, title, inner) {
    return (
      '<section class="bf-section" data-testid="' +
      testid +
      '">' +
      '<h2 class="bf-section-title">' +
      esc(title) +
      '</h2>' +
      inner +
      '</section>'
    );
  }

  function candidatesSectionHtml() {
    var inner;
    if (state.candidatesError) {
      inner =
        '<p class="bf-section-error" role="alert" data-testid="buffers-candidates-error">' +
        esc(CANDIDATES_LOAD_ERROR) +
        '</p>';
    } else if (state.candidates) {
      inner =
        candidateRows().length === 0
          ? '<p class="bf-section-empty" data-testid="buffers-candidates-empty">' +
            esc(CANDIDATES_EMPTY) +
            '</p>'
          : candidatesTableHtml();
    } else {
      inner = '';
    }
    return (
      sectionShellHtml('buffers-candidates', CANDIDATES_TITLE, inner) +
      (state.designateError
        ? '<p class="bf-section-error" role="alert" data-testid="buffers-designate-error">' +
          esc(state.designateError) +
          '</p>'
        : '')
    );
  }

  function candidatesTableHtml() {
    var rows = candidateRows();
    var body = [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!row || typeof row !== 'object') {
        continue; // a malformed row never breaks the table
      }
      body.push(candidateRowHtml(row));
    }
    return (
      '<table class="bf-cand-table">' +
      '<thead><tr>' +
      '<th scope="col">Item and Site</th>' +
      '<th scope="col">Class</th>' +
      '<th scope="col">Annual value</th>' +
      '<th scope="col">Lead time</th>' +
      '<th scope="col">Reason</th>' +
      (state.isAdmin ? '<th scope="col"></th>' : '') +
      '</tr></thead>' +
      '<tbody>' +
      body.join('') +
      '</tbody>' +
      '</table>'
    );
  }

  function candidateRowHtml(row) {
    var dlt = row.dlt_days;
    if (typeof dlt === 'string') {
      dlt = dlt === '' ? NaN : Number(dlt);
    }
    var leadTime = isFiniteNumber(dlt) ? String(Math.round(dlt)) + ' d' : EM_DASH;
    return (
      '<tr class="bf-row">' +
      '<td class="bf-cell-item">' +
      '<span class="bf-pair">' +
      esc(pairText(row.item_code, row.site_code)) +
      '</span>' +
      '</td>' +
      '<td class="bf-cell-num">' +
      esc(nonEmptyString(row.abc_class) || EM_DASH) +
      '</td>' +
      '<td class="bf-cell-num">' +
      esc('$' + wholeNumber(row.annual_value)) +
      '</td>' +
      '<td class="bf-cell-num">' +
      esc(leadTime) +
      '</td>' +
      '<td class="bf-cell-reason">' +
      esc(nonEmptyString(row.reason) || EM_DASH) +
      '</td>' +
      (state.isAdmin
        ? '<td>' +
          '<button type="button" class="bf-designate" data-testid="buffers-designate" ' +
          'data-item="' +
          esc(row.item_code) +
          '" data-site="' +
          esc(row.site_code) +
          '"' +
          (state.saving ? ' disabled' : '') +
          '>Designate</button>' +
          '</td>'
        : '') +
      '</tr>'
    );
  }

  // A well-formed proposal line: an object with string item_code,
  // site_code, start_on and end_on and finite factor, forecast_daily
  // and adu_base (existing_factor is free to be null or a figure).
  function isUsableProposal(entry) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
      return false;
    }
    if (
      typeof entry.item_code !== 'string' ||
      entry.item_code === '' ||
      typeof entry.site_code !== 'string' ||
      entry.site_code === '' ||
      typeof entry.start_on !== 'string' ||
      entry.start_on === '' ||
      typeof entry.end_on !== 'string' ||
      entry.end_on === ''
    ) {
      return false;
    }
    return (
      isFiniteNumber(entry.factor) &&
      isFiniteNumber(entry.forecast_daily) &&
      isFiniteNumber(entry.adu_base)
    );
  }

  // A usable proposals answer: an object whose "proposals" is an
  // array with at least one well-formed line; anything less — a
  // bare list, an object without the array, or an array with no
  // usable line — answers null and the block stays out.
  function usableProposals(body) {
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      return null;
    }
    if (!Array.isArray(body.proposals)) {
      return null;
    }
    var lines = [];
    for (var i = 0; i < body.proposals.length; i++) {
      if (isUsableProposal(body.proposals[i])) {
        lines.push(body.proposals[i]);
      }
    }
    return lines.length === 0 ? null : lines;
  }

  // The proposals block, inside the "Planned adjustments" section,
  // directly after the stored lines (or their empty note) and before
  // the add form: the lead line, then one line per well-formed
  // proposal in the answer's order, reusing the adjustments' list
  // classes — the pair, the window, "×<factor>", "forecast
  // <f>/day against <a>/day" (one decimal each) and, when
  // existing_factor is not null, "now ×<existing_factor>". Every
  // value is inserted as text, never as markup.
  function proposalsBlockHtml() {
    var lines = [];
    for (var i = 0; i < state.proposals.length; i++) {
      lines.push(proposalLineHtml(state.proposals[i], i));
    }
    return (
      '<div class="bf-adjust-proposals" data-testid="buffers-adjust-proposals">' +
      '<p class="bf-note" data-testid="buffers-adjust-proposals-lead">' +
      esc(PROPOSALS_LEAD) +
      '</p>' +
      '<ul class="bf-adjust-list">' +
      lines.join('') +
      '</ul>' +
      '</div>'
    );
  }

  function proposalLineHtml(proposal, index) {
    return (
      '<li class="bf-adjust-line" data-testid="buffers-adjust-proposal" data-index="' +
      String(index) +
      '">' +
      '<span class="bf-pair">' +
      esc(pairText(proposal.item_code, proposal.site_code)) +
      '</span>' +
      '<span class="bf-adjust-window">' +
      esc(proposal.start_on + ' ' + EN_DASH + ' ' + proposal.end_on) +
      '</span>' +
      '<span class="bf-adjust-factor">' +
      esc(TIMES + factorText(proposal.factor)) +
      '</span>' +
      '<span class="bf-adjust-note">' +
      esc(
        'forecast ' +
          proposal.forecast_daily.toFixed(1) +
          '/day against ' +
          proposal.adu_base.toFixed(1) +
          '/day'
      ) +
      '</span>' +
      existingFactorSpanHtml(proposal.existing_factor) +
      (state.isAdmin ? proposalUseButtonHtml(index) : '') +
      '</li>'
    );
  }

  // The "now ×<existing_factor>" span: only when existing_factor
  // is not null and carries a usable figure.
  function existingFactorSpanHtml(existing) {
    if (existing === null || existing === undefined) {
      return '';
    }
    if (typeof existing === 'string') {
      existing = existing === '' ? NaN : Number(existing);
    }
    if (!isFiniteNumber(existing)) {
      return '';
    }
    return (
      '<span class="bf-adjust-factor">' +
      esc('now ' + TIMES + factorText(existing)) +
      '</span>'
    );
  }

  function proposalUseButtonHtml(index) {
    return (
      '<button type="button" class="btn btn-secondary bf-adjust-use" ' +
      'data-testid="buffers-adjust-use" data-index="' +
      String(index) +
      '">Use</button>'
    );
  }

  // The record line, inside the "Planned adjustments" section,
  // directly after the proposals block (or where the block would
  // sit without it) and before the add form: shown whenever the
  // section has its adjustments answer and the record answer is
  // usable. It reuses the note class, sets the sentence as text,
  // never as markup, and carries no colour outside tokens.css.
  function recordLineHtml() {
    var record = state.record;
    if (!record) {
      return '';
    }
    var text;
    if (record.with_proposal > 0) {
      text =
        'Saved at the proposal: ' +
        wholeNumber(record.taken) +
        ' of ' +
        wholeNumber(record.with_proposal) +
        ' adjustments saved where a factor was proposed (' +
        wholeNumber(record.below) +
        ' below it, ' +
        wholeNumber(record.above) +
        ' above it)';
    } else {
      text = 'No adjustment has been saved where a factor was proposed yet.';
    }
    return (
      '<p class="bf-note" data-testid="buffers-adjust-record">' +
      esc(text) +
      '</p>'
    );
  }

  function adjustmentsSectionHtml() {
    var inner;
    if (state.adjustmentsError) {
      inner =
        '<p class="bf-section-error" role="alert" data-testid="buffers-adjustments-error">' +
        esc(ADJUSTMENTS_LOAD_ERROR) +
        '</p>';
    } else if (state.adjustments) {
      var rows = adjustmentRows();
      var proposals =
        state.proposals && state.proposals.length > 0 ? proposalsBlockHtml() : '';
      var record = recordLineHtml();
      if (rows.length === 0) {
        inner =
          '<p class="bf-section-empty" data-testid="buffers-adjustments-empty">' +
          esc(ADJUSTMENTS_EMPTY) +
          '</p>' +
          proposals +
          record +
          (state.isAdmin ? adjustFormHtml() : '');
      } else {
        var lines = [];
        for (var i = 0; i < rows.length; i++) {
          var row = rows[i];
          if (!row || typeof row !== 'object') {
            continue; // a malformed line never breaks the list
          }
          lines.push(adjustmentLineHtml(row));
        }
        inner =
          '<ul class="bf-adjust-list">' +
          lines.join('') +
          '</ul>' +
          proposals +
          record +
          (state.isAdmin ? adjustFormHtml() : '');
      }
    } else {
      inner = '';
    }
    return sectionShellHtml('buffers-adjustments', ADJUSTMENTS_TITLE, inner);
  }

  function adjustmentLineHtml(row) {
    var scope =
      row.item_code === null && row.site_code === null
        ? ALL_BUFFERS_SCOPE
        : pairText(row.item_code, row.site_code);
    var windowText = windowDate(row.start_on) + ' ' + EN_DASH + ' ' + windowDate(row.end_on);
    var note = nonEmptyString(row.note);
    return (
      '<li class="bf-adjust-line">' +
      '<span class="bf-pair">' +
      esc(scope) +
      '</span>' +
      '<span class="bf-adjust-window">' +
      esc(windowText) +
      '</span>' +
      '<span class="bf-adjust-factor">' +
      esc(TIMES + factorText(row.factor)) +
      '</span>' +
      (note ? '<span class="bf-adjust-note">' + esc(note) + '</span>' : '') +
      adjustDeleteControlsHtml(row) +
      '</li>'
    );
  }

  // A window bound as the server wrote it, an em dash without.
  function windowDate(value) {
    return nonEmptyString(value) || EM_DASH;
  }

  function adjustDeleteControlsHtml(row) {
    if (!state.isAdmin) {
      return '';
    }
    var id = String(row.id);
    if (state.confirmAdjustDelete && state.confirmAdjustDelete.id === id) {
      return (
        '<span class="bf-confirm">' +
        '<span class="bf-confirm-msg">' +
        esc(DELETE_ADJUSTMENT_QUESTION) +
        '</span>' +
        '<span class="bf-confirm-actions">' +
        '<button type="button" class="btn btn-primary" data-testid="buffers-adjust-delete-confirm" ' +
        'data-id="' +
        esc(id) +
        '"' +
        (state.adjustSaving ? ' disabled' : '') +
        '>Confirm</button>' +
        '<button type="button" class="btn btn-secondary" data-testid="buffers-adjust-delete-cancel" ' +
        'data-id="' +
        esc(id) +
        '"' +
        (state.adjustSaving ? ' disabled' : '') +
        '>Cancel</button>' +
        '</span>' +
        (state.adjustDeleteError
          ? '<span class="bf-remove-error" role="alert" data-testid="buffers-adjust-delete-error">' +
            esc(state.adjustDeleteError) +
            '</span>'
          : '') +
        '</span>'
      );
    }
    return (
      '<button type="button" class="bf-remove" data-testid="buffers-adjust-delete" ' +
      'data-id="' +
      esc(id) +
      '"' +
      (state.adjustSaving ? ' disabled' : '') +
      '>Delete</button>'
    );
  }

  function adjustFormHtml() {
    var draft = state.adjustDraft || freshAdjustDraft();
    return (
      '<form class="bf-add bf-adjust-add" data-testid="buffers-adjust-add">' +
      '<span class="bf-add-label">Plan adjustment</span>' +
      '<label class="bf-add-field">Item code (optional)' +
      '<input type="text" name="item_code" autocomplete="off" spellcheck="false" ' +
      'value="' +
      esc(draft.item_code) +
      '" maxlength="64"></label>' +
      '<label class="bf-add-field">Site code (optional)' +
      '<input type="text" name="site_code" autocomplete="off" spellcheck="false" ' +
      'value="' +
      esc(draft.site_code) +
      '" maxlength="64"></label>' +
      '<label class="bf-add-field">Start date' +
      '<input type="date" name="start_on" required ' +
      'value="' +
      esc(draft.start_on) +
      '"></label>' +
      '<label class="bf-add-field">End date' +
      '<input type="date" name="end_on" required ' +
      'value="' +
      esc(draft.end_on) +
      '"></label>' +
      '<label class="bf-add-field">Factor' +
      '<input type="number" name="factor" step="any" min="0" max="10" required ' +
      'value="' +
      esc(draft.factor) +
      '"></label>' +
      '<label class="bf-add-field">Note (optional)' +
      '<input type="text" name="note" autocomplete="off" ' +
      'value="' +
      esc(draft.note) +
      '" maxlength="200"></label>' +
      '<button type="submit" class="btn btn-primary bf-add-submit" ' +
      'data-testid="buffers-adjust-submit"' +
      (state.adjustSaving ? ' disabled' : '') +
      '>Add adjustment</button>' +
      (state.adjustError
        ? '<p class="bf-add-error" role="alert" data-testid="buffers-adjust-error">' +
          esc(state.adjustError) +
          '</p>'
        : '') +
      '</form>'
    );
  }

  function performanceSectionHtml() {
    var inner;
    if (state.performanceError) {
      inner =
        '<p class="bf-section-error" role="alert" data-testid="buffers-performance-error">' +
        esc(PERFORMANCE_LOAD_ERROR) +
        '</p>';
    } else if (state.performance) {
      inner =
        performanceRows().length === 0
          ? '<p class="bf-section-empty" data-testid="buffers-performance-empty">' +
            esc(PERFORMANCE_EMPTY) +
            '</p>'
          : performanceTableHtml();
    } else {
      inner = '';
    }
    return sectionShellHtml('buffers-performance', PERFORMANCE_TITLE, inner);
  }

  function performanceTableHtml() {
    var rows = performanceRows();
    var body = [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!row || typeof row !== 'object') {
        continue; // a malformed row never breaks the table
      }
      body.push(performanceRowHtml(row));
    }
    return (
      '<table class="bf-perf-table">' +
      '<thead><tr>' +
      '<th scope="col">Item and Site</th>' +
      '<th scope="col">Days observed</th>' +
      '<th scope="col">Time in zone</th>' +
      '<th scope="col">Stocked out</th>' +
      '</tr></thead>' +
      '<tbody>' +
      body.join('') +
      '</tbody>' +
      '</table>'
    );
  }

  function performanceRowHtml(row) {
    return (
      '<tr class="bf-row">' +
      '<td class="bf-cell-item">' +
      '<span class="bf-pair">' +
      esc(pairText(row.item_code, row.site_code)) +
      '</span>' +
      '</td>' +
      '<td class="bf-cell-num">' +
      esc(wholeNumber(row.days_observed)) +
      '</td>' +
      '<td class="bf-cell-zones">' +
      perfBarHtml(row) +
      '</td>' +
      '<td class="bf-cell-num">' +
      esc(stockedOutText(row.stocked_out_pct)) +
      '</td>' +
      '</tr>'
    );
  }

  // The stacked bar: red, yellow, green and over segments whose
  // flex-grow carries the four percentages, so the segment widths
  // keep their ratio; the "over" fill is the neutral token (excess
  // stock, not a shortage risk).
  function perfBarHtml(row) {
    return (
      '<div class="bf-perf-barwrap">' +
      '<div class="bf-perf-bar" data-testid="buffers-perf-bar">' +
      perfSegHtml('red', row.red_pct) +
      perfSegHtml('yellow', row.yellow_pct) +
      perfSegHtml('green', row.green_pct) +
      perfSegHtml('over', row.over_pct) +
      '</div>' +
      '</div>'
    );
  }

  function perfSegHtml(zone, pct) {
    return (
      '<div class="bf-perf-seg bf-perf-seg--' +
      zone +
      '" style="flex-grow: ' +
      zoneValue(pct) +
      ';"></div>'
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
    if (state.saving) {
      // A PUT/DELETE is out the door: activating the view again must
      // not start a second change.
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
    loadSeq += 1; // any answer still out the door belongs to a past session
    meSeq += 1;
    saveGen += 1;
    candidatesSeq += 1;
    adjustmentsSeq += 1;
    performanceSeq += 1;
    proposalsSeq += 1;
    recordSeq += 1;
    state = freshState();
    render();
  }

  (function boot() {
    rootEl = document.getElementById(ROOT_ID);
    if (!rootEl) {
      return;
    }
    rootEl.addEventListener('click', onRootClick);
    rootEl.addEventListener('submit', onRootSubmit);
    render();

    var appEl = document.getElementById(APP_ID);
    if (!appEl) {
      return;
    }
    // Sign-in shows #app: when it does and the buffers view is the
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

  global.Buffers = {
    activate: activate,
    reset: reset,
  };
})(window);
