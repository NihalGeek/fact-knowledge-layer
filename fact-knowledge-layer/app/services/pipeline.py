"""End-to-end ingestion pipeline.

    PDF bytes
      -> hash + de-duplicate (skip already-ingested documents)
      -> page-aware load
      -> entity detection
      -> fact extraction (+ optional LLM)
      -> persist facts
      -> INCREMENTAL reasoning: compare only the NEW facts against facts already
         in the store, create/update relationships
      -> persist relationships + document stats

Only the new document's facts drive comparison, so ingesting document N does not
re-process documents 1..N-1 — the knowledge layer grows incrementally.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from app.ingestion.pdf_loader import load_bytes
from app.models import Document, DocumentStatus
from app.normalize.entities import detect_primary_entity
from app.extraction.extractor import extract_facts
from app.reasoning.candidates import generate_pairs
from app.reasoning.relationships import classify
from app.services import store


def _persist_source(document_id: str, data: bytes) -> None:
    """Store the raw PDF under the upload dir so pages can be rendered later
    (evidence highlighting). Keyed by document_id, not the original name."""
    from pathlib import Path
    from app.config import settings
    dest = Path(settings.upload_dir) / f"{document_id}.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(data)


def source_pdf_path(document_id: str):
    from pathlib import Path
    from app.config import settings
    p = Path(settings.upload_dir) / f"{document_id}.pdf"
    return p if p.exists() else None


@dataclass
class IngestResult:
    document: Document
    facts: int
    relationships: int
    duplicate: bool = False


def ingest_bytes(data: bytes, name: str) -> IngestResult:
    from app.ingestion.pdf_loader import sha256_bytes

    sha = sha256_bytes(data)
    existing = store.find_document_by_hash(sha)
    if existing:                                      # de-duplication
        return IngestResult(document=existing, facts=existing.fact_count,
                            relationships=existing.relationship_count, duplicate=True)

    document_id = sha[:16]
    _persist_source(document_id, data)                # keep bytes for page rendering
    doc = Document(document_id=document_id, name=name, sha256=sha,
                   byte_size=len(data), status=DocumentStatus.PROCESSING)
    store.upsert_document(doc)

    t0 = time.time()
    try:
        loaded = load_bytes(data, name)
    except ValueError as exc:                         # malformed / encrypted PDF
        doc.status = DocumentStatus.FAILED
        doc.error = str(exc)
        store.upsert_document(doc)
        return IngestResult(document=doc, facts=0, relationships=0)

    full_text = "\n".join(p.text for p in loaded.pages)
    primary_entity = detect_primary_entity(full_text, hint=name)
    facts, llm_calls = extract_facts(loaded, document_id, primary_entity)

    store.insert_facts(facts)

    # Incremental reasoning: new facts vs everything already stored.
    existing_facts = [f for f in store.all_facts() if f.source_document_id != document_id]
    pairs = generate_pairs(facts, existing_facts, cross_document_only=True)
    rels = []
    for a, b in pairs:
        r = classify(a, b)
        if r:
            rels.append(r)
    store.upsert_relationships(rels)

    doc.page_count = loaded.page_count
    doc.primary_entity = primary_entity
    doc.status = DocumentStatus.PROCESSED
    doc.fact_count = len(facts)
    doc.relationship_count = len(rels)
    doc.processing_ms = int((time.time() - t0) * 1000)
    doc.llm_calls = llm_calls
    store.upsert_document(doc)

    return IngestResult(document=doc, facts=len(facts), relationships=len(rels))


def ingest_path(path: str) -> IngestResult:
    import os
    with open(path, "rb") as fh:
        data = fh.read()
    return ingest_bytes(data, os.path.basename(path))
