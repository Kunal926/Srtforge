// Shared logic for the "open output" split menu used in Queue + History.
// Knows what to open for each menu item (SRT file / run log / containing
// folder) based on the file's status and which paths are populated.

import { getLogsDir, openPath, revealInFolder } from "./tauri";
import { useUi } from "../store";
import type { QueueFile } from "../types";

export type LocateKind = "srt" | "log" | "folder";

export const locateFile = (file: QueueFile, kind: LocateKind) => {
  const showToast = useUi.getState().showToast;
  const fail = (e: unknown) => showToast(`Couldn't open: ${e}`);

  if (kind === "srt") {
    if (!file.outputPath) {
      showToast("No SRT yet — job hasn't completed");
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

  // "log" — the worker doesn't yet emit a run-id JSON event we can latch
  // onto, so we open the project's logs/ directory and let the user pick
  // the right run file. Resolved server-side because the React layer
  // doesn't know the absolute project root.
  getLogsDir()
    .then((dir) => openPath(dir))
    .catch(fail);
};
