# Srtforge Studio

A Tauri 2.0 + React shell that wraps the existing
[Srtforge](https://github.com/StiensGate928/Srtforge) Python pipeline as a
single Windows native app.

The pipeline (FFmpeg → FV4 → Parakeet → SRT) ships **unchanged** as a
PyInstaller-built sidecar binary. The React UI talks to it over the same
JSON-on-stdin/stdout protocol the legacy PySide6 GUI already uses.

```
┌─────────────────────────────────────────────────────────┐
│  Srtforge Studio.exe  (Tauri shell)                     │
│  React UI  ──invoke()──→  Rust commands                 │
│            ←─emit()─────  worker:event stream           │
└──────────────────────────│──────────────────────────────┘
                           │ stdin/stdout JSON
                           ▼
              ┌──────────────────────────────┐
              │ srtforge_worker.exe (sidecar)│
              │  • python -m srtforge worker │
              │  • unchanged pipeline        │
              └──────────────────────────────┘
```

## Repository layout

```
srtforge-studio/
├── package.json              # Vite + React + Tauri CLI
├── vite.config.ts
├── tsconfig.json
├── index.html
├── src/                      # React UI
│   ├── App.tsx               # top-level composer (wires Tauri ↔ store ↔ views)
│   ├── store.ts              # Zustand store + worker-event dispatch
│   ├── icons.tsx
│   ├── lib/
│   │   ├── tauri.ts          # invoke / listen / dialog wrappers
│   │   └── stages.ts
│   ├── styles/index.css      # design tokens (light/dark/forge themes)
│   └── components/
│       ├── TitleBar.tsx
│       ├── Sidebar.tsx
│       ├── StatusBar.tsx
│       ├── Queue.tsx         # DropZone, QueueEmpty, QueueTable, QueueCards, Sparkline
│       ├── ActiveDetail.tsx  # WaveformBig, StageList, LogsPanel
│       ├── EmptyState.tsx
│       ├── SettingsDrawer.tsx
│       └── BrandMark.tsx
├── src-tauri/
│   ├── Cargo.toml
│   ├── tauri.conf.json       # bundle config + sidecar registration
│   ├── capabilities/default.json
│   ├── icons/                # (you must add icon.png + icon.ico)
│   └── src/
│       ├── lib.rs            # worker process management + commands
│       └── main.rs
└── packaging/
    └── windows/
        └── srtforge_worker.spec  # PyInstaller spec for the sidecar
```

## Prerequisites (Windows)

- **Node.js 20+** and **pnpm** — `winget install OpenJS.NodeJS.LTS && npm i -g pnpm`
- **Rust** (stable) — https://rustup.rs/
- **Microsoft C++ Build Tools** — Visual Studio Build Tools 2022 with the
  "Desktop development with C++" workload
- **WebView2 Runtime** — already installed on Windows 11; on Win10 install
  the [Evergreen runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)
- **Python 3.12** with the existing Srtforge venv (used to build the sidecar)
- **PyInstaller** — `pip install pyinstaller`

## One-time setup

```powershell
# In this repo:
pnpm install

# In your Srtforge checkout (sibling to this folder):
cd ..\Srtforge
.\.venv\Scripts\activate
pip install pyinstaller
```

## Dev workflow

You need a worker binary to talk to. Two options:

**Option A — point at the system Python during dev** (fastest iteration):

Edit `src-tauri/src/lib.rs` and swap the `sidecar("srtforge_worker")` call
for a direct `Command::new("python").args(["-m", "srtforge", "worker"])`
behind a `cfg!(debug_assertions)` guard. Reload Rust.

**Option B — build the sidecar once, then iterate on the UI:**

```powershell
cd C:\Srtforge-lat\Srtforge
.\.venv\Scripts\Activate.ps1
.\scripts\rebuild_studio_sidecar.ps1
cd .\srtforge-studio
pnpm tauri dev
```

The helper stops stale `srtforge_worker.exe` processes, builds the PyInstaller
sidecar, copies it to Tauri's target-triple-suffixed filename, and runs the
suffixed sidecar `gpu-smoke` check. Re-run it after any Python pipeline, entry
shim, or PyInstaller spec change before comparing Studio performance.

The Vite dev server runs on `http://localhost:1420`; Tauri will open the
desktop window pointing at it. Edits to `src/**/*.tsx` hot-reload; edits to
`src-tauri/src/**/*.rs` trigger a Rust rebuild.

## Production build

```powershell
pnpm tauri build
```

Outputs:

- MSI installer: `src-tauri\target\release\bundle\msi\Srtforge Studio_0.1.0_x64_en-US.msi`
- NSIS installer: `src-tauri\target\release\bundle\nsis\Srtforge Studio_0.1.0_x64-setup.exe`

## Bundling the worker

`packaging/windows/srtforge_worker.spec` builds a **headless** Srtforge
worker — no PySide6, no Qt, no GUI deps. Output lands in
`src-tauri/binaries/` so Tauri's `externalBin` config picks it up at bundle
time.

The spec deliberately does **not** include the ~600 MB FV4 + Parakeet model
weights. The worker reads them from `%APPDATA%\Srtforge\models\` at runtime;
the React UI's first-run experience streams them from your GitHub Releases
asset URLs. This keeps the installer ~100 MB instead of ~800 MB.

If you have ffmpeg/ffprobe binaries you want bundled, set the env var
before invoking pyinstaller:

```powershell
$env:SRTFORGE_FFMPEG_DIR = "C:\path\to\ffmpeg\bin"
pyinstaller packaging\windows\srtforge_worker.spec --distpath ..\srtforge-studio\src-tauri\binaries
```

The supported GPU sidecar stack is CUDA 12.8. Build from an environment
installed through `install.ps1 -Gpu` or with `constraints-gpu-cu128.txt`, then
run the sidecar smoke check before shipping:

```powershell
srtforge gpu-smoke
```

The smoke check must report PyTorch CUDA, ONNX Runtime `CUDAExecutionProvider`,
`cuda.__version__`, and NeMo CUDA graph conditional-node support. End-user
machines need a compatible NVIDIA driver, not the CUDA Toolkit.

## Worker JSON protocol

This is what `srtforge/cli.py worker` actually speaks today. Both sides
emit one JSON object per line over stdin/stdout.

**UI → worker (stdin):**

| action       | payload                                                       |
|--------------|---------------------------------------------------------------|
| `transcribe` | `{ id, file, output?, config: <Settings> }`                   |
| `shutdown`   | —                                                             |

> Pause / resume / cancel are **not implemented in the Python worker yet**
> — adding them is a follow-up on the `srtforge/cli.py` side. The Rust
> shell only forwards what the worker accepts.

**Worker → UI (stdout):**

| event                    | payload                                  |
|--------------------------|------------------------------------------|
| `worker_starting`        | `{ pid, preload, cpu }`                  |
| `worker_ready`           | `{ pid }`                                |
| `worker_preload_skipped` | `{ reason }`                             |
| `worker_preload_failed`  | `{ error }`                              |
| `worker_stopping`        | —                                        |
| `job_started`            | `{ id, file }`                           |
| `srt_written`            | `{ id, path }`                           |
| `job_completed`          | `{ id, seconds }`                        |
| `job_failed`             | `{ id, error, traceback? }`              |
| `bad_json` / `bad_payload` / `unknown_action` | `{ … }` (UI shows toast) |

**No granular progress today.** The worker doesn't emit per-stage or per-
percent updates, so the UI's progress bar stays at 0 while a job runs and
flips to 100 when `srt_written` lands. Adding `progress` / `stage` / `log`
events would need:

- emit calls inside `srtforge/pipeline.py` between probe → extract → FV4
  → preprocess → ASR → polish → write
- a small `_emit_worker_event` per stage transition, plus throttled
  progress updates inside ASR
- the React store already has the matching event handlers wired (see the
  `progress` and `stage` cases that are currently unreachable)

## What's in / out of the MVP

In:
- Queue tab (Hybrid + Cards layouts)
- Active job tab (waveform, stages, live logs)
- Settings drawer (Basic / Performance / Advanced)
- Title bar with native Win11 close/min/max
- Status bar
- Drag-drop and file picker

Stubbed (`Coming soon` empty state) — to be filled in iteratively:
- History tab (full stat tiles + list)
- Watch folders tab (Sonarr/Radarr libraries)
- Normalize tab (FFmpeg-only WAV pipeline)
- BGM separation tab (FV4 stems)

The full design for those views lives in
[`../project/Srtforge Studio.html`](../project/Srtforge%20Studio.html) — port
them by lifting the corresponding components from
`../project/srtforge_studio/{watch,history,tools}.jsx`.

## Verification on Windows 11

After `pnpm tauri build` and installing the MSI:

1. Launch from Start menu → custom titlebar window opens
2. (First run only) Models prompt → download to `%APPDATA%\Srtforge\models\`
3. Drop a 30 s `.mkv` into the queue → row appears, status flips to Processing
4. Active job tab shows progress, current stage, log stream
5. SRT lands at `<output_dir>\<basename>.srt`
6. Settings → change output dir → next run respects it
7. Task Manager: `srtforge_worker.exe` is the only Python sidecar process
8. Close the app: sidecar exits cleanly (no zombie processes)

If steps 1–6 pass, the architecture is proven. Ship.
