"""Shared regexes / token classifiers used across extractors."""
from __future__ import annotations

import re

# A numeric cell as it appears in financial tables:
#   81,415.38   (348.01)   86,184   18.15   6.5   740   220+   12.7%
NUMERIC_TOKEN = re.compile(
    r"^[₹$€£]?\s*\(?-?\d[\d,]*(?:\.\d+)?\)?\s*[%+]?$"
)

# A value expression with optional currency + scale word (for narrative/KPI):
#   ₹81,415.38 million   ₹8,142 Cr   6.4 per cent   1.4 Mn   740 Mn   ₹758Mn
VALUE_EXPR = re.compile(
    r"(?P<cur>₹|Rs\.?|INR|US\$|\$|€|£)?\s*"
    r"(?P<num>\(?-?\d[\d,]*(?:\.\d+)?\)?)\s*"
    r"(?P<scale>thousand|lakhs?|lac|crores?|cr|billion|bn|million|mn|mln|trillion|tn|K|Mn|Cr|Bn)?"
    r"\s*(?P<pct>%|per\s*cent|percent)?",
    re.IGNORECASE,
)

# Status cues (estimate / forecast / actual / revised / budget)
STATUS_CUES = {
    "estimate": ["estimated", "estimate", "advance estimate", "provisional",
                 "first advance", "second advance", "projected to", "is expected to"],
    "forecast": ["projected", "forecast", "outlook", "expected to grow",
                 "is projected", "baseline scenario"],
    "revised": ["revised estimate", "revised", "re:", " re "],
    "budget": ["budget estimate", "budgeted", " be:", " be "],
    "actual": ["actual", "stood at", "recorded", "reported", "grew by",
               "moderated to", "rose by", "increased to", "as against"],
}

# Scope cues (financial reporting basis)
SCOPE_CUES = {
    "consolidated": ["consolidated"],
    "standalone": ["standalone", "stand-alone", "stand alone"],
}


def is_numeric_token(tok: str) -> bool:
    tok = tok.strip()
    if not tok:
        return False
    if NUMERIC_TOKEN.match(tok):
        # require at least one digit and not a bare footnote marker like "(1)"
        digits = re.sub(r"\D", "", tok)
        return len(digits) >= 1 and not re.fullmatch(r"\(\d\)", tok)
    return False


def detect_status(text: str) -> str | None:
    low = f" {text.lower()} "
    for status, cues in STATUS_CUES.items():
        for cue in cues:
            if cue in low:
                return status
    return None


def detect_scope(text: str) -> str | None:
    low = text.lower()
    for scope, cues in SCOPE_CUES.items():
        for cue in cues:
            if cue in low:
                return scope
    return None


def footnote_markers(label: str) -> list[str]:
    """'Team size(1)' -> ['1']; 'Adjusted EBITDA(2)(3)' -> ['2','3']."""
    return re.findall(r"\((\d)\)", label)
