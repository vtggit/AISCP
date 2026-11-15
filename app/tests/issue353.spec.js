// Proving spec for AIERP issue #353 — admin override and withdrawal of
// forecast periods in the Demand view (#/demand).
//
// The gate serves the static app with no backend, so every /api/* call
// is mocked with page.route. The mocked routes keep a list of stored
// overrides that the two POST routes add to, and answer the by-key
// detail and the overrides list from it: a stored override row makes
// its bucket's effective quantity and overridden days, and a stored
// withdrawal removes the effect of the override it withdraws.
//
// Proves, signed in through the development token card as an admin
// (/api/auth/me roles ["admin"]), at 1440x900:
//   * the view asks GET /api/auth/me once per view load, through
//     app/js/api.js: exactly one /me call on top of the sign-in's;
//   * only the admin sees the controls: every "Forecast periods" row
//     ([data-testid="demand-period"]) carries an "Override" button
//     ([data-testid="override-open"]) and every current override
//     ([data-testid="override-row"]) whose period_end is after the
//     forecast's as_of carries a "Withdraw" button
//     ([data-testid="override-withdraw"]);
//   * "Override" opens one form under its row
//     ([data-testid="override-form"]) with the quantity field
//     ([data-testid="override-quantity"]) at the period's quantity —
//     the effective one when overridden, the statistical one when
//     not — and a reason field
//     ([data-testid="override-reason"]); a second "Override" moves
//     the form to the other row, and Cancel
//     ([data-testid="override-cancel"]) closes it with no request
//     sent;
//   * "Save" ([data-testid="override-save"]) is disabled with a blank
//     reason, with a reason of only spaces, with a negative quantity
//     and with a quantity that is not a number, and is enabled with 0
//     and a reason [rule: OVR-5] [rule: OVR-6];
//   * Save sends one POST /api/forecast/overrides with the right
//     item_code, site_code, period_start, quantity and trimmed reason
//     [rule: OVR-1] — one request even when the button is clicked
//     twice;
//   * after the save: the form is closed, the period row reads
//     "Overridden", the override is listed under "Current overrides",
//     the chosen list row carries the
//     [data-testid="demand-row-overridden"] marker, the focus is on
//     that period's "Override" button, and the list is not loaded
//     again;
//   * a save answered 422 with a detail sentence: the sentence is
//     shown in [data-testid="override-error"], the form stays open
//     with the typed values, and a second attempt succeeds;
//   * "Withdraw" opens its form ([data-testid="withdraw-form"]) with
//     a reason field ([data-testid="withdraw-reason"]), disabled until
//     the reason is not blank, and sends
//     POST /api/forecast/overrides/withdrawals with item_code,
//     site_code, period_start and the trimmed reason [rule: OVR-9];
//     afterwards the override is gone from "Current overrides" and
//     the chosen list row's marker is gone; a withdrawal answered 409
//     shows the message in [data-testid="withdraw-error"] with the
//     form still open;
//   * a current override whose period ended before the forecast's
//     as_of has no "Withdraw" button [rule: OVR-12];
//   * a save delayed in the mocked route with another row chosen
//     before it answers: the second item-site's detail is unchanged
//     when the save lands;
//   * with 300 rows at 1440x900 neither the document nor the view
//     scrolls, and the list scrolled to 2,000 px stays at 2,000 px
//     through a save;
//   * no horizontal page scroll at 390x844 with a form open.
//
// The "(viewer)" pass boots the production way as a NON-ADMIN
// (role 'user'): the token arrives in the URL hash as
// #access_token=..., /api/auth/config answers production and
// /api/auth/me answers for the viewer — the fragment stays inert and
// the PKCE round-trip establishes the session. The periods table and
// the current overrides are shown, with no "Override" or "Withdraw"
// button; the same holds when the view's GET /api/auth/me answers
// 500 — a failed answer leaves the view read-only [rule: OVR-10].
//
// The delays live in the mocked routes (gates the spec resolves),
// never as fixed waits in the page.
//
// The tests are TOP-LEVEL and each title is the full proof name
// ("issue353 freeform"): the merge gate builds its junit nodeids from
// the Playwright test title, and a describe() block would join the
// titles with a "›" the contract nodeid does not carry, so the proof
// would read as "not collected".

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
  user: { sub: 'u1', username: 'planner1', roles: ['admin'], groups: [] },
};

const VIEWER_ME = {
  authenticated: true,
  user: { sub: 'u2', username: 'viewer1', roles: ['user'], groups: [] },
};

const EM_DASH = '\u2014';
const AS_OF = '2026-10-09';

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

// 12 buckets of 30 days starting the day after 2026-10-09, with no
// overridden days: the stored overrides are layered on top by the
// mocked by-key route, which answers the detail from the stored list.
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

// The by-key base details: the stored overrides are not baked in —
// the mocked route layers them on, as the backend does.
const A_DETAIL = {
  item_code: 'A',
  site_code: 'S1',
  as_of: AS_OF,
  demand_class: 'smooth',
  method: 'ses',
  params: { alpha: 0.3 },
  history_buckets: 18,
  total_forecast: 120,
  total_effective_forecast: 120,
  overridden_buckets: 0,
  forecast: forecastBuckets(new Array(12).fill(10)),
  backtest: { mae: 3.25, baseline_mae: 4.5, wape: 0.25, bias: -0.1 },
  arithmetic:
    'ses: forecast = final level 10.0, repeated for each of the 12 buckets (120.0 units)',
  history: historyBuckets(18, [10.5, 12, 9, 11]),
};

const B_DETAIL = {
  item_code: 'B',
  site_code: 'S1',
  as_of: AS_OF,
  demand_class: 'erratic',
  method: 'damped_trend',
  params: { alpha: 0.2, beta: 0.1, phi: 0.85 },
  history_buckets: 24,
  total_forecast: 18720,
  total_effective_forecast: 18720,
  overridden_buckets: 0,
  forecast: forecastBuckets(new Array(12).fill(1560.5)),
  backtest: { mae: 12.5, baseline_mae: 10.0, wape: 0.5, bias: 0.25 },
  arithmetic:
    'damped_trend: forecast 1,560.5 per bucket (18,726.0 units)',
  history: historyBuckets(24, [1500.25, 1520.5, 1480.75, 1560, 1560.125, 1510]),
};

const C_DETAIL = {
  item_code: 'C',
  site_code: 'S2',
  as_of: AS_OF,
  demand_class: 'lumpy',
  method: 'mean',
  params: {},
  history_buckets: 10,
  total_forecast: 36,
  total_effective_forecast: 36,
  overridden_buckets: 0,
  forecast: forecastBuckets(new Array(12).fill(3)),
  backtest: null,
  arithmetic: 'mean: forecast = 3.0, repeated for each of the 12 buckets (36.0 units)',
  history: historyBuckets(10, [4, 0, 4, 0, 4, 0, 4, 0, 4, 0]),
};

const DETAILS = {
  'A|S1': A_DETAIL,
  'B|S1': B_DETAIL,
  'C|S2': C_DETAIL,
};

// The list rows carry no history; the by-key detail adds it back.
const ROWS = [A_DETAIL, B_DETAIL, C_DETAIL].map((row) => {
  const { history, ...rest } = row;
  return rest;
});

// The stored override rows per item-site, as the mocked route keeps
// them. B's bucket-2 override applies; B's September override is for
// a completed period (it applies too, but cannot be withdrawn); A
// and C start with none.
const STORED = {
  'A|S1': [],
  'B|S1': [
    {
      id: 11,
      item_code: 'B',
      site_code: 'S1',
      period_start: '2026-12-09',
      period_end: '2027-01-07',
      kind: 'override',
      quantity: 2000,
      reason: 'Holiday push',
      created_by: 'u1',
      created_by_name: 'planner1',
      created_at: '2026-10-08T09:30:00Z',
      applies: true,
    },
    {
      id: 12,
      item_code: 'B',
      site_code: 'S1',
      period_start: '2026-09-01',
      period_end: '2026-09-30',
      kind: 'override',
      quantity: 999,
      reason: 'Old, completed',
      created_by: 'u1',
      created_by_name: 'planner1',
      created_at: '2026-08-25T10:00:00Z',
      applies: true,
    },
  ],
  'C|S2': [],
};

const ACCURACY_AHEAD = {
  as_of: AS_OF,
  compared_buckets: 976,
  overall: { forecast_total: 5000, actual_total: 4600, wape: 0.27, bias: -0.04 },
  by_horizon: [
    { bucket_index: 1, compared_buckets: 976, forecast_total: 5000, actual_total: 4600, wape: 0.27, bias: -0.04 },
  ],
  item_sites: [],
};

const ACCURACY_ALL = {
  as_of: AS_OF,
  compared_buckets: 6051,
  overall: { forecast_total: 30000, actual_total: 28000, wape: 0.42, bias: 0.06 },
  by_horizon: [
    { bucket_index: 1, compared_buckets: 976, forecast_total: 5000, actual_total: 4600, wape: 0.27, bias: -0.04 },
    { bucket_index: 2, compared_buckets: 5075, forecast_total: 25000, actual_total: 23400, wape: 0.42, bias: 0.06 },
  ],
  item_sites: [],
};

function bigRows() {
  const classes = ['smooth', 'erratic', 'intermittent', 'lumpy'];
  const rows = [];
  for (let i = 1; i <= 300; i += 1) {
    const total = 3000 - i;
    const per = Math.round((total / 12) * 10) / 10;
    rows.push({
      item_code: 'BIG-' + String(i).padStart(4, '0'),
      site_code: 'WH-' + ((i % 3) + 1),
      as_of: AS_OF,
      demand_class: classes[i % 4],
      method: 'ses',
      params: { alpha: 0.3 },
      history_buckets: 12,
      total_forecast: total,
      total_effective_forecast: total,
      overridden_buckets: 0,
      forecast: forecastBuckets(new Array(12).fill(per)),
      backtest: { mae: 1.5, baseline_mae: 2.0, wape: 0.1, bias: 0.0 },
      arithmetic: 'ses: level = 0.3 × quantity + 0.7 × level; forecast = final level, repeated',
    });
  }
  return rows;
}

function bigDetails(rows) {
  const details = {};
  rows.forEach((row) => {
    details[row.item_code + '|' + row.site_code] = Object.assign({}, row, {
      history: historyBuckets(12, [5, 6, 5, 6]),
    });
  });
  return details;
}

// The stored list, re-keyed: every period keeps the row with the
// highest id; only an override row that is the current row of its
// period applies.
function recomputeApplies(rows) {
  const latest = {};
  rows.forEach((row) => {
    if (!latest[row.period_start] || row.id > latest[row.period_start].id) {
      latest[row.period_start] = row;
    }
  });
  rows.forEach((row) => {
    row.applies = row.kind === 'override' && latest[row.period_start].id === row.id;
  });
}

// The bucket's length in days from its dates.
function daysIn(bucket) {
  return Math.round((Date.parse(bucket.bucket_end) - Date.parse(bucket.bucket_start)) / 86400000) + 1;
}

// The by-key answer the mocked route builds from the base detail and
// the stored list: a current override makes its bucket's effective
// quantity and overridden days; a withdrawal removes the effect.
function detailWithOverrides(base, storedRows) {
  const latest = {};
  storedRows.forEach((row) => {
    if (!latest[row.period_start] || row.id > latest[row.period_start].id) {
      latest[row.period_start] = row;
    }
  });
  const detail = JSON.parse(JSON.stringify(base));
  detail.forecast = detail.forecast.map((bucket) => {
    const row = latest[bucket.bucket_start];
    if (row && row.kind === 'override') {
      return Object.assign({}, bucket, {
        effective_quantity: row.quantity,
        override_days: daysIn(bucket),
      });
    }
    return Object.assign({}, bucket, {
      effective_quantity: bucket.quantity,
      override_days: 0,
    });
  });
  detail.overridden_buckets = detail.forecast.filter((b) => b.override_days > 0).length;
  return detail;
}

// Mock every /api/* call; records the /me, the /api/forecast, the
// by-key, the overrides and the two POST requests so the spec can
// inspect them. The stored list is kept per item-site: the two POST
// routes add to it, and the detail and the overrides answer from it.
// opts: rows, details, stored, me, me500AfterFirst (the view's /me
// answers 500), authConfig, saveGate (holds the save POST open inside
// the mocked route until resolved), withdrawGate, saveFail, withdrawFail.
async function mockDemandApi(page, opts = {}) {
  const api = {
    meResponses: [],
    listGets: 0,
    byKeyGets: [],
    overrideGets: [],
    savePosts: [],
    withdrawPosts: [],
    rows: opts.rows || ROWS,
    details: opts.details || DETAILS,
    stored: JSON.parse(JSON.stringify(opts.stored || STORED)),
    saveFail: opts.saveFail || null,
    withdrawFail: opts.withdrawFail || null,
    saveGate: opts.saveGate || null,
    withdrawGate: opts.withdrawGate || null,
    nextId: 1000,
  };
  await page.route('**/api/**', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (url.pathname === '/api/auth/config') {
      return route.fulfill({ status: 200, json: opts.authConfig || DEV_CONFIG });
    }
    if (url.pathname === '/api/auth/me') {
      const refused = opts.me500AfterFirst && api.meResponses.length >= 1;
      api.meResponses.push(refused ? 500 : 200);
      if (refused) {
        return route.fulfill({
          status: 500,
          json: { detail: 'Identity service unavailable.' },
        });
      }
      return route.fulfill({ status: 200, json: opts.me || ADMIN_ME });
    }
    if (url.pathname === '/api/forecast' && method === 'GET') {
      api.listGets += 1;
      return route.fulfill({ status: 200, json: api.rows });
    }
    if (url.pathname === '/api/forecast/by-key') {
      const item = url.searchParams.get('item_code');
      const site = url.searchParams.get('site_code');
      api.byKeyGets.push({ item, site });
      const base = api.details[item + '|' + site];
      if (!base) {
        return route.fulfill({ status: 404, json: { detail: 'forecast not found' } });
      }
      return route.fulfill({ status: 200, json: detailWithOverrides(base, api.stored[item + '|' + site] || []) });
    }
    if (url.pathname === '/api/forecast/overrides' && method === 'GET') {
      const item = url.searchParams.get('item_code');
      const site = url.searchParams.get('site_code');
      api.overrideGets.push({ item, site, limit: url.searchParams.get('limit') });
      const rows = (api.stored[item + '|' + site] || []).slice().sort((a, b) => b.id - a.id);
      return route.fulfill({ status: 200, json: rows });
    }
    if (url.pathname === '/api/forecast/overrides' && method === 'POST') {
      const body = JSON.parse(request.postData() || '{}');
      api.savePosts.push(body);
      const respond = () => {
        if (api.saveFail) {
          return route.fulfill({ status: api.saveFail.status, json: { detail: api.saveFail.detail } });
        }
        const key = body.item_code + '|' + body.site_code;
        const rows = api.stored[key] || (api.stored[key] = []);
        const base = api.details[key];
        const bucket = base
          ? base.forecast.find((b) => b.bucket_start === body.period_start)
          : null;
        const row = {
          id: ++api.nextId,
          item_code: body.item_code,
          site_code: body.site_code,
          period_start: body.period_start,
          period_end: bucket ? bucket.bucket_end : null,
          kind: 'override',
          quantity: body.quantity,
          reason: body.reason,
          created_by: 'u1',
          created_by_name: 'planner1',
          created_at: '2026-10-09T12:00:00Z',
          applies: true,
        };
        rows.push(row);
        recomputeApplies(rows);
        return route.fulfill({ status: 201, json: row });
      };
      if (api.saveGate) {
        return api.saveGate.promise.then(() => respond());
      }
      return respond();
    }
    if (url.pathname === '/api/forecast/overrides/withdrawals' && method === 'POST') {
      const body = JSON.parse(request.postData() || '{}');
      api.withdrawPosts.push(body);
      const respond = () => {
        if (api.withdrawFail) {
          return route.fulfill({ status: api.withdrawFail.status, json: { detail: api.withdrawFail.detail } });
        }
        const key = body.item_code + '|' + body.site_code;
        const rows = api.stored[key] || (api.stored[key] = []);
        const row = {
          id: ++api.nextId,
          item_code: body.item_code,
          site_code: body.site_code,
          period_start: body.period_start,
          period_end: body.period_start,
          kind: 'withdrawal',
          quantity: null,
          reason: body.reason,
          created_by: 'u1',
          created_by_name: 'planner1',
          created_at: '2026-10-09T12:00:00Z',
          applies: false,
        };
        rows.push(row);
        recomputeApplies(rows);
        return route.fulfill({ status: 201, json: row });
      };
      if (api.withdrawGate) {
        return api.withdrawGate.promise.then(() => respond());
      }
      return respond();
    }
    if (url.pathname === '/api/forecast/accuracy') {
      const isAhead = url.searchParams.get('bucket_index') === '1';
      return route.fulfill({ status: 200, json: isAhead ? ACCURACY_AHEAD : ACCURACY_ALL });
    }
    return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
  });
  return api;
}

async function signIn(page) {
  await expect(page.locator('#signin-form')).toBeVisible();
  await page.fill('#token-input', 'dev-admin-token');
  await page.click('#signin-button');
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('planner1');
}

// The viewer boots the production way: the token arrives in the URL
// hash the way production used to deliver it, /api/auth/config
// answers production, and the PKCE round-trip establishes the
// session. The fragment itself stays inert. The PKCE return lands on
// #/overview, so the boot ends by switching the hash to #/demand
// without a reload — the view's one /me load on top of the sign-in's.
async function bootViewer(page, apiOpts) {
  const api = await mockDemandApi(page, apiOpts);
  await page.route(/protocol\/openid-connect\/token/, (route) => {
    return route.fulfill({
      status: 200,
      json: { access_token: 'pkce-viewer-token', token_type: 'Bearer' },
    });
  });
  await page.route(/protocol\/openid-connect\/auth/, (route) => {
    const url = new URL(route.request().url());
    const state = url.searchParams.get('state') || '';
    // Return the browser to the app's own redirect_uri — where the
    // app asked the IdP to send it — with the one-time code.
    const redirectUri = url.searchParams.get('redirect_uri') || url.origin;
    const backToApp =
      (redirectUri.endsWith('/') ? redirectUri : redirectUri + '/') +
      '?code=fake-pkce-code&state=' +
      encodeURIComponent(state);
    return route.fulfill({
      status: 200,
      contentType: 'text/html',
      body:
        '<!doctype html><html><body><script>window.location.replace(' +
        JSON.stringify(backToApp) +
        ');</script></body></html>',
    });
  });
  await page.goto('/#access_token=viewer-token-abc');
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('viewer1');
  // The proof watches the Demand view: switch to it without a reload,
  // so the view's own GET /api/auth/me is the only /me on top of the
  // sign-in's.
  await page.evaluate(() => {
    window.location.hash = '#/demand';
  });
  return api;
}

// The first row fully visible in the list (below the sticky header),
// skipping the first skipIndex rows; null when there is none.
async function pickVisibleRow(page, skipIndex) {
  return page.evaluate((skip) => {
    const body = document.querySelector('.d-list-body');
    const header = document.querySelector('.d-row-head');
    const list = Array.from(document.querySelectorAll('[data-testid="demand-row"]'));
    const top = header.getBoundingClientRect().bottom;
    const bottom = body.getBoundingClientRect().bottom;
    for (let i = skip; i < list.length; i += 1) {
      const rect = list[i].getBoundingClientRect();
      if (rect.top >= top - 1 && rect.bottom <= bottom + 1) {
        return {
          index: i,
          item: list[i].getAttribute('data-item'),
          site: list[i].getAttribute('data-site'),
        };
      }
    }
    return null;
  }, skipIndex);
}

// The element the focus is on, by the hooks the proof checks.
async function focusInfo(page) {
  return page.evaluate(() => {
    const el = document.activeElement;
    if (!el) {
      return null;
    }
    const periodRow = el.closest ? el.closest('[data-testid="demand-period"]') : null;
    return {
      testid: el.getAttribute ? el.getAttribute('data-testid') : null,
      periodStart: periodRow ? periodRow.getAttribute('data-period-start') : null,
    };
  });
}

test('issue353 freeform', async ({ page }) => {
  page.setViewportSize({ width: 1440, height: 900 });
  const api = await mockDemandApi(page);

  await page.goto('/#/demand');
  await signIn(page);

  const rowA = page.locator('[data-testid="demand-row"][data-item="A"]');
  const rowB = page.locator('[data-testid="demand-row"][data-item="B"]');

  // --- the admin sees the controls, and the view asked /me once -------
  await expect(page.locator('.d-detail-title')).toHaveText('A \u00b7 S1');
  // The sign-in's /me and the view's own one — once per view load.
  expect(api.meResponses).toEqual([200, 200]);
  const periodRows = page.locator('[data-testid="demand-period"]');
  await expect(periodRows).toHaveCount(12);
  await expect(page.locator('[data-testid="override-open"]')).toHaveCount(12);
  await expect(page.locator('[data-testid="override-withdraw"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="demand-overrides"]')).toHaveText(
    'No overrides for this item-site.',
  );
  // A starts with no overridden bucket: no marker on the chosen row.
  await expect(rowA.locator('[data-testid="demand-row-overridden"]')).toHaveCount(0);

  // --- "Override" opens one form under its row -------------------------
  await periodRows.nth(0).locator('[data-testid="override-open"]').click();
  await expect(page.locator('[data-testid="override-form"]')).toHaveCount(1);
  // The form is the row under the period row it belongs to.
  await expect
    .poll(async () =>
      page.evaluate((start) => {
        const rows = Array.from(document.querySelectorAll('[data-testid="demand-period"]'));
        const row = rows.find((r) => r.getAttribute('data-period-start') === start);
        const formRow = row && row.nextElementSibling;
        return !!formRow && !!formRow.querySelector('[data-testid="override-form"]');
      }, '2026-10-10'),
    )
    .toBe(true);
  // The quantity starts at the period's quantity: the statistical one
  // when nothing is overridden.
  await expect(page.locator('[data-testid="override-quantity"]')).toHaveValue('10');
  await expect(page.locator('[data-testid="override-reason"]')).toHaveValue('');
  // A second "Override" moves the form to the other row.
  await periodRows.nth(1).locator('[data-testid="override-open"]').click();
  await expect(page.locator('[data-testid="override-form"]')).toHaveCount(1);
  await expect
    .poll(async () =>
      page.evaluate((start) => {
        const rows = Array.from(document.querySelectorAll('[data-testid="demand-period"]'));
        const row = rows.find((r) => r.getAttribute('data-period-start') === start);
        const formRow = row && row.nextElementSibling;
        return !!formRow && !!formRow.querySelector('[data-testid="override-form"]');
      }, '2026-11-09'),
    )
    .toBe(true);
  // ...and the first row has none any more.
  expect(
    await page.evaluate(() => {
      const rows = Array.from(document.querySelectorAll('[data-testid="demand-period"]'));
      const row = rows.find((r) => r.getAttribute('data-period-start') === '2026-10-10');
      return !!row && !!row.nextElementSibling && !!row.nextElementSibling.querySelector('[data-testid="override-form"]');
    }),
  ).toBe(false);
  // Cancel closes the form with nothing sent.
  await page.click('[data-testid="override-cancel"]');
  await expect(page.locator('[data-testid="override-form"]')).toHaveCount(0);
  expect(api.savePosts).toEqual([]);
  expect(api.withdrawPosts).toEqual([]);

  // --- Save: disabled until the quantity is a number of 0 or more and
  //     the reason is not blank ------------------------------------------
  await periodRows.nth(0).locator('[data-testid="override-open"]').click();
  const quantityInput = page.locator('[data-testid="override-quantity"]');
  const reasonInput = page.locator('[data-testid="override-reason"]');
  const saveButton = page.locator('[data-testid="override-save"]');
  // A blank reason refuses.
  await expect(saveButton).toBeDisabled();
  await reasonInput.fill('   ');
  await expect(saveButton).toBeDisabled();
  await reasonInput.fill('Promo push');
  await expect(saveButton).toBeEnabled();
  // A negative quantity refuses.
  await quantityInput.fill('-5');
  await expect(saveButton).toBeDisabled();
  // A quantity that is not a number refuses.
  await quantityInput.fill('twelve');
  await expect(saveButton).toBeDisabled();
  // 0 with a reason is allowed [rule: OVR-6].
  await quantityInput.fill('0');
  await reasonInput.fill('  Promo push  ');
  await expect(saveButton).toBeEnabled();

  // --- Save sends one POST, even when the button is clicked twice ------
  // Hold the save open in the mocked route so the in-flight state —
  // the disabled send button — is observable.
  const firstSaveGate = { promise: null, resolve: null };
  firstSaveGate.promise = new Promise((resolve) => {
    firstSaveGate.resolve = resolve;
  });
  api.saveGate = firstSaveGate;
  await saveButton.click();
  // The send button is disabled while the request is out, so a second
  // click on it sends nothing new.
  await expect(saveButton).toBeDisabled();
  await saveButton.dispatchEvent('click');
  expect(api.savePosts).toEqual([
    {
      item_code: 'A',
      site_code: 'S1',
      period_start: '2026-10-10',
      quantity: 0,
      reason: 'Promo push',
    },
  ]);
  // Let the held save answer; the "after the save" state follows.
  firstSaveGate.resolve();
  api.saveGate = null;

  // --- after the save: closed, reloaded, marked, focused ----------------
  await expect(page.locator('[data-testid="override-form"]')).toHaveCount(0);
  const savedPeriod = periodRows.nth(0);
  expect(await savedPeriod.locator('.d-p-eff').textContent()).toBe('0');
  expect(await savedPeriod.locator('.d-p-cov').textContent()).toBe('Overridden');
  // The override is listed under "Current overrides".
  const aOvr = page.locator('[data-testid="override-row"]');
  await expect(aOvr).toHaveCount(1);
  await expect(aOvr).toHaveAttribute('data-period-start', '2026-10-10');
  expect(await aOvr.locator('.d-ovr-qty').textContent()).toBe('0');
  expect(await aOvr.locator('.d-ovr-reason').textContent()).toBe('Promo push');
  expect(await aOvr.locator('.d-ovr-who').textContent()).toContain('planner1');
  // The chosen list row's marker follows the reloaded detail.
  await expect(rowA.locator('[data-testid="demand-row-overridden"]')).toHaveCount(1);
  // The focus is on that period's "Override" button.
  await expect.poll(() => focusInfo(page)).toEqual({
    testid: 'override-open',
    periodStart: '2026-10-10',
  });
  // The list is not loaded again.
  expect(api.listGets).toBe(1);

  // --- a 422 save: the sentence in the form, the second attempt wins ---
  api.saveFail = { status: 422, detail: 'The period is outside the current forecast horizon.' };
  await periodRows.nth(1).locator('[data-testid="override-open"]').click();
  await quantityInput.fill('42');
  await reasonInput.fill('  Stale attempt  ');
  await expect(saveButton).toBeEnabled();
  await saveButton.click();
  await expect(page.locator('[data-testid="override-error"]')).toHaveText(
    'The period is outside the current forecast horizon.',
  );
  await expect(page.locator('[data-testid="override-error"]')).toHaveAttribute('role', 'alert');
  // The form is still open with what was typed.
  await expect(page.locator('[data-testid="override-form"]')).toHaveCount(1);
  await expect(quantityInput).toHaveValue('42');
  await expect(reasonInput).toHaveValue('  Stale attempt  ');
  api.saveFail = null;
  await saveButton.click();
  // The refused attempt and the successful retry, on top of the first
  // save: exactly one more request per click.
  expect(api.savePosts.length).toBe(3);
  await expect(page.locator('[data-testid="override-form"]')).toHaveCount(0);
  const stalePeriod = periodRows.nth(1);
  expect(await stalePeriod.locator('.d-p-eff').textContent()).toBe('42');
  expect(await stalePeriod.locator('.d-p-cov').textContent()).toBe('Overridden');
  const aOvrNow = page.locator('[data-testid="override-row"]');
  await expect(aOvrNow).toHaveCount(2);
  expect(
    await aOvrNow.evaluateAll((rows) => rows.map((row) => row.getAttribute('data-period-start'))),
  ).toEqual(['2026-10-10', '2026-11-09']);

  // --- B: withdraw the current override, not the completed one ---------
  await rowB.click();
  await expect(page.locator('.d-detail-title')).toHaveText('B \u00b7 S1');
  const bOvr = page.locator('[data-testid="override-row"]');
  await expect(bOvr).toHaveCount(2);
  // In period order: the completed September period first.
  expect(
    await bOvr.evaluateAll((rows) => rows.map((row) => row.getAttribute('data-period-start'))),
  ).toEqual(['2026-09-01', '2026-12-09']);
  // The completed period's override has no "Withdraw" button
  // [rule: OVR-12]; the current one does.
  await expect(bOvr.nth(0).locator('[data-testid="override-withdraw"]')).toHaveCount(0);
  await expect(bOvr.nth(1).locator('[data-testid="override-withdraw"]')).toHaveCount(1);
  // The quantity field of an overridden period starts at its
  // effective quantity.
  const bPeriod = page.locator('[data-testid="demand-period"][data-period-start="2026-12-09"]');
  await bPeriod.locator('[data-testid="override-open"]').click();
  await expect(page.locator('[data-testid="override-quantity"]')).toHaveValue('2000');
  await page.click('[data-testid="override-cancel"]');
  await expect(page.locator('[data-testid="override-form"]')).toHaveCount(0);

  await bOvr.nth(1).locator('[data-testid="override-withdraw"]').click();
  await expect(page.locator('[data-testid="withdraw-form"]')).toHaveCount(1);
  const withdrawSave = page.locator('[data-testid="withdraw-save"]');
  const withdrawReason = page.locator('[data-testid="withdraw-reason"]');
  // Disabled with a blank reason.
  await expect(withdrawSave).toBeDisabled();
  await withdrawReason.fill('  Demand resumed  ');
  await expect(withdrawSave).toBeEnabled();
  await withdrawSave.click();
  expect(api.withdrawPosts).toEqual([
    {
      item_code: 'B',
      site_code: 'S1',
      period_start: '2026-12-09',
      reason: 'Demand resumed',
    },
  ]);
  // The form is closed and the override is gone from "Current
  // overrides" — only the completed one is listed any more.
  await expect(page.locator('[data-testid="withdraw-form"]')).toHaveCount(0);
  const bOvrNow = page.locator('[data-testid="override-row"]');
  await expect(bOvrNow).toHaveCount(1);
  await expect(bOvrNow).toHaveAttribute('data-period-start', '2026-09-01');
  // B's bucket is back to the statistical quantity, and the chosen
  // list row's marker is gone.
  expect(await bPeriod.locator('.d-p-eff').textContent()).toBe(EM_DASH);
  expect(await bPeriod.locator('.d-p-cov').textContent()).toBe(EM_DASH);
  await expect(rowB.locator('[data-testid="demand-row-overridden"]')).toHaveCount(0);
  // The withdrawn row's button is gone: the focus lands on the
  // periods table.
  await expect.poll(() => focusInfo(page)).toEqual({
    testid: 'demand-periods',
    periodStart: null,
  });
  expect(api.listGets).toBe(1);

  // --- a 409 withdrawal: the message in the form, which stays open -----
  await rowA.click();
  await expect(page.locator('.d-detail-title')).toHaveText('A \u00b7 S1');
  api.withdrawFail = { status: 409, detail: 'Nothing to withdraw for that period.' };
  const aFirstOvr = page.locator('[data-testid="override-row"][data-period-start="2026-10-10"]');
  await aFirstOvr.locator('[data-testid="override-withdraw"]').click();
  await withdrawReason.fill('Too late');
  await withdrawSave.click();
  await expect(page.locator('[data-testid="withdraw-error"]')).toHaveText(
    'Nothing to withdraw for that period.',
  );
  await expect(page.locator('[data-testid="withdraw-error"]')).toHaveAttribute('role', 'alert');
  await expect(page.locator('[data-testid="withdraw-form"]')).toHaveCount(1);
  await expect(withdrawReason).toHaveValue('Too late');
  api.withdrawFail = null;
  await page.click('[data-testid="withdraw-cancel"]');
  await expect(page.locator('[data-testid="withdraw-form"]')).toHaveCount(0);
  // B's withdrawal and the refused one — one request per click, and
  // the cancel sent nothing.
  expect(api.withdrawPosts).toEqual([
    {
      item_code: 'B',
      site_code: 'S1',
      period_start: '2026-12-09',
      reason: 'Demand resumed',
    },
    {
      item_code: 'A',
      site_code: 'S1',
      period_start: '2026-10-10',
      reason: 'Too late',
    },
  ]);

  // --- a save delayed in the mocked route: a later choice wins ---------
  const stalePage = await page.context().newPage();
  stalePage.setViewportSize({ width: 1440, height: 900 });
  const staleRows = [ROWS[0], ROWS[2]];
  const staleDetails = { 'A|S1': A_DETAIL, 'C|S2': C_DETAIL };
  const saveGate = { promise: null, resolve: null };
  saveGate.promise = new Promise((resolve) => {
    saveGate.resolve = resolve;
  });
  const staleApi = await mockDemandApi(stalePage, {
    rows: staleRows,
    details: staleDetails,
    stored: { 'A|S1': [], 'C|S2': [] },
    saveGate: saveGate,
  });
  await stalePage.goto('/#/demand');
  await signIn(stalePage);
  await expect(stalePage.locator('.d-detail-title')).toHaveText('A \u00b7 S1');
  // Open a save, hold it in the mocked route, then choose C.
  await stalePage
    .locator('[data-testid="demand-period"][data-period-start="2026-12-09"]')
    .locator('[data-testid="override-open"]')
    .click();
  await stalePage.fill('[data-testid="override-quantity"]', '7');
  await stalePage.fill('[data-testid="override-reason"]', 'Slow save');
  await stalePage.click('[data-testid="override-save"]');
  expect(staleApi.savePosts).toEqual([
    {
      item_code: 'A',
      site_code: 'S1',
      period_start: '2026-12-09',
      quantity: 7,
      reason: 'Slow save',
    },
  ]);
  // Choose C while the save is still in flight.
  await stalePage.locator('[data-testid="demand-row"][data-item="C"]').click();
  await expect(stalePage.locator('.d-detail-title')).toHaveText('C \u00b7 S2');
  // Wait for C's overrides to settle before capturing the detail's
  // text, so the capture is the final one the save must not change.
  await expect(stalePage.locator('[data-testid="demand-overrides"]')).toHaveText(
    'No overrides for this item-site.',
  );
  const cBefore = await stalePage.locator('.d-detail-inner').textContent();
  await expect(stalePage.locator('[data-testid="demand-row"][data-item="C"] [data-testid="demand-row-overridden"]')).toHaveCount(0);
  // The save lands: C's detail is unchanged.
  saveGate.resolve();
  await expect
    .poll(async () => stalePage.locator('.d-detail-inner').textContent())
    .toBe(cBefore);
  await expect(stalePage.locator('.d-detail-title')).toHaveText('C \u00b7 S2');
  await expect(stalePage.locator('[data-testid="demand-overrides"]')).toHaveText(
    'No overrides for this item-site.',
  );
  await expect(stalePage.locator('[data-testid="demand-row"][data-item="C"] [data-testid="demand-row-overridden"]')).toHaveCount(0);
  await stalePage.close();

  // --- 300 rows at 1440x900: no document/view scroll, and a save keeps
  //     the list at 2,000 px ----------------------------------------------
  const bigPage = await page.context().newPage();
  bigPage.setViewportSize({ width: 1440, height: 900 });
  const bigRowsList = bigRows();
  const bigApi = await mockDemandApi(bigPage, {
    rows: bigRowsList,
    details: bigDetails(bigRowsList),
    stored: {},
  });
  await bigPage.goto('/#/demand');
  await signIn(bigPage);
  await expect(bigPage.locator('[data-testid="demand-row"]')).toHaveCount(300);
  await expect(bigPage.locator('.d-detail-title')).toHaveText('BIG-0001 \u00b7 WH-2');
  await expect(bigPage.locator('[data-testid="demand-overrides"]')).toHaveText(
    'No overrides for this item-site.',
  );
  const layout = await bigPage.evaluate(() => {
    const view = document.getElementById('view-demand');
    return {
      docScrollHeight: document.documentElement.scrollHeight,
      docClientHeight: document.documentElement.clientHeight,
      docScrollWidth: document.documentElement.scrollWidth,
      viewScroll: view.scrollHeight,
      viewClient: view.clientHeight,
    };
  });
  expect(layout.docScrollHeight).toBe(layout.docClientHeight);
  expect(layout.docScrollWidth).toBeLessThanOrEqual(1440);
  expect(layout.viewScroll).toBeLessThanOrEqual(layout.viewClient);

  await bigPage.evaluate(() => {
    document.querySelector('.d-list-body').scrollTop = 2000;
  });
  expect(await bigPage.evaluate(() => document.querySelector('.d-list-body').scrollTop)).toBe(2000);
  const clicked = await pickVisibleRow(bigPage, 0);
  expect(clicked).not.toBeNull();
  await bigPage.locator('[data-testid="demand-row"]').nth(clicked.index).click();
  await expect(bigPage.locator('.d-detail-title')).toHaveText(
    clicked.item + ' \u00b7 ' + clicked.site,
  );
  // A save through the reloaded detail: the list keeps its scroll
  // position and is not loaded again.
  await bigPage
    .locator('[data-testid="demand-period"]')
    .first()
    .locator('[data-testid="override-open"]')
    .click();
  await bigPage.fill('[data-testid="override-quantity"]', '5');
  await bigPage.fill('[data-testid="override-reason"]', 'Scroll save');
  await bigPage.click('[data-testid="override-save"]');
  await expect(bigPage.locator('[data-testid="override-form"]')).toHaveCount(0);
  await expect(bigPage.locator('[data-testid="override-row"]')).toHaveCount(1);
  expect(await bigPage.evaluate(() => document.querySelector('.d-list-body').scrollTop)).toBe(2000);
  expect(bigApi.listGets).toBe(1);
  await expect(bigPage.locator(`[data-testid="demand-row"][data-item="${clicked.item}"][data-site="${clicked.site}"] [data-testid="demand-row-overridden"]`)).toHaveCount(1);
  await bigPage.close();

  // --- 390x844: no horizontal page scroll with a form open ---------------
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .locator('[data-testid="demand-period"][data-period-start="2026-12-09"]')
    .locator('[data-testid="override-open"]')
    .click();
  await expect(page.locator('[data-testid="override-form"]')).toHaveCount(1);
  const narrow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(narrow.scrollWidth).toBeLessThanOrEqual(narrow.innerWidth);
});

test('issue353 freeform (viewer)', async ({ page }) => {
  // NON-ADMIN pass: boot the production way — the token arrives in the
  // URL hash the way production used to deliver it, /api/auth/config
  // answers production, and /api/auth/me answers for a user with only
  // the "user" role. The periods table and the current overrides are
  // shown, with no "Override" or "Withdraw" button; the same holds
  // when the view's /api/auth/me answers 500.
  const viewerRows = [
    {
      item_code: 'V',
      site_code: 'S9',
      as_of: AS_OF,
      demand_class: 'smooth',
      method: 'ses',
      params: { alpha: 0.3 },
      history_buckets: 18,
      total_forecast: 84,
      total_effective_forecast: 96,
      overridden_buckets: 1,
      forecast: forecastBuckets(new Array(12).fill(7)),
      backtest: { mae: 1.1, baseline_mae: 1.4, wape: 0.15, bias: 0.05 },
      arithmetic: 'ses: forecast = final level 7.0, repeated for each of the 12 buckets (84.0 units)',
    },
  ];
  const viewerDetails = {
    'V|S9': Object.assign({}, viewerRows[0], { history: historyBuckets(18, [7, 8, 6, 7]) }),
  };
  const viewerStored = {
    'V|S9': [
      {
        id: 21,
        item_code: 'V',
        site_code: 'S9',
        period_start: '2026-10-10',
        period_end: '2026-11-08',
        kind: 'override',
        quantity: 12,
        reason: 'Viewer can see but not touch',
        created_by: 'u2',
        created_by_name: 'viewer1',
        created_at: '2026-10-05T08:15:00Z',
        applies: true,
      },
    ],
  };
  const viewerChecks = async (p) => {
    await expect(p.locator('#app')).toBeVisible();
    await expect(p.locator('[data-testid="demand-period"]')).toHaveCount(12);
    // No admin control anywhere in the view.
    await expect(p.locator('[data-testid="override-open"]')).toHaveCount(0);
    await expect(p.locator('[data-testid="override-withdraw"]')).toHaveCount(0);
    await expect(p.locator('[data-testid="override-form"]')).toHaveCount(0);
    await expect(p.locator('[data-testid="withdraw-form"]')).toHaveCount(0);
    // The current override is still listed, read-only.
    const ovr = p.locator('[data-testid="override-row"]');
    await expect(ovr).toHaveCount(1);
    await expect(ovr).toHaveAttribute('data-period-start', '2026-10-10');
    expect(await ovr.locator('.d-ovr-reason').textContent()).toBe('Viewer can see but not touch');
  };

  // --- role "user": the page renders, the controls are absent -----------
  page.setViewportSize({ width: 1440, height: 900 });
  const api = await bootViewer(page, {
    authConfig: PROD_CONFIG,
    me: VIEWER_ME,
    rows: viewerRows,
    details: viewerDetails,
    stored: viewerStored,
  });
  await viewerChecks(page);
  // The view asked /me once per view load, on top of the sign-in's.
  expect(api.meResponses).toEqual([200, 200]);

  // --- the same with the view's /api/auth/me answering 500 --------------
  const failPage = await page.context().newPage();
  failPage.setViewportSize({ width: 1440, height: 900 });
  const failApi = await bootViewer(failPage, {
    authConfig: PROD_CONFIG,
    me: VIEWER_ME,
    me500AfterFirst: true,
    rows: viewerRows,
    details: viewerDetails,
    stored: viewerStored,
  });
  await viewerChecks(failPage);
  // The sign-in's /me answered, the view's did not — the view stayed
  // read-only.
  expect(failApi.meResponses).toEqual([200, 500]);
  await failPage.close();
});
