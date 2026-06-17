"""
pipeline/health_refresh_daemon.py
---------------------------------
Continuously refresh compute pool node health snapshots.

This daemon runs run_batch_pool.py in --health-only mode on an interval,
which rewrites output/compute_pool/health-latest.json for the dashboard.

USAGE:
  python pipeline/health_refresh_daemon.py
  python pipeline/health_refresh_daemon.py --interval-seconds 45
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _run_health_probe() -> int:
    cmd = [sys.executable, "pipeline/run_batch_pool.py", "--job", "jobs/what-is-nexetra-live-es.json", "--health-only"]
    result = subprocess.run(cmd, cwd=ROOT)
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh node health snapshots in a loop")
    parser.add_argument("--interval-seconds", type=int, default=60, help="Seconds between refresh runs")
    args = parser.parse_args()

    interval = max(15, args.interval_seconds)
    print(f"[health-refresh] starting with interval={interval}s")

    while True:
        started = datetime.now(timezone.utc).isoformat()
        print(f"[health-refresh] probe start {started}")
        rc = _run_health_probe()
        status = "ok" if rc == 0 else f"fail(rc={rc})"
        ended = datetime.now(timezone.utc).isoformat()
        print(f"[health-refresh] probe end {ended} status={status}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
