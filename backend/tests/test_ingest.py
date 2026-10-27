from pathlib import Path

import pytest

from app import db
from app.ingest import (
    IngestError, build_document, content_hash, fetch_html, html_to_record,
    ingest_document, ingest_records, load_json_records, normalize_text,
)

SEED = Path(__file__).parents[1] / "data" / "seed_documents.json"


def rec(url="illustrative://t/a", content="Fee payment notice", **kw):
    return {"title": "T", "content": content, "source_url": url,
            "source_type": "json", "provenance": "illustrative", **kw}


def test_normalize_and_hash_are_stable():
    assert normalize_text("  Fee\n\t payment  ") == "Fee payment"
    assert content_hash("Fee  payment") == content_hash(" Fee\npayment ")
    assert content_hash("Fee payment") != content_hash("Fee payments")


def test_build_document_rejects_malformed_records():
    for bad in [{"title": "x"}, rec(content="  "), rec(provenance="made-up"), rec(semester=99)]:
        with pytest.raises(IngestError):
            build_document(bad)


def test_seed_corpus_is_valid_and_labelled_illustrative():
    docs = [build_document(r) for r in load_json_records(SEED)]
    assert 10 <= len(docs) <= 15
    assert all(d.provenance == "illustrative" and d.source_url.startswith("illustrative://")
               for d in docs)
    assert len({d.content_hash for d in docs}) == len(docs)


def test_load_json_rejects_bad_files(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "obj.json").write_text('{"a": 1}')
    for name in ["bad.json", "obj.json", "missing.json"]:
        with pytest.raises(IngestError):
            load_json_records(tmp_path / name)


FIXTURES = Path(__file__).parent / "fixtures"


def test_html_to_record_strips_scripts_and_nav():
    html = ("<html><head><title> Admissions </title><script>var x=1</script></head>"
            "<body><nav>Menu</nav><p>Apply online</p><footer>(c)</footer></body></html>")
    r = html_to_record(html, "https://example.org/a")
    assert r["title"] == "Admissions"
    assert normalize_text(r["content"]) == "Apply online"
    assert r["provenance"] == "official_web" and r["fetched_at"]


def test_html_prefers_main_content_and_drops_chrome():
    r = html_to_record((FIXTURES / "page_with_main.html").read_text(), "https://example.org/adm")
    assert r["title"] == "Admissions | Example University"
    assert r["content"] == (
        "Undergraduate Admissions "
        "Applications are submitted online through the admissions portal. "
        "Shortlisted applicants attend a personal interview. Results are emailed."
    )
    for noise in ["tracking", "cookies", "Home", "Related links", "Fest", "Copyright", "color"]:
        assert noise not in r["content"]


def test_html_without_main_drops_menus_footer_and_repeats():
    r = html_to_record((FIXTURES / "page_without_main.html").read_text(), "https://example.org/lib")
    assert r["content"] == ("Library Services Students may borrow books using their ID card. "
                            "Late returns attract a fine.")


def test_html_change_updates_document_hash(conn):
    url = "https://example.org/adm"
    html = (FIXTURES / "page_with_main.html").read_text()
    assert ingest_document(conn, build_document(html_to_record(html, url))) == "inserted"
    assert ingest_document(conn, build_document(html_to_record(html, url))) == "unchanged"
    changed = html.replace("personal interview", "group discussion")
    assert ingest_document(conn, build_document(html_to_record(changed, url))) == "updated"
    assert "group discussion" in db.get_by_url(conn, url).content


def test_fetch_html_rejects_non_http_and_unreachable():
    with pytest.raises(IngestError):
        fetch_html("file:///etc/passwd")
    with pytest.raises(IngestError):
        fetch_html("http://127.0.0.1:9/nothing")  # closed port, fails fast


# --- database-backed ---------------------------------------------------------

def test_insert_unchanged_update_and_duplicate(conn):
    assert ingest_document(conn, build_document(rec())) == "inserted"
    assert ingest_document(conn, build_document(rec(content=" Fee  payment notice"))) == "unchanged"
    first = db.get_by_url(conn, "illustrative://t/a")

    assert ingest_document(conn, build_document(rec(content="Fee payment notice v2"))) == "updated"
    updated = db.get_by_url(conn, "illustrative://t/a")
    assert updated.content == "Fee payment notice v2"
    assert updated.id == first.id and updated.updated_at >= first.updated_at

    # same content, different URL -> not stored twice
    dup = build_document(rec(url="illustrative://t/b", content="Fee payment notice v2"))
    assert ingest_document(conn, dup) == "duplicate"
    assert len(db.all_documents(conn)) == 1


def test_seed_ingest_persists_and_is_idempotent(conn):
    records = load_json_records(SEED)
    first = ingest_records(conn, records)
    assert first["inserted"] == len(records) and first["error"] == 0
    second = ingest_records(conn, records)
    assert second["unchanged"] == len(records)
    stored = db.all_documents(conn)
    assert len(stored) == len(records)
    cal = next(d for d in stored if d.source_url.endswith("academic-calendar-odd"))
    assert cal.campus == "Central" and cal.semester == 1 and str(cal.publication_date) == "2026-06-01"


def test_ingest_records_counts_errors_without_aborting(conn):
    counts = ingest_records(conn, [rec(), {"title": "broken"}])
    assert counts["inserted"] == 1 and counts["error"] == 1
