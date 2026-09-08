"""Tests for the intra-document accounting-identity checker."""
from app.models import Evidence, Fact, FactType, Period, PeriodKind
from app.reasoning.consistency import check_facts


def _f(fid, family, norm, page, scope="consolidated"):
    ev = Evidence(document_id="d1", document_name="d1.pdf", page=page, text="e")
    return Fact(
        fact_id=fid, entity="Delhivery", canonical_entity="Delhivery",
        attribute=family, canonical_attribute=family, fact_type=FactType.MONETARY,
        raw_value=f"₹{norm:.0f}", normalized_value=norm, normalized_unit="INR",
        currency="INR", period=Period(kind=PeriodKind.FISCAL_YEAR, label="FY2024", fy_end_year=2024),
        scope=scope, source_document_id="d1", source_document_name="d1.pdf",
        source_page=page, evidence=ev, extraction_confidence=0.8,
    )


def test_identity_holds():
    facts = [_f("a", "total income", 85942.34e6, 22),
             _f("b", "revenue", 81415.38e6, 22),
             _f("c", "other income", 4526.96e6, 22)]
    checks = check_facts(facts)
    assert len(checks) == 1
    assert checks[0].ok and checks[0].delta < 1


def test_identity_mismatch_flagged():
    facts = [_f("a", "total income", 90000e6, 22),      # wrong total
             _f("b", "revenue", 81415.38e6, 22),
             _f("c", "other income", 4526.96e6, 22)]
    checks = check_facts(facts)
    assert checks and not checks[0].ok


def test_requires_colocation():
    facts = [_f("a", "total income", 85942.34e6, 22),
             _f("b", "revenue", 81415.38e6, 22),
             _f("c", "other income", 4526.96e6, 80)]   # far-away page
    assert check_facts(facts) == []                     # not co-located -> skipped
