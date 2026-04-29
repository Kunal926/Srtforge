import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

import type {
  Density,
  FileStatus,
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
  extract: "dual_mono_center",
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
  setPaused: (p: boolean) => void;
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
  markSending: (id: string) => void;
  /** Patch one row's metadata (post-probe). */
  updateFileMeta: (id: string, meta: Partial<QueueFile>) => void;
  removeChecked: () => void;
  clearQueue: () => void;
  showToast: (msg: string) => void;

  handleWorkerEvent: (ev: WorkerEvent) => void;
}

let toastTimer: ReturnType<typeof setTimeout> | null = null;

export const useUi = create<UiState>()(
  persist(
    (set, get) => ({
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
  setPaused: (p) => set({ paused: p }),
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
      duration: "—",
      durationSec: 0,
      sampleRate: 0,
      channels: 0,
      fps: "—",
      codec: "—",
      status: "queued",
      progress: 0,
      eta: "—",
      stage: 0,
    }));
    set((s) => ({ files: [...s.files, ...fresh] }));
    return fresh.map((f) => f.id);
  },

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

  updateFileMeta: (id, meta) =>
    set((s) => ({
      files: s.files.map((f) => (f.id === id ? { ...f, ...meta } : f)),
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
    // Event names match srtforge/cli.py worker subcommand. Stage and
    // progress events arrive from the pipeline's `RunLogger.step`
    // emitter wiring (see srtforge/logging.py:set_event_emitter).
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
            sampleRate: 0,
            channels: 0,
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
      case "stage": {
        const id = (ev as unknown as { id: string }).id;
        const name = (ev as unknown as { stage: WorkerStage }).stage;
        const state = (ev as unknown as { state: "start" | "end" }).state;
        const seconds = (ev as unknown as { seconds?: number }).seconds;
        const idx = STAGE_INDEX[name];
        if (idx === undefined) break;
        set((s) => ({
          files: s.files.map((f) => {
            if (f.id !== id) return f;
            const stage = state === "start" ? Math.max(f.stage, idx) : f.stage;
            const stageDurations =
              state === "end" && typeof seconds === "number"
                ? { ...(f.stageDurations ?? {}), [name]: seconds }
                : f.stageDurations;
            return { ...f, stage, stageDurations };
          }),
        }));
        break;
      }
      case "progress": {
        const id = (ev as unknown as { id: string }).id;
        const fraction = (ev as unknown as { fraction?: number }).fraction;
        const progress = (ev as unknown as { progress?: number }).progress;
        const eta = (ev as unknown as { eta?: string }).eta;
        const value = typeof fraction === "number" ? fraction : progress;
        if (typeof value !== "number") break;
        set((s) => ({
          files: s.files.map((f) =>
            f.id === id
              ? {
                  ...f,
                  progress: Math.max(0, Math.min(1, value)),
                  eta: eta ?? f.eta,
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
        set((s) => ({
          files: s.files.map((f) =>
            f.id === id
              ? {
                  ...f,
                  embeddedPath: kind === "embedded" ? path : f.embeddedPath,
                  burnedPath: kind === "burned" ? path : f.burnedPath,
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
        // Tools (Normalize / BGM) reuse the QueueFile rows — surface the
        // produced path so the row can show a "Reveal output" link.
        set((s) => ({
          files: s.files.map((f) =>
            f.id === id ? { ...f, outputPath: path } : f,
          ),
        }));
        get().showToast(`${kind}: ${path.split(/[\\/]/).pop() ?? path}`);
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
    }),
    {
      name: "srtforge-studio:ui",
      storage: createJSONStorage(() => localStorage),
      // Only persist user-tunable preferences, not transient runtime
      // state (files in queue, current selection, toast text, etc.).
      partialize: (s) => ({
        theme: s.theme,
        layout: s.layout,
        density: s.density,
        settings: s.settings,
        libraries: s.libraries,
      }),
      // Bump when Settings union shapes change so old stored values that
      // would now fail the type unions (e.g. device "gpu" → "cuda",
      // style "default" → "bbc"|"custom") get discarded cleanly.
      version: 3,
    },
  ),
);
