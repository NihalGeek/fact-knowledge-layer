"""Optional LLM extraction layer.

Design principles (see README):
  * The LLM is used ONLY for semantic extraction of facts the deterministic
    strategies miss (unusual phrasings), never for arithmetic, dates, or
    evidence. It runs per page/chunk — never the whole document in one prompt.
  * Output is constrained to a strict JSON schema and validated with Pydantic.
    Invalid or unparseable output is discarded (the system abstains) — a
    malformed model response can never corrupt the store.
  * Every value the model returns MUST quote an ``evidence`` span that actually
    occurs in the page text; anything else is dropped, so the model cannot
    fabricate evidence or page numbers.

The whole layer is inert unless ``FACTLAYER_LLM_ENABLED=true`` and a key is set;
the system is fully functional without it.
"""
from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.ingestion.pdf_loader import LoadedDocument
from app.models import (
    Evidence, EvidenceGrounding, ExtractionMethod, Fact, FactStatus, FactType,
)
from app.normalize.attributes import canonical_family, normalize_attribute
from app.normalize.dates import parse_period
from app.normalize.entities import resolve_entity
from app.normalize.units import parse_value

_SYSTEM = (
    "You extract structured financial/economic facts from one page of a document. "
    "Return ONLY JSON: {\"facts\":[{\"entity\":str,\"attribute\":str,\"value\":str,"
    "\"period\":str,\"scope\":str|null,\"status\":str|null,\"evidence\":str}]}. "
    "The 'value' and 'evidence' MUST be copied verbatim from the page text. "
    "Do NOT invent numbers or facts. If unsure, return fewer facts."
)


class _LLMFact(BaseModel):
    entity: Optional[str] = None
    attribute: str
    value: str
    period: Optional[str] = ""
    scope: Optional[str] = None
    status: Optional[str] = None
    evidence: str


class _LLMResponse(BaseModel):
    facts: list[_LLMFact] = []


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _extract_page(client, page_text: str) -> list[_LLMFact]:
    msg = client.messages.create(
        model=settings.llm_model,
        max_tokens=1500,
        system=_SYSTEM,
        messages=[{"role": "user", "content": page_text[:8000]}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        return []
    try:
        return _LLMResponse.model_validate_json(raw[start:end + 1]).facts
    except (ValidationError, json.JSONDecodeError):
        return []                                     # abstain on malformed output


def augment_with_llm(document: LoadedDocument, document_id: str,
                     primary_entity: str) -> tuple[list[Fact], int]:
    """Return (facts, llm_call_count). Every fact is re-validated deterministically
    and must be grounded in the page's actual text, or it is dropped."""
    client = _client()
    facts: list[Fact] = []
    calls = 0
    for page in document.pages:
        if page.is_scanned or len(page.text.strip()) < 40:
            continue
        calls += 1
        for lf in _extract_page(client, page.text):
            if lf.evidence not in page.text:          # hallucination guard
                continue
            parsed = parse_value(lf.value)
            if parsed is None:
                continue
            norm_attr = normalize_attribute(lf.attribute)
            if not norm_attr:
                continue
            disp, _ = resolve_entity(lf.entity or "", primary_entity)
            fid = f"llm-{document_id}-{abs(hash((page.index, lf.attribute, lf.value))) % 10**10}"
            ev = Evidence(document_id=document_id, document_name=document.name,
                          page=page.index, text=lf.evidence[:600],
                          printed_page=page.printed_page)
            facts.append(Fact(
                fact_id=fid, entity=lf.entity or primary_entity, canonical_entity=disp,
                attribute=lf.attribute[:160], canonical_attribute=canonical_family(lf.attribute),
                fact_type=(FactType.PERCENTAGE if parsed.is_percent else
                           FactType.MONETARY if parsed.currency else FactType.COUNT),
                raw_value=lf.value[:80], value=parsed.value,
                normalized_value=parsed.normalized_value, normalized_unit=parsed.normalized_unit,
                currency=parsed.currency, period=parse_period(lf.period or ""),
                status=FactStatus(lf.status) if lf.status in FactStatus._value2member_map_
                else FactStatus.UNKNOWN,
                scope=lf.scope, qualifiers={"norm_attr": norm_attr},
                source_document_id=document_id, source_document_name=document.name,
                source_page=page.index, evidence=ev,
                grounding=EvidenceGrounding.DIRECTLY_EXTRACTED,
                extraction_confidence=0.65, extraction_method=ExtractionMethod.LLM_ASSISTED,
            ))
    return facts, calls
