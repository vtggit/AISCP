"""Proving test for issue #363: the static location is never cached.

AC-1: every response served by `location /` (the pages, scripts, stylesheets
and images) carries `Cache-Control: no-cache`, sent for every status via
`always` — including the `try_files` fallback to `index.html`. The API proxy
(`location /api/`) and its headers, the request-body ceiling and the
`try_files` fallback are unchanged.

AC-2: this test proves that from `app/nginx.conf` itself. It finds each
`location` block by matching its braces (not by line position) and checks
that `location /` holds the no-cache header, that `location /api/` has no
`Cache-Control` header of its own, and that every other directive of each
block is exactly the one listed below from the base branch.

The config is read by a small tokenizer, so a comment can never look like a
block or a brace, a quoted string keeps its braces, and plain, exact,
prefix and regex `location` forms are all found. The review defects are
re-proven below on hostile snippets.
"""

from pathlib import Path

NGINX_CONF = Path(__file__).resolve().parents[2] / "app" / "nginx.conf"

# The one directive this issue adds, exactly as it must appear in `location /`.
NO_CACHE_HEADER = 'add_header Cache-Control "no-cache" always;'

# Every directive the server block held on the base branch, verbatim — the
# request-body ceiling among them.
BASE_SERVER_DIRECTIVES = [
    "listen 8080;",
    "server_name _;",
    "root /usr/share/nginx/html;",
    "index index.html;",
    "client_max_body_size 1m;",
]

# Every directive each location block held on the base branch, verbatim.
BASE_LOCATION_DIRECTIVES = {
    "/": [
        "try_files $uri $uri/ /index.html;",
    ],
    "/api/": [
        "proxy_pass http://backend:9000;",
        "proxy_set_header Host $host;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "proxy_set_header X-Forwarded-Proto $scheme;",
    ],
}

# Hostile snippets that pin the parser's review defects.

# `server {` and `location / {` inside a comment must not read as blocks.
COMMENTED_HEADERS = """
# server {
#     listen 9999;
# }
server {
    listen 8080;
    # location / {
    #     add_header Cache-Control "no-cache" always;
    # }
}
"""

# A brace inside a comment of a nested block must not break the skip.
NESTED_COMMENT_BRACE = """
server {
    listen 8080;
    location / {
        # a lone { inside a comment
        try_files $uri /index.html;
    }
    index index.html;
}
"""

# Neither must a brace inside a quoted string.
NESTED_STRING_BRACE = """
server {
    listen 8080;
    location / {
        add_header X-Note "a } inside a string";
        try_files $uri /index.html;
    }
    index index.html;
}
"""

# Regex, exact and prefix locations must be found, not skipped.
REGEX_LOCATIONS = """
server {
    listen 8080;
    location / {
        try_files $uri /index.html;
    }
    location ~ \\.png$ {
        add_header X-Png "yes";
    }
    location ~* \\.(css|js)$ {
        add_header X-Asset "yes";
    }
    location = /exact {
        add_header X-Exact "yes";
    }
}
"""

# Characters that end a bare word while tokenizing.
_WORD_STOPS = ' \t\r\n"#{};'


def _tokenize(text):
    """Tokens of an nginx config: bare words, quoted strings kept verbatim
    (quotes included, backslash escapes honoured) and the structural
    characters '{', '}' and ';'.

    Everything from '#' to the end of its line is a comment and is dropped
    entirely, so a commented-out header or brace can never look like a
    block.
    """
    tokens = []
    i = 0
    size = len(text)
    while i < size:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
        elif ch == "#":
            while i < size and text[i] != "\n":
                i += 1
        elif ch == '"':
            end = i + 1
            while end < size:
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == '"':
                    break
                end += 1
            if end >= size:
                raise AssertionError("unterminated string in config")
            tokens.append(text[i : end + 1])
            i = end + 1
        elif ch in "{};":
            tokens.append(ch)
            i += 1
        else:
            end = i
            while end < size and text[end] not in _WORD_STOPS:
                end += 1
            tokens.append(text[i:end])
            i = end
    return tokens


def _parse_block(tokens, pos):
    """(directives, blocks, next_pos) of one block level.

    `directives` are the level's own directives, each with its ';', in
    order; `blocks` are (header, level) pairs of the nested blocks, in
    order, where `level` is a (directives, blocks) pair; `next_pos` is the
    index just past the '}' that closed the level (len(tokens) at the top
    level).
    """
    directives = []
    blocks = []
    current = []
    while pos < len(tokens):
        token = tokens[pos]
        if token == ";":
            if current:
                directives.append(" ".join(current) + ";")
            current = []
            pos += 1
        elif token == "{":
            header = " ".join(current)
            assert header, "block without a header"
            current = []
            nested_directives, nested_blocks, pos = _parse_block(tokens, pos + 1)
            blocks.append((header, (nested_directives, nested_blocks)))
        elif token == "}":
            assert not current, f"unterminated directive before '}}': {current!r}"
            return directives, blocks, pos + 1
        else:
            current.append(token)
            pos += 1
    assert not current, f"tokens without a terminating ';' or block: {current!r}"
    return directives, blocks, pos


def _parse_file(text):
    """(directives, blocks) of a whole config file, parsed at top level."""
    tokens = _tokenize(text)
    directives, blocks, pos = _parse_block(tokens, 0)
    assert pos == len(tokens), "config does not end at top level"
    return directives, blocks


def _collect(blocks, keyword, found):
    """Append (header, directives) of every block, at any depth, whose first
    header word is `keyword`, in file order."""
    for header, (directives, nested) in blocks:
        if header.split(None, 1)[0] == keyword:
            found.append((header, directives))
        _collect(nested, keyword, found)


def _server_blocks(text):
    """[(header, directives)] of every `server` block in `text`, in order."""
    found = []
    _collect(_parse_file(text)[1], "server", found)
    return found


def _location_blocks(text):
    """{location name: directives} of every `location` block in `text`, in
    file order — plain, exact (`=`), prefix (`^~`) and regex (`~`, `~*`)
    forms alike."""
    found = []
    _collect(_parse_file(text)[1], "location", found)
    return {header.split(None, 1)[1]: directives for header, directives in found}


def _sets_cache_control(directive):
    head = directive.split(None, 2)
    return (
        len(head) >= 3
        and head[0] == "add_header"
        and head[1].lower() == "cache-control"
    )


def test_issue363_freeform():
    text = NGINX_CONF.read_text(encoding="utf-8")

    # The server-level directives — including the request-body ceiling — are
    # the base branch ones, unchanged.
    server_found = _server_blocks(text)
    assert [header for header, _ in server_found] == [
        "server"
    ], f"expected exactly one server block, found: {server_found!r}"
    server_directives = server_found[0][1]
    assert server_directives == BASE_SERVER_DIRECTIVES, (
        "server block changed from the base branch: "
        f"{server_directives!r} != {BASE_SERVER_DIRECTIVES!r}"
    )

    locations = _location_blocks(text)
    assert set(locations) == {
        "/",
        "/api/",
        "= /api/aggregate/release",
    }, f"unexpected location blocks: {sorted(locations)}"

    # AC-1: `location /` carries the no-cache header, sent for every status
    # via `always` — it covers every response the location serves, including
    # the try_files fallback to index.html.
    root_directives = locations["/"]
    assert (
        NO_CACHE_HEADER in root_directives
    ), f"location / must hold {NO_CACHE_HEADER!r}, found: {root_directives!r}"

    # Every other directive of `location /` is the base branch one: the
    # try_files fallback, unchanged.
    root_rest = [
        directive for directive in root_directives if directive != NO_CACHE_HEADER
    ]
    assert root_rest == BASE_LOCATION_DIRECTIVES["/"], (
        "location / directives other than the no-cache header changed from "
        f"the base branch: {root_rest!r} != {BASE_LOCATION_DIRECTIVES['/']!r}"
    )

    # AC-1: the API proxy is unchanged — no Cache-Control header of its own,
    # and every directive still the base branch one.
    api_directives = locations["/api/"]
    for directive in api_directives:
        assert not _sets_cache_control(
            directive
        ), f"location /api/ must not set Cache-Control: {directive!r}"
    assert api_directives == BASE_LOCATION_DIRECTIVES["/api/"], (
        f"location /api/ changed from the base branch: "
        f"{api_directives!r} != {BASE_LOCATION_DIRECTIVES['/api/']!r}"
    )

    # Review defect 1: a `server {` / `location / {` that appears only
    # inside a comment is not a block; the real server keeps exactly its
    # own directives and no location block is invented.
    assert _server_blocks(COMMENTED_HEADERS) == [("server", ["listen 8080;"])]
    assert _location_blocks(COMMENTED_HEADERS) == {}

    # Review defect 2: a brace inside a comment, or inside a quoted string,
    # of a nested block cannot fool the brace matching; the directive after
    # the nested block is still the server block's own.
    for snippet, location_directives in (
        (NESTED_COMMENT_BRACE, ["try_files $uri /index.html;"]),
        (
            NESTED_STRING_BRACE,
            [
                'add_header X-Note "a } inside a string";',
                "try_files $uri /index.html;",
            ],
        ),
    ):
        assert _server_blocks(snippet) == [
            ("server", ["listen 8080;", "index index.html;"])
        ], f"nested-block skip broke on: {snippet!r}"
        assert _location_blocks(snippet) == {
            "/": location_directives
        }, f"nested-block skip broke on: {snippet!r}"

    # Review defect 3: regex (`~`, `~*`), exact (`=`) and prefix (`^~`)
    # locations are matched by the parser, not silently skipped.
    assert _location_blocks(REGEX_LOCATIONS) == {
        "/": ["try_files $uri /index.html;"],
        "~ \\.png$": ['add_header X-Png "yes";'],
        "~* \\.(css|js)$": ['add_header X-Asset "yes";'],
        "= /exact": ['add_header X-Exact "yes";'],
    }
