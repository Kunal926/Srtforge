// Thin wrapper around Tauri's invoke + event APIs.
// Centralises imports so components don't reach into @tauri-apps/* directly.

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { open as openDialog } from "@tauri-apps/plugin-dialog";

import type { WorkerEvent } from "../types";

export const enqueue = (
  file: string,
  config: Record<string, unknown>,
  options?: { id?: string; output?: string },
): Promise<string> =>
  invoke<string>("enqueue", {
    file,
    id: options?.id,
    output: options?.output,
    config,
  });

/** Standalone audio normalize/transcode (Normalize tool). The worker
 *  emits the same `job_started` / `asset_written` / `job_completed`
 *  vocabulary as transcribe so existing handlers light up the row. */
export const normalize = (
  file: string,
  config: Record<string, unknown>,
  options?: { id?: string },
): Promise<string> =>
  invoke<string>("normalize", { file, id: options?.id, config });

/** Standalone vocal/instrumental separation (BGM tool). */
export const separate = (
  file: string,
  config: Record<string, unknown>,
  options?: { id?: string },
): Promise<string> =>
  invoke<string>("separate", { file, id: options?.id, config });

export const shutdownWorker = (): Promise<void> => invoke("shutdown_worker");
export const restartWorker = (): Promise<void> => invoke("restart_worker");
/** Tells the running worker to call torch.cuda.empty_cache(). Wired to
 *  the "Free GPU memory when stopping" toggle. Cheap no-op when CUDA
 *  isn't loaded. */
export const clearGpuCache = (): Promise<void> => invoke("clear_gpu_cache");

export const openPath = (path: string): Promise<void> =>
  invoke("open_path", { path });

export const revealInFolder = (path: string): Promise<void> =>
  invoke("reveal_in_folder", { path });

export interface ProbeResult {
  duration_sec: number;
  sample_rate: number;
  channels: number;
  codec: string;
  fps: string;
}

export const probeFile = (path: string): Promise<ProbeResult> =>
  invoke<ProbeResult>("probe_file", { path });

export const getLogsDir = (): Promise<string> => invoke<string>("get_logs_dir");

export const onWorkerEvent = (
  handler: (ev: WorkerEvent) => void,
): Promise<UnlistenFn> =>
  listen<WorkerEvent>("worker:event", (e) => handler(e.payload));

export const pickFiles = async (): Promise<string[]> => {
  const result = await openDialog({
    multiple: true,
    filters: [
      {
        name: "Media",
        extensions: ["mkv", "mp4", "mov", "wav", "flac", "m4a", "webm", "aac"],
      },
    ],
  });
  if (!result) return [];
  return Array.isArray(result) ? result : [result];
};

export const pickFolder = async (): Promise<string | null> => {
  const result = await openDialog({ directory: true, multiple: false });
  return typeof result === "string" ? result : null;
};
