import { formatTotalDuration } from "../lib/format";

interface Props {
  queueEta: {
    seconds: number;
    updatedAtMs?: number;
  };
  doneCount: number;
  totalCount: number;
  /** Friendly ASR model label, from the current settings. */
  modelLabel: string;
  /** "cuda:0 · fp16"-style device and precision, from the current settings. */
  deviceLabel: string;
  status: "idle" | "paused" | "running";
}

export const StatusBar = ({
  queueEta,
  doneCount,
  totalCount,
  modelLabel,
  deviceLabel,
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
        <span className="chip" title="ASR model — change it in Settings › ASR">
          {modelLabel}
        </span>
        <span className="chip" title="Device · precision">
          {deviceLabel}
        </span>
      </div>
    </div>
  );
};
