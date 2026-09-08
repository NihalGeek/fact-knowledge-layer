"""Intra-document numeric consistency checks (an "auditor" over the facts).

Beyond cross-document reconciliation, we validate accounting identities *within*
a document — e.g. `total income == revenue from operations + other income` — for
each (entity, period, scope). A failing identity is a strong signal of an
extraction error or a genuine inconsistency in the source, and it demonstrates
that the system understands the numbers, not just the strings.

Identities are configured as `(result_family, [component_families])`; they are
general accounting relationships, not hard-coded document facts.
"""
from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel

from app.config import settings
from app.models import Fact

# result family == sum(component families). General financial identities.
IDENTITIES: list[tuple[str, list[str]]] = [
    ("total income", ["revenue", "other income"]),
]


class ConsistencyCheck(BaseModel):
    document_id: str
    document_name: str
    entity: str
    period: str
    scope: str | None
    identity: str                      # human-readable, e.g. "total income = revenue + other income"
    result_family: str
    result_value: float
    computed_value: float
    delta: float
    ok: bool
    unit: str | None
    components: list[dict]             # [{family, raw_value, page}]
    result_page: int


def _best_by_family(facts: list[Fact]) -> dict[str, Fact]:
    """Highest-confidence fact per family in a group."""
    best: dict[str, Fact] = {}
    for f in facts:
        cur = best.get(f.canonical_attribute)
        if cur is None or f.extraction_confidence > cur.extraction_confidence:
            best[f.canonical_attribute] = f
    return best


def check_facts(facts: list[Fact]) -> list[ConsistencyCheck]:
    groups: dict[tuple, list[Fact]] = defaultdict(list)
    for f in facts:
        if f.normalized_value is None or f.fact_type.value != "monetary":
            continue
        groups[(f.source_document_id, f.canonical_entity,
                f.period.comparable_key(), f.scope or "", f.normalized_unit)].append(f)

    checks: list[ConsistencyCheck] = []
    for (doc_id, entity, period, scope, unit), fs in groups.items():
        by_fam = _best_by_family(fs)
        for result_fam, comp_fams in IDENTITIES:
            if result_fam not in by_fam:
                continue
            if not all(c in by_fam for c in comp_fams):
                continue
            result = by_fam[result_fam]
            comps = [by_fam[c] for c in comp_fams]
            # An accounting identity is only meaningful when the figures are
            # presented together — require them co-located (same statement page).
            pages = [result.source_page] + [c.source_page for c in comps]
            if max(pages) - min(pages) > 1:
                continue
            computed = sum(c.normalized_value for c in comps)
            delta = abs(result.normalized_value - computed)
            denom = max(abs(result.normalized_value), 1e-9)
            ok = (delta / denom) <= max(settings.rel_tolerance, 0.01)
            checks.append(ConsistencyCheck(
                document_id=doc_id, document_name=result.source_document_name,
                entity=entity, period=result.period.label or period, scope=scope or None,
                identity=f"{result_fam} = " + " + ".join(comp_fams),
                result_family=result_fam, result_value=result.normalized_value,
                computed_value=computed, delta=delta, ok=ok, unit=unit,
                components=[{"family": c.canonical_attribute, "raw_value": c.raw_value,
                            "page": c.source_page} for c in comps],
                result_page=result.source_page,
            ))
    return checks
