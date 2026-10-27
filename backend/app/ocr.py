"""Optional OCR for scanned PDFs (RapidOCR on CPU). Imported only when OCR is requested.

Raises OcrError when the optional packages are missing, a page cannot be rendered, or the
recognised text is too short or too uncertain to be stored as a document.
"""

import statistics

from app.pdf import MIN_TOTAL_CHARS

MIN_MEAN_CONFIDENCE = 0.75  # below this the text is too unreliable to index
RENDER_SCALE = 2.0          # ~144 dpi; small print needs it, larger costs time with little gain


class OcrError(Exception):
    pass


_engine = None


def _load():
    global _engine
    try:
        import numpy as np
        import pypdfium2 as pdfium
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        raise OcrError("OCR is not installed; run: pip install -r requirements-ocr.txt") from None
    if _engine is None:
        _engine = RapidOCR()
    return np, pdfium, _engine


def ocr_pdf(data: bytes) -> tuple[list[str], float]:
    """Text per page and the mean recognition confidence over all recognised lines."""
    np, pdfium, engine = _load()
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as e:
        raise OcrError(f"cannot render PDF for OCR ({type(e).__name__})") from None
    pages, confidences = [], []
    for page in pdf:
        result, _ = engine(np.array(page.render(scale=RENDER_SCALE).to_pil()))
        lines = [(str(r[1]), float(r[2])) for r in result or []]
        pages.append("\n".join(text for text, _ in lines))
        confidences += [conf for _, conf in lines]
    total = sum(len(p.strip()) for p in pages)
    if total < MIN_TOTAL_CHARS:
        raise OcrError(f"OCR recognised only {total} characters")
    mean_conf = statistics.mean(confidences)
    if mean_conf < MIN_MEAN_CONFIDENCE:
        raise OcrError(f"OCR confidence too low ({mean_conf:.2f} < {MIN_MEAN_CONFIDENCE})")
    return pages, mean_conf
