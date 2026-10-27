from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal["json", "text", "html", "pdf"]
# official_web      = fetched from an official university URL
# supplied_document = file supplied locally whose official URL could not be verified
# illustrative      = synthetic sample record, never to be shown as official information
Provenance = Literal["official_web", "supplied_document", "illustrative"]


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
    extraction_status: Literal["ok", "warnings"] = "ok"
    extraction_note: str | None = None
    page_starts: list[int] | None = None  # PDF: character offset where each page begins


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
