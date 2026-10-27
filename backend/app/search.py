"""Custom inverted index + Okapi BM25 (no search libraries).

BM25, for query Q and document D:

    score(D, Q) = sum over unique terms t in Q of
        IDF(t) * tf(t,D) * (k1 + 1) / (tf(t,D) + k1 * (1 - b + b * |D| / avgdl))

    IDF(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))      (Lucene variant, always > 0)

Defaults k1 = 1.5, b = 0.75. Repeated query terms count once ("fee fee" == "fee").
Ties are broken by document id ascending, so ordering is deterministic.
Title is indexed together with content.
"""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from app.models import Document

STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was "
    "were will with".split()
)
_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    return [t for t in _TOKEN.findall(text) if t not in STOPWORDS]


@dataclass(frozen=True)
class ScoredDoc:
    doc: Document
    score: float


class SearchIndex:
    def __init__(self, docs: list[Document] = (), k1: float = 1.5, b: float = 0.75) -> None:
        if k1 < 0 or not 0 <= b <= 1:
            raise ValueError("require k1 >= 0 and 0 <= b <= 1")
        self.k1, self.b = k1, b
        self.build(list(docs))

    def build(self, docs: list[Document]) -> None:
        self.docs: dict[str, Document] = {}
        self.postings: dict[str, dict[str, int]] = {}  # term -> {doc_id: tf}
        self.doc_len: dict[str, int] = {}
        for d in docs:
            self._add(d)

    def upsert(self, doc: Document) -> None:
        self.remove(doc.id)
        self._add(doc)

    def remove(self, doc_id: str) -> None:
        if doc_id not in self.docs:
            return
        for term in tokenize(self._text(self.docs.pop(doc_id))):
            plist = self.postings.get(term)
            if plist and plist.pop(doc_id, None) is not None and not plist:
                del self.postings[term]
        del self.doc_len[doc_id]

    def _text(self, d: Document) -> str:
        return f"{d.title} {d.content}"

    def _add(self, d: Document) -> None:
        tokens = tokenize(self._text(d))
        self.docs[d.id] = d
        self.doc_len[d.id] = len(tokens)
        for term, tf in Counter(tokens).items():
            self.postings.setdefault(term, {})[d.id] = tf

    @property
    def avgdl(self) -> float:
        return sum(self.doc_len.values()) / len(self.doc_len) if self.doc_len else 0.0

    def df(self, term: str) -> int:
        return len(self.postings.get(term, {}))

    def idf(self, term: str) -> float:
        n, df = len(self.docs), self.df(term)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(
        self, query: str, keep: Callable[[Document], bool] = lambda d: True
    ) -> list[ScoredDoc]:
        terms = set(tokenize(query))
        avgdl = self.avgdl or 1.0
        scores: dict[str, float] = {}
        for t in terms:
            idf = self.idf(t)
            for doc_id, tf in self.postings.get(t, {}).items():
                norm = tf + self.k1 * (1 - self.b + self.b * self.doc_len[doc_id] / avgdl)
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * tf * (self.k1 + 1) / norm
        hits = [ScoredDoc(self.docs[i], s) for i, s in scores.items() if keep(self.docs[i])]
        return sorted(hits, key=lambda h: (-h.score, h.doc.id))


def snippet(content: str, query: str, width: int = 220) -> str:
    """Window of `content` around the first query-term match (or its start)."""
    terms = set(tokenize(query))
    start = 0
    for m in re.finditer(r"\w+", content):
        if m.group().casefold() in terms:
            start = max(0, m.start() - width // 3)
            break
    text = content[start : start + width]
    return ("…" if start else "") + text + ("…" if start + width < len(content) else "")
