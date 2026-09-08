"""Seed the store with the bundled sample documents (for the demo / eval).

Usage:  python -m scripts.seed_samples [--reset]
"""
from __future__ import annotations

import glob
import os
import sys
import time

from app.config import settings
from app.db import init_db
from app.services import pipeline

SAMPLE_GLOB = "sample_docs/**/*.pdf"


def main() -> None:
    if "--reset" in sys.argv:
        for p in (settings.db_path, settings.db_path + "-wal", settings.db_path + "-shm"):
            if os.path.exists(p):
                os.remove(p)
        print("reset store")
    settings.ensure_dirs()
    init_db()

    paths = sorted(glob.glob(SAMPLE_GLOB, recursive=True))
    if not paths:
        print("No sample PDFs found under sample_docs/.")
        return
    t0 = time.time()
    for path in paths:
        r = pipeline.ingest_path(path)
        tag = "DUPLICATE" if r.duplicate else "ok"
        print(f"  {os.path.basename(path):48s} facts={r.facts:4d} "
              f"new_relationships={r.relationships:4d}  {tag}")
    print(f"\nSeeded {len(paths)} documents in {time.time()-t0:.1f}s "
          f"(LLM {'ON' if settings.llm_active else 'OFF'}).")


if __name__ == "__main__":
    main()
