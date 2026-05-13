import { memo } from "react";

import { I } from "../icons";
import { openPath } from "../lib/tauri";
import { useUi } from "../store";
import type { Tab } from "../types";
import { BrandMark } from "./BrandMark";

interface Props {
  device: string;
  gpuPct: number;
  vram: string;
}

const REPO_URL = "https://github.com/StiensGate928/Srtforge";

const SidebarView = ({ device, gpuPct, vram }: Props) => {
  const active = useUi((s) => s.active);
  const setActive = useUi((s) => s.setActive);
  const setSettingsOpen = useUi((s) => s.setSettingsOpen);
  const showToast = useUi((s) => s.showToast);
  const asrModel = useUi((s) => s.settings.asrModel);
  const queueCount = useUi(
    (s) => s.files.filter((f) => f.status === "queued" || f.status === "processing").length,
  );
  const activeCount = useUi((s) => (s.files.some((f) => f.status === "processing") ? 1 : 0));
  const doneCount = useUi(
    (s) => s.files.filter((f) => f.status === "done" || f.status === "error").length,
  );
  // The settings store keeps the full HF id (e.g. nvidia/parakeet-tdt-0.6b-v2);
  // surface just the basename so it fits the sidebar.
  const modelLabel = asrModel.split("/").pop() ?? asrModel;

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
          {navBtn("queue", "Queue", <I.List size={14} />, queueCount)}
          {navBtn(
            "active",
            "Active job",
            <I.Pulse size={14} />,
            activeCount || "",
          )}
          {navBtn("history", "History", <I.Archive size={14} />, doneCount)}

          <div className="nav-section">Tools</div>
          {navBtn("normalize", "Normalize", <I.Sliders size={14} />, "")}
          {navBtn("bgm", "BGM separation", <I.Music size={14} />, "")}

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
            <span className="value" title={device}>{device}</span>
          </div>
          <div className="meter" title={`VRAM ${vram}`}>
            <span style={{ width: `${gpuPct}%` }} />
          </div>
          <div className="row">
            <span className="label">VRAM</span>
            <span className="value">{vram}</span>
          </div>
          <div className="row" style={{ marginTop: 2 }}>
            <span className="label">Model</span>
            <span className="value" title={asrModel}>{modelLabel}</span>
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
            title="Help — opens the Srtforge repo on GitHub"
            onClick={() =>
              openPath(REPO_URL).catch((e) => showToast(`Open failed: ${e}`))
            }
          >
            <I.Help size={14} />
          </button>
        </div>
      </div>
    </aside>
  );
};

export const Sidebar = memo(SidebarView);
