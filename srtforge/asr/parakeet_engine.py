from __future__ import annotations

import inspect
from contextlib import nullcontext
from pathlib import Path
from typing import Dict, List, Tuple, Optional, TYPE_CHECKING
import sys
import signal

try:  # pragma: no cover - optional dependency
    import soundfile as sf
except Exception:  # pragma: no cover - defer failure until used
    sf = None  # type: ignore[assignment]

# Windows does not define SIGKILL, but NeMo expects it to exist when importing on
# any platform.  Provide a reasonable fallback so the import succeeds.
if not hasattr(signal, "SIGKILL"):
    _sigkill_fallback = getattr(signal, "SIGTERM", getattr(signal, "SIGABRT", 9))
    signal.SIGKILL = _sigkill_fallback  # type: ignore[attr-defined]

from ._nemo_compat import ensure_cuda_python_available, install_megatron_microbatch_stub

install_megatron_microbatch_stub()

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

# Cache so we don't repeatedly tear down the Parakeet model
_MODEL_CACHE = None          # type: ignore[var-annotated]
_MODEL_CACHE_DTYPE = None    # type: ignore[var-annotated]
_MODEL_CACHE_USE_CUDA = None # type: ignore[var-annotated]
_MODEL_CACHE_KEY = None      # type: ignore[var-annotated]

from ..post.srt_utils import postprocess_segments, write_srt  # noqa: E402
from ..logging import RunLogger


LONG_AUDIO_THRESHOLD_S = 480.0


def _probe_audio_duration_seconds(path: Path) -> Optional[float]:
    """Return the duration of ``path`` in seconds when ``soundfile`` is available."""

    if sf is None:
        return None

    try:
        info = sf.info(str(path))
    except Exception:  # pragma: no cover - diagnostic helper
        return None

    if not info.samplerate or not info.frames:
        return None

    return float(info.frames) / float(info.samplerate)


def _log_event(run_logger: Optional[RunLogger], message: str) -> None:
    """Send ``message`` to the active :class:`RunLogger` if present."""

    if run_logger:
        run_logger.log(message)


if TYPE_CHECKING:  # pragma: no cover - type checking only
    import torch as torch_module

    TorchDType = torch_module.dtype
else:  # pragma: no cover - runtime fallback for type checkers
    TorchDType = object


def load_parakeet(
    nemo_local: Optional[Path] = None,
    force_float32: bool = True,
    prefer_gpu: bool = True,
    *,
    run_logger: Optional[RunLogger] = None,
) -> Tuple[nemo_asr.models.ASRModel, Optional[TorchDType], bool]:
    """Load the Parakeet-TDT-0.6B-V2 model, optionally from a local .nemo."""

    if nemo_asr is None:  # pragma: no cover - import error surfaced lazily
        raise RuntimeError("NVIDIA NeMo is required for Parakeet transcription") from _IMPORT_ERROR

    if torch is None:  # pragma: no cover - surfaced when dependency missing
        raise RuntimeError("PyTorch is required for Parakeet transcription") from _TORCH_IMPORT_ERROR

    global _MODEL_CACHE, _MODEL_CACHE_DTYPE, _MODEL_CACHE_USE_CUDA, _MODEL_CACHE_KEY

    cache_key = (
        str(nemo_local.resolve()) if nemo_local else None,
        bool(force_float32),
        bool(prefer_gpu),
    )

    # Reuse an existing model if we're called with the same configuration
    if _MODEL_CACHE is not None and _MODEL_CACHE_KEY == cache_key:
        asr = _MODEL_CACHE
        dtype = _MODEL_CACHE_DTYPE
        use_cuda = bool(_MODEL_CACHE_USE_CUDA)
        _log_event(run_logger, "Reusing cached Parakeet model instance")
        return asr, dtype, use_cuda

    use_cuda = prefer_gpu and torch.cuda.is_available()
    if use_cuda:
        try:
            ensure_cuda_python_available()
        except RuntimeError as exc:
            message = (
                "GPU inference requested but the CUDA runtime is unavailable. "
                "Falling back to CPU execution."
            )
            print(f"{message} ({exc})", file=sys.stderr)
            _log_event(run_logger, message)
            use_cuda = False

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

    # Remember this model so we don't destroy it between calls
    _MODEL_CACHE = asr
    _MODEL_CACHE_DTYPE = dtype
    _MODEL_CACHE_USE_CUDA = use_cuda
    _MODEL_CACHE_KEY = cache_key

    return asr, dtype, use_cuda


def _build_segments_from_hypothesis(hypothesis) -> List[Dict[str, object]]:
    """Recreate the segment/word mapping used by the srtforge CLI."""

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


def parakeet_to_srt(
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
    """Run Parakeet ASR and post-process using the srtforge pipeline."""

    step = run_logger.step if run_logger else None

    with (step("ASR: model load") if step else nullcontext()):
        asr, _, use_cuda = load_parakeet(
            nemo_local=nemo_local,
            force_float32=force_float32,
            prefer_gpu=prefer_gpu,
            run_logger=run_logger,
        )
    if run_logger:
        device = "GPU" if use_cuda else "CPU"
        run_logger.log(f"ASR device: {device}")

    audio_duration_seconds = _probe_audio_duration_seconds(audio_path)
    long_audio_settings_applied = bool(getattr(asr, "_parakeet_long_audio_applied", False))
    if (
        audio_duration_seconds is not None
        and audio_duration_seconds > LONG_AUDIO_THRESHOLD_S
        and not long_audio_settings_applied
    ):
        _log_event(
            run_logger,
            f"Audio duration {audio_duration_seconds:.2f}s exceeded long audio threshold "
            f"{LONG_AUDIO_THRESHOLD_S}s. Applying local attention settings.",
        )
        print(
            f"Audio duration ({audio_duration_seconds:.2f}s) > {LONG_AUDIO_THRESHOLD_S}s. "
            "Applying long audio settings.",
            file=sys.stderr,
        )
        if hasattr(asr, "change_attention_model"):
            try:
                asr.change_attention_model("rel_pos_local_attn", [768, 768])
            except Exception as exc:  # pragma: no cover - defensive logging
                warning = (
                    f"Warning: Failed to apply long audio settings: {exc}. Proceeding without them."
                )
                print(warning, file=sys.stderr)
                _log_event(run_logger, warning)
            else:
                long_audio_settings_applied = True
                setattr(asr, "_parakeet_long_audio_applied", True)
                success_message = "Long audio settings applied: Local Attention."
                print(success_message, file=sys.stderr)
                _log_event(run_logger, success_message)
        else:  # pragma: no cover
            warning = (
                "Warning: Parakeet model does not support change_attention_model; "
                "skipping long audio settings."
            )
            print(warning, file=sys.stderr)
            _log_event(run_logger, warning)

    def _attempt_transcribe() -> List[object]:
        """Call asr.transcribe while handling API differences between NeMo versions."""

        candidates: List[Dict[str, object]] = [
            {"timestamps": True, "return_hypotheses": True},
            {"return_timestamps": "word", "return_hypotheses": True},
            {"return_timestamps": True, "return_hypotheses": True},
            {"return_hypotheses": True},
        ]
        unexpected_kw_error_fragments = (
            "unexpected keyword",
            "got an unexpected keyword",
        )

        for kwargs in candidates:
            try:
                return asr.transcribe([str(audio_path)], **kwargs)
            except TypeError as exc:
                message = str(exc)
                if any(fragment in message for fragment in unexpected_kw_error_fragments):
                    continue
                raise

        try:
            sig = inspect.signature(asr.transcribe)
        except (TypeError, ValueError):
            sig_repr = "<unavailable>"
        else:
            sig_repr = str(sig)
        raise TypeError(
            "Could not call asr.transcribe with timestamp arguments; "
            f"signature: {sig_repr}"
        )

    with (step("ASR: inference") if step else nullcontext()):
        results = _attempt_transcribe()
    if not results or not results[0]:
        raise RuntimeError("Parakeet ASR did not return any hypotheses")

    hypothesis = results[0]

    with (step("ASR: build segments from hypothesis") if step else nullcontext()):
        segments = _build_segments_from_hypothesis(hypothesis)

    with (step("ASR: segmentation & timing normalization") if step else nullcontext()):
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

    result = processed

    with (step("ASR: write SRT + diagnostics") if step else nullcontext()):
        write_srt(result, str(srt_out))

    with (step("ASR: cleanup & GPU cache") if step else nullcontext()):
        try:
            import gc

            try:
                del hypothesis
            except NameError:
                pass
            try:
                del segments
            except NameError:
                pass
            try:
                del processed
            except NameError:
                pass

            if torch is not None:
                try:
                    if use_cuda and torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception as exc:
                    _log_event(
                        run_logger,
                        f"torch.cuda.empty_cache() failed during cleanup: {exc}",
                    )

            try:
                gc.collect()
            except Exception as exc:
                _log_event(run_logger, f"gc.collect() failed during cleanup: {exc}")
        except Exception as exc:
            _log_event(run_logger, f"ASR: cleanup step raised: {exc}")

    # NOTE: we deliberately do NOT delete the model here – it's held in the
    # module-level cache so the heavy destructor doesn't run per file.
    return result


__all__ = [
    "load_parakeet",
    "parakeet_to_srt",
]
