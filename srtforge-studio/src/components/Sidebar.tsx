import { memo, type MouseEvent as ReactMouseEvent } from "react";

import { I } from "../icons";
import { openPath } from "../lib/tauri";
import { SIDEBAR_DEFAULT_W, clampSidebarWidth, useUi } from "../store";
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
  const collapsed = useUi((s) => s.sidebarCollapsed);
  const sidebarWidth = useUi((s) => s.sidebarWidth);
  const setSidebarWidth = useUi((s) => s.setSidebarWidth);
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

  // Drag the sidebar's right edge to resize; double-click resets. The width
  // lives in the store so the title bar's first cell can follow it.
  const onResizeStart = (e: ReactMouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const startX = e.clientX;
    const startW = sidebarWidth;
    const onMove = (ev: MouseEvent) => setSidebarWidth(clampSidebarWidth(startW + ev.clientX - startX));
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.classList.remove("is-resizing");
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    document.body.classList.add("is-resizing");
  };

  const navBtn = (id: Tab, label: string, icon: JSX.Element, count?: number | string) => {
    // Collapsed rail: only a real, non-zero count earns a corner badge.
    const badge = collapsed ? (typeof count === "number" && count > 0 ? count : "") : count;
    return (
      <button
        className={active === id ? "active" : ""}
        onClick={() => setActive(id)}
        title={collapsed ? label : undefined}
        aria-label={label}
      >
        {icon} <span>{label}</span>
        <span className="count">{badge}</span>
      </button>
    );
  };

  return (
    <aside className={`sidebar${collapsed ? " collapsed" : ""}`}>
      <div>
        <div className="brand" title={collapsed ? "Srtforge Studio · v0.1.0" : undefined}>
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
          <div
            className="hook-row"
            title={collapsed ? "Sonarr hook · off" : "Sonarr webhook status"}
          >
            <I.Antenna size={14} />
            {!collapsed && <span>Sonarr hook</span>}
            <span className="hook-state off">
              <span className="hook-dot" />
              {!collapsed && "off"}
            </span>
          </div>
        </nav>
      </div>

      <div />

      <div>
        {collapsed ? (
          <div
            className="device-mini"
            title={`GPU ${device}\nVRAM ${vram}\nModel ${modelLabel}`}
          >
            <I.Cpu size={14} />
            <div className="meter">
              <span style={{ height: `${gpuPct}%` }} />
            </div>
          </div>
        ) : (
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
        )}
        <div className="sidebar-actions">
          <button
            className="btn btn-ghost btn-grow"
            title="Settings"
            onClick={() => setSettingsOpen(true)}
          >
            <I.Settings size={14} /> {!collapsed && "Settings"}
          </button>
          <button
            className="btn btn-ghost btn-square"
            title="Help — opens the Srtforge repo on GitHub"
            onClick={() =>
              openPath(REPO_URL).catch((e) => showToast(`Open failed: ${e}`))
            }
          >
            <I.Help size={14} />
          </button>
        </div>
      </div>
      {!collapsed && (
        <div
          className="sidebar-resizer"
          role="separator"
          aria-orientation="vertical"
          title="Drag to resize · double-click to reset"
          onMouseDown={onResizeStart}
          onDoubleClick={() => setSidebarWidth(SIDEBAR_DEFAULT_W)}
        />
      )}
    </aside>
  );
};

export const Sidebar = memo(SidebarView);
