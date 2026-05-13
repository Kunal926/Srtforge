import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ActiveDetail } from "./components/ActiveDetail";
import { BGMView } from "./components/BGM";
import { EmptyState } from "./components/EmptyState";
import { HistoryView } from "./components/History";
import { NormalizeView } from "./components/Normalize";
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
import { WatchView } from "./components/Watch";
import { I } from "./icons";
import { EMPTY_VALUE, formatDuration, formatTotalDuration } from "./lib/format";
import { useQueueEtaSnapshot } from "./lib/queueEta";
import {
  enqueue,
  gpuTelemetry,
  onWorkerEvent,
  pickFiles,
  pickFolder,
  probeFile,
  restartWorker,
  stopCurrentJob,
} from "./lib/tauri";
import {
  buildWorkerConfig,
  buildRunSettingsSummary,
  computeOutputPath,
  computeSidecarOutputPath,
} from "./lib/workerConfig";
import { useUi } from "./store";
import type { GpuTelemetry } from "./types";

const GPU_TELEMETRY_IDLE_INTERVAL_MS = 2000;

const sameTelemetryValue = <T,>(a: T | null | undefined, b: T | null | undefined) =>
  (a ?? null) === (b ?? null);

const sameGpuTelemetry = (a: GpuTelemetry, b: GpuTelemetry) =>
  a.available === b.available &&
  sameTelemetryValue(a.name, b.name) &&
  sameTelemetryValue(a.utilization_pct, b.utilization_pct) &&
  sameTelemetryValue(a.memory_used_mb, b.memory_used_mb) &&
  sameTelemetryValue(a.memory_total_mb, b.memory_total_mb) &&
  sameTelemetryValue(a.error, b.error);

const unavailableGpuTelemetry = (error: unknown): GpuTelemetry => ({
  available: false,
  error: error instanceof Error ? error.message : String(error),
});

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
  const queuePaused = useUi((s) => s.queuePaused);
  const setSettingsOpen = useUi((s) => s.setSettingsOpen);
  const removeChecked = useUi((s) => s.removeChecked);
  const clearQueue = useUi((s) => s.clearQueue);
  const clearHistory = useUi((s) => s.clearHistory);
  const setRunning = useUi((s) => s.setRunning);
  const setQueuePaused = useUi((s) => s.setQueuePaused);
  const setLayout = useUi((s) => s.setLayout);
  const showToast = useUi((s) => s.showToast);
  const handleWorkerEvent = useUi((s) => s.handleWorkerEvent);
  const settings = useUi((s) => s.settings);
  const logs = useUi((s) => s.logs);
  const toasts = useUi((s) => s.toasts);
  const selectedId = useUi((s) => s.selectedId);

  const [over, setOver] = useState(false);
  const [starting, setStarting] = useState(false);
  const [gpuState, setGpuState] = useState<GpuTelemetry>({ available: false });
  const gpuStateRef = useRef<GpuTelemetry>({ available: false });
  const gpuTelemetryInFlight = useRef(false);
  const gpuTelemetryMounted = useRef(true);
  const gpuPerformanceMode =
    settings.gpuPerformanceMode && files.some((f) => f.status === "processing");

  // Apply theme, density, and active-GPU-job attributes on root.
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.setAttribute("data-density", density);
    document.documentElement.setAttribute(
      "data-gpu-performance",
      gpuPerformanceMode ? "active" : "idle",
    );
  }, [theme, density, gpuPerformanceMode]);

  // Subscribe to worker events for the lifetime of the app.
  useEffect(() => {
    gpuTelemetryMounted.current = true;
    return () => {
      gpuTelemetryMounted.current = false;
    };
  }, []);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    onWorkerEvent(handleWorkerEvent).then((u) => {
      unlisten = u;
    });
    return () => {
      unlisten?.();
    };
  }, [handleWorkerEvent]);

  const pollGpuTelemetry = useCallback(() => {
    if (document.visibilityState !== "visible" || gpuTelemetryInFlight.current) {
      return;
    }
    gpuTelemetryInFlight.current = true;
    gpuTelemetry()
      .then((telemetry) => {
        if (!gpuTelemetryMounted.current) return;
        if (sameGpuTelemetry(gpuStateRef.current, telemetry)) return;
        gpuStateRef.current = telemetry;
        setGpuState(telemetry);
      })
      .catch((error: unknown) => {
        const telemetry = unavailableGpuTelemetry(error);
        if (!gpuTelemetryMounted.current) return;
        if (sameGpuTelemetry(gpuStateRef.current, telemetry)) return;
        gpuStateRef.current = telemetry;
        setGpuState(telemetry);
      })
      .finally(() => {
        gpuTelemetryInFlight.current = false;
      });
  }, []);

  useEffect(() => {
    if (gpuPerformanceMode) return;
    let cancelled = false;
    const poll = () => {
      if (!cancelled) pollGpuTelemetry();
    };
    poll();
    const interval = window.setInterval(poll, GPU_TELEMETRY_IDLE_INTERVAL_MS);
    document.addEventListener("visibilitychange", poll);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [gpuPerformanceMode, pollGpuTelemetry]);

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
  const hasQueued = files.some((f) => f.status === "queued");
  const hasProcessing = files.some((f) => f.status === "processing");

  const activeFile =
    files.find((f) => f.status === "processing") ??
    files.find((f) => f.id === selectedId);

  const totalSec = files.reduce((s, f) => s + f.durationSec, 0);
  const totalDurStr = formatTotalDuration(totalSec);

  const queueEta = useQueueEtaSnapshot(files);
  const currentRunSettings = useMemo(
    () => buildRunSettingsSummary(settings),
    [settings],
  );
  const activePlannedOutputPath = useMemo(() => {
    if (!activeFile) return undefined;
    if (activeFile.outputPath || activeFile.plannedOutputPath) {
      return activeFile.outputPath ?? activeFile.plannedOutputPath;
    }
    return settings.sidecarSrt
      ? computeSidecarOutputPath(activeFile.path) ?? undefined
      : computeOutputPath(activeFile.path, settings.outputDir) ?? undefined;
  }, [activeFile, settings.outputDir, settings.sidecarSrt]);

  const gpuUsed = gpuState.available ? gpuState.memory_used_mb ?? 0 : 0;
  const gpuTotal = gpuState.available ? gpuState.memory_total_mb ?? 0 : 0;
  const vramPct =
    gpuState.available && gpuTotal > 0
      ? Math.max(0, Math.min(100, Math.round((gpuUsed / gpuTotal) * 100)))
      : 0;
  const vramLabel =
    gpuState.available && gpuTotal > 0
      ? `${(gpuUsed / 1024).toFixed(1)} / ${(gpuTotal / 1024).toFixed(1)} GB`
      : EMPTY_VALUE;
  const deviceLabel =
    settings.device === "cpu"
      ? "CPU"
      : gpuState.available && gpuState.name
        ? gpuState.name
        : "GPU";

  const enqueuePaths = useUi((s) => s.enqueuePaths);
  const markSending = useUi((s) => s.markSending);
  const markDispatchFailed = useUi((s) => s.markDispatchFailed);
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
      showToast(`Queued ${picks.length} file${picks.length === 1 ? "" : "s"}`);
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
      showToast("Queued folder");
    } catch (e) {
      showToast(`Folder add failed: ${e}`);
    }
  };

  const onStart = async () => {
    if (!hasQueued) {
      showToast("Add files before starting");
      return;
    }
    setStarting(true);
    setQueuePaused(false);
    setRunning(false);
    try {
      await restartWorker();
      setRunning(true);
    } catch (e) {
      showToast(`Start failed: ${e}`);
    } finally {
      setStarting(false);
    }
  };

  const onStop = async () => {
    setRunning(false);
    setQueuePaused(false);
    try {
      await stopCurrentJob({ freeGpuOnStop: settings.freeGpuOnStop });
      showToast("Stopped current job");
    } catch (e) {
      showToast(`Stop failed: ${e}`);
    }
  };

  const onQueuePrimary = async () => {
    if (queuePaused) {
      setQueuePaused(false);
      if (hasQueued || hasProcessing) {
        setRunning(true);
      }
      showToast("Queue resumed");
      return;
    }
    if (running) {
      setQueuePaused(true);
      showToast(hasProcessing ? "Queue paused after current job" : "Queue paused");
      return;
    }
    await onStart();
  };

  // Pump: send one queued file to the worker at a time when running.
  // Triggers on every state change that could unblock dispatch.
  useEffect(() => {
    if (!running || queuePaused) return;
    const hasInflight = files.some((f) => f.status === "processing");
    if (hasInflight) return;
    const next = files.find((f) => f.status === "queued");
    if (!next) return;
    const output = settings.sidecarSrt
      ? computeSidecarOutputPath(next.path) ?? undefined
      : computeOutputPath(next.path, settings.outputDir) ?? undefined;
    markSending(next.id, currentRunSettings, output);
    const cfg = buildWorkerConfig(settings);
    enqueue(next.path, cfg, { id: next.id, output }).catch((e: unknown) => {
      const message = e instanceof Error ? e.message : String(e);
      markDispatchFailed(next.id, message);
      showToast(`Failed to dispatch: ${message}`);
    });
  }, [files, running, queuePaused, settings, currentRunSettings, markSending, markDispatchFailed, showToast]);

  useEffect(() => {
    if (!running) return;
    const hasWork = files.some((f) => f.status === "queued" || f.status === "processing");
    if (!hasWork) {
      setRunning(false);
      setQueuePaused(false);
      restartWorker().catch((e) => showToast(`Worker reset failed: ${e}`));
    }
  }, [files, running, setRunning, setQueuePaused, showToast]);

  const showQueueShell = active === "queue" || active === "active" || active === "history";
  const status: "idle" | "paused" | "running" = queuePaused ? "paused" : counts.active ? "running" : "idle";
  const queueActionLabel = queuePaused ? "Resume" : running ? "Pause" : starting ? "Starting" : "Start";
  const queueActionIcon = queuePaused || !running ? <I.Play size={12} /> : <I.Pause size={12} />;
  const queueActionDisabled = starting || (!running && !queuePaused && !hasQueued);

  return (
    <div
      className={`win-shell ${gpuPerformanceMode ? "gpu-max-mode" : ""}`}
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
          device={deviceLabel}
          gpuPct={vramPct}
          vram={vramLabel}
        />

        <div className="main">
          {showQueueShell && (
            <div className="toolbar toolbar-rich">
              <div className="title-block title-block-rich">
                <div className="tb-medallion" aria-hidden="true">
                  {active === "queue" && <I.Inbox size={20} />}
                  {active === "active" && <I.Pulse size={20} />}
                  {active === "history" && <I.Archive size={20} />}
                  {((active === "queue" && running && !queuePaused && counts.queue > 0) ||
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
                  <>
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
                        placeholder="Search files..."
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
                    <button
                      className="btn btn-primary"
                      onClick={() => void onQueuePrimary()}
                      disabled={queueActionDisabled}
                    >
                      {queueActionIcon} {queueActionLabel}
                    </button>
                  </>
                )}
                {active === "history" && (
                  <>
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
                        placeholder="Search files..."
                        style={{ paddingLeft: 28, width: 200, height: 32 }}
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                      />
                    </div>
                    <button className="btn btn-danger" onClick={clearHistory}>
                      <I.Trash size={14} /> Clear history
                    </button>
                  </>
                )}
                {active === "active" && hasProcessing && (
                  <button className="btn btn-danger" onClick={onStop}>
                    <I.Stop size={12} /> Stop
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
                  {layout === "hybrid" && (
                    <QueueTable files={visible} etaLabels={queueEta.etaById} />
                  )}
                  {layout === "card" && (
                    <QueueCards
                      files={visible}
                      quiet={gpuPerformanceMode}
                      etaLabels={queueEta.etaById}
                    />
                  )}
                </>
              ))}

            {active === "active" &&
              (activeFile && activeFile.status === "processing" ? (
                <ActiveDetail
                  file={activeFile}
                  paused={false}
                  logs={logs}
                  fallbackRunSettings={currentRunSettings}
                  outputPath={activePlannedOutputPath}
                  expanded
                  quiet={gpuPerformanceMode}
                />
              ) : (
                <EmptyState
                  icon={<I.Wave size={32} />}
                  title="No active job"
                  body="Start the queue to see the live transcription view here."
                  cta={{
                    label: "Open queue",
                    onClick: () => {
                      setActive("queue");
                    },
                  }}
                />
              ))}

            {active === "history" && <HistoryView files={visible} />}

            {active === "watch" && <WatchView />}
            {active === "normalize" && <NormalizeView />}
            {active === "bgm" && <BGMView />}
          </div>

          <StatusBar
            runId={"local"}
            queueEta={queueEta}
            doneCount={files.filter((f) => f.status === "done").length}
            totalCount={files.length}
            ffmpeg={"6.1"}
            model={"parakeet-tdt-0.6b-v2"}
            status={status}
          />
        </div>
      </div>

      <SettingsDrawer />

      <div className="toast-stack" aria-live="polite">
        {toasts.map((toast) => (
          <div className="toast show" key={toast.id}>
            <span
              style={{ width: 6, height: 6, borderRadius: 999, background: "var(--accent)" }}
            />
            {toast.msg}
          </div>
        ))}
      </div>
    </div>
  );
};
