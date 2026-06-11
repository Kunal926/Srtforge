// Settings drawer — Basic / Performance / Advanced tabs.
// Mirrors the Claude-Design prototype 1:1 (see project/srtforge_studio/
// settings.jsx in the design bundle).

import { useState } from "react";

import { I } from "../icons";
import {
  SUPPORTED_ASR_MODELS,
  asrEngineForModel,
  normalizeAsrModel,
} from "../lib/asrModels";
import { pickFolder } from "../lib/tauri";
import { useUi } from "../store";
import type { WhisperComputeType } from "../types";
import { Group, Row, Toggle } from "./settings/Field";

type Tab = "basic" | "performance" | "advanced";

interface PathRowProps {
  value: string;
  onChange: (v: string) => void;
}

const PathRow = ({ value, onChange }: PathRowProps) => (
  <div style={{ display: "flex", gap: 8, minWidth: 0 }}>
    <input
      className="input mono wide"
      style={{ flex: 1, minWidth: 0 }}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
    <button
      className="btn btn-ghost"
      style={{ flexShrink: 0 }}
      onClick={async () => {
        const picked = await pickFolder();
        if (picked) onChange(picked);
      }}
    >
      Browse…
    </button>
  </div>
);

const WHISPER_COMPUTE_OPTIONS: Array<{
  value: WhisperComputeType;
  label: string;
}> = [
  { value: "auto", label: "Auto" },
  { value: "int8_float16", label: "int8_float16 (recommended)" },
  { value: "float16", label: "float16" },
  { value: "bfloat16", label: "bfloat16" },
  { value: "int8", label: "int8" },
  { value: "int8_bfloat16", label: "int8_bfloat16" },
  { value: "float32", label: "float32" },
];

export const SettingsDrawer = () => {
  const useQuickSettings = useUi(
    (s) => s.settings.gpuPerformanceMode && s.files.some((f) => f.status === "processing"),
  );

  return useQuickSettings ? <QuickSettingsPanel /> : <FullSettingsDrawer />;
};

const QuickSettingsPanel = () => {
  const open = useUi((s) => s.settingsOpen);
  const onClose = () => useUi.getState().setSettingsOpen(false);
  const settings = useUi((s) => s.settings);
  const setSetting = useUi((s) => s.setSetting);
  const theme = useUi((s) => s.theme);
  const setTheme = useUi((s) => s.setTheme);

  if (!open) return null;

  return (
    <aside className="settings-lite" aria-label="Settings">
      <header>
        <h3>Settings</h3>
        <button className="btn btn-ghost" onClick={onClose}>
          <I.X size={14} /> Close
        </button>
      </header>
      <div className="settings-lite-body">
        <div className="settings-lite-row">
          <div className="label">Theme</div>
          <div className="seg">
            <button
              className={theme === "light" ? "active" : ""}
              onClick={() => setTheme("light")}
            >
              Light
            </button>
            <button
              className={theme === "dark" ? "active" : ""}
              onClick={() => setTheme("dark")}
            >
              Dark
            </button>
            <button
              className={theme === "forge" ? "active" : ""}
              onClick={() => setTheme("forge")}
            >
              Forge
            </button>
          </div>
        </div>
        <div className="settings-lite-row">
          <div className="label">Device</div>
          <div className="seg">
            <button
              className={settings.device === "auto" ? "active" : ""}
              onClick={() => setSetting("device", "auto")}
            >
              Auto
            </button>
            <button
              className={settings.device === "cuda" ? "active" : ""}
              onClick={() => setSetting("device", "cuda")}
            >
              GPU
            </button>
            <button
              className={settings.device === "cpu" ? "active" : ""}
              onClick={() => setSetting("device", "cpu")}
            >
              CPU
            </button>
          </div>
        </div>
        <div className="settings-lite-row compact">
          <div className="label">Max CUDA mode</div>
          <Toggle
            on={settings.gpuPerformanceMode}
            onClick={() => {
              setSetting("gpuPerformanceMode", !settings.gpuPerformanceMode);
              onClose();
            }}
          />
        </div>
        <div className="settings-lite-row compact">
          <div className="label">Save .srt next to video file</div>
          <Toggle
            on={settings.sidecarSrt}
            onClick={() => setSetting("sidecarSrt", !settings.sidecarSrt)}
          />
        </div>
        <div className="settings-lite-row compact">
          <div className="label">Free GPU memory when stopping</div>
          <Toggle
            on={settings.freeGpuOnStop}
            onClick={() =>
              setSetting("freeGpuOnStop", !settings.freeGpuOnStop)
            }
          />
        </div>
        <div className="settings-lite-row compact">
          <div className="label">Dump raw word-level timestamps</div>
          <Toggle
            on={settings.dumpWords}
            onClick={() => setSetting("dumpWords", !settings.dumpWords)}
          />
        </div>
      </div>
    </aside>
  );
};

const FullSettingsDrawer = () => {
  const open = useUi((s) => s.settingsOpen);
  const onClose = () => useUi.getState().setSettingsOpen(false);
  const settings = useUi((s) => s.settings);
  const setSetting = useUi((s) => s.setSetting);
  const theme = useUi((s) => s.theme);
  const setTheme = useUi((s) => s.setTheme);
  const resetSettings = useUi((s) => s.resetSettings);
  const showToast = useUi((s) => s.showToast);
  const [tab, setTab] = useState<Tab>("basic");
  const embedDisabled = !settings.embed;
  const selectedAsrModel = normalizeAsrModel(settings.asrModel);
  const selectedAsrEngine = asrEngineForModel(selectedAsrModel);
  const applyWhisperFv4Profile = () => {
    setSetting("device", "cuda");
    setSetting("preferGpu", true);
    setSetting("sep", "fv4");
    setSetting("preferCenter", true);
    setSetting("sepHz", 44100);
    setSetting("allowUntaggedEnglish", false);
    setSetting("fv4Cfg", "./models/voc_gabox.yaml");
    setSetting("fv4Ckpt", "./models/voc_fv4.ckpt");
    setSetting("asrModel", "large-v3-turbo");
    setSetting("engine", "whisper");
    setSetting("whisperComputeType", "int8_float16");
    setSetting("language", "en");
    setSetting("fp32", false);
    setSetting("attnLeft", 1280);
    setSetting("attnRight", 1280);
    setSetting("subsamplingChunkFactor", 0);
    setSetting("dumpWords", false);
    setSetting("extract", "dual_mono_center");
    setSetting(
      "filterChain",
      "highpass=f=60,lowpass=f=10000,aformat=sample_fmts=flt,aresample=resampler=soxr:osf=flt:osr=16000",
    );
    setSetting("geminiEnabled", false);
    setSetting("style", "netflix");
    showToast("Applied profile: Whisper int8_float16 + FV4");
  };

  return (
    <>
      <div className={`scrim ${open ? "open" : ""}`} onClick={onClose} />
      <aside className={`drawer ${open ? "open" : ""}`} aria-hidden={!open}>
        <header>
          <h3>Settings</h3>
          <button className="btn btn-ghost" onClick={onClose}>
            <I.X size={14} /> Close
          </button>
        </header>

        <div className="body">
          <div className="set-tabs" role="tablist">
            <button
              role="tab"
              className={tab === "basic" ? "active" : ""}
              onClick={() => setTab("basic")}
            >
              Basic
            </button>
            <button
              role="tab"
              className={tab === "performance" ? "active" : ""}
              onClick={() => setTab("performance")}
            >
              Performance
            </button>
            <button
              role="tab"
              className={tab === "advanced" ? "active" : ""}
              onClick={() => setTab("advanced")}
            >
              Advanced
            </button>
          </div>

          {tab === "basic" && (
            <div className="set-pane">
              <Group title="Appearance">
                <Row
                  label="Theme"
                  desc="Light is closest to Windows defaults. Forge is a warm charcoal with ember accent."
                >
                  <div className="seg">
                    <button
                      className={theme === "light" ? "active" : ""}
                      onClick={() => setTheme("light")}
                    >
                      Light
                    </button>
                    <button
                      className={theme === "dark" ? "active" : ""}
                      onClick={() => setTheme("dark")}
                    >
                      Dark
                    </button>
                    <button
                      className={theme === "forge" ? "active" : ""}
                      onClick={() => setTheme("forge")}
                    >
                      Forge
                    </button>
                  </div>
                </Row>
              </Group>

              <Group title="Video subtitle output">
                <Row
                  label="Embed in container"
                  desc="Mux .srt as a soft track via mkvmerge."
                  compact
                >
                  <Toggle
                    on={settings.embed}
                    onClick={() => setSetting("embed", !settings.embed)}
                  />
                </Row>
                <Row
                  label="Burn subtitles (hard sub)"
                  desc="Hard-encode subtitles into the video. Slow."
                  compact
                >
                  <Toggle
                    on={settings.embed && settings.burn}
                    disabled={embedDisabled}
                    onClick={() => setSetting("burn", !settings.burn)}
                  />
                </Row>
                <Row
                  label="Soft-embed method"
                  desc="Auto prefers MKVToolNix; falls back to FFmpeg remux."
                >
                  <div className="seg">
                    <button
                      className={settings.embed && settings.softEmbed === "auto" ? "active" : ""}
                      disabled={embedDisabled}
                      onClick={() => setSetting("softEmbed", "auto")}
                    >
                      Auto
                    </button>
                    <button
                      className={
                        settings.embed && settings.softEmbed === "mkvtoolnix"
                          ? "active"
                          : ""
                      }
                      disabled={embedDisabled}
                      onClick={() => setSetting("softEmbed", "mkvtoolnix")}
                    >
                      MKVToolNix
                    </button>
                    <button
                      className={settings.embed && settings.softEmbed === "ffmpeg" ? "active" : ""}
                      disabled={embedDisabled}
                      onClick={() => setSetting("softEmbed", "ffmpeg")}
                    >
                      FFmpeg
                    </button>
                  </div>
                </Row>
                <Row label="Track title">
                  <input
                    className="input wide"
                    disabled={embedDisabled}
                    value={settings.trackTitle}
                    onChange={(e) => setSetting("trackTitle", e.target.value)}
                  />
                </Row>
                <Row label="Track language">
                  <input
                    className="input mono"
                    style={{ width: 96, textAlign: "right" }}
                    disabled={embedDisabled}
                    value={settings.trackLang}
                    onChange={(e) => setSetting("trackLang", e.target.value)}
                  />
                </Row>
                <Row label="Set as default track" compact>
                  <Toggle
                    on={settings.embed && settings.defaultTrack}
                    disabled={embedDisabled}
                    onClick={() => setSetting("defaultTrack", !settings.defaultTrack)}
                  />
                </Row>
                <Row label="Mark as forced" compact>
                  <Toggle
                    on={settings.embed && settings.forcedTrack}
                    disabled={embedDisabled}
                    onClick={() => setSetting("forcedTrack", !settings.forcedTrack)}
                  />
                </Row>
                <Row
                  label="Replace original video file"
                  desc="Overwrite the source after a successful mux."
                  compact
                >
                  <Toggle
                    on={settings.embed && settings.replaceOriginal}
                    disabled={embedDisabled}
                    onClick={() =>
                      setSetting("replaceOriginal", !settings.replaceOriginal)
                    }
                  />
                </Row>
              </Group>

              <Group title="Output options">
                <Row label="Save .srt next to video file" compact>
                  <Toggle
                    on={settings.sidecarSrt}
                    onClick={() => setSetting("sidecarSrt", !settings.sidecarSrt)}
                  />
                </Row>
                <Row
                  label="Dump raw word-level timestamps"
                  desc="Writes a .json next to the .srt with per-word timing."
                  compact
                >
                  <Toggle
                    on={settings.dumpWords}
                    onClick={() => setSetting("dumpWords", !settings.dumpWords)}
                  />
                </Row>
                <Row
                  label="Enable Gemini text correction"
                  desc="Pass transcript through Gemini for clean-up. Configure in Performance tab."
                  compact
                >
                  <Toggle
                    on={settings.geminiEnabled}
                    onClick={() =>
                      setSetting("geminiEnabled", !settings.geminiEnabled)
                    }
                  />
                </Row>
              </Group>

              <Group title="Subtitle style">
                <Row
                  label="Style"
                  desc="Netflix house style — 42 char/line, ≥0.83 s, 17 cps."
                >
                  <div className="seg">
                    <button
                      className={settings.style === "netflix" ? "active" : ""}
                      onClick={() => setSetting("style", "netflix")}
                    >
                      Netflix
                    </button>
                    <button
                      className={settings.style === "bbc" ? "active" : ""}
                      onClick={() => setSetting("style", "bbc")}
                    >
                      BBC
                    </button>
                    <button
                      className={settings.style === "custom" ? "active" : ""}
                      onClick={() => setSetting("style", "custom")}
                    >
                      Custom
                    </button>
                  </div>
                </Row>
              </Group>
            </div>
          )}

          {tab === "performance" && (
            <div className="set-pane">
              <Group title="Profiles">
                <Row
                  label="Best benchmark profile"
                  desc="Local Whisper large-v3-turbo with int8_float16 compute and FV4 vocal separation."
                >
                  <button
                    className="btn btn-primary"
                    onClick={applyWhisperFv4Profile}
                  >
                    Apply Whisper int8+FV4
                  </button>
                </Row>
              </Group>

              <Group title="Hardware">
                <Row
                  label="Inference device"
                  desc="Auto-detected GPU. Falls back to CPU if unavailable."
                >
                  <div className="seg">
                    <button
                      className={settings.device === "auto" ? "active" : ""}
                      onClick={() => setSetting("device", "auto")}
                    >
                      Auto
                    </button>
                    <button
                      className={settings.device === "cuda" ? "active" : ""}
                      onClick={() => setSetting("device", "cuda")}
                    >
                      GPU
                    </button>
                    <button
                      className={settings.device === "cpu" ? "active" : ""}
                      onClick={() => setSetting("device", "cpu")}
                    >
                      CPU
                    </button>
                  </div>
                </Row>
                <Row
                  label="Max CUDA mode"
                  desc="Reduces foreground Studio rendering while GPU jobs run."
                  compact
                >
                  <Toggle
                    on={settings.gpuPerformanceMode}
                    onClick={() =>
                      setSetting("gpuPerformanceMode", !settings.gpuPerformanceMode)
                    }
                  />
                </Row>
                <Row
                  label="Free GPU memory when stopping"
                  desc="Kills the active worker tree and sweeps stale sidecar workers before restart."
                  compact
                >
                  <Toggle
                    on={settings.freeGpuOnStop}
                    onClick={() =>
                      setSetting("freeGpuOnStop", !settings.freeGpuOnStop)
                    }
                  />
                </Row>
              </Group>

              <Group title="ASR engine">
                <Row label="ASR model">
                  <select
                    className="input wide"
                    value={selectedAsrModel}
                    onChange={(e) => {
                      const v = e.target.value;
                      setSetting("asrModel", v);
                      setSetting("engine", asrEngineForModel(v));
                    }}
                  >
                    {SUPPORTED_ASR_MODELS.map((model) => (
                      <option key={model.value} value={model.value}>
                        {model.label}
                      </option>
                    ))}
                  </select>
                </Row>
                <Row
                  label="Whisper compute type"
                  desc="Faster-Whisper precision. int8_float16 matched the best FV4 benchmark profile."
                >
                  <select
                    className="input wide"
                    value={settings.whisperComputeType}
                    disabled={selectedAsrEngine !== "whisper"}
                    onChange={(e) =>
                      setSetting(
                        "whisperComputeType",
                        e.target.value as WhisperComputeType,
                      )
                    }
                  >
                    {WHISPER_COMPUTE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </Row>
                <Row
                  label="Whisper language"
                  desc="ISO 639-1 code, or 'auto' to detect."
                >
                  <input
                    className="input mono"
                    style={{ width: 96, textAlign: "right" }}
                    value={settings.language}
                    onChange={(e) => setSetting("language", e.target.value)}
                  />
                </Row>
                <Row
                  label="Force float32 (Parakeet)"
                  desc="Higher accuracy, ~30% slower than fp16."
                  compact
                >
                  <Toggle
                    on={settings.fp32}
                    onClick={() => setSetting("fp32", !settings.fp32)}
                  />
                </Row>
                <Row
                  label="Local attention window (Parakeet)"
                  desc="rel_pos_local_attn — left / right context tokens."
                >
                  <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                    <span className="dim mono" style={{ fontSize: 11 }}>
                      Left
                    </span>
                    <input
                      className="input mono"
                      style={{ width: 72, textAlign: "right" }}
                      value={settings.attnLeft}
                      onChange={(e) =>
                        setSetting(
                          "attnLeft",
                          parseInt(e.target.value || "0", 10),
                        )
                      }
                    />
                    <span className="dim mono" style={{ fontSize: 11 }}>
                      Right
                    </span>
                    <input
                      className="input mono"
                      style={{ width: 72, textAlign: "right" }}
                      value={settings.attnRight}
                      onChange={(e) =>
                        setSetting(
                          "attnRight",
                          parseInt(e.target.value || "0", 10),
                        )
                      }
                    />
                  </div>
                </Row>
                <Row
                  label="Subsampling conv chunking"
                  desc="Enable to set factor = 1; off keeps the default 0."
                  compact
                >
                  <Toggle
                    on={settings.subsamplingChunkFactor > 0}
                    onClick={() =>
                      setSetting(
                        "subsamplingChunkFactor",
                        settings.subsamplingChunkFactor > 0 ? 0 : 1,
                      )
                    }
                  />
                </Row>
              </Group>

              <Group title="Gemini correction">
                <Row label="Gemini model id">
                  <input
                    className="input mono wide"
                    value={settings.geminiModel}
                    onChange={(e) => setSetting("geminiModel", e.target.value)}
                  />
                </Row>
                <Row
                  label="Gemini API key"
                  desc="Leave blank to use SRTFORGE_GEMINI_API_KEY environment variable."
                >
                  <input
                    className="input mono wide"
                    type="password"
                    placeholder="Leave blank to use SRTFORGE_GEMINI_API_KEY"
                    value={settings.geminiKey}
                    onChange={(e) => setSetting("geminiKey", e.target.value)}
                  />
                </Row>
              </Group>
            </div>
          )}

          {tab === "advanced" && (
            <div className="set-pane">
              <Group title="Paths">
                <Row
                  label="Output directory"
                  desc="Where finished .srt files (and optional muxed media) are written."
                >
                  <PathRow
                    value={settings.outputDir}
                    onChange={(v) => setSetting("outputDir", v)}
                  />
                </Row>
                <Row
                  label="Temp directory"
                  desc="Scratch space for intermediate WAVs and isolated vocals."
                >
                  <PathRow
                    value={settings.tempDir}
                    onChange={(v) => setSetting("tempDir", v)}
                  />
                </Row>
              </Group>

              <Group title="Vocal separation">
                <Row label="Separation backend">
                  <select
                    className="input wide"
                    value={settings.sep}
                    onChange={(e) =>
                      setSetting("sep", e.target.value as "fv4" | "none")
                    }
                  >
                    <option value="fv4">FV4 (recommended)</option>
                    <option value="none">None — use raw audio</option>
                  </select>
                </Row>
                <Row label="Separation sample rate (Hz)">
                  <input
                    className="input mono"
                    style={{ width: 120, textAlign: "right" }}
                    value={settings.sepHz}
                    onChange={(e) =>
                      setSetting("sepHz", parseInt(e.target.value || "0", 10))
                    }
                  />
                </Row>
                <Row
                  label="Prefer center channel (separation)"
                  desc="When 5.1 is available, use FC for cleaner dialog."
                  compact
                >
                  <Toggle
                    on={settings.preferCenter}
                    onClick={() =>
                      setSetting("preferCenter", !settings.preferCenter)
                    }
                  />
                </Row>
                <Row
                  label="Allow untagged English fallback"
                  desc="Process audio with no language tag as English."
                  compact
                >
                  <Toggle
                    on={settings.allowUntaggedEnglish}
                    onClick={() =>
                      setSetting(
                        "allowUntaggedEnglish",
                        !settings.allowUntaggedEnglish,
                      )
                    }
                  />
                </Row>
                <Row
                  label="FV4 config"
                  desc="separation.fv4.cfg — MelBand Roformer YAML."
                >
                  <input
                    className="input mono wide"
                    value={settings.fv4Cfg}
                    onChange={(e) => setSetting("fv4Cfg", e.target.value)}
                  />
                </Row>
                <Row
                  label="FV4 checkpoint"
                  desc="separation.fv4.ckpt — model weights."
                >
                  <input
                    className="input mono wide"
                    value={settings.fv4Ckpt}
                    onChange={(e) => setSetting("fv4Ckpt", e.target.value)}
                  />
                </Row>
              </Group>

              <Group title="FFmpeg pipeline">
                <Row
                  label="Audio extraction mode"
                  desc="Stereo mix is safest; dual mono center isolates dialog when an FC channel is present."
                >
                  <select
                    className="input wide"
                    value={settings.extract}
                    onChange={(e) =>
                      setSetting(
                        "extract",
                        e.target.value as "stereo_mix" | "dual_mono_center",
                      )
                    }
                  >
                    <option value="stereo_mix">Stereo Mix</option>
                    <option value="dual_mono_center">
                      Dual Mono (Center Isolation)
                    </option>
                  </select>
                </Row>
                <Row
                  label="FFmpeg filter chain"
                  desc="Applied before resample to 16 kHz."
                >
                  <textarea
                    className="input mono wide"
                    rows={3}
                    style={{
                      height: "auto",
                      padding: 8,
                      lineHeight: 1.45,
                      resize: "vertical",
                    }}
                    value={settings.filterChain}
                    onChange={(e) => setSetting("filterChain", e.target.value)}
                  />
                </Row>
              </Group>

              <Group title="Sonarr integration">
                <Row label="Custom script enabled" compact>
                  <Toggle
                    on={settings.sonarr}
                    onClick={() => setSetting("sonarr", !settings.sonarr)}
                  />
                </Row>
                <Row label="Trigger events">
                  <span
                    className="mono"
                    style={{ fontSize: 11, color: "var(--text-2)" }}
                  >
                    On Import · On Upgrade
                  </span>
                </Row>
              </Group>
            </div>
          )}
        </div>

        <footer>
          <button
            className="btn btn-ghost"
            onClick={() => {
              resetSettings();
              showToast("Settings reset to defaults");
            }}
          >
            Reset to defaults
          </button>
          <button
            className="btn btn-ghost"
            onClick={() => showToast("ETA history is not yet recorded")}
            title="Will clear the per-file ETA prediction cache once that history is wired up."
          >
            Clear ETA history
          </button>
          <span style={{ flex: 1 }} />
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={onClose}>
            OK
          </button>
        </footer>
      </aside>
    </>
  );
};
