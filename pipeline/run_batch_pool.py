"""
pipeline/run_batch_pool.py
--------------------------
Pool-aware batch runner for Nexetra Media.

Goals:
- Use DGX + free Lab-Station and ubuntu hosts during batch operations.
- Never schedule work to protected hosts.
- Always release compute at the end of a batch so machines return to the lab pool.

Usage:
  python pipeline/run_batch_pool.py --job jobs/what-is-nexetra-live-es.json
  python pipeline/run_batch_pool.py --job jobs/what-is-nexetra-live-es.json --dry-run
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import socket
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

import paramiko
import yaml

try:
    from pipeline.run_history import append_run_event
    from pipeline.job_control import validate_job_path
    from pipeline.handler_registry import get_handler
    from pipeline.execution import (
        RunManifest,
        ensure_executable_job,
        evaluate_stage_artifacts,
        evaluate_success_criteria,
        make_stage_result,
    )
    from pipeline.provenance import source_files
except ModuleNotFoundError:
    from run_history import append_run_event
    from job_control import validate_job_path
    from handler_registry import get_handler
    from execution import (
        RunManifest,
        ensure_executable_job,
        evaluate_stage_artifacts,
        evaluate_success_criteria,
        make_stage_result,
    )
    from provenance import source_files


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = REPO_ROOT.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
LEASES_FILE = REPO_ROOT / "output" / "compute_pool" / "leases.json"
RUNS_LOG = REPO_ROOT / "output" / "job_runs.jsonl"


def _resolve_path(filename: str) -> Path:
    """Prefer repo-local config files; fall back to legacy parent layout."""
    local = REPO_ROOT / filename
    if local.exists():
        return local
    return WORKSPACE_ROOT / filename


WORKSTATIONS_CSV = _resolve_path("workstations.csv")
SECRETS_FILE = _resolve_path("secrets.local.md")


def _log_event(
    job: str,
    event: str,
    stage: str = "",
    host: str = "",
    detail: str = "",
    run_id: str = "",
    provenance: dict | None = None,
) -> None:
    """Append a JSON event line to job_runs.jsonl (best-effort, never raises)."""
    append_run_event({
        "job": job,
        "event": event,
        "stage": stage,
        "host": host,
        "detail": detail,
        "runner": "run_batch_pool",
        "run_id": run_id,
        **(provenance or {}),
    })


STAGES = [
    ("scriptgen", "pipeline/scriptgen/agent.py"),
    ("translate", "pipeline/translate/agent.py"),
    ("tts", "pipeline/tts/agent.py"),
    ("assembly", "pipeline/assembly/agent.py"),
    ("export", "pipeline/export/agent.py"),
]


@dataclass
class Host:
    name: str
    ip: str
    user: str
    password: str


def load_job_id(job_path: str) -> str:
    path = REPO_ROOT / job_path
    if not path.exists():
        raise FileNotFoundError(f"Job file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        job = json.load(f)
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        raise ValueError(f"job_id missing in job file: {path}")
    return job_id


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def parse_secrets(path: Path) -> dict[tuple[str, str], str]:
    if not path.exists():
        raise FileNotFoundError(f"Secrets file not found: {path}")

    text = path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"\n###\s+", text)
    out: dict[tuple[str, str], str] = {}

    for block in blocks:
        ip_match = re.search(r"^-\s*IP:\s*(.+)$", block, flags=re.MULTILINE)
        user_match = re.search(r"^-\s*Username:\s*(.+)$", block, flags=re.MULTILINE)
        pass_match = re.search(r"^-\s*Password:\s*(.+)$", block, flags=re.MULTILINE)
        if not (ip_match and user_match and pass_match):
            continue

        ip = ip_match.group(1).strip()
        user = user_match.group(1).strip()
        password = pass_match.group(1).strip()
        out[(ip.lower(), user.lower())] = password

    return out


def load_hosts(deny_hosts: set[str], allow_patterns: list[str]) -> list[Host]:
    if not WORKSTATIONS_CSV.exists():
        raise FileNotFoundError(f"Missing inventory: {WORKSTATIONS_CSV}")

    secrets = parse_secrets(SECRETS_FILE)
    out: list[Host] = []

    regexes = [re.compile(p) for p in allow_patterns]

    with WORKSTATIONS_CSV.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            ip = (row.get("ip") or "").strip()
            user = (row.get("un") or "").strip()

            if not name or not ip or not user:
                continue
            if name in deny_hosts:
                continue
            if not any(r.search(name) for r in regexes):
                continue

            key = (ip.lower(), user.lower())
            pwd = secrets.get(key)
            if not pwd:
                continue

            out.append(Host(name=name, ip=ip, user=user, password=pwd))

    return out


def tcp_open(ip: str, port: int = 22, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def remote_run(host: Host, command: str, timeout: int = 300) -> tuple[int, str, str]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            host.ip,
            username=host.user,
            password=host.password,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
            look_for_keys=False,
            allow_agent=False,
        )
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="ignore")
        err = stderr.read().decode("utf-8", errors="ignore")
        status = stdout.channel.recv_exit_status()
        return status, out, err
    finally:
        try:
            client.close()
        except Exception:
            pass


def _connect_ssh(host: Host) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host.ip,
        username=host.user,
        password=host.password,
        timeout=10,
        banner_timeout=10,
        auth_timeout=10,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def _sftp_download_tree(sftp: paramiko.SFTPClient, remote_dir: str, local_dir: Path) -> None:
    local_dir.mkdir(parents=True, exist_ok=True)
    for entry in sftp.listdir_attr(remote_dir):
        remote_path = f"{remote_dir.rstrip('/')}/{entry.filename}"
        local_path = local_dir / entry.filename
        if entry.st_mode & 0o40000:
            _sftp_download_tree(sftp, remote_path, local_path)
        else:
            sftp.get(remote_path, str(local_path))


def sync_remote_artifacts(host: Host, remote_root: str, job_id: str, run_id: str) -> tuple[bool, str]:
    remote_job_dir = f"{remote_root.rstrip('/')}/output/{job_id}/runs/{run_id}"
    local_job_dir = REPO_ROOT / "output" / job_id / "runs" / run_id
    client = None
    sftp = None
    try:
        client = _connect_ssh(host)
        sftp = client.open_sftp()
        _sftp_download_tree(sftp, remote_job_dir, local_job_dir)
        return True, f"synced {remote_job_dir} -> {local_job_dir}"
    except Exception as exc:
        return False, str(exc)
    finally:
        if sftp is not None:
            try:
                sftp.close()
            except Exception:
                pass
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _worker_source_archive() -> bytes:
    buffer = io.BytesIO()
    paths = source_files(REPO_ROOT)
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in paths:
            archive.add(path, arcname=path.relative_to(REPO_ROOT).as_posix())
        manifest_bytes = json.dumps({
            "files": [path.relative_to(REPO_ROOT).as_posix() for path in paths],
        }, indent=2).encode("utf-8")
        info = tarfile.TarInfo(".nexetra-source-manifest.json")
        info.size = len(manifest_bytes)
        info.mtime = int(time.time())
        archive.addfile(info, io.BytesIO(manifest_bytes))
    return buffer.getvalue()


def sync_worker_source(host: Host, remote_root: str, source_sha_value: str) -> tuple[bool, str]:
    remote_archive = f"/tmp/nexetra-media-{source_sha_value[:16]}.tgz"
    client = None
    sftp = None
    try:
        payload = _worker_source_archive()
        client = _connect_ssh(host)
        sftp = client.open_sftp()
        with sftp.open(remote_archive, "wb") as handle:
            handle.write(payload)
        command = (
            f"mkdir -p '{remote_root}' && "
            f"tar -xzf '{remote_archive}' -C '{remote_root}' && "
            f"rm -f '{remote_archive}'"
        )
        stdin, stdout, stderr = client.exec_command(command, timeout=180)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return code == 0, (err or out or f"uploaded {len(payload)} bytes").strip()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if sftp is not None:
            sftp.close()
        if client is not None:
            client.close()


def sync_run_record(host: Host, remote_root: str, manifest: RunManifest) -> tuple[bool, str]:
    remote_dir = f"{remote_root.rstrip('/')}/output/runs/{manifest.run_id}"
    client = None
    sftp = None
    try:
        code, _, error = remote_run(host, f"mkdir -p '{remote_dir}'", timeout=30)
        if code != 0:
            return False, error
        client = _connect_ssh(host)
        sftp = client.open_sftp()
        sftp.put(str(manifest.path), f"{remote_dir}/run_manifest.json")
        summary = manifest.run_dir / "run_summary.md"
        if summary.is_file():
            sftp.put(str(summary), f"{remote_dir}/run_summary.md")
        return True, f"synced run record to {remote_dir}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if sftp is not None:
            sftp.close()
        if client is not None:
            client.close()


def run_worker_preflight(
    host: Host,
    remote_root: str,
    job: str,
    job_id: str,
    run_id: str,
) -> tuple[int, dict, str]:
    job_arg = job.replace("\\", "/")
    artifact_root = f"{remote_root.rstrip('/')}/output/{job_id}/runs/{run_id}"
    command = (
        f"cd '{remote_root}' && .venv/bin/python pipeline/worker_preflight.py "
        f"--job '{job_arg}' --artifact-root '{artifact_root}'"
    )
    try:
        code, out, err = remote_run(host, command, timeout=180)
        report = json.loads(out) if out.strip() else {}
        return code, report, err[-4000:]
    except Exception as exc:
        return -1, {}, f"{type(exc).__name__}: {exc}"


def worker_preflight_matches(
    report: dict,
    expected_provenance: dict,
    required_stages: list[str],
    required_models: list[str] | None = None,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    actual = report.get("provenance", {})
    for key in ("source_sha", "job_sha", "dependency_sha"):
        if actual.get(key) != expected_provenance.get(key):
            errors.append(f"{key} mismatch")
    if not report.get("artifact_writable"):
        errors.append("artifact root is not writable")
    capabilities = report.get("capabilities", {})
    for stage in required_stages:
        if not capabilities.get(stage):
            errors.append(f"missing stage capability: {stage}")
    available_models = set(report.get("models", {}).get("ollama", []))
    for model in required_models or []:
        if model not in available_models:
            errors.append(f"missing model: {model}")
    return not errors, errors


def load_leases() -> dict:
    if not LEASES_FILE.exists():
        return {"leases": []}
    with LEASES_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_leases(data: dict) -> None:
    LEASES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LEASES_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def acquire_hosts(candidates: list[Host], job: str, limit: int | None = None) -> list[Host]:
    lease_data = load_leases()
    now = int(time.time())

    active_names = {
        x["name"]
        for x in lease_data.get("leases", [])
        if int(x.get("expires_at", 0)) > now
    }

    available = [h for h in candidates if h.name not in active_names and tcp_open(h.ip, 22)]

    selected = available[:limit] if limit else available
    ttl = 2 * 60 * 60
    for host in selected:
        lease_data.setdefault("leases", []).append(
            {
                "name": host.name,
                "ip": host.ip,
                "job": job,
                "acquired_at": now,
                "expires_at": now + ttl,
            }
        )

    save_leases(lease_data)
    return selected


def release_hosts(used_hosts: list[Host], job: str) -> None:
    # Return remote compute to pool by unloading active Ollama models if present.
    cleanup_cmd = (
        "if command -v ollama >/dev/null 2>&1; "
        "then ollama ps | awk 'NR>1 {print $1}' | xargs -r -n1 ollama stop; fi"
    )

    for host in used_hosts:
        try:
            code, out, err = remote_run(host, cleanup_cmd, timeout=120)
            print(f"[release] {host.name} exit={code}")
            if out.strip():
                print(out.strip())
            if err.strip():
                print(err.strip())
        except Exception as exc:
            print(f"[release] {host.name} cleanup failed: {exc}")

    lease_data = load_leases()
    lease_data["leases"] = [
        x
        for x in lease_data.get("leases", [])
        if not (x.get("job") == job and any(h.name == x.get("name") for h in used_hosts))
    ]
    save_leases(lease_data)


def probe_host_health(host: Host, remote_roots: list[str]) -> dict:
    root_candidates = " ".join([f'"{p}"' for p in remote_roots])
    cmd = (
        "set +e; "
        "echo OS:$(uname -s); "
        "if command -v ollama >/dev/null 2>&1; then echo OLLAMA:$(ollama --version | head -n1); else echo OLLAMA:MISSING; fi; "
        "if [ -x ~/.venvs/vllm/bin/python ]; then echo VLLM:$(~/.venvs/vllm/bin/python -c 'import vllm; print(vllm.__version__)' 2>/dev/null); else echo VLLM:MISSING; fi; "
        "ROOT=''; "
        f"for p in {root_candidates}; do case \"$p\" in \"~/\"*) p=\"$HOME/${{p#??}}\" ;; esac; if [ -d \"$p\" ]; then ROOT=$p; break; fi; done; "
        "if [ -n \"$ROOT\" ]; then echo ROOT:$ROOT; else echo ROOT:MISSING; fi; "
        "if [ -n \"$ROOT\" ] && [ -x \"$ROOT/.venv/bin/python\" ]; then echo PYTHON:READY; else echo PYTHON:MISSING; fi"
    )

    health = {
        "name": host.name,
        "ip": host.ip,
        "reachable": False,
        "os": "unknown",
        "ollama": False,
        "ollama_version": "",
        "vllm": False,
        "vllm_version": "",
        "remote_root": "",
        "remote_python": False,
        "remote_ready": False,
        "error": "",
    }

    try:
        code, out, err = remote_run(host, cmd, timeout=90)
        text = (out or "") + "\n" + (err or "")
        health["reachable"] = code == 0 or bool(text.strip())

        for line in text.splitlines():
            line = line.strip()
            if line.startswith("OS:"):
                health["os"] = line.split(":", 1)[1].strip()
            elif line.startswith("OLLAMA:"):
                val = line.split(":", 1)[1].strip()
                health["ollama"] = val != "MISSING"
                health["ollama_version"] = "" if val == "MISSING" else val
            elif line.startswith("VLLM:"):
                val = line.split(":", 1)[1].strip()
                health["vllm"] = val not in {"", "MISSING"}
                health["vllm_version"] = "" if val == "MISSING" else val
            elif line.startswith("ROOT:"):
                val = line.split(":", 1)[1].strip()
                health["remote_root"] = "" if val == "MISSING" else val
            elif line.startswith("PYTHON:"):
                val = line.split(":", 1)[1].strip()
                health["remote_python"] = val == "READY"

        health["remote_ready"] = bool(health["remote_root"] and health["remote_python"])
    except Exception as exc:
        health["error"] = str(exc)

    return health


def collect_health(hosts: list[Host], remote_roots: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for host in hosts:
        out[host.name] = probe_host_health(host, remote_roots)
    return out


def write_health_report(data: dict[str, dict]) -> Path:
    out_dir = REPO_ROOT / "output" / "compute_pool"
    out_dir.mkdir(parents=True, exist_ok=True)
    latest = out_dir / "health-latest.json"
    stamp = out_dir / f"health-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    payload = {
        "generated_at": dt.datetime.now().isoformat(),
        "hosts": data,
    }
    latest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    stamp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return latest


def run_local_stage(stage: str, script: str, job: str, dry_run: bool, output_dir: Path) -> tuple[int, str]:
    cmd = [sys.executable, script, "--job", job]
    if dry_run:
        cmd.append("--dry-run")

    print(f"\n=== Stage: {stage} (local) ===")
    environment = os.environ.copy()
    environment["NEXETRA_JOB_OUTPUT_DIR"] = str(output_dir)
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    return result.returncode, (result.stderr or result.stdout)[-4000:]


def run_remote_stage(
    stage: str,
    script: str,
    host: Host,
    job: str,
    dry_run: bool,
    remote_roots: list[str],
    remote_root: str,
    job_id: str,
    run_id: str,
) -> tuple[int, str]:
    dry = " --dry-run" if dry_run else ""
    job_arg = job.replace("\\", "/")
    artifact_root = f"{remote_root.rstrip('/')}/output/{job_id}/runs/{run_id}"
    cmd = (
        f"cd '{remote_root}' && NEXETRA_JOB_OUTPUT_DIR='{artifact_root}' .venv/bin/python "
        f"{script} --job '{job_arg}'{dry}"
    )

    print(f"\n=== Stage: {stage} (remote: {host.name}) ===")
    try:
        code, out, err = remote_run(host, cmd, timeout=1800)
        if out.strip():
            print(out.strip())
        if err.strip():
            print(err.strip())
        return code, (err or out)[-4000:]
    except Exception as exc:
        print(f"Remote stage error on {host.name}: {exc}")
        return -1, str(exc)


def pick_stage_host(stage: str, hosts: list[Host], health: dict[str, dict]) -> Host | None:
    if not hosts:
        return None

    ready = [h for h in hosts if health.get(h.name, {}).get("remote_ready")]
    if not ready:
        return None

    dgx = next((h for h in ready if h.name.startswith("gx10")), None)
    workers = [h for h in ready if not h.name.startswith("gx10")]
    ollama_hosts = [h for h in ready if health.get(h.name, {}).get("ollama")]
    dgx_ollama = next((h for h in ollama_hosts if h.name.startswith("gx10")), None)

    if stage in {"scriptgen", "translate"}:
        if dgx_ollama:
            return dgx_ollama
        if ollama_hosts:
            return ollama_hosts[0]
    if stage in {"tts"} and workers:
        return workers[0]
    if stage in {"assembly", "export"} and workers:
        return workers[-1]
    return dgx or (workers[0] if workers else None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pool-aware Nexetra media batch runner")
    parser.add_argument("--job", required=True, help="Path to job JSON")
    parser.add_argument("--dry-run", action="store_true", help="Pass dry-run to all stages")
    parser.add_argument(
        "--allow-local-fallback",
        action="store_true",
        help="If remote stage fails, retry locally",
    )
    parser.add_argument(
        "--health-only",
        action="store_true",
        help="Only run host runner health checks and write report",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate JCL and exit before inventory, SSH, leases, or artifacts",
    )
    args = parser.parse_args()

    job_path = Path(args.job)
    if not job_path.is_absolute():
        job_path = REPO_ROOT / job_path
    validation = validate_job_path(job_path)
    if args.validate_only or not validation["valid"]:
        print(json.dumps(validation, indent=2, ensure_ascii=False))
    if not validation["valid"]:
        sys.exit(2)
    if args.validate_only:
        return

    job = json.loads(job_path.read_text(encoding="utf-8"))
    try:
        ensure_executable_job(job)
    except ValueError as exc:
        print(json.dumps({"valid": True, "executable": False, "job_id": job.get("job_id"), "error": str(exc)}, indent=2))
        sys.exit(3)
    handler = get_handler(job["job_type"])
    if handler is None:
        print(json.dumps({"valid": True, "executable": False, "job_id": job.get("job_id"), "error": f"No handler for {job.get('job_type')}"}, indent=2))
        sys.exit(3)
    active_stages = [(stage.name, stage.script) for stage in handler.stages]

    conf = load_config()
    pool_conf = conf.get("compute_pool", {}) if isinstance(conf, dict) else {}

    deny = set(pool_conf.get("deny_hosts", []))
    if not deny:
        deny = {"linux-1", "linux-2", "das-Mac-mini.local"}

    allow_patterns = pool_conf.get("allow_name_patterns", [])
    if not allow_patterns:
        allow_patterns = [r"^gx10", r"^Lab-", r"^ubuntu-"]

    candidates = load_hosts(deny_hosts=deny, allow_patterns=allow_patterns)
    job_id = load_job_id(args.job)
    remote_roots = pool_conf.get("remote_root_candidates", [])
    if not remote_roots:
        remote_roots = [
            "~/nexetra-remote-coding/nexetra-media",
            "~/Code/nexetra-remote-coding/nexetra-media",
            "~/nexetra-media",
        ]

    if not candidates:
        raise RuntimeError("No eligible hosts found in compute pool")

    health = collect_health(candidates, remote_roots=remote_roots)
    health_report = write_health_report(health)
    print(f"Health report: {health_report}")
    for host in candidates:
        h = health.get(host.name, {})
        print(
            f"- {host.name}: reachable={h.get('reachable')} remote_ready={h.get('remote_ready')} "
            f"ollama={h.get('ollama')} vllm={h.get('vllm')}"
        )

    if args.health_only:
        return

    remote_ready = [h for h in candidates if health.get(h.name, {}).get("remote_ready")]
    if not remote_ready:
        print("No remote-ready hosts detected; runner will rely on local fallback where needed.")

    selected_pool = remote_ready if remote_ready else candidates
    selected_pool = sorted(
        selected_pool,
        key=lambda host: (0 if host.name == "ubuntu-1" else 1 if host.name.startswith("ubuntu-") else 2 if host.name.startswith("Lab-") else 3, host.name),
    )
    selected = acquire_hosts(selected_pool, job=args.job, limit=1)
    if not selected:
        raise RuntimeError("No free/reachable hosts available in compute pool")

    print("Selected compute hosts:")
    for h in selected:
        print(f"- {h.name} ({h.ip})")

    manifest = RunManifest(job, args.job, runner="run_batch_pool", dry_run=args.dry_run)
    provenance = manifest.data["provenance"]
    _log_event(args.job, "batch_start", detail=f"hosts={[h.name for h in selected]}", run_id=manifest.run_id, provenance=provenance)

    success = True
    anchor_host: Host | None = None
    try:
        ordered_candidates = sorted(
            selected,
            key=lambda host: (0 if host.name.startswith("ubuntu-") else 1 if host.name.startswith("Lab-") else 2, host.name),
        )
        for candidate in ordered_candidates:
            remote_root = health.get(candidate.name, {}).get("remote_root", "")
            if not remote_root:
                continue
            preflight_started = time.time()
            _log_event(args.job, "stage_start", stage="worker_preflight", host=candidate.name, run_id=manifest.run_id, provenance=provenance)
            sync_ok, sync_detail = sync_worker_source(candidate, remote_root, provenance["source_sha"])
            code, report, preflight_error = (-1, {}, sync_detail)
            if sync_ok:
                code, report, preflight_error = run_worker_preflight(
                    candidate,
                    remote_root=remote_root,
                    job=args.job,
                    job_id=job_id,
                    run_id=manifest.run_id,
                )
            matches, match_errors = worker_preflight_matches(
                report,
                expected_provenance=provenance,
                required_stages=list(handler.capability_names),
            )
            preflight_record = {
                "host": candidate.name,
                "ip": candidate.ip,
                "remote_root": remote_root,
                "source_sync": {"passed": sync_ok, "detail": sync_detail},
                "report": report,
                "matched": code == 0 and matches,
                "errors": ([preflight_error] if preflight_error else []) + match_errors,
            }
            manifest.add_preflight(preflight_record)
            preflight_passed = code == 0 and matches
            manifest.add_stage(make_stage_result(
                stage="worker_preflight",
                status="passed" if preflight_passed else "failed",
                host=candidate.name,
                returncode=code,
                started_at=preflight_started,
                outputs=[],
                error="" if preflight_passed else "; ".join(preflight_record["errors"]),
            ))
            _log_event(
                args.job,
                "stage_pass" if preflight_passed else "stage_fail",
                stage="worker_preflight",
                host=candidate.name,
                detail="hashes and capabilities matched" if preflight_passed else "; ".join(preflight_record["errors"]),
                run_id=manifest.run_id,
                provenance=provenance,
            )
            if preflight_passed:
                anchor_host = candidate
                break

        if anchor_host:
            print(f"Execution anchor host: {anchor_host.name} ({anchor_host.ip})")
        elif not args.allow_local_fallback:
            detail = "No worker passed source, job, dependency, capability, and artifact-root preflight"
            manifest.finalize("failed", error=detail)
            _log_event(args.job, "batch_fail", detail=detail, run_id=manifest.run_id, provenance=provenance)
            sys.exit(1)
        else:
            print("No worker passed preflight; using explicit local fallback.")

        for stage, script in active_stages:
            host = anchor_host
            code = -1
            error = "No eligible execution host"
            warnings: list[str] = []
            host_name = host.name if host else "local"
            stage_started = time.time()
            _log_event(args.job, "stage_start", stage=stage, host=host_name, run_id=manifest.run_id, provenance=provenance)
            if host:
                code, error = run_remote_stage(
                    stage,
                    script,
                    host=host,
                    job=args.job,
                    dry_run=args.dry_run,
                    remote_roots=remote_roots,
                    remote_root=health[host.name]["remote_root"],
                    job_id=job_id,
                    run_id=manifest.run_id,
                )
            else:
                code, error = run_local_stage(stage, script, job=args.job, dry_run=args.dry_run, output_dir=manifest.artifact_root)
                host_name = "local"
            if code != 0 and host is not None and args.allow_local_fallback:
                warnings.append(f"Remote attempt on {host_name} failed: {error}")
                print(f"Falling back to local for stage: {stage}")
                code, error = run_local_stage(stage, script, job=args.job, dry_run=args.dry_run, output_dir=manifest.artifact_root)
                host_name = "local"

            artifact_check = (
                evaluate_stage_artifacts(job, stage, output_dir=manifest.artifact_root, allow_stubs=args.dry_run)
                if code == 0 and host_name == "local"
                else {"passed": code == 0, "outputs": [], "missing": [], "validation": []}
            )
            ok = code == 0 and artifact_check["passed"]
            if code == 0 and artifact_check.get("missing"):
                error = f"Missing required stage artifacts: {artifact_check['missing']}"
            result = make_stage_result(
                stage=stage,
                status="passed" if ok else "failed",
                host=host_name,
                returncode=code,
                started_at=stage_started,
                outputs=artifact_check.get("outputs", []),
                error="" if ok else error,
                warnings=warnings,
            )
            manifest.add_stage(result)
            if ok:
                _log_event(args.job, "stage_pass", stage=stage, host=host_name, run_id=manifest.run_id, provenance=provenance)
            else:
                _log_event(args.job, "stage_fail", stage=stage, host=host_name, detail=error, run_id=manifest.run_id, provenance=provenance)
                print(f"Stage failed: {stage}")
                success = False
                break

        if success:
            if anchor_host and health.get(anchor_host.name, {}).get("remote_root"):
                sync_started = time.time()
                _log_event(args.job, "stage_start", stage="artifact_sync", host=anchor_host.name, run_id=manifest.run_id, provenance=provenance)
                ok, detail = sync_remote_artifacts(
                    anchor_host,
                    remote_root=health[anchor_host.name]["remote_root"],
                    job_id=job_id,
                    run_id=manifest.run_id,
                )
                if ok:
                    _log_event(args.job, "stage_pass", stage="artifact_sync", host=anchor_host.name, detail=detail, run_id=manifest.run_id, provenance=provenance)
                    print(f"\n=== Stage: artifact_sync (remote: {anchor_host.name}) ===")
                    print(detail)
                else:
                    _log_event(args.job, "stage_fail", stage="artifact_sync", host=anchor_host.name, detail=detail, run_id=manifest.run_id, provenance=provenance)
                    print(f"\n=== Stage: artifact_sync (remote: {anchor_host.name}) ===")
                    print(f"Artifact sync failed: {detail}")
                    success = False
                manifest.add_stage(make_stage_result(
                    stage="artifact_sync",
                    status="passed" if ok else "failed",
                    host=anchor_host.name,
                    returncode=0 if ok else 1,
                    started_at=sync_started,
                    outputs=[f"output/{job_id}"] if ok else [],
                    error="" if ok else detail,
                ))

        if success:
            evaluation = evaluate_success_criteria(job, output_dir=manifest.artifact_root, allow_stubs=args.dry_run)
            if not evaluation["passed"]:
                success = False
                failure_detail = f"Success criteria failed: missing={evaluation['missing']} empty={evaluation['empty']}"
            else:
                failure_detail = ""
        else:
            evaluation = evaluate_success_criteria(job, output_dir=manifest.artifact_root, allow_stubs=args.dry_run)
            failure_detail = next((stage.get("error", "") for stage in reversed(manifest.data["stages"]) if stage["status"] == "failed"), "batch failed")

        if success:
            manifest.finalize("done", evaluation=evaluation)
            record_ok, record_detail = sync_run_record(anchor_host, health[anchor_host.name]["remote_root"], manifest) if anchor_host else (True, "local manifest")
            if not record_ok:
                success = False
                failure_detail = f"Run record sync failed: {record_detail}"
                manifest.finalize("failed", error=failure_detail, evaluation=evaluation)
                if anchor_host:
                    sync_run_record(anchor_host, health[anchor_host.name]["remote_root"], manifest)
        if success:
            _log_event(args.job, "batch_done", detail="all stages and success criteria passed", run_id=manifest.run_id, provenance=provenance)
            print("\nBatch completed successfully.")
        else:
            if manifest.data["status"] != "failed":
                manifest.finalize("failed", error=failure_detail, evaluation=evaluation)
            if anchor_host:
                sync_run_record(anchor_host, health[anchor_host.name]["remote_root"], manifest)
            _log_event(args.job, "batch_fail", detail=failure_detail, run_id=manifest.run_id, provenance=provenance)
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        manifest.finalize("failed", error=detail)
        if anchor_host:
            sync_run_record(anchor_host, health[anchor_host.name]["remote_root"], manifest)
        _log_event(args.job, "batch_fail", detail=detail, run_id=manifest.run_id, provenance=provenance)
        raise
    finally:
        print("\nReleasing compute resources back to pool...")
        release_hosts(selected, job=args.job)
        print("Release complete.")


if __name__ == "__main__":
    main()
