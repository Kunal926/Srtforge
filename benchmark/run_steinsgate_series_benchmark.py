from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any

from run_precision_benchmark import (
    WHISPER_MODEL,
    Variant,
    clean_spoken_text,
    copy_pipeline_log,
    cue_timing_metrics,
    load_diag_metrics,
    parse_ass_dialogue,
    parse_stage_times,
    parse_srt,
    probe_json,
    sha256_file,
    transcript,
    wer_metrics,
    write_json,
)
from srtforge.pipeline import PipelineConfig, run_pipeline
from srtforge.settings import settings


STEINS_DIR = Path("D:/Shows/Currently Watching/Steins;Gate (2011) [tvdbid-244061]/Season 1")
BENCHMARK_ROOT = Path("benchmark/steinsgate_s01_all_best")
EP_RE = re.compile(r"S01E(?P<episode>\d{2})")

VARIANTS: tuple[Variant, ...] = (
    Variant(
        "srtforge_fv_whisper_int8_float16",
        "whisper",
        WHISPER_MODEL,
        separation_backend="fv4",
        whisper_compute_type="int8_float16",
    ),
    Variant(
        "raw_whisper_int8_float16",
        "whisper",
        WHISPER_MODEL,
        separation_backend="none",
        whisper_compute_type="int8_float16",
    ),
)


@dataclass(frozen=True)
class Episode:
    key: str
    media_path: Path
    reference_path: Path

    @property
    def title(self) -> str:
        return self.media_path.stem


def discover_episodes() -> list[Episode]:
    mkvs: dict[str, Path] = {}
    refs: dict[str, Path] = {}
    for path in sorted(STEINS_DIR.glob("*")):
        match = EP_RE.search(path.name)
        if not match:
            continue
        key = f"S01E{match.group('episode')}"
        if path.suffix.lower() == ".mkv":
            mkvs[key] = path
        elif path.suffix.lower() == ".ass":
            refs[key] = path
    episodes = [
        Episode(key=key, media_path=mkvs[key], reference_path=refs[key])
        for key in sorted(set(mkvs) & set(refs))
    ]
    if len(episodes) != 24:
        raise RuntimeError(f"Expected 24 MKV/ASS pairs, found {len(episodes)}.")
    return episodes


def selected_variants(names: list[str] | None) -> list[Variant]:
    if not names:
        return list(VARIANTS)
    by_name = {variant.name: variant for variant in VARIANTS}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise ValueError(f"Unknown variant(s): {', '.join(missing)}")
    return [by_name[name] for name in names]


def selected_episodes(names: list[str] | None) -> list[Episode]:
    episodes = discover_episodes()
    if not names:
        return episodes
    wanted = {name.upper().replace("S1E", "S01E") for name in names}
    by_key = {episode.key: episode for episode in episodes}
    missing = [name for name in wanted if name not in by_key]
    if missing:
        raise ValueError(f"Unknown episode(s): {', '.join(missing)}")
    return [by_key[key] for key in sorted(wanted)]


def reference_copy_path(root: Path, episode: Episode) -> Path:
    return root / "reference" / f"{episode.key}.ass"


def load_reference(root: Path, episode: Episode) -> tuple[Path, list[dict[str, Any]], str]:
    ref_path = reference_copy_path(root, episode)
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(episode.reference_path, ref_path)
    cues = parse_ass_dialogue(ref_path, {"Default"}, sdh=False)
    return ref_path, cues, transcript(cues)


def run_dir(root: Path, episode: Episode, variant: Variant) -> Path:
    return root / "runs" / variant.name / episode.key


def remove_previous_outputs(path: Path) -> None:
    for name in (
        "subtitle.srt",
        "subtitle.srt.diag.csv",
        "subtitle.srt.diag.json",
        "words.json",
        "metrics.json",
        "result.json",
        "pipeline.log",
        "pipeline_log.txt",
    ):
        target = path / name
        if target.exists():
            target.unlink()


def build_config(root: Path, episode: Episode, variant: Variant, out_dir: Path) -> PipelineConfig:
    output_path = out_dir / "subtitle.srt"
    words_path = out_dir / "words.json"
    return PipelineConfig(
        media_path=episode.media_path,
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


def config_snapshot(config: PipelineConfig, episode: Episode, variant: Variant) -> dict[str, Any]:
    return {
        "episode": {
            "key": episode.key,
            "media_path": str(episode.media_path),
            "reference_path": str(episode.reference_path),
        },
        "variant": asdict(variant),
        "pipeline": {
            "output_path": str(config.output_path),
            "temp_dir": str(config.temp_dir) if config.temp_dir else None,
            "separation_backend": config.separation_backend,
            "asr_engine": config.asr_engine,
            "whisper_model": config.whisper_model,
            "whisper_language": config.whisper_language,
            "whisper_compute_type": config.whisper_compute_type,
            "gemini_enabled": config.gemini_enabled,
            "word_timestamps_path": str(config.word_timestamps_path) if config.word_timestamps_path else None,
        },
    }


def build_metric_payload(
    root: Path,
    episode: Episode,
    variant: Variant,
    *,
    status: str,
    error: str | None = None,
    wall_seconds: float | None = None,
    log_path: Path | None = None,
) -> dict[str, Any]:
    out_dir = run_dir(root, episode, variant)
    output_path = out_dir / "subtitle.srt"
    words_path = out_dir / "words.json"
    reference_path, reference_cues, reference_text = load_reference(root, episode)
    generated_cues = parse_srt(output_path, sdh=False)
    generated_text = transcript(generated_cues)
    probe = probe_json(episode.media_path)
    media_duration_s = float(probe["format"]["duration"])
    metric: dict[str, Any] = {
        "dataset": "steinsgate_s01_all",
        "episode": episode.key,
        "episode_title": episode.title,
        "status": status,
        "variant": variant.name,
        "engine": variant.engine,
        "model": variant.model,
        "precision": variant.precision_label,
        "separation_backend": variant.separation_backend,
        "reference": str(reference_path),
        "reference_source": str(episode.reference_path),
        "reference_cue_count": len(reference_cues),
        "output_srt": str(output_path),
        "word_timestamps": str(words_path) if words_path.exists() else None,
        "media_duration_s": round(media_duration_s, 3),
        "wall_seconds": round(float(wall_seconds), 3) if wall_seconds else None,
        "error": error,
        "stage_times": parse_stage_times(log_path),
    }
    if metric["wall_seconds"]:
        metric["rtf"] = round(float(metric["wall_seconds"]) / media_duration_s, 5)
    if output_path.exists() and status == "completed":
        metric.update(wer_metrics(reference_text, generated_text, sdh=False))
        metric.update(cue_timing_metrics(generated_cues))
        metric.update(load_diag_metrics(output_path.with_suffix(output_path.suffix + ".diag.csv")))
        metric["output_sha256"] = sha256_file(output_path)
    write_json(out_dir / "metrics.json", metric)
    return metric


def run_one_in_process(root: Path, episode: Episode, variant: Variant, *, skip_existing: bool = False) -> dict[str, Any]:
    out_dir = run_dir(root, episode, variant)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / "subtitle.srt"
    if skip_existing and (out_dir / "metrics.json").exists() and output_path.exists():
        return json.loads((out_dir / "metrics.json").read_text(encoding="utf-8"))

    remove_previous_outputs(out_dir)
    _reference_path, _reference_cues, _reference_text = load_reference(root, episode)
    config = build_config(root, episode, variant, out_dir)
    write_json(out_dir / "config.json", config_snapshot(config, episode, variant))

    started = time.perf_counter()
    result = run_pipeline(config)
    wall_seconds = time.perf_counter() - started
    result_payload = {
        "failed": result.failed,
        "error": result.error,
        "run_id": result.run_id,
        "performance_log_path": str(result.performance_log_path) if result.performance_log_path else None,
        "output_path": str(result.output_path) if result.output_path else None,
        "wall_seconds": round(wall_seconds, 3),
    }
    write_json(out_dir / "result.json", result_payload)
    copied_log = None
    if result.performance_log_path and result.performance_log_path.exists():
        copied_log = copy_pipeline_log(result.performance_log_path, out_dir)
    status = "completed" if output_path.exists() and not result.failed else "failed"
    return build_metric_payload(
        root,
        episode,
        variant,
        status=status,
        error=result.error,
        wall_seconds=wall_seconds,
        log_path=copied_log,
    )


def run_one_subprocess(
    root: Path,
    episode: Episode,
    variant: Variant,
    *,
    timeout_seconds: int,
    skip_existing: bool,
) -> None:
    out_dir = run_dir(root, episode, variant)
    out_dir.mkdir(parents=True, exist_ok=True)
    if skip_existing and (out_dir / "metrics.json").exists():
        return
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--run-one",
        episode.key,
        variant.name,
    ]
    if skip_existing:
        command.append("--skip-existing")
    stdout_path = out_dir / "runner_stdout.txt"
    stderr_path = out_dir / "runner_stderr.txt"
    started = time.perf_counter()
    with stdout_path.open("w", encoding="utf-8") as stdout_fp, stderr_path.open("w", encoding="utf-8") as stderr_fp:
        proc = subprocess.Popen(command, stdout=stdout_fp, stderr=stderr_fp, text=True)
        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            build_metric_payload(
                root,
                episode,
                variant,
                status="timeout",
                error=f"episode variant exceeded timeout_seconds={timeout_seconds}",
                wall_seconds=time.perf_counter() - started,
            )
            return
    if proc.returncode != 0:
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        build_metric_payload(
            root,
            episode,
            variant,
            status="failed",
            error="\n".join(part for part in (stderr, stdout) if part)[-4000:],
            wall_seconds=time.perf_counter() - started,
        )


def flatten_metric(row: dict[str, Any]) -> dict[str, Any]:
    flat = {k: v for k, v in row.items() if k != "stage_times"}
    for key, value in (row.get("stage_times") or {}).items():
        flat[f"stage_{key}_s"] = value
    return flat


def load_all_metrics(root: Path, episodes: list[Episode], variants: list[Variant]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variant in variants:
        for episode in episodes:
            path = run_dir(root, episode, variant) / "metrics.json"
            if path.exists():
                rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


def write_manifest(root: Path, episodes: list[Episode], variants: list[Variant]) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    episode_payload = []
    for episode in episodes:
        reference_path, reference_cues, reference_text = load_reference(root, episode)
        probe = probe_json(episode.media_path)
        audio_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
        episode_payload.append(
            {
                "key": episode.key,
                "title": episode.title,
                "media_path": str(episode.media_path),
                "media_duration_s": round(float(probe["format"]["duration"]), 3),
                "reference_path": str(reference_path),
                "reference_source": str(episode.reference_path),
                "reference_cue_count": len(reference_cues),
                "reference_words": len(clean_spoken_text(reference_text).split()),
                "audio_streams": audio_streams,
            }
        )
    manifest = {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version,
        "source_dir": str(STEINS_DIR),
        "reference_policy": {
            "kind": "ass_sidecar",
            "styles": ["Default"],
            "scoring": "word-level WER after ASS tag, punctuation, and case normalization",
            "existing_srt_files_excluded": True,
        },
        "global_policy": {
            "gemini_enabled": False,
            "isolated_subprocess_per_episode_variant": True,
            "english_audio_selection": "first eng/en/english audio stream reported by ffprobe",
        },
        "episodes": episode_payload,
        "variants": [asdict(variant) for variant in variants],
    }
    write_json(root / "manifest.json", manifest)
    return manifest


def weighted_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if row.get("status") == "completed" and row.get("wer") is not None]
    ref_words = sum(int(row.get("reference_words") or 0) for row in completed)
    errors = sum(
        int(row.get("substitutions") or 0) + int(row.get("deletions") or 0) + int(row.get("insertions") or 0)
        for row in completed
    )
    wers = [float(row["wer_pct"]) for row in completed]
    rtfs = [float(row["rtf"]) for row in completed if row.get("rtf") is not None]
    return {
        "completed": len(completed),
        "weighted_wer_pct": round(100.0 * errors / ref_words, 2) if ref_words else None,
        "macro_wer_pct": round(mean(wers), 2) if wers else None,
        "median_wer_pct": round(median(wers), 2) if wers else None,
        "mean_rtf": round(mean(rtfs), 5) if rtfs else None,
        "total_wall_seconds": round(sum(float(row.get("wall_seconds") or 0.0) for row in completed), 3),
        "reference_words": ref_words,
        "word_errors": errors,
    }


def write_aggregate(root: Path, episodes: list[Episode], variants: list[Variant], manifest: dict[str, Any]) -> None:
    rows = load_all_metrics(root, episodes, variants)
    write_json(root / "metrics.json", rows)
    flats = [flatten_metric(row) for row in rows]
    fieldnames = sorted({key for row in flats for key in row.keys()})
    with (root / "metrics.csv").open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flats)

    lines = [
        "# Steins;Gate S01 All-Episode Best-Settings Benchmark",
        "",
        f"Source: `{manifest['source_dir']}`",
        "Reference: sidecar ASS `Default` dialogue only; existing SRT files excluded.",
        "Gemini correction disabled.",
        "",
        "## Variant Summary",
        "",
        "| Variant | Completed | Weighted WER % | Macro WER % | Median WER % | Mean RTF | Total wall s |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_variant.setdefault(row["variant"], []).append(row)
    summary_payload: dict[str, Any] = {}
    for variant in variants:
        summary = weighted_summary(by_variant.get(variant.name, []))
        summary_payload[variant.name] = summary
        lines.append(
            "| {variant} | {completed} | {weighted} | {macro} | {median} | {rtf} | {wall} |".format(
                variant=variant.name,
                completed=summary["completed"],
                weighted=summary["weighted_wer_pct"] if summary["weighted_wer_pct"] is not None else "",
                macro=summary["macro_wer_pct"] if summary["macro_wer_pct"] is not None else "",
                median=summary["median_wer_pct"] if summary["median_wer_pct"] is not None else "",
                rtf=summary["mean_rtf"] if summary["mean_rtf"] is not None else "",
                wall=summary["total_wall_seconds"],
            )
        )
    if {
        "srtforge_fv_whisper_int8_float16",
        "raw_whisper_int8_float16",
    }.issubset(by_variant):
        fv_rows = {
            row["episode"]: row
            for row in by_variant["srtforge_fv_whisper_int8_float16"]
            if row.get("status") == "completed" and row.get("wer_pct") is not None
        }
        raw_rows = {
            row["episode"]: row
            for row in by_variant["raw_whisper_int8_float16"]
            if row.get("status") == "completed" and row.get("wer_pct") is not None
        }
        common = sorted(set(fv_rows) & set(raw_rows))
        fv_better = sum(float(fv_rows[episode]["wer_pct"]) < float(raw_rows[episode]["wer_pct"]) for episode in common)
        raw_better = sum(float(raw_rows[episode]["wer_pct"]) < float(fv_rows[episode]["wer_pct"]) for episode in common)
        ties = len(common) - fv_better - raw_better
        fv_under_six = sum(float(fv_rows[episode]["wer_pct"]) < 6.0 for episode in common)
        raw_under_six = sum(float(raw_rows[episode]["wer_pct"]) < 6.0 for episode in common)
        fv_summary = summary_payload["srtforge_fv_whisper_int8_float16"]
        raw_summary = summary_payload["raw_whisper_int8_float16"]
        lines.extend(
            [
                "",
                "## FV4 vs Raw Control",
                "",
                f"Common completed episodes: {len(common)}",
                f"FV4 lower WER episodes: {fv_better}",
                f"Raw lower WER episodes: {raw_better}",
                f"Tied WER episodes: {ties}",
                f"FV4 episodes under 6% WER: {fv_under_six}",
                f"Raw episodes under 6% WER: {raw_under_six}",
                "Weighted WER delta (FV4 - raw): "
                f"{round(float(fv_summary['weighted_wer_pct']) - float(raw_summary['weighted_wer_pct']), 2)} percentage points",
                "Mean RTF delta (FV4 - raw): "
                f"{round(float(fv_summary['mean_rtf']) - float(raw_summary['mean_rtf']), 5)}",
                "",
            ]
        )
    lines.extend(["", "## Per-Episode Results", ""])
    for variant in variants:
        lines.extend(
            [
                f"### {variant.name}",
                "",
                "| Episode | Status | WER % | RTF | Wall s | Ref words | Hyp words |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in by_variant.get(variant.name, []):
            lines.append(
                "| {episode} | {status} | {wer} | {rtf} | {wall} | {ref} | {hyp} |".format(
                    episode=row.get("episode"),
                    status=row.get("status"),
                    wer=f"{float(row['wer_pct']):.2f}" if row.get("wer_pct") is not None else "",
                    rtf=row.get("rtf", ""),
                    wall=row.get("wall_seconds", ""),
                    ref=row.get("reference_words", ""),
                    hyp=row.get("hypothesis_words", ""),
                )
            )
        lines.append("")
    write_json(root / "summary.json", summary_payload)
    (root / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Steins;Gate S01 all-episode Srtforge benchmark.")
    parser.add_argument("--root", type=Path, default=BENCHMARK_ROOT)
    parser.add_argument("--only-episode", action="append", help="Episode key, e.g. S01E01. Can be repeated.")
    parser.add_argument("--only-variant", action="append", help="Variant name. Can be repeated.")
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--aggregate-only", action="store_true")
    parser.add_argument("--run-one", nargs=2, metavar=("EPISODE", "VARIANT"))
    args = parser.parse_args()

    if args.run_one:
        episode_key, variant_name = args.run_one
        episode = selected_episodes([episode_key])[0]
        variant = selected_variants([variant_name])[0]
        metric = run_one_in_process(args.root, episode, variant, skip_existing=args.skip_existing)
        print(json.dumps({"episode": episode.key, "variant": variant.name, "status": metric.get("status"), "wer_pct": metric.get("wer_pct")}))
        return 0 if metric.get("status") == "completed" else 1

    episodes = selected_episodes(args.only_episode)
    variants = selected_variants(args.only_variant)
    manifest = write_manifest(args.root, episodes, variants)
    if args.dry_run:
        print(f"Dry run manifest written to {args.root / 'manifest.json'}")
        return 0
    if not args.aggregate_only:
        for variant in variants:
            for episode in episodes:
                print(f"=== {variant.name}: {episode.key} ===", flush=True)
                run_one_subprocess(
                    args.root,
                    episode,
                    variant,
                    timeout_seconds=args.timeout_seconds,
                    skip_existing=args.skip_existing,
                )
                write_aggregate(args.root, episodes, variants, manifest)
    write_aggregate(args.root, episodes, variants, manifest)
    print(f"Benchmark report written to {args.root / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
