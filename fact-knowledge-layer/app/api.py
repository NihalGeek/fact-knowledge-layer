"""FastAPI application: upload PDFs, inspect facts, evidence and relationships."""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import settings
from app.db import init_db
from app.models import Relationship
from app.services import pipeline, store

_PDF_MAGIC = b"%PDF-"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()
    yield


app = FastAPI(title="Fact Knowledge Layer", version=__version__, lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _enrich_relationship(r: Relationship) -> dict:
    facts = store.get_facts([r.fact_a_id, r.fact_b_id])
    a, b = facts.get(r.fact_a_id), facts.get(r.fact_b_id)
    return {
        "relationship": r.model_dump(mode="json"),
        "fact_a": a.model_dump(mode="json") if a else None,
        "fact_b": b.model_dump(mode="json") if b else None,
    }


# --------------------------------------------------------------------------- #
# Health / stats
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok", "version": __version__, "llm_active": settings.llm_active}


@app.get("/stats")
def get_stats():
    return store.stats()


@app.get("/entities")
def entities():
    return store.list_entities()


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
@app.post("/documents/upload")
async def upload_document(file: UploadFile = File(...)):
    name = _SAFE_NAME.sub("_", Path(file.filename or "upload.pdf").name)
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only .pdf files are accepted.")
    data = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(413, f"File exceeds {settings.max_upload_mb} MB limit.")
    if not data.startswith(_PDF_MAGIC):
        raise HTTPException(400, "File does not look like a valid PDF.")

    # Persist the upload safely inside the configured upload dir (no traversal).
    dest = Path(settings.upload_dir) / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)

    result = pipeline.ingest_bytes(data, name)
    return {
        "document": result.document.model_dump(mode="json"),
        "facts": result.facts,
        "relationships": result.relationships,
        "duplicate": result.duplicate,
    }


@app.get("/documents")
def documents():
    return [d.model_dump(mode="json") for d in store.list_documents()]


@app.get("/documents/{document_id}")
def document(document_id: str):
    doc = store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Document not found.")
    return doc.model_dump(mode="json")


@app.delete("/documents/{document_id}")
def remove_document(document_id: str):
    doc = store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Document not found.")
    result = store.delete_document(document_id)
    # remove the stored source PDF used for evidence rendering
    from app.services.pipeline import source_pdf_path
    p = source_pdf_path(document_id)
    if p:
        try:
            p.unlink()
        except OSError:
            pass
    return {"deleted": document_id, "name": doc.name, **result}


@app.get("/documents/{document_id}/facts")
def document_facts(document_id: str):
    if not store.get_document(document_id):
        raise HTTPException(404, "Document not found.")
    return [f.model_dump(mode="json") for f in store.facts_for_document(document_id)]


# --------------------------------------------------------------------------- #
# Facts
# --------------------------------------------------------------------------- #
@app.get("/facts")
def facts(entity: str | None = None, attribute: str | None = None,
          fact_type: str | None = None, limit: int = Query(200, le=2000)):
    result = store.all_facts()
    if entity:
        result = [f for f in result if entity.lower() in f.canonical_entity.lower()]
    if attribute:
        result = [f for f in result
                  if attribute.lower() in f.canonical_attribute.lower()
                  or attribute.lower() in f.attribute.lower()]
    if fact_type:
        result = [f for f in result if f.fact_type.value == fact_type]
    result.sort(key=lambda f: f.extraction_confidence, reverse=True)
    return [f.model_dump(mode="json") for f in result[:limit]]


@app.get("/facts/{fact_id}")
def fact(fact_id: str):
    f = store.get_fact(fact_id)
    if not f:
        raise HTTPException(404, "Fact not found.")
    return f.model_dump(mode="json")


@app.get("/facts/{fact_id}/relationships")
def fact_relationships(fact_id: str):
    if not store.get_fact(fact_id):
        raise HTTPException(404, "Fact not found.")
    return [_enrich_relationship(r) for r in store.relationships_for_fact(fact_id)]


@app.get("/facts/{fact_id}/evidence.png")
def fact_evidence_image(fact_id: str):
    from fastapi.responses import Response
    from app.services.render import render_evidence_png
    f = store.get_fact(fact_id)
    if not f:
        raise HTTPException(404, "Fact not found.")
    png = render_evidence_png(f)
    if png is None:
        raise HTTPException(404, "Source PDF not available for rendering.")
    return Response(content=png, media_type="image/png")


# --------------------------------------------------------------------------- #
# Relationships / search
# --------------------------------------------------------------------------- #
@app.get("/relationships")
def relationships(relation: str | None = None,
                  min_confidence: float = Query(0.0, ge=0.0, le=1.0),
                  limit: int = Query(500, le=5000)):
    rels = store.list_relationships(relation=relation, min_confidence=min_confidence,
                                    limit=limit)
    return [_enrich_relationship(r) for r in rels]


@app.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(100, le=1000)):
    return [f.model_dump(mode="json") for f in store.search_facts(q, limit=limit)]


@app.get("/consistency")
def consistency(document_id: str | None = None):
    """Intra-document accounting-identity checks (e.g. total income = revenue +
    other income). Surfaces extraction errors and internal inconsistencies."""
    from app.reasoning.consistency import check_facts
    facts = store.facts_for_document(document_id) if document_id else store.all_facts()
    checks = check_facts(facts)
    checks.sort(key=lambda c: (c.ok, c.entity))       # failures first
    return [c.model_dump(mode="json") for c in checks]


# --------------------------------------------------------------------------- #
# UI (served last so API routes take precedence)
# --------------------------------------------------------------------------- #
_STATIC = Path(__file__).parent / "static"


@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
