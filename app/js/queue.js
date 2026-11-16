// AIERP planner screen — the Work queue view, the planner's page
// (docs/design/planner-screen.md, "Work queue (the planner's page)").
//
// #/queue is a split triage screen:
//   * a tab bar — Reorder points, Excess and dead stock and Stockout risk,
//     each with its count of rows whose `decision` is null — plus the
//     filter chips To decide, Decided and All (the pressed one is the
//     queue.default_filter setting — To decide while it is unset or the
//     settings read fails); the bar also carries the CSV download
//     buttons — "Download CSV" (the active tab's export: drift →
//     /api/drift/export.csv as drift.csv, excess →
//     /api/excess/export.csv as excess.csv, stockout →
//     /api/stockout/export.csv as stockout.csv) and "Planning sheet
//     (CSV)" (/api/recommendations/export.csv as
//     planning-parameters.csv), both fetched through Api.download;
//     while a download runs its button is disabled, and a failed one
//     shows "Download failed. <message>" in the bar's status region
//     (cleared by the next click; nothing is requested on load);
//   * a list pane (left, ~60%): a sticky column header and a body that
//     scrolls on its own; the columns are rank, item and site, the
//     kind's key figure, its money as whole dollars and the decision
//     badge (Undecided, Accepted, Applied, Dismissed or Snoozed); every
//     row is a button, and the selected row is tinted and carries
//     aria-current="true". When the API lists an item_description
//     for a row (a non-empty string), the item/site cell shows it on
//     its own line under the codes, in the secondary ink colour
//     token; a row without one renders no description.
//   * a detail pane (right, 440px, scrolling on its own): the kind label
//     and rank, the item code (its description, when listed, on the
//     line under the code) and site, two figure tiles, a Why block
//     (a one-sentence reason, the inputs as a single-column key/value
//     list, the arithmetic in monospace), a service level line for a
//     Reorder point row whose API data carries a finite service level
//     (the level × 100 with up to two decimals and trailing zeros
//     dropped, plus the class source when the row names one — built
//     from the row's own data, no extra request), an Order quantity
//     block for a Reorder point row whose API data carries a
//     recommended order quantity (the quantity with its days of
//     cover, the recommended max, the ERP's current order quantity
//     and max, the basis line and the arithmetic — built from the
//     row's own data, no extra request), a Proposed remedy section
//     for a Stockout risk row whose data carries a `remedy` key
//     (directly after the Why block and before the Decision block —
//     built from the row's own data, no extra request; see below) and
//     a Decision block with the row's decision history, newest first.
//
// The "Proposed remedy" section of the detail pane (issue #1315):
// for a selected Stockout risk row whose data carries a `remedy` key,
// directly after the Why block and before the Decision block — built
// from the row's own data, no request. The proposal line starts with
// the label of the matching action button: "Expedite: order
// <order_nbr> line <line_nbr> from <supplier>, <units> units expected
// <expected_on>.", "Transfer: <units> units from site <from_site>."
// or "Raise order: <units> units from <supplier>, order by
// <order_by>.": <supplier> the supplier_name, else the supplier_code
// (the " from <supplier>" part left out when both are missing),
// <units> whole units with thousands separators the way the queue
// writes units elsewhere; the reason line shows the remedy's reason.
// A remedy that is null, not an object, or of any other kind shows
// the section with the single line "No remedy proposed."; a row
// without the key (an older server) shows no section, and the
// Reorder points and Excess tabs never show one. Every value from
// the API is HTML-escaped and a missing code or date reads as an em
// dash. The section presses or changes no action button — the
// decision is unchanged.
//
// The answer of GET /api/erp-sources — fetched once with each load of
// the three lists, and the lists never wait for it — adds two things,
// only when its `sources` holds two or more entries: an ERP tag,
// reading "Acumatica" for acumatica, "Odoo" for odoo and otherwise
// the source code itself as text, in the list pane's item and site
// cell and, beside the site, in the detail pane, for every row whose
// item-site the answer lists (a row whose item-site is not in the
// answer shows no tag); and a select in the tab bar — "All ERPs"
// first, then one option per source — that starts on "All ERPs" and
// narrows the list pane and the tab counts the same way the filter
// chips do, a row without a source showing only under "All ERPs".
// With one source or none, with the answer still out, or on a failed
// read, the view reads exactly as it does without the answer: no
// tag, no select, no error.
//
// Decisions are admin-only: only a user whose /api/auth/me roles include
// "admin" sees the decision controls; everyone else sees the row
// read-only. Accept, Dismiss (a non-blank reason is required) and Snooze
// (7, 14 or 90 days — 30 for stockout) post to the kind's
// POST …/decisions with item_code and site_code; a 4xx response shows
// its `detail` inline. On success the row's badge, the tab count and the
// history update without a page load, and the Overview's figures are
// marked stale so the Overview's next visit re-requests the summary
// and the lists (overview.js).
//
// Admins also see an "Approve → draft PO" section under the decision
// block, for a selected Stockout risk or Reorder point row only (never
// Excess, never a non-admin): a supplier, a quantity and a unit price,
// prefilled from GET /api/recommendations/by-key (the suggested order
// quantity and the unit cost, when each is a finite number), and a
// Preview (confirm false) / Create (confirm true) pair posting to
// POST /api/replenishment/actions/draft-po. The outcome — would-create
// with the origin, already-exists / already-existed with the existing
// order's name, created with the new order's name, or the server's
// 409 / 502 / 422 detail — renders in the section's own status region;
// no decision or row state is touched by it. When the answer carries
// `target` — the Odoo item, site and vendor codes the client was
// actually called with — the status region also shows them in their
// own line, "Odoo item <item_code> · site <site_code> · vendor
// <supplier_code>" ([data-testid="queue-draftpo-target"]); an answer
// without `target` renders exactly as before.
//
// The queue's own settings (GET /api/settings) shape two of these
// behaviours: with decisions.require_action_choice true (the default) an
// excess or stockout row opens with no action pressed — Accept reads
// "Accept: choose an action" and stays disabled until one is, and the
// choice clears when another row is picked; with it false the first
// action is pre-selected instead. Reorder-point rows never carry an
// action, so they are unaffected. When the settings request fails the
// queue falls back to the defaults (choice required, To decide) and
// still loads.
//
// #/queue?kind=drift&item=<code>&site=<code> (the Overview links here)
// selects that row; otherwise the first row of the current filter is
// selected.
//
// Every call goes through app/js/api.js:
//   * GET /api/drift?limit=1000&include_decided=true — and the matching
//     /api/excess and /api/stockout (the stockout list additionally
//     asks for the remedy: include_remedy=true) — for the three tab
//     lists,
//   * GET /api/auth/me for the admin role,
//   * GET /api/settings for the two queue settings,
//   * GET /api/erp-sources once with each load of the three lists (the
//     lists never wait for it) for the rows' ERP source tags and the
//     tab bar's ERP filter — only the newest answer renders, and a
//     failed read shows no tag, no filter and no error,
//   * POST /api/drift/decisions (or /api/excess, /api/stockout) to
//     record a decision,
//   * GET …/decisions?item_code=&site_code= for the selected row's
//     history, newest first,
//   * GET /api/recommendations/by-key?item_code=&site_code= to prefill
//     the draft PO section (admin, stockout or drift rows only),
//   * GET /api/drift/export.csv (or the matching /api/excess and
//     /api/stockout) for the tab's "Download CSV" button and GET
//     /api/recommendations/export.csv for "Planning sheet (CSV)" —
//     both through Api.download (bearer token, Blob, a temporary
//     <a download> click),
//   * POST /api/replenishment/actions/draft-po to preview (confirm
//     false) or draft (confirm true) a purchase order.
//
// Every string from the API is HTML-escaped before it reaches the page.
// At 900px and below the list and detail panes stack; only the panes
// scroll, the document never does.
(function (global) {
  'use strict';

  var LIST_QUERY = 'limit=1000&include_decided=true';

  // The CSV exports the tab bar's download buttons fetch (issue #694):
  // the active tab's own list export, or the planning-parameter sheet
  // for every row. Nothing is requested until a button is clicked.
  var DOWNLOADS_BY_KIND = {
    drift: { path: '/api/drift/export.csv', filename: 'drift.csv' },
    excess: { path: '/api/excess/export.csv', filename: 'excess.csv' },
    stockout: { path: '/api/stockout/export.csv', filename: 'stockout.csv' },
  };
  var PLANNING_DOWNLOAD = {
    path: '/api/recommendations/export.csv',
    filename: 'planning-parameters.csv',
  };

  var KINDS = [
    {
      kind: 'drift',
      title: 'Reorder points',
      kindLabel: 'Reorder point',
      listPath: '/api/drift?' + LIST_QUERY,
      decisionsPath: '/api/drift/decisions',
      moneyField: 'money_at_stake',
      moneyLabel: 'Money at stake',
      figureLabel: 'Current \u2192 recommended',
      snoozeMax: 90,
      actions: [],
    },
    {
      kind: 'excess',
      title: 'Excess and dead stock',
      kindLabel: 'Excess and dead stock',
      listPath: '/api/excess?' + LIST_QUERY,
      decisionsPath: '/api/excess/decisions',
      moneyField: 'money_tied_up',
      moneyLabel: 'Money tied up',
      figureLabel: 'Excess units',
      snoozeMax: 90,
      actions: [
        { value: 'stop_replenishing', label: 'Stop replenishing' },
        { value: 'reduce', label: 'Reduce' },
        { value: 'dispose', label: 'Dispose' },
      ],
    },
    {
      kind: 'stockout',
      title: 'Stockout risk',
      kindLabel: 'Stockout risk',
      // The stockout list additionally asks for the remedy
      // (issue #1315): the "Proposed remedy" section of the detail
      // pane is built from the row's own data — no extra request.
      listPath: '/api/stockout?' + LIST_QUERY + '&include_remedy=true',
      decisionsPath: '/api/stockout/decisions',
      moneyField: 'money_at_risk',
      moneyLabel: 'Money at risk',
      figureLabel: 'Chance of stockout',
      snoozeMax: 30,
      actions: [
        { value: 'expedite', label: 'Expedite' },
        { value: 'raise_order', label: 'Raise order' },
        { value: 'transfer', label: 'Transfer' },
      ],
    },
  ];

  var KINDS_BY_KIND = {};
  KINDS.forEach(function (meta) {
    KINDS_BY_KIND[meta.kind] = meta;
  });

  var FILTERS = [
    { value: 'open', label: 'To decide' },
    { value: 'decided', label: 'Decided' },
    { value: 'all', label: 'All' },
  ];

  var DEFAULT_SNOOZE_DAYS = 14;

  var state = {
    ready: false,
    loading: false,
    loadError: null,
    isAdmin: false,
    kind: 'drift',
    filter: 'open',
    requireActionChoice: true, // decisions.require_action_choice, default true
    defaultFilter: 'open', // queue.default_filter, default "open"
    rows: { drift: null, excess: null, stockout: null },
    listRows: [], // the rows the list pane shows for kind + filter
    selected: null, // { item, site }
    pending: null, // { item, site } from the URL hash
    mode: 'none', // 'none' | 'dismiss' | 'snooze'
    actionDraft: null,
    reasonDraft: '',
    reasonError: false,
    snoozeDays: DEFAULT_SNOOZE_DAYS,
    postError: null,
    busy: false,
    downloadBusy: null, // 'list' | 'plan' while a download is running
    downloadError: null, // "Download failed. …" note after a failed download
    history: null, // [decision] newest first
    historyFor: null, // { item, site }
    historyLoading: false,
    draftPo: { supplier: '', qty: '', price: '', busy: false, resultHtml: '' },
    draftPoFor: null, // { item, site } the current draft-po prefill belongs to
    draftPoSeq: 0, // bumped on every draft-po reset; kills stale prefills
    erpSources: null, // the newest applied GET /api/erp-sources answer
    erpSourceByItemSite: {}, // "item\0site" -> source code or null
    erpSourcesList: [], // [{ code, label }] the filter's sources, answer order
    erpFilter: 'all', // 'all' (All ERPs) or one listed source code
    erpSourcesSeq: 0, // bumped on every load and reset; kills stale answers
  };

  var rootEl = null;

  /* ------------------------------ helpers ------------------------------ */

  function esc(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // The free-form item description the API may list alongside a row
  // (item_description): a non-empty string. Anything else — missing,
  // null, a number, an empty string — means "no description":
  // neither the list row nor the detail pane renders one then.
  function itemDescription(row) {
    var description = row.item_description;
    return typeof description === 'string' && description !== '' ? description : null;
  }

  function isMoney(value) {
    return typeof value === 'number' && isFinite(value);
  }

  // Whole dollars with thousands separators: 18720.4 -> "$18,720".
  // null/undefined (the only "without money" values) renders as an em dash.
  function moneyLabel(value) {
    if (!isMoney(value)) {
      return '\u2014';
    }
    return '$' + Math.round(value).toLocaleString('en-US');
  }

  // Planner quantity precision tiers: whole units with en-US thousands
  // separators (10460.72 -> "10,461"), one decimal for days and months
  // of cover (12.94 -> "12.9") and two decimals for the service factor
  // k (1.987 -> "1.99"). Money keeps its whole-dollar format
  // (moneyLabel). Every tier renders null/undefined as an em dash.
  function unitCount(value) {
    if (!isMoney(value)) {
      return '\u2014';
    }
    return Math.round(value).toLocaleString('en-US');
  }

  function coverValue(value) {
    if (!isMoney(value)) {
      return '\u2014';
    }
    return value.toLocaleString('en-US', {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    });
  }

  function factorValue(value) {
    if (!isMoney(value)) {
      return '\u2014';
    }
    return value.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  // The annual holding rate as a percent: 0.25 -> "25%", 0.253 ->
  // "25.3%" (up to two decimals — a rate is a fraction, never whole
  // dollars). null/undefined renders as an em dash.
  function holdingRatePercent(value) {
    if (!isMoney(value)) {
      return '\u2014';
    }
    return (value * 100).toLocaleString('en-US', { maximumFractionDigits: 2 }) + '%';
  }

  // A service level as a percent: 0.95 -> "95%", 0.9999 ->
  // "99.99%" (the level × 100, up to two decimals, trailing zeros
  // dropped). A level is a fraction, never whole dollars.
  // null/undefined/non-finite returns null — no service level to
  // show at all then.
  function serviceLevelPercent(value) {
    if (!isMoney(value)) {
      return null;
    }
    return (value * 100).toLocaleString('en-US', { maximumFractionDigits: 2 }) + '%';
  }

  // The "#" column of the list pane: the rank number for ranked rows.
  // Only the Reorder points tab labels unranked rows by why they carry no
  // rank — "No ROP" for items the ERP has no reorder point for, "No cost"
  // for the rest (their money at stake is unknown, so there is nothing to
  // rank them by). The other tabs keep the bare dash for unranked rows.
  function rankLabel(kind, row) {
    if (isMoney(row.rank)) {
      return String(Math.round(row.rank));
    }
    if (kind !== 'drift') {
      return '\u2014';
    }
    if (row.category === 'no_reorder_point') {
      return 'No ROP';
    }
    return 'No cost';
  }

  function kindMeta(kind) {
    return KINDS_BY_KIND[kind] || KINDS[0];
  }

  function rowsFor(kind) {
    return Array.isArray(state.rows[kind]) ? state.rows[kind] : [];
  }

  function isUndecided(row) {
    return !!row && typeof row === 'object' && row.decision == null;
  }

  function hasDecision(row) {
    return !!row && typeof row === 'object' && row.decision != null;
  }

  function undecidedCount(kind) {
    var rows = erpFilterRows(rowsFor(kind));
    var count = 0;
    for (var i = 0; i < rows.length; i++) {
      if (isUndecided(rows[i])) {
        count += 1;
      }
    }
    return count;
  }

  function findRow(kind, item, site) {
    var rows = rowsFor(kind);
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].item_code === item && rows[i].site_code === site) {
        return rows[i];
      }
    }
    return null;
  }

  function selectedRow() {
    if (!state.selected) {
      return null;
    }
    return findRow(state.kind, state.selected.item, state.selected.site);
  }

  // The list pane shows the rows the current kind + filter slice
  // picked. Deciding a row updates it in place (badge, tab count,
  // history) without re-slicing the list mid-triage; pressing a chip
  // or a tab re-slices it.
  function filterRows(kind, filter) {
    var rows = rowsFor(kind);
    if (filter === 'open') {
      rows = rows.filter(isUndecided);
    } else if (filter === 'decided') {
      rows = rows.filter(hasDecision);
    }
    return erpFilterRows(rows);
  }

  // The ERP source of a row's item-site, from the newest applied
  // answer of GET /api/erp-sources (issue #1003): a non-empty source
  // code, or null — no answer yet, an answer whose sources hold
  // fewer than two entries (only with several ERPs: with one entry or
  // none the page reads as without the answer), the item-site not
  // listed in the answer, or a null source_system.
  function erpSourceOf(row) {
    if (!state.erpSources || state.erpSourcesList.length < 2 || !row) {
      return null;
    }
    var source = state.erpSourceByItemSite[row.item_code + '\u0000' + row.site_code];
    return typeof source === 'string' && source !== '' ? source : null;
  }

  function erpSourceListed(code) {
    for (var i = 0; i < state.erpSourcesList.length; i++) {
      if (state.erpSourcesList[i].code === code) {
        return true;
      }
    }
    return false;
  }

  // The ERP filter: 'all' (All ERPs) or one of the listed source
  // codes. A row passes when its item-site carries that source — a
  // row without a source (not listed, or a null source_system) passes
  // only 'all'.
  function passesErpFilter(row) {
    if (state.erpFilter === 'all' || !state.erpSources) {
      return true;
    }
    return erpSourceOf(row) === state.erpFilter;
  }

  function erpFilterRows(rows) {
    return rows.filter(passesErpFilter);
  }

  // The tag's reading: the display name for the two known sources,
  // otherwise the source code itself, always as escaped text.
  function erpLabelFor(code) {
    if (code === 'acumatica') {
      return 'Acumatica';
    }
    if (code === 'odoo') {
      return 'Odoo';
    }
    return code;
  }

  // The row's ERP tag (issue #1003): rendered only with several ERPs
  // — the same two-or-more-sources guard renderErpFilter carries,
  // so a single-source answer shows no tag in the list pane or the
  // detail pane. The reading is escaped text.
  function erpTagHtml(source) {
    if (!source || state.erpSourcesList.length < 2) {
      return '';
    }
    return (
      '<span class="q-erp-tag" data-testid="queue-erp-tag">' +
      esc(erpLabelFor(source)) +
      '</span>'
    );
  }

  function recomputeList() {
    state.listRows = filterRows(state.kind, state.filter);
  }

  function visibleRows() {
    return state.listRows;
  }

  function actionLabel(decision) {
    if (!decision || !decision.action) {
      return '';
    }
    var meta = kindMeta(state.kind);
    for (var i = 0; i < meta.actions.length; i++) {
      if (meta.actions[i].value === decision.action) {
        return meta.actions[i].label;
      }
    }
    return String(decision.action);
  }

  function actionLabelForValue(meta, value) {
    for (var i = 0; i < meta.actions.length; i++) {
      if (meta.actions[i].value === value) {
        return meta.actions[i].label;
      }
    }
    return '';
  }

  // A listed drift row is applied when the ERP reorder point already
  // matches the recommendation (within one unit) — the same rule the
  // decisions summary applies. For excess and stockout an accepted
  // decision is applied only once the item-site is no longer listed, so
  // a listed accepted row is always still outstanding.
  function isAppliedRow(row) {
    if (state.kind !== 'drift' || !row) {
      return false;
    }
    var current = row.current_reorder_point;
    var recommended = row.reorder_point_units;
    return isMoney(current) && isMoney(recommended) && Math.abs(current - recommended) <= 1;
  }

  function badgeFor(row) {
    var decision = row && row.decision;
    if (!decision) {
      return { label: 'Undecided', cls: 'q-badge-undecided' };
    }
    if (decision.decision === 'accepted') {
      if (isAppliedRow(row)) {
        return { label: 'Applied', cls: 'q-badge-applied' };
      }
      var label = actionLabel(decision);
      return {
        label: label ? 'Accepted \u00b7 ' + label : 'Accepted',
        cls: 'q-badge-accepted',
      };
    }
    if (decision.decision === 'dismissed') {
      return { label: 'Dismissed', cls: 'q-badge-dismissed' };
    }
    if (decision.decision === 'snoozed') {
      return { label: 'Snoozed', cls: 'q-badge-snoozed' };
    }
    return { label: String(decision.decision), cls: 'q-badge-undecided' };
  }

  // The kind's key figure column of the list pane.
  function figureText(kind, row) {
    if (kind === 'drift') {
      var current = isMoney(row.current_reorder_point)
        ? unitCount(row.current_reorder_point)
        : 'not set';
      return current + ' \u2192 ' + unitCount(row.reorder_point_units);
    }
    if (kind === 'excess') {
      return unitCount(row.excess_units) + ' units';
    }
    return isMoney(row.p_stockout) ? Math.round(row.p_stockout * 100) + '%' : '\u2014';
  }

  function figureTiles(kind, row) {
    if (kind === 'drift') {
      return [
        {
          label: 'ERP reorder point',
          value: isMoney(row.current_reorder_point)
            ? unitCount(row.current_reorder_point)
            : 'not set',
        },
        { label: 'Recommended', value: unitCount(row.reorder_point_units), accent: true },
      ];
    }
    if (kind === 'excess') {
      return [
        { label: 'On hand', value: unitCount(row.on_hand_units) },
        { label: 'Excess units', value: unitCount(row.excess_units), accent: true },
      ];
    }
    return [
      {
        label: 'On hand + open supply',
        value: unitCount(row.on_hand_units) + ' + ' + unitCount(row.open_supply_units),
      },
      {
        label: 'Chance of stockout',
        value: isMoney(row.p_stockout) ? Math.round(row.p_stockout * 100) + '%' : '\u2014',
        accent: true,
      },
    ];
  }

  // The Why block's one-sentence reason, built from the row's data.
  function whyText(kind, row) {
    if (kind === 'drift') {
      if (row.category === 'no_reorder_point') {
        return (
          'The ERP has no reorder point yet; demand over the lead time justifies ' +
          unitCount(row.reorder_point_units) +
          ' units. It is listed for confirmation rather than ranked: items without ' +
          'a reorder point are often deliberately not stocked, for example made to order.'
        );
      }
      var gap = isMoney(row.units_gap) ? unitCount(Math.abs(row.units_gap)) : '\u2014';
      var side = row.direction === 'over' ? 'above' : 'below';
      return 'The ERP reorder point sits ' + gap + ' units ' + side + ' the recommended point.';
    }
    if (kind === 'excess') {
      if (row.category === 'dead') {
        return row.dead_reason === 'stopped'
          ? 'Dead stock: demand has stopped.'
          : 'Dead stock: the item has never moved.';
      }
      return 'Stock on hand is ' + unitCount(row.excess_units) + ' units beyond justified cover.';
    }
    if (isMoney(row.p_stockout)) {
      var within = isMoney(row.lead_time_days)
        ? ' within ' + coverValue(row.lead_time_days) + ' days'
        : '';
      return Math.round(row.p_stockout * 100) + '% chance of stockout' + within + '.';
    }
    return 'Stock is likely to run out before open supply lands.';
  }

  // The inputs as a single-column list (label on the left, value on the
  // right). The drift labels read in planner language, and the service
  // level the ERP point gives (k) is named as such so it is not confused
  // with the z of the recommended-point formula below the list.
  function inputList(kind, row) {
    if (kind === 'drift') {
      return [
        { label: 'Average lead-time demand', value: unitCount(row.mu_l) },
        { label: 'Lead-time demand variability (std dev)', value: unitCount(row.sigma_l) },
        { label: 'Service level the ERP point gives (k)', value: factorValue(row.k) },
        { label: 'Recommended safety stock', value: unitCount(row.safety_stock) },
        { label: 'Money at stake', value: moneyLabel(row.money_at_stake) },
      ];
    }
    if (kind === 'excess') {
      return [
        { label: 'On hand', value: unitCount(row.on_hand_units) },
        { label: 'Justified units', value: unitCount(row.justified_units) },
        { label: 'Months of cover', value: coverValue(row.months_of_cover) },
        { label: 'Money tied up', value: moneyLabel(row.money_tied_up) },
      ];
    }
    return [
      {
        label: 'Lead time',
        value: isMoney(row.lead_time_days) ? coverValue(row.lead_time_days) + ' days' : '\u2014',
      },
      { label: 'Days of cover', value: coverValue(row.days_of_cover) },
      { label: 'Expected units short', value: unitCount(row.expected_units_short) },
      { label: 'Money at risk', value: moneyLabel(row.money_at_risk) },
    ];
  }

  // The demand-basis lines of the Reorder points tab, under the inputs:
  // where the row's recommended point was built from, and the other
  // basis' whole-unit results with the signed difference against the
  // shown point. Excess and stockout rows have no basis, and a server
  // that sends no demand_basis (an older deploy) shows neither line.
  function basisLines(kind, row) {
    if (kind !== 'drift') {
      return '';
    }
    var basis = row.demand_basis;
    if (basis !== 'history' && basis !== 'forecast') {
      return '';
    }
    var parts = [
      '<div class="q-basis-lines">',
      '<p class="q-basis" data-testid="q-basis">' +
        (basis === 'history' ? 'Built on history' : 'Built on the forecast') +
        '</p>',
    ];
    var other = row.other_basis;
    if (other && typeof other === 'object') {
      var otherBasis =
        other.basis === 'history' || other.basis === 'forecast'
          ? other.basis
          : basis === 'history'
            ? 'forecast'
            : 'history';
      var text = otherBasis === 'forecast' ? 'On the forecast:' : 'On history:';
      if (isMoney(other.reorder_point_units)) {
        text += ' reorder point ' + unitCount(other.reorder_point_units) + ' units';
      }
      if (isMoney(other.safety_stock_units)) {
        text += ', safety stock ' + unitCount(other.safety_stock_units) + ' units';
      }
      if (isMoney(other.reorder_point_units) && isMoney(row.reorder_point_units)) {
        var difference = Math.round(other.reorder_point_units - row.reorder_point_units);
        if (difference !== 0) {
          text +=
            ' (' +
            (difference < 0 ? '\u2212' : '+') +
            Math.abs(difference).toLocaleString('en-US') +
            ')';
        }
      }
      parts.push(
        '<p class="q-other-basis" data-testid="q-other-basis">' + esc(text) + '</p>'
      );
    }
    parts.push('</div>');
    return parts.join('');
  }

  function formatDecidedAt(value) {
    var text = value == null ? '' : String(value);
    var match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(text);
    if (match) {
      return match[1] + ' ' + match[2] + ' UTC';
    }
    return text;
  }

  // Today + n days as YYYY-MM-DD in the browser's local date — the same
  // "today" the server-side snooze validation uses.
  function snoozeDate(days) {
    var date = new Date();
    date.setDate(date.getDate() + days);
    function pad(value) {
      return (value < 10 ? '0' : '') + value;
    }
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate());
  }

  function snoozeOptions(meta) {
    var options = [7, 14];
    if (meta.snoozeMax !== 7 && meta.snoozeMax !== 14) {
      options.push(meta.snoozeMax);
    }
    return options;
  }

  /* ------------------------------ rendering ---------------------------- */

  function queueActive() {
    var active = document.querySelector('.view.is-active');
    return !!active && active.getAttribute('data-view') === 'queue';
  }

  function render() {
    if (!rootEl) {
      return;
    }
    if (!state.ready) {
      rootEl.innerHTML = renderLoadingOrError();
      return;
    }
    var meta = kindMeta(state.kind);
    rootEl.innerHTML =
      '<div class="q-bar">' +
        '<div class="q-tabs" role="tablist" aria-label="Recommendation lists">' +
          renderTabs() +
        '</div>' +
        '<div class="q-chips" role="group" aria-label="Decision filter">' +
          renderChips() +
        '</div>' +
        renderErpFilter() +
        '<div class="q-downloads">' +
          renderDownloads() +
        '</div>' +
      '</div>' +
      '<div class="q-split">' +
        '<section class="q-list" aria-label="' + esc(meta.title) + ' rows">' +
          '<div class="q-list-body">' + renderListBody() + '</div>' +
        '</section>' +
        '<aside class="q-detail" aria-label="Selected row">' +
          renderDetail() +
        '</aside>' +
      '</div>';
  }

  function renderTabs() {
    return KINDS.map(function (meta) {
      var on = meta.kind === state.kind;
      return (
        '<button type="button" role="tab" class="q-tab' + (on ? ' is-on' : '') + '" ' +
        'aria-selected="' + (on ? 'true' : 'false') + '" data-kind="' + esc(meta.kind) + '">' +
        esc(meta.title) +
        ' <span class="q-tab-count">' + undecidedCount(meta.kind) + '</span>' +
        '</button>'
      );
    }).join('');
  }

  function renderChips() {
    return FILTERS.map(function (filter) {
      var on = filter.value === state.filter;
      return (
        '<button type="button" class="q-chip' + (on ? ' is-on' : '') + '" ' +
        'aria-pressed="' + (on ? 'true' : 'false') + '" data-filter="' + esc(filter.value) + '">' +
        esc(filter.label) +
        '</button>'
      );
    }).join('');
  }

  function renderDownloads() {
    var html =
      '<button type="button" class="btn btn-secondary q-download" ' +
      'data-testid="queue-download"' +
      (state.downloadBusy === 'list' ? ' disabled' : '') +
      '>Download CSV</button>' +
      '<button type="button" class="btn btn-secondary q-download-plan" ' +
      'data-testid="queue-download-plan"' +
      (state.downloadBusy === 'plan' ? ' disabled' : '') +
      '>Planning sheet (CSV)</button>';
    if (state.downloadError) {
      html +=
        '<span class="q-download-msg" data-testid="queue-download-msg" role="status">' +
        esc(state.downloadError) +
        '</span>';
    }
    return html;
  }

  // The ERP filter of the tab bar (issue #1003): a select with
  // "All ERPs" first and then one option per source, in the answer's
  // order — rendered only when the newest applied answer's sources
  // hold two or more entries (one entry or none, the answer still
  // out, or a failed read renders nothing at all). The chosen value
  // lives in module state, so it survives every re-render, and is
  // cleared on sign-out.
  function renderErpFilter() {
    if (!state.erpSources || state.erpSourcesList.length < 2) {
      return '';
    }
    var options = '<option value="all">All ERPs</option>';
    for (var i = 0; i < state.erpSourcesList.length; i++) {
      var source = state.erpSourcesList[i];
      options +=
        '<option value="' +
        esc(source.code) +
        '"' +
        (source.code === state.erpFilter ? ' selected' : '') +
        '>' +
        esc(source.label) +
        '</option>';
    }
    return (
      '<select class="q-erp-filter" data-testid="queue-erp-filter" ' +
      'aria-label="ERP filter">' + options + '</select>'
    );
  }

  function renderHead() {
    var meta = kindMeta(state.kind);
    return (
      '<div class="q-row-head">' +
        '<span class="q-h-rank">#</span>' +
        '<span class="q-h-item">Item \u00b7 site</span>' +
        '<span class="q-h-figure">' + esc(meta.figureLabel) + '</span>' +
        '<span class="q-h-money">' + esc(meta.moneyLabel) + '</span>' +
        '<span class="q-h-badge">Decision</span>' +
      '</div>'
    );
  }

  function rowHtml(row) {
    var meta = kindMeta(state.kind);
    var badge = badgeFor(row);
    var selected =
      !!state.selected &&
      state.selected.item === row.item_code &&
      state.selected.site === row.site_code;
    var description = itemDescription(row);
    return (
      '<button type="button" class="q-row' + (selected ? ' is-selected' : '') + '" ' +
      (selected ? 'aria-current="true" ' : '') +
      'data-item="' + esc(row.item_code) + '" data-site="' + esc(row.site_code) + '">' +
        '<span class="q-rank' +
        (state.kind === 'drift' && !isMoney(row.rank) ? ' is-unranked' : '') +
        '">' +
        rankLabel(state.kind, row) +
        '</span>' +
        '<span class="q-item-cell">' +
          '<span class="q-item-site">' + esc(row.item_code) + ' \u00b7 ' + esc(row.site_code) + '</span>' +
          erpTagHtml(erpSourceOf(row)) +
          (description ? '<span class="q-item-desc">' + esc(description) + '</span>' : '') +
        '</span>' +
        '<span class="q-figure">' + esc(figureText(state.kind, row)) + '</span>' +
        '<span class="q-money">' + esc(moneyLabel(row[meta.moneyField])) + '</span>' +
        '<span class="q-row-badge"><span class="badge ' + badge.cls + '">' + esc(badge.label) + '</span></span>' +
      '</button>'
    );
  }

  function renderListBody() {
    var rows = visibleRows();
    if (!rows.length) {
      var note =
        state.filter === 'open'
          ? 'Nothing here. Every row in this list already has a decision.'
          : 'No rows in this list.';
      return renderHead() + '<p class="q-list-empty">' + esc(note) + '</p>';
    }
    var dividerIndex = -1;
    var dividerCount = 0;
    if (state.kind === 'drift') {
      for (var i = 0; i < rows.length; i++) {
        if (rows[i].category === 'no_reorder_point') {
          dividerCount += 1;
          if (dividerIndex === -1) {
            dividerIndex = i;
          }
        }
      }
    }
    var parts = [renderHead()];
    for (var j = 0; j < rows.length; j++) {
      if (j === dividerIndex) {
        parts.push(unrankedGroupHtml(dividerCount));
      }
      parts.push(rowHtml(rows[j]));
    }
    return parts.join('');
  }

  // The divider before the first unranked row of the Reorder points list:
  // a full-width group label — not a .q-row, and (see .q-group in
  // app.css) not focusable and not selectable.
  function unrankedGroupHtml(count) {
    return (
      '<div class="q-group">' +
      'Not ranked: no reorder point in the ERP (' + count + ')' +
      '</div>'
    );
  }

  function renderDetail() {
    var row = selectedRow();
    if (!row) {
      return (
        '<div class="q-detail-inner">' +
        '<p class="q-detail-empty">Pick a row to see why it is listed and to decide.</p>' +
        '</div>'
      );
    }
    var meta = kindMeta(state.kind);
    var badge = badgeFor(row);
    var tiles = figureTiles(state.kind, row);
    var inputs = inputList(state.kind, row);
    var rank = '';
    if (isMoney(row.rank)) {
      rank = ' \u00b7 rank ' + Math.round(row.rank);
    } else if (row.category === 'no_reorder_point') {
      rank = ' \u00b7 not ranked';
    }
    return (
      '<div class="q-detail-inner">' +
        '<header class="q-detail-head">' +
          '<span class="q-detail-kind">' + esc(meta.kindLabel) + esc(rank) + '</span>' +
          '<h2 class="q-detail-title">' + esc(row.item_code) + '</h2>' +
          (itemDescription(row)
            ? '<p class="q-detail-desc">' + esc(itemDescription(row)) + '</p>'
            : '') +
          '<span class="q-detail-site">site ' +
          esc(row.site_code) +
          erpTagHtml(erpSourceOf(row)) +
          '</span>' +
        '</header>' +
        '<div class="q-figures">' +
          tiles
            .map(function (tile) {
              return (
                '<div class="q-figure-tile">' +
                  '<span class="q-figure-label">' + esc(tile.label) + '</span>' +
                  '<span class="q-figure-value' + (tile.accent ? ' is-accent' : '') + '">' +
                    esc(tile.value) +
                  '</span>' +
                '</div>'
              );
            })
            .join('') +
        '</div>' +
        '<section class="q-why" aria-label="Why">' +
          '<h3>Why</h3>' +
          '<p class="q-why-text">' + esc(whyText(state.kind, row)) + '</p>' +
          '<dl class="q-inputs">' +
            inputs
              .map(function (input) {
                return (
                  '<div class="q-input">' +
                    '<dt>' + esc(input.label) + '</dt>' +
                    '<dd>' + esc(input.value) + '</dd>' +
                  '</div>'
                );
              })
              .join('') +
          '</dl>' +
          basisLines(state.kind, row) +
          '<pre class="q-arithmetic">' + esc(row.arithmetic || '') + '</pre>' +
        '</section>' +
        remedyHtml(row) +
        serviceLevelHtml(row) +
        orderQuantityHtml(row) +
        renderDecisionBlock(row, badge) +
        (draftPoEligible() ? renderDraftPoBlock() : '') +
      '</div>'
    );
  }

  // The service level line of the Reorder points tab: a <p>
  // directly after the Why block (before the Order quantity block)
  // for a selected row whose API data carries a finite service_level
  // — "Service level <percent>". The source is named only when it is
  // one of the class levels: " · class A level" when the row's
  // service_level_source is class-sourced and its abc_class is an
  // A, B or C, " · class C, single level" when it is single-sourced —
  // nothing more otherwise. No element at all — a null or absent
  // service level, and the Excess and Stockout tabs. Built from the
  // row's own data: the queue makes no request for it.
  function serviceLevelHtml(row) {
    if (state.kind !== 'drift') {
      return '';
    }
    var percent = serviceLevelPercent(row.service_level);
    if (percent === null) {
      return '';
    }
    var source = row.service_level_source;
    if (!source || typeof source !== 'object' || Array.isArray(source)) {
      source = null;
    }
    var abcClass = source ? source.abc_class : null;
    var suffix = '';
    if (abcClass === 'A' || abcClass === 'B' || abcClass === 'C') {
      if (source.source === 'class') {
        suffix = ' \u00b7 class ' + abcClass + ' level';
      } else if (source.source === 'single') {
        suffix = ' \u00b7 class ' + abcClass + ', single level';
      }
    }
    return '<p class="q-service-level">' + esc('Service level ' + percent + suffix) + '</p>';
  }

  // The "Order quantity" block of the Reorder points tab: it renders
  // directly after the Why block, for a selected Reorder point row whose
  // API data carries a non-null `order_quantity` object, and nothing at
  // all (no element) otherwise — a null or absent order quantity, and
  // the Excess and Stockout tabs. It is built entirely from the row's
  // own data: the queue makes no request for it. The facts, in order —
  // the recommended quantity with its days of cover, the recommended
  // max (the recommended reorder point plus the quantity), the ERP's
  // current order quantity and max (an em dash when the ERP has no
  // value) — then the basis line (the economic order quantity's inputs,
  // or the 30-days-of-demand assumption, plus which clamp held) and the
  // order-quantity arithmetic in monospace.
  function orderQuantityHtml(row) {
    if (state.kind !== 'drift') {
      return '';
    }
    var oq = row.order_quantity;
    if (!oq || typeof oq !== 'object' || Array.isArray(oq)) {
      return '';
    }
    var basisText;
    if (oq.basis === 'eoq') {
      basisText =
        'Economic order quantity at ' +
        moneyLabel(oq.order_cost) +
        ' an order and ' +
        holdingRatePercent(oq.holding_rate) +
        ' a year holding cost.';
    } else if (oq.basis === 'assumed_30_days') {
      basisText = 'No unit cost on record: 30 days of demand is assumed.';
    } else {
      basisText = '';
    }
    if (oq.clamped === 'min_cover') {
      basisText += ' Held at the 7-day minimum.';
    } else if (oq.clamped === 'max_cover') {
      basisText += ' Held at the 365-day maximum.';
    }
    return (
      '<section class="q-orderqty" aria-label="Order quantity">' +
        '<h3>Order quantity</h3>' +
        '<dl class="q-oq-facts">' +
          '<div class="q-oq-fact">' +
            '<dt>Recommended order quantity</dt>' +
            '<dd>' +
            esc(
              unitCount(oq.order_quantity_units) +
                ' units \u00b7 ' +
                coverValue(oq.cover_days) +
                ' days of cover'
            ) +
            '</dd>' +
          '</div>' +
          '<div class="q-oq-fact">' +
            '<dt>Recommended max</dt>' +
            '<dd>' + esc(unitCount(oq.recommended_max_units) + ' units') + '</dd>' +
          '</div>' +
          '<div class="q-oq-fact">' +
            '<dt>ERP order quantity</dt>' +
            '<dd>' + esc(unitCount(oq.current_order_qty)) + '</dd>' +
          '</div>' +
          '<div class="q-oq-fact">' +
            '<dt>ERP max</dt>' +
            '<dd>' + esc(unitCount(oq.current_max_qty)) + '</dd>' +
          '</div>' +
        '</dl>' +
        (basisText
          ? '<p class="q-oq-basis">' + esc(basisText) + '</p>'
          : '') +
        '<pre class="q-oq-arithmetic">' + esc(oq.arithmetic || '') + '</pre>' +
      '</section>'
    );
  }

  // A remedy's text value, or an em dash when it is missing: a
  // missing code or date reads as an em dash, the way the queue
  // writes every other missing figure.
  function remedyValue(value) {
    if (value == null) {
      return '\u2014';
    }
    return String(value);
  }

  // The remedy's supplier reading: the supplier_name when it is a
  // non-empty string, else the supplier_code; null when both are
  // missing — the " from <supplier>" part of the line is left out
  // then.
  function remedySupplier(remedy) {
    var name = remedy.supplier_name;
    if (typeof name === 'string' && name !== '') {
      return name;
    }
    var code = remedy.supplier_code;
    if (typeof code === 'string' && code !== '') {
      return code;
    }
    return null;
  }

  // The proposal line's text (issue #1315): it starts with the label
  // of the matching action button — "Expedite: order <order_nbr> line
  // <line_nbr> from <supplier>, <units> units expected
  // <expected_on>.", "Transfer: <units> units from site
  // <from_site>." or "Raise order: <units> units from <supplier>,
  // order by <order_by>.". <supplier> is the supplier_name, else the
  // supplier_code (the " from <supplier>" part left out when both are
  // missing); <units> is whole units with thousands separators, the
  // way the queue writes units elsewhere. null for a remedy of any
  // other kind — the section then shows "No remedy proposed.".
  function remedyProposalText(remedy) {
    var meta = kindMeta(state.kind);
    var label = actionLabelForValue(meta, remedy.kind);
    if (label === '') {
      return null;
    }
    var supplier = remedySupplier(remedy);
    var fromSupplier = supplier === null ? '' : ' from ' + supplier;
    if (remedy.kind === 'expedite') {
      return (
        label +
        ': order ' +
        remedyValue(remedy.order_nbr) +
        ' line ' +
        remedyValue(remedy.line_nbr) +
        fromSupplier +
        ', ' +
        unitCount(remedy.units) +
        ' units expected ' +
        remedyValue(remedy.expected_on) +
        '.'
      );
    }
    if (remedy.kind === 'transfer') {
      return (
        label +
        ': ' +
        unitCount(remedy.units) +
        ' units from site ' +
        remedyValue(remedy.from_site) +
        '.'
      );
    }
    return (
      label +
      ': ' +
      unitCount(remedy.units) +
      ' units' +
      fromSupplier +
      ', order by ' +
      remedyValue(remedy.order_by) +
      '.'
    );
  }

  // The "Proposed remedy" section of the detail pane (issue #1315):
  // for a selected Stockout risk row whose data carries a `remedy`
  // key, directly after the Why block and before the Decision block.
  // The heading reads "Proposed remedy" (the section's aria-label for
  // assistive technology); the proposal line and the remedy's reason
  // follow, every value from the API HTML-escaped. A `remedy` that is
  // null, not an object, or of any other kind shows the section with
  // the single line "No remedy proposed." and no proposal or reason
  // line; a row without the key (an older server) shows no section,
  // and the Reorder points and Excess tabs never show one. Built from
  // the row's own data — the queue makes no request for it — and the
  // section presses or changes no action button.
  function remedyHtml(row) {
    if (
      state.kind !== 'stockout' ||
      !row ||
      typeof row !== 'object' ||
      Array.isArray(row)
    ) {
      return '';
    }
    if (!Object.prototype.hasOwnProperty.call(row, 'remedy')) {
      return '';
    }
    var remedy = row.remedy;
    var proposal =
      remedy && typeof remedy === 'object' && !Array.isArray(remedy)
        ? remedyProposalText(remedy)
        : null;
    var body =
      proposal === null
        ? '<p class="q-remedy-none" data-testid="queue-remedy-none">' +
          'No remedy proposed.' +
          '</p>'
        : '<p class="q-remedy-proposal" data-testid="queue-remedy-proposal">' +
          esc(proposal) +
          '</p>' +
          '<p class="q-remedy-reason" data-testid="queue-remedy-reason">' +
          esc(remedyValue(remedy.reason)) +
          '</p>';
    return (
      '<section class="q-remedy" aria-label="Proposed remedy" ' +
      'data-testid="queue-remedy">' +
      '<h3>Proposed remedy</h3>' +
      body +
      '</section>'
    );
  }

  function renderDecisionBlock(row, badge) {
    var parts = [
      '<section class="q-decision" aria-label="Decision">',
      '<header class="q-decision-head"><h3>Decision</h3>' +
        '<span class="badge ' + badge.cls + '">' + esc(badge.label) + '</span>' +
        '</header>',
    ];
    if (state.isAdmin) {
      parts.push(renderDecisionControls());
    }
    if (state.postError) {
      parts.push('<p class="q-post-error" role="alert">' + esc(state.postError) + '</p>');
    }
    parts.push(renderHistory());
    parts.push('</section>');
    return parts.join('');
  }

  function renderDecisionControls() {
    var meta = kindMeta(state.kind);
    var parts = [];
    if (state.mode === 'none') {
      if (meta.actions.length) {
        parts.push(
          '<div class="q-action-pick" role="group" aria-label="Accept with an action">'
        );
        meta.actions.forEach(function (action) {
          var on = state.actionDraft === action.value;
          parts.push(
            '<button type="button" class="q-option' + (on ? ' is-on' : '') + '" ' +
            'aria-pressed="' + (on ? 'true' : 'false') + '" data-action="' + esc(action.value) + '">' +
            esc(action.label) +
            '</button>'
          );
        });
        parts.push('</div>');
      }
      var acceptLabel =
        meta.kind === 'drift'
          ? 'Accept new reorder point'
          : 'Accept: ' + (actionLabelForValue(meta, state.actionDraft) || 'choose an action');
      // No action pressed (decisions.require_action_choice): the button
      // is disabled and reads "Accept: choose an action"; pressing an
      // action enables it as "Accept: <action label>".
      var needsChoice = meta.actions.length > 0 && !state.actionDraft;
      parts.push(
        '<div class="q-decision-buttons">' +
          '<button type="button" class="btn btn-primary" id="q-accept"' +
            (state.busy || needsChoice ? ' disabled' : '') + '>' + esc(acceptLabel) + '</button>' +
          '<button type="button" class="btn btn-secondary" id="q-dismiss"' +
            (state.busy ? ' disabled' : '') + '>Dismiss</button>' +
          '<button type="button" class="btn btn-secondary" id="q-snooze"' +
            (state.busy ? ' disabled' : '') + '>Snooze</button>' +
        '</div>'
      );
    } else if (state.mode === 'dismiss') {
      parts.push(
        '<div class="q-dismiss">' +
          '<label class="q-field-label" for="q-reason">Why dismiss? A reason is required.</label>' +
          '<textarea id="q-reason" class="q-reason" rows="3" ' +
            'placeholder="e.g. seasonal stock, strategic spare">' +
            esc(state.reasonDraft) +
          '</textarea>' +
          '<div class="q-decision-buttons">' +
            '<button type="button" class="btn btn-primary" id="q-dismiss-confirm"' +
              (state.busy ? ' disabled' : '') + '>Dismiss row</button>' +
            '<button type="button" class="btn btn-secondary" id="q-dismiss-cancel">Cancel</button>' +
          '</div>' +
          (state.reasonError
            ? '<span class="q-post-error q-reason-error" role="alert">Add a reason before dismissing.</span>'
            : '') +
        '</div>'
      );
    } else if (state.mode === 'snooze') {
      parts.push(
        '<div class="q-snooze">' +
          '<span class="q-field-label">Snooze until (at most ' + meta.snoozeMax + ' days)</span>' +
          '<div class="q-snooze-options" role="group" aria-label="Snooze duration">' +
          snoozeOptions(meta)
            .map(function (days) {
              var on = state.snoozeDays === days;
              return (
                '<button type="button" class="q-option' + (on ? ' is-on' : '') + '" ' +
                'aria-pressed="' + (on ? 'true' : 'false') + '" data-days="' + days + '">' +
                days + ' days</button>'
              );
            })
            .join('') +
          '</div>' +
          '<div class="q-decision-buttons">' +
            '<button type="button" class="btn btn-primary" id="q-snooze-confirm"' +
              (state.busy ? ' disabled' : '') + '>Snooze row</button>' +
            '<button type="button" class="btn btn-secondary" id="q-snooze-cancel">Cancel</button>' +
          '</div>' +
        '</div>'
      );
    }
    return parts.join('');
  }

  function renderHistory() {
    if (!state.history || !state.history.length) {
      return '';
    }
    return (
      '<div class="q-history">' +
        '<span class="q-history-title">History</span>' +
        '<ul class="q-history-list">' +
        state.history.map(historyItemHtml).join('') +
        '</ul>' +
      '</div>'
    );
  }

  function historyItemHtml(entry) {
    var label = 'Undecided';
    if (entry.decision === 'accepted') {
      label = 'Accepted';
      var action = actionLabel(entry);
      if (action) {
        label += ' \u00b7 ' + action;
      }
    } else if (entry.decision === 'dismissed') {
      label = 'Dismissed';
    } else if (entry.decision === 'snoozed') {
      label = 'Snoozed';
    } else {
      label = String(entry.decision);
    }
    var by = entry.decided_by ? ' by ' + esc(entry.decided_by) : '';
    var extras = [];
    if (entry.reason) {
      extras.push('reason: ' + esc(entry.reason));
    }
    if (entry.snooze_until) {
      extras.push('until ' + esc(String(entry.snooze_until)));
    }
    var extraHtml = extras.length ? ' <span class="q-history-extra">(' + extras.join('; ') + ')</span>' : '';
    return (
      '<li class="q-history-item">' +
        '<span class="q-history-date">' + esc(formatDecidedAt(entry.decided_at)) + '</span> ' +
        esc(label) + by + extraHtml +
      '</li>'
    );
  }

  function renderLoadingOrError() {
    if (!state.loading && state.loadError) {
      return (
        '<div class="q-error" role="alert">' +
          '<p class="q-error-msg">Couldn\u2019t load the work queue' +
          (state.loadError ? ': ' + esc(state.loadError) : '') + '.</p>' +
          '<button type="button" class="btn btn-secondary" id="q-retry">Retry</button>' +
        '</div>'
      );
    }
    var rows = '';
    for (var i = 0; i < 6; i++) {
      rows += '<span class="skel skel-q-row" aria-hidden="true"></span>';
    }
    return (
      '<div class="q-bar" aria-hidden="true">' +
        '<span class="skel skel-q-bar"></span>' +
      '</div>' +
      '<div class="q-split">' +
        '<section class="q-list" aria-label="Rows">' +
          '<div class="q-list-body">' + rows + '</div>' +
        '</section>' +
        '<aside class="q-detail">' +
          '<div class="q-detail-inner" aria-hidden="true">' +
            '<span class="skel skel-line"></span>' +
            '<span class="skel skel-value"></span>' +
            '<span class="skel skel-figures"></span>' +
          '</div>' +
        '</aside>' +
      '</div>'
    );
  }

  /* -------------------------------- data -------------------------------- */

  // The list answers (the three kinds' lists and the decision-history
  // reads): an array of row objects. A list element that is not an
  // object (a null the server may list, an array or a scalar) is
  // dropped — the queue renders the rows it has and a null list
  // element never crashes the render.
  function asRows(value) {
    if (!Array.isArray(value)) {
      return [];
    }
    var rows = [];
    for (var i = 0; i < value.length; i++) {
      var row = value[i];
      if (row && typeof row === 'object' && !Array.isArray(row)) {
        rows.push(row);
      }
    }
    return rows;
  }

  // The settings the queue applies on load (GET /api/settings). Only a
  // well-formed entry with a value the queue understands is applied;
  // anything else (missing, huge, non-boolean, off-enum, NUL bytes)
  // leaves the default in place.
  function applySettings(entries) {
    if (!Array.isArray(entries)) {
      return;
    }
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i];
      if (!entry || typeof entry !== 'object') {
        continue;
      }
      if (entry.key === 'decisions.require_action_choice') {
        if (entry.value === true || entry.value === false) {
          state.requireActionChoice = entry.value;
        }
      } else if (entry.key === 'queue.default_filter') {
        if (entry.value === 'open' || entry.value === 'decided' || entry.value === 'all') {
          state.defaultFilter = entry.value;
        }
      }
    }
  }

  // The answer of GET /api/erp-sources (issue #1003). Only a
  // well-formed payload is applied: the sources with a non-empty
  // source code feed the filter's options, and the item-sites'
  // (item_code, site_code) pairs feed the rows' tags. With fewer than
  // two source entries nothing is applied — one entry or none reads
  // as no answer at all: no tag, no filter. A malformed answer never
  // errors and never changes the page.
  function applyErpSources(payload) {
    var byItemSite = {};
    var list = [];
    if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
      var itemSites = Array.isArray(payload.item_sites) ? payload.item_sites : [];
      for (var i = 0; i < itemSites.length; i++) {
        var entry = itemSites[i];
        if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
          continue;
        }
        var item = typeof entry.item_code === 'string' ? entry.item_code : '';
        var site = typeof entry.site_code === 'string' ? entry.site_code : '';
        if (item === '' || site === '') {
          continue;
        }
        var source =
          typeof entry.source_system === 'string' && entry.source_system !== ''
            ? entry.source_system
            : null;
        byItemSite[item + '\u0000' + site] = source;
      }
      var sources = Array.isArray(payload.sources) ? payload.sources : [];
      for (var j = 0; j < sources.length; j++) {
        var src = sources[j];
        var code =
          src && typeof src === 'object' && !Array.isArray(src) && typeof src.source_system === 'string'
            ? src.source_system
            : '';
        if (code !== '') {
          list.push({ code: code, label: erpLabelFor(code) });
        }
      }
    }
    if (list.length < 2) {
      // Only with several ERPs: one entry or none means no tag and no
      // filter — the page reads exactly as it does without the
      // answer.
      clearErpSources();
      return;
    }
    state.erpSources = payload;
    state.erpSourceByItemSite = byItemSite;
    state.erpSourcesList = list;
    if (state.erpFilter !== 'all' && !erpSourceListed(state.erpFilter)) {
      state.erpFilter = 'all';
    }
    recomputeList();
  }

  // Drop the applied answer — a failed read, or an answer with fewer
  // than two sources: the tags and the filter vanish and the filter
  // falls back to "All ERPs". The page reads as it does today, with
  // no error.
  function clearErpSources() {
    state.erpSources = null;
    state.erpSourceByItemSite = {};
    state.erpSourcesList = [];
    if (state.erpFilter !== 'all') {
      state.erpFilter = 'all';
      recomputeList();
    }
  }

  function startLoad() {
    if (state.loading) {
      return;
    }
    state.loading = true;
    state.loadError = null;
    render();
    var meCall = global.Api.get('/api/auth/me')
      .then(function (me) {
        state.isAdmin =
          !!me &&
          me.authenticated &&
          !!me.user &&
          Array.isArray(me.user.roles) &&
          me.user.roles.indexOf('admin') !== -1;
        return null;
      })
      .catch(function () {
        // A 401 already restarted the sign-in flow in api.js; any other
        // failure leaves the queue read-only.
        state.isAdmin = false;
        return null;
      });
    // The ERP sources (issue #1003): fetched once with the three
    // lists, but the lists never wait for it — it is not part of the
    // Promise.all. The sequence guard renders only the newest answer
    // (a sign-out or a newer load supersedes it); a failed read leaves
    // the page exactly as it reads without sources: no tag, no filter,
    // no error.
    state.erpSourcesSeq += 1;
    var erpSeq = state.erpSourcesSeq;
    global.Api.get('/api/erp-sources').then(
      function (payload) {
        if (erpSeq !== state.erpSourcesSeq) {
          return;
        }
        applyErpSources(payload);
        if (state.mode === 'none') {
          render();
        }
      },
      function () {
        if (erpSeq !== state.erpSourcesSeq) {
          return;
        }
        clearErpSources();
        if (state.mode === 'none') {
          render();
        }
      }
    );
    var calls = [
      meCall,
      // Settings never block the load: a failed read keeps the defaults
      // (choice required, To decide) — the queue still loads.
      global.Api.get('/api/settings').then(
        function (entries) {
          applySettings(entries);
        },
        function () {}
      ),
      global.Api.get(KINDS[0].listPath),
      global.Api.get(KINDS[1].listPath),
      global.Api.get(KINDS[2].listPath),
    ];
    Promise.all(calls).then(
      function (results) {
        state.rows = {
          drift: asRows(results[2]),
          excess: asRows(results[3]),
          stockout: asRows(results[4]),
        };
        state.loading = false;
        state.ready = true;
        // queue.default_filter: the chip the queue opens with. The chips
        // are absent while the skeleton shows, so no chip can have been
        // pressed yet — the stored value wins on a fresh load.
        state.filter = state.defaultFilter;
        recomputeList();
        resolveSelection();
      },
      function (error) {
        state.loading = false;
        state.loadError =
          error && error.message ? error.message : 'the server could not be reached';
        render();
      }
    );
  }

  // Apply the URL hash to the queue state: ?kind=… picks the tab, and
  // ?item=…&site=… marks the row to select once the lists are in hand.
  function applyUrl() {
    var hash = global.location.hash || '';
    var queryIndex = hash.indexOf('?');
    if (queryIndex === -1) {
      return;
    }
    var params = new URLSearchParams(hash.slice(queryIndex + 1));
    var kind = params.get('kind');
    if (kind && KINDS_BY_KIND[kind] && kind !== state.kind) {
      state.kind = kind;
      state.selected = null;
      state.mode = 'none';
      state.postError = null;
      state.reasonError = false;
      state.reasonDraft = '';
      state.history = null;
      state.historyFor = null;
      recomputeList();
    }
    var item = params.get('item');
    var site = params.get('site');
    if (item && site) {
      state.pending = { item: item, site: site };
    }
  }

  function defaultActionDraft() {
    var meta = kindMeta(state.kind);
    if (!meta.actions.length) {
      return null;
    }
    // decisions.require_action_choice (the default) opens the pane with
    // no action pressed; only the explicit false pre-selects the first
    // action, as before.
    if (state.requireActionChoice) {
      return null;
    }
    return meta.actions[0].value;
  }

  // Choose the selected row: the URL's row when it exists (switching the
  // filter to All when the current filter would hide it), else the
  // current selection when it is still valid, else the first row of the
  // current filter (or of every row when the filter is empty).
  function resolveSelection() {
    state.actionDraft = defaultActionDraft();
    if (state.pending) {
      var target = findRow(state.kind, state.pending.item, state.pending.site);
      state.pending = null;
      if (target) {
        state.selected = { item: target.item_code, site: target.site_code };
        var visible = visibleRows().some(function (row) {
          return row === target;
        });
        if (state.filter !== 'all' && !visible) {
          state.filter = 'all';
          recomputeList();
        }
        loadDraftPoPrefill(target.item_code, target.site_code);
        render();
        loadHistory(target.item_code, target.site_code);
        return;
      }
    }
    var row = selectedRow();
    if (row) {
      loadDraftPoPrefill(row.item_code, row.site_code);
      render();
      loadHistory(row.item_code, row.site_code);
      return;
    }
    selectFirstVisible();
  }

  function selectFirstVisible() {
    var rows = visibleRows();
    var pick = null;
    if (rows.length) {
      pick = rows[0];
    } else {
      var all = rowsFor(state.kind);
      if (all.length) {
        pick = all[0];
      }
    }
    state.actionDraft = defaultActionDraft();
    if (pick) {
      state.selected = { item: pick.item_code, site: pick.site_code };
      loadDraftPoPrefill(pick.item_code, pick.site_code);
      render();
      loadHistory(pick.item_code, pick.site_code);
    } else {
      state.selected = null;
      state.history = null;
      state.historyFor = null;
      state.draftPoFor = null;
      state.draftPoSeq += 1;
      state.draftPo = { supplier: '', qty: '', price: '', busy: state.draftPo.busy, resultHtml: '' };
      render();
    }
  }

  function loadHistory(item, site) {
    state.history = null;
    state.historyFor = { item: item, site: site };
    state.historyLoading = true;
    var meta = kindMeta(state.kind);
    var path =
      meta.decisionsPath +
      '?' +
      'item_code=' + encodeURIComponent(item) +
      '&site_code=' + encodeURIComponent(site);
    global.Api.get(path).then(
      function (entries) {
        state.historyLoading = false;
        var stillCurrent =
          state.historyFor && state.historyFor.item === item && state.historyFor.site === site;
        if (stillCurrent) {
          state.history = asRows(entries);
          if (state.mode === 'none') {
            render();
          }
        }
      },
      function () {
        // History is supplementary: a failed read hides the block. The
        // re-render is what clears it — the row switch that started this
        // load already painted the pane with the previous row's history
        // (render runs before this load nulls state.history), so without
        // it that stale block would stay under the new row. A failed read
        // for a row the user has moved on from must not touch the
        // current row's history, hence stillCurrent.
        state.historyLoading = false;
        var stillCurrent =
          state.historyFor && state.historyFor.item === item && state.historyFor.site === site;
        if (stillCurrent) {
          state.history = [];
          if (state.mode === 'none') {
            render();
          }
        }
      }
    );
  }

  /* ------------------------- approve → draft PO ----------------------- */

  // The admin-only "Approve → draft PO" section of the detail pane:
  // it renders only for a signed-in admin (the same /api/auth/me roles
  // check the decision controls use) and only for a selected Stockout
  // risk or Reorder point row — never for Excess, never for a
  // non-admin. It drafts one Odoo purchase order through
  // POST /api/replenishment/actions/draft-po; it records no decision
  // and never touches the row's triage state.

  function draftPoEligible() {
    return state.isAdmin && (state.kind === 'drift' || state.kind === 'stockout');
  }

  // The three editable values as they would be sent: the supplier as
  // text, the quantity and the unit price as numbers. The buttons stay
  // disabled until the supplier is non-blank, the quantity is a number
  // of at least 1 and the unit price a number of at least 0.
  function draftPoInput() {
    var supplier = state.draftPo.supplier.trim();
    var qty = Number(state.draftPo.qty);
    var price = Number(state.draftPo.price);
    return {
      supplier: supplier,
      qty: qty,
      price: price,
      valid:
        supplier !== '' &&
        isFinite(qty) &&
        qty >= 1 &&
        isFinite(price) &&
        price >= 0,
    };
  }

  // Update the two buttons' disabled state in place — a re-render
  // would rebuild the inputs and steal focus mid-typing.
  function syncDraftPoButtons() {
    if (!rootEl) {
      return;
    }
    var disabled = state.draftPo.busy || !draftPoInput().valid;
    var preview = rootEl.querySelector('#q-draftpo-preview');
    var confirm = rootEl.querySelector('#q-draftpo-confirm');
    if (preview) {
      preview.disabled = disabled;
    }
    if (confirm) {
      confirm.disabled = disabled;
    }
  }

  // A newly selected row starts with a blank form; the by-key
  // recommendation (GET /api/recommendations/by-key) prefills the
  // quantity and the unit price when each is a finite number. A failed
  // or 404 read leaves the fields blank — the section stays fully
  // usable — and never throws. Re-selecting the same row (a
  // hashchange reactivation) does not re-fetch or clear anything.
  function loadDraftPoPrefill(item, site) {
    var same =
      !!state.draftPoFor && state.draftPoFor.item === item && state.draftPoFor.site === site;
    if (same) {
      return;
    }
    state.draftPoSeq += 1;
    state.draftPoFor = { item: item, site: site };
    // A draft-po request still in flight keeps the buttons disabled
    // across the row switch (any Preview or Create in flight); it
    // releases itself when it settles.
    state.draftPo = { supplier: '', qty: '', price: '', busy: state.draftPo.busy, resultHtml: '' };
    if (!draftPoEligible()) {
      return;
    }
    var seq = state.draftPoSeq;
    var path =
      '/api/recommendations/by-key?item_code=' +
      encodeURIComponent(item) +
      '&site_code=' +
      encodeURIComponent(site);
    global.Api.get(path).then(
      function (rec) {
        if (seq !== state.draftPoSeq) {
          return; // a newer row was selected while this read was out
        }
        var changed = false;
        if (rec && typeof rec === 'object') {
          // Only a still-blank field is prefilled: a value the planner
          // already typed wins over the late read.
          if (state.draftPo.qty === '' && isMoney(rec.suggested_order_quantity)) {
            state.draftPo.qty = String(rec.suggested_order_quantity);
            changed = true;
          }
          if (state.draftPo.price === '' && isMoney(rec.unit_cost)) {
            state.draftPo.price = String(rec.unit_cost);
            changed = true;
          }
        }
        if (changed) {
          render();
        }
      },
      function () {
        // A failed or 404 by-key leaves the (already blank) fields as
        // they are; the section stays usable.
      }
    );
  }

  // The result region's text for a successful answer. An `existing`
  // object (with a name) wins for the already-exists / already-existed
  // texts; the top-level name and origin carry the created /
  // would-create texts. Every string is escaped before it reaches the
  // page.
  function draftPoResultHtml(confirm, data) {
    data = data && typeof data === 'object' ? data : {};
    var existing = data.existing;
    var hasExisting = !!existing && typeof existing === 'object';
    var name = typeof data.name === 'string' ? data.name : '';
    if (hasExisting) {
      var existingName = typeof existing.name === 'string' ? existing.name : '';
      var shown = existingName !== '' ? existingName : name;
      return (
        (confirm
          ? 'Draft purchase order already existed: '
          : 'A draft purchase order already exists: ') + esc(shown)
      );
    }
    if (confirm) {
      return 'Draft purchase order created: ' + esc(name);
    }
    var origin = typeof data.origin === 'string' ? data.origin : '';
    return (
      'Would create a draft purchase order' +
      (origin !== '' ? ' (origin: ' + esc(origin) + ')' : '')
    );
  }

  // The Odoo target codes the answer carried under `target` (the codes
  // the Odoo client was actually called with): a second line in the
  // result region, every value escaped before it reaches the page. An
  // answer without a `target` object renders exactly as before — this
  // returns ''.
  function draftPoTargetHtml(data) {
    var target =
      data &&
      typeof data === 'object' &&
      typeof data.target === 'object' &&
      !Array.isArray(data.target) &&
      data.target
        ? data.target
        : null;
    if (target === null) {
      return '';
    }
    var item = typeof target.item_code === 'string' ? target.item_code : '';
    var site = typeof target.site_code === 'string' ? target.site_code : '';
    var supplier = typeof target.supplier_code === 'string' ? target.supplier_code : '';
    return (
      ' <span class="q-draftpo-target" data-testid="queue-draftpo-target">Odoo item ' +
      esc(item) +
      ' \u00b7 site ' +
      esc(site) +
      ' \u00b7 vendor ' +
      esc(supplier) +
      '</span>'
    );
  }

  function draftPoErrorText(error) {
    var status = error && typeof error.status === 'number' ? error.status : null;
    var hasDetail = error && typeof error.message === 'string' && error.message !== '';
    if (hasDetail && (status === 502 || (status >= 400 && status < 500))) {
      // A 409's "Odoo writes are disabled", a 502's upstream detail and
      // a 422's validation detail: the server's own message.
      return error.message;
    }
    return 'The draft purchase order could not be created. Please try again.';
  }

  function renderDraftPoBlock() {
    var disabled = state.draftPo.busy || !draftPoInput().valid;
    return (
      '<section class="q-draftpo" data-testid="queue-draftpo" aria-label="Approve as a draft purchase order">' +
        '<header class="q-draftpo-head">' +
          '<h3>Approve → draft PO</h3>' +
        '</header>' +
        '<div class="q-draftpo-fields">' +
          '<div class="q-draftpo-field">' +
            '<label class="q-field-label" for="queue-draftpo-supplier">Supplier</label>' +
            '<input id="queue-draftpo-supplier" data-testid="queue-draftpo-supplier" type="text" ' +
              'class="q-draftpo-input" autocomplete="off" spellcheck="false" ' +
              'value="' + esc(state.draftPo.supplier) + '">' +
          '</div>' +
          '<div class="q-draftpo-field">' +
            '<label class="q-field-label" for="queue-draftpo-qty">Quantity</label>' +
            '<input id="queue-draftpo-qty" data-testid="queue-draftpo-qty" type="number" ' +
              'min="1" step="1" class="q-draftpo-input" value="' + esc(state.draftPo.qty) + '">' +
          '</div>' +
          '<div class="q-draftpo-field">' +
            '<label class="q-field-label" for="queue-draftpo-price">Unit price</label>' +
            '<input id="queue-draftpo-price" data-testid="queue-draftpo-price" type="number" ' +
              'min="0" step="0.01" class="q-draftpo-input" value="' + esc(state.draftPo.price) + '">' +
          '</div>' +
        '</div>' +
        '<div class="q-draftpo-buttons">' +
          '<button type="button" class="btn btn-secondary" id="q-draftpo-preview" ' +
            'data-testid="queue-draftpo-preview"' + (disabled ? ' disabled' : '') + '>Preview</button>' +
          '<button type="button" class="btn btn-primary" id="q-draftpo-confirm" ' +
            'data-testid="queue-draftpo-confirm"' + (disabled ? ' disabled' : '') + '>Create draft PO</button>' +
        '</div>' +
        '<p class="q-draftpo-result" role="status" data-testid="queue-draftpo-result">' +
          state.draftPo.resultHtml +
        '</p>' +
      '</section>'
    );
  }

  // Preview (confirm false) and Create (confirm true) send the same
  // body: the selected row's item and site, the supplier as text and
  // the quantity and unit price as numbers. The answer — success or
  // the server's 409 / 502 / 422 detail — renders in the section's
  // own status region; the decision and row state is untouched either
  // way, and a failed read never disables the section.
  function postDraftPo(confirm) {
    if (!draftPoEligible() || state.draftPo.busy) {
      return;
    }
    var row = selectedRow();
    var input = draftPoInput();
    if (!row || !input.valid) {
      return;
    }
    var item = row.item_code;
    var site = row.site_code;
    var body = {
      item_code: item,
      site_code: site,
      supplier_code: input.supplier,
      quantity: input.qty,
      unit_price: input.price,
      confirm: confirm,
    };
    state.draftPo.busy = true;
    state.draftPo.resultHtml = '';
    render();
    global.Api.request('/api/replenishment/actions/draft-po', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(
      function (data) {
        var wasCurrent =
          state.draftPoFor && state.draftPoFor.item === item && state.draftPoFor.site === site;
        state.draftPo.busy = false;
        if (wasCurrent) {
          // When the answer carries `target`, the target line is part
          // of the result region's markup, right after the outcome
          // text; an answer without it renders exactly as before.
          state.draftPo.resultHtml =
            draftPoResultHtml(confirm, data) + draftPoTargetHtml(data);
        }
        render();
      },
      function (error) {
        var wasCurrent =
          state.draftPoFor && state.draftPoFor.item === item && state.draftPoFor.site === site;
        state.draftPo.busy = false;
        if (wasCurrent) {
          state.draftPo.resultHtml =
            '<span class="q-draftpo-error">' + esc(draftPoErrorText(error)) + '</span>';
        }
        render();
      }
    );
  }

  /* ------------------------------- actions ------------------------------ */

  function resetTransient() {
    state.mode = 'none';
    state.postError = null;
    state.reasonError = false;
    state.reasonDraft = '';
    state.actionDraft = defaultActionDraft();
    state.snoozeDays = DEFAULT_SNOOZE_DAYS;
  }

  function selectTab(kind) {
    if (!state.ready || !KINDS_BY_KIND[kind] || kind === state.kind) {
      return;
    }
    state.kind = kind;
    state.pending = null;
    state.selected = null;
    state.history = null;
    state.historyFor = null;
    resetTransient();
    recomputeList();
    selectFirstVisible();
  }

  function setFilter(filter) {
    if (!state.ready || filter !== 'open' && filter !== 'decided' && filter !== 'all') {
      return;
    }
    state.filter = filter;
    state.mode = 'none';
    state.postError = null;
    state.reasonError = false;
    state.reasonDraft = '';
    recomputeList();
    render();
  }

  // The ERP filter's select (issue #1003): the same re-slice the
  // chips run — the list pane and the tab counts follow it, and a
  // row without a source shows only under "All ERPs". When the new
  // filter hides the selected row, the first row of the new list is
  // selected, the same way a chip press behaves. No request goes out.
  function setErpFilter(source) {
    if (!state.ready) {
      return;
    }
    if (source !== 'all' && !erpSourceListed(source)) {
      return;
    }
    state.erpFilter = source;
    state.mode = 'none';
    state.postError = null;
    state.reasonError = false;
    state.reasonDraft = '';
    recomputeList();
    var selected = state.selected;
    var rows = visibleRows();
    var stillVisible = false;
    if (selected) {
      for (var i = 0; i < rows.length; i++) {
        if (rows[i].item_code === selected.item && rows[i].site_code === selected.site) {
          stillVisible = true;
          break;
        }
      }
    }
    if (stillVisible) {
      render();
    } else {
      selectFirstVisible();
    }
  }

  function selectRow(item, site) {
    if (!state.ready) {
      return;
    }
    state.selected = { item: item, site: site };
    state.pending = null;
    resetTransient();
    loadDraftPoPrefill(item, site);
    render();
    loadHistory(item, site);
  }

  function openMode(mode) {
    state.mode = mode;
    state.postError = null;
    state.reasonError = false;
    if (mode === 'dismiss') {
      state.reasonDraft = '';
    }
    render();
  }

  function postDecision(decision, extra) {
    var row = selectedRow();
    if (!row || state.busy) {
      return;
    }
    var meta = kindMeta(state.kind);
    var body = {
      item_code: row.item_code,
      site_code: row.site_code,
      decision: decision,
    };
    if (extra) {
      for (var key in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, key)) {
          body[key] = extra[key];
        }
      }
    }
    state.busy = true;
    state.postError = null;
    render();
    global.Api.request(meta.decisionsPath, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(
      function (stored) {
        state.busy = false;
        row.decision =
          stored && typeof stored === 'object' ? stored : { decision: decision };
        resetTransient();
        render();
        notifyOverview();
        loadHistory(row.item_code, row.site_code);
      },
      function (error) {
        state.busy = false;
        var clientError =
          error && typeof error.status === 'number' && error.status >= 400 && error.status < 500;
        state.postError = clientError
          ? error.message
          : 'The decision could not be saved. Please try again.';
        render();
      }
    );
  }

  // A recorded decision (any of the three kinds) makes the Overview's
  // figures stale: its next visit re-requests the summary and the
  // lists and shows the new figures, without a page reload.
  function notifyOverview() {
    if (global.Overview && typeof global.Overview.invalidate === 'function') {
      global.Overview.invalidate();
    }
  }

  function confirmAccept() {
    var meta = kindMeta(state.kind);
    // No action chosen: nothing to send. The disabled button already
    // keeps a click away; the guard keeps a forced one harmless too.
    if (meta.actions.length && !state.actionDraft) {
      return;
    }
    if (meta.actions.length) {
      postDecision('accepted', { action: state.actionDraft });
    } else {
      postDecision('accepted');
    }
  }

  function confirmDismiss() {
    var reason = state.reasonDraft.trim();
    if (!reason) {
      state.reasonError = true;
      render();
      return;
    }
    postDecision('dismissed', { reason: reason });
  }

  function confirmSnooze() {
    postDecision('snoozed', { snooze_until: snoozeDate(state.snoozeDays) });
  }

  /* ------------------------------- downloads ---------------------------- */

  // Start a CSV download: the active tab's own export (which) is
  // 'list', or the planning-parameter sheet ('plan'). The clicked
  // button is disabled while the fetch runs; on failure the bar shows
  // "Download failed. <message>" in its status region, cleared by the
  // next click. No request goes out until a button is clicked.
  function startDownload(which) {
    if (state.downloadBusy === which) {
      return;
    }
    var target =
      which === 'plan'
        ? PLANNING_DOWNLOAD
        : DOWNLOADS_BY_KIND[state.kind] || DOWNLOADS_BY_KIND.drift;
    state.downloadBusy = which;
    state.downloadError = null;
    render();
    global.Api.download(target.path, target.filename)
      .then(function () {
        state.downloadBusy = null;
        render();
      })
      .catch(function (error) {
        state.downloadBusy = null;
        state.downloadError =
          'Download failed.' + (error && error.message ? ' ' + error.message : '');
        render();
      });
  }

  /* -------------------------------- events ------------------------------ */

  function onRootClick(event) {
    var button = event.target && event.target.closest ? event.target.closest('button') : null;
    if (!button || !rootEl.contains(button)) {
      return;
    }
    if (button.hasAttribute('data-kind')) {
      selectTab(button.getAttribute('data-kind'));
      return;
    }
    if (button.hasAttribute('data-filter')) {
      setFilter(button.getAttribute('data-filter'));
      return;
    }
    if (button.hasAttribute('data-action')) {
      state.actionDraft = button.getAttribute('data-action');
      render();
      return;
    }
    if (button.hasAttribute('data-days')) {
      state.snoozeDays = Number(button.getAttribute('data-days'));
      render();
      return;
    }
    if (button.classList && button.classList.contains('q-row')) {
      selectRow(button.getAttribute('data-item'), button.getAttribute('data-site'));
      return;
    }
    if (button.classList && button.classList.contains('q-download')) {
      startDownload('list');
      return;
    }
    if (button.classList && button.classList.contains('q-download-plan')) {
      startDownload('plan');
      return;
    }
    switch (button.id) {
      case 'q-accept':
        confirmAccept();
        break;
      case 'q-dismiss':
        openMode('dismiss');
        break;
      case 'q-snooze':
        openMode('snooze');
        break;
      case 'q-dismiss-confirm':
        confirmDismiss();
        break;
      case 'q-dismiss-cancel':
        openMode('none');
        break;
      case 'q-snooze-confirm':
        confirmSnooze();
        break;
      case 'q-snooze-cancel':
        openMode('none');
        break;
      case 'q-draftpo-preview':
        postDraftPo(false);
        break;
      case 'q-draftpo-confirm':
        postDraftPo(true);
        break;
      case 'q-retry':
        startLoad();
        break;
      default:
        break;
    }
  }

  function onRootInput(event) {
    var target = event.target;
    if (!target) {
      return;
    }
    if (target.id === 'queue-draftpo-supplier') {
      state.draftPo.supplier = target.value;
      syncDraftPoButtons();
      return;
    }
    if (target.id === 'queue-draftpo-qty') {
      state.draftPo.qty = target.value;
      syncDraftPoButtons();
      return;
    }
    if (target.id === 'queue-draftpo-price') {
      state.draftPo.price = target.value;
      syncDraftPoButtons();
      return;
    }
    if (target.id !== 'q-reason') {
      return;
    }
    state.reasonDraft = target.value;
    // Clear the blank-reason alert as soon as a reason exists, without a
    // re-render that would steal focus from the textarea.
    if (state.reasonError && state.reasonDraft.trim()) {
      state.reasonError = false;
      var alert = rootEl.querySelector('.q-reason-error');
      if (alert && alert.parentNode) {
        alert.parentNode.removeChild(alert);
      }
    }
  }

  // The ERP filter's select (issue #1003): a change re-slices the
  // list and the tab counts — no request goes out for it.
  function onRootChange(event) {
    var target = event.target;
    if (!target || typeof target.closest !== 'function') {
      return;
    }
    var select = target.closest('select[data-testid="queue-erp-filter"]');
    if (select && rootEl.contains(select)) {
      setErpFilter(select.value);
    }
  }

  /* --------------------------------- boot ------------------------------- */

  function activate() {
    var appEl = document.getElementById('app');
    if (!appEl || appEl.hidden || !queueActive()) {
      return;
    }
    applyUrl();
    if (state.ready) {
      resolveSelection();
    } else if (!state.loading) {
      startLoad();
    }
  }

  function reset() {
    state.ready = false;
    state.loading = false;
    state.loadError = null;
    state.isAdmin = false;
    state.kind = 'drift';
    state.filter = 'open';
    state.requireActionChoice = true;
    state.defaultFilter = 'open';
    state.rows = { drift: null, excess: null, stockout: null };
    state.listRows = [];
    state.selected = null;
    state.pending = null;
    state.mode = 'none';
    state.actionDraft = null;
    state.reasonDraft = '';
    state.reasonError = false;
    state.snoozeDays = DEFAULT_SNOOZE_DAYS;
    state.postError = null;
    state.busy = false;
    state.downloadBusy = null;
    state.downloadError = null;
    state.history = null;
    state.historyFor = null;
    state.historyLoading = false;
    state.draftPo = { supplier: '', qty: '', price: '', busy: false, resultHtml: '' };
    state.draftPoFor = null;
    state.draftPoSeq += 1;
    state.erpSources = null;
    state.erpSourceByItemSite = {};
    state.erpSourcesList = [];
    state.erpFilter = 'all';
    state.erpSourcesSeq += 1;
    if (rootEl) {
      rootEl.innerHTML = '';
    }
  }

  (function boot() {
    rootEl = document.getElementById('q-root');
    if (!rootEl) {
      return;
    }
    rootEl.addEventListener('click', onRootClick);
    rootEl.addEventListener('input', onRootInput);
    rootEl.addEventListener('change', onRootChange);

    var appEl = document.getElementById('app');
    if (!appEl) {
      return;
    }
    // The queue loads once the signed-in shell is visible and the queue
    // view is the active one; the sign-in flow (auth.js) shows #app and
    // the hashchange handler (app.js) calls activate() on every switch.
    var observer = new MutationObserver(function () {
      if (!appEl.hidden && queueActive()) {
        activate();
      }
    });
    observer.observe(appEl, { attributes: true, attributeFilter: ['hidden'] });
    if (!appEl.hidden && queueActive()) {
      activate();
    }
  })();

  global.Queue = {
    activate: activate,
    reset: reset,
  };
})(window);
