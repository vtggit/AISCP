"""Issue #1175 proof: the docs name check.

``scripts/docs_names.py`` (standard library only, used by
``scripts/docs_check.py``) reports every route, setting and environment variable
a Markdown doc names that the code does not have.  The code offers: the routes,
the paths and methods of ``backend/openapi.json``; the settings, the
``key="…"`` values of ``backend/app/settings/registry.py`` read as text; and an
environment variable whenever its name appears as a quoted string, as
``${NAME}``, or as ``NAME=`` or ``NAME:`` at the start of a line, in any ``.py``
file under ``backend/``, any file under ``scripts/``, a ``.yml``/``.yaml`` file
at the repository root, a Dockerfile or an ``.env`` example.  A doc names: a
route, a method (GET, POST, PUT, PATCH or DELETE) followed by a path that starts
with ``/api/``; a bare path, a ``/api/`` path inside backticks; a setting key, a
backticked ``word.word`` whose first word is the first word of a registered
key; and an environment variable, a backticked upper-case name with at least
one underscore, or the name before ``=`` in a backticked ``NAME=value``.  A name
passes when path parameters written ``{id}``, ``<id>`` or ``:id`` match any
parameter, a query string and trailing punctuation are ignored, and a bare path
is a route's path or a prefix of one, ending at a ``/``, as in ``/api/auth/*``.

``code_names(root)`` returns the three sets; ``doc_problems(root, paths)``
returns one ``path:line: reason`` per name that does not exist, saying whether
the path or the method does not; with no paths the default set is read
(``README.md``, every ``.md`` under ``docs/``, every ``.md`` directly under
``backend/``), leaving out ``CHANGELOG.md``, ``docs/PRODUCT-VISION.md``,
``docs/reference/`` and anything under a ``tests`` directory.  The docs check
reports the same problems for each changed ``.md`` file outside the left-out
set.  This test builds a small repository in a temporary directory —
``backend/openapi.json`` with ``GET /api/things``,
``POST /api/things/{id}/revoke`` and ``GET /api/auth/me``; a
``backend/app/settings/registry.py`` with ``key="things.limit"``; and a
``backend/app/config.py`` that reads ``os.environ.get("THINGS_TOKEN")`` — and
checks, with both scripts loaded by
``importlib.util.spec_from_file_location``:

* a doc naming ``GET /api/things``, ``POST /api/things/<id>/revoke``,
  ``GET /api/things?limit=5``, ``/api/auth/*``,
  ``/api/things/{id}/revoke``, ``things.limit``, ``THINGS_TOKEN``,
  ``THINGS_TOKEN=abc``, ``app.py``, ``main.py`` and ``HTTP`` reports nothing;
* a doc naming ``DELETE /api/things`` (the method does not exist),
  ``GET /api/widgets``, ``/api/widgets``, ``things.size`` and ``OTHER_TOKEN``
  reports each once, with its path and line;
* a doc naming ``/api/things/{id}`` or ``GET /api/things/{id}`` reports both,
  with its path and line: a trailing path parameter names a path, not a
  directory prefix, and the code has no route there, only the longer
  ``/api/things/{id}/revoke``;
* ``CHANGELOG.md`` and ``docs/reference/API.md`` holding ``GET /api/widgets``
  are not read by default, but a reference directory nested below ``docs/``
  (``docs/api/reference/``) is read, and a relative path is read against the
  repository root, not the current directory;
* ``scripts/docs_check.py --files`` on a changed doc naming ``GET /api/widgets``
  exits 1 with that problem, and on a clean doc exits 0;
* this repository's docs, ``doc_problems`` with the default set, report
  nothing — this assertion fails whenever a later change removes or renames a
  route, setting or environment variable that a doc still names.
"""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


docs_names = _load("issue1175_docs_names", REPO_ROOT / "scripts" / "docs_names.py")
docs_check = _load("issue1175_docs_check", REPO_ROOT / "scripts" / "docs_check.py")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _build_repo(tmp_path: Path) -> dict[str, Path]:
    _write(
        tmp_path / "backend" / "openapi.json",
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "t", "version": "1"},
                "paths": {
                    "/api/things": {
                        "get": {"responses": {"200": {"description": "ok"}}}
                    },
                    "/api/things/{id}/revoke": {
                        "post": {"responses": {"200": {"description": "ok"}}}
                    },
                    "/api/auth/me": {
                        "get": {"responses": {"200": {"description": "ok"}}}
                    },
                },
            }
        ),
    )
    _write(
        tmp_path / "backend" / "app" / "settings" / "registry.py",
        "def register(setting):\n"
        "    pass\n"
        "\n"
        "class Setting:\n"
        "    pass\n"
        "\n"
        "register(\n"
        "    Setting(\n"
        '        key="things.limit",\n'
        '        scope="system",\n'
        '        type="number",\n'
        "        default=10,\n"
        '        description="d",\n'
        "        min=0,\n"
        "        max=100,\n"
        "    )\n"
        ")\n",
    )
    _write(
        tmp_path / "backend" / "app" / "config.py",
        "import os\n\n" 'THINGS_TOKEN = os.environ.get("THINGS_TOKEN")\n',
    )
    return {
        "api": _write(
            tmp_path / "docs" / "API.md",
            "The API.\n"
            "\n"
            "- `GET /api/things` lists things.\n"
            "- `POST /api/things/<id>/revoke` revokes one.\n"
            "- `GET /api/things?limit=5` pages the list.\n"
            "- The auth routes live under `/api/auth/*`.\n"
            "- The revoke endpoint is at `/api/things/{id}/revoke`.\n"
            "- The limit is the `things.limit` setting.\n"
            "- Set `THINGS_TOKEN` for the feed.\n"
            "- `THINGS_TOKEN=abc` in the .env.\n"
            "- Run `app.py`, then `main.py`.\n"
            "- Answers use `HTTP` status codes.\n",
        ),
        "clean": _write(
            tmp_path / "docs" / "clean.md",
            "See `GET /api/things` and the `things.limit` setting.\n",
        ),
        "bad": _write(
            tmp_path / "bad.md",
            "The bad doc.\n"
            "\n"
            "- `DELETE /api/things` removes one.\n"
            "- `GET /api/widgets` lists widgets (see `GET /api/widgets` again).\n"
            "- The widgets live under `/api/widgets`.\n"
            "- The size is the `things.size` setting.\n"
            "- Set `OTHER_TOKEN` too.\n",
        ),
        "changelog": _write(
            tmp_path / "CHANGELOG.md",
            "History: `GET /api/widgets` was removed.\n",
        ),
        "changelog_docs": _write(
            tmp_path / "docs" / "CHANGELOG.md",
            "History: `GET /api/widgets` was renamed.\n",
        ),
        "reference": _write(
            tmp_path / "docs" / "reference" / "API.md",
            "Reference: `GET /api/widgets`.\n",
        ),
        "nested_ref": _write(
            tmp_path / "docs" / "api" / "reference" / "notes.md",
            "A reference directory nested below docs/: `GET /api/things`.\n",
        ),
        "param": _write(
            tmp_path / "param.md",
            "- The item lives at `/api/things/{id}`.\n"
            "- `GET /api/things/{id}` removes one.\n",
        ),
    }


def test_issue1175_freeform(tmp_path, capsys):
    files = _build_repo(tmp_path)

    # The three sets the small repo's code offers.
    routes, settings, env_vars = docs_names.code_names(tmp_path)
    assert ("GET", "/api/things") in routes
    assert ("POST", "/api/things/*/revoke") in routes
    assert ("GET", "/api/auth/me") in routes
    assert ("DELETE", "/api/things") not in routes
    assert "things.limit" in settings
    assert "THINGS_TOKEN" in env_vars
    assert "OTHER_TOKEN" not in env_vars

    # The doc that names only what the code has reports nothing.
    assert docs_names.doc_problems(tmp_path, [files["api"]]) == []

    # The doc that names what the code does not have: each name once, with its
    # path and line, the route saying whether the path or the method is missing.
    problems = docs_names.doc_problems(tmp_path, [files["bad"]])
    assert problems == [
        "bad.md:3: route DELETE /api/things: the method does not exist",
        "bad.md:4: route GET /api/widgets: the path does not exist",
        "bad.md:5: path /api/widgets: the path does not exist",
        "bad.md:6: setting things.size: the setting does not exist",
        "bad.md:7: environment variable OTHER_TOKEN: the variable does not exist",
    ]

    # A trailing path parameter names a path, not a directory prefix: the
    # code has no route at /api/things/{id}, only the longer
    # /api/things/{id}/revoke, which a bare path still names exactly.
    param_problems = docs_names.doc_problems(tmp_path, [files["param"]])
    assert param_problems == [
        "param.md:1: path /api/things/*: the path does not exist",
        "param.md:2: route GET /api/things/*: the path does not exist",
    ]

    # The left-out set: the changelog and the generated reference are not read
    # by default, though they name a route the code does not have; a reference
    # directory nested below docs/ is not the generated docs/reference/.
    defaults = docs_names.default_docs(tmp_path)
    assert files["api"] in defaults
    assert files["clean"] in defaults
    assert files["nested_ref"] in defaults
    assert files["changelog"] not in defaults
    assert files["changelog_docs"] not in defaults
    assert files["reference"] not in defaults
    assert docs_names.doc_problems(tmp_path, defaults) == []
    # …and left_out reads a relative path against the repository root, not
    # the current directory (the test runs from the backend directory, and
    # tmp_path is elsewhere).
    assert docs_names.left_out(tmp_path, Path("docs/reference/API.md"))
    assert docs_names.left_out(tmp_path, files["reference"])
    assert not docs_names.left_out(tmp_path, Path("docs/api/reference/notes.md"))
    assert not docs_names.left_out(tmp_path, Path("docs/clean.md"))
    # …and asked for explicitly, they are checked like any other doc.
    explicit = docs_names.doc_problems(
        tmp_path, [files["changelog"], files["changelog_docs"], files["reference"]]
    )
    assert len(explicit) == 3
    assert all("route GET /api/widgets: the path does not exist" in p for p in explicit)

    # The docs check reports the name problems for a changed doc outside the
    # left-out set, in its path:line: reason output and exit status.
    old_root = docs_check.REPO_ROOT
    docs_check.REPO_ROOT = tmp_path
    try:
        assert docs_check.main(["--files", str(files["bad"])]) == 1
        out = capsys.readouterr().out
        assert "bad.md:4: route GET /api/widgets: the path does not exist" in out
        assert docs_check.main(["--files", str(files["clean"])]) == 0
        assert "clean" in capsys.readouterr().out
    finally:
        docs_check.REPO_ROOT = old_root

    # …and the same from the command line, with the scripts in the small repo so
    # the repository root is the temporary one.
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "docs_names.py", scripts / "docs_names.py")
    shutil.copy(REPO_ROOT / "scripts" / "docs_check.py", scripts / "docs_check.py")
    result = subprocess.run(
        [sys.executable, str(scripts / "docs_names.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    result = subprocess.run(
        [sys.executable, str(scripts / "docs_names.py"), "bad.md"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert (
        "bad.md:3: route DELETE /api/things: the method does not exist" in result.stdout
    )
    result = subprocess.run(
        [sys.executable, str(scripts / "docs_check.py"), "--files", "bad.md"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "bad.md:4: route GET /api/widgets: the path does not exist" in result.stdout
    result = subprocess.run(
        [sys.executable, str(scripts / "docs_check.py"), "--files", "docs/clean.md"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0

    # This repository's docs name nothing the code does not have.
    real_defaults = docs_names.default_docs(REPO_ROOT)
    assert REPO_ROOT / "README.md" in real_defaults
    assert docs_names.doc_problems(REPO_ROOT, real_defaults) == []
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "docs_names.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == ""
