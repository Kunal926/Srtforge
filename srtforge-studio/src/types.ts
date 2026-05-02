// Shared types for the React UI.
// The wire format mirrors the existing Srtforge worker protocol.

export type FileStatus = "queued" | "processing" | "done" | "error";

export interface QueueFile {
  id: string;
  name: string;
  path: string;            // input media file path
  outputPath?: string;     // SRT output path, set on srt_written
  embeddedPath?: string;   // muxed media path, set on media_written
  burnedPath?: string;     // hard-subbed media path, set on media_written
  runId?: string;          // Python RunLogger id for pipeline-backed jobs
  performanceLogPath?: string; // pipeline timing log
  debugLogPath?: string;   // Studio live/Typer debug log
  duration: string;        // "23:54"
  durationSec: number;
  sampleRate: number;      // kHz
  channels: number;
  fps: string;
  codec: string;
  status: FileStatus;
  progress: number;        // 0..1
  eta: string;             // "05:11" or "—" or "✓"
  stage: number;           // 0..6 from STAGES (highest stage seen so far)
  /** Per-stage timing once a stage end event lands. Keyed by stage name. */
  stageDurations?: Record<string, number>;
  error?: string;
}

export interface LogLine {
  t: string;       // "01:48.302"
  lvl: "info" | "ok" | "warn" | "err" | string;
  msg: string;
  source?: string;
  run?: boolean;
}

export interface Settings {
  // Compute
  device: "auto" | "cuda" | "cpu";
  gpuPct: number;
  fp32: boolean;
  preferGpu: boolean;
  gpuPerformanceMode: boolean;
  // Separation
  sep: "fv4" | "none";
  preferCenter: boolean;
  sepHz: number;
  allowUntaggedEnglish: boolean;
  fv4Cfg: string;
  fv4Ckpt: string;
  // ASR
  engine: "parakeet" | "whisper";
  asrModel: string;
  language: string;
  attnLeft: number;
  attnRight: number;
  subsamplingChunkFactor: number;
  // Output
  embed: boolean;
  burn: boolean;
  outputDir: string;
  tempDir: string;
  style: "netflix" | "bbc" | "custom";
  softEmbed: "auto" | "mkvtoolnix" | "ffmpeg";
  trackTitle: string;
  trackLang: string;
  defaultTrack: boolean;
  forcedTrack: boolean;
  replaceOriginal: boolean;
  sidecarSrt: boolean;
  dumpWords: boolean;
  freeGpuOnStop: boolean;
  // FFmpeg
  extract: "stereo_mix" | "dual_mono_center";
  filterChain: string;
  // Gemini
  geminiEnabled: boolean;
  geminiModel: string;
  geminiKey: string;
  // Integration
  sonarr: boolean;
}

export type Tab =
  | "queue"
  | "active"
  | "history"
  | "normalize"
  | "bgm"
  | "watch";

export type Theme = "light" | "dark" | "forge";
export type Layout = "card" | "hybrid";
export type Density = "comfortable" | "compact";

/** Canonical stage names emitted by the Python pipeline. */
export type WorkerStage =
  | "probe"
  | "extract"
  | "separation"
  | "preprocess"
  | "asr"
  | "post"
  | "write"
  | "mux"
  | "burn";

/** Worker → frontend wire events (one JSON line per emit). */
export type WorkerEvent =
  | { event: "queued"; id: string; file: string; meta: Partial<QueueFile> }
  | { event: "started"; id: string }
  | { event: "job_started"; id: string; file?: string; kind?: string; debug_log_path?: string }
  | {
      event: "stage";
      id: string;
      stage: WorkerStage;
      state: "start" | "end";
      run_id?: string;
      debug_log_path?: string;
      seconds?: number;
      ok?: boolean;
    }
  | {
      event: "progress";
      id: string;
      stage?: WorkerStage;
      fraction?: number;
      progress?: number;
      eta?: string;
      debug_log_path?: string;
    }
  | { event: "log"; id?: string; t?: string; lvl?: string; msg: string; source?: string; debug_log_path?: string }
  | {
      event: "srt_written";
      id: string;
      path: string;
      run_id?: string;
      performance_log_path?: string;
      debug_log_path?: string;
    }
  | { event: "media_written"; id: string; kind: "embedded" | "burned"; path: string; debug_log_path?: string }
  | { event: "asset_written"; id: string; kind: string; path: string; debug_log_path?: string }
  | {
      event: "job_completed";
      id: string;
      seconds?: number | null;
      run_id?: string;
      performance_log_path?: string;
      debug_log_path?: string;
    }
  | {
      event: "job_failed";
      id: string;
      error: string;
      run_id?: string;
      performance_log_path?: string;
      debug_log_path?: string;
      traceback?: string;
    }
  | { event: "paused" }
  | { event: "resumed" }
  | { event: "terminated"; code: number | null; debug_log_path?: string }
  | { event: "gpu_cache_cleared" }
  | { event: "gpu_cache_skipped"; reason?: string }
  | { event: "gpu_cache_failed"; error?: string }
  | { event: string; [key: string]: unknown };

/** A persisted entry in the Watch Folders view. UI-only this round. */
export type WatchSource = "sonarr" | "radarr" | "folder";
export type WatchSchedule = "live" | "5m" | "15m" | "1h" | "manual";

export interface WatchLibrary {
  id: string;
  name: string;
  path: string;
  source: WatchSource;
  schedule: WatchSchedule;
  /** When false the row stays in the list but is treated as inactive. */
  enabled: boolean;
  /** Optional Sonarr/Radarr instance label, e.g. "main" / "anime". */
  instance?: string;
  /** Counters surfaced in the row. Defaults to 0 when unknown. */
  itemsCount?: number;
  pendingCount?: number;
}
