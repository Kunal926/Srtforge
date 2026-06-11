import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

import type {
  Density,
  FileStatus,
  JobSettingsSummary,
  Layout,
  LogLine,
  QueueFile,
  Settings,
  Tab,
  Theme,
  WatchLibrary,
  WorkerEvent,
  WorkerStage,
} from "./types";
import {
  recordWaveformStageDuration,
  shouldRecordWaveformStageDuration,
} from "./lib/waveformEta";
import { DONE_VALUE, EMPTY_VALUE } from "./lib/format";
import { recordQueueEtaSample } from "./lib/queueEta";
import {
  DEFAULT_ASR_MODEL,
  asrEngineForModel,
  normalizeAsrModel,
} from "./lib/asrModels";

// Map worker stage names → numeric index used by `QueueFile.stage`.
// `mux` and `burn` are optional post-write stages and don't bump the
// dot count; they're surfaced through `media_written` events instead.
const STAGE_INDEX: Record<WorkerStage, number> = {
  probe: 0,
  extract: 1,
  separation: 2,
  preprocess: 3,
  asr: 4,
  post: 5,
  write: 6,
  mux: 6,
  burn: 6,
};

const MAX_LIVE_LOG_LINES = 400;
const MAX_TOASTS = 4;
const TOAST_MS = 2200;

interface ToastItem {
  id: string;
  msg: string;
}

const appendLiveLog = (logs: LogLine[], line: LogLine) => {
  if (logs.length < MAX_LIVE_LOG_LINES) return [...logs, line];
  return [...logs.slice(logs.length - MAX_LIVE_LOG_LINES + 1), line];
};

const elapsedJobSeconds = (file: QueueFile, reported?: number | null) => {
  if (typeof reported === "number" && Number.isFinite(reported) && reported > 0) {
    return reported;
  }
  if (typeof file.jobStartedAtMs === "number") {
    return Math.max(0, (Date.now() - file.jobStartedAtMs) / 1000);
  }
  return undefined;
};

const DEFAULT_SETTINGS: Settings = {
  device: "cuda",
  gpuPct: 100,
  fp32: false,
  preferGpu: true,
  gpuPerformanceMode: true,
  sep: "fv4",
  preferCenter: true,
  sepHz: 44100,
  allowUntaggedEnglish: false,
  fv4Cfg: "./models/voc_gabox.yaml",
  fv4Ckpt: "./models/voc_fv4.ckpt",
  engine: asrEngineForModel(DEFAULT_ASR_MODEL),
  asrModel: DEFAULT_ASR_MODEL,
  whisperComputeType: "int8_float16",
  language: "en",
  attnLeft: 1280,
  attnRight: 1280,
  subsamplingChunkFactor: 0,
  embed: true,
  burn: false,
  outputDir: "./output",
  tempDir: "./tmp",
  style: "netflix",
  softEmbed: "auto",
  trackTitle: "Srtforge (English)",
  trackLang: "eng",
  defaultTrack: true,
  forcedTrack: true,
  replaceOriginal: true,
  sidecarSrt: true,
  dumpWords: false,
  freeGpuOnStop: true,
  extract: "dual_mono_center",
  filterChain:
    "highpass=f=60,lowpass=f=10000,aformat=sample_fmts=flt,aresample=resampler=soxr:osf=flt:osr=16000",
  geminiEnabled: false,
  geminiModel: "gemini-3-flash-preview",
  geminiKey: "",
  sonarr: true,
};

const formatStageLogSeconds = (seconds: number | undefined) =>
  typeof seconds === "number" && Number.isFinite(seconds) ? ` (${seconds.toFixed(2)}s)` : "";

const stageLogLine = (
  stage: WorkerStage,
  state: "start" | "end",
  msg: string | undefined,
  seconds: number | undefined,
  ok: boolean | undefined,
): LogLine => {
  const label = msg ?? stage;
  const t = new Date().toLocaleTimeString([], { hour12: false });
  if (state === "start") {
    return {
      t,
      lvl: "info",
      msg: `Stage started: ${label}`,
      source: "stage",
      run: true,
    };
  }

  return {
    t,
    lvl: ok === false ? "err" : "ok",
    msg: `${ok === false ? "Stage failed" : "Stage finished"}: ${label}${formatStageLogSeconds(seconds)}`,
    source: "stage",
    run: true,
  };
};

interface UiState {
  files: QueueFile[];
  selectedId: string | null;
  checked: Set<string>;
  active: Tab;
  running: boolean;
  queuePaused: boolean;
  settingsOpen: boolean;
  logs: LogLine[];
  search: string;
  toasts: ToastItem[];

  theme: Theme;
  layout: Layout;
  density: Density;

  settings: Settings;

  /** Watch Folders state — UI-only this round. Persisted across reloads. */
  libraries: WatchLibrary[];

  // Actions
  setActive: (t: Tab) => void;
  setSelectedId: (id: string | null) => void;
  toggleChecked: (id: string) => void;
  clearChecked: () => void;
  setSearch: (q: string) => void;
  setSettingsOpen: (open: boolean) => void;
  setSetting: <K extends keyof Settings>(k: K, v: Settings[K]) => void;
  setTheme: (t: Theme) => void;
  setLayout: (l: Layout) => void;
  setDensity: (d: Density) => void;

  setRunning: (r: boolean) => void;
  setQueuePaused: (p: boolean) => void;
  resetSettings: () => void;

  // Watch folders (UI-only this round)
  addLibrary: (lib: Omit<WatchLibrary, "id">) => string;
  removeLibrary: (id: string) => void;
  toggleLibraryEnabled: (id: string) => void;
  updateLibrary: (id: string, patch: Partial<WatchLibrary>) => void;

  addFiles: (files: QueueFile[]) => void;
  /** Add raw file paths picked from disk; the pump turns them into worker jobs. */
  enqueuePaths: (paths: string[]) => string[];
  /** Promote one queued file to "sent" before invoking enqueue() on the Rust side. */
  markSending: (
    id: string,
    runSettings?: JobSettingsSummary,
    plannedOutputPath?: string,
  ) => void;
  /** Fail a row when the Rust bridge cannot hand it to the worker. */
  markDispatchFailed: (id: string, error: string) => void;
  /** Patch one row's metadata (post-probe). */
  updateFileMeta: (id: string, meta: Partial<QueueFile>) => void;
  removeChecked: () => void;
  clearQueue: () => void;
  clearHistory: () => void;
  showToast: (msg: string) => void;

  handleWorkerEvent: (ev: WorkerEvent) => void;
}

const toastTimers = new Map<string, ReturnType<typeof setTimeout>>();

const hideToast = (id: string) => {
  const timer = toastTimers.get(id);
  if (timer) {
    clearTimeout(timer);
    toastTimers.delete(id);
  }
  useUi.setState((s) => ({
    toasts: s.toasts.filter((toast) => toast.id !== id),
  }));
};

const normalizeEtaValue = (value: string | undefined) => {
  if (!value) return EMPTY_VALUE;
  const trimmed = value.trim();
  return trimmed === "\u00e2\u20ac\u201d" ||
    trimmed === "\u00c3\u00a2\u00e2\u201a\u00ac\u00e2\u20ac\u009d" ||
    trimmed === "\u2014"
    ? EMPTY_VALUE
    : value;
};

export const useUi = create<UiState>()(
  persist(
    (set, get) => ({
  files: [],
  selectedId: null,
  checked: new Set(),
  active: "queue",
  running: false,
  queuePaused: false,
  settingsOpen: false,
  logs: [],
  search: "",
  toasts: [],

  theme: "dark",
  layout: "hybrid",
  density: "comfortable",

  settings: DEFAULT_SETTINGS,

  libraries: [],

  setActive: (t) => set({ active: t }),
  setSelectedId: (id) => set({ selectedId: id }),
  toggleChecked: (id) =>
    set((s) => {
      const n = new Set(s.checked);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return { checked: n };
    }),
  clearChecked: () => set({ checked: new Set() }),
  setSearch: (q) => set({ search: q }),
  setSettingsOpen: (open) => set({ settingsOpen: open }),
  setSetting: (k, v) =>
    set((s) => ({ settings: { ...s.settings, [k]: v } })),
  setTheme: (t) => set({ theme: t }),
  setLayout: (l) => set({ layout: l }),
  setDensity: (d) => set({ density: d }),

  setRunning: (r) => set({ running: r }),
  setQueuePaused: (p) => set({ queuePaused: p }),
  resetSettings: () => set({ settings: DEFAULT_SETTINGS }),

  addLibrary: (lib) => {
    const id = crypto.randomUUID();
    set((s) => ({
      libraries: [
        ...s.libraries,
        {
          itemsCount: 0,
          pendingCount: 0,
          ...lib,
          id,
        },
      ],
    }));
    return id;
  },
  removeLibrary: (id) =>
    set((s) => ({ libraries: s.libraries.filter((l) => l.id !== id) })),
  toggleLibraryEnabled: (id) =>
    set((s) => ({
      libraries: s.libraries.map((l) =>
        l.id === id ? { ...l, enabled: !l.enabled } : l,
      ),
    })),
  updateLibrary: (id, patch) =>
    set((s) => ({
      libraries: s.libraries.map((l) => (l.id === id ? { ...l, ...patch } : l)),
    })),

  addFiles: (incoming) =>
    set((s) => ({ files: [...s.files, ...incoming] })),

  enqueuePaths: (paths) => {
    const fresh: QueueFile[] = paths.map((path) => ({
      id: crypto.randomUUID(),
      name: path.split(/[\\/]/).pop() ?? path,
      path,
      duration: EMPTY_VALUE,
      durationSec: 0,
      sampleRate: 0,
      channels: 0,
      fps: EMPTY_VALUE,
      codec: EMPTY_VALUE,
      status: "queued",
      progress: 0,
      eta: EMPTY_VALUE,
      stage: 0,
    }));
    set((s) => ({ files: [...s.files, ...fresh] }));
    return fresh.map((f) => f.id);
  },

  markSending: (id, runSettings, plannedOutputPath) =>
    // The pump grabs one queued file at a time, calls enqueue() on the
    // Rust side, and immediately marks it processing locally so the pump
    // doesn't pick it up again before the worker emits `job_started`.
    set((s) => {
      const now = Date.now();
      return {
        files: s.files.map((f) =>
          f.id === id
            ? {
                ...f,
                status: "processing" as FileStatus,
                progress: 0,
                eta: EMPTY_VALUE,
                jobStartedAtMs: now,
                etaUpdatedAtMs: now,
                runTimeSec: undefined,
                plannedOutputPath,
                runSettings,
                stage: 0,
                currentStageName: undefined,
                stageStartedAtMs: undefined,
                stageDurations: undefined,
              }
            : f,
        ),
        selectedId: id,
      };
    }),

  markDispatchFailed: (id, error) =>
    set((s) => ({
      running: false,
      queuePaused: false,
      files: s.files.map((f) =>
        f.id === id
          ? {
              ...f,
              status: "error" as FileStatus,
              error,
              currentStageName: undefined,
              stageStartedAtMs: undefined,
              runTimeSec: elapsedJobSeconds(f),
              eta: "failed",
            }
          : f,
      ),
    })),

  updateFileMeta: (id, meta) =>
    set((s) => ({
      files: s.files.map((f) => (f.id === id ? { ...f, ...meta } : f)),
    })),

  removeChecked: () => {
    const count = get().checked.size;
    set((s) => {
      const ids = s.checked;
      return {
        files: s.files.filter((f) => !ids.has(f.id)),
        checked: new Set(),
      };
    });
    get().showToast(`Removed ${count} file${count === 1 ? "" : "s"}`);
  },

  clearQueue: () => {
    const count = get().files.filter((f) => f.status === "queued").length;
    set((s) => ({
      files: s.files.filter((f) => f.status !== "queued"),
      checked: new Set(),
    }));
    get().showToast(
      count > 0
        ? `Cleared ${count} queued file${count === 1 ? "" : "s"}`
        : "No queued files to clear",
    );
  },

  clearHistory: () => {
    const count = get().files.filter(
      (f) => f.status === "done" || f.status === "error",
    ).length;
    set((s) => ({
      files: s.files.filter((f) => f.status !== "done" && f.status !== "error"),
      checked: new Set(),
    }));
    get().showToast(
      count > 0
        ? `Cleared ${count} history row${count === 1 ? "" : "s"}`
        : "No history rows to clear",
    );
  },

  showToast: (msg) => {
    const id = crypto.randomUUID();
    set((s) => ({
      toasts: [...s.toasts, { id, msg }].slice(-MAX_TOASTS),
    }));
    toastTimers.set(id, setTimeout(() => hideToast(id), TOAST_MS));
  },

  handleWorkerEvent: (ev) => {
    // Event names match srtforge/cli.py worker subcommand. Stage and
    // progress events arrive from the pipeline's `RunLogger.step`
    // emitter wiring (see srtforge/logging.py:set_event_emitter).
    switch (ev.event) {
      case "worker_starting":
      case "worker_preload_skipped":
        break;
      case "worker_ready":
        get().showToast("Worker ready");
        break;
      case "worker_stopping":
        set({ running: false, queuePaused: false });
        break;
      case "terminated": {
        const code = (ev as unknown as { code?: number | null }).code;
        const debugLogPath = (ev as unknown as { debug_log_path?: string }).debug_log_path;
        const error =
          code === null || code === undefined
            ? "Worker process ended before the active job finished."
            : `Worker process exited with code ${code} before the active job finished.`;
        const hadProcessing = get().files.some((f) => f.status === "processing");
        set((s) => ({
          running: false,
          queuePaused: false,
          files: s.files.map((f) =>
            f.status === "processing"
              ? {
                  ...f,
                  status: "error" as FileStatus,
                  error,
                  currentStageName: undefined,
                  stageStartedAtMs: undefined,
                  debugLogPath: debugLogPath ?? f.debugLogPath,
                  runTimeSec: elapsedJobSeconds(f),
                  eta: "failed",
                }
              : f,
          ),
        }));
        if (hadProcessing) {
          get().showToast(error);
        }
        break;
      }
      case "worker_preload_failed": {
        const error = (ev as unknown as { error?: string }).error ?? "preload failed";
        get().showToast(`Worker preload failed: ${error}`);
        break;
      }
      case "job_started": {
        const id = (ev as unknown as { id: string }).id;
        const file = (ev as unknown as { file?: string }).file ?? "";
        const debugLogPath = (ev as unknown as { debug_log_path?: string }).debug_log_path;
        const name = file.split(/[\\/]/).pop() ?? file;
        set((s) => {
          const now = Date.now();
          const exists = s.files.some((f) => f.id === id);
          if (exists) {
            return {
              files: s.files.map((f) =>
                f.id === id
                  ? {
                      ...f,
                      status: "processing" as FileStatus,
                      progress: 0,
                      eta: EMPTY_VALUE,
                      jobStartedAtMs: now,
                      etaUpdatedAtMs: now,
                      runTimeSec: undefined,
                      stage: 0,
                      currentStageName: undefined,
                      stageStartedAtMs: undefined,
                      stageDurations: undefined,
                      debugLogPath: debugLogPath ?? f.debugLogPath,
                    }
                  : f,
              ),
              selectedId: id,
              logs: [],
            };
          }
          // Worker started a job we don't know about — synthesize a row.
          const fresh: QueueFile = {
            id,
            name,
            path: file,
            duration: EMPTY_VALUE,
            durationSec: 0,
            sampleRate: 0,
            channels: 0,
            fps: EMPTY_VALUE,
            codec: EMPTY_VALUE,
            status: "processing",
            progress: 0,
            eta: EMPTY_VALUE,
            jobStartedAtMs: now,
            etaUpdatedAtMs: now,
            stage: 0,
            debugLogPath,
          };
          return { files: [...s.files, fresh], selectedId: id, logs: [] };
        });
        break;
      }
      case "stage": {
        const id = (ev as unknown as { id: string }).id;
        const name = (ev as unknown as { stage: WorkerStage }).stage;
        const state = (ev as unknown as { state: "start" | "end" }).state;
        const msg = (ev as unknown as { msg?: string }).msg;
        const seconds = (ev as unknown as { seconds?: number }).seconds;
        const ok = (ev as unknown as { ok?: boolean }).ok;
        const runId = (ev as unknown as { run_id?: string }).run_id;
        const debugLogPath = (ev as unknown as { debug_log_path?: string }).debug_log_path;
        const idx = STAGE_INDEX[name];
        if (idx === undefined) break;
        const currentFile = get().files.find((f) => f.id === id);
        const recordableStage = shouldRecordWaveformStageDuration(name, msg);
        if (
          currentFile &&
          state === "end" &&
          ok !== false &&
          typeof seconds === "number" &&
          recordableStage
        ) {
          recordWaveformStageDuration(name, currentFile.durationSec, seconds);
        }
        const line = stageLogLine(name, state, msg, seconds, ok);
        set((s) => ({
          logs: appendLiveLog(s.logs, line),
          files: s.files.map((f) => {
            if (f.id !== id) return f;
            const stage = state === "start" ? Math.max(f.stage, idx) : f.stage;
            let stageDurations = f.stageDurations;
            if (state === "start" && recordableStage && stageDurations?.[name] !== undefined) {
              stageDurations = { ...stageDurations };
              delete stageDurations[name];
            } else if (state === "end" && recordableStage && typeof seconds === "number") {
              stageDurations = { ...(stageDurations ?? {}), [name]: seconds };
            }
            return {
              ...f,
              stage,
              currentStageName:
                state === "start" && recordableStage ? name : f.currentStageName,
              stageStartedAtMs:
                state === "start" && recordableStage ? Date.now() : f.stageStartedAtMs,
              stageDurations,
              runId: runId ?? f.runId,
              debugLogPath: debugLogPath ?? f.debugLogPath,
            };
          }),
        }));
        break;
      }
      case "progress": {
        const id = (ev as unknown as { id: string }).id;
        const fraction = (ev as unknown as { fraction?: number }).fraction;
        const progress = (ev as unknown as { progress?: number }).progress;
        const eta = (ev as unknown as { eta?: string }).eta;
        const debugLogPath = (ev as unknown as { debug_log_path?: string }).debug_log_path;
        const value = typeof fraction === "number" ? fraction : progress;
        if (typeof value !== "number") break;
        const now = Date.now();
        set((s) => ({
          files: s.files.map((f) =>
            f.id === id
              ? {
                  ...f,
                  progress: Math.max(0, Math.min(1, value)),
                  eta: eta ? normalizeEtaValue(eta) : f.eta,
                  etaUpdatedAtMs: now,
                  debugLogPath: debugLogPath ?? f.debugLogPath,
                }
              : f,
          ),
        }));
        break;
      }
      case "media_written": {
        const id = (ev as unknown as { id: string }).id;
        const kind = (ev as unknown as { kind: "embedded" | "burned" }).kind;
        const path = (ev as unknown as { path: string }).path;
        const debugLogPath = (ev as unknown as { debug_log_path?: string }).debug_log_path;
        set((s) => ({
          files: s.files.map((f) =>
            f.id === id
              ? {
                  ...f,
                  embeddedPath: kind === "embedded" ? path : f.embeddedPath,
                  burnedPath: kind === "burned" ? path : f.burnedPath,
                  debugLogPath: debugLogPath ?? f.debugLogPath,
                }
              : f,
          ),
        }));
        break;
      }
      case "asset_written": {
        const id = (ev as unknown as { id: string }).id;
        const kind = (ev as unknown as { kind?: string }).kind ?? "asset";
        const path = (ev as unknown as { path: string }).path;
        const debugLogPath = (ev as unknown as { debug_log_path?: string }).debug_log_path;
        // Tools (Normalize / BGM) reuse the QueueFile rows — surface the
        // produced path so the row can show a "Reveal output" link.
        set((s) => ({
          files: s.files.map((f) =>
            f.id === id
              ? { ...f, outputPath: path, debugLogPath: debugLogPath ?? f.debugLogPath }
              : f,
          ),
        }));
        get().showToast(`${kind}: ${path.split(/[\\/]/).pop() ?? path}`);
        break;
      }
      case "srt_written": {
        const id = (ev as unknown as { id: string }).id;
        const outputPath = (ev as unknown as { path?: string }).path;
        const runId = (ev as unknown as { run_id?: string }).run_id;
        const performanceLogPath = (ev as unknown as { performance_log_path?: string }).performance_log_path;
        const debugLogPath = (ev as unknown as { debug_log_path?: string }).debug_log_path;
        const now = Date.now();
        set((s) => ({
          files: s.files.map((f) =>
            f.id === id
              ? {
                  ...f,
                  progress: Math.max(f.progress, 0.98),
                  etaUpdatedAtMs: now,
                  outputPath: outputPath ?? f.outputPath,
                  runId: runId ?? f.runId,
                  performanceLogPath: performanceLogPath ?? f.performanceLogPath,
                  debugLogPath: debugLogPath ?? f.debugLogPath,
                }
              : f,
          ),
        }));
        break;
      }
      case "job_completed": {
        const id = (ev as unknown as { id: string }).id;
        const seconds = (ev as unknown as { seconds?: number | null }).seconds;
        const outputPath = (ev as unknown as { path?: string }).path;
        const runId = (ev as unknown as { run_id?: string }).run_id;
        const performanceLogPath = (ev as unknown as { performance_log_path?: string }).performance_log_path;
        const debugLogPath = (ev as unknown as { debug_log_path?: string }).debug_log_path;
        const now = Date.now();
        set((s) => ({
          files: s.files.map((f) => {
            if (f.id !== id) return f;
            const runTimeSec = elapsedJobSeconds(f, seconds);
            recordQueueEtaSample(f, runTimeSec, now);
            return {
              ...f,
              status: "done" as FileStatus,
              progress: 1,
              eta: DONE_VALUE,
              currentStageName: undefined,
              stageStartedAtMs: undefined,
              etaUpdatedAtMs: now,
              runTimeSec,
              outputPath: outputPath ?? f.outputPath,
              runId: runId ?? f.runId,
              performanceLogPath: performanceLogPath ?? f.performanceLogPath,
              debugLogPath: debugLogPath ?? f.debugLogPath,
            };
          }),
        }));
        break;
      }
      case "job_failed": {
        const id = (ev as unknown as { id: string }).id;
        const error = (ev as unknown as { error: string }).error;
        const runId = (ev as unknown as { run_id?: string }).run_id;
        const performanceLogPath = (ev as unknown as { performance_log_path?: string }).performance_log_path;
        const debugLogPath = (ev as unknown as { debug_log_path?: string }).debug_log_path;
        set((s) => ({
          files: s.files.map((f) =>
            f.id === id
              ? {
                  ...f,
                  status: "error",
                  error,
                  currentStageName: undefined,
                  stageStartedAtMs: undefined,
                  runTimeSec: elapsedJobSeconds(f),
                  runId: runId ?? f.runId,
                  performanceLogPath: performanceLogPath ?? f.performanceLogPath,
                  debugLogPath: debugLogPath ?? f.debugLogPath,
                }
              : f,
          ),
        }));
        get().showToast(`Job failed: ${error}`);
        break;
      }
      case "bad_json":
      case "bad_payload":
      case "unknown_action": {
        get().showToast(`Worker rejected message: ${ev.event}`);
        break;
      }
      case "log": {
        const id = (ev as unknown as { id?: string }).id;
        const debugLogPath = (ev as unknown as { debug_log_path?: string }).debug_log_path;
        const line: LogLine = {
          t: (ev as unknown as { t?: string }).t ?? "",
          lvl: (ev as unknown as { lvl?: string }).lvl ?? "info",
          msg: (ev as unknown as { msg?: string }).msg ?? "",
          source: (ev as unknown as { source?: string }).source,
        };
        set((s) => ({
          logs: appendLiveLog(s.logs, line),
          files:
            id && debugLogPath
              ? s.files.map((f) => (f.id === id ? { ...f, debugLogPath } : f))
              : s.files,
        }));
        break;
      }
    }
  },
    }),
    {
      name: "srtforge-studio:ui",
      storage: createJSONStorage(() => localStorage),
      // Persist user-tunable preferences and completed History rows, not
      // transient runtime state (active queue, selection, toast text, etc.).
      partialize: (s) => ({
        files: s.files.filter(
          (f) => f.status === "done" || f.status === "error",
        ),
        theme: s.theme,
        layout: s.layout,
        density: s.density,
        settings: s.settings,
        libraries: s.libraries,
      }),
      migrate: (persisted, version) => {
        if (typeof persisted !== "object" || persisted === null) {
          return persisted as UiState;
        }
        const state = persisted as Partial<UiState>;
        const settings = state.settings as Partial<Settings> | undefined;

        let migratedSettings: Settings | undefined = settings
          ? { ...DEFAULT_SETTINGS, ...settings }
          : undefined;
        if (migratedSettings) {
          if (
            version < 4 &&
            migratedSettings.attnLeft === 768 &&
            migratedSettings.attnRight === 768
          ) {
            migratedSettings = { ...migratedSettings, attnLeft: 1280, attnRight: 1280 };
          }
          if (
            version < 5 &&
            settings?.gpuPerformanceMode === undefined
          ) {
            migratedSettings = { ...migratedSettings, gpuPerformanceMode: true };
          }
          if (version < 6) {
            const asrModel = normalizeAsrModel(migratedSettings.asrModel);
            const engine = asrEngineForModel(asrModel);
            if (
              asrModel !== migratedSettings.asrModel ||
              engine !== migratedSettings.engine
            ) {
              migratedSettings = { ...migratedSettings, asrModel, engine };
            }
          }
          if (version < 8 && !migratedSettings.whisperComputeType) {
            migratedSettings = { ...migratedSettings, whisperComputeType: "auto" };
          }
          if (version < 9) {
            migratedSettings = {
              ...migratedSettings,
              device: "cuda",
              fp32: false,
              preferGpu: true,
              sep: "fv4",
              preferCenter: true,
              sepHz: 44100,
              allowUntaggedEnglish: false,
              fv4Cfg: "./models/voc_gabox.yaml",
              fv4Ckpt: "./models/voc_fv4.ckpt",
              engine: "whisper",
              asrModel: "large-v3-turbo",
              whisperComputeType: "int8_float16",
              language: "en",
              attnLeft: 1280,
              attnRight: 1280,
              subsamplingChunkFactor: 0,
              dumpWords: false,
              extract: "dual_mono_center",
              filterChain:
                "highpass=f=60,lowpass=f=10000,aformat=sample_fmts=flt,aresample=resampler=soxr:osf=flt:osr=16000",
              geminiEnabled: false,
              style: "netflix",
            };
          }
        }

        const migratedFiles =
          version < 7 && Array.isArray(state.files)
            ? state.files.map((file) => ({
                ...file,
                duration: normalizeEtaValue(file.duration),
                fps: normalizeEtaValue(file.fps),
                codec: normalizeEtaValue(file.codec),
                eta: normalizeEtaValue(file.eta),
              }))
            : state.files;

        if (migratedSettings !== settings || migratedFiles !== state.files) {
          return {
            ...state,
            ...(migratedSettings ? { settings: migratedSettings } : {}),
            files: migratedFiles,
          } as UiState;
        }
        return persisted as UiState;
      },
      // Bump when Settings union shapes change so old stored values that
      // would now fail the type unions (e.g. device "gpu" → "cuda",
      // style "default" → "bbc"|"custom") get discarded cleanly.
      version: 9,
    },
  ),
);
