import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request

from app import db
from app.models import Document, SearchHit, SearchResponse
from app.search import SearchIndex, snippet

log = logging.getLogger("campussearch")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        with db.connect() as conn:
            db.init_schema(conn)
            docs = db.all_documents(conn)
    except Exception as e:  # start anyway so /health works without a DB
        log.warning("database unavailable, starting with empty index: %s", e)
        docs = []
    app.state.index = SearchIndex(docs)
    yield


app = FastAPI(title="CampusSearch API", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/search", response_model=SearchResponse)
def search(
    request: Request,
    q: Annotated[str, Query(max_length=500)] = "",
    # semantic / hybrid get added here in Phase 3
    mode: Literal["keyword"] = "keyword",
    campus: str | None = None,
    category: str | None = None,
    programme: str | None = None,
    semester: Annotated[int | None, Query(ge=1, le=12)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchResponse:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, "date_from must be on or before date_to")
    index: SearchIndex = getattr(request.app.state, "index", None) or SearchIndex()

    def keep(d: Document) -> bool:
        if campus and (d.campus or "").casefold() != campus.casefold():
            return False
        if category and (d.category or "").casefold() != category.casefold():
            return False
        if programme and (d.programme or "").casefold() != programme.casefold():
            return False
        if semester is not None and d.semester != semester:
            return False
        if (date_from or date_to) and d.publication_date is None:
            return False
        if date_from and d.publication_date < date_from:
            return False
        if date_to and d.publication_date > date_to:
            return False
        return True

    hits = index.search(q, keep)
    page = hits[offset : offset + limit]
    return SearchResponse(
        query=q,
        mode=mode,
        total=len(hits),
        limit=limit,
        offset=offset,
        results=[
            SearchHit(
                **h.doc.model_dump(include=set(SearchHit.model_fields)),
                score=round(h.score, 6),
                snippet=snippet(h.doc.content, q),
            )
            for h in page
        ],
    )
