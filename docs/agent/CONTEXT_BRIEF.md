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
  `set_event_emitter` hook used to publish `stage` events out of the
  pipeline.
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
- Pipeline stage events: wired (`stage="..."` keywords on
  `RunLogger.step`) and emitted via `srtforge.logging.set_event_emitter`,
  installed by the worker per-job.
- `WorkerStage` and `WorkerEvent` types: exist in
  `srtforge-studio/src/types.ts`.
- Worker protocol contract doc: see `docs/contracts/worker-protocol.md`.
- Worker test coverage: lifecycle (success and failure ordering) plus zero
  chunking factor preservation. Bad-JSON / unknown-action / GPU-cache paths
  are covered by the contract test suite added in this branch.
- Lightweight check: `pwsh ./scripts/check.ps1` (Windows) or
  `bash ./scripts/check.sh` (Unix).
- Codex layer check: `python scripts/check_docs.py` now validates `CODEX.md`,
  `.agents/skills/`, and `.codex/`.

## Open gaps and follow-ups

- The pipeline does not yet emit fine-grained `progress` events
  (only `stage start/end`). Surface this if/when the UI needs sub-stage
  progress bars; tracked in `QUALITY.md`.
- The Rust shell does not yet forward worker shutdown gracefully when
  `pnpm tauri dev` is killed; this leaves zombie `srtforge_worker.exe`
  processes on Windows. Workaround documented in `CLAUDE.md`. Tracked in
  `QUALITY.md`.
- Real Sonarr webhook listener, real filesystem watcher polling, and real
  GPU/VRAM telemetry probes for Studio are placeholder UIs only.

## Top risks

- **Windows file locking on the sidecar `.exe`.** Zombie worker processes
  break `pnpm tauri dev` rebuilds. Diagnose by running
  `Get-Process srtforge_worker`.
- **PyInstaller fragility.** Always activate the project venv before
  running `pyinstaller`, and use `--clean` on spec changes.
- **CUDA pin.** `cuda-python>=12.3,<13` is enforced at runtime; relaxing
  this without verifying NeMo + ONNX Runtime CUDA EP compat will break the
  Parakeet GPU path.

## Canonical lightweight check

```powershell
pwsh ./scripts/check.ps1
```

It runs Python import smoke, fast pytest selection (no slow / model / CUDA
/ media / FFmpeg), Ruff (if available), CLI smoke, frontend build (if
`node_modules/` exists), Rust `cargo check` (if cargo exists), and the doc
freshness check. Skipped checks print their reason.
