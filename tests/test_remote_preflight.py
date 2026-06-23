from __future__ import annotations

import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from pipeline import execution
from pipeline.job_control import ROOT, load_job
from pipeline.provenance import build_provenance, dependency_sha, source_sha
from pipeline.run_batch_pool import STAGES, _worker_source_archive, worker_preflight_matches
from pipeline.worker_preflight import collect_preflight


class RemotePreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.job_path = ROOT / "jobs" / "what-is-nexetra-live-es.json"
        cls.job = load_job(cls.job_path)

    def test_provenance_is_stable_and_complete(self) -> None:
        first = build_provenance(self.job_path)
        second = build_provenance(self.job_path)
        self.assertEqual(first, second)
        self.assertEqual(len(first["source_sha"]), 64)
        self.assertEqual(len(first["job_sha"]), 64)
        self.assertEqual(len(first["dependency_sha"]), 64)

    def test_source_hash_changes_with_source_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pipeline").mkdir()
            (root / "jobs").mkdir()
            (root / "viewer").mkdir()
            source = root / "pipeline" / "example.py"
            source.write_text("one", encoding="utf-8")
            (root / "requirements.txt").write_text("dep==1\n", encoding="utf-8")
            before = source_sha(root)
            source.write_text("two", encoding="utf-8")
            after = source_sha(root)
        self.assertNotEqual(before, after)

    def test_worker_archive_contains_jobs_and_excludes_runtime_data(self) -> None:
        payload = _worker_source_archive()
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            names = set(archive.getnames())
        self.assertIn("jobs/ma-product-launch-brief-v1.json", names)
        self.assertIn("pipeline/worker_preflight.py", names)
        self.assertFalse(any(name.startswith("output/") for name in names))
        self.assertNotIn("secrets.local.md", names)

    def test_local_worker_preflight_matches_provenance_and_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = collect_preflight(self.job_path, Path(tmp))
        matched, errors = worker_preflight_matches(
            report,
            build_provenance(self.job_path),
            [stage for stage, _ in STAGES],
        )
        self.assertTrue(matched, errors)
        for capability in ("llm_client", "translation_client", "tts", "ffmpeg", "export"):
            self.assertIn(capability, report["media_capabilities"])

    def test_stale_source_is_rejected(self) -> None:
        report = {
            "provenance": build_provenance(self.job_path),
            "artifact_writable": True,
            "capabilities": {stage: True for stage, _ in STAGES},
            "models": {"ollama": []},
        }
        report["provenance"]["source_sha"] = "0" * 64
        matched, errors = worker_preflight_matches(
            report,
            build_provenance(self.job_path),
            [stage for stage, _ in STAGES],
        )
        self.assertFalse(matched)
        self.assertIn("source_sha mismatch", errors)

    def test_missing_requested_model_is_rejected(self) -> None:
        report = {
            "provenance": build_provenance(self.job_path),
            "artifact_writable": True,
            "capabilities": {stage: True for stage, _ in STAGES},
            "models": {"ollama": ["available:model"]},
        }
        matched, errors = worker_preflight_matches(
            report,
            build_provenance(self.job_path),
            [stage for stage, _ in STAGES],
            required_models=["required:model"],
        )
        self.assertFalse(matched)
        self.assertIn("missing model: required:model", errors)

    def test_local_runner_writes_run_scoped_artifacts_and_provenance_events(self) -> None:
        job = copy.deepcopy(self.job)
        job["job_id"] = "stage3-run-scope-test"
        job["languages"] = ["en"]
        job["formats"] = ["16:9"]
        job["success_criteria"]["minimum_language_completion"] = 1
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
            manifests = [item for item in execution.load_run_manifests() if item.get("job_id") == job["job_id"]]
            self.assertTrue(manifests)
            manifest = manifests[0]
            artifact_root = Path(manifest["artifact_root"])
            self.assertTrue((artifact_root / "en" / "16x9.mp4").is_file())
            self.assertTrue(manifest["provenance"]["source_sha"])
            events = [
                json.loads(line)
                for line in (ROOT / "output" / "job_runs.jsonl").read_text(encoding="utf-8").splitlines()
                if manifest["run_id"] in line
            ]
            self.assertTrue(events)
            self.assertTrue(all(event.get("source_sha") and event.get("job_sha") for event in events))
        finally:
            shutil.rmtree(output_root, ignore_errors=True)
            if "manifest" in locals():
                shutil.rmtree(Path(manifest["manifest_path"]).parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
