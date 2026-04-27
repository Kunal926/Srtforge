import { create } from "zustand";

import { STAGES } from "./lib/stages";
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
    switch (ev.event) {
      case "queued": {
        const id = (ev as { id: string }).id;
        const file = (ev as { file: string }).file;
        const meta = (ev as { meta?: Partial<QueueFile> }).meta ?? {};
        const name = file.split(/[\\/]/).pop() ?? file;
        const fresh: QueueFile = {
          id,
          name,
          path: file,
          duration: meta.duration ?? "—",
          durationSec: meta.durationSec ?? 0,
          sampleRate: meta.sampleRate ?? 48,
          channels: meta.channels ?? 2,
          fps: meta.fps ?? "—",
          codec: meta.codec ?? "—",
          status: "queued",
          progress: 0,
          eta: "—",
          stage: 0,
        };
        set((s) => ({ files: [...s.files, fresh] }));
        break;
      }
      case "started": {
        const id = (ev as { id: string }).id;
        set((s) => ({
          files: s.files.map((f) =>
            f.id === id ? { ...f, status: "processing" as FileStatus } : f,
          ),
          selectedId: id,
          running: true,
          paused: false,
        }));
        break;
      }
      case "stage": {
        const id = (ev as { id: string }).id;
        const stage = (ev as { stage: number }).stage;
        set((s) => ({
          files: s.files.map((f) => (f.id === id ? { ...f, stage } : f)),
        }));
        break;
      }
      case "progress": {
        const id = (ev as { id: string }).id;
        const progress = (ev as { progress: number }).progress;
        const eta = (ev as { eta?: string }).eta ?? "—";
        set((s) => ({
          files: s.files.map((f) =>
            f.id === id
              ? {
                  ...f,
                  progress,
                  eta,
                  stage: Math.min(STAGES.length - 1, Math.floor(progress * STAGES.length)),
                }
              : f,
          ),
        }));
        break;
      }
      case "log": {
        const line: LogLine = {
          t: (ev as { t?: string }).t ?? "",
          lvl: (ev as { lvl?: string }).lvl ?? "info",
          msg: (ev as { msg?: string }).msg ?? "",
        };
        set((s) => ({ logs: [...s.logs.slice(-499), line] }));
        break;
      }
      case "srt_written": {
        const id = (ev as { id: string }).id;
        set((s) => ({
          files: s.files.map((f) =>
            f.id === id
              ? { ...f, status: "done" as FileStatus, progress: 1, eta: "✓" }
              : f,
          ),
        }));
        break;
      }
      case "job_failed": {
        const id = (ev as { id: string }).id;
        const error = (ev as { error: string }).error;
        set((s) => ({
          files: s.files.map((f) =>
            f.id === id ? { ...f, status: "error", error } : f,
          ),
        }));
        get().showToast(`Job failed: ${error}`);
        break;
      }
      case "paused":
        set({ paused: true });
        break;
      case "resumed":
        set({ paused: false, running: true });
        break;
      case "terminated":
        set({ running: false });
        break;
    }
  },
}));
