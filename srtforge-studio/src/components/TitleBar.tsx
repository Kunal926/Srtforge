import { getCurrentWindow } from "@tauri-apps/api/window";

import { I } from "../icons";
import { BrandMark } from "./BrandMark";

interface Props {
  jobName: string;
}

const win = () => getCurrentWindow();

export const TitleBar = ({ jobName }: Props) => (
  <div
    className="titlebar"
    onMouseDown={(e) => {
      // Drag the window when the user grabs the titlebar background.
      // Buttons stop propagation so they keep working.
      if (e.buttons === 1) win().startDragging();
    }}
  >
    <div className="tb-left">
      <span className="tb-mark">
        <BrandMark size={14} />
      </span>
      <span className="tb-title">Srtforge Studio</span>
      <span className="tb-sub">— {jobName}</span>
    </div>
    <div className="tb-center" />
    <div className="tb-controls">
      <button className="tb-btn" title="Minimize" onClick={() => win().minimize()}>
        <I.Min size={14} />
      </button>
      <button className="tb-btn" title="Maximize" onClick={() => win().toggleMaximize()}>
        <I.Sq size={11} sw={1.4} />
      </button>
      <button className="tb-btn close" title="Close" onClick={() => win().close()}>
        <I.X size={14} />
      </button>
    </div>
  </div>
);
