"""Emit worker provenance and execution capability data as JSON."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from pipeline.provenance import ROOT, build_provenance
except ModuleNotFoundError:
    from provenance import ROOT, build_provenance


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _ollama_models() -> list[str]:
    if not shutil.which("ollama"):
        return []
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=20, check=False
        )
        return [line.split()[0] for line in result.stdout.splitlines()[1:] if line.split()]
    except Exception:
        return []


def collect_preflight(job_path: Path, artifact_root: Path) -> dict[str, Any]:
    artifact_root.mkdir(parents=True, exist_ok=True)
    probe = artifact_root / ".preflight-write-test"
    writable = False
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError:
        pass

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg and _module_available("imageio_ffmpeg"):
        ffmpeg = "imageio-ffmpeg"
    media_capabilities = {
        "llm_client": _module_available("httpx") and _module_available("yaml"),
        "translation_client": _module_available("httpx"),
        "tts": _module_available("edge_tts"),
        "ffmpeg": bool(ffmpeg),
        "export": bool(ffmpeg),
    }
    capabilities = {
        "scriptgen": media_capabilities["llm_client"],
        "translate": media_capabilities["translation_client"],
        "tts": media_capabilities["tts"],
        "assembly": _module_available("PIL") and media_capabilities["ffmpeg"],
        "export": media_capabilities["export"],
        "content_plan": True,
        "content_draft": True,
        "content_variants": True,
        "content_review": True,
        "content_package": True,
    }
    usage = shutil.disk_usage(artifact_root)
    return {
        "schema_version": 1,
        "host": platform.node(),
        "os": platform.system(),
        "architecture": platform.machine(),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "provenance": build_provenance(job_path),
        "artifact_root": str(artifact_root.resolve()),
        "artifact_writable": writable,
        "disk_free_bytes": usage.free,
        "capabilities": capabilities,
        "media_capabilities": media_capabilities,
        "models": {
            "ollama": _ollama_models(),
            "mlx": _module_available("mlx"),
            "mlx_lm": _module_available("mlx_lm"),
        },
        "ready": writable and all(capabilities.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexetra worker preflight")
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--artifact-root", type=Path)
    args = parser.parse_args()
    artifact_root = args.artifact_root or Path(os.environ.get("NEXETRA_JOB_OUTPUT_DIR", ROOT / "output" / "preflight"))
    report = collect_preflight(args.job, artifact_root)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    raise SystemExit(0 if report["ready"] else 1)


if __name__ == "__main__":
    main()
