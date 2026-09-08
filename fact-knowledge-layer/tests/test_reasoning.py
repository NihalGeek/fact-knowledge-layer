"""Unit tests for the deterministic relationship classifier, using synthetic
facts so the logic is tested in isolation from extraction."""
from app.models import (
    Evidence, Fact, FactStatus, FactType, Period, PeriodKind, RelationType,
)
from app.reasoning.relationships import classify


def _fact(fid, value, unit, family, *, period, fy=None, kind=PeriodKind.FISCAL_YEAR,
          norm, ntype, scope=None, status=FactStatus.UNKNOWN, doc="docX",
          currency=None, raw=None):
    ev = Evidence(document_id=doc, document_name=doc, page=1, text="evidence")
    p = Period(kind=kind, label=period, fy_end_year=fy)
    return Fact(
        fact_id=fid, entity="India", canonical_entity="India",
        attribute=family, canonical_attribute=family, fact_type=ntype,
        raw_value=raw or f"{value}{unit}", value=value, normalized_value=norm,
        normalized_unit=unit if unit else "count", currency=currency, period=p,
        status=status, scope=scope, source_document_id=doc, source_document_name=doc,
        source_page=1, evidence=ev, extraction_confidence=0.8,
    )


def test_corroboration_same_value_same_period():
    a = _fact("a", 8142, "INR", "revenue", period="FY2024", fy=2024,
              norm=8.142e10, ntype=FactType.MONETARY, currency="INR", raw="₹8,142 Cr", doc="d1")
    b = _fact("b", 81415.38, "INR", "revenue", period="FY2024", fy=2024,
              norm=8.141538e10, ntype=FactType.MONETARY, currency="INR",
              raw="₹81,415.38 million", doc="d2")
    r = classify(a, b)
    assert r.relation == RelationType.CORROBORATES
    assert r.confidence > 0.8


def test_contradiction_same_period_diff_value():
    a = _fact("a", 6.4, "percent", "gdp growth", period="FY2025", fy=2025,
              norm=6.4, ntype=FactType.PERCENTAGE, raw="6.4 per cent", doc="d1")
    b = _fact("b", 6.5, "percent", "gdp growth", period="FY2025", fy=2025,
              norm=6.5, ntype=FactType.PERCENTAGE, raw="6.5 per cent", doc="d2")
    r = classify(a, b)
    assert r.relation == RelationType.CONTRADICTS


def test_contextual_difference_by_period():
    a = _fact("a", 86184, "", "team size", period="period ended 2021-12-31",
              kind=PeriodKind.RANGE, fy=2022, norm=86184, ntype=FactType.COUNT,
              raw="86,184", doc="d1")
    b = _fact("b", 63713, "", "team size", period="Q4 FY2024",
              kind=PeriodKind.QUARTER, fy=2024, norm=63713, ntype=FactType.COUNT,
              raw="63,713", doc="d2")
    r = classify(a, b)
    assert r.relation == RelationType.CONTEXTUALLY_DIFFERENT
    assert "period" in r.explanation.lower()


def test_contextual_difference_by_scope():
    a = _fact("a", 74540.82, "INR", "revenue", period="FY2024", fy=2024,
              norm=7.454e10, ntype=FactType.MONETARY, currency="INR",
              scope="standalone", doc="d1")
    b = _fact("b", 81415.38, "INR", "revenue", period="FY2024", fy=2024,
              norm=8.1415e10, ntype=FactType.MONETARY, currency="INR",
              scope="consolidated", doc="d2")
    r = classify(a, b)
    assert r.relation == RelationType.CONTEXTUALLY_DIFFERENT
    assert "scope" in " ".join(r.context_differences).lower()


def test_not_comparable_percent_vs_money():
    a = _fact("a", 6.5, "percent", "x", period="FY2025", fy=2025, norm=6.5,
              ntype=FactType.PERCENTAGE, doc="d1")
    b = _fact("b", 100, "INR", "x", period="FY2025", fy=2025, norm=100,
              ntype=FactType.MONETARY, currency="INR", doc="d2")
    r = classify(a, b)
    assert r.relation == RelationType.RELATED_NOT_COMPARABLE


def test_different_entity_not_related():
    a = _fact("a", 5, "percent", "x", period="FY2025", fy=2025, norm=5,
              ntype=FactType.PERCENTAGE, doc="d1")
    b = _fact("b", 5, "percent", "x", period="FY2025", fy=2025, norm=5,
              ntype=FactType.PERCENTAGE, doc="d2")
    b.canonical_entity = "Delhivery"
    assert classify(a, b) is None


def test_low_confidence_diff_abstains():
    a = _fact("a", 6.4, "percent", "gdp growth", period="FY2025", fy=2025,
              norm=6.4, ntype=FactType.PERCENTAGE, doc="d1")
    b = _fact("b", 6.5, "percent", "gdp growth", period="FY2025", fy=2025,
              norm=6.5, ntype=FactType.PERCENTAGE, doc="d2")
    a.extraction_confidence = 0.4
    b.extraction_confidence = 0.4
    r = classify(a, b)
    assert r.relation == RelationType.UNCERTAIN
