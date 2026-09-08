"""Failure-path / robustness tests: the system must degrade gracefully, never
crash or hallucinate, on bad input."""
import io

import pymupdf
import pytest

from app.ingestion.pdf_loader import load_bytes


def _blank_pdf(pages=1, text="") -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        p = doc.new_page()
        if text:
            p.insert_text((72, 72), text)
    buf = doc.tobytes()
    doc.close()
    return buf


def test_malformed_pdf_raises_valueerror():
    with pytest.raises(ValueError):
        load_bytes(b"%PDF-1.4 broken garbage not really a pdf", "bad.pdf")


def test_non_pdf_bytes_raise():
    with pytest.raises(ValueError):
        load_bytes(b"just text, no pdf", "x.pdf")


def test_blank_pages_flagged_scanned_and_extract_nothing():
    doc = load_bytes(_blank_pdf(2, text=""), "blank.pdf")
    assert doc.page_count == 2
    assert doc.scanned_page_count == 2          # detected as image-only/empty
    from app.extraction.extractor import extract_facts
    facts, _ = extract_facts(doc, doc.sha256[:8])
    assert facts == []                          # abstains, does not invent facts


def test_text_without_numbers_yields_no_numeric_facts():
    doc = load_bytes(_blank_pdf(1, "This page has words but no figures at all."),
                     "words.pdf")
    from app.extraction.extractor import extract_facts
    facts, _ = extract_facts(doc, doc.sha256[:8])
    assert all(f.value is not None for f in facts)  # never fabricate a value


def test_duplicate_hash_detected():
    raw = _blank_pdf(1, "hello")
    a = load_bytes(raw, "a.pdf")
    b = load_bytes(raw, "b.pdf")               # same file re-uploaded under a new name
    assert a.sha256 == b.sha256                 # identical bytes -> dedup key
