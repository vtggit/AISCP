// Proving spec for AIERP issue #1470 — the two computed-style checks in
// the issue638 proof (the per-tab item/site cell and the detail pane's
// title/description order) retry until they hold instead of failing on
// the first read.
//
// The amendment, pinned on the source of app/tests/issue638.spec.js:
//   * the per-tab cell check — the one
//     expect(await paper.evaluate((rowEl) => { ... })).toEqual({ ... })
//     whose expected object is the q-item-cell flex column, its five
//     grid tracks and the secondary ink colour — is now
//     await expect(async () => { expect(await paper.evaluate((rowEl)
//     => { ... })).toEqual({ ... }); }).toPass(): the same callback,
//     the same expected object, re-read on every try;
//   * the detail pane check — the one
//     expect(await page.locator('.q-detail-title').evaluate((titleEl)
//     => { ... })).toBe('q-detail-desc') — is now the same retrying
//     form around the same callback and the same expected value;
//   * nothing else in the spec moved: the retrying wrap check
//     (expect.poll), the one longDesc.evaluate and the list-width
//     check stay where issue1461 pins them, and the file now carries
//     the two, and only the two, }).toPass(); closers.
//
// The test is TOP-LEVEL and its title is the full proof name
// ("issue1470 freeform"): the merge gate builds its junit nodeids from
// the Playwright test title, and a describe block would join the
// titles with a "›" the contract nodeid does not carry, so the proof
// would read as "not collected".
//
// The proof is source-only: it reads the sibling spec from disk with
// Node's fs and makes no page, so it attaches no terminal-state hook.

const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');

// The retrying form the amendment introduces: an awaited expect of an
// async function, closed by .toPass().
const RETRY_OPENER = 'await expect(async () => {';
const RETRY_CLOSER = '}).toPass();';

// The marker must appear exactly once in the text, and it must sit
// after an RETRY_OPENER with no RETRY_CLOSER between that opener and
// the call — i.e. inside its own retrying expect.
function retryingCall(text, marker) {
  expect(text.split(marker).length - 1, 'occurrences of ' + marker).toBe(1);
  const markerIndex = text.indexOf(marker);
  const openerIndex = text.lastIndexOf(RETRY_OPENER, markerIndex - 1);
  expect(openerIndex, 'no retrying expect before ' + marker).toBeGreaterThanOrEqual(0);
  expect(text.slice(openerIndex + RETRY_OPENER.length, markerIndex)).not.toContain(
    RETRY_CLOSER,
    marker + ' is not inside its own retrying expect',
  );
}

test('issue1470 freeform', () => {
  const text = fs.readFileSync(path.join(__dirname, 'issue638.spec.js'), 'utf8');

  // The two, and only the two, checks now retry: the file carries at
  // least two }).toPass(); closers ...
  expect(text.split(RETRY_CLOSER).length - 1).toBeGreaterThanOrEqual(2);

  // ... and each of the two calls appears exactly once, inside its own
  // retrying expect.
  retryingCall(text, 'paper.evaluate((rowEl) =>');
  retryingCall(text, '.evaluate((titleEl) =>');

  // The per-tab cell check's expected object is unchanged: the
  // q-item-cell flex column, its five grid tracks and the secondary
  // ink colour.
  expect(text).toContain("cellDisplay: 'flex',");
  expect(text).toContain("cellDirection: 'column',");
  expect(text).toContain('tracks: 5,');
  expect(text).toContain("descColor: 'rgb(51, 65, 78)',");

  // Nothing else moved: the retrying wrap check still reads the
  // description exactly once, from longDesc.
  expect(text.split('longDesc.evaluate').length - 1).toBe(1);
});
