# Nexetra Media Factory

Local-first, multi-lingual promotional video production pipeline for YouTube and social channels.

This project is designed to generate high-volume, branded promo videos with minimal cloud spend by using the existing Nexetra lab footprint:
- 6x Mac Mini M4 (16GB)
- DGX Spark (GPU-heavy generation)
- ubuntu-1 (orchestration and artifacts)

## Mission

Build a repeatable video factory where one trigger produces:
1. Master script
2. Multi-language translations
3. Voice tracks (TTS)
4. Visual assets (talking head, slides, or AI B-roll)
5. Assembled videos in multiple formats (16:9, 9:16, 1:1)
6. Captions and run artifacts

Primary business goal: get first Nexetra customers through consistent, multi-market promotional content.

## Local-First Strategy

The pipeline is intentionally built to do the heavy work locally:
- Script generation: local large model on DGX Spark (Ollama)
- Translation: local worker models on Mac Minis
- TTS: local XTTS/Kokoro where supported
- Visual generation: local ComfyUI workflows on DGX Spark
- Assembly/export: local FFmpeg/MoviePy
- Captions: local Whisper.cpp

Cloud models (Claude/OpenAI) are optional and only used for light QA/polish when needed.

## Lab Topology (Planned Roles)

- DGX Spark: script brain, ComfyUI visuals, talking head rendering, subtitle transcription
- Mac Mini 01-02: translation workers
- Mac Mini 03-04: TTS workers
- Mac Mini 05: assembly and rendering
- Mac Mini 06: orchestration client, QA, evidence capture
- ubuntu-1: orchestration (n8n/Archon), job queue, artifact storage

## Pipeline Stages

1. Foundation setup (models, services, tooling)
2. Script and translation engine
3. Voice engine (multi-lingual TTS)
4. Visual engine (talking head / slides / B-roll)
5. Assembly and platform export
6. Orchestration and language prioritization by RTT tiers

## Output Targets

Each production job can output:
- YouTube: 16:9 (60-90s)
- Reels/TikTok: 9:16
- LinkedIn: 1:1 and/or 16:9

From one script, multiple language and format variants are generated in a batch.

## Language Coverage

Current target set includes:
- English (master)
- Spanish, French, German, Portuguese
- Arabic, Mandarin, Cantonese
- Hindi, Japanese
- Nordic languages (Swedish, Norwegian, Danish, Finnish)
- Additional languages based on RTT market-priority testing

## Project Status

Planning completed; implementation scaffold is next.

Detailed timestamped plans:
- docs/nexetra-video-pipeline-plan-20260608.md
- docs/nexetra-video-pipeline-plan-20260608.html

## Suggested Next Execution Steps

1. Create baseline folders:
   - pipeline/
   - templates/
   - jobs/
   - output/
2. Define first MVP job:
   - "What is Nexetra?" (60s)
   - EN + ES + FR
   - Export all three aspect ratios
3. Build initial script -> translation -> TTS chain and validate audio quality
4. Add one visual path (slides first) before expanding to talking head and B-roll
5. Wire first end-to-end run with logs and pass/fail summary

## Distributed Compute Pool Runner

For batch operations, use the pool-aware runner:

- Command:
   - `python pipeline/run_batch_pool.py --job jobs/what-is-nexetra-live-es.json --allow-local-fallback`

What it does:
- Selects only eligible hosts from `workstations.csv`.
- Hard excludes protected hosts: `linux-1`, `linux-2`, `das-Mac-mini.local`.
- Attempts remote stage execution on DGX + Lab/ubuntu workers.
- Always runs a final release step to return compute to the pool.

Release behavior:
- On batch end (success or failure), it unloads active Ollama models on used hosts if Ollama is present.
- Removes host leases from `output/compute_pool/leases.json`.

Runner health checks:
- Standalone check:
   - `python pipeline/check_runner_health.py`
- Inline check before a batch:
   - `python pipeline/run_batch_pool.py --job jobs/what-is-nexetra-live-es.json --health-only`

Lab mini admin bootstrap (one-time):
- Use `../bootstrap-lab-minis-ollama.ps1` to run the temporary grant -> install -> revoke flow.
- Dry-run first:
   - `powershell -ExecutionPolicy Bypass -File ..\bootstrap-lab-minis-ollama.ps1 -AdminUser <admin-user> -WhatIf`

## Success Criteria

- One command or workflow trigger starts a full run
- Multi-language files generated without manual editing
- All target aspect ratios exported from one master timeline
- Captions generated and attached/burned correctly
- Dated artifacts and stage logs available for audit and repeatability
