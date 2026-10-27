from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ingest import build_document, load_json_records
from app.main import app
from conftest import FixtureClient
from app.search import SearchIndex

SEED = Path(__file__).parents[1] / "data" / "seed_documents.json"


@pytest.fixture
def client():
    # no lifespan: the index is injected, so API tests need no database
    app.state.index = SearchIndex([build_document(r) for r in load_json_records(SEED)])
    yield FixtureClient(app)
    del app.state.index


def urls(res):
    return [r["source_url"].rsplit("/", 1)[1] for r in res.json()["results"]]


def test_response_structure(client):
    res = client.get("/api/search", params={"q": "academic calendar"})
    assert res.status_code == 200
    body = res.json()
    assert body.keys() == {"query", "mode", "total", "limit", "offset", "results"}
    assert body["mode"] == "keyword" and body["limit"] == 10 and body["offset"] == 0
    hit = body["results"][0]
    assert hit["chunk_id"] is None  # keyword hits are document-level
    assert set(hit) == {"id", "chunk_id", "title", "source_url", "source_type", "provenance", "campus",
                        "category", "programme", "semester", "publication_date", "score",
                        "snippet"}
    assert hit["provenance"] == "illustrative" and "calendar" in hit["snippet"].lower()
    scores = [r["score"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)


def test_relevant_query_returns_expected_order(client):
    # odd-semester doc says "calendar" more often in a similar length -> ranks first
    assert urls(client.get("/api/search", params={"q": "academic calendar"}))[:2] == \
        ["academic-calendar-odd", "academic-calendar-even"]
    assert urls(client.get("/api/search", params={"q": "hostel"}))[0] == "hostel"
    assert urls(client.get("/api/search", params={"q": "MBA interview"}))[0] == "mba-admissions"


@pytest.mark.parametrize("params, expected", [
    ({"campus": "kengeri"}, {"exam-registration", "placements"}),
    ({"category": "examinations"}, {"attendance", "exam-registration"}),
    ({"programme": "BTech Computer Science"}, {"exam-registration", "placements"}),
    ({"semester": 5}, {"exam-registration"}),
    ({"campus": "Kengeri", "semester": 7}, {"placements"}),
    ({"category": "examinations", "campus": "Kengeri"}, {"exam-registration"}),
    ({"campus": "Nowhere"}, set()),
])
def test_filters(client, params, expected):
    res = client.get("/api/search", params={"q": "students semester exam registration placement", **params})
    assert set(urls(res)) == expected
    for r in res.json()["results"]:
        for k, v in params.items():
            assert str(r[k]).casefold() == str(v).casefold()


def test_date_range_filter_excludes_undated(client):
    res = client.get("/api/search", params={"q": "library calendar", "date_from": "2026-06-01",
                                            "date_to": "2026-12-31"})
    assert set(urls(res)) == {"academic-calendar-odd", "academic-calendar-even"}


def test_pagination_is_consistent(client):
    q = {"q": "official students"}
    full = urls(client.get("/api/search", params={**q, "limit": 50}))
    assert len(full) > 4
    pages = [urls(client.get("/api/search", params={**q, "limit": 2, "offset": o}))
             for o in range(0, len(full), 2)]
    assert sum(pages, []) == full
    assert client.get("/api/search", params={**q, "offset": 999}).json()["results"] == []
    assert client.get("/api/search", params={**q, "limit": 2}).json()["total"] == len(full)


def test_empty_and_unknown_queries(client):
    for q in ["", "   ", "zzzqqq"]:
        body = client.get("/api/search", params={"q": q}).json()
        assert body["total"] == 0 and body["results"] == []


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 51}, {"offset": -1}, {"semester": 0}, {"semester": "x"},
    {"mode": "vector"}, {"date_from": "not-a-date"}, {"q": "a" * 501},
])
def test_invalid_params_422(client, params):
    assert client.get("/api/search", params={"q": "fee", **params}).status_code == 422


def test_inverted_date_range_400(client):
    res = client.get("/api/search", params={"q": "fee", "date_from": "2026-12-01",
                                            "date_to": "2026-01-01"})
    assert res.status_code == 400


def test_search_without_index_is_safe():
    assert TestClient(app).get("/api/search", params={"q": "fee"}).json()["total"] == 0
