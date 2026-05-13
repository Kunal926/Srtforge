# Srtforge Architecture

This is the stable map of where things live and which contracts hold the system
together. Implementation details belong in code, tests, contract docs, or local
README sections.

## Bird's-Eye View

```text
Srtforge
  srtforge/                         Python pipeline, CLI, worker, legacy GUI
    cli.py                          Typer commands and JSON-line worker loop
    pipeline.py                     probe -> extract -> separate -> ASR -> SRT
    logging.py                      run logs plus stage/progress event emitter
    settings.py                     typed settings loaded at boundaries
    worker_protocol.py              typed request/event helpers

  srtforge-studio/                  Current Tauri 2 desktop app
    src/                            React + Zustand UI
    src-tauri/src/lib.rs            Rust worker process manager and commands
    packaging/windows/              PyInstaller sidecar spec

  docs/contracts/                   Worker protocol docs and schemas
  docs/adr/                         Architecture decision records
  scripts/                          Local checks and packaging helpers
  tests/                            Lightweight pytest suite by default
```

Both desktop apps use the same worker:

```text
GUI -> stdin JSON request -> srtforge worker -> stdout JSON event -> GUI
```

The Python pipeline is the source of truth. GUI code should configure, launch,
display, and manage the worker rather than reimplementing transcription logic.

## Python Pipeline

- `srtforge/cli.py` defines `run`, `series`, `worker`, `sonarr-hook`, and
  utility commands such as `gpu-smoke`.
- `srtforge/pipeline.py` owns the ordered media pipeline and returns typed
  `PipelineResult` objects.
- `srtforge/ffmpeg.py` owns probing, extraction, separation, preprocessing,
  normalization, and FFmpeg command construction.
- `srtforge/engine_parakeet.py` and `srtforge/engine_whisper.py` provide ASR
  engines selected by settings.
- `srtforge/gpu_runtime.py` owns CUDA/ONNX Runtime preload, smoke checks, and
  cache cleanup helpers.
- `srtforge/mux.py` owns subtitle mux/burn helpers.
- `srtforge/worker_protocol.py` keeps worker request and event helpers typed.

The pipeline emits stage/progress/log events through `srtforge.logging` without
depending on any GUI.

## Studio

`srtforge-studio/` is the current desktop UI:

- `src/types.ts` defines the TypeScript worker event/request-facing types.
- `src/store.ts` consumes worker events into Zustand state.
- `src/lib/workerConfig.ts` builds per-job config payloads.
- `src/lib/tauri.ts` wraps Tauri command invocation and event listeners.
- `src/components/` contains Queue, Active, History, Watch, Settings, Normalize,
  and BGM surfaces.
- `src-tauri/src/lib.rs` owns the worker child process, forwards events, handles
  filesystem/dialog helpers, manages debug logs, and exposes GPU telemetry.

Studio bundles a PyInstaller one-dir sidecar under
`src-tauri/binaries/srtforge_worker/`. Generated sidecar binaries are ignored;
`.gitkeep` is tracked only to preserve the resource directory.

## Legacy GUI

`srtforge/gui_app.py` is the PySide6 GUI. It remains available as
`srtforge-gui`, but new desktop work should target Studio.

## Worker Protocol

The worker protocol is the main cross-language contract:

- Requests and events are newline-delimited JSON.
- Stdout from `srtforge worker` is JSON-only.
- The canonical prose contract is `docs/contracts/worker-protocol.md`.
- Advisory schemas live in `docs/contracts/worker-requests.schema.json` and
  `docs/contracts/worker-events.schema.json`.
- Contract tests live in `tests/test_cli_worker.py` and
  `tests/test_worker_protocol.py`.

Any protocol change must update Python emitters/helpers, Rust forwarding,
TypeScript types, Zustand handling, docs, schemas, and tests together.

## Test Boundaries

The default test selection must run without:

- CUDA or a real GPU,
- model downloads,
- real media files,
- private secrets,
- cloud API calls,
- heavyweight FFmpeg processing.

Heavy tests must be marked with pytest markers such as `requires_cuda`,
`requires_model`, `requires_media`, `requires_ffmpeg`, or `slow`.

## GPU and Packaging Boundaries

- The supported packaged GPU stack targets CUDA 12.8 wheels and a compatible
  NVIDIA driver, not a system CUDA Toolkit.
- `srtforge gpu-smoke` validates the runtime stack.
- `scripts/rebuild_studio_sidecar.ps1` rebuilds the Studio sidecar, runs
  `gpu-smoke`, and verifies fast `worker --no-preload` startup.
- Studio should keep active Max CUDA UI rendering and telemetry low-cost so FV4
  separation and Parakeet ASR retain GPU headroom.

## Invariants

1. The Python pipeline remains the source of truth.
2. Worker stdout stays JSON-only.
3. Successful transcribe jobs emit `job_started`, then stage/progress/log events,
   then `srt_written`, optional media outputs, and `job_completed`.
4. Failed jobs emit `job_started`, optional stage/progress/log events, and
   `job_failed`.
5. Filesystem paths that depend on the project root use frozen-aware helpers in
   `srtforge.config`.
6. Secrets, model files, media files, logs, runtime outputs, and generated
   binaries do not enter Git.
