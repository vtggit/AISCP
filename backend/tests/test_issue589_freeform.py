"""Issue #589 proof: the AICRM fallbacks become AIERP's.

AC-1 -- the fallback values in the three files match
``backend/app/config.py`` (``aierp`` for the DB name, user and password,
``aierp-backend`` for the auth client id, ``/realms/aierp`` for the
default issuer's realm segment), the ``env.py`` docstring and the
``config.py`` header comment say AIERP, and nothing else changed:
no variable was renamed and a value set in the environment still wins.

AC-2 -- proven below by reading the three files as text from the
repository (paths relative to this test file) and checking that none of
them contains the string ``aicrm`` in any letter case; that
``start.sh`` contains ``${DB_NAME:-aierp}``; that ``env.py`` contains
``os.getenv('DB_NAME', 'aierp')``; and that ``config.py`` contains
``os.getenv("AUTH_CLIENT_ID", "aierp-backend")`` and ``/realms/aierp``.

An independent review additionally confirmed three defects in the
rebranding, each proven fixed below:

* the migration URL in ``migrations/env.py`` built its connection string
  from raw environment values, so a ``DB_USER``/``DB_PASSWORD``/``DB_NAME``
  containing ``@``, ``:`` or ``/`` could rewrite the URL structure;
* ``start.sh`` interpolated its shell variables straight into the embedded
  Python probe, so a value containing a quote or newline could break the
  probe or inject statements into it;
* an empty (or non-numeric) ``AUTH_JWKS_CACHE_TTL`` raised
  ``ValueError`` at import time of ``app.auth.config``.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]

START_SH = (BACKEND / "start.sh").read_text(encoding="utf-8")
ENV_PY = (BACKEND / "migrations" / "env.py").read_text(encoding="utf-8")
AUTH_CONFIG_PY = (BACKEND / "app" / "auth" / "config.py").read_text(encoding="utf-8")


def _assert_migration_url_survives_hostile_values() -> None:
    """Exec env.py's db_url expression under hostile values and parse it.

    The three components that can carry arbitrary characters must survive
    a round-trip through percent-encoding: the parsed URL keeps the exact
    values instead of being reinterpreted (user ``@`` starting the host
    part, ``/`` adding a path, ``#`` starting a fragment, ...).
    """
    hostile = {
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
        "DB_USER": "u@x:1",
        "DB_PASSWORD": "p@ss/w#rd",
        "DB_NAME": "db/with@chars",
    }
    saved = {key: os.environ.get(key) for key in hostile}
    try:
        os.environ.update(hostile)
        match = re.search(r"db_url = \((.*?)\n\)", ENV_PY, re.DOTALL)
        assert match is not None, "db_url assignment not found in migrations/env.py"
        namespace: dict = {"os": os, "quote_plus": quote_plus}
        exec(match.group(0), namespace)
        url = make_url(namespace["db_url"])
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert url.username == "u@x:1"
    assert url.password == "p@ss/w#rd"
    assert url.database == "db/with@chars"
    assert url.host == "localhost"
    assert url.port == 5432
    assert url.get_dialect().driver == "psycopg2"


def _assert_auth_config_imports_with_hostile_ttl() -> None:
    """Import app.auth.config in a subprocess under hostile TTL values.

    Empty and non-numeric values must fall back to the 3600-second
    default instead of crashing the import, while a valid value set in
    the environment still wins.
    """
    for raw_value, expected in (("", "3600"), ("not-a-number", "3600"), ("77", "77")):
        env = dict(os.environ)
        env["AUTH_JWKS_CACHE_TTL"] = raw_value
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import app.auth.config as c; print(c.AUTH_JWKS_CACHE_TTL)",
            ],
            cwd=BACKEND,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert (
            proc.returncode == 0
        ), f"import crashed with AUTH_JWKS_CACHE_TTL={raw_value!r}: {proc.stderr}"
        assert (
            proc.stdout.strip() == expected
        ), f"AUTH_JWKS_CACHE_TTL={raw_value!r} -> {proc.stdout.strip()!r}, expected {expected!r}"


def test_issue589_freeform():
    # AC-2: none of the three files contains "aicrm" in any letter case.
    for name, text in (
        ("start.sh", START_SH),
        ("migrations/env.py", ENV_PY),
        ("app/auth/config.py", AUTH_CONFIG_PY),
    ):
        assert "aicrm" not in text.casefold(), f"{name} still mentions aicrm"

    # AC-2: start.sh falls back to the aierp database.
    assert "${DB_NAME:-aierp}" in START_SH

    # AC-2: env.py falls back to the aierp database (and, per AC-1, the
    # docstring says AIERP and the DB_USER/DB_PASSWORD fallbacks are aierp).
    assert "os.getenv('DB_NAME', 'aierp')" in ENV_PY
    assert "os.getenv('DB_USER', 'aierp')" in ENV_PY
    assert "os.getenv('DB_PASSWORD', 'aierp')" in ENV_PY
    assert "AIERP" in ENV_PY

    # AC-2: config.py falls back to the aierp client id and realm, and
    # (per AC-1) the header comment says AIERP.
    assert 'os.getenv("AUTH_CLIENT_ID", "aierp-backend")' in AUTH_CONFIG_PY
    assert "/realms/aierp" in AUTH_CONFIG_PY
    assert "AIERP" in AUTH_CONFIG_PY

    # AC-1: nothing else changed -- no variable was renamed and values set
    # in the environment still win.
    assert "${DB_HOST:-localhost}" in START_SH
    assert "${DB_PORT:-5432}" in START_SH
    assert "os.getenv('DB_HOST', 'localhost')" in ENV_PY
    assert "os.getenv('DB_PORT', '5432')" in ENV_PY
    assert "https://dev.example.com/realms/aierp" in AUTH_CONFIG_PY

    # Review fix: the migration URL percent-encodes the components that
    # can carry hostile characters, so hostile DB_USER/DB_PASSWORD/DB_NAME
    # values cannot rewrite the URL structure.
    assert "from urllib.parse import quote_plus" in ENV_PY
    assert "quote_plus(os.getenv('DB_USER', 'aierp'))" in ENV_PY
    assert "quote_plus(os.getenv('DB_PASSWORD', 'aierp'))" in ENV_PY
    assert "quote_plus(os.getenv('DB_NAME', 'aierp'))" in ENV_PY
    _assert_migration_url_survives_hostile_values()

    # Review fix: start.sh passes the connection parameters to the
    # embedded Python through the environment; no shell variable is
    # interpolated into the Python source.
    assert "export DB_HOST DB_PORT DB_NAME" in START_SH
    probe = START_SH.split('python -c "', 1)[1].split('\n"', 1)[0]
    assert "$" not in probe, "embedded Python still interpolates shell variables"
    assert "os.environ['DB_HOST']" in probe
    assert "os.environ['DB_NAME']" in probe
    assert "os.environ.get('DB_USER'" in probe
    assert "os.environ.get('DB_PASSWORD'" in probe

    # Review fix: an empty or non-numeric AUTH_JWKS_CACHE_TTL must not
    # crash the import of app.auth.config; a valid value still wins.
    assert 'int(os.getenv("AUTH_JWKS_CACHE_TTL", "3600"))' not in AUTH_CONFIG_PY
    _assert_auth_config_imports_with_hostile_ttl()
