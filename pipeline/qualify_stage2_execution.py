"""Run and persist Stage 2 truthful-execution qualification gates."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    from pipeline.capture_stage0_baseline import DEFAULT_DASHBOARD_URL, upload_report
    from pipeline.execution import RunManifest, evaluate_success_criteria, load_run_manifests, mark_abandoned_runs
    from pipeline.job_control import ROOT, load_job
except ModuleNotFoundError:
    from capture_stage0_baseline import DEFAULT_DASHBOARD_URL, upload_report
    from execution import RunManifest, evaluate_success_criteria, load_run_manifests, mark_abandoned_runs
    from job_control import ROOT, load_job


def _run(command: list[str], timeout: int = 180) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout[-12000:],
        "stderr": result.stderr[-12000:],
    }


def _check(check_id: str, passed: bool, detail: str) -> dict[str, str]:
    return {"id": check_id, "status": "pass" if passed else "fail", "detail": detail}


def _wsl_root() -> str:
    drive = ROOT.drive.rstrip(":").lower()
    suffix = ROOT.as_posix().split(":", 1)[1]
    return f"/mnt/{drive}{suffix}"


def build_report(wsl_passed: bool = False) -> dict[str, Any]:
    captured = dt.datetime.now(dt.timezone.utc)
    checks: list[dict[str, str]] = []
    evidence: dict[str, Any] = {}

    local_tests = _run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], timeout=180)
    evidence["local_tests"] = local_tests
    checks.append(_check("local_test_suite", local_tests["returncode"] == 0, f"returncode={local_tests['returncode']}"))

    checks.append(_check(
        "wsl_ubuntu_test_suite",
        wsl_passed,
        "passed as separate WSL promotion command" if wsl_passed else "required: run WSL suite, then use --wsl-passed",
    ))

    dry_run = _run([
        sys.executable,
        "pipeline/run_job.py",
        "--job",
        "tests/fixtures/jobs/media-en-es-baseline.json",
        "--dry-run",
    ], timeout=180)
    evidence["local_dry_run"] = dry_run
    checks.append(_check("local_en_es_dry_run", dry_run["returncode"] == 0, f"returncode={dry_run['returncode']}"))

    manifests = load_run_manifests()
    latest = next((item for item in manifests if item.get("job_id") == "stage0-media-en-es-baseline"), None)
    manifest_ok = bool(
        latest
        and latest.get("status") == "done"
        and len([stage for stage in latest.get("stages", []) if stage.get("status") == "passed"]) == 5
        and latest.get("success_evaluation", {}).get("passed")
    )
    checks.append(_check("terminal_manifest", manifest_ok, f"run_id={(latest or {}).get('run_id', 'missing')}"))
    checks.append(_check("unique_run_id", bool(latest and latest.get("run_id")), f"run_id={(latest or {}).get('run_id', 'missing')}"))

    base = load_job(ROOT / "jobs" / "what-is-nexetra-live-es.json")
    with tempfile.TemporaryDirectory() as tmp:
        missing = evaluate_success_criteria(base, output_dir=Path(tmp))
    checks.append(_check("missing_artifact_fails", not missing["passed"] and bool(missing["missing"]), f"missing={len(missing['missing'])}"))

    unsupported_results = []
    for runner in ("pipeline/run_job.py", "pipeline/run_batch_pool.py"):
        result = _run([sys.executable, runner, "--job", "jobs/ma-product-launch-brief-v1.json", "--dry-run"], timeout=60)
        unsupported_results.append(result)
        checks.append(_check(f"unsupported_handler_{Path(runner).stem}", result["returncode"] == 3, f"returncode={result['returncode']}"))
    evidence["unsupported_handler"] = unsupported_results

    with tempfile.TemporaryDirectory() as tmp:
        runs_dir = Path(tmp)
        stale = RunManifest(base, str(ROOT / "jobs" / "what-is-nexetra-live-es.json"), "qualification", True, runs_dir=runs_dir)
        stale.data["heartbeat_ts"] = time.time() - 100
        stale._write()
        abandoned = mark_abandoned_runs(runs_dir=runs_dir, timeout_seconds=10)
    checks.append(_check("stale_run_abandoned", abandoned[0]["status"] == "abandoned", abandoned[0]["status"]))

    passed_count = sum(1 for item in checks if item["status"] == "pass")
    return {
        "schema_version": 1,
        "stage": "stage-2",
        "checkpoint_id": captured.strftime("%Y%m%d-%H%M%S"),
        "captured_at": captured.isoformat(),
        "status": "pass" if passed_count == len(checks) else "fail",
        "summary": {
            "checks_total": len(checks),
            "checks_passed": passed_count,
            "checks_failed": len(checks) - passed_count,
            "latest_run_id": (latest or {}).get("run_id", ""),
        },
        "checks": checks,
        "evidence": evidence,
        "latest_manifest": latest,
    }


def write_report(report: dict[str, Any]) -> tuple[Path, Path]:
    stage_root = ROOT / "output" / "qualification" / "stage-2"
    destination = stage_root / report["checkpoint_id"]
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "qualification.json"
    markdown_path = destination / "qualification.md"
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    json_path.write_text(encoded, encoding="utf-8")
    lines = [
        "# Stage 2 Truthful Execution Qualification",
        "",
        f"- Captured: {report['captured_at']}",
        f"- Status: **{report['status'].upper()}**",
        f"- Checks: {report['summary']['checks_passed']}/{report['summary']['checks_total']}",
        f"- Latest run: `{report['summary']['latest_run_id']}`",
        "",
        "| Check | Status | Detail |",
        "|---|---|---|",
    ]
    for item in report["checks"]:
        lines.append(f"| {item['id']} | {item['status']} | {item['detail'].replace('|', '\\|')} |")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (stage_root / "latest.json").write_text(encoded, encoding="utf-8")
    (stage_root / "latest.md").write_text(markdown_path.read_text(encoding="utf-8"), encoding="utf-8")
    return json_path, markdown_path


def main() -> None:
    upload = "--upload" in sys.argv
    wsl_passed = "--wsl-passed" in sys.argv
    report = build_report(wsl_passed=wsl_passed)
    json_path, markdown_path = write_report(report)
    result: dict[str, Any] = {
        "status": report["status"],
        "json": str(json_path),
        "markdown": str(markdown_path),
        "summary": report["summary"],
    }
    if upload:
        result["upload"] = upload_report(report, DEFAULT_DASHBOARD_URL)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
