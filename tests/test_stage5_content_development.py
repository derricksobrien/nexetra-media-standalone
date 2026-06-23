from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import execution
from pipeline.handler_registry import get_handler
from pipeline.job_control import ROOT, load_job, validate_job_document
from viewer import app as viewer_app


class Stage5ContentDevelopmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_job = load_job(ROOT / "jobs" / "content-summer-campaign-v1.json")

    def _minimal_job(self) -> dict:
        job = copy.deepcopy(self.base_job)
        job["job_id"] = "stage5-content-minimal"
        job["languages"] = ["en"]
        job["formats"] = ["16:9"]
        job["scenario"]["variant_count"] = 1
        job["success_criteria"]["minimum_language_completion"] = 1
        return job

    def test_content_development_job_validates_and_has_handler(self) -> None:
        self.assertFalse(validate_job_document(self.base_job))
        handler = get_handler("content_development")
        self.assertIsNotNone(handler)
        self.assertEqual([stage.name for stage in handler.stages], ["plan", "draft", "variants", "review", "package"])

    def test_minimal_content_development_run_writes_contract_artifacts(self) -> None:
        job = self._minimal_job()
        output_root = ROOT / "output" / job["job_id"]
        shutil.rmtree(output_root, ignore_errors=True)
        with tempfile.TemporaryDirectory() as tmp:
            job_path = Path(tmp) / "job.json"
            job_path.write_text(json.dumps(job), encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            result = subprocess.run(
                [sys.executable, "pipeline/run_job.py", "--job", str(job_path), "--dry-run"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=120,
            )
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = next(item for item in execution.load_run_manifests() if item.get("job_id") == job["job_id"])
            self.assertEqual([stage["stage"] for stage in manifest["stages"]], ["plan", "draft", "variants", "review", "package"])
            self.assertTrue(manifest["success_evaluation"]["content_validation"]["passed"])
            artifact_root = Path(manifest["artifact_root"])
            self.assertTrue((artifact_root / "plan.json").is_file())
            self.assertTrue((artifact_root / "en" / "variants.json").is_file())
            self.assertTrue((artifact_root / "review.json").is_file())
            self.assertTrue((artifact_root / "package.json").is_file())
        finally:
            shutil.rmtree(output_root, ignore_errors=True)
            if "manifest" in locals():
                shutil.rmtree(Path(manifest["manifest_path"]).parent, ignore_errors=True)

    def test_full_content_campaign_meets_language_and_variant_contract(self) -> None:
        output_root = ROOT / "output" / self.base_job["job_id"]
        shutil.rmtree(output_root, ignore_errors=True)
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            [sys.executable, "pipeline/run_job.py", "--job", "jobs/content-summer-campaign-v1.json", "--dry-run"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=120,
        )
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = next(item for item in execution.load_run_manifests() if item.get("job_id") == self.base_job["job_id"])
            validation = manifest["success_evaluation"]["content_validation"]
            self.assertTrue(validation["passed"], validation["failures"])
            self.assertEqual(validation["languages_completed"], 5)
            self.assertEqual(validation["variant_count_expected"], 5)
        finally:
            shutil.rmtree(output_root, ignore_errors=True)
            if "manifest" in locals():
                shutil.rmtree(Path(manifest["manifest_path"]).parent, ignore_errors=True)

    def test_dashboard_uses_content_stage_names_and_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs_dir = root / "jobs"
            output_dir = root / "output"
            runs_dir = output_dir / "runs"
            jobs_dir.mkdir()
            job = self._minimal_job()
            (jobs_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
            manifest = execution.RunManifest(job, str(jobs_dir / "job.json"), "test", True, runs_dir=runs_dir)
            for stage in get_handler("content_development").stages:
                manifest.add_stage(execution.make_stage_result(stage.name, "passed", "local", 0, 1.0))
            manifest.finalize("done", evaluation={
                "passed": True,
                "content_validation": {
                    "passed": True,
                    "artifacts_checked": 4,
                    "failures": [],
                },
            })
            with mock.patch.object(viewer_app, "JOBS_DIR", jobs_dir), mock.patch.object(
                viewer_app, "OUTPUT_DIR", output_dir
            ), mock.patch.object(viewer_app, "RUNS_DIR", runs_dir):
                jobs = viewer_app.get_jobs()
                archive = viewer_app.get_run_archive()
        self.assertEqual(jobs[0]["stage_names"], ["plan", "draft", "variants", "review", "package"])
        self.assertEqual(jobs[0]["progress"], 100)
        self.assertTrue(jobs[0]["artifact_validation"]["passed"])
        self.assertTrue(archive[0]["artifact_validation"]["passed"])


if __name__ == "__main__":
    unittest.main()
