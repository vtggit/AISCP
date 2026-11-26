// AIERP planner screen — the Aggregate planning view (#/aggregate).
//
// #/aggregate is the read-only roll-forward: one block per site group,
// in the order GET /api/aggregate/projection returns them, each block
// labelled with the group's site code and showing the group's monthly
// buckets as columns — the rows Projected demand, Projected supply and
// Projected on-hand, in whole units or whole dollars per the toggle
// (on-hand has no dollar figure and reads as an em dash in dollars
// mode). A bucket the server marks projected_shortfall is flagged as a
// shortfall, and a bucket whose on-hand is at least three times its
// demand is flagged as excess; each flagged cell carries a leading
// marker (\u25bc shortfall, \u25b2 excess, drawn by CSS ::before so the
// figure is untouched) and the accessible name "Projected shortfall:
// <figure>" / "Projected excess: <figure>" in aria-label and title \u2014
// the figure is the units figure, so the name keeps its meaning while
// the cell reads as an em dash in dollars mode.
//
// The plan control ([data-testid="aggregate-plan-toggle"], "As is" by
// default / "With planned orders") re-renders the loaded answer
// without a new request: the on-hand rows become "Planned receipts"
// (planned_receipts_units, or planned_receipts_value as whole dollars
// in dollars mode) and "Planned on-hand" (planned_on_hand_units, an
// em dash in dollars mode as on-hand is today). The Planned on-hand
// cell carries the shortfall flag when the server marks
// planned_shortfall, and the excess flag when it is not short, the
// bucket's projected_demand_units is above zero and the planned
// on-hand is at least three times that demand \u2014 the same markers,
// classes, data-testids and accessible names as the as-is flags, the
// figure the planned on-hand in units, and the as-is on-hand flags
// never show in this view. A bucket whose planned fields are missing
// or not numbers reads an em dash in the planned cells and is never
// flagged. In the planned view a note under the legend
// ([data-testid="aggregate-plan-note"]) explains the planned orders;
// it does not show in As is. Below the controls a legend
// ([data-testid="aggregate-legend"]) gives each flag its swatch and
// plain-language meaning, shown whenever the grid has groups; above
// zero, each group notes the item-sites without a unit cost, excluded
// from the dollar figures.
//
// The months control ([data-testid="aggregate-months"], 3/6/9/12,
// default 6) re-fetches on change; the units/dollars toggle
// ([data-testid="aggregate-unit-toggle"]) switches the figures without
// a request, and the plan control switches the rows the same way \u2014
// the choice is kept across months re-fetches, and the select
// re-syncs to the state after every render. While a fetch is out a
// skeleton shows and the controls are disabled; every projection
// answer that is not the newest
// fetch's is ignored (a request sequence guard), so only the latest
// fetch's answer is rendered. Before any successful load — and on a
// load that returns no groups — an empty note shows; a failed load (or
// a malformed, non-object payload) shows an inline error with a Retry
// that re-fetches the current months.
//
// The scenario control ([data-testid="aggregate-scenario"], "Baseline"
// by default) offers one option per saved scenario, in the order
// GET /api/scenarios returns them: the option value the scenario's
// id, the text its name, inserted as text never HTML. The list is
// fetched once when the view activates; a failed or malformed answer
// leaves only "Baseline" and the grid keeps working. Choosing a
// scenario re-fetches the projection with each of that scenario's
// levers that is not null appended after months (service_level,
// demand_multiplier, lead_time_multiplier); choosing "Baseline"
// re-fetches with months only. The choice is kept when months
// changes (the re-fetch carries the same levers), Figures and Plan
// still switch without a request, and the select is disabled while a
// fetch is out and re-synced after every render like the other
// controls. While a scenario is chosen, a caption under the controls
// ([data-testid="aggregate-scenario-caption"]) names it and its
// levers: "Scenario: <name> \u2014 service level <p>%, demand <\u00b1q>%,
// lead time <\u00b1r>%", p the service level times 100 and q/r the
// multipliers minus one, times 100, each at most two decimals, a
// null lever left out; with Baseline no caption. A 422 from the
// projection shows the inline error with its Retry, the error text
// the server's detail string when there is one. Sign-out resets the
// choice to Baseline.
//
// The "Spend vs budget" block ([data-testid="aggregate-spend"])
// sits between the legend and notes and the site grid. Whenever
// the projection is fetched (activation, Months, Scenario, Retry)
// the block fetches GET /api/aggregate/spend with exactly the
// same query string, and its answer is guarded by its own
// request sequence, so only the newest spend answer renders and
// the two fetches race independently. Figures, Plan and the
// units/dollars toggle never re-fetch it. While the spend fetch
// is out the block shows a skeleton; a failed or malformed answer
// shows "Spend could not be loaded." with a Retry that re-fetches
// only the spend, leaving the site grid untouched. The Retry behaves
// as a button: click, Enter and Space all re-fetch only the spend,
// and Space's default page scroll is prevented (the control itself
// stays a span, never a control — the keys are delegated on the
// root like the click). The table
// carries one column per month in the answer's order, headed by
// the month as in the grid, and the rows Committed, Planned,
// Total, Budget and Remaining, every figure in whole dollars with
// thousands separators whatever the Figures toggle shows (Budget
// and Remaining an em dash when the server carries no figure). A
// month the server marks over_budget carries the over-budget mark
// on its Remaining cell ([data-testid="aggregate-over-budget"]):
// the shortfall's risk treatment, the leading marker (\u25bc)
// drawn by ::before so the figure is untouched, and the
// accessible name "Over budget by $<amount>" in aria-label and
// title, amount the absolute remaining in whole dollars. The note
// under the table ([data-testid="aggregate-spend-note"]) explains
// the planned spend; when every month's budget is null a second
// line says no purchasing budget is set, and when the answer's
// item_sites_without_unit_cost is above zero a third line says how
// many item-sites are left out.
//
// The "What to defer" block ([data-testid="aggregate-deferrals"])
// sits directly under the Spend vs budget block, and only while the
// latest successful spend answer has at least one month over budget
// \u2014 a failed or in-flight spend fetch produces no answer and
// leaves the condition untouched. The first such answer fetches GET
// /api/aggregate/deferrals with exactly the same query string as the
// spend and projection requests, and the block re-fetches whenever
// the spend re-fetches (never on Figures or Plan). The answer is
// guarded by its own request sequence, so only the newest answer
// renders. While the fetch is out the block shows a skeleton; a
// failed or malformed answer shows "Suggestions could not be
// loaded." with a real Retry button ("Retry suggestions") that
// re-fetches only the deferrals. Per over-budget month, in the
// answer's order, a section ([data-testid="aggregate-deferral-month"])
// carries the heading "<month>: over by $<overrun>", the summary
// "Defer $<value> across <n> order(s) to cover it." (or, with a
// still-uncovered amount, "...; $<uncovered> cannot be covered
// without a stockout."), a warning naming the following month when
// the server sets next_month_over_after
// ([data-testid="aggregate-deferral-warning"]), and a candidates
// table in the answer's order \u2014 Item \u00b7 site, Quantity,
// Value, Cover if deferred (days) and Status, whole dollars and
// whole units with separators, the cover at one decimal (an em dash
// when the server carries no figure), "Suggested" rows in the accent
// tokens ([data-testid="aggregate-deferral-suggested"]) and
// "Would stock out" rows in muted ink, the table scrolling within
// its own region. The spend block and the site grid are never
// affected by the block's state.
//
// The "Release to ERP" panel ([data-testid="aggregate-release"])
// sits directly under the "What to defer" block, and only in the
// "With planned orders" view. While a saved scenario is chosen it
// carries the baseline-only message and no controls: "Releasing
// uses the baseline plan. Switch Scenario to Baseline to release."
// Otherwise the month select ([data-testid="aggregate-release-month"],
// the current month and the next two as YYYY-MM labels, defaulting
// to the current month) and a real "Preview release" button
// ([data-testid="aggregate-release-preview"]) that POSTs
// /api/aggregate/release with {"month": <month>}. The answer renders
// a summary line ("<n> purchase orders, $<total_value> in total",
// the total in whole dollars from its decimal string), a table
// ([data-testid="aggregate-release-table"], scrolling within its own
// region) with one row per group — Warehouse (the warehouse code,
// else the site code), Vendor (the vendor code, else the supplier
// code), Lines (the line count), Value (whole dollars from the
// group's decimal string) and Status ("Will create" for
// would_create, "Already in ERP <order_nbr>" for exists,
// "Created <order_nbr>" for created, "Error: <error>" for error,
// empty when the outcome is null) — and, when the answer's
// unreleasable is non-empty, a note listing "<item_code> ·
// <site_code>" for each, with its reason. An answer with
// acumatica_available false carries the note "The ERP connection is
// not configured; this is the plan only." While the answer has at
// least one would_create group, a user whose roles include "admin"
// (GET /api/auth/me, once per activation) gets the "Release <k>
// purchase orders to the ERP" button ([data-testid=
// "aggregate-release-confirm"], "order" when k is 1); clicking it
// asks once more with an inline "Confirm release" / "Cancel" pair
// and nothing is sent until Confirm, which POSTs
// {"month": <month>, "confirm": true} and renders the answer the
// same way; a non-admin sees "Only an admin can release." instead
// of the button. Changing the month clears the shown answer. A
// failed request keeps the last shown answer and shows the
// server's detail inline for 409, 422, 502 and 503 and "The release
// could not be completed." for anything else. While a request is
// out the panel's controls are disabled, and a request-sequence
// guard renders only the newest answer. Choosing a different
// scenario or switching the plan view clears the shown answer the
// same way (the month select keeps its month), resets the panel's
// loading state and advances the release sequence, so a preview
// still in flight from before the change is ignored as stale.
//
//
// The view activates and fetches only once the signed-in shell is
// visible: the pre-sign-in activation (the shell shows the view named
// by the hash before Auth.bootstrap has completed) renders the
// controls and the empty note but sends no request, and a
// MutationObserver on #app runs the first load when sign-in shows the
// shell. The controls survive every innerHTML re-render: the events
// are delegated on #aggregate-root (change, click and keydown, bound
// once at boot) and the handlers mutate only module state and
// trigger a single render().
//
// Every string from the API is HTML-escaped before it reaches the
// page; colours, radii and steps come from app/css/tokens.css via
// app/css/aggregate.css.
(function (global) {
  'use strict';

  var PROJECTION_PATH = '/api/aggregate/projection';
  var SCENARIOS_PATH = '/api/scenarios';
  var SPEND_PATH = '/api/aggregate/spend';
  var DEFERRALS_PATH = '/api/aggregate/deferrals';
  var RELEASE_PATH = '/api/aggregate/release';
  var ME_PATH = '/api/auth/me';
  var EM_DASH = '\u2014';
  var ARROW_DOWN = '\u25bc'; // \u25bc the shortfall marker
  var ARROW_UP = '\u25b2'; // \u25b2 the excess marker
  var TIMES = '\u00d7'; // \u00d7 the excess rule ("at least 3\u00d7")
  var MONTH_OPTIONS = [3, 6, 9, 12];
  var DEFAULT_MONTHS = 6;

  // The metric rows of each plan view, in order; valueKey is the
  // dollar figure the server carries for the same bucket (on-hand and
  // planned on-hand have none: an em dash in dollars mode). The
  // "flags" key names which on-hand flags the row carries: 'as-is'
  // the projected on-hand flags, 'planned' the planned on-hand flags,
  // and null for the demand and supply rows.
  var METRICS_AS_IS = [
    {
      label: 'Projected demand',
      unitsKey: 'projected_demand_units',
      valueKey: 'projected_demand_value',
      flags: null,
    },
    {
      label: 'Projected supply',
      unitsKey: 'projected_supply_units',
      valueKey: 'projected_supply_value',
      flags: null,
    },
    {
      label: 'Projected on-hand',
      unitsKey: 'projected_on_hand_units',
      valueKey: null,
      flags: 'as-is',
    },
  ];

  var METRICS_PLANNED = [
    {
      label: 'Projected demand',
      unitsKey: 'projected_demand_units',
      valueKey: 'projected_demand_value',
      flags: null,
    },
    {
      label: 'Projected supply',
      unitsKey: 'projected_supply_units',
      valueKey: 'projected_supply_value',
      flags: null,
    },
    {
      label: 'Planned receipts',
      unitsKey: 'planned_receipts_units',
      valueKey: 'planned_receipts_value',
      flags: null,
    },
    {
      label: 'Planned on-hand',
      unitsKey: 'planned_on_hand_units',
      valueKey: null,
      flags: 'planned',
    },
  ];

  // The spend table's rows, in order: the month's committed and
  // planned spend, their total, the purchasing budget and what
  // remains under it. Every figure is in whole dollars with
  // thousands separators, whatever the Figures toggle shows.
  var SPEND_ROWS = [
    { label: 'Committed', key: 'committed_spend' },
    { label: 'Planned', key: 'planned_spend' },
    { label: 'Total', key: 'total_spend' },
    { label: 'Budget', key: 'budget' },
    { label: 'Remaining', key: 'remaining' },
  ];

  var state = {
    months: DEFAULT_MONTHS,
    unit: 'units', // 'units' | 'dollars'
    plan: 'as-is', // 'as-is' | 'planned'
    scenarioId: '', // the chosen saved scenario ('' = Baseline)
    groups: null, // the newest good payload (an array of groups)
    loading: false,
    error: null, // { status, message }
    spend: null, // the newest well-formed spend payload
    spendLoading: false,
    spendError: false,
    deferrals: null, // the newest well-formed deferrals payload
    deferralsLoading: false,
    deferralsError: false,
    releaseMonth: null, // the panel's month (null: the current month)
    releaseAnswer: null, // the newest well-formed release answer
    releaseLoading: false,
    releaseError: null, // the release request's failure message
    confirmAsk: false, // the inline Confirm release / Cancel pair is open
  };
  // The request sequence: every projection answer that is not the
  // newest fetch's is ignored, so a stale response can never change
  // what is rendered.
  var seq = 0;
  // The spend answer's own request sequence, guarded like the
  // projection's: a spend answer that is not the newest spend
  // fetch's is ignored, so the two fetches race independently.
  var spendSeq = 0;
  // Whether a spend fetch has ever gone out: before the first one
  // (the pre-sign-in render) the spend block does not show at all.
  var spendStarted = false;
  // The deferrals answer's own request sequence, guarded like the
  // spend's: a deferrals answer that is not the newest deferrals
  // fetch's is ignored, so only the newest answer renders.
  var defSeq = 0;
  // Whether the latest successful spend answer has at least one
  // month over budget: the "What to defer" block's visibility
  // condition. A failed or in-flight spend fetch produces no
  // answer and leaves it untouched.
  var spendHasOverBudget = false;

  // The release answer's own request sequence, guarded like
  // the deferrals': a release answer that is not the newest
  // release request's is ignored, so only the newest answer
  // renders.
  var releaseSeq = 0;
  // The viewer's admin role from GET /api/auth/me (once per
  // activation, its own sequence): null while the request is
  // out or before the first one — the release control shows
  // neither the button nor the note until the role is known;
  // a failed answer leaves the viewer read-only.
  var releaseAdmin = null;
  var releaseMeSeq = 0;

  // The saved-scenario list the Scenario control offers: the fetched
  // rows (held so a choice can be re-read across renders) and their
  // own request sequence, guarded like the projection's.
  var scenarioRows = [];
  var scenarioSeq = 0;

  var rootEl = null;

  var DOLLAR_FORMATTER = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  });

  /* ------------------------------ helpers ------------------------------ */

  var esc = window.Shared.esc;

  function isNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }

  // A well-formed spend answer: an object (never an array or a
  // bare value) whose months is an array — one entry per month
  // column, in the answer's order. Anything else is a failed
  // spend load.
  function spendValid(payload) {
    return (
      !!payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      Array.isArray(payload.months)
    );
  }

  // A well-formed deferrals answer: an object (never an array or a
  // bare value) whose months is an array — one entry per
  // over-budget month, in the answer's order. Anything else is a
  // failed deferrals load.
  function deferralsValid(payload) {
    return (
      !!payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      Array.isArray(payload.months)
    );
  }

  // True when at least one month of a spend answer is over budget:
  // the "What to defer" block's visibility condition.
  function anyMonthOverBudget(months) {
    for (var i = 0; i < months.length; i++) {
      var m = months[i] && typeof months[i] === 'object' ? months[i] : {};
      if (m.over_budget === true) {
        return true;
      }
    }
    return false;
  }

  // True when no month of the answer carries a budget figure:
  // the note's "no purchasing budget set" line shows.
  function allBudgetsNull(months) {
    if (months.length === 0) {
      return false;
    }
    for (var i = 0; i < months.length; i++) {
      var m = months[i] && typeof months[i] === 'object' ? months[i] : {};
      if (isNumber(m.budget)) {
        return false;
      }
    }
    return true;
  }

  // Whole numbers with thousands separators (10460.7 -> "10,461");
  // a missing or non-numeric value renders as an em dash.
  function wholeNumber(value) {
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return Math.round(value).toLocaleString('en-US');
  }

  // Whole dollars with thousands separators (10460.7 -> "$10,461").
  function wholeDollars(value) {
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return DOLLAR_FORMATTER.format(Math.round(value));
  }

  // The YYYY-MM label of the month ``offset`` months from the
  // current one (offset 0 the current month itself): the release
  // panel's month options are the current month and the next two.
  function monthLabel(offset) {
    var now = new Date();
    var index = now.getFullYear() * 12 + now.getMonth() + offset;
    var year = Math.floor(index / 12);
    var month = (index % 12) + 1;
    return String(year).padStart(4, '0') + '-' + String(month).padStart(2, '0');
  }

  // The release panel's month options: the current month and the
  // next two, as YYYY-MM labels.
  function releaseMonths() {
    return [monthLabel(0), monthLabel(1), monthLabel(2)];
  }

  // A well-formed release answer: an object (never an array or a
  // bare value) whose groups is an array — one entry per (site,
  // supplier) group, in the answer's order. Anything else is a
  // failed release.
  function releaseValid(payload) {
    return (
      !!payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      Array.isArray(payload.groups)
    );
  }

  // Whole dollars from a monetary figure the server carries as a
  // decimal string ("513.50" -> "$514"); a missing or unparsable
  // figure renders as an em dash.
  function moneyWholeDollars(value) {
    var figure = NaN;
    if (typeof value === 'number') {
      figure = value;
    } else if (typeof value === 'string') {
      var text = value.trim();
      figure = text === '' ? NaN : Number(text);
    }
    if (!isNumber(figure)) {
      return EM_DASH;
    }
    return DOLLAR_FORMATTER.format(Math.round(figure));
  }

  // The status cell of one release group, from its outcome: "Will
  // create" for would_create, "Already in ERP <order_nbr>" for
  // exists, "Created <order_nbr>" for created, "Error: <error>" for
  // error, and empty when the outcome is null (the bare plan, and a
  // preview with an incomplete Acumatica configuration).
  function releaseGroupStatus(group) {
    if (group.outcome === 'would_create') {
      return 'Will create';
    }
    if (group.outcome === 'exists') {
      return 'Already in ERP ' + (typeof group.order_nbr === 'string' ? group.order_nbr : '');
    }
    if (group.outcome === 'created') {
      return 'Created ' + (typeof group.order_nbr === 'string' ? group.order_nbr : '');
    }
    if (group.outcome === 'error') {
      return 'Error: ' + (typeof group.error === 'string' ? group.error : '');
    }
    return '';
  }


  // One grid cell: the bucket's figure for the metric in the unit the
  // toggle shows; on-hand has no dollar figure (an em dash in dollars
  // mode).
  function cellText(bucket, metric) {
    if (state.unit === 'dollars') {
      if (metric.valueKey === null) {
        return EM_DASH;
      }
      return wholeDollars(bucket[metric.valueKey]);
    }
    return wholeNumber(bucket[metric.unitsKey]);
  }

  // The on-hand flags of a bucket: a shortfall when the server marks
  // it (the rolled on-hand is below zero), an excess when the on-hand
  // is at least three times the demand of the same bucket.
  function onHandFlags(bucket) {
    var demand = isNumber(bucket.projected_demand_units) ? bucket.projected_demand_units : 0;
    var onHand = isNumber(bucket.projected_on_hand_units) ? bucket.projected_on_hand_units : 0;
    var shortfall = bucket.projected_shortfall === true;
    return {
      shortfall: shortfall,
      excess: !shortfall && demand > 0 && onHand >= 3 * demand,
    };
  }

  // The planned on-hand flags of a bucket: a shortfall when the server
  // marks planned_shortfall, an excess when it is not short, the
  // bucket's projected_demand_units is above zero and the planned
  // on-hand is at least three times that demand. A bucket whose
  // planned on-hand is missing or not a number is never flagged.
  function plannedOnHandFlags(bucket) {
    var hasPlannedOnHand = isNumber(bucket.planned_on_hand_units);
    var demand = isNumber(bucket.projected_demand_units) ? bucket.projected_demand_units : 0;
    var shortfall = bucket.planned_shortfall === true && hasPlannedOnHand;
    return {
      shortfall: shortfall,
      excess:
        !shortfall &&
        hasPlannedOnHand &&
        demand > 0 &&
        bucket.planned_on_hand_units >= 3 * demand,
    };
  }

  // The accessible name a flagged on-hand cell carries (aria-label and
  // title): "Projected shortfall: -150" / "Projected excess: 300". The
  // figure is the units figure \u2014 in dollars mode the cell itself reads
  // as an em dash, but the name keeps the units figure.
  function flagCellAttrs(kind, figure) {
    var note = kind + figure;
    return ' aria-label="' + esc(note) + '" title="' + esc(note) + '"';
  }

  /* ------------------------------ markup ------------------------------ */

  // The Scenario control's options: "Baseline" (the empty value, the
  // default) first, then one option per fetched saved scenario in the
  // order GET /api/scenarios returned them — the id as value, the
  // name as text (escaped, so a hostile name is text never HTML).
  function scenarioOptionsHtml() {
    var html = '<option value="">Baseline</option>';
    for (var i = 0; i < scenarioRows.length; i++) {
      var row = scenarioRows[i];
      html += '<option value="' + esc(row.id) + '">' + esc(row.name) + '</option>';
    }
    return html;
  }

  function controlsHtml() {
    var disabled = state.loading ? ' disabled' : '';
    var monthsOptions = '';
    for (var i = 0; i < MONTH_OPTIONS.length; i++) {
      var months = MONTH_OPTIONS[i];
      monthsOptions += '<option value="' + months + '">' + months + ' months</option>';
    }
    return (
      '<div class="ag-controls" data-testid="aggregate-controls">' +
      '<div class="ag-field">' +
      '<label class="ag-label" for="ag-months">Months</label>' +
      '<select class="ag-select" id="ag-months" data-testid="aggregate-months"' + disabled + '>' +
      monthsOptions +
      '</select>' +
      '</div>' +
      '<div class="ag-field">' +
      '<label class="ag-label" for="ag-unit">Figures</label>' +
      '<select class="ag-select" id="ag-unit" data-testid="aggregate-unit-toggle"' + disabled + '>' +
      '<option value="units">Units</option>' +
      '<option value="dollars">Dollars</option>' +
      '</select>' +
      '</div>' +
      '<div class="ag-field">' +
      '<label class="ag-label" for="ag-plan">Plan</label>' +
      '<select class="ag-select" id="ag-plan" data-testid="aggregate-plan-toggle"' + disabled + '>' +
      '<option value="as-is">As is</option>' +
      '<option value="planned">With planned orders</option>' +
      '</select>' +
      '</div>' +
      '<div class="ag-field">' +
      '<label class="ag-label" for="ag-scenario">Scenario</label>' +
      '<select class="ag-select" id="ag-scenario" data-testid="aggregate-scenario"' + disabled + '>' +
      scenarioOptionsHtml() +
      '</select>' +
      '</div>' +
      '</div>'
    );
  }

  // The legend: one swatch and label per flag, in the same tokens the
  // cells use. Shown below the controls whenever the grid has groups.
  function legendHtml() {
    return (
      '<div class="ag-legend" data-testid="aggregate-legend">' +
      '<span class="ag-legend-item">' +
      '<span class="ag-legend-swatch ag-legend-shortfall" aria-hidden="true">' +
        ARROW_DOWN +
      '</span>' +
      'Shortfall: projected on-hand below zero' +
      '</span>' +
      '<span class="ag-legend-item">' +
      '<span class="ag-legend-swatch ag-legend-excess" aria-hidden="true">' +
        ARROW_UP +
      '</span>' +
      'Excess: on-hand at least 3' + TIMES + ' the month\'s demand' +
      '</span>' +
      '</div>'
    );
  }

  // The plan note: what "With planned orders" means for the figures.
  // Shown under the legend, in the planned view only.
  function planNoteHtml() {
    return (
      '<p class="ag-plan-note" data-testid="aggregate-plan-note">' +
      'Planned orders: at each monthly review, an item-site whose stock position is below its reorder point plus that month\'s demand orders up to that level, arriving after its lead time.</p>'
    );
  }

  // The spend block's title row, shared by every state of the block.
  function spendTitleHtml() {
    return '<h2 class="ag-spend-title">Spend vs budget</h2>';
  }

  // The spend block's own skeleton: the spend fetch's loading
  // state, independent of the grid's.
  function spendSkeletonHtml() {
    return (
      '<section class="ag-spend" data-testid="aggregate-spend">' +
      spendTitleHtml() +
      '<div class="ag-skel-wrap" data-testid="aggregate-spend-skeleton" aria-hidden="true">' +
      '<div class="skel skel-line"></div>' +
      '<div class="skel skel-line skel-wide"></div>' +
      '<div class="skel ag-spend-skel-block"></div>' +
      '</div>' +
      '</section>'
    );
  }

  // The failed or malformed spend answer: the message and a Retry
  // that re-fetches only the spend — a span, never a button, so
  // the screen stays free of mutating controls; its button
  // behaviour (click, Enter and Space, Space's default scroll
  // prevented) is delegated on the root; the site grid is
  // untouched either way.
  function spendErrorHtml() {
    return (
      '<section class="ag-spend" data-testid="aggregate-spend">' +
      spendTitleHtml() +
      '<div class="ag-spend-error" role="alert" data-testid="aggregate-spend-error">' +
      '<p class="ag-spend-error-msg" data-testid="aggregate-spend-error-msg">Spend could not be loaded.</p>' +
      '<span class="ag-spend-retry" role="button" tabindex="0" data-testid="aggregate-spend-retry">Retry</span>' +
      '</div>' +
      '</section>'
    );
  }

  // The spend table: one column per month in the answer's order,
  // headed by the month as in the grid, the SPEND_ROWS rows in
  // order, every figure in whole dollars with thousands
  // separators whatever the Figures toggle shows (a missing
  // figure reads as an em dash). A month the server marks
  // over_budget carries the over-budget mark on its Remaining
  // cell ([data-testid="aggregate-over-budget"]): the shortfall's
  // risk treatment, the leading marker (\u25bc) drawn by ::before so
  // the figure is untouched, and the accessible name "Over budget
  // by $<amount>" in aria-label and title, amount the absolute
  // remaining in whole dollars.
  function spendTableHtml(payload) {
    var months = payload.months;
    var head = '<tr><th scope="col" class="ag-corner">Month</th>';
    for (var i = 0; i < months.length; i++) {
      var headMonth = months[i] && typeof months[i] === 'object' ? months[i] : {};
      head +=
        '<th scope="col" class="ag-num">' +
        esc(typeof headMonth.month === 'string' ? headMonth.month : '') +
        '</th>';
    }
    head += '</tr>';

    var body = '';
    for (var r = 0; r < SPEND_ROWS.length; r++) {
      var rowDef = SPEND_ROWS[r];
      var row = '<tr><th scope="row" class="ag-rowhead">' + rowDef.label + '</th>';
      for (var j = 0; j < months.length; j++) {
        var month = months[j] && typeof months[j] === 'object' ? months[j] : {};
        var figure = wholeDollars(month[rowDef.key]);
        var cellClass = 'ag-num';
        var cellTestId = '';
        var cellFlagAttrs = '';
        if (rowDef.key === 'remaining' && month.over_budget === true) {
          var amount = isNumber(month.remaining)
            ? Math.abs(Math.round(month.remaining))
            : 0;
          var note = 'Over budget by $' + amount.toLocaleString('en-US');
          cellClass += ' ag-spend-over';
          cellTestId = ' data-testid="aggregate-over-budget"';
          cellFlagAttrs = ' aria-label="' + esc(note) + '" title="' + esc(note) + '"';
        }
        row +=
          '<td class="' +
          cellClass +
          '"' +
          cellTestId +
          cellFlagAttrs +
          '>' +
          esc(figure) +
          '</td>';
      }
      row += '</tr>';
      body += row;
    }

    var notes =
      '<div class="ag-spend-notes" data-testid="aggregate-spend-note">' +
      '<p class="ag-spend-line">Planned spend counts planned orders at cost in the month they arrive.</p>';
    if (allBudgetsNull(months)) {
      notes +=
        '<p class="ag-spend-line">No purchasing budget set ' + EM_DASH +
        ' an admin can set one in Settings.</p>';
    }
    var withoutCost = isNumber(payload.item_sites_without_unit_cost)
      ? Math.round(payload.item_sites_without_unit_cost)
      : 0;
    if (withoutCost > 0) {
      notes +=
        '<p class="ag-spend-line">' +
        wholeNumber(withoutCost) +
        ' item-sites have no unit cost and are left out.</p>';
    }
    notes += '</div>';

    return (
      '<section class="ag-spend" data-testid="aggregate-spend">' +
      spendTitleHtml() +
      '<div class="ag-table-wrap ag-spend-wrap">' +
      '<table class="ag-table ag-spend-table">' +
      '<thead>' + head + '</thead>' +
      '<tbody>' + body + '</tbody>' +
      '</table>' +
      '</div>' +
      notes +
      '</section>'
    );
  }

  // The spend block's state: hidden before the first fetch (the
  // pre-sign-in render), a skeleton while the spend fetch is out,
  // its own error (with its own Retry) on a failed or malformed
  // answer, and the table with its notes otherwise.
  function spendHtml() {
    if (!spendStarted) {
      return '';
    }
    if (state.spendLoading) {
      return spendSkeletonHtml();
    }
    if (state.spendError) {
      return spendErrorHtml();
    }
    if (!spendValid(state.spend)) {
      return '';
    }
    return spendTableHtml(state.spend);
  }

  // The "What to defer" block's title row, shared by every state of
  // the block.
  function deferralsTitleHtml() {
    return '<h2 class="ag-deferrals-title">What to defer</h2>';
  }

  // The block's own skeleton: the deferrals fetch's loading state,
  // independent of the spend block's and the grid's.
  function deferralsSkeletonHtml() {
    return (
      '<section class="ag-deferrals" data-testid="aggregate-deferrals">' +
      deferralsTitleHtml() +
      '<div class="ag-skel-wrap" data-testid="aggregate-deferrals-skeleton" aria-hidden="true">' +
      '<div class="skel skel-line"></div>' +
      '<div class="skel skel-line skel-wide"></div>' +
      '<div class="skel ag-deferrals-skel-block"></div>' +
      '</div>' +
      '</section>'
    );
  }

  // The failed or malformed deferrals answer: the message and a real
  // Retry button that re-fetches only the deferrals — the spend
  // block and the site grid are untouched either way.
  function deferralsErrorHtml() {
    return (
      '<section class="ag-deferrals" data-testid="aggregate-deferrals">' +
      deferralsTitleHtml() +
      '<div class="ag-deferrals-error" role="alert" data-testid="aggregate-deferrals-error">' +
      '<p class="ag-deferrals-error-msg" data-testid="aggregate-deferrals-error-msg">Suggestions could not be loaded.</p>' +
      '<button type="button" class="btn btn-secondary ag-deferrals-retry" data-testid="aggregate-deferrals-retry">Retry suggestions</button>' +
      '</div>' +
      '</section>'
    );
  }

  // The candidates of one over-budget month, in the answer's order.
  // Item codes and site codes are escaped to text; the figures are
  // whole units or whole dollars with separators, and the cover
  // reads one decimal (an em dash when the server carries no
  // figure).
  function deferralTableHtml(candidates) {
    var head =
      '<tr>' +
      '<th scope="col">Item \u00b7 site</th>' +
      '<th scope="col" class="ag-num">Quantity</th>' +
      '<th scope="col" class="ag-num">Value</th>' +
      '<th scope="col" class="ag-num">Cover if deferred (days)</th>' +
      '<th scope="col">Status</th>' +
      '</tr>';

    var body = '';
    for (var i = 0; i < candidates.length; i++) {
      var c = candidates[i] && typeof candidates[i] === 'object' ? candidates[i] : {};
      var item = typeof c.item_code === 'string' ? c.item_code : '';
      var site = typeof c.site_code === 'string' ? c.site_code : '';
      var cover = isNumber(c.cover_days_if_deferred) ? c.cover_days_if_deferred.toFixed(1) : EM_DASH;
      var rowClass = '';
      var rowTestId = '';
      var status = '';
      if (c.suggested === true) {
        rowClass = 'ag-deferral-suggested';
        rowTestId = ' data-testid="aggregate-deferral-suggested"';
        status = 'Suggested';
      } else if (c.would_stock_out === true) {
        rowClass = 'ag-deferral-stockout';
        status = 'Would stock out';
      }
      body +=
        '<tr class="' + rowClass + '"' + rowTestId +
        '><td>' +
        esc(item) + ' \u00b7 ' + esc(site) +
        '</td>' +
        '<td class="ag-num">' + esc(wholeNumber(c.quantity)) + '</td>' +
        '<td class="ag-num">' + esc(wholeDollars(c.value)) + '</td>' +
        '<td class="ag-num">' + esc(cover) + '</td>' +
        '<td>' + esc(status) + '</td>' +
        '</tr>';
    }

    return (
      '<div class="ag-table-wrap ag-deferral-wrap">' +
      '<table class="ag-table ag-deferral-table">' +
      '<thead>' + head + '</thead>' +
      '<tbody>' + body + '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  // One over-budget month of the answer: its heading ("<month>:
  // over by $<overrun>"), its summary line, its following-month
  // warning (when the server sets next_month_over_after) and its
  // candidates table.
  function deferralMonthHtml(month) {
    var m = month && typeof month === 'object' ? month : {};
    var name = typeof m.month === 'string' ? m.month : '';
    var candidates = Array.isArray(m.candidates) ? m.candidates : [];
    var suggestedCount = 0;
    for (var i = 0; i < candidates.length; i++) {
      var c = candidates[i] && typeof candidates[i] === 'object' ? candidates[i] : {};
      if (c.suggested === true) {
        suggestedCount += 1;
      }
    }
    var orders = suggestedCount === 1 ? 'order' : 'orders';
    var summary =
      'Defer ' +
      wholeDollars(m.suggested_value) +
      ' across ' +
      suggestedCount +
      ' ' +
      orders +
      (isNumber(m.uncovered) && m.uncovered === 0
        ? ' to cover it.'
        : '; ' + wholeDollars(m.uncovered) + ' cannot be covered without a stockout.');

    var html =
      '<section class="ag-deferral-month" data-testid="aggregate-deferral-month">' +
      '<h3 class="ag-deferral-month-title">' +
      esc(name) +
      ': over by ' +
      esc(wholeDollars(m.overrun)) +
      '</h3>' +
      '<p class="ag-deferral-summary" data-testid="aggregate-deferral-summary">' +
      esc(summary) +
      '</p>';

    if (m.next_month_over_after === true) {
      var nextMonth = typeof m.next_month === 'string' ? m.next_month : '';
      html +=
        '<p class="ag-deferral-warning" data-testid="aggregate-deferral-warning">' +
        'Moving this spend into ' +
        esc(nextMonth) +
        ' puts that month over its budget.</p>';
    }

    html += deferralTableHtml(candidates);
    return html + '</section>';
  }

  // The "What to defer" block: directly under the Spend vs budget
  // block, only while the latest spend answer has an over-budget
  // month. A skeleton while the deferrals fetch is out, its own
  // error (with its own Retry suggestions) on a failed or malformed
  // answer, and one section per over-budget month in the answer's
  // order otherwise.
  function deferralsHtml() {
    if (!spendHasOverBudget) {
      return '';
    }
    if (state.deferralsLoading) {
      return deferralsSkeletonHtml();
    }
    if (state.deferralsError) {
      return deferralsErrorHtml();
    }
    if (!deferralsValid(state.deferrals)) {
      return '';
    }
    var months = state.deferrals.months;
    var html = '';
    for (var i = 0; i < months.length; i++) {
      html += deferralMonthHtml(months[i]);
    }
    return (
      '<section class="ag-deferrals" data-testid="aggregate-deferrals">' +
      deferralsTitleHtml() +
      html +
      '</section>'
    );
  }


  /* ---------------------------- release panel -------------------------- */

  // The panel's title row, shared by every state of the panel.
  function releaseTitleHtml() {
    return '<h2 class="ag-release-title">Release to ERP</h2>';
  }

  // The groups table: one row per group in the answer's order —
  // Warehouse (the warehouse code, else the site code), Vendor (the
  // vendor code, else the supplier code), Lines (the line count),
  // Value (whole dollars from the group's decimal string) and the
  // outcome's Status. Every server string is escaped to text, never
  // markup, and the table scrolls within its own region.
  function releaseTableHtml(groups) {
    var head =
      '<tr>' +
      '<th scope="col">Warehouse</th>' +
      '<th scope="col">Vendor</th>' +
      '<th scope="col" class="ag-num">Lines</th>' +
      '<th scope="col" class="ag-num">Value</th>' +
      '<th scope="col">Status</th>' +
      '</tr>';

    var body = '';
    for (var i = 0; i < groups.length; i++) {
      var group = groups[i] && typeof groups[i] === 'object' ? groups[i] : {};
      var lines = Array.isArray(group.lines) ? group.lines : [];
      var warehouse =
        typeof group.warehouse_code === 'string' && group.warehouse_code !== ''
          ? group.warehouse_code
          : typeof group.site_code === 'string'
            ? group.site_code
            : '';
      var vendor =
        typeof group.vendor_code === 'string' && group.vendor_code !== ''
          ? group.vendor_code
          : typeof group.supplier_code === 'string'
            ? group.supplier_code
            : '';
      body +=
        '<tr>' +
        '<td>' + esc(warehouse) + '</td>' +
        '<td>' + esc(vendor) + '</td>' +
        '<td class="ag-num">' + lines.length + '</td>' +
        '<td class="ag-num">' + esc(moneyWholeDollars(group.value)) + '</td>' +
        '<td>' + esc(releaseGroupStatus(group)) + '</td>' +
        '</tr>';
    }

    return (
      '<div class="ag-table-wrap ag-release-wrap">' +
      '<table class="ag-table ag-release-table" data-testid="aggregate-release-table">' +
      '<thead>' + head + '</thead>' +
      '<tbody>' + body + '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  // The release control under the answer: while the newest answer
  // has at least one would_create group, an admin gets the "Release
  // <k> purchase orders to the ERP" button (the inline Confirm
  // release / Cancel pair once it is clicked — nothing is sent until
  // Confirm) and a non-admin the note that only an admin can
  // release; while the role is still unknown, neither shows.
  function releaseConfirmHtml(answer) {
    var wouldCreate = 0;
    for (var i = 0; i < answer.groups.length; i++) {
      var group = answer.groups[i] && typeof answer.groups[i] === 'object' ? answer.groups[i] : {};
      if (group.outcome === 'would_create') {
        wouldCreate += 1;
      }
    }
    if (wouldCreate === 0) {
      return '';
    }
    if (releaseAdmin === false) {
      return (
        '<p class="ag-release-admin-note" data-testid="aggregate-release-admin-note">' +
        'Only an admin can release.</p>'
      );
    }
    if (releaseAdmin !== true) {
      return ''; // the role is still unknown: neither the button nor the note
    }
    var noun = wouldCreate === 1 ? 'order' : 'orders';
    if (state.confirmAsk) {
      return (
        '<div class="ag-release-confirm-ask" data-testid="aggregate-release-confirm-ask">' +
        '<span class="ag-release-confirm-text">Release ' +
        wouldCreate +
        ' purchase ' +
        noun +
        ' to the ERP?</span>' +
        '<button type="button" class="btn btn-primary" data-testid="aggregate-release-confirm-yes">Confirm release</button>' +
        '<button type="button" class="btn btn-secondary" data-testid="aggregate-release-confirm-no">Cancel</button>' +
        '</div>'
      );
    }
    return (
      '<button type="button" class="btn btn-primary" data-testid="aggregate-release-confirm">' +
      'Release ' +
      wouldCreate +
      ' purchase ' +
      noun +
      ' to the ERP</button>'
    );
  }

  // The newest answer: the summary line, the groups table, the
  // not-configured note (when acumatica_available is false), the
  // unreleasable note (when the answer carries one) and the release
  // control.
  function releaseAnswerHtml(answer) {
    var groups = answer.groups;
    var orders = groups.length === 1 ? 'order' : 'orders';
    var html =
      '<p class="ag-release-summary" data-testid="aggregate-release-summary">' +
      groups.length +
      ' purchase ' +
      orders +
      ', ' +
      esc(moneyWholeDollars(answer.total_value)) +
      ' in total</p>' +
      releaseTableHtml(groups);
    if (answer.acumatica_available === false) {
      html +=
        '<p class="ag-release-note" data-testid="aggregate-release-not-configured">' +
        'The ERP connection is not configured; this is the plan only.</p>';
    }
    var unreleasable = Array.isArray(answer.unreleasable) ? answer.unreleasable : [];
    if (unreleasable.length > 0) {
      var items = [];
      for (var i = 0; i < unreleasable.length; i++) {
        var item = unreleasable[i] && typeof unreleasable[i] === 'object' ? unreleasable[i] : {};
        var code = typeof item.item_code === 'string' ? item.item_code : '';
        var site = typeof item.site_code === 'string' ? item.site_code : '';
        var reason = typeof item.reason === 'string' ? item.reason : '';
        items.push(esc(code) + ' \u00b7 ' + esc(site) + (reason !== '' ? ': ' + esc(reason) : ''));
      }
      html +=
        '<p class="ag-release-unreleasable" data-testid="aggregate-release-unreleasable">' +
        'Cannot release: ' +
        items.join('; ') +
        '.</p>';
    }
    html += releaseConfirmHtml(answer);
    return html;
  }

  // The "Release to ERP" panel: directly under the "What to defer"
  // block, in the "With planned orders" view only. While a saved
  // scenario is chosen it carries the baseline-only message and no
  // controls; otherwise the month select (the current month and the
  // next two, defaulting to the current month) and the Preview
  // release button, and — once a request has answered — the
  // answer's summary, table and notes and the release control. A
  // failed request keeps the last shown answer and shows its own
  // error. While a request is out (or the inline confirm ask is
  // open) the panel's controls are disabled.
  function releaseHtml() {
    if (state.plan !== 'planned') {
      return '';
    }
    var html = '<section class="ag-release" data-testid="aggregate-release">' + releaseTitleHtml();
    if (state.scenarioId !== '') {
      html +=
        '<p class="ag-release-baseline-note" data-testid="aggregate-release-baseline-note">' +
        'Releasing uses the baseline plan. Switch Scenario to Baseline to release.</p>';
      return html + '</section>';
    }
    var months = releaseMonths();
    if (typeof state.releaseMonth !== 'string' || months.indexOf(state.releaseMonth) === -1) {
      state.releaseMonth = months[0];
    }
    var disabled = state.releaseLoading || state.confirmAsk ? ' disabled' : '';
    var options = '';
    for (var i = 0; i < months.length; i++) {
      options +=
        '<option value="' +
        esc(months[i]) +
        '"' +
        (state.releaseMonth === months[i] ? ' selected' : '') +
        '>' +
        months[i] +
        '</option>';
    }
    html +=
      '<div class="ag-release-controls">' +
      '<div class="ag-field">' +
      '<label class="ag-label" for="ag-release-month">Month</label>' +
      '<select class="ag-select" id="ag-release-month" data-testid="aggregate-release-month"' +
      disabled +
      '>' +
      options +
      '</select>' +
      '</div>' +
      '<div class="ag-field">' +
      '<button type="button" class="btn btn-primary" data-testid="aggregate-release-preview"' +
      disabled +
      '>Preview release</button>' +
      '</div>' +
      '</div>';
    if (state.releaseLoading) {
      html +=
        '<p class="ag-release-note" data-testid="aggregate-release-loading">Preparing the release\u2026</p>';
    } else {
      if (state.releaseError) {
        html +=
          '<p class="ag-release-error" role="alert" data-testid="aggregate-release-error">' +
          esc(state.releaseError) +
          '</p>';
      }
      if (releaseValid(state.releaseAnswer)) {
        html += releaseAnswerHtml(state.releaseAnswer);
      }
    }
    return html + '</section>';
  }

  // A lever figure rounded to at most two decimals, trailing zeros
  // dropped (98 -> "98", 19.5 -> "19.5", 19.999999 -> "20").
  function percentFigure(value) {
    return String(Math.round(value * 100) / 100);
  }

  // A signed lever figure for the caption: "+20", "-15", "+0".
  function signedPercentFigure(value) {
    var rounded = Math.round(value * 100) / 100;
    return (rounded < 0 ? '' : '+') + String(rounded);
  }

  // The scenario caption: the line under the controls naming the
  // chosen scenario and its levers — "Scenario: Peak +20% \u2014 service
  // level 98%, demand +20%" — a null lever left out, the name escaped
  // to text. No caption while Baseline is chosen.
  function scenarioCaptionHtml() {
    var scenario = findScenario(state.scenarioId);
    if (!scenario) {
      return '';
    }
    var parts = [];
    if (isNumber(scenario.service_level)) {
      parts.push('service level ' + percentFigure(scenario.service_level * 100) + '%');
    }
    if (isNumber(scenario.demand_multiplier)) {
      parts.push('demand ' + signedPercentFigure((scenario.demand_multiplier - 1) * 100) + '%');
    }
    if (isNumber(scenario.lead_time_multiplier)) {
      parts.push('lead time ' + signedPercentFigure((scenario.lead_time_multiplier - 1) * 100) + '%');
    }
    var text =
      'Scenario: ' +
      scenario.name +
      (parts.length ? ' ' + EM_DASH + ' ' + parts.join(', ') : '');
    return (
      '<p class="ag-scenario-caption" data-testid="aggregate-scenario-caption">' +
      esc(text) +
      '</p>'
    );
  }

  function skeletonHtml() {
    return (
      '<div class="ag-skel-wrap" data-testid="aggregate-skeleton" aria-hidden="true">' +
      '<div class="skel skel-line"></div>' +
      '<div class="skel skel-line skel-wide"></div>' +
      '<div class="skel ag-skel-block"></div>' +
      '</div>'
    );
  }

  function errorHtml() {
    return (
      '<div class="ag-error" role="alert" data-testid="aggregate-error">' +
      '<p class="ag-error-msg" data-testid="aggregate-error-msg">' + esc(state.error.message) + '</p>' +
      '<button type="button" class="btn btn-secondary ag-retry" data-testid="aggregate-retry">Retry</button>' +
      '</div>'
    );
  }

  function emptyHtml() {
    return '<p class="ag-empty" data-testid="aggregate-empty">No projected groups to show yet.</p>';
  }

  function exclusionNote(count) {
    if (count <= 0) {
      return '';
    }
    return (
      '<p class="ag-exclusion" data-testid="aggregate-exclusion-note">' +
      wholeNumber(count) +
      ' item-site' +
      (count === 1 ? ' is' : 's are') +
      ' excluded from the dollar figures.</p>'
    );
  }

  function groupHtml(group) {
    var key =
      group && typeof group === 'object' && group.group && typeof group.group === 'object'
        ? group.group
        : {};
    var code = typeof key.code === 'string' ? key.code : '';
    var buckets = group && Array.isArray(group.buckets) ? group.buckets : [];
    var withoutCost =
      group && isNumber(group.item_sites_without_unit_cost)
        ? Math.round(group.item_sites_without_unit_cost)
        : 0;

    var head = '<tr><th scope="col" class="ag-corner">Month</th>';
    for (var i = 0; i < buckets.length; i++) {
      var headBucket = buckets[i] && typeof buckets[i] === 'object' ? buckets[i] : {};
      head +=
        '<th scope="col" class="ag-num">' +
        esc(typeof headBucket.month === 'string' ? headBucket.month : '') +
        '</th>';
    }
    head += '</tr>';

    var body = '';
    var metrics = state.plan === 'planned' ? METRICS_PLANNED : METRICS_AS_IS;
    for (var m = 0; m < metrics.length; m++) {
      var metric = metrics[m];
      var row = '<tr><th scope="row" class="ag-rowhead">' + metric.label + '</th>';
      for (var j = 0; j < buckets.length; j++) {
        var bucket = buckets[j] && typeof buckets[j] === 'object' ? buckets[j] : {};
        var cellClass = 'ag-num';
        var cellTestId = '';
        var cellFlagAttrs = '';
        if (metric.flags === 'as-is') {
          var flags = onHandFlags(bucket);
          var onHandFigure = wholeNumber(bucket.projected_on_hand_units);
          if (flags.shortfall) {
            cellClass += ' ag-cell-shortfall';
            cellTestId = ' data-testid="aggregate-shortfall"';
            cellFlagAttrs = flagCellAttrs('Projected shortfall: ', onHandFigure);
          } else if (flags.excess) {
            cellClass += ' ag-cell-excess';
            cellTestId = ' data-testid="aggregate-excess"';
            cellFlagAttrs = flagCellAttrs('Projected excess: ', onHandFigure);
          }
        } else if (metric.flags === 'planned') {
          var plannedFlags = plannedOnHandFlags(bucket);
          var plannedFigure = wholeNumber(bucket.planned_on_hand_units);
          if (plannedFlags.shortfall) {
            cellClass += ' ag-cell-shortfall';
            cellTestId = ' data-testid="aggregate-shortfall"';
            cellFlagAttrs = flagCellAttrs('Projected shortfall: ', plannedFigure);
          } else if (plannedFlags.excess) {
            cellClass += ' ag-cell-excess';
            cellTestId = ' data-testid="aggregate-excess"';
            cellFlagAttrs = flagCellAttrs('Projected excess: ', plannedFigure);
          }
        }
        row +=
          '<td class="' +
          cellClass +
          '"' +
          cellTestId +
          cellFlagAttrs +
          '>' +
          esc(cellText(bucket, metric)) +
          '</td>';
      }
      row += '</tr>';
      body += row;
    }

    return (
      '<section class="ag-group" data-testid="aggregate-group" aria-label="Site ' + esc(code) + '">' +
      '<h2 class="ag-group-title">' + esc(code) + '</h2>' +
      '<div class="ag-table-wrap">' +
      '<table class="ag-table">' +
      '<thead>' + head + '</thead>' +
      '<tbody>' + body + '</tbody>' +
      '</table>' +
      '</div>' +
      exclusionNote(withoutCost) +
      '</section>'
    );
  }

  function gridHtml() {
    var html = '';
    for (var i = 0; i < state.groups.length; i++) {
      var group = state.groups[i] && typeof state.groups[i] === 'object' ? state.groups[i] : {};
      html += groupHtml(group);
    }
    return '<div class="ag-grid" data-testid="aggregate-grid">' + html + '</div>';
  }

  // The body region: skeleton while a fetch is out, the inline error
  // (with its Retry) on a failure, the empty note before any load or
  // on a load that returned no groups, and the grid otherwise — all
  // inside the same bounded scroll box, so the view owns the height.
  function bodyHtml() {
    var content;
    if (state.loading) {
      content = skeletonHtml();
    } else if (state.error) {
      content = errorHtml();
    } else if (!state.groups || state.groups.length === 0) {
      content = emptyHtml();
    } else {
      content = gridHtml();
    }
    return '<div class="ag-grid-region">' + content + '</div>';
  }

  function render() {
    if (!rootEl) {
      return;
    }
    // A choice that outlived the fetched list (a row left it) goes
    // back to Baseline before the caption and the options are built.
    dropStaleScenarioChoice();
    // The caption shows below the controls while a scenario is chosen,
    // whatever the grid shows; the legend follows it, below the
    // controls, whenever the grid has groups — never with the
    // skeleton, the error or the empty note; the plan note follows
    // the legend, in the planned view only.
    var hasGroups =
      !state.loading && !state.error && !!state.groups && state.groups.length > 0;
    rootEl.innerHTML =
      controlsHtml() +
      scenarioCaptionHtml() +
      (hasGroups ? legendHtml() : '') +
      (hasGroups && state.plan === 'planned' ? planNoteHtml() : '') +
      spendHtml() +
      deferralsHtml() +
      releaseHtml() +
      bodyHtml();
    // The selects were just replaced: re-sync them to the module
    // state so a re-render never resets a control the user chose.
    var monthsEl = rootEl.querySelector('[data-testid="aggregate-months"]');
    if (monthsEl) {
      monthsEl.value = String(state.months);
    }
    var unitEl = rootEl.querySelector('[data-testid="aggregate-unit-toggle"]');
    if (unitEl) {
      unitEl.value = state.unit;
    }
    var planEl = rootEl.querySelector('[data-testid="aggregate-plan-toggle"]');
    if (planEl) {
      planEl.value = state.plan;
    }
    var scenarioEl = rootEl.querySelector('[data-testid="aggregate-scenario"]');
    if (scenarioEl) {
      scenarioEl.value = state.scenarioId;
    }
  }

  // The fetched row the control's value names, or null (Baseline, or
  // an id the fetched list does not offer).
  function findScenario(id) {
    if (id === '') {
      return null;
    }
    for (var i = 0; i < scenarioRows.length; i++) {
      if (scenarioRows[i].id === id) {
        return scenarioRows[i];
      }
    }
    return null;
  }

  // A choice whose row left the fetched list goes back to Baseline.
  function dropStaleScenarioChoice() {
    if (state.scenarioId !== '' && findScenario(state.scenarioId) === null) {
      state.scenarioId = '';
    }
  }

  // GET /api/scenarios, once per activation, guarded by its own
  // sequence: a failed or malformed answer leaves the control on
  // "Baseline" only, and a choice that outlived the answer goes back
  // to Baseline — the grid keeps working either way.
  function fetchScenarios() {
    scenarioSeq += 1;
    var mySeq = scenarioSeq;
    return global.Api.get(SCENARIOS_PATH).then(
      function (payload) {
        if (mySeq !== scenarioSeq) {
          return; // a newer list fetch was started: this answer is stale
        }
        var rows = [];
        if (Array.isArray(payload)) {
          for (var i = 0; i < payload.length; i++) {
            var row = payload[i];
            if (
              row &&
              typeof row === 'object' &&
              typeof row.id === 'string' &&
              row.id !== '' &&
              typeof row.name === 'string'
            ) {
              rows.push(row);
            }
          }
        }
        scenarioRows = rows;
        dropStaleScenarioChoice();
        render();
      },
      function (error) {
        if (mySeq !== scenarioSeq) {
          return;
        }
        if (error && error.unauthorized) {
          return; // a 401 is the global auth flow's (the sign-out)
        }
        scenarioRows = [];
        dropStaleScenarioChoice();
        render();
      }
    );
  }

  /* ------------------------------- fetch ------------------------------- */

  // The projection's query string: months first, then each of the
  // chosen scenario's levers that is not null, in the order
  // service_level, demand_multiplier, lead_time_multiplier — Baseline
  // sends months only.
  function projectionQuery() {
    var query = '?months=' + state.months;
    var scenario = findScenario(state.scenarioId);
    if (scenario) {
      if (isNumber(scenario.service_level)) {
        query += '&service_level=' + encodeURIComponent(scenario.service_level);
      }
      if (isNumber(scenario.demand_multiplier)) {
        query += '&demand_multiplier=' + encodeURIComponent(scenario.demand_multiplier);
      }
      if (isNumber(scenario.lead_time_multiplier)) {
        query += '&lead_time_multiplier=' + encodeURIComponent(scenario.lead_time_multiplier);
      }
    }
    return query;
  }

  function load() {
    var query = projectionQuery(); // the string both fetches carry
    state.loading = true;
    state.error = null;
    seq += 1;
    var mySeq = seq;
    render();
    loadSpend(query); // the spend block rides every projection fetch
    global.Api.get(PROJECTION_PATH + query).then(
      function (payload) {
        if (mySeq !== seq) {
          return; // a newer fetch was started: this answer is stale
        }
        if (!Array.isArray(payload)) {
          // A malformed (non-object) payload is a failed load: the
          // inline error with its Retry, never the empty note.
          state.groups = null;
          state.loading = false;
          state.error = { status: 0, message: 'The projection could not be loaded.' };
          render();
          return;
        }
        state.groups = payload;
        state.loading = false;
        render();
      },
      function (error) {
        if (mySeq !== seq) {
          return;
        }
        state.loading = false;
        if (error && error.unauthorized) {
          return; // a 401 is the global auth flow's (the sign-out)
        }
        state.groups = null;
        state.error = {
          status: error && isNumber(error.status) ? error.status : 0,
          message: error && error.message ? error.message : 'The projection could not be loaded.',
        };
        render();
      }
    );
  }

  // GET /api/aggregate/spend with exactly the query string the
  // projection fetch carries. The answer is guarded by its own
  // sequence, so the spend block's fate never depends on the
  // projection's (and vice versa); a failed or malformed answer
  // shows the block's own error and Retry, which re-fetches only
  // the spend and leaves the site grid untouched.
  function loadSpend(query) {
    if (typeof query !== 'string') {
      query = projectionQuery();
    }
    spendStarted = true;
    state.spendLoading = true;
    state.spendError = false;
    state.spend = null;
    spendSeq += 1;
    var mySeq = spendSeq;
    render();
    global.Api.get(SPEND_PATH + query).then(
      function (payload) {
        if (mySeq !== spendSeq) {
          return; // a newer spend fetch was started: this answer is stale
        }
        if (!spendValid(payload)) {
          // A malformed (non-object, or no months array) payload is
          // a failed spend load: its own error with its own Retry.
          state.spend = null;
          state.spendLoading = false;
          state.spendError = true;
          render();
          return;
        }
        state.spend = payload;
        state.spendLoading = false;
        if (anyMonthOverBudget(payload.months)) {
          // The latest spend answer has an over-budget month: the
          // "What to defer" block shows, and its answer rides in
          // with exactly the query string this spend fetch carried.
          spendHasOverBudget = true;
          loadDeferrals(query); // renders with its own skeleton
          return;
        }
        spendHasOverBudget = false;
        state.deferrals = null;
        state.deferralsLoading = false;
        state.deferralsError = false;
        render();
      },
      function (error) {
        if (mySeq !== spendSeq) {
          return;
        }
        if (error && error.unauthorized) {
          return; // a 401 is the global auth flow's (the sign-out)
        }
        state.spend = null;
        state.spendLoading = false;
        state.spendError = true;
        render();
      }
    );
  }

  // GET /api/aggregate/deferrals with exactly the query string the
  // spend fetch carries. The answer is guarded by its own sequence,
  // so only the newest deferrals answer renders; a failed or
  // malformed answer shows the block's own error and its own Retry,
  // which re-fetches only the deferrals — the spend block and the
  // site grid are untouched either way.
  function loadDeferrals(query) {
    if (typeof query !== 'string') {
      query = projectionQuery();
    }
    state.deferralsLoading = true;
    state.deferralsError = false;
    state.deferrals = null;
    defSeq += 1;
    var mySeq = defSeq;
    render();
    global.Api.get(DEFERRALS_PATH + query).then(
      function (payload) {
        if (mySeq !== defSeq) {
          return; // a newer deferrals fetch was started: this answer is stale
        }
        if (!deferralsValid(payload)) {
          // A malformed (non-object, or no months array) payload is
          // a failed deferrals load: its own error with its own Retry.
          state.deferrals = null;
          state.deferralsLoading = false;
          state.deferralsError = true;
          render();
          return;
        }
        state.deferrals = payload;
        state.deferralsLoading = false;
        render();
      },
      function (error) {
        if (mySeq !== defSeq) {
          return;
        }
        if (error && error.unauthorized) {
          return; // a 401 is the global auth flow's (the sign-out)
        }
        state.deferrals = null;
        state.deferralsLoading = false;
        state.deferralsError = true;
        render();
      }
    );
  }


  // POST /api/aggregate/release with the panel's selected month:
  // confirm false for the preview, true once the inline ask is
  // confirmed. The answer is guarded by its own sequence, so only
  // the newest answer renders; a failed request keeps the last
  // shown answer and shows its own error — the server's detail for
  // 409, 422, 502 and 503, a plain message for anything else.
  function startRelease(confirm) {
    state.releaseLoading = true;
    state.releaseError = null;
    state.confirmAsk = false;
    releaseSeq += 1;
    var mySeq = releaseSeq;
    render();
    var body = { month: state.releaseMonth };
    if (confirm) {
      body.confirm = true; // the preview sends the month alone
    }
    global.Api.request(RELEASE_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(
      function (payload) {
        if (mySeq !== releaseSeq) {
          return; // a newer release request was started: this answer is stale
        }
        state.releaseLoading = false;
        if (!releaseValid(payload)) {
          // A malformed (non-object, or no groups array) payload is a
          // failed release: its own error, the last answer kept.
          state.releaseError = 'The release could not be completed.';
          render();
          return;
        }
        state.releaseAnswer = payload;
        render();
      },
      function (error) {
        if (mySeq !== releaseSeq) {
          return;
        }
        state.releaseLoading = false;
        if (error && error.unauthorized) {
          return; // a 401 is the global auth flow's (the sign-out)
        }
        var status = error && isNumber(error.status) ? error.status : 0;
        if (status === 409 || status === 422 || status === 502 || status === 503) {
          state.releaseError =
            error && error.message ? error.message : 'The release could not be completed.';
        } else {
          state.releaseError = 'The release could not be completed.';
        }
        render();
      }
    );
  }

  // A scenario choice or a plan-view switch invalidates the release
  // panel's answer the way a month change does: the shown answer
  // belongs to the old context, so it is cleared and the inline ask
  // closes (the month select keeps its month), while the panel's
  // loading state resets and the release request sequence advances,
  // so a preview still in flight from before the change is stale and
  // ignored when it arrives — the panel can never sit "Preparing the
  // release…" for a context that no longer exists.
  function clearReleaseState() {
    state.releaseAnswer = null;
    state.releaseError = null;
    state.confirmAsk = false;
    state.releaseLoading = false;
    releaseSeq += 1; // a newer context: the in-flight answer is stale
  }

  // The viewer's admin role, from GET /api/auth/me once per
  // activation: only a user whose roles include "admin" gets the
  // release control; a failed answer leaves the viewer read-only.
  function startMeLoad() {
    releaseAdmin = null;
    var mySeq = ++releaseMeSeq;
    global.Api.get(ME_PATH).then(
      function (me) {
        if (mySeq !== releaseMeSeq) {
          return; // a newer view load was started: this answer is stale
        }
        var roles = me && me.user && Array.isArray(me.user.roles) ? me.user.roles : [];
        releaseAdmin = roles.indexOf('admin') !== -1;
        render();
      },
      function () {
        if (mySeq !== releaseMeSeq) {
          return;
        }
        releaseAdmin = false;
        render();
      }
    );
  }

  /* ------------------------------- events ------------------------------ */

  // Delegation on the root: the controls are re-created by every
  // render(), but these listeners outlive them. The handlers mutate
  // only the module state and trigger a single render() (or a
  // re-fetch) — no per-control re-binding after any innerHTML.
  function onRootChange(event) {
    var target = event.target;
    if (!target || !target.getAttribute) {
      return;
    }
    var testId = target.getAttribute('data-testid');
    if (testId === 'aggregate-months') {
      var months = parseInt(target.value, 10);
      if (MONTH_OPTIONS.indexOf(months) === -1) {
        return; // a value the control cannot offer
      }
      state.months = months;
      load(); // the new window re-fetches
      return;
    }
    if (testId === 'aggregate-unit-toggle') {
      if (target.value !== 'units' && target.value !== 'dollars') {
        return;
      }
      state.unit = target.value;
      render(); // the figures switch without a request
      return;
    }
    if (testId === 'aggregate-plan-toggle') {
      if (target.value !== 'as-is' && target.value !== 'planned') {
        return;
      }
      state.plan = target.value;
      clearReleaseState(); // the panel's answer belongs to the other view
      render(); // the rows switch without a request
      return;
    }
    if (testId === 'aggregate-release-month') {
      var releaseOptions = releaseMonths();
      if (typeof target.value !== 'string' || releaseOptions.indexOf(target.value) === -1) {
        return; // a value the control cannot offer
      }
      state.releaseMonth = target.value;
      state.releaseAnswer = null; // the shown answer is for the old month
      state.releaseError = null;
      state.confirmAsk = false;
      render(); // the panel clears its answer without a request
      return;
    }
    if (testId === 'aggregate-scenario') {
      if (typeof target.value !== 'string') {
        return;
      }
      if (target.value !== '' && findScenario(target.value) === null) {
        return; // an id the control cannot offer
      }
      state.scenarioId = target.value;
      clearReleaseState(); // the panel's answer belongs to the old plan
      load(); // the scenario's levers (or the bare baseline) re-fetch
    }
  }

  function onRootClick(event) {
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }
    if (target.closest('[data-testid="aggregate-spend-retry"]')) {
      loadSpend(); // re-fetch only the spend, on the current query
      return;
    }
    if (target.closest('[data-testid="aggregate-deferrals-retry"]')) {
      loadDeferrals(); // re-fetch only the deferrals, on the current query
      return;
    }
    if (target.closest('[data-testid="aggregate-retry"]')) {
      load(); // re-fetch the current months (and the spend with them)
      return;
    }
    if (target.closest('[data-testid="aggregate-release-preview"]')) {
      startRelease(false); // preview the selected month
      return;
    }
    if (target.closest('[data-testid="aggregate-release-confirm"]')) {
      state.confirmAsk = true; // ask once more; nothing is sent until Confirm
      render();
      return;
    }
    if (target.closest('[data-testid="aggregate-release-confirm-no"]')) {
      state.confirmAsk = false; // Cancel: the ask closes, nothing is sent
      render();
      return;
    }
    if (target.closest('[data-testid="aggregate-release-confirm-yes"]')) {
      startRelease(true); // confirmed: the month's release goes to the ERP
    }
  }

  // Keyboard activation for the spend block's Retry: the control is
  // a span (the screen stays free of mutating controls), so its
  // button behaviour lives here — Enter and Space re-fetch only the
  // spend, as the click does, and Space's default (the page scroll)
  // is prevented. A held Space never re-activates it, as a held
  // button's Space never re-activates a button.
  function onRootKeydown(event) {
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }
    if (!target.closest('[data-testid="aggregate-spend-retry"]')) {
      return;
    }
    var isEnter = event.key === 'Enter' || event.keyCode === 13;
    var isSpace =
      event.key === ' ' || event.key === 'Spacebar' || event.keyCode === 32;
    if (!isEnter && !isSpace) {
      return;
    }
    if (isSpace) {
      event.preventDefault(); // the page's default scroll
    }
    if (isSpace && event.repeat) {
      return; // a held Space never re-activates a button
    }
    loadSpend(); // re-fetch only the spend, on the current query
  }

  /* -------------------------------- boot ------------------------------- */

  function aggregateActive() {
    var section = document.getElementById('view-aggregate');
    return section ? section.classList.contains('is-active') : false;
  }

  function activate() {
    if (!rootEl) {
      return;
    }
    if (!rootEl.firstElementChild) {
      render();
    }
    var appEl = document.getElementById('app');
    if (appEl && appEl.hidden) {
      // The shell is not signed in yet (Auth.bootstrap has not
      // completed): no request, the empty note stays put.
      return;
    }
    fetchScenarios(); // once per activation; a bad answer keeps Baseline
    startMeLoad(); // once per activation; a bad answer stays read-only
    load();
  }

  function reset() {
    seq += 1; // any answer still out the door belongs to a past session
    spendSeq += 1; // ... and so does any spend answer
    defSeq += 1; // ... and so does any deferrals answer
    scenarioSeq += 1; // ... and so does any saved-list answer
    releaseSeq += 1; // ... and so does any release answer
    releaseMeSeq += 1; // ... and so does any role answer
    releaseAdmin = null;
    scenarioRows = [];
    spendStarted = false;
    spendHasOverBudget = false;
    state.months = DEFAULT_MONTHS;
    state.unit = 'units';
    state.plan = 'as-is';
    state.scenarioId = '';
    state.groups = null;
    state.loading = false;
    state.error = null;
    state.spend = null;
    state.spendLoading = false;
    state.spendError = false;
    state.deferrals = null;
    state.deferralsLoading = false;
    state.deferralsError = false;
    state.releaseMonth = null;
    state.releaseAnswer = null;
    state.releaseLoading = false;
    state.releaseError = null;
    state.confirmAsk = false;
    render();
  }

  (function boot() {
    rootEl = document.getElementById('aggregate-root');
    if (!rootEl) {
      return;
    }
    rootEl.addEventListener('change', onRootChange);
    rootEl.addEventListener('click', onRootClick);
    rootEl.addEventListener('keydown', onRootKeydown);
    render();

    var appEl = document.getElementById('app');
    if (!appEl) {
      return;
    }
    // Sign-in shows #app: when it does and the aggregate view is the
    // active one, the projection loads then — the pre-sign-in
    // activation (the shell shows the hash's view before
    // Auth.bootstrap has completed) could not fetch.
    var observer = new MutationObserver(function () {
      if (!appEl.hidden && aggregateActive()) {
        activate();
      }
    });
    observer.observe(appEl, { attributes: true, attributeFilter: ['hidden'] });
    if (!appEl.hidden && aggregateActive()) {
      activate();
    }
  })();

  global.Aggregate = {
    activate: activate,
    reset: reset,
  };
})(window);
