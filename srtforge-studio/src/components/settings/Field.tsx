// Lightweight field-row primitives used by the Settings drawer.
// Matches the design's HTML structure exactly so the existing
// `.setting-row > div:last-child > .input.wide` CSS keeps working.

import type { ReactNode } from "react";

interface RowProps {
  label: string;
  desc?: string;
  compact?: boolean;
  children: ReactNode;
}

export const Row = ({ label, desc, compact, children }: RowProps) => (
  <div className={`setting-row${compact ? " compact" : ""}`}>
    <div>
      <div className="label">{label}</div>
      {desc && <div className="desc">{desc}</div>}
    </div>
    <div>{children}</div>
  </div>
);

interface GroupProps {
  title: string;
  children: ReactNode;
}

export const Group = ({ title, children }: GroupProps) => (
  <div className="setting-group">
    <h4>{title}</h4>
    {children}
  </div>
);

export const Toggle = ({
  on,
  onClick,
  disabled = false,
}: {
  on: boolean;
  onClick: () => void;
  disabled?: boolean;
}) => (
  <div
    className={`toggle ${on ? "on" : ""}${disabled ? " disabled" : ""}`}
    aria-disabled={disabled}
    onClick={() => {
      if (!disabled) onClick();
    }}
  />
);
