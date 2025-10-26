"""Processing pipeline for the Sonarr-driven offline SRT generation flow."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from rich.table import Table

from .asr.parakeet_engine import parakeet_to_srt_with_alt8
from .config import DEFAULT_OUTPUT_SUFFIX, FV4_CONFIG, FV4_MODEL, MODELS_DIR, PARAKEET_MODEL
from .ffmpeg import DEFAULT_TOOLS, AudioStream, FFmpegTooling
from .logging import get_console, status
from .settings import settings
from .utils import probe_video_fps


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
    ffmpeg_filter_chain: str = settings.ffmpeg.filter_chain
    ffmpeg_prefer_center: bool = settings.ffmpeg.prefer_center
    force_float32: bool = settings.parakeet.force_float32
    prefer_gpu: bool = True


@dataclass(slots=True)
class PipelineResult:
    """Summary of a completed pipeline run."""

    media_path: Path
    output_path: Optional[Path]
    skipped: bool
    reason: Optional[str] = None


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
        if self.config.temp_dir:
            self.config.temp_dir.mkdir(parents=True, exist_ok=True)
            tmp_kwargs["dir"] = str(self.config.temp_dir)

        try:
            with tempfile.TemporaryDirectory(**tmp_kwargs) as tmp_dir:
                tmp = Path(tmp_dir)
                extracted = tmp / "english.wav"
                vocals = tmp / "vocals.wav"
                preprocessed = tmp / "preprocessed.wav"

                # Step 2: probe audio streams -------------------------------------------------
                streams = self.config.tools.probe_audio_streams(media_path)
                english_stream = self._select_english_stream(streams)
                if not english_stream:
                    reason = "no English audio stream"
                    self.console.log(f"[yellow]Skipping[/yellow] {media_path} – {reason}")
                    return PipelineResult(media_path, None, True, reason)

                # Step 3: extract PCM float 48 kHz stereo -----------------------------------
                with status(
                    f"Extracting English audio to PCM f32 {self.config.sample_rate} Hz"
                ):
                    self.config.tools.extract_audio_stream(
                        media_path,
                        english_stream.index,
                        extracted,
                        sample_rate=self.config.sample_rate,
                        channels=2,
                    )

                # Step 4: vocal separation ---------------------------------------------------
                separated_source = extracted
                backend = (self.config.separation_backend or "fv4").lower()
                if backend == "fv4":
                    with status("Running FV4 MelBand Roformer vocal separation"):
                        self.config.tools.isolate_vocals(
                            extracted,
                            vocals,
                            self.config.fv4_model,
                            self.config.fv4_config,
                        )
                    separated_source = vocals
                elif backend in {"none", "skip"}:
                    separated_source = extracted
                else:
                    raise ValueError(f"Unsupported separation backend: {self.config.separation_backend}")

                # Step 5: preprocessing filters ---------------------------------------------
                filter_chain = self.config.ffmpeg_filter_chain
                if (
                    filter_chain
                    and self.config.ffmpeg_prefer_center
                    and english_stream.channels
                    and english_stream.channels >= 3
                    and "pan=" not in filter_chain
                ):
                    filter_chain = f"pan=mono|c0=c2,{filter_chain}"
                with status("Applying FFmpeg preprocessing filters"):
                    self.config.tools.preprocess_audio(
                        separated_source,
                        preprocessed,
                        filter_chain=filter_chain,
                    )

                # Step 6: ASR with Parakeet + alt-8 post-processing -------------------------
                with status("Running Parakeet ASR with alt-8 post-processing"):
                    fps = probe_video_fps(media_path)
                    nemo_local = self._resolve_parakeet_checkpoint()
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    parakeet_to_srt_with_alt8(
                        preprocessed,
                        output_path,
                        fps=fps,
                        nemo_local=nemo_local,
                        force_float32=self.config.force_float32,
                        prefer_gpu=self.config.prefer_gpu,
                    )

        except Exception as exc:
            self.console.log(f"[bold red]Pipeline failed[/bold red] {media_path}: {exc}")
            return PipelineResult(media_path, None, True, str(exc))

        self._show_summary(media_path, output_path)
        return PipelineResult(media_path, output_path, False)

    # ---- internal methods --------------------------------------------------------
    def _show_summary(self, media: Path, srt: Path) -> None:
        table = Table(title="srtforge summary", show_header=True, header_style="bold magenta")
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
        if not english_streams:
            return None
        if self.config.separation_prefer_center:
            for stream in english_streams:
                if stream.channels == 1:
                    return stream
        return english_streams[0]

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
