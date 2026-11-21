"""The plan release PO readback: the stored orders' answers from Acumatica.

``refresh_plan_release_readbacks()`` answers what the released
purchase orders carry in Acumatica now: it is not a route, a
maintenance entry point.  It reads every ``plan_releases`` row whose
outcome is ``created`` or ``exists``, whose ``order_nbr`` is
non-null, and whose ``erp_status`` is null or neither ``Completed``,
``Closed`` nor ``Canceled``, and calls
``AcumaticaClient.read_purchase_orders`` once per chunk of at most
``_MAX_READBACK_PER_CALL`` of their distinct order numbers, in
ascending order, merging the answers.  For every number the ERP
returns, each row with that number gets the answer's ``status`` in
``erp_status``, the answer's ``lines`` — exactly as returned — in
``erp_lines``, and now in ``erp_checked_at``; a number the ERP does
not return leaves its rows unchanged.

Returns ``{"checked", "updated"}`` — the order numbers sent to the
ERP and the rows whose readback was stored.  With no row to read it
returns ``{"checked": 0, "updated": 0}`` and makes no call.  When
Acumatica is not configured it returns ``{"checked": 0, "updated": 0,
"skipped": "not configured"}`` without raising and without touching a
row, as ``refresh_transfer_release_statuses`` does.
"""

# The order numbers one readback call may send to Acumatica: the
# client's per-call limit for a PO read.
_MAX_READBACK_PER_CALL = 100


def refresh_plan_release_readbacks() -> dict:
    """Read the stored release orders back from Acumatica.

    Not a route: a maintenance entry point.  Reads every
    ``plan_releases`` row whose outcome is ``created`` or ``exists``,
    whose ``order_nbr`` is non-null, and whose ``erp_status`` is null
    or neither ``Completed``, ``Closed`` nor ``Canceled``, and calls
    ``AcumaticaClient.read_purchase_orders`` once per chunk of at most
    ``_MAX_READBACK_PER_CALL`` of their distinct order numbers, in
    ascending order, merging the answers.  For every number the ERP
    returns, each row with that number gets the answer's ``status`` in
    ``erp_status``, the answer's ``lines`` — exactly as returned — in
    ``erp_lines``, and now in ``erp_checked_at``; a number the ERP
    does not return leaves its rows unchanged.

    Returns ``{"checked", "updated"}`` — the order numbers sent to the
    ERP and the rows whose readback was stored.  With no row to read
    it returns ``{"checked": 0, "updated": 0}`` and makes no call.
    When Acumatica is not configured it returns ``{"checked": 0,
    "updated": 0, "skipped": "not configured"}`` without raising and
    without touching a row, as ``refresh_transfer_release_statuses``
    does.
    """
    from app.feed.acumatica_client import AcumaticaClient, AcumaticaConfigError
    from app.repositories.plan_releases_postgres_repository import (
        PlanReleasePostgresRepository,
    )

    try:
        AcumaticaClient()._load_config()
    except AcumaticaConfigError:
        return {"checked": 0, "updated": 0, "skipped": "not configured"}

    repo = PlanReleasePostgresRepository()
    order_nbrs = repo.list_readback_order_nbrs()
    if not order_nbrs:
        return {"checked": 0, "updated": 0}

    found: dict[str, dict] = {}
    client = AcumaticaClient()
    for start in range(0, len(order_nbrs), _MAX_READBACK_PER_CALL):
        chunk = order_nbrs[start : start + _MAX_READBACK_PER_CALL]
        found.update(client.read_purchase_orders(chunk))
    updated = repo.mark_erp_readbacks(found)
    return {"checked": len(order_nbrs), "updated": updated}
