import { useEffect, useMemo, useRef } from "react";

import { I } from "../icons";
import { STAGES } from "../lib/stages";
import type { LogLine, QueueFile } from "../types";

interface WaveProps {
  progress: number;
  active: boolean;
}

export const WaveformBig = ({ progress, active }: WaveProps) => {
  const N = 240;
  const bars = useMemo(() => {
    const arr: number[] = [];
    let s = 13;
    for (let i = 0; i < N; i++) {
      s = (s * 9301 + 49297) % 233280;
      const r = s / 233280;
      const env = 0.45 + 0.55 * Math.sin((i / N) * Math.PI * 1.2);
      arr.push(0.1 + r * 0.9 * env);
    }
    return arr;
  }, []);
  return (
    <div className="wave-canvas" role="img" aria-label="audio waveform">
      <svg width="100%" height="100%" preserveAspectRatio="none" viewBox={`0 0 ${N} 100`}>
        {bars.map((h, i) => (
          <rect
            key={`u${i}`}
            x={i + 0.2}
            width={0.6}
            y={50 - h * 42}
            height={h * 84}
            rx="0.3"
            style={{ fill: "var(--text-2)", opacity: 0.18 }}
          />
        ))}
        {bars.map((h, i) => {
          const played = i / N <= progress;
          if (!played) return null;
          return (
            <rect
              key={`p${i}`}
              x={i + 0.2}
              width={0.6}
              y={50 - h * 42}
              height={h * 84}
              rx="0.3"
              style={{ fill: "var(--accent)", opacity: 0.95 }}
            />
          );
        })}
      </svg>
      {active && <div className="wave-playhead" style={{ left: `${progress * 100}%` }} />}
      <div className="wave-axis">
        <span>00:00</span>
        <span>05:58</span>
        <span>11:57</span>
        <span>17:55</span>
        <span>23:54</span>
      </div>
    </div>
  );
};

interface StageListProps {
  currentStage: number;
  paused: boolean;
}

export const StageList = ({ currentStage, paused }: StageListProps) => (
  <div className="stage-list">
    {STAGES.map((s) => {
      const state =
        currentStage > s.id ? "done" : currentStage === s.id ? "active" : "queued";
      return (
        <div key={s.id} className={`stage ${state}`}>
          <div className="dot">{state === "done" && <I.Check size={9} sw={3} />}</div>
          <div>{s.label}</div>
          <div className="meta">
            {state === "done"
              ? "✓"
              : state === "active"
                ? paused
                  ? "paused"
                  : "running"
                : "—"}
          </div>
        </div>
      );
    })}
  </div>
);

interface LogsPanelProps {
  logs: LogLine[];
  height?: number;
}

export const LogsPanel = ({ logs, height = 220 }: LogsPanelProps) => {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [logs]);
  return (
    <div className="logs" ref={ref} style={{ height }}>
      {logs.map((l, i) => (
        <div key={i} className={`line ${l.run ? "run" : ""}`}>
          <span className="t">{l.t}</span>
          <span className={`lvl ${l.lvl}`}>{l.lvl}</span>
          <span className="msg">{l.msg}</span>
        </div>
      ))}
    </div>
  );
};

interface DetailProps {
  file: QueueFile;
  paused: boolean;
  logs: LogLine[];
  expanded?: boolean;
}

export const ActiveDetail = ({ file, paused, logs, expanded }: DetailProps) => {
  const progress = file.progress;
  const currentStage = file.stage;
  return (
    <div className={`detail ${expanded ? "expanded" : ""}`}>
      <div className="detail-top">
        <div className="detail-left">
          <h4>Active job · waveform &amp; vocal isolation</h4>
          <WaveformBig progress={progress} active={!paused && file.status === "processing"} />
          <div className="detail-cols">
            <div>
              <h4>File</h4>
              <div className="kvs">
                <div className="k">Name</div>
                <div className="v" style={{ wordBreak: "break-all" }}>
                  {file.name}
                </div>
                <div className="k">Duration</div>
                <div className="v">{file.duration}</div>
                <div className="k">Codec</div>
                <div className="v">
                  {file.codec} · {file.channels} ch · {file.sampleRate} kHz
                </div>
                <div className="k">FPS</div>
                <div className="v">{file.fps}</div>
              </div>
            </div>
            <div>
              <h4>Pipeline</h4>
              <div className="kvs">
                <div className="k">Backend</div>
                <div className="v">FV4 MelBand Roformer</div>
                <div className="k">ASR</div>
                <div className="v">parakeet-tdt-0.6b-v2</div>
                <div className="k">Device</div>
                <div className="v">cuda:0 · fp32</div>
                <div className="k">Lang</div>
                <div className="v">eng (auto)</div>
              </div>
            </div>
            <div>
              <h4>Output</h4>
              <div className="kvs">
                <div className="k">Path</div>
                <div className="v" style={{ wordBreak: "break-all" }}>
                  ./output/{file.name.replace(/\.[^.]+$/, ".srt")}
                </div>
                <div className="k">Style</div>
                <div className="v">Netflix · 42 char/line</div>
                <div className="k">Embed</div>
                <div className="v">soft (mkvmerge)</div>
              </div>
            </div>
          </div>
        </div>

        <div className="detail-right">
          <h4>Pipeline stages</h4>
          <StageList currentStage={currentStage} paused={paused} />
        </div>
      </div>

      <div className="detail-logs">
        <h4
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>Live logs</span>
          <span
            className="mono"
            style={{ fontSize: 10, color: "var(--text-3)", textTransform: "none", letterSpacing: 0 }}
          >
            {logs.length} lines
          </span>
        </h4>
        <LogsPanel logs={logs} />
      </div>
    </div>
  );
};
