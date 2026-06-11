# Srtforge Benchmark Artifacts

This directory contains the benchmark data used by the final paper. It is
trimmed for repository publication: old pilot datasets, discarded abnormal
datasets, temporary audio, generated subtitle payloads, and per-run word dumps
are intentionally excluded.

## Retained Studies

- `steinsgate_s01e22_precision/`: selected reference media asset precision
  study for raw ASR and FV4-separated Srtforge runs.
- `steinsgate_s01_all_best/`: 24-episode dataset-wide Whisper
  `int8_float16` comparison, raw audio versus FV4-separated Srtforge.
- `figures/precision_accuracy_speed.*`: source copy of the paper's
  accuracy-speed trade-off plot.

The paper copies of the final figures live in `srt/figures/`.

## Key Results

Selected media asset, name-error exclusion applied only to Srtforge FV4 rows:

| ASR | Precision | Raw WER % | FV4 WER % | Raw RTF | FV4 RTF |
| --- | --- | ---: | ---: | ---: | ---: |
| Parakeet v2 | fp32 | 7.22 | 4.49 | 0.136 | 0.236 |
| Parakeet v2 | fp16 | 6.27 | 4.58 | 0.106 | 0.166 |
| Parakeet v2 | bf16 | 6.70 | 4.97 | 0.116 | 0.190 |
| Whisper large-v3-turbo | fp16 | 5.01 | 3.85 | 0.110 | 0.162 |
| Whisper large-v3-turbo | bf16 | 5.32 | 3.72 | 0.086 | 0.176 |
| Whisper large-v3-turbo | int8_float16 | 5.49 | 3.80 | 0.069 | 0.160 |

Dataset-wide result over 63,316 reference words:

| Setting | Weighted WER % | Macro WER % | Median WER % |
| --- | ---: | ---: | ---: |
| Srtforge FV4 + Whisper int8_float16 | 5.26 | 5.31 | 5.34 |
| Raw Whisper int8_float16 | 6.57 | 6.69 | 6.61 |

## Retained Files

Each study keeps only aggregate artifacts needed to audit the paper:

- `manifest.json`: benchmark matrix and environment/source metadata.
- `metrics.csv` / `metrics.json`: completed row metrics.
- `report.md`: human-readable benchmark summary.
- `figures/`: final benchmark plots used for paper drafting.
- `truth_assisted/metrics_*.csv|json`: dataset-wide assisted-truth rescoring.
- `truth_assisted/S01E*.truth.txt`: normalized assisted-truth references used
  for WER reproduction.
- `truth_assisted/S01E*.corrections.json` and `corrections.csv`: assisted-truth
  edit audit.
- `truth_assisted/srtforge_name_error_diffs.*`: audit of name-error exclusions
  used to approximate the disabled correction pass.

Full generated `.srt` files, `.ass` sidecars, temporary `.wav` files, and ASR
word timestamp dumps were removed from this publication-oriented bundle. The
normalized truth text is retained because the reported WER values cannot be
independently recomputed from aggregate counts alone.

## Regeneration

The scripts are kept for local regeneration with the original media and
sidecar subtitle files available on disk:

```powershell
.\.venv\Scripts\python.exe .\benchmark\run_precision_benchmark.py --dataset steins_s01e22 --timeout-seconds 5400
.\.venv\Scripts\python.exe .\benchmark\plot_precision_benchmark.py --copy-to .\srt\figures
.\.venv\Scripts\python.exe .\benchmark\run_steinsgate_series_benchmark.py --skip-existing
.\.venv\Scripts\python.exe .\benchmark\create_steinsgate_assisted_truth.py
.\.venv\Scripts\python.exe .\benchmark\plot_steinsgate_series_benchmark.py --copy-to .\srt\figures
```

Gemini correction is disabled for generation. The name-error exclusion is a
deterministic scoring proxy applied only to Srtforge rows, matching the paper's
offline benchmark protocol.
