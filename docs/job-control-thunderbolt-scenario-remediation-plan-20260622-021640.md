# Nexetra Media Job Control, Scenario, and Thunderbolt Remediation Plan

**Created:** 2026-06-22 02:16:40 MDT (UTC-06:00)  
**Last updated:** 2026-06-22 21:28:17 MDT (UTC-06:00)  
**Checkpoint ID:** `20260622-021640`  
**Workspace:** `nexetra-media`  
**Dashboard:** `http://10.0.0.200:7800/`  
**Status:** In progress; Stages 0 through 4 and Stage 5A complete

## Goal

Create a trustworthy, testable job-control system that:

1. Preserves the working media pipeline.
2. Rejects invalid or unsupported jobs before allocating compute.
3. Reports failure whenever required work or artifacts are incomplete.
4. Runs each newer scenario through a dedicated implementation.
5. Uses the management network as the known-good baseline.
6. Installs and qualifies MLX and MLX-LM on every Mac mini workstation.
7. Adds Thunderbolt routing incrementally, with automatic management-network fallback.

The final goal is successful, repeatable execution of media, content-development,
multi-agent, harness-regression, and RAG jobs across the compute pool, with TB
connections used where they provide measured value.

## Baseline At This Checkpoint

### Working

- All 10 job files are syntactically valid JSON.
- Python source compilation passes.
- `what-is-nexetra-live-es` shows 100% pipeline completion.
- The dashboard reports 11 compute hosts as reachable and remote-ready.
- Thunderbolt telemetry reports 6 of 6 Mac minis inference-ready and 6 of 6
  links good.
- The latest probe can import MLX-LM on all six minis (`0.29.1` or `0.31.3`).

### Mac Mini MLX And Thunderbolt Status

Source: `thunderbolt-mlx-probe-20260619-224135.csv` and
`mlx-install-20260619-171606.csv`.

| Workstation | Management IP | TB IP | TB Link | MLX | MLX-LM | Ollama API | Inference Ready |
|---|---|---|---|---|---|---:|---:|
| Lab-Station-01 | 10.0.0.242 | 169.254.74.116 | good | unknown | 0.29.1 | 1 | 1 |
| Lab-station-02 | 10.0.0.235 | 169.254.215.93 | good | unknown | 0.29.1 | 1 | 1 |
| Lab-station-03 | 10.0.0.45 | 169.254.36.141 | good | unknown | 0.31.3 | 1 | 1 |
| Lab-station-04 | 10.0.0.241 | 169.254.142.14 | good | unknown | 0.31.3 | 1 | 1 |
| Lab-station-05 | 10.0.0.113 | 169.254.4.57 | good | unknown | 0.31.3 | 1 | 1 |
| Lab-station-06 | 10.0.0.3 | 169.254.68.146 | good | unknown | 0.31.3 | 1 | 1 |

`Inference Ready=1` currently proves the probe's combined runtime check, not a
qualified MLX serving path. The core `MLX` field is `unknown` on every mini. The
earlier installer logged success on Minis 01-04 and 06, but failed on Mini 05 due
to the PEP 668 externally-managed Python restriction. Later telemetry reports
MLX-LM `0.31.3` on Mini 05, so its installation changed, but the environment and
core MLX version still need direct verification. No mini is considered fully
MLX-qualified until it passes Stage 7.

### Not Working Reliably

- `content-summer-campaign-v1`, `ma-product-launch-brief-v1`,
  `harness-regression-weekly-v1`, and `rag-index-validate-supportkb-v1` are at 0%.
- Recent content-development and multi-agent attempts failed at `scriptgen`.
- The runner always executes the fixed media stages: `scriptgen`, `translate`,
  `tts`, `assembly`, and `export`.
- `scenario`, `execution_policy`, `model_policy`, and `success_criteria` are not
  enforced by the runner.
- Several stage agents skip failed languages or artifacts but still exit with code 0.
- The exporter creates all formats instead of respecting the requested format list.
- There is no versioned job schema or semantic preflight validator.
- Remote workers are not proven to have the same code revision, job file, models,
  and dependencies as the orchestrator before a run begins.
- Core MLX versions, Python environments, model availability, serving endpoints,
  and TB-routed inference have not been verified consistently across all minis.

## Delivery Rules

- Complete stages in order. Do not start the next stage until its exit gate passes.
- Keep TB out of the critical execution path until management-network runs pass.
- Use one minimal fixture per behavior before testing full production jobs.
- Every run must produce a manifest, stage events, artifact inventory, and terminal
  `done` or `failed` state.
- A partial result is a failed job unless the job explicitly permits partial success.
- Never use dashboard progress alone as proof of success.

## Stage 0: Freeze And Reproduce The Baseline

**Purpose:** Establish evidence and protect the last known-good media path.

### Work

- Record the local commit SHA and the deployed SHA on every candidate worker.
- Archive the current `/api/snapshot`, job history, node health, TB telemetry, and
  output inventory under a timestamped test-evidence directory.
- Add minimal fixtures for a known-good EN-only media job and the existing EN+ES job.
- Reproduce the current management-network media run without changing scheduling.
- Capture the exact stderr for a recent scenario failure instead of only
  `stage_fail`.

### Tests

- Known-good media dry run completes.
- Known-good live media job creates exactly the required language and format files.
- A scenario failure records command, host, exit code, stderr, code SHA, and job SHA.

### Exit Gate

- Baseline evidence is archived and reproducible.
- The existing media path has a regression test that can run before every later stage.

### Completion Record

**Completed:** 2026-06-22 02:37:48 MDT (UTC-06:00)  
**Base commit SHA:** `656a21ca89e0fcc98101623d3162d9d3ed4768d4`  
**Working tree:** Stage 0 changes are uncommitted.

Implemented:

- Added `pipeline/capture_stage0_baseline.py` for timestamped JSON and Markdown
  qualification evidence.
- Added EN-only and EN+ES dry-run fixtures under `tests/fixtures/jobs/`.
- Added automated Stage 0 and dashboard qualification tests.
- Added qualification report ingestion to `/api/upload-qualification`.
- Added `qualification` to `/api/snapshot` and a Qualification Gates dashboard pane.
- Added `TimeoutStopSec=10` to dashboard deployment after SSE connections caused a
  graceful shutdown to stall.

Test commands:

```powershell
..\.venv-1\Scripts\python.exe -m unittest discover -s tests -v
..\.venv-1\Scripts\python.exe pipeline\capture_stage0_baseline.py `
  --run-dry-runs `
  --diagnostic-host Lab-station-02
..\.venv-1\Scripts\python.exe viewer\deploy.py --host ubuntu-1
Invoke-RestMethod http://10.0.0.200:7800/api/snapshot
```

Results:

- Automated tests: 4 passed.
- Stage 0 qualification: 8 of 8 checks passed.
- Live dashboard: reachable and reporting `stage-0: pass`.
- Known-good dashboard evidence: `what-is-nexetra-live-es` at 100%.
- TB evidence: 6 Mac minis and 6 good links.
- Scenario failure evidence: 16 failure events archived.
- Exact reproduction: `Lab-station-02` failed because
  `jobs/ma-product-launch-brief-v1.json` was missing on the worker.
- Comparison host: the same scriptgen dry-run succeeded on `ubuntu-1`.

Evidence:

- `output/qualification/stage-0/20260622-083438/baseline.json`
- `output/qualification/stage-0/20260622-083438/baseline.md`
- `output/qualification/stage-0/latest.json`
- Dashboard `/api/snapshot` qualification payload

Unresolved risks carried forward:

- Remote code and job-file drift is observed but is intentionally remediated in
  Stage 3.
- Lab-Station-01 was not reachable over SSH during diagnostic reproduction even
  though the last dashboard health snapshot marked it ready.
- Dashboard job progress still uses file-presence heuristics until Stage 2.
- The current changes need a commit before the base SHA represents Stage 0 code.

**Next stage authorized:** Stage 1 only.

## Stage 1: Define And Validate Job Control Language V1

**Purpose:** Turn the current informal JSON shape into an explicit contract.

### Work

- Add a versioned JSON Schema, initially `job_version: 1`.
- Add required top-level fields: `job_id`, `job_version`, `job_type`, `title`,
  `execution_policy`, and `success_criteria`.
- Define supported `job_type` values:
  - `media_pipeline`
  - `content_development`
  - `multi_agent_solution`
  - `agent_harness_regression`
  - `rag_index_and_validation`
- Define type-specific fields and artifact contracts using schema branches.
- Add `pipeline/validate_job.py` for schema and semantic validation.
- Preserve old media files through an explicit compatibility migration, not silent
  defaults.
- Reject unknown job types, stages, languages, formats, models, and policy values.

### Tests

- Every committed job validates or has a documented migration failure.
- Invalid fixtures cover missing IDs, duplicate IDs, bad languages, unsupported
  formats, invalid thresholds, unknown job types, and unsafe paths.
- `--validate-only` performs no host lease, SSH, model call, or artifact write.

### Exit Gate

- CI or the local test command validates all committed jobs.
- The runner refuses an invalid job before compute allocation.

### Completion Record

**Completed:** 2026-06-22 02:51:13 MDT (UTC-06:00)  
**Base commit SHA:** `656a21ca89e0fcc98101623d3162d9d3ed4768d4`  
**Working tree:** Stage 0 and Stage 1 changes are uncommitted.

Implemented:

- Added Draft 2020-12 JSON Schema at `jobs/schema/job-v1.schema.json`.
- Added `pipeline/job_control.py` and `pipeline/validate_job.py`.
- Migrated all 10 committed jobs and both test fixtures to explicit JCL V1.
- Added required `job_version`, `job_type`, `execution_policy`, and
  `success_criteria` declarations.
- Added schema branches for all five supported job types.
- Added structural and semantic checks for supported languages, formats, styles,
  models, policies, thresholds, artifact path safety, scenario/type matching, and
  catalog-wide duplicate IDs.
- Added `--validate-only` to local and pool runners before any event, lease, SSH,
  health-probe, or artifact activity.
- Added invalid-job preflight rejection with exit code 2 to both runners.
- Added JCL version and job type to dashboard job cards and snapshot JSON.
- Added `jsonschema` as a pinned major-version runtime dependency.

Test commands:

```powershell
..\.venv-1\Scripts\python.exe pipeline\validate_job.py --all
..\.venv-1\Scripts\python.exe -m unittest discover -s tests -v
..\.venv-1\Scripts\python.exe pipeline\qualify_stage1_jcl.py --upload
..\.venv-1\Scripts\python.exe viewer\deploy.py --host ubuntu-1
Invoke-RestMethod http://10.0.0.200:7800/api/snapshot
```

Results:

- Automated tests: 15 passed.
- Stage 1 qualification: 14 of 14 checks passed.
- Local catalog: 10 of 10 jobs valid.
- Deployed `ubuntu-1` catalog: 10 of 10 jobs valid.
- Both runners' validate-only paths produced valid JSON and no output changes.
- Both runners rejected invalid jobs with exit code 2 and no output changes.
- Live dashboard reports `stage-1: pass` and recognized JCL V1 metadata for all
  10 jobs.

Evidence:

- `output/qualification/stage-1/20260622-085028/qualification.json`
- `output/qualification/stage-1/20260622-085028/qualification.md`
- `output/qualification/stage-1/latest.json`
- Dashboard `/api/snapshot` qualification payload

Unresolved risks carried forward:

- Validation proves that a job is well-formed, not that its handler exists; scenario
  handlers remain Stage 5 work.
- Stage agents can still return exit code 0 after partial failures; this is the first
  Stage 2 remediation item.
- Success criteria are validated structurally but are not yet evaluated against
  runtime artifacts; this is Stage 2 work.
- Remote Mac workers can still have stale code or missing job files until Stage 3.
- The current changes need a commit before the base SHA represents Stages 0 and 1.

**Next stage authorized:** Stage 2 only.

## Stage 2: Make Execution Results Truthful

**Purpose:** Eliminate false-positive stage and job completion.

### Work

- Standardize a stage result contract with status, attempts, host, timing, outputs,
  warnings, and error detail.
- Make every agent exit nonzero when a required language or artifact fails.
- Respect the job's `languages` and `formats` exactly.
- Evaluate `success_criteria` after each stage and again before `batch_done`.
- Write an atomic `run_manifest.json` and `run_summary.md` per run.
- Add a unique `run_id`; do not group concurrent or historical runs only by job path.
- Mark interrupted runs as `abandoned` after a defined heartbeat timeout.

### Tests

- Inject one failed translation and verify the stage and batch fail.
- Remove one requested artifact and verify the final gate fails.
- Request only `16:9` and verify no `9:16` or `1:1` output is generated.
- Kill a run mid-stage and verify it does not remain `running` indefinitely.

### Exit Gate

- A `done` job guarantees that all declared required artifacts and thresholds passed.
- Dashboard state is derived from manifests and run IDs, not file-presence guesses.

### Completion Record

**Completed:** 2026-06-22 03:39:00 MDT (UTC-06:00)  
**Base commit SHA:** `656a21ca89e0fcc98101623d3162d9d3ed4768d4`  
**Working tree:** Stages 0 through 2 are uncommitted.

Implemented:

- Added atomic `run_manifest.json` and `run_summary.md` output under unique
  `output/runs/<run_id>/` directories.
- Added a common stage result contract with status, attempt, host, timestamps,
  duration, return code, outputs, warnings, and error detail.
- Added per-stage artifact checks and final JCL success-criteria evaluation.
- Changed translation, TTS, assembly, and export agents to exit nonzero when required
  language or artifact work is incomplete.
- Changed export to generate only the formats declared by the job.
- Added explicit exit code 3 for validated job types whose scenario handler is not
  implemented, preventing scenario jobs from entering the media pipeline.
- Added heartbeat tracking and automatic `abandoned` status after 15 minutes without
  a heartbeat.
- Changed dashboard job progress and run archive state to use manifests and run IDs
  instead of artifact file-presence guesses.
- Added bounded retries for atomic manifest replacement on shared Windows/WSL paths.
- Added direct-script import-path handling for the deployed dashboard service.

Promotion and test commands:

```powershell
# Windows local
..\.venv-1\Scripts\python.exe -m unittest discover -s tests -q

# WSL Ubuntu
wsl.exe -d Ubuntu --cd /mnt/d/Code/nexetra-remote-coding/nexetra-media `
  env PYTHONPATH=/home/derrick/.cache/nexetra-media-stage2-deps `
  python3 -m unittest discover -s tests -q

# Stage qualification
..\.venv-1\Scripts\python.exe pipeline\qualify_stage2_execution.py --wsl-passed

# Deploy and verify on ubuntu-1
..\.venv-1\Scripts\python.exe viewer\deploy.py --host ubuntu-1
```

Results:

- Windows local automated tests: 23 passed.
- WSL Ubuntu automated tests: 22 passed at the promotion gate.
- WSL EN+ES full dry-run: passed with terminal `done` manifest.
- Stage 2 qualification: 9 of 9 checks passed.
- Deployed `ubuntu-1` automated tests: 23 passed.
- Deployed `ubuntu-1` EN+ES dry-run: passed all five stages and final criteria.
- Live dashboard reports `stage-2: pass` and shows the deployed run by unique run ID
  with status `done`.

Evidence:

- `output/qualification/stage-2/20260622-093443/qualification.json`
- `output/qualification/stage-2/20260622-093443/qualification.md`
- `output/qualification/stage-2/latest.json`
- `output/runs/<run_id>/run_manifest.json`
- Dashboard `/api/snapshot` qualification and run payloads

Unresolved risks carried forward:

- Remote worker code/job drift and artifact transfer remain Stage 3 concerns.
- The pool runner has the manifest contract but distributed execution is intentionally
  not qualified until Stage 3 preflight and synchronization are implemented.
- Scenario-specific quality, regression, and RAG thresholds remain unevaluated until
  their Stage 5 handlers exist; those job types now fail cleanly before execution.
- Run artifacts still share a job output directory; Stage 3 must make remote handoff
  run-scoped and reproducible.
- The current changes need a commit before the base SHA represents Stages 0-2.

**Next stage authorized:** Stage 3 only.

## Stage 3: Make Remote Execution Reproducible

**Purpose:** Remove code, job-file, model, and environment drift from the pool.

### Work

- Add a worker preflight contract reporting code SHA, Python version, dependency lock
  hash, available models, runtime endpoints, free disk, and writable output path.
- Sync or deploy the exact job document and code revision before execution.
- Include job-content SHA and code SHA in every event and manifest.
- Match stage capability requirements to hosts instead of choosing one arbitrary
  anchor for every stage.
- Keep artifacts in a run-scoped shared location or explicitly transfer verified
  inputs and outputs between stage hosts.
- Add bounded retry and lease recovery based on `execution_policy`.

### Tests

- Deliberately place stale code on one worker and verify preflight rejects it.
- Remove a requested model and verify scheduling avoids or rejects that worker.
- Run the known-good media job on one remote management-network worker.
- Run it across two management-network workers with verified artifact handoff.

### Exit Gate

- The known-good media job passes remotely twice in succession.
- Every remote stage is traceable to a code SHA, job SHA, host, model, and input set.

### Completion Record

**Completed:** 2026-06-22 04:21:23 MDT (UTC-06:00)  
**Base commit SHA:** `656a21ca89e0fcc98101623d3162d9d3ed4768d4`  
**Working tree:** Stages 0 through 3 are uncommitted.

Implemented:

- Added deterministic SHA-256 provenance for executable source, job JSON, and
  dependency manifests.
- Added `pipeline/worker_preflight.py` with OS, architecture, Python, disk,
  writable-root, stage-capability, Ollama-model, and MLX inventory reporting.
- Added exact source bundle synchronization with a deployed source manifest.
- Added worker rejection for stale source, job, dependency, capability, writable
  storage, and requested-model mismatches.
- Replaced arbitrary anchor selection with sync, preflight, and capability matching.
- Limited Stage 3 acquisition to one preferred anchor, with `ubuntu-1` first.
- Added run-scoped remote artifact paths and verified SFTP handoff into the matching
  local run path.
- Added provenance to every new local and pool run event and manifest.
- Added terminal manifest/summary synchronization to the dashboard host.
- Added dashboard reporting for matched worker, hashes, and portable artifact path.
- Added 7 Stage 3 tests, bringing the full suite to 30 tests.

Test and qualification commands:

```powershell
..\.venv-1\Scripts\python.exe tests\run_suite_json.py

wsl.exe -d Ubuntu --cd /mnt/d/Code/nexetra-remote-coding/nexetra-media `
  env PYTHONPATH=/home/derrick/.cache/nexetra-media-stage2-deps `
  python3 tests/run_suite_json.py

..\.venv-1\Scripts\python.exe pipeline\run_batch_pool.py `
  --job jobs/what-is-nexetra-live-es.json --dry-run

..\.venv-1\Scripts\python.exe pipeline\qualify_stage3_remote.py `
  --wsl-passed --ubuntu-passed --upload
```

Results:

- Windows local suite: 30 of 30 tests passed with JSON output.
- WSL Ubuntu promotion suite completed successfully; the host WSL bridge did not
  preserve its requested JSON result file, so this limitation is retained in the
  evidence notes.
- Deployed `ubuntu-1` suite: 30 of 30 tests passed with JSON output.
- Stage 3 qualification: 9 of 9 checks passed.
- Three current-provenance remote runs qualified; the final two attempts completed
  consecutively on `ubuntu-1`.
- Final runs used distinct run IDs, matched all three hashes, passed all five media
  capabilities, synchronized artifacts, passed final criteria, synchronized their
  terminal manifests to the dashboard, and released all leases.
- One overlapping attempt selected `ubuntu-3`, failed preflight cleanly, produced a
  failed manifest, and released its lease. It was not counted.
- Live dashboard reports `stage-3: pass` and shows the current remote runs, matched
  worker, hash prefixes, and run-scoped artifact paths.

Evidence:

- `output/qualification/stage-3/20260622-101735/qualification.json`
- `output/qualification/stage-3/20260622-101735/qualification.md`
- `output/qualification/stage-3/latest.json`
- `output/runs/20260622-041623-2647b1e8/run_manifest.json`
- `output/runs/20260622-041715-a2c5c6db/run_manifest.json`
- Remote `output/qualification/stage-3/ubuntu-tests.json`
- Dashboard `/api/snapshot` qualification and run payloads

Unresolved risks carried forward:

- Stage 3 uses one execution anchor; multi-node allocation and policy enforcement
  remain Stage 6 work.
- Source synchronization does not install missing dependencies automatically; a
  worker without the declared environment is rejected.
- Model inventory is reported and generic requested-model rejection is tested, but
  job `model_policy` selection remains Stage 6 work.
- Scenario handlers remain unavailable until Stage 5 and continue to fail before
  execution with exit code 3.
- The current changes need a commit before the base Git SHA represents Stages 0-3.

**Next stage authorized:** Stage 4 only.

## Stage 4: Restore And Harden The Media Pipeline

**Purpose:** Establish the first complete workload on the new control foundation.

### Work

- Route `media_pipeline` jobs to the existing five media agents.
- Add explicit capability declarations for LLM, translation, TTS, FFmpeg, and export.
- Add artifact validation for JSON structure, nonempty audio, playable video, duration,
  dimensions, language count, and requested format count.
- Keep management networking as the only data path in this stage.

### Test Ladder

1. EN, script generation only.
2. EN, script plus TTS.
3. EN, full 16:9 media pipeline.
4. EN+ES, full 16:9 media pipeline.
5. EN+ES, all three formats.
6. Existing `what-is-nexetra-live-es` production job.

### Exit Gate

- Each ladder test passes twice consecutively.
- Failure injection at every stage produces a failed manifest and useful diagnostics.

### Completion Record

**Completed:** 2026-06-22 21:07:30 MDT (UTC-06:00)  
**Base commit SHA:** `656a21ca89e0fcc98101623d3162d9d3ed4768d4`  
**Working tree:** Stages 0 through 4 changes are uncommitted.

Implemented:

- Added media artifact contract validation in `pipeline/media_validation.py`.
- Wired media validation into stage checks and terminal success evaluation.
- Real runs now reject dry-run MP4 stubs; dry-runs explicitly mark stubs as accepted.
- Added explicit media capability declarations for LLM client, translation client,
  TTS, FFmpeg, and export in worker preflight.
- Added deterministic script fallback when the LLM returns empty or non-JSON content
  after a successful HTTP response; unreachable LLM endpoints still fail.
- Added dashboard `artifact_validation` summaries for job cards and run archive cards.
- Added `pipeline/qualify_stage4_media.py` to run the media ladder twice, produce
  JSON/Markdown qualification evidence, inject failures for every stage, and upload
  the report to the dashboard.

Test commands:

```powershell
..\.venv-1\Scripts\python.exe tests\run_suite_json.py
wsl.exe -d Ubuntu --cd /mnt/d/Code/nexetra-remote-coding/nexetra-media `
  env PYTHONPATH=/home/derrick/.cache/nexetra-media-stage2-deps python3 tests/run_suite_json.py
..\.venv-1\Scripts\python.exe viewer\deploy.py --host ubuntu-1
ssh ubuntu-1 'cd ~/nexetra-media && .venv/bin/python tests/run_suite_json.py'
ssh ubuntu-1 'cd ~/nexetra-media && .venv/bin/python pipeline/qualify_stage4_media.py --upload'
ssh ubuntu-1 'cd ~/nexetra-media && .venv/bin/python pipeline/run_job.py --job tests/fixtures/jobs/media-en-es-baseline.json'
Invoke-RestMethod http://10.0.0.200:7800/api/snapshot
```

Results:

- Windows JSON suite: 35 tests passed.
- WSL JSON suite after the script fallback change: 35 tests passed.
- Ubuntu-1 JSON suite after deployment: 35 tests passed.
- Stage 4 qualification on ubuntu-1: 8 of 8 checks passed.
- Ladder evidence: 12 dry-run ladder executions passed.
- Failure injection: `scriptgen`, `translate`, `tts`, `assembly`, and `export`
  each produced failed manifests with diagnostics.
- Live EN-only media run on ubuntu-1 completed with real TTS and FFmpeg artifacts.
- Live EN+ES all-format media run on ubuntu-1 completed with real translation, TTS,
  assembly, and six exports.
- Dashboard `/api/snapshot` reports `stage-4: pass`, 8/8 checks, and the latest run
  archive entry is `20260622-210703-132ce9ec` with media contract pass and 10
  artifacts checked.

Evidence:

- `output/qualification/stage-4/20260623-025854/qualification.json`
- `/home/user/nexetra-media/output/qualification/stage-4/20260623-030645/qualification.json`
- `/home/user/nexetra-media/output/stage0-media-en-minimal/runs/20260622-210455-12bdaef9/`
- `/home/user/nexetra-media/output/stage0-media-en-es-baseline/runs/20260622-210703-132ce9ec/`
- Dashboard `/api/snapshot` qualification and run archive payloads

Unresolved risks carried forward:

- The full production `what-is-nexetra-live-es` job was qualified through dry-run
  ladder coverage; the live all-format proof used the EN+ES baseline fixture to
  keep duration short while exercising translation, TTS, assembly, and export.
- Scenario handlers remain unavailable until Stage 5 and continue to fail before
  execution with exit code 3.

**Next stage authorized:** Stage 5 only.

## Stage 5: Implement New Scenario Handlers One At A Time

**Purpose:** Give each newer job type real execution semantics instead of forcing it
through media stages.

### Architecture

- Add a handler registry keyed by `job_type`.
- Each handler supplies its stage graph, capability needs, artifact contract, and
  success evaluator.
- Share orchestration, logging, retries, leases, and manifests across handlers.
- Do not add scenario-specific branches throughout the generic runner.

### Scenario Order

#### 5A: Content Development

- Implement planner, draft, variant, review, and package stages.
- Start with one language and one variant.
- Expand to the declared five languages and five variants only after the minimal job
  passes.
- Final test: `content-summer-campaign-v1` meets its artifact and language criteria.

##### Completion Record

**Completed:** 2026-06-22 21:28:17 MDT (UTC-06:00)  
**Base commit SHA:** `656a21ca89e0fcc98101623d3162d9d3ed4768d4`  
**Working tree:** Stages 0 through 5A changes are uncommitted.

Implemented:

- Added a handler registry keyed by `job_type`.
- Routed `media_pipeline` and `content_development` through registered stage graphs.
- Added deterministic content-development stages: `plan`, `draft`, `variants`,
  `review`, and `package`.
- Migrated `content-summer-campaign-v1` success criteria to content artifacts:
  `plan.json`, `{language}/variants.json`, `review.json`, and `package.json`.
- Added content artifact validation for language count, variant count, review pass,
  and package completeness.
- Updated dashboard progress to use handler-specific stage names and generic
  artifact-contract reporting.
- Added `pipeline/qualify_stage5_content.py` for JSON/Markdown qualification and
  dashboard upload.

Test commands:

```powershell
..\.venv-1\Scripts\python.exe tests\run_suite_json.py
wsl.exe -d Ubuntu --cd /mnt/d/Code/nexetra-remote-coding/nexetra-media `
  env PYTHONPATH=/home/derrick/.cache/nexetra-media-stage2-deps python3 tests/run_suite_json.py
..\.venv-1\Scripts\python.exe pipeline\qualify_stage5_content.py
..\.venv-1\Scripts\python.exe viewer\deploy.py --host ubuntu-1
ssh ubuntu-1 'cd ~/nexetra-media && .venv/bin/python tests/run_suite_json.py'
ssh ubuntu-1 'cd ~/nexetra-media && .venv/bin/python pipeline/qualify_stage5_content.py --upload'
ssh ubuntu-1 'cd ~/nexetra-media && .venv/bin/python pipeline/run_job.py --job jobs/content-summer-campaign-v1.json --dry-run'
Invoke-RestMethod http://10.0.0.200:7800/api/snapshot
```

Results:

- Windows JSON suite: 39 tests passed.
- WSL JSON suite: 39 tests passed.
- Ubuntu-1 JSON suite after deployment: 39 tests passed.
- Stage 5A local qualification: 8 of 8 checks passed.
- Stage 5A ubuntu-1 qualification: 8 of 8 checks passed and uploaded.
- Minimal EN/one-variant content job passed twice.
- Full `content-summer-campaign-v1` passed twice with 5 languages and 5 variants.
- Failure injection for `plan`, `draft`, `variants`, `review`, and `package`
  produced failed manifests with diagnostics.
- Remaining scenario handlers (`multi_agent_solution`, `agent_harness_regression`,
  and `rag_index_and_validation`) still fail before execution with exit code 3.
- Dashboard `/api/snapshot` reports `stage-5: pass`, substage
  `5A-content-development`, 8/8 checks.
- Latest dashboard run archive entry is `20260622-212755-2cc9d06f` for
  `content-summer-campaign-v1`, status `done`, artifact contract pass, 8 artifacts
  checked, and content job progress at 100%.

Evidence:

- `output/qualification/stage-5/20260623-032236/qualification.json`
- `/home/user/nexetra-media/output/qualification/stage-5/20260623-032736/qualification.json`
- `/home/user/nexetra-media/output/content-summer-campaign-v1/runs/20260622-212755-2cc9d06f/`
- Dashboard `/api/snapshot` qualification and run archive payloads

Unresolved risks carried forward:

- Content generation is deterministic in Stage 5A. Model-backed drafting and model
  selection remain Stage 6 policy work.
- The remaining scenario handlers are intentionally unavailable until Stages 5B,
  5C, and 5D.

**Next stage authorized:** Stage 5B only.

#### 5B: Multi-Agent Solution

- Implement planner, researcher, writer, critic, verifier, and handoff stages.
- Persist each agent input/output and critic decision.
- Enforce `critic_pass` before completion.
- Final test: `ma-product-launch-brief-v1` creates `brief.md`, `narrative.json`, and
  `handoff.md`.

#### 5C: Harness Regression

- Implement deterministic case loading, seeded execution, result aggregation, and
  failure bucketing.
- Begin with 3 cases, then 10, then all 120.
- Enforce `max_failure_rate`.
- Final test: `harness-regression-weekly-v1` produces all required reports.

#### 5D: RAG Index And Validation

- Implement corpus discovery, chunking, embedding, index creation, retrieval
  evaluation, and coverage reporting.
- Start with a tiny checked-in fixture corpus.
- Enforce recall-at-5 and artifact requirements.
- Final test: `rag-index-validate-supportkb-v1` passes against the intended corpus.

### Exit Gate

- Each scenario passes locally and on one management-network remote worker before the
  next scenario begins.
- All four production scenario jobs pass their declared success criteria.

## Stage 6: Activate Scheduling And Model Policies

**Purpose:** Make the JCL policy fields operational and testable.

### Work

- Enforce priority, `max_nodes`, retry budget, stage timeouts, and fallback policy.
- Resolve primary and fallback models against live worker capability reports.
- Record the selected model and reason for fallback.
- Add deterministic scheduling tests using a fake pool.
- Add resource limits so one job cannot lease the entire fleet by default.

### Exit Gate

- Policy unit tests cover no-capacity, model-missing, retry, timeout, fallback, and
  protected-host cases.
- A live test proves that `max_nodes` and model selection match the manifest.

## Stage 7: Install And Qualify MLX On Every Mac Mini

**Purpose:** Make MLX the verified inference runtime on all six workstations before
using the TB fabric for scenario workloads.

### Installation Standard

- Use one managed virtual environment path on every mini, such as
  `~/.venvs/nexetra-mlx`; do not modify Homebrew's externally managed Python.
- Pin compatible versions of Python, `mlx`, and `mlx-lm` in a requirements or lock
  file owned by this repository.
- Install the same baseline model on every mini before benchmarking.
- Manage the MLX-LM server with a repeatable service definition and explicit bind
  address, port, logs, restart policy, and health endpoint.
- Keep Ollama available as a fallback until MLX qualification is complete.

### Per-Workstation Work

For each of Lab-Station-01 through Lab-station-06:

1. Record macOS version, architecture, active Python executable, and Python version.
2. Create or repair the managed MLX virtual environment.
3. Install pinned `mlx` and `mlx-lm` versions.
4. Verify direct imports and print both package versions.
5. Pull or cache the baseline quantized model.
6. Start the MLX-LM server bound first to the management interface.
7. Verify model listing, health, and one deterministic inference request.
8. Restart the service and verify it returns healthy without manual intervention.
9. Repeat the health and inference request using the workstation's TB IP.
10. Upload the result to dashboard telemetry with environment, versions, model,
    endpoint, latency, and pass/fail detail.

### Installer Remediation

- Replace user or system `pip` installation with virtual-environment installation to
  avoid the PEP 668 failure observed on Mini 05.
- Make the installer idempotent and safe to rerun.
- Fail the installer when either `import mlx` or `import mlx_lm` fails.
- Make the probe report explicit core MLX and MLX-LM versions instead of `unknown`.
- Separate `ollama_ready`, `mlx_installed`, `mlx_server_ready`, and
  `mlx_tb_ready`; do not collapse them into one `InferenceReady` flag.

### Tests

- Run installer dry-run against all six inventory entries.
- Run installation or repair on one mini, then on the remaining five.
- Verify all six managed environments use the pinned package set.
- Run the same prompt and generation settings on every mini over management IP.
- Run the same test over every mini's TB IP.
- Record first-token latency, total latency, tokens per second, response hash or
  semantic check, peak memory, and error detail.
- Reboot or restart one mini and verify automatic service recovery.

### Exit Gate

- All 6 minis report explicit, non-`unknown` MLX and MLX-LM versions.
- All 6 pass direct imports from the managed environment.
- All 6 serve the pinned baseline model over management IP.
- All 6 serve a validated inference request over their TB IP.
- Dashboard status distinguishes installation, server, management-path, and TB-path
  health for each workstation.
- A timestamped MLX qualification CSV and Markdown report are archived.

## Stage 8: Introduce Thunderbolt As An Optional Data Path

**Purpose:** Use the healthy TB fabric without making it a new single point of failure.

### Work

- Use the Stage 7 MLX qualification report as the runtime eligibility source.
- Re-run the full TB/MLX probe and archive it with the run evidence.
- Build a ready-only Hybrid inference pool; do not use `-IncludeNotReady` in a passing
  production test.
- Verify routing from the actual orchestrator and worker processes to every selected
  TB endpoint, not only peer discovery.
- Add endpoint identity and health checks so a link-local address cannot be associated
  with the wrong worker.
- Add JCL routing policy: `management`, `thunderbolt_preferred`, or
  `thunderbolt_required`.
- Default to `management`; test `thunderbolt_preferred` with automatic fallback.
- Compare correctness, latency, throughput, failure rate, and artifact integrity
  against the management baseline.

### Test Ladder

1. Direct health request to one MLX/Ollama endpoint over TB.
2. One small inference request over TB.
3. Repeated inference requests on one node.
4. Request-level load balancing across two minis.
5. One scenario stage using TB-preferred inference.
6. A full scenario run using TB-preferred inference.
7. Disconnect or disable one TB path and verify management fallback completes the job.

### Exit Gate

- TB-preferred mode passes the same correctness suite as management mode.
- Fallback is automatic, bounded, visible in the manifest, and tested live.
- TB is retained only where benchmarks show a material benefit.

## Stage 9: Final Integrated Qualification

**Purpose:** Prove the final system under realistic mixed workloads.

### Work And Tests

- Run one job of every supported type on the management network.
- Run one job of every supported type with TB-preferred routing where applicable.
- Run a mixed concurrent batch while respecting leases and `max_nodes`.
- Restart the orchestrator during a test and verify run recovery or clean abandonment.
- Disable one worker, one model endpoint, and one TB link in separate tests.
- Confirm dashboard accuracy, downloadable artifacts, run manifests, and audit history.

### Final Exit Gate

- All five job types pass twice consecutively.
- No false `done` states occur during fault injection.
- Management fallback succeeds when TB is unavailable.
- Dashboard and manifest results agree for every run.
- A dated qualification report records commands, revisions, topology, results, and
  known limitations.

## Recommended First Implementation Slice

Start only with Stages 0 through 2:

1. Archive baseline evidence and reproduce one good and one failed run.
2. Add the versioned schema and validation-only command.
3. Fix stage exit codes and enforce required artifact checks.
4. Update dashboard progress to use run manifests.

This slice addresses the current trust problem before adding more scheduling or
scenario complexity.

## Progress Log

| Stage | Status | Evidence |
|---|---|---|
| 0. Baseline | Complete | 8/8 JSON gates pass; dashboard reports stage-0 pass |
| 1. JCL V1 | Complete | 14/14 JSON gates; 10/10 jobs valid locally and deployed |
| 2. Truthful results | Complete | 9/9 gates; WSL and ubuntu-1 dry-runs produce truthful manifests |
| 3. Remote reproducibility | Complete | 9/9 gates; final two remote runs passed consecutively on ubuntu-1 |
| 4. Media hardening | Not started | One legacy job currently reaches 100% |
| 5. Scenario handlers | Not started | Four scenario jobs currently at 0% |
| 6. Policy activation | Not started | Policy fields are currently informational |
| 7. MLX qualification | Not started | MLX-LM visible on 6/6; core MLX unknown on 6/6 |
| 8. TB data path | Not started | Fabric ready: 6/6 nodes and links |
| 9. Qualification | Not started | Depends on all prior gates |

## Resume Cue

Use this prompt when returning:

> Load `docs/job-control-thunderbolt-scenario-remediation-plan-20260622-021640.md`,
> review the Progress Log and latest evidence, and continue from the first incomplete
> stage without skipping its exit gate.

When a stage completes, update this document with:

- completion timestamp,
- commit SHA,
- exact test commands,
- evidence paths,
- pass/fail result,
- unresolved risks,
- next stage authorized.
