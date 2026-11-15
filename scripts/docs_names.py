#!/usr/bin/env python3
"""Name check: the routes, settings and environment variables the docs name must exist.

The backend CI and the docs check both ignore the names a Markdown file uses, so a doc
could keep naming a route, setting or environment variable the code no longer has. This
check (standard library only, also used by scripts/docs_check.py) reports every name a
doc uses that the code does not offer.

What the code offers (code_names):

* the routes are the paths and methods of backend/openapi.json;
* the settings are the key="…" values registered in backend/app/settings/registry.py,
  read as text;
* an environment variable exists when its name appears as a quoted string, as ${NAME},
  or as NAME= or NAME: at the start of a line, in any .py file under backend/, any file
  under scripts/, a compose or other .yml/.yaml file at the repository root, a
  Dockerfile, or an .env example.

What a doc names:

* a route is a method (GET, POST, PUT, PATCH or DELETE) followed by a path that starts
  with /api/;
* a bare path is a path that starts with /api/ inside backticks;
* a setting key is a backticked token of the form word.word (lower-case letters, digits
  after the first letter, and underscores) whose first word is the first word of a
  registered key;
* an environment variable is a backticked upper-case name with at least one underscore,
  or the name before = in a backticked NAME=value.

When a name passes: path parameters written {id}, <id> or :id match any parameter; a
query string and trailing punctuation are ignored; a bare path passes when it is a
route's path or a prefix of one, ending at a /, as in /api/auth/*.

Usage:
    python3 scripts/docs_names.py            # README.md, docs/**/*.md, backend/*.md
    python3 scripts/docs_names.py A.md …     # the given files

With no paths given the default set is read: README.md, every .md under docs/ and every
.md directly under backend/.  Left out: CHANGELOG.md (it records the past),
docs/PRODUCT-VISION.md (it records intent), docs/reference/ (generated), and anything
under a tests directory.

Exit status 0 when every named route, setting and environment variable exists, 1 on any
problem (one ``path:line: reason`` per line).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A doc names a route as "METHOD /api/…", METHOD in the five methods.
_ROUTE_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/api/\S*)")
# A backticked token (no nested backticks, no newline).
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
# A setting key: dot-separated words, each word of lower-case letters, digits after the
# first letter, and underscores.
_SETTING_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
# An environment variable name: upper-case letters, digits after the first letter and
# underscores.
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# A path parameter, written {id} by the code, <id> or :id by a doc.
_PARAM_RE = re.compile(r"^\{[^{}]*\}$|^<[^<>]*>$|^:[A-Za-z0-9_-]+$")
# Trailing punctuation a name may carry in prose (not } or *, which path segments keep).
_TRAILING = ".,;:!?)]'\"`"


def _read_text(path: Path) -> str | None:
    """The file's text, or None when it cannot be read as UTF-8 text."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return None if "\x00" in text else text


def _norm_path(path: str) -> str:
    """The path a name refers to: no query string, no trailing punctuation or slash,
    and every path parameter segment reduced to *."""
    path = path.split("?", 1)[0].rstrip(_TRAILING)
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return "/".join(
        "*" if _PARAM_RE.match(segment) else segment for segment in path.split("/")
    )


def _segments_match(doc_segments: list[str], route_segments: list[str]) -> bool:
    """Whether a doc's path segments equal a route's, * matching any one segment."""
    if len(doc_segments) != len(route_segments):
        return False
    for doc_segment, route_segment in zip(doc_segments, route_segments):
        if doc_segment != "*" and route_segment != "*" and doc_segment != route_segment:
            return False
    return True


def _prefix_match(prefix: list[str], route_segments: list[str]) -> bool:
    """Whether a route's path continues a prefix at a / boundary (a longer path)."""
    if len(route_segments) <= len(prefix):
        return False
    for prefix_segment, route_segment in zip(prefix, route_segments):
        if (
            prefix_segment != "*"
            and route_segment != "*"
            and prefix_segment != route_segment
        ):
            return False
    return True


def _named_segments(raw: str) -> tuple[list[str], list[str]]:
    """The segments of a path a doc names (no query string, no trailing
    punctuation or slash), and the same with every path parameter segment
    reduced to *."""
    stripped = raw.split("?", 1)[0].rstrip(_TRAILING)
    if stripped != "/" and stripped.endswith("/"):
        stripped = stripped[:-1]
    segments = stripped.split("/")
    return segments, [
        "*" if _PARAM_RE.match(segment) else segment for segment in segments
    ]


def _directory_prefix(segments: list[str]) -> list[str] | None:
    """The directory a bare path names, or None when it names a path.  A path
    names a directory when it ends at a / or its last segment is a literal *
    (as in /api/auth/*); a trailing path parameter names a path, not a
    directory."""
    last = segments[-1]
    if last == "*":
        return segments[:-1]
    if _PARAM_RE.match(last):
        return None
    return segments


def _bare_path_passes(raw: str, route_norms: set[str]) -> bool:
    """Whether a bare path is a route's path, or a directory prefix of one."""
    segments, doc_segments = _named_segments(raw)
    for route_norm in route_norms:
        if _segments_match(doc_segments, route_norm.split("/")):
            return True
    prefix = _directory_prefix(segments)
    if prefix is None:
        return False
    return any(
        _prefix_match(prefix, route_norm.split("/")) for route_norm in route_norms
    )


def _route_passes(
    method: str, raw: str, routes: set[tuple[str, str]]
) -> tuple[bool, str]:
    """Whether a named route exists; when not, whether the path or the method is
    missing.  A route is a specific method and path: only a literal trailing *,
    as in ``GET /api/auth/*``, names every route under a prefix, and a trailing
    path parameter names a path, not a prefix."""
    segments, doc_segments = _named_segments(raw)
    prefix = segments[:-1] if segments[-1] == "*" else None
    path_exists = False
    method_exists = False
    for route_method, route_norm in routes:
        route_segments = route_norm.split("/")
        if _segments_match(doc_segments, route_segments) or (
            prefix is not None and _prefix_match(prefix, route_segments)
        ):
            path_exists = True
            if route_method == method:
                method_exists = True
    if not path_exists:
        return False, "the path does not exist"
    if not method_exists:
        return False, "the method does not exist"
    return True, ""


def code_names(root: Path) -> tuple[set[tuple[str, str]], set[str], set[str]]:
    """The three sets the code at ``root`` offers: routes, settings, environment
    variables.  A route is a (METHOD, normalised path) pair."""
    root = Path(root)

    routes: set[tuple[str, str]] = set()
    spec_text = _read_text(root / "backend" / "openapi.json")
    if spec_text is not None:
        try:
            spec = json.loads(spec_text)
        except json.JSONDecodeError:
            spec = {}
        for path, methods in (spec.get("paths") or {}).items():
            if not isinstance(methods, dict):
                continue
            for method in methods:
                if method in ("get", "post", "put", "patch", "delete"):
                    routes.add((method.upper(), _norm_path(path)))

    settings: set[str] = set()
    registry_text = _read_text(root / "backend" / "app" / "settings" / "registry.py")
    if registry_text is not None:
        settings.update(re.findall(r'key\s*=\s*["\']([^"\']+)["\']', registry_text))

    env_files: list[Path] = []
    backend = root / "backend"
    if backend.is_dir():
        env_files.extend(p for p in backend.rglob("*.py") if p.is_file())
    scripts = root / "scripts"
    if scripts.is_dir():
        env_files.extend(p for p in scripts.rglob("*") if p.is_file())
    if root.is_dir():
        env_files.extend(
            p for p in root.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml")
        )
        env_files.extend(p for p in root.rglob("Dockerfile") if p.is_file())
        env_files.extend(p for p in root.rglob(".env*") if p.is_file())

    quoted = re.compile(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")
    dollar = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
    line_assignment = re.compile(r"^([A-Z][A-Z0-9_]*)\s*[=:]")
    env_vars: set[str] = set()
    for path in env_files:
        text = _read_text(path)
        if text is None:
            continue
        env_vars.update(quoted.findall(text))
        env_vars.update(dollar.findall(text))
        for line in text.splitlines():
            match = line_assignment.match(line)
            if match:
                env_vars.add(match.group(1))

    return routes, settings, env_vars


def left_out(root: Path, path: Path) -> bool:
    """Whether a doc is outside the name check's remit: CHANGELOG.md records the past,
    docs/PRODUCT-VISION.md records intent, docs/reference/ is generated, and anything
    under a tests directory is not documentation.  Only docs/reference/ itself is
    generated: a reference directory nested below docs/ is read.  A relative path
    is read against root, not the current directory."""
    path = Path(path)
    if path.is_absolute():
        try:
            rel = path.resolve().relative_to(Path(root).resolve())
        except ValueError:
            return False
    else:
        rel = path
    parts = rel.parts
    if rel.name == "CHANGELOG.md":
        return True
    if rel.as_posix() == "docs/PRODUCT-VISION.md":
        return True
    if parts[:2] == ("docs", "reference"):
        return True
    return "tests" in parts


def default_docs(root: Path) -> list[Path]:
    """The docs read with no paths given: README.md, every .md under docs/ and every
    .md directly under backend/ — minus the left-out set."""
    root = Path(root)
    candidates: list[Path] = []
    if (root / "README.md").is_file():
        candidates.append(root / "README.md")
    docs = root / "docs"
    if docs.is_dir():
        candidates.extend(p for p in docs.rglob("*.md") if p.is_file())
    backend = root / "backend"
    if backend.is_dir():
        candidates.extend(p for p in backend.glob("*.md") if p.is_file())
    return [p for p in sorted(candidates) if not left_out(root, p)]


def doc_problems(root: Path, paths: list[Path]) -> list[str]:
    """One ``path:line: reason`` per name a doc at ``paths`` uses that the code at
    ``root`` does not offer, saying for a route whether the path or the method is
    missing.  The same name is reported once per line."""
    root = Path(root)
    routes, settings, env_vars = code_names(root)
    route_norms = {norm for _, norm in routes}
    setting_first_words = {key.split(".", 1)[0] for key in settings}

    problems: list[str] = []
    for md_path in paths:
        md_path = Path(md_path)
        try:
            rel = md_path.resolve().relative_to(root.resolve())
        except ValueError:
            rel = md_path
        text = _read_text(md_path)
        if text is None:
            problems.append(f"{rel.as_posix()}:0: cannot be read as text")
            continue
        for number, line in enumerate(text.splitlines(), 1):
            seen: set[tuple[str, str]] = set()

            def report(kind: str, name: str, detail: str) -> None:
                if (kind, name) in seen:
                    return
                seen.add((kind, name))
                problems.append(f"{rel.as_posix()}:{number}: {kind} {name}: {detail}")

            for method, raw in _ROUTE_RE.findall(line):
                norm = _norm_path(raw)
                ok, detail = _route_passes(method, raw, routes)
                if not ok:
                    report("route", f"{method} {norm}", detail)

            for token in _BACKTICK_RE.findall(line):
                if token.startswith("/api/"):
                    norm = _norm_path(token)
                    if not _bare_path_passes(token, route_norms):
                        report("path", norm, "the path does not exist")
                    continue
                if _SETTING_TOKEN_RE.match(token) and (
                    token.split(".", 1)[0] in setting_first_words
                ):
                    if token not in settings:
                        report("setting", token, "the setting does not exist")
                    continue
                name: str | None = None
                if "=" in token:
                    candidate = token.split("=", 1)[0]
                    if _ENV_NAME_RE.match(candidate):
                        name = candidate
                elif "_" in token and _ENV_NAME_RE.match(token):
                    name = token
                if name is not None and name not in env_vars:
                    report(
                        "environment variable",
                        name,
                        "the variable does not exist",
                    )
    return problems


def _resolve_doc(arg: str, root: Path) -> Path:
    """A doc path from the command line, relative to the current directory or to the
    repository root."""
    path = Path(arg)
    if not path.is_absolute() and not path.exists():
        candidate = root / arg
        if candidate.exists():
            return candidate
    return path


def main(argv: list[str]) -> int:
    root = ROOT
    paths = [_resolve_doc(arg, root) for arg in argv] if argv else default_docs(root)
    problems = doc_problems(root, paths)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
