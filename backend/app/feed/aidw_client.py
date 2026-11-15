"""Fail-closed client for AIDW's OData v4 feed.

``iter_entities`` pages through one named entity set of the feed, following
``@odata.nextLink`` until a page has none. Every request carries the
``X-Api-Key`` header and a ``Prefer: odata.maxpagesize=<page_size>``
header, verifies TLS (against the CA bundle in ``AIDW_FEED_CA_BUNDLE``
when set) and uses a 30-second timeout unless
``AIDW_FEED_TIMEOUT_SECONDS`` overrides it.

Configuration is read from the environment at call time:

- ``AIDW_FEED_BASE_URL``: HTTPS origin, no path, query, or fragment.
- ``AIDW_FEED_API_KEY``: sent as the ``X-Api-Key`` header.
- ``AIDW_FEED_CA_BUNDLE``: optional CA bundle path for TLS verification;
  blank after trimming whitespace is treated as unset.
- ``AIDW_FEED_TIMEOUT_SECONDS``: optional per-request timeout in
  seconds, read at call time; a positive number (an integer or a
  decimal such as ``90`` or ``12.5``) replaces the 30-second
  default, while unset, blank, zero, negative, or non-numeric
  values keep it.

The client fails closed: configuration and argument problems raise before
any request, a ``@odata.nextLink`` pointing at any other scheme, host, or
port is refused so the API key never reaches another host, and no error
message contains the API key or a response body.
"""

import math
import os
import re
import ssl
import unicodedata
from collections.abc import Generator
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

__all__ = ["AidwFeedConfigError", "AidwFeedError", "iter_entities"]

BASE_URL_VAR = "AIDW_FEED_BASE_URL"
API_KEY_VAR = "AIDW_FEED_API_KEY"
CA_BUNDLE_VAR = "AIDW_FEED_CA_BUNDLE"
FEED_TIMEOUT_VAR = "AIDW_FEED_TIMEOUT_SECONDS"

_FEED_PATH = "/api/feed/v4"
_REQUEST_TIMEOUT = 30.0
_MAX_PAGE_SIZE = 1000
_MAX_FILTER_LENGTH = 2000
_ENTITY_SET_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_BASE_URL_ERROR = (
    f"{BASE_URL_VAR} must be an HTTPS origin with no path, query, or fragment"
)
_NEXT_LINK_ERROR = (
    "AIDW feed returned an @odata.nextLink outside the configured feed "
    "origin; refusing to send credentials to another host"
)


class AidwFeedError(Exception):
    """A feed request failed.

    The message names the entity set and, for transport failures, the
    failure class; it never contains the API key or a response body.
    """


class AidwFeedConfigError(AidwFeedError):
    """Feed configuration is missing or invalid.

    The message names the offending environment variable and never its value.
    """


def _validate_entity_set(entity_set: object) -> None:
    if not isinstance(entity_set, str) or _ENTITY_SET_RE.fullmatch(entity_set) is None:
        raise ValueError(
            "entity_set must be a string matching ^[A-Za-z_][A-Za-z0-9_]{0,127}$"
        )


def _validate_page_size(page_size: object) -> None:
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= _MAX_PAGE_SIZE
    ):
        raise ValueError("page_size must be an int (not a bool) from 1 to 1000")


def _validate_filter(query_filter: object) -> None:
    if query_filter is None:
        return
    if not isinstance(query_filter, str) or len(query_filter) > _MAX_FILTER_LENGTH:
        raise ValueError("filter must be a string of at most 2000 characters")
    if any(unicodedata.category(character) == "Cc" for character in query_filter):
        raise ValueError("filter must not contain control characters")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise AidwFeedConfigError(f"{name} must be set to a non-blank value")
    return value


def _validate_base_url(base_url: str) -> str:
    """Return the origin with any trailing slash dropped, or raise."""
    if base_url != base_url.strip():
        raise AidwFeedConfigError(_BASE_URL_ERROR)
    try:
        parts = urlsplit(base_url)
        hostname = parts.hostname
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        raise AidwFeedConfigError(_BASE_URL_ERROR) from None
    if (
        parts.scheme.lower() != "https"
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise AidwFeedConfigError(_BASE_URL_ERROR)
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
    raw = os.environ.get(FEED_TIMEOUT_VAR)
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
        raise AidwFeedConfigError(
            f"{CA_BUNDLE_VAR} names a file that does not exist or cannot be read"
        ) from None


def _build_client(ca_bundle: str | None, timeout: float) -> httpx.Client:
    verify = ca_bundle if ca_bundle is not None else True
    try:
        return httpx.Client(verify=verify, timeout=timeout)
    except (ssl.SSLError, OSError) as exc:
        raise AidwFeedConfigError(
            f"{CA_BUNDLE_VAR} could not be applied to the feed client "
            f"({type(exc).__name__})"
        ) from None


def _effective_port(scheme: str, port: int | None) -> int:
    if port is not None:
        return port
    return 443 if scheme.lower() == "https" else 80


def _assert_same_origin(next_link: str, base_url: str) -> None:
    try:
        link = urlsplit(next_link)
        link_port = link.port
        base = urlsplit(base_url)
        base_port = base.port
    except ValueError:
        raise AidwFeedError(_NEXT_LINK_ERROR) from None
    if (
        link.scheme.lower() != base.scheme.lower()
        or (link.hostname or "").lower() != (base.hostname or "").lower()
        or _effective_port(link.scheme, link_port)
        != _effective_port(base.scheme, base_port)
    ):
        raise AidwFeedError(_NEXT_LINK_ERROR)


def _fetch_page(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    entity_set: str,
    timeout: float,
) -> dict[str, Any]:
    try:
        response = client.get(url, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise AidwFeedError(
            f"AIDW feed request for entity set '{entity_set}' failed with "
            f"{type(exc).__name__}; not retrying"
        ) from None
    except (ssl.SSLError, OSError) as exc:
        raise AidwFeedError(
            f"AIDW feed request for entity set '{entity_set}' failed with "
            f"{type(exc).__name__}; not retrying"
        ) from None
    if not 200 <= response.status_code < 300:
        raise AidwFeedError(
            f"AIDW feed request for entity set '{entity_set}' "
            f"returned HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except (ValueError, httpx.HTTPError) as exc:
        raise AidwFeedError(
            f"AIDW feed response for entity set '{entity_set}' "
            f"is not valid JSON ({type(exc).__name__})"
        ) from None
    if not isinstance(payload, dict):
        raise AidwFeedError(
            f"AIDW feed response for entity set '{entity_set}' "
            "is not an OData page object"
        )
    rows = payload.get("value")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise AidwFeedError(
            f"AIDW feed response for entity set '{entity_set}' "
            "has a malformed 'value' list"
        )
    return payload


def _first_page_url(origin: str, entity_set: str, query_filter: str | None) -> str:
    # $filter must reach the wire as a literal OData parameter name; the
    # value is URL-encoded (httpx would percent-encode the '$' in the keys).
    # Page size travels in the Prefer header, not the URL.
    url = f"{origin}{_FEED_PATH}/{entity_set}"
    if query_filter is not None:
        url += f"?$filter={quote(query_filter, safe='')}"
    return url


def iter_entities(
    entity_set: str,
    filter: str | None = None,
    page_size: int = 500,
    client: httpx.Client | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Yield each row dict of ``entity_set`` from the AIDW OData v4 feed.

    Sends ``GET {AIDW_FEED_BASE_URL}/api/feed/v4/{entity_set}`` with
    ``$filter`` when given, then follows each response's
    ``@odata.nextLink`` until a page has none. Every request carries the
    ``X-Api-Key`` header and ``Prefer: odata.maxpagesize=page_size``.
    ``client`` may inject an ``httpx.Client`` (used by tests); a
    cross-host ``@odata.nextLink`` is refused before any request is sent
    to it.
    """
    _validate_entity_set(entity_set)
    _validate_page_size(page_size)
    _validate_filter(filter)

    base_url = _require_env(BASE_URL_VAR)
    origin = _validate_base_url(base_url)
    api_key = _require_env(API_KEY_VAR)
    ca_bundle = _ca_bundle_path()
    _ensure_ca_bundle_readable(ca_bundle)
    timeout = _request_timeout()

    active_client = client
    if active_client is None:
        active_client = _build_client(ca_bundle, timeout)
        owned_client = active_client
    else:
        owned_client = None
    headers = {
        "X-Api-Key": api_key,
        "Prefer": f"odata.maxpagesize={page_size}",
    }
    try:
        url = _first_page_url(origin, entity_set, filter)
        while True:
            payload = _fetch_page(active_client, url, headers, entity_set, timeout)
            yield from payload["value"]
            next_link = payload.get("@odata.nextLink")
            if not next_link:
                return
            if not isinstance(next_link, str):
                raise AidwFeedError(
                    f"AIDW feed response for entity set '{entity_set}' "
                    "has a malformed @odata.nextLink"
                )
            _assert_same_origin(next_link, base_url)
            url = next_link
    finally:
        if owned_client is not None:
            owned_client.close()
