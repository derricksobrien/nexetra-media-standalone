"""Persist Stage 3 remote reproducibility qualification gates."""

from __future__ import annotations

import datetime as dt
import io
import json
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

try:
    from pipeline.capture_stage0_baseline import DEFAULT_DASHBOARD_URL, upload_report
    from pipeline.execution import RunManifest, load_run_manifests
    from pipeline.job_control import ROOT, load_job
    from pipeline.provenance import build_provenance
    from pipeline.run_batch_pool import STAGES, _worker_source_archive, worker_preflight_matches
    from pipeline.worker_preflight import collect_preflight
except ModuleNotFoundError:
    from capture_stage0_baseline import DEFAULT_DASHBOARD_URL, upload_report
    from execution import RunManifest, load_run_manifests
    from job_control import ROOT, load_job
    from provenance import build_provenance
    from run_batch_pool import STAGES, _worker_source_archive, worker_preflight_matches
    from worker_preflight import collect_preflight


def _check(check_id: str, passed: bool, detail: str) -> dict[str, str]:
    return {"id": check_id, "status": "pass" if passed else "fail", "detail": detail}


def build_report(wsl_passed: bool, ubuntu_passed: bool) -> dict[str, Any]:
    captured = dt.datetime.now(dt.timezone.utc)
    job_path = ROOT / "jobs" / "what-is-nexetra-live-es.json"
    job = load_job(job_path)
    provenance = build_provenance(job_path)
    with tempfile.TemporaryDirectory() as tmp:
        local_preflight = collect_preflight(job_path, Path(tmp))
    matched, match_errors = worker_preflight_matches(
        local_preflight,
        provenance,
        [stage for stage, _ in STAGES],
    )

    stale = json.loads(json.dumps(local_preflight))
    stale["provenance"]["source_sha"] = "0" * 64
    stale_matched, stale_errors = worker_preflight_matches(stale, provenance, [stage for stage, _ in STAGES])
    model_matched, model_errors = worker_preflight_matches(
        local_preflight,
        provenance,
        [stage for stage, _ in STAGES],
        required_models=["stage3-required-missing:model"],
    )

    payload = _worker_source_archive()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        names = set(archive.getnames())
    archive_ok = (
        ".nexetra-source-manifest.json" in names
        and "pipeline/worker_preflight.py" in names
        and "jobs/what-is-nexetra-live-es.json" in names
        and not any(name.startswith("output/") for name in names)
    )

    manifests = [
        manifest
        for manifest in load_run_manifests()
        if manifest.get("runner") == "run_batch_pool"
        and manifest.get("job_id") == "what-is-nexetra-live-es"
        and manifest.get("dry_run")
    ]
    completed_remote = [
        manifest
        for manifest in manifests
        if manifest.get("status") == "done"
        and manifest.get("success_evaluation", {}).get("passed")
        and any(item.get("matched") for item in manifest.get("preflight", []))
        and manifest.get("provenance") == provenance
    ]

    checks = [
        _check("provenance_hashes", all(len(value) == 64 for value in provenance.values()), json.dumps(provenance, sort_keys=True)),
        _check("local_worker_preflight", matched, "; ".join(match_errors) or "all capabilities and hashes matched"),
        _check("source_archive", archive_ok, f"files={len(names)} bytes={len(payload)}"),
        _check("reject_stale_source", not stale_matched and "source_sha mismatch" in stale_errors, "; ".join(stale_errors)),
        _check("reject_missing_model", not model_matched and bool(model_errors), "; ".join(model_errors)),
        _check("wsl_ubuntu_suite", wsl_passed, "passed separately" if wsl_passed else "required: --wsl-passed"),
        _check("ubuntu_1_suite", ubuntu_passed, "passed on deployed host" if ubuntu_passed else "required: --ubuntu-passed"),
        _check("remote_runs_twice", len(completed_remote) >= 2, f"qualified_remote_runs={len(completed_remote)}"),
        _check(
            "run_scoped_artifacts",
            bool(completed_remote and all(manifest.get("run_id") in manifest.get("artifact_root", "") for manifest in completed_remote[:2])),
            "artifact roots contain run IDs",
        ),
    ]
    passed = sum(1 for item in checks if item["status"] == "pass")
    return {
        "schema_version": 1,
        "stage": "stage-3",
        "checkpoint_id": captured.strftime("%Y%m%d-%H%M%S"),
        "captured_at": captured.isoformat(),
        "status": "pass" if passed == len(checks) else "fail",
        "summary": {
            "checks_total": len(checks),
            "checks_passed": passed,
            "checks_failed": len(checks) - passed,
            "qualified_remote_runs": len(completed_remote),
        },
        "checks": checks,
        "provenance": provenance,
        "local_preflight": local_preflight,
        "remote_manifests": completed_remote[:5],
    }


def write_report(report: dict[str, Any]) -> tuple[Path, Path]:
    stage_root = ROOT / "output" / "qualification" / "stage-3"
    destination = stage_root / report["checkpoint_id"]
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "qualification.json"
    markdown_path = destination / "qualification.md"
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    json_path.write_text(encoded, encoding="utf-8")
    lines = [
        "# Stage 3 Remote Reproducibility Qualification",
        "",
        f"- Captured: {report['captured_at']}",
        f"- Status: **{report['status'].upper()}**",
        f"- Checks: {report['summary']['checks_passed']}/{report['summary']['checks_total']}",
        f"- Qualified remote runs: {report['summary']['qualified_remote_runs']}",
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
    report = build_report(
        wsl_passed="--wsl-passed" in sys.argv,
        ubuntu_passed="--ubuntu-passed" in sys.argv,
    )
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
