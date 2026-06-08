# Nexetra Local AI Video Production Factory — Plan
**Created:** 2026-06-08  
**Workspace:** nexetra-media  
**Goal:** Multi-lingual promotional video pipeline running 90%+ locally on the Mac Mini lab + DGX Spark. One trigger → script → translated voices → visuals → assembled multi-format video bundle.

---

## Lab Role Assignment

| Machine | IP | Role |
|---|---|---|
| DGX Spark | 10.0.0.7 | Large LLM (script gen), ComfyUI visuals, talking head, Whisper subtitles |
| Mac Mini 01 | 10.0.0.242 | Translation worker (Ollama 8B) |
| Mac Mini 02 | 10.0.0.235 | Translation worker (Ollama 8B) |
| Mac Mini 03 | 10.0.0.45 | TTS worker (XTTS-v2 + Kokoro) |
| Mac Mini 04 | 10.0.0.241 | TTS worker (XTTS-v2 + Kokoro) |
| Mac Mini 05 | 10.0.0.113 | Assembly + FFmpeg multi-format export |
| Mac Mini 06 | 10.0.0.3 | Orchestration client + QA + evidence capture |
| ubuntu-1 | 10.0.0.200 | n8n orchestrator, artifact storage, run manifests |

**Hardware:** Mac Minis are M4 16GB (low-end). DGX Spark handles all GPU-heavy work.

---

## Target Languages

English (master), Spanish, French, German, Portuguese, Arabic, Mandarin, Cantonese, Hindi, Japanese, Nordic (Swedish / Norwegian / Danish / Finnish) + additional markets per user RTT test results (to be supplied).

**Language tier order** will be determined by RTT test results — Tier 1 (<100ms) markets produced first in every batch run.

---

## Output Formats

All derived from one master production per video:
- **YouTube** — 16:9, 60–90 sec promo
- **Instagram / TikTok Reels** — 9:16 vertical short
- **LinkedIn** — 1:1 square or 16:9

---

## Video Styles

Mix of three, chosen per use case:
1. **AI avatar / talking head** — Nexetra persona photo driven by TTS audio (SadTalker / MuseTalk)
2. **Slides + voiceover** — Manim animated text/graphics + narration
3. **AI visuals + voiceover** — ComfyUI B-roll + narration (cinematic/product style)

---

## Architecture

```
Orchestrator (ubuntu-1 · n8n + Archon)
├── Script Agent        → DGX Spark (Ollama Qwen2.5 72B Q4)
│   └── QA pass         → Claude API (1 call per video, optional ~$0.01–0.05)
├── Translation Agent   → Mac Minis 01+02 (Ollama Llama3.1 8B, parallelized)
├── TTS Agent           → Mac Minis 03+04 (XTTS-v2 / Kokoro / Edge-TTS fallback)
├── Visual Agent        → DGX Spark (ComfyUI: FLUX + CogVideoX + SadTalker)
│   └── Slides path     → Mac Mini 05 (Manim)
├── Assembly Agent      → Mac Mini 05 (FFmpeg + MoviePy)
└── Export Agent        → Mac Mini 06 (multi-format crop + caption burn-in)
```

---

## Phase 0 — Foundation

Install and wire up all services.

1. Install **Ollama + Qwen2.5 72B Q4** (or Llama3.3 70B Q4) on DGX Spark — the script brain
2. Install **Ollama + Llama3.1 8B** on Mac Minis 01+02 — translation workers
3. Install **Coqui XTTS-v2** on Mac Minis 03+04 — 17-language local TTS
4. Install **Kokoro TTS** on Mac Minis 03+04 — fast EN voices + extras
5. Install **Edge-TTS** on Mac Mini 06 — free Microsoft TTS, covers Cantonese, Nordic, language gaps
6. Install **ComfyUI** on DGX Spark with:
   - FLUX (image/B-roll generation)
   - CogVideoX or Wan2.1 (AI video clip generation)
   - SadTalker or MuseTalk (talking head from photo + TTS audio)
7. Install **Whisper.cpp** on DGX Spark — local subtitle generation
8. Install **n8n** self-hosted on ubuntu-1 — visual workflow trigger UI
9. Install **Archon** on ubuntu-1 / Mac Mini 05 — YAML deterministic workflow spec
10. Scaffold `nexetra-media/` workspace:
    ```
    nexetra-media/
    ├── pipeline/
    │   ├── scriptgen/
    │   ├── translate/
    │   ├── tts/
    │   ├── visuals/
    │   ├── assembly/
    │   └── export/
    ├── templates/       ← script templates, brand assets
    ├── jobs/            ← per-video job JSON files
    ├── output/          ← dated artifact folders
    └── docs/            ← this file + runbooks
    ```

---

## Phase 1 — Script + Translation Engine

11. Build **Nexetra script templates** — JSON/YAML structures per video type (hook + body + CTA)
12. Build `scriptgen` agent — Ollama prompt chain on DGX Spark → structured script output
13. Optional: 1× Claude API call per video for tone/quality polish
14. Build `translate` agent — fan-out Ollama calls across Mac Minis 01+02 for all languages in parallel
15. Ingest **RTT test results** → build language tier list (Tier 1 markets produced first per batch)

---

## Phase 2 — Voice Engine

16. Map each language → TTS engine:

    | Language Group | Engine |
    |---|---|
    | ES, FR, DE, PT, HI, ZH, JA, AR | XTTS-v2 (local) |
    | EN (primary) | Kokoro TTS (local) |
    | Cantonese, Nordic (SV/NO/DA/FI) | Edge-TTS (free MS API) |

17. Build `tts` agent — translated script segment → WAV file per language
18. FFmpeg post-process — LUFS normalization, silence trimming, sample rate standardization

---

## Phase 3 — Visual Engine (DGX Spark)

19. **Talking head path** — SadTalker/MuseTalk drives Nexetra persona photo with TTS audio → MP4
20. **B-roll path** — ComfyUI + FLUX generates scene stills from script keywords → background clips
21. **Slides path** — Manim (Python, 100% local) generates animated text/graphic explainer clips
22. Build Nexetra brand overlay layer — logo watermark, color palette, font overlays in FFmpeg

*Active paths chosen per video type at job definition time.*

---

## Phase 4 — Assembly + Export

23. FFmpeg + MoviePy stitches audio + visuals → master 16:9 MP4 with brand watermark
24. Whisper.cpp generates `.SRT` subtitle file per language → burned in or attached as sidecar
25. Format exporter crops master into:
    - `16x9.mp4` — YouTube
    - `9x16.mp4` — Reels / TikTok
    - `1x1.mp4` — LinkedIn
26. Output: `output/YYYYMMDD-HHMMSS/{video_id}/{lang}/{format}.mp4`

---

## Phase 5 — Orchestration

27. Wire all agents into **n8n workflow** on ubuntu-1 (webhook trigger or cron schedule)
28. Mirror as **Archon YAML spec** — deterministic, scriptable, CLI-runnable
29. Per-video **job JSON file** drives each run (title, languages, style, CTA text)
30. Run manifests + per-stage logs following established overnight automation pattern:
    - `run_manifest.json`
    - `run_summary.md` (PASS / FAIL)
    - Per-stage log files

---

## Phase 6 — Language Prioritization

31. Ingest RTT test result CSV/JSON from user
32. Build tier list: Tier 1 <100ms, Tier 2 100–200ms, Tier 3 200ms+
33. Job queue: for each batch run, Tier 1 languages processed first
34. Output folder tagging by tier for reporting

---

## Tool Reference

| Tool | Cost | Host |
|---|---|---|
| Ollama + Qwen2.5 72B Q4 | Free | DGX Spark |
| Ollama + Llama3.1 8B | Free | Mac Minis 01+02 |
| Coqui XTTS-v2 | Free | Mac Minis 03+04 |
| Kokoro TTS | Free | Mac Minis 03+04 |
| Edge-TTS | Free (MS API) | Mac Mini 06 |
| ComfyUI + FLUX | Free | DGX Spark |
| ComfyUI + CogVideoX / Wan2.1 | Free | DGX Spark |
| SadTalker / MuseTalk | Free | DGX Spark |
| Whisper.cpp | Free | DGX Spark |
| Manim | Free | Mac Mini 05 |
| FFmpeg + MoviePy | Free | Mac Mini 05 |
| n8n (self-hosted) | Free | ubuntu-1 |
| Archon | Free | ubuntu-1 |
| Claude API | ~$0.01–0.05/video | Cloud |

---

## MVP First Run Target

A 60-second "What is Nexetra?" video:
- English master → Spanish + French translations
- = 3 languages × 3 formats = **9 output files** from one production run
- Validates the full pipeline before scaling to all languages

---

## Verification Gates

1. End-to-end dry run: EN-only job → TTS → static visual → 16:9 MP4 assembled
2. Talking head render: Nexetra persona photo + EN audio on DGX Spark
3. Spanish + Mandarin: translation + XTTS-v2 audio quality check
4. Format export: all 3 crops from one master confirmed
5. n8n full workflow trigger → all stages log PASS/FAIL
6. Output folder structure matches artifact storage convention

---

## Open Items

- [ ] User to supply RTT test results for language tier prioritization
- [ ] Confirm Nexetra persona / avatar photo for talking head (or decide on AI-generated mascot)
- [ ] Decide whether a human presenter is available (simplifies Phase 3 significantly)
- [ ] Decide n8n vs Archon as primary trigger (recommendation: start with Archon CLI, add n8n UI in Phase 5)
- [ ] Confirm first video brief / script direction for MVP run
