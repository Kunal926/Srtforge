# Worker protocol

`srtforge worker` runs a persistent loop that consumes one JSON object
per stdin line and emits one JSON object per stdout line, UTF-8 encoded.
Both the legacy PySide6 GUI and the current Tauri Studio rely on this
contract.

This document is the **canonical source of truth** for the protocol.
Any change must update:

- this document,
- `docs/contracts/worker-events.schema.json` and/or
  `docs/contracts/worker-requests.schema.json`,
- the Python emitters in `srtforge/cli.py` and `srtforge/logging.py`,
- the typed helpers in `srtforge/worker_protocol.py`,
- the TypeScript discriminated union in `srtforge-studio/src/types.ts`,
- the Zustand reducer in `srtforge-studio/src/store.ts`,
- the Rust forwarder in `srtforge-studio/src-tauri/src/lib.rs`,
- the contract tests in `tests/test_cli_worker.py` and
  `tests/test_worker_protocol.py`.

The `protocol-change` skill walks this lockstep.

## Conventions

- One JSON object per line, terminated by `\n`. No multi-line records.
- All strings are UTF-8.
- Timestamps, when present, are ISO-8601 UTC strings.
- Optional fields may be absent OR `null`. New optional fields must
  have a default that older consumers can ignore safely.
- Unknown fields are ignored by consumers — never crash on them.
- Stdout is JSON-only. Stack traces and unstructured logs go in a
  `traceback` field on `job_failed`, never raw on stdout.

## Requests (stdin → worker)

The worker accepts one JSON object per stdin line. Required:

```json
{"action": "<name>", ...}
```

### `transcribe`

Run the full pipeline against one media file. Required fields:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `action` | `"transcribe"` | yes | |
| `id` | string | yes | Caller-supplied job id; echoed back on every event |
| `file` | string (path) | yes | Absolute path to the media file |
| `output` | string (path) | no | Optional override; defaults to derived `<stem>.srt` |
| `config` | object | no | Per-job overrides (see below) |

Example:

```json
{"action":"transcribe","id":"job-01","file":"C:\\media\\ep01.mkv","output":"C:\\subs\\ep01.srt","config":{"prefer_gpu":false,"whisper":{"engine":"parakeet"}}}
```

The `config` object mirrors the shape of `srtforge.settings.AppSettings`
plus a few worker-only knobs. Common keys (not exhaustive):

- `prefer_gpu` (bool) — run ASR/separation on GPU when available.
- `whisper.engine` — `"parakeet"` or `"whisper"`.
- `whisper.model`, `whisper.language`, `whisper.force_float32`.
- `separation.backend`, `separation.sep_hz`, `separation.prefer_center`.
- `separation.fv4.ckpt`, `separation.fv4.cfg`.
- `ffmpeg.filter_chain`, `ffmpeg.extraction_mode`.
- `gemini.enabled`, `gemini.model_id`, `gemini.api_key`.
- `output.replace_original`, `output.burn`, `output.sidecar_srt`,
  `output.embed.{enabled,method,track_title,track_lang,default,forced}`.
- `paths.output_dir`, `paths.temp_dir`.

Missing keys fall back to the values resolved by `srtforge.settings`.

### `normalize`

Run a standalone audio normalize/transcode (Studio "Normalize" tool).
The pipeline isn't invoked.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `action` | `"normalize"` | yes | |
| `id` | string | yes | Job id echoed on events |
| `file` | string (path) | yes | Absolute path to source audio/video |
| `config` | object | no | See below |

`config` keys:

- `format` — `"wav" | "flac" | "mp3"` (default `wav`).
- `sample_rate` — int Hz (default `48000`).
- `bit_depth` — `16 | 24 | 32` (default `16`).
- `channels` — `1 | 2` (default `2`).
- `loudness` — bool (default `false`); enables EBU R128 loudnorm.
- `filter_chain` — optional ffmpeg filter chain string.
- `output` — optional output directory.

### `separate`

Run a standalone vocal/instrumental separation pass (Studio "BGM"
tool).

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `action` | `"separate"` | yes | |
| `id` | string | yes | |
| `file` | string (path) | yes | |
| `config` | object | no | See below |

`config` keys:

- `stems` — list of `"vocals"`, `"instrumental"` (default `["vocals"]`).
- `prefer_gpu` — bool.
- `model` — optional FV4 ckpt override.
- `config` — optional FV4 yaml override.
- `output` — optional output directory.

### `shutdown`

```json
{"action":"shutdown"}
```

Worker emits `worker_stopping` and exits cleanly.

### `clear_gpu_cache`

```json
{"action":"clear_gpu_cache"}
```

Best-effort: if `torch` is loaded and CUDA is available, calls
`torch.cuda.empty_cache()` and `torch.cuda.ipc_collect()`. Emits
`gpu_cache_cleared`, `gpu_cache_skipped`, or `gpu_cache_failed`.

## Events (worker → stdout)

Every event has an `event` discriminator field. Most carry an `id`
field correlating them with the originating request.

### Lifecycle events

#### `worker_starting`

Emitted once on startup before any preload.

| Field | Type | Required |
| --- | --- | --- |
| `event` | `"worker_starting"` | yes |
| `pid` | int | yes |
| `preload` | bool | yes |
| `cpu` | bool | yes |

#### `worker_ready`

Emitted once when preload (if any) is complete and the worker is
ready to accept requests.

| Field | Type | Required |
| --- | --- | --- |
| `event` | `"worker_ready"` | yes |
| `pid` | int | yes |

#### `worker_stopping`

Emitted in response to `{"action":"shutdown"}` immediately before
the loop exits.

#### `terminated`

Emitted by the **Tauri shell** (not by the Python worker) when the
child process exits. Carries the exit code if known.

| Field | Type | Required |
| --- | --- | --- |
| `event` | `"terminated"` | yes |
| `code` | int \| null | no |

### Preload events

#### `worker_preload_skipped`

Emitted when the preload path was selected but no preload runs (e.g.
ASR engine is `parakeet` and only Whisper has a preloader today).

| Field | Type |
| --- | --- |
| `event` | `"worker_preload_skipped"` |
| `reason` | string |

#### `worker_preload_failed`

Emitted when preload was attempted but raised. Worker continues to
accept jobs (the model will be loaded lazily on the first transcribe).

| Field | Type |
| --- | --- |
| `event` | `"worker_preload_failed"` |
| `error` | string |

### Job events

#### `job_started`

Emitted at the very beginning of every job (transcribe / normalize /
separate).

| Field | Type | Required |
| --- | --- | --- |
| `event` | `"job_started"` | yes |
| `id` | string | yes |
| `file` | string | no |
| `kind` | string | no — present for `normalize` / `separate` (`"normalize"`, `"separate"`) |

#### `stage`

Emitted on enter and exit of each major pipeline phase. Powered by
`srtforge.logging.set_event_emitter` and the `stage="..."` keyword on
`RunLogger.step`.

| Field | Type | Required |
| --- | --- | --- |
| `event` | `"stage"` | yes |
| `id` | string | yes |
| `stage` | string | yes — one of `probe`, `extract`, `separation`, `preprocess`, `asr`, `post`, `write`, `mux`, `burn` |
| `state` | `"start" \| "end"` | yes |
| `msg` | string | optional — human-readable step label (e.g. `"Probe audio streams"`) |
| `run_id` | string | optional — `RunLogger.run_id` for the current pipeline run |
| `seconds` | number | only on `state:"end"` |
| `ok` | bool | only on `state:"end"` |

#### `progress`

Reserved for sub-stage progress signals (e.g. ASR streaming partial
results). Not emitted by the current pipeline yet — see
`docs/agent/QUALITY.md`. Consumers must accept either `fraction` or
`progress` as the 0..1 numeric payload.

| Field | Type |
| --- | --- |
| `event` | `"progress"` |
| `id` | string |
| `stage` | string (see `stage` event values) — optional |
| `fraction` | number (0..1) — optional |
| `progress` | number (0..1) — optional alias |
| `eta` | string — optional |

#### `log`

Free-form log line. Consumed by the Studio for the bottom log pane;
also used as a fallback when the Rust shell can't parse a stdout line
as JSON.

| Field | Type |
| --- | --- |
| `event` | `"log"` |
| `t` | string — optional |
| `lvl` | string — optional |
| `msg` | string — required |

#### `srt_written`

Emitted by `transcribe` after the SRT file has been written to disk.

| Field | Type | Required |
| --- | --- | --- |
| `event` | `"srt_written"` | yes |
| `id` | string | yes |
| `path` | string | yes |

#### `media_written`

Emitted after an embed and/or burn step produced a new media file.
Zero, one, or two `media_written` events may follow `srt_written` for
a single transcribe job depending on which output flags are enabled.

| Field | Type | Required |
| --- | --- | --- |
| `event` | `"media_written"` | yes |
| `id` | string | yes |
| `kind` | `"embedded" \| "burned"` | yes |
| `path` | string | yes |

#### `asset_written`

Emitted by `normalize` and `separate` per output file. `kind` describes
which asset was produced (e.g. `"normalize"`, `"vocals"`,
`"instrumental"`).

| Field | Type | Required |
| --- | --- | --- |
| `event` | `"asset_written"` | yes |
| `id` | string | yes |
| `kind` | string | yes |
| `path` | string | yes |

#### `job_completed`

Emitted at the end of a successful job.

| Field | Type | Required |
| --- | --- | --- |
| `event` | `"job_completed"` | yes |
| `id` | string | yes |
| `seconds` | number \| null | no |

#### `job_failed`

Emitted when a job fails. Always terminates the job's event stream.

| Field | Type | Required |
| --- | --- | --- |
| `event` | `"job_failed"` | yes |
| `id` | string | yes |
| `file` | string | no |
| `run_id` | string | no |
| `error` | string | yes |
| `traceback` | string | no |

### Rejection events

#### `bad_json`

Emitted when stdin contained a line the worker couldn't parse as JSON.

| Field | Type | Required |
| --- | --- | --- |
| `event` | `"bad_json"` | yes |
| `line` | string (truncated to 500 chars) | yes |

#### `bad_payload`

Emitted when stdin parsed but wasn't a JSON object.

| Field | Type | Required |
| --- | --- | --- |
| `event` | `"bad_payload"` | yes |
| `reason` | string | yes |

#### `unknown_action`

Emitted when `payload["action"]` was not a recognized action name.

| Field | Type | Required |
| --- | --- | --- |
| `event` | `"unknown_action"` | yes |
| `action` | string | yes |

### GPU cache events

#### `gpu_cache_cleared`

Emitted after `torch.cuda.empty_cache()` succeeded.

| Field | Type |
| --- | --- |
| `event` | `"gpu_cache_cleared"` |

#### `gpu_cache_skipped`

Emitted when CUDA was unavailable or torch wasn't loaded.

| Field | Type |
| --- | --- |
| `event` | `"gpu_cache_skipped"` |
| `reason` | string |

#### `gpu_cache_failed`

Emitted when the cache-clear path raised.

| Field | Type |
| --- | --- |
| `event` | `"gpu_cache_failed"` |
| `error` | string |

## Event ordering invariants

For a successful `transcribe` job:

```
job_started
  (stage|progress|log)*
srt_written
  (media_written)*    # 0..2 times
job_completed
```

For a failed `transcribe` job:

```
job_started
  (stage|progress|log)*
job_failed
```

For `normalize` / `separate`:

```
job_started
  (stage|progress|log)*
asset_written           # one or more
job_completed
```

or:

```
job_started
  (stage|progress|log)*
job_failed
```

The worker never interleaves events from different `id`s — at most one
job runs at a time today.

## Error handling expectations

- Consumers MUST accept unknown `event` discriminator values without
  crashing (forward as `log` with `lvl: "info"`).
- Consumers MUST accept unknown fields on known events.
- Producers MUST NOT emit malformed JSON. If serialization fails, the
  job becomes `job_failed` with the serialization error in `traceback`.
- Stdout is JSON-only. Stderr may carry unstructured Python warnings.
  The Tauri shell folds stderr lines into `log` events with
  `lvl:"warn"`.
