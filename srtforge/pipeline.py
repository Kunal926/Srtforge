"""Processing pipeline for the Sonarr-driven offline SRT generation flow."""

from __future__ import annotations

import shutil
import tempfile
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional


from rich.table import Table

from .config import DEFAULT_OUTPUT_SUFFIX, FV4_CONFIG, FV4_MODEL, MODELS_DIR
from .ffmpeg import DEFAULT_TOOLS, AudioStream, FFmpegTooling
from .gpu_runtime import clear_accelerator_caches
from .logging import RunLogger, emit_log, emit_progress, get_console, status
from .mux import (
    burn_subtitles,
    embed_subtitles_ffmpeg,
    embed_subtitles_mkvmerge,
    pick_embed_method,
)
from .post import postprocess_segments
from .post.srt_utils import write_srt as _write_srt_with_diag
from .settings import (
    EXTRACTION_MODE_DUAL_MONO_CENTER,
    EXTRACTION_MODE_STEREO_MIX,
    settings,
)
from .utils import build_media_context_label, probe_video_fps


def _has_center_channel(layout: str | None, channels: int | None) -> bool:
    """Return ``True`` if the probed layout strongly indicates a center channel."""

    if not channels:
        return False
    text = (layout or "").upper()
    # Modern ffprobe exposes ``ch_layout`` as symbolic channel names (``FL+FR+FC``...)
    if "+" in text and "FC" in text:
        return True
    # Legacy ``channel_layout`` names provide less detail; fall back to conservative heuristics
    if channels >= 3 and any(tag in text for tag in {"3.0", "3.1", "4.0", "4.1", "5.0", "5.1", "6.1", "7.1"}):
        return True
    return False


@dataclass(slots=True)
class PipelineConfig:
    """Configuration for a single processing run."""

    media_path: Path
    output_path: Optional[Path] = None
    tools: FFmpegTooling = DEFAULT_TOOLS
    models_dir: Path = MODELS_DIR
    fv4_model: Path = settings.separation.fv4.ckpt or FV4_MODEL
    fv4_config: Path = settings.separation.fv4.cfg or FV4_CONFIG
    temp_dir: Optional[Path] = settings.paths.temp_dir
    output_directory: Optional[Path] = settings.paths.output_dir
    sample_rate: int = settings.separation.sep_hz
    separation_backend: str = settings.separation.backend
    separation_prefer_center: bool = settings.separation.prefer_center
    separation_prefer_gpu: bool = settings.separation.prefer_gpu
    ffmpeg_filter_chain: str = settings.ffmpeg.filter_chain
    ffmpeg_extraction_mode: str = settings.ffmpeg.extraction_mode
    prefer_gpu: bool = settings.separation.prefer_gpu
    asr_engine: str = settings.whisper.engine
    whisper_model: str = settings.whisper.model
    whisper_language: str = settings.whisper.language
    parakeet_force_float32: bool = settings.whisper.force_float32
    parakeet_rel_pos_local_attn: list[int] = field(default_factory=lambda: list(settings.whisper.rel_pos_local_attn))
    parakeet_subsampling_conv_chunking_factor: int = settings.whisper.subsampling_conv_chunking_factor
    gemini_enabled: bool = settings.gemini.enabled
    gemini_model_id: str = settings.gemini.model_id
    gemini_api_key: Optional[str] = settings.gemini.api_key
    allow_untagged_english: bool = settings.separation.allow_untagged_english
    dump_word_timestamps: bool = False
    word_timestamps_path: Optional[Path] = None

    # --- Output / muxing options -----------------------------------------
    # When ``embed_enabled`` is True the SRT is muxed into a copy of
    # ``media_path`` after post-processing. ``embed_method`` is one of
    # ``"auto"``, ``"mkvmerge"``, or ``"ffmpeg"``; ``"auto"`` prefers
    # mkvmerge for Matroska/WebM and falls back to ffmpeg otherwise.
    embed_enabled: bool = False
    embed_method: str = "auto"
    embed_track_title: str = "Srtforge (English)"
    embed_track_lang: str = "eng"
    embed_default: bool = True
    embed_forced: bool = False
    # ``replace_original`` swaps the muxed copy back onto ``media_path``;
    # otherwise the muxed file is written next to it as ``<stem>_subbed<suffix>``.
    replace_original: bool = False
    # ``burn_enabled`` produces a hard-subbed re-encode at
    # ``<stem>_burned<suffix>`` next to the source. Independent of embed.
    burn_enabled: bool = False
    # ``sidecar_srt`` keeps the SRT next to the original media even when
    # an embed/burn target is also written. Always True today; reserved
    # for a future "embed-only" toggle.
    sidecar_srt: bool = True


@dataclass(slots=True)
class PipelineResult:
    """Summary of a completed pipeline run."""

    media_path: Path
    output_path: Optional[Path]
    skipped: bool
    reason: Optional[str] = None
    run_id: Optional[str] = None
    performance_log_path: Optional[Path] = None
    # Populated when ``embed_enabled``/``burn_enabled`` produced a media
    # asset; the worker forwards these as ``media_written`` events.
    embedded_path: Optional[Path] = None
    burned_path: Optional[Path] = None

    @property
    def failed(self) -> bool:
        """Compatibility alias used by worker event emission."""
        return self.skipped

    @property
    def error(self) -> Optional[str]:
        """Compatibility alias used by worker/automation error surfaces."""
        return self.reason


class _PipelineProgress:
    """Map local stage progress to one monotonic run-level fraction."""

    _RANGES = {
        "asr": (0.545, 0.94),
        "post": (0.94, 0.985),
        "write": (0.985, 1.0),
    }

    def __init__(self) -> None:
        self._last = 0.0

    def emit(self, stage: str, local_fraction: float) -> None:
        start, end = self._RANGES[stage]
        local = max(0.0, min(1.0, float(local_fraction)))
        fraction = start + (end - start) * local
        if fraction < self._last:
            fraction = self._last
        self._last = fraction
        emit_progress(stage, fraction)

    def callback(self, stage: str) -> Callable[[float], None]:
        return lambda local_fraction: self.emit(stage, local_fraction)


class Pipeline:
    """Implements the ordered processing chain required by the project specification."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.console = get_console()

    # ---- helpers -----------------------------------------------------------------
    def _determine_output_path(self) -> Path:
        if self.config.output_path:
            return self.config.output_path
        if self.config.output_directory:
            return self.config.output_directory / f"{self.config.media_path.stem}{DEFAULT_OUTPUT_SUFFIX}"
        return self.config.media_path.with_suffix(DEFAULT_OUTPUT_SUFFIX)

    # ---- pipeline steps ----------------------------------------------------------
    def run(self) -> PipelineResult:
        media_path = self.config.media_path
        if not media_path.exists():
            return PipelineResult(media_path, None, True, "media missing")

        output_path = self._determine_output_path()
        tmp_kwargs: dict[str, str] = {"prefix": "srtforge_"}
        run_id: Optional[str] = None
        performance_log_path: Optional[Path] = None
        run_logger: RunLogger | None = None
        embedded_path: Optional[Path] = None
        burned_path: Optional[Path] = None
        progress = _PipelineProgress()

        base_tmp_dir = Path(tempfile.gettempdir())
        if self.config.temp_dir:
            self.config.temp_dir.mkdir(parents=True, exist_ok=True)
            tmp_kwargs["dir"] = str(self.config.temp_dir)
            base_tmp_dir = self.config.temp_dir

        try:
            with RunLogger.start() as run_logger:
                run_id = run_logger.run_id
                performance_log_path = run_logger.path
                tmp_kwargs["prefix"] = f"srtforge_{run_id}_"

                def _log_live_detail(message: str) -> None:
                    run_logger.log(message)
                    source = "pipeline-heartbeat" if "still running after" in message else "pipeline"
                    if not emit_log(message, source=source):
                        self.console.log(f"[dim]{message}[/dim]")

                run_logger.log(f"Media: {media_path}")
                run_logger.log(f"Output: {output_path}")
                self.console.log(f"[cyan]Run ID[/cyan] {run_id}")

                # Time stale temp-dir cleanup
                with run_logger.step("Cleanup stale temporary run directories"):
                    cleanup_run_directories(base_tmp_dir)

                tmp_ctx = tempfile.TemporaryDirectory(**tmp_kwargs)
                try:
                    tmp = Path(tmp_ctx.name)
                    # Include show + episode metadata in our working WAV filenames.
                    # This gives Gemini extra context when we upload audio, and also makes
                    # temp directories easier to inspect/debug.
                    # Keep this fairly short so Windows temp paths don't hit MAX_PATH.
                    media_label = build_media_context_label(media_path, max_length=120)

                    def _work_wav(stage: str) -> Path:
                        if media_label:
                            return tmp / f"{media_label} - {stage}.wav"
                        return tmp / f"{stage}.wav"

                    extracted = _work_wav("english")
                    vocals = _work_wav("vocals")
                    preprocessed = _work_wav("preprocessed")
                    word_timestamps_path: Optional[Path] = None

                    with run_logger.step("Probe audio streams", stage="probe"):
                        streams = self.config.tools.probe_audio_streams(media_path)
                        english_stream = self._select_english_stream(streams)
                    if not english_stream:
                        reason = "no English audio stream"
                        run_logger.mark_skipped(reason)
                        self.console.log(f"[yellow]Skipping[/yellow] {media_path} – {reason}")
                        return PipelineResult(
                            media_path,
                            None,
                            True,
                            reason,
                            run_id,
                            performance_log_path,
                        )

                    with status(
                        f"Extracting English audio to PCM f32 {self.config.sample_rate} Hz"
                    ), run_logger.step("Extract English audio", stage="extract"):
                        # Decide which extraction mode to use. We apply center isolation
                        # during extraction (not during preprocessing) so we never try to
                        # pan a 2-channel file for a missing FC channel.
                        requested_mode = (self.config.ffmpeg_extraction_mode or "").strip().lower()
                        layout = getattr(english_stream, "channel_layout", None)
                        channels = english_stream.channels or 0
                        has_center = _has_center_channel(layout, channels)

                        extraction_mode = requested_mode
                        if extraction_mode in {"", "default"}:
                            extraction_mode = EXTRACTION_MODE_STEREO_MIX

                        if extraction_mode not in {
                            EXTRACTION_MODE_STEREO_MIX,
                            EXTRACTION_MODE_DUAL_MONO_CENTER,
                        }:
                            run_logger.log(
                                "Warning: Unknown ffmpeg.extraction_mode="
                                f"{requested_mode!r}; falling back to {EXTRACTION_MODE_STEREO_MIX}."
                            )
                            self.console.log(
                                "[yellow]Warning[/yellow] Unknown ffmpeg.extraction_mode="
                                f"{requested_mode!r}; falling back to {EXTRACTION_MODE_STEREO_MIX}."
                            )
                            extraction_mode = EXTRACTION_MODE_STEREO_MIX

                        if extraction_mode == EXTRACTION_MODE_DUAL_MONO_CENTER and not has_center:
                            run_logger.log(
                                "Warning: extraction_mode=dual_mono_center requested, but the selected "
                                "audio stream has no detectable Center (FC) channel; falling back to stereo_mix."
                            )
                            self.console.log(
                                "[yellow]Warning[/yellow] Dual Mono (Center Isolation) requested, but "
                                "no Center (FC) channel was detected; falling back to Stereo Mix."
                            )
                            extraction_mode = EXTRACTION_MODE_STEREO_MIX

                        self.config.tools.extract_audio_stream(
                            media_path,
                            english_stream.index,
                            extracted,
                            sample_rate=self.config.sample_rate,
                            channels=2,
                            extraction_mode=extraction_mode,
                        )

                    separated_source = extracted
                    backend = (self.config.separation_backend or "fv4").lower()
                    if backend == "fv4":
                        with status("Running FV4 MelBand Roformer vocal separation"), run_logger.step(
                            "Vocal separation", stage="separation"
                        ):
                            self.config.tools.isolate_vocals(
                                extracted,
                                vocals,
                            self.config.fv4_model,
                            self.config.fv4_config,
                            prefer_gpu=self.config.separation_prefer_gpu,
                            diagnostic_callback=_log_live_detail,
                        )
                        separated_source = vocals
                    elif backend in {"none", "skip"}:
                        run_logger.log("Vocal separation skipped by configuration")
                        separated_source = extracted
                    else:
                        message = f"Unsupported separation backend: {self.config.separation_backend}"
                        run_logger.log_error(message)
                        raise ValueError(message)

                    # Preprocessing should never try to "pan" the already-extracted audio.
                    # It should just apply the filter chain (HPF/LPF + resample) and downmix
                    # the resulting stereo to mono.
                    filter_chain = self.config.ffmpeg_filter_chain
                    with status("Applying FFmpeg preprocessing filters"), run_logger.step(
                        "FFmpeg preprocessing", stage="preprocess"
                    ):
                        self.config.tools.preprocess_audio(
                            separated_source,
                            preprocessed,
                            filter_chain=filter_chain,
                        )

                    with run_logger.step("Release separation GPU resources"):
                        clear_accelerator_caches()

                    engine = (self.config.asr_engine or "whisper").strip().lower()
                    if engine in {"", "default"}:
                        engine = "whisper"
                    if engine == "parakeet" and self.config.prefer_gpu:
                        with run_logger.step("Prepare Parakeet CUDA runtime", stage="asr"):
                            from .asr._nemo_compat import ensure_cuda_python_available
                            from .engine_parakeet import get_parakeet_device_config
                            from .gpu_runtime import preload_onnxruntime_cuda_dlls

                            device, _compute_type = get_parakeet_device_config(
                                prefer_gpu=self.config.prefer_gpu,
                            )
                            if device == "cuda":
                                preload_error = preload_onnxruntime_cuda_dlls(prefer_gpu=True)
                                if preload_error:
                                    raise RuntimeError(
                                        "ONNX Runtime CUDA DLL preload failed before Parakeet startup: "
                                        f"{preload_error}"
                                    )
                                ensure_cuda_python_available()
                                run_logger.log("Parakeet CUDA Python bindings preloaded before ASR")

                    with status("Running ASR and subtitle post-processing"), run_logger.step(
                        "ASR pipeline", stage="asr"
                    ):
                        asr_progress = progress.callback("asr")
                        asr_progress(0.0)

                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        if self.config.dump_word_timestamps:
                            word_timestamps_path = (
                                self.config.word_timestamps_path or output_path.with_suffix(".words.json")
                            )
                            word_timestamps_path.parent.mkdir(parents=True, exist_ok=True)

                        if engine == "whisper":
                            from .engine_whisper import (
                                correct_text_only_with_gemini,
                                generate_optimized_events,
                                get_whisper_device_config,
                            )

                            device, compute_type = get_whisper_device_config(
                                prefer_gpu=self.config.prefer_gpu,
                            )
                            run_logger.log(
                                "ASR engine: whisper "
                                f"device: {device} compute: {compute_type} model: {self.config.whisper_model}"
                            )
                            events = generate_optimized_events(
                                str(preprocessed),
                                model_name=self.config.whisper_model,
                                language=self.config.whisper_language,
                                prefer_gpu=self.config.prefer_gpu,
                                word_timestamps_out=(
                                    str(word_timestamps_path.resolve()) if word_timestamps_path else None
                                ),
                                progress_callback=asr_progress,
                            )
                            run_logger.log(f"Whisper segments: {len(events)}")
                        elif engine == "parakeet":
                            from .engine_parakeet import generate_optimized_events, get_parakeet_device_config
                            from .engine_whisper import correct_text_only_with_gemini

                            device, compute_type = get_parakeet_device_config(
                                prefer_gpu=self.config.prefer_gpu,
                            )
                            run_logger.log(
                                "ASR engine: parakeet "
                                f"device: {device} compute: {compute_type} model: {self.config.whisper_model}"
                            )
                            run_logger.log(
                                "Parakeet options: "
                                f"rel_pos_local_attn={self.config.parakeet_rel_pos_local_attn} "
                                "subsampling_conv_chunking_factor="
                                f"{self.config.parakeet_subsampling_conv_chunking_factor} "
                                f"force_float32={self.config.parakeet_force_float32}"
                            )

                            def _log_parakeet_timing(label: str, seconds: float) -> None:
                                _log_live_detail(f"ASR detail: {label} - {seconds:.2f}s")

                            def _log_parakeet_diagnostic(message: str) -> None:
                                _log_live_detail(f"ASR detail: {message}")

                            events = generate_optimized_events(
                                str(preprocessed),
                                model_name=self.config.whisper_model,
                                language=self.config.whisper_language,
                                prefer_gpu=self.config.prefer_gpu,
                                force_float32=self.config.parakeet_force_float32,
                                rel_pos_local_attn=self.config.parakeet_rel_pos_local_attn,
                                subsampling_conv_chunking_factor=(
                                    self.config.parakeet_subsampling_conv_chunking_factor
                                ),
                                word_timestamps_out=(
                                    str(word_timestamps_path.resolve()) if word_timestamps_path else None
                                ),
                                progress_callback=asr_progress,
                                timing_callback=_log_parakeet_timing,
                                diagnostic_callback=_log_parakeet_diagnostic,
                            )
                            run_logger.log(f"Parakeet segments: {len(events)}")
                        else:
                            message = f"Unsupported ASR engine: {self.config.asr_engine}"
                            run_logger.log_error(message)
                            raise ValueError(message)

                        if self.config.gemini_enabled:
                            events = correct_text_only_with_gemini(
                                str(preprocessed),
                                events,
                                api_key=self.config.gemini_api_key,
                                model_id=self.config.gemini_model_id,
                            )
                            run_logger.log("Gemini correction enabled")
                        asr_progress(1.0)

                    # Subtitle post-processing (Netflix house style): re-segment on
                    # pauses, balance two-line shape, enforce CPS / min-readable /
                    # frame snap. Lives outside the ASR step so a hang in the
                    # post-processor doesn't get blamed on inference.
                    with run_logger.step("Post-processing", stage="post"):
                        post_progress = progress.callback("post")
                        post_progress(0.0)
                        snap_fps = probe_video_fps(media_path)
                        run_logger.log(
                            f"ASR events in: {len(events)}; snap_fps={snap_fps:.3f}"
                        )
                        events = postprocess_segments(
                            events,
                            snap_fps=snap_fps,
                            progress_callback=post_progress,
                        )
                        run_logger.log(f"Post-processed cues: {len(events)}")
                        post_progress(1.0)

                    with run_logger.step("Write SRT", stage="write"):
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        _write_srt_with_diag(events, str(output_path))
                        progress.emit("write", 1.0)

                    # Optional: soft-embed the SRT into a copy of the
                    # source container (or back onto it via
                    # ``replace_original``). Failures here do not delete
                    # the SRT — it remains the canonical output.
                    if self.config.embed_enabled:
                        with run_logger.step("Embed subtitles", stage="mux"):
                            method = pick_embed_method(media_path, self.config.embed_method)
                            run_logger.log(
                                f"Embed method: {method} (requested={self.config.embed_method!r}, "
                                f"replace_original={self.config.replace_original})"
                            )
                            embed_kwargs = dict(
                                track_title=self.config.embed_track_title,
                                track_lang=self.config.embed_track_lang,
                                default=self.config.embed_default,
                                forced=self.config.embed_forced,
                                replace_original=self.config.replace_original,
                            )
                            if method == "mkvmerge":
                                embedded_path = embed_subtitles_mkvmerge(
                                    media_path,
                                    output_path,
                                    **embed_kwargs,
                                )
                            else:
                                embedded_path = embed_subtitles_ffmpeg(
                                    media_path,
                                    output_path,
                                    **embed_kwargs,
                                )
                            run_logger.log(f"Embedded subtitles to: {embedded_path}")

                    # Optional: hard-burn into a separate
                    # ``<stem>_burned<suffix>`` re-encode. Always written
                    # alongside the source — never overwrites it.
                    if self.config.burn_enabled:
                        with run_logger.step("Burn subtitles", stage="burn"):
                            burned_path = burn_subtitles(media_path, output_path)
                            run_logger.log(f"Burned subtitles to: {burned_path}")
                finally:
                    # Time deletion of the per-run temp directory
                    #
                    # NOTE: On Windows it is common for the temp directory cleanup to fail
                    # transiently (e.g., ffmpeg/ONNX still holding a file handle briefly).
                    # Cleanup failure should not flip an otherwise successful run into a
                    # pipeline failure (SRTs may already be written).
                    with run_logger.step("Cleanup run temporary directory"):
                        try:
                            tmp_ctx.cleanup()
                        except Exception as exc:
                            # Best-effort cleanup: warn and continue.
                            run_logger.log(
                                "WARNING: Failed to cleanup run temporary directory "
                                f"{getattr(tmp_ctx, 'name', '')!r}: {exc}"
                            )

        except Exception as exc:
            if run_logger is not None:
                run_logger.log("TRACEBACK:")
                for line in traceback.format_exc(limit=40).rstrip().splitlines():
                    run_logger.log(line)
            self.console.log(f"[bold red]Pipeline failed[/bold red] {media_path}: {exc}")
            return PipelineResult(
                media_path,
                None,
                True,
                str(exc),
                run_id,
                performance_log_path,
            )

        self._show_summary(media_path, output_path)
        return PipelineResult(
            media_path,
            output_path,
            False,
            run_id=run_id,
            performance_log_path=performance_log_path,
            embedded_path=embedded_path,
            burned_path=burned_path,
        )

    # ---- internal methods --------------------------------------------------------
    def _show_summary(self, media: Path, srt: Path) -> None:
        table = Table(title="Srtforge summary", show_header=True, header_style="bold magenta")
        table.add_column("Media", style="cyan")
        table.add_column("SRT", style="green")
        table.add_row(str(media), str(srt))
        self.console.print(table)

    def _select_english_stream(self, streams: Iterable[AudioStream]) -> Optional[AudioStream]:
        english_streams: list[AudioStream] = []
        for stream in streams:
            lang = (stream.language or "").lower()
            if lang in {"en", "eng", "english"}:
                english_streams.append(stream)
        if english_streams:
            if self.config.separation_prefer_center:
                for stream in english_streams:
                    if stream.channels == 1:
                        return stream
            return english_streams[0]
        # Fallback path when opt-in setting is enabled
        if getattr(self.config, "allow_untagged_english", False):
            # Pick the first audio stream as a best-effort default
            for stream in streams:
                return stream
        return None


def run_pipeline(config: PipelineConfig) -> PipelineResult:
    """Convenience wrapper for launching the pipeline."""

    pipeline = Pipeline(config)
    return pipeline.run()


def cleanup_run_directories(base_dir: Path) -> None:
    """Remove leftover temporary run directories older than 24 hours."""

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    if not base_dir.exists():
        return
    for entry in base_dir.iterdir():
        if not entry.is_dir() or not entry.name.startswith("srtforge_"):
            continue
        try:
            modified = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if modified < cutoff:
            try:
                shutil.rmtree(entry)
            except OSError:
                continue
