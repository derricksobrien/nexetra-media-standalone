"""
pipeline/run_stress_suite.py
----------------------------
Run a predefined multi-job stress suite and print a summary.

USAGE:
  python pipeline/run_stress_suite.py
  python pipeline/run_stress_suite.py --dry-run
  python pipeline/run_stress_suite.py --jobs jobs/stress-thunderbolt-a.json jobs/stress-thunderbolt-b.json
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

DEFAULT_JOBS = [
    "jobs/stress-thunderbolt-a.json",
    "jobs/stress-thunderbolt-b.json",
    "jobs/stress-thunderbolt-c.json",
]


def run_job(job_path: str, dry_run: bool) -> tuple[bool, float]:
    cmd = [sys.executable, "pipeline/run_job.py", "--job", job_path]
    if dry_run:
        cmd.append("--dry-run")

    print(f"\n=== STRESS JOB START: {job_path} ===")
    started_at = time.time()
    result = subprocess.run(cmd, cwd=ROOT)
    elapsed = time.time() - started_at
    ok = result.returncode == 0
    print(f"=== STRESS JOB {'PASS' if ok else 'FAIL'}: {job_path} ({elapsed:.1f}s) ===")
    return ok, elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Nexetra stress suite")
    parser.add_argument("--dry-run", action="store_true", help="Run all jobs with --dry-run")
    parser.add_argument("--jobs", nargs="*", default=DEFAULT_JOBS, help="Override job list")
    args = parser.parse_args()

    total_started = time.time()
    rows: list[tuple[str, bool, float]] = []

    for job in args.jobs:
        ok, seconds = run_job(job, dry_run=args.dry_run)
        rows.append((job, ok, seconds))

    print("\n=== STRESS SUITE SUMMARY ===")
    for job, ok, seconds in rows:
        print(f"- {'PASS' if ok else 'FAIL'} | {seconds:7.1f}s | {job}")

    total_seconds = time.time() - total_started
    passed = sum(1 for _, ok, _ in rows if ok)
    print(f"Total: {passed}/{len(rows)} passed in {total_seconds:.1f}s")

    if passed != len(rows):
        sys.exit(1)


if __name__ == "__main__":
    main()
