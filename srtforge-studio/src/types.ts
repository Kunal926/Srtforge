// Shared types for the React UI.
// The wire format mirrors the existing Srtforge worker protocol.

export type FileStatus = "queued" | "processing" | "done" | "error";

export interface QueueFile {
  id: string;
  name: string;
  path: string;            // input media file path
  outputPath?: string;     // SRT output path, set on srt_written
  duration: string;        // "23:54"
  durationSec: number;
  sampleRate: number;      // kHz
  channels: number;
  fps: string;
  codec: string;
  status: FileStatus;
  progress: number;        // 0..1
  eta: string;             // "05:11" or "—" or "✓"
  stage: number;           // 0..6 from STAGES
  error?: string;
}

export interface LogLine {
  t: string;       // "01:48.302"
  lvl: "info" | "ok" | "warn" | "err" | string;
  msg: string;
  run?: boolean;
}

export interface Settings {
  // Compute
  device: "auto" | "cuda" | "cpu";
  gpuPct: number;
  fp32: boolean;
  preferGpu: boolean;
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

/** Worker → frontend wire events (one JSON line per emit). */
export type WorkerEvent =
  | { event: "queued"; id: string; file: string; meta: Partial<QueueFile> }
  | { event: "started"; id: string }
  | { event: "stage"; id: string; stage: number }
  | { event: "progress"; id: string; progress: number; eta: string }
  | { event: "log"; t?: string; lvl?: string; msg: string }
  | { event: "srt_written"; id: string; path: string }
  | { event: "job_failed"; id: string; error: string }
  | { event: "paused" }
  | { event: "resumed" }
  | { event: "terminated"; code: number | null }
  | { event: string; [key: string]: unknown };
