"""Issue #3 proof: alembic.ini must prepend the backend root to sys.path.

Runs the interpreter in isolated mode (like the `alembic` console script, which
does not put the working directory on sys.path) from backend/ with PYTHONPATH
removed, loads the alembic config, and imports the app package. Without
`prepend_sys_path = .` in alembic.ini, `import app` raises ModuleNotFoundError.

The import must resolve to the backend/app package that lives next to this
test: the subprocess asserts the imported module's path equals that directory
(passed via AIERP_PROOF_APP_DIR) and prints it, and this test compares the
output against it. An unrelated `app` module importable from anywhere else
(site-packages, a stray path entry, ...) cannot make the test pass.
"""

import os
import subprocess
import sys

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOCAL_APP_DIR = os.path.realpath(os.path.join(_BACKEND_ROOT, "app"))

_SNIPPET = (
    "from alembic.config import Config; "
    "from alembic.script import ScriptDirectory; "
    'ScriptDirectory.from_config(Config("alembic.ini")); '
    "import app, os; "
    "expected = os.environ['AIERP_PROOF_APP_DIR']; "
    "imported_app = os.path.realpath(app.__path__[0]); "
    "assert imported_app == expected, "
    "f'imported app is not the local backend package: {imported_app!r} != {expected!r}'; "
    "print(imported_app)"
)


def test_issue3_freeform():
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["AIERP_PROOF_APP_DIR"] = _LOCAL_APP_DIR
    proc = subprocess.run(
        [sys.executable, "-I", "-c", _SNIPPET],
        cwd=_BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"isolated-mode alembic+app import failed "
        f"(exit code {proc.returncode}):\n{proc.stderr}"
    )
    assert proc.stdout.strip() == _LOCAL_APP_DIR, (
        f"imported app is not the local backend/app package "
        f"(expected {_LOCAL_APP_DIR!r}):\n{proc.stdout!r}"
    )
