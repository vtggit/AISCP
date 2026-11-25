// Proving spec for AIERP issue #1601 — the partial-list notices.
//
// The Overview's three list reads and the Demand view's list read are
// each limited to 1,000 entries, so an answer that arrives as an
// array of exactly 1,000 is a cut answer, and the UI must say so:
//
//   * Overview (app/js/overview.js): for a card whose list is full,
//     the card's to-decide label (.ov-card-to-decide) reads
//     "at least <count> to decide" — <count> the count the card
//     shows today (the undecided rows of the card's own list read),
//     formatted the same way. A card whose list is not full reads
//     exactly as today. The three list reads are unchanged (GET
//     /api/drift|excess|stockout?limit=1000&include_decided=true),
//     and so are every other figure on the cards (they come from
//     /api/decisions/summary), the cards' blocks, the attention
//     list, every other region and every request.
//   * Demand (app/js/demand.js): when the list answer
//     (GET /api/forecast?limit=1000, unchanged) is an array of
//     exactly 1,000, the list pane shows, above its rows and its
//     column header, one <p class="dm-list-cut"
//     data-testid="demand-list-cut" role="status"> whose text is
//     exactly "Showing the first 1,000 item-sites. There may be
//     more." With fewer entries, while loading, after an error or
//     with no rows there is no such line, and a demand-class filter
//     chip does not change it: it describes what was loaded. The
//     rows, the chips and their counts, the chosen row, the detail
//     pane, the accuracy reads and every request are unchanged.
//
// The gate serves the static app with no backend, so every /api/*
// call is mocked with page.route — the Overview the way
// app/tests/issue1534.spec.js mocks it (the development token card
// at 1440x900 on #/overview as the admin planner1, with the same
// summary, block and shell answers), and the Demand view the way
// app/tests/issue1461.spec.js mocks it (the development token card
// at #/demand, the forecast rows with their by-key details, the two
// accuracy answers and the empty overrides). The list answers are
// steered through the mocks: the Overview's stockout list holds
// exactly 1,000 rows of which 600 have no decision — the drift and
// excess lists hold 3 rows each, all undecided — for the full case,
// and 999 rows of which 600 have no decision for the not-full case;
// the forecast list holds exactly 1,000 rows for the full case —
// one of them carrying the hostile item_code
// "<img src=x onerror=window.__pwned=1>", which must reach the page
// as text only — and 999 rows for the not-full case.
//
// The pass proves, in order:
//   * OVERVIEW, FULL: the Stockout risk card's to-decide label
//     reads "at least 600 to decide", the Reorder points and the
//     Excess and dead stock cards read "3 to decide" — their lists
//     are not full — while the cards' figures still come from
//     /api/decisions/summary, the cards' blocks render, the value
//     tiles read their summary figures, the attention list holds
//     its 5 rows, and every list read went out as a GET with the
//     query ?limit=1000&include_decided=true;
//   * OVERVIEW, NOT FULL: the stockout list holding 999 rows of
//     which 600 have no decision reads "600 to decide" on its card
//     — no "at least";
//   * DEMAND, FULL: with the forecast list holding exactly 1,000
//     rows, the list pane holds exactly one
//     [data-testid="demand-list-cut"], above its rows and its
//     column header, reading "Showing the first 1,000 item-sites.
//     There may be more." with role="status" and class dm-list-cut;
//     the list read went out as a GET with the query ?limit=1000;
//     the first row is chosen and its detail renders with the
//     admin's 12 Override buttons and its Add signal button;
//     choosing a demand-class chip narrows the rows while the line
//     stays, exactly once; the hostile item_code renders as text,
//     its row holds no <img> and window.__pwned stays undefined;
//   * DEMAND, NOT FULL: with 999 rows there is no
//     [data-testid="demand-list-cut"] on the page;
//   * and ends as a NON-ADMIN (role 'user') after a full reload,
//     the way app/tests/issue1534.spec.js's viewer pass does: the
//     token arrives in the URL hash as #access_token=...,
//     /api/auth/config and /api/auth/me are mocked for that user,
//     the IdP's auth and token endpoints carry the PKCE
//     round-trip, the shell renders for the viewer with the
//     full-list notices still up on both views, and the admin-only
//     controls — the Demand view's Override buttons and its Add
//     signal button — are absent while the page still renders.
//
// Code inside page.evaluate uses only values written inside that
// callback or passed to it as plain data.
//
// The test is TOP-LEVEL and its title is the full proof name
// ("issue1601 freeform"): the merge gate builds its junit nodeids
// from the Playwright test title, and a describe() block would join
// the titles with a "›" the contract nodeid does not carry, so the
// proof would read as "not collected".

const { test, expect } = require('@playwright/test');

// CodeAgent terminal-state hook (frontend_recipe.build_terminal_state_hook)
// CodeAgent#750: once this proof's own assertions pass, nothing on the page may still
// show a loading placeholder. A render path that never reaches its terminal state
// (data, an empty note, an error) is a defect the proof did not ask about. Elements
// under [data-loading-ok] are exempt. Generated by the engine at write time -- edit
// the generator, not this block.
test.afterEach(async ({ page }, testInfo) => {
  if (testInfo.status !== testInfo.expectedStatus || page.isClosed()) return;
  const stuck = await page.evaluate((markers) => Array.from(document.querySelectorAll('body *')).filter((el) => {
    if (el.children.length || el.closest('[data-loading-ok]')) return false;
    const t = (el.textContent || '').trim();
    if (!t || !markers.some((m) => t === m || t.startsWith(m))) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }).map((el) => {
    const p = el.closest('[data-panel]');
    const cls = (el.getAttribute('class') || '').trim().split(/\s+/)[0];
    return (p && p.getAttribute('data-panel')) || (el.tagName.toLowerCase() + (cls ? '.' + cls : ''));
  }), ["Loading data\u2026", "Loading data...", "Loading\u2026", "Loading..."]).catch(() => []);
  test.expect(stuck, `element(s) still showing a loading placeholder after the proof: ${stuck.join(', ')} -- every render path must reach a terminal state (data, an empty note or an error), or declare data-loading-ok`).toEqual([]);
});

// The freeform pass runs several sub-scenarios in one test; give slow CI
// runners room without weakening the assertions.
test.setTimeout(120000);

const DEV_CONFIG = {
  authEnabled: true,
  authMode: 'development',
  issuer: 'http://idp.test/realms/r',
  clientId: 'aierp-app',
  audience: 'aierp-app',
  jwksUrl: 'http://idp.test/realms/r/protocol/openid-connect/certs',
  algorithms: ['RS256'],
};

const PROD_CONFIG = {
  authEnabled: true,
  authMode: 'production',
  issuer: 'http://idp.test/realms/r',
  clientId: 'aierp-app',
  audience: 'aierp-app',
  jwksUrl: 'http://idp.test/realms/r/protocol/openid-connect/certs',
  algorithms: ['RS256'],
};

// The planner pass signs in through the development token card, the
// way the existing Overview specs do.
const PLANNER_ME = {
  authenticated: true,
  user: { sub: 'u1', username: 'planner1', roles: ['admin'], groups: [] },
};

// The viewer pass: a NON-ADMIN (role 'user').
const VIEWER_ME = {
  authenticated: true,
  user: { sub: 'v1', username: 'viewer1', roles: ['user'], groups: [] },
};

// 1534's full acceptance answer: the acceptance read's answer — the
// drift card's "acceptance" block renders beside the new label.
const FULL = {
  accepted: { count: 3, money_at_stake: 700, without_money: 1 },
  dismissed: { count: 2, money_at_stake: 350, without_money: 0 },
  snoozed: { count: 1 },
  rate: 0.6,
  money_rate: 0.6667,
  raise: { accepted: 2, dismissed: 0 },
  lower: { accepted: 1, dismissed: 1 },
};

// 1534's writeback record answer: the drift card's "pushes" block.
const RECORD_READ = {
  reorder_points: { pushed: 5, read: 4, unchanged: 3, rate: 0.75 },
  buffers: { pushed: 3, read: 2, unchanged: 1, rate: 0.5 },
};

// 1534's proposals answers: the stockout and excess cards' blocks.
const FIRST = {
  accepted: 7,
  stop_replenishing: { proposed: 2, chosen: 1 },
  reduce: { proposed: 2, chosen: 1 },
  dispose: { proposed: 1, chosen: 1 },
  total: { proposed: 5, chosen: 3 },
  without_proposal: 2,
};

// A small known summary: accepted 3400 = 1200 + 800 + 1400; applied
// 1000 (29% of accepted); outstanding 2400 = 1200 + 800 + 400. The
// cards' figures come from this answer, never from the lists.
const SUMMARY = {
  drift: {
    accepted: { money: 1200, count: 1 },
    applied: { money: 0, count: 0 },
    outstanding: { money: 1200, count: 1 },
    dismissed: { count: 0 },
  },
  excess: {
    accepted: { money: 800, count: 1 },
    applied: { money: 0, count: 0 },
    outstanding: { money: 800, count: 0 },
    dismissed: { count: 0 },
  },
  stockout: {
    accepted: { money: 1400, count: 1 },
    applied: { money: 1000, count: 1 },
    outstanding: { money: 400, count: 0 },
    dismissed: { count: 0 },
  },
  total: {
    accepted: { money: 3400, count: 3 },
    applied: { money: 1000, count: 1 },
    outstanding: { money: 2400, count: 2 },
    dismissed: { count: 0 },
  },
};

// The Settings answers for the shell: one system setting (read-only)
// and one personal one, the way the existing Overview specs mock it.
const SETTINGS_ENTRIES = [
  {
    key: 'decisions.require_action_choice',
    scope: 'system',
    type: 'boolean',
    allowed: null,
    default: true,
    description: 'Accept on an excess or stockout row stays disabled until an action is chosen.',
    value: true,
    source: 'default',
    editable: false,
    updated_by: null,
    updated_by_name: null,
    updated_at: null,
  },
  {
    key: 'queue.default_filter',
    scope: 'user',
    type: 'enum',
    allowed: ['to_decide', 'decided', 'all'],
    default: 'to_decide',
    description: 'The filter the Work queue opens with.',
    value: 'to_decide',
    source: 'default',
    editable: true,
    updated_by: null,
    updated_by_name: null,
    updated_at: null,
  },
];

// The releases answer: PO003077 late (1 d), PO003068 awaiting
// approval (20 d waiting), PO003070 awaiting approval (3 d waiting).
function releasesPayload() {
  return {
    as_of: '2026-10-22',
    orders: [
      {
        order_nbr: 'PO003077',
        state: 'late',
        month: '2026-10',
        site_code: 'WHOLESALE',
        supplier_code: 'CONGOODT00',
        warehouse_code: 'WHOLESALE',
        vendor_code: 'CONGOODT00',
        released_at: '2026-10-20T06:12:41Z',
        actor: 'jason',
        value: '118240.55',
        status: 'On hold',
        hold: true,
        promised_on: '2026-10-21',
        days_waiting: null,
        days_late: 1,
        lines: [],
      },
      {
        order_nbr: 'PO003068',
        state: 'awaiting_approval',
        month: '2026-10',
        site_code: 'WHOLESALE',
        supplier_code: 'CONGOODT01',
        warehouse_code: 'WHOLESALE',
        vendor_code: 'CONGOODT01',
        released_at: '2026-10-02T09:30:00Z',
        actor: 'jason',
        value: '797380.24',
        status: 'Open',
        hold: false,
        promised_on: '2026-11-05',
        days_waiting: 20,
        days_late: null,
        lines: [],
      },
      {
        order_nbr: 'PO003070',
        state: 'awaiting_approval',
        month: '2026-10',
        site_code: 'WHOLESALE',
        supplier_code: 'CONGOODT02',
        warehouse_code: 'WHOLESALE',
        vendor_code: 'CONGOODT02',
        released_at: '2026-10-19T14:02:11Z',
        actor: 'planner1',
        value: '41250.00',
        status: 'Open',
        hold: false,
        promised_on: '2026-10-30',
        days_waiting: 3,
        days_late: null,
        lines: [],
      },
    ],
    counts: { awaiting_approval: 2, late: 1, partly_received: 0, received: 0 },
  };
}

// The "Value by class" answer: total_annual_value 1000, counts A 2,
// B 1, C 1, unclassified 3.
function abcPayload() {
  return {
    as_of: '2026-10-22',
    total_annual_value: 1000,
    counts: { A: 2, B: 1, C: 1, unclassified: 3 },
    rows: [
      { item_code: 'IT0001', site_code: 'WHOLESALE', annual_value: 500, rank: 1, share_before: 0, abc_class: 'A' },
      { item_code: 'IT0002', site_code: 'WHOLESALE', annual_value: 300, rank: 2, share_before: 0.5, abc_class: 'A' },
      { item_code: 'IT0003', site_code: 'WHOLESALE', annual_value: 100, rank: 3, share_before: 0.8, abc_class: 'B' },
      { item_code: 'IT0004', site_code: 'WHOLESALE', annual_value: 100, rank: 4, share_before: 0.9, abc_class: 'C' },
      { item_code: 'IT0005', site_code: 'WHOLESALE', annual_value: null, rank: null, share_before: null, abc_class: null },
      { item_code: 'IT0006', site_code: 'WHOLESALE', annual_value: null, rank: null, share_before: null, abc_class: null },
      { item_code: 'IT0007', site_code: 'WHOLESALE', annual_value: null, rank: null, share_before: null, abc_class: null },
    ],
  };
}

// The "Exceptions today" answer: counts high 1, medium 3,
// acknowledged 1 and three unacknowledged rows.
function exceptionsPayload() {
  return {
    as_of: '2026-10-30',
    counts: { high: 1, medium: 3, acknowledged: 1 },
    rows: [
      {
        kind: 'stockout_likely',
        severity: 'high',
        key: 'stockout_likely:IT-001/WHOLESALE',
        item_code: 'IT-001',
        site_code: 'WHOLESALE',
        title: 'Stockout likely',
        detail: 'projected stockout 2026-11-04, 82% chance',
        view: '#/queue?kind=stockout',
        detected_on: '2026-10-30',
        acknowledged: false,
        acknowledged_by_name: null,
        acknowledged_at: null,
        snooze_until: null,
      },
      {
        kind: 'expedite_late',
        severity: 'medium',
        key: 'expedite_late:PO00987/2',
        item_code: null,
        site_code: null,
        title: 'Expedite late',
        detail: '40 units, 6 days late',
        view: '#/expedite',
        detected_on: '2026-10-30',
        acknowledged: false,
        acknowledged_by_name: null,
        acknowledged_at: null,
        snooze_until: null,
      },
      {
        kind: 'buffer_critical',
        severity: 'medium',
        key: 'buffer_critical:IT-003/SOUTH',
        item_code: 'IT-003',
        site_code: 'SOUTH',
        title: 'Buffer critical',
        detail: 'on hand 3 (22% of top of red)',
        view: '#/buffers',
        detected_on: '2026-10-30',
        acknowledged: false,
        acknowledged_by_name: null,
        acknowledged_at: null,
        snooze_until: null,
      },
    ],
    sources_failed: [],
  };
}

// The Supply this week card's answer.
function summaryPayload() {
  return {
    as_of: '2026-11-02',
    horizon_days: 7,
    line_count: 18,
    total_value: '344142.85',
    supplier_count: 9,
    transfer_count: 2,
    transfer_value: '5109.21',
    transfers_in_flight: 1,
    transfers_awaiting_release: 0,
  };
}

// The Overview list rows, in the API's shapes: rank i, item SO-/EX-/
// DR-000i, and a decision on every row past the undecided count. The
// money fields descend, so the attention list's top 5 are decided by
// the money order.
function overviewRows(kind, count, undecided) {
  const prefix = kind === 'drift' ? 'DR' : kind === 'excess' ? 'EX' : 'SO';
  const rows = [];
  for (let i = 1; i <= count; i += 1) {
    const row = {
      rank: i,
      item_code: prefix + '-' + String(i).padStart(4, '0'),
      site_code: 'WH-' + ((i % 3) + 1),
      as_of: '2026-10-09',
      notes: [],
      decision: i <= undecided ? null : { action: 'accept', decided_by: 'planner1' },
      decision_lapsed: false,
    };
    if (kind === 'drift') {
      row.category = 'drift';
      row.direction = 'under';
      row.units_gap = -120;
      row.money_at_stake = 9600 - i;
      row.unit_cost = 4.5;
      row.reorder_point = 950;
      row.reorder_point_units = 950;
      row.safety_stock = 210;
      row.arithmetic = 'ROP = d·LT + k·σ_L = 700 + 1.645 × 127.6 = 950.0 → 950 units';
      row.current_reorder_point = 830;
    } else if (kind === 'excess') {
      row.category = 'excess';
      row.dead_reason = null;
      row.on_hand_units = 4200;
      row.unit_cost = 4.5;
      row.last_demand_on = '2026-09-30';
      row.demand_window_units = 900;
      row.months_of_cover = 4.7;
      row.justified_units = 3500;
      row.excess_units = 700;
      row.money_tied_up = 18900 - i;
      row.arithmetic = 'excess = on hand − justified = 4200 − 3500 = 700 units';
    } else {
      row.category = 'likely';
      row.on_hand_units = 250;
      row.open_supply_units = 100;
      row.open_supply_due_by = '2026-10-29';
      row.unit_cost = 4.5;
      row.lead_time_days = 21;
      row.lead_time_demand = 520;
      row.p_stockout = 0.72;
      row.days_of_cover = 2.4;
      row.projected_stockout_on = '2026-11-02';
      row.recommended_reorder_point_units = null;
      row.expected_units_short = 170;
      row.money_at_risk = 765 - i * 0.001;
      row.arithmetic = 'cover = (250 + 100) ÷ 146 per day = 2.4 days < lead time 21';
    }
    rows.push(row);
  }
  return rows;
}

// The demand view's constants, the way issue1461 pins them.
const AS_OF = '2026-10-09';
const CLASSES = ['smooth', 'erratic', 'intermittent', 'lumpy'];
const XSS_CODE = '<img src=x onerror=window.__pwned=1>';

// The realised-accuracy responses the two /api/forecast/accuracy
// requests the view sends with the list load answer with.
const ACCURACY_AHEAD = {
  as_of: AS_OF,
  compared_buckets: 42,
  overall: { forecast_total: 1000, actual_total: 900, wape: 0.11, bias: 0.02 },
  by_horizon: [
    { bucket_index: 1, compared_buckets: 42, forecast_total: 1000, actual_total: 900, wape: 0.11, bias: 0.02 },
  ],
  item_sites: [
    { item_code: 'BIG-0001', site_code: 'WH-2', method: 'ses', compared_buckets: 42, forecast_total: 1000, actual_total: 900, wape: 0.11, bias: 0.02 },
  ],
};
const ACCURACY_ALL = {
  as_of: AS_OF,
  compared_buckets: 504,
  overall: { forecast_total: 12000, actual_total: 10800, wape: 0.11, bias: 0.02 },
  by_horizon: [
    { bucket_index: 1, compared_buckets: 42, forecast_total: 1000, actual_total: 900, wape: 0.11, bias: 0.02 },
    { bucket_index: 2, compared_buckets: 462, forecast_total: 11000, actual_total: 9900, wape: 0.11, bias: 0.02 },
  ],
  item_sites: [
    { item_code: 'BIG-0001', site_code: 'WH-2', method: 'ses', compared_buckets: 504, forecast_total: 12000, actual_total: 10800, wape: 0.11, bias: 0.02 },
  ],
};

function isoDay(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

// count 30-day buckets ending on 2026-10-09, oldest first.
function historyBuckets(count, quantities) {
  const starts = [];
  const ends = [];
  let end = Date.UTC(2026, 9, 9);
  for (let i = 0; i < count; i += 1) {
    ends.unshift(isoDay(end));
    const start = end - 29 * 86400000;
    starts.unshift(isoDay(start));
    end = start - 86400000;
  }
  const buckets = [];
  for (let i = 0; i < count; i += 1) {
    buckets.push({
      bucket_start: starts[i],
      bucket_end: ends[i],
      quantity: quantities[i % quantities.length],
    });
  }
  return buckets;
}

// 12 buckets of 30 days starting the day after 2026-10-09. No
// override covers any bucket: the effective quantity is the
// statistical one and override_days is 0.
function forecastBuckets(quantities) {
  const buckets = [];
  let start = Date.UTC(2026, 9, 10);
  for (let i = 0; i < quantities.length; i += 1) {
    const end = start + 29 * 86400000;
    buckets.push({
      bucket_start: isoDay(start),
      bucket_end: isoDay(end),
      quantity: quantities[i],
      effective_quantity: quantities[i],
      override_days: 0,
    });
    start = end + 86400000;
  }
  return buckets;
}

// One row per item-site, in the order the API returns them: no
// overrides, a backtest on every row, and the demand classes
// cycling, so the chips and the class filter have their counts.
function forecastRows(count) {
  const rows = [];
  for (let i = 1; i <= count; i += 1) {
    rows.push({
      item_code: 'BIG-' + String(i).padStart(4, '0'),
      site_code: 'WH-' + ((i % 3) + 1),
      as_of: AS_OF,
      demand_class: CLASSES[i % 4],
      method: 'ses',
      params: { alpha: 0.3 },
      history_buckets: 12,
      total_forecast: 3000 - (i % 97),
      total_effective_forecast: 3000 - (i % 97),
      overridden_buckets: 0,
      backtest: { mae: 1.5, baseline_mae: 2.0, wape: 0.11, bias: 0.02 },
    });
  }
  return rows;
}

// The list rows carry no buckets; the by-key detail adds them back.
function detailsFor(rows) {
  const details = {};
  rows.forEach((row) => {
    details[row.item_code + '|' + row.site_code] = Object.assign({}, row, {
      history: historyBuckets(12, [5, 6, 5, 6]),
      forecast: forecastBuckets(new Array(12).fill(10)),
      arithmetic:
        'ses: level = 0.3 \u00d7 quantity + 0.7 \u00d7 level; forecast = final level, repeated for each of the 12 buckets',
    });
  });
  return details;
}

// Mock every /api/* call the shell, the Overview and the Demand view
// make — issue1534's handler with its three list answers steered
// through ctrl (recorded in ctrl.listGets with their query) plus the
// Demand view's branches, mocked the way issue1461 does it (the
// forecast list steered through ctrl, the by-key details, the empty
// overrides, the two accuracy answers). Every request's method and
// path lands in ctrl.requests, and the handler reads ctrl on every
// request, so the test can steer the answers and the sign-in
// (ctrl.config / ctrl.me) when the pass switches from the admin
// planner to the non-admin viewer.
async function mockApi(page) {
  const ctrl = {
    config: DEV_CONFIG,
    me: PLANNER_ME,
    driftAnswer: [],
    excessAnswer: [],
    stockoutAnswer: [],
    listGets: [], // { kind, query } of every Overview list read
    driftAcceptanceAnswer: FULL,
    driftAcceptanceStatus: 200,
    recordAnswer: RECORD_READ,
    recordStatus: 200,
    proposalsAnswer: FIRST,
    proposalsStatus: 200,
    excessProposalsAnswer: FIRST,
    excessProposalsStatus: 200,
    abcAnswer: abcPayload(),
    exceptionsAnswer: exceptionsPayload(),
    releasesAnswer: releasesPayload(),
    summaryAnswer: summaryPayload(),
    forecastAnswer: null, // the demand list rows, steered for the viewer pass
    forecastDetails: {},
    forecastGets: [], // the query of every /api/forecast list read
    byKeyGets: [], // { item, site } of every by-key read
    meRequests: [],
    requests: [],
  };
  const appOrigin = { value: null };

  const handler = (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (appOrigin.value === null) {
      appOrigin.value = url.origin;
    }
    ctrl.requests.push({ method: request.method(), path: url.pathname });
    const path = url.pathname;
    if (path === '/api/auth/config') {
      return route.fulfill({ status: 200, json: ctrl.config });
    }
    if (path === '/api/auth/me') {
      ctrl.meRequests.push(request);
      return route.fulfill({ status: 200, json: ctrl.me });
    }
    if (path === '/api/drift/decisions/acceptance') {
      if (ctrl.driftAcceptanceStatus !== 200) {
        return route.fulfill({
          status: ctrl.driftAcceptanceStatus,
          json: { detail: 'Internal Server Error' },
        });
      }
      return route.fulfill({ status: 200, json: ctrl.driftAcceptanceAnswer });
    }
    if (path === '/api/parameter-writeback/record') {
      if (ctrl.recordStatus !== 200) {
        return route.fulfill({
          status: ctrl.recordStatus,
          json: { detail: 'Internal Server Error' },
        });
      }
      return route.fulfill({ status: 200, json: ctrl.recordAnswer });
    }
    if (path === '/api/stockout/proposals') {
      if (ctrl.proposalsStatus !== 200) {
        return route.fulfill({
          status: ctrl.proposalsStatus,
          json: { detail: 'Internal Server Error' },
        });
      }
      return route.fulfill({ status: 200, json: ctrl.proposalsAnswer });
    }
    if (path === '/api/excess/proposals') {
      if (ctrl.excessProposalsStatus !== 200) {
        return route.fulfill({
          status: ctrl.excessProposalsStatus,
          json: { detail: 'Internal Server Error' },
        });
      }
      return route.fulfill({ status: 200, json: ctrl.excessProposalsAnswer });
    }
    if (path === '/api/drift' || path === '/api/excess' || path === '/api/stockout') {
      ctrl.listGets.push({ kind: path.slice('/api/'.length), query: url.search });
      const answer =
        path === '/api/drift'
          ? ctrl.driftAnswer
          : path === '/api/excess'
            ? ctrl.excessAnswer
            : ctrl.stockoutAnswer;
      return route.fulfill({ status: 200, json: answer });
    }
    const decisions = /^\/api\/(drift|excess|stockout)\/decisions$/.exec(path);
    if (decisions) {
      if (request.method() === 'POST') {
        return route.fulfill({ status: 201, json: request.postDataJSON() });
      }
      return route.fulfill({ status: 200, json: [] });
    }
    if (path === '/api/decisions/summary') {
      return route.fulfill({ status: 200, json: SUMMARY });
    }
    if (path === '/api/abc') {
      return route.fulfill({ status: 200, json: ctrl.abcAnswer });
    }
    if (path === '/api/aggregate/releases') {
      return route.fulfill({ status: 200, json: ctrl.releasesAnswer });
    }
    if (path === '/api/exceptions') {
      return route.fulfill({ status: 200, json: ctrl.exceptionsAnswer });
    }
    if (path === '/api/supply-plan/summary') {
      return route.fulfill({ status: 200, json: ctrl.summaryAnswer });
    }
    if (path === '/api/settings') {
      return route.fulfill({ status: 200, json: SETTINGS_ENTRIES });
    }
    // The Demand view, mocked the way issue1461 does it.
    if (path === '/api/forecast') {
      ctrl.forecastGets.push(url.search);
      return route.fulfill({ status: 200, json: ctrl.forecastAnswer });
    }
    if (path === '/api/forecast/by-key') {
      const item = url.searchParams.get('item_code');
      const site = url.searchParams.get('site_code');
      ctrl.byKeyGets.push({ item, site });
      const detail = ctrl.forecastDetails[item + '|' + site];
      if (!detail) {
        return route.fulfill({ status: 404, json: { detail: 'forecast not found' } });
      }
      return route.fulfill({ status: 200, json: detail });
    }
    if (path === '/api/forecast/overrides') {
      return route.fulfill({ status: 200, json: [] });
    }
    if (path === '/api/forecast/accuracy') {
      const isAhead = url.searchParams.get('bucket_index') === '1';
      return route.fulfill({ status: 200, json: isAhead ? ACCURACY_AHEAD : ACCURACY_ALL });
    }
    return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
  };

  await page.route('**/api/**', handler);
  return { ctrl, appOrigin, handler };
}

// Mock every /api/* call the demand view makes, on its own page —
// issue1461's mocks without the detail holding: the forecast list
// answers api.rows, the by-key details answer from api.details, the
// overrides answer empty and the two accuracy answers their
// payloads.
async function mockDemandApi(page, { rows, details }) {
  const api = { rows, details, listGets: [], byKeyGets: [], requests: [] };
  await page.route('**/api/**', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    api.requests.push({ method: request.method(), path: url.pathname });
    if (url.pathname === '/api/auth/config') {
      return route.fulfill({ status: 200, json: DEV_CONFIG });
    }
    if (url.pathname === '/api/auth/me') {
      return route.fulfill({ status: 200, json: PLANNER_ME });
    }
    if (url.pathname === '/api/forecast') {
      api.listGets.push(url.search);
      return route.fulfill({ status: 200, json: api.rows });
    }
    if (url.pathname === '/api/forecast/by-key') {
      const item = url.searchParams.get('item_code');
      const site = url.searchParams.get('site_code');
      api.byKeyGets.push({ item, site });
      const detail = api.details[item + '|' + site];
      if (!detail) {
        return route.fulfill({ status: 404, json: { detail: 'forecast not found' } });
      }
      return route.fulfill({ status: 200, json: detail });
    }
    if (url.pathname === '/api/forecast/overrides') {
      return route.fulfill({ status: 200, json: [] });
    }
    if (url.pathname === '/api/forecast/accuracy') {
      const isAhead = url.searchParams.get('bucket_index') === '1';
      return route.fulfill({ status: 200, json: isAhead ? ACCURACY_AHEAD : ACCURACY_ALL });
    }
    return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
  });
  return api;
}

// Mark the figures stale and take the Overview off and on again: the
// next activation re-requests every region, the lists included.
async function reactivateOverview(page) {
  await page.evaluate(() => {
    window.Overview.invalidate();
  });
  await page.evaluate(() => {
    window.location.hash = '#/queue';
  });
  await expect(page.locator('#view-queue')).toBeVisible();
  await page.evaluate(() => {
    window.location.hash = '#/overview';
  });
  await expect(page.locator('#view-overview')).toBeVisible();
}

// The card's three figures, in order: Accepted, Applied, Dismissed —
// all from /api/decisions/summary, never from the lists.
async function assertCardFigures(card, accepted, applied, dismissed) {
  await expect(card.locator('.ov-figure-value').nth(0)).toHaveText(accepted);
  await expect(card.locator('.ov-figure-value').nth(1)).toHaveText(applied);
  await expect(card.locator('.ov-figure-value').nth(2)).toHaveText(dismissed);
}

// The Overview's three list reads are unchanged: every kind is
// read as a GET with the read's own query. The queue's own excess
// and stockout reads add include_remedy=true on top of it, and no
// list read drifts from those two shapes.
const OVERVIEW_LIST_QUERY = '?limit=1000&include_decided=true';
const QUEUE_REMEDY_QUERY = OVERVIEW_LIST_QUERY + '&include_remedy=true';

function assertListReadsUnchanged(ctrl) {
  expect(ctrl.listGets.length).toBeGreaterThan(0);
  expect(
    ctrl.listGets.every(
      (get) => get.query === OVERVIEW_LIST_QUERY || get.query === QUEUE_REMEDY_QUERY,
    ),
  ).toBe(true);
  expect(
    ctrl.listGets.some((get) => get.kind === 'drift' && get.query === OVERVIEW_LIST_QUERY),
  ).toBe(true);
  expect(
    ctrl.listGets.some((get) => get.kind === 'excess' && get.query === OVERVIEW_LIST_QUERY),
  ).toBe(true);
  expect(
    ctrl.listGets.some(
      (get) => get.kind === 'stockout' && get.query === OVERVIEW_LIST_QUERY,
    ),
  ).toBe(true);
}

test('issue1601 freeform', async ({ page, context }) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  /* ---------------- planner pass: development sign-in ------------------ */
  page.setViewportSize({ width: 1440, height: 900 });
  const { ctrl, appOrigin, handler } = await mockApi(page);

  const driftCard = page.locator('#ov-region-card-drift');
  const excessCard = page.locator('#ov-region-card-excess');
  const stockoutCard = page.locator('#ov-region-card-stockout');

  // FULL: the stockout list holds exactly 1,000 rows of which 600
  // have no decision; the drift and excess lists hold 3 rows each,
  // all undecided.
  ctrl.driftAnswer = overviewRows('drift', 3, 3);
  ctrl.excessAnswer = overviewRows('excess', 3, 3);
  ctrl.stockoutAnswer = overviewRows('stockout', 1000, 600);

  await page.goto('/');

  // Sign in through the development token card as the admin planner,
  // the way the existing Overview specs do. The Overview is the
  // default view, so its regions — the summary, the three lists and
  // the cards' own reads — load the first time the view is active.
  await expect(page.locator('#signin-form')).toBeVisible();
  await page.fill('#token-input', 'dev-token-1601');
  await page.click('#signin-button');
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('planner1');

  // --- OVERVIEW, FULL ---------------------------------------------------
  // The full list's card says "at least"; the not-full cards read
  // exactly as today.
  await expect(stockoutCard.locator('.ov-card-to-decide')).toHaveText(
    'at least 600 to decide',
  );
  await expect(driftCard.locator('.ov-card-to-decide')).toHaveText('3 to decide');
  await expect(excessCard.locator('.ov-card-to-decide')).toHaveText('3 to decide');

  // Every other figure on the cards comes from
  // /api/decisions/summary, as before.
  await assertCardFigures(driftCard, '$1,200', '$0', '0');
  await assertCardFigures(excessCard, '$800', '$0', '0');
  await assertCardFigures(stockoutCard, '$1,400', '$1,000', '0');

  // The cards' blocks still render beside the labels.
  await expect(page.locator('[data-testid="ov-drift-acceptance"]')).toBeVisible();
  await expect(page.locator('[data-testid="ov-drift-pushes"]')).toBeVisible();
  await expect(page.locator('[data-testid="ov-stockout-proposals"]')).toBeVisible();
  await expect(page.locator('[data-testid="ov-excess-proposals"]')).toBeVisible();

  // The value tiles read the summary's total buckets, as before.
  const tiles = page.locator('#ov-region-tiles .ov-tile');
  await expect(tiles.nth(0).locator('.ov-tile-value')).toHaveText('$3,400');
  await expect(tiles.nth(1).locator('.ov-tile-value')).toHaveText('$1,000');
  await expect(tiles.nth(2).locator('.ov-tile-value')).toHaveText('$2,400');

  // The attention list still holds its 5 rows across the 606
  // undecided rows.
  await expect(page.locator('#ov-attention-body .attn-row')).toHaveCount(5);

  // The three list reads are unchanged: GET, the read's own query.
  assertListReadsUnchanged(ctrl);
  expect(ctrl.requests.every((request) => request.method === 'GET')).toBe(true);
  expect(pageErrors).toEqual([]);

  // --- OVERVIEW, NOT FULL -----------------------------------------------
  // The stockout list holding 999 rows of which 600 have no decision:
  // its card reads the count, with no "at least".
  ctrl.stockoutAnswer = overviewRows('stockout', 999, 600);
  await reactivateOverview(page);
  await expect(stockoutCard.locator('.ov-card-to-decide')).toHaveText('600 to decide');
  await expect(driftCard.locator('.ov-card-to-decide')).toHaveText('3 to decide');
  await expect(excessCard.locator('.ov-card-to-decide')).toHaveText('3 to decide');
  await assertCardFigures(driftCard, '$1,200', '$0', '0');
  await assertCardFigures(excessCard, '$800', '$0', '0');
  await assertCardFigures(stockoutCard, '$1,400', '$1,000', '0');
  await expect(page.locator('#ov-attention-body .attn-row')).toHaveCount(5);
  assertListReadsUnchanged(ctrl);
  expect(ctrl.requests.every((request) => request.method === 'GET')).toBe(true);
  expect(pageErrors).toEqual([]);

  /* ---------------- demand pass: full, on its own page ----------------- */
  const pageErrorsB1 = [];
  const rows1000 = forecastRows(1000);
  // The hostile item_code: one of the 1,000 rows — it must reach the
  // page as text only.
  rows1000[499].item_code = XSS_CODE;
  const pageB1 = await context.newPage();
  pageB1.on('pageerror', (error) => pageErrorsB1.push(error.message));
  await pageB1.setViewportSize({ width: 1440, height: 900 });
  const apiB1 = await mockDemandApi(pageB1, {
    rows: rows1000,
    details: detailsFor(rows1000),
  });

  await pageB1.goto('/#/demand');
  await expect(pageB1.locator('#signin-form')).toBeVisible();
  await pageB1.fill('#token-input', 'dev-token-1601');
  await pageB1.click('#signin-button');
  await expect(pageB1.locator('#app')).toBeVisible();
  await expect(pageB1.locator('#user-name')).toHaveText('planner1');

  const rowsB1 = pageB1.locator('[data-testid="demand-row"]');
  await expect(rowsB1).toHaveCount(1000);

  // The list read is unchanged: one GET with the read's own query.
  await expect.poll(() => apiB1.listGets.length).toBe(1);
  expect(apiB1.listGets[0]).toBe('?limit=1000');

  // The cut line: exactly one, the exact text, its class and role,
  // above the rows and the column header inside the list pane.
  const cutB1 = pageB1.locator('[data-testid="demand-list-cut"]');
  await expect(cutB1).toHaveCount(1);
  await expect(cutB1).toHaveText('Showing the first 1,000 item-sites. There may be more.');
  await expect(cutB1).toHaveAttribute('role', 'status');
  await expect(cutB1).toHaveAttribute('class', 'dm-list-cut');
  const placement = await pageB1.evaluate(() => {
    const list = document.querySelector('.d-list');
    const cutEl = document.querySelector('[data-testid="demand-list-cut"]');
    const head = document.querySelector('.d-row-head');
    const firstRow = document.querySelector('[data-testid="demand-row"]');
    if (!list || !cutEl || !head || !firstRow) {
      return null;
    }
    return {
      inList: list.contains(cutEl),
      beforeHead:
        (cutEl.compareDocumentPosition(head) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0,
      beforeFirstRow:
        (firstRow.compareDocumentPosition(cutEl) & Node.DOCUMENT_POSITION_PRECEDING) !== 0,
    };
  });
  expect(placement).toEqual({ inList: true, beforeHead: true, beforeFirstRow: true });

  // The rest of the view renders as before: the first row is chosen,
  // its detail shows, and the admin's controls render for the admin.
  await expect(pageB1.locator('.d-detail-title')).toHaveText('BIG-0001 \u00b7 WH-2');
  expect(apiB1.byKeyGets[0]).toEqual({ item: 'BIG-0001', site: 'WH-2' });
  await expect(pageB1.locator('[data-testid="override-open"]')).toHaveCount(12);
  await expect(pageB1.locator('[data-testid="demand-signal-add"]')).toHaveCount(1);

  // Escaping: the hostile item_code renders as text, its row holds
  // no <img>, and window.__pwned stays undefined.
  const xssRow = pageB1.locator('[data-testid="demand-row"][data-item="' + XSS_CODE + '"]');
  await expect(xssRow).toHaveCount(1);
  await expect(xssRow.locator('.d-item')).toHaveText(XSS_CODE);
  expect(await xssRow.locator('img').count()).toBe(0);
  expect(await pageB1.evaluate(() => window.__pwned)).toBeUndefined();

  // The chips and their counts render as before; a demand-class chip
  // narrows the rows — the line describes what was loaded, so it
  // stays, exactly once.
  await expect(pageB1.locator('.d-chip[data-filter="all"] .d-chip-count')).toHaveText('1000');
  await expect(pageB1.locator('.d-chip[data-filter="erratic"] .d-chip-count')).toHaveText('250');
  await pageB1.click('.d-chip[data-filter="erratic"]');
  await expect(rowsB1).toHaveCount(250);
  await expect(cutB1).toHaveCount(1);
  await expect(cutB1).toHaveText('Showing the first 1,000 item-sites. There may be more.');
  expect(apiB1.listGets.length).toBe(1);
  expect(pageErrorsB1).toEqual([]);
  await pageB1.close();

  /* ---------------- demand pass: not full, on its own page ------------ */
  const pageErrorsB2 = [];
  const rows999 = forecastRows(999);
  const pageB2 = await context.newPage();
  pageB2.on('pageerror', (error) => pageErrorsB2.push(error.message));
  await pageB2.setViewportSize({ width: 1440, height: 900 });
  const apiB2 = await mockDemandApi(pageB2, {
    rows: rows999,
    details: detailsFor(rows999),
  });

  await pageB2.goto('/#/demand');
  await expect(pageB2.locator('#signin-form')).toBeVisible();
  await pageB2.fill('#token-input', 'dev-token-1601');
  await pageB2.click('#signin-button');
  await expect(pageB2.locator('#app')).toBeVisible();
  await expect(pageB2.locator('#user-name')).toHaveText('planner1');

  await expect(pageB2.locator('[data-testid="demand-row"]')).toHaveCount(999);
  await expect(pageB2.locator('[data-testid="demand-list-cut"]')).toHaveCount(0);
  await expect(pageB2.locator('.d-detail-title')).toHaveText('BIG-0001 \u00b7 WH-2');
  expect(apiB2.listGets[0]).toBe('?limit=1000');
  expect(pageErrorsB2).toEqual([]);
  await pageB2.close();

  /* ---------- viewer pass: NON-ADMIN, production boot, full reload ---- */
  // The token arrives in the URL hash the way the identity requirement
  // describes; since the hash is no longer a token delivery path the
  // production boot completes through the PKCE round-trip, so the IdP's
  // auth and token endpoints carry the viewer through it.
  page.setViewportSize({ width: 1440, height: 900 });
  const meCountBeforeViewer = ctrl.meRequests.length;
  await page.evaluate(() => {
    window.sessionStorage.clear();
  });
  await page.unroute('**/api/**');
  ctrl.config = PROD_CONFIG;
  ctrl.me = VIEWER_ME;
  ctrl.driftAnswer = overviewRows('drift', 3, 3);
  ctrl.excessAnswer = overviewRows('excess', 3, 3);
  ctrl.stockoutAnswer = overviewRows('stockout', 1000, 600);
  ctrl.forecastAnswer = rows1000;
  ctrl.forecastDetails = detailsFor(rows1000);
  await page.route('**/api/**', handler);
  await page.route(/protocol\/openid-connect\/token/, (route) => {
    return route.fulfill({
      status: 200,
      json: { access_token: 'pkce-viewer-token', token_type: 'Bearer' },
    });
  });
  await page.route(/protocol\/openid-connect\/auth/, (route) => {
    const state = new URL(route.request().url()).searchParams.get('state') || '';
    const backToApp =
      appOrigin.value + '/?code=fake-pkce-code&state=' + encodeURIComponent(state);
    return route.fulfill({
      status: 200,
      contentType: 'text/html',
      body:
        '<!doctype html><html><body><script>window.location.replace(' +
        JSON.stringify(backToApp) +
        ');</script></body></html>',
    });
  });

  // A full reload through a distinct path carrying the token hash, as
  // the identity requirement calls for.
  await page.goto('/index.html#access_token=viewer-token-1601');

  // The PKCE round-trip completes and the shell renders for the
  // non-admin user, signed in through /api/auth/me ...
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('viewer1');
  expect(page.url()).not.toContain('access_token');
  expect(await page.evaluate(() => window.sessionStorage.getItem('aierp_token'))).toBe(
    'pkce-viewer-token',
  );
  await expect.poll(() => ctrl.meRequests.length).toBeGreaterThan(meCountBeforeViewer);
  const viewerMe = ctrl.meRequests[ctrl.meRequests.length - 1];
  expect(viewerMe.headers()['authorization']).toBe('Bearer pkce-viewer-token');

  // ... and the Overview renders for the viewer: the full stockout
  // list still says "at least", the not-full cards read their counts.
  await expect(page.locator('#ov-region-card-stockout .ov-card-to-decide')).toHaveText(
    'at least 600 to decide',
  );
  await expect(page.locator('#ov-region-card-drift .ov-card-to-decide')).toHaveText(
    '3 to decide',
  );
  await expect(page.locator('#ov-region-card-excess .ov-card-to-decide')).toHaveText(
    '3 to decide',
  );

  // The Demand view renders for the viewer with the cut line up ...
  await page.click('.nav-btn[data-view="demand"]');
  await expect(page).toHaveURL(/#\/demand$/);
  await expect(page.locator('#view-demand')).toBeVisible();
  await expect(page.locator('[data-testid="demand-row"]')).toHaveCount(1000);
  await expect(page.locator('.d-detail-title')).toHaveText('BIG-0001 \u00b7 WH-2');
  await expect(page.locator('[data-testid="demand-list-cut"]')).toHaveCount(1);
  await expect(page.locator('[data-testid="demand-list-cut"]')).toHaveText(
    'Showing the first 1,000 item-sites. There may be more.',
  );
  expect(ctrl.forecastGets.length).toBe(1);
  expect(ctrl.forecastGets[0]).toBe('?limit=1000');

  // ... and the admin-only controls are absent: the Override buttons
  // and the Add signal button do not render at all.
  await expect(page.locator('[data-testid="override-open"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="demand-signal-add"]')).toHaveCount(0);

  // Every request the run sent was a GET, and no page error was
  // raised.
  expect(ctrl.requests.every((request) => request.method === 'GET')).toBe(true);
  expect(pageErrors).toEqual([]);
});
