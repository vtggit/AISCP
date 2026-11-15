"""Proving test for issue #556: only /api/aggregate/release gets a longer
proxy timeout.

AC-1/AC-3: `app/nginx.conf` gains one exact-match block,
`location = /api/aggregate/release`, placed before `location /api/`. It
proxies to the same upstream with the same four `proxy_set_header`
directives as `/api/`, and alone among the API routes it raises the proxy
timeouts to 300 s — that route drives one full ERP round trip, and an
idle ERP instance cold-starts inside that round trip. Every other `/api/`
route keeps the 60 s default, so the `/api/` block holds no
`proxy_read_timeout` of its own, and the file still has exactly one
`location /`, one `location /api/` and no stray location blocks
(`location /`'s no-cache header and the request-body ceiling are pinned
by the issue-363 test).

The config is read the way the issue-363 test reads it — a path relative
to this file — and parsed with the issue-363 tokenizer, so plain, exact
(`=`), prefix and regex locations are all found by name and the file
order of the blocks is preserved for the "before `location /api/`" check.
It is read as bytes and decoded with replacement, so a stray non-UTF-8
byte in the file cannot crash the proof; the defect is re-proven below on
a hostile byte string.
"""

from pathlib import Path

from tests.test_issue363_freeform import _collect, _parse_file

NGINX_CONF = Path(__file__).resolve().parents[2] / "app" / "nginx.conf"

RELEASE = "= /api/aggregate/release"
PROXY_PASS = "proxy_pass http://backend:9000;"
READ_TIMEOUT = "proxy_read_timeout 300s;"
SEND_TIMEOUT = "proxy_send_timeout 300s;"


def _decode_conf(raw):
    """Config text from raw bytes, with non-UTF-8 bytes replaced so a
    stray byte (for instance in a comment) cannot crash the reader."""
    return raw.decode("utf-8", errors="replace")


def _locations_in_order(text):
    """[(name, directives)] of every `location` block in `text`, in file
    order — keyed like the issue-363 parser, an exact-match location
    keeping its `=` modifier."""
    found = []
    _collect(_parse_file(text)[1], "location", found)
    return [(header.split(None, 1)[1], directives) for header, directives in found]


def test_issue556_freeform():
    text = _decode_conf(NGINX_CONF.read_bytes())
    locations = _locations_in_order(text)
    names = [name for name, _ in locations]

    # Exactly one of each block: the new exact release route, the API
    # prefix and the static root — no duplicates, no strays.
    assert names.count(RELEASE) == 1, f"expected one {RELEASE!r} block: {names!r}"
    assert names.count("/api/") == 1, f"expected one /api/ block: {names!r}"
    assert names.count("/") == 1, f"expected one / block: {names!r}"

    # The exact route is declared before the /api/ prefix it special-cases,
    # so nginx matches it for this one path and nothing else.
    assert names.index(RELEASE) < names.index(
        "/api/"
    ), f"{RELEASE!r} must be declared before /api/: {names!r}"

    by_name = dict(locations)
    release_directives = by_name[RELEASE]
    api_directives = by_name["/api/"]

    # The release route proxies to the same upstream as /api/ ...
    assert (
        PROXY_PASS in release_directives
    ), f"{RELEASE!r} lost {PROXY_PASS!r}: {release_directives!r}"

    # ... with the same four proxy_set_header directives, verbatim.
    api_headers = [
        directive
        for directive in api_directives
        if directive.startswith("proxy_set_header ")
    ]
    release_headers = [
        directive
        for directive in release_directives
        if directive.startswith("proxy_set_header ")
    ]
    assert len(api_headers) == 4, f"/api/ must keep its four headers: {api_headers!r}"
    assert release_headers == api_headers, (
        f"{RELEASE!r} headers differ from /api/: "
        f"{release_headers!r} != {api_headers!r}"
    )

    # ... and alone among the API routes raises the proxy timeouts to 300 s.
    assert (
        READ_TIMEOUT in release_directives
    ), f"{RELEASE!r} lost {READ_TIMEOUT!r}: {release_directives!r}"
    assert (
        SEND_TIMEOUT in release_directives
    ), f"{RELEASE!r} lost {SEND_TIMEOUT!r}: {release_directives!r}"

    # Every other route keeps the 60 s default: no read timeout in /api/.
    assert not [
        directive
        for directive in api_directives
        if directive.startswith("proxy_read_timeout")
    ], f"/api/ must keep the default proxy_read_timeout: {api_directives!r}"

    # Review defect: a non-UTF-8 byte in the file used to crash the
    # reader with a UnicodeDecodeError; the lenient decode replaces it
    # and the blocks still parse.
    hostile = _decode_conf(
        b"server {\n"
        b"    listen 8080;\n"
        b"    # \xff a stray byte in a comment\n"
        b"    location / {\n"
        b"        try_files $uri /index.html;\n"
        b"    }\n"
        b"}\n"
    )
    assert _locations_in_order(hostile) == [
        ("/", ["try_files $uri /index.html;"]),
    ]
