# Srtforge Studio

Srtforge Studio is the current Windows desktop app for Srtforge. It is a
Tauri 2 shell with a React and Zustand UI around the Python `srtforge worker`
process.
The Python pipeline remains the source of truth; Studio is a native desktop
wrapper that manages queueing, settings, progress display, logs, and local file
actions.

## Architecture

```text
Srtforge Studio.exe
  React UI
    invoke/listen
  Tauri Rust shell
    owns worker process, logs, dialogs, filesystem helpers, GPU telemetry
  PyInstaller one-dir sidecar
    srtforge_worker.exe worker --no-preload
  Python pipeline
    ffprobe -> ffmpeg -> FV4 -> Parakeet/Faster-Whisper -> SRT -> mux/burn
```

The UI and worker communicate over the JSON-line protocol documented in
`../docs/contracts/worker-protocol.md`.

## Prerequisites

- Node.js 20+
- pnpm
- Rust stable
- Microsoft C++ Build Tools 2022 with the Desktop development workload
- WebView2 Runtime
- Python 3.10-3.12 with the root Srtforge virtual environment
- PyInstaller in that virtual environment

## Development

Build the worker sidecar before running Tauri:

```powershell
cd C:\Srtforge-lat\Srtforge
.\.venv\Scripts\Activate.ps1
.\scripts\rebuild_studio_sidecar.ps1
cd .\srtforge-studio
pnpm install
pnpm tauri dev
```

The rebuild helper:

- stops stale `srtforge_worker.exe` processes,
- removes old sidecar output under `src-tauri/binaries/`,
- builds a PyInstaller one-dir sidecar at
  `src-tauri/binaries/srtforge_worker/srtforge_worker.exe`,
- runs `gpu-smoke`,
- verifies `worker --no-preload` reaches `worker_ready` within the startup
  threshold,
- preserves `.gitkeep` so the resource directory exists in clean checkouts.

Run it again after changes to:

- `srtforge/`
- `srtforge-studio/packaging/windows/srtforge_worker.spec`
- the worker entry point or packaging hooks
- GPU runtime dependency pins

Pure React/CSS changes do not require a sidecar rebuild.

## Useful Commands

```powershell
pnpm exec tsc --noEmit
pnpm tauri dev
pnpm tauri build
```

Rust shell checks:

```powershell
cd src-tauri
cargo fmt --check
cargo test
cargo check --quiet
```

Root repo checks:

```powershell
cd C:\Srtforge-lat\Srtforge
python scripts/check_docs.py
python -m pytest --color=no -q
python -m ruff check srtforge tests scripts
```

## Sidecar Layout

Studio bundles the worker as a one-dir PyInstaller resource:

```text
src-tauri/binaries/
  srtforge_worker/
    .gitkeep
    srtforge_worker.exe       # generated, ignored
    _internal/                # generated, ignored
```

Generated binaries and `_internal/` contents are not committed. The `.gitkeep`
placeholder is committed so Tauri resource paths exist before the first local
build.

The supported GPU sidecar stack targets CUDA 12.8. End-user machines need a
compatible NVIDIA driver; they do not need the CUDA Toolkit.

## Production Build

```powershell
pnpm tauri build
```

Tauri outputs installers under `src-tauri/target/release/bundle/`. Build output
is ignored by Git.

## Current UI Surface

Studio includes:

- queue management with drag/drop and file picker,
- active-job progress, stage timeline, ETA, and live logs,
- history with output/log actions,
- watch-folder management surface,
- normalize and BGM separation tools,
- settings for ASR, GPU/device behavior, output paths, and embed/burn options,
- low-cost active-job rendering to avoid competing with CUDA work.

During active Max CUDA jobs, Studio keeps foreground rendering and telemetry
quiet so FV4 separation and Parakeet ASR keep GPU headroom.
