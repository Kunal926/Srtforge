// Srtforge Studio — Tauri shell.
//
// Owns the Python worker subprocess and bridges between the React UI
// (via Tauri commands + events) and the worker's stdin/stdout JSON
// protocol — the same protocol the existing PySide6 GUI already uses
// to talk to `python -m srtforge worker`.

use std::collections::HashMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{
    atomic::{AtomicBool, AtomicU64, Ordering},
    Arc,
};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

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
type SharedWorkerChild = Arc<Mutex<Option<CommandChild>>>;

struct WorkerState {
    child: Mutex<Option<SharedWorkerChild>>,
    /// Channel used to push stdin lines into the worker's writer task.
    tx: Mutex<Option<mpsc::UnboundedSender<String>>>,
    /// False when the child has exited or the writer hit a broken stdin pipe.
    alive: Arc<AtomicBool>,
    /// Monotonic worker generation so stale terminated events cannot poison a replacement worker.
    generation: Arc<AtomicU64>,
    /// Active Studio debug log for the currently-running worker job.
    debug_log: Arc<Mutex<Option<ActiveDebugLog>>>,
    /// Studio-only per-job metadata used for debug-log diagnostics.
    job_meta: Arc<Mutex<HashMap<String, StudioJobMeta>>>,
}

impl WorkerState {
    fn new() -> Self {
        Self {
            child: Mutex::new(None),
            tx: Mutex::new(None),
            alive: Arc::new(AtomicBool::new(false)),
            generation: Arc::new(AtomicU64::new(0)),
            debug_log: Arc::new(Mutex::new(None)),
            job_meta: Arc::new(Mutex::new(HashMap::new())),
        }
    }
}

struct ActiveDebugLog {
    job_id: String,
    path: PathBuf,
    file: std::fs::File,
}

#[derive(Debug, Clone)]
struct StudioJobMeta {
    gpu_performance_mode: bool,
    enqueued_at: u64,
}

#[derive(Debug, Clone, Serialize)]
struct GpuTelemetry {
    available: bool,
    name: Option<String>,
    utilization_pct: Option<u8>,
    memory_used_mb: Option<u64>,
    memory_total_mb: Option<u64>,
    error: Option<String>,
}

impl GpuTelemetry {
    fn unavailable(error: impl Into<String>) -> Self {
        Self {
            available: false,
            name: None,
            utilization_pct: None,
            memory_used_mb: None,
            memory_total_mb: None,
            error: Some(error.into()),
        }
    }
}

const WEBVIEW_GPU_ACCELERATION_DISABLED: bool = false;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[cfg(target_os = "windows")]
const SIDECAR_DIR_NAME: &str = "srtforge_worker";
#[cfg(target_os = "windows")]
const SIDECAR_EXE_NAME: &str = "srtforge_worker.exe";
#[cfg(not(target_os = "windows"))]
const SIDECAR_DIR_NAME: &str = "srtforge_worker";
#[cfg(not(target_os = "windows"))]
const SIDECAR_EXE_NAME: &str = "srtforge_worker";

fn onedir_sidecar_path(base: &Path) -> PathBuf {
    base.join(SIDECAR_DIR_NAME).join(SIDECAR_EXE_NAME)
}

fn hidden_command(program: &str) -> std::process::Command {
    let mut command = std::process::Command::new(program);
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
}

fn dev_sidecar_binary_path(root: &Path) -> PathBuf {
    root.join("srtforge-studio")
        .join("src-tauri")
        .join("binaries")
        .join(SIDECAR_DIR_NAME)
        .join(SIDECAR_EXE_NAME)
}

fn find_project_root_from(start: &Path) -> Option<PathBuf> {
    let mut cur = if start.is_file() {
        start.parent()
    } else {
        Some(start)
    };
    for _ in 0..8 {
        let dir = cur?;
        if dir.join("models").is_dir() {
            return Some(dir.to_path_buf());
        }
        cur = dir.parent();
    }
    None
}

fn ffmpeg_tool_dir_is_complete(dir: &Path) -> bool {
    #[cfg(target_os = "windows")]
    let tools = ["ffmpeg.exe", "ffprobe.exe"];
    #[cfg(not(target_os = "windows"))]
    let tools = ["ffmpeg", "ffprobe"];

    tools.iter().all(|tool| dir.join(tool).is_file())
}

fn resolve_bundled_ffmpeg_dir(
    app: &AppHandle,
    project_root: Option<&Path>,
    worker_dir: &Path,
) -> Option<PathBuf> {
    let mut candidates = Vec::new();

    if let Some(root) = project_root {
        candidates.push(root.join("ffmpeg").join("bin"));
        candidates.push(root.join("ffmpeg"));
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        candidates.push(resource_dir.join("ffmpeg").join("bin"));
        candidates.push(resource_dir.join("ffmpeg"));
    }

    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            candidates.push(exe_dir.join("ffmpeg").join("bin"));
            candidates.push(exe_dir.join("ffmpeg"));
        }
    }

    if let Some(parent) = worker_dir.parent() {
        candidates.push(parent.join("ffmpeg").join("bin"));
        candidates.push(parent.join("ffmpeg"));
    }

    candidates
        .into_iter()
        .find(|candidate| ffmpeg_tool_dir_is_complete(candidate))
}

fn resolve_sidecar_binary_path(app: &AppHandle) -> anyhow::Result<PathBuf> {
    let mut attempted: Vec<String> = Vec::new();

    if let Some(root) = find_dev_project_root() {
        let dev_path = dev_sidecar_binary_path(&root);
        attempted.push(dev_path.display().to_string());
        if dev_path.exists() {
            return Ok(dev_path);
        }
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        let resource_path = onedir_sidecar_path(&resource_dir);
        attempted.push(resource_path.display().to_string());
        if resource_path.exists() {
            return Ok(resource_path);
        }
    }

    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            let adjacent_path = onedir_sidecar_path(exe_dir);
            attempted.push(adjacent_path.display().to_string());
            if adjacent_path.exists() {
                return Ok(adjacent_path);
            }
        }
    }

    anyhow::bail!(
        "sidecar worker executable not found; tried {}",
        attempted.join("; ")
    )
}

fn kill_worker_child(child: CommandChild) -> Option<u32> {
    let pid = child.pid();
    #[cfg(target_os = "windows")]
    {
        let pid_text = pid.to_string();
        let _ = hidden_command("taskkill")
            .args(["/PID", &pid_text, "/T", "/F"])
            .output();
    }
    let _ = child.kill();
    let _ = wait_for_process_exit(pid, Duration::from_secs(8));
    Some(pid)
}

fn kill_worker_child_handle(child: &SharedWorkerChild) -> Option<u32> {
    let child = child.lock().take()?;
    kill_worker_child(child)
}

#[cfg(target_os = "windows")]
fn process_is_running(pid: u32) -> bool {
    let filter = format!("PID eq {pid}");
    let Ok(output) = hidden_command("tasklist")
        .args(["/FI", &filter, "/FO", "CSV", "/NH"])
        .output()
    else {
        return false;
    };
    let stdout = String::from_utf8_lossy(&output.stdout);
    stdout
        .lines()
        .any(|line| line.contains(&format!("\"{pid}\"")) || line.contains(&format!(",{pid},")))
}

#[cfg(not(target_os = "windows"))]
fn process_is_running(_pid: u32) -> bool {
    false
}

fn wait_for_process_exit(pid: u32, timeout: Duration) -> bool {
    let started = Instant::now();
    while started.elapsed() < timeout {
        if !process_is_running(pid) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    !process_is_running(pid)
}

fn normalize_process_path_text(value: impl AsRef<str>) -> String {
    value
        .as_ref()
        .trim()
        .trim_matches('"')
        .replace('/', "\\")
        .trim_end_matches('\\')
        .to_ascii_lowercase()
}

fn path_text_is_under_dir(candidate: &str, dir: &Path) -> bool {
    let base = normalize_process_path_text(dir.display().to_string());
    let candidate = normalize_process_path_text(candidate);
    if base.is_empty() || candidate.is_empty() {
        return false;
    }
    candidate == base || candidate.starts_with(&format!("{base}\\"))
}

#[cfg(target_os = "windows")]
fn sidecar_worker_processes() -> Vec<(u32, String)> {
    let script = format!(
        "Get-CimInstance Win32_Process -Filter \"Name = '{SIDECAR_EXE_NAME}'\" | ForEach-Object {{ \"$($_.ProcessId)|$($_.ExecutablePath)\" }}"
    );
    let Ok(output) = hidden_command("powershell.exe")
        .args(["-NoProfile", "-Command", &script])
        .output()
    else {
        return Vec::new();
    };

    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| {
            let (pid, path) = line.split_once('|')?;
            let pid = pid.trim().parse::<u32>().ok()?;
            Some((pid, path.trim().to_string()))
        })
        .collect()
}

#[cfg(target_os = "windows")]
fn sweep_stale_sidecar_workers(sidecar_dir: &Path) -> Vec<u32> {
    let mut killed = Vec::new();
    for (pid, path) in sidecar_worker_processes() {
        if !path_text_is_under_dir(&path, sidecar_dir) {
            continue;
        }
        let pid_text = pid.to_string();
        let _ = hidden_command("taskkill")
            .args(["/PID", &pid_text, "/T", "/F"])
            .output();
        let _ = wait_for_process_exit(pid, Duration::from_secs(5));
        killed.push(pid);
    }
    killed
}

#[cfg(not(target_os = "windows"))]
fn sweep_stale_sidecar_workers(_sidecar_dir: &Path) -> Vec<u32> {
    Vec::new()
}

fn parse_gpu_telemetry_number<T>(value: Option<&str>) -> Option<T>
where
    T: std::str::FromStr,
{
    value?.trim().parse::<T>().ok()
}

fn parse_gpu_telemetry_line(line: &str) -> Option<GpuTelemetry> {
    let mut parts = line.split(',').map(str::trim);
    let name = parts.next()?.to_string();
    let utilization_pct = parse_gpu_telemetry_number::<u8>(parts.next());
    let memory_used_mb = parse_gpu_telemetry_number::<u64>(parts.next());
    let memory_total_mb = parse_gpu_telemetry_number::<u64>(parts.next());
    if name.is_empty() || memory_total_mb.unwrap_or(0) == 0 {
        return None;
    }
    Some(GpuTelemetry {
        available: true,
        name: Some(name),
        utilization_pct,
        memory_used_mb,
        memory_total_mb,
        error: None,
    })
}

fn parse_gpu_telemetry_output(stdout: &[u8]) -> GpuTelemetry {
    let stdout = String::from_utf8_lossy(stdout);
    stdout
        .lines()
        .find_map(parse_gpu_telemetry_line)
        .unwrap_or_else(|| GpuTelemetry::unavailable("nvidia-smi returned no GPU telemetry"))
}

/// Spawn the bundled `srtforge_worker` sidecar and start the I/O pumps.
/// Called once at app startup; safe to call again to recover from a crash.
fn spawn_worker(app: &AppHandle, state: &WorkerState) -> anyhow::Result<()> {
    let generation = state.generation.fetch_add(1, Ordering::SeqCst) + 1;
    state.alive.store(false, Ordering::SeqCst);
    *state.tx.lock() = None;

    // Replace any prior child first.
    if let Some(prev) = state.child.lock().take() {
        let _ = kill_worker_child_handle(&prev);
    }
    state.job_meta.lock().clear();

    let worker_exe = resolve_sidecar_binary_path(app)?;
    let worker_dir = worker_exe
        .parent()
        .ok_or_else(|| anyhow::anyhow!("sidecar worker executable has no parent directory"))?;
    let mut sidecar = app
        .shell()
        .command(worker_exe.as_os_str())
        // The bundled exe ships `python -m srtforge` as a whole; the
        // persistent stdin/stdout JSON loop lives behind the `worker`
        // subcommand (see srtforge/cli.py). Studio sends job settings per
        // request, so startup must not preload from root srtforge.config.
        .args(["worker", "--no-preload"])
        .current_dir(worker_dir);

    // Pin the worker's project root so relative FV4 paths resolve to the
    // real `<repo>/models` folder. Release no-bundle runs keep the sidecar
    // under `src-tauri/binaries/`, so the resource parent is not always the
    // project root.
    let project_root = if let Ok(explicit) = std::env::var("SRTFORGE_PROJECT_ROOT") {
        sidecar = sidecar.env("SRTFORGE_PROJECT_ROOT", explicit.clone());
        Some(PathBuf::from(explicit))
    } else {
        let discovered_root = find_project_root_from(worker_dir)
            .or_else(|| {
                app.path()
                    .resource_dir()
                    .ok()
                    .and_then(|path| find_project_root_from(&path))
            })
            .or_else(|| {
                std::env::current_exe()
                    .ok()
                    .and_then(|path| find_project_root_from(&path))
            })
            .or_else(find_dev_project_root)
            .or_else(|| worker_dir.parent().map(Path::to_path_buf));
        if let Some(project_root) = discovered_root.as_ref() {
            sidecar = sidecar.env("SRTFORGE_PROJECT_ROOT", project_root.display().to_string());
        }
        discovered_root
    };

    if let Some(ffmpeg_dir) = resolve_bundled_ffmpeg_dir(app, project_root.as_deref(), worker_dir) {
        let mut path_entries = vec![ffmpeg_dir];
        if let Some(existing_path) = std::env::var_os("PATH") {
            path_entries.extend(std::env::split_paths(&existing_path));
        }
        if let Ok(joined_path) = std::env::join_paths(path_entries) {
            sidecar = sidecar.env("PATH", joined_path.to_string_lossy().to_string());
        }
    }

    let (mut rx, child) = sidecar
        .spawn()
        .map_err(|e| anyhow::anyhow!("sidecar spawn failed: {e}"))?;
    state.alive.store(true, Ordering::SeqCst);

    // Outbound writer pump: anything pushed to `tx` is written + newline'd
    // to the worker's stdin.
    let (tx, mut stdin_rx) = mpsc::unbounded_channel::<String>();
    let writer_child = Arc::new(Mutex::new(Some(child)));
    *state.child.lock() = Some(writer_child.clone());
    {
        let writer_child = writer_child.clone();
        let writer_app_handle = app.clone();
        let writer_alive = state.alive.clone();
        let writer_generation = state.generation.clone();
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
                        let error = format!("worker stdin write failed: {e}");
                        eprintln!("{error}");
                        if writer_generation.load(Ordering::SeqCst) == generation {
                            writer_alive.store(false, Ordering::SeqCst);
                            let _ = writer_app_handle.emit(
                                "worker:event",
                                worker_write_failed_payload(&payload, &error),
                            );
                        }
                        break;
                    }
                }
            }
        });
    }

    // Inbound reader pump: each stdout line is forwarded to the frontend.
    let app_handle = app.clone();
    let debug_log = state.debug_log.clone();
    let job_meta = state.job_meta.clone();
    let reader_child = writer_child.clone();
    let worker_alive = state.alive.clone();
    let worker_generation = state.generation.clone();
    tauri::async_runtime::spawn(async move {
        let mut live_event_filter = LiveWorkerEventFilter::default();
        while let Some(ev) = rx.recv().await {
            match ev {
                CommandEvent::Stdout(line_bytes) => {
                    let line = String::from_utf8_lossy(&line_bytes).to_string();
                    let trimmed_line = line.trim_end();
                    // Worker emits one JSON object per line. We tolerate
                    // malformed lines by forwarding them raw under "log".
                    let parsed_worker_event = serde_json::from_str::<WorkerEvent>(&line);
                    let is_raw_log = parsed_worker_event.is_err();
                    let mut payload: serde_json::Value = parsed_worker_event
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
                                "msg": trimmed_line
                            })
                        });
                    enrich_worker_payload(&app_handle, &mut payload, &debug_log, &job_meta);
                    if (!is_raw_log || should_emit_live_terminal_line(trimmed_line))
                        && live_event_filter.should_emit(&payload)
                    {
                        let _ = app_handle.emit("worker:event", payload);
                    }
                }
                CommandEvent::Stderr(line_bytes) => {
                    let line = String::from_utf8_lossy(&line_bytes).to_string();
                    let trimmed_line = line.trim_end();
                    let payload =
                        log_payload_for_line(&debug_log, "stderr", Some("warn"), trimmed_line);
                    if should_emit_live_terminal_line(trimmed_line)
                        && live_event_filter.should_emit(&payload)
                    {
                        let _ = app_handle.emit("worker:event", payload);
                    }
                }
                CommandEvent::Terminated(payload) => {
                    if worker_generation.load(Ordering::SeqCst) != generation {
                        break;
                    }
                    worker_alive.store(false, Ordering::SeqCst);
                    let _ = reader_child.lock().take();
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
                    job_meta.lock().clear();
                    let _ = app_handle.emit("worker:event", event_payload);
                    break;
                }
                _ => {}
            }
        }
    });

    // Stash the writer half so commands can find it.
    *state.tx.lock() = Some(tx);

    Ok(())
}

fn send_to_worker(state: &WorkerState, req: &WorkerRequest) -> Result<(), String> {
    if !state.alive.load(Ordering::SeqCst) {
        return Err("worker not running".to_string());
    }
    let payload = serde_json::to_string(req).map_err(|e| format!("encode failed: {e}"))?;
    let guard = state.tx.lock();
    let tx = guard.as_ref().ok_or("worker not running")?;
    tx.send(payload)
        .map_err(|e| format!("worker channel closed: {e}"))?;
    Ok(())
}

fn should_restart_worker_after_send_failure(error: &str) -> bool {
    error.contains("worker not running") || error.contains("worker channel closed")
}

fn send_to_worker_with_restart(
    app: &AppHandle,
    state: &WorkerState,
    req: &WorkerRequest,
) -> Result<(), String> {
    match send_to_worker(state, req) {
        Ok(()) => Ok(()),
        Err(first_error) if should_restart_worker_after_send_failure(&first_error) => {
            spawn_worker(app, state).map_err(|restart_error| {
                format!("{first_error}; restart failed: {restart_error}")
            })?;
            send_to_worker(state, req).map_err(|retry_error| {
                format!("{first_error}; restart retry failed: {retry_error}")
            })
        }
        Err(error) => Err(error),
    }
}

fn request_job_id(line: &str) -> Option<String> {
    let value = serde_json::from_str::<serde_json::Value>(line.trim()).ok()?;
    value
        .get("id")
        .and_then(serde_json::Value::as_str)
        .map(str::to_string)
}

fn worker_write_failed_payload(line: &str, error: &str) -> serde_json::Value {
    if let Some(id) = request_job_id(line) {
        serde_json::json!({
            "event": "job_failed",
            "id": id,
            "error": error,
        })
    } else {
        serde_json::json!({
            "event": "terminated",
            "code": null,
            "error": error,
        })
    }
}

/// Walk up from cwd until a directory containing `models/` is found. The
/// dev launch path is `srtforge-studio/src-tauri/` (where `cargo run` is
/// invoked), and the Srtforge repo root sits two levels up. Stops after a
/// few ancestors so we never wander out of the repo.
fn find_dev_project_root() -> Option<std::path::PathBuf> {
    let start = std::env::current_dir().ok()?;
    find_project_root_from(&start)
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

fn file_modified_unix(path: &PathBuf) -> Option<u64> {
    std::fs::metadata(path)
        .ok()?
        .modified()
        .ok()?
        .duration_since(UNIX_EPOCH)
        .ok()
        .map(|duration| duration.as_secs())
}

fn append_studio_job_metadata(
    app: &AppHandle,
    debug_log: &Arc<Mutex<Option<ActiveDebugLog>>>,
    job_id: &str,
    job_meta: &Arc<Mutex<HashMap<String, StudioJobMeta>>>,
) {
    let meta = job_meta.lock().get(job_id).cloned();
    let sidecar_path = resolve_sidecar_binary_path(app).ok();
    let sidecar_path_text = sidecar_path
        .as_ref()
        .map(|path| path.display().to_string())
        .unwrap_or_else(|| "unavailable".to_string());
    let sidecar_modified_text = sidecar_path
        .as_ref()
        .and_then(file_modified_unix)
        .map(|seconds| seconds.to_string())
        .unwrap_or_else(|| "unavailable".to_string());
    let max_cuda_text = meta
        .as_ref()
        .map(|value| {
            if value.gpu_performance_mode {
                "enabled"
            } else {
                "disabled"
            }
        })
        .unwrap_or("unknown");
    let enqueued_text = meta
        .as_ref()
        .map(|value| value.enqueued_at.to_string())
        .unwrap_or_else(|| "unknown".to_string());
    let webview_gpu_text = if WEBVIEW_GPU_ACCELERATION_DISABLED {
        "disabled"
    } else {
        "enabled"
    };
    let line = format!(
        "Studio runtime: max_cuda_mode={max_cuda_text} webview_gpu_acceleration={webview_gpu_text} sidecar_path={sidecar_path_text} sidecar_modified_unix={sidecar_modified_text} enqueued_unix={enqueued_text}"
    );
    let _ = append_active_debug_line(debug_log, Some(job_id), "studio", Some("info"), &line);
}

fn enrich_worker_payload(
    app: &AppHandle,
    payload: &mut serde_json::Value,
    debug_log: &Arc<Mutex<Option<ActiveDebugLog>>>,
    job_meta: &Arc<Mutex<HashMap<String, StudioJobMeta>>>,
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
                append_studio_job_metadata(app, debug_log, id, job_meta);
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
            if let Some(id) = job_id.as_deref() {
                job_meta.lock().remove(id);
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

fn is_terminal_progress_line(msg: &str) -> bool {
    let trimmed = msg.trim();
    (trimmed.starts_with("Transcribing:")
        && (trimmed.contains("it/s") || trimmed.contains("?it/s") || trimmed.contains("s/it")))
        || (trimmed.contains("%|") && (trimmed.contains("it/s") || trimmed.contains("?it/s")))
}

fn should_emit_live_terminal_line(msg: &str) -> bool {
    let trimmed = msg.trim();
    !trimmed.is_empty()
}

#[derive(Default)]
struct LiveWorkerEventFilter {
    last_progress_job: Option<String>,
    last_progress_stage: Option<String>,
    last_progress_at: Option<Instant>,
    last_log_at: Option<Instant>,
    last_terminal_progress_log_at: Option<Instant>,
}

impl LiveWorkerEventFilter {
    fn should_emit(&mut self, payload: &serde_json::Value) -> bool {
        match payload.get("event").and_then(serde_json::Value::as_str) {
            Some("progress") => self.should_emit_progress(payload),
            Some("log") => self.should_emit_log(payload),
            _ => true,
        }
    }

    fn should_emit_progress(&mut self, payload: &serde_json::Value) -> bool {
        let Some(fraction) = payload
            .get("fraction")
            .or_else(|| payload.get("progress"))
            .and_then(serde_json::Value::as_f64)
        else {
            return true;
        };

        let job = payload
            .get("id")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default()
            .to_string();
        let stage = payload
            .get("stage")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default()
            .to_string();

        let job_changed = self.last_progress_job.as_deref() != Some(job.as_str());
        let stage_changed = self.last_progress_stage.as_deref() != Some(stage.as_str());
        let enough_time = self
            .last_progress_at
            .map(|last| last.elapsed() >= Duration::from_secs(1))
            .unwrap_or(true);

        if job_changed || stage_changed || fraction >= 1.0 || enough_time {
            self.last_progress_job = Some(job);
            self.last_progress_stage = Some(stage);
            self.last_progress_at = Some(Instant::now());
            return true;
        }

        false
    }

    fn should_emit_log(&mut self, payload: &serde_json::Value) -> bool {
        let msg = payload
            .get("msg")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default();
        let lvl = payload
            .get("lvl")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default()
            .to_ascii_lowercase();
        let source = payload
            .get("source")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default();
        if source == "pipeline-heartbeat" || msg.contains("still running after") {
            return true;
        }
        if lvl.contains("warn") || lvl.contains("err") || lvl.contains("error") {
            return true;
        }
        if is_terminal_progress_line(msg) {
            let enough_time = self
                .last_terminal_progress_log_at
                .map(|last| last.elapsed() >= Duration::from_secs(1))
                .unwrap_or(true);
            if enough_time {
                self.last_terminal_progress_log_at = Some(Instant::now());
                return true;
            }
            return false;
        }

        let enough_time = self
            .last_log_at
            .map(|last| last.elapsed() >= Duration::from_secs(1))
            .unwrap_or(true);
        if enough_time {
            self.last_log_at = Some(Instant::now());
            return true;
        }
        false
    }
}

#[tauri::command]
fn enqueue(
    app: AppHandle,
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
    let gpu_performance_mode = config
        .get("studio")
        .and_then(|studio| studio.get("gpu_performance_mode"))
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(true);
    state.job_meta.lock().insert(
        id.clone(),
        StudioJobMeta {
            gpu_performance_mode,
            enqueued_at: now_unix_seconds(),
        },
    );
    let request = WorkerRequest::Transcribe {
        id: id.clone(),
        file,
        output,
        config,
    };
    let send_result = send_to_worker_with_restart(&app, &state, &request);
    if send_result.is_err() {
        state.job_meta.lock().remove(&id);
    }
    send_result?;
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
fn stop_current_job(
    app: AppHandle,
    state: State<'_, WorkerState>,
    free_gpu_on_stop: Option<bool>,
) -> Result<(), String> {
    let known_job_id = state.job_meta.lock().keys().next().cloned();
    state.generation.fetch_add(1, Ordering::SeqCst);
    state.alive.store(false, Ordering::SeqCst);
    *state.tx.lock() = None;

    let killed_pid = state
        .child
        .lock()
        .take()
        .and_then(|child| kill_worker_child_handle(&child));
    let detail = killed_pid
        .map(|pid| format!("stopped by user (pid {pid})"))
        .unwrap_or_else(|| "stopped by user".to_string());

    if let Some((id, path)) =
        close_active_debug_log(&state.debug_log, None, "job_stopped", Some(&detail))
    {
        let _ = app.emit(
            "worker:event",
            serde_json::json!({
                "event": "job_failed",
                "id": id,
                "error": "Stopped by user",
                "debug_log_path": path,
            }),
        );
    } else if let Some(id) = known_job_id {
        let _ = app.emit(
            "worker:event",
            serde_json::json!({
                "event": "job_failed",
                "id": id,
                "error": "Stopped by user",
            }),
        );
    } else {
        let _ = app.emit(
            "worker:event",
            serde_json::json!({
                "event": "terminated",
                "code": null,
                "error": "Stopped by user",
            }),
        );
    }

    state.job_meta.lock().clear();
    if free_gpu_on_stop.unwrap_or(true) {
        if let Ok(worker_exe) = resolve_sidecar_binary_path(&app) {
            if let Some(sidecar_dir) = worker_exe.parent() {
                let _ = sweep_stale_sidecar_workers(sidecar_dir);
            }
        }
    }
    spawn_worker(&app, &state).map_err(|e| e.to_string())
}

#[tauri::command]
fn clear_gpu_cache(state: State<'_, WorkerState>) -> Result<(), String> {
    send_to_worker(&state, &WorkerRequest::ClearGpuCache)
}

fn read_gpu_telemetry() -> GpuTelemetry {
    let output = hidden_command("nvidia-smi")
        .args([
            "--query-gpu=name,utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ])
        .output();

    let Ok(output) = output else {
        return GpuTelemetry::unavailable("nvidia-smi unavailable");
    };
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        let error = if stderr.is_empty() {
            "nvidia-smi failed".to_string()
        } else {
            stderr
        };
        return GpuTelemetry::unavailable(error);
    }
    parse_gpu_telemetry_output(&output.stdout)
}

#[tauri::command]
async fn gpu_telemetry() -> GpuTelemetry {
    tauri::async_runtime::spawn_blocking(read_gpu_telemetry)
        .await
        .unwrap_or_else(|e| GpuTelemetry::unavailable(format!("GPU telemetry task failed: {e}")))
}

#[tauri::command]
fn normalize(
    app: AppHandle,
    state: State<'_, WorkerState>,
    file: String,
    id: Option<String>,
    config: serde_json::Value,
) -> Result<String, String> {
    let id = id.unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
    send_to_worker_with_restart(
        &app,
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
    app: AppHandle,
    state: State<'_, WorkerState>,
    file: String,
    id: Option<String>,
    config: serde_json::Value,
) -> Result<String, String> {
    let id = id.unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
    send_to_worker_with_restart(
        &app,
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
        hidden_command("cmd")
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
    let output = hidden_command("ffprobe")
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
            stop_current_job,
            clear_gpu_cache,
            gpu_telemetry,
            open_path,
            reveal_in_folder,
            probe_file,
            get_logs_dir,
        ])
        .setup(|_app| Ok(()))
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::{
        debug_log_filename, dev_sidecar_binary_path, format_debug_log_line,
        is_terminal_progress_line, onedir_sidecar_path, parse_gpu_telemetry_line,
        path_text_is_under_dir, request_job_id, sanitize_debug_log_token,
        should_emit_live_terminal_line, should_restart_worker_after_send_failure,
        worker_write_failed_payload, LiveWorkerEventFilter, SIDECAR_DIR_NAME, SIDECAR_EXE_NAME,
    };

    #[test]
    fn sidecar_paths_use_onedir_layout() {
        let resource_dir = std::path::Path::new("resources");
        assert_eq!(
            onedir_sidecar_path(resource_dir),
            resource_dir.join(SIDECAR_DIR_NAME).join(SIDECAR_EXE_NAME)
        );

        let repo_root = std::path::Path::new("repo");
        assert_eq!(
            dev_sidecar_binary_path(repo_root),
            repo_root
                .join("srtforge-studio")
                .join("src-tauri")
                .join("binaries")
                .join(SIDECAR_DIR_NAME)
                .join(SIDECAR_EXE_NAME)
        );
    }

    #[test]
    fn path_text_scope_only_matches_current_sidecar_dir() {
        let sidecar_dir = std::path::Path::new(
            r"C:\Srtforge-lat\Srtforge\srtforge-studio\src-tauri\binaries\srtforge_worker",
        );

        assert!(path_text_is_under_dir(
            r"C:\Srtforge-lat\Srtforge\srtforge-studio\src-tauri\binaries\srtforge_worker\srtforge_worker.exe",
            sidecar_dir,
        ));
        assert!(!path_text_is_under_dir(
            r"C:\Srtforge\srtforge_worker\srtforge_worker.exe",
            sidecar_dir,
        ));
    }

    #[test]
    fn gpu_telemetry_csv_parser_reads_nvidia_smi_line() {
        let telemetry = parse_gpu_telemetry_line("NVIDIA GeForce RTX 4090, 37, 12100, 24564")
            .expect("valid telemetry");

        assert!(telemetry.available);
        assert_eq!(telemetry.name.as_deref(), Some("NVIDIA GeForce RTX 4090"));
        assert_eq!(telemetry.utilization_pct, Some(37));
        assert_eq!(telemetry.memory_used_mb, Some(12100));
        assert_eq!(telemetry.memory_total_mb, Some(24564));
    }

    #[test]
    fn gpu_telemetry_csv_parser_rejects_missing_memory_total() {
        assert!(parse_gpu_telemetry_line("NVIDIA GeForce RTX 4090, 37, 12100, 0").is_none());
    }

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

    #[test]
    fn terminal_progress_lines_are_forwarded_raw() {
        let separator_line = " 57%|#####7    | 102/178 [00:56<00:42,  1.79it/s]";
        let transcribe_line = "Transcribing: 1it [04:35, 275.00s/it]";

        assert!(is_terminal_progress_line(separator_line));
        assert!(is_terminal_progress_line(transcribe_line));
        assert!(should_emit_live_terminal_line(separator_line));
        assert!(should_emit_live_terminal_line(transcribe_line));
        assert!(should_emit_live_terminal_line(
            "FV4 separation is running with CUDA acceleration"
        ));
    }

    #[test]
    fn live_worker_filter_throttles_progress_updates() {
        let mut filter = LiveWorkerEventFilter::default();

        assert!(filter.should_emit(&serde_json::json!({
            "event": "progress",
            "id": "job-1",
            "stage": "separation",
            "fraction": 0.10
        })));
        assert!(!filter.should_emit(&serde_json::json!({
            "event": "progress",
            "id": "job-1",
            "stage": "separation",
            "fraction": 0.11
        })));
        assert!(filter.should_emit(&serde_json::json!({
            "event": "progress",
            "id": "job-1",
            "stage": "asr",
            "fraction": 0.12
        })));
        assert!(filter.should_emit(&serde_json::json!({
            "event": "progress",
            "id": "job-1",
            "stage": "asr",
            "fraction": 1.0
        })));
    }

    #[test]
    fn live_worker_filter_keeps_raw_progress_and_warnings() {
        let mut filter = LiveWorkerEventFilter::default();

        assert!(filter.should_emit(&serde_json::json!({
            "event": "log",
            "lvl": "info",
            "msg": "loading model"
        })));
        assert!(!filter.should_emit(&serde_json::json!({
            "event": "log",
            "lvl": "info",
            "msg": "another info line"
        })));
        assert!(filter.should_emit(&serde_json::json!({
            "event": "log",
            "lvl": "warn",
            "msg": "worker warning"
        })));
        assert!(filter.should_emit(&serde_json::json!({
            "event": "log",
            "lvl": "info",
            "msg": " 57%|#####7    | 102/178 [00:56<00:42,  2.15it/s]"
        })));
        assert!(!filter.should_emit(&serde_json::json!({
            "event": "log",
            "lvl": "info",
            "msg": " 58%|#####8    | 103/178 [00:57<00:41,  2.15it/s]"
        })));
        assert!(filter.should_emit(&serde_json::json!({
            "event": "log",
            "lvl": "info",
            "source": "pipeline-heartbeat",
            "msg": "FV4 detail: Separator model load still running after 30s"
        })));
    }

    #[test]
    fn request_job_id_reads_worker_request_id() {
        assert_eq!(
            request_job_id(r#"{"action":"transcribe","id":"job-1","file":"x.mkv"}"#),
            Some("job-1".to_string())
        );
        assert_eq!(request_job_id(r#"{"action":"shutdown"}"#), None);
        assert_eq!(request_job_id("not json"), None);
    }

    #[test]
    fn worker_write_failed_payload_fails_known_job() {
        let payload = worker_write_failed_payload(
            r#"{"action":"transcribe","id":"job-2","file":"x.mkv"}"#,
            "worker stdin write failed: broken pipe",
        );

        assert_eq!(payload["event"], "job_failed");
        assert_eq!(payload["id"], "job-2");
        assert_eq!(payload["error"], "worker stdin write failed: broken pipe");
    }

    #[test]
    fn worker_write_failed_payload_reports_termination_without_job_id() {
        let payload = worker_write_failed_payload(
            r#"{"action":"clear_gpu_cache"}"#,
            "worker stdin write failed",
        );

        assert_eq!(payload["event"], "terminated");
        assert_eq!(payload["code"], serde_json::Value::Null);
        assert_eq!(payload["error"], "worker stdin write failed");
    }

    #[test]
    fn send_failure_restart_policy_is_limited_to_dead_worker_paths() {
        assert!(should_restart_worker_after_send_failure(
            "worker not running"
        ));
        assert!(should_restart_worker_after_send_failure(
            "worker channel closed: receiving half dropped"
        ));
        assert!(!should_restart_worker_after_send_failure(
            "encode failed: bad value"
        ));
    }
}
