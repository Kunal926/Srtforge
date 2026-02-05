"""Processing pipeline for the Sonarr-driven offline SRT generation flow."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional


from rich.table import Table

from .config import DEFAULT_OUTPUT_SUFFIX, FV4_CONFIG, FV4_MODEL, MODELS_DIR
from .ffmpeg import DEFAULT_TOOLS, AudioStream, FFmpegTooling
from .logging import RunLogger, get_console, status
from .settings import (
    EXTRACTION_MODE_DUAL_MONO_CENTER,
    EXTRACTION_MODE_STEREO_MIX,
    settings,
)
from .utils import build_media_context_label


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
    gemini_enabled: bool = settings.gemini.enabled
    gemini_model_id: str = settings.gemini.model_id
    gemini_api_key: Optional[str] = settings.gemini.api_key
    allow_untagged_english: bool = settings.separation.allow_untagged_english
    dump_word_timestamps: bool = False
    word_timestamps_path: Optional[Path] = None


@dataclass(slots=True)
class PipelineResult:
    """Summary of a completed pipeline run."""

    media_path: Path
    output_path: Optional[Path]
    skipped: bool
    reason: Optional[str] = None
    run_id: Optional[str] = None

    @property
    def failed(self) -> bool:
        """Compatibility alias used by worker event emission."""
        return self.skipped

    @property
    def error(self) -> Optional[str]:
        """Compatibility alias used by worker/automation error surfaces."""
        return self.reason


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

        base_tmp_dir = Path(tempfile.gettempdir())
        if self.config.temp_dir:
            self.config.temp_dir.mkdir(parents=True, exist_ok=True)
            tmp_kwargs["dir"] = str(self.config.temp_dir)
            base_tmp_dir = self.config.temp_dir

        try:
            with RunLogger.start() as run_logger:
                run_id = run_logger.run_id
                tmp_kwargs["prefix"] = f"srtforge_{run_id}_"
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

                    with run_logger.step("Probe audio streams"):
                        streams = self.config.tools.probe_audio_streams(media_path)
                        english_stream = self._select_english_stream(streams)
                    if not english_stream:
                        reason = "no English audio stream"
                        run_logger.mark_skipped(reason)
                        self.console.log(f"[yellow]Skipping[/yellow] {media_path} – {reason}")
                        return PipelineResult(media_path, None, True, reason, run_id)

                    with status(
                        f"Extracting English audio to PCM f32 {self.config.sample_rate} Hz"
                    ), run_logger.step("Extract English audio"):
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
                            "Vocal separation"
                        ):
                            self.config.tools.isolate_vocals(
                                extracted,
                                vocals,
                                self.config.fv4_model,
                                self.config.fv4_config,
                                prefer_gpu=self.config.separation_prefer_gpu,
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
                        "FFmpeg preprocessing"
                    ):
                        self.config.tools.preprocess_audio(
                            separated_source,
                            preprocessed,
                            filter_chain=filter_chain,
                        )

                    with status("Running ASR and subtitle post-processing"), run_logger.step("ASR pipeline"):
                        engine = (self.config.asr_engine or "whisper").strip().lower()
                        if engine in {"", "default"}:
                            engine = "whisper"

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
                                write_srt,
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
                            )
                            run_logger.log(f"Whisper segments: {len(events)}")
                        elif engine == "parakeet":
                            from .engine_parakeet import generate_optimized_events, get_parakeet_device_config
                            from .engine_whisper import correct_text_only_with_gemini, write_srt

                            device, compute_type = get_parakeet_device_config(
                                prefer_gpu=self.config.prefer_gpu,
                            )
                            run_logger.log(
                                "ASR engine: parakeet "
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

                        write_srt(events, str(output_path))
                finally:
                    # Time deletion of the per-run temp directory
                    with run_logger.step("Cleanup run temporary directory"):
                        tmp_ctx.cleanup()

        except Exception as exc:
            self.console.log(f"[bold red]Pipeline failed[/bold red] {media_path}: {exc}")
            return PipelineResult(media_path, None, True, str(exc), run_id)

        self._show_summary(media_path, output_path)
        return PipelineResult(media_path, output_path, False, run_id=run_id)

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
