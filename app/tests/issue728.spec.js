// Proving spec for AIERP issue #728 — the "Push to ERP" screen
// (#/writeback): a planner screen wired like the others — the
// "Push to ERP" nav button (after "Expedite", before "Decision
// log"), the #view-writeback section with #writeback-root, and the
// view registered in the shell (title "Push to ERP", subtitle
// "Accepted reorder points the ERP does not have yet, and every
// write so far").
//
// The screen fetches GET /api/parameter-writeback/preview and
// GET /api/parameter-writeback/history?limit=50 through
// app/js/api.js on activation and when the Refresh button
// ([data-testid="writeback-refresh"], a real <button>) is pressed. A
// request-sequence guard renders only the newest answers, and a
// skeleton shows while a fetch is out. Whether the viewer is an admin
// is read from GET /api/auth/me's roles, exactly as the Work queue
// does; an unknown or missing role fails closed.
//
// The summary line ([data-testid="writeback-summary"]) reads
// "<count> outstanding · <writable_count> ready to push". The
// outstanding table ([data-testid="writeback-table"]) scrolls within
// its own region, one row per preview row in the answer's order:
// Select (admin only, writable rows only,
// [data-testid="writeback-select"]), Item and Site, ERP ("Acumatica"
// for "acumatica", "Odoo" for "odoo", the raw value otherwise, an em
// dash without one), Reorder point and Max as "<current> →
// <proposed>" in whole units (an em dash for a null current value),
// Money at stake (whole dollars, an em dash when null) and Status
// ("Ready" when writable, the blocked_reason otherwise).
//
// Pushing is admin-only: the "Push selected to ERP" button
// ([data-testid="writeback-push"]) is disabled until at least one row
// is selected, and pressing it does not write — it opens the inline
// confirm panel ([data-testid="writeback-confirm-panel"]) reading
// "Write <n> reorder point(s) into the ERP? This changes the ERP's
// planning parameters.". Cancel closes it and sends nothing; Confirm
// ([data-testid="writeback-confirm"], disabled while the request is
// out) sends ONE POST /api/parameter-writeback/confirm with the
// selected pairs. The result list
// ([data-testid="writeback-results"]) shows one line per result —
// item, site, an outcome badge (Written, Unchanged, Failed or
// Skipped) followed by the result's reason when it has one — and both
// lists are fetched again. A 403 shows "Only an admin can push to the
// ERP.", any other failure "The push could not be completed.",
// neither clears the selection. A non-admin sees no Select column, no
// Push button, and a note reading "Only an admin can push to the
// ERP." instead.
//
// The history table ([data-testid="writeback-history"]) has one row
// per history row, newest first: When (written_at as a local date and
// time), Item, Site and ERP, Reorder point "<before> → <after>", the
// outcome badge, By (written_by_name, else written_by) and the error,
// or empty. An empty preview reads "Nothing to push: every accepted
// reorder point is in the ERP.", an empty history "No writes yet."; a
// failed preview load shows "The push preview could not be loaded."
// with its own Retry while the history still renders, and a failed
// history load shows "The write history could not be loaded." with
// its own. Every API value is rendered as text, never as markup; at
// 390x844 the page does not scroll sideways.
//
// The gate serves the static app with no backend, so every /api/*
// call is mocked with page.route. The mocked preview has three rows,
// with count 3 and writable_count 2: ITEM-A / WHOLESALE (acumatica,
// current 10 / max 30, proposed 14 / 34, money 1200.5, writable),
// ITEM-B / WH (odoo, current null / max null, proposed 5 / 5, money
// null, writable) and ITEM-C / MAIN ("aidw-feed", not writable,
// "unsupported source system"). The mocked history has one row: a
// failed Acumatica write with an error. The mocked confirm answer
// carries one "written" result and one "failed" result with its
// error.
//
// The pass runs as an ADMIN signed in through the development token
// card at 1440x900, and it ends as a NON-ADMIN (role 'user') after a
// full reload: the token arrives in the URL hash as
// #access_token=..., /api/auth/config and /api/auth/me are mocked for
// that user, the IdP's auth and token endpoints carry the PKCE
// round-trip, no writeback fetch goes out before the view is
// activated, the screen then renders for the viewer, and the
// admin-only controls — the Select column and the Push button — are
// absent where "Only an admin can push to the ERP." shows instead,
// the page rendering on.
//
// The test is TOP-LEVEL and its title is the full proof name
// ("issue728 freeform"): the merge gate builds its junit nodeids
// from the Playwright test title, and a describe() block would join
// the titles with a "›" the contract nodeid does not carry, so the
// proof would read as "not collected".

const { test, expect } = require('@playwright/test');

const { NAV_VIEWS } = require('./nav_order');

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

// The admin: the only audience for the push controls.
const ADMIN_ME = {
  authenticated: true,
  user: { sub: 'u-admin', username: 'admin1', roles: ['admin'], groups: [] },
};

// The viewer: a signed-in non-admin user (role 'user'), the screen's
// audience for the read-only pass.
const VIEWER_ME = {
  authenticated: true,
  user: { sub: 'v1', username: 'viewer1', roles: ['user'], groups: [] },
};

// The Overview view activates on sign-in (the default view); its
// answers stay minimal so the page renders clean while the Push to
// ERP screen is under test.
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
  as_of: '2026-10-24',
  orders: [],
  counts: { awaiting_approval: 0, late: 0, partly_received: 0, received: 0 },
};

// The Work queue's settings answer: the defaults.
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

// The preview answer: three rows in the server's order, count 3,
// writable_count 2.
function previewPayloadBase() {
  return {
    rows: [
      {
        item_code: 'ITEM-A',
        site_code: 'WHOLESALE',
        decision_id: 'dec-a',
        decided_at: '2026-10-26T08:00:00Z',
        money_at_stake: 1200.5,
        source_system: 'acumatica',
        current_reorder_point: 10,
        current_max_qty: 30,
        current_order_qty: 20,
        proposed_reorder_point: 14,
        proposed_max_qty: 34,
        writable: true,
        blocked_reason: null,
      },
      {
        item_code: 'ITEM-B',
        site_code: 'WH',
        decision_id: 'dec-b',
        decided_at: '2026-10-26T09:30:00Z',
        money_at_stake: null,
        source_system: 'odoo',
        current_reorder_point: null,
        current_max_qty: null,
        current_order_qty: null,
        proposed_reorder_point: 5,
        proposed_max_qty: 5,
        writable: true,
        blocked_reason: null,
      },
      {
        item_code: 'ITEM-C',
        site_code: 'MAIN',
        decision_id: 'dec-c',
        decided_at: '2026-10-27T10:00:00Z',
        money_at_stake: 900,
        source_system: 'aidw-feed',
        current_reorder_point: 8,
        current_max_qty: 24,
        current_order_qty: 16,
        proposed_reorder_point: 10,
        proposed_max_qty: 24,
        writable: false,
        blocked_reason: 'unsupported source system',
      },
    ],
    count: 3,
    writable_count: 2,
  };
}

// The empty preview answer: nothing outstanding.
function previewPayloadEmpty() {
  return {
    rows: [],
    count: 0,
    writable_count: 0,
  };
}

// The history answer: one failed Acumatica write with its error.
function historyPayloadBase() {
  return [
    {
      id: 'wb-1',
      item_code: 'OLDITEM',
      site_code: 'WHOLESALE',
      source_system: 'acumatica',
      decision_id: 'dec-old',
      before_reorder_point: 12,
      before_max_qty: 40,
      after_reorder_point: null,
      after_max_qty: null,
      requested_reorder_point: 16,
      requested_max_qty: 44,
      outcome: 'failed',
      error: 'writer_error',
      written_by: 'u-admin',
      written_by_name: 'Admin User',
      written_at: '2026-10-28T14:30:00Z',
    },
  ];
}

// The empty history answer: no writes yet.
function historyPayloadEmpty() {
  return [];
}

// The confirm answer: one "written" result and one "failed" result
// with its error, in request order.
function confirmPayloadBase() {
  return {
    results: [
      {
        item_code: 'ITEM-A',
        site_code: 'WHOLESALE',
        outcome: 'written',
        reason: null,
        before: { reorder_point: 10, max_qty: 30, order_qty: 20 },
        after: { reorder_point: 14, max_qty: 34, order_qty: 20 },
      },
      {
        item_code: 'ITEM-B',
        site_code: 'WH',
        outcome: 'failed',
        reason: 'writer_error',
        before: null,
        after: null,
      },
    ],
    written: 1,
    unchanged: 0,
    failed: 1,
    skipped: 0,
  };
}

// written_at as a local date and time, the way the screen renders it
// ("YYYY-MM-DD HH:MM"): the same calendar arithmetic, in the test's
// (the machine's) local time zone.
function localDateTime(isoString) {
  const date = new Date(isoString);
  const pad = (value) => (value < 10 ? '0' + value : String(value));
  return (
    date.getFullYear() +
    '-' +
    pad(date.getMonth() + 1) +
    '-' +
    pad(date.getDate()) +
    ' ' +
    pad(date.getHours()) +
    ':' +
    pad(date.getMinutes())
  );
}

// Mock every /api/* call the app makes. The handler reads ctrl on
// every request, so the test can steer the preview and history
// answers (ctrl.previewAnswer / ctrl.historyAnswer, either a payload
// or a { status, json } failure), steer the confirm (ctrl.confirmNext,
// the one-shot failure, and ctrl.confirmAnswer), and steer the
// sign-in (ctrl.config / ctrl.me) when the pass switches from the
// admin to the non-admin viewer. Every writeback request is counted
// (ctrl.previewGets, ctrl.historyGets) and every confirm body is
// recorded in ctrl.confirmBodies.
async function mockWritebackApi(page) {
  const ctrl = {
    config: DEV_CONFIG,
    me: ADMIN_ME,
    previewAnswer: null,
    previewNext: null,
    historyAnswer: null,
    historyNext: null,
    confirmAnswer: null,
    confirmNext: null,
    previewGets: 0,
    historyGets: 0,
    confirmBodies: [],
    meRequests: [],
    summaryGets: 0,
  };
  const appOrigin = { value: null };

  const answerOrFulfill = (route, answer, fallback) => {
    if (answer && typeof answer === 'object' && !Array.isArray(answer) && 'status' in answer) {
      return route.fulfill({ status: answer.status, json: answer.json });
    }
    return route.fulfill({ status: 200, json: answer || fallback });
  };

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
    if (path === '/api/parameter-writeback/preview') {
      ctrl.previewGets += 1;
      if (ctrl.previewNext) {
        const next = ctrl.previewNext;
        ctrl.previewNext = null;
        return route.fulfill(next);
      }
      return answerOrFulfill(route, ctrl.previewAnswer, previewPayloadBase());
    }
    if (path === '/api/parameter-writeback/history') {
      ctrl.historyGets += 1;
      if (ctrl.historyNext) {
        const next = ctrl.historyNext;
        ctrl.historyNext = null;
        return route.fulfill(next);
      }
      return answerOrFulfill(route, ctrl.historyAnswer, historyPayloadBase());
    }
    if (path === '/api/parameter-writeback/confirm' && request.method() === 'POST') {
      let body = null;
      try {
        body = request.postDataJSON();
      } catch (error) {
        // An unparsable body: the recorded null is the failure's proof.
      }
      ctrl.confirmBodies.push(body);
      if (ctrl.confirmNext) {
        const next = ctrl.confirmNext;
        ctrl.confirmNext = null;
        return route.fulfill(next);
      }
      return route.fulfill({ status: 200, json: ctrl.confirmAnswer || confirmPayloadBase() });
    }
    if (path === '/api/aggregate/releases') {
      return route.fulfill({ status: 200, json: RELEASES_EMPTY });
    }
    if (path === '/api/decisions/summary') {
      ctrl.summaryGets += 1;
      return route.fulfill({ status: 200, json: SUMMARY });
    }
    if (path === '/api/drift' || path === '/api/excess' || path === '/api/stockout') {
      return route.fulfill({ status: 200, json: [] });
    }
    if (path === '/api/settings') {
      return route.fulfill({ status: 200, json: SETTINGS_ENTRIES });
    }
    return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
  };

  await page.route('**/api/**', handler);
  return { ctrl, appOrigin, handler };
}

test('issue728 freeform', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  // --- admin pass: development sign-in --------------------------------
  page.setViewportSize({ width: 1440, height: 900 });
  const { ctrl, appOrigin, handler } = await mockWritebackApi(page);

  const summary = page.locator('[data-testid="writeback-summary"]');
  const table = page.locator('[data-testid="writeback-table"]');
  const rows = page.locator('[data-testid="writeback-table"] tbody tr');
  const refresh = page.locator('[data-testid="writeback-refresh"]');
  const pushBtn = page.locator('[data-testid="writeback-push"]');
  const confirmPanel = page.locator('[data-testid="writeback-confirm-panel"]');
  const confirmMsg = page.locator('[data-testid="writeback-confirm-panel"] .wb-confirm-msg');
  const confirmBtn = page.locator('[data-testid="writeback-confirm"]');
  const cancelBtn = page.locator('[data-testid="writeback-cancel"]');
  const results = page.locator('[data-testid="writeback-results"]');
  const pushError = page.locator('[data-testid="writeback-push-error"]');
  const histTable = page.locator('[data-testid="writeback-history"]');
  const histRows = page.locator('[data-testid="writeback-history"] tbody tr');

  await page.goto('/');

  // No writeback request before the sign-in (Auth.bootstrap)
  // completes.
  await expect(page.locator('#signin-form')).toBeVisible();
  expect(ctrl.previewGets).toBe(0);
  expect(ctrl.historyGets).toBe(0);

  // Sign in through the development token card as the admin.
  await page.fill('#token-input', 'dev-admin-token');
  await page.click('#signin-button');
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('admin1');
  // The writeback view is not active yet (the default view is): no
  // writeback request.
  expect(ctrl.previewGets).toBe(0);
  expect(ctrl.historyGets).toBe(0);
  expect(pageErrors).toEqual([]);

  // The nav is wired like the other screens: the "Push to ERP"
  // button after "Expedite", before "Decision log", the view section
  // with #writeback-root, and the hash #/writeback on navigation.
  await expect(page.locator('.nav-btn[data-view="writeback"]')).toHaveCount(1);
  await expect(page.locator('.nav-btn[data-view="writeback"]')).toHaveText('Push to ERP');
  const navViews = await page
    .locator('.nav .nav-btn')
    .evaluateAll((buttons) => buttons.map((b) => b.getAttribute('data-view')));
  expect(navViews).toEqual(NAV_VIEWS);
  const writebackIndex = navViews.indexOf('writeback');
  expect(navViews[writebackIndex - 1]).toBe('expedite');
  expect(navViews[writebackIndex + 1]).toBe('buffers');
  await expect(page.locator('#view-writeback.view[data-view="writeback"]')).toHaveCount(1);
  await expect(page.locator('#view-writeback > #writeback-root')).toHaveCount(1);

  await page.click('.nav-btn[data-view="writeback"]');
  await expect(page).toHaveURL(/#\/writeback$/);
  await expect(page.locator('#view-writeback')).toBeVisible();
  await expect(page.locator('.nav-btn[data-view="writeback"]')).toHaveAttribute(
    'aria-current',
    'page',
  );
  await expect(page.locator('#page-title')).toHaveText('Push to ERP');
  await expect(page.locator('#page-subtitle')).toHaveText(
    'Accepted reorder points the ERP does not have yet, and every write so far',
  );

  // Activation fetches both lists exactly once, and the answers
  // render.
  await expect.poll(() => ctrl.previewGets).toBe(1);
  await expect.poll(() => ctrl.historyGets).toBe(1);
  await expect(table).toBeVisible();
  await expect(histTable).toBeVisible();
  expect(pageErrors).toEqual([]);

  // The summary reads "<count> outstanding · <writable_count> ready
  // to push".
  await expect(summary).toHaveText('3 outstanding · 2 ready to push');

  // The admin answer from /api/auth/me may land after the preview:
  // wait for the admin-only checkboxes so the column check below is
  // stable.
  await expect(page.locator('[data-testid="writeback-select"]')).toHaveCount(2);

  // The table's columns (admin: the Select column is present).
  expect(
    await page.locator('[data-testid="writeback-table"] thead th').allTextContents(),
  ).toEqual([
    'Select',
    'Item and Site',
    'ERP',
    'Reorder point',
    'Max',
    'Money at stake',
    'Status',
    'Source',
  ]);

  // One row per preview row, in the answer's order.
  await expect(rows).toHaveCount(3);

  // ITEM-A / WHOLESALE: acumatica, 10 → 14, 30 → 34, $1,201, Ready —
  // and a select box.
  const rowA = rows.nth(0);
  await expect(rowA.locator('td').nth(1)).toHaveText('ITEM-A / WHOLESALE');
  await expect(rowA.locator('td').nth(2)).toHaveText('Acumatica');
  await expect(rowA.locator('td').nth(3)).toHaveText('10 → 14');
  await expect(rowA.locator('td').nth(4)).toHaveText('30 → 34');
  await expect(rowA.locator('td').nth(5)).toHaveText('$1,201');
  await expect(rowA.locator('td').nth(6)).toHaveText('Ready');
  await expect(rowA.locator('[data-testid="writeback-select"]')).toHaveCount(1);

  // ITEM-B / WH: odoo, a null current value renders as an em dash,
  // no money, Ready — and a select box.
  const rowB = rows.nth(1);
  await expect(rowB.locator('td').nth(1)).toHaveText('ITEM-B / WH');
  await expect(rowB.locator('td').nth(2)).toHaveText('Odoo');
  await expect(rowB.locator('td').nth(3)).toHaveText('— → 5');
  await expect(rowB.locator('td').nth(4)).toHaveText('— → 5');
  await expect(rowB.locator('td').nth(5)).toHaveText('—');
  await expect(rowB.locator('td').nth(6)).toHaveText('Ready');
  await expect(rowB.locator('[data-testid="writeback-select"]')).toHaveCount(1);

  // ITEM-C / MAIN: the raw source system, no select box on a
  // non-writable row, and the blocked_reason as its status.
  const rowC = rows.nth(2);
  await expect(rowC.locator('td').nth(1)).toHaveText('ITEM-C / MAIN');
  await expect(rowC.locator('td').nth(2)).toHaveText('aidw-feed');
  await expect(rowC.locator('td').nth(6)).toHaveText('unsupported source system');
  await expect(rowC.locator('[data-testid="writeback-select"]')).toHaveCount(0);
  // The non-writable row keeps the Select cell (column alignment) but
  // renders no checkbox in it — for an admin either.
  await expect(rowC.locator('td.wb-cell-select')).toHaveCount(1);
  await expect(rowC.locator('td.wb-cell-select input')).toHaveCount(0);

  // The history table: one row per history row, newest first, with
  // the audit's columns.
  expect(
    await page.locator('[data-testid="writeback-history"] thead th').allTextContents(),
  ).toEqual([
    'When',
    'Item, Site and ERP',
    'Reorder point',
    'Outcome',
    'By',
    'Error',
  ]);
  await expect(histRows).toHaveCount(1);
  const histRow = histRows.nth(0);
  await expect(histRow.locator('td').nth(0)).toHaveText(
    localDateTime('2026-10-28T14:30:00Z'),
  );
  await expect(histRow.locator('td').nth(1)).toHaveText('OLDITEM / WHOLESALE · Acumatica');
  await expect(histRow.locator('td').nth(2)).toHaveText('12 → —');
  await expect(histRow.locator('td').nth(3)).toHaveText('Failed');
  await expect(histRow.locator('td').nth(4)).toHaveText('Admin User');
  await expect(histRow.locator('td').nth(5)).toHaveText('writer_error');
  expect(pageErrors).toEqual([]);

  // --- the push flow: select, confirm panel, confirm -------------------
  // Push is disabled until at least one row is selected.
  await expect(pushBtn).toBeVisible();
  await expect(pushBtn).toHaveText('Push selected to ERP');
  await expect(pushBtn).toBeDisabled();
  await expect(confirmPanel).toHaveCount(0);
  expect(ctrl.confirmBodies).toEqual([]);

  // Selecting a writable row enables Push.
  await rowA.locator('[data-testid="writeback-select"]').check();
  await expect(pushBtn).toBeEnabled();
  await rowB.locator('[data-testid="writeback-select"]').check();

  // Push opens the confirm panel with no request.
  await pushBtn.click();
  await expect(confirmPanel).toBeVisible();
  await expect(confirmMsg).toHaveText(
    "Write 2 reorder point(s) into the ERP? This changes the ERP's planning parameters.",
  );
  expect(ctrl.confirmBodies).toEqual([]);

  // Cancel closes it and sends nothing.
  await cancelBtn.click();
  await expect(confirmPanel).toHaveCount(0);
  expect(ctrl.confirmBodies).toEqual([]);

  // Confirm sends exactly one POST whose body lists the selected
  // pairs.
  const previewGetsBeforeConfirm = ctrl.previewGets;
  const historyGetsBeforeConfirm = ctrl.historyGets;
  await pushBtn.click();
  await confirmBtn.click();
  await expect.poll(() => ctrl.confirmBodies.length).toBe(1);
  expect(ctrl.confirmBodies[0]).toEqual({
    items: [
      { item_code: 'ITEM-A', site_code: 'WHOLESALE' },
      { item_code: 'ITEM-B', site_code: 'WH' },
    ],
  });

  // The result list shows one line per result: the mocked "written"
  // and "failed" outcomes render both badges, the failed one with its
  // reason.
  await expect(confirmPanel).toHaveCount(0);
  const resultLines = results.locator('li');
  await expect(resultLines).toHaveCount(2);
  await expect(resultLines.nth(0)).toContainText('ITEM-A / WHOLESALE');
  await expect(resultLines.nth(0).locator('.wb-badge')).toHaveText('Written');
  await expect(resultLines.nth(1)).toContainText('ITEM-B / WH');
  await expect(resultLines.nth(1).locator('.wb-badge')).toHaveText('Failed');
  await expect(resultLines.nth(1)).toContainText('writer_error');
  await expect(pushError).toHaveCount(0);

  // Both lists are fetched again afterwards.
  await expect.poll(() => ctrl.previewGets).toBe(previewGetsBeforeConfirm + 1);
  await expect.poll(() => ctrl.historyGets).toBe(historyGetsBeforeConfirm + 1);
  expect(pageErrors).toEqual([]);

  // --- a 403 shows the admin note and clears nothing --------------------
  ctrl.confirmNext = { status: 403, json: { detail: 'Not enough permissions' } };
  await pushBtn.click();
  await confirmBtn.click();
  await expect(pushError).toHaveText('Only an admin can push to the ERP.');
  expect(ctrl.confirmBodies.length).toBe(2);
  // No refetch on a failed push, and the selection survives.
  expect(ctrl.previewGets).toBe(previewGetsBeforeConfirm + 1);
  expect(ctrl.historyGets).toBe(historyGetsBeforeConfirm + 1);
  await expect(rows.nth(0).locator('[data-testid="writeback-select"]')).toBeChecked();
  await expect(rows.nth(1).locator('[data-testid="writeback-select"]')).toBeChecked();
  await expect(pushBtn).toBeEnabled();

  // --- any other failure shows the generic message -----------------------
  ctrl.confirmNext = { status: 500, json: { detail: 'boom' } };
  await pushBtn.click();
  await confirmBtn.click();
  await expect(pushError).toHaveText('The push could not be completed.');
  expect(ctrl.confirmBodies.length).toBe(3);
  await expect(rows.nth(0).locator('[data-testid="writeback-select"]')).toBeChecked();
  await expect(rows.nth(1).locator('[data-testid="writeback-select"]')).toBeChecked();
  expect(pageErrors).toEqual([]);

  // --- the Refresh button re-fetches both lists -------------------------
  const previewGetsBeforeRefresh = ctrl.previewGets;
  const historyGetsBeforeRefresh = ctrl.historyGets;
  await refresh.click();
  await expect.poll(() => ctrl.previewGets).toBe(previewGetsBeforeRefresh + 1);
  await expect.poll(() => ctrl.historyGets).toBe(historyGetsBeforeRefresh + 1);
  await expect(rows).toHaveCount(3);
  await expect(histRows).toHaveCount(1);

  // --- empty answers show their notes -----------------------------------
  ctrl.previewAnswer = previewPayloadEmpty();
  ctrl.historyAnswer = historyPayloadEmpty();
  await refresh.click();
  await expect(page.locator('[data-testid="writeback-empty"]')).toHaveText(
    'Nothing to push: every accepted reorder point is in the ERP.',
  );
  await expect(page.locator('[data-testid="writeback-history-empty"]')).toHaveText(
    'No writes yet.',
  );
  await expect(summary).toHaveText('0 outstanding · 0 ready to push');
  expect(pageErrors).toEqual([]);

  // --- a 503 on the preview shows its error with a Retry, and the
  //     history still renders ---------------------------------------------
  ctrl.previewAnswer = { status: 503, json: { detail: 'upstream unavailable' } };
  ctrl.historyAnswer = historyPayloadBase();
  await refresh.click();
  await expect(page.locator('[data-testid="writeback-preview-error"]')).toBeVisible();
  await expect(page.locator('[data-testid="writeback-preview-error"] p')).toHaveText(
    'The push preview could not be loaded.',
  );
  await expect(histRows).toHaveCount(1);
  ctrl.previewAnswer = previewPayloadBase();
  await page.click('[data-testid="writeback-preview-retry"]');
  await expect(table).toBeVisible();
  await expect(rows).toHaveCount(3);
  await expect(summary).toHaveText('3 outstanding · 2 ready to push');

  // --- a failed history load shows its own error and Retry ---------------
  ctrl.historyAnswer = { status: 500, json: { detail: 'broken' } };
  ctrl.historyNext = null;
  await refresh.click();
  await expect(page.locator('[data-testid="writeback-history-error"]')).toBeVisible();
  await expect(page.locator('[data-testid="writeback-history-error"] p')).toHaveText(
    'The write history could not be loaded.',
  );
  await expect(table).toBeVisible();
  ctrl.historyAnswer = historyPayloadBase();
  await page.click('[data-testid="writeback-history-retry"]');
  await expect(histRows).toHaveCount(1);
  expect(pageErrors).toEqual([]);

  // --- a non-string item_code still selects and pushes -------------------
  // The checkbox must carry the row's own pair key: item_code and
  // site_code need not be strings, and a key re-derived from string-only
  // attributes would not match the row the box was rendered on, so the
  // selection — and the push — would silently miss it.
  await rows.nth(0).locator('[data-testid="writeback-select"]').uncheck();
  await rows.nth(1).locator('[data-testid="writeback-select"]').uncheck();
  await expect(pushBtn).toBeDisabled();

  const numericPreview = {
    rows: [
      {
        item_code: 4242,
        site_code: 'NUM-SITE',
        decision_id: 'dec-num',
        decided_at: '2026-10-27T11:00:00Z',
        money_at_stake: 250,
        source_system: 'acumatica',
        current_reorder_point: 2,
        current_max_qty: 6,
        current_order_qty: 4,
        proposed_reorder_point: 3,
        proposed_max_qty: 7,
        writable: true,
        blocked_reason: null,
      },
    ],
    count: 1,
    writable_count: 1,
  };
  ctrl.previewAnswer = numericPreview;
  await refresh.click();
  await expect(rows).toHaveCount(1);
  await expect(summary).toHaveText('1 outstanding · 1 ready to push');
  const numRow = rows.nth(0);
  await expect(numRow.locator('td').nth(2)).toHaveText('Acumatica');
  await expect(numRow.locator('td').nth(3)).toHaveText('2 → 3');
  await expect(numRow.locator('td').nth(4)).toHaveText('6 → 7');
  await expect(numRow.locator('td').nth(5)).toHaveText('$250');
  await expect(numRow.locator('td').nth(6)).toHaveText('Ready');
  await expect(numRow.locator('[data-testid="writeback-select"]')).toHaveCount(1);

  // Checking the box: the key round-trips, the box stays checked after
  // the re-render the change triggers, and the push carries the row's
  // raw (non-string) values.
  await numRow.locator('[data-testid="writeback-select"]').check();
  await expect(numRow.locator('[data-testid="writeback-select"]')).toBeChecked();
  await expect(pushBtn).toBeEnabled();
  await pushBtn.click();
  await expect(confirmMsg).toHaveText(
    "Write 1 reorder point(s) into the ERP? This changes the ERP's planning parameters.",
  );
  await confirmBtn.click();
  await expect.poll(() => ctrl.confirmBodies.length).toBe(4);
  expect(ctrl.confirmBodies[3]).toEqual({
    items: [{ item_code: 4242, site_code: 'NUM-SITE' }],
  });

  // Back to the base preview: the numeric row's selection matches no row
  // there, so Push goes disabled again — and the base rows are re-selected
  // for the rest of the pass.
  ctrl.previewAnswer = previewPayloadBase();
  await refresh.click();
  await expect(rows).toHaveCount(3);
  await expect(pushBtn).toBeDisabled();
  await rows.nth(0).locator('[data-testid="writeback-select"]').check();
  await rows.nth(1).locator('[data-testid="writeback-select"]').check();
  await expect(pushBtn).toBeEnabled();
  expect(pageErrors).toEqual([]);

  // --- a hostile item code: text, never markup ---------------------------
  const hostile = previewPayloadBase();
  hostile.rows[0].item_code = '<img src=x onerror=window.__pwned=1>';
  ctrl.previewAnswer = hostile;
  await refresh.click();
  const hostileCell = rows.nth(0).locator('td').nth(1);
  await expect(hostileCell).toContainText('<img src=x onerror=window.__pwned=1>');
  await expect(page.locator('#writeback-root img')).toHaveCount(0);
  expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
  ctrl.previewAnswer = previewPayloadBase();
  expect(pageErrors).toEqual([]);

  // --- "__proto__" as an API value: the raw string, never the object ----
  // A bare lookup would read Object.prototype off the label table for a
  // "__proto__" source_system, and the ERP cell would read
  // "[object Object]".
  const protoPreview = previewPayloadBase();
  protoPreview.rows[0].source_system = '__proto__';
  ctrl.previewAnswer = protoPreview;
  await refresh.click();
  await expect(rows.nth(0).locator('td').nth(2)).toHaveText('__proto__');
  await expect(page.locator('#writeback-root')).not.toContainText('[object Object]');
  ctrl.previewAnswer = previewPayloadBase();

  // The same guard on the confirm's outcome: the badge shows the raw
  // value with the neutral class — not "[object Object]", not a class
  // named after the prototype.
  ctrl.confirmNext = {
    status: 200,
    json: {
      results: [
        {
          item_code: 'ITEM-A',
          site_code: 'WHOLESALE',
          outcome: '__proto__',
          reason: 'proto outcome',
          before: null,
          after: null,
        },
      ],
      written: 0,
      unchanged: 0,
      failed: 0,
      skipped: 0,
    },
  };
  await pushBtn.click();
  await confirmBtn.click();
  await expect.poll(() => ctrl.confirmBodies.length).toBe(5);
  expect(ctrl.confirmBodies[4]).toEqual({
    items: [
      { item_code: 'ITEM-A', site_code: 'WHOLESALE' },
      { item_code: 'ITEM-B', site_code: 'WH' },
    ],
  });
  await expect(results.locator('li')).toHaveCount(1);
  const protoLine = results.locator('li').nth(0);
  await expect(protoLine.locator('.wb-badge')).toHaveText('__proto__');
  await expect(protoLine.locator('.wb-badge')).toHaveClass('wb-badge wb-badge--other');
  await expect(protoLine).toContainText('proto outcome');
  await expect(page.locator('#writeback-root')).not.toContainText('[object Object]');
  expect(pageErrors).toEqual([]);

  // --- 390x844: the page does not scroll sideways ------------------------
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('#view-writeback')).toBeVisible();
  await expect(refresh).toBeVisible();
  const narrowScrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(narrowScrollWidth).toBeLessThanOrEqual(390);
  expect(pageErrors).toEqual([]);

  // --- viewer pass: NON-ADMIN, production boot, full reload -------------
  // The token arrives in the URL hash the way the identity requirement
  // describes; since the hash is no longer a token delivery path the
  // production boot completes through the PKCE round-trip, so the IdP's
  // auth and token endpoints carry the viewer through it.
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.evaluate(() => {
    window.sessionStorage.clear();
  });
  const previewGetsBeforeViewer = ctrl.previewGets;
  const historyGetsBeforeViewer = ctrl.historyGets;
  const meCountBeforeViewer = ctrl.meRequests.length;
  const summaryGetsBeforeViewer = ctrl.summaryGets;
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

  // ... and no writeback fetch went out before the view is activated:
  // the Overview view (the default) loads on its own ...
  await expect.poll(() => ctrl.summaryGets).toBeGreaterThan(summaryGetsBeforeViewer);
  // ... and the Push to ERP screen has not asked for its data yet.
  expect(ctrl.previewGets).toBe(previewGetsBeforeViewer);
  expect(ctrl.historyGets).toBe(historyGetsBeforeViewer);

  // Activating the view for the viewer: the screen renders ...
  await page.click('.nav-btn[data-view="writeback"]');
  expect(page.url()).toContain('#/writeback');
  await expect(page.locator('#view-writeback')).toBeVisible();
  await expect.poll(() => ctrl.previewGets).toBe(previewGetsBeforeViewer + 1);
  await expect.poll(() => ctrl.historyGets).toBe(historyGetsBeforeViewer + 1);
  await expect(summary).toHaveText('3 outstanding · 2 ready to push');
  await expect(rows).toHaveCount(3);
  await expect(histRows).toHaveCount(1);

  // ... and the admin-only controls are absent where the read-only
  // note shows instead — while the page still renders.
  await expect(page.locator('[data-testid="writeback-select"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="writeback-push"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="writeback-confirm-panel"]')).toHaveCount(0);
  await expect(
    page.locator('[data-testid="writeback-admin-note"]'),
  ).toHaveText('Only an admin can push to the ERP.');
  expect(
    await page.locator('[data-testid="writeback-table"] thead th').allTextContents(),
  ).toEqual([
    'Item and Site',
    'ERP',
    'Reorder point',
    'Max',
    'Money at stake',
    'Status',
    'Source',
  ]);
  expect(pageErrors).toEqual([]);
});
