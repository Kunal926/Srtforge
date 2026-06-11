# Dataset-Wide Assisted-Truth Benchmark

Dataset: 24-episode evaluation set
Reference words: 63,316

| Setting | Weighted WER % | Macro WER % | Median WER % | Scoring |
| --- | ---: | ---: | ---: | --- |
| Srtforge FV4 + Whisper int8_float16 | 5.26 | 5.31 | 5.34 | name-error-excluded Srtforge WER |
| Raw Whisper int8_float16 | 6.57 | 6.69 | 6.61 | normal WER |

The retained per-episode metrics in `metrics.csv` use this final paper scoring policy.
Generated subtitles, source sidecar subtitles, temporary audio, and word timestamp dumps are omitted.
