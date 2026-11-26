// AIERP planner screen — the Guidance view (#/guidance).
//
// #/guidance is the place in AIERP's screens where the guidance the
// product is built under can be read: GET /api/guidance through
// app/js/api.js, fetched only while the view is active — on view
// activation and on the Refresh button
// ([data-testid="guidance-refresh"]). A request-sequence guard renders
// only the newest answer, and a skeleton shows while a fetch is out.
//
// Above the items, the source line ([data-testid="guidance-source"])
// reads "Published <date> from <source> by <published_by>.", <date>
// the first ten characters of publication.published_at.
//
// The items with status active or proposed show in sections by type,
// in the fixed order direction "Direction", boundary "Boundaries",
// roadmap "Roadmap" and design "Design guidance" — a type without such
// items has no section. Inside a section the items are grouped by
// area, the groups in the order their first item has in the answer,
// each under its area_title, the items of a group in the answer's
// order. The items with status retired are listed apart, in a last
// section headed "Retired", in the answer's order and without area
// sub-headings; with no retired item there is no such section.
//
// One block per item ([data-testid="guidance-item"], the key in its
// data-key attribute): the title; the stamp
// ([data-testid="guidance-stamp"], the answer's stamp as text, such
// as SHOP-D-2@caddbcd58d); the statement; and one line
// ([data-testid="guidance-decided"]) reading "Decided by <decided_by>
// on <decided_on>", followed by " under <decided_under>" when that is
// not null, then " (<decided_ref>)" when that is not null, then a
// full stop, then " Changing it takes: <authority>." when authority is
// not null, then " In AIERP since <date>.", <date> the first ten
// characters of since. A proposed item carries a tag
// ([data-testid="guidance-status"]) reading "Proposed", a retired
// item one reading "Retired"; an active item has no tag. The word
// carries the meaning, the colour is only a second signal.
//
// The statement of each item, the statement of each row of an item's
// history and the statement in the looked-up block of a stamp render
// the statement's three forms: a code run (a backtick, one or more
// characters that are not a backtick, a backtick) shows as a code
// element, its content exactly as written, asterisks included; a bold
// run (two asterisks, one or more characters that do not include two
// asterisks in a row, two asterisks) shows as a strong element and may
// hold a code run; an italic run (one asterisk, then text that starts
// and ends with a character that is neither a space nor an asterisk,
// then one asterisk) shows as an em element. The markers themselves
// are not shown, and a marker that does not close a run is shown as it
// is: an unpaired backtick or asterisk, a lone asterisk between spaces
// as in "2 * 3 * 4". The statement's text is escaped before the forms
// are applied, so the only elements a statement can produce are
// strong, em and code, none with an attribute, and every other value
// is shown as text exactly as before. The find input still matches the
// statement as published, markers included.
//
// Every item block carries a "History" button
// ([data-testid="guidance-history"], aria-expanded false while
// closed). Pressing it sends GET /api/guidance/items/<key> once, the
// key URL-encoded, through app/js/api.js, marks the button
// aria-expanded true and opens the item's history — a list
// ([data-testid="guidance-versions"]) inside the block that reads
// "Loading the history..." while the answer is out. One row
// ([data-testid="guidance-version"]) per entry of the answer's
// versions, in the answer's order (the newest first): the version's
// stamp, its statement, the line "Decided by <decided_by> on
// <decided_on>", and its period — "In AIERP since <first date>
// (<first source>). Current." for a version with current true,
// "In AIERP from <first date> (<first source>) to <last date>
// (<last source>)." for any other, a date the first ten characters of
// that publication's published_at. A version whose status is proposed
// or retired also carries the word "Proposed" or "Retired". When the
// answer's current is false, a line
// ([data-testid="guidance-withdrawn"]) above the rows reads "This item
// is no longer published.". Pressing the button again closes the list
// without a request; opening it again sends the request again. A
// failed request reads "The history could not be loaded." in the
// list's place, and a close and open again sends the request again —
// except a 401, which shows no local error and leaves the sign-out to
// the shell. Only the newest answer renders: one arriving after the
// list was closed, after a newer request for the same item, after a
// Refresh or after the sign-out is discarded, and a Refresh and the
// sign-out close every open history. A re-render of the list (a find
// keystroke or a Refresh) never leaves an item marked expanded while
// its history is absent.
//
// The find input ([data-testid="guidance-find"]), labelled "Find a key
// or a word", narrows the screen as the user types: an item stays
// when its key, its stamp, its title or its statement contains the
// typed text, compared without regard to case and after trimming the
// typed text; a section or an area group with no item left is not
// shown. The count ([data-testid="guidance-count"]) reads "<M> items"
// while the input is empty and "<N> of <M> items" otherwise, M the
// number of items in the answer and N the number shown; when nothing
// matches the screen reads "No item matches." in place of the
// sections. When the hash carries a find value in its query string,
// as in #/guidance?find=SHOP-D-2, the screen opens with that text,
// URL-decoded, in the find input and already applied — the hash is
// read the way app/js/queue.js reads its own query string.
//
// When the trimmed find text is a stamp — an uppercase letter, one to
// seven more letters or digits, a dash, one of N, R, D or B, a dash,
// one to four digits, an @ and ten characters from 0 to 9 and a to f
// (^[A-Z][A-Z0-9]{1,7}-[NRDB]-[0-9]{1,4}@[0-9a-f]{10}$) — and no item
// of the current answer carries exactly that stamp, the screen sends
// GET /api/guidance/stamps/<stamp> once, the stamp URL-encoded,
// through app/js/api.js; a find that is not a stamp, and the stamp an
// item of the answer carries, send nothing, and the same stamp is not
// asked twice in a row while the find text stays the same. The answer
// shows in one block ([data-testid="guidance-lookup"]) above the item
// sections, in place of "No item matches.", headed "<stamp> is an
// earlier version of <key>." when the answer's current is false and
// "<stamp> is the current version of <key>." when it is true, showing
// the answer's title and statement. One line
// ([data-testid="guidance-lookup-state"]) reads "The item reads
// differently now." when superseded is true, "The item is no longer
// published." when current and superseded are both false, and is
// absent when current is true. Below, one row
// ([data-testid="guidance-lookup-version"]) per entry of versions, in
// the answer's order, each reading "In AIERP from <first date>
// (<first source>) to <last date> (<last source>).", a date the first
// ten characters of that publication's published_at. When superseded
// is true the block carries a button
// ([data-testid="guidance-lookup-current"]) reading "Show the item as
// it is now"; pressing it puts the answer's key into the find input
// and applies it, so the current item shows. While the answer is out
// the block reads "Looking up <stamp>...". A 404 shows "AIERP has
// never held the stamp <stamp>." in the block; any other failure
// shows "The stamp could not be looked up." with a "Try again" button
// ([data-testid="guidance-lookup-retry"]) that sends the request
// again. A find that is no longer the stamp removes the block, and an
// answer arriving after the find text changed, after a Refresh or
// after the sign-out is discarded.
//
// With publication null the screen reads "No guidance has been
// published to AIERP yet." and shows no source line, no find input
// and no count. A failed fetch shows "The guidance could not be
// loaded." with a "Try again" button that sends the request again,
// and the rest of the app works as before.
//
// The screen is read-only: it has no control that writes, and it
// sends no POST, PUT or DELETE. Every API value is set as text, never
// as markup, the colours come only from tokens.css, the answer lives
// in module state, sign-out clears the answer and the find text, and
// an answer arriving after sign-out is discarded.
(function (global) {
  'use strict';

  var VIEW_ID = 'view-guidance';
  var ROOT_ID = 'guidance-root';
  var APP_ID = 'app';
  var LIST_PATH = '/api/guidance';

  var GENERIC_ERROR = 'The guidance could not be loaded.';
  var NOT_PUBLISHED = 'No guidance has been published to AISCP yet.';
  var NO_MATCH = 'No item matches.';
  var FIND_LABEL = 'Find a key or a word';

  var HISTORY_BUTTON = 'History';
  var HISTORY_LOADING = 'Loading the history...';
  var HISTORY_ERROR = 'The history could not be loaded.';
  var WITHDRAWN_LINE = 'This item is no longer published.';

  // The stamp lookup: the find names a stamp the answer carries no
  // item with, and the block that shows what the stamp says now.
  var STAMP_PATTERN = /^[A-Z][A-Z0-9]{1,7}-[NRDB]-[0-9]{1,4}@[0-9a-f]{10}$/;
  var LOOKUP_FAILED = 'The stamp could not be looked up.';
  var LOOKUP_CURRENT_BUTTON = 'Show the item as it is now';
  var STATE_SUPERSEDED = 'The item reads differently now.';
  var STATE_WITHDRAWN = 'The item is no longer published.';

  // The type sections in the order they render, with their headings.
  // The retired items are not a type: they get the last section.
  var TYPE_SECTIONS = [
    { type: 'direction', heading: 'Direction' },
    { type: 'boundary', heading: 'Boundaries' },
    { type: 'roadmap', heading: 'Roadmap' },
    { type: 'design', heading: 'Design guidance' }
  ];
  var RETIRED_HEADING = 'Retired';

  var rootEl = null;

  var state = {
    data: null, // the newest successful answer
    loading: false, // a fetch is out
    error: null, // the newest failed load
    find: '', // the find input's text, as typed
    findFocused: false // the find input holds the focus
  };

  // The request-sequence guard: only the newest fetch's answer renders.
  var seq = 0;

  // The per-item history: the key maps to its own little state — the
  // list is open or closed, an answer is out or a failure is shown,
  // the newest answer held, and the request's sequence, so an answer
  // that is no longer the newest for its item is discarded. The state
  // is kept across the find keystrokes' re-renders, so an open list
  // survives them.
  var historyState = {};

  // Bumped on a Refresh and on the sign-out: every in-flight history
  // answer belongs to the past session then, and every open list
  // closes.
  var historyEpoch = 0;

  // The stamp lookup, one at a time: the stamp asked for, an answer
  // out or a failure shown, the newest answer held, and the request's
  // sequence — an answer that is no longer the newest (the find moved
  // on, a Refresh, the sign-out) is discarded.
  var lookup = {
    stamp: null,
    loading: false,
    error: null, // 'not-held' (a 404) or 'failed' (any other failure)
    payload: null,
    seq: 0
  };

  /* ------------------------------ helpers ------------------------------ */

  var esc = window.Shared.esc;

  // A value to set as text: null and undefined read as nothing, every
  // other value through String.
  function str(value) {
    return value === null || value === undefined ? '' : String(value);
  }

  // The first ten characters of a date-time string: the date part.
  function dateOf(value) {
    return typeof value === 'string' ? value.slice(0, 10) : '';
  }

  /* --------------------- the statement's forms ----------------------- */

  // The statement's characters, each { ch, code }: the code runs (a
  // backtick, one or more characters that are not a backtick, a
  // backtick) are found first and their content is kept exactly as
  // written, asterisks included; an unpaired backtick stays text.
  function statementChars(value) {
    var text = esc(str(value));
    var chars = [];
    var i = 0;
    while (i < text.length) {
      if (text.charAt(i) === '`') {
        var close = text.indexOf('`', i + 1);
        if (close > i + 1) {
          var c;
          for (c = i + 1; c < close; c++) {
            chars.push({ ch: text.charAt(c), code: true });
          }
          i = close + 1;
          continue;
        }
      }
      chars.push({ ch: text.charAt(i), code: false });
      i += 1;
    }
    return chars;
  }

  // Whether the character at the index is a space or an asterisk — the
  // edge a bold or italic run's text may not start or end with.
  function edgeChar(chars, index) {
    if (index < 0 || index >= chars.length) {
      return true;
    }
    var ch = chars[index].ch;
    return ch === ' ' || ch === '*';
  }

  // The earliest run at or after pos, { type, start, end } on the
  // character list, or null: a code run (found first, its content as
  // written); a bold run — two asterisks, one or more characters that
  // do not include two asterisks in a row, two asterisks, a code run
  // may sit inside; or an italic run — one asterisk, then text that
  // starts and ends with a character that is neither a space nor an
  // asterisk, then one asterisk. A marker that does not close a run is
  // no run: it stays text.
  function findRun(chars, pos) {
    var n = chars.length;
    var p;
    for (p = pos; p < n; p++) {
      if (chars[p].code && (p === 0 || !chars[p - 1].code)) {
        var end = p;
        while (end < n && chars[end].code) {
          end += 1;
        }
        return { type: 'code', start: p, end: end };
      }
      if (chars[p].code || chars[p].ch !== '*') {
        continue;
      }
      if (p + 1 < n && !chars[p + 1].code && chars[p + 1].ch === '*') {
        var close = -1;
        var c;
        for (c = p + 2; c + 1 < n; c++) {
          if (
            !chars[c].code &&
            chars[c].ch === '*' &&
            !chars[c + 1].code &&
            chars[c + 1].ch === '*'
          ) {
            close = c;
            break;
          }
        }
        if (close >= p + 3) {
          return { type: 'strong', start: p, end: close + 2 };
        }
        continue; // the ** never closes: the asterisks stay text
      }
      if (p > 0 && !chars[p - 1].code && chars[p - 1].ch === '*') {
        continue; // part of a **: not a single asterisk
      }
      if (p + 1 >= n || edgeChar(chars, p + 1)) {
        continue;
      }
      var italicClose = -1;
      var k;
      for (k = p + 2; k < n; k++) {
        if (!chars[k].code && chars[k].ch === '*' && !edgeChar(chars, k - 1)) {
          italicClose = k;
          break;
        }
      }
      if (italicClose !== -1) {
        return { type: 'em', start: p, end: italicClose + 1 };
      }
    }
    return null;
  }

  // The characters from from to to (or to the end) as plain text: they
  // are already escaped, and no code character reaches this path,
  // because a code run always becomes a run of its own.
  function charsText(chars, from, to) {
    var end = to === undefined ? chars.length : to;
    var out = '';
    var i;
    for (i = from; i < end; i++) {
      out += chars[i].ch;
    }
    return out;
  }

  // A slice of the statement with its code runs shown as code elements
  // and everything else as text: the italic run's content.
  function renderCodeOnly(chars) {
    var html = '';
    var pos = 0;
    while (pos < chars.length) {
      if (!chars[pos].code) {
        var end = pos;
        while (end < chars.length && !chars[end].code) {
          end += 1;
        }
        html += charsText(chars, pos, end);
        pos = end;
        continue;
      }
      var codeEnd = pos;
      while (codeEnd < chars.length && chars[codeEnd].code) {
        codeEnd += 1;
      }
      html += '<code>' + charsText(chars, pos, codeEnd) + '</code>';
      pos = codeEnd;
    }
    return html;
  }

  // A slice of the statement with all three forms applied: the
  // character list renders as text plus strong, em and code elements,
  // none with an attribute.
  function renderRuns(chars) {
    var html = '';
    var pos = 0;
    while (pos < chars.length) {
      var run = findRun(chars, pos);
      if (!run) {
        html += charsText(chars, pos);
        break;
      }
      if (run.start > pos) {
        html += charsText(chars, pos, run.start);
      }
      if (run.type === 'code') {
        html += '<code>' + charsText(chars, run.start, run.end) + '</code>';
      } else if (run.type === 'strong') {
        html +=
          '<strong>' +
          renderRuns(chars.slice(run.start + 2, run.end - 2)) +
          '</strong>';
      } else {
        html +=
          '<em>' +
          renderCodeOnly(chars.slice(run.start + 1, run.end - 1)) +
          '</em>';
      }
      pos = run.end;
    }
    return html;
  }

  // The statement's html: the escaped text with its forms applied. The
  // only elements it can produce are strong, em and code, none with an
  // attribute; a value without any marker reads as before.
  function statementHtml(value) {
    return renderRuns(statementChars(value));
  }

  function answerValid(payload) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      return false;
    }
    var publication = payload.publication;
    if (
      publication !== null &&
      (typeof publication !== 'object' || Array.isArray(publication))
    ) {
      return false;
    }
    return Array.isArray(payload.items);
  }

  // The items the screen can show: the answer's items that are
  // objects, in the answer's order.
  function itemsOf(payload) {
    var items = payload.items;
    var usable = [];
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      if (item && typeof item === 'object' && !Array.isArray(item)) {
        usable.push(item);
      }
    }
    return usable;
  }

  // The find text an item answers to: its key, stamp, title or
  // statement contains the typed text, without regard to case and
  // after trimming the typed text.
  function matches(item, needle) {
    if (needle === '') {
      return true;
    }
    var fields = [item.key, item.stamp, item.title, item.statement];
    for (var i = 0; i < fields.length; i++) {
      var value = fields[i];
      if (typeof value === 'string' && value.toLowerCase().indexOf(needle) !== -1) {
        return true;
      }
    }
    return false;
  }

  // The decided line of one item, word for word.
  function decidedLine(item) {
    var line =
      'Decided by ' + esc(str(item.decided_by)) + ' on ' + esc(str(item.decided_on));
    if (item.decided_under !== null && item.decided_under !== undefined) {
      line += ' under ' + esc(str(item.decided_under));
    }
    if (item.decided_ref !== null && item.decided_ref !== undefined) {
      line += ' (' + esc(str(item.decided_ref)) + ')';
    }
    line += '.';
    if (item.authority !== null && item.authority !== undefined) {
      line += ' Changing it takes: ' + esc(str(item.authority)) + '.';
    }
    line += ' In AISCP since ' + esc(dateOf(item.since)) + '.';
    return line;
  }

  // The history state of one item, created on first touch.
  function historyOf(key) {
    if (!Object.prototype.hasOwnProperty.call(historyState, key)) {
      historyState[key] = {
        open: false,
        loading: false,
        error: false,
        payload: null,
        seq: 0
      };
    }
    return historyState[key];
  }

  function historyValid(payload) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      return false;
    }
    if (!Array.isArray(payload.versions)) {
      return false;
    }
    for (var i = 0; i < payload.versions.length; i++) {
      var version = payload.versions[i];
      if (!version || typeof version !== 'object' || Array.isArray(version)) {
        return false;
      }
    }
    return true;
  }

  // The find text's stamp, if any: the trimmed find, when it is a
  // stamp at all.
  function stampOfFind() {
    var text = state.find.trim();
    return STAMP_PATTERN.test(text) ? text : null;
  }

  // Whether an item of the items carries exactly the stamp.
  function answerCarriesStamp(items, stamp) {
    for (var i = 0; i < items.length; i++) {
      if (items[i].stamp === stamp) {
        return true;
      }
    }
    return false;
  }

  // Forget the lookup: the block goes away, and an answer still out
  // the door belongs to the past.
  function clearLookup() {
    lookup.stamp = null;
    lookup.loading = false;
    lookup.error = null;
    lookup.payload = null;
    lookup.seq += 1;
  }

  // The one request for the stamp, the way the screen asks for it:
  // GET /api/guidance/stamps/<stamp> through app/js/api.js, the stamp
  // URL-encoded. Only the newest request's answer renders — the find
  // moving on, a Refresh or the sign-out (a newer sequence) discards
  // it.
  function sendLookupRequest(stamp) {
    lookup.stamp = stamp;
    lookup.loading = true;
    lookup.error = null;
    lookup.payload = null;
    lookup.seq += 1;
    var mySeq = lookup.seq;
    global.Api.get('/api/guidance/stamps/' + encodeURIComponent(stamp)).then(
      function (payload) {
        if (mySeq !== lookup.seq) {
          return; // the find moved, a Refresh, or the sign-out: stale
        }
        lookup.loading = false;
        if (!historyValid(payload)) {
          lookup.error = 'failed';
          lookup.payload = null;
        } else {
          lookup.payload = payload;
          lookup.error = null;
        }
        render();
      },
      function (error) {
        if (mySeq !== lookup.seq) {
          return;
        }
        lookup.loading = false;
        if (error && error.unauthorized) {
          // A 401: the shell owns the sign-out — app/js/api.js cleared
          // the token and ran the unauthorized handler. No local error.
          clearLookup();
        } else if (error && error.status === 404) {
          lookup.error = 'not-held';
          lookup.payload = null;
        } else {
          lookup.error = 'failed';
          lookup.payload = null;
        }
        render();
      }
    );
  }

  // The find's stamp, if any: a trimmed find that is a stamp no item
  // of the answer carries asks for its lookup once; every other find
  // text — and the stamp an item carries — asks nothing and forgets
  // the lookup. The same stamp is not asked twice in a row while the
  // find text stays the same.
  function maybeLookup() {
    var stamp = stampOfFind();
    if (
      stamp === null ||
      answerCarriesStamp(state.data ? itemsOf(state.data) : [], stamp)
    ) {
      clearLookup();
      return;
    }
    if (lookup.stamp === stamp) {
      return; // already asked for this stamp: no second request
    }
    sendLookupRequest(stamp);
  }

  // "Show the item as it is now": the answer's key into the find
  // input, applied — the current item of the answer shows, and the
  // key is no stamp, so the lookup forgets itself.
  function showCurrentItem() {
    var payload = lookup.payload;
    if (!payload) {
      return;
    }
    state.find = typeof payload.key === 'string' ? payload.key : '';
    maybeLookup();
  }

  // The period line of one version, word for word.
  function periodLine(version) {
    var first = version.first && typeof version.first === 'object' ? version.first : {};
    var last = version.last && typeof version.last === 'object' ? version.last : {};
    if (version.current === true) {
      return (
        'In AISCP since ' +
        esc(dateOf(first.published_at)) +
        ' (' +
        esc(str(first.source)) +
        '). Current.'
      );
    }
    return (
      'In AISCP from ' +
      esc(dateOf(first.published_at)) +
      ' (' +
      esc(str(first.source)) +
      ') to ' +
      esc(dateOf(last.published_at)) +
      ' (' +
      esc(str(last.source)) +
      ').'
    );
  }

  /* ------------------------------ building ----------------------------- */

  // One row of the item's history, one per version of the answer.
  function versionRowHtml(version) {
    var tag = '';
    if (version.status === 'proposed') {
      tag =
        '<span class="g-status g-status-proposed" data-testid="guidance-version-status">Proposed</span>';
    } else if (version.status === 'retired') {
      tag =
        '<span class="g-status g-status-retired" data-testid="guidance-version-status">Retired</span>';
    }
    return (
      '<div class="g-version" data-testid="guidance-version">' +
      '<div class="g-version-head">' +
      '<span class="g-version-stamp">' + esc(str(version.stamp)) + '</span>' +
      tag +
      '</div>' +
      '<p class="g-version-statement">' + statementHtml(version.statement) + '</p>' +
      '<p class="g-version-decided">Decided by ' +
      esc(str(version.decided_by)) +
      ' on ' +
      esc(str(version.decided_on)) +
      '</p>' +
      '<p class="g-version-period">' + periodLine(version) + '</p>' +
      '</div>'
    );
  }

  // The history of one item, below its lines: the "History" button and
  // the list it opens, hidden while the history is closed.
  function historyHtml(item) {
    var history = historyOf(item.key);
    var body = '';
    if (history.open) {
      if (history.loading) {
        body = '<p class="g-history-note">' + esc(HISTORY_LOADING) + '</p>';
      } else if (history.error) {
        body =
          '<p class="g-history-error" role="alert">' + esc(HISTORY_ERROR) + '</p>';
      } else {
        var payload = history.payload;
        if (payload && payload.current === false) {
          body +=
            '<p class="g-history-withdrawn" data-testid="guidance-withdrawn">' +
            esc(WITHDRAWN_LINE) +
            '</p>';
        }
        var versions = payload && Array.isArray(payload.versions) ? payload.versions : [];
        for (var i = 0; i < versions.length; i++) {
          var version = versions[i];
          if (version && typeof version === 'object' && !Array.isArray(version)) {
            body += versionRowHtml(version);
          }
        }
      }
    }
    return (
      '<div class="g-history">' +
      '<button type="button" class="btn btn-secondary g-history-btn" ' +
      'data-testid="guidance-history" aria-expanded="' +
      (history.open ? 'true' : 'false') +
      '">' +
      esc(HISTORY_BUTTON) +
      '</button>' +
      '<div class="g-history-body" data-testid="guidance-versions"' +
      (history.open ? '' : ' hidden') +
      '>' +
      body +
      '</div>' +
      '</div>'
    );
  }

  function itemHtml(item) {
    var tag = '';
    if (item.status === 'proposed') {
      tag =
        '<span class="g-status g-status-proposed" data-testid="guidance-status">Proposed</span>';
    } else if (item.status === 'retired') {
      tag =
        '<span class="g-status g-status-retired" data-testid="guidance-status">Retired</span>';
    }
    return (
      '<article class="g-item" data-testid="guidance-item" data-key="' +
      esc(item.key) +
      '">' +
      '<header class="g-item-head">' +
      '<h4 class="g-item-title">' + esc(str(item.title)) + '</h4>' +
      tag +
      '<span class="g-stamp" data-testid="guidance-stamp">' +
      esc(str(item.stamp)) +
      '</span>' +
      '</header>' +
      '<p class="g-item-statement">' + statementHtml(item.statement) + '</p>' +
      '<p class="g-item-decided" data-testid="guidance-decided">' +
      decidedLine(item) +
      '</p>' +
      historyHtml(item) +
      '</article>'
    );
  }

  // The area groups of one type section: the groups in the order their
  // first item has in the answer, each under its area_title, the
  // items of a group in the answer's order.
  function areaGroupsHtml(typeItems) {
    var groups = [];
    var byArea = {};
    for (var i = 0; i < typeItems.length; i++) {
      var item = typeItems[i];
      var areaId = typeof item.area === 'string' ? item.area : '\u0000' + str(item.area);
      if (!Object.prototype.hasOwnProperty.call(byArea, areaId)) {
        byArea[areaId] = { areaTitle: item.area_title, items: [] };
        groups.push(byArea[areaId]);
      }
      byArea[areaId].items.push(item);
    }
    var html = '';
    for (var g = 0; g < groups.length; g++) {
      var group = groups[g];
      var itemsHtml = '';
      for (var j = 0; j < group.items.length; j++) {
        itemsHtml += itemHtml(group.items[j]);
      }
      html +=
        '<div class="g-area">' +
        '<h3 class="g-area-title">' + esc(str(group.areaTitle)) + '</h3>' +
        '<div class="g-items">' + itemsHtml + '</div>' +
        '</div>';
    }
    return html;
  }

  // The sections of the content: the active and proposed items by
  // type (a type without such items has no section), then the retired
  // items apart, in the answer's order and without area sub-headings.
  // The find text narrows: a section or an area group with no item
  // left is not shown.
  function sectionsHtml(items) {
    var needle = state.find.trim().toLowerCase();
    var html = '';
    var visibleCount = 0;

    for (var s = 0; s < TYPE_SECTIONS.length; s++) {
      var meta = TYPE_SECTIONS[s];
      var typeItems = [];
      for (var i = 0; i < items.length; i++) {
        var item = items[i];
        if (
          item.type === meta.type &&
          (item.status === 'active' || item.status === 'proposed') &&
          matches(item, needle)
        ) {
          typeItems.push(item);
        }
      }
      if (typeItems.length === 0) {
        continue; // a type without such items has no section
      }
      html +=
        '<section class="g-section">' +
        '<h2 class="g-section-title">' + esc(meta.heading) + '</h2>' +
        areaGroupsHtml(typeItems) +
        '</section>';
      visibleCount += typeItems.length;
    }

    var retired = [];
    for (var r = 0; r < items.length; r++) {
      var retiredItem = items[r];
      if (retiredItem.status === 'retired' && matches(retiredItem, needle)) {
        retired.push(retiredItem);
      }
    }
    if (retired.length > 0) {
      var retiredHtml = '';
      for (var k = 0; k < retired.length; k++) {
        retiredHtml += itemHtml(retired[k]);
      }
      html +=
        '<section class="g-section g-section-retired">' +
        '<h2 class="g-section-title">' + esc(RETIRED_HEADING) + '</h2>' +
        '<div class="g-items">' + retiredHtml + '</div>' +
        '</section>';
      visibleCount += retired.length;
    }

    return { html: html, visibleCount: visibleCount };
  }

  function barHtml(total, visible, showControls) {
    var findHtml = '';
    if (showControls) {
      var text =
        state.find.trim() === ''
          ? total + ' items'
          : visible + ' of ' + total + ' items';
      findHtml =
        '<div class="g-find">' +
        '<label class="g-find-label" for="guidance-find-input">' +
        esc(FIND_LABEL) +
        '</label>' +
        '<input id="guidance-find-input" type="text" class="g-find-input" ' +
        'data-testid="guidance-find" value="' +
        esc(state.find) +
        '" placeholder="' +
        esc(FIND_LABEL) +
        '">' +
        '</div>' +
        '<span class="g-count" data-testid="guidance-count">' + esc(text) + '</span>';
    }
    return (
      '<div class="g-bar">' +
      findHtml +
      '<button type="button" class="btn btn-secondary g-refresh" data-testid="guidance-refresh">Refresh</button>' +
      '</div>'
    );
  }

  function skeletonHtml() {
    return (
      '<div class="g-skel" aria-hidden="true">' +
      '<div class="skel skel-line"></div>' +
      '<div class="skel skel-line skel-wide"></div>' +
      '<div class="skel g-skel-block"></div>' +
      '</div>'
    );
  }

  function errorHtml() {
    return (
      '<div class="g-error" role="alert">' +
      '<p class="g-error-msg" data-testid="guidance-error">' + esc(GENERIC_ERROR) + '</p>' +
      '<button type="button" class="btn btn-primary g-retry" data-testid="guidance-retry">Try again</button>' +
      '</div>'
    );
  }

  function notPublishedHtml() {
    return (
      '<p class="g-note" data-testid="guidance-nopub">' + esc(NOT_PUBLISHED) + '</p>'
    );
  }

  function noMatchHtml() {
    return '<p class="g-note" data-testid="guidance-nomatch">' + esc(NO_MATCH) + '</p>';
  }

  // One row of the lookup: "In AIERP from <first date> (<first
  // source>) to <last date> (<last source>).", a date the first ten
  // characters of that publication's published_at.
  function lookupVersionLine(version) {
    var first = version.first && typeof version.first === 'object' ? version.first : {};
    var last = version.last && typeof version.last === 'object' ? version.last : {};
    return (
      'In AISCP from ' +
      esc(dateOf(first.published_at)) +
      ' (' +
      esc(str(first.source)) +
      ') to ' +
      esc(dateOf(last.published_at)) +
      ' (' +
      esc(str(last.source)) +
      ').'
    );
  }

  // The stamp lookup's block, above the item sections while the find
  // names a stamp no item carries — in place of "No item matches."
  // when nothing matches: "Looking up <stamp>..." while the answer is
  // out, the 404's note, the failure with its "Try again", or the
  // answer — the heading, the title and the statement, the state line
  // (when there is one), one row per version, and the "Show the item
  // as it is now" button when superseded.
  function lookupBlockHtml() {
    if (!lookup.stamp) {
      return '';
    }
    if (lookup.loading) {
      return (
        '<section class="g-lookup" data-testid="guidance-lookup">' +
        '<p class="g-lookup-loading">Looking up ' +
        esc(lookup.stamp) +
        '...</p>' +
        '</section>'
      );
    }
    if (lookup.error === 'not-held') {
      return (
        '<section class="g-lookup" data-testid="guidance-lookup">' +
        '<p class="g-lookup-note">AISCP has never held the stamp ' +
        esc(lookup.stamp) +
        '.</p>' +
        '</section>'
      );
    }
    if (lookup.error === 'failed') {
      return (
        '<section class="g-lookup" data-testid="guidance-lookup">' +
        '<p class="g-lookup-error" role="alert">' + esc(LOOKUP_FAILED) + '</p>' +
        '<button type="button" class="btn btn-primary g-lookup-btn" ' +
        'data-testid="guidance-lookup-retry">Try again</button>' +
        '</section>'
      );
    }
    var payload = lookup.payload;
    if (!payload) {
      return '';
    }
    var heading =
      payload.current === true
        ? lookup.stamp + ' is the current version of ' + str(payload.key) + '.'
        : lookup.stamp + ' is an earlier version of ' + str(payload.key) + '.';
    var html =
      '<section class="g-lookup" data-testid="guidance-lookup">' +
      '<h3 class="g-lookup-head">' + esc(heading) + '</h3>' +
      '<h4 class="g-lookup-title">' + esc(str(payload.title)) + '</h4>' +
      '<p class="g-lookup-statement">' + statementHtml(payload.statement) + '</p>';
    if (payload.superseded === true) {
      html +=
        '<p class="g-lookup-state" data-testid="guidance-lookup-state">' +
        esc(STATE_SUPERSEDED) +
        '</p>';
    } else if (payload.current !== true) {
      html +=
        '<p class="g-lookup-state" data-testid="guidance-lookup-state">' +
        esc(STATE_WITHDRAWN) +
        '</p>';
    }
    var versions = Array.isArray(payload.versions) ? payload.versions : [];
    for (var i = 0; i < versions.length; i++) {
      var version = versions[i];
      if (version && typeof version === 'object' && !Array.isArray(version)) {
        html +=
          '<p class="g-lookup-version" data-testid="guidance-lookup-version">' +
          lookupVersionLine(version) +
          '</p>';
      }
    }
    if (payload.superseded === true) {
      html +=
        '<button type="button" class="btn btn-primary g-lookup-btn" ' +
        'data-testid="guidance-lookup-current">' +
        esc(LOOKUP_CURRENT_BUTTON) +
        '</button>';
    }
    return html + '</section>';
  }

  // The content: the source line above the items, the stamp lookup's
  // block above the sections while the find names a stamp no item
  // carries, and — with the find text narrowing away every item —
  // "No item matches." in place of the sections, the block standing
  // in for the note when it is out.
  function contentHtml(payload, sections) {
    var publication = payload.publication;
    var source =
      '<p class="g-source" data-testid="guidance-source">Published ' +
      esc(dateOf(publication.published_at)) +
      ' from ' +
      esc(str(publication.source)) +
      ' by ' +
      esc(str(publication.published_by)) +
      '.</p>';
    if (sections.visibleCount === 0 && state.find.trim() !== '') {
      // Nothing matches: the block stands in for the note.
      var lookupHtml = lookupBlockHtml();
      if (lookupHtml !== '') {
        return source + lookupHtml;
      }
      return source + noMatchHtml();
    }
    // Sections visible: the block, when out, renders above them.
    return (
      source +
      lookupBlockHtml() +
      '<div class="g-sections">' +
      sections.html +
      '</div>'
    );
  }

  function shellHtml() {
    var bar;
    var body;
    if (state.error) {
      // A failed load, on the first read or a re-read: the error box.
      bar = barHtml(0, 0, false);
      body = errorHtml();
    } else if (!state.data || state.loading) {
      // The first read is out, or a re-read of an answer already held:
      // the bar without the find controls and the skeleton.
      bar = barHtml(0, 0, false);
      body = skeletonHtml();
    } else if (!state.data.publication) {
      // Nothing published: the note, and no source line, find input or
      // count.
      bar = barHtml(0, 0, false);
      body = notPublishedHtml();
    } else {
      var items = itemsOf(state.data);
      var sections = sectionsHtml(items);
      bar = barHtml(items.length, sections.visibleCount, true);
      body = contentHtml(state.data, sections);
    }
    return '<div class="g">' + bar + '<div class="g-body">' + body + '</div></div>';
  }

  function render() {
    if (!rootEl) {
      return;
    }
    rootEl.innerHTML = shellHtml();
    // A keystroke re-rendered the screen and rebuilt the find input:
    // put the focus and the caret back where the user is typing.
    if (state.findFocused) {
      var input = rootEl.querySelector('#guidance-find-input');
      if (input) {
        input.focus();
        var length = input.value.length;
        input.setSelectionRange(length, length);
      }
    }
  }

  /* ------------------------------ loading ------------------------------ */

  function load() {
    seq += 1;
    var mySeq = seq;
    // A re-read (a Refresh, or the view's activation) closes every
    // open history and makes its in-flight answers stale.
    historyEpoch += 1;
    historyState = {};
    // The stamp lookup's in-flight answer is stale too, and is
    // forgotten — a held answer (a payload, a 404, a failure) stays,
    // so the same stamp is not asked twice in a row while the find
    // text stays the same.
    if (lookup.loading) {
      clearLookup();
    }
    state.loading = true;
    state.error = null;
    render();
    global.Api.get(LIST_PATH).then(
      function (payload) {
        if (mySeq !== seq) {
          return; // a newer fetch is out, or signed out: this answer is stale
        }
        state.loading = false;
        if (!answerValid(payload)) {
          state.data = null;
          state.error = GENERIC_ERROR;
        } else {
          state.data = payload;
          // The find may name a stamp the answer carries no item with.
          maybeLookup();
        }
        render();
      },
      function () {
        if (mySeq !== seq) {
          return;
        }
        state.loading = false;
        state.error = GENERIC_ERROR;
        render();
      }
    );
  }

  /* ------------------------------ history ----------------------------- */

  // The "History" button of one item: closed, open the list and send
  // the request; open, close it again without a request.
  function toggleHistory(key) {
    var history = historyOf(key);
    if (history.open) {
      closeHistory(key);
      return;
    }
    history.open = true;
    history.loading = true;
    history.error = false;
    history.payload = null;
    history.seq += 1;
    var mySeq = history.seq;
    var myEpoch = historyEpoch;
    render();
    global.Api.get('/api/guidance/items/' + encodeURIComponent(key)).then(
      function (payload) {
        // A Refresh or the sign-out (a newer epoch), or a close or a
        // newer request for this item (a newer sequence), made this
        // answer stale: it is discarded.
        if (myEpoch !== historyEpoch || mySeq !== history.seq) {
          return;
        }
        history.loading = false;
        if (!historyValid(payload)) {
          history.error = true;
          history.payload = null;
        } else {
          history.payload = payload;
        }
        render();
      },
      function (error) {
        if (myEpoch !== historyEpoch || mySeq !== history.seq) {
          return;
        }
        history.loading = false;
        if (error && error.unauthorized) {
          // A 401: the shell owns the sign-out — app/js/api.js cleared
          // the token and ran the unauthorized handler. No local error,
          // and the list closes quietly behind the sign-in screen.
          history.open = false;
          history.payload = null;
        } else {
          history.error = true;
          history.payload = null;
        }
        render();
      }
    );
  }

  function closeHistory(key) {
    var history = historyOf(key);
    history.open = false;
    history.loading = false;
    history.error = false;
    history.payload = null;
    history.seq += 1; // an answer still out the door is stale now
    render();
  }

  /* ------------------------------ events ------------------------------- */

  function onRootClick(event) {
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }
    var button = target.closest('button');
    if (!button || !rootEl.contains(button)) {
      return;
    }
    var testId = button.getAttribute('data-testid');
    if (testId === 'guidance-refresh' || testId === 'guidance-retry') {
      load();
    } else if (testId === 'guidance-lookup-retry') {
      // The failure's "Try again": the same stamp, asked again.
      if (lookup.stamp) {
        sendLookupRequest(lookup.stamp);
        render();
      }
    } else if (testId === 'guidance-lookup-current') {
      showCurrentItem();
      render();
    } else if (testId === 'guidance-history') {
      var item = button.closest('[data-testid="guidance-item"]');
      var key = item ? item.getAttribute('data-key') : null;
      if (typeof key === 'string' && key !== '') {
        toggleHistory(key);
      }
    }
  }

  function onRootInput(event) {
    var target = event.target;
    if (!target || target.getAttribute('data-testid') !== 'guidance-find') {
      return;
    }
    state.find = typeof target.value === 'string' ? target.value : '';
    state.findFocused = true;
    maybeLookup(); // the find may now name a stamp, or no longer
    render();
  }

  function onRootBlur(event) {
    var target = event.target;
    if (!target || target.getAttribute('data-testid') !== 'guidance-find') {
      return;
    }
    state.findFocused = false;
  }

  /* ------------------------------ activation --------------------------- */

  // The find the URL hash carries, if any: ?find=... after the view's
  // own hash part, read the way app/js/queue.js reads its own query
  // string — URLSearchParams URL-decodes the value.
  function findFromHash() {
    var hash = global.location.hash || '';
    var queryIndex = hash.indexOf('?');
    if (queryIndex === -1) {
      return null;
    }
    var params = new URLSearchParams(hash.slice(queryIndex + 1));
    return params.get('find');
  }

  function viewActive() {
    var view = document.getElementById(VIEW_ID);
    return !!view && view.classList.contains('is-active');
  }

  function activate() {
    if (!rootEl) {
      return;
    }
    // A link into the view: the hash's find value, URL-decoded, opens
    // in the find input and already applied. A hash without one keeps
    // the find as it is (sign-out clears it, the way the queue keeps
    // its filter across views).
    var linked = findFromHash();
    if (linked !== null) {
      state.find = linked;
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
    historyEpoch += 1; // and so does every in-flight history answer
    historyState = {};
    clearLookup(); // and so does the stamp lookup's in-flight answer
    state.data = null;
    state.loading = false;
    state.error = null;
    state.find = ''; // sign-out clears the find text
    state.findFocused = false;
    render();
  }

  function onAppVisibility() {
    var appEl = document.getElementById(APP_ID);
    if (!appEl || appEl.hidden) {
      return;
    }
    if (viewActive()) {
      activate();
    }
  }

  (function boot() {
    rootEl = document.getElementById(ROOT_ID);
    if (!rootEl) {
      return;
    }
    rootEl.addEventListener('click', onRootClick);
    rootEl.addEventListener('input', onRootInput);
    rootEl.addEventListener('blur', onRootBlur, true);
    render();

    var appEl = document.getElementById(APP_ID);
    if (!appEl) {
      return;
    }
    // Sign-in shows #app: when it does and Guidance is the active
    // view, the guidance loads then — the pre-sign-in activation (the
    // shell shows the hash's view before Auth.bootstrap has completed)
    // could not fetch.
    var observer = new MutationObserver(function () {
      onAppVisibility();
    });
    observer.observe(appEl, { attributes: true, attributeFilter: ['hidden'] });
    if (!appEl.hidden) {
      onAppVisibility();
    }
  })();

  global.Guidance = {
    activate: activate,
    reset: reset
  };
})(window);
