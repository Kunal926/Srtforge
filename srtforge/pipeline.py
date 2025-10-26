"""Processing pipeline for the Sonarr-driven offline SRT generation flow."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import soundfile as sf
from rich.table import Table

from .config import DEFAULT_OUTPUT_SUFFIX, FV4_CONFIG, FV4_MODEL, PARAKEET_MODEL
from .ffmpeg import DEFAULT_TOOLS, AudioStream, FFmpegTooling
from .logging import get_console, status

try:
    import torch
except Exception:  # pragma: no cover - torch optional during linting
    torch = None  # type: ignore[assignment]


@dataclass(slots=True)
class PipelineConfig:
    """Configuration for a single processing run."""

    media_path: Path
    output_path: Optional[Path] = None
    tools: FFmpegTooling = DEFAULT_TOOLS
    model_path: Path = PARAKEET_MODEL
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


@dataclass(slots=True)
class TranscriptSegment:
    """Single subtitle segment containing timestamps and text."""

    index: int
    start: float
    end: float
    text: str


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

                # Step 3: extract PCM float 44.1 kHz stereo ----------------------------------
                with status("Extracting English audio to PCM f32 44.1 kHz"):
                    self.config.tools.extract_audio_stream(
                        media_path,
                        english_stream.index,
                        extracted,
                        sample_rate=44100,
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

                # Step 6: ASR with Parakeet-TDT-0.6B-V2 ------------------------------------
                with status("Running NeMo Parakeet-TDT-0.6B-V2 ASR"):
                    segments = self._transcribe(preprocessed)

                # Step 7: Emit SRT -----------------------------------------------------------
                with status("Writing SRT"):
                    self._write_srt(output_path, segments)

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

    def _transcribe(self, audio_path: Path) -> List[TranscriptSegment]:
        try:
            from nemo.collections.asr.models import ASRModel
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("NVIDIA NeMo is required for transcription") from exc

        if not self.config.model_path.exists():
            raise FileNotFoundError(
                f"Missing Parakeet checkpoint at {self.config.model_path}. "
                "Run install.sh or install.ps1 to fetch models."
            )

        prefer_gpu = self.config.prefer_gpu and torch is not None and torch.cuda.is_available()
        device = "cuda" if prefer_gpu else "cpu"

        # Reference: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2 (model card)
        model = ASRModel.restore_from(str(self.config.model_path), map_location=device)
        model = model.to(device)
        model.eval()

        hypotheses = model.transcribe(
            paths2audio_files=[str(audio_path)],
            batch_size=1,
            num_workers=0,
            return_hypotheses=True,
            timestamps=True,
        )
        if not hypotheses:
            return [TranscriptSegment(1, 0.0, 0.0, "")]

        hypo = hypotheses[0]
        segments = self._extract_segments(hypo, audio_path)
        if not segments:
            text = getattr(hypo, "text", "").strip()
            duration = self._duration_seconds(audio_path)
            segments = [TranscriptSegment(1, 0.0, duration, text)]
        return segments

    def _extract_segments(self, hypo, audio_path: Path) -> List[TranscriptSegment]:
        segments: List[TranscriptSegment] = []
        raw_segments = getattr(hypo, "segments", None)
        if raw_segments:
            for idx, segment in enumerate(raw_segments, 1):
                text = (getattr(segment, "text", "") or "").strip()
                start = float(getattr(segment, "start_time", 0.0) or 0.0)
                end = float(getattr(segment, "end_time", start))
                if text:
                    segments.append(TranscriptSegment(idx, start, max(end, start + 1e-3), text))
        else:
            # Fallback to grouping word-level timestamps, if available.
            words = getattr(hypo, "words", None)
            if words:
                group = []
                current_start: Optional[float] = None
                current_end: Optional[float] = None
                for word in words:
                    token = word.get("word", "").strip()
                    if not token:
                        continue
                    start = float(word.get("start_time") or word.get("start_offset") or 0.0)
                    end = float(word.get("end_time") or word.get("end_offset") or start)
                    if current_start is None:
                        current_start = start
                    current_end = end
                    group.append(token)
                    if token.endswith( (".", "?", "!")):
                        segments.append(
                            TranscriptSegment(len(segments) + 1, current_start, max(current_end, current_start + 1e-3), " ".join(group))
                        )
                        group = []
                        current_start = None
                        current_end = None
                if group:
                    cs = current_start or 0.0
                    ce = current_end or (cs + 1.0)
                    segments.append(TranscriptSegment(len(segments) + 1, cs, max(ce, cs + 1e-3), " ".join(group)))
        return segments

    def _duration_seconds(self, audio_path: Path) -> float:
        data, sample_rate = sf.read(str(audio_path))
        return float(len(data) / sample_rate) if sample_rate else 0.0

    def _write_srt(self, destination: Path, segments: List[TranscriptSegment]) -> None:
        lines = []
        for segment in segments:
            lines.extend(
                [
                    str(segment.index),
                    f"{self._format_timestamp(segment.start)} --> {self._format_timestamp(segment.end)}",
                    segment.text.strip(),
                    "",
                ]
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("\n".join(lines), encoding="utf-8")

    def _format_timestamp(self, seconds: float) -> str:
        seconds = max(0.0, seconds)
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        millis = int(round((secs - int(secs)) * 1000))
        return f"{hours:02}:{minutes:02}:{int(secs):02},{millis:03}"


def run_pipeline(config: PipelineConfig) -> PipelineResult:
    """Convenience wrapper for launching the pipeline."""

    pipeline = Pipeline(config)
    return pipeline.run()
