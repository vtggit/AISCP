// AIERP planner screen — the S&OP review view (#/sop).
//
// #/sop puts one month's demand, supply and money on one page, and who
// agreed it: GET /api/sop/review?months=<n> through app/js/api.js,
// fetched only while the view is active — on view activation, on the
// Refresh button ([data-testid="sop-refresh"]) and when the horizon
// select ([data-testid="sop-months"], "3 months" (the default),
// "6 months" or "12 months") changes. A request-sequence guard
// renders only the newest answer, and a skeleton shows while a fetch
// is out.
//
// The status line ([data-testid="sop-status"]) reads "Review month
// <review_month> · as of <as_of>", followed by the sign-off
// ([data-testid="sop-signoff"]): "Not signed off yet" when
// latest_signoff is null, else "Signed off by <actor_name, else
// actor> on <the date of created_at>", with the note — when there is
// one — in [data-testid="sop-signoff-note"].
//
// The sign-off control under the status line: the view sends
// GET /api/auth/me once per activation, and the viewer is an admin
// when user.roles includes "admin", the way the Aggregate view reads
// it — a non-admin, a failed role read, or a role still out sees no
// control at all. Once a review answer is loaded, an admin sees
// [data-testid="sop-signoff-open"] reading "Sign off <review_month>"
// (the month of the loaded answer) or "Sign off <review_month>
// again" when latest_signoff is not null; the button is not shown
// while the review failed to load, and it is hidden while the panel
// is open — it returns when the panel closes, by Cancel or after a
// sign-off, and the panel's own behaviour is unchanged.
//
// Clicking it opens the panel ([data-testid="sop-signoff-panel"])
// with the sentence "This records that <review_month> was reviewed
// and stores today's figures. It changes nothing in the plan or the
// ERP.", the note field ([data-testid="sop-signoff-text"], labelled
// "Note (optional)", limited to 500 characters) and
// [data-testid="sop-signoff-confirm"] / [data-testid="sop-signoff-cancel"].
// Opening the panel sends no request; Cancel closes the panel and
// sends nothing. Confirming sends one POST /api/sop/signoffs through
// app/js/api.js with {"month": <review_month>, "months": <the chosen
// horizon>} and the trimmed note when it is not empty — the month
// and horizon come from what is loaded at that moment — and the
// confirm button is disabled while the request is out, so a double
// click sends one request. On success the panel closes and clears
// and the view fetches the review once more through its usual path,
// so the status line reads the server's answer; on failure the panel
// stays open with the note kept and the message shows in
// [data-testid="sop-signoff-error"] — a 403 reads "Only an admin can
// sign off.", a 422 shows the server's detail (how a month that has
// rolled over is reported), anything else reads "The sign-off could
// not be recorded.". The role, the panel state, the note, the
// pending flag and the error live in module state: a review refresh
// or a horizon change does not close the open panel or lose its note,
// sign-out clears them, and an answer arriving after sign-out is
// discarded.
//
// Below the links, the Sign-offs section
// ([data-testid="sop-history"]), headed "Sign-offs", is visible to
// every signed-in user, whether or not the review has loaded. Its
// "Show sign-offs" / "Refresh sign-offs" button
// ([data-testid="sop-history-show"]) is the only trigger of
// GET /api/sop/signoffs?limit=50 through app/js/api.js — opening
// the view sends no such request — and after an answer has landed
// the button reads "Refresh sign-offs". A sign-off recorded on the
// screen while a list answer is showing refetches the list once, so
// the new row appears. The list
// ([data-testid="sop-history-table"]) has one row per answer row in
// the answer's order: Month, Signed by (actor_name, else actor),
// Date (the date of created_at), Note (an em dash when null) and a
// "View figures" button ([data-testid="sop-history-view"]); an
// empty answer reads "No sign-offs yet.", and a failed list fetch
// shows "Sign-offs could not be loaded." in
// [data-testid="sop-history-error"] with the review above
// unchanged.
//
// "View figures" sends GET /api/sop/signoffs/{id} once and shows
// the frozen figures ([data-testid="sop-history-figures"]) headed
// "Figures as signed on <the date of created_at> by <the signer>",
// holding the same forecast sentence and the same month table the
// review shows — the row's stored figures fed to the same
// rendering code, so the two cannot drift apart. "Close"
// ([data-testid="sop-history-close"]) hides it; choosing another
// row replaces it, a request-sequence guard renders only the
// newest choice, and a failure shows "These figures could not be
// loaded." inside the block. The block is read-only (no sign-off
// control), never changes the review above, its status line or the
// chosen horizon, and a review refresh never changes it; a stored
// figure missing a field the renderer reads shows an em dash,
// because a stored row is never rewritten when the review's shape
// grows later. The list, its error, the chosen row and its answer
// live in module state and survive a re-render; sign-out clears
// them, and an answer arriving after sign-out is discarded.
//
// Directly above the Scenarios section, the Demand signals
// section ([data-testid="sop-signals"]), headed "Demand signals",
// is visible to every signed-in user, whether or not the review has
// loaded. Its "Show demand signals" / "Refresh demand signals"
// button ([data-testid="sop-signals-show"]) is the only trigger of
// GET /api/demand-signals?limit=200 through app/js/api.js — opening
// the view sends no such request, and once an answer has landed the
// button reads "Refresh demand signals". While a request is out the
// button is disabled, so a double click sends one request, and a
// request-sequence guard renders only the newest answer. The table
// ([data-testid="sop-signals-table"]) has one row per signal,
// sorted by start_date ascending and then by id, with the columns
// "Item" (linking #/demand), "Site", "Kind" ("Promotion", "Deal" or
// "Event"), "From" and "To" (the API's start_date and end_date as
// raw ISO date strings, without locale formatting), "Units" (with
// thousands separators), "Reason" and "Added by". An empty answer
// reads "No demand signals.", and when the answer has 200 rows
// [data-testid="sop-signals-more"] reads "Showing the first 200
// demand signals." A failed fetch shows "Demand signals could not
// be loaded." in [data-testid="sop-signals-error"] and leaves the
// rest of the screen unchanged. The section is read-only: it adds
// and withdraws nothing, those controls stay on the Demand screen.
// The answer, the error and the pending flag live in module state
// and survive a re-render; sign-out clears them, and an answer
// arriving after sign-out is discarded. Every API value is escaped
// or set as text, the colours come only from tokens.css, the table
// scrolls inside its own region, and at 390x844 the page does not
// scroll sideways.
//
// Directly above the Sign-offs section, the Scenarios section
// ([data-testid="sop-scenarios"]), headed "Scenarios", is visible to
// every signed-in user, whether or not the review has loaded. Its
// "Compare saved scenarios" / "Refresh the comparison" button
// ([data-testid="sop-scenarios-show"]) is the only trigger of
// GET /api/sop/scenarios?months=<the chosen horizon> through
// app/js/api.js — opening the view and changing the horizon send no
// such request — and once an answer has landed the button reads
// "Refresh the comparison". While a request is out the button is
// disabled and reads "Comparing…", so a double click sends one
// request, and a request-sequence guard renders only the newest
// answer. After the horizon changes, an answer already showing stays
// and its heading says which horizon it covers:
// [data-testid="sop-scenarios-heading"] reads "Over
// <horizon_months> months, compared with the plan". The table
// ([data-testid="sop-scenarios-table"]) has a first row "Plan" from
// the baseline, then one row per scenario in the answer's order,
// with the columns "Scenario" (the name), "Demand value", "Change",
// "Total spend", "Change", "Months over budget" and "Months short
// with plan" — whole dollars, a change reading with a leading "+"
// or "-" ("No change" when it rounds to zero), the Plan row's change
// cells an em dash. Under each scenario's name,
// [data-testid="sop-scenarios-levers"] lists the levers that are
// set, as "Service level 95.0%", "Demand × 1.50" and "Lead time
// × 1.20"; a scenario with no lever set reads "Same as the plan".
// A scenario whose error is not null shows its name and, across the
// figure cells, reads "Could not be compared: <error>". When
// scenarios_total is above the number of rows,
// [data-testid="sop-scenarios-more"] reads "Showing the newest <n>
// of <scenarios_total> saved scenarios."; with no scenarios,
// [data-testid="sop-scenarios-empty"] reads "No saved scenarios
// yet. Save one on the Scenario screen." (the "Scenario screen"
// linking #/scenario) and the Plan row is not shown. A failed fetch
// shows "The comparison could not be loaded." in
// [data-testid="sop-scenarios-error"] and leaves the review and any
// earlier comparison unchanged. The answer, the error and the
// pending flag live in module state and survive a re-render;
// sign-out clears them, and an answer arriving after sign-out is
// discarded. Every API value is inserted as text, the colours come
// only from tokens.css, the table scrolls inside its own region,
// and at 390x844 the page does not scroll sideways.
//
// The forecast block ([data-testid="sop-forecast"]), headed
// "Forecast accuracy", reads "Error <wape as a percent to one
// decimal> overall · <next_month_wape as a percent to one decimal>
// one month ahead · bias <bias as a signed percent to one decimal>";
// with wape null it reads "No forecast accuracy yet", and with
// next_month_wape null the middle part is left out. The line
// [data-testid="sop-forecast-overrides"] reads "Planner overrides
// changed the error by <value_added in points to one decimal,
// signed>" only when overridden_buckets is above 0 and value_added is
// not null. The line [data-testid="sop-forecast-signals"], directly
// after the overrides line's place, reads "Demand signals changed the
// error by <value_added in points to one decimal, signed> on
// <compared_buckets thousands-separated> periods" only when the
// fetched answer's compared_buckets is above 0 and its value_added is
// a number. It is fetched with the review: the screen sends
// GET /api/forecast/accuracy/signals through app/js/api.js once with
// each load of the review, under its own request-sequence guard, so
// only the newest answer renders; the review request does not change,
// and a sign-out advances the guard and clears the answer. While the
// request is out, with compared_buckets 0, with a null value_added or
// on a failed fetch the line is not in the page, and the rest of the
// review reads as before. It belongs to the live review only: the
// frozen figures of a past sign-off store no such figure and never
// show it. Every API value is set as text, the colours come only from
// tokens.css, and at 390x844 the page does not scroll sideways.
//
// The "Since the last sign-off" block
// ([data-testid="sop-since"]), directly above the month table,
// is headed "Since the last sign-off". It reads only the review
// answer already loaded and sends no request of its own, and it
// shows only when since_signoff is not null — with since_signoff
// null, or absent from the answer, there is no block and no
// empty placeholder. Its sentence
// ([data-testid="sop-since-base"]) reads "Compared with the
// sign-off of <signed_month> by <signed_by> on <the date of
// signed_at>". The table ([data-testid="sop-since-table"]) has
// one column per entry of since_signoff.months in the answer's
// order, headed by the month, and these rows in this order:
// "Demand units", "From demand signals", "Demand value",
// "Open supply value", "Planned receipts value", "Total spend" and
// "Budget", from demand_units_delta, signal_units_delta,
// demand_value_delta, supply_value_delta,
// planned_receipts_value_delta, total_spend_delta and
// budget_delta. A positive delta reads with a leading "+" and a
// negative one with a leading "-": units carry thousands
// separators and values are whole dollars, as in "+1,234" and
// "-$5,678"; a delta that rounds to zero reads "No change", and
// a null or missing delta reads an em dash. With an empty months
// the sentence shows and, in place of the table, the block reads
// "No months in common with that sign-off.". The block belongs
// to the live review only: it never shows inside a past
// sign-off's frozen figures, even though a stored figures answer
// can carry since_signoff. Every API value is set as text, a
// delta is never marked good or bad by colour (a rise can be
// either), the table scrolls inside its own region, and at
// 390x844 the page does not scroll sideways.

// The month table ([data-testid="sop-table"]) has one column per
// entry of months in the answer's order, headed by the month, and
// these rows in this order under three group headings: "Demand" —
// "Demand units", "Demand value", "From demand signals"; "Supply" —
// "Open supply units", "Planned receipts units", "On hand as is",
// "On hand with plan", "Short as is", "Short with plan"; "Money" —
// "Committed", "Planned", "Total spend", "Budget", "Remaining",
// "Budget status". Units carry thousands separators, values and
// money are whole dollars, "From demand signals" reads
// signal_units with separators or an em dash when it is 0, rounds
// to 0, or is absent (a frozen row of an older answer), and "Short
// as is" / "Short with plan" join
// sites_short_as_is / sites_short_planned with commas (an em dash
// when empty); "Budget" and "Remaining" read an em dash when null;
// "Budget status" reads "Over budget" when over_budget is true,
// "No budget" when budget is null, else "Within budget". An
// over-budget cell and a non-empty short cell also carry the class
// sop-flag, so the flag is both words and colour. The table scrolls
// inside its own region, and at 390x844 the page does not scroll
// sideways.
//
// [data-testid="sop-no-cost"] reads "<n> item-sites have no unit
// cost, so the dollar figures leave them out." ("1 item-site has ...
// leave it out." at exactly 1), shown only when
// item_sites_without_unit_cost is above 0. [data-testid="sop-links"]
// holds "Open Demand" (#/demand), "Open Aggregate" (#/aggregate),
// "Open Scenario" (#/scenario) and "Open Order plan" (#/orders).
//
// A failed fetch shows "The review could not be loaded." in
// [data-testid="sop-error"] in place of the figures, and Refresh
// tries again. The answer, the error and the chosen horizon live in
// module state: sign-out clears them, and an answer arriving after
// sign-out is discarded. Every API value is inserted as text, never
// as markup.
(function (global) {
  'use strict';

  var LIST_PATH = '/api/sop/review';
  var ME_PATH = '/api/auth/me';
  var SIGNOFF_PATH = '/api/sop/signoffs';
  var SCENARIOS_PATH = '/api/sop/scenarios';
  var SIGNALS_PATH = '/api/demand-signals';
  var FORECAST_SIGNALS_PATH = '/api/forecast/accuracy/signals';
  var ROOT_ID = 'sop-root';
  var VIEW_ID = 'view-sop';
  var APP_ID = 'app';

  var EM_DASH = '\u2014';
  var MIDDLE_DOT = '\u00b7';

  var GENERIC_ERROR = 'The review could not be loaded.';
  var NOT_SIGNED = 'Not signed off yet';
  var SIGNOFF_FORBIDDEN = 'Only an admin can sign off.';
  var SIGNOFF_GENERIC_ERROR = 'The sign-off could not be recorded.';
  var HISTORY_HEADING = 'Sign-offs';
  var HISTORY_SHOW = 'Show sign-offs';
  var HISTORY_REFRESH = 'Refresh sign-offs';
  var HISTORY_EMPTY = 'No sign-offs yet.';
  var HISTORY_ERROR = 'Sign-offs could not be loaded.';
  var FIGURES_ERROR = 'These figures could not be loaded.';
  var SCENARIOS_TITLE = 'Scenarios';
  var SCENARIOS_SHOW = 'Compare saved scenarios';
  var SCENARIOS_REFRESH = 'Refresh the comparison';
  var SCENARIOS_BUSY = 'Comparing\u2026';
  var SCENARIOS_ERROR = 'The comparison could not be loaded.';
  var SCENARIOS_SAME_AS_PLAN = 'Same as the plan';
  var SIGNALS_TITLE = 'Demand signals';
  var SIGNALS_LIMIT = 200;
  var SIGNALS_SHOW = 'Show demand signals';
  var SIGNALS_REFRESH = 'Refresh demand signals';
  var SIGNALS_EMPTY = 'No demand signals.';
  var SIGNALS_ERROR = 'Demand signals could not be loaded.';
  var SIGNALS_MORE = 'Showing the first ' + SIGNALS_LIMIT + ' demand signals.';
  var SIGNAL_KINDS = { promotion: 'Promotion', deal: 'Deal', event: 'Event' };
  var NO_FORECAST = 'No forecast accuracy yet';
  var FORECAST_HEADING = 'Forecast accuracy';
  var SINCE_HEADING = 'Since the last sign-off';
  var SINCE_NO_COMMON = 'No months in common with that sign-off.';
  var NO_CHANGE = 'No change';

  // The horizons the select offers, in display order; 3 months is the
  // default and matches the server's own default.
  var HORIZONS = [
    { months: 3, label: '3 months' },
    { months: 6, label: '6 months' },
    { months: 12, label: '12 months' },
  ];
  var DEFAULT_MONTHS = 3;

  var rootEl = null;

  var state = {
    data: null, // the newest successful answer
    loading: false, // a fetch is out
    error: null, // the newest failed load: { status, message }
    months: DEFAULT_MONTHS, // the chosen horizon
    signoffAsk: false, // the sign-off panel is open
    signoffNote: '', // the note as typed into the panel
    signoffPending: false, // a sign-off request is out
    signoffError: null, // the last sign-off failure message
    historyRows: null, // the newest successful sign-offs list answer
    historyError: null, // the newest failed sign-offs list load
    historyLoaded: false, // a list answer has landed at least once
    historyPick: null, // the row whose frozen figures are shown
    historyFigures: null, // the newest successful figures answer of the pick
    historyFigError: null, // the newest failed figures load of the pick
    scenariosAnswer: null, // the newest successful comparison answer
    scenariosError: null, // the newest failed comparison load
    scenariosLoaded: false, // a comparison answer has landed at least once
    scenariosPending: false, // a comparison request is out
    signalsRows: null, // the newest successful demand-signals answer
    signalsError: null, // the newest failed demand-signals load
    signalsLoaded: false, // an answer has landed at least once
    signalsPending: false, // a demand-signals request is out
    forecastSignals: null, // the newest forecast-signals answer (fetched with the review)
  };

  // The request-sequence guard: only the newest fetch's answer renders.
  var seq = 0;

  // The viewer's admin role from GET /api/auth/me (once per
  // activation, its own sequence): null while the request is out or
  // before the first one — the sign-off control shows nothing until
  // the role is known; a failed answer leaves the viewer without it.
  var isAdmin = null;
  var meSeq = 0;

  // The sign-off request's own sequence: sign-out advances it, so a
  // sign-off answer arriving after sign-out is discarded.
  var signoffSeq = 0;

  // The sign-offs list's own sequence: sign-out advances it, so a
  // list answer arriving after sign-out is discarded.
  var historySeq = 0;

  // The frozen-figures request's own sequence: sign-out and Close
  // advance it, so a figures answer arriving after either is
  // discarded.
  var figuresSeq = 0;

  // The scenarios comparison's own sequence: sign-out advances it,
  // so a comparison answer arriving after sign-out is discarded.
  var scenariosSeq = 0;

  // The demand-signals list's own sequence: sign-out advances it,
  // so a list answer arriving after sign-out is discarded.
  var signalsSeq = 0;

  // The forecast-signals request's own sequence: sign-out advances
  // it, so an answer arriving after sign-out is discarded.
  var forecastSignalsSeq = 0;

  /* ------------------------------ helpers ------------------------------ */

  function esc(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
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

  function isUsable(value) {
    return isFinite(asNumber(value));
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

  // Whole dollars with separators (3121987.5 -> "$3,121,988"); an em
  // dash when the server carries no usable figure.
  function wholeDollars(value) {
    var number = asNumber(value);
    if (!isFinite(number)) {
      return EM_DASH;
    }
    return '$' + Math.round(number).toLocaleString('en-US');
  }

  // The month's demand-signal units with separators (1500 ->
  // "1,500"): an em dash when the figure is 0, rounds to 0, or is
  // absent — a frozen row of an older answer carries no
  // signal_units at all.
  function signalUnits(value) {
    var number = asNumber(value);
    if (!isFinite(number) || Math.round(number) === 0) {
      return EM_DASH;
    }
    return Math.round(number).toLocaleString('en-US');
  }

  // A rate as a percent to one decimal (0.445 -> "44.5%").
  function percent1(value) {
    return (asNumber(value) * 100).toFixed(1) + '%';
  }

  // A rate as a SIGNED percent to one decimal (0.023 -> "+2.3%",
  // -0.012 -> "-1.2%").
  function signedPercent1(value) {
    var number = asNumber(value);
    return (number >= 0 ? '+' : '') + (number * 100).toFixed(1) + '%';
  }

  // A rate as SIGNED points to one decimal, no percent sign
  // (0.021 -> "+2.1", -0.012 -> "-1.2"): the planner-override delta
  // of the error.
  function signedPoints1(value) {
    var number = asNumber(value);
    return (number >= 0 ? '+' : '') + (number * 100).toFixed(1);
  }

  // A delta in whole units, signed (1234 -> "+1,234",
  // -5678.4 -> "-5,678"): thousands separators, no dollar sign; a
  // delta that rounds to zero reads "No change", a null or missing
  // delta an em dash.
  function deltaUnits(value) {
    var number = asNumber(value);
    if (!isFinite(number)) {
      return EM_DASH;
    }
    if (Math.round(number) === 0) {
      return NO_CHANGE;
    }
    return (number > 0 ? '+' : '-') + Math.round(Math.abs(number)).toLocaleString('en-US');
  }

  // A delta in whole dollars, signed (1000 -> "+$1,000",
  // -5678.4 -> "-$5,678"); zero and missing read as in deltaUnits.
  function deltaDollars(value) {
    var number = asNumber(value);
    if (!isFinite(number)) {
      return EM_DASH;
    }
    if (Math.round(number) === 0) {
      return NO_CHANGE;
    }
    return (number > 0 ? '+$' : '-$') + Math.round(Math.abs(number)).toLocaleString('en-US');
  }

  function nonEmptyString(value) {
    return typeof value === 'string' && value !== '' ? value : null;
  }

  // A value the screen can show as its own text: a non-empty string,
  // or a finite number through String — a numeric review month or a
  // numeric site code is data the server sent, not a missing value.
  function displayValue(value) {
    if (typeof value === 'string') {
      return value !== '' ? value : null;
    }
    if (typeof value === 'number' && isFinite(value)) {
      return String(value);
    }
    return null;
  }

  // The sign-off's date: the part of created_at before the "T"; an
  // em dash when the answer carries no usable one.
  function signoffDateText(value) {
    if (typeof value !== 'string') {
      return EM_DASH;
    }
    var date = value.split('T')[0];
    return date !== '' ? date : EM_DASH;
  }

  // The shortage sites joined with commas — a numeric code renders
  // through String (displayValue), so a non-string code is never
  // silently dropped; an em dash when the list is empty or carries no
  // usable site code.
  function sitesText(list) {
    if (!Array.isArray(list)) {
      return EM_DASH;
    }
    var parts = [];
    for (var i = 0; i < list.length; i++) {
      var item = displayValue(list[i]);
      if (item) {
        parts.push(item);
      }
    }
    return parts.length > 0 ? parts.join(', ') : EM_DASH;
  }

  // A well-formed answer: an object (never an array or a bare value).
  // Anything else is a failed load.
  function answerValid(payload) {
    return payload && typeof payload === 'object' && !Array.isArray(payload);
  }

  /* ------------------------------ loading ------------------------------ */

  function load() {
    seq += 1;
    var mySeq = seq;
    state.loading = true;
    state.error = null;
    render();
    loadForecastSignals(); // once with each load of the review: its own guard
    global.Api.get(LIST_PATH + '?months=' + encodeURIComponent(state.months)).then(
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
        state.error = {
          status: error && typeof error.status === 'number' ? error.status : 0,
          message: GENERIC_ERROR,
        };
        render();
      }
    );
  }

  // The realised value of the stored demand signals for the forecast
  // card's signals line: GET /api/forecast/accuracy/signals once with
  // each load of the review, through app/js/api.js, under its own
  // request-sequence guard so only the newest answer renders. The
  // answer is cleared while a request is out — nothing to say while
  // loading — and on a failed fetch, so the line is not in the page in
  // either case, and the rest of the review reads as before.
  function loadForecastSignals() {
    forecastSignalsSeq += 1;
    var mySeq = forecastSignalsSeq;
    state.forecastSignals = null;
    global.Api.get(FORECAST_SIGNALS_PATH).then(
      function (payload) {
        if (mySeq !== forecastSignalsSeq) {
          return; // signed out in the meantime: the answer is discarded
        }
        if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
          state.forecastSignals = payload;
        }
        render();
      },
      function () {
        if (mySeq !== forecastSignalsSeq) {
          return;
        }
        state.forecastSignals = null;
        render();
      }
    );
  }

  /* --------------------------- sign-off control ------------------------ */

  // The viewer's admin role, from GET /api/auth/me once per
  // activation: only a user whose roles include "admin" gets the
  // sign-off control; a failed answer leaves the viewer without it.
  function startMeLoad() {
    isAdmin = null;
    var mySeq = ++meSeq;
    global.Api.get(ME_PATH).then(
      function (me) {
        if (mySeq !== meSeq) {
          return; // a newer activation or sign-out: this answer is stale
        }
        var roles = me && me.user && Array.isArray(me.user.roles) ? me.user.roles : [];
        isAdmin = roles.indexOf('admin') !== -1;
        render();
      },
      function () {
        if (mySeq !== meSeq) {
          return;
        }
        isAdmin = false;
        render();
      }
    );
  }

  // Opening the panel is a local state change: no request goes out.
  function openSignoffPanel() {
    if (!state.data) {
      return; // no loaded answer: the open control is not shown either
    }
    state.signoffAsk = true;
    state.signoffError = null;
    render();
  }

  // Cancel closes the panel and sends nothing. The Cancel button is
  // disabled while a request is out, so this path always finds the
  // pending flag clear — and clears it.
  function cancelSignoff() {
    if (state.signoffPending) {
      return;
    }
    state.signoffAsk = false;
    state.signoffNote = '';
    state.signoffError = null;
    state.signoffPending = false;
    render();
  }

  // The failure message: a 403 always reads the admin note, a 422
  // shows the server's detail (how a rolled-over month is reported),
  // anything else the generic message.
  function signoffErrorMessage(error) {
    var status = error && typeof error.status === 'number' ? error.status : 0;
    if (status === 403) {
      return SIGNOFF_FORBIDDEN;
    }
    if (status === 422 && error && error.message) {
      return error.message;
    }
    return SIGNOFF_GENERIC_ERROR;
  }

  // Confirming sends one POST /api/sop/signoffs through app/js/api.js
  // with the month and the horizon loaded at that moment and the
  // trimmed note when it is not empty. The pending flag — the
  // confirm button's disabled state — makes a double click send one
  // request. On success the panel closes and clears and the review
  // fetches once more through its usual path; on failure the panel
  // stays open with the note kept and the message below the note.
  function confirmSignoff() {
    if (state.signoffPending || !state.data) {
      return;
    }
    var note = String(state.signoffNote).trim();
    var body = {
      month: state.data.review_month,
      months: state.months,
    };
    if (note !== '') {
      body.note = note;
    }
    var mySeq = signoffSeq;
    state.signoffPending = true;
    state.signoffError = null;
    render();
    global.Api.request(SIGNOFF_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(
      function () {
        if (mySeq !== signoffSeq) {
          return; // signed out in the meantime: this answer is discarded
        }
        state.signoffPending = false;
        state.signoffAsk = false;
        state.signoffNote = '';
        state.signoffError = null;
        load(); // the status line reads the server's answer
        if (state.historyRows) {
          loadSignoffs(); // a list answer is on screen: the new row appears
        }
      },
      function (error) {
        if (mySeq !== signoffSeq) {
          return;
        }
        state.signoffPending = false;
        state.signoffError = signoffErrorMessage(error);
        render();
      }
    );
  }

  /* ------------------------- the stored sign-offs ---------------------- */

  // The stored sign-offs, fetched on demand only: the Show/Refresh
  // button is the sole trigger, through app/js/api.js, with the
  // screen's fixed limit of 50 rows. The newest answer renders, a
  // failed load shows the error with the review above untouched, and
  // the previous answer stays on screen — the module state is
  // cleared only by sign-out.
  function loadSignoffs() {
    historySeq += 1;
    var mySeq = historySeq;
    state.historyError = null;
    render();
    global.Api.get(SIGNOFF_PATH + '?limit=50').then(
      function (payload) {
        if (mySeq !== historySeq) {
          return; // signed out in the meantime: the answer is discarded
        }
        if (Array.isArray(payload)) {
          state.historyRows = payload;
          state.historyLoaded = true;
        } else {
          state.historyError = HISTORY_ERROR;
        }
        render();
      },
      function () {
        if (mySeq !== historySeq) {
          return;
        }
        state.historyError = HISTORY_ERROR;
        render();
      }
    );
  }

  // One row's frozen figures: GET /api/sop/signoffs/{id} once per
  // click. The block opens with its heading (the row's signer and
  // the date of created_at) while the answer is out; the stored
  // figures then render through the same forecast and month-table
  // code as the review, so the two cannot drift apart. Choosing
  // another row replaces the block, and only the newest answer of
  // the newest choice renders; a failure shows its own note inside
  // the block.
  function viewFigures(row) {
    if (!row || typeof row !== 'object') {
      return;
    }
    state.historyPick = row;
    state.historyFigures = null;
    state.historyFigError = null;
    figuresSeq += 1;
    var mySeq = figuresSeq;
    render(); // the block stands with its heading while the fetch is out
    global.Api.get(SIGNOFF_PATH + '/' + encodeURIComponent(String(row.id))).then(
      function (payload) {
        if (mySeq !== figuresSeq) {
          return; // closed, replaced or signed out: the answer is stale
        }
        var figures =
          payload && typeof payload === 'object' &&
          payload.figures && typeof payload.figures === 'object'
            ? payload.figures
            : null;
        if (figures) {
          state.historyFigures = figures;
        } else {
          state.historyFigError = FIGURES_ERROR;
        }
        render();
      },
      function () {
        if (mySeq !== figuresSeq) {
          return;
        }
        state.historyFigError = FIGURES_ERROR;
        render();
      }
    );
  }

  // Close hides the block and discards any figures answer still out.
  function closeFigures() {
    figuresSeq += 1;
    state.historyPick = null;
    state.historyFigures = null;
    state.historyFigError = null;
    render();
  }

  /* ------------------------- the scenarios comparison ------------------ */

  // The scenarios comparison, fetched on demand only: the
  // Compare/Refresh button is the sole trigger, through app/js/api.js,
  // with the screen's chosen horizon — opening the view and changing
  // the horizon send no request. The pending flag (the button's
  // disabled state) makes a double click send one request, a
  // request-sequence guard renders only the newest answer, and an
  // answer arriving after sign-out is discarded. After the horizon
  // changes, the answer already showing stays — its heading still
  // says which horizon it covers. A failed load shows the error with
  // the review above and any earlier comparison untouched.
  function loadScenarios() {
    if (state.scenariosPending) {
      return; // a request is already out the door: one at a time
    }
    scenariosSeq += 1;
    var mySeq = scenariosSeq;
    state.scenariosPending = true;
    state.scenariosError = null;
    render();
    global.Api.get(SCENARIOS_PATH + '?months=' + encodeURIComponent(state.months)).then(
      function (payload) {
        if (mySeq !== scenariosSeq) {
          return; // signed out in the meantime: the answer is discarded
        }
        state.scenariosPending = false;
        if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
          state.scenariosAnswer = payload;
          state.scenariosLoaded = true;
        } else {
          state.scenariosError = SCENARIOS_ERROR;
        }
        render();
      },
      function () {
        if (mySeq !== scenariosSeq) {
          return;
        }
        state.scenariosPending = false;
        state.scenariosError = SCENARIOS_ERROR;
        render();
      }
    );
  }

  /* ------------------------- the demand signals ---------------------- */

  // The demand signals, fetched on demand only: the Show/Refresh
  // button is the sole trigger, through app/js/api.js, with the
  // screen's fixed limit of 200 rows — opening the view sends no
  // request. The pending flag (the button's disabled state) makes a
  // double click send one request, a request-sequence guard renders
  // only the newest answer, and an answer arriving after sign-out is
  // discarded. A failed load shows its error with the rest of the
  // screen unchanged. The section is read-only: it adds and
  // withdraws nothing, those controls stay on the Demand screen.
  function loadSignals() {
    if (state.signalsPending) {
      return; // a request is already out the door: one at a time
    }
    signalsSeq += 1;
    var mySeq = signalsSeq;
    state.signalsPending = true;
    state.signalsError = null;
    render();
    global.Api.get(SIGNALS_PATH + '?limit=' + SIGNALS_LIMIT).then(
      function (payload) {
        if (mySeq !== signalsSeq) {
          return; // signed out in the meantime: the answer is discarded
        }
        state.signalsPending = false;
        if (Array.isArray(payload)) {
          state.signalsRows = payload;
          state.signalsLoaded = true;
        } else {
          state.signalsError = SIGNALS_ERROR;
        }
        render();
      },
      function () {
        if (mySeq !== signalsSeq) {
          return;
        }
        state.signalsPending = false;
        state.signalsError = SIGNALS_ERROR;
        render();
      }
    );
  }

  // The rows in display order: start_date ascending, then id. A
  // usable ISO date string always sorts before a missing one, a
  // missing id sorts after any id; the answer's own array is never
  // mutated.
  function sortSignals(rows) {
    var sorted = rows.slice();
    sorted.sort(function (a, b) {
      var left = a && typeof a === 'object' ? a : {};
      var right = b && typeof b === 'object' ? b : {};
      var leftHasDate = typeof left.start_date === 'string' && left.start_date !== '';
      var rightHasDate = typeof right.start_date === 'string' && right.start_date !== '';
      if (leftHasDate && rightHasDate) {
        if (left.start_date !== right.start_date) {
          return left.start_date < right.start_date ? -1 : 1;
        }
      } else if (leftHasDate !== rightHasDate) {
        return leftHasDate ? -1 : 1;
      }
      var leftId = asNumber(left.id);
      var rightId = asNumber(right.id);
      if (!isFinite(leftId) && !isFinite(rightId)) {
        return 0;
      }
      if (!isFinite(leftId)) {
        return 1;
      }
      if (!isFinite(rightId)) {
        return -1;
      }
      return leftId - rightId;
    });
    return sorted;
  }

  // "promotion" -> "Promotion" (the table's Kind column); an unknown
  // kind is capitalised, an empty one reads an em dash — never raw
  // markup.
  function signalKindLabel(value) {
    var key = String(value == null ? '' : value).toLowerCase();
    if (SIGNAL_KINDS[key]) {
      return SIGNAL_KINDS[key];
    }
    var text = String(value == null ? '' : value).trim();
    if (!text) {
      return EM_DASH;
    }
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  /* ------------------------------ rendering ---------------------------- */

  function render() {
    if (!rootEl) {
      return;
    }
    rootEl.innerHTML = shellHtml();
    // The note is set as a value, never as markup: the panel markup
    // carries no note at all, so hostile text can never run.
    if (state.signoffAsk) {
      var noteEl = rootEl.querySelector('[data-testid="sop-signoff-text"]');
      if (noteEl) {
        noteEl.value = state.signoffNote;
      }
    }
  }

  function shellHtml() {
    // The Sign-offs section renders under every body state — the
    // skeleton, the error and the figures — below the links when
    // they are there, visible whether or not the review has loaded.
    // First load (no answer, no failure yet) and every in-flight
    // fetch of it: the skeleton.
    if (!state.data && !state.error) {
      return (
        '<div class="sop">' + barHtml() + signoffPanelHtml() +
        '<div class="sop-body">' + skeletonHtml() + signalsHtml() + scenariosHtml() + historyHtml() + '</div></div>'
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
      '<div class="sop">' + barHtml() + signoffPanelHtml() +
      '<div class="sop-body">' + body + signalsHtml() + scenariosHtml() + historyHtml() + '</div></div>'
    );
  }

  function barHtml() {
    var options = HORIZONS.map(function (horizon) {
      return (
        '<option value="' + horizon.months + '"' +
        (state.months === horizon.months ? ' selected' : '') + '>' +
        esc(horizon.label) +
        '</option>'
      );
    }).join('');
    return (
      '<div class="sop-bar">' +
      '<label class="sop-months-label" for="sop-months">Horizon</label>' +
      '<select class="sop-months" id="sop-months" data-testid="sop-months">' +
      options +
      '</select>' +
      '<button type="button" class="btn btn-secondary sop-refresh" data-testid="sop-refresh">Refresh</button>' +
      '</div>'
    );
  }

  function statusHtml(data) {
    var status =
      'Review month ' +
      (displayValue(data.review_month) || EM_DASH) +
      ' ' + MIDDLE_DOT + ' as of ' +
      (displayValue(data.as_of) || EM_DASH);
    var signoff = data.latest_signoff;
    var signoffHtml;
    if (signoff && typeof signoff === 'object') {
      var who = nonEmptyString(signoff.actor_name) || nonEmptyString(signoff.actor) || EM_DASH;
      signoffHtml =
        '<p class="sop-signoff" data-testid="sop-signoff">Signed off by ' +
        esc(who) + ' on ' + esc(signoffDateText(signoff.created_at)) + '</p>';
      var note = nonEmptyString(signoff.note);
      if (note) {
        signoffHtml +=
          '<p class="sop-signoff-note" data-testid="sop-signoff-note">' +
          esc(note) + '</p>';
      }
    } else {
      signoffHtml =
        '<p class="sop-signoff" data-testid="sop-signoff">' +
        esc(NOT_SIGNED) + '</p>';
    }
    return (
      '<section class="sop-card sop-status-card">' +
      '<p class="sop-status" data-testid="sop-status">' + esc(status) + '</p>' +
      signoffHtml +
      signoffOpenHtml(data) +
      '</section>'
    );
  }

  // The forecast line: the overall wape leads; the one-month-ahead
  // part is left out when next_month_wape is null; with wape null the
  // whole line reads the not-yet note.
  function forecastLine(data) {
    var forecast = data.forecast && typeof data.forecast === 'object' ? data.forecast : null;
    if (!forecast || !isUsable(forecast.wape)) {
      return NO_FORECAST;
    }
    var parts = ['Error ' + percent1(forecast.wape) + ' overall'];
    if (isUsable(forecast.next_month_wape)) {
      parts.push(percent1(forecast.next_month_wape) + ' one month ahead');
    }
    if (isUsable(forecast.bias)) {
      parts.push('bias ' + signedPercent1(forecast.bias));
    }
    return parts.join(' ' + MIDDLE_DOT + ' ');
  }

  // The forecast card's demand-signals line (issue #985): the
  // realised value of the stored demand signals, from the answer
  // fetched with the review. Shown only when compared_buckets is
  // above 0 and value_added is a number — while the request is out,
  // with compared_buckets 0, with a null value_added or on a failed
  // fetch the line is not in the page. The points scale by 100, so a
  // hostile value that overflows that scale is no number at all and
  // leaves the line out; the frozen figures of a past sign-off carry
  // no such figure and never render the line.
  function forecastSignalsHtml() {
    var data = state.forecastSignals;
    if (!data || typeof data !== 'object') {
      return '';
    }
    var buckets = asNumber(data.compared_buckets);
    var points = asNumber(data.value_added);
    if (!isFinite(buckets) || buckets <= 0) {
      return '';
    }
    if (!isFinite(points) || !isFinite(points * 100)) {
      return '';
    }
    return (
      '<p class="sop-forecast-signals" data-testid="sop-forecast-signals">' +
      esc(
        'Demand signals changed the error by ' +
        signedPoints1(data.value_added) +
        ' points on ' +
        wholeNumber(buckets) +
        ' periods'
      ) +
      '</p>'
    );
  }

  // tableTestId / forecastTestId / overridesTestId let the frozen
  // sign-off figures reuse the same rendering code under their own
  // testids, so the review's locators stay unambiguous; signalsHtml
  // is the live review's own forecast-signals line — the frozen
  // figures pass none, because they store no such figure.
  function forecastHtml(data, forecastTestId, overridesTestId, signalsHtml) {
    var forecast = data.forecast && typeof data.forecast === 'object' ? data.forecast : null;
    var overridesHtml = '';
    if (
      forecast &&
      asNumber(forecast.overridden_buckets) > 0 &&
      isUsable(forecast.value_added)
    ) {
      overridesHtml =
        '<p class="sop-forecast-overrides" data-testid="' +
        (overridesTestId || 'sop-forecast-overrides') + '">' +
        esc(
          'Planner overrides changed the error by ' +
          signedPoints1(forecast.value_added) + ' points'
        ) +
        '</p>';
    }
    return (
      '<section class="sop-card sop-forecast-card">' +
      '<h2 class="sop-card-title">' + esc(FORECAST_HEADING) + '</h2>' +
      '<p class="sop-forecast" data-testid="' + (forecastTestId || 'sop-forecast') + '">' +
      esc(forecastLine(data)) + '</p>' +
      overridesHtml +
      (signalsHtml || '') +
      '</section>'
    );
  }

  // The "Since the last sign-off" block: the drift of this review
  // against the newest stored sign-off, read only from the
  // already-loaded review answer's since_signoff — it sends no
  // request of its own. Shown only when since_signoff is present
  // and not null — with it null or absent there is no block and
  // no empty placeholder — and it belongs to the live review
  // only: the frozen figures of a past sign-off never render it,
  // even though a stored figures answer can carry since_signoff.
  // With an empty months the sentence stands and the no-common
  // note takes the table's place. A delta is never marked good or
  // bad by colour, because a rise can be either.
  function sinceHtml(data) {
    var since = data && typeof data === 'object' ? data.since_signoff : null;
    if (!since || typeof since !== 'object' || Array.isArray(since)) {
      return '';
    }
    var month = nonEmptyString(since.signed_month) || EM_DASH;
    var who = nonEmptyString(since.signed_by) || EM_DASH;
    var sentence =
      'Compared with the sign-off of ' + month +
      ' by ' + who +
      ' on ' + signoffDateText(since.signed_at);
    return (
      '<section class="sop-card sop-since-card" data-testid="sop-since" aria-label="' +
      esc(SINCE_HEADING) + '">' +
      '<h2 class="sop-card-title">' + esc(SINCE_HEADING) + '</h2>' +
      '<p class="sop-since-base" data-testid="sop-since-base">' + esc(sentence) + '</p>' +
      sinceTableHtml(since) +
      '</section>'
    );
  }

  // The delta table: one column per entry of since_signoff.months
  // in the answer's order, headed by the month, the six rows in
  // order; an empty months reads the no-common note instead. The
  // table scrolls inside its own region.
  function sinceTableHtml(since) {
    var months = Array.isArray(since.months) ? since.months : [];
    if (months.length === 0) {
      return (
        '<p class="sop-since-none" data-testid="sop-since-none">' +
        esc(SINCE_NO_COMMON) + '</p>'
      );
    }
    var headerCells = ['<th class="sop-corner"></th>'];
    for (var i = 0; i < months.length; i++) {
      var entry = months[i];
      var heading =
        entry && typeof entry === 'object'
          ? nonEmptyString(entry.month) || String(i + 1)
          : String(i + 1);
      headerCells.push('<th scope="col">' + esc(heading) + '</th>');
    }
    var rows = [];
    rows.push(dataRowHtml('Demand units', months, function (m) { return deltaCell(m, 'demand_units_delta', deltaUnits, 'num'); }));
    rows.push(dataRowHtml('From demand signals', months, function (m) { return deltaCell(m, 'signal_units_delta', deltaUnits, 'num'); }));
    rows.push(dataRowHtml('Demand value', months, function (m) { return deltaCell(m, 'demand_value_delta', deltaDollars, 'money'); }));
    rows.push(dataRowHtml('Open supply value', months, function (m) { return deltaCell(m, 'supply_value_delta', deltaDollars, 'money'); }));
    rows.push(dataRowHtml('Planned receipts value', months, function (m) { return deltaCell(m, 'planned_receipts_value_delta', deltaDollars, 'money'); }));
    rows.push(dataRowHtml('Total spend', months, function (m) { return deltaCell(m, 'total_spend_delta', deltaDollars, 'money'); }));
    rows.push(dataRowHtml('Budget', months, function (m) { return deltaCell(m, 'budget_delta', deltaDollars, 'money'); }));
    return (
      '<div class="sop-since-region" role="region" aria-label="' + esc(SINCE_HEADING) + '">' +
      '<table class="sop-table" data-testid="sop-since-table">' +
      '<thead><tr>' + headerCells.join('') + '</tr></thead>' +
      '<tbody>' + rows.join('') + '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  // One delta cell: the formatter's text, set as text — a delta
  // is never marked good or bad by colour.
  function deltaCell(month, field, format, kind) {
    return '<td class="sop-cell sop-cell-' + kind + '">' + esc(format(month[field])) + '</td>';
  }

  // The row builders: each returns the month's cell markup for one
  // row label. Units read whole with separators, money reads whole
  // dollars, the shortage cells flag when non-empty, and the budget
  // status flags when over budget.
  function unitCell(month, field) {
    return '<td class="sop-cell sop-cell-num">' + esc(wholeNumber(month[field])) + '</td>';
  }

  function signalCell(month, field) {
    return '<td class="sop-cell sop-cell-num">' + esc(signalUnits(month[field])) + '</td>';
  }

  function moneyCell(month, field) {
    return '<td class="sop-cell sop-cell-money">' + esc(wholeDollars(month[field])) + '</td>';
  }

  function sitesCell(month, field) {
    var text = sitesText(month[field]);
    var flag = text !== EM_DASH ? ' sop-flag' : '';
    return '<td class="sop-cell sop-cell-sites' + flag + '">' + esc(text) + '</td>';
  }

  function budgetStatusCell(month) {
    var over = month.over_budget === true;
    var text;
    if (over) {
      text = 'Over budget';
    } else if (month.budget === null || month.budget === undefined) {
      text = 'No budget';
    } else {
      text = 'Within budget';
    }
    var flag = over ? ' sop-flag' : '';
    return '<td class="sop-cell sop-cell-status' + flag + '">' + esc(text) + '</td>';
  }

  function groupRowHtml(label, colspan) {
    return (
      '<tr class="sop-group-row"><th class="sop-group" colspan="' +
      colspan + '" scope="colgroup">' + esc(label) + '</th></tr>'
    );
  }

  function dataRowHtml(label, months, cellFn) {
    var cells = [];
    for (var i = 0; i < months.length; i++) {
      cells.push(cellFn(months[i] || {}, i));
    }
    return (
      '<tr><th scope="row" class="sop-row-label">' + esc(label) + '</th>' +
      cells.join('') + '</tr>'
    );
  }

  function tableHtml(data, tableTestId) {
    var months = data && Array.isArray(data.months) ? data.months : [];
    var headerCells = ['<th class="sop-corner"></th>'];
    for (var i = 0; i < months.length; i++) {
      var heading = nonEmptyString(months[i] && months[i].month) || String(i + 1);
      headerCells.push('<th scope="col">' + esc(heading) + '</th>');
    }
    var colspan = months.length + 1;
    var rows = [];
    rows.push(groupRowHtml('Demand', colspan));
    rows.push(dataRowHtml('Demand units', months, function (month) { return unitCell(month, 'demand_units'); }));
    rows.push(dataRowHtml('Demand value', months, function (month) { return moneyCell(month, 'demand_value'); }));
    rows.push(dataRowHtml('From demand signals', months, function (month) { return signalCell(month, 'signal_units'); }));
    rows.push(groupRowHtml('Supply', colspan));
    rows.push(dataRowHtml('Open supply units', months, function (month) { return unitCell(month, 'supply_units'); }));
    rows.push(dataRowHtml('Planned receipts units', months, function (month) { return unitCell(month, 'planned_receipts_units'); }));
    rows.push(dataRowHtml('On hand as is', months, function (month) { return unitCell(month, 'projected_on_hand_units'); }));
    rows.push(dataRowHtml('On hand with plan', months, function (month) { return unitCell(month, 'planned_on_hand_units'); }));
    rows.push(dataRowHtml('Short as is', months, function (month) { return sitesCell(month, 'sites_short_as_is'); }));
    rows.push(dataRowHtml('Short with plan', months, function (month) { return sitesCell(month, 'sites_short_planned'); }));
    rows.push(groupRowHtml('Money', colspan));
    rows.push(dataRowHtml('Committed', months, function (month) { return moneyCell(month, 'committed_spend'); }));
    rows.push(dataRowHtml('Planned', months, function (month) { return moneyCell(month, 'planned_spend'); }));
    rows.push(dataRowHtml('Total spend', months, function (month) { return moneyCell(month, 'total_spend'); }));
    rows.push(dataRowHtml('Budget', months, function (month) { return moneyCell(month, 'budget'); }));
    rows.push(dataRowHtml('Remaining', months, function (month) { return moneyCell(month, 'remaining'); }));
    rows.push(dataRowHtml('Budget status', months, function (month) { return budgetStatusCell(month); }));
    return (
      '<div class="sop-table-region" role="region" aria-label="Review by month">' +
      '<table class="sop-table" data-testid="' + (tableTestId || 'sop-table') + '">' +
      '<thead><tr>' + headerCells.join('') + '</tr></thead>' +
      '<tbody>' + rows.join('') + '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  // The missing-cost note: shown only while item_sites_without_unit_cost
  // is above 0, singular at exactly 1.
  function noCostHtml(data) {
    var count = asNumber(data.item_sites_without_unit_cost);
    if (!isFinite(count) || count <= 0) {
      return '';
    }
    var text =
      count === 1
        ? '1 item-site has no unit cost, so the dollar figures leave it out.'
        : Math.round(count) + ' item-sites have no unit cost, so the dollar figures leave them out.';
    return '<p class="sop-no-cost" data-testid="sop-no-cost">' + esc(text) + '</p>';
  }

  // Where to act: the sibling screens the review points at.
  function linksHtml() {
    return (
      '<nav class="sop-links" data-testid="sop-links" aria-label="Where to act">' +
      '<a href="#/demand">Open Demand</a>' +
      '<a href="#/aggregate">Open Aggregate</a>' +
      '<a href="#/scenario">Open Scenario</a>' +
      '<a href="#/orders">Open Order plan</a>' +
      '</nav>'
    );
  }

  // The Sign-offs section: below the links, for every signed-in
  // user, whether or not the review has loaded. The Show/Refresh
  // button is the only trigger of the list fetch; the list, its
  // error, the chosen row and its answer come from module state, so
  // a re-render keeps them.
  function historyHtml() {
    var label = state.historyLoaded ? HISTORY_REFRESH : HISTORY_SHOW;
    var error = state.historyError
      ? '<p class="sop-history-error" data-testid="sop-history-error">' +
        esc(HISTORY_ERROR) + '</p>'
      : '';
    var list = '';
    if (!state.historyError && state.historyRows) {
      if (state.historyRows.length === 0) {
        list =
          '<p class="sop-history-empty" data-testid="sop-history-empty">' +
          esc(HISTORY_EMPTY) + '</p>';
      } else {
        list = historyTableHtml(state.historyRows);
      }
    }
    return (
      '<section class="sop-card sop-history" data-testid="sop-history" aria-label="' +
      esc(HISTORY_HEADING) + '">' +
      '<div class="sop-history-head">' +
      '<h2 class="sop-card-title">' + esc(HISTORY_HEADING) + '</h2>' +
      '<button type="button" class="btn btn-secondary sop-history-show" data-testid="sop-history-show">' +
      esc(label) + '</button>' +
      '</div>' +
      error + list + historyFiguresHtml() +
      '</section>'
    );
  }

  // One row per answer row, in the answer's order: Month, Signed by
  // (actor_name, else actor), Date (the date of created_at), Note
  // (an em dash when null) and the View figures button, which names
  // its row by its index in the answer. A malformed row renders
  // nothing and nothing throws.
  function historyTableHtml(rows) {
    var trs = [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!row || typeof row !== 'object') {
        continue;
      }
      var who = nonEmptyString(row.actor_name) || nonEmptyString(row.actor) || EM_DASH;
      trs.push(
        '<tr>' +
        '<td class="sop-history-cell">' + esc(displayValue(row.month) || EM_DASH) + '</td>' +
        '<td class="sop-history-cell">' + esc(who) + '</td>' +
        '<td class="sop-history-cell">' + esc(signoffDateText(row.created_at)) + '</td>' +
        '<td class="sop-history-cell sop-history-note">' +
        esc(nonEmptyString(row.note) || EM_DASH) + '</td>' +
        '<td class="sop-history-cell sop-history-action">' +
        '<button type="button" class="btn btn-secondary sop-history-view" data-testid="sop-history-view" data-sop-history-index="' +
        i + '">View figures</button>' +
        '</td>' +
        '</tr>'
      );
    }
    return (
      '<div class="sop-history-region" role="region" aria-label="' +
      esc(HISTORY_HEADING) + ' list">' +
      '<table class="sop-history-table" data-testid="sop-history-table">' +
      '<thead><tr>' +
      '<th scope="col">Month</th>' +
      '<th scope="col">Signed by</th>' +
      '<th scope="col">Date</th>' +
      '<th scope="col">Note</th>' +
      '<th scope="col"></th>' +
      '</tr></thead>' +
      '<tbody>' + trs.join('') + '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  // The frozen figures of the picked row: read-only, with no
  // sign-off control. The heading carries the date of the row's
  // created_at and its signer; the forecast sentence and the month
  // table are the review's own renderers fed the stored figures.
  // While the answer is out the block stands with its heading; a
  // failure shows its own note inside it.
  function historyFiguresHtml() {
    if (!state.historyPick || typeof state.historyPick !== 'object') {
      return '';
    }
    var row = state.historyPick;
    var who = nonEmptyString(row.actor_name) || nonEmptyString(row.actor) || EM_DASH;
    var heading =
      'Figures as signed on ' + signoffDateText(row.created_at) + ' by ' + who;
    var inner;
    if (state.historyFigError) {
      inner =
        '<p class="sop-history-fig-error" data-testid="sop-history-figures-error">' +
        esc(FIGURES_ERROR) + '</p>';
    } else if (state.historyFigures) {
      inner =
        forecastHtml(
          state.historyFigures,
          'sop-history-forecast',
          'sop-history-forecast-overrides'
        ) +
        tableHtml(state.historyFigures, 'sop-history-figures-table');
    } else {
      inner = ''; // the answer is still out
    }
    return (
      '<div class="sop-history-figures" data-testid="sop-history-figures">' +
      '<div class="sop-history-figures-head">' +
      '<h3 class="sop-history-figures-title">' + esc(heading) + '</h3>' +
      '<button type="button" class="btn btn-secondary sop-history-close" data-testid="sop-history-close">Close</button>' +
      '</div>' +
      inner +
      '</div>'
    );
  }

  // The Demand signals section: directly above the Scenarios
  // section, for every signed-in user, whether or not the review has
  // loaded. The button's label stands for the module state (an
  // answer that has landed reads "Refresh demand signals"), the
  // pending flag disables it, and the answer, the error and the
  // label survive a re-render. The section holds no add or withdraw
  // control: those stay on the Demand screen.
  function signalsHtml() {
    var label = state.signalsLoaded ? SIGNALS_REFRESH : SIGNALS_SHOW;
    var disabled = state.signalsPending ? ' disabled' : '';
    var error = state.signalsError
      ? '<p class="sop-signals-error" data-testid="sop-signals-error">' +
        esc(SIGNALS_ERROR) + '</p>'
      : '';
    var content;
    if (!state.signalsRows) {
      content = '';
    } else if (state.signalsRows.length === 0) {
      content =
        '<p class="sop-signals-empty" data-testid="sop-signals-empty">' +
        esc(SIGNALS_EMPTY) + '</p>';
    } else {
      content = signalsTableHtml(state.signalsRows);
    }
    return (
      '<section class="sop-card sop-signals" data-testid="sop-signals" aria-label="' +
      esc(SIGNALS_TITLE) + '">' +
      '<div class="sop-signals-head">' +
      '<h2 class="sop-card-title">' + esc(SIGNALS_TITLE) + '</h2>' +
      '<button type="button" class="btn btn-secondary sop-signals-show" data-testid="sop-signals-show"' +
      disabled + '>' +
      esc(label) + '</button>' +
      '</div>' +
      error + content +
      '</section>'
    );
  }

  // One row per signal, sorted by start_date ascending and then by
  // id: Item (linking #/demand), Site, Kind, From and To (the API's
  // start_date and end_date as raw ISO date strings, without locale
  // formatting), Units with separators, Reason and Added by. Every
  // API value is escaped; a malformed row renders nothing and
  // nothing throws. When the answer has the full limit of rows, the
  // "more" note follows the table.
  function signalsTableHtml(rows) {
    var sorted = sortSignals(rows);
    var trs = [];
    for (var i = 0; i < sorted.length; i++) {
      var row = sorted[i];
      if (!row || typeof row !== 'object') {
        continue;
      }
      var item = displayValue(row.item_code) || EM_DASH;
      trs.push(
        '<tr>' +
        '<td class="sop-signals-item">' +
        '<a class="sop-signals-link" href="#/demand">' + esc(item) + '</a>' +
        '</td>' +
        '<td class="sop-signals-code">' +
        esc(displayValue(row.site_code) || EM_DASH) +
        '</td>' +
        '<td class="sop-signals-kind">' + esc(signalKindLabel(row.kind)) + '</td>' +
        '<td class="sop-signals-date">' +
        esc(row.start_date == null ? EM_DASH : String(row.start_date)) +
        '</td>' +
        '<td class="sop-signals-date">' +
        esc(row.end_date == null ? EM_DASH : String(row.end_date)) +
        '</td>' +
        '<td class="sop-signals-units">' + esc(wholeNumber(row.quantity)) + '</td>' +
        '<td class="sop-signals-reason">' +
        esc(nonEmptyString(row.reason) || EM_DASH) +
        '</td>' +
        '<td class="sop-signals-by">' +
        esc(nonEmptyString(row.created_by_name) || EM_DASH) +
        '</td>' +
        '</tr>'
      );
    }
    var more =
      sorted.length >= SIGNALS_LIMIT
        ? '<p class="sop-signals-more" data-testid="sop-signals-more">' +
          esc(SIGNALS_MORE) + '</p>'
        : '';
    return (
      '<div class="sop-signals-region" role="region" aria-label="' +
      esc(SIGNALS_TITLE) + '">' +
      '<table class="sop-signals-table" data-testid="sop-signals-table">' +
      '<thead><tr>' +
      '<th scope="col">Item</th>' +
      '<th scope="col">Site</th>' +
      '<th scope="col">Kind</th>' +
      '<th scope="col">From</th>' +
      '<th scope="col">To</th>' +
      '<th scope="col">Units</th>' +
      '<th scope="col">Reason</th>' +
      '<th scope="col">Added by</th>' +
      '</tr></thead>' +
      '<tbody>' + trs.join('') + '</tbody>' +
      '</table>' +
      '</div>' +
      more
    );
  }

  // The Scenarios section: directly above the Sign-offs section, for
  // every signed-in user, whether or not the review has loaded. The
  // button's label stands for the module state (an answer that has
  // landed reads "Refresh the comparison"), the pending flag
  // disables it, and the answer, the error and the label survive a
  // re-render — a review refresh or a horizon change re-renders the
  // section as it was.
  function scenariosHtml() {
    var label;
    if (state.scenariosPending) {
      label = SCENARIOS_BUSY;
    } else if (state.scenariosLoaded) {
      label = SCENARIOS_REFRESH;
    } else {
      label = SCENARIOS_SHOW;
    }
    var disabled = state.scenariosPending ? ' disabled' : '';
    var error = state.scenariosError
      ? '<p class="sop-scenarios-error" data-testid="sop-scenarios-error">' +
        esc(SCENARIOS_ERROR) + '</p>'
      : '';
    var content = state.scenariosAnswer ? scenariosAnswerHtml(state.scenariosAnswer) : '';
    return (
      '<section class="sop-card sop-scenarios" data-testid="sop-scenarios" aria-label="' +
      esc(SCENARIOS_TITLE) + '">' +
      '<div class="sop-scenarios-head">' +
      '<h2 class="sop-card-title">' + esc(SCENARIOS_TITLE) + '</h2>' +
      '<button type="button" class="btn btn-secondary sop-scenarios-show" data-testid="sop-scenarios-show"' +
      disabled + '>' +
      esc(label) + '</button>' +
      '</div>' +
      error + content +
      '</section>'
    );
  }

  // The answer: the heading, which horizon it covers, the table —
  // the Plan row from the baseline, then one row per scenario in the
  // answer's order — and, when scenarios_total is above the number of
  // rows, the "more than shown" note. With no scenarios the empty
  // note takes the table's place and the Plan row is not shown.
  function scenariosAnswerHtml(answer) {
    var horizon = asNumber(answer.horizon_months);
    var heading =
      'Over ' +
      (isFinite(horizon) ? String(Math.round(horizon)) : EM_DASH) +
      ' months, compared with the plan';
    var headingHtml =
      '<p class="sop-scenarios-heading" data-testid="sop-scenarios-heading">' +
      esc(heading) + '</p>';
    var scenarios = Array.isArray(answer.scenarios) ? answer.scenarios : [];
    if (scenarios.length === 0) {
      return (
        headingHtml +
        '<p class="sop-scenarios-empty" data-testid="sop-scenarios-empty">No saved scenarios yet. Save one on the <a class="sop-scenarios-link" href="#/scenario">Scenario screen</a>.</p>'
      );
    }
    var rows = [planRowHtml(answer.baseline)];
    for (var i = 0; i < scenarios.length; i++) {
      rows.push(scenarioRowHtml(scenarios[i]));
    }
    var total = asNumber(answer.scenarios_total);
    var more =
      isFinite(total) && total > scenarios.length
        ? '<p class="sop-scenarios-more" data-testid="sop-scenarios-more">Showing the newest ' +
          scenarios.length + ' of ' + Math.round(total) + ' saved scenarios.</p>'
        : '';
    return (
      headingHtml +
      '<div class="sop-scenarios-region" role="region" aria-label="' +
      esc(SCENARIOS_TITLE) + ' comparison">' +
      '<table class="sop-scenarios-table" data-testid="sop-scenarios-table">' +
      '<thead><tr>' +
      '<th scope="col">Scenario</th>' +
      '<th scope="col">Demand value</th>' +
      '<th scope="col">Change</th>' +
      '<th scope="col">Total spend</th>' +
      '<th scope="col">Change</th>' +
      '<th scope="col">Months over budget</th>' +
      '<th scope="col">Months short with plan</th>' +
      '</tr></thead>' +
      '<tbody>' + rows.join('') + '</tbody>' +
      '</table>' +
      '</div>' +
      more
    );
  }

  // The Plan row: the baseline's figures, whole dollars; its change
  // cells read an em dash — the plan is what the scenarios compare
  // against, not the other way round.
  function planRowHtml(baseline) {
    var b = baseline && typeof baseline === 'object' ? baseline : {};
    return (
      '<tr class="sop-scenarios-plan">' +
      '<td class="sop-scenarios-name">' + esc('Plan') + '</td>' +
      '<td class="sop-scenarios-money">' + esc(wholeDollars(b.demand_value)) + '</td>' +
      '<td class="sop-scenarios-money">' + esc(EM_DASH) + '</td>' +
      '<td class="sop-scenarios-money">' + esc(wholeDollars(b.total_spend)) + '</td>' +
      '<td class="sop-scenarios-money">' + esc(EM_DASH) + '</td>' +
      '<td class="sop-scenarios-num">' + esc(wholeNumber(b.months_over_budget)) + '</td>' +
      '<td class="sop-scenarios-num">' + esc(wholeNumber(b.months_short_planned)) + '</td>' +
      '</tr>'
    );
  }

  // One scenario's levers that are set — a lever is set when it
  // carries a usable number: "Service level 95.0%", "Demand × 1.50",
  // "Lead time × 1.20"; without a lever set, "Same as the plan".
  function leversText(scenario) {
    var parts = [];
    if (isUsable(scenario.service_level)) {
      parts.push('Service level ' + (asNumber(scenario.service_level) * 100).toFixed(1) + '%');
    }
    if (isUsable(scenario.demand_multiplier)) {
      parts.push('Demand \u00d7 ' + asNumber(scenario.demand_multiplier).toFixed(2));
    }
    if (isUsable(scenario.lead_time_multiplier)) {
      parts.push('Lead time \u00d7 ' + asNumber(scenario.lead_time_multiplier).toFixed(2));
    }
    return parts.length > 0 ? parts.join(' ' + MIDDLE_DOT + ' ') : SCENARIOS_SAME_AS_PLAN;
  }

  // One row per scenario, in the answer's order: the name with its
  // levers below it, the figures in whole dollars and the deltas
  // signed — or, with an error set, the name and "Could not be
  // compared: <error>" across the figure cells. A malformed row
  // renders as an unknown scenario and nothing throws.
  function scenarioRowHtml(scenario) {
    var s = scenario && typeof scenario === 'object' ? scenario : {};
    var name = nonEmptyString(s.name) || EM_DASH;
    var nameCell =
      '<td class="sop-scenarios-name">' +
      esc(name) +
      '<div class="sop-scenarios-levers" data-testid="sop-scenarios-levers">' +
      esc(leversText(s)) +
      '</div>' +
      '</td>';
    if (s.error !== null && s.error !== undefined) {
      var errorText = typeof s.error === 'string' ? s.error : String(s.error);
      return (
        '<tr class="sop-scenarios-bad">' +
        nameCell +
        '<td class="sop-scenarios-bad-cell" colspan="6">' +
        esc('Could not be compared: ' + errorText) +
        '</td>' +
        '</tr>'
      );
    }
    return (
      '<tr>' +
      nameCell +
      '<td class="sop-scenarios-money">' + esc(wholeDollars(s.demand_value)) + '</td>' +
      '<td class="sop-scenarios-money">' + esc(deltaDollars(s.demand_value_delta)) + '</td>' +
      '<td class="sop-scenarios-money">' + esc(wholeDollars(s.total_spend)) + '</td>' +
      '<td class="sop-scenarios-money">' + esc(deltaDollars(s.total_spend_delta)) + '</td>' +
      '<td class="sop-scenarios-num">' + esc(wholeNumber(s.months_over_budget)) + '</td>' +
      '<td class="sop-scenarios-num">' + esc(wholeNumber(s.months_short_planned)) + '</td>' +
      '</tr>'
    );
  }

  function contentHtml() {
    var data = state.data;
    return (
      statusHtml(data) +
      forecastHtml(data, undefined, undefined, forecastSignalsHtml()) +
      sinceHtml(data) +
      tableHtml(data) +
      noCostHtml(data) +
      linksHtml()
    );
  }

  function skeletonHtml() {
    return (
      '<div class="sop-skel" data-testid="sop-skeleton" aria-hidden="true">' +
      '<div class="skel skel-line"></div>' +
      '<div class="skel skel-line skel-wide"></div>' +
      '<div class="skel sop-skel-block"></div>' +
      '</div>'
    );
  }

  function errorHtml() {
    return (
      '<div class="sop-error" role="alert">' +
      '<p class="sop-error-msg" data-testid="sop-error">' +
      esc(state.error.message) + '</p>' +
      '</div>'
    );
  }

  // The open control: an admin (the role read succeeded) with a
  // loaded answer only — the button's month comes from that answer,
  // and it reads "again" once the month carries a sign-off. While
  // the panel is open the control is hidden — it returns when the
  // panel closes, by Cancel or after a sign-off — and nothing else
  // about it changes.
  function signoffOpenHtml(data) {
    if (isAdmin !== true || state.signoffAsk) {
      return '';
    }
    var month = displayValue(data.review_month) || EM_DASH;
    var signed = data.latest_signoff && typeof data.latest_signoff === 'object';
    return (
      '<button type="button" class="btn btn-secondary sop-signoff-open" data-testid="sop-signoff-open">' +
      esc('Sign off ' + month + (signed ? ' again' : '')) +
      '</button>'
    );
  }

  // The sign-off panel: rendered from module state between the bar
  // and the body, so a review refresh or a horizon change re-renders
  // it as it was — the note comes back as a value (set in render),
  // never as markup. While a request is out the confirm button is
  // disabled, and so is Cancel.
  function signoffPanelHtml() {
    if (!state.signoffAsk) {
      return '';
    }
    var month =
      state.data && displayValue(state.data.review_month)
        ? displayValue(state.data.review_month)
        : EM_DASH;
    var sentence =
      'This records that ' +
      month +
      " was reviewed and stores today's figures. It changes nothing in the plan or the ERP.";
    var errorLine = state.signoffError
      ? '<p class="sop-signoff-error" data-testid="sop-signoff-error">' +
        esc(state.signoffError) + '</p>'
      : '';
    var disabled = state.signoffPending ? ' disabled' : '';
    return (
      '<section class="sop-card sop-signoff-panel" data-testid="sop-signoff-panel" aria-label="Sign off the review month">' +
      '<p class="sop-signoff-sentence">' + esc(sentence) + '</p>' +
      '<label class="sop-signoff-label" for="sop-signoff-text">Note (optional)</label>' +
      '<textarea class="sop-signoff-text" id="sop-signoff-text" data-testid="sop-signoff-text" rows="3" maxlength="500"></textarea>' +
      errorLine +
      '<div class="sop-signoff-actions">' +
      '<button type="button" class="btn btn-primary sop-signoff-confirm" data-testid="sop-signoff-confirm"' + disabled + '>Confirm sign-off</button>' +
      '<button type="button" class="btn btn-secondary sop-signoff-cancel" data-testid="sop-signoff-cancel"' + disabled + '>Cancel</button>' +
      '</div>' +
      '</section>'
    );
  }

  /* ------------------------------ events ------------------------------- */

  function onRootClick(event) {
    var target = event.target;
    if (!target || typeof target.closest !== 'function') {
      return;
    }
    if (target.closest('[data-testid="sop-refresh"]')) {
      load();
      return;
    }
    if (target.closest('[data-testid="sop-signoff-open"]')) {
      openSignoffPanel();
      return;
    }
    if (target.closest('[data-testid="sop-signoff-cancel"]')) {
      cancelSignoff();
      return;
    }
    if (target.closest('[data-testid="sop-signoff-confirm"]')) {
      confirmSignoff();
      return;
    }
    if (target.closest('[data-testid="sop-history-show"]')) {
      loadSignoffs();
      return;
    }
    if (target.closest('[data-testid="sop-signals-show"]')) {
      loadSignals();
      return;
    }
    if (target.closest('[data-testid="sop-scenarios-show"]')) {
      loadScenarios();
      return;
    }
    var viewButton = target.closest('[data-testid="sop-history-view"]');
    if (viewButton) {
      // The button names its row by its index in the answer.
      var index = asNumber(viewButton.getAttribute('data-sop-history-index'));
      var picked =
        isFinite(index) && state.historyRows
          ? state.historyRows[Math.round(index)]
          : null;
      viewFigures(picked);
      return;
    }
    if (target.closest('[data-testid="sop-history-close"]')) {
      closeFigures();
    }
  }

  // The note is tracked per keystroke (input, not just change) so a
  // re-render between keystroke and blur cannot lose it; it updates
  // module state only — no re-render, the field keeps its focus.
  function onRootInput(event) {
    var target = event.target;
    if (!target || typeof target.getAttribute !== 'function') {
      return;
    }
    if (target.getAttribute('data-testid') !== 'sop-signoff-text') {
      return;
    }
    state.signoffNote = target.value;
  }

  function onRootChange(event) {
    var target = event.target;
    if (!target || typeof target.getAttribute !== 'function') {
      return;
    }
    if (target.getAttribute('data-testid') !== 'sop-months') {
      return;
    }
    var months = asNumber(target.value);
    if (isFinite(months)) {
      state.months = Math.round(months);
    }
    load();
  }

  /* ------------------------------- shell ------------------------------- */

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
    startMeLoad(); // once per activation; a failed read leaves no control
    load();
  }

  function reset() {
    seq += 1; // any answer still out the door belongs to a past session
    meSeq += 1; // ... and so does any role answer
    signoffSeq += 1; // ... and so does any sign-off answer
    historySeq += 1; // ... and so does any sign-offs list answer
    figuresSeq += 1; // ... and so does any frozen-figures answer
    scenariosSeq += 1; // ... and so does any comparison answer
    signalsSeq += 1; // ... and so does any demand-signals answer
    forecastSignalsSeq += 1; // ... and so does any forecast-signals answer
    isAdmin = null;
    state.data = null;
    state.loading = false;
    state.error = null;
    state.months = DEFAULT_MONTHS;
    state.signoffAsk = false;
    state.signoffNote = '';
    state.signoffPending = false;
    state.signoffError = null;
    state.historyRows = null;
    state.historyError = null;
    state.historyLoaded = false;
    state.historyPick = null;
    state.historyFigures = null;
    state.historyFigError = null;
    state.scenariosAnswer = null;
    state.scenariosError = null;
    state.scenariosLoaded = false;
    state.scenariosPending = false;
    state.signalsRows = null;
    state.signalsError = null;
    state.signalsLoaded = false;
    state.signalsPending = false;
    state.forecastSignals = null;
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
    rootEl.addEventListener('change', onRootChange);
    rootEl.addEventListener('click', onRootClick);
    rootEl.addEventListener('input', onRootInput);
    render();

    var appEl = document.getElementById(APP_ID);
    if (!appEl) {
      return;
    }
    // Sign-in shows #app: when it does and the S&OP review is the
    // active view, the review loads then — the pre-sign-in activation
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

  global.Sop = {
    activate: activate,
    reset: reset,
  };
})(window);
