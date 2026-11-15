"""Item-mapping suggestions: Acumatica item codes to Odoo item codes.

``suggest_item_mappings`` answers ``GET /api/erp-mappings/suggestions``.
It writes nothing: one read of ``item_sites``, one of ``inventory_items``
and one of ``erp_code_mappings`` feed two match rules, and the answer is
a list of ``{source_code, target_code, matched_on, source_description,
target_description, ambiguous}`` ordered by ``matched_on`` with ``code``
first, then ``source_code``, then ``target_code``.

A suggestion's ``source_code`` is the Acumatica code and its
``target_code`` the Odoo code — the way ``erp_code_mappings`` stores
``kind`` ``item`` with ``target_system`` ``odoo``.

  * by code — an item code held in ``item_sites`` under both
    ``acumatica`` and ``odoo``, at different sites, is one suggestion
    with ``source_code`` and ``target_code`` both that code and
    ``matched_on`` ``code``;
  * by description — a code in ``inventory_items`` under ``acumatica``
    and a different code there under ``odoo`` whose ``description``
    values are equal after trimming and ignoring case is one suggestion
    with ``matched_on`` ``description``.

A null, empty or only-spaces description never matches, and nothing
else counts: no similarity score, no partial match, no match on the
unit of measure.  When one code's description matches several codes on
the other side, each pair is its own suggestion and each carries
``ambiguous`` true; otherwise ``ambiguous`` is false.  A code that
already has a stored item mapping to ``odoo`` as its ``source_code``,
or that is already some mapping's ``target_code``, is left out of the
result.  The descriptions are the stored values, null when the code
has no ``inventory_items`` row under its side.
"""

from app.repositories.erp_code_mappings_postgres_repository import (
    ErpCodeMappingPostgresRepository,
)
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)

_SOURCE_SYSTEM = "acumatica"
_TARGET_SYSTEM = "odoo"


def _normalized(description: str | None) -> str | None:
    """The description trimmed and lower-cased; None for null, empty
    or only-spaces values (which never match)."""
    if description is None:
        return None
    trimmed = description.strip().lower()
    return trimmed or None


def suggest_item_mappings(limit: int = 200) -> list[dict]:
    """The suggested item mappings, capped at ``limit``.

    One read of each of ``item_sites``, ``inventory_items`` and
    ``erp_code_mappings`` (and no more, and no writes) computes the
    suggestions; the order is ``matched_on`` with ``code`` first, then
    ``source_code``, then ``target_code``.
    """
    # One read of item_sites: the sites holding each code, per side.
    item_site_rows = ItemSitePostgresRepository().source_summary()["item_sites"]
    acu_sites: dict[str, set[str]] = {}
    odoo_sites: dict[str, set[str]] = {}
    for row in item_site_rows:
        code = row["item_code"]
        if row["source_system"] == _SOURCE_SYSTEM:
            acu_sites.setdefault(code, set()).add(row["site_code"])
        elif row["source_system"] == _TARGET_SYSTEM:
            odoo_sites.setdefault(code, set()).add(row["site_code"])

    # One read of inventory_items: each code's stored description, per side.
    inventory_rows = InventoryItemPostgresRepository().list_all()
    acu_description: dict[str, str | None] = {}
    odoo_description: dict[str, str | None] = {}
    for row in inventory_rows:
        code = row["item_code"]
        if row["source_system"] == _SOURCE_SYSTEM:
            acu_description[code] = row["description"]
        elif row["source_system"] == _TARGET_SYSTEM:
            odoo_description[code] = row["description"]

    # One read of erp_code_mappings: the codes already mapped are left
    # out — a stored item mapping to odoo by source code, or any
    # mapping's target code.
    mappings = ErpCodeMappingPostgresRepository().list()
    excluded = {
        m["source_code"]
        for m in mappings
        if m["kind"] == "item" and m["target_system"] == "odoo"
    }
    excluded |= {m["target_code"] for m in mappings}

    suggestions: list[dict] = []

    # By code: held under both systems, at different sites.
    for code in set(acu_sites) & set(odoo_sites):
        if acu_sites[code] & odoo_sites[code]:
            continue  # the same site on both sides: not a pair
        if code in excluded:
            continue
        suggestions.append(
            {
                "source_code": code,
                "target_code": code,
                "matched_on": "code",
                "source_description": acu_description.get(code),
                "target_description": odoo_description.get(code),
                "ambiguous": False,
            }
        )

    # By description: equal after trimming and ignoring case; a null,
    # empty or only-spaces description never matches, and nothing else
    # counts — no similarity, no partial match, no unit of measure.
    pairs = [
        (source, target)
        for source, source_desc in acu_description.items()
        for target, target_desc in odoo_description.items()
        if target != source
        and _normalized(source_desc) is not None
        and _normalized(target_desc) == _normalized(source_desc)
    ]
    targets_of: dict[str, set[str]] = {}
    sources_of: dict[str, set[str]] = {}
    for source, target in pairs:
        targets_of.setdefault(source, set()).add(target)
        sources_of.setdefault(target, set()).add(source)

    for source, target in pairs:
        if source in excluded or target in excluded:
            continue
        suggestions.append(
            {
                "source_code": source,
                "target_code": target,
                "matched_on": "description",
                "source_description": acu_description[source],
                "target_description": odoo_description[target],
                "ambiguous": len(targets_of[source]) > 1 or len(sources_of[target]) > 1,
            }
        )

    suggestions.sort(
        key=lambda suggestion: (
            suggestion["matched_on"] != "code",
            suggestion["source_code"],
            suggestion["target_code"],
        )
    )
    return suggestions[:limit]
