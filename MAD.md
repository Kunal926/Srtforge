# Srtforge – Master Architecture Document (“The Vision”)
Status: In Progress  
Last updated: 2025-12-01  
Owner: <TBD_OWNER>

---

## 1. Master Architecture (“The Vision”)

### 1.1 High‑Level Summary
- Srtforge is an **offline Windows‑first subtitle generation pipeline** that takes TV/film media files and produces high‑quality English `.srt` subtitles using **FV4 MelBand Roformer** separation and **NVIDIA Parakeet‑TDT‑0.6B‑V2** ASR.
- It is designed for **Windows 11** with a **CLI**, a **Win11‑style GUI**, and **Sonarr** post‑processing integration, all running fully locally with no cloud calls.
- The system enforces (as far as practical) **Netflix‑style timing and formatting rules** under tight time constraints (~20–30 min episodes in under ~4 minutes on a 3070 Ti‑class GPU).
- Outputs are primarily `.srt` files written either to a configured output directory or next to the media; **embedding/burn‑in** is optional and never done by default.
- Privacy is a hard constraint: **all processing happens locally**, models are stored and executed on the user’s machine, and no media/metadata is uploaded anywhere.

### 1.2 Vision vs Current State

- **Vision (ideal end‑state):**
  - Turn a Windows 11 machine into a **one‑click, fully offline subtitle factory**:
    - User or Sonarr drops new episodes in a watch folder.
    - Srtforge automatically generates **Netflix‑quality English subtitles** with robust handling for edge‑cases (mixed‑language, noisy audio, music‑only sections) under a predictable latency budget.
    - A polished **Win11 GUI (“Srtforge Studio”)** exposes queue management, ETA estimation, rich logs, and safe tools for soft embedding/burning subtitles, including backup and restore of original subtitles/tracks.
    - A bundled installer ships **ffmpeg, MKVToolNix, Parakeet, FV4, and all CUDA/NeMo dependencies** in a self‑contained, well‑tested package.
    - Config is **single‑source‑of‑truth**: a YAML file mirrored by the GUI “Options” dialog, with a clear reset‑to‑defaults path and no hidden state.
    - Audio separation failures are **non‑fatal**: if FV4 breaks, the system transparently falls back to mixed audio with sensible logging and JSON notifications.
    - The system remains responsive and robust for **very long media (up to ~24 hours)**, including streaming/segmented processing modes.

- **Current State (as of this MAD):**
  - Fully offline pipeline implemented using:
    - **FV4 MelBand Roformer** separator (via `audio-separator`) and **NVIDIA Parakeet‑TDT‑0.6B‑V2** ASR via NeMo.
    - A structured `Pipeline` / `PipelineConfig` abstraction with FFmpeg‑based extraction and preprocessing.
    - Netflix‑style SRT post‑processing rules (line length, CPS, gaps, merges/splits) enforced in a single pass where feasible.
  - **CLI** implemented via Typer with three subcommands:
    - `run` – single‑file processing.
    - `series` – batch processing for a directory.
    - `sonarr-hook` – Sonarr custom script integration (plus a dedicated `srtforge-sonarr` entry point).
  - **GUI** implemented using **PySide6**, providing:
    - A queue of media items, per‑item status and ETA, and streaming of CLI logs into the UI.
    - Controls for temp/output directories, CPU/GPU selection, separation/backend toggles, and basic embed/burn options.
  - **Config model** backed by a YAML file (`config.yaml`) in the package root, overridable via `SRTFORGE_CONFIG` and session‑level GUI overrides.
  - Officially **Windows 11‑only** support, with an expectation that other OSes *might* work but are not tested or guaranteed.

- **Major gaps between Vision and Current State:**
  - **Installer bundling gaps:**
    - No fully official, single‑bundle distribution that ships FFmpeg, MKVToolNix, Parakeet `.nemo`, and FV4 checkpoint/config as first‑class artifacts; some pieces rely on external download links.
  - **Separation robustness:**
    - FV4 failures currently behave like “skip/failed pipeline” rather than being treated as “skip separation and continue with mixed audio”; treating these as non‑fatal is a **planned** enhancement.
  - **Queue & scaling model:**
    - GUI queue is single‑process, single‑pipeline; no concurrent pipelines or cross‑session job management.
  - **JSON event surface:**
    - Only a single `srt_written` event exists; no structured progress/error events yet.
  - **UX / safety tooling:**
    - Embed/burn features exist but still need more “safety rails” (clear previews, backups, restore options) to fully match the vision.
  - **Advanced language scenarios:**
    - English‑only ASR backend (Parakeet) is supported; mixed‑language or dual‑subtitle scenarios remain out‑of‑scope for now.

---

## 2. Goals & Non‑Goals

### 2.1 Core Design Goals

- **G1: Fully offline, privacy‑preserving pipeline.**  
  No cloud calls, no telemetry, and no external dependencies at runtime beyond what’s installed/bundled locally.

- **G2: Netflix‑grade subtitles under tight latency budgets.**  
  For a ~20–30 minute episode on an RTX 3070 Ti‑class GPU, target wall‑clock time is **< 4 minutes**, with reasonable behavior up to ~24‑hour media.

- **G3: Windows 11 first‑class support.**  
  The system is designed, tested, and supported officially **only on Windows 11**; other platforms are considered “nice when they work” but not a support target.

- **G4: Simple, script‑friendly CLI contract.**  
  A small number of commands (`run`, `series`, `sonarr-hook`) with **predictable exit codes** (0, 1, 2) and a single JSON event type `srt_written` for automation.

- **G5: Single‑source configuration with GUI parity.**  
  A YAML config that is mirrored in the GUI Options dialog; GUI changes write back to YAML, and a **reset‑to‑defaults** path always exists.

- **G6: Robustness over perfection.**  
  Prefer “skip with explanation and continue” over crashes. When something fails (no English audio, missing model, broken FV4), the system should degrade gracefully.

- **G7: Netflix guidelines as “north star”, not an infinite rabbit hole.**  
  Enforce Netflix‑style timing / reading speed as far as feasible within compute and time budgets (≈30 seconds of extra processing per file at most).

### 2.2 Explicit Non‑Goals

- **NG1: Multi‑language ASR.**  
  Only **English** is supported; multi‑language/multi‑track ASR is out‑of‑scope for this codebase.

- **NG2: Cross‑platform support.**  
  Linux/macOS support is not an official goal; things may work but are not tested or guaranteed.

- **NG3: Cloud or client‑server deployments.**  
  No plans to run Srtforge as a SaaS or remote API. The system is **local‑only by design**.

- **NG4: Realtime/live captioning.**  
  The pipeline is designed for offline media files, not live audio streams or ultra‑low‑latency scenarios.

- **NG5: High‑availability / multi‑node scaling.**  
  No clustering or distributed job scheduling; a “big but single” Windows 11 box is the target deployment.

- **NG6: Arbitrary subtitle editing suite.**  
  Srtforge cares about **generating good SRTs**, not about being a full subtitle editor GUI.

---

## 3. High‑Level System Overview

### 3.1 Context & Inputs/Outputs

- **Primary inputs:**
  - Local media files (typically `.mkv`, but **any format FFmpeg can decode** is effectively supported, since audio is extracted to WAV).
  - Configuration from `config.yaml` (plus optional overrides via `SRTFORGE_CONFIG` and GUI session options).
  - Sonarr post‑processing environment variables (e.g. `SONARR_EVENTTYPE`, `SONARR_EPISODEFILE_PATH`).

- **Primary outputs:**
  - `.srt` subtitle files:
    - Typically placed next to the media (`<stem>.srt`) or into a configured `output_dir`.
    - “Sidecar” diagnostic SRTs (`.diag.*.srt`) stored in per‑run temp directories, not next to the media.
  - Optional **embedded subtitles** or **burned‑in video files** when user explicitly enables those actions in the GUI.
  - Logs:
    - Human‑oriented Rich console logs (CLI) and GUI console pane.
    - A single structured JSON event: `{"event": "srt_written", "path": "<abs-path>"}` on successful runs.

- **External systems / services:**
  - **Sonarr** (Custom Script hook for Download/Upgrade events).
  - External package/index services only during installation:
    - Python package index (for NeMo, PyTorch, audio‑separator, etc.).
    - Hugging Face / GitHub hosting FV4 and Parakeet checkpoints (installer downloads `voc_fv4.ckpt`, `voc_gabox.yaml`, and `parakeet_tdt_0.6b_v2.nemo`).citeturn11view0turn13view0
  - Tools invoked at runtime (ideally bundled in future vision):
    - **FFmpeg** for probing, extraction, filtering, and embedding/burn‑in.
    - **MKVToolNix (mkvmerge)** for soft‑embedding subtitles in MKV containers.

### 3.2 Textual Diagram(s)

```text
[User / Sonarr]
  ├─(CLI)─> [srtforge run / series]
  │            |
  │            v
  │        [Pipeline]
  │          1) ffprobe/ffmpeg: find English audio, extract WAV
  │          2) Optional FV4 separation (GPU/CPU)
  │          3) ffmpeg filter_chain (HPF/LPF/soxr to 16 kHz mono)
  │          4) Parakeet ASR via NeMo
  │          5) Netflix-style SRT post-processing
  │          6) Write .srt + diagnostics to output/temp dirs
  │          7) Emit JSON: {"event": "srt_written", "path": ...}
  │
  ├─(GUI)─> [PySide6 "Srtforge Studio"]
  │            ├─> Queue management (per-item run)
  │            ├─> Spawns CLI subprocesses (srtforge run)
  │            ├─> Parses stdout for JSON events + logs
  │            └─> Exposes settings/paths/embedding/burn UI
  │
  └─(Sonarr)─> [srtforge sonarr-hook / srtforge-sonarr]
               ├─> Read SONARR_EVENTTYPE + EPISODEFILE_PATH
               ├─> Normalize event → {download|upgrade}
               ├─> Build PipelineConfig(media_path=episode)
               └─> Call Pipeline; always exit 0
```

- **Happy path (single file):**
  1. CLI/GUI receives media path.
  2. Pipeline probes and extracts English audio to WAV.
  3. Optional FV4 separation runs (GPU preferred, fallback to CPU; on failure currently leads to `skipped=True`, but the **vision** is to continue with mixed audio).
  4. Filtered audio goes into Parakeet/NeMo; transcripts and timestamps are converted to SRT segments respecting Netflix heuristics.
  5. `.srt` written to output path; `srt_written` JSON emitted; GUI/automation consumes that event.

- **Error paths (high level):**
  - **Missing media / no English stream / FV4 failure / NeMo error:**
    - Pipeline returns `skipped=True` with a reason string; CLI uses **exit code 2** for `run` and omits JSON events for failed files.
  - **`series` command with no matching files:**
    - Emits log line and exits with **code 1**.
  - **Sonarr hook failures:**
    - Exceptions within pipeline are caught; hook always exits **code 0** to avoid breaking Sonarr’s upgrade/import pipeline.
  - **Config / path / model issues:**
    - Typically result in `skipped=True` + logs; the vision is to refine error messages and GUI surfacing over time.

---

## 4. Component Architecture

### 4.1 `srtforge.pipeline` – Core Processing Pipeline

**Responsibility & Boundaries**

- **Responsible for:**
  - Orchestrating the full media → SRT pipeline for a single file.
  - Managing temp directories, FFmpeg invocations, optional FV4 separation, and Parakeet ASR calls.
  - Applying Netflix‑style SRT post‑processing rules (segment splits/merges, gaps, timing caps) and writing `.srt` plus diagnostics.
  - Returning a structured `PipelineResult` indicating success, skip/failure, and output paths.
- **Explicitly does not:**
  - Manage multiple files (that’s `series` or the GUI queue).
  - Handle Sonarr event wiring or GUI UX.
  - Implement configuration loading; it consumes a `PipelineConfig` already resolved from global/app settings.

**Key Public APIs (Python)**

- `class PipelineConfig`:  
  Dataclass describing a single run:
  - `media_path: Path` – input media file (must exist for non‑skipped runs).
  - `output_path: Optional[Path]` – explicit SRT path (otherwise inferred).
  - `prefer_gpu: bool` – GPU preference for Parakeet.
  - `separation_prefer_gpu: bool` – GPU preference for FV4.
  - `settings: AppSettings` – global settings snapshot (paths, ffmpeg, separation, parakeet).

- `class PipelineResult`:
  - `output_path: Optional[Path]`
  - `skipped: bool`
  - `reason: Optional[str]`
  - Potentially additional metadata (timing, diagnostics paths, etc.).

- `def run_pipeline(config: PipelineConfig) -> PipelineResult`:
  - Main single‑file orchestrator; wraps errors into `PipelineResult(skipped=True, reason=<message>)` instead of raising.

- Internal helpers (representative):
  - `_probe_audio_streams(...)` – uses `ffprobe` to find English (or center) audio track; respects `prefer_center` and `allow_untagged_english` flags.
  - `_extract_audio_to_wav(...)` – uses FFmpeg to write WAV at `sep_hz` (e.g. 44.1kHz) or another configured sample rate.
  - `_run_separation(...)` – invokes FV4 via `audio-separator`; returns vocal stem path or raises/flags skip on failure.
  - `_prepare_for_parakeet(...)` – applies FFmpeg `filter_chain` (HPF 60Hz, LPF 10kHz, soxr resample to 16 kHz mono float).
  - `_run_parakeet_asr(...)` – calls NeMo’s Parakeet‑TDT model via `parakeet_engine` wrapper.
  - `_write_srt_and_diags(...)` – writes `.srt` plus `.diag.*` sidecars into run temp directory, moving final `.srt` into the configured output location.

**Dependencies**

- Internal:
  - `srtforge.config` / `settings` for `AppSettings`.
  - `srtforge.ffmpeg` for probe/extract/filter/embedding/burn‑in commands.
  - `srtforge.parakeet_engine` for NeMo ASR integration.
  - `srtforge.srt_utils` for SRT segment shaping and Netflix rule enforcement.
  - `srtforge.logging` for structured run IDs, timing, and Rich console integration.
- External:
  - FFmpeg binary (path/discovery influenced by settings and environment).
  - `audio-separator` (FV4 backend config + checkpoint).
  - NVIDIA NeMo + CUDA stack for Parakeet‑TDT.
  - OS filesystem and temp directories.

**Configuration**

- Consumes `AppSettings` (see §6) plus run‑level overrides:
  - `paths.temp_dir`, `paths.output_dir` for temp and final output.
  - `ffmpeg.filter_chain`, `ffmpeg.prefer_center` for audio processing.
  - `separation.*` for backend, sample rate, GPU preference, and English language handling.
  - `parakeet.*` for GPU/CPU behavior and float format.

**Operational Notes**

- **Performance:**
  - Single‑file pipeline; throughput is “one at a time” per process.
  - GPU strongly recommended (e.g. 3070 Ti, 10GB VRAM) for target runtime (<4 minutes for 20–30 min episodes). Longer content up to ~24h is allowed but naturally slower.
- **Scaling:**
  - One pipeline per process; GUI queue enforces single pipeline at any given time.
  - No cross‑process coordination or job distribution.
- **Failure modes:**
  - Missing media, no English audio stream, FFmpeg failure, FV4 errors, NeMo exceptions.
  - All are caught and reified as `PipelineResult(skipped=True, reason=...)`; CLI converts this to **exit code 2** for `run`.
  - Diagnostics and temp directories are cleaned up via `cleanup_run_directories` and run‑scoped deletion, with a 24‑hour retention policy for old runs.

### 4.2 `srtforge.cli` – Typer CLI Entry Point

**Responsibility & Boundaries**

- **Responsible for:**
  - Exposing user‑facing CLI commands (`run`, `series`, `sonarr-hook`) via Typer.
  - Translating CLI arguments into `PipelineConfig` instances.
  - Emitting human‑readable logs and the `srt_written` JSON events.
  - Managing process‑level exit codes.
- **Explicitly does not:**
  - Implement the actual pipeline logic (delegated to `run_pipeline`).
  - Implement Sonarr env handling (`sonarr-hook` delegates to `sonarr_hook.main()`).
  - Handle GUI concerns beyond being invoked as a subprocess.

**Key Public APIs (CLI)**

- `srtforge run [OPTIONS] MEDIA`
  - `MEDIA` (positional, must exist).
  - `--output, -o PATH`: optional explicit SRT path.
  - `--cpu`: force CPU for both ASR and separation.
  - Behavior:
    - Builds `PipelineConfig` with `media_path=MEDIA`, `output_path=...`, `prefer_gpu=not --cpu`, `separation_prefer_gpu=not --cpu`.
    - Calls `run_pipeline(config)`.
    - On success (`skipped=False`, `result.output_path` set):
      - Logs `SRT written to <path>`.
      - Emits JSON: `{"event": "srt_written", "path": "<abs-path>"}` on stdout (one line).
    - On skip/failure:
      - Emits logs only; **no JSON** event.

- `srtforge series [OPTIONS] DIRECTORY`
  - `DIRECTORY` (must exist and be a directory).
  - `--glob PATTERN` (default `**/*.mkv`).
  - `--cpu` (same semantics as `run`).
  - Behavior:
    - Glob under `DIRECTORY` for media files.
    - If no files match: log message & exit **code 1**.
    - For each file:
      - Print a Rich rule with the path.
      - Build `PipelineConfig` as above (no explicit `output_path`).
      - Call `run_pipeline`.
      - On success: emit `srt_written` JSON line to stdout.
      - On skip/failure: log only; no JSON for that file.

- `srtforge sonarr-hook`
  - Simply delegates to `srtforge.sonarr_hook.main()`.

**Dependencies**

- Internal: `srtforge.pipeline`, `srtforge.sonarr_hook`, `srtforge.config`, `srtforge.logging`.
- External: Depends on Python/Typer, FFmpeg, NeMo, FV4 through the pipeline.

**Configuration**

- Inherits global settings from `config.yaml` via `AppSettings` at process startup.
- CLI flags override only **run‑level aspects**:
  - `--cpu` toggles GPU preferences.
  - `--output` changes output target path for that run only.

**Operational Notes**

- **Exit code semantics (excluding Typer parser errors):**
  - `0` – At least one successful SRT for `run`/`series` (and for Sonarr hook, “script completed” semantics).
  - `1` – `series` found no matching files.
  - `2` – For `run`, the pipeline reported `skipped=True` (logical “did not produce SRT” without treating this as a crash).
- **JSON contract:**
  - Only emits `{"event": "srt_written", "path": "<absolute-or-normalised-path>"}`.
  - Integrators should parse stdout line‑by‑line and filter for `event == "srt_written"`.

### 4.3 `srtforge.sonarr_hook` – Sonarr Custom Script Integration

**Responsibility & Boundaries**

- **Responsible for:**
  - Reading Sonarr’s custom script environment variables.
  - Determining whether to trigger the pipeline based on event type.
  - Invoking `run_pipeline` for a single episode file without disrupting Sonarr’s workflow.
- **Explicitly does not:**
  - Emit JSON events.
  - Modify original media or existing subtitles.
  - Fail Sonarr jobs via non‑zero exit codes.

**Key Behavior**

- Reads event type from (case‑insensitive):
  - `SONARR_EVENTTYPE`, `sonarr_eventtype`.
- Normalizes via:
  - `TRIGGER_EVENTS = {"download", "upgrade"}`.
  - `EVENT_ALIASES = {"onimport": "download", "manualimport": "download", "onupgrade": "upgrade"}`.
- Reads episode path from (case‑insensitive):
  - `SONARR_EPISODEFILE_PATH`, `sonarr_episodefile_path`,
  - `SONARR_EPISODE_FILE_PATH`, `sonarr_episode_file_path`.
- Behavior:
  - If event type is not in the trigger set → log and **return** (no pipeline run).
  - If event is recognized but episode path missing → log `EpisodeFile.Path missing in environment` and return.
  - Otherwise:
    - `config = PipelineConfig(media_path=episode_path)`.
    - Call `run_pipeline(config)`.
- Exit codes:
  - Always returns **0** (no `sys.exit` / `typer.Exit`), even if pipeline failed/raised internally; failures are logged but **never break Sonarr’s import/upgrade**.

### 4.4 `srtforge.gui_app` – PySide6 Windows 11 GUI (“Srtforge Studio”)

**Responsibility & Boundaries**

- **Responsible for:**
  - Providing a Win11‑style GUI for configuring Srtforge, managing a queue, and visualizing logs and progress.
  - Spawning CLI subprocesses (`srtforge run`) and streaming their stdout/stderr into a console view.
  - Managing session‑level configuration overrides and writing them back to YAML.
  - Exposing soft embedding/burn‑in capabilities in a safe, explicit way.
- **Explicitly does not:**
  - Implement the pipeline itself (delegates to CLI).
  - Modify YAML schema (it operates within the same config model as the CLI).
  - Run multiple pipelines concurrently.

**Key Components / APIs**

- `TranscriptionWorker` / queue model:
  - Holds a queue of media items with status and ETA estimates.
  - Launches `srtforge run` subprocesses sequentially.
  - Parses stdout for `srt_written` JSON events to update item status and final SRT paths.
  - Tails logs into the GUI console and provides per‑run details.

- Options / settings dialog:
  - Binds to the same YAML schema as `config.yaml` (`AppSettings`).
  - Exposes controls for temp/output dirs, CPU/GPU, separation backend and toggles, FFmpeg filter chain, etc.
  - Writes effective settings to a session‑level YAML file that the CLI can read via `SRTFORGE_CONFIG`.
  - Provides a **“Reset to defaults”** option that restores built‑in defaults from code.

- Embedding / burn‑in controls:
  - Soft embedding:
    - Uses FFmpeg or MKVToolNix (mkvmerge) to add SRT as a subtitle track, respecting the configured language code (`eng`) and default/forced flags.
    - Never overwrites existing subtitle tracks unless user explicitly opts into an overwrite mode.
  - Burn‑in:
    - Uses FFmpeg filters to render subtitles into a new video file (target path chosen explicitly by user).
  - Both are **opt‑in** actions and do not run automatically after every SRT generation.

**Dependencies**

- Internal: `srtforge.cli` (subprocess invocation), `settings`/`config`, `srtforge.win11_backdrop` and `.qss` for visual styling.
- External: PySide6, FFmpeg, MKVToolNix, OS process management.

**Operational Notes**

- **Queue model:**
  - Single pipeline at a time; jobs are processed strictly in order.
  - Cancelling a job maps to terminating the underlying CLI process when possible.
- **Config coherence:**
  - GUI reads/writes YAML using the same structure as `config.yaml`; this is a hard requirement so GUI and CLI never disagree.
  - Session‑level overrides apply only for the running GUI session and can be reset to defaults.
- **Error handling:**
  - Exit code 2 from `srtforge run` is surfaced as a “skipped” state with the reason text where available.
  - Other non‑zero codes are treated as “hard failures” and highlighted distinctly in the UI.

### 4.5 `srtforge.config` / `settings` – Configuration Loader

**Responsibility & Boundaries**

- **Responsible for:**
  - Loading and merging configuration from `config.yaml` and environment variables into `AppSettings` dataclasses.
  - Exposing a singleton or module‑level settings object for the rest of the app.
- **Explicitly does not:**
  - Persist per‑run state.
  - Implement CLI args parsing or GUI layouts.

**Key Structures**

Dataclasses (current shape):

- `PathsSettings`:
  - `temp_dir: Optional[Path]`
  - `output_dir: Optional[Path]`

- `FFmpegSettings`:
  - `prefer_center: bool`
  - `filter_chain: str`

- `FV4Settings`:
  - `cfg: Path` (e.g. `./models/voc_gabox.yaml`).
  - `ckpt: Path` (e.g. `./models/voc_fv4.ckpt`).

- `SeparationSettings`:
  - `backend: str` (e.g. `"fv4"` or `"none"`).
  - `sep_hz: int` (sample rate for extraction; YAML default `44100`).
  - `prefer_center: bool`
  - `prefer_gpu: bool`
  - `allow_untagged_english: bool` (fallback to first audio stream when no `eng` tag).
  - `fv4: FV4Settings`.

- `ParakeetSettings`:
  - `force_float32: bool` (force fp32 for GPU stability).
  - `prefer_gpu: bool`.

- `AppSettings`:
  - `paths: PathsSettings`
  - `ffmpeg: FFmpegSettings`
  - `separation: SeparationSettings`
  - `parakeet: ParakeetSettings`

**Loading Logic**

- Default location:
  - `PACKAGE_ROOT / "config.yaml"` (inside the installed package).
- Override via:
  - `SRTFORGE_CONFIG=/path/to/config.yaml` (environment variable).
- Merge behavior:
  - YAML is merged recursively into default dataclasses.
  - Any subset of fields may be overridden; others remain at code defaults.
  - Relative paths in YAML are resolved relative to the **project root / package root**.

### 4.6 `srtforge.ffmpeg` – FFmpeg/MKVToolNix Helpers

**Responsibility & Boundaries**

- **Responsible for:**
  - Wrapping FFmpeg invocations for:
    - `ffprobe` (stream discovery).
    - Audio extraction to WAV (with configurable sample rate).
    - Preprocessing via `filter_chain`.
    - Soft embedding and burn‑in where used by GUI.
  - Optionally invoking `mkvmerge` for MKV subtitle track manipulation.
- **Explicitly does not:**
  - Contain business logic about language selection beyond `eng` + default/forced flags.
  - Manage configuration; it consumes `FFmpegSettings` and other runtime parameters.

**Key Functions**

- `probe_streams(path) -> ProbeResult`
- `extract_audio(input_path, output_wav, sample_rate, prefer_center, ...)`
- `apply_filter_chain(input_wav, output_wav, filter_chain)`
- `embed_subtitle_ffmpeg(media_path, srt_path, language="eng", default_flag=False, forced_flag=False, overwrite=False)`
- `embed_subtitle_mkvmerge(...)`
- `burn_subtitle_ffmpeg(media_path, srt_path, output_path, ...)`

**Dependencies**

- External binaries: `ffmpeg`, `ffprobe`, `mkvmerge`.
- Internal: `settings`, `logging` for command logging and timing.

**Operational Notes**

- Soft embedding aims to be **non‑destructive**:
  - Default behavior is to add a new `eng` subtitle track without touching existing subtitles.
  - Overwrite modes (e.g. replace an existing `eng` forced/default track) require explicit user consent.
- Burn‑in outputs are treated as **new media files**; the original video is left untouched.

### 4.7 `srtforge.parakeet_engine` – NeMo / Parakeet Integration

**Responsibility & Boundaries**

- **Responsible for:**
  - Loading the Parakeet‑TDT‑0.6B‑V2 model from local `.nemo` checkpoint.
  - Managing GPU/CPU selection, CUDA graphs, and NeMo runtime flags.
  - Exposing a single “transcribe to SRT segments” style API to the pipeline.

**Key Functions**

- `load_parakeet_model(settings: ParakeetSettings, device: str) -> ModelHandle`
- `parakeet_to_srt(audio_path, settings, long_audio: bool = True) -> List[SrtSegment]`
  - Handles NeMo’s `transcribe` API variations (e.g. `timestamps=True` vs `timestamp_type="word"`).
  - Applies long‑audio heuristics (chunking / streaming options) to keep memory usage manageable.

**Dependencies**

- Internal: `_nemo_compat` for Megatron stub and CUDA runtime checks.
- External: `nemo_toolkit[asr]`, PyTorch, CUDA bindings.

**Operational Notes**

- GPU strongly preferred; CPU mode is supported but significantly slower.
- When CUDA/NeMo misconfigurations occur, `_nemo_compat.ensure_cuda_python_available()` and related logic try to produce **clear, early error messages** instead of obscure runtime traces.

### 4.8 `_nemo_compat` – NeMo Compatibility Helpers

**Responsibility & Boundaries**

- **Responsible for:**
  - Installing a minimal `megatron.core.num_microbatches_calculator` stub to silence noisy NeMo warnings when Megatron is absent.
  - Ensuring `cuda-python` bindings are available when GPU mode is requested; raising clear errors otherwise.
- **Explicitly does not:**
  - Replace NeMo’s internal scheduling logic; only provides the minimal shim to keep inference happy.
  - Manage CUDA driver/toolkit installation.

**Key Functions**

- `install_megatron_microbatch_stub()`
- `ensure_cuda_python_available()`

**Dependencies**

- External: `cuda-python`, OS environment.

---

## 5. Data & File Flows

### 5.1 Storage Locations

- **Media input:**
  - User‑supplied media files, typically under a Sonarr library or arbitrary directories chosen in the GUI/CLI.
- **Temp directories:**
  - Root temp directory:
    - Configured via `paths.temp_dir` (default: `./tmp` in repo config).citeturn11view0turn0file30
  - Per‑run directories:
    - Namespaced by run ID (e.g. `tmp/run-<uuid>`), storing:
      - Extracted WAVs (original and separated).
      - Intermediate processed audio.
      - Diagnostic `.diag.*.srt` files.
      - Logs / per‑run metadata (`run.json`, etc.).
- **Output directories:**
  - `paths.output_dir` (default: `./output`) when set:
    - SRT path: `<output_dir>/<stem>.srt`.
  - If `output_dir` is not set:
    - SRT is written next to the media file with suffix `.srt` (`DEFAULT_OUTPUT_SUFFIX = ".srt"`).
- **Models:**
  - Parakeet `.nemo` checkpoint, FV4 config and checkpoint under `./models` by default, downloadable from Hugging Face / GitHub.citeturn11view0turn13view0

### 5.2 Lifecycle & Cleanup

- **Temp naming:**
  - Run directories under `temp_dir` with unique IDs (`run-<timestamp>-<uuid>` or similar).
- **Cleanup on success/error:**
  - Within the pipeline:
    - Per‑run directory is retained at least for the lifetime of the process for diagnostics.
  - Background cleanup:
    - `cleanup_run_directories` scans `temp_dir` and removes run directories older than **24 hours** by default.
- **Diagnostics:**
  - `.diag.*.srt` are written into run temp directories, not next to media, to avoid clutter.
  - Vision: optional “keep last N runs” policy and one‑click “open diagnostics folder” in the GUI.

### 5.3 File / Object Schemas (High‑Level)

- **SRT files (`.srt`):**
  - Standard SRT numbering and `HH:MM:SS,mmm` timestamps.
  - Netflix‑style rules as far as budget allows:
    - Reasonable CPS (characters per second).
    - Minimum/maximum line durations.
    - Max lines per subtitle (typically 2).
- **Diagnostics (`.diag.*.srt`):**
  - Additional SRTs containing raw segmentation or pre‑post‑processing segments for debugging.
- **JSON event (`srt_written`):**
  - `{"event": "srt_written", "path": "<absolute-or-normalised-path>"}` (single line per successful file).

---

## 6. Configuration Model

### 6.1 Sources of Configuration

- **Defaults in code (`AppSettings` dataclasses).**
- **Bundled YAML (`config.yaml` at package root).**
- **Environment variables:**
  - `SRTFORGE_CONFIG` – path to override YAML file.
- **CLI flags:**
  - `--cpu`, `--output`, `--glob`, directory/media path arguments.
- **GUI session overrides:**
  - Options dialog writes session‑level YAML used by the CLI subprocesses.
- **Remote/dynamic config:**
  - **None.** All configuration is local; no remote config services.

### 6.2 Precedence Rules

Effective configuration is resolved roughly as:

1. **Hardcoded defaults** in dataclasses (`AppSettings` and nested types).
2. **Bundled `config.yaml`** in the package root.
3. **User‑specified config via `SRTFORGE_CONFIG`** (YAML, same schema).
4. **GUI session overrides** (YAML, same schema; takes precedence over files when launching CLI from GUI).
5. **CLI flags** for run‑specific options (`--output`, `--cpu`, `--glob`).

Conflicts are resolved by the later layer overriding earlier ones. GUI must **mirror the YAML schema exactly** so that toggling an Option is equivalent to editing the corresponding config field.

### 6.3 Configuration Objects / Structures

- `AppSettings` as described in §4.5 is the root configuration object.
- Configuration flow at runtime:
  1. Import `settings` at process startup; this triggers YAML + env load and dataclass merge.
  2. CLI/GUI reads from `settings` to construct `PipelineConfig` for each run.
  3. GUI may write a new YAML file that subsequent CLI runs read via `SRTFORGE_CONFIG`.

---

## 7. Operational Behaviour

### 7.1 Performance Considerations

- **Target hardware:** Windows 11 machine with **RTX 3070 Ti (10GB VRAM)** or similar.
- **Typical workload:** 20–30 minute TV episodes processed in **under 4 minutes**, including FV4 separation + Parakeet‑based ASR, given the above GPU. Longer content up to ~24h is supported with proportionally higher latency.
- **GPU vs CPU:**
  - GPU strongly preferred for both FV4 and Parakeet.
  - `--cpu` flag forces CPU mode for both, which may be 3–10× slower depending on hardware.
- **Batch vs streaming:**
  - Pipeline is batch‑oriented (whole‑file processing) with internal chunking for long audio within NeMo.

### 7.2 Failure Modes & Graceful Degradation

- **Common error scenarios:**
  - Media file missing → `PipelineResult.skipped=True` with `reason="media missing"` → `run` exits code 2.
  - No suitable English audio stream (with `allow_untagged_english=False`) → skip with code 2.
  - FFmpeg/FV4/NeMo operational errors → caught and translated into `skipped=True` with explanation.
- **Partial failures in `series`:**
  - Some files may succeed (emit `srt_written`), others may skip/fail (no JSON); overall `series` still exits 0 if at least one file was processed.
- **Sonarr hook:**
  - All errors are treated as non‑fatal; Sonarr always sees exit code 0.
- **Vision for FV4 failure handling:**
  - On FV4 separation failure:
    - Log a **hard error** for separation itself.
    - Treat this as “skip separation but continue” with mixed audio (fallback path) rather than marking the whole pipeline as skipped.
    - This is marked as **future work**, see §10.

### 7.3 Observability

- **Logging:**
  - Rich console logging in CLI with per‑run UUIDs and timing metrics.
  - GUI console tailing CLI stdout/stderr per queue item.
- **Metrics:**
  - No dedicated metrics system yet; timings are log‑based.
- **Tracing:**
  - Run IDs / temp directory names serve as the primary correlation mechanism between logs, diagnostics, and outputs.

---

## 8. User Interfaces

### 8.1 CLI UX

- **Main commands:**
  - `srtforge run [OPTIONS] MEDIA`
  - `srtforge series [OPTIONS] DIRECTORY`
  - `srtforge sonarr-hook`
  - `srtforge-sonarr` (direct script, same as `sonarr-hook`)
  - `python -m srtforge ...` (invokes Typer app).

- **Key flags:**
  - `--cpu` – force CPU inference for both FV4 and Parakeet.
  - `--output, -o` – explicit SRT output path for `run`.
  - `--glob` – glob pattern for `series`.

- **Exit codes recap (non‑Typer errors):**
  - `0` – Success (SRT(s) produced).
  - `1` – `series`: no files matched glob.
  - `2` – `run`: pipeline skipped/failed for this media.

- **Example invocations:**
  - `srtforge run "Show.S01E01.mkv"`
  - `srtforge run --cpu -o "Show.S01E01.en.srt" "Show.S01E01.mkv"`
  - `srtforge series "D:/TV/Show"`
  - `srtforge series --glob "**/*.mp4" "D:/TV/Show"`
  - `srtforge sonarr-hook` (from Sonarr Custom Script).

### 8.2 GUI UX

- **High‑level flow:**
  1. User launches `srtforge-gui`.
  2. Adds files/folders to the queue.
  3. Optionally adjusts settings (paths, CPU/GPU, separation, embedding defaults) in Options dialog.
  4. Starts processing; queue items run one at a time via CLI subprocesses.
  5. User reviews status, logs, and resulting SRT paths; optionally triggers embed or burn‑in operations.

- **Queue / job model:**
  - Single running job at a time; pending, running, completed, skipped, failed states.
  - ETA column based on prior runs and/or simple heuristics.
  - Logs per item accessible via console pane or details panel.

- **Status, ETA, logs:**
  - `srt_written` JSON events determine the “Completed” state and final SRT location.
  - Exit code 2 marks items as “Skipped” with textual reason when available.

- **Embedding/burning UI:**
  - Dedicated controls to:
    - Soft embed existing SRT into the original MKV/MP4 (FFmpeg or MKVToolNix).
    - Burn subtitles into a new video file.
  - Explicit selection of language code (`eng`), default/forced flags, and overwrite behavior.

### 8.3 Automation Hooks

- **Sonarr integration:**
  - `srtforge-sonarr` / `srtforge sonarr-hook` invoked as a Custom Script for `Download` / `Upgrade` events.
  - Script reads environment, triggers pipeline, and **never fails the Sonarr job** even if subtitles fail.

- **JSON events for other automation:**
  - Any external tool can run `srtforge run`/`series` and watch stdout:
    - Filter lines for JSON and `event == "srt_written"` to know when/where subtitles were created.
  - Exit code 2 vs other errors can be used to distinguish “skipped/soft failure” vs “hard crash” cases.

- **CI/CD / scheduled jobs (future vision):**
  - Potential watchers that scan directories periodically and invoke `srtforge series` for new files.

---

## 9. Phase / Step Log (Implementation History)

> Note: Commit hashes below use short Git SHAs (e.g. `a8cf382`) referencing commits in the `main` branch history.

### Phase 1 – Foundational Pipeline & CLI

- **Status:** ✅ Completed  
- **Timeframe:** v1.0.0 (initial public release)
- **Commits:**
  - `a8cf382` – Implement `Pipeline`, `PipelineConfig`, and core processing chain with FV4 separation, and Parakeet ASR.fileciteturn0file30turn0file22
  - `a8cf382` – Add Typer CLI with `run` and `series` subcommands and `srt_written` JSON events.
- **Notes:**
  - Established Netflix‑style post‑processing defaults and `.srt` output naming.
  - Defined exit code semantics (0 vs 2) for single‑file runs.
  - Added basic tests covering pipeline orchestration.fileciteturn0file27

### Phase 2 – Sonarr Integration

- **Status:** ✅ Completed  
- **Timeframe:** v1.x
- **Commits:**
  - `a8cf382` – Add `srtforge.sonarr_hook` and `srtforge-sonarr` entry point.fileciteturn0file24turn0file30
- **Notes:**
  - Normalised Sonarr event names and environment variables.
  - Ensured Sonarr hook always exits with 0 while surfacing failures through logs only.
  - Added tests for mapping of alias events and environment handling.fileciteturn0file24

### Phase 3 – Windows 11 GUI (“Srtforge Studio”)

- **Status:** ✅ Completed  
- **Timeframe:** v1.x
- **Commits:**
  - `57c6beb` – Introduce PySide6 GUI (`gui_app`) with queue, ETA, and log tailing.fileciteturn0file31turn0file20
  - `1d57feb` – Add subtitle embedding (FFmpeg/MKVToolNix) and burn‑in features.
- **Notes:**
  - Implemented `TranscriptionWorker` and CLI subprocess integration, including JSON event parsing and structured Run ID handling.
  - Added session‑level YAML config overrides written by the Options dialog.
  - Integrated a Win11‑style QSS theme for a modern look.

### Phase 4 – NeMo Compatibility & CUDA Robustness

- **Status:** ✅ Completed  
- **Timeframe:** v1.x
- **Commits:**
  - `b8b20cc` – Add `_nemo_compat` with Megatron microbatch stub and `ensure_cuda_python_available`.fileciteturn0file21turn0file28
  - `1ddb8ee` – Refine `parakeet_to_srt` to handle NeMo API differences and apply long‑audio settings.fileciteturn0file22turn0file25
- **Notes:**
  - Removed noisy NeMo warnings when Megatron is absent.
  - Provided clearer error messages when CUDA runtime bindings are missing or outdated.
  - Ensured GPU failures fall back to CPU instead of aborting runs.

### Phase 5 – Temp Directory Hygiene & Diagnostics Relocation

- **Status:** ✅ Completed  
- **Timeframe:** v1.x
- **Commits:**
  - `1031ff4` – Add `cleanup_run_directories` (24‑hour stale cleanup) and run‑scoped temp directory management.fileciteturn0file30
  - `8c38cc8` – Move `.diag.*` SRT sidecars into per‑run temp directories when SRTs are written next to media.
- **Notes:**
  - Reduced clutter in media directories.
  - Ensured stale temp directories and diagnostics don’t accumulate indefinitely.

### Phase 6 – Audio Separation Robustness (Planning)

- **Status:** ☐ Planned  
- **Timeframe:** Future
- **Notes:**
  - Goal: treat FV4 separation failures as non‑fatal where possible:
    - Log a hard error for the separation step.
    - Fall back to mixed audio while still producing SRTs when ASR can proceed.
    - Optionally emit a future JSON warning event to surface “degraded separation” to integrators.
  - Requires careful interaction design with CLI exit codes and GUI error messaging.

---

## 10. Future Work / Parking Lot

> Items below are **speculative** or **planned**, not commitments. They are grouped by rough horizon.

### Near‑Term / Likely

1. **Title:** FV4 Failure Fallback Path  
   **Type:** Refactor / Robustness  
   **Description:**  
   Treat FV4 separation failures as a **non‑fatal** condition where possible: log a clear error, skip separation, and continue the pipeline with mixed audio. Only truly catastrophic errors (e.g. unreadable media) should cause a full pipeline skip.  
   **Open Questions / Risks:**
   - How to distinguish “FV4 broken but audio usable” from “audio unusable at all”?
   - Do we need a separate JSON event or additional reason codes to surface “no separation” to downstream tools?
   **Dependencies:**  
   - Existing pipeline error handling and configuration flags for separation backends.

2. **Title:** Config/GUI Single‑Source‑of‑Truth Hardening  
   **Type:** Refactor / UX  
   **Description:**  
   Ensure the YAML config and GUI Options dialog remain perfectly in sync, including support for “Reset to defaults” and protection against partial writes. The config loader should tolerate older config versions gracefully.  
   **Open Questions / Risks:**
   - Backward compatibility for config keys as new options are added.
   - How to handle deprecations (e.g. removed fields) without breaking existing configs?
   **Dependencies:**  
   - Current `AppSettings` schema and GUI binding logic.

3. **Title:** Windows‑Only Bundled Distribution  
   **Type:** Infra / Packaging  
   **Description:**  
   Ship a **single Windows 11 installer** that bundles FFmpeg, MKVToolNix, Parakeet `.nemo`, FV4 checkpoint/config, and the appropriate CUDA/NeMo runtime dependencies, with sensible defaults for GPU/CPU installs.  
   **Open Questions / Risks:**
   - Size of the bundled installer vs. user expectations.
   - Handling NVIDIA driver and CUDA version compatibility gracefully.
   **Dependencies:**  
   - Stable model versions and installer scripts (`install.ps1`, `install.sh`).

4. **Title:** Improved Error Surfacing in GUI  
   **Type:** UX  
   **Description:**  
   Make error/skip reasons from the pipeline highly visible in the GUI (bad FFmpeg, missing models, no English stream), including tooltips, icons, and a dedicated “diagnostics” view.  
   **Open Questions / Risks:**
   - How much technical detail to expose vs. keeping UX approachable?
   **Dependencies:**  
   - Existing CLI/logging behavior and `PipelineResult.reason` semantics.

### Longer‑Term / Speculative

5. **Title:** Advanced Netflix‑Rule Tuning  
   **Type:** Experiment / Quality  
   **Description:**  
   Explore more sophisticated heuristics or ML‑assisted tuning for Netflix reading speeds and line breaks (e.g. learning from golden reference SRTs).  
   **Open Questions / Risks:**
   - ROI vs complexity and runtime overhead.
   **Dependencies:**  
   - Access to high‑quality aligned subtitle datasets (not bundled).

6. **Title:** Multi‑Queue / Multi‑Process Support  
   **Type:** Infra / Scaling (Local)  
   **Description:**  
   Investigate safely running multiple pipeline instances in parallel on large GPUs or multi‑GPU systems while respecting VRAM and CPU constraints.  
   **Open Questions / Risks:**
   - VRAM fragmentation and NeMo’s GPU memory patterns.
   - Complexity of job scheduling vs. simplicity of current design.
   **Dependencies:**  
   - Existing single‑pipeline implementation and CUDA/NeMo behavior.

7. **Title:** Rich Automation API (Local‑Only)  
   **Type:** Idea / API  
   **Description:**  
   Provide a local HTTP or IPC API on top of the pipeline, preserving the privacy and offline guarantees while making it easier to integrate with other tools beyond Sonarr (e.g. local media managers).  
   **Open Questions / Risks:**
   - How to avoid user confusion between “online API” and “local‑only service”?
   **Dependencies:**  
   - Stable CLI/JSON contract and configuration model.

8. **Title:** SRT Quality Metrics Dashboard  
   **Type:** UX / Tooling  
   **Description:**  
   Build a diagnostics dashboard summarizing per‑file CPS, subtitle length distribution, and rule violations, based on `.diag.*` outputs.  
   **Open Questions / Risks:**
   - Need for extra data collection vs. privacy constraints (should remain strictly local).
   **Dependencies:**  
   - Availability of diagnostic SRTs and structured rule evaluation output.

---

*End of Master Architecture Document for Srtforge.*
