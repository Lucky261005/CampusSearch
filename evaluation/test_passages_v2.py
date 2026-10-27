"""Regression tests on the real corpus for the v2 questions whose supporting passage was missed
in Phase 6A. They check the passage text actually returned to /api/ask, not document IDs.

Skipped unless PostgreSQL holds the official corpus and the embedding model is cached locally.
"""

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from app import db  # noqa: E402
from app.embeddings import get_embedder  # noqa: E402
from app.retrieval import Filters, answer_passages, load_chunk_index  # noqa: E402
from app.search import SearchIndex  # noqa: E402

QUERIES = {q["id"]: q for q in json.loads(
    (Path(__file__).parent / "v2" / "queries.json").read_text(encoding="utf-8"))["queries"]}


@pytest.fixture(scope="module")
def corpus():
    try:
        conn = db.connect()
    except Exception as e:
        pytest.skip(f"database unavailable: {e}")
    docs = db.all_documents(conn)
    if not {QUERIES[q]["supporting_doc"] for q in ("V2-K01", "V2-K06", "V2-S02")} <= {d.id for d in docs}:
        conn.close()
        pytest.skip("official corpus not ingested (see README: Load documents)")
    embedder, why = get_embedder()
    if embedder is None:
        conn.close()
        pytest.skip(f"embedding model unavailable: {why}")
    yield conn, embedder, SearchIndex(docs), load_chunk_index(conn)
    conn.close()


def supporting_passage_position(corpus, qid):
    conn, embedder, index, chunks = corpus
    q = QUERIES[qid]
    passages, _ = answer_passages(conn, embedder, index, chunks, q["query"], Filters(), top_k=5)
    norm = lambda t: " ".join(t.split()).casefold()  # noqa: E731
    return next((i for i, p in enumerate(passages, 1)
                 if p.doc.id == q["supporting_doc"] and norm(q["supporting_quote"]) in norm(p.text)), None)


def test_v2_k01_deepavali_passage_from_long_calendar_is_returned(corpus):
    assert supporting_passage_position(corpus, "V2-K01") is not None


@pytest.mark.xfail(strict=True, reason="known: the 'Annexure-6' chunk is neither the semantic nor the "
                   "keyword best chunk of the PM-Vidyalaxmi notice (see evaluation/v2_6b/analysis.md)")
def test_v2_k06_pm_vidyalaxmi_documents_passage(corpus):
    assert supporting_passage_position(corpus, "V2-K06") is not None


@pytest.mark.xfail(strict=True, reason="known: paraphrase shares one word with the attendance rule in "
                   "the 298-chunk handbook (see evaluation/v2_6b/analysis.md)")
def test_v2_s02_attendance_passage_from_handbook(corpus):
    assert supporting_passage_position(corpus, "V2-S02") is not None
