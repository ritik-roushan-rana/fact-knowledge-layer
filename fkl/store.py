"""SQLite persistence.

One file, no server, no migrations framework. Claims carry both the model's
proposal and the pipeline's verification so any row can be audited on its own.
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
from .models import Claim, ClaimContext, GroundedClaim, Grounding, Relation, SourceSpan

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
    stats        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id              TEXT PRIMARY KEY,
    doc_id                TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    subject               TEXT NOT NULL,
    predicate             TEXT NOT NULL,
    value                 TEXT NOT NULL,
    ctx_period            TEXT,
    ctx_unit              TEXT,
    ctx_scope             TEXT,
    ctx_other             TEXT,
    source_document       TEXT NOT NULL,
    span_page             INTEGER NOT NULL,
    span_text             TEXT NOT NULL,
    extraction_confidence REAL NOT NULL,
    grounded              INTEGER NOT NULL,
    grounding_score       REAL NOT NULL,
    grounding_page        INTEGER,
    grounding_page_label  TEXT,
    matched_text          TEXT,
    char_start            INTEGER,
    char_end              INTEGER,
    page_shift            INTEGER,
    value_in_span         INTEGER,
    grounding_note        TEXT,
    final_confidence      REAL NOT NULL,
    needs_review          INTEGER NOT NULL,
    review_reasons        TEXT NOT NULL DEFAULT '[]',
    embedding             BLOB,
    created_at            REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_doc ON claims(doc_id);
CREATE INDEX IF NOT EXISTS idx_claims_review ON claims(needs_review);

CREATE TABLE IF NOT EXISTS relations (
    relation_id     TEXT PRIMARY KEY,
    claim_a_id      TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    claim_b_id      TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    decided_by      TEXT NOT NULL,
    similarity      REAL NOT NULL,
    confidence      REAL NOT NULL,
    explanation     TEXT NOT NULL,
    reasoning_trace TEXT NOT NULL DEFAULT '[]',
    context_diff    TEXT NOT NULL DEFAULT '{}',
    value_agreement TEXT,
    created_at      REAL NOT NULL,
    UNIQUE (claim_a_id, claim_b_id)
);
CREATE INDEX IF NOT EXISTS idx_rel_kind ON relations(kind);
CREATE INDEX IF NOT EXISTS idx_rel_a ON relations(claim_a_id);
CREATE INDEX IF NOT EXISTS idx_rel_b ON relations(claim_b_id);
"""


class Store:
    def __init__(self, db_path: Path | str | None = None):
        CONFIG.ensure_dirs()
        self.db_path = Path(db_path or CONFIG.db_path)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------------- documents ----------------
    def upsert_document(self, *, doc_id: str, filename: str, path: str, n_pages: int,
                        pages_read: int, status: str, error: str | None = None,
                        stats: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            """INSERT INTO documents (doc_id, filename, path, n_pages, pages_read, status,
                                      error, ingested_at, stats)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(doc_id) DO UPDATE SET
                 filename=excluded.filename, path=excluded.path, n_pages=excluded.n_pages,
                 pages_read=excluded.pages_read, status=excluded.status,
                 error=excluded.error, ingested_at=excluded.ingested_at, stats=excluded.stats""",
            (doc_id, filename, path, n_pages, pages_read, status, error, time.time(),
             json.dumps(stats or {})),
        )
        self.conn.commit()

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def list_documents(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM documents ORDER BY ingested_at DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["stats"] = json.loads(d.get("stats") or "{}")
            d["claim_count"] = self.conn.execute(
                "SELECT COUNT(*) c FROM claims WHERE doc_id=?", (d["doc_id"],)
            ).fetchone()["c"]
            out.append(d)
        return out

    def delete_document(self, doc_id: str) -> None:
        self.conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))
        self.conn.commit()

    # ---------------- claims ----------------
    def insert_claims(self, claims: Iterable[GroundedClaim], embeddings: np.ndarray) -> None:
        rows = []
        for i, gc in enumerate(claims):
            c, g = gc.claim, gc.grounding
            rows.append((
                gc.claim_id, gc.doc_id, c.subject, c.predicate, c.value,
                c.context.period, c.context.unit, c.context.scope, c.context.other_qualifiers,
                c.source_document, c.source_span.page, c.source_span.text, c.confidence,
                int(g.located), g.score, g.page, g.page_label, g.matched_text,
                g.char_start, g.char_end, g.page_shift,
                None if g.value_in_span is None else int(g.value_in_span),
                g.note, gc.final_confidence, int(gc.needs_review),
                json.dumps(gc.review_reasons),
                to_blob(embeddings[i]) if len(embeddings) > i else None,
                time.time(),
            ))
        self.conn.executemany(
            """INSERT OR REPLACE INTO claims VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        self.conn.commit()

    def claim_row_to_dict(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d.pop("embedding", None)
        d["review_reasons"] = json.loads(d.get("review_reasons") or "[]")
        d["value_in_span"] = None if d["value_in_span"] is None else bool(d["value_in_span"])
        d["grounded"] = bool(d["grounded"])
        d["needs_review"] = bool(d["needs_review"])
        return d

    def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
        return self.claim_row_to_dict(r) if r else None

    def get_claims(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(f"SELECT * FROM claims WHERE claim_id IN ({marks})", ids).fetchall()
        return {r["claim_id"]: self.claim_row_to_dict(r) for r in rows}

    def query_claims(self, *, doc_id: str | None = None, needs_review: bool | None = None,
                     search: str | None = None, limit: int = 200, offset: int = 0
                     ) -> list[dict[str, Any]]:
        where, params = [], []
        if doc_id:
            where.append("doc_id = ?")
            params.append(doc_id)
        if needs_review is not None:
            where.append("needs_review = ?")
            params.append(int(needs_review))
        if search:
            where.append("(subject LIKE ? OR predicate LIKE ? OR value LIKE ?)")
            like = f"%{search}%"
            params += [like, like, like]
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = self.conn.execute(
            f"SELECT * FROM claims {clause} ORDER BY final_confidence DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [self.claim_row_to_dict(r) for r in rows]

    def count_claims(self, **kw) -> int:
        return len(self.query_claims(limit=10**9, **kw))

    def all_embeddings(self, exclude_doc: str | None = None
                       ) -> tuple[list[str], list[str], np.ndarray]:
        """Return (claim_ids, doc_ids, matrix) for every claim that has a vector."""
        sql = "SELECT claim_id, doc_id, embedding FROM claims WHERE embedding IS NOT NULL"
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
    def insert_relations(self, relations: Iterable[Relation]) -> int:
        rows = [(
            r.relation_id, r.claim_a_id, r.claim_b_id, r.kind, r.decided_by, r.similarity,
            r.confidence, r.explanation, json.dumps(r.reasoning_trace),
            json.dumps(r.context_diff), r.value_agreement, time.time(),
        ) for r in relations]
        if not rows:
            return 0
        cur = self.conn.executemany(
            "INSERT OR REPLACE INTO relations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows
        )
        self.conn.commit()
        return cur.rowcount

    def relation_row_to_dict(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["reasoning_trace"] = json.loads(d.get("reasoning_trace") or "[]")
        d["context_diff"] = json.loads(d.get("context_diff") or "{}")
        return d

    def query_relations(self, *, kind: str | None = None, doc_id: str | None = None,
                        cross_document_only: bool = True, limit: int = 200,
                        offset: int = 0) -> list[dict[str, Any]]:
        sql = """SELECT r.* FROM relations r
                 JOIN claims a ON a.claim_id = r.claim_a_id
                 JOIN claims b ON b.claim_id = r.claim_b_id"""
        where, params = [], []
        if kind:
            where.append("r.kind = ?")
            params.append(kind)
        if cross_document_only:
            where.append("a.doc_id != b.doc_id")
        if doc_id:
            where.append("(a.doc_id = ? OR b.doc_id = ?)")
            params += [doc_id, doc_id]
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

    def existing_pairs_for(self, claim_ids: list[str]) -> set[tuple[str, str]]:
        if not claim_ids:
            return set()
        marks = ",".join("?" * len(claim_ids))
        rows = self.conn.execute(
            f"SELECT claim_a_id, claim_b_id FROM relations "
            f"WHERE claim_a_id IN ({marks}) OR claim_b_id IN ({marks})",
            claim_ids + claim_ids,
        ).fetchall()
        return {(r["claim_a_id"], r["claim_b_id"]) for r in rows}


def row_to_claim(d: dict[str, Any]) -> Claim:
    """Rebuild the assignment-shaped Claim from a stored row."""
    return Claim(
        subject=d["subject"], predicate=d["predicate"], value=d["value"],
        context=ClaimContext(
            period=d["ctx_period"], unit=d["ctx_unit"],
            scope=d["ctx_scope"], other_qualifiers=d["ctx_other"],
        ),
        source_document=d["source_document"],
        source_span=SourceSpan(page=d["span_page"], text=d["span_text"]),
        confidence=d["extraction_confidence"],
    )
