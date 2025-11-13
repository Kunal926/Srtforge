
"""Runtime configuration loaded from YAML for srtforge."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Optional, Union, get_args, get_origin

import yaml

from .config import FV4_CONFIG, FV4_MODEL, PACKAGE_ROOT, PROJECT_ROOT

CONFIG_ENV_VAR = "SRTFORGE_CONFIG"
DEFAULT_CONFIG_FILENAME = "config.yaml"


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


@dataclass(slots=True)
class PathsSettings:
    """Filesystem locations used by the pipeline."""

    temp_dir: Optional[Path] = None
    output_dir: Optional[Path] = None


@dataclass(slots=True)
class FFmpegSettings:
    """Controls applied to FFmpeg processing steps."""

    prefer_center: bool = False
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
    fv4: FV4Settings = field(default_factory=FV4Settings)


@dataclass(slots=True)
class ParakeetSettings:
    """Configuration forwarded to the Parakeet ASR stage."""

    force_float32: bool = True
    prefer_gpu: bool = True


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
            config_path = PACKAGE_ROOT / DEFAULT_CONFIG_FILENAME
    config = AppSettings()
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf8") as handle:
            payload = yaml.safe_load(handle) or {}
        if isinstance(payload, dict):
            _merge_dataclass(config, payload)
    # Ensure FV4 paths default to package data when left relative
    config.separation.fv4.cfg = _resolve_path(config.separation.fv4.cfg)
    config.separation.fv4.ckpt = _resolve_path(config.separation.fv4.ckpt)
    config.paths.temp_dir = _resolve_path(config.paths.temp_dir)
    config.paths.output_dir = _resolve_path(config.paths.output_dir)
    return config


settings = load_settings()

__all__ = [
    "AppSettings",
    "FFmpegSettings",
    "ParakeetSettings",
    "PathsSettings",
    "SeparationSettings",
    "FV4Settings",
    "load_settings",
    "settings",
]
