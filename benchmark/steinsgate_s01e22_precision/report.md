# Reference Media Asset Precision Benchmark

Dataset label: M-1
Reference words: 2314

Srtforge FV4 rows use the deterministic name-error-exclusion scoring proxy. Raw ASR rows use normal WER.

| ASR | Precision | Raw WER % | FV4 WER % | Raw RTF | FV4 RTF |
| --- | --- | ---: | ---: | ---: | ---: |
| Parakeet v2 | fp32 | 7.22 | 4.49 | 0.136 | 0.236 |
| Parakeet v2 | fp16 | 6.27 | 4.58 | 0.106 | 0.166 |
| Parakeet v2 | bf16 | 6.70 | 4.97 | 0.116 | 0.190 |
| Whisper large-v3-turbo | fp16 | 5.01 | 3.85 | 0.110 | 0.162 |
| Whisper large-v3-turbo | bf16 | 5.32 | 3.72 | 0.086 | 0.176 |
| Whisper large-v3-turbo | int8_float16 | 5.49 | 3.80 | 0.069 | 0.160 |

Full generated SRT files and word-timestamp dumps are omitted from the publication bundle.
