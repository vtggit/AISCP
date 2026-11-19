// Proving spec for AIERP issue #1205 — the "Order plan" screen
// (#/orders): the admin's top-up choosing and releasing with them.
//
// For an admin — the same shared Auth identity's role the release
// control reads — each top-up row of a block's suggested-top-ups
// table starts with a checkbox, labelled for assistive technology
// "Add <item_code> at <site_code> to this order", unticked at first;
// a non-admin's table carries no checkbox and no extra column,
// exactly as before. The ticked rows are kept per block, by the
// block's supplier_code and each row's item_code and site_code,
// beside the block's release state: they survive a full re-render
// (a view re-activation re-reads the answers the screen already
// holds, which move no suggestions), and they clear when a new plan
// answer or a new top-ups answer lands — the suggestions may have
// changed — and on sign-out. While at least one row of a block is
// ticked, a line follows the outcome line reading "With the chosen
// top-ups: <total>", the block's total_value plus the ticked rows'
// value in dollars and cents, continuing " \u2014 meets the
// minimum." at or above the block's minimum, else " \u2014 still
// short by <minimum minus total>." — and with nothing ticked the
// line is absent.
//
// Releasing with them: the "Release to Acumatica" preview adds the
// block's ticked rows to the POST /api/supply-plan/release body as
// "top_ups" — a list of {"supplier_code", "item_code",
// "site_code"} in the table's order; with no row ticked the body is
// byte-for-byte what it is today, with no top_ups key. "Confirm
// release" resends the same body with "confirm": true. The preview
// panel shows the returned groups' lines and each unreleasable
// entry's item and reason exactly as before, so a kept top-up
// appears among the group's lines and a top-up the suggestions no
// longer hold reads "not a suggested top-up". The plan read, the
// supplier-risk read and the top-ups read, with their order and
// their counts, the CSV download, the transfer suggestions and
// their release, and every other request are unchanged, and the
// screen sends no request it does not send today apart from the
// body's field.
//
// The gate serves the static app with no backend, so every /api/*
// call is mocked with one page.route('**/api/**', handler), the
// handler defined before it is registered with page.route, the way
// app/tests/issue1167.spec.js does it. The mocked supply-plan
// answer (horizon 7) has one block: the acumatica supplier S-A
// ("Alpha Supply") with total "1800.00", minimum "2500.00" and
// shortfall "700.00", one line. The mocked top-ups answer has
// lookahead_days 30 and gives S-A two top-ups: T1 at 159 (value
// "450.00") and T2 at 160 (value "300.00"). The mocked release
// answer keeps T1 among its group's lines (site 159, two lines,
// "2250.00") and lists T2 in unreleasable with the reason "not a
// suggested top-up".
//
// The ADMIN pass signs in through the development token card at
// 1440x900: the two unticked checkboxes and no running line, ticking
// T1's running line "still short by $250.00", ticking T2's "meets
// the minimum" line, the preview's one POST with the body's
// top_ups in the table's order and today's other fields, the
// preview panel's group lines and the unreleasable reason, the
// confirm resending the same body with "confirm": true, the ticks
// surviving a full re-render (leaving the view and coming back), a
// refresh that lands a new plan answer clearing them and the running
// line, the unticked release body with no top_ups key, and — at
// 390x844 — the checkbox and the running line inside the block with
// no horizontal scroll of the page. The PLANNER pass (role 'user',
// the identity requirement) runs as a second pass after a full
// reload, booted the production way — the token in the URL hash as
// #access_token=..., /api/auth/config and /api/auth/me mocked for
// that user, the fragment inert through the PKCE round-trip — and
// sees the top-ups table with no checkbox and no running line,
// while the page renders and the admin-only Release control stays
// absent. The admin pass sends no request but GETs and the release
// POSTs, and the planner pass sends none of the POSTs either.
//
// The test is TOP-LEVEL and its title is the full proof name
// ("issue1205 freeform"): the merge gate builds its junit nodeids
// from the Playwright test title, and a describe() block would join
// the titles with a "\u203a" the contract nodeid does not carry, so
// the proof would read as "not collected".

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


// The freeform pass runs several sub-scenarios in one test; give slow
// CI runners room without weakening the assertions.
test.setTimeout(90000);

// The development sign-in configuration the mock answers with.
const DEV_CONFIG = {
  authEnabled: true,
  authMode: 'development',
  issuer: 'http://idp.test/realms/r',
  clientId: 'aierp-app',
  audience: 'aierp-app',
  jwksUrl: 'http://idp.test/realms/r/protocol/openid-connect/certs',
  algorithms: ['RS256'],
};

// The production sign-in configuration the mock answers with on the
// planner pass: the PKCE round-trip signs the user in.
const PROD_CONFIG = {
  authEnabled: true,
  authMode: 'production',
  issuer: 'http://idp.test/realms/r',
  clientId: 'aierp-app',
  audience: 'aierp-app',
  jwksUrl: 'http://idp.test/realms/r/protocol/openid-connect/certs',
  algorithms: ['RS256'],
};

// The admin: the sign-in the development token card lands.
const ADMIN_ME = {
  authenticated: true,
  user: { sub: 'u1', username: 'admin1', roles: ['admin'], groups: [] },
};

// The planner: the signed-in non-admin user of the second pass.
const PLANNER_ME = {
  authenticated: true,
  user: { sub: 'u2', username: 'planner1', roles: ['user'], groups: [] },
};

// The Overview view activates on sign-in (the default view); its
// answers stay minimal so the page renders clean while the Order
// plan screen is under test.
const SUMMARY = {
  total: {
    accepted: { money: 0, count: 0 },
    applied: { money: 0, count: 0 },
    outstanding: { money: 0, count: 0 },
  },
  drift: {},
  excess: {},
  stockout: {},
};

// The Overview's Released orders card answer: nothing released.
const RELEASES_EMPTY = {
  as_of: '2026-11-08',
  orders: [],
  counts: { awaiting_approval: 0, late: 0, partly_received: 0, received: 0 },
};

// The risk answer: no supplier rated — the blocks' heads carry no
// risk badge, and the short badge stands alone.
const RISK_ANSWER = {
  as_of: '2026-11-08',
  suppliers: [],
};

// One plan line: the block's single line.
function planLine(itemCode, siteCode, extra) {
  return Object.assign(
    {
      item_code: itemCode,
      site_code: siteCode,
      position_units: 10,
      reorder_point_units: 30,
      daily_demand: 2,
      due_in_days: 0,
      order_by: '2026-11-10',
      lead_time_days: 5,
      arrives_on: '2026-11-15',
      raw_quantity: 1,
      quantity: 1,
      adjustments: [],
      short_of_need: false,
      terms: null,
      unit_cost: null,
      value: null,
      arithmetic: 'raw 1',
      supplier_code: null,
      source_system: null,
    },
    extra || {},
  );
}

// The mocked supply-plan answer: the one acumatica block S-A
// ("Alpha Supply") — total "1800.00", minimum "2500.00", shortfall
// "700.00" — with one line. opts.asOf names the refresh answer's
// distinct as-of, the new plan answer the clear check rides on.
function supplyAnswer(horizonDays, opts) {
  const asOf = (opts && opts.asOf) || '2026-11-08';
  return {
    as_of: asOf,
    horizon_days: horizonDays,
    line_count: 1,
    total_value: 1800,
    suppliers: [
      {
        supplier_code: 'S-A',
        source_system: 'acumatica',
        vendor_code: 'V-A',
        name: 'Alpha Supply',
        line_count: 1,
        total_value: '1800.00',
        minimum: '2500.00',
        shortfall: '700.00',
        lines: [
          planLine('X', '159', {
            quantity: 180,
            unit_cost: 10,
            value: '1800.00',
            arithmetic: 'raw 180, at the minimum of 100 and the lot of 10 holds',
            supplier_code: 'S-A',
            source_system: 'acumatica',
          }),
        ],
      },
    ],
  };
}

// The mocked top-ups answer: lookahead_days 30, and S-A's two
// top-ups — T1 at 159 (value "450.00") and T2 at 160 (value
// "300.00") — in the answer's order, reaching the minimum.
function topUpsAnswer() {
  return {
    as_of: '2026-11-08',
    horizon_days: 7,
    lookahead_days: 30,
    suppliers: [
      {
        supplier_code: 'S-A',
        source_system: 'acumatica',
        reaches_minimum: true,
        top_ups: [
          {
            item_code: 'T1',
            site_code: '159',
            due_in_days: 10,
            order_by: '2026-10-10',
            quantity: 3,
            value: '450.00',
            holding_cost: '3.11',
          },
          {
            item_code: 'T2',
            site_code: '160',
            due_in_days: 15,
            order_by: '2026-10-15',
            quantity: 2,
            value: '300.00',
            holding_cost: '2.07',
          },
        ],
      },
    ],
  };
}

// The mocked release answer: the kept top-up T1 among the group's
// lines (site 159, two lines, "2250.00") and T2 — the top-up the
// suggestions no longer hold — in unreleasable with its reason.
function releaseAnswer() {
  return {
    month: '2026-10',
    groups: [
      {
        site_code: '159',
        supplier_code: 'S-A',
        origin: 'AIERP order plan 2026-11-08 site 159 vendor V-A',
        lines: [
          { item_code: 'T1', quantity: 3, unit_cost: '150.00', value: '450.00' },
          { item_code: 'X', quantity: 180, unit_cost: '10.00', value: '1800.00' },
        ],
        value: '2250.00',
        outcome: 'created',
        warehouse_code: null,
        vendor_code: 'V-A',
        order_nbr: 'PO-8801',
        error: null,
      },
    ],
    unreleasable: [
      { item_code: 'T2', site_code: '160', reason: 'not a suggested top-up' },
    ],
    total_value: '2250.00',
    acumatica_available: true,
  };
}

// The body the release sends today (no top_ups key): the screen's
// horizon and the block's supplier code, nothing else.
const RELEASE_BODY_NO_TOP_UPS = { horizon_days: 7, supplier_codes: ['S-A'] };

// The GET paths the app has always sent on the routes this test
// walks: the sign-in reads, the shell's feed status, the Overview's
// card reads (the default view the sign-in lands on, and the view
// the re-render check passes through) and the Order plan's own
// reads — the transfers read is on-demand, and the mocked plan
// answer carries no transferable line. The request ledger checks
// every GET against this set, so a request the app does not send
// today — any GET to any other path — fails the proof.
const KNOWN_GET_PATHS = [
  '/api/abc',
  '/api/aggregate/releases',
  '/api/auth/config',
  '/api/auth/me',
  '/api/decisions/summary',
  '/api/drift',
  '/api/excess',
  '/api/exceptions',
  '/api/feed/status',
  '/api/stockout',
  '/api/stockout/proposals',
  '/api/excess/proposals',
  '/api/drift/decisions/acceptance',
  '/api/exceptions/proposals',
  '/api/forecast/overrides/proposals',
  '/api/supplier-risk',
  '/api/supply-plan',
  '/api/supply-plan/summary',
  '/api/supply-plan/top-ups',
  '/api/supply-plan/transfers',
];

// Mock every /api/* call the app makes with ONE route handler,
// defined before it is registered with page.route. The handler reads
// ctrl on every request, so the test can steer the plan answer
// (ctrl.supplyAnswer, a builder taking the requested horizon), the
// top-ups answer (ctrl.topUpsAnswer, either a payload or a
// { status, json } failure) and the release answer (ctrl.releaseAnswer),
// and steer the sign-in (ctrl.config / ctrl.me). Every supply-plan
// request is counted in ctrl.supplyGets, every risk request in
// ctrl.riskGets and every top-ups request in ctrl.topUpsGets, with
// each top-ups request's query string in ctrl.topUpsQueries; each
// release POST's method, raw body and parsed body land in
// ctrl.releasePosts, each request's method and path in
// ctrl.requests for the request-ledger check, and the app's origin
// on ctrl.origin for the PKCE redirect mock.
async function mockOrdersApi(page) {
  const ctrl = {
    config: DEV_CONFIG,
    me: ADMIN_ME,
    supplyAnswer: supplyAnswer,
    topUpsAnswer: null,
    releaseAnswer: null,
    origin: null,
    meRequests: [],
    supplyGets: 0,
    riskGets: 0,
    topUpsGets: 0,
    topUpsQueries: [],
    releasePosts: [],
    requests: [],
  };

  const handler = (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (ctrl.origin === null) {
      ctrl.origin = url.origin;
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
    if (path === '/api/supply-plan') {
      ctrl.supplyGets += 1;
      const horizonDays = parseInt(url.searchParams.get('horizon_days') || '7', 10);
      return route.fulfill({
        status: 200,
        json: ctrl.supplyAnswer(isFinite(horizonDays) ? horizonDays : 7),
      });
    }
    if (path === '/api/supplier-risk') {
      ctrl.riskGets += 1;
      return route.fulfill({ status: 200, json: RISK_ANSWER });
    }
    if (path === '/api/supply-plan/top-ups') {
      ctrl.topUpsGets += 1;
      ctrl.topUpsQueries.push(url.search);
      if (ctrl.topUpsAnswer !== null) {
        return route.fulfill(ctrl.topUpsAnswer);
      }
      return route.fulfill({ status: 200, json: topUpsAnswer() });
    }
    if (path === '/api/supply-plan/release') {
      const raw = request.postData();
      let parsed = null;
      try {
        parsed = JSON.parse(raw);
      } catch (error) {
        parsed = raw;
      }
      ctrl.releasePosts.push({ method: request.method(), raw, body: parsed });
      return route.fulfill({ status: 200, json: ctrl.releaseAnswer || releaseAnswer() });
    }
    if (path === '/api/aggregate/releases') {
      return route.fulfill({ status: 200, json: RELEASES_EMPTY });
    }
    if (path === '/api/decisions/summary') {
      return route.fulfill({ status: 200, json: SUMMARY });
    }
    if (path === '/api/drift' || path === '/api/excess' || path === '/api/stockout') {
      return route.fulfill({ status: 200, json: [] });
    }
    if (path === '/api/feed/status') {
      return route.fulfill({ status: 200, json: { synced_at: null, data_through: null } });
    }
    return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
  };

  await page.route('**/api/**', handler);
  return { ctrl, handler };
}

test('issue1205 freeform', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  // --- admin pass: development sign-in ---------------------------------
  page.setViewportSize({ width: 1440, height: 900 });
  const { ctrl } = await mockOrdersApi(page);

  const summary = page.locator('[data-testid="orders-summary"]');
  const groups = page.locator('[data-testid="orders-supplier"]');
  const refresh = page.locator('[data-testid="orders-refresh"]');
  const topUpRows = page.locator('[data-testid="orders-top-up"]');
  const chooseBoxes = page.locator('[data-testid="orders-top-up-choose"]');
  const runningLine = page.locator('[data-testid="orders-top-ups-chosen"]');
  const releasePanel = page.locator('[data-testid="orders-release-panel"]');

  await page.goto('/');

  // No plan, risk or top-ups read before the sign-in (Auth.bootstrap)
  // completes.
  await expect(page.locator('#signin-form')).toBeVisible();
  expect(ctrl.supplyGets).toBe(0);
  expect(ctrl.riskGets).toBe(0);
  expect(ctrl.topUpsGets).toBe(0);

  // Sign in through the development token card as the admin.
  await page.fill('#token-input', 'dev-admin-token');
  await page.click('#signin-button');
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('admin1');
  expect(pageErrors).toEqual([]);

  // --- activation: the plan read, the risk read, the top-ups read ------
  await page.click('.nav-btn[data-view="orders"]');
  await expect(page).toHaveURL(/#\/orders$/);
  await expect(page.locator('#view-orders')).toBeVisible();

  // The load of the screen sends exactly one supply-plan read, one
  // risk read and — because the plan answer carries a short block —
  // exactly one top-ups read, the top-ups read carrying the horizon
  // the plan read asked for.
  await expect.poll(() => ctrl.supplyGets).toBe(1);
  await expect.poll(() => ctrl.riskGets).toBe(1);
  await expect.poll(() => ctrl.topUpsGets).toBe(1);
  expect(ctrl.topUpsQueries[0]).toBe('?horizon_days=7');

  // The plan answer settles: the one block, its totals, its minimum
  // line and its short badge.
  await expect(summary).toHaveText('1 line \u00b7 $1,800 across 1 suppliers');
  await expect(groups).toHaveCount(1);
  await expect(groups.nth(0).locator('h2')).toHaveText('Alpha Supply');
  await expect(groups.nth(0).locator('[data-testid="orders-supplier-totals"]')).toHaveText(
    '1 line \u00b7 $1,800',
  );
  await expect(
    groups.nth(0).locator('[data-testid="orders-supplier-minimum"]'),
  ).toHaveText('Supplier minimum $2,500.00');
  await expect(
    groups.nth(0).locator('[data-testid="orders-supplier-short"]'),
  ).toHaveText('Below minimum by $700.00');
  expect(pageErrors).toEqual([]);

  // The top-ups answer settles: the two rows, in the answer's order,
  // with the outcome line.
  await expect(topUpRows).toHaveCount(2);
  await expect(topUpRows.nth(0).locator('td')).toHaveCount(6);
  await expect(topUpRows.nth(0).locator('.op-cell-pair')).toHaveText('T1 / 159');
  await expect(topUpRows.nth(0).locator('.op-cell-money')).toHaveText(['$450.00', '$3.11']);
  await expect(topUpRows.nth(1).locator('.op-cell-pair')).toHaveText('T2 / 160');
  await expect(topUpRows.nth(1).locator('.op-cell-money')).toHaveText(['$300.00', '$2.07']);
  await expect(
    groups.nth(0).locator('[data-testid="orders-top-ups-outcome"]'),
  ).toHaveText('Adding these reaches the minimum.');
  expect(pageErrors).toEqual([]);

  // --- the choosing: unticked checkboxes, no running line --------------
  // Each row starts with its checkbox, at the head of the row's
  // first cell, labelled for assistive technology, unticked at
  // first — and with nothing ticked the running line is absent.
  await expect(groups.nth(0).locator('[data-testid="orders-top-ups-lines"] thead th')).toHaveCount(6);
  await expect(chooseBoxes).toHaveCount(2);
  await expect(chooseBoxes.nth(0)).not.toBeChecked();
  await expect(chooseBoxes.nth(1)).not.toBeChecked();
  await expect(chooseBoxes.nth(0)).toHaveAttribute(
    'aria-label',
    'Add T1 at 159 to this order',
  );
  await expect(chooseBoxes.nth(1)).toHaveAttribute(
    'aria-label',
    'Add T2 at 160 to this order',
  );
  await expect(runningLine).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // Ticking T1: the block's "1800.00" plus T1's "450.00" stands at
  // $2,250.00, still short of the "2500.00" minimum by $250.00.
  await chooseBoxes.nth(0).check();
  await expect(chooseBoxes.nth(0)).toBeChecked();
  await expect(chooseBoxes.nth(1)).not.toBeChecked();
  await expect(runningLine).toHaveText(
    'With the chosen top-ups: $2,250.00 \u2014 still short by $250.00.',
  );
  expect(pageErrors).toEqual([]);

  // Ticking T2 as well: $2,550.00 stands at or above the minimum.
  await chooseBoxes.nth(1).check();
  await expect(runningLine).toHaveText(
    'With the chosen top-ups: $2,550.00 \u2014 meets the minimum.',
  );
  expect(pageErrors).toEqual([]);

  // Unticking T1 again: the line follows the ticks.
  await chooseBoxes.nth(0).uncheck();
  await expect(runningLine).toHaveText(
    'With the chosen top-ups: $2,100.00 \u2014 still short by $400.00.',
  );
  // ... and ticking it back for the release.
  await chooseBoxes.nth(0).check();
  await expect(runningLine).toHaveText(
    'With the chosen top-ups: $2,550.00 \u2014 meets the minimum.',
  );
  expect(pageErrors).toEqual([]);

  // --- the release: the preview body with the ticked rows --------------
  // "Release to Acumatica" sends one POST to /api/supply-plan/release:
  // the body's top_ups is the block's ticked rows in the table's
  // order, and its other fields are the ones the release sends today.
  await page.click('[data-testid="orders-release"]');
  await expect.poll(() => ctrl.releasePosts.length).toBe(1);
  expect(ctrl.releasePosts[0].method).toBe('POST');
  expect(ctrl.releasePosts[0].body).toEqual({
    horizon_days: 7,
    supplier_codes: ['S-A'],
    top_ups: [
      { supplier_code: 'S-A', item_code: 'T1', site_code: '159' },
      { supplier_code: 'S-A', item_code: 'T2', site_code: '160' },
    ],
  });

  // The preview panel shows the returned groups' lines exactly as
  // today — the kept top-up T1 among them — ...
  await expect(releasePanel).toBeVisible();
  await expect(releasePanel.locator('.op-release-group')).toHaveCount(1);
  await expect(releasePanel.locator('.op-release-group-site')).toHaveText('159');
  await expect(releasePanel.locator('.op-release-group-lines')).toHaveText('2 lines');
  await expect(releasePanel.locator('.op-release-group-value')).toHaveText('$2,250');
  // ... and each unreleasable entry its item and its reason, so the
  // top-up the suggestions no longer hold reads "not a suggested
  // top-up".
  await expect(releasePanel.locator('[data-testid="orders-release-unreleasable"] li')).toHaveText([
    'T2 \u2014 not a suggested top-up',
  ]);
  expect(pageErrors).toEqual([]);

  // "Confirm release" resends the same body with "confirm": true.
  await page.click('[data-testid="orders-release-confirm"]');
  await expect.poll(() => ctrl.releasePosts.length).toBe(2);
  expect(ctrl.releasePosts[1].method).toBe('POST');
  expect(ctrl.releasePosts[1].body).toEqual({
    horizon_days: 7,
    supplier_codes: ['S-A'],
    top_ups: [
      { supplier_code: 'S-A', item_code: 'T1', site_code: '159' },
      { supplier_code: 'S-A', item_code: 'T2', site_code: '160' },
    ],
    confirm: true,
  });
  const confirmWithoutFlag = Object.assign({}, ctrl.releasePosts[1].body);
  delete confirmWithoutFlag.confirm;
  expect(confirmWithoutFlag).toEqual(ctrl.releasePosts[0].body);
  // The confirmed panel shows each group's outcome, as before.
  await expect(releasePanel.locator('[data-testid="orders-release-outcome"]')).toHaveText(
    'Draft PO PO-8801 created on hold',
  );
  expect(pageErrors).toEqual([]);

  // --- a small screen: the checkbox and the line stay in the block -----
  // At 390x844 the top-ups table scrolls in its own region as before:
  // the checkbox and the running line stay inside the block, and the
  // page does not scroll sideways.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(topUpRows.first()).toBeVisible();
  await expect(runningLine).toBeVisible();
  const smallScreen = await groups.nth(0).evaluate((block) => {
    const blockRect = block.getBoundingClientRect();
    const boxes = Array.from(block.querySelectorAll('[data-testid="orders-top-up-choose"]'));
    const line = block.querySelector('[data-testid="orders-top-ups-chosen"]');
    const inside = (rect) =>
      rect.left >= blockRect.left - 0.5 && rect.right <= blockRect.right + 0.5;
    return {
      boxes: boxes.length,
      boxesInside:
        boxes.length === 2 &&
        boxes.every((box) => inside(box.getBoundingClientRect())),
      lineInside: line !== null && inside(line.getBoundingClientRect()),
      pageScrolls:
        document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
  });
  expect(smallScreen).toEqual({
    boxes: 2,
    boxesInside: true,
    lineInside: true,
    pageScrolls: false,
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  expect(pageErrors).toEqual([]);

  // --- kept: a full re-render keeps the ticks ---------------------------
  // Leaving the view and coming back is a full re-render: the re-read
  // lands the answers the screen already holds (no suggestions moved),
  // and both ticks survive it — the same plan, risk and top-ups reads
  // as today, no request the screen does not send.
  const supplyGetsBeforeNav = ctrl.supplyGets;
  await page.click('.nav-btn[data-view="overview"]');
  await expect(page).toHaveURL(/#\/overview$/);
  await page.click('.nav-btn[data-view="orders"]');
  await expect(page).toHaveURL(/#\/orders$/);
  await expect(page.locator('#view-orders')).toBeVisible();
  await expect.poll(() => ctrl.supplyGets).toBe(supplyGetsBeforeNav + 1);
  await expect.poll(() => ctrl.riskGets).toBe(2);
  await expect.poll(() => ctrl.topUpsGets).toBe(2);
  expect(ctrl.topUpsQueries[1]).toBe('?horizon_days=7');
  await expect(topUpRows).toHaveCount(2);
  await expect(chooseBoxes).toHaveCount(2);
  await expect(chooseBoxes.nth(0)).toBeChecked();
  await expect(chooseBoxes.nth(1)).toBeChecked();
  await expect(runningLine).toHaveText(
    'With the chosen top-ups: $2,550.00 \u2014 meets the minimum.',
  );
  expect(pageErrors).toEqual([]);

  // --- cleared: a refresh that lands a new plan answer -------------------
  // A new plan answer (a distinct as-of) may have moved the
  // suggestions: the ticks clear, and the running line goes.
  ctrl.supplyAnswer = (days) => supplyAnswer(days, { asOf: '2026-11-09' });
  await refresh.click();
  await expect.poll(() => ctrl.supplyGets).toBe(3);
  await expect.poll(() => ctrl.riskGets).toBe(3);
  await expect.poll(() => ctrl.topUpsGets).toBe(3);
  expect(ctrl.topUpsQueries[2]).toBe('?horizon_days=7');
  await expect(summary).toHaveText('1 line \u00b7 $1,800 across 1 suppliers');
  await expect(groups).toHaveCount(1);
  await expect(topUpRows).toHaveCount(2);
  await expect(chooseBoxes).toHaveCount(2);
  await expect(chooseBoxes.nth(0)).not.toBeChecked();
  await expect(chooseBoxes.nth(1)).not.toBeChecked();
  await expect(runningLine).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // --- none ticked: the body is byte-for-byte today's -------------------
  // With no row ticked the release body carries no top_ups key and
  // equals the body the release sends today.
  await page.click('[data-testid="orders-release"]');
  await expect.poll(() => ctrl.releasePosts.length).toBe(3);
  expect(ctrl.releasePosts[2].method).toBe('POST');
  expect(ctrl.releasePosts[2].body).toEqual(RELEASE_BODY_NO_TOP_UPS);
  expect(ctrl.releasePosts[2].body).not.toHaveProperty('top_ups');
  expect(ctrl.releasePosts[2].raw).toBe(
    JSON.stringify(RELEASE_BODY_NO_TOP_UPS),
  );
  // Cancel closes the panel and sends nothing.
  await page.click('[data-testid="orders-release-cancel"]');
  await expect(releasePanel).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // --- the request ledger: today's GETs and the release POSTs only ------
  // The screen sends no request it does not send today, apart from the
  // body's field: every GET is one of the reads it has always sent,
  // and the only POSTs are the three release calls.
  expect(ctrl.requests.length).toBeGreaterThan(0);
  for (const request of ctrl.requests) {
    if (request.method === 'GET') {
      expect(KNOWN_GET_PATHS, 'unexpected GET ' + request.path).toContain(request.path);
    } else {
      expect(request.method).toBe('POST');
      expect(request.path).toBe('/api/supply-plan/release');
    }
  }
  expect(ctrl.releasePosts.length).toBe(3);

  // --- planner pass: the production way, a non-admin --------------------
  // A full reload through a distinct path carrying the token hash, as
  // the identity requirement calls for: the sign-in configuration
  // answers production, the fragment stays inert through the PKCE
  // round-trip, and /api/auth/me signs the planner in.
  ctrl.config = PROD_CONFIG;
  ctrl.me = PLANNER_ME;
  ctrl.supplyAnswer = supplyAnswer;
  ctrl.topUpsAnswer = null;
  // The admin pass's session token persists in the tab's
  // sessionStorage: clear it so the reload really boots from the
  // fragment, not from the stored session.
  await page.evaluate(() => {
    window.sessionStorage.clear();
  });
  await page.route(/protocol\/openid-connect\/token/, (route) => {
    return route.fulfill({
      status: 200,
      json: { access_token: 'pkce-planner-token', token_type: 'Bearer' },
    });
  });
  await page.route(/protocol\/openid-connect\/auth/, (route) => {
    const state = new URL(route.request().url()).searchParams.get('state') || '';
    const backToApp = ctrl.origin + '/?code=fake-pkce-code&state=' + encodeURIComponent(state);
    return route.fulfill({
      status: 200,
      contentType: 'text/html',
      body:
        '<!doctype html><html><body><script>window.location.replace(' +
        JSON.stringify(backToApp) +
        ');</script></body></html>',
    });
  });

  await page.goto('/index.html#access_token=planner-token-1205');

  // The PKCE round-trip completes and the shell renders for the
  // planner ...
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('planner1');
  // ... signed in through /api/auth/me with the IdP's token, the
  // fragment's token never read, stored or sent ...
  expect(await page.evaluate(() => window.sessionStorage.getItem('aierp_token'))).toBe(
    'pkce-planner-token',
  );
  expect(page.url()).not.toContain('access_token');
  const plannerMe = ctrl.meRequests[ctrl.meRequests.length - 1];
  expect(plannerMe.headers()['authorization']).toBe('Bearer pkce-planner-token');

  // The Order plan for the planner: one plan read and one top-ups
  // read again, at the default horizon ...
  await page.click('.nav-btn[data-view="orders"]');
  await expect(page).toHaveURL(/#\/orders$/);
  await expect(page.locator('#view-orders')).toBeVisible();
  await expect.poll(() => ctrl.supplyGets).toBe(4);
  await expect.poll(() => ctrl.topUpsGets).toBe(4);
  expect(ctrl.topUpsQueries[3]).toBe('?horizon_days=7');

  // ... and the same block, minimum line, short badge and rows show.
  await expect(summary).toHaveText('1 line \u00b7 $1,800 across 1 suppliers');
  await expect(groups).toHaveCount(1);
  await expect(groups.nth(0).locator('h2')).toHaveText('Alpha Supply');
  await expect(
    groups.nth(0).locator('[data-testid="orders-supplier-minimum"]'),
  ).toHaveText('Supplier minimum $2,500.00');
  await expect(
    groups.nth(0).locator('[data-testid="orders-supplier-short"]'),
  ).toHaveText('Below minimum by $700.00');
  await expect(topUpRows).toHaveCount(2);
  await expect(topUpRows.nth(0).locator('td')).toHaveText([
    'T1 / 159',
    '10 days',
    '2026-10-10',
    '3',
    '$450.00',
    '$3.11',
  ]);
  await expect(topUpRows.nth(1).locator('td')).toHaveText([
    'T2 / 160',
    '15 days',
    '2026-10-15',
    '2',
    '$300.00',
    '$2.07',
  ]);
  await expect(
    groups.nth(0).locator('[data-testid="orders-top-ups-outcome"]'),
  ).toHaveText('Adding these reaches the minimum.');
  expect(pageErrors).toEqual([]);

  // ... but with no checkbox and no extra column on the rows, and no
  // running line of any kind — the choosing is the admin's — while
  // the page renders and the admin-only Release control stays absent.
  await expect(
    groups.nth(0).locator('[data-testid="orders-top-ups-lines"] thead th'),
  ).toHaveText([
    'Item and Site',
    'Pulled forward',
    'Order by',
    'Quantity',
    'Value',
    'Holding cost',
  ]);
  await expect(chooseBoxes).toHaveCount(0);
  await expect(runningLine).toHaveCount(0);
  await expect(page.locator('[data-testid="orders-release"]')).toHaveCount(0);
  await expect(groups.nth(0).locator('[data-testid="orders-release-note"]')).toHaveCount(0);
  // The planner sends no release traffic: the ledger's POSTs are the
  // admin pass' three, untouched.
  expect(ctrl.releasePosts.length).toBe(3);
  expect(pageErrors).toEqual([]);
});
