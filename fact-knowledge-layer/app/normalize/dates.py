"""Deterministic period / fiscal-year parsing.

Time is a first-class property of every fact. This module turns the many ways a
period is written (``FY24``, ``2024-25``, ``FY2024/25``, ``Q4 FY24``,
``year ended March 31, 2024``, ``as of December 31, 2021``) into a canonical
:class:`~app.models.Period`, using the Indian convention that a fiscal year ends
on 31 March (so FY2024 == 1 Apr 2023 – 31 Mar 2024).

Nothing here is specific to the starter documents; it is pattern-based.
"""
from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Optional

from app.models import Period, PeriodKind

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(calendar.month_name)
    if m
}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})

_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

# e.g. "March 31, 2024" or "31 March 2024" or "31 December 2021"
_DATE_RE = re.compile(
    rf"(?P<mon>{_MONTH_ALT})\.?\s+(?P<day>\d{{1,2}}),?\s+(?P<year>\d{{4}})"
    rf"|(?P<day2>\d{{1,2}})\s+(?P<mon2>{_MONTH_ALT})\.?,?\s+(?P<year2>\d{{4}})",
    re.IGNORECASE,
)

_QUARTER_RE = re.compile(
    r"\bQ(?P<q>[1-4])\s*[:\-]?\s*(?:FY)?\s*(?P<y>\d{2,4})(?:\s*[-/]\s*(?P<y2>\d{2,4}))?",
    re.IGNORECASE,
)

# "FY2024-25", "FY 2024/25", "FY24-25", "2024-25", "FY2024/25"
_FY_SPAN_RE = re.compile(
    r"\b(?:FY|F\.Y\.?|fiscal(?:\s+year)?)?\s*(?P<y1>\d{4}|\d{2})\s*[-/]\s*(?P<y2>\d{2,4})\b",
    re.IGNORECASE,
)

# "FY2024", "FY24", "Fiscal 2024", "fiscal year 2024"
_FY_SINGLE_RE = re.compile(
    r"\b(?:FY|F\.Y\.?|fiscal(?:\s+year)?)\s*[:\-]?\s*(?P<y>\d{4}|\d{2})\b",
    re.IGNORECASE,
)

_ENDED_RE = re.compile(
    r"(?P<n>\w+)?\s*months?\s+ended\s+(?P<rest>.+)", re.IGNORECASE
)
_YEAR_ENDED_RE = re.compile(
    r"(?:for\s+the\s+)?year\s+ended\s+(?P<rest>.+)", re.IGNORECASE
)
_ASOF_RE = re.compile(r"as\s+(?:of|at|on)\s+(?P<rest>.+)", re.IGNORECASE)


def _norm_year(token: str) -> int:
    y = int(token)
    if y < 100:                       # two-digit year -> 20xx
        y += 2000
    return y


def _end_year_from_span(y1: str, y2: str) -> int:
    """'2024-25' or 'FY2024/25' -> fiscal year *ending* 2025."""
    start = _norm_year(y1)
    end_two = int(y2)
    if end_two < 100:
        end = (start // 100) * 100 + end_two
        if end < start:               # e.g. 1999-00 -> 2000
            end += 100
    else:
        end = end_two
    return end


def _parse_date(text: str) -> Optional[date]:
    m = _DATE_RE.search(text)
    if not m:
        return None
    if m.group("mon"):
        mon = _MONTHS[m.group("mon").lower()]
        day = int(m.group("day"))
        year = int(m.group("year"))
    else:
        mon = _MONTHS[m.group("mon2").lower()]
        day = int(m.group("day2"))
        year = int(m.group("year2"))
    try:
        return date(year, mon, day)
    except ValueError:
        return None


def _fy_from_date(d: date) -> int:
    """India FY ends 31 Mar: months Apr-Dec belong to next year's FY."""
    return d.year + 1 if d.month >= 4 else d.year


def parse_period(text: str) -> Period:
    """Extract the strongest period signal from ``text``."""
    if not text:
        return Period()
    t = " ".join(text.split())

    # 1) Quarter (highest priority: most specific)
    qm = _QUARTER_RE.search(t)
    if qm:
        q = int(qm.group("q"))
        if qm.group("y2"):
            fy_end = _end_year_from_span(qm.group("y"), qm.group("y2"))
        else:
            fy_end = _norm_year(qm.group("y"))
        return Period(
            kind=PeriodKind.QUARTER, raw=qm.group(0).strip(),
            label=f"Q{q} FY{fy_end}", fy_end_year=fy_end, quarter=q,
        )

    # 2) "nine months ended <date>" -> RANGE
    em = _ENDED_RE.search(t)
    if em and "year ended" not in t.lower():
        d = _parse_date(em.group("rest"))
        if d:
            return Period(
                kind=PeriodKind.RANGE, raw=t.strip()[:80],
                label=f"period ended {d.isoformat()}", end_date=d,
                fy_end_year=_fy_from_date(d),
            )

    # 3) "year ended <date>" -> FISCAL_YEAR
    ym = _YEAR_ENDED_RE.search(t)
    if ym:
        d = _parse_date(ym.group("rest"))
        if d:
            fy = _fy_from_date(d)
            return Period(kind=PeriodKind.FISCAL_YEAR, raw=t.strip()[:80],
                          label=f"FY{fy}", fy_end_year=fy, end_date=d)

    # 4) "as of/at <date>" -> AS_OF
    am = _ASOF_RE.search(t)
    if am:
        d = _parse_date(am.group("rest"))
        if d:
            return Period(kind=PeriodKind.AS_OF, raw=am.group(0).strip()[:80],
                          label=f"as of {d.isoformat()}", end_date=d,
                          fy_end_year=_fy_from_date(d))

    # 5) Fiscal-year span "2024-25" / "FY2024/25"
    fs = _FY_SPAN_RE.search(t)
    if fs and not _looks_like_plain_range(fs, t):
        fy_end = _end_year_from_span(fs.group("y1"), fs.group("y2"))
        return Period(kind=PeriodKind.FISCAL_YEAR, raw=fs.group(0).strip(),
                      label=f"FY{fy_end}", fy_end_year=fy_end)

    # 6) Single fiscal year "FY24" / "Fiscal 2024"
    fsg = _FY_SINGLE_RE.search(t)
    if fsg:
        fy = _norm_year(fsg.group("y"))
        return Period(kind=PeriodKind.FISCAL_YEAR, raw=fsg.group(0).strip(),
                      label=f"FY{fy}", fy_end_year=fy)

    # 7) Bare date
    d = _parse_date(t)
    if d:
        if d.month == 3 and d.day == 31:      # fiscal-year-end date
            fy = _fy_from_date(d)
            return Period(kind=PeriodKind.FISCAL_YEAR, raw=t.strip()[:80],
                          label=f"FY{fy}", fy_end_year=fy, end_date=d)
        return Period(kind=PeriodKind.AS_OF, raw=t.strip()[:80],
                      label=f"as of {d.isoformat()}", end_date=d,
                      fy_end_year=_fy_from_date(d))

    # 8) Bare calendar year
    cy = re.search(r"\b(19|20)\d{2}\b", t)
    if cy:
        y = int(cy.group(0))
        return Period(kind=PeriodKind.CALENDAR_YEAR, raw=cy.group(0),
                      label=f"CY{y}", end_date=date(y, 12, 31))

    return Period()


def _looks_like_plain_range(match: re.Match, text: str) -> bool:
    """Avoid mis-reading things like '2,024-25' or page ranges as fiscal spans."""
    span = match.group(0)
    if "," in span:
        return True
    return False
