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

CREATE EXTENSION IF NOT EXISTS vector;

-- shortcut: no ANN index; exact cosine scan is correct and fast for a small corpus.
-- Add HNSW (with pgvector iterative scans for filtered queries) past ~100k chunks.
CREATE TABLE IF NOT EXISTS chunks (
    id           TEXT PRIMARY KEY,                -- "<document_id>:<position>"
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    page         INTEGER,
    content      TEXT NOT NULL,
    content_hash TEXT NOT NULL,                   -- hash of the embedded text
    model        TEXT NOT NULL,
    embedding    vector(384) NOT NULL,
    UNIQUE (document_id, position)
);

-- Phase 6A migration: idempotent, upgrades databases created by earlier versions in place
ALTER TABLE documents ADD COLUMN IF NOT EXISTS extraction_status TEXT NOT NULL DEFAULT 'ok';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS extraction_note TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS page_starts INTEGER[];
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
    """Insert or replace a document. Its old chunks go in the same transaction, so stale
    text is never searchable; `python -m app.reindex` re-embeds the new content."""
    cols = [c for c in COLUMNS if c != "updated_at"]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "id")
    conn.execute(
        f"INSERT INTO documents ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
        f"ON CONFLICT (source_url) DO UPDATE SET {updates}, updated_at = now()",
        [getattr(doc, c) for c in cols],
    )
    conn.execute("DELETE FROM chunks WHERE document_id = %s", (doc.id,))
    conn.commit()


def update_metadata(conn: psycopg.Connection, doc: Document, fields: tuple[str, ...]) -> None:
    """Update descriptive fields of an unchanged document; content and chunks stay as they are."""
    conn.execute(
        f"UPDATE documents SET {', '.join(f'{f} = %s' for f in fields)}, updated_at = now() "
        "WHERE source_url = %s",
        [getattr(doc, f) for f in fields] + [doc.source_url],
    )
    conn.commit()


def delete_by_url(conn: psycopg.Connection, url: str) -> bool:
    """Delete a document; its chunks cascade."""
    deleted = conn.execute("DELETE FROM documents WHERE source_url = %s", (url,)).rowcount
    conn.commit()
    return bool(deleted)


def all_documents(conn: psycopg.Connection) -> list[Document]:
    return [Document(**r) for r in conn.execute("SELECT * FROM documents ORDER BY id")]
