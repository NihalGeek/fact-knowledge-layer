"""Page-aware PDF ingestion built on PyMuPDF.

Responsibilities:
  * open a PDF safely (bad/encrypted files fail gracefully),
  * hash bytes for de-duplication / incremental ingestion,
  * expose each page's text, layout blocks, words and detected tables together
    with their source locations (page + bbox + char offsets),
  * flag pages that look scanned (little/no extractable text) so the caller can
    decide to OCR or abstain rather than silently emit nothing.

Large documents are handled page-by-page; the whole PDF is never concatenated
into a single blob for downstream LLM prompting.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterator, Optional

import pymupdf


@dataclass
class Block:
    text: str
    bbox: tuple[float, float, float, float]
    char_start: int
    char_end: int


@dataclass
class TableCell:
    text: str
    row: int
    col: int
    bbox: Optional[tuple[float, float, float, float]] = None


@dataclass
class Table:
    rows: list[list[str]]
    bbox: Optional[tuple[float, float, float, float]] = None
    header: Optional[list[str]] = None


@dataclass
class Page:
    index: int                                  # 1-based PDF page number
    text: str
    printed_page: Optional[str]
    blocks: list[Block] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    is_scanned: bool = False


@dataclass
class LoadedDocument:
    name: str
    sha256: str
    byte_size: int
    page_count: int
    pages: list[Page]
    scanned_page_count: int = 0


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _detect_printed_page(text: str) -> Optional[str]:
    """Light heuristic: a lone number on the first or last line is often the
    printed page number (which can differ from the PDF index in excerpts)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for candidate in ([lines[-1]] if lines else []) + ([lines[0]] if lines else []):
        if re.fullmatch(r"\d{1,4}", candidate):
            return candidate
        m = re.fullmatch(r"(?:page\s+)?(\d{1,4})(?:\s+of\s+\d+)?", candidate.lower())
        if m:
            return m.group(1)
    return None


def _extract_tables(page: "pymupdf.Page") -> list[Table]:
    tables: list[Table] = []
    try:
        found = page.find_tables()
    except Exception:
        return tables
    for t in getattr(found, "tables", []):
        try:
            rows = t.extract()
        except Exception:
            continue
        clean = [[(c or "").strip() for c in row] for row in rows if any(row)]
        if not clean or len(clean) < 2:
            continue
        header = clean[0] if clean else None
        tables.append(Table(rows=clean, bbox=tuple(t.bbox), header=header))
    return tables


def load_bytes(data: bytes, name: str) -> LoadedDocument:
    """Load a PDF from raw bytes. Raises ValueError on unreadable input."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # malformed / not a PDF
        raise ValueError(f"Could not open PDF '{name}': {exc}") from exc

    if doc.needs_pass:
        doc.close()
        raise ValueError(f"PDF '{name}' is password-protected/encrypted.")

    pages: list[Page] = []
    scanned = 0
    for i, p in enumerate(doc):
        text = p.get_text() or ""
        blocks: list[Block] = []
        cursor = 0
        for b in p.get_text("blocks"):
            btext = (b[4] or "").strip()
            if not btext:
                continue
            idx = text.find(btext, cursor)
            if idx < 0:
                idx = text.find(btext)
            start = idx if idx >= 0 else 0
            end = start + len(btext)
            cursor = end
            blocks.append(Block(text=btext, bbox=(b[0], b[1], b[2], b[3]),
                                char_start=start, char_end=end))
        is_scanned = len(text.strip()) < 20
        if is_scanned:
            scanned += 1
        pages.append(Page(
            index=i + 1, text=text, printed_page=_detect_printed_page(text),
            blocks=blocks, tables=_extract_tables(p), is_scanned=is_scanned,
        ))
    page_count = doc.page_count
    doc.close()
    return LoadedDocument(
        name=name, sha256=sha256_bytes(data), byte_size=len(data),
        page_count=page_count, pages=pages, scanned_page_count=scanned,
    )


def load_path(path: str) -> LoadedDocument:
    import os
    with open(path, "rb") as fh:
        data = fh.read()
    return load_bytes(data, os.path.basename(path))
