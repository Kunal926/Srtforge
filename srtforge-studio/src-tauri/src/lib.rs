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
    //   - Otherwise, in dev (debug) builds, walk up from cwd until we
    //     find a directory that contains a `models/` subdirectory.
    //     `pnpm tauri dev` runs from `src-tauri/`, so the search has to
    //     climb two levels (or more) to reach the Srtforge repo root.
    //   - In release builds, leave it unset; srtforge/config.py falls back
    //     to `<exe dir>/models`, the install convention.
    if let Ok(explicit) = std::env::var("SRTFORGE_PROJECT_ROOT") {
        sidecar = sidecar.env("SRTFORGE_PROJECT_ROOT", explicit);
    } else if cfg!(debug_assertions) {
        if let Some(root) = find_dev_project_root() {
            sidecar = sidecar.env("SRTFORGE_PROJECT_ROOT", root.display().to_string());
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

/// Walk up from cwd until a directory containing `models/` is found. The
/// dev launch path is `srtforge-studio/src-tauri/` (where `cargo run` is
/// invoked), and the Srtforge repo root sits two levels up. Stops after a
/// few ancestors so we never wander out of the repo.
fn find_dev_project_root() -> Option<std::path::PathBuf> {
    let start = std::env::current_dir().ok()?;
    let mut cur: Option<&std::path::Path> = Some(start.as_path());
    for _ in 0..6 {
        let dir = cur?;
        if dir.join("models").is_dir() {
            return Some(dir.to_path_buf());
        }
        cur = dir.parent();
    }
    None
}

#[tauri::command]
fn enqueue(
    state: State<'_, WorkerState>,
    file: String,
    id: Option<String>,
    output: Option<String>,
    config: serde_json::Value,
) -> Result<String, String> {
    // The UI generates an id when it adds the row to its local queue so
    // the Tauri shell, the React store, and the Python worker all agree
    // on the same id from the moment the user clicks "Add files".
    let id = id.unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
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

/// Open a file or folder with the OS's default associated application.
/// On Windows we use `cmd /c start "" <path>` so spaces in the path
/// don't break things and `start` resolves the registered handler for
/// the file's extension.
#[tauri::command]
fn open_path(path: String) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("cmd")
            .args(["/C", "start", "", &path])
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&path)
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        std::process::Command::new("xdg-open")
            .arg(&path)
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
}

/// Probe a media file with `ffprobe` and return enough metadata to fill in
/// a queue row's Duration / sample rate / channels / fps / codec cells.
/// The Tauri shell calls this right after the user adds files; the Python
/// worker doesn't have to participate.
#[derive(Debug, Clone, Serialize)]
struct ProbeResult {
    duration_sec: f64,
    sample_rate: u32,
    channels: u32,
    codec: String,
    fps: String,
}

#[tauri::command]
fn probe_file(path: String) -> Result<ProbeResult, String> {
    let output = std::process::Command::new("ffprobe")
        .args([
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-show_entries",
            "stream=codec_type,codec_name,sample_rate,channels,r_frame_rate",
            "-of",
            "json",
            &path,
        ])
        .output()
        .map_err(|e| format!("ffprobe spawn failed: {e}"))?;

    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
    }

    let data: serde_json::Value = serde_json::from_slice(&output.stdout)
        .map_err(|e| format!("ffprobe JSON parse failed: {e}"))?;

    let duration_sec: f64 = data
        .get("format")
        .and_then(|f| f.get("duration"))
        .and_then(|v| v.as_str())
        .and_then(|s| s.parse().ok())
        .unwrap_or(0.0);

    let empty: Vec<serde_json::Value> = Vec::new();
    let streams = data
        .get("streams")
        .and_then(|v| v.as_array())
        .unwrap_or(&empty);
    let audio = streams.iter().find(|s| s.get("codec_type").and_then(|c| c.as_str()) == Some("audio"));
    let video = streams.iter().find(|s| s.get("codec_type").and_then(|c| c.as_str()) == Some("video"));

    let sample_rate: u32 = audio
        .and_then(|s| s.get("sample_rate"))
        .and_then(|v| v.as_str())
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);
    let channels: u32 = audio
        .and_then(|s| s.get("channels"))
        .and_then(|v| v.as_u64())
        .map(|n| n as u32)
        .unwrap_or(0);
    // Prefer the audio codec for the audio-centric pipeline; fall back to
    // the video codec when probing audio-only files where there's no video
    // stream at all.
    let codec = audio
        .or(video)
        .and_then(|s| s.get("codec_name"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| "—".into());
    let fps = video
        .and_then(|s| s.get("r_frame_rate"))
        .and_then(|v| v.as_str())
        .map(|s| {
            let mut parts = s.split('/');
            let n: f64 = parts.next().and_then(|p| p.parse().ok()).unwrap_or(0.0);
            let d: f64 = parts.next().and_then(|p| p.parse().ok()).unwrap_or(1.0);
            if d > 0.0 && n > 0.0 {
                let v = n / d;
                if (v - v.round()).abs() < 0.05 {
                    format!("{:.0}", v)
                } else {
                    format!("{:.2}", v)
                }
            } else {
                "—".into()
            }
        })
        .unwrap_or_else(|| "—".into());

    Ok(ProbeResult {
        duration_sec,
        sample_rate,
        channels,
        codec,
        fps,
    })
}

/// Open File Explorer (or the platform equivalent) with the file at
/// `path` already selected. Useful for "containing folder" — feels like
/// a native macOS Finder reveal.
#[tauri::command]
fn reveal_in_folder(path: String) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .arg(format!("/select,{path}"))
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .args(["-R", &path])
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        // Most Linux file managers don't support "reveal"; fall back to
        // opening the parent directory.
        let parent = std::path::Path::new(&path)
            .parent()
            .map(|p| p.to_path_buf())
            .ok_or_else(|| "no parent directory".to_string())?;
        std::process::Command::new("xdg-open")
            .arg(parent)
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
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
            open_path,
            reveal_in_folder,
            probe_file,
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
