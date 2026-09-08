#!/usr/bin/env bash
# Twenty-second smoke path: ingest one small PDF plus one bigger one, wire the
# UI, and open the browser. Useful for reviewers who want to see the system
# work without waiting on the full 511-page corpus (~90 s).
#
# Usage:  ./scripts/quickstart.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
    echo "→ Creating .venv and installing requirements (one-time)..."
    python3 -m venv .venv
    .venv/bin/pip install -q -r requirements.txt
fi

Q4="starter-datasets/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf"
IMF="starter-datasets/india-macroeconomy/03-imf-india-2025-article-iv-excerpt.pdf"
if [ ! -f "$Q4" ] || [ ! -f "$IMF" ]; then
    echo "starter PDFs missing. Restore starter-datasets/ before running quickstart." >&2
    exit 1
fi

echo "→ Ingesting the Delhivery Q4 deck (~2 s) and the IMF Article IV (~15 s)..."
.venv/bin/python scripts/ingest.py "$Q4" "$IMF"

cat <<'EOF'

─────────────────────────────────────────────────────────────────────────
  Ingest done. Starting the API + UI at http://127.0.0.1:8000
  Ctrl+C to stop. To ingest the whole starter corpus:
      .venv/bin/python scripts/ingest.py starter-datasets/*/*.pdf
─────────────────────────────────────────────────────────────────────────

EOF

exec .venv/bin/python scripts/serve.py
