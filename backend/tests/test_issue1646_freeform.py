"""Issue #1646 proof: an unset or empty AUTH_MODE fails closed.

Before the fix ``app.auth.config`` defaulted ``AUTH_MODE`` to
``"development"``, so a deployment that never set the variable silently
ran the dev-token stub.  After the fix the default is the empty string,
``security.validate_token`` rejects every token with its own error when
no mode is configured, and the ``GET /api/auth/me`` boundary answers 401.

The last check runs a fresh interpreter from the backend root with
``AUTH_MODE`` removed from the environment: importing
``app.auth.config`` there must yield the empty string.
"""

import logging
import os
import subprocess
import sys
from contextlib import contextmanager

from app.auth import config as auth_config
from app.auth.security import validate_token

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# tests/conftest.py configures this dev token for the served app; the
# `client` fixture sends it on every request.
DEV_TOKEN = "dev-secret-token"


@contextmanager
def _security_logger_enabled():
    """Lift the disabled flag on the security logger around a call.

    The session's Alembic upgrade runs fileConfig(alembic.ini), whose
    disable_existing_loggers default leaves the app loggers disabled, so
    records from the logger would otherwise be dropped before any handler
    sees them.  The flag is restored afterwards.
    """
    log = logging.getLogger("app.auth.security")
    old_disabled = log.disabled
    log.disabled = False
    try:
        yield
    finally:
        log.disabled = old_disabled


def test_issue1646_freeform(client, monkeypatch, caplog):
    assert auth_config.AUTH_DEV_TOKEN == DEV_TOKEN

    # --- Unset mode: validate_token fails closed with its own error ----
    monkeypatch.setattr(auth_config, "AUTH_MODE", "")
    caplog.set_level(logging.ERROR)

    captured = len(caplog.records)
    with _security_logger_enabled():
        assert validate_token("any-token") is None
    logged = caplog.records[captured:]
    assert len(logged) == 1
    assert logged[0].levelno == logging.ERROR
    assert logged[0].getMessage() == (
        "auth: AUTH_MODE is not set — failing closed; "
        "set it to production or development"
    )

    # The same failure is visible at the API boundary: with no mode
    # configured the development token is not accepted.
    assert client.get("/api/auth/me").status_code == 401

    # --- Development mode is unchanged ----------------------------------
    monkeypatch.setattr(auth_config, "AUTH_MODE", "development")
    assert client.get("/api/auth/me").status_code == 200

    # --- Unknown mode keeps its own error -------------------------------
    monkeypatch.setattr(auth_config, "AUTH_MODE", "prod")
    captured = len(caplog.records)
    with _security_logger_enabled():
        assert validate_token("any-token") is None
    logged = caplog.records[captured:]
    assert len(logged) == 1
    assert logged[0].getMessage() == "auth: unsupported AUTH_MODE=prod — failing closed"

    # --- No default: with AUTH_MODE unset the config value is "" --------
    env = os.environ.copy()
    env.pop("AUTH_MODE", None)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.auth import config; print(repr(config.AUTH_MODE))",
        ],
        cwd=_BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "''"
