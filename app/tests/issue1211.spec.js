// Proving spec for AIERP issue #1211 — the "Standing grants" section
// on the Releases screen (#/releases), below the Transfer orders
// section.
//
// The section ([data-testid="releases-grants"]) is headed
// "Standing grants" and is visible to every signed-in user. It sends
// no request until its button ([data-testid="releases-grants-show"],
// "Show standing grants") is clicked; each click then sends exactly
// three reads through app/js/api.js — GET /api/standing-grants, GET
// /api/standing-grants/runs?limit=20 and GET /api/settings — and once
// an answer has landed the button reads "Refresh standing grants".
// The settings answer's erp.standing_grants_enabled entry drives the
// switch line ([data-testid="releases-grants-switch"]): "Standing
// grants are on." when its value is true, "Standing grants are off:
// every write to the ERP needs a person's approval." when it is
// false, absent when the entry is missing. A failed grants read or
// runs read shows "Standing grants could not be loaded." in
// [data-testid="releases-grants-error"] and leaves the rest of the
// screen unchanged; a failed settings read only leaves out the switch
// line.
//
// The grants table ([data-testid="releases-grants-table"]) has one
// row ([data-testid="releases-grant"]) per grant, in the answer's
// order: Process ("Order plan" for order_plan_release, "Transfers"
// for transfer_release), Per order and Per day (dollars and cents),
// Data age ("<hours> h"), Created (the date of created_at, then
// "by <created_by>" on a second line) and State ("In force", or
// "Revoked <date of revoked_at> by <revoked_by>"); with no grant it
// reads "No standing grant yet.". The runs table
// ([data-testid="releases-grant-runs"]) has one row
// ([data-testid="releases-grant-run"]) per run, in the answer's
// order: Started (started_at in UTC as YYYY-MM-DD HH:MM), Process,
// Outcome (a badge reading "Released", "Nothing due", "Skipped" or
// "Failed", with "Failed" in the risk tokens), Sent ("<documents>
// order(s), <value in dollars and cents>", an em dash when documents
// is 0), Held back (the number of left_out entries, an em dash when
// there are none) and Reason (an em dash without one); with no run
// it reads "No runs yet.".
//
// An admin — and only an admin, the role the shell gates
// [data-admin-only] with — sees, below the grants table, the create
// form ([data-testid="releases-grant-form"]): a process select
// ([data-testid="releases-grant-process"]) with "Order plan" and
// "Transfers", number inputs for the limit per order, the limit per
// day and the data age in hours (1 to 168), and a "Create grant"
// button. Submitting sends one POST /api/standing-grants with the
// four fields as numbers: a 201 sends the three reads again, a 409
// reads "A grant for this process is already in force. Revoke it
// first.", a 422 reads "Check the limits: the daily limit must not
// be below the per-order limit, and the data age must be from 1 to
// 168 hours.", a 403 reads "Only an admin can create a grant.", any
// other failure reads "The grant could not be created.", and a value
// the inputs cannot hold (an empty field among them) sends no
// request and reads the 422 message. Each in-force row carries a
// "Revoke" button ([data-testid="releases-grant-revoke"]); a first
// click turns it into "Confirm revoke" plus a "Cancel" button (which
// sends nothing), and confirming sends one POST
// /api/standing-grants/<id>/revoke: a 200, a 409 or a 404 sends the
// three reads again, any other failure reads "The grant could not be
// revoked." beside the row.
//
// The answers, the error and the pending flag live in module state:
// a re-render (the chip filter) keeps them, a sign-out clears them,
// and an answer that lands after a sign-out — or after a newer
// click's answer — is discarded. Every API value is inserted as
// text, never as markup, the colours come only from the design
// tokens, and at 390x844 the section's tables scroll within the
// section with no sideways scroll of the page.
//
// The gate serves the static app with no backend, so every /api/*
// call is mocked with one page.route('**/api/**', handler), the way
// app/tests/issue1170.spec.js does it: the mock keeps the grants'
// store and the runs' answer, counts every read, keeps every create
// body and revoke path, and can fail, delay or hold a single answer.
// The mock also records each read's method, path and query and the
// body each read serves, so the proof pins the required read shape:
// a GET on the exact path — no query on the grants and settings
// reads, limit=20 on the runs read — and the first click's answers
// the exact shapes the section renders.
//
// The pass runs as an ADMIN (roles ["admin"]) signed in through the
// development token card at 1440x900, and it ends as a NON-ADMIN
// (role 'user') after a full reload: the token arrives in the URL
// hash as #access_token=..., /api/auth/config and /api/auth/me are
// mocked for that user, the IdP's auth and token endpoints carry the
// PKCE round-trip, and the section shows both tables — no form, no
// Revoke buttons, no extra column — while the page still renders,
// with no POST sent.
//
// The test is TOP-LEVEL and its title is the full proof name
// ("issue1211 freeform"): the merge gate builds its junit nodeids
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

// The freeform pass walks the on-demand, rendering, form, revoke,
// stale-answer, failure, sign-out and phone-width checks plus the
// production-boot non-admin pass in one test; give slow CI runners
// room without weakening the assertions.
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

// The production sign-in configuration the planner pass boots with.
const PROD_CONFIG = {
  authEnabled: true,
  authMode: 'production',
  issuer: 'http://idp.test/realms/r',
  clientId: 'aierp-app',
  audience: 'aierp-app',
  jwksUrl: 'http://idp.test/realms/r/protocol/openid-connect/certs',
  algorithms: ['RS256'],
};

// The admin: roles ["admin"], the role the screen's other admin
// controls read.
const ADMIN_ME = {
  authenticated: true,
  user: { sub: 'u-admin', username: 'admin1', roles: ['admin'], groups: [] },
};

// The planner: a signed-in non-admin user, the identity requirement's
// audience.
const PLANNER_ME = {
  authenticated: true,
  user: { sub: 'u1', username: 'planner1', roles: ['user'], groups: [] },
};

// The em dash the screen renders for an absent figure.
const EM_DASH = '\u2014';

// The hostile created_by the successful create lands: it must reach
// the page as text, never as markup.
const HOSTILE_CREATOR = '<img src=x onerror=window.__pwned1211=1>';

// The grants' store, in the answer's order: an in-force order-plan
// grant (5,000 per order, 20,000 per day, 26 h) and a revoked
// transfer grant.
function freshGrants() {
  return [
    {
      id: 2,
      process: 'order_plan_release',
      max_document_value: 5000,
      max_daily_value: 20000,
      max_data_age_hours: 26,
      created_at: '2026-11-12T09:15:00+00:00',
      created_by: 'admin1',
      revoked_at: null,
      revoked_by: null,
      in_force: true,
    },
    {
      id: 1,
      process: 'transfer_release',
      max_document_value: 1500,
      max_daily_value: 9000,
      max_data_age_hours: 12,
      created_at: '2026-11-02T14:00:00+00:00',
      created_by: 'admin1',
      revoked_at: '2026-11-10T08:30:00+00:00',
      revoked_by: 'admin1',
      in_force: false,
    },
  ];
}

// The runs' answer, in the answer's order: a failed run (22:05 at
// +02:00 is 20:05 UTC), a released run (two documents, 1,563.50, one
// group left out) and a nothing-due run.
function freshRuns() {
  return [
    {
      id: 31,
      process: 'order_plan_release',
      grant_id: 2,
      started_at: '2026-11-14T22:05:00+02:00',
      outcome: 'failed',
      reason: 'Acumatica writes are disabled',
      documents: 0,
      value: 0,
      left_out: [],
    },
    {
      id: 32,
      process: 'order_plan_release',
      grant_id: 2,
      started_at: '2026-11-14T18:40:00+00:00',
      outcome: 'released',
      reason: null,
      documents: 2,
      value: 1563.5,
      left_out: [
        {
          site_code: 'RETAIL',
          supplier_code: 'ELEEASTCOM',
          value: 5200,
          reason: 'daily_limit',
        },
      ],
    },
    {
      id: 33,
      process: 'transfer_release',
      grant_id: 1,
      started_at: '2026-11-13T06:00:00+00:00',
      outcome: 'nothing_due',
      reason: null,
      documents: 0,
      value: 0,
      left_out: [],
    },
  ];
}

// The settings read's answer while the switch is on — the shape the
// section requires: an array of { key, value, ... } entries carrying
// the erp.standing_grants_enabled entry (value true) the switch line
// reads.
const SETTINGS_ON_ANSWER = [
  {
    key: 'planning.horizon_weeks',
    scope: 'planning',
    type: 'integer',
    allowed: null,
    min: 1,
    max: 52,
    default: 12,
    description: 'The planning horizon in weeks.',
    value: 12,
    source: 'default',
    editable: true,
    updated_by: null,
    updated_by_name: null,
  },
  {
    key: 'erp.standing_grants_enabled',
    scope: 'erp',
    type: 'boolean',
    allowed: null,
    min: null,
    max: null,
    default: true,
    description: 'Whether the standing grants are enabled.',
    value: true,
    source: 'default',
    editable: true,
    updated_by: null,
    updated_by_name: null,
  },
];

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

// The releases answer: three orders (two open, one received) — the
// table the section must leave unchanged.
function releasesPayload() {
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

// The grants table's rows as plain data, in the table's order.
const ORDER_ROW = {
  id: '2',
  process: 'Order plan',
  perOrder: '$5,000.00',
  perDay: '$20,000.00',
  dataAge: '26 h',
  createdDate: '2026-11-12',
  createdBy: ' by admin1',
  state: 'In force',
  revokeText: 'Revoke',
};

const TRANSFER_ROW = {
  id: '1',
  process: 'Transfers',
  perOrder: '$1,500.00',
  perDay: '$9,000.00',
  dataAge: '12 h',
  createdDate: '2026-11-02',
  createdBy: ' by admin1',
  state: 'Revoked 2026-11-10 by admin1',
  revokeText: null,
};

// The same rows the non-admin sees: no Revoke column, no buttons.
const ORDER_ROW_VIEWER = { ...ORDER_ROW, revokeText: null };
const TRANSFER_ROW_VIEWER = TRANSFER_ROW;

const FAILED_RUN_ROW = {
  started: '2026-11-14 20:05',
  process: 'Order plan',
  outcome: 'Failed',
  sent: EM_DASH,
  heldBack: EM_DASH,
  reason: 'Acumatica writes are disabled',
};

const RELEASED_RUN_ROW = {
  started: '2026-11-14 18:40',
  process: 'Order plan',
  outcome: 'Released',
  sent: '2 order(s), $1,563.50',
  heldBack: '1',
  reason: EM_DASH,
};

const NOTHING_DUE_RUN_ROW = {
  started: '2026-11-13 06:00',
  process: 'Transfers',
  outcome: 'Nothing due',
  sent: EM_DASH,
  heldBack: EM_DASH,
  reason: EM_DASH,
};

// Mock every /api/* call the app makes with ONE route handler,
// defined before it is registered with page.route. The handler reads
// ctrl on every request, so the test can steer the sign-in
// (ctrl.config / ctrl.me), the settings entry (ctrl.settingsEnabled,
// a boolean or null to keep the entry out of the answer), the store
// (ctrl.grantStore, ctrl.runsAnswer) and the single answers (the
// one-shot *FailNext failures, the held create, the delayed reads).
// Every read is counted, every create body and revoke path is kept,
// each read's method, path and query are kept per route (
// ctrl.grantsRequests, ctrl.runsRequests, ctrl.settingsRequests )
// and each read's served body in ctrl.grantsBodies / ctrl.runsBodies
// / ctrl.settingsBodies — the required read shape the proof pins.
// Each request's method and path also land in ctrl.requests.
async function mockGrantsApi(page) {
  const ctrl = {
    config: DEV_CONFIG,
    me: ADMIN_ME,
    settingsEnabled: true,
    grantStore: freshGrants(),
    runsAnswer: freshRuns(),
    createdBy: 'admin1',
    nextId: 100,
    grantsGets: 0,
    runsGets: 0,
    settingsGets: 0,
    releasesGets: 0,
    grantsFulfilled: 0,
    runsFulfilled: 0,
    meRequests: [],
    requests: [],
    createPosts: [],
    revokePosts: [],
    runsRequests: [],
    grantsRequests: [],
    settingsRequests: [],
    grantsBodies: [],
    runsBodies: [],
    settingsBodies: [],
    grantsFailNext: null,
    runsFailNext: null,
    settingsFailNext: null,
    createFailNext: null,
    revokeFailNext: null,
    createHoldNext: false,
    heldCreate: null,
    grantsDelayNextMs: 0,
    runsDelayMs: 0,
  };
  const appOrigin = { value: null };

  // The settings answer, in the shape SETTINGS_ON_ANSWER pins: the
  // neutral entry, plus the erp.standing_grants_enabled entry while
  // ctrl.settingsEnabled holds a boolean (its value).
  const settingsAnswer = () => {
    const entries = [{ ...SETTINGS_ON_ANSWER[0] }];
    if (typeof ctrl.settingsEnabled === 'boolean') {
      entries.push({ ...SETTINGS_ON_ANSWER[1], value: ctrl.settingsEnabled });
    }
    return entries;
  };

  const handler = (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (appOrigin.value === null) {
      appOrigin.value = url.origin;
    }
    const path = url.pathname;
    ctrl.requests.push({ method: request.method(), path, search: url.search });
    if (path === '/api/auth/config') {
      return route.fulfill({ status: 200, json: ctrl.config });
    }
    if (path === '/api/auth/me') {
      ctrl.meRequests.push(request);
      return route.fulfill({ status: 200, json: ctrl.me });
    }
    if (path === '/api/standing-grants' && request.method() === 'GET') {
      ctrl.grantsGets += 1;
      ctrl.grantsRequests.push(request);
      if (ctrl.grantsFailNext) {
        const fail = ctrl.grantsFailNext;
        ctrl.grantsFailNext = null;
        ctrl.grantsFulfilled += 1;
        return route.fulfill(fail);
      }
      const body = ctrl.grantStore.map((row) => ({ ...row }));
      const fulfill = () => {
        ctrl.grantsFulfilled += 1;
        ctrl.grantsBodies.push(body);
        route.fulfill({ status: 200, json: body });
      };
      if (ctrl.grantsDelayNextMs > 0) {
        const delay = ctrl.grantsDelayNextMs;
        ctrl.grantsDelayNextMs = 0;
        setTimeout(fulfill, delay);
        return undefined;
      }
      return fulfill();
    }
    if (path === '/api/standing-grants' && request.method() === 'POST') {
      const body = request.postDataJSON();
      ctrl.createPosts.push(body);
      if (ctrl.createFailNext) {
        const fail = ctrl.createFailNext;
        ctrl.createFailNext = null;
        return route.fulfill(fail);
      }
      if (ctrl.createHoldNext) {
        // Held: the test answers the route itself.
        ctrl.createHoldNext = false;
        ctrl.heldCreate = { route, body };
        return undefined;
      }
      const row = {
        id: ctrl.nextId++,
        process: body.process,
        max_document_value: body.max_document_value,
        max_daily_value: body.max_daily_value,
        max_data_age_hours: body.max_data_age_hours,
        created_at: '2026-11-15T10:00:00+00:00',
        created_by: ctrl.createdBy,
        revoked_at: null,
        revoked_by: null,
        in_force: true,
      };
      ctrl.grantStore.unshift(row);
      return route.fulfill({ status: 201, json: { ...row } });
    }
    if (path === '/api/standing-grants/runs' && request.method() === 'GET') {
      ctrl.runsGets += 1;
      ctrl.runsRequests.push(request);
      if (ctrl.runsFailNext) {
        const fail = ctrl.runsFailNext;
        ctrl.runsFailNext = null;
        ctrl.runsFulfilled += 1;
        return route.fulfill(fail);
      }
      const body = ctrl.runsAnswer.map((row) => ({ ...row }));
      const fulfill = () => {
        ctrl.runsFulfilled += 1;
        ctrl.runsBodies.push(body);
        route.fulfill({ status: 200, json: body });
      };
      if (ctrl.runsDelayMs > 0) {
        setTimeout(fulfill, ctrl.runsDelayMs);
        return undefined;
      }
      return fulfill();
    }
    const revokeMatch = path.match(/^\/api\/standing-grants\/(\d+)\/revoke$/);
    if (revokeMatch && request.method() === 'POST') {
      const id = Number(revokeMatch[1]);
      ctrl.revokePosts.push(path);
      if (ctrl.revokeFailNext) {
        const fail = ctrl.revokeFailNext;
        ctrl.revokeFailNext = null;
        return route.fulfill(fail);
      }
      const row = ctrl.grantStore.find((grant) => grant.id === id);
      if (!row) {
        return route.fulfill({
          status: 404,
          json: { detail: 'Unknown standing grant' },
        });
      }
      if (row.revoked_at !== null) {
        return route.fulfill({
          status: 409,
          json: { detail: 'The grant is already revoked' },
        });
      }
      row.revoked_at = '2026-11-15T11:00:00+00:00';
      row.revoked_by = 'admin1';
      row.in_force = false;
      return route.fulfill({ status: 200, json: { ...row } });
    }
    if (path === '/api/settings') {
      ctrl.settingsGets += 1;
      ctrl.settingsRequests.push(request);
      if (ctrl.settingsFailNext) {
        const fail = ctrl.settingsFailNext;
        ctrl.settingsFailNext = null;
        return route.fulfill(fail);
      }
      const body = settingsAnswer();
      ctrl.settingsBodies.push(body);
      return route.fulfill({ status: 200, json: body });
    }
    if (path === '/api/aggregate/releases') {
      ctrl.releasesGets += 1;
      return route.fulfill({ status: 200, json: releasesPayload() });
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

test('issue1211 freeform', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  const section = page.locator('[data-testid="releases-grants"]');
  const showButton = page.locator('[data-testid="releases-grants-show"]');
  const switchLine = page.locator('[data-testid="releases-grants-switch"]');
  const sectionError = page.locator('[data-testid="releases-grants-error"]');
  const grantsTable = page.locator('[data-testid="releases-grants-table"]');
  const grantRows = page.locator('[data-testid="releases-grant"]');
  const runsTable = page.locator('[data-testid="releases-grant-runs"]');
  const runRows = page.locator('[data-testid="releases-grant-run"]');
  const form = page.locator('[data-testid="releases-grant-form"]');
  const processSelect = page.locator('[data-testid="releases-grant-process"]');
  const perOrderInput = page.locator('[data-testid="releases-grant-per-order"]');
  const perDayInput = page.locator('[data-testid="releases-grant-per-day"]');
  const dataAgeInput = page.locator('[data-testid="releases-grant-data-age"]');
  const createButton = page.locator('[data-testid="releases-grant-create"]');
  const formError = page.locator('[data-testid="releases-grant-form-error"]');
  const revokeButtons = page.locator('[data-testid="releases-grant-revoke"]');
  const releasesTable = page.locator('[data-testid="releases-table"]');
  const rlRows = page.locator('tr.rl-row');

  // The section's three reads as one counter triple, and the check
  // that a click sent exactly one of each.
  let ctrl = null;
  let handler = null;
  let appOrigin = null;
  const reads = () => [ctrl.grantsGets, ctrl.runsGets, ctrl.settingsGets];
  async function expectOneReadOfEach(before) {
    await expect.poll(() => ctrl.grantsGets).toBe(before[0] + 1);
    await expect.poll(() => ctrl.runsGets).toBe(before[1] + 1);
    await expect.poll(() => ctrl.settingsGets).toBe(before[2] + 1);
  }

  // The grants' rows as plain data, in the table's order.
  const grantRowData = () =>
    grantRows.evaluateAll((rowElements) =>
      rowElements.map((row) => {
        const cells = Array.from(row.querySelectorAll('td'));
        return {
          id: row.getAttribute('data-grant-id'),
          process: cells[0].textContent,
          perOrder: cells[1].textContent,
          perDay: cells[2].textContent,
          dataAge: cells[3].textContent,
          createdDate: row.querySelector('.rl-grant-created-date')
            ? row.querySelector('.rl-grant-created-date').textContent
            : null,
          createdBy: row.querySelector('.rl-grant-created-by')
            ? row.querySelector('.rl-grant-created-by').textContent
            : null,
          state: row.querySelector('.rl-grant-state').textContent,
          revokeText: row.querySelector('[data-testid="releases-grant-revoke"]')
            ? row.querySelector('[data-testid="releases-grant-revoke"]').textContent
            : null,
        };
      }),
    );

  // The runs' rows as plain data, in the table's order.
  const runRowData = () =>
    runRows.evaluateAll((rowElements) =>
      rowElements.map((row) => {
        const cells = Array.from(row.querySelectorAll('td'));
        return {
          started: cells[0].textContent,
          process: cells[1].textContent,
          outcome: row.querySelector('[data-testid="releases-grant-run-outcome"]').textContent,
          sent: cells[3].textContent,
          heldBack: cells[4].textContent,
          reason: cells[5].textContent,
        };
      }),
    );

  // --- admin pass: development sign-in at 1440x900 -------------------
  page.setViewportSize({ width: 1440, height: 900 });
  ({ ctrl, handler, appOrigin } = await mockGrantsApi(page));

  await page.goto('/');

  // No read of the section's answers before the sign-in completes.
  await expect(page.locator('#signin-form')).toBeVisible();
  expect(ctrl.grantsGets).toBe(0);
  expect(ctrl.runsGets).toBe(0);
  expect(ctrl.settingsGets).toBe(0);

  // Sign in through the development token card as the admin.
  await page.fill('#token-input', 'dev-admin-token');
  await page.click('#signin-button');
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('admin1');
  const adminMe = ctrl.meRequests[ctrl.meRequests.length - 1];
  expect(adminMe.headers()['authorization']).toBe('Bearer dev-admin-token');
  expect(pageErrors).toEqual([]);

  // --- opening the view: the section shows, no grants request -------
  await page.click('.nav-btn[data-view="releases"]');
  await expect(page).toHaveURL(/#\/releases$/);
  await expect(page.locator('#view-releases')).toBeVisible();
  await expect(releasesTable).toBeVisible();
  await expect(rlRows).toHaveCount(3);
  // The section sits below the Transfer orders section, headed
  // "Standing grants", with its button ...
  await expect(section).toBeVisible();
  await expect(section.locator('.rl-grants-title')).toHaveText('Standing grants');
  const sectionPlace = await page.evaluate(() => {
    const transfers = document.querySelector('[data-testid="releases-transfers"]');
    const grants = document.querySelector('[data-testid="releases-grants"]');
    return {
      afterTransfers: !!(transfers && grants && transfers.nextElementSibling === grants),
    };
  });
  expect(sectionPlace).toEqual({ afterTransfers: true });
  await expect(showButton).toHaveText('Show standing grants');
  // ... and opening the view sent no request to the grants routes or
  // the settings beyond what it sends today (nothing).
  expect(ctrl.grantsGets).toBe(0);
  expect(ctrl.runsGets).toBe(0);
  expect(ctrl.settingsGets).toBe(0);
  expect(pageErrors).toEqual([]);

  // --- one click: exactly the three reads -----------------------------
  let before = reads();
  await showButton.click();
  await expectOneReadOfEach(before);
  // The click's three reads landed in the shapes the section
  // requires: the grants read a bare array of grant rows, the runs
  // read a bare array of run rows, the settings read an array of
  // { key, value } entries carrying the erp.standing_grants_enabled
  // entry (value true).
  await expect.poll(() => ctrl.grantsBodies.length).toBe(1);
  await expect.poll(() => ctrl.runsBodies.length).toBe(1);
  await expect.poll(() => ctrl.settingsBodies.length).toBe(1);
  expect(ctrl.grantsBodies[0]).toEqual(freshGrants());
  expect(ctrl.runsBodies[0]).toEqual(freshRuns());
  expect(ctrl.settingsBodies[0]).toEqual(SETTINGS_ON_ANSWER);
  // An answer has landed: the button refreshes.
  await expect(showButton).toHaveText('Refresh standing grants');
  // The switch line, from the settings answer's entry (value true).
  await expect(switchLine).toHaveText('Standing grants are on.');
  await expect(sectionError).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // The grants table: one row per grant, in the answer's order.
  await expect(grantsTable).toBeVisible();
  await expect(grantRows).toHaveCount(2);
  expect(await grantRowData()).toEqual([ORDER_ROW, TRANSFER_ROW]);
  expect(pageErrors).toEqual([]);

  // The runs table: one row per run, in the answer's order.
  await expect(runsTable).toBeVisible();
  await expect(runRows).toHaveCount(3);
  expect(await runRowData()).toEqual([FAILED_RUN_ROW, RELEASED_RUN_ROW, NOTHING_DUE_RUN_ROW]);
  // "Failed" renders in the risk tokens.
  const failedBadge = runRows.nth(0).locator('[data-testid="releases-grant-run-outcome"]');
  const badgeStyle = await failedBadge.evaluate((badge) => {
    const style = getComputedStyle(badge);
    return { color: style.color, background: style.backgroundColor };
  });
  expect(badgeStyle.color).toBe('rgb(138, 59, 5)'); // --color-risk #8A3B05
  expect(badgeStyle.background).toBe('rgb(253, 232, 211)'); // --color-risk-soft #FDE8D3
  expect(pageErrors).toEqual([]);

  // A re-render (the chip filter) keeps the section's answers: the
  // module state survives.
  await page.click('.rl-chip[data-state="received"]');
  await expect(rlRows).toHaveCount(1);
  await expect(grantRows).toHaveCount(2);
  await expect(runRows).toHaveCount(3);
  await expect(showButton).toHaveText('Refresh standing grants');
  await page.click('.rl-chip[data-state="all"]');
  await expect(rlRows).toHaveCount(3);
  expect(pageErrors).toEqual([]);

  // Empty answers: the notes replace the tables, and a successful
  // empty answer keeps the refreshed label.
  ctrl.grantStore = [];
  ctrl.runsAnswer = [];
  before = reads();
  await showButton.click();
  await expectOneReadOfEach(before);
  await expect(page.locator('[data-testid="releases-grants-empty"]')).toHaveText(
    'No standing grant yet.',
  );
  await expect(grantsTable).toHaveCount(0);
  await expect(page.locator('[data-testid="releases-grant-runs-empty"]')).toHaveText(
    'No runs yet.',
  );
  await expect(runsTable).toHaveCount(0);
  await expect(showButton).toHaveText('Refresh standing grants');
  expect(pageErrors).toEqual([]);
  // Restore the answers: the tables come back.
  ctrl.grantStore = freshGrants();
  ctrl.runsAnswer = freshRuns();
  before = reads();
  await showButton.click();
  await expectOneReadOfEach(before);
  await expect(grantRows).toHaveCount(2);
  await expect(runRows).toHaveCount(3);
  expect(await grantRowData()).toEqual([ORDER_ROW, TRANSFER_ROW]);
  expect(pageErrors).toEqual([]);

  // --- the admin's form: below the grants table -----------------------
  await expect(form).toBeVisible();
  const formPlace = await page.evaluate(() => {
    const region = document.querySelector('[data-testid="releases-grants-table"]').closest('.rl-grants-region');
    const grantForm = document.querySelector('[data-testid="releases-grant-form"]');
    return {
      belowTable: !!(region && grantForm && region.nextElementSibling === grantForm),
    };
  });
  expect(formPlace).toEqual({ belowTable: true });
  const optionTexts = await processSelect.locator('option').allTextContents();
  expect(optionTexts).toEqual(['Order plan', 'Transfers']);
  expect(await processSelect.locator('option').nth(0).getAttribute('value')).toBe(
    'order_plan_release',
  );
  expect(await processSelect.locator('option').nth(1).getAttribute('value')).toBe(
    'transfer_release',
  );
  await expect(perOrderInput).toHaveAttribute('type', 'number');
  await expect(perDayInput).toHaveAttribute('type', 'number');
  await expect(dataAgeInput).toHaveAttribute('type', 'number');
  await expect(dataAgeInput).toHaveAttribute('min', '1');
  await expect(dataAgeInput).toHaveAttribute('max', '168');
  await expect(createButton).toHaveText('Create grant');
  await expect(formError).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // Submitting with an empty field sends no request: the 422 message.
  let requestsBefore = ctrl.requests.length;
  await createButton.click();
  await expect(formError).toHaveText(
    'Check the limits: the daily limit must not be below the per-order limit, and the data age must be from 1 to 168 hours.',
  );
  expect(ctrl.createPosts).toEqual([]);
  expect(ctrl.requests.length).toBe(requestsBefore);
  expect(pageErrors).toEqual([]);

  // A 201 create: the exact typed body, as numbers; the three reads
  // again; the new row first, with the hostile created_by as text.
  ctrl.createdBy = HOSTILE_CREATOR;
  await processSelect.selectOption('transfer_release');
  await perOrderInput.fill('2500');
  await perDayInput.fill('10000');
  await dataAgeInput.fill('48');
  before = reads();
  await createButton.click();
  await expect.poll(() => ctrl.createPosts.length).toBe(1);
  expect(ctrl.createPosts[0]).toEqual({
    process: 'transfer_release',
    max_document_value: 2500,
    max_daily_value: 10000,
    max_data_age_hours: 48,
  });
  await expectOneReadOfEach(before);
  await expect(formError).toHaveCount(0);
  const CREATED_ROW = {
    id: '100',
    process: 'Transfers',
    perOrder: '$2,500.00',
    perDay: '$10,000.00',
    dataAge: '48 h',
    createdDate: '2026-11-15',
    createdBy: ' by ' + HOSTILE_CREATOR,
    state: 'In force',
    revokeText: 'Revoke',
  };
  await expect(grantRows).toHaveCount(3);
  expect(await grantRowData()).toEqual([CREATED_ROW, ORDER_ROW, TRANSFER_ROW]);
  // The hostile created_by reached the page as text, never as markup.
  await expect(section.locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => window.__pwned1211)).toBeUndefined();
  expect(pageErrors).toEqual([]);

  // A 409 create: its own message, no further read.
  await processSelect.selectOption('order_plan_release');
  before = reads();
  ctrl.createFailNext = {
    status: 409,
    json: { detail: 'A grant for this process is already in force' },
  };
  await createButton.click();
  await expect.poll(() => ctrl.createPosts.length).toBe(2);
  await expect(formError).toHaveText(
    'A grant for this process is already in force. Revoke it first.',
  );
  expect(ctrl.grantsGets).toBe(before[0]);
  expect(ctrl.runsGets).toBe(before[1]);
  expect(ctrl.settingsGets).toBe(before[2]);
  expect(pageErrors).toEqual([]);

  // A 422 create (a per-day limit below the per-order limit): the
  // 422 message.
  await perOrderInput.fill('5000');
  await perDayInput.fill('4000');
  await dataAgeInput.fill('24');
  ctrl.createFailNext = {
    status: 422,
    json: { detail: 'The daily limit must not be below the per-order limit' },
  };
  await createButton.click();
  await expect.poll(() => ctrl.createPosts.length).toBe(3);
  await expect(formError).toHaveText(
    'Check the limits: the daily limit must not be below the per-order limit, and the data age must be from 1 to 168 hours.',
  );
  expect(pageErrors).toEqual([]);

  // A 403 create: its own message.
  ctrl.createFailNext = { status: 403, json: { detail: 'Only an admin can create a grant' } };
  await createButton.click();
  await expect.poll(() => ctrl.createPosts.length).toBe(4);
  await expect(formError).toHaveText('Only an admin can create a grant.');
  expect(pageErrors).toEqual([]);

  // Any other failure: the generic message.
  ctrl.createFailNext = { status: 500, json: { detail: 'simulated outage' } };
  await createButton.click();
  await expect.poll(() => ctrl.createPosts.length).toBe(5);
  await expect(formError).toHaveText('The grant could not be created.');
  expect(pageErrors).toEqual([]);

  // An answer that lands after a newer click's answer is discarded:
  // a held create's 201 sends the three reads again while the show
  // click's grants read is still out (delayed); the newer batch's
  // answer wins, and the stale one lands late and is dropped.
  const CONCURRENT_ROW = {
    id: '77',
    process: 'Transfers',
    perOrder: '$9,999.00',
    perDay: '$12,000.00',
    dataAge: '72 h',
    createdDate: '2026-11-15',
    createdBy: ' by other-admin',
    state: 'In force',
    revokeText: 'Revoke',
  };
  const CONCURRENT_GRANT = {
    id: 77,
    process: 'transfer_release',
    max_document_value: 9999,
    max_daily_value: 12000,
    max_data_age_hours: 72,
    created_at: '2026-11-15T09:00:00+00:00',
    created_by: 'other-admin',
    revoked_at: null,
    revoked_by: null,
    in_force: true,
  };
  const grantsFulfilledBefore = ctrl.grantsFulfilled;
  ctrl.createHoldNext = true;
  ctrl.grantsDelayNextMs = 900;
  before = reads();
  await createButton.click();
  await expect.poll(() => ctrl.createPosts.length).toBe(6);
  // The show click while the create is out: its grants read is the
  // delayed one.
  await showButton.click();
  await expect.poll(() => ctrl.runsGets).toBe(before[1] + 1);
  await expect.poll(() => ctrl.settingsGets).toBe(before[2] + 1);
  // A concurrent client stores a grant while the show click's reads
  // are out the door.
  ctrl.grantStore = ctrl.grantStore.concat([CONCURRENT_GRANT]);
  // The create's 201 lands: its three reads supersede the show
  // click's.
  ctrl.heldCreate.route.fulfill({
    status: 201,
    json: { id: 999, process: 'transfer_release' },
  });
  await expect.poll(() => ctrl.grantsGets).toBe(before[0] + 2);
  await expect.poll(() => ctrl.runsGets).toBe(before[1] + 2);
  await expect.poll(() => ctrl.settingsGets).toBe(before[2] + 2);
  // The show click's delayed grants answer lands after the newer
  // batch ...
  await expect.poll(() => ctrl.grantsFulfilled).toBe(grantsFulfilledBefore + 2);
  // ... and it is discarded: the table holds the newer answer.
  await expect(grantRows).toHaveCount(4);
  expect(await grantRowData()).toEqual([CREATED_ROW, ORDER_ROW, TRANSFER_ROW, CONCURRENT_ROW]);
  await expect(formError).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // --- revoke: the in-force order-plan grant (id 2) -------------------
  const orderPlanRow = page.locator('[data-testid="releases-grant"][data-grant-id="2"]');
  const revokeButton = orderPlanRow.locator('[data-testid="releases-grant-revoke"]');
  const cancelButton = orderPlanRow.locator('[data-testid="releases-grant-cancel"]');
  const rowError = orderPlanRow.locator('[data-testid="releases-grant-revoke-error"]');

  // A first click arms the row: "Confirm revoke" and "Cancel".
  await revokeButton.click();
  await expect(revokeButton).toHaveText('Confirm revoke');
  await expect(cancelButton).toHaveText('Cancel');
  await expect(rowError).toHaveCount(0);

  // Cancel sends nothing and un-arms the row.
  requestsBefore = ctrl.requests.length;
  const revokePostsBefore = ctrl.revokePosts.length;
  await cancelButton.click();
  expect(ctrl.revokePosts.length).toBe(revokePostsBefore);
  expect(ctrl.requests.length).toBe(requestsBefore);
  await expect(revokeButton).toHaveText('Revoke');
  await expect(cancelButton).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // A 409 revoke: one POST for the row's id, the three reads again,
  // the row still in force.
  ctrl.revokeFailNext = { status: 409, json: { detail: 'The grant is already revoked' } };
  before = reads();
  await revokeButton.click();
  await expect(revokeButton).toHaveText('Confirm revoke');
  await revokeButton.click();
  await expect.poll(() => ctrl.revokePosts.length).toBe(revokePostsBefore + 1);
  expect(ctrl.revokePosts[0]).toBe('/api/standing-grants/2/revoke');
  await expectOneReadOfEach(before);
  await expect(orderPlanRow.locator('.rl-grant-state')).toHaveText('In force');
  await expect(rowError).toHaveCount(0);
  await expect(revokeButton).toHaveText('Revoke');
  expect(pageErrors).toEqual([]);

  // Any other failure: the message beside the row, no re-read.
  ctrl.revokeFailNext = { status: 500, json: { detail: 'simulated outage' } };
  before = reads();
  await revokeButton.click();
  await expect(revokeButton).toHaveText('Confirm revoke');
  await revokeButton.click();
  await expect.poll(() => ctrl.revokePosts.length).toBe(revokePostsBefore + 2);
  expect(ctrl.revokePosts[1]).toBe('/api/standing-grants/2/revoke');
  expect(ctrl.grantsGets).toBe(before[0]);
  expect(ctrl.runsGets).toBe(before[1]);
  expect(ctrl.settingsGets).toBe(before[2]);
  await expect(rowError).toHaveText('The grant could not be revoked.');
  await expect(orderPlanRow.locator('.rl-grant-state')).toHaveText('In force');
  expect(pageErrors).toEqual([]);

  // A 200 revoke: one POST, the three reads again, the row revoked.
  before = reads();
  await revokeButton.click();
  await expect(revokeButton).toHaveText('Confirm revoke');
  await revokeButton.click();
  await expect.poll(() => ctrl.revokePosts.length).toBe(revokePostsBefore + 3);
  await expectOneReadOfEach(before);
  await expect(orderPlanRow.locator('.rl-grant-state')).toHaveText(
    'Revoked 2026-11-15 by admin1',
  );
  await expect(orderPlanRow.locator('[data-testid="releases-grant-revoke"]')).toHaveCount(0);
  await expect(rowError).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // --- the switch off: the entry's value false ------------------------
  ctrl.settingsEnabled = false;
  before = reads();
  await showButton.click();
  await expectOneReadOfEach(before);
  await expect(switchLine).toHaveText(
    "Standing grants are off: every write to the ERP needs a person's approval.",
  );
  await expect(sectionError).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // The entry missing: the line is absent, and no error line either.
  ctrl.settingsEnabled = null;
  before = reads();
  await showButton.click();
  await expectOneReadOfEach(before);
  await expect(switchLine).toHaveCount(0);
  await expect(sectionError).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // --- failures: a 500 runs read ---------------------------------------
  ctrl.runsFailNext = { status: 500, json: { detail: 'simulated outage' } };
  const grantsRowsBeforeFail = await grantRowData();
  const runRowsBeforeFail = await runRowData();
  before = reads();
  await showButton.click();
  await expectOneReadOfEach(before);
  await expect(sectionError).toHaveText('Standing grants could not be loaded.');
  // The rest of the screen is unchanged.
  await expect(releasesTable).toBeVisible();
  await expect(rlRows).toHaveCount(3);
  expect(await grantRowData()).toEqual(grantsRowsBeforeFail);
  expect(await runRowData()).toEqual(runRowsBeforeFail);
  expect(pageErrors).toEqual([]);

  // A 500 grants read: the same error, the rest still unchanged.
  ctrl.grantsFailNext = { status: 500, json: { detail: 'simulated outage' } };
  before = reads();
  await showButton.click();
  await expectOneReadOfEach(before);
  await expect(sectionError).toHaveText('Standing grants could not be loaded.');
  await expect(rlRows).toHaveCount(3);
  expect(await grantRowData()).toEqual(grantsRowsBeforeFail);
  expect(await runRowData()).toEqual(runRowsBeforeFail);
  expect(pageErrors).toEqual([]);

  // A 500 settings read: only the switch line is left out, no error.
  ctrl.settingsFailNext = { status: 500, json: { detail: 'simulated outage' } };
  before = reads();
  await showButton.click();
  await expectOneReadOfEach(before);
  await expect(sectionError).toHaveCount(0);
  await expect(switchLine).toHaveCount(0);
  await expect(rlRows).toHaveCount(3);
  expect(pageErrors).toEqual([]);

  // --- sign-out: the state clears, an in-flight answer is discarded ---
  const runsFulfilledBefore = ctrl.runsFulfilled;
  ctrl.runsDelayMs = 400;
  before = reads();
  await showButton.click();
  await expectOneReadOfEach(before); // the reads are sent ...
  // ... the runs read is still out the door when the user signs out.
  await page.click('#signout-button');
  await expect(page.locator('#signin-form')).toBeVisible();
  // The delayed answer lands on the past session: discarded.
  await expect.poll(() => ctrl.runsFulfilled).toBe(runsFulfilledBefore + 1);
  ctrl.runsDelayMs = 0;
  // Sign back in: the section is at its first-load state, and
  // opening the view again sends no grants request.
  await page.fill('#token-input', 'dev-admin-token');
  await page.click('#signin-button');
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('admin1');
  await expect(releasesTable).toBeVisible();
  await expect(rlRows).toHaveCount(3);
  expect(ctrl.grantsGets).toBe(before[0] + 1);
  expect(ctrl.runsGets).toBe(before[1] + 1);
  expect(ctrl.settingsGets).toBe(before[2] + 1);
  await expect(showButton).toHaveText('Show standing grants');
  await expect(grantRows).toHaveCount(0);
  await expect(runRows).toHaveCount(0);
  await expect(switchLine).toHaveCount(0);
  await expect(sectionError).toHaveCount(0);
  expect(pageErrors).toEqual([]);

  // --- 390x844: the section's tables scroll within the section --------
  page.setViewportSize({ width: 390, height: 844 });
  before = reads();
  await showButton.click();
  await expectOneReadOfEach(before);
  await expect(grantRows).toHaveCount(4);
  await expect(runRows).toHaveCount(3);
  const narrow = await page.evaluate(() => {
    const regions = Array.from(document.querySelectorAll('.rl-grants-region'));
    return {
      pageScrollWidth: document.documentElement.scrollWidth,
      regions: regions.map((region) => ({
        overflowX: getComputedStyle(region).overflowX,
        scrolls: region.scrollWidth > region.clientWidth,
      })),
    };
  });
  expect(narrow.pageScrollWidth).toBeLessThanOrEqual(390);
  expect(narrow.regions.length).toBe(2);
  expect(narrow.regions.every((region) => region.overflowX === 'auto')).toBe(true);
  // The admin's grants table overflows its region and scrolls in it.
  expect(narrow.regions[0].scrolls).toBe(true);
  expect(pageErrors).toEqual([]);

  // Every runs read the section sent carried limit=20.
  for (const request of ctrl.runsRequests) {
    const runsUrl = new URL(request.url());
    expect(runsUrl.pathname).toBe('/api/standing-grants/runs');
    expect(runsUrl.searchParams.get('limit')).toBe('20');
  }
  // The admin pass' fifteen clicks of the section (the first show
  // click, the two empty-answer show clicks, the 201 create, the show
  // click and the create of the stale-answer check, the 409 and the
  // 200 revoke, the switch-off, the missing-entry, the three 500
  // clicks, the sign-out click and the phone click) sent exactly
  // three reads each, and nothing else.
  expect(ctrl.grantsGets).toBe(15);
  expect(ctrl.runsGets).toBe(15);
  expect(ctrl.settingsGets).toBe(15);

  // --- planner pass: NON-ADMIN, production boot, full reload ----------
  // Restore the store to its two original rows: the planner sees the
  // section's first answers.
  page.setViewportSize({ width: 1440, height: 900 });
  ctrl.grantStore = freshGrants();
  ctrl.runsAnswer = freshRuns();
  ctrl.settingsEnabled = true;

  // The token arrives in the URL hash the way the identity requirement
  // describes; since the hash is no longer a token delivery path the
  // production boot completes through the PKCE round-trip, so the
  // IdP's auth and token endpoints carry the planner through it.
  await page.evaluate(() => {
    window.sessionStorage.clear();
  });
  const meCountBeforePlanner = ctrl.meRequests.length;
  const createPostsBeforePlanner = ctrl.createPosts.length;
  const revokePostsBeforePlanner = ctrl.revokePosts.length;
  await page.unroute('**/api/**');
  ctrl.config = PROD_CONFIG;
  ctrl.me = PLANNER_ME;
  await page.route('**/api/**', handler);
  await page.route(/protocol\/openid-connect\/token/, (route) => {
    return route.fulfill({
      status: 200,
      json: { access_token: 'pkce-planner-token', token_type: 'Bearer' },
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
  await page.goto('/index.html#access_token=planner-token-1211');

  // The PKCE round-trip completes and the shell renders for the
  // non-admin user, signed in through /api/auth/me ...
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#user-name')).toHaveText('planner1');
  expect(page.url()).not.toContain('access_token');
  expect(await page.evaluate(() => window.sessionStorage.getItem('aierp_token'))).toBe(
    'pkce-planner-token',
  );
  await expect.poll(() => ctrl.meRequests.length).toBeGreaterThan(meCountBeforePlanner);
  const plannerMe = ctrl.meRequests[ctrl.meRequests.length - 1];
  expect(plannerMe.headers()['authorization']).toBe('Bearer pkce-planner-token');

  // Opening the screen as the planner: no request to the grants
  // routes or the settings.
  const readsBeforePlanner = reads();
  await page.click('.nav-btn[data-view="releases"]');
  await expect(page).toHaveURL(/#\/releases$/);
  await expect(page.locator('#view-releases')).toBeVisible();
  await expect(releasesTable).toBeVisible();
  await expect(rlRows).toHaveCount(3);
  await expect(section).toBeVisible();
  await expect(section.locator('.rl-grants-title')).toHaveText('Standing grants');
  await expect(showButton).toHaveText('Show standing grants');
  expect(ctrl.grantsGets).toBe(readsBeforePlanner[0]);
  expect(ctrl.runsGets).toBe(readsBeforePlanner[1]);
  expect(ctrl.settingsGets).toBe(readsBeforePlanner[2]);
  expect(pageErrors).toEqual([]);

  // The click sends the three reads; both tables show, with the
  // switch line ...
  await showButton.click();
  await expectOneReadOfEach(readsBeforePlanner);
  expect(ctrl.grantsGets).toBe(16);
  expect(ctrl.runsGets).toBe(16);
  expect(ctrl.settingsGets).toBe(16);
  await expect(switchLine).toHaveText('Standing grants are on.');
  await expect(grantsTable).toBeVisible();
  await expect(grantRows).toHaveCount(2);
  expect(await grantRowData()).toEqual([ORDER_ROW_VIEWER, TRANSFER_ROW_VIEWER]);
  await expect(runsTable).toBeVisible();
  await expect(runRows).toHaveCount(3);
  expect(await runRowData()).toEqual([FAILED_RUN_ROW, RELEASED_RUN_ROW, NOTHING_DUE_RUN_ROW]);
  // ... but there is no form, no Revoke buttons and no extra column.
  await expect(form).toHaveCount(0);
  await expect(processSelect).toHaveCount(0);
  await expect(perOrderInput).toHaveCount(0);
  await expect(perDayInput).toHaveCount(0);
  await expect(dataAgeInput).toHaveCount(0);
  await expect(createButton).toHaveCount(0);
  await expect(formError).toHaveCount(0);
  await expect(revokeButtons).toHaveCount(0);
  const columnCounts = await page.evaluate(() => {
    const grantsHead = document.querySelector('[data-testid="releases-grants-table"] thead');
    const runsHead = document.querySelector('[data-testid="releases-grant-runs"] thead');
    const grantCells = Array.from(document.querySelectorAll('[data-testid="releases-grant"]')).map(
      (row) => row.querySelectorAll('td').length,
    );
    return {
      grantsColumns: grantsHead ? grantsHead.querySelectorAll('th').length : 0,
      runsColumns: runsHead ? runsHead.querySelectorAll('th').length : 0,
      grantCells,
    };
  });
  expect(columnCounts.grantsColumns).toBe(6);
  expect(columnCounts.runsColumns).toBe(6);
  expect(columnCounts.grantCells).toEqual([6, 6]);
  // No write went out: the only POSTs the whole pass sent are the
  // admin pass' creates and revokes.
  expect(ctrl.createPosts.length).toBe(createPostsBeforePlanner);
  expect(ctrl.revokePosts.length).toBe(revokePostsBeforePlanner);
  expect(pageErrors).toEqual([]);

  // --- the required read shape: every read of the whole proof --------
  // The admin pass' fifteen clicks and the planner's one each sent
  // the three reads once — sixteen of each — and every one of them
  // carries the exact shape the contract names: a GET on the exact
  // path, no query on the grants and settings reads, limit=20 on
  // the runs read.
  expect(ctrl.grantsRequests.length).toBe(16);
  expect(ctrl.runsRequests.length).toBe(16);
  expect(ctrl.settingsRequests.length).toBe(16);
  for (const request of ctrl.grantsRequests) {
    const grantsUrl = new URL(request.url());
    expect(request.method()).toBe('GET');
    expect(grantsUrl.pathname).toBe('/api/standing-grants');
    expect(grantsUrl.search).toBe('');
  }
  for (const request of ctrl.runsRequests) {
    const runsUrl = new URL(request.url());
    expect(request.method()).toBe('GET');
    expect(runsUrl.pathname).toBe('/api/standing-grants/runs');
    expect(runsUrl.searchParams.get('limit')).toBe('20');
  }
  for (const request of ctrl.settingsRequests) {
    const settingsUrl = new URL(request.url());
    expect(request.method()).toBe('GET');
    expect(settingsUrl.pathname).toBe('/api/settings');
    expect(settingsUrl.search).toBe('');
  }
  expect(pageErrors).toEqual([]);
});
