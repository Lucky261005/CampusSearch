import hashlib
import math
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
            # test DB only: skip commit fsync (slow on Docker/WSL2 volumes)
            c.execute(f'ALTER DATABASE "{name}" SET synchronous_commit = off')
    except psycopg.OperationalError as e:
        pytest.skip(f"PostgreSQL not available: {e}")
    return TEST_DATABASE_URL


class FixtureClient:
    """TestClient whose requests opt in to illustrative records, which most fixtures are.
    Normal API callers never see them unless they pass include_illustrative explicitly."""

    def __init__(self, app):
        from fastapi.testclient import TestClient

        self._client = TestClient(app)

    def get(self, url, params=None, **kw):
        return self._client.get(url, params={"include_illustrative": True, **(params or {})}, **kw)

    def post(self, url, json=None, **kw):
        if isinstance(json, dict) and "question" in json:
            json = {**json, "filters": {"include_illustrative": True, **json.get("filters", {})}}
        return self._client.post(url, json=json, **kw)


class FakeEmbedder:
    """Test-only stand-in: hashed bag-of-words, 384-dim, unit length. Never used at runtime."""

    name = "fake-test-model"

    def __init__(self) -> None:
        self.calls = 0

    def encode(self, texts: list[str]) -> list[list[float]]:
        from app.search import tokenize

        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * 384
            for tok in tokenize(t):
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 384] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def conn(_test_db):
    with db.connect(_test_db) as c:
        db.init_schema(c)
        c.execute("DELETE FROM documents")  # cascades to chunks; faster than TRUNCATE here
        c.commit()
        yield c
