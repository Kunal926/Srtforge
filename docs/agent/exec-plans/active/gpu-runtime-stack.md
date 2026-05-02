# ExecPlan: gpu-runtime-stack

## Purpose / big picture

Make the Studio sidecar and installer use a clean, redistributable GPU runtime
stack. The default Windows GPU build should use a unified CUDA 12.8 runtime,
avoid dependence on a system CUDA Toolkit, preload ONNX Runtime CUDA DLLs from
packaged runtime libraries, and fail early when Parakeet's NeMo CUDA-graph fast
path is unavailable.

## Context and orientation

- `requirements.txt`, `pyproject.toml`, and `install.ps1` currently disagree:
  the requirements file is older and conservative while the active venv has
  newer CUDA 13 PyTorch plus CUDA 12.8 ONNX Runtime.
- `install.ps1` currently defaults GPU PyTorch installs to CUDA 13.0 and tries
  to ensure a system CUDA Toolkit. The target default is CUDA 12.8 wheels and
  only an NVIDIA driver requirement.
- `srtforge-studio/packaging/windows/srtforge_worker.spec` collects CUDA
  packages, but the PyInstaller archive missed `_cuda_bindings_redirector`.
- `srtforge-studio/src-tauri/src/lib.rs` starts the sidecar as `worker`, which
  can preload an ASR model from root `srtforge.config`. Studio should start the
  worker with `--no-preload`.
- `srtforge/ffmpeg.py` probes ONNX Runtime CUDA providers before calling
  `onnxruntime.preload_dlls()`. The runtime should preload first.

## Progress

- [x] Phase 1: Capture the runtime-stack plan in a durable ExecPlan.
- [x] Phase 2: Align dependency constraints and Windows installer behavior.
- [x] Phase 3: Add deterministic GPU runtime initialization and smoke checks.
- [x] Phase 4: Update PyInstaller sidecar hooks and Studio worker launch.
- [x] Phase 5: Add tests, docs, validation, and handoff.

## Milestones

- Phase 2 done: install files default to CUDA 12.8 GPU stack and no longer
  require the CUDA Toolkit for end users.
- Phase 3 done: `srtforge gpu-smoke` reports Torch, ORT, CUDA Python,
  CUDA Python runtime-binding importability, and NeMo CUDA-graph status. It
  exits nonzero on missing fast-path support or missing runtime bindings.
- Phase 4 done: PyInstaller bundles the CUDA redirector hook, the hook adds the
  frozen CUDA/Torch DLL directories before CUDA Python imports, and Studio
  starts the sidecar with `worker --no-preload`.
- Phase 5 done: fast tests, Rust check, doc check, and handoff are updated.

## Steps

1. Update dependency truth:
   `requirements.txt`, `pyproject.toml`, and a new
   `constraints-gpu-cu128.txt` must pin the selected CUDA 12.8 GPU stack.
2. Update `install.ps1`:
   default `-Cuda auto` to CUDA 12.8, reinstall mismatched CUDA Torch wheels,
   remove the CUDA Toolkit install path from the default GPU flow, and install
   only `onnxruntime-gpu==1.25.1` for GPU.
3. Add Python runtime helpers:
   preload ONNX Runtime CUDA DLLs before provider checks, add a GPU smoke report
   command, and expose a cache cleanup helper between FV4 and ASR.
4. Update packaging:
   import `_cuda_bindings_redirector` from a PyInstaller runtime hook and add
   the hidden import/data coverage needed by the sidecar.
5. Update Studio launch:
   change Rust sidecar args to `["worker", "--no-preload"]`.
6. Validate:
   run targeted pytest, `cargo check`, `scripts/check_docs.py`, then the
   lightweight harness if feasible.

## Validation and acceptance

- `.\.venv\Scripts\python.exe -m pytest tests/test_gpu_runtime.py tests/test_nemo_compat.py tests/test_ffmpeg.py tests/test_cli_worker.py -q` - pass, 17 passed after fixing the NeMo smoke contract.
- `.\.venv\Scripts\python.exe -m pytest -q` - pass, 83 passed after repairing the current venv.
- `cargo fmt --check` from `srtforge-studio/src-tauri` - pass.
- `cargo check` from `srtforge-studio/src-tauri` - pass.
- `.\.venv\Scripts\python.exe scripts\check_docs.py` - pass after regenerating `docs/agent/PROJECT_MAP.md`.
- `.\.codex\actions\harness-check.ps1` - pass after removing one unused import found by Ruff; rerun after the installer no-CPU-fallback edit also passed.
- `.\.venv\Scripts\python.exe -m srtforge gpu-smoke` - initially failed in the dirty venv because it still had `torch 2.11.0+cu130`, `onnxruntime-gpu 1.25.0` built for CUDA 12.8, and older NeMo CUDA-graph behavior.
- `.\.venv\Scripts\python.exe -m srtforge gpu-smoke` after repairing the venv - pass; reports Torch CUDA 12.8, ORT CUDA build 12.8, CUDA Python 12.9.6, and NeMo conditional-node support.
- `.\.venv\Scripts\python.exe -m srtforge gpu-smoke` after adding the runtime binding guard - pass; reports `cuda_python.runtime_bindings` as `available`.
- `gpu-smoke` with CUDA Toolkit and Nsight paths removed from `PATH` - pass.
- `.\.venv\Scripts\python.exe -m pytest tests\test_nemo_compat.py tests\test_pipeline.py tests\test_gpu_runtime.py tests\test_engine_parakeet.py -q` - pass; 32 passed after adding frozen-runtime binding and prewarm coverage.
- `.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm srtforge-studio\packaging\windows\srtforge_worker.spec --distpath srtforge-studio\src-tauri\binaries` - pass.
- `srtforge-studio\src-tauri\binaries\srtforge_worker-x86_64-pc-windows-msvc.exe gpu-smoke` - pass after replacing the stale suffixed sidecar with the rebuilt exe.
- Suffixed sidecar `gpu-smoke` with CUDA Toolkit and Nsight paths removed from `PATH` - pass.
- Direct suffixed sidecar media run with `SRTFORGE_PROJECT_ROOT` set to the repo root - pass; wrote `tmp\new_saga_sidecar_check.srt`, completed FV4 separation at about `1.97 it/s`, and did not hit the previous `cuda.bindings.runtime` failure.
- `.\.venv\Scripts\python.exe -m pytest -m "not slow and not requires_model and not requires_cuda and not requires_media and not requires_ffmpeg" -q` - pass; 84 passed.
- `.\.codex\actions\harness-check.ps1` after the final sidecar fix - pass; default pytest (`84 passed`), Ruff, doc check, frontend type-check, and Rust `cargo check` all passed.
- PyInstaller archive inspection confirms `pyi_rth_cuda_bindings_redirector`,
  `_cuda_bindings_redirector.py`, `_cuda_bindings_redirector.pth`, and CUDA
  binding modules are bundled.
- `.\.venv\Scripts\python.exe -m pytest tests\test_engine_parakeet.py tests\test_pipeline.py tests\test_cli_worker.py tests\test_gpu_runtime.py -q` after the Parakeet whole-pass/Lhotse-off patch and Studio live-event throttling - pass; 30 passed.
- Venv worker benchmark using a Studio-shaped whole-pass Parakeet job for
  `D:\New Saga.mkv` - pass. `logs\7e9bc40a7df2474da50aa6f79a64ca19.log`
  showed FV4 `95.91s`, ASR `124.39s`, post-processing `13.59s`, write
  `1.14s`, total `243.44s`, and `rel_pos_local_attn=[768, 768]`.
- Rebuilt the PyInstaller sidecar again after the Parakeet/Studio performance
  fix and copied it to the suffixed Tauri sidecar name.
- Rebuilt suffixed sidecar `gpu-smoke` normally and with CUDA Toolkit/NVIDIA
  Toolkit paths removed from `PATH` - pass; Torch CUDA `12.8`, ORT CUDA build
  `12.8`, CUDA Python `12.9.6`, runtime bindings available, and NeMo
  conditional-node support.
- Rebuilt suffixed sidecar media benchmark with `SRTFORGE_PROJECT_ROOT` set and
  CUDA Toolkit paths removed from `PATH` - pass.
  `logs\c97981f387194c0380588977406e24e4.log` showed FV4 `111.45s`, ASR
  `131.92s`, post-processing `13.69s`, write `1.12s`, total `266.06s`.
- `.\.venv\Scripts\python.exe -m pytest -m "not slow and not requires_model and not requires_cuda and not requires_media and not requires_ffmpeg" -q` after the latest performance fix - pass; 85 passed.
- `.\.codex\actions\harness-check.ps1` after the latest performance fix - pass; default pytest (`85 passed`), Ruff, doc check, frontend type-check, and Rust `cargo check` all passed.

## Surprises and discoveries

- The current dev venv still reproduces the root issue: Torch reports CUDA 13.0
  while ONNX Runtime reports a CUDA 12.8 build. The new smoke command now fails
  this combination explicitly instead of letting Studio proceed on a slow path.
- `onnxruntime.preload_dlls()` writes DLL-load failures and compatibility
  warnings to stdout/stderr instead of raising, so the smoke helper captures
  that output and treats it as a failed preload.
- The first harness run caught an unused import in the new GPU helper; removing
  it made the full Codex harness pass.
- NeMo's CUDA-graph helper returns `None` when conditional nodes are supported
  and raises on failure. The smoke helper originally treated `None` as false;
  this is fixed and covered by a regression test.
- NeMo 2.7.3's `datasets` dependency requires newer PyArrow, so the repo pin
  moved from `pyarrow>=20,<21` to `pyarrow>=24,<25`.
- `faster-whisper 1.2.1` declares a dependency on the CPU `onnxruntime`
  distribution name. For the GPU stack, do not satisfy `pip check` by installing
  CPU ORT; install requirements first, install editable Srtforge with
  `--no-deps`, and keep only `onnxruntime-gpu`.
- The user's `gpu-smoke` failure came from running the stale suffixed Tauri
  sidecar. PyInstaller had produced a newer unsuffixed `srtforge_worker.exe`,
  but Tauri launches `srtforge_worker-x86_64-pc-windows-msvc.exe`; after copying
  the rebuilt exe to the suffixed name, the sidecar command passed.
- The later Studio failure was narrower than stack mismatch: the frozen archive
  contained `cuda\bindings\runtime.cp312-win_amd64.pyd`, but Windows could not
  find its hashed MSVC dependency because PyInstaller placed it under
  `_MEIPASS\cuda`. The runtime hook now calls `os.add_dll_directory()` for
  `_MEIPASS\cuda`, `_MEIPASS\torch\lib`, and `_MEIPASS\torchvision` before
  importing `_cuda_bindings_redirector`.
- Running the frozen sidecar directly outside Tauri needs
  `SRTFORGE_PROJECT_ROOT` in this dev layout. Without it the one-file release
  fallback looks for `<exe dir>\models`, while the repo models are under
  `C:\Srtforge-lat\Srtforge\models`. Tauri debug builds already set this env
  var by walking up to a directory that contains `models\`.
- The Studio ASR slowdown was not post-processing and not Parakeet's need for
  chunking. `logs\68af634e84594b56a1db8055ac78c324.log` showed post-processing
  did run in `13.75s`; NeMo ASR took `302.70s` and Studio stderr showed the
  Lhotse transcribe path (`Transcribing: 1it [04:35]`).
- The previous fast direct sidecar comparison was Whisper, not Parakeet,
  because root `srtforge.config` still had `whisper.engine: whisper`.
- Parakeet remains a single whole-episode pass. The fix is to call NeMo
  transcribe with `use_lhotse=False`, `num_workers=0`, `verbose=False`,
  `batch_size=1`, and timestamps enabled when the signature supports those
  arguments.
- Studio can lose throughput to WebView work when it mirrors tqdm/progress
  lines aggressively. The Tauri bridge now suppresses noisy raw tqdm lines and
  throttles live structured `progress` forwarding while keeping debug-log
  diagnostics.

## Decision log

- 2026-04-30: Default GPU packaging targets CUDA 12.8 instead of CUDA 13.0
  because ONNX Runtime 1.25 reports a CUDA 12.8 build and the goal is a clean
  end-user install without system CUDA Toolkit dependencies.
- 2026-04-30: CUDA 13 remains an explicit experimental path, not the default,
  until ORT, NeMo, and CUDA Python smoke checks all pass without compatibility
  shims.

## Outcomes and retrospective

- Implementation is complete for the repo-side stack alignment:
  dependency pins, installer behavior, ORT preload, sidecar CUDA redirector
  hook, Studio `worker --no-preload`, NeMo CUDA Python checks, GPU smoke report,
  separation-to-ASR cache cleanup, docs, and tests.
- The current `.venv` is repaired to the CUDA 12.8 stack and passes
  `gpu-smoke` without CUDA Toolkit paths on `PATH`.
- The rebuilt direct sidecar media path passes with Parakeet and CUDA
  separation after setting the same project-root environment Studio uses.
- Remaining acceptance is UI focused: restart `pnpm tauri dev` so Studio
  spawns the rebuilt suffixed sidecar, confirm no CUDA Python or NeMo
  CUDA-graph warnings in Studio logs, confirm no `Transcribing: 1it [04:35]`
  Lhotse path, then benchmark the same media through Studio. The rebuilt
  sidecar baseline for `D:\New Saga.mkv` is ASR `~132s` and total `~266s`
  without CUDA Toolkit paths on `PATH`.

## Idempotence and recovery

- Dependency-file edits are plain text and can be re-applied safely.
- `install.ps1` changes are idempotent: rerunning the installer should reinstall
  Torch only when the installed CUDA runtime does not match the requested tag.
- PyInstaller changes require a clean rebuild:
  `pyinstaller --clean --noconfirm srtforge-studio\packaging\windows\srtforge_worker.spec --distpath srtforge-studio\src-tauri\binaries`.
- If the sidecar exe is locked, run:
  `Get-Process srtforge_worker -ErrorAction SilentlyContinue | Stop-Process -Force`.
