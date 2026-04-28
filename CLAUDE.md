# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo layout — two heads, one pipeline

This repo ships **one Python subtitle pipeline** consumed by **two GUIs**:

- `srtforge/` — the pipeline package + Typer CLI (`srtforge run`, `srtforge series`, `srtforge worker`, `srtforge sonarr-hook`). This is the source of truth for all transcription logic.
- `srtforge/gui_app.py` — legacy **PySide6** desktop wrapper, distributed as `srtforge-gui`. It shells out to a child `srtforge` CLI process via `subprocess.Popen`.
- `srtforge-studio/` — current **Tauri 2.0 + React + Zustand** wrapper. Tauri spawns a PyInstaller-bundled worker sidecar (`srtforge-studio/src-tauri/binaries/srtforge_worker-x86_64-pc-windows-msvc.exe`) and talks to it over the same JSON-per-line stdin/stdout protocol as the legacy GUI.

When porting new UI features, port from the original prototype at `project/srtforge_studio/{watch,history,tools}.jsx` — that's the design source of truth, not the legacy PySide6 layout.

## Setup & dev commands

Primary platform is Windows 11 / PowerShell. The repo also has a Linux/WSL path.

```powershell
# One-time install (Windows; auto-detects GPU and downloads FV4 model + ffmpeg)
./install.ps1                       # or ./install.ps1 -Cpu / -Gpu / -PythonPath ...

# Daily work
.\.venv\Scripts\Activate.ps1
srtforge --help                     # Typer CLI
srtforge run path\to\video.mkv      # single file end-to-end
srtforge worker                     # persistent JSON loop on stdin/stdout (used by both GUIs)
srtforge-gui                        # legacy PySide6 GUI
```

Tests use pytest:

```powershell
pytest                              # full suite
pytest tests/test_pipeline.py       # one file
pytest tests/test_pipeline.py::test_name -k pattern    # one test
```

## Tauri Studio dev loop

```powershell
cd srtforge-studio
pnpm install                        # one-time
pnpm tauri dev                      # dev: launches Vite + Rust shell + bundled sidecar
```

`pnpm tauri dev` does **not** rebuild the Python sidecar. After any change to `srtforge/`, the spec, or the entry shim, rebuild it manually:

```powershell
cd C:\Srtforge-lat\Srtforge
.\.venv\Scripts\Activate.ps1     # MUST be the project venv, not global Python
pyinstaller --clean --noconfirm srtforge-studio\packaging\windows\srtforge_worker.spec --distpath srtforge-studio\src-tauri\binaries
ren srtforge-studio\src-tauri\binaries\srtforge_worker.exe srtforge_worker-x86_64-pc-windows-msvc.exe
```

Always activate `.venv` before pyinstaller — running it from global Python silently produces a half-bundled exe (the spec's `collect_all` skips deps with "as it is not a package" warnings, and the resulting worker fails at runtime with `ModuleNotFoundError: No module named 'rich'` or similar). The `-x86_64-pc-windows-msvc` suffix is required by Tauri's sidecar lookup. Use `--clean` whenever the spec or entry shim changes — stale onefile caches can produce a working-looking exe that crashes in the bootloader. To smoke-test the bundled worker independent of Tauri: run the exe directly with `worker`, then paste a JSON line on stdin (see `srtforge/cli.py worker` for the contract).

The legacy PySide6 GUI has its own PyInstaller spec at `packaging/windows/srtforge_gui.spec` that produces both `SrtforgeGUI.exe` and `SrtforgeCLI.exe` (the GUI shells out to the CLI exe).

## Pipeline architecture (`srtforge/pipeline.py`)

A single run is a fixed sequence — most edits land somewhere in this chain:

1. **English audio stream pick** (`ffmpeg.FFmpegTooling.probe_audio_streams`, ffprobe)
2. **PCM extraction** to 48 kHz stereo `pcm_f32le` via ffmpeg
3. **FV4 vocal separation** (`audio-separator` + `voc_fv4.ckpt`/`voc_gabox.yaml` from `models/`); GPU path uses `onnxruntime-gpu` with autocast
4. **Filter chain** (highpass/lowpass + SoXr resample to 16 kHz mono float)
5. **ASR** — `engine_whisper.py` (Faster-Whisper) or `engine_parakeet.py` (NeMo Parakeet TDT). Selected by `whisper.engine` in config (default: `parakeet`).
6. **Segmentation + SRT shaping** in `srtforge/post/{segmenter,srt_utils}.py`
7. Optional **Gemini correction** (gated by `gemini.enabled`)

Per-stage progress events are NOT yet emitted to the worker protocol — `pipeline.py` only emits the final `srt_written`. The Tauri UI handlers exist for `progress` events; lighting them up requires `_emit_worker_event({"event":"progress","id":...,"progress":...})` calls inside the pipeline.

## Worker protocol (JSON over stdin/stdout)

`srtforge worker` runs a persistent loop that consumes one JSON object per stdin line and emits one JSON object per stdout line. Defined in `srtforge/cli.py` (look for `_emit_worker_event` and the `worker` Typer command). Event vocabulary (also mirrored by Tauri's Rust shell and Zustand reducer):

- Lifecycle: `worker_starting`, `worker_ready`, `worker_stopping`, `worker_preload_skipped`, `worker_preload_failed`
- Job: `job_started`, `job_completed`, `job_failed`, `srt_written`, `bad_json`, `bad_payload`, `unknown_action`

When extending the protocol, edit **all three sides in lockstep**: emitter in `srtforge/cli.py`, the Rust event handler in `srtforge-studio/src-tauri/src/lib.rs`, and the Zustand reducer in `srtforge-studio/src/store.ts`.

## Config & path resolution

Two config files, both YAML despite extensions:

- `srtforge/config.yaml` — defaults bundled with the package (loaded via `srtforge.settings.load_settings`).
- `srtforge.config` — persistent on-disk overrides. Search order: `$SRTFORGE_PERSISTENT_CONFIG` → next to `sys.executable` (frozen builds) → `PROJECT_ROOT/srtforge.config` → user config dir.

`PROJECT_ROOT` resolution is in `srtforge/config.py:_resolve_project_root` and is **frozen-aware**:

- Dev: parent of the package dir.
- PyInstaller frozen: `$SRTFORGE_PROJECT_ROOT` if set, else the directory of `sys.executable`. Without this override, one-file bundles point at the throwaway `_MEI*` extraction dir and can't find `models/`.
- The Tauri shell sets `SRTFORGE_PROJECT_ROOT` automatically in dev builds by walking up from cwd to find a `models/` ancestor (see `find_dev_project_root` in `src-tauri/src/lib.rs`).

Models live in `models/` next to the project root (or next to the bundled exe in production).

## CUDA / GPU constraints

- `cuda-python>=12.3,<13` is a hard pin. The Parakeet GPU path calls `srtforge.asr._nemo_compat.ensure_cuda_python_available()` which rejects 13+ with a clear error. Don't relax this without verifying NeMo + ONNX Runtime CUDA EP compat.
- `_nemo_compat.install_megatron_microbatch_stub()` shims `megatron.core.num_microbatches_calculator` so NeMo doesn't warn about missing Megatron during inference. Touch with care — NeMo's distributed-training assumptions leak into the inference path.
- The PyInstaller spec (`srtforge-studio/packaging/windows/srtforge_worker.spec`) uses `collect_all` for ML deps because one-file mode strips lazily-loaded submodules. `cuda` is in the list specifically because its `cuda.bindings.runtime` / `cuda.cudart` are native `.pyd` extensions inside a namespace package — without `collect_all`, `ensure_cuda_python_available()` raises at runtime.
- The spec also sets `module_collection_mode={"nemo": "pyz+py", "torch": "pyz+py"}` so the bundle ships original `.py` source alongside `.pyc`. NeMo's Parakeet calls `torch.jit.script` on functions like `nemo.collections.asr.parts.utils.activations.snake`, and TorchScript needs `inspect.getsource()` to succeed at runtime. If a *different* scripted function fails with "Can't get source for &lt;function ... at 0x...&gt;", add its top-level package to `module_collection_mode` and rebuild.
- ffmpeg/ffprobe are bundled into the worker if `SRTFORGE_FFMPEG_DIR` is set at PyInstaller build time. The installer scripts set this for you (downloading from BtbN).

## What CLAUDE.md does **not** know that you should look up

- **Sonarr hook contract:** `srtforge/sonarr_hook.py` reads `EpisodeFile.Path` + `EventType` from env. Wired into Typer as `sonarr-hook`.
- **Settings drawer / Studio UI state:** `srtforge-studio/src/components/settings/` is partially UI-only stubs against the real `srtforge.settings` YAML schema; check both sides before claiming a setting is wired up.
- **PyInstaller multiprocessing on Windows:** `pyi_rth_multiprocessing.py` runtime hook already calls `freeze_support()` — adding it manually to the entry shim is redundant and has historically broken the bootloader. If a child crashes with "Bootloader did not set sys._pyinstaller_pyz", do a `--clean` rebuild before assuming a multiprocessing bug.
