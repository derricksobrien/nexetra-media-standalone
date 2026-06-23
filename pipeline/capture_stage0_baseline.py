"""Capture Stage 0 qualification evidence as JSON and Markdown."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_DASHBOARD_URL = "http://10.0.0.200:7800"
DEFAULT_SCENARIO_JOB = "jobs/ma-product-launch-brief-v1.json"
SCENARIO_TYPES = {
    "content_development",
    "multi_agent_solution",
    "agent_harness_regression",
    "rag_index_and_validation",
}


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _run(command: list[str], timeout: int = 180) -> dict[str, Any]:
    started = _utc_now()
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=timeout,
        )
        return {
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout[-20000:],
            "stderr": result.stderr[-20000:],
            "started_at": started.isoformat(),
            "duration_seconds": round((_utc_now() - started).total_seconds(), 3),
        }
    except Exception as exc:
        return {
            "command": command,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "started_at": started.isoformat(),
            "duration_seconds": round((_utc_now() - started).total_seconds(), 3),
        }


def _fetch_json(url: str, timeout: int = 15) -> tuple[dict[str, Any] | None, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")), ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _git_state() -> dict[str, Any]:
    sha = _run(["git", "rev-parse", "HEAD"], timeout=30)
    status = _run(["git", "status", "--short"], timeout=30)
    return {
        "commit_sha": sha["stdout"].strip() if sha["returncode"] == 0 else "unknown",
        "dirty": bool(status["stdout"].strip()),
        "status": status["stdout"].splitlines(),
    }


def _job_inventory() -> tuple[list[dict[str, Any]], list[str]]:
    jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in sorted((ROOT / "jobs").glob("*.json")):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            scenario = job.get("scenario") if isinstance(job.get("scenario"), dict) else {}
            jobs.append({
                "file": path.name,
                "job_id": job.get("job_id", ""),
                "status": job.get("status", ""),
                "scenario_type": scenario.get("type", ""),
                "languages": job.get("languages", []),
                "formats": job.get("formats", []),
            })
        except Exception as exc:
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return jobs, errors


def _read_run_events() -> list[dict[str, Any]]:
    path = ROOT / "output" / "job_runs.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _output_inventory(job_id: str) -> list[dict[str, Any]]:
    root = ROOT / "output" / job_id
    if not root.exists():
        return []
    return [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _run_fixture(path: Path) -> dict[str, Any]:
    return _run(
        [
            sys.executable,
            "pipeline/run_job.py",
            "--job",
            path.relative_to(ROOT).as_posix(),
            "--dry-run",
        ],
        timeout=180,
    )


def _remote_scenario_diagnostic(host_name: str, job_path: str) -> dict[str, Any]:
    try:
        from pipeline.run_batch_pool import load_config, load_hosts, remote_run

        config = load_config().get("compute_pool", {})
        hosts = load_hosts(
            set(config.get("deny_hosts", [])),
            config.get("allow_name_patterns", []),
        )
        host = next((item for item in hosts if item.name == host_name), None)
        if host is None:
            raise RuntimeError(f"Host not found in eligible inventory: {host_name}")
        remote_command = (
            "cd ~/nexetra-media && .venv/bin/python pipeline/scriptgen/agent.py "
            f"--job {job_path} --dry-run"
        )
        started = _utc_now()
        rc, stdout, stderr = remote_run(host, remote_command, timeout=120)
        return {
            "host": host.name,
            "management_ip": host.ip,
            "command": remote_command,
            "returncode": rc,
            "stdout": stdout[-20000:],
            "stderr": stderr[-20000:],
            "started_at": started.isoformat(),
            "duration_seconds": round((_utc_now() - started).total_seconds(), 3),
        }
    except Exception as exc:
        return {
            "host": host_name,
            "command": "remote scriptgen dry-run",
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "started_at": _utc_now().isoformat(),
            "duration_seconds": 0,
        }


def _check(check_id: str, passed: bool, detail: str) -> dict[str, str]:
    return {"id": check_id, "status": "pass" if passed else "fail", "detail": detail}


def build_report(
    dashboard_url: str,
    run_dry_runs: bool = False,
    diagnostic_host: str = "",
    scenario_job: str = DEFAULT_SCENARIO_JOB,
) -> dict[str, Any]:
    captured = _utc_now()
    snapshot, snapshot_error = _fetch_json(f"{dashboard_url.rstrip('/')}/api/snapshot")
    jobs, job_errors = _job_inventory()
    events = _read_run_events()
    scenario_failures = [
        event
        for event in events
        if event.get("event") in {"stage_fail", "batch_fail"}
        and any(item["scenario_type"] in SCENARIO_TYPES and item["file"] in str(event.get("job", "")) for item in jobs)
    ][-20:]

    fixture_results: list[dict[str, Any]] = []
    if run_dry_runs:
        for name in ("media-en-minimal.json", "media-en-es-baseline.json"):
            path = ROOT / "tests" / "fixtures" / "jobs" / name
            result = _run_fixture(path)
            result["fixture"] = name
            fixture_results.append(result)

    diagnostic = (
        _remote_scenario_diagnostic(diagnostic_host, scenario_job)
        if diagnostic_host
        else None
    )
    dashboard_jobs = (snapshot or {}).get("jobs", [])
    known_good = next(
        (job for job in dashboard_jobs if job.get("job_id") == "what-is-nexetra-live-es"),
        None,
    )
    tb_summary = (snapshot or {}).get("thunderbolt", {}).get("summary", {})

    checks = [
        _check("job_json", not job_errors and bool(jobs), f"jobs={len(jobs)} errors={len(job_errors)}"),
        _check("dashboard_snapshot", snapshot is not None, snapshot_error or "snapshot fetched"),
        _check(
            "known_good_dashboard_evidence",
            bool(known_good and known_good.get("progress") == 100),
            f"progress={(known_good or {}).get('progress', 'missing')}",
        ),
        _check(
            "scenario_failure_history",
            bool(scenario_failures),
            f"scenario failure events={len(scenario_failures)}",
        ),
        _check(
            "thunderbolt_snapshot",
            tb_summary.get("mac_hosts") == 6 and tb_summary.get("good_links") == 6,
            json.dumps(tb_summary, sort_keys=True),
        ),
    ]
    for result in fixture_results:
        checks.append(_check(
            f"dry_run_{Path(result['fixture']).stem.replace('-', '_')}",
            result["returncode"] == 0,
            f"returncode={result['returncode']}",
        ))
    if diagnostic is not None:
        exact_failure = diagnostic["returncode"] not in {0, None} and bool(diagnostic["stderr"].strip())
        checks.append(_check(
            "scenario_failure_diagnostic",
            exact_failure,
            f"host={diagnostic_host} returncode={diagnostic['returncode']}",
        ))

    passed = sum(1 for item in checks if item["status"] == "pass")
    report = {
        "schema_version": 1,
        "stage": "stage-0",
        "checkpoint_id": captured.strftime("%Y%m%d-%H%M%S"),
        "captured_at": captured.isoformat(),
        "status": "pass" if passed == len(checks) else "fail",
        "summary": {
            "checks_total": len(checks),
            "checks_passed": passed,
            "checks_failed": len(checks) - passed,
            "jobs_parsed": len(jobs),
            "scenario_failure_events": len(scenario_failures),
            "known_good_job": "what-is-nexetra-live-es",
        },
        "checks": checks,
        "source": {
            "dashboard_url": dashboard_url,
            "dashboard_error": snapshot_error,
            "git": _git_state(),
            "python": sys.version,
        },
        "inventory": {
            "jobs": jobs,
            "job_errors": job_errors,
            "known_good_outputs": _output_inventory("what-is-nexetra-live-es"),
        },
        "dashboard": {
            "health": (snapshot or {}).get("health", {}),
            "thunderbolt": (snapshot or {}).get("thunderbolt", {}),
            "jobs": dashboard_jobs,
            "runs": (snapshot or {}).get("runs", []),
        },
        "scenario_failures": scenario_failures,
        "fixture_runs": fixture_results,
        "remote_diagnostic": diagnostic,
    }
    return report


def write_report(report: dict[str, Any], output_root: Path) -> tuple[Path, Path]:
    checkpoint = report["checkpoint_id"]
    stage_root = output_root / "stage-0"
    destination = stage_root / checkpoint
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "baseline.json"
    markdown_path = destination / "baseline.md"
    json_text = json.dumps(report, indent=2, ensure_ascii=False)
    json_path.write_text(json_text, encoding="utf-8")

    lines = [
        "# Stage 0 Baseline Qualification",
        "",
        f"- Captured: {report['captured_at']}",
        f"- Status: **{report['status'].upper()}**",
        f"- Checkpoint: `{report['checkpoint_id']}`",
        f"- Checks: {report['summary']['checks_passed']}/{report['summary']['checks_total']} passed",
        "",
        "## Checks",
        "",
        "| Check | Status | Detail |",
        "|---|---|---|",
    ]
    for item in report["checks"]:
        detail = str(item["detail"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {item['id']} | {item['status']} | {detail} |")
    lines.extend([
        "",
        "## Remote Diagnostic",
        "",
        "```text",
        json.dumps(report.get("remote_diagnostic"), indent=2, ensure_ascii=False),
        "```",
        "",
    ])
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    stage_root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(json_path, stage_root / "latest.json")
    shutil.copyfile(markdown_path, stage_root / "latest.md")
    return json_path, markdown_path


def upload_report(report: dict[str, Any], dashboard_url: str) -> dict[str, Any]:
    body = json.dumps(report, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{dashboard_url.rstrip('/')}/api/upload-qualification",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Qualification upload failed: HTTP {exc.code}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture Stage 0 baseline evidence")
    parser.add_argument("--dashboard-url", default=DEFAULT_DASHBOARD_URL)
    parser.add_argument("--output-root", type=Path, default=ROOT / "output" / "qualification")
    parser.add_argument("--run-dry-runs", action="store_true")
    parser.add_argument("--diagnostic-host", default="")
    parser.add_argument("--scenario-job", default=DEFAULT_SCENARIO_JOB)
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()

    report = build_report(
        args.dashboard_url,
        run_dry_runs=args.run_dry_runs,
        diagnostic_host=args.diagnostic_host,
        scenario_job=args.scenario_job,
    )
    json_path, markdown_path = write_report(report, args.output_root)
    print(json.dumps({
        "status": report["status"],
        "json": str(json_path),
        "markdown": str(markdown_path),
        "summary": report["summary"],
    }, indent=2))
    if args.upload:
        print(json.dumps({"upload": upload_report(report, args.dashboard_url)}, indent=2))
    raise SystemExit(0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
