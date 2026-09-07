#!/usr/bin/env python
"""Preflight: confirm the LLM endpoint works and supports what the pipeline needs.

    python scripts/check_llm.py            # list models + structured-output smoke test
    python scripts/check_llm.py --model grok-4
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fkl.config import CONFIG  # noqa: E402
from fkl.llm import LLMClient, LLMNotConfigured  # noqa: E402

SMOKE_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "value": {"type": "string"},
                    "period": {"type": ["string", "null"]},
                },
                "required": ["subject", "value", "period"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    print(f"endpoint : {CONFIG.llm_base_url}")
    print(f"key env  : {CONFIG.llm_api_key_env}")
    print(f"model    : {args.model or CONFIG.llm_model}\n")

    try:
        client = LLMClient(model=args.model)
    except LLMNotConfigured as e:
        print(f"NOT CONFIGURED: {e}")
        return 2

    try:
        models = client.list_models()
        print(f"available models ({len(models)}):")
        for m in models:
            print(f"  - {m}")
    except Exception as e:
        print(f"could not list models: {type(e).__name__}: {e}")
        models = []

    print("\nstructured-output smoke test...")
    try:
        res = client.complete_json(
            system="You extract claims from text and reply with the given JSON schema.",
            user=("Text: 'Acme reported revenue of 12.4 million euros for the year "
                  "ended March 2024.' Extract every claim."),
            schema=SMOKE_SCHEMA, schema_name="smoke", max_tokens=2000,
        )
        print(f"  OK  model={res.model} tokens={res.input_tokens}/{res.output_tokens}")
        print(f"  parsed: {res.data}")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
        return 1

    print("\nReady. Run an ingest with:")
    print("  python scripts/ingest.py --max-pages 12 "
          "starter-datasets/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
