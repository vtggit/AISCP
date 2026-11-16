// Proving spec for AIERP issue #1307 — the "Expedite" screen
// (#/expedite): the "Request" column and the draft panel.
//
// The table gains a twelfth column after Why, headed "Request". Each
// row's cell (.ex-cell-request, never wrapping) holds one button
// ([data-testid="expedite-draft-open"]) reading "Draft request", its
// accessible name "Draft request for <order>", <order> the row's
// Order cell text. Every signed-in user gets it; the screen has no
// admin-only control, and drafting changes nothing.
//
// The button opens that row's draft in a panel
// ([data-testid="expedite-draft"]), the first child of .ex-body,
// above the table's scrolling region and outside it, so it stays in
// view however far the table scrolls sideways: a heading (h2,
// "Expedite request for <order>"), a note ("As of <as_of>. Nothing
// is sent from here: copy the text into your own email or chat."),
// a read-only text box (labelled "Expedite request text") holding
// the draft, a Copy and a Close button, and a status line
// (role="status"), empty when the panel opens.
//
// The draft is plain text the row's own fields build and nothing
// else, its eleven lines joined by a newline, no trailing newline,
// no trailing spaces: the greeting (the supplier's name, or just
// "Hello," without one), an empty line, "Please expedite this order
// line:", the order ("Order: <order_nbr>, line <line_nbr>", each
// part as the Order cell shows it, an em dash when missing; an Odoo
// line — order_type containing a "." — reads "Order: [add the order
// reference]", its database name and record id meaning nothing to a
// supplier), the item (the code, with the description in
// parentheses when there is one), the open quantity (en-US
// separators, up to three decimals, an em dash without a usable
// figure, no unit), the date ("Promised for: <promised_on>" when
// promise_is_default is false, else "Expected by: <expected_on>",
// ending with " (<n> days late)" when days_late is above 0, "(1
// day late)" for 1), an empty line, the need ("We need it by
// <projected_stockout_on>." when the line arrives after the stock
// runs out, else "We need it as soon as possible." — a late row's
// draft never quotes its stockout date — then " Please reply with
// the earliest date you can deliver.") and "Thank you."
//
// Focus: opening a draft moves focus to Copy; Close removes the
// panel and moves focus to that row's Draft request button. One
// draft at a time: another row's button replaces it. The open
// draft is kept in state by the row's index in the answer's lines;
// a re-render, such as the supplier-risk answer landing, keeps it
// open with the same text. The index holds only within one answer,
// so the draft closes on every new expedite read (Refresh, Retry,
// the view activated again), on choosing a chip and on sign-out.
//
// Copy writes the text box's value to the clipboard with one
// navigator.clipboard.writeText call; the status reads "Copied." on
// success, written into the panel in place without a re-render
// (focus stays on Copy) and kept in state. Where the clipboard is
// absent or the call rejects, the text box is focused, its whole
// text selected and document.execCommand('copy') called once; the
// status reads "Could not copy automatically. The text is selected,
// ready to copy." when that returns false or throws, and the text
// stays selected. Opening, copying and closing a draft send no
// request of any kind, open no window and navigate nowhere: no
// mailto: link, no popup, the URL still ends in #/expedite. The
// expedite read and the supplier-risk read are unchanged, with
// their order and their counts, and the screen still sends nothing
// but GETs. At 390x844 the panel, its text box, Copy and Close lie
// between 0 and 390 across, the text box does not scroll sideways
// and neither does the page.
//
// The gate serves the static app with no backend, so every /api/*
// call is mocked with one page.route('**/api/**', handler), the
// handler defined before it is registered, the way
// app/tests/issue1071.spec.js does it. The mocked expedite answer
// has as_of "2026-11-08", stale_excluded 0 and three lines — A
// (late, its own promise), B (after stockout, the ERP's default
// promise) and C (an Odoo line, late) — and the first supplier-risk
// read is held until the test answers it with #1071's risk answer
// (S-HIGH high, S-LOW low).
//
// The pass runs as a PLANNER (role 'user') signed in through the
// development token card at 1440x900, and it ends as a NON-ADMIN
// (role 'user') after a full reload: the token arrives in the URL
// hash as #access_token=..., /api/auth/config and /api/auth/me are
// mocked for that user, the IdP's auth and token endpoints carry
// the PKCE round-trip, the screen then renders for the viewer with
// its full controls — every row's Draft request button — and the
// admin-only control is absent: there is no admin-only control on
// the screen, so the viewer sees exactly the planner sees, the page
// rendering on.
//
// The test is TOP-LEVEL and its title is the full proof name
// ("issue1307 freeform"): the merge gate builds its junit nodeids
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

// The freeform pass runs several sub-scenarios in one test; give slow
// CI runners room without weakening the assertions.
test.setTimeout(90000);

// The development sign-in configuration the mock answers with, and
// the production one for the viewer pass's full-reload boot.
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

// The viewer: another signed-in non-admin, for the production-boot
// pass at the end of the test.
const VIEWER_ME = {
  authenticated: true,
  user: { sub: 'v1', username: 'viewer1', roles: ['user'], groups: [] },
};

// The Overview view activates on sign-in (the default view); its
// answers stay minimal so the page renders clean while the Expedite
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

const RELEASES_EMPTY = {
  as_of: '2026-11-08',
  orders: [],
  counts: { awaiting_approval: 0, late: 0, partly_received: 0, received: 0 },
};

// The mocked expedite answer: as_of "2026-11-08", stale_excluded 0,
// three lines in this order — A, late, its own promise; B, after
// stockout, the ERP's default promise; C, an Odoo line, late.
function expediteAnswer() {
  return {
    as_of: '2026-11-08',
    stale_excluded: 0,
    lines: [
      {
        order_type: 'PO',
        order_nbr: 'PO004101',
        line_nbr: '1',
        item_code: 'GASKET01',
        item_description: 'Racing gasket',
        site_code: 'WHOLESALE',
        supplier_code: 'S-HIGH',
        supplier_name: 'Highland Fasteners',
        vendor_code: 'V-900',
        ordered_on: '2026-09-20',
        promised_on: '2026-11-07',
        expected_on: '2026-11-07',
        promise_is_default: false,
        open_units: 250,
        open_value: 1250.0,
        days_late: 1,
        on_hand_units: 20,
        d: 8.0,
        days_of_cover: 2.5,
        projected_stockout_on: '2026-11-10',
        reasons: ['late'],
        supplier_on_time_rate: 0.72,
      },
      {
        order_type: 'PO',
        order_nbr: 'PO004102',
        line_nbr: '2',
        item_code: 'BEARING02',
        item_description: null,
        site_code: 'RETAIL',
        supplier_code: 'S-LOW',
        supplier_name: null,
        vendor_code: 'V-901',
        ordered_on: '2026-10-20',
        promised_on: '2026-10-20',
        expected_on: '2026-11-30',
        promise_is_default: true,
        open_units: 1200,
        open_value: 900.0,
        days_late: null,
        on_hand_units: 40,
        d: 2.0,
        days_of_cover: 20.0,
        projected_stockout_on: '2026-11-28',
        reasons: ['after_stockout'],
        supplier_on_time_rate: 0.97,
      },
      {
        order_type: 'purchase.order.line',
        order_nbr: 'demo',
        line_nbr: '42',
        item_code: 'SPROCKET03',
        item_description: null,
        site_code: 'WHOLESALE',
        supplier_code: 'S-LOW',
        supplier_name: 'Lowland Parts',
        vendor_code: 'V-900',
        ordered_on: '2026-09-20',
        promised_on: '2026-11-07',
        expected_on: '2026-11-07',
        promise_is_default: false,
        open_units: 250,
        open_value: 1250.0,
        days_late: 1,
        on_hand_units: 20,
        d: 8.0,
        days_of_cover: 2.5,
        projected_stockout_on: '2026-11-10',
        reasons: ['late'],
        supplier_on_time_rate: 0.72,
      },
    ],
  };
}

// #1071's risk answer: S-HIGH high (variable lead times, and the
// only supplier of 3 items) and S-LOW low (no sign).
const RISK_ANSWER = {
  as_of: '2026-11-08',
  suppliers: [
    {
      supplier_code: 'S-HIGH',
      supplier_name: 'Highland Fasteners',
      vendor_code: 'V-900',
      receipts: 40,
      lead_time_mean_days: 12.5,
      lead_time_std_days: 4.38,
      lead_time_cv: 0.35,
      on_time_rate: 0.62,
      sole_source_items: 3,
      late: false,
      variable: true,
      sole_source: true,
      risk: 'high',
    },
    {
      supplier_code: 'S-LOW',
      supplier_name: 'Lowland Metals',
      vendor_code: 'V-901',
      receipts: 30,
      lead_time_mean_days: 9.0,
      lead_time_std_days: 1.08,
      lead_time_cv: 0.12,
      on_time_rate: 0.95,
      sole_source_items: 0,
      late: false,
      variable: false,
      sole_source: false,
      risk: 'low',
    },
  ],
};

// The three drafts the screen must build, their eleven lines joined
// by a newline, no trailing newline, no trailing spaces.
const DRAFT_A = [
  'Hello Highland Fasteners,',
  '',
  'Please expedite this order line:',
  'Order: PO004101, line 1',
  'Item: GASKET01 (Racing gasket)',
  'Open quantity: 250',
  'Promised for: 2026-11-07 (1 day late)',
  '',
  'We need it as soon as possible. Please reply with the earliest date you can deliver.',
  '',
  'Thank you.',
].join('\n');

const DRAFT_B = [
  'Hello,',
  '',
  'Please expedite this order line:',
  'Order: PO004102, line 2',
  'Item: BEARING02',
  'Open quantity: 1,200',
  'Expected by: 2026-11-30',
  '',
  'We need it by 2026-11-28. Please reply with the earliest date you can deliver.',
  '',
  'Thank you.',
].join('\n');

const DRAFT_C = [
  'Hello Lowland Parts,',
  '',
  'Please expedite this order line:',
  'Order: [add the order reference]',
  'Item: SPROCKET03',
  'Open quantity: 250',
  'Promised for: 2026-11-07 (1 day late)',
  '',
  'We need it as soon as possible. Please reply with the earliest date you can deliver.',
  '',
  'Thank you.',
].join('\n');

// Mock every /api/* call the app makes with ONE route handler,
// defined before it is registered with page.route, the way
// app/tests/issue1071.spec.js does it. The handler reads ctrl on
// every request, so the test can steer the sign-in (ctrl.config /
// ctrl.me) when the pass switches from the planner to the viewer,
// and hold the next risk request until it is answered
// (ctrl.holdRiskNext / ctrl.heldRisk). Every expedite request is
// counted in ctrl.expediteGets, every risk request in ctrl.riskGets,
// and each request's method and path land in ctrl.requests for the
// read-only check.
async function mockExpediteApi(page) {
  const ctrl = {
    config: DEV_CONFIG,
    me: PLANNER_ME,
    riskAnswer: null,
    holdRiskNext: false,
    heldRisk: [],
    expediteGets: 0,
    riskGets: 0,
    requests: [],
    meRequests: [],
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
    if (path === '/api/expedite') {
      ctrl.expediteGets += 1;
      return route.fulfill({ status: 200, json: expediteAnswer() });
    }
    if (path === '/api/supplier-risk') {
      ctrl.riskGets += 1;
      if (ctrl.holdRiskNext) {
        ctrl.holdRiskNext = false;
        ctrl.heldRisk.push(route);
        return; // held: the test answers it (the in-flight state)
      }
      if (ctrl.riskAnswer !== null) {
        return route.fulfill(ctrl.riskAnswer);
      }
      return route.fulfill({ status: 200, json: RISK_ANSWER });
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
  return { ctrl, appOrigin, handler };
}

test('issue1307 freeform', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  const popups = [];
  page.on('popup', (popup) => popups.push(popup));

  // --- planner pass: development sign-in -------------------------------
  page.setViewportSize({ width: 1440, height: 900 });
  const { ctrl, appOrigin, handler } = await mockExpediteApi(page);

  const table = page.locator('[data-testid="expedite-table"]');
  const rows = page.locator('tr.ex-row');
  const refresh = page.locator('[data-testid="expedite-refresh"]');
  const chipsRow = page.locator('[data-testid="expedite-filter"]');
  const chipByState = (state) => chipsRow.locator('button[data-state="' + state + '"]');
  const openButtons = page.locator('[data-testid="expedite-draft-open"]');
  const panel = page.locator('[data-testid="expedite-draft"]');
  const draftTitle = page.locator('[data-testid="expedite-draft-title"]');
  const draftNote = page.locator('[data-testid="expedite-draft-note"]');
  const draftText = page.locator('[data-testid="expedite-draft-text"]');
  const draftCopy = page.locator('[data-testid="expedite-draft-copy"]');
  const draftClose = page.locator('[data-testid="expedite-draft-close"]');
  const draftStatus = page.locator('[data-testid="expedite-draft-status"]');

  await page.goto('/');

  // No expedite or risk read before the sign-in (Auth.bootstrap)
  // completes.
  await expect(page.locator('#signin-form')).toBeVisible();
  expect(ctrl.expediteGets).toBe(0);
  expect(ctrl.riskGets).toBe(0);

  // Sign in through the development token card as the planner.
  await page.fill('#token-input', 'dev-planner-token');
  await page.click('#signin-button');
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('planner1');
  expect(pageErrors).toEqual([]);

  // --- activation: one expedite read, the first risk read held --------
  ctrl.holdRiskNext = true;
  await page.click('.nav-btn[data-view="expedite"]');
  await expect(page).toHaveURL(/#\/expedite$/);
  await expect(page.locator('#view-expedite')).toBeVisible();
  await expect.poll(() => ctrl.expediteGets).toBe(1);
  await expect.poll(() => ctrl.riskGets).toBe(1);
  await expect(table).toBeVisible();
  await expect(rows).toHaveCount(3);
  expect(pageErrors).toEqual([]);

  // --- the column: Request after Why, a button in every row ----------
  expect(
    await page.locator('[data-testid="expedite-table"] thead th').allTextContents(),
  ).toEqual([
    'Order',
    'Item',
    'Site',
    'Supplier',
    'Promised',
    'Late',
    'Open units',
    'Value',
    'Cover',
    'Stockout on',
    'Why',
    'Request',
  ]);
  await expect(openButtons).toHaveCount(3);
  await expect(openButtons).toHaveText(['Draft request', 'Draft request', 'Draft request']);
  await expect(openButtons.nth(0)).toHaveAccessibleName('Draft request for PO004101 / 1');
  await expect(openButtons.nth(1)).toHaveAccessibleName('Draft request for PO004102 / 2');
  // The first eleven columns read as they do today.
  await expect(rows.locator('.ex-cell-order')).toHaveText([
    'PO004101 / 1',
    'PO004102 / 2',
    'demo / 42',
  ]);
  // No panel before a click.
  await expect(panel).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // Nothing is sent from the draft: the listener starts just before
  // the first Draft request click.
  const sentRequests = [];
  page.on('request', (request) => sentRequests.push(request.url()));

  // --- draft A: the panel, its text, focus on Copy --------------------
  await openButtons.nth(0).click();
  await expect(panel).toHaveCount(1);
  // The panel is the first child of .ex-body, above and outside the
  // table's scrolling region.
  const panelPlacement = await panel.evaluate((el) => ({
    parent: el.parentElement ? el.parentElement.className : null,
    isFirstChild: el.parentElement ? el.parentElement.firstElementChild === el : false,
    containsTableRegion: !!el.querySelector('.ex-table-region'),
    nextIsTableRegion: el.nextElementSibling
      ? el.nextElementSibling.classList.contains('ex-table-region')
      : false,
  }));
  expect(panelPlacement).toEqual({
    parent: 'ex-body',
    isFirstChild: true,
    containsTableRegion: false,
    nextIsTableRegion: true,
  });
  await expect(draftTitle).toHaveText('Expedite request for PO004101 / 1');
  await expect(draftNote).toHaveText(
    'As of 2026-11-08. Nothing is sent from here: copy the text into your own email or chat.',
  );
  await expect(draftText).toHaveCount(1);
  await expect(draftText).toHaveAttribute('readonly', '');
  // The text box's label, its label element (the gate's Playwright
  // predates expect(...).toHaveLabel).
  const draftLabel = page.locator('label[for="expedite-draft-textarea"]');
  await expect(draftLabel).toHaveCount(1);
  await expect(draftLabel).toHaveText('Expedite request text');
  // The label is not orphaned: the box carries the id the label's
  // for points at and the browser resolves the label to that
  // control, so the box's accessible name is the label's text — an
  // unassociated (orphan) label would leave the box unnamed and
  // fail these checks.
  const labelAssociation = await draftText.evaluate((box) => {
    const label = document.querySelector('label[for="' + box.id + '"]');
    return {
      boxId: box.id,
      labelFound: !!label,
      labelControlsBox: !!label && label.control === box,
    };
  });
  expect(labelAssociation).toEqual({
    boxId: 'expedite-draft-textarea',
    labelFound: true,
    labelControlsBox: true,
  });
  await expect(draftText).toHaveAccessibleName('Expedite request text');
  await expect(draftText).toHaveValue(DRAFT_A);
  // The panel's buttons read Copy and Close.
  await expect(draftCopy).toHaveText('Copy');
  await expect(draftClose).toHaveText('Close');
  await expect(draftCopy).toBeFocused();
  await expect(draftStatus).toHaveText('');
  expect(pageErrors).toEqual([]);

  // --- kept: the risk answer lands, the draft stays open --------------
  ctrl.heldRisk[0].fulfill({ status: 200, json: RISK_ANSWER });
  ctrl.heldRisk.length = 0; // later holds start at index 0 again
  const highBadge = rows
    .nth(0)
    .locator('.ex-cell-supplier [data-testid="expedite-supplier-risk"]');
  await expect(highBadge).toHaveCount(1);
  await expect(highBadge).toHaveText('High risk');
  await expect(highBadge).toHaveClass(/is-high/);
  // The re-render kept the draft open, with the same text.
  await expect(panel).toHaveCount(1);
  await expect(draftTitle).toHaveText('Expedite request for PO004101 / 1');
  await expect(draftText).toHaveValue(DRAFT_A);
  expect(pageErrors).toEqual([]);

  // --- copy: the clipboard API, one call, the status in place ---------
  await page.evaluate(() => {
    window.__clipCalls = [];
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: (text) => {
          window.__clipCalls.push(text);
          return Promise.resolve();
        },
      },
    });
  });
  await draftCopy.click();
  await expect.poll(() => page.evaluate(() => window.__clipCalls.length)).toBe(1);
  expect(await page.evaluate(() => window.__clipCalls[0])).toBe(DRAFT_A);
  await expect(draftStatus).toHaveText('Copied.');
  // The status landed in place — no re-render — so Copy keeps focus.
  await expect(draftCopy).toBeFocused();
  expect(pageErrors).toEqual([]);

  // --- draft B: one panel, the other row's draft replaces it ----------
  await openButtons.nth(1).click();
  await expect(panel).toHaveCount(1);
  await expect(draftTitle).toHaveText('Expedite request for PO004102 / 2');
  await expect(draftNote).toHaveText(
    'As of 2026-11-08. Nothing is sent from here: copy the text into your own email or chat.',
  );
  await expect(draftText).toHaveValue(DRAFT_B);
  // Opening another draft emptied the status again.
  await expect(draftStatus).toHaveText('');
  await expect(draftCopy).toBeFocused();
  expect(pageErrors).toEqual([]);

  // --- draft C: an Odoo line ------------------------------------------
  await openButtons.nth(2).click();
  await expect(panel).toHaveCount(1);
  await expect(draftTitle).toHaveText('Expedite request for demo / 42');
  await expect(draftText).toHaveValue(DRAFT_C);
  const draftC = await draftText.inputValue();
  expect(draftC).toContain('Order: [add the order reference]');
  // The Odoo database name and record id never reach the draft.
  expect(draftC).not.toContain('demo');
  expect(draftC).not.toContain('42');
  expect(pageErrors).toEqual([]);

  // Back on B: the fallback below copies B's text.
  await openButtons.nth(1).click();
  await expect(panel).toHaveCount(1);
  await expect(draftText).toHaveValue(DRAFT_B);

  // --- the fallback: no clipboard API, execCommand decides ------------
  await page.evaluate(() => {
    window.__copyCalls = [];
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    document.execCommand = (command) => {
      const box = document.querySelector('[data-testid="expedite-draft-text"]');
      const start = box.selectionStart;
      const end = box.selectionEnd;
      window.__copyCalls.push({
        command: command,
        focused: document.activeElement === box,
        selectedText: box.value.substring(start, end),
        allSelected: start === 0 && end === box.value.length,
      });
      return false;
    };
  });
  await draftCopy.click();
  await expect.poll(() => page.evaluate(() => window.__copyCalls.length)).toBe(1);
  expect(await page.evaluate(() => window.__copyCalls[0])).toEqual({
    command: 'copy',
    focused: true,
    selectedText: DRAFT_B,
    allSelected: true,
  });
  await expect(draftStatus).toHaveText(
    'Could not copy automatically. The text is selected, ready to copy.',
  );
  // The text stays selected, in the focused text box.
  await expect(draftText).toBeFocused();
  const selection = await draftText.evaluate((box) => [box.selectionStart, box.selectionEnd]);
  expect(selection[0]).toBe(0);
  expect(selection[1]).toBe((await draftText.inputValue()).length);
  expect(pageErrors).toEqual([]);

  // --- close: the panel is gone, focus on B's Draft request -----------
  await draftClose.click();
  await expect(panel).toHaveCount(0);
  await expect(openButtons.nth(1)).toBeFocused();
  expect(pageErrors).toEqual([]);

  // --- a small screen: 390x844, a draft open --------------------------
  await openButtons.nth(1).click();
  await expect(panel).toHaveCount(1);
  await expect(draftText).toHaveValue(DRAFT_B);
  page.setViewportSize({ width: 390, height: 844 });
  await expect(panel).toBeVisible();
  for (const locator of [panel, draftText, draftCopy, draftClose]) {
    const box = await locator.boundingBox();
    expect(box).not.toBeNull();
    expect(box.x).toBeGreaterThanOrEqual(-0.5);
    expect(box.x + box.width).toBeLessThanOrEqual(390.5);
  }
  const noHScroll = await page.evaluate(() => {
    const box = document.querySelector('[data-testid="expedite-draft-text"]');
    return {
      textScroll: box.scrollWidth,
      textClient: box.clientWidth,
      docScroll: document.documentElement.scrollWidth,
      docClient: document.documentElement.clientWidth,
    };
  });
  expect(noHScroll.textScroll).toBeLessThanOrEqual(noHScroll.textClient);
  expect(noHScroll.docScroll).toBeLessThanOrEqual(noHScroll.docClient);
  expect(pageErrors).toEqual([]);

  // --- closed by a change: a chip closes it, Refresh re-reads ---------
  // A draft is open: choosing "Late" closes it.
  await chipByState('late').click();
  await expect(panel).toHaveCount(0);
  // Back on "All" with a draft open: Refresh closes it and sends the
  // second expedite read and the second risk read.
  await chipByState('all').click();
  await openButtons.nth(1).click();
  await expect(panel).toHaveCount(1);
  // Nothing was sent between the first Draft request click and this
  // Refresh, apart from any /api/feed/status read the shell makes on
  // its own ...
  const stray = sentRequests.filter((url) => new URL(url).pathname !== '/api/feed/status');
  expect(stray).toEqual([]);
  expect(ctrl.requests.length).toBeGreaterThan(0);
  expect(ctrl.requests.every((request) => request.method === 'GET')).toBe(true);
  // ... and the expedite read and the risk read each ran exactly
  // once before it.
  expect(ctrl.expediteGets).toBe(1);
  expect(ctrl.riskGets).toBe(1);

  await refresh.click();
  await expect.poll(() => ctrl.expediteGets).toBe(2);
  await expect.poll(() => ctrl.riskGets).toBe(2);
  await expect(panel).toHaveCount(0);
  await expect(rows).toHaveCount(3);
  expect(pageErrors).toEqual([]);

  // --- nothing sent, over the whole test ------------------------------
  // Every /api/* request was a GET; no mailto: link, no popup, and
  // the URL still ends in #/expedite.
  expect(ctrl.requests.every((request) => request.method === 'GET')).toBe(true);
  await expect(page.locator('a[href^="mailto:"]')).toHaveCount(0);
  expect(popups).toEqual([]);
  expect(page.url()).toMatch(/#\/expedite$/);
  expect(pageErrors).toEqual([]);

  // --- viewer pass: NON-ADMIN, production boot, full reload -----------
  // The token arrives in the URL hash the way the identity requirement
  // describes; since the hash is no longer a token delivery path the
  // production boot completes through the PKCE round-trip, so the IdP's
  // auth and token endpoints carry the viewer through it.
  page.setViewportSize({ width: 1440, height: 900 });
  await page.evaluate(() => {
    window.sessionStorage.clear();
  });
  const expediteGetsBeforeViewer = ctrl.expediteGets;
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
  // non-admin user, signed in through /api/auth/me.
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('viewer1');
  expect(page.url()).not.toContain('access_token');
  expect(await page.evaluate(() => window.sessionStorage.getItem('aierp_token'))).toBe(
    'pkce-viewer-token',
  );
  const viewerMe = ctrl.meRequests[ctrl.meRequests.length - 1];
  expect(viewerMe.headers()['authorization']).toBe('Bearer pkce-viewer-token');
  expect(pageErrors).toEqual([]);

  // The screen renders for the viewer with its full controls: the
  // table and every row's Draft request button — the screen has no
  // admin-only control (asserted below), so the viewer sees exactly
  // what the planner and an admin see.
  await page.click('.nav-btn[data-view="expedite"]');
  await expect(page.locator('#view-expedite')).toBeVisible();
  await expect.poll(() => ctrl.expediteGets).toBe(expediteGetsBeforeViewer + 1);
  await expect(rows).toHaveCount(3);
  await expect(rows.locator('.ex-cell-order')).toHaveText([
    'PO004101 / 1',
    'PO004102 / 2',
    'demo / 42',
  ]);
  await expect(openButtons).toHaveCount(3);
  await expect(openButtons.nth(0)).toHaveAccessibleName('Draft request for PO004101 / 1');
  await expect(openButtons.nth(1)).toHaveAccessibleName('Draft request for PO004102 / 2');
  // No admin-only control is present in the expedite view at all:
  // the screen has none, so nothing is hidden from the non-admin.
  await expect(page.locator('#view-expedite [data-admin-only]')).toHaveCount(0);

  // And a draft opens for the viewer, as it does for anyone
  // signed in.
  await openButtons.nth(0).click();
  await expect(panel).toHaveCount(1);
  await expect(draftTitle).toHaveText('Expedite request for PO004101 / 1');
  await expect(draftText).toHaveValue(DRAFT_A);
  await expect(draftCopy).toBeFocused();
  expect(pageErrors).toEqual([]);
});
