// E2E proof for issue 1575.
//
// One test walks every screen of the app's own view table
// (window.App.VIEWS) through its #/<view> route, on the real stack
// against an empty, freshly migrated database. On each screen it waits
// until #page-title reads that screen's title and the screen's /api/
// traffic settles. It fails if a title never shows, a screen never
// settles, or the watch list recorded anything — a page error, a failed
// /api/ request, or an /api/ response with a status of 500 or more —
// and every failure names the screen.
//
// It also proves the two helper behaviours this issue's review
// flagged, with real traffic only:
//   * the title wait compares the rendered text against the table's
//     title as strings, so a title that is not a string in the view
//     table still matches (#page-title always holds DOM text);
//   * settled() honours a /api/ request that is already in flight when
//     it is called: a real request to the public /api/health probe is
//     held in flight on a real, emulated slow link (a transport
//     condition — nothing is intercepted and the real backend serves
//     the response), and settled() must not resolve until that request
//     completes and the 500 ms quiet window has passed.
//
// It asserts nothing about data, because the database holds no rows,
// and it mocks nothing.
const { test, expect } = require('@playwright/test');

const { signIn, screens, settled, watch } = require('./helpers');

test('issue1575 e2e', async ({ page, request }) => {
  // One test covers every screen: give it the room for all of them.
  test.setTimeout(5 * 60 * 1000);

  await signIn(page);

  // From now on, every page error, failed /api/ request and /api/
  // 5xx lands in issues.
  const issues = watch(page);

  const screenList = await screens(page);
  expect(screenList.length, 'the app view table must not be empty').toBeGreaterThan(0);

  for (const screen of screenList) {
    try {
      // Open the screen through its #/<view> route: the URL hash
      // changes, nothing navigates, so load states would never cover
      // the view's own /api/ traffic.
      await page.evaluate(
        (view) => {
          location.hash = '#/' + view;
        },
        screen.view
      );

      // The shell sets #page-title the moment the view activates.
      // Compare both sides as strings: #page-title holds DOM text,
      // which is always a string, while the title in the app's view
      // table is not guaranteed to be one.
      await page.waitForFunction(
        (title) => {
          const element = document.getElementById('page-title');
          return (
            element !== null &&
            String(element.textContent).trim() === String(title)
          );
        },
        screen.title,
        { timeout: 20 * 1000 }
      );

      // Proof of the string-tolerant title wait: the rendered title
      // reads exactly the screen's declared title, whatever its type.
      expect(
        String(await page.locator('#page-title').textContent()).trim(),
        'screen "' + screen.view + '" must render its declared title'
      ).toBe(String(screen.title));

      // Wait for the view's own /api/ traffic to go quiet.
      await settled(page);
    } catch (error) {
      throw new Error(
        'screen ' +
          JSON.stringify(screen.title) +
          ' (view "' +
          screen.view +
          '"): ' +
          error.message
      );
    }

    if (issues.length > 0) {
      throw new Error(
        'screen ' +
          JSON.stringify(screen.title) +
          ' (view "' +
          screen.view +
          '"): watch recorded ' +
          issues.length +
          ' problem(s):\n' +
          issues.join('\n')
      );
    }
  }

  // Proof that settled() honours a /api/ request already in flight:
  // put a real request in flight and keep it there with a real
  // transport condition — the browser's network conditions emulated
  // to a slow link, the way a slow connection would. No route is
  // intercepted and nothing is faked: the bytes really travel to the
  // real backend, which really serves the /api/health probe, just
  // slowly. A settled() that ignored in-flight requests would resolve
  // ~500 ms after being called — long before this request completes.
  const cdp = await page.context().newCDPSession(page);
  try {
    await cdp.send('Network.enable');
    await cdp.send('Network.emulateNetworkConditions', {
      offline: false,
      latency: 2000,
      downloadThroughput: -1,
      uploadThroughput: -1,
    });

    const startedAt = Date.now();
    const donePromise = page.evaluate(() =>
      fetch('/api/health', { cache: 'no-store' }).then((response) => ({
        status: response.status,
        at: Date.now(),
      }))
    );
    const settledPromise = settled(page).then(() => Date.now());

    // allSettled: report both outcomes instead of hanging on one.
    const [doneResult, settledResult] = await Promise.allSettled([
      donePromise,
      settledPromise,
    ]);
    if (doneResult.status !== 'fulfilled') {
      throw new Error(
        'in-flight proof: the real /api/health request did not complete: ' +
          (doneResult.reason && doneResult.reason.message)
      );
    }
    if (settledResult.status !== 'fulfilled') {
      throw new Error(
        'in-flight proof: settled() failed while the held request was in flight: ' +
          (settledResult.reason && settledResult.reason.message)
      );
    }

    const done = doneResult.value;
    const settledAt = settledResult.value;
    expect(
      done.status,
      'in-flight proof: the real backend must answer the held request'
    ).toBe(200);
    expect(
      done.at - startedAt,
      'in-flight proof: the held request must really have stayed in flight'
    ).toBeGreaterThanOrEqual(1500);
    expect(
      settledAt - done.at,
      'in-flight proof: settled() must not resolve before the in-flight ' +
        'request completes plus the 500 ms quiet window'
    ).toBeGreaterThanOrEqual(400);
  } finally {
    // Restore the normal network and let anything the slow window
    // delayed finish before the final check.
    await cdp
      .send('Network.emulateNetworkConditions', {
        offline: false,
        latency: 0,
        downloadThroughput: -1,
        uploadThroughput: -1,
      })
      .catch(() => {});
  }
  await settled(page);

  // A page error, a failed /api/ request or an /api/ 5xx anywhere in
  // the walk is a failure — named against the screen that was open.
  if (issues.length > 0) {
    throw new Error(
      'screen ' +
        JSON.stringify(screenList[screenList.length - 1].title) +
        ' (view "' +
        screenList[screenList.length - 1].view +
        '"): watch recorded ' +
        issues.length +
        ' problem(s):\n' +
        issues.join('\n')
    );
  }
});
