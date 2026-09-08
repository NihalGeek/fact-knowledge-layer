# Design Notes — Fact Knowledge Layer

This document captures how the problem was understood, the plan, the skeptical
review of that plan, and the final architecture that was actually built. It is
the "thinking" companion to the `README.md` (which is the operational guide).

---

## 1. The problem, in plain terms

Important facts are scattered across documents, written in different ways,
sometimes supported by another source and sometimes contradicted by one. An
analyst reading a company's prospectus, its annual report and its earnings deck
— or three institutions' reports on the same economy — has to hold hundreds of
pages in their head to answer: *do these two numbers agree, and if not, why?*

The system turns PDFs into **facts** — `(entity, attribute, value, unit, period,
scope)` tuples — each tied to the exact source text, then reconciles facts about
the same thing across documents into one of: **corroborates**, **contradicts**,
**contextually different** (an *apparent* contradiction that context explains),
or **uncertain**.

The three worked cases the dataset actually contains:

| Case | Fact A | Fact B | Verdict |
|---|---|---|---|
| Corroboration | Q4 deck: **₹8,142 Cr** FY24 revenue (p.6) | Annual report: **₹81,415.38 million** FY24 revenue (p.36) | **Corroborates** — equal after unit normalization |
| Contradiction | Economic Survey: FY25 real GDP **6.4%** (p.4) | RBI: FY25 real GDP **6.5%** (p.8) | **Contradicts** — same entity/period, different estimate (vintage-flagged) |
| Apparent contradiction | Prospectus: team size **86,184** as of Dec-2021 (p.44) | Q4 deck: team size **63,713** as of Mar-2024 (p.8) | **Contextually different** — different dates *and* definitions |

Why naive comparison fails, in one line each: `8142 ≠ 81415` yet they are the
same fact (crore vs million); `86184 ≠ 63713` but they are not comparable at all
(three years apart, different footnote definition). The whole product is the
logic between those two traps.

---

## 2. Initial plan (what a first pass would reach for)

> PDF → LLM extracts every fact → embeddings + vector DB for similarity →
> LLM judges every pair for contradiction → store in a graph database → chat UI.

Phased: ingest → LLM extraction → embeddings index → LLM pairwise reasoning →
Neo4j knowledge graph → API → chat-style UI.

## 3. Skeptical review of that plan

1. **LLM-for-everything is the wrong default for finance.** The one unforgivable
   failure here is a hallucinated number or a hallucinated page cite. An LLM that
   "extracts every fact" is exactly the component most likely to do that.
2. **Arithmetic and dates must be code.** "8,142 Cr ≈ 81,415.38 Mn" has to be
   *provable*, not vibes. Same for FY24 = 2023-24. An LLM adds risk and cost to
   something deterministic.
3. **A vector DB is unjustified.** The matching keys — entity, normalized
   attribute, period, scope — are *structured*. Blocking on those is a SQL
   `GROUP BY`, exact and explainable. Embeddings would add a dependency and
   opacity for a problem that is not fuzzy at the level that matters.
4. **A graph DB is architecture theatre here.** "Knowledge layer" ≠ Neo4j.
   Documents, facts, evidence and relationships are four tables with clear keys.
5. **The centre is not a chatbot.** The assignment is explicit: the interesting
   part is how facts are discovered, grounded, compared and explained. The UI
   should be an analyst's fact/evidence/reconciliation surface.
6. **Pairwise-LLM comparison is O(n²) LLM calls** — slow, expensive, and mostly
   spent on pairs that are trivially non-comparable.

## 4. Final architecture (what was built)

Deterministic-first, LLM-optional. Every value and page number is a literal span
from the document, so the core **cannot fabricate evidence**.

```
PDF ─▶ ingest (PyMuPDF, page-aware, hash+dedup)
     ─▶ extract  ├─ narrative sentences   ┐
                 ├─ KPI tiles             ├─ deterministic, evidence-grounded
                 └─ period-column tables  ┘   (+ optional LLM assist, validated)
     ─▶ normalize (units·dates·entities·attributes — pure code, provable)
     ─▶ persist facts (SQLite; JSON blob ⇒ schema evolves without migrations)
     ─▶ candidate blocking (entity, family) — cheap, incremental
     ─▶ classify (ordered deterministic rules → corroborate/contradict/contextual/uncertain)
     ─▶ persist relationships ─▶ API ─▶ analyst UI
```

**Where the LLM is / isn't used.** Deterministic code owns arithmetic, unit
conversion, date parsing, validation, dedup, thresholds and classification. The
LLM (optional, off by default, activated only with a key) is confined to
semantic extraction of unusually-phrased facts — constrained to strict JSON,
validated with Pydantic, and required to quote an evidence span that literally
occurs on the page, or it is discarded. The system is fully functional with the
LLM off, which is how it is demoed and evaluated.

**Reasoning is ordered and auditable** — never `if a.value != b.value`.
Structural context (type → entity → period → scope → definition) is checked
*before* the value comparison, so genuine disagreements are separated from
apparent ones, and every verdict names the dimension that drove it. When
extraction confidence is low, the classifier abstains (`uncertain`) rather than
assert a contradiction.

## 5. Priorities (in order)

Correctness → evidence grounding → reasoning quality → generalization →
reliability → explainability → UX → performance → extensions → polish. No
component was kept merely because it appeared in the first plan; the vector DB,
graph DB, multi-agent framework and chat UI were all cut.

## 6. Key trade-offs

- **Deterministic extraction** gives high precision and zero hallucination but
  lower recall than an LLM on oddly-phrased facts — accepted, because a smaller
  set of trustworthy facts beats a large set of dubious ones (and the LLM layer
  is there when recall matters).
- **Block-based table parsing** (not PyMuPDF `find_tables`, which mangled these
  layouts) — chosen after empirically comparing both on the actual documents.
- **SQLite + JSON blobs** — simple, reliable, and the JSON column is exactly what
  makes the fact schema evolve without migrations.
- **Precision over recall in reasoning** — confidence gating and structural
  checks keep the false-contradiction rate low, at the cost of some missed
  low-confidence links.
