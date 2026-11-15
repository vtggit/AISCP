"""Odoo -> AIERP read-then-write sync runner.

``run_sync`` reads all six entity sets through an
:class:`app.feed.odoo_client.OdooClient` — ``read_item_sites()``,
``read_on_hand(as_of)``, ``read_demand_events()``,
``read_lead_time_observations()``,
``read_open_purchase_lines(as_of)``, and ``read_open_sales_lines(as_of)``
— and only after every read succeeds writes each row list through the
matching Postgres repository's ``upsert_many``. If any of the six
reads raises, the error propagates and no ``upsert_many`` is called: a
failed extract never half-writes.

After the six reads, ``run_sync`` reads ``read_supplier_terms()`` as an
optional seventh: it is skipped — the six read models proceed either
way — when the client has no such method or when it raises
``OdooError`` (Odoo may refuse the ``product.supplierinfo`` model for a
user without purchase rights). When the read happens, its rows are
written through the ``supplier_terms`` repository and the result gains
``supplier_terms`` alongside the six table names.

After the supplier-terms read, ``run_sync`` reads ``read_products()``
as an optional eighth, the same way: it is skipped — the other read
models proceed either way — when the client has no such method or when
it raises ``OdooError``, and a skipped read writes no inventory items
and adds no result key.  When the read happens, its rows pass the
shared item-name guard
(:func:`app.sync.clashes.apply_item_name_guard`) — the rows of a code
``inventory_items`` stores under a different ``source_system`` are
removed, the first holder keeps its row exactly as it was, and an item
master row names no site so nothing is added to ``erp_code_clashes`` —
and are then written through the ``inventory_items`` repository and
the result gains ``inventory_items`` alongside the six table names.
In both runners, when a row is removed the result gains
``item_names_held`` — the number of rows removed — only when it is
above 0, so the result shape is unchanged when nothing is held.  The
guard is skipped when the injected inventory-items repository has no
``held_by_others``, the way the runner skips an optional reader the
client does not have.

After the products read, ``run_sync`` reads
``read_company_currencies()`` as an optional ninth, the same way:
it is skipped — the other read models proceed either way — when
the client has no such method or when it raises ``OdooError``,
and a skipped read changes nothing stored and adds no result key.
When the read happens, the codes are stored for ``odoo`` through
``ErpSourceCurrencyPostgresRepository.replace_for`` — exactly the
codes the read answered, in one transaction — and the result gains
``currencies``, the list of codes, alongside the table names.

Between the last read and the first write, ``run_sync`` runs the
shared code-clash guard
(:func:`app.sync.clashes.apply_clash_guard`) over the six row lists:
rows of a pair that ``item_sites`` already stores under a different
``source_system`` are removed — the first holder keeps its row exactly
as it was — each held pair is recorded in ``erp_code_clashes`` and
this source's still-open clash rows whose pair was not held this time
get ``resolved_at`` set.  When a pair is held, the result gains
``clashes`` — the number of held pairs — alongside the six table
names; with no clash the result shape is unchanged.  The guard is
skipped when the injected item-sites repository has no
``held_by_others``, the way the runner skips an optional reader the
client does not have.

The runner derives one ``as_of`` snapshot value once — the given
``as_of``, or the current UTC time at the sync's start — rendered as a
non-empty ISO-8601 string, and passes that same value to
``read_on_hand``, ``read_open_purchase_lines`` and
``read_open_sales_lines``, so one sync run shares
one snapshot time and the readers stamp the supplied value instead of
re-deriving ``now()``.

A string ``as_of`` must parse as ISO-8601; a non-ISO string raises
``ValueError`` before any read, so an unvalidated snapshot value can
never reach a reader. The client is used read-only: the runner calls
only the client's read methods, never constructs ``business_key``s or
reshapes rows (the readers already did), and writes nothing beyond the
repositories' ``upsert_many``. When the runner built the client itself
(``client`` omitted), it is closed before returning — on the failure
path a failing close is swallowed so it never masks the in-flight
read or write error.
"""

import contextlib
from datetime import UTC, date, datetime

from app.feed.odoo_client import OdooClient, OdooError
from app.repositories.demand_events_postgres_repository import (
    DemandEventPostgresRepository,
)
from app.repositories.erp_source_currencies_postgres_repository import (
    ErpSourceCurrencyPostgresRepository,
)
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.lead_time_observations_postgres_repository import (
    LeadTimeObservationPostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.repositories.open_sales_lines_postgres_repository import (
    OpenSalesLinePostgresRepository,
)
from app.repositories.supplier_terms_postgres_repository import (
    SupplierTermsPostgresRepository,
)
from app.sync.clashes import apply_clash_guard, apply_item_name_guard

__all__ = ["run_sync"]


def _snapshot_as_of(as_of: object) -> str:
    """The run's one shared snapshot time, as a non-empty ISO-8601 string.

    A non-blank string must parse as ISO-8601 or ``ValueError`` is
    raised, so a non-ISO string can never reach a reader as the
    snapshot; a datetime is rendered in UTC as ``YYYY-MM-DDTHH:MM:SSZ``
    (a naive datetime is taken as UTC); a bare date as ``YYYY-MM-DD``;
    ``None`` — and a blank string — becomes the current UTC time,
    derived once. Any other type raises ``TypeError`` so a malformed
    value can never reach a reader.
    """
    if isinstance(as_of, str):
        text = as_of.strip()
        if text:
            try:
                datetime.fromisoformat(text)
            except ValueError as exc:
                raise ValueError(
                    f"as_of must be a non-empty ISO-8601 string, got {text!r}"
                ) from exc
            return text
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(as_of, datetime):
        moment = as_of
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(as_of, date):
        return as_of.isoformat()
    if as_of is None:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    raise TypeError(
        "as_of must be an ISO-8601 string, a datetime, a date, or None, "
        f"got {type(as_of).__name__}"
    )


def run_sync(
    client: OdooClient | None = None,
    as_of: str | datetime | date | None = None,
    item_sites_repo: ItemSitePostgresRepository | None = None,
    on_hand_repo: OnHandPostgresRepository | None = None,
    demand_events_repo: DemandEventPostgresRepository | None = None,
    lead_time_observations_repo: LeadTimeObservationPostgresRepository | None = None,
    open_purchase_lines_repo: OpenPurchaseLinePostgresRepository | None = None,
    open_sales_lines_repo: OpenSalesLinePostgresRepository | None = None,
    supplier_terms_repo: SupplierTermsPostgresRepository | None = None,
    inventory_items_repo: InventoryItemPostgresRepository | None = None,
    currencies_repo: ErpSourceCurrencyPostgresRepository | None = None,
) -> dict:
    """Read all six entity sets, then write each row list once.

        Reads item sites, on hand, demand events, lead time observations, open
        purchase lines and open sales lines through ``client`` (defaulting to a new
        :class:`app.feed.odoo_client.OdooClient` that the runner closes before
        returning). Every read completes before any write, so a failed
        extract never half-writes: if any read raises, the error propagates
        and no ``upsert_many`` is called.

        Only after all six reads succeed, each row list is written through
        the matching repository's ``upsert_many`` (each repository defaulting
        to its Postgres implementation), and the result maps the six table
        names — ``item_sites``, ``on_hand``, ``demand_events``,
        ``lead_time_observations``, ``open_purchase_lines``,
        ``open_sales_lines`` — to that repository's ``upsert_many`` return
        value.

        After the six reads, ``read_supplier_terms()`` is read as an
        optional seventh: it is skipped — the six read models proceed
        either way — when the client has no such method or when it raises
        ``OdooError`` (Odoo may refuse ``product.supplierinfo`` for a user
        without purchase rights). When the read happens, its rows are
        written through the ``supplier_terms`` repository (defaulting to
        the Postgres implementation) and the result gains
        ``supplier_terms`` alongside the six table names.

        After the supplier-terms read, ``read_products()`` is read as an
        optional eighth, the same way: it is skipped — the other read
        models proceed either way — when the client has no such method or
        when it raises ``OdooError``, and a skipped read writes no
        inventory items and adds no result key.  When the read happens,
        the shared item-name guard (``app.sync.clashes.
        apply_item_name_guard``) removes the rows of a code
        ``inventory_items`` stores under a different ``source_system`` —
        the first holder keeps its row exactly as it was — and the rows
        are written through the ``inventory_items`` repository (defaulting
        to the Postgres implementation); the result gains
        ``inventory_items`` alongside the six table names, and, only when
        a row was removed, ``item_names_held`` — the number of rows
        removed.  An item master row names no site, so nothing is added to
        ``erp_code_clashes``.  The guard is skipped when the injected
        inventory-items repository has no ``held_by_others``.

        After the products read, ``read_company_currencies()`` is
    read as an optional ninth, the same way: it is skipped — the
    other read models proceed either way — when the client has no
    such method or when it raises ``OdooError``, and a skipped read
    changes nothing stored and adds no result key.  When the read
    happens, the codes are stored for ``odoo`` through the
    ``erp_source_currencies`` repository's ``replace_for`` (defaulting
    to the Postgres implementation) — exactly the codes the read
    answered, in one transaction — and the result gains
    ``currencies``, the list of codes, alongside the table names.

        Between the last read and the first write, the shared code-clash
        guard (``app.sync.clashes.apply_clash_guard``) removes rows of a
        pair ``item_sites`` stores under a different ``source_system`` —
        the first holder keeps its row — records each held pair in
        ``erp_code_clashes`` and resolves this source's still-open clash
        rows whose pair was not held this time.  When a pair is held the
        result gains ``clashes`` — the number of held pairs — alongside
        the six table names; with no clash the result shape is unchanged.
        The guard is skipped when the injected item-sites repository has
        no ``held_by_others``.

        One ``as_of`` snapshot value is derived once — the given ``as_of``,
        or the current UTC time at the sync's start — and the same non-empty
        ISO-8601 string is passed to ``read_on_hand``,
        ``read_open_purchase_lines`` and ``read_open_sales_lines`` so one
        sync run shares one snapshot time. The client is used read-only:
        no ``business_key`` is built and no row is reshaped here.
    """
    snapshot = _snapshot_as_of(as_of)
    owns_client = client is None
    if owns_client:
        client = OdooClient()
    try:
        # Read first: all six reads complete before any write.
        item_site_rows = client.read_item_sites()
        on_hand_rows = client.read_on_hand(snapshot)
        demand_event_rows = client.read_demand_events()
        lead_time_rows = client.read_lead_time_observations()
        open_purchase_line_rows = client.read_open_purchase_lines(snapshot)
        open_sales_line_rows = client.read_open_sales_lines(snapshot)

        # Optional seventh read: skipped — the six read models proceed
        # either way — when the client has no such method or when it
        # raises OdooError (Odoo may refuse product.supplierinfo for a
        # user without purchase rights).
        supplier_terms_reader = getattr(client, "read_supplier_terms", None)
        supplier_term_rows: list[dict] | None = None
        if supplier_terms_reader is not None:
            try:
                supplier_term_rows = supplier_terms_reader()
            except OdooError:
                supplier_term_rows = None

        # Optional eighth read: skipped — the other read models
        # proceed either way — when the client has no such method or
        # when it raises OdooError; a skipped read writes no inventory
        # items and adds no result key.
        inventory_items_reader = getattr(client, "read_products", None)
        inventory_item_rows: list[dict] | None = None
        if inventory_items_reader is not None:
            try:
                inventory_item_rows = inventory_items_reader()
            except OdooError:
                inventory_item_rows = None

        # Optional ninth read: skipped — the other read models
        # proceed either way — when the client has no such method
        # or when it raises OdooError; a skipped read changes
        # nothing stored and adds no result key.
        currencies_reader = getattr(client, "read_company_currencies", None)
        currency_codes: list[str] | None = None
        if currencies_reader is not None:
            try:
                currency_codes = currencies_reader()
            except OdooError:
                currency_codes = None

        if item_sites_repo is None:
            item_sites_repo = ItemSitePostgresRepository()
        if on_hand_repo is None:
            on_hand_repo = OnHandPostgresRepository()
        if demand_events_repo is None:
            demand_events_repo = DemandEventPostgresRepository()
        if lead_time_observations_repo is None:
            lead_time_observations_repo = LeadTimeObservationPostgresRepository()
        if open_purchase_lines_repo is None:
            open_purchase_lines_repo = OpenPurchaseLinePostgresRepository()
        if open_sales_lines_repo is None:
            open_sales_lines_repo = OpenSalesLinePostgresRepository()

        if supplier_term_rows is not None and supplier_terms_repo is None:
            supplier_terms_repo = SupplierTermsPostgresRepository()

        if inventory_item_rows is not None and inventory_items_repo is None:
            inventory_items_repo = InventoryItemPostgresRepository()

        if currency_codes is not None and currencies_repo is None:
            currencies_repo = ErpSourceCurrencyPostgresRepository()

        # Then the shared code-clash guard: it removes rows of a pair
        # item_sites stores under a different source_system — the
        # first holder keeps its row — records each held pair in
        # erp_code_clashes and resolves this source's still-open clash
        # rows whose pair was not held this time.  Skipped when the
        # item-sites repository has no held_by_others.
        clash_count = apply_clash_guard(
            "odoo",
            [
                item_site_rows,
                on_hand_rows,
                demand_event_rows,
                lead_time_rows,
                open_purchase_line_rows,
                open_sales_line_rows,
            ],
            item_sites_repo,
        )

        # Then the item-name guard: it removes the rows of a code
        # inventory_items stores under a different source_system — the
        # first holder keeps its row exactly as it was — and an item
        # master row names no site, so nothing is added to
        # erp_code_clashes.  Skipped when the inventory-items
        # repository has no held_by_others.
        item_names_held = 0
        if inventory_item_rows is not None:
            item_names_held = apply_item_name_guard(
                "odoo", inventory_item_rows, inventory_items_repo
            )

        # Then write: only after every read succeeded.
        result = {
            "item_sites": item_sites_repo.upsert_many(item_site_rows),
            "on_hand": on_hand_repo.upsert_many(on_hand_rows),
            "demand_events": demand_events_repo.upsert_many(demand_event_rows),
            "lead_time_observations": lead_time_observations_repo.upsert_many(
                lead_time_rows
            ),
            "open_purchase_lines": open_purchase_lines_repo.upsert_many(
                open_purchase_line_rows
            ),
            "open_sales_lines": open_sales_lines_repo.upsert_many(open_sales_line_rows),
        }
        if supplier_term_rows is not None:
            result["supplier_terms"] = supplier_terms_repo.upsert_many(
                supplier_term_rows
            )
        if inventory_item_rows is not None:
            result["inventory_items"] = inventory_items_repo.upsert_many(
                inventory_item_rows
            )
        if currency_codes is not None:
            # Exactly the codes the read answered, stored for
            # odoo in one transaction; the result lists the codes.
            currencies_repo.replace_for("odoo", currency_codes)
            result["currencies"] = currency_codes
        if item_names_held > 0:
            result["item_names_held"] = item_names_held
        if clash_count > 0:
            result["clashes"] = clash_count
    except BaseException:
        # Close a runner-built client even when a read or write failed,
        # but a failing close must never mask the in-flight error: the
        # original exception propagates unchanged.
        if owns_client:
            with contextlib.suppress(Exception):
                client.close()
        raise
    if owns_client:
        client.close()
    return result
