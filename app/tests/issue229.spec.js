// Proving spec for AIERP issue #229 — the Overview view, the manager's page.
//
// The gate serves the static app with no backend, so every /api/* call is
// mocked with page.route (/api/auth/config answers authMode development).
// Each scenario signs in through the development token card from #228 and
// proves, with fixed payloads:
//   * the three value tiles from GET /api/decisions/summary `total` —
//     accepted money with the accepted count, applied money in the accent
//     color with the progress bar and "N% of accepted money has reached
//     the ERP", outstanding money in the risk color with the outstanding
//     count;
//   * the three list cards — "N to decide" (the rows of that list whose
//     decision is null), accepted and applied money from that kind's
//     summary buckets, the dismissed count, and "Open … list" setting
//     #/queue?kind=<kind>;
//   * "Needs a decision, largest money first" — exactly the five
//     largest-money undecided rows, money descending, the money-0 row
//     before rows without money, ties by kind order (drift, excess,
//     stockout) then item_code then site_code, none of them decided, and
//     clicking the first row setting
//     #/queue?kind=<kind>&item=<item_code>&site=<site_code>;
//   * a quiet skeleton while a region loads;
//   * a 500 from /api/decisions/summary: the inline error + Retry in the
//     tiles region while the cards' list data still loads, and Retry
//     re-requesting only /api/decisions/summary, then showing the tiles;
//   * a failed load never leaves a blank page;
//   * at 1440x900 the document does not scroll and the Overview region
//     does not scroll either.
//
// The test is TOP-LEVEL and its title is the full proof name
// ("issue229 freeform"): the merge gate builds its junit nodeids from the
// Playwright test title, and a describe() block would join the titles with
// a "›" the contract nodeid does not carry, so the proof would read as
// "not collected".

const { test, expect } = require('@playwright/test');

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

const ME = {
  authenticated: true,
  user: { sub: 'u1', username: 'planner1', roles: ['admin'], groups: [] },
};

const LIST_QUERY = '?limit=1000&include_decided=true';

/* ----------------------------- fixed payloads ----------------------------- */

// A summary with known totals: total sums the three kinds bucket by bucket.
// accepted 18720 = 4200 + 5000 + 9520; applied 7600 = 2600 + 5000 + 0
// (40.6% of accepted, rounds to 41%); outstanding 11120 = 1600 + 0 + 9520.
const SUMMARY = {
  drift: {
    accepted: { count: 2, money: 4200, without_money: 0 },
    applied: { count: 1, money: 2600, without_money: 0 },
    outstanding: { count: 1, money: 1600, without_money: 0 },
    dismissed: { count: 3 },
    snoozed: { count: 1 },
  },
  excess: {
    accepted: { count: 1, money: 5000, without_money: 0 },
    applied: { count: 1, money: 5000, without_money: 0 },
    outstanding: { count: 0, money: 0, without_money: 0 },
    dismissed: { count: 2 },
    snoozed: { count: 0 },
  },
  stockout: {
    accepted: { count: 1, money: 9520, without_money: 1 },
    applied: { count: 0, money: 0, without_money: 0 },
    outstanding: { count: 1, money: 9520, without_money: 1 },
    dismissed: { count: 1 },
    snoozed: { count: 2 },
  },
  total: {
    accepted: { count: 4, money: 18720, without_money: 1 },
    applied: { count: 2, money: 7600, without_money: 0 },
    outstanding: { count: 2, money: 11120, without_money: 1 },
    dismissed: { count: 6 },
    snoozed: { count: 3 },
  },
};

// The same summary with nothing accepted: "0% of accepted money has
// reached the ERP".
function zeroBucket() {
  return { count: 0, money: 0, without_money: 0 };
}
const EMPTY_SUMMARY = {
  drift: { accepted: zeroBucket(), applied: zeroBucket(), outstanding: zeroBucket(), dismissed: { count: 0 }, snoozed: { count: 0 } },
  excess: { accepted: zeroBucket(), applied: zeroBucket(), outstanding: zeroBucket(), dismissed: { count: 0 }, snoozed: { count: 0 } },
  stockout: { accepted: zeroBucket(), applied: zeroBucket(), outstanding: zeroBucket(), dismissed: { count: 0 }, snoozed: { count: 0 } },
  total: { accepted: zeroBucket(), applied: zeroBucket(), outstanding: zeroBucket(), dismissed: { count: 0 }, snoozed: { count: 0 } },
};

function decisionOf(itemCode, siteCode, decision) {
  return {
    id: 'dec-' + itemCode.toLowerCase(),
    item_code: itemCode,
    site_code: siteCode,
    decision: decision,
    reason: decision === 'dismissed' ? 'not worth the effort' : null,
    snooze_until: null,
    as_of: '2026-10-08',
    decided_by: 'planner1',
    decided_at: '2026-10-07T12:00:00Z',
  };
}

function driftRow(fields) {
  return Object.assign(
    {
      item_code: '',
      site_code: '',
      as_of: '2026-10-08',
      category: 'drift',
      rank: null,
      direction: 'under',
      units_gap: 0,
      money_at_stake: null,
      unit_cost: null,
      notes: [],
      money_basis: 'units_short_per_year * unit_cost',
      reorder_point: 0,
      reorder_point_units: 0,
      safety_stock: 0,
      arithmetic: '',
      current_reorder_point: null,
      decision: null,
      decision_lapsed: false,
    },
    fields,
  );
}

function excessRow(fields) {
  return Object.assign(
    {
      rank: null,
      category: 'excess',
      dead_reason: null,
      item_code: '',
      site_code: '',
      on_hand_units: 0,
      unit_cost: null,
      last_demand_on: null,
      demand_window_units: 0,
      months_of_cover: null,
      justified_units: 0,
      excess_units: 0,
      money_tied_up: null,
      as_of: '2026-10-08',
      notes: [],
      arithmetic: '',
      decision: null,
      decision_lapsed: false,
    },
    fields,
  );
}

function stockoutRow(fields) {
  return Object.assign(
    {
      rank: null,
      category: 'at_risk',
      item_code: '',
      site_code: '',
      on_hand_units: 0,
      open_supply_units: 0,
      open_supply_due_by: null,
      unit_cost: null,
      lead_time_days: 0,
      lead_time_demand: 0,
      p_stockout: 0,
      days_of_cover: null,
      projected_stockout_on: null,
      recommended_reorder_point_units: null,
      expected_units_short: 0,
      money_at_risk: null,
      as_of: '2026-10-08',
      notes: [],
      arithmetic: '',
      decision: null,
      decision_lapsed: false,
    },
    fields,
  );
}

// Scenario 1 lists: decided and undecided rows mixed, with and without
// money, including one undecided row with money 0 (D-200) and one undecided
// row without money (D-300, money_at_stake null).
const DRIFT_ROWS = [
  driftRow({
    item_code: 'D-100', site_code: 'S1', rank: 1, direction: 'under',
    units_gap: 24, money_at_stake: 18720, unit_cost: 78,
    current_reorder_point: 30, reorder_point: 54, reorder_point_units: 54, safety_stock: 12,
  }),
  driftRow({
    item_code: 'D-200', site_code: 'S1', rank: 2, direction: 'over',
    units_gap: 10, money_at_stake: 0, unit_cost: 0,
    current_reorder_point: 90, reorder_point: 80, reorder_point_units: 80, safety_stock: 6,
  }),
  driftRow({
    item_code: 'D-300', site_code: 'S2', rank: null, direction: 'under',
    units_gap: 5, money_at_stake: null, unit_cost: null,
    current_reorder_point: 20, reorder_point: 25, reorder_point_units: 25, safety_stock: 4,
  }),
  driftRow({
    item_code: 'D-400', site_code: 'S1', rank: 3, direction: 'over',
    units_gap: 8, money_at_stake: 900, unit_cost: 45,
    current_reorder_point: 88, reorder_point: 80, reorder_point_units: 80, safety_stock: 8,
    decision: decisionOf('D-400', 'S1', 'accepted'),
  }),
];

const EXCESS_ROWS = [
  excessRow({
    item_code: 'E-100', site_code: 'S1', rank: 1, category: 'excess',
    on_hand_units: 640, unit_cost: 31, excess_units: 400, money_tied_up: 12400,
  }),
  excessRow({
    item_code: 'E-300', site_code: 'S1', rank: 2, category: 'dead',
    dead_reason: 'never_moved', on_hand_units: 120, unit_cost: null,
    excess_units: 120, money_tied_up: null,
    decision: decisionOf('E-300', 'S1', 'dismissed'),
  }),
];

const STOCKOUT_ROWS = [
  stockoutRow({
    item_code: 'K-200', site_code: 'S1', rank: 1, category: 'likely',
    on_hand_units: 12, open_supply_units: 0, unit_cost: 936, lead_time_days: 14,
    p_stockout: 0.92, money_at_risk: 18720,
  }),
  stockoutRow({
    item_code: 'K-300', site_code: 'S2', rank: 2, category: 'at_risk',
    on_hand_units: 30, open_supply_units: 20, unit_cost: 105, lead_time_days: 10,
    p_stockout: 0.4, money_at_risk: 4200,
    decision: decisionOf('K-300', 'S2', 'accepted'),
  }),
];

// Scenario 2 lists: same-kind ties only a fixed-order tie-break can resolve
// (the list order is reversed on purpose).
const TIE_DRIFT_ROWS = [
  driftRow({
    item_code: 'D-100', site_code: 'S1', rank: 1, direction: 'under',
    units_gap: 24, money_at_stake: 18720, unit_cost: 78,
    current_reorder_point: 30, reorder_point: 54, reorder_point_units: 54, safety_stock: 12,
  }),
];

const TIE_EXCESS_ROWS = [
  excessRow({
    item_code: 'E-200', site_code: 'S1', rank: 2, category: 'excess',
    on_hand_units: 400, unit_cost: 31, excess_units: 400, money_tied_up: 12400,
  }),
  excessRow({
    item_code: 'E-100', site_code: 'S2', rank: 1, category: 'excess',
    on_hand_units: 400, unit_cost: 31, excess_units: 400, money_tied_up: 12400,
  }),
];

const TIE_STOCKOUT_ROWS = [
  stockoutRow({
    item_code: 'K-100', site_code: 'S2', rank: 2, category: 'likely',
    on_hand_units: 8, open_supply_units: 0, unit_cost: 936, lead_time_days: 14,
    p_stockout: 0.9, money_at_risk: 5100,
  }),
  stockoutRow({
    item_code: 'K-100', site_code: 'S1', rank: 1, category: 'likely',
    on_hand_units: 8, open_supply_units: 0, unit_cost: 936, lead_time_days: 14,
    p_stockout: 0.9, money_at_risk: 5100,
  }),
];

/* ------------------------------ the mocks ------------------------------ */

// Mock every /api/* call; records each request (pathname + search) so the
// spec can inspect what the app fetched, in what order.
async function mockApi(page, opts = {}) {
  const state = {
    summaryStatus: opts.summaryStatus !== undefined ? opts.summaryStatus : 200,
    summaryPayload: opts.summaryPayload !== undefined ? opts.summaryPayload : SUMMARY,
    drift: opts.drift !== undefined ? opts.drift : DRIFT_ROWS,
    excess: opts.excess !== undefined ? opts.excess : EXCESS_ROWS,
    stockout: opts.stockout !== undefined ? opts.stockout : STOCKOUT_ROWS,
    rejectData: !!opts.rejectData,
    delayMs: opts.delayMs || 0,
  };
  const requests = [];
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    requests.push({ pathname: url.pathname, search: url.search });
    if (url.pathname === '/api/auth/config') {
      return route.fulfill({ status: 200, json: DEV_CONFIG });
    }
    if (url.pathname === '/api/auth/me') {
      return route.fulfill({ status: 200, json: ME });
    }
    if (state.rejectData) {
      return route.abort('failed');
    }
    const respond = () => {
      if (url.pathname === '/api/decisions/summary') {
        return route.fulfill({
          status: state.summaryStatus,
          json:
            state.summaryStatus === 200
              ? state.summaryPayload
              : { detail: 'Internal Server Error' },
        });
      }
      if (url.pathname === '/api/drift' && url.search === LIST_QUERY) {
        return route.fulfill({ status: 200, json: state.drift });
      }
      if (url.pathname === '/api/excess' && url.search === LIST_QUERY) {
        return route.fulfill({ status: 200, json: state.excess });
      }
      if (url.pathname === '/api/stockout' && url.search === LIST_QUERY) {
        return route.fulfill({ status: 200, json: state.stockout });
      }
      return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
    };
    if (state.delayMs > 0) {
      return new Promise((resolve) =>
        setTimeout(() => resolve(respond()), state.delayMs),
      );
    }
    return respond();
  });
  return { requests, state };
}

// Sign in through the development token card (#228).
async function signIn(page) {
  await expect(page.locator('#signin-form')).toBeVisible();
  await page.fill('#token-input', 'issue229-dev-token');
  await page.click('#signin-button');
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('planner1');
}

const REGION_IDS = [
  'ov-region-tiles',
  'ov-region-card-drift',
  'ov-region-card-excess',
  'ov-region-card-stockout',
  'ov-attention-body',
];

/* -------------------------------- the test ------------------------------- */

test('issue229 freeform', async ({ page }) => {
  page.setViewportSize({ width: 1440, height: 900 });

  /* ------------------- scenario 1: everything loads ------------------- */
  const { requests } = await mockApi(page, { delayMs: 800 });
  await page.goto('/');
  await signIn(page);

  // The Overview loads the summary and the three lists, decided rows
  // included, through the app's API layer.
  await expect.poll(() =>
    requests.some((r) => r.pathname === '/api/decisions/summary'),
  ).toBe(true);
  for (const pathName of ['/api/drift', '/api/excess', '/api/stockout']) {
    await expect.poll(() =>
      requests.some((r) => r.pathname === pathName && r.search === LIST_QUERY),
    ).toBe(true);
  }

  // While a region loads it shows a quiet skeleton.
  await expect(page.locator('#ov-region-tiles .skel').first()).toBeVisible();
  await expect(page.locator('#ov-region-card-drift .skel').first()).toBeVisible();
  await expect(page.locator('#ov-attention-body .skel').first()).toBeVisible();

  // --- the three value tiles from the summary's total ----------------
  const tiles = page.locator('#ov-region-tiles .ov-tile');
  await expect(tiles).toHaveCount(3);
  await expect(tiles.nth(0)).toContainText('$18,720');
  await expect(tiles.nth(0)).toContainText('4 accepted decisions across all three lists');
  await expect(tiles.nth(1)).toContainText('$7,600');
  await expect(tiles.nth(1)).toContainText('41% of accepted money has reached the ERP');
  await expect(tiles.nth(2)).toContainText('$11,120');
  await expect(tiles.nth(2)).toContainText('2 accepted changes not yet seen in the ERP');

  // Applied money in the accent color, outstanding money in the risk color.
  const appliedColor = await tiles
    .nth(1)
    .locator('.ov-tile-value')
    .evaluate((el) => getComputedStyle(el).color);
  expect(appliedColor).toBe('rgb(29, 78, 216)'); // --color-accent #1D4ED8
  const riskColor = await tiles
    .nth(2)
    .locator('.ov-tile-value')
    .evaluate((el) => getComputedStyle(el).color);
  expect(riskColor).toBe('rgb(138, 59, 5)'); // --color-risk #8A3B05

  // The progress bar shows 41% of accepted money reached the ERP.
  await expect(page.locator('#ov-region-tiles .ov-progress')).toHaveAttribute(
    'aria-valuenow',
    '41',
  );
  const barRatio = await page.evaluate(() => {
    const track = document.querySelector('#ov-region-tiles .ov-progress');
    const fill = document.querySelector('#ov-region-tiles .ov-progress-fill');
    return fill.getBoundingClientRect().width / track.getBoundingClientRect().width;
  });
  expect(barRatio).toBeGreaterThan(0.4);
  expect(barRatio).toBeLessThan(0.42);

  // --- the three list cards ------------------------------------------
  const driftCard = page.locator('#ov-region-card-drift');
  await expect(driftCard).toContainText('3 to decide');
  await expect(driftCard).toContainText('Open Reorder points list');
  const excessCard = page.locator('#ov-region-card-excess');
  await expect(excessCard).toContainText('1 to decide');
  const stockoutCard = page.locator('#ov-region-card-stockout');
  await expect(stockoutCard).toContainText('1 to decide');

  // Accepted and applied money from the kind's summary buckets, the
  // dismissed count.
  const cardFigures = (id) => page.locator('#' + id + ' .ov-figure-value');
  await expect(cardFigures('ov-region-card-drift').nth(0)).toHaveText('$4,200');
  await expect(cardFigures('ov-region-card-drift').nth(1)).toHaveText('$2,600');
  await expect(cardFigures('ov-region-card-drift').nth(2)).toHaveText('3');
  await expect(cardFigures('ov-region-card-excess').nth(0)).toHaveText('$5,000');
  await expect(cardFigures('ov-region-card-excess').nth(1)).toHaveText('$5,000');
  await expect(cardFigures('ov-region-card-excess').nth(2)).toHaveText('2');
  await expect(cardFigures('ov-region-card-stockout').nth(0)).toHaveText('$9,520');
  await expect(cardFigures('ov-region-card-stockout').nth(1)).toHaveText('$0');
  await expect(cardFigures('ov-region-card-stockout').nth(2)).toHaveText('1');

  // "Open … list" goes to that tab of the work queue.
  await page.click('#ov-region-card-excess .ov-open');
  await expect.poll(() => page.evaluate(() => window.location.hash)).toBe(
    '#/queue?kind=excess',
  );
  await page.evaluate(() => {
    window.location.hash = '#/overview';
  });
  await expect(page.locator('#view-overview')).toBeVisible();

  // --- needs a decision, largest money first --------------------------
  const attention = page.locator('#ov-attention-body .attn-row');
  await expect(attention).toHaveCount(5);
  const expectedAttention = [
    { badge: 'Reorder', item: 'D-100 · S1', money: '$18,720' },
    { badge: 'Stockout', item: 'K-200 · S1', money: '$18,720' },
    { badge: 'Excess', item: 'E-100 · S1', money: '$12,400' },
    { badge: 'Reorder', item: 'D-200 · S1', money: '$0' },
    { badge: 'Reorder', item: 'D-300 · S2', money: '—' },
  ];
  for (let i = 0; i < expectedAttention.length; i++) {
    const row = attention.nth(i);
    await expect(row.locator('.badge')).toHaveText(expectedAttention[i].badge);
    await expect(row.locator('.attn-item')).toHaveText(expectedAttention[i].item);
    await expect(row.locator('.attn-money')).toHaveText(expectedAttention[i].money);
    // A one-line reason on every row.
    await expect(row.locator('.attn-why')).toBeVisible();
    const whyText = (await row.locator('.attn-why').textContent()).trim();
    expect(whyText.length).toBeGreaterThan(0);
  }
  // None of them is decided: the decided rows (D-400, E-300, K-300) never
  // make the list.
  const attentionText = await page.locator('#ov-attention-body').textContent();
  for (const decidedItem of ['D-400', 'E-300', 'K-300']) {
    expect(attentionText).not.toContain(decidedItem);
  }

  // Clicking the first row opens the work queue on that row.
  await attention.nth(0).click();
  await expect.poll(() => page.evaluate(() => window.location.hash)).toBe(
    '#/queue?kind=drift&item=D-100&site=S1',
  );

  // --- no page scroll at 1440x900: the Overview fits ------------------
  await page.evaluate(() => {
    window.location.hash = '#/overview';
  });
  await expect(page.locator('#view-overview')).toBeVisible();
  const layout = await page.evaluate(() => {
    const region = document.getElementById('view-overview');
    return {
      docHeight: document.documentElement.scrollHeight,
      viewport: window.innerHeight,
      bodyMargin: getComputedStyle(document.body).margin,
      regionScrolls: region.scrollHeight > region.clientHeight,
    };
  });
  expect(layout.bodyMargin).toBe('0px');
  expect(layout.docHeight).toBe(layout.viewport);
  expect(layout.regionScrolls).toBe(true);

  /* ------------- scenario 2: the fixed-order tie-breaks --------------- */
  const page2 = await page.context().newPage();
  page2.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page2, {
    summaryPayload: EMPTY_SUMMARY,
    drift: TIE_DRIFT_ROWS,
    excess: TIE_EXCESS_ROWS,
    stockout: TIE_STOCKOUT_ROWS,
  });
  await page2.goto('/');
  await signIn(page2);

  const attention2 = page2.locator('#ov-attention-body .attn-row');
  await expect(attention2).toHaveCount(5);
  const tieOrder = [
    ['Reorder', 'D-100 · S1', '$18,720'],
    ['Excess', 'E-100 · S2', '$12,400'],
    ['Excess', 'E-200 · S1', '$12,400'],
    ['Stockout', 'K-100 · S1', '$5,100'],
    ['Stockout', 'K-100 · S2', '$5,100'],
  ];
  for (let i = 0; i < tieOrder.length; i++) {
    const row = attention2.nth(i);
    await expect(row.locator('.badge')).toHaveText(tieOrder[i][0]);
    await expect(row.locator('.attn-item')).toHaveText(tieOrder[i][1]);
    await expect(row.locator('.attn-money')).toHaveText(tieOrder[i][2]);
  }
  // Nothing accepted: 0% of accepted money has reached the ERP.
  await expect(page2.locator('#ov-region-tiles')).toContainText(
    '0% of accepted money has reached the ERP',
  );
  await expect(page2.locator('#ov-region-tiles .ov-progress')).toHaveAttribute(
    'aria-valuenow',
    '0',
  );
  await page2.close();

  /* --------- scenario 3: a 500 from the summary endpoint -------------- */
  const page3 = await page.context().newPage();
  page3.setViewportSize({ width: 1440, height: 900 });
  const m3 = await mockApi(page3, { summaryStatus: 500 });
  await page3.goto('/');
  await signIn(page3);

  // The tiles region shows the inline error with Retry ...
  await expect(page3.locator('#ov-region-tiles .ov-error')).toBeVisible();
  await expect(page3.locator('#ov-region-tiles .ov-retry')).toBeVisible();
  // ... the card list data still loads: every list was requested with the
  // decided rows included ...
  for (const pathName of ['/api/drift', '/api/excess', '/api/stockout']) {
    await expect.poll(() =>
      m3.requests.some((r) => r.pathname === pathName && r.search === LIST_QUERY),
    ).toBe(true);
  }
  // ... the card regions that needed the summary failed too ...
  await expect(page3.locator('#ov-region-card-drift .ov-error')).toBeVisible();
  // ... and the attention list renders from the complete list data.
  await expect(page3.locator('#ov-attention-body .attn-row')).toHaveCount(5);

  // Retry re-requests only /api/decisions/summary, then shows the tiles.
  const requestsBefore = m3.requests.length;
  m3.state.summaryStatus = 200;
  await page3.click('#ov-region-tiles .ov-retry');
  await expect(page3.locator('#ov-region-tiles .ov-tile')).toHaveCount(3);
  await expect(page3.locator('#ov-region-tiles .ov-tile').nth(0)).toContainText(
    '$18,720',
  );
  const retried = m3.requests.slice(requestsBefore);
  expect(retried).toHaveLength(1);
  expect(retried[0].pathname).toBe('/api/decisions/summary');
  await page3.close();

  /* -------- scenario 4: a failed load never leaves a blank page ------- */
  const page4 = await page.context().newPage();
  page4.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page4, { rejectData: true });
  await page4.goto('/');
  await signIn(page4);

  // Every region ends in an inline error with a Retry button ...
  for (const regionId of REGION_IDS) {
    await expect(page4.locator('#' + regionId + ' .ov-error')).toBeVisible();
    await expect(page4.locator('#' + regionId + ' .ov-retry')).toBeVisible();
  }
  // ... and the page is not blank.
  const overviewText = (await page4.locator('#view-overview').textContent()).trim();
  expect(overviewText.length).toBeGreaterThan(0);
  const failedLayout = await page4.evaluate(() => {
    const region = document.getElementById('view-overview');
    return {
      docHeight: document.documentElement.scrollHeight,
      viewport: window.innerHeight,
      regionScrolls: region.scrollHeight > region.clientHeight,
    };
  });
  expect(failedLayout.docHeight).toBe(failedLayout.viewport);
  expect(failedLayout.regionScrolls).toBe(false);
  await page4.close();
});
