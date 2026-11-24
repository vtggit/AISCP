"""The parameter writeback record: what AISCP pushed, and what the
ERPs kept.

``push_record()`` measures, over the append-only
``parameter_writebacks`` audit table, how much of what the writeback
confirm pushed back to the ERPs the ERPs actually kept.  It answers
``{reorder_points, buffers}``, each kind carrying
``{pushed, read, unchanged, rate}``: ``pushed`` is the number of
item-sites whose counted push is of that kind, ``read`` how many of
those are read back, ``unchanged`` how many of the read ones are
unchanged, and ``rate`` ``unchanged / read`` rounded to 4 places,
null when ``read`` is 0.

An item-site is one (item_code, site_code) pair.  For each item-site
only one ``parameter_writebacks`` row counts: the latest of its rows
whose ``outcome`` is "written" or "unchanged" and whose
``after_reorder_point`` and ``after_max_qty`` are both not null —
the latest being the greatest ``written_at``, and among equal
``written_at`` the greatest ``id``.  A "failed" row never counts, and
never hides an earlier push: a pair whose newest audit row is a
failure still counts its latest written or unchanged row.  The
basis decides the kind: the push is a reorder-point push when that
row's ``basis`` is "decision" and a buffer push when it is
"ddmrp"; a push of any other basis (such as "excess") is counted in
neither kind.

The push is read back when the item-site's ``item_sites`` row exists
and its ``synced_at`` is later than the push's ``written_at`` — the
first sync that saw the ERP's state after the push.  A push that is
read back is unchanged when the ``item_sites`` row's ``reorder_point``
and ``max_qty`` are both not null, and each differs by no more than
0.000001 from the push's ``after_reorder_point`` and
``after_max_qty``; every other push that is read back is changed.

The measure reads only: it writes nothing and calls no ERP.  Its
limits are the sync's: AISCP keeps no history of the ERP's values
between syncs, so a value the ERP changed and changed back between
two syncs counts as unchanged, and an item-site that no sync lists
any more is never read back.
"""

from __future__ import annotations

from app.db.connection import get_cursor

# The audit outcomes that count as a push at all.
_COUNTED_OUTCOMES = ("written", "unchanged")

# A read-back value differing by no more than this is unchanged.
_UNCHANGED_TOLERANCE = 0.000001


def _counted_pushes() -> list[dict]:
    """One counted push per item-site.

    Every ``parameter_writebacks`` row whose outcome is "written" or
    "unchanged" and whose after values are both stored, reduced per
    item-site to the latest such row: the greatest ``written_at``,
    ties broken by the greatest ``id``.  A "failed" row never counts
    and never hides an earlier push.  Each counted push carries its
    row's ``basis``, the source discriminator its kind is decided
    by.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, item_code, site_code, decision_id, basis, "
            "after_reorder_point, after_max_qty, written_at "
            "FROM parameter_writebacks "
            "WHERE outcome IN %s "
            "AND after_reorder_point IS NOT NULL "
            "AND after_max_qty IS NOT NULL",
            (_COUNTED_OUTCOMES,),
        )
        rows = [dict(row) for row in cur.fetchall()]
    latest: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["item_code"], row["site_code"])
        current = latest.get(key)
        if current is None or (row["written_at"], row["id"]) > (
            current["written_at"],
            current["id"],
        ):
            latest[key] = row
    return list(latest.values())


def _item_sites(pairs: set[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """The item_sites rows of the given pairs, keyed by the pair.

    A pair no sync lists any more simply has no entry — its push is
    never read back.
    """
    if not pairs:
        return {}
    with get_cursor() as cur:
        cur.execute(
            "SELECT item_code, site_code, synced_at, reorder_point, max_qty "
            "FROM item_sites "
            "WHERE (item_code, site_code) IN "
            "(SELECT * FROM UNNEST(%s::text[], %s::text[]))",
            (
                [item_code for item_code, _ in pairs],
                [site_code for _, site_code in pairs],
            ),
        )
        return {
            (row["item_code"], row["site_code"]): dict(row) for row in cur.fetchall()
        }


def _is_unchanged(site: dict, push: dict) -> bool:
    """Whether one read-back push is unchanged.

    The item-site's ``reorder_point`` and ``max_qty`` must both be
    stored, and each may differ from the push's after value by no
    more than 0.000001.
    """
    if site["reorder_point"] is None or site["max_qty"] is None:
        return False
    return (
        abs(site["reorder_point"] - push["after_reorder_point"]) <= _UNCHANGED_TOLERANCE
        and abs(site["max_qty"] - push["after_max_qty"]) <= _UNCHANGED_TOLERANCE
    )


def push_record() -> dict:
    """The parameter writeback record of every counted push.

    For each item-site, its one counted push (see
    ``_counted_pushes``) is a reorder-point push when its ``basis``
    is "decision" and a buffer push when its ``basis`` is "ddmrp"
    (the basis decides the kind, not the ``decision_id``); a counted
    push of any other basis is counted in neither kind.  The push is
    read back when the item-site's ``item_sites`` row exists and its
    ``synced_at`` is later than the push's ``written_at``; a
    read-back push is unchanged when the row's ``reorder_point`` and
    ``max_qty`` are both not null and each differs by no more than
    0.000001 from the push's after values, every other read-back
    push being changed.  The answer is
    ``{reorder_points, buffers}``, each ``{pushed, read, unchanged,
    rate}`` with ``rate`` ``unchanged / read`` rounded to 4 places,
    null when ``read`` is 0.  Reads only: nothing written, no ERP
    call.
    """
    pushes = _counted_pushes()
    sites = _item_sites({(push["item_code"], push["site_code"]) for push in pushes})
    kinds = {
        "reorder_points": {"pushed": 0, "read": 0, "unchanged": 0, "rate": None},
        "buffers": {"pushed": 0, "read": 0, "unchanged": 0, "rate": None},
    }
    for push in pushes:
        if push["basis"] == "decision":
            kind = "reorder_points"
        elif push["basis"] == "ddmrp":
            kind = "buffers"
        else:
            # A counted push of any other basis is counted in
            # neither kind.
            continue
        stats = kinds[kind]
        stats["pushed"] += 1
        site = sites.get((push["item_code"], push["site_code"]))
        if site is None or site["synced_at"] <= push["written_at"]:
            continue
        stats["read"] += 1
        if _is_unchanged(site, push):
            stats["unchanged"] += 1
    for stats in kinds.values():
        if stats["read"]:
            stats["rate"] = round(stats["unchanged"] / stats["read"], 4)
    return kinds
