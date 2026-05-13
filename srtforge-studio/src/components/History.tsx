import { I } from "../icons";
import { formatDuration, formatTotalDuration } from "../lib/format";
import { locateFile } from "../lib/locate";
import { useUi } from "../store";
import type { QueueFile } from "../types";
import { OutputSplitButton } from "./Queue";
import { EmptyState } from "./EmptyState";

interface Props {
  files: QueueFile[];
}

const fileExt = (n: string) => (n.split(".").pop() ?? "").toUpperCase();

const historyRuntimeSeconds = (file: QueueFile) => {
  if (typeof file.runTimeSec === "number" && Number.isFinite(file.runTimeSec) && file.runTimeSec > 0) {
    return file.runTimeSec;
  }
  const durations = Object.values(file.stageDurations ?? {}).filter(
    (seconds) => Number.isFinite(seconds) && seconds > 0,
  );
  if (!durations.length) return null;
  return durations.reduce((sum, seconds) => sum + seconds, 0);
};

export const HistoryView = ({ files }: Props) => {
  if (!files.length) {
    return (
      <EmptyState
        icon={<I.Done size={32} />}
        title="No completed runs yet"
        body="Files you transcribe will appear here with their output paths."
      />
    );
  }

  const completed = files.filter((f) => f.status === "done");
  const failed = files.filter((f) => f.status === "error");

  // For "Output" stat tile: show the parent dir of the most recent
  // output if we have one, otherwise the configured output dir.
  const outputDir = useUi.getState().settings.outputDir;
  const recentOutput = [...files].reverse().find((f) => f.outputPath)?.outputPath;
  const outputLabel = recentOutput
    ? recentOutput.replace(/[\\/][^\\/]+$/, "")
    : outputDir;

  return (
    <div className="history">
      <div className="hist-stats">
        <div className="stat completed">
          <div className="hist-stat-icon">
            <I.Check size={15} />
          </div>
          <div className="hist-stat-body">
            <span className="lbl">Completed</span>
            <span className="num">{completed.length}</span>
          </div>
        </div>
        <div className={`stat failed${failed.length ? " has-failures" : ""}`}>
          <div className="hist-stat-icon">
            <I.X size={15} />
          </div>
          <div className="hist-stat-body">
            <span className="lbl">Failed</span>
            <span
              className="num"
              style={{ color: failed.length ? "var(--danger)" : undefined }}
            >
              {failed.length}
            </span>
          </div>
        </div>
        <div className="stat duration">
          <div className="hist-stat-icon">
            <I.Clock size={15} />
          </div>
          <div className="hist-stat-body">
            <span className="lbl">Total duration</span>
            <span className="num mono">
              {formatTotalDuration(files.reduce((s, f) => s + f.durationSec, 0))}
            </span>
          </div>
        </div>
        <div className="stat output">
          <div className="hist-stat-icon">
            <I.Folder size={15} />
          </div>
          <div className="hist-stat-body">
            <span className="lbl">Output</span>
            <span
              className="num mono"
              style={{ fontSize: 13, wordBreak: "break-all" }}
              title={outputLabel}
            >
              {outputLabel}
            </span>
          </div>
        </div>
      </div>

      <div className="hist-list">
        <div className="hist-head">
          <span></span>
          <span>File</span>
          <span>Result</span>
          <span>Duration</span>
          <span>Time taken</span>
          <span>Output</span>
          <span></span>
        </div>
        {files.map((f) => (
          <div key={f.id} className="hist-row">
            <div className={`hist-glyph ${f.status}`}>
              {f.status === "done" ? (
                <I.Check size={12} sw={2.6} />
              ) : (
                <I.X size={12} sw={2.6} />
              )}
            </div>
            <div className="hist-name">
              <div className="filename">{f.name}</div>
              <div className="meta">
                {f.duration !== "—" && (
                  <span className="pill">{f.duration}</span>
                )}
                {f.sampleRate > 0 && (
                  <span className="pill">{f.sampleRate} kHz</span>
                )}
                {f.channels > 0 && (
                  <span className="pill">{f.channels} ch</span>
                )}
                {f.codec !== "—" && <span className="pill">{f.codec}</span>}
                {fileExt(f.name) && (
                  <span className="pill">{fileExt(f.name)}</span>
                )}
                {f.error && <span className="pill err">{f.error}</span>}
              </div>
            </div>
            <div className={`hist-result ${f.status}`}>
              {f.status === "done" ? "Completed" : "Failed"}
            </div>
            <div className="mono dim">{f.duration}</div>
            <div className="mono dim">{formatDuration(historyRuntimeSeconds(f) ?? 0)}</div>
            <div
              className="mono dim ellipsis"
              title={f.outputPath ?? "—"}
            >
              {f.outputPath ?? "—"}
            </div>
            <OutputSplitButton onLocate={(k) => locateFile(f, k)} />
          </div>
        ))}
      </div>
    </div>
  );
};
