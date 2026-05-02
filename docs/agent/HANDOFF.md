# HANDOFF.md

Living state of the repo. **Update this file before stopping any session.**

A fresh agent should be able to read this and `CONTEXT_BRIEF.md` and pick up
where you left off.

---

## Current goal

Restore the old Parakeet/FV4 baseline settings, keep minimum WER as the first
priority, and isolate the remaining separation regression without reducing FV4
quality.

Status: repo-side baseline fixes are implemented and validated. The current
repo now restores Parakeet local attention `[1280, 1280]`, migrates legacy
`parakeet:` config into the current ASR settings, logs actual Parakeet runtime
dtype, and keeps FV4 quality settings unchanged. The user reran the old
`C:\Srtforge` GUI path successfully on 2026-05-02, so treat that as the valid
old baseline and treat the earlier shell recheck in this worktree as a
contaminated comparison, not contrary evidence. The user then confirmed the
current focused Studio path matches old performance after the Max CUDA sidecar
and WebView/rendering fixes.

The default packaged GPU stack is now recorded as CUDA 12.8:

- PyTorch/TorchAudio/TorchVision `2.11.0+cu128` from the PyTorch cu128 wheel
  index.
- `onnxruntime-gpu==1.25.1` with no separate CPU `onnxruntime` package in GPU
  installs.
- `cuda-python==12.9.6` and `cuda-bindings==12.9.6`.
- `nemo_toolkit[asr]==2.7.3`, `audio-separator==0.44.1`,
  `faster-whisper==1.2.1`, and `ctranslate2==4.7.1`.

The current local venv now passes `srtforge gpu-smoke` both normally and with
CUDA Toolkit directories removed from `PATH`. It has `torch 2.11.0+cu128`,
`onnxruntime-gpu 1.25.1`, `cuda-python 12.9.6`, `cuda-bindings 12.9.6`,
`nemo-toolkit 2.7.3`, and no separate CPU `onnxruntime` distribution.

The rebuilt Tauri sidecar at
`srtforge-studio/src-tauri/binaries/srtforge_worker-x86_64-pc-windows-msvc.exe`
now exposes `gpu-smoke` and passes it, including with CUDA Toolkit paths removed
from `PATH`. The smoke now verifies `cuda.bindings.runtime` importability, not
only `cuda.__version__`.

Latest performance diagnosis on `D:\New Saga.mkv`:

- The Studio log at
  `logs/studio-debug/1777606434_92a64146-85e4-40c7-9c6a-188fefd9a8da.debug.log`
  did run Netflix-style post-processing. The slow part was NeMo ASR:
  `logs/68af634e84594b56a1db8055ac78c324.log` shows FV4 separation
  `125.16s`, ASR `302.70s`, post-processing `13.75s`, write `1.22s`.
- The earlier "fast sidecar" comparison was not Parakeet. It used Whisper
  (`large-v3-turbo`) from root `srtforge.config`, so it was not a valid
  Parakeet/Studio comparison.
- Parakeet stays single-pass for full episodes. No audio chunking is used.
- The root ASR slowdown was NeMo's Lhotse transcribe path. The Parakeet
  transcribe call now uses the whole audio path with `batch_size=1`,
  `use_lhotse=False`, `num_workers=0`, and `verbose=False` when the installed
  NeMo signature supports those arguments.
- A venv worker benchmark with the same Studio-shaped Parakeet job produced
  `logs/7e9bc40a7df2474da50aa6f79a64ca19.log`: FV4 `95.91s`, ASR
  `124.39s`, post-processing `13.59s`, write `1.14s`, total `243.44s`.
- The rebuilt suffixed sidecar was then tested without CUDA Toolkit paths on
  `PATH` and produced `logs/c97981f387194c0380588977406e24e4.log`: FV4
  `111.45s`, ASR `131.92s`, post-processing `13.69s`, write `1.12s`, total
  `266.06s`.
- Studio's Tauri bridge keeps the original raw terminal progress lines instead
  of adding synthetic FV4 summary lines. Normal structured `progress` events are
  still throttled to reduce WebView/Zustand repaint pressure.

Latest Studio settings fix:

- Product bug: Studio's queue pump always computed
  `<settings.outputDir>/<video>.srt` and passed that explicit output path to the
  worker. This ignored the "Save .srt next to video file" toggle and forced SRT
  output under `C:\Srtforge-lat\Srtforge\output` when the output directory was
  still `./output`.
- Fix: `srtforge-studio/src/App.tsx` now uses
  `computeSidecarOutputPath()` when `settings.sidecarSrt` is enabled, producing
  `<video-dir>\<video-name>.srt`; it falls back to `computeOutputPath()` only
  when sidecar output is disabled.
- New Studio defaults set `sidecarSrt: true`.
- The embed-related controls are now grouped together under "Video subtitle
  output": embed in container, burn subtitles, soft-embed method, track title,
  track language, default flag, forced flag, and replace-original.
- When "Embed in container" is disabled, the grouped burn/method/track/replace
  controls are disabled in the UI. The Studio worker payload also masks
  `burn` and `replace_original` to `false` when embed is off.
- Python worker config mapping now enforces the same relationship so a stale
  Studio state or hand-written worker payload cannot burn or replace media when
  embed is disabled.

Latest CLI ASR/GPU-utilization diagnosis:

- There is no explicit GPU usage cap in the Python ASR path: no CUDA memory
  fraction limit, no `CUDA_VISIBLE_DEVICES` pin, and no artificial throttle was
  found. Studio's sidebar GPU percentage remains placeholder telemetry and is
  not evidence of real CUDA utilization.
- The current ASR shape is the main intentional limiter: Parakeet v2 transcribes
  one whole episode as one audio item with `batch_size=1`, no Lhotse loader, and
  no ASR chunking. This preserves whole-episode context for minimum WER, but it
  will not necessarily drive an RTX laptop GPU to 100% SM utilization at every
  moment.
- Local runtime smoke is healthy: `srtforge gpu-smoke` reports Torch
  `2.11.0+cu128`, ORT CUDA build `12.8`, CUDA Python runtime bindings available,
  and NeMo CUDA-graph conditional-node support.
- Current `D:\New Saga.mkv` logs show variability even without config changes:
  latest `logs\36c406120334436c9653bda45025743d.log` had FV4 `108.67s`, ASR
  `88.28s`, post `13.72s`, total `220.11s`; the prior
  `logs\d283f97d44e64b5c9e97d642c932dd5d.log` had FV4 `109.61s`, ASR
  `145.61s`, post `13.58s`, total `278.36s`.
- `nvidia-smi` showed two live `srtforge_worker.exe worker --no-preload`
  processes under `srtforge-studio/src-tauri/target/debug/`, with one holding
  CUDA memory after a Studio run. Treat clean benchmarking as "stop stale Studio
  workers first, then run CLI/worker benchmark."
- New diagnostics now log Parakeet ASR sub-timings into the per-run performance
  log without changing model, precision, chunking, timestamps, or decoding
  arguments. Look for `ASR detail: parakeet_model_load_or_cache`,
  `parakeet_runtime_settings`, `parakeet_transcribe_with_timestamps`,
  optional `parakeet_write_word_timestamps`, and `parakeet_event_shaping`.
- User clarified that "recent UI" means the whole new Rust/Tauri UI compared
  with the older direct CLI flow, not only the small settings/logging tweaks.
  That makes the relevant delta the Tauri sidecar bridge: PyInstaller sidecar
  stdout/stderr captured by Rust, persisted Studio debug logs, and live event
  forwarding.
- The latest Studio debug log confirmed FV4 is on CUDA but the separator tqdm
  lines are captured by the Rust bridge: `logs\studio-debug\1777643355_036bb213-12ee-4b0b-bc12-ff4acffb69d2.debug.log`
  shows Torch CUDA, ORT `CUDAExecutionProvider`, `FV4 separation is running with
  CUDA acceleration`, and tqdm around `1.80-1.83 it/s`.
- Rust bridge fix: `srtforge-studio/src-tauri/src/lib.rs` now keeps tqdm-style
  terminal progress as the original raw log lines for live/debug logs and does
  not synthesize extra `FV4 progress: ...` lines. Normal structured `progress`
  events remain throttled separately.

Latest old-baseline restoration and separation isolation:

- `C:\Srtforge` is the golden comparison for this investigation. Its known fast
  run `C:\Srtforge\logs\8f6737756be146c6be37c70def109453.log` shows
  `D:\New Saga.mkv`, FV4 `92.77s` at about `2.13 it/s`, ASR inference
  `75.19s`, and Parakeet local attention `[1280, 1280]`.
- The user reran the same old repo via `PS C:\Srtforge> .\.venv\Scripts\Activate.ps1`
  then `srtforge-gui` on 2026-05-02. That run showed FV4 steady-state around
  `2.13-2.18 it/s`, FV4 `91.80s`, ASR inference `74.52s`, ASR pipeline
  `102.99s`, and pipeline total `199.76s`. The outer GUI timer showed
  `235.73s`, which includes wrapper/queue/process overhead outside the
  pipeline `RunLogger` total.
- The current Tauri performance log named by the user,
  `logs\2b28d3b97a334ef8a9af14302bd4b60c.log`, shows the real remaining gap:
  total `284.08s`, FV4 `107.61s`, an extra pre-separation
  `Prepare Parakeet CUDA runtime` block of `8.05s`, and ASR pipeline
  `163.86s`. Against the old `8f673...` performance log this is `+81.61s`
  total, with FV4 `+14.84s`, prewarm `+8.05s`, and ASR pipeline `+59.06s`.
  That log predates, or did not pick up, the current prewarm-order fix that
  moves Parakeet CUDA preparation after FV4 separation.
- The confirmed config regression is fixed in the current repo: Python defaults,
  `srtforge/config.yaml`, legacy PySide defaults, Studio defaults, and local
  `srtforge.config` now use Parakeet `rel_pos_local_attn=[1280, 1280]`.
- Current settings loading now migrates legacy top-level `parakeet:` config
  fields into the newer `whisper:` ASR block while preserving explicit
  `whisper:` values.
- Parakeet now logs actual model runtime dtype after load, after applying the
  precision policy, and after long-audio attention mutation. On CUDA with
  `force_float32=false`, it reapplies bf16 when supported, otherwise fp16.
- Parakeet CUDA runtime prewarm now runs after FV4 separation and separation GPU
  cache cleanup, before ASR model load. This restores the old stage ordering so
  ASR CUDA setup does not touch the GPU before the FV4 benchmark.
- No FV4 model/config quality setting was reduced. The FV4 checkpoint hashes
  match between `C:\Srtforge-lat\Srtforge\models\voc_fv4.ckpt` and
  `C:\Srtforge\models\voc_fv4.ckpt`; the `voc_gabox.yaml` content is the same
  aside from file-level hash/formatting differences.
- Current CLI benchmark with `[1280, 1280]` before the prewarm-order change:
  `logs\68ea693117de42cfb8a9d36526a6e870.log` showed FV4 `104.12s`,
  Parakeet dtype `float32 -> bfloat16 -> bfloat16`, Parakeet transcribe
  `89.27s`, ASR pipeline `127.81s`, total `256.67s`.
- Current CLI benchmark after moving prewarm after separation:
  `logs\bff1fa063c614e9b91ea53ee7f501c13.log` showed FV4 `101.42s`, final
  tqdm about `1.95 it/s`, prewarm after separation `1.44s`, Parakeet transcribe
  `87.84s`, ASR pipeline `105.64s`, total `211.91s`.
- Current CLI benchmark with repo-bundled FFmpeg prepended to `PATH`:
  `logs\7f427b322902425e81a453ebeec8b52d.log` showed FV4 `102.47s`,
  Parakeet transcribe `86.67s`, ASR pipeline `104.88s`, total `228.75s`.
  The bundled FFmpeg path did not restore `2.10+ it/s`.
- Current source run under the old `C:\Srtforge\.venv` dependency stack, using
  Whisper config only to bypass Parakeet startup, failed later because that venv
  lacks `faster_whisper`, but completed FV4 first:
  `logs\4cda1625920b4656854b545d423a1be1.log` showed FV4 `104.14s`.
- A direct old-repo recheck in this shell failed to reproduce the user's GUI
  baseline and the wrapper PowerShell hung after Python wrote an SRT. Do not use
  that shell recheck to dismiss the old repo baseline; the user's direct
  `srtforge-gui` run is the valid comparison.
- Studio/Tauri observability fix: `srtforge-studio/src-tauri/src/lib.rs`
  forwards the original separator tqdm log lines again, such as
  `[stderr] warn: 28%|... 2.14it/s`, and removed the synthetic
  `[stderr] info: FV4 progress: ...` summaries to avoid extra log/render work.

Latest Studio Max CUDA throughput fix:

- The fixed Tauri run now matches old performance. The likely regression was
  not FV4/ASR quality settings; it was a combination of stale sidecar binaries
  and foreground WebView2/DWM/render pressure. The stale sidecar was proven when
  the Studio log still said `Parakeet CUDA Python bindings preloaded before
  separation`, while current source logs `before ASR`.
- Added `scripts/rebuild_studio_sidecar.ps1`. It stops stale
  `srtforge_worker.exe` processes, runs PyInstaller with the Studio spec, copies
  `srtforge_worker.exe` over
  `srtforge_worker-x86_64-pc-windows-msvc.exe`, and runs the suffixed
  `gpu-smoke`. `CLAUDE.md`, `srtforge-studio/README.md`, and
  `docs/agent/CONTEXT_BRIEF.md` now point to this helper.
- Rebuilt the suffixed Studio sidecar with the helper on 2026-05-02. The
  suffixed `gpu-smoke` passed with Torch `2.11.0+cu128`, ORT
  `CUDAExecutionProvider`, `cuda-python` runtime bindings available, and NeMo
  conditional-node support true. The rebuilt suffixed sidecar LastWriteTime was
  `05/02/2026 09:42:38`.
- Tauri window config now uses an opaque window instead of a transparent one
  and sets WebView2 browser args to preserve Wry's default disabled WebView
  features while adding `--disable-gpu`.
- Studio now has a persisted `gpuPerformanceMode` setting, defaulting to true.
  While a job is processing, the app applies `gpu-max-mode` / root
  `data-gpu-performance=active`, disables nonessential animations/transitions,
  hides pulses/playheads, and swaps active queue/detail waveforms to static
  progress surfaces. This does not change FV4, ASR, precision, attention,
  chunking, or decoding behavior.
- Rust live-event forwarding now throttles normal `progress` and normal `log`
  events to about once per second, while preserving immediate stage/job events,
  warnings/errors, and raw terminal progress lines. Synthetic
  `FV4 progress: ... it/s` summaries were removed after the successful
  benchmark because they added log/render events beyond the original tqdm line.
  Saved Studio debug logs still get the full terminal/debug stream.
- Studio debug logs now write a job-start runtime line with Max CUDA mode state,
  WebView GPU acceleration state, sidecar path, sidecar mtime, and enqueue time.
  Use this line to confirm the focused-Studio benchmark is using the rebuilt
  sidecar and Max CUDA path.
- Design guidance after the fix: bring back polish only when it is idle-only or
  static during GPU jobs. Safe candidates are static CSS fills, static icons,
  stable layouts, and one-time state changes. Avoid active animated SVG/canvas
  waveforms, pulse/playhead loops, transparent-window composition, WebView GPU
  acceleration, and synthetic per-progress log events while FV4/ASR is running.

Latest Studio UI/log cleanup:

- The user confirmed focused Studio now matches old performance.
- History output menus now open upward when there is not enough viewport space
  below the row, so rows near the footer still expose SRT, performance log,
  debug log, and containing-folder actions.
- The active live-log pane resets per job and no longer slices the current job
  down to the last 80/500 lines. Saved debug logs remain the authoritative full
  live/Typer stream for completed jobs.
- The Rust bridge no longer parses tqdm into synthetic `FV4 progress:` summary
  events. Live/debug logs now keep the original raw tqdm-style progress lines,
  for example `[stderr] warn: 28%|... 2.14it/s`, while normal structured
  `progress` events remain throttled.

## Current branch / git state

- Branch: `codex-windows-agent-layer`.
- Upstream: `origin/codex-windows-agent-layer`.
- Latest implementation push: `dded575 studio: restore cuda throughput`.
- `dded575` is pushed to `origin/codex-windows-agent-layer`.
- Working tree was clean immediately after the implementation push; this
  handoff record is being committed and pushed separately.
- Latest pushed commit before the GPU throughput work:
  `068e909 docs: record successful push`.

## Changed files

Runtime stack and installer:

- `constraints-gpu-cu128.txt`
- `requirements.txt`
- `pyproject.toml`
- `install.ps1`

Python runtime and CLI:

- `srtforge/gpu_runtime.py`
- `srtforge/cli.py`
- `srtforge/ffmpeg.py`
- `srtforge/pipeline.py`
- `srtforge/asr/_nemo_compat.py`
- `srtforge/settings.py`
- `srtforge/config.yaml`
- `srtforge/engine_parakeet.py`
- `srtforge/gui_app.py`

Studio sidecar and UI:

- `scripts/rebuild_studio_sidecar.ps1`
- `srtforge-studio/src-tauri/tauri.conf.json`
- `srtforge-studio/src-tauri/src/lib.rs`
- `srtforge-studio/src/App.tsx`
- `srtforge-studio/src/components/ActiveDetail.tsx`
- `srtforge-studio/src/components/Queue.tsx`
- `srtforge-studio/src/components/SettingsDrawer.tsx`
- `srtforge-studio/src/components/settings/Field.tsx`
- `srtforge-studio/src/lib/workerConfig.ts`
- `srtforge-studio/src/store.ts`
- `srtforge-studio/src/styles/index.css`
- `srtforge-studio/src/types.ts`
- `srtforge-studio/packaging/windows/srtforge_worker.spec`
- `srtforge-studio/packaging/windows/pyi_rth_cuda_bindings_redirector.py`

Tests:

- `tests/test_gpu_runtime.py`
- `tests/test_cli_worker.py`
- `tests/test_nemo_compat.py`
- `tests/test_pipeline.py`
- `tests/test_engine_parakeet.py`

Docs and project context:

- `README.md`
- `CLAUDE.md`
- `srtforge-studio/README.md`
- `docs/agent/CONTEXT_BRIEF.md`
- `docs/architecture/ARCHITECTURE.md`
- `docs/agent/PROJECT_MAP.md`
- `docs/agent/exec-plans/active/gpu-runtime-stack.md`
- `docs/agent/HANDOFF.md`

## Commands run and results

- `.\.venv\Scripts\python.exe -m pytest tests/test_gpu_runtime.py tests/test_nemo_compat.py tests/test_ffmpeg.py tests/test_cli_worker.py -q` - pass; 17 passed after fixing the NeMo smoke contract.
- `.\.venv\Scripts\python.exe -m pytest -q` - pass; 83 passed after fixing the current venv.
- `.\.venv\Scripts\python.exe -m srtforge gpu-smoke` before fixing the venv - expected fail. It reported `torch 2.11.0+cu130`, ORT CUDA build `12.8`, failed CUDA 12 DLL preload, and NeMo CUDA graphs disabled.
- `.\.venv\Scripts\python.exe -m pip uninstall -y onnxruntime` - pass; removed CPU ORT.
- `.\.venv\Scripts\python.exe -m pip install --upgrade --force-reinstall --index-url https://download.pytorch.org/whl/cu128 --extra-index-url https://pypi.org/simple torch==2.11.0+cu128 torchvision==0.26.0+cu128 torchaudio==2.11.0+cu128` - pass.
- `.\.venv\Scripts\python.exe -m pip install --no-cache-dir --upgrade --upgrade-strategy eager -c constraints-gpu-cu128.txt onnxruntime-gpu==1.25.1 cuda-python==12.9.6 cuda-bindings==12.9.6 audio-separator==0.44.1 ctranslate2==4.7.1 nemo_toolkit[asr]==2.7.3 google-genai` - pass after the first attempt hit a pip wheel-cache permission error.
- `.\.venv\Scripts\python.exe -m pip install -e .` - pass, but pip reinstalled CPU `onnxruntime` through Faster-Whisper metadata; installer was changed to use `pip install --no-deps -e .`.
- `.\.venv\Scripts\python.exe -m pip uninstall -y onnxruntime` followed by `.\.venv\Scripts\python.exe -m pip install --no-cache-dir --force-reinstall --no-deps onnxruntime-gpu==1.25.1` - pass; restored the GPU ORT package after removing CPU ORT.
- `.\.venv\Scripts\python.exe -m srtforge gpu-smoke` after fixing the venv and NeMo smoke contract - pass; reports Torch CUDA 12.8, ORT CUDA build 12.8, `cuda.__version__` 12.9.6, and NeMo conditional nodes supported.
- `gpu-smoke` with CUDA Toolkit and Nsight paths removed from `PATH` - pass; confirms the venv does not depend on system CUDA Toolkit paths for the smoke.
- `.\.venv\Scripts\python.exe -m pip list --format=freeze` filtered to stack packages - pass; confirms `onnxruntime-gpu==1.25.1` is installed and CPU `onnxruntime` is absent.
- `.\.venv\Scripts\python.exe -m pip check` - expected fail because `faster-whisper 1.2.1` declares a dependency on the CPU `onnxruntime` distribution even though `onnxruntime-gpu` provides the runtime module. Do not satisfy this by installing CPU ORT in the GPU stack.
- `cargo fmt --check` from `srtforge-studio/src-tauri/` - pass.
- `cargo check` from `srtforge-studio/src-tauri/` - pass.
- `.\.venv\Scripts\python.exe -m compileall -q srtforge` - pass.
- `.\.venv\Scripts\python.exe scripts\update_context.py` - pass; regenerated `docs/agent/PROJECT_MAP.md`.
- `.\.venv\Scripts\python.exe scripts\check_docs.py` - pass; `docs check OK`.
- `.\.venv\Scripts\python.exe -m pip index versions numpy` - pass; confirms `2.4.4` available.
- `.\.venv\Scripts\python.exe -m pip index versions onnxruntime-gpu` - pass; confirms `1.25.1` available.
- `.\.venv\Scripts\python.exe -m pip index versions nemo_toolkit` - pass; confirms `2.7.3` available.
- `.\.venv\Scripts\python.exe -m pip index versions audio-separator` - pass; confirms `0.44.1` available.
- `.\.venv\Scripts\python.exe -m pip index versions cuda-python` - pass; confirms `12.9.6` available.
- `.\.venv\Scripts\python.exe -m pip index versions cuda-bindings` - pass; confirms `12.9.6` available.
- `.\.venv\Scripts\python.exe -m pip index versions faster-whisper` - pass; confirms `1.2.1` available.
- `.\.venv\Scripts\python.exe -m pip index versions ctranslate2` - pass; confirms `4.7.1` available.
- `.\.venv\Scripts\python.exe -m pip index versions torch --index-url https://download.pytorch.org/whl/cu128` - pass; confirms `2.11.0+cu128` available.
- `.\.venv\Scripts\python.exe -m pip index versions torchaudio --index-url https://download.pytorch.org/whl/cu128` - pass; confirms `2.11.0+cu128` available.
- `.\.venv\Scripts\python.exe -m pip index versions torchvision --index-url https://download.pytorch.org/whl/cu128` - pass; confirms `0.26.0+cu128` available.
- `.\.venv\Scripts\python.exe -m pip index versions pyinstaller` - pass; confirms `6.20.0` available.
- `.\.codex\actions\harness-check.ps1` - first run failed Ruff because `srtforge/gpu_runtime.py` had an unused `sys` import.
- `.\.codex\actions\harness-check.ps1` after repairing the current venv and updating the handoff - pass. It fell back from missing `pwsh` to Windows PowerShell and all executed checks passed, including default pytest (`83 passed`).
- `.\.venv\Scripts\python.exe -m pytest tests\test_nemo_compat.py tests\test_pipeline.py tests\test_gpu_runtime.py tests\test_engine_parakeet.py -q` - pass; 32 passed after adding the frozen-runtime binding guard and prewarm coverage.
- `.\.venv\Scripts\python.exe -m srtforge gpu-smoke` after adding the runtime binding check - pass; reports `cuda_python.runtime_bindings` as `available`.
- `.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm srtforge-studio\packaging\windows\srtforge_worker.spec --distpath srtforge-studio\src-tauri\binaries` - pass; rebuilt `srtforge_worker.exe` from the fixed venv.
- `srtforge-studio\src-tauri\binaries\srtforge_worker.exe --help` - pass; command list includes `gpu-smoke`.
- `srtforge-studio\src-tauri\binaries\srtforge_worker.exe gpu-smoke` - pass.
- Copied rebuilt `srtforge_worker.exe` over `srtforge_worker-x86_64-pc-windows-msvc.exe`, the sidecar name Tauri launches.
- `srtforge-studio\src-tauri\binaries\srtforge_worker-x86_64-pc-windows-msvc.exe gpu-smoke` - pass.
- Suffixed sidecar `gpu-smoke` with CUDA Toolkit and Nsight paths removed from `PATH` - pass.
- Direct sidecar media repro without `SRTFORGE_PROJECT_ROOT` - expected fail:
  frozen sidecar used release convention `<exe dir>/models`, but the dev
  models live at repo-root `models/`. Studio debug builds set
  `SRTFORGE_PROJECT_ROOT` automatically.
- Direct sidecar media repro with `SRTFORGE_PROJECT_ROOT=C:\Srtforge-lat\Srtforge`:
  pass. Command wrote `tmp\new_saga_sidecar_check.srt`, FV4 separation used
  CUDA and completed 178 steps in 1:30 at about `1.97 it/s`; the whole sidecar
  run completed in about 3:43 with no `cuda.bindings.runtime` failure.
- `.\.venv\Scripts\python.exe -m pytest -m "not slow and not requires_model and not requires_cuda and not requires_media and not requires_ffmpeg" -q` - pass; 84 passed.
- `.\.codex\actions\harness-check.ps1` after the final sidecar fix - pass. It
  fell back from missing `pwsh` to Windows PowerShell and all executed checks
  passed, including default pytest (`84 passed`), Ruff, doc freshness, frontend
  type-check, and Rust `cargo check`.
- `.\.venv\Scripts\python.exe -m pytest tests\test_engine_parakeet.py tests\test_pipeline.py tests\test_cli_worker.py tests\test_gpu_runtime.py -q` after the Parakeet whole-pass/Lhotse-off patch and Studio live-event throttling - pass; 30 passed.
- `cargo fmt --check` and `cargo check` from `srtforge-studio/src-tauri/` after
  the Studio live-event throttling patch - pass.
- Venv worker benchmark using a Studio-shaped Parakeet JSON job for
  `D:\New Saga.mkv` - pass; wrote
  `tmp\new_saga_parakeet_wholepass_worker.srt`. Performance log
  `logs\7e9bc40a7df2474da50aa6f79a64ca19.log` showed FV4 `95.91s`, ASR
  `124.39s`, post-processing `13.59s`, write `1.14s`, total `243.44s`, with
  `rel_pos_local_attn=[768, 768]` and no Lhotse transcribe progress warning.
- `.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm srtforge-studio\packaging\windows\srtforge_worker.spec --distpath srtforge-studio\src-tauri\binaries` after the Parakeet/Studio performance fix - pass; rebuilt `srtforge_worker.exe` and copied it over `srtforge_worker-x86_64-pc-windows-msvc.exe`.
- Rebuilt suffixed sidecar `gpu-smoke` normally and with CUDA Toolkit/NVIDIA
  Toolkit paths removed from `PATH` - pass; both reported Torch CUDA `12.8`,
  ORT CUDA build `12.8`, CUDA Python `12.9.6`,
  `cuda_python.runtime_bindings=available`, and NeMo conditional-node support.
- Rebuilt suffixed sidecar media benchmark using the same Studio-shaped Parakeet
  JSON job, `SRTFORGE_PROJECT_ROOT=C:\Srtforge-lat\Srtforge`, and CUDA Toolkit
  paths removed from `PATH` - pass; wrote
  `tmp\new_saga_parakeet_wholepass_sidecar.srt`. Performance log
  `logs\c97981f387194c0380588977406e24e4.log` showed FV4 `111.45s`, ASR
  `131.92s`, post-processing `13.69s`, write `1.12s`, total `266.06s`.
- `.\.venv\Scripts\python.exe -m pytest -m "not slow and not requires_model and not requires_cuda and not requires_media and not requires_ffmpeg" -q` after the latest performance fix - pass; 85 passed.
- `.\.codex\actions\harness-check.ps1` after the latest performance fix - pass.
  It fell back from missing `pwsh` to Windows PowerShell and all executed checks
  passed, including default pytest (`85 passed`), Ruff, doc freshness, frontend
  type-check, and Rust `cargo check`.
- `pnpm exec tsc --noEmit` from `srtforge-studio/` after the settings-output
  fix - pass.
- `.\.venv\Scripts\python.exe -m pytest tests\test_cli_worker.py -q` after the
  settings-output fix - pass; 5 passed.
- `.\.venv\Scripts\python.exe -m compileall -q srtforge` after the
  settings-output fix - pass.
- `.\.venv\Scripts\python.exe -m pytest -m "not slow and not requires_model and not requires_cuda and not requires_media and not requires_ffmpeg" -q` after the settings-output fix - pass; 86 passed.
- `.\.codex\actions\harness-check.ps1` after the settings-output fix - pass.
  It fell back from missing `pwsh` to Windows PowerShell and all executed checks
  passed, including default pytest (`86 passed`), Ruff, frontend type-check, and
  Rust `cargo check`. Doc freshness printed a stale-project-map warning, so
  `scripts/update_context.py` was rerun afterward.
- `.\.venv\Scripts\python.exe scripts\update_context.py` after the final
  source/doc edits - pass; refreshed `docs\agent\PROJECT_MAP.md`.
- `.\.venv\Scripts\python.exe scripts\check_docs.py` after refreshing the
  project map and handoff - pass; `docs check OK`.
- `.\.venv\Scripts\pyi-archive_viewer.exe -l -b srtforge-studio\src-tauri\binaries\srtforge_worker-x86_64-pc-windows-msvc.exe | Select-String "_cuda_bindings_redirector|cuda\bindings"` - pass; archive contains `pyi_rth_cuda_bindings_redirector`, `_cuda_bindings_redirector.py`, `_cuda_bindings_redirector.pth`, and CUDA binding modules.
- `git diff --check` - pass; only line-ending warnings were printed.
- `nvidia-smi --query-gpu=name,driver_version,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,pstate,clocks.sm,clocks.mem,power.draw,power.limit --format=csv` - pass; detected RTX 3070 Ti Laptop GPU, driver `595.97`, and about `4210 MiB` allocated while Studio worker processes were still alive.
- `.\.venv\Scripts\python.exe -m srtforge gpu-smoke` - pass; runtime report OK for Torch CUDA 12.8, ORT CUDA 12.8, CUDA Python runtime bindings, and NeMo CUDA-graph conditional-node support.
- Torch probe via `.\.venv\Scripts\python.exe -` - pass; `torch 2.11.0+cu128`, CUDA available, RTX 3070 Ti Laptop GPU, compute capability `(8, 6)`.
- `Get-CimInstance Win32_Process -Filter "Name='srtforge_worker.exe'"` - pass; found two live Studio debug worker processes running `worker --no-preload`.
- `.\.venv\Scripts\python.exe -m pytest tests\test_pipeline.py tests\test_engine_parakeet.py -q` - pass; `23 passed`.
- `.\.venv\Scripts\python.exe -m compileall -q srtforge` - pass.
- `.\.venv\Scripts\python.exe -m ruff check srtforge\engine_parakeet.py srtforge\pipeline.py tests\test_pipeline.py` - pass.
- `.\.codex\actions\harness-check.ps1` after Parakeet timing diagnostics - pass. It fell back from missing `pwsh` to Windows PowerShell and all executed checks passed, including default pytest (`86 passed`), Ruff, frontend type-check, and Rust `cargo check`. Doc freshness warned that `PROJECT_MAP.md` was stale after source edits.
- `.\.venv\Scripts\python.exe scripts\update_context.py` after Parakeet timing diagnostics - pass; refreshed `docs\agent\PROJECT_MAP.md`.
- `.\.venv\Scripts\python.exe scripts\check_docs.py` after refreshing the project map - pass; `docs check OK`.
- `cargo fmt` from `srtforge-studio/src-tauri` after the Rust bridge progress-line filter - pass after replacing a Rust 2024 `let` chain with Rust 2021-compatible code.
- `cargo test --lib` from `srtforge-studio/src-tauri` after the Rust bridge progress-line filter - pass; `4 passed`.
- `cargo check` from `srtforge-studio/src-tauri` after the Rust bridge progress-line filter - pass.
- `cargo fmt` from `srtforge-studio/src-tauri` after adding sampled FV4 `it/s`
  progress summaries - pass.
- `cargo test --lib` from `srtforge-studio/src-tauri` after adding sampled FV4
  `it/s` progress summaries - pass; `7 passed`.
- `cargo check` from `srtforge-studio/src-tauri` after adding sampled FV4
  `it/s` progress summaries - pass.
- `.\.venv\Scripts\python.exe scripts\update_context.py` after the FV4
  progress-summary and handoff edits - pass; refreshed
  `docs\agent\PROJECT_MAP.md`.
- `.\.venv\Scripts\python.exe scripts\check_docs.py` after refreshing the
  project map - pass; `docs check OK`.
- Final `.\.codex\actions\harness-check.ps1` after the Max CUDA sidecar/UI
  changes - pass. It fell back from missing `pwsh` to Windows PowerShell and
  all executed checks passed: Python import smoke, default pytest (`90 passed`),
  CLI smoke, Ruff, doc freshness, frontend type-check, and Rust `cargo check`.
- `cargo fmt`, `cargo test --lib`, and `cargo check` after restoring saved
  debug-log fidelity for raw terminal progress - pass; Rust tests remained
  `9 passed`.
- `.\.venv\Scripts\python.exe scripts\check_docs.py` after the debug-log
  fidelity handoff edit - pass; `docs check OK`.
- Final `.\.codex\actions\harness-check.ps1` after the debug-log fidelity
  adjustment - pass. It fell back from missing `pwsh` to Windows PowerShell and
  all executed checks passed: Python import smoke, default pytest (`90 passed`),
  CLI smoke, Ruff, doc freshness, frontend type-check, and Rust `cargo check`.
- `pnpm exec tsc --noEmit` after the History menu and log cleanup - pass.
- `cargo fmt`, `cargo test --lib`, and `cargo check` after removing synthetic
  FV4 progress summaries from the Rust bridge - pass; Rust tests now `6 passed`.
- `.\.venv\Scripts\python.exe scripts\update_context.py` after the UI/log docs
  edits - pass; refreshed `docs\agent\PROJECT_MAP.md`.
- `git diff --check` after the UI/log cleanup - pass; only line-ending warnings
  were printed.
- `.\.venv\Scripts\python.exe scripts\check_docs.py` after refreshing project
  context - pass; `docs check OK`.
- Final `.\.codex\actions\harness-check.ps1` after the UI/log cleanup - pass.
  It fell back from missing `pwsh` to Windows PowerShell and all executed checks
  passed: Python import smoke, default pytest (`90 passed`), CLI smoke, Ruff,
  doc freshness, frontend type-check, and Rust `cargo check`.
- `git diff --check` after the FV4 progress-summary edits - pass; only
  line-ending warnings were printed.
- `.\.codex\actions\harness-check.ps1` after the FV4 progress-summary and
  handoff edits - pass. It fell back from missing `pwsh` to Windows PowerShell
  and all executed checks passed: Python import smoke, default pytest
  (`90 passed`), CLI smoke, Ruff, doc freshness, frontend type-check, and Rust
  `cargo check`.
- `.\.codex\actions\harness-check.ps1` after the Rust bridge progress-line filter - pass. It fell back from missing `pwsh` to Windows PowerShell and all executed checks passed, including default pytest (`86 passed`), Ruff, doc freshness, frontend type-check, and Rust `cargo check`.
- `.\.venv\Scripts\python.exe -m pytest tests/test_settings.py tests/test_engine_parakeet.py tests/test_pipeline.py tests/test_cli_worker.py -q` after restoring `[1280, 1280]`, adding legacy `parakeet:` migration, and adding Parakeet dtype diagnostics - pass; `35 passed`.
- `.\.venv\Scripts\python.exe -m ruff check srtforge\settings.py srtforge\engine_parakeet.py srtforge\pipeline.py srtforge\ffmpeg.py srtforge\gui_app.py tests\test_settings.py tests\test_engine_parakeet.py tests\test_pipeline.py tests\test_cli_worker.py` - pass.
- `.\.venv\Scripts\python.exe -m compileall -q srtforge` - pass.
- `pnpm exec tsc --noEmit` from `srtforge-studio/` after Studio default attention migration - pass.
- `cargo test --lib` from `srtforge-studio/src-tauri/` - pass; `4 passed`.
- `cargo check` from `srtforge-studio/src-tauri/` - pass.
- `.\.venv\Scripts\python.exe -m pytest tests\test_gpu_runtime.py tests\test_nemo_compat.py -q` - pass; `10 passed`.
- Current venv CLI benchmark on `D:\New Saga.mkv` with current local config `[1280, 1280]` - pass; wrote `tmp\new_saga_current_cli_1280.srt`. Performance log `logs\68ea693117de42cfb8a9d36526a6e870.log` showed FV4 `104.12s`, Parakeet dtype `float32 -> bfloat16 -> bfloat16`, Parakeet transcribe `89.27s`, ASR pipeline `127.81s`, total `256.67s`.
- Current venv CLI benchmark after moving Parakeet CUDA prewarm after FV4 - pass; wrote `tmp\new_saga_current_cli_post_sep_prewarm_1280.srt`. Performance log `logs\bff1fa063c614e9b91ea53ee7f501c13.log` showed FV4 `101.42s`, prewarm after separation `1.44s`, Parakeet transcribe `87.84s`, ASR pipeline `105.64s`, total `211.91s`.
- Current venv CLI benchmark with repo-bundled FFmpeg prepended to `PATH` - pass; wrote `tmp\new_saga_current_cli_bundled_ffmpeg_1280.srt`. Performance log `logs\7f427b322902425e81a453ebeec8b52d.log` showed FV4 `102.47s`, Parakeet transcribe `86.67s`, ASR pipeline `104.88s`, total `228.75s`.
- Current source under the old `C:\Srtforge\.venv` dependency stack with a Whisper-only temp config to bypass Parakeet startup - expected fail after FV4 because old venv lacks `faster_whisper`; performance log `logs\4cda1625920b4656854b545d423a1be1.log` still captured FV4 `104.14s`.
- Old repo shell recheck from `C:\Srtforge` - contaminated comparison. The user
  later reran the valid old `srtforge-gui` workflow and reproduced the fast
  baseline: FV4 `91.80s` with steady `2.13-2.18 it/s`, ASR inference `74.52s`,
  and pipeline total `199.76s`.
- `git diff --check` - pass; only line-ending warnings were printed.
- `.\.codex\actions\harness-check.ps1` after the baseline/default/dtype changes - pass. It fell back from missing `pwsh` to Windows PowerShell and all executed checks passed: Python import smoke, default pytest (`90 passed`), CLI smoke, Ruff, doc freshness, frontend type-check, and Rust `cargo check`. Doc freshness printed a stale `PROJECT_MAP.md` warning but exited zero.
- `.\.venv\Scripts\python.exe scripts\check_docs.py` after updating this handoff - pass with a stale `PROJECT_MAP.md` warning.
- `.\.venv\Scripts\python.exe scripts\update_context.py` - pass; refreshed `docs\agent\PROJECT_MAP.md`.
- `.\.venv\Scripts\python.exe scripts\check_docs.py` after refreshing the project map - pass; `docs check OK`.
- Final `.\.codex\actions\harness-check.ps1` after the handoff and project-map refresh - pass. It fell back from missing `pwsh` to Windows PowerShell and all executed checks passed: Python import smoke, default pytest (`90 passed`), CLI smoke, Ruff, doc freshness, frontend type-check, and Rust `cargo check`.
- Final `git diff --check` - pass; only line-ending warnings were printed.
- `pnpm exec tsc --noEmit` from `srtforge-studio/` after adding Max CUDA
  Studio mode and quiet UI props - pass.
- `cargo fmt` from `srtforge-studio/src-tauri/` after adding Studio debug
  runtime metadata and live-event throttling - pass.
- `cargo test --lib` from `srtforge-studio/src-tauri/` after adding Max CUDA
  Rust bridge tests - pass; `9 passed`.
- `cargo check` from `srtforge-studio/src-tauri/` after the Tauri/Rust changes -
  pass.
- `.\scripts\rebuild_studio_sidecar.ps1` - pass. It rebuilt the PyInstaller
  sidecar, copied the suffixed Tauri sidecar, and the suffixed `gpu-smoke`
  passed with Torch CUDA `12.8`, ORT `CUDAExecutionProvider`, CUDA Python
  runtime bindings available, and NeMo conditional-node support true. Rebuilt
  suffixed sidecar LastWriteTime was `05/02/2026 09:42:38`.
- `.\.venv\Scripts\python.exe scripts\update_context.py` after the Max CUDA
  source/doc edits - pass; refreshed `docs\agent\PROJECT_MAP.md`.
- `git diff --check` after the Max CUDA edits - pass; only line-ending
  warnings were printed.
- `.\.venv\Scripts\python.exe scripts\check_docs.py` after refreshing the
  project map - pass; `docs check OK`.
- `git branch --show-current` before push - pass;
  `codex-windows-agent-layer`, not `main`.
- Pre-push remote/config token scan - pass; no token-like remote or branch
  config entries found.
- `git diff --cached --check` before commit - pass.
- `git commit -m "studio: restore cuda throughput"` - pass; created
  `dded575`.
- `git push -u origin codex-windows-agent-layer` - pass; pushed
  `068e909..dded575` to GitHub and set upstream tracking.
- Post-push `git remote -v` - pass; `origin` remains
  `https://github.com/StiensGate928/Srtforge.git`.
- Post-push remote/config token scan - pass; no token-like remote or branch
  config entries found.

## Skipped checks and why

- Clean-machine GPU smoke was not run. The current venv is fixed and smoke was
  run without CUDA Toolkit paths, but this is still the developer machine.
- Full UI-run Studio benchmark was not run after the Parakeet/Lhotse-off fix.
  The rebuilt sidecar direct media path passed with the same dev project-root
  environment that Studio provides and with CUDA Toolkit paths removed from
  `PATH`.
- This agent did not independently rerun the heavy `D:\New Saga.mkv` Studio
  benchmark after the final log/menu cleanup in this session. The user
  confirmed the Max CUDA Studio path matched old performance before these
  low-risk UI/log cleanup edits.

## Decisions made

- CUDA 12.8 is the default packaged GPU stack. CUDA 13 remains experimental
  because local ORT reports a CUDA 12.8 build while the current venv's Torch is
  CUDA 13.0.
- End users should need only a compatible NVIDIA driver, not CUDA Toolkit or
  `nvcc`. `install.ps1` now checks the NVIDIA driver floor instead of installing
  CUDA Toolkit.
- GPU installs now fail fast if `onnxruntime-gpu==1.25.1` cannot be installed
  instead of silently installing CPU `onnxruntime`.
- Editable installs now use `pip install --no-deps -e .` because requirements
  are installed first; otherwise Faster-Whisper metadata pulls CPU
  `onnxruntime` back into GPU installs.
- `pyarrow` is now `>=24,<25` because NeMo 2.7.3's `datasets` dependency
  requires `pyarrow>=21`, making the previous `<21` repo pin impossible for the
  selected stack.
- NeMo's `check_cuda_python_cuda_graphs_conditional_nodes_supported()` returns
  `None` on success and raises on failure. The smoke helper now treats `None` as
  supported instead of a disabled fast path.
- The sidecar CUDA Python root cause was frozen DLL lookup, not the CUDA stack:
  `cuda\bindings\runtime.cp312-win_amd64.pyd` was bundled, but its hashed MSVC
  dependency from `cuda_bindings.libs` was placed under the frozen `cuda\`
  directory and was not on Windows' DLL search path. The PyInstaller runtime
  hook now adds `_MEIPASS\cuda`, `_MEIPASS\torch\lib`, and `_MEIPASS\torchvision`
  with `os.add_dll_directory()` before importing `_cuda_bindings_redirector`.
- `srtforge gpu-smoke` is intentionally strict: Torch CUDA, ORT CUDA build,
  ORT providers, CUDA Python `cuda.__version__`, CUDA Python runtime bindings,
  and NeMo CUDA-graph support must all line up.
- Studio now starts the sidecar as `worker --no-preload` so it does not load a
  hidden ASR model from root `srtforge.config`; job settings should decide what
  gets loaded.
- ORT CUDA DLL preload happens before audio-separation provider checks, and
  separation-to-ASR now runs best-effort Python/CUDA cache cleanup before
  loading Parakeet.
- Parakeet v2 remains a single whole-episode pass. Do not split anime episodes
  into ASR chunks unless the user explicitly revisits that tradeoff.
- For Parakeet v2 on NeMo 2.7.3, Studio/worker should bypass NeMo's Lhotse
  transcribe loader by passing `use_lhotse=False` and suppressing verbose
  transcribe/tqdm output. The old Lhotse path was the cause of the
  `Transcribing: 1it [04:35]` Studio behavior.
- Live Studio terminal output should not add synthetic progress summaries.
  Preserve the original raw tqdm-style terminal lines, throttle normal
  structured `progress` events, and avoid adding extra log rows beyond what the
  worker already emits.
- For performance work, preserve current Parakeet output behavior first. The
  selected quality guard is output parity against the current Parakeet baseline;
  do not change chunking, model, precision, or decoding settings just to raise
  GPU utilization.
- Minimum WER remains the top priority. The known-good Parakeet long-audio
  attention window is `[1280, 1280]`, so the current repo now restores that
  default across Python settings, persistent config, PySide defaults, and Studio
  defaults instead of optimizing downward.
- Legacy top-level `parakeet:` YAML remains supported as input config. Current
  code migrates it into the newer `whisper:` ASR settings block so old
  persistent configs do not silently lose attention/dtype fields.
- FV4 quality settings were not changed during separation isolation. The
  slower reproduced runs also happened in the old repo, so the next separation
  action is clean benchmark hygiene/runtime-state isolation, not reducing model
  quality or overlap.
- Max CUDA Studio mode is the default while a GPU job is active. Foreground UI
  smoothness is secondary to CUDA throughput for FV4/Parakeet work, so the
  visible React UI can throttle normal live log/progress updates and use static
  waveform surfaces without changing pipeline quality.
- Studio disables WebView2 GPU acceleration via Tauri
  `additionalBrowserArgs` and uses an opaque window to reduce WebView/DWM
  competition with CUDA. If this causes a launch or usability issue, keep the
  opaque/quiet UI path and revisit only the WebView flag; do not reduce
  FV4/ASR quality settings.
- The confirmed old-performance match means the Studio regression should be
  documented as UI/runtime pressure, not an ASR/FV4 quality issue. Future UI
  features must prove they do not reduce focused-window CUDA throughput before
  becoming active during GPU stages.

## Known blockers

- `rg.exe` still returns access denied in this environment; use PowerShell
  `Select-String` fallback.
- `pip check` is not clean for the GPU venv because Faster-Whisper declares
  `onnxruntime` by distribution name. Runtime smoke is clean with
  `onnxruntime-gpu`; installing CPU ORT would violate the GPU stack decision.
- A Studio debug worker can remain alive and keep CUDA memory allocated after a
  run. Before CLI benchmarking, inspect `Get-Process srtforge_worker` and stop
  stale workers if the user is not actively running Studio.
- In a Tauri dev run, two `srtforge_worker.exe` PIDs may appear because the
  PyInstaller one-file bootloader launches the extracted child process. Verify
  actual contention with `nvidia-smi`/process memory instead of assuming both
  PIDs are doing compute.
- The old `C:\Srtforge` repo is still fast through the user's `srtforge-gui`
  workflow. The prior shell recheck from this worktree is contaminated and
  should not be used as evidence that the old repo slowed down.
- WebView2 `--disable-gpu` through Tauri `additionalBrowserArgs` is now part of
  the validated focused-Studio performance path. Revisit it only if a future
  WebView2 update creates a launch/usability problem.

## Next recommended action

Keep Max CUDA mode as the active-job default. When adding back Studio design
polish, add it in idle-only/static form first and compare focused-window FV4
throughput before enabling it during GPU stages. For the latest cleanup, verify
History output menus open upward near the footer, live logs show the original
raw tqdm progress lines without synthetic `FV4 progress:` rows, and saved debug
logs remain complete.
