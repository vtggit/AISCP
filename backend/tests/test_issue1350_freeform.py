"""Issue #1350 proof: the acknowledgement event store and the counts.

``PUT /api/exceptions/ack`` keeps every check it makes today, in the
same order, and still builds the current list exactly once, through
``_collect``.  Once the key is in that list, the route takes the
first row of that same list whose key is the request's key and works
out the proposal ``GET /api/exceptions?include_snoozed=true&include_
proposal=true`` answers for that row at that moment — the first-seen
dates read once through ``_first_seen_rows()``, the current stockout
decisions read once through ``_current_stockout_decisions()`` and
mapped by (item_code, site_code) exactly as the list maps them, and
``_row_proposal``, the very rule the list runs.  It then stores the
acknowledgement through ``_exception_acks_repo.upsert`` exactly as
today, and appends one event to the append-only
``exception_ack_events`` table (one row per stored acknowledgement,
never updated or deleted — a cleared acknowledgement keeps its
rows): the stored row's key, subject, name, time and snooze, the
list row's kind, item and site, and the proposal (jsonb, SQL null
when nothing answered the row or a read failed).  Building or storing
the event never fails the acknowledgement: an exception raised there
is swallowed, a failed insert logs a warning with the key, and the
route answers the stored row as today.  A request the route refuses
(401, 403, 422 or 404) stores no event and reads neither the
first-seen dates nor the decisions.  The PUT's answer is exactly
today's ``AckRow`` — its six keys, no ``proposal`` key — and
``DELETE /api/exceptions/ack`` stores no event.

A new ``GET /api/exceptions/proposals`` route (the route function
``exception_proposal_counts``), on the exceptions router, is open to
every signed-in user through the router's existing gate and takes no
dependency of its own; it writes nothing, calls no ERP and reads the
store once, through ``_exception_ack_events_repo.counts()`` (one
SELECT, grouped by kind, by the snooze and by the proposal),
answering ``ExceptionProposalCounts``: ``acknowledged`` the number of
stored events (every event counts once, a snooze and a second
acknowledgement of a key included — acknowledgements stored before
the events existed stored no row and are not counted), ``proposed``
and ``without_proposal`` those whose proposal is set and those whose
is null, ``snoozed`` and ``snoozed_proposed`` those whose snooze is
set and those of them whose proposal is set too, and ``by_kind`` one
``ExceptionProposalBucket`` — ``acknowledged`` and ``proposed`` — per
kind that has at least one stored event.  ``proposed`` plus
``without_proposal`` equals ``acknowledged``, and the buckets add up
to both; an empty store answers every number 0 and ``by_kind`` {}.

The acknowledgement store and the event store are the real tables
(the event migration creates the event table in the test database,
and storing a proposal without a ``record_kind`` through ``insert``
raises ``psycopg2.errors.CheckViolation`` and stores nothing); every
other read is replaced by the stand-ins of test 1344 in
``app.api.exceptions`` — ``_collect`` answering, on every call, the
as-of 2026-11-02, no failed source and fresh rows (
``stockout_likely:A/159`` high, ``stockout_at_risk:B/159`` medium,
``buffer_critical:C/159`` medium, ``stockout_likely:D/159`` high,
``expedite_late:000301/1`` for item F at site 159 medium,
``expedite_after_stockout:000401/2`` for item G at site 159 high,
``release_failed:7`` high with item and site null), the first-seen
read answering 2026-11-01 for D's row, 2026-10-29 for F's and G's
rows, 2026-11-02 for ``release_failed:7`` and 2026-10-30 for A's,
B's and C's rows, the decisions read answering six current decisions
(A accepted with expedite decided 2026-10-31 09:15 UTC by "Pat", B
accepted with raise_order decided 2026-10-30 23:30 UTC with no name,
C accepted with transfer decided 2026-11-01 08:00 UTC by "Pat", D
accepted with expedite decided 2026-10-31 22:30 UTC, F accepted with
expedite decided 2026-10-30 10:00 UTC by "Pat", G accepted with
raise_order decided 2026-10-30 10:00 UTC) and ``_utcnow`` answering
2026-11-02 12:00 UTC — each read recording its calls, and the
first-seen read, the decisions read and the event insert each made to
raise in turn — so no test reads the wall clock.

Proved here:

* the empty store — before the first PUT, the counts answer exactly
  every number 0 and ``by_kind`` {};
* stored with each acknowledgement — the admin's seven PUTs (A's
  ``stockout_likely`` row with no snooze, B's row snoozed until
  2026-11-09, D's row with no snooze, G's row with no snooze,
  ``release_failed:7`` snoozed until 2026-11-09, A's row again
  snoozed until 2026-11-09 with the note "after the supplier call",
  F's row with no snooze) each answer 200 with exactly the six keys
  of ``AckRow`` and no ``proposal`` key, each stores exactly one new
  event row (read with one SELECT in id order) carrying the PUT
  answer's key, subject, name, time and snooze and the list row's
  kind, item and site, and its proposal equals the proposal the list
  answers for the row just after the PUT, recorded_at compared as a
  parsed time — A's decision (expedite) for both of A's PUTs, B's
  decision (raise_order) and F's decision (expedite), each with
  exactly the seven keys of ``ExceptionProposal``, D's, G's and
  ``release_failed:7``'s rows SQL NULL; each PUT, on its own, called
  ``_collect`` once, the first-seen read once and the decisions read
  once, each with no argument;
* refusals — a PUT of C's row with the user token answers 403 and
  calls none of the three reads, the same PUT with no token 401, a
  PUT of ``nope:404`` 404 (``_collect`` once, neither of the other
  two reads) and a PUT of C's row snoozed until 2026-11-01 422 (none
  of the three reads) — each storing no acknowledgement and no event;
* clearing — ``DELETE /api/exceptions/ack?key=stockout_likely:A/159``
  answers 204 and stores no event;
* never in the way — with the event insert raising, a PUT of C's row
  answers 200 with the stored row, the list then reads C's row
  acknowledged and no event is stored; with the decisions read
  raising, a PUT of C's row answers 200 and stores one event whose
  proposal is SQL null, and the same with the first-seen read
  raising instead; with every stand-in restored, one more PUT of
  C's row stores C's decision (transfer);
* the counts — after all of the above, the counts answer exactly
  acknowledged 10, proposed 5, without_proposal 5, snoozed 3,
  snoozed_proposed 2 and the six by_kind buckets; the user token gets
  the same answer and no token gets 401;
* the schema — the app's OpenAPI document lists
  ``/api/exceptions/proposals`` with ``get`` only, answering
  ``ExceptionProposalCounts`` whose properties are exactly its six
  keys; ``ExceptionProposalBucket`` has exactly ``acknowledged`` and
  ``proposed``; ``AckRow`` and ``AckBody`` keep exactly their keys;
* the migration — the script directory has exactly one head, and
  exactly one revision has the excess-decision-proposals revision as
  its down_revision (the proof does not compare that revision with
  the head, so a later migration leaves it unchanged);
* the identity — a second pass after a full reload, the page booted
  the production way for the non-admin (the token it would take from
  the URL hash as #access_token=..., ``GET /api/auth/config``
  answering publicly and ``GET /api/auth/me`` resolving the viewer):
  the page still renders (the same answer as the admin's) while the
  admin-only controls are absent for the viewer (the ack PUT and the
  ack DELETE answer 403), and no token still gets 401.
"""

import os
from datetime import UTC, date, datetime, timedelta, timezone

import psycopg2
import pytest
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.api.exceptions as exceptions_api
from app.api.exceptions import ExceptionRow
from app.db.connection import get_cursor
from app.main import app
from app.repositories.exception_ack_events_postgres_repository import (
    ExceptionAckEventPostgresRepository,
)

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

URL = "/api/exceptions"
ACK_URL = "/api/exceptions/ack"
COUNTS_URL = "/api/exceptions/proposals"
SITE = "159"
AS_OF = date(2026, 11, 2)
UTC_PLUS_2 = timezone(timedelta(hours=2))

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")

# The six keys the PUT answers (AckRow), exactly.
ACK_ROW_KEYS = {
    "key",
    "acknowledged_by",
    "acknowledged_by_name",
    "acknowledged_at",
    "snooze_until",
    "note",
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
    """The seven stand-in rows, fresh on every call, in order."""
    return [
        _row("stockout_likely:A/159", "stockout_likely", "high", "A", SITE),
        _row("stockout_at_risk:B/159", "stockout_at_risk", "medium", "B", SITE),
        _row("buffer_critical:C/159", "buffer_critical", "medium", "C", SITE),
        _row("stockout_likely:D/159", "stockout_likely", "high", "D", SITE),
        _row("expedite_late:000301/1", "expedite_late", "medium", "F", SITE),
        _row(
            "expedite_after_stockout:000401/2",
            "expedite_after_stockout",
            "high",
            "G",
            SITE,
        ),
        _row("release_failed:7", "release_failed", "high", None, None),
    ]


# The first-seen dates: 2026-11-01 for D's row, 2026-10-29 for F's
# and G's rows, 2026-11-02 for release_failed:7 and 2026-10-30 for
# A's, B's and C's rows.
FIRST_SEEN = {
    "stockout_likely:A/159": date(2026, 10, 30),
    "stockout_at_risk:B/159": date(2026, 10, 30),
    "buffer_critical:C/159": date(2026, 10, 30),
    "stockout_likely:D/159": date(2026, 11, 1),
    "expedite_late:000301/1": date(2026, 10, 29),
    "expedite_after_stockout:000401/2": date(2026, 10, 29),
    "release_failed:7": date(2026, 11, 2),
}


def _decision(item, site, decision, action, decided_at, decided_by_name="Pat"):
    """One current decision: a dict with every stored column of
    stockout_decisions."""
    return {
        "id": f"decision-{item}-{site}",
        "item_code": item,
        "site_code": site,
        "decision": decision,
        "action": action,
        "reason": None,
        "snooze_until": None,
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
        "F", SITE, "accepted", "expedite", datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
    ),
    _decision(
        "G", SITE, "accepted", "raise_order", datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
    ),
]

# The rows answered by a proposal, keyed by the row's key: the six
# keys exact, recorded_at kept out on purpose — it is checked by
# parsing (the time named in RECORDED_ATS), never by a hard-coded
# wire format.
EXPECTED_PROPOSALS = {
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
}

# The time each non-null recorded_at must parse to, in UTC.
RECORDED_ATS = {
    "stockout_likely:A/159": datetime(2026, 10, 31, 9, 15, tzinfo=UTC),
    "stockout_at_risk:B/159": datetime(2026, 10, 30, 23, 30, tzinfo=UTC),
    "buffer_critical:C/159": datetime(2026, 11, 1, 8, 0, tzinfo=UTC),
    "expedite_late:000301/1": datetime(2026, 10, 30, 10, 0, tzinfo=UTC),
}


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
    failed source and fresh rows, recording each call."""

    def __init__(self):
        self.calls = []

    def __call__(self):
        self.calls.append(())
        return AS_OF, _standin_rows(), []


def _stored_events():
    """The stored event rows, one SELECT in id order."""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM exception_ack_events ORDER BY id")
        return [dict(row) for row in cur.fetchall()]


def _check_proposal(stored, listed, key):
    """The stored event's proposal and the proposal the list answers
    for the row just after the PUT, against the expected one: the six
    keys exact, recorded_at compared as a parsed time — it must parse
    to the time named in RECORDED_ATS, whatever suffix (Z or +00:00)
    the serializer writes — or null for the rows nothing answers."""
    if key not in EXPECTED_PROPOSALS:
        assert stored is None, stored
        assert listed is None, listed
        return
    expected = EXPECTED_PROPOSALS[key]
    for proposal in (stored, listed):
        assert isinstance(proposal, dict), proposal
        assert set(proposal) == set(expected) | {"recorded_at"}, proposal
        recorded = datetime.fromisoformat(proposal["recorded_at"])
        assert recorded.tzinfo is not None
        assert recorded.astimezone(UTC) == RECORDED_ATS[key]
        assert {
            field: value for field, value in proposal.items() if field != "recorded_at"
        } == expected, proposal


def test_issue1350_freeform(client, monkeypatch):
    # -- The stand-ins, all in app.api.exceptions. ----------------------
    collect = _Collect()
    decisions = _Read(DECISIONS)
    first_seen = _Read(FIRST_SEEN)
    monkeypatch.setattr(exceptions_api, "_collect", collect)
    monkeypatch.setattr(
        exceptions_api._stockout_decisions_repo, "current_all", decisions
    )
    monkeypatch.setattr(exceptions_api._exception_first_seen_repo, "list", first_seen)
    monkeypatch.setattr(
        exceptions_api,
        "_utcnow",
        lambda: datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
    )

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon = TestClient(app)

    def _listed_row(key):
        """The row the list answers for the key just now, snoozed
        rows and the proposals included."""
        response = admin.get(
            URL,
            params={"include_snoozed": "true", "include_proposal": "true"},
        )
        assert response.status_code == 200, response.text
        return {row["key"]: row for row in response.json()["rows"]}[key]

    def _put_and_check(
        key, kind, item_code, site_code, snooze_until, note, expected_total
    ):
        """One admin PUT: the six-key answer, one new event row and
        the three reads each once more with no argument."""
        before_collect = len(collect.calls)
        before_first_seen = len(first_seen.calls)
        before_decisions = len(decisions.calls)
        body = {"key": key}
        if snooze_until is not None:
            body["snooze_until"] = snooze_until.isoformat()
        if note is not None:
            body["note"] = note
        response = admin.put(ACK_URL, json=body)
        assert response.status_code == 200, response.text
        answer = response.json()
        assert set(answer) == ACK_ROW_KEYS
        assert answer["key"] == key
        assert answer["acknowledged_by"] == "dev-admin-1"
        assert answer["acknowledged_by_name"] == "admin"
        assert answer["snooze_until"] == (
            snooze_until.isoformat() if snooze_until is not None else None
        )
        assert answer["note"] == note
        # The one new event row, read with one SELECT in id order.
        rows = _stored_events()
        assert len(rows) == expected_total
        event = rows[-1]
        assert event["key"] == answer["key"]
        assert event["kind"] == kind
        assert event["item_code"] == item_code
        assert event["site_code"] == site_code
        assert event["acknowledged_by"] == answer["acknowledged_by"]
        assert event["acknowledged_by_name"] == answer["acknowledged_by_name"]
        assert event["acknowledged_at"] == datetime.fromisoformat(
            answer["acknowledged_at"]
        )
        assert event["snooze_until"] == snooze_until
        # The three reads, each once more, each with no argument —
        # checked before the list read below, which calls them again.
        assert collect.calls[before_collect:] == [()]
        assert first_seen.calls[before_first_seen:] == [((), {})]
        assert decisions.calls[before_decisions:] == [((), {})]
        # The proposal the list answers for the row just after the PUT.
        _check_proposal(event["proposal"], _listed_row(key)["proposal"], key)

    # -- The empty store: every number 0, by_kind {}. --------------------
    response = admin.get(COUNTS_URL)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "acknowledged": 0,
        "proposed": 0,
        "without_proposal": 0,
        "snoozed": 0,
        "snoozed_proposed": 0,
        "by_kind": {},
    }
    assert _stored_events() == []

    # -- The seven PUTs, in order. ----------------------------------------
    _put_and_check("stockout_likely:A/159", "stockout_likely", "A", SITE, None, None, 1)
    _put_and_check(
        "stockout_at_risk:B/159",
        "stockout_at_risk",
        "B",
        SITE,
        date(2026, 11, 9),
        None,
        2,
    )
    _put_and_check("stockout_likely:D/159", "stockout_likely", "D", SITE, None, None, 3)
    _put_and_check(
        "expedite_after_stockout:000401/2",
        "expedite_after_stockout",
        "G",
        SITE,
        None,
        None,
        4,
    )
    _put_and_check(
        "release_failed:7", "release_failed", None, None, date(2026, 11, 9), None, 5
    )
    _put_and_check(
        "stockout_likely:A/159",
        "stockout_likely",
        "A",
        SITE,
        date(2026, 11, 9),
        "after the supplier call",
        6,
    )
    _put_and_check("expedite_late:000301/1", "expedite_late", "F", SITE, None, None, 7)

    # -- Refusals: no acknowledgement, no event, the reads as named. ------
    def _no_new_calls(before):
        assert collect.calls[before[0] :] == []
        assert first_seen.calls[before[1] :] == []
        assert decisions.calls[before[2] :] == []

    before = (len(collect.calls), len(first_seen.calls), len(decisions.calls))
    assert viewer.put(ACK_URL, json={"key": "buffer_critical:C/159"}).status_code == 403
    _no_new_calls(before)
    assert anon.put(ACK_URL, json={"key": "buffer_critical:C/159"}).status_code == 401
    _no_new_calls(before)

    before = (len(collect.calls), len(first_seen.calls), len(decisions.calls))
    assert admin.put(ACK_URL, json={"key": "nope:404"}).status_code == 404
    assert collect.calls[before[0] :] == [()]
    assert first_seen.calls[before[1] :] == []
    assert decisions.calls[before[2] :] == []

    before = (len(collect.calls), len(first_seen.calls), len(decisions.calls))
    past = admin.put(
        ACK_URL,
        json={"key": "buffer_critical:C/159", "snooze_until": "2026-11-01"},
    )
    assert past.status_code == 422, past.text
    _no_new_calls(before)

    # No refusal stored an event.
    assert len(_stored_events()) == 7

    # -- Clearing: 204, no event. ------------------------------------------
    assert (
        admin.delete(ACK_URL, params={"key": "stockout_likely:A/159"}).status_code
        == 204
    )
    assert len(_stored_events()) == 7

    # -- Never in the way, in order. ----------------------------------------
    real_insert = exceptions_api._exception_ack_events_repo.insert

    def _insert_down(*args, **kwargs):
        raise RuntimeError("the event insert is down")

    # The insert raising: 200 with the stored row, the list then reads
    # C's row acknowledged, and no event is stored.
    monkeypatch.setattr(
        exceptions_api._exception_ack_events_repo, "insert", _insert_down
    )
    response = admin.put(ACK_URL, json={"key": "buffer_critical:C/159"})
    assert response.status_code == 200, response.text
    assert set(response.json()) == ACK_ROW_KEYS
    body = admin.get(URL).json()
    row = {r["key"]: r for r in body["rows"]}["buffer_critical:C/159"]
    assert row["acknowledged"] is True
    assert row["acknowledged_by_name"] == "admin"
    assert len(_stored_events()) == 7

    # The decisions read raising: one event, its proposal SQL null.
    monkeypatch.setattr(
        exceptions_api._exception_ack_events_repo, "insert", real_insert
    )
    decisions_down = _Read(None, raises=RuntimeError("the decisions read is down"))
    monkeypatch.setattr(
        exceptions_api._stockout_decisions_repo, "current_all", decisions_down
    )
    response = admin.put(ACK_URL, json={"key": "buffer_critical:C/159"})
    assert response.status_code == 200, response.text
    rows = _stored_events()
    assert len(rows) == 8
    assert rows[-1]["key"] == "buffer_critical:C/159"
    assert rows[-1]["proposal"] is None

    # The first-seen read raising instead: one event, its proposal
    # SQL null.
    monkeypatch.setattr(
        exceptions_api._stockout_decisions_repo, "current_all", decisions
    )
    first_seen_down = _Read(None, raises=RuntimeError("the first-seen read is down"))
    monkeypatch.setattr(
        exceptions_api._exception_first_seen_repo, "list", first_seen_down
    )
    response = admin.put(ACK_URL, json={"key": "buffer_critical:C/159"})
    assert response.status_code == 200, response.text
    rows = _stored_events()
    assert len(rows) == 9
    assert rows[-1]["key"] == "buffer_critical:C/159"
    assert rows[-1]["proposal"] is None

    # Every stand-in restored: one more PUT stores C's decision
    # (transfer).
    monkeypatch.setattr(exceptions_api._exception_first_seen_repo, "list", first_seen)
    response = admin.put(ACK_URL, json={"key": "buffer_critical:C/159"})
    assert response.status_code == 200, response.text
    rows = _stored_events()
    assert len(rows) == 10
    assert rows[-1]["key"] == "buffer_critical:C/159"
    _check_proposal(
        rows[-1]["proposal"],
        _listed_row("buffer_critical:C/159")["proposal"],
        "buffer_critical:C/159",
    )

    # -- The counts: after all of the above. --------------------------------
    expected_counts = {
        "acknowledged": 10,
        "proposed": 5,
        "without_proposal": 5,
        "snoozed": 3,
        "snoozed_proposed": 2,
        "by_kind": {
            "stockout_likely": {"acknowledged": 3, "proposed": 2},
            "stockout_at_risk": {"acknowledged": 1, "proposed": 1},
            "buffer_critical": {"acknowledged": 3, "proposed": 1},
            "expedite_late": {"acknowledged": 1, "proposed": 1},
            "expedite_after_stockout": {"acknowledged": 1, "proposed": 0},
            "release_failed": {"acknowledged": 1, "proposed": 0},
        },
    }
    response = admin.get(COUNTS_URL)
    assert response.status_code == 200, response.text
    assert response.json() == expected_counts
    counts = response.json()
    assert counts["proposed"] + counts["without_proposal"] == counts["acknowledged"]
    assert (
        sum(bucket["acknowledged"] for bucket in counts["by_kind"].values())
        == counts["acknowledged"]
    )
    assert (
        sum(bucket["proposed"] for bucket in counts["by_kind"].values())
        == counts["proposed"]
    )
    # The user token gets the same answer; no token gets 401.
    response = viewer.get(COUNTS_URL)
    assert response.status_code == 200, response.text
    assert response.json() == expected_counts
    assert anon.get(COUNTS_URL).status_code == 401

    # -- The schema: the new path and its two schemas. -----------------------
    spec = app.openapi()
    proposals = spec["paths"]["/api/exceptions/proposals"]
    assert set(proposals) == {"get"}
    get_counts = proposals["get"]
    assert "acknowledgement events" in get_counts["description"]
    assert "proposal" in get_counts["description"]
    response_schema = get_counts["responses"]["200"]["content"]["application/json"]
    assert response_schema["schema"] == {
        "$ref": "#/components/schemas/ExceptionProposalCounts"
    }
    counts_schema = spec["components"]["schemas"]["ExceptionProposalCounts"]
    assert set(counts_schema["properties"]) == {
        "acknowledged",
        "proposed",
        "without_proposal",
        "snoozed",
        "snoozed_proposed",
        "by_kind",
    }
    bucket_schema = spec["components"]["schemas"]["ExceptionProposalBucket"]
    assert set(bucket_schema["properties"]) == {"acknowledged", "proposed"}
    ack_row_schema = spec["components"]["schemas"]["AckRow"]
    assert set(ack_row_schema["properties"]) == ACK_ROW_KEYS
    ack_body_schema = spec["components"]["schemas"]["AckBody"]
    assert set(ack_body_schema["properties"]) == {"key", "snooze_until", "note"}

    # -- The migration: one head, one successor of the excess-decision
    #    proposals revision (not compared with the head, so a later
    #    migration leaves this unchanged), and the table and its
    #    constraint in the test database. -------------------------------------
    directory = ScriptDirectory(_MIGRATIONS_DIR)
    assert len(directory.get_heads()) == 1
    history = list(directory.walk_revisions())
    successors = [
        script
        for script in history
        if script.down_revision == "0046_excess_decision_proposals"
    ]
    assert len(successors) == 1
    # The constraint: a proposal object without a record_kind is a
    # check violation, and nothing is stored.
    event_repo = ExceptionAckEventPostgresRepository()
    with pytest.raises(psycopg2.errors.CheckViolation):
        event_repo.insert(
            key="buffer_critical:C/159",
            kind="buffer_critical",
            item_code="C",
            site_code=SITE,
            acknowledged_by="dev-admin-1",
            acknowledged_by_name="admin",
            acknowledged_at=datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
            snooze_until=None,
            proposal={"reason": "no record kind"},
        )
    assert len(_stored_events()) == 10

    # -- Identity, second pass after a full reload: the page still renders
    #    for the non-admin, the admin-only controls are absent. --------------
    anon_reloaded = TestClient(app)
    viewer_reloaded = TestClient(
        app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
    )
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer_reloaded.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == VIEWER_SUB
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    baseline = admin.get(URL).json()
    page = viewer_reloaded.get(URL)
    assert page.status_code == 200, page.text
    assert page.json() == baseline
    counts_page = viewer_reloaded.get(COUNTS_URL)
    assert counts_page.status_code == 200, counts_page.text
    assert counts_page.json() == expected_counts
    # The admin-only controls are absent for the viewer.
    assert (
        viewer_reloaded.put(ACK_URL, json={"key": "buffer_critical:C/159"}).status_code
        == 403
    )
    assert (
        viewer_reloaded.delete(
            ACK_URL, params={"key": "buffer_critical:C/159"}
        ).status_code
        == 403
    )
    assert anon_reloaded.get(URL).status_code == 401
    assert anon_reloaded.get(COUNTS_URL).status_code == 401
