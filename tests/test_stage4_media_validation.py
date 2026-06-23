from __future__ import annotations

import copy
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from pipeline import execution
from pipeline.job_control import ROOT, load_job
from pipeline.media_validation import validate_media_artifacts
from pipeline.scriptgen import agent as scriptgen_agent
from viewer import app as viewer_app


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 8000)


def _write_script(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "hook": "Hook",
            "body": "Body",
            "cta": "CTA",
            "duration_hint": 1,
            "keywords": ["nexetra"],
        }),
        encoding="utf-8",
    )


class Stage4MediaValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_job = load_job(ROOT / "tests" / "fixtures" / "jobs" / "media-en-es-baseline.json")

    def test_dry_run_media_contract_accepts_expected_stub_video_set(self) -> None:
        job = copy.deepcopy(self.base_job)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            for language in job["languages"]:
                _write_script(output / language / "script.json")
                _write_wav(output / language / "audio.wav")
                for name in ("16x9.mp4", "9x16.mp4", "1x1.mp4"):
                    (output / language / name).write_bytes(b"DRY_RUN_STUB")
            result = validate_media_artifacts(job, output, allow_stubs=True)
        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["languages_expected"], 2)
        self.assertEqual(result["formats_expected"], 3)

    def test_malformed_script_fails_success_criteria(self) -> None:
        job = copy.deepcopy(self.base_job)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            for language in job["languages"]:
                (output / language).mkdir(parents=True)
                (output / language / "script.json").write_text("{}", encoding="utf-8")
                _write_wav(output / language / "audio.wav")
                for name in ("16x9.mp4", "9x16.mp4", "1x1.mp4"):
                    (output / language / name).write_bytes(b"DRY_RUN_STUB")
            result = execution.evaluate_success_criteria(job, output_dir=output, allow_stubs=True)
        self.assertFalse(result["passed"])
        self.assertTrue(result["media_validation"]["failures"])

    def test_stub_video_is_rejected_for_real_run_contract(self) -> None:
        job = copy.deepcopy(self.base_job)
        job["languages"] = ["en"]
        job["formats"] = ["16:9"]
        job["success_criteria"]["minimum_language_completion"] = 1
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            _write_script(output / "en" / "script.json")
            _write_wav(output / "en" / "audio.wav")
            (output / "en" / "16x9.mp4").write_bytes(b"DRY_RUN_STUB")
            result = execution.evaluate_success_criteria(job, output_dir=output, allow_stubs=False)
        self.assertFalse(result["passed"])

    def test_dashboard_exposes_artifact_validation_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs_dir = root / "jobs"
            output_dir = root / "output"
            runs_dir = output_dir / "runs"
            jobs_dir.mkdir()
            job = copy.deepcopy(self.base_job)
            job["job_id"] = "stage4-dashboard-validation"
            (jobs_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
            manifest = execution.RunManifest(job, str(jobs_dir / "job.json"), "test", True, runs_dir=runs_dir)
            for stage in viewer_app.STAGES:
                manifest.add_stage(execution.make_stage_result(stage, "passed", "local", 0, 1.0))
            manifest.finalize("done", evaluation={
                "passed": True,
                "media_validation": {
                    "passed": True,
                    "artifacts_checked": 10,
                    "failures": [],
                },
            })
            with mock.patch.object(viewer_app, "JOBS_DIR", jobs_dir), mock.patch.object(
                viewer_app, "OUTPUT_DIR", output_dir
            ), mock.patch.object(viewer_app, "RUNS_DIR", runs_dir):
                jobs = viewer_app.get_jobs()
                archive = viewer_app.get_run_archive()
        self.assertEqual(jobs[0]["artifact_validation"]["artifacts_checked"], 10)
        self.assertTrue(archive[0]["artifact_validation"]["passed"])

    def test_scriptgen_empty_llm_response_uses_deterministic_fallback(self) -> None:
        job = copy.deepcopy(self.base_job)
        job["job_id"] = "stage4-scriptgen-fallback"
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = {"message": {"content": ""}}
        response.raise_for_status.return_value = None
        with mock.patch.object(scriptgen_agent, "_load_config", return_value={
            "llm": {"hermes": {"base_url": "http://example.invalid", "model": "test-model"}}
        }), mock.patch.object(scriptgen_agent.httpx, "post", return_value=response):
            script = scriptgen_agent.generate_script(job, dry_run=False)
        self.assertEqual(script["model"], "test-model:fallback")
        self.assertTrue(script["hook"])
        self.assertTrue(script["warnings"])


if __name__ == "__main__":
    unittest.main()
