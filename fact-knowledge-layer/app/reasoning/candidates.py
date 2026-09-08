"""Candidate generation.

Comparing every fact against every other fact is O(n²) and mostly wasteful. We
first *block* facts by ``(canonical_entity, canonical_family)`` — a cheap
structured key — and only propose deeper comparison for facts that share a block
and come from different documents. A lexical back-stop (rapidfuzz) links close
attribute wordings that normalization did not already merge.

This is what keeps incremental ingestion cheap: a new document's facts are only
matched against the buckets they land in, never the whole store.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from rapidfuzz import fuzz

from app.models import Fact


def _entity_key(f: Fact) -> str:
    return f.canonical_entity.strip().lower()


def _comparable(f: Fact) -> bool:
    # "X% of GDP" share families are too ambiguous (share of what?) to reconcile
    # across documents — keep them as browseable facts, but don't compare them.
    return not f.canonical_attribute.endswith("share")


def block_facts(facts: Iterable[Fact]) -> dict[tuple[str, str], list[Fact]]:
    buckets: dict[tuple[str, str], list[Fact]] = defaultdict(list)
    for f in facts:
        if _comparable(f):
            buckets[(_entity_key(f), f.canonical_attribute)].append(f)
    return buckets


def _norm_attr(f: Fact) -> str:
    return f.qualifiers.get("norm_attr", f.canonical_attribute)


def generate_pairs(
    new_facts: list[Fact], existing_facts: list[Fact], *,
    cross_document_only: bool = True, use_lexical: bool = True,
    lexical_threshold: int = 93,
) -> list[tuple[Fact, Fact]]:
    """Propose candidate pairs of (new_fact, other_fact).

    Blocks by ``(entity, family)``. The optional lexical back-stop is deliberately
    conservative (high threshold, same-kind only): it links close attribute
    wordings that normalization missed without merging different metrics.
    """
    pairs: list[tuple[Fact, Fact]] = []
    seen: set[tuple[str, str]] = set()

    existing_buckets = block_facts(existing_facts)
    by_entity: dict[str, list[Fact]] = defaultdict(list)
    for f in existing_facts:
        by_entity[_entity_key(f)].append(f)

    for nf in new_facts:
        if not _comparable(nf):
            continue
        candidates: list[Fact] = []
        # 1) exact structured block
        candidates.extend(existing_buckets.get((_entity_key(nf), nf.canonical_attribute), []))
        # 2) conservative lexical back-stop within the same entity + same type
        if use_lexical:
            na = _norm_attr(nf)
            for ef in by_entity.get(_entity_key(nf), []):
                if ef.canonical_attribute == nf.canonical_attribute:
                    continue
                if ef.fact_type != nf.fact_type:
                    continue
                if fuzz.token_set_ratio(na, _norm_attr(ef)) >= lexical_threshold:
                    candidates.append(ef)

        for ef in candidates:
            if nf.fact_id == ef.fact_id:
                continue
            if cross_document_only and nf.source_document_id == ef.source_document_id:
                continue
            key = tuple(sorted((nf.fact_id, ef.fact_id)))
            if key in seen:
                continue
            seen.add(key)
            pairs.append((nf, ef))
    return pairs
