"""SQLite persistence.

One file, no server. Every claim row holds both what an extractor asserted and
what the grounding verifier proved about it, so any row can be audited on its
own without re-running anything.

Schema changes are applied additively at startup (missing columns are added in
place) so an existing database keeps its documents, claims and resume state
across upgrades.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .config import CONFIG
from .embed import from_blob, to_blob
from .models import (Claim, ClaimContext, Confidence, GroundedClaim, Grounding,
                     Relation, SourceSpan)

CLAIM_COLUMNS = [
    "claim_id", "doc_id", "subject", "predicate", "value", "value_num", "unit",
    "ctx_period", "ctx_unit", "ctx_scope", "ctx_basis", "ctx_geography",
    "ctx_as_of", "ctx_denominator", "ctx_other",
    "asserted_by", "modality", "origin", "extraction_rule",
    "source_document", "span_page", "span_text",
    "extraction_confidence", "grounding_confidence", "final_confidence",
    "grounded", "grounding_score", "grounding_page", "grounding_page_label",
    "matched_text", "char_start", "char_end", "bbox", "page_shift",
    "value_in_span", "grounding_note",
    "quarantined", "needs_review", "review_reasons", "embedding", "created_at",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    path         TEXT NOT NULL,
    n_pages      INTEGER NOT NULL,
    pages_read   INTEGER NOT NULL,
    status       TEXT NOT NULL,
    error        TEXT,
    ingested_at  REAL NOT NULL,
    extractor    TEXT,
    batch_id     TEXT,
    stats        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id              TEXT PRIMARY KEY,
    doc_id                TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    subject               TEXT NOT NULL,
    predicate             TEXT NOT NULL,
    value                 TEXT NOT NULL,
    value_num             REAL,
    unit                  TEXT,
    ctx_period            TEXT,
    ctx_unit              TEXT,
    ctx_scope             TEXT,
    ctx_basis             TEXT,
    ctx_geography         TEXT,
    ctx_as_of             TEXT,
    ctx_denominator       TEXT,
    ctx_other             TEXT,
    asserted_by           TEXT,
    modality              TEXT DEFAULT 'reported',
    origin                TEXT DEFAULT 'sentence',
    extraction_rule       TEXT,
    source_document       TEXT NOT NULL,
    span_page             INTEGER NOT NULL,
    span_text             TEXT NOT NULL,
    extraction_confidence REAL NOT NULL DEFAULT 0,
    grounding_confidence  REAL NOT NULL DEFAULT 0,
    final_confidence      REAL NOT NULL DEFAULT 0,
    grounded              INTEGER NOT NULL DEFAULT 0,
    grounding_score       REAL NOT NULL DEFAULT 0,
    grounding_page        INTEGER,
    grounding_page_label  TEXT,
    matched_text          TEXT,
    char_start            INTEGER,
    char_end              INTEGER,
    bbox                  TEXT,
    page_shift            INTEGER,
    value_in_span         INTEGER,
    grounding_note        TEXT,
    quarantined           INTEGER NOT NULL DEFAULT 0,
    needs_review          INTEGER NOT NULL DEFAULT 0,
    review_reasons        TEXT NOT NULL DEFAULT '[]',
    embedding             BLOB,
    created_at            REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS relations (
    relation_id             TEXT PRIMARY KEY,
    claim_a_id              TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    claim_b_id              TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    kind                    TEXT NOT NULL,
    decided_by              TEXT NOT NULL,
    rule_id                 TEXT,
    similarity              REAL NOT NULL DEFAULT 0,
    subject_similarity      REAL,
    predicate_similarity    REAL,
    match_confidence        REAL NOT NULL DEFAULT 0,
    relationship_confidence REAL NOT NULL DEFAULT 0,
    confidence              REAL NOT NULL DEFAULT 0,
    explanation             TEXT NOT NULL,
    reasoning_trace         TEXT NOT NULL DEFAULT '[]',
    context_diff            TEXT NOT NULL DEFAULT '{}',
    value_agreement         TEXT,
    value_delta             TEXT,
    created_at              REAL NOT NULL,
    UNIQUE (claim_a_id, claim_b_id)
);
"""

# Indexes are created only after migrations run: an existing database may not
# yet have the columns they reference.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_claims_doc ON claims(doc_id);
CREATE INDEX IF NOT EXISTS idx_claims_review ON claims(needs_review);
CREATE INDEX IF NOT EXISTS idx_claims_quarantine ON claims(quarantined);
CREATE INDEX IF NOT EXISTS idx_rel_kind ON relations(kind);
CREATE INDEX IF NOT EXISTS idx_rel_a ON relations(claim_a_id);
CREATE INDEX IF NOT EXISTS idx_rel_b ON relations(claim_b_id);
"""

# Columns added after the first release, applied in place so existing
# databases keep their contents and resume state.
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "documents": [("extractor", "TEXT"), ("batch_id", "TEXT")],
    "claims": [
        ("value_num", "REAL"), ("unit", "TEXT"), ("ctx_basis", "TEXT"),
        ("ctx_geography", "TEXT"), ("ctx_as_of", "TEXT"), ("ctx_denominator", "TEXT"),
        ("asserted_by", "TEXT"), ("modality", "TEXT DEFAULT 'reported'"),
        ("origin", "TEXT DEFAULT 'sentence'"), ("extraction_rule", "TEXT"),
        ("grounding_confidence", "REAL NOT NULL DEFAULT 0"),
        ("bbox", "TEXT"), ("quarantined", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "relations": [
        ("subject_similarity", "REAL"), ("predicate_similarity", "REAL"),
        ("match_confidence", "REAL NOT NULL DEFAULT 0"),
        ("relationship_confidence", "REAL NOT NULL DEFAULT 0"),
        ("value_delta", "TEXT"),
        ("rule_id", "TEXT"),
    ],
}


class Store:
    def __init__(self, db_path: Path | str | None = None):
        CONFIG.ensure_dirs()
        self.db_path = Path(db_path or CONFIG.db_path)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.executescript(INDEXES)
        self.conn.commit()

    def _migrate(self) -> None:
        for table, columns in _MIGRATIONS.items():
            try:
                existing = {r["name"] for r in
                            self.conn.execute(f"PRAGMA table_info({table})")}
            except sqlite3.Error:
                continue
            if not existing:
                continue
            for name, decl in columns:
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    # ---------------- documents ----------------
    def upsert_document(self, *, doc_id: str, filename: str, path: str, n_pages: int,
                        pages_read: int, status: str, error: str | None = None,
                        stats: dict[str, Any] | None = None,
                        extractor: str | None = None, batch_id: str | None = None) -> None:
        self.conn.execute(
            """INSERT INTO documents (doc_id, filename, path, n_pages, pages_read,
                                      status, error, ingested_at, extractor, batch_id, stats)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(doc_id) DO UPDATE SET
                 filename=excluded.filename, path=excluded.path, n_pages=excluded.n_pages,
                 pages_read=excluded.pages_read, status=excluded.status,
                 error=excluded.error, ingested_at=excluded.ingested_at,
                 extractor=excluded.extractor, batch_id=excluded.batch_id,
                 stats=excluded.stats""",
            (doc_id, filename, path, n_pages, pages_read, status, error, time.time(),
             extractor, batch_id, json.dumps(stats or {})),
        )
        self.conn.commit()

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def document_is_complete(self, doc_id: str) -> bool:
        """Resume support: has this exact file already been ingested successfully?"""
        doc = self.get_document(doc_id)
        return bool(doc and doc.get("status") == "extracted")

    def list_documents(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM documents ORDER BY ingested_at DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["stats"] = json.loads(d.get("stats") or "{}")
            d["claim_count"] = self.conn.execute(
                "SELECT COUNT(*) c FROM claims WHERE doc_id=?", (d["doc_id"],)).fetchone()["c"]
            out.append(d)
        return out

    def delete_document(self, doc_id: str) -> None:
        self.conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))
        self.conn.commit()

    # ---------------- claims ----------------
    def insert_claims(self, claims: Iterable[GroundedClaim], embeddings) -> None:
        rows = []
        for i, gc in enumerate(claims):
            c, g, cf = gc.claim, gc.grounding, gc.confidence
            emb = None
            if embeddings is not None and len(embeddings) > i:
                emb = to_blob(embeddings[i])
            rows.append({
                "claim_id": gc.claim_id, "doc_id": gc.doc_id,
                "subject": c.subject, "predicate": c.predicate, "value": c.value,
                "value_num": c.value_num, "unit": c.unit,
                "ctx_period": c.context.period, "ctx_unit": c.context.unit,
                "ctx_scope": c.context.scope, "ctx_basis": c.context.basis,
                "ctx_geography": c.context.geography, "ctx_as_of": c.context.as_of,
                "ctx_denominator": c.context.denominator,
                "ctx_other": c.context.other_qualifiers,
                "asserted_by": c.asserted_by, "modality": c.modality,
                "origin": c.origin, "extraction_rule": c.extraction_rule,
                "source_document": c.source_document,
                "span_page": c.source_span.page, "span_text": c.source_span.text,
                "extraction_confidence": cf.extraction,
                "grounding_confidence": cf.grounding,
                "final_confidence": cf.combined,
                "grounded": int(g.located), "grounding_score": g.score,
                "grounding_page": g.page, "grounding_page_label": g.page_label,
                "matched_text": g.matched_text,
                "char_start": g.char_start, "char_end": g.char_end,
                "bbox": json.dumps(list(g.bbox)) if g.bbox else None,
                "page_shift": g.page_shift,
                "value_in_span": None if g.value_in_span is None else int(g.value_in_span),
                "grounding_note": g.note,
                "quarantined": int(gc.quarantined), "needs_review": int(gc.needs_review),
                "review_reasons": json.dumps(gc.review_reasons),
                "embedding": emb, "created_at": time.time(),
            })
        if not rows:
            return
        cols = ",".join(CLAIM_COLUMNS)
        marks = ",".join(f":{c}" for c in CLAIM_COLUMNS)
        self.conn.executemany(
            f"INSERT OR REPLACE INTO claims ({cols}) VALUES ({marks})", rows)
        self.conn.commit()

    def claim_row_to_dict(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d.pop("embedding", None)
        d["review_reasons"] = json.loads(d.get("review_reasons") or "[]")
        d["bbox"] = json.loads(d["bbox"]) if d.get("bbox") else None
        d["value_in_span"] = None if d["value_in_span"] is None else bool(d["value_in_span"])
        for flag in ("grounded", "needs_review", "quarantined"):
            d[flag] = bool(d.get(flag))
        return d

    def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
        return self.claim_row_to_dict(r) if r else None

    def get_claims(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(ids), 500):
            batch = ids[i:i + 500]
            marks = ",".join("?" * len(batch))
            for r in self.conn.execute(
                    f"SELECT * FROM claims WHERE claim_id IN ({marks})", batch):
                out[r["claim_id"]] = self.claim_row_to_dict(r)
        return out

    def query_claims(self, *, doc_id: str | None = None, needs_review: bool | None = None,
                     quarantined: bool | None = None, origin: str | None = None,
                     search: str | None = None, limit: int = 200, offset: int = 0
                     ) -> list[dict[str, Any]]:
        where, params = [], []
        if doc_id:
            where.append("doc_id = ?"); params.append(doc_id)
        if needs_review is not None:
            where.append("needs_review = ?"); params.append(int(needs_review))
        if quarantined is not None:
            where.append("quarantined = ?"); params.append(int(quarantined))
        if origin:
            where.append("origin = ?"); params.append(origin)
        if search:
            where.append("(subject LIKE ? OR predicate LIKE ? OR value LIKE ?)")
            like = f"%{search}%"; params += [like, like, like]
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = self.conn.execute(
            f"SELECT * FROM claims {clause} ORDER BY final_confidence DESC LIMIT ? OFFSET ?",
            (*params, limit, offset)).fetchall()
        return [self.claim_row_to_dict(r) for r in rows]

    def count_claims(self, **kw) -> int:
        where, params = [], []
        for key, col in (("doc_id", "doc_id"), ("needs_review", "needs_review"),
                         ("quarantined", "quarantined"), ("origin", "origin")):
            if kw.get(key) is not None:
                value = kw[key]
                where.append(f"{col} = ?")
                params.append(int(value) if isinstance(value, bool) else value)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        return self.conn.execute(
            f"SELECT COUNT(*) c FROM claims {clause}", params).fetchone()["c"]

    def all_embeddings(self, exclude_doc: str | None = None):
        sql = ("SELECT claim_id, doc_id, embedding FROM claims "
               "WHERE embedding IS NOT NULL AND quarantined = 0")
        params: tuple = ()
        if exclude_doc:
            sql += " AND doc_id != ?"
            params = (exclude_doc,)
        rows = self.conn.execute(sql, params).fetchall()
        if not rows:
            return [], [], np.zeros((0, 0), dtype=np.float32)
        ids = [r["claim_id"] for r in rows]
        docs = [r["doc_id"] for r in rows]
        mat = np.vstack([from_blob(r["embedding"]) for r in rows])
        return ids, docs, mat

    # ---------------- relations ----------------
    _RELATION_COLUMNS = (
        "relation_id", "claim_a_id", "claim_b_id", "kind", "decided_by", "rule_id",
        "similarity", "subject_similarity", "predicate_similarity",
        "match_confidence", "relationship_confidence", "confidence",
        "explanation", "reasoning_trace", "context_diff",
        "value_agreement", "value_delta", "created_at",
    )

    def insert_relations(self, relations: Iterable[Relation]) -> int:
        rows = [(
            r.relation_id, r.claim_a_id, r.claim_b_id, r.kind, r.decided_by, r.rule_id,
            r.similarity, r.subject_similarity, r.predicate_similarity,
            r.match_confidence, r.relationship_confidence, r.confidence,
            r.explanation, json.dumps(r.reasoning_trace), json.dumps(r.context_diff),
            r.value_agreement, json.dumps(r.value_delta) if r.value_delta else None,
            time.time(),
        ) for r in relations]
        if not rows:
            return 0
        cols = ",".join(self._RELATION_COLUMNS)
        marks = ",".join(["?"] * len(self._RELATION_COLUMNS))
        cur = self.conn.executemany(
            f"INSERT OR REPLACE INTO relations ({cols}) VALUES ({marks})", rows)
        self.conn.commit()
        return cur.rowcount

    def relation_row_to_dict(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["reasoning_trace"] = json.loads(d.get("reasoning_trace") or "[]")
        d["context_diff"] = json.loads(d.get("context_diff") or "{}")
        d["value_delta"] = json.loads(d["value_delta"]) if d.get("value_delta") else None
        return d

    def query_relations(self, *, kind: str | None = None, doc_id: str | None = None,
                        cross_document_only: bool = True, exclude_unrelated: bool = True,
                        limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        sql = """SELECT r.* FROM relations r
                 JOIN claims a ON a.claim_id = r.claim_a_id
                 JOIN claims b ON b.claim_id = r.claim_b_id"""
        where, params = [], []
        if kind:
            where.append("r.kind = ?"); params.append(kind)
        elif exclude_unrelated:
            where.append("r.kind != 'unrelated'")
        if cross_document_only:
            where.append("a.doc_id != b.doc_id")
        if doc_id:
            where.append("(a.doc_id = ? OR b.doc_id = ?)"); params += [doc_id, doc_id]
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY r.confidence DESC, r.similarity DESC LIMIT ? OFFSET ?"
        rows = self.conn.execute(sql, (*params, limit, offset)).fetchall()
        return [self.relation_row_to_dict(r) for r in rows]

    def relation_counts(self, cross_document_only: bool = True) -> dict[str, int]:
        sql = """SELECT r.kind, COUNT(*) c FROM relations r
                 JOIN claims a ON a.claim_id=r.claim_a_id
                 JOIN claims b ON b.claim_id=r.claim_b_id"""
        if cross_document_only:
            sql += " WHERE a.doc_id != b.doc_id"
        sql += " GROUP BY r.kind"
        return {r["kind"]: r["c"] for r in self.conn.execute(sql).fetchall()}


def row_to_claim(d: dict[str, Any]) -> Claim:
    """Rebuild the Claim object from a stored row."""
    return Claim(
        subject=d["subject"], predicate=d["predicate"], value=d["value"],
        value_num=d.get("value_num"), unit=d.get("unit"),
        context=ClaimContext(
            period=d.get("ctx_period"), unit=d.get("ctx_unit"), scope=d.get("ctx_scope"),
            basis=d.get("ctx_basis"), geography=d.get("ctx_geography"),
            as_of=d.get("ctx_as_of"), denominator=d.get("ctx_denominator"),
            other_qualifiers=d.get("ctx_other")),
        asserted_by=d.get("asserted_by"), modality=d.get("modality") or "reported",
        source_document=d["source_document"],
        source_span=SourceSpan(page=d["span_page"], text=d["span_text"]),
        origin=d.get("origin") or "sentence",
        extraction_rule=d.get("extraction_rule"),
        confidence=d.get("extraction_confidence", 0.5),
    )
