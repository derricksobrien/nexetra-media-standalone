from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from pipeline import execution
from pipeline.export import agent as export_agent
from pipeline.job_control import ROOT, load_job
from pipeline.translate import agent as translate_agent
from viewer import app as viewer_app


class TruthfulExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_job = load_job(ROOT / "jobs" / "what-is-nexetra-live-es.json")

    def test_manifest_ids_are_unique_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            job_path = str(ROOT / "jobs" / "what-is-nexetra-live-es.json")
            first = execution.RunManifest(self.base_job, job_path, "test", True, runs_dir=runs)
            second = execution.RunManifest(self.base_job, job_path, "test", True, runs_dir=runs)
            self.assertNotEqual(first.run_id, second.run_id)
            self.assertTrue(first.path.exists())
            self.assertFalse(list(runs.rglob("*.tmp-*")))

    def test_missing_required_artifact_fails_success_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = execution.evaluate_success_criteria(self.base_job, output_dir=Path(tmp))
        self.assertFalse(result["passed"])
        self.assertTrue(result["missing"])

    def test_translation_partial_failure_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "job.json"
            path.write_text(json.dumps(self.base_job), encoding="utf-8")
            with mock.patch.object(translate_agent, "run", return_value=[]), mock.patch.object(
                sys, "argv", ["agent.py", "--job", str(path), "--dry-run"]
            ):
                with self.assertRaises(SystemExit) as raised:
                    translate_agent.main()
        self.assertEqual(raised.exception.code, 1)

    def test_export_writes_only_requested_format(self) -> None:
        job = copy.deepcopy(self.base_job)
        job["job_id"] = "stage2-format-isolation"
        job["languages"] = ["en"]
        job["formats"] = ["16:9"]
        job["success_criteria"]["minimum_language_completion"] = 1
        root = ROOT / "output" / job["job_id"]
        shutil.rmtree(root, ignore_errors=True)
        lang_dir = root / "en"
        lang_dir.mkdir(parents=True)
        (lang_dir / "master_16x9.mp4").write_bytes(b"master")
        try:
            written = export_agent.run_from_job(job, dry_run=True)
            self.assertEqual([path.name for path in written], ["16x9.mp4"])
            self.assertFalse((lang_dir / "9x16.mp4").exists())
            self.assertFalse((lang_dir / "1x1.mp4").exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_stale_running_manifest_becomes_abandoned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            manifest = execution.RunManifest(self.base_job, str(ROOT / "jobs" / "what-is-nexetra-live-es.json"), "test", True, runs_dir=runs)
            manifest.data["heartbeat_ts"] = time.time() - 100
            manifest._write()
            actual = execution.mark_abandoned_runs(runs_dir=runs, timeout_seconds=10)
            self.assertEqual(actual[0]["status"], "abandoned")
            persisted = json.loads(manifest.path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["status"], "abandoned")

    def test_dashboard_progress_and_archive_use_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs_dir = root / "jobs"
            output_dir = root / "output"
            runs_dir = output_dir / "runs"
            jobs_dir.mkdir()
            job = copy.deepcopy(self.base_job)
            job["job_id"] = "manifest-dashboard-test"
            (jobs_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
            manifest = execution.RunManifest(job, str(jobs_dir / "job.json"), "test", True, runs_dir=runs_dir)
            for stage in viewer_app.STAGES:
                manifest.add_stage(execution.make_stage_result(stage, "passed", "local", 0, time.time()))
            manifest.finalize("done", evaluation={"passed": True})
            with mock.patch.object(viewer_app, "JOBS_DIR", jobs_dir), mock.patch.object(
                viewer_app, "OUTPUT_DIR", output_dir
            ), mock.patch.object(viewer_app, "RUNS_DIR", runs_dir):
                jobs = viewer_app.get_jobs()
                archive = viewer_app.get_run_archive()
        self.assertEqual(jobs[0]["progress"], 100)
        self.assertEqual(jobs[0]["run_status"], "done")
        self.assertEqual(archive[0]["run_id"], manifest.run_id)

    def test_unsupported_scenario_is_rejected_before_output(self) -> None:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        output = ROOT / "output"

        def fingerprint() -> dict[str, tuple[int, int]]:
            return {
                path.relative_to(output).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
                for path in output.rglob("*")
                if path.is_file()
            }

        before = fingerprint()
        for runner in ("pipeline/run_job.py", "pipeline/run_batch_pool.py"):
            result = subprocess.run(
                [sys.executable, runner, "--job", "jobs/ma-product-launch-brief-v1.json", "--dry-run"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=60,
            )
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertFalse(json.loads(result.stdout)["executable"])
        self.assertEqual(before, fingerprint())

    def test_viewer_direct_script_import_resolves_pipeline(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", "import runpy; runpy.run_path('viewer/app.py', run_name='viewer_test')"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
