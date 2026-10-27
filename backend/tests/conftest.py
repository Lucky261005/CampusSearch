import os

import psycopg
import pytest

from app import db

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql://campus:campus@localhost:5432/campussearch_test"
)


@pytest.fixture(scope="session")
def _test_db() -> str:
    """Create an isolated test database on the dev server; skip DB tests if unreachable."""
    base, name = TEST_DATABASE_URL.rsplit("/", 1)
    try:
        with psycopg.connect(f"{base}/postgres", autocommit=True, connect_timeout=3) as c:
            if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                c.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError as e:
        pytest.skip(f"PostgreSQL not available: {e}")
    return TEST_DATABASE_URL


@pytest.fixture
def conn(_test_db):
    with db.connect(_test_db) as c:
        db.init_schema(c)
        c.execute("TRUNCATE documents")
        c.commit()
        yield c
