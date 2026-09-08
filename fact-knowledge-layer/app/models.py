"""Core domain models (Pydantic v2).

The central object is the ``Fact``. The schema is deliberately *generic*: an
attribute is a free-form string and open-ended ``qualifiers`` carry scope /
definition context, so a brand-new kind of fact ("customer retention rate")
can be stored and compared without any code or DB-column change.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #
class FactType(str, enum.Enum):
    MONETARY = "monetary"
    PERCENTAGE = "percentage"
    COUNT = "count"
    RATIO = "ratio"
    QUANTITY = "quantity"          # tonnes, shipments, km, etc.
    TEXT = "text"                  # non-numeric attribute value
    UNKNOWN = "unknown"


class PeriodKind(str, enum.Enum):
    FISCAL_YEAR = "fiscal_year"    # FY2024 (India FY ends 31 Mar)
    QUARTER = "quarter"            # Q4 FY2024
    CALENDAR_YEAR = "calendar_year"
    AS_OF = "as_of"                # a point in time ("as of 31 Dec 2021")
    RANGE = "range"                # "nine months ended 31 Dec 2021"
    UNKNOWN = "unknown"


class FactStatus(str, enum.Enum):
    ACTUAL = "actual"
    ESTIMATE = "estimate"          # advance / provisional estimate
    FORECAST = "forecast"          # projection / outlook
    REVISED = "revised"            # revised estimate
    BUDGET = "budget"              # budget estimate
    UNKNOWN = "unknown"


class ExtractionMethod(str, enum.Enum):
    DETERMINISTIC_NARRATIVE = "deterministic_narrative"
    DETERMINISTIC_TABLE = "deterministic_table"
    DETERMINISTIC_KPI = "deterministic_kpi"
    LLM_ASSISTED = "llm_assisted"


class EvidenceGrounding(str, enum.Enum):
    """How a stored value relates to the source text — keeps derived values
    honest and distinct from what the document literally says."""
    DIRECTLY_EXTRACTED = "directly_extracted"   # value is a literal span
    INFERRED = "inferred"                        # code-derived (e.g. normalized)
    RECONCILED = "reconciled"                    # produced by cross-doc reasoning


class RelationType(str, enum.Enum):
    CORROBORATES = "corroborates"
    CONTRADICTS = "contradicts"
    CONTEXTUALLY_DIFFERENT = "contextually_different"
    RELATED_NOT_COMPARABLE = "related_not_comparable"
    UNCERTAIN = "uncertain"


# --------------------------------------------------------------------------- #
# Period
# --------------------------------------------------------------------------- #
class Period(BaseModel):
    kind: PeriodKind = PeriodKind.UNKNOWN
    raw: str = ""                              # exactly as written in the doc
    label: str = ""                            # canonical label, e.g. "FY2024"
    fy_end_year: Optional[int] = None          # fiscal year identified by end year
    quarter: Optional[int] = None              # 1..4 when kind == QUARTER
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    def comparable_key(self) -> str:
        """Two periods are 'the same period' iff this key matches."""
        if self.kind == PeriodKind.QUARTER and self.fy_end_year and self.quarter:
            return f"Q{self.quarter}-FY{self.fy_end_year}"
        if self.kind == PeriodKind.FISCAL_YEAR and self.fy_end_year:
            return f"FY{self.fy_end_year}"
        if self.kind == PeriodKind.CALENDAR_YEAR and self.end_date:
            return f"CY{self.end_date.year}"
        if self.kind in (PeriodKind.AS_OF, PeriodKind.RANGE) and self.end_date:
            return f"{self.kind.value}:{self.end_date.isoformat()}"
        return self.label or self.raw or "unknown"


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #
class Evidence(BaseModel):
    document_id: str
    document_name: str
    page: int                                  # PDF page index (1-based)
    printed_page: Optional[str] = None         # page number printed on the page
    section: Optional[str] = None
    chunk_id: Optional[str] = None
    text: str                                  # the exact supporting text
    char_start: Optional[int] = None           # offset within the page text
    char_end: Optional[int] = None
    bbox: Optional[list[float]] = None         # [x0,y0,x1,y1] when available


# --------------------------------------------------------------------------- #
# Fact
# --------------------------------------------------------------------------- #
class Fact(BaseModel):
    fact_id: str

    # --- identity ---
    entity: str                                # entity as written
    canonical_entity: str                      # resolved / normalized entity
    attribute: str                             # e.g. "revenue from services"
    canonical_attribute: str                   # normalized attribute key

    # --- value ---
    fact_type: FactType = FactType.UNKNOWN
    raw_value: str = ""                         # value exactly as written
    value: Optional[float] = None              # parsed numeric value
    raw_unit: Optional[str] = None             # unit token as written (Cr, Mn, %)
    normalized_value: Optional[float] = None   # value in a base unit
    normalized_unit: Optional[str] = None      # base unit (INR, percent, count...)
    currency: Optional[str] = None             # ISO-ish code (INR, USD) when monetary
    text_value: Optional[str] = None           # for non-numeric facts

    # --- time & context ---
    period: Period = Field(default_factory=Period)
    as_of_date: Optional[date] = None
    status: FactStatus = FactStatus.UNKNOWN
    scope: Optional[str] = None                # consolidated / standalone / ...
    definition: Optional[str] = None           # metric definition text if present
    qualifiers: dict[str, str] = Field(default_factory=dict)  # open-ended context

    # --- provenance ---
    source_document_id: str = ""
    source_document_name: str = ""
    source_page: int = 0
    source_section: Optional[str] = None
    evidence: Evidence
    grounding: EvidenceGrounding = EvidenceGrounding.DIRECTLY_EXTRACTED

    # --- confidence / method ---
    extraction_confidence: float = 0.0
    reasoning_confidence: float = 0.0
    extraction_method: ExtractionMethod = ExtractionMethod.DETERMINISTIC_NARRATIVE

    created_at: datetime = Field(default_factory=datetime.utcnow)

    def match_key(self) -> str:
        """Structured key used to *group* candidate facts cheaply."""
        return f"{self.canonical_entity}|{self.canonical_attribute}"


# --------------------------------------------------------------------------- #
# Relationship
# --------------------------------------------------------------------------- #
class Relationship(BaseModel):
    relationship_id: str
    fact_a_id: str
    fact_b_id: str
    relation: RelationType
    confidence: float
    explanation: str                            # human-readable reasoning
    reasoning_factors: dict[str, str] = Field(default_factory=dict)
    context_differences: list[str] = Field(default_factory=list)
    method: str = "deterministic"               # or "llm_assisted"
    created_at: datetime = Field(default_factory=datetime.utcnow)


# --------------------------------------------------------------------------- #
# Document
# --------------------------------------------------------------------------- #
class DocumentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DUPLICATE = "duplicate"


class Document(BaseModel):
    document_id: str
    name: str
    sha256: str
    page_count: int = 0
    byte_size: int = 0
    status: DocumentStatus = DocumentStatus.PENDING
    error: Optional[str] = None
    primary_entity: Optional[str] = None
    fact_count: int = 0
    relationship_count: int = 0
    processing_ms: int = 0
    llm_calls: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
