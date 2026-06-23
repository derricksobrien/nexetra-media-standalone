"""Deterministic content-development stage agent."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent.parent
STAGES = {"plan", "draft", "variants", "review", "package"}


def _job_output_dir(job_id: str) -> Path:
    override = os.environ.get("NEXETRA_JOB_OUTPUT_DIR")
    return Path(override) if override else ROOT / "output" / job_id


def _load_job(job_path: Path) -> dict[str, Any]:
    return json.loads(job_path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_plan(job: dict[str, Any], out_dir: Path) -> list[Path]:
    languages = job.get("languages", [])
    variant_count = int(job.get("scenario", {}).get("variant_count", 1))
    payload = {
        "job_id": job["job_id"],
        "title": job["title"],
        "brand_profile": job.get("scenario", {}).get("brand_profile", ""),
        "cta": job.get("cta", ""),
        "languages": languages,
        "variant_count": variant_count,
        "message_pillars": [
            "practical AI delivery",
            "software engineering discipline",
            "measurable campaign outcomes",
        ],
        "created_at": _timestamp(),
    }
    return [_write_json(out_dir / "plan.json", payload)]


def run_draft(job: dict[str, Any], out_dir: Path) -> list[Path]:
    plan_path = out_dir / "plan.json"
    if not plan_path.is_file():
        raise FileNotFoundError(f"Missing plan artifact: {plan_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    written: list[Path] = []
    for language in job.get("languages", []):
        text = "\n".join([
            f"# {job['title']} ({language})",
            "",
            f"Campaign CTA: {job.get('cta', '')}",
            "",
            "Core message:",
            f"Nexetra helps teams turn {plan['message_pillars'][0]} into repeatable business value.",
            "",
            "Audience promise:",
            "A practical workshop that moves from strategy to working implementation.",
        ])
        written.append(_write_text(out_dir / language / "draft.md", text))
    return written


def run_variants(job: dict[str, Any], out_dir: Path) -> list[Path]:
    variant_count = int(job.get("scenario", {}).get("variant_count", 1))
    written: list[Path] = []
    for language in job.get("languages", []):
        draft_path = out_dir / language / "draft.md"
        if not draft_path.is_file():
            raise FileNotFoundError(f"Missing draft artifact: {draft_path}")
        variants = []
        for index in range(1, variant_count + 1):
            variants.append({
                "id": f"{language}-variant-{index:02d}",
                "language": language,
                "headline": f"{job['title']} - option {index}",
                "body": f"Book a Nexetra AI workshop and turn your summer campaign into working systems. Variant {index}.",
                "cta": job.get("cta", ""),
            })
        written.append(_write_json(out_dir / language / "variants.json", {
            "language": language,
            "variant_count": variant_count,
            "variants": variants,
            "created_at": _timestamp(),
        }))
    return written


def run_review(job: dict[str, Any], out_dir: Path) -> list[Path]:
    variant_count = int(job.get("scenario", {}).get("variant_count", 1))
    languages = job.get("languages", [])
    missing = []
    for language in languages:
        path = out_dir / language / "variants.json"
        if not path.is_file():
            missing.append(path.relative_to(out_dir).as_posix())
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if len(payload.get("variants", [])) != variant_count:
            missing.append(path.relative_to(out_dir).as_posix())
    passed = not missing
    return [_write_json(out_dir / "review.json", {
        "passed": passed,
        "critic": "deterministic-content-review",
        "languages_reviewed": len(languages) - len(missing),
        "languages_expected": len(languages),
        "variant_count_expected": variant_count,
        "issues": missing,
        "reviewed_at": _timestamp(),
    })]


def run_package(job: dict[str, Any], out_dir: Path) -> list[Path]:
    review_path = out_dir / "review.json"
    if not review_path.is_file():
        raise FileNotFoundError(f"Missing review artifact: {review_path}")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if not review.get("passed"):
        raise RuntimeError(f"Content review did not pass: {review.get('issues', [])}")
    package = {
        "job_id": job["job_id"],
        "title": job["title"],
        "languages": job.get("languages", []),
        "formats": job.get("formats", []),
        "variant_count": int(job.get("scenario", {}).get("variant_count", 1)),
        "artifacts": [],
        "packaged_at": _timestamp(),
    }
    for language in job.get("languages", []):
        package["artifacts"].append({
            "language": language,
            "draft": f"{language}/draft.md",
            "variants": f"{language}/variants.json",
        })
    return [_write_json(out_dir / "package.json", package)]


RUNNERS = {
    "plan": run_plan,
    "draft": run_draft,
    "variants": run_variants,
    "review": run_review,
    "package": run_package,
}


def run(job_path: Path, stage: str) -> list[Path]:
    if stage not in STAGES:
        raise ValueError(f"Unknown content-development stage: {stage}")
    job = _load_job(job_path)
    if job.get("job_type") != "content_development":
        raise ValueError(f"Job type must be content_development, got {job.get('job_type')}")
    return RUNNERS[stage](job, _job_output_dir(job["job_id"]))


def main(stage: str | None = None) -> None:
    parser = argparse.ArgumentParser(description="Nexetra content-development stage agent")
    parser.add_argument("--job", required=True, help="Path to job JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Accepted for runner compatibility")
    if stage is None:
        parser.add_argument("--stage", required=True, choices=sorted(STAGES))
    args = parser.parse_args()
    selected_stage = stage or args.stage
    try:
        written = run(Path(args.job), selected_stage)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    for path in written:
        print(f"Written -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
