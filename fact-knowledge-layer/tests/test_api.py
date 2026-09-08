"""API integration tests using FastAPI's TestClient over a temporary store."""
import glob
import os
import tempfile

import pytest

# Point the app at a throwaway DB before importing it.
_TMP = tempfile.mkdtemp()
os.environ["FACTLAYER_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["FACTLAYER_UPLOAD_DIR"] = os.path.join(_TMP, "uploads")

from fastapi.testclient import TestClient   # noqa: E402
from app.api import app                     # noqa: E402

DECK = next(iter(glob.glob("sample_docs/**/03-delhivery-q4*.pdf", recursive=True)), None)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_reject_non_pdf(client):
    r = client.post("/documents/upload",
                    files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_reject_fake_pdf(client):
    r = client.post("/documents/upload",
                    files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 400


@pytest.mark.skipif(DECK is None, reason="sample deck not present")
def test_upload_and_query_flow(client):
    with open(DECK, "rb") as fh:
        data = fh.read()
    r = client.post("/documents/upload",
                    files={"file": ("deck.pdf", data, "application/pdf")})
    assert r.status_code == 200
    body = r.json()
    assert body["facts"] > 0

    # duplicate upload is detected
    r2 = client.post("/documents/upload",
                     files={"file": ("deck.pdf", data, "application/pdf")})
    assert r2.json()["duplicate"] is True

    # facts + search
    assert client.get("/facts?limit=5").status_code == 200
    hits = client.get("/search?q=team size").json()
    assert any("team size" in f["canonical_attribute"] for f in hits)

    # documents + stats
    assert len(client.get("/documents").json()) >= 1
    assert client.get("/stats").json()["facts"] > 0

    # evidence image renders for a fact
    fid = client.get("/facts?limit=1").json()[0]["fact_id"]
    img = client.get(f"/facts/{fid}/evidence.png")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert img.content[:4] == b"\x89PNG"

    # consistency endpoint responds
    assert client.get("/consistency").status_code == 200

    # delete cascades: document + its facts + its relationships all go away
    doc_id = client.get("/documents").json()[0]["document_id"]
    facts_before = client.get("/stats").json()["facts"]
    dresp = client.request("DELETE", f"/documents/{doc_id}")
    assert dresp_ok(dresp)
    assert client.get(f"/documents/{doc_id}").status_code == 404
    assert client.get(f"/documents/{doc_id}/facts").status_code == 404
    assert client.get("/stats").json()["facts"] < facts_before
    # deleting an unknown document is a clean 404
    assert client.request("DELETE", "/documents/nope").status_code == 404


def dresp_ok(r):
    return r.status_code == 200 and r.json()["facts_removed"] >= 0
