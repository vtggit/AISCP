// AIERP planner screen — the application shell.
//
// Wires the shell together: hash-based navigation (#/overview, #/queue,
// #/demand, #/scenario, #/log, #/settings) that switches views without a
// page load and survives a reload,
// the sign-in form, and sign-out. The view shown by the URL hash is the
// single source of truth, so a reload keeps the current view.
(function (global) {
  'use strict';

  var VIEWS = {
    overview: {
      title: 'Overview',
      subtitle: 'What matters across the replenishment lists, at a glance.',
    },
    exceptions: {
      title: 'Exceptions',
      subtitle: 'What needs attention today, across every planning signal',
    },
    queue: {
      title: 'Work queue',
      subtitle: 'Triage the rows that still need a decision, one at a time.',
    },
    demand: {
      title: 'Demand',
      subtitle: "Each item-site's forecast, the method behind it and how well it backtests.",
    },
    scenario: {
      title: 'Scenario',
      subtitle: 'Re-run the reorder plan under the levers and see the impact before anything is stored.',
    },
    aggregate: {
      title: 'Aggregate planning',
      subtitle: 'Projected demand, supply and on-hand for every site, rolled forward month by month.',
    },
    sop: {
      title: 'S&OP review',
      subtitle: "One month's demand, supply and money on one page, and who agreed it",
    },
    across: {
      title: 'Across ERPs',
      subtitle: 'The same item in each ERP, side by side, and where one is short while the other holds too much.',
    },
    orders: {
      title: 'Order plan',
      subtitle: "What to order, from whom and by when, fitted to each supplier's terms",
    },
    releases: {
      title: 'Releases',
      subtitle: 'Purchase orders released to the ERP, and where each one stands',
    },
    suppliers: {
      title: 'Suppliers',
      subtitle: 'How reliably each supplier delivers by the promised date',
    },
    expedite: {
      title: 'Expedite',
      subtitle: 'Open purchase lines that are late or will arrive after the item runs out',
    },
    writeback: {
      title: 'Push to ERP',
      subtitle: 'Accepted reorder points the ERP does not have yet, and every write so far',
    },
    buffers: {
      title: 'Buffers',
      subtitle: 'Demand-driven buffers: where each one stands and what to order',
    },
    log: {
      title: 'Decision log',
      subtitle: 'Every decision taken, with its reason and its outcome.',
    },
    settings: {
      title: 'Settings',
      subtitle: 'System and personal settings, and where each value comes from.',
    },
    guidance: {
      title: 'Guidance',
      subtitle:
        'What AISCP is built to do, and the decisions that steer it. Every item has a key you can trace.',
    },
  };
  var DEFAULT_VIEW = 'overview';

  function viewFromHash() {
    // The hash may carry a query string (e.g. #/queue?kind=excess&item=...):
    // the view is the part before the "?".
    var base = (global.location.hash || '').split('?')[0];
    var match = /^#\/([a-z0-9]+)$/.exec(base);
    if (match && VIEWS[match[1]]) {
      return match[1];
    }
    return null;
  }

  function currentViewHash() {
    return '#/' + (viewFromHash() || DEFAULT_VIEW);
  }

  function setView(view) {
    var meta = VIEWS[view] || VIEWS[DEFAULT_VIEW];

    var sections = document.querySelectorAll('.view');
    for (var i = 0; i < sections.length; i++) {
      sections[i].classList.toggle(
        'is-active',
        sections[i].getAttribute('data-view') === view
      );
    }

    var buttons = document.querySelectorAll('.nav-btn');
    for (var j = 0; j < buttons.length; j++) {
      if (buttons[j].getAttribute('data-view') === view) {
        buttons[j].setAttribute('aria-current', 'page');
      } else {
        buttons[j].removeAttribute('aria-current');
      }
    }

    document.getElementById('page-title').textContent = meta.title;
    document.getElementById('page-subtitle').textContent = meta.subtitle;

    if (view === 'overview' && global.Overview) {
      global.Overview.activate();
    }

    if (view === 'queue' && global.Queue) {
      global.Queue.activate();
    }

    if (view === 'demand' && global.Demand) {
      global.Demand.activate();
    }

    if (view === 'scenario' && global.Scenario) {
      global.Scenario.activate();
    }

    if (view === 'aggregate' && global.Aggregate) {
      global.Aggregate.activate();
    }

    if (view === 'sop' && global.Sop) {
      global.Sop.activate();
    }

    if (view === 'across' && global.Across) {
      global.Across.activate();
    }

    if (view === 'orders' && global.Orders) {
      global.Orders.activate();
    }

    if (view === 'releases' && global.Releases) {
      global.Releases.activate();
    }

    if (view === 'suppliers' && global.Suppliers) {
      global.Suppliers.activate();
    }

    if (view === 'expedite' && global.Expedite) {
      global.Expedite.activate();
    }

    if (view === 'writeback' && global.Writeback) {
      global.Writeback.activate();
    }

    if (view === 'buffers' && global.Buffers) {
      global.Buffers.activate();
    }

    if (view === 'exceptions' && global.Exceptions) {
      global.Exceptions.activate();
    }

    if (view === 'log' && global.Log) {
      global.Log.activate();
    }

    if (view === 'settings' && global.Settings) {
      global.Settings.activate();
    }

    if (view === 'guidance' && global.Guidance) {
      global.Guidance.activate();
    }
  }

  function init() {
    var navButtons = document.querySelectorAll('.nav-btn');
    for (var i = 0; i < navButtons.length; i++) {
      (function (button) {
        button.addEventListener('click', function () {
          var view = button.getAttribute('data-view');
          if (viewFromHash() === view) {
            return;
          }
          global.location.hash = '#/' + view;
        });
      })(navButtons[i]);
    }

    global.window.addEventListener('hashchange', function () {
      setView(viewFromHash() || DEFAULT_VIEW);
    });

    document.getElementById('signout-button').addEventListener('click', function () {
      if (global.Overview) {
        global.Overview.reset();
      }
      if (global.Queue) {
        global.Queue.reset();
      }
      if (global.Demand) {
        global.Demand.reset();
      }
      if (global.Scenario) {
        global.Scenario.reset();
      }
      if (global.Aggregate) {
        global.Aggregate.reset();
      }
      if (global.Sop) {
        global.Sop.reset();
      }
      if (global.Across) {
        global.Across.reset();
      }
      if (global.Releases) {
        global.Releases.reset();
      }
      if (global.Suppliers) {
        global.Suppliers.reset();
      }
      if (global.Expedite) {
        global.Expedite.reset();
      }
      if (global.Writeback) {
        global.Writeback.reset();
      }
      if (global.Orders) {
        global.Orders.reset();
      }
      if (global.Buffers) {
        global.Buffers.reset();
      }
      if (global.Exceptions) {
        global.Exceptions.reset();
      }
      if (global.Log) {
        global.Log.reset();
      }
      if (global.Settings) {
        global.Settings.reset();
      }
      if (global.Guidance) {
        global.Guidance.reset();
      }
      global.Auth.signOut();
    });

    document.getElementById('signin-form').addEventListener('submit', function (event) {
      event.preventDefault();
      var input = document.getElementById('token-input');
      global.Auth.signIn(input ? input.value.trim() : '');
    });

    document.getElementById('retry-button').addEventListener('click', function () {
      global.Auth.retrySignIn();
    });

    // A 401 on any API call means signed out: back to the sign-in flow.
    global.Api.setUnauthorizedHandler(function (reason) {
      global.Auth.startSignIn(reason);
    });

    // Record the default view in the URL hash on a fresh load (a token
    // hash, #access_token=..., is left alone for Auth to consume).
    if (!global.location.hash) {
      global.history.replaceState(null, '', currentViewHash());
    }

    // Show the view named by the hash before the sign-in state resolves,
    // so the shell is already correct the moment it becomes visible.
    var initialView = viewFromHash() || DEFAULT_VIEW;
    setView(initialView);

    global.Auth.bootstrap();

    // Auth.bootstrap() runs synchronously up to its first await and
    // rewrites any non-route hash (a token fragment or anything else) to
    // #/overview. When it does that to a hash we have already resolved to
    // a view, the URL drifts from the view the shell is showing and the
    // next nav click can go nowhere: put the hash back so the URL and
    // the view stay one source of truth.
    if (initialView !== DEFAULT_VIEW && viewFromHash() !== initialView) {
      global.history.replaceState(null, '', '#/' + initialView);
    }
  }

  global.App = {
    init: init,
    currentViewHash: currentViewHash,
    VIEWS: VIEWS,
    DEFAULT_VIEW: DEFAULT_VIEW,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(window);
