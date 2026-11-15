"""Issue #121 proof: the development-mode token has no default value.

Before the fix, ``app.auth.config`` fell back to ``dev-secret-token``
when the ``AUTH_DEV_TOKEN`` environment variable was unset, so a
development deployment that never configured a token silently accepted
the hardcoded stub.  After the fix the default is the empty string and
``security._validate_token_dev`` rejects every token through its
existing fail-closed branch.

AC-2 is proven with fresh Python subprocesses (``cwd`` = backend root):

* First subprocess — ``AUTH_MODE=development`` and ``AUTH_DEV_TOKEN``
  removed from the environment.  It imports ``app.auth.security`` and
  prints the results of ``validate_token("dev-secret-token")`` and
  ``validate_token("dev-secret-token:admin")``; this test asserts both
  printed results are ``None``.
* Second subprocess — ``AUTH_DEV_TOKEN=configured-token``.  It asserts
  that ``validate_token("configured-token:admin")`` returns a user whose
  roles include ``admin``.

Identity (non-admin, role 'user'): this repository is a pure backend —
there is no UI page to boot with a URL-hash token — so the non-admin
pass runs in the same test, after the admin pass, at the auth boundary
itself: the plain ``configured-token`` (no role suffix) must validate to
a user whose roles do NOT include ``admin`` while the token is still
accepted (the "page renders" part of the identity criterion).
"""

import os
import subprocess
import sys

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOCAL_APP_DIR = os.path.realpath(os.path.join(_BACKEND_ROOT, "app"))

# Both subprocesses first verify they imported the local backend/app
# package (an unrelated ``app`` importable from anywhere else must not
# make the proof pass), mirroring tests/test_issue3_freeform.py.
_APP_GUARD = (
    "import os, app; "
    "assert os.path.realpath(app.__path__[0]) == os.environ['AIERP_PROOF_APP_DIR'], "
    "'imported the wrong app package: ' + os.path.realpath(app.__path__[0]); "
)

# AC-2, first subprocess: AUTH_DEV_TOKEN removed — both forms of the old
# hardcoded default must now be rejected, and the subprocess prints the
# results of both validate_token calls.
_SNIPPET_UNSET = (
    _APP_GUARD
    + "import app.auth.security as security; "
    + "print(repr(security.validate_token('dev-secret-token'))); "
    + "print(repr(security.validate_token('dev-secret-token:admin')))"
)

# AC-2, second subprocess: AUTH_DEV_TOKEN=configured-token — the
# admin-suffixed form must return a user whose roles include admin ...
_SNIPPET_CONFIGURED = (
    _APP_GUARD
    + "import app.auth.security as security; "
    + "admin = security.validate_token('configured-token:admin'); "
    + "assert admin is not None, 'configured admin token rejected'; "
    + "assert 'admin' in admin.roles, f'admin role missing: {admin.roles!r}'; "
    + "print('admin-roles ' + repr(admin.roles)); "
    # ... and the plain token (the non-admin identity, role 'user') is
    # still accepted but must never carry the admin role.
    + "viewer = security.validate_token('configured-token'); "
    + "assert viewer is not None, 'plain configured token rejected'; "
    + "assert 'admin' not in viewer.roles, f'viewer must not be admin: {viewer.roles!r}'; "
    + "print('viewer-roles ' + repr(viewer.roles))"
)


def _run_subprocess(snippet: str, env_overrides: dict) -> subprocess.CompletedProcess:
    """Run ``snippet`` in a fresh interpreter with cwd = backend root."""
    env = os.environ.copy()
    env.pop("AUTH_DEV_TOKEN", None)
    env["AUTH_MODE"] = "development"
    env["AIERP_PROOF_APP_DIR"] = _LOCAL_APP_DIR
    env["PYTHONPATH"] = _BACKEND_ROOT
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=_BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_issue121_freeform():
    # --- AC-2, first subprocess: AUTH_DEV_TOKEN removed from the env ----
    unset = _run_subprocess(_SNIPPET_UNSET, {})
    assert unset.returncode == 0, (
        f"unset-token subprocess failed (exit code {unset.returncode}):\n"
        f"{unset.stderr}"
    )
    printed = unset.stdout.strip().splitlines()
    # The subprocess printed the results of both validate_token calls;
    # with no configured token both must be None (fail closed).
    assert printed == ["None", "None"], (
        f"expected both results to be None with AUTH_DEV_TOKEN unset, "
        f"got: {printed!r}\n{unset.stderr}"
    )

    # --- AC-2, second subprocess: AUTH_DEV_TOKEN=configured-token -------
    configured = _run_subprocess(
        _SNIPPET_CONFIGURED,
        {"AUTH_DEV_TOKEN": "configured-token", "AUTH_DEV_ROLES": "user"},
    )
    assert configured.returncode == 0, (
        f"configured-token subprocess failed (exit code {configured.returncode}):\n"
        f"{configured.stderr}"
    )
    printed = dict(
        line.split(" ", 1) for line in configured.stdout.strip().splitlines()
    )
    # The admin-suffixed form returns a user whose roles include admin.
    assert printed.get("admin-roles") == "['admin', 'user']", (
        f"expected admin roles ['admin', 'user'], got: {printed!r}\n"
        f"{configured.stderr}"
    )
    # Non-admin pass (role 'user'): the plain token is accepted but never
    # carries the admin role.
    assert printed.get("viewer-roles") == "['user']", (
        f"expected viewer roles ['user'], got: {printed!r}\n" f"{configured.stderr}"
    )
