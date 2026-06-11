from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

import jiwer

from srtforge.ffmpeg import DEFAULT_TOOLS
from srtforge.pipeline import PipelineConfig, run_pipeline
from srtforge.settings import settings


STEINS_DIR = "D:/Shows/Currently Watching/Steins;Gate (2011) [tvdbid-244061]/Season 1"

PARAKEET_V2 = "nvidia/parakeet-tdt-0.6b-v2"
PARAKEET_V3 = "nvidia/parakeet-tdt-0.6b-v3"
WHISPER_MODEL = "large-v3-turbo"

HTML_TAG_RE = re.compile(r"<[^>]+>")
ASS_TAG_RE = re.compile(r"\{[^}]*\}")
SRT_TIMESTAMP_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
)
BRACKET_RE = re.compile(r"\[[^\]]+\]|\([^)]*\)")
SPEAKER_PREFIX_RE = re.compile(r"^\s*(?:[A-Z][A-Z0-9 .'\-]{1,30}:)\s+")
PUNCT_RE = re.compile(r"[^a-z0-9\s']")
SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    title: str
    benchmark_dir: str
    media_path: str
    reference_kind: str
    episode: str = "S01E01"
    season_dir: str | None = None
    assisted_truth_path: str | None = None
    ass_styles: tuple[str, ...] = ("Default",)
    subtitle_stream_spec: str | None = None
    subtitle_stream_index: int | None = None
    sdh_reference: bool = False


@dataclass(frozen=True)
class Variant:
    name: str
    engine: str
    model: str
    separation_backend: str = "none"
    parakeet_precision: str | None = None
    whisper_compute_type: str | None = None
    force_float32: bool = False
    experimental: bool = False
    continuity: bool = False
    rel_pos_local_attn: tuple[int, int] = (1280, 1280)
    subsampling_conv_chunking_factor: int = 0

    @property
    def precision_label(self) -> str:
        if self.engine == "parakeet":
            return self.parakeet_precision or ("fp32" if self.force_float32 else "auto")
        return self.whisper_compute_type or "auto"


DATASETS: dict[str, DatasetSpec] = {
    "steins_s01e22": DatasetSpec(
        key="steins_s01e22",
        title="Steins;Gate S01E22",
        benchmark_dir="benchmark/steinsgate_s01e22_precision",
        media_path="",
        reference_kind="assisted_truth",
        episode="S01E22",
        season_dir=STEINS_DIR,
        assisted_truth_path="benchmark/steinsgate_s01_all_best/truth_assisted/S01E22.truth.txt",
    ),
}


STEINS_S01E22_VARIANTS: tuple[Variant, ...] = (
    Variant("parakeet_v3_raw_auto", "parakeet", PARAKEET_V3, parakeet_precision="auto"),
    Variant("parakeet_v3_raw_fp32", "parakeet", PARAKEET_V3, parakeet_precision="fp32"),
    Variant("parakeet_v3_raw_fp16", "parakeet", PARAKEET_V3, parakeet_precision="fp16"),
    Variant("parakeet_v3_raw_bf16", "parakeet", PARAKEET_V3, parakeet_precision="bf16"),
    Variant("parakeet_v2_raw_auto", "parakeet", PARAKEET_V2, parakeet_precision="auto", continuity=True),
    Variant("parakeet_v2_raw_fp32", "parakeet", PARAKEET_V2, parakeet_precision="fp32", continuity=True),
    Variant("parakeet_v2_raw_fp16", "parakeet", PARAKEET_V2, parakeet_precision="fp16", continuity=True),
    Variant("parakeet_v2_raw_bf16", "parakeet", PARAKEET_V2, parakeet_precision="bf16", continuity=True),
    Variant("whisper_raw_float32", "whisper", WHISPER_MODEL, whisper_compute_type="float32"),
    Variant("whisper_raw_float16", "whisper", WHISPER_MODEL, whisper_compute_type="float16"),
    Variant("whisper_raw_bfloat16", "whisper", WHISPER_MODEL, whisper_compute_type="bfloat16"),
    Variant("whisper_raw_int8", "whisper", WHISPER_MODEL, whisper_compute_type="int8"),
    Variant("whisper_raw_int8_float16", "whisper", WHISPER_MODEL, whisper_compute_type="int8_float16"),
    Variant(
        "srtforge_fv_whisper_float16",
        "whisper",
        WHISPER_MODEL,
        separation_backend="fv4",
        whisper_compute_type="float16",
    ),
    Variant(
        "srtforge_fv_whisper_bfloat16",
        "whisper",
        WHISPER_MODEL,
        separation_backend="fv4",
        whisper_compute_type="bfloat16",
    ),
    Variant(
        "srtforge_fv_whisper_int8_float16",
        "whisper",
        WHISPER_MODEL,
        separation_backend="fv4",
        whisper_compute_type="int8_float16",
    ),
    Variant(
        "srtforge_fv_parakeet_v2_fp32",
        "parakeet",
        PARAKEET_V2,
        separation_backend="fv4",
        parakeet_precision="fp32",
        continuity=True,
    ),
    Variant(
        "srtforge_fv_parakeet_v2_fp16",
        "parakeet",
        PARAKEET_V2,
        separation_backend="fv4",
        parakeet_precision="fp16",
        continuity=True,
    ),
    Variant(
        "srtforge_fv_parakeet_v2_bf16",
        "parakeet",
        PARAKEET_V2,
        separation_backend="fv4",
        parakeet_precision="bf16",
        continuity=True,
    ),
    Variant(
        "srtforge_fv_parakeet_v3_fp32",
        "parakeet",
        PARAKEET_V3,
        separation_backend="fv4",
        parakeet_precision="fp32",
    ),
)


def variants_for_dataset(dataset_key: str) -> list[Variant]:
    if dataset_key == "steins_s01e22":
        return list(STEINS_S01E22_VARIANTS)
    raise ValueError(f"Unknown dataset {dataset_key!r}. Expected one of: {', '.join(DATASETS)}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def clean_spoken_text(text: str, *, sdh: bool = False) -> str:
    text = ASS_TAG_RE.sub("", text)
    text = HTML_TAG_RE.sub("", text)
    text = text.replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("♪", " ")
    pieces: list[str] = []
    for raw_line in text.splitlines() or [text]:
        line = raw_line.strip()
        if not line:
            continue
        if sdh:
            line = SPEAKER_PREFIX_RE.sub("", line)
            line = BRACKET_RE.sub("", line)
        line = SPACE_RE.sub(" ", line).strip()
        if line:
            pieces.append(line)
    return SPACE_RE.sub(" ", " ".join(pieces)).strip()


def normalize_for_wer(text: str, *, sdh: bool = False) -> str:
    text = clean_spoken_text(text, sdh=sdh).lower()
    text = text.replace("’", "'").replace("`", "'")
    text = PUNCT_RE.sub(" ", text)
    text = re.sub(r"\bo\s+f\b", "of", text)
    return SPACE_RE.sub(" ", text).strip()


def parse_ass_time(value: str) -> float:
    h, m, rest = value.strip().split(":")
    s, cs = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100.0


def parse_srt_time(value: str) -> float:
    h, m, rest = value.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_ass_dialogue(path: Path, styles: set[str], *, sdh: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", errors="replace") as fp:
        for line in fp:
            line = line.rstrip("\n")
            if not line.startswith("Dialogue:"):
                continue
            fields = line.split(":", 1)[1].lstrip().split(",", 9)
            if len(fields) < 10:
                continue
            _layer, start, end, style, _name, _ml, _mr, _mv, _effect, text = fields
            if style.strip() not in styles:
                continue
            cleaned = clean_spoken_text(text, sdh=sdh)
            if cleaned:
                rows.append({"start": parse_ass_time(start), "end": parse_ass_time(end), "text": cleaned})
    return rows


def parse_srt(path: Path, *, sdh: bool = False) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    if not path.exists():
        return cues
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [line.strip("\ufeff") for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        ts_idx = next((i for i, line in enumerate(lines) if SRT_TIMESTAMP_RE.search(line)), None)
        if ts_idx is None:
            continue
        match = SRT_TIMESTAMP_RE.search(lines[ts_idx])
        if not match:
            continue
        text = clean_spoken_text("\n".join(lines[ts_idx + 1 :]), sdh=sdh)
        if text:
            cues.append(
                {
                    "start": parse_srt_time(match.group("start")),
                    "end": parse_srt_time(match.group("end")),
                    "text": text,
                }
            )
    return cues


def transcript(cues: list[dict[str, Any]]) -> str:
    return " ".join(cue["text"] for cue in sorted(cues, key=lambda c: (c["start"], c["end"])))


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * pct
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def cue_timing_metrics(cues: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [max(0.0, cue["end"] - cue["start"]) for cue in cues]
    chars = [len(cue["text"].replace("\n", "")) for cue in cues]
    cps = [c / d for c, d in zip(chars, durations) if d > 0]
    gaps = [
        cues[i + 1]["start"] - cues[i]["end"]
        for i in range(len(cues) - 1)
        if cues[i + 1]["start"] >= cues[i]["start"]
    ]
    return {
        "cue_count": len(cues),
        "duration_mean_s": round(mean(durations), 3) if durations else None,
        "duration_median_s": round(median(durations), 3) if durations else None,
        "duration_p95_s": round(percentile(durations, 0.95), 3) if durations else None,
        "cps_mean": round(mean(cps), 3) if cps else None,
        "cps_p95": round(percentile(cps, 0.95), 3) if cps else None,
        "cps_over_20_pct": round(100.0 * sum(v > 20.0 for v in cps) / len(cps), 2) if cps else None,
        "overlap_count": sum(1 for g in gaps if g < 0),
    }


def wer_metrics(reference_text: str, hypothesis_text: str, *, sdh: bool = False) -> dict[str, Any]:
    ref_norm = normalize_for_wer(reference_text, sdh=sdh)
    hyp_norm = normalize_for_wer(hypothesis_text, sdh=False)
    output = jiwer.process_words(ref_norm, hyp_norm)
    return {
        "wer": round(float(output.wer), 5),
        "wer_pct": round(float(output.wer) * 100.0, 2),
        "mer": round(float(output.mer), 5),
        "wil": round(float(output.wil), 5),
        "hits": int(output.hits),
        "substitutions": int(output.substitutions),
        "deletions": int(output.deletions),
        "insertions": int(output.insertions),
        "reference_words": len(ref_norm.split()),
        "hypothesis_words": len(hyp_norm.split()),
    }


def load_diag_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as fp:
        rows = list(csv.DictReader(fp))
    if not rows:
        return {}
    cps = [float(row["cps_float"]) for row in rows if row.get("cps_float")]
    return {
        "diag_cue_count": len(rows),
        "diag_cps_p95": round(percentile(cps, 0.95), 3) if cps else None,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def remove_previous_outputs(run_dir: Path) -> None:
    for name in (
        "subtitle.srt",
        "subtitle.srt.diag.csv",
        "words.json",
        "metrics.json",
        "result.json",
    ):
        path = run_dir / name
        if path.exists():
            path.unlink()


def run_command(command: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def probe_json(media: Path) -> dict[str, Any]:
    result = run_command(
        [
            DEFAULT_TOOLS.ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name:stream_tags=language,title",
            "-of",
            "json",
            str(media),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def find_steins_files(spec: DatasetSpec) -> tuple[Path, Path]:
    season_dir = Path(spec.season_dir or "").expanduser()
    mkvs = sorted(season_dir.glob(f"*{spec.episode}*.mkv"))
    refs = sorted(season_dir.glob(f"*{spec.episode}*.ass"))
    if not mkvs:
        raise FileNotFoundError(f"No MKV matched {spec.episode!r} under {season_dir}")
    if not refs:
        raise FileNotFoundError(f"No ASS matched {spec.episode!r} under {season_dir}")
    return mkvs[0], refs[0]


def resolve_dataset(spec: DatasetSpec, root: Path) -> tuple[Path, Path, list[dict[str, Any]], str]:
    reference_dir = root / "reference"
    reference_dir.mkdir(parents=True, exist_ok=True)
    if spec.reference_kind in {"ass_sidecar", "assisted_truth"}:
        media, source_ref = find_steins_files(spec)
        copied_ass = reference_dir / source_ref.name
        shutil.copy2(source_ref, copied_ass)
        if spec.reference_kind == "assisted_truth":
            if not spec.assisted_truth_path:
                raise ValueError(f"{spec.key} requested assisted truth without assisted_truth_path")
            truth_source = Path(spec.assisted_truth_path)
            if not truth_source.exists():
                raise FileNotFoundError(f"Assisted truth missing: {truth_source}")
            copied_truth = reference_dir / f"{spec.episode}.assisted_truth.txt"
            shutil.copy2(truth_source, copied_truth)
            truth_text = copied_truth.read_text(encoding="utf-8", errors="replace")
            cues = [{"start": 0.0, "end": 0.0, "text": truth_text}]
            return media, copied_truth, cues, str(source_ref)
        cues = parse_ass_dialogue(copied_ass, set(spec.ass_styles), sdh=spec.sdh_reference)
        return media, copied_ass, cues, str(source_ref)

    media = Path(spec.media_path)
    if not media.exists():
        raise FileNotFoundError(f"Media missing: {media}")
    copied_ref = reference_dir / f"{media.stem}.stream_{spec.subtitle_stream_index}_sdh.srt"
    command = [
        DEFAULT_TOOLS.ffmpeg_bin,
        "-y",
        "-i",
        str(media),
        "-map",
        str(spec.subtitle_stream_spec),
        "-f",
        "srt",
        str(copied_ref),
    ]
    result = run_command(command)
    if result.returncode != 0 or not copied_ref.exists():
        raise RuntimeError(result.stderr or result.stdout)
    cues = parse_srt(copied_ref, sdh=spec.sdh_reference)
    return media, copied_ref, cues, f"{media}#{spec.subtitle_stream_spec}"


def parse_stage_times(log_path: Path | None) -> dict[str, float]:
    if log_path is None or not log_path.exists():
        return {}
    text = log_path.read_text(encoding="utf-8", errors="replace")
    stages: dict[str, float] = {}
    for match in re.finditer(r"END ([^-–]+)\s*[–-]\s*([0-9.]+)s", text):
        stages[match.group(1).strip().lower().replace(" ", "_")] = round(float(match.group(2)), 3)
    completed = re.search(r"completed in ([0-9.]+)s", text)
    if completed:
        stages["logged_total"] = round(float(completed.group(1)), 3)
    for match in re.finditer(r"ASR detail: ([^-]+)\s*-\s*([0-9.]+)s", text):
        stages["asr_detail_" + match.group(1).strip().lower().replace(" ", "_")] = round(float(match.group(2)), 3)
    for match in re.finditer(r"ASR detail: (.+?) still running after ([0-9.]+)s", text):
        key = "asr_detail_" + match.group(1).strip().lower().replace(" ", "_") + "_timeout_floor"
        stages[key] = max(stages.get(key, 0.0), round(float(match.group(2)), 3))
    return stages


def normalize_precision_label(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_")
    return {
        "float32": "fp32",
        "torch.float32": "fp32",
        "float": "fp32",
        "float16": "fp16",
        "torch.float16": "fp16",
        "half": "fp16",
        "bfloat16": "bf16",
        "torch.bfloat16": "bf16",
        "mixed": "bf16",
    }.get(text, text)


def extract_effective_precision(engine: str, variant: Variant, log_path: Path | None) -> str | None:
    requested = normalize_precision_label(variant.precision_label)
    if log_path is not None and log_path.exists():
        text = log_path.read_text(encoding="utf-8", errors="replace")
        if engine == "parakeet":
            matches = re.findall(r"Parakeet runtime dtype after (?:long-audio settings|precision policy):\s*([A-Za-z0-9_.]+)", text)
            if matches:
                return normalize_precision_label(matches[-1]) or requested
        elif engine == "whisper":
            match = re.search(r"ASR engine:\s*whisper\s+device:\s*\S+\s+compute:\s*([A-Za-z0-9_]+)", text)
            if not match:
                match = re.search(r"ASR device:\s*\S+\s+compute:\s*([A-Za-z0-9_]+)", text)
            if match:
                return normalize_precision_label(match.group(1)) or requested
    if requested == "auto":
        device_precision = variant.parakeet_precision
        if variant.engine == "parakeet":
            try:
                from srtforge.engine_parakeet import get_parakeet_device_config

                _device, compute_type = get_parakeet_device_config(prefer_gpu=True, precision=device_precision)
                return normalize_precision_label(compute_type) or requested
            except Exception:
                return requested
        return requested
    return requested


def copy_pipeline_log(source: Path, run_dir: Path) -> Path:
    copied = run_dir / "pipeline.log"
    shutil.copy2(source, copied)
    shutil.copy2(source, run_dir / "pipeline_log.txt")
    return copied


def build_config(media: Path, output_path: Path, words_path: Path, variant: Variant, root: Path) -> PipelineConfig:
    return PipelineConfig(
        media_path=media,
        output_path=output_path,
        output_directory=None,
        temp_dir=root / "tmp",
        sample_rate=settings.separation.sep_hz,
        separation_backend=variant.separation_backend,
        separation_prefer_center=settings.separation.prefer_center,
        separation_prefer_gpu=True,
        ffmpeg_filter_chain=settings.ffmpeg.filter_chain,
        ffmpeg_extraction_mode=settings.ffmpeg.extraction_mode,
        prefer_gpu=True,
        asr_engine=variant.engine,
        whisper_model=variant.model,
        whisper_language="en",
        whisper_compute_type=variant.whisper_compute_type,
        parakeet_force_float32=variant.force_float32,
        parakeet_precision=variant.parakeet_precision,
        parakeet_rel_pos_local_attn=list(variant.rel_pos_local_attn),
        parakeet_subsampling_conv_chunking_factor=variant.subsampling_conv_chunking_factor,
        gemini_enabled=False,
        gemini_model_id=settings.gemini.model_id,
        gemini_api_key=None,
        dump_word_timestamps=True,
        word_timestamps_path=words_path,
        allow_untagged_english=False,
    )


def config_snapshot(config: PipelineConfig, variant: Variant) -> dict[str, Any]:
    return {
        "variant": asdict(variant),
        "pipeline": {
            "media_path": str(config.media_path),
            "output_path": str(config.output_path),
            "temp_dir": str(config.temp_dir) if config.temp_dir else None,
            "separation_backend": config.separation_backend,
            "asr_engine": config.asr_engine,
            "whisper_model": config.whisper_model,
            "whisper_language": config.whisper_language,
            "whisper_compute_type": config.whisper_compute_type,
            "parakeet_precision": config.parakeet_precision,
            "parakeet_force_float32": config.parakeet_force_float32,
            "gemini_enabled": config.gemini_enabled,
            "word_timestamps_path": str(config.word_timestamps_path) if config.word_timestamps_path else None,
        },
    }


def run_variant_in_process(
    spec: DatasetSpec,
    variant: Variant,
    *,
    skip_existing: bool = False,
) -> dict[str, Any]:
    root = Path(spec.benchmark_dir)
    media, reference_path, reference_cues, _source = resolve_dataset(spec, root)
    reference_text = transcript(reference_cues)
    probe = probe_json(media)
    media_duration_s = float(probe["format"]["duration"])
    run_dir = root / "runs" / variant.name
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "subtitle.srt"
    words_path = run_dir / "words.json"
    config = build_config(media, output_path, words_path, variant, root)
    write_json(run_dir / "config.json", config_snapshot(config, variant))
    copied_log: Path | None = None
    wall_seconds: float | None = None
    result_payload: dict[str, Any] = {"skipped_existing": skip_existing and output_path.exists()}

    if not result_payload["skipped_existing"]:
        remove_previous_outputs(run_dir)
        write_json(run_dir / "config.json", config_snapshot(config, variant))
        started = time.perf_counter()
        result = run_pipeline(config)
        wall_seconds = time.perf_counter() - started
        result_payload.update(
            {
                "failed": result.failed,
                "error": result.error,
                "run_id": result.run_id,
                "performance_log_path": str(result.performance_log_path) if result.performance_log_path else None,
                "output_path": str(result.output_path) if result.output_path else None,
                "wall_seconds": round(wall_seconds, 3),
            }
        )
        if result.performance_log_path and result.performance_log_path.exists():
            copied_log = copy_pipeline_log(result.performance_log_path, run_dir)
    else:
        copied_log = run_dir / "pipeline.log"
        if not copied_log.exists():
            copied_log = run_dir / "pipeline_log.txt"
        if not copied_log.exists():
            copied_log = None

    write_json(run_dir / "result.json", result_payload)
    return build_metric_payload(
        spec,
        variant,
        media,
        reference_path,
        reference_text,
        output_path,
        words_path,
        copied_log,
        media_duration_s,
        wall_seconds if wall_seconds is not None else result_payload.get("wall_seconds"),
        status=(
            "completed"
            if output_path.exists() and not result_payload.get("failed")
            else "failed"
        ),
        error=result_payload.get("error"),
    )


def build_metric_payload(
    spec: DatasetSpec,
    variant: Variant,
    media: Path,
    reference_path: Path,
    reference_text: str,
    output_path: Path,
    words_path: Path,
    log_path: Path | None,
    media_duration_s: float,
    wall_seconds: float | None,
    *,
    status: str,
    error: str | None = None,
) -> dict[str, Any]:
    cues = parse_srt(output_path, sdh=False)
    generated_text = transcript(cues)
    metric: dict[str, Any] = {
        "dataset": spec.key,
        "status": status,
        "variant": variant.name,
        "engine": variant.engine,
        "model": variant.model,
        "model_short": "parakeet_v3" if variant.model == PARAKEET_V3 else "parakeet_v2" if variant.model == PARAKEET_V2 else variant.model,
        "precision_requested": variant.precision_label,
        "effective_precision": extract_effective_precision(variant.engine, variant, log_path),
        "separation_backend": variant.separation_backend,
        "experimental": variant.experimental,
        "continuity": variant.continuity,
        "reference": str(reference_path),
        "output_srt": str(output_path),
        "word_timestamps": str(words_path) if words_path.exists() else None,
        "media_duration_s": round(media_duration_s, 3),
        "wall_seconds": round(float(wall_seconds), 3) if wall_seconds else None,
        "error": error,
        "stage_times": parse_stage_times(log_path),
    }
    metric["precision"] = metric["effective_precision"] or metric["precision_requested"]
    if metric["wall_seconds"]:
        metric["rtf"] = round(float(metric["wall_seconds"]) / media_duration_s, 5)
    if output_path.exists():
        metric.update(wer_metrics(reference_text, generated_text, sdh=spec.sdh_reference))
        metric.update(cue_timing_metrics(cues))
        metric.update(load_diag_metrics(output_path.with_suffix(output_path.suffix + ".diag.csv")))
        metric["output_sha256"] = sha256_file(output_path)
    write_json(output_path.parent / "metrics.json", metric)
    return metric


def load_metrics(root: Path, variants: list[Variant]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variant in variants:
        path = root / "runs" / variant.name / "metrics.json"
        if path.exists():
            rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


def flatten_metric(row: dict[str, Any]) -> dict[str, Any]:
    flat = {k: v for k, v in row.items() if k != "stage_times"}
    for key, value in (row.get("stage_times") or {}).items():
        flat[f"stage_{key}_s"] = value
    return flat


def write_aggregate(spec: DatasetSpec, manifest: dict[str, Any], variants: list[Variant]) -> None:
    root = Path(spec.benchmark_dir)
    metrics = load_metrics(root, variants)
    write_json(root / "manifest.json", manifest)
    write_json(root / "metrics.json", metrics)
    flats = [flatten_metric(row) for row in metrics]
    fieldnames = sorted({key for row in flats for key in row.keys()})
    with (root / "metrics.csv").open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flats)
    lines = [
        f"# {spec.title} Precision Benchmark",
        "",
        f"Media: `{manifest['media']['path']}`",
        f"Reference: `{manifest['reference']['path']}`",
        f"Reference words: {manifest['reference']['word_count']}",
        "",
        "| Variant | Status | Engine | Model | Precision | WER % | RTF | Wall s | ASR s |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in metrics:
        stages = row.get("stage_times") or {}
        lines.append(
            "| {variant} | {status} | {engine} | {model} | {precision} | {wer} | {rtf} | {wall} | {asr} |".format(
                variant=row.get("variant"),
                status=row.get("status"),
                engine=row.get("engine"),
                model=row.get("model_short"),
                precision=row.get("precision"),
                wer=f"{float(row['wer_pct']):.2f}" if row.get("wer_pct") is not None else "",
                rtf=row.get("rtf", ""),
                wall=row.get("wall_seconds", ""),
                asr=stages.get("asr_pipeline", ""),
            )
        )
    lines.extend(
        [
            "",
            "WER is computed against spoken dialogue only.",
            "Gemini correction is disabled for every run.",
            "Experimental precision rows with unsupported/failed status are not valid WER rows.",
            "",
        ]
    )
    (root / "report.md").write_text("\n".join(lines), encoding="utf-8")


def create_manifest(spec: DatasetSpec, variants: list[Variant]) -> dict[str, Any]:
    root = Path(spec.benchmark_dir)
    root.mkdir(parents=True, exist_ok=True)
    media, reference, reference_cues, source_ref = resolve_dataset(spec, root)
    probe = probe_json(media)
    reference_text = transcript(reference_cues)
    reference_norm = normalize_for_wer(reference_text, sdh=spec.sdh_reference)
    audio_streams = [
        stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"
    ]
    subtitle_streams = [
        stream for stream in probe.get("streams", []) if stream.get("codec_type") == "subtitle"
    ]
    return {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version,
        "cwd": str(Path.cwd()),
        "dataset": {
            "key": spec.key,
            "title": spec.title,
        },
        "media": {
            "path": str(media),
            "duration_s": round(float(probe["format"]["duration"]), 3),
            "size_bytes": media.stat().st_size,
            "sha256": sha256_file(media),
        },
        "reference": {
            "kind": spec.reference_kind,
            "path": str(reference),
            "source": source_ref,
            "assisted_truth_path": spec.assisted_truth_path,
            "subtitle_stream_spec": spec.subtitle_stream_spec,
            "subtitle_stream_index": spec.subtitle_stream_index,
            "sdh_reference": spec.sdh_reference,
            "cue_count": len(reference_cues),
            "word_count": len(reference_norm.split()),
            "sha256": sha256_file(reference),
        },
        "streams": {
            "audio": audio_streams,
            "subtitle": subtitle_streams,
        },
        "ffmpeg": {
            "ffmpeg_bin": DEFAULT_TOOLS.ffmpeg_bin,
            "ffprobe_bin": DEFAULT_TOOLS.ffprobe_bin,
        },
        "global_policy": {
            "gemini_enabled": False,
            "manual_existing_srt_outputs_excluded": True,
            "isolated_subprocess_per_variant": True,
        },
        "variants": [asdict(v) for v in variants],
    }


def selected_datasets(value: str) -> list[DatasetSpec]:
    if value == "all":
        return [DATASETS["steins_s01e22"]]
    if value not in DATASETS:
        raise ValueError(f"Unknown dataset {value!r}. Expected one of: {', '.join(DATASETS)}")
    return [DATASETS[value]]


def selected_variants(spec: DatasetSpec, names: list[str] | None) -> list[Variant]:
    variants = variants_for_dataset(spec.key)
    if not names:
        return variants
    by_name = {variant.name: variant for variant in variants}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise ValueError(f"Unknown variant(s) for {spec.key}: {', '.join(missing)}")
    return [by_name[name] for name in names]


def record_status(
    spec: DatasetSpec,
    variant: Variant,
    *,
    status: str,
    error: str,
    wall_seconds: float | None = None,
) -> dict[str, Any]:
    root = Path(spec.benchmark_dir)
    media, reference, reference_cues, _source = resolve_dataset(spec, root)
    probe = probe_json(media)
    reference_text = transcript(reference_cues)
    run_dir = root / "runs" / variant.name
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "subtitle.srt"
    words_path = run_dir / "words.json"
    remove_previous_outputs(run_dir)
    write_json(
        run_dir / "result.json",
        {
            "failed": True,
            "status": status,
            "error": error,
            "wall_seconds": round(wall_seconds, 3) if wall_seconds else None,
        },
    )
    return build_metric_payload(
        spec,
        variant,
        media,
        reference,
        reference_text,
        output_path,
        words_path,
        run_dir / "pipeline.log",
        float(probe["format"]["duration"]),
        wall_seconds,
        status=status,
        error=error,
    )


def smoke_parakeet_experimental(spec: DatasetSpec, variant: Variant, *, seconds: int = 60) -> tuple[bool, str]:
    if variant.engine != "parakeet" or not variant.experimental:
        return True, ""
    root = Path(spec.benchmark_dir)
    media, _reference, _cues, _source = resolve_dataset(spec, root)
    smoke_dir = root / "smoke" / variant.name
    smoke_dir.mkdir(parents=True, exist_ok=True)
    smoke_wav = smoke_dir / "smoke.wav"
    result = run_command(
        [
            DEFAULT_TOOLS.ffmpeg_bin,
            "-y",
            "-i",
            str(media),
            "-map",
            "0:a:0",
            "-t",
            str(seconds),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(smoke_wav),
        ],
        timeout=120,
    )
    if result.returncode != 0:
        return False, result.stderr or result.stdout
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--run-smoke-one",
        spec.key,
        variant.name,
        str(smoke_wav),
    ]
    started = time.perf_counter()
    proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    elapsed = time.perf_counter() - started
    (smoke_dir / "stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (smoke_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        combined = "\n".join(part for part in (proc.stderr, proc.stdout) if part)
        return False, f"smoke failed after {elapsed:.1f}s: {combined}"
    return True, ""


def run_smoke_one(spec: DatasetSpec, variant: Variant, wav_path: Path) -> int:
    if variant.engine != "parakeet":
        return 0
    from srtforge.engine_parakeet import generate_optimized_events

    generate_optimized_events(
        str(wav_path),
        model_name=variant.model,
        language="en",
        prefer_gpu=True,
        precision=variant.parakeet_precision,
        word_timestamps_out=str(wav_path.with_suffix(".words.json")),
    )
    return 0


def run_variant_subprocess(
    spec: DatasetSpec,
    variant: Variant,
    *,
    timeout_seconds: int,
    skip_existing: bool,
) -> None:
    root = Path(spec.benchmark_dir)
    run_dir = root / "runs" / variant.name
    run_dir.mkdir(parents=True, exist_ok=True)
    if skip_existing and (run_dir / "metrics.json").exists():
        return
    if variant.experimental:
        ok, error = smoke_parakeet_experimental(spec, variant)
        if not ok:
            record_status(spec, variant, status="unsupported", error=error)
            return
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--run-one",
        spec.key,
        variant.name,
    ]
    if skip_existing:
        command.append("--skip-existing")
    stdout_path = run_dir / "runner_stdout.txt"
    stderr_path = run_dir / "runner_stderr.txt"
    started = time.perf_counter()
    with stdout_path.open("w", encoding="utf-8") as stdout_fp, stderr_path.open("w", encoding="utf-8") as stderr_fp:
        proc = subprocess.Popen(command, stdout=stdout_fp, stderr=stderr_fp, text=True)
        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            record_status(
                spec,
                variant,
                status="timeout",
                error=f"variant exceeded timeout_seconds={timeout_seconds}",
                wall_seconds=time.perf_counter() - started,
            )
            return
    if proc.returncode != 0:
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        error = "\n".join(part for part in (stderr, stdout) if part)
        record_status(
            spec,
            variant,
            status="failed",
            error=error[-4000:],
            wall_seconds=time.perf_counter() - started,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run raw precision benchmark matrix for Srtforge.")
    parser.add_argument("--dataset", default="steins_s01e22", help="Dataset key or 'all'.")
    parser.add_argument("--only", action="append", help="Variant name to run. Can be repeated.")
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--aggregate-only", action="store_true")
    parser.add_argument("--run-one", nargs=2, metavar=("DATASET", "VARIANT"))
    parser.add_argument("--run-smoke-one", nargs=3, metavar=("DATASET", "VARIANT", "WAV"))
    args = parser.parse_args()

    if args.run_smoke_one:
        dataset_key, variant_name, wav = args.run_smoke_one
        spec = DATASETS[dataset_key]
        variant = selected_variants(spec, [variant_name])[0]
        return run_smoke_one(spec, variant, Path(wav))

    if args.run_one:
        dataset_key, variant_name = args.run_one
        spec = DATASETS[dataset_key]
        variant = selected_variants(spec, [variant_name])[0]
        metric = run_variant_in_process(spec, variant, skip_existing=args.skip_existing)
        print(json.dumps({"variant": variant.name, "status": metric.get("status"), "wer_pct": metric.get("wer_pct")}))
        return 0 if metric.get("status") == "completed" else 1

    for spec in selected_datasets(args.dataset):
        variants = selected_variants(spec, args.only)
        manifest = create_manifest(spec, variants)
        write_json(Path(spec.benchmark_dir) / "manifest.json", manifest)
        if args.dry_run:
            print(f"Dry run manifest written to {Path(spec.benchmark_dir) / 'manifest.json'}")
            continue
        if not args.aggregate_only:
            for variant in variants:
                print(f"=== {spec.key}: {variant.name} ===", flush=True)
                run_variant_subprocess(
                    spec,
                    variant,
                    timeout_seconds=args.timeout_seconds,
                    skip_existing=args.skip_existing,
                )
        write_aggregate(spec, manifest, variants)
        print(f"Benchmark report written to {Path(spec.benchmark_dir) / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
