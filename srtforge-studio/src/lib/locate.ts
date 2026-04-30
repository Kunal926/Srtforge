// Shared logic for the "open output" split menu used in Queue + History.
// Knows what to open for each menu item (SRT file / performance log /
// debug log / containing folder) based on the file's status and which
// paths are populated.

import { getLogsDir, openPath, revealInFolder } from "./tauri";
import { useUi } from "../store";
import type { QueueFile } from "../types";

export type LocateKind = "srt" | "performance-log" | "debug-log" | "folder";

export const locateFile = (file: QueueFile, kind: LocateKind) => {
  const showToast = useUi.getState().showToast;
  const fail = (e: unknown) => showToast(`Couldn't open: ${e}`);

  if (kind === "srt") {
    if (!file.outputPath) {
      showToast("No SRT yet - job hasn't completed");
      return;
    }
    openPath(file.outputPath).catch(fail);
    return;
  }

  if (kind === "folder") {
    // Prefer the SRT location if we have one (so the user lands next
    // to their newly-written subtitles); otherwise fall back to the
    // input media file.
    const target = file.outputPath ?? file.path;
    if (!target) {
      showToast("No path on this row yet");
      return;
    }
    revealInFolder(target).catch(fail);
    return;
  }

  if (kind === "performance-log") {
    if (file.performanceLogPath) {
      openPath(file.performanceLogPath).catch(fail);
      return;
    }
    getLogsDir()
      .then((dir) => openPath(dir))
      .catch(fail);
    return;
  }

  if (kind === "debug-log") {
    if (!file.debugLogPath) {
      showToast("No debug log saved for this run");
      return;
    }
    openPath(file.debugLogPath).catch(fail);
  }
};
