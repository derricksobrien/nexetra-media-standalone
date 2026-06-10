"""
pipeline/run_history.py
-----------------------
Shared append-only run ledger for Nexetra Media pipeline executions.

Each event is stored in output/job_runs.jsonl so the dashboard can show an
archive of runs, stage timings, and metadata from both local and pool runs.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_LOG = REPO_ROOT / "output" / "job_runs.jsonl"


def append_run_event(payload: dict[str, Any]) -> None:
    """Append one JSON event line to the shared run ledger."""
    try:
        RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
        event = {"ts": time.time(), **payload}
        with RUNS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass
