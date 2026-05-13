import type { WorkerStage } from "../types";

export type WaveformStage =
  | "probe"
  | "extract"
  | "separation"
  | "preprocess"
  | "asr"
  | "post"
  | "write";

export interface StageBand {
  start: number;
  end: number;
}

interface StageTimingDefault {
  factor: number;
  fallbackSeconds: number;
  minSeconds: number;
  maxSeconds: number;
}

interface StageEtaEntry {
  factor: number;
  samples: number;
  updatedAt: number;
}

interface EtaHistoryFile {
  version: 3;
  stages: Partial<Record<WaveformStage, StageEtaEntry>>;
}

const STORAGE_KEY = "srtforge:waveformEtaHistory:v3";
const EMA_ALPHA = 0.22;
const MAX_SAMPLES = 32;

export const WAVEFORM_STAGE_BANDS: Record<WaveformStage, StageBand> = {
  probe: { start: 0.0, end: 0.012 },
  extract: { start: 0.012, end: 0.03 },
  separation: { start: 0.03, end: 0.535 },
  preprocess: { start: 0.535, end: 0.545 },
  asr: { start: 0.545, end: 0.94 },
  post: { start: 0.94, end: 0.985 },
  write: { start: 0.985, end: 1.0 },
};

const TIMING_DEFAULTS: Record<WaveformStage, StageTimingDefault> = {
  probe: { factor: 0.0002, fallbackSeconds: 1.0, minSeconds: 0.5, maxSeconds: 5 },
  extract: { factor: 0.0018, fallbackSeconds: 3.0, minSeconds: 1.0, maxSeconds: 12 },
  separation: { factor: 0.074, fallbackSeconds: 105.0, minSeconds: 10.0, maxSeconds: 360 },
  preprocess: { factor: 0.001, fallbackSeconds: 2.0, minSeconds: 0.8, maxSeconds: 12 },
  asr: { factor: 0.074, fallbackSeconds: 105.0, minSeconds: 20.0, maxSeconds: 480 },
  post: { factor: 0.0095, fallbackSeconds: 14.0, minSeconds: 3.0, maxSeconds: 90 },
  write: { factor: 0.001, fallbackSeconds: 2.0, minSeconds: 0.8, maxSeconds: 15 },
};

const RECORDABLE_STAGE_LABELS: Record<WaveformStage, string> = {
  probe: "Probe audio streams",
  extract: "Extract English audio",
  separation: "Vocal separation",
  preprocess: "FFmpeg preprocessing",
  asr: "ASR pipeline",
  post: "Post-processing",
  write: "Write SRT",
};

const clamp = (value: number, min: number, max: number) =>
  Math.max(min, Math.min(max, value));

const hasLocalStorage = () =>
  typeof window !== "undefined" && typeof window.localStorage !== "undefined";

export const isWaveformStage = (stage: WorkerStage | undefined): stage is WaveformStage =>
  Boolean(stage && stage in WAVEFORM_STAGE_BANDS);

export const shouldRecordWaveformStageDuration = (
  stage: WorkerStage | undefined,
  msg: string | undefined,
): stage is WaveformStage =>
  isWaveformStage(stage) && msg === RECORDABLE_STAGE_LABELS[stage];

export const getWaveformStageBand = (stage: WaveformStage): StageBand =>
  WAVEFORM_STAGE_BANDS[stage];

const saneEntry = (entry: unknown): StageEtaEntry | undefined => {
  if (typeof entry !== "object" || entry === null) return undefined;
  const candidate = entry as Partial<StageEtaEntry>;
  if (
    typeof candidate.factor !== "number" ||
    !Number.isFinite(candidate.factor) ||
    candidate.factor <= 0
  ) {
    return undefined;
  }
  return {
    factor: candidate.factor,
    samples:
      typeof candidate.samples === "number" && Number.isFinite(candidate.samples)
        ? clamp(Math.round(candidate.samples), 0, MAX_SAMPLES)
        : 0,
    updatedAt:
      typeof candidate.updatedAt === "number" && Number.isFinite(candidate.updatedAt)
        ? candidate.updatedAt
        : 0,
  };
};

const readHistory = (): EtaHistoryFile => {
  if (!hasLocalStorage()) {
    return { version: 3, stages: {} };
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { version: 3, stages: {} };
    const parsed = JSON.parse(raw) as Partial<EtaHistoryFile>;
    const stages: Partial<Record<WaveformStage, StageEtaEntry>> = {};
    for (const stage of Object.keys(WAVEFORM_STAGE_BANDS) as WaveformStage[]) {
      const entry = saneEntry(parsed.stages?.[stage]);
      if (entry) stages[stage] = entry;
    }
    return { version: 3, stages };
  } catch {
    return { version: 3, stages: {} };
  }
};

const writeHistory = (history: EtaHistoryFile) => {
  if (!hasLocalStorage()) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(history));
  } catch {
    // Storage can be disabled or full. The waveform falls back to defaults.
  }
};

export const estimateWaveformStageSeconds = (
  stage: WaveformStage,
  mediaDurationSec: number,
): number => {
  const defaults = TIMING_DEFAULTS[stage];
  const duration = Number.isFinite(mediaDurationSec) && mediaDurationSec > 0 ? mediaDurationSec : 0;
  const history = readHistory();
  const factor = saneEntry(history.stages[stage])?.factor ?? defaults.factor;
  const estimate = duration > 0 ? duration * factor : defaults.fallbackSeconds;
  return clamp(estimate, defaults.minSeconds, defaults.maxSeconds);
};

export const recordWaveformStageDuration = (
  stage: WorkerStage,
  mediaDurationSec: number,
  seconds: number,
  updatedAt = Date.now(),
) => {
  if (!isWaveformStage(stage)) return;
  if (
    !Number.isFinite(mediaDurationSec) ||
    mediaDurationSec <= 0 ||
    !Number.isFinite(seconds) ||
    seconds <= 0
  ) {
    return;
  }

  const defaults = TIMING_DEFAULTS[stage];
  const observedFactor = clamp(
    seconds / mediaDurationSec,
    defaults.minSeconds / mediaDurationSec,
    defaults.maxSeconds / mediaDurationSec,
  );
  const history = readHistory();
  const previous = saneEntry(history.stages[stage]);
  const factor = previous
    ? previous.factor * (1 - EMA_ALPHA) + observedFactor * EMA_ALPHA
    : observedFactor;

  history.stages[stage] = {
    factor,
    samples: Math.min(MAX_SAMPLES, (previous?.samples ?? 0) + 1),
    updatedAt,
  };
  writeHistory(history);
};
