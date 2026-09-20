import type { MouseEvent as ReactMouseEvent } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";

import { I } from "../icons";

interface Props {
  jobName: string;
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
}

const win = () => getCurrentWindow();

export const TitleBar = ({ jobName, sidebarCollapsed, onToggleSidebar }: Props) => {
  // Only drag when the mousedown lands on the titlebar background itself,
  // not on a child button. `startDragging()` captures the mouse and would
  // otherwise eat the click event before it reaches min/max/close.
  const onTitlebarMouseDown = (e: ReactMouseEvent<HTMLDivElement>) => {
    if (e.buttons !== 1) return;
    if ((e.target as HTMLElement).closest("button")) return;
    win().startDragging();
  };

  return (
    <div className="titlebar" onMouseDown={onTitlebarMouseDown}>
      <div className="tb-left">
        <button
          type="button"
          className={`tb-btn tb-sidebar-toggle${sidebarCollapsed ? "" : " on"}`}
          title={sidebarCollapsed ? "Show sidebar (Ctrl+B)" : "Hide sidebar (Ctrl+B)"}
          aria-label={sidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
          aria-pressed={!sidebarCollapsed}
          onClick={onToggleSidebar}
        >
          <I.PanelLeft size={14} />
        </button>
      </div>
      <div className="tb-center">
        <span className="tb-sub" title={jobName}>
          {jobName}
        </span>
      </div>
      <div className="tb-controls">
        <button
          type="button"
          className="tb-btn"
          title="Minimize"
          onClick={() => {
            void win().minimize();
          }}
        >
          <I.Min size={14} />
        </button>
        <button
          type="button"
          className="tb-btn"
          title="Maximize"
          onClick={() => {
            void win().toggleMaximize();
          }}
        >
          <I.Sq size={11} sw={1.4} />
        </button>
        <button
          type="button"
          className="tb-btn close"
          title="Close"
          onClick={() => {
            void win().close();
          }}
        >
          <I.X size={14} />
        </button>
      </div>
    </div>
  );
};
