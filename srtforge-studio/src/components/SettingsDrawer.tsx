import { useState } from "react";

import { I } from "../icons";
import { useUi } from "../store";
import type { Theme } from "../types";

type Tab = "basic" | "performance" | "advanced";

const Toggle = ({
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

export const SettingsDrawer = () => {
  const open = useUi((s) => s.settingsOpen);
  const close = () => useUi.getState().setSettingsOpen(false);
  const settings = useUi((s) => s.settings);
  const setSetting = useUi((s) => s.setSetting);
  const theme = useUi((s) => s.theme);
  const setTheme = useUi((s) => s.setTheme);
  const [tab, setTab] = useState<Tab>("basic");

  return (
    <>
      <div className={`scrim ${open ? "open" : ""}`} onClick={close} />
      <div className={`drawer ${open ? "open" : ""}`}>
        <header>
          <h3>Settings</h3>
          <button className="btn btn-ghost" onClick={close}>
            <I.X size={14} />
          </button>
        </header>

        <div className="body">
          <div className="set-tabs">
            <button className={tab === "basic" ? "active" : ""} onClick={() => setTab("basic")}>
              Basic
            </button>
            <button
              className={tab === "performance" ? "active" : ""}
              onClick={() => setTab("performance")}
            >
              Performance
            </button>
            <button
              className={tab === "advanced" ? "active" : ""}
              onClick={() => setTab("advanced")}
            >
              Advanced
            </button>
          </div>

          {tab === "basic" && (
            <div className="set-pane">
              <div className="setting-group">
                <h4>Appearance</h4>
                <div className="setting-row">
                  <div className="label">
                    Theme
                    <div className="desc">Light · Dark · Forge</div>
                  </div>
                  <div className="seg">
                    {(["light", "dark", "forge"] as Theme[]).map((t) => (
                      <button
                        key={t}
                        className={theme === t ? "active" : ""}
                        onClick={() => setTheme(t)}
                      >
                        {t[0].toUpperCase() + t.slice(1)}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              <div className="setting-group">
                <h4>Output</h4>
                <div className="setting-row">
                  <div className="label">Output folder</div>
                  <input
                    className="input wide mono"
                    value={settings.outputDir}
                    onChange={(e) => setSetting("outputDir", e.target.value)}
                  />
                </div>
                <div className="setting-row compact">
                  <div className="label">Burn subtitles</div>
                  <Toggle value={settings.burn} onChange={(v) => setSetting("burn", v)} />
                </div>
                <div className="setting-row compact">
                  <div className="label">Embed soft track</div>
                  <Toggle value={settings.embed} onChange={(v) => setSetting("embed", v)} />
                </div>
                <div className="setting-row compact">
                  <div className="label">Sidecar .srt file</div>
                  <Toggle
                    value={settings.sidecarSrt}
                    onChange={(v) => setSetting("sidecarSrt", v)}
                  />
                </div>
              </div>
            </div>
          )}

          {tab === "performance" && (
            <div className="set-pane">
              <div className="setting-group">
                <h4>ASR</h4>
                <div className="setting-row">
                  <div className="label">Engine</div>
                  <select
                    className="input wide"
                    value={settings.engine}
                    onChange={(e) =>
                      setSetting("engine", e.target.value as "parakeet" | "whisper")
                    }
                  >
                    <option value="parakeet">Parakeet TDT</option>
                    <option value="whisper">Whisper</option>
                  </select>
                </div>
                <div className="setting-row">
                  <div className="label">Model</div>
                  <input
                    className="input wide mono"
                    value={settings.asrModel}
                    onChange={(e) => setSetting("asrModel", e.target.value)}
                  />
                </div>
                <div className="setting-row compact">
                  <div className="label">Force float32</div>
                  <Toggle value={settings.fp32} onChange={(v) => setSetting("fp32", v)} />
                </div>
                <div className="setting-row compact">
                  <div className="label">Free GPU on stop</div>
                  <Toggle
                    value={settings.freeGpuOnStop}
                    onChange={(v) => setSetting("freeGpuOnStop", v)}
                  />
                </div>
              </div>
            </div>
          )}

          {tab === "advanced" && (
            <div className="set-pane">
              <div className="setting-group">
                <h4>Paths</h4>
                <div className="setting-row">
                  <div className="label">Temp folder</div>
                  <input
                    className="input wide mono"
                    value={settings.tempDir}
                    onChange={(e) => setSetting("tempDir", e.target.value)}
                  />
                </div>
              </div>
              <div className="setting-group">
                <h4>FFmpeg</h4>
                <div className="setting-row">
                  <div className="label">Filter chain</div>
                  <textarea
                    className="input wide mono"
                    rows={3}
                    value={settings.filterChain}
                    onChange={(e) => setSetting("filterChain", e.target.value)}
                  />
                </div>
              </div>
            </div>
          )}
        </div>

        <footer>
          <button className="btn btn-ghost" onClick={close}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={close}>
            OK
          </button>
        </footer>
      </div>
    </>
  );
};
