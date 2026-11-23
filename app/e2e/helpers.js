// AIERP e2e helpers — the shared CommonJS module the issue proof specs
// in this directory reuse.
//
// The specs run on the real stack: the real backend behind the
// production proxy, a freshly migrated database, and the development
// sign-in mode that accepts one bearer token (E2E_TOKEN). Everything
// here drives the app the way a browser user would — the sign-in form,
// the #/<view> hash routes, and the page's own /api/ traffic. Nothing
// is mocked: no routes are intercepted and no app module is touched.

'use strict';

// How long the page must show no /api/ request in flight before it is
// settled, and how long settled() waits before failing.
var SETTLED_QUIET_MS = 500;
var SETTLED_TIMEOUT_MS = 20000;
// How often settled() looks at the in-flight set; far finer than the
// quiet window, so the window is honoured to the millisecond it is
// measured in.
var SETTLED_POLL_MS = 50;

function sleep(ms) {
  return new Promise(function (resolve) {
    setTimeout(resolve, ms);
  });
}

function isApiUrl(url) {
  var parsed;
  try {
    parsed = new URL(url);
  } catch (error) {
    return false;
  }
  return parsed.pathname.indexOf('/api/') === 0;
}

// The request path as it appears in the address bar, query included.
function apiPath(url) {
  var parsed = new URL(url);
  return parsed.pathname + parsed.search;
}

// Per-page tracker of the in-flight /api/ requests. Every helper that
// touches a page installs it exactly once — on that page's first use,
// before any of the page's traffic — and keeps it for the page's whole
// life. That is what lets settled() see requests that are ALREADY in
// flight when it is called: their 'request' event fired earlier and,
// having not completed yet, they are still in the set, holding the
// quiet window open until they finish.
var trackers = new WeakMap();

function ensureTracker(page) {
  var tracker = trackers.get(page);
  if (tracker) {
    return tracker;
  }
  tracker = { inFlight: new Set(), quietSince: null };
  trackers.set(page, tracker);

  page.on('request', function (request) {
    if (!isApiUrl(request.url())) {
      return;
    }
    tracker.inFlight.add(request);
    // Traffic resumed: the quiet window starts over.
    tracker.quietSince = null;
  });

  function release(request) {
    if (!tracker.inFlight.delete(request)) {
      return;
    }
    if (tracker.inFlight.size === 0) {
      tracker.quietSince = Date.now();
    }
  }
  // requestfinished fires when the response body is fully received;
  // requestfailed when the request never completed (network error,
  // abort). A request fires at most one of the two, so the in-flight
  // count cannot underflow.
  page.on('requestfinished', release);
  page.on('requestfailed', release);

  return tracker;
}

// Opens / and signs in through the development sign-in form with the
// gate's E2E_TOKEN: fills #token-input, then clicks #signin-button.
// Resolves once the signed-in shell, #app, is visible.
async function signIn(page) {
  // Track this page from before its first /api/ traffic, so every later
  // settled() sees the page's whole request history.
  ensureTracker(page);
  await page.goto('/');
  // fill() waits for the token field to be actionable, which covers
  // the sign-in card appearing only after the auth bootstrap ran.
  await page.fill('#token-input', process.env.E2E_TOKEN);
  await page.click('#signin-button');
  await page.waitForSelector('#app', { state: 'visible' });
}

// One entry per row of the app's own view table, window.App.VIEWS
// (app/js/app.js), read from the signed-in page and kept in its
// declared order: [{ view, title }, ...]. A screen added to the table
// later is covered by the suite without editing it.
async function screens(page) {
  ensureTracker(page);
  return page.evaluate(() =>
    Object.keys(App.VIEWS).map(function (view) {
      return { view: view, title: App.VIEWS[view].title };
    })
  );
}

// Resolves once none of the page's /api/ requests has been in flight
// for 500 ms, and fails after 20 s. It reads the page's persistent
// in-flight tracker (installed on the page's first helper use) instead
// of registering its own listeners, so a /api/ request that is already
// in flight when settled() is called counts: it stays in the set until
// it completes, and the quiet window only starts once the set is empty.
// Opening a screen changes only the URL hash, which is not a
// navigation, so Playwright's load states never cover it — the
// in-flight request set is the settle signal.
async function settled(page) {
  var tracker = ensureTracker(page);
  var deadline = Date.now() + SETTLED_TIMEOUT_MS;
  for (;;) {
    await sleep(SETTLED_POLL_MS);
    var now = Date.now();
    if (tracker.inFlight.size === 0) {
      if (tracker.quietSince === null) {
        tracker.quietSince = now;
      }
      if (now - tracker.quietSince >= SETTLED_QUIET_MS) {
        return;
      }
    } else {
      // Traffic is in flight again: the quiet window starts over.
      tracker.quietSince = null;
    }
    if (now >= deadline) {
      throw new Error(
        SETTLED_TIMEOUT_MS +
          ' ms passed with ' +
          tracker.inFlight.size +
          ' /api/ request(s) still in flight'
      );
    }
  }
}

// Returns a list. From the call onward the list gets one line for
// each of: a page error; a failed /api/ request; an /api/ response
// with a status of 500 or more. Each line names the request path and
// the status, where there is one (a failed request has no response,
// and a page error has no request).
function watch(page) {
  ensureTracker(page);
  var issues = [];

  page.on('pageerror', function (error) {
    var message = error && error.message ? error.message : String(error);
    issues.push('page error: ' + message);
  });

  page.on('requestfailed', function (request) {
    if (!isApiUrl(request.url())) {
      return;
    }
    var failure = request.failure();
    var detail = failure && failure.errorText ? failure.errorText : 'failed';
    issues.push(
      'api request ' +
        request.method() +
        ' ' +
        apiPath(request.url()) +
        ' failed (' +
        detail +
        ')'
    );
  });

  page.on('response', function (response) {
    if (!isApiUrl(response.url())) {
      return;
    }
    var status = response.status();
    if (status < 500) {
      return;
    }
    issues.push(
      'api response ' +
        response.request().method() +
        ' ' +
        apiPath(response.url()) +
        ' returned ' +
        status
    );
  });

  return issues;
}

module.exports = {
  signIn: signIn,
  screens: screens,
  settled: settled,
  watch: watch,
};
