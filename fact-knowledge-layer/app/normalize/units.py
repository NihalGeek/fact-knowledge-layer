"""Deterministic unit / currency / scale normalization.

Arithmetic is done in code (never by an LLM) so that equivalence between, e.g.,
``₹8,142 Cr`` and ``₹81,415.38 million`` is *provable* and reproducible. The raw
representation is always preserved on the Fact; this module only computes the
normalized companion values used for comparison.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Multiplicative scale words -> multiplier. Indian + international systems.
SCALES: dict[str, float] = {
    "thousand": 1e3,
    "k": 1e3,
    "lakh": 1e5,
    "lac": 1e5,
    "lakhs": 1e5,
    "million": 1e6,
    "mn": 1e6,
    "mln": 1e6,
    "m": 1e6,
    "crore": 1e7,
    "cr": 1e7,
    "crores": 1e7,
    "billion": 1e9,
    "bn": 1e9,
    "b": 1e9,
    "trillion": 1e12,
    "tn": 1e12,
}

# Currency symbols / codes -> ISO-ish code.
CURRENCY: dict[str, str] = {
    "₹": "INR",
    "rs": "INR",
    "rs.": "INR",
    "inr": "INR",
    "rupees": "INR",
    "$": "USD",
    "us$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
}

_CUR_ALT = sorted((re.escape(k) for k in CURRENCY), key=len, reverse=True)
_SCALE_ALT = sorted((re.escape(k) for k in SCALES), key=len, reverse=True)

# A number like 81,415.38 or 1,429 or 6.4 or (348.01) [negative in parentheses]
_NUM = r"\(?-?\d{1,3}(?:,\d{2,3})*(?:\.\d+)?\)?|\(?-?\d+(?:\.\d+)?\)?"


@dataclass
class ParsedValue:
    value: float                       # scaled numeric value in the written unit's terms
    normalized_value: float            # value in the base unit
    normalized_unit: str               # "INR", "percent", "count", ...
    currency: Optional[str]
    scale_word: Optional[str]
    is_percent: bool
    raw_number: str


def _to_number(token: str) -> Optional[float]:
    token = token.strip()
    neg = token.startswith("(") and token.endswith(")")
    token = token.strip("()").replace(",", "").replace("−", "-")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        return None
    val = float(token)
    return -val if neg else val


def detect_currency(text: str) -> Optional[str]:
    low = text.lower()
    for token, code in sorted(CURRENCY.items(), key=lambda kv: -len(kv[0])):
        if token in low:
            return code
    return None


def parse_value(raw: str) -> Optional[ParsedValue]:
    """Parse a monetary / percentage / plain numeric string.

    Returns None when there is no parseable number (caller should abstain).
    """
    if raw is None:
        return None
    text = raw.strip()
    low = text.lower()

    currency = detect_currency(text)
    is_percent = "%" in text or re.search(r"\bper\s*cent\b|\bpercent\b", low) is not None

    m = re.search(_NUM, text)
    if not m:
        return None
    number = _to_number(m.group(0))
    if number is None:
        return None

    # Scale word appearing after the number (million / crore / lakh / K ...).
    scale_word = None
    multiplier = 1.0
    tail = low[m.end():]
    sm = re.search(r"[a-z]+", tail)
    if sm:
        word = sm.group(0)
        if word in SCALES:
            scale_word = word
            multiplier = SCALES[word]

    scaled = number * multiplier

    if is_percent:
        return ParsedValue(
            value=number,
            normalized_value=number,          # keep percentage points as-is
            normalized_unit="percent",
            currency=None,
            scale_word=scale_word,
            is_percent=True,
            raw_number=m.group(0),
        )

    if currency:
        return ParsedValue(
            value=scaled,
            normalized_value=scaled,
            normalized_unit=currency,
            currency=currency,
            scale_word=scale_word,
            is_percent=False,
            raw_number=m.group(0),
        )

    # Plain number (count / quantity / ratio). Base unit is the scaled count.
    return ParsedValue(
        value=scaled,
        normalized_value=scaled,
        normalized_unit="count",
        currency=None,
        scale_word=scale_word,
        is_percent=False,
        raw_number=m.group(0),
    )


def rounding_halfwidth(parsed: ParsedValue) -> float:
    """Half the granularity implied by how a value was written.

    ``8,142 Cr`` is rounded to the nearest crore, so its true value lies within
    ±0.5 crore. This lets us prove that ``8,142 Cr`` and ``81,415.38 Mn`` agree
    *because of rounding*, not merely 'close enough'.
    """
    raw = parsed.raw_number.strip("()").replace(",", "")
    if "." in raw:
        decimals = len(raw.split(".", 1)[1])
        step_in_written_units = 10 ** (-decimals)
    else:
        step_in_written_units = 1.0
    multiplier = SCALES.get(parsed.scale_word, 1.0) if parsed.scale_word else 1.0
    return 0.5 * step_in_written_units * multiplier


def values_equivalent(
    a: ParsedValue, b: ParsedValue, rel_tolerance: float = 0.005
) -> tuple[bool, str]:
    """Decide whether two parsed values are equivalent, and explain why.

    Uses the *looser* of (rounding-implied bound, relative tolerance) so that
    coarse figures reconcile with precise ones without falsely merging genuinely
    different numbers.
    """
    if a.normalized_unit != b.normalized_unit:
        return False, f"different units ({a.normalized_unit} vs {b.normalized_unit})"
    diff = abs(a.normalized_value - b.normalized_value)
    scale = max(abs(a.normalized_value), abs(b.normalized_value), 1e-9)
    rel = diff / scale
    # Corroboration-by-rounding requires the *more precise* value to round to the
    # *coarser* one — i.e. Δ within the LARGER half-width. Using the sum instead
    # would let merely-touching intervals (6.4% vs 6.5%) read as equal, which in
    # this domain is a genuine disagreement, not a rounding artefact.
    rounding_bound = max(rounding_halfwidth(a), rounding_halfwidth(b))
    if diff <= rounding_bound:
        return True, f"within rounding tolerance (Δ={diff:.4g} ≤ {rounding_bound:.4g})"
    if rel <= rel_tolerance:
        return True, f"within relative tolerance ({rel*100:.3f}% ≤ {rel_tolerance*100:.3f}%)"
    return False, f"values differ by {rel*100:.2f}% (Δ={diff:.4g})"
