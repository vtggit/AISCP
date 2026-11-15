"""Issue #78 proof: page size travels in the Prefer header, not the URL.

Proves, with httpx.MockTransport, that iter_entities no longer sends
$top: a set served as three pages of 1000, 1000 and 7 rows linked by
@odata.nextLink yields all 2007 rows; no request URL contains $top;
every request (first page and each followed nextLink) carries
``Prefer: odata.maxpagesize=500`` and ``X-Api-Key`` by default; with
``page_size=250`` the header reads ``odata.maxpagesize=250``; and a
given filter still reaches the first request as ``$filter``,
URL-encoded.
"""

import urllib.parse

import httpx

from app.feed.aidw_client import iter_entities

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


def _set_env(monkeypatch, base_url=BASE_URL, api_key=API_KEY):
    monkeypatch.setenv("AIDW_FEED_BASE_URL", base_url)
    monkeypatch.setenv("AIDW_FEED_API_KEY", api_key)
    monkeypatch.delenv("AIDW_FEED_CA_BUNDLE", raising=False)


def _three_page_handler(seen):
    """Serve 1000, 1000 and 7 rows over three @odata.nextLink pages."""

    def feed_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) > 10:
            raise AssertionError("pagination did not advance")
        query = request.url.query.decode("ascii")
        skip = urllib.parse.parse_qs(query).get("$skip", ["0"])[0]
        if skip == "0":
            return _page(
                [{"id": row_id} for row_id in range(0, 1000)],
                f"{BASE_URL}/api/feed/v4/Things?$skip=1000",
            )
        if skip == "1000":
            return _page(
                [{"id": row_id} for row_id in range(1000, 2000)],
                f"{BASE_URL}/api/feed/v4/Things?$skip=2000",
            )
        return _page([{"id": row_id} for row_id in range(2000, 2007)])

    return feed_handler


def test_issue78_freeform(monkeypatch):
    _set_env(monkeypatch)

    # 1. Three pages of 1000, 1000 and 7 rows linked by @odata.nextLink
    #    yield all 2007 rows; no request URL contains $top; every request
    #    carries Prefer: odata.maxpagesize=500 and X-Api-Key.
    seen = []

    with _mock_client(_three_page_handler(seen)) as client:
        rows = list(iter_entities("Things", client=client))

    assert len(rows) == 2007
    assert [row["id"] for row in rows] == list(range(2007))
    assert [request.url.path for request in seen] == [
        "/api/feed/v4/Things" for _ in range(3)
    ]
    assert seen[0].url.query == b""
    assert seen[1].url.query.decode("ascii") == "$skip=1000"
    assert seen[2].url.query.decode("ascii") == "$skip=2000"
    for request in seen:
        assert request.method == "GET"
        assert "$top" not in str(request.url)
        assert request.headers["Prefer"] == "odata.maxpagesize=500"
        assert request.headers["X-Api-Key"] == API_KEY

    # 2. With page_size=250 every request's header reads
    #    odata.maxpagesize=250; all 2007 rows are still yielded.
    seen250 = []

    with _mock_client(_three_page_handler(seen250)) as client:
        rows250 = list(iter_entities("Things", page_size=250, client=client))

    assert len(rows250) == 2007
    assert len(seen250) == 3
    for request in seen250:
        assert "$top" not in str(request.url)
        assert request.headers["Prefer"] == "odata.maxpagesize=250"
        assert request.headers["X-Api-Key"] == API_KEY

    # 3. A given filter still reaches the first request as $filter,
    #    URL-encoded, and every request still carries both headers.
    seenf = []

    with _mock_client(_three_page_handler(seenf)) as client:
        rowsf = list(iter_entities("Things", filter=FILTER_VALUE, client=client))

    assert len(rowsf) == 2007
    first_query = seenf[0].url.query.decode("ascii")
    first_params = urllib.parse.parse_qs(first_query)
    assert "$top" not in first_params
    assert first_params["$filter"] == [FILTER_VALUE]
    # The wire form must be URL-encoded: no raw spaces or quotes in the query.
    assert " " not in first_query
    assert "'" not in first_query
    assert '"' not in first_query
    for request in seenf:
        assert request.headers["Prefer"] == "odata.maxpagesize=500"
        assert request.headers["X-Api-Key"] == API_KEY
