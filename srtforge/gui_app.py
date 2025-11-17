"""Windows 11-inspired GUI for the srtforge transcription pipeline."""

from __future__ import annotations

import importlib.resources as resources
import json
import os
import re
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, TextIO

from PySide6 import QtCore, QtGui, QtWidgets

from .config import DEFAULT_OUTPUT_SUFFIX
from .logging import LATEST_LOG, LOGS_DIR
from .settings import settings
from .win11_backdrop import apply_win11_look, get_windows_accent_qcolor


@dataclass(slots=True)
class FFmpegBinaries:
    """Resolved FFmpeg and ffprobe executables."""

    ffmpeg: Path
    ffprobe: Path


@dataclass(slots=True)
class MKVToolNixBinaries:
    """Resolved MKVToolNix executables."""

    mkvmerge: Path
    mkvpropedit: Optional[Path] = None


@dataclass(slots=True)
class WorkerOptions:
    """Options that control how the transcription worker runs."""

    prefer_gpu: bool
    embed_subtitles: bool
    burn_subtitles: bool
    cleanup_gpu: bool
    ffmpeg_bin: Optional[str]
    ffprobe_bin: Optional[str]
    embed_method: str
    mkvmerge_bin: Optional[str]
    mkvpropedit_bin: Optional[str]
    srt_title: str
    srt_language: str
    srt_default: bool
    srt_forced: bool


class DropArea(QtWidgets.QFrame):
    """A rounded drop zone that accepts media files."""

    filesDropped = QtCore.Signal(list)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("DropArea")
        self.setMinimumHeight(140)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        icon = QtWidgets.QLabel("📂")
        icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 32px;")
        layout.addWidget(icon)
        label = QtWidgets.QLabel("Drag and drop videos here")
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        layout.addWidget(label)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:  # noqa: D401 - Qt override
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:  # noqa: D401 - Qt override
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:  # noqa: D401 - Qt override
        paths: List[str] = []
        for url in event.mimeData().urls():
            local = Path(url.toLocalFile())
            if local.is_file():
                paths.append(str(local))
        if paths:
            self.filesDropped.emit(paths)
        event.acceptProposedAction()


def add_shadow(widget: QtWidgets.QWidget) -> None:
    """Add a soft drop shadow to widgets to emulate Windows 11 cards."""

    effect = QtWidgets.QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(30)
    effect.setOffset(0, 12)
    effect.setColor(QtGui.QColor(15, 23, 42, 50))
    widget.setGraphicsEffect(effect)


def _normalize_paths(paths: Iterable[str]) -> List[Path]:
    unique: List[Path] = []
    seen = set()
    for raw in paths:
        path = Path(raw).resolve()
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _expected_srt_path(media: Path) -> Path:
    output_dir = settings.paths.output_dir
    if output_dir:
        return output_dir / f"{media.stem}{DEFAULT_OUTPUT_SUFFIX}"
    return media.with_suffix(DEFAULT_OUTPUT_SUFFIX)


def _ffmpeg_directory_from_options(options: WorkerOptions) -> Optional[Path]:
    if options.ffmpeg_bin:
        return Path(options.ffmpeg_bin).resolve().parent
    return None


class StopRequested(Exception):
    """Raised when the user cancels the current operation."""


class TranscriptionWorker(QtCore.QThread):
    """Background worker that processes queued media sequentially."""

    logMessage = QtCore.Signal(str)
    progress = QtCore.Signal(int, int)
    fileStarted = QtCore.Signal(str)
    fileCompleted = QtCore.Signal(str, str)
    fileFailed = QtCore.Signal(str, str)
    queueFinished = QtCore.Signal(bool)
    runLogReady = QtCore.Signal(str)

    def __init__(self, files: Iterable[str], options: WorkerOptions, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self.files = [Path(path) for path in files]
        self.options = options
        self._stop_event = threading.Event()
        self._active_process: Optional[subprocess.Popen[str]] = None
        self._cli_qprocess: Optional[QtCore.QProcess] = None
        self._last_srt_path: Optional[Path] = None
        self._current_run_id: Optional[str] = None
        self._cli_pid: Optional[int] = None

    _RUN_ID_PATTERN = re.compile(r"Run ID[:\s]+([0-9A-Za-z-]+)")

    def request_stop(self) -> None:
        """Ask the worker to stop after the current task finishes."""

        import signal as _signal
        import os as _os
        self._stop_event.set()
        qprocess = self._cli_qprocess
        if qprocess and qprocess.state() != QtCore.QProcess.ProcessState.NotRunning:
            try:
                QtCore.QMetaObject.invokeMethod(
                    qprocess,
                    "terminate",
                    QtCore.Qt.QueuedConnection,
                )
                QtCore.QTimer.singleShot(
                    2000,
                    lambda: self._queue_cli_kill(),
                )
                if _os.name != "nt":
                    QtCore.QTimer.singleShot(
                        2200,
                        lambda: self._posix_terminate_cli_tree(force=False),
                    )
                    QtCore.QTimer.singleShot(
                        4200,
                        lambda: self._posix_terminate_cli_tree(force=True),
                    )
                if _os.name == "nt":
                    QtCore.QTimer.singleShot(
                        3500,
                        lambda: self._windows_taskkill_fallback(),
                    )
            except Exception:
                if _os.name == "nt":
                    self._windows_taskkill_fallback()
                else:
                    QtCore.QMetaObject.invokeMethod(
                        qprocess,
                        "kill",
                        QtCore.Qt.QueuedConnection,
                    )
                    self._posix_terminate_cli_tree(force=False)
        process = self._active_process
        if process and process.poll() is None:
            try:
                if _os.name == "nt":
                    # Signal the process group spawned with CREATE_NEW_PROCESS_GROUP.
                    # GUI builds often have no attached console, so the taskkill fallback
                    # below is expected to handle that scenario when CTRL_BREAK is ignored.
                    process.send_signal(_signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                    try:
                        process.wait(timeout=2)
                    except Exception:
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                else:
                    _os.killpg(process.pid, _signal.SIGTERM)  # type: ignore[attr-defined]
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.terminate()
            except Exception:
                if _os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    process.terminate()

    def _queue_cli_kill(self) -> None:
        """Post a kill request for the active QProcess without blocking the UI."""

        qprocess = self._cli_qprocess
        if not qprocess:
            return
        QtCore.QMetaObject.invokeMethod(
            qprocess,
            "kill",
            QtCore.Qt.QueuedConnection,
        )

    def _posix_terminate_cli_tree(self, *, force: bool = False) -> None:
        """Terminate the CLI process and its descendants on POSIX platforms."""

        if os.name == "nt":
            return
        pid = self._cli_pid or 0
        if pid <= 0:
            return

        sig = signal.SIGKILL if force else signal.SIGTERM

        def _children(parent: int) -> list[int]:
            try:
                output = subprocess.check_output(
                    ["pgrep", "-P", str(parent)],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                return _children_via_ps(parent)
            except subprocess.CalledProcessError:
                return []
            return [int(line.strip()) for line in output.splitlines() if line.strip()]

        def _children_via_ps(parent: int) -> list[int]:
            try:
                output = subprocess.check_output(
                    ["ps", "-eo", "pid=", "-o", "ppid="],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                return []
            result: list[int] = []
            for row in output.splitlines():
                parts = row.split()
                if len(parts) != 2:
                    continue
                child_pid, parent_pid = parts
                try:
                    if int(parent_pid) == parent:
                        result.append(int(child_pid))
                except ValueError:
                    continue
            return result

        def _kill_tree(root: int, signal_number: int) -> None:
            visited: set[int] = set()

            def _walk(node: int) -> None:
                if node <= 0 or node in visited:
                    return
                visited.add(node)
                for child in _children(node):
                    _walk(child)
                try:
                    os.kill(node, signal_number)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    pass
                except OSError:
                    pass

            _walk(root)

        threading.Thread(target=_kill_tree, args=(pid, sig), daemon=True).start()

    def _windows_taskkill_fallback(self) -> None:
        """Forcefully terminate the CLI tree on Windows if it survives termination."""

        if os.name != "nt":
            return
        if not self._cli_qprocess:
            return
        pid = self._cli_pid or 0
        if pid <= 0:
            return
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    def run(self) -> None:  # noqa: D401 - Qt override
        total = len(self.files)
        self.progress.emit(0, total)
        for index, media_path in enumerate(self.files, start=1):
            if self._stop_event.is_set():
                break
            self.fileStarted.emit(str(media_path))
            try:
                srt_path = self._run_pipeline_subprocess(media_path)
                if self._stop_event.is_set():
                    raise StopRequested
                embed_output = None
                burn_output = None
                if self.options.embed_subtitles:
                    embed_method = (self.options.embed_method or "auto").lower()
                    if embed_method not in {"auto", "ffmpeg", "mkvmerge"}:
                        embed_method = "auto"
                    suffix = media_path.suffix.lower()
                    has_ffmpeg = bool(self.options.ffmpeg_bin)
                    has_mkvmerge = bool(self.options.mkvmerge_bin)
                    if embed_method == "ffmpeg":
                        can_embed_backend = has_ffmpeg
                    elif embed_method == "mkvmerge":
                        if suffix == ".mkv" and has_mkvmerge:
                            can_embed_backend = True
                        else:
                            can_embed_backend = has_ffmpeg
                    else:  # auto
                        if suffix == ".mkv":
                            can_embed_backend = has_mkvmerge or has_ffmpeg
                        else:
                            can_embed_backend = has_ffmpeg
                    if can_embed_backend:
                        if self._stop_event.is_set():
                            raise StopRequested
                        try:
                            embed_output = self._embed_subtitles(media_path, srt_path)
                        except StopRequested:
                            raise
                        except Exception as exc:  # pragma: no cover - defensive logging
                            raise RuntimeError(f"Embed failed: {exc}") from exc
                    else:
                        self.logMessage.emit(
                            "Soft embedding skipped: no compatible backend available"
                        )
                if self.options.burn_subtitles and self.options.ffmpeg_bin:
                    if self._stop_event.is_set():
                        raise StopRequested
                    try:
                        burn_output = self._burn_subtitles(media_path, srt_path)
                    except StopRequested:
                        raise
                    except Exception as exc:  # pragma: no cover - defensive logging
                        raise RuntimeError(f"Burn failed: {exc}") from exc
                summary_parts = [str(srt_path)]
                if embed_output:
                    summary_parts.append(f"embedded → {embed_output}")
                if burn_output:
                    summary_parts.append(f"burned → {burn_output}")
                self.fileCompleted.emit(str(media_path), "; ".join(summary_parts))
            except StopRequested:
                self.fileFailed.emit(str(media_path), "Cancelled by user")
                break
            except Exception as exc:  # pragma: no cover - defensive safeguard
                self.fileFailed.emit(str(media_path), str(exc))
            self.progress.emit(index, total)
        stopped = self._stop_event.is_set()
        self.queueFinished.emit(stopped)

    # ---- helpers -----------------------------------------------------------------
    def _prepare_cli_invocation(self, media: Path) -> tuple[list[str], dict[str, str]]:
        cli_binary = locate_cli_executable()
        if cli_binary:
            command = [str(cli_binary), "run", str(media)]
        else:
            # -u => unbuffered stdout/stderr so the GUI can read lines as they appear (Python docs)
            command = [sys.executable, "-u", "-m", "srtforge", "run", str(media)]
        if not self.options.prefer_gpu:
            command.append("--cpu")
        env = os.environ.copy()
        # Also request unbuffered output via env for robustness (Python docs: PYTHONUNBUFFERED)
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("PYTHONIOENCODING", "UTF-8")
        env.setdefault("PYTHONUTF8", "1")
        ffmpeg_dir = _ffmpeg_directory_from_options(self.options)
        if ffmpeg_dir:
            env_path = env.get("PATH", "")
            env["PATH"] = os.pathsep.join(str(part) for part in (ffmpeg_dir, env_path) if part)
        return command, env

    def _process_cli_line(self, text: str) -> None:
        if not text:
            return
        match = self._RUN_ID_PATTERN.search(text)
        if match:
            run_id = match.group(1)
            if run_id and run_id != self._current_run_id:
                self._current_run_id = run_id
                self.runLogReady.emit(run_id)
        try:
            payload = json.loads(text)
        except Exception:
            return
        if isinstance(payload, dict) and payload.get("event") == "srt_written":
            candidate = Path(str(payload.get("path", ""))).expanduser()
            if candidate.exists() and candidate != self._last_srt_path:
                self._last_srt_path = candidate
                self.logMessage.emit(f"SRT ready: {candidate}")

    def _run_pipeline_qprocess(self, media: Path) -> tuple[int, str, str]:
        command, env = self._prepare_cli_invocation(media)
        self.logMessage.emit(f"Transcription: {' '.join(command)}")
        process = QtCore.QProcess()
        environment = QtCore.QProcessEnvironment()
        for key, value in env.items():
            environment.insert(key, value)
        process.setProcessEnvironment(environment)
        process.setProgram(command[0])
        process.setArguments(command[1:])
        process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.SeparateChannels)

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        stdout_buffer = ""
        stderr_buffer = ""

        def _flush_lines(buffer: str) -> str:
            if not buffer:
                return ""
            segments = buffer.splitlines(keepends=True)
            remainder = ""
            if segments and not segments[-1].endswith(("\n", "\r")):
                remainder = segments.pop()
            for segment in segments:
                text = segment.rstrip("\r\n")
                if text:
                    self._process_cli_line(text)
                    self.logMessage.emit(text)
            return remainder

        def _handle_stdout() -> None:
            nonlocal stdout_buffer
            data = process.readAllStandardOutput()
            if not data:
                return
            chunk = bytes(data).decode("utf-8", errors="replace")
            stdout_parts.append(chunk)
            stdout_buffer += chunk
            stdout_buffer = _flush_lines(stdout_buffer)

        def _handle_stderr() -> None:
            nonlocal stderr_buffer
            data = process.readAllStandardError()
            if not data:
                return
            chunk = bytes(data).decode("utf-8", errors="replace")
            stderr_parts.append(chunk)
            stderr_buffer += chunk
            stderr_buffer = _flush_lines(stderr_buffer)

        process.readyReadStandardOutput.connect(_handle_stdout)  # Qt docs: readyRead* fire as new data arrives
        process.readyReadStandardError.connect(_handle_stderr)

        loop = QtCore.QEventLoop()
        process.finished.connect(loop.quit)
        self._cli_qprocess = process
        try:
            process.start()
            if not process.waitForStarted(5000):
                error = process.errorString() or "unable to start transcription process"
                raise RuntimeError(error)
            pid = int(process.processId())
            self._cli_pid = pid or None
            if process.state() != QtCore.QProcess.ProcessState.NotRunning:
                loop.exec()
            _handle_stdout()
            _handle_stderr()
        finally:
            self._cli_qprocess = None
            self._cli_pid = None
            process.deleteLater()

        if stdout_buffer:
            text = stdout_buffer.rstrip("\r\n")
            if text:
                self._process_cli_line(text)
                self.logMessage.emit(text)
        if stderr_buffer:
            text = stderr_buffer.rstrip("\r\n")
            if text:
                self._process_cli_line(text)
                self.logMessage.emit(text)

        stdout = "".join(stdout_parts)
        stderr = "".join(stderr_parts)
        return process.exitCode(), stdout, stderr

    def _run_pipeline_subprocess(self, media: Path) -> Path:
        self._last_srt_path = None
        self._current_run_id = None
        use_qprocess = os.getenv("SRTFORGE_USE_QPROCESS", "1") != "0"
        if use_qprocess:
            return_code, stdout, stderr = self._run_pipeline_qprocess(media)
        else:
            command, env = self._prepare_cli_invocation(media)
            return_code, stdout, stderr = self._run_command(
                command,
                "Transcription",
                env=env,
                check=False,
            )
        if return_code == 0:
            if self._last_srt_path and self._last_srt_path.exists():
                return self._last_srt_path
            import re as _re

            # Prefer a structured event line emitted by the CLI
            for line in (stdout or "").splitlines():
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except Exception:
                    pass
                else:
                    if isinstance(payload, dict) and payload.get("event") == "srt_written":
                        candidate = Path(str(payload.get("path", ""))).expanduser()
                        if candidate.exists():
                            return candidate

            # Fallback to legacy human-readable log lines
            for line in (stdout or "").splitlines():
                m = _re.search(r"SRT written to\s+(?P<path>.*?\.srt)\s*$", line)
                if m:
                    candidate = Path(m.group("path")).expanduser()
                    if candidate.exists():
                        return candidate
            # Fallback to expected path derived from settings
            output_path = _expected_srt_path(media)
            if output_path.exists():
                return output_path
            raise RuntimeError("SRT output missing")
        if self._stop_event.is_set():
            raise StopRequested
        if return_code == 2:
            reason = (stderr or stdout or "Pipeline skipped").strip()
            raise RuntimeError(reason or "Pipeline skipped")
        message = (stderr or stdout or f"Pipeline exited with code {return_code}").strip()
        raise RuntimeError(message)

    def _embed_subtitles(self, media: Path, subtitles: Path) -> Path:
        ext = media.suffix.lower()
        method = (self.options.embed_method or "auto").lower()
        if method not in {"auto", "ffmpeg", "mkvmerge"}:
            method = "auto"
        can_use_mkvmerge = bool(self.options.mkvmerge_bin)
        if (
            ext == ".mkv"
            and method in {"auto", "mkvmerge"}
            and can_use_mkvmerge
        ):
            return self._embed_subtitles_mkvmerge(media, subtitles)
        if method == "mkvmerge" and not can_use_mkvmerge:
            self.logMessage.emit("MKVToolNix not available, falling back to FFmpeg")
        return self._embed_subtitles_ffmpeg(media, subtitles)

    def _embed_subtitles_ffmpeg(self, media: Path, subtitles: Path) -> Path:
        output = media.with_name(f"{media.stem}_subbed{media.suffix}")
        codec = "mov_text" if media.suffix.lower() in {".mp4", ".m4v", ".mov"} else "subrip"
        subtitle_index = self._count_subtitle_streams(media)
        title = self.options.srt_title or "srtforge (English)"
        language = self.options.srt_language or "eng"
        command = [
            self.options.ffmpeg_bin or "ffmpeg",
            "-y",
            "-i",
            str(media),
            "-i",
            str(subtitles),
            # Explicitly select all streams from the media (input 0) and the
            # first subtitle stream from the SRT input (input 1).
            "-map",
            "0",
            "-map",
            "1:s:0",
            "-c",
            "copy",
            "-c:s",
            "copy",
            f"-c:s:{subtitle_index}",
            codec,
            f"-disposition:s:{subtitle_index}",
            "0",
            f"-metadata:s:s:{subtitle_index}",
            f"title={title}",
            f"-metadata:s:s:{subtitle_index}",
            f"language={language}",
            str(output),
        ]
        self._run_command(command, "Embed subtitles (ffmpeg)")
        return output

    def _embed_subtitles_mkvmerge(self, media: Path, subtitles: Path) -> Path:
        output = media.with_name(f"{media.stem}_subbed{media.suffix}")
        mkvmerge = self.options.mkvmerge_bin or "mkvmerge"
        title = self.options.srt_title or "srtforge (English)"
        language = self.options.srt_language or "eng"
        default_flag = "1" if self.options.srt_default else "0"
        forced_flag = "1" if self.options.srt_forced else "0"
        command = [
            mkvmerge,
            "-o",
            str(output),
            str(media),
            "--track-name",
            f"0:{title}",
            "--language",
            f"0:{language}",
            "--default-track-flag",
            f"0:{default_flag}",
            "--forced-display-flag",
            f"0:{forced_flag}",
            str(subtitles),
        ]
        self._run_command(command, "Embed subtitles (mkvmerge)")
        return output

    def _count_subtitle_streams(self, media: Path) -> int:
        """Return how many subtitle streams already exist in the media file."""

        ffprobe = self.options.ffprobe_bin or "ffprobe"
        command = [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "s",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(media),
        ]
        return_code, stdout, stderr = self._run_command(command, "Probe subtitle streams", check=False)
        if return_code != 0:
            if self._stop_event.is_set():
                raise StopRequested
            message = stderr.strip() or stdout.strip() or "Unable to inspect subtitle streams"
            raise RuntimeError(message)
        return sum(1 for line in stdout.splitlines() if line.strip())

    def _burn_subtitles(self, media: Path, subtitles: Path) -> Path:
        output = media.with_name(f"{media.stem}_burned{media.suffix}")
        subtitles_arg = _escape_subtitles_filter_path(subtitles)
        mov_flags: list[str] = []
        if output.suffix.lower() in {".mp4", ".m4v", ".mov"}:
            mov_flags = ["-movflags", "+faststart"]
        command = [
            self.options.ffmpeg_bin or "ffmpeg",
            "-y",
            "-i",
            str(media),
            "-vf",
            f"subtitles='{subtitles_arg}':force_style='Fontsize=24'",
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "medium",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            *mov_flags,
            str(output),
        ]
        self._run_command(command, "Burn subtitles")
        return output

    def _run_command(
        self,
        command: List[str],
        description: str,
        *,
        env: Optional[dict[str, str]] = None,
        check: bool = True,
    ) -> tuple[int, str, str]:
        self.logMessage.emit(f"{description}: {' '.join(command)}")
        kwargs: dict[str, object] = {}
        if os.name == "nt":
            # Create a new process group so we can send CTRL_BREAK to the whole tree
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            # POSIX: start a new session safely (thread-friendly)
            kwargs["start_new_session"] = True

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,  # text mode => str lines (required for line buffering per Python docs)
            bufsize=1,  # request line-buffered pipes where possible for responsive logs
            encoding="utf-8",
            errors="replace",
            env=env,
            **kwargs,
        )
        self._active_process = process
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def _pump(stream: Optional[TextIO], sink: list[str]) -> None:
            if stream is None:
                return
            try:
                for line in iter(stream.readline, ""):
                    if not line:
                        break
                    text = line.rstrip("\r\n")
                    if text:
                        self._process_cli_line(text)
                        self.logMessage.emit(text)
                    sink.append(line)
            except Exception:
                pass

        t_out = threading.Thread(target=_pump, args=(process.stdout, stdout_lines), daemon=True)
        t_err = threading.Thread(target=_pump, args=(process.stderr, stderr_lines), daemon=True)
        t_out.start()
        t_err.start()
        try:
            return_code = process.wait()
        finally:
            # Ensure the pump threads finish draining any remaining output before
            # we build the combined stdout/stderr strings.
            t_out.join()
            t_err.join()
            self._active_process = None

        stdout = "".join(stdout_lines)
        stderr = "".join(stderr_lines)
        if check and return_code != 0:
            if self._stop_event.is_set():
                raise StopRequested
            message = stderr.strip() or stdout.strip() or f"{description} failed"
            raise RuntimeError(message)
        return return_code or 0, stdout or "", stderr or ""


def _escape_subtitles_filter_path(path: Path) -> str:
    """Escape characters that are special to FFmpeg's subtitles filter."""

    escaped = path.as_posix()
    # Escape path separators, drive-letter colons, and embedded quotes so the
    # string remains valid when wrapped in single quotes within the -vf value.
    replacements = {
        "\\": r"\\",
        ":": r"\:",
        "'": r"\'",
    }
    for target, replacement in replacements.items():
        if target in escaped:
            escaped = escaped.replace(target, replacement)
    return escaped


def locate_ffmpeg_binaries() -> Optional[FFmpegBinaries]:
    """Attempt to find FFmpeg binaries bundled next to the executable."""

    candidates: List[Path] = []
    env_dir = os.environ.get("SRTFORGE_FFMPEG_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    bundle_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidates.append(bundle_dir / "ffmpeg")
    candidates.append(Path(sys.executable).resolve().parent / "ffmpeg")
    candidates.append(Path.cwd() / "ffmpeg")
    candidates.append(Path(__file__).resolve().parent / ".." / "ffmpeg")

    seen = set()
    filtered: List[Path] = []
    for path in candidates:
        normalized = path.resolve()
        if normalized in seen:
            continue
        seen.add(normalized)
        filtered.append(normalized)

    exe_suffix = ".exe" if os.name == "nt" else ""
    for base in filtered:
        ffmpeg_path = base / f"ffmpeg{exe_suffix}"
        ffprobe_path = base / f"ffprobe{exe_suffix}"
        if ffmpeg_path.exists() and ffprobe_path.exists():
            return FFmpegBinaries(ffmpeg_path, ffprobe_path)

    ffmpeg_which = shutil_which("ffmpeg")
    ffprobe_which = shutil_which("ffprobe")
    if ffmpeg_which and ffprobe_which:
        return FFmpegBinaries(Path(ffmpeg_which), Path(ffprobe_which))
    return None


def locate_mkvtoolnix_binaries() -> Optional[MKVToolNixBinaries]:
    """Find mkvmerge (and optionally mkvpropedit) using common locations."""

    candidates: list[Path] = []
    env_dir = os.environ.get("SRTFORGE_MKV_DIR") or os.environ.get("SRTFORGE_MKVTOOLNIX_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    exe_dir = Path(sys.executable).resolve().parent
    bundle_dir = Path(getattr(sys, "_MEIPASS", exe_dir))
    candidates += [
        bundle_dir / "mkvtoolnix",
        exe_dir / "mkvtoolnix",
        Path.cwd() / "mkvtoolnix",
    ]

    seen: set[Path] = set()
    filtered: list[Path] = []
    for candidate in candidates:
        normalized = candidate.resolve()
        if normalized in seen:
            continue
        seen.add(normalized)
        filtered.append(normalized)

    exe_suffix = ".exe" if os.name == "nt" else ""
    for base in filtered:
        merge = base / f"mkvmerge{exe_suffix}"
        prop = base / f"mkvpropedit{exe_suffix}"
        if merge.exists():
            return MKVToolNixBinaries(merge, prop if prop.exists() else None)

    from shutil import which

    merge_p = which("mkvmerge")
    if merge_p:
        prop_p = which("mkvpropedit")
        return MKVToolNixBinaries(Path(merge_p), Path(prop_p) if prop_p else None)
    return None


def locate_cli_executable() -> Optional[Path]:
    """Return a packaged CLI binary if it exists alongside the GUI."""

    env_cli = os.environ.get("SRTFORGE_CLI_BIN")
    if env_cli:
        candidate = Path(env_cli).expanduser()
        if candidate.exists():
            return candidate

    suffix = ".exe" if os.name == "nt" else ""
    cli_name = f"SrtforgeCLI{suffix}"
    executable = Path(sys.executable).resolve()
    cli_candidate = executable.with_name(cli_name)
    if cli_candidate.exists():
        return cli_candidate
    return None


def shutil_which(binary: str) -> Optional[str]:
    """Local helper to avoid importing ``shutil`` globally."""

    from shutil import which

    return which(binary)


def cleanup_gpu_memory() -> None:
    """Free GPU caches if torch with CUDA support is available."""

    try:
        import torch  # type: ignore
    except Exception:  # pragma: no cover - optional dependency
        return
    if torch.cuda.is_available():  # pragma: no cover - depends on runtime hardware
        torch.cuda.empty_cache()


class LogTailer(QtCore.QObject):
    """Poll structured run logs and forward step markers to the GUI."""

    _MARKERS = ("START ", "END ", "Run ", "ASR device:")

    def __init__(self, callback: Callable[[str], None], parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self._callback = callback
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._poll)
        self._offset = 0
        self._buffer = ""
        self._active = False
        self._target_path = LATEST_LOG

    def start(self) -> None:
        self._timer.stop()
        self._active = True
        self._target_path = LATEST_LOG
        try:
            self._offset = self._target_path.stat().st_size
        except OSError:
            self._offset = 0
        self._buffer = ""
        self._timer.start()

    def stop(self) -> None:
        self._active = False
        self._timer.stop()
        self._offset = 0
        self._buffer = ""

    def set_run_id(self, run_id: str) -> None:
        if not run_id:
            return
        new_target = LOGS_DIR / f"{run_id}.log"
        if self._target_path == new_target:
            if self._active and not self._timer.isActive():
                self._timer.start()
            return
        self._timer.stop()
        self._target_path = new_target
        self._offset = 0
        self._buffer = ""
        if self._active:
            self._timer.start()

    def _poll(self) -> None:
        try:
            size = self._target_path.stat().st_size
        except OSError:
            return
        if size < self._offset:
            self._offset = 0
        try:
            with self._target_path.open("r", encoding="utf8") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
                self._offset = handle.tell()
        except OSError:
            return
        if not chunk:
            return
        text = self._buffer + chunk
        lines = text.splitlines(keepends=True)
        self._buffer = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            self._buffer = lines.pop()
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            body = line.split("] ", 1)[1] if "] " in line else line
            if any(marker in body for marker in self._MARKERS):
                self._callback(body)


class MainWindow(QtWidgets.QMainWindow):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("srtforge Studio")
        self.setMinimumSize(960, 640)
        self.setObjectName("MainWindow")
        self._worker: Optional[TranscriptionWorker] = None
        self._log_tailer: Optional[LogTailer] = None
        self.ffmpeg_paths = locate_ffmpeg_binaries()
        self.mkv_paths = locate_mkvtoolnix_binaries()
        self._build_ui()
        self._log_tailer = LogTailer(self._append_log, self)
        self._apply_styles()
        self._update_ffmpeg_status()
        apply_win11_look(self)

    # ---- UI construction ---------------------------------------------------------
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setSpacing(16)
        header = QtWidgets.QLabel("Windows 11-style subtitle studio for srtforge")
        header.setObjectName("HeaderLabel")
        header.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        self.drop_area = DropArea()
        self.drop_area.filesDropped.connect(self._handle_dropped_files)
        layout.addWidget(self.drop_area)
        add_shadow(self.drop_area)

        queue_group = QtWidgets.QGroupBox("Transcription queue")
        queue_layout = QtWidgets.QHBoxLayout(queue_group)
        self.queue_list = QtWidgets.QListWidget()
        self.queue_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        queue_layout.addWidget(self.queue_list)
        queue_buttons = QtWidgets.QVBoxLayout()
        add_button = QtWidgets.QPushButton("Add files…")
        add_button.clicked.connect(self._open_file_dialog)
        remove_button = QtWidgets.QPushButton("Remove selected")
        remove_button.clicked.connect(self._remove_selected_items)
        clear_button = QtWidgets.QPushButton("Clear queue")
        clear_button.clicked.connect(self._clear_queue)
        for button in (add_button, remove_button, clear_button):
            button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            queue_buttons.addWidget(button)
        queue_buttons.addStretch()
        queue_layout.addLayout(queue_buttons)
        layout.addWidget(queue_group)
        add_shadow(queue_group)

        options_group = QtWidgets.QGroupBox("Processing options")
        options_layout = QtWidgets.QGridLayout(options_group)
        device_label = QtWidgets.QLabel("Device")
        self.device_combo = QtWidgets.QComboBox()
        self.device_combo.addItem("Use GPU", True)
        self.device_combo.addItem("CPU only", False)
        options_layout.addWidget(device_label, 0, 0)
        options_layout.addWidget(self.device_combo, 0, 1)
        self.embed_checkbox = QtWidgets.QCheckBox("Embed subtitles (soft track)")
        method_label = QtWidgets.QLabel("Soft-embed method")
        self.embed_method_combo = QtWidgets.QComboBox()
        self.embed_method_combo.addItem("Auto (mkvmerge for MKV, ffmpeg otherwise)", "auto")
        self.embed_method_combo.addItem("FFmpeg", "ffmpeg")
        self.embed_method_combo.addItem("MKVToolNix (mkvmerge)", "mkvmerge")
        options_layout.addWidget(method_label, 1, 2)
        options_layout.addWidget(self.embed_method_combo, 1, 3)
        self.title_edit = QtWidgets.QLineEdit("srtforge (English)")
        self.lang_edit = QtWidgets.QLineEdit("eng")
        options_layout.addWidget(QtWidgets.QLabel("Track title"), 2, 2)
        options_layout.addWidget(self.title_edit, 2, 3)
        options_layout.addWidget(QtWidgets.QLabel("Track language"), 3, 2)
        options_layout.addWidget(self.lang_edit, 3, 3)
        self.default_checkbox = QtWidgets.QCheckBox("Set as default track")
        self.forced_checkbox = QtWidgets.QCheckBox("Mark as forced")
        options_layout.addWidget(self.default_checkbox, 4, 2)
        options_layout.addWidget(self.forced_checkbox, 4, 3)
        self.burn_checkbox = QtWidgets.QCheckBox("Burn subtitles (hard sub)")
        self.cleanup_checkbox = QtWidgets.QCheckBox("Free GPU memory when stopping")
        options_layout.addWidget(self.embed_checkbox, 1, 0, 1, 2)
        options_layout.addWidget(self.burn_checkbox, 2, 0, 1, 2)
        options_layout.addWidget(self.cleanup_checkbox, 3, 0, 1, 2)
        self.embed_checkbox.toggled.connect(self._update_embed_controls)
        layout.addWidget(options_group)
        add_shadow(options_group)
        self._update_embed_controls()

        progress_row = QtWidgets.QHBoxLayout()
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_label = QtWidgets.QLabel("Idle")
        progress_row.addWidget(self.progress_bar)
        progress_row.addWidget(self.progress_label)
        layout.addLayout(progress_row)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(180)
        self.log_view.setMaximumBlockCount(10000)
        layout.addWidget(self.log_view)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch()
        self.start_button = QtWidgets.QPushButton("Start")
        self.start_button.clicked.connect(self._start_processing)
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.clicked.connect(self._stop_processing)
        self.stop_button.setEnabled(False)
        pointer_cursor = QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        for button in (self.start_button, self.stop_button):
            button.setCursor(pointer_cursor)
            button_row.addWidget(button)
        layout.addLayout(button_row)

        self.ffmpeg_status = QtWidgets.QLabel()
        self.ffmpeg_status.setWordWrap(True)
        layout.addWidget(self.ffmpeg_status)

        self.setCentralWidget(central)

    def _apply_styles(self) -> None:
        accent = get_windows_accent_qcolor() or QtGui.QColor("#2563eb")
        palette = self.palette()
        palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#f5f6f8"))
        palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#ffffff"))
        palette.setColor(QtGui.QPalette.ColorRole.Highlight, accent)
        palette.setColor(QtGui.QPalette.ColorRole.Button, accent)
        palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor("#ffffff"))
        self.setPalette(palette)

        stylesheet = self._load_win11_stylesheet(accent)
        if stylesheet:
            self.setStyleSheet(stylesheet)

    def _load_win11_stylesheet(self, accent: QtGui.QColor) -> Optional[str]:
        try:
            data = resources.files("srtforge.assets.styles").joinpath("win11.qss").read_text(encoding="utf-8")
        except Exception:  # pragma: no cover - packaging guard
            return None
        lighter = QtGui.QColor(accent)
        lighter = lighter.lighter(115)
        return (
            data.replace("{ACCENT_COLOR}", accent.name())
            .replace("{ACCENT_COLOR_LIGHT}", lighter.name())
        )

    # ---- runtime helpers ---------------------------------------------------------
    def _update_ffmpeg_status(self) -> None:
        messages = []
        if self.ffmpeg_paths:
            messages.append(f"FFmpeg detected at {self.ffmpeg_paths.ffmpeg.parent}")
        else:
            messages.append(
                "FFmpeg binaries not found. Place ffmpeg/ffprobe next to the executable or set "
                "SRTFORGE_FFMPEG_DIR."
            )
            self.burn_checkbox.setEnabled(False)
        if self.mkv_paths:
            messages.append(f"MKVToolNix: {self.mkv_paths.mkvmerge.parent}")
        else:
            messages.append(
                "MKVToolNix (mkvmerge) not found. Place binaries next to the executable or set "
                "SRTFORGE_MKV_DIR."
            )
        has_embed_backend = (self.ffmpeg_paths is not None) or (self.mkv_paths is not None)
        self.embed_checkbox.setEnabled(has_embed_backend and (self._worker is None))
        if not has_embed_backend:
            messages.append("Soft embedding disabled until FFmpeg or MKVToolNix is available.")
        self.ffmpeg_status.setText("\n".join(messages))
        self._update_embed_controls()

    def _handle_dropped_files(self, files: list) -> None:
        self._add_files_to_queue(files)

    def _open_file_dialog(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Select media files")
        if files:
            self._add_files_to_queue(files)

    def _add_files_to_queue(self, files: Iterable[str]) -> None:
        existing = {
            Path(self.queue_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole))
            for i in range(self.queue_list.count())
        }
        for path in _normalize_paths(files):
            if path in existing:
                continue
            item = QtWidgets.QListWidgetItem(str(path))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, str(path))
            self.queue_list.addItem(item)
            existing.add(path)
        self._update_start_state()

    def _remove_selected_items(self) -> None:
        for item in self.queue_list.selectedItems():
            row = self.queue_list.row(item)
            self.queue_list.takeItem(row)
        self._update_start_state()

    def _clear_queue(self) -> None:
        self.queue_list.clear()
        self._update_start_state()

    def _update_start_state(self) -> None:
        has_items = self.queue_list.count() > 0
        self.start_button.setEnabled(has_items and not self._worker)

    def _update_embed_controls(self) -> None:
        enabled = (
            self.embed_checkbox.isEnabled()
            and self.embed_checkbox.isChecked()
            and self._worker is None
        )
        widgets = (
            self.embed_method_combo,
            self.title_edit,
            self.lang_edit,
            self.default_checkbox,
            self.forced_checkbox,
        )
        for widget in widgets:
            widget.setEnabled(enabled)

    def _start_processing(self) -> None:
        if self._worker:
            return
        files = [self.queue_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole) for i in range(self.queue_list.count())]
        if not files:
            return
        prefer_gpu = bool(self.device_combo.currentData())
        embed_method = str(self.embed_method_combo.currentData())
        options = WorkerOptions(
            prefer_gpu=prefer_gpu,
            embed_subtitles=self.embed_checkbox.isChecked(),
            burn_subtitles=self.burn_checkbox.isChecked(),
            cleanup_gpu=self.cleanup_checkbox.isChecked(),
            ffmpeg_bin=str(self.ffmpeg_paths.ffmpeg) if self.ffmpeg_paths else None,
            ffprobe_bin=str(self.ffmpeg_paths.ffprobe) if self.ffmpeg_paths else None,
            embed_method=embed_method,
            mkvmerge_bin=str(self.mkv_paths.mkvmerge) if self.mkv_paths else None,
            mkvpropedit_bin=(
                str(self.mkv_paths.mkvpropedit) if (self.mkv_paths and self.mkv_paths.mkvpropedit) else None
            ),
            srt_title=self.title_edit.text().strip() or "srtforge (English)",
            srt_language=self.lang_edit.text().strip() or "eng",
            srt_default=self.default_checkbox.isChecked(),
            srt_forced=self.forced_checkbox.isChecked(),
        )
        self._worker = TranscriptionWorker(files, options)
        self._worker.logMessage.connect(self._append_log)
        self._worker.progress.connect(self._update_progress)
        self._worker.fileStarted.connect(self._on_file_started)
        self._worker.fileCompleted.connect(self._on_file_completed)
        self._worker.fileFailed.connect(self._on_file_failed)
        self._worker.queueFinished.connect(self._on_queue_finished)
        self._worker.runLogReady.connect(self._handle_run_log_ready)
        self._worker.start()
        self._append_log("Started processing queue")
        self._set_running_state(True)

    def _stop_processing(self) -> None:
        if not self._worker:
            return
        self._append_log("Stopping current task…")
        self._worker.request_stop()

    def _set_running_state(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.queue_list.setEnabled(not running)
        self.device_combo.setEnabled(not running)
        has_embed_backend = (self.ffmpeg_paths is not None) or (self.mkv_paths is not None)
        self.embed_checkbox.setEnabled(has_embed_backend and not running)
        self.burn_checkbox.setEnabled((self.ffmpeg_paths is not None) and not running)
        self._update_embed_controls()

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _update_progress(self, current: int, total: int) -> None:
        if total <= 0:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
        self.progress_label.setText(f"{current}/{total} files" if total > 0 else "Working…")

    def _on_file_started(self, path: str) -> None:
        self.progress_bar.setRange(0, 0)  # Qt docs: 0/0 switches to indeterminate/busy state
        if self._log_tailer:
            self._log_tailer.start()
        self.progress_label.setText("Working…")
        self._append_log(f"Processing {path}")

    def _on_file_completed(self, media: str, summary: str) -> None:
        self.progress_bar.setRange(0, max(1, self.queue_list.count()))
        if self._log_tailer:
            self._log_tailer.stop()
        self._append_log(f"✅ {media}: {summary}")

    def _on_file_failed(self, media: str, reason: str) -> None:
        self.progress_bar.setRange(0, max(1, self.queue_list.count()))
        if self._log_tailer:
            self._log_tailer.stop()
        self._append_log(f"⚠️ {media}: {reason}")

    def _handle_run_log_ready(self, run_id: str) -> None:
        if self._log_tailer:
            self._log_tailer.set_run_id(run_id)

    def _on_queue_finished(self, stopped: bool) -> None:
        self._append_log("Queue cancelled" if stopped else "All files processed")
        if self._log_tailer:
            self._log_tailer.stop()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Idle")
        self._worker = None
        self._set_running_state(False)
        self._update_start_state()
        if self.cleanup_checkbox.isChecked() and bool(self.device_combo.currentData()):
            cleanup_gpu_memory()
            self._append_log("GPU cache cleared")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: D401 - Qt override
        if self._worker:
            self._worker.request_stop()
            self._worker.wait(2000)
        super().closeEvent(event)


def main() -> None:
    """Entry point used by ``srtforge-gui``."""

    QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_EnableHighDpiScaling)
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":  # pragma: no cover - manual launch helper
    main()
