"""SQLite storage layer.

A relational store is enough for a knowledge layer of this shape — documents,
facts, evidence and relationships are simple entities with clear keys. We keep
indexed columns for the fields we filter/join on and stash the full typed object
as JSON, which is what makes the fact schema *evolve without migrations*: a new
attribute or qualifier is just new JSON, no ALTER TABLE.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id      TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    sha256           TEXT NOT NULL UNIQUE,
    page_count       INTEGER,
    byte_size        INTEGER,
    status           TEXT,
    error            TEXT,
    primary_entity   TEXT,
    fact_count       INTEGER DEFAULT 0,
    relationship_count INTEGER DEFAULT 0,
    processing_ms    INTEGER DEFAULT 0,
    llm_calls        INTEGER DEFAULT 0,
    created_at       TEXT,
    json             TEXT
);

CREATE TABLE IF NOT EXISTS facts (
    fact_id          TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL,
    canonical_entity TEXT,
    canonical_attribute TEXT,
    attribute        TEXT,
    period_key       TEXT,
    fact_type        TEXT,
    normalized_value REAL,
    normalized_unit  TEXT,
    source_page      INTEGER,
    extraction_confidence REAL,
    json             TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(document_id)
);
CREATE INDEX IF NOT EXISTS idx_facts_entity_attr
    ON facts(canonical_entity, canonical_attribute);
CREATE INDEX IF NOT EXISTS idx_facts_document ON facts(document_id);

CREATE TABLE IF NOT EXISTS relationships (
    relationship_id  TEXT PRIMARY KEY,
    fact_a_id        TEXT NOT NULL,
    fact_b_id        TEXT NOT NULL,
    relation         TEXT,
    confidence       REAL,
    json             TEXT NOT NULL,
    FOREIGN KEY (fact_a_id) REFERENCES facts(fact_id),
    FOREIGN KEY (fact_b_id) REFERENCES facts(fact_id)
);
CREATE INDEX IF NOT EXISTS idx_rel_a ON relationships(fact_a_id);
CREATE INDEX IF NOT EXISTS idx_rel_b ON relationships(fact_b_id);
CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(relation);
"""


def _connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(path: str | None = None) -> None:
    path = path or settings.db_path
    conn = _connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn(path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = _connect(path or settings.db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
