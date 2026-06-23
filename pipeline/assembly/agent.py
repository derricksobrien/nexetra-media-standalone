"""
pipeline/assembly/agent.py
───────────────────────────
Stage 4 — Assembly agent.

Combines a per-language audio.wav with a plain title-card slide image
(generated via Pillow) into a single MP4 using FFmpeg.

Runs on ubuntu-1 (or any host with FFmpeg + Pillow installed).
No Mac Mini, no GPU required.

USAGE:
    python pipeline/assembly/agent.py --job jobs/what-is-nexetra.json --dry-run
    python pipeline/assembly/agent.py --job jobs/what-is-nexetra.json

OUTPUT:
    output/<job_id>/<lang>/master_16x9.mp4
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# Brand colours (Nexetra placeholder — adjust once brand guidelines are set)
BRAND_BG = (10, 18, 42)       # dark navy
BRAND_TEXT = (255, 255, 255)  # white


def _job_output_dir(job_id: str) -> Path:
    override = os.environ.get("NEXETRA_JOB_OUTPUT_DIR")
    return Path(override) if override else ROOT / "output" / job_id


def _make_title_slide(text: str, out_path: Path, width: int = 1920, height: int = 1080) -> bool:
    """Render a simple branded title card PNG using Pillow."""
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415
    except ImportError:
        print("  ERROR: Pillow not installed — run: pip install pillow", file=sys.stderr)
        return False

    img = Image.new("RGB", (width, height), BRAND_BG)
    draw = ImageDraw.Draw(img)

    # Try to use a bundled font; fall back to PIL default
    try:
        font = ImageFont.truetype("arial.ttf", size=72)
    except OSError:
        font = ImageFont.load_default()

    # Centre the text
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (width - (bbox[2] - bbox[0])) // 2
    y = (height - (bbox[3] - bbox[1])) // 2
    draw.text((x, y), text, fill=BRAND_TEXT, font=font)

    img.save(str(out_path))
    return True


def _resolve_ffmpeg() -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    try:
        import imageio_ffmpeg  # noqa: PLC0415
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def assemble(job: dict, lang: str, dry_run: bool = False) -> Path | None:
    """
    Assemble audio + slide image into a 16:9 MP4 for `lang`.
    Returns output path, or None on failure.
    """
    job_id = job["job_id"]
    lang_dir = _job_output_dir(job_id) / lang
    audio_path = lang_dir / "audio.wav"
    slide_path = lang_dir / "title_slide.png"
    out_path   = lang_dir / "master_16x9.mp4"

    if not audio_path.exists():
        print(f"  SKIP {lang}: no audio.wav found at {audio_path.relative_to(ROOT)}", file=sys.stderr)
        return None

    # Read duration from script
    script_path = lang_dir / "script.json"
    duration = job.get("duration_seconds", 60)
    if script_path.exists():
        script = json.loads(script_path.read_text(encoding="utf-8"))
        duration = script.get("duration_hint", duration)

    title = job.get("title", "Nexetra")

    if dry_run:
        print(f"  [DRY RUN] Would assemble {lang}: slide + audio → {out_path.relative_to(ROOT)}")
        # Write a 1-byte stub so downstream steps can detect "file present"
        out_path.write_bytes(b"DRY_RUN_STUB")
        return out_path

    # Build title slide
    print(f"  Rendering title slide for {lang} …")
    if not _make_title_slide(title, slide_path):
        return None

    # Check FFmpeg
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        print(
            "  ERROR: ffmpeg not found on PATH and imageio-ffmpeg not available.",
            file=sys.stderr,
        )
        return None

    # Assemble with FFmpeg:
    # -loop 1 -i slide.png   → static image as video source
    # -i audio.wav            → audio track
    # -shortest               → match shorter of video/audio
    cmd = [
        ffmpeg, "-y",
        "-loop", "1", "-framerate", "25", "-i", str(slide_path),
        "-i", str(audio_path),
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-t", str(duration),
        str(out_path),
    ]
    print(f"  FFmpeg assembling {lang} …")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: ffmpeg failed:\n{result.stderr[-800:]}", file=sys.stderr)
        return None

    size = out_path.stat().st_size
    print(f"  Written → {out_path.relative_to(ROOT)}  ({size:,} bytes)")
    return out_path


def run(job_path: Path, dry_run: bool = False) -> list[Path]:
    """Assemble all languages for the job. Returns list of written output paths."""
    job = json.loads(job_path.read_text(encoding="utf-8"))
    languages = job.get("languages", ["en"])
    written = []

    for lang in languages:
        print(f"Assembly → {lang} …")
        path = assemble(job, lang, dry_run=dry_run)
        if path:
            written.append(path)

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexetra Media — Assembly agent")
    parser.add_argument("--job", required=True, help="Path to job JSON file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip real rendering; write stub MP4 files")
    args = parser.parse_args()
    job_path = Path(args.job)
    job = json.loads(job_path.read_text(encoding="utf-8"))
    expected = len(job.get("languages", ["en"]))
    written = run(job_path, dry_run=args.dry_run)
    if len(written) != expected:
        print(f"ERROR: Assembly completed {len(written)}/{expected} required languages.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
