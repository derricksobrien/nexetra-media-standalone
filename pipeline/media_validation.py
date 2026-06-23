"""Media-pipeline artifact contract validation."""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any


FORMAT_EXPECTATIONS = {
    "16:9": {"name": "16x9.mp4", "width": 1920, "height": 1080},
    "9:16": {"name": "9x16.mp4", "width": 1080, "height": 1920},
    "1:1": {"name": "1x1.mp4", "width": 1080, "height": 1080},
}


def _result(kind: str, path: str, passed: bool, detail: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": path,
        "passed": passed,
        "detail": detail,
        **extra,
    }


def _is_dry_run_stub(path: Path) -> bool:
    try:
        return path.read_bytes() == b"DRY_RUN_STUB"
    except OSError:
        return False


def _ffprobe(path: Path) -> dict[str, Any] | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height,duration:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return {"error": result.stderr[-1000:] or f"ffprobe exit={result.returncode}"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"error": f"ffprobe returned invalid JSON: {exc}"}


def validate_script(path: Path, relative: str) -> dict[str, Any]:
    if not path.is_file():
        return _result("script", relative, False, "missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _result("script", relative, False, f"invalid JSON: {exc}")
    required_text = ["hook", "body", "cta"]
    missing_text = [key for key in required_text if not str(payload.get(key, "")).strip()]
    if missing_text:
        return _result("script", relative, False, f"missing text fields: {missing_text}")
    if not isinstance(payload.get("keywords", []), list):
        return _result("script", relative, False, "keywords must be a list")
    duration = payload.get("duration_hint")
    if duration is not None and not isinstance(duration, (int, float)):
        return _result("script", relative, False, "duration_hint must be numeric")
    return _result("script", relative, True, "valid script JSON")


def validate_audio(path: Path, relative: str) -> dict[str, Any]:
    if not path.is_file():
        return _result("audio", relative, False, "missing")
    size = path.stat().st_size
    if size <= 0:
        return _result("audio", relative, False, "empty file", bytes=size)
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            duration = frames / float(rate) if rate else 0.0
        return _result("audio", relative, duration > 0, "valid WAV audio", bytes=size, duration_seconds=round(duration, 3))
    except wave.Error:
        probe = _ffprobe(path)
        if probe is None:
            return _result("audio", relative, True, "nonempty audio; ffprobe unavailable", bytes=size)
        if probe.get("error"):
            return _result("audio", relative, False, probe["error"], bytes=size)
        duration = float(probe.get("format", {}).get("duration") or 0)
        audio_stream = any(stream.get("codec_type") == "audio" for stream in probe.get("streams", []))
        return _result(
            "audio",
            relative,
            audio_stream and duration > 0,
            "playable audio" if audio_stream and duration > 0 else "no playable audio stream",
            bytes=size,
            duration_seconds=round(duration, 3),
        )


def validate_video(path: Path, relative: str, expected_width: int, expected_height: int, allow_stubs: bool = False) -> dict[str, Any]:
    if not path.is_file():
        return _result("video", relative, False, "missing")
    size = path.stat().st_size
    if size <= 0:
        return _result("video", relative, False, "empty file", bytes=size)
    is_stub = _is_dry_run_stub(path)
    if is_stub and not allow_stubs:
        return _result("video", relative, False, "dry-run stub is not valid for a real run", bytes=size)
    if allow_stubs and is_stub:
        return _result(
            "video",
            relative,
            True,
            "dry-run video stub accepted",
            bytes=size,
            width=expected_width,
            height=expected_height,
            duration_seconds=0,
        )
    probe = _ffprobe(path)
    if probe is None:
        return _result("video", relative, True, "nonempty video; ffprobe unavailable", bytes=size)
    if probe.get("error"):
        return _result("video", relative, False, probe["error"], bytes=size)
    video_streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"]
    stream = video_streams[0] if video_streams else {}
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    duration = float(stream.get("duration") or probe.get("format", {}).get("duration") or 0)
    passed = bool(video_streams) and width == expected_width and height == expected_height and duration > 0
    return _result(
        "video",
        relative,
        passed,
        "playable video with expected dimensions" if passed else "video contract mismatch",
        bytes=size,
        width=width,
        height=height,
        expected_width=expected_width,
        expected_height=expected_height,
        duration_seconds=round(duration, 3),
    )


def validate_media_artifacts(job: dict[str, Any], output_dir: Path, allow_stubs: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    languages = list(job.get("languages", []))
    formats = list(job.get("formats", []))
    for language in languages:
        checks.append(validate_script(output_dir / language / "script.json", f"{language}/script.json"))
        checks.append(validate_audio(output_dir / language / "audio.wav", f"{language}/audio.wav"))
        for output_format in formats:
            expected = FORMAT_EXPECTATIONS[output_format]
            checks.append(
                validate_video(
                    output_dir / language / expected["name"],
                    f"{language}/{expected['name']}",
                    expected["width"],
                    expected["height"],
                    allow_stubs=allow_stubs,
                )
            )
    failures = [item for item in checks if not item["passed"]]
    return {
        "passed": not failures,
        "languages_expected": len(languages),
        "formats_expected": len(formats),
        "artifacts_expected": len(languages) * (2 + len(formats)),
        "artifacts_checked": len(checks),
        "failures": failures,
        "checks": checks,
        "allow_stubs": allow_stubs,
    }
