"""Deterministic fact extraction from narrative sentences and KPI tiles.

These two strategies complement the table parser (``tables.py``):
  * NARRATIVE — sentences like "revenue from operations on consolidated basis
    for FY24 stood at ₹ 81,415.38 million" or "real GDP is estimated to grow by
    6.4 per cent in FY25". They carry the richest period / scope / status context.
  * KPI — dashboard-style tiles ("₹8,142 Cr FY24 revenue from services") common
    in earnings presentations and report cover pages.

Everything here is pattern-based; no value, page, or entity is ever invented —
each extraction points back to the exact source text it came from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.ingestion.pdf_loader import Page
from app.models import ExtractionMethod, Period
from app.normalize.dates import parse_period
from app.extraction.patterns import VALUE_EXPR, detect_status, detect_scope


@dataclass
class CandidateExtraction:
    attribute: str
    raw_value: str
    period: Period
    evidence_text: str
    page: int
    method: ExtractionMethod
    entity: Optional[str] = None
    status: Optional[str] = None
    scope: Optional[str] = None
    definition: Optional[str] = None
    qualifiers: dict[str, str] = field(default_factory=dict)
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    bbox: Optional[tuple] = None
    confidence: float = 0.7


_LINK_VERBS = re.compile(
    r"\b(?:stood at|amounted to|totall?ed|came in at|was|were|is|are|reached|"
    r"grew by|grew|rose by|rose|increased to|increased by|decreased to|declined to|"
    r"moderated to|estimated to grow by|estimated to be|estimated at|is estimated|"
    r"is projected at|projected at|projected to|expected to grow by|expected to be|"
    r"registered a|recorded|grow by)\b",
    re.IGNORECASE)

_MONEY_OR_PCT = re.compile(
    r"(?:₹|Rs\.?|INR|US\$|\$|€|£)\s*\d|(?:\d[\d,]*(?:\.\d+)?)\s*"
    r"(?:crore|cr|lakh|million|mn|billion|bn|per\s*cent|percent|%)",
    re.IGNORECASE)

_PERIOD_AFTER = re.compile(
    r"^\s*(?:for|in|during|as of|as at|of)?\s*"
    r"(FY\s*\d{2,4}(?:[-/]\d{2,4})?|Q[1-4]\s*FY?\s*\d{2,4}|\d{4}[-/]\d{2,4}|"
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},?\s+\d{4}|\d{4})",
    re.IGNORECASE)

_LEADING_JUNK = re.compile(
    r"^(?:and|but|the|our|your|its|a|an|as|on|in|for|during|of|to|with|"
    r"however|moreover|further|additionally|meanwhile|accordingly|thus|"
    r"therefore|while|although|though|based on|according to|note that)\b\s*",
    re.IGNORECASE)


# Split only on a sentence-ending '.'/';' that is followed by whitespace and a
# new-sentence start — NOT on the '.' inside "6.4" or "Rs." Wrapped lines are
# reflowed first (a single newline is a line wrap, not a sentence break); only a
# blank line separates paragraphs.
_SENT_BOUNDARY = re.compile(
    r"(?<!Rs)(?<!No)(?<!Mr)(?<!Ms)(?<!Dr)(?<!Fig)(?<!Vol)(?<!pg)"
    r"(?<=[.;])\s+(?=[A-Z(₹\"])")


def _split_sentences(text: str) -> list[tuple[str, int]]:
    """Return (sentence, char_offset) pairs, preserving decimals like 6.4.

    Char offsets index into the *reflowed* text (returned alongside is unnecessary
    since evidence stores the sentence itself).
    """
    # Reflow: join single-newline line wraps to spaces; keep blank lines as breaks.
    flowed = re.sub(r"\n[ \t]*\n", "\x00", text)     # paragraph break sentinel
    flowed = re.sub(r"\s*\n\s*", " ", flowed)        # join wrapped lines
    flowed = flowed.replace("\x00", "\n")
    out: list[tuple[str, int]] = []
    for para in flowed.split("\n"):
        start = 0
        for m in _SENT_BOUNDARY.finditer(para):
            seg = para[start:m.start()]
            if seg.strip():
                out.append((seg.strip(), start))
            start = m.end()
        tail = para[start:]
        if tail.strip():
            out.append((tail.strip(), start))
    return out


def _detect_entity(sentence: str) -> Optional[str]:
    m = re.match(r"\s*([A-Z][a-zA-Z]+)(?:'s|’s)\b", sentence)  # "India's ..."
    if m:
        return m.group(1)
    m = re.match(r"\s*([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})\s+"
                 r"(?:reported|posted|recorded|registered|had|has|grew|rose)", sentence)
    if m:
        return m.group(1)
    return None


def _clean_metric(raw: str) -> str:
    text = raw.strip().strip(",:;").strip()
    prev = None
    while prev != text:
        prev = text
        text = _LEADING_JUNK.sub("", text).strip()
    tokens = text.split()
    if len(tokens) > 12:
        tokens = tokens[-12:]
    return " ".join(tokens).strip()


def extract_narrative(page: Page, primary_period: Optional[Period]) -> list[CandidateExtraction]:
    facts: list[CandidateExtraction] = []
    for sentence, base in _split_sentences(page.text):
        if not _MONEY_OR_PCT.search(sentence):
            continue
        if len(sentence) > 400:                       # avoid whole-paragraph blobs
            continue
        value_iter = [m for m in VALUE_EXPR.finditer(sentence)
                      if _is_strong_value(m)]
        if not value_iter:
            continue
        first_val = value_iter[0]
        verb = None
        for vm in _LINK_VERBS.finditer(sentence):
            if vm.end() <= first_val.start():
                verb = vm                              # first linking verb -> subject
                break
        metric_raw = sentence[:verb.start()] if verb else sentence[:first_val.start()]
        metric = _clean_metric(metric_raw)
        if not _valid_metric(metric):
            continue
        status = detect_status(sentence)
        scope = detect_scope(sentence)
        entity = _detect_entity(sentence)

        is_pct_sentence = bool(re.search(r"%|per\s*cent|percent", sentence, re.IGNORECASE))
        for k, vm in enumerate(value_iter):
            tail_start = vm.end()
            tail_end = value_iter[k + 1].start() if k + 1 < len(value_iter) else len(sentence)
            tail = sentence[tail_start:tail_end]
            pm = _PERIOD_AFTER.match(tail)
            # For percentages (growth rates/inflation), a comparative value like
            # "9.2% a year ago" with no explicit period would inherit the wrong
            # period — so require an explicit trailing period for percentages.
            if is_pct_sentence and vm.group("pct") and not pm:
                continue
            period = parse_period(pm.group(1)) if pm else parse_period(sentence)
            if period.kind.value == "unknown" and primary_period is not None:
                period = primary_period
                inferred = True
            else:
                inferred = False
            q = {"period_inferred": "true"} if inferred else {}
            facts.append(CandidateExtraction(
                attribute=metric,
                raw_value=vm.group(0).strip(),
                period=period,
                evidence_text=sentence,
                page=page.index,
                method=ExtractionMethod.DETERMINISTIC_NARRATIVE,
                entity=entity,
                status=status,
                scope=scope,
                qualifiers=q,
                char_start=base,
                char_end=base + len(sentence),
                confidence=0.78 if not inferred else 0.6,
            ))
    return facts


def _is_strong_value(m: re.Match) -> bool:
    return bool(m.group("cur") or m.group("scale") or m.group("pct"))


def _valid_metric(metric: str) -> bool:
    if not metric or len(metric) < 3:
        return False
    letters = sum(c.isalpha() for c in metric)
    if letters < 3:
        return False
    if len(metric.split()) > 12:
        return False
    return True


# --------------------------------------------------------------------------- #
# KPI tiles
# --------------------------------------------------------------------------- #
_KPI_NOISE = re.compile(r"\bYoY\b|\bQoQ\b|\(\d\)|[:•]|\bvs\.?\b", re.IGNORECASE)


def extract_kpi(page: Page, primary_period: Optional[Period]) -> list[CandidateExtraction]:
    """Extract KPI tiles. Tiles are frequently split across layout blocks
    ("₹8,142 Cr" in one block, "FY24 revenue from services" in the next), so we
    scan the page's blocks joined in reading order. Only *strong* values
    (currency/scale/percent) seed a tile, which keeps bare chart numbers out."""
    facts: list[CandidateExtraction] = []
    # One reading-order string per short block-run; keep the seeding block for bbox.
    segments: list[tuple[str, "object"]] = []
    buf, anchor = [], None
    for block in page.blocks:
        t = " ".join(block.text.split())
        if not t:
            continue
        if anchor is None:
            anchor = block
        buf.append(t)
        if sum(len(x) for x in buf) > 400:
            segments.append((" ".join(buf), anchor))
            buf, anchor = [], None
    if buf:
        segments.append((" ".join(buf), anchor))

    for line, block in segments:
        vals = [m for m in VALUE_EXPR.finditer(line) if _is_strong_value(m)]
        if not vals:
            continue
        for k, vm in enumerate(vals):
            seg_end = vals[k + 1].start() if k + 1 < len(vals) else len(line)
            after = line[vm.end():seg_end]
            # optional inline period right after the value
            pm = _PERIOD_AFTER.match(after)
            inline_period = None
            label_region = after
            if pm:
                inline_period = parse_period(pm.group(1))
                label_region = after[pm.end():]
            label = _KPI_NOISE.split(label_region)[0].strip(" -–,:")
            label = " ".join(label.split()[:6]).strip()
            if not _valid_metric(label):
                continue
            if inline_period and inline_period.kind.value != "unknown":
                period, inferred = inline_period, False
            elif primary_period is not None:
                period, inferred = primary_period, True
            else:
                continue
            ev = f"{vm.group(0).strip()} {pm.group(1) if pm else ''} {label}".strip()
            facts.append(CandidateExtraction(
                attribute=label,
                raw_value=vm.group(0).strip(),
                period=period,
                evidence_text=ev,
                page=page.index,
                method=ExtractionMethod.DETERMINISTIC_KPI,
                status=detect_status(ev),
                scope=detect_scope(line),
                qualifiers={"period_inferred": "true"} if inferred else {},
                char_start=block.char_start,
                char_end=block.char_end,
                bbox=block.bbox,
                confidence=0.72 if not inferred else 0.55,
            ))
    return facts
