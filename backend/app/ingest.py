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
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        self._in_title = tag == "title"

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_record(html: str, url: str) -> dict:
    parser = _TextExtractor()
    parser.feed(html)
    return {
        "title": parser.title or url,
        "content": " ".join(parser.parts),
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
