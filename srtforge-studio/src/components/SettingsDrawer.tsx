import { useState } from "react";

import { I } from "../icons";
import { useUi } from "../store";
import type { Density, Layout, Theme } from "../types";
import {
  Group,
  NumInput,
  PathPicker,
  Row,
  Seg,
  Select,
  Text,
  Toggle,
} from "./settings/Field";

type Tab = "basic" | "performance" | "advanced";

// Some fields are visible in the UI but the current Python pipeline
// doesn't honor them yet (they were used by the legacy PySide6 GUI for
// post-pipeline embed/burn steps). Marked here so we can flag them with
// a small badge so the user isn't surprised when toggling has no effect.
const NOT_WIRED_NOTE = "Stored in settings — not yet honored by the pipeline.";

export const SettingsDrawer = () => {
  const open = useUi((s) => s.settingsOpen);
  const close = () => useUi.getState().setSettingsOpen(false);
  const settings = useUi((s) => s.settings);
  const setSetting = useUi((s) => s.setSetting);
  const theme = useUi((s) => s.theme);
  const setTheme = useUi((s) => s.setTheme);
  const layout = useUi((s) => s.layout);
  const setLayout = useUi((s) => s.setLayout);
  const density = useUi((s) => s.density);
  const setDensity = useUi((s) => s.setDensity);
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
              <Group title="Appearance">
                <Row label="Theme" desc="Light · Dark · Forge">
                  <Seg
                    options={[
                      { value: "light", label: "Light" },
                      { value: "dark", label: "Dark" },
                      { value: "forge", label: "Forge" },
                    ]}
                    value={theme}
                    onChange={(v) => setTheme(v as Theme)}
                  />
                </Row>
                <Row label="Queue layout" desc="Spacious table or compact cards">
                  <Seg
                    options={[
                      { value: "hybrid", label: "Hybrid" },
                      { value: "card", label: "Cards" },
                    ]}
                    value={layout}
                    onChange={(v) => setLayout(v as Layout)}
                  />
                </Row>
                <Row label="Row density">
                  <Seg
                    options={[
                      { value: "comfortable", label: "Comfortable" },
                      { value: "compact", label: "Compact" },
                    ]}
                    value={density}
                    onChange={(v) => setDensity(v as Density)}
                  />
                </Row>
              </Group>

              <Group title="Output">
                <Row label="Output folder" desc="Where SRT files are written">
                  <PathPicker
                    value={settings.outputDir}
                    onChange={(v) => setSetting("outputDir", v)}
                  />
                </Row>
                <Row label="Replace original" desc={NOT_WIRED_NOTE} compact>
                  <Toggle
                    value={settings.replaceOriginal}
                    onChange={(v) => setSetting("replaceOriginal", v)}
                  />
                </Row>
                <Row label="Sidecar .srt next to media" desc={NOT_WIRED_NOTE} compact>
                  <Toggle
                    value={settings.sidecarSrt}
                    onChange={(v) => setSetting("sidecarSrt", v)}
                  />
                </Row>
                <Row label="Embed soft track" desc={NOT_WIRED_NOTE} compact>
                  <Toggle
                    value={settings.embed}
                    onChange={(v) => setSetting("embed", v)}
                  />
                </Row>
                <Row label="Burn into video" desc={NOT_WIRED_NOTE} compact>
                  <Toggle
                    value={settings.burn}
                    onChange={(v) => setSetting("burn", v)}
                  />
                </Row>
              </Group>

              <Group title="Subtitle track">
                <Row label="Style preset">
                  <Seg
                    options={[
                      { value: "netflix", label: "Netflix" },
                      { value: "default", label: "Default" },
                    ]}
                    value={settings.style}
                    onChange={(v) => setSetting("style", v as "netflix" | "default")}
                  />
                </Row>
                <Row label="Soft-embed method" desc="Auto, ffmpeg, or mkvmerge">
                  <Seg
                    options={[
                      { value: "auto", label: "Auto" },
                      { value: "always", label: "Always" },
                      { value: "never", label: "Never" },
                    ]}
                    value={settings.softEmbed}
                    onChange={(v) =>
                      setSetting("softEmbed", v as "auto" | "always" | "never")
                    }
                  />
                </Row>
                <Row label="Track title">
                  <Text
                    value={settings.trackTitle}
                    onChange={(v) => setSetting("trackTitle", v)}
                  />
                </Row>
                <Row label="Track language" desc="ISO 639-2/B (3-letter)">
                  <Text
                    value={settings.trackLang}
                    onChange={(v) => setSetting("trackLang", v)}
                    mono
                  />
                </Row>
                <Row label="Default track" compact>
                  <Toggle
                    value={settings.defaultTrack}
                    onChange={(v) => setSetting("defaultTrack", v)}
                  />
                </Row>
                <Row label="Forced track" compact>
                  <Toggle
                    value={settings.forcedTrack}
                    onChange={(v) => setSetting("forcedTrack", v)}
                  />
                </Row>
              </Group>
            </div>
          )}

          {tab === "performance" && (
            <div className="set-pane">
              <Group title="Compute">
                <Row label="Device">
                  <Seg
                    options={[
                      { value: "auto", label: "Auto" },
                      { value: "gpu", label: "GPU" },
                      { value: "cpu", label: "CPU" },
                    ]}
                    value={settings.device}
                    onChange={(v) =>
                      setSetting("device", v as "auto" | "gpu" | "cpu")
                    }
                  />
                </Row>
                <Row label="Prefer GPU" desc="Try CUDA first, fall back to CPU on failure" compact>
                  <Toggle
                    value={settings.preferGpu}
                    onChange={(v) => setSetting("preferGpu", v)}
                  />
                </Row>
                <Row label="GPU budget" desc="Maximum percent of VRAM the worker may use">
                  <NumInput
                    value={settings.gpuPct}
                    onChange={(v) => setSetting("gpuPct", v)}
                    min={10}
                    max={100}
                    step={5}
                  />
                </Row>
                <Row label="Force float32" desc="Disable mixed-precision; slower but bit-exact" compact>
                  <Toggle
                    value={settings.fp32}
                    onChange={(v) => setSetting("fp32", v)}
                  />
                </Row>
                <Row label="Free GPU when idle" compact>
                  <Toggle
                    value={settings.freeGpuOnStop}
                    onChange={(v) => setSetting("freeGpuOnStop", v)}
                  />
                </Row>
              </Group>

              <Group title="Vocal separation">
                <Row label="Backend">
                  <Seg
                    options={[
                      { value: "fv4", label: "FV4" },
                      { value: "demucs", label: "Demucs" },
                      { value: "off", label: "Off" },
                    ]}
                    value={settings.sep}
                    onChange={(v) =>
                      setSetting("sep", v as "fv4" | "demucs" | "off")
                    }
                  />
                </Row>
                <Row label="Sample rate (Hz)">
                  <NumInput
                    value={settings.sepHz}
                    onChange={(v) => setSetting("sepHz", v)}
                    min={16000}
                    max={48000}
                    step={100}
                    width={140}
                  />
                </Row>
                <Row label="Prefer center channel" desc="Use FC stream when available" compact>
                  <Toggle
                    value={settings.preferCenter}
                    onChange={(v) => setSetting("preferCenter", v)}
                  />
                </Row>
                <Row label="Allow untagged English" desc="Treat untagged streams as English-eligible" compact>
                  <Toggle
                    value={settings.allowUntaggedEnglish}
                    onChange={(v) => setSetting("allowUntaggedEnglish", v)}
                  />
                </Row>
              </Group>

              <Group title="ASR">
                <Row label="Engine">
                  <Seg
                    options={[
                      { value: "parakeet", label: "Parakeet" },
                      { value: "whisper", label: "Whisper" },
                    ]}
                    value={settings.engine}
                    onChange={(v) =>
                      setSetting("engine", v as "parakeet" | "whisper")
                    }
                  />
                </Row>
                <Row label="Model" desc="HuggingFace name or local path">
                  <Text
                    value={settings.asrModel}
                    onChange={(v) => setSetting("asrModel", v)}
                    mono
                  />
                </Row>
                <Row label="Language" desc="ISO 639-1 (2-letter)">
                  <Text
                    value={settings.language}
                    onChange={(v) => setSetting("language", v)}
                    mono
                  />
                </Row>
                <Row label="Local-attn left" desc="Parakeet rel-pos window (frames)">
                  <NumInput
                    value={settings.attnLeft}
                    onChange={(v) => setSetting("attnLeft", v)}
                    min={0}
                    max={4096}
                    step={64}
                  />
                </Row>
                <Row label="Local-attn right">
                  <NumInput
                    value={settings.attnRight}
                    onChange={(v) => setSetting("attnRight", v)}
                    min={0}
                    max={4096}
                    step={64}
                  />
                </Row>
                <Row label="Subsampling chunk factor" desc="0 disables chunking">
                  <NumInput
                    value={settings.subsamplingChunkFactor}
                    onChange={(v) =>
                      setSetting("subsamplingChunkFactor", v)
                    }
                    min={0}
                    max={32}
                    step={1}
                  />
                </Row>
              </Group>
            </div>
          )}

          {tab === "advanced" && (
            <div className="set-pane">
              <Group title="Paths">
                <Row label="Temp folder" desc="Working directory for separation + ASR runs">
                  <PathPicker
                    value={settings.tempDir}
                    onChange={(v) => setSetting("tempDir", v)}
                  />
                </Row>
                <Row label="FV4 config" desc="MelBand Roformer YAML">
                  <Text
                    value={settings.fv4Cfg}
                    onChange={(v) => setSetting("fv4Cfg", v)}
                    mono
                  />
                </Row>
                <Row label="FV4 checkpoint">
                  <Text
                    value={settings.fv4Ckpt}
                    onChange={(v) => setSetting("fv4Ckpt", v)}
                    mono
                  />
                </Row>
              </Group>

              <Group title="FFmpeg">
                <Row label="Audio extraction">
                  <Select
                    value={settings.extract}
                    onChange={(v) =>
                      setSetting("extract", v as "center" | "stereo_mix" | "dual_mono_center")
                    }
                    options={[
                      { value: "center", label: "Center channel only" },
                      { value: "stereo_mix", label: "Stereo mix" },
                      { value: "dual_mono_center", label: "Dual mono (center + fallback)" },
                    ]}
                  />
                </Row>
                <Row label="Filter chain" desc="Applied to extracted audio before separation">
                  <textarea
                    className="input wide mono"
                    rows={3}
                    value={settings.filterChain}
                    onChange={(e) => setSetting("filterChain", e.target.value)}
                  />
                </Row>
              </Group>

              <Group title="Gemini correction">
                <Row label="Enable Gemini pass" desc="Send transcript to Gemini for grammar/style fixes" compact>
                  <Toggle
                    value={settings.geminiEnabled}
                    onChange={(v) => setSetting("geminiEnabled", v)}
                  />
                </Row>
                <Row label="Model">
                  <Text
                    value={settings.geminiModel}
                    onChange={(v) => setSetting("geminiModel", v)}
                    mono
                  />
                </Row>
                <Row label="API key" desc="Stored locally; never transmitted except to Google">
                  <input
                    type="password"
                    className="input wide mono"
                    value={settings.geminiKey}
                    placeholder="AIza…"
                    onChange={(e) => setSetting("geminiKey", e.target.value)}
                  />
                </Row>
              </Group>

              <Group title="Integrations">
                <Row label="Sonarr custom-script hook" desc={NOT_WIRED_NOTE} compact>
                  <Toggle
                    value={settings.sonarr}
                    onChange={(v) => setSetting("sonarr", v)}
                  />
                </Row>
              </Group>

              <Group title="Debug">
                <Row label="Dump word timestamps" desc="Write a per-word JSON next to the SRT" compact>
                  <Toggle
                    value={settings.dumpWords}
                    onChange={(v) => setSetting("dumpWords", v)}
                  />
                </Row>
              </Group>
            </div>
          )}
        </div>

        <footer>
          <button
            className="btn btn-ghost"
            onClick={() => useUi.getState().resetSettings()}
            title="Reset all settings to factory defaults (theme + queue layout preserved)"
          >
            Reset to defaults
          </button>
          <button className="btn btn-primary" onClick={close}>
            Done
          </button>
        </footer>
      </div>
    </>
  );
};
