"""
viewer/app.py
─────────────────────────────────────────
Nexetra Media — real-time compute dashboard.

Reads:
  output/compute_pool/health-latest.json  — node health
  output/compute_pool/leases.json         — active batch leases
  output/job_runs.jsonl                   — event history (written by run_batch_pool)
  jobs/*.json                             — job definitions / goals

Start (from nexetra-media/):
  python viewer/app.py
  NEXETRA_VIEWER_PORT=7800 python viewer/app.py

URL:  http://localhost:7800
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import time
from pathlib import Path
from typing import AsyncGenerator
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

ROOT = Path(__file__).resolve().parent.parent  # nexetra-media/
HEALTH_FILE  = ROOT / "output" / "compute_pool" / "health-latest.json"
LEASES_FILE  = ROOT / "output" / "compute_pool" / "leases.json"
JOBS_DIR     = ROOT / "jobs"
OUTPUT_DIR   = ROOT / "output"
RUNS_LOG     = ROOT / "output" / "job_runs.jsonl"

# Hosts that must never appear in the dashboard
PROTECTED_HOSTS = {"linux-1", "linux-2", "das-Mac-mini.local", "sql-server"}

STAGES = ["scriptgen", "translate", "tts", "assembly", "export"]

DEFAULT_STAGE_ESTIMATES_MINUTES = {
    "scriptgen": 0.5,
    "translate": 0.3,
    "tts": 0.2,
    "assembly": 0.5,
    "export": 0.5,
}

# Returns True when the stage has produced at least one artifact
STAGE_CHECKS: dict[str, object] = {
    "scriptgen": lambda d: (d / "en" / "script.json").exists(),
    "translate":  lambda d: any((d / lg / "script.json").exists()
                                for lg in ("es", "fr", "de", "ja", "zh", "ar", "pt")),
    "tts":        lambda d: any((d / lg / "audio.wav").exists()
                                for lg in ("en", "es", "fr", "de", "ja", "zh", "ar", "pt")),
    "assembly":   lambda d: any((d / lg / "master_16x9.mp4").exists()
                                for lg in ("en", "es", "fr", "de", "ja", "zh", "ar", "pt")),
    "export":     lambda d: any((d / lg / "16x9.mp4").exists()
                                for lg in ("en", "es", "fr", "de", "ja", "zh", "ar", "pt")),
}

app = FastAPI(title="Nexetra Media Dashboard", docs_url=None, redoc_url=None)
_TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"


def _render_html(snapshot: dict) -> str:
    html = _TEMPLATE_PATH.read_text(encoding="utf-8")
    safe_json = json.dumps(snapshot).replace("</script>", "<\\/script>")
    return html.replace("{{ snapshot_json | safe }}", safe_json)


# ─── helpers ───────────────────────────────────────────────────────────────

def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_health_data() -> dict:
    raw = _read_json(HEALTH_FILE) or {}
    hosts = raw.get("hosts", {})
    return {
        "generated_at": raw.get("generated_at", ""),
        "hosts": {k: v for k, v in hosts.items() if k not in PROTECTED_HOSTS},
    }


def get_active_leases() -> list[dict]:
    raw = _read_json(LEASES_FILE) or {}
    now = int(time.time())
    return [x for x in raw.get("leases", []) if x.get("expires_at", 0) > now]


def _safe_output_path(rel_path: str) -> Path:
    root = OUTPUT_DIR.resolve()
    target = (OUTPUT_DIR / Path(rel_path)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Invalid output path") from exc
    return target


def _route_path(rel_path: str) -> str:
    return quote(Path(rel_path).as_posix(), safe="/")


def _estimate_minutes(job: dict, stage: str) -> float:
    estimates = job.get("stage_estimates_minutes")
    if isinstance(estimates, dict):
        value = estimates.get(stage)
        if isinstance(value, (int, float)):
            return float(value)

    estimates = job.get("stage_estimates_seconds")
    if isinstance(estimates, dict):
        value = estimates.get(stage)
        if isinstance(value, (int, float)):
            return float(value) / 60.0

    duration = float(job.get("duration_seconds", 60) or 60)
    scale = max(duration, 1.0) / 60.0
    return DEFAULT_STAGE_ESTIMATES_MINUTES.get(stage, 0.5) * scale


def _job_estimates(job: dict) -> tuple[list[dict], float]:
    stage_rows = []
    total = 0.0
    for stage in STAGES:
        mins = round(_estimate_minutes(job, stage), 1)
        total += mins
        stage_rows.append({"stage": stage, "minutes": mins})
    return stage_rows, round(total, 1)


def _collect_output_items(job_id: str, limit: int = 60) -> list[dict]:
    root = OUTPUT_DIR / job_id
    if not root.exists():
        return []

    items: list[dict] = []
    seen: set[str] = set()

    def add_entry(path: Path, label: str, kind: str) -> None:
        rel = path.relative_to(OUTPUT_DIR).as_posix()
        if rel in seen:
            return
        seen.add(rel)
        items.append({
            "label": label,
            "rel_path": rel,
            "href": f"/browse/{_route_path(rel)}" if kind == "folder" else f"/download/{_route_path(rel)}",
            "kind": kind,
        })

    add_entry(root, f"output/{job_id}", "folder")

    for child in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if len(items) >= limit:
            break
        if child.is_dir():
            add_entry(child, child.relative_to(OUTPUT_DIR).as_posix(), "folder")
            for grandchild in sorted(child.rglob("*"), key=lambda p: (p.is_dir(), p.name.lower())):
                if len(items) >= limit:
                    break
                if grandchild.is_file():
                    add_entry(grandchild, grandchild.relative_to(OUTPUT_DIR).as_posix(), "file")
        elif child.is_file():
            add_entry(child, child.relative_to(OUTPUT_DIR).as_posix(), "file")

    return items


def _job_goal(job: dict) -> str:
    title    = job.get("title", "")
    langs    = " + ".join(lg.upper() for lg in job.get("languages", []))
    formats  = ", ".join(job.get("formats", []))
    cta      = job.get("cta", "")
    duration = job.get("duration_seconds", "?")
    style    = job.get("style", "slides")
    return (
        f"Produce a {duration}s {style} promo for [{title}] in {langs}. "
        f"Export formats: {formats}. CTA: {cta}."
    )


def get_jobs() -> list[dict]:
    jobs = []
    for p in sorted(JOBS_DIR.glob("*.json")):
        try:
            job    = json.loads(p.read_text(encoding="utf-8"))
            job_id = job.get("job_id", p.stem)
            out_dir = OUTPUT_DIR / job_id
            stages_done = [s for s, fn in STAGE_CHECKS.items()
                           if out_dir.exists() and fn(out_dir)]
            stage_estimates, estimated_total_minutes = _job_estimates(job)
            jobs.append({
                "job_id":      job_id,
                "title":       job.get("title", job_id),
                "goal":        _job_goal(job),
                "languages":   job.get("languages", []),
                "formats":     job.get("formats", []),
                "status":      job.get("status", "draft"),
                "cta":         job.get("cta", ""),
                "stages_done": stages_done,
                "stages_total": len(STAGES),
                "progress":    int(len(stages_done) / len(STAGES) * 100),
                "estimated_stage_minutes": stage_estimates,
                "estimated_total_minutes": estimated_total_minutes,
                "output_items": _collect_output_items(job_id),
            })
        except Exception:
            pass
    return jobs


def get_history(n: int = 120) -> list[dict]:
    if not RUNS_LOG.exists():
        return []
    lines = RUNS_LOG.read_text(encoding="utf-8").splitlines()
    out = []
    for line in reversed(lines[-n:]):
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out[:60]


def get_run_archive(n: int = 60) -> list[dict]:
    history = get_history(n=n)
    ordered = list(reversed(history))
    open_runs: dict[tuple[str, str], dict] = {}
    runs: list[dict] = []

    for event in ordered:
        job = event.get("job", "")
        runner = event.get("runner", "") or "unknown"
        run_key = (job, runner)
        ev_name = event.get("event", "")

        if ev_name == "batch_start":
            record = {
                "job": job,
                "runner": runner,
                "status": "running",
                "started_at": event.get("ts", 0),
                "ended_at": 0,
                "events": 1,
                "last_event": ev_name,
                "last_stage": event.get("stage", ""),
            }
            runs.append(record)
            open_runs[run_key] = record
            continue

        record = open_runs.get(run_key)
        if record is None:
            record = {
                "job": job,
                "runner": runner,
                "status": "running",
                "started_at": event.get("ts", 0),
                "ended_at": 0,
                "events": 0,
                "last_event": "",
                "last_stage": "",
            }
            runs.append(record)
            open_runs[run_key] = record

        record["events"] += 1
        record["last_event"] = ev_name
        record["last_stage"] = event.get("stage", "")

        if ev_name in {"batch_done", "batch_fail"}:
            record["status"] = "done" if ev_name == "batch_done" else "failed"
            record["ended_at"] = event.get("ts", 0)
            open_runs.pop(run_key, None)

    return list(reversed(runs[-n:]))


def build_snapshot() -> dict:
    return {
        "health":  get_health_data(),
        "leases":  get_active_leases(),
        "jobs":    get_jobs(),
        "history": get_history(),
        "runs":    get_run_archive(),
        "ts":      time.time(),
    }


def _render_directory_listing(rel_path: str, path: Path) -> str:
    entries = []
    if path != OUTPUT_DIR:
        parent_rel = path.parent.relative_to(OUTPUT_DIR).as_posix()
        entries.append(f'<li><a href="/browse/{_route_path(parent_rel)}">..</a></li>')
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        child_rel = child.relative_to(OUTPUT_DIR).as_posix()
        if child.is_dir():
            entries.append(
                f'<li>[DIR] <a href="/browse/{_route_path(child_rel)}">{html.escape(child.name)}</a></li>'
            )
        else:
            entries.append(
                f'<li>[FILE] <a href="/download/{_route_path(child_rel)}">{html.escape(child.name)}</a></li>'
            )

    items = "".join(entries) or "<li><em>Empty folder</em></li>"
    title = html.escape(rel_path or "output")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>{title} · Nexetra Output</title>
    <style>
        body{{background:#0b0f1a;color:#c8d6f0;font-family:system-ui,sans-serif;margin:0;padding:24px}}
        a{{color:#3b82f6;text-decoration:none}}
        .card{{max-width:980px;margin:0 auto;background:#131929;border:1px solid #253250;border-radius:12px;padding:20px}}
        h1{{margin:0 0 8px;font-size:20px}}
        .path{{color:#6b7ea8;font-family:monospace;margin-bottom:16px}}
        ul{{list-style:none;padding:0;margin:0;display:grid;gap:8px}}
        li{{padding:10px 12px;background:#1a2338;border:1px solid #253250;border-radius:8px;overflow:hidden;text-overflow:ellipsis}}
        .hint{{margin-top:16px;color:#6b7ea8;font-size:13px}}
    </style>
</head>
<body>
    <div class="card">
        <h1>Output Browser</h1>
        <div class="path">/{title}</div>
        <ul>{items}</ul>
        <div class="hint">Use this page to browse folders or download generated assets directly.</div>
    </div>
</body>
</html>"""


# ─── routes ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    snapshot = build_snapshot()
    return HTMLResponse(content=_render_html(snapshot))


@app.get("/api/snapshot")
async def api_snapshot():
    return build_snapshot()


@app.get("/browse/{rel_path:path}", response_class=HTMLResponse)
async def browse_output(rel_path: str = ""):
    target = _safe_output_path(rel_path or ".")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Output path not found")
    if target.is_file():
        return FileResponse(target, filename=target.name)
    return HTMLResponse(content=_render_directory_listing(rel_path, target))


@app.get("/download/{rel_path:path}")
async def download_output(rel_path: str):
    target = _safe_output_path(rel_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, filename=target.name)


async def _sse_generator() -> AsyncGenerator[str, None]:
    while True:
        try:
            data = json.dumps(build_snapshot())
            yield f"data: {data}\n\n"
        except Exception as exc:
            yield f"data: {{\"error\": \"{exc}\"}}\n\n"
        await asyncio.sleep(4)


@app.get("/events")
async def sse_events():
    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("NEXETRA_VIEWER_PORT", "7800"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
