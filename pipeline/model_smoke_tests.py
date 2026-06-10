"""
pipeline/model_smoke_tests.py
-----------------------------
One-command smoke tests for DGX-backed model capabilities and media pipeline outputs.

Coverage:
- Coding model inference (qwen2.5-coder:32b by default)
- Image model inference (llava:34b by default)
- Real TTS generation via pipeline/tts/agent.py
- Real assembly and export via pipeline/assembly/agent.py + pipeline/export/agent.py

USAGE:
  python pipeline/model_smoke_tests.py --job jobs/what-is-nexetra-live-es.json --image ../IMG_1840.jpeg
"""

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OLLAMA_URL = "http://10.0.0.7:11434"


def _post_chat(payload: dict, timeout: float = 300.0) -> dict:
    resp = httpx.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def test_coding_model(model: str) -> bool:
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Return only JSON with keys code and note. "
                    "code must be a Python function build_export_filters() returning crop filters."
                ),
            }
        ],
    }

    print(f"[coding] Testing model: {model}")
    try:
        result = _post_chat(payload, timeout=300.0)
        content = result.get("message", {}).get("content", "")
        data = json.loads(content)
        ok = isinstance(data.get("code"), str) and "def build_export_filters" in data.get("code", "")
        print("[coding] PASS" if ok else "[coding] FAIL")
        return ok
    except Exception as exc:
        print(f"[coding] FAIL: {exc}")
        return False


def test_image_model(model: str, image_path: Path) -> bool:
    if not image_path.exists():
        print(f"[image] FAIL: image not found: {image_path}")
        return False

    b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {
                "role": "user",
                "content": "Describe this image in JSON with keys: scene, objects, style.",
                "images": [b64],
            }
        ],
    }

    print(f"[image] Testing model: {model}")
    try:
        result = _post_chat(payload, timeout=300.0)
        content = result.get("message", {}).get("content", "")
        data = json.loads(content)
        ok = all(k in data for k in ("scene", "objects", "style"))
        print("[image] PASS" if ok else "[image] FAIL")
        return ok
    except Exception as exc:
        print(f"[image] FAIL: {exc}")
        return False


def run_pipeline_stage(script_rel: str, job: Path) -> bool:
    cmd = [sys.executable, script_rel, "--job", str(job)]
    print(f"[pipeline] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=ROOT)
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexetra media model + pipeline smoke tests")
    parser.add_argument("--job", required=True, help="Job JSON path")
    parser.add_argument("--image", required=True, help="Image path for vision model test")
    parser.add_argument("--coding-model", default="qwen2.5-coder:32b")
    parser.add_argument("--image-model", default="llava:34b")
    args = parser.parse_args()

    job = Path(args.job)
    image = Path(args.image)

    ok_coding = test_coding_model(args.coding_model)
    ok_image = test_image_model(args.image_model, image)

    print("[pipeline] Running live TTS -> assembly -> export")
    ok_tts = run_pipeline_stage("pipeline/tts/agent.py", job)
    ok_assembly = run_pipeline_stage("pipeline/assembly/agent.py", job)
    ok_export = run_pipeline_stage("pipeline/export/agent.py", job)

    all_ok = all([ok_coding, ok_image, ok_tts, ok_assembly, ok_export])
    print("\n=== SUMMARY ===")
    print(f"coding={ok_coding} image={ok_image} tts={ok_tts} assembly={ok_assembly} export={ok_export}")

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
