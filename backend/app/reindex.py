"""Chunk + embed stored documents into pgvector. Reuses unchanged embeddings.

CLI:  python -m app.reindex
"""

import sys

import psycopg

from app import db
from app.embeddings import Embedder, chunk_text, embed_input, get_embedder, text_hash, to_pgvector


def index_documents(conn: psycopg.Connection, embedder: Embedder) -> dict[str, int]:
    counts = {"documents": 0, "embedded": 0, "reused": 0, "deleted": 0, "errors": 0}
    for doc in db.all_documents(conn):
        try:
            chunks = chunk_text(doc.content)
            inputs = [embed_input(doc.title, c) for c in chunks]
            existing = {
                r["position"]: r
                for r in conn.execute(
                    "SELECT position, content_hash, model FROM chunks WHERE document_id = %s",
                    (doc.id,),
                )
            }
            todo = [
                i for i, text in enumerate(inputs)
                if not (
                    (row := existing.get(i))
                    and row["content_hash"] == text_hash(text)
                    and row["model"] == embedder.name
                )
            ]
            vectors = embedder.encode([inputs[i] for i in todo]) if todo else []
            for i, vec in zip(todo, vectors):
                conn.execute(
                    "INSERT INTO chunks (id, document_id, position, page, content, content_hash,"
                    " model, embedding) VALUES (%s, %s, %s, NULL, %s, %s, %s, %s::vector)"
                    " ON CONFLICT (document_id, position) DO UPDATE SET content = EXCLUDED.content,"
                    " content_hash = EXCLUDED.content_hash, model = EXCLUDED.model,"
                    " embedding = EXCLUDED.embedding",
                    (f"{doc.id}:{i}", doc.id, i, chunks[i],
                     text_hash(inputs[i]), embedder.name, to_pgvector(vec)),
                )
            deleted = conn.execute(
                "DELETE FROM chunks WHERE document_id = %s AND position >= %s",
                (doc.id, len(inputs)),
            ).rowcount
            conn.commit()
        except Exception as e:  # one bad document must not abort the run
            conn.rollback()
            print(f"reindex failed for {doc.source_url}: {e}", file=sys.stderr)
            counts["errors"] += 1
            continue
        counts["documents"] += 1
        counts["embedded"] += len(todo)
        counts["reused"] += len(inputs) - len(todo)
        counts["deleted"] += deleted
    return counts


def main() -> None:
    embedder, error = get_embedder()
    if embedder is None:
        sys.exit(f"cannot reindex: {error}")
    with db.connect() as conn:
        db.init_schema(conn)
        print(index_documents(conn, embedder))


if __name__ == "__main__":
    main()
