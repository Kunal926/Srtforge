import { formatTotalDuration } from "../lib/format";

interface Props {
  runId: string;
  queueEta: {
    seconds: number;
    updatedAtMs?: number;
  };
  doneCount: number;
  totalCount: number;
  ffmpeg: string;
  model: string;
  status: "idle" | "paused" | "running";
}

export const StatusBar = ({
  runId,
  queueEta,
  doneCount,
  totalCount,
  ffmpeg,
  model,
  status,
}: Props) => {
  const queueEtaLabel = formatTotalDuration(queueEta.seconds);
  const label =
    status === "idle"
      ? "System ready"
      : status === "paused"
        ? "Queue paused"
        : "Transcribing";
  return (
    <div className="statusbar">
      <div className="group shrink">
        <span
          className={`dot ${status === "idle" ? "idle" : status === "paused" ? "warn" : ""}`}
        />
        <span>{label}</span>
        <span style={{ color: "var(--text-3)" }}>·</span>
        <span className="ellipsis" style={{ color: "var(--text-3)" }}>
          {doneCount} / {totalCount} files complete · queue ETA{" "}
          <span className="mono" style={{ color: "var(--text-2)" }}>
            {queueEtaLabel}
          </span>
        </span>
      </div>
      <div className="group">
        <span className="chip">
          run <span style={{ color: "var(--text-1)" }}>{runId}</span>
        </span>
        <span className="chip">ffmpeg {ffmpeg}</span>
        <span className="chip">{model}</span>
      </div>
    </div>
  );
};
