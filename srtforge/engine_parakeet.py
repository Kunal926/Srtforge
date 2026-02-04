"""
Parakeet ASR engine integration (NeMo).

IMPORTANT: Do NOT import torch/nemo at module import time.
"""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .engine_events import (
    apply_extension_then_merge,
    apply_global_start_offset,
    apply_hybrid_linger_with_report,
    enforce_timing_constraints,
    segment_smart_stream,
    shape_block_text,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParakeetEngineConfig:
    model: str = "nvidia/parakeet-tdt-0.6b-v3"
    language: str = "en"
    prefer_gpu: bool = True


_MODEL_CACHE: Dict[Tuple[str, str], Any] = {}


def _detect_cuda_available() -> bool:
    try:
        import torch  # heavy; only inside worker process paths

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def get_parakeet_device_config(*, prefer_gpu: bool) -> Tuple[str, str]:
    device = "cuda" if (prefer_gpu and _detect_cuda_available()) else "cpu"
    compute_type = "float16" if device == "cuda" else "float32"
    return device, compute_type


def load_parakeet_model(model_name: str, *, prefer_gpu: bool = True) -> Any:
    device, _compute_type = get_parakeet_device_config(prefer_gpu=prefer_gpu)
    cache_key = (model_name, device)
    if cache_key in _MODEL_CACHE:
        return _MODEL_CACHE[cache_key]

    logger.info("Loading Parakeet model '%s' (device=%s)...", model_name, device)

    from nemo.collections.asr.models import ASRModel, EncDecRNNTBPEModel  # type: ignore

    try:
        model = EncDecRNNTBPEModel.from_pretrained(model_name)
    except Exception:
        model = ASRModel.from_pretrained(model_name)

    import torch  # type: ignore

    if device == "cuda":
        model = model.to(device)
        if hasattr(torch.cuda, "amp"):
            torch.set_float32_matmul_precision("high")
    else:
        model = model.to("cpu")

    model.eval()
    _MODEL_CACHE[cache_key] = model
    return model


def preload_parakeet_model(model_name: str = "nvidia/parakeet-tdt-0.6b-v3", *, prefer_gpu: bool = True) -> None:
    _ = load_parakeet_model(model_name, prefer_gpu=prefer_gpu)


def _normalize_word_timestamp_entries(entries: Sequence[Any]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in entries:
        if isinstance(item, dict):
            word = item.get("word") or item.get("text") or ""
            start = item.get("start")
            end = item.get("end")
        elif isinstance(item, (list, tuple)) and len(item) >= 3:
            word, start, end = item[0], item[1], item[2]
        else:
            continue
        if start is None or end is None:
            continue
        text = str(word or "").strip()
        if text:
            normalized.append({"word": text, "start": float(start), "end": float(end)})
    return normalized


def _extract_word_timestamps_from_hypothesis(hyp: Any) -> List[Dict[str, Any]]:
    if hyp is None:
        return []
    for attr in ("word_timestamps", "words", "word_ts"):
        data = getattr(hyp, attr, None)
        if data:
            if isinstance(data, dict) and "word" in data:
                return _normalize_word_timestamp_entries(data["word"])
            if isinstance(data, (list, tuple)):
                return _normalize_word_timestamp_entries(data)
    timestamps = getattr(hyp, "timestamps", None)
    if isinstance(timestamps, dict) and "word" in timestamps:
        return _normalize_word_timestamp_entries(timestamps["word"])
    return []


def _call_timestamp_helper(func: Any, model: Any, audio_path: str, transcript: str) -> Optional[List[Dict[str, Any]]]:
    call_patterns = [
        ((), {"model": model, "audio_file": audio_path, "transcript": transcript}),
        ((), {"asr_model": model, "audio_file": audio_path, "transcript": transcript}),
        ((), {"model": model, "audio_path": audio_path, "transcript": transcript}),
        ((), {"audio_file": audio_path, "transcript": transcript}),
        ((model, audio_path, transcript), {}),
        ((audio_path, transcript), {}),
    ]
    for args, kwargs in call_patterns:
        try:
            result = func(*args, **kwargs)
        except Exception:
            continue
        if result:
            if isinstance(result, dict) and "word" in result:
                return _normalize_word_timestamp_entries(result["word"])
            if isinstance(result, (list, tuple)):
                return _normalize_word_timestamp_entries(result)
    return None


def _derive_word_timestamps_with_alignment(model: Any, audio_path: str, transcript: str) -> List[Dict[str, Any]]:
    try:
        from nemo.collections.asr.parts.utils import timestamp_utils  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "NeMo did not return word timestamps and the timestamp_utils module is unavailable."
        ) from exc

    helpers = [
        getattr(timestamp_utils, name, None)
        for name in (
            "get_word_timestamps",
            "get_word_timestamps_from_alignment",
            "get_word_timestamps_from_hypothesis",
            "extract_word_timestamps",
        )
    ]

    for func in helpers:
        if func is None:
            continue
        result = _call_timestamp_helper(func, model, audio_path, transcript)
        if result:
            return result

    sigs = []
    for func in helpers:
        if func is None:
            continue
        try:
            sigs.append(f"{func.__name__}{inspect.signature(func)}")
        except Exception:
            sigs.append(func.__name__)
    raise RuntimeError(
        "Unable to derive word timestamps from NeMo. Tried helpers: " + ", ".join(sigs)
    )


_PARAKEET_V3_LANGS = {
    "bg",
    "hr",
    "cs",
    "da",
    "nl",
    "en",
    "et",
    "fi",
    "fr",
    "de",
    "el",
    "hu",
    "it",
    "lv",
    "lt",
    "mt",
    "pl",
    "pt",
    "ro",
    "sk",
    "sl",
    "es",
    "sv",
    "ru",
    "uk",
}


def _resolve_language(model_name: str, language: str) -> str:
    requested = (language or "en").strip().lower()
    model_id = model_name.lower()
    if "parakeet-tdt-0.6b-v2" in model_id:
        if requested != "en":
            logger.warning("Parakeet v2 supports English only; forcing language to 'en'.")
        return "en"
    if "parakeet-tdt-0.6b-v3" in model_id:
        if requested not in _PARAKEET_V3_LANGS:
            logger.warning("Unsupported Parakeet v3 language '%s'; falling back to 'en'.", requested)
            return "en"
        return requested
    return requested


def _transcribe_with_timestamps(model: Any, audio_path: str, *, language: Optional[str] = None) -> Tuple[str, List[Dict[str, Any]]]:
    transcribe_kwargs: Dict[str, Any] = {
        "paths2audio_files": [audio_path],
        "return_hypotheses": True,
    }
    if language:
        try:
            sig = inspect.signature(model.transcribe)
        except Exception:
            sig = None
        if sig and "language" in sig.parameters:
            transcribe_kwargs["language"] = language
        elif sig and "lang" in sig.parameters:
            transcribe_kwargs["lang"] = language
    try:
        outputs = model.transcribe(**transcribe_kwargs, timestamps=True)
    except TypeError:
        outputs = model.transcribe(**transcribe_kwargs)

    hyp = outputs[0] if isinstance(outputs, (list, tuple)) and outputs else outputs
    transcript = ""
    if isinstance(hyp, str):
        transcript = hyp
    else:
        transcript = getattr(hyp, "text", "") or getattr(hyp, "transcript", "") or ""

    words = _extract_word_timestamps_from_hypothesis(hyp)
    if not words and transcript:
        words = _derive_word_timestamps_with_alignment(model, audio_path, transcript)

    return transcript, words


def generate_optimized_events(
    audio_path: str,
    *,
    model_name: str = "nvidia/parakeet-tdt-0.6b-v3",
    language: str = "en",
    prefer_gpu: bool = True,
    pause_ms: int = 400,
    max_chars: int = 84,
    max_dur_s: float = 7.0,
    word_timestamps_out: Optional[str] = None,
) -> List[Dict[str, Any]]:
    logger.info("Generating optimized events with Parakeet (NeMo)... model=%s language=%s", model_name, language)
    model = load_parakeet_model(model_name, prefer_gpu=prefer_gpu)

    resolved_language = _resolve_language(model_name, language)
    transcript, words = _transcribe_with_timestamps(model, audio_path, language=resolved_language)
    if not words and transcript:
        logger.warning("Parakeet returned no word timestamps; derived alignment may be required.")

    if word_timestamps_out:
        path = Path(word_timestamps_out)
        with path.open("w", encoding="utf-8") as fp:
            json.dump(words, fp, ensure_ascii=False, indent=2)

    events = segment_smart_stream(words, pause_ms=pause_ms, max_chars=max_chars, max_dur_s=max_dur_s)
    events = apply_global_start_offset(events, offset_ms=50)
    events = apply_extension_then_merge(events, target_cps=22.0)
    events = apply_hybrid_linger_with_report(events, linger_ms=600)

    for ev in events:
        ev["text"] = shape_block_text(ev["words"], max_chars=42)

    events = enforce_timing_constraints(events, min_dur=1.0, min_gap=0.084)
    return events


__all__ = [
    "ParakeetEngineConfig",
    "generate_optimized_events",
    "get_parakeet_device_config",
    "load_parakeet_model",
    "preload_parakeet_model",
]
