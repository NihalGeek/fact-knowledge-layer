"""Render a PDF page with a fact's evidence highlighted.

This is what makes grounding tangible: for any fact, we can show the exact page
it came from with the supporting text boxed. We highlight by searching the page
for the evidence text (robust to layout), falling back to the stored block bbox.
"""
from __future__ import annotations

from typing import Optional

import pymupdf

from app.models import Fact
from app.services.pipeline import source_pdf_path

_ZOOM = 2.0
_HL = (1.0, 0.86, 0.20)     # amber highlight


def _snippets(fact: Fact) -> list[str]:
    out = []
    rv = (fact.raw_value or "").strip()
    if rv:
        out.append(rv)
    text = (fact.evidence.text or "").strip()
    if text:
        out.append(text[:60])
        out.append(text)
    return out


def render_evidence_png(fact: Fact) -> Optional[bytes]:
    path = source_pdf_path(fact.source_document_id)
    if path is None:
        return None
    doc = pymupdf.open(str(path))
    try:
        page_no = max(1, min(fact.source_page, doc.page_count)) - 1
        page = doc[page_no]

        rects = []
        for snip in _snippets(fact):
            try:
                rects = page.search_for(snip, quads=False)
            except Exception:
                rects = []
            if rects:
                break
        if not rects and fact.evidence.bbox:
            rects = [pymupdf.Rect(*fact.evidence.bbox)]

        for r in rects:
            annot = page.add_highlight_annot(r)
            annot.set_colors(stroke=_HL)
            annot.update()

        pix = page.get_pixmap(matrix=pymupdf.Matrix(_ZOOM, _ZOOM))
        return pix.tobytes("png")
    finally:
        doc.close()
