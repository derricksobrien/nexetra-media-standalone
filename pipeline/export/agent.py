"""
pipeline/export/agent.py
------------------------
Stage 5 - Platform export agent.

Takes a per-language master_16x9.mp4 and produces delivery variants:
- 16x9.mp4 (copy from master)
- 9x16.mp4 (vertical crop/scale)
- 1x1.mp4  (square crop/scale)

USAGE:
    python pipeline/export/agent.py --job jobs/what-is-nexetra.json --dry-run
    python pipeline/export/agent.py --job jobs/what-is-nexetra.json
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve_ffmpeg() -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    try:
        import imageio_ffmpeg  # noqa: PLC0415
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _run_ffmpeg(args: list[str]) -> bool:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: ffmpeg failed:\n{result.stderr[-800:]}", file=sys.stderr)
        return False
    return True


def _make_stub(path: Path) -> None:
    path.write_bytes(b"DRY_RUN_STUB")


def _export_one(lang_dir: Path, dry_run: bool) -> bool:
    master_path = lang_dir / "master_16x9.mp4"
    if not master_path.exists():
        print(f"  SKIP: {master_path.relative_to(ROOT)} is missing", file=sys.stderr)
        return False

    out_16x9 = lang_dir / "16x9.mp4"
    out_9x16 = lang_dir / "9x16.mp4"
    out_1x1 = lang_dir / "1x1.mp4"

    if dry_run:
        print(f"  [DRY RUN] Would export variants for {lang_dir.name}")
        _make_stub(out_16x9)
        _make_stub(out_9x16)
        _make_stub(out_1x1)
        return True

    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        print(
            "  ERROR: ffmpeg not found on PATH and imageio-ffmpeg not available.",
            file=sys.stderr,
        )
        return False

    # 16:9 output is a direct copy from master.
    shutil.copyfile(master_path, out_16x9)

    # 9:16 vertical center-crop from 16:9 source.
    ok_9x16 = _run_ffmpeg([
        ffmpeg, "-y",
        "-i", str(master_path),
        "-vf", "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        str(out_9x16),
    ])

    # 1:1 square center-crop from 16:9 source.
    ok_1x1 = _run_ffmpeg([
        ffmpeg, "-y",
        "-i", str(master_path),
        "-vf", "crop=ih:ih:(iw-ih)/2:0,scale=1080:1080",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        str(out_1x1),
    ])

    return ok_9x16 and ok_1x1


def run(job_path: Path, dry_run: bool = False) -> list[Path]:
    """Export all languages listed in the job. Returns written files."""
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job_id = job["job_id"]
    languages = job.get("languages", ["en"])
    written: list[Path] = []

    for lang in languages:
        lang_dir = ROOT / "output" / job_id / lang
        print(f"Export -> {lang} ...")
        ok = _export_one(lang_dir, dry_run=dry_run)
        if not ok:
            continue
        for name in ("16x9.mp4", "9x16.mp4", "1x1.mp4"):
            path = lang_dir / name
            if path.exists():
                written.append(path)
                print(f"  Written -> {path.relative_to(ROOT)}")

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexetra Media - Export agent")
    parser.add_argument("--job", required=True, help="Path to job JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Write stubs only")
    args = parser.parse_args()
    run(Path(args.job), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
