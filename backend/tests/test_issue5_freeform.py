"""Issue #5 proof: the AIDW OData v4 feed client.

Proves, with httpx.MockTransport, that iter_entities yields rows across
pages linked by @odata.nextLink, that $top and $filter reach the first
request URL-encoded, and that every request carries the X-Api-Key header.
It also proves the fail-closed behavior: a cross-host @odata.nextLink, a
non-2xx response, a transport failure, and every argument or configuration
violation raise before or without leaking the API key into a message.
"""

import urllib.parse

import httpx
import pytest

from app.feed.aidw_client import (
    AidwFeedConfigError,
    AidwFeedError,
    iter_entities,
)

BASE_URL = "https://feed.aidw.internal.example"
API_KEY = "s3cret-aidw-feed-key"
FILTER_VALUE = "name eq 'A B' and tag ne \"C D\""


def _page(rows, next_link=None):
    body = {"value": rows}
    if next_link is not None:
        body["@odata.nextLink"] = next_link
    return httpx.Response(200, json=body)


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _set_env(monkeypatch, base_url=BASE_URL, api_key=API_KEY, ca_bundle=None):
    monkeypatch.setenv("AIDW_FEED_BASE_URL", base_url)
    if api_key is None:
        monkeypatch.delenv("AIDW_FEED_API_KEY", raising=False)
    else:
        monkeypatch.setenv("AIDW_FEED_API_KEY", api_key)
    if ca_bundle is None:
        monkeypatch.delenv("AIDW_FEED_CA_BUNDLE", raising=False)
    else:
        monkeypatch.setenv("AIDW_FEED_CA_BUNDLE", ca_bundle)


def _capture(fn, expected_type):
    """Call fn() and return the expected_type exception it must raise."""
    try:
        fn()
    except expected_type as exc:
        return exc
    except Exception as exc:
        pytest.fail(
            f"expected {expected_type.__name__}, " f"got {type(exc).__name__}: {exc}"
        )
    pytest.fail(f"expected {expected_type.__name__}, but no exception was raised")


def test_issue5_freeform(monkeypatch, tmp_path):
    _set_env(monkeypatch)

    # 1. Three pages linked by @odata.nextLink; $top and $filter reach the
    #    first request URL-encoded; X-Api-Key is sent on every request.
    seen = []

    def feed_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) > 10:
            raise AssertionError("pagination did not advance")
        query = request.url.query.decode("ascii")
        skip = urllib.parse.parse_qs(query).get("$skip", ["0"])[0]
        if skip == "0":
            return _page(
                [{"id": 1}, {"id": 2}],
                f"{BASE_URL}/api/feed/v4/Things?$skip=2",
            )
        if skip == "2":
            return _page([{"id": 3}], f"{BASE_URL}/api/feed/v4/Things?$skip=3")
        return _page([{"id": 4}])

    with _mock_client(feed_handler) as client:
        rows = list(iter_entities("Things", filter=FILTER_VALUE, client=client))

    assert rows == [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
    assert [request.url.path for request in seen] == [
        "/api/feed/v4/Things" for _ in range(3)
    ]
    first_query = seen[0].url.query.decode("ascii")
    first_params = urllib.parse.parse_qs(first_query)
    assert (
        "$top" not in first_params
        and seen[0].headers["Prefer"] == "odata.maxpagesize=500"
    )
    assert first_params["$filter"] == [FILTER_VALUE]
    # The wire form must be URL-encoded: no raw spaces or quotes in the query.
    assert " " not in first_query
    assert "'" not in first_query
    assert '"' not in first_query
    for request in seen:
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == urllib.parse.urlsplit(BASE_URL).hostname
        assert request.headers["X-Api-Key"] == API_KEY

    # 2. A cross-host @odata.nextLink raises AidwFeedError; no request is
    #    ever sent to the foreign host, so the key never leaves.
    seen_cross = []

    def cross_handler(request: httpx.Request) -> httpx.Response:
        seen_cross.append(request)
        return _page([{"id": 1}], "https://evil.example/api/feed/v4/Things?$skip=2")

    with _mock_client(cross_handler) as client:
        error = _capture(
            lambda: list(iter_entities("Things", client=client)),
            AidwFeedError,
        )

    assert len(seen_cross) == 1
    assert all(request.url.host != "evil.example" for request in seen_cross)
    assert API_KEY not in str(error)

    # 3. A 401 raises AidwFeedError carrying the status code and the entity
    #    set name — never the response body, never the key.
    seen_401 = []

    def unauthorized_handler(request: httpx.Request) -> httpx.Response:
        seen_401.append(request)
        return httpx.Response(401, text="do-not-leak-this-body")

    with _mock_client(unauthorized_handler) as client:
        error = _capture(
            lambda: list(iter_entities("Things", client=client)),
            AidwFeedError,
        )

    message = str(error)
    assert "401" in message
    assert "Things" in message
    assert API_KEY not in message
    assert "do-not-leak-this-body" not in message
    assert len(seen_401) == 1

    # 4. A missing AIDW_FEED_API_KEY raises AidwFeedConfigError naming the
    #    variable; no request is sent.
    _set_env(monkeypatch, api_key=None)
    seen_nokey = []

    def nokey_handler(request: httpx.Request) -> httpx.Response:
        seen_nokey.append(request)
        return _page([])

    with _mock_client(nokey_handler) as client:
        error = _capture(
            lambda: list(iter_entities("Things", client=client)),
            AidwFeedConfigError,
        )

    assert "AIDW_FEED_API_KEY" in str(error)
    assert API_KEY not in str(error)
    assert seen_nokey == []
    _set_env(monkeypatch)

    # 5. An http:// base URL raises AidwFeedConfigError before any request,
    #    even when a client is injected.
    _set_env(monkeypatch, base_url="http://feed.aidw.internal.example")
    seen_http = []

    def http_handler(request: httpx.Request) -> httpx.Response:
        seen_http.append(request)
        return _page([])

    with _mock_client(http_handler) as client:
        error = _capture(
            lambda: list(iter_entities("Things", client=client)),
            AidwFeedConfigError,
        )

    assert "AIDW_FEED_BASE_URL" in str(error)
    assert seen_http == []
    _set_env(monkeypatch)

    # 6. page_size=True and page_size=0 each raise ValueError before any
    #    request.
    seen_pagesize = []

    def pagesize_handler(request: httpx.Request) -> httpx.Response:
        seen_pagesize.append(request)
        return _page([])

    with _mock_client(pagesize_handler) as client:
        _capture(
            lambda: list(iter_entities("Things", page_size=True, client=client)),
            ValueError,
        )
        _capture(
            lambda: list(iter_entities("Things", page_size=0, client=client)),
            ValueError,
        )
    assert seen_pagesize == []

    # 7. A missing AIDW_FEED_CA_BUNDLE file raises AidwFeedConfigError naming
    #    the variable (never the path); no request is sent.
    missing_ca = tmp_path / "no-such-ca-bundle.pem"
    _set_env(monkeypatch, ca_bundle=str(missing_ca))
    seen_ca = []

    def ca_handler(request: httpx.Request) -> httpx.Response:
        seen_ca.append(request)
        return _page([])

    with _mock_client(ca_handler) as client:
        error = _capture(
            lambda: list(iter_entities("Things", client=client)),
            AidwFeedConfigError,
        )

    assert "AIDW_FEED_CA_BUNDLE" in str(error)
    assert str(missing_ca) not in str(error)
    assert seen_ca == []
    _set_env(monkeypatch)

    # 8. A filter containing a newline raises ValueError before any request.
    seen_newline = []

    def newline_handler(request: httpx.Request) -> httpx.Response:
        seen_newline.append(request)
        return _page([])

    with _mock_client(newline_handler) as client:
        _capture(
            lambda: list(
                iter_entities("Things", filter="name eq 'x'\nid ne 1", client=client)
            ),
            ValueError,
        )
    assert seen_newline == []

    # 9. A CA bundle that is blank after trimming whitespace is treated as
    #    unset: iteration succeeds.
    _set_env(monkeypatch, ca_bundle="   \t ")
    seen_blank = []

    def blank_ca_handler(request: httpx.Request) -> httpx.Response:
        seen_blank.append(request)
        return _page([{"id": 9}])

    with _mock_client(blank_ca_handler) as client:
        rows = list(iter_entities("Things", client=client))
    assert rows == [{"id": 9}]
    _set_env(monkeypatch)

    # 10. A transport failure raises AidwFeedError naming the entity set and
    #     the failure class, without retrying.
    seen_timeout = []

    def timeout_handler(request: httpx.Request):
        seen_timeout.append(request)
        raise httpx.ReadTimeout("read timed out")

    with _mock_client(timeout_handler) as client:
        error = _capture(
            lambda: list(iter_entities("Things", client=client)),
            AidwFeedError,
        )

    message = str(error)
    assert "Things" in message
    assert "ReadTimeout" in message
    assert API_KEY not in message
    assert len(seen_timeout) == 1

    # 11. A whitespace-only AIDW_FEED_BASE_URL raises AidwFeedConfigError
    #     naming the variable; no request is sent.
    _set_env(monkeypatch, base_url="   ")
    seen_nobase = []

    def nobase_handler(request: httpx.Request) -> httpx.Response:
        seen_nobase.append(request)
        return _page([])

    with _mock_client(nobase_handler) as client:
        error = _capture(
            lambda: list(iter_entities("Things", client=client)),
            AidwFeedConfigError,
        )

    assert "AIDW_FEED_BASE_URL" in str(error)
    assert seen_nobase == []
    _set_env(monkeypatch)
