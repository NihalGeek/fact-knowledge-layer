"""Unit tests for deterministic unit / currency / scale normalization."""
from app.normalize.units import parse_value, values_equivalent


def test_crore_and_million_equivalent():
    a = parse_value("₹8,142 Cr")
    b = parse_value("₹81,415.38 million")
    assert a.currency == "INR" and b.currency == "INR"
    assert abs(a.normalized_value - 8.142e10) < 1
    ok, why = values_equivalent(a, b)
    assert ok, why                      # reconcile across units via rounding


def test_lakh_crore_scaling():
    assert parse_value("₹5 lakh").normalized_value == 5e5
    assert parse_value("₹2 crore").normalized_value == 2e7
    assert parse_value("$3 billion").normalized_value == 3e9


def test_percentages_not_equal_when_distinct():
    ok, _ = values_equivalent(parse_value("6.4 per cent"), parse_value("6.5 per cent"))
    assert not ok                       # 6.4 vs 6.5 is a real difference


def test_percent_equal_when_same():
    ok, _ = values_equivalent(parse_value("3.5 percent"), parse_value("3.5 per cent"))
    assert ok


def test_parentheses_negative():
    assert parse_value("(404)").normalized_value == -404


def test_rounding_bound_direction():
    # A precise figure that rounds to the coarse one corroborates.
    ok, _ = values_equivalent(parse_value("₹81,415 million"), parse_value("₹81,415.38 million"))
    assert ok
    # Figures differing by more than the relative tolerance do not corroborate.
    ok2, _ = values_equivalent(parse_value("8,142 Cr"), parse_value("8,300 Cr"))
    assert not ok2


def test_abstain_on_unparseable():
    assert parse_value("n/a") is None
    assert parse_value("") is None


def test_currency_type_incompatible_units():
    ok, why = values_equivalent(parse_value("6.5 percent"), parse_value("₹6.5 crore"))
    assert not ok and "unit" in why
