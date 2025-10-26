from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Dict, List, Tuple, Optional, TYPE_CHECKING
import sys

try:  # pragma: no cover - heavy dependency import
    import torch
except Exception as exc:  # pragma: no cover - delay failure until used
    torch = None  # type: ignore[assignment]
    _TORCH_IMPORT_ERROR = exc
else:  # pragma: no cover - torch import succeeded
    _TORCH_IMPORT_ERROR = None

try:  # pragma: no cover - heavy dependency import
    import nemo.collections.asr as nemo_asr
except Exception as exc:  # pragma: no cover - delay failure until used
    nemo_asr = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc

# Make the vendored post-processing modules importable via their original module names.
POST_DIR = Path(__file__).resolve().parents[1] / "post"
if str(POST_DIR) not in sys.path:
    sys.path.insert(0, str(POST_DIR))

from srt_utils import postprocess_segments, write_srt  # type: ignore  # noqa: E402
from ..logging import RunLogger


if TYPE_CHECKING:  # pragma: no cover - type checking only
    import torch as torch_module

    TorchDType = torch_module.dtype
else:  # pragma: no cover - runtime fallback for type checkers
    TorchDType = object


def load_parakeet(
    nemo_local: Optional[Path] = None,
    force_float32: bool = True,
    prefer_gpu: bool = True,
) -> Tuple[nemo_asr.models.ASRModel, Optional[TorchDType], bool]:
    """Load the Parakeet-TDT-0.6B-V2 model, optionally from a local .nemo."""

    if nemo_asr is None:  # pragma: no cover - import error surfaced lazily
        raise RuntimeError("NVIDIA NeMo is required for Parakeet transcription") from _IMPORT_ERROR

    if torch is None:  # pragma: no cover - surfaced when dependency missing
        raise RuntimeError("PyTorch is required for Parakeet transcription") from _TORCH_IMPORT_ERROR

    use_cuda = prefer_gpu and torch.cuda.is_available()
    if nemo_local and nemo_local.exists():
        asr = nemo_asr.models.ASRModel.restore_from(restore_path=str(nemo_local))
    else:
        asr = nemo_asr.models.ASRModel.from_pretrained(
            model_name="nvidia/parakeet-tdt-0.6b-v2", strict=False
        )

    dtype: Optional[TorchDType] = torch.float32
    if use_cuda:
        asr = asr.cuda()
        if force_float32:
            asr = asr.to(dtype=torch.float32)
            dtype = torch.float32
        else:
            if hasattr(torch.cuda, "is_bf16_supported") and torch.cuda.is_bf16_supported():
                asr = asr.to(dtype=torch.bfloat16)
                dtype = torch.bfloat16
            else:
                asr = asr.half()
                dtype = torch.float16
    asr.eval()
    return asr, dtype, use_cuda


def _build_segments_from_hypothesis(hypothesis) -> List[Dict[str, object]]:
    """Recreate the segment/word mapping used by the alt-8 CLI script."""

    timestamps = getattr(hypothesis, "timestamp", None) or {}
    segment_ts = timestamps.get("segment") or []
    word_ts = timestamps.get("word") or []

    segments: List[Dict[str, object]] = []
    if segment_ts:
        for segment in segment_ts:
            text_val = segment.get("segment")
            start = segment.get("start")
            end = segment.get("end")
            if text_val is None or start is None or end is None:
                continue
            words: List[Dict[str, object]] = []
            if word_ts:
                for word in word_ts:
                    w_start = word.get("start")
                    w_end = word.get("end")
                    token = word.get("word")
                    if w_start is None or w_end is None or token is None:
                        continue
                    if w_start >= start and w_end <= end:
                        words.append({
                            "word": str(token),
                            "start": float(w_start),
                            "end": float(w_end),
                        })
            segments.append({
                "start": float(start),
                "end": float(end),
                "text": str(text_val),
                "words": words,
            })
        return segments

    if word_ts:
        word_list = [
            {
                "word": str(item["word"]),
                "start": float(item["start"]),
                "end": float(item["end"]),
            }
            for item in word_ts
            if item.get("word") and item.get("start") is not None and item.get("end") is not None
        ]
        if word_list:
            return [{
                "start": word_list[0]["start"],
                "end": word_list[-1]["end"],
                "text": " ".join(word["word"] for word in word_list),
                "words": word_list,
            }]

    text_attr = getattr(hypothesis, "text", "")
    if text_attr:
        return [{"start": 0.0, "end": 0.1, "text": str(text_attr), "words": []}]

    return []


def parakeet_to_srt_with_alt8(
    audio_path: Path,
    srt_out: Path,
    fps: float,
    nemo_local: Optional[Path],
    *,
    force_float32: bool = True,
    prefer_gpu: bool = True,
    run_logger: Optional[RunLogger] = None,
    max_chars_per_line: int = 42,
    pause_ms: int = 240,
    punct_pause_ms: int = 160,
    comma_pause_ms: int = 120,
    cps_target: float = 20.0,
    two_line_threshold: float = 0.60,
    min_two_line_chars: int = 24,
    min_readable: float = 1.20,
    coalesce_gap_ms: int = 360,
    max_block_duration_s: float = 7.0,
    max_merge_gap_ms: int = 360,
) -> List[Dict[str, object]]:
    """Run Parakeet ASR and post-process using the original alt-8 pipeline."""

    step = run_logger.step if run_logger else None

    with (step("ASR: model load") if step else nullcontext()):
        asr, _, use_cuda = load_parakeet(
            nemo_local=nemo_local,
            force_float32=force_float32,
            prefer_gpu=prefer_gpu,
        )
    if run_logger:
        device = "GPU" if use_cuda else "CPU"
        run_logger.log(f"ASR device: {device}")

    with (step("ASR: inference") if step else nullcontext()):
        results = asr.transcribe(
            [str(audio_path)], timestamps=True, return_hypotheses=True
        )
    if not results or not results[0]:
        raise RuntimeError("Parakeet ASR did not return any hypotheses")

    hypothesis = results[0]
    with (step("ASR: post-processing & cleanup") if step else nullcontext()):
        segments = _build_segments_from_hypothesis(hypothesis)

        processed = postprocess_segments(
            segments,
            max_chars_per_line=max_chars_per_line,
            max_lines=2,
            pause_ms=pause_ms,
            punct_pause_ms=punct_pause_ms,
            comma_pause_ms=comma_pause_ms,
            cps_target=cps_target,
            snap_fps=fps,
            use_spacy=True,
            coalesce_gap_ms=coalesce_gap_ms,
            two_line_threshold=two_line_threshold,
            min_readable=min_readable,
            min_two_line_chars=min_two_line_chars,
            max_block_duration_s=max_block_duration_s,
            max_merge_gap_ms=max_merge_gap_ms,
        )
        write_srt(processed, str(srt_out))
    return processed


__all__ = [
    "load_parakeet",
    "parakeet_to_srt_with_alt8",
]
