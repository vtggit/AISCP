// AIERP planner screen — the Demand view (#/demand).
//
// #/demand is the forecast review screen: one row per item-site the
// backend forecasts (GET /api/forecast?limit=1000) and, for the chosen
// row, the full picture (GET /api/forecast/by-key): the history buckets
// and the 12-bucket forecast as one chart, the method with its
// parameters and its arithmetic, and the one-bucket-ahead backtest
// score beside the baseline's.
//
//   * a filter chip bar above the list — "All" (the default) and one
//     chip per demand class present in the rows, each with its count
//     and aria-pressed; choosing one narrows the list without a
//     request;
//   * a list pane (left): a sticky column header (item, site, class,
//     method, the 12-bucket forecast total, the backtest error as a
//     percentage — an em dash when there is no backtest) above the
//     rows, which scroll under it; each row is a
//     [data-testid="demand-row"] button with data-item and data-site;
//     the chosen row is tinted and carries aria-current="true"; the
//     first row is chosen when the view opens, and when a filter chip
//     hides the chosen row the first row still listed becomes the
//     chosen one — the tint, the aria-current and the detail pane all
//     move to it; choosing a row never moves the list: the scroll
//     position of the scrolling region and the keyboard focus on the
//     chosen row survive the re-renders its detail load causes, and a
//     pressed filter chip shows its narrowed list from the top with
//     the focus staying on the chip; a row whose overridden_buckets
//     is above 0 shows a small marker
//     ([data-testid="demand-row-overridden"], the accessible text
//     "Has overrides") beside its item code; a row with none has no
//     marker;
//   * a detail pane (right, --detail-width): the item-site heading,
//     its demand class and as-of date, an SVG chart
//     (svg[data-testid="demand-chart"]) with one rect per history
//     bucket and one per forecast bucket, in date order with the
//     history first, the two series in different token colours and a
//     <title> on each bar giving its date range and quantity; a
//     forecast bucket with overridden days is a third series —
//     data-series="override" in the --color-override token colour,
//     drawn at its effective quantity, its <title> giving the
//     statistical and the effective quantity, and the legend naming
//     the third series while such a bar is shown;
//     "Forecast periods" — a table
//     ([data-testid="demand-periods"]) with one row per forecast
//     bucket ([data-testid="demand-period"], carrying
//     data-period-start): the period's dates, the statistical
//     quantity, always shown, and the effective quantity beside it —
//     an em dash for a bucket no override covers, the effective
//     quantity with "Overridden" when the override covers the whole
//     period, "N of M days overridden" for a partly covered one;
//     "Current overrides" ([data-testid="demand-overrides"]) lists
//     the stored rows whose applies is true, in period order
//     ([data-testid="override-row"], carrying data-period-start),
//     each with the period's dates, the quantity, the reason, who
//     made it (created_by_name) and when: "Loading overrides…" while
//     its request is out, "No overrides for this item-site." with
//     none, "Overrides are not available." when it failed — the rest
//     of the detail works either way;
//     admin only [rule: OVR-10] — the view asks GET /api/auth/me once
//     per view load, through app/js/api.js, and only a user whose
//     roles include "admin" sees the controls below; a failed answer
//     leaves the view read-only. Each forecast period row carries an
//     "Override" button ([data-testid="override-open"]) that opens a
//     form under its row ([data-testid="override-form"]) with a
//     quantity field ([data-testid="override-quantity"], starting at
//     the period's effective quantity, or its statistical quantity
//     when nothing is overridden) and a reason field
//     ([data-testid="override-reason"]); "Save"
//     ([data-testid="override-save"]) is disabled until the quantity
//     is a number of 0 or more and the reason is not blank, and sends
//     POST /api/forecast/overrides with item_code, site_code,
//     period_start, quantity and the trimmed reason [rule: OVR-1]
//     [rule: OVR-5] [rule: OVR-6]. One form is open at a time, and
//     "Cancel" ([data-testid="override-cancel"]) closes it with
//     nothing sent. Each listed current override whose period_end is
//     after the forecast's as_of carries a "Withdraw" button
//     ([data-testid="override-withdraw"]) that opens a form
//     ([data-testid="withdraw-form"]) with a reason field
//     ([data-testid="withdraw-reason"]); "Withdraw"
//     ([data-testid="withdraw-save"]) is disabled until the reason is
//     not blank and sends
//     POST /api/forecast/overrides/withdrawals with item_code,
//     site_code, period_start and the trimmed reason [rule: OVR-9]
//     [rule: OVR-12]; an override for a completed period has no
//     "Withdraw" button. After a save or a withdrawal the form
//     closes, the detail and the overrides load again for the same
//     item-site, the chosen list row's "Has overrides" marker
//     follows the reloaded detail, and the focus returns to the
//     button that opened the form, or to the periods table when that
//     button is gone; the list is not loaded again and keeps its
//     scroll position. A refused request shows the server's message
//     in the form ([data-testid="override-error"] or
//     [data-testid="withdraw-error"], role="alert") and the form
//     stays open with what was typed; the send button is disabled
//     while a request is out, so a double click sends one request. A
//     save or withdrawal response that arrives after another
//     item-site has been chosen changes nothing on screen.
//     "How this forecast was made" — the method, its parameters, the
//     number of history buckets used and the arithmetic string
//     ([data-testid="demand-arithmetic"]); and "Backtest" — the
//     method's mean absolute error beside the baseline's, the error
//     and the bias as percentages. When the backtest is null the
//     section reads "Not enough history to backtest. Fewer than 12
//     buckets, so the forecast is the average."; and "Realised, one
//     period ahead" — the chosen item-site's forecast total, actual
//     total, error, signed bias and number of periods compared, from
//     the realised accuracy of the stored snapshots
//     (GET /api/forecast/accuracy with bucket_index=1, the one the
//     list's "Realised error" column also reads). When that entry
//     covers overridden buckets, three facts follow "Periods
//     compared": the effective forecast's total and error, and the
//     overrides' effect — "X points better" (accent colour), "X
//     points worse" (risk colour), "no change", or an em dash for a
//     null value_added; the words stay in the text, so the colour is
//     never the only signal; and "Reorder point from past demand" —
//     for a chosen item-site whose demand class is "intermittent" or
//     "lumpy" only, the empirical block of
//     GET /api/recommendations/by-key (the row's item_code and
//     site_code, through app/js/api.js, with each detail load — the
//     selectRow and the setFilter selection paths alike). The
//     section goes after every existing section of the pane and
//     holds "Reorder point <reorder_point_units> · safety stock
//     <safety_stock_units> · service level <percent>" (the
//     service_level × 100, trailing zeros dropped), "ERP reorder
//     point now: <n>" from the answer's current settings'
//     reorder_point — "not set" when it is not a number — the
//     empirical arithmetic string, and "For review: <spans_with_demand>
//     of <spans> spans had demand. Not yet used as the
//     recommendation." Every value reaches the page escaped. The
//     request never delays or changes the rendering of any existing
//     element; an answer for a superseded item-site changes nothing
//     on screen; a non-200 answer, a network failure or a null
//     empirical renders no element and no message at all.
//   * a summary line above the list and the detail panes
//     ([data-testid="demand-realised"]): "Realised accuracy. One
//     period ahead: … error, … bias, … periods compared. All
//     horizons: …" from the two accuracy responses' overall measures
//     and compared_buckets — the error as a rounded percentage, the
//     bias signed, the counts with thousands separators, an em dash
//     for a null measure. When the all-horizons response's overall
//     covers overridden buckets, the line ends with "Overrides: N
//     periods overridden, X points better than the statistical
//     forecast." — "worse" for a negative value_added, "no change"
//     for 0 — with N thousands-separated and X the |value_added| x
//     100 rounded to one decimal, the "better"/"worse" tail coloured
//     by the accent/risk token; a response without the fields (or
//     with no overridden period or a null value_added) reads exactly
//     as before. "Realised accuracy: loading…" while the two
//     accuracy requests are in flight, "Realised accuracy: no
//     forecast period has completed yet." when the one-period-ahead
//     response has no compared buckets, and "Realised accuracy is not
//     available." when either request fails — the list column shows
//     an em dash and the detail section the same sentence, and the
//     rest of the view keeps working.
//     Directly below it, the demand signals line
//     ([data-testid="demand-realised-signals"]) reads the overall
//     measures of GET /api/forecast/accuracy/signals: "Demand
//     signals: N periods with a signal, X% error without them, Y%
//     with them, Z points better." — "Z points worse" for a negative
//     value_added, "no change" for 0 — with N thousands-separated,
//     X and Y percentages to one decimal and Z the |value_added| x
//     100 to one decimal, the "better"/"worse" wording carrying the
//     overrides clause' accent/risk token classes. The line is
//     absent while its request is in flight, with no compared
//     bucket, with a null or non-numeric value_added or a non-
//     numeric wape, and reads "Demand signal accuracy is not
//     available." when its request fails — independently of the two
//     accuracy requests' loading and error states.
//
// The accuracy requests (the two realised ones and the signals one)
// go out with the list load (and with the list's "Try again" only):
// choosing a row or a filter chip sends none of them again, the list
// and the detail pane never wait for them, and a response that
// arrives after a newer load has started is ignored.
//
// The chosen item-site's stored overrides (GET /api/forecast/overrides
// with the row's item_code and site_code, limit 200) go out with each
// detail load, through app/js/api.js: the detail never waits for the
// response, a response that arrives after another item-site has been
// chosen changes nothing, and a failed request leaves only the
// "Current overrides" section unavailable.
//
// The "Demand signals" section sits after "Current overrides" in the
// detail pane: its "Show signals" button fetches
// GET /api/demand-signals for the chosen item-site on demand only —
// opening a detail sends no signals request — and the button reads
// "Refresh signals" once an answer has landed (a success or a
// failure). The answer's signals list in a table (Kind, From, To,
// Units, Reason, Added by), "No demand signals for this item-site."
// with none, "Signals could not be loaded." on a failure; an admin
// also gets an "Add signal" form (kind, dates, units, reason —
// POST /api/demand-signals) and a per-row "Withdraw" form (POST
// /api/demand-signals/<id>/withdraw), and a save's or a withdrawal's
// success reloads the signals and the detail for the same item-site.
// Choosing another item-site clears the section back to "Show
// signals" and discards an answer for the earlier pair; sign-out
// clears it too. A forecast period whose signal quantity is above 0
// shows its effective quantity in the periods table and a "+N from
// signals" note in its coverage; a period with no signal reads
// exactly as before.
//
// Every call goes through app/js/api.js. Every string from the API is
// HTML-escaped before it reaches the page; numbers use thousands
// separators. While the list loads, skeleton rows show; with no rows a
// centred empty state does; a failed load shows the error message and
// a "Try again" button that loads again.
//
// At 1440x900 neither the document nor the view scrolls: the list pane
// and the detail pane scroll on their own. At 900px and below the
// panes stack and the page never scrolls sideways.
(function (global) {
  'use strict';

  var LIST_PATH = '/api/forecast?limit=1000';
  // The realised accuracy of the stored snapshots: the one-period-ahead
  // measures (bucket 1) and everything compared — the list's "Realised
  // error" column, the summary line and the detail section read them.
  var ACCURACY_AHEAD_PATH = '/api/forecast/accuracy?bucket_index=1&limit=1000';
  var ACCURACY_ALL_PATH = '/api/forecast/accuracy?limit=1000';
  // The realised value of the stored demand signals (issue #984):
  // the signals line under the summary reads its overall measures.
  var ACCURACY_SIGNALS_PATH = '/api/forecast/accuracy/signals';
  var EM_DASH = '\u2014';
  var REALISED_NOT_AVAILABLE = 'Realised accuracy is not available.';
  var REALISED_LOADING = 'Realised accuracy: loading\u2026';
  var REALISED_NONE_YET = 'Realised accuracy: no forecast period has completed yet.';
  var REALISED_DETAIL_NONE = 'No forecast period has completed for this item-site yet.';
  var REALISED_NO_DEMAND = 'No demand in the compared periods, so the error has no percentage.';
  // The signals line's failed state (issue #984): its request is
  // independent of the two accuracy requests' error.
  var SIGNALS_ACCURACY_NOT_AVAILABLE = 'Demand signal accuracy is not available.';
  var NOT_ENOUGH_HISTORY =
    'Not enough history to backtest. Fewer than 12 buckets, so the forecast is the average.';
  // The "Current overrides" section's states: the request in flight,
  // no stored row that applies, and the request failed.
  var OVERRIDES_LOADING = 'Loading overrides\u2026';
  var OVERRIDES_NONE = 'No overrides for this item-site.';
  var OVERRIDES_NOT_AVAILABLE = 'Overrides are not available.';
  // The "Demand signals" section's states (issue #965): the request
  // in flight, the empty answer, and the request failed.
  var SIGNALS_LOADING = 'Loading signals\u2026';
  var SIGNALS_EMPTY = 'No demand signals for this item-site.';
  var SIGNALS_NOT_AVAILABLE = 'Signals could not be loaded.';
  // The signal's refusal the add/withdraw forms show: the 403 reads
  // the admin sentence, any other failure the generic one.
  var SIGNAL_NOT_ADMIN = 'Only an admin can change demand signals.';
  var SIGNAL_SAVE_FAILED = 'The signal could not be saved.';
  // A hostile payload must not build an unbounded chart; the API bounds
  // both bucket lists, so this cap is belt and braces.
  var CHART_MAX_BARS = 400;

  var state = {
    loading: false,
    ready: false,
    error: null,
    rows: [], // the rows from GET /api/forecast, in API order
    filter: 'all', // 'all' or one demand class value
    selected: null, // { item, site }
    detail: null, // the by-key response for the chosen row
    detailFor: null, // { item, site } the detail pane shows
    detailLoading: false,
    detailError: null,
    overrides: null, // the stored override rows for the chosen item-site
    overridesLoading: false, // its request still in flight
    overridesError: false, // its request failed
    empirical: null, // the by-key recommendation answer for empiricalFor
    empiricalFor: null, // { item, site } that answer belongs to
    scrollToTop: false, // the next render shows the list from its top
    accuracyAhead: null, // the bucket_index=1 accuracy response
    accuracyAll: null, // the all-horizons accuracy response
    accuracyLoading: false, // either accuracy request still in flight
    accuracyError: false, // either accuracy request failed
    accuracySignals: null, // the /api/forecast/accuracy/signals response
    accuracySignalsError: false, // that request failed — its own
    // flag; the signals line never sets or clears the shared
    // accuracyLoading/accuracyError
    admin: null, // [rule: OVR-10] null while the view's /me is in flight
    openOverride: null, // the period_start of the open override form
    overrideDraft: null, // { quantity, reason } typed in that form
    overrideError: null, // the server's refusal shown in that form
    sendingOverride: false, // its save request is out
    openWithdraw: null, // the stored row id of the open withdrawal form
    withdrawDraft: null, // { reason } typed in that form
    withdrawError: null, // the server's refusal shown in that form
    sendingWithdraw: false, // its withdrawal request is out
    // Demand signals (issue #965): fetched on demand only — the list
    // for the chosen item-site, its load, the button's "an answer has
    // landed" flag, the open add/withdraw forms with their drafts and
    // refusals, and the send flags. All of it survives re-renders and
    // is cleared by sign-out.
    signals: null, // the signals list for the chosen item-site
    signalsLoading: false, // its request still in flight
    signalsError: null, // set when the request failed
    signalsTried: false, // an answer (success or failure) has landed
    signalFormOpen: false, // the "Add signal" form is open
    signalDraft: null, // { kind, start, end, quantity, reason } typed in it
    signalError: null, // the server's refusal shown in that form
    sendingSignal: false, // its save request is out
    openSignalWithdraw: null, // the signal id its withdrawal form sits under
    signalWithdrawDraft: null, // { reason } typed in that form
    signalWithdrawError: null, // the server's refusal shown in that form
    sendingSignalWithdraw: false, // its withdrawal request is out
    pendingFocus: null, // where the focus returns once the reloaded detail shows
  };
  var listSeq = 0;
  var detailSeq = 0;
  var overridesSeq = 0; // an overrides response after a newer detail load is ignored
  var signalsSeq = 0; // a signals answer after a newer choice is ignored
  var empiricalSeq = 0; // an empirical answer after a newer detail load is ignored
  var accuracySeq = 0; // a response after a newer load is ignored
  var meSeq = 0; // the view's /me answer after a newer view load is ignored
  var writeSeq = 0; // a save/withdrawal answer after a newer write or a
  // new choice is stale and changes nothing on screen
  var rootEl = null;

  // The one-period-ahead item_sites, keyed by the text form of the
  // item-site code pair (a NUL separates the two codes, so no pair of
  // codes can collide); rebuilt only when the response object changes.
  var aheadMap = {};
  var aheadMapSource = null;

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

  // A number whose x 100 stays finite: the signals line and the
  // overrides clause read their values scaled by 100 (percentages and
  // points), and a hostile 1e308 overflows that scale to Infinity —
  // a non-number for the formatters, not a real measure.
  function isScaledNumber(value) {
    return isNumber(value) && isFinite(value * 100);
  }

  // Item-site codes and demand classes may arrive from the API as
  // numbers, but the same values travel through the page as DOM
  // attribute strings. Compare them through their text form so a
  // numeric 42 and the attribute "42" are the same code; null is never
  // a code.
  function sameCode(left, right) {
    if (left == null || right == null) {
      return false;
    }
    return String(left) === String(right);
  }

  // Whole units with thousands separators (10460.7 -> "10,461");
  // null/undefined renders as an em dash.
  function units(value) {
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return Math.round(value).toLocaleString('en-US');
  }

  // The single boundary every quantity the view displays passes
  // through: thousands separators, at most 2 decimal places, trailing
  // zeros dropped (957296.582121957 -> "957,296.58",
  // 82468.22544865588 -> "82,468.23", 472 -> "472", 0.06 -> "0.06").
  // null/undefined renders as an em dash. Values that the view only
  // carries, not displays — the override form's starting value and
  // the quantity the save request sends — never pass through it.
  var QUANTITY_FORMATTER = new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 2,
  });

  function quantityText(value) {
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return QUANTITY_FORMATTER.format(value);
  }

  // Two-decimal figures with thousands separators (MAE values).
  function figure2(value) {
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return value.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  // A fraction as a rounded percentage: 0.25 -> "25%".
  function percent(value) {
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return Math.round(value * 100).toLocaleString('en-US') + '%';
  }

  // A fraction as a percentage with exactly one decimal place:
  // 0.2 -> "20.0%", 0.04 -> "4.0%", 0 -> "0.0%".
  // null/undefined and any non-number (or one whose x 100 overflows
  // to Infinity) render an em dash.
  var PERCENT1_FORMATTER = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });

  function percent1(value) {
    if (!isScaledNumber(value)) {
      return EM_DASH;
    }
    return PERCENT1_FORMATTER.format(value * 100) + '%';
  }

  // A fraction as a signed rounded percentage: 0.06 -> "+6%",
  // -0.04 -> "-4%", 0 -> "0%".
  function signedPercent(value) {
    if (!isNumber(value)) {
      return EM_DASH;
    }
    var rounded = Math.round(value * 100);
    return (rounded > 0 ? '+' : '') + rounded.toLocaleString('en-US') + '%';
  }

  // A service level as a percent with trailing zeros dropped:
  // 0.95 -> "95%", 0.975 -> "97.5%" (the level × 100, up to two
  // decimals — a level is a fraction, never whole units).
  // null/undefined renders an em dash.
  function serviceLevelText(value) {
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return (value * 100).toLocaleString('en-US', { maximumFractionDigits: 2 }) + '%';
  }

  // The overrides' effect size: |value_added| x 100 with exactly one
  // decimal place ("4.2", "1.5", "3.0") and thousands separators —
  // null/undefined and any non-number (or one whose x 100 overflows
  // to Infinity) read as an em dash.
  var POINTS_FORMATTER = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });

  function pointsText(value) {
    if (!isScaledNumber(value)) {
      return EM_DASH;
    }
    return POINTS_FORMATTER.format(Math.abs(value) * 100);
  }

  function aheadKey(item, site) {
    return String(item) + '\u0000' + String(site);
  }

  function aheadMapFor() {
    if (aheadMapSource !== state.accuracyAhead) {
      aheadMapSource = state.accuracyAhead;
      aheadMap = {};
      var ahead = state.accuracyAhead;
      var sites = ahead && Array.isArray(ahead.item_sites) ? ahead.item_sites : [];
      for (var i = 0; i < sites.length; i++) {
        var entry = sites[i];
        if (
          !entry ||
          typeof entry !== 'object' ||
          entry.item_code == null ||
          entry.site_code == null
        ) {
          continue;
        }
        aheadMap[aheadKey(entry.item_code, entry.site_code)] = entry;
      }
    }
    return aheadMap;
  }

  // The one-period-ahead entry for an item-site, or null when the
  // response has not landed or does not cover it.
  function aheadEntry(item, site) {
    if (item == null || site == null) {
      return null;
    }
    return aheadMapFor()[aheadKey(item, site)] || null;
  }

  // "smooth" -> "Smooth" (the chips and the row's class column).
  function classLabel(value) {
    var text = value == null ? '' : String(value);
    if (!text) {
      return EM_DASH;
    }
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  /* ---------------------- admin overrides [rule: OVR-10] -------------- */

  // Whether the reloaded detail carries any overridden forecast bucket —
  // the chosen row's "Has overrides" marker follows this, because the
  // list itself is not loaded again after a save or a withdrawal.
  function detailHasOverrides(detail) {
    var buckets = asRows(detail.forecast);
    for (var i = 0; i < buckets.length; i++) {
      if (isBucket(buckets[i]) && overrideDays(buckets[i]) > 0) {
        return true;
      }
    }
    return false;
  }

  // The quantity the override form opens with: the period's effective
  // quantity when an override covers it, its statistical quantity when
  // nothing is overridden, '' when neither is a number.
  function initialQuantityFor(bucket) {
    var value = null;
    if (overrideDays(bucket) > 0 && isNumber(bucket.effective_quantity)) {
      value = bucket.effective_quantity;
    } else if (isNumber(bucket.quantity)) {
      value = bucket.quantity;
    }
    return value === null ? '' : String(value);
  }

  function bucketForPeriod(period) {
    if (!state.detail || period == null) {
      return null;
    }
    var buckets = asRows(state.detail.forecast);
    for (var i = 0; i < buckets.length; i++) {
      if (isBucket(buckets[i]) && String(buckets[i].bucket_start) === String(period)) {
        return buckets[i];
      }
    }
    return null;
  }

  // A decimal number of 0 or more, and nothing else: empty, blank,
  // negative, non-numeric, exponential junk or control characters all
  // refuse [rule: OVR-6]; zero is allowed [rule: OVR-6].
  var QUANTITY_PATTERN = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

  function parseQuantity(text) {
    var trimmed = String(text == null ? '' : text).trim();
    if (!QUANTITY_PATTERN.test(trimmed)) {
      return null;
    }
    var num = Number(trimmed);
    if (!isFinite(num) || num < 0) {
      return null;
    }
    return num;
  }

  function overrideDraftValid(draft) {
    return (
      !!draft &&
      parseQuantity(draft.quantity) !== null &&
      String(draft.reason == null ? '' : draft.reason).trim() !== ''
    );
  }

  function withdrawDraftValid(draft) {
    return !!draft && String(draft.reason == null ? '' : draft.reason).trim() !== '';
  }

  // The send button is disabled while its request is out (a double
  // click sends one request) and while the draft would be refused
  // [rule: OVR-5] [rule: OVR-6].
  function overrideSendDisabled() {
    return state.sendingOverride || !overrideDraftValid(state.overrideDraft);
  }

  function withdrawSendDisabled() {
    return state.sendingWithdraw || !withdrawDraftValid(state.withdrawDraft);
  }

  function asRows(value) {
    return Array.isArray(value) ? value : [];
  }

  // One row per item-site; non-object entries are dropped.
  function asForecastRows(value) {
    var rows = asRows(value);
    return rows.filter(function (row) {
      return !!row && typeof row === 'object' && row.item_code != null && row.site_code != null;
    });
  }

  function visibleRows() {
    if (state.filter === 'all') {
      return state.rows;
    }
    return state.rows.filter(function (row) {
      return sameCode(row.demand_class, state.filter);
    });
  }

  // One entry per demand class present in the rows, in first-seen
  // order. seen is null-prototype: a demand class that is an
  // inherited property name ("constructor", "toString", "__proto__")
  // still gets its chip, and its count is never written onto the
  // shared global it would otherwise inherit.
  function classCounts() {
    var counts = [];
    var seen = Object.create(null);
    for (var i = 0; i < state.rows.length; i++) {
      var key = state.rows[i].demand_class == null ? '' : String(state.rows[i].demand_class);
      if (!key) {
        continue;
      }
      if (!seen[key]) {
        seen[key] = { value: key, count: 0 };
        counts.push(seen[key]);
      }
      seen[key].count += 1;
    }
    return counts;
  }

  function selectedRow() {
    if (!state.selected) {
      return null;
    }
    for (var i = 0; i < state.rows.length; i++) {
      if (
        sameCode(state.rows[i].item_code, state.selected.item) &&
        sameCode(state.rows[i].site_code, state.selected.site)
      ) {
        return state.rows[i];
      }
    }
    return null;
  }

  function isBucket(bucket) {
    return (
      !!bucket &&
      typeof bucket === 'object' &&
      typeof bucket.bucket_start === 'string' &&
      typeof bucket.bucket_end === 'string'
    );
  }

  // "alpha = 0.3, beta = 0.1" from the method's params; an em dash when
  // the method takes none.
  function paramsText(params) {
    if (!params || typeof params !== 'object' || Array.isArray(params)) {
      return EM_DASH;
    }
    var parts = [];
    for (var key in params) {
      if (Object.prototype.hasOwnProperty.call(params, key) && parts.length < 8) {
        var value = params[key];
        var text;
        if (typeof value === 'number' && isFinite(value)) {
          text = value.toLocaleString('en-US', { maximumFractionDigits: 4 });
        } else if (value == null) {
          text = '';
        } else {
          text = String(value);
        }
        parts.push(esc(String(key)) + ' = ' + esc(text));
      }
    }
    return parts.length ? parts.join(', ') : EM_DASH;
  }

  /* ------------------------------ rendering ---------------------------- */

  function demandActive() {
    var active = document.querySelector('.view.is-active');
    return !!active && active.getAttribute('data-view') === 'demand';
  }

  // A re-render rebuilds the whole view, but two things must survive
  // it: the list's scroll position and the keyboard focus. Choosing a
  // row re-renders twice (once on the choice, again when the detail
  // lands or fails), and a pressed chip re-renders too; in none of
  // those moments may the scrolled list jump or the focus leave the
  // control the user is on. The one deliberate move is a chip's
  // narrowed list, which opens from its top.
  function render() {
    if (!rootEl) {
      return;
    }
    var focusKey = captureFocus();
    var oldBody = rootEl.querySelector('.d-list-body');
    var nextScrollTop = state.scrollToTop ? 0 : oldBody ? oldBody.scrollTop : 0;
    state.scrollToTop = false;
    if (state.loading) {
      rootEl.innerHTML = renderSkeleton();
    } else if (state.error) {
      rootEl.innerHTML = renderError();
    } else if (!state.rows.length) {
      rootEl.innerHTML = renderEmpty();
    } else {
      rootEl.innerHTML = renderMain();
    }
    var body = rootEl.querySelector('.d-list-body');
    if (body) {
      body.scrollTop = nextScrollTop;
    }
    restoreFocus(focusKey);
    restorePendingFocus();
  }

  // The control the user is on, by identity that survives a rebuild:
  // a row keeps its item/site pair, a chip its filter. Null when the
  // focus is on neither (a retry button, the document, ...).
  function captureFocus() {
    var active = document.activeElement;
    if (!active || !rootEl || !rootEl.contains(active) || !active.getAttribute) {
      return null;
    }
    if (active.classList && active.classList.contains('d-row')) {
      return {
        kind: 'row',
        item: active.getAttribute('data-item'),
        site: active.getAttribute('data-site'),
      };
    }
    if (active.classList && active.classList.contains('d-chip')) {
      return { kind: 'chip', filter: active.getAttribute('data-filter') };
    }
    // The admin override and withdrawal controls: a button or a draft
    // field keeps its data-testid across a rebuild, so the focus can
    // follow it. The "Override" and "Withdraw" buttons repeat across
    // the period rows and the stored rows, so the focus follows the
    // one button, not the first of the testid: the key also carries
    // the button's row identity (its own data-period-start or
    // data-override-id).
    var formTestid = active.getAttribute('data-testid');
    if (formTestid && /^(override|withdraw)-/.test(formTestid)) {
      var formKeyAttr =
        formTestid === 'override-open'
          ? 'data-period-start'
          : formTestid === 'override-withdraw'
            ? 'data-override-id'
            : null;
      return {
        kind: 'form',
        testid: formTestid,
        keyAttr: formKeyAttr,
        key: formKeyAttr ? active.getAttribute(formKeyAttr) : null,
      };
    }
    if (formTestid && formTestid.indexOf('demand-signal') === 0) {
      // The add form's controls are unique; the withdrawal controls
      // repeat per signal row, so the key is the row's signal id.
      var signalKeyHost = active.closest('[data-signal-id]');
      return {
        kind: 'signal-form',
        testid: formTestid,
        key: signalKeyHost ? signalKeyHost.getAttribute('data-signal-id') : null,
      };
    }
    return null;
  }

  // After a rebuild, put the focus back where the user had it.
  // preventScroll keeps the restored scroll position intact.
  function restoreFocus(focusKey) {
    if (!focusKey || !rootEl) {
      return;
    }
    var nodes = rootEl.querySelectorAll(focusKey.kind === 'row' ? '.d-row' : '.d-chip');
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var matches =
        focusKey.kind === 'row'
          ? node.getAttribute('data-item') === focusKey.item &&
            node.getAttribute('data-site') === focusKey.site
          : node.getAttribute('data-filter') === focusKey.filter;
      if (matches) {
        node.focus({ preventScroll: true });
        return;
      }
    }
    if (focusKey.kind === 'form') {
      var formNodes = rootEl.querySelectorAll('[data-testid]');
      for (var j = 0; j < formNodes.length; j++) {
        var formNode = formNodes[j];
        if (formNode.getAttribute('data-testid') !== focusKey.testid) {
          continue;
        }
        if (focusKey.keyAttr && formNode.getAttribute(focusKey.keyAttr) !== focusKey.key) {
          // A repeated testid: the control the focus was on lived in
          // one row only. The same testid on another row is not that
          // control, and a row that is gone leaves the focus where the
          // rebuild left it.
          continue;
        }
        formNode.focus({ preventScroll: true });
        return;
      }
    }
    if (focusKey.kind === 'signal-form') {
      var signalNodes = rootEl.querySelectorAll('[data-testid]');
      for (var k = 0; k < signalNodes.length; k++) {
        var signalNode = signalNodes[k];
        if (signalNode.getAttribute('data-testid') !== focusKey.testid) {
          continue;
        }
        if (focusKey.key !== null) {
          var signalHost = signalNode.closest('[data-signal-id]');
          if (!signalHost || signalHost.getAttribute('data-signal-id') !== focusKey.key) {
            // A repeated testid: the control the focus was on lived
            // in one row only.
            continue;
          }
        }
        signalNode.focus({ preventScroll: true });
        return;
      }
    }
  }

  // After a save or a withdrawal the detail and the overrides load
  // again; once the reloaded detail is on screen the focus returns to
  // the button that opened the form, or to the periods table when
  // that button is gone (a withdrawn row is not listed any more).
  function restorePendingFocus() {
    var wanted = state.pendingFocus;
    if (!wanted || !rootEl) {
      return;
    }
    if (
      state.detailFor &&
      (!sameCode(state.detailFor.item, wanted.item) || !sameCode(state.detailFor.site, wanted.site))
    ) {
      // The choice moved on in the meantime: the pending focus is
      // stale and is dropped.
      state.pendingFocus = null;
      return;
    }
    if (!state.detail) {
      return; // the reloaded detail is not on screen yet; keep waiting
    }
    if (wanted.kind === 'override-button') {
      var button = findPeriodOverrideButton(wanted.key);
      if (button) {
        button.focus({ preventScroll: true });
        state.pendingFocus = null;
      }
      return;
    }
    var withdrawButton = findWithdrawButton(wanted.key);
    if (withdrawButton) {
      withdrawButton.focus({ preventScroll: true });
      state.pendingFocus = null;
      return;
    }
    // The row's button is gone; wait for the reloaded overrides to
    // settle, then land the focus on the periods table.
    if (!state.overridesLoading) {
      var table = rootEl.querySelector('[data-testid="demand-periods"]');
      if (table) {
        table.focus({ preventScroll: true });
        state.pendingFocus = null;
      }
    }
  }

  // The "Override" button of one period row, by its data-period-start.
  // The rows are walked by attribute, so a hostile date value can
  // never break a selector.
  function findPeriodOverrideButton(period) {
    var rows = rootEl.querySelectorAll('[data-testid="demand-period"]');
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].getAttribute('data-period-start') === String(period)) {
        return rows[i].querySelector('[data-testid="override-open"]');
      }
    }
    return null;
  }

  // The "Withdraw" button of one stored row, by its id.
  function findWithdrawButton(id) {
    var rows = rootEl.querySelectorAll('[data-testid="override-row"]');
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].getAttribute('data-override-id') === String(id)) {
        return rows[i].querySelector('[data-testid="override-withdraw"]');
      }
    }
    return null;
  }

  function renderSkeleton() {
    var rows = '';
    for (var i = 0; i < 6; i++) {
      rows += '<span class="skel skel-d-row" aria-hidden="true"></span>';
    }
    return (
      '<div class="d-split">' +
        '<section class="d-list" aria-label="Forecast rows">' +
          '<div class="d-list-body" aria-hidden="true">' + rows + '</div>' +
        '</section>' +
        '<aside class="d-detail" aria-label="Chosen forecast">' +
          '<div class="d-detail-inner" aria-hidden="true">' +
            '<span class="skel skel-line"></span>' +
            '<span class="skel skel-value"></span>' +
            '<span class="skel skel-figures"></span>' +
          '</div>' +
        '</aside>' +
      '</div>'
    );
  }

  function renderError() {
    return (
      '<div class="d-error" role="alert">' +
        '<p class="d-error-msg">' + esc(state.error) + '</p>' +
        '<button type="button" class="btn btn-secondary" id="demand-retry">Try again</button>' +
      '</div>'
    );
  }

  function renderEmpty() {
    return (
      '<div class="d-split">' +
        '<section class="d-list" aria-label="Forecast rows">' +
          '<div class="d-empty">' +
            '<h2>No forecasts yet</h2>' +
            '<p>Forecasts appear here once item-sites have demand history.</p>' +
          '</div>' +
        '</section>' +
        '<aside class="d-detail" aria-label="Chosen forecast">' +
          '<div class="d-detail-inner"><p class="d-detail-empty">No forecast chosen.</p></div>' +
        '</aside>' +
      '</div>'
    );
  }

  function renderMain() {
    return (
      '<div class="d-chips" role="group" aria-label="Demand class filter">' +
        renderChips() +
      '</div>' +
      '<p class="d-realised-summary" data-testid="demand-realised">' +
        renderRealisedSummary() +
      '</p>' +
      renderRealisedSignalsLine() +
      '<div class="d-split">' +
        '<section class="d-list" aria-label="Forecast rows">' +
          '<div class="d-list-body">' + renderHead() + renderRows() + '</div>' +
        '</section>' +
        '<aside class="d-detail" aria-label="Chosen forecast">' +
          renderDetail() +
        '</aside>' +
      '</div>'
    );
  }

  function renderChips() {
    var chips = [chipHtml('all', 'All', state.rows.length, state.filter === 'all')];
    classCounts().forEach(function (entry) {
      chips.push(
        chipHtml(entry.value, classLabel(entry.value), entry.count, state.filter === entry.value)
      );
    });
    return chips.join('');
  }

  function chipHtml(value, label, count, on) {
    return (
      '<button type="button" class="d-chip' + (on ? ' is-on' : '') + '" ' +
      'aria-pressed="' + (on ? 'true' : 'false') + '" data-filter="' + esc(value) + '">' +
      esc(label) +
      ' <span class="d-chip-count">' + count + '</span>' +
      '</button>'
    );
  }

  function renderHead() {
    return (
      '<div class="d-row-head">' +
        '<span class="d-h-item">Item</span>' +
        '<span class="d-h-site">Site</span>' +
        '<span class="d-h-class">Class</span>' +
        '<span class="d-h-method">Method</span>' +
        '<span class="d-h-total">12-bucket total</span>' +
        '<span class="d-h-error">Backtest error</span>' +
        '<span class="d-h-realised">Realised error</span>' +
      '</div>'
    );
  }

  function renderRows() {
    return visibleRows().map(rowHtml).join('');
  }

  function rowHtml(row) {
    var selected =
      !!state.selected &&
      sameCode(state.selected.item, row.item_code) &&
      sameCode(state.selected.site, row.site_code);
    var wape =
      row.backtest && typeof row.backtest === 'object' ? percent(row.backtest.wape) : EM_DASH;
    // The item-site's one-period-ahead wape from the accuracy response:
    // an em dash until the response lands (or it failed), and for an
    // item-site the response does not cover or whose wape is null.
    var entry = aheadEntry(row.item_code, row.site_code);
    var realised = EM_DASH;
    if (!state.accuracyError && !state.accuracyLoading && entry) {
      realised = percent(entry.wape);
    }
    // The row's marker: its overridden_buckets count from the list is
    // above 0 — the item cell then carries the "Has overrides" marker
    // beside the item code; a row with none has no marker. The chosen
    // row follows its reloaded detail instead: a save or a withdrawal
    // loads the detail again without loading the list, so the marker
    // reads the detail's overridden buckets once it is in.
    var overridden = isNumber(row.overridden_buckets) && row.overridden_buckets > 0;
    if (
      selected &&
      state.detail &&
      sameCode(state.detail.item_code, row.item_code) &&
      sameCode(state.detail.site_code, row.site_code)
    ) {
      overridden = detailHasOverrides(state.detail);
    }
    return (
      '<button type="button" class="d-row' + (selected ? ' is-selected' : '') + '" ' +
      (selected ? 'aria-current="true" ' : '') +
      'data-testid="demand-row" data-item="' + esc(row.item_code) +
      '" data-site="' + esc(row.site_code) + '">' +
        '<span class="d-item-cell">' +
          '<span class="d-item">' + esc(row.item_code) + '</span>' +
          (overridden
            ? '<span class="d-ovr-badge" role="img" aria-label="Has overrides" ' +
              'data-testid="demand-row-overridden"></span>'
            : '') +
        '</span>' +
        '<span class="d-site">' + esc(row.site_code) + '</span>' +
        '<span class="d-class">' + esc(classLabel(row.demand_class)) + '</span>' +
        '<span class="d-method">' + esc(row.method) + '</span>' +
        '<span class="d-total">' + esc(quantityText(row.total_forecast)) + '</span>' +
        '<span class="d-bt-error">' + esc(wape) + '</span>' +
        '<span class="d-realised">' + esc(realised) + '</span>' +
      '</button>'
    );
  }

  function renderDetail() {
    var row = selectedRow();
    if (!row) {
      return (
        '<div class="d-detail-inner">' +
        '<p class="d-detail-empty">Pick a row to see how its forecast was made.</p>' +
        '</div>'
      );
    }
    if (state.detailError) {
      return (
        '<div class="d-detail-inner">' +
          '<div class="d-detail-error" role="alert">' +
            '<p class="d-detail-error-msg">' + esc(state.detailError) + '</p>' +
            '<button type="button" class="btn btn-secondary" id="demand-detail-retry">Try again</button>' +
          '</div>' +
        '</div>'
      );
    }
    if (!state.detail) {
      return (
        '<div class="d-detail-inner" aria-hidden="true">' +
          '<span class="skel skel-line"></span>' +
          '<span class="skel skel-value"></span>' +
          '<span class="skel skel-figures"></span>' +
        '</div>'
      );
    }
    return renderDetailBody(state.detail);
  }

  // The bucket's overridden days, 0 when absent, non-numeric or
  // negative — the API sends an integer from 0 to the bucket's length.
  function overrideDays(bucket) {
    var days = bucket && bucket.override_days;
    if (!isNumber(days)) {
      return 0;
    }
    return Math.max(0, Math.floor(days));
  }

  // The bucket's signal quantity, 0 when absent, non-numeric or
  // negative (issue #965): a period above 0 shows its effective
  // quantity and the "+N from signals" coverage note.
  function signalQuantity(bucket) {
    var qty = bucket && bucket.signal_quantity;
    if (!isNumber(qty)) {
      return 0;
    }
    return Math.max(0, qty);
  }

  // The bucket's length in days from its dates; null when they do not
  // parse (a hostile payload).
  function periodDays(bucket) {
    var start = Date.parse(String(bucket.bucket_start));
    var end = Date.parse(String(bucket.bucket_end));
    if (isNaN(start) || isNaN(end) || end < start) {
      return null;
    }
    return Math.round((end - start) / 86400000) + 1;
  }

  // The period row's coverage note: an em dash for a bucket no
  // override covers, "Overridden" for one covered for all of its
  // days, "N of M days overridden" for a partly covered one.
  function coverageText(bucket) {
    var days = overrideDays(bucket);
    if (!days) {
      return EM_DASH;
    }
    var length = periodDays(bucket);
    if (isNumber(length) && length > 0 && days >= length) {
      return 'Overridden';
    }
    if (isNumber(length) && length > 0) {
      return days + ' of ' + length + ' days overridden';
    }
    return days + ' days overridden';
  }

  // The "Forecast periods" section: one row per forecast bucket with
  // the period's dates, the statistical quantity — always shown — and
  // the effective quantity beside it.
  function renderPeriods(detail) {
    var buckets = asRows(detail.forecast).filter(isBucket);
    var admin = state.admin === true;
    var rows = '';
    for (var i = 0; i < buckets.length && i < CHART_MAX_BARS; i++) {
      var bucket = buckets[i];
      var days = overrideDays(bucket);
      // A signal-covered period shows its effective quantity even
      // with no override day, and its coverage note gains the
      // "+N from signals" tag; a period with no signal reads exactly
      // as it does without signals.
      var signal = signalQuantity(bucket);
      rows +=
        '<tr class="d-period" data-testid="demand-period" data-period-start="' +
        esc(String(bucket.bucket_start)) +
        '">' +
          '<td class="d-p-dates">' +
            esc(String(bucket.bucket_start)) +
            ' \u2013 ' +
            esc(String(bucket.bucket_end)) +
          '</td>' +
          '<td class="d-p-stat">' + esc(quantityText(bucket.quantity)) + '</td>' +
          '<td class="d-p-eff">' +
            esc(days > 0 || signal > 0 ? quantityText(bucket.effective_quantity) : EM_DASH) +
          '</td>' +
          '<td class="d-p-cov">' +
            esc(coverageText(bucket)) +
            (signal > 0
              ? ' <span class="d-p-signal" data-testid="demand-period-signal">' +
                '+' + units(signal) + ' from signals</span>'
              : '') +
          '</td>' +
          (admin ? '<td class="d-p-act">' + overrideButtonHtml(bucket) + '</td>' : '') +
        '</tr>';
      if (admin && state.openOverride === String(bucket.bucket_start)) {
        rows += overrideFormRowHtml(bucket);
      }
    }
    return (
      '<section class="d-section" aria-label="Forecast periods">' +
        '<h3>Forecast periods</h3>' +
        // tabindex="-1": the focus lands here after a withdrawal when
        // the withdrawn row's button is gone.
        '<table class="d-periods" data-testid="demand-periods" tabindex="-1">' +
          '<thead><tr>' +
            '<th scope="col">Period</th>' +
            '<th scope="col">Statistical</th>' +
            '<th scope="col">Effective</th>' +
            '<th scope="col">Coverage</th>' +
            (admin ? '<th scope="col" class="d-h-act">Action</th>' : '') +
          '</tr></thead>' +
          '<tbody>' +
            rows +
          '</tbody>' +
        '</table>' +
      '</section>'
    );
  }

  // The period row's "Override" button (admin only): it opens the
  // override form under its row.
  function overrideButtonHtml(bucket) {
    return (
      '<button type="button" class="d-ovr-btn" data-testid="override-open" ' +
      'data-period-start="' + esc(String(bucket.bucket_start)) + '">Override</button>'
    );
  }

  // The override form, as the row under the period it overrides. One
  // form is open at a time (state.openOverride); the draft the user
  // typed survives the re-renders that keep happening while it is
  // open, and a refusal shows the server's message in the form with
  // what was typed.
  function overrideFormRowHtml(bucket) {
    var draft = state.overrideDraft || { quantity: '', reason: '' };
    return (
      '<tr class="d-ovr-form-row">' +
        '<td colspan="5">' +
          '<div class="d-ovr-form" data-testid="override-form">' +
            '<div class="d-ovr-fields">' +
              '<label class="d-ovr-field">' +
                '<span class="d-ovr-label">Quantity</span>' +
                '<input type="text" inputmode="decimal" autocomplete="off" spellcheck="false" ' +
                  'data-testid="override-quantity" value="' + esc(draft.quantity) + '">' +
              '</label>' +
              '<label class="d-ovr-field d-ovr-field-wide">' +
                '<span class="d-ovr-label">Reason</span>' +
                '<input type="text" autocomplete="off" ' +
                  'data-testid="override-reason" value="' + esc(draft.reason) + '">' +
              '</label>' +
            '</div>' +
            '<p class="d-ovr-form-error" data-testid="override-error" role="alert">' +
              (state.overrideError ? esc(state.overrideError) : '') +
            '</p>' +
            '<div class="d-ovr-actions">' +
              '<button type="button" class="btn btn-primary" data-testid="override-save"' +
                (overrideSendDisabled() ? ' disabled' : '') + '>Save</button>' +
              '<button type="button" class="btn btn-secondary" data-testid="override-cancel">Cancel</button>' +
            '</div>' +
          '</div>' +
        '</td>' +
      '</tr>'
    );
  }

  // The chosen item-site's stored rows that apply: only the rows whose
  // applies flag is true — the current override of each period,
  // neither a withdrawn one nor a superseded row — in period order.
  function appliesRows() {
    var rows = Array.isArray(state.overrides) ? state.overrides : [];
    var applies = [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (
        row &&
        typeof row === 'object' &&
        row.applies === true &&
        typeof row.period_start === 'string'
      ) {
        applies.push(row);
      }
    }
    applies.sort(function (left, right) {
      if (left.period_start < right.period_start) {
        return -1;
      }
      if (left.period_start > right.period_start) {
        return 1;
      }
      return 0;
    });
    return applies;
  }

  // A stored row's "when": the stored date-time as "YYYY-MM-DD HH:MM
  // UTC"; a value that does not parse renders as-is.
  function whenText(value) {
    if (typeof value !== 'string' && typeof value !== 'number') {
      return EM_DASH;
    }
    var parsed = new Date(value);
    if (isNaN(parsed.getTime())) {
      return String(value);
    }
    function pad(part) {
      return (part < 10 ? '0' : '') + part;
    }
    return (
      parsed.getUTCFullYear() +
      '-' +
      pad(parsed.getUTCMonth() + 1) +
      '-' +
      pad(parsed.getUTCDate()) +
      ' ' +
      pad(parsed.getUTCHours()) +
      ':' +
      pad(parsed.getUTCMinutes()) +
      ' UTC'
    );
  }

  // One listed row of "Current overrides": the period's dates and the
  // quantity, the reason, who made it and when. An admin sees a
  // "Withdraw" button on a row whose period has not completed — its
  // period_end is after the forecast's as_of [rule: OVR-12]; an
  // override for a completed period has none.
  function overrideRowHtml(row) {
    var name = row.created_by_name == null ? EM_DASH : String(row.created_by_name);
    var end = row.period_end == null ? EM_DASH : String(row.period_end);
    var reason = row.reason == null ? EM_DASH : String(row.reason);
    var asOf =
      state.detail && state.detail.as_of != null ? String(state.detail.as_of) : '';
    var withdrawable =
      state.admin === true && row.period_end != null && asOf !== '' && String(row.period_end) > asOf;
    return (
      '<div class="d-override-row" data-testid="override-row" data-period-start="' +
      esc(String(row.period_start)) +
      '" data-override-id="' + esc(row.id == null ? '' : String(row.id)) + '">' +
        '<div class="d-ovr-top">' +
          '<span class="d-ovr-dates">' +
            esc(String(row.period_start)) +
            ' \u2013 ' +
            esc(end) +
          '</span>' +
          '<span class="d-ovr-qty">' + esc(quantityText(row.quantity)) + '</span>' +
        '</div>' +
        '<p class="d-ovr-reason">' + esc(reason) + '</p>' +
        '<p class="d-ovr-who">' + esc(name) + ' \u00b7 ' + esc(whenText(row.created_at)) + '</p>' +
        (state.admin === true
          ? '<div class="d-ovr-foot">' +
              (withdrawable
                ? '<button type="button" class="d-ovr-btn" data-testid="override-withdraw" ' +
                  'data-override-id="' + esc(row.id == null ? '' : String(row.id)) + '">Withdraw</button>'
                : '') +
              (state.openWithdraw === (row.id == null ? '' : String(row.id))
                ? withdrawFormHtml()
                : '') +
            '</div>'
          : '') +
      '</div>'
    );
  }

  // The withdrawal form, opened under the stored row it withdraws.
  // Only the reason: the period and the quantity come from the stored
  // row [rule: OVR-9].
  function withdrawFormHtml() {
    var draft = state.withdrawDraft || { reason: '' };
    return (
      '<div class="d-withdraw-form" data-testid="withdraw-form">' +
        '<label class="d-ovr-field d-ovr-field-wide">' +
          '<span class="d-ovr-label">Reason</span>' +
          '<input type="text" autocomplete="off" ' +
            'data-testid="withdraw-reason" value="' + esc(draft.reason) + '">' +
        '</label>' +
        '<p class="d-ovr-form-error" data-testid="withdraw-error" role="alert">' +
          (state.withdrawError ? esc(state.withdrawError) : '') +
        '</p>' +
        '<div class="d-ovr-actions">' +
          '<button type="button" class="btn btn-primary" data-testid="withdraw-save"' +
            (withdrawSendDisabled() ? ' disabled' : '') + '>Withdraw</button>' +
          '<button type="button" class="btn btn-secondary" data-testid="withdraw-cancel">Cancel</button>' +
        '</div>' +
      '</div>'
    );
  }

  // The "Current overrides" section of the chosen item-site: "Loading
  // overrides…" while its request is out, "No overrides for this
  // item-site." with none, "Overrides are not available." when the
  // request failed, and the rows that apply in between.
  function renderOverrides() {
    var parts = [
      '<section class="d-section" aria-label="Current overrides">',
      '<h3>Current overrides</h3>',
      '<div class="d-overrides" data-testid="demand-overrides">',
    ];
    if (state.overridesError) {
      parts.push('<p class="d-overrides-note">' + esc(OVERRIDES_NOT_AVAILABLE) + '</p>');
    } else if (state.overridesLoading) {
      parts.push('<p class="d-overrides-note">' + esc(OVERRIDES_LOADING) + '</p>');
    } else {
      var rows = appliesRows();
      if (!rows.length) {
        parts.push('<p class="d-overrides-note">' + esc(OVERRIDES_NONE) + '</p>');
      } else {
        for (var i = 0; i < rows.length; i++) {
          parts.push(overrideRowHtml(rows[i]));
        }
      }
    }
    parts.push('</div></section>');
    return parts.join('');
  }

  // The "Demand signals" section of the detail pane, after "Current
  // overrides" (issue #965): the on-demand button — "Show signals"
  // until an answer has landed, "Refresh signals" after a success or
  // a failure — the admin's "Add signal" button, and the list, the
  // empty note or the error. The fetch is on demand only: opening a
  // detail sends no signals request.
  function renderSignals() {
    var admin = state.admin === true;
    var parts = [
      '<section class="d-section d-signals-section" data-testid="demand-signals" ' +
        'aria-label="Demand signals">',
      '<h3>Demand signals</h3>',
      '<div class="d-signals">',
      '<div class="d-signals-toolbar">' +
        '<button type="button" class="d-ovr-btn" data-testid="demand-signals-show">' +
          esc(state.signalsTried ? 'Refresh signals' : 'Show signals') +
        '</button>' +
        (admin
          ? '<button type="button" class="d-ovr-btn" data-testid="demand-signal-add">Add signal</button>'
          : '') +
      '</div>',
    ];
    if (state.signalFormOpen) {
      parts.push(signalFormHtml());
    }
    if (state.signalsError) {
      parts.push(
        '<p class="d-signals-note d-signals-error-note" data-testid="demand-signals-error" role="alert">' +
          esc(state.signalsError) +
        '</p>'
      );
    } else if (state.signalsLoading) {
      parts.push(
        '<p class="d-signals-note" data-testid="demand-signals-loading">' +
          esc(SIGNALS_LOADING) +
        '</p>'
      );
    } else if (state.signals !== null) {
      var rows = Array.isArray(state.signals) ? state.signals : [];
      if (!rows.length) {
        parts.push(
          '<p class="d-signals-note" data-testid="demand-signals-empty">' +
            esc(SIGNALS_EMPTY) +
          '</p>'
        );
      } else {
        parts.push(renderSignalsTable(rows, admin));
      }
    }
    parts.push('</div></section>');
    return parts.join('');
  }

  // The signals table: one row per signal in the answer's order, the
  // columns Kind, From, To, Units, Reason and Added by — and, for an
  // admin, a Withdraw button per row. The table scrolls inside its
  // own region, so a wide table never scrolls the page sideways.
  function renderSignalsTable(rows, admin) {
    var body = '';
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i] && typeof rows[i] === 'object' ? rows[i] : {};
      var id = row.id == null ? '' : String(row.id);
      body +=
        '<tr class="d-signal" data-testid="demand-signal" data-signal-id="' + esc(id) + '">' +
          '<td class="d-sig-kind">' + esc(signalKindLabel(row.kind)) + '</td>' +
          '<td class="d-sig-date">' +
            esc(row.start_date == null ? EM_DASH : String(row.start_date)) +
          '</td>' +
          '<td class="d-sig-date">' +
            esc(row.end_date == null ? EM_DASH : String(row.end_date)) +
          '</td>' +
          '<td class="d-sig-units">' + esc(units(row.quantity)) + '</td>' +
          '<td class="d-sig-reason">' + esc(row.reason == null ? EM_DASH : String(row.reason)) + '</td>' +
          '<td class="d-sig-by">' +
            esc(row.created_by_name == null ? EM_DASH : String(row.created_by_name)) +
          '</td>' +
          (admin
            ? '<td class="d-p-act">' +
              '<button type="button" class="d-ovr-btn" data-testid="demand-signal-withdraw" ' +
              'data-signal-id="' + esc(id) + '">Withdraw</button>' +
            '</td>'
            : '') +
        '</tr>';
      if (admin && state.openSignalWithdraw === id) {
        body += signalWithdrawFormHtml();
      }
    }
    return (
      '<div class="d-signals-scroll">' +
        '<table class="d-signals-table" data-testid="demand-signals-table">' +
          '<thead><tr>' +
            '<th scope="col">Kind</th>' +
            '<th scope="col">From</th>' +
            '<th scope="col">To</th>' +
            '<th scope="col">Units</th>' +
            '<th scope="col">Reason</th>' +
            '<th scope="col">Added by</th>' +
            (admin ? '<th scope="col" class="d-h-act">Action</th>' : '') +
          '</tr></thead>' +
          '<tbody>' +
            body +
          '</tbody>' +
        '</table>' +
      '</div>'
    );
  }

  // The "Add signal" form: the kind (defaulting to Promotion), the
  // period's dates, the units and the reason. Save is disabled until
  // both dates are set, the end is not before the start, the units
  // are a number above 0 and the reason is not blank — and while its
  // request is out.
  function signalFormHtml() {
    var draft =
      state.signalDraft || { kind: 'promotion', start: '', end: '', quantity: '', reason: '' };
    var kinds = [
      ['promotion', 'Promotion'],
      ['deal', 'Deal'],
      ['event', 'Event'],
    ];
    var options = '';
    for (var i = 0; i < kinds.length; i++) {
      options +=
        '<option value="' + kinds[i][0] + '"' +
        (String(draft.kind) === kinds[i][0] ? ' selected' : '') + '>' +
        esc(kinds[i][1]) +
        '</option>';
    }
    return (
      '<div class="d-signal-form" data-testid="demand-signal-form">' +
        '<div class="d-ovr-fields">' +
          '<label class="d-ovr-field">' +
            '<span class="d-ovr-label">Kind</span>' +
            '<select data-testid="demand-signal-kind">' + options + '</select>' +
          '</label>' +
          '<label class="d-ovr-field">' +
            '<span class="d-ovr-label">From</span>' +
            '<input type="date" data-testid="demand-signal-start" value="' +
            esc(draft.start) + '">' +
          '</label>' +
          '<label class="d-ovr-field">' +
            '<span class="d-ovr-label">To</span>' +
            '<input type="date" data-testid="demand-signal-end" value="' +
            esc(draft.end) + '">' +
          '</label>' +
          '<label class="d-ovr-field">' +
            '<span class="d-ovr-label">Units</span>' +
            '<input type="text" inputmode="decimal" autocomplete="off" spellcheck="false" ' +
              'data-testid="demand-signal-quantity" value="' + esc(draft.quantity) + '">' +
          '</label>' +
          '<label class="d-ovr-field d-ovr-field-wide">' +
            '<span class="d-ovr-label">Reason</span>' +
            '<input type="text" autocomplete="off" ' +
              'data-testid="demand-signal-reason" value="' + esc(draft.reason) + '">' +
          '</label>' +
        '</div>' +
        '<p class="d-ovr-form-error" data-testid="demand-signal-error" role="alert">' +
          (state.signalError ? esc(state.signalError) : '') +
        '</p>' +
        '<div class="d-ovr-actions">' +
          '<button type="button" class="btn btn-primary" data-testid="demand-signal-save"' +
            (signalSaveDisabled() ? ' disabled' : '') + '>Save</button>' +
          '<button type="button" class="btn btn-secondary" data-testid="demand-signal-cancel">Cancel</button>' +
        '</div>' +
      '</div>'
    );
  }

  // The withdrawal form, the row under the signal it withdraws: only
  // the reason — the period and the quantity come from the stored
  // signal.
  function signalWithdrawFormHtml() {
    var draft = state.signalWithdrawDraft || { reason: '' };
    return (
      '<tr class="d-signal-form-row" data-signal-id="' +
      esc(state.openSignalWithdraw == null ? '' : String(state.openSignalWithdraw)) +
      '">' +
        '<td colspan="' + (state.admin === true ? 7 : 6) + '">' +
          '<div class="d-signal-withdraw-form" data-testid="demand-signal-withdraw-form">' +
            '<label class="d-ovr-field d-ovr-field-wide">' +
              '<span class="d-ovr-label">Reason</span>' +
              '<input type="text" autocomplete="off" ' +
                'data-testid="demand-signal-withdraw-reason" value="' + esc(draft.reason) + '">' +
            '</label>' +
            '<p class="d-ovr-form-error" data-testid="demand-signal-error" role="alert">' +
              (state.signalWithdrawError ? esc(state.signalWithdrawError) : '') +
            '</p>' +
            '<div class="d-ovr-actions">' +
              '<button type="button" class="btn btn-primary" data-testid="demand-signal-withdraw-confirm"' +
                (signalWithdrawSaveDisabled() ? ' disabled' : '') +
                '>Confirm withdrawal</button>' +
              '<button type="button" class="btn btn-secondary" ' +
                'data-testid="demand-signal-withdraw-cancel">Cancel</button>' +
            '</div>' +
          '</div>' +
        '</td>' +
      '</tr>'
    );
  }

  function renderDetailBody(detail) {
    // The legend names the third series while a forecast bucket with
    // overridden days draws its override bar.
    var hasOverrideBar = asRows(detail.forecast).some(function (bucket) {
      return isBucket(bucket) && overrideDays(bucket) > 0;
    });
    return (
      '<div class="d-detail-inner">' +
        '<header class="d-detail-head">' +
          '<h2 class="d-detail-title">' + esc(detail.item_code) + ' \u00b7 ' + esc(detail.site_code) + '</h2>' +
          '<p class="d-detail-sub">' +
            esc(classLabel(detail.demand_class)) + ' \u00b7 as of ' + esc(detail.as_of) +
          '</p>' +
        '</header>' +
        '<figure class="d-chart" aria-label="History and forecast buckets">' +
          renderChart(detail) +
          '<figcaption class="d-chart-legend">' +
            '<span class="d-legend d-legend-history">History</span>' +
            '<span class="d-legend d-legend-forecast">Forecast</span>' +
            (hasOverrideBar ? '<span class="d-legend d-legend-override">Override</span>' : '') +
          '</figcaption>' +
        '</figure>' +
        renderPeriods(detail) +
        renderOverrides() +
        renderSignals() +
        '<section class="d-section" aria-label="How this forecast was made">' +
          '<h3>How this forecast was made</h3>' +
          '<dl class="d-facts">' +
            '<div class="d-fact"><dt>Method</dt><dd>' + esc(detail.method) + '</dd></div>' +
            '<div class="d-fact"><dt>Parameters</dt><dd>' + paramsText(detail.params) + '</dd></div>' +
            '<div class="d-fact"><dt>History buckets used</dt><dd>' + esc(units(detail.history_buckets)) + '</dd></div>' +
          '</dl>' +
          '<pre class="d-arithmetic" data-testid="demand-arithmetic">' + esc(detail.arithmetic || '') + '</pre>' +
        '</section>' +
        renderBacktest(detail.backtest) +
        renderRealisedDetail(detail.item_code, detail.site_code) +
        renderEmpirical(detail) +
      '</div>'
    );
  }

  // One rect per history bucket, then one per forecast bucket, in date
  // order (the API sends both lists oldest bucket first).
  function renderChart(detail) {
    var history = asRows(detail.history).filter(isBucket);
    var forecast = asRows(detail.forecast).filter(isBucket);
    while (history.length + forecast.length > CHART_MAX_BARS) {
      if (history.length) {
        history.pop();
      } else {
        forecast.pop();
      }
    }
    var all = history.concat(forecast);
    // The value each bar draws at: its quantity as-is, the effective
    // quantity for a forecast bucket with overridden days.
    var values = all.map(function (bucket, index) {
      var series =
        index < history.length ? 'history' : overrideDays(bucket) > 0 ? 'override' : 'forecast';
      return series === 'override' && isNumber(bucket.effective_quantity)
        ? bucket.effective_quantity
        : isNumber(bucket.quantity)
          ? bucket.quantity
          : 0;
    });
    var max = 0;
    for (var i = 0; i < values.length; i++) {
      if (values[i] > max) {
        max = values[i];
      }
    }
    if (!max) {
      max = 1;
    }
    var width = 440;
    var height = 160;
    var plotTop = 10;
    var plotBottom = 150;
    var slot = width / all.length;
    var barWidth = Math.min(slot * 0.7, 24);
    var bars = [];
    for (var index = 0; index < all.length; index++) {
      var bucket = all[index];
      var series =
        index < history.length ? 'history' : overrideDays(bucket) > 0 ? 'override' : 'forecast';
      var value = values[index];
      var barHeight = value <= 0 ? 0 : Math.max(2, (value / max) * (plotBottom - plotTop));
      var x = index * slot + (slot - barWidth) / 2;
      var y = plotBottom - barHeight;
      bars.push(
        '<rect class="d-bar d-bar-' + series + '" data-series="' + series + '" ' +
        'x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" ' +
        'width="' + barWidth.toFixed(1) + '" height="' + barHeight.toFixed(1) + '">' +
          '<title>' + esc(barTitle(bucket, series)) + '</title>' +
        '</rect>'
      );
    }
    return (
      '<svg data-testid="demand-chart" viewBox="0 0 ' + width + ' ' + height + '" role="img" ' +
      'aria-label="The history buckets, then the forecast buckets, in date order">' +
      '<line class="d-chart-baseline" x1="0" y1="' + plotBottom + '" x2="' + width + '" y2="' + plotBottom + '"></line>' +
      bars.join('') +
      '</svg>'
    );
  }

  // "2026-01-01 – 2026-01-30: 12" — the bar's date range and the
  // bucket's own quantity through the view's quantity boundary, an em
  // dash when the quantity is not a number (the bar still draws at
  // zero height); the override series gives the statistical and the
  // effective quantity.
  function barTitle(bucket, series) {
    var range = String(bucket.bucket_start) + ' \u2013 ' + String(bucket.bucket_end);
    if (series === 'override') {
      return (
        range +
        ': statistical ' +
        quantityText(bucket.quantity) +
        ', effective ' +
        quantityText(bucket.effective_quantity)
      );
    }
    return range + ': ' + quantityText(bucket.quantity);
  }

  function renderBacktest(backtest) {
    var parts = ['<section class="d-section d-backtest" aria-label="Backtest">', '<h3>Backtest</h3>'];
    if (!backtest || typeof backtest !== 'object') {
      parts.push('<p class="d-backtest-note">' + esc(NOT_ENOUGH_HISTORY) + '</p>');
      parts.push('</section>');
      return parts.join('');
    }
    parts.push(
      '<div class="d-backtest-tiles">' +
        backtestTile('Method MAE', figure2(backtest.mae)) +
        backtestTile('Baseline MAE', figure2(backtest.baseline_mae)) +
        backtestTile('Error', percent(backtest.wape)) +
        backtestTile('Bias', percent(backtest.bias)) +
      '</div>'
    );
    parts.push('</section>');
    return parts.join('');
  }

  function backtestTile(label, value) {
    return (
      '<div class="d-bt-tile">' +
        '<span class="d-bt-label">' + esc(label) + '</span>' +
        '<span class="d-bt-value">' + esc(value) + '</span>' +
      '</div>'
    );
  }

  // The summary line above the list and the detail panes: the overall
  // measures of the two accuracy responses — the error as a rounded
  // percentage, the bias signed, the counts with thousands separators,
  // an em dash for a null measure — "Realised accuracy: loading…"
  // while either request is in flight, the nothing-compared sentence
  // when the one-period-ahead response has no compared buckets, and
  // "Realised accuracy is not available." when either request failed.
  function renderRealisedSummary() {
    if (state.accuracyError) {
      return esc(REALISED_NOT_AVAILABLE);
    }
    if (state.accuracyLoading) {
      return esc(REALISED_LOADING);
    }
    var ahead = state.accuracyAhead;
    var all = state.accuracyAll;
    if (!ahead || !all) {
      return esc(REALISED_NOT_AVAILABLE);
    }
    if (isNumber(ahead.compared_buckets) && ahead.compared_buckets === 0) {
      return esc(REALISED_NONE_YET);
    }
    var aheadOverall = ahead.overall && typeof ahead.overall === 'object' ? ahead.overall : {};
    var allOverall = all.overall && typeof all.overall === 'object' ? all.overall : {};
    var line =
      'Realised accuracy. One period ahead: ' +
      percent(aheadOverall.wape) + ' error, ' + signedPercent(aheadOverall.bias) + ' bias, ' +
      units(ahead.compared_buckets) + ' periods compared. ' +
      'All horizons: ' +
      percent(allOverall.wape) + ' error, ' + signedPercent(allOverall.bias) + ' bias, ' +
      units(all.compared_buckets) + ' periods compared.';
    // The overrides' effect (issue #388): when the all-horizons
    // comparison covers overridden buckets, the line ends with the
    // count and how far the overrides moved the error from the
    // statistical forecast's — the word carries the direction and the
    // token colour is only a second signal.
    var clause = summaryOverrideClause(allOverall);
    if (!clause) {
      return esc(line);
    }
    return esc(line) + clause;
  }

  // The summary's tail: " Overrides: N periods overridden, X points
  // better than the statistical forecast." — "worse" for a negative
  // value_added, "no change" for 0 — with N through the view's count
  // boundary (thousands separators) and X the effect size in points.
  // Empty when no compared bucket was overridden or value_added is
  // null (a response without the fields reads exactly as before).
  function summaryOverrideClause(overall) {
    if (!isNumber(overall.overridden_buckets) || overall.overridden_buckets <= 0) {
      return '';
    }
    if (!isNumber(overall.value_added)) {
      return '';
    }
    var className = 'd-realised-ovr';
    var effect;
    if (overall.value_added > 0) {
      className += ' d-realised-ovr-better';
      effect = pointsText(overall.value_added) + ' points better than the statistical forecast';
    } else if (overall.value_added < 0) {
      className += ' d-realised-ovr-worse';
      effect = pointsText(overall.value_added) + ' points worse than the statistical forecast';
    } else {
      effect = 'no change';
    }
    return (
      '<span class="' + className + '">' +
      esc(' Overrides: ' + units(overall.overridden_buckets) + ' periods overridden, ' + effect + '.') +
      '</span>'
    );
  }

  // The demand signals line under the summary (issue #984): the
  // overall measures of GET /api/forecast/accuracy/signals — "Demand
  // signals: N periods with a signal, X% error without them, Y% with
  // them, Z points better." — "Z points worse" for a negative
  // value_added, "no change" for 0, with N through the view's count
  // boundary (thousands separators), X and Y percentages to one
  // decimal and Z the effect size in points. The better and worse
  // wording carries the overrides clause' classes — the word carries
  // the direction and the token colour is only a second signal. The
  // line is absent while the request is in flight, with no compared
  // bucket, with a null or non-numeric value_added or a non-numeric
  // wape, and with any of them so large that x 100 overflows to
  // Infinity (a hostile API value, not a valid result); it reads the
  // unavailable sentence when the request failed — independently of
  // the two accuracy requests.
  function renderRealisedSignalsLine() {
    if (state.accuracySignalsError) {
      return (
        '<p class="d-realised-signals" data-testid="demand-realised-signals">' +
        esc(SIGNALS_ACCURACY_NOT_AVAILABLE) +
        '</p>'
      );
    }
    var data = state.accuracySignals;
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
      return ''; // the request is still in flight
    }
    var buckets = data.compared_buckets;
    var beforeWape = data.before_wape;
    var afterWape = data.after_wape;
    var valueAdded = data.value_added;
    if (!isNumber(buckets) || buckets <= 0) {
      return '';
    }
    if (!isScaledNumber(beforeWape) || !isScaledNumber(afterWape) || !isScaledNumber(valueAdded)) {
      return '';
    }
    var text =
      'Demand signals: ' +
      units(buckets) +
      ' periods with a signal, ' +
      percent1(beforeWape) +
      ' error without them, ' +
      percent1(afterWape) +
      ' with them, ';
    var effect = 'no change';
    var className = 'd-realised-ovr';
    if (valueAdded > 0) {
      effect = pointsText(valueAdded) + ' points better';
      className += ' d-realised-ovr-better';
    } else if (valueAdded < 0) {
      effect = pointsText(valueAdded) + ' points worse';
      className += ' d-realised-ovr-worse';
    }
    return (
      '<p class="d-realised-signals" data-testid="demand-realised-signals">' +
      esc(text) +
      '<span class="' + className + '">' +
      esc(effect + '.') +
      '</span></p>'
    );
  }

  // The detail section below "Backtest": the chosen item-site's
  // one-period-ahead measures, or the sentence for why they are not
  // there yet.
  function renderRealisedDetail(item, site) {
    var parts = [
      '<section class="d-section d-realised-section" data-testid="demand-realised-detail" ' +
        'aria-label="Realised, one period ahead">',
      '<h3>Realised, one period ahead</h3>',
    ];
    if (state.accuracyError) {
      parts.push('<p class="d-realised-note">' + esc(REALISED_NOT_AVAILABLE) + '</p>');
    } else if (state.accuracyLoading) {
      parts.push('<p class="d-realised-note">' + esc(REALISED_LOADING) + '</p>');
    } else {
      var entry = aheadEntry(item, site);
      if (!entry) {
        parts.push('<p class="d-realised-note">' + esc(REALISED_DETAIL_NONE) + '</p>');
      } else {
        parts.push(
          '<dl class="d-facts">' +
            '<div class="d-fact"><dt>Forecast total</dt><dd>' +
              esc(quantityText(entry.forecast_total)) + '</dd></div>' +
            '<div class="d-fact"><dt>Actual total</dt><dd>' +
              esc(quantityText(entry.actual_total)) + '</dd></div>' +
            '<div class="d-fact"><dt>Error</dt><dd>' +
              esc(percent(entry.wape)) + '</dd></div>' +
            '<div class="d-fact"><dt>Bias</dt><dd>' +
              esc(signedPercent(entry.bias)) + '</dd></div>' +
            '<div class="d-fact"><dt>Periods compared</dt><dd>' +
              esc(units(entry.compared_buckets)) + '</dd></div>' +
            overrideEffectFacts(entry) +
          '</dl>'
        );
        if (isNumber(entry.actual_total) && entry.actual_total === 0) {
          parts.push('<p class="d-realised-note">' + esc(REALISED_NO_DEMAND) + '</p>');
        }
      }
    }
    parts.push('</section>');
    return parts.join('');
  }

  // The three facts after "Periods compared" (issue #388): when the
  // item-site's one-period-ahead entry covers overridden buckets —
  // the effective forecast's total and error beside the
  // statistical ones, and the overrides' effect: "X points better"
  // in the accent colour, "X points worse" in the risk colour,
  // "no change", or an em dash when value_added is not a number.
  // With no overridden bucket the section is exactly as before.
  function overrideEffectFacts(entry) {
    if (!isNumber(entry.overridden_buckets) || entry.overridden_buckets <= 0) {
      return '';
    }
    var effect = EM_DASH;
    var className = '';
    if (isNumber(entry.value_added)) {
      if (entry.value_added > 0) {
        effect = pointsText(entry.value_added) + ' points better';
        className = 'd-ovr-effect-better';
      } else if (entry.value_added < 0) {
        effect = pointsText(entry.value_added) + ' points worse';
        className = 'd-ovr-effect-worse';
      } else {
        effect = 'no change';
      }
    }
    return (
      '<div class="d-fact"><dt>Effective forecast total</dt><dd>' +
        esc(figure2(entry.effective_total)) + '</dd></div>' +
      '<div class="d-fact"><dt>Effective error</dt><dd>' +
        esc(percent(entry.effective_wape)) + '</dd></div>' +
      '<div class="d-fact"><dt>Overrides\u2019 effect</dt><dd' +
      (className ? ' class="' + className + '"' : '') + '>' +
      esc(effect) + '</dd></div>'
    );
  }

  // The empirical reorder point section (issue #684): the pane's
  // last section, drawn only for a 200 answer whose empirical block
  // is an object and only while the pane shows the item-site the
  // answer belongs to. A non-200 answer, a network failure or a null
  // empirical renders no element and no message at all — the state
  // is cleared when the load starts, so there is nothing to draw
  // until the answer lands.
  function renderEmpirical(detail) {
    var rec = state.empirical;
    var forPair = state.empiricalFor;
    if (!rec || !forPair) {
      return '';
    }
    if (
      !sameCode(forPair.item, detail.item_code) ||
      !sameCode(forPair.site, detail.site_code)
    ) {
      return '';
    }
    var block = rec.empirical;
    if (!block || typeof block !== 'object' || Array.isArray(block)) {
      return '';
    }
    var current =
      rec.current && typeof rec.current === 'object' && !Array.isArray(rec.current)
        ? rec.current
        : null;
    var currentRop = current ? current.reorder_point : null;
    return (
      '<section class="d-section d-empirical" data-testid="demand-empirical" ' +
        'aria-label="Reorder point from past demand">' +
        '<h3>Reorder point from past demand</h3>' +
        '<p data-testid="demand-empirical-summary">' +
          esc(
            'Reorder point ' +
              units(block.reorder_point_units) +
              ' \u00b7 safety stock ' +
              units(block.safety_stock_units) +
              ' \u00b7 service level ' +
              serviceLevelText(block.service_level)
          ) +
        '</p>' +
        '<p data-testid="demand-empirical-current">' +
          esc('ERP reorder point now: ' + (isNumber(currentRop) ? quantityText(currentRop) : 'not set')) +
        '</p>' +
        '<pre class="d-arithmetic" data-testid="demand-empirical-arithmetic">' +
          esc(block.arithmetic == null ? '' : String(block.arithmetic)) +
        '</pre>' +
        '<p class="d-empirical-note" data-testid="demand-empirical-note">' +
          esc(
            'For review: ' +
              units(block.spans_with_demand) +
              ' of ' +
              units(block.spans) +
              ' spans had demand. Not yet used as the recommendation.'
          ) +
        '</p>' +
      '</section>'
    );
  }

  /* -------------------------------- data -------------------------------- */

  function startLoad() {
    if (state.loading) {
      return;
    }
    state.loading = true;
    state.error = null;
    render();
    var seq = ++listSeq;
    // The two accuracy requests go out with the list load — and with
    // the list's "Try again" only, never with a row or a chip choice.
    startAccuracyLoad();
    // The view's own who-am-I: once per view load [rule: OVR-10].
    startMeLoad();
    global.Api.get(LIST_PATH).then(
      function (rows) {
        if (seq !== listSeq) {
          return;
        }
        state.rows = asForecastRows(rows);
        state.loading = false;
        state.ready = true;
        if (state.rows.length) {
          // The first row is chosen when the view opens.
          selectRow(state.rows[0].item_code, state.rows[0].site_code);
        } else {
          state.selected = null;
          state.detail = null;
          state.detailFor = null;
          state.detailLoading = false;
          empiricalSeq += 1;
          state.empirical = null;
          state.empiricalFor = null;
          render();
        }
      },
      function (error) {
        if (seq !== listSeq) {
          return;
        }
        state.loading = false;
        state.error =
          error && error.message
            ? String(error.message)
            : 'The forecasts could not be loaded. Please try again.';
        render();
      }
    );
  }

  // The realised accuracy: the two accuracy requests (the
  // one-period-ahead measures at bucket_index=1 and the all-horizons
  // ones) plus the signals request (issue #984). The list and the
  // detail pane never wait for them; a response that arrives after a
  // newer load has started is ignored; either accuracy failure makes
  // the realised figures unavailable while the rest of the view keeps
  // working. The signals request rides the same sequence guard but
  // keeps its own loading and error state: it never sets or clears
  // accuracyLoading or accuracyError, and neither accuracy request's
  // settle counts it.
  function startAccuracyLoad() {
    state.accuracyAhead = null;
    state.accuracyAll = null;
    state.accuracyError = false;
    state.accuracyLoading = true;
    state.accuracySignals = null;
    state.accuracySignalsError = false;
    var pending = 2;
    var seq = ++accuracySeq;

    function settle(which, ok, payload) {
      if (seq !== accuracySeq) {
        return; // a newer load has started; ignore this response
      }
      if (
        ok &&
        !state.accuracyError &&
        payload &&
        typeof payload === 'object' &&
        !Array.isArray(payload)
      ) {
        if (which === 'ahead') {
          state.accuracyAhead = payload;
        } else {
          state.accuracyAll = payload;
        }
      } else {
        state.accuracyError = true;
      }
      pending -= 1;
      state.accuracyLoading = !state.accuracyError && pending > 0;
      render();
    }

    function settleSignals(ok, payload) {
      if (seq !== accuracySeq) {
        return; // a newer load has started; ignore this response
      }
      if (ok && payload && typeof payload === 'object' && !Array.isArray(payload)) {
        state.accuracySignals = payload;
      } else {
        state.accuracySignalsError = true;
      }
      render();
    }

    global.Api.get(ACCURACY_AHEAD_PATH).then(
      function (payload) {
        settle('ahead', true, payload);
      },
      function () {
        settle('ahead', false, null);
      }
    );
    global.Api.get(ACCURACY_ALL_PATH).then(
      function (payload) {
        settle('all', true, payload);
      },
      function () {
        settle('all', false, null);
      }
    );
    global.Api.get(ACCURACY_SIGNALS_PATH).then(
      function (payload) {
        settleSignals(true, payload);
      },
      function () {
        settleSignals(false, null);
      }
    );
  }

  function selectRow(item, site) {
    if (item == null || site == null || String(item) === '' || String(site) === '') {
      return;
    }
    if (
      state.selected &&
      sameCode(state.selected.item, item) &&
      sameCode(state.selected.site, site)
    ) {
      return; // already chosen; its detail is loading or shown
    }
    state.selected = { item: item, site: site };
    closeOpenForms();
    // The signals section belongs to the chosen item-site: choosing
    // another one clears it back to "Show signals" and makes an
    // answer for the earlier pair stale.
    resetSignalsState();
    startDetailLoad(item, site);
    render();
  }

  function startDetailLoad(item, site) {
    state.detail = null;
    state.detailError = null;
    state.detailFor = { item: item, site: site };
    state.detailLoading = true;
    startOverridesLoad(item, site);
    startEmpiricalLoad(item, site);
    var seq = ++detailSeq;
    var path =
      '/api/forecast/by-key?item_code=' +
      encodeURIComponent(String(item)) +
      '&site_code=' +
      encodeURIComponent(String(site));
    global.Api.get(path).then(
      function (detail) {
        if (seq !== detailSeq) {
          return;
        }
        state.detailLoading = false;
        if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
          state.detail = detail;
        } else {
          state.detailError =
            'The forecast detail could not be loaded. Please try again.';
        }
        render();
      },
      function (error) {
        if (seq !== detailSeq) {
          return;
        }
        state.detailLoading = false;
        state.detailError =
          error && error.message
            ? String(error.message)
            : 'The forecast detail could not be loaded. Please try again.';
        render();
      }
    );
  }

  // The chosen item-site's stored overrides: one request per detail
  // load, through the shared API layer. The detail never waits for it;
  // a response that arrives after another item-site has been chosen
  // changes nothing on screen; a failure leaves only the "Current
  // overrides" section unavailable, the rest of the detail works.
  function startOverridesLoad(item, site) {
    state.overrides = null;
    state.overridesLoading = true;
    state.overridesError = false;
    var seq = ++overridesSeq;
    var path =
      '/api/forecast/overrides?item_code=' +
      encodeURIComponent(String(item)) +
      '&site_code=' +
      encodeURIComponent(String(site)) +
      '&limit=200';
    global.Api.get(path).then(
      function (payload) {
        if (seq !== overridesSeq) {
          return; // another item-site was chosen in the meantime
        }
        state.overridesLoading = false;
        state.overrides = Array.isArray(payload) ? payload : [];
        render();
      },
      function () {
        if (seq !== overridesSeq) {
          return; // another item-site was chosen in the meantime
        }
        state.overridesLoading = false;
        state.overridesError = true;
        render();
      }
    );
  }

  // The chosen item-site's empirical reorder point
  // (GET /api/recommendations/by-key with the row's item_code and
  // site_code): one request per detail load, through the shared API
  // layer, for an "intermittent" or a "lumpy" demand class only —
  // the class of the list row the selection came from. The detail
  // never waits for the answer; an answer that arrives after another
  // item-site has been chosen changes nothing on screen; a failed
  // answer or a null empirical shows no section and no message.
  function startEmpiricalLoad(item, site) {
    state.empirical = null;
    state.empiricalFor = null;
    var seq = ++empiricalSeq;
    var row = null;
    for (var i = 0; i < state.rows.length; i++) {
      if (
        sameCode(state.rows[i].item_code, item) &&
        sameCode(state.rows[i].site_code, site)
      ) {
        row = state.rows[i];
        break;
      }
    }
    var cls = row && row.demand_class == null ? '' : String(row.demand_class);
    if (cls !== 'intermittent' && cls !== 'lumpy') {
      return; // no request for any other demand class
    }
    var path =
      '/api/recommendations/by-key?item_code=' +
      encodeURIComponent(String(item)) +
      '&site_code=' +
      encodeURIComponent(String(site));
    global.Api.get(path).then(
      function (payload) {
        if (seq !== empiricalSeq) {
          return; // another item-site was chosen in the meantime
        }
        var block =
          payload && typeof payload === 'object' && !Array.isArray(payload)
            ? payload.empirical
            : null;
        if (!block || typeof block !== 'object' || Array.isArray(block)) {
          return; // a null empirical shows nothing at all
        }
        state.empirical = payload;
        state.empiricalFor = { item: item, site: site };
        render();
      },
      function () {
        if (seq !== empiricalSeq) {
          return; // another item-site was chosen in the meantime
        }
        // A failed request shows no section and no message; the
        // state was cleared when this load started.
      }
    );
  }

  // The view's who-am-I [rule: OVR-10]: once per view load, through
  // the shared API layer. Only a user whose roles include "admin"
  // gets the override and withdrawal controls; a failed answer leaves
  // the view read-only, and an answer after a newer view load is
  // ignored.
  function startMeLoad() {
    state.admin = null;
    var seq = ++meSeq;
    global.Api.get('/api/auth/me').then(
      function (me) {
        if (seq !== meSeq) {
          return;
        }
        var roles = me && me.user && Array.isArray(me.user.roles) ? me.user.roles : [];
        state.admin = roles.indexOf('admin') !== -1;
        render();
      },
      function () {
        if (seq !== meSeq) {
          return;
        }
        state.admin = false;
        render();
      }
    );
  }

  // Close whatever admin form is open, with nothing sent. Bumping
  // writeSeq makes a request still in flight stale: its answer changes
  // nothing on screen.
  function closeOpenForms() {
    state.openOverride = null;
    state.overrideDraft = null;
    state.overrideError = null;
    state.sendingOverride = false;
    state.openWithdraw = null;
    state.withdrawDraft = null;
    state.withdrawError = null;
    state.sendingWithdraw = false;
    state.signalFormOpen = false;
    state.signalDraft = null;
    state.signalError = null;
    state.sendingSignal = false;
    state.openSignalWithdraw = null;
    state.signalWithdrawDraft = null;
    state.signalWithdrawError = null;
    state.sendingSignalWithdraw = false;
    writeSeq += 1;
  }

  // The chosen item-site is still the one the write went out for.
  function stillOn(owner) {
    return (
      !!state.selected &&
      sameCode(state.selected.item, owner.item) &&
      sameCode(state.selected.site, owner.site)
    );
  }

  // Open the override form under one period row. One form is open at
  // a time: opening it closes any open withdrawal form. The quantity
  // starts at the period's effective quantity, or its statistical
  // quantity when nothing is overridden.
  function openOverrideForm(period) {
    if (state.admin !== true || !state.detail || period == null) {
      return;
    }
    var bucket = bucketForPeriod(period);
    if (!bucket) {
      return;
    }
    state.openOverride = String(period);
    state.overrideDraft = { quantity: initialQuantityFor(bucket), reason: '' };
    state.overrideError = null;
    state.openWithdraw = null;
    state.withdrawDraft = null;
    state.withdrawError = null;
    render();
  }

  // Open the withdrawal form under one stored row. One form is open
  // at a time: opening it closes any open override form.
  function openWithdrawForm(id) {
    if (state.admin !== true || id == null) {
      return;
    }
    var rows = Array.isArray(state.overrides) ? state.overrides : [];
    var found = null;
    for (var i = 0; i < rows.length; i++) {
      if (
        rows[i] &&
        typeof rows[i] === 'object' &&
        rows[i].applies === true &&
        String(rows[i].id) === String(id)
      ) {
        found = rows[i];
        break;
      }
    }
    if (!found) {
      return;
    }
    state.openWithdraw = String(id);
    state.withdrawDraft = { reason: '' };
    state.withdrawError = null;
    state.openOverride = null;
    state.overrideDraft = null;
    state.overrideError = null;
    render();
  }

  // Save the open override form [rule: OVR-1] [rule: OVR-5] [rule: OVR-6]:
  // POST /api/forecast/overrides with the chosen item-site, the period,
  // the quantity and the trimmed reason. The send button is disabled
  // while the request is out, so a double click sends one request. On
  // success the form closes and the detail and the overrides load
  // again for the same item-site; the list is not loaded again. On a
  // refusal the server's message shows in the form and the form stays
  // open with what was typed. An answer after another item-site has
  // been chosen changes nothing on screen.
  function submitOverride() {
    if (
      state.admin !== true ||
      state.sendingOverride ||
      state.openOverride == null ||
      !overrideDraftValid(state.overrideDraft) ||
      !state.detail
    ) {
      return;
    }
    var quantity = parseQuantity(state.overrideDraft.quantity);
    var reason = String(state.overrideDraft.reason).trim();
    if (quantity === null || reason === '') {
      return;
    }
    var owner = { item: state.detail.item_code, site: state.detail.site_code };
    var period = state.openOverride;
    var seq = ++writeSeq;
    state.sendingOverride = true;
    state.overrideError = null;
    render();
    global.Api.request(
      '/api/forecast/overrides',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          item_code: owner.item,
          site_code: owner.site,
          period_start: period,
          quantity: quantity,
          reason: reason,
        }),
      }
    ).then(
      function () {
        if (seq !== writeSeq || !stillOn(owner)) {
          return; // another item-site was chosen in the meantime
        }
        state.sendingOverride = false;
        state.openOverride = null;
        state.overrideDraft = null;
        state.overrideError = null;
        state.pendingFocus = { item: owner.item, site: owner.site, kind: 'override-button', key: period };
        startDetailLoad(owner.item, owner.site);
        render();
      },
      function (error) {
        if (seq !== writeSeq || !stillOn(owner)) {
          return;
        }
        state.sendingOverride = false;
        state.overrideError =
          error && error.message
            ? String(error.message)
            : 'The override could not be saved. Please try again.';
        render();
      }
    );
  }

  // Withdraw the stored row the open withdrawal form sits under
  // [rule: OVR-9]: POST /api/forecast/overrides/withdrawals with the
  // chosen item-site, the stored period and the trimmed reason. Same
  // shape as a save: disabled while out, the server's refusal in the
  // form with what was typed, a stale answer changes nothing.
  function submitWithdraw() {
    if (
      state.admin !== true ||
      state.sendingWithdraw ||
      state.openWithdraw == null ||
      !withdrawDraftValid(state.withdrawDraft) ||
      !state.detail
    ) {
      return;
    }
    var rows = Array.isArray(state.overrides) ? state.overrides : [];
    var found = null;
    for (var i = 0; i < rows.length; i++) {
      if (
        rows[i] &&
        typeof rows[i] === 'object' &&
        rows[i].applies === true &&
        String(rows[i].id) === state.openWithdraw
      ) {
        found = rows[i];
        break;
      }
    }
    if (!found || found.period_start == null) {
      return;
    }
    var reason = String(state.withdrawDraft.reason).trim();
    if (reason === '') {
      return;
    }
    var owner = { item: state.detail.item_code, site: state.detail.site_code };
    var rowId = String(found.id);
    var seq = ++writeSeq;
    state.sendingWithdraw = true;
    state.withdrawError = null;
    render();
    global.Api.request(
      '/api/forecast/overrides/withdrawals',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          item_code: owner.item,
          site_code: owner.site,
          period_start: String(found.period_start),
          reason: reason,
        }),
      }
    ).then(
      function () {
        if (seq !== writeSeq || !stillOn(owner)) {
          return;
        }
        state.sendingWithdraw = false;
        state.openWithdraw = null;
        state.withdrawDraft = null;
        state.withdrawError = null;
        state.pendingFocus = { item: owner.item, site: owner.site, kind: 'withdraw-button', key: rowId };
        startDetailLoad(owner.item, owner.site);
        render();
      },
      function (error) {
        if (seq !== writeSeq || !stillOn(owner)) {
          return;
        }
        state.sendingWithdraw = false;
        state.withdrawError =
          error && error.message
            ? String(error.message)
            : 'The withdrawal could not be saved. Please try again.';
        render();
      }
    );
  }

  /* ------------------------ demand signals #965 ---------------------- */

  // Clear the signals section of the chosen item-site: back to
  // "Show signals", no list, no error, no open form — and an answer
  // for the earlier pair stale.
  function resetSignalsState() {
    state.signals = null;
    state.signalsLoading = false;
    state.signalsError = null;
    state.signalsTried = false;
    state.signalFormOpen = false;
    state.signalDraft = null;
    state.signalError = null;
    state.sendingSignal = false;
    state.openSignalWithdraw = null;
    state.signalWithdrawDraft = null;
    state.signalWithdrawError = null;
    state.sendingSignalWithdraw = false;
    signalsSeq += 1;
  }

  // GET /api/demand-signals for the chosen item-site, through the
  // shared API layer, on demand only: the section's button is the
  // trigger, and so are a save's and a withdrawal's success. The
  // detail never waits for the answer; an answer after another
  // item-site has been chosen changes nothing on screen; a failure
  // leaves the section's error on screen and the button reading
  // "Refresh signals" — it does not reset it to "Show signals".
  function startSignalsLoad(item, site) {
    if (item == null || site == null) {
      return;
    }
    state.signals = null;
    state.signalsError = null;
    state.signalsLoading = true;
    var seq = ++signalsSeq;
    var path =
      '/api/demand-signals?item_code=' +
      encodeURIComponent(String(item)) +
      '&site_code=' +
      encodeURIComponent(String(site));
    global.Api.get(path).then(
      function (payload) {
        if (seq !== signalsSeq) {
          return; // another item-site was chosen in the meantime
        }
        state.signalsLoading = false;
        state.signalsTried = true;
        state.signals = Array.isArray(payload) ? payload : [];
        render();
      },
      function () {
        if (seq !== signalsSeq) {
          return;
        }
        state.signalsLoading = false;
        state.signalsTried = true;
        state.signalsError = SIGNALS_NOT_AVAILABLE;
        render();
      }
    );
  }

  // "promotion" -> "Promotion" (the table's Kind column and the add
  // form's select); an unknown kind is capitalised, never raw markup.
  var SIGNAL_KINDS = { promotion: 'Promotion', deal: 'Deal', event: 'Event' };

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

  // A number above 0, and nothing else: empty, blank, zero, negative,
  // non-numeric or control-character junk all refuse.
  function parseSignalQuantity(text) {
    var trimmed = String(text == null ? '' : text).trim();
    if (!QUANTITY_PATTERN.test(trimmed)) {
      return null;
    }
    var num = Number(trimmed);
    if (!isFinite(num) || num <= 0) {
      return null;
    }
    return num;
  }

  // A YYYY-MM-DD date the browser's date field sends.
  var ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

  function isIsoDate(value) {
    var text = String(value == null ? '' : value).trim();
    return ISO_DATE_PATTERN.test(text) && !isNaN(Date.parse(text));
  }

  // The add form's draft: both dates set, the end not before the
  // start, the units a number above 0 and the reason not blank.
  function signalDraftValid(draft) {
    if (!draft) {
      return false;
    }
    var start = String(draft.start == null ? '' : draft.start).trim();
    var end = String(draft.end == null ? '' : draft.end).trim();
    return (
      String(draft.kind == null ? '' : draft.kind) !== '' &&
      isIsoDate(start) &&
      isIsoDate(end) &&
      end >= start &&
      parseSignalQuantity(draft.quantity) !== null &&
      String(draft.reason == null ? '' : draft.reason).trim() !== ''
    );
  }

  function signalSaveDisabled() {
    return state.sendingSignal || !signalDraftValid(state.signalDraft);
  }

  function signalWithdrawSaveDisabled() {
    return (
      state.sendingSignalWithdraw ||
      !state.signalWithdrawDraft ||
      String(state.signalWithdrawDraft.reason == null ? '' : state.signalWithdrawDraft.reason).trim() ===
        ''
    );
  }

  // The refusal the signal forms show: a 409 or a 422 carries the
  // server's detail, a 403 the admin sentence, anything else the
  // generic one.
  function signalErrorText(error) {
    var status = error && typeof error.status === 'number' ? error.status : null;
    if (status === 409 || status === 422) {
      return error && error.message
        ? String(error.message)
        : SIGNAL_SAVE_FAILED;
    }
    if (status === 403) {
      return SIGNAL_NOT_ADMIN;
    }
    return SIGNAL_SAVE_FAILED;
  }

  // Open the "Add signal" form (admin only). One signals form is open
  // at a time: opening it closes any open withdrawal form.
  function openSignalForm() {
    if (state.admin !== true || !state.detail) {
      return;
    }
    state.signalFormOpen = true;
    state.signalDraft = { kind: 'promotion', start: '', end: '', quantity: '', reason: '' };
    state.signalError = null;
    state.openSignalWithdraw = null;
    state.signalWithdrawDraft = null;
    state.signalWithdrawError = null;
    render();
  }

  // Open the withdrawal form under one signal row (admin only).
  function openSignalWithdrawForm(id) {
    if (state.admin !== true || id == null || String(id) === '' || !state.detail) {
      return;
    }
    state.openSignalWithdraw = String(id);
    state.signalWithdrawDraft = { reason: '' };
    state.signalWithdrawError = null;
    state.signalFormOpen = false;
    state.signalDraft = null;
    state.signalError = null;
    render();
  }

  // Close whichever signals form is open, with nothing sent. Bumping
  // writeSeq makes a request still in flight stale: its answer changes
  // nothing on screen.
  function cancelSignalForms() {
    state.signalFormOpen = false;
    state.signalDraft = null;
    state.signalError = null;
    state.sendingSignal = false;
    state.openSignalWithdraw = null;
    state.signalWithdrawDraft = null;
    state.signalWithdrawError = null;
    state.sendingSignalWithdraw = false;
    writeSeq += 1;
  }

  // Save the open "Add signal" form: POST /api/demand-signals with
  // the kind, the chosen item-site, the period's dates, the units and
  // the trimmed reason. The send button is disabled while the request
  // is out, so a double click sends one request. On success the form
  // closes and the signals and the detail load again for the same
  // item-site, so the periods table shows the new effective
  // forecast. On a refusal the form stays open with what was typed
  // and the mapped message shows in it. An answer after another
  // item-site has been chosen changes nothing on screen.
  function submitSignal() {
    if (
      state.admin !== true ||
      state.sendingSignal ||
      !state.signalFormOpen ||
      !signalDraftValid(state.signalDraft) ||
      !state.detail
    ) {
      return;
    }
    var draft = state.signalDraft;
    var quantity = parseSignalQuantity(draft.quantity);
    var reason = String(draft.reason).trim();
    if (quantity === null || reason === '') {
      return;
    }
    var owner = { item: state.detail.item_code, site: state.detail.site_code };
    var seq = ++writeSeq;
    state.sendingSignal = true;
    state.signalError = null;
    render();
    global.Api.request(
      '/api/demand-signals',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: String(draft.kind),
          item_code: owner.item,
          site_code: owner.site,
          start_date: String(draft.start).trim(),
          end_date: String(draft.end).trim(),
          quantity: quantity,
          reason: reason,
        }),
      }
    ).then(
      function () {
        if (seq !== writeSeq || !stillOn(owner)) {
          return; // another item-site was chosen in the meantime
        }
        state.sendingSignal = false;
        state.signalFormOpen = false;
        state.signalDraft = null;
        state.signalError = null;
        startSignalsLoad(owner.item, owner.site);
        startDetailLoad(owner.item, owner.site);
        render();
      },
      function (error) {
        if (seq !== writeSeq || !stillOn(owner)) {
          return;
        }
        state.sendingSignal = false;
        state.signalError = signalErrorText(error);
        render();
      }
    );
  }

  // Withdraw the signal the open withdrawal form sits under:
  // POST /api/demand-signals/<id>/withdraw with the trimmed reason.
  // Same shape as a save: disabled while out, the mapped refusal in
  // the form with what was typed, a stale answer changes nothing.
  function submitSignalWithdraw() {
    if (
      state.admin !== true ||
      state.sendingSignalWithdraw ||
      state.openSignalWithdraw == null ||
      !state.detail
    ) {
      return;
    }
    var draft = state.signalWithdrawDraft || {};
    var reason = String(draft.reason == null ? '' : draft.reason).trim();
    if (reason === '') {
      return;
    }
    var rows = Array.isArray(state.signals) ? state.signals : [];
    var found = null;
    for (var i = 0; i < rows.length; i++) {
      if (
        rows[i] &&
        typeof rows[i] === 'object' &&
        String(rows[i].id) === state.openSignalWithdraw
      ) {
        found = rows[i];
        break;
      }
    }
    if (!found || found.id == null) {
      return;
    }
    var owner = { item: state.detail.item_code, site: state.detail.site_code };
    var rowId = String(found.id);
    var seq = ++writeSeq;
    state.sendingSignalWithdraw = true;
    state.signalWithdrawError = null;
    render();
    global.Api.request(
      '/api/demand-signals/' + encodeURIComponent(rowId) + '/withdraw',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: reason }),
      }
    ).then(
      function () {
        if (seq !== writeSeq || !stillOn(owner)) {
          return;
        }
        state.sendingSignalWithdraw = false;
        state.openSignalWithdraw = null;
        state.signalWithdrawDraft = null;
        state.signalWithdrawError = null;
        startSignalsLoad(owner.item, owner.site);
        startDetailLoad(owner.item, owner.site);
        render();
      },
      function (error) {
        if (seq !== writeSeq || !stillOn(owner)) {
          return;
        }
        state.sendingSignalWithdraw = false;
        state.signalWithdrawError = signalErrorText(error);
        render();
      }
    );
  }

  // Choosing a chip narrows the list without a request. When the
  // chosen row is hidden, the first row still listed becomes the
  // chosen one — the tint, the aria-current and the detail pane move.
  function setFilter(value) {
    if (value !== 'all') {
      var known = classCounts().some(function (entry) {
        return entry.value === value;
      });
      if (!known) {
        return;
      }
    }
    state.filter = value;
    var rows = visibleRows();
    var stillChosen =
      !!state.selected &&
      rows.some(
        function (row) {
          return sameCode(row.item_code, state.selected.item) && sameCode(row.site_code, state.selected.site);
        }
      );
    if (rows.length && (!state.selected || !stillChosen)) {
      var first = rows[0];
      state.selected = { item: first.item_code, site: first.site_code };
      closeOpenForms();
      resetSignalsState();
      startDetailLoad(first.item_code, first.site_code);
    } else if (!rows.length) {
      closeOpenForms();
      resetSignalsState();
      state.selected = null;
      state.detail = null;
      state.detailFor = null;
      state.detailLoading = false;
      detailSeq += 1;
      overridesSeq += 1;
      empiricalSeq += 1;
      state.empirical = null;
      state.empiricalFor = null;
      state.overrides = null;
      state.overridesLoading = false;
      state.overridesError = false;
    }
    state.scrollToTop = true;
    render();
  }

  /* -------------------------------- events ------------------------------ */

  function onRootClick(event) {
    var target = event.target;
    if (!target || !target.closest || !rootEl) {
      return;
    }
    if (!rootEl.contains(target)) {
      return;
    }
    var chip = target.closest('.d-chip');
    if (chip) {
      setFilter(chip.getAttribute('data-filter'));
      return;
    }
    // The admin override form: open it under a period row, cancel it
    // with nothing sent, save it.
    var overrideOpen = target.closest('[data-testid="override-open"]');
    if (overrideOpen) {
      openOverrideForm(overrideOpen.getAttribute('data-period-start'));
      return;
    }
    if (target.closest('[data-testid="override-cancel"]')) {
      closeOpenForms();
      render();
      return;
    }
    if (target.closest('[data-testid="override-save"]')) {
      submitOverride();
      return;
    }
    // The admin withdrawal form, opened under a stored row.
    var withdrawOpen = target.closest('[data-testid="override-withdraw"]');
    if (withdrawOpen) {
      openWithdrawForm(withdrawOpen.getAttribute('data-override-id'));
      return;
    }
    if (target.closest('[data-testid="withdraw-cancel"]')) {
      closeOpenForms();
      render();
      return;
    }
    if (target.closest('[data-testid="withdraw-save"]')) {
      submitWithdraw();
      return;
    }
    // The demand signals section (issue #965): the fetch is on
    // demand only — the button is the trigger, plus the reloads a
    // save's or a withdrawal's success sends out.
    if (target.closest('[data-testid="demand-signals-show"]')) {
      if (state.selected) {
        startSignalsLoad(state.selected.item, state.selected.site);
      }
      return;
    }
    if (target.closest('[data-testid="demand-signal-add"]')) {
      openSignalForm();
      return;
    }
    if (target.closest('[data-testid="demand-signal-cancel"]')) {
      cancelSignalForms();
      render();
      return;
    }
    if (target.closest('[data-testid="demand-signal-save"]')) {
      submitSignal();
      return;
    }
    var signalWithdrawOpen = target.closest('[data-testid="demand-signal-withdraw"]');
    if (signalWithdrawOpen) {
      openSignalWithdrawForm(signalWithdrawOpen.getAttribute('data-signal-id'));
      return;
    }
    if (target.closest('[data-testid="demand-signal-withdraw-cancel"]')) {
      cancelSignalForms();
      render();
      return;
    }
    if (target.closest('[data-testid="demand-signal-withdraw-confirm"]')) {
      submitSignalWithdraw();
      return;
    }
    var row = target.closest('.d-row');
    if (row) {
      selectRow(row.getAttribute('data-item'), row.getAttribute('data-site'));
      return;
    }
    if (target.closest('#demand-retry')) {
      startLoad();
      return;
    }
    if (target.closest('#demand-detail-retry')) {
      if (state.selected) {
        startDetailLoad(state.selected.item, state.selected.site);
      }
    }
  }

  // The draft fields update the state on every keystroke and re-evaluate
  // only their send button: a full re-render per key would drop the
  // caret, and nothing else on screen depends on the draft.
  function onRootInput(event) {
    var target = event.target;
    if (!target || !target.getAttribute || !rootEl || !rootEl.contains(target)) {
      return;
    }
    var testid = target.getAttribute('data-testid');
    if (testid === 'override-quantity' || testid === 'override-reason') {
      if (state.openOverride == null || !state.overrideDraft) {
        return;
      }
      if (testid === 'override-quantity') {
        state.overrideDraft.quantity = target.value;
      } else {
        state.overrideDraft.reason = target.value;
      }
      var save = rootEl.querySelector('[data-testid="override-save"]');
      if (save) {
        save.disabled = overrideSendDisabled();
      }
    } else if (testid === 'withdraw-reason') {
      if (state.openWithdraw == null || !state.withdrawDraft) {
        return;
      }
      state.withdrawDraft.reason = target.value;
      var withdraw = rootEl.querySelector('[data-testid="withdraw-save"]');
      if (withdraw) {
        withdraw.disabled = withdrawSendDisabled();
      }
    } else if (testid === 'demand-signal-kind') {
      if (!state.signalFormOpen || !state.signalDraft) {
        return;
      }
      state.signalDraft.kind = target.value;
    } else if (
      testid === 'demand-signal-start' ||
      testid === 'demand-signal-end' ||
      testid === 'demand-signal-quantity' ||
      testid === 'demand-signal-reason'
    ) {
      if (!state.signalFormOpen || !state.signalDraft) {
        return;
      }
      if (testid === 'demand-signal-start') {
        state.signalDraft.start = target.value;
      } else if (testid === 'demand-signal-end') {
        state.signalDraft.end = target.value;
      } else if (testid === 'demand-signal-quantity') {
        state.signalDraft.quantity = target.value;
      } else {
        state.signalDraft.reason = target.value;
      }
      var signalSave = rootEl.querySelector('[data-testid="demand-signal-save"]');
      if (signalSave) {
        signalSave.disabled = signalSaveDisabled();
      }
    } else if (testid === 'demand-signal-withdraw-reason') {
      if (state.openSignalWithdraw == null || !state.signalWithdrawDraft) {
        return;
      }
      state.signalWithdrawDraft.reason = target.value;
      var signalConfirm = rootEl.querySelector('[data-testid="demand-signal-withdraw-confirm"]');
      if (signalConfirm) {
        signalConfirm.disabled = signalWithdrawSaveDisabled();
      }
    }
  }

  /* -------------------------------- boot -------------------------------- */

  function activate() {
    var appEl = document.getElementById('app');
    if (!appEl || appEl.hidden || !demandActive()) {
      return;
    }
    if (state.ready || state.loading) {
      return;
    }
    startLoad();
  }

  function reset() {
    listSeq += 1;
    detailSeq += 1;
    overridesSeq += 1;
    signalsSeq += 1;
    empiricalSeq += 1;
    accuracySeq += 1;
    meSeq += 1;
    writeSeq += 1;
    state.accuracyAhead = null;
    state.accuracyAll = null;
    state.accuracyLoading = false;
    state.accuracyError = false;
    state.accuracySignals = null;
    state.accuracySignalsError = false;
    state.loading = false;
    state.ready = false;
    state.error = null;
    state.rows = [];
    state.filter = 'all';
    state.selected = null;
    state.detail = null;
    state.detailFor = null;
    state.detailLoading = false;
    state.detailError = null;
    state.overrides = null;
    state.overridesLoading = false;
    state.overridesError = false;
    state.empirical = null;
    state.empiricalFor = null;
    state.scrollToTop = false;
    state.admin = null;
    state.openOverride = null;
    state.overrideDraft = null;
    state.overrideError = null;
    state.sendingOverride = false;
    state.openWithdraw = null;
    state.withdrawDraft = null;
    state.withdrawError = null;
    state.sendingWithdraw = false;
    state.signals = null;
    state.signalsLoading = false;
    state.signalsError = null;
    state.signalsTried = false;
    state.signalFormOpen = false;
    state.signalDraft = null;
    state.signalError = null;
    state.sendingSignal = false;
    state.openSignalWithdraw = null;
    state.signalWithdrawDraft = null;
    state.signalWithdrawError = null;
    state.sendingSignalWithdraw = false;
    state.pendingFocus = null;
    if (rootEl) {
      rootEl.innerHTML = '';
    }
  }

  (function boot() {
    rootEl = document.getElementById('demand-root');
    if (!rootEl) {
      return;
    }
    rootEl.addEventListener('click', onRootClick);
    rootEl.addEventListener('input', onRootInput);

    var appEl = document.getElementById('app');
    if (!appEl) {
      return;
    }
    // The demand view loads once the signed-in shell is visible and the
    // demand view is the active one; the hashchange handler (app.js)
    // calls activate() on every switch to #/demand.
    var observer = new MutationObserver(function () {
      if (!appEl.hidden && demandActive()) {
        activate();
      }
    });
    observer.observe(appEl, { attributes: true, attributeFilter: ['hidden'] });
    if (!appEl.hidden && demandActive()) {
      activate();
    }
  })();

  global.Demand = {
    activate: activate,
    reset: reset,
  };
})(window);
