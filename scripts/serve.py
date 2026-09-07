#!/usr/bin/env python
"""Run the API + UI:  python scripts/serve.py  ->  http://127.0.0.1:8000"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("fkl.api:app", host="127.0.0.1", port=8000, reload=False)
