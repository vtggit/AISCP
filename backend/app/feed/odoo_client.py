"""Fail-closed client for an Odoo database's JSON-RPC endpoint.

``OdooClient`` talks to one Odoo database over its ``/jsonrpc`` endpoint.
``authenticate()`` posts ``common.authenticate`` once and caches the uid
for the client's lifetime; ``search_read()`` posts ``object.execute_kw``
against one model, carrying only the given fields, limit, offset and,
when given, order. ``read_products()`` maps ``product.product`` records
onto the ``InventoryItemCreate`` shape, ``read_on_hand()`` sums internal
``stock.quant`` quantities per item and site onto the ``OnHandUpsert``
shape, ``read_item_sites()`` maps the distinct item/site pairs holding
internal stock onto the ``ItemSiteUpsert`` shape, ``read_demand_events()``
maps completed customer shipments (``stock.move``) onto the
``DemandEventUpsert`` shape, ``read_lead_time_observations()`` maps
completed supplier receipts (``stock.move``) onto the
``LeadTimeObservationUpsert`` shape, ``read_open_purchase_lines()``
maps open confirmed ``purchase.order.line`` rows onto the
``OpenPurchaseLineUpsert`` shape, ``read_open_sales_lines()`` maps
``sale.order.line`` rows of ``sale``, ``done`` or ``cancel`` orders
onto the ``open_sales_lines`` upsert shape,
``read_supplier_terms()`` maps ``product.supplierinfo`` rows onto the
``supplier_terms`` upsert shape, ``read_company_currencies()``
answers the sorted distinct uppercase currency codes of
``res.company``, and
``create_draft_purchase_order()`` creates one DRAFT ``purchase.order``
for an item/site/supplier
triple, idempotent on an origin tag and gated by the
``ODOO_ALLOW_WRITES`` write switch, and
``preview_draft_purchase_order()`` read-only previews that same
resolution and existing-draft lookup: only ``search_read`` reads,
never a create, and it does not consult the write switch.
``write_reorder_rule()`` writes the ``product_min_qty`` and
``product_max_qty`` of the one active ``stock.warehouse.orderpoint``
for an item/site, gated by the same write switch, and verifies the
change by reading the same rule id back.

Configuration is read from the environment at call time:

- ``ODOO_BASE_URL``: http(s) origin, no path, query, or fragment; must be
  ``https://`` unless ``ODOO_ALLOW_INSECURE`` is set to ``1`` or ``true``.
- ``ODOO_DB``: the Odoo database name.
- ``ODOO_LOGIN``: the login sent to ``common.authenticate``.
- ``ODOO_API_KEY``: the API key sent to ``common.authenticate``.
- ``ODOO_TIMEOUT_SECONDS``: optional per-request timeout in seconds, read
  at call time; a positive number (an integer or a decimal such as ``90``
  or ``12.5``) replaces the 30-second default, while unset, blank, zero,
  negative, or non-numeric values keep it.
- ``ODOO_CA_BUNDLE``: optional CA bundle path for TLS verification; blank
  after trimming whitespace is treated as unset.
- ``ODOO_ALLOW_INSECURE``: optional; only ``1`` or ``true`` accepts an
  ``http://`` base URL.
- ``ODOO_ALLOW_WRITES``: optional; only ``1``, ``true``, or ``yes``
  (case-insensitive, whitespace-trimmed) enables the write paths;
  anything else makes the write paths raise
  ``OdooWritesDisabledError`` before any request.

The client fails closed: configuration and argument problems raise before
any request, every request goes only to the configured origin (scheme,
host and port) so the API key never reaches another host, and no error
message contains the API key, the login, or a response body.
Authenticate-phase failures (transport, JSON-RPC error, ``false``, or
non-integer result) raise ``OdooAuthError``; ``search_read``-phase
failures raise ``OdooError`` naming the model. The write paths fail
closed on a switch: unless ``ODOO_ALLOW_WRITES`` is enabled,
``create_draft_purchase_order`` and ``write_reorder_rule`` raise
``OdooWritesDisabledError`` before authentication, configuration
validation, or any request. The client performs no automatic retries.
"""

import json
import math
import os
import re
import ssl
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

__all__ = [
    "OdooAuthError",
    "OdooClient",
    "OdooConfigError",
    "OdooError",
    "OdooWriteVerificationError",
    "OdooWritesDisabledError",
]

BASE_URL_VAR = "ODOO_BASE_URL"
DB_VAR = "ODOO_DB"
LOGIN_VAR = "ODOO_LOGIN"
API_KEY_VAR = "ODOO_API_KEY"
CA_BUNDLE_VAR = "ODOO_CA_BUNDLE"
TIMEOUT_VAR = "ODOO_TIMEOUT_SECONDS"
ALLOW_INSECURE_VAR = "ODOO_ALLOW_INSECURE"
ALLOW_WRITES_VAR = "ODOO_ALLOW_WRITES"

_JSONRPC_PATH = "/jsonrpc"
_REQUEST_TIMEOUT = 30.0
_REORDER_TOLERANCE = 1e-6
_MODEL_RE = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")
_BASE_URL_ERROR = (
    f"{BASE_URL_VAR} must be an http(s) origin with no path, query, or fragment"
)
_INSECURE_URL_ERROR = (
    f"{BASE_URL_VAR} must use https unless {ALLOW_INSECURE_VAR} is set to 1 or true"
)


class OdooError(Exception):
    """An Odoo request failed.

    The message names the model (for ``search_read``-phase failures) or
    the failure class; it never contains the API key, the login, or a
    response body.
    """


class OdooConfigError(OdooError):
    """Odoo configuration is missing or invalid.

    The message names the offending environment variable and never its value.
    """


class OdooAuthError(OdooError):
    """The ``common.authenticate`` phase failed.

    Raised for every authenticate-phase failure (transport, JSON-RPC
    error, ``false``, or non-integer result); the message names the
    failure class but never the API key, the login, or a response body.
    """


class OdooWritesDisabledError(OdooError):
    """The explicit write switch is off.

    Raised by the write paths (``create_draft_purchase_order`` and
    ``write_reorder_rule``) when ``ODOO_ALLOW_WRITES`` is not set to
    an enabling value; the message is fixed and never contains
    environment values, credentials, or request payloads. It is not an
    authentication failure.
    """


class OdooWriteVerificationError(OdooError):
    """The read-back after a write did not match the intended values.

    Raised by ``write_reorder_rule`` when the
    ``stock.warehouse.orderpoint`` read back after a ``write`` does not
    carry the intended ``product_min_qty`` and ``product_max_qty``; the
    message names the item, the site, and the field that did not match,
    and never contains the API key, the login, or a response body. A
    read-back transport or JSON-RPC failure raises the plain
    ``OdooError`` instead and is never reported as a successful
    verification.
    """


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise OdooConfigError(f"{name} must be set to a non-blank value")
    return value


def _allow_insecure() -> bool:
    raw = os.environ.get(ALLOW_INSECURE_VAR)
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true")


def _writes_enabled() -> bool:
    """Return True only when ``ODOO_ALLOW_WRITES`` enables writes.

    Enabled only for ``1``, ``true``, or ``yes`` (case-insensitive,
    whitespace-trimmed); unset, blank, or any other value keeps the
    write path disabled.
    """
    raw = os.environ.get(ALLOW_WRITES_VAR)
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes")


def _validate_base_url(base_url: str, allow_insecure: bool) -> str:
    """Return the origin with any trailing slash dropped, or raise."""
    if base_url != base_url.strip():
        raise OdooConfigError(_BASE_URL_ERROR)
    try:
        parts = urlsplit(base_url)
        hostname = parts.hostname
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        raise OdooConfigError(_BASE_URL_ERROR) from None
    scheme = parts.scheme.lower()
    if (
        scheme not in ("http", "https")
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise OdooConfigError(_BASE_URL_ERROR)
    if scheme == "http" and not allow_insecure:
        raise OdooConfigError(_INSECURE_URL_ERROR)
    return base_url.rstrip("/")


def _ca_bundle_path() -> str | None:
    raw = os.environ.get(CA_BUNDLE_VAR)
    if raw is None:
        return None
    trimmed = raw.strip()
    return trimmed or None


def _request_timeout() -> float:
    """Return the per-request timeout in seconds, read at call time.

    A positive finite number (an integer or a decimal such as ``90``
    or ``12.5``) wins; unset, blank, zero, negative, non-numeric, or
    non-finite values fall back to the 30-second default.
    """
    raw = os.environ.get(TIMEOUT_VAR)
    if raw is None:
        return _REQUEST_TIMEOUT
    try:
        value = float(raw.strip())
    except ValueError:
        return _REQUEST_TIMEOUT
    if not math.isfinite(value) or value <= 0:
        return _REQUEST_TIMEOUT
    return value


def _ensure_ca_bundle_readable(ca_bundle: str | None) -> None:
    if ca_bundle is None:
        return
    try:
        with open(ca_bundle, "rb"):
            pass
    except OSError:
        raise OdooConfigError(
            f"{CA_BUNDLE_VAR} names a file that does not exist or cannot be read"
        ) from None


def _build_client(ca_bundle: str | None, timeout: float) -> httpx.Client:
    verify = ca_bundle if ca_bundle is not None else True
    try:
        return httpx.Client(verify=verify, timeout=timeout)
    except (ssl.SSLError, OSError) as exc:
        raise OdooConfigError(
            f"{CA_BUNDLE_VAR} could not be applied to the Odoo client "
            f"({type(exc).__name__})"
        ) from None


def _validate_model(model: object) -> None:
    if not isinstance(model, str) or _MODEL_RE.fullmatch(model) is None:
        raise ValueError(
            "model must be a dotted lowercase identifier such as 'product.product'"
        )


def _validate_fields(fields: object) -> list[str]:
    if not isinstance(fields, (list, tuple)):
        raise ValueError("fields must be a list of non-empty strings")
    for field in fields:
        if not isinstance(field, str) or not field:
            raise ValueError("fields must be a list of non-empty strings")
    return list(fields)


def _validate_limit_offset(limit: object, offset: object) -> None:
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
    ):
        raise ValueError("limit must be a non-negative int (not a bool) or None")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative int (not a bool)")


_ORDER_ITEM_RE = re.compile(r"^[a-z_][a-z0-9_.]*( asc| desc)?$", re.IGNORECASE)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _validate_order(order: object) -> None:
    if order is None:
        return
    if not isinstance(order, str) or not order.strip():
        raise ValueError("order must be a non-empty string or None")
    for item in order.split(","):
        if _ORDER_ITEM_RE.fullmatch(item.strip()) is None:
            raise ValueError(
                "order must name columns with an optional asc or desc "
                "direction, e.g. 'id asc' or 'date desc, id asc'"
            )


def _date_part(value: object) -> str | None:
    """Return the ``YYYY-MM-DD`` date part of an Odoo date or datetime.

    Accepts a bare date (``2026-10-15``) or a datetime (``2026-10-15
    14:30:00`` or ``2026-10-15T14:30:00``, with or without a timezone
    suffix); missing values, non-strings, and dates that do not parse
    (such as ``2026-02-30``) yield None.
    """
    if not isinstance(value, str):
        return None
    match = _DATE_RE.match(value.strip())
    if match is None:
        return None
    try:
        date.fromisoformat(match.group(0))
    except ValueError:
        return None
    return match.group(0)


def _uom_display_name(uom_id: object) -> str | None:
    """Return the display name of an Odoo ``[id, name]`` ref, or None."""
    if isinstance(uom_id, (list, tuple)) and len(uom_id) >= 2:
        name = uom_id[1]
        if isinstance(name, str):
            return name
    return None


def _supplier_code(partner: object) -> str | None:
    """Return a vendor's supplier code, or None.

    The vendor's ``ref`` when it is a non-empty string, else the
    vendor's ``name``; a partner with neither yields None.
    """
    if not isinstance(partner, dict):
        return None
    for field in ("ref", "name"):
        value = partner.get(field)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _ref_id(value: object) -> int | None:
    """Return the id of an Odoo m2o ref, or None when unresolvable.

    Accepts a bare id or the ``[id, name]`` pair; ``False``, ``None``,
    and other shapes yield None.
    """
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _finite_float(value: object) -> float | None:
    """Return a numeric value as a finite float, else None.

    Bools are not numbers here; non-numeric shapes (strings, refs,
    missing values) and non-finite values yield None, as do integers
    too large to convert to a float.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if not math.isfinite(value):
            return None
        return float(value)
    except OverflowError:
        return None


def _usable_unit_factor(factors: dict, unit_id: int | None) -> float | None:
    """Return the unit's factor when it is a usable base-unit factor.

    A usable factor is a finite number greater than zero; a missing
    unit, and a missing, zero, negative, or non-finite factor, yield
    None.
    """
    if unit_id is None:
        return None
    factor = _finite_float(factors.get(unit_id))
    if factor is None or factor <= 0:
        return None
    return factor


def _to_base_unit(
    quantity: float,
    line_unit: int | None,
    product_unit: int | None,
    factors: dict,
) -> float | None:
    """Convert a line quantity to the product's base unit.

    A line with no unit, or whose unit is the product's own unit, is
    taken as is. A line whose unit differs from the product's unit
    yields ``quantity * factor(line unit) / factor(product unit)``, or
    None when the conversion is unusable: the product has no unit,
    either factor is missing, zero, negative, or non-finite, or the
    converted quantity is not finite.
    """
    if line_unit is None or line_unit == product_unit:
        return quantity
    line_factor = _usable_unit_factor(factors, line_unit)
    if line_factor is None:
        return None
    product_factor = _usable_unit_factor(factors, product_unit)
    if product_factor is None:
        return None
    converted = quantity * line_factor / product_factor
    if not math.isfinite(converted):
        return None
    return converted


class OdooClient:
    """Fail-closed client for one Odoo database's JSON-RPC endpoint.

    ``client`` may inject an ``httpx.Client`` (used by tests to stub the
    transport with ``httpx.MockTransport``); when omitted the client builds
    its own from the call-time configuration (verified TLS, optional CA
    bundle, per-request timeout) and closes it in :meth:`close`. Every
    request goes only to the configured origin's ``/jsonrpc`` path. The uid
    obtained from ``common.authenticate`` is cached for the client's
    lifetime; the client performs no automatic retries.
    """

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client
        self._owns_client = client is None
        self._uid: int | None = None
        self._request_id = 0

    def close(self) -> None:
        """Close the owned HTTP client, if one was built. Idempotent."""
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "OdooClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- configuration -----------------------------------------------------

    def _load_config(self) -> tuple[str, str, str, str, str | None, float]:
        """Read and validate the environment at call time.

        Returns ``(origin, db, login, api_key, ca_bundle, timeout)``; raises
        ``OdooConfigError`` before any request when a value is missing or
        malformed.
        """
        base_url = _require_env(BASE_URL_VAR)
        origin = _validate_base_url(base_url, _allow_insecure())
        db = _require_env(DB_VAR)
        login = _require_env(LOGIN_VAR)
        api_key = _require_env(API_KEY_VAR)
        ca_bundle = _ca_bundle_path()
        _ensure_ca_bundle_readable(ca_bundle)
        timeout = _request_timeout()
        return origin, db, login, api_key, ca_bundle, timeout

    def _active_client(self, ca_bundle: str | None, timeout: float) -> httpx.Client:
        if self._client is None:
            self._client = _build_client(ca_bundle, timeout)
        return self._client

    # -- JSON-RPC core -----------------------------------------------------

    @staticmethod
    def _failure(error_cls: type[OdooError], subject: str, detail: str) -> OdooError:
        return error_cls(f"{subject} {detail}")

    def _rpc(
        self,
        origin: str,
        ca_bundle: str | None,
        timeout: float,
        service: str,
        method: str,
        args: list[Any],
        error_cls: type[OdooError],
        subject: str,
    ) -> Any:
        """POST one JSON-RPC call to ``{origin}/jsonrpc``; return ``result``.

        ``subject`` names the caller in error messages (the model for
        ``search_read``-phase calls); no message ever contains the API key,
        the login, or a response body.
        """
        body = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service, "method": method, "args": args},
            "id": self._next_request_id(),
        }
        try:
            payload = json.dumps(body)
        except (TypeError, ValueError, RecursionError):
            raise ValueError(
                "Odoo request arguments must be JSON-serializable"
            ) from None
        try:
            response = self._active_client(ca_bundle, timeout).post(
                f"{origin}{_JSONRPC_PATH}",
                content=payload,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise self._failure(
                error_cls, subject, f"failed with {type(exc).__name__}"
            ) from None
        except (ssl.SSLError, OSError) as exc:
            raise self._failure(
                error_cls, subject, f"failed with {type(exc).__name__}"
            ) from None
        if not 200 <= response.status_code < 300:
            raise self._failure(
                error_cls, subject, f"returned HTTP {response.status_code}"
            )
        try:
            envelope = response.json()
        except (ValueError, httpx.HTTPError) as exc:
            raise self._failure(
                error_cls,
                subject,
                f"response is not valid JSON ({type(exc).__name__})",
            ) from None
        if not isinstance(envelope, dict):
            raise self._failure(
                error_cls, subject, "returned a malformed JSON-RPC envelope"
            )
        if "error" in envelope:
            raise self._failure(error_cls, subject, "returned a JSON-RPC error")
        if "result" not in envelope:
            raise self._failure(
                error_cls, subject, "returned a malformed JSON-RPC envelope"
            )
        return envelope["result"]

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _ensure_uid(self) -> int:
        if self._uid is None:
            self._uid = self.authenticate()
        return self._uid

    # -- public API ---------------------------------------------------------

    def authenticate(self) -> int:
        """Post ``common.authenticate`` with ``[db, login, api_key, {}]``.

        Returns the uid. A transport failure, a JSON-RPC error body, a
        ``false`` result, or a non-integer result raises ``OdooAuthError``;
        the message names the failure class but never the API key, the
        login, or a response body.
        """
        origin, db, login, api_key, ca_bundle, timeout = self._load_config()
        subject = "Odoo authenticate"
        result = self._rpc(
            origin,
            ca_bundle,
            timeout,
            "common",
            "authenticate",
            [db, login, api_key, {}],
            OdooAuthError,
            subject,
        )
        if result is False:
            raise OdooAuthError("Odoo authenticate was refused by the server")
        if isinstance(result, bool) or not isinstance(result, int):
            raise OdooAuthError("Odoo authenticate returned a non-integer uid")
        return result

    def search_read(
        self,
        model: str,
        domain: Any,
        fields: list[str],
        limit: int | None = None,
        offset: int = 0,
        order: str | None = None,
    ) -> list[dict]:
        """Authenticate (caching the uid), then post ``object.execute_kw``.

        Posts ``[db, uid, api_key, model, "search_read", [domain], {...}]``
        carrying only the given ``fields``, ``limit`` and ``offset`` and,
        when given, ``order`` (forwarded verbatim, e.g. ``"id asc"``, so
        callers can guarantee deterministic, stable pagination), and
        returns the list of record dicts. A transport failure or a
        ``{"error": ...}`` body raises ``OdooError`` naming the model.
        """
        _validate_model(model)
        fields_list = _validate_fields(fields)
        _validate_limit_offset(limit, offset)
        _validate_order(order)
        uid = self._ensure_uid()
        origin, db, _login, api_key, ca_bundle, timeout = self._load_config()
        kwargs: dict[str, Any] = {"fields": fields_list, "offset": offset}
        if limit is not None:
            kwargs["limit"] = limit
        if order is not None:
            kwargs["order"] = order
        subject = f"Odoo request for model '{model}'"
        result = self._rpc(
            origin,
            ca_bundle,
            timeout,
            "object",
            "execute_kw",
            [db, uid, api_key, model, "search_read", [domain], kwargs],
            OdooError,
            subject,
        )
        if not isinstance(result, list) or not all(
            isinstance(row, dict) for row in result
        ):
            raise OdooError(f"{subject} returned a malformed result")
        return result

    def _read_product_code_and_unit(
        self, product_ids: set[int], order: str | None = None
    ) -> tuple[dict[int, str], dict[int, int | None]]:
        """One ``product.product`` read for ``default_code`` and ``uom_id``.

        Over the given product ids (an ``id in [...]`` domain), omitted
        entirely when no id is seen. Returns ``(product_code,
        product_unit)``; ``product_code`` holds only ids with a
        non-empty code, and ``product_unit`` maps every resolved
        product id to its unit id or None (the product has no unit).
        Shared by the readers that convert line quantities to the
        product's base unit: :meth:`read_demand_events`,
        :meth:`read_open_purchase_lines` and
        :meth:`read_open_sales_lines`.
        """
        product_code: dict[int, str] = {}
        product_unit: dict[int, int | None] = {}
        if product_ids:
            for record in self.search_read(
                "product.product",
                [["id", "in", sorted(product_ids)]],
                ["default_code", "uom_id"],
                order=order,
            ):
                record_id = _ref_id(record.get("id"))
                if record_id is None:
                    continue
                code = record.get("default_code")
                if isinstance(code, str) and code:
                    product_code[record_id] = code
                product_unit[record_id] = _ref_id(record.get("uom_id"))
        return product_code, product_unit

    def _read_line_unit_factors(
        self,
        lines: list[dict],
        unit_field: str,
        product_unit: dict[int, int | None],
    ) -> dict[int, object]:
        """One ``uom.uom`` read of the factors the lines' units need.

        Issued only when at least one line's unit differs from its
        product's unit, after the method's other reads: the sorted ids
        of those lines' units and their products' units, with the
        active clause, so a deactivated unit still answers its factor.
        Returns the unit id to raw ``factor`` map (empty when no read
        was issued).
        """
        unit_ids: set[int] = set()
        for line in lines:
            line_unit = _ref_id(line.get(unit_field))
            if line_unit is None:
                continue
            product_unit_id = product_unit.get(_ref_id(line.get("product_id")))
            if line_unit == product_unit_id:
                continue
            unit_ids.add(line_unit)
            if product_unit_id is not None:
                unit_ids.add(product_unit_id)
        if not unit_ids:
            return {}
        factors: dict[int, object] = {}
        for record in self.search_read(
            "uom.uom",
            [
                ["id", "in", sorted(unit_ids)],
                ["active", "in", [True, False]],
            ],
            ["factor"],
        ):
            record_id = _ref_id(record.get("id"))
            if record_id is not None:
                factors[record_id] = record.get("factor")
        return factors

    def read_products(self) -> list[dict]:
        """Read ``product.product`` records shaped for ``InventoryItemCreate``.

        One dict per product with a non-empty ``default_code``:
        ``item_code`` the ``default_code``, ``description`` the ``name``,
        ``uom`` the ``uom_id`` display name (the second element of Odoo's
        ``[id, name]`` pair, or null when absent), and ``source_system``
        ``"odoo"``. Products with no ``default_code`` are skipped (they
        have no stable key).
        """
        records = self.search_read(
            "product.product", [], ["default_code", "name", "uom_id"]
        )
        items: list[dict] = []
        for record in records:
            code = record.get("default_code")
            if not isinstance(code, str) or not code:
                continue
            name = record.get("name")
            items.append(
                {
                    "item_code": code,
                    "description": name if isinstance(name, str) else None,
                    "uom": _uom_display_name(record.get("uom_id")),
                    "source_system": "odoo",
                }
            )
        return items

    def _resolve_product_location_warehouse(
        self,
        product_ids: set[int],
        location_ids: set[int],
        order: str | None = None,
    ) -> tuple[dict[int, str], dict[int, int | None], dict[int, str]]:
        """Resolve product and location ids to item and site codes.

        One ``search_read`` over the given product ids (for
        ``default_code``) plus the location and warehouse resolution
        shared with :meth:`read_item_sites` (see
        :meth:`_resolve_location_warehouse`), each over an ``id in
        [...]`` domain and each omitted entirely when no id is seen.
        Returns ``(product_code, location_warehouse, warehouse_code)``;
        ``product_code`` and ``warehouse_code`` hold only ids with a
        non-empty code, and ``location_warehouse`` maps every resolved
        location id to its warehouse id or None.
        """
        product_code: dict[int, str] = {}
        if product_ids:
            for record in self.search_read(
                "product.product",
                [["id", "in", sorted(product_ids)]],
                ["default_code"],
                order=order,
            ):
                record_id = _ref_id(record.get("id"))
                code = record.get("default_code")
                if record_id is not None and isinstance(code, str) and code:
                    product_code[record_id] = code

        location_warehouse, warehouse_code = self._resolve_location_warehouse(
            location_ids, order=order
        )
        return product_code, location_warehouse, warehouse_code

    def _resolve_location_warehouse(
        self,
        location_ids: set[int],
        order: str | None = None,
    ) -> tuple[dict[int, int | None], dict[int, str]]:
        """Resolve location ids to warehouse ids and warehouse codes.

        One ``search_read`` each over the given location ids (for
        ``warehouse_id``) and the warehouses those locations resolve to
        (for ``code``), each over an ``id in [...]`` domain and each
        omitted entirely when no id is seen. Returns
        ``(location_warehouse, warehouse_code)``; ``warehouse_code``
        holds only ids with a non-empty code, and
        ``location_warehouse`` maps every resolved location id to its
        warehouse id or None. Shared by every reader that resolves a
        location to a site: :meth:`read_on_hand`,
        :meth:`read_item_sites`, :meth:`read_demand_events` and
        :meth:`read_lead_time_observations`.
        """
        location_warehouse: dict[int, int | None] = {}
        if location_ids:
            for record in self.search_read(
                "stock.location",
                [["id", "in", sorted(location_ids)]],
                ["warehouse_id"],
                order=order,
            ):
                record_id = _ref_id(record.get("id"))
                if record_id is not None:
                    location_warehouse[record_id] = _ref_id(record.get("warehouse_id"))

        warehouse_ids = {
            warehouse_id
            for warehouse_id in location_warehouse.values()
            if warehouse_id is not None
        }
        warehouse_code: dict[int, str] = {}
        if warehouse_ids:
            for record in self.search_read(
                "stock.warehouse",
                [["id", "in", sorted(warehouse_ids)]],
                ["code"],
                order=order,
            ):
                record_id = _ref_id(record.get("id"))
                code = record.get("code")
                if record_id is not None and isinstance(code, str) and code:
                    warehouse_code[record_id] = code

        return location_warehouse, warehouse_code

    def read_on_hand(self, as_of: str | None = None) -> list[dict]:
        """Read on-hand stock shaped for ``OnHandUpsert``.

        Reads the ``stock.quant`` rows whose location usage is
        ``internal`` (customer, supplier and transit stock excluded),
        resolves each quant's product to its ``default_code`` (the
        ``item_code``) and its location to a warehouse ``code`` (the
        ``site_code``), and sums the ``quantity`` of every quant into
        one row per ``(item_code, site_code)``. A negative sum is kept
        (ERPs report negative stock) and a sum of exactly zero is still
        emitted. A quant whose product has no ``default_code``, or whose
        location resolves to no warehouse or to a warehouse with no
        ``code``, is skipped, as is a quant whose quantity is missing,
        non-numeric, non-finite, or too large to represent as a finite
        float (including integers too large to convert to one); and a
        ``(item_code, site_code)`` pair whose summed quantity overflows
        the float range (a non-finite total) is dropped rather than
        emitted, so no row ever carries a non-finite
        ``quantity_on_hand``. Nothing here raises for such rows and no
        quant is folded into another key. The product, location and
        warehouse lookups are one ``search_read`` each, over the ids
        actually seen (an ``id in [...]`` domain), and are omitted
        entirely when no id is seen. Each row carries the given
        ``as_of`` string, or the current UTC time as
        ``YYYY-MM-DDTHH:MM:SSZ`` when ``as_of`` is None or not a
        non-blank string, and ``source_system`` ``"odoo"``. The method
        only reads through :meth:`search_read`; it writes nothing.
        """
        if not isinstance(as_of, str) or not as_of:
            as_of = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        quants = self.search_read(
            "stock.quant",
            [["location_id.usage", "=", "internal"]],
            ["product_id", "location_id", "quantity"],
        )

        product_ids: set[int] = set()
        location_ids: set[int] = set()
        for quant in quants:
            product_id = _ref_id(quant.get("product_id"))
            if product_id is not None:
                product_ids.add(product_id)
            location_id = _ref_id(quant.get("location_id"))
            if location_id is not None:
                location_ids.add(location_id)

        product_code, location_warehouse, warehouse_code = (
            self._resolve_product_location_warehouse(product_ids, location_ids)
        )

        totals: dict[tuple[str, str], float] = {}
        overflowed: set[tuple[str, str]] = set()
        for quant in quants:
            item_code = product_code.get(_ref_id(quant.get("product_id")))
            if item_code is None:
                continue
            warehouse_id = location_warehouse.get(_ref_id(quant.get("location_id")))
            if warehouse_id is None:
                continue
            site_code = warehouse_code.get(warehouse_id)
            if site_code is None:
                continue
            quantity = _finite_float(quant.get("quantity"))
            if quantity is None:
                continue
            key = (item_code, site_code)
            if key in overflowed:
                continue
            total = totals.get(key, 0.0) + quantity
            if not math.isfinite(total):
                overflowed.add(key)
                totals.pop(key, None)
                continue
            totals[key] = total

        return [
            {
                "item_code": item_code,
                "site_code": site_code,
                "quantity_on_hand": total,
                "as_of": as_of,
                "source_system": "odoo",
            }
            for (item_code, site_code), total in totals.items()
        ]

    def read_item_sites(self) -> list[dict]:
        """Read internal-stock item/site pairs shaped for ``ItemSiteUpsert``.

        Reads the ``stock.quant`` rows whose location usage is
        ``internal`` (customer, supplier and transit stock excluded),
        and emits one row per distinct ``(item_code, site_code)`` pair
        where a product holds internal stock: ``item_code`` the
        product's ``default_code`` and ``site_code`` the location's
        warehouse ``code``. A quant whose product has no
        ``default_code``, or whose location resolves to no warehouse or
        to a warehouse with no ``code``, contributes no pair, and the
        same pair reached from several internal locations of one
        warehouse is emitted exactly once. Each row carries
        ``source_system`` ``"odoo"``, ``unit_cost`` the product's
        ``standard_price`` as a float (or null when it is not a finite
        number), and ``master_lead_time_days`` the ``delay`` (as a
        float) of the product's primary vendor — the
        ``product.supplierinfo`` named by the first id in the product's
        ``seller_ids``, or null when the product has no ``seller_ids``
        or that supplierinfo has no numeric ``delay``. A product's cost
        and lead time are the same across its sites, so the per-pair
        rows of one product repeat them; ``safety_stock`` stays null on
        every row. ``reorder_point``, ``max_qty`` and ``order_qty``
        come from the pair's reorder rules, read in one ``search_read``
        on ``stock.warehouse.orderpoint`` with the domain
        ``[["active", "=", True], ["product_id", "in", ...]]`` over the
        product ids actually seen (ascending) and the fields
        ``product_id``, ``warehouse_id``, ``product_min_qty`` and
        ``product_max_qty``, omitted entirely when no product id is
        seen: a rule belongs to the (product, warehouse) pair whose
        product id is the rule's ``product_id`` id and whose warehouse
        id is the rule's ``warehouse_id`` id; when exactly one rule
        matches the pair and both its ``product_min_qty`` and
        ``product_max_qty`` are finite numbers, the row carries
        ``reorder_point`` the ``product_min_qty`` and ``max_qty`` the
        ``product_max_qty``, both as floats, and ``order_qty`` the
        difference ``max_qty - reorder_point`` when that is greater
        than 0 and finite (a difference that overflows the float
        range leaves ``order_qty`` null, so no row ever carries a
        non-finite value), and null otherwise, while a pair with no
        rule, a pair with more than one rule (ambiguous), and a rule
        whose min or max is not a finite number all leave
        ``reorder_point``, ``max_qty`` and ``order_qty`` null. The
        product, location,
        warehouse, supplierinfo and orderpoint lookups are one
        ``search_read`` each over the ids actually seen (an ``id in
        [...]`` domain), the location and warehouse
        resolution is shared with :meth:`read_on_hand`,
        :meth:`read_demand_events` and
        :meth:`read_lead_time_observations`, and each lookup is omitted
        entirely when no id is seen. The method only reads through
        :meth:`search_read`; it writes nothing.
        """
        quants = self.search_read(
            "stock.quant",
            [["location_id.usage", "=", "internal"]],
            ["product_id", "location_id"],
        )

        product_ids: set[int] = set()
        location_ids: set[int] = set()
        for quant in quants:
            product_id = _ref_id(quant.get("product_id"))
            if product_id is not None:
                product_ids.add(product_id)
            location_id = _ref_id(quant.get("location_id"))
            if location_id is not None:
                location_ids.add(location_id)

        products: dict[int, dict] = {}
        if product_ids:
            for record in self.search_read(
                "product.product",
                [["id", "in", sorted(product_ids)]],
                ["default_code", "standard_price", "seller_ids"],
            ):
                record_id = _ref_id(record.get("id"))
                if record_id is not None:
                    products[record_id] = record

        location_warehouse, warehouse_code = self._resolve_location_warehouse(
            location_ids
        )

        orderpoint_rules: dict[tuple[int, int], list[dict]] = {}
        if product_ids:
            for record in self.search_read(
                "stock.warehouse.orderpoint",
                [
                    ["active", "=", True],
                    ["product_id", "in", sorted(product_ids)],
                ],
                [
                    "product_id",
                    "warehouse_id",
                    "product_min_qty",
                    "product_max_qty",
                ],
            ):
                rule_product_id = _ref_id(record.get("product_id"))
                rule_warehouse_id = _ref_id(record.get("warehouse_id"))
                if rule_product_id is None or rule_warehouse_id is None:
                    continue
                orderpoint_rules.setdefault(
                    (rule_product_id, rule_warehouse_id), []
                ).append(record)

        primary_seller: dict[int, int | None] = {}
        seller_ids_seen: set[int] = set()
        for product_id, record in products.items():
            seller_ids = record.get("seller_ids")
            seller_id: int | None = None
            if isinstance(seller_ids, (list, tuple)) and seller_ids:
                seller_id = _ref_id(seller_ids[0])
            primary_seller[product_id] = seller_id
            if seller_id is not None:
                seller_ids_seen.add(seller_id)

        supplier_delay: dict[int, float | None] = {}
        if seller_ids_seen:
            for record in self.search_read(
                "product.supplierinfo",
                [["id", "in", sorted(seller_ids_seen)]],
                ["delay"],
            ):
                record_id = _ref_id(record.get("id"))
                if record_id is not None:
                    supplier_delay[record_id] = _finite_float(record.get("delay"))

        rows: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for quant in quants:
            record = products.get(_ref_id(quant.get("product_id")))
            if record is None:
                continue
            item_code = record.get("default_code")
            if not isinstance(item_code, str) or not item_code:
                continue
            warehouse_id = location_warehouse.get(_ref_id(quant.get("location_id")))
            if warehouse_id is None:
                continue
            site_code = warehouse_code.get(warehouse_id)
            if site_code is None:
                continue
            key = (item_code, site_code)
            if key in seen:
                continue
            seen.add(key)
            lead_time: float | None = None
            seller_id = primary_seller.get(_ref_id(quant.get("product_id")))
            if seller_id is not None:
                lead_time = supplier_delay.get(seller_id)
            rules = orderpoint_rules.get(
                (_ref_id(quant.get("product_id")), warehouse_id), []
            )
            reorder_point: float | None = None
            max_qty: float | None = None
            order_qty: float | None = None
            if len(rules) == 1:
                min_qty = _finite_float(rules[0].get("product_min_qty"))
                max_rule_qty = _finite_float(rules[0].get("product_max_qty"))
                if min_qty is not None and max_rule_qty is not None:
                    reorder_point = min_qty
                    max_qty = max_rule_qty
                    order_qty_value = max_rule_qty - min_qty
                    if order_qty_value > 0 and math.isfinite(order_qty_value):
                        order_qty = order_qty_value
            rows.append(
                {
                    "item_code": item_code,
                    "site_code": site_code,
                    "unit_cost": _finite_float(record.get("standard_price")),
                    "reorder_point": reorder_point,
                    "safety_stock": None,
                    "max_qty": max_qty,
                    "order_qty": order_qty,
                    "master_lead_time_days": lead_time,
                    "source_system": "odoo",
                }
            )
        return rows

    def read_demand_events(self) -> list[dict]:
        """Read completed customer shipments shaped for ``DemandEventUpsert``.

        Reads the ``stock.move`` rows that are ``state = done`` and whose
        destination location usage is ``customer`` (completed shipments
        to a customer), ordered ``id asc`` for stable offset paging, and
        maps each kept move onto one row:

        - ``business_key``: ``odoo:{db}:stock.move:{move_id}``, where
          ``{db}`` is the configured Odoo database identifier and
          ``{move_id}`` the move's id — namespaced by source system and
          entity type so it cannot collide with a key from another
          system, database, or entity;
        - ``item_code``: the move's product ``default_code``;
        - ``site_code``: the move's source location's warehouse ``code``;
        - ``quantity``: the move's ``quantity``, converted to the
          product's base unit (quantity × factor of the move's unit ÷
          factor of the product's unit; a move with no unit, or whose
          unit is the product's unit, is taken as is), as a positive
          float (its absolute value);
        - ``occurred_on``: the date part of the move's ``date`` as
          ``YYYY-MM-DD``;
        - ``event_type``: ``"shipment"``;
        - ``source_system``: ``"odoo"``.

        A move is skipped, never an error, when it has no id, its
        product has no ``default_code``, its source location resolves to
        no warehouse or to a warehouse with no ``code``, its
        ``quantity`` is missing, zero, non-numeric, non-finite, or too
        large to represent as a finite float, it is unusable — its unit
        differs from the product's unit while the product has no unit,
        or either unit's factor is missing, zero, negative, or
        non-finite — or its ``date`` is missing or not a parseable
        date. Each kept move is one row; moves are never summed. The
        product, location and warehouse lookups are one ``search_read``
        each over the ids actually seen (an ``id in [...]`` domain),
        each omitted entirely when no id is seen, and the product
        lookup asks ``default_code`` and ``uom_id``; when at least one
        move's unit differs from its product's unit, one more
        ``uom.uom`` lookup (``factor``, with the active clause) is made
        after the other ones, over the sorted ids of those moves' units
        and their products' units. The method only reads through
        :meth:`search_read`; it writes nothing.
        """
        _origin, db, _login, _api_key, _ca_bundle, _timeout = self._load_config()

        moves = self.search_read(
            "stock.move",
            [["state", "=", "done"], ["location_dest_id.usage", "=", "customer"]],
            ["product_id", "location_id", "quantity", "date", "uom_id"],
            order="id asc",
        )

        product_ids: set[int] = set()
        location_ids: set[int] = set()
        for move in moves:
            product_id = _ref_id(move.get("product_id"))
            if product_id is not None:
                product_ids.add(product_id)
            location_id = _ref_id(move.get("location_id"))
            if location_id is not None:
                location_ids.add(location_id)

        product_code, product_unit = self._read_product_code_and_unit(
            product_ids, order="id asc"
        )
        location_warehouse, warehouse_code = self._resolve_location_warehouse(
            location_ids, order="id asc"
        )
        factors = self._read_line_unit_factors(moves, "uom_id", product_unit)

        rows: list[dict] = []
        for move in moves:
            move_id = _ref_id(move.get("id"))
            if move_id is None:
                continue
            product_id = _ref_id(move.get("product_id"))
            item_code = product_code.get(product_id)
            if item_code is None:
                continue
            warehouse_id = location_warehouse.get(_ref_id(move.get("location_id")))
            if warehouse_id is None:
                continue
            site_code = warehouse_code.get(warehouse_id)
            if site_code is None:
                continue
            quantity = _finite_float(move.get("quantity"))
            if quantity is None or quantity == 0:
                continue
            quantity = _to_base_unit(
                quantity,
                _ref_id(move.get("uom_id")),
                product_unit.get(product_id),
                factors,
            )
            if quantity is None:
                continue
            occurred_on = _date_part(move.get("date"))
            if occurred_on is None:
                continue
            rows.append(
                {
                    "business_key": f"odoo:{db}:stock.move:{move_id}",
                    "item_code": item_code,
                    "site_code": site_code,
                    "quantity": abs(quantity),
                    "occurred_on": occurred_on,
                    "event_type": "shipment",
                    "source_system": "odoo",
                }
            )
        return rows

    def read_lead_time_observations(self) -> list[dict]:
        """Read completed supplier receipts shaped for ``LeadTimeObservationUpsert``.

        Reads the ``stock.move`` rows that are ``state = done`` and whose
        source location usage is ``supplier`` (completed receipts
        from a supplier), ordered ``id asc`` for stable offset paging,
        and maps each kept move onto one row:

        - ``business_key``: ``odoo:{db}:stock.move:{move_id}``, where
          ``{db}`` is the configured Odoo database identifier and
          ``{move_id}`` the move's id — namespaced by source system and
          entity type so it cannot collide with a key from another
          system, database, or entity;
        - ``item_code``: the move's product ``default_code``;
        - ``supplier_code``: the PO vendor's ``ref`` when it is a
          non-empty string, else the vendor's ``name``;
        - ``site_code``: the move's destination location's warehouse
          ``code``, or null when the location does not resolve to a
          coded warehouse;
        - ``ordered_on``: the date part of the PO's ``date_order`` as
          ``YYYY-MM-DD``;
        - ``received_on``: the date part of the move's ``date`` as
          ``YYYY-MM-DD``;
        - ``source_system``: ``"odoo"``.

        ``lead_time_days`` is never emitted: it is a generated column
        the database computes as ``received_on - ordered_on``.

        A move is skipped, never an error, when it has no id, no
        ``purchase_line_id`` (or its chain does not resolve to a
        ``purchase.order``), the PO has no ``date_order``, the PO vendor
        has no usable ``supplier_code`` (no non-empty ``ref`` and no
        non-empty ``name``), its product has no ``default_code``, its
        ``date`` is missing or not a parseable date, or its
        ``received_on`` precedes its ``ordered_on`` (a
        ``receipt_before_order`` skip, matching the Acumatica sync).
        Each kept move is one row, so several receipts of the same
        purchase line keep their own keys and are never folded
        together. The purchase line, order, partner, product, location
        and warehouse lookups are one ``search_read`` each over the ids
        actually seen (an ``id in [...]`` domain), each omitted entirely
        when no id is seen; the product, location and warehouse
        resolution is shared with :meth:`read_on_hand` and
        :meth:`read_demand_events`. The method only reads through
        :meth:`search_read`; it writes nothing.
        """
        _origin, db, _login, _api_key, _ca_bundle, _timeout = self._load_config()

        moves = self.search_read(
            "stock.move",
            [["state", "=", "done"], ["location_id.usage", "=", "supplier"]],
            ["product_id", "location_dest_id", "date", "purchase_line_id"],
            order="id asc",
        )

        product_ids: set[int] = set()
        location_ids: set[int] = set()
        line_ids: set[int] = set()
        for move in moves:
            product_id = _ref_id(move.get("product_id"))
            if product_id is not None:
                product_ids.add(product_id)
            location_id = _ref_id(move.get("location_dest_id"))
            if location_id is not None:
                location_ids.add(location_id)
            line_id = _ref_id(move.get("purchase_line_id"))
            if line_id is not None:
                line_ids.add(line_id)

        line_order: dict[int, int | None] = {}
        if line_ids:
            for record in self.search_read(
                "purchase.order.line",
                [["id", "in", sorted(line_ids)]],
                ["order_id"],
                order="id asc",
            ):
                record_id = _ref_id(record.get("id"))
                if record_id is not None:
                    line_order[record_id] = _ref_id(record.get("order_id"))

        order_ids = {
            order_id for order_id in line_order.values() if order_id is not None
        }
        order_date: dict[int, str | None] = {}
        order_partner: dict[int, int | None] = {}
        if order_ids:
            for record in self.search_read(
                "purchase.order",
                [["id", "in", sorted(order_ids)]],
                ["date_order", "partner_id"],
                order="id asc",
            ):
                record_id = _ref_id(record.get("id"))
                if record_id is not None:
                    order_date[record_id] = _date_part(record.get("date_order"))
                    order_partner[record_id] = _ref_id(record.get("partner_id"))

        partner_ids = {
            partner_id
            for partner_id in order_partner.values()
            if partner_id is not None
        }
        partner_code: dict[int, str] = {}
        if partner_ids:
            for record in self.search_read(
                "res.partner",
                [["id", "in", sorted(partner_ids)]],
                ["ref", "name"],
                order="id asc",
            ):
                record_id = _ref_id(record.get("id"))
                code = _supplier_code(record)
                if record_id is not None and code is not None:
                    partner_code[record_id] = code

        product_code, location_warehouse, warehouse_code = (
            self._resolve_product_location_warehouse(
                product_ids, location_ids, order="id asc"
            )
        )

        rows: list[dict] = []
        for move in moves:
            move_id = _ref_id(move.get("id"))
            if move_id is None:
                continue
            line_id = _ref_id(move.get("purchase_line_id"))
            if line_id is None:
                continue
            order_id = line_order.get(line_id)
            if order_id is None:
                continue
            ordered_on = order_date.get(order_id)
            if ordered_on is None:
                continue
            supplier_code = partner_code.get(order_partner.get(order_id))
            if supplier_code is None:
                continue
            item_code = product_code.get(_ref_id(move.get("product_id")))
            if item_code is None:
                continue
            received_on = _date_part(move.get("date"))
            if received_on is None:
                continue
            if received_on < ordered_on:
                continue
            warehouse_id = location_warehouse.get(_ref_id(move.get("location_dest_id")))
            site_code = (
                warehouse_code.get(warehouse_id) if warehouse_id is not None else None
            )
            rows.append(
                {
                    "business_key": f"odoo:{db}:stock.move:{move_id}",
                    "supplier_code": supplier_code,
                    "item_code": item_code,
                    "site_code": site_code,
                    "ordered_on": ordered_on,
                    "received_on": received_on,
                    "source_system": "odoo",
                }
            )
        return rows

    def read_open_purchase_lines(self, as_of: str | None = None) -> list[dict]:
        """Read open confirmed purchase lines shaped for ``OpenPurchaseLineUpsert``.

        Reads the ``purchase.order.line`` rows whose order state is
        ``purchase`` or ``done`` (confirmed orders), and maps each line
        still open — ``product_qty`` greater than ``qty_received``,
        decided in Python when computing ``open_units`` because an Odoo
        domain cannot compare two fields — onto one row:

        - ``business_key``: ``odoo:{db}:purchase.order.line:{line_id}``,
          where ``{db}`` is the configured Odoo database identifier and
          ``{line_id}`` the line's id — namespaced by source system,
          database, and entity type so it cannot collide with a key
          from another system, database, or entity;
        - ``item_code``: the line's product ``default_code``;
        - ``site_code``: the receiving warehouse ``code`` — the
          warehouse the PO's picking type belongs to;
        - ``supplier_code``: the PO vendor's ``ref`` when it is a
          non-empty string, else the vendor's ``name``, or null when
          the vendor has neither;
        - ``order_type``: ``"purchase"``;
        - ``open_units``: ``product_qty`` minus ``qty_received`` as a
          float, both quantities first converted to the product's base
          unit (quantity × factor of the line's unit ÷ factor of the
          product's unit; a line with no unit, or whose unit is the
          product's unit, is taken as is);
        - ``promised_on``: the date part of the line's ``date_planned``
          as ``YYYY-MM-DD``, or null;
        - ``ordered_on``: the date part of the PO's ``date_order`` as
          ``YYYY-MM-DD``, or null;
        - ``as_of``: the given ``as_of`` string, or the current UTC
          time as ``YYYY-MM-DDTHH:MM:SSZ`` when ``as_of`` is None or
          not a non-blank string;
        - ``source_system``: ``"odoo"``.

        A line is skipped, never an error, when it has no id, its
        product has no ``default_code``, its order does not resolve to
        a warehouse with a ``code`` (no picking type, no warehouse, or
        the warehouse has no code), it is unusable — its unit differs
        from the product's unit while the product has no unit, or
        either unit's factor is missing, zero, negative, or non-
        finite — or its ``open_units`` is not a positive finite
        number. A null or missing ``qty_received`` is treated as
        ``0.0``, so ``open_units`` equals ``product_qty`` and the line
        is kept, not skipped. Each kept line is one row; lines are
        never folded together. The purchase order, picking type,
        warehouse, vendor and product lookups are one ``search_read``
        each over the ids actually seen (an ``id in [...]`` domain),
        each omitted entirely when no id is seen, and the product
        lookup asks ``default_code`` and ``uom_id``; when at least one
        line's unit differs from its product's unit, one more
        ``uom.uom`` lookup (``factor``, with the active clause) is made
        after the other ones, over the sorted ids of those lines' units
        and their products' units; the warehouse and vendor resolution
        follows the other readers (a warehouse ``code`` lookup, and
        the vendor's ``ref``-else-``name``). The method only reads
        through :meth:`search_read`; it writes nothing.
        """
        if not isinstance(as_of, str) or not as_of:
            as_of = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        _origin, db, _login, _api_key, _ca_bundle, _timeout = self._load_config()

        lines = self.search_read(
            "purchase.order.line",
            [["order_id.state", "in", ["purchase", "done"]]],
            [
                "product_id",
                "product_qty",
                "qty_received",
                "date_planned",
                "order_id",
                "uom_id",
            ],
        )

        product_ids: set[int] = set()
        order_ids: set[int] = set()
        for line in lines:
            product_id = _ref_id(line.get("product_id"))
            if product_id is not None:
                product_ids.add(product_id)
            order_id = _ref_id(line.get("order_id"))
            if order_id is not None:
                order_ids.add(order_id)

        order_date: dict[int, str | None] = {}
        order_partner: dict[int, int | None] = {}
        order_picking_type: dict[int, int | None] = {}
        if order_ids:
            for record in self.search_read(
                "purchase.order",
                [["id", "in", sorted(order_ids)]],
                ["date_order", "partner_id", "picking_type_id"],
            ):
                record_id = _ref_id(record.get("id"))
                if record_id is not None:
                    order_date[record_id] = _date_part(record.get("date_order"))
                    order_partner[record_id] = _ref_id(record.get("partner_id"))
                    order_picking_type[record_id] = _ref_id(
                        record.get("picking_type_id")
                    )

        picking_type_ids = {
            picking_type_id
            for picking_type_id in order_picking_type.values()
            if picking_type_id is not None
        }
        picking_type_warehouse: dict[int, int | None] = {}
        if picking_type_ids:
            for record in self.search_read(
                "stock.picking.type",
                [["id", "in", sorted(picking_type_ids)]],
                ["warehouse_id"],
            ):
                record_id = _ref_id(record.get("id"))
                if record_id is not None:
                    picking_type_warehouse[record_id] = _ref_id(
                        record.get("warehouse_id")
                    )

        warehouse_ids = {
            warehouse_id
            for warehouse_id in picking_type_warehouse.values()
            if warehouse_id is not None
        }
        warehouse_code: dict[int, str] = {}
        if warehouse_ids:
            for record in self.search_read(
                "stock.warehouse",
                [["id", "in", sorted(warehouse_ids)]],
                ["code"],
            ):
                record_id = _ref_id(record.get("id"))
                code = record.get("code")
                if record_id is not None and isinstance(code, str) and code:
                    warehouse_code[record_id] = code

        partner_ids = {
            partner_id
            for partner_id in order_partner.values()
            if partner_id is not None
        }
        partner_code: dict[int, str] = {}
        if partner_ids:
            for record in self.search_read(
                "res.partner",
                [["id", "in", sorted(partner_ids)]],
                ["ref", "name"],
            ):
                record_id = _ref_id(record.get("id"))
                code = _supplier_code(record)
                if record_id is not None and code is not None:
                    partner_code[record_id] = code

        product_code, product_unit = self._read_product_code_and_unit(product_ids)
        factors = self._read_line_unit_factors(lines, "uom_id", product_unit)

        rows: list[dict] = []
        for line in lines:
            line_id = _ref_id(line.get("id"))
            if line_id is None:
                continue
            product_id = _ref_id(line.get("product_id"))
            item_code = product_code.get(product_id)
            if item_code is None:
                continue
            order_id = _ref_id(line.get("order_id"))
            if order_id is None:
                continue
            site_code = None
            picking_type_id = order_picking_type.get(order_id)
            if picking_type_id is not None:
                warehouse_id = picking_type_warehouse.get(picking_type_id)
                if warehouse_id is not None:
                    site_code = warehouse_code.get(warehouse_id)
            if site_code is None:
                continue
            product_qty = _finite_float(line.get("product_qty"))
            if product_qty is None:
                continue
            qty_received = _finite_float(line.get("qty_received"))
            if qty_received is None:
                qty_received = 0.0
            line_unit = _ref_id(line.get("uom_id"))
            product_qty = _to_base_unit(
                product_qty, line_unit, product_unit.get(product_id), factors
            )
            if product_qty is None:
                continue
            qty_received = _to_base_unit(
                qty_received, line_unit, product_unit.get(product_id), factors
            )
            if qty_received is None:
                continue
            open_units = product_qty - qty_received
            if not math.isfinite(open_units) or open_units <= 0:
                continue
            rows.append(
                {
                    "business_key": f"odoo:{db}:purchase.order.line:{line_id}",
                    "item_code": item_code,
                    "site_code": site_code,
                    "supplier_code": partner_code.get(order_partner.get(order_id)),
                    "order_type": "purchase",
                    "open_units": open_units,
                    "promised_on": _date_part(line.get("date_planned")),
                    "ordered_on": order_date.get(order_id),
                    "as_of": as_of,
                    "source_system": "odoo",
                }
            )
        return rows

    def read_open_sales_lines(self, as_of: str | None = None) -> list[dict]:
        """Read sale-order lines shaped for the ``open_sales_lines`` upsert.

        Reads the ``sale.order.line`` rows whose parent order state is
        ``sale``, ``done`` or ``cancel`` — every state a confirmed
        order can hold or reach, so a line that leaves the
        confirmed/open state is still re-read and its row zeroed, not
        left stale (the read model is upsert-only) — and maps each
        line onto one row:

        - ``business_key``: ``odoo:{db}:sale.order.line:{line_id}``,
          where ``{db}`` is the configured Odoo database identifier
          and ``{line_id}`` the line's id — namespaced by source
          system, database, and entity type so it cannot collide with
          a key from another system, database, or entity;
        - ``item_code``: the line's product ``default_code``;
        - ``site_code``: the line's warehouse ``code``;
        - ``order_type``: ``"SO"``;
        - ``open_units``: ``product_uom_qty`` minus ``qty_delivered``
          as a float, floored at 0, when the parent order's state is
          ``sale`` — decided in Python when computing ``open_units``
          because an Odoo domain cannot compare two fields, both
          quantities first converted to the product's base unit
          (quantity × factor of the line's unit ÷ factor of the
          product's unit; a line with no unit, or whose unit is the
          product's unit, is taken as is) — and 0 when the state is
          ``done`` or ``cancel``, so a line whose parent order closed
          is zeroed on the next sync;
        - ``ship_on``: the date part of the order's
          ``commitment_date`` as ``YYYY-MM-DD``, else of its
          ``expected_date``, else null;
        - ``as_of``: the given ``as_of`` string, or the current UTC
          time as ``YYYY-MM-DDTHH:MM:SSZ`` when ``as_of`` is None or
          not a non-blank string;
        - ``source_system``: ``"odoo"``.

        A line is skipped, never an error, when it has no id, its
        order does not resolve to an id, its product has no
        ``default_code``, or its warehouse has no ``code``; a line
        that is unusable — its unit differs from the product's unit
        while the product has no unit, or either unit's factor is
        missing, zero, negative, or non-finite — is skipped only when
        its order's state is ``sale``: a line of a closed order still
        answers a row with 0 open units. A fully delivered line is
        kept with ``open_units`` 0, because the read model is
        upsert-only and a closed line must be zeroed. A null or
        missing ``product_uom_qty`` or ``qty_delivered`` is treated as
        ``0.0``. The product, warehouse and order lookups are one
        ``search_read`` each over the ids actually seen (an ``id in
        [...]`` domain), each omitted entirely when no id is seen, and
        the product lookup asks ``default_code`` and ``uom_id``; when
        at least one line's unit differs from its product's unit, one
        more ``uom.uom`` lookup (``factor``, with the active clause)
        is made after the other ones, over the sorted ids of those
        lines' units and their products' units. The method only reads
        through :meth:`search_read`; it writes nothing.
        """
        if not isinstance(as_of, str) or not as_of:
            as_of = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        _origin, db, _login, _api_key, _ca_bundle, _timeout = self._load_config()

        lines = self.search_read(
            "sale.order.line",
            [
                ["order_id.state", "in", ["sale", "done", "cancel"]],
                ["display_type", "=", False],
                ["is_service", "=", False],
            ],
            [
                "order_id",
                "product_id",
                "product_uom_qty",
                "qty_delivered",
                "warehouse_id",
                "product_uom_id",
            ],
        )

        product_ids: set[int] = set()
        warehouse_ids: set[int] = set()
        order_ids: set[int] = set()
        for line in lines:
            product_id = _ref_id(line.get("product_id"))
            if product_id is not None:
                product_ids.add(product_id)
            warehouse_id = _ref_id(line.get("warehouse_id"))
            if warehouse_id is not None:
                warehouse_ids.add(warehouse_id)
            order_id = _ref_id(line.get("order_id"))
            if order_id is not None:
                order_ids.add(order_id)

        product_code, product_unit = self._read_product_code_and_unit(product_ids)

        warehouse_code: dict[int, str] = {}
        if warehouse_ids:
            for record in self.search_read(
                "stock.warehouse",
                [["id", "in", sorted(warehouse_ids)]],
                ["code"],
            ):
                record_id = _ref_id(record.get("id"))
                code = record.get("code")
                if record_id is not None and isinstance(code, str) and code:
                    warehouse_code[record_id] = code

        order_state: dict[int, str | None] = {}
        order_commitment: dict[int, str | None] = {}
        order_expected: dict[int, str | None] = {}
        if order_ids:
            for record in self.search_read(
                "sale.order",
                [["id", "in", sorted(order_ids)]],
                ["state", "commitment_date", "expected_date"],
            ):
                record_id = _ref_id(record.get("id"))
                if record_id is None:
                    continue
                state = record.get("state")
                order_state[record_id] = state if isinstance(state, str) else None
                order_commitment[record_id] = _date_part(record.get("commitment_date"))
                order_expected[record_id] = _date_part(record.get("expected_date"))

        factors = self._read_line_unit_factors(lines, "product_uom_id", product_unit)

        rows: list[dict] = []
        for line in lines:
            line_id = _ref_id(line.get("id"))
            if line_id is None:
                continue
            product_id = _ref_id(line.get("product_id"))
            item_code = product_code.get(product_id)
            if item_code is None:
                continue
            site_code = warehouse_code.get(_ref_id(line.get("warehouse_id")))
            if site_code is None:
                continue
            order_id = _ref_id(line.get("order_id"))
            if order_id is None:
                continue
            if order_state.get(order_id) == "sale":
                product_uom_qty = _finite_float(line.get("product_uom_qty"))
                if product_uom_qty is None:
                    product_uom_qty = 0.0
                qty_delivered = _finite_float(line.get("qty_delivered"))
                if qty_delivered is None:
                    qty_delivered = 0.0
                line_unit = _ref_id(line.get("product_uom_id"))
                product_uom_qty = _to_base_unit(
                    product_uom_qty,
                    line_unit,
                    product_unit.get(product_id),
                    factors,
                )
                if product_uom_qty is None:
                    continue
                qty_delivered = _to_base_unit(
                    qty_delivered, line_unit, product_unit.get(product_id), factors
                )
                if qty_delivered is None:
                    continue
                open_units = max(0.0, product_uom_qty - qty_delivered)
            else:
                # "done" or "cancel" (or an unresolvable state): the
                # parent order is closed, so the line's row must be
                # zeroed, not skipped.
                open_units = 0.0
            ship_on = order_commitment.get(order_id)
            if ship_on is None:
                ship_on = order_expected.get(order_id)
            rows.append(
                {
                    "business_key": f"odoo:{db}:sale.order.line:{line_id}",
                    "item_code": item_code,
                    "site_code": site_code,
                    "order_type": "SO",
                    "open_units": open_units,
                    "ship_on": ship_on,
                    "as_of": as_of,
                    "source_system": "odoo",
                }
            )
        return rows

    def read_supplier_terms(self) -> list[dict]:
        """Read ``product.supplierinfo`` rows shaped for the ``supplier_terms`` upsert.

        Reads every supplierinfo line in one ``search_read`` and maps
        each resolvable line onto one row:

        - ``business_key``: ``odoo:{db}:product.supplierinfo:{id}``,
          where ``{db}`` is the configured Odoo database identifier
          and ``{id}`` the line's id — namespaced by source system,
          database, and entity type so it cannot collide with a key
          from another system, database, or entity;
        - ``source_system``: ``"odoo"``;
        - ``item_code``: the product's ``default_code``, resolved via
          the line's ``product_id`` when it is set, otherwise via the
          line's ``product_tmpl_id`` when that template has exactly
          one product variant with a non-empty ``default_code``;
        - ``supplier_code``: the line's partner's ``ref`` when it is a
          non-empty string, else the partner's ``name`` — the same
          code :meth:`read_open_purchase_lines` emits for a vendor —
          or null when the partner has neither;
        - ``min_order_qty``: ``min_qty`` as a float, with ``0`` read
          as null;
        - ``max_order_qty``: null — Odoo's supplierinfo carries no
          maximum order quantity;
        - ``lot_size``: null — Odoo's supplierinfo carries no lot
          size;
        - ``lead_time_days``: ``delay`` as a float, or null when
          missing;
        - ``is_default``: true only for the line with the lowest
          ``sequence`` (then lowest id) among the product's lines.

        A line is skipped, never an error, when it has no id or its
        item cannot be resolved (a ``product_id`` whose product has no
        ``default_code``, or a ``product_tmpl_id`` whose variants do
        not give exactly one non-empty ``default_code``); a partner
        with neither ``ref`` nor ``name`` contributes a null
        ``supplier_code``, not a skip. The product, template-variant
        and partner lookups are one ``search_read`` each over the ids
        actually seen (an ``id in [...]`` or ``product_tmpl_id in
        [...]`` domain), each omitted entirely when no id is seen.
        The method only reads through :meth:`search_read`; it writes
        nothing.
        """
        _origin, db, _login, _api_key, _ca_bundle, _timeout = self._load_config()

        lines = self.search_read(
            "product.supplierinfo",
            [],
            [
                "id",
                "product_id",
                "product_tmpl_id",
                "partner_id",
                "min_qty",
                "delay",
                "sequence",
            ],
        )

        product_ids: set[int] = set()
        tmpl_ids: set[int] = set()
        partner_ids: set[int] = set()
        for line in lines:
            product_id = _ref_id(line.get("product_id"))
            if product_id is not None:
                product_ids.add(product_id)
            else:
                tmpl_id = _ref_id(line.get("product_tmpl_id"))
                if tmpl_id is not None:
                    tmpl_ids.add(tmpl_id)
            partner_id = _ref_id(line.get("partner_id"))
            if partner_id is not None:
                partner_ids.add(partner_id)

        product_code: dict[int, str] = {}
        if product_ids:
            for record in self.search_read(
                "product.product",
                [["id", "in", sorted(product_ids)]],
                ["default_code"],
            ):
                record_id = _ref_id(record.get("id"))
                code = record.get("default_code")
                if record_id is not None and isinstance(code, str) and code:
                    product_code[record_id] = code

        tmpl_code: dict[int, str] = {}
        if tmpl_ids:
            tmpl_variants: dict[int, dict[int, str]] = {}
            for record in self.search_read(
                "product.product",
                [["product_tmpl_id", "in", sorted(tmpl_ids)]],
                ["product_tmpl_id", "default_code"],
            ):
                tmpl_id = _ref_id(record.get("product_tmpl_id"))
                variant_id = _ref_id(record.get("id"))
                code = record.get("default_code")
                if tmpl_id is None or variant_id is None:
                    continue
                if isinstance(code, str) and code:
                    tmpl_variants.setdefault(tmpl_id, {})[variant_id] = code
            for tmpl_id, variants in tmpl_variants.items():
                if len(variants) == 1:
                    tmpl_code[tmpl_id] = next(iter(variants.values()))

        partner_code: dict[int, str] = {}
        if partner_ids:
            for record in self.search_read(
                "res.partner",
                [["id", "in", sorted(partner_ids)]],
                ["ref", "name"],
            ):
                record_id = _ref_id(record.get("id"))
                code = _supplier_code(record)
                if record_id is not None and code is not None:
                    partner_code[record_id] = code

        candidates: list[tuple[dict, int, str]] = []
        for line in lines:
            line_id = _ref_id(line.get("id"))
            if line_id is None:
                continue
            product_id = _ref_id(line.get("product_id"))
            item_code = product_code.get(product_id) if product_id is not None else None
            if item_code is None and product_id is None:
                tmpl_id = _ref_id(line.get("product_tmpl_id"))
                if tmpl_id is not None:
                    item_code = tmpl_code.get(tmpl_id)
            if item_code is None:
                continue
            candidates.append((line, line_id, item_code))

        default_rank: dict[str, tuple[float, int]] = {}
        default_line: dict[str, int] = {}
        for line, line_id, item_code in candidates:
            sequence = _finite_float(line.get("sequence"))
            rank = (math.inf if sequence is None else sequence, line_id)
            if item_code not in default_rank or rank < default_rank[item_code]:
                default_rank[item_code] = rank
                default_line[item_code] = line_id

        rows: list[dict] = []
        for line, line_id, item_code in candidates:
            min_qty = _finite_float(line.get("min_qty"))
            if min_qty == 0:
                min_qty = None
            rows.append(
                {
                    "business_key": f"odoo:{db}:product.supplierinfo:{line_id}",
                    "source_system": "odoo",
                    "item_code": item_code,
                    "supplier_code": partner_code.get(_ref_id(line.get("partner_id"))),
                    "min_order_qty": min_qty,
                    "max_order_qty": None,
                    "lot_size": None,
                    "lead_time_days": _finite_float(line.get("delay")),
                    "is_default": default_line[item_code] == line_id,
                }
            )
        return rows

    def read_company_currencies(self) -> list[str]:
        """Answer the currency codes the companies report in.

        Reads every ``res.company`` row in one ``search_read`` with the
        ``currency_id`` field and returns the distinct currency codes
        of the companies, normalized to uppercase ISO-4217 (so
        ``"usd"`` and ``"USD"`` answer as one code) and sorted: the
        code is the company currency's display name, the second
        element of Odoo's ``[id, name]`` pair. A company with no
        currency (a falsy ``currency_id``) or with a non-string code
        contributes nothing — it is ignored, never an error. The
        method only reads through :meth:`search_read`; it writes
        nothing.
        """
        records = self.search_read("res.company", [], ["currency_id"])
        codes: set[str] = set()
        for record in records:
            value = record.get("currency_id")
            if isinstance(value, (list, tuple)):
                value = value[1] if len(value) == 2 else None
            if isinstance(value, str):
                code = value.strip().upper()
                if code:
                    codes.add(code)
        return sorted(codes)

    # -- write path ---------------------------------------------------------

    def create_draft_purchase_order(
        self,
        *,
        item_code: str,
        site_code: str,
        supplier_code: str,
        quantity: object,
        unit_price: object,
        origin: str | None = None,
    ) -> dict:
        """Create one DRAFT ``purchase.order`` in Odoo, idempotently.

        Fails closed on writes: the first action, before any network
        call, is the explicit write switch — writes are enabled only
        when ``ODOO_ALLOW_WRITES`` is ``1``, ``true``, or ``yes``
        (case-insensitive, whitespace-trimmed); anything else raises
        ``OdooWritesDisabledError`` (a fixed message with no
        environment values, credentials, or request payloads) and no
        request of any kind is made, before authentication or
        configuration validation. Inputs are then validated with no
        network: the three codes must be non-empty strings, ``origin``
        must be ``None`` or a non-empty string, and ``quantity`` and
        ``unit_price`` must be finite numbers (bools are not numbers)
        with ``quantity > 0`` and ``unit_price >= 0``, else
        ``ValueError`` before any request.

        Resolution goes through :meth:`search_read`, so the existing
        contract (authenticate once, one pinned origin, no secret in
        errors) holds:

        - the product is the one ``product.product`` whose
          ``default_code`` equals ``item_code``, read with the fields
          ``id`` and ``uom_id``;
        - the vendor is the one ``res.partner`` whose ``ref`` equals
          ``supplier_code`` when any vendor has that non-empty
          ``ref``, else the one whose ``name`` equals it;
        - the warehouse is the one ``stock.warehouse`` whose ``code``
          equals ``site_code``, and its receipt operation
          ``in_type_id`` is the order's ``picking_type_id``.

        Each must resolve to exactly one record with a usable id (a
        warehouse must also have an ``in_type_id``); zero or multiple
        matches raise ``OdooError`` naming which one failed, and
        nothing is created. A product whose ``uom_id`` is not usable
        raises ``OdooError`` "item '<item code>' has no unit of
        measure in Odoo" right after that one product read — before
        the idempotency search and before any create.

        The origin tag is the given ``origin``, or when none is given
        the namespaced default ``"AIERP:replenishment:" +
        quote(item_code, safe="") + ":" + quote(site_code, safe="")``
        (each code percent-encoded, so two different
        ``(item_code, site_code)`` pairs never share a tag even when a
        code contains a colon); the tag is used only as an opaque
        exact-match key and is never parsed back into fields. Before
        creating, one ``search_read`` looks for a ``purchase.order``
        whose ``origin`` equals the tag and whose ``state`` is
        ``"draft"``; exactly one such order is returned as-is with
        ``already_existed`` True and no create is issued (multiple
        such orders fail closed). Otherwise one ``purchase.order``
        ``create`` posts ``{partner_id, picking_type_id, origin,
        order_line: [(0, 0, {product_id, product_qty, price_unit,
        uom_id})]}`` — the ``uom_id`` is the product's base unit id
        from the one product read — and the order is left in its
        default ``draft`` state — the method never calls
        ``button_confirm``, ``button_cancel``,
        ``write``, or ``unlink``. The result is read back via
        ``search_read`` of ``["id", "name", "state"]`` and returned as
        ``{"odoo_id": int, "name": str, "state": str,
        "already_existed": bool}``. The method performs only these
        reads and the single create; it schedules nothing.
        """
        if not _writes_enabled():
            raise OdooWritesDisabledError(
                "Odoo writes are disabled; set ODOO_ALLOW_WRITES to 1, "
                "true, or yes to enable purchase order creation"
            )
        for name, value in (
            ("item_code", item_code),
            ("site_code", site_code),
            ("supplier_code", supplier_code),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if origin is not None and (not isinstance(origin, str) or not origin):
            raise ValueError("origin must be None or a non-empty string")
        quantity_value = _finite_float(quantity)
        unit_price_value = _finite_float(unit_price)
        if quantity_value is None or quantity_value <= 0:
            raise ValueError("quantity must be a positive finite number")
        if unit_price_value is None or unit_price_value < 0:
            raise ValueError("unit_price must be a non-negative finite number")

        if origin is None:
            origin = (
                "AIERP:replenishment:"
                + quote(item_code, safe="")
                + ":"
                + quote(site_code, safe="")
            )

        product_id, product = self._resolve_draft_po_product_record(
            item_code, ["id", "uom_id"]
        )
        uom_id = _ref_id(product.get("uom_id"))
        if uom_id is None:
            raise OdooError(f"item '{item_code}' has no unit of measure in Odoo")
        partner_id = self._resolve_draft_po_partner(supplier_code)
        picking_type_id = self._resolve_draft_po_picking_type(site_code)

        existing = self.search_read(
            "purchase.order",
            [["origin", "=", origin], ["state", "=", "draft"]],
            ["id", "name", "state"],
        )
        if len(existing) > 1:
            raise OdooError(f"multiple draft purchase orders carry origin '{origin}'")
        if existing:
            return self._draft_po_result(existing[0], already_existed=True)

        values = {
            "partner_id": partner_id,
            "picking_type_id": picking_type_id,
            "origin": origin,
            "order_line": [
                (
                    0,
                    0,
                    {
                        "product_id": product_id,
                        "product_qty": quantity,
                        "price_unit": unit_price,
                        "uom_id": uom_id,
                    },
                )
            ],
        }
        origin_url, db, _login, api_key, ca_bundle, timeout = self._load_config()
        uid = self._ensure_uid()
        subject = "Odoo request for model 'purchase.order'"
        result = self._rpc(
            origin_url,
            ca_bundle,
            timeout,
            "object",
            "execute_kw",
            [db, uid, api_key, "purchase.order", "create", [values]],
            OdooError,
            subject,
        )
        if isinstance(result, bool) or not isinstance(result, int) or result <= 0:
            raise OdooError(f"{subject} create returned a malformed id")
        created = self.search_read(
            "purchase.order", [["id", "=", result]], ["id", "name", "state"]
        )
        if len(created) != 1:
            raise OdooError(f"{subject} create could not be read back")
        return self._draft_po_result(created[0], already_existed=False)

    def preview_draft_purchase_order(
        self,
        *,
        item_code: str,
        site_code: str,
        supplier_code: str,
        origin: str | None = None,
    ) -> dict:
        """Preview what :meth:`create_draft_purchase_order` would do.

        Read-only: the method performs ONLY ``search_read`` reads —
        never a create, no write of any kind — and it does NOT consult
        ``ODOO_ALLOW_WRITES``: a preview is read-only, so the write
        switch neither enables nor blocks it. Inputs are validated with
        no network: the three codes must be non-empty strings and
        ``origin`` must be ``None`` or a non-empty string, else
        ``ValueError`` before any request.

        Resolution goes through the same exactly-one-match helpers as
        :meth:`create_draft_purchase_order`:

        - the product is the one ``product.product`` whose
          ``default_code`` equals ``item_code``;
        - the vendor is the one ``res.partner`` whose ``ref`` equals
          ``supplier_code`` when any vendor has that non-empty
          ``ref``, else the one whose ``name`` equals it;
        - the warehouse is the one ``stock.warehouse`` whose ``code``
          equals ``site_code``, and its receipt operation
          ``in_type_id`` is the order's ``picking_type_id``.

        Each must resolve to exactly one record with a usable id (a
        warehouse must also have an ``in_type_id``); zero or multiple
        matches raise ``OdooError`` naming which one failed.

        The origin tag is the given ``origin``, or when none is given
        the same namespaced default the create path uses:
        ``"AIERP:replenishment:" + quote(item_code, safe="") + ":" +
        quote(site_code, safe="")`` (each code percent-encoded). One
        ``search_read`` then looks for a ``purchase.order`` whose
        ``origin`` equals the tag and whose ``state`` is ``"draft"``;
        multiple such orders fail closed with ``OdooError``, and a
        single one is returned under ``existing``. The result is
        ``{"resolved": {"product_id": int, "partner_id": int,
        "picking_type_id": int}, "origin": str, "existing":
        {"odoo_id": int, "name": str, "state": str} | None}``.
        """
        for name, value in (
            ("item_code", item_code),
            ("site_code", site_code),
            ("supplier_code", supplier_code),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if origin is not None and (not isinstance(origin, str) or not origin):
            raise ValueError("origin must be None or a non-empty string")

        if origin is None:
            origin = (
                "AIERP:replenishment:"
                + quote(item_code, safe="")
                + ":"
                + quote(site_code, safe="")
            )

        product_id = self._resolve_draft_po_product(item_code)
        partner_id = self._resolve_draft_po_partner(supplier_code)
        picking_type_id = self._resolve_draft_po_picking_type(site_code)

        existing = self.search_read(
            "purchase.order",
            [["origin", "=", origin], ["state", "=", "draft"]],
            ["id", "name", "state"],
        )
        if len(existing) > 1:
            raise OdooError(f"multiple draft purchase orders carry origin '{origin}'")
        return {
            "resolved": {
                "product_id": product_id,
                "partner_id": partner_id,
                "picking_type_id": picking_type_id,
            },
            "origin": origin,
            "existing": self._draft_po_existing(existing[0]) if existing else None,
        }

    def write_reorder_rule(
        self,
        *,
        item_code: str,
        site_code: str,
        reorder_point: float,
        max_qty: float,
    ) -> dict:
        """Write the reorder point and max quantity of one reorder rule.

        Fails closed on writes: the first action, before any network
        call, is the explicit write switch — writes are enabled only
        when ``ODOO_ALLOW_WRITES`` is ``1``, ``true``, or ``yes``
        (case-insensitive, whitespace-trimmed); anything else raises
        ``OdooWritesDisabledError`` (a fixed message with no
        environment values, credentials, or request payloads) and no
        request of any kind is made, before authentication or
        configuration validation. Inputs are then validated with no
        network: ``item_code`` and ``site_code`` must be non-empty
        strings, and ``reorder_point`` and ``max_qty`` must be finite
        numbers (bools are not numbers) with
        ``0 <= reorder_point <= max_qty``, else ``ValueError`` before
        any request.

        The rule is located through :meth:`search_read`, so the
        existing contract (authenticate once, one pinned origin, no
        secret in errors) holds:

        - the product is the one ``product.product`` whose
          ``default_code`` equals ``item_code``;
        - the warehouse is the one ``stock.warehouse`` whose ``code``
          equals ``site_code``;
        - the rule is the one active ``stock.warehouse.orderpoint``
          for that product and warehouse, requested with the fields
          ``id``, ``product_min_qty`` and ``product_max_qty``.

        Each must resolve to exactly one record with a usable id;
        zero or multiple matches raise ``OdooError`` naming which one
        failed, and the method never creates a rule: no rule raises
        ``OdooError`` "no reorder rule for <item>/<site>" and more
        than one rule raises ``OdooError`` "more than one reorder
        rule for <item>/<site>", both before any write.

        The rule's current ``product_min_qty`` and ``product_max_qty``
        are the result's ``before``. When both already equal the
        request (absolute difference under 1e-6), the method returns
        ``{"outcome": "unchanged", "before": ..., "after": <same>}``
        with no ``write``. Otherwise exactly one ``execute_kw``
        ``write`` on the rule's id sets ``{"product_min_qty":
        reorder_point, "product_max_qty": max_qty}``, and the rule is
        then read back with ``read`` on that same id as ``after``.
        When the read-back matches the request (same tolerance), the
        method returns ``{"outcome": "written", "before": ...,
        "after": ...}``; when it does not, it raises
        ``OdooWriteVerificationError`` naming the item, the site and
        the field that did not match. A read-back transport or
        JSON-RPC failure raises the plain ``OdooError`` naming the
        model — never this verification error — and is not reported as
        a successful verification. No message contains the API key,
        the login, or a response body.
        """
        if not _writes_enabled():
            raise OdooWritesDisabledError(
                "Odoo writes are disabled; set ODOO_ALLOW_WRITES to 1, "
                "true, or yes to enable reorder rule writes"
            )
        for name, value in (("item_code", item_code), ("site_code", site_code)):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        reorder_point_value = _finite_float(reorder_point)
        max_qty_value = _finite_float(max_qty)
        if reorder_point_value is None or reorder_point_value < 0:
            raise ValueError("reorder_point must be a non-negative finite number")
        if max_qty_value is None or max_qty_value < reorder_point_value:
            raise ValueError("max_qty must be a finite number >= reorder_point")

        product_id = self._resolve_draft_po_product(item_code)
        warehouse_id = self._resolve_reorder_warehouse(site_code)

        rules = self.search_read(
            "stock.warehouse.orderpoint",
            [
                ["active", "=", True],
                ["product_id", "=", product_id],
                ["warehouse_id", "=", warehouse_id],
            ],
            ["id", "product_min_qty", "product_max_qty"],
        )
        if not rules:
            raise OdooError(f"no reorder rule for {item_code}/{site_code}")
        if len(rules) > 1:
            raise OdooError(f"more than one reorder rule for {item_code}/{site_code}")
        rule = rules[0]
        rule_id = _ref_id(rule.get("id"))
        if rule_id is None:
            raise OdooError(f"the reorder rule for {item_code}/{site_code} has no id")

        before = {
            "reorder_point": rule.get("product_min_qty"),
            "max_qty": rule.get("product_max_qty"),
        }
        before_reorder_point = _finite_float(before["reorder_point"])
        before_max_qty = _finite_float(before["max_qty"])
        if (
            before_reorder_point is not None
            and before_max_qty is not None
            and abs(before_reorder_point - reorder_point_value) < _REORDER_TOLERANCE
            and abs(before_max_qty - max_qty_value) < _REORDER_TOLERANCE
        ):
            return {
                "outcome": "unchanged",
                "before": before,
                "after": dict(before),
            }

        origin, db, _login, api_key, ca_bundle, timeout = self._load_config()
        uid = self._ensure_uid()
        subject = "Odoo request for model 'stock.warehouse.orderpoint'"
        values = {"product_min_qty": reorder_point, "product_max_qty": max_qty}
        write_result = self._rpc(
            origin,
            ca_bundle,
            timeout,
            "object",
            "execute_kw",
            [
                db,
                uid,
                api_key,
                "stock.warehouse.orderpoint",
                "write",
                [[rule_id], values],
            ],
            OdooError,
            subject,
        )
        if write_result is not True:
            raise OdooError(f"{subject} write returned a malformed result")

        after_result = self._rpc(
            origin,
            ca_bundle,
            timeout,
            "object",
            "execute_kw",
            [
                db,
                uid,
                api_key,
                "stock.warehouse.orderpoint",
                "read",
                [[rule_id], ["id", "product_min_qty", "product_max_qty"]],
            ],
            OdooError,
            subject,
        )
        if (
            not isinstance(after_result, list)
            or len(after_result) != 1
            or not isinstance(after_result[0], dict)
            or _ref_id(after_result[0].get("id")) != rule_id
        ):
            raise OdooError(f"{subject} read back is malformed")
        after = after_result[0]
        after_reorder_point = _finite_float(after.get("product_min_qty"))
        after_max_qty = _finite_float(after.get("product_max_qty"))
        if (
            after_reorder_point is None
            or abs(after_reorder_point - reorder_point_value) >= _REORDER_TOLERANCE
        ):
            raise OdooWriteVerificationError(
                f"write verification failed for {item_code}/{site_code}: "
                "product_min_qty does not match the requested value"
            )
        if (
            after_max_qty is None
            or abs(after_max_qty - max_qty_value) >= _REORDER_TOLERANCE
        ):
            raise OdooWriteVerificationError(
                f"write verification failed for {item_code}/{site_code}: "
                "product_max_qty does not match the requested value"
            )
        return {
            "outcome": "written",
            "before": before,
            "after": {
                "reorder_point": after.get("product_min_qty"),
                "max_qty": after.get("product_max_qty"),
            },
        }

    def _resolve_draft_po_product_record(
        self, item_code: str, fields: list[str]
    ) -> tuple[int, dict]:
        """Return the id and row of the one matching ``product.product``.

        The row is read with exactly ``fields``. Zero or multiple
        matches, or a match without a usable id, raise ``OdooError``
        naming the product; nothing has been written yet, so nothing
        is created either way.
        """
        records = self.search_read(
            "product.product", [["default_code", "=", item_code]], fields
        )
        if not records:
            raise OdooError(f"no product matches item code '{item_code}'")
        if len(records) > 1:
            raise OdooError(f"multiple products match item code '{item_code}'")
        product_id = _ref_id(records[0].get("id"))
        if product_id is None:
            raise OdooError(f"the product matching item code '{item_code}' has no id")
        return product_id, records[0]

    def _resolve_draft_po_product(self, item_code: str) -> int:
        """Return the id of the one product whose ``default_code`` matches.

        The row is read with the fields ``id`` only. Zero or multiple
        matches, or a match without a usable id, raise ``OdooError``
        naming the product; nothing has been written yet, so nothing
        is created either way.
        """
        product_id, _record = self._resolve_draft_po_product_record(item_code, ["id"])
        return product_id

    def _resolve_draft_po_partner(self, supplier_code: str) -> int:
        """Return the id of the one vendor matching the supplier code.

        A ``res.partner`` whose non-empty ``ref`` equals the code wins;
        with no such vendor, the one whose ``name`` equals it. Zero or
        multiple matches at either step raise ``OdooError`` naming the
        vendor; nothing has been written yet, so nothing is created
        either way.
        """
        for key in ("ref", "name"):
            records = self.search_read(
                "res.partner", [[key, "=", supplier_code]], ["id"]
            )
            if len(records) > 1:
                raise OdooError(
                    f"multiple vendors match supplier code '{supplier_code}'"
                )
            if not records:
                continue
            partner_id = _ref_id(records[0].get("id"))
            if partner_id is None:
                raise OdooError(
                    f"the vendor matching supplier code '{supplier_code}' " "has no id"
                )
            return partner_id
        raise OdooError(f"no vendor matches supplier code '{supplier_code}'")

    def _resolve_draft_po_picking_type(self, site_code: str) -> int:
        """Return the id of the site's warehouse receipt operation.

        The one ``stock.warehouse`` whose ``code`` equals the code, via
        its ``in_type_id`` (the PO's ``picking_type_id``). Zero or
        multiple warehouses, or a warehouse without an ``in_type_id``,
        raise ``OdooError`` naming the warehouse; nothing has been
        written yet, so nothing is created either way.
        """
        records = self.search_read(
            "stock.warehouse", [["code", "=", site_code]], ["id", "in_type_id"]
        )
        if not records:
            raise OdooError(f"no warehouse matches site code '{site_code}'")
        if len(records) > 1:
            raise OdooError(f"multiple warehouses match site code '{site_code}'")
        picking_type_id = _ref_id(records[0].get("in_type_id"))
        if picking_type_id is None:
            raise OdooError(
                f"the warehouse matching site code '{site_code}' has no "
                "receipt operation (in_type_id)"
            )
        return picking_type_id

    @staticmethod
    def _draft_po_result(record: dict, *, already_existed: bool) -> dict:
        """Shape a read-back ``purchase.order`` row into the result dict.

        The row must carry a usable id plus ``name`` and ``state``
        strings; anything else is a malformed read-back and raises
        ``OdooError``.
        """
        odoo_id = _ref_id(record.get("id"))
        name = record.get("name")
        state = record.get("state")
        if odoo_id is None or not isinstance(name, str) or not isinstance(state, str):
            raise OdooError("the draft purchase order read back is malformed")
        return {
            "odoo_id": odoo_id,
            "name": name,
            "state": state,
            "already_existed": already_existed,
        }

    @staticmethod
    def _draft_po_existing(record: dict) -> dict:
        """Shape a draft ``purchase.order`` row into the preview's
        ``existing`` dict.

        The row must carry a usable id plus ``name`` and ``state``
        strings; anything else is a malformed row and raises
        ``OdooError``.
        """
        odoo_id = _ref_id(record.get("id"))
        name = record.get("name")
        state = record.get("state")
        if odoo_id is None or not isinstance(name, str) or not isinstance(state, str):
            raise OdooError("the draft purchase order read back is malformed")
        return {
            "odoo_id": odoo_id,
            "name": name,
            "state": state,
        }

    def _resolve_reorder_warehouse(self, site_code: str) -> int:
        """Return the id of the one warehouse whose ``code`` matches.

        Zero or multiple matches, or a match without a usable id,
        raise ``OdooError`` naming the warehouse; nothing has been
        written yet, so nothing is created either way.
        """
        records = self.search_read(
            "stock.warehouse", [["code", "=", site_code]], ["id"]
        )
        if not records:
            raise OdooError(f"no warehouse matches site code '{site_code}'")
        if len(records) > 1:
            raise OdooError(f"multiple warehouses match site code '{site_code}'")
        warehouse_id = _ref_id(records[0].get("id"))
        if warehouse_id is None:
            raise OdooError(f"the warehouse matching site code '{site_code}' has no id")
        return warehouse_id
