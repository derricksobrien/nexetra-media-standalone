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
import csv
import html
import io
import json
import os
import re
import tarfile
import threading
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

try:
    from viewer.archive_utils import should_exclude_archive_path
except ModuleNotFoundError:
    from archive_utils import should_exclude_archive_path

try:
    import paramiko
except Exception:  # pragma: no cover - handled by endpoint checks
    paramiko = None

ROOT = Path(__file__).resolve().parent.parent  # nexetra-media/
WORKSPACE_ROOT = ROOT.parent
HEALTH_FILE  = ROOT / "output" / "compute_pool" / "health-latest.json"
LEASES_FILE  = ROOT / "output" / "compute_pool" / "leases.json"
JOBS_DIR     = ROOT / "jobs"
OUTPUT_DIR   = ROOT / "output"
RUNS_LOG     = ROOT / "output" / "job_runs.jsonl"
THUNDERBOLT_LOGS = WORKSPACE_ROOT / "logs"
THUNDERBOLT_TELEMETRY = ROOT / "output" / "telemetry"

# Hosts that must never appear in the dashboard
PROTECTED_HOSTS = {"linux-1", "linux-2", "das-Mac-mini.local", "sql-server", "ubuntu-3"}

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


def _resolve_path(filename: str) -> Path:
    local = ROOT / filename
    if local.exists():
        return local
    return WORKSPACE_ROOT / filename


WORKSTATIONS_CSV = _resolve_path("workstations.csv")
SECRETS_FILE = _resolve_path("secrets.local.md")
MAC_BOOTSTRAP_SCRIPT = ROOT / "viewer" / "remote" / "bootstrap_mac.sh"
LINUX_BOOTSTRAP_SCRIPT = ROOT / "viewer" / "remote" / "bootstrap_linux.sh"

_REMEDIATION_RUNS: dict[str, dict] = {}
_REMEDIATION_LOCK = threading.Lock()


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


def _load_host_users() -> dict[str, str]:
    if not WORKSTATIONS_CSV.exists():
        return {}
    users: dict[str, str] = {}
    with WORKSTATIONS_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            user = (row.get("un") or "").strip()
            if name and user:
                users[name] = user
    return users


def _load_workstations() -> list[dict[str, str]]:
    if not WORKSTATIONS_CSV.exists():
        return []
    out: list[dict[str, str]] = []
    with WORKSTATIONS_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append({
                "name": (row.get("name") or "").strip(),
                "ip": (row.get("ip") or "").strip(),
                "user": (row.get("un") or "").strip(),
            })
    return out


def _latest_thunderbolt_probe_csv() -> Path | None:
    candidates: list[Path] = []
    for base in (THUNDERBOLT_LOGS, THUNDERBOLT_TELEMETRY):
        if not base.exists():
            continue
        candidates.extend(base.glob("thunderbolt-mlx-probe-*.csv"))

    if not candidates:
        return None

    files = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0]


def _thunderbolt_host_commands(name: str, ip: str, user: str, ready: bool) -> list[dict]:
    install_cmd = (
        f"ssh {user}@{ip} \"python3 -m pip install --user --upgrade mlx mlx-lm; "
        "command -v ollama >/dev/null 2>&1 || echo 'Install Ollama first'; "
        "curl -s http://127.0.0.1:11434/api/tags >/dev/null && echo OLLAMA_API_OK || echo OLLAMA_API_NOT_READY\""
    )
    return [
        {
            "label": "Inspect Thunderbolt bridge + link state",
            "command": (
                f"ssh {user}@{ip} \"networksetup -listallhardwareports; "
                "ifconfig bridge0 2>/dev/null || true; "
                "system_profiler SPThunderboltDataType | sed -n '1,80p'\""
            ),
        },
        {
            "label": "Run full fleet Thunderbolt/MLX probe from admin host",
            "command": "powershell -ExecutionPolicy Bypass -File .\\probe-lab-minis-thunderbolt-mlx.ps1",
        },
        {
            "label": "Install MLX runtime and verify inference prerequisites",
            "command": install_cmd if not ready else f"ssh {user}@{ip} \"python3 -c 'import mlx, mlx_lm; print(mlx.__version__, mlx_lm.__version__)'\"",
        },
    ]


def build_thunderbolt_status() -> dict:
    users = _load_host_users()
    workstations = _load_workstations()
    name_by_ip = {w["ip"]: w["name"] for w in workstations if w.get("ip") and w.get("name")}
    name_by_key = {(w["name"] or "").lower(): w["name"] for w in workstations if w.get("name")}
    mac_rows = [w for w in workstations if (w.get("name") or "").startswith("Lab-")]
    probe_file = _latest_thunderbolt_probe_csv()
    probe_by_host: dict[str, dict] = {}
    probe_tb_ip_by_host: dict[str, str] = {}

    if probe_file and probe_file.exists():
        try:
            with probe_file.open(encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    host = (row.get("Host") or "").strip()
                    if host:
                        probe_by_host[host] = row
                        tb_ip = (row.get("TB_IP") or "").strip()
                        if tb_ip and tb_ip.lower() != "none":
                            probe_tb_ip_by_host[tb_ip] = host
        except Exception:
            probe_by_host = {}
            probe_tb_ip_by_host = {}

    hosts: list[dict] = []
    links: list[dict] = []
    seen_links: set[tuple[str, str]] = set()

    for ws in mac_rows:
        name = ws["name"]
        ip = ws["ip"]
        user = ws["user"] or users.get(name, "user")
        row = probe_by_host.get(name, {})

        status = (row.get("Status") or "PENDING").upper()
        tb_link = (row.get("TB_Link") or "pending").lower()
        tb_bridge = row.get("TB_Bridge") or "unknown"
        tb_ip = row.get("TB_IP") or "unknown"
        mlx_lm = row.get("MLX_LM") or "unknown"
        mlx = row.get("MLX") or "unknown"

        try:
            inf_ready = int(row.get("InferenceReady") or 0)
        except Exception:
            inf_ready = 0

        try:
            wifi_was_on = int(row.get("WiFiWasOn") or 0)
        except Exception:
            wifi_was_on = -1  # -1 means no data yet

        connected_names: list[str] = []
        peers = (row.get("TB_Peers") or "").strip()
        if peers and peers.lower() != "none":
            for peer_ip in [x.strip() for x in peers.split(",") if x.strip()]:
                peer_name = (
                    name_by_ip.get(peer_ip)
                    or probe_tb_ip_by_host.get(peer_ip)
                    or name_by_key.get(peer_ip.lower())
                    or peer_ip
                )
                if peer_name == name:
                    continue
                connected_names.append(peer_name)
                pair = tuple(sorted((name, peer_name)))
                if pair not in seen_links:
                    links.append({"a": pair[0], "b": pair[1]})
                    seen_links.add(pair)

        summary = (
            "Probe complete and inference-ready"
            if status == "OK" and inf_ready == 1
            else "Thunderbolt link active but runtime not ready"
            if status == "OK" and tb_link == "good"
            else "Probe complete with partial link state"
            if status == "OK"
            else "Probe pending or failed"
        )

        hosts.append({
            "name": name,
            "ip": ip,
            "user": user,
            "status": status,
            "tb_link": tb_link,
            "tb_bridge": tb_bridge,
            "tb_ip": tb_ip,
            "mlx": mlx,
            "mlx_lm": mlx_lm,
            "inference_ready": bool(inf_ready),
            "connected_to": connected_names,
            "wifi_was_on": wifi_was_on,
            "summary": summary,
            "commands": _thunderbolt_host_commands(name, ip, user, bool(inf_ready)),
        })

    ready = sum(1 for h in hosts if h["inference_ready"])
    good_links = sum(1 for h in hosts if h["tb_link"] == "good")

    return {
        "source": str(probe_file) if probe_file else "",
        "hosts": hosts,
        "links": links,
        "summary": {
            "mac_hosts": len(hosts),
            "inference_ready": ready,
            "good_links": good_links,
        },
    }


def _repair_commands(name: str, host: dict, user: str) -> list[dict]:
    ip = host.get("ip", "")
    os_name = (host.get("os") or "unknown").lower()

    if not host.get("reachable"):
        return [
            {
                "label": "Check the network path first",
                "command": f"ping {ip}",
            },
            {
                "label": "Try SSH once the host answers ping",
                "command": f"ssh {user}@{ip}",
            },
        ]

    if os_name == "linux":
        return [
            {
                "label": "Preferred: run the fix directly from the dashboard button",
                "command": f"POST http://10.0.0.200:7800/api/remediate/{name}/run",
            },
            {
                "label": "CLI trigger from any Windows admin shell",
                "command": f"Invoke-WebRequest -Method Post http://10.0.0.200:7800/api/remediate/{name}/run -UseBasicParsing",
            },
            {
                "label": f"Fallback manual repair for {name}",
                "command": f"python viewer\\deploy.py --host {name}",
            },
            {
                "label": "Watch remediation status",
                "command": (
                    f"Invoke-WebRequest http://10.0.0.200:7800/api/remediate/{name}/status -UseBasicParsing "
                    "| Select-Object -ExpandProperty Content"
                ),
            },
        ]

    if os_name == "darwin":
        return [
            {
                "label": "Preferred: run the macOS bootstrap from the dashboard button",
                "command": f"POST http://10.0.0.200:7800/api/remediate/{name}/run",
            },
            {
                "label": "CLI trigger from any Windows admin shell",
                "command": f"Invoke-WebRequest -Method Post http://10.0.0.200:7800/api/remediate/{name}/run -UseBasicParsing",
            },
            {
                "label": "Manual host check",
                "command": f"ssh {user}@{ip} 'uname -s; test -x ~/nexetra-media/.venv/bin/python && echo PY_READY || echo PY_MISSING'",
            },
            {
                "label": "Watch remediation status",
                "command": (
                    f"Invoke-WebRequest http://10.0.0.200:7800/api/remediate/{name}/status -UseBasicParsing "
                    "| Select-Object -ExpandProperty Content"
                ),
            },
        ]

    return [
        {
            "label": "Inspect the host OS and deployment state",
            "command": f"ssh {user}@{ip} 'uname -a; test -d ~/nexetra-media && echo ROOT_PRESENT || echo ROOT_MISSING'",
        },
        {
            "label": "If it is a Linux viewer target, deploy the runtime",
            "command": f"python viewer\\deploy.py --host {name}",
        },
    ]


def build_remediations() -> list[dict]:
    health = get_health_data().get("hosts", {})
    users = _load_host_users()
    with _REMEDIATION_LOCK:
        run_states = dict(_REMEDIATION_RUNS)
    out: list[dict] = []

    for name, host in health.items():
        if host.get("remote_ready"):
            continue

        user = users.get(name, "user")
        commands = _repair_commands(name, host, user)
        out.append({
            "name": name,
            "ip": host.get("ip", ""),
            "os": host.get("os", "unknown"),
            "reachable": bool(host.get("reachable")),
            "remote_ready": bool(host.get("remote_ready")),
            "remote_root": host.get("remote_root", ""),
            "remote_python": bool(host.get("remote_python")),
            "user": user,
            "summary": (
                "Linux host missing the viewer runtime" if host.get("os") == "Linux"
                else "macOS host missing nexetra-media bootstrap" if host.get("os") == "Darwin"
                else "Reachable host that still needs manual inspection"
            ),
            "commands": commands,
            "run_endpoint": f"/api/remediate/{quote(name)}/run",
            "status_endpoint": f"/api/remediate/{quote(name)}/status",
            "run_state": run_states.get(name, {"status": "idle"}),
        })

    return out


def get_active_leases() -> list[dict]:
    raw = _read_json(LEASES_FILE) or {}
    now = int(time.time())
    return [x for x in raw.get("leases", []) if x.get("expires_at", 0) > now]


def _parse_secrets(path: Path) -> dict[tuple[str, str], str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"\n###\s+", text)
    out: dict[tuple[str, str], str] = {}
    for block in blocks:
        ip_m = re.search(r"^-\s*IP:\s*(.+)$", block, re.MULTILINE)
        user_m = re.search(r"^-\s*Username:\s*(.+)$", block, re.MULTILINE)
        pass_m = re.search(r"^-\s*Password:\s*(.+)$", block, re.MULTILINE)
        if not (ip_m and user_m and pass_m):
            continue
        out[(ip_m.group(1).strip().lower(), user_m.group(1).strip().lower())] = pass_m.group(1).strip()
    return out


def _load_host_credentials(name: str) -> tuple[str, str, str]:
    if not WORKSTATIONS_CSV.exists():
        raise RuntimeError(f"Missing inventory file: {WORKSTATIONS_CSV}")

    secrets = _parse_secrets(SECRETS_FILE)
    with WORKSTATIONS_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("name") or "").strip() != name:
                continue
            ip = (row.get("ip") or "").strip()
            user = (row.get("un") or "").strip()
            pwd = secrets.get((ip.lower(), user.lower()))
            if not pwd:
                raise RuntimeError(f"No password found for {name} ({ip} / {user})")
            return ip, user, pwd
    raise RuntimeError(f"Host '{name}' not found in {WORKSTATIONS_CSV}")


def _make_archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in ROOT.rglob("*"):
            rel = p.relative_to(ROOT)
            if should_exclude_archive_path(rel):
                continue
            if p.is_file():
                tf.add(p, arcname=str(rel))
    return buf.getvalue()


def _ssh_run(client: "paramiko.SSHClient", cmd: str, timeout: int = 900) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="ignore")
    err = stderr.read().decode("utf-8", errors="ignore")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def _upload_payload(client: "paramiko.SSHClient", archive: bytes, script_path: Path, remote_script: str) -> None:
    sftp = client.open_sftp()
    try:
        with sftp.open("/tmp/nexetra-media-sync.tgz", "wb") as f:
            f.write(archive)
        with sftp.open(remote_script, "wb") as f:
            f.write(script_path.read_bytes())
    finally:
        sftp.close()


def _bootstrap_linux(client: "paramiko.SSHClient", archive: bytes) -> tuple[int, str, str]:
    remote_script = "/tmp/nexetra-bootstrap-linux.sh"
    _upload_payload(client, archive, LINUX_BOOTSTRAP_SCRIPT, remote_script)
    return _ssh_run(client, f"chmod +x {remote_script}; {remote_script} /tmp/nexetra-media-sync.tgz", timeout=1800)


def _bootstrap_mac(client: "paramiko.SSHClient", archive: bytes) -> tuple[int, str, str]:
    remote_script = "/tmp/nexetra-bootstrap-mac.sh"
    _upload_payload(client, archive, MAC_BOOTSTRAP_SCRIPT, remote_script)
    return _ssh_run(client, f"chmod +x {remote_script}; {remote_script} /tmp/nexetra-media-sync.tgz", timeout=1800)


def _set_run_state(host: str, update: dict) -> None:
    with _REMEDIATION_LOCK:
        existing = _REMEDIATION_RUNS.get(host, {})
        existing.update(update)
        _REMEDIATION_RUNS[host] = existing


def _run_remediation(host: str) -> None:
    run_id = str(uuid.uuid4())
    _set_run_state(host, {
        "run_id": run_id,
        "host": host,
        "status": "running",
        "started_at": time.time(),
        "ended_at": 0,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "error": "",
    })

    if paramiko is None:
        _set_run_state(host, {
            "status": "failed",
            "ended_at": time.time(),
            "error": "paramiko is not installed on dashboard host",
        })
        return

    client = None
    try:
        ip, user, pwd = _load_host_credentials(host)
        health = get_health_data().get("hosts", {}).get(host, {})
        os_name = (health.get("os") or "").lower()

        archive = _make_archive()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            ip,
            username=user,
            password=pwd,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )

        if os_name == "darwin":
            rc, out, err = _bootstrap_mac(client, archive)
        else:
            rc, out, err = _bootstrap_linux(client, archive)

        _set_run_state(host, {
            "status": "done" if rc == 0 else "failed",
            "ended_at": time.time(),
            "exit_code": rc,
            "stdout": out[-8000:],
            "stderr": err[-4000:],
        })
    except Exception as exc:
        _set_run_state(host, {
            "status": "failed",
            "ended_at": time.time(),
            "error": str(exc),
        })
    finally:
        if client is not None:
            client.close()


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
        "remediations": build_remediations(),
        "thunderbolt": build_thunderbolt_status(),
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


@app.post("/api/remediate/{host}/run")
async def api_run_remediation(host: str):
    hosts = get_health_data().get("hosts", {})
    if host not in hosts:
        raise HTTPException(status_code=404, detail=f"Unknown host: {host}")

    with _REMEDIATION_LOCK:
        active = _REMEDIATION_RUNS.get(host)
        if active and active.get("status") == "running":
            return {"ok": True, "host": host, "status": "running", "run_id": active.get("run_id")}

    t = threading.Thread(target=_run_remediation, args=(host,), daemon=True)
    t.start()
    return {"ok": True, "host": host, "status": "started"}


@app.get("/api/remediate/{host}/status")
async def api_remediation_status(host: str):
    with _REMEDIATION_LOCK:
        run = _REMEDIATION_RUNS.get(host)
    if not run:
        return {"host": host, "status": "idle"}
    return run


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


@app.post("/api/upload-telemetry")
async def api_upload_telemetry(request: Request):
    """Accept a raw CSV body (text/csv) and persist it as the latest thunderbolt probe snapshot."""
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty body")
    THUNDERBOLT_TELEMETRY.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    latest = THUNDERBOLT_TELEMETRY / "thunderbolt-mlx-probe-latest.csv"
    stamped = THUNDERBOLT_TELEMETRY / f"thunderbolt-mlx-probe-{ts}.csv"
    latest.write_bytes(body)
    stamped.write_bytes(body)
    return {"ok": True, "saved": str(latest), "timestamp": ts}


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
