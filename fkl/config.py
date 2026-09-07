"""Central configuration. Everything is env-overridable; no secrets live in the repo."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Config:
    # --- storage ---
    data_dir: Path = Path(_env("FKL_DATA_DIR", str(REPO_ROOT / "data")))
    db_path: Path = Path(_env("FKL_DB_PATH", str(REPO_ROOT / "data" / "fkl.db")))
    upload_dir: Path = Path(_env("FKL_UPLOAD_DIR", str(REPO_ROOT / "data" / "uploads")))

    # --- extraction ---
    extract_model: str = _env("FKL_EXTRACT_MODEL", "claude-opus-5")
    judge_model: str = _env("FKL_JUDGE_MODEL", "claude-opus-5")
    effort: str = _env("FKL_EFFORT", "medium")
    max_tokens: int = _env_int("FKL_MAX_TOKENS", 16000)
    # Characters of page text per LLM extraction call.
    chunk_chars: int = _env_int("FKL_CHUNK_CHARS", 8000)
    extract_concurrency: int = _env_int("FKL_EXTRACT_CONCURRENCY", 6)

    # --- grounding ---
    # Minimum fuzzy similarity (0-1) for a proposed quote to count as located.
    grounding_threshold: float = _env_float("FKL_GROUNDING_THRESHOLD", 0.80)
    # Claims below this final confidence land on the "needs review" list.
    review_threshold: float = _env_float("FKL_REVIEW_THRESHOLD", 0.60)

    # --- embeddings / matching ---
    embed_model: str = _env("FKL_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    # Cosine similarity floor for two claims to be considered "about the same thing".
    match_threshold: float = _env_float("FKL_MATCH_THRESHOLD", 0.62)
    # Max candidate partners considered per claim (keeps incremental ingest cheap).
    match_top_k: int = _env_int("FKL_MATCH_TOP_K", 15)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


CONFIG = Config()
