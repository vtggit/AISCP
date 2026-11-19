"""Issue #1420 proof: ``copy_tree`` and the two caches built on it.

AC-1 — ``app.db.read_cache.copy_tree`` copies the trees the cached
reads answer, which hold no shared or cyclic references: a value
whose type is exactly ``dict`` becomes a new ``dict`` with the same
keys, each value copied by ``copy_tree``, one whose type is exactly
``list`` a new ``list`` and one whose type is exactly ``tuple`` a new
``tuple``, each item copied by ``copy_tree``, and one whose type is
exactly ``set`` or ``frozenset`` a new one of the same type with the
same items; the immutable scalars — ``None``, ``bool``, ``int``,
``float``, ``str``, ``bytes``, ``decimal.Decimal``, ``datetime.date``,
``datetime.datetime``, ``datetime.time`` and ``datetime.timedelta`` —
come back as the same object; any other value, a subclass of those
containers included, is copied with ``copy.deepcopy``.

AC-2 — the caches use it: ``read_cache``'s ``_cached_call`` stores
and answers with ``copy_tree`` instead of ``copy.deepcopy`` — the
first caller still keeps the original result, the cache keeps a copy
— and ``recommendations_for_all`` stores into and answers from
``recommendations_memo()`` with ``copy_tree``.

AC-3 — the exceptions call, seeded the way
``tests/test_issue1410_freeform.py`` seeds it: ``_collect()`` answers
exactly what ``_gather_sources()`` answers without any block.
"""

import copy
import datetime
import decimal

import psycopg2
import psycopg2.sql

import app.api.exceptions as exceptions
import app.db.read_cache as read_cache
import app.recommend.reorder as reorder
from app.db.connection import get_connection
from app.db.read_cache import cached_read, copy_tree, read_once
from app.recommend.reorder import recommendations_for_all, recommendations_memo

SITE = "SITE-1420"
SUPPLIER = "SUP-1420"
VENDOR = "VEND-1420"


# ---------------------------------------------------------------------------
# copy_tree itself (AC-1): the equivalence and the fallback.
# ---------------------------------------------------------------------------


def _assert_equivalence() -> None:
    price = decimal.Decimal("19.99")
    day = datetime.date(2026, 11, 19)
    moment = datetime.datetime(2026, 11, 19, 8, 30, 0)
    text = "text"
    # A list of dicts holding nested dicts, lists, tuples and a set,
    # with numbers, strings, a Decimal, a date, a datetime and None.
    tree = [
        {
            "nested": {
                "list": [1, 2.5, text, b"bytes"],
                "tuple": (1, (2.5, "inner")),
                "set": {1, "two", price},
            },
            "number": 7,
            "price": price,
            "day": day,
            "moment": moment,
            "nothing": None,
        },
        {"deep": {"deeper": {"list": [{"set": {1, 2}}]}}},
    ]

    copied = copy_tree(tree)

    # The copy equals what copy.deepcopy makes of the same tree.
    assert copied == copy.deepcopy(tree)

    # The copy's containers are new objects...
    assert copied is not tree
    assert copied[0] is not tree[0]
    assert copied[0]["nested"] is not tree[0]["nested"]
    assert copied[0]["nested"]["list"] is not tree[0]["nested"]["list"]
    assert copied[0]["nested"]["tuple"] is not tree[0]["nested"]["tuple"]
    assert copied[0]["nested"]["set"] is not tree[0]["nested"]["set"]
    assert copied[1]["deep"]["deeper"]["list"] is not tree[1]["deep"]["deeper"]["list"]
    assert (
        copied[1]["deep"]["deeper"]["list"][0]
        is not tree[1]["deep"]["deeper"]["list"][0]
    )
    # ...and its scalars are the original objects.
    assert copied[0]["number"] is tree[0]["number"]
    assert copied[0]["price"] is price
    assert copied[0]["day"] is day
    assert copied[0]["moment"] is moment
    assert copied[0]["nothing"] is None
    assert copied[0]["nested"]["list"][2] is text
    assert copied[0]["nested"]["list"][1] is tree[0]["nested"]["list"][1]
    assert copied[0]["nested"]["list"][3] is tree[0]["nested"]["list"][3]
    assert copied[0]["nested"]["tuple"][1][0] is tree[0]["nested"]["tuple"][1][0]
    set_decimals = [
        item for item in copied[0]["nested"]["set"] if isinstance(item, decimal.Decimal)
    ]
    assert set_decimals and set_decimals[0] is price

    pristine_copied = copy.deepcopy(copied)
    copy.deepcopy(tree)
    # Changing the original leaves the copy unchanged.
    tree[0]["nested"]["list"].append(1000)
    tree[0]["nested"]["set"].discard(1)
    tree[0]["nested"]["changed"] = True
    tree[1]["deep"]["deeper"]["list"][0]["set"].clear()
    assert copied == pristine_copied

    # Changing any dict, list or set inside the copy leaves the
    # original unchanged.
    after_change = copy.deepcopy(tree)
    copied[0]["nested"]["list"].append(99)
    copied[0]["nested"]["list"][0] = 0
    copied[0]["nested"]["set"].add("added")
    copied[0]["nested"]["new_key"] = True
    copied[0]["price"] = decimal.Decimal("1.00")
    copied[1]["deep"]["deeper"]["list"][0]["set"].add(99)
    assert tree == after_change


class _Holder:
    """A small class with a list attribute: not one of the container
    types, so ``copy_tree`` copies it with ``copy.deepcopy``."""

    def __init__(self):
        self.items = [1, 2]


class _SubDict(dict):
    """A dict subclass: its type is not exactly ``dict``."""


def _assert_fallback() -> None:
    # Any other value, a subclass of the containers included, is
    # copied with copy.deepcopy: a counting stand-in in
    # app.db.read_cache runs once per value and delegates to the real
    # deepcopy.
    calls = {"n": 0}
    real_deepcopy = read_cache.copy.deepcopy

    def counting_deepcopy(obj, memo=None):
        calls["n"] += 1
        return real_deepcopy(obj, memo)

    read_cache.copy.deepcopy = counting_deepcopy
    try:
        holder = _Holder()
        holder_copy = copy_tree(holder)
        sub = _SubDict({"items": [1, 2]})
        sub_copy = copy_tree(sub)
    finally:
        read_cache.copy.deepcopy = real_deepcopy

    assert calls["n"] == 2
    # The copies are different objects of the same type.
    assert holder_copy is not holder
    assert type(holder_copy) is _Holder
    assert sub_copy is not sub
    assert type(sub_copy) is _SubDict
    # Changing the copy's list leaves the original unchanged.
    holder_copy.items.append(3)
    assert holder.items == [1, 2]
    sub_copy["items"].append(3)
    assert sub["items"] == [1, 2]


# ---------------------------------------------------------------------------
# The caches use copy_tree (AC-2).
# ---------------------------------------------------------------------------


def _assert_read_cache() -> None:
    produced = []

    @cached_read
    def plain_reads(key):
        # A list of plain dicts.
        result = [{"a": 1, "b": [2]}, {"c": 3}]
        produced.append(result)
        return result

    # copy.deepcopy in app.db.read_cache, replaced by a counting
    # stand-in: the copies are built by copy_tree, so it never runs.
    calls = {"n": 0}
    real_deepcopy = read_cache.copy.deepcopy

    def counting_deepcopy(obj, memo=None):
        calls["n"] += 1
        return real_deepcopy(obj, memo)

    read_cache.copy.deepcopy = counting_deepcopy
    try:
        with read_once():
            first = plain_reads("k")
            second = plain_reads("k")
            third = plain_reads("k")
            # The first caller still keeps the original result.
            assert first is produced[0]
            # The third answer equals the first.
            assert third == first
            # The cache keeps a copy, not the original: mutating the
            # first caller's list changes nothing a later answer is
            # given.
            first[0]["a"] = -1
            fourth = plain_reads("k")
            assert fourth[0]["a"] == 1
            # Changing the second answer leaves the third unchanged.
            second[0]["a"] = 999
    finally:
        read_cache.copy.deepcopy = real_deepcopy

    assert calls["n"] == 0
    assert third[0]["a"] == 1
    assert third == [{"a": 1, "b": [2]}, {"c": 3}]


def _assert_memo() -> None:
    as_of = datetime.date(2026, 11, 19)
    compute_calls = {"n": 0}

    def standin_compute(resolved_as_of):
        compute_calls["n"] += 1
        return [
            {"item_code": "ITEM-1", "site_code": SITE, "value": 1},
            {"item_code": "ITEM-2", "site_code": SITE, "value": 2},
        ]

    # copy.deepcopy, replaced by a counting stand-in for the memo
    # block: the memo copies are built by copy_tree, so no deepcopy
    # the module would take never runs.
    calls = {"n": 0}
    real_deepcopy = copy.deepcopy

    def counting_deepcopy(obj, memo=None):
        calls["n"] += 1
        return real_deepcopy(obj, memo)

    real_compute = reorder._compute_recommendations
    reorder._compute_recommendations = standin_compute
    copy.deepcopy = counting_deepcopy
    try:
        with recommendations_memo():
            first = recommendations_for_all(as_of)
            second = recommendations_for_all(as_of)
    finally:
        reorder._compute_recommendations = real_compute
        copy.deepcopy = real_deepcopy

    # Two calls compute once, the deepcopy stand-in never ran, and
    # the answers are equal rows.
    assert compute_calls["n"] == 1
    assert calls["n"] == 0
    assert first == second
    # Changing a row of the first answer leaves the second unchanged.
    first[0]["value"] = 999
    assert second[0]["value"] == 1


# ---------------------------------------------------------------------------
# The exceptions call (AC-3): the seed the way
# tests/test_issue1410_freeform.py seeds it.
# ---------------------------------------------------------------------------


def _truncate_all() -> None:
    """Every public table (but alembic_version) empty, so the proof
    sees a database of its own rows, whatever ran before it."""
    conn = get_connection()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
            tables = [row[0] for row in cur.fetchall()]
            for table in tables:
                cur.execute(
                    psycopg2.sql.SQL("TRUNCATE TABLE {} CASCADE").format(
                        psycopg2.sql.Identifier(table)
                    )
                )
    finally:
        conn.close()


def _seed_failed_release() -> None:
    """One failed release within the 14-day window: the releases
    source answers one row, kind "release_failed"."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO plan_releases "
                "(month, site_code, supplier_code, origin, warehouse_code, "
                "vendor_code, order_nbr, outcome, error, line_count, value, "
                "lines, actor) "
                "VALUES (%s, %s, %s, %s, NULL, %s, NULL, %s, %s, %s, %s, %s, %s)",
                (
                    "2026-10",
                    SITE,
                    SUPPLIER,
                    "test-origin",
                    VENDOR,
                    "error",
                    "boom",
                    1,
                    10,
                    "[]",
                    "tester",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _assert_exceptions_call() -> None:
    _truncate_all()
    _seed_failed_release()
    # _collect() — inside its one block — answers exactly what
    # _gather_sources() answers without any block.
    standin = exceptions._gather_sources()
    route_answer = exceptions._collect()
    assert standin == route_answer
    as_of, rows, sources_failed = route_answer
    assert as_of is not None
    assert sources_failed == []
    # At least one source answers a row: the seeded failed release.
    assert any(row.kind == "release_failed" for row in rows)


def test_issue1420_freeform(client) -> None:
    _assert_equivalence()
    _assert_fallback()
    _assert_read_cache()
    _assert_memo()
    _assert_exceptions_call()
