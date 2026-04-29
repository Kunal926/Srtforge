# QUALITY.md — known weak areas, debt, follow-ups

This file tracks places where Srtforge could be better. Agent-generated
code amplifies whatever patterns it sees, so promoting weak spots here is
how we keep the harness from rotting.

When in doubt, file it. The cost of an entry here is one line; the cost
of an unflagged repeated mistake compounds.

## Format

Each entry: short title, location, evidence, proposed fix, owner (if any).

---

## Weak / missing harness coverage

### Pipeline `progress` events not emitted at sub-stage granularity

- Location: `srtforge/pipeline.py`, `srtforge/logging.py`.
- Evidence: Stage start/end events fire, but no progress between them. The
  UI shows binary stage transitions rather than smooth bars.
- Proposed fix: Add an optional `event_sink` to `PipelineConfig` (or reuse
  `set_event_emitter`) and emit `{event:"progress", id, stage, fraction}`
  from the ASR loop and the post-processing batch loop. Worker installs
  the per-job emitter the same way it already does for `stage`.
- Acceptance: Tests in `tests/test_pipeline.py` assert monotonic progress
  values over a fake ASR run.

### No real Sonarr webhook listener wired to the Watch view

- Location: `srtforge-studio/src/components/Watch.tsx`.
- Evidence: UI banner explicitly says "listener not yet implemented".
- Proposed fix: Add a Tauri-side HTTP listener (or scheduled folder
  poller) that turns Sonarr on-import / on-upgrade events into worker
  `transcribe` jobs.
- Acceptance: Adding a Sonarr library + receiving a fake hook enqueues a
  job in the queue.

### Filesystem watcher polling not implemented

- Location: `srtforge-studio/src/components/Watch.tsx` + new Tauri command.
- Evidence: `WatchSchedule` has values like `"5m"`, `"15m"`, `"1h"`,
  `"manual"` that the UI persists but no scheduler consumes.
- Proposed fix: Add a Tauri-side watcher service that walks each enabled
  library on its schedule, diffs against the persisted `lastScanAt`, and
  enqueues transcribe jobs for new files.

### Real GPU/VRAM telemetry not implemented

- Location: `srtforge-studio/src/App.tsx` (placeholder `gpuPct={0}`,
  `vram="—"` with TODO).
- Proposed fix: Add a Tauri command that runs `nvidia-smi --query-gpu=...`
  (Windows / Linux) and returns the parsed result. Expose via a custom
  React hook on a 2-second interval.

### Graceful Tauri-shell shutdown

- Location: `srtforge-studio/src-tauri/src/lib.rs`.
- Evidence: When `pnpm tauri dev` is killed (Ctrl-C), the spawned worker
  child keeps running. Windows refuses `fs::remove_file` on a running
  `.exe`, so the next `pnpm tauri dev` panics in `tauri-build` with
  `PermissionDenied`. Workaround documented in `CLAUDE.md`.
- Proposed fix: On Tauri's `RunEvent::ExitRequested`, send
  `{"action":"shutdown"}` to the worker stdin and wait briefly before
  killing. Prevents the recurring zombie-worker symptom.
- Acceptance: After `pnpm tauri dev` is closed, `Get-Process srtforge_worker`
  returns nothing.

## Test gaps

- ASR engines (Whisper, Parakeet) have engine-specific code paths but no
  contract tests on their fake outputs. Add fakes that produce small
  synthetic word-timestamp lists and assert the post-processor handles
  them sensibly.
- Settings persistence round-trip is not tested across all dataclasses.
- **Pipeline tests must pin `asr_engine` explicitly.** `PipelineConfig`
  defaults `asr_engine` to `settings.whisper.engine`, which is whatever
  the persistent `srtforge.config` has at module-import time. Tests that
  rely on the default are silently coupled to the host environment —
  passes locally with `engine: whisper`, fails in CI (no config file)
  with a confusing `megatron.core.num_microbatches_calculator.__spec__
  is None` error from the lazy NeMo import path. Always pass
  `asr_engine="whisper"` (or `"parakeet"`) explicitly when constructing
  `PipelineConfig` in a test.

## Doc gaps

- BBC subtitle style is referenced in `Settings.style` but the
  post-processor doesn't honor it yet. Either implement it or document the
  choice as Netflix-only and remove the option until ready.
- The `style: "custom"` setting is similarly not implemented.

## Code smells

- `srtforge/cli.py` `_build_pipeline_config` has grown long; consider
  splitting per-config-block parsing helpers (whisper / separation /
  ffmpeg / output).
- `srtforge-studio/src/lib/workerConfig.ts` is becoming the protocol
  schema by accident. Promote it to a typed builder that round-trips
  through the worker_requests JSON schema.

## Lint debt — ignored ruff F-class warnings

The following ignores are configured in `pyproject.toml`
`[tool.ruff.lint.per-file-ignores]`. Each is a real (minor) issue worth
fixing on a future pass, not a hard problem today.

- `srtforge/gui_app.py` — `F841` unused locals (`total`, `green_select_strong`).
  Stale variables from past refactors of the legacy PySide6 layout.
- `srtforge/win11_backdrop.py` — `F841` unused `dwm_set_window_attribute`.
  The Windows DWM probe is dead code now; remove the AttributeError
  guard if the shim is no longer needed.
- `srtforge/post/srt_utils.py` — `F841` unused `end` variable in a
  rebalancing loop. Either incorporate it into the time-borrow heuristic
  or delete it.
- `srtforge/ffmpeg.py` — `F821` `Separator` forward reference. The file
  uses `from __future__ import annotations` so runtime cost is zero, but
  the better fix is a `TYPE_CHECKING` import.

## Stale references

- `README.md` still describes Faster-Whisper as the headline ASR engine,
  but the default has been Parakeet for several releases. Audit on the
  next docs sweep.

---

When you fix something here, **delete the entry** in the same commit.
Don't leave a "fixed" marker — the diff is the record.
