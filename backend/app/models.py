from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal["json", "text", "html"]
# "illustrative" = synthetic sample record, never to be shown as official information
Provenance = Literal["official_web", "illustrative"]


class Document(BaseModel):
    id: str
    title: str
    content: str
    source_url: str
    source_type: SourceType
    provenance: Provenance
    campus: str | None = None
    category: str | None = None
    programme: str | None = None
    semester: int | None = Field(default=None, ge=1, le=12)
    publication_date: date | None = None
    content_hash: str
    fetched_at: datetime | None = None
    updated_at: datetime | None = None


class SearchHit(BaseModel):
    id: str
    chunk_id: str | None  # set for semantic/hybrid hits backed by an embedded chunk
    title: str
    source_url: str
    source_type: SourceType
    provenance: Provenance
    campus: str | None
    category: str | None
    programme: str | None
    semester: int | None
    publication_date: date | None
    score: float
    snippet: str


class SearchResponse(BaseModel):
    query: str
    mode: str
    total: int
    limit: int
    offset: int
    results: list[SearchHit]


class Citation(BaseModel):
    n: int
    document_id: str
    chunk_id: str | None
    title: str
    source_url: str
    provenance: Provenance
    provenance_label: str
    page: int | None
    excerpt: str
    quote: str | None = None  # verified verbatim substring of excerpt


class SourcePassage(Citation):
    score: float


class AskResponse(BaseModel):
    question: str
    status: Literal["answered", "insufficient_evidence", "fallback"]
    answer: str | None
    message: str
    citations: list[Citation]
    sources: list[SourcePassage]
    retrieval: dict
