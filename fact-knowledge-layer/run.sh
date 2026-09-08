#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python -m pip install --quiet -r requirements.txt 2>/dev/null || true
python -m scripts.seed_samples "$@"          # ingest bundled sample PDFs (idempotent)
echo "Starting API + UI on http://127.0.0.1:8000 ..."
exec uvicorn app.api:app --host 127.0.0.1 --port 8000
