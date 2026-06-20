"""
viewer/deploy.py
─────────────────────────────────────────────────────
Deploy the Nexetra Media viewer to a remote Linux host as a
persistent systemd --user service.

Usage (from nexetra-media/):
  python viewer/deploy.py                    # deploys to ubuntu-1 by default
  python viewer/deploy.py --host ubuntu-1
  python viewer/deploy.py --restart-only     # restart without re-syncing code
"""

from __future__ import annotations

import argparse
import io
import json
import re
import tarfile
import time
from pathlib import Path

import paramiko

try:
    from viewer.archive_utils import should_exclude_archive_path
except ModuleNotFoundError:
    from archive_utils import should_exclude_archive_path

ROOT = Path(__file__).resolve().parent.parent          # nexetra-media/
WORKSPACE_ROOT = ROOT.parent


def _resolve_path(filename: str) -> Path:
    """Prefer repo-local config files; fall back to legacy parent layout."""
    local = ROOT / filename
    if local.exists():
        return local
    return WORKSPACE_ROOT / filename


SECRETS_FILE = _resolve_path("secrets.local.md")
WORKSTATIONS_CSV = _resolve_path("workstations.csv")

# ── default deployment target ──────────────────────────────────────────────
DEFAULT_HOST = "ubuntu-1"
VIEWER_PORT  = 7800
SERVICE_NAME = "nexetra-viewer"


def parse_secrets(path: Path) -> dict[tuple[str, str], str]:
    text   = path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"\n###\s+", text)
    out: dict[tuple[str, str], str] = {}
    for block in blocks:
        ip_m   = re.search(r"^-\s*IP:\s*(.+)$",       block, re.MULTILINE)
        user_m = re.search(r"^-\s*Username:\s*(.+)$",  block, re.MULTILINE)
        pass_m = re.search(r"^-\s*Password:\s*(.+)$",  block, re.MULTILINE)
        if not (ip_m and user_m and pass_m):
            continue
        out[(ip_m.group(1).strip().lower(), user_m.group(1).strip().lower())] = pass_m.group(1).strip()
    return out


def load_host(name: str):
    import csv as _csv
    secrets = parse_secrets(SECRETS_FILE)
    with WORKSTATIONS_CSV.open(encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            if (row.get("name") or "").strip() == name:
                ip   = (row.get("ip") or "").strip()
                user = (row.get("un") or "").strip()
                pwd  = secrets.get((ip.lower(), user.lower()))
                if not pwd:
                    raise RuntimeError(f"No password found for {name} ({ip} / {user})")
                return ip, user, pwd
    raise RuntimeError(f"Host '{name}' not found in {WORKSTATIONS_CSV}")


def make_archive() -> bytes:
    """Package deployable source without local credentials or generated data."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in ROOT.rglob("*"):
            rel = p.relative_to(ROOT)
            if should_exclude_archive_path(rel):
                continue
            if p.is_file():
                tf.add(p, arcname=str(rel))
    return buf.getvalue()


SYSTEMD_UNIT = """\
[Unit]
Description=Nexetra Media Compute Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/{user}/nexetra-media
ExecStart=/home/{user}/nexetra-media/.venv/bin/python viewer/app.py
Restart=on-failure
RestartSec=5
Environment=NEXETRA_VIEWER_PORT={port}

[Install]
WantedBy=default.target
"""


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 600) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="ignore")
    err = stderr.read().decode("utf-8", errors="ignore")
    rc  = stdout.channel.recv_exit_status()
    return rc, out, err


def deploy(host_name: str, restart_only: bool = False) -> None:
    ip, user, pwd = load_host(host_name)
    print(f"Connecting to {host_name} ({ip}) as {user}…")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        ip, username=user, password=pwd,
        timeout=15, banner_timeout=15, auth_timeout=15,
        look_for_keys=False, allow_agent=False,
    )

    try:
        if not restart_only:
            print("Packaging code…")
            payload = make_archive()
            print(f"Archive: {len(payload):,} bytes — uploading…")
            sftp = client.open_sftp()
            with sftp.open("/tmp/nexetra-media-sync.tgz", "wb") as f:
                f.write(payload)
            sftp.close()

            print("Extracting + installing deps on remote…")
            setup = (
                "set -e; "
                "mkdir -p ~/nexetra-media; "
                "tar -xzf /tmp/nexetra-media-sync.tgz -C ~/nexetra-media; "
                "cd ~/nexetra-media; "
                "python3 -m venv .venv; "
                ".venv/bin/python -m pip install -q --upgrade pip; "
                ".venv/bin/pip install -q -r requirements.txt; "
                ".venv/bin/pip install -q -r viewer/requirements.txt; "
                "echo INSTALL_OK"
            )
            rc, out, err = run(client, setup, timeout=1800)
            print(out.strip())
            if err.strip():
                print("STDERR:", err.strip())
            if rc != 0:
                raise RuntimeError(f"Install failed (rc={rc})")

        # Write systemd user unit
        print("Installing systemd user service…")
        unit_content = SYSTEMD_UNIT.format(user=user, port=VIEWER_PORT)
        unit_escaped = unit_content.replace("'", "'\\''")
        unit_cmd = (
            "mkdir -p ~/.config/systemd/user; "
            f"printf '%s' '{unit_escaped}' > ~/.config/systemd/user/{SERVICE_NAME}.service; "
            "systemctl --user daemon-reload; "
            f"systemctl --user enable {SERVICE_NAME}; "
            f"systemctl --user restart {SERVICE_NAME}; "
            "sleep 2; "
            f"systemctl --user is-active {SERVICE_NAME}"
        )
        rc, out, err = run(client, unit_cmd, timeout=120)
        print(out.strip())
        if err.strip():
            print("STDERR:", err.strip())

        # Verify
        rc2, out2, _ = run(client, f"ss -tlnp | grep :{VIEWER_PORT} || echo NOT_LISTENING", timeout=30)
        if str(VIEWER_PORT) in out2:
            print(f"\n✅  Viewer is live → http://{ip}:{VIEWER_PORT}")
        else:
            print(f"\n⚠️  Port {VIEWER_PORT} not detected yet — check: systemctl --user status {SERVICE_NAME}")
            print(f"   URL (may need a moment): http://{ip}:{VIEWER_PORT}")

    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy nexetra-media viewer to a remote host")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Target host name (default: {DEFAULT_HOST})")
    parser.add_argument("--restart-only", action="store_true", help="Skip code sync; just restart service")
    args = parser.parse_args()
    deploy(args.host, restart_only=args.restart_only)
