"""FFmpeg/ffprobe utilities used by the pipeline."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from .logging import get_console


@dataclass(slots=True)
class AudioStream:
    """Representation of an audio stream reported by ``ffprobe``."""

    index: int
    codec_name: str
    language: Optional[str]
    channels: Optional[int]
    sample_rate: Optional[int]

    @classmethod
    def from_probe(cls, data: dict) -> "AudioStream":
        tags = data.get("tags", {}) or {}
        language = tags.get("language") or tags.get("LANGUAGE")
        sample_rate = int(data["sample_rate"]) if data.get("sample_rate") else None
        channels = int(data.get("channels")) if data.get("channels") else None
        return cls(
            index=int(data["index"]),
            codec_name=data.get("codec_name", "unknown"),
            language=language,
            channels=channels,
            sample_rate=sample_rate,
        )


class FFmpegError(RuntimeError):
    """Raised when FFmpeg or ffprobe exits with a failure."""


class FFmpegTooling:
    """Thin wrapper around FFmpeg/ffprobe commands."""

    def __init__(self, ffmpeg_bin: str = "ffmpeg", ffprobe_bin: str = "ffprobe") -> None:
        self.ffmpeg_bin = shutil.which(ffmpeg_bin) or ffmpeg_bin
        self.ffprobe_bin = shutil.which(ffprobe_bin) or ffprobe_bin

    def probe_audio_streams(self, media: Path) -> List[AudioStream]:
        """Return the list of audio streams contained in ``media``."""

        command = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index,codec_name,channels,sample_rate:stream_tags=language,LANGUAGE",
            "-of",
            "json",
            str(media),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise FFmpegError(result.stderr.strip() or "ffprobe failed")
        payload = json.loads(result.stdout or "{}")
        return [AudioStream.from_probe(stream) for stream in payload.get("streams", [])]

    def extract_audio_stream(
        self,
        media: Path,
        stream_index: int,
        output: Path,
        sample_rate: int = 44100,
        channels: int = 2,
    ) -> Path:
        """Extract an audio stream to PCM float at ``sample_rate`` and ``channels``."""

        filter_chain = f"aresample=resampler=soxr:osf=flt:osr={sample_rate}:precision=33"
        command = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(media),
            "-map",
            f"0:{stream_index}",
            "-af",
            filter_chain,
            "-c:a",
            "pcm_f32le",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            str(output),
        ]
        self._run(command)
        return output

    def preprocess_audio(
        self,
        source: Path,
        destination: Path,
        *,
        filter_chain: Optional[str] = None,
    ) -> Path:
        """Apply preprocessing filters producing 16 kHz mono float output."""

        chain = filter_chain or (
            "highpass=f=60,lowpass=f=10000,aformat=sample_fmts=flt,"
            "aresample=resampler=soxr:osf=flt:ocl=mono:osr=16000"
        )

        command = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(source),
            "-vn",
            "-af",
            chain,
            "-acodec",
            "pcm_f32le",
            str(destination),
        ]
        self._run(command)
        return destination

    def isolate_vocals(self, source: Path, destination: Path, model: Path, config: Path) -> Path:
        """Run MelBand Roformer separation to keep vocals only."""

        try:
            from audio_separator.separator import Separator
        except Exception as exc:  # pragma: no cover - import errors propagated
            raise RuntimeError("audio-separator is required for vocal isolation") from exc

        # ``audio-separator`` stores intermediate data in output directory and returns the
        # paths through ``Separator.separate()``. We keep the canonical stems location.
        model_dir = model.parent
        os.environ.setdefault("AUDIO_SEPARATOR_MODEL_DIR", str(model_dir))
        target_config = model_dir / config.name
        if config.exists() and not target_config.exists():
            shutil.copyfile(config, target_config)

        separator = Separator(
            model_file_dir=str(model_dir),
            output_dir=str(destination.parent),
            output_format="WAV",
            output_single_stem="vocals",
            use_autocast=False,
        )
        separator.load_model(model_filename=str(model.name))
        torch_spec = importlib.util.find_spec("torch")
        if torch_spec is not None:
            torch = importlib.import_module("torch")
            model_instance = getattr(separator, "model_instance", None)
            if model_instance is not None:
                module = getattr(model_instance, "model", model_instance)
                if hasattr(module, "to"):
                    module.to(dtype=torch.float32)
        outputs = separator.separate(str(source))
        vocals_path = None
        for item in outputs or []:
            path_obj = Path(item)
            if "vocals" in path_obj.stem.lower():
                vocals_path = path_obj
                break
        if not vocals_path or not vocals_path.exists():
            raise RuntimeError("Vocal separation failed to produce a vocals stem")
        vocals_path.replace(destination)
        return destination

    def _run(self, command: Iterable[str]) -> None:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            get_console().log("[bold red]FFmpeg error[/bold red]", " ".join(command))
            raise FFmpegError(result.stderr.strip() or "ffmpeg failed")


DEFAULT_TOOLS = FFmpegTooling()
