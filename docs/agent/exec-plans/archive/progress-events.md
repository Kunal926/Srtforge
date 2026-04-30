# ExecPlan: progress-events

## Purpose / big picture

Wire the already-documented worker `progress` event through the Python
pipeline so GUI worker clients can receive fine-grained, monotonic progress
fractions during ASR and subtitle post-processing. Done means the pipeline
emits `progress` events through the same per-job worker emitter path used by
stage events, the event remains compatible with the existing Rust/TypeScript
forwarding layers, and tests prove emitted fractions never decrease across a
run.

## Context and orientation

- `docs/contracts/worker-protocol.md` already documents `progress`.
- `docs/contracts/worker-events.schema.json` already accepts `progress`.
- `srtforge/worker_protocol.py` already exposes `progress_event`.
- `srtforge/logging.py` contains the existing process-global stage event
  emitter installed per worker job.
- `srtforge/cli.py` installs the per-job emitter before running the pipeline.
- `srtforge/pipeline.py` owns ASR and post-processing loops.
- `tests/test_pipeline_events.py` is the harness pattern to extend.
- `srtforge-studio/src-tauri/src/lib.rs` forwards worker JSON lines without
  inspecting each event shape.
- `srtforge-studio/src/types.ts` and `srtforge-studio/src/store.ts` already
  include the documented progress event contract or must be verified before
  closing this plan.

## Progress

- [x] Phase 1: Create protocol ExecPlan and inspect lockstep files.
- [x] Phase 2: Patch Python pipeline progress emission.
- [x] Phase 3: Add monotonic progress tests.
- [x] Phase 4: Run protocol and lightweight validation.
- [x] Phase 5: Update handoff and archive/close the plan state if complete.

## Milestones

Phase 1 is done when this ExecPlan exists in `active/` and the current worker
protocol files have been inspected.

Phase 2 is done when ASR and post-processing emit `progress` events using the
worker's per-job emitter path and no progress event is emitted outside an
active worker job.

Phase 3 is done when `tests/test_pipeline_events.py` has a
`TestProgressMonotonic` class asserting non-decreasing `fraction` values across
the run.

Phase 4 is done when the focused protocol pytest selection passes, TypeScript
and Rust checks pass or are recorded with exact blockers, docs checks pass, and
the lightweight harness result is recorded.

Phase 5 is done when `docs/agent/HANDOFF.md` records changed layers, commands,
skips, blockers, and next action.

## Steps

1. Inspect protocol files:

   ```powershell
   Get-Content docs/contracts/worker-protocol.md
   Get-Content docs/contracts/worker-events.schema.json
   Get-Content srtforge/worker_protocol.py
   Get-Content srtforge/logging.py
   Get-Content srtforge/cli.py
   Get-Content srtforge/pipeline.py
   Get-Content srtforge-studio/src/types.ts
   Get-Content srtforge-studio/src/store.ts
   Get-Content srtforge-studio/src-tauri/src/lib.rs
   Get-Content tests/test_pipeline_events.py
   ```

2. Patch the Python emitter path, keeping docs/schema/Rust/TypeScript unchanged
   unless inspection shows they disagree with the documented `progress` shape.

3. Add focused tests:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests/test_pipeline_events.py -q
   ```

4. Run protocol lockstep checks:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests/test_worker_protocol.py tests/test_cli_worker.py tests/test_pipeline_events.py -q
   cd srtforge-studio
   pnpm exec tsc --noEmit
   cd src-tauri
   cargo check
   cd ..\..
   .\.venv\Scripts\python.exe scripts/check_docs.py
   ```

5. Run the available lightweight harness:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   .\scripts\check.ps1
   ```

## Validation and acceptance

- `tests/test_pipeline_events.py` passes.
- `tests/test_worker_protocol.py tests/test_cli_worker.py tests/test_pipeline_events.py` passes.
- `pnpm exec tsc --noEmit` passes from `srtforge-studio/`.
- `cargo check` passes from `srtforge-studio/src-tauri/`.
- `scripts/check_docs.py` passes.
- Lightweight harness passes or any environment blocker is recorded precisely.

## Surprises and discoveries

- Rust does not need a shape-specific change for `progress`; it deserializes
  the worker line into an event name plus flattened JSON and re-emits it
  verbatim.
- TypeScript already contained the `progress` union case and reducer handling
  for either `fraction` or `progress`, so no frontend contract edit was needed.
- The Codex action wrapper still depends on `pwsh`, but the same harness passes
  directly under Windows PowerShell 5.1.

## Decision log

- 2026-04-29: Created an ExecPlan even though the event shape already exists,
  because the `protocol-change` skill requires an ExecPlan for protocol event
  wiring and ordering changes. Decided by Codex following user instruction to
  use the skill.
- 2026-04-29: Reused `srtforge.logging.set_event_emitter` for `progress`
  events rather than adding a second worker callback path, because the worker
  already installs and clears that per-job emitter safely.
- 2026-04-29: Mapped local ASR/post-processing fractions into a monotonic
  run-level fraction in `pipeline.py`, because the GUI's queue progress bar
  consumes one job-level 0..1 value.

## Outcomes and retrospective

Shipped:

- `progress` events now emit during ASR generation, subtitle post-processing,
  and final SRT write.
- ASR engines accept optional `progress_callback` parameters while keeping
  existing call sites compatible.
- `tests/test_pipeline_events.py` includes `TestProgressMonotonic` and asserts
  non-decreasing fractions plus ASR/post stage coverage.
- Contract and QUALITY docs no longer claim progress is reserved/not emitted.

Validation:

- `.\.venv\Scripts\python.exe -m pytest tests/test_pipeline_events.py -q`
  passed: 8 passed.
- `.\.venv\Scripts\python.exe -m pytest tests/test_worker_protocol.py tests/test_cli_worker.py tests/test_pipeline_events.py -q`
  passed: 34 passed.
- `pnpm exec tsc --noEmit` passed from `srtforge-studio/`.
- `cargo check` passed from `srtforge-studio/src-tauri/`.
- `.\.venv\Scripts\python.exe scripts\check_docs.py` passed.
- `.\.venv\Scripts\python.exe -m ruff check srtforge tests scripts` passed.
- `. .\.venv\Scripts\Activate.ps1; & .\scripts\check.ps1` passed: 76 passed,
  all executed checks passed.

## Idempotence and recovery

Plan complete and archived. To recover or verify later, rerun:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_worker_protocol.py tests/test_cli_worker.py tests/test_pipeline_events.py -q
. .\.venv\Scripts\Activate.ps1; & .\scripts\check.ps1
```
