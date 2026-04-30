# ExecPlan: studio-live-logs

## Purpose / big picture

Persist the Studio Active-pane live debug/Typer log stream per job and expose it
separately from the existing pipeline performance/timing log in History. Done
means finished History rows offer two distinct log actions: Performance log
opens the pipeline `RunLogger` file, and Debug log opens the saved live Studio
log.

## Context and orientation

- `srtforge/logging.py` writes pipeline performance logs in `logs/<run_id>.log`
  and `logs/srtforge.log`.
- `srtforge/pipeline.py` returns `PipelineResult.run_id` but not the log path.
- `srtforge/cli.py` emits transcribe worker events consumed by Studio.
- `srtforge-studio/src-tauri/src/lib.rs` forwards worker stdout/stderr and is
  the right place to capture Studio debug logs.
- `srtforge-studio/src/store.ts`, `src/types.ts`, `src/lib/locate.ts`, and
  History/Queue components own row metadata and log actions.
- Protocol changes must update `docs/contracts/worker-protocol.md`,
  `docs/contracts/worker-events.schema.json`, Python helpers, TypeScript types,
  Rust forwarding, and tests in lockstep.

## Progress

- [x] Phase 1: Add durable plan and protocol contract for log metadata.
- [x] Phase 2: Emit Python performance log metadata for transcribe jobs.
- [x] Phase 3: Capture Tauri debug logs to `logs/studio-debug/*.debug.log`.
- [x] Phase 4: Expose Performance log and Debug log actions in Studio History.
- [x] Phase 5: Validate protocol, frontend, Rust, docs, and update handoff.

## Milestones

- Phase 1 done: active ExecPlan exists and names the exact metadata fields.
- Phase 2 done: CLI tests prove success/failure transcribe events include
  `run_id` and `performance_log_path` when available.
- Phase 3 done: `cargo check` passes and Rust unit tests cover debug-log helper
  behavior.
- Phase 4 done: `pnpm exec tsc --noEmit` passes and History has separate log
  actions.
- Phase 5 done: narrow protocol checks and docs checks pass; handoff is current.

## Steps

1. Add `PipelineResult.performance_log_path`, set it from `RunLogger.path`, and
   forward it from `srtforge worker` on `srt_written`, `job_completed`, and
   `job_failed` when present.
2. Update `srtforge/worker_protocol.py`, `docs/contracts/worker-protocol.md`,
   and `docs/contracts/worker-events.schema.json` with optional
   `run_id`, `performance_log_path`, and `debug_log_path` fields.
3. Add Tauri debug-log state in `src-tauri/src/lib.rs`: create
   `logs/studio-debug/<timestamp>_<job-id>.debug.log` on `job_started`, append
   live log lines and stderr/raw stdout fallback lines, enrich forwarded events
   with `debug_log_path`, and close on job terminal events.
4. Extend Studio types/store with `runId`, `performanceLogPath`, and
   `debugLogPath`; persist only completed/failed History rows plus user
   preferences.
5. Split the History/Queue log menu into `Performance log` and `Debug log`,
   with existing SRT/folder actions unchanged.
6. Run:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests/test_worker_protocol.py tests/test_cli_worker.py tests/test_pipeline_events.py -q
   cd srtforge-studio
   pnpm exec tsc --noEmit
   cd src-tauri
   cargo test debug_log --lib
   cargo check
   cd ..\..
   .\.venv\Scripts\python.exe scripts\check_docs.py
   pwsh ./scripts/check.ps1
   ```

## Validation and acceptance

- Python protocol tests pass for updated transcribe metadata.
- Rust helper tests pass for debug-log filename sanitization and formatting.
- TypeScript compile passes with new row/event fields and split log actions.
- Docs/schema/tests agree on the optional fields.
- Default lightweight harness is run if `pwsh` is available; otherwise document
  the same existing environment blocker in handoff.

## Surprises and discoveries

- `rg.exe` remains unavailable in this environment with access denied; use
  PowerShell `Select-String` fallback.
- `cargo fmt --check` required reformatting `src-tauri/src/lib.rs` after the
  debug-log bridge changes.

## Decision log

- 2026-04-30: User chose Project logs for saved live logs and Logs only for the
  first pass, so this plan excludes flag notes/report bundles.
- 2026-04-30: User clarified History needs two log choices: Performance log for
  timing and Debug log for the live Typer stream.

## Outcomes and retrospective

- Shipped separate History log actions for Performance log and Debug log.
- Performance log metadata now flows from Python `PipelineResult` through
  `srtforge worker` as `run_id` and `performance_log_path`.
- Studio captures live worker/Typer debug logs in
  `logs/studio-debug/*.debug.log` and attaches `debug_log_path` to job rows.
- Completed/failed History rows now persist so log links survive app restart.
- Validation passed: protocol pytest selection, frontend type-check, Rust
  debug-log unit tests, Rust `cargo check`, docs check, and the lightweight
  harness under Windows PowerShell 5.1.

## Idempotence and recovery

- Re-running the implementation is safe: debug log files are created only at
  runtime, not during tests except unit-test temp dirs.
- If TypeScript persistence migration fails, bump the Zustand store version and
  keep only completed/error `files` in persisted state.
- If the full harness cannot run because `pwsh` is unavailable, run the narrow
  checks above and record the blocker in `docs/agent/HANDOFF.md`.
