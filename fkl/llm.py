"""LLM access layer.

Targets xAI's Grok through its OpenAI-compatible Chat Completions endpoint.
Everything provider-specific is confined to this file and to config: the base
URL, the key's env var, and the model name are all settings, so pointing the
pipeline at a different OpenAI-compatible endpoint (or a local model) is a
config change rather than a code change.

Two entry points are all the rest of the system needs:
  complete_json  -- schema-constrained extraction and adjudication
  complete_text  -- free-form, currently unused but kept for debugging
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI
from openai import APIError, APIStatusError, APITimeoutError, RateLimitError

from .config import CONFIG

log = logging.getLogger("fkl.llm")


class LLMNotConfigured(RuntimeError):
    pass


@dataclass
class LLMResult:
    data: Any
    raw_text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


# Params some models reject. If the endpoint complains about one, we drop it and
# retry rather than failing the whole chunk.
_OPTIONAL_PARAMS = ("reasoning_effort", "temperature", "max_tokens")


class TokenBucket:
    """Paces requests against a tokens-per-minute ceiling.

    Free API tiers cap tokens per minute, and a single oversized request is
    rejected outright rather than queued. Estimating a request's cost before
    sending it -- and reconciling against the real usage afterwards -- keeps a
    long ingest running unattended instead of dying partway through.
    """

    def __init__(self, tokens_per_minute: int):
        self.tpm = tokens_per_minute
        self._lock = threading.Lock()
        self._window_start = time.monotonic()
        self._used = 0

    def acquire(self, estimate: int) -> None:
        if self.tpm <= 0:
            return
        estimate = min(estimate, self.tpm)
        while True:
            with self._lock:
                now = time.monotonic()
                if now - self._window_start >= 60.0:
                    self._window_start, self._used = now, 0
                if self._used + estimate <= self.tpm:
                    self._used += estimate
                    return
                wait = 60.0 - (now - self._window_start) + 0.25
            log.info("token budget reached; waiting %.1fs", wait)
            time.sleep(max(0.5, wait))

    def reconcile(self, estimate: int, actual: int) -> None:
        """Charge any tokens the estimate under-counted to the current window."""
        if self.tpm <= 0:
            return
        with self._lock:
            self._used += max(0, actual - estimate)


def estimate_tokens(text: str) -> int:
    """Rough char->token estimate. Only needs to be good enough to pace."""
    return max(1, len(text) // 3)


class LLMClient:
    def __init__(self, *, model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, timeout: float = 300.0,
                 bucket: "TokenBucket | None" = None):
        self.model = model or CONFIG.llm_model
        self.base_url = base_url or CONFIG.llm_base_url
        key = api_key or os.environ.get(CONFIG.llm_api_key_env) or ""
        if not key:
            raise LLMNotConfigured(
                f"No API key found. Set {CONFIG.llm_api_key_env} in the environment "
                f"(or pass api_key=). Endpoint: {self.base_url}"
            )
        self._client = OpenAI(api_key=key, base_url=self.base_url, timeout=timeout,
                              max_retries=0)  # retries handled below so we can log them
        self.bucket = bucket if bucket is not None else TokenBucket(CONFIG.tpm_limit)

    # ------------------------------------------------------------------
    def list_models(self) -> list[str]:
        return sorted(m.id for m in self._client.models.list().data)

    # ------------------------------------------------------------------
    def _request(self, messages: list[dict], *, response_format: dict | None,
                 max_tokens: int, temperature: float | None) -> Any:
        params: dict[str, Any] = {"model": self.model, "messages": messages}
        if response_format is not None:
            params["response_format"] = response_format
        if max_tokens:
            params["max_tokens"] = max_tokens
        if temperature is not None:
            params["temperature"] = temperature
        if CONFIG.reasoning_effort:
            params["reasoning_effort"] = CONFIG.reasoning_effort

        # Budget = what we send plus what we expect back.
        prompt_chars = sum(len(m.get("content") or "") for m in messages)
        estimate = (prompt_chars // 3) + CONFIG.expected_output_tokens

        attempts = 0
        while True:
            attempts += 1
            self.bucket.acquire(estimate)
            try:
                resp = self._client.chat.completions.create(**params)
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    self.bucket.reconcile(
                        estimate,
                        (getattr(usage, "prompt_tokens", 0) or 0)
                        + (getattr(usage, "completion_tokens", 0) or 0),
                    )
                return resp

            except (RateLimitError, APITimeoutError) as e:
                if attempts >= CONFIG.llm_max_retries:
                    raise
                delay = _retry_after(e) or min(60.0, 2 ** attempts) + random.uniform(0, 1.0)
                log.warning("%s; retrying in %.1fs (attempt %d)", type(e).__name__, delay, attempts)
                time.sleep(delay)

            except APIStatusError as e:
                msg = str(e).lower()
                # Rate-limit rejections are transient; wait out the window.
                if "rate_limit" in msg or "too large" in msg or "tokens per minute" in msg:
                    if attempts >= CONFIG.llm_max_retries:
                        raise
                    delay = _retry_after(e) or 20.0
                    log.warning("rate limited; waiting %.1fs (attempt %d)", delay, attempts)
                    time.sleep(delay)
                    continue
                # Otherwise drop a parameter the model does not accept and retry.
                dropped = False
                for prm in _OPTIONAL_PARAMS:
                    if prm in params and prm in msg:
                        params.pop(prm)
                        dropped = True
                        log.warning("endpoint rejected %r; retrying without it", prm)
                        break
                if not dropped or attempts >= CONFIG.llm_max_retries:
                    raise

            except APIError:
                if attempts >= CONFIG.llm_max_retries:
                    raise
                time.sleep(min(10.0, 2 ** attempts))

    # ------------------------------------------------------------------
    def complete_json(self, *, system: str, user: str, schema: dict, schema_name: str,
                      max_tokens: int | None = None, temperature: float | None = 0.0
                      ) -> LLMResult:
        """Schema-constrained completion.

        Uses OpenAI-compatible strict json_schema mode so the response validates
        by construction. Falls back to plain json_object mode if the endpoint
        rejects the schema, and finally to raw text -- a chunk that returns
        unusable output is recorded as a failure, never silently dropped.
        """
        response_format: dict = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        }
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        mt = max_tokens or CONFIG.max_tokens

        try:
            resp = self._request(messages, response_format=response_format,
                                 max_tokens=mt, temperature=temperature)
        except APIStatusError as e:
            if "json_schema" not in str(e).lower() and "response_format" not in str(e).lower():
                raise
            log.warning("endpoint rejected json_schema; falling back to json_object mode")
            resp = self._request(
                messages, response_format={"type": "json_object"},
                max_tokens=mt, temperature=temperature,
            )

        text = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        return LLMResult(
            data=_loads(text),
            raw_text=text,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=getattr(resp, "model", self.model),
        )

    def complete_text(self, *, system: str, user: str, max_tokens: int | None = None,
                      temperature: float | None = 0.0) -> LLMResult:
        resp = self._request(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format=None, max_tokens=max_tokens or CONFIG.max_tokens,
            temperature=temperature,
        )
        text = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        return LLMResult(data=text, raw_text=text,
                         input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                         output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                         model=getattr(resp, "model", self.model))


def _retry_after(exc: Exception) -> float | None:
    resp = getattr(exc, "response", None)
    header = getattr(resp, "headers", {}) or {}
    for key in ("retry-after", "x-ratelimit-reset-tokens"):
        raw = header.get(key)
        if not raw:
            continue
        try:
            return min(120.0, float(str(raw).rstrip("s")))
        except ValueError:
            continue
    return None


def _loads(text: str) -> Any:
    """Parse JSON, tolerating a fenced code block if the model wrapped it."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    stripped = text.strip()
    if stripped.startswith("```"):
        body = stripped.split("```", 2)
        if len(body) >= 2:
            inner = body[1]
            if inner.lstrip().lower().startswith("json"):
                inner = inner.lstrip()[4:]
            return json.loads(inner.strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        return json.loads(stripped[start:end + 1])
    raise json.JSONDecodeError("no JSON object found in response", text, 0)
