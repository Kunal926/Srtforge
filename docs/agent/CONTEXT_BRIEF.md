# CONTEXT_BRIEF.md — Srtforge in one page

A fresh agent should be able to read this and `HANDOFF.md` and start working.

## What Srtforge is

An offline tool that turns a video file into an `.srt` subtitle file using
local models. The pipeline runs without cloud calls by default; an optional
Gemini correction step is gated behind a config flag.

The pipeline does, in order:

1. Probe audio streams with `ffprobe`, pick the English track.
2. Extract that stream to PCM 48 kHz stereo `pcm_f32le` with `ffmpeg`.
3. Run **FV4 MelBand Roformer** vocal separation
   (`audio-separator` + `voc_fv4.ckpt` / `voc_gabox.yaml` from `models/`).
4. Apply preprocessing filters (HPF / LPF + SoXr resample to 16 kHz mono).
5. Run ASR — **NeMo Parakeet TDT** (default) or **Faster-Whisper**.
6. Post-process (re-segment, balance two-line shape, CPS / readability,
   frame snap).
7. Write SRT.
8. Optional embed (mkvmerge or ffmpeg) and/or burn re-encode.

## Three repo heads

| Head | What it is | Where |
| --- | --- | --- |
| Python pipeline | Source of truth, including CLI and worker JSON loop | `srtforge/` |
| PySide6 GUI (legacy) | Desktop wrapper that shells the CLI | `srtforge/gui_app.py` + `packaging/windows/srtforge_gui.spec` |
| Tauri Studio (current) | Tauri 2 + React + Zustand wrapper that owns a PyInstaller-bundled worker sidecar | `srtforge-studio/` |

Both GUIs use the **same** stdin/stdout JSON-line worker protocol against
`srtforge worker`.

## Critical files

- `srtforge/cli.py` — Typer CLI, including the `worker` JSON loop.
- `srtforge/pipeline.py` — `Pipeline.run()` is the ordered chain.
- `srtforge/logging.py` — `RunLogger`, `_TimedStep`, and the
  `set_event_emitter` hook used to publish `stage` and `progress` events
  out of the pipeline.
- `srtforge/settings.py` — config dataclass tree, YAML loading,
  persistent-config path resolution.
- `srtforge/config.py` — `PROJECT_ROOT` / `MODELS_DIR` / `FV4_*` resolution
  (frozen-aware).
- `srtforge/mux.py` — embed/burn helpers (mkvmerge + ffmpeg).
- `srtforge-studio/src/types.ts` — `WorkerEvent` discriminated union, the
  TypeScript shape of the worker protocol.
- `srtforge-studio/src/store.ts` — Zustand reducer that consumes worker events.
- `srtforge-studio/src-tauri/src/lib.rs` — Rust shell that owns the worker
  child process and forwards JSON events to the React app.
- `tests/test_cli_worker.py` — CliRunner-based worker protocol tests.
- `tests/test_pipeline.py` — pipeline tests using fakes and monkeypatching.

## Agent operating layer

- `AGENTS.md` is the shared entry point for any coding agent.
- Claude Code uses `CLAUDE.md` and `.claude/skills/`.
- OpenAI Codex Windows app / CLI / IDE uses `CODEX.md` and
  `.agents/skills/`.
- Codex app local actions live in `.codex/`.
- Durable context lives in `docs/agent/*`, protocol truth in
  `docs/contracts/*`, architecture truth in `docs/architecture/*`, and
  durable decisions in `docs/adr/*`.
- Chat-only decisions are not durable. Convert them into docs, tests,
  schemas, ADRs, task notes, ExecPlans, or `QUALITY.md`.

## Current state of the harness

- ExecPlan / handoff / context-brief conventions: this commit introduces them.
- Pipeline stage/progress events: wired through
  `srtforge.logging.set_event_emitter`, installed by the worker per-job.
  Stage events come from `RunLogger.step(stage="...")`; progress events come
  from ASR generation, post-processing, and final SRT write.
- Studio History now distinguishes the Python pipeline performance log
  (`performance_log_path`, backed by `logs/<run_id>.log`) from the Tauri
  captured live debug/Typer log (`debug_log_path`, backed by
  `logs/studio-debug/*.debug.log`).
- `WorkerStage` and `WorkerEvent` types: exist in
  `srtforge-studio/src/types.ts`.
- Worker protocol contract doc: see `docs/contracts/worker-protocol.md`.
- Worker test coverage: lifecycle (success and failure ordering) plus zero
  chunking factor preservation. Bad-JSON / unknown-action / GPU-cache paths
  are covered by the contract test suite added in this branch.
- Lightweight check: `.\.codex\actions\harness-check.ps1` (Windows, with
  `pwsh` -> Windows PowerShell fallback) or `bash ./scripts/check.sh` (Unix).
- Codex layer check: `python scripts/check_docs.py` now validates `CODEX.md`,
  `.agents/skills/`, and `.codex/`.
- Studio sidecar rebuilds: after any `srtforge/`, entry-shim, or PyInstaller
  spec change, run `.\scripts\rebuild_studio_sidecar.ps1` before benchmarking
  Tauri. The helper stops stale workers, rebuilds/copies the suffixed sidecar,
  and runs the suffixed `gpu-smoke`.
- Studio GPU throughput: focused Studio now matches the old PySide/CLI
  baseline when the rebuilt sidecar is used, the window is opaque, WebView2 GPU
  acceleration is disabled, and active-job UI rendering is quiet. Treat the
  likely regression cause as a combination of stale sidecar plus foreground
  WebView2/DWM compositing and high-frequency React/log/waveform repaint
  pressure competing with CUDA. During GPU jobs, reintroduce design polish only
  as static CSS/DOM, CPU-light state, or idle-only effects.

## Open gaps and follow-ups

- The Rust shell does not yet forward worker shutdown gracefully when
  `pnpm tauri dev` is killed; this leaves zombie `srtforge_worker.exe`
  processes on Windows. Workaround documented in `CLAUDE.md`. Tracked in
  `QUALITY.md`.
- Real Sonarr webhook listener, real filesystem watcher polling, and real
  GPU/VRAM telemetry probes for Studio are placeholder UIs only.

## Top risks

- **Windows file locking on the sidecar `.exe`.** Zombie worker processes
  break `pnpm tauri dev` rebuilds. Diagnose by running
  `Get-Process srtforge_worker`; the rebuild helper stops stale workers before
  copying the sidecar.
- **Studio foreground rendering can steal GPU headroom.** Keep active-job
  Studio surfaces low cost: no WebView GPU acceleration, no transparent window,
  no animated waveform/playhead/pulse loops, and no synthetic high-frequency
  log events during FV4/ASR.
- **PyInstaller fragility.** Always activate the project venv before
  running `pyinstaller`, and use `--clean` on spec changes.
- **CUDA runtime pin.** The supported packaged GPU stack targets CUDA 12.8
  (`torch==2.11.0+cu128`, `onnxruntime-gpu==1.25.1`, `cuda-python==12.9.6`).
  CUDA 13 remains experimental until `srtforge gpu-smoke` and real media
  benchmarks prove NeMo + ONNX Runtime compatibility without shims.

## Canonical lightweight check

```powershell
.\.codex\actions\harness-check.ps1
```

It runs Python import smoke, fast pytest selection (no slow / model / CUDA
/ media / FFmpeg), Ruff (if available), CLI smoke, frontend build (if
`node_modules/` exists), Rust `cargo check` (if cargo exists), and the doc
freshness check. Skipped checks print their reason.
