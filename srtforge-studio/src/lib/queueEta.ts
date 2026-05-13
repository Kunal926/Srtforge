import { useEffect, useMemo, useState } from "react";

import type { QueueFile } from "../types";
import { DONE_VALUE, EMPTY_VALUE, formatDuration, parseClockDuration } from "./format";
import {
  estimateWaveformStageSeconds,
  isWaveformStage,
  type WaveformStage,
} from "./waveformEta";

const RUN_HISTORY_KEY = "srtforge:queueEtaHistory:v1";
const MAX_RUNTIME_SAMPLES = 32;
const STAGE_ORDER: WaveformStage[] = [
  "probe",
  "extract",
  "separation",
  "preprocess",
  "asr",
  "post",
  "write",
];

interface RuntimeSample {
  path: string;
  name: string;
  durationSec: number;
  runtimeSec: number;
  factor: number;
  updatedAt: number;
}

interface RuntimeHistory {
  version: 1;
  samples: RuntimeSample[];
}

export interface QueueEtaSnapshot {
  seconds: number;
  updatedAtMs: number;
  etaById: Record<string, string>;
}

const hasLocalStorage = () =>
  typeof window !== "undefined" && typeof window.localStorage !== "undefined";

const normalizeKey = (value: string | undefined) =>
  (value ?? "").trim().replaceAll("\\", "/").toLowerCase();

const saneSample = (sample: unknown): RuntimeSample | undefined => {
  if (typeof sample !== "object" || sample === null) return undefined;
  const candidate = sample as Partial<RuntimeSample>;
  if (
    typeof candidate.durationSec !== "number" ||
    typeof candidate.runtimeSec !== "number" ||
    !Number.isFinite(candidate.durationSec) ||
    !Number.isFinite(candidate.runtimeSec) ||
    candidate.durationSec <= 0 ||
    candidate.runtimeSec <= 0
  ) {
    return undefined;
  }
  return {
    path: candidate.path ?? "",
    name: candidate.name ?? "",
    durationSec: candidate.durationSec,
    runtimeSec: candidate.runtimeSec,
    factor:
      typeof candidate.factor === "number" &&
      Number.isFinite(candidate.factor) &&
      candidate.factor > 0
        ? candidate.factor
        : candidate.runtimeSec / candidate.durationSec,
    updatedAt:
      typeof candidate.updatedAt === "number" && Number.isFinite(candidate.updatedAt)
        ? candidate.updatedAt
        : 0,
  };
};

const readRuntimeHistory = (): RuntimeHistory => {
  if (!hasLocalStorage()) return { version: 1, samples: [] };
  try {
    const raw = window.localStorage.getItem(RUN_HISTORY_KEY);
    if (!raw) return { version: 1, samples: [] };
    const parsed = JSON.parse(raw) as Partial<RuntimeHistory>;
    return {
      version: 1,
      samples: Array.isArray(parsed.samples)
        ? parsed.samples.flatMap((sample) => {
            const sane = saneSample(sample);
            return sane ? [sane] : [];
          })
        : [],
    };
  } catch {
    return { version: 1, samples: [] };
  }
};

const writeRuntimeHistory = (history: RuntimeHistory) => {
  if (!hasLocalStorage()) return;
  try {
    window.localStorage.setItem(RUN_HISTORY_KEY, JSON.stringify(history));
  } catch {
    // Storage can be disabled or full. ETA falls back to stage defaults.
  }
};

const sameFile = (
  file: Pick<QueueFile, "path" | "name" | "durationSec">,
  candidate: Pick<QueueFile, "path" | "name" | "durationSec"> | RuntimeSample,
) => {
  const path = normalizeKey(file.path);
  const candidatePath = normalizeKey(candidate.path);
  if (path && candidatePath && path === candidatePath) return true;
  const name = normalizeKey(file.name);
  const candidateName = normalizeKey(candidate.name);
  return (
    Boolean(name && candidateName && name === candidateName) &&
    Math.abs(file.durationSec - candidate.durationSec) <= 2
  );
};

export const recordQueueEtaSample = (
  file: QueueFile,
  runtimeSec: number | undefined,
  updatedAt = Date.now(),
) => {
  if (
    !runtimeSec ||
    !Number.isFinite(runtimeSec) ||
    runtimeSec <= 0 ||
    !Number.isFinite(file.durationSec) ||
    file.durationSec <= 0
  ) {
    return;
  }

  const sample: RuntimeSample = {
    path: file.path,
    name: file.name,
    durationSec: file.durationSec,
    runtimeSec,
    factor: runtimeSec / file.durationSec,
    updatedAt,
  };
  const history = readRuntimeHistory();
  const samples = [
    ...history.samples.filter((existing) => !sameFile(file, existing)),
    sample,
  ]
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, MAX_RUNTIME_SAMPLES);
  writeRuntimeHistory({ version: 1, samples });
};

const completedRuntimeForFile = (file: QueueFile, files: QueueFile[]) => {
  const match = files.find(
    (candidate) =>
      candidate.id !== file.id &&
      candidate.status === "done" &&
      typeof candidate.runTimeSec === "number" &&
      candidate.runTimeSec > 0 &&
      sameFile(file, candidate),
  );
  return match?.runTimeSec;
};

const historyRuntimeForFile = (file: QueueFile, history: RuntimeHistory) =>
  history.samples.find((sample) => sameFile(file, sample))?.runtimeSec;

const learnedRuntimeFactor = (history: RuntimeHistory) => {
  const samples = history.samples
    .filter((sample) => sample.factor > 0 && Number.isFinite(sample.factor))
    .slice(0, 12);
  if (samples.length === 0) return null;
  return samples.reduce((sum, sample) => sum + sample.factor, 0) / samples.length;
};

const summedStageDefaults = (file: QueueFile) =>
  STAGE_ORDER.reduce(
    (sum, stage) => sum + estimateWaveformStageSeconds(stage, file.durationSec),
    0,
  );

const totalEstimateSeconds = (
  file: QueueFile,
  files: QueueFile[],
  history: RuntimeHistory,
) => {
  const sameFileRuntime =
    completedRuntimeForFile(file, files) ?? historyRuntimeForFile(file, history);
  if (sameFileRuntime) return sameFileRuntime;
  const factor = learnedRuntimeFactor(history);
  if (factor && Number.isFinite(file.durationSec) && file.durationSec > 0) {
    return Math.max(1, file.durationSec * factor);
  }
  return summedStageDefaults(file);
};

const stageRemainingSeconds = (file: QueueFile, nowMs: number) => {
  if (!isWaveformStage(file.currentStageName)) return null;
  const stageIndex = STAGE_ORDER.indexOf(file.currentStageName);
  if (stageIndex < 0) return null;
  const elapsedCurrent =
    typeof file.stageStartedAtMs === "number"
      ? Math.max(0, (nowMs - file.stageStartedAtMs) / 1000)
      : 0;
  const currentEstimate = estimateWaveformStageSeconds(
    file.currentStageName,
    file.durationSec,
  );
  const currentRemaining = Math.max(0, currentEstimate - elapsedCurrent);
  const futureRemaining = STAGE_ORDER.slice(stageIndex + 1).reduce(
    (sum, stage) => sum + estimateWaveformStageSeconds(stage, file.durationSec),
    0,
  );
  return currentRemaining + futureRemaining;
};

const activeRemainingSeconds = (
  file: QueueFile,
  files: QueueFile[],
  history: RuntimeHistory,
  nowMs: number,
) => {
  const fromStage = stageRemainingSeconds(file, nowMs);
  if (fromStage !== null) return fromStage;

  const elapsed =
    typeof file.jobStartedAtMs === "number"
      ? Math.max(0, (nowMs - file.jobStartedAtMs) / 1000)
      : 0;
  const total = totalEstimateSeconds(file, files, history);
  const fromTotal = Math.max(0, total - elapsed);
  if (fromTotal > 0) return fromTotal;
  return parseClockDuration(file.eta) ?? 0;
};

const formatEta = (seconds: number | null) =>
  seconds === null || !Number.isFinite(seconds)
    ? EMPTY_VALUE
    : formatDuration(Math.max(1, seconds));

export const useQueueEtaSnapshot = (files: QueueFile[]): QueueEtaSnapshot => {
  const [nowMs, setNowMs] = useState(() => Date.now());
  const hasActiveEta = files.some(
    (file) => file.status === "queued" || file.status === "processing",
  );

  useEffect(() => {
    if (!hasActiveEta) return;
    const tick = () => {
      if (document.visibilityState === "visible") {
        setNowMs(Date.now());
      }
    };
    tick();
    const interval = window.setInterval(tick, 1000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [hasActiveEta]);

  return useMemo(() => {
    const history = readRuntimeHistory();
    let seconds = 0;
    const etaById: Record<string, string> = {};
    for (const file of files) {
      if (file.status === "done") {
        etaById[file.id] = DONE_VALUE;
        continue;
      }
      if (file.status === "error") {
        etaById[file.id] = "failed";
        continue;
      }
      const remaining =
        file.status === "processing"
          ? activeRemainingSeconds(file, files, history, nowMs)
          : totalEstimateSeconds(file, files, history);
      seconds += Math.max(0, remaining);
      etaById[file.id] = formatEta(remaining);
    }
    return { seconds, updatedAtMs: nowMs, etaById };
  }, [files, nowMs]);
};
