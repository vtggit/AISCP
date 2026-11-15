"""The migration environment must name its database driver.

On 2026-10-12 a rebuilt backend image picked up SQLAlchemy 2.1, whose default driver
for a bare ``postgresql://`` URL is psycopg (version 3). The image installs
psycopg2, so ``alembic upgrade head`` failed at start with
``ModuleNotFoundError: No module named 'psycopg'`` and the backend never came up.
The URL now names the driver the image installs.
"""

import re
from pathlib import Path

from sqlalchemy.engine import make_url

ENV_PY = Path(__file__).resolve().parents[1] / "migrations" / "env.py"


def test_migration_url_names_the_installed_driver():
    source = ENV_PY.read_text(encoding="utf-8")
    schemes = re.findall(r"""["'](postgresql[+\w]*)://""", source)
    assert schemes == ["postgresql+psycopg2"], schemes


def test_that_scheme_resolves_to_psycopg2_whatever_sqlalchemy_defaults_to():
    url = make_url("postgresql+psycopg2://user:pw@localhost:5432/db")
    assert url.get_dialect().driver == "psycopg2"
