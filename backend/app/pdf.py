"""PDF text extraction with cleanup and quality checks (pypdf; no OCR).

Returns per-page text so chunks can carry page numbers. Raises PdfError when a PDF is
unreadable, encrypted or has (almost) no extractable text, e.g. scanned or poster images.
"""

import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

import pypdf
from pypdf.errors import PdfReadError

MIN_TOTAL_CHARS = 200     # below this the "document" is effectively empty
LOW_TEXT_PER_PAGE = 80    # pages under this are likely image-only
HEADER_FOOTER_SHARE = 0.6  # a line on >= 60% of pages (3+ pages) is a header/footer


class PdfError(Exception):
    def __init__(self, message: str, scanned: bool = False):
        super().__init__(message)
        self.scanned = scanned  # readable PDF without a usable text layer (OCR may help)


@dataclass
class PdfText:
    title: str | None
    pages: list[str]
    warnings: list[str] = field(default_factory=list)


GLUED_MIN_LETTERS = 19  # a run of 19+ letters is almost never a real English word
_GLUED = re.compile(rf"[A-Za-z]{{{GLUED_MIN_LETTERS},}}")


def glued_words(text: str) -> int:
    """Count letter runs long enough to be several words with their spaces lost."""
    return len(_GLUED.findall(text))


def choose_page_text(plain: str, layout) -> tuple[str, bool]:
    """Keep pypdf's plain text unless it has lost word spacing and layout mode repairs it.

    `layout` is a zero-argument callable, so the slower layout extraction only runs for pages
    that need it. Plain text is preferred because layout mode can detach table cells (e.g. a
    calendar date from its event).
    """
    if glued_words(plain) == 0:
        return plain, False
    alt = layout() or ""
    # a spacing repair adds spaces; it must not lose content (empty or truncated layout output)
    keeps_content = _alnum(alt) >= 0.95 * _alnum(plain)
    if keeps_content and glued_words(alt) < glued_words(plain):
        return alt, True
    return plain, False


def _alnum(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


_PAGE_NUMBER = re.compile(r"^(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?$", re.I)
# drop-cap artefact: "C\nommencement" -> "Commencement"
_SPLIT_CAP = re.compile(r"(?<![A-Za-z])([A-Z])\n(?=[a-z]{2})")


def _unspace(line: str) -> str:
    """'T h i s  i s' -> 'This is' when a line is letter-spaced."""
    tokens = line.split(" ")
    singles = sum(len(t) == 1 for t in tokens if t)
    if len(tokens) < 6 or singles / max(1, len([t for t in tokens if t])) < 0.7:
        return line
    return " ".join("".join(word.split(" ")) for word in re.split(r" {2,}", line))


def clean_pages(pages: list[str]) -> tuple[list[str], list[str]]:
    warnings = []
    lines_per_page = []
    for text in pages:
        text = _SPLIT_CAP.sub(r"\1", text.replace("\r", ""))
        lines_per_page.append([_unspace(l).strip() for l in text.split("\n")])

    if len(pages) >= 3:
        counts = Counter(l for lines in lines_per_page for l in set(lines) if l)
        repeated = {l for l, n in counts.items() if n / len(pages) >= HEADER_FOOTER_SHARE}
        if repeated:
            warnings.append(f"removed {len(repeated)} repeated header/footer line(s)")
    else:
        repeated = set()

    cleaned = []
    for lines in lines_per_page:
        kept = [l for l in lines if l and l not in repeated and not _PAGE_NUMBER.match(l)]
        text = "\n".join(kept)
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # de-hyphenate line breaks
        # NFKC here so later normalization cannot change lengths (page offsets stay valid)
        cleaned.append(" ".join(unicodedata.normalize("NFKC", text).split()))
    return cleaned, warnings


def extract_pdf(data: bytes) -> PdfText:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise PdfError("PDF is encrypted")
        raw, relaid, still_glued = [], [], []
        for n, page in enumerate(reader.pages, start=1):
            text, used_layout = choose_page_text(
                page.extract_text() or "", lambda p=page: p.extract_text(extraction_mode="layout"))
            raw.append(text)
            if used_layout:
                relaid.append(n)
            elif glued_words(text):
                still_glued.append(n)
        title = (reader.metadata or {}).get("/Title")
    except PdfError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as e:
        raise PdfError(f"unreadable PDF ({type(e).__name__})") from None
    if not raw:
        raise PdfError("PDF has no pages")

    pages, warnings = clean_pages(raw)
    if relaid:
        warnings.append(f"word spacing repaired with layout extraction on page(s) {relaid[:10]}")
    if still_glued:
        warnings.append(f"possible missing word spaces on {len(still_glued)} page(s) "
                        f"{still_glued[:10]}; needs manual inspection")
    total = sum(len(p) for p in pages)
    if total < MIN_TOTAL_CHARS:
        raise PdfError(f"only {total} characters of text extracted; the PDF is probably "
                       "scanned or image-based (ingest with --ocr if OCR is installed)", scanned=True)
    low = [i + 1 for i, p in enumerate(pages) if len(p) < LOW_TEXT_PER_PAGE]
    if low:
        warnings.append(f"little or no text on page(s) {low[:10]}; content may be in images")
    return PdfText(str(title).strip() if title else None, pages, warnings)


def join_pages(pages: list[str]) -> tuple[str, list[int]]:
    """Content string plus the character offset where each page starts."""
    starts, parts, pos = [], [], 0
    for p in pages:
        starts.append(pos)  # an empty page starts where the next text starts
        if p:
            parts.append(p)
            pos += len(p) + 1  # joined with one space
    return " ".join(parts), starts
