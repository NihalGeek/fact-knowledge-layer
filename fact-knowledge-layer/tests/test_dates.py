"""Unit tests for fiscal-period parsing."""
from app.models import PeriodKind
from app.normalize.dates import parse_period


def test_fy_short_and_long():
    assert parse_period("FY24").fy_end_year == 2024
    assert parse_period("FY2024").fy_end_year == 2024
    assert parse_period("Fiscal 2024").fy_end_year == 2024


def test_fy_span_end_year():
    # 2024-25 and FY2024/25 both mean the fiscal year ENDING in 2025.
    assert parse_period("2024-25").fy_end_year == 2025
    assert parse_period("FY2024/25").fy_end_year == 2025
    assert parse_period("FY25").fy_end_year == 2025


def test_span_alignment_across_publishers():
    # Economic Survey "FY25", RBI "2024-25", IMF "FY2024/25" -> same period key.
    keys = {parse_period("FY25").comparable_key(),
            parse_period("2024-25").comparable_key(),
            parse_period("FY2024/25").comparable_key()}
    assert keys == {"FY2025"}


def test_quarter():
    p = parse_period("Q4 FY24")
    assert p.kind == PeriodKind.QUARTER and p.quarter == 4 and p.fy_end_year == 2024


def test_year_ended_date_is_fiscal_year():
    p = parse_period("year ended March 31, 2024")
    assert p.kind == PeriodKind.FISCAL_YEAR and p.fy_end_year == 2024


def test_as_of_date():
    p = parse_period("as of December 31, 2021")
    assert p.kind == PeriodKind.AS_OF and p.end_date.isoformat() == "2021-12-31"


def test_nine_months_ended_is_range():
    p = parse_period("nine months ended December 31, 2021")
    assert p.kind == PeriodKind.RANGE and p.end_date.isoformat() == "2021-12-31"


def test_unknown_period():
    assert parse_period("some text with no period").kind == PeriodKind.UNKNOWN
