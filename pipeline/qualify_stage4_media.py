"""Run and persist Stage 4 media-pipeline qualification gates."""

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
    from pipeline.execution import evaluate_stage_artifacts, load_run_manifests
    from pipeline.job_control import ROOT, load_job
    from pipeline.worker_preflight import collect_preflight
except ModuleNotFoundError:
    from capture_stage0_baseline import DEFAULT_DASHBOARD_URL, upload_report
    from execution import evaluate_stage_artifacts, load_run_manifests
    from job_control import ROOT, load_job
    from worker_preflight import collect_preflight


STAGE_SEQUENCE = ["scriptgen", "translate", "tts", "assembly", "export"]
STAGE_SCRIPTS = {
    "scriptgen": "pipeline/scriptgen/agent.py",
    "translate": "pipeline/translate/agent.py",
    "tts": "pipeline/tts/agent.py",
    "assembly": "pipeline/assembly/agent.py",
    "export": "pipeline/export/agent.py",
}


def _check(check_id: str, passed: bool, detail: str) -> dict[str, str]:
    return {"id": check_id, "status": "pass" if passed else "fail", "detail": detail}


def _run(command: list[str], output_dir: Path | None = None, inject_stage: str = "", timeout: int = 180) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    if output_dir is not None:
        env["NEXETRA_JOB_OUTPUT_DIR"] = str(output_dir)
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


def _variant_job(base: dict[str, Any], job_id: str, languages: list[str], formats: list[str]) -> dict[str, Any]:
    job = copy.deepcopy(base)
    job["job_id"] = job_id
    job["languages"] = languages
    job["formats"] = formats
    job["duration_seconds"] = min(int(job.get("duration_seconds", 60)), 1)
    job["success_criteria"]["minimum_language_completion"] = len(languages)
    job["success_criteria"]["required_artifacts"] = [
        "{language}/script.json",
        "{language}/audio.wav",
        "{language}/{format}.mp4",
    ]
    return job


def _write_job(job: dict[str, Any], directory: Path) -> Path:
    path = directory / f"{job['job_id']}.json"
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    return path


def _run_direct_ladder(job: dict[str, Any], stages: list[str], iteration: int) -> dict[str, Any]:
    work_parent = ROOT / "output" / "qualification" / "stage-4" / "work"
    work_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_parent) as tmp:
        tmp_path = Path(tmp)
        job_path = _write_job(job, tmp_path)
        output_dir = tmp_path / "output"
        stage_results: list[dict[str, Any]] = []
        for stage in stages:
            result = _run([sys.executable, STAGE_SCRIPTS[stage], "--job", str(job_path), "--dry-run"], output_dir=output_dir)
            artifact_check = evaluate_stage_artifacts(job, stage, output_dir=output_dir, allow_stubs=True)
            stage_results.append({
                "stage": stage,
                "returncode": result["returncode"],
                "artifact_check": artifact_check,
                "stderr": result["stderr"][-1000:],
            })
            if result["returncode"] != 0 or not artifact_check["passed"]:
                break
    passed = len(stage_results) == len(stages) and all(
        item["returncode"] == 0 and item["artifact_check"]["passed"] for item in stage_results
    )
    return {
        "id": f"{job['job_id']}-iter-{iteration}",
        "passed": passed,
        "stages": stage_results,
    }


def _run_full_ladder(job: dict[str, Any], job_path: Path, iteration: int) -> dict[str, Any]:
    before = {manifest.get("run_id") for manifest in load_run_manifests()}
    result = _run([sys.executable, "pipeline/run_job.py", "--job", str(job_path), "--dry-run"], timeout=240)
    manifests = [manifest for manifest in load_run_manifests() if manifest.get("run_id") not in before]
    latest = next((manifest for manifest in manifests if manifest.get("job_id") == job["job_id"]), None)
    passed = bool(
        result["returncode"] == 0
        and latest
        and latest.get("status") == "done"
        and latest.get("success_evaluation", {}).get("passed")
        and latest.get("success_evaluation", {}).get("media_validation", {}).get("passed")
    )
    return {
        "id": f"{job['job_id']}-iter-{iteration}",
        "passed": passed,
        "returncode": result["returncode"],
        "run_id": (latest or {}).get("run_id", ""),
        "manifest": latest,
        "stderr": result["stderr"][-1000:],
    }


def _run_failure_injection(job: dict[str, Any], job_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for stage in STAGE_SEQUENCE:
        before = {manifest.get("run_id") for manifest in load_run_manifests()}
        result = _run(
            [sys.executable, "pipeline/run_job.py", "--job", str(job_path), "--dry-run"],
            inject_stage=stage,
            timeout=240,
        )
        manifests = [manifest for manifest in load_run_manifests() if manifest.get("run_id") not in before]
        latest = next((manifest for manifest in manifests if manifest.get("job_id") == job["job_id"]), None)
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
    base = load_job(ROOT / "jobs" / "what-is-nexetra-live-es.json")
    checks: list[dict[str, str]] = []
    evidence: dict[str, Any] = {"ladder": {}, "failure_injection": []}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        preflight = collect_preflight(ROOT / "jobs" / "what-is-nexetra-live-es.json", tmp_path / "preflight")
    media_capabilities = preflight.get("media_capabilities", {})
    required_capabilities = ["llm_client", "translation_client", "tts", "ffmpeg", "export"]
    checks.append(_check(
        "explicit_media_capabilities",
        all(key in media_capabilities for key in required_capabilities),
        json.dumps(media_capabilities, sort_keys=True),
    ))

    ladder_specs = [
        ("en_script_only", ["scriptgen"], _variant_job(base, "stage4-en-script-only", ["en"], ["16:9"])),
        ("en_script_tts", ["scriptgen", "tts"], _variant_job(base, "stage4-en-script-tts", ["en"], ["16:9"])),
    ]
    for ladder_id, stages, job in ladder_specs:
        runs = [_run_direct_ladder(job, stages, iteration) for iteration in (1, 2)]
        evidence["ladder"][ladder_id] = runs
        checks.append(_check(ladder_id, all(item["passed"] for item in runs), f"passes={sum(1 for item in runs if item['passed'])}/2"))

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        full_specs = [
            ("en_full_16x9", _variant_job(base, "stage4-en-full-16x9", ["en"], ["16:9"])),
            ("en_es_full_16x9", _variant_job(base, "stage4-en-es-full-16x9", ["en", "es"], ["16:9"])),
            ("en_es_all_formats", _variant_job(base, "stage4-en-es-all-formats", ["en", "es"], ["16:9", "9:16", "1:1"])),
            ("production_job", base),
        ]
        production_job_path = ROOT / "jobs" / "what-is-nexetra-live-es.json"
        injection_job_path = _write_job(_variant_job(base, "stage4-failure-injection", ["en", "es"], ["16:9"]), tmp_path)
        for ladder_id, job in full_specs:
            job_path = production_job_path if ladder_id == "production_job" else _write_job(job, tmp_path)
            runs = [_run_full_ladder(job, job_path, iteration) for iteration in (1, 2)]
            evidence["ladder"][ladder_id] = runs
            checks.append(_check(ladder_id, all(item["passed"] for item in runs), f"passes={sum(1 for item in runs if item['passed'])}/2"))
        evidence["failure_injection"] = _run_failure_injection(load_job(injection_job_path), injection_job_path)
        checks.append(_check(
            "failure_injection_all_stages",
            all(item["passed"] for item in evidence["failure_injection"]),
            f"passes={sum(1 for item in evidence['failure_injection'] if item['passed'])}/{len(STAGE_SEQUENCE)}",
        ))

    passed = sum(1 for item in checks if item["status"] == "pass")
    latest_run_ids = [
        run.get("run_id", "")
        for runs in evidence["ladder"].values()
        for run in runs
        if run.get("run_id")
    ]
    return {
        "schema_version": 1,
        "stage": "stage-4",
        "checkpoint_id": captured.strftime("%Y%m%d-%H%M%S"),
        "captured_at": captured.isoformat(),
        "status": "pass" if passed == len(checks) else "fail",
        "summary": {
            "checks_total": len(checks),
            "checks_passed": passed,
            "checks_failed": len(checks) - passed,
            "ladder_runs": sum(len(runs) for runs in evidence["ladder"].values()),
            "latest_run_id": latest_run_ids[-1] if latest_run_ids else "",
        },
        "checks": checks,
        "evidence": evidence,
        "local_preflight": preflight,
    }


def write_report(report: dict[str, Any]) -> tuple[Path, Path]:
    stage_root = ROOT / "output" / "qualification" / "stage-4"
    destination = stage_root / report["checkpoint_id"]
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "qualification.json"
    markdown_path = destination / "qualification.md"
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    json_path.write_text(encoded, encoding="utf-8")
    lines = [
        "# Stage 4 Media Pipeline Qualification",
        "",
        f"- Captured: {report['captured_at']}",
        f"- Status: **{report['status'].upper()}**",
        f"- Checks: {report['summary']['checks_passed']}/{report['summary']['checks_total']}",
        f"- Ladder runs: {report['summary']['ladder_runs']}",
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
