// Proving spec for AIERP issue #1359 — the Demand view's by-key
// detail read asks for the proposed correction
// (GET /api/forecast/by-key?item_code=…&site_code=…&
// include_correction=true, the parameter appended after site_code —
// every other request the view sends is byte-identical to before:
// the list, the overrides read, the two accuracy reads, the signals
// read, /api/auth/me, the demand signals reads and writes, and the
// override and withdrawal posts) and, while the shown detail answer
// carries its own correction key, the pane gains a "Proposed
// correction" section between "Forecast periods" and "Current
// overrides": the answer's reason line and a table with the head
// cells "Period", "Forecast" and "Proposed", one row per proposed
// period in the answer's order — the period's dates with the en dash
// between spaces, the statistical and the proposed quantity through
// the view's quantity formatter. A usable correction is an object
// not null and not an array, with a non-empty string reason and a
// non-empty array of periods whose period_start and period_end are
// YYYY-MM-DD strings and whose quantity and proposed_quantity are
// finite numbers; a null or unusable value shows the single line
// "No correction proposed.", and an answer without the key (an older
// server) shows no section at all. Every value from the answer is
// escaped — the reason, the dates and the figures reach the page as
// text, never as markup. The section prefills no control: the
// override form still starts at the period's effective or
// statistical quantity, never the proposed one, and a save's or a
// withdrawal's reload of the detail (again with
// include_correction=true) is what refreshes the section; the list
// is not loaded again.
//
// The gate serves the static app with no backend, so every /api/*
// call is mocked with one page.route handler that answers by
// url.pathname the way the issue #984 spec's does and records every
// request's method, path and url.search:
//   * GET /api/forecast answers seven rows, A to G at site 159, each
//     with demand class smooth (so no empirical read is sent),
//     whatever the query;
//   * GET /api/forecast/by-key answers the row's detail: the fields
//     of issue #984's details, with three forecast buckets —
//     2026-11-17 to 2026-12-16, 2026-12-17 to 2027-01-15 and
//     2027-01-16 to 2027-02-14 — with quantities 120, 90 and 45.9,
//     the second with a signal of 60 and an effective quantity of
//     150. Only when the query's include_correction is true does the
//     detail carry the item-site's correction below, and F's detail
//     never carries the key: A, usable — the reason "Forecast over
//     actual by 80% across 6 periods, so each period without an
//     override or a demand signal is proposed at 55.6% of its
//     forecast." with the two periods 2026-11-17 to 2026-12-16 at
//     120 proposed 66.67 and 2027-01-16 to 2027-02-14 at 45.9
//     proposed 25.5; B, null; C, usable — the reason "<img src=x
//     onerror=window.__pwned=1> Forecast under actual by 70% across
//     4 periods" followed by a space and a run of 150 letters with
//     no space, one period 2026-11-17 to 2026-12-16 at 1234567.891
//     proposed 4115226.3; D, A's with its second period's
//     proposed_quantity the string "25.5"; E, an empty array; F, no
//     correction key at all; G, A's with its first period's
//     period_start the string "2026/11/17";
//   * GET /api/forecast/overrides answers the stored overrides, none
//     at first;
//   * POST /api/forecast/overrides records its body and answers 201
//     with the stored row the way issue #353's mock does. From then
//     on A's detail answers its first bucket overridden (override
//     days 30, effective quantity 70) and A's correction with its
//     third period only;
//   * the accuracy and the signals reads answer as issue #984's do.
//
// The pass proves, in order, signed in as the ADMIN through the
// development token card at 1440x900 on #/demand:
//   * the requests: one list request, whose url.search is exactly
//     ?limit=1000, as today; A's detail request (the first row is
//     chosen), with ?item_code=A&site_code=159&include_correction=
//     true; A's overrides request with ?item_code=A&site_code=159&
//     limit=200; the two accuracy requests with ?bucket_index=1&
//     limit=1000 and ?limit=1000; and the signals request with an
//     empty search, as today;
//   * A: the section is headed "Proposed correction", sits directly
//     after the "Forecast periods" section and before "Current
//     overrides", and its reason line and its two rows read A's
//     reason, "2026-11-17 – 2026-12-16", "120", "66.67" and
//     "2027-01-16 – 2027-02-14", "45.9", "25.5";
//   * B: one detail request with ?item_code=B&site_code=159&
//     include_correction=true, and the section holds exactly one
//     line, "No correction proposed.", no reason line or table;
//   * C: the reason line reads as text, "<img …>" included — no img
//     element inside the section and window.__pwned undefined — and
//     the row reads "2026-11-17 – 2026-12-16", "1,234,567.89" and
//     "4,115,226.3";
//   * D, E and G each read "No correction proposed.";
//   * F: no [data-testid="demand-correction"], and the pane's h3
//     headings are A's without "Proposed correction";
//   * nothing prefilled: on A, pressing the first period's Override
//     opens the form with the quantity field reading "120", not
//     "66.67", and no POST has been sent;
//   * the save: with the quantity 70 and the reason "Promotion
//     ended", Save sends exactly one POST /api/forecast/overrides,
//     its body parsing to exactly {"item_code": "A", "site_code":
//     "159", "period_start": "2026-11-17", "quantity": 70, "reason":
//     "Promotion ended"}; A's detail is then requested again with
//     ?item_code=A&site_code=159&include_correction=true, the list
//     is not requested again, and A's section lists one row —
//     "2027-01-16 – 2027-02-14", "45.9" and "25.5";
//   * at 390x844 with A selected, the section's left and right
//     edges lie within the detail pane's (.d-detail), to a pixel,
//     and document.documentElement.scrollWidth is at most 390 — the
//     same with C selected, whose long unbroken reason wraps;
//   * nothing else: no PUT or DELETE is sent, the save is the only
//     POST, no empirical read went out, and every /api/forecast
//     request is the list, a detail, an overrides read, the save,
//     one of the two accuracy reads or the signals read.
//
// The pass ends as a NON-ADMIN (role 'user') after a full reload:
// the token arrives in the URL hash as #access_token=...,
// /api/auth/config and /api/auth/me are mocked for that user, the
// IdP's auth and token endpoints carry the PKCE round-trip, and
// opening #/demand sends the list request with ?limit=1000 and A's
// detail request with ?item_code=A&site_code=159&include_correction=
// true — A's section reads as the admin saw it after the save (the
// reason and the one row) and the pane has no Override button while
// the page renders on. No page error in either pass.
//
// The test is TOP-LEVEL and its title is the full proof name
// ("issue1359 freeform"): the merge gate builds its junit nodeids
// from the Playwright test title, and a describe() block would join
// the titles with a "›" the contract nodeid does not carry, so the
// proof would read as "not collected".

const { test, expect } = require('@playwright/test');

// CodeAgent terminal-state hook (frontend_recipe.build_terminal_state_hook)
// CodeAgent#751: once this proof's own assertions pass, nothing on the page may still
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
test.setTimeout(90000);

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

const ADMIN_ME = {
  authenticated: true,
  user: { sub: 'u-admin', username: 'planner1', roles: ['admin'], groups: [] },
};

// The viewer: a signed-in non-admin user (role 'user'), the screen's
// audience for the read-only pass.
const VIEWER_ME = {
  authenticated: true,
  user: { sub: 'v1', username: 'viewer1', roles: ['user'], groups: [] },
};

const AS_OF = '2026-11-16';
const SITE = '159';
const EN_DASH = '\u2013';

// The seven rows' forecast buckets: 2026-11-17 to 2026-12-16 at 120,
// 2026-12-17 to 2027-01-15 at 90 — a signal of 60 and an effective
// quantity of 150 — and 2027-01-16 to 2027-02-14 at 45.9.
const BASE_BUCKETS = [
  {
    bucket_start: '2026-11-17',
    bucket_end: '2026-12-16',
    quantity: 120,
    effective_quantity: 120,
    override_days: 0,
    signal_quantity: 0,
  },
  {
    bucket_start: '2026-12-17',
    bucket_end: '2027-01-15',
    quantity: 90,
    effective_quantity: 150,
    override_days: 0,
    signal_quantity: 60,
  },
  {
    bucket_start: '2027-01-16',
    bucket_end: '2027-02-14',
    quantity: 45.9,
    effective_quantity: 45.9,
    override_days: 0,
    signal_quantity: 0,
  },
];

const HISTORY = [
  { bucket_start: '2026-10-18', bucket_end: '2026-11-16', quantity: 110 },
  { bucket_start: '2026-09-18', bucket_end: '2026-10-17', quantity: 125 },
  { bucket_start: '2026-08-19', bucket_end: '2026-09-17', quantity: 98 },
  { bucket_start: '2026-07-20', bucket_end: '2026-08-18', quantity: 130 },
];

// The by-key detail of one row: the fields of issue #984's details,
// with the three forecast buckets. The list rows carry no history.
function detailFor(item) {
  return {
    item_code: item,
    site_code: SITE,
    as_of: AS_OF,
    demand_class: 'smooth',
    method: 'ses',
    params: { alpha: 0.3 },
    history_buckets: 18,
    total_forecast: 255.9,
    total_effective_forecast: 315.9,
    overridden_buckets: 0,
    forecast: JSON.parse(JSON.stringify(BASE_BUCKETS)),
    backtest: { mae: 3.25, baseline_mae: 4.5, wape: 0.25, bias: -0.1 },
    arithmetic: 'ses: forecast = final level, repeated for each of the 3 buckets (255.9 units)',
    history: JSON.parse(JSON.stringify(HISTORY)),
  };
}

const ITEMS = ['A', 'B', 'C', 'D', 'E', 'F', 'G'];
const BASE_DETAILS = {};
ITEMS.forEach((item) => {
  BASE_DETAILS[item] = detailFor(item);
});
const ROWS = ITEMS.map((item) => {
  const { history: _history, ...rest } = BASE_DETAILS[item];
  return rest;
});

// The corrections, per the acceptance criteria. A, usable: the reason
// and the two periods. B: null. C, usable: the hostile reason —
// markup followed by a space and a run of 150 letters with no space —
// and one big period. D: A's with its second period's
// proposed_quantity the string "25.5" (unusable). E: an empty array
// (unusable). F: no correction key at all. G: A's with its first
// period's period_start the string "2026/11/17" (not a YYYY-MM-DD
// string, unusable).
const A_REASON =
  'Forecast over actual by 80% across 6 periods, so each period without an override or a demand signal is proposed at 55.6% of its forecast.';
const A_CORRECTION = {
  reason: A_REASON,
  periods: [
    { period_start: '2026-11-17', period_end: '2026-12-16', quantity: 120, proposed_quantity: 66.67 },
    { period_start: '2027-01-16', period_end: '2027-02-14', quantity: 45.9, proposed_quantity: 25.5 },
  ],
};
const C_REASON =
  '<img src=x onerror=window.__pwned=1> Forecast under actual by 70% across 4 periods ' +
  'a'.repeat(150);
const C_CORRECTION = {
  reason: C_REASON,
  periods: [
    { period_start: '2026-11-17', period_end: '2026-12-16', quantity: 1234567.891, proposed_quantity: 4115226.3 },
  ],
};
const D_CORRECTION = JSON.parse(JSON.stringify(A_CORRECTION));
D_CORRECTION.periods[1].proposed_quantity = '25.5';
const E_CORRECTION = [];
const G_CORRECTION = JSON.parse(JSON.stringify(A_CORRECTION));
// G's first period's period_start is "2026/11/17" — not a
// YYYY-MM-DD string, so the correction is unusable.
G_CORRECTION.periods[0].period_start = '2026/11/17';
const CORRECTIONS = {
  A: A_CORRECTION,
  B: null,
  C: C_CORRECTION,
  D: D_CORRECTION,
  E: E_CORRECTION,
  G: G_CORRECTION,
};

// A's detail once its first bucket is overridden: override days 30,
// effective quantity 70 — and A's correction with its third period
// only, the one period left without an override day or a signal.
const A_DETAIL_AFTER_SAVE = detailFor('A');
A_DETAIL_AFTER_SAVE.forecast[0] = {
  bucket_start: '2026-11-17',
  bucket_end: '2026-12-16',
  quantity: 120,
  effective_quantity: 70,
  override_days: 30,
  signal_quantity: 0,
};
A_DETAIL_AFTER_SAVE.overridden_buckets = 1;
const A_CORRECTION_AFTER_SAVE = {
  reason: A_REASON,
  periods: [
    { period_start: '2027-01-16', period_end: '2027-02-14', quantity: 45.9, proposed_quantity: 25.5 },
  ],
};

// The one-period-ahead accuracy response (bucket_index=1): issue
// #984's answer.
const ACCURACY_AHEAD = {
  as_of: AS_OF,
  compared_buckets: 976,
  overall: { forecast_total: 5000, actual_total: 4600, wape: 0.27, bias: -0.04 },
  by_horizon: [
    { bucket_index: 1, compared_buckets: 976, forecast_total: 5000, actual_total: 4600, wape: 0.27, bias: -0.04 },
  ],
  item_sites: [
    { item_code: 'B', site_code: 'S1', method: 'damped_trend', compared_buckets: 876, forecast_total: 1560.125, actual_total: 1480.5, wape: 0.18, bias: 0.02, effective_total: 1234.5, effective_wape: 0.18, effective_bias: 0.01, overridden_buckets: 2, value_added: 0.03 },
    { item_code: 'A', site_code: 'S1', method: 'ses', compared_buckets: 500, forecast_total: 240, actual_total: 215.5, wape: null, bias: null, effective_total: 240, effective_wape: null, effective_bias: null, overridden_buckets: 0, value_added: null },
  ],
};

// The all-horizons accuracy response (no bucket_index): issue
// #984's answer — the overall covers three overridden buckets, 4.2
// points better than the statistical forecast.
const ACCURACY_ALL = {
  as_of: AS_OF,
  compared_buckets: 6051,
  overall: { forecast_total: 30000, actual_total: 28000, wape: 0.42, bias: 0.06, effective_total: 29100, effective_wape: 0.378, effective_bias: 0.05, overridden_buckets: 3, value_added: 0.042 },
  by_horizon: [
    { bucket_index: 1, compared_buckets: 976, forecast_total: 5000, actual_total: 4600, wape: 0.27, bias: -0.04, effective_total: 4800, effective_wape: 0.25, effective_bias: -0.02, overridden_buckets: 1, value_added: 0.02 },
    { bucket_index: 2, compared_buckets: 5075, forecast_total: 25000, actual_total: 23400, wape: 0.42, bias: 0.06, effective_total: 24300, effective_wape: 0.39, effective_bias: 0.04, overridden_buckets: 2, value_added: 0.03 },
  ],
  item_sites: [
    { item_code: 'B', site_code: 'S1', method: 'damped_trend', compared_buckets: 5256, forecast_total: 93607.5, actual_total: 88830, wape: 0.18, bias: 0.02, effective_total: 90000, effective_wape: 0.16, effective_bias: 0.01, overridden_buckets: 2, value_added: 0.02 },
    { item_code: 'A', site_code: 'S1', method: 'ses', compared_buckets: 3000, forecast_total: 14400, actual_total: 12930, wape: null, bias: null, effective_total: 14400, effective_wape: null, effective_bias: null, overridden_buckets: 1, value_added: null },
  ],
};

// The GET /api/forecast/accuracy/signals answer: issue #984's —
// 1,200 compared buckets, 20.0% error before the signals, 4.0%
// after, 16.0 points better.
const SIGNALS_BETTER = {
  as_of: AS_OF,
  compared_buckets: 1200,
  actual_total: 20000,
  before_total: 19000,
  after_total: 20500,
  signal_total: 1500,
  before_wape: 0.2,
  after_wape: 0.04,
  value_added: 0.16,
  item_sites: [
    { item_code: 'B', site_code: 'S1', compared_buckets: 300, actual_total: 5000, before_total: 4900, after_total: 5200, signal_total: 300, before_wape: 0.18, after_wape: 0.05, value_added: 0.13 },
    { item_code: 'A', site_code: 'S1', compared_buckets: 150, actual_total: 2000, before_total: 1950, after_total: 2100, signal_total: 150, before_wape: 0.22, after_wape: 0.06, value_added: 0.16 },
  ],
};

// Mock every /api/* call the app makes: one handler that answers by
// url.pathname the way the issue #984 spec's does and records every
// request's method, path and url.search. The stored overrides are
// kept per item-site: the save POST adds to them, and the detail and
// the overrides read answer from them (the way issue #353's mock
// does). Once A's save has landed, A's detail answers its first
// bucket overridden and A's correction with its third period only.
async function mockDemandApi(page) {
  const api = {
    requests: [],
    listGets: 0,
    byKeyGets: [],
    overrideGets: 0,
    accuracyGets: 0,
    signalsGets: 0,
    meRequests: [],
    savePosts: [],
    stored: {},
    nextId: 0,
    saved: false,
    config: DEV_CONFIG,
    me: ADMIN_ME,
  };
  const appOrigin = { value: null };
  api.appOrigin = appOrigin;

  const handler = (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (appOrigin.value === null) {
      appOrigin.value = url.origin;
    }
    const path = url.pathname;
    const method = request.method();
    api.requests.push({ method, path, search: url.search });
    if (path === '/api/auth/config') {
      return route.fulfill({ status: 200, json: api.config });
    }
    if (path === '/api/auth/me') {
      api.meRequests.push(request);
      return route.fulfill({ status: 200, json: api.me });
    }
    if (path === '/api/forecast') {
      api.listGets += 1;
      return route.fulfill({ status: 200, json: ROWS });
    }
    if (path === '/api/forecast/by-key') {
      const item = url.searchParams.get('item_code');
      const site = url.searchParams.get('site_code');
      api.byKeyGets.push({ item, site, search: url.search });
      const base = BASE_DETAILS[item];
      if (!base || site !== SITE) {
        return route.fulfill({ status: 404, json: { detail: 'forecast not found' } });
      }
      const saved = api.saved && item === 'A';
      const detail = JSON.parse(JSON.stringify(saved ? A_DETAIL_AFTER_SAVE : base));
      // Only a detail asked for with include_correction=true carries
      // the item-site's correction — and F's never does.
      if (url.searchParams.get('include_correction') === 'true' && item !== 'F') {
        detail.correction = JSON.parse(
          JSON.stringify(saved ? A_CORRECTION_AFTER_SAVE : CORRECTIONS[item]),
        );
      }
      return route.fulfill({ status: 200, json: detail });
    }
    if (path === '/api/forecast/overrides' && method === 'GET') {
      api.overrideGets += 1;
      const key = url.searchParams.get('item_code') + '|' + url.searchParams.get('site_code');
      return route.fulfill({ status: 200, json: api.stored[key] || [] });
    }
    if (path === '/api/forecast/overrides' && method === 'POST') {
      const body = JSON.parse(request.postData() || '{}');
      api.savePosts.push(body);
      const key = body.item_code + '|' + body.site_code;
      const base = BASE_DETAILS[body.item_code];
      const bucket = base && base.forecast.find((b) => b.bucket_start === body.period_start);
      const row = {
        id: ++api.nextId,
        item_code: body.item_code,
        site_code: body.site_code,
        period_start: body.period_start,
        period_end: bucket ? bucket.bucket_end : body.period_start,
        kind: 'override',
        quantity: body.quantity,
        reason: body.reason,
        created_by: ADMIN_ME.user.sub,
        created_by_name: ADMIN_ME.user.username,
        created_at: AS_OF + 'T09:00:00Z',
        applies: true,
      };
      (api.stored[key] = api.stored[key] || []).push(row);
      api.saved = true;
      return route.fulfill({ status: 201, json: row });
    }
    if (path === '/api/forecast/accuracy') {
      api.accuracyGets += 1;
      const isAhead = url.searchParams.get('bucket_index') === '1';
      return route.fulfill({ status: 200, json: isAhead ? ACCURACY_AHEAD : ACCURACY_ALL });
    }
    if (path === '/api/forecast/accuracy/signals') {
      api.signalsGets += 1;
      return route.fulfill({ status: 200, json: SIGNALS_BETTER });
    }
    return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
  };

  await page.route('**/api/**', handler);
  return api;
}

async function signIn(page, username) {
  await expect(page.locator('#signin-form')).toBeVisible();
  await page.fill('#token-input', 'dev-admin-token');
  await page.click('#signin-button');
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText(username);
}

// The uncaught exceptions of the page so far.
function collectPageErrors(page) {
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  return errors;
}

test('issue1359 freeform', async ({ page }) => {
  // --- the admin pass at 1440x900, through the dev token card ------
  page.setViewportSize({ width: 1440, height: 900 });
  const api = await mockDemandApi(page);
  const pageErrors = collectPageErrors(page);

  await page.goto('/#/demand');
  await signIn(page, 'planner1');

  const rows = page.locator('[data-testid="demand-row"]');
  await expect(rows).toHaveCount(7);
  // The first row is chosen when the view opens; its detail loads.
  await expect(page.locator('.d-detail-title')).toHaveText('A \u00b7 159');

  // The requests: one list request, byte-identical to today's query;
  // A's detail with the parameter appended after site_code; A's
  // overrides read; the two accuracy reads and the signals read, as
  // today.
  await expect.poll(() => api.accuracyGets).toBe(2);
  await expect.poll(() => api.signalsGets).toBe(1);
  expect(api.listGets).toBe(1);
  const listRequests = api.requests.filter(
    (r) => r.method === 'GET' && r.path === '/api/forecast',
  );
  expect(listRequests).toEqual([
    { method: 'GET', path: '/api/forecast', search: '?limit=1000' },
  ]);
  expect(api.byKeyGets).toEqual([
    { item: 'A', site: '159', search: '?item_code=A&site_code=159&include_correction=true' },
  ]);
  const overrideReads = api.requests.filter(
    (r) => r.method === 'GET' && r.path === '/api/forecast/overrides',
  );
  expect(overrideReads).toEqual([
    { method: 'GET', path: '/api/forecast/overrides', search: '?item_code=A&site_code=159&limit=200' },
  ]);
  const accuracyRequests = api.requests.filter(
    (r) => r.method === 'GET' && r.path === '/api/forecast/accuracy',
  );
  expect(accuracyRequests.map((r) => r.search).sort()).toEqual([
    '?bucket_index=1&limit=1000',
    '?limit=1000',
  ]);
  const signalsRequests = api.requests.filter(
    (r) => r.method === 'GET' && r.path === '/api/forecast/accuracy/signals',
  );
  expect(signalsRequests).toEqual([
    { method: 'GET', path: '/api/forecast/accuracy/signals', search: '' },
  ]);

  // A: the section — its heading, its place, its reason and its rows.
  const correction = page.locator('[data-testid="demand-correction"]');
  await expect(correction).toBeVisible();
  expect(await correction.getAttribute('aria-label')).toBe('Proposed correction');
  await expect(correction.locator('h3')).toHaveText('Proposed correction');
  const neighbours = await correction.evaluate((el) => ({
    prev: el.previousElementSibling ? el.previousElementSibling.getAttribute('aria-label') : null,
    next: el.nextElementSibling ? el.nextElementSibling.getAttribute('aria-label') : null,
  }));
  expect(neighbours.prev).toBe('Forecast periods');
  expect(neighbours.next).toBe('Current overrides');
  const aHeadings = await page.evaluate(() =>
    Array.from(document.querySelectorAll('.d-detail h3')).map((el) => el.textContent),
  );
  expect(aHeadings).toContain('Proposed correction');

  await expect(page.locator('[data-testid="demand-correction-reason"]')).toHaveText(A_REASON);
  const table = page.locator('[data-testid="demand-correction-periods"]');
  await expect(table).toBeVisible();
  const headCells = table.locator('thead th');
  await expect(headCells).toHaveCount(3);
  await expect(headCells.nth(0)).toHaveText('Period');
  await expect(headCells.nth(1)).toHaveText('Forecast');
  await expect(headCells.nth(2)).toHaveText('Proposed');
  const periodRows = page.locator('[data-testid="demand-correction-period"]');
  await expect(periodRows).toHaveCount(2);
  expect(await periodRows.nth(0).getAttribute('data-period-start')).toBe('2026-11-17');
  expect(await periodRows.nth(1).getAttribute('data-period-start')).toBe('2027-01-16');
  await expect(periodRows.nth(0).locator('td').nth(0)).toHaveText(
    '2026-11-17 ' + EN_DASH + ' 2026-12-16',
  );
  await expect(periodRows.nth(0).locator('td').nth(1)).toHaveText('120');
  await expect(periodRows.nth(0).locator('td').nth(2)).toHaveText('66.67');
  await expect(periodRows.nth(1).locator('td').nth(0)).toHaveText(
    '2027-01-16 ' + EN_DASH + ' 2027-02-14',
  );
  await expect(periodRows.nth(1).locator('td').nth(1)).toHaveText('45.9');
  await expect(periodRows.nth(1).locator('td').nth(2)).toHaveText('25.5');
  expect(pageErrors).toEqual([]);

  // B: one detail request with the parameter; the section holds
  // exactly one line, no reason line or table.
  const byKeyCountBeforeB = api.byKeyGets.length;
  await page.click('[data-testid="demand-row"][data-item="B"]');
  await expect(page.locator('.d-detail-title')).toHaveText('B \u00b7 159');
  expect(api.byKeyGets.length).toBe(byKeyCountBeforeB + 1);
  expect(api.byKeyGets[api.byKeyGets.length - 1].search).toBe(
    '?item_code=B&site_code=159&include_correction=true',
  );
  await expect(page.locator('[data-testid="demand-correction"]')).toBeVisible();
  await expect(page.locator('[data-testid="demand-correction-none"]')).toHaveText(
    'No correction proposed.',
  );
  expect(await page.locator('[data-testid="demand-correction-reason"]').count()).toBe(0);
  expect(await page.locator('[data-testid="demand-correction-periods"]').count()).toBe(0);
  expect(
    await page
      .locator('[data-testid="demand-correction"]')
      .evaluate((el) => el.querySelectorAll('p, table').length),
  ).toBe(1);

  // C: the reason reads as text — the markup included — with no img
  // element in the section and no __pwned; the row's figures through
  // the quantity formatter.
  await page.click('[data-testid="demand-row"][data-item="C"]');
  await expect(page.locator('.d-detail-title')).toHaveText('C \u00b7 159');
  await expect(page.locator('[data-testid="demand-correction-reason"]')).toHaveText(C_REASON);
  expect(await page.locator('[data-testid="demand-correction"] img').count()).toBe(0);
  expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
  const cRows = page.locator('[data-testid="demand-correction-period"]');
  await expect(cRows).toHaveCount(1);
  await expect(cRows.locator('td').nth(0)).toHaveText('2026-11-17 ' + EN_DASH + ' 2026-12-16');
  await expect(cRows.locator('td').nth(1)).toHaveText('1,234,567.89');
  await expect(cRows.locator('td').nth(2)).toHaveText('4,115,226.3');
  expect(pageErrors).toEqual([]);

  // D, E and G: each reads "No correction proposed.".
  for (const item of ['D', 'E', 'G']) {
    await page.click('[data-testid="demand-row"][data-item="' + item + '"]');
    await expect(page.locator('.d-detail-title')).toHaveText(item + ' \u00b7 159');
    await expect(page.locator('[data-testid="demand-correction-none"]')).toHaveText(
      'No correction proposed.',
    );
    expect(await page.locator('[data-testid="demand-correction-reason"]').count()).toBe(0);
    expect(await page.locator('[data-testid="demand-correction-periods"]').count()).toBe(0);
  }

  // F: no section at all; the pane's headings are A's without
  // "Proposed correction".
  await page.click('[data-testid="demand-row"][data-item="F"]');
  await expect(page.locator('.d-detail-title')).toHaveText('F \u00b7 159');
  expect(await page.locator('[data-testid="demand-correction"]').count()).toBe(0);
  const fHeadings = await page.evaluate(() =>
    Array.from(document.querySelectorAll('.d-detail h3')).map((el) => el.textContent),
  );
  expect(fHeadings).toEqual(aHeadings.filter((h) => h !== 'Proposed correction'));

  // Nothing prefilled: on A, pressing the first period's Override
  // opens the form at the period's quantity — 120, not the proposed
  // 66.67 — and sends nothing.
  await page.click('[data-testid="demand-row"][data-item="A"]');
  await expect(page.locator('.d-detail-title')).toHaveText('A \u00b7 159');
  await expect(page.locator('[data-testid="demand-correction-reason"]')).toHaveText(A_REASON);
  await expect(page.locator('[data-testid="demand-correction-period"]')).toHaveCount(2);
  await page.click('[data-testid="override-open"]');
  await expect(page.locator('[data-testid="override-quantity"]')).toHaveValue('120');
  expect(api.savePosts).toEqual([]);
  expect(api.requests.filter((r) => r.method === 'POST')).toEqual([]);

  // The save: exactly one POST, the exact body; A's detail is then
  // requested again with the parameter, the list is not requested
  // again, and A's section lists the one remaining period.
  const byKeyCountBeforeSave = api.byKeyGets.length;
  await page.fill('[data-testid="override-quantity"]', '70');
  await page.fill('[data-testid="override-reason"]', 'Promotion ended');
  await page.click('[data-testid="override-save"]');
  await expect.poll(() => api.savePosts.length).toBe(1);
  expect(api.savePosts[0]).toEqual({
    item_code: 'A',
    site_code: '159',
    period_start: '2026-11-17',
    quantity: 70,
    reason: 'Promotion ended',
  });
  await expect.poll(() => api.byKeyGets.length).toBe(byKeyCountBeforeSave + 1);
  expect(api.byKeyGets[api.byKeyGets.length - 1]).toEqual({
    item: 'A',
    site: '159',
    search: '?item_code=A&site_code=159&include_correction=true',
  });
  expect(api.listGets).toBe(1);
  await expect(page.locator('[data-testid="demand-correction-reason"]')).toHaveText(A_REASON);
  const savedRows = page.locator('[data-testid="demand-correction-period"]');
  await expect(savedRows).toHaveCount(1);
  await expect(savedRows.locator('td').nth(0)).toHaveText(
    '2027-01-16 ' + EN_DASH + ' 2027-02-14',
  );
  await expect(savedRows.locator('td').nth(1)).toHaveText('45.9');
  await expect(savedRows.locator('td').nth(2)).toHaveText('25.5');
  expect(pageErrors).toEqual([]);

  // A small screen: with A selected, the section's edges lie within
  // the detail pane's, to a pixel, and the page does not scroll
  // sideways — the same with C selected, whose long unbroken reason
  // wraps.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('[data-testid="demand-correction"]')).toBeVisible();
  const withinPane = async () => {
    const pane = await page.locator('.d-detail').boundingBox();
    const section = await page.locator('[data-testid="demand-correction"]').boundingBox();
    expect(section.x).toBeGreaterThanOrEqual(pane.x);
    expect(section.x + section.width).toBeLessThanOrEqual(pane.x + pane.width);
  };
  await withinPane();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.click('[data-testid="demand-row"][data-item="C"]');
  await expect(page.locator('.d-detail-title')).toHaveText('C \u00b7 159');
  await expect(page.locator('[data-testid="demand-correction"]')).toBeVisible();
  await withinPane();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  expect(pageErrors).toEqual([]);

  // --- the viewer pass: NON-ADMIN, production boot, full reload ----
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.evaluate(() => {
    window.sessionStorage.clear();
  });
  const meCountBeforeViewer = api.meRequests.length;
  api.config = PROD_CONFIG;
  api.me = VIEWER_ME;
  await page.route(/protocol\/openid-connect\/token/, (route) => {
    return route.fulfill({
      status: 200,
      json: { access_token: 'pkce-viewer-token', token_type: 'Bearer' },
    });
  });
  await page.route(/protocol\/openid-connect\/auth/, (route) => {
    const state = new URL(route.request().url()).searchParams.get('state') || '';
    const backToApp =
      api.appOrigin.value + '/?code=fake-pkce-code&state=' + encodeURIComponent(state);
    return route.fulfill({
      status: 200,
      contentType: 'text/html',
      body:
        '<!doctype html><html><body><script>window.location.replace(' +
        JSON.stringify(backToApp) +
        ');</script></body></html>',
    });
  });

  // A full reload through a distinct path carrying the token hash.
  await page.goto('/index.html#access_token=viewer-token-abc');

  // The PKCE round-trip completes and the shell renders for the
  // non-admin user, signed in through /api/auth/me.
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('viewer1');
  expect(page.url()).not.toContain('access_token');
  expect(await page.evaluate(() => window.sessionStorage.getItem('aierp_token'))).toBe(
    'pkce-viewer-token',
  );
  await expect.poll(() => api.meRequests.length).toBeGreaterThan(meCountBeforeViewer);
  expect(api.meRequests[api.meRequests.length - 1].headers()['authorization']).toBe(
    'Bearer pkce-viewer-token',
  );

  // The Demand view renders for the viewer: the list request with
  // today's query and A's detail request with the parameter.
  await page.click('.nav-btn[data-view="demand"]');
  expect(page.url()).toContain('#/demand');
  await expect(page.locator('#view-demand')).toBeVisible();
  await expect(page.locator('[data-testid="demand-row"]')).toHaveCount(7);
  await expect(page.locator('.d-detail-title')).toHaveText('A \u00b7 159');
  const listRequestsNow = api.requests.filter(
    (r) => r.method === 'GET' && r.path === '/api/forecast',
  );
  expect(listRequestsNow[listRequestsNow.length - 1].search).toBe('?limit=1000');
  expect(api.byKeyGets[api.byKeyGets.length - 1]).toEqual({
    item: 'A',
    site: '159',
    search: '?item_code=A&site_code=159&include_correction=true',
  });

  // A's section reads as the admin saw it after the save: the reason
  // and the one row — and the pane has no Override button.
  await expect(page.locator('[data-testid="demand-correction-reason"]')).toHaveText(A_REASON);
  const viewerRows = page.locator('[data-testid="demand-correction-period"]');
  await expect(viewerRows).toHaveCount(1);
  await expect(viewerRows.locator('td').nth(0)).toHaveText(
    '2027-01-16 ' + EN_DASH + ' 2027-02-14',
  );
  await expect(viewerRows.locator('td').nth(1)).toHaveText('45.9');
  await expect(viewerRows.locator('td').nth(2)).toHaveText('25.5');
  expect(await page.locator('[data-testid="override-open"]').count()).toBe(0);
  expect(pageErrors).toEqual([]);

  // Nothing else: no PUT or DELETE anywhere, the save the only POST,
  // no empirical read, and every /api/forecast request one of the
  // list, a detail, an overrides read, the save, one of the two
  // accuracy reads or the signals read.
  expect(api.requests.filter((r) => r.method === 'PUT' || r.method === 'DELETE')).toEqual([]);
  expect(api.requests.filter((r) => r.method === 'POST')).toEqual([
    { method: 'POST', path: '/api/forecast/overrides', search: '' },
  ]);
  expect(api.requests.filter((r) => r.path.startsWith('/api/recommendations'))).toEqual([]);
  const allowed = [
    { method: 'GET', path: '/api/forecast', search: '?limit=1000' },
    { method: 'GET', path: '/api/forecast/by-key' },
    { method: 'GET', path: '/api/forecast/overrides' },
    { method: 'POST', path: '/api/forecast/overrides', search: '' },
    { method: 'GET', path: '/api/forecast/accuracy', search: '?bucket_index=1&limit=1000' },
    { method: 'GET', path: '/api/forecast/accuracy', search: '?limit=1000' },
    { method: 'GET', path: '/api/forecast/accuracy/signals', search: '' },
    { method: 'GET', path: '/api/forecast/accuracy/corrections', search: '' },
    { method: 'GET', path: '/api/forecast/overrides/proposals', search: '' },
    '/api/work/scorecard',
  ];
  for (const req of api.requests.filter((r) => r.path.startsWith('/api/forecast'))) {
    expect(
      allowed.some(
        (a) => a.method === req.method && a.path === req.path && (a.search === undefined || a.search === req.search),
      ),
    ).toBe(true);
  }
});
