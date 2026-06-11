from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt


DEFAULT_ROOT = Path("benchmark/steinsgate_s01e22_precision")


LABELS = {
    "srtforge_fv_whisper_int8_float16": "Whisper\nFV4 int8+fp16",
    "whisper_raw_int8_float16": "Whisper\nraw int8+fp16",
    "srtforge_fv_parakeet_v2_fp16": "Parakeet v2\nFV4 fp16",
    "srtforge_fv_parakeet_v2_fp32": "Parakeet v2\nFV4 fp32",
    "srtforge_fv_parakeet_v2_bf16": "Parakeet v2\nFV4 bf16",
    "whisper_raw_float16": "Whisper\nraw fp16",
    "srtforge_fv_whisper_float16": "Whisper\nFV4 fp16",
    "srtforge_fv_whisper_bfloat16": "Whisper\nFV4 bf16",
}

CONTROLLED_VARIANTS = (
    "whisper_raw_int8_float16",
    "srtforge_fv_whisper_int8_float16",
    "srtforge_fv_parakeet_v2_fp16",
)

PARAKEET_STAGE_VARIANTS = (
    "srtforge_fv_parakeet_v2_fp32",
    "srtforge_fv_parakeet_v2_fp16",
    "srtforge_fv_parakeet_v2_bf16",
    "parakeet_v2_raw_fp32",
)

WHISPER_STAGE_VARIANTS = (
    "srtforge_fv_whisper_float16",
    "srtforge_fv_whisper_bfloat16",
    "srtforge_fv_whisper_int8_float16",
    "whisper_raw_float16",
)

COLORS = {
    "parakeet": "#1f77b4",
    "whisper": "#d62728",
    "timeout": "#7f7f7f",
}

# Muted single-hue palette for the paper's stage-timing figure: the two
# dominant stages (FV4, ASR) carry the darkest tones, fixed costs stay light.
STAGE_COMPONENTS = (
    ("Extract", "stage_extract_english_audio_s", "#c6d2e0"),
    ("FV4", "stage_vocal_separation_s", "#6b8cae"),
    ("CUDA prep", "stage_prepare_parakeet_cuda_runtime_s", "#b8c4bb"),
    ("ASR", "stage_asr_pipeline_s", "#54616f"),
    ("FFmpeg/post", "other", "#d8d2c4"),
)


def read_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with (root / "metrics.csv").open("r", encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            parsed: dict[str, Any] = dict(row)
            for key in (
                "wer_pct",
                "rtf",
                "wall_seconds",
                "stage_extract_english_audio_s",
                "stage_vocal_separation_s",
                "stage_prepare_parakeet_cuda_runtime_s",
                "stage_asr_pipeline_s",
                "stage_asr_detail_parakeet_transcribe_with_timestamps_timeout_floor_s",
                "stage_logged_total_s",
                "stage_ffmpeg_preprocessing_s",
                "stage_write_srt_s",
            ):
                value = parsed.get(key)
                parsed[key] = float(value) if value not in (None, "") else None
            rows.append(parsed)
    apply_srtforge_name_error_exclusions(root, rows)
    return rows


def apply_srtforge_name_error_exclusions(root: Path, rows: list[dict[str, Any]]) -> None:
    path = root / "metrics_srtforge_name_errors_excluded.csv"
    if not path.exists():
        return
    adjusted: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            adjusted[str(row.get("base_variant"))] = row
    for row in rows:
        variant = str(row.get("variant") or "")
        if row.get("separation_backend") != "fv4" or variant not in adjusted:
            continue
        source = adjusted[variant]
        row["normal_wer_pct"] = row.get("wer_pct")
        row["wer_pct"] = float(source["wer_pct"])
        row["word_errors"] = float(source["word_errors"])
        row["excluded_name_errors"] = float(source.get("excluded_name_errors") or 0.0)
        row["scoring_policy"] = source.get("scoring_policy")


def save(fig: plt.Figure, out_dir: Path, name: str, copy_dir: Path | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"{name}.png"
    pdf = out_dir / f"{name}.pdf"
    fig.savefig(png, dpi=240, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    if copy_dir:
        copy_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(png, copy_dir / png.name)
        shutil.copy2(pdf, copy_dir / pdf.name)
    plt.close(fig)


def completed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("status") in ("", "completed") and row.get("wer_pct") is not None]


def precision_label(row: dict[str, Any]) -> str:
    precision = row.get("effective_precision") or row.get("precision") or row.get("precision_requested") or ""
    if row.get("precision_requested") == "auto":
        precision = f"{precision} default"
    return str(precision).replace("int8_float16", "int8+fp16")


def label_for(row: dict[str, Any]) -> str:
    if row.get("_controlled_label"):
        return str(row["_controlled_label"])
    label = LABELS.get(str(row.get("variant", "")))
    if label:
        return label
    model = str(row.get("model_short") or row.get("model") or "")
    if model == "large-v3-turbo":
        model = "Whisper"
    elif model == "parakeet_v3":
        model = "Parakeet v3"
    elif model == "parakeet_v2":
        model = "Parakeet v2"
    backend = "FV4" if row.get("separation_backend") == "fv4" else "raw"
    return f"{model}\n{backend} {precision_label(row)}"


def controlled_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    done = completed(rows)
    by_variant = {row.get("variant"): row for row in done}
    selected: list[dict[str, Any]] = []
    for variant in CONTROLLED_VARIANTS[:2]:
        if variant in by_variant:
            selected.append(by_variant[variant])
    parakeet_raw = [
        row
        for row in done
        if row.get("engine") == "parakeet"
        and row.get("separation_backend") == "none"
        and row.get("wall_seconds") is not None
    ]
    if parakeet_raw:
        fastest = dict(min(parakeet_raw, key=lambda row: float(row["wall_seconds"])))
        fastest["_controlled_label"] = f"{str(fastest.get('model_short', 'Parakeet')).replace('parakeet_', 'Parakeet ')}\nraw {precision_label(fastest)}"
        selected.append(fastest)
    for variant in CONTROLLED_VARIANTS[2:]:
        if variant in by_variant:
            selected.append(by_variant[variant])
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in selected:
        key = str(row.get("variant"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def plot_wer(rows: list[dict[str, Any]], out_dir: Path, copy_dir: Path | None) -> None:
    data = controlled_rows(rows)
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    labels = [label_for(row) for row in data]
    colors = [COLORS.get(row["engine"], "#555555") for row in data]
    values = [float(row["wer_pct"]) for row in data]
    y = list(range(len(data)))
    bars = ax.barh(y, values, color=colors, edgecolor="#222222", linewidth=0.6)
    ax.set_yticks(y, [label.replace("\n", " ") for label in labels], fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("WER (%)")
    ax.set_title("Reference media asset (M-1) WER by pipeline variant")
    ax.set_xlim(0, max(values) * 1.24)
    ax.grid(axis="x", color="#dddddd", linewidth=0.8)
    ax.set_axisbelow(True)
    for bar, value in zip(bars, values):
        ax.text(
            value + max(values) * 0.018,
            bar.get_y() + bar.get_height() / 2,
            f"{value:.2f}%",
            ha="left",
            va="center",
            fontsize=8,
        )
    fig.tight_layout()
    save(fig, out_dir, "benchmark_wer_by_variant", copy_dir)


def plot_rtf(rows: list[dict[str, Any]], out_dir: Path, copy_dir: Path | None) -> None:
    data = [row for row in controlled_rows(rows) if row.get("rtf") is not None]
    fig, ax = plt.subplots(figsize=(7.8, 4.2))
    labels = [label_for(row) for row in data]
    colors = [COLORS["timeout"] if row.get("status") == "timeout" else COLORS.get(row["engine"], "#555555") for row in data]
    bars = ax.bar(labels, [row["rtf"] for row in data], color=colors, edgecolor="#222222", linewidth=0.6)
    ax.set_yscale("log")
    ax.set_ylabel("Real-time factor (log scale)")
    ax.set_title("Pipeline real-time factor by setting")
    ax.set_ylim(min(row["rtf"] for row in data) * 0.75, max(row["rtf"] for row in data) * 1.45)
    ax.axhline(1.0, color="#333333", linewidth=0.9, linestyle="--")
    ax.text(len(data) - 0.2, 1.03, "real time", ha="right", va="bottom", fontsize=8)
    ax.grid(axis="y", color="#dddddd", linewidth=0.8, which="both")
    ax.set_axisbelow(True)
    for bar, row in zip(bars, data):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            row["rtf"] * 1.08,
            f"{row['rtf']:.3f}",
            ha="center",
            va="bottom",
            fontsize=8,
        )
    save(fig, out_dir, "benchmark_rtf_by_variant", copy_dir)


def plot_stage_breakdown(
    rows: list[dict[str, Any]],
    out_dir: Path,
    copy_dir: Path | None,
    *,
    variants: tuple[str, ...],
    title: str,
    filename: str,
) -> None:
    by_variant = {str(row.get("variant")): row for row in completed(rows)}
    data = [by_variant[variant] for variant in variants if variant in by_variant and by_variant[variant].get("wall_seconds") is not None]
    if not data:
        return
    labels = [label_for(row).replace("\n", " ") for row in data]
    components = STAGE_COMPONENTS
    fig, ax = plt.subplots(figsize=(8.4, 3.8))
    left = [0.0] * len(data)
    for label, key, color in components:
        values = []
        for row in data:
            if key == "other":
                known = sum(float(row.get(k) or 0.0) for _, k, _ in components if k != "other")
                values.append(max(0.0, float(row["wall_seconds"]) - known))
            else:
                values.append(float(row.get(key) or 0.0))
        ax.barh(labels, values, left=left, label=label, color=color, edgecolor="#222222", linewidth=0.4)
        left = [a + b for a, b in zip(left, values)]
    ax.set_xlabel("Seconds")
    ax.set_title(title)
    ax.invert_yaxis()
    ax.grid(axis="x", color="#dddddd", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(ncol=5, fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.18))
    fig.subplots_adjust(left=0.31, right=0.98, top=0.78, bottom=0.18)
    save(fig, out_dir, filename, copy_dir)


def stage_rows(rows: list[dict[str, Any]], variants: tuple[str, ...]) -> list[dict[str, Any]]:
    by_variant = {str(row.get("variant")): row for row in completed(rows)}
    return [
        by_variant[variant]
        for variant in variants
        if variant in by_variant and by_variant[variant].get("wall_seconds") is not None
    ]


def draw_stage_axis(ax: plt.Axes, data: list[dict[str, Any]], title: str, *, show_legend: bool = False) -> None:
    labels = [label_for(row).replace("\n", " ") for row in data]
    components = STAGE_COMPONENTS
    left = [0.0] * len(data)
    y = list(range(len(data)))
    for label, key, color in components:
        values = []
        for row in data:
            if key == "other":
                known = sum(float(row.get(k) or 0.0) for _, k, _ in components if k != "other")
                values.append(max(0.0, float(row["wall_seconds"]) - known))
            else:
                values.append(float(row.get(key) or 0.0))
        ax.barh(y, values, left=left, height=0.36, label=label, color=color, edgecolor="#222222", linewidth=0.3)
        left = [a + b for a, b in zip(left, values)]
    ax.set_title(title, fontsize=9.5)
    ax.set_yticks(y, labels, fontsize=7.4)
    ax.invert_yaxis()
    ax.grid(axis="x", color="#dddddd", linewidth=0.8)
    ax.set_axisbelow(True)
    if show_legend:
        ax.legend(ncol=5, fontsize=7.3, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.34))


def plot_combined_stage_breakdown(rows: list[dict[str, Any]], out_dir: Path, copy_dir: Path | None) -> None:
    parakeet = stage_rows(rows, PARAKEET_STAGE_VARIANTS)
    whisper = stage_rows(rows, WHISPER_STAGE_VARIANTS)
    if not parakeet and not whisper:
        return
    fig, axes = plt.subplots(2, 1, figsize=(8.1, 4.25), sharex=True)
    draw_stage_axis(axes[0], parakeet, "Parakeet v2", show_legend=True)
    draw_stage_axis(axes[1], whisper, "Whisper")
    axes[1].set_xlabel("Seconds")
    max_seconds = max(
        float(row["wall_seconds"])
        for row in [*parakeet, *whisper]
        if row.get("wall_seconds") is not None
    )
    for ax in axes:
        ax.set_xlim(0, max_seconds * 1.05)
    fig.subplots_adjust(left=0.25, right=0.985, top=0.86, bottom=0.12, hspace=0.42)
    save(fig, out_dir, "benchmark_stage_combined_column", copy_dir)


def plot_scatter(rows: list[dict[str, Any]], out_dir: Path, copy_dir: Path | None) -> None:
    data = completed(rows)
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    for row in data:
        ax.scatter(
            row["rtf"],
            row["wer_pct"],
            s=80,
            color=COLORS.get(row["engine"], "#555555"),
            edgecolor="#222222",
            linewidth=0.7,
        )
        ax.annotate(
            LABELS.get(row["variant"], row["variant"]).replace("\n", " "),
            (row["rtf"], row["wer_pct"]),
            xytext=(5, 4),
            textcoords="offset points",
            fontsize=8,
        )
    ax.set_xlabel("Real-time factor")
    ax.set_ylabel("WER (%)")
    ax.set_title("Accuracy-speed trade-off")
    ax.grid(color="#dddddd", linewidth=0.8)
    ax.set_axisbelow(True)
    save(fig, out_dir, "benchmark_accuracy_speed", copy_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot the controlled selected-episode benchmark results.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--copy-to", type=Path, default=None)
    args = parser.parse_args()

    rows = read_rows(args.root)
    out_dir = args.root / "figures"
    plot_combined_stage_breakdown(rows, out_dir, args.copy_to)
    print(f"Wrote figures to {out_dir}")
    if args.copy_to:
        print(f"Copied figures to {args.copy_to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
