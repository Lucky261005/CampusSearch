"""Keyword (BM25), semantic (pgvector) and hybrid (RRF) retrieval over one result type.

Reciprocal Rank Fusion:  rrf(d) = sum over rankings i of  w_i / (k + rank_i(d)),
rank is 1-based, documents absent from a ranking contribute 0. Defaults k = 60, w_i = 1.
"""

from dataclasses import dataclass
from datetime import date

import psycopg
from pydantic import BaseModel, Field

from app.embeddings import Embedder, to_pgvector
from app.models import Document
from app.search import SearchIndex, snippet

CANDIDATES = 100  # per-ranking pool for semantic/hybrid
RRF_K = 60


class Filters(BaseModel):
    campus: str | None = None
    category: str | None = None
    programme: str | None = None
    semester: int | None = Field(default=None, ge=1, le=12)
    date_from: date | None = None
    date_to: date | None = None

    def keep(self, d: Document) -> bool:
        for f in ("campus", "category", "programme"):
            want = getattr(self, f)
            if want and (getattr(d, f) or "").casefold() != want.casefold():
                return False
        if self.semester is not None and d.semester != self.semester:
            return False
        if self.date_from or self.date_to:
            if d.publication_date is None:
                return False
            if self.date_from and d.publication_date < self.date_from:
                return False
            if self.date_to and d.publication_date > self.date_to:
                return False
        return True

    def sql(self) -> tuple[str, list]:
        """Same semantics as keep(), as a parameterized WHERE fragment on alias d."""
        clauses, params = [], []
        for f in ("campus", "category", "programme"):
            if want := getattr(self, f):
                clauses.append(f"lower(d.{f}) = lower(%s)")
                params.append(want)
        if self.semester is not None:
            clauses.append("d.semester = %s")
            params.append(self.semester)
        if self.date_from:
            clauses.append("d.publication_date >= %s")
            params.append(self.date_from)
        if self.date_to:
            clauses.append("d.publication_date <= %s")
            params.append(self.date_to)
        return " AND ".join(clauses) or "TRUE", params


@dataclass
class Passage:
    doc: Document
    chunk_id: str | None
    page: int | None
    text: str
    score: float
    similarity: float | None = None  # cosine similarity of the best chunk, when known


def keyword(index: SearchIndex, q: str, filters: Filters, width: int = 220) -> list[Passage]:
    return [
        Passage(h.doc, None, None, snippet(h.doc.content, q, width), h.score)
        for h in index.search(q, filters.keep)
    ]


def semantic(
    conn: psycopg.Connection,
    embedder: Embedder,
    q: str,
    filters: Filters,
    limit: int = CANDIDATES,
    doc_ids: list[str] | None = None,
) -> list[Passage]:
    """Best chunk per document, documents ordered by cosine similarity (ties by doc id)."""
    if not q.strip():
        return []
    where, params = filters.sql()
    if doc_ids is not None:
        where += " AND d.id = ANY(%s)"
        params.append(doc_ids)
    qvec = to_pgvector(embedder.encode([q])[0])
    rows = conn.execute(
        f"""
        SELECT * FROM (
            SELECT DISTINCT ON (c.document_id)
                   c.id AS chunk_id, c.page, c.content AS chunk_text,
                   c.embedding <=> %s::vector AS distance, d.*
            FROM chunks c JOIN documents d ON d.id = c.document_id
            WHERE c.model = %s AND {where}
            ORDER BY c.document_id, distance, c.position
        ) best
        ORDER BY distance, id
        LIMIT %s
        """,
        [qvec, embedder.name, *params, limit],
    ).fetchall()
    out = []
    for r in rows:
        sim = 1 - r.pop("distance")
        chunk_id, page, text = r.pop("chunk_id"), r.pop("page"), r.pop("chunk_text")
        out.append(Passage(Document(**r), chunk_id, page, text, sim, sim))
    return out


def rrf(rankings: list[list[str]], k: int = RRF_K, weights: list[float] | None = None
        ) -> list[tuple[str, float]]:
    if k <= 0:
        raise ValueError("k must be positive")
    weights = weights or [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("one weight per ranking")
    scores: dict[str, float] = {}
    for ranking, w in zip(rankings, weights):
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + w / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def hybrid(
    conn: psycopg.Connection,
    embedder: Embedder,
    index: SearchIndex,
    q: str,
    filters: Filters,
    k: int = RRF_K,
) -> list[Passage]:
    kw = keyword(index, q, filters)[:CANDIDATES]
    sem = semantic(conn, embedder, q, filters)
    by_id = {p.doc.id: p for p in sem}
    # keyword-only hits still get their best-matching chunk as evidence when one exists
    missing = [p.doc.id for p in kw if p.doc.id not in by_id]
    if missing:
        by_id |= {p.doc.id: p for p in semantic(conn, embedder, q, filters, doc_ids=missing)}
    kw_by_id = {p.doc.id: p for p in kw}
    fused = rrf([[p.doc.id for p in kw], [p.doc.id for p in sem]], k=k)
    out = []
    for doc_id, score in fused:
        p = by_id.get(doc_id) or kw_by_id[doc_id]
        out.append(Passage(p.doc, p.chunk_id, p.page, p.text, score, p.similarity))
    return out
