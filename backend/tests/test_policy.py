"""Corpus policy: illustrative records never reach normal users (Phase 6B, workstream 1)."""

import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.ingest import ingest_records
from app.main import app
from app.reindex import index_documents
from app.retrieval import Filters
from app.search import SearchIndex

FAKE_FEE = {"title": "[Illustrative] Hostel fee", "source_url": "illustrative://t/hostel-fee",
            "content": "ILLUSTRATIVE SAMPLE. The hostel fee is Rs 85,000 per year for all hostel residents.",
            "source_type": "json", "provenance": "illustrative"}
OFFICIAL = {"title": "Hostel and campus facilities", "source_url": "https://example.org/hostel",
            "content": "Hostel accommodation is available on campus; hostel rules and timings apply.",
            "source_type": "html", "provenance": "official_web"}
SUPPLIED = {"title": "Hostel notice", "source_url": "supplied://n/hostel.pdf",
            "content": "Notice about hostel admission forms for residents.",
            "source_type": "pdf", "provenance": "supplied_document"}


@pytest.fixture
def client(conn, fake_embedder, _test_db):
    ingest_records(conn, [FAKE_FEE, OFFICIAL, SUPPLIED])
    index_documents(conn, fake_embedder)
    app.state.index = SearchIndex(db.all_documents(conn))
    app.state.embedder = fake_embedder
    app.state.db_url = _test_db
    yield TestClient(app)  # a normal client: no opt-in
    for attr in ("index", "embedder", "db_url", "llm"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_illustrative_fee_record_is_hidden_from_normal_search(client, mode):
    body = client.get("/api/search", params={"q": "hostel fee", "mode": mode}).json()
    provenances = [r["provenance"] for r in body["results"]]
    assert "illustrative" not in provenances
    assert provenances and provenances[0] in ("official_web", "supplied_document")
    assert all("85,000" not in r["snippet"] for r in body["results"])


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_opt_in_shows_it_labelled_and_never_as_official(client, mode):
    body = client.get("/api/search", params={"q": "hostel fee", "mode": mode,
                                             "include_illustrative": True}).json()
    fake = [r for r in body["results"] if r["source_url"] == FAKE_FEE["source_url"]]
    assert fake and fake[0]["provenance"] == "illustrative"
    supplied = [r for r in body["results"] if r["source_url"] == SUPPLIED["source_url"]]
    assert supplied and supplied[0]["provenance"] == "supplied_document"  # stays distinguishable


def test_ask_never_cites_illustrative_by_default(client):
    seen = []

    def llm(messages):
        seen.append(messages[1]["content"])
        return json.dumps({"answer": "", "citations": [], "insufficient_evidence": True})

    app.state.llm = llm
    body = client.post("/api/ask", json={"question": "What is the hostel fee?"}).json()
    assert all(s["provenance"] != "illustrative" for s in body["sources"])
    assert "85,000" not in "".join(seen) and "ILLUSTRATIVE" not in "".join(seen)


def test_filter_policy_is_identical_in_python_and_sql():
    docs = {p: type("D", (), {"provenance": p, "campus": None, "category": None, "programme": None,
                              "semester": None, "publication_date": None})()
            for p in ("illustrative", "official_web", "supplied_document")}
    default, opted = Filters(), Filters(include_illustrative=True)
    assert [default.keep(d) for d in docs.values()] == [False, True, True]
    assert all(opted.keep(d) for d in docs.values())
    assert "d.provenance <> 'illustrative'" in default.sql()[0]
    assert "illustrative" not in opted.sql()[0]
