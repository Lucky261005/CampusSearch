"""Ingestion: local JSON records and public HTML pages -> normalized, deduplicated documents.

CLI:  python -m app.ingest data/seed_documents.json [--url https://...]
"""

import argparse
import hashlib
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

import psycopg
from pydantic import ValidationError

from app import db
from app.models import Document

MAX_HTML_BYTES = 2_000_000
FETCH_TIMEOUT_S = 10

Outcome = Literal["inserted", "updated", "unchanged", "duplicate"]


class IngestError(Exception):
    pass


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


def ingest_document(conn: psycopg.Connection, doc: Document) -> Outcome:
    existing = db.get_by_url(conn, doc.source_url)
    if existing and existing.content_hash == doc.content_hash:
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


def fetch_html(url: str) -> dict:
    """Fetch a public page. No auth, no cookies; anything else raises IngestError."""
    if not url.startswith(("http://", "https://")):
        raise IngestError(f"unsupported URL scheme: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "CampusSearchBot/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as res:
            if "text/html" not in res.headers.get("Content-Type", ""):
                raise IngestError(f"not HTML: {url}")
            body = res.read(MAX_HTML_BYTES + 1)
            charset = res.headers.get_content_charset() or "utf-8"
            url = res.geturl()  # after redirects, so aliases dedupe by URL
    except IngestError:
        raise
    except Exception as e:  # HTTPError, URLError, timeouts, resets
        raise IngestError(f"fetch failed {url}: {e}") from None
    if len(body) > MAX_HTML_BYTES:
        raise IngestError(f"page too large: {url}")
    return html_to_record(body.decode(charset, errors="replace"), url)


def ingest_records(conn: psycopg.Connection, records: list[dict]) -> dict[str, int]:
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "duplicate": 0, "error": 0}
    for rec in records:
        try:
            counts[ingest_document(conn, build_document(rec))] += 1
        except IngestError as e:
            print(f"skip: {e}", file=sys.stderr)
            counts["error"] += 1
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_files", nargs="*", type=Path)
    ap.add_argument("--url", action="append", default=[], help="public HTML page to ingest")
    args = ap.parse_args()

    records: list[dict] = []
    for path in args.json_files:
        records += load_json_records(path)
    for url in args.url:
        try:
            records.append(fetch_html(url))
        except IngestError as e:
            print(f"skip: {e}", file=sys.stderr)

    with db.connect() as conn:
        db.init_schema(conn)
        print(ingest_records(conn, records))


if __name__ == "__main__":
    main()
