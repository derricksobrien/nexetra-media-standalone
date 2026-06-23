"""Run and persist Stage 5A content-development qualification gates."""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from pipeline.capture_stage0_baseline import DEFAULT_DASHBOARD_URL, upload_report
    from pipeline.execution import load_run_manifests
    from pipeline.handler_registry import get_handler
    from pipeline.job_control import ROOT, load_job, validate_job_document
except ModuleNotFoundError:
    from capture_stage0_baseline import DEFAULT_DASHBOARD_URL, upload_report
    from execution import load_run_manifests
    from handler_registry import get_handler
    from job_control import ROOT, load_job, validate_job_document


CONTENT_STAGES = ["plan", "draft", "variants", "review", "package"]


def _check(check_id: str, passed: bool, detail: str) -> dict[str, str]:
    return {"id": check_id, "status": "pass" if passed else "fail", "detail": detail}


def _run(command: list[str], inject_stage: str = "", timeout: int = 180) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    if inject_stage:
        env["NEXETRA_INJECT_STAGE_FAILURE"] = inject_stage
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


def _minimal_job(base: dict[str, Any]) -> dict[str, Any]:
    job = copy.deepcopy(base)
    job["job_id"] = "stage5-content-minimal"
    job["languages"] = ["en"]
    job["formats"] = ["16:9"]
    job["scenario"]["variant_count"] = 1
    job["success_criteria"]["minimum_language_completion"] = 1
    return job


def _write_job(job: dict[str, Any], directory: Path) -> Path:
    path = directory / f"{job['job_id']}.json"
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    return path


def _run_content_job(job_path: Path, job_id: str, iteration: int) -> dict[str, Any]:
    before = {manifest.get("run_id") for manifest in load_run_manifests()}
    result = _run([sys.executable, "pipeline/run_job.py", "--job", str(job_path), "--dry-run"], timeout=180)
    manifests = [manifest for manifest in load_run_manifests() if manifest.get("run_id") not in before]
    latest = next((manifest for manifest in manifests if manifest.get("job_id") == job_id), None)
    validation = (latest or {}).get("success_evaluation", {}).get("content_validation", {})
    passed = bool(
        result["returncode"] == 0
        and latest
        and latest.get("status") == "done"
        and [stage.get("stage") for stage in latest.get("stages", [])] == CONTENT_STAGES
        and validation.get("passed")
    )
    return {
        "id": f"{job_id}-iter-{iteration}",
        "passed": passed,
        "returncode": result["returncode"],
        "run_id": (latest or {}).get("run_id", ""),
        "content_validation": validation,
        "stderr": result["stderr"][-1000:],
    }


def _run_failure_injection(job_path: Path, job_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for stage in CONTENT_STAGES:
        before = {manifest.get("run_id") for manifest in load_run_manifests()}
        result = _run([sys.executable, "pipeline/run_job.py", "--job", str(job_path), "--dry-run"], inject_stage=stage)
        manifests = [manifest for manifest in load_run_manifests() if manifest.get("run_id") not in before]
        latest = next((manifest for manifest in manifests if manifest.get("job_id") == job_id), None)
        failed_stage = next(
            (
                item
                for item in (latest or {}).get("stages", [])
                if item.get("stage") == stage and item.get("status") == "failed"
            ),
            {},
        )
        out.append({
            "stage": stage,
            "passed": bool(result["returncode"] != 0 and latest and latest.get("status") == "failed" and failed_stage.get("error")),
            "returncode": result["returncode"],
            "run_id": (latest or {}).get("run_id", ""),
            "diagnostic": failed_stage.get("error", ""),
        })
    return out


def build_report() -> dict[str, Any]:
    captured = dt.datetime.now(dt.timezone.utc)
    base = load_job(ROOT / "jobs" / "content-summer-campaign-v1.json")
    handler = get_handler("content_development")
    checks: list[dict[str, str]] = []
    evidence: dict[str, Any] = {}

    local_tests = _run([sys.executable, "tests/run_suite_json.py"], timeout=240)
    try:
        local_payload = json.loads(local_tests["stdout"].splitlines()[-1])
    except Exception:
        local_payload = {"successful": False}
    evidence["local_tests"] = local_payload
    checks.append(_check("local_json_suite", bool(local_payload.get("successful")), f"tests={local_payload.get('tests_run', '?')}"))

    checks.append(_check(
        "handler_registry",
        bool(handler and [stage.name for stage in handler.stages] == CONTENT_STAGES),
        ",".join(stage.name for stage in handler.stages) if handler else "missing",
    ))
    checks.append(_check("content_job_validates", not validate_job_document(base), "content-summer-campaign-v1 JCL valid"))

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        minimal = _minimal_job(base)
        minimal_path = _write_job(minimal, tmp_path)
        minimal_runs = [_run_content_job(minimal_path, minimal["job_id"], iteration) for iteration in (1, 2)]
        full_runs = [_run_content_job(ROOT / "jobs" / "content-summer-campaign-v1.json", base["job_id"], iteration) for iteration in (1, 2)]
        injection_path = _write_job(minimal, tmp_path)
        failure_injection = _run_failure_injection(injection_path, minimal["job_id"])

    evidence["minimal_runs"] = minimal_runs
    evidence["full_runs"] = full_runs
    evidence["failure_injection"] = failure_injection
    checks.append(_check("minimal_content_twice", all(item["passed"] for item in minimal_runs), f"passes={sum(1 for item in minimal_runs if item['passed'])}/2"))
    checks.append(_check("full_campaign_twice", all(item["passed"] for item in full_runs), f"passes={sum(1 for item in full_runs if item['passed'])}/2"))
    latest_full = full_runs[-1].get("content_validation", {}) if full_runs else {}
    checks.append(_check(
        "full_campaign_contract",
        bool(latest_full.get("passed") and latest_full.get("languages_completed") == 5 and latest_full.get("variant_count_expected") == 5),
        json.dumps({
            "languages_completed": latest_full.get("languages_completed"),
            "variant_count_expected": latest_full.get("variant_count_expected"),
            "artifacts_checked": latest_full.get("artifacts_checked"),
        }, sort_keys=True),
    ))
    checks.append(_check(
        "failure_injection_all_content_stages",
        all(item["passed"] for item in failure_injection),
        f"passes={sum(1 for item in failure_injection if item['passed'])}/{len(CONTENT_STAGES)}",
    ))

    unsupported = []
    for job_file in ("ma-product-launch-brief-v1.json", "harness-regression-weekly-v1.json", "rag-index-validate-supportkb-v1.json"):
        result = _run([sys.executable, "pipeline/run_job.py", "--job", f"jobs/{job_file}", "--dry-run"], timeout=60)
        unsupported.append({"job": job_file, "returncode": result["returncode"]})
    evidence["unsupported_remaining_handlers"] = unsupported
    checks.append(_check(
        "remaining_scenarios_still_rejected",
        all(item["returncode"] == 3 for item in unsupported),
        json.dumps(unsupported, sort_keys=True),
    ))

    passed = sum(1 for item in checks if item["status"] == "pass")
    return {
        "schema_version": 1,
        "stage": "stage-5",
        "checkpoint_id": captured.strftime("%Y%m%d-%H%M%S"),
        "captured_at": captured.isoformat(),
        "status": "pass" if passed == len(checks) else "fail",
        "summary": {
            "substage": "5A-content-development",
            "checks_total": len(checks),
            "checks_passed": passed,
            "checks_failed": len(checks) - passed,
            "latest_run_id": full_runs[-1].get("run_id", "") if full_runs else "",
        },
        "checks": checks,
        "evidence": evidence,
    }


def write_report(report: dict[str, Any]) -> tuple[Path, Path]:
    stage_root = ROOT / "output" / "qualification" / "stage-5"
    destination = stage_root / report["checkpoint_id"]
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "qualification.json"
    markdown_path = destination / "qualification.md"
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    json_path.write_text(encoded, encoding="utf-8")
    lines = [
        "# Stage 5A Content Development Qualification",
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
        detail = item["detail"].replace("|", "\\|")
        lines.append(f"| {item['id']} | {item['status']} | {detail} |")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (stage_root / "latest.json").write_text(encoded, encoding="utf-8")
    (stage_root / "latest.md").write_text(markdown_path.read_text(encoding="utf-8"), encoding="utf-8")
    return json_path, markdown_path


def main() -> None:
    report = build_report()
    json_path, markdown_path = write_report(report)
    result: dict[str, Any] = {
        "status": report["status"],
        "json": str(json_path),
        "markdown": str(markdown_path),
        "summary": report["summary"],
    }
    if "--upload" in sys.argv:
        result["upload"] = upload_report(report, DEFAULT_DASHBOARD_URL)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
