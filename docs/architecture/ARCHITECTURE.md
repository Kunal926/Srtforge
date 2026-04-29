# ARCHITECTURE.md — Srtforge

Stable bird's-eye view. Designed to answer "where does X live?" and
"what is the contract between component A and component B?" without
spilling into implementation detail. Implementation detail goes in
`docs/agent/PROJECT_MAP.md` (generated) and inline file comments.

## Bird's-eye

```
┌────────────────────────────────────────────────────────────────────┐
│                         SRTFORGE                                   │
│                                                                    │
│   ┌────────────────────┐         ┌────────────────────────────┐    │
│   │ srtforge-studio/   │         │ srtforge/gui_app.py        │    │
│   │ Tauri 2 + React +  │         │ PySide6 desktop GUI        │    │
│   │ Zustand            │         │ (legacy, still shipped)    │    │
│   └─────────┬──────────┘         └────────────┬───────────────┘    │
│             │                                 │                    │
│             │   one JSON-line stdin/stdout protocol                │
│             │                                 │                    │
│             ▼                                 ▼                    │
│   ┌────────────────────────────────────────────────────────────┐   │
│   │ srtforge worker  (srtforge/cli.py)                         │   │
│   │ persistent loop, transcribe / normalize / separate /       │   │
│   │ shutdown / clear_gpu_cache                                 │   │
│   └────────────────────────────┬───────────────────────────────┘   │
│                                │                                   │
│                                ▼                                   │
│   ┌────────────────────────────────────────────────────────────┐   │
│   │ srtforge.pipeline.Pipeline.run()                           │   │
│   │ probe → extract → separate → preprocess → ASR → post →     │   │
│   │ write → optional embed → optional burn                     │   │
│   └────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
```

## Code map

### `srtforge/` — Python pipeline (source of truth)

- `cli.py` — Typer CLI: `run`, `series`, `worker`, `sonarr-hook`. The
  `worker` command is the JSON loop both GUIs depend on.
- `pipeline.py` — `Pipeline.run()` is the ordered processing chain. New
  stages MUST be added with a `stage="..."` keyword on
  `RunLogger.step` so they emit `stage` events.
- `logging.py` — `RunLogger`, `_TimedStep`, and `set_event_emitter`. The
  worker installs a per-job emitter so pipeline events reach the GUI
  without the pipeline knowing the GUI exists.
- `settings.py` — Dataclass tree (`AppSettings`, `WhisperSettings`,
  `SeparationSettings`, …) loaded from YAML at boundaries.
- `config.py` — `PROJECT_ROOT`, `MODELS_DIR`, `FV4_*` resolution,
  frozen-aware.
- `ffmpeg.py` — `FFmpegTooling` with `probe_audio_streams`,
  `extract_audio_stream`, `isolate_vocals`, `preprocess_audio`,
  `normalize_audio`.
- `engine_whisper.py`, `engine_parakeet.py` — ASR engines, picked by
  `whisper.engine` config (default: `parakeet`).
- `post/` — Subtitle post-processing (segmenter, srt_utils).
- `mux.py` — Embed (mkvmerge / ffmpeg) and burn helpers.
- `sonarr_hook.py` — Sonarr custom-script entry point.
- `worker_protocol.py` (new) — Typed parse helpers and event builders for
  the worker JSON contract.
- `gui_app.py` — Legacy PySide6 GUI.

### `srtforge-studio/` — Tauri 2 + React + Zustand GUI

- `src/types.ts` — `WorkerEvent` discriminated union, `WorkerStage`,
  `Settings`, `QueueFile`, `WatchLibrary`. The TypeScript shape of the
  worker contract.
- `src/store.ts` — Zustand reducer that consumes `WorkerEvent`s. Versioned
  `persist` middleware.
- `src/lib/tauri.ts` — Thin invoke wrappers around Tauri commands.
- `src/lib/workerConfig.ts` — Builds the `config` object sent in
  `transcribe` / `normalize` / `separate` requests.
- `src/components/` — UI panels (Queue, Active, History, Watch, Normalize,
  BGM, Settings drawer).
- `src-tauri/src/lib.rs` — Rust shell. Owns the worker subprocess, defines
  Tauri commands (`enqueue`, `normalize`, `separate`, `shutdown_worker`,
  `restart_worker`, `clear_gpu_cache`, `open_path`, `reveal_in_folder`,
  `probe_file`, `get_logs_dir`).
- `packaging/windows/srtforge_worker.spec` — PyInstaller spec for the
  bundled worker exe.

### `tests/` — Pytest (lightweight by default)

- `test_cli_worker.py` — Worker protocol behavior using Typer's
  `CliRunner`.
- `test_pipeline.py` — Pipeline behavior using fakes.
- `test_settings.py` — Settings loading / coercion / migrations.
- `test_ffmpeg.py` — FFmpeg helper behavior using fake binaries.
- `test_engine_parakeet.py`, `test_nemo_compat.py` — ASR engine plumbing.
- `test_sonarr_hook.py` — Sonarr environment parsing.
- `test_worker_protocol.py` (new) — Typed contract tests.

### `scripts/` — Operating harness

- `check.ps1` / `check.sh` — Lightweight validation entry point.
- `doctor.py` — Read-only environment report.
- `update_context.py` — Regenerates `docs/agent/PROJECT_MAP.md`.
- `check_docs.py` — Doc freshness / required-sections check.

### `docs/`

- `docs/agent/` — Agent-facing operating docs (this README, workflow,
  plans, handoff, quality, brief, external reading, project map, tasks,
  exec-plans).
- `docs/architecture/` — Stable architecture (this file).
- `docs/contracts/` — Worker protocol contract + JSON schemas.
- `docs/adr/` — Architecture Decision Records.

### `packaging/`

- `windows/srtforge_gui.spec` — Legacy PySide6 GUI exe spec.
- `windows/ffmpeg/` — Optional vendored FFmpeg (gitignored binaries).

### `.github/workflows/`

- `harness.yml` — Lightweight CI gate (no CUDA / models / media).

## Architecture boundaries

### The Python pipeline is the source of truth

- All transcription logic lives in `srtforge/`. GUIs are thin wrappers.
- Settings are parsed from YAML into dataclasses **at boundaries**
  (`srtforge.settings.load_settings`). Inside the pipeline, settings are
  typed.
- Pipeline outputs are typed (`PipelineResult`) and the CLI / worker
  surface them as JSON events.

### The worker protocol is the bridge

- One JSON object per stdin line (request), one JSON object per stdout
  line (event). UTF-8.
- See `docs/contracts/worker-protocol.md` for the canonical event /
  request vocabulary.
- **Any change to the protocol must update Python emitter, Rust
  forwarder, TypeScript consumer, the contract doc, the JSON schema, and
  the test in lockstep.** The `protocol-change` skill walks this.

### Heavy ML / FFmpeg paths are integration-only in normal tests

- The default test selection excludes `slow`, `requires_ffmpeg`,
  `requires_model`, `requires_cuda`, `requires_media`.
- Integration / golden tests that need real assets must be marked.
- CI runs only the lightweight default.

## Cross-cutting concerns

| Concern | Where |
| --- | --- |
| Settings | `srtforge/settings.py` (dataclasses) + `srtforge/config.yaml` (defaults) + `srtforge.config` (persistent overrides) |
| Logging | `srtforge/logging.py` — `RunLogger`, file logs in `logs/`, latest run at `logs/srtforge.log` |
| Worker events | `srtforge/cli.py` `_emit_worker_event` + `srtforge/logging.py` `_emit_stage` |
| Model paths | `srtforge/config.py` (`MODELS_DIR`, `FV4_*`); frozen-aware |
| Temp / output paths | `PipelineConfig.temp_dir`, `PipelineConfig.output_directory`; CLI resolves under `PROJECT_ROOT` |
| Windows packaging | `packaging/windows/*.spec` (legacy GUI), `srtforge-studio/packaging/windows/*.spec` (Studio sidecar) |

## Invariants (do not break without an ADR)

1. The default test selection runs **without** CUDA, real models, real
   media, private secrets, or cloud calls.
2. The worker emits JSON-only on stdout. Anything that would have written
   non-JSON to stdout (e.g. tracebacks) is captured into a `job_failed`
   event with a `traceback` field.
3. A successful transcribe job emits the events in this order:
   `job_started → (stage|progress|log)* → srt_written → (media_written)*
   → job_completed`.
4. A failed transcribe job emits:
   `job_started → (stage|progress|log)* → job_failed`.
5. `PROJECT_ROOT` resolution is **frozen-aware**. New code that needs
   filesystem paths must use `srtforge.config.PROJECT_ROOT` rather than
   `Path.cwd()` or `__file__` walks.
6. Worker protocol changes never silently drop fields. New optional
   fields land in the schema with `"description"` text and a default;
   removed fields are deprecated for at least one release.
7. Secrets do not enter repo files, log files, screenshots, or CI
   secrets unless explicitly ratified by an ADR.
