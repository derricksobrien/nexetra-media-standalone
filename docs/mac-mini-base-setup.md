# Mac Mini Base Setup for Nexetra Batch Jobs

**Purpose:** base software and verification steps for the Lab Mac minis after a factory reset so they can rejoin the Nexetra batch pool.

**Scope:** Lab-Station-01 through Lab-Station-06 only.

---

## 1) Install the Core Tooling

Install these on every Lab Mac mini:

- Homebrew
- Xcode Command Line Tools
- Ollama
- Git
- Python 3
- FFmpeg

Recommended optional tools:

- `rsync` for file sync
- `curl` for health checks
- `jq` for JSON inspection

---

## 2) Base Bootstrap Flow

Run the one-time bootstrap script from the Windows admin host when the Mac mini user account is ready:

```powershell
powershell -ExecutionPolicy Bypass -File .\bootstrap-lab-minis-ollama.ps1 -AdminUser <admin-user> -WhatIf
```

If the dry run looks correct, rerun without `-WhatIf`.

What the bootstrap does:

- Grants temporary admin rights to the lab user
- Installs Homebrew if needed
- Installs Ollama with Homebrew
- Starts the Ollama service
- Re-checks the install
- Revokes admin rights when complete

---

## 3) Verify the Host

After the reset and bootstrap, confirm the host is ready:

```bash
command -v brew
command -v ollama
brew --version
ollama --version
curl -s http://127.0.0.1:11434/api/tags
curl -s http://127.0.0.1:11434/api/ps
```

If SSH is used for orchestration, also confirm SSH is reachable from the control host.

---

## 4) Practical Model Prep

For batch participation, preload only small practical models unless the host has a specific role:

```bash
ollama pull qwen2.5:3b
```

This is enough for most light local worker tasks and keeps memory use low.

---

## 5) Role-Specific Add-Ons

### Mac Mini 01 and 02

Translation workers.

- Ollama
- One small translation model such as `qwen2.5:3b` or `llama3.1:8b`
- Python 3 for helper scripts

### Mac Mini 03 and 04

TTS workers.

- Ollama if needed for local text processing
- Python 3
- `edge-tts` in the project virtual environment when using fallback speech generation

### Mac Mini 05

Assembly and export.

- FFmpeg
- Python 3
- `Pillow`
- `imageio-ffmpeg`

### Mac Mini 06

Orchestration, QA, and evidence capture.

- Python 3
- Git
- SSH client tools
- Browser access for dashboard checks

---

## 6) Nexetra Python Dependencies

Inside the repo virtual environment, install the project packages used by the batch jobs:

- `httpx`
- `pyyaml`
- `edge-tts`
- `Pillow`
- `imageio-ffmpeg`
- `paramiko`

Example:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 7) Notes

- Keep `linux-1`, `linux-2`, and `das-Mac-mini.local` out of the batch pool.
- Prefer small resident models on the Mac minis to avoid memory pressure.
- If a host is reset again later, repeat only this base setup plus the role-specific add-ons it needs.