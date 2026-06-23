"""Validate one job or the complete committed JCL catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from pipeline.job_control import ROOT, validate_job_catalog, validate_job_path
except ModuleNotFoundError:
    from job_control import ROOT, validate_job_catalog, validate_job_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Nexetra JCL V1 jobs")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--job", type=Path, help="Validate one job JSON file")
    group.add_argument("--all", action="store_true", help="Validate all committed jobs")
    args = parser.parse_args()

    report = validate_job_catalog(ROOT / "jobs") if args.all else {
        "schema_version": 1,
        "valid": False,
        "jobs": [validate_job_path(args.job)],
    }
    if not args.all:
        report["valid"] = report["jobs"][0]["valid"]
        report["summary"] = {
            "jobs_total": 1,
            "jobs_valid": int(report["valid"]),
            "jobs_invalid": int(not report["valid"]),
        }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    raise SystemExit(0 if report["valid"] else 2)


if __name__ == "__main__":
    main()
