"""The RAG integrity check must be able to fail, or 8/8 passes would mean nothing."""

import pytest

from app import rag
from app.models import Citation
from evaluation.run import citation_problems

STORED = {"content": "Hostel accommodation is allotted on application.", "document_id": "d1",
          "source_url": "illustrative://campussearch/hostel", "title": "Hostel",
          "provenance": "illustrative"}


class FakeConn:
    def __init__(self, row):
        self.row = row

    def execute(self, *_):
        return self

    def fetchone(self):
        return self.row


def cite(**over):
    base = dict(n=1, document_id="d1", chunk_id="d1:0", title="Hostel",
                source_url=STORED["source_url"], provenance="illustrative",
                provenance_label=rag.PROVENANCE_LABELS["illustrative"], page=None,
                excerpt=STORED["content"], quote="allotted on application")
    return Citation(**(base | over))


class Res:
    def __init__(self, *citations):
        self.citations = citations


def test_matching_citation_has_no_problems():
    assert citation_problems(Res(cite()), FakeConn(STORED)) == []


@pytest.mark.parametrize("over, expected", [
    ({"source_url": "https://example.org/fake"}, "source_url differs"),
    ({"title": "Invented title"}, "title differs"),
    ({"document_id": "d2"}, "document_id differs"),
    ({"provenance": "official_web",
      "provenance_label": rag.PROVENANCE_LABELS["official_web"]}, "provenance differs"),
    ({"excerpt": "Hostel accommodation is free."}, "excerpt differs"),
    ({"quote": "rooms are free"}, "quote not in excerpt"),
    ({"provenance_label": "Official university web page"}, "provenance label wrong"),
])
def test_tampered_citation_is_detected(over, expected):
    problems = citation_problems(Res(cite(**over)), FakeConn(STORED))
    assert any(expected in p for p in problems), problems


def test_unknown_chunk_is_detected():
    assert citation_problems(Res(cite()), FakeConn(None)) == ["citation 1: chunk/document not found"]
