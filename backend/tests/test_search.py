import math

import pytest

from app.ingest import build_document
from app.search import SearchIndex, snippet, tokenize


def doc(url: str, content: str, title: str = "t", **meta):
    return build_document(
        {"title": title, "content": content, "source_url": url,
         "source_type": "json", "provenance": "illustrative", **meta}
    )


def test_tokenize_normalizes_case_punctuation_unicode_and_stopwords():
    assert tokenize("The Academic-Calendar, 2026!") == ["academic", "calendar", "2026"]
    assert tokenize("ＦＥＥ") == ["fee"]  # full-width -> ASCII via NFKC
    assert tokenize("   ") == []


def test_postings_tf_df_and_lengths():
    a, b = doc("u:a", "fee fee hostel"), doc("u:b", "fee library")
    idx = SearchIndex([a, b])
    assert idx.postings["fee"] == {a.id: 2, b.id: 1}
    assert idx.df("fee") == 2 and idx.df("hostel") == 1 and idx.df("nope") == 0
    assert idx.doc_len[a.id] == 4  # title "t" + 3 content tokens
    assert idx.avgdl == 3.5


def test_bm25_score_matches_formula():
    a, b = doc("u:a", "fee fee hostel"), doc("u:b", "library books")
    idx = SearchIndex([a, b], k1=1.2, b=0.75)
    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    tf, dl, avgdl = 2, 4, 3.5
    expected = idf * tf * 2.2 / (tf + 1.2 * (1 - 0.75 + 0.75 * dl / avgdl))
    [hit] = idx.search("fee")
    assert hit.doc.id == a.id and hit.score == pytest.approx(expected)


def test_relevant_doc_ranks_above_unrelated():
    idx = SearchIndex([doc("u:1", "hostel rules and timings"),
                       doc("u:2", "academic calendar for odd semester"),
                       doc("u:3", "library borrowing")])
    assert [h.doc.source_url for h in idx.search("academic calendar")] == ["u:2"]


def test_higher_term_frequency_ranks_higher():
    idx = SearchIndex([doc("u:lo", "exam notice general info"),
                       doc("u:hi", "exam exam exam notice")])
    assert [h.doc.source_url for h in idx.search("exam")] == ["u:hi", "u:lo"]


def test_shorter_document_ranks_higher_at_equal_tf():
    long = "exam " + " ".join(f"filler{i}" for i in range(40))
    idx = SearchIndex([doc("u:long", long), doc("u:short", "exam schedule")])
    assert [h.doc.source_url for h in idx.search("exam")] == ["u:short", "u:long"]


def test_b_zero_disables_length_normalization():
    long = "exam " + " ".join(f"filler{i}" for i in range(40))
    idx = SearchIndex([doc("u:long", long), doc("u:short", "exam schedule")], b=0)
    s = {h.doc.source_url: h.score for h in idx.search("exam")}
    assert s["u:long"] == pytest.approx(s["u:short"])


def test_rare_term_contributes_more_than_common_term():
    idx = SearchIndex([doc("u:1", "fee scholarship"), doc("u:2", "fee hostel"),
                       doc("u:3", "fee library")])
    assert idx.idf("scholarship") > idx.idf("fee") > 0


def test_repeated_query_terms_count_once():
    idx = SearchIndex([doc("u:1", "fee hostel"), doc("u:2", "fee fee")])
    assert [(h.doc.id, h.score) for h in idx.search("fee fee fee")] == \
           [(h.doc.id, h.score) for h in idx.search("fee")]


def test_empty_whitespace_and_unknown_queries_return_nothing():
    idx = SearchIndex([doc("u:1", "fee")])
    assert idx.search("") == [] and idx.search("   ") == [] and idx.search("zzz") == []
    assert SearchIndex().search("fee") == []


def test_ties_break_by_id_deterministically():
    docs = [doc(f"u:{i}", "same words here") for i in range(5)]
    hits = SearchIndex(list(reversed(docs))).search("words")
    assert len({h.score for h in hits}) == 1
    assert [h.doc.id for h in hits] == sorted(d.id for d in docs)


def test_upsert_and_remove_update_index():
    a = doc("u:a", "hostel rules")
    idx = SearchIndex([a, doc("u:b", "library")])
    idx.upsert(doc("u:a", "scholarship forms"))
    assert idx.search("hostel") == [] and "hostel" not in idx.postings
    assert [h.doc.source_url for h in idx.search("scholarship")] == ["u:a"]
    idx.remove(a.id)
    assert idx.search("scholarship") == [] and len(idx.docs) == 1


def test_empty_document_content_rejected():
    from app.ingest import IngestError
    with pytest.raises(IngestError):
        doc("u:e", "   ")


def test_invalid_bm25_params():
    with pytest.raises(ValueError):
        SearchIndex(b=1.5)


def test_snippet_centers_on_match():
    text = "x " * 200 + "academic calendar details " + "y " * 200
    s = snippet(text, "calendar")
    assert "calendar" in s and s.startswith("…") and s.endswith("…")
    assert snippet("short text", "zzz") == "short text"
