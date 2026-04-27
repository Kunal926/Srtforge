interface Props {
  runId: string;
  queueEta: string;
  doneCount: number;
  totalCount: number;
  ffmpeg: string;
  model: string;
  status: "idle" | "warn" | "running";
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
  const label =
    status === "idle" ? "System ready" : status === "warn" ? "Stopped" : "Transcribing";
  return (
    <div className="statusbar">
      <div className="group shrink">
        <span
          className={`dot ${status === "idle" ? "idle" : status === "warn" ? "warn" : ""}`}
        />
        <span>{label}</span>
        <span style={{ color: "var(--text-3)" }}>·</span>
        <span className="ellipsis" style={{ color: "var(--text-3)" }}>
          {doneCount} / {totalCount} files complete · queue ETA{" "}
          <span className="mono" style={{ color: "var(--text-2)" }}>
            {queueEta}
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
