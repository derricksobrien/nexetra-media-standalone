"""
pipeline/translate/agent.py
────────────────────────────
Stage 2 — Translation agent.

Reads an English script.json produced by scriptgen, fans out translation
calls across the Mac Mini workers (Ollama 8B), and writes per-language
script.json files under output/<job_id>/<lang>/.

USAGE (standalone smoke-test):
    python pipeline/translate/agent.py --job jobs/what-is-nexetra.json --dry-run

OUTPUT:
    output/<job_id>/<lang>/script.json  for each non-EN language in the job
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "pipeline" / "config.yaml"
ROOT_CONFIG_PATH = ROOT / "config.yaml"

# Translation worker — DGX Spark handles all LLM work (avoids Mac Mini dependency).
# If you later want to fan out to additional Ollama nodes, just add their URLs here.
TRANSLATION_WORKERS = [
    "http://10.0.0.7:11434",     # DGX Spark (primary)
]

LANGUAGE_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "pt": "Portuguese", "ar": "Arabic", "zh": "Mandarin Chinese",
    "yue": "Cantonese", "hi": "Hindi", "ja": "Japanese",
    "sv": "Swedish", "no": "Norwegian", "da": "Danish", "fi": "Finnish",
}

TRANSLATION_MODEL = "gpt-oss:120b"


def _job_output_dir(job_id: str) -> Path:
    override = os.environ.get("NEXETRA_JOB_OUTPUT_DIR")
    return Path(override) if override else ROOT / "output" / job_id


def _load_config() -> dict:
    if ROOT_CONFIG_PATH.exists():
        return yaml.safe_load(ROOT_CONFIG_PATH.read_text(encoding="utf-8"))
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    fallback = ROOT / ".." / "config.yaml"
    if fallback.exists():
        return yaml.safe_load(fallback.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"No config.yaml found")


def translate_script(script: dict, target_lang: str, worker_url: str, dry_run: bool = False) -> dict:
    """
    Translate all text fields in `script` to `target_lang`.
    Returns a new script dict with translated content.
    """
    lang_name = LANGUAGE_NAMES.get(target_lang, target_lang)

    if dry_run:
        return {
            **script,
            "hook": f"[DRY RUN {lang_name}] {script.get('hook', '')}",
            "body": f"[DRY RUN {lang_name}] {script.get('body', '')}",
            "cta":  f"[DRY RUN {lang_name}] {script.get('cta', '')}",
            "translated_to": target_lang,
            "translated_at": datetime.now(timezone.utc).isoformat(),
        }

    fields = {
        "hook": script.get("hook", ""),
        "body": script.get("body", ""),
        "cta":  script.get("cta",  ""),
    }

    system_prompt = (
        f"You are a professional translator. "
        f"Translate the following JSON fields into {lang_name}. "
        f"Return ONLY valid JSON with the same keys. "
        f"Preserve formatting. No markdown, no preamble."
    )
    user_prompt = json.dumps(fields, ensure_ascii=False)

    payload = {
        "model": TRANSLATION_MODEL,
        "format": "json",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {"temperature": 0.3, "num_predict": 600},
        "stream": False,
    }

    try:
        # Preferred endpoint for newer Ollama builds.
        resp = httpx.post(f"{worker_url}/api/chat", json=payload, timeout=120.0)
        if resp.status_code == 404:
            # Compatibility fallback for servers exposing only /api/generate.
            gen_payload = {
                "model": TRANSLATION_MODEL,
                "prompt": f"System:\n{system_prompt}\n\nUser:\n{user_prompt}",
                "options": payload["options"],
                "stream": False,
            }
            resp = httpx.post(f"{worker_url}/api/generate", json=gen_payload, timeout=120.0)

        resp.raise_for_status()
        result = resp.json()
        content = (
            result.get("message", {}).get("content")
            or result.get("response")
            or ""
        ).strip()
        try:
            translated_fields = json.loads(content)
        except json.JSONDecodeError:
            # Some models still prepend/append text; salvage first JSON object.
            match = re.search(r"\{[\s\S]*\}", content)
            if not match:
                raise
            translated_fields = json.loads(match.group(0))
    except httpx.ConnectError:
        print(f"  ERROR: Worker at {worker_url} unreachable.", file=sys.stderr)
        return None
    except httpx.HTTPStatusError as exc:
        print(f"  ERROR: Worker request failed: {exc}", file=sys.stderr)
        return None
    except json.JSONDecodeError as exc:
        print(f"  ERROR: Worker returned non-JSON: {exc}", file=sys.stderr)
        return None

    return {
        **script,
        **translated_fields,
        "translated_to": target_lang,
        "translated_at": datetime.now(timezone.utc).isoformat(),
    }


def run(job_path: Path, dry_run: bool = False) -> list[Path]:
    """
    Translate the EN script for the given job into all non-EN languages.
    Returns list of output paths written.
    """
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job_id = job["job_id"]
    languages = [lang for lang in job.get("languages", ["en"]) if lang != "en"]

    if not languages:
        print("No non-English languages specified in job — nothing to translate.")
        return []

    job_output = _job_output_dir(job_id)
    en_script_path = job_output / "en" / "script.json"
    if not en_script_path.exists():
        print(f"ERROR: English script not found at {en_script_path}. Run scriptgen first.", file=sys.stderr)
        sys.exit(1)

    en_script = json.loads(en_script_path.read_text(encoding="utf-8"))
    written = []

    for i, lang in enumerate(languages):
        worker_url = TRANSLATION_WORKERS[i % len(TRANSLATION_WORKERS)]
        lang_name = LANGUAGE_NAMES.get(lang, lang)
        print(f"Translating → {lang_name} ({lang}) via {worker_url} …")

        translated = translate_script(en_script, lang, worker_url, dry_run=dry_run)
        if translated is None:
            print(f"  FAILED for {lang} — skipping.")
            continue

        out_dir = job_output / lang
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "script.json"
        out_path.write_text(json.dumps(translated, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Written → {out_path.relative_to(ROOT)}")
        written.append(out_path)

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexetra Media — Translation agent")
    parser.add_argument("--job", required=True, help="Path to job JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Stub output, skip LLM calls")
    args = parser.parse_args()
    job_path = Path(args.job)
    job = json.loads(job_path.read_text(encoding="utf-8"))
    expected = len([lang for lang in job.get("languages", ["en"]) if lang != "en"])
    written = run(job_path, dry_run=args.dry_run)
    if len(written) != expected:
        print(f"ERROR: Translation completed {len(written)}/{expected} required languages.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
