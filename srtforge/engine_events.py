"""
Shared subtitle segmentation, shaping, and timing utilities.

Extracted from engine_whisper to keep engine implementations in sync.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

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
    """Initial shaping based on word-level timestamps."""
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
    Re-shapes corrected text to match the EXACT original logic.
    Rule: If >= 2 words, split into balanced lines.
    """
    clean = text.replace("\n", " ").strip()
    words = clean.split()

    if len(words) < 2:
        return clean

    cut_idx = get_balanced_split_index(words, max_chars)
    return f"{' '.join(words[:cut_idx])}\n{' '.join(words[cut_idx:])}".strip()


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


__all__ = [
    "apply_extension_then_merge",
    "apply_global_start_offset",
    "apply_hybrid_linger_with_report",
    "enforce_timing_constraints",
    "get_balanced_split_index",
    "reshape_text_string",
    "segment_smart_stream",
    "shape_block_text",
]
