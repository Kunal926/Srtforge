import { useId, useMemo } from "react";

interface LowCostWaveformProps {
  progress: number;
  active?: boolean;
  variant?: "big" | "small";
  seed?: number;
  showAxis?: boolean;
  ariaLabel?: string;
}

const clamp01 = (n: number) => Math.max(0, Math.min(1, n));

const buildBarPath = (count: number, seed: number) => {
  const width = 1000;
  const step = width / count;
  const barWidth = Math.max(2.6, step * 0.48);
  let s = seed;
  let d = "";

  for (let i = 0; i < count; i += 1) {
    s = (s * 9301 + 49297) % 233280;
    const r = s / 233280;
    const env = 0.38 + 0.62 * Math.sin((i / Math.max(1, count - 1)) * Math.PI);
    const h = 14 + r * 78 * env;
    const x = i * step + (step - barWidth) / 2;
    const y = (100 - h) / 2;
    d += `M${x.toFixed(2)} ${y.toFixed(2)}h${barWidth.toFixed(2)}v${h.toFixed(2)}h-${barWidth.toFixed(2)}z`;
  }

  return d;
};

export const LowCostWaveform = ({
  progress,
  active = false,
  variant = "big",
  seed = 13,
  showAxis = variant === "big",
  ariaLabel = "audio waveform progress",
}: LowCostWaveformProps) => {
  const clipId = `waveClip${useId().replace(/:/g, "")}`;
  const count = variant === "big" ? 88 : 48;
  const path = useMemo(() => buildBarPath(count, seed), [count, seed]);
  const pct = clamp01(progress);

  return (
    <div
      className={`wave-low wave-low-${variant} ${active ? "active" : ""}`}
      role="img"
      aria-label={ariaLabel}
    >
      <svg
        width="100%"
        height="100%"
        preserveAspectRatio="none"
        viewBox="0 0 1000 100"
        aria-hidden="true"
      >
        <defs>
          <clipPath id={clipId}>
            <rect x="0" y="0" width={pct * 1000} height="100" />
          </clipPath>
        </defs>
        <path className="wave-low-path under" d={path} />
        <path className="wave-low-path fill" d={path} clipPath={`url(#${clipId})`} />
      </svg>
      {showAxis && (
        <div className="wave-axis">
          <span>00:00</span>
          <span>25%</span>
          <span>50%</span>
          <span>75%</span>
          <span>100%</span>
        </div>
      )}
    </div>
  );
};
