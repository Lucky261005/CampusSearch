from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, embeddings
from app.embeddings import chunk_text
from app.ingest import build_document, ingest_document, ingest_records, load_json_records
from app.main import app
from app.reindex import index_documents
from app.retrieval import Filters, hybrid, keyword, rrf, semantic
from app.search import SearchIndex

SEED = Path(__file__).parents[1] / "data" / "seed_documents.json"


def rec(url, content, **kw):
    return {"title": "T", "content": content, "source_url": url,
            "source_type": "json", "provenance": "illustrative", **kw}


# --- chunking -----------------------------------------------------------------

def test_chunking_overlaps_and_covers_all_words():
    words = [f"w{i}" for i in range(320)]
    chunks = chunk_text(" ".join(words), size=150, overlap=30)
    assert [len(c.split()) for c in chunks] == [150, 150, 80]
    assert chunks[0].split()[-30:] == chunks[1].split()[:30]
    assert chunks[-1].split()[-1] == "w319"


def test_chunking_edge_cases():
    assert chunk_text("") == [] and chunk_text("   ") == []
    assert chunk_text("one two") == ["one two"]
    assert len(chunk_text(" ".join(["x"] * 150))) == 1  # exactly one window, no empty tail
    with pytest.raises(ValueError):
        chunk_text("a", size=10, overlap=10)


# --- RRF ---------------------------------------------------------------------

def test_rrf_formula_and_order():
    fused = dict(rrf([["a", "b", "c"], ["b", "c", "d"]], k=60))
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["d"] == pytest.approx(1 / 63)
    assert [d for d, _ in rrf([["a", "b", "c"], ["b", "c", "d"]])] == ["b", "c", "a", "d"]


def test_rrf_ties_weights_and_validation():
    assert rrf([["x"], ["y"]]) == [("x", 1 / 61), ("y", 1 / 61)]  # tie -> id order
    assert rrf([["x"], ["y"]], weights=[1, 2])[0][0] == "y"
    assert rrf([[], []]) == []
    with pytest.raises(ValueError):
        rrf([["x"]], k=0)
    with pytest.raises(ValueError):
        rrf([["x"]], weights=[1, 2])


# --- embeddings in pgvector ------------------------------------------------------

def test_reindex_persists_384_dim_and_is_idempotent(conn, fake_embedder):
    ingest_records(conn, load_json_records(SEED))
    first = index_documents(conn, fake_embedder)
    assert first["documents"] == 12 and first["embedded"] == 12 and first["errors"] == 0
    row = conn.execute("SELECT vector_dims(embedding) AS d, model, id, document_id "
                       "FROM chunks LIMIT 1").fetchone()
    assert row["d"] == 384 and row["model"] == "fake-test-model"
    assert row["id"] == f"{row['document_id']}:0"

    calls = fake_embedder.calls
    second = index_documents(conn, fake_embedder)
    assert second["embedded"] == 0 and second["reused"] == 12
    assert fake_embedder.calls == calls  # nothing re-encoded
    assert conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"] == 12


def test_reindex_handles_changed_shrunk_and_new_model(conn, fake_embedder):
    long = " ".join(f"w{i}" for i in range(390))  # windows at 0, 120, 240 -> 3 chunks
    ingest_document(conn, build_document(rec("u:a", long)))
    assert index_documents(conn, fake_embedder)["embedded"] == 3

    ingest_document(conn, build_document(rec("u:a", "hostel rules only")))
    c = index_documents(conn, fake_embedder)
    assert c["embedded"] == 1 and c["deleted"] == 2
    assert conn.execute("SELECT content FROM chunks").fetchone()["content"] == "hostel rules only"

    fake_embedder.name = "fake-test-model-v2"
    assert index_documents(conn, fake_embedder)["embedded"] == 1  # model change re-embeds


def test_semantic_orders_by_similarity_and_filters(conn, fake_embedder):
    ingest_records(conn, [
        rec("u:hostel", "hostel rooms and hostel rules", campus="Kengeri", semester=1),
        rec("u:library", "library books borrowing", campus="Central"),
        rec("u:mixed", "hostel library", campus="Central", semester=1),
    ])
    index_documents(conn, fake_embedder)
    hits = semantic(conn, fake_embedder, "hostel rules", Filters())
    assert [h.doc.source_url for h in hits][:2] == ["u:hostel", "u:mixed"]
    assert hits[0].similarity > hits[1].similarity and hits[0].chunk_id
    assert [h.doc.source_url for h in
            semantic(conn, fake_embedder, "hostel", Filters(campus="central"))] == \
        ["u:mixed", "u:library"]
    assert [h.doc.source_url for h in
            semantic(conn, fake_embedder, "hostel", Filters(campus="Central", semester=1))] == \
        ["u:mixed"]
    assert semantic(conn, fake_embedder, "   ", Filters()) == []


def test_hybrid_fuses_and_attaches_chunks(conn, fake_embedder):
    ingest_records(conn, load_json_records(SEED))
    index_documents(conn, fake_embedder)
    index = SearchIndex(db.all_documents(conn))
    hits = hybrid(conn, fake_embedder, index, "hostel accommodation", Filters())
    assert hits[0].doc.source_url.endswith("/hostel")
    assert all(h.chunk_id for h in hits)  # every doc is indexed, so evidence is a chunk
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    kw = {p.doc.id for p in keyword(index, "hostel accommodation", Filters())}
    assert kw <= {h.doc.id for h in hits}


# --- API: modes, filters, missing model -------------------------------------------

@pytest.fixture
def client(conn, fake_embedder, _test_db):
    ingest_records(conn, load_json_records(SEED))
    index_documents(conn, fake_embedder)
    app.state.index = SearchIndex(db.all_documents(conn))
    app.state.embedder = fake_embedder
    app.state.db_url = _test_db
    yield TestClient(app)
    for attr in ("index", "embedder", "db_url"):
        delattr(app.state, attr)


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_modes_share_schema_and_filters(client, mode):
    res = client.get("/api/search", params={"q": "examination registration", "mode": mode,
                                            "campus": "Kengeri"})
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == mode and body["results"]
    for r in body["results"]:
        assert r["campus"] == "Kengeri"
        assert (r["chunk_id"] is None) == (mode == "keyword")
    assert body["results"][0]["source_url"].endswith("exam-registration")


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_date_filter_in_vector_modes(client, mode):
    res = client.get("/api/search", params={"q": "calendar", "mode": mode,
                                            "date_from": "2026-06-01", "date_to": "2026-12-31"})
    dates = [r["publication_date"] for r in res.json()["results"]]
    assert dates and all("2026-06-01" <= d <= "2026-12-31" for d in dates)


def test_missing_model_returns_503_and_keyword_still_works(client):
    app.state.embedder = None
    for mode in ["semantic", "hybrid"]:
        res = client.get("/api/search", params={"q": "hostel", "mode": mode})
        assert res.status_code == 503 and "unavailable" in res.json()["detail"]
    assert client.get("/api/search", params={"q": "hostel"}).json()["total"] > 0


def test_get_embedder_caches_load_failure(monkeypatch):
    calls = []

    def boom(*a, **kw):
        calls.append(1)
        raise OSError("no network")

    monkeypatch.setattr(embeddings, "SentenceTransformerEmbedder", boom)
    monkeypatch.setattr(embeddings, "_embedder", None)
    monkeypatch.setattr(embeddings, "_error", None)
    assert embeddings.get_embedder() == (None, "embedding model could not be loaded")
    assert embeddings.get_embedder()[0] is None and len(calls) == 1
