"""Unit tests for entity resolution and attribute canonicalization."""
from app.normalize.attributes import canonical_family, family_kind, normalize_attribute
from app.normalize.entities import normalize_entity, resolve_entity, detect_primary_entity


def test_entity_normalization():
    assert normalize_entity("Delhivery Limited") == "delhivery"
    assert normalize_entity("Delhivery") == "delhivery"


def test_generic_reference_resolves_to_primary():
    disp, key = resolve_entity("the Company", "Delhivery")
    assert disp == "Delhivery" and key == "delhivery"


def test_detect_primary_entity_from_content():
    text = ("India India India India India economy grew. " * 20)
    assert detect_primary_entity(text, hint="report.pdf") == "India"


def test_revenue_family_merges_total_lines():
    assert canonical_family("Revenue from operations") == "revenue"
    assert canonical_family("Revenue from services") == "revenue"


def test_revenue_subcomponent_kept_separate():
    fam = canonical_family("Revenue from Express Parcel services")
    assert fam != "revenue"                      # sub-component must not merge


def test_gdp_growth_vs_share_and_nominal():
    # canonical_family only sees the phrase; growth marker in the phrase counts.
    assert canonical_family("real GDP growth") == "gdp growth"
    assert canonical_family("nominal GDP growth") == "nominal gdp growth"


def test_family_kind():
    assert family_kind("revenue") == "monetary"
    assert family_kind("team size") == "count"
    assert family_kind("gdp growth") == "percent"


def test_scope_words_stripped_from_norm():
    assert "consolidated" not in normalize_attribute("Consolidated revenue from operations")
