// Proving spec for AIERP issue #919 — the Transfer orders section
// on the Releases screen (#/releases).
//
// Below the releases table the section ([data-testid=
// "releases-transfers"]) is visible to every signed-in user: headed
// "Transfer orders" with a "Show transfer orders" button
// ([data-testid="releases-transfers-show"]), it sends no request
// until that button is clicked. One click sends exactly one GET
// /api/supply-plan/transfers/release/audit?limit=50 and the answer
// renders as a table ([data-testid="releases-transfers-table"]), one
// row per audit row in the answer's order: Transfer (reference_nbr,
// else an em dash), From and To (the warehouse code, else the site
// code), Lines (each line's item and quantity), Outcome, ERP status
// (erp_status, else an em dash) and Released (the date of
// created_at). Once a 200 (rows or an empty list) has landed the
// button reads "Refresh transfer orders"; a failure before the first
// answer leaves it "Show transfer orders". An empty answer reads
// "No transfer orders released yet.", a 403 — the audit route is
// admin-only — reads "Only an admin can see transfer orders.", and
// any other failure reads "Transfer orders could not be loaded." in
// [data-testid="releases-transfers-error"], leaving the releases
// table unchanged. The answer, error and pending flag live in module
// state: a re-render (the chip filter) keeps the answer, sign-out
// clears them, and an answer that lands after sign-out is discarded.
// Every API value is inserted as text, never as markup, the colours
// come only from the design tokens, and at 390x844 the page does not
// scroll sideways.
//
// The gate serves the static app with no backend, so every /api/*
// call is mocked with page.route — the same mocks and sign-in as the
// supplier-name spec (issue #619), plus a mock for the audit route
// that counts its requests and is steered per click: the one-row
// answer (reference "003219", warehouses RETAIL and WHOLESALE, one
// line of WIDGET01 quantity 5117, outcome "created", erp_status
// "On Hold", created_at "2026-11-02T00:20:16+00:00"), a 403, a row
// with a null reference_nbr and erp_status and null warehouse codes
// (the site codes fall back), an empty list, a 503, and a row whose
// item_code is hostile markup.
//
// The pass runs as a PLANNER (role 'user') signed in through the
// development token card at 1440x900 — the section shows, opening
// the view sends no audit request, and a click answered with 403
// reads the admin-only message — and it ends as a NON-ADMIN (role
// 'user') after a full reload: the token arrives in the URL hash as
// #access_token=..., /api/auth/config and /api/auth/me are mocked
// for that user, the IdP's auth and token endpoints carry the PKCE
// round-trip, the section still shows for the viewer (opening the
// view sends no audit request) and the click the admin-only audit
// route answers with 403 shows the read-only message where the audit
// table is absent, while the releases table still renders.
//
// The test is TOP-LEVEL and its title is the full proof name
// ("issue919 freeform"): the merge gate builds its junit nodeids
// from the Playwright test title, and a describe() block would join
// the titles with a "›" the contract nodeid does not carry, so the
// proof would read as "not collected".

const { test, expect } = require('@playwright/test');

// The freeform pass walks the on-demand, rendering, failure,
// sign-out and phone-width checks plus the production-boot viewer
// pass in one test; give slow CI runners room without weakening the
// assertions.
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

// The planner: a signed-in non-admin user, the screen's audience.
const PLANNER_ME = {
  authenticated: true,
  user: { sub: 'u1', username: 'planner1', roles: ['user'], groups: [] },
};

// The viewer: the NON-ADMIN the identity requirement boots the page
// for through the production PKCE round-trip.
const VIEWER_ME = {
  authenticated: true,
  user: { sub: 'v1', username: 'viewer1', roles: ['user'], groups: [] },
};

const AUDIT_PATH = '/api/supply-plan/transfers/release/audit';

// The one-row audit answer the rendering check reads: reference
// "003219", warehouses RETAIL and WHOLESALE, one line of WIDGET01
// quantity 5117, outcome "created", erp_status "On Hold".
const AUDIT_ROW = {
  id: 1,
  origin: 'AIERP order plan 2026-11-02',
  from_site_code: 'RETAIL',
  to_site_code: 'WHOLESALE',
  from_warehouse_code: 'RETAIL',
  to_warehouse_code: 'WHOLESALE',
  reference_nbr: '003219',
  outcome: 'created',
  error: null,
  line_count: 1,
  lines: [{ item_code: 'WIDGET01', quantity: 5117 }],
  actor: 'planner1',
  actor_name: 'Planner One',
  created_at: '2026-11-02T00:20:16+00:00',
  updated_at: '2026-11-02T00:20:16+00:00',
  erp_status: 'On Hold',
  erp_status_checked_at: null,
};

// The null-values row: no reference_nbr and no erp_status (em
// dashes), and null warehouse codes that fall back to the site codes.
const NULL_ROW = {
  id: 2,
  origin: 'AIERP order plan 2026-11-02',
  from_site_code: 'RETAIL',
  to_site_code: 'WHOLESALE',
  from_warehouse_code: null,
  to_warehouse_code: null,
  reference_nbr: null,
  outcome: 'created',
  error: null,
  line_count: 1,
  lines: [{ item_code: 'WIDGET01', quantity: 3 }],
  actor: 'planner1',
  actor_name: 'Planner One',
  created_at: '2026-11-02T00:20:16+00:00',
  updated_at: '2026-11-02T00:20:16+00:00',
  erp_status: null,
  erp_status_checked_at: null,
};

// The hostile item_code the escaping case sends to the section: it
// must reach the page as text, never as markup.
const HOSTILE_ITEM = '<img src=x onerror=window.__pwned=1>';

const HOSTILE_ROW = {
  id: 3,
  origin: 'AIERP order plan 2026-11-02',
  from_site_code: 'RETAIL',
  to_site_code: 'WHOLESALE',
  from_warehouse_code: 'RETAIL',
  to_warehouse_code: 'WHOLESALE',
  reference_nbr: '003220',
  outcome: 'created',
  error: null,
  line_count: 1,
  lines: [{ item_code: HOSTILE_ITEM, quantity: 7 }],
  actor: 'planner1',
  actor_name: 'Planner One',
  created_at: '2026-11-02T00:20:16+00:00',
  updated_at: '2026-11-02T00:20:16+00:00',
  erp_status: 'On Hold',
  erp_status_checked_at: null,
};

// The Overview view activates on sign-in (the default view); its
// answers stay minimal so the page renders clean while the releases
// screen is under test.
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

// The releases answer: three orders, the same shape the releases
// screen reads — the table the section must leave unchanged.
function releasesPayloadBase() {
  return {
    as_of: '2026-10-25',
    orders: [
      {
        order_nbr: 'PO004103',
        state: 'open',
        month: '2026-10',
        site_code: 'WHOLESALE',
        supplier_code: 'ELEEASTCOM',
        warehouse_code: 'WHOLESALE',
        vendor_code: 'AAVENDOR',
        supplier_name: 'Goto Vendor Inc',
        released_at: '2026-10-20T09:30:00Z',
        actor: 'planner1',
        value: '66931.41',
        status: 'Open',
        hold: false,
        promised_on: '2026-11-05',
        days_waiting: null,
        days_late: null,
        lines: [],
      },
      {
        order_nbr: 'PO004104',
        state: 'received',
        month: '2026-10',
        site_code: 'RETAIL',
        supplier_code: 'CONPERIPH',
        warehouse_code: 'RETAIL',
        vendor_code: 'BBVENDOR',
        released_at: '2026-10-05T10:00:00Z',
        actor: 'planner1',
        value: '12040.00',
        status: 'Closed',
        hold: false,
        promised_on: '2026-10-28',
        days_waiting: null,
        days_late: null,
        lines: [],
      },
      {
        order_nbr: 'PO004105',
        state: 'open',
        month: '2026-11',
        site_code: 'RETAIL',
        supplier_code: 'FOODWESTER',
        warehouse_code: 'RETAIL',
        vendor_code: 'CCVENDOR',
        supplier_name: null,
        released_at: '2026-11-09T14:45:00Z',
        actor: 'planner1',
        value: '8300.00',
        status: 'Open',
        hold: false,
        promised_on: null,
        days_waiting: null,
        days_late: null,
        lines: [],
      },
    ],
    counts: { awaiting_approval: 0, late: 0, partly_received: 0, received: 1 },
  };
}

// Mock every /api/* call the app makes. The handler reads ctrl on
// every request, so the test can steer the sign-in (ctrl.config /
// ctrl.me) and the audit answer (ctrl.auditStatus / ctrl.auditBody)
// as the pass walks the checks. Every request to the audit route is
// counted (ctrl.auditGets) and kept (ctrl.auditRequests), and every
// /api/auth/me request lands in ctrl.meRequests.
async function mockReleasesApi(page) {
  const ctrl = {
    config: DEV_CONFIG,
    me: PLANNER_ME,
    releasesGets: 0,
    meRequests: [],
    auditGets: 0,
    auditFulfilled: 0,
    auditRequests: [],
    auditStatus: 200,
    auditBody: [AUDIT_ROW],
    auditDelayMs: 0,
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
    if (path === AUDIT_PATH) {
      ctrl.auditGets += 1;
      ctrl.auditRequests.push(request);
      const fulfill = () => {
        ctrl.auditFulfilled += 1;
        route.fulfill({ status: ctrl.auditStatus, json: ctrl.auditBody });
      };
      if (ctrl.auditDelayMs > 0) {
        setTimeout(fulfill, ctrl.auditDelayMs);
        return undefined;
      }
      return fulfill();
    }
    if (path === '/api/aggregate/releases') {
      ctrl.releasesGets += 1;
      return route.fulfill({ status: 200, json: releasesPayloadBase() });
    }
    if (path === '/api/decisions/summary') {
      return route.fulfill({ status: 200, json: SUMMARY });
    }
    if (path === '/api/drift' || path === '/api/excess' || path === '/api/stockout') {
      return route.fulfill({ status: 200, json: [] });
    }
    return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
  };

  await page.route('**/api/**', handler);
  return { ctrl, appOrigin, handler };
}

test('issue919 freeform', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  const section = page.locator('[data-testid="releases-transfers"]');
  const showButton = page.locator('[data-testid="releases-transfers-show"]');
  const transfersTable = page.locator('[data-testid="releases-transfers-table"]');
  const transfersRows = page.locator('[data-testid="releases-transfers-row"]');
  const transfersError = page.locator('[data-testid="releases-transfers-error"]');
  const releasesTable = page.locator('[data-testid="releases-table"]');
  const rlRows = page.locator('tr.rl-row');

  // --- planner pass: development sign-in at 1440x900 ---------------
  page.setViewportSize({ width: 1440, height: 900 });
  const { ctrl, appOrigin, handler } = await mockReleasesApi(page);

  await page.goto('/');

  // No audit request before the sign-in (Auth.bootstrap) completes.
  await expect(page.locator('#signin-form')).toBeVisible();
  expect(ctrl.auditGets).toBe(0);

  // Sign in through the development token card as the planner (a
  // non-admin, role 'user').
  await page.fill('#token-input', 'dev-planner-token');
  await page.click('#signin-button');
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('planner1');
  expect(pageErrors).toEqual([]);

  // --- on demand: the section shows, the opening sends no request --
  await page.click('.nav-btn[data-view="releases"]');
  await expect(page).toHaveURL(/#\/releases$/);
  await expect(page.locator('#view-releases')).toBeVisible();
  await expect(releasesTable).toBeVisible();
  await expect(rlRows).toHaveCount(3);
  // The section sits below the releases table, headed "Transfer
  // orders", with its button — and opening the view sent no audit
  // request.
  await expect(section).toBeVisible();
  await expect(section.locator('.rl-transfers-title')).toHaveText('Transfer orders');
  await expect(showButton).toBeVisible();
  await expect(showButton).toHaveText('Show transfer orders');
  expect(ctrl.auditGets).toBe(0);
  const belowTable = await page.evaluate((selectors) => {
    const [tableSelector, sectionSelector] = selectors;
    const table = document.querySelector(tableSelector);
    const box = document.querySelector(sectionSelector);
    if (!table || !box) return null;
    const tableRect = table.getBoundingClientRect();
    const sectionRect = box.getBoundingClientRect();
    return sectionRect.top >= tableRect.bottom - 0.5;
  }, ['[data-testid="releases-table"]', '[data-testid="releases-transfers"]']);
  expect(belowTable).toBe(true);
  expect(pageErrors).toEqual([]);

  // --- as a planner: the admin-only route answers 403 --------------
  // The planner is a role 'user': the click is refused, the fixed
  // message shows, and — no success having landed yet — the button
  // stays "Show transfer orders".
  ctrl.auditStatus = 403;
  ctrl.auditBody = { detail: 'Admins only.' };
  await showButton.click();
  await expect.poll(() => ctrl.auditGets).toBe(1);
  await expect(transfersError).toHaveText('Only an admin can see transfer orders.');
  await expect(showButton).toHaveText('Show transfer orders');
  // The releases table is untouched by the failure.
  await expect(releasesTable).toBeVisible();
  await expect(rlRows).toHaveCount(3);
  expect(pageErrors).toEqual([]);

  // --- rendering: one click, exactly one request, the row ----------
  ctrl.auditStatus = 200;
  ctrl.auditBody = [AUDIT_ROW];
  await showButton.click();
  await expect.poll(() => ctrl.auditGets).toBe(2); // exactly one request
  await expect(transfersTable).toBeVisible();
  await expect(transfersRows).toHaveCount(1);
  const cells = transfersRows.nth(0).locator('td');
  await expect(cells.nth(0)).toHaveText('003219');
  await expect(cells.nth(1)).toHaveText('RETAIL');
  await expect(cells.nth(2)).toHaveText('WHOLESALE');
  await expect(cells.nth(3)).toHaveText('WIDGET01 \u00d7 5,117');
  await expect(cells.nth(4)).toHaveText('created');
  await expect(cells.nth(5)).toHaveText('On Hold');
  await expect(cells.nth(6)).toHaveText('2026-11-02');
  // A successful answer has landed: the button now refreshes.
  await expect(showButton).toHaveText('Refresh transfer orders');
  expect(pageErrors).toEqual([]);

  // A re-render (the chip filter) keeps the section's answer: the
  // module state survives, the row is still there.
  await page.click('.rl-chip[data-state="received"]');
  await expect(rlRows).toHaveCount(1);
  await expect(transfersTable).toBeVisible();
  await expect(transfersRows).toHaveCount(1);
  await expect(transfersRows.nth(0).locator('td').nth(0)).toHaveText('003219');
  await page.click('.rl-chip[data-state="all"]');
  await expect(rlRows).toHaveCount(3);
  expect(pageErrors).toEqual([]);

  // --- null values: em dashes, the site codes fall back ------------
  ctrl.auditBody = [NULL_ROW];
  await showButton.click();
  await expect.poll(() => ctrl.auditGets).toBe(3);
  await expect(transfersRows).toHaveCount(1);
  const nullCells = transfersRows.nth(0).locator('td');
  await expect(nullCells.nth(0)).toHaveText('\u2014'); // no reference_nbr
  await expect(nullCells.nth(1)).toHaveText('RETAIL'); // no warehouse code
  await expect(nullCells.nth(2)).toHaveText('WHOLESALE'); // no warehouse code
  await expect(nullCells.nth(3)).toHaveText('WIDGET01 \u00d7 3');
  await expect(nullCells.nth(4)).toHaveText('created');
  await expect(nullCells.nth(5)).toHaveText('\u2014'); // no erp_status
  await expect(nullCells.nth(6)).toHaveText('2026-11-02');
  expect(pageErrors).toEqual([]);

  // --- empty answer -------------------------------------------------
  ctrl.auditBody = [];
  await showButton.click();
  await expect.poll(() => ctrl.auditGets).toBe(4);
  await expect(page.locator('[data-testid="releases-transfers-empty"]')).toHaveText(
    'No transfer orders released yet.',
  );
  await expect(transfersTable).toHaveCount(0);
  // An empty list is a successful answer: the label stays refreshed.
  await expect(showButton).toHaveText('Refresh transfer orders');
  expect(pageErrors).toEqual([]);

  // --- a 503: the generic failure, the releases table stays --------
  ctrl.auditStatus = 503;
  ctrl.auditBody = { detail: 'The ERP is unreachable.' };
  await showButton.click();
  await expect.poll(() => ctrl.auditGets).toBe(5);
  await expect(transfersError).toHaveText('Transfer orders could not be loaded.');
  await expect(releasesTable).toBeVisible();
  await expect(rlRows).toHaveCount(3);
  expect(pageErrors).toEqual([]);

  // --- sign-out clears the module state ----------------------------
  // A click in flight when the user signs out: the answer that lands
  // afterwards is discarded, and the fresh sign-in opens the section
  // with no answer and no new request.
  ctrl.auditDelayMs = 400;
  ctrl.auditStatus = 200;
  ctrl.auditBody = [AUDIT_ROW];
  await showButton.click();
  await expect.poll(() => ctrl.auditGets).toBe(6);
  // The request is out; sign out before it lands.
  await page.click('#signout-button');
  await expect(page.locator('#signin-form')).toBeVisible();
  // The delayed answer lands on the old session: discarded.
  await expect.poll(() => ctrl.auditFulfilled).toBe(6);
  // Sign back in and open the view again: the section is back at its
  // first-load state, and opening it sends no audit request.
  await page.fill('#token-input', 'dev-planner-token');
  await page.click('#signin-button');
  await expect(page.locator('#app')).toBeVisible();
  await expect(releasesTable).toBeVisible();
  expect(ctrl.auditGets).toBe(6); // no new request on opening the view
  await expect(showButton).toHaveText('Show transfer orders');
  await expect(transfersTable).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // --- hostile text: the item_code renders as text ------------------
  ctrl.auditDelayMs = 0;
  ctrl.auditBody = [HOSTILE_ROW];
  await showButton.click();
  await expect.poll(() => ctrl.auditGets).toBe(7);
  await expect(transfersRows).toHaveCount(1);
  await expect(transfersRows.nth(0).locator('td').nth(3)).toHaveText(
    HOSTILE_ITEM + ' \u00d7 7',
  );
  await expect(section.locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
  expect(pageErrors).toEqual([]);

  // Every audit request the section sent carried limit=50.
  for (const request of ctrl.auditRequests) {
    const auditUrl = new URL(request.url());
    expect(auditUrl.pathname).toBe(AUDIT_PATH);
    expect(auditUrl.searchParams.get('limit')).toBe('50');
  }

  // --- phone width: 390x844, no sideways scroll ---------------------
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('#view-releases')).toBeVisible();
  const narrowScrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(narrowScrollWidth).toBeLessThanOrEqual(390);
  expect(pageErrors).toEqual([]);

  // --- viewer pass: NON-ADMIN, production boot, full reload --------
  // The token arrives in the URL hash the way the identity requirement
  // describes; since the hash is no longer a token delivery path the
  // production boot completes through the PKCE round-trip, so the IdP's
  // auth and token endpoints carry the viewer through it.
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.evaluate(() => {
    window.sessionStorage.clear();
  });
  const auditGetsBeforeViewer = ctrl.auditGets;
  const meCountBeforeViewer = ctrl.meRequests.length;
  await page.unroute('**/api/**');
  ctrl.config = PROD_CONFIG;
  ctrl.me = VIEWER_ME;
  ctrl.auditStatus = 403;
  ctrl.auditBody = { detail: 'Admins only.' };
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

  // ... the section shows for the viewer too, and opening the view
  // sends no audit request ...
  await page.click('.nav-btn[data-view="releases"]');
  await expect(page).toHaveURL(/#\/releases$/);
  await expect(page.locator('#view-releases')).toBeVisible();
  await expect(releasesTable).toBeVisible();
  await expect(rlRows).toHaveCount(3);
  await expect(section).toBeVisible();
  await expect(showButton).toHaveText('Show transfer orders');
  expect(ctrl.auditGets).toBe(auditGetsBeforeViewer);
  expect(pageErrors).toEqual([]);

  // ... and the admin-only audit read stays refused: the click is
  // answered 403, the read-only message shows where the audit table
  // is absent, while the releases table still renders.
  await showButton.click();
  await expect.poll(() => ctrl.auditGets).toBe(auditGetsBeforeViewer + 1);
  await expect(transfersError).toHaveText('Only an admin can see transfer orders.');
  await expect(transfersTable).toHaveCount(0);
  await expect(releasesTable).toBeVisible();
  await expect(rlRows).toHaveCount(3);
  expect(pageErrors).toEqual([]);
});
