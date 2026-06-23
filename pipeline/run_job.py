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
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    from pipeline.run_history import append_run_event
    from pipeline.job_control import validate_job_path
    from pipeline.handler_registry import get_handler
    from pipeline.execution import (
        RunManifest,
        ensure_executable_job,
        evaluate_stage_artifacts,
        evaluate_success_criteria,
        make_stage_result,
    )
except ModuleNotFoundError:
    from run_history import append_run_event
    from job_control import validate_job_path
    from handler_registry import get_handler
    from execution import (
        RunManifest,
        ensure_executable_job,
        evaluate_stage_artifacts,
        evaluate_success_criteria,
        make_stage_result,
    )

ROOT = Path(__file__).resolve().parent.parent


def run_stage(
    stage_name: str,
    script_parts: list[str],
    job_path: str,
    job: dict,
    dry_run: bool,
    run_id: str,
    output_dir: Path,
    provenance: dict,
) -> dict:
    cmd = [sys.executable, *script_parts, "--job", job_path]
    if dry_run:
        cmd.append("--dry-run")

    print(f"\n=== Stage: {stage_name} ===")
    started_at = time.time()
    append_run_event({
        "job": job_path,
        "run_id": run_id,
        "event": "stage_start",
        "stage": stage_name,
        "host": "local",
        "runner": "run_job",
        "mode": "local",
        **provenance,
    })
    injected_failure = os.environ.get("NEXETRA_INJECT_STAGE_FAILURE")
    if injected_failure == stage_name:
        error = f"Injected failure for diagnostic gate: {stage_name}"
        stage_result = make_stage_result(
            stage=stage_name,
            status="failed",
            host="local",
            returncode=97,
            started_at=started_at,
            outputs=[],
            error=error,
        )
        append_run_event({
            "job": job_path,
            "run_id": run_id,
            "event": "stage_fail",
            "stage": stage_name,
            "host": "local",
            "runner": "run_job",
            "mode": "local",
            "detail": error,
            "duration_sec": stage_result["duration_seconds"],
            **provenance,
        })
        print(f"Stage failed: {stage_name}")
        return stage_result
    environment = os.environ.copy()
    environment["NEXETRA_JOB_OUTPUT_DIR"] = str(output_dir)
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    artifact_check = (
        evaluate_stage_artifacts(job, stage_name, output_dir=output_dir, allow_stubs=dry_run)
        if result.returncode == 0
        else {"passed": False, "outputs": [], "missing": [], "validation": []}
    )
    passed = result.returncode == 0 and artifact_check["passed"]
    error = ""
    if result.returncode != 0:
        error = (result.stderr or result.stdout or f"exit={result.returncode}")[-4000:]
    elif artifact_check["missing"]:
        error = f"Missing required stage artifacts: {artifact_check['missing']}"
    stage_result = make_stage_result(
        stage=stage_name,
        status="passed" if passed else "failed",
        host="local",
        returncode=result.returncode,
        started_at=started_at,
        outputs=artifact_check.get("outputs", []),
        error=error,
    )
    print(f"Stage {'passed' if passed else 'failed'}: {stage_name}")
    append_run_event({
        "job": job_path,
        "run_id": run_id,
        "event": "stage_pass" if passed else "stage_fail",
        "stage": stage_name,
        "host": "local",
        "runner": "run_job",
        "mode": "local",
        "detail": error or f"exit={result.returncode}",
        "duration_sec": stage_result["duration_seconds"],
        **provenance,
    })
    return stage_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexetra Media pipeline runner")
    parser.add_argument("--job", required=True, help="Path to job JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Run all stages in dry-run mode")
    parser.add_argument("--validate-only", action="store_true", help="Validate JCL and exit without running stages")
    args = parser.parse_args()

    job_path = Path(args.job)
    if not job_path.is_absolute():
        job_path = ROOT / job_path
    validation = validate_job_path(job_path)
    if args.validate_only or not validation["valid"]:
        print(json.dumps(validation, indent=2, ensure_ascii=False))
    if not validation["valid"]:
        sys.exit(2)
    if args.validate_only:
        return

    job = json.loads(job_path.read_text(encoding="utf-8"))
    try:
        ensure_executable_job(job)
    except ValueError as exc:
        print(json.dumps({"valid": True, "executable": False, "job_id": job.get("job_id"), "error": str(exc)}, indent=2))
        sys.exit(3)

    manifest = RunManifest(job, args.job, runner="run_job", dry_run=args.dry_run)
    provenance = manifest.data["provenance"]
    handler = get_handler(job["job_type"])
    if handler is None:
        print(json.dumps({"valid": True, "executable": False, "job_id": job.get("job_id"), "error": f"No handler for {job.get('job_type')}"}, indent=2))
        sys.exit(3)

    append_run_event({
        "job": args.job,
        "run_id": manifest.run_id,
        "event": "batch_start",
        "stage": "",
        "host": "local",
        "runner": "run_job",
        "mode": "local",
        "dry_run": args.dry_run,
        **provenance,
    })

    started_at = time.time()
    failure = ""
    try:
        for stage in handler.stages:
            result = run_stage(
                stage.name,
                [stage.script],
                job_path=str(job_path),
                job=job,
                dry_run=args.dry_run,
                run_id=manifest.run_id,
                output_dir=manifest.artifact_root,
                provenance=provenance,
            )
            manifest.add_stage(result)
            if result["status"] != "passed":
                failure = result["error"] or f"Stage failed: {stage.name}"
                break

        evaluation = evaluate_success_criteria(job, output_dir=manifest.artifact_root, allow_stubs=args.dry_run)
        if not failure and not evaluation["passed"]:
            failure = f"Success criteria failed: missing={evaluation['missing']} empty={evaluation['empty']}"

        if failure:
            manifest.finalize("failed", error=failure, evaluation=evaluation)
            append_run_event({
                "job": args.job,
                "run_id": manifest.run_id,
                "event": "batch_fail",
                "stage": "",
                "host": "local",
                "runner": "run_job",
                "mode": "local",
                "detail": failure,
                "duration_sec": round(time.time() - started_at, 3),
                **provenance,
            })
            sys.exit(1)

        manifest.finalize("done", evaluation=evaluation)
        print("\nPipeline completed successfully.")
        append_run_event({
            "job": args.job,
            "run_id": manifest.run_id,
            "event": "batch_done",
            "stage": "",
            "host": "local",
            "runner": "run_job",
            "mode": "local",
            "detail": "all stages and success criteria passed",
            "duration_sec": round(time.time() - started_at, 3),
            **provenance,
        })
    except SystemExit:
        raise
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        manifest.finalize("failed", error=detail)
        append_run_event({
            "job": args.job,
            "run_id": manifest.run_id,
            "event": "batch_fail",
            "stage": "",
            "host": "local",
            "runner": "run_job",
            "mode": "local",
            "detail": detail,
            **provenance,
        })
        raise


if __name__ == "__main__":
    main()
