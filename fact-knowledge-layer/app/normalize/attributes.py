"""Attribute canonicalization.

Groups differently-worded metrics into a comparable *family* while preserving
the full, specific wording as the fact's ``definition``. The lexicon below is
general financial/economic vocabulary (extensible), NOT a list of starter-dataset
facts — an unrecognised metric simply falls back to its own normalized phrase, so
brand-new fact types remain comparable across documents by exact / lexical match.
"""
from __future__ import annotations

import re

# Ordered longest-first so multi-word heads win over their substrings.
METRIC_HEADS: list[tuple[str, str]] = [
    (r"gross\s+domestic\s+product|gdp", "gdp"),
    (r"gross\s+value\s+added|gva", "gva"),
    (r"revenues?\s+from\s+operations|revenues?\s+from\s+services|"
     r"revenues?\s+from\s+contracts|revenues?\s+from\s+customers|"
     r"total\s+income\s+from\s+operations|turnover|revenues?", "revenue"),
    (r"ebitda\s+margin", "ebitda margin"),          # a percentage — before EBITDA
    (r"adjusted\s+ebitda", "adjusted ebitda"),
    (r"ebitda", "ebitda"),
    (r"other\s+income", "other income"),            # before total income
    (r"total\s+income", "total income"),
    (r"net\s+worth", "net worth"),
    (r"profit\s+after\s+tax|pat|net\s+profit", "profit after tax"),
    (r"loss\s+for\s+the\s+period|loss\s+for\s+the\s+year|net\s+loss", "net loss"),
    (r"team\s+size", "team size"),
    (r"head\s*count", "headcount"),
    (r"work\s*force", "workforce"),
    (r"number\s+of\s+employees|no\.?\s+of\s+employees|employees", "employees"),
    (r"partner\s+agents", "partner agents"),
    (r"active\s+customers", "active customers"),
    # Inflation sub-metrics are kept DISTINCT (food vs core vs headline vs CPI-IW
    # are different indices) — only the explicit headline-CPI synonyms are merged.
    (r"cpi[\s-]*iw|cpi\s+for\s+industrial\s+workers|industrial\s+workers",
     "cpi-iw inflation"),
    (r"consumer\s+price\s+inflation|headline\s+cpi|cpi\s+inflation|"
     r"retail\s+inflation|cpi", "cpi inflation"),
    (r"wholesale\s+price|wpi", "wpi inflation"),
    (r"food\s+inflation", "food inflation"),
    (r"core\s+inflation", "core inflation"),
    (r"fiscal\s+deficit", "fiscal deficit"),
    (r"current\s+account\s+deficit|cad", "current account deficit"),
    (r"shipments?", "shipments"),
    (r"tonnage|tonnes?|tons?", "tonnage"),
    (r"ebitda\s+margin", "ebitda margin"),
]

_SCOPE_WORDS = {"consolidated", "standalone", "stand-alone", "combined"}
_FILLER = {"the", "of", "for", "a", "an", "our", "your", "in", "on", "at",
           "total", "restated", "real", "nominal", "gross", "net", "overall"}
_PERIOD_WORDS = re.compile(
    r"\bfy\s*\d{2,4}\b|\bq[1-4]\b|\b(?:19|20)\d{2}\b|\bfiscal\b|\bquarter\b|\byear\b",
    re.IGNORECASE)


def normalize_attribute(attribute: str) -> str:
    """Cleaned, lower-cased attribute phrase (definition-preserving companion)."""
    text = attribute.lower()
    text = re.sub(r"\([^)]*\)", " ", text)           # drop parentheticals
    text = re.sub(r"[\*#]", " ", text)
    text = _PERIOD_WORDS.sub(" ", text)
    text = re.sub(r"[^\w\s%\-]", " ", text)
    tokens = [t for t in text.split() if t and t not in _SCOPE_WORDS]
    return " ".join(tokens).strip()


# Only these exact phrases are the TOTAL revenue line; sub-components
# ("revenue from Express Parcel services") must keep their own family so they are
# never compared against the top line.
_REVENUE_TOTAL = {
    "revenue", "revenues", "turnover", "total revenue", "total revenues",
    "revenue from operations", "revenues from operations",
    "revenue from services", "revenues from services",
    "revenue from contracts", "revenues from contracts",
    "revenue from contracts with customers", "revenues from contracts with customers",
    "revenue from customers", "revenues from customers", "total revenue from customers",
    "total income from operations",
}


def canonical_family(attribute: str) -> str:
    """Coarse comparability key: metric head if recognised, else the phrase."""
    norm = normalize_attribute(attribute)
    # Revenue: collapse only genuine total-revenue phrasings.
    if re.search(r"\brevenues?\b|\bturnover\b", norm):
        if norm in _REVENUE_TOTAL:
            return "revenue"
        return norm                                   # sub-component keeps identity
    nominal = re.search(r"\bnominal\b", attribute, re.IGNORECASE) is not None
    for pattern, head in METRIC_HEADS:
        if re.search(rf"\b(?:{pattern})\b", norm):
            # A growth/percentage metric of GDP/GVA is its own family; nominal and
            # real growth are DIFFERENT metrics and must not be merged.
            if head in {"gdp", "gva"} and re.search(r"grow|growth", attribute.lower()):
                return f"nominal {head} growth" if nominal else f"{head} growth"
            return head
    # Fall back to the normalized phrase minus filler words.
    tokens = [t for t in norm.split() if t not in _FILLER]
    return " ".join(tokens) if tokens else norm


def looks_like_growth(attribute: str) -> bool:
    return bool(re.search(r"grow|growth|expand|rose|increase", attribute.lower()))


# Family -> broad kind, used to gate how table numbers may be interpreted.
_MONETARY_FAMILIES = {
    "revenue", "ebitda", "adjusted ebitda", "total income", "other income",
    "net worth", "profit after tax", "net loss", "fiscal deficit",
    "current account deficit",
}
_COUNT_FAMILIES = {
    "team size", "headcount", "workforce", "employees", "partner agents",
    "active customers", "shipments", "tonnage",
}
_PERCENT_FAMILIES = {
    "gdp growth", "gva growth", "cpi inflation", "wpi inflation",
    "food inflation", "core inflation", "ebitda margin",
}


def family_kind(family: str) -> str:
    if family in _MONETARY_FAMILIES:
        return "monetary"
    if family in _COUNT_FAMILIES:
        return "count"
    if family in _PERCENT_FAMILIES:
        return "percent"
    return "unknown"
