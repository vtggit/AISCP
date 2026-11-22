// AIERP planner screen — the Releases view (#/releases).
//
// #/releases shows where the purchase orders released to the ERP
// stand: one row per released order, in the order GET
// /api/aggregate/releases returns them. Above the table a row of
// state chips ([data-testid="releases-filter"]) filters the table
// without a new request: "All" (the default, applied only on first
// load), "Late", "Awaiting approval", "Partly received", "Open",
// "Received", "Cancelled" and "Missing". Each chip shows its number
// of orders — Late, Awaiting approval, Partly received and Received
// come from the answer's counts, the rest are counted from the
// orders. A real Refresh button ([data-testid="releases-refresh"])
// re-fetches, and so does activating the view; while a fetch is out a
// skeleton shows, and a request-sequence guard renders only the
// newest answer. When a new answer arrives the selected chip filter
// is preserved and re-applied to the new orders, and any expanded row
// collapses.
//
// The table ([data-testid="releases-table"]) scrolls within its own
// region: Order (order_nbr), State (a badge — "Late" in the risk
// tokens with a leading ▼ marker, "Awaiting approval" in the accent
// tokens, the rest neutral), Vendor (supplier_name, with
// vendor_code beneath it in the secondary ink token; the vendor
// code without a name, else supplier_code), Warehouse
// (warehouse_code, else site_code),
// Released (the date part of released_at on one line, then " by
// <actor>" on a second line in the secondary ink token),
// Value (whole dollars with separators, parsed from the
// decimal string), Promised (promised_on, else an em dash; when
// the promise is the ERP default — promise_is_default exactly
// true — and the server carries an expected_on, an "expected
// <expected_on>" note in the secondary ink token sits on its own
// line beneath) and Delay ("Late <n> d" when days_late is set,
// "Waiting <n> d" when days_waiting is set, else empty). A row
// is focusable and clicking
// it — or pressing Enter or Space on it — expands a line table
// beneath it ([data-testid="releases-lines"]): Item (item_description,
// with the item_code beneath it in the secondary ink token; just the
// code when there is no description; a line whose source is exactly
// the string "buffer" — the line the ERP order drew from its
// DDMRP buffer's order signal — ends with one small badge after
// the code ([data-testid="releases-line-source"], class
// rl-line-source) reading "Buffer" with the title "Ordered on its
// DDMRP buffer's order signal"; any other source —
// "reorder_point", "top_up", missing, null or another value —
// shows no such element and the cell reads exactly as before),
// Warehouse, Ordered, Received,
// Promised and Status ("Completed", "Cancelled" or empty); a second
// click collapses it. A row's identity is its
// index in the answer's orders array, never its order_nbr, so two
// orders the server tagged with the same number expand and collapse
// independently, and the re-render after a toggle hands the keyboard
// focus back to the fresh row so a second Enter still reaches it.
//
// With no orders an empty note shows ("Nothing has been released to
// the ERP yet."); with orders but the selected chip matching none of
// them, a note says so instead ("No orders are <chip label,
// lower-cased>."). A 502 or 503 shows the server's detail inline
// with a Retry; any other failure shows "Releases could not be
// loaded."
// with a Retry that re-fetches. Every API value is inserted as text,
// never as markup. Sign-out resets the view to its first-load state.
//
// Below the table the Transfer orders section
// ([data-testid="releases-transfers"]) is visible to every signed-in
// user, headed "Transfer orders" with a "Show transfer orders"
// button ([data-testid="releases-transfers-show"]). It sends no
// request until that button is clicked: one GET
// /api/supply-plan/transfers/release/audit?limit=50 per click, and
// once an answer has landed the button reads "Refresh transfer
// orders" (a failure before the first answer leaves it "Show
// transfer orders"). The answer renders as a table
// ([data-testid="releases-transfers-table"]), one row per audit row
// in the answer's order: Transfer (reference_nbr, else an em dash),
// From and To (the warehouse code, else the site code), Lines (each
// line's item and quantity), Outcome, ERP status (erp_status, else
// an em dash) and Released (the date of created_at). An empty answer
// reads "No transfer orders released yet.", a 403 — the audit route
// is admin-only — reads "Only an admin can see transfer orders.",
// and any other failure reads "Transfer orders could not be loaded."
// in [data-testid="releases-transfers-error"], leaving the releases
// table unchanged. The answer, error and pending flag live in module
// state, survive a re-render and clear on sign-out; an answer that
// arrives after sign-out is discarded.
//
// Below the Transfer orders section the Standing grants section
// ([data-testid="releases-grants"]) is visible to every signed-in
// user, headed "Standing grants" with a "Show standing grants"
// button ([data-testid="releases-grants-show"]). It sends no request
// until that button is clicked; each click sends exactly three reads
// through app/js/api.js — one GET /api/standing-grants, one GET
// /api/standing-grants/runs?limit=20 and one GET /api/settings — and
// once an answer has landed the button reads "Refresh standing
// grants". The settings answer's erp.standing_grants_enabled entry
// drives a switch line ([data-testid="releases-grants-switch"]):
// "Standing grants are on." when its value is true, "Standing grants
// are off: every write to the ERP needs a person's approval." when
// it is false, absent when the entry is missing or the read failed.
// A failed grants read or runs read shows "Standing grants could not
// be loaded." in [data-testid="releases-grants-error"] and leaves
// the rest of the screen unchanged; a failed settings read only
// leaves out the switch line.
//
// The grants table ([data-testid="releases-grants-table"]) has one
// row ([data-testid="releases-grant"]) per grant, in the answer's
// order: Process ("Order plan" for order_plan_release, "Transfers"
// for transfer_release), Per order and Per day (dollars and cents),
// Data age ("<hours> h"), Created (the date of created_at, then
// "by <created_by>" on a second line) and State ("In force", or
// "Revoked <date of revoked_at> by <revoked_by>"). With no grant it
// reads "No standing grant yet.". The runs table
// ([data-testid="releases-grant-runs"]) has one row
// ([data-testid="releases-grant-run"]) per run, in the answer's
// order: Started (started_at in UTC as YYYY-MM-DD HH:MM), Process,
// Outcome (a badge — "Released", "Nothing due", "Skipped" or
// "Failed", the last in the risk tokens), Sent ("<documents>
// order(s), <value in dollars and cents>"; a failed run's reads
// "Tried <documents> order(s), <value>" — what it tried to send,
// none or only some of it having reached the ERP; an em dash when
// documents is 0), Held back (the number of left_out entries, or an
// em dash when there are none) and Reason (or an em dash). With no
// run it reads "No runs yet.".
//
// An admin — the role the screen's probe reads off the shell's
// sign-in /api/auth/me answer, the same way the Order plan screen
// reads it — and only an admin sees, below the grants table, the
// create form ([data-testid="releases-grant-form"]): a process
// select with "Order plan" and "Transfers", number inputs for the
// limit per order, the limit per day and the data age in hours, and
// a "Create grant" button. Submitting sends one POST
// /api/standing-grants with the four fields as numbers: a 201 sends
// the three reads again; a 409 reads "A grant for this process is
// already in force. Revoke it first."; a 422 reads "Check the
// limits: the daily limit must not be below the per-order limit, and
// the data age must be from 1 to 168 hours."; a 403 reads "Only an
// admin can create a grant."; any other failure reads "The grant
// could not be created." — each in
// [data-testid="releases-grant-form-error"]. A value the inputs
// cannot hold, such as an empty field, sends no request and shows
// the 422 message. Each in-force row's last cell (admin only)
// carries a "Revoke" button ([data-testid="releases-grant-revoke"]):
// a first click turns it into "Confirm revoke" and a "Cancel"
// button (which sends nothing); confirming sends one POST
// /api/standing-grants/<id>/revoke — a 200, a 409 or a 404 sends the
// three reads again, any other failure shows "The grant could not be
// revoked." beside the row.
//
// The section's answers, error and pending flag live in module
// state, survive a re-render and clear on sign-out; an answer that
// lands after sign-out, or after a newer click's answer, is
// discarded. Every API value is inserted as text, never as markup.
(function (global) {
  'use strict';

  var LIST_PATH = '/api/aggregate/releases';
  var ROOT_ID = 'releases-root';
  var VIEW_ID = 'view-releases';
  var APP_ID = 'app';

  var EM_DASH = '\u2014';
  var GENERIC_ERROR = 'Releases could not be loaded.';

  // The Transfer orders section below the table: the admin-only
  // audit route, fetched on demand — never on opening the view —
  // one request per click of the section's button.
  var TRANSFERS_PATH = '/api/supply-plan/transfers/release/audit?limit=50';
  var TRANSFERS_SHOW_LABEL = 'Show transfer orders';
  var TRANSFERS_REFRESH_LABEL = 'Refresh transfer orders';
  var TRANSFERS_ADMIN_ERROR = 'Only an admin can see transfer orders.';
  var TRANSFERS_GENERIC_ERROR = 'Transfer orders could not be loaded.';
  var TRANSFERS_EMPTY_NOTE = 'No transfer orders released yet.';

  // The Standing grants section below the Transfer orders section:
  // visible to every signed-in user, fetched on demand only — never
  // on opening the view — and each click of the section's button
  // sends the three reads (grants, runs, settings) together.
  var GRANTS_PATH = '/api/standing-grants';
  var GRANTS_RUNS_PATH = '/api/standing-grants/runs?limit=20';
  var SETTINGS_PATH = '/api/settings';
  var GRANTS_ENABLED_KEY = 'erp.standing_grants_enabled';
  var GRANTS_SHOW_LABEL = 'Show standing grants';
  var GRANTS_REFRESH_LABEL = 'Refresh standing grants';
  var GRANTS_GENERIC_ERROR = 'Standing grants could not be loaded.';
  var GRANTS_EMPTY_NOTE = 'No standing grant yet.';
  var RUNS_EMPTY_NOTE = 'No runs yet.';
  var GRANTS_SWITCH_ON = 'Standing grants are on.';
  var GRANTS_SWITCH_OFF =
    'Standing grants are off: every write to the ERP needs a person\'s approval.';
  var GRANT_CREATE_CONFLICT =
    'A grant for this process is already in force. Revoke it first.';
  var GRANT_CREATE_INVALID =
    'Check the limits: the daily limit must not be below the per-order limit, and the data age must be from 1 to 168 hours.';
  var GRANT_CREATE_FORBIDDEN = 'Only an admin can create a grant.';
  var GRANT_CREATE_FAILED = 'The grant could not be created.';
  var GRANT_REVOKE_FAILED = 'The grant could not be revoked.';

  // The two grant processes and their labels, plus the runs'
  // outcome labels ("Failed" renders in the risk tokens).
  var PROCESS_LABEL = {
    order_plan_release: 'Order plan',
    transfer_release: 'Transfers',
  };
  var OUTCOME_LABEL = {
    released: 'Released',
    nothing_due: 'Nothing due',
    skipped: 'Skipped',
    failed: 'Failed',
  };

  // The viewer's role, read off the shell's sign-in /api/auth/me
  // answer the way the Order plan screen reads it: the screen keeps
  // a zero-size [data-admin-only] probe and fails closed while the
  // identity is unresolved. No request of the screen's own.
  var ROLE_UNRESOLVED = 'unresolved';
  var ROLE_ADMIN = 'admin';
  var ROLE_OTHER = 'other';

  // The tooltip of the Promised cell's expected note: the promise
  // is the ERP default, so the note shows the date expected from
  // the supplier's average lead time.
  var EXPECTED_NOTE_TITLE =
    'The promised date is the ERP default; expected from the supplier\'s average lead time';

  // minimumFractionDigits must be pinned with maximumFractionDigits:
  // for currency style the minimum defaults to the currency's (2 for
  // USD), and a maximum below the minimum makes the constructor throw
  // a RangeError at script load, aborting the whole script before
  // window.Releases is published.
  var DOLLAR_FORMATTER = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });

  // The grants' limits and the runs' values: dollars and cents
  // (5000 -> "$5,000.00", 1563.5 -> "$1,563.50").
  var DOLLAR_CENTS_FORMATTER = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  // The chips, in display order. ``key`` is the chip's identity; the
  // order's ``state`` it matches (``partly_received`` reads the
  // server's ``partially_received``) is mapped below.
  var CHIPS = [
    { key: 'all', label: 'All' },
    { key: 'late', label: 'Late' },
    { key: 'awaiting_approval', label: 'Awaiting approval' },
    { key: 'partly_received', label: 'Partly received' },
    { key: 'open', label: 'Open' },
    { key: 'received', label: 'Received' },
    { key: 'cancelled', label: 'Cancelled' },
    { key: 'missing', label: 'Missing' },
  ];

  // The chip keys whose count comes from the answer's fixed counts;
  // every other chip is counted from the orders themselves.
  var COUNT_KEYS = {
    late: true,
    awaiting_approval: true,
    partly_received: true,
    received: true,
  };

  var CHIP_STATE = {
    late: 'late',
    awaiting_approval: 'awaiting_approval',
    partly_received: 'partially_received',
    open: 'open',
    received: 'received',
    cancelled: 'cancelled',
    missing: 'missing',
  };

  var STATE_LABEL = {
    late: 'Late',
    awaiting_approval: 'Awaiting approval',
    partially_received: 'Partly received',
    open: 'Open',
    received: 'Received',
    cancelled: 'Cancelled',
    missing: 'Missing',
  };

  var rootEl = null;

  var state = {
    data: null, // the newest successful answer: { as_of, orders, counts }
    loading: false, // a fetch is out
    error: null, // the newest failed load: { status, message }
    filter: 'all', // the selected chip; "all" is the first-load default
    expanded: null, // the row id (index in the answer's orders) whose lines are open
    // The section's own module state: it survives a re-render (a
    // chip click, a row toggle, a new releases answer) and clears on
    // sign-out.
    transfers: {
      rows: null, // the newest successful audit answer (an array)
      loading: false, // an audit fetch is out
      error: null, // the newest failed audit load: { status, message }
    },
    // The Standing grants section's own module state: it survives a
    // re-render (a chip click, a row toggle, a new releases answer)
    // and clears on sign-out.
    grants: {
      grants: null, // the newest successful grants answer (an array)
      runs: null, // the newest successful runs answer (an array)
      settings: null, // the newest successful settings answer (an array)
      pending: 0, // reads of the newest click still out the door (3 on a click)
      error: null, // the newest click's failed reads: { grantsFailed, runsFailed }
      form: {
        process: 'order_plan_release',
        perOrder: '', // the limit-per-order input, as typed
        perDay: '', // the limit-per-day input, as typed
        dataAge: '', // the data-age input, as typed
        error: null, // the create/revoke message line's text
        creating: false, // a create POST is out
        revokingId: null, // the grant id armed at "Confirm revoke"
        revoking: false, // a revoke POST is out
        revokeError: null, // the failed revoke: { id, message }
      },
    },
    role: ROLE_UNRESOLVED, // the shared Auth identity's role
  };

  // The request-sequence guard: only the newest fetch's answer renders.
  var seq = 0;

  // The section's own sequence guard: a later click — or a sign-out —
  // invalidates an audit answer still out the door.
  var transfersSeq = 0;

  // The grants section's own sequence guard: a later click — or a
  // sign-out — invalidates its three reads still out the door.
  var grantsSeq = 0;

  // The form's own generation guard: a sign-out invalidates a create
  // or revoke answer still out the door.
  var formGen = 0;

  /* ------------------------------ helpers ------------------------------ */

  function esc(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function isNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }

  // A whole figure with separators (273 -> "273"); an em dash when
  // the server carries no usable figure.
  function wholeNumber(value) {
    if (typeof value === 'string') {
      value = value === '' ? NaN : Number(value);
    }
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return Math.round(value).toLocaleString('en-US');
  }

  // Whole dollars with separators, parsed from the decimal string
  // (66931.41 -> "$66,931"); an em dash when there is no figure.
  function wholeDollars(value) {
    if (value === null || value === undefined || value === '') {
      return EM_DASH;
    }
    var n = Number(value);
    if (!isNumber(n)) {
      return EM_DASH;
    }
    return DOLLAR_FORMATTER.format(Math.round(n));
  }

  // The first non-empty string of the pair, else an em dash.
  function pick(primary, fallback) {
    if (typeof primary === 'string' && primary !== '') {
      return primary;
    }
    if (typeof fallback === 'string' && fallback !== '') {
      return fallback;
    }
    return EM_DASH;
  }

  function nonEmptyString(value) {
    return typeof value === 'string' && value !== '' ? value : null;
  }

  function ordersOf(data) {
    return data && Array.isArray(data.orders) ? data.orders : [];
  }

  function chipCount(key, data) {
    var orders = ordersOf(data);
    if (key === 'all') {
      return orders.length;
    }
    if (COUNT_KEYS[key]) {
      var counts =
        data && data.counts && typeof data.counts === 'object' ? data.counts : {};
      return asCount(counts[key]);
    }
    // Counted from the orders: every order whose state is the
    // chip's state.
    var n = 0;
    for (var i = 0; i < orders.length; i++) {
      var order = orders[i];
      if (order && typeof order === 'object' && order.state === key) {
        n += 1;
      }
    }
    return n;
  }

  function asCount(value) {
    if (typeof value === 'string') {
      value = Number(value);
    }
    if (!isNumber(value)) {
      return 0;
    }
    return Math.max(0, Math.round(value));
  }

  // The visible { order, id } pairs. ``id`` is the order's index in
  // the answer's full orders array — the row's identity. Two orders
  // the server tagged with the same order_nbr still get distinct ids,
  // so expanding one never touches the other.
  function visibleOrders() {
    var orders = ordersOf(state.data);
    var wanted = state.filter === 'all' ? null : CHIP_STATE[state.filter] || state.filter;
    var out = [];
    for (var i = 0; i < orders.length; i++) {
      var order = orders[i];
      if (wanted === null || (order && typeof order === 'object' && order.state === wanted)) {
        out.push({ order: order, id: i });
      }
    }
    return out;
  }

  // A well-formed answer: an object (never an array or a bare value)
  // with an orders array. Anything else is a failed load.
  function answerValid(payload) {
    return (
      payload &&
      typeof payload === 'object' &&
      !Array.isArray(payload) &&
      Array.isArray(payload.orders)
    );
  }

  /* ------------------------------ loading ------------------------------ */

  function load() {
    seq += 1;
    var mySeq = seq;
    state.loading = true;
    state.error = null;
    render();
    global.Api.get(LIST_PATH).then(
      function (payload) {
        if (mySeq !== seq) {
          return; // a newer fetch is out: this answer is stale
        }
        state.loading = false;
        if (!answerValid(payload)) {
          state.error = { status: 0, message: GENERIC_ERROR };
        } else {
          state.data = payload;
          state.expanded = null; // a new answer: any expansion collapses
        }
        render();
      },
      function (error) {
        if (mySeq !== seq) {
          return;
        }
        state.loading = false;
        var status = error && typeof error.status === 'number' ? error.status : 0;
        if (status === 502 || status === 503) {
          // The server's detail, when it carries one.
          state.error = {
            status: status,
            message:
              error && typeof error.message === 'string' && error.message !== ''
                ? error.message
                : GENERIC_ERROR,
          };
        } else {
          state.error = { status: status, message: GENERIC_ERROR };
        }
        render();
      }
    );
  }

  // The section's on-demand fetch: one request per click of its
  // button, never on opening the view. The answer is a bare array of
  // audit rows; a 403 (the route is admin-only) reads its own
  // message, every other failure the generic one. A 200 with rows or
  // an empty list lands the answer, and from then on the button reads
  // "Refresh transfer orders" — later failures never change it.
  function loadTransfers() {
    transfersSeq += 1;
    var mySeq = transfersSeq;
    state.transfers.loading = true;
    state.transfers.error = null;
    render();
    global.Api.get(TRANSFERS_PATH).then(
      function (payload) {
        if (mySeq !== transfersSeq) {
          return; // a newer click or a sign-out owns the section now
        }
        state.transfers.loading = false;
        if (!Array.isArray(payload)) {
          state.transfers.error = { status: 0, message: TRANSFERS_GENERIC_ERROR };
        } else {
          state.transfers.rows = payload;
          state.transfers.error = null;
        }
        render();
      },
      function (error) {
        if (mySeq !== transfersSeq) {
          return;
        }
        state.transfers.loading = false;
        var status = error && typeof error.status === 'number' ? error.status : 0;
        state.transfers.error = {
          status: status,
          message: status === 403 ? TRANSFERS_ADMIN_ERROR : TRANSFERS_GENERIC_ERROR,
        };
        render();
      }
    );
  }

  // The section's on-demand fetch: one click sends exactly three
  // reads — the grants, the runs (limit 20) and the settings —
  // through the shared HTTP layer, each under the section's
  // sequence guard. A grants or runs failure (including a
  // malformed answer) marks that read failed and shows the section's
  // error line; the other answers that did land still render. A
  // settings failure only leaves out the switch line. The newest
  // successful answer of each read stays put in module state, so a
  // later failed refresh never erases what the screen already shows.
  function loadGrants() {
    grantsSeq += 1;
    var mySeq = grantsSeq;
    var g = state.grants;
    g.pending = 3;
    g.error = null;
    render();
    global.Api.get(GRANTS_PATH).then(
      function (payload) {
        if (mySeq !== grantsSeq) {
          return; // a newer click or a sign-out owns the section now
        }
        g.pending -= 1;
        if (Array.isArray(payload)) {
          g.grants = payload;
        } else {
          g.error = grantsErrorWith(g.error, { grantsFailed: true });
        }
        render();
      },
      function () {
        if (mySeq !== grantsSeq) {
          return;
        }
        g.pending -= 1;
        g.error = grantsErrorWith(g.error, { grantsFailed: true });
        render();
      }
    );
    global.Api.get(GRANTS_RUNS_PATH).then(
      function (payload) {
        if (mySeq !== grantsSeq) {
          return;
        }
        g.pending -= 1;
        if (Array.isArray(payload)) {
          g.runs = payload;
        } else {
          g.error = grantsErrorWith(g.error, { runsFailed: true });
        }
        render();
      },
      function () {
        if (mySeq !== grantsSeq) {
          return;
        }
        g.pending -= 1;
        g.error = grantsErrorWith(g.error, { runsFailed: true });
        render();
      }
    );
    global.Api.get(SETTINGS_PATH).then(
      function (payload) {
        if (mySeq !== grantsSeq) {
          return;
        }
        g.pending -= 1;
        g.settings = Array.isArray(payload) ? payload : null;
        render();
      },
      function () {
        if (mySeq !== grantsSeq) {
          return;
        }
        g.pending -= 1;
        // A failed settings read leaves out the switch line.
        g.settings = null;
        render();
      }
    );
  }

  function grantsErrorWith(current, patch) {
    var out = current || { grantsFailed: false, runsFailed: false };
    out.grantsFailed = out.grantsFailed || patch.grantsFailed;
    out.runsFailed = out.runsFailed || patch.runsFailed;
    return out;
  }

  // The admin's create: one POST with the four fields as numbers,
  // disabled while it is out. A value the inputs cannot hold (an
  // empty field among them) sends no request and shows the 422
  // message; a 201 sends the three reads again; the 409/422/403
  // each read their own message, any other failure the generic one.
  function createGrant() {
    if (state.role !== ROLE_ADMIN) {
      return; // fail closed: the form renders for an admin only
    }
    var g = state.grants;
    var form = g.form;
    if (form.creating || form.revoking) {
      return; // a request is already out: one at a time
    }
    var perOrder = parseLimit(form.perOrder);
    var perDay = parseLimit(form.perDay);
    var dataAge = parseLimit(form.dataAge);
    if (perOrder === null || perDay === null || dataAge === null) {
      form.error = GRANT_CREATE_INVALID;
      render();
      return;
    }
    form.error = null;
    form.creating = true;
    render();
    var myGen = formGen;
    global.Api.request(GRANTS_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        process: form.process,
        max_document_value: perOrder,
        max_daily_value: perDay,
        max_data_age_hours: dataAge,
      }),
    }).then(
      function () {
        if (myGen !== formGen) {
          return; // a sign-out happened: this answer is past
        }
        form.creating = false;
        form.error = null;
        render();
        loadGrants(); // the three reads again
      },
      function (error) {
        if (myGen !== formGen) {
          return;
        }
        form.creating = false;
        var status = error && typeof error.status === 'number' ? error.status : 0;
        if (status === 409) {
          form.error = GRANT_CREATE_CONFLICT;
        } else if (status === 422) {
          form.error = GRANT_CREATE_INVALID;
        } else if (status === 403) {
          form.error = GRANT_CREATE_FORBIDDEN;
        } else {
          form.error = GRANT_CREATE_FAILED;
        }
        render();
      }
    );
  }

  // The row's Revoke button arms itself: the first click turns it
  // into "Confirm revoke" and a "Cancel" button (which sends
  // nothing), the second click sends the revoke.
  function armRevoke(grantId) {
    if (state.role !== ROLE_ADMIN) {
      return;
    }
    var form = state.grants.form;
    if (form.revoking) {
      return; // a revoke is out: one at a time
    }
    form.revokingId = grantId;
    form.revokeError = null;
    render();
  }

  function cancelRevoke() {
    var form = state.grants.form;
    if (form.revoking) {
      return;
    }
    form.revokingId = null;
    render(); // no request
  }

  function confirmRevoke(grantId) {
    if (state.role !== ROLE_ADMIN) {
      return;
    }
    var form = state.grants.form;
    if (form.revoking || form.revokingId !== grantId) {
      return;
    }
    form.revoking = true;
    render();
    var myGen = formGen;
    global.Api.request(GRANTS_PATH + '/' + encodeURIComponent(String(grantId)) + '/revoke', {
      method: 'POST',
    }).then(
      function () {
        if (myGen !== formGen) {
          return;
        }
        form.revoking = false;
        form.revokingId = null;
        form.revokeError = null;
        render();
        loadGrants(); // a 200: the three reads again
      },
      function (error) {
        if (myGen !== formGen) {
          return;
        }
        form.revoking = false;
        var status = error && typeof error.status === 'number' ? error.status : 0;
        if (status === 409 || status === 404) {
          // Already revoked or never stored: nothing to fix locally —
          // the three reads again.
          form.revokingId = null;
          form.revokeError = null;
          render();
          loadGrants();
          return;
        }
        form.revokingId = null;
        form.revokeError = { id: grantId, message: GRANT_REVOKE_FAILED };
        render();
      }
    );
  }

  /* ------------------------------ rendering ---------------------------- */

  function render() {
    if (!rootEl) {
      return;
    }
    rootEl.innerHTML = shellHtml();
  }

  // The shell gates [data-admin-only] elements from the sign-in's
  // /api/auth/me answer. This zero-size probe sits in the render
  // until the sign-in's role lands on it: the shell leaves it hidden
  // for a non-admin and un-hides it for an admin — the screen's only
  // read of the shared identity, no request of its own.
  function roleProbeHtml() {
    if (state.role !== ROLE_UNRESOLVED) {
      return '';
    }
    return '<span class="rl-role-probe" data-admin-only hidden aria-hidden="true"></span>';
  }

  function shellHtml() {
    var probe = roleProbeHtml();
    // First load (no answer, no failure yet) and every in-flight
    // fetch of it: the skeleton.
    if (!state.data && !state.error) {
      return '<div class="rl">' + probe + skeletonHtml() + transfersHtml() + grantsHtml() + '</div>';
    }
    var body;
    if (state.loading) {
      body = skeletonHtml();
    } else if (state.error) {
      body = errorHtml();
    } else {
      var visible = visibleOrders();
      if (visible.length === 0) {
        // No orders at all ("All"): the first-load note. Orders
        // exist but the selected chip matches none: the chip's own
        // note, its label lower-cased.
        body =
          state.filter === 'all'
            ? emptyHtml()
            : emptyHtml(chipLabel(state.filter));
      } else {
        body = tableHtml(visible);
      }
    }
    return (
      '<div class="rl">' +
      probe +
      barHtml() +
      '<div class="rl-body">' + body + '</div>' +
      transfersHtml() +
      grantsHtml() +
      '</div>'
    );
  }

  function barHtml() {
    var chips = CHIPS.map(function (chip) {
      var on = state.filter === chip.key;
      return (
        '<button type="button" class="rl-chip' + (on ? ' is-on' : '') + '" ' +
        'aria-pressed="' + (on ? 'true' : 'false') + '" data-state="' + esc(chip.key) + '">' +
        esc(chip.label) + ' ' + chipCount(chip.key, state.data) +
        '</button>'
      );
    }).join('');
    return (
      '<div class="rl-bar">' +
      '<div class="rl-chips" role="group" aria-label="Order state filter" data-testid="releases-filter">' +
        chips +
      '</div>' +
      '<button type="button" class="btn btn-secondary rl-refresh" data-testid="releases-refresh"' +
        (state.loading ? ' disabled' : '') +
      '>Refresh</button>' +
      '</div>'
    );
  }

  function skeletonHtml() {
    return (
      '<div class="rl-skel-wrap" data-testid="releases-skeleton" aria-hidden="true">' +
      '<div class="skel skel-line"></div>' +
      '<div class="skel skel-line skel-wide"></div>' +
      '<div class="skel rl-skel-block"></div>' +
      '</div>'
    );
  }

  function errorHtml() {
    return (
      '<div class="rl-error" role="alert" data-testid="releases-error">' +
      '<p class="rl-error-msg" data-testid="releases-error-msg">' +
        esc(state.error.message) +
      '</p>' +
      '<button type="button" class="btn btn-secondary" data-testid="releases-retry">Retry</button>' +
      '</div>'
    );
  }

  // The chip's display label (its empty note lower-cases it).
  function chipLabel(key) {
    for (var i = 0; i < CHIPS.length; i++) {
      if (CHIPS[i].key === key) {
        return CHIPS[i].label;
      }
    }
    return key;
  }

  // The empty note: no orders at all keeps the first-load message;
  // with orders, the selected chip matching none gets its own note.
  function emptyHtml(label) {
    var message =
      label && label !== ''
        ? 'No orders are ' + label.toLowerCase() + '.'
        : 'Nothing has been released to the ERP yet.';
    return (
      '<p class="rl-empty" data-testid="releases-empty">' +
      esc(message) +
      '</p>'
    );
  }

  function tableHtml(entries) {
    var rows = [];
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i];
      var order = entry && entry.order;
      if (!order || typeof order !== 'object') {
        continue; // a malformed row never breaks the table
      }
      rows.push(orderHtml(order, entry.id));
    }
    return (
      '<div class="rl-table-region">' +
      '<table class="rl-table" data-testid="releases-table">' +
      '<thead><tr>' +
      '<th scope="col">Order</th>' +
      '<th scope="col">State</th>' +
      '<th scope="col">Vendor</th>' +
      '<th scope="col">Warehouse</th>' +
      '<th scope="col">Released</th>' +
      '<th scope="col">Value</th>' +
      '<th scope="col">Promised</th>' +
      '<th scope="col">Delay</th>' +
      '</tr></thead>' +
      '<tbody>' + rows.join('') + '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  function orderHtml(order, id) {
    var expanded = state.expanded === id;
    var html =
      '<tr class="rl-row" data-row-id="' + id + '" tabindex="0" ' +
      'aria-expanded="' + (expanded ? 'true' : 'false') + '" data-testid="releases-row">' +
      '<td class="rl-cell-order">' + esc(order.order_nbr) + '</td>' +
      '<td>' + badgeHtml(order.state) + '</td>' +
      '<td class="rl-cell-vendor">' + vendorHtml(order) + '</td>' +
      '<td class="rl-cell-warehouse">' + esc(pick(order.warehouse_code, order.site_code)) + '</td>' +
      '<td class="rl-cell-released">' + releasedHtml(order.released_at, order.actor) + '</td>' +
      '<td class="rl-cell-value"><span class="rl-cell-value-fig">' + esc(wholeDollars(order.value)) + '</span></td>' +
      '<td class="rl-cell-promised">' + promisedHtml(order) + '</td>' +
      '<td class="rl-cell-delay">' + esc(delayText(order.days_late, order.days_waiting)) + '</td>' +
      '</tr>';
    if (expanded) {
      html +=
        '<tr class="rl-lines-row">' +
        '<td colspan="8">' + linesHtml(order.lines) + '</td>' +
        '</tr>';
    }
    return html;
  }

  function badgeHtml(stateValue) {
    var stateKey = typeof stateValue === 'string' ? stateValue : '';
    var label = STATE_LABEL[stateKey] || (stateKey !== '' ? stateKey : EM_DASH);
    var cls = 'rl-badge';
    var marker = '';
    if (stateKey === 'late') {
      cls += ' rl-badge--late';
      marker = '\u25bc ';
    } else if (stateKey === 'awaiting_approval') {
      cls += ' rl-badge--awaiting';
    } else {
      cls += ' rl-badge--neutral';
    }
    return (
      '<span class="' + cls + '" data-testid="releases-badge">' +
      esc(marker + label) +
      '</span>'
    );
  }

  // The Vendor cell: the name first, with the vendor code beneath it
  // in the secondary ink token; without a name the vendor code, else
  // the supplier code, as before.
  function vendorHtml(order) {
    var name = nonEmptyString(order.supplier_name);
    if (name) {
      var html = '<span class="rl-vendor-name">' + esc(name) + '</span>';
      var vendorCode = nonEmptyString(order.vendor_code);
      if (vendorCode) {
        html += '<span class="rl-vendor-code">' + esc(vendorCode) + '</span>';
      }
      return html;
    }
    return esc(pick(order.vendor_code, order.supplier_code));
  }

  // The Promised cell: the promised date (an em dash without
  // one) on one line. When the promise is the ERP default —
  // promise_is_default exactly true, never a truthy lookalike —
  // and the server carries a non-empty expected_on, a note
  // beneath reads "expected <expected_on>" (the date from the
  // supplier's average lead time); every other combination
  // renders the date alone, exactly as before.
  function promisedHtml(order) {
    var html =
      '<span class="rl-cell-date">' +
      esc(nonEmptyString(order.promised_on) || EM_DASH) +
      '</span>';
    var expectedOn = nonEmptyString(order.expected_on);
    if (order.promise_is_default === true && expectedOn !== null) {
      html +=
        '<span class="rl-cell-expected" title="' + esc(EXPECTED_NOTE_TITLE) + '">' +
        'expected ' +
        esc(expectedOn) +
        '</span>';
    }
    return html;
  }

  // The Released cell: the date on its own line, then " by <actor>"
  // on a second line (the secondary ink token). The second line
  // carries a leading space, so the cell's flat text content stays
  // "DATE by ACTOR". An actor that is exactly "standing-grant:<id>"
  // with <id> one or more digits reads " by standing grant #<id>"
  // instead — the grant released the order, not a person — and
  // every other actor (one that only starts with "standing-grant:"
  // among them) reads exactly as before.
  function releasedHtml(releasedAt, actor) {
    var datePart = null;
    if (typeof releasedAt === 'string' && releasedAt.length >= 10) {
      datePart = releasedAt.slice(0, 10);
    }
    if (!datePart) {
      return esc(EM_DASH);
    }
    var by = nonEmptyString(actor);
    if (by === null) {
      return '<span class="rl-released-date">' + esc(datePart) + '</span>';
    }
    var standing = /^standing-grant:([0-9]+)$/.exec(by);
    if (standing !== null) {
      by = 'standing grant #' + standing[1];
    }
    return (
      '<span class="rl-released-date">' + esc(datePart) + '</span>' +
      '<span class="rl-released-by"> by ' + esc(by) + '</span>'
    );
  }

  function delayText(daysLate, daysWaiting) {
    if (daysLate !== null && daysLate !== undefined && daysLate !== '') {
      return 'Late ' + daysLate + ' d';
    }
    if (daysWaiting !== null && daysWaiting !== undefined && daysWaiting !== '') {
      return 'Waiting ' + daysWaiting + ' d';
    }
    return '';
  }

  function linesHtml(lines) {
    var list = Array.isArray(lines) ? lines : [];
    var rows = [];
    for (var i = 0; i < list.length; i++) {
      var raw = list[i];
      var line = raw && typeof raw === 'object' ? raw : {};
      var status = line.completed ? 'Completed' : line.cancelled ? 'Cancelled' : '';
      rows.push(
        '<tr>' +
        '<td class="rl-cell-line-item">' + itemHtml(line) + '</td>' +
        '<td>' + esc(nonEmptyString(line.warehouse_code) || EM_DASH) + '</td>' +
        '<td class="rl-cell-figure">' + esc(wholeNumber(line.order_qty)) + '</td>' +
        '<td class="rl-cell-figure">' + esc(wholeNumber(line.received_qty)) + '</td>' +
        '<td>' + esc(nonEmptyString(line.promised) || EM_DASH) + '</td>' +
        '<td>' + esc(status) + '</td>' +
        '</tr>'
      );
    }
    return (
      '<table class="rl-lines" data-testid="releases-lines">' +
      '<thead><tr>' +
      '<th scope="col">Item</th>' +
      '<th scope="col">Warehouse</th>' +
      '<th scope="col">Ordered</th>' +
      '<th scope="col">Received</th>' +
      '<th scope="col">Promised</th>' +
      '<th scope="col">Status</th>' +
      '</tr></thead>' +
      '<tbody>' + rows.join('') + '</tbody>' +
      '</table>'
    );
  }

  // The line's Item cell: item_description on the primary line, with
  // the item_code beneath it in the secondary ink token; just the
  // code when there is no description; an em dash without either.
  // A line whose source is exactly the string "buffer" — the line
  // the ERP order drew from its DDMRP buffer's order signal — ends
  // with the buffer badge (issue #1560) right after the code; any
  // other source leaves the cell exactly as before.
  function itemHtml(line) {
    var description = nonEmptyString(line.item_description);
    var code = nonEmptyString(line.item_code);
    if (!description) {
      return esc(code || EM_DASH) + bufferSourceHtml(line);
    }
    return (
      '<span class="rl-line-item-desc">' + esc(description) + '</span>' +
      '<span class="rl-line-item-code">' + esc(code || EM_DASH) + '</span>' +
      bufferSourceHtml(line)
    );
  }

  // The buffer source badge in the line's Item cell (issue
  // #1560): one element right after the code when the line's
  // source is exactly the string "buffer" — the line the ERP
  // order drew from its DDMRP buffer's order signal. Any other
  // source ("reorder_point", "top_up", missing, null or another
  // value) leaves the cell exactly as before.
  function bufferSourceHtml(line) {
    if (line.source !== 'buffer') {
      return '';
    }
    return (
      '<span class="rl-line-source" data-testid="releases-line-source"' +
      ' title="Ordered on its DDMRP buffer\'s order signal">Buffer</span>'
    );
  }

  // The Transfer orders section: visible to every signed-in user,
  // fetched on demand only. Its button reads "Show transfer orders"
  // until a 200 (rows or an empty list) has landed, then "Refresh
  // transfer orders" — a failure before the first answer leaves it
  // "Show transfer orders".
  function transfersHtml() {
    var transfers = state.transfers;
    var html =
      '<section class="rl-transfers" data-testid="releases-transfers" aria-label="Transfer orders">' +
      '<div class="rl-transfers-bar">' +
      '<h2 class="rl-transfers-title">Transfer orders</h2>' +
      '<button type="button" class="btn btn-secondary rl-transfers-show" ' +
      'data-testid="releases-transfers-show"' +
      (transfers.loading ? ' disabled' : '') +
      '>' +
      esc(transfers.rows !== null ? TRANSFERS_REFRESH_LABEL : TRANSFERS_SHOW_LABEL) +
      '</button>' +
      '</div>';
    if (transfers.error) {
      html +=
        '<p class="rl-transfers-error" role="alert" data-testid="releases-transfers-error">' +
        esc(transfers.error.message) +
        '</p>';
    } else if (transfers.rows !== null) {
      if (transfers.rows.length === 0) {
        html +=
          '<p class="rl-transfers-empty" data-testid="releases-transfers-empty">' +
          esc(TRANSFERS_EMPTY_NOTE) +
          '</p>';
      } else {
        html += transfersTableHtml(transfers.rows);
      }
    }
    return html + '</section>';
  }

  // The section's table: one row per audit row, in the answer's
  // order, in a region that scrolls of its own — the page never
  // scrolls sideways or downwards for it.
  function transfersTableHtml(rows) {
    var body = [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!row || typeof row !== 'object') {
        continue; // a malformed row never breaks the table
      }
      body.push(transfersRowHtml(row));
    }
    return (
      '<div class="rl-transfers-region">' +
      '<table class="rl-transfers-table" data-testid="releases-transfers-table">' +
      '<thead><tr>' +
      '<th scope="col">Transfer</th>' +
      '<th scope="col">From</th>' +
      '<th scope="col">To</th>' +
      '<th scope="col">Lines</th>' +
      '<th scope="col">Outcome</th>' +
      '<th scope="col">ERP status</th>' +
      '<th scope="col">Released</th>' +
      '</tr></thead>' +
      '<tbody>' + body.join('') + '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  function transfersRowHtml(row) {
    var lines = Array.isArray(row.lines) ? row.lines : [];
    var parts = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (!line || typeof line !== 'object') {
        continue;
      }
      parts.push(
        esc(nonEmptyString(line.item_code) || EM_DASH) +
        ' \u00d7 ' +
        esc(wholeNumber(line.quantity))
      );
    }
    return (
      '<tr class="rl-transfers-row" data-testid="releases-transfers-row">' +
      '<td class="rl-transfers-cell">' + esc(nonEmptyString(row.reference_nbr) || EM_DASH) + '</td>' +
      '<td class="rl-transfers-cell">' + esc(pick(row.from_warehouse_code, row.from_site_code)) + '</td>' +
      '<td class="rl-transfers-cell">' + esc(pick(row.to_warehouse_code, row.to_site_code)) + '</td>' +
      '<td class="rl-transfers-cell rl-transfers-lines">' +
        (parts.length === 0 ? esc(EM_DASH) : parts.join(', ')) +
      '</td>' +
      '<td class="rl-transfers-cell">' + esc(nonEmptyString(row.outcome) || EM_DASH) + '</td>' +
      '<td class="rl-transfers-cell">' + esc(nonEmptyString(row.erp_status) || EM_DASH) + '</td>' +
      '<td class="rl-transfers-cell">' + esc(datePart(row.created_at)) + '</td>' +
      '</tr>'
    );
  }

  // The date part of an ISO timestamp, else an em dash.
  function datePart(value) {
    if (typeof value === 'string' && value.length >= 10) {
      return value.slice(0, 10);
    }
    return EM_DASH;
  }

  // The Standing grants section: visible to every signed-in user,
  // fetched on demand only. Its button reads "Show standing grants"
  // until one of the three reads has landed a 200, then "Refresh
  // standing grants" — a failure before the first answer leaves it
  // "Show standing grants".
  function grantsHtml() {
    var g = state.grants;
    var landed = g.grants !== null || g.runs !== null || g.settings !== null;
    var html =
      '<section class="rl-grants" data-testid="releases-grants" aria-label="Standing grants">' +
      '<div class="rl-grants-bar">' +
      '<h2 class="rl-grants-title">Standing grants</h2>' +
      '<button type="button" class="btn btn-secondary rl-grants-show" ' +
      'data-testid="releases-grants-show"' +
      (g.pending > 0 ? ' disabled' : '') +
      '>' +
      esc(landed ? GRANTS_REFRESH_LABEL : GRANTS_SHOW_LABEL) +
      '</button>' +
      '</div>' +
      switchHtml();
    if (g.error && (g.error.grantsFailed || g.error.runsFailed)) {
      html +=
        '<p class="rl-grants-error" role="alert" data-testid="releases-grants-error">' +
        esc(GRANTS_GENERIC_ERROR) +
        '</p>';
    }
    html += grantsTableHtml();
    if (state.role === ROLE_ADMIN && g.grants !== null) {
      // The admin's form: below the grants table (or its empty
      // note), absent for a non-admin and before a grants answer.
      html += grantFormHtml();
    }
    html += runsTableHtml();
    return html + '</section>';
  }

  // The switch line, from the settings answer's
  // erp.standing_grants_enabled entry: on / off, absent when the
  // entry is missing or the read has not landed (or failed).
  function switchHtml() {
    var list = Array.isArray(state.grants.settings) ? state.grants.settings : [];
    var entry = null;
    for (var i = 0; i < list.length; i++) {
      var candidate = list[i];
      if (candidate && candidate.key === GRANTS_ENABLED_KEY) {
        entry = candidate;
        break;
      }
    }
    if (entry === null) {
      return '';
    }
    if (entry.value === true) {
      return (
        '<p class="rl-grants-switch" data-testid="releases-grants-switch">' +
        esc(GRANTS_SWITCH_ON) +
        '</p>'
      );
    }
    if (entry.value === false) {
      return (
        '<p class="rl-grants-switch rl-grants-switch--off" data-testid="releases-grants-switch">' +
        esc(GRANTS_SWITCH_OFF) +
        '</p>'
      );
    }
    return '';
  }

  // The grants table: one row per grant, in the answer's order; an
  // empty answer reads the empty note. The admin's last column holds
  // each in-force row's Revoke control; a non-admin gets no column.
  function grantsTableHtml() {
    var g = state.grants;
    if (g.grants === null) {
      return '';
    }
    if (g.grants.length === 0) {
      return (
        '<p class="rl-grants-empty" data-testid="releases-grants-empty">' +
        esc(GRANTS_EMPTY_NOTE) +
        '</p>'
      );
    }
    var rows = [];
    for (var i = 0; i < g.grants.length; i++) {
      var grant = g.grants[i];
      if (!grant || typeof grant !== 'object') {
        continue; // a malformed row never breaks the table
      }
      rows.push(grantRowHtml(grant));
    }
    var revokeHead =
      state.role === ROLE_ADMIN ? '<th scope="col" class="rl-grant-revoke-head">Revoke</th>' : '';
    return (
      '<div class="rl-grants-region">' +
      '<table class="rl-grants-table" data-testid="releases-grants-table">' +
      '<thead><tr>' +
      '<th scope="col">Process</th>' +
      '<th scope="col">Per order</th>' +
      '<th scope="col">Per day</th>' +
      '<th scope="col">Data age</th>' +
      '<th scope="col">Created</th>' +
      '<th scope="col">State</th>' +
      revokeHead +
      '</tr></thead>' +
      '<tbody>' + rows.join('') + '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  function grantInForce(grant) {
    return typeof grant.in_force === 'boolean' ? grant.in_force : grant.revoked_at === null;
  }

  function grantRowHtml(grant) {
    var inForce = grantInForce(grant);
    var stateText;
    if (inForce) {
      stateText = 'In force';
    } else {
      var parts = ['Revoked'];
      var revokedDate = datePart(grant.revoked_at);
      if (revokedDate !== EM_DASH) {
        parts.push(revokedDate);
      }
      var revokedBy = nonEmptyString(grant.revoked_by);
      if (revokedBy !== null) {
        parts.push('by ' + revokedBy);
      }
      stateText = parts.join(' ');
    }
    return (
      '<tr class="rl-grant-row" data-grant-id="' + esc(grant.id) + '" data-testid="releases-grant">' +
      '<td class="rl-grant-process">' + esc(processLabel(grant.process)) + '</td>' +
      '<td class="rl-grant-money">' + esc(dollarsAndCents(grant.max_document_value)) + '</td>' +
      '<td class="rl-grant-money">' + esc(dollarsAndCents(grant.max_daily_value)) + '</td>' +
      '<td class="rl-grant-money">' + esc(dataAgeText(grant.max_data_age_hours)) + '</td>' +
      '<td class="rl-grant-created">' + grantCreatedHtml(grant.created_at, grant.created_by) + '</td>' +
      '<td class="rl-grant-state">' + esc(stateText) + '</td>' +
      grantRevokeCellHtml(grant, inForce) +
      '</tr>'
    );
  }

  // The Created cell: the date on its own line, then " by
  // <created_by>" on a second line (the secondary ink token). The
  // second line carries a leading space, so the cell's flat text
  // content stays "DATE by ACTOR".
  function grantCreatedHtml(createdAt, createdBy) {
    var date = datePart(createdAt);
    if (date === EM_DASH) {
      return esc(EM_DASH);
    }
    var html = '<span class="rl-grant-created-date">' + esc(date) + '</span>';
    var by = nonEmptyString(createdBy);
    if (by !== null) {
      html += '<span class="rl-grant-created-by"> by ' + esc(by) + '</span>';
    }
    return html;
  }

  // The admin's Revoke cell: "Revoke" on an in-force row; the first
  // click arms it ("Confirm revoke" plus a "Cancel" button); a failed
  // revoke shows its message beside the row. Revoked rows and
  // non-admins get no control — non-admins no column at all.
  function grantRevokeCellHtml(grant, inForce) {
    if (state.role !== ROLE_ADMIN) {
      return '';
    }
    var form = state.grants.form;
    var html = '<td class="rl-grant-revoke-cell">';
    if (inForce) {
      var armed = form.revokingId === grant.id;
      html +=
        '<button type="button" class="rl-grant-revoke' + (armed ? ' rl-grant-revoke--confirm' : '') + '" ' +
        'data-testid="releases-grant-revoke"' +
        (form.revoking ? ' disabled' : '') +
        '>' +
        esc(armed ? 'Confirm revoke' : 'Revoke') +
        '</button>';
      if (armed) {
        html +=
          '<button type="button" class="rl-grant-cancel" data-testid="releases-grant-cancel"' +
          (form.revoking ? ' disabled' : '') +
          '>Cancel</button>';
      }
      if (form.revokeError && form.revokeError.id === grant.id) {
        html +=
          '<span class="rl-grant-revoke-error" data-testid="releases-grant-revoke-error">' +
          esc(form.revokeError.message) +
          '</span>';
      }
    }
    return html + '</td>';
  }

  // The admin's create form, below the grants table: the process
  // select, the three number inputs (their values live in module
  // state, so a re-render keeps what the admin typed) and the
  // Create grant button. The message line renders only with a
  // message.
  function grantFormHtml() {
    var form = state.grants.form;
    var disabled = form.creating ? ' disabled' : '';
    return (
      '<form class="rl-grant-form" data-testid="releases-grant-form" novalidate>' +
      '<div class="rl-grant-form-controls">' +
      '<label class="rl-grant-form-label">Process</label>' +
      '<select class="rl-grant-process" data-testid="releases-grant-process" name="process"' + disabled + '>' +
      '<option value="order_plan_release"' + (form.process === 'order_plan_release' ? ' selected' : '') + '>Order plan</option>' +
      '<option value="transfer_release"' + (form.process === 'transfer_release' ? ' selected' : '') + '>Transfers</option>' +
      '</select>' +
      '<label class="rl-grant-form-label">Per order</label>' +
      '<input type="number" class="rl-grant-limit" data-testid="releases-grant-per-order" ' +
      'name="max_document_value" min="0" step="0.01" inputmode="decimal" ' +
      'value="' + esc(form.perOrder) + '"' + disabled + '>' +
      '<label class="rl-grant-form-label">Per day</label>' +
      '<input type="number" class="rl-grant-limit" data-testid="releases-grant-per-day" ' +
      'name="max_daily_value" min="0" step="0.01" inputmode="decimal" ' +
      'value="' + esc(form.perDay) + '"' + disabled + '>' +
      '<label class="rl-grant-form-label">Data age (hours)</label>' +
      '<input type="number" class="rl-grant-age" data-testid="releases-grant-data-age" ' +
      'name="max_data_age_hours" min="1" max="168" step="1" inputmode="numeric" ' +
      'value="' + esc(form.dataAge) + '"' + disabled + '>' +
      '<button type="submit" class="btn btn-primary rl-grant-create" data-testid="releases-grant-create"' +
      (form.creating ? ' disabled' : '') +
      '>Create grant</button>' +
      '</div>' +
      (form.error
        ? '<p class="rl-grant-form-error" role="alert" data-testid="releases-grant-form-error">' +
          esc(form.error) +
          '</p>'
        : '') +
      '</form>'
    );
  }

  // The runs table: one row per run, in the answer's order; an empty
  // answer reads the empty note.
  function runsTableHtml() {
    var g = state.grants;
    if (g.runs === null) {
      return '';
    }
    if (g.runs.length === 0) {
      return (
        '<p class="rl-grant-runs-empty" data-testid="releases-grant-runs-empty">' +
        esc(RUNS_EMPTY_NOTE) +
        '</p>'
      );
    }
    var rows = [];
    for (var i = 0; i < g.runs.length; i++) {
      var run = g.runs[i];
      if (!run || typeof run !== 'object') {
        continue;
      }
      rows.push(runRowHtml(run));
    }
    return (
      '<div class="rl-grants-region">' +
      '<table class="rl-grants-runs-table" data-testid="releases-grant-runs">' +
      '<thead><tr>' +
      '<th scope="col">Started</th>' +
      '<th scope="col">Process</th>' +
      '<th scope="col">Outcome</th>' +
      '<th scope="col">Sent</th>' +
      '<th scope="col">Held back</th>' +
      '<th scope="col">Reason</th>' +
      '</tr></thead>' +
      '<tbody>' + rows.join('') + '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  function runRowHtml(run) {
    return (
      '<tr class="rl-grant-run-row" data-testid="releases-grant-run">' +
      '<td class="rl-grant-started">' + esc(utcStamp(run.started_at)) + '</td>' +
      '<td class="rl-grant-process">' + esc(processLabel(run.process)) + '</td>' +
      '<td>' + outcomeBadgeHtml(run.outcome) + '</td>' +
      '<td class="rl-grant-money">' + esc(sentText(run)) + '</td>' +
      '<td class="rl-grant-money">' + esc(heldBackText(run.left_out)) + '</td>' +
      '<td class="rl-grant-reason">' + esc(nonEmptyString(run.reason) || EM_DASH) + '</td>' +
      '</tr>'
    );
  }

  // The runs' Outcome badge: "Failed" in the risk tokens, the rest
  // neutral; an unknown outcome reads itself.
  function outcomeBadgeHtml(outcome) {
    var key = typeof outcome === 'string' ? outcome : '';
    var label = OUTCOME_LABEL[key] || (key !== '' ? key : EM_DASH);
    var cls =
      key === 'failed'
        ? 'rl-grant-outcome rl-grant-outcome--failed'
        : 'rl-grant-outcome rl-grant-outcome--neutral';
    return (
      '<span class="' + cls + '" data-testid="releases-grant-run-outcome">' +
      esc(label) +
      '</span>'
    );
  }

  function pad2(value) {
    return (value < 10 ? '0' : '') + value;
  }

  // A timestamp in UTC as "YYYY-MM-DD HH:MM" (the runs' Started
  // column), else an em dash: the server's ISO string carries its
  // UTC offset, and the display is the UTC instant, never the local
  // one.
  function utcStamp(value) {
    if (typeof value !== 'string' || value === '') {
      return EM_DASH;
    }
    var date = new Date(value);
    if (isNaN(date.getTime())) {
      return EM_DASH;
    }
    return (
      date.getUTCFullYear() +
      '-' +
      pad2(date.getUTCMonth() + 1) +
      '-' +
      pad2(date.getUTCDate()) +
      ' ' +
      pad2(date.getUTCHours()) +
      ':' +
      pad2(date.getUTCMinutes())
    );
  }

  // Dollars and cents (2500 -> "$2,500.00"), parsed from the number
  // or the decimal string; an em dash when there is no figure.
  function dollarsAndCents(value) {
    if (value === null || value === undefined || value === '') {
      return EM_DASH;
    }
    var n = typeof value === 'string' ? Number(value) : value;
    if (!isNumber(n)) {
      return EM_DASH;
    }
    return DOLLAR_CENTS_FORMATTER.format(n);
  }

  // The grant's data age: "<hours> h" (26 -> "26 h"), else an em
  // dash.
  function dataAgeText(value) {
    var n = typeof value === 'string' ? (value === '' ? NaN : Number(value)) : value;
    if (!isNumber(n)) {
      return EM_DASH;
    }
    return String(Math.round(n)) + ' h';
  }

  // The process label: "Order plan" / "Transfers"; an unknown name
  // reads itself as text, an em dash without one.
  function processLabel(processName) {
    var key = typeof processName === 'string' ? processName : '';
    if (Object.prototype.hasOwnProperty.call(PROCESS_LABEL, key)) {
      return PROCESS_LABEL[key];
    }
    return key !== '' ? key : EM_DASH;
  }

  // The runs' Sent column: "<documents> order(s), <value in dollars
  // and cents>" — a failed run's "Tried <documents> order(s),
  // <value>", what it tried to send, none or only some of it having
  // reached the ERP — or an em dash when documents is 0.
  function sentText(run) {
    var raw = run.documents;
    var docs = typeof raw === 'string' ? (raw === '' ? NaN : Number(raw)) : raw;
    if (!isNumber(docs) || docs === 0) {
      return EM_DASH;
    }
    var prefix = run.outcome === 'failed' ? 'Tried ' : '';
    return (
      prefix +
      String(Math.round(docs)) +
      ' order(s), ' +
      dollarsAndCents(run.value)
    );
  }

  // The runs' Held back column: the number of left_out entries, or
  // an em dash when there are none.
  function heldBackText(leftOut) {
    var list = Array.isArray(leftOut) ? leftOut : [];
    return list.length === 0 ? EM_DASH : String(list.length);
  }

  // The form's number inputs as typed: a finite number, else null —
  // null is a value the input cannot hold (an empty field among
  // them).
  function parseLimit(value) {
    var text = typeof value === 'string' ? value : '';
    text = text.replace(/\s/g, '');
    if (text === '') {
      return null;
    }
    var n = Number(text);
    return isNumber(n) ? n : null;
  }

  /* ------------------------------ events ------------------------------- */

  function onRootClick(event) {
    var chip = event.target.closest ? event.target.closest('.rl-chip') : null;
    if (chip) {
      state.filter = chip.getAttribute('data-state') || 'all';
      render();
      return;
    }
    if (event.target.closest ? event.target.closest('[data-testid="releases-refresh"]') : null) {
      load();
      return;
    }
    if (event.target.closest ? event.target.closest('[data-testid="releases-retry"]') : null) {
      load();
      return;
    }
    if (event.target.closest ? event.target.closest('[data-testid="releases-transfers-show"]') : null) {
      loadTransfers();
      return;
    }
    if (event.target.closest ? event.target.closest('[data-testid="releases-grants-show"]') : null) {
      loadGrants();
      return;
    }
    var revokeButton = event.target.closest ? event.target.closest('[data-testid="releases-grant-revoke"]') : null;
    if (revokeButton) {
      var revokeRow = event.target.closest ? revokeButton.closest('tr[data-grant-id]') : null;
      if (revokeRow) {
        var grantId = parseInt(revokeRow.getAttribute('data-grant-id'), 10);
        if (isFinite(grantId) && grantId > 0) {
          if (state.grants.form.revokingId === grantId) {
            confirmRevoke(grantId);
          } else {
            armRevoke(grantId);
          }
        }
      }
      return;
    }
    if (event.target.closest ? event.target.closest('[data-testid="releases-grant-cancel"]') : null) {
      cancelRevoke();
      return;
    }
    var row = event.target.closest ? event.target.closest('tr.rl-row') : null;
    if (row) {
      toggleRow(row);
    }
  }

  // The create form's submit: one POST when the inputs hold values,
  // the 422 message (and no request) when they do not.
  function onRootSubmit(event) {
    var formEl = event.target;
    if (formEl && formEl.getAttribute && formEl.getAttribute('data-testid') === 'releases-grant-form') {
      event.preventDefault();
      createGrant();
    }
  }

  // The form's inputs, kept in module state as typed: a re-render
  // (a refresh landing, a revoke arming) restores what the admin
  // typed. No re-render here — the caret must not move.
  function onRootInput(event) {
    var target = event.target;
    if (!target || !target.getAttribute) {
      return;
    }
    var form = state.grants.form;
    var testid = target.getAttribute('data-testid');
    if (testid === 'releases-grant-process') {
      form.process = target.value === 'transfer_release' ? 'transfer_release' : 'order_plan_release';
    } else if (testid === 'releases-grant-per-order') {
      form.perOrder = target.value;
    } else if (testid === 'releases-grant-per-day') {
      form.perDay = target.value;
    } else if (testid === 'releases-grant-data-age') {
      form.dataAge = target.value;
    }
  }

  function onRootKeydown(event) {
    if (event.key !== 'Enter' && event.key !== ' ') {
      return;
    }
    var row = event.target.closest ? event.target.closest('tr.rl-row') : null;
    if (!row) {
      return;
    }
    event.preventDefault();
    toggleRow(row);
  }

  function toggleRow(row) {
    var id = parseInt(row.getAttribute('data-row-id'), 10);
    if (!isFinite(id) || id < 0) {
      return;
    }
    // The render below replaces this element: when the row held the
    // keyboard focus (the Enter path), hand the focus back to its
    // fresh twin, or the next keypress lands on <body> and the
    // second Enter never collapses.
    var hadFocus = document.activeElement === row;
    state.expanded = state.expanded === id ? null : id;
    render();
    if (hadFocus) {
      var fresh = rootEl.querySelector('tr.rl-row[data-row-id="' + id + '"]');
      if (fresh) {
        fresh.focus();
      }
    }
  }

  /* ------------------------------ lifecycle ---------------------------- */

  function viewActive() {
    var section = document.getElementById(VIEW_ID);
    return section ? section.classList.contains('is-active') : false;
  }

  // The sign-in's role, read off the probe the shell just gated.
  // Only a probe that is in the DOM can have been gated, so a
  // missing probe means the identity is not resolved: the admin
  // controls stay off.
  function captureRole() {
    if (state.role !== ROLE_UNRESOLVED) {
      return; // already resolved for this session
    }
    var probe = rootEl ? rootEl.querySelector('.rl-role-probe') : null;
    if (!probe) {
      return; // not resolved: fail closed
    }
    state.role = probe.hasAttribute('hidden') ? ROLE_OTHER : ROLE_ADMIN;
    render();
  }

  function onAppVisibility() {
    var appEl = document.getElementById(APP_ID);
    if (!appEl) {
      return;
    }
    if (appEl.hidden) {
      // Signed out: the identity is not resolved any more, and a
      // stale role must not outlive the session. Fail closed; the
      // probe comes back so the next sign-in's role can be read.
      if (state.role !== ROLE_UNRESOLVED) {
        state.role = ROLE_UNRESOLVED;
        render();
      }
      return;
    }
    captureRole();
    if (viewActive()) {
      activate();
    }
  }

  function activate() {
    if (!rootEl) {
      return;
    }
    if (!rootEl.firstElementChild) {
      render();
    }
    var appEl = document.getElementById(APP_ID);
    if (appEl && appEl.hidden) {
      // The shell is not signed in yet (Auth.bootstrap has not
      // completed): no request, the skeleton stays put.
      return;
    }
    load();
  }

  function reset() {
    seq += 1; // any answer still out the door belongs to a past session
    transfersSeq += 1; // the section's pending audit answer, too
    grantsSeq += 1; // the grants section's pending reads, too
    formGen += 1; // a pending create or revoke is a past session's
    state.data = null;
    state.loading = false;
    state.error = null;
    state.filter = 'all'; // the "All" default is a first-load one
    state.expanded = null;
    state.transfers.rows = null;
    state.transfers.loading = false;
    state.transfers.error = null;
    state.grants = {
      grants: null,
      runs: null,
      settings: null,
      pending: 0,
      error: null,
      form: {
        process: 'order_plan_release',
        perOrder: '',
        perDay: '',
        dataAge: '',
        error: null,
        creating: false,
        revokingId: null,
        revoking: false,
        revokeError: null,
      },
    };
    state.role = ROLE_UNRESOLVED; // the identity is unknown again
    render();
  }

  (function boot() {
    rootEl = document.getElementById(ROOT_ID);
    if (!rootEl) {
      return;
    }
    rootEl.addEventListener('click', onRootClick);
    rootEl.addEventListener('keydown', onRootKeydown);
    rootEl.addEventListener('submit', onRootSubmit);
    rootEl.addEventListener('input', onRootInput);
    render();

    var appEl = document.getElementById(APP_ID);
    if (!appEl) {
      return;
    }
    // Sign-in shows #app and gates the role probe: when it does and
    // the releases view is the active one, the role lands and the
    // list loads then — the pre-sign-in activation (the shell shows
    // the hash's view before Auth.bootstrap has completed) could not
    // fetch.
    var observer = new MutationObserver(onAppVisibility);
    observer.observe(appEl, { attributes: true, attributeFilter: ['hidden'] });
    if (!appEl.hidden) {
      onAppVisibility();
    }
  })();

  global.Releases = {
    activate: activate,
    reset: reset,
  };
})(window);
