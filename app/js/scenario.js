// AIERP planner screen — the Scenario view (#/scenario).
//
// #/scenario is the what-if screen: the planner moves three levers —
// the target service level (a percentage, 50–99.9, optional) and the
// demand and lead-time changes (each a percentage from -90 to 500,
// 0 meaning no change) — and "Run scenario" re-runs the reorder
// recommendation read-only (POST /api/scenarios/simulate, through
// app/js/api.js): the service level travels as the percentage/100
// (null when the field is blank), each change as 1 + change/100
// (null when 0).
//
//   * the levers form ([data-testid="scenario-levers"]) carries one
//     visible <label for=…> per field and the "Run scenario" button
//     ([data-testid="scenario-run"]), disabled while a request is out;
//   * the per-class service levels fieldset
//     ([data-testid="scenario-class-levels"]) sits between the levers
//     form and the saved-scenarios control: three optional number
//     inputs (A, B, C; 50–99.9, blank by default) that travel
//     together — all three filled sends service_level_by_class
//     (each percentage/100, rounded like the service level), all three
//     blank sends today's body, and one or two filled sends nothing,
//     the status line asking for all three or all none; the answer's
//     echoed mapping shows as the "Class levels: …" note
//     ([data-testid="scenario-class-note"]) in the summary; selecting
//     a saved scenario clears the three inputs (saved scenarios
//     persist only the three levers);
//   * the saved-scenarios control ([data-testid="scenario-saved"])
//     sits alongside the levers: a labelled select
//     ([data-testid="scenario-saved-select"]) whose first option is a
//     non-selectable placeholder and whose remaining options are the
//     saved scenarios in the order GET /api/scenarios returns them
//     (newest first), held in memory so selecting one needs no further
//     request; a labelled name input
//     ([data-testid="scenario-name"]) and Save
//     ([data-testid="scenario-save"]) / Delete
//     ([data-testid="scenario-delete"], disabled on the placeholder)
//     over the control's own status region
//     ([data-testid="scenario-save-msg"], role="status"); picking a
//     saved scenario fills the three fields from it and runs nothing,
//     Save posts the current levers under the trimmed name (a blank
//     name sends nothing and shows "Enter a name to save"), Delete
//     deletes the selected scenario; a failed or empty list load
//     leaves the levers fully runnable, Save and Delete are disabled
//     while a save, load or delete is out, and no save, load or delete
//     ever touches the run status; a separate savedSeq keeps a stale
//     saved-list GET from overwriting newer local state;
//   * while a run is out the status region shows a skeleton, and the
//     answer replaces it; a run started while an older one is still
//     out supersedes it — the older response is ignored and only the
//     newest run's answer is rendered;
//   * the summary ([data-testid="scenario-summary"]) reads the dollar
//     impact as currency, the total reorder-point-unit and
//     safety-stock-unit changes signed with thousands separators, and
//     the item-site counts whose reorder point rises, falls or is
//     unchanged; above zero, a note counts the item-sites without a
//     unit cost, excluded from the dollar figure;
//   * the table ([data-testid="scenario-table"]) lists one row per
//     simulated item-site — item, site, baseline reorder-point units,
//     scenario reorder-point units and the signed unit difference, in
//     the order the response returns them — scrolling inside its own
//     region; the view owns the full height and the document never
//     scrolls;
//   * before any run an empty prompt invites the levers to be set and
//     the scenario run; a 422 (an out-of-range lever) shows the
//     server's message and leaves the levers editable; any other
//     failure shows an inline error with a Retry that re-runs the
//     last levers.
//
// Every string from the API is HTML-escaped before it reaches the
// page; colours, radii and steps come from app/css/tokens.css via
// app/css/scenario.css.
(function (global) {
  'use strict';

  var SIMULATE_PATH = '/api/scenarios/simulate';
  var SAVED_PATH = '/api/scenarios';
  var EM_DASH = '\u2014';
  // The ABC class keys the per-class service-level mapping maps, in
  // the order the note reads them.
  var CLASS_KEYS = ['A', 'B', 'C'];
  // The status line a partly filled class-level set shows instead of
  // a run.
  var CLASS_GATE_MESSAGE = 'Fill in all three class levels, or leave them all blank.';

  var state = {
    running: false,
    result: null,
    error: null,
    lastBody: null,
  };
  // The run sequence: every answer that is not the newest run's is
  // ignored, so a superseded run can never change what is rendered.
  var seq = 0;
  // The class-level gate message: set when a run is refused over a
  // partly filled class-level set; cleared by the next keystroke in
  // the three inputs, a passed run, a saved-scenario selection or a
  // reset.
  var classGateMessage = '';

  // The saved-scenario control: the fetched rows (held so selecting
  // one needs no request), the selected id ('' = the placeholder),
  // whether a save, load or delete is out, and the control's own
  // short message (the run status region is never touched).
  var savedRows = [];
  var savedSelectedId = '';
  var savedBusy = false;
  var savedMessage = '';
  // The saved-list sequence, distinct from the run seq: every saved
  // list answer that is not the newest fetch's is ignored, so a stale
  // GET can never overwrite newer local state.
  var savedSeq = 0;

  var rootEl = null;
  var statusEl = null;
  var savedSelectEl = null;
  var savedNameEl = null;
  var savedSaveBtn = null;
  var savedDeleteBtn = null;
  var savedMsgEl = null;

  var CURRENCY_FORMATTER = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
  });

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

  // Lever math: percentages to their wire values, the float artefacts
  // of the division and the addition rounded away (1 - 90/100 must
  // read as 0.1, not 0.09999999999999998).
  function roundLever(value) {
    return Math.round(value * 1e10) / 1e10;
  }

  // A percentage figure rounded to at most two decimals, trailing
  // zeros dropped (0.98 -> "98", 0.955 -> "95.5"): the echoed class
  // levels read the way the service-level line does.
  function percentFigure(value) {
    return String(Math.round(value * 100) / 100);
  }

  // Currency with thousands separators (-84210.55 -> "-$84,210.55");
  // a missing or non-numeric value renders as an em dash.
  function currency(value) {
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return CURRENCY_FORMATTER.format(value);
  }

  // Whole units with thousands separators (10460.7 -> "10,461");
  // a missing or non-numeric value renders as an em dash.
  function units(value) {
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return Math.round(value).toLocaleString('en-US');
  }

  // A signed whole-unit figure: 12345 -> "+12,345", -678 -> "-678",
  // 0 -> "0"; a missing or non-numeric value renders as an em dash.
  function signedUnits(value) {
    if (!isNumber(value)) {
      return EM_DASH;
    }
    var rounded = Math.round(value);
    var text = Math.abs(rounded).toLocaleString('en-US');
    if (rounded > 0) {
      return '+' + text;
    }
    if (rounded < 0) {
      return '-' + text;
    }
    return '0';
  }

  // A plain count (the up/down/unchanged figures), thousands
  // separators for very large portfolios; non-numeric renders as an
  // em dash.
  function count(value) {
    if (!isNumber(value)) {
      return EM_DASH;
    }
    return Math.round(value).toLocaleString('en-US');
  }

  function leverValue(testId) {
    var el = rootEl ? rootEl.querySelector('[data-testid="' + testId + '"]') : null;
    return el ? String(el.value).trim() : '';
  }

  // The levers as the wire body: the service level as the
  // percentage/100 (null while blank), each change as 1 + change/100
  // (null when 0, and while the field is blank — blank is no change).
  function buildBody() {
    var body = {
      service_level: null,
      demand_multiplier: null,
      lead_time_multiplier: null,
    };
    var serviceLevel = parseFloat(leverValue('scenario-service-level'));
    if (isFinite(serviceLevel)) {
      body.service_level = roundLever(serviceLevel / 100);
    }
    var demandChange = parseFloat(leverValue('scenario-demand-change'));
    if (isFinite(demandChange) && demandChange !== 0) {
      body.demand_multiplier = roundLever(1 + demandChange / 100);
    }
    var leadTimeChange = parseFloat(leverValue('scenario-lead-time-change'));
    if (isFinite(leadTimeChange) && leadTimeChange !== 0) {
      body.lead_time_multiplier = roundLever(1 + leadTimeChange / 100);
    }
    var classLevels = classLevelBody();
    if (classLevels) {
      body.service_level_by_class = classLevels;
    }
    return body;
  }

  // The per-class service levels as the wire mapping: each percentage
  // /100, rounded the way the service-level lever rounds. Only when
  // all three inputs hold finite numbers; a partial set or all blank
  // returns null (all blank omits the key — the body is exactly
  // today's).
  function classLevelBody() {
    var a = parseFloat(leverValue('scenario-class-level-a'));
    var b = parseFloat(leverValue('scenario-class-level-b'));
    var c = parseFloat(leverValue('scenario-class-level-c'));
    if (!isFinite(a) || !isFinite(b) || !isFinite(c)) {
      return null;
    }
    return {
      A: roundLever(a / 100),
      B: roundLever(b / 100),
      C: roundLever(c / 100),
    };
  }

  // How many of the three class-level inputs hold a value: all three
  // (the mapping travels), none (the body is today's) or a partial
  // set (the run is refused — no request, the status line
  // explains).
  function classLevelFilledCount() {
    var count = 0;
    if (leverValue('scenario-class-level-a') !== '') {
      count += 1;
    }
    if (leverValue('scenario-class-level-b') !== '') {
      count += 1;
    }
    if (leverValue('scenario-class-level-c') !== '') {
      count += 1;
    }
    return count;
  }

  // A stored lever set to what the three fields read: the service
  // level as a percentage (blank while null), each multiplier as
  // (m - 1) * 100 ("0" while null) — both through roundLever
  // (0.98 -> "98", 1.2 -> "20"). A hostile row (missing or non-
  // numeric levers) reads like a null lever: the view never throws.
  function leverFieldValues(row) {
    var source = row && typeof row === 'object' ? row : {};
    function given(value) {
      return typeof value === 'number' && isFinite(value) ? value : null;
    }
    var serviceLevel = given(source.service_level);
    var demand = given(source.demand_multiplier);
    var leadTime = given(source.lead_time_multiplier);
    return {
      serviceLevel: serviceLevel === null ? '' : String(roundLever(serviceLevel * 100)),
      demandChange: demand === null ? '0' : String(roundLever((demand - 1) * 100)),
      leadTimeChange: leadTime === null ? '0' : String(roundLever((leadTime - 1) * 100)),
    };
  }

  /* ------------------------------ markup ------------------------------ */

  function leverField(id, testId, label, attributes, value, placeholder) {
    return (
      '<div class="sc-field">' +
      '<label class="sc-label" for="' + id + '">' + label + '</label>' +
      '<input class="sc-input" id="' + id + '" data-testid="' + testId + '" type="number" ' +
        attributes +
        ' value="' + esc(value) + '"' +
        (placeholder ? ' placeholder="' + esc(placeholder) + '"' : '') +
      '</div>'
    );
  }

  function renderRoot() {
    rootEl.innerHTML =
      '<form class="sc-levers" data-testid="scenario-levers" novalidate>' +
      leverField(
        'sc-service-level',
        'scenario-service-level',
        'Target service level (%)',
        'min="50" max="99.9" step="0.1" inputmode="decimal"',
        '',
        'Optional, e.g. 98'
      ) +
      leverField(
        'sc-demand-change',
        'scenario-demand-change',
        'Demand change (%)',
        'min="-90" max="500" step="1" inputmode="decimal"',
        '0'
      ) +
      leverField(
        'sc-lead-time-change',
        'scenario-lead-time-change',
        'Lead-time change (%)',
        'min="-90" max="500" step="1" inputmode="decimal"',
        '0'
      ) +
      '<div class="sc-field sc-field-run">' +
      '<button type="submit" class="btn btn-primary sc-run" id="scenario-run" data-testid="scenario-run">Run scenario</button>' +
      '</div>' +
      '</form>' +
      '<fieldset class="sc-class-levels" data-testid="scenario-class-levels">' +
      '<legend class="sc-class-levels-legend">Service level by class (%)</legend>' +
      '<div class="sc-class-levels-row">' +
      leverField(
        'sc-class-level-a',
        'scenario-class-level-a',
        'A',
        'min="50" max="99.9" step="0.1" inputmode="decimal"',
        '',
        'e.g. 98'
      ) +
      leverField(
        'sc-class-level-b',
        'scenario-class-level-b',
        'B',
        'min="50" max="99.9" step="0.1" inputmode="decimal"',
        '',
        'e.g. 95'
      ) +
      leverField(
        'sc-class-level-c',
        'scenario-class-level-c',
        'C',
        'min="50" max="99.9" step="0.1" inputmode="decimal"',
        '',
        'e.g. 90'
      ) +
      '</div>' +
      '</fieldset>' +
      savedControlHtml() +
      '<div class="sc-status" data-testid="scenario-status"></div>';
    statusEl = rootEl.querySelector('.sc-status');
    savedSelectEl = rootEl.querySelector('[data-testid="scenario-saved-select"]');
    savedNameEl = rootEl.querySelector('[data-testid="scenario-name"]');
    savedSaveBtn = rootEl.querySelector('[data-testid="scenario-save"]');
    savedDeleteBtn = rootEl.querySelector('[data-testid="scenario-delete"]');
    savedMsgEl = rootEl.querySelector('[data-testid="scenario-save-msg"]');
    renderStatus();
    renderSaved();
  }

  /* ------------------------- saved scenarios ------------------------- */

  function savedControlHtml() {
    return (
      '<section class="card sc-saved" data-testid="scenario-saved" aria-label="Saved scenarios">' +
      '<div class="sc-saved-row">' +
      '<div class="sc-field">' +
      '<label class="sc-label" for="sc-saved-select">Saved scenario</label>' +
      '<select class="sc-select" id="sc-saved-select" data-testid="scenario-saved-select">' +
      '<option value="" disabled selected>Load a saved scenario\u2026</option>' +
      '</select>' +
      '</div>' +
      '<div class="sc-field">' +
      '<label class="sc-label" for="sc-saved-name">New scenario name</label>' +
      '<input class="sc-input" id="sc-saved-name" data-testid="scenario-name" type="text" maxlength="200" autocomplete="off" placeholder="Name the current levers">' +
      '</div>' +
      '<div class="sc-field sc-field-save">' +
      '<button type="button" class="btn btn-primary sc-save" id="scenario-save" data-testid="scenario-save">Save</button>' +
      '</div>' +
      '<div class="sc-field sc-field-delete">' +
      '<button type="button" class="btn btn-secondary sc-delete" id="scenario-delete" data-testid="scenario-delete" disabled>Delete</button>' +
      '</div>' +
      '</div>' +
      '<p class="sc-saved-msg" data-testid="scenario-save-msg" role="status"></p>' +
      '</section>'
    );
  }

  // The control's own short message, the fetched rows and the button
  // states — the run status region is never touched here.
  function renderSaved() {
    if (!savedSelectEl || !savedNameEl || !savedSaveBtn || !savedDeleteBtn || !savedMsgEl) {
      return;
    }
    var html = '<option value="" disabled selected>Load a saved scenario\u2026</option>';
    for (var i = 0; i < savedRows.length; i++) {
      var row = savedRows[i];
      html += '<option value="' + esc(row.id) + '">' + esc(row.name) + '</option>';
    }
    savedSelectEl.innerHTML = html;
    if (savedSelectedId) {
      savedSelectEl.value = savedSelectedId;
      if (savedSelectEl.value !== savedSelectedId) {
        savedSelectedId = ''; // the row left the fetched list
      }
    }
    savedSaveBtn.disabled = savedBusy;
    savedDeleteBtn.disabled = savedBusy || savedSelectedId === '';
    savedMsgEl.textContent = savedMessage;
  }

  // GET /api/scenarios, guarded by the saved-list sequence. A failed or
  // empty load (or a malformed payload) leaves the control present and
  // the levers fully runnable; options: announceFailure shows the
  // control's error line on a failure (the primary load from
  // activate), clearOnSuccess clears it on success — a refresh after a
  // successful mutation keeps the mutation's message either way, so a
  // good save is never reported as failed by a bad refresh.
  function fetchSavedList(options) {
    var announceFailure = !options || options.announceFailure !== false;
    var clearOnSuccess = !options || options.clearOnSuccess !== false;
    savedSeq += 1;
    var mySeq = savedSeq;
    savedBusy = true;
    renderSaved();
    return global.Api.get(SAVED_PATH).then(
      function (payload) {
        if (mySeq !== savedSeq) {
          return; // a newer fetch was started: this list is stale
        }
        if (!Array.isArray(payload)) {
          // A malformed list answer: treat it as a failed load — the
          // previously fetched rows (and the selection) stay put.
          savedBusy = false;
          if (announceFailure) {
            savedMessage = 'Saved scenarios could not be loaded.';
          }
          renderSaved();
          return;
        }
        var rows = [];
        for (var i = 0; i < payload.length; i++) {
          var row = payload[i];
          if (row && typeof row === 'object' && typeof row.id === 'string' && row.id !== '' && typeof row.name === 'string') {
            rows.push(row);
          }
        }
        savedRows = rows;
        if (savedSelectedId) {
          var stillThere = false;
          for (var j = 0; j < rows.length; j++) {
            if (rows[j].id === savedSelectedId) {
              stillThere = true;
              break;
            }
          }
          if (!stillThere) {
            savedSelectedId = '';
          }
        }
        if (clearOnSuccess) {
          savedMessage = '';
        }
        savedBusy = false;
        renderSaved();
      },
      function (error) {
        if (mySeq !== savedSeq) {
          return;
        }
        savedBusy = false;
        // A 401 is the global auth flow's (sign-out); the control's
        // message stays untouched.
        if (announceFailure && !(error && error.unauthorized)) {
          savedMessage = 'Saved scenarios could not be loaded.';
        }
        renderSaved();
      }
    );
  }

  // Select a fetched row by id: fills the three fields from it and
  // enables Delete, running nothing. Unknown ids are a no-op — there
  // is no stored row to load; the answer says so.
  function selectSavedRow(id) {
    var row = null;
    for (var i = 0; i < savedRows.length; i++) {
      if (savedRows[i].id === id) {
        row = savedRows[i];
        break;
      }
    }
    if (!row) {
      return false;
    }
    applySavedRow(row);
    savedSelectedId = row.id;
    renderSaved();
    return true;
  }

  function applySavedRow(row) {
    var values = leverFieldValues(row);
    var serviceEl = rootEl ? rootEl.querySelector('[data-testid="scenario-service-level"]') : null;
    var demandEl = rootEl ? rootEl.querySelector('[data-testid="scenario-demand-change"]') : null;
    var leadEl = rootEl ? rootEl.querySelector('[data-testid="scenario-lead-time-change"]') : null;
    if (serviceEl) {
      serviceEl.value = values.serviceLevel;
    }
    if (demandEl) {
      demandEl.value = values.demandChange;
    }
    if (leadEl) {
      leadEl.value = values.leadTimeChange;
    }
    // Saved scenarios persist only the three levers: the class-level
    // inputs go back to blank, so a later run cannot silently carry
    // class levels the saved scenario never stored.
    var classAEl = rootEl ? rootEl.querySelector('[data-testid="scenario-class-level-a"]') : null;
    var classBEl = rootEl ? rootEl.querySelector('[data-testid="scenario-class-level-b"]') : null;
    var classCEl = rootEl ? rootEl.querySelector('[data-testid="scenario-class-level-c"]') : null;
    if (classAEl) {
      classAEl.value = '';
    }
    if (classBEl) {
      classBEl.value = '';
    }
    if (classCEl) {
      classCEl.value = '';
    }
    if (classGateMessage) {
      classGateMessage = '';
      renderStatus();
    }
  }

  // Save: the trimmed name plus the current levers (the same
  // buildBody the run uses). A blank name sends nothing. A 422 shows
  // the server's message; any other failure a short generic one. A 401
  // leaves the save status untouched — the global auth flow owns the
  // sign-out.
  function saveScenario() {
    if (savedBusy) {
      return;
    }
    var name = savedNameEl ? String(savedNameEl.value).trim() : '';
    if (name === '') {
      savedMessage = 'Enter a name to save';
      renderSaved();
      return;
    }
    var levers = buildBody();
    var payload = {
      name: name,
      service_level: levers.service_level,
      demand_multiplier: levers.demand_multiplier,
      lead_time_multiplier: levers.lead_time_multiplier,
    };
    savedBusy = true;
    renderSaved();
    global.Api.request(SAVED_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(
      function (created) {
        var newId =
          created && typeof created === 'object' && typeof created.id === 'string' && created.id !== ''
            ? created.id
            : '';
        if (savedNameEl) {
          savedNameEl.value = '';
        }
        // Refresh the list (never reported as a failed save), then
        // select the new scenario when the refresh lists it; when it
        // does not (a failed refresh, a row the list will not hold)
        // the select stays on the placeholder — no optimistic insert
        // of the returned scenario.
        return fetchSavedList({ announceFailure: false, clearOnSuccess: false }).then(function () {
          if (!selectSavedRow(newId)) {
            savedSelectedId = '';
          }
          savedMessage = 'Saved';
          savedBusy = false;
          renderSaved();
        });
      },
      function (error) {
        if (error && error.unauthorized) {
          savedBusy = false;
          renderSaved();
          return;
        }
        savedMessage =
          error && error.status === 422 && error.message ? error.message : 'The scenario could not be saved.';
        savedBusy = false;
        renderSaved();
      }
    );
  }

  // Delete the selected scenario: DELETE /api/scenarios/{id}. The
  // lever fields are left unchanged; a 404 (already gone) refreshes
  // the list and says so rather than throwing; a 401 is the global
  // auth flow's and leaves the save status untouched.
  function deleteScenario() {
    if (savedBusy || savedSelectedId === '') {
      return;
    }
    var id = savedSelectedId;
    savedBusy = true;
    renderSaved();
    global.Api.request(SAVED_PATH + '/' + encodeURIComponent(id), { method: 'DELETE' }).then(
      function () {
        return fetchSavedList({ announceFailure: false, clearOnSuccess: false }).then(function () {
          savedSelectedId = ''; // back to the placeholder: Delete disabled
          savedMessage = 'Deleted';
          savedBusy = false;
          renderSaved();
        });
      },
      function (error) {
        if (error && error.unauthorized) {
          savedBusy = false;
          renderSaved();
          return;
        }
        if (error && error.status === 404) {
          return fetchSavedList({ announceFailure: false, clearOnSuccess: false }).then(function () {
            savedSelectedId = '';
            savedMessage = 'That scenario is already gone.';
            savedBusy = false;
            renderSaved();
          });
        }
        savedMessage = 'The scenario could not be deleted.';
        savedBusy = false;
        renderSaved();
      }
    );
  }

  function skeletonHtml() {
    return (
      '<div class="sc-skel-wrap" data-testid="scenario-skeleton" aria-hidden="true">' +
      '<div class="skel skel-line"></div>' +
      '<div class="skel skel-line skel-wide"></div>' +
      '<div class="skel sc-skel-block"></div>' +
      '</div>'
    );
  }

  function errorHtml() {
    // A 422 is the server refusing the levers: the message is shown
    // and the form stays the way back — no Retry, because the levers
    // (not the request) need the attention.
    var retry =
      state.error.status !== 422
        ? '<button type="button" class="btn sc-retry" id="scenario-retry" data-testid="scenario-retry">Retry</button>'
        : '';
    return (
      '<div class="sc-error" role="alert" data-testid="scenario-error">' +
      '<p class="sc-error-msg" data-testid="scenario-error-msg">' + esc(state.error.message) + '</p>' +
      retry +
      '</div>'
    );
  }

  function exclusionNote(summary) {
    var withoutCost = isNumber(summary.item_sites_without_unit_cost)
      ? Math.round(summary.item_sites_without_unit_cost)
      : 0;
    if (withoutCost <= 0) {
      return '';
    }
    return (
      '<p class="sc-exclusion" data-testid="scenario-exclusion-note">' +
      count(withoutCost) +
      ' item-site' +
      (withoutCost === 1 ? ' has no unit cost and is excluded from the dollar figure.' : 's have no unit cost and are excluded from the dollar figure.') +
      '</p>'
    );
  }

  // The gate line: a partly filled class-level set refuses the run
  // — no request, and the status region says the fields must be
  // filled together or left all blank.
  function gateMessageHtml() {
    return (
      '<p class="sc-class-gate" role="status" data-testid="scenario-class-gate">' +
      esc(classGateMessage) +
      '</p>'
    );
  }

  // The echo of the per-class mapping the run sent:
  // "Class levels: A 98% · B 95% · C 90%" (the echoed
  // fractions ×100, trailing zeros dropped, like the
  // service-level line). No element while the echo is not an object
  // with class values.
  function classNoteHtml(summary) {
    var byClass = summary && summary.service_level_by_class;
    if (!byClass || typeof byClass !== 'object' || Array.isArray(byClass)) {
      return '';
    }
    var parts = [];
    for (var i = 0; i < CLASS_KEYS.length; i += 1) {
      var key = CLASS_KEYS[i];
      var value = byClass[key];
      if (isNumber(value)) {
        parts.push(key + ' ' + percentFigure(value * 100) + '%');
      }
    }
    if (parts.length === 0) {
      return '';
    }
    return (
      '<p class="sc-class-note" data-testid="scenario-class-note">' +
      esc('Class levels: ' + parts.join(' \u00B7 ')) +
      '</p>'
    );
  }

  function summaryHtml(result) {
    var summary = result.summary || {};
    return (
      '<section class="card sc-summary" data-testid="scenario-summary" aria-label="Scenario summary">' +
      '<dl class="sc-summary-grid">' +
      '<div class="sc-stat">' +
      '<dt class="sc-stat-label">Dollar impact</dt>' +
      '<dd class="sc-stat-value" data-testid="scenario-dollar-impact">' +
      currency(summary.dollar_impact) +
      '</dd>' +
      '</div>' +
      '<div class="sc-stat">' +
      '<dt class="sc-stat-label">Reorder-point units</dt>' +
      '<dd class="sc-stat-value" data-testid="scenario-rp-total">' +
      signedUnits(summary.reorder_point_units_delta_total) +
      '</dd>' +
      '</div>' +
      '<div class="sc-stat">' +
      '<dt class="sc-stat-label">Safety-stock units</dt>' +
      '<dd class="sc-stat-value" data-testid="scenario-ss-total">' +
      signedUnits(summary.safety_stock_units_delta_total) +
      '</dd>' +
      '</div>' +
      '<div class="sc-stat sc-stat-counts">' +
      '<dt class="sc-stat-label">Reorder point</dt>' +
      '<dd class="sc-stat-value sc-counts">' +
      '<span data-testid="scenario-count-up">' + count(summary.reorder_point_up) + '</span> up, ' +
      '<span data-testid="scenario-count-down">' + count(summary.reorder_point_down) + '</span> down, ' +
      '<span data-testid="scenario-count-unchanged">' + count(summary.reorder_point_unchanged) + '</span> unchanged' +
      '</dd>' +
      '</div>' +
      '</dl>' +
      exclusionNote(summary) +
      classNoteHtml(summary) +
      '</section>'
    );
  }

  function tableHtml(result) {
    var rows = Array.isArray(result.item_sites) ? result.item_sites : [];
    var body = '';
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i] || {};
      var baseline = row.baseline || {};
      var scenario = row.scenario || {};
      var delta = row.reorder_point_units_delta;
      var deltaClass = 'sc-delta sc-delta-flat';
      if (isNumber(delta) && delta > 0) {
        deltaClass = 'sc-delta sc-delta-up';
      } else if (isNumber(delta) && delta < 0) {
        deltaClass = 'sc-delta sc-delta-down';
      }
      body +=
        '<tr class="sc-row" data-testid="scenario-row" data-item="' + esc(row.item_code) + '" data-site="' +
        esc(row.site_code) + '">' +
        '<td class="sc-code">' + esc(row.item_code) + '</td>' +
        '<td class="sc-code">' + esc(row.site_code) + '</td>' +
        '<td class="sc-num">' + units(baseline.reorder_point_units) + '</td>' +
        '<td class="sc-num">' + units(scenario.reorder_point_units) + '</td>' +
        '<td class="sc-num ' + deltaClass + '">' + signedUnits(delta) + '</td>' +
        '</tr>';
    }
    return (
      '<div class="sc-table-region" data-testid="scenario-table-region">' +
      '<table class="sc-table" data-testid="scenario-table">' +
      '<thead><tr>' +
      '<th scope="col">Item</th>' +
      '<th scope="col">Site</th>' +
      '<th scope="col" class="sc-num">Baseline RPO (units)</th>' +
      '<th scope="col" class="sc-num">Scenario RPO (units)</th>' +
      '<th scope="col" class="sc-num">RPO change (units)</th>' +
      '</tr></thead>' +
      '<tbody>' +
      body +
      '</tbody>' +
      '</table>' +
      '</div>'
    );
  }

  function renderStatus() {
    if (!rootEl || !statusEl) {
      return;
    }
    var run = rootEl.querySelector('[data-testid="scenario-run"]');
    if (run) {
      run.disabled = state.running;
    }
    if (state.running) {
      statusEl.innerHTML = skeletonHtml();
    } else if (state.error) {
      statusEl.innerHTML = errorHtml();
    } else if (classGateMessage) {
      statusEl.innerHTML = gateMessageHtml();
    } else if (state.result) {
      statusEl.innerHTML = summaryHtml(state.result) + tableHtml(state.result);
    } else {
      statusEl.innerHTML =
        '<p class="sc-empty" data-testid="scenario-empty">Set the levers and run the scenario to see how the reorder plan would change.</p>';
    }
  }

  /* ------------------------------- runs -------------------------------- */

  function startRun(body) {
    state.lastBody = body;
    state.error = null;
    state.result = null;
    state.running = true;
    seq += 1;
    var mySeq = seq;
    renderStatus();
    global.Api.request(SIMULATE_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(
      function (payload) {
        if (mySeq !== seq) {
          return; // a newer run was started: this answer is stale
        }
        state.result = payload && typeof payload === 'object' ? payload : {};
        state.running = false;
        renderStatus();
      },
      function (error) {
        if (mySeq !== seq) {
          return;
        }
        state.error = {
          status: error && isNumber(error.status) ? error.status : 0,
          message: error && error.message ? error.message : 'The scenario could not be run.',
        };
        state.running = false;
        renderStatus();
      }
    );
  }

  function run() {
    var filled = classLevelFilledCount();
    if (filled > 0 && filled < 3) {
      // A partial set: no request — the status line says the
      // fields must be filled together or left all blank.
      classGateMessage = CLASS_GATE_MESSAGE;
      renderStatus();
      return;
    }
    classGateMessage = '';
    startRun(buildBody());
  }

  // The Retry button: re-runs the last levers — the ones the failed
  // request carried, whatever the fields read since.
  function retry() {
    if (state.lastBody) {
      startRun(state.lastBody);
      return;
    }
    run();
  }

  /* ----------------------------- events ------------------------------- */

  function onRootSubmit(event) {
    var target = event.target;
    if (!target || !target.getAttribute || target.getAttribute('data-testid') !== 'scenario-levers') {
      return;
    }
    event.preventDefault();
    run();
  }

  function onRootClick(event) {
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }
    if (target.closest('[data-testid="scenario-retry"]')) {
      retry();
      return;
    }
    if (target.closest('[data-testid="scenario-save"]')) {
      saveScenario();
      return;
    }
    if (target.closest('[data-testid="scenario-delete"]')) {
      deleteScenario();
    }
  }

  function onRootChange(event) {
    var target = event.target;
    if (!target || !target.getAttribute || target.getAttribute('data-testid') !== 'scenario-saved-select') {
      return;
    }
    // Choosing a saved scenario fills the fields and enables Delete,
    // running nothing; the placeholder disables Delete and leaves the
    // fields as they are.
    if (target.value === '') {
      savedSelectedId = '';
      renderSaved();
      return;
    }
    selectSavedRow(target.value);
  }

  // A keystroke in a class-level input addresses the gate message —
  // the user is fixing or clearing the set, so the line goes away.
  function onRootInput(event) {
    var target = event.target;
    if (!target || !target.getAttribute) {
      return;
    }
    var testId = target.getAttribute('data-testid');
    if (
      testId !== 'scenario-class-level-a' &&
      testId !== 'scenario-class-level-b' &&
      testId !== 'scenario-class-level-c'
    ) {
      return;
    }
    if (classGateMessage) {
      classGateMessage = '';
      renderStatus();
    }
  }

  /* -------------------------------- boot ------------------------------- */

  function scenarioActive() {
    var section = document.getElementById('view-scenario');
    return section ? section.classList.contains('is-active') : false;
  }

  function activate() {
    // The view renders its levers and empty prompt on boot. A root
    // left empty (a reset while the shell was hidden) is rebuilt, and
    // every activation refreshes the saved-scenario list (newest
    // first) — but only once the signed-in shell is visible, so the
    // pre-sign-in activation sends no request.
    if (!rootEl) {
      return;
    }
    if (!rootEl.firstElementChild) {
      renderRoot();
    }
    var appEl = document.getElementById('app');
    if (appEl && appEl.hidden) {
      return;
    }
    fetchSavedList();
  }

  function reset() {
    seq += 1; // any answer still out the door belongs to a past run
    savedSeq += 1; // ... and so does any saved list still out the door
    state.running = false;
    state.result = null;
    state.error = null;
    state.lastBody = null;
    savedRows = [];
    savedSelectedId = '';
    savedBusy = false;
    savedMessage = '';
    classGateMessage = '';
    if (!rootEl) {
      return;
    }
    renderRoot();
  }

  (function boot() {
    rootEl = document.getElementById('scenario-root');
    if (!rootEl) {
      return;
    }
    rootEl.addEventListener('submit', onRootSubmit);
    rootEl.addEventListener('click', onRootClick);
    rootEl.addEventListener('change', onRootChange);
    rootEl.addEventListener('input', onRootInput);
    renderRoot();

    var appEl = document.getElementById('app');
    if (!appEl) {
      return;
    }
    // Sign-in shows #app: when it does and the scenario view is the
    // active one, the saved list loads then — the activation that ran
    // before sign-in could not fetch.
    var observer = new MutationObserver(function () {
      if (!appEl.hidden && scenarioActive()) {
        activate();
      }
    });
    observer.observe(appEl, { attributes: true, attributeFilter: ['hidden'] });
    if (!appEl.hidden && scenarioActive()) {
      activate();
    }
  })();

  global.Scenario = {
    activate: activate,
    reset: reset,
  };
})(window);
