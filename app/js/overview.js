// AIERP planner screen — the Overview view, the manager's page
// (docs/design/planner-screen.md, "Overview (the manager's page)").
//
// #/overview loads, every call through app/js/api.js:
//   * GET /api/decisions/summary, and
//   * the three decision lists with decided rows included:
//     GET /api/drift?limit=1000&include_decided=true,
//     GET /api/excess?limit=1000&include_decided=true,
//     GET /api/stockout?limit=1000&include_decided=true, and
//   * GET /api/drift/decisions/acceptance, the Reorder points
//     card's "acceptance" block, the card's own read beside its
//     summary and list reads; a failed read — any status, a network
//     error, or a body that is not an object whose accepted and
//     dismissed hold the finite number count and whose rate is a
//     finite number or null — leaves the block out, the card renders
//     exactly as before;
//   * GET /api/stockout/proposals, the Stockout risk card's
//     "proposed remedy" block, the card's own read beside its
//     summary and list reads; a failed read — any status, a network
//     error, or a body that is not an object whose total holds the
//     finite numbers proposed and chosen and whose without_proposal
//     is a finite number — leaves the block out, the card renders
//     exactly as before;
//   * GET /api/excess/proposals, the Excess and dead stock card's
//     "proposed action" block, the card's own read beside its
//     summary and list reads; a failed read — any status, a network
//     error, or a body that is not an object whose total holds the
//     finite numbers proposed and chosen and whose without_proposal
//     is a finite number — leaves the block out, the card renders
//     exactly as before;
//   * GET /api/aggregate/releases, the Released orders card's answer.
//   * GET /api/abc, the "Value by class" card's answer.
//   * GET /api/exceptions, the "Exceptions today" card's answer.
//   * GET /api/exceptions/proposals, the "Exceptions today" card's
//     measure line, the card's own read beside its list read; a
//     failed read — any status, a network error, or a body that is
//     not an object whose acknowledged and proposed hold the finite
//     numbers with proposed no greater than acknowledged — leaves
//     the line out, the card renders exactly as before;
//   * GET /api/supply-plan/summary, the "Supply this week" card's answer.
//   * GET /api/forecast/overrides/proposals, the "Forecast
//     overrides" card's answer; a failed read — any status, a
//     network error, or a body that is not an object whose
//     overrides, proposed, taken, same_direction and
//     without_proposal hold the finite numbers with taken and
//     same_direction each no greater than proposed and proposed no
//     greater than overrides — removes the card — no error text,
//     no Retry, no skeleton survives.
//
// The figures load the first time the view becomes the active view. A
// decision recorded in the Work queue (any of the three kinds) marks
// them stale (Queue posts, then Overview.invalidate()), so the next
// time the Overview is the active view every region re-requests the
// summary and the lists and shows the new figures — no page reload.
//
// The view is split into load regions so a rejected call or a non-2xx
// response fails only the regions that needed the endpoint:
//   * the value tiles region — the summary's `total` buckets (accepted
//     money + count, applied money with the progress bar, outstanding
//     money + count);
//   * one region per list card — that kind's summary buckets (accepted
//     money, applied money, dismissed count) plus the "N to decide"
//     count of that list; the Reorder points card carries its
//     "acceptance" block (GET /api/drift/decisions/acceptance), the
//     Stockout risk card its "proposed remedy" block (GET
//     /api/stockout/proposals) and the Excess and dead stock card
//     its "proposed action" block (GET /api/excess/proposals), each
//     directly after its figures row and
//     directly before its Open button; a failed read leaves the block
//     out — the card renders exactly as before, no error text, no
//     Retry, no new [role="alert"];
//   * the Released orders card — GET /api/aggregate/releases: the four
//     state counts (Late, Awaiting approval, Partly received,
//     Received), the oldest waiting approval order and the link to
//     #/releases; with no orders it reads "Nothing has been released
//     to the ERP yet.";
//   * the "Value by class" card — GET /api/abc, one row per class
//     (A, B, C): the item-site count, the class's annual value in
//     whole dollars and its share of the total annual value, plus a
//     note counting the unclassified item-sites; a failed read removes
//     the card — no error text, no Retry, no skeleton survives;
//   * the "Exceptions today" card — GET /api/exceptions, the counts
//     line, the "<n> new today" line when the answer's counts.new is
//     a number above 0, up to three of the unacknowledged rows (the
//     answer's order), the measure line from GET
//     /api/exceptions/proposals — "AISCP had proposed the answer for
//     <proposed> of <acknowledged> acknowledgements (<pct>%)" when
//     acknowledged is above 0, "No acknowledgements measured yet."
//     when it is 0 — directly before the link, the link to
//     #/exceptions and the partial-failure note; a failed measure
//     read leaves the line out — the card renders exactly as
//     before, no error text, no Retry, no skeleton survives;
//     a failed list read removes the card — no error text, no
//     Retry, no skeleton survives;
//   * the "Supply this week" card — GET /api/supply-plan/summary:
//     the orders line (the lines, the whole-dollar value and the
//     suppliers), the transfers line ("No transfers suggested"
//     without any), the in-flight line while transfers are in flight
//     and the link to #/orders;
//     a failed read removes the card — no error text, no Retry, no
//     skeleton survives;
//   * the "Forecast overrides" card — GET
//     /api/forecast/overrides/proposals, the card's own read beside
//     the Supply this week card: the line ("Proposed quantity taken
//     in <taken> of <proposed> overrides (<pct>%)" while proposed
//     is above 0, "No proposed corrections measured yet." when it
//     is 0), the direction line ("Moved the same way as the
//     proposal in <same_direction> of <proposed>") while proposed
//     is above 0, the note ("Without a proposed correction:
//     <without_proposal> of <overrides>") while without_proposal
//     is above 0, and the link to #/demand; a failed read removes
//     the card — no error text, no Retry, no skeleton survives;
//   * the attention region — "Needs a decision, largest money first",
//     the five undecided rows with the most money across the three
//     lists; it renders only when all three lists loaded, never from
//     partial list data.
//
// While a region loads it shows a quiet skeleton. A failed load shows
// an inline message with a Retry button in the failed region; Retry
// re-fetches only that region's endpoints. A failed load never leaves
// a blank page: every region ends in data, a skeleton or an error.
(function (global) {
  'use strict';

  var SUMMARY_PATH = '/api/decisions/summary';
  var LIST_QUERY = 'limit=1000&include_decided=true';
  var RELEASES_PATH = '/api/aggregate/releases';
  var ABC_PATH = '/api/abc';
  var EXCEPTIONS_PATH = '/api/exceptions';
  var EXCEPTIONS_PROPOSALS_PATH = '/api/exceptions/proposals';
  var SUPPLY_PATH = '/api/supply-plan/summary';
  var OVERRIDES_PROPOSALS_PATH = '/api/forecast/overrides/proposals';
  var STOCKOUT_PROPOSALS_PATH = '/api/stockout/proposals';
  var EXCESS_PROPOSALS_PATH = '/api/excess/proposals';
  var DRIFT_ACCEPTANCE_PATH = '/api/drift/decisions/acceptance';

  var KINDS = [
    {
      kind: 'drift',
      title: 'Reorder points',
      badge: 'Reorder',
      blurb: 'ERP reorder points that have drifted from the recommended one.',
      endpoint: '/api/drift?' + LIST_QUERY,
      moneyField: 'money_at_stake',
    },
    {
      kind: 'excess',
      title: 'Excess and dead stock',
      badge: 'Excess',
      blurb: 'Money sitting in stock beyond justified cover, plus dead lines.',
      endpoint: '/api/excess?' + LIST_QUERY,
      moneyField: 'money_tied_up',
    },
    {
      kind: 'stockout',
      title: 'Stockout risk',
      badge: 'Stockout',
      blurb: 'Item-sites likely to run out before open supply lands.',
      endpoint: '/api/stockout?' + LIST_QUERY,
      moneyField: 'money_at_risk',
    },
  ];

  var KINDS_BY_KIND = {};
  KINDS.forEach(function (kindMeta) {
    KINDS_BY_KIND[kindMeta.kind] = kindMeta;
  });

  var KIND_ORDER = { drift: 0, excess: 1, stockout: 2 };
  var ATTENTION_LIMIT = 5;

  var REGION_IDS = [
    'ov-region-tiles',
    'ov-region-card-drift',
    'ov-region-card-excess',
    'ov-region-card-stockout',
    'ov-region-releases',
    'ov-region-abc',
    'ov-region-exceptions',
    'ov-region-supply',
    'ov-region-overrides',
    'ov-attention-body',
  ];

  var REGION_LABELS = {
    'ov-region-tiles': 'the value tiles',
    'ov-region-card-drift': 'the Reorder points list card',
    'ov-region-card-excess': 'the Excess and dead stock list card',
    'ov-region-card-stockout': 'the Stockout risk list card',
    'ov-region-releases': 'the Released orders card',
    'ov-attention-body': 'the attention list',
  };

  var regionEls = {};
  var started = false;
  var stale = false;

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
  function moneyLabel(value) {
    if (!isMoney(value)) {
      return '\u2014';
    }
    return '$' + Math.round(value).toLocaleString('en-US');
  }

  function countLabel(value) {
    if (!isMoney(value)) {
      return '0';
    }
    return Math.round(value).toLocaleString('en-US');
  }

  // "N% of accepted money has reached the ERP": 0 when nothing is accepted.
  function appliedPct(acceptedMoney, appliedMoney) {
    if (!isMoney(acceptedMoney) || acceptedMoney <= 0) {
      return 0;
    }
    if (!isMoney(appliedMoney)) {
      return 0;
    }
    var pct = Math.round((appliedMoney / acceptedMoney) * 100);
    return Math.min(100, Math.max(0, pct));
  }

  function isUndecided(row) {
    return !!row && typeof row === 'object' && row.decision == null;
  }

  // Planner quantity precision tiers: whole units with en-US thousands
  // separators (1465.42 -> "1,465") and one decimal for days
  // (12.94 -> "12.9"). Money keeps its whole-dollar format (moneyLabel).
  // Every tier renders null/undefined as an em dash.
  function unitCount(value) {
    if (!isMoney(value)) {
      return '\u2014';
    }
    return Math.round(value).toLocaleString('en-US');
  }

  function coverValue(value) {
    if (!isMoney(value)) {
      return '\u2014';
    }
    return value.toLocaleString('en-US', {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    });
  }

  function reasonFor(kind, row) {
    if (!row || typeof row !== 'object') {
      return 'Needs a decision';
    }
    if (kind === 'drift') {
      if (row.category === 'no_reorder_point') {
        return 'No reorder point in the ERP yet';
      }
      // The direction word already conveys the sign, so the gap is shown
      // as an unsigned magnitude: a -1465.42 under-protected point reads
      // "ERP point 1,465 units below the recommended point", never
      // "ERP point -1465.42 units below". A null gap keeps the direction
      // word and shows an em dash.
      var side = row.direction === 'over' ? 'above' : 'below';
      var gap = isMoney(row.units_gap)
        ? unitCount(Math.abs(row.units_gap))
        : '\u2014';
      return 'ERP point ' + gap + ' units ' + side + ' the recommended point';
    }
    if (kind === 'excess') {
      if (row.category === 'dead') {
        return row.dead_reason === 'stopped'
          ? 'Dead stock: demand has stopped'
          : 'Dead stock: never moved';
      }
      return unitCount(row.excess_units) + ' units beyond justified cover';
    }
    if (kind === 'stockout' && isMoney(row.p_stockout)) {
      var lead = isMoney(row.lead_time_days)
        ? ' within ' + coverValue(row.lead_time_days) + ' days'
        : '';
      return Math.round(row.p_stockout * 100) + '% chance of stockout' + lead;
    }
    return 'Needs a decision';
  }

  /* --------------------------- attention list --------------------------- */

  // The undecided rows (decision null) across the three lists, ordered:
  // money first — a row has money when its kind's money field is a number
  // (including 0); only null/undefined counts as without money and comes
  // after every row with money. Money descending, then the fixed kind
  // order (drift, excess, stockout), then item_code, then site_code.
  function attentionRows(lists) {
    var candidates = [];
    KINDS.forEach(function (kindMeta) {
      var rows = lists && Array.isArray(lists[kindMeta.kind]) ? lists[kindMeta.kind] : [];
      rows.forEach(function (row) {
        if (!isUndecided(row)) {
          return;
        }
        candidates.push({
          kind: kindMeta.kind,
          badge: kindMeta.badge,
          itemCode: typeof row.item_code === 'string' ? row.item_code : '',
          siteCode: typeof row.site_code === 'string' ? row.site_code : '',
          money: row[kindMeta.moneyField],
          reason: reasonFor(kindMeta.kind, row),
        });
      });
    });
    candidates.sort(attentionCompare);
    return candidates.slice(0, ATTENTION_LIMIT);
  }

  function attentionCompare(a, b) {
    var aHasMoney = isMoney(a.money);
    var bHasMoney = isMoney(b.money);
    if (aHasMoney !== bHasMoney) {
      return aHasMoney ? -1 : 1;
    }
    if (aHasMoney && a.money !== b.money) {
      return b.money - a.money;
    }
    if (KIND_ORDER[a.kind] !== KIND_ORDER[b.kind]) {
      return KIND_ORDER[a.kind] - KIND_ORDER[b.kind];
    }
    if (a.itemCode !== b.itemCode) {
      return a.itemCode < b.itemCode ? -1 : 1;
    }
    if (a.siteCode !== b.siteCode) {
      return a.siteCode < b.siteCode ? -1 : 1;
    }
    return 0;
  }

  function attentionRowHtml(row) {
    var target =
      '#/queue?kind=' + encodeURIComponent(row.kind) +
      '&item=' + encodeURIComponent(row.itemCode) +
      '&site=' + encodeURIComponent(row.siteCode);
    return (
      '<button type="button" class="attn-row" data-target="' + esc(target) + '">' +
        '<span class="badge badge-' + esc(row.kind) + '">' + esc(row.badge) + '</span>' +
        '<span class="attn-main">' +
          '<span class="attn-item">' +
            esc(row.itemCode) + ' \u00b7 ' + esc(row.siteCode) +
          '</span>' +
          '<span class="attn-why">' + esc(row.reason) + '</span>' +
        '</span>' +
        '<span class="attn-money">' + moneyLabel(row.money) + '</span>' +
        '<svg class="attn-chevron" width="18" height="18" viewBox="0 0 24 24" fill="none" ' +
          'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
          'aria-hidden="true"><path d="M9 6l6 6-6 6"></path></svg>' +
      '</button>'
    );
  }

  /* ------------------------------ regions ------------------------------- */

  function overviewActive() {
    var active = document.querySelector('.view.is-active');
    return !!active && active.getAttribute('data-view') === 'overview';
  }

  function showSkeleton(regionId) {
    if (regionId === ABC_REGION_ID) {
      ensureAbcSection();
    }
    if (regionId === EXCEPTIONS_REGION_ID) {
      ensureExceptionsSection();
    }
    if (regionId === SUPPLY_REGION_ID) {
      ensureSupplySection();
    }
    if (regionId === OVERRIDES_REGION_ID) {
      ensureOverridesSection();
    }
    if (regionId === 'ov-region-tiles') {
      var tiles = '';
      for (var i = 0; i < 3; i++) {
        tiles +=
          '<div class="card ov-tile">' +
            '<span class="skel skel-line"></span>' +
            '<span class="skel skel-value"></span>' +
            '<span class="skel skel-line skel-wide"></span>' +
          '</div>';
      }
      regionEls[regionId].innerHTML =
        '<div class="ov-tiles" aria-hidden="true">' + tiles + '</div>';
      return;
    }
    if (regionId === 'ov-attention-body') {
      var rows = '';
      for (var j = 0; j < ATTENTION_LIMIT; j++) {
        rows += '<span class="skel skel-attn-row"></span>';
      }
      regionEls[regionId].innerHTML = '<div aria-hidden="true">' + rows + '</div>';
      return;
    }
    regionEls[regionId].innerHTML =
      '<span class="skel skel-line" aria-hidden="true"></span>' +
      '<span class="skel skel-line skel-wide" aria-hidden="true"></span>' +
      '<span class="skel skel-figures" aria-hidden="true"></span>' +
      '<span class="skel skel-button" aria-hidden="true"></span>';
  }

  function showError(regionId, error) {
    var detail = error && typeof error.message === 'string' ? error.message : '';
    regionEls[regionId].innerHTML =
      '<div class="ov-error" role="alert">' +
        '<p class="ov-error-msg">Couldn\u2019t load ' + REGION_LABELS[regionId] +
          (detail ? ': ' + esc(detail) : '') + '.</p>' +
        '<button type="button" class="btn btn-secondary ov-retry">Retry</button>' +
      '</div>';
    regionEls[regionId]
      .querySelector('.ov-retry')
      .addEventListener('click', function () {
        loadRegion(regionId);
      });
  }

  // The Released orders card's error: a 502 or 503 carries the
  // server's detail (the ERP is unreachable), any other failure gets
  // the generic line — the same split as the Releases view. The block
  // stays a single row (message and Retry side by side) so a failed
  // card grows the Overview no taller than a row of figures.
  function showReleasesError(error) {
    var status = error && typeof error.status === 'number' ? error.status : 0;
    var message = error && typeof error.message === 'string' ? error.message : '';
    var text = (status === 502 || status === 503) && message
      ? message
      : 'Released orders could not be loaded.';
    regionEls['ov-region-releases'].innerHTML =
      '<div class="ov-releases-error" role="alert">' +
        '<p class="ov-releases-error-msg">' + esc(text) + '</p>' +
        '<button type="button" class="btn btn-secondary ov-retry">Retry</button>' +
      '</div>';
    regionEls['ov-region-releases']
      .querySelector('.ov-retry')
      .addEventListener('click', function () {
        loadRegion('ov-region-releases');
      });
  }

  function loadRegion(regionId) {
    showSkeleton(regionId);
    var work;
    if (regionId === 'ov-region-tiles') {
      work = global.Api.get(SUMMARY_PATH).then(function (summary) {
        renderTiles(summary);
      });
    } else if (regionId === 'ov-region-releases') {
      work = global.Api.get(RELEASES_PATH).then(function (answer) {
        renderReleases(answer);
      });
    } else if (regionId === ABC_REGION_ID) {
      work = global.Api.get(ABC_PATH).then(function (answer) {
        renderAbc(answer);
      });
    } else if (regionId === EXCEPTIONS_REGION_ID) {
      // The card's measure line: its own read beside the list,
      // joined in one Promise.all with its own catch that answers
      // null, so a failed read never fails the card — it only
      // leaves the line out.
      work = Promise.all([
        global.Api.get(EXCEPTIONS_PATH),
        global.Api.get(EXCEPTIONS_PROPOSALS_PATH).catch(function () {
          return null;
        }),
      ]).then(function (results) {
        renderExceptions(results[0], results[1]);
      });
    } else if (regionId === SUPPLY_REGION_ID) {
      work = global.Api.get(SUPPLY_PATH).then(function (answer) {
        renderSupply(answer);
      });
    } else if (regionId === OVERRIDES_REGION_ID) {
      work = global.Api.get(OVERRIDES_PROPOSALS_PATH).then(function (answer) {
        renderOverrides(answer);
      });
    } else if (regionId === 'ov-attention-body') {
      work = Promise.all([
        global.Api.get(KINDS_BY_KIND.drift.endpoint),
        global.Api.get(KINDS_BY_KIND.excess.endpoint),
        global.Api.get(KINDS_BY_KIND.stockout.endpoint),
      ]).then(function (results) {
        renderAttention({
          drift: results[0],
          excess: results[1],
          stockout: results[2],
        });
      });
    } else {
      var kind = regionId.slice('ov-region-card-'.length);
      var reads = [
        global.Api.get(SUMMARY_PATH),
        global.Api.get(KINDS_BY_KIND[kind].endpoint),
      ];
      if (kind === 'drift') {
        // The card's "acceptance" block: its own read beside the
        // summary and the list, with its own catch that answers
        // null so a failed read never fails the card — it only
        // leaves the block out.
        reads.push(
          global.Api.get(DRIFT_ACCEPTANCE_PATH).catch(function () {
            return null;
          })
        );
      }
      if (kind === 'stockout') {
        // The card's "proposed remedy" block: its own read beside
        // the summary and the list, with its own catch that answers
        // null so a failed read never fails the card — it only
        // leaves the block out.
        reads.push(
          global.Api.get(STOCKOUT_PROPOSALS_PATH).catch(function () {
            return null;
          })
        );
      }
      if (kind === 'excess') {
        // The card's "proposed action" block: its own read beside
        // the summary and the list, with its own catch that answers
        // null so a failed read never fails the card — it only
        // leaves the block out.
        reads.push(
          global.Api.get(EXCESS_PROPOSALS_PATH).catch(function () {
            return null;
          })
        );
      }
      work = Promise.all(reads).then(function (results) {
        renderCard(KINDS_BY_KIND[kind], results[0], results[1], results[2]);
      });
    }
    work.catch(function (error) {
      if (regionId === ABC_REGION_ID) {
        removeAbcSection();
      } else if (regionId === EXCEPTIONS_REGION_ID) {
        removeExceptionsSection();
      } else if (regionId === SUPPLY_REGION_ID) {
        removeSupplySection();
      } else if (regionId === OVERRIDES_REGION_ID) {
        removeOverridesSection();
      } else if (regionId === 'ov-region-releases') {
        showReleasesError(error);
      } else {
        showError(regionId, error);
      }
    });
  }

  function activate() {
    var appEl = document.getElementById('app');
    if (!appEl || appEl.hidden || !overviewActive()) {
      return;
    }
    // Already loaded and nothing has changed: the figures stay as they
    // are. A decision recorded in the Work queue sets stale, and the
    // next activation then re-requests the summary and the lists.
    if (started && !stale) {
      return;
    }
    started = true;
    stale = false;
    REGION_IDS.forEach(function (regionId) {
      // The "Value by class", "Exceptions today", "Supply this
      // week" and "Forecast overrides" sections are dropped from
      // the DOM when their read fails, so they are (re)created on
      // every activation.
      if (
        regionId === ABC_REGION_ID ||
        regionId === EXCEPTIONS_REGION_ID ||
        regionId === SUPPLY_REGION_ID ||
        regionId === OVERRIDES_REGION_ID ||
        regionEls[regionId]
      ) {
        loadRegion(regionId);
      }
    });
  }

  // A decision recorded in the Work queue (any of the three kinds)
  // makes the figures stale: the next time the Overview becomes the
  // active view it re-requests the summary and the lists and shows the
  // new figures, without a page reload.
  function invalidate() {
    stale = true;
  }

  // Sign out drops the session: the next sign-in re-loads the Overview.
  function reset() {
    started = false;
    stale = false;
    REGION_IDS.forEach(function (regionId) {
      if (regionId === ABC_REGION_ID) {
        removeAbcSection();
      } else if (regionId === EXCEPTIONS_REGION_ID) {
        removeExceptionsSection();
      } else if (regionId === SUPPLY_REGION_ID) {
        removeSupplySection();
      } else if (regionId === OVERRIDES_REGION_ID) {
        removeOverridesSection();
      } else if (regionEls[regionId]) {
        regionEls[regionId].innerHTML = '';
      }
    });
  }

  /* ------------------------------ rendering ----------------------------- */

  function renderTiles(summary) {
    var total =
      summary && typeof summary === 'object' && summary.total &&
      typeof summary.total === 'object'
        ? summary.total
        : {};
    var accepted = total.accepted || {};
    var applied = total.applied || {};
    var outstanding = total.outstanding || {};
    var pct = appliedPct(accepted.money, applied.money);

    regionEls['ov-region-tiles'].innerHTML =
      '<div class="ov-tiles">' +
        '<div class="card ov-tile">' +
          '<span class="ov-tile-label">Accepted, money at stake</span>' +
          '<span class="ov-tile-value">' + moneyLabel(accepted.money) + '</span>' +
          '<span class="ov-tile-note">' +
            countLabel(accepted.count) +
            ' accepted decisions across all three lists' +
          '</span>' +
        '</div>' +
        '<div class="card ov-tile">' +
          '<span class="ov-tile-label">Applied in the ERP</span>' +
          '<span class="ov-tile-value is-accent">' + moneyLabel(applied.money) + '</span>' +
          '<span class="ov-progress" role="progressbar" ' +
            'aria-label="Accepted money that has reached the ERP" ' +
            'aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + pct + '">' +
            '<span class="ov-progress-fill"></span>' +
          '</span>' +
          '<span class="ov-tile-note">' +
            pct + '% of accepted money has reached the ERP' +
          '</span>' +
        '</div>' +
        '<div class="card ov-tile">' +
          '<span class="ov-tile-label">Accepted, still outstanding</span>' +
          '<span class="ov-tile-value is-risk">' + moneyLabel(outstanding.money) + '</span>' +
          '<span class="ov-tile-note">' +
            countLabel(outstanding.count) +
            ' accepted changes not yet seen in the ERP' +
          '</span>' +
        '</div>' +
      '</div>';

    var fill = regionEls['ov-region-tiles'].querySelector('.ov-progress-fill');
    if (fill) {
      fill.style.width = pct + '%';
    }
  }

  function figureHtml(label, valueHtml, valueClass) {
    return (
      '<span class="ov-figure">' +
        '<span class="ov-figure-label">' + esc(label) + '</span>' +
        '<span class="ov-figure-value' + (valueClass ? ' ' + valueClass : '') + '">' +
          valueHtml +
        '</span>' +
      '</span>'
    );
  }

  function renderCard(kindMeta, summary, list, ownRead) {
    var kind = kindMeta.kind;
    var buckets =
      summary && typeof summary === 'object' && summary[kind] &&
      typeof summary[kind] === 'object'
        ? summary[kind]
        : {};
    var rows = Array.isArray(list) ? list : [];
    var toDecide = 0;
    rows.forEach(function (row) {
      if (isUndecided(row)) {
        toDecide += 1;
      }
    });

    var region = regionEls['ov-region-card-' + kind];
    region.innerHTML =
      '<div class="ov-card-head">' +
        '<h2 class="ov-card-title">' + esc(kindMeta.title) + '</h2>' +
        '<span class="ov-card-to-decide">' + countLabel(toDecide) + ' to decide</span>' +
      '</div>' +
      '<span class="ov-card-blurb">' + esc(kindMeta.blurb) + '</span>' +
      '<div class="ov-card-figures">' +
        figureHtml('Accepted', moneyLabel(buckets.accepted ? buckets.accepted.money : null)) +
        figureHtml('Applied', moneyLabel(buckets.applied ? buckets.applied.money : null), 'is-accent') +
        figureHtml('Dismissed', countLabel(buckets.dismissed ? buckets.dismissed.count : null)) +
      '</div>' +
      '<button type="button" class="btn btn-secondary ov-open" data-kind="' +
        esc(kind) + '">Open ' + esc(kindMeta.title) + ' list</button>';

    region.querySelector('.ov-open').addEventListener('click', function () {
      global.location.hash = '#/queue?kind=' + kind;
    });

    // The Reorder points card's "acceptance" block: with a usable
    // answer, directly after the figures row and directly before the
    // Open button; a failed read or an unusable body leaves it out
    // and the card renders exactly as before.
    if (kind === 'drift' && driftAcceptanceValid(ownRead)) {
      region
        .querySelector('.ov-card-figures')
        .insertAdjacentElement('afterend', driftAcceptanceBlock(ownRead));
    }
    // The Stockout risk card's "proposed remedy" block: with a
    // usable answer, directly after the figures row and directly
    // before the Open button; a failed read or an unusable body
    // leaves it out and the card renders exactly as before.
    if (kind === 'stockout' && proposalsAnswerValid(ownRead)) {
      region
        .querySelector('.ov-card-figures')
        .insertAdjacentElement('afterend', proposalsBlock(ownRead));
    }
    // The Excess and dead stock card's "proposed action" block:
    // the same answer shape as the Stockout risk card's read, the
    // same placement; a failed read or an unusable body leaves it
    // out and the card renders exactly as before.
    if (kind === 'excess' && proposalsAnswerValid(ownRead)) {
      region
        .querySelector('.ov-card-figures')
        .insertAdjacentElement('afterend', excessProposalsBlock(ownRead));
    }
  }

  /* ------------------------ stockout proposals ------------------------ */

  // The Stockout risk card's "proposed remedy" measurement. The
  // answer is usable only when it is an object whose total holds the
  // finite numbers proposed and chosen and whose without_proposal is
  // a finite number; anything else — the failed read's null, any
  // status, a network error, a body of another shape — leaves the
  // block out.
  function proposalsAnswerValid(answer) {
    if (!answer || typeof answer !== 'object') {
      return false;
    }
    var total = answer.total;
    if (!total || typeof total !== 'object') {
      return false;
    }
    return (
      isMoney(total.proposed) &&
      isMoney(total.chosen) &&
      isMoney(answer.without_proposal)
    );
  }

  // The per-kind lines, in the fixed order and with the fixed labels
  // the Work queue and the decision log use: Expedite, Transfer,
  // Raise order.
  var PROPOSAL_KINDS = [
    { key: 'expedite', label: 'Expedite' },
    { key: 'transfer', label: 'Transfer' },
    { key: 'raise_order', label: 'Raise order' },
  ];

  // The block's lines, built node by node with textContent — no API
  // value goes through innerHTML — in order: the measurement line
  // ("No proposed remedies measured yet." while total.proposed is
  // 0), the per-kind line only while total.proposed is above 0 (each
  // kind whose proposed is a number above 0, "<label> <chosen> of
  // <proposed>", joined by " \u00b7 "; the line is left out when no
  // kind is left) and the note only while without_proposal is above
  // 0. Every number goes through countLabel, whole with thousands
  // separators, and the words stay the same for a count of 1, like
  // the value tiles' "accepted decisions" note.
  function proposalsBlock(answer) {
    var total = answer.total;

    var block = document.createElement('div');
    block.className = 'ov-proposals';
    block.setAttribute('data-testid', 'ov-stockout-proposals');

    var line = document.createElement('p');
    line.className = 'ov-proposals-line';
    line.setAttribute('data-testid', 'ov-stockout-proposals-line');
    if (total.proposed > 0) {
      var pct = Math.round((100 * total.chosen) / total.proposed);
      line.textContent =
        'Proposed remedy taken in ' +
        countLabel(total.chosen) +
        ' of ' +
        countLabel(total.proposed) +
        ' accepted decisions (' +
        countLabel(pct) +
        '%)';
    } else {
      line.textContent = 'No proposed remedies measured yet.';
    }
    block.appendChild(line);

    if (total.proposed > 0) {
      var parts = [];
      PROPOSAL_KINDS.forEach(function (kindMeta) {
        var kind = answer[kindMeta.key];
        if (
          !kind ||
          typeof kind !== 'object' ||
          !isMoney(kind.proposed) ||
          kind.proposed <= 0
        ) {
          return;
        }
        parts.push(
          kindMeta.label +
            ' ' +
            countLabel(kind.chosen) +
            ' of ' +
            countLabel(kind.proposed)
        );
      });
      if (parts.length) {
        var kinds = document.createElement('p');
        kinds.className = 'ov-proposals-kinds';
        kinds.setAttribute('data-testid', 'ov-stockout-proposals-kinds');
        kinds.textContent = parts.join(' \u00b7 ');
        block.appendChild(kinds);
      }
    }

    if (answer.without_proposal > 0) {
      var note = document.createElement('p');
      note.className = 'ov-proposals-note';
      note.setAttribute('data-testid', 'ov-stockout-proposals-note');
      note.textContent =
        countLabel(answer.without_proposal) +
        ' accepted before proposals were kept';
      block.appendChild(note);
    }

    return block;
  }

  /* ------------------------ excess proposals ------------------------ */

  // The Excess and dead stock card's "proposed action" measurement.
  // The answer is usable under the same rule as the Stockout risk
  // card's read (proposalsAnswerValid): an object whose total holds
  // the finite numbers proposed and chosen and whose without_proposal
  // is a finite number; anything else — the failed read's null, any
  // status, a network error, a body of another shape — leaves the
  // block out.
  // The per-kind lines, in the fixed order and with the fixed labels
  // the Work queue's excess action buttons use: Stop replenishing,
  // Reduce, Dispose.
  var EXCESS_PROPOSAL_KINDS = [
    { key: 'stop_replenishing', label: 'Stop replenishing' },
    { key: 'reduce', label: 'Reduce' },
    { key: 'dispose', label: 'Dispose' },
  ];

  // The block's lines, built node by node with textContent — no API
  // value goes through innerHTML — in order: the measurement line
  // ("No proposed actions measured yet." while total.proposed is
  // 0), the per-kind line only while total.proposed is above 0 (each
  // kind whose proposed is a number above 0, "<label> <chosen> of
  // <proposed>", joined by " · "; the line is left out when no
  // kind is left) and the note only while without_proposal is above
  // 0. Every number goes through countLabel, whole with thousands
  // separators, and the words stay the same for a count of 1, like
  // the value tiles' "accepted decisions" note. The block reuses the
  // Stockout risk block's classes, so the styles come from the same
  // rules.
  function excessProposalsBlock(answer) {
    var total = answer.total;

    var block = document.createElement('div');
    block.className = 'ov-proposals';
    block.setAttribute('data-testid', 'ov-excess-proposals');

    var line = document.createElement('p');
    line.className = 'ov-proposals-line';
    line.setAttribute('data-testid', 'ov-excess-proposals-line');
    if (total.proposed > 0) {
      var pct = Math.round((100 * total.chosen) / total.proposed);
      line.textContent =
        'Proposed action taken in ' +
        countLabel(total.chosen) +
        ' of ' +
        countLabel(total.proposed) +
        ' accepted decisions (' +
        countLabel(pct) +
        '%)';
    } else {
      line.textContent = 'No proposed actions measured yet.';
    }
    block.appendChild(line);

    if (total.proposed > 0) {
      var parts = [];
      EXCESS_PROPOSAL_KINDS.forEach(function (kindMeta) {
        var kind = answer[kindMeta.key];
        if (
          !kind ||
          typeof kind !== 'object' ||
          !isMoney(kind.proposed) ||
          kind.proposed <= 0
        ) {
          return;
        }
        parts.push(
          kindMeta.label +
            ' ' +
            countLabel(kind.chosen) +
            ' of ' +
            countLabel(kind.proposed)
        );
      });
      if (parts.length) {
        var kinds = document.createElement('p');
        kinds.className = 'ov-proposals-kinds';
        kinds.setAttribute('data-testid', 'ov-excess-proposals-kinds');
        kinds.textContent = parts.join(' \u00b7 ');
        block.appendChild(kinds);
      }
    }

    if (answer.without_proposal > 0) {
      var note = document.createElement('p');
      note.className = 'ov-proposals-note';
      note.setAttribute('data-testid', 'ov-excess-proposals-note');
      note.textContent =
        countLabel(answer.without_proposal) +
        ' accepted before proposals were kept';
      block.appendChild(note);
    }

    return block;
  }

  /* ------------------------ drift acceptance ------------------------ */

  // The Reorder points card's "acceptance" measurement. The answer
  // is usable only when it is an object whose accepted and dismissed
  // are objects holding the finite number count and whose rate is a
  // finite number or null; anything else — the failed read's null,
  // any status, a network error, a body of another shape — leaves
  // the block out.
  function driftAcceptanceValid(answer) {
    if (!answer || typeof answer !== 'object') {
      return false;
    }
    var accepted = answer.accepted;
    var dismissed = answer.dismissed;
    if (!accepted || typeof accepted !== 'object' || !isMoney(accepted.count)) {
      return false;
    }
    if (!dismissed || typeof dismissed !== 'object' || !isMoney(dismissed.count)) {
      return false;
    }
    return answer.rate === null || isMoney(answer.rate);
  }

  // The block's lines, built node by node with textContent — no API
  // value goes through innerHTML — in order: the measurement line
  // ("No reorder-point decisions measured yet." while decided is 0,
  // otherwise "Recommendation accepted in <accepted> of <decided>
  // decided rows (<pct>%)" with pct = round(100 * accepted /
  // decided), and ", <mpct>% of the money at stake" with mpct =
  // round(100 * money_rate) while money_rate is a finite number) and
  // the direction line only while decided is above 0 ("Raises <a> of
  // <n>" for the raise bucket, "Cuts <a> of <n>" for the lower
  // bucket, each part only while its bucket is an object holding the
  // finite numbers accepted and dismissed and n = accepted +
  // dismissed is above 0, joined by " · "; the line is left out
  // when neither part is left). Every number goes through countLabel,
  // whole with thousands separators. The block reuses the other
  // cards' blocks' ov-proposals classes, so the styles come from the
  // same rules.
  function driftAcceptanceBlock(answer) {
    var accepted = answer.accepted.count;
    var dismissed = answer.dismissed.count;
    var decided = accepted + dismissed;

    var block = document.createElement('div');
    block.className = 'ov-proposals';
    block.setAttribute('data-testid', 'ov-drift-acceptance');

    var line = document.createElement('p');
    line.className = 'ov-proposals-line';
    line.setAttribute('data-testid', 'ov-drift-acceptance-line');
    if (decided > 0) {
      var pct = Math.round((100 * accepted) / decided);
      var text =
        'Recommendation accepted in ' +
        countLabel(accepted) +
        ' of ' +
        countLabel(decided) +
        ' decided rows (' +
        countLabel(pct) +
        '%)';
      if (isMoney(answer.money_rate)) {
        text +=
          ', ' +
          countLabel(Math.round(100 * answer.money_rate)) +
          '% of the money at stake';
      }
      line.textContent = text;
    } else {
      line.textContent = 'No reorder-point decisions measured yet.';
    }
    block.appendChild(line);

    if (decided > 0) {
      var parts = [];
      [
        { key: 'raise', label: 'Raises' },
        { key: 'lower', label: 'Cuts' },
      ].forEach(function (bucketMeta) {
        var bucket = answer[bucketMeta.key];
        if (
          !bucket ||
          typeof bucket !== 'object' ||
          !isMoney(bucket.accepted) ||
          !isMoney(bucket.dismissed)
        ) {
          return;
        }
        var total = bucket.accepted + bucket.dismissed;
        if (total <= 0) {
          return;
        }
        parts.push(
          bucketMeta.label +
            ' ' +
            countLabel(bucket.accepted) +
            ' of ' +
            countLabel(total)
        );
      });
      if (parts.length) {
        var direction = document.createElement('p');
        direction.className = 'ov-proposals-kinds';
        direction.setAttribute('data-testid', 'ov-drift-acceptance-direction');
        direction.textContent = parts.join(' \u00b7 ');
        block.appendChild(direction);
      }
    }

    return block;
  }

  function renderAttention(lists) {
    var rows = attentionRows(lists);
    if (!rows.length) {
      regionEls['ov-attention-body'].innerHTML =
        '<p class="ov-attention-empty">Nothing needs a decision right now.</p>';
      return;
    }
    regionEls['ov-attention-body'].innerHTML = rows.map(attentionRowHtml).join('');
    var buttons = regionEls['ov-attention-body'].querySelectorAll('.attn-row');
    for (var i = 0; i < buttons.length; i++) {
      (function (button) {
        button.addEventListener('click', function () {
          var target = button.getAttribute('data-target');
          if (target) {
            global.location.hash = target;
          }
        });
      })(buttons[i]);
    }
  }

  /* --------------------------- released orders --------------------------- */

  // "Oldest waiting <n> d (<order_nbr>)": the largest days_waiting among
  // the awaiting_approval orders; the first one in the answer's order
  // wins a tie. An order without a finite numeric days_waiting never
  // qualifies.
  function oldestWaiting(orders) {
    var best = null;
    for (var i = 0; i < orders.length; i++) {
      var order = orders[i];
      if (!order || typeof order !== 'object' || order.state !== 'awaiting_approval') {
        continue;
      }
      var days = order.days_waiting;
      if (typeof days !== 'number' || !isFinite(days)) {
        continue;
      }
      if (best === null || days > best.days) {
        best = {
          days: days,
          orderNbr: order.order_nbr == null ? '' : order.order_nbr,
        };
      }
    }
    return best;
  }

  function releasesCount(counts, key) {
    var value = counts[key];
    return isMoney(value) ? value : 0;
  }

  // One of the card's four counts: the number with its label. Late
  // carries the leading \u25bc marker and the risk colour while above 0.
  function releaseFigureHtml(label, value, isLate) {
    var valueClass = '';
    var marker = '';
    if (isLate && value > 0) {
      valueClass = ' is-risk';
      marker = '\u25bc ';
    }
    return (
      '<span class="ov-figure">' +
        '<span class="ov-figure-label">' + esc(label) + '</span>' +
        '<span class="ov-figure-value' + valueClass + '">' +
          esc(marker + countLabel(value)) +
        '</span>' +
      '</span>'
    );
  }

  function renderReleases(answer) {
    var region = regionEls['ov-region-releases'];
    var orders = answer && Array.isArray(answer.orders) ? answer.orders : [];
    var counts =
      answer && typeof answer.counts === 'object' && answer.counts !== null
        ? answer.counts
        : {};

    var head =
      '<div class="ov-releases-head">' +
        '<h2 class="ov-releases-title">Released orders</h2>' +
        '<a class="ov-releases-open" href="#/releases" ' +
          'data-testid="ov-releases-open">Open Releases</a>' +
      '</div>';

    // With no orders the card keeps its title and link and shows the
    // first-load note instead of the counts.
    if (!orders.length) {
      region.innerHTML =
        head +
        '<p class="ov-releases-empty">Nothing has been released to the ERP yet.</p>';
      return;
    }

    var body =
      '<div class="ov-releases-figures">' +
        releaseFigureHtml('Late', releasesCount(counts, 'late'), true) +
        releaseFigureHtml('Awaiting approval', releasesCount(counts, 'awaiting_approval'), false) +
        releaseFigureHtml('Partly received', releasesCount(counts, 'partly_received'), false) +
        releaseFigureHtml('Received', releasesCount(counts, 'received'), false) +
      '</div>';

    var oldest = oldestWaiting(orders);
    if (oldest) {
      body +=
        '<p class="ov-releases-note">Oldest waiting ' + esc(oldest.days) +
        ' d (' + esc(oldest.orderNbr) + ')</p>';
    }

    region.innerHTML = head + body;
  }

  /* --------------------------- value by class --------------------------- */

  // The "Value by class" card is a load region like the others, but its
  // failure mode is silent: a failed read — any status, a network error,
  // or a body without a numeric total_annual_value, counts and an array
  // of rows — removes the section from the DOM. No error text, no Retry
  // and no skeleton survive; the next load recreates the section after
  // the Released orders card. The card shows class aggregates only,
  // never an item code, and every value is inserted escaped.

  var ABC_REGION_ID = 'ov-region-abc';
  var ABC_CLASSES = ['A', 'B', 'C'];

  function ensureAbcSection() {
    var existing = document.getElementById(ABC_REGION_ID);
    if (existing) {
      return existing;
    }
    var section = document.createElement('section');
    section.id = ABC_REGION_ID;
    section.className = 'card ov-abc';
    section.setAttribute('aria-label', 'Value by class');
    var releases = document.getElementById('ov-region-releases');
    if (releases && releases.parentNode) {
      releases.insertAdjacentElement('afterend', section);
    } else {
      var container = document.querySelector('#view-overview .ov');
      if (container) {
        var attention = container.querySelector('.ov-attention');
        if (attention) {
          attention.insertAdjacentElement('beforebegin', section);
        } else {
          container.appendChild(section);
        }
      }
    }
    regionEls[ABC_REGION_ID] = section;
    return section;
  }

  function removeAbcSection() {
    var section = regionEls[ABC_REGION_ID];
    regionEls[ABC_REGION_ID] = null;
    if (section && section.parentNode) {
      section.parentNode.removeChild(section);
    }
  }

  // The answer is usable only with a numeric total_annual_value, a
  // counts object and an array of rows; anything else fails the region.
  function abcAnswerValid(answer) {
    return (
      !!answer && typeof answer === 'object' &&
      typeof answer.total_annual_value === 'number' &&
      isFinite(answer.total_annual_value) &&
      typeof answer.counts === 'object' && answer.counts !== null &&
      Array.isArray(answer.rows)
    );
  }

  // The class's annual value: the sum of that class's rows' finite
  // annual_value entries (unclassified and without-money rows add nothing).
  function abcClassSum(rows, cls) {
    var sum = 0;
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!row || typeof row !== 'object' || row.abc_class !== cls) {
        continue;
      }
      var value = row.annual_value;
      if (typeof value === 'number' && isFinite(value)) {
        sum += value;
      }
    }
    return sum;
  }

  function renderAbc(answer) {
    if (!abcAnswerValid(answer)) {
      removeAbcSection();
      return;
    }
    var section = ensureAbcSection();
    var total = answer.total_annual_value;
    var bodyRows = ABC_CLASSES.map(function (cls) {
      var sum = abcClassSum(answer.rows, cls);
      // total_annual_value 0: the shares read "0%", never a division by zero.
      var share = total > 0 ? Math.round((sum / total) * 100) : 0;
      return (
        '<tr>' +
          '<td class="ov-abc-class">' + esc(cls) + '</td>' +
          '<td class="ov-abc-count">' + countLabel(answer.counts[cls]) + '</td>' +
          '<td class="ov-abc-value">' + moneyLabel(sum) + '</td>' +
          '<td class="ov-abc-share">' + share + '%</td>' +
        '</tr>'
      );
    }).join('');

    section.innerHTML =
      '<h2 class="ov-abc-title">Value by class</h2>' +
      '<table class="ov-abc-table">' +
        '<colgroup>' +
          '<col class="ov-abc-col-class"><col class="ov-abc-col-count">' +
          '<col class="ov-abc-col-value"><col class="ov-abc-col-share">' +
        '</colgroup>' +
        '<thead><tr>' +
          '<th scope="col">Class</th>' +
          '<th scope="col">Item-sites</th>' +
          '<th scope="col">Annual value</th>' +
          '<th scope="col">Share</th>' +
        '</tr></thead>' +
        '<tbody>' + bodyRows + '</tbody>' +
      '</table>' +
      '<p class="ov-abc-note">' + countLabel(answer.counts.unclassified) +
        ' item-sites have no class (no demand, no cost or no recommendation).</p>';
  }

  /* --------------------------- exceptions today ------------------------- */

  // The "Exceptions today" card is a load region like the "Value by
  // class" card, with the same silent failure: a failed read — any
  // status, a network error, or a body that is not an object with
  // numeric counts.high / counts.medium / counts.acknowledged and an
  // array rows — removes the section from the DOM. No error text, no
  // Retry and no skeleton survive; the next load recreates the section
  // after the "Value by class" card, or after the Released orders card
  // when that card is absent. The card shows the counts line, up to
  // three of the unacknowledged rows (the answer's order) and the link
  // to the inbox; every API value is inserted as text. When the
  // answer's counts.new is a number above 0, the card shows the
  // "<n> new today" line directly after the counts line; otherwise
  // there is no such element. The measure line comes from the
  // card's own read (GET /api/exceptions/proposals) and stands
  // directly before the link: "AISCP had proposed the answer for
  // <proposed> of <acknowledged> acknowledgements (<pct>%)" when
  // acknowledged is above 0, "No acknowledgements measured yet."
  // when it is 0. A failed measure read — any status, a network
  // error, or a body that is not an object whose acknowledged and
  // proposed are finite numbers with proposed no greater than
  // acknowledged — leaves the line out; the card renders exactly
  // as without the line.

  var EXCEPTIONS_REGION_ID = 'ov-region-exceptions';
  var EXCEPTIONS_TOP_LIMIT = 3;

  function ensureExceptionsSection() {
    var existing = document.getElementById(EXCEPTIONS_REGION_ID);
    if (existing) {
      return existing;
    }
    var section = document.createElement('section');
    section.id = EXCEPTIONS_REGION_ID;
    section.className = 'card ov-exceptions';
    section.setAttribute('aria-label', 'Exceptions today');
    // After the "Value by class" card when it is in the DOM, otherwise
    // after the Released orders card.
    var abc = document.querySelector('.ov-abc');
    var anchor = abc && abc.parentNode ? abc : null;
    if (!anchor) {
      var releases = document.getElementById('ov-region-releases');
      anchor = releases && releases.parentNode ? releases : null;
    }
    if (anchor) {
      anchor.insertAdjacentElement('afterend', section);
    } else {
      var container = document.querySelector('#view-overview .ov');
      if (container) {
        var attention = container.querySelector('.ov-attention');
        if (attention) {
          attention.insertAdjacentElement('beforebegin', section);
        } else {
          container.appendChild(section);
        }
      }
    }
    regionEls[EXCEPTIONS_REGION_ID] = section;
    return section;
  }

  function removeExceptionsSection() {
    var section = regionEls[EXCEPTIONS_REGION_ID];
    regionEls[EXCEPTIONS_REGION_ID] = null;
    if (section && section.parentNode) {
      section.parentNode.removeChild(section);
    }
  }

  // The answer is usable only with an object counts whose high, medium
  // and acknowledged entries are finite numbers, and an array of rows;
  // anything else fails the region.
  function exceptionsAnswerValid(answer) {
    if (!answer || typeof answer !== 'object') {
      return false;
    }
    var counts = answer.counts;
    if (!counts || typeof counts !== 'object') {
      return false;
    }
    return (
      isMoney(counts.high) &&
      isMoney(counts.medium) &&
      isMoney(counts.acknowledged) &&
      Array.isArray(answer.rows)
    );
  }

  // "<title> — <item_code> / <site_code>: <detail>", with the
  // " — <item_code> / <site_code>" part left out when both codes are
  // null (the row is not item-specific).
  function exceptionsRowText(row) {
    var title = row.title == null ? '' : String(row.title);
    var detail = row.detail == null ? '' : String(row.detail);
    var itemCode = row.item_code;
    var siteCode = row.site_code;
    if (itemCode == null && siteCode == null) {
      return title + ': ' + detail;
    }
    return (
      title + ' \u2014 ' +
      (itemCode == null ? '' : String(itemCode)) + ' / ' +
      (siteCode == null ? '' : String(siteCode)) + ': ' + detail
    );
  }

  // The measure is usable only with an object whose acknowledged and
  // proposed entries are finite numbers, proposed no greater than
  // acknowledged; anything else leaves the line out.
  function exceptionsMeasureValid(measure) {
    if (!measure || typeof measure !== 'object' || Array.isArray(measure)) {
      return false;
    }
    return (
      isMoney(measure.acknowledged) &&
      isMoney(measure.proposed) &&
      measure.proposed <= measure.acknowledged
    );
  }

  // "AISCP had proposed the answer for <proposed> of
  // <acknowledged> acknowledgements (<pct>%)" — every number through
  // countLabel, the words the same for a count of 1 — or "No
  // acknowledgements measured yet." when acknowledged is 0.
  function exceptionsMeasureText(measure) {
    if (measure.acknowledged <= 0) {
      return 'No acknowledgements measured yet.';
    }
    var pct = Math.round(100 * measure.proposed / measure.acknowledged);
    return (
      'AISCP had proposed the answer for ' +
      countLabel(measure.proposed) +
      ' of ' +
      countLabel(measure.acknowledged) +
      ' acknowledgements (' +
      countLabel(pct) +
      '%)'
    );
  }

  function renderExceptions(answer, measure) {
    if (!exceptionsAnswerValid(answer)) {
      removeExceptionsSection();
      return;
    }
    var section = ensureExceptionsSection();

    // The top rows: the unacknowledged ones, in the answer's order, up
    // to EXCEPTIONS_TOP_LIMIT.
    var topRows = [];
    for (var i = 0; i < answer.rows.length && topRows.length < EXCEPTIONS_TOP_LIMIT; i++) {
      var row = answer.rows[i];
      if (!row || typeof row !== 'object' || row.acknowledged) {
        continue;
      }
      topRows.push(exceptionsRowText(row));
    }

    // Every API value is inserted as text: the DOM is built node by
    // node, nothing goes through innerHTML.
    while (section.firstChild) {
      section.removeChild(section.firstChild);
    }

    var title = document.createElement('h2');
    title.className = 'ov-exceptions-title';
    title.textContent = 'Exceptions today';
    section.appendChild(title);

    var countsLine = document.createElement('p');
    countsLine.className = 'ov-exceptions-counts';
    countsLine.textContent =
      countLabel(answer.counts.high) + ' high \u00b7 ' +
      countLabel(answer.counts.medium) + ' medium \u00b7 ' +
      countLabel(answer.counts.acknowledged) + ' acknowledged';
    section.appendChild(countsLine);

    // The "new today" line: directly after the counts line, only when
    // the answer's counts.new is a number above 0 — a missing or
    // non-numeric field renders nothing.
    if (isMoney(answer.counts.new) && answer.counts.new > 0) {
      var newLine = document.createElement('p');
      newLine.className = 'ov-exceptions-new';
      newLine.textContent = countLabel(answer.counts.new) + ' new today';
      section.appendChild(newLine);
    }

    if (topRows.length) {
      var list = document.createElement('ul');
      list.className = 'ov-exceptions-top';
      for (var j = 0; j < topRows.length; j++) {
        var item = document.createElement('li');
        item.textContent = topRows[j];
        list.appendChild(item);
      }
      section.appendChild(list);
    } else {
      var none = document.createElement('p');
      none.className = 'ov-exceptions-none';
      none.textContent = 'Nothing waiting.';
      section.appendChild(none);
    }

    // The measure line, directly before the link: the card's own
    // read's answer, set as text like the rest of the card. A
    // failed read — a null catch or a not-usable body — leaves
    // the line out; the card renders exactly as without it.
    if (exceptionsMeasureValid(measure)) {
      var measureLine = document.createElement('p');
      measureLine.className = 'ov-exceptions-measure';
      measureLine.setAttribute('data-testid', 'ov-exceptions-measure');
      measureLine.textContent = exceptionsMeasureText(measure);
      section.appendChild(measureLine);
    }

    var open = document.createElement('a');
    open.className = 'ov-exceptions-open';
    open.setAttribute('href', '#/exceptions');
    open.textContent = 'Open the inbox';
    section.appendChild(open);

    var sourcesFailed = Array.isArray(answer.sources_failed) ? answer.sources_failed : [];
    if (sourcesFailed.length) {
      var partial = document.createElement('p');
      partial.className = 'ov-exceptions-partial';
      partial.textContent = 'Some signals could not be read.';
      section.appendChild(partial);
    }
  }

  /* --------------------------- supply this week ------------------------- */

  // The "Supply this week" card is a load region like the "Exceptions
  // today" card, with the same silent failure: a failed read — any
  // status, a network error, or a body without numeric line_count,
  // supplier_count and transfer_count — removes the section from the
  // DOM. No error text, no Retry and no skeleton survive; the next
  // load recreates the section after the "Exceptions today" card, or
  // where that card would stand. The whole-dollar figures come from
  // the answer's total_value and transfer_value (a number or its
  // numeric string), and every API value is set as text.

  var SUPPLY_REGION_ID = 'ov-region-supply';

  function ensureSupplySection() {
    var existing = document.getElementById(SUPPLY_REGION_ID);
    if (existing) {
      return existing;
    }
    var section = document.createElement('section');
    section.id = SUPPLY_REGION_ID;
    section.className = 'card ov-supply';
    section.setAttribute('aria-label', 'Supply this week');
    // After the "Exceptions today" card when it is in the DOM,
    // otherwise where that card would stand: after the "Value by
    // class" card, after the Released orders card, or before the
    // attention region.
    var exceptions = document.querySelector('.ov-exceptions');
    var anchor = exceptions && exceptions.parentNode ? exceptions : null;
    if (!anchor) {
      var abc = document.querySelector('.ov-abc');
      anchor = abc && abc.parentNode ? abc : null;
    }
    if (!anchor) {
      var releases = document.getElementById('ov-region-releases');
      anchor = releases && releases.parentNode ? releases : null;
    }
    if (anchor) {
      anchor.insertAdjacentElement('afterend', section);
    } else {
      var container = document.querySelector('#view-overview .ov');
      if (container) {
        var attention = container.querySelector('.ov-attention');
        if (attention) {
          attention.insertAdjacentElement('beforebegin', section);
        } else {
          container.appendChild(section);
        }
      }
    }
    regionEls[SUPPLY_REGION_ID] = section;
    return section;
  }

  function removeSupplySection() {
    var section = regionEls[SUPPLY_REGION_ID];
    regionEls[SUPPLY_REGION_ID] = null;
    if (section && section.parentNode) {
      section.parentNode.removeChild(section);
    }
  }

  // The answer is usable only with numeric line_count, supplier_count
  // and transfer_count; anything else fails the region.
  function supplyAnswerValid(answer) {
    return (
      !!answer && typeof answer === 'object' && !Array.isArray(answer) &&
      isMoney(answer.line_count) &&
      isMoney(answer.supplier_count) &&
      isMoney(answer.transfer_count)
    );
  }

  // The card's whole-dollar figure: the answer carries the money as a
  // number or its numeric string, and anything else is without a
  // figure (moneyLabel then renders the em dash).
  function supplyMoney(value) {
    if (typeof value === 'number') {
      return isFinite(value) ? value : null;
    }
    if (typeof value === 'string') {
      var text = value.trim();
      if (text === '') {
        return null;
      }
      var parsed = Number(text);
      return isFinite(parsed) ? parsed : null;
    }
    return null;
  }

  function renderSupply(answer) {
    if (!supplyAnswerValid(answer)) {
      removeSupplySection();
      return;
    }
    var section = ensureSupplySection();

    // Every API value is set as text: the DOM is built node by node,
    // nothing goes through innerHTML.
    while (section.firstChild) {
      section.removeChild(section.firstChild);
    }

    var title = document.createElement('h2');
    title.className = 'ov-supply-title';
    title.textContent = 'Supply this week';
    section.appendChild(title);

    // "<n> lines · $<value> across <n> suppliers", with "1 line"
    // and "1 supplier" when a count is exactly 1.
    var ordersLine = document.createElement('p');
    ordersLine.className = 'ov-supply-orders';
    ordersLine.textContent =
      countLabel(answer.line_count) +
      (answer.line_count === 1 ? ' line' : ' lines') +
      ' · ' +
      moneyLabel(supplyMoney(answer.total_value)) +
      ' across ' +
      countLabel(answer.supplier_count) +
      (answer.supplier_count === 1 ? ' supplier' : ' suppliers');
    section.appendChild(ordersLine);

    // "<n> transfers could cover $<value>", "1 transfer could cover
    // $<value>" for exactly one, "No transfers suggested" without any.
    var transfersLine = document.createElement('p');
    transfersLine.className = 'ov-supply-transfers';
    if (answer.transfer_count > 0) {
      transfersLine.textContent =
        countLabel(answer.transfer_count) +
        (answer.transfer_count === 1 ? ' transfer could cover' : ' transfers could cover') +
        ' ' +
        moneyLabel(supplyMoney(answer.transfer_value));
    } else {
      transfersLine.textContent = 'No transfers suggested';
    }
    section.appendChild(transfersLine);

    // The in-flight line: only while the answer carries transfers in
    // flight — a missing or non-numeric count renders nothing.
    var inFlight = isMoney(answer.transfers_in_flight) ? answer.transfers_in_flight : 0;
    if (inFlight > 0) {
      var inflightLine = document.createElement('p');
      inflightLine.className = 'ov-supply-inflight';
      inflightLine.textContent =
        countLabel(inFlight) +
        (inFlight === 1 ? ' transfer in flight' : ' transfers in flight') +
        ' · ' +
        countLabel(answer.transfers_awaiting_release) +
        ' awaiting release';
      section.appendChild(inflightLine);
    }

    var open = document.createElement('a');
    open.className = 'ov-supply-open';
    open.setAttribute('href', '#/orders');
    open.textContent = 'Open the order plan';
    section.appendChild(open);
  }

  /* ------------------------ forecast overrides ------------------------ */

  // The "Forecast overrides" card is a load region like the "Supply
  // this week" card, with the same silent failure: a failed read —
  // any status, a network error, or a body that is not an object
  // whose overrides, proposed, taken, same_direction and
  // without_proposal hold the finite numbers with taken and
  // same_direction each no greater than proposed and proposed no
  // greater than overrides — removes the section from the DOM. No
  // error text, no Retry and no skeleton survive; the next load
  // recreates the section after the "Supply this week" card, or,
  // where that card would stand, using the Supply card's own
  // anchors in order: after the "Exceptions today" card, after the
  // "Value by class" card, after the Released orders card, or before
  // the attention region. Every number goes through countLabel, the
  // words stay the same for a count of 1, and every API value is
  // set as text.

  var OVERRIDES_REGION_ID = 'ov-region-overrides';

  function ensureOverridesSection() {
    var existing = document.getElementById(OVERRIDES_REGION_ID);
    if (existing) {
      return existing;
    }
    var section = document.createElement('section');
    section.id = OVERRIDES_REGION_ID;
    section.className = 'card ov-overrides';
    section.setAttribute('aria-label', 'Forecast overrides');
    // After the "Supply this week" card when it is in the DOM,
    // otherwise where that card would stand: after the "Exceptions
    // today" card, after the "Value by class" card, after the
    // Released orders card, or before the attention region.
    var supply = document.querySelector('.ov-supply');
    var anchor = supply && supply.parentNode ? supply : null;
    if (!anchor) {
      var exceptions = document.querySelector('.ov-exceptions');
      anchor = exceptions && exceptions.parentNode ? exceptions : null;
    }
    if (!anchor) {
      var abc = document.querySelector('.ov-abc');
      anchor = abc && abc.parentNode ? abc : null;
    }
    if (!anchor) {
      var releases = document.getElementById('ov-region-releases');
      anchor = releases && releases.parentNode ? releases : null;
    }
    if (anchor) {
      anchor.insertAdjacentElement('afterend', section);
    } else {
      var container = document.querySelector('#view-overview .ov');
      if (container) {
        var attention = container.querySelector('.ov-attention');
        if (attention) {
          attention.insertAdjacentElement('beforebegin', section);
        } else {
          container.appendChild(section);
        }
      }
    }
    regionEls[OVERRIDES_REGION_ID] = section;
    return section;
  }

  function removeOverridesSection() {
    var section = regionEls[OVERRIDES_REGION_ID];
    regionEls[OVERRIDES_REGION_ID] = null;
    if (section && section.parentNode) {
      section.parentNode.removeChild(section);
    }
  }

  // The answer is usable only with the finite numbers overrides,
  // proposed, taken, same_direction and without_proposal, taken and
  // same_direction each no greater than proposed and proposed no
  // greater than overrides; anything else fails the region.
  function overridesAnswerValid(answer) {
    if (!answer || typeof answer !== 'object' || Array.isArray(answer)) {
      return false;
    }
    return (
      isMoney(answer.overrides) &&
      isMoney(answer.proposed) &&
      isMoney(answer.taken) &&
      isMoney(answer.same_direction) &&
      isMoney(answer.without_proposal) &&
      answer.taken <= answer.proposed &&
      answer.same_direction <= answer.proposed &&
      answer.proposed <= answer.overrides
    );
  }

  function renderOverrides(answer) {
    if (!overridesAnswerValid(answer)) {
      removeOverridesSection();
      return;
    }
    var section = ensureOverridesSection();

    // Every API value is set as text: the DOM is built node by node,
    // nothing goes through innerHTML.
    while (section.firstChild) {
      section.removeChild(section.firstChild);
    }

    var title = document.createElement('h2');
    title.className = 'ov-overrides-title';
    title.textContent = 'Forecast overrides';
    section.appendChild(title);

    // "Proposed quantity taken in <taken> of <proposed> overrides
    // (<pct>%)" while proposed is above 0 — pct the rounded share of
    // the proposed overrides whose quantity was taken — "No proposed
    // corrections measured yet." when it is 0. The words stay the
    // same for a count of 1.
    var line = document.createElement('p');
    line.className = 'ov-overrides-line';
    line.setAttribute('data-testid', 'ov-overrides-line');
    if (answer.proposed > 0) {
      var pct = Math.round(100 * answer.taken / answer.proposed);
      line.textContent =
        'Proposed quantity taken in ' +
        countLabel(answer.taken) +
        ' of ' +
        countLabel(answer.proposed) +
        ' overrides (' +
        countLabel(pct) +
        '%)';
    } else {
      line.textContent = 'No proposed corrections measured yet.';
    }
    section.appendChild(line);

    // The direction line: only while proposed is above 0 — "Moved
    // the same way as the proposal in <same_direction> of
    // <proposed>".
    if (answer.proposed > 0) {
      var direction = document.createElement('p');
      direction.className = 'ov-overrides-direction';
      direction.setAttribute('data-testid', 'ov-overrides-direction');
      direction.textContent =
        'Moved the same way as the proposal in ' +
        countLabel(answer.same_direction) +
        ' of ' +
        countLabel(answer.proposed);
      section.appendChild(direction);
    }

    // The note: only while without_proposal is above 0 — "Without a
    // proposed correction: <without_proposal> of <overrides>".
    if (answer.without_proposal > 0) {
      var note = document.createElement('p');
      note.className = 'ov-overrides-note';
      note.setAttribute('data-testid', 'ov-overrides-note');
      note.textContent =
        'Without a proposed correction: ' +
        countLabel(answer.without_proposal) +
        ' of ' +
        countLabel(answer.overrides);
      section.appendChild(note);
    }

    var open = document.createElement('a');
    open.className = 'ov-overrides-open';
    open.setAttribute('href', '#/demand');
    open.setAttribute('data-testid', 'ov-overrides-open');
    open.textContent = 'Open Demand';
    section.appendChild(open);
  }

  /* -------------------------------- boot -------------------------------- */

  (function boot() {
    REGION_IDS.forEach(function (regionId) {
      var regionEl = document.getElementById(regionId);
      if (regionEl) {
        regionEls[regionId] = regionEl;
      }
    });
    // The "Value by class", "Exceptions today", "Supply this week"
    // and "Forecast overrides" cards are not in the markup: they are
    // created after the Released orders card (the supply card after
    // the exceptions card, where that card would stand when its read
    // failed; the overrides card after the supply card, where that
    // card would stand when its read failed) and again after any
    // failed read removed them (see ensureAbcSection /
    // removeAbcSection, ensureExceptionsSection / removeExceptionsSection,
    // ensureSupplySection / removeSupplySection,
    // ensureOverridesSection / removeOverridesSection).
    ensureAbcSection();
    ensureExceptionsSection();
    ensureSupplySection();
    ensureOverridesSection();

    var appEl = document.getElementById('app');
    if (!appEl) {
      return;
    }
    // The Overview loads once the signed-in shell is visible; the sign-in
    // flow (auth.js) shows #app, this observer starts the regions.
    var observer = new MutationObserver(function () {
      if (!appEl.hidden && overviewActive()) {
        activate();
      }
    });
    observer.observe(appEl, { attributes: true, attributeFilter: ['hidden'] });
    if (!appEl.hidden && overviewActive()) {
      activate();
    }
  })();

  global.Overview = {
    activate: activate,
    invalidate: invalidate,
    reset: reset,
  };
})(window);
