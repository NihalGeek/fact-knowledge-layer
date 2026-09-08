"""Extraction orchestrator.

Runs the deterministic strategies (narrative, KPI tiles, period-column tables),
optionally augments with the LLM layer when configured, normalizes every result
into an evidence-grounded :class:`~app.models.Fact`, and de-duplicates within a
document. No cross-document reasoning happens here.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Optional

from app.config import settings
from app.ingestion.pdf_loader import LoadedDocument, Page
from app.models import (
    Evidence, ExtractionMethod, Fact, FactStatus, FactType, Period, PeriodKind,
    EvidenceGrounding,
)
from app.normalize.attributes import canonical_family, family_kind, normalize_attribute
from app.normalize.dates import parse_period
from app.normalize.entities import detect_primary_entity, resolve_entity
from app.normalize.units import parse_value
from app.extraction.deterministic import (
    CandidateExtraction, extract_kpi, extract_narrative,
)
from app.extraction.tables import extract_row_facts

_CURRENCY_SYMBOL = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}

# Connector / filler-only phrases that are not real metrics.
_METRIC_STOPWORDS = {
    "and", "or", "others", "other", "the", "of", "for", "to", "with", "in",
    "on", "at", "as", "by", "a", "an", "is", "are", "was", "were", "it",
    "this", "that", "these", "those", "which", "such", "per", "cent", "percent",
    "total", "further", "however", "moreover", "also", "thus", "therefore",
    "from", "less", "add", "plus", "minus", "includes", "including", "net",
    "gross", "sub", "note", "remarks", "particulars",
}


def _is_junk_metric(norm_attr: str) -> bool:
    tokens = [t for t in norm_attr.split() if t]
    content = [t for t in tokens if t not in _METRIC_STOPWORDS]
    return len(content) == 0


def _fact_id(document_id: str, page: int, anchor: int, attribute: str, raw_value: str) -> str:
    key = f"{document_id}|{page}|{anchor}|{attribute}|{raw_value}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def detect_primary_period(document: LoadedDocument) -> Optional[Period]:
    """Most frequent fiscal year mentioned across the document."""
    counter: Counter[int] = Counter()
    for page in document.pages[:60]:
        for m in re.finditer(r"\bFY\s*(\d{2,4})\b", page.text):
            p = parse_period(f"FY{m.group(1)}")
            if p.fy_end_year:
                counter[p.fy_end_year] += 1
    if not counter:
        return None
    fy = counter.most_common(1)[0][0]
    return Period(kind=PeriodKind.FISCAL_YEAR, raw=f"FY{fy}", label=f"FY{fy}", fy_end_year=fy)


def _fact_type(parsed) -> FactType:
    if parsed is None:
        return FactType.UNKNOWN
    if parsed.is_percent:
        return FactType.PERCENTAGE
    if parsed.currency:
        return FactType.MONETARY
    return FactType.COUNT


def _build_fact(
    *, document: LoadedDocument, document_id: str, primary_entity: str,
    entity_raw: Optional[str], attribute: str, raw_value_display: str,
    raw_value_parse: str, period: Period, status: Optional[str],
    scope: Optional[str], definition: Optional[str], qualifiers: dict,
    evidence_text: str, page: int, char_start, char_end, bbox,
    method: ExtractionMethod, confidence: float, printed_page: Optional[str],
) -> Optional[Fact]:
    parsed = parse_value(raw_value_parse)
    if parsed is None:
        return None                                   # abstain: no parseable value
    canon_display, canon_key = resolve_entity(entity_raw or "", primary_entity)
    norm_attr = normalize_attribute(attribute)
    if not norm_attr or _is_junk_metric(norm_attr):
        return None
    family = canonical_family(attribute)
    if _is_junk_metric(family):
        return None
    # A GDP/GVA percentage is a growth rate only in a growth context; a
    # "share of GDP" percentage is a different quantity and must not collide.
    if parsed.is_percent and family in {"gdp", "gva"}:
        if re.search(r"grow|growth|expand|rose|ris(?:e|ing)|increas|moderat|"
                     r"contract|declin|accelerat|slow", evidence_text, re.IGNORECASE):
            family = f"{family} growth"
        else:
            family = f"{family} share"
        # Nominal vs real are different metrics — keep them apart (real is default).
        if re.search(r"\bnominal\b", attribute, re.IGNORECASE) or \
           re.search(r"\bnominal\b", evidence_text[:120], re.IGNORECASE):
            family = f"nominal {family}"
    # A percentage carrying a monetary metric label ("... % of revenue") is a
    # ratio, not the monetary metric itself — too ambiguous to store reliably.
    if parsed.is_percent and family_kind(family) == "monetary":
        return None
    # Sanity bound: a real GDP/GVA growth or inflation rate is never ~60%. Such a
    # value is a mislabelled share/level, so we abstain rather than store it.
    if (parsed.is_percent and (family.endswith("growth") or "inflation" in family)
            and parsed.normalized_value is not None and abs(parsed.normalized_value) > 35):
        return None
    fid = _fact_id(document_id, page, char_start or 0, family, parsed.raw_number)

    ev = Evidence(
        document_id=document_id, document_name=document.name, page=page,
        printed_page=printed_page, text=evidence_text.strip()[:600],
        char_start=char_start, char_end=char_end,
        bbox=list(bbox) if bbox else None,
    )
    return Fact(
        fact_id=fid,
        entity=(entity_raw or primary_entity),
        canonical_entity=canon_display,
        attribute=attribute.strip()[:160],
        canonical_attribute=family,
        fact_type=_fact_type(parsed),
        raw_value=raw_value_display.strip()[:80],
        value=parsed.value,
        raw_unit=(parsed.scale_word or ("%" if parsed.is_percent else None)),
        normalized_value=parsed.normalized_value,
        normalized_unit=parsed.normalized_unit,
        currency=parsed.currency,
        period=period,
        as_of_date=period.end_date if period.kind in (PeriodKind.AS_OF, PeriodKind.RANGE) else None,
        status=FactStatus(status) if status in FactStatus._value2member_map_ else FactStatus.UNKNOWN,
        scope=scope,
        definition=(definition or None),
        qualifiers={**qualifiers, "norm_attr": norm_attr},
        source_document_id=document_id,
        source_document_name=document.name,
        source_page=page,
        evidence=ev,
        grounding=EvidenceGrounding.DIRECTLY_EXTRACTED,
        extraction_confidence=round(confidence, 3),
        extraction_method=method,
    )


def _candidate_to_fact(c: CandidateExtraction, document, document_id, primary_entity,
                       printed_page) -> Optional[Fact]:
    return _build_fact(
        document=document, document_id=document_id, primary_entity=primary_entity,
        entity_raw=c.entity, attribute=c.attribute,
        raw_value_display=c.raw_value, raw_value_parse=c.raw_value,
        period=c.period, status=c.status, scope=c.scope, definition=c.definition,
        qualifiers=c.qualifiers, evidence_text=c.evidence_text, page=c.page,
        char_start=c.char_start, char_end=c.char_end, bbox=c.bbox,
        method=c.method, confidence=c.confidence, printed_page=printed_page,
    )


_SCALE_CTX_RE = re.compile(
    r"(?:amounts?|figures?|values?|₹|rs\.?|inr)?\s*(?:are\s+)?(?:in|:)\s*"
    r"(?:₹|rs\.?|inr|us\$|\$)?\s*(million|lakhs?|lac|crores?|thousand|billion)s?"
    r"|(?:₹|rs\.?|inr)\s*(million|lakhs?|crores?|billion)",
    re.IGNORECASE)


def detect_page_monetary_scale(text: str) -> Optional[str]:
    m = _SCALE_CTX_RE.search(text)
    if not m:
        return None
    return (m.group(1) or m.group(2) or "").lower().rstrip("s") or None


def _rowfact_to_facts(row, page: Page, document, document_id, primary_entity,
                      page_scale: Optional[str]) -> list[Fact]:
    facts: list[Fact] = []
    family = canonical_family(row.label)
    kind = family_kind(family)
    sym = _CURRENCY_SYMBOL.get(row.currency_hint or "", "")
    scale = row.unit_hint or (page_scale if kind == "monetary" else None)

    # Precision gate: only emit table numbers we can interpret honestly.
    #   * monetary metric with no SCALE (crore/million/...) -> abstain: a bare
    #     "₹8,142" is ambiguous in magnitude, so we never guess.
    #   * unrecognised family with no unit                  -> abstain (ambiguous)
    if kind == "monetary" and not scale:
        return facts
    if kind == "unknown" and not (row.currency_hint or row.unit_hint):
        return facts

    unit = f" {scale}" if scale else ""
    if scale and not sym and kind == "monetary":
        sym = "₹"                                     # unit context implies currency
    definition = row.footnotes[0] if row.footnotes else None
    scopes = row.scopes or [None] * len(row.values)
    for value, period, scope in zip(row.values, row.periods, scopes):
        if period is None or period.kind == PeriodKind.UNKNOWN:
            continue
        raw_parse = f"{sym}{value}{unit}".strip()
        raw_display = f"{sym}{value}{unit}".strip()
        f = _build_fact(
            document=document, document_id=document_id, primary_entity=primary_entity,
            entity_raw=None, attribute=row.label,
            raw_value_display=raw_display, raw_value_parse=raw_parse,
            period=period, status=None, scope=scope, definition=definition,
            qualifiers={"source": "table",
                        "column_confidence": f"{row.column_confidence:.2f}"},
            evidence_text=row.evidence_text, page=page.index,
            char_start=row.char_start, char_end=row.char_end, bbox=row.bbox,
            method=ExtractionMethod.DETERMINISTIC_TABLE,
            confidence=0.7 * row.column_confidence, printed_page=page.printed_page,
        )
        if f:
            facts.append(f)
    return facts


def _dedupe(facts: list[Fact]) -> list[Fact]:
    best: dict[tuple, Fact] = {}
    for f in facts:
        key = (
            f.canonical_entity.lower(), f.canonical_attribute,
            f.period.comparable_key(),
            round(f.normalized_value, 2) if f.normalized_value is not None else None,
            f.scope or "",
        )
        cur = best.get(key)
        if cur is None or f.extraction_confidence > cur.extraction_confidence:
            best[key] = f
    return list(best.values())


def extract_facts(document: LoadedDocument, document_id: str,
                  primary_entity: Optional[str] = None) -> tuple[list[Fact], int]:
    """Extract, normalize and de-duplicate all facts in a document.

    Returns ``(facts, llm_calls)``.
    """
    full_text = "\n".join(p.text for p in document.pages)
    primary_entity = primary_entity or detect_primary_entity(full_text, hint=document.name)
    primary_period = detect_primary_period(document)

    facts: list[Fact] = []
    for page in document.pages:
        if page.is_scanned:
            continue                                  # abstain on unreadable pages
        for c in extract_narrative(page, primary_period):
            f = _candidate_to_fact(c, document, document_id, primary_entity, page.printed_page)
            if f:
                facts.append(f)
        for c in extract_kpi(page, primary_period):
            f = _candidate_to_fact(c, document, document_id, primary_entity, page.printed_page)
            if f:
                facts.append(f)
        page_scale = detect_page_monetary_scale(page.text)
        for row in extract_row_facts(page):
            facts.extend(_rowfact_to_facts(row, page, document, document_id,
                                           primary_entity, page_scale))

    llm_calls = 0
    if settings.llm_active:
        try:
            from app.extraction.llm import augment_with_llm
            extra, llm_calls = augment_with_llm(document, document_id, primary_entity)
            facts.extend(extra)
        except Exception:
            llm_calls = 0                             # LLM failure never blocks

    return _dedupe(facts), llm_calls
