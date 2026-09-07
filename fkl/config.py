"""Central configuration. Everything is env-overridable; no secrets live in the repo."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Read .env into the environment if present. Real env vars always win, and
    .env is gitignored -- credentials never enter the repository."""
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()


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

    # --- LLM provider -------------------------------------------------
    # Defaults target Groq's OpenAI-compatible endpoint. All three are settings,
    # so any OpenAI-compatible endpoint (xAI, OpenAI, a local server) works
    # without a code change -- only this block is provider-specific.
    llm_base_url: str = _env("FKL_LLM_BASE_URL", "https://api.groq.com/openai/v1")
    llm_api_key_env: str = _env("FKL_LLM_API_KEY_ENV", "GROQ_API_KEY")
    llm_model: str = _env("FKL_LLM_MODEL", "openai/gpt-oss-120b")
    # Model used only for the small number of genuinely ambiguous comparisons.
    judge_model: str = _env("FKL_JUDGE_MODEL", _env("FKL_LLM_MODEL", "openai/gpt-oss-120b"))
    llm_max_retries: int = _env_int("FKL_LLM_MAX_RETRIES", 6)
    # Reasoning depth. "low" gave identical extractions at half the output
    # tokens in testing, which matters a lot under a tokens-per-minute cap.
    reasoning_effort: str = _env("FKL_REASONING_EFFORT", "low")
    # Tokens-per-minute ceiling to pace against. 0 disables pacing.
    tpm_limit: int = _env_int("FKL_TPM_LIMIT", 8000)
    expected_output_tokens: int = _env_int("FKL_EXPECTED_OUTPUT_TOKENS", 2000)

    # --- extraction ---
    max_tokens: int = _env_int("FKL_MAX_TOKENS", 6000)
    # Characters of page text per LLM extraction call.
    chunk_chars: int = _env_int("FKL_CHUNK_CHARS", 9000)
    extract_concurrency: int = _env_int("FKL_EXTRACT_CONCURRENCY", 2)

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
    # Ceiling on LLM adjudications per run. Escalation is meant to be the
    # exception; a run that wants to escalate everything is a signal the rules
    # need work, not a reason to spend the token budget.
    max_escalations: int = _env_int("FKL_MAX_ESCALATIONS", 40)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


CONFIG = Config()
