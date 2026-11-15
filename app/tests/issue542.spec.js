// Proving spec for AIERP issue #542 — the "Release to ERP" panel
// ([data-testid="aggregate-release"]) of the Aggregate planning
// screen (#/aggregate): directly under the What to defer block, only
// in the "With planned orders" view, it offers the month select
// (the current month and the next two, YYYY-MM labels, defaulting to
// the current month) and a real "Preview release" button that POSTs
// /api/aggregate/release with {"month": <month>}; the answer renders
// the summary "<n> purchase orders, $<total_value> in total" (the
// total in whole dollars from its decimal string), the groups table
// (Warehouse, Vendor, Lines, Value, Status — "Will create" for
// would_create, "Already in ERP <order_nbr>" for exists, "Created
// <order_nbr>" for created, "Error: <error>" for error, empty when
// the outcome is null; the table scrolls within its own region) and
// the unreleasable note ("item_code · site_code" per entry, with its
// reason); an answer with acumatica_available false reads "The ERP
// connection is not configured; this is the plan only." While the
// answer has at least one would_create group an admin (role from
// GET /api/auth/me, like the other views) gets the "Release <k>
// purchase orders to the ERP" button (a real <button>, "order" when
// k is 1) that asks once more with an inline "Confirm release" /
// "Cancel" pair — nothing is sent until Confirm, which POSTs
// {"month": <month>, "confirm": true} and renders the answer the
// same way — and a non-admin sees "Only an admin can release."
// instead of the button. While a saved scenario is chosen the panel
// shows only "Releasing uses the baseline plan. Switch Scenario to
// Baseline to release." with no controls; changing the month clears
// the shown answer; a 409, 422, 502 or 503 shows the server's detail
// inline and any other failure "The release could not be completed.",
// both keeping the last shown table; while a request is out the
// panel's controls are disabled and a sequence guard renders only
// the newest answer.
//
// The gate serves the static app with no backend, so every /api/*
// call is mocked with page.route (every release request body is
// recorded). Proves, signed in through the development token card as
// an admin (/api/auth/me roles ["admin"]) at 1440x900:
//   * the planned view shows the panel with the three month options,
//     defaulting to the current month;
//   * the preview POSTs {"month": <current>} and renders the two
//     groups (would_create WHOLESALE / CONPERIPH, 2 lines,
//     "513.50"; exists RETAIL / FOODWESTER, 1 line, "80.00",
//     PO000777), one unreleasable item and total_value "593.50" as
//     "2 purchase orders, $594 in total", the statuses "Will create"
//     and "Already in ERP PO000777", the values "$514" and "$80" and
//     the unreleasable note;
//   * the confirm button reads "Release 1 purchase order to the
//     ERP"; clicking it shows Confirm and Cancel without sending,
//     and Cancel sends nothing;
//   * Confirm POSTs {"month": <current>, "confirm": true} and shows
//     "Created PO003100";
//   * a confirm answered 409 {"detail": "Acumatica writes are
//     disabled"} shows that text and keeps the table;
//   * with acumatica_available false the not-configured note shows;
//   * changing the month clears the shown answer;
//   * while a request is out the panel's controls are disabled;
//   * a hostile vendor code renders as text (never HTML);
//   * with a saved scenario chosen the panel shows the baseline-only
//     message and no controls;
//   * at 390x844 the page does not scroll sideways.
//
// The pass ends as a NON-ADMIN (role 'user') after a full reload:
// the token arrives in the URL hash as #access_token=...,
// /api/auth/config and /api/auth/me are mocked for that user, the
// IdP's auth and token endpoints carry the PKCE round-trip, no
// fetch goes out before the view is activated, and the screen then
// renders for the viewer — the panel included — where the preview
// works but the admin-only release control is absent and "Only an
// admin can release." shows instead of the button.
//
// The spec asserts nothing about the Spend vs budget or the What to
// defer blocks (their own specs, issue513 and issue529, prove them):
// its mocked spend answer has no month over budget, so the What to
// defer block is absent and no deferrals request is expected.
//
// The test is TOP-LEVEL and its title is the full proof name
// ("issue542 freeform"): the merge gate builds its junit nodeids
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
  user: { sub: 'u1', username: 'planner1', roles: ['admin'], groups: [] },
};

const VIEWER_ME = {
  authenticated: true,
  user: { sub: 'v1', username: 'viewer1', roles: ['user'], groups: [] },
};

// The projection answer: one group, one bucket — the grid only has
// to keep rendering while the release panel is under test.
const PROJECTION = [
  {
    group: { dimension: 'site', code: 'S1' },
    buckets: [
      {
        month: '2026-10',
        projected_demand_units: 100,
        projected_supply_units: 0,
        projected_on_hand_units: 400,
        projected_shortfall: false,
        projected_demand_value: 500,
        projected_supply_value: 0,
        planned_receipts_units: 100,
        planned_receipts_value: 500,
        planned_on_hand_units: 400,
        planned_shortfall: false,
      },
    ],
    item_sites_without_unit_cost: 0,
  },
];

// The spend answer with no month over budget: the What to defer
// block stays absent and no deferrals request ever goes out.
const SPEND_NO_OVER = {
  months: [
    {
      month: '2026-10',
      committed_spend: 1000,
      planned_spend: 1500,
      total_spend: 2500,
      budget: 5000,
      remaining: 2500,
      over_budget: false,
    },
    {
      month: '2026-11',
      committed_spend: 0,
      planned_spend: 800,
      total_spend: 800,
      budget: 9000,
      remaining: 8200,
      over_budget: false,
    },
  ],
  item_sites_without_unit_cost: 0,
};

const SCENARIOS = [
  {
    id: 'sc-peak',
    name: 'Peak +20%',
    service_level: 0.98,
    demand_multiplier: 1.2,
    lead_time_multiplier: null,
    created_at: '2026-10-01T09:00:00Z',
    updated_at: '2026-10-01T09:00:00Z',
  },
];

// The YYYY-MM of the current month shifted by ``offset`` months —
// the same rule the panel's month options use.
function monthLabel(offset) {
  const now = new Date();
  const index = now.getFullYear() * 12 + now.getMonth() + offset;
  const year = Math.floor(index / 12);
  const month = (index % 12) + 1;
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}`;
}

// The preview answer: two groups — would_create (WHOLESALE /
// CONPERIPH, 2 lines, "513.50") and exists (RETAIL / FOODWESTER,
// 1 line, "80.00", PO000777) — one unreleasable item and
// total_value "593.50".
function releasePayloadBase() {
  const month = monthLabel(0);
  return {
    month,
    acumatica_available: true,
    total_value: '593.50',
    groups: [
      {
        site_code: '154',
        supplier_code: 'CONPERIPH',
        origin: `AIERP plan ${month} site 154 vendor CONPERIPH`,
        lines: [
          { item_code: 'AACOMPUT01', quantity: 1, unit_cost: '283.50', value: '283.50' },
          { item_code: 'CONMITT', quantity: 1, unit_cost: '230.00', value: '230.00' },
        ],
        value: '513.50',
        outcome: 'would_create',
        warehouse_code: 'WHOLESALE',
        vendor_code: 'CONPERIPH',
        order_nbr: null,
        error: null,
      },
      {
        site_code: '159',
        supplier_code: 'FOODWESTER',
        origin: `AIERP plan ${month} site 159 vendor FOODWESTER`,
        lines: [{ item_code: 'GLASSBOTT', quantity: 1, unit_cost: '80.00', value: '80.00' }],
        value: '80.00',
        outcome: 'exists',
        warehouse_code: 'RETAIL',
        vendor_code: 'FOODWESTER',
        order_nbr: 'PO000777',
        error: null,
      },
    ],
    unreleasable: [
      { item_code: 'GLASSJAR', site_code: '154', reason: 'no supplier in purchase history' },
    ],
  };
}

// The confirm's answer: the would_create group now created as
// PO003100, the exists group untouched.
function releasePayloadConfirmed() {
  const payload = releasePayloadBase();
  payload.groups[0].outcome = 'created';
  payload.groups[0].order_nbr = 'PO003100';
  return payload;
}

// The preview with an incomplete Acumatica configuration: the
// not-configured note, the null outcomes and codes.
function releasePayloadNotConfigured() {
  const payload = releasePayloadBase();
  payload.acumatica_available = false;
  for (const group of payload.groups) {
    group.outcome = null;
    group.warehouse_code = null;
    group.vendor_code = null;
    group.order_nbr = null;
    group.error = null;
  }
  return payload;
}

// The answer with a hostile vendor code on the first group: it must
// reach the page as text, never as markup.
function releasePayloadHostile() {
  const payload = releasePayloadBase();
  payload.groups[0].vendor_code = '<img src=x onerror=window.__pwned=1>';
  return payload;
}

const RELEASE_409 = { status: 409, json: { detail: 'Acumatica writes are disabled' } };

// Mock every /api/* call the app makes. The handler reads ctrl on
// every request, so the test can steer each release answer
// (ctrl.releaseAnswer, the one-shot ctrl.releaseNext), hold a
// release request until it is answered (ctrl.holdReleaseNext /
// ctrl.heldRelease) for the in-flight state, and steer the sign-in
// (ctrl.config / ctrl.me) when the pass switches from the admin to
// the non-admin viewer. Every release request body is recorded in
// ctrl.releasePosts.
async function mockAggregateApi(page) {
  const ctrl = {
    config: DEV_CONFIG,
    me: ADMIN_ME,
    scenarios: SCENARIOS,
    meRequests: [],
    projectionGets: [],
    spendGets: [],
    deferralsGets: [],
    releasePosts: [],
    releaseAnswer: null,
    releaseNext: null,
    holdReleaseNext: false,
    heldRelease: [],
  };
  const appOrigin = { value: null };

  const handler = (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (appOrigin.value === null) {
      appOrigin.value = url.origin;
    }
    const path = url.pathname;
    if (path === '/api/auth/config') {
      return route.fulfill({ status: 200, json: ctrl.config });
    }
    if (path === '/api/auth/me') {
      ctrl.meRequests.push(request);
      return route.fulfill({ status: 200, json: ctrl.me });
    }
    if (path === '/api/scenarios') {
      return route.fulfill({ status: 200, json: ctrl.scenarios });
    }
    if (path === '/api/aggregate/projection') {
      ctrl.projectionGets.push(request.url());
      return route.fulfill({ status: 200, json: PROJECTION });
    }
    if (path === '/api/aggregate/spend') {
      ctrl.spendGets.push(request.url());
      return route.fulfill({ status: 200, json: SPEND_NO_OVER });
    }
    if (path === '/api/aggregate/deferrals') {
      ctrl.deferralsGets.push(request.url());
      return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
    }
    if (path === '/api/aggregate/release' && request.method() === 'POST') {
      let body = null;
      try {
        body = request.postData() ? JSON.parse(request.postData()) : null;
      } catch (error) {
        body = null;
      }
      ctrl.releasePosts.push(body);
      if (ctrl.holdReleaseNext) {
        ctrl.holdReleaseNext = false;
        ctrl.heldRelease.push(route);
        return; // held: the test answers it (the in-flight state)
      }
      if (ctrl.releaseNext) {
        const next = ctrl.releaseNext;
        ctrl.releaseNext = null;
        return route.fulfill(next);
      }
      const pick =
        typeof ctrl.releaseAnswer === 'function' ? ctrl.releaseAnswer() : ctrl.releaseAnswer;
      return route.fulfill({ status: 200, json: pick || releasePayloadBase() });
    }
    return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
  };

  await page.route('**/api/**', handler);
  return { ctrl, appOrigin, handler };
}

test('issue542 freeform', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  // Every request URL the page makes, for the record.
  const allRequests = [];
  page.on('request', (request) => allRequests.push(request.url()));

  const months = [monthLabel(0), monthLabel(1), monthLabel(2)];
  const currentMonth = months[0];

  // --- admin pass: development sign-in card, straight onto #/aggregate
  page.setViewportSize({ width: 1440, height: 900 });
  const { ctrl, appOrigin, handler } = await mockAggregateApi(page);

  const release = page.locator('[data-testid="aggregate-release"]');
  const releaseMonth = page.locator('[data-testid="aggregate-release-month"]');
  const releasePreview = page.locator('[data-testid="aggregate-release-preview"]');
  const releaseSummary = release.locator('[data-testid="aggregate-release-summary"]');
  const releaseTable = release.locator('[data-testid="aggregate-release-table"]');
  const releaseRows = releaseTable.locator('tbody tr');
  const releaseUnreleasable = release.locator('[data-testid="aggregate-release-unreleasable"]');
  const releaseConfirm = release.locator('[data-testid="aggregate-release-confirm"]');
  const releaseAsk = release.locator('[data-testid="aggregate-release-confirm-ask"]');
  const releaseConfirmYes = release.locator('[data-testid="aggregate-release-confirm-yes"]');
  const releaseConfirmNo = release.locator('[data-testid="aggregate-release-confirm-no"]');
  const releaseError = release.locator('[data-testid="aggregate-release-error"]');
  const releaseLoading = release.locator('[data-testid="aggregate-release-loading"]');
  const releaseNotConfigured = release.locator('[data-testid="aggregate-release-not-configured"]');
  const releaseAdminNote = release.locator('[data-testid="aggregate-release-admin-note"]');
  const releaseBaselineNote = release.locator('[data-testid="aggregate-release-baseline-note"]');
  const deferrals = page.locator('[data-testid="aggregate-deferrals"]');
  const planToggle = page.locator('[data-testid="aggregate-plan-toggle"]');
  const scenarioSelect = page.locator('[data-testid="aggregate-scenario"]');
  const grid = page.locator('[data-testid="aggregate-grid"]');

  await page.goto('/#/aggregate');

  // No request before the sign-in (Auth.bootstrap) completes, and no
  // panel before any answer exists.
  await expect(page.locator('#signin-form')).toBeVisible();
  expect(ctrl.projectionGets.length).toBe(0);
  expect(ctrl.spendGets.length).toBe(0);
  expect(ctrl.deferralsGets.length).toBe(0);
  expect(ctrl.releasePosts.length).toBe(0);
  await expect(release).toHaveCount(0);

  // Sign in through the development token card (the usual admin stub).
  await page.fill('#token-input', 'dev-admin-token');
  await page.click('#signin-button');
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('planner1');
  expect(page.url()).toContain('#/aggregate');
  await expect(grid).toBeVisible();
  await expect.poll(() => ctrl.projectionGets.length).toBe(1);
  await expect.poll(() => ctrl.spendGets.length).toBe(1);

  // The spend answer has no over-budget month: the What to defer
  // block is absent and no deferrals request ever goes out.
  await expect(deferrals).toHaveCount(0);
  await page.waitForTimeout(200);
  expect(ctrl.deferralsGets.length).toBe(0);

  // As is: the planned view's panel is not on the screen yet.
  await expect(release).toHaveCount(0);

  // --- With planned orders: the panel, its controls ---------------
  await planToggle.selectOption('planned');
  await expect(release).toBeVisible();
  // The month select offers the current month and the next two,
  // defaulting to the current month.
  await expect(releaseMonth.locator('option')).toHaveText(months);
  await expect(releaseMonth).toHaveValue(currentMonth);
  await expect(releaseMonth).toBeEnabled();
  // A real <button type="button">, labelled "Preview release".
  const previewShape = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="aggregate-release-preview"]');
    return el
      ? { tag: el.tagName.toLowerCase(), type: el.getAttribute('type'), text: el.textContent }
      : null;
  });
  expect(previewShape).toEqual({ tag: 'button', type: 'button', text: 'Preview release' });
  // No release request before the first preview, no release control
  // before any answer.
  expect(ctrl.releasePosts.length).toBe(0);
  await expect(releaseConfirm).toHaveCount(0);
  await expect(releaseAdminNote).toHaveCount(0);
  // The panel sits under the (absent) deferrals block: after the
  // spend block in the root's children.
  const sectionOrder = await page.evaluate(() => {
    const root = document.querySelector('#aggregate-root');
    const ids = Array.from(root.children).map((child) => child.getAttribute('data-testid'));
    return { spend: ids.indexOf('aggregate-spend'), release: ids.indexOf('aggregate-release') };
  });
  expect(sectionOrder.spend).toBeGreaterThanOrEqual(0);
  expect(sectionOrder.release).toBeGreaterThan(sectionOrder.spend);
  expect(pageErrors).toEqual([]);

  // --- Preview: POST {"month": <current>}, the answer renders -----
  await releasePreview.click();
  await expect.poll(() => ctrl.releasePosts.length).toBe(1);
  expect(ctrl.releasePosts[0]).toEqual({ month: currentMonth });
  await expect(releaseSummary).toHaveText('2 purchase orders, $594 in total');
  await expect(releaseRows).toHaveCount(2);
  expect(await releaseRows.nth(0).locator('td').allTextContents()).toEqual([
    'WHOLESALE',
    'CONPERIPH',
    '2',
    '$514',
    'Will create',
  ]);
  expect(await releaseRows.nth(1).locator('td').allTextContents()).toEqual([
    'RETAIL',
    'FOODWESTER',
    '1',
    '$80',
    'Already in ERP PO000777',
  ]);
  await expect(releaseUnreleasable).toHaveCount(1);
  await expect(releaseUnreleasable).toContainText('GLASSJAR \u00b7 154');
  await expect(releaseUnreleasable).toContainText('no supplier in purchase history');
  await expect(releaseNotConfigured).toHaveCount(0);
  // The release control: one would_create group, so "order" singular.
  await expect(releaseConfirm).toHaveCount(1);
  await expect(releaseConfirm).toHaveText('Release 1 purchase order to the ERP');
  expect(pageErrors).toEqual([]);

  // --- The inline ask: Confirm and Cancel, nothing is sent --------
  await releaseConfirm.click();
  await expect(releaseAsk).toBeVisible();
  await expect(releaseConfirmYes).toHaveCount(1);
  await expect(releaseConfirmYes).toHaveText('Confirm release');
  await expect(releaseConfirmNo).toHaveCount(1);
  await expect(releaseConfirmNo).toHaveText('Cancel');
  await expect(releaseConfirm).toHaveCount(0);
  expect(ctrl.releasePosts.length).toBe(1); // the ask sends nothing
  // Cancel closes the ask and sends nothing.
  await releaseConfirmNo.click();
  await expect(releaseAsk).toHaveCount(0);
  await expect(releaseConfirm).toHaveCount(1);
  expect(ctrl.releasePosts.length).toBe(1);
  expect(pageErrors).toEqual([]);

  // --- Confirm: POST {"month": <current>, "confirm": true} --------
  ctrl.releaseAnswer = releasePayloadConfirmed;
  await releaseConfirm.click();
  await expect(releaseConfirmYes).toBeVisible();
  await releaseConfirmYes.click();
  await expect.poll(() => ctrl.releasePosts.length).toBe(2);
  expect(ctrl.releasePosts[1]).toEqual({ month: currentMonth, confirm: true });
  await expect(releaseRows).toHaveCount(2);
  expect(await releaseRows.nth(0).locator('td').allTextContents()).toEqual([
    'WHOLESALE',
    'CONPERIPH',
    '2',
    '$514',
    'Created PO003100',
  ]);
  expect(await releaseRows.nth(1).locator('td').allTextContents()).toEqual([
    'RETAIL',
    'FOODWESTER',
    '1',
    '$80',
    'Already in ERP PO000777',
  ]);
  // No would_create group left: the release control goes away.
  await expect(releaseConfirm).toHaveCount(0);
  await expect(releaseAsk).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // --- A 409 confirm: the server's detail, the table kept ---------
  ctrl.releaseAnswer = null; // the preview answers the base plan again
  await releasePreview.click();
  await expect.poll(() => ctrl.releasePosts.length).toBe(3);
  await expect(releaseConfirm).toHaveText('Release 1 purchase order to the ERP');
  ctrl.releaseNext = RELEASE_409;
  await releaseConfirm.click();
  await expect(releaseConfirmYes).toBeVisible();
  await releaseConfirmYes.click();
  await expect.poll(() => ctrl.releasePosts.length).toBe(4);
  expect(ctrl.releasePosts[3]).toEqual({ month: currentMonth, confirm: true });
  await expect(releaseError).toHaveCount(1);
  await expect(releaseError).toHaveText('Acumatica writes are disabled');
  // The last shown table stays in place.
  await expect(releaseRows).toHaveCount(2);
  expect(await releaseRows.nth(0).locator('td').allTextContents()).toEqual([
    'WHOLESALE',
    'CONPERIPH',
    '2',
    '$514',
    'Will create',
  ]);
  expect(await releaseRows.nth(1).locator('td').allTextContents()).toEqual([
    'RETAIL',
    'FOODWESTER',
    '1',
    '$80',
    'Already in ERP PO000777',
  ]);
  // The failed confirm leaves no ask behind.
  await expect(releaseAsk).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // --- While a request is out: the panel's controls are disabled --
  ctrl.holdReleaseNext = true;
  await releasePreview.click();
  await expect.poll(() => ctrl.releasePosts.length).toBe(5);
  await expect(releaseLoading).toBeVisible();
  await expect(releasePreview).toBeDisabled();
  await expect(releaseMonth).toBeDisabled();
  // The held (latest) answer is the one that renders.
  ctrl.heldRelease[0].fulfill({ status: 200, json: releasePayloadConfirmed() });
  await expect(releaseLoading).toHaveCount(0);
  await expect(releasePreview).toBeEnabled();
  await expect(releaseMonth).toBeEnabled();
  expect(await releaseRows.nth(0).locator('td').allTextContents()).toEqual([
    'WHOLESALE',
    'CONPERIPH',
    '2',
    '$514',
    'Created PO003100',
  ]);
  expect(pageErrors).toEqual([]);

  // --- Changing the month clears the shown answer ------------------
  await releaseMonth.selectOption(months[1]);
  await expect(releaseMonth).toHaveValue(months[1]);
  await expect(releaseSummary).toHaveCount(0);
  await expect(releaseRows).toHaveCount(0);
  await expect(releaseError).toHaveCount(0);
  await expect(releaseConfirm).toHaveCount(0);
  expect(ctrl.releasePosts.length).toBe(5); // clearing sends nothing
  expect(pageErrors).toEqual([]);

  // --- acumatica_available false: the not-configured note ----------
  ctrl.releaseAnswer = releasePayloadNotConfigured;
  await releasePreview.click();
  await expect.poll(() => ctrl.releasePosts.length).toBe(6);
  expect(ctrl.releasePosts[5]).toEqual({ month: months[1] });
  await expect(releaseNotConfigured).toHaveCount(1);
  await expect(releaseNotConfigured).toHaveText(
    'The ERP connection is not configured; this is the plan only.',
  );
  // The null outcomes: empty Status cells, no release control.
  await expect(releaseRows).toHaveCount(2);
  expect(await releaseRows.nth(0).locator('td').allTextContents()).toEqual([
    '154',
    'CONPERIPH',
    '2',
    '$514',
    '',
  ]);
  await expect(releaseConfirm).toHaveCount(0);
  await expect(releaseAdminNote).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // --- A hostile vendor code: text, never markup --------------------
  ctrl.releaseAnswer = releasePayloadHostile;
  await releasePreview.click();
  await expect.poll(() => ctrl.releasePosts.length).toBe(7);
  const hostileCell = releaseRows.nth(0).locator('td').nth(1);
  await expect(hostileCell).toHaveText('<img src=x onerror=window.__pwned=1>');
  await expect(release.locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
  expect(pageErrors).toEqual([]);

  // --- A saved scenario: the baseline-only message, no controls ----
  await expect.poll(async () => scenarioSelect.locator('option').allTextContents()).toEqual([
    'Baseline',
    'Peak +20%',
  ]);
  const projectionGetsBeforeScenario = ctrl.projectionGets.length;
  await scenarioSelect.selectOption('sc-peak');
  await expect.poll(() => ctrl.projectionGets.length).toBe(projectionGetsBeforeScenario + 1);
  await expect(releaseBaselineNote).toHaveCount(1);
  await expect(releaseBaselineNote).toHaveText(
    'Releasing uses the baseline plan. Switch Scenario to Baseline to release.',
  );
  // No controls of any kind while the scenario is chosen.
  await expect(releaseMonth).toHaveCount(0);
  await expect(releasePreview).toHaveCount(0);
  await expect(releaseTable).toHaveCount(0);
  await expect(releaseConfirm).toHaveCount(0);
  expect(ctrl.releasePosts.length).toBe(7);
  expect(pageErrors).toEqual([]);

  // Back to Baseline: the controls come back.
  await scenarioSelect.selectOption('');
  await expect.poll(() => ctrl.projectionGets.length).toBe(projectionGetsBeforeScenario + 2);
  await expect(releaseBaselineNote).toHaveCount(0);
  await expect(releaseMonth).toHaveCount(1);
  await expect(releasePreview).toHaveCount(1);
  expect(pageErrors).toEqual([]);

  // --- 390x844: the page does not scroll sideways ------------------
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(release).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
    390,
  );
  expect(pageErrors).toEqual([]);

  // --- viewer pass: NON-ADMIN, production boot, full reload ---------
  // The token arrives in the URL hash the way the identity requirement
  // describes; since the hash is no longer a token delivery path the
  // production boot completes through the PKCE round-trip, so the IdP's
  // auth and token endpoints carry the viewer through it.
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.evaluate(() => {
    window.sessionStorage.clear();
  });
  const meCountBeforeViewer = ctrl.meRequests.length;
  const projectionGetsBeforeViewer = ctrl.projectionGets.length;
  const spendGetsBeforeViewer = ctrl.spendGets.length;
  const releasePostsBeforeViewer = ctrl.releasePosts.length;
  await page.unroute('**/api/**');
  ctrl.config = PROD_CONFIG;
  ctrl.me = VIEWER_ME;
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
  await page.goto('/index.html#access_token=viewer-token-abc');

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

  // ... and no fetch went out before the view is activated.
  expect(ctrl.projectionGets.length).toBe(projectionGetsBeforeViewer);
  expect(ctrl.spendGets.length).toBe(spendGetsBeforeViewer);
  expect(ctrl.releasePosts.length).toBe(releasePostsBeforeViewer);

  // Activating the view for the viewer: the page still renders ...
  await page.click('.nav-btn[data-view="aggregate"]');
  expect(page.url()).toContain('#/aggregate');
  await expect(page.locator('#view-aggregate')).toBeVisible();
  await expect(grid).toBeVisible();
  // ... the spend answer has no over-budget month, so the What to
  // defer block stays absent and no deferrals request goes out.
  await expect(deferrals).toHaveCount(0);
  expect(ctrl.deferralsGets.length).toBe(0);

  // ... and in the planned view the panel works for the viewer, with
  // the same month options and default.
  await planToggle.selectOption('planned');
  await expect(release).toBeVisible();
  await expect(releaseMonth.locator('option')).toHaveText(months);
  await expect(releaseMonth).toHaveValue(currentMonth);
  ctrl.releaseAnswer = null;
  await releasePreview.click();
  await expect.poll(() => ctrl.releasePosts.length).toBe(releasePostsBeforeViewer + 1);
  expect(ctrl.releasePosts[releasePostsBeforeViewer]).toEqual({ month: currentMonth });
  await expect(releaseSummary).toHaveText('2 purchase orders, $594 in total');
  await expect(releaseRows).toHaveCount(2);
  expect(await releaseRows.nth(0).locator('td').allTextContents()).toEqual([
    'WHOLESALE',
    'CONPERIPH',
    '2',
    '$514',
    'Will create',
  ]);

  // ... but only an admin can release: the note shows instead of the
  // button, and the page renders on.
  await expect(releaseAdminNote).toHaveCount(1);
  await expect(releaseAdminNote).toHaveText('Only an admin can release.');
  await expect(releaseConfirm).toHaveCount(0);
  await expect(releaseAsk).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // No deferrals request of the whole pass (no over-budget month in
  // any spend answer).
  expect(ctrl.deferralsGets.length).toBe(0);
});
