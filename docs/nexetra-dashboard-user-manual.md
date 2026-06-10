# Nexetra Media Compute Dashboard — User Manual

**Version:** 1.0 · **Date:** 2026-06-09  
**Permanent URL:** http://10.0.0.200:7800  
**Hosted on:** ubuntu-1 (10.0.0.200) as a persistent systemd user service

---

## Overview

The Nexetra Media Compute Dashboard is a real-time web application that gives you a single-pane view of:

- **Node health** — every eligible compute host, its capabilities, and whether it is actively running a job
- **Batch jobs** — per-job progress through the 5-stage media pipeline with goal descriptions
- **Event history** — timestamped log of every stage start, pass, fail, and batch completion
- **Pool statistics** — remote-ready count, reachable count, Ollama nodes, active leases, jobs defined

The dashboard auto-updates every **4 seconds** via Server-Sent Events (SSE). No manual refresh needed.

---

## Access

Open a browser and navigate to:

```
http://10.0.0.200:7800
```

The dashboard is accessible from any machine on the lab network.

---

## Dashboard Sections

### 1. Header Bar

The top bar shows:
- The application name and logo
- A **LIVE** badge with a green pulsing dot confirming the SSE connection is active
- A **"Updated HH:MM:SS"** timestamp showing when data was last refreshed

If the connection drops, the badge changes to **"⚠ Reconnecting…"** and reconnects automatically.

---

### 2. Pool Overview (Stats Bar)

Five summary cards at the top of the page:

| Card | Colour | Meaning |
|------|--------|---------|
| Remote Ready | Green | Hosts with nexetra-media deployed and a working `.venv` |
| Reachable | Yellow | Hosts that are online but not yet fully provisioned |
| Ollama Nodes | Blue | Hosts running the Ollama model server |
| Active Leases | Purple | Hosts currently locked by a running batch job |
| Jobs Defined | Green | Number of job JSON files in `jobs/` |

---

### 3. Node Health Grid

Each eligible compute host appears as a card. Protected hosts (`linux-1`, `linux-2`, `das-Mac-mini.local`, `sql-server`) are never shown.

**Card border colours:**

| Border | Status |
|--------|--------|
| Green (left) | Remote-ready — can run pipeline stages |
| Yellow (left) | Reachable — SSH accessible but not provisioned |
| Red (left) | Offline — unreachable |
| Purple (left) + animated | Actively running a batch job |

**Capability pills on each card:**

- **Ollama** — green if Ollama server is running on this host
- **vLLM** — green if vLLM is installed
- **Remote✓** — shown only when the host is fully remote-ready

When a host is leased, its card turns purple, shows a "⚡ Running" indicator, and displays the job name it is working on.

---

### 4. Batch Jobs

Each job defined in `nexetra-media/jobs/*.json` appears as a card showing:

- **Title** — the video title
- **Status badge** — `complete`, `running` (animated blue), `live-test`, or `draft`
- **Goal sentence** — auto-generated description of what the job produces, e.g.:
  > *Produce a 60s slides promo for [What is Nexetra?] in EN + ES. Export formats: 16:9, 9:16, 1:1. CTA: Visit nexetra.com.*
- **Pipeline tracker** — 5 dots representing each stage:

| Symbol | Stage |
|--------|-------|
| S | scriptgen |
| T | translate |
| ♪ | tts |
| A | assembly |
| E | export |

  - Green filled = stage completed
  - Blue pulsing = stage currently running
  - Grey outlined = not yet started

- **Progress bar** — percentage complete across all 5 stages
- **Meta tags** — languages, formats, and job ID

---

### 5. Event History

A scrollable table of the last 60 events from all batch runs, most-recent first:

| Column | Contents |
|--------|----------|
| Time | Local time of the event |
| Job | Job ID (without path/extension) |
| Stage | Which pipeline stage |
| Event | `batch_start`, `stage_start`, `stage_pass`, `stage_fail`, `batch_done`, `batch_fail`, `release` |
| Detail | Host name, host list, or error summary |

**Event colour coding:**
- Blue — `batch_start`, `stage_start`
- Green — `stage_pass`, `batch_done`
- Red — `stage_fail`, `batch_fail`
- Dim — release events

History is stored in `nexetra-media/output/job_runs.jsonl` and persists across restarts.

---

## Running a Batch Job

### From the command line (local machine)

```powershell
# From nexetra-media/ directory
python pipeline\run_batch_pool.py --job jobs\what-is-nexetra-live-es.json
```

The runner will:
1. Run a health check across all eligible hosts
2. Acquire leases on remote-ready hosts only
3. Execute all 5 stages on the anchor host (ubuntu-1 by default)
4. Log every stage event to `output/job_runs.jsonl`
5. Release all leases when done (pass or fail)

The dashboard updates automatically as events are written.

### Dry run (no actual LLM/TTS calls)

```powershell
python pipeline\run_batch_pool.py --job jobs\what-is-nexetra-live-es.json --dry-run
```

### Local fallback (if remote hosts unavailable)

```powershell
python pipeline\run_batch_pool.py --job jobs\what-is-nexetra-live-es.json --allow-local-fallback
```

### Health check only

```powershell
python pipeline\run_batch_pool.py --job jobs\what-is-nexetra-live-es.json --health-only
```

---

## Running Stages Individually

Each stage can be invoked standalone:

```powershell
python pipeline\scriptgen\agent.py --job jobs\what-is-nexetra-live-es.json
python pipeline\translate\agent.py --job jobs\what-is-nexetra-live-es.json
python pipeline\tts\agent.py       --job jobs\what-is-nexetra-live-es.json
python pipeline\assembly\agent.py  --job jobs\what-is-nexetra-live-es.json
python pipeline\export\agent.py    --job jobs\what-is-nexetra-live-es.json
```

Add `--dry-run` to any stage to skip external calls and write placeholder files.

---

## Adding a New Job

1. Create a new JSON file in `nexetra-media/jobs/`:

```json
{
  "job_id": "product-demo-fr",
  "title": "Nexetra Product Demo",
  "duration_seconds": 60,
  "languages": ["en", "fr", "de"],
  "formats": ["16:9", "9:16", "1:1"],
  "style": "slides",
  "cta": "Book a demo at nexetra.com",
  "status": "draft"
}
```

2. The dashboard will show the new job card within 4 seconds (next SSE cycle).
3. Run the batch: `python pipeline\run_batch_pool.py --job jobs\product-demo-fr.json`

---

## Managing the Dashboard Service

The dashboard runs as a **systemd user service** on ubuntu-1 (`10.0.0.200`).

### Check status

```bash
ssh user@10.0.0.200
systemctl --user status nexetra-viewer
```

### View logs

```bash
journalctl --user -u nexetra-viewer -f
```

### Restart after code changes

From `nexetra-media/` on your local machine:

```powershell
python viewer\deploy.py --host ubuntu-1
```

For a restart-only (no code sync):

```powershell
python viewer\deploy.py --host ubuntu-1 --restart-only
```

### Stop / Start manually

```bash
systemctl --user stop nexetra-viewer
systemctl --user start nexetra-viewer
```

The service is enabled at login and will restart automatically after crashes.

---

## Compute Pool Configuration

Edit `nexetra-media/config.yaml` to control which hosts participate:

```yaml
compute_pool:
  deny_hosts:           # These hosts are NEVER touched
    - "linux-1"
    - "linux-2"
    - "das-Mac-mini.local"

  allow_name_patterns:  # Only hosts matching these patterns are eligible
    - "^gx10"
    - "^Lab-"
    - "^ubuntu-"

  remote_root_candidates:  # Where to look for nexetra-media on each remote host
    - "/home/gx10/nexetra-media"
    - "/home/user/nexetra-media"
    - "~/nexetra-media"
```

---

## Provisioning New Remote Hosts

To add a new host to the remote-ready pool:

1. Ensure the host is in `workstations.csv` with name, IP, and username
2. Add its password to `secrets.local.md` in the standard format
3. Deploy the nexetra-media runtime to it:

```powershell
# From nexetra-media/
python viewer\deploy.py --host ubuntu-3
```

This syncs the codebase and builds a `.venv` on the remote host. The host will appear as **Remote✓** in the dashboard within 4 seconds of the next health check.

---

## Dashboard API

The viewer exposes two additional endpoints:

| Endpoint | Description |
|----------|-------------|
| `GET /api/snapshot` | Full JSON snapshot of current health, leases, jobs, and history |
| `GET /events` | SSE stream of snapshots (used internally by the dashboard) |

Example:
```bash
curl http://10.0.0.200:7800/api/snapshot | python -m json.tool
```

---

## File Locations

| File | Purpose |
|------|---------|
| `nexetra-media/viewer/app.py` | FastAPI backend |
| `nexetra-media/viewer/templates/index.html` | Dashboard UI |
| `nexetra-media/viewer/deploy.py` | Remote deployment script |
| `nexetra-media/output/compute_pool/health-latest.json` | Latest node health snapshot |
| `nexetra-media/output/compute_pool/leases.json` | Active batch leases |
| `nexetra-media/output/job_runs.jsonl` | Append-only event history |
| `nexetra-media/jobs/*.json` | Job definitions |
| `nexetra-media/output/<job_id>/<lang>/` | Stage artifacts per job/language |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Dashboard shows blank node grid | `health-latest.json` missing or stale | Run `python pipeline\check_runner_health.py` |
| Node shows "Reachable" not "Ready" | nexetra-media not deployed there | Run `python viewer\deploy.py --host <name>` |
| "⚠ Reconnecting…" in header | SSE connection dropped | Browser auto-reconnects; check service logs if persistent |
| Event history empty after batch | `job_runs.jsonl` not yet on ubuntu-1 | Sync manually or re-run a batch after last deploy |
| Batch fails at scriptgen | DGX Ollama unreachable | Check `http://10.0.0.7:11434` is accessible |
| Stage fails with "REMOTE_ROOT_NOT_FOUND" | Host not provisioned | Run `python viewer\deploy.py --host <name>` |
