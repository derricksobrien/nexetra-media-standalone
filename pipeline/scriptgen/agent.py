"""
pipeline/scriptgen/agent.py
────────────────────────────
Stage 1 — Script generation agent.

Calls the Ollama LLM on DGX Spark to produce a structured promotional script
for a given job definition.

USAGE (standalone smoke-test):
    python pipeline/scriptgen/agent.py --job jobs/what-is-nexetra.json

OUTPUT:
    Writes output/<job_id>/en/script.json
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "pipeline" / "config.yaml"
ROOT_CONFIG_PATH = ROOT / "config.yaml"

SCRIPT_TEMPLATE = {
    "hook": "",        # opening line — 5-8 sec
    "body": "",        # main content — 40-50 sec
    "cta": "",         # call-to-action — 5-8 sec
    "duration_hint": 60,
    "keywords": [],
    "generated_at": "",
    "model": "",
}


def _load_config() -> dict:
    if ROOT_CONFIG_PATH.exists():
        return yaml.safe_load(ROOT_CONFIG_PATH.read_text(encoding="utf-8"))
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    # Fallback: look for a config.yaml one level up from the repo root.
    fallback = ROOT / ".." / "config.yaml"
    if fallback.exists():
        return yaml.safe_load(fallback.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        f"No config.yaml found (looked in {ROOT_CONFIG_PATH}, {CONFIG_PATH}, and {fallback})"
    )


def generate_script(job: dict, dry_run: bool = False) -> dict:
    """
    Generate a structured script for the given job dict.

    Parameters
    ----------
    job      : parsed job JSON (from jobs/<name>.json)
    dry_run  : if True, return a stub without calling the LLM

    Returns
    -------
    dict matching SCRIPT_TEMPLATE shape
    """
    if dry_run:
        return {
            **SCRIPT_TEMPLATE,
            "hook": f"[DRY RUN] Hook for: {job.get('title')}",
            "body": "[DRY RUN] Body placeholder — LLM not called.",
            "cta": job.get("cta", "Learn more."),
            "duration_hint": job.get("duration_seconds", 60),
            "keywords": [job.get("title", "")],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": "dry-run",
        }

    cfg = _load_config()
    llm = cfg["llm"]["hermes"]
    base_url = llm["base_url"]
    model = llm["model"]

    title = job.get("title", "")
    cta = job.get("cta", "")
    duration = job.get("duration_seconds", 60)
    style = job.get("style", "slides")

    system_prompt = (
        "You are a professional video script writer for a B2B technology company. "
        "Write tight, punchy promotional scripts. "
        "Return ONLY valid JSON with keys: hook, body, cta, keywords (array of 3-5 strings). "
        "No markdown, no preamble."
    )

    user_prompt = (
        f"Write a {duration}-second promotional video script for:\n"
        f"  Title: {title}\n"
        f"  Style: {style}\n"
        f"  Call to action: {cta}\n"
        f"\nThe company is Nexetra — a software and AI professional services firm. "
        f"Keep each section concise:\n"
        f"  hook (5-8 sec), body (40-50 sec), cta (5-8 sec)."
    )

    payload = {
        "model": model,
        "format": "json",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {"temperature": 0.6, "num_predict": 512},
        "stream": False,
    }

    try:
        # Preferred endpoint for newer Ollama builds.
        resp = httpx.post(f"{base_url}/api/chat", json=payload, timeout=90.0)
        if resp.status_code == 404:
            # Compatibility fallback for servers exposing only /api/generate.
            gen_payload = {
                "model": model,
                "prompt": f"System:\n{system_prompt}\n\nUser:\n{user_prompt}",
                "options": payload["options"],
                "stream": False,
            }
            resp = httpx.post(f"{base_url}/api/generate", json=gen_payload, timeout=90.0)

        resp.raise_for_status()
        result = resp.json()
        content = (
            result.get("message", {}).get("content")
            or result.get("response")
            or ""
        ).strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # Some models still prepend/append text; salvage first JSON object.
            match = re.search(r"\{[\s\S]*\}", content)
            if not match:
                raise
            parsed = json.loads(match.group(0))
    except httpx.ConnectError:
        print(f"ERROR: Cannot reach LLM at {base_url}. Is Ollama running on DGX Spark?", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        print(f"ERROR: LLM request failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"ERROR: LLM returned non-JSON: {exc}\nRaw: {content!r}", file=sys.stderr)
        sys.exit(1)

    return {
        **SCRIPT_TEMPLATE,
        **parsed,
        "duration_hint": duration,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
    }


def run(job_path: Path, dry_run: bool = False) -> Path:
    """
    Generate script for the job at `job_path`, write to output dir, return output path.
    """
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job_id = job["job_id"]
    script = generate_script(job, dry_run=dry_run)

    out_dir = ROOT / "output" / job_id / "en"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "script.json"
    out_path.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Script written → {out_path.relative_to(ROOT)}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexetra Media — Script generation agent")
    parser.add_argument("--job", required=True, help="Path to job JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Stub output, skip LLM call")
    args = parser.parse_args()
    run(Path(args.job), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
