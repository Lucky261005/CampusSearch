"""Phase 6A ingestion: PDFs, ZIPs, refresh/delete consistency, migration, missing model."""

import io
import os
import sys
import types
import zipfile
from pathlib import Path

import pytest

from app import db, embeddings, ingest
from app.embeddings import chunk_document
from app.ingest import (IngestError, build_document, distinct_words, ingest_document,
                        ingest_records, manifest_records, pdf_to_record, prune, read_zip,
                        zip_records)
from app.pdf import PdfError, clean_pages, extract_pdf, join_pages
from app.reindex import index_documents
from app.retrieval import Filters, semantic
from pdf_factory import make_pdf

LINE = "Students register for examinations online through the student portal before the deadline."


def three_page_pdf(extra: str = "") -> bytes:
    return make_pdf([
        ["CU Notice Header", LINE, f"Examination rules are explained in detail on this page {extra}.", "Page 1 of 3"],
        ["CU Notice Header", "Tuition fees are paid online before the due date of each semester.",
         "Late payment attracts a fine as notified by the finance office.", "Page 2 of 3"],
        ["CU Notice Header", "Results are published on the university website after evaluation.",
         "Revaluation requests are accepted within the published window.", "3"],
    ])


# --- PDF extraction ---------------------------------------------------------------------------

def test_pdf_extraction_strips_headers_footers_and_page_numbers():
    r = extract_pdf(three_page_pdf())
    assert len(r.pages) == 3 and r.pages[0].startswith(LINE)
    joined = " ".join(r.pages)
    assert "CU Notice Header" not in joined and "Page 1 of 3" not in joined
    assert "removed 1 repeated header/footer line(s)" in r.warnings


def test_clean_pages_repairs_drop_caps_letter_spacing_and_hyphenation():
    pages, _ = clean_pages(["C\nommencement of classes", "T h i s  i s  a  n o t i c e  t o  s t u d e n t s",
                            "regis-\ntration opens"])
    assert pages == ["Commencement of classes", "This is a notice to students", "registration opens"]


def test_join_pages_offsets_survive_empty_pages_and_normalization():
    content, starts = join_pages(["alpha beta", "", "gamma ﬁle"])
    assert content == "alpha beta gamma ﬁle" and starts == [0, 11, 11]
    doc = build_document({"title": "t", "content": content, "source_url": "u", "source_type": "pdf",
                          "provenance": "official_web", "page_starts": starts})
    assert doc.content[starts[2]:].startswith("gamma")  # build_document did not shift offsets


@pytest.mark.parametrize("data, message", [
    (make_pdf([[""], [""]]), "probably scanned or image-based"),
    (make_pdf([["tiny"]]), "probably scanned or image-based"),
    (b"%PDF-1.4\nthis is not a real pdf", "unreadable PDF"),
    (b"", "unreadable PDF"),
])
def test_empty_scanned_or_malformed_pdf_is_rejected(data, message):
    with pytest.raises(PdfError, match=message):
        extract_pdf(data)
    with pytest.raises(IngestError, match="PDF extraction failed"):
        pdf_to_record(data, "https://example.org/x.pdf", "official_web")


def test_pdf_record_carries_pages_status_and_provenance():
    rec = pdf_to_record(three_page_pdf(), "https://example.org/Exam%20Rules.pdf", "official_web")
    assert rec["title"] == "Exam Rules" and rec["source_type"] == "pdf"
    assert rec["extraction_status"] == "warnings" and "header/footer" in rec["extraction_note"]
    assert len(rec["page_starts"]) == 3 and rec["fetched_at"]


def test_page_aware_chunks_never_cross_pages():
    content, starts = join_pages([" ".join(f"a{i}" for i in range(200)), "b0 b1 b2"])
    chunks = chunk_document(content, starts)
    assert [p for p, _ in chunks] == [1, 1, 2]
    assert chunks[-1][1] == "b0 b1 b2" and all("b0" not in t for p, t in chunks if p == 1)
    assert chunk_document("x y", None) == [(None, "x y")]  # non-PDF behaviour unchanged


# --- database: duplicates, changes, deletion, stale chunks ------------------------------------

def pdf_doc(url="https://example.org/rules.pdf", extra=""):
    return build_document(pdf_to_record(three_page_pdf(extra), url, "official_web"))


def test_pdf_ingest_reindex_stores_page_numbers(conn, fake_embedder):
    assert ingest_document(conn, pdf_doc()) == "inserted"
    index_documents(conn, fake_embedder)
    rows = conn.execute("SELECT position, page FROM chunks ORDER BY position").fetchall()
    assert [r["page"] for r in rows] == [1, 2, 3]
    stored = db.get_by_url(conn, "https://example.org/rules.pdf")
    assert stored.extraction_status == "warnings" and stored.page_starts == pdf_doc().page_starts


def test_duplicate_ingestion_is_idempotent(conn, fake_embedder):
    assert ingest_document(conn, pdf_doc()) == "inserted"
    assert ingest_document(conn, pdf_doc()) == "unchanged"
    assert ingest_document(conn, pdf_doc(url="https://example.org/copy.pdf")) == "duplicate"
    first = index_documents(conn, fake_embedder)
    again = index_documents(conn, fake_embedder)
    assert again["embedded"] == 0 and again["reused"] == first["embedded"]
    assert conn.execute("SELECT count(*) n FROM documents").fetchone()["n"] == 1


def test_metadata_only_change_updates_fields_and_keeps_chunks(conn, fake_embedder):
    ingest_document(conn, pdf_doc())
    index_documents(conn, fake_embedder)
    recat = build_document(pdf_to_record(three_page_pdf(), "https://example.org/rules.pdf", "official_web")
                           | {"category": "examinations"})
    assert ingest_document(conn, recat) == "metadata_updated"
    assert db.get_by_url(conn, "https://example.org/rules.pdf").category == "examinations"
    assert conn.execute("SELECT count(*) n FROM chunks").fetchone()["n"] == 3  # nothing dropped
    assert index_documents(conn, fake_embedder)["embedded"] == 0
    retitled = build_document(pdf_to_record(three_page_pdf(), "https://example.org/rules.pdf",
                                            "official_web", title="Examination Rules")
                              | {"category": "examinations"})
    assert ingest_document(conn, retitled) == "metadata_updated"
    assert index_documents(conn, fake_embedder)["embedded"] == 3  # title is part of the embedded text


def test_changed_document_leaves_no_stale_chunks(conn, fake_embedder):
    ingest_document(conn, pdf_doc(extra="alpha"))
    index_documents(conn, fake_embedder)
    assert ingest_document(conn, pdf_doc(extra="omega")) == "updated"
    # old chunks are gone at once, so the old text cannot be retrieved before reindexing
    assert conn.execute("SELECT count(*) n FROM chunks").fetchone()["n"] == 0
    assert semantic(conn, fake_embedder, "examination rules alpha", Filters()) == []
    index_documents(conn, fake_embedder)
    texts = [r["content"] for r in conn.execute("SELECT content FROM chunks")]
    assert any("omega" in t for t in texts) and not any("alpha" in t for t in texts)


def test_delete_and_prune_remove_documents_and_chunks(conn, fake_embedder):
    ingest_document(conn, pdf_doc())
    ingest_document(conn, pdf_doc(url="https://example.org/other.pdf", extra="other"))
    ingest_records(conn, [{"title": "Sample", "content": "Illustrative hostel record.",
                           "source_url": "illustrative://t/h", "source_type": "json",
                           "provenance": "illustrative"}])
    index_documents(conn, fake_embedder)
    assert db.delete_by_url(conn, "https://example.org/rules.pdf")
    assert not db.delete_by_url(conn, "https://example.org/rules.pdf")
    assert prune(conn, keep={"https://example.org/kept.pdf"}) == ["https://example.org/other.pdf"]
    left = conn.execute("SELECT source_url FROM documents").fetchall()
    assert [r["source_url"] for r in left] == ["illustrative://t/h"]  # prune never touches non-official
    orphans = conn.execute("SELECT count(*) n FROM chunks c LEFT JOIN documents d "
                           "ON d.id = c.document_id WHERE d.id IS NULL").fetchone()["n"]
    assert orphans == 0


def test_chunks_and_embeddings_stay_consistent(conn, fake_embedder):
    ingest_document(conn, pdf_doc())
    index_documents(conn, fake_embedder)
    row = conn.execute("SELECT count(*) n, count(DISTINCT id) ids, min(vector_dims(embedding)) d, "
                       "count(*) FILTER (WHERE model <> %s) other FROM chunks",
                       (fake_embedder.name,)).fetchone()
    assert row["n"] == row["ids"] == 3 and row["d"] == 384 and row["other"] == 0


# --- schema migration -------------------------------------------------------------------------

OLD_SCHEMA = """
CREATE TABLE documents (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
    source_url TEXT NOT NULL UNIQUE, source_type TEXT NOT NULL, provenance TEXT NOT NULL,
    campus TEXT, category TEXT, programme TEXT, semester INTEGER, publication_date DATE,
    content_hash TEXT NOT NULL UNIQUE, fetched_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
"""


def test_migration_upgrades_existing_database_in_place(conn):
    conn.execute("DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS documents")
    conn.execute(OLD_SCHEMA)
    conn.execute("INSERT INTO documents (id, title, content, source_url, source_type, provenance, "
                 "content_hash) VALUES ('d1', 'Old', 'old content', 'u:old', 'json', "
                 "'illustrative', 'h1')")
    conn.commit()
    db.init_schema(conn)
    db.init_schema(conn)  # idempotent
    [old] = db.all_documents(conn)
    assert old.content == "old content" and old.extraction_status == "ok" and old.page_starts is None
    cols = {r["column_name"] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'documents'")}
    assert {"extraction_status", "extraction_note", "page_starts"} <= cols


def test_fresh_database_setup(conn):
    conn.execute("DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS documents")
    conn.commit()
    db.init_schema(conn)
    assert ingest_document(conn, pdf_doc()) == "inserted"


# --- ZIP handling -----------------------------------------------------------------------------

def build_zip(path: Path, entries: dict[str, bytes], symlink: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 << 16)
            z.writestr(info, "/etc/passwd")
    return path


def test_zip_rejects_traversal_absolute_drive_symlink_and_non_pdf(tmp_path):
    good = three_page_pdf()
    archive = build_zip(tmp_path / "a.zip", {
        "notices/good.pdf": good, "../evil.pdf": good, "notices/../../evil2.pdf": good,
        "/abs.pdf": good, "C:/windows.pdf": good, "readme.txt": b"hello", "run.exe": b"MZ",
    }, symlink="notices/link.pdf")
    before = set(os.listdir(tmp_path))
    members, rejected = read_zip(archive)
    assert [n for n, _ in members] == ["good.pdf"]
    reasons = " | ".join(rejected)
    for bad in ("../evil.pdf", "evil2.pdf", "/abs.pdf", "C:/windows.pdf"):
        assert f"{bad}: unsafe path" in reasons or bad in reasons
    assert "link.pdf: symbolic link" in reasons and "readme.txt: not a PDF" in reasons
    assert set(os.listdir(tmp_path)) == before  # nothing extracted to disk


def test_zip_limits(tmp_path, monkeypatch):
    archive = build_zip(tmp_path / "b.zip", {f"f{i}.pdf": b"x" for i in range(4)})
    monkeypatch.setattr(ingest, "ZIP_MAX_MEMBERS", 3)
    with pytest.raises(IngestError, match="exceeds limit"):
        read_zip(archive)
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bomb.pdf", b"0" * 5_000_000)
    monkeypatch.setattr(ingest, "ZIP_MAX_MEMBERS", 50)
    assert "suspicious compression ratio" in read_zip(bomb)[1][0]
    (tmp_path / "bad.zip").write_bytes(b"not a zip")
    with pytest.raises(IngestError, match="cannot open ZIP"):
        read_zip(tmp_path / "bad.zip")


def test_zip_rejects_boilerplate_only_posters(tmp_path):
    boiler = ["About Us: The Scholarship and Fellowship Support Cell guides students to obtain "
              "scholarships and fellowships from the university and external organisations. "
              "For assistance contact the Scholarship and Fellowship Support Cell by email."]
    detail = [" ".join(f"Eligibility rule {i} for the national scholarship scheme applies." for i in range(12))]
    archive = build_zip(tmp_path / "n.zip", {
        "Poster A.pdf": make_pdf([boiler + ["Notice 1"]]),
        "Poster B.pdf": make_pdf([boiler + ["Notice 2"]]),
        "Real Notice.pdf": make_pdf([boiler + detail]),
    })
    records, problems = zip_records(archive, "test-notices")
    assert [r["title"] for r in records] == ["Real Notice"]
    assert records[0]["provenance"] == "supplied_document"
    assert records[0]["source_url"] == "supplied://test-notices/Real%20Notice.pdf"
    assert sum("probably in images" in p for p in problems) == 2


def test_distinct_words_small_batches_use_plain_counts():
    assert distinct_words({"a": "one two three", "b": "four"}) == {"a": 3, "b": 1}


# --- fetching (mocked network) ----------------------------------------------------------------

class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, ctype: str, url: str):
        super().__init__(body)
        self.headers = types.SimpleNamespace(get=lambda k, d="": ctype,
                                             get_content_charset=lambda: "utf-8")
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_dispatches_pdf_and_html_and_rejects_others(monkeypatch):
    responses = {
        "https://example.org/a.pdf": (three_page_pdf(), "application/pdf"),
        "https://example.org/page": (b"<html><head><title>T</title></head><body><main><p>"
                                     b"Caf\xc3\xa9 \x96 hours</p></main></body></html>", "text/html; charset=utf-8"),
        "https://example.org/img": (b"\x89PNG", "image/png"),
    }
    monkeypatch.setattr(ingest.urllib.request, "urlopen", lambda req, timeout: FakeResponse(
        *responses[req.full_url], req.full_url))
    assert ingest.fetch("https://example.org/a.pdf")["source_type"] == "pdf"
    html = ingest.fetch("https://example.org/page")
    assert html["content"] == "Café – hours"  # stray cp1252 byte recovered, no U+FFFD
    with pytest.raises(IngestError, match="unsupported content type image/png"):
        ingest.fetch("https://example.org/img")
    with pytest.raises(IngestError, match="unsupported URL scheme"):
        ingest.fetch("file:///etc/passwd")


def test_manifest_collects_errors_and_merges_metadata(monkeypatch):
    def fake_fetch(url):
        if "broken" in url:
            raise IngestError(f"fetch failed {url}: HTTP Error 404")
        return {"title": "Generic", "content": "c", "source_url": url + "?final",
                "source_type": "html", "provenance": "official_web"}

    monkeypatch.setattr(ingest, "fetch", fake_fetch)
    records, errors, keep = manifest_records(
        [{"url": "https://example.org/ok", "title": "Real title", "category": "fees", "note": "x"},
         {"url": "https://example.org/broken"}], delay_s=0)
    assert records[0]["title"] == "Real title" and records[0]["category"] == "fees"
    assert "note" not in records[0] and errors == ["fetch failed https://example.org/broken: HTTP Error 404"]
    assert keep == {"https://example.org/ok", "https://example.org/ok?final", "https://example.org/broken"}


def test_manifest_file_validation(tmp_path):
    (tmp_path / "m.json").write_text('[{"url": "http://insecure.example"}]')
    with pytest.raises(IngestError, match="https url"):
        ingest.load_manifest(tmp_path / "m.json")


def test_repository_manifest_is_well_formed():
    entries = ingest.load_manifest(Path(__file__).parents[1] / "data" / "sources.json")
    urls = [e["url"] for e in entries]
    assert len(urls) == len(set(urls)) and all(u.startswith("https://christuniversity.in/") for u in urls)
    assert all(e.get("title") and e.get("category") for e in entries)


# --- missing embedding model ------------------------------------------------------------------

def test_cli_reindex_without_model_keeps_keyword_data(conn, _test_db, monkeypatch, capsys, tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text('[{"title": "T", "content": "Hostel rules apply.", "source_url": "u:h", '
                    '"source_type": "json", "provenance": "illustrative"}]')
    monkeypatch.setattr(db, "connect", lambda url=_test_db: db.psycopg.connect(
        _test_db, row_factory=db.dict_row))
    monkeypatch.setattr(embeddings, "get_embedder", lambda: (None, "embedding model could not be loaded"))
    monkeypatch.setattr(sys, "argv", ["ingest", str(seed), "--reindex"])
    ingest.main()
    out = capsys.readouterr()
    assert "'inserted': 1" in out.out and "reindex skipped: embedding model could not be loaded" in out.err
    assert conn.execute("SELECT count(*) n FROM chunks").fetchone()["n"] == 0


def test_cached_model_is_tried_before_any_download(monkeypatch):
    calls = []

    class FakeST:
        def __init__(self, name, local_files_only=False):
            calls.append(local_files_only)
            if local_files_only:
                raise OSError("not cached")

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=FakeST))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    embeddings.SentenceTransformerEmbedder()
    assert calls == [True, False]  # cache first, then one download attempt
    calls.clear()
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with pytest.raises(OSError):
        embeddings.SentenceTransformerEmbedder()
    assert calls == [True]  # offline mode never tries the network


def test_cli_reports_unreachable_database(monkeypatch, capsys):
    def refuse(*a, **k):
        raise db.psycopg.OperationalError("connection refused")

    monkeypatch.setattr(db, "connect", refuse)
    monkeypatch.setattr(sys, "argv", ["ingest"])
    with pytest.raises(SystemExit, match="cannot connect to PostgreSQL"):
        ingest.main()
