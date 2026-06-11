from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt


DEFAULT_ROOTS = (
    Path("benchmark/steinsgate_s01e22_precision"),
)

ENGINE_COLORS = {
    "parakeet": "#3b6ea5",
    "whisper": "#b0413e",
    "failed": "#737373",
    "unsupported": "#a3a3a3",
    "timeout": "#525252",
}

PRECISION_LABELS = {
    "float32": "fp32",
    "float16": "fp16",
    "bfloat16": "bf16",
    "int8_float16": "int8+fp16",
    "int8_bfloat16": "int8+bf16",
}

WHISPER_WER_VARIANTS = (
    ("whisper_raw_float32", "fp32"),
    ("whisper_raw_float16", "fp16"),
    ("whisper_raw_int8_float16", "int8+fp16"),
)

PARAKEET_V2_WER_VARIANTS = (
    ("parakeet_v2_raw_fp32", "fp32"),
    ("parakeet_v2_raw_fp16", "fp16"),
    ("parakeet_v2_raw_bf16", "bf16"),
)

PAPER_TIMING_VARIANTS = {
    "parakeet_v2_raw_fp32",
    "parakeet_v2_raw_fp16",
    "parakeet_v2_raw_bf16",
    "whisper_raw_float16",
    "whisper_raw_bfloat16",
    "whisper_raw_int8_float16",
    "srtforge_fv_whisper_int8_float16",
    "srtforge_fv_parakeet_v2_fp16",
}

SCATTER_VARIANTS = {
    "parakeet_v3_raw_fp32",
    "parakeet_v3_raw_fp16",
    "parakeet_v3_raw_bf16",
    "parakeet_v2_raw_fp32",
    "parakeet_v2_raw_fp16",
    "parakeet_v2_raw_bf16",
    "whisper_raw_float16",
    "whisper_raw_bfloat16",
    "whisper_raw_int8_float16",
    "srtforge_fv_whisper_int8_float16",
    "srtforge_fv_parakeet_v3_fp32",
}


def load_rows(roots: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for root in roots:
        path = root / "metrics.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload:
            row = dict(row)
            row["_root"] = str(root)
            row["_dataset_label"] = dataset_label(root, row)
            rows.append(row)
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


def dataset_label(root: Path, row: dict[str, Any]) -> str:
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            dataset = manifest.get("dataset", {})
            if dataset.get("title"):
                return str(dataset["title"])
            media = manifest.get("media", {})
            stem = Path(str(media.get("path", ""))).stem
            if stem:
                if "Steins;Gate" in stem:
                    return "Reference media asset (M-1)"
        except Exception:
            pass
    key = str(row.get("dataset") or root.name)
    return {
        "steins_s01e22": "Reference media asset (M-1)",
    }.get(key, key)


def valid_wer_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get("status") == "completed"
        and row.get("wer_pct") is not None
        and row.get("rtf") is not None
    ]


def short_label(row: dict[str, Any]) -> str:
    model = str(row.get("model_short") or row.get("model") or "")
    if model == "large-v3-turbo":
        model = "Whisper"
    elif model == "parakeet_v3":
        model = "Parakeet v3"
    elif model == "parakeet_v2":
        model = "Parakeet v2"
    precision = row.get("effective_precision") or row.get("precision") or row.get("precision_requested")
    precision = PRECISION_LABELS.get(str(precision), str(precision).replace("int8_float16", "int8+fp16"))
    if row.get("precision_requested") == "auto":
        precision = f"{precision} default"
    if row.get("separation_backend") == "fv4":
        return f"{model}\nFV4 {precision}"
    return f"{model}\n{precision}"


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


def group_by_dataset(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["_dataset_label"], []).append(row)
    return [(label, grouped[label]) for label in sorted(grouped)]


def sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {"parakeet_v3": 0, "parakeet_v2": 1, "large-v3-turbo": 2, "whisper": 2}
    return sorted(
        rows,
        key=lambda row: (
            order.get(str(row.get("model_short") or row.get("model")), 99),
            str(row.get("precision") or ""),
        ),
    )


def rows_by_variant(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in valid_wer_rows(rows):
        variant = str(row.get("variant") or "")
        selected.setdefault(variant, row)
    return selected


def paper_timing_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("variant") in PAPER_TIMING_VARIANTS]


def scatter_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("variant") in SCATTER_VARIANTS]


def plot_selected_wer(
    rows: list[dict[str, Any]],
    *,
    variants: tuple[tuple[str, str], ...],
    title: str,
    filename: str,
    out_dir: Path,
    copy_dir: Path | None,
) -> None:
    by_variant = rows_by_variant(rows)
    data: list[tuple[dict[str, Any], str]] = [
        (by_variant[variant], label) for variant, label in variants if variant in by_variant
    ]
    if not data:
        return
    values = [float(row["wer_pct"]) for row, _label in data]
    labels = [label for _row, label in data]
    colors = [ENGINE_COLORS.get(row.get("engine"), "#525252") for row, _label in data]

    fig, ax = plt.subplots(figsize=(4.8, 3.25))
    x = list(range(len(data)))
    bars = ax.bar(x, values, color=colors, edgecolor="#222222", linewidth=0.65)
    ax.set_xticks(x, labels, fontsize=9)
    ax.set_ylabel("WER (%)")
    ax.set_title(title, fontsize=11)
    ax.grid(axis="y", color="#dddddd", linewidth=0.8)
    ax.set_axisbelow(True)
    if values:
        ax.set_ylim(0, max(values) * 1.22)
    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + max(values) * 0.025,
            f"{value:.2f}",
            ha="center",
            va="bottom",
            fontsize=8,
        )
    fig.tight_layout()
    save(fig, out_dir, filename, copy_dir)


def plot_metric_by_dataset(
    rows: list[dict[str, Any]],
    *,
    metric_key: str,
    ylabel: str,
    title: str,
    filename: str,
    out_dir: Path,
    copy_dir: Path | None,
    log_y: bool = False,
) -> None:
    grouped = group_by_dataset(valid_wer_rows(rows))
    if not grouped:
        return
    max_rows = max(len(group) for _label, group in grouped)
    horizontal = max_rows > 8
    if horizontal:
        fig_height = max(4.8, 0.42 * max_rows * len(grouped) + 1.8)
    else:
        fig_height = max(3.2, 2.65 * len(grouped))
    fig, axes = plt.subplots(len(grouped), 1, figsize=(9.2, fig_height), squeeze=False)
    for ax, (dataset, dataset_rows) in zip(axes[:, 0], grouped):
        data = [row for row in sort_rows(dataset_rows) if row.get(metric_key) is not None]
        values = [float(row[metric_key]) for row in data]
        colors = [ENGINE_COLORS.get(row.get("engine"), "#525252") for row in data]
        ax.set_title(dataset, fontsize=10)
        if horizontal:
            y = list(range(len(data)))
            bars = ax.barh(y, values, color=colors, edgecolor="#222222", linewidth=0.55)
            ax.set_yticks(y, [short_label(row).replace("\n", " ") for row in data], fontsize=8)
            ax.invert_yaxis()
            ax.set_xlabel(ylabel)
            ax.grid(axis="x", color="#dddddd", linewidth=0.8, which="both")
        else:
            x = list(range(len(data)))
            bars = ax.bar(x, values, color=colors, edgecolor="#222222", linewidth=0.55)
            ax.set_ylabel(ylabel)
            ax.set_xticks(x, [short_label(row) for row in data], fontsize=8)
            ax.grid(axis="y", color="#dddddd", linewidth=0.8, which="both")
        ax.set_axisbelow(True)
        if log_y and values and min(values) > 0:
            if horizontal:
                ax.set_xscale("log")
                ax.axvline(1.0, color="#333333", linewidth=0.9, linestyle="--")
            else:
                ax.set_yscale("log")
                ax.axhline(1.0, color="#333333", linewidth=0.9, linestyle="--")
        if values:
            ymax = max(values)
            if horizontal:
                if log_y:
                    ax.set_xlim(max(min(values) * 0.72, 1e-4), ymax * 1.85)
                else:
                    ax.set_xlim(0, ymax * 1.22)
            elif log_y:
                ax.set_ylim(max(min(values) * 0.72, 1e-4), ymax * 1.8)
            else:
                ax.set_ylim(0, ymax * 1.18)
        for bar, value in zip(bars, values):
            label = f"{value:.2f}" if metric_key == "wer_pct" else f"{value:.3f}"
            if horizontal:
                x_text = value * 1.04 if log_y else value + (max(values) * 0.015 if values else 0.0)
                ax.text(x_text, bar.get_y() + bar.get_height() / 2, label, ha="left", va="center", fontsize=7)
            else:
                y = value * 1.05 if log_y else value + (max(values) * 0.02 if values else 0.0)
                ax.text(bar.get_x() + bar.get_width() / 2, y, label, ha="center", va="bottom", fontsize=7)
    fig.suptitle(title, fontsize=12)
    fig.tight_layout()
    save(fig, out_dir, filename, copy_dir)


def asr_seconds(row: dict[str, Any]) -> float | None:
    stages = row.get("stage_times") or {}
    value = stages.get("asr_pipeline") or row.get("stage_asr_pipeline_s")
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def plot_asr_time(rows: list[dict[str, Any]], out_dir: Path, copy_dir: Path | None) -> None:
    patched: list[dict[str, Any]] = []
    for row in rows:
        seconds = asr_seconds(row)
        if seconds is not None:
            row = dict(row)
            row["_asr_seconds"] = seconds
            patched.append(row)
    plot_metric_by_dataset(
        patched,
        metric_key="_asr_seconds",
        ylabel="ASR stage seconds",
        title="Reference media asset (M-1) ASR-stage wall time by model precision",
        filename="precision_asr_time_by_variant",
        out_dir=out_dir,
        copy_dir=copy_dir,
    )


def plot_scatter(rows: list[dict[str, Any]], out_dir: Path, copy_dir: Path | None) -> None:
    data = valid_wer_rows(rows)
    if not data:
        return
    fig, ax = plt.subplots(figsize=(7.2, 4.8))
    for row in data:
        x = float(row["rtf"])
        y = float(row["wer_pct"])
        ax.scatter(
            x,
            y,
            s=78,
            marker="s",
            color=ENGINE_COLORS.get(row.get("engine"), "#525252"),
            edgecolor="#222222",
            linewidth=0.7,
        )
        ax.annotate(
            short_label(row).replace("\n", " "),
            (x, y),
            xytext=(5, 4),
            textcoords="offset points",
            fontsize=7,
        )
    ax.set_xlabel("Real-time factor")
    ax.set_ylabel("WER (%)")
    if all(float(row["rtf"]) > 0 for row in data):
        ax.set_xscale("log")
    ax.grid(color="#dddddd", linewidth=0.8, which="both")
    ax.set_axisbelow(True)
    save(fig, out_dir, "precision_accuracy_speed", copy_dir)


def summarize_error(error: Any) -> str:
    text = str(error or "").replace("\n", " ")
    text = " ".join(text.split())
    if not text:
        return ""
    if "CUDA out of memory" in text:
        return "CUDA out of memory during timestamp transcription"
    if "Float8_e4m3fn" in text and "mul_cuda" in text:
        return '"mul_cuda" not implemented for Float8_e4m3fn'
    if "Float8_e5m2" in text and "mul_cuda" in text:
        return '"mul_cuda" not implemented for Float8_e5m2'
    if "int8_dynamic is unsupported" in text:
        return "Parakeet int8_dynamic unsupported for CUDA NeMo inference"
    if "max() iterable argument is empty" in text or "max() iterable" in text:
        return "Faster-Whisper int8 produced empty segment output"
    return text[-180:] if len(text) > 180 else text


def write_status_table(rows: list[dict[str, Any]], out_dir: Path, copy_dir: Path | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "| Dataset | Variant | Status | Engine | Model | Precision | WER % | RTF | Error |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for row in sorted(rows, key=lambda r: (r["_dataset_label"], r.get("variant", ""))):
        error = summarize_error(row.get("error")).replace("|", "/")
        wer = f"{float(row['wer_pct']):.2f}" if row.get("wer_pct") is not None else ""
        rtf = f"{float(row['rtf']):.5f}" if row.get("rtf") is not None and math.isfinite(float(row["rtf"])) else ""
        lines.append(
            "| {dataset} | {variant} | {status} | {engine} | {model} | {precision} | {wer} | {rtf} | {error} |".format(
                dataset=row["_dataset_label"],
                variant=row.get("variant", ""),
                status=row.get("status", ""),
                engine=row.get("engine", ""),
                model=row.get("model_short") or row.get("model", ""),
                precision=row.get("effective_precision") or row.get("precision", ""),
                wer=wer,
                rtf=rtf,
                error=error,
            )
        )
    path = out_dir / "precision_status_table.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if copy_dir:
        copy_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, copy_dir / path.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot Srtforge raw precision benchmark outputs.")
    parser.add_argument("--root", action="append", type=Path, help="Benchmark root. Can be repeated.")
    parser.add_argument("--out-dir", type=Path, default=Path("benchmark/figures"))
    parser.add_argument("--copy-to", type=Path, default=None)
    args = parser.parse_args()

    roots = args.root or list(DEFAULT_ROOTS)
    rows = load_rows(roots)
    if not rows:
        print("No metrics.json files found.")
        return 1
    plot_scatter(scatter_rows(rows), args.out_dir, args.copy_to)
    print(f"Wrote precision figures to {args.out_dir}")
    if args.copy_to:
        print(f"Copied precision figures to {args.copy_to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
