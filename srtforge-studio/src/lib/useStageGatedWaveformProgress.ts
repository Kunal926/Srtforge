import { useEffect, useMemo, useRef, useState } from "react";

import type { QueueFile } from "../types";
import {
  estimateWaveformStageSeconds,
  getWaveformStageBand,
  isWaveformStage,
} from "./waveformEta";

const TICK_MS = 250;
const STAGE_HOLD_FRACTION = 0.98;
const MIN_ADVANCE_PER_TICK = 0.0015;
const MAX_ADVANCE_PER_TICK = 0.012;
const EASE_FRACTION = 0.28;

const clamp01 = (value: number) => Math.max(0, Math.min(1, value));

const hasCompletedStage = (file: QueueFile, stage: string) =>
  typeof file.stageDurations?.[stage] === "number";

const getTargetProgress = (
  file: QueueFile,
  active: boolean,
  expectedStageSeconds: number,
  nowMs: number,
) => {
  const workerProgress = clamp01(file.progress);
  if (!active || file.status !== "processing") return workerProgress;
  if (!isWaveformStage(file.currentStageName)) return workerProgress;

  const stage = file.currentStageName;
  const band = getWaveformStageBand(stage);
  const stageEnd = band.end;
  const stageWidth = stageEnd - band.start;
  const stageComplete = hasCompletedStage(file, stage);

  if (stageComplete) {
    return Math.max(workerProgress, stageEnd);
  }

  const cap = band.start + stageWidth * STAGE_HOLD_FRACTION;
  const startedAt = file.stageStartedAtMs;
  const elapsedSeconds =
    typeof startedAt === "number" && Number.isFinite(startedAt)
      ? Math.max(0, (nowMs - startedAt) / 1000)
      : 0;
  const predictedLocal =
    expectedStageSeconds > 0 ? clamp01(elapsedSeconds / expectedStageSeconds) : 0;
  const predicted = band.start + stageWidth * Math.min(predictedLocal, STAGE_HOLD_FRACTION);

  return Math.max(band.start, Math.min(workerProgress, cap), predicted);
};

const easeForward = (previous: number, target: number) => {
  if (target <= previous) return previous;
  const delta = target - previous;
  const step = Math.min(
    delta,
    Math.max(MIN_ADVANCE_PER_TICK, Math.min(MAX_ADVANCE_PER_TICK, delta * EASE_FRACTION)),
  );
  return previous + step;
};

const useReducedMotion = () => {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return reduced;
};

const useDocumentVisible = () => {
  const [visible, setVisible] = useState(() =>
    typeof document === "undefined" ? true : document.visibilityState === "visible",
  );

  useEffect(() => {
    if (typeof document === "undefined") return;
    const update = () => setVisible(document.visibilityState === "visible");
    update();
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);

  return visible;
};

export const useStageGatedWaveformProgress = (
  file: QueueFile,
  active: boolean,
  paused: boolean,
) => {
  const reducedMotion = useReducedMotion();
  const documentVisible = useDocumentVisible();
  const fileId = file.id;
  const stageName = isWaveformStage(file.currentStageName) ? file.currentStageName : undefined;
  const expectedStageSeconds = useMemo(
    () =>
      stageName
        ? estimateWaveformStageSeconds(stageName, file.durationSec)
        : 0,
    [file.durationSec, fileId, stageName],
  );
  const [visualProgress, setVisualProgress] = useState(() =>
    getTargetProgress(file, active && !paused, expectedStageSeconds, Date.now()),
  );
  const lastFileId = useRef(fileId);
  const targetRef = useRef(visualProgress);

  useEffect(() => {
    const target = getTargetProgress(file, active && !paused, expectedStageSeconds, Date.now());
    if (lastFileId.current !== fileId) {
      lastFileId.current = fileId;
      targetRef.current = target;
      setVisualProgress(target);
      return;
    }
    targetRef.current = Math.max(targetRef.current, target);
    if (!active || paused || reducedMotion || !documentVisible || file.status !== "processing") {
      setVisualProgress((previous) => Math.max(previous, targetRef.current));
    }
  }, [
    active,
    documentVisible,
    expectedStageSeconds,
    file,
    fileId,
    file.currentStageName,
    file.progress,
    file.stageDurations,
    file.stageStartedAtMs,
    file.status,
    paused,
    reducedMotion,
  ]);

  useEffect(() => {
    if (!active || paused || reducedMotion || !documentVisible || file.status !== "processing") return;

    const tick = () => {
      const target = getTargetProgress(file, true, expectedStageSeconds, Date.now());
      targetRef.current = Math.max(targetRef.current, target);
      setVisualProgress((previous) => {
        const next = easeForward(previous, targetRef.current);
        return Math.abs(next - previous) < 0.001 ? previous : next;
      });
    };

    tick();
    const interval = window.setInterval(tick, TICK_MS);
    return () => window.clearInterval(interval);
  }, [active, documentVisible, expectedStageSeconds, file, paused, reducedMotion]);

  return clamp01(visualProgress);
};
