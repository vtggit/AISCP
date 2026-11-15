"""The supplier risk rating: late, variable and sole-source signs.

``supplier_risk`` reads the lead-time observations exactly once over
the same 730-day window and as-of that
``app.stats.lead_time.lead_time_stats_for_all`` uses (the shared
demand default as-of when no as-of is supplied), and runs
``compute_lead_time_stats`` once per ``supplier_code`` over all of
that supplier's observations.  For the thresholds it reads the stored
system settings once (one query, falling back to the registry
defaults), for the vendor code and display name the suppliers read
model once, and for the on-time rate it calls
``supplier_performance`` once with its default window — the figure
the Suppliers screen shows by default.  It writes nothing and calls
no ERP.

One entry per ``supplier_code`` with at least one in-window
observation:

  * ``receipts`` — the supplier's in-window observations
    (``n_observations``);
  * ``lead_time_mean_days`` / ``lead_time_std_days`` — the outlier
    fenced mean and sample standard deviation (``mean_days`` /
    ``std_days``);
  * ``lead_time_cv`` — the standard deviation over the mean, rounded
    to 2 places, null when either is null or the mean is 0;
  * ``on_time_rate`` — the figure the Suppliers screen shows by
    default (``supplier_performance`` with its default window),
    matched by ``supplier_code``, null when the supplier has no entry
    there;
  * ``sole_source_items`` — the item codes whose observations in the
    window all come from this one supplier (an observation stored
    without an item code is no item and counts for none of them);
  * ``sole_source_item_codes`` — the sorted codes counted by
    ``sole_source_items``, from that same one read — the empty list
    when the count is 0;
  * ``late`` — true when ``on_time_rate`` is not null and below the
    ``supplier_risk.on_time_floor`` setting;
  * ``variable`` — true only when the supplier's lead-time stats are
    not low confidence (at least three used observations) and the
    rounded ``lead_time_cv`` is not null and at least the
    ``supplier_risk.lead_time_cv_ceiling`` setting — the flag
    follows the rounded value the route returns, so at the 0.6
    default ceiling a displayed ``lead_time_cv`` of 0.60 is variable
    and one of 0.50 is not;
  * ``sole_source`` — true when ``sole_source_items`` is above 0;
  * ``risk`` — ``high`` when the supplier is ``late`` or
    ``variable`` and also ``sole_source``, ``medium`` when it is
    ``late`` or ``variable`` without being ``sole_source``, and
    ``low`` when it is neither ``late`` nor ``variable``, whether or
    not it is ``sole_source``.

Entries are ordered high, then medium, then low, then by
``sole_source_items`` descending, then ``supplier_code``.  Every
entry carries ``vendor_code`` and ``supplier_name``, looked up by
``supplier_code`` in the suppliers read model (read once through
``SupplierPostgresRepository.list_all``) — both null for a supplier
missing from the read model, or when the read model is empty, which
is still listed, never dropped.

The answer is ``{"as_of", "suppliers"}`` with ``as_of`` the resolved
ISO date.
"""

from __future__ import annotations

from app.db.connection import get_cursor
from app.recommend.supplier_performance import (
    _suppliers_by_code,
    supplier_performance,
)
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)
from app.settings.registry import get_setting
from app.stats.lead_time import (
    _read_window,
    _resolve_as_of,
    compute_lead_time_stats,
)

ON_TIME_FLOOR_SETTING = "supplier_risk.on_time_floor"
LEAD_TIME_CV_CEILING_SETTING = "supplier_risk.lead_time_cv_ceiling"

# The level's rank in the answer order: high first, then medium, then low.
RISK_RANK = {"high": 0, "medium": 1, "low": 2}

_suppliers_repo = SupplierPostgresRepository()


def _stored_system_settings() -> dict:
    """The stored values of every system setting, in one query."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT key, value FROM settings " "WHERE scope = 'system' AND owner = ''"
        )
        return {row["key"]: row["value"] for row in cur.fetchall()}


def _number_setting(stored: dict, key: str) -> float:
    """The stored value of a number setting, or the registry default.

    The stored value applies when it is a real number within the
    setting's bounds; a value written out of band that the setting
    cannot hold (not a number, or outside ``[min, max]``) never
    applies — the default does.
    """
    setting = get_setting(key)
    if setting is None or setting.type != "number":
        raise ValueError(f"{key!r} is not a registered number setting")
    value = stored.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(setting.default)
    if not (setting.min <= value <= setting.max):
        return float(setting.default)
    return float(value)


def _risk_level(late: bool, variable: bool, sole_source: bool) -> str:
    """late or variable is high with a sole source and medium
    without; neither is low, sole source or not."""
    if late or variable:
        return "high" if sole_source else "medium"
    return "low"


def supplier_risk() -> dict:
    """The per-supplier risk signs over the 730-day window."""
    with get_cursor() as cur:
        resolved_as_of = _resolve_as_of(cur, None)
        rows = _read_window(cur, resolved_as_of)

    # The observations, pooled per supplier (for the lead-time stats)
    # and per item (for the sole-source count), in that one read.
    by_supplier: dict[str, list[float]] = {}
    item_suppliers: dict[str, set[str]] = {}
    for row in rows:
        supplier_code = row.get("supplier_code")
        item_code = row.get("item_code")
        lead_time_days = row.get("lead_time_days")
        if supplier_code is None or lead_time_days is None:
            continue
        by_supplier.setdefault(supplier_code, []).append(float(lead_time_days))
        # An observation stored without an item code is no item: it
        # feeds the supplier's lead-time stats but never the item
        # map, where a NULL would count as a sole-source item and
        # crash the sorted codes or the typed list of strings.
        if item_code is not None:
            item_suppliers.setdefault(item_code, set()).add(supplier_code)

    # The sole-source codes, from the same one read as the count:
    # every item whose in-window observations all come from one
    # supplier, sorted per supplier — the empty list when the
    # count is 0.
    sole_source_items = {code: 0 for code in by_supplier}
    sole_source_codes = {code: [] for code in by_supplier}
    for item_code, suppliers in item_suppliers.items():
        if len(suppliers) == 1:
            sole = next(iter(suppliers))
            sole_source_items[sole] += 1
            sole_source_codes[sole].append(item_code)
    for codes in sole_source_codes.values():
        codes.sort()

    stored = _stored_system_settings()
    on_time_floor = _number_setting(stored, ON_TIME_FLOOR_SETTING)
    lead_time_cv_ceiling = _number_setting(stored, LEAD_TIME_CV_CEILING_SETTING)

    # The on-time rate the Suppliers screen shows by default:
    # supplier_performance with its default window, matched by
    # supplier_code.
    on_time_by_code = {
        entry["supplier_code"]: entry["on_time_rate"]
        for entry in supplier_performance()["suppliers"]
    }

    # The suppliers read model, once: the vendor code and display
    # name of every supplier that has an entry, looked up by
    # supplier_code.
    suppliers_by_code = _suppliers_by_code(_suppliers_repo.list_all())

    entries = []
    for supplier_code, days in by_supplier.items():
        stats = compute_lead_time_stats(days)
        mean_days = stats["mean_days"]
        std_days = stats["std_days"]
        if mean_days is None or std_days is None or mean_days == 0:
            lead_time_cv = None
        else:
            lead_time_cv = round(std_days / mean_days, 2)

        on_time_rate = on_time_by_code.get(supplier_code)
        late = on_time_rate is not None and on_time_rate < on_time_floor
        # The sign needs evidence: the stats must not be low
        # confidence, and it follows the same rounded lead_time_cv
        # the route returns, so a displayed 0.60 at the 0.6 default
        # ceiling is variable and a displayed 0.50 is not.
        variable = (
            not stats["low_confidence"]
            and lead_time_cv is not None
            and lead_time_cv >= lead_time_cv_ceiling
        )
        count = sole_source_items[supplier_code]
        sole_source = count > 0

        entries.append(
            {
                "supplier_code": supplier_code,
                "receipts": stats["n_observations"],
                "lead_time_mean_days": mean_days,
                "lead_time_std_days": std_days,
                "lead_time_cv": lead_time_cv,
                "on_time_rate": on_time_rate,
                "sole_source_items": count,
                "sole_source_item_codes": sole_source_codes[supplier_code],
                "late": late,
                "variable": variable,
                "sole_source": sole_source,
                "risk": _risk_level(late, variable, sole_source),
            }
        )

    entries.sort(
        key=lambda entry: (
            RISK_RANK[entry["risk"]],
            -entry["sole_source_items"],
            entry["supplier_code"],
        )
    )
    # Every entry carries the vendor code and display name, looked
    # up in the read model — both null when the read model is empty
    # or the supplier is missing from it — never dropped, never a
    # failure.
    for entry in entries:
        vendor_code, supplier_name = suppliers_by_code.get(
            entry["supplier_code"], (None, None)
        )
        entry["vendor_code"] = vendor_code
        entry["supplier_name"] = supplier_name
    return {
        "as_of": resolved_as_of.isoformat(),
        "suppliers": entries,
    }
