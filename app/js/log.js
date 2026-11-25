// AIERP planner screen — the Decision log view (#/log).
//
// #/log is a single card holding a table of every recorded decision
// across the three lists, merged and sorted newest first by
// decided_at. The data comes, every call through app/js/api.js, from:
//   * GET /api/drift/decisions?limit=1000,
//   * GET /api/excess/decisions?limit=1000,
//   * GET /api/stockout/decisions?limit=1000.
//
// The columns are:
//   * List — "Reorder point", "Excess and dead stock" or
//     "Stockout risk";
//   * Item and site — "<item_code> · <site_code>", as a link to
//     #/queue?kind=<kind>&item=<item_code>&site=<site_code>;
//   * Decision — the reason after a dismissal, "until <snooze_until>"
//     after a snooze, and the action label (Stop replenishing, Reduce,
//     Dispose, Expedite, Raise order or Transfer) after an accept that
//     recorded an action; an em dash when nothing was recorded;
//   * Money — money_at_stake, money_tied_up or money_at_risk
//     respectively, formatted exactly as the Overview's moneyLabel
//     (copied here; overview.js is not changed), an em dash when null;
//   * Status — a badge reading Accepted, Dismissed or Snoozed, styled
//     as the design's Status badges table says;
//   * Decided — the decider's display name (decided_by_name,
//     falling back to decided_by for rows written before names
//     were recorded) and the decided_at date.
//
// The table header stays in place while the body scrolls inside its
// own region; the page itself never scrolls, and at narrow widths the
// table scrolls sideways inside its card. With no decisions the card
// centres an empty state on both axes: the heading "No decisions
// recorded yet", a sentence pointing at the Work queue, and an
// "Open the Work queue" link to #/queue. A failed load shows an
// inline error with a Retry button that repeats the three requests.
//
// The list cut line (issue #1600): the server lists at most 1,000
// decisions per list, so when one or more of the three answers is an
// array of exactly 1,000 entries — counted on the answers as
// received — the log card shows, directly before the table's
// scrolling region, a status line reading "Showing the first 1,000
// decisions of: <names>. There may be more.", <names> the full
// answers' list names (Reorder point, Excess and dead stock, Stockout
// risk) in that order, joined by ", ". Decided again on every load;
// with no full answer, and in the loading, error and empty states, no
// line at all.
//
// The view is read-only and is shown to any signed-in user. Every
// string from the API is HTML-escaped before it reaches the page.
(function (global) {
  'use strict';

  var EM_DASH = '\u2014';

  var ENDPOINTS = [
    {
      kind: 'drift',
      list: 'Reorder point',
      endpoint: '/api/drift/decisions?limit=1000',
      moneyField: 'money_at_stake',
    },
    {
      kind: 'excess',
      list: 'Excess and dead stock',
      endpoint: '/api/excess/decisions?limit=1000',
      moneyField: 'money_tied_up',
    },
    {
      kind: 'stockout',
      list: 'Stockout risk',
      endpoint: '/api/stockout/decisions?limit=1000',
      moneyField: 'money_at_risk',
    },
  ];

  // The action labels of an accept that recorded an action
  // (docs/design/planner-screen.md).
  var ACTION_LABELS = {
    stop_replenishing: 'Stop replenishing',
    reduce: 'Reduce',
    dispose: 'Dispose',
    expedite: 'Expedite',
    raise_order: 'Raise order',
    transfer: 'Transfer',
  };

  var state = {
    loading: false,
    error: null,
    rows: [], // [{ meta, entry }] newest first
    cutLists: [], // issue #1600: list names of the answers that arrived with exactly 1,000 entries, in endpoint order
  };
  var loadSeq = 0;
  var rootEl = null;

  /* ------------------------------ helpers ------------------------------ */

  function esc(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function isMoney(value) {
    return typeof value === 'number' && isFinite(value);
  }

  // Whole dollars with thousands separators: 18720 -> "$18,720".
  // null/undefined (the only "without money" values) renders as an em dash.
  // Copied from app/js/overview.js so the log formats money exactly the
  // way the Overview does.
  function moneyLabel(value) {
    if (!isMoney(value)) {
      return '\u2014';
    }
    return '$' + Math.round(value).toLocaleString('en-US');
  }

  // "2026-09-18 09:00 UTC" from an API timestamp; the raw text when it
  // does not carry a parseable date.
  function formatDecidedAt(value) {
    var text = value == null ? '' : String(value);
    var match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(text);
    if (match) {
      return match[1] + ' ' + match[2] + ' UTC';
    }
    return text;
  }

  // The name the Decided column shows: decided_by_name, falling back
  // to decided_by when the name is absent, null, empty or whitespace
  // only (rows written before names were recorded).
  function decidedByName(entry) {
    var name = entry.decided_by_name == null ? '' : String(entry.decided_by_name);
    if (name.trim() !== '') {
      return name;
    }
    return entry.decided_by == null ? '' : String(entry.decided_by);
  }

  function decidedAtMs(value) {
    if (value == null) {
      return -Infinity;
    }
    var ms = Date.parse(String(value));
    return isNaN(ms) ? -Infinity : ms;
  }

  function actionLabel(value) {
    return Object.prototype.hasOwnProperty.call(ACTION_LABELS, value)
      ? ACTION_LABELS[value]
      : value;
  }

  /* ------------------------------ content ------------------------------ */

  function decisionText(entry) {
    if (entry.decision === 'dismissed') {
      var reason = entry.reason == null ? '' : String(entry.reason);
      return reason.trim() ? reason : EM_DASH;
    }
    if (entry.decision === 'snoozed') {
      return entry.snooze_until == null ? EM_DASH : 'until ' + String(entry.snooze_until);
    }
    if (entry.decision === 'accepted') {
      return entry.action ? actionLabel(String(entry.action)) : EM_DASH;
    }
    return EM_DASH;
  }

  function statusBadge(entry) {
    if (entry.decision === 'accepted') {
      return { text: 'Accepted', className: 'q-badge-accepted' };
    }
    if (entry.decision === 'dismissed') {
      return { text: 'Dismissed', className: 'q-badge-dismissed' };
    }
    if (entry.decision === 'snoozed') {
      return { text: 'Snoozed', className: 'q-badge-snoozed' };
    }
    return null;
  }

  // Merge the three decision lists, newest first by decided_at (the id
  // breaking ties, mirroring the API's own ordering).
  function mergeDecisions(lists) {
    var merged = [];
    for (var i = 0; i < ENDPOINTS.length; i++) {
      var meta = ENDPOINTS[i];
      var list = lists[i];
      if (!Array.isArray(list)) {
        continue;
      }
      for (var j = 0; j < list.length; j++) {
        var entry = list[j];
        if (entry && typeof entry === 'object') {
          merged.push({ meta: meta, entry: entry });
        }
      }
    }
    merged.sort(function (a, b) {
      var diff = decidedAtMs(b.entry.decided_at) - decidedAtMs(a.entry.decided_at);
      if (diff !== 0) {
        return diff;
      }
      return String(b.entry.id || '').localeCompare(String(a.entry.id || ''));
    });
    return merged;
  }

  function rowHtml(item) {
    var meta = item.meta;
    var entry = item.entry;
    var itemCode = entry.item_code == null ? '' : String(entry.item_code);
    var siteCode = entry.site_code == null ? '' : String(entry.site_code);
    var href =
      '#/queue?kind=' +
      encodeURIComponent(meta.kind) +
      '&item=' +
      encodeURIComponent(itemCode) +
      '&site=' +
      encodeURIComponent(siteCode);
    var badge = statusBadge(entry);
    return (
      '<tr class="log-row">' +
        '<td class="log-list">' + esc(meta.list) + '</td>' +
        '<td class="log-item">' +
          '<a class="log-item-link" href="' + esc(href) + '">' +
            esc(itemCode) + ' \u00b7 ' + esc(siteCode) +
          '</a>' +
        '</td>' +
        '<td class="log-decision">' + esc(decisionText(entry)) + '</td>' +
        '<td class="log-money">' + moneyLabel(entry[meta.moneyField]) + '</td>' +
        '<td class="log-status">' +
          (badge
            ? '<span class="badge ' + badge.className + '">' + badge.text + '</span>'
            : esc(EM_DASH)) +
        '</td>' +
        '<td class="log-decided">' +
          '<span class="log-decided-by">' + esc(decidedByName(entry)) + '</span>' +
          '<span class="log-decided-at">' + esc(formatDecidedAt(entry.decided_at)) + '</span>' +
        '</td>' +
      '</tr>'
    );
  }

  // The cut line of the log card (issue #1600): the server lists at
  // most 1,000 decisions per list, so a full answer means the log
  // may hold more than what loaded. It names the full answers' lists
  // in the endpoint order and sits directly before the table's
  // scrolling region; with no full answer it does not render at all.
  function cutLineHtml() {
    return (
      '<p class="log-cut" data-testid="log-cut" role="status">' +
      'Showing the first 1,000 decisions of: ' +
      state.cutLists.join(', ') +
      '. There may be more.' +
      '</p>'
    );
  }

  function headRow() {
    return (
      '<tr>' +
        '<th scope="col">List</th>' +
        '<th scope="col">Item and site</th>' +
        '<th scope="col">Decision</th>' +
        '<th scope="col">Money</th>' +
        '<th scope="col">Status</th>' +
        '<th scope="col">Decided</th>' +
      '</tr>'
    );
  }

  function skeletonRows(count) {
    var rows = [];
    for (var i = 0; i < count; i++) {
      rows.push(
        '<tr class="log-skel-row">' +
          '<td colspan="6"><span class="log-skel"></span></td>' +
        '</tr>'
      );
    }
    return rows.join('');
  }

  /* ------------------------------- render ------------------------------ */

  function render() {
    if (!rootEl) {
      return;
    }
    if (state.loading) {
      rootEl.innerHTML =
        '<article class="card log-card" data-panel="log" aria-label="Decision log">' +
          '<div class="log-scroll">' +
            '<table class="log-table">' +
              '<thead>' + headRow() + '</thead>' +
              '<tbody>' + skeletonRows(6) + '</tbody>' +
            '</table>' +
          '</div>' +
        '</article>';
      return;
    }
    if (state.error) {
      rootEl.innerHTML =
        '<article class="card log-card" data-panel="log" aria-label="Decision log">' +
          '<div class="log-error" role="alert">' +
            '<p class="log-error-msg">' + esc(state.error) + '</p>' +
            '<button type="button" class="btn btn-secondary log-retry" id="log-retry">Retry</button>' +
          '</div>' +
        '</article>';
      return;
    }
    if (!state.rows.length) {
      rootEl.innerHTML =
        '<article class="card log-card" data-panel="log" aria-label="Decision log">' +
          '<div class="log-empty">' +
            '<h2 class="log-empty-title">No decisions recorded yet</h2>' +
            '<p class="log-empty-text">Decisions you accept, dismiss or snooze in the Work queue appear here, newest first.</p>' +
            '<a class="log-empty-link" href="#/queue">Open the Work queue</a>' +
          '</div>' +
        '</article>';
      return;
    }
    rootEl.innerHTML =
      '<article class="card log-card" data-panel="log" aria-label="Decision log">' +
        (state.cutLists.length ? cutLineHtml() : '') +
        '<div class="log-scroll">' +
          '<table class="log-table">' +
            '<thead>' + headRow() + '</thead>' +
            '<tbody>' + state.rows.map(rowHtml).join('') + '</tbody>' +
          '</table>' +
        '</div>' +
      '</article>';
  }

  /* -------------------------------- load ------------------------------- */

  function startLoad() {
    if (state.loading) {
      return;
    }
    state.loading = true;
    state.error = null;
    render();
    var seq = ++loadSeq;
    Promise.all(
      ENDPOINTS.map(function (meta) {
        return global.Api.get(meta.endpoint);
      })
    )
      .then(function (lists) {
        if (seq !== loadSeq) {
          return;
        }
        // issue #1600: a list is cut when its answer as received is
        // an array of exactly 1,000 entries — counted before
        // mergeDecisions drops anything, re-decided on every load.
        var cutLists = [];
        for (var i = 0; i < ENDPOINTS.length; i++) {
          if (Array.isArray(lists[i]) && lists[i].length === 1000) {
            cutLists.push(ENDPOINTS[i].list);
          }
        }
        state.cutLists = cutLists;
        state.rows = mergeDecisions(lists);
        state.loading = false;
        render();
      })
      .catch(function (error) {
        if (seq !== loadSeq) {
          return;
        }
        state.loading = false;
        state.error =
          error && error.message
            ? String(error.message)
            : 'The decision log could not be loaded. Please try again.';
        render();
      });
  }

  /* -------------------------------- boot ------------------------------- */

  function logActive() {
    var active = document.querySelector('.view.is-active');
    return !!active && active.getAttribute('data-view') === 'log';
  }

  function activate() {
    var appEl = document.getElementById('app');
    if (!appEl || appEl.hidden || !logActive()) {
      return;
    }
    startLoad();
  }

  function reset() {
    loadSeq += 1;
    state.loading = false;
    state.error = null;
    state.rows = [];
    state.cutLists = [];
    if (rootEl) {
      rootEl.innerHTML = '';
    }
  }

  (function boot() {
    rootEl = document.getElementById('log-root');
    if (!rootEl) {
      return;
    }
    rootEl.addEventListener('click', function (event) {
      var button = event.target && event.target.closest ? event.target.closest('button') : null;
      if (button && button.id === 'log-retry') {
        startLoad();
      }
    });

    var appEl = document.getElementById('app');
    if (!appEl) {
      return;
    }
    // The log loads once the signed-in shell is visible and the log
    // view is the active one; the hashchange handler (app.js) calls
    // activate() on every switch to #/log.
    var observer = new MutationObserver(function () {
      if (!appEl.hidden && logActive()) {
        activate();
      }
    });
    observer.observe(appEl, { attributes: true, attributeFilter: ['hidden'] });
    if (!appEl.hidden && logActive()) {
      activate();
    }
  })();

  global.Log = {
    activate: activate,
    reset: reset,
  };
})(window);
