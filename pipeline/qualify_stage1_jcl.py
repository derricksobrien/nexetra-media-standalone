"""Run and persist the Stage 1 JCL V1 qualification gates."""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

try:
    from pipeline.capture_stage0_baseline import DEFAULT_DASHBOARD_URL, upload_report
    from pipeline.job_control import ROOT, load_job, load_schema, validate_job_catalog, validate_job_document
except ModuleNotFoundError:
    from capture_stage0_baseline import DEFAULT_DASHBOARD_URL, upload_report
    from job_control import ROOT, load_job, load_schema, validate_job_catalog, validate_job_document


def _check(check_id: str, passed: bool, detail: str) -> dict[str, str]:
    return {"id": check_id, "status": "pass" if passed else "fail", "detail": detail}


def _output_fingerprint() -> dict[str, tuple[int, int]]:
    output = ROOT / "output"
    if not output.exists():
        return {}
    return {
        path.relative_to(output).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in output.rglob("*")
        if path.is_file()
    }


def _validate_only(runner: str) -> tuple[bool, str]:
    before = _output_fingerprint()
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [sys.executable, runner, "--job", "jobs/what-is-nexetra-live-es.json", "--validate-only"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
    )
    after = _output_fingerprint()
    valid_json = False
    try:
        valid_json = bool(json.loads(result.stdout).get("valid"))
    except json.JSONDecodeError:
        pass
    passed = result.returncode == 0 and valid_json and before == after
    return passed, f"returncode={result.returncode} json={valid_json} output_unchanged={before == after}"


def _invalid_preflight(runner: str) -> tuple[bool, str]:
    before = _output_fingerprint()
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    with tempfile.TemporaryDirectory() as tmp:
        invalid_path = Path(tmp) / "invalid.json"
        invalid_path.write_text(json.dumps({"job_id": "invalid-job"}), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, runner, "--job", str(invalid_path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=60,
        )
    invalid_json = False
    try:
        invalid_json = not bool(json.loads(result.stdout).get("valid"))
    except json.JSONDecodeError:
        pass
    unchanged = before == _output_fingerprint()
    passed = result.returncode == 2 and invalid_json and unchanged
    return passed, f"returncode={result.returncode} rejected={invalid_json} output_unchanged={unchanged}"


def _invalid_case(base: dict[str, Any], mutation: Callable[[dict[str, Any]], None]) -> bool:
    candidate = copy.deepcopy(base)
    mutation(candidate)
    return bool(validate_job_document(candidate))


def build_report() -> dict[str, Any]:
    captured = dt.datetime.now(dt.timezone.utc)
    schema = load_schema()
    catalog = validate_job_catalog(ROOT / "jobs")
    base = load_job(ROOT / "jobs" / "what-is-nexetra-live-es.json")
    checks = [
        _check("schema_draft_2020_12", schema.get("$schema", "").endswith("2020-12/schema"), schema.get("$schema", "")),
        _check("catalog", catalog["valid"], json.dumps(catalog["summary"], sort_keys=True)),
        _check("reject_missing_id", _invalid_case(base, lambda job: job.pop("job_id")), "missing job_id rejected"),
        _check("reject_bad_language", _invalid_case(base, lambda job: job.update(languages=["xx"])), "unknown language rejected"),
        _check("reject_bad_format", _invalid_case(base, lambda job: job.update(formats=["4:3"])), "unsupported format rejected"),
        _check("reject_bad_threshold", _invalid_case(base, lambda job: job["success_criteria"].update(minimum_language_completion=99)), "invalid threshold rejected"),
        _check("reject_unknown_type", _invalid_case(base, lambda job: job.update(job_type="unknown")), "unknown job type rejected"),
        _check("reject_unknown_model", _invalid_case(base, lambda job: job.update(model_policy={"fallback": "unknown:model"})), "unknown model rejected"),
        _check("reject_unsafe_path", _invalid_case(base, lambda job: job["success_criteria"].update(required_artifacts=["../secret.txt"])), "unsafe artifact path rejected"),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        encoded = json.dumps(base)
        (directory / "one.json").write_text(encoded, encoding="utf-8")
        (directory / "two.json").write_text(encoded, encoding="utf-8")
        duplicate = validate_job_catalog(directory)
    checks.append(_check("reject_duplicate_ids", not duplicate["valid"], "duplicate job_id rejected"))

    for runner in ("pipeline/run_job.py", "pipeline/run_batch_pool.py"):
        passed, detail = _validate_only(runner)
        checks.append(_check(f"validate_only_{Path(runner).stem}", passed, detail))
        passed, detail = _invalid_preflight(runner)
        checks.append(_check(f"invalid_preflight_{Path(runner).stem}", passed, detail))

    passed_count = sum(1 for item in checks if item["status"] == "pass")
    return {
        "schema_version": 1,
        "stage": "stage-1",
        "checkpoint_id": captured.strftime("%Y%m%d-%H%M%S"),
        "captured_at": captured.isoformat(),
        "status": "pass" if passed_count == len(checks) else "fail",
        "summary": {
            "checks_total": len(checks),
            "checks_passed": passed_count,
            "checks_failed": len(checks) - passed_count,
            **catalog["summary"],
        },
        "checks": checks,
        "catalog": catalog,
    }


def write_report(report: dict[str, Any]) -> tuple[Path, Path]:
    stage_root = ROOT / "output" / "qualification" / "stage-1"
    destination = stage_root / report["checkpoint_id"]
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "qualification.json"
    markdown_path = destination / "qualification.md"
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    json_path.write_text(encoded, encoding="utf-8")
    lines = [
        "# Stage 1 JCL V1 Qualification",
        "",
        f"- Captured: {report['captured_at']}",
        f"- Status: **{report['status'].upper()}**",
        f"- Checks: {report['summary']['checks_passed']}/{report['summary']['checks_total']}",
        f"- Jobs: {report['summary']['jobs_valid']}/{report['summary']['jobs_total']} valid",
        "",
        "| Check | Status | Detail |",
        "|---|---|---|",
    ]
    for item in report["checks"]:
        detail = item["detail"].replace("|", "\\|")
        lines.append(f"| {item['id']} | {item['status']} | {detail} |")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stage_root.mkdir(parents=True, exist_ok=True)
    (stage_root / "latest.json").write_text(encoded, encoding="utf-8")
    (stage_root / "latest.md").write_text(markdown_path.read_text(encoding="utf-8"), encoding="utf-8")
    return json_path, markdown_path


def main() -> None:
    upload = "--upload" in sys.argv
    report = build_report()
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
