from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import capture_stage0_baseline as baseline
from viewer import app as viewer_app


class Stage0BaselineTests(unittest.TestCase):
    def test_job_inventory_parses_committed_jobs(self) -> None:
        jobs, errors = baseline._job_inventory()
        self.assertFalse(errors)
        self.assertGreaterEqual(len(jobs), 10)
        self.assertTrue(any(item["scenario_type"] == "multi_agent_solution" for item in jobs))

    def test_write_report_persists_json_markdown_and_latest(self) -> None:
        report = {
            "schema_version": 1,
            "stage": "stage-0",
            "checkpoint_id": "20260622-000000",
            "captured_at": "2026-06-22T00:00:00+00:00",
            "status": "pass",
            "summary": {"checks_total": 1, "checks_passed": 1, "checks_failed": 0},
            "checks": [{"id": "example", "status": "pass", "detail": "ok"}],
            "remote_diagnostic": None,
        }
        with tempfile.TemporaryDirectory() as tmp:
            json_path, markdown_path = baseline.write_report(report, Path(tmp))
            self.assertTrue(json_path.exists())
            self.assertTrue(markdown_path.exists())
            latest = Path(tmp) / "stage-0" / "latest.json"
            self.assertEqual(json.loads(latest.read_text(encoding="utf-8"))["status"], "pass")

    def test_dashboard_reads_latest_qualification(self) -> None:
        report = {
            "schema_version": 1,
            "stage": "stage-0",
            "checkpoint_id": "test",
            "captured_at": "2026-06-22T00:00:00+00:00",
            "status": "pass",
            "summary": {"checks_total": 1, "checks_passed": 1},
            "checks": [{"id": "example", "status": "pass", "detail": "ok"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage = root / "stage-0"
            stage.mkdir(parents=True)
            (stage / "latest.json").write_text(json.dumps(report), encoding="utf-8")
            with mock.patch.object(viewer_app, "QUALIFICATION_DIR", root):
                actual = viewer_app.get_qualification_status()
            self.assertEqual(actual["stage"], "stage-0")
            self.assertEqual(actual["status"], "pass")

    def test_dashboard_rejects_invalid_qualification(self) -> None:
        with self.assertRaises(ValueError):
            viewer_app._validate_qualification_report({"stage": "stage-0"})


if __name__ == "__main__":
    unittest.main()
