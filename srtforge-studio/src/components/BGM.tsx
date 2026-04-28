// BGM separation tool — standalone vocals/instrumental split via FV4
// (MelBand Roformer). Worker action `separate` calls
// srtforge.ffmpeg.FFmpegTooling.isolate_vocals for vocals; instrumental
// is derived via ffmpeg subtraction in srtforge/cli.py:_run_separate_job.

import { useState } from "react";

import { I } from "../icons";
import { formatDuration } from "../lib/format";
import { pickFiles, pickFolder, probeFile, separate } from "../lib/tauri";
import { useUi } from "../store";
import type { QueueFile } from "../types";
import { OutputSplitButton, StatusPill } from "./Queue";

interface BGMOptions {
  model: "fv4" | "fv4_inst";
  vocals: boolean;
  instrumental: boolean;
  order: "vocals_first" | "parallel";
  output: string;
}

const DEFAULT_OPTS: BGMOptions = {
  model: "fv4",
  vocals: true,
  instrumental: true,
  order: "vocals_first",
  output: "",
};

const fileExt = (n: string) => (n.split(".").pop() ?? "").toUpperCase();

export const BGMView = () => {
  const files = useUi((s) => s.files);
  const enqueuePaths = useUi((s) => s.enqueuePaths);
  const updateFileMeta = useUi((s) => s.updateFileMeta);
  const markSending = useUi((s) => s.markSending);
  const showToast = useUi((s) => s.showToast);
  const settings = useUi((s) => s.settings);

  const [opts, setOpts] = useState<BGMOptions>({
    ...DEFAULT_OPTS,
    output: settings.outputDir || DEFAULT_OPTS.output,
  });
  const setOpt = <K extends keyof BGMOptions>(k: K, v: BGMOptions[K]) =>
    setOpts((s) => ({ ...s, [k]: v }));

  const items = files;

  const onAddFiles = async () => {
    try {
      const picks = await pickFiles();
      if (picks.length === 0) return;
      const ids = enqueuePaths(picks);
      ids.forEach((id, i) => {
        const path = picks[i];
        probeFile(path)
          .then((p) =>
            updateFileMeta(id, {
              duration: formatDuration(p.duration_sec),
              durationSec: p.duration_sec,
              sampleRate: Math.round(p.sample_rate / 1000),
              channels: p.channels,
              codec: p.codec,
              fps: p.fps,
            }),
          )
          .catch(() => {});
      });
    } catch (e) {
      showToast(`File picker failed: ${e}`);
    }
  };

  const onPickOutput = async () => {
    try {
      const picked = await pickFolder();
      if (picked) setOpt("output", picked);
    } catch (e) {
      showToast(`Folder picker failed: ${e}`);
    }
  };

  const onRun = () => {
    const stems: string[] = [];
    if (opts.vocals) stems.push("vocals");
    if (opts.instrumental) stems.push("instrumental");
    if (stems.length === 0) {
      showToast("Pick at least one stem (vocals or instrumental).");
      return;
    }
    const queued = items.filter((f) => f.status === "queued");
    if (queued.length === 0) return;
    queued.forEach((file) => {
      markSending(file.id);
      separate(
        file.path,
        {
          stems,
          model: opts.model === "fv4" ? null : opts.model, // null = pipeline default
          output: opts.output || null,
        },
        { id: file.id },
      ).catch((e) => showToast(`Separate dispatch failed: ${e}`));
    });
  };

  return (
    <div className="tool-pane">
      <div className="tool-header">
        <div className="tool-icon">
          <I.Music size={20} />
        </div>
        <div>
          <h2>BGM separation</h2>
          <p>
            Pull vocals out of music or background score using FV4 (MelBand
            Roformer). Outputs <span className="mono">.vocals.wav</span> and
            (optionally) <span className="mono">.instrumental.wav</span>.
          </p>
        </div>
      </div>

      <div className="tool-drop bgm">
        <div className="td-icon">
          <I.Drop size={28} />
        </div>
        <div className="td-text">
          <h3>Drop files to separate</h3>
          <p>
            Best with full mixes (music + dialog) at 44.1 kHz or higher.
            Multi-channel sources are downmixed before FV4.
          </p>
        </div>
        <div className="td-actions">
          <button className="btn btn-primary" onClick={onAddFiles}>
            <I.Plus size={14} /> Add files
          </button>
        </div>
      </div>

      <div className="tool-grid">
        <div className="tool-form panel">
          <div className="panel-h">FV4 separation</div>
          <div className="form-row">
            <label>Model</label>
            <div className="seg">
              <button
                className={opts.model === "fv4" ? "active" : ""}
                onClick={() => setOpt("model", "fv4")}
              >
                FV4 voc_gabox
              </button>
              <button
                className={opts.model === "fv4_inst" ? "active" : ""}
                onClick={() => setOpt("model", "fv4_inst")}
                disabled
                title="Not yet bundled"
              >
                FV4 inst
              </button>
            </div>
          </div>
          <div className="form-row">
            <label>Stems</label>
            <div className="stem-toggles">
              <button
                className={`stem ${opts.vocals ? "on" : ""}`}
                onClick={() => setOpt("vocals", !opts.vocals)}
              >
                <span className="dot" /> Vocals
              </button>
              <button
                className={`stem ${opts.instrumental ? "on" : ""}`}
                onClick={() => setOpt("instrumental", !opts.instrumental)}
              >
                <span className="dot" /> Instrumental
              </button>
            </div>
          </div>
          <div className="form-row">
            <label>Render order</label>
            <div className="seg">
              <button
                className={opts.order === "vocals_first" ? "active" : ""}
                onClick={() => setOpt("order", "vocals_first")}
              >
                Vocals first
              </button>
              <button
                className={opts.order === "parallel" ? "active" : ""}
                onClick={() => setOpt("order", "parallel")}
                disabled
                title="Worker runs sequentially today"
              >
                Parallel
              </button>
            </div>
          </div>
          <div className="form-row vstack">
            <label>Output folder</label>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                className="input mono wide"
                style={{ flex: 1, minWidth: 0 }}
                value={opts.output}
                onChange={(e) => setOpt("output", e.target.value)}
                placeholder="(default: next to source)"
              />
              <button className="btn btn-ghost" onClick={onPickOutput}>
                Browse…
              </button>
            </div>
            <span className="hint mono" style={{ marginTop: 4 }}>
              writes <span className="text-2">{`{name}.vocals.wav`}</span> +{" "}
              <span className="text-2">{`{name}.instrumental.wav`}</span>
            </span>
          </div>
          <div className="form-actions">
            <button
              className="btn btn-ghost"
              onClick={() => setOpts(DEFAULT_OPTS)}
            >
              Reset
            </button>
            <span style={{ flex: 1 }} />
            <button
              className="btn btn-primary"
              onClick={onRun}
              disabled={items.filter((f) => f.status === "queued").length === 0}
            >
              <I.Play size={13} /> Separate{" "}
              {items.filter((f) => f.status === "queued").length} files
            </button>
          </div>
        </div>

        <div className="tool-list panel">
          <div className="panel-h">
            <span>Items</span>
            <span className="dim mono" style={{ fontSize: 11 }}>
              {items.length} files
            </span>
          </div>
          {items.length === 0 && <div className="tool-empty">No files added yet.</div>}
          {items.map((it: QueueFile) => (
            <div key={it.id} className="tool-item bgm">
              <div className="ti-name">
                <span className="ext">{fileExt(it.name)}</span>
                <div>
                  <div className="filename">{it.name}</div>
                  <div className="meta">
                    {it.duration}
                    {it.status === "processing" && (
                      <>
                        {" · "}
                        {Math.round(it.progress * 100)}%
                      </>
                    )}
                    {it.status === "done" && (
                      <>
                        {" · "}
                        {opts.vocals && "vocals"}
                        {opts.vocals && opts.instrumental && " + "}
                        {opts.instrumental && "instrumental"}
                      </>
                    )}
                  </div>
                </div>
              </div>
              {it.status === "done" ? (
                <div className="stem-out">
                  {opts.vocals && (
                    <span className="stem-pill">
                      <span className="dot v" /> vocals.wav
                    </span>
                  )}
                  {opts.instrumental && (
                    <span className="stem-pill">
                      <span className="dot i" /> instrumental.wav
                    </span>
                  )}
                </div>
              ) : (
                <StatusPill s={it.status} progress={it.progress} />
              )}
              <div className="ti-actions">
                {it.status === "done" && it.outputPath && (
                  <OutputSplitButton onLocate={() => {}} />
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
