"""Ingestion: JSON records, public HTML pages and PDFs, and locally supplied ZIPs of PDFs
-> normalized, deduplicated, provenance-labelled documents.

CLI examples (from backend/):
    python -m app.ingest data/seed_documents.json
    python -m app.ingest --manifest data/sources.json --prune --reindex
    python -m app.ingest --zip "path/to/notices.zip" --collection scholarship-notices --reindex
    python -m app.ingest --delete https://example.org/old-page
See README.md for the full refresh workflow.
"""

import argparse
import codecs
import hashlib
import json
import re
import stat
import sys
import time
import unicodedata
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal
from urllib.parse import quote, unquote, urlsplit

import psycopg
from pydantic import ValidationError

from app import db
from app.models import Document
from app.pdf import PdfError, PdfText, clean_pages, extract_pdf, join_pages

MAX_HTML_BYTES = 2_000_000
MAX_PDF_BYTES = 25_000_000
FETCH_TIMEOUT_S = 20
USER_AGENT = "CampusSearchBot/0.1 (student project; local use)"

Outcome = Literal["inserted", "updated", "metadata_updated", "unchanged", "duplicate"]


class IngestError(Exception):
    pass


# Some official pages declare UTF-8 but contain stray Windows-1252 bytes (smart quotes,
# dashes). Decode just those bytes as cp1252 instead of turning them into U+FFFD.
codecs.register_error(
    "cp1252fallback",
    lambda e: (e.object[e.start:e.end].decode("cp1252", errors="replace"), e.end),
)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode()).hexdigest()


def doc_id(source_url: str) -> str:
    return hashlib.sha256(source_url.encode()).hexdigest()[:16]


def build_document(record: dict) -> Document:
    """Validate a raw record and fill derived fields (id, normalized text, hash)."""
    try:
        title = normalize_text(str(record["title"]))
        content = normalize_text(str(record["content"]))
        url = str(record["source_url"]).strip()
    except KeyError as e:
        raise IngestError(f"missing field {e}") from None
    if not title or not content or not url:
        raise IngestError(f"empty title/content/url for {url or '<no url>'}")
    try:
        return Document(
            **{**record, "title": title, "content": content, "source_url": url},
            id=doc_id(url),
            content_hash=content_hash(content),
        )
    except ValidationError as e:
        raise IngestError(f"invalid record {url}: {e.errors()[0]['msg']}") from None


METADATA = ("title", "source_type", "provenance", "campus", "category", "programme", "semester",
            "publication_date", "extraction_status", "extraction_note", "page_starts")


def ingest_document(conn: psycopg.Connection, doc: Document) -> Outcome:
    existing = db.get_by_url(conn, doc.source_url)
    if existing and existing.content_hash == doc.content_hash:
        if any(getattr(existing, f) != getattr(doc, f) for f in METADATA):
            db.update_metadata(conn, doc, METADATA)  # chunks kept; reindex re-embeds if title changed
            return "metadata_updated"
        return "unchanged"
    owner = db.hash_owner(conn, doc.content_hash)
    if owner and owner != doc.source_url:
        return "duplicate"  # identical content already stored under another URL
    db.upsert(conn, doc)
    return "updated" if existing else "inserted"


def load_json_records(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise IngestError(f"cannot read {path}: {e}") from None
    if not isinstance(data, list):
        raise IngestError(f"{path} must contain a JSON list of records")
    return data


class _TextExtractor(HTMLParser):
    """Collects text blocks, skipping boilerplate regions; tracks <main>/<article> separately."""

    SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "nav", "header", "footer",
                 "aside", "form", "iframe", "button", "select"}
    # class/id words that mark site chrome rather than page content
    SKIP_ATTR = re.compile(
        r"(?:^|[\s_-])(nav|navbar|menu|footer|header|sidebar|breadcrumbs?|cookie|modal|popup|"
        r"social|offcanvas|skip-link)(?:$|[\s_-])", re.I)
    MAIN_TAGS = {"main", "article"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
            "source", "track", "wbr"}
    BLOCK = {"p", "div", "section", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
             "td", "th", "tr", "table", "br", "dd", "dt", "blockquote", "pre", "main", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.blocks: list[str] = []       # all non-skipped blocks
        self.main_blocks: list[str] = []  # blocks inside <main>/<article>/role=main
        self._buf: list[str] = []
        self._skip: list[str] = []        # open skipped tags (innermost last)
        self._main: list[str] = []
        self._in_title = False

    def _flush(self) -> None:
        text = normalize_text(" ".join(self._buf))
        self._buf = []
        if text:
            self.blocks.append(text)
            if self._main:
                self.main_blocks.append(text)

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
            return
        if tag in self.VOID:
            if tag == "br":
                self._flush()
            return
        if self._skip:
            self._skip.append(tag)
            return
        a = dict(attrs)
        marker = f"{a.get('class') or ''} {a.get('id') or ''} {a.get('role') or ''}"
        if (tag in self.SKIP_TAGS or self.SKIP_ATTR.search(marker)
                or a.get("role") in ("navigation", "banner", "contentinfo")):
            self._flush()
            self._skip.append(tag)
            return
        if tag in self.BLOCK:
            self._flush()
        if tag in self.MAIN_TAGS or a.get("role") == "main":
            self._main.append(tag)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
            return
        if self._skip:
            # pop to the matching open tag; tolerates unclosed children
            if tag in self._skip:
                while self._skip and self._skip.pop() != tag:
                    pass
            return
        if tag in self.BLOCK:
            self._flush()
        if self._main and tag == self._main[-1]:
            self._main.pop()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._buf.append(data)

    def text(self) -> str:
        self._flush()
        blocks = self.main_blocks or self.blocks
        seen: set[str] = set()
        out = []
        for b in blocks:  # repeated blocks (menus, carousels) are kept once
            if b.casefold() not in seen:
                seen.add(b.casefold())
                out.append(b)
        return " ".join(out)


def html_to_record(html: str, url: str) -> dict:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return {
        "title": normalize_text(parser.title) or url,
        "content": parser.text(),
        "source_url": url,
        "source_type": "html",
        "provenance": "official_web",
        "fetched_at": datetime.now(timezone.utc),
    }


def pdf_to_record(data: bytes, url: str, provenance: str, title: str | None = None,
                  ocr: bool = False) -> dict:
    """PDF bytes -> record with page offsets and extraction status. Raises IngestError.
    With ocr=True, a PDF without a text layer is recognised with the optional OCR engine."""
    try:
        pdf = extract_pdf(data)
    except PdfError as e:
        if not (ocr and e.scanned):
            raise IngestError(f"PDF extraction failed for {url}: {e}") from None
        pdf = _ocr_text(data, url)
    content, starts = join_pages(pdf.pages)
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1]).removesuffix(".pdf")
    return {
        "title": title or name or pdf.title or url,
        "content": content,
        "page_starts": starts,
        "source_url": url,
        "source_type": "pdf",
        "provenance": provenance,
        "fetched_at": datetime.now(timezone.utc),
        "extraction_status": "warnings" if pdf.warnings else "ok",
        "extraction_note": "; ".join(pdf.warnings) or None,
    }


def _ocr_text(data: bytes, url: str) -> PdfText:
    from app.ocr import OcrError, ocr_pdf

    try:
        raw_pages, confidence = ocr_pdf(data)
    except OcrError as e:
        raise IngestError(f"OCR failed for {url}: {e}") from None
    pages, warnings = clean_pages(raw_pages)
    return PdfText(None, pages, [f"text recognised by OCR (mean confidence {confidence:.2f}); "
                                 "may contain recognition errors", *warnings])


def fetch(url: str, ocr: bool = False) -> dict:
    """Fetch a public HTML page or PDF. No auth, no cookies; failures raise IngestError."""
    if not url.startswith(("http://", "https://")):
        raise IngestError(f"unsupported URL scheme: {url}")
    req = urllib.request.Request(quote(url, safe=":/?&=%#()"),
                                 headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as res:
            ctype = res.headers.get("Content-Type", "")
            limit = MAX_PDF_BYTES if "pdf" in ctype else MAX_HTML_BYTES
            body = res.read(limit + 1)
            charset = res.headers.get_content_charset() or "utf-8"
            final = unquote(res.geturl())  # after redirects, so aliases dedupe by URL
    except Exception as e:  # HTTPError, URLError, timeouts, resets
        raise IngestError(f"fetch failed {url}: {e}") from None
    if len(body) > limit:
        raise IngestError(f"{url}: larger than {limit // 1_000_000} MB")
    if "application/pdf" in ctype:
        return pdf_to_record(body, final, "official_web", ocr=ocr)
    if "text/html" in ctype:
        return html_to_record(body.decode(charset, errors="cp1252fallback"), final)
    raise IngestError(f"{url}: unsupported content type {ctype or 'unknown'}")


fetch_html = fetch  # backwards-compatible name


# --- ZIP archives (read in memory; nothing is extracted to disk) -----------------------------

ZIP_MAX_MEMBERS = 50
ZIP_MAX_MEMBER_BYTES = 25_000_000
ZIP_MAX_TOTAL_BYTES = 150_000_000
ZIP_MAX_RATIO = 100  # uncompressed/compressed; higher suggests a zip bomb
MIN_DISTINCT_WORDS = 40  # words not shared with the rest of the batch


def read_zip(path: Path) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Return (name, bytes) for PDF members and a list of rejected entries with reasons."""
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise IngestError(f"cannot open ZIP {path.name}: {e}") from None
    infos = zf.infolist()
    if len(infos) > ZIP_MAX_MEMBERS:
        raise IngestError(f"{path.name}: {len(infos)} entries exceeds limit of {ZIP_MAX_MEMBERS}")
    if sum(i.file_size for i in infos) > ZIP_MAX_TOTAL_BYTES:
        raise IngestError(f"{path.name}: uncompressed size exceeds "
                          f"{ZIP_MAX_TOTAL_BYTES // 1_000_000} MB")
    members, rejected = [], []
    for i in infos:
        name = i.filename
        parts = name.replace("\\", "/").split("/")
        if i.is_dir():
            continue
        if name.startswith(("/", "\\")) or ".." in parts or re.match(r"^[A-Za-z]:", name):
            rejected.append(f"{name}: unsafe path")
        elif stat.S_ISLNK(i.external_attr >> 16):
            rejected.append(f"{name}: symbolic link")
        elif not name.lower().endswith(".pdf"):
            rejected.append(f"{name}: not a PDF")
        elif i.file_size > ZIP_MAX_MEMBER_BYTES or (
                i.compress_size and i.file_size / i.compress_size > ZIP_MAX_RATIO):
            rejected.append(f"{name}: too large or suspicious compression ratio")
        else:
            members.append((parts[-1], zf.read(i)))
    return members, rejected


def distinct_words(texts: dict[str, str]) -> dict[str, int]:
    """Per document: words not inside a 5-word phrase shared by at least half the batch.
    Catches poster PDFs whose only extractable text is common boilerplate."""
    words = {k: v.casefold().split() for k, v in texts.items()}
    if len(texts) < 3:
        return {k: len(w) for k, w in words.items()}
    grams = {k: {tuple(w[i:i + 5]) for i in range(max(0, len(w) - 4))} for k, w in words.items()}
    counts = Counter(g for v in grams.values() for g in v)
    common = {g for g, c in counts.items() if c >= len(texts) / 2}
    out = {}
    for k, w in words.items():
        covered: set[int] = set()
        for i in range(max(0, len(w) - 4)):
            if tuple(w[i:i + 5]) in common:
                covered.update(range(i, i + 5))
        out[k] = len(w) - len(covered)
    return out


def zip_records(path: Path, collection: str, ocr: bool = False) -> tuple[list[dict], list[str]]:
    """Records for the PDFs in a locally supplied ZIP. Their official URLs are unknown, so
    provenance is `supplied_document` and source_url is a stable `supplied://` reference."""
    members, problems = read_zip(path)
    records = {}
    for name, data in members:
        url = f"supplied://{collection}/{quote(name)}"
        try:
            records[name] = pdf_to_record(data, url, "supplied_document", ocr=ocr,
                                          title=name.removesuffix(".pdf"))
        except IngestError as e:
            problems.append(f"{name}: {e}")
    for name, n in distinct_words({n: r["content"] for n, r in records.items()}).items():
        if n < MIN_DISTINCT_WORDS:
            problems.append(f"{name}: only {n} words differ from text shared with the other "
                            "files; the details are probably in images (--ocr only applies to "
                            "PDFs with no text layer)")
            del records[name]
    return list(records.values()), problems


# --- manifest of public sources --------------------------------------------------------------

def load_manifest(path: Path) -> list[dict]:
    data = load_json_records(path)
    for entry in data:
        if not isinstance(entry, dict) or not str(entry.get("url", "")).startswith("https://"):
            raise IngestError(f"{path.name}: every entry needs an https url: {entry}")
    return data


def manifest_records(entries: list[dict], delay_s: float = 1.0, ocr: bool = False
                     ) -> tuple[list[dict], list[str], set[str]]:
    """Fetch each manifest source politely. Returns records, errors and all URLs to keep.
    Entries marked "ocr": true are scanned PDFs, fetched only when OCR is enabled."""
    records, errors, keep = [], [], set()
    meta_keys = ("title", "category", "campus", "programme", "semester", "publication_date")
    fetched = 0
    for entry in entries:
        keep.add(entry["url"])
        if entry.get("ocr") and not ocr:
            errors.append(f"{entry['url']}: scanned PDF, needs OCR (install requirements-ocr.txt, "
                          "then ingest with --ocr)")
            continue
        if fetched:
            time.sleep(delay_s)
        fetched += 1
        try:
            rec = fetch(entry["url"], ocr=bool(entry.get("ocr")))
        except IngestError as e:
            errors.append(str(e))
            continue
        keep.add(rec["source_url"])
        rec.update({k: entry[k] for k in meta_keys if entry.get(k) is not None})
        records.append(rec)
    return records, errors, keep


def ingest_records(conn: psycopg.Connection, records: list[dict]) -> dict[str, int]:
    counts = {"inserted": 0, "updated": 0, "metadata_updated": 0, "unchanged": 0, "duplicate": 0,
              "error": 0}
    for rec in records:
        try:
            counts[ingest_document(conn, build_document(rec))] += 1
        except IngestError as e:
            print(f"skip: {e}", file=sys.stderr)
            counts["error"] += 1
    return counts


def prune(conn: psycopg.Connection, keep: set[str]) -> list[str]:
    """Delete official_web documents no longer listed in the manifest (chunks cascade)."""
    rows = conn.execute("SELECT source_url FROM documents WHERE provenance = 'official_web'")
    gone = [r["source_url"] for r in rows if r["source_url"] not in keep]
    for url in gone:
        db.delete_by_url(conn, url)
    return gone


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest documents into CampusSearch.")
    ap.add_argument("json_files", nargs="*", type=Path, help="JSON lists of records (seed data)")
    ap.add_argument("--url", action="append", default=[], help="public HTML page or PDF")
    ap.add_argument("--manifest", type=Path, help="JSON list of official sources")
    ap.add_argument("--prune", action="store_true",
                    help="with --manifest: delete official web documents no longer listed")
    ap.add_argument("--zip", type=Path, help="local ZIP of PDFs (provenance: supplied_document)")
    ap.add_argument("--collection", default="supplied", help="name used in supplied:// references")
    ap.add_argument("--category", help="with --zip: category for every document in the ZIP")
    ap.add_argument("--delete", action="append", default=[], metavar="SOURCE_URL",
                    help="delete a document and its chunks")
    ap.add_argument("--reindex", action="store_true", help="chunk and embed afterwards")
    ap.add_argument("--ocr", action="store_true",
                    help="recognise scanned PDFs with OCR (optional: pip install -r requirements-ocr.txt)")
    args = ap.parse_args()
    if args.prune and not args.manifest:
        ap.error("--prune requires --manifest")

    errors: list[str] = []
    records: list[dict] = []
    keep: set[str] = set()
    try:
        for path in args.json_files:
            records += load_json_records(path)
        if args.manifest:
            got, errs, keep = manifest_records(load_manifest(args.manifest), ocr=args.ocr)
            records += got
            errors += errs
        if args.zip:
            got, errs = zip_records(args.zip, args.collection, ocr=args.ocr)
            for rec in got:
                rec["category"] = args.category
            records += got
            errors += errs
    except IngestError as e:
        sys.exit(f"error: {e}")
    for url in args.url:
        try:
            records.append(fetch(url, ocr=args.ocr))
        except IngestError as e:
            errors.append(str(e))

    try:
        conn = db.connect()
    except psycopg.OperationalError:
        sys.exit("error: cannot connect to PostgreSQL. Is it running? (docker compose up -d db)")
    with conn:
        db.init_schema(conn)
        for url in args.delete:
            print(f"delete {url}: {'done' if db.delete_by_url(conn, url) else 'not found'}")
        print(ingest_records(conn, records))
        if args.prune:
            for url in prune(conn, keep):
                print(f"pruned (no longer in manifest): {url}")
        if args.reindex:
            from app.embeddings import get_embedder
            from app.reindex import index_documents

            embedder, why = get_embedder()
            if embedder is None:
                print(f"reindex skipped: {why}; keyword search still works", file=sys.stderr)
            else:
                print("reindex:", index_documents(conn, embedder))
    for e in errors:
        print(f"skip: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
