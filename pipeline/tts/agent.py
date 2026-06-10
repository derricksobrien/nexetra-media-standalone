"""
pipeline/tts/agent.py
──────────────────────
Stage 3 — Text-to-speech agent.

Reads a per-language script.json, generates a WAV audio track using
Edge-TTS (free Microsoft TTS, no GPU needed, covers all target languages),
and writes output/<job_id>/<lang>/audio.wav.

Edge-TTS runs as a Python library — no local server required.
Falls back to a silent placeholder WAV if Edge-TTS is unavailable (dry-run).

USAGE:
    python pipeline/tts/agent.py --job jobs/what-is-nexetra.json --dry-run
    python pipeline/tts/agent.py --job jobs/what-is-nexetra.json

Host dependency: none — runs locally on ubuntu-1 or any Python host.
No Mac Mini needed.
"""

import argparse
import asyncio
import json
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# Edge-TTS voice map — one voice per language
VOICE_MAP = {
    "en":  "en-US-JennyNeural",
    "es":  "es-ES-ElviraNeural",
    "fr":  "fr-FR-DeniseNeural",
    "de":  "de-DE-KatjaNeural",
    "pt":  "pt-BR-FranciscaNeural",
    "ar":  "ar-SA-ZariyahNeural",
    "zh":  "zh-CN-XiaoxiaoNeural",
    "yue": "zh-HK-HiuMaanNeural",
    "hi":  "hi-IN-SwaraNeural",
    "ja":  "ja-JP-NanamiNeural",
    "sv":  "sv-SE-SofieNeural",
    "no":  "nb-NO-PernilleNeural",
    "da":  "da-DK-ChristelNeural",
    "fi":  "fi-FI-NooraNeural",
}


def _script_to_ssml(script: dict) -> str:
    """Concatenate hook + body + cta into a single narration string."""
    parts = [
        script.get("hook", ""),
        script.get("body", ""),
        script.get("cta", ""),
    ]
    return "  ".join(p.strip() for p in parts if p.strip())


def _write_silent_wav(path: Path, duration_seconds: int = 5) -> None:
    """Write a minimal silent WAV placeholder (for dry-run or missing deps)."""
    sample_rate = 22050
    num_samples = sample_rate * duration_seconds
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack("<" + "h" * num_samples, *([0] * num_samples)))


async def _synthesize(text: str, voice: str, out_path: Path) -> None:
    try:
        import edge_tts  # noqa: PLC0415
    except ImportError:
        raise ImportError("edge-tts not installed — run: pip install edge-tts")

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(out_path))


def generate_audio(script: dict, lang: str, out_path: Path, dry_run: bool = False) -> bool:
    """
    Generate audio for `script` in `lang`, write to `out_path`.
    Returns True on success.
    """
    voice = VOICE_MAP.get(lang)
    if voice is None:
        print(f"  WARNING: No voice mapping for lang '{lang}' — writing silent placeholder.", file=sys.stderr)
        _write_silent_wav(out_path, duration_seconds=script.get("duration_hint", 60))
        return True

    text = _script_to_ssml(script)
    if not text:
        print(f"  WARNING: Script for lang '{lang}' has no text content.", file=sys.stderr)
        _write_silent_wav(out_path, 5)
        return True

    if dry_run:
        print(f"  [DRY RUN] Would synthesize {len(text)} chars with voice {voice}")
        _write_silent_wav(out_path, duration_seconds=script.get("duration_hint", 60))
        return True

    try:
        asyncio.run(_synthesize(text, voice, out_path))
        return True
    except ImportError as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"  ERROR during TTS synthesis: {exc}", file=sys.stderr)
        return False


def run(job_path: Path, dry_run: bool = False) -> list[Path]:
    """
    Generate audio for all languages in the job. Returns list of written paths.
    """
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job_id = job["job_id"]
    languages = job.get("languages", ["en"])
    written = []

    for lang in languages:
        script_path = ROOT / "output" / job_id / lang / "script.json"
        if not script_path.exists():
            print(f"  SKIP {lang}: no script.json at {script_path.relative_to(ROOT)}", file=sys.stderr)
            continue

        script = json.loads(script_path.read_text(encoding="utf-8"))
        out_dir = ROOT / "output" / job_id / lang
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "audio.wav"

        voice = VOICE_MAP.get(lang, "?")
        print(f"TTS → {lang} ({voice}) …")
        ok = generate_audio(script, lang, out_path, dry_run=dry_run)
        if ok:
            size = out_path.stat().st_size if out_path.exists() else 0
            print(f"  Written → {out_path.relative_to(ROOT)}  ({size:,} bytes)")
            written.append(out_path)
        else:
            print(f"  FAILED for {lang}")

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexetra Media — TTS agent")
    parser.add_argument("--job", required=True, help="Path to job JSON file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Write silent WAV placeholders, skip real synthesis")
    args = parser.parse_args()
    run(Path(args.job), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
