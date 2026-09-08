"""Persistence / repository functions over the SQLite store."""
from __future__ import annotations

import json
from typing import Optional

from app.db import get_conn
from app.models import Document, Fact, Relationship


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
def find_document_by_hash(sha256: str) -> Optional[Document]:
    with get_conn() as c:
        row = c.execute("SELECT json FROM documents WHERE sha256=?", (sha256,)).fetchone()
    return Document.model_validate_json(row["json"]) if row else None


def upsert_document(doc: Document) -> None:
    with get_conn() as c:
        c.execute(
            """INSERT INTO documents (document_id,name,sha256,page_count,byte_size,
                 status,error,primary_entity,fact_count,relationship_count,
                 processing_ms,llm_calls,created_at,json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(document_id) DO UPDATE SET
                 status=excluded.status, error=excluded.error,
                 primary_entity=excluded.primary_entity, fact_count=excluded.fact_count,
                 relationship_count=excluded.relationship_count,
                 processing_ms=excluded.processing_ms, llm_calls=excluded.llm_calls,
                 json=excluded.json""",
            (doc.document_id, doc.name, doc.sha256, doc.page_count, doc.byte_size,
             doc.status.value, doc.error, doc.primary_entity, doc.fact_count,
             doc.relationship_count, doc.processing_ms, doc.llm_calls,
             doc.created_at.isoformat(), doc.model_dump_json()),
        )


def list_documents() -> list[Document]:
    with get_conn() as c:
        rows = c.execute("SELECT json FROM documents ORDER BY created_at DESC").fetchall()
    return [Document.model_validate_json(r["json"]) for r in rows]


def get_document(document_id: str) -> Optional[Document]:
    with get_conn() as c:
        row = c.execute("SELECT json FROM documents WHERE document_id=?",
                        (document_id,)).fetchone()
    return Document.model_validate_json(row["json"]) if row else None


def delete_document(document_id: str) -> dict:
    """Remove a document and cascade: its facts, and every relationship that
    references any of those facts. Returns counts of what was removed."""
    with get_conn() as c:
        fact_ids = [r["fact_id"] for r in
                    c.execute("SELECT fact_id FROM facts WHERE document_id=?", (document_id,))]
        rels_removed = 0
        if fact_ids:
            ph = ",".join("?" * len(fact_ids))
            cur = c.execute(
                f"DELETE FROM relationships WHERE fact_a_id IN ({ph}) OR fact_b_id IN ({ph})",
                fact_ids + fact_ids)
            rels_removed = cur.rowcount
        facts_removed = c.execute("DELETE FROM facts WHERE document_id=?",
                                  (document_id,)).rowcount
        c.execute("DELETE FROM documents WHERE document_id=?", (document_id,))
    return {"facts_removed": facts_removed, "relationships_removed": rels_removed}


# --------------------------------------------------------------------------- #
# Facts
# --------------------------------------------------------------------------- #
def insert_facts(facts: list[Fact]) -> None:
    with get_conn() as c:
        c.executemany(
            """INSERT OR REPLACE INTO facts (fact_id,document_id,canonical_entity,
                 canonical_attribute,attribute,period_key,fact_type,normalized_value,
                 normalized_unit,source_page,extraction_confidence,json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(f.fact_id, f.source_document_id, f.canonical_entity, f.canonical_attribute,
              f.attribute, f.period.comparable_key(), f.fact_type.value,
              f.normalized_value, f.normalized_unit, f.source_page,
              f.extraction_confidence, f.model_dump_json()) for f in facts],
        )


def all_facts() -> list[Fact]:
    with get_conn() as c:
        rows = c.execute("SELECT json FROM facts").fetchall()
    return [Fact.model_validate_json(r["json"]) for r in rows]


def facts_for_document(document_id: str) -> list[Fact]:
    with get_conn() as c:
        rows = c.execute("SELECT json FROM facts WHERE document_id=? "
                         "ORDER BY source_page", (document_id,)).fetchall()
    return [Fact.model_validate_json(r["json"]) for r in rows]


def get_fact(fact_id: str) -> Optional[Fact]:
    with get_conn() as c:
        row = c.execute("SELECT json FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
    return Fact.model_validate_json(row["json"]) if row else None


def get_facts(fact_ids: list[str]) -> dict[str, Fact]:
    if not fact_ids:
        return {}
    placeholders = ",".join("?" * len(fact_ids))
    with get_conn() as c:
        rows = c.execute(f"SELECT fact_id,json FROM facts WHERE fact_id IN ({placeholders})",
                         fact_ids).fetchall()
    return {r["fact_id"]: Fact.model_validate_json(r["json"]) for r in rows}


def search_facts(query: str, limit: int = 100) -> list[Fact]:
    like = f"%{query.lower()}%"
    with get_conn() as c:
        rows = c.execute(
            """SELECT json FROM facts
               WHERE lower(attribute) LIKE ? OR lower(canonical_entity) LIKE ?
                  OR lower(canonical_attribute) LIKE ?
               ORDER BY extraction_confidence DESC LIMIT ?""",
            (like, like, like, limit)).fetchall()
    return [Fact.model_validate_json(r["json"]) for r in rows]


# --------------------------------------------------------------------------- #
# Relationships
# --------------------------------------------------------------------------- #
def upsert_relationships(rels: list[Relationship]) -> None:
    with get_conn() as c:
        c.executemany(
            """INSERT OR REPLACE INTO relationships
                 (relationship_id,fact_a_id,fact_b_id,relation,confidence,json)
               VALUES (?,?,?,?,?,?)""",
            [(r.relationship_id, r.fact_a_id, r.fact_b_id, r.relation.value,
              r.confidence, r.model_dump_json()) for r in rels],
        )


def relationships_for_fact(fact_id: str) -> list[Relationship]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT json FROM relationships WHERE fact_a_id=? OR fact_b_id=? "
            "ORDER BY confidence DESC", (fact_id, fact_id)).fetchall()
    return [Relationship.model_validate_json(r["json"]) for r in rows]


def list_relationships(relation: Optional[str] = None, min_confidence: float = 0.0,
                       limit: int = 500) -> list[Relationship]:
    sql = "SELECT json FROM relationships WHERE confidence>=?"
    params: list = [min_confidence]
    if relation:
        sql += " AND relation=?"
        params.append(relation)
    sql += " ORDER BY confidence DESC LIMIT ?"
    params.append(limit)
    with get_conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [Relationship.model_validate_json(r["json"]) for r in rows]


def list_entities() -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT canonical_entity AS entity, COUNT(*) AS fact_count "
            "FROM facts GROUP BY canonical_entity ORDER BY fact_count DESC").fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    with get_conn() as c:
        docs = c.execute("SELECT COUNT(*) n FROM documents").fetchone()["n"]
        facts = c.execute("SELECT COUNT(*) n FROM facts").fetchone()["n"]
        rels = c.execute("SELECT COUNT(*) n FROM relationships").fetchone()["n"]
        by_type = {r["relation"]: r["n"] for r in c.execute(
            "SELECT relation, COUNT(*) n FROM relationships GROUP BY relation").fetchall()}
    return {"documents": docs, "facts": facts, "relationships": rels,
            "relationships_by_type": by_type}
