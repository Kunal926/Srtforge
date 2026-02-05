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

try:  # pragma: no cover - optional dependency
    import soundfile as sf
except Exception:  # pragma: no cover - defer failure until used
    sf = None  # type: ignore[assignment]

from .engine_events import (
    apply_extension_then_merge,
    apply_global_start_offset,
    apply_hybrid_linger_with_report,
    enforce_timing_constraints,
    segment_smart_stream,
    shape_block_text,
)

logger = logging.getLogger(__name__)


LONG_AUDIO_THRESHOLD_S = 480.0
DEFAULT_REL_POS_LOCAL_ATTN: Tuple[int, int] = (768, 768)


@dataclass(frozen=True)
class ParakeetEngineConfig:
    model: str = "nvidia/parakeet-tdt-0.6b-v3"
    language: str = "en"
    prefer_gpu: bool = True


_MODEL_CACHE: Dict[Tuple[str, str], Any] = {}


def _normalize_rel_pos_local_attn_window(value: Optional[Sequence[int]]) -> List[int]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            left = int(value[0])
            right = int(value[1])
        except Exception:
            left, right = DEFAULT_REL_POS_LOCAL_ATTN
    else:
        left, right = DEFAULT_REL_POS_LOCAL_ATTN

    if left <= 0 or right <= 0:
        left, right = DEFAULT_REL_POS_LOCAL_ATTN
    return [left, right]


def _probe_audio_duration_seconds(path: Path) -> Optional[float]:
    if sf is None:
        return None

    try:
        info = sf.info(str(path))
    except Exception:
        return None

    if not info.samplerate or not info.frames:
        return None
    return float(info.frames) / float(info.samplerate)


def _maybe_apply_long_audio_settings(
    model: Any,
    audio_path: str,
    *,
    rel_pos_local_attn: Optional[Sequence[int]] = None,
) -> None:
    duration_s = _probe_audio_duration_seconds(Path(audio_path))
    if duration_s is None or duration_s <= LONG_AUDIO_THRESHOLD_S:
        return

    desired_window = tuple(_normalize_rel_pos_local_attn_window(rel_pos_local_attn))
    applied_window = getattr(model, "_parakeet_rel_pos_local_attn_window", None)
    if applied_window == desired_window:
        return

    if not hasattr(model, "change_attention_model"):
        logger.warning(
            "Parakeet model does not support change_attention_model; skipping long-audio settings."
        )
        return

    logger.info(
        "Audio duration %.1fs exceeds %.1fs; applying long-audio Parakeet settings "
        "(rel_pos_local_attn=%s).",
        duration_s,
        LONG_AUDIO_THRESHOLD_S,
        list(desired_window),
    )
    model.change_attention_model("rel_pos_local_attn", list(desired_window))
    setattr(model, "_parakeet_rel_pos_local_attn_window", desired_window)


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


def _enable_parakeet_timestamping(model: Any) -> None:
    """Force NeMo/Parakeet to emit word/segment/char timestamps.

    NeMo models commonly store decoding configuration in OmegaConf's DictConfig.
    DictConfig supports both attribute-style and mapping-style access, but some
    objects may be read-only depending on how they were constructed. To be
    defensive across NeMo/NVIDIA releases, we try multiple set/get patterns.
    """

    if getattr(model, "_parakeet_timestamping_enabled", False):
        return

    def _cfg_get(obj: Any, key: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        getter = getattr(obj, "get", None)
        if callable(getter):
            try:
                return getter(key)
            except Exception:
                pass
        try:
            return obj[key]  # type: ignore[index]
        except Exception:
            return getattr(obj, key, None)

    def _cfg_set(obj: Any, key: str, value: Any) -> None:
        if obj is None:
            return
        if isinstance(obj, dict):
            obj[key] = value
            return
        # OmegaConf DictConfig reliably supports item assignment.
        try:
            obj[key] = value  # type: ignore[index]
            return
        except Exception:
            pass
        try:
            setattr(obj, key, value)
        except Exception:
            return

    decoding_cfg = None
    for cfg_attr in ("cfg", "config"):
        cfg = getattr(model, cfg_attr, None)
        if cfg is None:
            continue
        # Prefer mapping-style access when available.
        maybe = _cfg_get(cfg, "decoding")
        if maybe is not None:
            decoding_cfg = maybe
            break
        if hasattr(cfg, "decoding"):
            decoding_cfg = getattr(cfg, "decoding")
            break

    if decoding_cfg is None:
        decoding_cfg = getattr(model, "decoding_cfg", None)

    if decoding_cfg is None:
        logger.warning(
            "Parakeet model does not expose decoding config; unable to force preserve_alignments/compute_timestamps."
        )
        return

    # Force at top-level.
    for key in ("preserve_alignments", "compute_timestamps"):
        _cfg_set(decoding_cfg, key, True)

    # NeMo often reads these flags from the active decoding strategy.
    for strategy_key in ("greedy", "beam"):
        sub_cfg = _cfg_get(decoding_cfg, strategy_key)
        if sub_cfg is None:
            sub_cfg = getattr(decoding_cfg, strategy_key, None)
        if sub_cfg is None:
            continue
        for key in ("preserve_alignments", "compute_timestamps"):
            _cfg_set(sub_cfg, key, True)

    # Apply the updated config to the model decoder (best-effort).
    if hasattr(model, "change_decoding_strategy"):
        try:
            model.change_decoding_strategy(decoding_cfg=decoding_cfg)
        except TypeError:
            try:
                model.change_decoding_strategy(decoding_cfg)
            except Exception:
                logger.debug("Failed to apply Parakeet decoding strategy update.", exc_info=True)
        except Exception:
            logger.debug("Failed to apply Parakeet decoding strategy update.", exc_info=True)

    setattr(model, "_parakeet_timestamping_enabled", True)


def _normalize_word_timestamp_entries(entries: Sequence[Any]) -> List[Dict[str, Any]]:
    def _first_present(source: Dict[str, Any], keys: Sequence[str]) -> Any:
        for key in keys:
            if key in source and source[key] is not None:
                return source[key]
        return None

    normalized: List[Dict[str, Any]] = []
    for item in entries:
        if isinstance(item, dict):
            word = _first_present(item, ("word", "text", "token")) or ""
            start = _first_present(item, ("start", "start_time", "start_offset", "begin", "t0"))
            end = _first_present(item, ("end", "end_time", "end_offset", "finish", "t1"))
        elif isinstance(item, (list, tuple)) and len(item) >= 3:
            word, start, end = item[0], item[1], item[2]
        elif all(hasattr(item, attr) for attr in ("start", "end")):
            word = getattr(item, "word", None) or getattr(item, "text", None) or ""
            start = getattr(item, "start", None)
            end = getattr(item, "end", None)
        elif all(hasattr(item, attr) for attr in ("start_offset", "end_offset")):
            word = getattr(item, "word", None) or getattr(item, "text", None) or ""
            start = getattr(item, "start_offset", None)
            end = getattr(item, "end_offset", None)
        else:
            continue
        if start is None or end is None:
            continue
        text = str(word or "").strip()
        if text:
            normalized.append({"word": text, "start": float(start), "end": float(end)})
    return normalized


def _derive_words_from_char_timestamps(entries: Sequence[Any]) -> List[Dict[str, Any]]:
    normalized_chars: List[Dict[str, Any]] = []
    for item in entries:
        if isinstance(item, dict):
            char_text = item.get("word") or item.get("text") or item.get("token")
            start = item.get("start")
            end = item.get("end")
            if start is None:
                start = item.get("start_time") or item.get("start_offset")
            if end is None:
                end = item.get("end_time") or item.get("end_offset")
        else:
            char_text = getattr(item, "word", None) or getattr(item, "text", None)
            start = getattr(item, "start", None)
            end = getattr(item, "end", None)
            if start is None:
                start = getattr(item, "start_offset", None)
            if end is None:
                end = getattr(item, "end_offset", None)

        if char_text is None or start is None or end is None:
            continue
        normalized_chars.append({"word": str(char_text), "start": float(start), "end": float(end)})

    if not normalized_chars:
        return []

    words: List[Dict[str, Any]] = []
    current_text: List[str] = []
    current_start: Optional[float] = None
    current_end: Optional[float] = None

    for token in normalized_chars:
        char_text = str(token.get("word", ""))
        if not char_text:
            continue
        if char_text.isspace():
            if current_text and current_start is not None and current_end is not None:
                words.append({"word": "".join(current_text), "start": current_start, "end": current_end})
            current_text = []
            current_start = None
            current_end = None
            continue

        if current_start is None:
            current_start = float(token["start"])
        current_end = float(token["end"])
        current_text.append(char_text)

    if current_text and current_start is not None and current_end is not None:
        words.append({"word": "".join(current_text), "start": current_start, "end": current_end})

    return words


def _derive_words_from_segment_timestamps(entries: Sequence[Any], transcript: str) -> List[Dict[str, Any]]:
    normalized_segments = _normalize_word_timestamp_entries(entries)
    words = [w for w in str(transcript or "").strip().split() if w]
    if not normalized_segments or not words:
        return []

    start = float(normalized_segments[0]["start"])
    end = float(normalized_segments[-1]["end"])
    if end <= start:
        return []

    total_words = len(words)
    duration = end - start
    generated: List[Dict[str, Any]] = []
    for idx, word in enumerate(words):
        word_start = start + duration * (idx / total_words)
        word_end = start + duration * ((idx + 1) / total_words)
        generated.append({"word": word, "start": word_start, "end": word_end})
    return generated


def _extract_word_timestamps_from_hypothesis(hyp: Any) -> List[Dict[str, Any]]:
    """Extract normalized word timestamps from a NeMo Hypothesis-like object.

    NeMo has changed timestamp container shapes across releases. We therefore
    support:
      - direct fields (word_timestamps / word_ts / words)
      - nested containers (timestamps / timestamp / timestep)
      - helper outputs that return {word:[...], segment:[...], char:[...]} directly
    """

    if hyp is None:
        return []

    def _is_mapping_like(obj: Any) -> bool:
        if obj is None:
            return False
        if isinstance(obj, dict):
            return True
        # Avoid classifying sequences as mappings.
        if isinstance(obj, (list, tuple, str, bytes)):
            return False
        return hasattr(obj, "get") or hasattr(obj, "__getitem__")

    def _get(obj: Any, key: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        getter = getattr(obj, "get", None)
        if callable(getter):
            try:
                return getter(key)
            except Exception:
                pass
        try:
            return obj[key]  # type: ignore[index]
        except Exception:
            return getattr(obj, key, None)

    def _norm(entries: Any) -> List[Dict[str, Any]]:
        if entries is None:
            return []
        if isinstance(entries, dict):
            return []
        try:
            return _normalize_word_timestamp_entries(entries)
        except Exception:
            return []

    transcript = ""
    if _is_mapping_like(hyp):
        transcript = str(_get(hyp, "text") or _get(hyp, "transcript") or "")
    else:
        transcript = str(getattr(hyp, "text", "") or getattr(hyp, "transcript", ""))

    # 0) Some helpers return a dict like {"word": [...], "segment": [...], "char": [...]}.
    if _is_mapping_like(hyp):
        for key in ("word", "words"):
            data = _get(hyp, key)
            if isinstance(data, dict) and "word" in data:
                words = _norm(_get(data, "word") or data.get("word"))
                if words:
                    return words
            else:
                words = _norm(data)
                if words:
                    return words

        char_data = _get(hyp, "char")
        if char_data:
            words = _derive_words_from_char_timestamps(char_data)
            if words:
                return words

        seg_data = _get(hyp, "segment") or _get(hyp, "segments")
        if seg_data and transcript:
            words = _derive_words_from_segment_timestamps(seg_data, transcript)
            if words:
                return words

    # 1) Direct fields that some outputs use.
    if _is_mapping_like(hyp):
        for key in ("word_timestamps", "words", "word_ts"):
            data = _get(hyp, key)
            if isinstance(data, dict) and "word" in data:
                words = _norm(_get(data, "word") or data.get("word"))
                if words:
                    return words
            else:
                words = _norm(data)
                if words:
                    return words

    for attr in ("word_timestamps", "words", "word_ts"):
        data = getattr(hyp, attr, None)
        if not data:
            continue
        if isinstance(data, dict) and "word" in data:
            words = _norm(data.get("word"))
        else:
            words = _norm(data)
        if words:
            return words

    # 2) Timestamp containers: timestamps / timestamp / timestep.
    timestamp_containers: List[Any] = []
    if _is_mapping_like(hyp):
        timestamp_containers.extend([_get(hyp, "timestamps"), _get(hyp, "timestamp"), _get(hyp, "timestep")])
    timestamp_containers.extend([getattr(hyp, "timestamps", None), getattr(hyp, "timestamp", None), getattr(hyp, "timestep", None)])

    for timestamps in timestamp_containers:
        if timestamps is None:
            continue

        if _is_mapping_like(timestamps):
            for key in ("word", "words"):
                data = _get(timestamps, key)
                if isinstance(data, dict) and "word" in data:
                    words = _norm(_get(data, "word") or data.get("word"))
                    if words:
                        return words
                else:
                    words = _norm(data)
                    if words:
                        return words

            char_data = _get(timestamps, "char")
            if char_data:
                words = _derive_words_from_char_timestamps(char_data)
                if words:
                    return words

            seg_data = _get(timestamps, "segment")
            if seg_data and transcript:
                words = _derive_words_from_segment_timestamps(seg_data, transcript)
                if words:
                    return words

        for key in ("word", "words"):
            if hasattr(timestamps, key):
                words = _norm(getattr(timestamps, key))
                if words:
                    return words
        if hasattr(timestamps, "char"):
            words = _derive_words_from_char_timestamps(getattr(timestamps, "char"))
            if words:
                return words
        if hasattr(timestamps, "segment") and transcript:
            words = _derive_words_from_segment_timestamps(getattr(timestamps, "segment"), transcript)
            if words:
                return words

    return []


def _unwrap_first_hypothesis(outputs: Any) -> Any:
    current = outputs
    while isinstance(current, (list, tuple)) and current:
        current = current[0]
    return current


def _iter_output_candidates(outputs: Any) -> Any:
    stack = [outputs]
    while stack:
        current = stack.pop(0)
        if isinstance(current, (list, tuple)):
            stack[0:0] = list(current)
            continue
        yield current


def _extract_transcript_from_outputs(outputs: Any) -> str:
    for candidate in _iter_output_candidates(outputs):
        if isinstance(candidate, str):
            text = candidate.strip()
            if text:
                return text
            continue
        if isinstance(candidate, dict):
            text = str(candidate.get("text") or candidate.get("transcript") or "").strip()
        else:
            text = str(getattr(candidate, "text", "") or getattr(candidate, "transcript", "")).strip()
        if text:
            return text
    return ""


def _extract_words_from_outputs(outputs: Any) -> List[Dict[str, Any]]:
    for candidate in _iter_output_candidates(outputs):
        words = _extract_word_timestamps_from_hypothesis(candidate)
        if words:
            return words
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


def _derive_word_timestamps_with_alignment(
    model: Any,
    audio_path: str,
    transcript: str,
    *,
    outputs: Any = None,
) -> List[Dict[str, Any]]:
    """Best-effort timestamp derivation via NeMo timestamp_utils.

    Different NeMo versions expose different helpers. Some helpers return a list
    of Hypothesis objects (with .timestamp/.timestep populated), while others
    return dicts like {"word": [...], "segment": [...], ...}. This function
    normalizes all of the above into our internal [{"word","start","end"}] list.
    """

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
            "process_timestamp_outputs",
            "process_aed_timestamp_outputs",
        )
    ]

    for func in helpers:
        if func is None:
            continue

        func_name = getattr(func, "__name__", "")

        # Some helpers accept the raw transcribe outputs directly.
        if outputs is not None and func_name in {"process_timestamp_outputs", "process_aed_timestamp_outputs"}:
            try:
                result = func(outputs)
            except Exception:
                continue
            if not result:
                continue

            # Common: helper returns a list of processed Hypothesis objects.
            words = _extract_words_from_outputs(result)
            if words:
                return words

            # Some versions return a dict like {"word": [...], "segment": [...], ...}.
            if isinstance(result, dict):
                for key in ("word", "words"):
                    data = result.get(key)
                    if isinstance(data, dict) and "word" in data:
                        words = _normalize_word_timestamp_entries(data.get("word") or [])
                        if words:
                            return words
                    else:
                        try:
                            words = _normalize_word_timestamp_entries(data)
                        except Exception:
                            words = []
                        if words:
                            return words

                char_data = result.get("char")
                if char_data:
                    words = _derive_words_from_char_timestamps(char_data)
                    if words:
                        return words

                seg_data = result.get("segment") or result.get("segments")
                if seg_data and transcript:
                    words = _derive_words_from_segment_timestamps(seg_data, transcript)
                    if words:
                        return words

            # Some versions return the list of word entries directly (or other iterables).
            try:
                words = _normalize_word_timestamp_entries(result)
            except Exception:
                words = []
            if words:
                return words

            continue

        # Other helpers tend to accept (model, audio_file, transcript) in some shape.
        result = _call_timestamp_helper(func, model, audio_path, transcript)
        if result:
            return result

    sigs: List[str] = []
    for func in helpers:
        if func is None:
            continue
        try:
            sigs.append(f"{func.__name__}{inspect.signature(func)}")
        except Exception:
            sigs.append(getattr(func, "__name__", str(func)))
    helper_details = ", ".join(sigs) if sigs else "<no compatible timestamp helpers found>"
    raise RuntimeError("Unable to derive word timestamps from NeMo. Tried helpers: " + helper_details)

def _fallback_word_timestamps_uniform(audio_path: str, transcript: str) -> List[Dict[str, Any]]:
    """Last-resort word timestamps.

    If NeMo fails to provide/derive timestamps, we generate a monotonic timeline
    by distributing words uniformly across the clip duration. This is less
    accurate than true alignments, but avoids hard pipeline failure.
    """

    tokens = [w for w in (transcript or "").strip().split() if w]
    if not tokens:
        return []

    duration = _probe_audio_duration_seconds(Path(audio_path))
    if duration is None or duration <= 0:
        # Conservative fallback: ~2.5 words/sec (150 wpm).
        duration = max(1.0, len(tokens) / 2.5)

    start = 0.0
    end = float(duration)
    if end <= start:
        end = start + 0.1

    step = (end - start) / max(1, len(tokens))
    out: List[Dict[str, Any]] = []
    t0 = start
    for tok in tokens:
        t1 = t0 + step
        out.append({"word": tok, "start": float(t0), "end": float(t1)})
        t0 = t1
    return out



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
    try:
        sig = inspect.signature(model.transcribe)
    except Exception:
        sig = None

    parameters = sig.parameters if sig else {}
    _enable_parakeet_timestamping(model)
    audio_key_candidates = [
        ("paths2audio_files", True),
        ("audio", False),
        ("audio_files", True),
        ("audio_filepath", False),
        ("audio_file", False),
    ]
    available_audio_keys = [name for name, _expects_list in audio_key_candidates if name in parameters]

    selected_audio_key: Optional[str] = None
    selected_audio_value: Any = None
    for key, expects_list in audio_key_candidates:
        if key not in parameters:
            continue
        selected_audio_key = key
        selected_audio_value = [audio_path] if expects_list else audio_path
        break

    if selected_audio_key is None:
        attempted = ", ".join(name for name, _expects_list in audio_key_candidates)
        available = ", ".join(parameters.keys()) if parameters else "<unknown>"
        raise RuntimeError(
            "Unable to resolve NeMo transcribe audio argument key. "
            f"Attempted keys: [{attempted}]; signature parameters: [{available}]"
        )

    transcribe_kwargs: Dict[str, Any] = {
        selected_audio_key: selected_audio_value,
        "return_hypotheses": True,
    }
    if sig and "batch_size" in sig.parameters:
        transcribe_kwargs["batch_size"] = 1
    if language:
        if sig and "language" in sig.parameters:
            transcribe_kwargs["language"] = language
        elif sig and "lang" in sig.parameters:
            transcribe_kwargs["lang"] = language

    call_variants = [
        {**transcribe_kwargs, "timestamps": True},
        dict(transcribe_kwargs),
    ]
    if "return_hypotheses" in transcribe_kwargs:
        no_hypo = dict(transcribe_kwargs)
        no_hypo.pop("return_hypotheses", None)
        call_variants.append(no_hypo)

    last_type_error: Optional[TypeError] = None
    outputs: Any = None
    for kwargs in call_variants:
        try:
            outputs = model.transcribe(**kwargs)
            break
        except TypeError as exc:
            last_type_error = exc
    else:
        attempted_keys = ", ".join(available_audio_keys or [selected_audio_key])
        raise RuntimeError(
            "All NeMo transcribe compatibility retries failed with TypeError. "
            f"Resolved audio key: '{selected_audio_key}'. "
            f"Detected audio keys in signature: [{attempted_keys}]."
        ) from last_type_error

    transcript = _extract_transcript_from_outputs(outputs)
    words = _extract_words_from_outputs(outputs)
    if not words and transcript:
        try:
            words = _derive_word_timestamps_with_alignment(model, audio_path, transcript, outputs=outputs)
        except Exception as exc:
            logger.warning(
                "Parakeet was unable to provide/derive word timestamps (%s). Falling back to uniform timestamps.",
                exc,
            )
            words = _fallback_word_timestamps_uniform(audio_path, transcript)

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
    force_float32: bool = False,
    rel_pos_local_attn: Optional[Sequence[int]] = None,
    subsampling_conv_chunking_factor: int = 1,
) -> List[Dict[str, Any]]:
    logger.info("Generating optimized events with Parakeet (NeMo)... model=%s language=%s", model_name, language)
    if force_float32:
        logger.info("Parakeet option enabled: force_float32=true")
    if subsampling_conv_chunking_factor > 1:
        logger.info(
            "Parakeet option enabled: subsampling_conv_chunking_factor=%s",
            subsampling_conv_chunking_factor,
        )
    model = load_parakeet_model(model_name, prefer_gpu=prefer_gpu)
    _maybe_apply_long_audio_settings(model, audio_path, rel_pos_local_attn=rel_pos_local_attn)

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
