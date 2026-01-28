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

logger = logging.getLogger(__name__)

# ==============================================================================
#  PART 1: HELPERS & SHAPING (from reference whisper.py)
# ==============================================================================

HARD_PUNCT: Tuple[str, ...] = (".", "!", "?", "…", ":", ";")
SOFT_PUNCT: Tuple[str, ...] = (",",)


def _wtext(w: Dict[str, Any]) -> str:
    return (w.get("word") or "").strip()


def get_balanced_split_index(words: List[str], max_chars: int) -> int:
    """Calculates the best index to split a list of words into two balanced lines."""
    if len(words) < 2:
        return len(words)
    best_cut = -1
    best_score = -float("inf")

    for i in range(len(words) - 1):
        l1 = " ".join(words[: i + 1])
        l2 = " ".join(words[i + 1 :])
        len1, len2 = len(l1), len(l2)

        score = 0.0
        if len1 > max_chars:
            score -= 5000
        if len2 > max_chars:
            score -= 5000
        score -= abs(len1 - len2) * 5.0
        if words[i].endswith(HARD_PUNCT):
            score += 5
        elif words[i].endswith(SOFT_PUNCT):
            score += 3
        if len2 >= len1:
            score += 1

        if score > best_score:
            best_score, best_cut = score, i + 1

    return len(words) // 2 if best_cut == -1 else best_cut


def shape_block_text(words: List[Dict[str, Any]], max_chars: int = 42) -> str:
    """Initial shaping based on Whisper word-level timestamps."""
    toks = [_wtext(w) for w in words]
    if not toks:
        return ""
    # ORIGINAL RULE: If 2 or more words, ALWAYS split.
    if len(toks) >= 2:
        cut_idx = get_balanced_split_index(toks, max_chars)
        return f"{' '.join(toks[:cut_idx])}\n{' '.join(toks[cut_idx:])}".strip()
    return " ".join(toks)


def reshape_text_string(text: str, max_chars: int = 42) -> str:
    """
    Re-shapes Gemini text to match the EXACT original logic.
    Rule: If >= 2 words, split into balanced lines.
    """
    clean = text.replace("\n", " ").strip()
    words = clean.split()

    if len(words) < 2:
        return clean

    cut_idx = get_balanced_split_index(words, max_chars)
    return f"{' '.join(words[:cut_idx])}\n{' '.join(words[cut_idx:])}".strip()


# ==============================================================================
#  PART 2: SMART STREAMING SEGMENTATION (from reference whisper.py)
# ==============================================================================


def find_best_split_point_in_buffer(words: List[Dict[str, Any]]) -> int:
    if len(words) < 2:
        return 1
    full_text = " ".join(_wtext(w) for w in words)
    target_len = len(full_text) / 2
    best_idx = -1
    best_score = -float("inf")
    current_len = 0
    for i in range(len(words) - 1):
        w = words[i]
        nxt = words[i + 1]
        score = 0.0
        current_len += len(_wtext(w)) + 1
        dist = abs(current_len - target_len)
        score -= dist * 1.5
        gap = (nxt["start"] - w["end"])
        if gap > 0:
            score += gap * 200.0
        txt = _wtext(w)
        if txt.endswith(HARD_PUNCT):
            score += 50.0
        elif txt.endswith(SOFT_PUNCT):
            score += 25.0
        if score > best_score:
            best_score, best_idx = score, i + 1
    return best_idx if best_idx != -1 else len(words) // 2


def segment_smart_stream(
    words: Sequence[Dict[str, Any]],
    pause_ms: int = 400,
    max_chars: int = 84,
    max_dur_s: float = 7.0,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    buf: List[Dict[str, Any]] = []
    buf_start = 0.0

    def create_event(word_list: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not word_list:
            return None
        return {
            "start": float(word_list[0]["start"]),
            "end": float(word_list[-1]["end"]),
            "words": word_list,
            "text": "",
        }

    for i, w in enumerate(words):
        if not buf:
            buf_start = float(w["start"])
        buf.append(w)

        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = ((float(nxt["start"]) - float(w["end"])) * 1000.0) if nxt else 0.0
        if gap >= pause_ms:
            ev = create_event(buf)
            if ev:
                out.append(ev)
            buf = []
            continue

        current_text = " ".join(_wtext(x) for x in buf)
        current_dur = float(w["end"]) - buf_start
        if len(current_text) > max_chars or current_dur > max_dur_s:
            split_idx = find_best_split_point_in_buffer(buf)
            ev1 = create_event(buf[:split_idx])
            if ev1:
                out.append(ev1)
            buf = buf[split_idx:]
            if buf:
                buf_start = float(buf[0]["start"])

    if buf:
        ev = create_event(buf)
        if ev:
            out.append(ev)
    return out


# ==============================================================================
#  PART 3: TIMING FIXES (from reference whisper.py)
# ==============================================================================


def apply_global_start_offset(events: List[Dict[str, Any]], offset_ms: int = 50) -> List[Dict[str, Any]]:
    offset_s = offset_ms / 1000.0
    for ev in events:
        ev["start"] += offset_s
        if ev["start"] >= ev["end"]:
            ev["end"] = ev["start"] + 0.1
    return events


def apply_extension_then_merge(
    events: List[Dict[str, Any]],
    target_cps: float = 22.0,
    max_silence_s: float = 1.0,
    max_chars_total: int = 84,
    min_gap: float = 0.084,
) -> List[Dict[str, Any]]:
    if not events:
        return []
    i = 0
    while i < len(events):
        ev = events[i]
        txt_len = len(" ".join(_wtext(w) for w in ev["words"]))
        dur = float(ev["end"]) - float(ev["start"])
        cps = txt_len / max(0.01, dur)
        if cps <= target_cps and dur >= 1.0:
            i += 1
            continue

        next_ev = events[i + 1] if i < len(events) - 1 else None
        gap_next = (float(next_ev["start"]) - float(ev["end"])) if next_ev else 999.0

        needed = txt_len / target_cps
        missing = max(0.0, needed - dur)
        if dur + missing < 1.0:
            missing = 1.0 - dur

        extended = False
        if missing > 0:
            rn = max(0.0, gap_next - min_gap)
            if rn > 0:
                take_next = min(missing, rn)
                ev["end"] += take_next
                if take_next >= missing or take_next > 0.3:
                    extended = True

        if extended:
            i += 1
            continue

        merged = False
        prev_ev = events[i - 1] if i > 0 else None
        gap_prev = (float(ev["start"]) - float(prev_ev["end"])) if prev_ev else 999.0
        gap_next = (float(next_ev["start"]) - float(ev["end"])) if next_ev else 999.0
        min_g = min(gap_prev, gap_next)
        if min_g <= max_silence_s:
            side = "prev" if gap_prev <= gap_next else "next"
            if side == "prev" and prev_ev is not None:
                new_w = prev_ev["words"] + ev["words"]
                if len(" ".join(_wtext(w) for w in new_w)) <= max_chars_total:
                    prev_ev["words"] = new_w
                    prev_ev["end"] = ev["end"]
                    events.pop(i)
                    i -= 1
                    merged = True
            elif side == "next" and next_ev is not None:
                new_w = ev["words"] + next_ev["words"]
                if len(" ".join(_wtext(w) for w in new_w)) <= max_chars_total:
                    ev["words"] = new_w
                    ev["end"] = next_ev["end"]
                    events.pop(i + 1)
                    merged = True

        if not merged:
            i += 1

    return events


def apply_hybrid_linger_with_report(events: List[Dict[str, Any]], linger_ms: int = 600) -> List[Dict[str, Any]]:
    linger_s = linger_ms / 1000.0
    MIN_GAP = 0.084
    CHAIN_THRESHOLD = 0.500
    FORBIDDEN_MIDPOINT = (MIN_GAP + CHAIN_THRESHOLD) / 2.0

    for i in range(len(events)):
        ev = events[i]
        if i == len(events) - 1:
            ev["end"] += linger_s
        else:
            next_start = float(events[i + 1]["start"])
            desired_end = float(ev["end"]) + linger_s
            potential_gap = next_start - desired_end

            if potential_gap >= CHAIN_THRESHOLD:
                ev["end"] = desired_end
            elif potential_gap <= MIN_GAP:
                ev["end"] = next_start - MIN_GAP
            else:
                if potential_gap < FORBIDDEN_MIDPOINT:
                    ev["end"] = next_start - MIN_GAP
                else:
                    ev["end"] = next_start - CHAIN_THRESHOLD

            if ev["end"] <= ev["start"]:
                ev["end"] = float(ev["start"]) + 0.1
    return events


def enforce_timing_constraints(events: List[Dict[str, Any]], min_dur: float = 1.0, min_gap: float = 0.084) -> List[Dict[str, Any]]:
    for i in range(len(events) - 1):
        if float(events[i + 1]["start"]) - float(events[i]["end"]) < min_gap:
            events[i]["end"] = float(events[i + 1]["start"]) - min_gap
            if float(events[i]["end"]) <= float(events[i]["start"]):
                events[i]["end"] = float(events[i]["start"]) + 0.1
    return events


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
    2) Segment with smart streaming rules
    3) Apply timing fixes + shaping (exact logic from reference whisper.py)
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

    payload_lines: List[str] = []
    for i, ev in enumerate(events, 1):
        clean_text = str(ev.get("text") or "").replace("\n", " ")
        payload_lines.append(f"{i}|{clean_text}")
    full_payload = "\n".join(payload_lines)

    prompt = """
You are a professional subtitle editor.
I will provide a list of subtitle lines in the format 'ID|Text'.
The audio file is provided for context.

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
