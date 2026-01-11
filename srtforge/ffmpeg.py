"""FFmpeg/ffprobe utilities used by the pipeline."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence

from .logging import get_console


@dataclass(slots=True)
class AudioStream:
    """Representation of an audio stream reported by ``ffprobe``."""

    index: int
    codec_name: str
    language: Optional[str]
    channels: Optional[int]
    sample_rate: Optional[int]
    channel_layout: Optional[str] = None

    @classmethod
    def from_probe(cls, data: dict) -> "AudioStream":
        tags = data.get("tags", {}) or {}
        language = tags.get("language") or tags.get("LANGUAGE")
        try:
            sample_rate = int(data["sample_rate"]) if data.get("sample_rate") else None
        except (TypeError, ValueError):  # pragma: no cover - defensive against exotic ffprobe output
            sample_rate = None
        channels = int(data.get("channels")) if data.get("channels") else None
        layout = data.get("ch_layout") or data.get("channel_layout") or None
        try:
            index_value = int(data["index"])
        except (TypeError, ValueError, KeyError):  # pragma: no cover - defensive parsing
            index_value = 0
        return cls(
            index=index_value,
            codec_name=data.get("codec_name", "unknown"),
            language=language,
            channels=channels,
            sample_rate=sample_rate,
            channel_layout=layout,
        )


class FFmpegError(RuntimeError):
    """Raised when FFmpeg or ffprobe exits with a failure."""


def _ensure_separator_supports_model(separator: "Separator", model: Path, config: Path | None) -> None:
    """Patch ``separator`` so ``model`` is treated as a supported download."""

    try:
        original_list_supported = separator.list_supported_model_files
    except AttributeError:  # pragma: no cover - defensive safeguard for unexpected API changes
        return

    try:
        existing = original_list_supported()
    except Exception:  # pragma: no cover - if probing fails we fall back to default behaviour
        return

    model_name = model.name

    def _contains(target: dict) -> bool:
        for models in target.values():
            for info in models.values():
                if info.get("filename") == model_name:
                    return True
        return False

    if _contains(existing):
        return

    download_files = [model_name]
    if config is not None and config.exists():
        download_files.append(config.name)

    def patched_list_supported(self):
        data = original_list_supported()
        if not _contains(data):
            mdxc = data.setdefault("MDXC", {})
            mdxc[f"Local {model_name}"] = {
                "filename": model_name,
                "scores": {},
                "stems": [],
                "target_stem": None,
                "download_files": download_files,
            }
        return data

    separator.list_supported_model_files = types.MethodType(patched_list_supported, separator)


def _iter_separator_output_paths(outputs: object) -> Iterable[Path]:
    """Yield :class:`pathlib.Path` objects from ``audio_separator`` outputs."""

    def handle(value: object) -> List[Path]:
        if value is None:
            return []
        if isinstance(value, (str, os.PathLike)):
            return [Path(value)]
        if isinstance(value, Mapping):
            paths: List[Path] = []
            for nested in value.values():
                paths.extend(handle(nested))
            return paths
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, os.PathLike)):
            paths: List[Path] = []
            for nested in value:
                paths.extend(handle(nested))
            return paths
        return []

    return handle(outputs)


def _resolve_separator_output_path(path_obj: Path, output_dir: Path) -> Optional[Path]:
    """Resolve ``audio_separator`` output paths to an existing file."""

    candidates: List[Path] = []
    if path_obj.is_absolute():
        candidates.append(path_obj)
    else:
        candidates.append(output_dir / path_obj)
        candidates.append(output_dir / path_obj.name)
        candidates.append(path_obj)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


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
            "stream=index,codec_name,channels,sample_rate,channel_layout,ch_layout:stream_tags=language,LANGUAGE",
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
        *,
        extraction_mode: str = "stereo_mix",
    ) -> Path:
        """Extract an audio stream to PCM float at ``sample_rate``.

        ``extraction_mode`` controls how multi-channel sources are handled:

        - ``stereo_mix`` (default): Standard stereo downmix of all source channels.
        - ``dual_mono_center``: Extract only the Center (FC) channel and map it to
          both Left and Right (dual-mono stereo). This requires the input stream to
          have a Center channel; callers should probe and fall back to ``stereo_mix``
          when no Center channel is present.
        """

        base_chain = f"aresample=resampler=soxr:osf=flt:osr={sample_rate}:precision=33"
        mode = (extraction_mode or "stereo_mix").strip().lower()
        if mode == "dual_mono_center":
            # Map center (FC) to both L/R.
            filter_chain = f"pan=stereo|c0=FC|c1=FC,{base_chain}"
        else:
            filter_chain = base_chain
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
            "aresample=resampler=soxr:osf=flt:osr=16000"
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
            "-ac",
            "1",
            str(destination),
        ]
        self._run(command)
        return destination

    def isolate_vocals(
        self,
        source: Path,
        destination: Path,
        model: Path,
        config: Path,
        *,
        prefer_gpu: bool = True,
    ) -> Path:
        """Run MelBand Roformer separation to keep vocals only."""

        console = get_console()

        try:
            from audio_separator.separator import Separator
        except ModuleNotFoundError as exc:  # pragma: no cover - explicit dependency message
            missing = exc.name or ""
            if missing.startswith("onnxruntime"):
                raise RuntimeError(
                    "audio-separator requires onnxruntime for vocal isolation. "
                    "Install it with 'pip install onnxruntime' or re-run the installer."
                ) from exc
            raise RuntimeError(
                "audio-separator is required for vocal isolation. "
                "Re-run the installer or install the optional dependency manually."
            ) from exc
        except Exception as exc:  # pragma: no cover - import errors propagated
            raise RuntimeError(
                f"audio-separator failed to initialize for vocal isolation: {exc}"
            ) from exc

        # ``audio-separator`` stores intermediate data in output directory and returns the
        # paths through ``Separator.separate()``. We keep the canonical stems location.
        model_dir = model.parent
        os.environ.setdefault("AUDIO_SEPARATOR_MODEL_DIR", str(model_dir))
        target_config = model_dir / config.name
        config_for_registration: Path | None = None
        if config.exists() and not target_config.exists():
            shutil.copyfile(config, target_config)
            config_for_registration = target_config
        elif target_config.exists():
            config_for_registration = target_config
        elif config.exists():
            config_for_registration = config

        torch_spec = importlib.util.find_spec("torch")
        torch_module = None
        torch_cuda_available = False
        torch_cuda_probe_error: str | None = None
        torch_cuda_built = False
        if torch_spec is not None:
            torch_module = importlib.import_module("torch")
            if prefer_gpu:
                try:
                    torch_cuda_available = bool(torch_module.cuda.is_available())
                except Exception as exc:  # pragma: no cover - defensive fallback if CUDA probing fails
                    torch_cuda_probe_error = str(exc)
                    torch_cuda_available = False
            torch_version = getattr(torch_module, "version", None)
            cuda_version = getattr(torch_version, "cuda", None)
            torch_cuda_built = bool(cuda_version)

        onnx_cuda_available = False
        onnx_probe_error: str | None = None
        if prefer_gpu:
            try:
                import onnxruntime as ort  # type: ignore
            except ModuleNotFoundError:
                onnx_probe_error = "onnxruntime is not installed"
            except Exception as exc:  # pragma: no cover - unexpected import failure
                onnx_probe_error = str(exc)
            else:
                try:
                    onnx_cuda_available = "CUDAExecutionProvider" in set(ort.get_available_providers())
                except Exception as exc:  # pragma: no cover - defensive fallback if provider probing fails
                    onnx_probe_error = str(exc)

        if prefer_gpu and torch_module is not None and not torch_cuda_available:
            if not torch_cuda_built:
                console.log(
                    "[yellow]PyTorch was installed without CUDA support; install a CUDA-enabled build to run FV4 separation on the GPU.[/yellow]"
                )
            elif torch_cuda_probe_error:
                console.log(
                    "[yellow]PyTorch could not query CUDA availability; FV4 separation will fall back to the CPU. "
                    f"({torch_cuda_probe_error})[/yellow]"
                )
            else:
                console.log(
                    "[yellow]CUDA is unavailable to PyTorch; FV4 separation will use the CPU unless CUDA drivers are installed correctly.[/yellow]"
                )
        if prefer_gpu and not onnx_cuda_available and onnx_probe_error:
            console.log(
                "[yellow]Could not verify ONNX Runtime CUDA support; FV4 separation may run on the CPU. "
                f"({onnx_probe_error})[/yellow]"
            )

        separator = Separator(
            model_file_dir=str(model_dir),
            output_dir=str(destination.parent),
            output_format="WAV",
            output_single_stem="vocals",
            use_autocast=bool(prefer_gpu and torch_cuda_available),
        )
        _ensure_separator_supports_model(separator, model, config_for_registration)
        separator.load_model(model_filename=str(model.name))
        if torch_module is not None:
            model_instance = getattr(separator, "model_instance", None)
            if model_instance is not None:
                module = getattr(model_instance, "model", model_instance)
                if hasattr(module, "to"):
                    module.to(dtype=torch_module.float32)

        execution_providers = [prov for prov in getattr(separator, "onnx_execution_provider", []) or []]
        torch_device = getattr(separator, "torch_device", None)
        if prefer_gpu:
            if torch_cuda_available and any(prov == "CUDAExecutionProvider" for prov in execution_providers):
                console.log("[green]FV4 separation is running with CUDA acceleration[/green]")
            elif torch_cuda_available:
                console.log(
                    "[yellow]PyTorch detected CUDA but ONNX Runtime did not expose CUDAExecutionProvider; vocal separation will run on the CPU."
                )
            else:
                console.log(
                    "[yellow]CUDA not available to audio-separator; vocal separation will use the CPU."
                )
        elif torch_device is not None and getattr(torch_device, "type", "cpu") != "cpu":
            console.log(
                "[yellow]Vocal separation GPU preference disabled; forcing CPU execution."
            )
        outputs = separator.separate(str(source))
        vocals_path: Optional[Path] = None
        for path_obj in _iter_separator_output_paths(outputs):
            if "vocals" not in path_obj.stem.lower():
                continue
            resolved = _resolve_separator_output_path(path_obj, destination.parent)
            if resolved is not None:
                vocals_path = resolved
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
