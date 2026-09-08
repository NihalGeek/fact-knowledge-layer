"""General entity resolution.

The goal is to recognise that ``Delhivery Limited``, ``Delhivery`` and, inside a
Delhivery document, ``the Company`` all refer to one entity — and to do so
*without* a hard-coded list of names. The document's primary entity is detected
by frequency, and generic references ("the Company", "the Bank", "we") resolve
to it.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

_LEGAL_SUFFIXES = {
    "limited", "ltd", "ltd.", "inc", "inc.", "plc", "corporation", "corp",
    "corp.", "company", "co", "co.", "llp", "llc", "group", "holdings",
}
_GENERIC_REFS = {
    "the company", "the group", "the bank", "the corporation", "the firm",
    "the issuer", "we", "our company", "the organisation", "the organization",
    "us", "it",
}
# tokens that are never an entity subject on their own
_STOP_PROPER = {
    "The", "This", "In", "As", "For", "During", "Note", "Table", "Chart",
    "Source", "Figure", "March", "December", "June", "September", "January",
    "April", "May", "July", "August", "October", "November", "February",
    "Fiscal", "Total", "Revenue", "India's", "Our", "Its", "Annual", "Report",
    "Quarter", "Financial", "Year", "Company", "Limited", "Reserve", "Board",
}


def normalize_entity(name: str) -> str:
    """Lower-cased matching key with legal suffixes and punctuation removed."""
    if not name:
        return ""
    text = re.sub(r"[^\w\s]", " ", name.lower())
    tokens = [t for t in text.split() if t and t not in {"the"}]
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens).strip()


def display_entity(name: str) -> str:
    """Human-readable canonical form (drops trailing legal suffix)."""
    tokens = name.strip().split()
    while tokens and tokens[-1].lower().strip(".") in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens) if tokens else name.strip()


def is_generic_reference(name: str) -> bool:
    return name.strip().lower() in _GENERIC_REFS


_LEGAL_SUFFIX_RE = re.compile(
    r"\b([A-Z][A-Za-z&.\-]+(?:\s+[A-Z][A-Za-z&.\-]+){0,3})\s+"
    r"(?:Limited|Ltd|Inc|PLC|Corporation|LLP|LLC)\b")


def detect_primary_entity(text: str, hint: Optional[str] = None) -> str:
    """Best-guess primary entity of a document.

    Two complementary signals, no hard-coded names:
      1. the name that most often precedes a legal suffix ("<X> Limited") — the
         reporting company in a corporate filing;
      2. otherwise the most frequent salient proper noun (a country in an
         economic report).
    """
    sample = text[:400_000]

    # Bare salient-token frequency: the ground truth of what the document is about.
    counter: Counter[str] = Counter()
    for m in re.finditer(r"\b([A-Z][a-z]{2,})(?:['’]s)?\b", sample):
        tok = m.group(1)
        if tok in _STOP_PROPER or tok.lower() in _LEGAL_SUFFIXES or tok in _COMMON_NON_ENTITY:
            continue
        counter[tok] += 1
    lower_freq = {k.lower(): (k, v) for k, v in counter.items()}

    # Legal-suffix company head tokens ("Delhivery" from "Delhivery Limited").
    company_heads: Counter[str] = Counter()
    for m in _LEGAL_SUFFIX_RE.finditer(sample):
        name = m.group(1).strip()
        toks = [t for t in name.split()
                if t not in _STOP_PROPER and t not in _COMMON_NON_ENTITY and t.lower() != "of"]
        if toks:
            company_heads[toks[-1]] += 1

    # Signal 0: a subject token from the file name, VALIDATED against content
    # (the token must actually recur in the document, so a misleading name can't
    # dominate). File names commonly name their subject.
    for htok in _hint_tokens(hint):
        if htok in {h.lower() for h in company_heads}:
            return display_entity(htok.capitalize())
        if htok in lower_freq and lower_freq[htok][1] >= 5:
            return lower_freq[htok][0]

    # The reporting entity is the legal-suffix company that is ALSO a frequent bare
    # token (the document's subject), not a one-off citation ("CEIC Data Ltd").
    best_company, best_freq = None, 0
    for head in company_heads:
        freq = counter.get(head, 0)
        if freq > best_freq:
            best_company, best_freq = head, freq
    if best_company and best_freq >= 15:
        return display_entity(best_company)

    if counter:
        return counter.most_common(1)[0][0]
    if company_heads:
        return display_entity(company_heads.most_common(1)[0][0])
    return hint or "Unknown"


_HINT_STOP = {
    "annual", "report", "excerpt", "prospectus", "presentation", "earnings",
    "survey", "economic", "consultation", "article", "iv", "fy", "fy24", "fy25",
    "q1", "q2", "q3", "q4", "final", "draft", "the", "and", "for", "of", "pdf",
    "reserve", "bank", "fund", "international", "monetary", "limited", "ltd",
}


def _hint_tokens(hint: Optional[str]) -> list[str]:
    if not hint:
        return []
    toks = re.split(r"[^A-Za-z]+", hint.lower())
    return [t for t in toks if len(t) >= 3 and t not in _HINT_STOP]


# High-frequency capitalized words that are not the document's subject entity.
_COMMON_NON_ENTITY = {
    "Source", "Chart", "Table", "Figure", "Note", "Notes", "Data", "Growth",
    "Inflation", "Food", "Prices", "Sector", "Economic", "Survey", "Bank",
    "Government", "Report", "Annual", "Fund", "Staff", "Board", "Committee",
    "Gross", "Domestic", "Product", "Fiscal", "Policy", "Market", "Global",
    "Real", "Total", "Capital", "Current", "Account", "Services", "Trade",
    "Exports", "Imports", "Fixed", "Rate", "Rates", "Index", "Consumer",
    "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct",
    "Nov", "Dec", "January", "February", "April", "June", "July", "August",
    "September", "October", "November", "December",
    "Private", "Public", "Reserve", "Note", "Mudran", "Corporation", "Company",
    "Institute", "Association", "Authority", "Ministry", "Department",
}


def resolve_entity(raw_entity: str, primary_entity: Optional[str]) -> tuple[str, str]:
    """Return ``(canonical_display, canonical_key)`` for a fact's entity."""
    if not raw_entity or is_generic_reference(raw_entity):
        base = primary_entity or raw_entity or "Unknown"
    else:
        base = raw_entity
    return display_entity(base), normalize_entity(base)
