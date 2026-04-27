// Thin wrapper around Tauri's invoke + event APIs.
// Centralises imports so components don't reach into @tauri-apps/* directly.

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { open as openDialog } from "@tauri-apps/plugin-dialog";

import type { Settings, WorkerEvent } from "../types";

export const enqueue = (file: string, config: Partial<Settings>): Promise<string> =>
  invoke<string>("enqueue", { file, config });

export const pauseQueue = (): Promise<void> => invoke("pause_queue");
export const resumeQueue = (): Promise<void> => invoke("resume_queue");
export const cancelJob = (id: string): Promise<void> => invoke("cancel_job", { id });
export const restartWorker = (): Promise<void> => invoke("restart_worker");

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
