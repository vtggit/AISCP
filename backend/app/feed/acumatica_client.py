"""Fail-closed client for one Acumatica instance's entity and OData APIs.

``AcumaticaClient`` talks to one Acumatica instance. Every public call
opens exactly one server session: one ``POST {base}/entity/auth/login``
carrying ``{"name", "password", "tenant"}`` (any 2xx is success; 401 or
403 raises ``AcumaticaAuthError``; any other non-2xx raises
``AcumaticaError`` with the status), then the call's work, and — in a
``finally`` — one ``POST {base}/entity/auth/logout``; the logout is sent
even when the work raised. When the login itself fails, no logout is
sent and the error propagates. The logout is best-effort: a failed
logout never replaces or masks the primary error.

``resolve_codes(site_ids, vendor_ids)`` reads
``GET {base}/odatav4/{tenant}/INSite?$select=SiteID,SiteCD`` and
``GET {base}/odatav4/{tenant}/BAccount?$select=BAccountID,AcctCD,Type``
with HTTP Basic auth and returns
``{"sites": {"154": "WHOLESALE", ...}, "vendors": {"4851": "CONPERIPH",
...}}`` for the requested ids only: codes are stripped of surrounding
whitespace, only ``BAccount`` rows of ``Type`` ``VE`` or ``VC`` count as
vendors, and an id with no match is simply absent.

``create_draft_purchase_order(*, site_id, vendor_id, origin, lines)``
creates one ON-HOLD ``PurchaseOrder`` for a site/vendor pair,
idempotent on an origin tag and gated by the ``ACUMATICA_ALLOW_WRITES``
write switch, and ``preview_draft_purchase_order(...)`` read-only
previews that same resolution and duplicate search: it returns the
payload it would send and whether an order with that origin already
exists, never a write, and it does not consult the write switch.

``create_draft_purchase_orders(orders)`` and
``preview_draft_purchase_orders(orders)`` run that same per-order work
for a whole batch of ``{site_id, vendor_id, origin, lines}`` entries in
one session: every entry is validated exactly as the single-order
functions validate their arguments — all before any request, an
invalid entry raising ``ValueError`` naming its index, as do two
entries carrying the same origin — an empty list returns ``[]`` with
no request, one code-resolution pass over the union of the entries'
site and vendor ids supplies the codes, and then, per order in input
order, the duplicate search runs and — on the write path only, which
raises ``AcumaticaWritesDisabledError`` before any request unless
writes are enabled — the write. The result is one dict per order, in
input order: ``{origin, outcome, order_nbr, status, warehouse_code,
vendor_code, line_count, error}``; ``outcome`` is ``would_create``
(carrying the would-be ``payload``) or ``exists`` for the preview and
``created`` or ``exists`` for the write, or ``error`` — with
``error`` a message carrying no secrets — when that order's site or
vendor code is missing, its duplicate search is ambiguous or
malformed, or its write fails, in which case the batch continues with
the next order. A login failure, a code-lookup failure, or a 401/403
anywhere in the session still raises for the whole batch.

``preview_transfer_orders(transfers)`` and
``create_transfer_orders(transfers)`` run the same one-session,
per-entry work for a batch of ``{from_site_id, to_site_id, origin,
lines}`` entries: every entry is validated before any request — the
two site ids are positive integer ids (the ``_normalize_id`` rules,
so an empty, NUL, non-numeric, non-positive, or leading-zero id is
rejected) that must differ from each other, ``origin`` is a non-empty string of at
most 255 characters, and ``lines`` is a non-empty list of
``{item_code, quantity}`` with every quantity a whole number above 0
— an invalid entry raising ``ValueError`` naming its index, as do two
entries carrying the same origin, and an empty list returns ``[]``
with no request. One session (login, then logout in a best-effort
``finally``) resolves the warehouse codes for the union of the
entries' site ids in one code-lookup pass, then — per entry, in input
order — runs the duplicate search on the origin tag and, on the write
path only, which raises ``AcumaticaWritesDisabledError`` before any
request unless writes are enabled, the write. The result is one dict
per entry, in input order: ``{origin, outcome, reference_nbr, status,
from_warehouse_code, to_warehouse_code, line_count, error}`` with
``outcome`` ``would_create`` for the preview and ``created``,
``exists``, or ``error`` for the write; an ``error`` names the
operation mode and the failing entry with a secret-free reason, and
the batch continues past it. A login or code-lookup failure, or a
401/403 anywhere in the session, still raises for the whole batch.

``read_purchase_orders(order_nbrs)`` reads the named orders: one
session (the same login, best-effort logout, no redirects, encoded
values) over the numbers in chunks of at most 25, each a single
``GET {base}/entity/Default/{version}/PurchaseOrder`` with the
``$filter`` the ``or`` of one ``OrderNbr eq '<nbr>'`` term per number
(quotes doubled). Every answer is parsed exactly as #543 parses the
duplicate search: a bare JSON array of rows, every field wrapped as
``{"value": ...}`` or plain, and the envelope keys ``id``,
``rowNumber``, ``note``, ``custom`` and ``_links`` ignored. It
returns ``{order_nbr: {status, hold, date, promised_on, total,
lines: [{item_code, warehouse_code, order_qty, received_qty,
completed, cancelled, promised}]}}`` for the orders found (an order
not in the ERP is absent, ``received_qty`` is ``QtyOnReceipts``, and
every date is the ISO calendar date of the ERP timestamp); a failed
chunk raises for the whole call rather than merging partial results.
No write is involved, so ``ACUMATICA_ALLOW_WRITES`` does not apply.

``read_transfer_order_statuses(reference_nbrs)`` reads the named
transfer orders' statuses: one session (the same login, best-effort
logout, no redirects, encoded values) over the reference numbers in
chunks of at most 20, each a single
``GET {base}/entity/Default/{version}/TransferOrder`` with the
``$filter`` the ``or`` of one ``ReferenceNbr eq '<nbr>'`` term per
number (quotes doubled). The list is a list of non-empty reference
number strings, at most 100 of them and none carrying a NUL byte, an
empty list answers ``{}`` with no request, and a 401/403 raises
``AcumaticaAuthError`` while any other failure raises
``AcumaticaError`` with no secrets. It answers
``{reference_nbr: status}`` for the orders found (a number the ERP
does not return is absent) and never writes.

``read_ledger_currencies()`` reads the ledger currencies: one session
(the same login, best-effort logout, no redirects) sending exactly one
``GET {base}/entity/Default/{version}/Ledger`` with no ``$select``.
The answer is parsed exactly as the other entity reads parse theirs:
a bare JSON array of rows (an object whose ``"value"`` is an array is
accepted as well), every field plain or wrapped as ``{"value": ...}``,
and the envelope keys ``id``, ``rowNumber``, ``note``, ``custom`` and
``_links`` ignored. A failed sign-in, a 401/403 (``AcumaticaAuthError``),
any other non-2xx, or a malformed body or row raises
``AcumaticaError`` for the whole call. It answers the sorted,
distinct, uppercased ``Currency`` values of the ledgers whose ``Type``
is ``Actual`` (case-insensitive); a ledger whose ``Type`` or
``Currency`` is missing or not a non-empty string contributes nothing.
No write is involved, so ``ACUMATICA_ALLOW_WRITES`` does not apply.

``write_item_warehouse_parameters(*, item_code, site_code,
reorder_point, max_qty)`` writes one item-warehouse row's
replenishment parameters, gated by the same
``ACUMATICA_ALLOW_WRITES`` write switch and validated before any
request: non-empty string codes, both numbers finite with
``0 <= reorder_point <= max_qty`` (bools are not numbers), else
``ValueError``. One session reads
``GET {origin}/entity/Default/{version}/ItemWarehouse`` filtered on
the item and the site (the REST contract endpoint the PurchaseOrder
paths build with ``_endpoint_version()`` — never the read-only
``odatav4`` feed) with ``$top=1``; a row-less answer raises
``AcumaticaError`` naming the item and the site, and a null
``ReorderPoint`` or ``MaxQty`` is 0.0 for the ``before`` state. When
both values already equal the request (absolute difference under
1e-6) it returns ``{"outcome": "unchanged", "before", "after"}`` with
``after`` the ``before`` and no write; otherwise it PUTs that same
REST URL twice — first the override flags, then the values, every
value wrapped as ``{"value": ...}`` — and re-reads the row as
``after``. The read-back must carry both requested values and both
override flags enabled, else it raises
``AcumaticaWriteVerificationError`` naming the item, the site and the
field that did not take.

Configuration is read from the environment at call time:

- ``ACUMATICA_BASE_URL``: the instance URL (e.g. ``https://host/Instance``);
  must be ``https://`` unless ``ACUMATICA_ALLOW_INSECURE`` is truthy.
- ``ACUMATICA_TENANT``: the Acumatica tenant (site) name.
- ``ACUMATICA_USERNAME``: the user name sent to the login call.
- ``ACUMATICA_PASSWORD``: the password sent to the login call.
- ``ACUMATICA_ENDPOINT_VERSION``: optional entity-API endpoint version;
  a positive simple value such as ``25.200.001`` replaces the
  ``25.200.001`` default, while unset, blank, or malformed values keep
  the default or raise ``AcumaticaConfigError``.
- ``ACUMATICA_CA_BUNDLE``: optional CA bundle path for TLS verification;
  blank after trimming whitespace is treated as unset.
- ``ACUMATICA_TIMEOUT_SECONDS``: optional per-request timeout in seconds,
  read at call time; a positive number (an integer or a decimal such as
  ``90`` or ``12.5``) replaces the 30-second default, while unset,
  blank, zero, negative, or non-numeric values keep it.
- ``ACUMATICA_ALLOW_INSECURE``: optional; a truthy value (``1``,
  ``true``, ``yes``, or ``on``, case-insensitive, whitespace-trimmed)
  accepts an ``http://`` base URL.
- ``ACUMATICA_ALLOW_WRITES``: optional; a truthy value (``1``, ``true``,
  ``yes``, or ``on``, case-insensitive, whitespace-trimmed) enables the
  write path; anything else makes ``create_draft_purchase_order`` raise
  ``AcumaticaWritesDisabledError`` before any request.

The client fails closed: configuration and argument problems raise
before any request; every OData filter and URL path/query value derived
from configuration or plan data is percent-encoded (httpx params for
query values, ``quote`` for path segments); the client never follows
redirects, so credentials are never forwarded to another host; and no
error message contains the password, the user name, or — beyond at most
300 characters on a write failure — a response body. Transport errors,
OData auth failures, duplicate-search ambiguity, and malformed 2xx
responses raise explicit client errors rather than being treated as
successful writes. The client performs no automatic retries.
"""

import logging
import math
import os
import re
import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.feed.switches import TRUE_VALUES, env_switch

logger = logging.getLogger(__name__)

__all__ = [
    "AcumaticaAuthError",
    "AcumaticaClient",
    "AcumaticaConfigError",
    "AcumaticaError",
    "AcumaticaWriteVerificationError",
    "AcumaticaWritesDisabledError",
]

BASE_URL_VAR = "ACUMATICA_BASE_URL"
TENANT_VAR = "ACUMATICA_TENANT"
USERNAME_VAR = "ACUMATICA_USERNAME"
PASSWORD_VAR = "ACUMATICA_PASSWORD"
ENDPOINT_VERSION_VAR = "ACUMATICA_ENDPOINT_VERSION"
CA_BUNDLE_VAR = "ACUMATICA_CA_BUNDLE"
TIMEOUT_VAR = "ACUMATICA_TIMEOUT_SECONDS"
ALLOW_INSECURE_VAR = "ACUMATICA_ALLOW_INSECURE"
ALLOW_WRITES_VAR = "ACUMATICA_ALLOW_WRITES"

DEFAULT_ENDPOINT_VERSION = "25.200.001"

_LOGIN_PATH = "/entity/auth/login"
_LOGOUT_PATH = "/entity/auth/logout"
_REQUEST_TIMEOUT = 30.0
_MAX_IDS_PER_CALL = 1000
_MAX_ORIGIN_LENGTH = 255
_MAX_CODE_LENGTH = 255
_MAX_BODY_SNIPPET = 300
_MAX_PO_READ_CHUNK = 25
_MAX_TRANSFER_READ_CHUNK = 20
_MAX_TRANSFER_STATUS_READ = 100
_TRUTHY_VALUES = TRUE_VALUES
_ID_RE = re.compile(r"[0-9]{1,19}")
_VERSION_RE = re.compile(r"[0-9A-Za-z._-]{1,64}")
_INSTANCE_PATH_RE = re.compile(r"[A-Za-z0-9._~-]+(/[A-Za-z0-9._~-]+)*")

_BASE_URL_ERROR = (
    f"{BASE_URL_VAR} must be an http(s) instance URL with a host and no "
    "user info, query, or fragment"
)
_INSECURE_URL_ERROR = (
    f"{BASE_URL_VAR} must use https unless {ALLOW_INSECURE_VAR} is set "
    "to a truthy value"
)
_VERSION_ERROR = (
    f"{ENDPOINT_VERSION_VAR} must be a simple endpoint version such as " "25.200.001"
)


class AcumaticaError(Exception):
    """An Acumatica request or call failed.

    The message names the failure class and, for non-2xx responses, the
    HTTP status; it never contains the password, the user name, or a
    response body beyond at most 300 characters on a write failure.
    """


class AcumaticaConfigError(AcumaticaError):
    """Acumatica configuration is missing or invalid.

    The message names the offending environment variable and never its value.
    """


class AcumaticaAuthError(AcumaticaError):
    """An Acumatica authentication phase failed.

    Raised when the login call — or a call inside an authenticated
    session — is answered 401 or 403; the message names the HTTP status
    but never the password, the user name, or a response body.
    """


class AcumaticaWritesDisabledError(AcumaticaError):
    """The explicit write switch is off.

    Raised by the write paths (``create_draft_purchase_order``,
    ``create_draft_purchase_orders``, ``create_transfer_orders`` and
    ``write_item_warehouse_parameters``) when
    ``ACUMATICA_ALLOW_WRITES`` is not set to a truthy value; the message
    is fixed and never contains environment values, credentials, or
    request payloads. It is not an authentication failure.
    """


class AcumaticaWriteVerificationError(AcumaticaError):
    """A write was sent, but the read-back does not confirm it.

    Raised by ``write_item_warehouse_parameters`` when the row
    re-read after the two PUTs does not carry the requested
    ``ReorderPoint``/``MaxQty`` values and both override flags
    enabled; the message names the item, the site and the field that
    did not take, and never contains credentials or a response body.
    """


@dataclass(frozen=True)
class _AcumaticaConfig:
    """One validated call-time configuration snapshot."""

    origin: str
    tenant: str
    username: str
    password: str
    version: str
    ca_bundle: str | None
    timeout: float


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise AcumaticaConfigError(f"{name} must be set to a non-blank value")
    return value


def _is_truthy_env(name: str) -> bool:
    """Return True when ``name`` is set to a truthy value.

    Truthy means ``1``, ``true``, ``yes``, or ``on`` (case-insensitive,
    whitespace-trimmed); unset, blank, or any other value is falsy.
    """
    return env_switch(name)


def _allow_insecure() -> bool:
    """Return True when ``ACUMATICA_ALLOW_INSECURE`` is set to a
    truthy value (``1``, ``true``, ``yes``, or ``on``)."""
    return _is_truthy_env(ALLOW_INSECURE_VAR)


def _writes_enabled() -> bool:
    """Return True when ``ACUMATICA_ALLOW_WRITES`` is set to a
    truthy value (``1``, ``true``, ``yes``, or ``on``)."""
    return _is_truthy_env(ALLOW_WRITES_VAR)


def _validate_base_url(base_url: str, allow_insecure: bool) -> str:
    """Return the instance URL with any trailing slash dropped, or raise."""
    if base_url != base_url.strip():
        raise AcumaticaConfigError(_BASE_URL_ERROR)
    try:
        parts = urlsplit(base_url)
        hostname = parts.hostname
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        raise AcumaticaConfigError(_BASE_URL_ERROR) from None
    scheme = parts.scheme.lower()
    path = parts.path[1:] if parts.path.startswith("/") else parts.path
    path = path.rstrip("/")
    path_ok = not path or _INSTANCE_PATH_RE.fullmatch(path) is not None
    if (
        scheme not in ("http", "https")
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or not path_ok
    ):
        raise AcumaticaConfigError(_BASE_URL_ERROR)
    if scheme == "http" and not allow_insecure:
        raise AcumaticaConfigError(_INSECURE_URL_ERROR)
    return base_url.rstrip("/")


def _endpoint_version() -> str:
    """Return the endpoint version, or the default when unset or blank."""
    raw = os.environ.get(ENDPOINT_VERSION_VAR)
    if raw is None or not raw.strip():
        return DEFAULT_ENDPOINT_VERSION
    version = raw.strip()
    if _VERSION_RE.fullmatch(version) is None:
        raise AcumaticaConfigError(_VERSION_ERROR)
    return version


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
        raise AcumaticaConfigError(
            f"{CA_BUNDLE_VAR} names a file that does not exist or cannot be read"
        ) from None


def _build_client(ca_bundle: str | None, timeout: float) -> httpx.Client:
    """Build the per-call ``httpx.Client``.

    Redirects are never followed, so credentials (the Basic header on
    OData reads, the session from the login call) are never forwarded to
    a redirected host. Tests replace this module-level function with a
    factory over ``httpx.MockTransport``.
    """
    verify = ca_bundle if ca_bundle is not None else True
    try:
        return httpx.Client(verify=verify, timeout=timeout, follow_redirects=False)
    except (ssl.SSLError, OSError) as exc:
        raise AcumaticaConfigError(
            f"{CA_BUNDLE_VAR} could not be applied to the Acumatica client "
            f"({type(exc).__name__})"
        ) from None


def _normalize_id(value: object, name: str) -> str:
    """Normalize one id (int or numeric string) to its string form.

    Bools and other shapes, non-numeric strings, strings with a
    leading zero (not the canonical form of a positive integer id),
    and non-positive ids raise ``ValueError`` naming the parameter.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{name} must be a positive integer id")
    if isinstance(value, str):
        text = value.strip()
        if _ID_RE.fullmatch(text) is None:
            raise ValueError(f"{name} must be a positive integer id")
        if text[0] == "0":
            raise ValueError(f"{name} must be a positive integer id")
        return text
    if not 1 <= value <= 10**18:
        raise ValueError(f"{name} must be a positive integer id")
    return str(value)


def _validate_id_list(values: object, name: str) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ValueError(f"{name} must be a list of integer ids")
    if len(values) > _MAX_IDS_PER_CALL:
        raise ValueError(f"{name} must contain at most {_MAX_IDS_PER_CALL} ids")
    return [_normalize_id(value, name) for value in values]


def _row_id(value: object) -> str | None:
    """Normalize one id coming from an Acumatica row, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    if isinstance(value, str):
        text = value.strip()
        return text if _ID_RE.fullmatch(text) is not None else None
    return str(value) if 1 <= value <= 10**18 else None


def _finite_number(value: object, name: str, *, positive: bool) -> float | int:
    """Validate one numeric line field; return it unchanged.

    Bools and non-numeric shapes, non-finite values, and — when
    ``positive`` — values ``<= 0`` raise ``ValueError`` naming the field.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if positive and value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    if not positive and value < 0:
        raise ValueError(f"{name} must be a finite number of at least 0")
    return value


def _validate_draft_po_args(
    site_id: object,
    vendor_id: object,
    origin: object,
    lines: object,
) -> tuple[str, str, str, list[dict[str, Any]]]:
    """Validate the draft-PO arguments with no network; return normalized.

    ``site_id`` and ``vendor_id`` must be positive integer ids (ints or
    numeric strings), ``origin`` a non-empty string of at most 255
    characters, and ``lines`` a list of ``{item_code, quantity,
    unit_cost}`` dicts whose quantity is a positive finite number and
    whose unit cost is a finite number >= 0; anything else raises
    ``ValueError`` before any request.
    """
    site = _normalize_id(site_id, "site_id")
    vendor = _normalize_id(vendor_id, "vendor_id")
    if not isinstance(origin, str) or not origin:
        raise ValueError("origin must be a non-empty string")
    if len(origin) > _MAX_ORIGIN_LENGTH:
        raise ValueError(f"origin must be at most {_MAX_ORIGIN_LENGTH} characters")
    if isinstance(lines, (str, bytes)) or not isinstance(lines, (list, tuple)):
        raise ValueError(
            "lines must be a list of {item_code, quantity, unit_cost} dicts"
        )
    validated: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise ValueError(
                "lines must be a list of {item_code, quantity, unit_cost} dicts"
            )
        item_code = line.get("item_code")
        if not isinstance(item_code, str) or not item_code.strip():
            raise ValueError(f"lines[{index}].item_code must be a non-empty string")
        if len(item_code) > _MAX_CODE_LENGTH:
            raise ValueError(
                f"lines[{index}].item_code must be at most {_MAX_CODE_LENGTH} characters"
            )
        quantity = _finite_number(
            line.get("quantity"), f"lines[{index}].quantity", positive=True
        )
        unit_cost = _finite_number(
            line.get("unit_cost"), f"lines[{index}].unit_cost", positive=False
        )
        validated.append(
            {"item_code": item_code, "quantity": quantity, "unit_cost": unit_cost}
        )
    return site, vendor, origin, validated


def _validate_draft_po_orders(
    orders: object,
) -> list[tuple[str, str, str, list[dict[str, Any]]]]:
    """Validate a batch of draft-PO entries with no network; return them.

    ``orders`` must be a list of ``{site_id, vendor_id, origin, lines}``
    dicts, each entry validated exactly as
    :meth:`AcumaticaClient.create_draft_purchase_order` validates its
    arguments; an invalid entry raises ``ValueError`` naming its
    index, and two entries carrying the same origin raise
    ``ValueError`` as well. Everything happens before any request.
    """
    if isinstance(orders, (str, bytes)) or not isinstance(orders, (list, tuple)):
        raise ValueError(
            "orders must be a list of {site_id, vendor_id, origin, lines} dicts"
        )
    validated: list[tuple[str, str, str, list[dict[str, Any]]]] = []
    first_origin_index: dict[str, int] = {}
    for index, entry in enumerate(orders):
        if not isinstance(entry, dict):
            raise ValueError(
                f"orders[{index}] must be a dict with site_id, vendor_id, "
                "origin and lines"
            )
        try:
            site, vendor, origin_text, lines = _validate_draft_po_args(
                entry.get("site_id"),
                entry.get("vendor_id"),
                entry.get("origin"),
                entry.get("lines"),
            )
        except ValueError as exc:
            raise ValueError(f"orders[{index}]: {exc}") from None
        first = first_origin_index.setdefault(origin_text, index)
        if first != index:
            raise ValueError(
                f"orders[{index}].origin is a duplicate of " f"orders[{first}].origin"
            )
        validated.append((site, vendor, origin_text, lines))
    return validated


def _validate_transfer_args(
    from_site_id: object,
    to_site_id: object,
    origin: object,
    lines: object,
) -> tuple[str, str, str, list[dict[str, Any]]]:
    """Validate one transfer-order entry with no network; return it.

    ``from_site_id`` and ``to_site_id`` are positive integer ids —
    the existing :func:`_normalize_id` rules, so an empty, NUL,
    non-numeric, non-positive, or leading-zero id is rejected — and
    must differ from each other; ``origin`` is a non-empty string of
    at most 255 characters; and ``lines`` is a non-empty list of
    ``{item_code, quantity}`` dicts with every quantity a whole
    number above 0 (bools are not numbers). Anything else raises
    ``ValueError`` before any request.
    """
    from_site = _normalize_id(from_site_id, "from_site_id")
    to_site = _normalize_id(to_site_id, "to_site_id")
    if from_site == to_site:
        raise ValueError("from_site_id and to_site_id must differ")
    if not isinstance(origin, str) or not origin:
        raise ValueError("origin must be a non-empty string")
    if len(origin) > _MAX_ORIGIN_LENGTH:
        raise ValueError(f"origin must be at most {_MAX_ORIGIN_LENGTH} characters")
    if (
        isinstance(lines, (str, bytes))
        or not isinstance(lines, (list, tuple))
        or not lines
    ):
        raise ValueError(
            "lines must be a non-empty list of {item_code, quantity} dicts"
        )
    validated: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise ValueError(
                "lines must be a non-empty list of {item_code, quantity} dicts"
            )
        item_code = line.get("item_code")
        if not isinstance(item_code, str) or not item_code.strip():
            raise ValueError(f"lines[{index}].item_code must be a non-empty string")
        if len(item_code) > _MAX_CODE_LENGTH:
            raise ValueError(
                f"lines[{index}].item_code must be at most {_MAX_CODE_LENGTH} characters"
            )
        quantity = line.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError(f"lines[{index}].quantity must be a whole number above 0")
        validated.append({"item_code": item_code, "quantity": quantity})
    return from_site, to_site, origin, validated


def _validate_transfer_orders(
    transfers: object,
) -> list[tuple[str, str, str, list[dict[str, Any]]]]:
    """Validate a batch of transfer-order entries with no network.

    ``transfers`` must be a list of ``{from_site_id, to_site_id,
    origin, lines}`` dicts, each entry validated exactly as
    :func:`_validate_transfer_args` validates it; an invalid entry
    raises ``ValueError`` naming its index, and two entries carrying
    the same origin raise ``ValueError`` as well. Everything happens
    before any request.
    """
    if isinstance(transfers, (str, bytes)) or not isinstance(transfers, (list, tuple)):
        raise ValueError(
            "transfers must be a list of "
            "{from_site_id, to_site_id, origin, lines} dicts"
        )
    validated: list[tuple[str, str, str, list[dict[str, Any]]]] = []
    first_origin_index: dict[str, int] = {}
    for index, entry in enumerate(transfers):
        if not isinstance(entry, dict):
            raise ValueError(
                f"transfers[{index}] must be a dict with from_site_id, "
                "to_site_id, origin and lines"
            )
        try:
            validated_entry = _validate_transfer_args(
                entry.get("from_site_id"),
                entry.get("to_site_id"),
                entry.get("origin"),
                entry.get("lines"),
            )
        except ValueError as exc:
            raise ValueError(f"transfers[{index}]: {exc}") from None
        from_site, to_site, origin_text, lines = validated_entry
        first = first_origin_index.setdefault(origin_text, index)
        if first != index:
            raise ValueError(
                f"transfers[{index}].origin is a duplicate of "
                f"transfers[{first}].origin"
            )
        validated.append((from_site, to_site, origin_text, lines))
    return validated


def _item_warehouse_quantity(value: object, name: str) -> float:
    """One item-warehouse quantity: a finite number of at least 0.

    Bools and non-numeric shapes, non-finite values, and magnitudes
    beyond the float range (which would otherwise leak
    ``OverflowError``) raise ``ValueError`` naming the parameter.
    """
    try:
        return float(_finite_number(value, name, positive=False))
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite number") from exc


def _validate_item_warehouse_args(
    item_code: object,
    site_code: object,
    reorder_point: object,
    max_qty: object,
) -> tuple[str, str, float, float]:
    """Validate the item-warehouse arguments with no network; return them.

    ``item_code`` and ``site_code`` must be non-empty strings and both
    numbers finite with ``0 <= reorder_point <= max_qty`` (bools are
    not numbers); anything else raises ``ValueError`` before any
    request.
    """
    if not isinstance(item_code, str) or not item_code:
        raise ValueError("item_code must be a non-empty string")
    if not isinstance(site_code, str) or not site_code:
        raise ValueError("site_code must be a non-empty string")
    reorder = _item_warehouse_quantity(reorder_point, "reorder_point")
    max_value = _item_warehouse_quantity(max_qty, "max_qty")
    if reorder > max_value:
        raise ValueError("reorder_point must be at most max_qty")
    return item_code, site_code, reorder, max_value


def _odata_escape(value: str) -> str:
    """Escape one value for an OData string literal (double the quotes)."""
    return value.replace("'", "''")


def _validate_order_nbrs(values: object, name: str = "order_nbrs") -> list[str]:
    """Validate one order-number list; return it unchanged.

    A list or tuple of non-empty strings (a string, bytes, or a list
    carrying a non-string or empty entry raises ``ValueError`` naming
    the offending entry) — every number reaches the wire only inside
    a quoted, quote-doubled OData literal.
    """
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ValueError(f"{name} must be a list of order number strings")
    result: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name}[{index}] must be a non-empty order number string")
        result.append(value)
    return result


def _validate_transfer_reference_nbrs(
    values: object, name: str = "reference_nbrs"
) -> list[str]:
    """Validate one transfer reference-number list; return it unchanged.

    A list or tuple of non-empty strings, at most
    ``_MAX_TRANSFER_STATUS_READ`` of them and none carrying a NUL byte
    (a string, bytes, an over-long list, or a list carrying a
    non-string, empty, or NUL entry raises ``ValueError`` naming the
    offending entry) — every number reaches the wire only inside a
    quoted, quote-doubled OData literal.
    """
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ValueError(f"{name} must be a list of reference number strings")
    if len(values) > _MAX_TRANSFER_STATUS_READ:
        raise ValueError(
            f"{name} must contain at most "
            f"{_MAX_TRANSFER_STATUS_READ} reference numbers"
        )
    result: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"{name}[{index}] must be a non-empty reference number string"
            )
        if "\x00" in value:
            raise ValueError(f"{name}[{index}] must not contain a NUL byte")
        result.append(value)
    return result


def _entity_value(row: dict[str, Any], name: str) -> Any:
    """The raw value of one row field.

    Plain, or inside the ``{"value": ...}`` envelope the entity API
    carries on scalar fields; missing is None.
    """
    value = row.get(name)
    if isinstance(value, dict):
        return value.get("value")
    return value


def _po_text(row: dict[str, Any], name: str, subject: str) -> str | None:
    """One text field: plain or enveloped string, missing is None."""
    value = _entity_value(row, name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AcumaticaError(f"{subject} returned a malformed field {name}")
    return value


def _po_bool(row: dict[str, Any], name: str, subject: str) -> bool:
    """One boolean field: plain or enveloped bool, missing is False."""
    value = _entity_value(row, name)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise AcumaticaError(f"{subject} returned a malformed field {name}")
    return value


def _po_number(
    row: dict[str, Any], name: str, subject: str, *, default: float | None
) -> float | None:
    """One numeric field: int or float that fits in a finite float,
    missing default.

    An int too large to convert to float is malformed like the other
    hostile shapes; the overflow must not escape the client.
    """
    value = _entity_value(row, name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AcumaticaError(f"{subject} returned a malformed field {name}")
    try:
        number = float(value)
    except OverflowError:
        raise AcumaticaError(f"{subject} returned a malformed field {name}")
    if not math.isfinite(number):
        raise AcumaticaError(f"{subject} returned a malformed field {name}")
    return number


def _erp_date(value: object, subject: str) -> str | None:
    """The ISO calendar date of one ERP timestamp field, or None.

    A ``YYYY-MM-DD`` date or a ``YYYY-MM-DDTHH:MM:SS`` timestamp
    (fractional seconds and a ``Z`` or numeric offset allowed) is
    answered as its date part, ``YYYY-MM-DD``; missing, null, or blank
    is None, and any other shape raises the explicit client error.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise AcumaticaError(f"{subject} returned a malformed date")
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise AcumaticaError(f"{subject} returned a malformed date") from None
    return parsed.date().isoformat()


def _parse_purchase_order_row(row: dict[str, Any], subject: str) -> dict[str, Any]:
    """One PurchaseOrder row in the contract-based REST shape.

    Every field is plain or enveloped as ``{"value": ...}``, and the
    envelope keys ``id``, ``rowNumber``, ``note``, ``custom`` and
    ``_links`` are ignored. ``OrderNbr`` and each line's ``InventoryID``
    are required; a missing, empty, or otherwise typed required field,
    a non-finite number, or a non-ISO date raises the explicit client
    error rather than a half-parsed order.
    """
    order_nbr = _entity_field(row, "OrderNbr")
    if order_nbr is None:
        raise AcumaticaError(f"{subject} returned a malformed order")
    details = row.get("Details")
    if details is None:
        details = []
    if not isinstance(details, list) or any(
        not isinstance(line, dict) for line in details
    ):
        raise AcumaticaError(f"{subject} returned a malformed order")
    lines: list[dict[str, Any]] = []
    for line in details:
        item_code = _entity_field(line, "InventoryID")
        if item_code is None:
            raise AcumaticaError(f"{subject} returned a malformed line")
        order_qty = _po_number(line, "OrderQty", subject, default=None)
        if order_qty is None:
            raise AcumaticaError(f"{subject} returned a malformed line")
        lines.append(
            {
                "item_code": item_code,
                "warehouse_code": _po_text(line, "WarehouseID", subject),
                "order_qty": order_qty,
                "received_qty": _po_number(line, "QtyOnReceipts", subject, default=0.0),
                "completed": _po_bool(line, "Completed", subject),
                "cancelled": _po_bool(line, "Cancelled", subject),
                "promised": _erp_date(_entity_value(line, "Promised"), subject),
            }
        )
    return {
        "order_nbr": order_nbr,
        "status": _po_text(row, "Status", subject),
        "hold": _po_bool(row, "Hold", subject),
        "date": _erp_date(_entity_value(row, "Date"), subject),
        "promised_on": _erp_date(_entity_value(row, "PromisedOn"), subject),
        "total": _po_number(row, "OrderTotal", subject, default=None),
        "lines": lines,
    }


def _body_snippet(body: str, config: _AcumaticaConfig) -> str:
    """Return at most 300 characters of a response body, secrets removed."""
    snippet = body[:_MAX_BODY_SNIPPET]
    for secret in (config.password, config.username):
        if secret:
            snippet = snippet.replace(secret, "[redacted]")
    return snippet


def _entity_field(row: dict[str, Any], name: str) -> str | None:
    """Return one required entity field as a plain string, else None.

    A field is usable when it is a plain non-empty string, or an object
    whose ``"value"`` is a plain non-empty string (the Acumatica entity
    envelope); either way the plain string is returned. A missing,
    null, empty, or otherwise typed field returns None so the caller
    raises the explicit malformed error.
    """
    value = row.get(name)
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        inner = value.get("value")
        if isinstance(inner, str):
            return inner or None
    return None


class AcumaticaClient:
    """Fail-closed client for one Acumatica instance.

    Every public call reads the environment at call time, opens one
    server session (login ... logout in a ``finally``), and performs only
    the reads and, on the write path, the writes described below (at most
    one per order on the batch paths).
    The per-call ``httpx.Client`` comes from the module-level
    :func:`_build_client` — which tests replace — and is always closed,
    even after an error. The client performs no automatic retries and
    never follows redirects.
    """

    # -- configuration -----------------------------------------------------

    def _load_config(self) -> _AcumaticaConfig:
        """Read and validate the environment at call time.

        Raises ``AcumaticaConfigError`` before any request when a value
        is missing or malformed; the message names the variable, never
        its value.
        """
        base_url = _require_env(BASE_URL_VAR)
        origin = _validate_base_url(base_url, _allow_insecure())
        tenant = _require_env(TENANT_VAR)
        username = _require_env(USERNAME_VAR)
        password = _require_env(PASSWORD_VAR)
        version = _endpoint_version()
        ca_bundle = _ca_bundle_path()
        _ensure_ca_bundle_readable(ca_bundle)
        timeout = _request_timeout()
        return _AcumaticaConfig(
            origin, tenant, username, password, version, ca_bundle, timeout
        )

    # -- session -----------------------------------------------------------

    @contextmanager
    def _session(self, config: _AcumaticaConfig) -> Iterator[httpx.Client]:
        """Open one server session; yield the per-call client.

        ``POST {origin}/entity/auth/login`` with ``{"name", "password",
        "tenant"}`` first: any 2xx is success, 401 or 403 raises
        ``AcumaticaAuthError``, any other non-2xx raises
        ``AcumaticaError`` with the status, and a transport failure
        raises ``AcumaticaError`` — in every login failure the error
        propagates and no logout is sent. When the login succeeds, the
        call always ends with ``POST {origin}/entity/auth/logout`` in a
        ``finally``, including after an error in the call's body; the
        logout is best-effort and never replaces or masks the primary
        error. The client is always closed.
        """
        client = _build_client(config.ca_bundle, config.timeout)
        logged_in = False
        try:
            try:
                response = client.post(
                    config.origin + _LOGIN_PATH,
                    json={
                        "name": config.username,
                        "password": config.password,
                        "tenant": config.tenant,
                    },
                )
            except httpx.HTTPError as exc:
                raise AcumaticaError(
                    f"the Acumatica login failed with {type(exc).__name__}"
                ) from exc
            status = response.status_code
            if status in (401, 403):
                raise AcumaticaAuthError(
                    f"the Acumatica login was rejected with HTTP {status}"
                )
            if not 200 <= status < 300:
                raise AcumaticaError(f"the Acumatica login failed with HTTP {status}")
            logged_in = True
            yield client
        finally:
            if logged_in:
                try:
                    client.post(config.origin + _LOGOUT_PATH, json={})
                except Exception:  # best effort: never mask the primary error
                    logger.debug(
                        "Acumatica logout failed; ignored (best effort)",
                        exc_info=True,
                    )
            client.close()

    # -- HTTP helpers ------------------------------------------------------

    @staticmethod
    def _check_status(response: httpx.Response, subject: str) -> None:
        status = response.status_code
        if status in (401, 403):
            raise AcumaticaAuthError(f"{subject} was rejected with HTTP {status}")
        if not 200 <= status < 300:
            raise AcumaticaError(f"{subject} failed with HTTP {status}")

    @staticmethod
    def _parse_json(response: httpx.Response, subject: str) -> Any:
        try:
            return response.json()
        except (ValueError, httpx.HTTPError):
            raise AcumaticaError(f"{subject} returned malformed JSON") from None

    def _fetch_odata(
        self,
        client: httpx.Client,
        config: _AcumaticaConfig,
        entity: str,
        params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """GET one OData entity set with HTTP Basic auth; return its rows.

        The tenant is percent-encoded into the path and every query
        value goes through httpx params, so no configuration or plan
        value reaches the wire unencoded. A transport error, a 401/403
        (``AcumaticaAuthError``), any other non-2xx, or a malformed 2xx
        body raises an explicit client error.
        """
        url = f"{config.origin}/odatav4/{quote(config.tenant, safe='')}/{entity}"
        subject = f"the Acumatica {entity} read"
        try:
            response = client.get(
                url, params=params, auth=(config.username, config.password)
            )
        except httpx.HTTPError as exc:
            raise AcumaticaError(f"{subject} failed with {type(exc).__name__}") from exc
        self._check_status(response, subject)
        data = self._parse_json(response, subject)
        if not isinstance(data, dict) or not isinstance(data.get("value"), list):
            raise AcumaticaError(f"{subject} returned a malformed response")
        return [row for row in data["value"] if isinstance(row, dict)]

    def _find_duplicate_order(
        self,
        client: httpx.Client,
        config: _AcumaticaConfig,
        origin: str,
    ) -> dict[str, str] | None:
        """Search for an existing order whose ``Description`` is ``origin``.

        ``GET {origin}/entity/Default/{version}/PurchaseOrder`` with the
        single quotes of the origin doubled inside the OData string
        literal and every query value encoded via httpx params. A 2xx
        body is the list of rows: either a bare JSON array or an object
        whose ``"value"`` is an array, and every other shape is
        malformed; extra envelope metadata on a row is ignored. Each
        row's ``OrderNbr`` and ``Status`` is a plain non-empty string,
        or an object whose ``"value"`` is a plain non-empty string, and
        is returned as a plain string. No match yields None; exactly
        one match yields ``{"order_nbr", "status"}``; multiple matches
        (ambiguity), a malformed 2xx body, a malformed order row, a
        401/403, or any other non-2xx raise an explicit client error
        rather than being treated as a successful write.
        """
        url = (
            f"{config.origin}/entity/Default/"
            f"{quote(config.version, safe='')}/PurchaseOrder"
        )
        subject = "the Acumatica PurchaseOrder duplicate search"
        params = {
            "$filter": f"Description eq '{_odata_escape(origin)}'",
            "$select": "OrderNbr,Status",
        }
        try:
            response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise AcumaticaError(f"{subject} failed with {type(exc).__name__}") from exc
        self._check_status(response, subject)
        data = self._parse_json(response, subject)
        if isinstance(data, dict) and isinstance(data.get("value"), list):
            rows = list(data["value"])
        elif isinstance(data, list):
            rows = list(data)
        else:
            raise AcumaticaError(f"{subject} returned a malformed response")
        if any(not isinstance(row, dict) for row in rows):
            raise AcumaticaError(f"{subject} returned a malformed response")
        if len(rows) > 1:
            raise AcumaticaError(
                f"{subject} is ambiguous: {len(rows)} orders match the origin"
            )
        if not rows:
            return None
        order_nbr = _entity_field(rows[0], "OrderNbr")
        order_status = _entity_field(rows[0], "Status")
        if order_nbr is None or order_status is None:
            raise AcumaticaError(f"{subject} returned a malformed order")
        return {"order_nbr": order_nbr, "status": order_status}

    @staticmethod
    def _draft_po_payload(
        *,
        site_code: str,
        vendor_code: str,
        origin: str,
        lines: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the exact ``PurchaseOrder`` payload a write would send."""
        return {
            "Type": {"value": "Normal"},
            "VendorID": {"value": vendor_code},
            "Hold": {"value": True},
            "Description": {"value": origin},
            "Details": [
                {
                    "InventoryID": {"value": line["item_code"]},
                    "WarehouseID": {"value": site_code},
                    "OrderQty": {"value": line["quantity"]},
                    "UnitCost": {"value": line["unit_cost"]},
                }
                for line in lines
            ],
        }

    def _create_order(
        self,
        client: httpx.Client,
        config: _AcumaticaConfig,
        payload: dict[str, Any],
    ) -> dict[str, str]:
        """PUT one ON-HOLD ``PurchaseOrder``; return its nbr and status.

        ``PUT {origin}/entity/Default/{version}/PurchaseOrder?$expand=Details``.
        A transport error, a 401/403 (``AcumaticaAuthError``), or any
        other non-2xx raises ``AcumaticaError`` with the status and at
        most 300 characters of the body — never the password; a 2xx
        body that is not an order object whose ``OrderNbr`` and
        ``Status`` are each a plain non-empty string, or an object
        whose ``"value"`` is a plain non-empty string (returned as
        plain strings, extra envelope metadata ignored), raises the
        same explicit error rather than being treated as a successful
        write.
        """
        url = (
            f"{config.origin}/entity/Default/"
            f"{quote(config.version, safe='')}/PurchaseOrder"
        )
        subject = "the Acumatica PurchaseOrder write"
        try:
            response = client.put(url, params={"$expand": "Details"}, json=payload)
        except httpx.HTTPError as exc:
            raise AcumaticaError(f"{subject} failed with {type(exc).__name__}") from exc
        status = response.status_code
        if status in (401, 403):
            raise AcumaticaAuthError(f"{subject} was rejected with HTTP {status}")
        if not 200 <= status < 300:
            raise AcumaticaError(
                f"{subject} failed with HTTP {status}: "
                f"{_body_snippet(response.text, config)}"
            )
        data = self._parse_json(response, subject)
        if not isinstance(data, dict):
            raise AcumaticaError(f"{subject} returned a malformed response")
        order_nbr = _entity_field(data, "OrderNbr")
        order_status = _entity_field(data, "Status")
        if order_nbr is None or order_status is None:
            raise AcumaticaError(f"{subject} returned a malformed response")
        return {"order_nbr": order_nbr, "status": order_status}

    # -- code lookup -------------------------------------------------------

    @staticmethod
    def _code_maps(
        site_rows: list[dict[str, Any]],
        vendor_rows: list[dict[str, Any]],
        site_ids: list[str],
        vendor_ids: list[str],
    ) -> dict[str, dict[str, str]]:
        """Map the requested ids to their stripped codes; absent = no match."""
        sites: dict[str, str] = {}
        for row in site_rows:
            row_id = _row_id(row.get("SiteID"))
            if row_id is None:
                continue
            code = row.get("SiteCD")
            if not isinstance(code, str) or not code.strip():
                continue
            sites.setdefault(row_id, code.strip())
        vendors: dict[str, str] = {}
        for row in vendor_rows:
            row_id = _row_id(row.get("BAccountID"))
            if row_id is None:
                continue
            account = row.get("AcctCD")
            account_type = row.get("Type")
            if not isinstance(account, str) or not account.strip():
                continue
            if not isinstance(account_type, str) or account_type.strip() not in (
                "VE",
                "VC",
            ):
                continue
            vendors.setdefault(row_id, account.strip())
        return {
            "sites": {
                site_id: sites[site_id] for site_id in site_ids if site_id in sites
            },
            "vendors": {
                vendor_id: vendors[vendor_id]
                for vendor_id in vendor_ids
                if vendor_id in vendors
            },
        }

    def _fetch_codes(
        self,
        client: httpx.Client,
        config: _AcumaticaConfig,
        site_ids: list[str],
        vendor_ids: list[str],
    ) -> dict[str, dict[str, str]]:
        site_rows = self._fetch_odata(
            client, config, "INSite", {"$select": "SiteID,SiteCD"}
        )
        vendor_rows = self._fetch_odata(
            client, config, "BAccount", {"$select": "BAccountID,AcctCD,Type"}
        )
        return self._code_maps(site_rows, vendor_rows, site_ids, vendor_ids)

    def resolve_codes(
        self, site_ids: list, vendor_ids: list
    ) -> dict[str, dict[str, str]]:
        """Resolve the requested site and vendor ids to their codes.

        Opens one session and reads the two OData entity sets with HTTP
        Basic auth (``INSite`` for site ids, ``BAccount`` for vendor
        ids). Returns ``{"sites": {id: SiteCD}, "vendors": {id: AcctCD}}``
        for the requested ids only, with codes stripped of surrounding
        whitespace and only ``BAccount`` rows of ``Type`` ``VE`` or
        ``VC`` treated as vendors; an id with no match is simply absent.
        Invalid ids raise ``ValueError`` before any request.
        """
        sites = _validate_id_list(site_ids, "site_ids")
        vendors = _validate_id_list(vendor_ids, "vendor_ids")
        config = self._load_config()
        with self._session(config) as client:
            return self._fetch_codes(client, config, sites, vendors)

    # -- draft purchase order ----------------------------------------------

    def create_draft_purchase_order(
        self,
        *,
        site_id: object,
        vendor_id: object,
        origin: str,
        lines: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create one ON-HOLD draft ``PurchaseOrder`` in Acumatica.

        Fails closed on writes: the first action, before any network
        call, is the explicit write switch — writes are enabled only
        when ``ACUMATICA_ALLOW_WRITES`` is truthy (``1``, ``true``,
        ``yes``, or ``on``, case-insensitive, whitespace-trimmed);
        anything else raises ``AcumaticaWritesDisabledError`` and no
        request of any kind is made. Inputs are then validated with no
        network: ``site_id`` and ``vendor_id`` must be positive integer
        ids, ``origin`` a non-empty string of at most 255 characters,
        and ``lines`` a list of ``{item_code, quantity, unit_cost}``
        dicts with every quantity a positive finite number and every
        unit cost a finite number >= 0 (bools are not numbers), else
        ``ValueError`` before any request.

        One session then performs, in order: the INSite and BAccount
        code lookups (a missing code raises ``AcumaticaError`` naming
        the id), the duplicate search on the origin tag, and — only
        when no order carries the tag — the write. The result is
        ``{"order_nbr", "status", "already_existed", "warehouse_code",
        "vendor_code", "line_count"}``; an existing order is returned
        as-is with ``already_existed`` True and nothing is created.
        """
        if not _writes_enabled():
            raise AcumaticaWritesDisabledError(
                "Acumatica writes are disabled; set ACUMATICA_ALLOW_WRITES "
                "to a truthy value to enable purchase order creation"
            )
        site, vendor, origin_text, validated = _validate_draft_po_args(
            site_id, vendor_id, origin, lines
        )
        config = self._load_config()
        with self._session(config) as client:
            codes = self._fetch_codes(client, config, [site], [vendor])
            site_code = codes["sites"].get(site)
            if site_code is None:
                raise AcumaticaError(f"no INSite site matches site id {site}")
            vendor_code = codes["vendors"].get(vendor)
            if vendor_code is None:
                raise AcumaticaError(
                    f"no BAccount vendor of type VE or VC matches vendor id {vendor}"
                )
            payload = self._draft_po_payload(
                site_code=site_code,
                vendor_code=vendor_code,
                origin=origin_text,
                lines=validated,
            )
            existing = self._find_duplicate_order(client, config, origin_text)
            if existing is not None:
                return {
                    "order_nbr": existing["order_nbr"],
                    "status": existing["status"],
                    "already_existed": True,
                    "warehouse_code": site_code,
                    "vendor_code": vendor_code,
                    "line_count": len(validated),
                }
            created = self._create_order(client, config, payload)
        return {
            "order_nbr": created["order_nbr"],
            "status": created["status"],
            "already_existed": False,
            "warehouse_code": site_code,
            "vendor_code": vendor_code,
            "line_count": len(validated),
        }

    def preview_draft_purchase_order(
        self,
        *,
        site_id: object,
        vendor_id: object,
        origin: str,
        lines: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Preview the draft-PO resolution and duplicate search, read-only.

        Takes the same arguments as :meth:`create_draft_purchase_order`
        (with the same ``ValueError`` validation) and does not consult
        the write switch. One session performs the login, the INSite
        and BAccount code lookups, and the duplicate search — never a
        write — and returns ``{"payload", "already_existed"}``: the
        exact payload a write would send and whether an order with that
        origin already exists. A missing code raises ``AcumaticaError``
        naming the id, as on the write path.
        """
        site, vendor, origin_text, validated = _validate_draft_po_args(
            site_id, vendor_id, origin, lines
        )
        config = self._load_config()
        with self._session(config) as client:
            codes = self._fetch_codes(client, config, [site], [vendor])
            site_code = codes["sites"].get(site)
            if site_code is None:
                raise AcumaticaError(f"no INSite site matches site id {site}")
            vendor_code = codes["vendors"].get(vendor)
            if vendor_code is None:
                raise AcumaticaError(
                    f"no BAccount vendor of type VE or VC matches vendor id {vendor}"
                )
            existing = self._find_duplicate_order(client, config, origin_text)
        return {
            "payload": self._draft_po_payload(
                site_code=site_code,
                vendor_code=vendor_code,
                origin=origin_text,
                lines=validated,
            ),
            "already_existed": existing is not None,
        }

    # -- draft purchase order batch ----------------------------------------

    def _run_draft_po_batch(
        self,
        entries: list[tuple[str, str, str, list[dict[str, Any]]]],
        *,
        write: bool,
    ) -> list[dict[str, Any]]:
        """Run one validated batch in a single session; one result per order.

        One session performs, in order: the code lookups — one
        ``resolve_codes`` pass over the union of the entries' site and
        vendor ids (a login or code-lookup failure raises for the whole
        batch, since nothing can proceed without them) — then, per
        order in input order: the missing-code checks, the duplicate
        search on the origin tag, and — on the write path only — the
        write. An order whose site or vendor code is missing, whose
        duplicate search is ambiguous or malformed, or whose write
        fails gets an ``error`` result carrying a message with no
        secrets, and the batch continues with the next order; a 401 or
        403 anywhere in the session raises ``AcumaticaAuthError`` for
        the whole batch, and the logout in the ``finally`` is still
        sent.
        """
        config = self._load_config()
        site_ids = list(dict.fromkeys(site for site, _, _, _ in entries))
        vendor_ids = list(dict.fromkeys(vendor for _, vendor, _, _ in entries))
        results: list[dict[str, Any]] = []
        with self._session(config) as client:
            codes = self._fetch_codes(client, config, site_ids, vendor_ids)
            for site, vendor, origin_text, lines in entries:
                site_code = codes["sites"].get(site)
                vendor_code = codes["vendors"].get(vendor)
                result: dict[str, Any] = {
                    "origin": origin_text,
                    "outcome": "error",
                    "order_nbr": None,
                    "status": None,
                    "warehouse_code": site_code,
                    "vendor_code": vendor_code,
                    "line_count": len(lines),
                    "error": None,
                }
                try:
                    if site_code is None:
                        raise AcumaticaError(f"no INSite site matches site id {site}")
                    if vendor_code is None:
                        raise AcumaticaError(
                            f"no BAccount vendor of type VE or VC matches "
                            f"vendor id {vendor}"
                        )
                    existing = self._find_duplicate_order(client, config, origin_text)
                    if existing is not None:
                        result["outcome"] = "exists"
                        result["order_nbr"] = existing["order_nbr"]
                        result["status"] = existing["status"]
                    elif write:
                        payload = self._draft_po_payload(
                            site_code=site_code,
                            vendor_code=vendor_code,
                            origin=origin_text,
                            lines=lines,
                        )
                        created = self._create_order(client, config, payload)
                        result["outcome"] = "created"
                        result["order_nbr"] = created["order_nbr"]
                        result["status"] = created["status"]
                    else:
                        result["outcome"] = "would_create"
                        result["payload"] = self._draft_po_payload(
                            site_code=site_code,
                            vendor_code=vendor_code,
                            origin=origin_text,
                            lines=lines,
                        )
                except AcumaticaAuthError:
                    raise  # batch-level: nothing further can proceed
                except AcumaticaError as exc:
                    result["outcome"] = "error"
                    result["error"] = str(exc)
                results.append(result)
        return results

    def preview_draft_purchase_orders(
        self, orders: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Preview a whole batch of draft-PO resolutions, read-only.

        ``orders`` is a list of ``{site_id, vendor_id, origin, lines}``
        entries, each validated exactly as
        :meth:`create_draft_purchase_order` validates its arguments —
        all validation happens before any request, an invalid entry
        raises ``ValueError`` naming its index, two entries with the
        same origin raise ``ValueError``, and an empty list returns
        ``[]`` with no request. One session (login, then logout in a
        best-effort ``finally``) resolves the codes for all entries in
        one ``resolve_codes`` pass over the union of their site and
        vendor ids, then runs the duplicate search per order in input
        order — never a write, and the write switch is not consulted.
        The result is one dict per order, in input order: ``{origin,
        outcome, order_nbr, status, warehouse_code, vendor_code,
        line_count, error}`` with ``outcome`` ``would_create`` (plus
        the would-be ``payload``), ``exists``, or ``error`` — a
        secret-free message when that order's site or vendor code is
        missing or its search is ambiguous or malformed — with the
        batch continuing past such an order. A login or code-lookup
        failure, or a 401/403 anywhere in the session, raises for the
        whole batch.
        """
        entries = _validate_draft_po_orders(orders)
        if not entries:
            return []
        return self._run_draft_po_batch(entries, write=False)

    def create_draft_purchase_orders(
        self, orders: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Create a batch of ON-HOLD draft ``PurchaseOrder``s in Acumatica.

        Fails closed on writes: the first action, before any network
        call, is the explicit write switch — anything but a truthy
        ``ACUMATICA_ALLOW_WRITES`` raises
        ``AcumaticaWritesDisabledError`` and no request of any kind is
        made. ``orders`` is a list of ``{site_id, vendor_id, origin,
        lines}`` entries, each validated exactly as
        :meth:`create_draft_purchase_order` validates its arguments —
        all validation happens before any request, an invalid entry
        raises ``ValueError`` naming its index, two entries with the
        same origin raise ``ValueError``, and an empty list returns
        ``[]`` with no request. One session (login, then logout in a
        best-effort ``finally``) resolves the codes for all entries in
        one ``resolve_codes`` pass over the union of their site and
        vendor ids, then — per order, in input order — runs the
        duplicate search on the origin tag and, only when no order
        carries the tag, sends the write. The result is one dict per
        order, in input order: ``{origin, outcome, order_nbr, status,
        warehouse_code, vendor_code, line_count, error}`` with
        ``outcome`` ``created``, ``exists`` (an order with that origin
        is returned as-is, nothing is created), or ``error`` — a
        secret-free message when that order's site or vendor code is
        missing, its search is ambiguous or malformed, or its write
        fails — with the batch continuing past such an order. A login
        or code-lookup failure, or a 401/403 anywhere in the session,
        raises for the whole batch.
        """
        if not _writes_enabled():
            raise AcumaticaWritesDisabledError(
                "Acumatica writes are disabled; set ACUMATICA_ALLOW_WRITES "
                "to a truthy value to enable purchase order creation"
            )
        entries = _validate_draft_po_orders(orders)
        if not entries:
            return []
        return self._run_draft_po_batch(entries, write=True)

    # -- transfer order batch ----------------------------------------------

    @staticmethod
    def _transfer_order_payload(
        *,
        from_code: str,
        to_code: str,
        origin: str,
        lines: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the exact ``TransferOrder`` payload a write would send."""
        return {
            "TransferType": {"value": "1-Step"},
            "FromWarehouseID": {"value": from_code},
            "ToWarehouseID": {"value": to_code},
            "Hold": {"value": True},
            "Description": {"value": origin},
            "Details": [
                {
                    "InventoryID": {"value": line["item_code"]},
                    "Qty": {"value": line["quantity"]},
                }
                for line in lines
            ],
        }

    def _find_duplicate_transfer_order(
        self,
        client: httpx.Client,
        config: _AcumaticaConfig,
        origin: str,
    ) -> dict[str, str] | None:
        """Search for an existing transfer whose ``Description`` is ``origin``.

        ``GET {origin}/entity/Default/{version}/TransferOrder`` with
        the single quotes of the origin doubled inside the OData
        string literal and every query value encoded via httpx
        params. A 2xx body is the list of rows: either a bare JSON
        array or an object whose ``"value"`` is an array, and every
        other shape is malformed; extra envelope metadata on a row is
        ignored. Each row's ``ReferenceNbr`` and ``Status`` is a
        plain non-empty string, or an object whose ``"value"`` is a
        plain non-empty string, and is returned as a plain string. No
        match yields None; exactly one match yields ``{"reference_nbr",
        "status"}``; multiple matches (ambiguity), a malformed 2xx
        body, a malformed order row, a 401/403, or any other non-2xx
        raise an explicit client error rather than being treated as a
        successful write.
        """
        url = (
            f"{config.origin}/entity/Default/"
            f"{quote(config.version, safe='')}/TransferOrder"
        )
        subject = "the Acumatica TransferOrder duplicate search"
        params = {
            "$filter": f"Description eq '{_odata_escape(origin)}'",
            "$select": "ReferenceNbr,Status",
        }
        try:
            response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise AcumaticaError(f"{subject} failed with {type(exc).__name__}") from exc
        self._check_status(response, subject)
        data = self._parse_json(response, subject)
        if isinstance(data, dict) and isinstance(data.get("value"), list):
            rows = list(data["value"])
        elif isinstance(data, list):
            rows = list(data)
        else:
            raise AcumaticaError(f"{subject} returned a malformed response")
        if any(not isinstance(row, dict) for row in rows):
            raise AcumaticaError(f"{subject} returned a malformed response")
        if len(rows) > 1:
            raise AcumaticaError(
                f"{subject} is ambiguous: {len(rows)} orders match the origin"
            )
        if not rows:
            return None
        reference_nbr = _entity_field(rows[0], "ReferenceNbr")
        order_status = _entity_field(rows[0], "Status")
        if reference_nbr is None or order_status is None:
            raise AcumaticaError(f"{subject} returned a malformed order")
        return {"reference_nbr": reference_nbr, "status": order_status}

    def _create_transfer_order(
        self,
        client: httpx.Client,
        config: _AcumaticaConfig,
        payload: dict[str, Any],
    ) -> dict[str, str]:
        """PUT one ON-HOLD ``TransferOrder``; return its nbr and status.

        ``PUT {origin}/entity/Default/{version}/TransferOrder?$expand=
        Details``. A transport error, a 401/403
        (``AcumaticaAuthError``), or any other non-2xx raises
        ``AcumaticaError`` with the status and at most 300 characters
        of the body — never the password or a session cookie; a 2xx
        body that is not an order object whose ``ReferenceNbr`` and
        ``Status`` are each a plain non-empty string, or an object
        whose ``"value"`` is a plain non-empty string (returned as
        plain strings, extra envelope metadata ignored), raises the
        same explicit error rather than being treated as a successful
        write.
        """
        url = (
            f"{config.origin}/entity/Default/"
            f"{quote(config.version, safe='')}/TransferOrder"
        )
        subject = "the Acumatica TransferOrder write"
        try:
            response = client.put(url, params={"$expand": "Details"}, json=payload)
        except httpx.HTTPError as exc:
            raise AcumaticaError(f"{subject} failed with {type(exc).__name__}") from exc
        status = response.status_code
        if status in (401, 403):
            raise AcumaticaAuthError(f"{subject} was rejected with HTTP {status}")
        if not 200 <= status < 300:
            raise AcumaticaError(
                f"{subject} failed with HTTP {status}: "
                f"{_body_snippet(response.text, config)}"
            )
        data = self._parse_json(response, subject)
        if not isinstance(data, dict):
            raise AcumaticaError(f"{subject} returned a malformed response")
        reference_nbr = _entity_field(data, "ReferenceNbr")
        order_status = _entity_field(data, "Status")
        if reference_nbr is None or order_status is None:
            raise AcumaticaError(f"{subject} returned a malformed response")
        return {"reference_nbr": reference_nbr, "status": order_status}

    def _run_transfer_batch(
        self,
        entries: list[tuple[str, str, str, list[dict[str, Any]]]],
        *,
        write: bool,
    ) -> list[dict[str, Any]]:
        """Run one validated batch in a single session; one result per entry.

        One session performs, in order: the code lookups — one
        ``INSite`` pass over the union of the entries' from and to
        site ids (a login or code-lookup failure raises for the whole
        batch, since nothing can proceed without them) — then, per
        entry in input order: the missing-code checks, the duplicate
        search on the origin tag, and — on the write path only — the
        write. An entry whose site has no warehouse code, whose
        duplicate search is ambiguous or malformed, or whose write
        fails gets an ``error`` result naming the operation mode, the
        failing entry, and a secret-free reason, and the batch
        continues with the next entry; a 401 or 403 anywhere in the
        session raises ``AcumaticaAuthError`` for the whole batch,
        and the logout in the ``finally`` is still sent.
        """
        mode = "create" if write else "preview"
        config = self._load_config()
        site_ids = list(
            dict.fromkeys(
                site
                for from_site, to_site, _, _ in entries
                for site in (from_site, to_site)
            )
        )
        results: list[dict[str, Any]] = []
        with self._session(config) as client:
            site_rows = self._fetch_odata(
                client, config, "INSite", {"$select": "SiteID,SiteCD"}
            )
            codes = self._code_maps(site_rows, [], site_ids, [])
            for index, (from_site, to_site, origin_text, lines) in enumerate(entries):
                from_code = codes["sites"].get(from_site)
                to_code = codes["sites"].get(to_site)
                result: dict[str, Any] = {
                    "origin": origin_text,
                    "outcome": "error",
                    "reference_nbr": None,
                    "status": None,
                    "from_warehouse_code": from_code,
                    "to_warehouse_code": to_code,
                    "line_count": len(lines),
                    "error": None,
                }
                try:
                    if from_code is None:
                        raise AcumaticaError(
                            f"no INSite site matches site id {from_site}"
                        )
                    if to_code is None:
                        raise AcumaticaError(
                            f"no INSite site matches site id {to_site}"
                        )
                    existing = self._find_duplicate_transfer_order(
                        client, config, origin_text
                    )
                    if existing is not None:
                        result["outcome"] = "exists"
                        result["reference_nbr"] = existing["reference_nbr"]
                        result["status"] = existing["status"]
                    elif write:
                        payload = self._transfer_order_payload(
                            from_code=from_code,
                            to_code=to_code,
                            origin=origin_text,
                            lines=lines,
                        )
                        created = self._create_transfer_order(client, config, payload)
                        result["outcome"] = "created"
                        result["reference_nbr"] = created["reference_nbr"]
                        result["status"] = created["status"]
                    else:
                        result["outcome"] = "would_create"
                except AcumaticaAuthError:
                    raise  # batch-level: nothing further can proceed
                except AcumaticaError as exc:
                    result["outcome"] = "error"
                    result["error"] = (
                        f"transfer order {mode} for transfers[{index}] "
                        f"(origin {origin_text!r}): {exc}"
                    )
                results.append(result)
        return results

    def preview_transfer_orders(
        self, transfers: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Preview a whole batch of transfer orders, read-only.

        ``transfers`` is a list of ``{from_site_id, to_site_id,
        origin, lines}`` entries, each validated before any request —
        the two site ids are positive integer ids (the
        ``_normalize_id`` rules) that must differ from each other,
        ``origin`` a non-empty string of at most 255 characters, and
        ``lines`` a non-empty list of ``{item_code, quantity}`` with
        every quantity a whole number above 0 — an invalid entry
        raises ``ValueError`` naming its index, two entries with the
        same origin raise ``ValueError``, and an empty list returns
        ``[]`` with no request. One session (login, then logout in a
        best-effort ``finally``) resolves the warehouse codes for the
        union of the entries' site ids in one code-lookup pass, then
        runs the duplicate search per entry in input order — never a
        write, and the write switch is not consulted. The result is
        one dict per entry, in input order: ``{origin, outcome,
        reference_nbr, status, from_warehouse_code,
        to_warehouse_code, line_count, error}`` with ``outcome``
        ``would_create``, ``exists`` (an order with that origin is
        returned as-is), or ``error`` — an entry-naming, secret-free
        message when a site has no warehouse code or the search is
        ambiguous or malformed — with the batch continuing past such
        an entry. A login or code-lookup failure, or a 401/403
        anywhere in the session, raises for the whole batch.
        """
        entries = _validate_transfer_orders(transfers)
        if not entries:
            return []
        return self._run_transfer_batch(entries, write=False)

    def create_transfer_orders(
        self, transfers: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Create a batch of ON-HOLD 1-Step ``TransferOrder``s in Acumatica.

        Fails closed on writes: the first action, before any network
        call, is the explicit write switch — anything but a truthy
        ``ACUMATICA_ALLOW_WRITES`` raises
        ``AcumaticaWritesDisabledError`` and no request of any kind is
        made, ahead of the entry validation. ``transfers`` is a list
        of ``{from_site_id, to_site_id, origin, lines}`` entries, each
        validated before any request — the two site ids are positive
        integer ids (the ``_normalize_id`` rules) that must differ
        from each other, ``origin`` a non-empty string of at most 255
        characters, and ``lines`` a non-empty list of ``{item_code,
        quantity}`` with every quantity a whole number above 0 — an
        invalid entry raises ``ValueError`` naming its index, two
        entries with the same origin raise ``ValueError``, and an
        empty list returns ``[]`` with no request. One session (login,
        then logout in a best-effort ``finally``) resolves the
        warehouse codes for the union of the entries' site ids in one
        code-lookup pass, then — per entry, in input order — runs the
        duplicate search on the origin tag and, only when no order
        carries the tag, sends the write. The result is one dict per
        entry, in input order: ``{origin, outcome, reference_nbr,
        status, from_warehouse_code, to_warehouse_code, line_count,
        error}`` with ``outcome`` ``created``, ``exists`` (an order
        with that origin is returned as-is, nothing is created), or
        ``error`` — an entry-naming, secret-free message when a site
        has no warehouse code, the search is ambiguous or malformed,
        or the write fails — with the batch continuing past such an
        entry. A login or code-lookup failure, or a 401/403 anywhere
        in the session, raises for the whole batch.
        """
        if not _writes_enabled():
            raise AcumaticaWritesDisabledError(
                "Acumatica writes are disabled; set ACUMATICA_ALLOW_WRITES "
                "to a truthy value to enable transfer order creation"
            )
        entries = _validate_transfer_orders(transfers)
        if not entries:
            return []
        return self._run_transfer_batch(entries, write=True)

    # -- item warehouse parameters -----------------------------------------

    def _fetch_item_warehouse_row(
        self,
        client: httpx.Client,
        config: _AcumaticaConfig,
        item_code: str,
        site_code: str,
    ) -> dict[str, Any]:
        """GET one item-warehouse row; raise when no row matches.

        One ``GET {origin}/entity/Default/{version}/ItemWarehouse`` —
        the REST contract endpoint the PurchaseOrder paths build with
        ``_endpoint_version()`` (never the read-only ``odatav4``
        feed) — filtered on the item and the site (single quotes
        doubled the way ``_odata_escape`` does) with ``$top=1``. A
        transport error, a 401/403 (``AcumaticaAuthError``), any other
        non-2xx, a malformed 2xx body, or an answer without the row
        raises the explicit client error naming the item and the site.
        """
        url = (
            f"{config.origin}/entity/Default/"
            f"{quote(config.version, safe='')}/ItemWarehouse"
        )
        subject = "the Acumatica ItemWarehouse read"
        params = {
            "$filter": (
                f"InventoryID eq '{_odata_escape(item_code)}' "
                f"and WarehouseID eq '{_odata_escape(site_code)}'"
            ),
            "$top": "1",
        }
        try:
            response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise AcumaticaError(f"{subject} failed with {type(exc).__name__}") from exc
        self._check_status(response, subject)
        data = self._parse_json(response, subject)
        if isinstance(data, dict) and isinstance(data.get("value"), list):
            rows = list(data["value"])
        elif isinstance(data, list):
            rows = list(data)
        else:
            raise AcumaticaError(f"{subject} returned a malformed response")
        if any(not isinstance(row, dict) for row in rows):
            raise AcumaticaError(f"{subject} returned a malformed response")
        if not rows:
            raise AcumaticaError(
                f"no ItemWarehouse row matches item {item_code} at site {site_code}"
            )
        return rows[0]

    @staticmethod
    def _put_item_warehouse_row(
        client: httpx.Client,
        config: _AcumaticaConfig,
        payload: dict[str, Any],
    ) -> None:
        """PUT one item-warehouse payload to the REST contract endpoint.

        ``PUT {origin}/entity/Default/{version}/ItemWarehouse`` with
        every value wrapped as ``{"value": ...}``. A transport error,
        a 401/403 (``AcumaticaAuthError``), or any other non-2xx
        raises the way the existing write paths handle it — the
        status and at most 300 characters of the body, never the
        password — and the 2xx body is not parsed: the caller verifies
        the write by reading the row back.
        """
        url = (
            f"{config.origin}/entity/Default/"
            f"{quote(config.version, safe='')}/ItemWarehouse"
        )
        subject = "the Acumatica ItemWarehouse write"
        try:
            response = client.put(url, json=payload)
        except httpx.HTTPError as exc:
            raise AcumaticaError(f"{subject} failed with {type(exc).__name__}") from exc
        status = response.status_code
        if status in (401, 403):
            raise AcumaticaAuthError(f"{subject} was rejected with HTTP {status}")
        if not 200 <= status < 300:
            raise AcumaticaError(
                f"{subject} failed with HTTP {status}: "
                f"{_body_snippet(response.text, config)}"
            )

    @staticmethod
    def _item_warehouse_state(row: dict[str, Any], subject: str) -> dict[str, Any]:
        """The row's replenishment values and override flags.

        ``ReorderPoint`` and ``MaxQty`` are finite numbers — a null is
        0.0 for the ``before`` state and the unchanged comparison,
        regardless of the override flag state — and the override flags
        are booleans, a null being False; a non-numeric or
        non-boolean field raises the explicit client error.
        """

        def quantity(name: str) -> float:
            value = _entity_value(row, name)
            if value is None:
                return 0.0
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AcumaticaError(f"{subject} returned a malformed field {name}")
            if not math.isfinite(value):
                raise AcumaticaError(f"{subject} returned a malformed field {name}")
            return float(value)

        def flag(name: str) -> bool:
            value = _entity_value(row, name)
            if value is None:
                return False
            if not isinstance(value, bool):
                raise AcumaticaError(f"{subject} returned a malformed field {name}")
            return value

        return {
            "reorder_point": quantity("ReorderPoint"),
            "max_qty": quantity("MaxQty"),
            "override_reorder_point": flag("OverrideReorderPoint"),
            "override_max_qty": flag("OverrideMaxQty"),
        }

    def write_item_warehouse_parameters(
        self,
        *,
        item_code: str,
        site_code: str,
        reorder_point: float,
        max_qty: float,
    ) -> dict[str, Any]:
        """Write one item-warehouse row's replenishment parameters.

        Fails closed on writes: the first action, before any network
        call, is the explicit write switch — anything but a truthy
        ``ACUMATICA_ALLOW_WRITES`` raises
        ``AcumaticaWritesDisabledError`` and no request of any kind is
        made. Inputs are then validated with no network: ``item_code``
        and ``site_code`` must be non-empty strings and both numbers
        finite with ``0 <= reorder_point <= max_qty`` (bools are not
        numbers), else ``ValueError`` before any request.

        One session (login ... logout in a ``finally``) reads the row
        with ``GET {origin}/entity/Default/{version}/ItemWarehouse``
        filtered on the item and the site — the REST contract
        endpoint the PurchaseOrder paths build (never the read-only
        ``odatav4`` feed) — with ``$top=1``; an answer without the row
        raises ``AcumaticaError`` naming the item and the site, and a
        null ``ReorderPoint`` or ``MaxQty`` is 0.0 for the ``before``
        state. When both values already equal the request (absolute
        difference under 1e-6) it returns ``{"outcome":
        "unchanged", "before", "after"}`` — ``after`` the ``before`` —
        and sends no write. Otherwise it PUTs that same REST URL
        twice: first the override flags (``OverrideReplenishmentSettings``,
        ``OverrideReorderPoint`` and ``OverrideMaxQty`` all true, no
        values), then the values (``ReorderPoint`` and ``MaxQty``, no
        flags), every value wrapped as ``{"value": ...}``; it then
        re-reads the row as ``after``. The read-back must carry both
        requested values (same tolerance) and both override flags
        enabled — only then does it return ``{"outcome": "written",
        "before", "after"}``; any other read-back raises
        ``AcumaticaWriteVerificationError`` naming the item, the site
        and the field that did not take. A non-2xx on either request
        is handled the way the existing write paths handle it.
        """
        if not _writes_enabled():
            raise AcumaticaWritesDisabledError(
                "Acumatica writes are disabled; set ACUMATICA_ALLOW_WRITES "
                "to a truthy value to enable item warehouse parameter writes"
            )
        item, site, reorder, max_value = _validate_item_warehouse_args(
            item_code, site_code, reorder_point, max_qty
        )
        config = self._load_config()
        with self._session(config) as client:
            row = self._fetch_item_warehouse_row(client, config, item, site)
            before = self._item_warehouse_state(row, "the Acumatica ItemWarehouse read")
            if (
                abs(before["reorder_point"] - reorder) < 1e-6
                and abs(before["max_qty"] - max_value) < 1e-6
            ):
                before_values = {
                    "reorder_point": before["reorder_point"],
                    "max_qty": before["max_qty"],
                }
                return {
                    "outcome": "unchanged",
                    "before": before_values,
                    "after": dict(before_values),
                }
            self._put_item_warehouse_row(
                client,
                config,
                {
                    "InventoryID": {"value": item},
                    "WarehouseID": {"value": site},
                    "OverrideReplenishmentSettings": {"value": True},
                    "OverrideReorderPoint": {"value": True},
                    "OverrideMaxQty": {"value": True},
                },
            )
            self._put_item_warehouse_row(
                client,
                config,
                {
                    "InventoryID": {"value": item},
                    "WarehouseID": {"value": site},
                    "ReorderPoint": {"value": reorder},
                    "MaxQty": {"value": max_value},
                },
            )
            after_row = self._fetch_item_warehouse_row(client, config, item, site)
            after = self._item_warehouse_state(
                after_row, "the Acumatica ItemWarehouse read"
            )
        failed: list[str] = []
        if abs(after["reorder_point"] - reorder) >= 1e-6:
            failed.append("ReorderPoint")
        if abs(after["max_qty"] - max_value) >= 1e-6:
            failed.append("MaxQty")
        if not after["override_reorder_point"]:
            failed.append("OverrideReorderPoint")
        if not after["override_max_qty"]:
            failed.append("OverrideMaxQty")
        if failed:
            raise AcumaticaWriteVerificationError(
                f"the Acumatica ItemWarehouse write for item {item} at site "
                f"{site} did not take: {', '.join(failed)}"
            )
        return {
            "outcome": "written",
            "before": {
                "reorder_point": before["reorder_point"],
                "max_qty": before["max_qty"],
            },
            "after": {
                "reorder_point": after["reorder_point"],
                "max_qty": after["max_qty"],
            },
        }

    # -- purchase order read -----------------------------------------------

    def _fetch_purchase_orders(
        self,
        client: httpx.Client,
        config: _AcumaticaConfig,
        order_nbrs: list[str],
    ) -> list[dict[str, Any]]:
        """GET one chunk of order numbers; the parsed orders.

        One ``GET {origin}/entity/Default/{version}/PurchaseOrder`` per
        call, with the ``$filter`` the ``or`` of one ``OrderNbr
        eq '<nbr>'`` term per number (the single quotes of every number
        doubled), the tenant and the version percent-encoded into the
        path and every query value encoded via httpx params. A
        transport error, a 401/403 (``AcumaticaAuthError``), any other
        non-2xx, a malformed 2xx body, or a malformed row raises the
        explicit client error — a failed chunk is never merged with the
        others.
        """
        url = (
            f"{config.origin}/entity/Default/"
            f"{quote(config.version, safe='')}/PurchaseOrder"
        )
        subject = "the Acumatica PurchaseOrder read"
        params = {
            "$filter": " or ".join(
                f"OrderNbr eq '{_odata_escape(nbr)}'" for nbr in order_nbrs
            ),
            "$expand": "Details",
        }
        try:
            response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise AcumaticaError(f"{subject} failed with {type(exc).__name__}") from exc
        self._check_status(response, subject)
        data = self._parse_json(response, subject)
        if isinstance(data, dict) and isinstance(data.get("value"), list):
            rows = list(data["value"])
        elif isinstance(data, list):
            rows = list(data)
        else:
            raise AcumaticaError(f"{subject} returned a malformed response")
        if any(not isinstance(row, dict) for row in rows):
            raise AcumaticaError(f"{subject} returned a malformed response")
        return [_parse_purchase_order_row(row, subject) for row in rows]

    def read_purchase_orders(self, order_nbrs) -> dict[str, dict[str, Any]]:
        """Read the named purchase orders: one session, reads only.

        ``order_nbrs`` is a list of non-empty order number strings; a
        string, bytes, or a list carrying a non-string or empty entry
        raises ``ValueError`` before any request, and an empty list
        returns ``{}`` with no request. One session — the same login
        and best-effort logout as every other call, no redirects, every
        value percent-encoded — reads the numbers in chunks of at most
        25: each chunk is one ``GET {origin}/entity/Default/{version}/
        PurchaseOrder`` whose ``$filter`` is the ``or`` of one ``OrderNbr
        eq '<nbr>'`` term per number (the single quotes of every number
        doubled) and whose ``$expand`` brings the order lines back in
        the row. Every chunk's answer is parsed exactly as the #543
        duplicate search parses its: a bare JSON array of rows (an
        object whose ``"value"`` is an array is accepted as well),
        every field plain or wrapped as ``{"value": ...}``, and the
        envelope keys ``id``, ``rowNumber``, ``note``, ``custom`` and
        ``_links`` ignored. A transport error, a 401/403
        (``AcumaticaAuthError``), any other non-2xx, or a malformed
        body or row raises for the whole call, so a failed chunk is
        never merged with the others. No write is involved, so the
        write switch is not consulted.

        The result is ``{order_nbr: {status, hold, date, promised_on,
        total, lines: [{item_code, warehouse_code, order_qty,
        received_qty, completed, cancelled, promised}]}}`` for the
        orders the ERP returned; an order number it did not return is
        simply absent. ``received_qty`` is the ``QtyOnReceipts`` and
        every date is the ISO calendar date (``YYYY-MM-DD``, the date
        part of the ERP timestamp).
        """
        config = self._load_config()
        numbers = _validate_order_nbrs(order_nbrs)
        if not numbers:
            return {}
        found: dict[str, dict[str, Any]] = {}
        with self._session(config) as client:
            for start in range(0, len(numbers), _MAX_PO_READ_CHUNK):
                chunk = numbers[start : start + _MAX_PO_READ_CHUNK]
                for order in self._fetch_purchase_orders(client, config, chunk):
                    found[order.pop("order_nbr")] = order
        return found

    # -- transfer order status read --------------------------------------

    def _fetch_transfer_order_statuses(
        self,
        client: httpx.Client,
        config: _AcumaticaConfig,
        reference_nbrs: list[str],
    ) -> list[tuple[str, str]]:
        """GET one chunk of reference numbers; the parsed (nbr, status) pairs.

        One ``GET {origin}/entity/Default/{version}/TransferOrder`` per
        call, with the ``$filter`` the ``or`` of one ``ReferenceNbr
        eq '<nbr>'`` term per number (the single quotes of every number
        doubled), the version percent-encoded into the path and every
        query value encoded via httpx params. A transport error, a
        401/403 (``AcumaticaAuthError``), any other non-2xx, a
        malformed 2xx body, or a malformed row raises the explicit
        client error — a failed chunk is never merged with the others.
        """
        url = (
            f"{config.origin}/entity/Default/"
            f"{quote(config.version, safe='')}/TransferOrder"
        )
        subject = "the Acumatica TransferOrder status read"
        params = {
            "$filter": " or ".join(
                f"ReferenceNbr eq '{_odata_escape(nbr)}'" for nbr in reference_nbrs
            ),
        }
        try:
            response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise AcumaticaError(f"{subject} failed with {type(exc).__name__}") from exc
        self._check_status(response, subject)
        data = self._parse_json(response, subject)
        if isinstance(data, dict) and isinstance(data.get("value"), list):
            rows = list(data["value"])
        elif isinstance(data, list):
            rows = list(data)
        else:
            raise AcumaticaError(f"{subject} returned a malformed response")
        if any(not isinstance(row, dict) for row in rows):
            raise AcumaticaError(f"{subject} returned a malformed response")
        pairs: list[tuple[str, str]] = []
        for row in rows:
            reference_nbr = _entity_field(row, "ReferenceNbr")
            order_status = _entity_field(row, "Status")
            if reference_nbr is None or order_status is None:
                raise AcumaticaError(f"{subject} returned a malformed order")
            pairs.append((reference_nbr, order_status))
        return pairs

    def read_transfer_order_statuses(self, reference_nbrs) -> dict[str, str]:
        """Read the named transfer orders' statuses: one session, reads only.

        ``reference_nbrs`` is a list of non-empty reference number
        strings, at most ``_MAX_TRANSFER_STATUS_READ`` of them and none
        carrying a NUL byte; a string, bytes, an over-long list, or a
        list carrying a non-string, empty, or NUL entry raises
        ``ValueError`` before any request, and an empty list returns
        ``{}`` with no request. One session — the same login and
        best-effort logout as every other call, no redirects, every
        value percent-encoded — reads the numbers in chunks of at most
        ``_MAX_TRANSFER_READ_CHUNK``: each chunk is one ``GET
        {origin}/entity/Default/{version}/TransferOrder`` whose
        ``$filter`` is the ``or`` of one ``ReferenceNbr eq '<nbr>'``
        term per number (the single quotes of every number doubled).
        Every chunk's answer is parsed as a bare JSON array of rows (an
        object whose ``"value"`` is an array is accepted as well),
        each row's ``ReferenceNbr`` and ``Status`` a plain non-empty
        string, or an object whose ``"value"`` is a plain non-empty
        string, and the envelope keys ``id``, ``rowNumber``, ``note``,
        ``custom`` and ``_links`` ignored. A transport error, a
        401/403 (``AcumaticaAuthError``), any other non-2xx, or a
        malformed body or row raises for the whole call, so a failed
        chunk is never merged with the others. No write is involved,
        so the write switch is not consulted.

        The result is ``{reference_nbr: status}`` for the transfer
        orders the ERP returned; a reference number it did not return
        is simply absent.
        """
        config = self._load_config()
        numbers = _validate_transfer_reference_nbrs(reference_nbrs)
        if not numbers:
            return {}
        found: dict[str, str] = {}
        with self._session(config) as client:
            for start in range(0, len(numbers), _MAX_TRANSFER_READ_CHUNK):
                chunk = numbers[start : start + _MAX_TRANSFER_READ_CHUNK]
                for reference_nbr, order_status in self._fetch_transfer_order_statuses(
                    client, config, chunk
                ):
                    found[reference_nbr] = order_status
        return found

    # -- ledger currencies read ------------------------------------------

    def _fetch_ledgers(
        self,
        client: httpx.Client,
        config: _AcumaticaConfig,
    ) -> list[dict[str, Any]]:
        """GET all the ledgers; the parsed rows.

        One ``GET {origin}/entity/Default/{version}/Ledger`` per call —
        no ``$select``, no filter — with the version percent-encoded
        into the path. A transport error, a 401/403
        (``AcumaticaAuthError``), any other non-2xx, or a malformed 2xx
        body raises the explicit client error.
        """
        url = (
            f"{config.origin}/entity/Default/"
            f"{quote(config.version, safe='')}/Ledger"
        )
        subject = "the Acumatica Ledger read"
        try:
            response = client.get(url)
        except httpx.HTTPError as exc:
            raise AcumaticaError(f"{subject} failed with {type(exc).__name__}") from exc
        self._check_status(response, subject)
        data = self._parse_json(response, subject)
        if isinstance(data, dict) and isinstance(data.get("value"), list):
            rows = list(data["value"])
        elif isinstance(data, list):
            rows = list(data)
        else:
            raise AcumaticaError(f"{subject} returned a malformed response")
        if any(not isinstance(row, dict) for row in rows):
            raise AcumaticaError(f"{subject} returned a malformed response")
        return rows

    def read_ledger_currencies(self) -> list[str]:
        """Read the ledger currencies: one session, reads only.

        One session — the same login and best-effort logout as every
        other call, no redirects — sends exactly one ``GET
        {origin}/entity/Default/{version}/Ledger`` with no
        ``$select``. The answer is parsed exactly as the other entity
        reads parse theirs: a bare JSON array of rows (an object whose
        ``"value"`` is an array is accepted as well), every field plain
        or wrapped as ``{"value": ...}``, and the envelope keys ``id``,
        ``rowNumber``, ``note``, ``custom`` and ``_links`` ignored. A
        failed sign-in, a 401/403 (``AcumaticaAuthError``), any other
        non-2xx, or a malformed body or row raises for the whole call.
        No write is involved, so the write switch is not consulted.

        The result is the sorted, distinct, uppercased ``Currency``
        values of the ledgers whose ``Type`` is ``Actual``
        (case-insensitive); a ledger whose ``Type`` or ``Currency`` is
        missing or not a non-empty string contributes nothing.
        """
        config = self._load_config()
        with self._session(config) as client:
            rows = self._fetch_ledgers(client, config)
        currencies: set[str] = set()
        for row in rows:
            ledger_type = _entity_field(row, "Type")
            currency = _entity_field(row, "Currency")
            if ledger_type is None or currency is None:
                continue
            if ledger_type.strip().upper() == "ACTUAL":
                currencies.add(currency.upper())
        return sorted(currencies)
