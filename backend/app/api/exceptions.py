"""Exceptions: the planner's triage list of what needs attention.

``GET /api/exceptions`` (any signed-in user; the router is mounted
behind the app's authentication gate) gathers one row per exception
from sixteen sources, each read once per call (the releases
source reads its ``plan_releases`` rows and the newest open-purchase
snapshot, once each, the transfers source its newest
``transfer_releases`` rows and its in-flight rows, once each, and
the sop source its newest ``sop_signoffs`` row and the newest
row of the current review month, once each):

* the DDMRP buffers — the rows of the plan the DDMRP plan route
  builds, through the very same shared code (``ddmrp_zone_rows`` and
  ``plan_ddmrp``), not a copy: ``on_hand_status`` "stocked_out" is
  kind "buffer_stocked_out" (severity high) and "critical" is kind
  "buffer_critical" (severity medium); the ref is the item/site and
  the detail reads "on hand <n> (<pct>% of top of red)" (the
percentage part only when the plan reports one);
* the stockout risk — the rows of
  ``app.recommend.stockout.stockout_ranked``: category "likely" is
  kind "stockout_likely" (high) and "at_risk" is kind
  "stockout_at_risk" (medium); the ref is the item/site and the
  detail reads "projected stockout <date>, <p> % chance" with
  ``p_stockout`` as a whole percent (the chance part only when the
  source reports one);
* the expedite lines — the lines of
  ``app.recommend.expedite.expedite_list``: a line whose ``reasons``
  include "after_stockout" is kind "expedite_after_stockout" (high),
  otherwise a line with "late" is kind "expedite_late" (medium); the
  ref is "<order_nbr>/<line_nbr>" and the detail reads "<open_units>
  units, <days_late> days late" (the days part only when set);
* the releases — the ``plan_releases`` rows, read once
  alongside the newest open-purchase snapshot (a failure of either
  read fails the source, listed "releases" like today): the rows
  with outcome "error" or "aborted" whose ``updated_at`` is within
  the last 14 days are kind "release_failed" (high), the ref is the
  row id, the detail reads "release to <vendor_code or
  supplier_code> failed" (never the stored error text); the
  in-flight rows — the very rule
  ``app.aggregate.projection._in_flight_supply_lines`` applies:
  outcome "created" or "exists", a non-null ``order_nbr`` and that
  order number not among the newest open-purchase snapshot's feed
  order numbers (``projection._feed_order_numbers``) — whose
  ``updated_at`` is at least the
  ``exceptions.release_approval_days`` system setting (default 3)
  days before the current UTC time are kind
  "release_awaiting_approval" (medium), the ref is the row id, the
  detail reads "<order_nbr> to <vendor_code, else supplier_code,
  else "an unknown supplier"> awaiting approval in the ERP for
  <n> days" ("1 day" at exactly one whole day; n the whole days
  since ``updated_at``); the setting is read once per call — a
  missing or out-of-range stored value falls back to the registry
  default;
* the transfers — the ``transfer_releases`` rows, read once per
  call: the newest rows, through
  ``TransferReleasePostgresRepository.list_newest(limit=200)`` —
  the rows with outcome "error" or "aborted" whose ``updated_at``
  is within the last 14 days are kind "transfer_failed" (high),
  the ref is the row id, the detail reads "transfer
  <from_site_code> → <to_site_code> failed" (never the stored
  error text) — and the in-flight rows, through
  ``TransferReleasePostgresRepository.list_in_flight()`` — the
  rows whose ``erp_status`` is null or none of "Released",
  "Completed" and "Canceled" and whose ``updated_at`` is at least
  the ``exceptions.release_approval_days`` system setting (default
  3) days before the current UTC time (inclusive of the exact
  cutoff) are kind "transfer_awaiting_release" (medium), the ref
  is the row id, the detail reads "transfer <reference_nbr>
  <from_site_code> → <to_site_code> awaiting release in the ERP
  for <n> days" ("1 day" at exactly one whole day; n the whole
  days since ``updated_at``), ``detected_on`` the row's date (a
  failure of either read fails the source, listed "transfers";
  the setting is read once per call, a missing or out-of-range
  stored value falling back to the registry default, as for the
  release-awaiting-approval rows);
* the failed ERP pushes — the ``parameter_writebacks`` rows with
  outcome "failed" whose ``written_at`` is within the last 14 days:
  kind "erp_push_failed" (high), the ref is the row id, the detail
  reads "push to <source_system> failed" (never the stored error);
* the forecast bias — the item-sites of
  ``app.forecast.accuracy.realised_accuracy``, read with its
  default as-of, with ``bucket_index=1`` (the forecast one period
  ahead) and ``latest_periods`` the
  ``exceptions.forecast_bias_periods`` system setting (default 6) —
  each item-site's latest forecast periods, each forecast one period
  ahead: an item-site whose ``compared_buckets`` — its number of
  distinct measured periods, at most the setting's value — is at
  least the ``exceptions.forecast_bias_min_periods`` system setting
  (default 3) and whose ``effective_bias`` is a finite number of
  absolute value at least the
  ``exceptions.forecast_bias_threshold`` system setting (default
  0.5) is kind "forecast_bias" (medium); the three
  settings are read once per call — a missing or out-of-range stored
  value falls back to the registry default, and a settings read
  failure fails the source like any other forecast failure — the ref
  is the item/site and the detail reads "forecast <over|under>
  actual by <p>% across <n> periods" (a positive bias is
  "over", <p> the absolute bias as a whole percent, <n> the
  item-site's compared buckets); the accuracy's as-of is the
  row's ``detected_on``, never the response's ``as_of``.
* the S&OP sign-off — the ``sop_signoffs`` rows, read once per
  call: the newest row of any month, through
  ``SopSignoffsPostgresRepository.list_newest(limit=1)``, and the
  newest row of the current month — the "YYYY-MM" of the current
  server date, the review month ``GET /api/sop/review`` uses —
  through ``SopSignoffsPostgresRepository.latest_for_month``.  One
  row, kind "sop_signoff_due" (medium), the key
  "sop_signoff_due:<month>", ``item_code`` and ``site_code`` null,
  title "S&OP sign-off due", the detail "<month> has not been
  signed off. The last sign-off was for <the newest row's month>
  on <the date of its created_at>.", view "#/sop" and
  ``detected_on`` the date in the current month whose day is the
  first whole day at or above the ``exceptions.sop_signoff_day``
  system setting (default 10), is
  yielded only when all of these hold: at least one sign-off of any
  month exists, the current month has none, and the current server
  date's day of the month is at least the setting.  The setting is
  read once per call from the stored system settings — a missing or
  out-of-range stored value falls back to the registry default, as
  the other settings do (a failure of either read fails the source,
  listed "sop").
* the code clashes — the ``erp_code_clashes`` rows, read once per
  call, through
  ``ErpCodeClashPostgresRepository.list_open(limit=200)``: the rows
  whose ``resolved_at`` is still null, the most recently seen
  first, then by ``id``, in one SQL statement (a failure of the
  read fails the source, listed "clashes").  One row per open
  clash, kind "erp_code_clash" (high), the key
  "erp_code_clash:<refused_source>:<item_code>/<site_code>", the
  clash's ``item_code`` and ``site_code``, title "Same codes in two
  ERPs", the detail "<item_code> at <site_code> is held by
  <held_by>, so <refused_source> rows for it are not loaded.", view
  "#/settings" and ``detected_on`` the date of ``first_seen_at``.
* the currencies — the ``erp_source_currencies`` rows, read once per
  call, through ``ErpSourceCurrencyPostgresRepository.list_all()``
  (a failure of the read fails the source, listed "currencies"): one
  row per stored code that differs from the
  ``erp.reporting_currency`` system setting (default USD), ignoring
  case — kind "erp_currency_mismatch" (high), the key
  "erp_currency_mismatch:<source_system>:<currency_code>",
  ``item_code`` and ``site_code`` null, title "ERP reports in
  another currency", the detail "<source_system> reports in
  <currency_code>, but values are added up as <setting>. AIERP does
  not convert currencies.", view "#/settings" and ``detected_on``
  the date of ``seen_at``.  The setting is read from the stored
  system settings — a missing stored value falls back to the
  registry default, as the other settings do.
* the cross-ERP opportunities — the stored item mappings, read once
  per call, through
  ``ErpCodeMappingPostgresRepository.list(kind="item")``: only when
  at least one is stored, the rows of
  ``app.recommend.cross_erp.cross_erp_items`` (called once per
  call) — one row per answer row whose ``opportunity`` is true,
  kind "cross_erp_opportunity" (medium), the key
  "cross_erp_opportunity:<source_code>:<target_code>", the short
  side's ``item_code``, ``site_code`` null, title "Short in one
  ERP, excess in the other", the detail "<short source> is short of
  <short code>, while <excess source> holds excess of <excess
  code>. AIERP does not move stock between ERPs." (each source the
  side's ``source_system`` — "Acumatica" for ``acumatica`` and
  "Odoo" for ``odoo``, compared ignoring case, any other non-empty
  string as stored, or "an ERP" when it is null or empty — with the
  "Up to <N> units could move." sentence before the last one when
  the comparison row's ``suggested_move_units`` is an integer above
  0, N with thousands separators), view "#/across" and
  ``detected_on`` the answer's ``as_of`` (with no
  mapping stored nothing is called and no row is added, and a
  failure of the mapping read or of the comparison fails the
  source, listed "across").
* the high-risk suppliers — the entries of
  ``app.recommend.supplier_risk.supplier_risk`` (called once per
  call): one row per entry whose ``risk`` is "high", kind
  "supplier_risk_high" (medium), the key
  "supplier_risk_high:<supplier_code>", ``item_code`` and
  ``site_code`` null, title "High-risk supplier", the detail
  "<name> is the only supplier of <N> item(s) and <reasons>." (the
  name the entry's ``supplier_name``, else its ``supplier_code``,
  N the entry's ``sole_source_items``, written "item" when it is
  1, and the reasons "delivers late" when the entry is ``late``
  and "has variable lead times" when it is ``variable``, joined by
  " and " when both), view "#/suppliers" and ``detected_on`` the
  answer's ``as_of`` (a failure of the rating fails the source,
  listed "supplier_risk").
* the second sources — the entries of
  ``app.recommend.second_source.second_sources`` (called once per
  call): one row per entry whose ``supplier_risk`` is "high",
  whose ``candidates`` hold no candidate with a ``basis`` of
  "terms" and whose ``bridge_short_units`` is a number above 0,
  kind "sole_source_short" (severity "high" when the entry has no
  candidates at all and "medium" otherwise), the key
  "sole_source_short:<item_code>", the entry's ``item_code`` and
  ``site_code`` null, title "Sole source, short of stock", the
  detail "<item_code> comes only from <name>, rated high risk,
  and no other supplier has terms for it. Holding <bridge_units>
  units would cover <bridge_days> days; on hand is short by
  <bridge_short_units>." (the name the entry's ``supplier_name``,
  else its ``supplier_code``) plus " Suppliers of similar items to
  approach: <N>." when the entry has N candidates, or " No supplier
  of similar items found." when it has none, view "#/suppliers"
  and ``detected_on`` the answer's ``as_of`` (a failure of the
  candidates read fails the source, listed "second_sources").
* the order minimums — the supplier groups of
  ``app.api.supply_plan.build_supply_plan(7)`` (the 7-day horizon
  ``GET /api/supply-plan`` and the Order plan screen default to,
  built once per call only when the stored supplier order
  minimums — read once per call, through a
  ``SupplierOrderMinimumPostgresRepository`` — hold at least one
  row): one row per group that carries a ``minimum`` and a
  ``shortfall`` above 0 and holds a line whose ``due_in_days`` is
  at most the ``exceptions.order_minimum_due_days`` system setting
  (default 2, applied with ``setting_value`` on the stored system
  settings the list already reads once per call, so a missing or
  out-of-range stored value, or a failed settings read, reads 2),
  kind "order_below_minimum" (severity "high" when one of the
  group's lines has a ``due_in_days`` of 0, else "medium"), the key
  "order_below_minimum:<source_system>:<supplier_code>",
  ``item_code`` and ``site_code`` null, title "Order below supplier
  minimum", the detail "The order to <name> comes to
  <total_value>, <shortfall> below its minimum of <minimum>;
  <item_code> at <site_code> must be ordered by <order_by>." (the
  name the group's ``name``, else its ``supplier_code``; the line
  named the group's first — its lines are ordered by
  ``due_in_days``, then item, then site — the amounts dollars and
  cents with thousands separators, ``order_by`` read
  ``YYYY-MM-DD``), view "#/orders" and ``detected_on`` the plan's
  ``as_of`` (the plan's ``as_of`` is the rows' ``detected_on``
  only, never the response's ``as_of``; a group without a
  supplier has no minimum, so it never qualifies, and a failure of
  the minimums read or of the plan build fails the source, listed
  "order_minimums").
* the standing grants — the grants in force, once per call for
  each of ``order_plan_release`` and ``transfer_release``, through
  a ``ErpStandingGrantPostgresRepository``, and each process's
  latest standing-grant run, once per call, through a
  ``StandingGrantRunPostgresRepository`` (a failure of either read
  fails the source, listed "standing_grants"): with the
  ``erp.standing_grants_enabled`` switch from the stored system
  settings the list already reads once per call stored false it
  raises nothing (a missing value, or one that is not a boolean,
  counts as on); a latest run under the grant in force that
  failed, that skipped, or that released with a ``left_out`` that
  is not empty is one row, and a grant whose time — the later of
  the grant's ``created_at`` and the latest run's ``started_at``
  under the grant, or ``created_at`` alone while the process has
  no run under the grant — is more than
  ``STANDING_GRANT_IDLE_HOURS`` (26 — one daily sync plus two
  hours) hours before the current UTC time (the current time is
  read once per call, through the module's ``_utcnow()``) is one
  idle row, whatever the latest run's outcome: kind
  "standing_grant_run" (severity "high" for a failed run and for
  the idle row, else "medium"), the key "standing_grant_run:<run
  id>" (the idle row's key "standing_grant_run:idle:<grant id>:<
  the latest run id under the grant, or 0>"), ``item_code`` and
  ``site_code`` null, title "Standing grant run needs
  attention", the detail "The standing grant for <the order plan,
  for transfers> failed at <time>: <reason>." for a failed run,
  "...sent nothing at <time>: <reason>." for a skipped one,
  "...sent <documents> document(s) at <time> and held <n> back
  over its limits." for a held-back one (<n> the number of
  ``left_out`` entries) and "...has not run since <time>." for the
  idle row (<time> the run's ``started_at`` or the grant's time,
  in UTC as ``YYYY-MM-DD HH:MM UTC``), view "#/releases" and
  ``detected_on`` the run's UTC date (the idle row's the grant's
  time plus 26 hours) — neither ever the response's ``as_of``.
* the demand basis switch — the one call of
  ``app.forecast.accuracy.basis_accuracy`` per call, with
  ``bucket_index=1`` (the demand basis one period ahead, the
  horizon the reorder points compare): one row, kind
  "demand_basis_switch" (medium), the key
  "demand_basis_switch:<the proposed basis>", ``item_code`` and
  ``site_code`` null, title "Demand basis to review", the detail
  "One period ahead over <N> periods, the <proposed basis> was
  <Z> points nearer what sold than the <basis in use>, which
  reorder points use. An admin may switch the demand basis to the
  <proposed basis> in Settings." (N the measure's compared
  buckets, with thousands separators, Z the absolute
  ``value_added`` times 100, to one decimal), view "#/settings"
  and ``detected_on`` the measure's ``as_of`` — never the
  response's ``as_of`` — yielded only when the measure's
  ``compared_buckets`` is at least the
  ``exceptions.basis_switch_min_periods`` system setting (default
  60) and the demand basis not in use was nearer what sold, one
  period ahead, by at least the
  ``exceptions.basis_switch_margin`` system setting (default 5)
  points: the in-use basis ``history`` with ``value_added`` times
  100 at least the margin proposes ``forecast``, the in-use basis
  ``forecast`` with minus ``value_added`` times 100 at least the
  margin proposes ``history``, a null ``value_added`` proposes
  neither.  The effective ``replenishment.demand_basis`` and the
  two settings come from the stored system settings the list
  already reads once per call (the two settings through
  ``setting_value`` — a missing or out-of-range stored value
  falls back to the registry default, as the other settings do —
  the basis the way the reorder point reads it, never through
  ``setting_value`` and never stored by the route; while the
  settings read itself failed the source runs off the registry
  defaults, never failing on that account).

The 14-day window is a UTC timestamp cutoff: a failure row is
included when its timestamp is at least the current UTC time minus
14 days (inclusive of the exact cutoff) — never calendar days, never
the instance time zone.

Each row carries ``kind``, ``severity`` ("high" or "medium"), a
stable ``key`` (``<kind>:<ref>``), ``item_code`` and ``site_code``
(null when the exception is not item-specific), ``title``,
``detail``, ``view`` (the planner hash that shows it) and
``detected_on`` (the as-of date the source reports, or the
row's date for the three failure kinds and the two awaiting
kinds, release awaiting approval and transfer awaiting
release, the S&OP sign-off due's the date in the current month
whose day is the setting, the code clash's the date of its
``first_seen_at``, the currency mismatch's the date of its
``seen_at``, the cross-ERP opportunity's the comparison's
``as_of``, the supplier risk high's the rating's ``as_of``, the
standing grant run's the run's UTC date and the idle row's the
grant's time plus 26 hours, the demand basis switch's the
measure's ``as_of``).
  The rows are ordered high before
medium, then kind order (buffer_stocked_out, buffer_critical,
stockout_likely, stockout_at_risk, expedite_after_stockout,
expedite_late, forecast_bias, release_awaiting_approval,
transfer_awaiting_release, release_failed, transfer_failed,
erp_push_failed, sop_signoff_due, erp_code_clash,
erp_currency_mismatch, cross_erp_opportunity,
supplier_risk_high, sole_source_short,
order_below_minimum, standing_grant_run,
demand_basis_switch), then item_code
then key.

The response is ``{"as_of", "counts": {"high", "medium",
"acknowledged", "new"}, "rows", "sources_failed"}``: ``as_of`` is the
latest as-of date reported by any successful source (null when none
reports one) — the DDMRP plan's, the stockout rows' and the
expedite lines' as-ofs (the forecast bias, the three failure
kinds, the two awaiting kinds, the S&OP sign-off due, the code
clash, the currency mismatch, the cross-ERP opportunity,
the supplier risk high, the sole source short,
the order below minimum and the demand basis switch report none,
their dates are the rows'
``detected_on``),
and a source that raises is caught — its name ("ddmrp",
"stockout", "expedite", "releases", "transfers", "pushes",
"forecast", "sop", "clashes", "currencies", "supplier_risk",
"second_sources", "order_minimums",
"standing_grants", "basis" or "across") is listed
in ``sources_failed`` and the other sources still answer, with
status 200.  The counts cover only the rows returned.  Nothing is
written and no ERP client is called.

The DDMRP plan, the stockout ranking and the expedite list read their
``app.recommend.reorder.recommendations_for_all`` through one
``recommendations_memo`` per request (the stockout ranking is given the
plan's resolved as-of when it is the ranking's own default,
``app.stats.demand.default_as_of``; the expedite list keeps its own
default, today): the request never computes the recommendations more
than once per as-of.  And the gathering runs inside the one
``app.db.read_cache.read_once`` block and the one
``app.db.connection.one_connection()`` block the request carries:
every read the sources share — the newest open-purchase snapshot
(``OpenPurchaseLinePostgresRepository.list_latest``), the demand
statistics (``demand_stats_for_all``) and the 30-day history buckets
(``history_for_all``) the recommendations load, the ranked stockout
rows (``stockout_ranked``), the supplier performance
(``supplier_performance``) and the lead-time observations
(``LeadTimeObservationPostgresRepository.list_rows``) — answers each
repeated call with the same arguments from the first call's result,
deep-copied, so one request reads each of them once, whatever the
number of sources that read it, and every ``get_cursor()`` of the
request runs on the one connection of the block.  The answer — the
as-of date, the rows and their order, and ``sources_failed`` — is the
same as without the memo and the blocks.

Every row also carries its first-seen date from the
``exception_first_seen`` table, read once per call and never written
by the route: ``first_seen_on`` (the date the row's key was first
captured, or null when the key has never been captured) and ``new``
(true only when ``first_seen_on`` equals the response's ``as_of``),
and ``counts`` carries ``new``, the number of returned rows whose
``new`` is true.  A failure reading the table is caught: every row
then reads ``first_seen_on`` null and ``new`` false, and the route
still answers 200.

``capture_exception_first_seen`` (the capture job, not a route)
builds the current exceptions list through the very same code the
GET route uses, snoozed rows included, and stores the list's
``as_of`` (or the current UTC date when the list reports none) as
the first-seen date of every row's key that has none yet: a key that
already has a date is left untouched, so a capture the same day
inserts nothing.  It returns ``{"captured_on", "inserted"}``.

Every row also carries the stored acknowledgement of its key, when
there is one: ``acknowledged`` (true) plus ``acknowledged_by_name``,
``acknowledged_at`` and ``snooze_until`` — all null or false while
the key is unacknowledged.  A row snoozed until a date after the
as-of date is left out of the rows and the counts unless the request
passes ``include_snoozed=true``, and acknowledgements whose key no
longer appears in the list are simply ignored.

``GET /api/exceptions`` takes ``include_proposal``, a boolean query
parameter, false by default.  While it is false the answer is
exactly that, key for key: no row carries a ``proposal`` key and the
stockout decisions are not read.  While it is true and at least one
row is returned, the route reads every current stockout decision
once — the latest of each item-site by ``decided_at``, ``id``
breaking ties, the decision the Work queue shows on
``GET /api/stockout`` — through
``_stockout_decisions_repo.current_all()`` (the read is never a
source: it adds no row and is never listed in
``sources_failed``), and every returned row carries ``proposal``, an
``ExceptionProposal`` — ``record_kind`` ``stockout_decision``, the
only record kind of this slice, ``record_id`` the decision's id,
``decision`` and ``action`` the decision's own, ``recorded_at`` its
``decided_at`` in UTC, ``recorded_by_name`` its ``decided_by_name``
(null when it has none) and ``reason`` the one line of text for why
the decision answers the row — or null when nothing answers the
row.  The rule runs on each returned row, after the snoozed rows are
left out and each row's acknowledgement and ``first_seen_on`` are
attached: a row of kind ``stockout_likely``, ``stockout_at_risk``,
``buffer_stocked_out`` or ``buffer_critical`` is answered when its
item-site's current decision is ``accepted`` with an action of
``expedite``, ``raise_order`` or ``transfer``, a row of kind
``expedite_late`` or ``expedite_after_stockout`` only when the
decision is ``accepted`` with the action ``expedite``, and the
decision must be recorded on or after the day the row was first
seen — the UTC date of its ``decided_at`` (read through the module's
``_aware_utc``, a value with no time zone taken as UTC) on or after
the row's ``first_seen_on``.  A row with a null ``item_code`` or
``site_code``, a null ``first_seen_on``, a kind no rule answers, an
item-site with no current decision, a dismissed or snoozed decision,
an action that does not answer the row's kind or is none of the
three, a decision recorded before the first-seen day, or a decision
whose ``decided_at`` carries no time has no proposal, and the row's
own acknowledgement is looked at not at all: an acknowledged row
carries its proposal like any other row.  With no row returned the
decisions are not read, and a read that raises is caught: every
row's ``proposal`` is then null and the route still answers 200 with
the rows, the counts and the ``sources_failed`` it answers without
the parameter.  Nothing is written and no ERP client is called.

``PUT /api/exceptions/ack`` (admin only — the same
``require_role("admin")`` the planner decisions use) takes
``{"key", "snooze_until"?, "note"?}`` and stores or replaces the
acknowledgement in the ``exception_acks`` table (one row per key,
the primary key): ``acknowledged_by`` is the token subject and
``acknowledged_by_name`` its display name, ``acknowledged_at`` is
``now()`` on store or replace, and an omitted ``snooze_until`` or
``note`` clears the stored value.  It answers 200 with the stored
row, 422 for an empty key, a NUL byte, a key that would not fit the
stored column or a ``snooze_until`` in the past (against the current
UTC date at request time, never the response as-of date), and 404
when the key is not in the current exceptions list.
``DELETE /api/exceptions/ack?key=…`` (admin only) removes the stored
acknowledgement: 204, or 404 when no acknowledgement is stored for
the key.  A non-admin write answers 403 and no token 401.

Every accepted PUT also appends one row to the append-only
``exception_ack_events`` table (one row per stored acknowledgement,
never updated or deleted — a cleared acknowledgement keeps its
rows): the stored acknowledgement's key, subject, name, time and
snooze, the acknowledged row's kind, item and site, and the proposal
``GET /api/exceptions?include_snoozed=true&include_proposal=true``
answers for that row at that moment — the first-seen dates read once
through ``_first_seen_rows()``, the current stockout decisions read
once through ``_current_stockout_decisions()`` and mapped by
``(item_code, site_code)`` exactly as the list maps them, and
``_row_proposal``, the very rule the list runs — stored as jsonb,
null when the rule answers None or a read fails.  Building or
storing the event never fails the acknowledgement: an exception
raised there is swallowed (a failed insert logs a warning with the
key, so the under-counting is visible in the logs) and the route
answers the stored row as today, and a request the route refuses
stores no event and reads neither the first-seen dates nor the
decisions.

``GET /api/exceptions/proposals`` (any signed-in user through the
router's gate, the route takes no dependency of its own) counts the
stored acknowledgement events and answers
``ExceptionProposalCounts``: ``acknowledged`` their number (every
event counts once, a snooze and a second acknowledgement of a key
included — acknowledgements stored before the events existed stored
no row and are not counted), ``proposed`` and ``without_proposal``
those whose proposal is set and those whose is null, ``snoozed`` and
``snoozed_proposed`` those whose snooze is set and those of them
whose proposal is set too, and ``by_kind`` one
``ExceptionProposalBucket`` — ``acknowledged`` that kind's events
and ``proposed`` those of them whose proposal is set — per kind that
has at least one stored event.  The route reads the store once,
through ``_exception_ack_events_repo.counts()`` (one SELECT, grouped
by kind, by the snooze and by the proposal); it writes nothing and
calls no ERP.
"""

from __future__ import annotations

import contextlib
import logging
import math
import numbers
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, PrivateAttr, model_serializer, model_validator

from app.aggregate import projection
from app.api.ddmrp import _stored_system_settings, ddmrp_zone_rows
from app.api.supply_plan import build_supply_plan
from app.auth.authorization import require_role
from app.auth.models import AuthUser
from app.db.connection import get_cursor, one_connection
from app.db.read_cache import read_once
from app.ddmrp.plan import plan_ddmrp
from app.ddmrp.zones import setting_value
from app.forecast.accuracy import basis_accuracy, realised_accuracy
from app.recommend.cross_erp import cross_erp_items
from app.recommend.expedite import expedite_list
from app.recommend.reorder import recommendations_memo
from app.recommend.second_source import second_sources
from app.recommend.stockout import stockout_ranked
from app.recommend.supplier_risk import supplier_risk
from app.repositories.erp_code_clashes_postgres_repository import (
    ErpCodeClashPostgresRepository,
)
from app.repositories.erp_code_mappings_postgres_repository import (
    ErpCodeMappingPostgresRepository,
)
from app.repositories.erp_source_currencies_postgres_repository import (
    ErpSourceCurrencyPostgresRepository,
)
from app.repositories.erp_standing_grants_postgres_repository import (
    ErpStandingGrantPostgresRepository,
)
from app.repositories.exception_ack_events_postgres_repository import (
    ExceptionAckEventPostgresRepository,
)
from app.repositories.exception_acks_postgres_repository import (
    ExceptionAcksPostgresRepository,
)
from app.repositories.exception_first_seen_postgres_repository import (
    ExceptionFirstSeenPostgresRepository,
)
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.repositories.parameter_writebacks_postgres_repository import (
    ParameterWritebacksPostgresRepository,
)
from app.repositories.plan_releases_postgres_repository import (
    PlanReleasePostgresRepository,
)
from app.repositories.sop_signoffs_postgres_repository import (
    SopSignoffsPostgresRepository,
)
from app.repositories.standing_grant_runs_postgres_repository import (
    StandingGrantRunPostgresRepository,
)
from app.repositories.stockout_decisions_postgres_repository import (
    StockoutDecisionPostgresRepository,
)
from app.repositories.supplier_order_minimums_postgres_repository import (
    SupplierOrderMinimumPostgresRepository,
)
from app.repositories.transfer_releases_postgres_repository import (
    TransferReleasePostgresRepository,
)
from app.settings.registry import get_setting
from app.stats.demand import default_as_of

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/exceptions", tags=["exceptions"])

# The three failure kinds (release, transfer and push failures) are
# listed only
# while their row timestamp is within this many days of now.
FAILURE_WINDOW_DAYS = 14

# The one read of the parameter_writebacks audit per call: the
# newest rows, with a limit large enough that the 14-day failure
# window is never truncated by the pagination.
_PUSHS_READ_LIMIT = 10_000

# The one read of the transfer_releases audit per call: the newest
# rows, with a limit large enough that the 14-day failure window
# is never truncated by the pagination.
_TRANSFERS_READ_LIMIT = 200

# The system settings the forecast-bias measure and filter apply:
# how many of the latest forecast periods, each forecast one period
# ahead, the measure runs over, the absolute bias at or above which
# an item-site is an exception and the fewest compared forecast
# periods it needs first.  The demand-basis-switch settings: the
# fewest periods, one period ahead, the basis measure must compare
# before the inbox proposes switching the basis and the margin, in
# points nearer what sold, one period ahead, the basis not in use
# must have been; and the demand basis the reorder points are built
# on, the basis the switch reads its in-use basis from.  A missing
# or out-of-range stored value falls back to the registry default.
BASIS_SWITCH_MARGIN_KEY = "exceptions.basis_switch_margin"
BASIS_SWITCH_MIN_PERIODS_KEY = "exceptions.basis_switch_min_periods"
FORECAST_BIAS_PERIODS_KEY = "exceptions.forecast_bias_periods"
FORECAST_BIAS_THRESHOLD_KEY = "exceptions.forecast_bias_threshold"
FORECAST_BIAS_MIN_PERIODS_KEY = "exceptions.forecast_bias_min_periods"
ORDER_MINIMUM_DUE_DAYS_KEY = "exceptions.order_minimum_due_days"
RELEASE_APPROVAL_DAYS_KEY = "exceptions.release_approval_days"
SOP_SIGNOFF_DAY_KEY = "exceptions.sop_signoff_day"
REPORTING_CURRENCY_KEY = "erp.reporting_currency"
STANDING_GRANTS_ENABLED_KEY = "erp.standing_grants_enabled"
DEMAND_BASIS_KEY = "replenishment.demand_basis"

# One daily sync plus two hours: a standing grant whose time is
# more than this many hours before the current UTC time is idle.
STANDING_GRANT_IDLE_HOURS = 26

_plan_releases_repo = PlanReleasePostgresRepository()
_open_purchase_lines_repo = OpenPurchaseLinePostgresRepository()
_parameter_writebacks_repo = ParameterWritebacksPostgresRepository()
_transfer_releases_repo = TransferReleasePostgresRepository()
_supplier_order_minimum_repo = SupplierOrderMinimumPostgresRepository()
_sop_signoffs_repo = SopSignoffsPostgresRepository()
_exception_acks_repo = ExceptionAcksPostgresRepository()
_exception_ack_events_repo = ExceptionAckEventPostgresRepository()
_exception_first_seen_repo = ExceptionFirstSeenPostgresRepository()
_stockout_decisions_repo = StockoutDecisionPostgresRepository()
_erp_code_clashes_repo = ErpCodeClashPostgresRepository()
_standing_grants_repo = ErpStandingGrantPostgresRepository()
_standing_grant_runs_repo = StandingGrantRunPostgresRepository()
_erp_code_mappings_repo = ErpCodeMappingPostgresRepository()
_erp_source_currencies_repo = ErpSourceCurrencyPostgresRepository()

# The String(255) length of the stored key and actor columns:
# hostile values that would not fit answer 422 instead of a
# database error.
_ACK_MAX_LENGTH = 255

# The kind order after the severity (the source order, the
# forecast bias standing directly after the expedite lines,
# release awaiting approval directly after the forecast bias and
# transfer awaiting release directly after release awaiting
# approval — the three last among the medium kinds, the S&OP
# sign-off due directly after erp_push_failed, the code clash
# directly after the S&OP sign-off due, the currency mismatch
# directly after the code clash, the cross-ERP opportunity
# directly after the currency mismatch, the supplier risk high
# directly after the cross-ERP opportunity, the sole source
# short directly after the supplier risk high, the order
# below minimum directly after the sole source short, the
# standing grant run directly after the order below minimum
# and the demand basis switch the last kind).
_KIND_ORDER = {
    "buffer_stocked_out": 0,
    "buffer_critical": 1,
    "stockout_likely": 2,
    "stockout_at_risk": 3,
    "expedite_after_stockout": 4,
    "expedite_late": 5,
    "forecast_bias": 6,
    "release_awaiting_approval": 7,
    "transfer_awaiting_release": 8,
    "release_failed": 9,
    "transfer_failed": 10,
    "erp_push_failed": 11,
    "sop_signoff_due": 12,
    "erp_code_clash": 13,
    "erp_currency_mismatch": 14,
    "cross_erp_opportunity": 15,
    "supplier_risk_high": 16,
    "sole_source_short": 17,
    "order_below_minimum": 18,
    "standing_grant_run": 19,
    "demand_basis_switch": 20,
}

_TITLES = {
    "buffer_stocked_out": "Buffer stocked out",
    "buffer_critical": "Buffer critical",
    "stockout_likely": "Stockout likely",
    "stockout_at_risk": "Stockout at risk",
    "expedite_after_stockout": "Expedite after stockout",
    "expedite_late": "Expedite late",
    "forecast_bias": "Forecast bias",
    "release_awaiting_approval": "Release awaiting approval",
    "transfer_awaiting_release": "Transfer awaiting release",
    "release_failed": "Release failed",
    "transfer_failed": "Transfer failed",
    "erp_push_failed": "ERP push failed",
    "sop_signoff_due": "S&OP sign-off due",
    "erp_code_clash": "Same codes in two ERPs",
    "erp_currency_mismatch": "ERP reports in another currency",
    "cross_erp_opportunity": "Short in one ERP, excess in the other",
    "supplier_risk_high": "High-risk supplier",
    "sole_source_short": "Sole source, short of stock",
    "order_below_minimum": "Order below supplier minimum",
    "standing_grant_run": "Standing grant run needs attention",
    "demand_basis_switch": "Demand basis to review",
}


class ExceptionCounts(BaseModel):
    """The response's row counts: by severity, by stored
    acknowledgement and the rows whose ``new`` is true.  The counts
    cover only the rows returned."""

    high: int
    medium: int
    acknowledged: int
    new: int


class ExceptionProposal(BaseModel):
    """The stockout decision that already chose the row's remedy.

    The item-site's current decision of the ``stockout_decisions``
    read model that answers the row: ``record_kind`` is
    ``stockout_decision``, the only record kind of this slice,
    ``record_id`` the decision's id, ``decision`` and ``action`` the
    decision's own (``action`` null when it has none), ``recorded_at``
    its ``decided_at`` in UTC, ``recorded_by_name`` its
    ``decided_by_name`` (null when it has none) and ``reason`` the
    one line of text for why the decision answers the row.
    """

    record_kind: Literal["stockout_decision"]
    record_id: str
    decision: str
    action: str | None = None
    recorded_at: datetime
    recorded_by_name: str | None = None
    reason: str


class ExceptionRow(BaseModel):
    """One exception: a row of the triage list.

    ``key`` is the stable ``<kind>:<ref>`` identity; ``item_code``
    and ``site_code`` are null when the exception is not
    item-specific; ``view`` is the planner hash that shows it and
    ``detected_on`` the date the exception was detected.
    ``first_seen_on`` is the date the key was first captured (null
    when it has never been captured) and ``new`` is true only when
    ``first_seen_on`` equals the response's as-of date.
    """

    kind: str
    severity: str
    key: str
    item_code: str | None = None
    site_code: str | None = None
    title: str
    detail: str
    view: str
    detected_on: date
    acknowledged: bool = False
    acknowledged_by_name: str | None = None
    acknowledged_at: str | None = None
    snooze_until: date | None = None
    first_seen_on: date | None = None
    new: bool = False
    proposal: ExceptionProposal | None = None
    # The route's include_proposal parameter for this row: read off
    # the answered row's dict (a key the validation ignores) by the
    # validator below and kept as private state, so it is part of
    # neither the model's fields nor its schema, let alone the answer.
    _include_proposal: bool = PrivateAttr(default=False)

    @model_validator(mode="wrap")
    def _remember_include_proposal(cls, value, handler):
        """Remember the route's include_proposal parameter from the
        answered row's dict, so the serializer can drop the proposal
        key while it is false."""
        if isinstance(value, dict):
            include_proposal = value.get("include_proposal", False)
        else:
            include_proposal = getattr(value, "_include_proposal", False)
        row = handler(value)
        row._include_proposal = bool(include_proposal)
        return row

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        """The row's fields, without ``proposal`` while the route's
        ``include_proposal`` parameter is false (the key stays out of
        the answer, exactly as before the proposal existed)."""
        data = handler(self)
        if not self._include_proposal:
            data.pop("proposal", None)
        return data


class AckBody(BaseModel):
    """The acknowledgement request: the exception's stable key plus
    the optional snooze date and note (null when omitted, which
    clears the stored value)."""

    key: str = Field(min_length=1, max_length=_ACK_MAX_LENGTH)
    snooze_until: date | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _no_nul(self) -> AckBody:
        """A NUL byte in the key or the note answers 422 instead of a
        database error."""
        if "\x00" in self.key:
            raise ValueError("key must not contain NUL bytes")
        if self.note is not None and "\x00" in self.note:
            raise ValueError("note must not contain NUL bytes")
        return self


class AckRow(BaseModel):
    """The stored acknowledgement row, as answered by the PUT."""

    key: str
    acknowledged_by: str
    acknowledged_by_name: str | None = None
    acknowledged_at: datetime
    snooze_until: date | None = None
    note: str | None = None


class ExceptionResponse(BaseModel):
    """The exceptions triage list, as of the latest source as-of."""

    as_of: date | None
    counts: ExceptionCounts
    rows: list[ExceptionRow]
    sources_failed: list[str]


def _utcnow() -> datetime:
    """The current UTC time (the tests pin it to test the window)."""
    return datetime.now(UTC)


def _as_date(value: object) -> date | None:
    """A source as-of value (date, datetime or ISO string) as a
    date, or None when it carries no date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _aware_utc(value: object) -> datetime | None:
    """A row timestamp (datetime or ISO string) as an aware UTC
    datetime, or None when it carries no timestamp.

    A naive timestamp is taken as UTC; the comparison window is a
    UTC timestamp, never the instance time zone.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _whole_pct(probability: object) -> int | None:
    """A 0..1 probability as a whole percent (0.83 -> 83)."""
    try:
        value = float(probability)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return int(round(value * 100))


def _ddmrp_source() -> tuple[date, list[dict]]:
    """The DDMRP source: the plan the DDMRP plan route builds.

    The shared code, not a copy: the very same ``ddmrp_zone_rows``
    + ``plan_ddmrp`` pair ``GET /api/ddmrp/plan`` calls, so the
    exception rows are the plan's own rows.  Returns the plan's
    as-of date and its rows (one read per call).
    """
    as_of, zone_rows = ddmrp_zone_rows()
    return as_of, plan_ddmrp(as_of, zone_rows)["rows"]


def _ddmrp_rows(as_of: date, plan_rows: list[dict]) -> list[ExceptionRow]:
    """The exception rows of the DDMRP plan's buffer rows."""
    rows: list[ExceptionRow] = []
    for plan_row in plan_rows:
        status = plan_row.get("on_hand_status")
        if status == "stocked_out":
            kind = "buffer_stocked_out"
        elif status == "critical":
            kind = "buffer_critical"
        else:
            continue
        item_code = plan_row.get("item_code")
        site_code = plan_row.get("site_code")
        ref = f"{item_code}/{site_code}"
        on_hand = plan_row.get("on_hand")
        pct = plan_row.get("on_hand_pct_of_red")
        if pct is None:
            detail = f"on hand {on_hand:g}"
        else:
            detail = f"on hand {on_hand:g} ({pct:g}% of top of red)"
        rows.append(
            ExceptionRow(
                kind=kind,
                severity="high" if status == "stocked_out" else "medium",
                key=f"{kind}:{ref}",
                item_code=item_code,
                site_code=site_code,
                title=_TITLES[kind],
                detail=detail,
                view="#/buffers",
                detected_on=as_of,
            )
        )
    return rows


def _stockout_rows(source_rows: list[dict]) -> list[ExceptionRow]:
    """The exception rows of the ranked stockout-risk rows."""
    rows: list[ExceptionRow] = []
    for row in source_rows:
        category = row.get("category")
        if category == "likely":
            kind = "stockout_likely"
        elif category == "at_risk":
            kind = "stockout_at_risk"
        else:
            continue
        item_code = row.get("item_code")
        site_code = row.get("site_code")
        ref = f"{item_code}/{site_code}"
        projected = _as_date(row.get("projected_stockout_on"))
        pct = _whole_pct(row.get("p_stockout"))
        if projected is not None:
            if pct is None:
                detail = f"projected stockout {projected.isoformat()}"
            else:
                detail = f"projected stockout {projected.isoformat()}, {pct}% chance"
        elif pct is None:
            detail = "no projected stockout date"
        else:
            detail = f"no projected stockout date, {pct}% chance"
        rows.append(
            ExceptionRow(
                kind=kind,
                severity="high" if category == "likely" else "medium",
                key=f"{kind}:{ref}",
                item_code=item_code,
                site_code=site_code,
                title=_TITLES[kind],
                detail=detail,
                view="#/queue?kind=stockout",
                detected_on=_as_date(row.get("as_of")),
            )
        )
    return rows


def _expedite_rows(payload: dict) -> tuple[date | None, list[ExceptionRow]]:
    """The exception rows of the expedite lines, with the source's
    as-of date."""
    as_of = _as_date(payload.get("as_of"))
    rows: list[ExceptionRow] = []
    for line in payload.get("lines") or []:
        reasons = line.get("reasons") or []
        if "after_stockout" in reasons:
            kind = "expedite_after_stockout"
        elif "late" in reasons:
            kind = "expedite_late"
        else:
            continue
        order_nbr = line.get("order_nbr")
        line_nbr = line.get("line_nbr")
        ref = f"{order_nbr}/{line_nbr}"
        open_units = line.get("open_units")
        days_late = line.get("days_late")
        if days_late is None:
            detail = f"{open_units:g} units"
        else:
            detail = f"{open_units:g} units, {days_late} days late"
        rows.append(
            ExceptionRow(
                kind=kind,
                severity="high" if "after_stockout" in reasons else "medium",
                key=f"{kind}:{ref}",
                item_code=line.get("item_code"),
                site_code=line.get("site_code"),
                title=_TITLES[kind],
                detail=detail,
                view="#/expedite",
                detected_on=as_of,
            )
        )
    return as_of, rows


def _forecast_bias(value: object) -> float | None:
    """The item-site's ``effective_bias`` as a finite number, or
    None when it is not one — a missing measure is never a bias,
    nor is a non-finite one (nan or infinity: the whole-percent
    it would round to does not exist).  Booleans are not numbers
    here: true is not a 100% bias."""
    if isinstance(value, bool):
        return None
    try:
        bias = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return bias if math.isfinite(bias) else None


def _compared_bucket_count(value: object) -> int:
    """The item-site's ``compared_buckets`` as a count, 0 when it
    carries no number at all (never a negative)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def _forecast_bias_accuracy(stored_settings: dict | None) -> dict:
    """The forecast bias's one accuracy read for a call.

    Takes the stored system settings the caller already read once
    and reads no setting of its own: it reads
    ``exceptions.forecast_bias_periods`` from them through
    ``setting_value``, so a missing, out-of-range or non-numeric
    stored value reads the registry default, 6, and answers
    ``realised_accuracy(bucket_index=1, latest_periods=periods)``
    with ``periods`` the whole part of that value — the completed
    snapshots one period ahead over the item-sites' latest
    forecast periods, each forecast one period ahead.  The read
    goes through the module's own ``realised_accuracy`` name.
    """
    periods = int(setting_value(stored_settings, FORECAST_BIAS_PERIODS_KEY))
    return realised_accuracy(bucket_index=1, latest_periods=periods)


def _forecast_rows(
    payload: object,
    bias_threshold: float,
    min_periods: float,
) -> list[ExceptionRow]:
    """The exception rows of the forecast-bias item-sites: one row
    per item-site whose ``compared_buckets`` is at least
    ``min_periods`` and whose ``effective_bias`` is a number of
    absolute value at least ``bias_threshold`` — a positive bias
    forecasts "over", a negative one "under", the
    percent the absolute bias as a whole percent.  A finite bias
    whose whole percent overflows the float percent (``abs(bias) *
    100`` past the largest finite float) is still given its exact
    whole percent, never a failed source.

    The accuracy result's as-of date is the row's ``detected_on``
    only: the source never reports it as the response's as-of.
    """
    as_of = None
    item_sites: list | None = None
    if isinstance(payload, dict):
        as_of = _as_date(payload.get("as_of"))
        raw_sites = payload.get("item_sites")
        if isinstance(raw_sites, list):
            item_sites = raw_sites
    rows: list[ExceptionRow] = []
    for site in item_sites or []:
        if not isinstance(site, dict):
            continue
        buckets = _compared_bucket_count(site.get("compared_buckets"))
        bias = _forecast_bias(site.get("effective_bias"))
        if buckets < min_periods or bias is None or abs(bias) < bias_threshold:
            continue
        item_code = site.get("item_code")
        site_code = site.get("site_code")
        ref = f"{item_code}/{site_code}"
        pct_value = abs(bias) * 100
        pct = (
            int(round(pct_value))
            if math.isfinite(pct_value)
            else int(round(Decimal(abs(bias)) * 100))
        )
        direction = "over" if bias > 0 else "under"
        rows.append(
            ExceptionRow(
                kind="forecast_bias",
                severity="medium",
                key=f"forecast_bias:{ref}",
                item_code=item_code,
                site_code=site_code,
                title=_TITLES["forecast_bias"],
                detail=(
                    f"forecast {direction} actual by {pct}% "
                    f"across {buckets} periods"
                ),
                view="#/demand",
                detected_on=as_of,
            )
        )
    return rows


def _failure_rows(
    kind: str,
    source_rows: list[dict],
    timestamp_field: str,
    outcomes: tuple[str, ...],
    detail_for,
) -> list[ExceptionRow]:
    """The exception rows of one failure kind (release, transfer or
    push).

    The rows whose outcome is in ``outcomes`` and whose
    ``timestamp_field`` is within the 14-day UTC window (inclusive of
    the exact cutoff); ``detail_for`` builds the public detail —
    never the stored error text.  ``detected_on`` is the row's date.
    """
    cutoff = _utcnow() - timedelta(days=FAILURE_WINDOW_DAYS)
    rows: list[ExceptionRow] = []
    for row in source_rows:
        if row.get("outcome") not in outcomes:
            continue
        timestamp = _aware_utc(row.get(timestamp_field))
        if timestamp is None or timestamp < cutoff:
            continue
        row_id = row.get("id")
        if kind == "release_failed":
            view = "#/releases"
        elif kind == "transfer_failed":
            view = "#/orders"
        else:
            view = "#/writeback"
        rows.append(
            ExceptionRow(
                kind=kind,
                severity="high",
                key=f"{kind}:{row_id}",
                item_code=None,
                site_code=None,
                title=_TITLES[kind],
                detail=detail_for(row),
                view=view,
                detected_on=timestamp.date(),
            )
        )
    return rows


def _release_awaiting_rows(
    release_rows: list[dict],
    feed_order_numbers: set,
    days: float,
) -> list[ExceptionRow]:
    """The exception rows of the in-flight release rows still
    awaiting the ERP's approval.

    A row is flagged when it is in flight — the very rule
    ``app.aggregate.projection._in_flight_supply_lines`` applies:
    outcome "created" or "exists" (``projection._IN_FLIGHT_OUTCOMES``),
    a non-null ``order_nbr`` and that order number not among the
    newest open-purchase snapshot's feed order numbers — and its
    ``updated_at`` is at least ``days`` days before the current UTC
    time (inclusive of the exact cutoff, as the failure window is).
    The ref is the row id, the detail reads "<order_nbr> to
    <vendor_code, else supplier_code, else "an unknown supplier">
    awaiting approval in the ERP for <n> days" ("1 day" at exactly
    one whole day; n the whole days since ``updated_at``) and
    ``detected_on`` is the row's date.  Nothing here can raise on a
    hostile row (never a 500).
    """
    now = _utcnow()
    cutoff = now - timedelta(days=days)
    rows: list[ExceptionRow] = []
    for row in release_rows:
        if not isinstance(row, dict):
            continue
        if row.get("outcome") not in projection._IN_FLIGHT_OUTCOMES:
            continue
        order_nbr = row.get("order_nbr")
        if order_nbr is None:
            continue
        if isinstance(order_nbr, str) and order_nbr in feed_order_numbers:
            continue
        updated_at = _aware_utc(row.get("updated_at"))
        if updated_at is None or updated_at > cutoff:
            continue
        days_waited = (now - updated_at).days
        target = row.get("vendor_code") or row.get("supplier_code")
        if not target:
            target = "an unknown supplier"
        wait = "1 day" if days_waited == 1 else f"{days_waited} days"
        rows.append(
            ExceptionRow(
                kind="release_awaiting_approval",
                severity="medium",
                key=f"release_awaiting_approval:{row.get('id')}",
                item_code=None,
                site_code=None,
                title=_TITLES["release_awaiting_approval"],
                detail=(
                    f"{order_nbr} to {target} awaiting approval "
                    f"in the ERP for {wait}"
                ),
                view="#/releases",
                detected_on=updated_at.date(),
            )
        )
    return rows


# The ERP statuses that stop an in-flight transfer release row
# counting: once the ERP reads the order in one of these the
# on-hand sync shows the moved stock, so the row stops awaiting.
_TRANSFER_RELEASED_ERP_STATUSES = ("Released", "Completed", "Canceled")


def _transfer_awaiting_rows(
    in_flight_rows: list[dict],
    days: float,
) -> list[ExceptionRow]:
    """The exception rows of the in-flight transfer releases still
    awaiting the ERP's release.

    ``in_flight_rows`` is the one ``list_in_flight`` read of the
    call (every ``transfer_releases`` row whose outcome is
    "created" or "exists" and whose ``reference_nbr`` is non-null).
    A row is flagged when its ``erp_status`` is null or none of
    "Released", "Completed" and "Canceled" (the very rule
    ``app.recommend.reorder`` applies to the same read) and its
    ``updated_at`` is at least ``days`` days before the current UTC
    time (inclusive of the exact cutoff, as the failure window and
    the release-approval window are).  The ref is the row id, the
    detail reads "transfer <reference_nbr> <from_site_code> →
    <to_site_code> awaiting release in the ERP for <n> days"
    ("1 day" at exactly one whole day; n the whole days since
    ``updated_at``), the view is "#/orders" and ``detected_on`` is
    the row's date.  Nothing here can raise on a hostile row
    (never a 500).
    """
    now = _utcnow()
    cutoff = now - timedelta(days=days)
    rows: list[ExceptionRow] = []
    for row in in_flight_rows:
        if not isinstance(row, dict):
            continue
        if row.get("erp_status") in _TRANSFER_RELEASED_ERP_STATUSES:
            continue
        updated_at = _aware_utc(row.get("updated_at"))
        if updated_at is None or updated_at > cutoff:
            continue
        days_waited = (now - updated_at).days
        wait = "1 day" if days_waited == 1 else f"{days_waited} days"
        rows.append(
            ExceptionRow(
                kind="transfer_awaiting_release",
                severity="medium",
                key=f"transfer_awaiting_release:{row.get('id')}",
                item_code=None,
                site_code=None,
                title=_TITLES["transfer_awaiting_release"],
                detail=(
                    f"transfer {row.get('reference_nbr')} "
                    f"{row.get('from_site_code')} → "
                    f"{row.get('to_site_code')} awaiting release "
                    f"in the ERP for {wait}"
                ),
                view="#/orders",
                detected_on=updated_at.date(),
            )
        )
    return rows


def _release_detail(row: dict) -> str:
    """The failed release's public detail: the vendor code when
    Acumatica resolved one, else the supplier code — never the
    stored error text."""
    target = row.get("vendor_code") or row.get("supplier_code")
    return f"release to {target} failed"


def _sop_signoff_due_rows(
    newest_rows: list[dict],
    current_month_latest: dict | None,
    stored_settings: dict | None,
) -> list[ExceptionRow]:
    """The one S&OP sign-off-due row, when it is due.

    ``newest_rows`` is the one ``list_newest(limit=1)`` read of the
    call (the newest stored sign-off of any month, empty while none
    is stored) and ``current_month_latest`` the one
    ``latest_for_month`` read (the newest stored sign-off of the
    current month, the "YYYY-MM" of the current server date, the
    review month ``GET /api/sop/review`` uses, or None when the
    month carries none).  The row is yielded only when all of these
    hold: at least one sign-off of any month exists, the current
    month has none, and the current server date's day of the month
    is at least the ``exceptions.sop_signoff_day`` system setting
    (default 10) — a missing or out-of-range stored value falls
    back to the registry default, as the other settings do.  The
    row is kind "sop_signoff_due" (medium), the key
    "sop_signoff_due:<month>", ``item_code`` and ``site_code``
    null, title "S&OP sign-off due", the detail "<month> has not
    been signed off. The last sign-off was for <the newest row's
    month> on <the date of its created_at>." and view "#/sop",
    ``detected_on`` the date in the current month whose day is the
    first whole day at or above the setting — the setting itself
    for a whole-day value, the first due day for a fractional one
    (the comparison is made against the stored value as-is, never
    a truncated whole day).  Nothing here can raise on a hostile
    row (never a 500).
    """
    if not newest_rows or current_month_latest is not None:
        return []
    newest = newest_rows[0]
    if not isinstance(newest, dict):
        return []
    today = date.today()
    due_day = setting_value(stored_settings, SOP_SIGNOFF_DAY_KEY)
    if today.day < due_day:
        return []
    month = today.strftime("%Y-%m")
    last_month = newest.get("month")
    if not isinstance(last_month, str):
        last_month = "an unknown month"
    created_on = _as_date(newest.get("created_at"))
    created_text = "an unknown date" if created_on is None else created_on.isoformat()
    return [
        ExceptionRow(
            kind="sop_signoff_due",
            severity="medium",
            key=f"sop_signoff_due:{month}",
            item_code=None,
            site_code=None,
            title=_TITLES["sop_signoff_due"],
            detail=(
                f"{month} has not been signed off. "
                f"The last sign-off was for {last_month} "
                f"on {created_text}."
            ),
            view="#/sop",
            detected_on=date(today.year, today.month, math.ceil(due_day)),
        )
    ]


def _erp_code_clash_rows(clash_rows: list[dict]) -> list[ExceptionRow]:
    """The erp_code_clash rows of the open clash pairs.

    One row per open row of ``erp_code_clashes`` (``resolved_at``
    still null, the most recently seen first, then by ``id`` — the
    order of the one ``list_open`` read): kind "erp_code_clash"
    (high), the key
    "erp_code_clash:<refused_source>:<item_code>/<site_code>", the
    clash's ``item_code`` and ``site_code``, title "Same codes in
    two ERPs", the detail "<item_code> at <site_code> is held by
    <held_by>, so <refused_source> rows for it are not loaded.",
    view "#/settings" and ``detected_on`` the date of
    ``first_seen_at``.  A row whose codes are not strings or whose
    ``first_seen_at`` carries no date contributes nothing — nothing
    here can raise on a hostile row (never a 500).
    """
    rows: list[ExceptionRow] = []
    for clash in clash_rows:
        if not isinstance(clash, dict):
            continue
        item_code = clash.get("item_code")
        site_code = clash.get("site_code")
        held_by = clash.get("held_by")
        refused_source = clash.get("refused_source")
        if (
            not isinstance(item_code, str)
            or not isinstance(site_code, str)
            or not isinstance(held_by, str)
            or not isinstance(refused_source, str)
        ):
            continue
        detected_on = _as_date(clash.get("first_seen_at"))
        if detected_on is None:
            continue
        rows.append(
            ExceptionRow(
                kind="erp_code_clash",
                severity="high",
                key=f"erp_code_clash:{refused_source}:{item_code}/{site_code}",
                item_code=item_code,
                site_code=site_code,
                title=_TITLES["erp_code_clash"],
                detail=(
                    f"{item_code} at {site_code} is held by {held_by}, "
                    f"so {refused_source} rows for it are not loaded."
                ),
                view="#/settings",
                detected_on=detected_on,
            )
        )
    return rows


def _reporting_currency(stored_settings: dict | None) -> str:
    """The ``erp.reporting_currency`` setting for one call: the stored
    value when it is one of the allowed currencies — compared ignoring
    case, so a stored "usd" reads as "USD" — else the registry
    default: the currency every ERP is assumed to report in."""
    setting = get_setting(REPORTING_CURRENCY_KEY)
    allowed = tuple(value.upper() for value in (setting.allowed or ()))
    stored = (stored_settings or {}).get(REPORTING_CURRENCY_KEY)
    if isinstance(stored, str):
        candidate = stored.strip().upper()
        if candidate in allowed:
            return candidate
    return str(setting.default) if setting is not None else "USD"


def _erp_currency_mismatch_rows(
    currency_rows: list[dict], reporting_currency: str
) -> list[ExceptionRow]:
    """The erp_currency_mismatch rows of the stored currency codes.

    One row per stored row of ``erp_source_currencies`` whose
    ``currency_code`` differs from *reporting_currency* ignoring
    case: kind "erp_currency_mismatch" (high), the key
    "erp_currency_mismatch:<source_system>:<currency_code>",
    ``item_code`` and ``site_code`` null, title "ERP reports in
    another currency", the detail "<source_system> reports in
    <currency_code>, but values are added up as <setting>. AIERP does
    not convert currencies.", view "#/settings" and ``detected_on``
    the date of ``seen_at``.  A row whose fields are not strings or
    whose ``seen_at`` carries no date contributes nothing — nothing
    here can raise on a hostile row (never a 500).
    """
    rows: list[ExceptionRow] = []
    expected = reporting_currency.upper()
    for currency in currency_rows:
        if not isinstance(currency, dict):
            continue
        source_system = currency.get("source_system")
        code = currency.get("currency_code")
        if not isinstance(source_system, str) or not isinstance(code, str):
            continue
        code = code.strip().upper()
        if not code or code == expected:
            continue
        detected_on = _as_date(currency.get("seen_at"))
        if detected_on is None:
            continue
        rows.append(
            ExceptionRow(
                kind="erp_currency_mismatch",
                severity="high",
                key=f"erp_currency_mismatch:{source_system}:{code}",
                item_code=None,
                site_code=None,
                title=_TITLES["erp_currency_mismatch"],
                detail=(
                    f"{source_system} reports in {code}, but values "
                    f"are added up as {expected}. AIERP does not "
                    "convert currencies."
                ),
                view="#/settings",
                detected_on=detected_on,
            )
        )
    return rows


def _cross_erp_opportunity_rows(answer: dict) -> list[ExceptionRow]:
    """The cross_erp_opportunity rows of the cross-ERP comparison.

    One row per answer row whose ``opportunity`` is true: kind
    "cross_erp_opportunity" (medium), the key
    "cross_erp_opportunity:<source_code>:<target_code>", the short
    side's ``item_code`` and ``site_code`` null, title "Short in one
    ERP, excess in the other", the detail "<short source> is short
    of <short code>, while <excess source> holds excess of <excess
    code>. AIERP does not move stock between ERPs." — with the
    "Up to <N> units could move." sentence before the last one when
    the comparison row's ``suggested_move_units`` is an integer
    above 0 (N with thousands separators) — (each source the side's
    ``source_system``: "Acumatica" for ``acumatica`` and "Odoo" for
    ``odoo``, compared ignoring case, any other non-empty string as
    stored, or "an ERP" when it is null or empty), view "#/across"
    and ``detected_on`` the answer's ``as_of``.  A row whose fields
    are not what the comparison reports contributes nothing — nothing
    here can raise on a hostile row (never a 500).
    """
    if not isinstance(answer, dict):
        return []
    detected_on = _as_date(answer.get("as_of"))
    if detected_on is None:
        return []
    answer_rows = answer.get("rows")
    if not isinstance(answer_rows, list):
        return []
    rows: list[ExceptionRow] = []
    for row in answer_rows:
        if not isinstance(row, dict) or row.get("opportunity") is not True:
            continue
        source_code = row.get("source_code")
        target_code = row.get("target_code")
        if not isinstance(source_code, str) or not isinstance(target_code, str):
            continue
        sides = row.get("sides")
        if not isinstance(sides, list):
            continue
        short_side = _cross_erp_side(sides, row.get("short_side"))
        excess_side = _cross_erp_side(sides, row.get("excess_side"))
        if short_side is None or excess_side is None:
            continue
        short_code = short_side.get("item_code")
        excess_code = excess_side.get("item_code")
        if not isinstance(short_code, str) or not isinstance(excess_code, str):
            continue
        move_units = row.get("suggested_move_units")
        move_sentence = (
            f" Up to {move_units:,} units could move."
            if isinstance(move_units, int)
            and not isinstance(move_units, bool)
            and move_units > 0
            else ""
        )
        rows.append(
            ExceptionRow(
                kind="cross_erp_opportunity",
                severity="medium",
                key=f"cross_erp_opportunity:{source_code}:{target_code}",
                item_code=short_code,
                site_code=None,
                title=_TITLES["cross_erp_opportunity"],
                detail=(
                    f"{_cross_erp_source_name(short_side.get('source_system'))} "
                    f"is short of {short_code}, while "
                    f"{_cross_erp_source_name(excess_side.get('source_system'))} "
                    f"holds excess of {excess_code}.{move_sentence} AIERP "
                    "does not move stock between ERPs."
                ),
                view="#/across",
                detected_on=detected_on,
            )
        )
    return rows


def _cross_erp_side(sides: list, index: object) -> dict | None:
    """The side at *index*, or None when the index is not an integer
    in range (a bool never is one) or the side is not a mapping."""
    if isinstance(index, bool) or not isinstance(index, int):
        return None
    if not 0 <= index < len(sides):
        return None
    side = sides[index]
    return side if isinstance(side, dict) else None


def _cross_erp_source_name(source_system: object) -> str:
    """The side's source system for the detail: "Acumatica" for
    ``acumatica`` and "Odoo" for ``odoo`` (compared ignoring case),
    any other non-empty string as stored, or "an ERP" when the side
    stores none (or stores a value that is not a non-empty
    string)."""
    if isinstance(source_system, str) and source_system:
        lowered = source_system.lower()
        if lowered == "acumatica":
            return "Acumatica"
        if lowered == "odoo":
            return "Odoo"
        return source_system
    return "an ERP"


def _supplier_risk_high_rows(answer: dict) -> list[ExceptionRow]:
    """The supplier_risk_high rows of the supplier risk rating.

    One row per rating entry whose ``risk`` is "high": kind
    "supplier_risk_high" (medium), the key
    "supplier_risk_high:<supplier_code>", ``item_code`` and
    ``site_code`` null, title "High-risk supplier", the detail
    "<name> is the only supplier of <N> item(s) and <reasons>."
    (the name the entry's ``supplier_name``, else its
    ``supplier_code``, N the entry's ``sole_source_items``, written
    "item" when it is 1, and the reasons "delivers late" when the
    entry is ``late`` and "has variable lead times" when it is
    ``variable``, joined by " and " when both), view "#/suppliers"
    and ``detected_on`` the answer's ``as_of``.  A row whose fields
    are not what the rating reports contributes nothing — nothing
    here can raise on a hostile row (never a 500).
    """
    if not isinstance(answer, dict):
        return []
    detected_on = _as_date(answer.get("as_of"))
    if detected_on is None:
        return []
    suppliers = answer.get("suppliers")
    if not isinstance(suppliers, list):
        return []
    rows: list[ExceptionRow] = []
    for entry in suppliers:
        if not isinstance(entry, dict) or entry.get("risk") != "high":
            continue
        supplier_code = entry.get("supplier_code")
        if not isinstance(supplier_code, str) or not supplier_code:
            continue
        name = entry.get("supplier_name")
        if not isinstance(name, str) or not name:
            name = supplier_code
        count = entry.get("sole_source_items")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            continue
        items = "1 item" if count == 1 else f"{count} items"
        reasons = []
        if entry.get("late"):
            reasons.append("delivers late")
        if entry.get("variable"):
            reasons.append("has variable lead times")
        detail = f"{name} is the only supplier of {items}"
        if reasons:
            detail += f" and {' and '.join(reasons)}"
        detail += "."
        rows.append(
            ExceptionRow(
                kind="supplier_risk_high",
                severity="medium",
                key=f"supplier_risk_high:{supplier_code}",
                item_code=None,
                site_code=None,
                title=_TITLES["supplier_risk_high"],
                detail=detail,
                view="#/suppliers",
                detected_on=detected_on,
            )
        )
    return rows


def _finite_number(value: object) -> float | None:
    """A usable number as a finite float — an int, float or
    Decimal, never a bool, never non-finite — None for anything
    else."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _whole_text(value: object) -> str:
    """A number's text, a whole number's trailing ``.0`` dropped."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return str(value)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return str(value)


def _sole_source_short_rows(answer: dict) -> list[ExceptionRow]:
    """The sole_source_short rows of the second-source answer.

    One row per entry whose ``supplier_risk`` is "high", whose
    ``candidates`` hold no candidate with a ``basis`` of "terms"
    and whose ``bridge_short_units`` is a number above 0: kind
    "sole_source_short", severity "high" when the entry has no
    candidates at all and "medium" otherwise, the key
    "sole_source_short:<item_code>", the entry's ``item_code`` and
    ``site_code`` null, title "Sole source, short of stock", the
    detail "<item_code> comes only from <name>, rated high risk,
    and no other supplier has terms for it. Holding <bridge_units>
    units would cover <bridge_days> days; on hand is short by
    <bridge_short_units>." (the name the entry's ``supplier_name``,
    else its ``supplier_code``) plus " Suppliers of similar items
    to approach: <N>." when the entry has N candidates, or " No
    supplier of similar items found." when it has none, view
    "#/suppliers" and ``detected_on`` the answer's ``as_of``.  A
    row whose fields are not what the answer reports contributes
    nothing — nothing here can raise on a hostile row (never a
    500).
    """
    if not isinstance(answer, dict):
        return []
    detected_on = _as_date(answer.get("as_of"))
    if detected_on is None:
        return []
    items = answer.get("items")
    if not isinstance(items, list):
        return []
    rows: list[ExceptionRow] = []
    for entry in items:
        if not isinstance(entry, dict) or entry.get("supplier_risk") != "high":
            continue
        item_code = entry.get("item_code")
        if not isinstance(item_code, str) or not item_code:
            continue
        supplier_code = entry.get("supplier_code")
        if not isinstance(supplier_code, str) or not supplier_code:
            continue
        name = entry.get("supplier_name")
        if not isinstance(name, str) or not name:
            name = supplier_code
        candidates = entry.get("candidates")
        if not isinstance(candidates, list):
            candidates = []
        if any(
            isinstance(candidate, dict) and candidate.get("basis") == "terms"
            for candidate in candidates
        ):
            continue
        short_units = _finite_number(entry.get("bridge_short_units"))
        if short_units is None or short_units <= 0:
            continue
        bridge_units = _finite_number(entry.get("bridge_units"))
        bridge_days = _finite_number(entry.get("bridge_days"))
        if bridge_units is None or bridge_days is None:
            continue
        detail = (
            f"{item_code} comes only from {name}, rated high risk, "
            f"and no other supplier has terms for it. Holding "
            f"{_whole_text(bridge_units)} units would cover "
            f"{_whole_text(bridge_days)} days; on hand is short by "
            f"{_whole_text(short_units)}."
        )
        if candidates:
            detail += f" Suppliers of similar items to approach: {len(candidates)}."
        else:
            detail += " No supplier of similar items found."
        rows.append(
            ExceptionRow(
                kind="sole_source_short",
                severity="high" if not candidates else "medium",
                key=f"sole_source_short:{item_code}",
                item_code=item_code,
                site_code=None,
                title=_TITLES["sole_source_short"],
                detail=detail,
                view="#/suppliers",
                detected_on=detected_on,
            )
        )
    return rows


def _money(value: float) -> str:
    """A number as dollars and cents, thousands separated:
    ``$1,800.00``."""
    return f"${value:,.2f}"


def _plan_money(value: object) -> float | None:
    """A plan amount (int, float or Decimal) as a finite float, None
    for anything else — the plan answers its money as Decimals, which
    ``_finite_number`` does not take."""
    if isinstance(value, bool) or not isinstance(value, (numbers.Real, Decimal)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _order_below_minimum_rows(
    stored_settings: dict | None, plan: object
) -> list[ExceptionRow]:
    """The order_below_minimum rows of the order plan's answer.

    One row per supplier group of the plan that carries a
    ``minimum`` and a ``shortfall`` above 0 and holds a line whose
    ``due_in_days`` is at most the
    ``exceptions.order_minimum_due_days`` system setting (the
    stored value when it is a number within the setting's bounds,
    else the registry default — a failed settings read reads the
    default too): kind "order_below_minimum", severity "high" when
    one of the group's lines has a ``due_in_days`` of 0, else
    "medium", the key
    "order_below_minimum:<source_system>:<supplier_code>",
    ``item_code`` and ``site_code`` null, title "Order below
    supplier minimum", the detail "The order to <name> comes to
    <total_value>, <shortfall> below its minimum of <minimum>;
    <item_code> at <site_code> must be ordered by <order_by>."
    (the name the group's ``name``, else its ``supplier_code``; the
    line named the group's first — its lines are ordered by
    ``due_in_days``, then item, then site — the amounts dollars and
    cents with thousands separators, ``order_by`` read
    ``YYYY-MM-DD``), view "#/orders" and ``detected_on`` the
    plan's ``as_of`` (the plan's ``as_of`` is the rows'
    ``detected_on`` only, never the response's ``as_of``).  A group
    without a supplier has no minimum, so it never qualifies, and a
    group whose fields are not what the plan reports contributes
    nothing — nothing here can raise on a hostile row (never a
    500).
    """
    due_days = setting_value(stored_settings, ORDER_MINIMUM_DUE_DAYS_KEY)
    if not isinstance(plan, dict):
        return []
    detected_on = _as_date(plan.get("as_of"))
    if detected_on is None:
        return []
    suppliers = plan.get("suppliers")
    if not isinstance(suppliers, list):
        return []
    rows: list[ExceptionRow] = []
    for group in suppliers:
        if not isinstance(group, dict):
            continue
        supplier_code = group.get("supplier_code")
        if not isinstance(supplier_code, str) or not supplier_code:
            continue
        source_system = group.get("source_system")
        if not isinstance(source_system, str) or not source_system:
            continue
        total_value = _plan_money(group.get("total_value"))
        minimum = _plan_money(group.get("minimum"))
        shortfall = _plan_money(group.get("shortfall"))
        if total_value is None or minimum is None or shortfall is None:
            continue
        if shortfall <= 0:
            continue
        lines = group.get("lines")
        if not isinstance(lines, list) or not lines:
            continue
        first_line = lines[0]
        if not isinstance(first_line, dict):
            continue
        item_code = first_line.get("item_code")
        site_code = first_line.get("site_code")
        if not isinstance(item_code, str) or not item_code:
            continue
        if not isinstance(site_code, str) or not site_code:
            continue
        order_by = _as_date(first_line.get("order_by"))
        if order_by is None:
            continue
        due_in = [
            _plan_money(line.get("due_in_days"))
            for line in lines
            if isinstance(line, dict)
        ]
        if not any(due is not None and due <= due_days for due in due_in):
            continue
        severity = "high" if any(due == 0 for due in due_in) else "medium"
        name = group.get("name")
        if not isinstance(name, str) or not name:
            name = supplier_code
        detail = (
            f"The order to {name} comes to {_money(total_value)}, "
            f"{_money(shortfall)} below its minimum of "
            f"{_money(minimum)}; {item_code} at {site_code} must be "
            f"ordered by {order_by.isoformat()}."
        )
        rows.append(
            ExceptionRow(
                kind="order_below_minimum",
                severity=severity,
                key=f"order_below_minimum:{source_system}:{supplier_code}",
                item_code=None,
                site_code=None,
                title=_TITLES["order_below_minimum"],
                detail=detail,
                view="#/orders",
                detected_on=detected_on,
            )
        )
    return rows


# The two processes a standing grant covers, and the name each
# reads in a detail of this kind — the order plan and transfers.
_STANDING_GRANT_PROCESS_NAMES = {
    "order_plan_release": "the order plan",
    "transfer_release": "transfers",
}


def _standing_grant_reason(run: dict) -> str:
    """The run's stored reason as the detail names it — an empty
    string while the run stored none (never the word for null)."""
    reason = run.get("reason")
    return reason if isinstance(reason, str) else ""


def _standing_grant_rows_for(
    process: str, grant: object, run: object, now: datetime
) -> list[ExceptionRow]:
    """The rows of one in-force grant: its latest run's run row and
    the idle row, when due (the module function's docstring states
    the rule).

    A hostile stored row contributes nothing: a run under another
    grant, a run without a parseable time, a ``left_out`` that is
    not a list and a grant without a parseable ``created_at`` all
    keep their pieces out — nothing here can raise on a stored row
    (never a 500).
    """
    name = _STANDING_GRANT_PROCESS_NAMES.get(process)
    if not isinstance(grant, dict) or name is None:
        return []
    grant_id = grant.get("id")
    created_at = _aware_utc(grant.get("created_at"))
    run_under_grant = (
        run
        if isinstance(run, dict)
        and grant_id is not None
        and run.get("grant_id") == grant_id
        else None
    )
    started_at = (
        _aware_utc(run_under_grant.get("started_at"))
        if run_under_grant is not None
        else None
    )
    rows: list[ExceptionRow] = []
    if run_under_grant is not None and started_at is not None:
        outcome = run_under_grant.get("outcome")
        left_out = run_under_grant.get("left_out")
        held_back = (
            outcome == "released" and isinstance(left_out, list) and len(left_out) > 0
        )
        if outcome == "failed" or outcome == "skipped" or held_back:
            time_text = f"{started_at:%Y-%m-%d %H:%M} UTC"
            if outcome == "failed":
                severity = "high"
                detail = (
                    f"The standing grant for {name} failed at "
                    f"{time_text}: {_standing_grant_reason(run_under_grant)}."
                )
            elif outcome == "skipped":
                severity = "medium"
                detail = (
                    f"The standing grant for {name} sent nothing at "
                    f"{time_text}: {_standing_grant_reason(run_under_grant)}."
                )
            else:
                severity = "medium"
                documents = run_under_grant.get("documents")
                detail = (
                    f"The standing grant for {name} sent "
                    f"{documents if isinstance(documents, int) else 0} "
                    f"document(s) at {time_text} and held "
                    f"{len(left_out)} back over its limits."
                )
            rows.append(
                ExceptionRow(
                    kind="standing_grant_run",
                    severity=severity,
                    key=f"standing_grant_run:{run_under_grant.get('id')}",
                    item_code=None,
                    site_code=None,
                    title=_TITLES["standing_grant_run"],
                    detail=detail,
                    view="#/releases",
                    detected_on=started_at.date(),
                )
            )
    # The grant's time: the later of the grant's created_at and the
    # latest run's started_at under the grant, or created_at alone
    # while the process has no run under the grant.
    if run_under_grant is not None and started_at is not None:
        grant_time = started_at
        if created_at is not None and created_at > started_at:
            grant_time = created_at
    else:
        grant_time = created_at
    if grant_time is None:
        return rows
    if now - grant_time > timedelta(hours=STANDING_GRANT_IDLE_HOURS):
        idle_run_id = run_under_grant.get("id") if run_under_grant is not None else 0
        rows.append(
            ExceptionRow(
                kind="standing_grant_run",
                severity="high",
                key=f"standing_grant_run:idle:{grant_id}:{idle_run_id}",
                item_code=None,
                site_code=None,
                title=_TITLES["standing_grant_run"],
                detail=(
                    f"The standing grant for {name} has not run since "
                    f"{grant_time:%Y-%m-%d %H:%M} UTC."
                ),
                view="#/releases",
                detected_on=(
                    grant_time + timedelta(hours=STANDING_GRANT_IDLE_HOURS)
                ).date(),
            )
        )
    return rows


def _standing_grant_run_rows(
    stored_settings: dict | None, now: datetime
) -> list[ExceptionRow]:
    """The standing_grant_run rows of the exceptions list.

    The grants in force, once per call for each of
    ``order_plan_release`` and ``transfer_release``, through
    ``_standing_grants_repo.in_force_for``, and for each process
    with a grant in force its latest run row, through
    ``_standing_grant_runs_repo.latest_for`` — the process's row
    with the greatest ``started_at``, the greatest ``id`` breaking
    a tie, or None while it has none.  The ``erp.standing_grants
    _enabled`` switch comes from the stored system settings the
    list already reads once per call: a stored false raises no row
    of the kind, a missing value, or one that is not a boolean,
    counts as on.  The rows of one in-force grant come from
    :func:`_standing_grant_rows_for`: a latest run under the grant
    that failed, that skipped, or that released with a
    ``left_out`` that is not empty is one row, and the grant's time
    more than ``STANDING_GRANT_IDLE_HOURS`` hours before ``now``
    (the current time, read once per call, through the module's
    ``_utcnow()``) is one idle row, whatever the latest run's
    outcome.  Neither row's ``detected_on`` counts in the
    response's ``as_of``.
    """
    if (stored_settings or {}).get(STANDING_GRANTS_ENABLED_KEY) is False:
        return []
    rows: list[ExceptionRow] = []
    for process in ("order_plan_release", "transfer_release"):
        grant = _standing_grants_repo.in_force_for(process)
        if grant is None:
            continue
        rows.extend(
            _standing_grant_rows_for(
                process,
                grant,
                _standing_grant_runs_repo.latest_for(process),
                now,
            )
        )
    return rows


def _push_detail(row: dict) -> str:
    """The failed push's public detail: the source system — never
    the stored error."""
    return f"push to {row.get('source_system')} failed"


def _transfer_detail(row: dict) -> str:
    """The failed transfer's public detail: the two site codes —
    never the stored error text."""
    return f"transfer {row.get('from_site_code')} → {row.get('to_site_code')} " "failed"


def _effective_demand_basis(stored_settings: dict | None) -> str:
    """The effective ``replenishment.demand_basis`` of the call's
    stored settings: the stored value when it is one of the
    setting's allowed values, else the registry default
    (``history``) — the same rule the reorder point's basis
    applies.  It runs off the settings the list already read once:
    no settings read of its own, and the enum never runs through
    ``setting_value`` — a value written out of band that the
    setting cannot hold never names a basis, and the exceptions
    flow persists none."""
    setting = get_setting(DEMAND_BASIS_KEY)
    if setting is None:
        return "history"
    value = (stored_settings or {}).get(setting.key)
    if value is not None and value in setting.allowed:
        return value
    return setting.default


def _basis_switch_rows(
    answer: object,
    stored_settings: dict | None,
) -> list[ExceptionRow]:
    """The one demand-basis-switch row, when the measure proposes
    one.

    ``answer`` is the call's one ``basis_accuracy(bucket_index=1)``
    read (the demand basis one period ahead, the horizon the
    reorder points compare).  The row is yielded only when the
    measure's ``compared_buckets`` is at least the
    ``exceptions.basis_switch_min_periods`` system setting (default
    60) and the demand basis not in use was nearer what sold, one
    period ahead, by at least the ``exceptions.basis_switch_margin``
    system setting (default 5) points: the in-use basis ``history``
    with ``value_added`` times 100 at least the margin proposes
    ``forecast``; the in-use basis ``forecast`` with minus
    ``value_added`` times 100 at least the margin proposes
    ``history`` — a null ``value_added`` proposes neither.  The two
    settings come from the stored system settings the list already
    read once, through ``setting_value`` — a missing or out-of-range
    stored value falls back to the registry default, as the other
    settings do — while the stored settings hold no read at all
    (the settings read failed) the source runs off the registry
    defaults, never failing on that account — and the effective
    ``replenishment.demand_basis`` from them, the way the reorder
    point reads it.  The row is kind
    "demand_basis_switch" (medium), the key
    "demand_basis_switch:<the proposed basis>", ``item_code`` and
    ``site_code`` null, title "Demand basis to review", the detail
    "One period ahead over <N> periods, the <proposed basis> was
    <Z> points nearer what sold than the <basis in use>, which
    reorder points use. An admin may switch the demand basis to the
    <proposed basis> in Settings." (N the measure's compared
    buckets, with thousands separators, Z the absolute
    ``value_added`` times 100, to one decimal), view "#/settings"
    and ``detected_on`` the measure's ``as_of`` — never the
    response's ``as_of``.  Nothing here can raise on a hostile
    answer (never a 500).
    """
    if not isinstance(answer, dict):
        return []
    min_periods = setting_value(stored_settings, BASIS_SWITCH_MIN_PERIODS_KEY)
    buckets = _compared_bucket_count(answer.get("compared_buckets"))
    if buckets < min_periods:
        return []
    value_added = _finite_number(answer.get("value_added"))
    if value_added is None:
        return []
    margin = setting_value(stored_settings, BASIS_SWITCH_MARGIN_KEY)
    basis = _effective_demand_basis(stored_settings)
    if basis == "history":
        if value_added * 100 < margin:
            return []
        proposed, in_use = "forecast", "history"
    else:
        if -value_added * 100 < margin:
            return []
        proposed, in_use = "history", "forecast"
    points = abs(value_added) * 100
    points_text = (
        f"{points:.1f}"
        if math.isfinite(points)
        else f"{Decimal(abs(value_added)) * 100:.1f}"
    )
    as_of = _as_date(answer.get("as_of"))
    return [
        ExceptionRow(
            kind="demand_basis_switch",
            severity="medium",
            key=f"demand_basis_switch:{proposed}",
            item_code=None,
            site_code=None,
            title=_TITLES["demand_basis_switch"],
            detail=(
                f"One period ahead over {buckets:,} periods, the "
                f"{proposed} was {points_text} points nearer what "
                f"sold than the {in_use}, which reorder points use. "
                f"An admin may switch the demand basis to the "
                f"{proposed} in Settings."
            ),
            view="#/settings",
            detected_on=as_of,
        )
    ]


def _sort_key(row: ExceptionRow) -> tuple:
    """High before medium, then kind order, then item_code (nulls
    last) and key."""
    item_code = row.item_code
    return (
        0 if row.severity == "high" else 1,
        _KIND_ORDER[row.kind],
        item_code is None,
        item_code or "",
        row.key,
    )


def _collect() -> tuple[date | None, list[ExceptionRow], list[str]]:
    """The current exceptions list: the sixteen sources, each read once.

    Returns the latest as-of date reported by any successful source
    (null when none reports one), the rows — ordered high before
    medium, then kind order, then item_code and key — and the names
    of the sources that raised: a source that raises is caught and
    listed while the other sources still answer.

    The gathering runs inside one
    ``app.recommend.reorder.recommendations_memo``, the one
    ``app.db.read_cache.read_once`` block and the one
    ``app.db.connection.one_connection()`` block: the DDMRP plan,
    the stockout ranking and the expedite list read their
    ``recommendations_for_all`` through the same memo — the ranking
    is given the plan's resolved as-of when it is the ranking's own
    default (``app.stats.demand.default_as_of``), the expedite list
    keeps its own default (today) — so one request never computes the
    recommendations more than once per as-of; every read the sources
    share answers each repeated call with the same arguments from the
    first call's result, deep-copied, so one request reads each of
    them once, whatever the number of sources that read it (the
    newest open-purchase snapshot, the demand statistics and 30-day
    history buckets the recommendations load, the ranked stockout
    rows, the supplier performance and the lead-time observations);
    and every ``get_cursor()`` of the call runs on the one
    connection of the block.  The answer — the as-of date, the rows
    and their order, and ``sources_failed`` — is the same as without
    the memo and the blocks.
    """
    with recommendations_memo(), read_once(), one_connection():
        return _gather_sources()


def _gather_sources() -> tuple[date | None, list[ExceptionRow], list[str]]:
    """The sixteen sources of the exceptions list, each read once,
    under ``_collect``'s one ``recommendations_memo``, the one
    ``app.db.read_cache.read_once`` block and the one
    ``app.db.connection.one_connection()`` block it carries.

    Returns the latest as-of date reported by any successful source
    (null when none reports one), the rows — ordered high before
    medium, then kind order, then item_code and key — and the names
    of the sources that raised: a source that raises is caught and
    listed while the other sources still answer.
    """
    rows: list[ExceptionRow] = []
    sources_failed: list[str] = []
    reported_as_ofs: list[date] = []

    # The stored system settings the setting-driven filters apply
    # (the forecast-bias measure and bounds and the release-approval
    # window), read once per call: a missing or out-of-range stored
    # value falls back to the registry default, and a settings read
    # failure fails the forecast source like any other forecast
    # failure (the release rows then run their window off the
    # registry default).
    try:
        stored_settings = _stored_system_settings()
    except Exception:
        stored_settings = None

    plan_as_of: date | None = None
    try:
        plan_as_of, plan_rows = _ddmrp_source()
        rows.extend(_ddmrp_rows(plan_as_of, plan_rows))
        # The as-of is reported only when the whole source answered
        # — a failure of the row mapping fails the source, so its
        # as-of must not count.
        reported_as_ofs.append(plan_as_of)
    except Exception:
        sources_failed.append("ddmrp")

    try:
        # The stockout ranking shares the DDMRP plan's one
        # recommendations computation when the plan's resolved as-of
        # is the ranking's own default (
        # ``app.stats.demand.default_as_of``): the memo then answers
        # the ranking from the plan's computation.  The expedite list
        # below keeps its own default (today) — its ``days_late`` must
        # read against today, never against the plan's as-of.
        with get_cursor() as cur:
            stockout_as_of = (
                plan_as_of
                if plan_as_of is not None and plan_as_of == default_as_of(cur)
                else None
            )
        stockout_source_rows = stockout_ranked(stockout_as_of)
        rows.extend(_stockout_rows(stockout_source_rows))
        if stockout_source_rows:
            reported = _as_date(stockout_source_rows[0].get("as_of"))
            if reported is not None:
                reported_as_ofs.append(reported)
    except Exception:
        sources_failed.append("stockout")

    try:
        expedite_as_of, expedite_rows = _expedite_rows(expedite_list())
        if expedite_as_of is not None:
            reported_as_ofs.append(expedite_as_of)
        rows.extend(expedite_rows)
    except Exception:
        sources_failed.append("expedite")

    try:
        # The releases' one read of plan_releases, shared by the
        # failed-release rows and the release-awaiting-approval
        # rows, alongside the one read of the newest open-purchase
        # snapshot: a failure of either read fails the source.
        release_rows = _plan_releases_repo.list_newest()
        open_lines = _open_purchase_lines_repo.list_latest()
        approval_days = setting_value(stored_settings, RELEASE_APPROVAL_DAYS_KEY)
        rows.extend(
            _failure_rows(
                "release_failed",
                release_rows,
                "updated_at",
                ("error", "aborted"),
                _release_detail,
            )
        )
        rows.extend(
            _release_awaiting_rows(
                release_rows,
                projection._feed_order_numbers(open_lines),
                approval_days,
            )
        )
    except Exception:
        sources_failed.append("releases")

    try:
        # The one read of the transfer_releases audit per call:
        # the newest rows, with a limit large enough that the
        # 14-day failure window is never truncated by the
        # pagination, alongside the one read of the in-flight
        # transfer rows: a failure of either read fails the source.
        newest_transfer_rows = _transfer_releases_repo.list_newest(
            limit=_TRANSFERS_READ_LIMIT
        )
        # The in-flight rows are read through the repository's
        # list_in_flight when it has one: a fake of the repository
        # that predates the read answers no in-flight rows.
        list_in_flight = getattr(_transfer_releases_repo, "list_in_flight", None)
        in_flight_transfer_rows = list_in_flight() if callable(list_in_flight) else []
        rows.extend(
            _failure_rows(
                "transfer_failed",
                newest_transfer_rows,
                "updated_at",
                ("error", "aborted"),
                _transfer_detail,
            )
        )
        rows.extend(
            _transfer_awaiting_rows(
                in_flight_transfer_rows,
                setting_value(stored_settings, RELEASE_APPROVAL_DAYS_KEY),
            )
        )
    except Exception:
        sources_failed.append("transfers")

    try:
        rows.extend(
            _failure_rows(
                "erp_push_failed",
                _parameter_writebacks_repo.list(limit=_PUSHS_READ_LIMIT),
                "written_at",
                ("failed",),
                _push_detail,
            )
        )
    except Exception:
        sources_failed.append("pushes")

    try:
        # The two reads of the sop_signoffs audit per call: the
        # newest stored sign-off of any month and the newest stored
        # sign-off of the current month (the "YYYY-MM" of the
        # current server date, the review month GET /api/sop/review
        # uses): a failure of either read fails the source.
        current_month = date.today().strftime("%Y-%m")
        rows.extend(
            _sop_signoff_due_rows(
                _sop_signoffs_repo.list_newest(limit=1),
                _sop_signoffs_repo.latest_for_month(current_month),
                stored_settings,
            )
        )
    except Exception:
        sources_failed.append("sop")

    try:
        # The one read of the open erp_code_clashes rows per call:
        # the rows whose resolved_at is still null, the most
        # recently seen first, then by id.
        rows.extend(_erp_code_clash_rows(_erp_code_clashes_repo.list_open(limit=200)))
    except Exception:
        sources_failed.append("clashes")

    try:
        # The one read of the erp_source_currencies rows per call:
        # one row per stored code the source reports in that
        # differs from the erp.reporting_currency setting, ignoring
        # case.  The setting comes from the stored system settings
        # read once per call; a missing stored value falls back to
        # the registry default.
        rows.extend(
            _erp_currency_mismatch_rows(
                _erp_source_currencies_repo.list_all(),
                _reporting_currency(stored_settings),
            )
        )
    except Exception:
        sources_failed.append("currencies")

    try:
        # The one read of the stored item mappings per call: the
        # cross-ERP comparison runs only when at least one mapping
        # is stored (and then once per call); a failure of the
        # mapping read or of the comparison fails the source,
        # listed "across".
        stored_item_mappings = _erp_code_mappings_repo.list(kind="item")
        if stored_item_mappings:
            rows.extend(_cross_erp_opportunity_rows(cross_erp_items()))
    except Exception:
        sources_failed.append("across")

    try:
        # The one call of the supplier risk rating per call: one row
        # per entry whose risk is "high".  The rating's as-of is the
        # rows' detected_on only, never the response's as_of.
        rows.extend(_supplier_risk_high_rows(supplier_risk()))
    except Exception:
        sources_failed.append("supplier_risk")

    try:
        # The one call of the second-source candidates per call:
        # one row per sole-source item of a high-risk supplier that
        # no other supplier holds terms for, short of its bridge
        # stock.  The answer's as-of is the rows' detected_on only,
        # never the response's as_of.
        rows.extend(_sole_source_short_rows(second_sources()))
    except Exception:
        sources_failed.append("second_sources")

    try:
        # The one read of the stored supplier order minimums per
        # call: the order plan is built (once, for the 7-day
        # horizon GET /api/supply-plan and the Order plan screen
        # default to) only when at least one minimum is stored.
        # A failure of the minimums read or of the plan build
        # fails the source, listed "order_minimums".  The plan's
        # as-of is the rows' detected_on only, never the
        # response's as_of.
        if _supplier_order_minimum_repo.list():
            rows.extend(
                _order_below_minimum_rows(stored_settings, build_supply_plan(7))
            )
    except Exception:
        sources_failed.append("order_minimums")

    try:
        # The one read of the stored system settings failed: the
        # forecast source fails like any other forecast failure.
        if stored_settings is None:
            raise RuntimeError("the settings read failed")
        # The two system settings the forecast-bias filter applies:
        # a missing or out-of-range stored value falls back to the
        # registry default.  The measure's own setting (how many of
        # the latest periods, each forecast one period ahead, it
        # runs over) is read from these same stored settings inside
        # _forecast_bias_accuracy.
        bias_threshold = setting_value(stored_settings, FORECAST_BIAS_THRESHOLD_KEY)
        min_periods = setting_value(stored_settings, FORECAST_BIAS_MIN_PERIODS_KEY)
        # The accuracy result's as-of is the row's detected_on
        # only: it never counts in the response's as_of.
        rows.extend(
            _forecast_rows(
                _forecast_bias_accuracy(stored_settings), bias_threshold, min_periods
            )
        )
    except Exception:
        sources_failed.append("forecast")

    try:
        # The grants in force, once per call for each of the two
        # processes, and each process's latest standing-grant run:
        # a failure of either read fails the source, listed
        # "standing_grants".  The switch comes from the stored
        # system settings read once per call above and the current
        # time the idle window runs off is read once per call,
        # through the module's _utcnow().
        rows.extend(_standing_grant_run_rows(stored_settings, _utcnow()))
    except Exception:
        sources_failed.append("standing_grants")

    try:
        # The one call of the basis accuracy per call (the demand
        # basis one period ahead, the horizon the reorder points
        # compare): the demand-basis-switch row, when the measure
        # proposes one.  The effective demand basis and the two
        # settings come from the stored system settings read once
        # per call above — a missing or out-of-range stored value
        # falls back to the registry default, as the other settings
        # do; while the settings read itself failed the source runs
        # off the registry defaults rather than failing.
        rows.extend(_basis_switch_rows(basis_accuracy(bucket_index=1), stored_settings))
    except Exception:
        sources_failed.append("basis")

    rows.sort(key=_sort_key)
    as_of = max(reported_as_ofs) if reported_as_ofs else None
    return as_of, rows, sources_failed


def _acked_rows() -> dict[str, dict]:
    """The stored acknowledgements by key.

    The read is best effort: a failure answers no acknowledgements —
    the list still renders, every row reading unacknowledged.
    """
    try:
        rows = _exception_acks_repo.list()
    except Exception:
        return {}
    return {row["key"]: row for row in rows}


def _first_seen_rows() -> dict[str, date]:
    """The stored first-seen dates by key.

    The read is best effort: a failure answers no first-seen dates —
    the list still renders, every row reading never captured.
    """
    try:
        return _exception_first_seen_repo.list()
    except Exception:
        return {}


# The one-line words of the decision's action, in the words of
# AIERP-D-51, per action.
_PROPOSAL_ACTION_WORDS = {
    "expedite": "speed up an open order",
    "raise_order": "place or increase an order",
    "transfer": "move stock from another site",
}

# The exception kinds an accepted stockout decision answers, by the
# actions that answer them: the four stockout and buffer kinds any of
# the three actions, the two expedite kinds only an expedite.  Every
# other kind has no rule and is never answered.
_PROPOSAL_KIND_ACTIONS = {
    "buffer_stocked_out": ("expedite", "raise_order", "transfer"),
    "buffer_critical": ("expedite", "raise_order", "transfer"),
    "stockout_likely": ("expedite", "raise_order", "transfer"),
    "stockout_at_risk": ("expedite", "raise_order", "transfer"),
    "expedite_after_stockout": ("expedite",),
    "expedite_late": ("expedite",),
}


def _current_stockout_decisions() -> list[dict] | None:
    """The item-sites' current stockout decisions, the one read.

    Every current decision — the latest of each item-site by
    ``decided_at``, ``id`` breaking ties, the decision the Work queue
    shows on ``GET /api/stockout`` — read once through
    ``_stockout_decisions_repo.current_all()``.  None when the read
    raised: the read is never a source (it adds no row and is never
    listed in ``sources_failed``), so a failure simply answers no
    proposal.
    """
    try:
        return _stockout_decisions_repo.current_all()
    except Exception:
        return None


def _row_proposal(
    row: ExceptionRow, by_item_site: dict[tuple, dict] | None
) -> ExceptionProposal | None:
    """The row's proposal: its item-site's current stockout decision
    when the decision already chose the remedy the row points at.

    ``by_item_site`` maps ``(item_code, site_code)`` to the
    item-site's current decision; it is None when the read raised, in
    which case no row has a proposal.  Answered only when the row's
    kind is one the decision answers (``_PROPOSAL_KIND_ACTIONS``),
    the decision is ``accepted`` with an action that answers the
    kind, and the decision was recorded on or after the day the row
    was first seen — the UTC date of its ``decided_at``, read through
    the module's ``_aware_utc`` (a value with no time zone taken as
    UTC), on or after the row's ``first_seen_on``.  A row with a null
    ``item_code`` or ``site_code``, a null ``first_seen_on``, a kind
    no rule answers, an item-site with no current decision, a
    dismissed or snoozed decision, an action that does not answer the
    row's kind or is none of the three, a decision recorded before
    the first-seen day, or a decision whose ``decided_at`` carries no
    time has none.  The row's own acknowledgement is looked at not at
    all: an acknowledged row carries its proposal like any other row.
    """
    if by_item_site is None:
        return None
    if row.item_code is None or row.site_code is None:
        return None
    if row.first_seen_on is None:
        return None
    answering = _PROPOSAL_KIND_ACTIONS.get(row.kind)
    if answering is None:
        return None
    decision = by_item_site.get((row.item_code, row.site_code))
    if decision is None or decision.get("decision") != "accepted":
        return None
    action = decision.get("action")
    if action not in answering:
        return None
    decided_at = _aware_utc(decision.get("decided_at"))
    if decided_at is None or decided_at.date() < row.first_seen_on:
        return None
    record_id = decision.get("id")
    if not record_id:
        return None
    return ExceptionProposal(
        record_kind="stockout_decision",
        record_id=record_id,
        decision=decision["decision"],
        action=action,
        recorded_at=decided_at,
        recorded_by_name=decision.get("decided_by_name"),
        reason=(
            "This item-site's stockout decision, accepted on "
            f"{decided_at.date().isoformat()}, already chose to "
            f"{_PROPOSAL_ACTION_WORDS[action]}."
        ),
    )


def _ack_proposal(row: ExceptionRow) -> dict | None:
    """The proposal the list answers for the row at this moment, in JSON.

    The very rule the list runs, on the row with its first-seen date
    from the store: the first-seen dates read once through
    ``_first_seen_rows()`` and the row's ``first_seen_on`` set from
    them, the current stockout decisions read once through
    ``_current_stockout_decisions()`` and mapped by ``(item_code,
    site_code)`` exactly as ``list_exceptions`` maps them (None when
    the read answers None), and ``_row_proposal`` the very rule the
    list runs.  ``proposal.model_dump(mode="json")`` when the rule
    answers a proposal, None when it answers None or an exception is
    raised anywhere: a failed read gives an event whose proposal is
    null, and the event never fails the acknowledgement.
    """
    try:
        first_seen = _first_seen_rows()
        with_first_seen = row.model_copy(
            update={"first_seen_on": first_seen.get(row.key)}
        )
        decisions = _current_stockout_decisions()
        by_item_site = (
            None
            if decisions is None
            else {
                (decision.get("item_code"), decision.get("site_code")): decision
                for decision in decisions
            }
        )
        proposal = _row_proposal(with_first_seen, by_item_site)
        if proposal is None:
            return None
        return proposal.model_dump(mode="json")
    except Exception:
        return None


@router.get("", response_model=ExceptionResponse)
def list_exceptions(
    include_snoozed: bool = False, include_proposal: bool = False
) -> dict:
    """The exceptions triage list of the sixteen sources.

    Open to any signed-in user (the router is mounted behind the
    app's authentication gate).  Each source is read once per call;
    a source that raises is caught and listed in ``sources_failed``
    while the other sources still answer (status 200).  The rows are
    ordered high before medium, then kind order, then item_code and
    key; ``as_of`` is the latest as-of date reported by any
    successful source (null when none reports one).

    The order minimums source reads the stored supplier order
    minimums once per call and, only when at least one is stored,
    builds the 7-day order plan (the horizon ``GET /api/supply-plan``
    and the Order plan screen default to), through
    ``build_supply_plan``: one row per plan supplier group that
    carries a minimum and a shortfall above 0 and holds a line whose
    ``due_in_days`` is at most the
    ``exceptions.order_minimum_due_days`` system setting (default
    2), kind "order_below_minimum", severity "high" when one of the
    group's lines has a ``due_in_days`` of 0, else "medium", the key
    "order_below_minimum:<source_system>:<supplier_code>",
    ``item_code`` and ``site_code`` null, title "Order below
    supplier minimum", the detail "The order to <name> comes to
    <total_value>, <shortfall> below its minimum of <minimum>;
    <item_code> at <site_code> must be ordered by <order_by>."
    (the name the group's name, else its supplier code; the line
    named the group's first; the amounts dollars and cents with
    thousands separators, ``order_by`` read ``YYYY-MM-DD``), view
    "#/orders" and ``detected_on`` the plan's ``as_of`` (the plan's
    ``as_of`` is the rows' ``detected_on`` only, never the
    response's ``as_of``; a failure of the minimums read or of the
    plan build fails the source, listed "order_minimums").

    The standing grants source reads the grants in force, once per
    call for each of ``order_plan_release`` and
    ``transfer_release``, and each process's latest standing-grant
    run: with the ``erp.standing_grants_enabled`` switch from the
    stored system settings stored false (a missing value, or one
    that is not a boolean, counts as on) it raises nothing; a
    latest run under the grant in force that failed, that skipped,
    or that released with a ``left_out`` that is not empty is one
    row, and a grant whose time (the later of its ``created_at``
    and the latest run's ``started_at`` under the grant, or
    ``created_at`` alone while the process has no run under the
    grant) is more than ``STANDING_GRANT_IDLE_HOURS`` (26 — one
    daily sync plus two hours) hours before the current UTC time is
    one idle row, whatever the latest run's outcome: kind
    "standing_grant_run", severity "high" for a failed run and for
    the idle row, else "medium", ``item_code`` and ``site_code``
    null, title "Standing grant run needs attention", view
    "#/releases", the key "standing_grant_run:<run id>" (the idle
    row's key "standing_grant_run:idle:<grant id>:<the latest run
    id under the grant, or 0>") and ``detected_on`` the run's UTC
    date (the idle row's the grant's time plus 26 hours) — neither
    ever the response's ``as_of``; a failure of the grants read or
    of the runs read fails the source, listed
    "standing_grants".

    The basis source calls ``basis_accuracy(bucket_index=1)`` once
    per call (the demand basis one period ahead, the horizon the
    reorder points compare) and, from the stored system settings the
    list already reads once, the effective
    ``replenishment.demand_basis`` and the two settings the switch
    applies — the number settings through ``setting_value`` (a
    missing or out-of-range stored value falling back to the
    registry default, as the other settings do) and the basis the
    way the reorder point reads it, never through ``setting_value``:
    the route persists no demand basis of its own.  One row, kind
    "demand_basis_switch" (medium), the key
    "demand_basis_switch:<the proposed basis>", ``item_code`` and
    ``site_code`` null, title "Demand basis to review", the detail
    "One period ahead over <N> periods, the <proposed basis> was
    <Z> points nearer what sold than the <basis in use>, which
    reorder points use. An admin may switch the demand basis to the
    <proposed basis> in Settings." (N the measure's compared
    buckets, with thousands separators, Z the absolute
    ``value_added`` times 100, to one decimal), view "#/settings"
    and ``detected_on`` the measure's ``as_of`` — never the
    response's ``as_of`` — when the measure's ``compared_buckets``
    is at least the ``exceptions.basis_switch_min_periods`` system
    setting (default 60) and the demand basis not in use was nearer
    what sold, one period ahead, by at least the
    ``exceptions.basis_switch_margin`` system setting (default 5)
    points: the in-use basis ``history`` with ``value_added`` times
    100 at least the margin proposes ``forecast``, the in-use basis
    ``forecast`` with minus ``value_added`` times 100 at least the
    margin proposes ``history``, a null ``value_added`` proposes
    neither (a settings read failure runs the source off the
    registry defaults, never failing it on that account).

    Every row carries the stored acknowledgement of its key, when
    there is one: ``acknowledged`` (true) plus
    ``acknowledged_by_name``, ``acknowledged_at`` and
    ``snooze_until`` — all null or false while the key is
    unacknowledged.  A row snoozed until a date after the as-of date
    is left out of the rows and the counts unless the request passes
    ``include_snoozed=true``, and acknowledgements whose key no
    longer appears in the list are simply ignored.  ``counts`` count
    only the rows returned and carry ``acknowledged``.  Nothing is
    written and no ERP client is called.

    Every row also carries its first-seen date from the
    ``exception_first_seen`` table, read once per call and never
    written: ``first_seen_on`` (the date the key was first captured,
    or null when it has never been captured) and ``new`` (true only
    when ``first_seen_on`` equals the response's as-of date), and
    ``counts`` carries ``new``, the number of returned rows whose
    ``new`` is true.  A failure reading the table is caught: every
    row then reads ``first_seen_on`` null and ``new`` false, and the
    route still answers 200.

    While ``include_proposal`` is false (the default) the answer is
    exactly that, key for key: no row carries a ``proposal`` key and
    the stockout decisions are not read.  While it is true and at
    least one row is returned, every current stockout decision is
    read once — the latest of each item-site by ``decided_at``,
    ``id`` breaking ties, the decision the Work queue shows on
    ``GET /api/stockout`` — through
    ``_stockout_decisions_repo.current_all()``, and every returned
    row carries ``proposal``, an ``ExceptionProposal`` (
    ``record_kind`` ``stockout_decision``, the only record kind of
    this slice, ``record_id`` the decision's id, ``decision`` and
    ``action`` the decision's own, ``recorded_at`` its ``decided_at``
    in UTC, ``recorded_by_name`` its ``decided_by_name`` and the
    one-line ``reason``) or null when nothing answers the row.  The
    rule runs on each returned row, after the snoozed rows are left
    out and each row's acknowledgement and ``first_seen_on`` are
    attached: a row of kind ``stockout_likely``,
    ``stockout_at_risk``, ``buffer_stocked_out`` or
    ``buffer_critical`` is answered when its item-site's current
    decision is ``accepted`` with an action of ``expedite``,
    ``raise_order`` or ``transfer``, a row of kind
    ``expedite_late`` or ``expedite_after_stockout`` only when the
    decision is ``accepted`` with the action ``expedite``, and the
    decision must be recorded on or after the day the row was first
    seen — the UTC date of its ``decided_at`` (read through the
    module's ``_aware_utc``, a value with no time zone taken as UTC)
    on or after the row's ``first_seen_on``.  A row with a null
    ``item_code`` or ``site_code``, a null ``first_seen_on``, a kind
    no rule answers, an item-site with no current decision, a
    dismissed or snoozed decision, an action that does not answer
    the row's kind or is none of the three, a decision recorded
    before the first-seen day, or a decision whose ``decided_at``
    carries no time has no proposal, and the row's own
    acknowledgement is looked at not at all: an acknowledged row
    carries its proposal like any other row.  The read is never a
    source (it adds no row and is never listed in
    ``sources_failed``); with no row returned it is not called, and
    a read that raises is caught — every row's ``proposal`` is then
    null and the route still answers 200 with the rows, the counts
    and the ``sources_failed`` it answers without the parameter.
    Nothing is written and no ERP client is called.
    """
    as_of, rows, sources_failed = _collect()
    acks = _acked_rows()
    first_seen = _first_seen_rows()
    returned: list[ExceptionRow] = []
    for row in rows:
        ack = acks.get(row.key)
        snoozed = (
            ack is not None
            and as_of is not None
            and ack["snooze_until"] is not None
            and ack["snooze_until"] > as_of
        )
        if snoozed and not include_snoozed:
            continue
        first_seen_on = first_seen.get(row.key)
        answered = row.model_copy(
            update={
                "acknowledged": ack is not None,
                "acknowledged_by_name": (
                    ack["acknowledged_by_name"] if ack is not None else None
                ),
                "acknowledged_at": (
                    ack["acknowledged_at"].isoformat()
                    if ack is not None and ack["acknowledged_at"] is not None
                    else None
                ),
                "snooze_until": (ack["snooze_until"] if ack is not None else None),
                "first_seen_on": first_seen_on,
                "new": as_of is not None and first_seen_on == as_of,
            }
        )
        answered._include_proposal = include_proposal
        returned.append(answered)
    if include_proposal and returned:
        decisions = _current_stockout_decisions()
        by_item_site = (
            None
            if decisions is None
            else {
                (decision.get("item_code"), decision.get("site_code")): decision
                for decision in decisions
            }
        )
        for row in returned:
            row.proposal = _row_proposal(row, by_item_site)
    counts = ExceptionCounts(
        high=sum(1 for row in returned if row.severity == "high"),
        medium=sum(1 for row in returned if row.severity == "medium"),
        acknowledged=sum(1 for row in returned if row.acknowledged),
        new=sum(1 for row in returned if row.new),
    )
    return {
        "as_of": as_of,
        "counts": counts,
        "rows": returned,
        "sources_failed": sources_failed,
    }


def capture_exception_first_seen() -> dict:
    """Capture the first-seen date of every key in the current list.

    The current exceptions list is built through the very same
    ``_collect`` the GET route uses, snoozed rows included, and every
    row's key is stored in ``exception_first_seen`` with the list's
    ``as_of`` date — or the current UTC date when the list reports
    none.  A key that already has a stored date is left untouched
    (``ON CONFLICT DO NOTHING``), so a capture the same day inserts
    nothing.  Returns ``{"captured_on": <the stored date, as an ISO
    date>, "inserted": <the number of keys inserted>}``.
    """
    as_of, rows, _ = _collect()
    captured_on = as_of if as_of is not None else _utcnow().date()
    inserted = _exception_first_seen_repo.insert_missing(
        [row.key for row in rows],
        captured_on,
    )
    return {"captured_on": captured_on.isoformat(), "inserted": inserted}


def _require_storable_ack_actor(user: AuthUser) -> None:
    """The token subject and its display name are stored in
    String(255) columns: a hostile token whose values would not fit
    answers 422 instead of a database error.  An absent display name
    is not hostile: it is stored as null, so only a name that would
    not fit the column refuses."""
    display = user.display_name
    if len(user.sub) > _ACK_MAX_LENGTH or (
        display is not None and len(display) > _ACK_MAX_LENGTH
    ):
        raise HTTPException(
            status_code=422,
            detail="the token identity does not fit the acknowledgement store",
        )


@router.put(
    "/ack",
    response_model=AckRow,
    responses={404: {"description": "The key is not in the current exceptions list."}},
)
def acknowledge_exception(
    body: AckBody,
    user: AuthUser = Depends(require_role("admin")),
) -> dict:
    """Store or replace the acknowledgement of one current exception.

    Admin only — the same ``require_role("admin")`` the planner
    decisions use: a non-admin write answers 403 and no token 401.
    The body carries the exception's stable key plus the optional
    snooze and note; an empty key, a NUL byte or a key that would not
    fit the stored column answers 422, a ``snooze_until`` in the past
    (against the current UTC date at request time, never the response
    as-of date) answers 422, and a key that is not in the current
    exceptions list answers 404.  ``acknowledged_by`` is the token
    subject and ``acknowledged_by_name`` its display name.  The
    response is the stored row: a second PUT of the key replaces the
    row — the new subject and name, a fresh ``acknowledged_at``, and
    the request's snooze and note (an omitted ``snooze_until`` or
    ``note`` clears the stored value).
    """
    _require_storable_ack_actor(user)
    if body.snooze_until is not None and body.snooze_until < _utcnow().date():
        raise HTTPException(
            status_code=422,
            detail="snooze_until must not be in the past (current UTC date)",
        )
    _, current_rows, _ = _collect()
    if body.key not in {row.key for row in current_rows}:
        raise HTTPException(
            status_code=404,
            detail="key is not in the current exceptions list",
        )
    list_row = next(row for row in current_rows if row.key == body.key)
    # The proposal the list answers for the row at this moment — the
    # first-seen read and the decisions read each running once, the
    # very rule the list runs: the event's snapshot of the
    # acknowledgement, worked out before the row is stored.
    proposal = _ack_proposal(list_row)
    stored = _exception_acks_repo.upsert(
        key=body.key,
        acknowledged_by=user.sub,
        acknowledged_by_name=user.display_name,
        snooze_until=body.snooze_until,
        note=body.note,
    )
    # The append-only history of the stored acknowledgement: storing
    # it never fails the acknowledgement — an exception is swallowed
    # (a failed insert logs a warning with the key, so the
    # under-counting is visible in the logs) and the route answers
    # the stored row as today.
    with contextlib.suppress(Exception):
        try:
            _exception_ack_events_repo.insert(
                key=stored["key"],
                kind=list_row.kind,
                item_code=list_row.item_code,
                site_code=list_row.site_code,
                acknowledged_by=stored["acknowledged_by"],
                acknowledged_by_name=stored["acknowledged_by_name"],
                acknowledged_at=stored["acknowledged_at"],
                snooze_until=stored["snooze_until"],
                proposal=proposal,
            )
        except Exception as exc:
            logger.warning(
                "the acknowledgement event of %s was not stored: %s",
                stored["key"],
                exc,
            )
    return stored


@router.delete(
    "/ack",
    status_code=204,
    responses={404: {"description": "No acknowledgement is stored for the key."}},
)
def remove_acknowledgement(
    key: str,
    user: AuthUser = Depends(require_role("admin")),
) -> Response:
    """Remove the acknowledgement stored for one key.

    Admin only — the same ``require_role("admin")`` the planner
    decisions use: a non-admin write answers 403 and no token 401.
    Answers 204 when the key's acknowledgement is removed and 404
    when no acknowledgement is stored for the key — a key that is no
    longer in the exceptions list keeps its acknowledgement until it
    is removed.
    """
    if "\x00" in key or not _exception_acks_repo.delete(key):
        raise HTTPException(
            status_code=404,
            detail="no acknowledgement stored for this key",
        )
    return Response(status_code=204)


class ExceptionProposalBucket(BaseModel):
    """One exception kind's stored acknowledgement events: their
    number and the number of them whose proposal is set."""

    acknowledged: int
    proposed: int


class ExceptionProposalCounts(BaseModel):
    """The stored acknowledgement events of the exceptions list,
    counted: every event counts once, a snooze and a second
    acknowledgement of a key included; ``proposed`` plus
    ``without_proposal`` equals ``acknowledged``, and the buckets of
    ``by_kind`` add up to ``acknowledged`` and ``proposed``."""

    acknowledged: int
    proposed: int
    without_proposal: int
    snoozed: int
    snoozed_proposed: int
    by_kind: dict[str, ExceptionProposalBucket]


@router.get("/proposals", response_model=ExceptionProposalCounts)
def exception_proposal_counts() -> dict:
    """Count the stored acknowledgement events of the exceptions list.

    Open to any signed-in user (the router is mounted behind the
    app's authentication gate; the route takes no dependency of its
    own).  Over every row of the append-only ``exception_ack_events``
    table — one per stored acknowledgement, a snooze and a second
    acknowledgement of a key among them, acknowledgements stored
    before the events existed absent — read once through
    ``_exception_ack_events_repo.counts()`` (one SELECT, grouped by
    kind, by the snooze and by the proposal): ``acknowledged`` is
    their number, ``proposed`` and ``without_proposal`` those whose
    proposal is set and those whose is null, ``snoozed`` and
    ``snoozed_proposed`` those whose snooze is set and those of them
    whose proposal is set too, and ``by_kind`` one
    ``ExceptionProposalBucket`` per kind that has at least one stored
    event — ``acknowledged`` that kind's events and ``proposed``
    those of them whose proposal is set.  The route writes nothing
    and calls no ERP.
    """
    acknowledged = 0
    proposed = 0
    snoozed = 0
    snoozed_proposed = 0
    buckets: dict[str, dict[str, int]] = {}
    for group in _exception_ack_events_repo.counts():
        count = int(group["acknowledged"])
        group_proposed = bool(group["proposed"])
        group_snoozed = bool(group["snoozed"])
        acknowledged += count
        if group_proposed:
            proposed += count
        if group_snoozed:
            snoozed += count
            if group_proposed:
                snoozed_proposed += count
        bucket = buckets.setdefault(group["kind"], {"acknowledged": 0, "proposed": 0})
        bucket["acknowledged"] += count
        if group_proposed:
            bucket["proposed"] += count
    return {
        "acknowledged": acknowledged,
        "proposed": proposed,
        "without_proposal": acknowledged - proposed,
        "snoozed": snoozed,
        "snoozed_proposed": snoozed_proposed,
        "by_kind": {
            kind: ExceptionProposalBucket(**bucket) for kind, bucket in buckets.items()
        },
    }
