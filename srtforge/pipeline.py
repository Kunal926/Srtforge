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
from .utils import probe_video_fps


@dataclass(slots=True)
class PipelineConfig:
    """Configuration for a single processing run."""

    media_path: Path
    output_path: Optional[Path] = None
    tools: FFmpegTooling = DEFAULT_TOOLS
    model_path: Path = PARAKEET_MODEL
    models_dir: Path = MODELS_DIR
    fv4_model: Path = FV4_MODEL
    fv4_config: Path = FV4_CONFIG
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
        return self.config.media_path.with_suffix(DEFAULT_OUTPUT_SUFFIX)

    # ---- pipeline steps ----------------------------------------------------------
    def run(self) -> PipelineResult:
        media_path = self.config.media_path
        if not media_path.exists():
            return PipelineResult(media_path, None, True, "media missing")

        output_path = self._determine_output_path()
        try:
            with tempfile.TemporaryDirectory(prefix="srtforge_") as tmp_dir:
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
                with status("Extracting English audio to PCM f32 48 kHz"):
                    self.config.tools.extract_audio_stream(
                        media_path,
                        english_stream.index,
                        extracted,
                        sample_rate=48000,
                        channels=2,
                    )

                # Step 4: vocal separation ---------------------------------------------------
                with status("Running FV4 MelBand Roformer vocal separation"):
                    self.config.tools.isolate_vocals(
                        extracted,
                        vocals,
                        self.config.fv4_model,
                        self.config.fv4_config,
                    )

                # Step 5: preprocessing filters ---------------------------------------------
                with status("Applying FFmpeg preprocessing filters"):
                    self.config.tools.preprocess_audio(vocals, preprocessed)

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
                        force_float32=True,
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
        for stream in streams:
            lang = (stream.language or "").lower()
            if lang in {"en", "eng", "english"}:
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
