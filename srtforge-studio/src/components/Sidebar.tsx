import { I } from "../icons";
import { useUi } from "../store";
import type { Tab } from "../types";
import { BrandMark } from "./BrandMark";

interface Props {
  device: string;
  gpuPct: number;
  vram: string;
}

export const Sidebar = ({ device, gpuPct, vram }: Props) => {
  const active = useUi((s) => s.active);
  const setActive = useUi((s) => s.setActive);
  const setSettingsOpen = useUi((s) => s.setSettingsOpen);
  const counts = useUi((s) => ({
    queue: s.files.filter((f) => f.status === "queued" || f.status === "processing").length,
    active: s.files.find((f) => f.status === "processing") ? 1 : 0,
    done: s.files.filter((f) => f.status === "done" || f.status === "error").length,
  }));

  const navBtn = (id: Tab, label: string, icon: JSX.Element, count?: number | string) => (
    <button className={active === id ? "active" : ""} onClick={() => setActive(id)}>
      {icon} <span>{label}</span>
      <span className="count">{count}</span>
    </button>
  );

  return (
    <aside className="sidebar">
      <div>
        <div className="brand">
          <div className="brand-mark">
            <BrandMark size={18} />
          </div>
          <div className="name">
            Srtforge<span className="sub">Studio · v0.1.0</span>
          </div>
        </div>
        <nav className="nav">
          <div className="nav-section">Workspace</div>
          {navBtn("queue", "Queue", <I.List size={14} />, counts.queue)}
          {navBtn(
            "active",
            "Active job",
            <I.Pulse size={14} />,
            counts.active || "",
          )}
          {navBtn("history", "History", <I.Archive size={14} />, counts.done)}

          <div className="nav-section">Sources</div>
          {navBtn("watch", "Watch folders", <I.Folder size={14} />, "—")}
          <div className="hook-row" title="Sonarr webhook status">
            <I.Antenna size={14} />
            <span>Sonarr hook</span>
            <span className="hook-state off">
              <span className="hook-dot" />
              off
            </span>
          </div>
        </nav>
      </div>

      <div />

      <div>
        <div className="device-card">
          <div className="row">
            <span className="label">GPU</span>
            <span className="value">{device}</span>
          </div>
          <div className="meter">
            <span style={{ width: `${gpuPct}%` }} />
          </div>
          <div className="row">
            <span className="label">VRAM</span>
            <span className="value">{vram}</span>
          </div>
          <div className="row" style={{ marginTop: 2 }}>
            <span className="label">Model</span>
            <span className="value">parakeet-tdt-0.6b</span>
          </div>
        </div>
        <div style={{ padding: "0 8px 12px", display: "flex", gap: 6 }}>
          <button
            className="btn btn-ghost"
            style={{ flex: 1 }}
            onClick={() => setSettingsOpen(true)}
          >
            <I.Settings size={14} /> Settings
          </button>
          <button
            className="btn btn-ghost"
            style={{ width: 32, padding: 0, justifyContent: "center" }}
            title="Help"
          >
            <I.Help size={14} />
          </button>
        </div>
      </div>
    </aside>
  );
};
