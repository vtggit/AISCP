"""Shared test fixtures for the AIERP backend.

Provides an isolated test database created and dropped per session, Alembic migrations
applied to it, a FastAPI TestClient, and per-test cleanup so tests never see each
other's rows.

Named for AIERP deliberately: AIDW's conftest was copied from AICRM at its bootstrap and
still declares "AICRM" with an aicrm_test_db — harmless but confusing drift that stops
here.
"""

import os
import sys
from collections.abc import Generator

import psycopg2
import psycopg2.sql
import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _test_db_name() -> str:
    """Resolve the test database name.

    An explicitly empty ``AIERP_TEST_DB`` value is treated exactly like an
    unset one: ``CREATE DATABASE ""`` is a zero-length identifier that would
    crash the session fixture before a single test could run.
    """
    return os.getenv("AIERP_TEST_DB") or "aierp_test_db"


_TEST_DB_NAME = _test_db_name()
_DB_HOST = os.getenv("DB_HOST", "localhost")
_DB_PORT = os.getenv("DB_PORT", "5432")
_DB_USER = os.getenv("DB_USER", "aierp")
_DB_PASSWORD = os.getenv("DB_PASSWORD", "aierp")


def _admin_connection():
    """Connect to the maintenance database to create/drop the test database."""
    for user, password, dbname in (
        (_DB_USER, _DB_PASSWORD, "postgres"),
        ("postgres", "postgres", "postgres"),
    ):
        try:
            conn = psycopg2.connect(
                host=_DB_HOST,
                port=_DB_PORT,
                user=user,
                password=password,
                dbname=dbname,
            )
            conn.autocommit = True
            return conn
        except psycopg2.Error:
            continue
    raise RuntimeError(f"cannot reach PostgreSQL at {_DB_HOST}:{_DB_PORT}")


@pytest.fixture(scope="session", autouse=True)
def _test_database() -> Generator[None, None, None]:
    """Create the test database, migrate it, and drop it when the session ends."""
    admin = _admin_connection()
    ident = psycopg2.sql.Identifier(_TEST_DB_NAME)
    with admin.cursor() as cur:
        cur.execute(psycopg2.sql.SQL("DROP DATABASE IF EXISTS {}").format(ident))
        cur.execute(psycopg2.sql.SQL("CREATE DATABASE {}").format(ident))

    os.environ["DB_HOST"] = _DB_HOST
    os.environ["DB_PORT"] = _DB_PORT
    os.environ["DB_USER"] = _DB_USER
    os.environ["DB_PASSWORD"] = _DB_PASSWORD
    os.environ["DB_NAME"] = _TEST_DB_NAME
    # Development-mode auth: the entity recipe generates auth-protected routers and
    # tests asserting 401 without credentials, so auth must be live in tests and the
    # dev token must match the fixtures at the bottom of this file.
    os.environ["AUTH_MODE"] = "development"
    os.environ["AUTH_DEV_TOKEN"] = "dev-secret-token"

    backend_root = os.path.join(os.path.dirname(__file__), "..")
    cfg = AlembicConfig(os.path.join(backend_root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_root, "migrations"))
    alembic_command.upgrade(cfg, "head")

    yield

    admin2 = _admin_connection()
    with admin2.cursor() as cur:
        cur.execute(
            psycopg2.sql.SQL(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()"
            ),
            (_TEST_DB_NAME,),
        )
        cur.execute(psycopg2.sql.SQL("DROP DATABASE IF EXISTS {}").format(ident))
    admin2.close()
    admin.close()


@pytest.fixture()
def client(_test_database) -> Generator[TestClient, None, None]:
    """A TestClient against the app, with tables truncated after each test.

    Authenticates the way a real caller does: every request carries the
    development bearer token by default, because the app gates every
    /api route except GET /api/health.  A test that needs an
    unauthenticated request must build its own TestClient against
    ``app.main.app``.
    """
    from app.main import app

    dev_token = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")
    with TestClient(app, headers={"Authorization": f"Bearer {dev_token}"}) as c:
        yield c

    conn = psycopg2.connect(
        host=_DB_HOST,
        port=_DB_PORT,
        user=_DB_USER,
        password=_DB_PASSWORD,
        dbname=_TEST_DB_NAME,
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'alembic_version'"
        )
        tables = [r[0] for r in cur.fetchall()]
        for table in tables:
            cur.execute(
                psycopg2.sql.SQL("TRUNCATE TABLE {} CASCADE").format(
                    psycopg2.sql.Identifier(table)
                )
            )
    conn.close()


# ---------------------------------------------------------------------------
# Auth fixtures (development mode) — the generated entity tests use these.
# ---------------------------------------------------------------------------


@pytest.fixture()
def admin_token():
    """Development-mode admin token."""
    return "dev-secret-token:admin"


@pytest.fixture()
def user_token():
    """Development-mode non-admin token."""
    return "dev-secret-token:user"


@pytest.fixture()
def admin_headers(admin_token):
    """Authorization headers for an admin user."""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture()
def user_headers(user_token):
    """Authorization headers for a non-admin user."""
    return {"Authorization": f"Bearer {user_token}"}
