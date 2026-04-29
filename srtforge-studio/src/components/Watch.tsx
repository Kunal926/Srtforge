// Watch Folders — Sonarr/Radarr/Folder library list. UI + persisted state
// only this round; no actual filesystem watcher or Sonarr webhook listener
// is wired up. The hook banner below explains that explicitly.

import { useState } from "react";

import { I } from "../icons";
import { pickFolder } from "../lib/tauri";
import { useUi } from "../store";
import type { WatchSchedule, WatchSource } from "../types";

interface SourceMap {
  label: string;
  color: string;
}

const SOURCE_MAP: Record<WatchSource, SourceMap> = {
  sonarr: { label: "Sonarr", color: "#2193b0" },
  radarr: { label: "Radarr", color: "#ffc233" },
  folder: { label: "Folder", color: "var(--accent)" },
};

const SourceBadge = ({ source }: { source: WatchSource }) => {
  const s = SOURCE_MAP[source];
  return (
    <span
      className="src-badge"
      style={{ ["--src" as never]: s.color } as React.CSSProperties}
    >
      <span className="src-dot" />
      {s.label}
    </span>
  );
};

const SCHEDULE_LABEL: Record<WatchSchedule, string> = {
  live: "Live",
  "5m": "Every 5m",
  "15m": "Every 15m",
  "1h": "Every 1h",
  manual: "Manual",
};

interface DraftLibrary {
  name: string;
  path: string;
  source: WatchSource;
  schedule: WatchSchedule;
}

const EMPTY_DRAFT: DraftLibrary = {
  name: "",
  path: "",
  source: "sonarr",
  schedule: "live",
};

export const WatchView = () => {
  const libraries = useUi((s) => s.libraries);
  const addLibrary = useUi((s) => s.addLibrary);
  const removeLibrary = useUi((s) => s.removeLibrary);
  const toggleLibraryEnabled = useUi((s) => s.toggleLibraryEnabled);
  const showToast = useUi((s) => s.showToast);

  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<DraftLibrary>(EMPTY_DRAFT);

  const totalItems = libraries.reduce((a, l) => a + (l.itemsCount ?? 0), 0);
  const totalPending = libraries.reduce(
    (a, l) => a + (l.enabled ? l.pendingCount ?? 0 : 0),
    0,
  );
  const enabledCount = libraries.filter((l) => l.enabled).length;

  const onBrowse = async () => {
    try {
      const picked = await pickFolder();
      if (picked) setDraft((d) => ({ ...d, path: picked }));
    } catch (e) {
      showToast(`Folder picker failed: ${e}`);
    }
  };

  const onAdd = () => {
    const trimmedPath = draft.path.trim();
    if (!trimmedPath) return;
    const name =
      draft.name.trim() ||
      trimmedPath.split(/[\\/]/).filter(Boolean).pop() ||
      "Library";
    addLibrary({
      name,
      path: trimmedPath,
      source: draft.source,
      schedule: draft.schedule,
      enabled: true,
      instance: draft.source === "sonarr" ? "Sonarr" : draft.source === "radarr" ? "Radarr" : "Local",
    });
    setAdding(false);
    setDraft(EMPTY_DRAFT);
  };

  return (
    <div className="watch-pane">
      <div className="watch-stats">
        <div className="wstat wstat-hero">
          <div className="wstat-head">
            <span className="wstat-label">Library coverage</span>
            <span className="wstat-trend">
              <I.Antenna size={11} /> watching
            </span>
          </div>
          <div className="wstat-num">
            {enabledCount}
            <span className="of">/ {libraries.length}</span>
          </div>
          <div className="wstat-bar" aria-hidden="true">
            {libraries.map((l, i) => (
              <span
                key={l.id}
                className={`wstat-bar-seg ${l.enabled ? "on" : "off"}`}
                style={{ ["--i" as never]: i } as React.CSSProperties}
                title={`${l.name} — ${l.enabled ? "active" : "paused"}`}
              />
            ))}
          </div>
          <div className="wstat-foot">
            <span className="mono">{enabledCount}</span> active
            <span className="dotsep" />
            <span className="mono">{libraries.length - enabledCount}</span> paused
          </div>
        </div>

        <div className="wstat wstat-tile">
          <div className="wstat-tile-icon">
            <I.Files size={15} />
          </div>
          <div className="wstat-tile-body">
            <div className="wstat-num mono">{totalItems.toLocaleString()}</div>
            <div className="wstat-label">Tracked items</div>
          </div>
        </div>

        <div className={`wstat wstat-tile ${totalPending ? "is-warn" : "is-ok"}`}>
          <div className="wstat-tile-icon">
            {totalPending ? <I.Clock size={15} /> : <I.Check size={15} />}
          </div>
          <div className="wstat-tile-body">
            <div className="wstat-num mono">
              {totalPending}
              {totalPending > 0 && <span className="wstat-pulse" aria-hidden="true" />}
            </div>
            <div className="wstat-label">
              {totalPending ? "Awaiting subs" : "Caught up"}
            </div>
          </div>
        </div>
      </div>

      {/* Sonarr hook status banner — listener not yet implemented */}
      <div className="hook-banner">
        <div className="hb-icon">
          <I.Antenna size={18} />
        </div>
        <div className="hb-text">
          <h4>
            Sonarr webhook
            <span className="hook-state off">
              <span className="hook-dot" />
              offline
            </span>
          </h4>
          <p>
            Listener not yet implemented in Studio. Libraries below are saved to
            your local UI state — they won't auto-scan or accept Sonarr pings until
            the integration lands.
          </p>
        </div>
        <div className="hb-actions">
          <button className="btn btn-ghost" disabled title="Listener not yet implemented">
            <I.Open size={13} /> Copy URL
          </button>
          <button className="btn" disabled title="Listener not yet implemented">
            Test ping
          </button>
        </div>
      </div>

      <div className="lib-list">
        {libraries.length === 0 && !adding && (
          <div className="lib-empty">
            <p>No libraries yet — add one to get started.</p>
          </div>
        )}

        {libraries.map((lib) => {
          const icon =
            /anime/i.test(lib.name) ? <I.Tv size={16} /> :
            /movie|film/i.test(lib.name) ? <I.Film size={16} /> :
            /tv|show|series/i.test(lib.name) ? <I.Tv size={16} /> :
            lib.source === "sonarr" ? <I.Tv size={16} /> :
            lib.source === "radarr" ? <I.Film size={16} /> :
            <I.Folder size={16} />;
          return (
            <div key={lib.id} className={`lib-row ${lib.enabled ? "" : "off"}`}>
              <div className="lib-icon">{icon}</div>
              <div className="lib-main">
                <div className="lib-name">
                  <span>{lib.name}</span>
                  <SourceBadge source={lib.source} />
                </div>
                <div className="lib-path mono">{lib.path}</div>
                <div className="lib-meta">
                  {lib.instance && <span>{lib.instance}</span>}
                  {lib.instance && <span>·</span>}
                  <span>
                    schedule <span className="mono">{SCHEDULE_LABEL[lib.schedule]}</span>
                  </span>
                </div>
              </div>
              <div className="lib-stat">
                <div className="ls-num mono">{(lib.itemsCount ?? 0).toLocaleString()}</div>
                <div className="ls-label">items</div>
              </div>
              <div className="lib-stat">
                <div className={`ls-num mono ${lib.pendingCount ? "warn" : "dim"}`}>
                  {lib.pendingCount ?? 0}
                </div>
                <div className="ls-label">pending</div>
              </div>
              <div className="lib-actions">
                <button
                  className={`toggle ${lib.enabled ? "on" : ""}`}
                  onClick={() => toggleLibraryEnabled(lib.id)}
                  title={lib.enabled ? "Disable" : "Enable"}
                />
                <button
                  className="row-action"
                  title="Open folder"
                  onClick={() => {
                    import("../lib/tauri").then(({ openPath }) =>
                      openPath(lib.path).catch((e) => showToast(`Open failed: ${e}`)),
                    );
                  }}
                >
                  <I.FolderOpen size={14} />
                </button>
                <button
                  className="row-action danger"
                  title="Remove"
                  onClick={() => removeLibrary(lib.id)}
                >
                  <I.Trash size={14} />
                </button>
              </div>
            </div>
          );
        })}

        {!adding ? (
          <button className="lib-add" onClick={() => setAdding(true)}>
            <I.Plus size={14} /> Add a library or watch folder
          </button>
        ) : (
          <div className="lib-add-form">
            <div className="laf-row">
              <label>Source</label>
              <div className="seg">
                {(["sonarr", "radarr", "folder"] as const).map((s) => (
                  <button
                    key={s}
                    className={draft.source === s ? "active" : ""}
                    onClick={() => setDraft({ ...draft, source: s })}
                  >
                    {SOURCE_MAP[s].label}
                  </button>
                ))}
              </div>
            </div>
            <div className="laf-row">
              <label>Name</label>
              <input
                className="input"
                placeholder="TV — Jellyfin (Sonarr)"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </div>
            <div className="laf-row">
              <label>Path</label>
              <div style={{ display: "flex", gap: 8, flex: 1 }}>
                <input
                  className="input mono"
                  style={{ flex: 1, minWidth: 0 }}
                  placeholder={
                    draft.source === "folder" ? "D:\\Media\\TV" : "\\\\NAS-01\\media\\tv"
                  }
                  value={draft.path}
                  onChange={(e) => setDraft({ ...draft, path: e.target.value })}
                />
                <button className="btn btn-ghost" onClick={onBrowse}>
                  Browse…
                </button>
              </div>
            </div>
            <div className="laf-row">
              <label>Schedule</label>
              <div className="seg">
                {(["live", "5m", "15m", "1h", "manual"] as const).map((s) => (
                  <button
                    key={s}
                    className={draft.schedule === s ? "active" : ""}
                    onClick={() => setDraft({ ...draft, schedule: s })}
                    disabled={draft.source === "folder" && s === "live"}
                  >
                    {SCHEDULE_LABEL[s]}
                  </button>
                ))}
              </div>
            </div>
            <div className="laf-actions">
              <button
                className="btn"
                onClick={() => {
                  setAdding(false);
                  setDraft(EMPTY_DRAFT);
                }}
              >
                Cancel
              </button>
              <span style={{ flex: 1 }} />
              <button
                className="btn btn-primary"
                onClick={onAdd}
                disabled={!draft.path.trim()}
              >
                <I.Plus size={13} /> Add library
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
