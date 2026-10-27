"""Passage selection for answers (Phase 6B, workstream 2): the passage text that is returned."""

import json

from fastapi.testclient import TestClient

from app import db
from app.ingest import build_document, ingest_document, ingest_records, pdf_to_record
from app.main import app
from app.reindex import index_documents
from app.retrieval import Filters, answer_passages, best_keyword_chunks, load_chunk_index
from app.search import SearchIndex
from test_documents import three_page_pdf

# A long official "calendar": the early chunks repeat the generic query words, the exact fact
# ("Deepavali") sits in a later chunk that a bag-of-words embedding ranks lower.
GENERIC = " ".join(["academic calendar holiday schedule for the semester"] * 30)
CALENDAR = {"title": "Calendar", "source_url": "https://example.org/calendar", "source_type": "html",
            "provenance": "official_web",
            "content": f"{GENERIC} {GENERIC} 10 (Tue) Holiday- Balipadyami, Deepavali. "
                       + " ".join(f"event{i} commencement of classes" for i in range(40))}
OTHER = {"title": "Library", "source_url": "https://example.org/library", "source_type": "html",
         "provenance": "official_web", "content": "Library borrowing rules for students and staff."}

QUERY = "Deepavali holiday academic calendar"


def setup(conn, emb):
    ingest_records(conn, [CALENDAR, OTHER])
    index_documents(conn, emb)
    return SearchIndex(db.all_documents(conn)), load_chunk_index(conn)


def test_exact_term_chunk_is_offered_alongside_semantic_best(conn, fake_embedder):
    index, chunks = setup(conn, fake_embedder)
    passages, mode = answer_passages(conn, fake_embedder, index, chunks, QUERY, Filters(), 5)
    cal = [p for p in passages if p.doc.source_url == CALENDAR["source_url"]]
    assert mode == "hybrid" and len(cal) == 2
    assert "Deepavali" not in cal[0].text  # the semantic favourite is the generic chunk...
    assert "10 (Tue) Holiday- Balipadyami, Deepavali" in cal[1].text  # ...the fact is still sent
    assert len({p.chunk_id for p in passages}) == len(passages)  # no duplicates


def test_fusion_keeps_exact_term_passage_when_budget_is_tight(conn, fake_embedder):
    extra = [{"title": f"Notice {i}", "source_url": f"https://example.org/n{i}", "source_type": "html",
              "provenance": "official_web",
              "content": f"Notice {i}: academic calendar holiday schedule semester update."} for i in range(4)]
    ingest_records(conn, [CALENDAR, OTHER, *extra])
    index_documents(conn, fake_embedder)
    index, chunks = SearchIndex(db.all_documents(conn)), load_chunk_index(conn)
    passages, _ = answer_passages(conn, fake_embedder, index, chunks, QUERY, Filters(), 3)
    assert len(passages) == 3  # more candidates than budget: the fused ranking decides
    assert any("Balipadyami, Deepavali" in p.text for p in passages)
    assert len({p.chunk_id for p in passages}) == 3


def test_single_passage_when_both_signals_agree(conn, fake_embedder):
    index, chunks = setup(conn, fake_embedder)
    passages, _ = answer_passages(conn, fake_embedder, index, chunks, "library borrowing", Filters(), 5)
    lib = [p for p in passages if p.doc.source_url == OTHER["source_url"]]
    assert len(lib) == 1 and "borrowing" in lib[0].text


def test_top_k_bounds_total_passages(conn, fake_embedder):
    index, chunks = setup(conn, fake_embedder)
    assert len(answer_passages(conn, fake_embedder, index, chunks, QUERY, Filters(), 1)[0]) == 1


def test_keyword_fallback_returns_chunks_with_pages(conn, fake_embedder):
    ingest_document(conn, build_document(pdf_to_record(three_page_pdf(), "https://example.org/rules.pdf",
                                                       "official_web")))
    index_documents(conn, fake_embedder)
    index, chunks = SearchIndex(db.all_documents(conn)), load_chunk_index(conn)
    passages, mode = answer_passages(None, None, index, chunks, "revaluation requests window",
                                     Filters(), 5)
    assert mode == "keyword"
    [p] = passages
    assert p.chunk_id and p.page == 3 and "Revaluation requests" in p.text


def test_best_keyword_chunks_respects_document_set(conn, fake_embedder):
    _, chunks = setup(conn, fake_embedder)
    cal_id = db.get_by_url(conn, CALENDAR["source_url"]).id
    assert set(best_keyword_chunks(chunks, QUERY, {cal_id})) == {cal_id}
    assert best_keyword_chunks(chunks, QUERY, set()) == {}


def test_ask_cites_the_exact_term_chunk(conn, fake_embedder, _test_db):
    index, chunks = setup(conn, fake_embedder)
    app.state.index, app.state.chunk_index = index, chunks
    app.state.embedder, app.state.db_url = fake_embedder, _test_db
    quote = "10 (Tue) Holiday- Balipadyami, Deepavali"

    def llm(messages):
        n = messages[1]["content"].split(quote)[0].rsplit("\n[", 1)[1].split("]")[0]
        return json.dumps({"answer": f"Deepavali is a holiday on 10 (Tue) [{n}].",
                           "citations": [{"source": int(n), "quote": quote}],
                           "insufficient_evidence": False})

    app.state.llm = llm
    try:
        body = TestClient(app).post("/api/ask", json={"question": QUERY}).json()
    finally:
        for a in ("index", "chunk_index", "embedder", "db_url", "llm"):
            delattr(app.state, a)
    assert body["status"] == "answered"
    [c] = body["citations"]
    assert quote in c["excerpt"] and c["source_url"] == CALENDAR["source_url"]
