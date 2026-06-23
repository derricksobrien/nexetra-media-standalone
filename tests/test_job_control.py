from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pipeline.job_control import ROOT, load_job, validate_job_catalog, validate_job_document


class JobControlV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.valid_job = load_job(ROOT / "jobs" / "what-is-nexetra-live-es.json")

    def _errors_for(self, mutation) -> list[str]:
        job = copy.deepcopy(self.valid_job)
        mutation(job)
        return validate_job_document(job)

    def test_all_committed_jobs_validate(self) -> None:
        report = validate_job_catalog(ROOT / "jobs")
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["summary"]["jobs_valid"], 10)

    def test_missing_job_id_is_rejected(self) -> None:
        self.assertTrue(self._errors_for(lambda job: job.pop("job_id")))

    def test_bad_language_is_rejected(self) -> None:
        self.assertTrue(self._errors_for(lambda job: job.update(languages=["xx"])))

    def test_unsupported_format_is_rejected(self) -> None:
        self.assertTrue(self._errors_for(lambda job: job.update(formats=["4:3"])))

    def test_invalid_threshold_is_rejected(self) -> None:
        self.assertTrue(self._errors_for(lambda job: job["success_criteria"].update(minimum_language_completion=99)))

    def test_unknown_job_type_is_rejected(self) -> None:
        self.assertTrue(self._errors_for(lambda job: job.update(job_type="unknown")))

    def test_unknown_model_is_rejected(self) -> None:
        self.assertTrue(self._errors_for(lambda job: job.update(model_policy={"fallback": "unknown:model"})))

    def test_unsafe_artifact_path_is_rejected(self) -> None:
        self.assertTrue(self._errors_for(lambda job: job["success_criteria"].update(required_artifacts=["../secret.txt"])))

    def test_duplicate_job_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            encoded = json.dumps(self.valid_job)
            (root / "one.json").write_text(encoded, encoding="utf-8")
            (root / "two.json").write_text(encoded, encoding="utf-8")
            report = validate_job_catalog(root)
        self.assertFalse(report["valid"])
        self.assertTrue(all("duplicate job_id" in result["errors"][0] for result in report["jobs"]))

    def test_runner_validate_only_has_no_output_side_effects(self) -> None:
        output = ROOT / "output"

        def fingerprint() -> dict[str, tuple[int, int]]:
            if not output.exists():
                return {}
            return {
                path.relative_to(output).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
                for path in output.rglob("*")
                if path.is_file()
            }

        before = fingerprint()
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        for runner in ("pipeline/run_job.py", "pipeline/run_batch_pool.py"):
            result = subprocess.run(
                [sys.executable, runner, "--job", "jobs/what-is-nexetra-live-es.json", "--validate-only"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["valid"])
        self.assertEqual(before, fingerprint())

    def test_runners_reject_invalid_job_before_output_side_effects(self) -> None:
        output = ROOT / "output"

        def fingerprint() -> dict[str, tuple[int, int]]:
            if not output.exists():
                return {}
            return {
                path.relative_to(output).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
                for path in output.rglob("*")
                if path.is_file()
            }

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        with tempfile.TemporaryDirectory() as tmp:
            invalid_path = Path(tmp) / "invalid.json"
            invalid_path.write_text(json.dumps({"job_id": "invalid-job"}), encoding="utf-8")
            before = fingerprint()
            for runner in ("pipeline/run_job.py", "pipeline/run_batch_pool.py"):
                result = subprocess.run(
                    [sys.executable, runner, "--job", str(invalid_path)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    env=env,
                    timeout=60,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(json.loads(result.stdout)["valid"])
            self.assertEqual(before, fingerprint())


if __name__ == "__main__":
    unittest.main()
