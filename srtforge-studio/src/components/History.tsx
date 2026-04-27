import { I } from "../icons";
import { locateFile } from "../lib/locate";
import { useUi } from "../store";
import type { QueueFile } from "../types";
import { OutputSplitButton } from "./Queue";
import { EmptyState } from "./EmptyState";

interface Props {
  files: QueueFile[];
}

const totalDurationLabel = (files: QueueFile[]) => {
  const sec = files.reduce((s, f) => s + f.durationSec, 0);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
};

const fileExt = (n: string) => (n.split(".").pop() ?? "").toUpperCase();

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
        <div className="stat">
          <span className="lbl">Completed</span>
          <span className="num">{completed.length}</span>
        </div>
        <div className="stat">
          <span className="lbl">Failed</span>
          <span
            className="num"
            style={{ color: failed.length ? "var(--danger)" : undefined }}
          >
            {failed.length}
          </span>
        </div>
        <div className="stat">
          <span className="lbl">Total duration</span>
          <span className="num mono">{totalDurationLabel(files)}</span>
        </div>
        <div className="stat">
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

      <div className="hist-list">
        <div className="hist-head">
          <span></span>
          <span>File</span>
          <span>Result</span>
          <span>Duration</span>
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
                <span className="pill">{f.duration}</span>
                <span className="pill">{f.sampleRate} kHz</span>
                <span className="pill">{f.channels} ch</span>
                <span className="pill">{f.codec}</span>
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
