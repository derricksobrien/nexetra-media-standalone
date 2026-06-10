"""
pipeline/check_runner_health.py
-------------------------------
Standalone runner health check for compute pool hosts.

Usage:
  python pipeline/check_runner_health.py
"""

from __future__ import annotations

import json
from pathlib import Path

from run_batch_pool import (
    load_config,
    load_hosts,
    collect_health,
    write_health_report,
)


def main() -> None:
    conf = load_config()
    pool_conf = conf.get("compute_pool", {}) if isinstance(conf, dict) else {}

    deny = set(pool_conf.get("deny_hosts", [])) or {
        "linux-1",
        "linux-2",
        "das-Mac-mini.local",
    }
    allow_patterns = pool_conf.get("allow_name_patterns", []) or [r"^gx10", r"^Lab-", r"^ubuntu-"]
    remote_roots = pool_conf.get("remote_root_candidates", []) or [
        "~/nexetra-remote-coding/nexetra-media",
        "~/Code/nexetra-remote-coding/nexetra-media",
        "~/nexetra-media",
    ]

    hosts = load_hosts(deny_hosts=deny, allow_patterns=allow_patterns)
    health = collect_health(hosts, remote_roots=remote_roots)
    report_path = write_health_report(health)

    print(f"Health report: {report_path}")
    for h in hosts:
        row = health.get(h.name, {})
        print(
            f"{h.name:16} "
            f"reachable={str(row.get('reachable')):5} "
            f"remote_ready={str(row.get('remote_ready')):5} "
            f"ollama={str(row.get('ollama')):5} "
            f"vllm={str(row.get('vllm')):5}"
        )


if __name__ == "__main__":
    main()
