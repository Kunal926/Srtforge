"""
Parakeet ASR engine integration.

Currently a thin wrapper around the Whisper implementation to preserve the
pipeline contract. Replace with a real Parakeet backend when available.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .engine_whisper import generate_optimized_events as _whisper_generate_optimized_events
from .engine_whisper import get_whisper_device_config


def get_parakeet_device_config(*, prefer_gpu: bool) -> Tuple[str, str]:
    """Return the runtime device configuration for Parakeet."""

    return get_whisper_device_config(prefer_gpu=prefer_gpu)


def generate_optimized_events(
    audio_path: str,
    *,
    model_name: str,
    language: str,
    prefer_gpu: bool,
    word_timestamps_out: Optional[str] = None,
) -> List[Dict]:
    """
    Generate subtitle events using the Parakeet engine.

    NOTE: Placeholder implementation that proxies to Whisper for now.
    """

    return _whisper_generate_optimized_events(
        audio_path,
        model_name=model_name,
        language=language,
        prefer_gpu=prefer_gpu,
        word_timestamps_out=word_timestamps_out,
    )


__all__ = ["generate_optimized_events", "get_parakeet_device_config"]
