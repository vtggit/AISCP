// AIERP planner screen — the shared view helpers (issue #1620).
//
// The one HTML escaper for the whole screen. Each of the fifteen
// views used to carry its own copy of this function; now every view
// binds its local esc to Shared.esc, so the escaping rules live in
// exactly one place:
//
//   Shared.esc turns any value into a string with String(value) and
//   replaces &, <, >, " and ' with &amp;, &lt;, &gt;, &quot; and
//   &#39; — the same five replacements every copy made, in the same
//   order (& first, so an already-escaped &lt; becomes &amp;lt;).
//
// index.html loads this script immediately after js/api.js and before
// every view script, so window.Shared.esc exists before any view
// binds it.
(function (global) {
  'use strict';

  global.Shared = global.Shared || {};

  global.Shared.esc = function (value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  };
})(window);
