import { useEffect, useMemo, useState } from "react";

import { ActiveDetail } from "./components/ActiveDetail";
import { EmptyState } from "./components/EmptyState";
import { HistoryView } from "./components/History";
import {
  DropZone,
  QueueCards,
  QueueEmpty,
  QueueTable,
} from "./components/Queue";
import { SettingsDrawer } from "./components/SettingsDrawer";
import { Sidebar } from "./components/Sidebar";
import { StatusBar } from "./components/StatusBar";
import { TitleBar } from "./components/TitleBar";
import { I } from "./icons";
import { formatDuration, formatTotalDuration } from "./lib/format";
import {
  clearGpuCache,
  enqueue,
  onWorkerEvent,
  pickFiles,
  pickFolder,
  probeFile,
} from "./lib/tauri";
import { buildWorkerConfig, computeOutputPath } from "./lib/workerConfig";
import { useUi } from "./store";

export const App = () => {
  const theme = useUi((s) => s.theme);
  const density = useUi((s) => s.density);
  const layout = useUi((s) => s.layout);

  const active = useUi((s) => s.active);
  const setActive = useUi((s) => s.setActive);
  const files = useUi((s) => s.files);
  const checked = useUi((s) => s.checked);
  const search = useUi((s) => s.search);
  const setSearch = useUi((s) => s.setSearch);
  const running = useUi((s) => s.running);
  const paused = useUi((s) => s.paused);
  const setSettingsOpen = useUi((s) => s.setSettingsOpen);
  const removeChecked = useUi((s) => s.removeChecked);
  const clearQueue = useUi((s) => s.clearQueue);
  const setRunning = useUi((s) => s.setRunning);
  const setPaused = useUi((s) => s.setPaused);
  const setLayout = useUi((s) => s.setLayout);
  const showToast = useUi((s) => s.showToast);
  const handleWorkerEvent = useUi((s) => s.handleWorkerEvent);
  const settings = useUi((s) => s.settings);
  const logs = useUi((s) => s.logs);
  const toast = useUi((s) => s.toast);
  const selectedId = useUi((s) => s.selectedId);

  const [over, setOver] = useState(false);

  // Apply theme + density attributes on root.
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.setAttribute("data-density", density);
  }, [theme, density]);

  // Subscribe to worker events for the lifetime of the app.
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    onWorkerEvent(handleWorkerEvent).then((u) => {
      unlisten = u;
    });
    return () => {
      unlisten?.();
    };
  }, [handleWorkerEvent]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return files;
    return files.filter((f) => f.name.toLowerCase().includes(q));
  }, [files, search]);

  const visible = useMemo(() => {
    if (active === "queue") return filtered.filter((f) => f.status !== "done" && f.status !== "error");
    if (active === "history") return filtered.filter((f) => f.status === "done" || f.status === "error");
    return filtered;
  }, [active, filtered]);

  const counts = {
    queue: filtered.filter((f) => f.status === "queued" || f.status === "processing").length,
    active: filtered.find((f) => f.status === "processing") ? 1 : 0,
    done: filtered.filter((f) => f.status === "done" || f.status === "error").length,
  };

  const activeFile =
    files.find((f) => f.status === "processing") ??
    files.find((f) => f.id === selectedId);

  const totalSec = files.reduce((s, f) => s + f.durationSec, 0);
  const totalDurStr = formatTotalDuration(totalSec);

  const remainingSec = files
    .filter((f) => f.status !== "done" && f.status !== "error")
    .reduce((s, f) => s + (1 - f.progress) * f.durationSec * 0.22, 0);
  const queueEtaStr = formatTotalDuration(remainingSec);

  const enqueuePaths = useUi((s) => s.enqueuePaths);
  const markSending = useUi((s) => s.markSending);
  const updateFileMeta = useUi((s) => s.updateFileMeta);

  // Fire ffprobe per id; tolerate failures silently — the row keeps its
  // "—" placeholders and the worker still runs the job. We do this in
  // parallel because each call is a quick exec and they're independent.
  const probeAll = (ids: string[], paths: string[]) => {
    ids.forEach((id, i) => {
      const path = paths[i];
      probeFile(path)
        .then((p) =>
          updateFileMeta(id, {
            duration: formatDuration(p.duration_sec),
            durationSec: p.duration_sec,
            sampleRate: Math.round(p.sample_rate / 1000),
            channels: p.channels,
            fps: p.fps,
            codec: p.codec,
          }),
        )
        .catch(() => {});
    });
  };

  const onAddFiles = async () => {
    try {
      const picks = await pickFiles();
      if (picks.length === 0) return;
      const ids = enqueuePaths(picks);
      probeAll(ids, picks);
      // First add → start running by default; the user can Pause from here.
      setRunning(true);
      setPaused(false);
    } catch (e) {
      showToast(`File picker failed: ${e}`);
    }
  };

  const onAddFolder = async () => {
    try {
      const picked = await pickFolder();
      if (!picked) return;
      const ids = enqueuePaths([picked]);
      probeAll(ids, [picked]);
      setRunning(true);
      setPaused(false);
    } catch (e) {
      showToast(`Folder add failed: ${e}`);
    }
  };

  const onStart = () => {
    setRunning(true);
    setPaused(false);
  };

  const onPause = () => {
    // Client-side pause: the pump stops dispatching new jobs to the worker.
    // The currently-processing job runs to completion (the Python worker
    // doesn't yet support cancel/pause mid-job).
    setPaused(true);
    showToast("Pausing — current job will finish, then queue holds");
    // Best-effort GPU cache flush, gated on the user setting. Fires
    // immediately even if a job is mid-flight; torch.cuda.empty_cache()
    // only releases the unused-but-cached blocks, so it's safe.
    if (useUi.getState().settings.freeGpuOnStop) {
      clearGpuCache().catch((e) => showToast(`GPU cache clear failed: ${e}`));
    }
  };

  // Pump: send one queued file to the worker at a time when running.
  // Triggers on every state change that could unblock dispatch.
  useEffect(() => {
    if (!running || paused) return;
    const hasInflight = files.some((f) => f.status === "processing");
    if (hasInflight) return;
    const next = files.find((f) => f.status === "queued");
    if (!next) return;
    markSending(next.id);
    const cfg = buildWorkerConfig(settings);
    const output = computeOutputPath(next.path, settings.outputDir) ?? undefined;
    enqueue(next.path, cfg, { id: next.id, output }).catch((e) => {
      showToast(`Failed to dispatch: ${e}`);
    });
  }, [files, running, paused, settings, markSending, showToast]);

  const showQueueShell = active === "queue" || active === "active" || active === "history";
  const status: "idle" | "warn" | "running" = paused ? "warn" : counts.active ? "running" : "idle";

  return (
    <div
      className="win-shell"
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        // Tauri exposes dropped paths via webview events; for the MVP we
        // route the user to the file picker.
        onAddFiles();
      }}
    >
      <TitleBar jobName={activeFile?.name ?? "Idle"} />

      <div className="app-layout">
        <Sidebar
          device={settings.device === "cpu" ? "CPU" : "GPU"}
          gpuPct={running && !paused ? 88 : 12}
          vram="6.4 / 8 GB"
        />

        <div className="main">
          {showQueueShell && (
            <div className="toolbar toolbar-rich">
              <div className="title-block title-block-rich">
                <div className="tb-medallion" aria-hidden="true">
                  {active === "queue" && <I.Inbox size={20} />}
                  {active === "active" && <I.Pulse size={20} />}
                  {active === "history" && <I.Archive size={20} />}
                  {((active === "queue" && running && !paused && counts.queue > 0) ||
                    (active === "active" && counts.active > 0)) && (
                    <span className="tb-medallion-pulse" />
                  )}
                </div>
                <div className="tb-text">
                  <h1>
                    {active === "history"
                      ? "History"
                      : active === "active"
                        ? "Active job"
                        : "Transcription queue"}
                  </h1>
                  <p>
                    {files.length} files · total duration{" "}
                    <span className="mono">{totalDurStr}</span> · output →{" "}
                    <span className="mono">{settings.outputDir}</span>
                  </p>
                </div>
              </div>
              <div className="actions">
                {active === "queue" && (
                  <div className="seg" title="Layout">
                    <button
                      className={layout === "card" ? "active" : ""}
                      onClick={() => setLayout("card")}
                      title="Cards"
                    >
                      <I.Grid size={14} />
                    </button>
                    <button
                      className={layout === "hybrid" ? "active" : ""}
                      onClick={() => setLayout("hybrid")}
                      title="Hybrid"
                    >
                      <I.Layers size={14} />
                    </button>
                  </div>
                )}
                <div style={{ position: "relative" }}>
                  <I.Search
                    size={13}
                    style={{
                      position: "absolute",
                      left: 9,
                      top: "50%",
                      transform: "translateY(-50%)",
                      color: "var(--text-3)",
                    }}
                  />
                  <input
                    className="input"
                    placeholder="Search files…"
                    style={{ paddingLeft: 28, width: 200, height: 32 }}
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
                <button
                  className="btn btn-danger"
                  onClick={removeChecked}
                  disabled={checked.size === 0}
                >
                  <I.Trash size={14} /> Remove ({checked.size})
                </button>
                <button className="btn btn-danger" onClick={clearQueue}>
                  Clear queue
                </button>
                {!running ? (
                  <button className="btn btn-primary" onClick={onStart}>
                    <I.Play size={12} /> Start
                  </button>
                ) : paused ? (
                  <button className="btn btn-primary" onClick={onStart}>
                    <I.Play size={12} /> Resume
                  </button>
                ) : (
                  <button className="btn btn-danger" onClick={onPause}>
                    <I.Pause size={12} /> Pause
                  </button>
                )}
                <button
                  className="btn btn-ghost"
                  onClick={() => setSettingsOpen(true)}
                  title="Settings"
                >
                  <I.Settings size={14} />
                </button>
              </div>
            </div>
          )}

          <div className="content">
            {active === "queue" &&
              (visible.length === 0 ? (
                <QueueEmpty over={over} onAdd={onAddFiles} onAddFolder={onAddFolder} />
              ) : (
                <>
                  <DropZone over={over} onAdd={onAddFiles} onAddFolder={onAddFolder} />
                  {layout === "hybrid" && <QueueTable files={visible} />}
                  {layout === "card" && <QueueCards files={visible} />}
                </>
              ))}

            {active === "active" &&
              (activeFile && activeFile.status === "processing" ? (
                <ActiveDetail file={activeFile} paused={paused} logs={logs} expanded />
              ) : (
                <EmptyState
                  icon={<I.Wave size={32} />}
                  title="No active job"
                  body="Start the queue to see the live transcription view here."
                  cta={{
                    label: "Start queue",
                    onClick: () => {
                      onStart();
                      setActive("queue");
                    },
                  }}
                />
              ))}

            {active === "history" && <HistoryView files={visible} />}

            {(active === "watch" || active === "normalize" || active === "bgm") && (
              <EmptyState
                icon={<I.Sliders size={32} />}
                title="Coming soon"
                body="This view isn't part of the MVP. Switch back to the Queue to start transcribing."
                cta={{ label: "Back to Queue", onClick: () => setActive("queue") }}
              />
            )}
          </div>

          <StatusBar
            runId={"local"}
            queueEta={queueEtaStr}
            doneCount={files.filter((f) => f.status === "done").length}
            totalCount={files.length}
            ffmpeg={"6.1"}
            model={"parakeet-tdt-0.6b-v2"}
            status={status}
          />
        </div>
      </div>

      <SettingsDrawer />

      <div className={`toast ${toast ? "show" : ""}`}>
        <span style={{ width: 6, height: 6, borderRadius: 999, background: "var(--accent)" }} />
        {toast}
      </div>
    </div>
  );
};
