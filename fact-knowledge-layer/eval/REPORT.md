# Evaluation report — Fact Knowledge Layer

Corpus: **6 documents**, **2302 facts**, **1133 relationships** (deterministic run, 0 LLM calls).

## Evidence grounding
**100.0%** of facts carry evidence text + an in-range page (2302/2302).

## Relationship classification (hand-labelled cases)

| case | facts | expected | predicted | ✓ |
|---|---|---|---|---|
| Delhivery FY24 revenue — ₹8,142 Cr vs ₹81,415.38 million | ₹8,142 Cr / ₹81,415.38 million | corroborates | corroborates (0.89) | ✅ |
| India FY25 real GDP growth — Economic Survey 6.4% vs RBI 6.5% | 6.4 per cent / 6.5 per cent | contradicts | contradicts (0.71) | ✅ |
| India FY25 real GDP growth — IMF 6.5% vs RBI 6.5% (agree) | 6.5 percent / 6.5 per cent | corroborates | corroborates (0.89) | ✅ |
| Delhivery team size — 86,184 (Dec-2021) vs 63,713 (Q4 FY24) | 86,184 / 63,713 | contextually_different | contextually_different (0.98) | ✅ |
| India core inflation — IMF 3.5% vs RBI 3.5% | 3.5 percent / 3.5 per cent | corroborates | corroborates (0.91) | ✅ |

**Accuracy: 5/5 = 100%** · extraction 5/5 pairs found.

### Confusion matrix (expected → predicted)

| expected \ predicted | corroborates | contradicts | contextually_different |
|---|---|---|---|
| **corroborates** | 3 | 0 | 0 |
| **contradicts** | 0 | 1 | 0 |
| **contextually_different** | 0 | 0 | 1 |

## Intra-document consistency
**4/4** accounting identities hold (e.g. total income = revenue + other income).

## Store relationship distribution

- contextually_different: 865
- contradicts: 8
- corroborates: 10
- related_not_comparable: 129
- uncertain: 121

High-confidence contradictions: **8** (tracked for false-positive drift).

## Method note

- Deterministic-only run (LLM disabled). The LLM layer is an *additive* recall aid; correctness of the labelled cases does not depend on it.
- The benchmark is small and hand-labelled — a **regression signal**, not a population estimate. Extend `eval/benchmark_cases.json` to grow it.
