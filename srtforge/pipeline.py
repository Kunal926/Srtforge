"""Processing pipeline for the Sonarr-driven offline SRT generation flow."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional


from rich.table import Table

from .asr.parakeet_engine import parakeet_to_srt
from .config import DEFAULT_OUTPUT_SUFFIX, FV4_CONFIG, FV4_MODEL, MODELS_DIR, PARAKEET_MODEL
from .ffmpeg import DEFAULT_TOOLS, AudioStream, FFmpegTooling
from .logging import RunLogger, get_console, status
from .settings import settings
from .utils import probe_video_fps


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
    model_path: Path = PARAKEET_MODEL
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
    ffmpeg_prefer_center: bool = settings.ffmpeg.prefer_center
    force_float32: bool = settings.parakeet.force_float32
    prefer_gpu: bool = settings.parakeet.prefer_gpu
    allow_untagged_english: bool = settings.separation.allow_untagged_english


@dataclass(slots=True)
class PipelineResult:
    """Summary of a completed pipeline run."""

    media_path: Path
    output_path: Optional[Path]
    skipped: bool
    reason: Optional[str] = None
    run_id: Optional[str] = None


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
                    extracted = tmp / "english.wav"
                    vocals = tmp / "vocals.wav"
                    preprocessed = tmp / "preprocessed.wav"

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
                        self.config.tools.extract_audio_stream(
                            media_path,
                            english_stream.index,
                            extracted,
                            sample_rate=self.config.sample_rate,
                            channels=2,
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

                    filter_chain = self.config.ffmpeg_filter_chain
                    pan_expr = None
                    layout = getattr(english_stream, "channel_layout", None)
                    channels = english_stream.channels or 0
                    has_center = _has_center_channel(layout, channels)
                    if (
                        self.config.ffmpeg_prefer_center
                        and channels >= 2
                        and (not filter_chain or "pan=" not in filter_chain)
                    ):
                        pan_expr = "pan=mono|c0=FC" if has_center else "pan=mono|c0=0.5*FL+0.5*FR"
                    if pan_expr:
                        filter_chain = f"{pan_expr},{filter_chain}" if filter_chain else pan_expr
                    with status("Applying FFmpeg preprocessing filters"), run_logger.step(
                        "FFmpeg preprocessing"
                    ):
                        self.config.tools.preprocess_audio(
                            separated_source,
                            preprocessed,
                            filter_chain=filter_chain,
                        )

                    with status("Running Parakeet ASR and subtitle post-processing"), run_logger.step(
                        "ASR pipeline"
                    ):
                        fps = probe_video_fps(media_path)
                        nemo_local = self._resolve_parakeet_checkpoint()
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        parakeet_to_srt(
                            preprocessed,
                            output_path,
                            fps=fps,
                            nemo_local=nemo_local,
                            force_float32=self.config.force_float32,
                            prefer_gpu=self.config.prefer_gpu,
                            run_logger=run_logger,
                        )

                        # If the SRT is being written next to the media file, avoid
                        # leaving diagnostic sidecars in the media directory. Move
                        # them into the per-run temporary directory so they can be
                        # cleaned up automatically.
                        if output_path.parent == media_path.parent:
                            try:
                                diag_dir = tmp / "diagnostics"
                                diag_dir.mkdir(exist_ok=True)
                                for suffix in (".diag.csv", ".diag.json"):
                                    diag_src = output_path.with_suffix(output_path.suffix + suffix)
                                    if diag_src.exists():
                                        diag_dst = diag_dir / diag_src.name
                                        shutil.move(str(diag_src), str(diag_dst))
                            except Exception:
                                # Diagnostics are best-effort; never fail the run if
                                # moving them fails for any reason (permissions,
                                # cross-device moves, etc.).
                                pass
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

    def _resolve_parakeet_checkpoint(self) -> Optional[Path]:
        """Locate a local Parakeet checkpoint if available."""

        candidate = self.config.model_path
        if candidate and candidate.exists():
            return candidate

        nested = self.config.models_dir / "parakeet" / "parakeet-tdt-0.6b-v2.nemo"
        if nested.exists():
            return nested

        legacy = self.config.models_dir / "parakeet_tdt_0.6b_v2.nemo"
        if legacy.exists():
            return legacy

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
