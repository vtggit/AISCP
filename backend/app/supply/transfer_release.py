"""The transfer release plan: the chosen transfer suggestions as orders.

``transfer_release_plan(entries, as_of=None)`` answers which of the
current transfer suggestions — the suggestions the
``GET /api/supply-plan/transfers`` builder
(``app.api.supply_plan.build_transfer_suggestions``) produces for
``as_of`` — go out as ON-HOLD transfer orders in Acumatica for the
requested entries, as the plan dict ``{"as_of", "groups",
"unreleasable"}``.

Each requested entry — ``{"item_code", "from_site", "to_site"}``,
never a quantity of its own — is matched by (``item_code``,
``from_site``, ``to_site``) to a current suggestion, and the matched
quantity is the suggestion's ``quantity``: the request names the
suggestions to release, and the server's current numbers are the ones
released.  An entry that matches no suggestion is ``unreleasable`` as
``{"item_code", "from_site", "to_site", "reason": "no longer
suggested"}``, and a suggestion whose ``source_system`` is not
``acumatica`` is ``unreleasable`` as ``"only Acumatica transfers are
released here"`` — the reason the named lines are not released.  The
matched entries are grouped per (``from_site``, ``to_site``) — one
group per pair, in the order the pair first appears in the request,
the lines in request order — each tagged with its origin ``"AIERP
transfer <as_of> site <from_site> to <to_site>"``, the idempotency
tag the transfer order is written under in Acumatica.  The as-of is
the resolved as-of date (``app.stats.demand.default_as_of``), unless
``as_of`` is given, in which case that date is the as-of and
``default_as_of`` is never consulted — the confirm passes the
snapshot it resolved after taking the release's advisory lock, so the
audit row records the same snapshot the release used.  When
``suggestions`` is given — a dict of the shape
``app.api.supply_plan.build_transfer_suggestions`` answers — the
entries are matched against its ``suggestions`` instead, the plan's
``as_of`` and its origin tags use its ``as_of``, ``as_of`` is not
used and ``build_transfer_suggestions`` is not called — the
standing-grant run passes the read it took, so a grant's orders and
a person's orders for the same sites on the same data carry the same
origin tag.  Otherwise the function reads the current suggestions
once, through the same builder the GET route uses, and writes
nothing.
"""

NO_LONGER_SUGGESTED_REASON = "no longer suggested"
NOT_ACUMATICA_REASON = "only Acumatica transfers are released here"


def transfer_release_plan(entries: list[dict], as_of=None, suggestions=None) -> dict:
    """The ON-HOLD transfer-order release plan of the requested entries.

    See the module docstring for the full contract: the current
    suggestions matched by (item_code, from_site, to_site), the
    matched quantity the suggestion's quantity, one group per
    (from_site, to_site) with the origin tag and the lines in request
    order, the unmatched and non-Acumatica entries as
    ``unreleasable``.  ``suggestions`` — when it is given — the read
    to match against, of the shape
    ``app.api.supply_plan.build_transfer_suggestions`` answers: the
    entries are matched against its ``suggestions``, the plan's
    ``as_of`` and its origin tags use its ``as_of``, ``as_of`` is not
    used and the current suggestions are never read.  Returns
    ``{"as_of", "groups", "unreleasable"}`` and writes nothing.
    """
    if suggestions is None:
        # Imported here: app.api.supply_plan imports this module's
        # plan for its release route, so the builder must not be
        # imported at module level (it would be a cycle).
        from app.api.supply_plan import build_transfer_suggestions

        suggestions = build_transfer_suggestions(as_of)
    by_key = {
        (
            suggestion["item_code"],
            suggestion["from_site"],
            suggestion["to_site"],
        ): suggestion
        for suggestion in suggestions["suggestions"]
    }

    groups: dict[tuple[str, str], dict] = {}
    unreleasable: list[dict] = []
    for entry in entries:
        key = (entry["item_code"], entry["from_site"], entry["to_site"])
        suggestion = by_key.get(key)
        if suggestion is None:
            unreleasable.append({**entry, "reason": NO_LONGER_SUGGESTED_REASON})
            continue
        if suggestion.get("source_system") != "acumatica":
            unreleasable.append({**entry, "reason": NOT_ACUMATICA_REASON})
            continue
        group = groups.setdefault(
            (entry["from_site"], entry["to_site"]),
            {
                "origin": (
                    f"AIERP transfer {suggestions['as_of'].isoformat()} "
                    f"site {entry['from_site']} to {entry['to_site']}"
                ),
                "from_site": entry["from_site"],
                "to_site": entry["to_site"],
                "lines": [],
            },
        )
        group["lines"].append(
            {"item_code": entry["item_code"], "quantity": suggestion["quantity"]}
        )

    return {
        "as_of": suggestions["as_of"],
        "groups": list(groups.values()),
        "unreleasable": unreleasable,
    }


def _wire(groups: list[dict]) -> list[dict]:
    """The release groups as the client's transfer batch entries.

    Each group becomes a ``{from_site_id, to_site_id, origin, lines}``
    entry — the site codes as the id strings the client normalizes —
    with the group's exact lines, ``{item_code, quantity}``, in order.
    """
    return [
        {
            "from_site_id": group["from_site"],
            "to_site_id": group["to_site"],
            "origin": group["origin"],
            "lines": [
                {"item_code": line["item_code"], "quantity": int(line["quantity"])}
                for line in group["lines"]
            ],
        }
        for group in groups
    ]


def preview_transfer_release(groups: list[dict]) -> list[dict]:
    """The Acumatica preview of the release groups, one client call.

    ``AcumaticaClient.preview_transfer_orders`` once with the groups'
    wire entries — one per-entry result ``{origin, outcome,
    reference_nbr, status, from_warehouse_code, to_warehouse_code,
    line_count, error}`` in input order, the ones the release route
    copies onto its groups.  An auth or transport failure raises for
    the whole batch; an empty batch returns ``[]`` with no request.
    """
    from app.feed.acumatica_client import AcumaticaClient

    return AcumaticaClient().preview_transfer_orders(_wire(groups))


def create_transfer_release(groups: list[dict]) -> list[dict]:
    """The Acumatica write of the release groups, one client call.

    ``AcumaticaClient.create_transfer_orders`` once with the groups'
    wire entries — the ON-HOLD transfer orders, one per-entry result
    ``{origin, outcome, reference_nbr, status, from_warehouse_code,
    to_warehouse_code, line_count, error}`` in input order, the ones
    the release route updates its pending rows with.  An auth or
    transport failure raises for the whole batch; an empty batch
    returns ``[]`` with no request.
    """
    from app.feed.acumatica_client import AcumaticaClient

    return AcumaticaClient().create_transfer_orders(_wire(groups))


def transfer_write_probe() -> None:
    """Consult the write switch the way the client consults it.

    ``AcumaticaClient.create_transfer_orders`` with an empty batch —
    before any request, no write: it raises
    ``AcumaticaWritesDisabledError`` when writes are off and answers
    with an empty result otherwise.
    """
    from app.feed.acumatica_client import AcumaticaClient

    AcumaticaClient().create_transfer_orders([])


# The reference numbers one refresh call may send to Acumatica: the
# client's per-call limit for a status read.
_MAX_STATUS_READ_PER_CALL = 100


def refresh_transfer_release_statuses() -> dict:
    """Read the stored transfer orders' statuses back from Acumatica.

    Not a route: a maintenance entry point.  Reads every
    ``transfer_releases`` row whose outcome is ``created`` or
    ``exists``, whose ``reference_nbr`` is non-null, and whose
    ``erp_status`` is null or neither ``Released`` nor ``Canceled``,
    and calls
    ``AcumaticaClient.read_transfer_order_statuses`` once per chunk of
    at most ``_MAX_STATUS_READ_PER_CALL`` of their distinct reference
    numbers, merging the answers.  For every number the ERP returns,
    the row's ``erp_status`` is set to the answer and its
    ``erp_status_checked_at`` to now; a number the ERP does not return
    is left unchanged.

    Returns ``{"checked", "updated"}`` — the reference numbers sent to
    the ERP and the rows whose status was stored.  When Acumatica is
    not configured it returns ``{"checked": 0, "updated": 0,
    "skipped": "not configured"}`` without raising and without
    touching a row.
    """
    from app.feed.acumatica_client import AcumaticaClient, AcumaticaConfigError
    from app.repositories.transfer_releases_postgres_repository import (
        TransferReleasePostgresRepository,
    )

    try:
        AcumaticaClient()._load_config()
    except AcumaticaConfigError:
        return {"checked": 0, "updated": 0, "skipped": "not configured"}

    repo = TransferReleasePostgresRepository()
    reference_nbrs = repo.list_status_refresh_reference_nbrs()
    if not reference_nbrs:
        return {"checked": 0, "updated": 0}

    found: dict[str, str] = {}
    client = AcumaticaClient()
    for start in range(0, len(reference_nbrs), _MAX_STATUS_READ_PER_CALL):
        chunk = reference_nbrs[start : start + _MAX_STATUS_READ_PER_CALL]
        found.update(client.read_transfer_order_statuses(chunk))
    updated = repo.mark_erp_statuses(found)
    return {"checked": len(reference_nbrs), "updated": updated}
