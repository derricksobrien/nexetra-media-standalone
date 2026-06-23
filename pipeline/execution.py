"""Truthful run manifests, stage results, and artifact success evaluation."""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from pipeline.handler_registry import executable_job_types
    from pipeline.provenance import build_provenance
    from pipeline.media_validation import FORMAT_EXPECTATIONS, validate_audio, validate_media_artifacts, validate_script, validate_video
except ModuleNotFoundError:
    from handler_registry import executable_job_types
    from provenance import build_provenance
    from media_validation import FORMAT_EXPECTATIONS, validate_audio, validate_media_artifacts, validate_script, validate_video


ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "output" / "runs"
SUPPORTED_EXECUTION_TYPES = executable_job_types()
FORMAT_FILENAMES = {key: value["name"].removesuffix(".mp4") for key, value in FORMAT_EXPECTATIONS.items()}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".tmp-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(6):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 5:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))


def ensure_executable_job(job: dict[str, Any]) -> None:
    job_type = job.get("job_type", "")
    if job_type not in SUPPORTED_EXECUTION_TYPES:
        raise ValueError(
            f"No execution handler is implemented for job_type '{job_type}'. "
            "Validation succeeded, but execution is unavailable."
        )


def validate_content_artifacts(job: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    languages = list(job.get("languages", []))
    variant_count = int(job.get("scenario", {}).get("variant_count", 1))
    checks: list[dict[str, Any]] = []

    def add(path: Path, relative: str, passed: bool, detail: str) -> None:
        checks.append({"path": relative, "passed": passed, "detail": detail})

    plan_path = output_dir / "plan.json"
    add(plan_path, "plan.json", plan_path.is_file() and plan_path.stat().st_size > 0, "plan present")

    completed_languages = 0
    for language in languages:
        relative = f"{language}/variants.json"
        path = output_dir / relative
        if not path.is_file() or path.stat().st_size <= 0:
            add(path, relative, False, "missing variants")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            variants = payload.get("variants", [])
            passed = payload.get("language") == language and len(variants) == variant_count
            add(path, relative, passed, "variants complete" if passed else "variant contract mismatch")
            if passed:
                completed_languages += 1
        except Exception as exc:
            add(path, relative, False, f"invalid variants JSON: {exc}")

    review_path = output_dir / "review.json"
    try:
        review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.is_file() else {}
        review_passed = bool(review.get("passed"))
        add(review_path, "review.json", review_passed, "review passed" if review_passed else "review did not pass")
    except Exception as exc:
        add(review_path, "review.json", False, f"invalid review JSON: {exc}")

    package_path = output_dir / "package.json"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8")) if package_path.is_file() else {}
        package_passed = (
            package.get("job_id") == job.get("job_id")
            and package.get("languages") == languages
            and int(package.get("variant_count", 0)) == variant_count
        )
        add(package_path, "package.json", package_passed, "package complete" if package_passed else "package contract mismatch")
    except Exception as exc:
        add(package_path, "package.json", False, f"invalid package JSON: {exc}")

    failures = [item for item in checks if not item["passed"]]
    return {
        "passed": not failures,
        "languages_expected": len(languages),
        "languages_completed": completed_languages,
        "variant_count_expected": variant_count,
        "artifacts_checked": len(checks),
        "failures": failures,
        "checks": checks,
    }


def _expand_artifact(template: str, job: dict[str, Any], language: str | None = None) -> list[str]:
    languages = [language] if language else list(job.get("languages", []))
    formats = list(job.get("formats", []))
    language_values: list[str | None] = languages if "{language}" in template else [None]
    format_values: list[str | None] = formats if "{format}" in template else [None]
    paths: list[str] = []
    for lang in language_values:
        for output_format in format_values:
            value = template
            if lang is not None:
                value = value.replace("{language}", lang)
            if output_format is not None:
                value = value.replace("{format}", FORMAT_FILENAMES[output_format])
            paths.append(value)
    return paths


def required_artifact_paths(job: dict[str, Any], language: str | None = None) -> list[str]:
    templates = job.get("success_criteria", {}).get("required_artifacts", [])
    paths: list[str] = []
    for template in templates:
        paths.extend(_expand_artifact(template, job, language=language))
    return sorted(set(paths))


def evaluate_success_criteria(job: dict[str, Any], output_dir: Path | None = None, allow_stubs: bool = False) -> dict[str, Any]:
    job_root = output_dir or ROOT / "output" / job["job_id"]
    required = required_artifact_paths(job)
    missing: list[str] = []
    empty: list[str] = []
    present: list[str] = []
    for relative in required:
        path = job_root / relative
        if not path.exists() or not path.is_file():
            missing.append(relative)
        elif path.stat().st_size <= 0:
            empty.append(relative)
        else:
            present.append(relative)

    completed_languages = 0
    for language in job.get("languages", []):
        language_paths = required_artifact_paths(job, language=language)
        if language_paths and all((job_root / relative).is_file() and (job_root / relative).stat().st_size > 0 for relative in language_paths):
            completed_languages += 1
    minimum_languages = job.get("success_criteria", {}).get("minimum_language_completion", 0)

    unevaluated = [
        key
        for key in ("quality_gate", "max_failure_rate", "minimum_recall_at_5")
        if key in job.get("success_criteria", {})
    ]
    media_validation = None
    content_validation = None
    if job.get("job_type") == "media_pipeline":
        media_validation = validate_media_artifacts(job, job_root, allow_stubs=allow_stubs)
    if job.get("job_type") == "content_development":
        content_validation = validate_content_artifacts(job, job_root)

    passed = (
        not missing
        and not empty
        and completed_languages >= minimum_languages
        and not unevaluated
        and (media_validation is None or media_validation["passed"])
        and (content_validation is None or content_validation["passed"])
    )
    return {
        "passed": passed,
        "job_output_dir": str(job_root),
        "required": required,
        "present": present,
        "missing": missing,
        "empty": empty,
        "languages_completed": completed_languages,
        "minimum_language_completion": minimum_languages,
        "unevaluated_criteria": unevaluated,
        "media_validation": media_validation,
        "content_validation": content_validation,
    }


def stage_artifact_paths(job: dict[str, Any], stage: str) -> list[str]:
    languages = list(job.get("languages", []))
    if stage == "scriptgen":
        return ["en/script.json"]
    if stage == "translate":
        return [f"{language}/script.json" for language in languages]
    if stage == "tts":
        return [f"{language}/audio.wav" for language in languages]
    if stage == "assembly":
        return [f"{language}/master_16x9.mp4" for language in languages]
    if stage == "export":
        return [
            f"{language}/{FORMAT_FILENAMES[output_format]}.mp4"
            for language in languages
            for output_format in job.get("formats", [])
        ]
    if stage == "plan":
        return ["plan.json"]
    if stage == "draft":
        return [f"{language}/draft.md" for language in languages]
    if stage == "variants":
        return [f"{language}/variants.json" for language in languages]
    if stage == "review":
        return ["review.json"]
    if stage == "package":
        return ["package.json"]
    return []


def evaluate_stage_artifacts(job: dict[str, Any], stage: str, output_dir: Path | None = None, allow_stubs: bool = False) -> dict[str, Any]:
    job_root = output_dir or ROOT / "output" / job["job_id"]
    required = stage_artifact_paths(job, stage)
    checks: list[dict[str, Any]] = []
    for relative in required:
        path = job_root / relative
        if stage in {"scriptgen", "translate"}:
            checks.append(validate_script(path, relative))
        elif stage == "tts":
            checks.append(validate_audio(path, relative))
        elif stage == "assembly":
            checks.append(validate_video(path, relative, 1920, 1080, allow_stubs=allow_stubs))
        elif stage == "export":
            output_format = next((fmt for fmt, spec in FORMAT_EXPECTATIONS.items() if relative.endswith(spec["name"])), "16:9")
            spec = FORMAT_EXPECTATIONS[output_format]
            checks.append(validate_video(path, relative, spec["width"], spec["height"], allow_stubs=allow_stubs))
        elif stage in {"plan", "variants", "review", "package"}:
            if not path.is_file() or path.stat().st_size <= 0:
                checks.append({"path": relative, "passed": False, "detail": "missing or empty"})
            else:
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                    checks.append({"path": relative, "passed": True, "detail": "valid JSON"})
                except json.JSONDecodeError as exc:
                    checks.append({"path": relative, "passed": False, "detail": f"invalid JSON: {exc}"})
        elif stage == "draft":
            checks.append({
                "path": relative,
                "passed": path.is_file() and path.stat().st_size > 0,
                "detail": "draft present" if path.is_file() else "missing",
            })
        else:
            checks.append({
                "path": relative,
                "passed": path.is_file() and path.stat().st_size > 0,
                "detail": "present" if path.is_file() else "missing",
            })
    missing = [item["path"] for item in checks if not item["passed"]]
    return {
        "passed": not missing,
        "required": required,
        "missing": missing,
        "outputs": [relative for relative in required if relative not in missing],
        "validation": checks,
    }


def make_stage_result(
    stage: str,
    status: str,
    host: str,
    returncode: int | None,
    started_at: float,
    outputs: list[str] | None = None,
    error: str = "",
    warnings: list[str] | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    ended_at = time.time()
    return {
        "stage": stage,
        "status": status,
        "attempt": attempt,
        "host": host,
        "started_at": dt.datetime.fromtimestamp(started_at, dt.timezone.utc).isoformat(),
        "ended_at": dt.datetime.fromtimestamp(ended_at, dt.timezone.utc).isoformat(),
        "duration_seconds": round(ended_at - started_at, 3),
        "returncode": returncode,
        "outputs": outputs or [],
        "warnings": warnings or [],
        "error": error,
    }


class RunManifest:
    def __init__(
        self,
        job: dict[str, Any],
        job_path: str,
        runner: str,
        dry_run: bool,
        runs_dir: Path | None = None,
    ) -> None:
        self.run_id = f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.run_dir = (runs_dir or RUNS_DIR) / self.run_id
        self.artifact_root = ROOT / "output" / job["job_id"] / "runs" / self.run_id
        self.artifact_rel = Path("output") / job["job_id"] / "runs" / self.run_id
        self.path = self.run_dir / "run_manifest.json"
        now = time.time()
        self.data: dict[str, Any] = {
            "schema_version": 1,
            "run_id": self.run_id,
            "job_id": job["job_id"],
            "job_type": job["job_type"],
            "job_version": job["job_version"],
            "job_path": job_path,
            "provenance": build_provenance(Path(job_path) if Path(job_path).is_absolute() else ROOT / job_path),
            "runner": runner,
            "dry_run": dry_run,
            "status": "running",
            "started_at": utc_now(),
            "started_ts": now,
            "heartbeat_at": utc_now(),
            "heartbeat_ts": now,
            "ended_at": "",
            "ended_ts": 0,
            "stages": [],
            "success_evaluation": None,
            "error": "",
            "preflight": [],
            "artifact_root": str(self.artifact_root),
            "artifact_rel": self.artifact_rel.as_posix(),
        }
        self._write()

    def _write(self) -> None:
        _atomic_write_json(self.path, self.data)

    def heartbeat(self) -> None:
        now = time.time()
        self.data["heartbeat_at"] = utc_now()
        self.data["heartbeat_ts"] = now
        self._write()

    def add_stage(self, result: dict[str, Any]) -> None:
        self.data["stages"].append(result)
        self.heartbeat()

    def add_preflight(self, report: dict[str, Any]) -> None:
        self.data["preflight"].append(report)
        self.heartbeat()

    def finalize(self, status: str, error: str = "", evaluation: dict[str, Any] | None = None) -> None:
        now = time.time()
        self.data.update({
            "status": status,
            "error": error,
            "success_evaluation": evaluation,
            "heartbeat_at": utc_now(),
            "heartbeat_ts": now,
            "ended_at": utc_now(),
            "ended_ts": now,
        })
        self._write()
        self._write_summary()

    def _write_summary(self) -> None:
        lines = [
            f"# Run {self.run_id}",
            "",
            f"- Job: `{self.data['job_id']}`",
            f"- Type: `{self.data['job_type']}`",
            f"- Status: **{self.data['status'].upper()}**",
            f"- Runner: `{self.data['runner']}`",
            f"- Started: {self.data['started_at']}",
            f"- Ended: {self.data['ended_at'] or 'running'}",
            "",
            "| Stage | Status | Host | Return | Duration | Error |",
            "|---|---|---|---:|---:|---|",
        ]
        for stage in self.data["stages"]:
            error = str(stage.get("error", "")).replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {stage['stage']} | {stage['status']} | {stage['host']} | "
                f"{stage.get('returncode')} | {stage.get('duration_seconds', 0)} | {error} |"
            )
        if self.data.get("error"):
            lines.extend(["", "## Error", "", self.data["error"]])
        (self.run_dir / "run_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_run_manifests(runs_dir: Path | None = None, limit: int = 200) -> list[dict[str, Any]]:
    root = runs_dir or RUNS_DIR
    if not root.exists():
        return []
    manifests: list[dict[str, Any]] = []
    paths = sorted(root.glob("*/run_manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in paths[:limit]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("run_id"):
                value["manifest_path"] = str(path)
                manifests.append(value)
        except Exception:
            continue
    return manifests


def mark_abandoned_runs(runs_dir: Path | None = None, timeout_seconds: int = 900) -> list[dict[str, Any]]:
    now = time.time()
    manifests = load_run_manifests(runs_dir=runs_dir)
    for manifest in manifests:
        if manifest.get("status") != "running":
            continue
        heartbeat = float(manifest.get("heartbeat_ts") or manifest.get("started_ts") or 0)
        if not heartbeat or now - heartbeat <= timeout_seconds:
            continue
        manifest.update({
            "status": "abandoned",
            "error": f"No heartbeat for more than {timeout_seconds} seconds",
            "ended_at": utc_now(),
            "ended_ts": now,
        })
        path = Path(manifest.pop("manifest_path"))
        _atomic_write_json(path, manifest)
        manifest["manifest_path"] = str(path)
    return manifests
