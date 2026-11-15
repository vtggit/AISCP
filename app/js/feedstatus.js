// AIERP planner screen — the feed freshness labels.
//
// The sidebar's "Synced ..." line (p.feed-status) and the header's
// "Data through ..." line (p#as-of) report the real state of the
// OData feed, fetched from GET /api/feed/status through the app's
// single HTTP layer (Api.get in js/api.js) — they no longer carry the
// fixed sync-time text the shell used to ship with (both start as
// "Checking sync…").
//
// The fetch runs once when the signed-in shell becomes visible and
// then every ten minutes while it stays signed in (a MutationObserver
// on the shell's hidden attribute is the sign-in signal, so every
// sign-in path — development token card or production PKCE — is
// covered). A request-sequence guard keeps an old, slow answer from
// painting over a newer one. Every value from the answer is inserted
// as text (textContent / setAttribute), never as markup.
//
// Label rules (all instants compared in UTC):
//   * fresh (synced_at within 36 hours) — "Synced <when> UTC" where
//     <when> is "today HH:MM" or "yesterday HH:MM" when its UTC date
//     is today's or yesterday's UTC date, else "YYYY-MM-DD HH:MM";
//   * stale (synced_at older than 36 hours) — the label gains
//     is-stale and reads "Not synced for <n> h" (<n> whole hours);
//   * never synced (synced_at null) — "Never synced", also stale;
//   * partial (oldest_synced_at more than 2 hours older than
//     synced_at) — the label gains " (partial)";
//   * a stale label's title names the newest and oldest table sync
//     times;
//   * a failed or malformed answer — "Sync status unavailable" in
//     both places, never a stale guess.
// The header shows "Data through <data_through>", or "No history
// yet" when data_through is null.
(function (global) {
  'use strict';

  var STATUS_PATH = '/api/feed/status';
  var REFRESH_MS = 10 * 60 * 1000; // ten minutes
  var STALE_MS = 36 * 60 * 60 * 1000; // 36 hours
  var PARTIAL_MS = 2 * 60 * 60 * 1000; // 2 hours
  var UNAVAILABLE = 'Sync status unavailable';

  var label = null; // sidebar <p class="feed-status">
  var asOf = null; // header <p class="as-of">
  var timer = null; // the ten-minute interval, while signed in
  var sequence = 0; // request-sequence guard

  /* ------------------------------ formatting ------------------------- */

  function pad2(value) {
    return (value < 10 ? '0' : '') + value;
  }

  // "YYYY-MM-DD HH:MM" in UTC.
  function utcStamp(date) {
    return (
      date.getUTCFullYear() +
      '-' + pad2(date.getUTCMonth() + 1) +
      '-' + pad2(date.getUTCDate()) +
      ' ' + pad2(date.getUTCHours()) +
      ':' + pad2(date.getUTCMinutes())
    );
  }

  function sameUtcDate(left, right) {
    return (
      left.getUTCFullYear() === right.getUTCFullYear() &&
      left.getUTCMonth() === right.getUTCMonth() &&
      left.getUTCDate() === right.getUTCDate()
    );
  }

  // "today HH:MM" / "yesterday HH:MM" / "YYYY-MM-DD HH:MM" for a UTC
  // instant, relative to the current UTC date.
  function syncWord(when, now) {
    var clock = pad2(when.getUTCHours()) + ':' + pad2(when.getUTCMinutes());
    if (sameUtcDate(when, now)) {
      return 'today ' + clock;
    }
    var yesterday = new Date(now.getTime());
    yesterday.setUTCDate(now.getUTCDate() - 1);
    if (sameUtcDate(when, yesterday)) {
      return 'yesterday ' + clock;
    }
    return utcStamp(when);
  }

  /* ------------------------------- parsing --------------------------- */

  // The parsed answer, or null when the body is not a usable feed
  // status (wrong shape, or an instant that is neither null nor a
  // parseable date): a malformed answer must read "unavailable",
  // never a guess. Usable means a plain object that carries a
  // synced_at key (null is fine: that is the explicit "never synced"
  // state); an array or an object without it is malformed, not
  // "Never synced".
  function parseStatus(payload) {
    if (
      !payload ||
      typeof payload !== 'object' ||
      Array.isArray(payload) ||
      !('synced_at' in payload)
    ) {
      return null;
    }
    function parseInstant(value) {
      if (value === null || value === undefined) {
        return { ok: true, date: null };
      }
      if (typeof value !== 'string') {
        return { ok: false, date: null };
      }
      var parsed = new Date(value);
      if (isNaN(parsed.getTime())) {
        return { ok: false, date: null };
      }
      return { ok: true, date: parsed };
    }

    var synced = parseInstant(payload.synced_at);
    if (!synced.ok) {
      return null;
    }
    var oldest = parseInstant(payload.oldest_synced_at);
    if (!oldest.ok) {
      return null;
    }
    return {
      syncedAt: synced.date,
      oldestSyncedAt: oldest.date,
      dataThrough:
        payload.data_through === null || payload.data_through === undefined
          ? null
          : String(payload.data_through),
    };
  }

  /* ------------------------------- rendering ------------------------- */

  function render(status) {
    var now = new Date();
    var stale = false;
    var title = null;
    var text;

    if (status.syncedAt === null) {
      stale = true;
      text = 'Never synced';
      title = 'Never synced';
    } else {
      var age = now.getTime() - status.syncedAt.getTime();
      if (age > STALE_MS) {
        stale = true;
        text = 'Not synced for ' + Math.floor(age / 3600000) + ' h';
        title = 'Newest: ' + utcStamp(status.syncedAt) + ' UTC';
        if (status.oldestSyncedAt !== null) {
          title += ' · Oldest: ' + utcStamp(status.oldestSyncedAt) + ' UTC';
        }
      } else {
        text = 'Synced ' + syncWord(status.syncedAt, now) + ' UTC';
      }
      if (
        status.oldestSyncedAt !== null &&
        status.syncedAt.getTime() - status.oldestSyncedAt.getTime() > PARTIAL_MS
      ) {
        text += ' (partial)';
      }
    }

    label.textContent = text;
    label.classList.toggle('is-stale', stale);
    if (title === null) {
      label.removeAttribute('title');
    } else {
      label.setAttribute('title', title);
    }
    asOf.textContent =
      status.dataThrough === null
        ? 'No history yet'
        : 'Data through ' + status.dataThrough;
  }

  function renderUnavailable() {
    label.textContent = UNAVAILABLE;
    label.classList.remove('is-stale');
    label.removeAttribute('title');
    asOf.textContent = UNAVAILABLE;
  }

  /* ------------------------------ scheduling ------------------------- */

  function fetchStatus() {
    sequence += 1;
    var mine = sequence;
    global.Api.get(STATUS_PATH).then(
      function (payload) {
        if (mine !== sequence) {
          return; // a newer request owns the labels now
        }
        var status = parseStatus(payload);
        if (status === null) {
          renderUnavailable();
          return;
        }
        render(status);
      },
      function () {
        if (mine !== sequence) {
          return;
        }
        renderUnavailable();
      }
    );
  }

  // One fetch now, then every ten minutes while the shell is up.
  function start() {
    stop();
    fetchStatus();
    timer = global.setInterval(fetchStatus, REFRESH_MS);
  }

  function stop() {
    if (timer !== null) {
      global.clearInterval(timer);
      timer = null;
    }
    sequence += 1; // any in-flight answer is now out of sequence
  }

  function watchShell() {
    var shell = document.getElementById('app');
    var observer = new global.MutationObserver(function () {
      if (shell.hidden) {
        stop();
      } else {
        start();
      }
    });
    observer.observe(shell, { attributes: true, attributeFilter: ['hidden'] });
    if (!shell.hidden) {
      start();
    }
  }

  function init() {
    label = document.querySelector('p.feed-status');
    asOf = document.getElementById('as-of');
    if (!label || !asOf || !global.Api) {
      return;
    }
    watchShell();
  }

  global.FeedStatus = {
    init: init,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(window);
