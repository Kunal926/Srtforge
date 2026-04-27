import { create } from "zustand";

import type {
  Density,
  FileStatus,
  Layout,
  LogLine,
  QueueFile,
  Settings,
  Tab,
  Theme,
  WorkerEvent,
} from "./types";

const DEFAULT_SETTINGS: Settings = {
  device: "auto",
  gpuPct: 100,
  fp32: false,
  preferGpu: true,
  sep: "fv4",
  preferCenter: true,
  sepHz: 44100,
  allowUntaggedEnglish: false,
  fv4Cfg: "./models/voc_gabox.yaml",
  fv4Ckpt: "./models/voc_fv4.ckpt",
  engine: "parakeet",
  asrModel: "nvidia/parakeet-tdt-0.6b-v2",
  language: "en",
  attnLeft: 768,
  attnRight: 768,
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
  sidecarSrt: false,
  dumpWords: false,
  freeGpuOnStop: true,
  extract: "center",
  filterChain:
    "highpass=f=60,lowpass=f=10000,aformat=sample_fmts=flt,aresample=resampler=soxr:osf=flt:osr=16000",
  geminiEnabled: false,
  geminiModel: "gemini-3-flash-preview",
  geminiKey: "",
  sonarr: true,
};

interface UiState {
  files: QueueFile[];
  selectedId: string | null;
  checked: Set<string>;
  active: Tab;
  running: boolean;
  paused: boolean;
  settingsOpen: boolean;
  logs: LogLine[];
  search: string;
  toast: string | null;

  theme: Theme;
  layout: Layout;
  density: Density;

  settings: Settings;

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
  setPaused: (p: boolean) => void;

  addFiles: (files: QueueFile[]) => void;
  /** Add raw file paths picked from disk; the pump turns them into worker jobs. */
  enqueuePaths: (paths: string[]) => void;
  /** Promote one queued file to "sent" before invoking enqueue() on the Rust side. */
  markSending: (id: string) => void;
  removeChecked: () => void;
  clearQueue: () => void;
  showToast: (msg: string) => void;

  handleWorkerEvent: (ev: WorkerEvent) => void;
}

let toastTimer: ReturnType<typeof setTimeout> | null = null;

export const useUi = create<UiState>((set, get) => ({
  files: [],
  selectedId: null,
  checked: new Set(),
  active: "queue",
  running: false,
  paused: false,
  settingsOpen: false,
  logs: [],
  search: "",
  toast: null,

  theme: "dark",
  layout: "hybrid",
  density: "comfortable",

  settings: DEFAULT_SETTINGS,

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
  setPaused: (p) => set({ paused: p }),

  addFiles: (incoming) =>
    set((s) => ({ files: [...s.files, ...incoming] })),

  enqueuePaths: (paths) =>
    set((s) => {
      const fresh: QueueFile[] = paths.map((path) => ({
        id: crypto.randomUUID(),
        name: path.split(/[\\/]/).pop() ?? path,
        path,
        duration: "—",
        durationSec: 0,
        sampleRate: 48,
        channels: 2,
        fps: "—",
        codec: "—",
        status: "queued",
        progress: 0,
        eta: "—",
        stage: 0,
      }));
      return { files: [...s.files, ...fresh] };
    }),

  markSending: (id) =>
    // The pump grabs one queued file at a time, calls enqueue() on the
    // Rust side, and immediately marks it processing locally so the pump
    // doesn't pick it up again before the worker emits `job_started`.
    set((s) => ({
      files: s.files.map((f) =>
        f.id === id ? { ...f, status: "processing" as FileStatus } : f,
      ),
      selectedId: id,
    })),

  removeChecked: () =>
    set((s) => {
      const ids = s.checked;
      return {
        files: s.files.filter((f) => !ids.has(f.id)),
        checked: new Set(),
        toast: `Removed ${ids.size} file${ids.size === 1 ? "" : "s"}`,
      };
    }),

  clearQueue: () =>
    set((s) => ({
      files: s.files.filter(
        (f) => f.status === "done" || f.status === "error",
      ),
      checked: new Set(),
      toast: "Queue cleared",
    })),

  showToast: (msg) => {
    set({ toast: msg });
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => set({ toast: null }), 1800);
  },

  handleWorkerEvent: (ev) => {
    // Event names match srtforge/cli.py worker subcommand. The worker
    // doesn't emit progress/stage/log streams today, so the UI stays
    // pinned at "processing" between job_started and srt_written/
    // job_completed. Adding granular events is a Python-side TODO.
    switch (ev.event) {
      case "worker_starting":
      case "worker_preload_skipped":
        set({ running: false });
        break;
      case "worker_ready":
        set({ running: true });
        get().showToast("Worker ready");
        break;
      case "worker_stopping":
      case "terminated":
        set({ running: false });
        break;
      case "worker_preload_failed": {
        const error = (ev as unknown as { error?: string }).error ?? "preload failed";
        get().showToast(`Worker preload failed: ${error}`);
        break;
      }
      case "job_started": {
        const id = (ev as unknown as { id: string }).id;
        const file = (ev as unknown as { file?: string }).file ?? "";
        const name = file.split(/[\\/]/).pop() ?? file;
        set((s) => {
          const exists = s.files.some((f) => f.id === id);
          if (exists) {
            return {
              files: s.files.map((f) =>
                f.id === id ? { ...f, status: "processing" as FileStatus } : f,
              ),
              selectedId: id,
            };
          }
          // Worker started a job we don't know about — synthesize a row.
          const fresh: QueueFile = {
            id,
            name,
            path: file,
            duration: "—",
            durationSec: 0,
            sampleRate: 48,
            channels: 2,
            fps: "—",
            codec: "—",
            status: "processing",
            progress: 0,
            eta: "—",
            stage: 0,
          };
          return { files: [...s.files, fresh], selectedId: id };
        });
        break;
      }
      case "srt_written":
      case "job_completed": {
        const id = (ev as unknown as { id: string }).id;
        const outputPath = (ev as unknown as { path?: string }).path;
        set((s) => ({
          files: s.files.map((f) =>
            f.id === id
              ? {
                  ...f,
                  status: "done" as FileStatus,
                  progress: 1,
                  eta: "✓",
                  outputPath: outputPath ?? f.outputPath,
                }
              : f,
          ),
        }));
        break;
      }
      case "job_failed": {
        const id = (ev as unknown as { id: string }).id;
        const error = (ev as unknown as { error: string }).error;
        set((s) => ({
          files: s.files.map((f) =>
            f.id === id ? { ...f, status: "error", error } : f,
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
        const line: LogLine = {
          t: (ev as unknown as { t?: string }).t ?? "",
          lvl: (ev as unknown as { lvl?: string }).lvl ?? "info",
          msg: (ev as unknown as { msg?: string }).msg ?? "",
        };
        set((s) => ({ logs: [...s.logs.slice(-499), line] }));
        break;
      }
    }
  },
}));
