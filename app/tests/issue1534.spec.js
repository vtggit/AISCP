// Proving spec for AIERP issue #1534 — the measure on the Overview's
// Reorder points card: the card's load region
// (#ov-region-card-drift) gains GET /api/parameter-writeback/record
// (the Push to ERP screen's writeback record, no query), its own read
// beside the summary, the list and the acceptance read it sends today
// (no query string, out with them: first activation, each activation
// after Overview.invalidate(), the card's own Retry), with its own
// catch that answers null, exactly like the card's acceptance read.
// With a usable answer the card gains the "pushes" block
// (data-testid="ov-drift-pushes", class ov-proposals — the styling
// the card's acceptance block uses) directly before the card's Open
// button: after the acceptance block when that block shows, otherwise
// directly after the card's figures row. The answer is usable only
// while its reorder_points is an object holding the whole numbers
// pushed, read and unchanged, each at least 0, unchanged no greater
// than read and read no greater than pushed; the buffers kind is not
// read on this card, and a usable answer whose pushed is 0 leaves the
// block out. A failed read — any status, a network error — or an
// unusable body leaves the block out: the card renders exactly as
// before, no error text, no Retry, no new [role="alert"].
//
// The gate serves the static app with no backend, so every /api/*
// call is mocked with page.route — the same mocks and sign-in as
// app/tests/issue1413.spec.js: /api/auth/config answers authMode
// development and the pass signs in through the development token
// card at 1440x900 on #/overview, as the admin planner1. The handler
// gains one branch: /api/parameter-writeback/record counts each
// request in ctrl.recordGets, records its query string in
// ctrl.recordQueries and answers ctrl.recordAnswer with the status
// ctrl.recordStatus (200 by default; any other status answers
// {"detail": "Internal Server Error"}). The acceptance branch stays
// as it is, answering 1413's FULL answer unless a case says
// otherwise; the stockout and excess proposals branches stay as they
// are, answering their FIRST answers throughout. Every request's
// method and path also lands in ctrl.requests, and a pageerror
// listener collects page errors.
//
// The pass proves, in order:
//   * READ: with reorder_points {pushed 5, read 4, unchanged 3, rate
//     0.75} and buffers {pushed 3, read 2, unchanged 1, rate 0.5},
//     one /api/parameter-writeback/record request, that request with
//     no query string, and the block's line — "Reorder points still
//     as pushed in the ERP: 3 of 4 read back (75%)" — exactly one
//     block on the page, inside the Reorder points card, its
//     previous element sibling the card's acceptance block and its
//     next the card's .ov-open button; the card still reads "0 to
//     decide" with its three figures "$1,200", "$0" and "0".
//   * FROM THE COUNTS: reorder_points {pushed 10, read 10,
//     unchanged 1, rate 0.5} — the line reads "Reorder points still
//     as pushed in the ERP: 1 of 10 read back (10%)", the 10% worked
//     out from the counts, not the 0.5 rate.
//   * NOT READ: reorder_points {pushed 2, read 0, unchanged 0, rate
//     null} — the line reads "Reorder points pushed: 2; none read
//     back by a sync yet".
//   * NONE: reorder_points {pushed 0, read 0, unchanged 0, rate
//     null} — there is no block, the card renders exactly as before.
//   * Without the acceptance block: with the acceptance read
//     answering 500, the block sits directly after .ov-card-figures
//     and directly before .ov-open.
//   * Fails: each of a 500 answer, a body of {}, and reorder_points
//     with unchanged 5 above its read 4 leaves no block, while the
//     card's figures, its acceptance block and its Open button render
//     as before and the card holds no [role="alert"].
//   * A small screen: at 390x844 the page does not scroll sideways.
//
// The pass ends as a NON-ADMIN (role 'user') after a full reload,
// the way app/tests/issue1413.spec.js's viewer pass does: the token
// arrives in the URL hash as #access_token=..., /api/auth/config and
// /api/auth/me are mocked for that user, the IdP's auth and token
// endpoints carry the PKCE round-trip, the shell renders for the
// viewer with the same line the admin saw, and the admin-only
// control — the Order plan's Release buttons — is absent while the
// page still renders.
//
// Throughout: one Overview load sends exactly one record read, as a
// GET with no query, after each case the Reorder points card holds
// no skeleton element, no page error was raised, and every request
// the run sends is a GET.
//
// The test is TOP-LEVEL and its title is the full proof name
// ("issue1534 freeform"): the merge gate builds its junit nodeids
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

// 1413's full acceptance answer: the acceptance read's answer
// unless a case says otherwise.
const FULL = {
  accepted: { count: 3, money_at_stake: 700, without_money: 1 },
  dismissed: { count: 2, money_at_stake: 350, without_money: 0 },
  snoozed: { count: 1 },
  rate: 0.6,
  money_rate: 0.6667,
  raise: { accepted: 2, dismissed: 0 },
  lower: { accepted: 1, dismissed: 1 },
};

// The record answers the card's read is steered through, the Push to
// ERP screen's shapes (issue1529's payloads): the rates must not leak
// into the line — the percentage is worked out from the counts.
const RECORD_READ = {
  reorder_points: { pushed: 5, read: 4, unchanged: 3, rate: 0.75 },
  buffers: { pushed: 3, read: 2, unchanged: 1, rate: 0.5 },
};

// FROM THE COUNTS: the rate is 0.5 but unchanged over read is 10% —
// the line must read the counts' percentage.
const RECORD_FROM_COUNTS = {
  reorder_points: { pushed: 10, read: 10, unchanged: 1, rate: 0.5 },
  buffers: { pushed: 0, read: 0, unchanged: 0, rate: null },
};

// NOT READ: pushed, but no sync has read them back yet.
const RECORD_NOT_READ = {
  reorder_points: { pushed: 2, read: 0, unchanged: 0, rate: null },
  buffers: { pushed: 2, read: 0, unchanged: 0, rate: null },
};

// NONE: nothing was pushed of the reorder-point kind — no line, no
// block.
const RECORD_NONE = {
  reorder_points: { pushed: 0, read: 0, unchanged: 0, rate: null },
  buffers: { pushed: 0, read: 0, unchanged: 0, rate: null },
};

// The stockout and excess proposals answers, kept so the other cards'
// blocks stay up while the drift card is steered through its cases.
const FIRST = {
  accepted: 7,
  stop_replenishing: { proposed: 2, chosen: 1 },
  reduce: { proposed: 2, chosen: 1 },
  dispose: { proposed: 1, chosen: 1 },
  total: { proposed: 5, chosen: 3 },
  without_proposal: 2,
};

// A small known summary so the value tiles render real figures:
// accepted 3400 = 1200 + 800 + 1400; applied 1000 (29% of accepted);
// outstanding 2400 = 1200 + 800 + 400.
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
// B 1, C 1, unclassified 3 — the card renders, so the Exceptions
// today card (and the Supply this week card) have their neighbours to
// follow.
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

// The Order plan's answer: one acumatica supplier group with one
// line — the Order plan view renders for the link check and, for an
// admin, would carry its Release button the viewer must not see.
function planPayload() {
  return {
    as_of: '2026-11-02',
    horizon_days: 7,
    line_count: 1,
    total_value: '114.00',
    suppliers: [
      {
        supplier_code: 'SUP-A',
        source_system: 'acumatica',
        vendor_code: 'V-100',
        name: 'Acme Fasteners',
        line_count: 1,
        total_value: '114.00',
        lines: [
          {
            item_code: 'ITEM-A',
            site_code: 'MAIN',
            position_units: 4,
            reorder_point_units: 20,
            daily_demand: 3,
            due_in_days: 0,
            order_by: '2026-11-02',
            lead_time_days: 5,
            arrives_on: '2026-11-07',
            raw_quantity: 10,
            quantity: 12,
            adjustments: ['raised_to_minimum', 'rounded_to_lot'],
            short_of_need: false,
            terms: { min_order_qty: 10, lot_size: 6, max_order_qty: null },
            unit_cost: 9.5,
            value: 114,
            arithmetic: 'raw 10, raised to the minimum of 10, rounded up to the lot of 12',
            supplier_code: 'SUP-A',
            source_system: 'acumatica',
          },
        ],
      },
    ],
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

// Mock every /api/* call the app makes — issue1413's handler with
// its one new branch: /api/parameter-writeback/record counts each
// request in ctrl.recordGets, records its query string in
// ctrl.recordQueries and answers ctrl.recordAnswer with the status
// ctrl.recordStatus (200 by default; any other status answers
// {"detail": "Internal Server Error"}). The acceptance branch stays
// as it is, answering 1413's FULL answer unless a case says
// otherwise; the stockout and excess proposals branches stay as they
// are, answering their FIRST answers throughout. Every request's
// method and path also lands in ctrl.requests, and the handler reads
// ctrl on every request, so the test can steer the answers and the
// sign-in (ctrl.config / ctrl.me) when the pass switches from the
// admin planner to the non-admin viewer.
async function mockApi(page) {
  const ctrl = {
    config: DEV_CONFIG,
    me: PLANNER_ME,
    abcAnswer: abcPayload(),
    exceptionsAnswer: exceptionsPayload(),
    summaryAnswer: summaryPayload(),
    summaryStatus: 200,
    summaryGets: 0,
    proposalsAnswer: FIRST,
    proposalsStatus: 200,
    proposalsGets: 0,
    proposalQueries: [],
    excessProposalsAnswer: FIRST,
    excessProposalsStatus: 200,
    excessProposalsGets: 0,
    excessProposalsQueries: [],
    driftAcceptanceAnswer: FULL,
    driftAcceptanceStatus: 200,
    driftAcceptanceGets: 0,
    driftAcceptanceQueries: [],
    recordAnswer: RECORD_READ,
    recordStatus: 200,
    recordGets: 0,
    recordQueries: [],
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
      ctrl.driftAcceptanceGets += 1;
      ctrl.driftAcceptanceQueries.push(url.search);
      if (ctrl.driftAcceptanceStatus !== 200) {
        return route.fulfill({
          status: ctrl.driftAcceptanceStatus,
          json: { detail: 'Internal Server Error' },
        });
      }
      return route.fulfill({ status: 200, json: ctrl.driftAcceptanceAnswer });
    }
    if (path === '/api/parameter-writeback/record') {
      ctrl.recordGets += 1;
      ctrl.recordQueries.push(url.search);
      if (ctrl.recordStatus !== 200) {
        return route.fulfill({
          status: ctrl.recordStatus,
          json: { detail: 'Internal Server Error' },
        });
      }
      return route.fulfill({ status: 200, json: ctrl.recordAnswer });
    }
    if (path === '/api/stockout/proposals') {
      ctrl.proposalsGets += 1;
      ctrl.proposalQueries.push(url.search);
      if (ctrl.proposalsStatus !== 200) {
        return route.fulfill({
          status: ctrl.proposalsStatus,
          json: { detail: 'Internal Server Error' },
        });
      }
      return route.fulfill({ status: 200, json: ctrl.proposalsAnswer });
    }
    if (path === '/api/excess/proposals') {
      ctrl.excessProposalsGets += 1;
      ctrl.excessProposalsQueries.push(url.search);
      if (ctrl.excessProposalsStatus !== 200) {
        return route.fulfill({
          status: ctrl.excessProposalsStatus,
          json: { detail: 'Internal Server Error' },
        });
      }
      return route.fulfill({ status: 200, json: ctrl.excessProposalsAnswer });
    }
    if (path === '/api/abc') {
      return route.fulfill({ status: 200, json: ctrl.abcAnswer });
    }
    if (path === '/api/aggregate/releases') {
      return route.fulfill({ status: 200, json: releasesPayload() });
    }
    if (path === '/api/exceptions') {
      return route.fulfill({ status: 200, json: ctrl.exceptionsAnswer });
    }
    if (path === '/api/supply-plan/summary') {
      ctrl.summaryGets += 1;
      if (ctrl.summaryStatus !== 200) {
        return route.fulfill({
          status: ctrl.summaryStatus,
          json: { detail: 'Internal Server Error' },
        });
      }
      return route.fulfill({ status: 200, json: ctrl.summaryAnswer });
    }
    if (path === '/api/supply-plan') {
      return route.fulfill({ status: 200, json: planPayload() });
    }
    if (path === '/api/supply-plan/transfers') {
      return route.fulfill({ status: 200, json: { suggestions: [] } });
    }
    if (path === '/api/decisions/summary') {
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

// Mark the figures stale and take the Overview off and on again: the
// next activation re-requests every region, the Reorder points
// card's own reads (the acceptance and the record) included.
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

test('issue1534 freeform', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  /* ---------------- planner pass: development sign-in ------------------ */
  page.setViewportSize({ width: 1440, height: 900 });
  const { ctrl, appOrigin, handler } = await mockApi(page);

  const driftRegion = page.locator('#ov-region-card-drift');
  const block = page.locator('[data-testid="ov-drift-pushes"]');
  const blockLine = page.locator('[data-testid="ov-drift-pushes-line"]');
  const acceptanceBlock = page.locator('[data-testid="ov-drift-acceptance"]');
  const excessBlock = page.locator('[data-testid="ov-excess-proposals"]');
  const stockoutBlock = page.locator('[data-testid="ov-stockout-proposals"]');

  // The "throughout" check: after each case the Reorder points card
  // holds no skeleton, no page error was raised, and every request
  // in ctrl.requests is a GET.
  async function assertSettled() {
    expect(await driftRegion.locator('.skel').count()).toBe(0);
    expect(pageErrors).toEqual([]);
    expect(ctrl.requests.length).toBeGreaterThan(0);
    expect(ctrl.requests.every((request) => request.method === 'GET')).toBe(true);
  }

  // The block's placement: exactly one block on the page, inside the
  // Reorder points card, classed ov-proposals like the card's
  // acceptance block, its previous element sibling the given element
  // (the acceptance block when it shows, else the card's figures row)
  // and its next the card's .ov-open button; the other list cards
  // hold none.
  async function assertPlacement(prevSelector) {
    const placement = await page.evaluate((prev) => {
      const region = document.getElementById('ov-region-card-drift');
      const el = document.querySelector('#ov-region-card-drift [data-testid="ov-drift-pushes"]');
      const prevEl = prev ? region.querySelector(prev) : null;
      return {
        insideRegion: !!el,
        className: el ? el.className : '',
        prevIsExpected: !!(el && prevEl && el.previousElementSibling === prevEl),
        nextIsOpen: !!(el && region && el.nextElementSibling === region.querySelector('.ov-open')),
        excessHas: !!document.querySelector(
          '#ov-region-card-excess [data-testid="ov-drift-pushes"]',
        ),
        stockoutHas: !!document.querySelector(
          '#ov-region-card-stockout [data-testid="ov-drift-pushes"]',
        ),
      };
    }, prevSelector);
    expect(placement.insideRegion).toBe(true);
    expect(placement.className).toBe('ov-proposals');
    expect(placement.prevIsExpected).toBe(true);
    expect(placement.nextIsOpen).toBe(true);
    expect(placement.excessHas).toBe(false);
    expect(placement.stockoutHas).toBe(false);
  }

  // The card renders exactly as before: its title, its "N to decide"
  // count, its three figures and its Open button.
  async function assertCardAsBefore() {
    await expect(driftRegion.locator('.ov-card-title')).toHaveText('Reorder points');
    await expect(driftRegion.locator('.ov-card-to-decide')).toHaveText('0 to decide');
    await expect(driftRegion.locator('.ov-figure-value').nth(0)).toHaveText('$1,200');
    await expect(driftRegion.locator('.ov-figure-value').nth(1)).toHaveText('$0');
    await expect(driftRegion.locator('.ov-figure-value').nth(2)).toHaveText('0');
    await expect(driftRegion.locator('.ov-open')).toHaveText('Open Reorder points list');
  }

  await page.goto('/');

  // Sign in through the development token card as the admin planner,
  // the way the existing Overview specs do. The Overview is the
  // default view, so the Reorder points card's region — its summary,
  // its list, its acceptance read and its record read — loads the
  // first time the view becomes active.
  await expect(page.locator('#signin-form')).toBeVisible();
  await page.fill('#token-input', 'issue1534-dev-token');
  await page.click('#signin-button');
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('planner1');

  // --- Case 1: READ — the read and the block, part way read back ------
  // One Overview load sends exactly one record read, as a GET with no
  // query string.
  await expect.poll(() => ctrl.recordGets).toBe(1);
  expect(ctrl.recordQueries[0]).toBe('');

  await expect(block).toBeVisible();
  await expect(blockLine).toHaveText(
    'Reorder points still as pushed in the ERP: 3 of 4 read back (75%)',
  );
  expect(await block.count()).toBe(1);

  // The block sits after the acceptance block (1413's FULL answer)
  // and directly before the card's .ov-open button.
  await expect(acceptanceBlock).toBeVisible();
  await assertPlacement('[data-testid="ov-drift-acceptance"]');

  // The other list cards still show their own blocks, and the
  // Reorder points card's own content is unchanged by the new block.
  await expect(stockoutBlock).toBeVisible();
  await expect(excessBlock).toBeVisible();
  await assertCardAsBefore();
  await assertSettled();

  // --- Case 2: FROM THE COUNTS — the percentage from the counts -------
  // The rate is 0.5 but unchanged over read is 10%: the line reads
  // the counts' percentage, never the rate.
  ctrl.recordAnswer = RECORD_FROM_COUNTS;
  const getsBeforeFromCounts = ctrl.recordGets;
  await reactivateOverview(page);
  await expect.poll(() => ctrl.recordGets).toBe(getsBeforeFromCounts + 1);
  await expect(blockLine).toHaveText(
    'Reorder points still as pushed in the ERP: 1 of 10 read back (10%)',
  );
  await assertPlacement('[data-testid="ov-drift-acceptance"]');
  await assertSettled();

  // --- Case 3: NOT READ — pushed, but no sync has read back yet -------
  ctrl.recordAnswer = RECORD_NOT_READ;
  const getsBeforeNotRead = ctrl.recordGets;
  await reactivateOverview(page);
  await expect.poll(() => ctrl.recordGets).toBe(getsBeforeNotRead + 1);
  await expect(blockLine).toHaveText('Reorder points pushed: 2; none read back by a sync yet');
  await assertPlacement('[data-testid="ov-drift-acceptance"]');
  await assertSettled();

  // --- Case 4: NONE — nothing pushed, no block -------------------------
  ctrl.recordAnswer = RECORD_NONE;
  const getsBeforeNone = ctrl.recordGets;
  await reactivateOverview(page);
  await expect.poll(() => ctrl.recordGets).toBe(getsBeforeNone + 1);
  expect(await block.count()).toBe(0);
  await expect(acceptanceBlock).toBeVisible();
  await assertCardAsBefore();
  await assertSettled();

  // --- Case 5: without the acceptance block ----------------------------
  // The acceptance read answers 500, so its block is out: the pushes
  // block sits directly after .ov-card-figures and directly before
  // .ov-open.
  ctrl.recordAnswer = RECORD_READ;
  ctrl.driftAcceptanceStatus = 500;
  const getsBeforeNoAcceptance = ctrl.recordGets;
  await reactivateOverview(page);
  await expect.poll(() => ctrl.recordGets).toBe(getsBeforeNoAcceptance + 1);
  expect(await acceptanceBlock.count()).toBe(0);
  await expect(block).toBeVisible();
  await expect(blockLine).toHaveText(
    'Reorder points still as pushed in the ERP: 3 of 4 read back (75%)',
  );
  await assertPlacement('.ov-card-figures');
  await assertCardAsBefore();
  await assertSettled();
  ctrl.driftAcceptanceStatus = 200;
  ctrl.driftAcceptanceAnswer = FULL;

  // --- Case 6: Fails — no block, the card renders exactly as before ---
  // In each case the card's figures, its acceptance block and its
  // Open button render as before, and the card holds no
  // [role="alert"], no error text and no Retry.
  const failCases = [
    // (a) A 500 answer.
    () => {
      ctrl.recordStatus = 500;
    },
    // (b) A body of {}.
    () => {
      ctrl.recordStatus = 200;
      ctrl.recordAnswer = {};
    },
    // (c) reorder_points with unchanged 5 above its read 4.
    () => {
      ctrl.recordStatus = 200;
      ctrl.recordAnswer = {
        reorder_points: { pushed: 5, read: 4, unchanged: 5, rate: null },
        buffers: RECORD_READ.buffers,
      };
    },
  ];
  for (const steer of failCases) {
    steer();
    const getsBeforeFail = ctrl.recordGets;
    await reactivateOverview(page);
    await expect.poll(() => ctrl.recordGets).toBe(getsBeforeFail + 1);
    expect(await block.count()).toBe(0);
    await expect(acceptanceBlock).toBeVisible();
    await assertCardAsBefore();
    expect(await driftRegion.locator('[role="alert"]').count()).toBe(0);
    expect(await driftRegion.locator('.ov-error').count()).toBe(0);
    expect(await driftRegion.locator('.ov-retry').count()).toBe(0);
    await assertSettled();
  }

  // --- Case 7: phone width — the block, no sideways scroll -------------
  ctrl.recordStatus = 200;
  ctrl.recordAnswer = RECORD_READ;
  const getsBeforePhone = ctrl.recordGets;
  await reactivateOverview(page);
  await expect.poll(() => ctrl.recordGets).toBe(getsBeforePhone + 1);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(block).toBeVisible();
  await expect(blockLine).toHaveText(
    'Reorder points still as pushed in the ERP: 3 of 4 read back (75%)',
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await assertSettled();

  // Every record read the run sent was a GET with no query string.
  expect(ctrl.recordQueries.every((query) => query === '')).toBe(true);
  expect(ctrl.requests.every((request) => request.method === 'GET')).toBe(true);

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
  ctrl.driftAcceptanceStatus = 200;
  ctrl.driftAcceptanceAnswer = FULL;
  ctrl.recordStatus = 200;
  ctrl.recordAnswer = RECORD_READ;
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
  await page.goto('/index.html#access_token=viewer-token-1534');

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
  // ... and the Reorder points card's pushes block loaded for the
  // viewer with the same line the admin saw.
  await expect(block).toBeVisible();
  await expect(blockLine).toHaveText(
    'Reorder points still as pushed in the ERP: 3 of 4 read back (75%)',
  );
  await assertSettled();

  // The admin-only controls are absent while the page still renders:
  // the Order plan shows its summary for the viewer, but the Release
  // controls do not render at all.
  await page.click('.nav-btn[data-view="orders"]');
  await expect(page).toHaveURL(/#\/orders$/);
  await expect(page.locator('#view-orders')).toBeVisible();
  await expect(page.locator('[data-testid="orders-summary"]')).toBeVisible();
  await expect(page.locator('[data-testid="orders-release"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="orders-transfer-release"]')).toHaveCount(0);
  expect(pageErrors).toEqual([]);
  expect(ctrl.requests.every((request) => request.method === 'GET')).toBe(true);
});
