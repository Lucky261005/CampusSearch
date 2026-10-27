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
