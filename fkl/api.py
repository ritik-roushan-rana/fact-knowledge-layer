"""HTTP API + UI for the fact knowledge layer.

Ingest takes minutes (it is bounded by the provider's tokens-per-minute limit),
so uploads return immediately with a job id and the UI polls for progress.
Everything the pipeline decided is exposed -- claims, evidence, relationships,
and the reasoning trace behind every verdict -- because the reasoning is the
part worth inspecting.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import CONFIG
from .extractors import build_extractor
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
    """Create or update a job record. The id is always present in the record,
    so callers never pass it as a field as well."""
    with _jobs_lock:
        JOBS.setdefault(job_id, {"job_id": job_id}).update(fields)


# --------------------------------------------------------------------------
# ingestion
# --------------------------------------------------------------------------
def _run_ingest(job_id: str, path: Path, relate: bool,
                extractor: str | None = None, force: bool = False) -> None:
    started = time.time()
    _set_job(job_id, status="running", stage="loading", detail={}, started_at=started)

    def progress(stage: str, info: dict) -> None:
        _set_job(job_id, stage=stage, detail=info, elapsed=round(time.time() - started, 1))

    try:
        report = ingest_pdf(path, store(), extractor=extractor, force=force,
                            progress=progress)
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
                          relate: bool = Query(True),
                          extractor: str | None = Query(None),
                          force: bool = Query(False)):
    """Accept a new PDF and start ingesting it. Returns a job id to poll."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files are accepted.")

    CONFIG.ensure_dirs()
    # Uniqueness goes on the directory, not the filename: the document keeps
    # the name the user recognises, and two uploads called "report.pdf" still
    # cannot collide on disk.
    folder = CONFIG.upload_dir / uuid.uuid4().hex[:8]
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / Path(file.filename).name
    with open(dest, "wb") as fh:
        shutil.copyfileobj(file.file, fh)

    existing = store().get_document(file_id(dest))
    job_id = uuid.uuid4().hex[:12]
    _set_job(job_id, filename=file.filename, status="queued",
             stage="queued", already_ingested=bool(existing))
    background.add_task(_run_ingest, job_id, dest, relate, extractor, force)
    return {"job_id": job_id, "filename": file.filename,
            "already_ingested": bool(existing),
            "extractor": extractor or CONFIG.extractor,
            "note": "Deterministic extraction runs at roughly 5 pages/second and "
                    "needs no API key. Poll /api/jobs/{job_id} for progress."}


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
                      quarantined: bool | None = None, origin: str | None = None,
                      search: str | None = None, limit: int = 100, offset: int = 0):
    return store().query_claims(doc_id=doc_id, needs_review=needs_review,
                                quarantined=quarantined, origin=origin,
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


@app.get("/api/claims/{claim_id}/preview.png")
async def claim_preview(claim_id: str, zoom: str = Query("crop")):
    """Render the claim's source page as a PNG with its bbox highlighted.

    Serves the strongest version of the engineering story visually: every
    claim points at a real region of a real page, and you can see it. Uses
    PyMuPDF -- already a dependency for extraction -- so no browser-side PDF
    renderer is needed.

    ``zoom=crop`` (default) shows a tight crop around the bbox with padding;
    ``zoom=page`` renders the whole page with the bbox drawn in red.
    """
    import pymupdf

    claim = store().get_claim(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Unknown claim.")
    bbox = claim.get("bbox")
    if not bbox:
        raise HTTPException(status_code=404, detail="This claim has no bbox.")

    doc = store().get_document(claim["doc_id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Source document not found.")
    pdf_path = Path(doc.get("path") or "")
    if not pdf_path.exists():
        raise HTTPException(status_code=404,
                            detail=f"Source PDF not on disk at {pdf_path!s}.")

    page_num = claim.get("grounding_page") or claim["span_page"]
    try:
        pdf = pymupdf.open(pdf_path)
        page = pdf[page_num - 1]
        rect = pymupdf.Rect(*bbox)
        # Draw a red outline over the bbox in-memory; the PDF file is not
        # modified. draw_rect() lives on the page's shape, not the document.
        page.draw_rect(rect, color=(0.9, 0.15, 0.15), width=1.6, overlay=True)

        if zoom == "page":
            pix = page.get_pixmap(dpi=120)
        else:
            # Tight crop with padding, so the eye lands on the cell without
            # losing surrounding context.
            pad = 60.0
            page_rect = page.rect
            clip = pymupdf.Rect(max(page_rect.x0, rect.x0 - pad),
                                max(page_rect.y0, rect.y0 - pad),
                                min(page_rect.x1, rect.x1 + pad),
                                min(page_rect.y1, rect.y1 + pad))
            pix = page.get_pixmap(dpi=160, clip=clip)
        png_bytes = pix.tobytes("png")
    finally:
        try:
            pdf.close()
        except Exception:
            pass
    return Response(png_bytes, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=300"})


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


@app.get("/api/relations/{relation_id}/counterfactuals")
async def relation_counterfactuals(relation_id: str):
    """What the verdict would be if one qualifier were missing.

    Runs the deterministic cascade once per ablatable qualifier and lists
    only the ablations that change the verdict. The user sees exactly which
    qualifiers were doing the work on this pair -- a demonstration that
    context, not just values, is what the reasoning turns on.
    """
    from .compare import counterfactuals
    rows = store().query_relations(cross_document_only=False,
                                   exclude_unrelated=False, limit=10)
    match = None
    for r in rows:
        if r["relation_id"] == relation_id:
            match = r
            break
    if match is None:
        # Fall back to a direct search across the whole table (query_relations
        # sorts by confidence, so a rare relation may not appear in the top
        # slice).
        page = 0
        while True:
            batch = store().query_relations(cross_document_only=False,
                                            exclude_unrelated=False,
                                            limit=500, offset=page * 500)
            if not batch:
                break
            for r in batch:
                if r["relation_id"] == relation_id:
                    match = r
                    break
            if match is not None:
                break
            page += 1
    if match is None:
        raise HTTPException(status_code=404, detail="Unknown relation.")

    a = store().get_claim(match["claim_a_id"])
    b = store().get_claim(match["claim_b_id"])
    if not a or not b:
        raise HTTPException(status_code=404,
                            detail="One of the claims in this relation was deleted.")
    return {
        "relation_id": relation_id,
        "live_kind": match["kind"],
        "live_rule_id": match.get("rule_id"),
        "counterfactuals": counterfactuals(a, b, similarity=match["similarity"]),
    }


@app.get("/api/stats")
async def stats():
    st = store()
    docs = st.list_documents()
    extractor = build_extractor()
    return {
        "documents": len(docs),
        "claims": st.count_claims(),
        "claims_from_tables": st.count_claims(origin="table"),
        "claims_from_sentences": st.count_claims(origin="sentence"),
        "claims_quarantined": st.count_claims(quarantined=True),
        "claims_needing_review": st.count_claims(needs_review=True),
        "relations_by_kind": st.relation_counts(),
        "engine": {
            "extractor": extractor.name,
            "requires_api_key": extractor.requires_api_key,
            "llm_fallback_enabled": CONFIG.enable_llm_fallback,
            "llm_configured": bool(os.environ.get(CONFIG.llm_api_key_env)),
            "judge_model": CONFIG.judge_model,
        },
        "thresholds": {
            "grounding": CONFIG.grounding_threshold,
            "review": CONFIG.review_threshold,
            "subject_match": CONFIG.subject_threshold,
            "predicate_match": CONFIG.predicate_threshold,
            "value_tolerance": CONFIG.value_tolerance,
            "percentage_point_tolerance": CONFIG.percentage_point_tolerance,
        },
    }


@app.post("/api/relations/rebuild")
async def rebuild_relations(background: BackgroundTasks, escalate: bool = Query(True)):
    """Recompare the whole corpus. Useful after tuning thresholds."""
    job_id = uuid.uuid4().hex[:12]
    _set_job(job_id, filename="(rebuild relations)", status="queued",
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
#
# Two decisions worth calling out. index.html is rewritten on each request to
# stamp the app.js src with a cache-busting query, using the largest mtime
# under fkl/web/ as the tag. That means any edit to the frontend is picked up
# by the browser without the user having to hard-refresh: a real bug we hit
# was a fast ingest completing before the poller re-rendered, and the
# investigation was slowed down by a stale cached bundle. The tag changes only
# when a file actually changes, so it does not defeat caching -- it just makes
# it correct.
#
# Static assets are then served with Cache-Control: no-cache. That still lets
# the browser reuse the local copy, but only after checking with the server
# via a conditional request. ETag/Last-Modified do the rest.

def _web_build_tag() -> str:
    latest = 0.0
    for path in WEB_DIR.rglob("*"):
        if path.is_file():
            latest = max(latest, path.stat().st_mtime)
    return f"{int(latest)}"


class _NoCacheStaticFiles(StaticFiles):
    """StaticFiles with revalidation-first cache headers.

    Development iteration on the JS/CSS should never require a manual
    hard-refresh, and the cost of a conditional GET (a few hundred bytes) is
    negligible against the cost of a user staring at a stuck UI thinking the
    ingest hung.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


@app.get("/")
async def index():
    page = WEB_DIR / "index.html"
    if not page.exists():
        return JSONResponse({"error": "UI not built"}, status_code=404)
    html = page.read_text(encoding="utf-8")
    # Attach a build tag to the module entry point so browsers pick up a
    # frontend change even without a hard-refresh. The tag is the newest
    # mtime under fkl/web/, so it only changes when something actually did.
    tag = _web_build_tag()
    html = html.replace('src="/static/js/main.js"',
                        f'src="/static/js/main.js?v={tag}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})


if WEB_DIR.exists():
    app.mount("/static", _NoCacheStaticFiles(directory=WEB_DIR), name="static")
