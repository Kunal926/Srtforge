import type { MouseEvent as ReactMouseEvent } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";

import { I } from "../icons";
import { BrandMark } from "./BrandMark";

interface Props {
  jobName: string;
}

const win = () => getCurrentWindow();

export const TitleBar = ({ jobName }: Props) => {
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
        <span className="tb-mark">
          <BrandMark size={14} />
        </span>
        <span className="tb-title">Srtforge Studio</span>
        <span className="tb-sub">— {jobName}</span>
      </div>
      <div className="tb-center" />
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
