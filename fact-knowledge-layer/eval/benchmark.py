"""Evaluation harness.

Measures three things over the (small, hand-labelled) benchmark:
  1. Extraction & grounding — are the benchmark facts extracted, and does every
     fact carry usable evidence (non-empty text + an in-range page)?
  2. Relationship classification accuracy on the labelled pairs.
  3. A precision signal — the count of high-confidence contradictions, to watch
     the false-contradiction rate as the system evolves.

Run:  python -m eval.benchmark   (seeds the store from sample_docs/ if empty)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.db import init_db
from app.services import store
from app.services.store import get_facts

HERE = Path(__file__).parent


def _ensure_seeded() -> None:
    init_db()
    if store.stats()["documents"] == 0:
        from scripts.seed_samples import main as seed
        seed()


def _find_fact(facts, entity, family, doc_sub, value_sub):
    for f in facts:
        if (f.canonical_entity.lower() == entity.lower()
                and f.canonical_attribute == family
                and doc_sub in f.source_document_name
                and value_sub in f.raw_value.replace(" ", "")):
            return f
    return None


def _relation_between(a_id, b_id):
    for r in store.relationships_for_fact(a_id):
        if {r.fact_a_id, r.fact_b_id} == {a_id, b_id}:
            return r
    return None


_RELATIONS = ["corroborates", "contradicts", "contextually_different",
              "uncertain", "related_not_comparable", "none"]


def run(write_report: bool = False) -> dict:
    _ensure_seeded()
    facts = store.all_facts()
    spec = json.loads((HERE / "benchmark_cases.json").read_text())
    st = store.stats()

    # (1) grounding
    grounded = sum(1 for f in facts if f.evidence.text.strip() and f.evidence.page >= 1)
    grounding_rate = grounded / max(len(facts), 1)

    # (2) classification on labelled pairs + confusion matrix
    rows, correct, extracted = [], 0, 0
    confusion: dict[tuple[str, str], int] = {}
    for case in spec["cases"]:
        a = _find_fact(facts, case["entity"], case["family"],
                       case["a"]["doc"], case["a"]["value"].replace(" ", ""))
        b = _find_fact(facts, case["entity"], case["family"],
                       case["b"]["doc"], case["b"]["value"].replace(" ", ""))
        if not a or not b:
            rows.append((case["name"], "MISSING FACT", case["expected"], "—", False))
            continue
        extracted += 1
        rel = _relation_between(a.fact_id, b.fact_id)
        got = rel.relation.value if rel else "none"
        ok = got == case["expected"]
        correct += ok
        confusion[(case["expected"], got)] = confusion.get((case["expected"], got), 0) + 1
        rows.append((case["name"], f"{a.raw_value} / {b.raw_value}",
                     case["expected"], got + (f" ({rel.confidence:.2f})" if rel else ""), ok))

    # (3) consistency checks
    from app.reasoning.consistency import check_facts
    checks = check_facts(facts)
    checks_ok = sum(c.ok for c in checks)

    contradictions = store.list_relationships(relation="contradicts", min_confidence=0.7)
    n = len(spec["cases"])

    lines: list[str] = []
    p = lines.append
    p("# Evaluation report — Fact Knowledge Layer\n")
    p(f"Corpus: **{st['documents']} documents**, **{len(facts)} facts**, "
      f"**{st['relationships']} relationships** (deterministic run, 0 LLM calls).\n")
    p(f"## Evidence grounding\n**{grounding_rate*100:.1f}%** of facts carry evidence "
      f"text + an in-range page ({grounded}/{len(facts)}).\n")
    p("## Relationship classification (hand-labelled cases)\n")
    p("| case | facts | expected | predicted | ✓ |")
    p("|---|---|---|---|---|")
    for name, vals, exp, got, ok in rows:
        p(f"| {name} | {vals} | {exp} | {got} | {'✅' if ok else '❌'} |")
    p(f"\n**Accuracy: {correct}/{n} = {correct/n*100:.0f}%** · "
      f"extraction {extracted}/{n} pairs found.\n")
    p("### Confusion matrix (expected → predicted)\n")
    labels = [r for r in _RELATIONS if any(k[0] == r or k[1] == r for k in confusion)]
    p("| expected \\ predicted | " + " | ".join(labels) + " |")
    p("|" + "---|" * (len(labels) + 1))
    for exp in labels:
        cells = [str(confusion.get((exp, pr), 0)) for pr in labels]
        p(f"| **{exp}** | " + " | ".join(cells) + " |")
    p(f"\n## Intra-document consistency\n**{checks_ok}/{len(checks)}** accounting "
      f"identities hold (e.g. total income = revenue + other income).\n")
    p("## Store relationship distribution\n")
    for k, v in sorted(st["relationships_by_type"].items()):
        p(f"- {k}: {v}")
    p(f"\nHigh-confidence contradictions: **{len(contradictions)}** "
      "(tracked for false-positive drift).\n")
    p("## Method note\n")
    p("- Deterministic-only run (LLM disabled). The LLM layer is an *additive* "
      "recall aid; correctness of the labelled cases does not depend on it.\n"
      "- The benchmark is small and hand-labelled — a **regression signal**, not a "
      "population estimate. Extend `eval/benchmark_cases.json` to grow it.")

    report = "\n".join(lines)
    print(report)
    if write_report:
        (HERE / "REPORT.md").write_text(report + "\n")
        print(f"\n[written] {HERE / 'REPORT.md'}")

    return {"grounding_rate": grounding_rate, "extracted": extracted,
            "correct": correct, "total": n, "checks_ok": checks_ok,
            "checks_total": len(checks), "high_conf_contradictions": len(contradictions)}


if __name__ == "__main__":
    import sys
    run(write_report="--report" in sys.argv)
