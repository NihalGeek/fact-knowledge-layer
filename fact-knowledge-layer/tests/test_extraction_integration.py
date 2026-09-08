"""Integration tests over a real sample PDF (the 27-page earnings deck — fast).

These assert that the deterministic pipeline extracts the expected, evidence-
grounded facts with correct normalization, entity and period — without any
hard-coding of those facts in the code under test.
"""
import glob
import pytest

from app.ingestion.pdf_loader import load_path
from app.extraction.extractor import extract_facts

DECK = next(iter(glob.glob("sample_docs/**/03-delhivery-q4*.pdf", recursive=True)), None)
pytestmark = pytest.mark.skipif(DECK is None, reason="sample deck PDF not present")


@pytest.fixture(scope="module")
def facts():
    doc = load_path(DECK)
    fs, _ = extract_facts(doc, doc.sha256[:8])
    return fs


def test_entity_detected(facts):
    assert any(f.canonical_entity == "Delhivery" for f in facts)


def test_team_size_extracted_with_period(facts):
    ts = [f for f in facts if f.canonical_attribute == "team size"
          and "63,713" in f.raw_value]
    assert ts, "team size 63,713 not extracted"
    assert ts[0].period.comparable_key() == "Q4-FY2024"
    assert ts[0].evidence.page == 8


def test_revenue_scaled_to_crore(facts):
    rev = [f for f in facts if f.canonical_attribute == "revenue"
           and "8,142" in f.raw_value]
    assert rev, "revenue ₹8,142 Cr not extracted"
    assert abs(rev[0].normalized_value - 8.142e10) < 1e8   # crore scaling applied
    assert rev[0].period.fy_end_year == 2024


def test_every_fact_has_evidence(facts):
    for f in facts:
        assert f.evidence.text.strip()
        assert 1 <= f.evidence.page <= 27
        assert f.source_document_name.endswith(".pdf")


def test_no_unscaled_monetary_facts(facts):
    # A monetary fact must have a currency + a plausible magnitude (never a bare
    # rupee value like ₹8,142 with no scale).
    for f in facts:
        if f.fact_type.value == "monetary" and f.canonical_attribute == "revenue":
            assert f.normalized_value is None or f.normalized_value > 1e6
