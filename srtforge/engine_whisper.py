"""
Faster-Whisper + (optional) Gemini correction engine.

This module contains the transcription + post-processing logic adapted from the user's
reference `whisper.py`, but refactored for:
  - strict lazy imports of heavy libraries (torch, faster_whisper, google.genai)
  - reuse inside a persistent CLI worker process

IMPORTANT: Do NOT import torch/faster_whisper/google.genai at module import time.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .engine_events import (
    apply_extension_then_merge,
    apply_global_start_offset,
    apply_hybrid_linger_with_report,
    enforce_timing_constraints,
    reshape_text_string,
    segment_smart_stream,
    shape_block_text,
)
from .utils import parse_media_context_from_filename

logger = logging.getLogger(__name__)

def _fmt_ms(t: float) -> str:
    return f"{int(t//3600):02}:{int((t%3600)//60):02}:{int(t%60):02},{int((t*1000)%1000):03}"


def write_srt(events: Sequence[Dict[str, Any]], out_path: str) -> None:
    with open(out_path, "w", encoding="utf-8") as f:
        for i, ev in enumerate(events, 1):
            f.write(f"{i}\n{_fmt_ms(float(ev['start']))} --> {_fmt_ms(float(ev['end']))}\n{(ev.get('text') or '').strip()}\n\n")


# ==============================================================================
#  PART 4: FASTER-WHISPER + GEMINI (lazy imports + caching)
# ==============================================================================


@dataclass(frozen=True)
class WhisperEngineConfig:
    model: str = "large-v3-turbo"
    language: str = "en"
    prefer_gpu: bool = True


# Process-local cache so the worker loads the model once.
_MODEL_CACHE: Dict[Tuple[str, str, str], Any] = {}  # key=(model, device, compute_type) -> WhisperModel


def _detect_cuda_available() -> bool:
    """Try torch first (common), otherwise fall back to ctranslate2 when available."""
    try:
        import torch  # heavy; but only called inside worker process paths

        return bool(torch.cuda.is_available())
    except Exception:
        pass
    try:
        import ctranslate2  # type: ignore

        return bool(ctranslate2.get_cuda_device_count() > 0)
    except Exception:
        return False


def get_whisper_device_config(*, prefer_gpu: bool = True) -> Tuple[str, str]:
    """Return the device/compute_type pair used by Faster-Whisper."""
    device = "cuda" if (prefer_gpu and _detect_cuda_available()) else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type


def load_whisper_model(model_name: str, *, prefer_gpu: bool = True) -> Any:
    """
    Lazily load and cache a Faster-Whisper WhisperModel instance.

    Returns an instance of faster_whisper.WhisperModel, but we intentionally type as Any
    to avoid importing faster_whisper at module import time.
    """
    device, compute_type = get_whisper_device_config(prefer_gpu=prefer_gpu)
    cache_key = (model_name, device, compute_type)

    logger.info("ASR device: %s compute: %s model: %s", device, compute_type, model_name)
    if cache_key in _MODEL_CACHE:
        return _MODEL_CACHE[cache_key]

    # Heavy import is strictly inside this function.
    from faster_whisper import WhisperModel  # type: ignore

    logger.info("ASR device: %s compute: %s model: %s", device, compute_type, model_name)
    logger.info("Loading Faster-Whisper model '%s' (device=%s, compute_type=%s)...", model_name, device, compute_type)
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    _MODEL_CACHE[cache_key] = model
    return model


def preload_whisper_model(model_name: str = "large-v3-turbo", *, prefer_gpu: bool = True) -> None:
    """Convenience preload hook for the CLI worker startup."""
    _ = load_whisper_model(model_name, prefer_gpu=prefer_gpu)


def generate_optimized_events(
    audio_path: str,
    *,
    model_name: str = "large-v3-turbo",
    language: str = "en",
    prefer_gpu: bool = True,
    pause_ms: int = 400,
    max_chars: int = 84,
    max_dur_s: float = 7.0,
    word_timestamps_out: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    1) Transcribe with Faster-Whisper (word timestamps)
    2) Optionally dump raw word timestamps (unmodified from Faster-Whisper)
    3) Segment with smart streaming rules
    4) Apply timing fixes + shaping (exact logic from reference whisper.py)
    """
    logger.info("Generating optimized events with Faster-Whisper...")
    model = load_whisper_model(model_name, prefer_gpu=prefer_gpu)

    # The WhisperModel.transcribe API yields segments; keep the same flags as the reference.
    segments, _info = model.transcribe(
        audio_path,
        language=language,
        word_timestamps=True,
        condition_on_previous_text=False,
        vad_filter=False,
    )

    raw_words: List[Dict[str, Any]] = []
    all_words: List[Dict[str, Any]] = []
    for s in segments:
        # faster_whisper returns s.words with .word/.start/.end
        for w in getattr(s, "words", []) or []:
            raw_word = getattr(w, "word", "")
            raw_words.append({"word": raw_word, "start": float(w.start), "end": float(w.end)})
            t = (raw_word or "").strip()
            if t:
                all_words.append({"word": t, "start": float(w.start), "end": float(w.end)})

    if word_timestamps_out:
        path = Path(word_timestamps_out)
        with path.open("w", encoding="utf-8") as fp:
            json.dump(raw_words, fp, ensure_ascii=False, indent=2)

    events = segment_smart_stream(all_words, pause_ms=pause_ms, max_chars=max_chars, max_dur_s=max_dur_s)
    events = apply_global_start_offset(events, offset_ms=50)
    events = apply_extension_then_merge(events, target_cps=22.0)
    events = apply_hybrid_linger_with_report(events, linger_ms=600)

    for ev in events:
        ev["text"] = shape_block_text(ev["words"], max_chars=42)

    events = enforce_timing_constraints(events, min_dur=1.0, min_gap=0.084)
    return events


def correct_text_only_with_gemini(
    audio_path: str,
    events: List[Dict[str, Any]],
    *,
    api_key: Optional[str] = None,
    model_id: str = "gemini-3-flash-preview",
) -> List[Dict[str, Any]]:
    """
    Upload audio to Gemini and request text-only corrections.

    IMPORTANT:
      - This updates ONLY ev['text'] (no timestamp changes).
      - It then reshapes text using the same forced-split logic as the reference.
    """
    effective_key = (api_key or os.environ.get("SRTFORGE_GEMINI_API_KEY") or "").strip()
    if not effective_key:
        raise ValueError(
            "Gemini API key not configured. Set SRTFORGE_GEMINI_API_KEY or provide api_key in config."
        )

    # Heavy imports inside the function.
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore

    logger.info("Uploading audio to Gemini for text-only correction...")
    client = genai.Client(api_key=effective_key)
    file_ref = client.files.upload(file=audio_path)

    # Try to extract show/episode context from the (working) audio filename.
    # The pipeline names working WAVs with a prefix like:
    #   "Show Name (2021) - S01E01 - Episode Title - preprocessed.wav"
    audio_basename = str(audio_path).replace("\\", "/").split("/")[-1]
    ctx = parse_media_context_from_filename(audio_basename)

    show_context_lines: List[str] = []
    if ctx.show or ctx.season_episode or ctx.episode_number or ctx.episode_title:
        show_context_lines.append("SHOW CONTEXT (parsed from filename):")
        if ctx.show:
            show_context_lines.append(f"- Show: {ctx.show}")
        episode_bits: List[str] = []
        if ctx.season_episode:
            episode_bits.append(ctx.season_episode)
        if ctx.episode_number:
            episode_bits.append(ctx.episode_number)
        if ctx.episode_title:
            episode_bits.append(ctx.episode_title)
        if episode_bits:
            show_context_lines.append(f"- Episode: {' - '.join(episode_bits)}")
        show_context_lines.append(f"- Source filename: {audio_basename}")
    else:
        show_context_lines.append(f"Source filename: {audio_basename}")
    show_context = "\n".join(show_context_lines).strip()

    payload_lines: List[str] = []
    for i, ev in enumerate(events, 1):
        clean_text = str(ev.get("text") or "").replace("\n", " ")
        payload_lines.append(f"{i}|{clean_text}")
    full_payload = "\n".join(payload_lines)

    prompt = f"""
You are a professional subtitle editor.
I will provide a list of subtitle lines in the format 'ID|Text'.
The audio file is provided for context.

{show_context}

TASK:
1. Listen to the audio to identify correct Name spellings (Context: Anime).
   * Pay attention to Character Names, Locations, and specific Terminology.
   * Maintain standard romanization for Japanese(Anime) names (e.g. 'Satou', 'Kyouma').
2. Fix phonetic typos and capitalization.
3. STRICTLY follow the STYLE GUIDE below.

STYLE GUIDE:
- Ellipses: Use the single char (…, U+2026). Do NOT use three dots.
  * Use to indicate trailing off or pauses >2s.
  * NO space after ellipsis at start of line (e.g., "…and then").
- Numbers & Decades:
  * Decades: "1950s" or "'50s".
  * Ages: Always use numerals (e.g., "He is 5").
  * Times: "9:30 a.m.", "a.m./p.m." (lowercase). Spell out "noon", "midnight", "half past", "quarter of".
  * "o'clock": Spell out the number (e.g., "eleven o'clock").
- Punctuation:
  * Exclamation marks (!): Use ONLY for shouting/surprise. Avoid overuse.
  * Interrobangs (?!): Allowed for emphatic disbelief (e.g., "What did you say?!").
  * Ampersands (&): Only in initialisms (e.g., "R&B").
  * Hashtags (#): Allowed if mentioned (e.g., "#winning"). Spell out "hashtag" if used as a verb.

OUTPUT FORMAT:
- Output ONLY the corrected list in 'ID|Corrected Text' format.
- Do NOT include timestamps.
- Do NOT merge or split lines. Keep line count identical.

INPUT DATA:
""".strip()

    config = types.GenerateContentConfig(
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
    )

    response = client.models.generate_content(
        model=model_id,
        config=config,
        contents=[prompt, file_ref, full_payload],
    )

    corrected_map: Dict[int, str] = {}
    raw_response = (response.text or "").strip()
    for line in raw_response.split("\n"):
        if "|" in line:
            parts = line.split("|", 1)
            if len(parts) == 2 and parts[0].strip().isdigit():
                idx = int(parts[0].strip())
                new_text = parts[1].strip()
                corrected_map[idx] = new_text

    logger.info("Gemini returned %d corrected lines.", len(corrected_map))

    update_count = 0
    for i, ev in enumerate(events, 1):
        if i in corrected_map:
            new_text = corrected_map[i]
            if new_text and str(ev.get("text") or "").replace("\n", " ") != new_text:
                ev["text"] = reshape_text_string(new_text, max_chars=42)
                update_count += 1

    logger.info("Updated %d lines with Gemini corrections (reshaped).", update_count)
    return events
