// The one shared navigation list — AIERP issue #832.
//
// The shell renders its thirteen views as .nav .nav-btn buttons in
// app/index.html, and the proving specs pin that order and labelling.
// Before #832 each spec restated the whole list as a literal array; the
// list now lives here and the specs import it, so a re-ordered or
// re-labelled nav fails the shared source instead of ten copies of it.
//
// NAV_VIEWS: the view ids in the order the buttons render (their
// data-view attributes). NAV_LABELS: the button texts in the same
// order. Both must stay in sync with the nav markup in app/index.html
// and App.VIEWS in app/js/app.js; issue832.spec.js is the one spec that
// pins the rendered page to these lists.

const NAV_VIEWS = [
  'overview',
  'exceptions',
  'queue',
  'demand',
  'scenario',
  'aggregate',
  'sop',
  'across',
  'orders',
  'releases',
  'suppliers',
  'expedite',
  'writeback',
  'buffers',
  'log',
  'settings',
  'guidance',
];

const NAV_LABELS = [
  'Overview',
  'Exceptions',
  'Work queue',
  'Demand',
  'Scenario',
  'Aggregate',
  'S&OP',
  'Across ERPs',
  'Order plan',
  'Releases',
  'Suppliers',
  'Expedite',
  'Push to ERP',
  'Buffers',
  'Decision log',
  'Settings',
  'Guidance',
];

module.exports = { NAV_VIEWS, NAV_LABELS };
