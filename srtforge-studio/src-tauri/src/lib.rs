// Srtforge Studio — Tauri shell.
//
// Owns the Python worker subprocess and bridges between the React UI
// (via Tauri commands + events) and the worker's stdin/stdout JSON
// protocol — the same protocol the existing PySide6 GUI already uses
// to talk to `python -m srtforge worker`.

use std::sync::Arc;

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tokio::sync::mpsc;

/// Outbound command shapes sent down the worker's stdin.
/// Mirrors the JSON contract used by `srtforge.cli.worker` (only
/// `transcribe` and `shutdown` are accepted today; pause/resume/cancel
/// would need to be added on the Python side first).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "action", rename_all = "snake_case")]
enum WorkerRequest {
    Transcribe {
        id: String,
        file: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        output: Option<String>,
        config: serde_json::Value,
    },
    Shutdown,
}

/// Inbound event shapes the worker emits on stdout (one JSON per line).
/// We re-emit them verbatim to the frontend on the `worker:event` channel.
#[derive(Debug, Clone, Deserialize)]
struct WorkerEvent {
    #[serde(default)]
    event: Option<String>,
    #[serde(flatten)]
    rest: serde_json::Map<String, serde_json::Value>,
}

/// Mutable shared state managed by Tauri.
struct WorkerState {
    child: Mutex<Option<CommandChild>>,
    /// Channel used to push stdin lines into the worker's writer task.
    tx: Mutex<Option<mpsc::UnboundedSender<String>>>,
}

impl WorkerState {
    fn new() -> Self {
        Self {
            child: Mutex::new(None),
            tx: Mutex::new(None),
        }
    }
}

/// Spawn the bundled `srtforge_worker` sidecar and start the I/O pumps.
/// Called once at app startup; safe to call again to recover from a crash.
fn spawn_worker(app: &AppHandle, state: &WorkerState) -> anyhow::Result<()> {
    // Replace any prior child first.
    if let Some(prev) = state.child.lock().take() {
        let _ = prev.kill();
    }

    let mut sidecar = app
        .shell()
        .sidecar("srtforge_worker")
        .map_err(|e| anyhow::anyhow!("sidecar lookup failed: {e}"))?
        // The bundled exe ships `python -m srtforge` as a whole; the
        // persistent stdin/stdout JSON loop lives behind the `worker`
        // subcommand (see srtforge/cli.py).
        .args(["worker"]);

    // Pin the worker's "project root" so it can find the `models/` folder
    // (FV4 ckpt + config). Inside a PyInstaller one-file bundle the package
    // resolves to `_MEI*` (a temp extraction dir) and the wrong models
    // location is computed. We override it explicitly:
    //
    //   - If the user sets SRTFORGE_PROJECT_ROOT in their environment,
    //     pass it through.
    //   - Otherwise, in dev (debug) builds, point at the parent of the
    //     working directory — Tauri runs in `srtforge-studio/` and the
    //     parent is the Srtforge repo root with `models/` inside.
    //   - In release builds, leave it unset; srtforge/config.py will fall
    //     back to `<exe dir>/models`, the install convention.
    if let Ok(explicit) = std::env::var("SRTFORGE_PROJECT_ROOT") {
        sidecar = sidecar.env("SRTFORGE_PROJECT_ROOT", explicit);
    } else if cfg!(debug_assertions) {
        if let Some(parent) = std::env::current_dir().ok().and_then(|cwd| {
            cwd.parent().map(|p| p.to_path_buf())
        }) {
            sidecar = sidecar.env("SRTFORGE_PROJECT_ROOT", parent.display().to_string());
        }
    }

    let (mut rx, child) = sidecar
        .spawn()
        .map_err(|e| anyhow::anyhow!("sidecar spawn failed: {e}"))?;

    // Outbound writer pump: anything pushed to `tx` is written + newline'd
    // to the worker's stdin.
    let (tx, mut stdin_rx) = mpsc::unbounded_channel::<String>();
    let writer_child = Arc::new(Mutex::new(Some(child)));
    {
        let writer_child = writer_child.clone();
        // tauri::async_runtime::spawn works from any context; tokio::spawn
        // would panic here because Tauri's `.setup()` callback runs before
        // a Tokio reactor is attached to the calling thread.
        tauri::async_runtime::spawn(async move {
            while let Some(line) = stdin_rx.recv().await {
                let mut payload = line;
                if !payload.ends_with('\n') {
                    payload.push('\n');
                }
                let mut guard = writer_child.lock();
                if let Some(child) = guard.as_mut() {
                    if let Err(e) = child.write(payload.as_bytes()) {
                        eprintln!("worker stdin write failed: {e}");
                    }
                }
            }
        });
    }

    // Inbound reader pump: each stdout line is forwarded to the frontend.
    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(ev) = rx.recv().await {
            match ev {
                CommandEvent::Stdout(line_bytes) => {
                    let line = String::from_utf8_lossy(&line_bytes).to_string();
                    // Worker emits one JSON object per line. We tolerate
                    // malformed lines by forwarding them raw under "log".
                    let payload: serde_json::Value =
                        serde_json::from_str::<WorkerEvent>(&line)
                            .map(|ev| {
                                let mut map = ev.rest;
                                if let Some(name) = ev.event {
                                    map.insert(
                                        "event".into(),
                                        serde_json::Value::String(name),
                                    );
                                }
                                serde_json::Value::Object(map)
                            })
                            .unwrap_or_else(|_| {
                                serde_json::json!({ "event": "log", "msg": line.trim_end() })
                            });
                    let _ = app_handle.emit("worker:event", payload);
                }
                CommandEvent::Stderr(line_bytes) => {
                    let line = String::from_utf8_lossy(&line_bytes).to_string();
                    let _ = app_handle.emit(
                        "worker:event",
                        serde_json::json!({ "event": "log", "lvl": "warn", "msg": line.trim_end() }),
                    );
                }
                CommandEvent::Terminated(payload) => {
                    let _ = app_handle.emit(
                        "worker:event",
                        serde_json::json!({
                            "event": "terminated",
                            "code": payload.code,
                        }),
                    );
                    break;
                }
                _ => {}
            }
        }
    });

    // Stash the writer half so commands can find it.
    *state.tx.lock() = Some(tx);
    // We can't keep the same child Arc in WorkerState cheaply; instead
    // store None here and let the writer task own it. Killing the worker
    // happens by closing the tx + dropping the writer task on shutdown.
    *state.child.lock() = None;

    Ok(())
}

fn send_to_worker(state: &WorkerState, req: &WorkerRequest) -> Result<(), String> {
    let payload = serde_json::to_string(req).map_err(|e| format!("encode failed: {e}"))?;
    let guard = state.tx.lock();
    let tx = guard.as_ref().ok_or("worker not running")?;
    tx.send(payload).map_err(|e| format!("worker channel closed: {e}"))?;
    Ok(())
}

#[tauri::command]
fn enqueue(
    state: State<'_, WorkerState>,
    file: String,
    output: Option<String>,
    config: serde_json::Value,
) -> Result<String, String> {
    let id = uuid::Uuid::new_v4().to_string();
    send_to_worker(
        &state,
        &WorkerRequest::Transcribe {
            id: id.clone(),
            file,
            output,
            config,
        },
    )?;
    Ok(id)
}

#[tauri::command]
fn shutdown_worker(state: State<'_, WorkerState>) -> Result<(), String> {
    send_to_worker(&state, &WorkerRequest::Shutdown)
}

#[tauri::command]
fn restart_worker(app: AppHandle, state: State<'_, WorkerState>) -> Result<(), String> {
    spawn_worker(&app, &state).map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(WorkerState::new())
        .invoke_handler(tauri::generate_handler![
            enqueue,
            shutdown_worker,
            restart_worker,
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let state = app.state::<WorkerState>();
            // Best-effort spawn; if the sidecar isn't bundled (dev mode
            // without a built worker), the UI still loads and surfaces a
            // friendly error from the first command call.
            if let Err(e) = spawn_worker(&handle, &state) {
                eprintln!("worker spawn skipped: {e}");
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
