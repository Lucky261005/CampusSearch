import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from app import db, rag, retrieval
from app.embeddings import Embedder, get_embedder
from app.models import AskResponse, SearchHit, SearchResponse
from app.retrieval import Filters
from app.search import SearchIndex

log = logging.getLogger("campussearch")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        with db.connect() as conn:
            db.init_schema(conn)
            docs = db.all_documents(conn)
            app.state.chunk_index = retrieval.load_chunk_index(conn)
    except Exception as e:  # start anyway so /health works without a DB
        log.warning("database unavailable, starting with empty index: %s", e)
        docs = []
        app.state.chunk_index = SearchIndex()
    app.state.index = SearchIndex(docs)
    yield


app = FastAPI(title="CampusSearch API", lifespan=lifespan)


def _index(request: Request) -> SearchIndex:
    return getattr(request.app.state, "index", None) or SearchIndex()


def _embedder(request: Request) -> Embedder | None:
    """Injected embedder (tests) or the shared lazily-loaded model."""
    if hasattr(request.app.state, "embedder"):
        return request.app.state.embedder
    return get_embedder()[0]


def _conn(request: Request):
    return db.connect(getattr(request.app.state, "db_url", db.DATABASE_URL))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/search", response_model=SearchResponse)
def search(
    request: Request,
    q: Annotated[str, Query(max_length=500)] = "",
    mode: Literal["keyword", "semantic", "hybrid"] = "keyword",
    campus: str | None = None,
    category: str | None = None,
    programme: str | None = None,
    semester: Annotated[int | None, Query(ge=1, le=12)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    include_illustrative: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchResponse:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, "date_from must be on or before date_to")
    filters = Filters(campus=campus, category=category, programme=programme,
                      semester=semester, date_from=date_from, date_to=date_to,
                      include_illustrative=include_illustrative)

    if mode == "keyword":
        passages = retrieval.keyword(_index(request), q, filters)
    else:
        embedder = _embedder(request)
        if embedder is None:
            raise HTTPException(503, f"{mode} search unavailable: embedding model not loaded")
        try:
            with _conn(request) as conn:
                passages = (retrieval.semantic(conn, embedder, q, filters) if mode == "semantic"
                            else retrieval.hybrid(conn, embedder, _index(request), q, filters))
        except Exception as e:
            log.warning("%s search failed: %s", mode, e)
            raise HTTPException(503, f"{mode} search unavailable: database error") from None

    page = passages[offset : offset + limit]
    return SearchResponse(
        query=q,
        mode=mode,
        total=len(passages),
        limit=limit,
        offset=offset,
        results=[
            SearchHit(
                **p.doc.model_dump(include=set(SearchHit.model_fields)),
                chunk_id=p.chunk_id,
                score=round(p.score, 6),
                snippet=p.text if p.chunk_id is None else p.text[:300],
            )
            for p in page
        ],
    )


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    filters: Filters = Filters()
    top_k: int = Field(default=5, ge=1, le=8)

    @field_validator("question")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question must not be blank")
        return v.strip()


@app.post("/api/ask", response_model=AskResponse)
def ask(request: Request, body: AskRequest) -> AskResponse:
    f = body.filters
    if f.date_from and f.date_to and f.date_from > f.date_to:
        raise HTTPException(400, "date_from must be on or before date_to")
    embedder = _embedder(request)
    info: dict = {"mode": "hybrid", "semantic_available": embedder is not None}
    chunks = getattr(request.app.state, "chunk_index", None) or SearchIndex()
    passages = None
    if embedder is not None:
        try:
            with _conn(request) as conn:
                passages, info["mode"] = retrieval.answer_passages(
                    conn, embedder, _index(request), chunks, body.question, f, body.top_k)
        except Exception as e:
            log.warning("hybrid retrieval failed, using keyword: %s", e)
    if passages is None:
        passages, info["mode"] = retrieval.answer_passages(
            None, None, _index(request), chunks, body.question, f, body.top_k)
    info["retrieved"] = len(passages)
    llm = getattr(request.app.state, "llm", rag.groq_chat)
    return rag.answer(body.question, passages, llm, info)
