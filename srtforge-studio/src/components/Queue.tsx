import { useEffect, useMemo, useRef, useState } from "react";

import { I } from "../icons";
import { locateFile, type LocateKind } from "../lib/locate";
import { useUi } from "../store";
import type { FileStatus, QueueFile } from "../types";

interface DropZoneProps {
  over: boolean;
  onAdd: () => void;
  onAddFolder: () => void;
}

export const DropZone = ({ over, onAdd, onAddFolder }: DropZoneProps) => (
  <div className={`dropzone ${over ? "over" : ""}`}>
    <div className="icon">
      <I.Drop size={26} />
    </div>
    <div className="text">
      <h3>Drop video or audio files here</h3>
      <p>
        .mkv · .mp4 · .mov · .wav · .flac · .m4a — folders are scanned recursively.
        English audio is auto-selected.
      </p>
    </div>
    <div style={{ display: "flex", gap: 8 }}>
      <button className="btn btn-primary" onClick={onAdd}>
        <I.Plus size={14} /> Add files
      </button>
      <button className="btn" onClick={onAddFolder}>
        <I.FolderOpen size={14} /> Add folder
      </button>
    </div>
  </div>
);

interface EmptyProps {
  over: boolean;
  onAdd: () => void;
  onAddFolder: () => void;
}

export const QueueEmpty = ({ over, onAdd, onAddFolder }: EmptyProps) => (
  <div className={`q-empty ${over ? "over" : ""}`}>
    <div className="qe-art">
      <svg viewBox="0 0 220 110" width="220" height="110" fill="none">
        <rect x="36" y="28" width="148" height="10" rx="3" fill="currentColor" opacity="0.10" />
        <rect x="60" y="46" width="100" height="10" rx="3" fill="currentColor" opacity="0.18" />
        <rect x="36" y="64" width="148" height="10" rx="3" fill="currentColor" opacity="0.10" />
        <path
          d="M110 8 v18 m-7 -7 l7 7 7 -7"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity="0.55"
        />
        <circle cx="110" cy="89" r="3.2" fill="var(--accent)" />
      </svg>
    </div>
    <h2>Your queue is empty</h2>
    <p>
      Drop video or audio files anywhere in this window — folders scan recursively and
      English audio is auto-selected. Or pick from disk:
    </p>
    <div className="qe-actions">
      <button className="btn btn-primary" onClick={onAdd}>
        <I.Plus size={14} /> Add files
      </button>
      <button className="btn" onClick={onAddFolder}>
        <I.FolderOpen size={14} /> Add folder
      </button>
    </div>
    <div className="qe-formats">
      {[".mkv", ".mp4", ".mov", ".wav", ".flac", ".m4a", ".webm", ".aac"].map((f) => (
        <span key={f} className="qe-fmt mono">
          {f}
        </span>
      ))}
    </div>
  </div>
);

interface StatusPillProps {
  s: FileStatus;
  progress: number;
}

export const StatusPill = ({ s, progress }: StatusPillProps) => {
  const map: Record<FileStatus, { cls: string; label: string }> = {
    queued: { cls: "queued", label: "Queued" },
    processing: { cls: "processing", label: `Processing ${Math.round(progress * 100)}%` },
    done: { cls: "done", label: "Done" },
    error: { cls: "error", label: "Failed" },
  };
  const m = map[s];
  return (
    <span className={`status ${m.cls}`}>
      <span className="pulse" />
      {m.label}
    </span>
  );
};

const fileExt = (n: string) => (n.split(".").pop() ?? "").toUpperCase();

interface OutputMenuProps {
  onPick: (kind: LocateKind) => void;
  /** Anchor coordinates in viewport space. Opens upward near the footer. */
  anchor: { right: number; top?: number; bottom?: number };
}

// Rendered with `position: fixed` against viewport coords so the menu
// escapes any `overflow: hidden` ancestor (the queue/history list).
const OutputMenu = ({ onPick, anchor }: OutputMenuProps) => (
  <div
    className="output-menu"
    style={{
      position: "fixed",
      right: anchor.right,
      top: anchor.top,
      bottom: anchor.bottom,
    }}
    onClick={(e) => e.stopPropagation()}
  >
    <button onClick={() => onPick("srt")}>
      <I.Done size={14} /> SRT file
    </button>
    <button onClick={() => onPick("performance-log")}>
      <I.List size={14} /> Performance log
    </button>
    <button onClick={() => onPick("debug-log")}>
      <I.List size={14} /> Debug log
    </button>
    <button onClick={() => onPick("folder")}>
      <I.FolderOpen size={14} /> Containing folder
    </button>
  </div>
);

interface OutputSplitProps {
  onLocate: (kind: LocateKind) => void;
}

export const OutputSplitButton = ({ onLocate }: OutputSplitProps) => {
  const [anchor, setAnchor] = useState<OutputMenuProps["anchor"] | null>(
    null,
  );
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuHeight = 152;
  useEffect(() => {
    if (!anchor) return;
    const close = () => setAnchor(null);
    const t = setTimeout(() => window.addEventListener("click", close, { once: true }), 0);
    // Reposition on scroll/resize since `position: fixed` is viewport-anchored.
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      clearTimeout(t);
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [anchor]);
  return (
    <div className="output-split">
      <button
        ref={btnRef}
        className="row-action"
        title="Open output"
        onClick={(e) => {
          e.stopPropagation();
          if (anchor) {
            setAnchor(null);
            return;
          }
          const r = btnRef.current?.getBoundingClientRect();
          if (!r) return;
          const right = Math.max(8, window.innerWidth - r.right);
          const top = r.bottom + 4;
          const opensPastViewport = top + menuHeight > window.innerHeight - 8;
          setAnchor(
            opensPastViewport && r.top > menuHeight
              ? {
                  right,
                  bottom: window.innerHeight - r.top + 4,
                }
              : {
                  right,
                  top: Math.max(8, Math.min(top, window.innerHeight - menuHeight - 8)),
                },
          );
        }}
      >
        <I.FolderOpen size={14} />
      </button>
      {anchor && (
        <OutputMenu
          anchor={anchor}
          onPick={(k) => {
            setAnchor(null);
            onLocate(k);
          }}
        />
      )}
    </div>
  );
};

interface QueueTableProps {
  files: QueueFile[];
}

export const QueueTable = ({ files }: QueueTableProps) => {
  const selectedId = useUi((s) => s.selectedId);
  const setSelectedId = useUi((s) => s.setSelectedId);
  const checked = useUi((s) => s.checked);
  const toggleChecked = useUi((s) => s.toggleChecked);

  return (
    <div className="queue-wrap">
      <div className="qhead">
        <span></span>
        <span className="sortable">
          Name <I.ChevronD size={11} sw={1.4} />
        </span>
        <span>Status</span>
        <span>Duration</span>
        <span>Metadata</span>
        <span>ETA</span>
        <span>Progress</span>
        <span></span>
      </div>
      {files.map((f) => (
        <div
          key={f.id}
          className={`qrow is-${f.status} ${selectedId === f.id ? "selected" : ""}`}
          onClick={() => setSelectedId(f.id)}
        >
          <div
            className={`checkbox ${checked.has(f.id) ? "on" : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              toggleChecked(f.id);
            }}
          >
            {checked.has(f.id) && <I.Check size={11} sw={2.4} />}
          </div>
          <div className="name">
            <span className="ext">{fileExt(f.name)}</span>
            <span className="filename">{f.name}</span>
          </div>
          <StatusPill s={f.status} progress={f.progress} />
          <span className="duration">{f.duration}</span>
          <div className="metapills">
            {f.sampleRate > 0 && <span className="pill">{f.sampleRate} kHz</span>}
            {f.channels > 0 && <span className="pill">{f.channels} ch</span>}
            {f.fps !== "—" && <span className="pill">{f.fps} fps</span>}
          </div>
          <span className={`eta ${f.eta === "—" ? "dim" : ""}`}>{f.eta}</span>
          <div className="progress" style={{ opacity: f.status === "queued" ? 0.35 : 1 }}>
            <span style={{ width: `${f.progress * 100}%` }} />
          </div>
          <OutputSplitButton onLocate={(k) => locateFile(f, k)} />
        </div>
      ))}
    </div>
  );
};

interface SparklineProps {
  progress: number;
  active: boolean;
  height?: number;
  seed?: number;
  quiet?: boolean;
}

export const Sparkline = ({
  progress,
  active,
  height = 44,
  seed = 7,
  quiet = false,
}: SparklineProps) => {
  const N = 96;
  const bars = useMemo(() => {
    const arr: number[] = [];
    let s = seed;
    for (let i = 0; i < N; i++) {
      s = (s * 9301 + 49297) % 233280;
      const r = s / 233280;
      const env = 0.55 + 0.45 * Math.sin((i / N) * Math.PI);
      arr.push(0.18 + r * 0.82 * env);
    }
    return arr;
  }, [seed]);
  if (quiet && active) {
    return (
      <div className="wave wave-static-small" style={{ height }}>
        <span style={{ width: `${Math.max(0, Math.min(1, progress)) * 100}%` }} />
      </div>
    );
  }
  return (
    <div className="wave" style={{ height }}>
      <svg width="100%" height="100%" preserveAspectRatio="none" viewBox={`0 0 ${N} 100`}>
        {bars.map((h, i) => {
          const played = i / N <= progress;
          return (
            <rect
              key={`p${i}`}
              x={i + 0.15}
              width={0.7}
              y={50 - h * 46}
              height={h * 92}
              rx="0.3"
              style={{
                fill: "var(--accent)",
                opacity: played ? (active ? 0.9 : 0.55) : 0,
              }}
            />
          );
        })}
        {bars.map((h, i) => (
          <rect
            key={`u${i}`}
            x={i + 0.15}
            width={0.7}
            y={50 - h * 46}
            height={h * 92}
            rx="0.3"
            style={{ fill: "var(--text-2)", opacity: 0.2 }}
          />
        ))}
      </svg>
      {active && <div className="wave-playhead" style={{ left: `${progress * 100}%` }} />}
    </div>
  );
};

interface QueueCardsProps {
  files: QueueFile[];
  quiet?: boolean;
}

export const QueueCards = ({ files, quiet = false }: QueueCardsProps) => {
  const selectedId = useUi((s) => s.selectedId);
  const setSelectedId = useUi((s) => s.setSelectedId);
  return (
    <div className="cards">
      {files.map((f) => (
        <div
          key={f.id}
          className={`card ${selectedId === f.id ? "selected" : ""}`}
          onClick={() => setSelectedId(f.id)}
        >
          <div className="ctop">
            <div>
              <div className="filename">{f.name}</div>
              <div className="meta" style={{ marginTop: 6 }}>
                {f.duration !== "—" && <span className="pill">{f.duration}</span>}
                {f.sampleRate > 0 && <span className="pill">{f.sampleRate} kHz</span>}
                {f.channels > 0 && <span className="pill">{f.channels} ch</span>}
                {f.fps !== "—" && <span className="pill">{f.fps} fps</span>}
                {f.codec !== "—" && <span className="pill">{f.codec}</span>}
              </div>
            </div>
            <StatusPill s={f.status} progress={f.progress} />
          </div>
          <Sparkline
            progress={f.progress}
            active={f.status === "processing"}
            quiet={quiet}
          />
          <div className="progress-row">
            <div className="progress" style={{ opacity: f.status === "queued" ? 0.35 : 1 }}>
              <span style={{ width: `${f.progress * 100}%` }} />
            </div>
            <span className="mono" style={{ minWidth: 36, textAlign: "right" }}>
              {f.eta}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
};
