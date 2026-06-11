from __future__ import annotations

import argparse
import csv
import re
import shutil
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt


DEFAULT_ROOT = Path("benchmark/steinsgate_s01_all_best")

LABELS = {
    "srtforge_fv_whisper_int8_float16": "Srtforge FV4 + Whisper int8_float16",
    "raw_whisper_int8_float16": "Raw Whisper int8_float16",
}
COLORS = {
    "srtforge_fv_whisper_int8_float16": "#2563eb",
    "raw_whisper_int8_float16": "#dc2626",
}


def episode_label(episode: str) -> str:
    match = re.search(r"E(\d+)$", episode)
    if match:
        return str(int(match.group(1)))
    return episode


def read_rows(root: Path) -> list[dict[str, Any]]:
    path = root / "metrics.csv"
    if not path.exists():
        return []
    return read_csv_rows(path)


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fp:
        rows = []
        for row in csv.DictReader(fp):
            parsed: dict[str, Any] = dict(row)
            for key in ("wer_pct", "rtf", "wall_seconds", "reference_words", "word_errors"):
                value = parsed.get(key)
                parsed[key] = float(value) if value not in (None, "") else None
            rows.append(parsed)
        return rows


def adjusted_srtforge_vs_raw_rows(root: Path) -> list[dict[str, Any]]:
    assisted_path = root / "truth_assisted" / "metrics_assisted.csv"
    name_path = root / "truth_assisted" / "metrics_name_errors_excluded.csv"
    if not assisted_path.exists() or not name_path.exists():
        return []
    assisted = read_csv_rows(assisted_path)
    name_excluded = read_csv_rows(name_path)
    rows: list[dict[str, Any]] = []
    for row in name_excluded:
        if row.get("variant") == "srtforge_fv_whisper_int8_float16_name_errors_excluded":
            adjusted = dict(row)
            adjusted["variant"] = "srtforge_fv_whisper_int8_float16"
            rows.append(adjusted)
    rows.extend(row for row in assisted if row.get("variant") == "raw_whisper_int8_float16")
    return rows


def completed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get("wer_pct") is not None and row.get("status", "completed") == "completed"
    ]


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


def pivot(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, float]]:
    table: dict[str, dict[str, float]] = {}
    for row in completed(rows):
        table.setdefault(row["episode"], {})[row["variant"]] = float(row[key])
    return table


def plot_episode_wer(rows: list[dict[str, Any]], out_dir: Path, copy_dir: Path | None) -> None:
    data = pivot(rows, "wer_pct")
    episodes = sorted(data)
    variants = list(LABELS)
    width = 0.38
    x = list(range(len(episodes)))
    fig, ax = plt.subplots(figsize=(11.2, 4.8))
    for idx, variant in enumerate(variants):
        offset = (idx - 0.5) * width
        values = [data[episode].get(variant, 0.0) for episode in episodes]
        ax.bar(
            [v + offset for v in x],
            values,
            width=width,
            label=LABELS[variant],
            color=COLORS[variant],
            edgecolor="#222222",
            linewidth=0.35,
        )
    ax.axhline(6.0, color="#111111", linestyle="--", linewidth=0.9)
    ax.text(len(episodes) - 0.1, 6.08, "6% target", ha="right", va="bottom", fontsize=8)
    ax.set_xticks(x, [episode_label(episode) for episode in episodes], rotation=0, ha="center", fontsize=8)
    ax.set_ylabel("WER (%)")
    ax.set_title("Season-scale WER by episode")
    ax.grid(axis="y", color="#dddddd", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8)
    save(fig, out_dir, "steinsgate_s01_episode_wer", copy_dir)


def plot_delta(
    rows: list[dict[str, Any]],
    out_dir: Path,
    copy_dir: Path | None,
    *,
    title: str = "Vocal-separation WER change by episode",
    filename: str = "steinsgate_s01_fv_delta",
) -> None:
    data = pivot(rows, "wer_pct")
    episodes = sorted(data)
    deltas = [
        data[episode]["srtforge_fv_whisper_int8_float16"] - data[episode]["raw_whisper_int8_float16"]
        for episode in episodes
        if set(LABELS).issubset(data[episode])
    ]
    episodes = [episode for episode in episodes if set(LABELS).issubset(data[episode])]
    fig, ax = plt.subplots(figsize=(10.6, 4.2))
    colors = ["#3b6ea5" if value < 0 else "#b0413e" for value in deltas]
    ax.bar(episodes, deltas, color=colors, edgecolor="#222222", linewidth=0.35)
    ax.axhline(0.0, color="#111111", linewidth=0.9)
    ax.set_xticks(range(len(episodes)), [episode_label(episode) for episode in episodes], rotation=0, ha="center", fontsize=8)
    ax.set_ylabel("FV WER - raw WER (points)")
    ax.set_xlabel("Episode")
    if title:
        ax.set_title(title)
    ax.grid(axis="y", color="#dddddd", linewidth=0.8)
    ax.set_axisbelow(True)
    save(fig, out_dir, filename, copy_dir)


def plot_summary(rows: list[dict[str, Any]], out_dir: Path, copy_dir: Path | None) -> None:
    data = completed(rows)
    variants = list(LABELS)
    weighted = []
    mean_rtf = []
    for variant in variants:
        subset = [row for row in data if row["variant"] == variant]
        errors = sum(float(row.get("substitutions") or 0) + float(row.get("deletions") or 0) + float(row.get("insertions") or 0) for row in subset)
        words = sum(float(row.get("reference_words") or 0) for row in subset)
        weighted.append(100.0 * errors / words if words else 0.0)
        mean_rtf.append(sum(float(row.get("rtf") or 0) for row in subset) / len(subset) if subset else 0.0)

    fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.8))
    axes[0].bar([LABELS[v] for v in variants], weighted, color=[COLORS[v] for v in variants], edgecolor="#222222")
    axes[0].set_ylabel("Weighted WER (%)")
    axes[0].set_title("Accuracy")
    axes[0].grid(axis="y", color="#dddddd")
    axes[1].bar([LABELS[v] for v in variants], mean_rtf, color=[COLORS[v] for v in variants], edgecolor="#222222")
    axes[1].set_ylabel("Mean RTF")
    axes[1].set_title("Throughput")
    axes[1].grid(axis="y", color="#dddddd")
    for ax in axes:
        ax.tick_params(axis="x", labelrotation=20, labelsize=8)
        ax.set_axisbelow(True)
    fig.suptitle("Season-scale all-episode summary")
    fig.tight_layout()
    save(fig, out_dir, "steinsgate_s01_summary", copy_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot the season-scale all-episode benchmark.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--copy-to", type=Path, default=None)
    args = parser.parse_args()

    out_dir = args.root / "figures"
    assisted_rows = adjusted_srtforge_vs_raw_rows(args.root)
    if assisted_rows:
        plot_delta(
            assisted_rows,
            out_dir,
            args.copy_to,
            title="",
            filename="season_assisted_fv_delta",
        )
    print(f"Wrote figures to {out_dir}")
    if args.copy_to:
        print(f"Copied figures to {args.copy_to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
