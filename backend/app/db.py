import os

import psycopg
from psycopg.rows import dict_row

from app.models import Document

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://campus:campus@localhost:5432/campussearch"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    content          TEXT NOT NULL,
    source_url       TEXT NOT NULL UNIQUE,
    source_type      TEXT NOT NULL,
    provenance       TEXT NOT NULL,
    campus           TEXT,
    category         TEXT,
    programme        TEXT,
    semester         INTEGER,
    publication_date DATE,
    content_hash     TEXT NOT NULL UNIQUE,
    fetched_at       TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

COLUMNS = list(Document.model_fields)


def connect(url: str = DATABASE_URL) -> psycopg.Connection:
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=3)


def init_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA)
    conn.commit()


def get_by_url(conn: psycopg.Connection, url: str) -> Document | None:
    row = conn.execute("SELECT * FROM documents WHERE source_url = %s", (url,)).fetchone()
    return Document(**row) if row else None


def hash_owner(conn: psycopg.Connection, content_hash: str) -> str | None:
    row = conn.execute(
        "SELECT source_url FROM documents WHERE content_hash = %s", (content_hash,)
    ).fetchone()
    return row["source_url"] if row else None


def upsert(conn: psycopg.Connection, doc: Document) -> None:
    cols = [c for c in COLUMNS if c != "updated_at"]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "id")
    conn.execute(
        f"INSERT INTO documents ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
        f"ON CONFLICT (source_url) DO UPDATE SET {updates}, updated_at = now()",
        [getattr(doc, c) for c in cols],
    )
    conn.commit()


def all_documents(conn: psycopg.Connection) -> list[Document]:
    return [Document(**r) for r in conn.execute("SELECT * FROM documents ORDER BY id")]
