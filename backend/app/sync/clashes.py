"""The shared ERP code-clash guard of the sync runners.

Between the last read (and mapping) and the first write, ``run_sync``
in ``app.sync.odoo`` and ``app.sync.acumatica`` each call
:func:`apply_clash_guard` once with the six row lists — item sites,
on hand, demand events, lead time observations, open purchase lines
and open sales lines.  The guard collects the ``(item_code,
site_code)`` pair of every row (a row with a null ``site_code``
identifies no pair and is kept), asks the item-sites repository which
of those pairs ``item_sites`` already stores under a different
``source_system``, and removes every row of a held pair from the six
lists so the first holder keeps its row exactly as it was, its
``source_system`` included.  Each held pair is upserted into
``erp_code_clashes`` through
:class:`app.repositories.erp_code_clashes_postgres_repository.ErpCodeClashPostgresRepository`
with ``held_by`` the holder and ``refused_source`` the sync's source,
and — in the same sync, even when no pair is held — this source's
still-open clash rows whose pair was not held this time get
``resolved_at`` set, so a clash someone fixed in the ERP closes
itself.  No row is ever deleted.

When the injected item-sites repository has no ``held_by_others``
(a test double), the guard is skipped the way the Odoo runner skips
an optional reader the client does not have: nothing is filtered,
recorded or resolved.

The item master's names are guarded the same way by
:func:`apply_item_name_guard`: between the last read (and mapping) and
the first write, ``run_sync`` in ``app.sync.odoo`` and
``app.sync.acumatica`` each call it once with the inventory-item rows
and the inventory-items repository.  It collects the distinct
``item_code`` of every row, asks the inventory-items repository which
of those codes ``inventory_items`` already stores under a different
``source_system``, and removes every row of a held code from the list
so the first holder keeps its row exactly as it was, its
``source_system`` included.  An item master row names no site, so
nothing is recorded in ``erp_code_clashes``.  When the injected
inventory-items repository has no ``held_by_others`` (a test double),
the guard is skipped the same way: nothing is filtered.
"""

from app.repositories.erp_code_clashes_postgres_repository import (
    ErpCodeClashPostgresRepository,
)

__all__ = ["apply_clash_guard", "apply_item_name_guard"]


def _pairs(row_lists) -> list[tuple[str, str]]:
    """The distinct non-null (item_code, site_code) pairs of the rows.

    A row with a null (or missing) ``item_code`` or ``site_code``
    identifies no pair and contributes nothing; a null ``site_code``
    is kept in the row lists by the caller.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for rows in row_lists:
        for row in rows:
            if not isinstance(row, dict):
                continue
            item_code = row.get("item_code")
            site_code = row.get("site_code")
            if not isinstance(item_code, str) or not isinstance(site_code, str):
                continue
            pair = (item_code, site_code)
            if pair not in seen:
                seen.add(pair)
                pairs.append(pair)
    return pairs


def apply_clash_guard(
    source_system: str,
    row_lists: list[list[dict]],
    item_sites_repo,
    clashes_repo: ErpCodeClashPostgresRepository | None = None,
) -> int:
    """Refuse every pair held by another source system, in place.

    Removes from each of ``row_lists`` every row whose
    ``(item_code, site_code)`` pair ``item_sites`` stores under a
    different ``source_system``, records each held pair as a clash
    (``held_by`` the holder, ``refused_source`` ``source_system``) and
    resolves this source's still-open clash rows whose pair was not
    held this time.  Returns the number of held pairs — 0 when the
    guard is skipped because the item-sites repository has no
    ``held_by_others``.
    """
    held_by_others = getattr(item_sites_repo, "held_by_others", None)
    if held_by_others is None:
        return 0
    if clashes_repo is None:
        clashes_repo = ErpCodeClashPostgresRepository()
    held = held_by_others(source_system, _pairs(row_lists))
    if held:
        for rows in row_lists:
            rows[:] = [
                row
                for row in rows
                if not (
                    isinstance(row, dict)
                    and (row.get("item_code"), row.get("site_code")) in held
                )
            ]
        for (item_code, site_code), holder in held.items():
            clashes_repo.record_clash(item_code, site_code, holder, source_system)
    # The resolve runs on every sync, even when no pair is held: this
    # source's still-open rows whose pair was not held this time get
    # resolved_at set.
    clashes_repo.resolve_unoffered(source_system, set(held))
    return len(held)


def apply_item_name_guard(
    source_system: str,
    inventory_item_rows: list[dict],
    inventory_items_repo,
) -> int:
    """Refuse every item code held by another source system, in place.

    Removes from ``inventory_item_rows`` every row whose
    ``item_code`` ``inventory_items`` stores under a different
    ``source_system`` — the first holder keeps its row exactly as it
    was, its ``source_system`` included.  An item master row names no
    site, so nothing is recorded in ``erp_code_clashes``.  Returns the
    number of rows removed — 0 when the guard is skipped because the
    inventory-items repository has no ``held_by_others`` (a test
    double).
    """
    held_by_others = getattr(inventory_items_repo, "held_by_others", None)
    if held_by_others is None:
        return 0
    item_codes: list[str] = []
    seen: set[str] = set()
    for row in inventory_item_rows:
        if not isinstance(row, dict):
            continue
        item_code = row.get("item_code")
        if isinstance(item_code, str) and item_code not in seen:
            seen.add(item_code)
            item_codes.append(item_code)
    held = held_by_others(source_system, item_codes)
    if not held:
        return 0
    kept = [
        row
        for row in inventory_item_rows
        if not (isinstance(row, dict) and row.get("item_code") in held)
    ]
    removed = len(inventory_item_rows) - len(kept)
    inventory_item_rows[:] = kept
    return removed
