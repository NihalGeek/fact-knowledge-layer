"""Deterministic relationship reasoning.

Given two candidate facts, decide how they relate and *explain why*. The logic is
explicitly ordered so the classification is auditable — it never reduces to
``if a.value != b.value: contradiction``. Structural context (period, scope,
definition, unit) is checked before any value comparison, so genuine
disagreements are separated from apparent ones.

An optional LLM pass can refine the natural-language explanation, but never the
classification itself and never the evidence.
"""
from __future__ import annotations

import hashlib

from rapidfuzz import fuzz

from app.config import settings
from app.models import Fact, RelationType, Relationship
from app.normalize.units import parse_value, values_equivalent


def _rel_id(a: Fact, b: Fact) -> str:
    lo, hi = sorted((a.fact_id, b.fact_id))
    return hashlib.sha1(f"{lo}|{hi}".encode()).hexdigest()[:16]


def _type_compatible(a: Fact, b: Fact) -> bool:
    if a.normalized_unit and b.normalized_unit:
        # percent vs money vs count are not directly comparable
        pa, pb = a.normalized_unit, b.normalized_unit
        cur = {"INR", "USD", "EUR", "GBP"}
        ka = "money" if pa in cur else pa
        kb = "money" if pb in cur else pb
        return ka == kb
    return a.fact_type == b.fact_type


def _same_period(a: Fact, b: Fact) -> bool:
    return (a.period.comparable_key() == b.period.comparable_key()
            and a.period.kind.value != "unknown")


def _period_known(a: Fact, b: Fact) -> bool:
    return a.period.kind.value != "unknown" and b.period.kind.value != "unknown"


def _scope_conflict(a: Fact, b: Fact) -> bool:
    """A scope difference exists if the two scopes disagree, or if exactly one
    fact is explicitly on a standalone/consolidated basis (so a labelled
    standalone figure is not called a contradiction of an unlabelled one)."""
    if a.scope and b.scope:
        return a.scope != b.scope
    known = {"standalone", "consolidated"}
    return bool((a.scope in known) ^ (b.scope in known))


def _scope_desc(a: Fact, b: Fact) -> str:
    return f"{a.scope or 'unspecified'} vs {b.scope or 'unspecified'}"


def _definition_conflict(a: Fact, b: Fact) -> bool:
    da, db = (a.definition or "").strip(), (b.definition or "").strip()
    if da and db:
        return fuzz.token_set_ratio(da.lower(), db.lower()) < 75
    return False


def _status_differs(a: Fact, b: Fact) -> bool:
    ok = {"actual", "estimate", "forecast", "revised", "budget"}
    return (a.status.value in ok and b.status.value in ok
            and a.status.value != b.status.value)


def _fmt(f: Fact) -> str:
    return f"{f.raw_value} ({f.source_document_name}, p.{f.source_page})"


def classify(a: Fact, b: Fact, rel_tolerance: float | None = None) -> Relationship | None:
    """Classify the relationship between two candidate facts."""
    tol = settings.rel_tolerance if rel_tolerance is None else rel_tolerance

    if a.canonical_entity.strip().lower() != b.canonical_entity.strip().lower():
        return None
    if not _type_compatible(a, b):
        return _make(a, b, RelationType.RELATED_NOT_COMPARABLE, 0.4,
                     f"Both describe {a.canonical_attribute} for {a.canonical_entity}, "
                     f"but the quantities are not directly comparable "
                     f"({a.normalized_unit} vs {b.normalized_unit}).",
                     {"type_a": a.normalized_unit or "", "type_b": b.normalized_unit or ""},
                     [])

    pa, pb = parse_value(a.raw_value), parse_value(b.raw_value)
    if pa is None or pb is None:
        return _make(a, b, RelationType.UNCERTAIN, 0.3,
                     "One of the values could not be parsed for comparison.", {}, [])
    equal, value_note = values_equivalent(pa, pb, tol)

    factors: dict[str, str] = {
        "same_period": str(_same_period(a, b)),
        "value_relation": value_note,
        "period_a": a.period.label or a.period.comparable_key(),
        "period_b": b.period.label or b.period.comparable_key(),
    }
    ctx_diffs: list[str] = []
    if a.raw_unit and b.raw_unit and a.raw_unit != b.raw_unit:
        ctx_diffs.append(f"different units ({a.raw_unit} vs {b.raw_unit})")
    if _scope_conflict(a, b):
        ctx_diffs.append(f"different scope ({_scope_desc(a, b)})")
    if _definition_conflict(a, b):
        ctx_diffs.append("different metric definitions")
    if _status_differs(a, b):
        ctx_diffs.append(f"different reporting basis ({a.status.value} vs {b.status.value})")

    base_conf = min(a.extraction_confidence, b.extraction_confidence)

    # --- period not established: cannot safely compare across time ---
    if not _period_known(a, b):
        if equal:
            return _make(a, b, RelationType.CORROBORATES, round(0.55 * (0.6 + base_conf), 3),
                         f"{a.canonical_entity} — {a.canonical_attribute}: {_fmt(a)} and "
                         f"{_fmt(b)} agree ({value_note}), but at least one period is "
                         f"unstated, so this corroboration is tentative.",
                         factors, ctx_diffs)
        return _make(a, b, RelationType.UNCERTAIN, 0.35,
                     f"Values differ ({value_note}) but a period is unstated for at least "
                     f"one fact, so we abstain from calling it a contradiction.",
                     factors, ctx_diffs)

    # --- different period: an apparent difference explained by time ---
    if not _same_period(a, b):
        expl = (f"{a.canonical_entity} — {a.canonical_attribute}: {_fmt(a)} covers "
                f"{factors['period_a']} while {_fmt(b)} covers {factors['period_b']}. "
                f"They describe different periods and are not contradictory")
        if ctx_diffs:
            expl += "; also " + ", ".join(ctx_diffs)
        expl += "."
        return _make(a, b, RelationType.CONTEXTUALLY_DIFFERENT,
                     round(0.7 * (0.7 + base_conf), 3), expl, factors, ctx_diffs)

    # --- same period ---
    if equal:
        expl = (f"{a.canonical_entity} — {a.canonical_attribute} for {factors['period_a']}: "
                f"{_fmt(a)} and {_fmt(b)} report the same figure ({value_note})")
        unit_diffs = [d for d in ctx_diffs if d.startswith("different units")]
        if unit_diffs:
            expl += f", reconciled across {unit_diffs[0]}"
        expl += "."
        conf = round(min(0.99, 0.75 + 0.2 * base_conf), 3)
        return _make(a, b, RelationType.CORROBORATES, conf, expl, factors, ctx_diffs)

    # same period, values differ -> is there a structural explanation?
    structural = [d for d in ctx_diffs
                  if d.startswith(("different scope", "different metric"))]
    if structural:
        expl = (f"{a.canonical_entity} — {a.canonical_attribute} for {factors['period_a']}: "
                f"{_fmt(a)} and {_fmt(b)} differ ({value_note}), explained by "
                + ", ".join(structural) + ".")
        return _make(a, b, RelationType.CONTEXTUALLY_DIFFERENT,
                     round(0.7 * (0.7 + base_conf), 3), expl, factors, ctx_diffs)

    # genuine / likely contradiction — but only assert it between reasonably
    # confident extractions; otherwise abstain (a wrong contradiction is costly).
    if base_conf < 0.65:
        return _make(a, b, RelationType.UNCERTAIN, round(0.3 + 0.2 * base_conf, 3),
                     f"{a.canonical_entity} — {a.canonical_attribute} for "
                     f"{factors['period_a']}: values differ ({value_note}), but the "
                     f"extraction confidence is too low to assert a contradiction.",
                     factors, ctx_diffs)
    vintage = _status_differs(a, b)
    expl = (f"{a.canonical_entity} — {a.canonical_attribute} for {factors['period_a']}: "
            f"{_fmt(a)} and {_fmt(b)} give different values for the same entity, period, "
            f"scope and definition ({value_note}).")
    conf = round(min(0.9, 0.6 + 0.3 * base_conf), 3)
    if vintage:
        expl += (f" Note: reported on a different basis "
                 f"({a.status.value} vs {b.status.value}), which may reflect estimate "
                 f"vintage/revision rather than a hard conflict.")
        conf = round(conf * 0.85, 3)
    return _make(a, b, RelationType.CONTRADICTS, conf, expl, factors, ctx_diffs)


def _make(a, b, rel, conf, expl, factors, ctx) -> Relationship:
    conf = round(max(0.0, min(0.99, conf)), 3)        # confidence is always a [0,1) score
    return Relationship(
        relationship_id=_rel_id(a, b),
        fact_a_id=a.fact_id, fact_b_id=b.fact_id,
        relation=rel, confidence=conf, explanation=expl,
        reasoning_factors=factors, context_differences=ctx,
        method="deterministic",
    )
