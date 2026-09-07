"""HTTP API + UI for the fact knowledge layer.

Ingest takes minutes (it is bounded by the provider's tokens-per-minute limit),
so uploads return immediately with a job id and the UI polls for progress.
Everything the pipeline decided is exposed -- claims, evidence, relationships,
and the reasoning trace behind every verdict -- because the reasoning is the
part worth inspecting.
"""
from __future__ import annotations

import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import CONFIG
from .pdf import file_id
from .pipeline import build_relations, ingest_pdf
from .store import Store

WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(title="Fact Knowledge Layer", version="0.1.0")
_store: Store | None = None
_store_lock = threading.Lock()

# In-memory job registry. Deliberately not persisted: jobs describe a running
# process, and a restart legitimately invalidates them. The claims themselves
# are durable in SQLite.
JOBS: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            _store = Store()
        return _store


def _set_job(job_id: str, **fields) -> None:
    with _jobs_lock:
        JOBS.setdefault(job_id, {}).update(fields)


# --------------------------------------------------------------------------
# ingestion
# --------------------------------------------------------------------------
def _run_ingest(job_id: str, path: Path, relate: bool) -> None:
    started = time.time()
    _set_job(job_id, status="running", stage="loading", detail={}, started_at=started)

    def progress(stage: str, info: dict) -> None:
        _set_job(job_id, stage=stage, detail=info, elapsed=round(time.time() - started, 1))

    try:
        report = ingest_pdf(path, store(), progress=progress)
        _set_job(job_id, doc_id=report.doc_id, report=report.as_dict())

        if relate:
            _set_job(job_id, stage="relating")
            new_ids = [c["claim_id"] for c in
                       store().query_claims(doc_id=report.doc_id, limit=10**9)]
            # Only the new document's claims are compared against the corpus --
            # existing pairs are already decided and are not recomputed.
            rel = build_relations(store(), new_claim_ids=new_ids, progress=progress)
            _set_job(job_id, relations=rel.as_dict())

        _set_job(job_id, status="done", stage="complete",
                 elapsed=round(time.time() - started, 1))
    except Exception as e:
        _set_job(job_id, status="failed", stage="error",
                 error=f"{type(e).__name__}: {e}",
                 elapsed=round(time.time() - started, 1))


@app.post("/api/documents")
async def upload_document(background: BackgroundTasks, file: UploadFile = File(...),
                          relate: bool = Query(True)):
    """Accept a new PDF and start ingesting it. Returns a job id to poll."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files are accepted.")

    CONFIG.ensure_dirs()
    dest = CONFIG.upload_dir / f"{uuid.uuid4().hex[:8]}-{Path(file.filename).name}"
    with open(dest, "wb") as fh:
        shutil.copyfileobj(file.file, fh)

    existing = store().get_document(file_id(dest))
    job_id = uuid.uuid4().hex[:12]
    _set_job(job_id, job_id=job_id, filename=file.filename, status="queued",
             stage="queued", already_ingested=bool(existing))
    background.add_task(_run_ingest, job_id, dest, relate)
    return {"job_id": job_id, "filename": file.filename,
            "already_ingested": bool(existing),
            "note": "Ingest is paced against the provider's token limit and may "
                    "take several minutes for a large PDF. Poll /api/jobs/{job_id}."}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    with _jobs_lock:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    return job


@app.get("/api/jobs")
async def list_jobs():
    with _jobs_lock:
        return sorted(JOBS.values(), key=lambda j: j.get("started_at", 0), reverse=True)


# --------------------------------------------------------------------------
# reading the knowledge layer
# --------------------------------------------------------------------------
@app.get("/api/documents")
async def list_documents():
    return store().list_documents()


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str):
    if not store().get_document(doc_id):
        raise HTTPException(status_code=404, detail="Unknown document.")
    store().delete_document(doc_id)
    return {"deleted": doc_id}


@app.get("/api/claims")
async def list_claims(doc_id: str | None = None, needs_review: bool | None = None,
                      search: str | None = None, limit: int = 100, offset: int = 0):
    return store().query_claims(doc_id=doc_id, needs_review=needs_review,
                                search=search, limit=min(limit, 1000), offset=offset)


@app.get("/api/claims/{claim_id}")
async def get_claim(claim_id: str):
    claim = store().get_claim(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Unknown claim.")
    related = store().query_relations(cross_document_only=False, limit=200)
    claim["relations"] = [r for r in related
                          if claim_id in (r["claim_a_id"], r["claim_b_id"])]
    return claim


@app.get("/api/relations")
async def list_relations(kind: str | None = None, doc_id: str | None = None,
                         cross_document_only: bool = True,
                         limit: int = 100, offset: int = 0):
    """Relationships, each returned with both claims and their evidence inline."""
    rels = store().query_relations(kind=kind, doc_id=doc_id,
                                   cross_document_only=cross_document_only,
                                   limit=min(limit, 1000), offset=offset)
    ids = {r["claim_a_id"] for r in rels} | {r["claim_b_id"] for r in rels}
    claims = store().get_claims(sorted(ids))
    for r in rels:
        r["claim_a"] = claims.get(r["claim_a_id"])
        r["claim_b"] = claims.get(r["claim_b_id"])
    return rels


@app.get("/api/stats")
async def stats():
    st = store()
    docs = st.list_documents()
    return {
        "documents": len(docs),
        "claims": st.count_claims(),
        "claims_needing_review": st.count_claims(needs_review=True),
        "relations_by_kind": st.relation_counts(),
        "config": {
            "model": CONFIG.llm_model,
            "endpoint": CONFIG.llm_base_url,
            "embedding_model": CONFIG.embed_model,
            "grounding_threshold": CONFIG.grounding_threshold,
            "match_threshold": CONFIG.match_threshold,
            "review_threshold": CONFIG.review_threshold,
        },
    }


@app.post("/api/relations/rebuild")
async def rebuild_relations(background: BackgroundTasks, escalate: bool = Query(True)):
    """Recompare the whole corpus. Useful after tuning thresholds."""
    job_id = uuid.uuid4().hex[:12]
    _set_job(job_id, job_id=job_id, filename="(rebuild relations)", status="queued",
             stage="queued", started_at=time.time())

    def run() -> None:
        started = time.time()
        _set_job(job_id, status="running")
        try:
            rel = build_relations(
                store(), escalate=escalate,
                progress=lambda s, i: _set_job(job_id, stage=s, detail=i,
                                               elapsed=round(time.time() - started, 1)),
            )
            _set_job(job_id, status="done", stage="complete", relations=rel.as_dict())
        except Exception as e:
            _set_job(job_id, status="failed", error=f"{type(e).__name__}: {e}")

    background.add_task(run)
    return {"job_id": job_id}


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
@app.get("/")
async def index():
    page = WEB_DIR / "index.html"
    if not page.exists():
        return JSONResponse({"error": "UI not built"}, status_code=404)
    return FileResponse(page)


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
