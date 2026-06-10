"""
pipeline/run_job.py
-------------------
Single entrypoint to run the media pipeline sequentially for one job.

Stages:
1) scriptgen
2) translate
3) tts
4) assembly
5) export

USAGE:
    python pipeline/run_job.py --job jobs/what-is-nexetra.json --dry-run
    python pipeline/run_job.py --job jobs/what-is-nexetra.json
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

try:
    from pipeline.run_history import append_run_event
except ModuleNotFoundError:
    from run_history import append_run_event

ROOT = Path(__file__).resolve().parent.parent


STAGES = [
    ("scriptgen", ["pipeline/scriptgen/agent.py"]),
    ("translate", ["pipeline/translate/agent.py"]),
    ("tts", ["pipeline/tts/agent.py"]),
    ("assembly", ["pipeline/assembly/agent.py"]),
    ("export", ["pipeline/export/agent.py"]),
]


def run_stage(stage_name: str, script_parts: list[str], job: str, dry_run: bool) -> bool:
    cmd = [sys.executable, *script_parts, "--job", job]
    if dry_run:
        cmd.append("--dry-run")

    print(f"\n=== Stage: {stage_name} ===")
    started_at = time.time()
    append_run_event({
        "job": job,
        "event": "stage_start",
        "stage": stage_name,
        "host": "local",
        "runner": "run_job",
        "mode": "local",
    })
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print(f"Stage failed: {stage_name} (exit={result.returncode})")
        append_run_event({
            "job": job,
            "event": "stage_fail",
            "stage": stage_name,
            "host": "local",
            "runner": "run_job",
            "mode": "local",
            "detail": f"exit={result.returncode}",
            "duration_sec": round(time.time() - started_at, 3),
        })
        return False

    print(f"Stage passed: {stage_name}")
    append_run_event({
        "job": job,
        "event": "stage_pass",
        "stage": stage_name,
        "host": "local",
        "runner": "run_job",
        "mode": "local",
        "detail": f"exit={result.returncode}",
        "duration_sec": round(time.time() - started_at, 3),
    })
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexetra Media pipeline runner")
    parser.add_argument("--job", required=True, help="Path to job JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Run all stages in dry-run mode")
    args = parser.parse_args()

    append_run_event({
        "job": args.job,
        "event": "batch_start",
        "stage": "",
        "host": "local",
        "runner": "run_job",
        "mode": "local",
        "dry_run": args.dry_run,
    })

    started_at = time.time()

    for stage_name, script_parts in STAGES:
        ok = run_stage(stage_name, script_parts, job=args.job, dry_run=args.dry_run)
        if not ok:
            append_run_event({
                "job": args.job,
                "event": "batch_fail",
                "stage": "",
                "host": "local",
                "runner": "run_job",
                "mode": "local",
                "duration_sec": round(time.time() - started_at, 3),
            })
            sys.exit(1)

    print("\nPipeline completed successfully.")
    append_run_event({
        "job": args.job,
        "event": "batch_done",
        "stage": "",
        "host": "local",
        "runner": "run_job",
        "mode": "local",
        "detail": "all stages passed",
        "duration_sec": round(time.time() - started_at, 3),
    })


if __name__ == "__main__":
    main()
