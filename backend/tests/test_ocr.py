"""Optional OCR for scanned PDFs (Phase 6B, workstream 4)."""

import importlib.util
import io
import sys

import pytest

from app import ingest, ocr
from app.ingest import IngestError, manifest_records, pdf_to_record
from pdf_factory import make_pdf

SCANNED = make_pdf([[""], [""]])  # readable PDF, no text layer
URL = "https://example.org/regulation.pdf"
OCR_INSTALLED = all(importlib.util.find_spec(m) for m in ("rapidocr_onnxruntime", "pypdfium2", "PIL"))


def fake_ocr(pages, conf=0.92):
    return lambda data: (pages, conf)


def test_scanned_pdf_without_ocr_explains_how_to_enable_it():
    with pytest.raises(IngestError, match="scanned or image-based .*--ocr"):
        pdf_to_record(SCANNED, URL, "official_web")


def test_ocr_text_keeps_pages_provenance_and_is_flagged(monkeypatch):
    pages = ["Regulation for Student Code of Conduct. Students shall carry identity cards at all times.",
             "Loitering in the corridors during lecture hours is not permitted. Page 2"]
    monkeypatch.setattr(ocr, "ocr_pdf", fake_ocr(pages))
    rec = pdf_to_record(SCANNED, URL, "official_web", ocr=True)
    assert rec["provenance"] == "official_web" and rec["source_type"] == "pdf"
    assert len(rec["page_starts"]) == 2 and rec["content"][rec["page_starts"][1]:].startswith("Loitering")
    assert rec["extraction_status"] == "warnings"
    assert "text recognised by OCR (mean confidence 0.92)" in rec["extraction_note"]


def test_text_layer_pdfs_never_go_through_ocr(monkeypatch):
    def boom(data):
        raise AssertionError("OCR must not run for a PDF with a text layer")

    monkeypatch.setattr(ocr, "ocr_pdf", boom)
    from test_documents import three_page_pdf
    assert pdf_to_record(three_page_pdf(), URL, "official_web", ocr=True)["content"]


@pytest.mark.parametrize("error", ["OCR recognised only 12 characters",
                                   "OCR confidence too low (0.41 < 0.75)"])
def test_ocr_failure_is_explicit(monkeypatch, error):
    def fail(data):
        raise ocr.OcrError(error)

    monkeypatch.setattr(ocr, "ocr_pdf", fail)
    with pytest.raises(IngestError, match=f"OCR failed for {URL}: {error[:20]}"):
        pdf_to_record(SCANNED, URL, "official_web", ocr=True)


def test_missing_ocr_packages_give_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)  # makes the import fail
    monkeypatch.setattr(ocr, "_engine", None)
    with pytest.raises(IngestError, match="OCR is not installed; run: pip install -r requirements-ocr.txt"):
        pdf_to_record(SCANNED, URL, "official_web", ocr=True)


def test_manifest_skips_ocr_sources_unless_enabled(monkeypatch):
    calls = []
    monkeypatch.setattr(ingest, "fetch", lambda url, ocr=False: calls.append((url, ocr)) or {
        "title": "t", "content": "c", "source_url": url, "source_type": "pdf", "provenance": "official_web"})
    entries = [{"url": "https://example.org/scan.pdf", "ocr": True}, {"url": "https://example.org/page"}]
    records, errors, keep = manifest_records(entries, delay_s=0)
    assert [r["source_url"] for r in records] == ["https://example.org/page"]
    assert "needs OCR" in errors[0] and "https://example.org/scan.pdf" in keep  # never pruned
    calls.clear()
    manifest_records(entries, delay_s=0, ocr=True)
    assert calls == [("https://example.org/scan.pdf", True), ("https://example.org/page", False)]


@pytest.mark.skipif(not OCR_INSTALLED, reason="optional OCR packages not installed")
def test_real_ocr_on_an_image_only_pdf():
    from PIL import Image, ImageDraw, ImageFont

    lines = ["CHRIST University Regulation", "Students must carry their identity card",
             "on campus at all times. Attendance is", "recorded in every class by the teacher.",
             "Loitering in the corridors during lecture", "hours is not permitted on any campus.",
             "Students should use the library during", "free periods between their classes."]
    img = Image.new("RGB", (1700, 1200), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=48)
    for i, line in enumerate(lines):
        draw.text((80, 80 + i * 130), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    with pytest.raises(IngestError):  # no text layer
        pdf_to_record(buf.getvalue(), URL, "official_web")
    rec = pdf_to_record(buf.getvalue(), URL, "official_web", ocr=True)
    text = rec["content"].lower()
    assert "identity card" in text and "attendance" in text
    assert "OCR" in rec["extraction_note"]
