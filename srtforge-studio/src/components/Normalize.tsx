// Normalize tool — standalone audio transcode/normalize. Worker action
// `normalize` runs srtforge.ffmpeg.FFmpegTooling.normalize_audio for each
// queued file and emits the same job_started / asset_written /
// job_completed vocabulary as a transcribe job, so progress lights up
// the same row UI.

import { useState } from "react";

import { I } from "../icons";
import { formatDuration } from "../lib/format";
import { normalize, pickFiles, pickFolder, probeFile } from "../lib/tauri";
import { useUi } from "../store";
import type { QueueFile } from "../types";
import { OutputSplitButton, StatusPill } from "./Queue";

interface NormalizeOptions {
  out_format: "wav" | "flac" | "mp3";
  bit_depth: 16 | 24 | 32;
  sample_rate: 16000 | 22050 | 44100 | 48000;
  channels: 1 | 2;
  loudness: boolean;
  filter_chain: string;
  output: string;
}

const DEFAULT_OPTS: NormalizeOptions = {
  out_format: "wav",
  bit_depth: 16,
  sample_rate: 16000,
  channels: 1,
  loudness: true,
  filter_chain:
    "highpass=f=60,lowpass=f=10000,aformat=sample_fmts=flt,aresample=resampler=soxr:osf=flt:osr=16000",
  output: "",
};

const filename = (p: string) => p.split(/[\\/]/).pop() ?? p;
const fileExt = (n: string) => (n.split(".").pop() ?? "").toUpperCase();

export const NormalizeView = () => {
  const files = useUi((s) => s.files);
  const enqueuePaths = useUi((s) => s.enqueuePaths);
  const updateFileMeta = useUi((s) => s.updateFileMeta);
  const markSending = useUi((s) => s.markSending);
  const showToast = useUi((s) => s.showToast);
  const settings = useUi((s) => s.settings);

  const [opts, setOpts] = useState<NormalizeOptions>({
    ...DEFAULT_OPTS,
    output: settings.outputDir || DEFAULT_OPTS.output,
  });

  const setOpt = <K extends keyof NormalizeOptions>(k: K, v: NormalizeOptions[K]) =>
    setOpts((s) => ({ ...s, [k]: v }));

  // Items we own = files added through this tool. Cheap heuristic: any
  // QueueFile we created sits in the global store; we surface every file
  // here. Future polish: scope by a tag.
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

  const onAddFolder = async () => {
    try {
      const picked = await pickFolder();
      if (!picked) return;
      const ids = enqueuePaths([picked]);
      ids.forEach((id) => updateFileMeta(id, { duration: "—" }));
    } catch (e) {
      showToast(`Folder picker failed: ${e}`);
    }
  };

  const onRun = () => {
    const queued = items.filter((f) => f.status === "queued");
    if (queued.length === 0) return;
    queued.forEach((file) => {
      markSending(file.id);
      normalize(
        file.path,
        {
          format: opts.out_format,
          bit_depth: opts.bit_depth,
          sample_rate: opts.sample_rate,
          channels: opts.channels,
          loudness: opts.loudness,
          filter_chain: opts.filter_chain,
          output: opts.output || null,
        },
        { id: file.id },
      ).catch((e) => showToast(`Normalize dispatch failed: ${e}`));
    });
  };

  const onPickOutput = async () => {
    try {
      const picked = await pickFolder();
      if (picked) setOpt("output", picked);
    } catch (e) {
      showToast(`Folder picker failed: ${e}`);
    }
  };

  return (
    <div className="tool-pane">
      <div className="tool-header">
        <div className="tool-icon">
          <I.Sliders size={20} />
        </div>
        <div>
          <h2>Normalize audio</h2>
          <p>
            Extract a clean WAV (or FLAC / MP3) from any video or audio file.
            Same FFmpeg pipeline Srtforge uses before FV4 — no transcription.
          </p>
        </div>
      </div>

      <div className="tool-drop">
        <div className="td-icon">
          <I.Drop size={28} />
        </div>
        <div className="td-text">
          <h3>Drop files to normalize</h3>
          <p>.mkv · .mp4 · .mov · .wav · .flac · .m4a — folders are scanned recursively.</p>
        </div>
        <div className="td-actions">
          <button className="btn btn-primary" onClick={onAddFiles}>
            <I.Plus size={14} /> Add files
          </button>
          <button className="btn" onClick={onAddFolder}>
            <I.FolderOpen size={14} /> Add folder
          </button>
        </div>
      </div>

      <div className="tool-grid">
        <div className="tool-form panel">
          <div className="panel-h">Output</div>
          <div className="form-row">
            <label>Format</label>
            <div className="seg">
              {(["wav", "flac", "mp3"] as const).map((f) => (
                <button
                  key={f}
                  className={opts.out_format === f ? "active" : ""}
                  onClick={() => setOpt("out_format", f)}
                >
                  {f.toUpperCase()}
                </button>
              ))}
            </div>
          </div>
          <div className="form-row">
            <label>Bit depth</label>
            <div className="seg">
              {([16, 24, 32] as const).map((b) => (
                <button
                  key={b}
                  className={opts.bit_depth === b ? "active" : ""}
                  onClick={() => setOpt("bit_depth", b)}
                >
                  {b === 32 ? "32-bit float" : `${b}-bit`}
                </button>
              ))}
            </div>
          </div>
          <div className="form-row">
            <label>Sample rate</label>
            <div className="seg">
              {([16000, 22050, 44100, 48000] as const).map((sr) => (
                <button
                  key={sr}
                  className={opts.sample_rate === sr ? "active" : ""}
                  onClick={() => setOpt("sample_rate", sr)}
                >
                  {sr / 1000} kHz
                </button>
              ))}
            </div>
          </div>
          <div className="form-row">
            <label>Channels</label>
            <div className="seg">
              <button
                className={opts.channels === 1 ? "active" : ""}
                onClick={() => setOpt("channels", 1)}
              >
                Mono
              </button>
              <button
                className={opts.channels === 2 ? "active" : ""}
                onClick={() => setOpt("channels", 2)}
              >
                Stereo
              </button>
            </div>
          </div>
          <div className="form-row">
            <label>Loudness normalize</label>
            <div
              className={`toggle ${opts.loudness ? "on" : ""}`}
              onClick={() => setOpt("loudness", !opts.loudness)}
              role="switch"
              aria-checked={opts.loudness}
            />
          </div>
          <div className="form-row vstack">
            <label>
              FFmpeg filter chain
              <span className="hint mono">default = Srtforge pre-FV4 chain</span>
            </label>
            <textarea
              className="input mono wide"
              rows={3}
              value={opts.filter_chain}
              onChange={(e) => setOpt("filter_chain", e.target.value)}
            />
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
          </div>
          <div className="form-actions">
            <button className="btn btn-ghost" onClick={() => setOpts(DEFAULT_OPTS)}>
              Reset to defaults
            </button>
            <span style={{ flex: 1 }} />
            <button
              className="btn btn-primary"
              onClick={onRun}
              disabled={items.filter((f) => f.status === "queued").length === 0}
            >
              <I.Play size={13} /> Run normalize on{" "}
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
          {items.length === 0 && (
            <div className="tool-empty">No files added yet.</div>
          )}
          {items.map((it: QueueFile) => (
            <div key={it.id} className="tool-item">
              <div className="ti-name">
                <span className="ext">{fileExt(it.name)}</span>
                <div>
                  <div className="filename">{it.name}</div>
                  <div className="meta">
                    {it.duration} · {it.channels} ch · → {opts.out_format}{" "}
                    {opts.bit_depth}-bit · {opts.sample_rate / 1000} kHz ·{" "}
                    {opts.channels === 1 ? "mono" : "stereo"}
                  </div>
                </div>
              </div>
              <StatusPill s={it.status} progress={it.progress} />
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

export const _filename = filename;
