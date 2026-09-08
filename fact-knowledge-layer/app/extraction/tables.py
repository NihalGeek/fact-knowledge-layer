"""Period-column table extraction.

Financial/economic tables are the hardest layout to read reliably. PyMuPDF's
``find_tables`` mis-aligns row labels and headers on these documents, so instead
we work from PyMuPDF text *blocks*, which keep a row's label and its numeric
cells together in reading order (e.g. ``'Team size(4)\\n60,373\\n57,307\\n...'``).

Strategy:
  * assemble the header region and parse it into an ordered list of column
    Periods (fiscal years, quarters, dates, "nine months ended ...");
  * flatten each data block into tokens, group each label with the run of
    numeric cells that follows it, and align cells to columns *right-anchored*
    (the newest value maps to the newest period) — the near-universal ordering
    of financial tables.

This is conservative: rows whose column count can't be reconciled with the
header are emitted with lower confidence and UNKNOWN periods rather than guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.ingestion.pdf_loader import Page
from app.models import Period, PeriodKind
from app.normalize.dates import parse_period
from app.extraction.patterns import is_numeric_token, footnote_markers

_MONTHS = ("January|February|March|April|May|June|July|August|September|"
           "October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec")
_DATE_RE = re.compile(rf"(?:{_MONTHS})\.?\s+\d{{1,2}},?\s+\d{{4}}", re.IGNORECASE)
_QUARTER_RE = re.compile(r"Q[1-4]\s*[:\-]?\s*(?:FY)?\s*\d{2,4}", re.IGNORECASE)
_FY_RE = re.compile(r"\bFY\s*\d{2,4}(?:\s*[-/]\s*\d{2,4})?\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


@dataclass
class RowFact:
    label: str
    values: list[str]
    periods: list[Optional[Period]]
    scopes: list[Optional[str]] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    unit_hint: Optional[str] = None
    currency_hint: Optional[str] = None
    evidence_text: str = ""
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    bbox: Optional[tuple] = None
    column_confidence: float = 1.0


def parse_header_periods(header_text: str) -> list[Period]:
    """Turn a table's header region into an ordered list of column Periods.

    Each successive pattern reads from a copy in which earlier (more specific)
    matches have been blanked out, so ``Q4 FY24`` is counted once as a quarter,
    not also as a fiscal year. Bare years become fiscal years when the header
    carries a fiscal-year context (India FY ends 31 Mar).
    """
    text = " ".join(header_text.split())
    atoms: list[tuple[int, Period]] = []
    working = list(text)

    def _blank(a: int, b: int) -> None:
        for i in range(a, min(b, len(working))):
            working[i] = " "

    fiscal_ctx = bool(re.search(r"fiscal|year\s+ended\s+march\s+31", text, re.I))
    range_ctx = bool(re.search(r"months?\s+ended|period\s+ended", text, re.I))

    # Quarters, then explicit dates, then FY tokens — each on the blanked copy.
    for rx in (_QUARTER_RE, _DATE_RE, _FY_RE):
        for m in rx.finditer("".join(working)):
            span = m.group(0)
            ctx = text[max(0, m.start() - 30): m.start()].lower()
            year_end = "year ended" in ctx or "fy ended" in ctx
            if rx is _DATE_RE and ("ended" in ctx or range_ctx) and not year_end:
                span = "nine months ended " + span   # -> RANGE
            atoms.append((m.start(), parse_period(span)))
            _blank(m.start(), m.end())

    # Remaining bare years -> fiscal or calendar depending on context.
    for m in _YEAR_RE.finditer("".join(working)):
        year = m.group(0)
        span = f"year ended March 31, {year}" if fiscal_ctx else year
        atoms.append((m.start(), parse_period(span)))

    atoms.sort(key=lambda t: t[0])
    periods = [p for _, p in atoms if p.kind != PeriodKind.UNKNOWN]
    return periods


def scopes_for_columns(header_text: str, k: int) -> list[Optional[str]]:
    """Assign a reporting scope to each of ``k`` columns.

    Financial statements commonly place ``Standalone`` and ``Consolidated`` as
    spanning labels over equal halves of the period columns. When both appear we
    split the columns; when one appears we apply it to all; otherwise None.
    """
    low = header_text.lower()
    has_s = "standalone" in low or "stand-alone" in low
    has_c = "consolidated" in low
    if has_s and has_c and k % 2 == 0:
        half = k // 2
        # order follows reading order of the two words in the header
        s_first = low.find("standalone") < low.find("consolidated")
        first, second = ("standalone", "consolidated") if s_first else ("consolidated", "standalone")
        return [first] * half + [second] * half
    if has_c and not has_s:
        return ["consolidated"] * k
    if has_s and not has_c:
        return ["standalone"] * k
    return [None] * k


def _split_tokens(block_text: str) -> list[str]:
    toks: list[str] = []
    for line in block_text.split("\n"):
        line = line.strip()
        if line:
            toks.append(line)
    return toks


_HEADER_CTX_RE = re.compile(
    r"fiscal\s+year|year\s+ended|fy\s+ended|months?\s+ended|period\s+ended"
    r"|as\s+of\s+and\s+for|for\s+the\s+year|as\s+at|as\s+of\s+the\s+end"
    r"|standalone|consolidated", re.IGNORECASE)


def _period_atom_count(block_text: str) -> int:
    toks = _split_tokens(block_text)
    return sum(
        1 for t in toks
        if _QUARTER_RE.search(t) or _FY_RE.search(t) or _YEAR_RE.fullmatch(t.strip())
        or _DATE_RE.search(t)
    )


def _looks_like_header_block(block_text: str) -> bool:
    toks = _split_tokens(block_text)
    if not toks:
        return False
    period_like = _period_atom_count(block_text)
    if period_like >= 2 and period_like >= len(toks) // 2:
        return True
    # A pure context block ("...Fiscal Year Ended March 31,") with no data rows.
    numeric = sum(1 for t in toks if is_numeric_token(t))
    return bool(_HEADER_CTX_RE.search(block_text)) and numeric == 0


def _unit_from_label(label: str) -> tuple[Optional[str], Optional[str]]:
    low = label.lower()
    currency = "INR" if ("₹" in label or "rs" in low or "inr" in low) else None
    if "$" in label or "us$" in low or "usd" in low:
        currency = "USD"
    scale = None
    for word in ("crore", "cr", "lakh", "million", "mn", "billion", "bn", "thousand"):
        if re.search(rf"\bin\b.*\b{word}s?\b", low) or re.search(rf"\({word}s?\)", low):
            scale = word
            break
    return scale, currency


def extract_row_facts(page: Page) -> list[RowFact]:
    """Extract period-column table rows from a page's text blocks."""
    rows: list[RowFact] = []
    active_periods: list[Period] = []
    header_buf = ""
    saw_data_since_header = False
    footnote_defs: dict[str, str] = {}

    # First pass: collect footnote definitions on the page.
    for m in re.finditer(r"\((\d)\)\s*([A-Z][^\n]{6,200})", page.text):
        footnote_defs.setdefault(m.group(1), m.group(2).strip())

    for block in page.blocks:
        btext = block.text
        if _looks_like_header_block(btext):
            if saw_data_since_header:          # new table -> reset the header
                header_buf = ""
                saw_data_since_header = False
            header_buf = (header_buf + " " + btext).strip()
            periods = parse_header_periods(header_buf)
            if periods:
                active_periods = periods
            continue

        toks = _split_tokens(btext)
        # Stitch a dangling header date across the block boundary: if the header
        # ends with a bare "Month day," and this data block starts with a year,
        # fold that year in so the last column period is completed.
        lead_years = []
        k = 0
        while k < len(toks) and _YEAR_RE.fullmatch(toks[k].strip()):
            lead_years.append(toks[k]); k += 1
        if lead_years and header_buf:
            header_buf = (header_buf + " " + " ".join(lead_years)).strip()
            periods = parse_header_periods(header_buf)
            if periods:
                active_periods = periods

        i = 0
        n = len(toks)
        while i < n:
            if is_numeric_token(toks[i]):
                i += 1
                continue
            # Accumulate a (possibly multi-token) label until numbers begin.
            label_parts = [toks[i]]
            j = i + 1
            while j < n and not is_numeric_token(toks[j]):
                label_parts.append(toks[j])
                j += 1
            # Collect the run of numeric cells.
            values: list[str] = []
            while j < n and is_numeric_token(toks[j]):
                values.append(toks[j])
                j += 1
            label = " ".join(label_parts).strip()
            if values and len(values) >= 2 and _is_reasonable_label(label):
                rows.append(_build_row(label, values, active_periods, footnote_defs,
                                       block, header_buf))
                saw_data_since_header = True
            i = j if j > i else i + 1

    return rows


def _is_reasonable_label(label: str) -> bool:
    if len(label) < 2:
        return False
    letters = sum(c.isalpha() for c in label)
    return letters >= 2 and len(label) <= 120


def _build_row(label, values, active_periods, footnote_defs, block, header_buf="") -> RowFact:
    scale, currency = _unit_from_label(label)
    fns = footnote_markers(label)
    clean_label = re.sub(r"\(\d\)", "", label).strip().rstrip("*").strip()

    K = len(values)
    M = len(active_periods)
    periods: list[Optional[Period]] = [None] * K
    conf = 1.0
    if M >= K:                                   # right-anchored alignment
        for idx in range(K):
            periods[idx] = active_periods[M - K + idx]
    elif M > 0:
        for idx in range(K):
            src = idx - (K - M)
            periods[idx] = active_periods[src] if src >= 0 else None
        conf = 0.6
    else:
        conf = 0.4

    scopes = scopes_for_columns(header_buf, K)

    defs = [footnote_defs[f] for f in fns if f in footnote_defs]
    return RowFact(
        label=clean_label,
        values=values,
        periods=periods,
        scopes=scopes,
        footnotes=defs,
        unit_hint=scale,
        currency_hint=currency,
        evidence_text=(clean_label + " " + " ".join(values)).strip(),
        char_start=block.char_start,
        char_end=block.char_end,
        bbox=block.bbox,
        column_confidence=conf,
    )
