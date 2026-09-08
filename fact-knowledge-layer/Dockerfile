FROM python:3.11-slim

WORKDIR /app

# System deps kept minimal; PyMuPDF ships manylinux wheels so no build tools needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Seed the bundled sample documents at build time so the container is demo-ready.
RUN python -m scripts.seed_samples || true

EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
