"""Issue #83 proof: AIDW_FEED_TIMEOUT_SECONDS overrides the feed request timeout.

Proves, with a mocked httpx transport that records each request's timeout,
that iter_entities applies the environment-configured timeout to every
request it sends - the first page and each followed @odata.nextLink:
AIDW_FEED_TIMEOUT_SECONDS=120 makes a three-page iteration send all three
requests with a 120-second timeout, 12.5 yields 12.5 seconds, and an unset
variable or the values "", 0, -5, or abc keep the 30-second default.
"""

import httpx

from app.feed.aidw_client import iter_entities

BASE_URL = "https://feed.aidw.internal.example"
API_KEY = "s3cret-aidw-feed-key"
DEFAULT_TIMEOUT = 30.0


class _TimeoutRecordingTransport:
    """Mock transport that records each request's effective timeout."""

    def __init__(self, handler):
        self._inner = httpx.MockTransport(handler)
        self.timeouts = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        timeout = request.extensions.get("timeout")
        self.timeouts.append(timeout["read"])
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def _page(rows, next_link=None):
    body = {"value": rows}
    if next_link is not None:
        body["@odata.nextLink"] = next_link
    return httpx.Response(200, json=body)


def _three_page_handler():
    """Serve three one-row pages linked by @odata.nextLink."""

    def feed_handler(request: httpx.Request) -> httpx.Response:
        skip = int(request.url.params.get("$skip", "0"))
        if skip == 0:
            return _page(
                [{"id": 0}],
                f"{BASE_URL}/api/feed/v4/Things?$skip=1",
            )
        if skip == 1:
            return _page([{"id": 1}], f"{BASE_URL}/api/feed/v4/Things?$skip=2")
        return _page([{"id": 2}])

    return feed_handler


def _iter_three_pages(transport):
    client = httpx.Client(transport=transport)
    try:
        return list(iter_entities("Things", client=client))
    finally:
        client.close()


def test_issue83_freeform(monkeypatch):
    monkeypatch.setenv("AIDW_FEED_BASE_URL", BASE_URL)
    monkeypatch.setenv("AIDW_FEED_API_KEY", API_KEY)
    monkeypatch.delenv("AIDW_FEED_CA_BUNDLE", raising=False)

    # Positive integer: all three requests (first page + two nextLinks)
    # carry a 120-second timeout.
    monkeypatch.setenv("AIDW_FEED_TIMEOUT_SECONDS", "120")
    transport = _TimeoutRecordingTransport(_three_page_handler())
    rows = _iter_three_pages(transport)
    assert [row["id"] for row in rows] == [0, 1, 2]
    assert transport.timeouts == [120.0, 120.0, 120.0]

    # Decimal value: 12.5 seconds on every request.
    monkeypatch.setenv("AIDW_FEED_TIMEOUT_SECONDS", "12.5")
    transport = _TimeoutRecordingTransport(_three_page_handler())
    _iter_three_pages(transport)
    assert transport.timeouts == [12.5, 12.5, 12.5]

    # Unset, blank, zero, negative, and non-numeric keep the 30-second
    # default on every request.
    for raw in (None, "", "0", "-5", "abc"):
        if raw is None:
            monkeypatch.delenv("AIDW_FEED_TIMEOUT_SECONDS", raising=False)
        else:
            monkeypatch.setenv("AIDW_FEED_TIMEOUT_SECONDS", raw)
        transport = _TimeoutRecordingTransport(_three_page_handler())
        _iter_three_pages(transport)
        assert transport.timeouts == [
            DEFAULT_TIMEOUT,
            DEFAULT_TIMEOUT,
            DEFAULT_TIMEOUT,
        ], (
            f"unexpected timeouts for AIDW_FEED_TIMEOUT_SECONDS={raw!r}: "
            f"{transport.timeouts}"
        )
