// Srtforge Studio — Tauri shell.
//
// Owns the Python worker subprocess and bridges between the React UI
// (via Tauri commands + events) and the worker's stdin/stdout JSON
// protocol — the same protocol the existing PySide6 GUI already uses
// to talk to `python -m srtforge worker`.

use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

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
    /// Standalone audio normalize/transcode (Normalize tool).
    Normalize {
        id: String,
        file: String,
        config: serde_json::Value,
    },
    /// Standalone vocal/instrumental separation (BGM tool).
    Separate {
        id: String,
        file: String,
        config: serde_json::Value,
    },
    Shutdown,
    /// Tells the worker to call torch.cuda.empty_cache() without
    /// terminating. Wired to the "Free GPU memory when stopping" toggle.
    ClearGpuCache,
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
    /// Active Studio debug log for the currently-running worker job.
    debug_log: Arc<Mutex<Option<ActiveDebugLog>>>,
}

impl WorkerState {
    fn new() -> Self {
        Self {
            child: Mutex::new(None),
            tx: Mutex::new(None),
            debug_log: Arc::new(Mutex::new(None)),
        }
    }
}

struct ActiveDebugLog {
    job_id: String,
    path: PathBuf,
    file: std::fs::File,
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
    let debug_log = state.debug_log.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(ev) = rx.recv().await {
            match ev {
                CommandEvent::Stdout(line_bytes) => {
                    let line = String::from_utf8_lossy(&line_bytes).to_string();
                    // Worker emits one JSON object per line. We tolerate
                    // malformed lines by forwarding them raw under "log".
                    let mut payload: serde_json::Value = serde_json::from_str::<WorkerEvent>(&line)
                        .map(|ev| {
                            let mut map = ev.rest;
                            if let Some(name) = ev.event {
                                map.insert("event".into(), serde_json::Value::String(name));
                            }
                            serde_json::Value::Object(map)
                        })
                        .unwrap_or_else(|_| {
                            serde_json::json!({
                                "event": "log",
                                "source": "stdout",
                                "msg": line.trim_end()
                            })
                        });
                    enrich_worker_payload(&mut payload, &debug_log);
                    let _ = app_handle.emit("worker:event", payload);
                }
                CommandEvent::Stderr(line_bytes) => {
                    let line = String::from_utf8_lossy(&line_bytes).to_string();
                    let _ = app_handle.emit(
                        "worker:event",
                        log_payload_for_line(&debug_log, "stderr", Some("warn"), line.trim_end()),
                    );
                }
                CommandEvent::Terminated(payload) => {
                    let detail = payload.code.map(|code| format!("exit code {code}"));
                    let mut event_payload = serde_json::json!({
                        "event": "terminated",
                        "code": payload.code,
                    });
                    if let Some((_id, path)) =
                        close_active_debug_log(&debug_log, None, "terminated", detail.as_deref())
                    {
                        if let Some(map) = event_payload.as_object_mut() {
                            insert_string_field(map, "debug_log_path", path);
                        }
                    }
                    let _ = app_handle.emit("worker:event", event_payload);
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
    tx.send(payload)
        .map_err(|e| format!("worker channel closed: {e}"))?;
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

fn project_root_path() -> Result<PathBuf, String> {
    std::env::var("SRTFORGE_PROJECT_ROOT")
        .ok()
        .map(PathBuf::from)
        .or_else(find_dev_project_root)
        .or_else(|| {
            // Production fallback: <exe dir>/logs.
            std::env::current_exe()
                .ok()
                .and_then(|p| p.parent().map(|p| p.to_path_buf()))
        })
        .ok_or_else(|| "could not resolve project root".to_string())
}

fn logs_dir_path() -> Result<PathBuf, String> {
    Ok(project_root_path()?.join("logs"))
}

fn now_unix_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn sanitize_debug_log_token(value: &str) -> String {
    let mut out = String::new();
    let mut last_was_sep = false;
    for ch in value.chars() {
        let mapped = if ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.') {
            ch
        } else {
            '_'
        };
        if mapped == '_' {
            if last_was_sep {
                continue;
            }
            last_was_sep = true;
        } else {
            last_was_sep = false;
        }
        out.push(mapped);
        if out.len() >= 80 {
            break;
        }
    }
    let trimmed = out.trim_matches('_').to_string();
    if trimmed.is_empty() {
        "job".to_string()
    } else {
        trimmed
    }
}

fn debug_log_filename(job_id: &str, unix_seconds: u64) -> String {
    format!(
        "{}_{}.debug.log",
        unix_seconds,
        sanitize_debug_log_token(job_id)
    )
}

fn format_debug_log_line(source: &str, lvl: Option<&str>, msg: &str) -> String {
    match lvl {
        Some(lvl) if !lvl.trim().is_empty() => format!("[{source}] {lvl}: {msg}\n"),
        _ => format!("[{source}] {msg}\n"),
    }
}

fn start_debug_log(
    debug_log: &Arc<Mutex<Option<ActiveDebugLog>>>,
    job_id: &str,
    file_path: Option<&str>,
) -> Option<String> {
    let dir = logs_dir_path().ok()?.join("studio-debug");
    if std::fs::create_dir_all(&dir).is_err() {
        return None;
    }
    let started = now_unix_seconds();
    let path = dir.join(debug_log_filename(job_id, started));
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&path)
        .ok()?;

    let _ = writeln!(file, "Srtforge Studio debug log");
    let _ = writeln!(file, "job_id={job_id}");
    if let Some(file_path) = file_path {
        let _ = writeln!(file, "file={file_path}");
    }
    let _ = writeln!(file, "started_unix={started}");
    let _ = writeln!(file);
    let _ = file.flush();

    let path_text = path.display().to_string();
    let mut guard = debug_log.lock();
    if let Some(mut previous) = guard.take() {
        let _ = writeln!(
            previous.file,
            "{}",
            format_debug_log_line("studio", Some("warn"), "debug log superseded by a new job")
                .trim_end()
        );
        let _ = previous.file.flush();
    }
    *guard = Some(ActiveDebugLog {
        job_id: job_id.to_string(),
        path,
        file,
    });
    Some(path_text)
}

fn active_debug_log_path(
    debug_log: &Arc<Mutex<Option<ActiveDebugLog>>>,
    expected_job_id: Option<&str>,
) -> Option<(String, String)> {
    let guard = debug_log.lock();
    let active = guard.as_ref()?;
    if let Some(expected) = expected_job_id {
        if expected != active.job_id {
            return None;
        }
    }
    Some((active.job_id.clone(), active.path.display().to_string()))
}

fn append_active_debug_line(
    debug_log: &Arc<Mutex<Option<ActiveDebugLog>>>,
    expected_job_id: Option<&str>,
    source: &str,
    lvl: Option<&str>,
    msg: &str,
) -> Option<(String, String)> {
    let mut guard = debug_log.lock();
    let active = guard.as_mut()?;
    if let Some(expected) = expected_job_id {
        if expected != active.job_id {
            return None;
        }
    }
    let line = format_debug_log_line(source, lvl, msg);
    let _ = active.file.write_all(line.as_bytes());
    let _ = active.file.flush();
    Some((active.job_id.clone(), active.path.display().to_string()))
}

fn close_active_debug_log(
    debug_log: &Arc<Mutex<Option<ActiveDebugLog>>>,
    expected_job_id: Option<&str>,
    event: &str,
    detail: Option<&str>,
) -> Option<(String, String)> {
    let mut guard = debug_log.lock();
    let should_close = guard
        .as_ref()
        .map(|active| {
            expected_job_id
                .map(|id| id == active.job_id)
                .unwrap_or(true)
        })
        .unwrap_or(false);
    if !should_close {
        return None;
    }
    let mut active = guard.take()?;
    let path = active.path.display().to_string();
    let footer = match detail {
        Some(detail) if !detail.trim().is_empty() => format!("{event}: {detail}"),
        _ => event.to_string(),
    };
    let _ = writeln!(active.file);
    let _ = active
        .file
        .write_all(format_debug_log_line("studio", Some("terminal"), &footer).as_bytes());
    let _ = active.file.flush();
    Some((active.job_id, path))
}

fn insert_string_field(
    map: &mut serde_json::Map<String, serde_json::Value>,
    key: &str,
    value: String,
) {
    map.insert(key.into(), serde_json::Value::String(value));
}

fn enrich_worker_payload(
    payload: &mut serde_json::Value,
    debug_log: &Arc<Mutex<Option<ActiveDebugLog>>>,
) {
    let Some(map) = payload.as_object_mut() else {
        return;
    };
    let event = map
        .get("event")
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string();
    let job_id = map
        .get("id")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());

    match event.as_str() {
        "job_started" => {
            if let Some(id) = job_id.as_deref() {
                let file = map
                    .get("file")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                if let Some(path) = start_debug_log(debug_log, id, file.as_deref()) {
                    insert_string_field(map, "debug_log_path", path);
                }
            }
        }
        "log" => {
            let msg = map
                .get("msg")
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string();
            let lvl = map
                .get("lvl")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());
            let source = map
                .get("source")
                .and_then(|v| v.as_str())
                .unwrap_or("stdout")
                .to_string();
            if let Some((id, path)) = append_active_debug_line(
                debug_log,
                job_id.as_deref(),
                &source,
                lvl.as_deref(),
                &msg,
            ) {
                map.entry("id")
                    .or_insert_with(|| serde_json::Value::String(id));
                map.entry("source")
                    .or_insert_with(|| serde_json::Value::String(source));
                insert_string_field(map, "debug_log_path", path);
            }
        }
        "job_completed" | "job_failed" => {
            let detail = map
                .get("error")
                .or_else(|| map.get("path"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());
            if let Some((_id, path)) = close_active_debug_log(
                debug_log,
                job_id.as_deref(),
                event.as_str(),
                detail.as_deref(),
            ) {
                insert_string_field(map, "debug_log_path", path);
            }
        }
        _ => {
            if let Some(id) = job_id.as_deref() {
                if let Some((_id, path)) = active_debug_log_path(debug_log, Some(id)) {
                    insert_string_field(map, "debug_log_path", path);
                }
            }
        }
    }
}

fn log_payload_for_line(
    debug_log: &Arc<Mutex<Option<ActiveDebugLog>>>,
    source: &str,
    lvl: Option<&str>,
    msg: &str,
) -> serde_json::Value {
    let mut map = serde_json::Map::new();
    map.insert("event".into(), serde_json::Value::String("log".into()));
    map.insert("msg".into(), serde_json::Value::String(msg.to_string()));
    if let Some(lvl) = lvl {
        map.insert("lvl".into(), serde_json::Value::String(lvl.to_string()));
    }
    map.insert(
        "source".into(),
        serde_json::Value::String(source.to_string()),
    );
    if let Some((id, path)) = append_active_debug_line(debug_log, None, source, lvl, msg) {
        map.insert("id".into(), serde_json::Value::String(id));
        map.insert("debug_log_path".into(), serde_json::Value::String(path));
    }
    serde_json::Value::Object(map)
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

#[tauri::command]
fn clear_gpu_cache(state: State<'_, WorkerState>) -> Result<(), String> {
    send_to_worker(&state, &WorkerRequest::ClearGpuCache)
}

#[tauri::command]
fn normalize(
    state: State<'_, WorkerState>,
    file: String,
    id: Option<String>,
    config: serde_json::Value,
) -> Result<String, String> {
    let id = id.unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
    send_to_worker(
        &state,
        &WorkerRequest::Normalize {
            id: id.clone(),
            file,
            config,
        },
    )?;
    Ok(id)
}

#[tauri::command]
fn separate(
    state: State<'_, WorkerState>,
    file: String,
    id: Option<String>,
    config: serde_json::Value,
) -> Result<String, String> {
    let id = id.unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
    send_to_worker(
        &state,
        &WorkerRequest::Separate {
            id: id.clone(),
            file,
            config,
        },
    )?;
    Ok(id)
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

/// Resolve the absolute path of `<project_root>/logs/`. The Tauri shell
/// already pins SRTFORGE_PROJECT_ROOT at startup (so the worker can find
/// `models/`); we use the same root for runtime logs.
#[tauri::command]
fn get_logs_dir() -> Result<String, String> {
    Ok(logs_dir_path()?.display().to_string())
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
    let audio = streams
        .iter()
        .find(|s| s.get("codec_type").and_then(|c| c.as_str()) == Some("audio"));
    let video = streams
        .iter()
        .find(|s| s.get("codec_type").and_then(|c| c.as_str()) == Some("video"));

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
        // explorer.exe has its own command-line parser that doesn't follow
        // the standard Windows escaping rules. Rust's auto-quoting wraps
        // the whole `/select,<path with spaces>` in quotes, which breaks
        // the `/select,` form. `raw_arg` lets us write the cmdline ourselves.
        use std::os::windows::process::CommandExt;
        std::process::Command::new("explorer.exe")
            .raw_arg(format!("/select,\"{path}\""))
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
            normalize,
            separate,
            shutdown_worker,
            restart_worker,
            clear_gpu_cache,
            open_path,
            reveal_in_folder,
            probe_file,
            get_logs_dir,
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

#[cfg(test)]
mod tests {
    use super::{debug_log_filename, format_debug_log_line, sanitize_debug_log_token};

    #[test]
    fn debug_log_filename_sanitizes_job_id() {
        assert_eq!(
            debug_log_filename("job:one/two\\three", 123),
            "123_job_one_two_three.debug.log"
        );
    }

    #[test]
    fn debug_log_sanitize_falls_back_for_empty_tokens() {
        assert_eq!(sanitize_debug_log_token(":///"), "job");
    }

    #[test]
    fn debug_log_line_formats_source_level_and_message() {
        assert_eq!(
            format_debug_log_line("stderr", Some("warn"), "something happened"),
            "[stderr] warn: something happened\n"
        );
        assert_eq!(
            format_debug_log_line("stdout", None, "plain"),
            "[stdout] plain\n"
        );
    }
}
