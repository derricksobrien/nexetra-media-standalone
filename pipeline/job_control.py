"""JCL V1 loading, JSON Schema validation, and semantic checks."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "jobs" / "schema" / "job-v1.schema.json"


def load_schema() -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def load_job(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Job document must be a JSON object")
    return value


def _format_schema_error(error: Any) -> str:
    location = ".".join(str(part) for part in error.absolute_path) or "$"
    return f"{location}: {error.message}"


def _artifact_path_errors(job: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    criteria = job.get("success_criteria", {})
    for artifact in criteria.get("required_artifacts", []):
        normalized = artifact.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or normalized.startswith("~"):
            errors.append(f"success_criteria.required_artifacts: unsafe path '{artifact}'")
    return errors


def validate_job_document(job: dict[str, Any], schema: dict[str, Any] | None = None) -> list[str]:
    active_schema = schema or load_schema()
    validator = Draft202012Validator(active_schema)
    errors = [_format_schema_error(error) for error in sorted(validator.iter_errors(job), key=lambda item: list(item.absolute_path))]
    errors.extend(_artifact_path_errors(job))

    languages = job.get("languages", [])
    minimum = job.get("success_criteria", {}).get("minimum_language_completion")
    if isinstance(minimum, int) and isinstance(languages, list) and minimum > len(languages):
        errors.append("success_criteria.minimum_language_completion: exceeds languages count")

    scenario = job.get("scenario")
    if isinstance(scenario, dict) and scenario.get("type") != job.get("job_type"):
        errors.append("scenario.type: must match job_type")
    return sorted(set(errors))


def validate_job_path(path: Path, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        job = load_job(path)
    except Exception as exc:
        return {
            "path": str(path),
            "job_id": "",
            "valid": False,
            "errors": [f"$: {type(exc).__name__}: {exc}"],
        }
    errors = validate_job_document(job, schema=schema)
    return {
        "path": str(path),
        "job_id": job.get("job_id", ""),
        "job_version": job.get("job_version"),
        "job_type": job.get("job_type", ""),
        "valid": not errors,
        "errors": errors,
    }


def validate_job_catalog(jobs_dir: Path) -> dict[str, Any]:
    schema = load_schema()
    results = [validate_job_path(path, schema=schema) for path in sorted(jobs_dir.glob("*.json"))]
    ids: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        job_id = result.get("job_id")
        if job_id:
            ids.setdefault(job_id, []).append(result)
    for job_id, duplicates in ids.items():
        if len(duplicates) > 1:
            for result in duplicates:
                result["valid"] = False
                result["errors"].append(f"job_id: duplicate job_id '{job_id}'")
    valid_count = sum(1 for result in results if result["valid"])
    return {
        "schema_version": 1,
        "schema_path": str(SCHEMA_PATH),
        "jobs_dir": str(jobs_dir),
        "valid": valid_count == len(results) and bool(results),
        "summary": {
            "jobs_total": len(results),
            "jobs_valid": valid_count,
            "jobs_invalid": len(results) - valid_count,
        },
        "jobs": results,
    }
