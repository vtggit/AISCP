"""Issue #1344 proof: the stockout decision that already chose the remedy.

``GET /api/exceptions`` takes ``include_proposal``, a boolean query
parameter, false by default, declared the way ``include_snoozed`` is.
While it is false the route answers exactly as today, key for key: no
row carries a ``proposal`` key and the stockout decisions are not
read.  While it is true and at least one row is returned, the route
reads every current stockout decision once — the latest of each
item-site by ``decided_at``, ``id`` breaking ties, the decision the
Work queue shows on ``GET /api/stockout`` — through
``_stockout_decisions_repo.current_all()`` in ``app.api.exceptions``,
and every returned row carries ``proposal``, an ``ExceptionProposal``
— ``record_kind`` ``stockout_decision``, the only record kind of this
slice, ``record_id`` the decision's id, ``decision`` and ``action``
the decision's own, ``recorded_at`` its ``decided_at`` in UTC,
``recorded_by_name`` its ``decided_by_name`` (null when it has none)
and ``reason`` the one line of text for why the decision answers the
row — or null when nothing answers the row.  The rule runs on each
returned row, after the snoozed rows are left out and each row's
acknowledgement and ``first_seen_on`` are attached: a row of kind
``stockout_likely``, ``stockout_at_risk``, ``buffer_stocked_out`` or
``buffer_critical`` is answered when its item-site's current decision
is ``accepted`` with an action of ``expedite``, ``raise_order`` or
``transfer``, a row of kind ``expedite_late`` or
``expedite_after_stockout`` only when the decision is ``accepted``
with the action ``expedite``, and the decision must be recorded on or
after the day the row was first seen — the UTC date of its
``decided_at`` (a value with no time zone taken as UTC) on or after
the row's ``first_seen_on``; a row with a null ``item_code`` or
``site_code``, a null ``first_seen_on``, a kind no rule answers, an
item-site with no current decision, a dismissed or snoozed decision,
an action that does not answer the row's kind or is none of the
three, or a decision recorded before the first-seen day has none, and
the row's own acknowledgement is looked at not at all.  The read adds
no row and is never listed in ``sources_failed``; with no row
returned it is not called, and a read that raises is caught — every
row's ``proposal`` is then null and the route still answers 200 with
the rows, the counts and the ``sources_failed`` it answers without
the parameter.  The route writes nothing and calls no ERP.

Everything is read through the app's test client, signed in the way
test 1331 signs in, with every read of the route replaced by a
stand-in in ``app.api.exceptions`` that answers fixed data at as-of
2026-11-02, so no test reads the wall clock or the database:

* ``_collect`` answers, on every call, the as-of 2026-11-02, no
  failed source and fresh ``ExceptionRow``s, in this order:
  ``stockout_likely:A/159`` (high), ``stockout_at_risk:B/159``
  (medium), ``buffer_critical:C/159`` (medium),
  ``stockout_likely:D/159`` (high), ``stockout_at_risk:E/159``
  (medium), ``expedite_late:000301/1`` for item F at site 159
  (medium), ``expedite_after_stockout:000401/2`` for item G at site
  159 (high), ``stockout_likely:H/159`` (high),
  ``stockout_likely:A/160`` (high), ``forecast_bias:A/159``
  (medium), ``buffer_stocked_out:J/159`` (high),
  ``stockout_at_risk:K/159`` (medium) and ``release_failed:7``
  (high, ``item_code`` and ``site_code`` null) — each row the
  kind's title from ``_TITLES``, the detail "stand-in", the view
  "#/exceptions" and ``detected_on`` 2026-11-02;
* the first-seen read answers 2026-11-01 for D's row, 2026-10-29
  for F's and G's rows, 2026-11-02 for ``release_failed:7``,
  nothing for H's row and 2026-10-30 for every other key;
* the acknowledgement read answers two acknowledgements: A's
  ``stockout_likely`` row, by "Pat" at 2026-11-01 10:00 UTC with no
  snooze, and C's row, by "Pat" at 2026-11-01 10:00 UTC, snoozed
  until 2026-11-05;
* the decisions read (``_stockout_decisions_repo.current_all``)
  answers eleven current decisions, each a dict with every stored
  column of ``stockout_decisions``: A at 159 accepted with
  ``expedite``, decided 2026-10-31 09:15 UTC by "Pat"; B at 159
  accepted with ``raise_order``, decided 2026-10-31 01:30 at UTC+2
  (2026-10-30 23:30 UTC), ``decided_by_name`` null; C at 159
  accepted with ``transfer``, decided 2026-11-01 08:00 UTC by
  "Pat"; D at 159 accepted with ``expedite``, decided 2026-11-01
  00:30 at UTC+2 (2026-10-31 22:30 UTC); E at 159 dismissed with a
  reason, decided 2026-11-01 09:00 UTC; F at 159 accepted with
  ``expedite``, decided 2026-10-30 10:00 UTC by "Pat"; G at 159
  accepted with ``raise_order``, decided 2026-10-30 10:00 UTC; H at
  159 accepted with ``expedite``, decided 2026-11-01 09:00 UTC; J
  at 159 snoozed until 2026-11-20, decided 2026-11-01 09:00 UTC; K
  at 159 accepted with the stored action "teleport", decided
  2026-11-01 09:00 UTC; and Z at 159 accepted with ``expedite`` —
  an item-site with no row.  A at 160 has no decision.

Proved here:

  * without the parameter the rows are the stand-in rows without
    C's row (snoozed), in the same order, each with exactly today's
    fifteen keys and no ``proposal`` key; A's row reads
    acknowledged by "Pat", H's row reads ``first_seen_on`` null,
    ``release_failed:7`` reads ``new`` true, the counts are 7 high,
    5 medium, 1 acknowledged and 1 new, and the decisions read was
    not called;
  * with it the same rows and the same counts, each row with
    exactly one key more, ``proposal``: A's row (though
    acknowledged) the ``stockout_decision``, A's decision id,
    ``accepted``, ``expedite``, recorded at 2026-10-31 09:15 UTC by
    "Pat"; B's row the ``raise_order`` recorded at 2026-10-30
    23:30 UTC, ``recorded_by_name`` null — the UTC date counts,
    never the decision's own offset, and a decision on the
    first-seen day answers the row; F's ``expedite_late`` row the
    ``expedite`` recorded at 2026-10-30 10:00 UTC by "Pat"; null
    for D's row (decided on 2026-10-31 in UTC, the day before it was
    first seen, although its date at UTC+2 is 2026-11-01), E's
    (dismissed), G's (a raised order does not answer a late order),
    H's (never first seen), A at 160's (no decision at that site),
    A's ``forecast_bias`` row (a kind with no rule), J's (snoozed),
    K's (an action none of the three) and ``release_failed:7`` — each
    ``recorded_at`` parses to the time named, with a zero UTC offset,
    whatever suffix the serializer writes;
  * the snoozed rows — with ``include_snoozed=true`` and
    ``include_proposal=true`` C's row is listed too, acknowledged,
    with its ``transfer`` proposal recorded at 2026-11-01 08:00 UTC
    by "Pat"; the counts are 7 high, 6 medium, 2 acknowledged and
    1 new;
  * the read — each request with the parameter and at least one row
    calls the decisions read exactly once, with no argument; with
    ``_collect`` answering no rows it is not called;
  * the failed reads — the decisions read raising leaves every
    ``proposal`` null, every other key as without the parameter,
    and the first-seen read raising leaves every row
    ``first_seen_on`` null, ``new`` false and ``proposal`` null;
    each answers 200;
  * a bad value — ``include_proposal=xyz`` answers 422;
  * the schema — ``include_proposal`` among the route's query
    parameters (boolean, default false) beside
    ``include_snoozed``, the route's description mentions
    ``include_proposal`` and ``proposal``, ``ExceptionRow``'s
    properties are today's fifteen plus ``proposal``
    (``ExceptionProposal`` or null), ``ExceptionProposal`` has
    exactly the seven keys, ``ExceptionCounts`` keeps exactly
    ``high``, ``medium``, ``acknowledged`` and ``new``, and
    ``include_proposal`` is in neither ``ExceptionRow``'s fields
    nor its schema.

Identity — the proof also runs as a NON-ADMIN (role ``user``,
asserted via ``GET /api/auth/me``) in a second pass after a full
reload (a fresh ``TestClient`` booted the production way — the token
the page would take from the URL hash as ``#access_token=...``, with
``GET /api/auth/config`` answering publicly and ``GET /api/auth/me``
resolving the viewer): the route has no admin-only control — the
viewer reads the whole list with proposals (it renders, the same
answer as the admin's) — and a request without a token still gets
401.  Nothing is written: none of the store writes was called.
"""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.api.exceptions as exceptions_api
from app.api.exceptions import ExceptionCounts, ExceptionRow
from app.main import app

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

URL = "/api/exceptions"
SITE = "159"
AS_OF = date(2026, 11, 2)
UTC_PLUS_2 = timezone(timedelta(hours=2))

# The fifteen keys every row of the triage list answers today.
TODAY_ROW_KEYS = {
    "kind",
    "severity",
    "key",
    "item_code",
    "site_code",
    "title",
    "detail",
    "view",
    "detected_on",
    "acknowledged",
    "acknowledged_by_name",
    "acknowledged_at",
    "snooze_until",
    "first_seen_on",
    "new",
}


def _row(key, kind, severity, item, site):
    """One stand-in exception row: the kind's title from _TITLES,
    the detail "stand-in", the view "#/exceptions" and detected_on
    2026-11-02."""
    return ExceptionRow(
        kind=kind,
        severity=severity,
        key=key,
        item_code=item,
        site_code=site,
        title=exceptions_api._TITLES[kind],
        detail="stand-in",
        view="#/exceptions",
        detected_on=AS_OF,
    )


def _standin_rows():
    """The thirteen stand-in rows, fresh on every call, in order."""
    return [
        _row("stockout_likely:A/159", "stockout_likely", "high", "A", SITE),
        _row("stockout_at_risk:B/159", "stockout_at_risk", "medium", "B", SITE),
        _row("buffer_critical:C/159", "buffer_critical", "medium", "C", SITE),
        _row("stockout_likely:D/159", "stockout_likely", "high", "D", SITE),
        _row("stockout_at_risk:E/159", "stockout_at_risk", "medium", "E", SITE),
        _row("expedite_late:000301/1", "expedite_late", "medium", "F", SITE),
        _row(
            "expedite_after_stockout:000401/2",
            "expedite_after_stockout",
            "high",
            "G",
            SITE,
        ),
        _row("stockout_likely:H/159", "stockout_likely", "high", "H", SITE),
        _row("stockout_likely:A/160", "stockout_likely", "high", "A", "160"),
        _row("forecast_bias:A/159", "forecast_bias", "medium", "A", SITE),
        _row("buffer_stocked_out:J/159", "buffer_stocked_out", "high", "J", SITE),
        _row("stockout_at_risk:K/159", "stockout_at_risk", "medium", "K", SITE),
        _row("release_failed:7", "release_failed", "high", None, None),
    ]


# The first-seen dates: 2026-11-01 for D's row, 2026-10-29 for F's
# and G's rows, 2026-11-02 for release_failed:7, nothing for H's row
# and 2026-10-30 for every other key.
FIRST_SEEN = {
    "stockout_likely:A/159": date(2026, 10, 30),
    "stockout_at_risk:B/159": date(2026, 10, 30),
    "buffer_critical:C/159": date(2026, 10, 30),
    "stockout_likely:D/159": date(2026, 11, 1),
    "stockout_at_risk:E/159": date(2026, 10, 30),
    "expedite_late:000301/1": date(2026, 10, 29),
    "expedite_after_stockout:000401/2": date(2026, 10, 29),
    "stockout_likely:A/160": date(2026, 10, 30),
    "forecast_bias:A/159": date(2026, 10, 30),
    "buffer_stocked_out:J/159": date(2026, 10, 30),
    "stockout_at_risk:K/159": date(2026, 10, 30),
    "release_failed:7": date(2026, 11, 2),
}

# Two acknowledgements: A's stockout_likely row (no snooze) and
# C's row, both by "Pat" at 2026-11-01 10:00 UTC; C's until
# 2026-11-05.
ACKS = [
    {
        "key": "stockout_likely:A/159",
        "acknowledged_by": "pat",
        "acknowledged_by_name": "Pat",
        "acknowledged_at": datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
        "snooze_until": None,
        "note": None,
    },
    {
        "key": "buffer_critical:C/159",
        "acknowledged_by": "pat",
        "acknowledged_by_name": "Pat",
        "acknowledged_at": datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
        "snooze_until": date(2026, 11, 5),
        "note": None,
    },
]


def _decision(
    item,
    site,
    decision,
    action,
    decided_at,
    decided_by_name="Pat",
    snooze_until=None,
    reason=None,
):
    """One current decision: a dict with every stored column of
    stockout_decisions."""
    return {
        "id": f"decision-{item}-{site}",
        "item_code": item,
        "site_code": site,
        "decision": decision,
        "action": action,
        "reason": reason,
        "snooze_until": snooze_until,
        "as_of": AS_OF,
        "category": "likely",
        "p_stockout": 0.9,
        "on_hand_units": 0.0,
        "open_supply_units": 0.0,
        "expected_units_short": 10.0,
        "money_at_risk": 90.0,
        "decided_by": "pat",
        "decided_by_name": decided_by_name,
        "decided_at": decided_at,
    }


DECISIONS = [
    _decision(
        "A", SITE, "accepted", "expedite", datetime(2026, 10, 31, 9, 15, tzinfo=UTC)
    ),
    _decision(
        "B",
        SITE,
        "accepted",
        "raise_order",
        datetime(2026, 10, 31, 1, 30, tzinfo=UTC_PLUS_2),
        decided_by_name=None,
    ),
    _decision(
        "C", SITE, "accepted", "transfer", datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
    ),
    # 2026-11-01 00:30 at UTC+2 is 2026-10-31 22:30 in UTC — the day
    # before D's row was first seen.
    _decision(
        "D",
        SITE,
        "accepted",
        "expedite",
        datetime(2026, 11, 1, 0, 30, tzinfo=UTC_PLUS_2),
    ),
    _decision(
        "E",
        SITE,
        "dismissed",
        None,
        datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
        reason="the demand is seasonal",
    ),
    _decision(
        "F", SITE, "accepted", "expedite", datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
    ),
    _decision(
        "G", SITE, "accepted", "raise_order", datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
    ),
    _decision(
        "H", SITE, "accepted", "expedite", datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    ),
    _decision(
        "J",
        SITE,
        "snoozed",
        None,
        datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
        snooze_until=date(2026, 11, 20),
    ),
    _decision(
        "K", SITE, "accepted", "teleport", datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    ),
    # An item-site with no row.
    _decision(
        "Z", SITE, "accepted", "expedite", datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    ),
]


def _expected_row(
    key,
    kind,
    severity,
    item,
    site,
    acked=False,
    snoozed_until=None,
    first_seen=None,
    new=False,
):
    """The answered shape of one stand-in row, as JSON."""
    return {
        "kind": kind,
        "severity": severity,
        "key": key,
        "item_code": item,
        "site_code": site,
        "title": exceptions_api._TITLES[kind],
        "detail": "stand-in",
        "view": "#/exceptions",
        "detected_on": AS_OF.isoformat(),
        "acknowledged": acked,
        "acknowledged_by_name": "Pat" if acked else None,
        "acknowledged_at": "2026-11-01T10:00:00+00:00" if acked else None,
        "snooze_until": snoozed_until,
        "first_seen_on": first_seen,
        "new": new,
    }


# The twelve rows the answer carries without include_snoozed: the
# stand-in rows without C's row (snoozed until 2026-11-05), in the
# same order.
EXPECTED_ROWS = [
    _expected_row(
        "stockout_likely:A/159",
        "stockout_likely",
        "high",
        "A",
        SITE,
        acked=True,
        first_seen="2026-10-30",
    ),
    _expected_row(
        "stockout_at_risk:B/159",
        "stockout_at_risk",
        "medium",
        "B",
        SITE,
        first_seen="2026-10-30",
    ),
    _expected_row(
        "stockout_likely:D/159",
        "stockout_likely",
        "high",
        "D",
        SITE,
        first_seen="2026-11-01",
    ),
    _expected_row(
        "stockout_at_risk:E/159",
        "stockout_at_risk",
        "medium",
        "E",
        SITE,
        first_seen="2026-10-30",
    ),
    _expected_row(
        "expedite_late:000301/1",
        "expedite_late",
        "medium",
        "F",
        SITE,
        first_seen="2026-10-29",
    ),
    _expected_row(
        "expedite_after_stockout:000401/2",
        "expedite_after_stockout",
        "high",
        "G",
        SITE,
        first_seen="2026-10-29",
    ),
    _expected_row(
        "stockout_likely:H/159",
        "stockout_likely",
        "high",
        "H",
        SITE,
        first_seen=None,
    ),
    _expected_row(
        "stockout_likely:A/160",
        "stockout_likely",
        "high",
        "A",
        "160",
        first_seen="2026-10-30",
    ),
    _expected_row(
        "forecast_bias:A/159",
        "forecast_bias",
        "medium",
        "A",
        SITE,
        first_seen="2026-10-30",
    ),
    _expected_row(
        "buffer_stocked_out:J/159",
        "buffer_stocked_out",
        "high",
        "J",
        SITE,
        first_seen="2026-10-30",
    ),
    _expected_row(
        "stockout_at_risk:K/159",
        "stockout_at_risk",
        "medium",
        "K",
        SITE,
        first_seen="2026-10-30",
    ),
    _expected_row(
        "release_failed:7",
        "release_failed",
        "high",
        None,
        None,
        first_seen="2026-11-02",
        new=True,
    ),
]

# C's row: snoozed until 2026-11-05, acknowledged.
C_ROW = _expected_row(
    "buffer_critical:C/159",
    "buffer_critical",
    "medium",
    "C",
    SITE,
    acked=True,
    snoozed_until="2026-11-05",
    first_seen="2026-10-30",
)

# The rows answered by a proposal, keyed by the row's key.
# ``recorded_at`` is kept out on purpose: it is checked by
# parsing (the time named in RECORDED_ATS, a zero UTC offset),
# never by a hard-coded wire format — the serializer writes a Z
# or a +00:00 suffix depending on the pydantic version.
PROPOSALS = {
    "stockout_likely:A/159": {
        "record_kind": "stockout_decision",
        "record_id": "decision-A-159",
        "decision": "accepted",
        "action": "expedite",
        "recorded_by_name": "Pat",
        "reason": (
            "This item-site's stockout decision, accepted on 2026-10-31, "
            "already chose to speed up an open order."
        ),
    },
    "stockout_at_risk:B/159": {
        "record_kind": "stockout_decision",
        "record_id": "decision-B-159",
        "decision": "accepted",
        "action": "raise_order",
        "recorded_by_name": None,
        "reason": (
            "This item-site's stockout decision, accepted on 2026-10-30, "
            "already chose to place or increase an order."
        ),
    },
    "expedite_late:000301/1": {
        "record_kind": "stockout_decision",
        "record_id": "decision-F-159",
        "decision": "accepted",
        "action": "expedite",
        "recorded_by_name": "Pat",
        "reason": (
            "This item-site's stockout decision, accepted on 2026-10-30, "
            "already chose to speed up an open order."
        ),
    },
    "buffer_critical:C/159": {
        "record_kind": "stockout_decision",
        "record_id": "decision-C-159",
        "decision": "accepted",
        "action": "transfer",
        "recorded_by_name": "Pat",
        "reason": (
            "This item-site's stockout decision, accepted on 2026-11-01, "
            "already chose to move stock from another site."
        ),
    },
}

# Every other returned row reads a null proposal.
NULL_PROPOSAL_KEYS = {
    "stockout_likely:D/159",
    "stockout_at_risk:E/159",
    "expedite_after_stockout:000401/2",
    "stockout_likely:H/159",
    "stockout_likely:A/160",
    "forecast_bias:A/159",
    "buffer_stocked_out:J/159",
    "stockout_at_risk:K/159",
    "release_failed:7",
}

# The time each non-null recorded_at must parse to, in UTC.
RECORDED_ATS = {
    "stockout_likely:A/159": datetime(2026, 10, 31, 9, 15, tzinfo=UTC),
    "stockout_at_risk:B/159": datetime(2026, 10, 30, 23, 30, tzinfo=UTC),
    "expedite_late:000301/1": datetime(2026, 10, 30, 10, 0, tzinfo=UTC),
    "buffer_critical:C/159": datetime(2026, 11, 1, 8, 0, tzinfo=UTC),
}


def _assert_row(expected, row):
    """One answered row, checked key for key.

    The row's keys read exactly as expected and its
    ``proposal`` is the expected one: the six keys exact, and
    ``recorded_at`` checked by parsing — it must parse to the
    time named in RECORDED_ATS with a zero UTC offset, whatever
    suffix (Z or +00:00) the serializer writes — or null for the
    rows nothing answers."""
    assert {
        key: value for key, value in row.items() if key != "proposal"
    } == expected, row
    proposal = row["proposal"]
    if expected["key"] in PROPOSALS:
        expected_proposal = PROPOSALS[expected["key"]]
        assert proposal is not None, row
        assert set(proposal) == set(expected_proposal) | {"recorded_at"}, row
        assert {
            field: value for field, value in proposal.items() if field != "recorded_at"
        } == expected_proposal, row
        recorded = datetime.fromisoformat(proposal["recorded_at"])
        assert recorded.utcoffset() == timedelta(0)
        assert recorded == RECORDED_ATS[expected["key"]]
    else:
        assert proposal is None, row


class _Read:
    """The stand-in for one read.

    Answers a fixed payload (or raises) and records every call's
    arguments, so a test can see when the read ran and with what.
    """

    def __init__(self, answer, raises=None):
        self.answer = answer
        self.raises = raises
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.answer


class _Collect:
    """The stand-in _collect: on every call the as-of 2026-11-02, no
    failed source and fresh ExceptionRows, recording each call."""

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else _standin_rows
        self.calls = []

    def __call__(self):
        self.calls.append(())
        built = self.rows() if callable(self.rows) else self.rows
        return AS_OF, built, []


def test_issue1344_freeform(client, monkeypatch):
    # -- The stand-ins: every read of the route, in app.api.exceptions. --
    collect = _Collect()
    decisions = _Read(DECISIONS)
    first_seen = _Read(FIRST_SEEN)
    acks = _Read(ACKS)
    monkeypatch.setattr(exceptions_api, "_collect", collect)
    monkeypatch.setattr(
        exceptions_api._stockout_decisions_repo, "current_all", decisions
    )
    monkeypatch.setattr(exceptions_api._exception_first_seen_repo, "list", first_seen)
    monkeypatch.setattr(exceptions_api._exception_acks_repo, "list", acks)

    # -- Nothing is written: every store write fails the test. ----------
    def _no_write(*args, **kwargs):
        pytest.fail("the route wrote to a store")

    monkeypatch.setattr(exceptions_api._exception_acks_repo, "upsert", _no_write)
    monkeypatch.setattr(exceptions_api._exception_acks_repo, "delete", _no_write)
    monkeypatch.setattr(
        exceptions_api._exception_first_seen_repo, "insert_missing", _no_write
    )

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    anon = TestClient(app)

    # -- Without the parameter: exactly today's answer. -----------------
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] == "2026-11-02"
    assert body["sources_failed"] == []
    assert body["rows"] == EXPECTED_ROWS
    for row in body["rows"]:
        assert set(row) == TODAY_ROW_KEYS
        assert "proposal" not in row
    by_key = {row["key"]: row for row in body["rows"]}
    # A's row reads acknowledged by "Pat", H's row reads
    # first_seen_on null, release_failed:7 reads new true.
    assert by_key["stockout_likely:A/159"]["acknowledged"] is True
    assert by_key["stockout_likely:A/159"]["acknowledged_by_name"] == "Pat"
    assert by_key["stockout_likely:A/159"]["acknowledged_at"] == (
        "2026-11-01T10:00:00+00:00"
    )
    assert by_key["stockout_likely:H/159"]["first_seen_on"] is None
    assert by_key["release_failed:7"]["new"] is True
    assert body["counts"] == {"high": 7, "medium": 5, "acknowledged": 1, "new": 1}
    # The decisions read was not called.
    assert decisions.calls == []

    # -- With it: the same rows and counts, each row one key more. ------
    response = admin.get(URL, params={"include_proposal": "true"})
    assert response.status_code == 200, response.text
    proposal_body = response.json()
    assert proposal_body["as_of"] == "2026-11-02"
    assert proposal_body["sources_failed"] == []
    assert proposal_body["counts"] == {
        "high": 7,
        "medium": 5,
        "acknowledged": 1,
        "new": 1,
    }
    assert len(proposal_body["rows"]) == len(EXPECTED_ROWS)
    for expected, row in zip(EXPECTED_ROWS, proposal_body["rows"]):
        assert set(row) == TODAY_ROW_KEYS | {"proposal"}
        _assert_row(expected, row)
    # Null for every other row: no decision at the item-site, a
    # dismissed or snoozed decision, an action none of the three or
    # that does not answer the kind, a decision recorded before the
    # first-seen day, a never-seen row, and every other kind.
    assert {
        row["key"] for row in proposal_body["rows"] if row["proposal"] is None
    } == NULL_PROPOSAL_KEYS
    # The decisions read: once for the request, with no argument.
    assert decisions.calls == [((), {})]

    # -- The snoozed rows: C's row is listed too, with its proposal. ----
    response = admin.get(
        URL, params={"include_snoozed": "true", "include_proposal": "true"}
    )
    assert response.status_code == 200, response.text
    snoozed_body = response.json()
    assert snoozed_body["counts"] == {
        "high": 7,
        "medium": 6,
        "acknowledged": 2,
        "new": 1,
    }
    c_row = next(
        row for row in snoozed_body["rows"] if row["key"] == "buffer_critical:C/159"
    )
    _assert_row(C_ROW, c_row)
    assert c_row["acknowledged"] is True
    for expected in EXPECTED_ROWS:
        row = next(r for r in snoozed_body["rows"] if r["key"] == expected["key"])
        _assert_row(expected, row)
    assert decisions.calls == [((), {}), ((), {})]

    # -- No row returned: the decisions are not read. -------------------
    monkeypatch.setattr(exceptions_api, "_collect", _Collect(rows=[]))
    response = admin.get(URL, params={"include_proposal": "true"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "as_of": "2026-11-02",
        "counts": {"high": 0, "medium": 0, "acknowledged": 0, "new": 0},
        "rows": [],
        "sources_failed": [],
    }
    assert decisions.calls == [((), {}), ((), {})]
    monkeypatch.setattr(exceptions_api, "_collect", collect)

    # -- A failed read: the decisions read raises. ----------------------
    decisions_down = _Read(
        None, raises=RuntimeError("the stockout decisions read is down")
    )
    monkeypatch.setattr(
        exceptions_api._stockout_decisions_repo, "current_all", decisions_down
    )
    response = admin.get(URL, params={"include_proposal": "true"})
    assert response.status_code == 200, response.text
    failed_body = response.json()
    assert failed_body["sources_failed"] == []
    assert failed_body["counts"] == {
        "high": 7,
        "medium": 5,
        "acknowledged": 1,
        "new": 1,
    }
    # Every row's proposal is null and every other key reads as
    # without the parameter.
    for expected, row in zip(EXPECTED_ROWS, failed_body["rows"]):
        assert row == {**expected, "proposal": None}
    assert decisions_down.calls == [((), {})]
    monkeypatch.setattr(
        exceptions_api._stockout_decisions_repo, "current_all", decisions
    )

    # -- A failed read: the first-seen read raises. ----------------------
    first_seen_down = _Read(None, raises=RuntimeError("the first-seen read is down"))
    monkeypatch.setattr(
        exceptions_api._exception_first_seen_repo, "list", first_seen_down
    )
    response = admin.get(URL, params={"include_proposal": "true"})
    assert response.status_code == 200, response.text
    unseen_body = response.json()
    assert unseen_body["counts"] == {
        "high": 7,
        "medium": 5,
        "acknowledged": 1,
        "new": 0,
    }
    for expected, row in zip(EXPECTED_ROWS, unseen_body["rows"]):
        assert row["first_seen_on"] is None
        assert row["new"] is False
        assert row["proposal"] is None
        # Every other key reads as without the parameter.
        assert {
            key: value
            for key, value in row.items()
            if key not in ("proposal", "first_seen_on", "new")
        } == {
            key: value
            for key, value in expected.items()
            if key not in ("proposal", "first_seen_on", "new")
        }
    assert first_seen_down.calls == [((), {})]
    monkeypatch.setattr(exceptions_api._exception_first_seen_repo, "list", first_seen)

    # -- A bad value: include_proposal is a plain boolean query
    #    parameter, a non-boolean answers 422, never 500. ----------------
    assert admin.get(URL, params={"include_proposal": "xyz"}).status_code == 422

    # -- The schema: the parameter, the proposal property and the
    #    ExceptionProposal keys; ExceptionCounts gains nothing. ----------
    spec = app.openapi()
    get_exceptions = spec["paths"][URL]["get"]
    parameters = {param["name"]: param for param in get_exceptions["parameters"]}
    include_proposal = parameters["include_proposal"]
    assert include_proposal["in"] == "query"
    assert include_proposal["schema"]["type"] == "boolean"
    assert include_proposal["schema"]["default"] is False
    # Beside include_snoozed, the way it is declared.
    assert "include_snoozed" in parameters
    # The route's description documents the parameter and the field.
    assert "include_proposal" in get_exceptions["description"]
    assert "proposal" in get_exceptions["description"]
    row_schema = spec["components"]["schemas"]["ExceptionRow"]
    proposal_property = row_schema["properties"]["proposal"]
    assert {"$ref": "#/components/schemas/ExceptionProposal"} in proposal_property.get(
        "anyOf", []
    ), proposal_property
    assert {"type": "null"} in proposal_property.get("anyOf", []), proposal_property
    # ExceptionRow's properties are today's fifteen plus proposal.
    assert set(row_schema["properties"]) == TODAY_ROW_KEYS | {"proposal"}
    proposal_schema = spec["components"]["schemas"]["ExceptionProposal"]
    assert set(proposal_schema["properties"]) == {
        "record_kind",
        "record_id",
        "decision",
        "action",
        "recorded_at",
        "recorded_by_name",
        "reason",
    }
    counts_schema = spec["components"]["schemas"]["ExceptionCounts"]
    assert set(counts_schema["properties"]) == {
        "high",
        "medium",
        "acknowledged",
        "new",
    }
    # The parameter is part of neither the model's fields nor its
    # schema (it rides on the answered row as private state the
    # serializer reads).
    assert set(ExceptionRow.model_fields) == TODAY_ROW_KEYS | {"proposal"}
    assert "include_proposal" not in ExceptionRow.model_fields
    assert "include_proposal" not in row_schema["properties"]
    assert "include_proposal" not in ExceptionRow.model_json_schema()["properties"]
    assert set(ExceptionCounts.model_fields) == {
        "high",
        "medium",
        "acknowledged",
        "new",
    }

    # -- Identity, second pass after a full reload: the route has no
    #    admin-only control.  The page boots the production way for the
    #    non-admin (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the viewer reads the whole
    #    list with proposals (it renders, the same answer as the
    #    admin's) — and a request without a token still gets 401. ----
    assert TestClient(app).get("/api/auth/config").status_code == 200
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == VIEWER_SUB
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]
    response = viewer.get(URL, params={"include_proposal": "true"})
    assert response.status_code == 200, response.text
    assert response.json() == proposal_body
    # The viewer's request read the decisions once more.
    assert decisions.calls == [((), {}), ((), {}), ((), {}), ((), {})]
    assert anon.get(URL, params={"include_proposal": "true"}).status_code == 401
