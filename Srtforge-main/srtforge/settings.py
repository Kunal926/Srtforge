
"""Runtime configuration loaded from YAML for srtforge."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Optional, Union, get_args, get_origin

import yaml

from .config import FV4_CONFIG, FV4_MODEL, PACKAGE_ROOT, PROJECT_ROOT

CONFIG_ENV_VAR = "SRTFORGE_CONFIG"
DEFAULT_CONFIG_FILENAME = "config.yaml"

# FFmpeg audio extraction mode values.
#
# - "stereo_mix" (default): standard stereo downmix of all channels.
# - "dual_mono_center": if the source contains a Center channel (FC), extract
#   only FC and map it to both L/R in the output WAV.
EXTRACTION_MODE_STEREO_MIX = "stereo_mix"
EXTRACTION_MODE_DUAL_MONO_CENTER = "dual_mono_center"

# Single persistent config file used by the GUI (and optionally the CLI).
#
# Note: despite the ".config" extension, this file still contains YAML.
PERSISTENT_CONFIG_FILENAME = "srtforge.config"
PERSISTENT_CONFIG_ENV_VAR = "SRTFORGE_PERSISTENT_CONFIG"


def _resolve_path(value: str | Path | None) -> Optional[Path]:
    if value is None:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _unwrap_optional(type_hint: Any) -> Any:
    origin = get_origin(type_hint)
    if origin is Union:
        args = [arg for arg in get_args(type_hint) if arg is not type(None)]
        return args[0] if args else Any
    return type_hint


def get_persistent_config_path() -> Path:
    """Return the default on-disk config file used for persisting GUI/CLI settings.

    Selection order:
      1) $SRTFORGE_PERSISTENT_CONFIG (explicit override)
      2) If running as a frozen executable (PyInstaller), next to the executable
      3) PROJECT_ROOT/srtforge.config (dev checkout / portable runs)
      4) OS user config dir (~/.config/srtforge/srtforge.config or %APPDATA%\\srtforge\\srtforge.config)

    This function does not create files/directories; it only chooses a path.
    """

    override = os.environ.get(PERSISTENT_CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()

    candidates: list[Path] = []

    # PyInstaller / frozen builds: keep config next to the executable for portability.
    if getattr(sys, "frozen", False):  # pragma: no cover
        try:
            candidates.append(Path(sys.executable).resolve().with_name(PERSISTENT_CONFIG_FILENAME))
        except Exception:
            pass

    # Dev checkout / portable folder: write alongside the project root.
    candidates.append((PROJECT_ROOT / PERSISTENT_CONFIG_FILENAME).resolve())

    # User config dir fallback (non-temp, user-writable).
    home = Path.home()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        base_path = Path(base) if base else home
        candidates.append(base_path / "srtforge" / PERSISTENT_CONFIG_FILENAME)
    else:
        base = os.environ.get("XDG_CONFIG_HOME")
        base_path = Path(base) if base else (home / ".config")
        candidates.append(base_path / "srtforge" / PERSISTENT_CONFIG_FILENAME)

    # Prefer an existing config file if present.
    for p in candidates:
        try:
            if p.exists():
                return p
        except Exception:
            continue

    # Otherwise choose the first whose parent looks writable; else last fallback.
    for p in candidates:
        try:
            if p.parent.exists() and os.access(str(p.parent), os.W_OK):
                return p
        except Exception:
            continue

    return candidates[-1]


@dataclass(slots=True)
class PathsSettings:
    """Filesystem locations used by the pipeline."""

    temp_dir: Optional[Path] = None
    output_dir: Optional[Path] = None


@dataclass(slots=True)
class FFmpegSettings:
    """Controls applied to FFmpeg processing steps."""

    # See EXTRACTION_MODE_* constants above.
    extraction_mode: str = EXTRACTION_MODE_STEREO_MIX
    filter_chain: str = (
        "highpass=f=60,lowpass=f=10000,aformat=sample_fmts=flt,"
        "aresample=resampler=soxr:osf=flt:osr=16000"
    )


@dataclass(slots=True)
class FV4Settings:
    """Paths to the MelBand Roformer configuration and checkpoint."""

    cfg: Path = FV4_CONFIG
    ckpt: Path = FV4_MODEL


@dataclass(slots=True)
class SeparationSettings:
    """Options for the vocal separation stage."""

    backend: str = "fv4"
    sep_hz: int = 48000
    prefer_center: bool = False
    prefer_gpu: bool = True
    allow_untagged_english: bool = False
    fv4: FV4Settings = field(default_factory=FV4Settings)


@dataclass(slots=True)
class ParakeetSettings:
    """Configuration forwarded to the Parakeet ASR stage."""

    # Keep float32 enabled by default for maximum compatibility.
    force_float32: bool = True

    # Prefer CUDA when available (mirrors the GUI Device dropdown / CLI --cpu flag).
    prefer_gpu: bool = True

    # Parakeet long-audio local attention window used when change_attention_model()
    # is applied. Lower values reduce VRAM usage at the cost of context/accuracy.
    rel_pos_local_attn: list[int] = field(default_factory=lambda: [768, 768])

    # When enabled, apply asr.change_subsampling_conv_chunking_factor(1) after the
    # model is loaded. This can reduce memory pressure on long audio.
    subsampling_conv_chunking: bool = False

    # Best-effort GPU limiting knob. 100 preserves current behavior.
    # Values < 100 may cap per-process CUDA allocator memory and/or use a lower
    # priority CUDA stream during inference so the desktop stays responsive.
    gpu_limit_percent: int = 100

    # When enabled, Parakeet inference runs on a low-priority CUDA stream to improve
    # desktop responsiveness on display-attached GPUs. This is independent of gpu_limit_percent.
    use_low_priority_cuda_stream: bool = False


@dataclass(slots=True)
class AppSettings:
    """Top-level settings exposed to the rest of the application."""

    paths: PathsSettings = field(default_factory=PathsSettings)
    ffmpeg: FFmpegSettings = field(default_factory=FFmpegSettings)
    separation: SeparationSettings = field(default_factory=SeparationSettings)
    parakeet: ParakeetSettings = field(default_factory=ParakeetSettings)


def _coerce_value(value: Any, target_type: Any) -> Any:
    """Convert ``value`` into ``target_type`` when possible."""

    base_type = _unwrap_optional(target_type)
    if base_type is Path:
        return _resolve_path(value)
    if base_type is str:
        return None if value is None else str(value)
    if base_type is int:
        return None if value is None else int(value)
    if base_type is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        return bool(value)
    return value


def _merge_dataclass(instance: Any, data: dict[str, Any]) -> Any:
    for field_info in fields(instance):
        key = field_info.name
        if key not in data:
            continue
        value = data[key]
        current = getattr(instance, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge_dataclass(current, value)
        else:
            coerced = _coerce_value(value, field_info.type)
            setattr(instance, key, coerced)
    return instance


def load_settings(path: Optional[Path] = None) -> AppSettings:
    """Load configuration from ``path`` falling back to defaults."""

    config_path = path
    if config_path is None:
        env_value = os.environ.get(CONFIG_ENV_VAR)
        if env_value:
            config_path = Path(env_value).expanduser()
        else:
            persistent = get_persistent_config_path()
            if persistent.exists():
                config_path = persistent
            else:
                config_path = PACKAGE_ROOT / DEFAULT_CONFIG_FILENAME
    config = AppSettings()
    missing_low_pri_key = True
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf8") as handle:
            loaded = yaml.safe_load(handle) or {}
        if isinstance(loaded, dict):
            # Backward compatibility: older configs used ffmpeg.prefer_center (bool).
            # Map to ffmpeg.extraction_mode.
            ffmpeg_payload = loaded.get("ffmpeg") if isinstance(loaded.get("ffmpeg"), dict) else {}
            if (
                ffmpeg_payload
                and "extraction_mode" not in ffmpeg_payload
                and "prefer_center" in ffmpeg_payload
            ):
                prefer_center_value = _coerce_value(ffmpeg_payload.get("prefer_center"), bool)
                migrated_mode = (
                    EXTRACTION_MODE_DUAL_MONO_CENTER
                    if bool(prefer_center_value)
                    else EXTRACTION_MODE_STEREO_MIX
                )
                patched_ffmpeg = dict(ffmpeg_payload)
                patched_ffmpeg["extraction_mode"] = migrated_mode
                loaded = dict(loaded)
                loaded["ffmpeg"] = patched_ffmpeg

            parakeet_payload = loaded.get("parakeet") if isinstance(loaded.get("parakeet"), dict) else {}
            missing_low_pri_key = not bool(parakeet_payload and "use_low_priority_cuda_stream" in parakeet_payload)
            _merge_dataclass(config, loaded)

    # Ensure FV4 paths default to package data when left relative
    config.separation.fv4.cfg = _resolve_path(config.separation.fv4.cfg)
    config.separation.fv4.ckpt = _resolve_path(config.separation.fv4.ckpt)
    config.paths.temp_dir = _resolve_path(config.paths.temp_dir)
    config.paths.output_dir = _resolve_path(config.paths.output_dir)

    # Backward compatibility: older configs coupled gpu_limit_percent<100 to low-priority streams.
    if missing_low_pri_key and int(getattr(config.parakeet, "gpu_limit_percent", 100) or 100) < 100:
        config.parakeet.use_low_priority_cuda_stream = True

    return config


settings = load_settings()

__all__ = [
    "AppSettings",
    "EXTRACTION_MODE_DUAL_MONO_CENTER",
    "EXTRACTION_MODE_STEREO_MIX",
    "FFmpegSettings",
    "ParakeetSettings",
    "PathsSettings",
    "SeparationSettings",
    "FV4Settings",
    "PERSISTENT_CONFIG_FILENAME",
    "PERSISTENT_CONFIG_ENV_VAR",
    "get_persistent_config_path",
    "load_settings",
    "settings",
]
