// Lightweight field-row primitives used by the Settings drawer.
// Keep these dumb — the parent drawer owns the binding to Zustand.

import type { ReactNode } from "react";

import { I } from "../../icons";
import { pickFolder } from "../../lib/tauri";

interface RowProps {
  label: string;
  desc?: string;
  compact?: boolean;
  children: ReactNode;
}

export const Row = ({ label, desc, compact, children }: RowProps) => (
  <div className={`setting-row${compact ? " compact" : ""}`}>
    <div className="label">
      {label}
      {desc && <div className="desc">{desc}</div>}
    </div>
    {children}
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
  value,
  onChange,
}: {
  value: boolean;
  onChange: (v: boolean) => void;
}) => (
  <button
    type="button"
    className={`toggle ${value ? "on" : ""}`}
    onClick={() => onChange(!value)}
    aria-pressed={value}
  />
);

interface SegProps<T extends string> {
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onChange: (v: T) => void;
}

export const Seg = <T extends string>({ options, value, onChange }: SegProps<T>) => (
  <div className="seg">
    {options.map((o) => (
      <button
        key={o.value}
        className={value === o.value ? "active" : ""}
        onClick={() => onChange(o.value)}
      >
        {o.label}
      </button>
    ))}
  </div>
);

interface TextProps {
  value: string;
  onChange: (v: string) => void;
  mono?: boolean;
  placeholder?: string;
}

export const Text = ({ value, onChange, mono, placeholder }: TextProps) => (
  <input
    type="text"
    className={`input wide${mono ? " mono" : ""}`}
    value={value}
    placeholder={placeholder}
    onChange={(e) => onChange(e.target.value)}
  />
);

interface NumberProps {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  width?: number;
}

export const NumInput = ({ value, onChange, min, max, step, width = 120 }: NumberProps) => (
  <input
    type="number"
    className="input mono"
    style={{ width }}
    value={Number.isFinite(value) ? value : 0}
    min={min}
    max={max}
    step={step}
    onChange={(e) => {
      const v = parseFloat(e.target.value);
      onChange(Number.isFinite(v) ? v : 0);
    }}
  />
);

interface SelectProps {
  value: string;
  onChange: (v: string) => void;
  options: ReadonlyArray<{ value: string; label: string }>;
}

export const Select = ({ value, onChange, options }: SelectProps) => (
  <select
    className="input wide"
    value={value}
    onChange={(e) => onChange(e.target.value)}
  >
    {options.map((o) => (
      <option key={o.value} value={o.value}>
        {o.label}
      </option>
    ))}
  </select>
);

interface PathProps {
  value: string;
  onChange: (v: string) => void;
}

/** Text input + folder-picker button. Lets the user paste or browse. */
export const PathPicker = ({ value, onChange }: PathProps) => (
  <div style={{ display: "flex", gap: 6, alignItems: "center", minWidth: 0, flex: 1 }}>
    <input
      type="text"
      className="input wide mono"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
    <button
      type="button"
      className="btn btn-ghost"
      title="Browse…"
      onClick={async () => {
        const picked = await pickFolder();
        if (picked) onChange(picked);
      }}
    >
      <I.FolderOpen size={14} />
    </button>
  </div>
);
