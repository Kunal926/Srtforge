# Srtforge

Srtforge is an offline subtitle generator for local media files. It selects the
best English audio stream, isolates vocals with FV4 MelBand Roformer, runs ASR,
post-processes subtitle timing and line shape, and writes `.srt` files without
cloud calls by default.

The Python package in `srtforge/` is the source of truth. It is exposed through
the `srtforge` CLI, a persistent JSON-line worker used by the GUIs, the current
Tauri Studio app, and a legacy PySide6 desktop GUI.

![Active Job UI](srtforge/assets/images/Active%20Job.png)

## Current Pipeline

1. Probe audio streams with `ffprobe` and choose the English track.
2. Extract the selected track to 48 kHz stereo PCM with `ffmpeg`.
3. Isolate vocals with FV4 MelBand Roformer through `audio-separator`.
4. Filter and resample the vocal stem to 16 kHz mono.
5. Transcribe with NVIDIA Parakeet TDT by default, or Faster-Whisper when
   configured.
6. Re-segment, balance subtitle lines, apply readability limits, and snap timing.
7. Write `.srt`.
8. Optionally mux soft subtitles or burn subtitles into a new media file.

Gemini correction is optional and disabled unless enabled in configuration.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `srtforge/` | Python pipeline, CLI, settings, ASR engines, worker loop, and legacy GUI |
| `srtforge-studio/` | Current Tauri 2 + React + Zustand desktop app |
| `docs/contracts/` | Worker request/event contract and JSON schemas |
| `docs/architecture/` | Stable architecture overview |
| `docs/adr/` | Architecture decision records |
| `scripts/` | Local validation, environment checks, and Studio sidecar rebuild helpers |
| `packaging/windows/` | Legacy PySide6 GUI PyInstaller packaging |

## Install

### Windows 11

Install Python 3.10-3.12, Git for Windows, and an NVIDIA driver if you plan to
use GPU mode. CUDA Toolkit is not required for the supported GPU runtime.

```powershell
git clone https://github.com/StiensGate928/Srtforge.git
cd Srtforge
.\install.ps1
.\.venv\Scripts\Activate.ps1
srtforge --help
```

Useful installer switches:

```powershell
.\install.ps1 -Gpu              # force CUDA 12.8 GPU wheels
.\install.ps1 -Cpu              # force CPU wheels
.\install.ps1 -PythonVersion 3.12
.\install.ps1 -PythonPath C:\Python312\python.exe
```

The GPU path targets CUDA 12.8 wheels:

- `torch==2.11.0+cu128`
- `onnxruntime-gpu==1.25.1`
- `cuda-python==12.9.6`

Run the optional runtime check after installation:

```powershell
srtforge gpu-smoke
```

### Linux and WSL

```bash
git clone https://github.com/StiensGate928/Srtforge.git
cd Srtforge
./install.sh
source .venv/bin/activate
srtforge --help
```

Use `./install.sh --gpu` or `./install.sh --cpu` to override hardware detection.
Set `HF_TOKEN` before installing if you need authenticated Hugging Face access.

Both installers download FV4 assets into `models/`. Model files, generated
subtitles, logs, build output, and local configuration are intentionally ignored
by Git.

## CLI Usage

```bash
# Single media file, output path derived from the input name
srtforge run /path/to/video.mkv

# Explicit output path
srtforge run /path/to/video.mkv --output /path/to/video.srt

# Process a season folder
srtforge series "/shows/Series/Season 1" --glob "**/*.mkv"

# Sonarr custom-script entry point
srtforge sonarr-hook
```

The default config lives in package defaults and can be overridden with
`srtforge.config` in the repo or app data location. Common settings include ASR
engine/model, language, GPU preference, output directory, embed/burn behavior,
and optional Gemini correction.

## Desktop Apps

### Srtforge Studio

`srtforge-studio/` is the current desktop app. It uses Tauri 2, React, Zustand,
and a PyInstaller one-dir Python worker sidecar. Studio communicates with the
same `srtforge worker` JSON-line protocol used by the legacy GUI.

![Empty Queue UI](srtforge/assets/images/Empty%20Queue.png)
Development workflow:

```powershell
cd C:\Srtforge-lat\Srtforge
.\.venv\Scripts\Activate.ps1
.\scripts\rebuild_studio_sidecar.ps1
cd .\srtforge-studio
pnpm install
pnpm tauri dev
```

The sidecar rebuild helper stops stale workers, builds
`srtforge-studio/src-tauri/binaries/srtforge_worker/srtforge_worker.exe`, runs
`gpu-smoke`, and checks that `worker --no-preload` reaches `worker_ready`
quickly. Rebuild the sidecar after changes to `srtforge/`, the worker entry
shim, or the PyInstaller spec.

Production build:

```powershell
cd srtforge-studio
pnpm tauri build
```

### Legacy PySide6 GUI

The legacy GUI remains available as `srtforge-gui`:

```powershell
srtforge-gui
```

It wraps the same Python pipeline and worker protocol, but new desktop work
should target Srtforge Studio.

## Worker Protocol

Both GUIs spawn `srtforge worker` and exchange one JSON object per line over
stdin/stdout. The contract is documented in
`docs/contracts/worker-protocol.md`, with advisory schemas in:

- `docs/contracts/worker-requests.schema.json`
- `docs/contracts/worker-events.schema.json`

Protocol changes must keep the Python worker, Rust Tauri forwarder, TypeScript
types, Zustand reducer, docs, schemas, and tests aligned.

## Validation

The default validation path is lightweight. It must not require CUDA, model
downloads, real media, private secrets, or cloud APIs.

Windows:

```powershell
.\.venv\Scripts\Activate.ps1
.\scripts\check.ps1
```

Linux/WSL:

```bash
bash ./scripts/check.sh
```

Focused checks:

```powershell
python scripts/check_docs.py
python -m pytest --color=no -q
python -m ruff check srtforge tests scripts
cd srtforge-studio
pnpm exec tsc --noEmit
cd src-tauri
cargo check --quiet
```

Heavy tests and real-media benchmarks should be run explicitly with the matching
pytest markers and local assets.

## Sonarr Integration

Add Srtforge as a Sonarr custom script:

1. Install Srtforge in an environment Sonarr can access.
2. In Sonarr, open Settings -> Connect and add a Custom Script.
3. Set the path to `srtforge-sonarr` or a wrapper that runs
   `srtforge sonarr-hook`.
4. Leave arguments empty; the hook reads Sonarr environment variables.
5. Enable the import and upgrade events you want to process.

The hook resolves the imported episode path and runs the standard pipeline to
produce a sidecar `.srt`.

## Build Artifacts and Local Data

Do not commit generated or machine-local files:

- `.venv/`, `node_modules/`, `target/`, `dist/`, `build/`
- `models/` checkpoints, except tracked lightweight config files
- `output/`, `tmp/`, `logs/`, `*.log`
- `srtforge.config`, `.env`, secrets
- PyInstaller and Tauri sidecar binaries

The repository keeps only source, configuration, docs, tests, and lightweight
placeholders needed for clean builds.
