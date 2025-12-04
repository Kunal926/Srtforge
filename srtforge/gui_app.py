"""Windows 11-inspired GUI for the srtforge transcription pipeline."""

from __future__ import annotations

import importlib.resources as resources
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Callable, Iterable, List, Optional, TextIO

import yaml

from PySide6 import QtCore, QtGui, QtWidgets

from .config import DEFAULT_OUTPUT_SUFFIX
from .logging import LATEST_LOG, LOGS_DIR
from .settings import settings, CONFIG_ENV_VAR
from .win11_backdrop import apply_win11_look, get_windows_accent_qcolor


@dataclass(slots=True)
class FFmpegBinaries:
    """Resolved FFmpeg and ffprobe executables."""

    ffmpeg: Path
    ffprobe: Path


@dataclass(slots=True)
class MKVToolNixBinaries:
    """Resolved mkvmerge executable."""

    mkvmerge: Path


@dataclass(slots=True)
class WorkerOptions:
    """Options that control how the transcription worker runs."""

    prefer_gpu: bool
    embed_subtitles: bool
    burn_subtitles: bool
    cleanup_gpu: bool
    ffmpeg_bin: Optional[str]
    ffprobe_bin: Optional[str]
    soft_embed_method: str  # "auto" | "mkvmerge" | "ffmpeg"
    mkvmerge_bin: Optional[str]
    srt_title: str
    srt_language: str
    srt_default: bool
    srt_forced: bool
    # NEW: overwrite source container instead of creating *_subbed.*
    soft_embed_overwrite_source: bool = False
    # NEW: override global output_dir and put the .srt next to the media file
    place_srt_next_to_media: bool = False
    config_path: Optional[str] = None


def add_shadow(widget: QtWidgets.QWidget) -> None:
    """Add a soft drop shadow to widgets to emulate Windows 11 cards."""

    effect = QtWidgets.QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(30)
    effect.setOffset(0, 12)
    effect.setColor(QtGui.QColor(15, 23, 42, 50))
    widget.setGraphicsEffect(effect)


class QueueItemDelegate(QtWidgets.QStyledItemDelegate):
    """
    Custom delegate for the queue list that removes the inner focus rectangle.

    This gets rid of the 'double box' effect where you see a second
    thinner rectangle inside the selected row.
    """

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:  # type: ignore[override]
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        # Remove the focus state so the style doesn't draw a focus rect
        opt.state &= ~QtWidgets.QStyle.StateFlag.State_HasFocus

        style = opt.widget.style() if opt.widget else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)


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
    fileStarted = QtCore.Signal(str)
    fileCompleted = QtCore.Signal(str, str)
    fileFailed = QtCore.Signal(str, str)
    queueFinished = QtCore.Signal(bool)
    runLogReady = QtCore.Signal(str)
    etaMeasured = QtCore.Signal(str, float, float, bool)

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
        self._timer_origin: float = time.perf_counter()
        self._file_start_ts: float = 0.0
        self._file_media_duration: float = 0.0

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

    def _log_timing(self, label: str) -> None:
        """Emit a timestamped log line for latency investigation."""

        elapsed = time.perf_counter() - getattr(self, "_timer_origin", time.perf_counter())
        self.logMessage.emit(f"[timer {elapsed:7.2f}s] {label}")

    def _probe_media_duration_seconds(self, media: Path) -> float:
        """Use ffprobe to estimate the duration of ``media`` in seconds."""

        ffprobe = self.options.ffprobe_bin or "ffprobe"
        try:
            out = subprocess.check_output(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    str(media),
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            return float((out or "0").strip() or 0.0)
        except Exception:
            return 0.0

    def run(self) -> None:  # noqa: D401 - Qt override
        total = len(self.files)
        self._timer_origin = time.perf_counter()
        self._log_timing("queue started")
        for index, media_path in enumerate(self.files, start=1):
            if self._stop_event.is_set():
                break
            self.fileStarted.emit(str(media_path))
            try:
                self._file_start_ts = time.perf_counter()
                self._file_media_duration = self._probe_media_duration_seconds(media_path)
                self._log_timing(f"{media_path.name}: pipeline start")
                srt_path = self._run_pipeline_subprocess(media_path)
                self._log_timing(f"{media_path.name}: pipeline finished")
                if self._stop_event.is_set():
                    raise StopRequested
                embed_output = None
                burn_output = None
                if self.options.embed_subtitles and (
                    self.options.ffmpeg_bin or self.options.mkvmerge_bin or _find_mkvmerge()
                ):
                    embed_method = (self.options.soft_embed_method or "auto").lower()
                    if embed_method not in {"auto", "ffmpeg", "mkvmerge"}:
                        embed_method = "auto"
                    suffix = media_path.suffix.lower()
                    has_ffmpeg = bool(
                        (self.options.ffmpeg_bin or which("ffmpeg"))
                        and (self.options.ffprobe_bin or which("ffprobe"))
                    )
                    has_mkvmerge = bool(self.options.mkvmerge_bin or _find_mkvmerge())
                    if embed_method == "ffmpeg" and not has_ffmpeg:
                        embed_method = "auto"
                    if embed_method == "mkvmerge" and not has_mkvmerge:
                        embed_method = "auto"
                    use_mkvmerge = (
                        embed_method == "mkvmerge"
                        or (embed_method == "auto" and suffix in {".mkv", ".webm"} and has_mkvmerge)
                    )
                    if self._stop_event.is_set():
                        raise StopRequested
                    try:
                        if use_mkvmerge:
                            self._log_timing(f"{media_path.name}: mkvmerge embed start")
                            embed_output = self._embed_subtitles_mkvmerge(media_path, srt_path)
                            self._log_timing(f"{media_path.name}: mkvmerge embed done")
                        elif has_ffmpeg:
                            self._log_timing(f"{media_path.name}: ffmpeg embed start")
                            embed_output = self._embed_subtitles_ffmpeg(media_path, srt_path)
                            self._log_timing(f"{media_path.name}: ffmpeg embed done")
                        else:
                            self.logMessage.emit(
                                "Soft embedding skipped: no compatible backend available"
                            )
                    except StopRequested:
                        raise
                    except Exception as exc:  # pragma: no cover - defensive logging
                        raise RuntimeError(f"Embed failed: {exc}") from exc
                if self.options.burn_subtitles and self.options.ffmpeg_bin:
                    if self._stop_event.is_set():
                        raise StopRequested
                    try:
                        self._log_timing(f"{media_path.name}: burn start")
                        burn_output = self._burn_subtitles(media_path, srt_path)
                        self._log_timing(f"{media_path.name}: burn done")
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
                runtime_s = max(0.0, time.perf_counter() - self._file_start_ts)
                self.etaMeasured.emit(
                    str(media_path),
                    float(runtime_s),
                    float(self._file_media_duration),
                    bool(self.options.prefer_gpu),
                )
            except StopRequested:
                self.fileFailed.emit(str(media_path), "Cancelled by user")
                break
            except Exception as exc:  # pragma: no cover - defensive safeguard
                self.fileFailed.emit(str(media_path), str(exc))
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

        # NEW: force the SRT to live next to the media file, matching the Lua script
        if getattr(self.options, "place_srt_next_to_media", False):
            output_path = media.with_suffix(DEFAULT_OUTPUT_SUFFIX)
            command.extend(["--output", str(output_path)])

        if not self.options.prefer_gpu:
            command.append("--cpu")
        env = os.environ.copy()
        # Also request unbuffered output via env for robustness (Python docs: PYTHONUNBUFFERED)
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("PYTHONIOENCODING", "UTF-8")
        env.setdefault("PYTHONUTF8", "1")
        if self.options.config_path:
            env[CONFIG_ENV_VAR] = str(self.options.config_path)
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
            # NEW: Fallback – either use media location or the configured output_dir
            if getattr(self.options, "place_srt_next_to_media", False):
                output_path = media.with_suffix(DEFAULT_OUTPUT_SUFFIX)
            else:
                # Existing behavior: derive from settings.paths.output_dir
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

    def _embed_subtitles_ffmpeg(self, media: Path, subtitles: Path) -> Path:
        overwrite_source = bool(getattr(self.options, "soft_embed_overwrite_source", False))

        if overwrite_source:
            # Write to a temporary file in the same directory, then atomically
            # replace the original container once the command succeeds.
            fd, tmp_path_str = tempfile.mkstemp(
                prefix=f"{media.stem}_srtforge_embed_",
                suffix=media.suffix,
                dir=str(media.parent),
            )
            os.close(fd)
            output = Path(tmp_path_str)
        else:
            output = media.with_name(f"{media.stem}_subbed{media.suffix}")

        codec = "mov_text" if media.suffix.lower() in {".mp4", ".m4v", ".mov"} else "subrip"
        subtitle_index = self._count_subtitle_streams(media)
        title = self.options.srt_title or "Srtforge (English)"
        language = self.options.srt_language or "eng"

        disposition_flags: list[str] = []
        if self.options.srt_default:
            disposition_flags.append("default")
        if self.options.srt_forced:
            disposition_flags.append("forced")
        disposition_value = "+".join(disposition_flags) if disposition_flags else "0"

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
            disposition_value,
            f"-metadata:s:s:{subtitle_index}",
            f"title={title}",
            f"-metadata:s:s:{subtitle_index}",
            f"language={language}",
            str(output),
        ]

        try:
            self._run_command(command, "Embed subtitles (ffmpeg)")
        except Exception:
            if overwrite_source and output.exists():
                try:
                    output.unlink()
                except OSError:
                    pass
            raise

        if overwrite_source:
            try:
                os.replace(output, media)
            except OSError as exc:
                if output.exists():
                    try:
                        output.unlink()
                    except OSError:
                        pass
                raise RuntimeError(
                    f"Failed to overwrite original media file with embedded version: {exc}"
                ) from exc
            return media

        return output

    def _embed_subtitles_mkvmerge(self, media: Path, subtitles: Path) -> Path:
        if media.suffix.lower() not in {".mkv", ".webm"}:
            return self._embed_subtitles_ffmpeg(media, subtitles)

        overwrite_source = bool(getattr(self.options, "soft_embed_overwrite_source", False))

        if overwrite_source:
            fd, tmp_path_str = tempfile.mkstemp(
                prefix=f"{media.stem}_srtforge_embed_",
                suffix=media.suffix,
                dir=str(media.parent),
            )
            os.close(fd)
            output = Path(tmp_path_str)
        else:
            output = media.with_name(f"{media.stem}_subbed{media.suffix}")
        mkvmerge = self.options.mkvmerge_bin or _find_mkvmerge()
        if not mkvmerge:
            raise RuntimeError("MKVToolNix (mkvmerge) not found. Install it or set SRTFORGE_MKV_DIR.")
        title = self.options.srt_title or "Srtforge (English)"
        language = (self.options.srt_language or "eng").lower()
        default_flag = "yes" if self.options.srt_default else "no"
        forced_flag = "yes" if self.options.srt_forced else "no"
        command = [
            str(mkvmerge),
            "-o",
            str(output),
            str(media),
            "--language",
            f"0:{language}",
            "--track-name",
            f"0:{title}",
            "--default-track-flag",
            f"0:{default_flag}",
            "--forced-display-flag",
            f"0:{forced_flag}",
            str(subtitles),
        ]
        try:
            self._run_command(command, "Embed subtitles (mkvmerge)")
        except Exception:
            if overwrite_source and output.exists():
                try:
                    output.unlink()
                except OSError:
                    pass
            raise

        if overwrite_source:
            try:
                os.replace(output, media)
            except OSError as exc:
                if output.exists():
                    try:
                        output.unlink()
                    except OSError:
                        pass
                raise RuntimeError(
                    f"Failed to overwrite original media file with embedded version: {exc}"
                ) from exc
            return media

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


def _find_mkvmerge() -> Optional[Path]:
    """Locate mkvmerge using common install and bundle locations."""

    exe = "mkvmerge.exe" if os.name == "nt" else "mkvmerge"

    root = os.getenv("SRTFORGE_MKV_DIR")
    if root:
        candidate = Path(root) / exe
        if candidate.exists():
            return candidate

    bundle_root = Path(__file__).resolve().parent
    portable = bundle_root / "packaging" / "windows" / "mkvtoolnix" / exe
    if portable.exists():
        return portable

    repo_portable = bundle_root.parent / "packaging" / "windows" / "mkvtoolnix" / exe
    if repo_portable.exists():
        return repo_portable

    if os.name == "nt":
        program_files = Path(r"C:\Program Files\MKVToolNix\mkvmerge.exe")
        if program_files.exists():
            return program_files

    probe = which("mkvmerge")
    return Path(probe) if probe else None


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


def locate_mkvmerge_binary() -> Optional[MKVToolNixBinaries]:
    """Find mkvmerge via environment, common install paths, or PATH."""

    path = _find_mkvmerge()
    return MKVToolNixBinaries(path) if path else None


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


class OptionsDialog(QtWidgets.QDialog):
    """Popup dialog with Basic and Advanced tabs for configuration."""

    def __init__(self, *, parent=None, initial_basic: dict, initial_settings) -> None:
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.resize(760, 520)
        layout = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget(self)
        layout.addWidget(self.tabs)

        # ----- Basic tab (mirrors main window) ---------------------------------
        basic = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(basic)
        row = 0
        self.device_combo = QtWidgets.QComboBox()
        self.device_combo.addItem("Use GPU", True)
        self.device_combo.addItem("CPU only", False)
        self.device_combo.setCurrentIndex(0 if initial_basic.get("prefer_gpu", True) else 1)
        grid.addWidget(QtWidgets.QLabel("Device"), row, 0)
        grid.addWidget(self.device_combo, row, 1)
        row += 1

        # Collapsible "Embed subtitles" section (checkbox + chevron header)
        header = QtWidgets.QFrame()
        header.setObjectName("EmbedHeader")
        header_layout = QtWidgets.QHBoxLayout(header)
        # Slight inner padding so the pill looks intentional
        header_layout.setContentsMargins(4, 2, 4, 2)
        header_layout.setSpacing(4)

        # Checkbox is the source of truth for soft-embed being enabled
        self.embed_checkbox = QtWidgets.QCheckBox("Embed subtitles (soft track)")
        self.embed_checkbox.setObjectName("EmbedCheckbox")
        self.embed_checkbox.setChecked(bool(initial_basic.get("embed_subtitles", False)))

        # Chevron is a visual indicator + extra click target; it does not
        # have independent state, it simply mirrors the checkbox.
        self.embed_chevron = QtWidgets.QToolButton()
        self.embed_chevron.setObjectName("EmbedChevron")
        self.embed_chevron.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self.embed_chevron.setAutoRaise(True)
        self.embed_chevron.setArrowType(QtCore.Qt.RightArrow)
        self.embed_chevron.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.embed_chevron.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        # Put chevron right after the text, then push everything left
        header_layout.addWidget(self.embed_checkbox)
        header_layout.addWidget(self.embed_chevron)
        header_layout.addStretch()

        grid.addWidget(header, row, 0, 1, 2)
        self.embed_header = header
        row += 1

        def _toggle_embed_from_header(event: QtGui.QMouseEvent) -> None:
            if event.button() == QtCore.Qt.LeftButton:
                self.embed_checkbox.toggle()
            event.accept()

        header.mousePressEvent = _toggle_embed_from_header  # type: ignore[assignment]

        self.embed_panel = QtWidgets.QWidget()
        ep_grid = QtWidgets.QGridLayout(self.embed_panel)
        # method
        self.embed_method = QtWidgets.QComboBox()
        self.embed_method.addItem("Auto (prefer MKVToolNix)", "auto")
        self.embed_method.addItem("MKVToolNix (mkvmerge)", "mkvmerge")
        self.embed_method.addItem("FFmpeg (legacy)", "ffmpeg")
        idx = max(0, self.embed_method.findData(initial_basic.get("soft_embed_method", "auto")))
        self.embed_method.setCurrentIndex(idx)
        ep_grid.addWidget(QtWidgets.QLabel("Soft-embed method"), 0, 0)
        ep_grid.addWidget(self.embed_method, 0, 1)
        # title/language
        self.title_edit = QtWidgets.QLineEdit(initial_basic.get("srt_title", "Srtforge (English)"))
        self.lang_edit = QtWidgets.QLineEdit(initial_basic.get("srt_language", "eng"))
        ep_grid.addWidget(QtWidgets.QLabel("Track title"), 1, 0)
        ep_grid.addWidget(self.title_edit, 1, 1)
        ep_grid.addWidget(QtWidgets.QLabel("Track language"), 2, 0)
        ep_grid.addWidget(self.lang_edit, 2, 1)
        # flags
        self.default_cb = QtWidgets.QCheckBox("Set as default track")
        self.default_cb.setChecked(bool(initial_basic.get("srt_default", False)))
        self.forced_cb = QtWidgets.QCheckBox("Mark as forced")
        self.forced_cb.setChecked(bool(initial_basic.get("srt_forced", False)))
        ep_grid.addWidget(self.default_cb, 3, 0)
        ep_grid.addWidget(self.forced_cb, 3, 1)

        # NEW: option to overwrite the source container
        self.embed_overwrite_cb = QtWidgets.QCheckBox("Replace original video file")
        self.embed_overwrite_cb.setToolTip(
            "Write the soft-embedded subtitle track back into the source container, "
            "overwriting the original video file in-place. The path and filename are "
            "preserved. A temporary file and atomic replace are used so failures do "
            "not corrupt the original."
        )
        self.embed_overwrite_cb.setChecked(
            bool(initial_basic.get("soft_embed_overwrite_source", False))
        )
        ep_grid.addWidget(self.embed_overwrite_cb, 4, 0, 1, 2)
        grid.addWidget(self.embed_panel, row, 0, 1, 2)
        # remember the “normal” max height so we can restore it later
        self._embed_panel_max = self.embed_panel.maximumHeight()
        row += 1

        # Make sure the collapsible section really collapses and doesn’t leave
        # a big empty row in the grid when hidden.
        initial_embed = bool(initial_basic.get("embed_subtitles", False))
        self.embed_checkbox.setChecked(initial_embed)
        self._update_embed_panel(initial_embed)

        # Checkbox drives the on/off state; panel expands/collapses automatically.
        self.embed_checkbox.toggled.connect(self._on_embed_checkbox_toggled)

        # Chevron is just an alias click target for the same toggle.
        self.embed_chevron.clicked.connect(self.embed_checkbox.toggle)

        # NEW: explicit control over where the .srt ends up for GUI runs
        self.external_srt_cb = QtWidgets.QCheckBox("Save .srt next to video file")
        self.external_srt_cb.setToolTip(
            "Ignore the global 'Output directory' for GUI jobs and write the subtitle beside "
            "the media as 'filename.srt' so Jellyfin / Jellyfin MPV Shim can auto-detect it."
        )
        self.external_srt_cb.setChecked(bool(initial_basic.get("srt_next_to_media", False)))
        grid.addWidget(self.external_srt_cb, row, 0, 1, 2)
        row += 1

        self.burn_cb = QtWidgets.QCheckBox("Burn subtitles (hard sub)")
        self.burn_cb.setChecked(bool(initial_basic.get("burn_subtitles", False)))
        grid.addWidget(self.burn_cb, row, 0, 1, 2)
        row += 1

        self.cleanup_cb = QtWidgets.QCheckBox("Free GPU memory when stopping")
        self.cleanup_cb.setChecked(bool(initial_basic.get("cleanup_gpu", False)))
        grid.addWidget(self.cleanup_cb, row, 0, 1, 2)
        row += 1

        # Push any extra vertical space below the controls
        grid.setRowStretch(row, 1)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        self.tabs.addTab(basic, "Basic")

        # ----- Advanced tab (writes YAML for CLI via SRTFORGE_CONFIG) ----------
        adv = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(adv)

        # Paths
        self.output_dir = QtWidgets.QLineEdit(str(initial_settings.paths.output_dir or ""))
        self.temp_dir = QtWidgets.QLineEdit(str(initial_settings.paths.temp_dir or ""))
        out_row = QtWidgets.QHBoxLayout()
        out_widget = QtWidgets.QWidget()
        out_widget.setLayout(out_row)
        out_row.addWidget(self.output_dir, 1)
        btn_out = QtWidgets.QToolButton(text="Browse…")
        out_row.addWidget(btn_out)
        form.addRow("Output directory", out_widget)
        tmp_row = QtWidgets.QHBoxLayout()
        tmp_widget = QtWidgets.QWidget()
        tmp_widget.setLayout(tmp_row)
        tmp_row.addWidget(self.temp_dir, 1)
        btn_tmp = QtWidgets.QToolButton(text="Browse…")
        tmp_row.addWidget(btn_tmp)
        form.addRow("Temp directory", tmp_widget)
        btn_out.clicked.connect(lambda: self._pick_dir(self.output_dir))
        btn_tmp.clicked.connect(lambda: self._pick_dir(self.temp_dir))

        # Separation
        self.backend = QtWidgets.QComboBox()
        self.backend.addItem("FV4 vocal separation (recommended)", "fv4")
        self.backend.addItem("Skip separation", "none")
        backend_value = (initial_settings.separation.backend or "fv4").lower()
        self.backend.setCurrentIndex(max(0, self.backend.findData(backend_value)))
        form.addRow("Separation backend", self.backend)

        self.sep_hz = QtWidgets.QSpinBox()
        self.sep_hz.setRange(8000, 96000)
        self.sep_hz.setSingleStep(1000)
        self.sep_hz.setValue(int(initial_settings.separation.sep_hz))
        form.addRow("Separation sample rate (Hz)", self.sep_hz)

        self.sep_prefer_center = QtWidgets.QCheckBox()
        self.sep_prefer_center.setChecked(bool(initial_settings.separation.prefer_center))
        form.addRow("Prefer center channel (separation)", self.sep_prefer_center)

        self.allow_untagged = QtWidgets.QCheckBox()
        self.allow_untagged.setChecked(bool(initial_settings.separation.allow_untagged_english))
        form.addRow("Allow untagged English fallback", self.allow_untagged)

        # FFmpeg
        self.ff_prefer_center = QtWidgets.QCheckBox()
        self.ff_prefer_center.setChecked(bool(initial_settings.ffmpeg.prefer_center))
        form.addRow("Prefer center channel (FFmpeg pan)", self.ff_prefer_center)

        self.filter_chain = QtWidgets.QPlainTextEdit(initial_settings.ffmpeg.filter_chain or "")
        self.filter_chain.setMinimumHeight(80)
        form.addRow("FFmpeg filter chain", self.filter_chain)

        # Parakeet
        self.force_f32 = QtWidgets.QCheckBox()
        self.force_f32.setChecked(bool(initial_settings.parakeet.force_float32))
        form.addRow("Force float32 (Parakeet)", self.force_f32)

        self.tabs.addTab(adv, "Advanced")

        # Buttons
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    def _on_embed_checkbox_toggled(self, checked: bool) -> None:
        """Entry point from the header checkbox; keeps panel + chevron in sync."""
        self._update_embed_panel(checked)

    def _update_embed_panel(self, checked: bool) -> None:
        # 0 height when collapsed, natural height when expanded
        max_h = getattr(self, "_embed_panel_max", self.embed_panel.maximumHeight())
        self.embed_panel.setVisible(checked)
        self.embed_panel.setMaximumHeight(max_h if checked else 0)
        self.embed_panel.updateGeometry()

        # Update chevron direction to reflect expand/collapse
        if hasattr(self, "embed_chevron"):
            self.embed_chevron.setArrowType(
                QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow
            )

        # Tint the header row when enabled so “dark box means on”
        header = getattr(self, "embed_header", None)
        if header is not None:
            header.setProperty("checked", checked)
            header.style().unpolish(header)
            header.style().polish(header)
            header.update()

    def _pick_dir(self, edit: QtWidgets.QLineEdit) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select folder", edit.text().strip() or "")
        if path:
            edit.setText(path)

    def basic_values(self) -> dict:
        return {
            "prefer_gpu": bool(self.device_combo.currentData()),
            "embed_subtitles": self.embed_checkbox.isChecked(),
            "burn_subtitles": self.burn_cb.isChecked(),
            "cleanup_gpu": self.cleanup_cb.isChecked(),
            "soft_embed_method": str(self.embed_method.currentData()),
            "soft_embed_overwrite_source": self.embed_overwrite_cb.isChecked(),  # NEW
            "srt_title": self.title_edit.text().strip() or "Srtforge (English)",
            "srt_language": self.lang_edit.text().strip() or "eng",
            "srt_default": self.default_cb.isChecked(),
            "srt_forced": self.forced_cb.isChecked(),
            # NEW
            "srt_next_to_media": self.external_srt_cb.isChecked(),
        }

    def settings_payload(self, *, prefer_gpu: Optional[bool] = None) -> dict:
        gpu_pref = bool(prefer_gpu) if prefer_gpu is not None else True
        return {
            "paths": {
                "temp_dir": self.temp_dir.text().strip() or None,
                "output_dir": self.output_dir.text().strip() or None,
            },
            "ffmpeg": {
                "prefer_center": self.ff_prefer_center.isChecked(),
                "filter_chain": self.filter_chain.toPlainText().strip(),
            },
            "separation": {
                "backend": str(self.backend.currentData() or "fv4"),
                "sep_hz": int(self.sep_hz.value()),
                "prefer_center": self.sep_prefer_center.isChecked(),
                "prefer_gpu": gpu_pref,
                "allow_untagged_english": self.allow_untagged.isChecked(),
            },
            "parakeet": {
                "force_float32": self.force_f32.isChecked(),
                "prefer_gpu": gpu_pref,
            },
        }


class MainWindow(QtWidgets.QMainWindow):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        # Capitalize brand and open at the size in the screenshot
        self.setWindowTitle("Srtforge Studio")
        self.resize(1200, 720)
        self.setMinimumSize(960, 640)
        self.setObjectName("MainWindow")

        icon = _load_app_icon()
        self._app_icon = icon  # store for reuse
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.setAcceptDrops(True)
        self._worker: Optional[TranscriptionWorker] = None
        self._log_tailer: Optional[LogTailer] = None
        self._last_worker_options: Optional[WorkerOptions] = None
        self._runtime_config_path: Optional[str] = None

        # Theme state (persisted via QSettings)
        self._dark_mode: bool = False
        # Single source of truth for user options (kept only in the Options dialog)
        self._basic_options = {
            "prefer_gpu": True,
            "embed_subtitles": False,
            "burn_subtitles": False,
            "cleanup_gpu": False,
            "soft_embed_method": "auto",
            "soft_embed_overwrite_source": False,  # NEW
            "srt_title": "Srtforge (English)",
            "srt_language": "eng",
            "srt_default": False,
            "srt_forced": False,
            # NEW
            "srt_next_to_media": False,
        }
        self.ffmpeg_paths = locate_ffmpeg_binaries()
        self.mkv_paths = locate_mkvmerge_binary()
        self._qsettings = QtCore.QSettings("srtforge", "SrtforgeStudio")
        self._eta_timer = QtCore.QTimer(self)
        self._eta_timer.setInterval(1000)
        self._eta_timer.timeout.connect(self._tick_eta)
        self._eta_deadline: Optional[float] = None
        self._eta_mode_gpu: bool = True
        self._eta_media: Optional[str] = None
        self._eta_memory = _EtaMemory()
        # Track per-file durations for the “Total duration” summary
        self._queue_duration_cache: dict[str, float] = {}
        # Track the current ETA window so we can compute % progress
        self._eta_total: float = 0.0
        self._build_ui()  # builds a page widget; we wrap it in a scroll area below
        self._log_tailer = LogTailer(self._append_log, self)
        self._load_persistent_options()
        self._apply_styles()
        self._update_tool_status()
        apply_win11_look(self)

    # ---- UI construction ---------------------------------------------------------
    def _build_ui(self) -> None:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        # No extra vertical gap above/below the header; keep side padding
        layout.setSpacing(0)
        layout.setContentsMargins(16, 0, 16, 12)
        pointer_cursor = QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        # --- Header: logo + title centered, controls on the right --------------
        header_layout = QtWidgets.QGridLayout()
        # Remove header margins entirely so logo+title sit as tight as possible
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setHorizontalSpacing(0)
        header_layout.setVerticalSpacing(0)
        header_layout.setColumnStretch(0, 1)  # left spacer
        header_layout.setColumnStretch(1, 0)  # brand
        header_layout.setColumnStretch(2, 1)  # right controls + spacer
        layout.addLayout(header_layout)

        # Dummy left column so 0 and 2 grow symmetrically
        header_layout.addItem(
            QtWidgets.QSpacerItem(
                0,
                0,
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Minimum,
            ),
            0,
            0,
        )

        # Centered branding (bigger logo + title)
        brand_row = QtWidgets.QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)

        logo_label = QtWidgets.QLabel()
        logo_label.setContentsMargins(0, 0, 0, 0)
        # Remove all padding/margins around the logo so the pixmap box is tight
        logo_label.setStyleSheet("margin: 0px; padding: 0px;")

        icon = getattr(self, "_app_icon", _load_app_icon())
        if icon and not icon.isNull():
            # Keep the logo at the same visual size, but with no extra border
            logo_pix = icon.pixmap(63, 63)
            logo_label.setPixmap(logo_pix)
            logo_label.setFixedSize(logo_pix.size())

        brand_row.addWidget(logo_label, 0, QtCore.Qt.AlignVCenter)

        title = QtWidgets.QLabel("SrtForge\nStudio")
        title.setObjectName("HeaderLabel")
        title.setContentsMargins(0, 0, 0, 0)
        title.setMargin(0)
        title.setIndent(0)
        # Completely kill padding/margins so text hugs the logo
        title.setStyleSheet("margin: 0px; padding: 0px;")

        # Hard 0px spacing between logo and text so they visually touch
        brand_row.setSpacing(0)
        brand_row.addWidget(title, 0, QtCore.Qt.AlignVCenter)

        brand_row.addStretch()

        brand_widget = QtWidgets.QWidget()
        brand_widget.setLayout(brand_row)
        header_layout.addWidget(brand_widget, 0, 1, alignment=QtCore.Qt.AlignCenter)

        # Right‑hand controls (theme toggle + options)
        actions_row = QtWidgets.QHBoxLayout()
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(6)

        self.theme_toggle = QtWidgets.QToolButton()
        self.theme_toggle.setObjectName("ThemeToggle")
        self.theme_toggle.setCheckable(True)
        self.theme_toggle.setCursor(pointer_cursor)
        self.theme_toggle.toggled.connect(self._on_theme_toggled)
        actions_row.addWidget(self.theme_toggle)

        self.options_button = QtWidgets.QToolButton()
        self.options_button.setObjectName("OptionsButton")
        self.options_button.setCursor(pointer_cursor)
        self.options_button.setToolTip("Options")
        self.options_button.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView)
        )
        self.options_button.clicked.connect(self._open_options_dialog)
        actions_row.addWidget(self.options_button)

        actions_widget = QtWidgets.QWidget()
        actions_widget.setLayout(actions_row)
        header_layout.addWidget(actions_widget, 0, 2, alignment=QtCore.Qt.AlignRight)

        # --- Queue card ------------------------------------------------------
        queue_card = QtWidgets.QFrame()
        queue_card.setObjectName("QueueCard")
        queue_card.setFrameShape(QtWidgets.QFrame.NoFrame)
        card_layout = QtWidgets.QVBoxLayout(queue_card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)

        # Top action bar: Add / Remove / Clear
        action_bar = QtWidgets.QHBoxLayout()

        self.add_button = QtWidgets.QPushButton("Add files…")
        self.add_button.setObjectName("PrimaryButton")
        self.add_button.setCursor(pointer_cursor)
        self.add_button.clicked.connect(self._open_file_dialog)
        action_bar.addWidget(self.add_button)

        self.remove_button = QtWidgets.QPushButton("Remove")
        self.remove_button.setObjectName("SecondaryButton")
        self.remove_button.setCursor(pointer_cursor)
        self.remove_button.clicked.connect(self._remove_selected_items)
        action_bar.addWidget(self.remove_button)

        self.clear_button = QtWidgets.QPushButton("Clear")
        self.clear_button.setObjectName("SecondaryButton")
        self.clear_button.setCursor(pointer_cursor)
        self.clear_button.setFlat(True)  # secondary / ghost-style
        self.clear_button.clicked.connect(self._clear_queue)
        action_bar.addWidget(self.clear_button)

        action_bar.addStretch()

        card_layout.addLayout(action_bar)

        # Center: stacked empty state vs queue list
        self.queue_stack = QtWidgets.QStackedWidget()

        # Empty drag & drop state
        self.queue_placeholder = QtWidgets.QWidget()
        ph_layout = QtWidgets.QVBoxLayout(self.queue_placeholder)
        ph_layout.addStretch()

        icon_lbl = QtWidgets.QLabel("🎬")
        icon_lbl.setObjectName("DropIcon")
        icon_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        ph_layout.addWidget(icon_lbl)

        text_lbl = QtWidgets.QLabel("Drag & drop video files here\nor click “Add files…”")
        text_lbl.setObjectName("DropHint")
        text_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        ph_layout.addWidget(text_lbl)

        ph_layout.addStretch()
        self.queue_stack.addWidget(self.queue_placeholder)

        # Actual queue list
        self.queue_list = QtWidgets.QListWidget()
        self.queue_list.setObjectName("QueueList")
        self.queue_list.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.queue_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.queue_list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.queue_list.setUniformItemSizes(True)
        self.queue_list.setMinimumHeight(160)

        # 🔧 Remove inner focus border (fixes 'double boxing')
        self.queue_list.setItemDelegate(QueueItemDelegate(self.queue_list))

        self.queue_stack.addWidget(self.queue_list)

        card_layout.addWidget(self.queue_stack)

        # Bottom bar: total duration (left) + Start / Stop (right)
        bottom_bar = QtWidgets.QHBoxLayout()
        self.queue_summary_label = QtWidgets.QLabel("")
        bottom_bar.addWidget(self.queue_summary_label)
        bottom_bar.addStretch()

        self.start_button = QtWidgets.QPushButton("Start transcription")
        self.start_button.setCursor(pointer_cursor)
        self.start_button.clicked.connect(self._start_processing)
        bottom_bar.addWidget(self.start_button)

        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setCursor(pointer_cursor)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_processing)
        bottom_bar.addWidget(self.stop_button)

        card_layout.addLayout(bottom_bar)

        layout.addWidget(queue_card)
        add_shadow(queue_card)

        # Log drawer (hidden by default – toggled from the status bar)
        self.log_container = QtWidgets.QFrame()
        self.log_container.setObjectName("LogContainer")
        log_layout = QtWidgets.QVBoxLayout(self.log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(110)
        self.log_view.setMaximumHeight(260)
        self.log_view.setMaximumBlockCount(10000)
        self._init_log_zoom()
        log_layout.addWidget(self.log_view)
        add_shadow(self.log_view)

        # Start hidden; user can reveal via the terminal icon in the status bar
        self.log_container.setVisible(False)
        layout.addWidget(self.log_container)

        scroll = QtWidgets.QScrollArea()
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.setCentralWidget(scroll)

        # --- Status bar: system status + ETA + log toggle --------------------
        status_bar = QtWidgets.QStatusBar(self)
        self.setStatusBar(status_bar)

        # Tiny coloured dot + short text; full details in tooltip
        self.status_indicator = QtWidgets.QLabel()
        self.status_indicator.setObjectName("StatusIndicator")
        status_bar.addWidget(self.status_indicator)

        # We’ll move ETA text down here
        self.eta_label = QtWidgets.QLabel("Idle")
        self.eta_label.setObjectName("EtaLabel")
        status_bar.addWidget(self.eta_label)

        # Sleek progress bar, only visible while processing
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setMaximumWidth(180)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        status_bar.addPermanentWidget(self.progress_bar)

        # “Terminal” toggle for the log drawer
        console_trigger = QtWidgets.QWidget()
        console_trigger.setObjectName("FooterConsoleTrigger")
        console_trigger.setCursor(pointer_cursor)

        console_layout = QtWidgets.QHBoxLayout(console_trigger)
        # Give the pill its own padding instead of the icon button doing it
        console_layout.setContentsMargins(6, 2, 10, 2)
        console_layout.setSpacing(6)

        # Icon button (acts as the actual toggle)
        self.log_toggle_button = QtWidgets.QToolButton(console_trigger)
        self.log_toggle_button.setObjectName("LogToggle")
        self.log_toggle_button.setCheckable(True)
        self.log_toggle_button.setToolTip("Show console")
        self.log_toggle_button.setCursor(pointer_cursor)
        self.log_toggle_button.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon)
        )
        # make it look like a flat icon inside the pill, not its own button box
        self.log_toggle_button.setAutoRaise(True)
        self.log_toggle_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.log_toggle_button.setIconSize(QtCore.QSize(16, 16))
        # icon-only; text lives in a separate label for cleaner alignment
        self.log_toggle_button.setText("")
        self.log_toggle_button.toggled.connect(self._toggle_log_panel)

        # 🔧 Ensure the icon button itself never adds its own grey background
        self.log_toggle_button.setStyleSheet(
            """
            QToolButton#LogToggle {
                background-color: transparent;
                border: none;
                padding: 0px;
            }
            QToolButton#LogToggle:hover,
            QToolButton#LogToggle:pressed,
            QToolButton#LogToggle:checked {
                background-color: transparent;
            }
            """
        )

        # Keep the whole pill in sync with the toggle state for styling
        console_trigger.setProperty("checked", False)

        def _sync_console_pill(checked: bool, w=console_trigger) -> None:
            w.setProperty("checked", checked)
            w.style().unpolish(w)
            w.style().polish(w)
            w.update()

        self.log_toggle_button.toggled.connect(_sync_console_pill)

        # Text label >_ Console
        log_label = QtWidgets.QLabel(">_ Console", console_trigger)
        log_label.setObjectName("LogToggleLabel")
        log_label.setCursor(pointer_cursor)

        console_layout.addWidget(self.log_toggle_button)
        console_layout.addWidget(log_label)
        console_layout.setAlignment(QtCore.Qt.AlignVCenter)

        # Make the whole pill clickable
        def _toggle_console_from_mouse(event: QtGui.QMouseEvent) -> None:
            if event.button() == QtCore.Qt.LeftButton:
                self.log_toggle_button.toggle()
            event.accept()

        console_trigger.mousePressEvent = _toggle_console_from_mouse  # type: ignore[assignment]
        log_label.mousePressEvent = _toggle_console_from_mouse  # type: ignore[assignment]

        status_bar.addPermanentWidget(console_trigger)

        self._update_start_state()

    # Make the whole window a drop target with a gentle grey overlay
    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:  # noqa: D401 - Qt override
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._show_drop_overlay()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:  # noqa: D401 - Qt override
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:  # noqa: D401 - Qt override
        self._hide_drop_overlay()
        event.accept()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:  # noqa: D401 - Qt override
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._add_files_to_queue(paths)
        self._hide_drop_overlay()
        event.acceptProposedAction()

    def _show_drop_overlay(self) -> None:
        if getattr(self, "_overlay", None):
            self._overlay.show()
            self._overlay.raise_()
            return
        parent = self.centralWidget().viewport() if isinstance(self.centralWidget(), QtWidgets.QScrollArea) else self.centralWidget()
        self._overlay = QtWidgets.QFrame(parent)
        self._overlay.setObjectName("GlobalDropOverlay")
        self._overlay.setStyleSheet("#GlobalDropOverlay { background: rgba(0,0,0,0.28); }")
        self._overlay.setGeometry(parent.rect())
        label = QtWidgets.QLabel("Drop files to add", self._overlay)
        label.setStyleSheet("color: white; font-size: 18px;")
        label.adjustSize()
        label.move((self._overlay.width() - label.width()) // 2, (self._overlay.height() - label.height()) // 2)
        self._overlay.show()
        self._overlay.raise_()

    def _hide_drop_overlay(self) -> None:
        if getattr(self, "_overlay", None):
            self._overlay.hide()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        # Keep the overlay sized to the scroll viewport
        if getattr(self, "_overlay", None) and self._overlay.isVisible():
            parent = self.centralWidget().viewport() if isinstance(self.centralWidget(), QtWidgets.QScrollArea) else self.centralWidget()
            self._overlay.setParent(parent)
            self._overlay.setGeometry(parent.rect())

    # --- log font zoom helpers (Ctrl+, Ctrl-, Ctrl+0) ----------------------------
    def _init_log_zoom(self) -> None:
        self._log_zoom_delta = 0
        self._apply_log_font()
        QtGui.QShortcut(QtGui.QKeySequence.ZoomIn, self, activated=self._zoom_in)
        QtGui.QShortcut(QtGui.QKeySequence.ZoomOut, self, activated=self._zoom_out)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+0"), self, activated=self._zoom_reset)

    def _apply_log_font(self) -> None:
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        size = max(8, (font.pointSize() or 10) + self._log_zoom_delta)
        font.setPointSize(size)
        self.log_view.setFont(font)

    def _toggle_log_panel(self, checked: bool) -> None:
        if getattr(self, "log_container", None) is None:
            return
        self.log_container.setVisible(checked)
        if hasattr(self, "log_toggle_button"):
            self.log_toggle_button.setToolTip("Hide console" if checked else "Show console")

    def _zoom_in(self) -> None:
        self._log_zoom_delta += 1
        self._apply_log_font()

    def _zoom_out(self) -> None:
        self._log_zoom_delta = max(-6, self._log_zoom_delta - 1)
        self._apply_log_font()

    def _zoom_reset(self) -> None:
        self._log_zoom_delta = 0
        self._apply_log_font()
    def _apply_styles(self) -> None:
        # Brand accent: Parakeet-style green for buttons and highlights
        accent = QtGui.QColor("#16A34A")  # close to that Parakeet green
        palette = self.palette()

        if self._dark_mode:
            # ---- Dark (Slate) palette ----
            # App background      #0F172A
            # Card / queue bg     #1E293B-ish (we use cards via QSS)
            # Primary text        #F1F5F9
            # Secondary text      #94A3B8
            palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#0F172A"))
            palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#020617"))
            palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor("#020617"))
            palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#E5E7EB"))
            palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor("#F1F5F9"))
            palette.setColor(QtGui.QPalette.ColorRole.Button, accent)
            palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor("#F9FAFB"))
            palette.setColor(QtGui.QPalette.ColorRole.Highlight, accent)
            palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor("#020617"))
            palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor("#E5E7EB"))
        else:
            # ---- Light palette ----
            # App background      #F8FAFC
            # Card / queue bg     #FFFFFF
            # Primary text        #0F172A
            # Secondary text      #64748B
            palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#F8FAFC"))
            palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#FFFFFF"))
            palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor("#F1F5F9"))
            palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#0F172A"))
            palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor("#0F172A"))
            palette.setColor(QtGui.QPalette.ColorRole.Button, accent)
            palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor("#FFFFFF"))
            palette.setColor(QtGui.QPalette.ColorRole.Highlight, accent)
            palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor("#E5E7EB"))
            palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor("#020617"))

        self.setPalette(palette)

        # Only use the Win11 .qss file as a base for light mode; in dark mode we fully override.
        base_qss = ""
        if not self._dark_mode:
            base_qss = self._load_win11_stylesheet(accent) or ""

        lighter = QtGui.QColor(accent)
        lighter = lighter.lighter(115)
        darker = QtGui.QColor(accent)
        darker = darker.darker(115)

        if self._dark_mode:
            # --- Dark mode QSS: no bright borders, rely on elevation + slate cards ---
            custom = f"""
            #MainWindow {{
                background-color: #0F172A;
            }}

            QLabel {{
                color: #E5E7EB;
            }}
            QLabel#HeaderLabel {{
                color: #F9FAFB;
                font-size: 22px;
                font-weight: 500;
            }}
            QLabel#EtaLabel {{
                color: #94A3B8;
                padding: 6px 10px;
            }}

            #QueueCard {{
                background-color: #020617;
                border-radius: 16px;
                border: none;
            }}

            QPlainTextEdit, QTextEdit {{
                background-color: #020617;
                color: #E5E7EB;
                border-radius: 12px;
                border: 1px solid #020617;
            }}

            QGroupBox {{
                background-color: #020617;
                border-radius: 16px;
                border: none;
                margin-top: 16px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 16px;
                padding: 4px 8px 4px 8px;
                color: #E5E7EB;
                font-weight: 500;
            }}

            #EmbedHeader {{
                border-radius: 8px;
                padding: 2px 8px;
            }}
            #EmbedHeader[checked="true"] {{
                background-color: rgba(30, 64, 175, 0.75); /* dark blue-ish box */
            }}

            /* Header contents: keep them flat on the pill */
            QCheckBox#EmbedCheckbox {{
                background-color: transparent;
                border: none;
            }}
            /* Always show a real checkbox box so it looks toggle-able */
            QCheckBox#EmbedCheckbox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 3px;
                border: 1px solid #1F2937;
                background: #020617;
                margin-right: 6px;
            }}
            QCheckBox#EmbedCheckbox::indicator:checked {{
                background: {accent.name()};
                border-color: {accent.name()};
            }}
            QToolButton#EmbedChevron {{
                background-color: transparent;
                border: none;
                padding: 0;
                margin-left: 4px;
                min-width: 20px;
                max-width: 20px;
            }}
            #EmbedHeader[checked="true"] QCheckBox#EmbedCheckbox {{
                font-weight: 500;
            }}

            QListWidget#QueueList {{
                background-color: #020617;
                border-radius: 10px;
                border: none;
            }}
            QListWidget#QueueList::item {{
                padding: 6px 8px;
                border: none;
                outline: none;
            }}
            QListWidget#QueueList::item:hover {{
                background-color: rgba(148, 163, 184, 0.18);
            }}
            QListWidget#QueueList::item:selected,
            QListWidget#QueueList::item:selected:active,
            QListWidget#QueueList::item:selected:!active,
            QListWidget#QueueList::item:focus {{
                background-color: rgba(59, 130, 246, 0.35);
                color: #E5E7EB;
                border: none;
                outline: none;
            }}

            QPushButton, QToolButton {{
                background-color: {accent.name()};
                color: #F9FAFB;
                border-radius: 8px;
                padding: 6px 14px;
                border: none;
            }}
            QPushButton:disabled, QToolButton:disabled {{
                background-color: #1E293B;
                color: #64748B;
            }}
            QPushButton:hover, QToolButton:hover {{
                background-color: {lighter.name()};
            }}
            QPushButton:pressed, QToolButton:pressed {{
                background-color: {darker.name()};
            }}

            /* Ghost secondary actions: Remove / Clear */
            QPushButton#SecondaryButton {{
                background-color: transparent;
                color: #94A3B8;
                border-radius: 8px;
                border: 1px solid #1F2937;
            }}
            QPushButton#SecondaryButton:disabled {{
                color: #4B5563;
                border: 1px solid #1F2937;
            }}
            QPushButton#SecondaryButton:hover {{
                color: #FCA5A5;
                border-color: #FCA5A5;
                background-color: rgba(248, 113, 113, 0.10);
            }}
            QPushButton#SecondaryButton:pressed {{
                background-color: rgba(248, 113, 113, 0.18);
            }}

            /* Top-right header icons: ghost buttons */
            QToolButton#ThemeToggle,
            QToolButton#OptionsButton {{
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0;
                border-radius: 16px;
                background-color: transparent;   /* no solid blue block */
                border: none;
                color: #E5E7EB;                  /* slate-ish icon color */
            }}
            QToolButton#ThemeToggle:hover,
            QToolButton#OptionsButton:hover {{
                background-color: rgba(148, 163, 184, 0.24); /* light grey circle on hover */
            }}
            QToolButton#ThemeToggle:pressed,
            QToolButton#OptionsButton:pressed {{
                background-color: rgba(148, 163, 184, 0.32);
            }}

            /* Console pill in status bar (dark mode) */
            QToolButton#LogToggle {{
                background-color: transparent;
                color: #94A3B8;
                border: none;
                padding: 0;                 /* no internal button padding */
                margin: 0 4px 0 0;          /* small gap before the label */
            }}
            QToolButton#LogToggle:hover,
            QToolButton#LogToggle:pressed,
            QToolButton#LogToggle:checked {{
                background-color: transparent;   /* pill handles hover/active */
            }}

            #FooterConsoleTrigger {{
                border-radius: 999px;
                padding: 2px 10px;              /* pill height + horizontal breathing room */
            }}
            #FooterConsoleTrigger:hover {{
                background-color: rgba(148, 163, 184, 0.16);
            }}

            /* When the console is open, keep the pill visibly active */
            #FooterConsoleTrigger[checked="true"] {{
                background-color: rgba(15, 23, 42, 0.80);
            }}

            QLabel#LogToggleLabel {{
                color: #94A3B8;
            }}
            #FooterConsoleTrigger:hover QLabel#LogToggleLabel {{
                color: #E5E7EB;
            }}
            #FooterConsoleTrigger[checked="true"] QLabel#LogToggleLabel {{
                color: #F9FAFB;
            }}

            QLineEdit, QComboBox {{
                background-color: #020617;
                border-radius: 8px;
                border: 1px solid #1E293B;
                padding: 4px 8px;
                color: #E5E7EB;
                selection-background-color: {accent.name()};
            }}

            QLabel#DropIcon {{
                font-size: 56px;
                color: rgba(148, 163, 184, 0.5);
            }}
            QLabel#DropHint {{
                color: #94A3B8;
            }}

            QScrollArea {{
                background-color: #0F172A;
                border: none;
            }}

            #GlobalDropOverlay {{
                background: rgba(15, 23, 42, 0.80);
            }}
            """
        else:
            # --- Light mode QSS ---
            custom = f"""
            QLabel#HeaderLabel {{
                font-size: 22px;
                font-weight: 500;
                padding: 0px;
                margin: 0px;
            }}

            QLabel,QLineEdit,QComboBox,QPushButton,QCheckBox {{
                padding-top: 4px;
                padding-bottom: 4px;
            }}

            /* Ensure all text inputs in light mode are dark-on-light and readable */
            QLineEdit, QComboBox, QTextEdit, QPlainTextEdit {{
                color: #0F172A;
                selection-background-color: {accent.name()};
                selection-color: #FFFFFF;
            }}

            /* Make inputs look like light-mode fields instead of dark-theme leftovers */
            QLineEdit, QComboBox {{
                background-color: #FFFFFF;
                border-radius: 8px;
                border: 1px solid #CBD5E1;
                padding: 4px 8px;
            }}

            QGroupBox {{
                margin-top: 12px;
                background: #FFFFFF;
                border-radius: 16px;
                border: 1px solid #E2E8F0;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 16px;
                padding: 4px 8px 4px 8px;
            }}

            #EmbedHeader {{
                border-radius: 8px;
                padding: 2px 8px;
            }}
            #EmbedHeader[checked="true"] {{
                background-color: rgba(59, 130, 246, 0.08);  /* soft blue pill */
            }}

            /* Header contents: keep a visible checkbox so it's clearly clickable */
            QCheckBox#EmbedCheckbox {{
                background-color: transparent;
                border: none;
            }}
            QCheckBox#EmbedCheckbox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 3px;
                border: 1px solid #CBD5E1;
                background: #FFFFFF;
                margin-right: 6px;
            }}
            QCheckBox#EmbedCheckbox::indicator:checked {{
                background: {accent.name()};
                border-color: {accent.name()};
            }}
            QToolButton#EmbedChevron {{
                background-color: transparent;
                border: none;
                padding: 0;
                margin-left: 4px;
                min-width: 20px;
                max-width: 20px;
            }}
            #EmbedHeader[checked="true"] QCheckBox#EmbedCheckbox {{
                font-weight: 500;
            }}

            #QueueCard {{
                background: #FFFFFF;
                border-radius: 16px;
                border: 1px solid #E2E8F0;
            }}

            #QueueList {{
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }}

            #QueueList::item {{
                padding: 6px 8px;
                border: none;
                outline: none;
            }}

            #QueueList::item:hover {{
                background: rgba(0,0,0,0.03);
            }}

            #QueueList::item:selected,
            #QueueList::item:selected:active,
            #QueueList::item:selected:!active,
            #QueueList::item:focus {{
                background: rgba(59,130,246,0.14);
                color: #111827;
                border: none;
                outline: none;
            }}

            #EtaLabel {{
                color: #64748B;
                padding: 6px 10px;
            }}

            QPushButton, QToolButton {{
                background-color: {accent.name()};
                color: #FFFFFF;
                border-radius: 8px;
                padding: 6px 14px;
                border: none;
            }}
            QPushButton:disabled, QToolButton:disabled {{
                background-color: #E5E7EB;
                color: #9CA3AF;
            }}
            QPushButton:hover, QToolButton:hover {{
                background-color: {lighter.name()};
            }}
            QPushButton:pressed, QToolButton:pressed {{
                background-color: {darker.name()};
            }}

            /* Ghost secondary actions: Remove / Clear */
            QPushButton#SecondaryButton {{
                background-color: transparent;
                color: #64748B;
                border-radius: 8px;
                border: 1px solid #CBD5E1;
            }}
            QPushButton#SecondaryButton:disabled {{
                color: #CBD5E1;
                border: 1px solid #E5E7EB;
            }}
            QPushButton#SecondaryButton:hover {{
                color: #EF4444;
                border-color: #FCA5A5;
                background-color: #FEF2F2;
            }}
            QPushButton#SecondaryButton:pressed {{
                background-color: #FEE2E2;
            }}

            /* Top-right header icons: ghost buttons */
            QToolButton#ThemeToggle,
            QToolButton#OptionsButton {{
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0;
                border-radius: 16px;
                background-color: transparent;
                border: none;
                color: #475569;                 /* slate/dark grey */
            }}
            QToolButton#ThemeToggle:hover,
            QToolButton#OptionsButton:hover {{
                background-color: rgba(148, 163, 184, 0.20); /* light grey circle on hover */
            }}
            QToolButton#ThemeToggle:pressed,
            QToolButton#OptionsButton:pressed {{
                background-color: rgba(148, 163, 184, 0.30);
            }}

            /* Console pill in status bar (light mode) */
            QToolButton#LogToggle {{
                background-color: transparent;
                color: #64748B;
                border: none;
                padding: 0;                 /* let the pill own the padding */
                margin: 0 4px 0 0;
            }}
            QToolButton#LogToggle:hover,
            QToolButton#LogToggle:pressed,
            QToolButton#LogToggle:checked {{
                background-color: transparent;   /* no extra box on hover */
            }}

            #FooterConsoleTrigger {{
                border-radius: 999px;
                padding: 2px 10px;
            }}
            #FooterConsoleTrigger:hover {{
                background-color: rgba(148, 163, 184, 0.12);
            }}
            /* Active/open state when console is shown */
            #FooterConsoleTrigger[checked="true"] {{
                background-color: rgba(59, 130, 246, 0.08);
            }}

            QLabel#LogToggleLabel {{
                color: #64748B;
            }}
            #FooterConsoleTrigger:hover QLabel#LogToggleLabel {{
                color: #0F172A;
            }}
            #FooterConsoleTrigger[checked="true"] QLabel#LogToggleLabel {{
                color: #1D4ED8;
            }}

            QLabel#DropIcon {{
                font-size: 56px;
                color: rgba(148, 163, 184, 0.5);
            }}
            QLabel#DropHint {{
                color: #94A3B8;
            }}

            QPlainTextEdit {{
                background-color: #FFFFFF;
                border-radius: 10px;
                border: 1px solid #E5E7EB;
                color: #0F172A;
                selection-background-color: {accent.name()};
                selection-color: #FFFFFF;
            }}
            """

        self.setStyleSheet(base_qss + custom)

    def _update_theme_toggle_label(self) -> None:
        """Update the theme toggle glyph + tooltip."""
        if not hasattr(self, "theme_toggle"):
            return
        if self._dark_mode:
            # Currently dark → show sun to indicate you can go back to light
            self.theme_toggle.setText("☀")
            self.theme_toggle.setToolTip("Switch to light mode")
        else:
            self.theme_toggle.setText("🌙")
            self.theme_toggle.setToolTip("Switch to dark mode")

    def _on_theme_toggled(self, checked: bool) -> None:
        """Switch between light and dark palettes."""
        self._dark_mode = bool(checked)
        self._update_theme_toggle_label()
        self._apply_styles()

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

    # ---- persistent options ------------------------------------------------------
    def _load_persistent_options(self) -> None:
        """Restore user-facing options from the last run."""
        s = self._qsettings

        self._basic_options["prefer_gpu"] = s.value("device_prefer_gpu", True, type=bool)
        self._basic_options["embed_subtitles"] = s.value("embed_subtitles", False, type=bool)
        self._basic_options["burn_subtitles"] = s.value("burn_subtitles", False, type=bool)
        self._basic_options["cleanup_gpu"] = s.value("cleanup_gpu", False, type=bool)
        self._basic_options["soft_embed_method"] = s.value("soft_embed_method", "auto", type=str)
        self._basic_options["soft_embed_overwrite_source"] = s.value(
            "soft_embed_overwrite_source", False, type=bool
        )
        self._basic_options["srt_title"] = s.value("srt_title", "Srtforge (English)", type=str)
        self._basic_options["srt_language"] = s.value("srt_language", "eng", type=str)
        self._basic_options["srt_default"] = s.value("srt_default", False, type=bool)
        self._basic_options["srt_forced"] = s.value("srt_forced", False, type=bool)
        # NEW
        self._basic_options["srt_next_to_media"] = s.value("srt_next_to_media", False, type=bool)

        # Theme
        self._dark_mode = s.value("dark_mode", False, type=bool)
        if hasattr(self, "theme_toggle"):
            block = self.theme_toggle.blockSignals(True)
            try:
                self.theme_toggle.setChecked(self._dark_mode)
            finally:
                self.theme_toggle.blockSignals(block)
        self._update_theme_toggle_label()

    def _save_persistent_options(self) -> None:
        """Persist current GUI options for the next run."""
        s = self._qsettings

        s.setValue("device_prefer_gpu", bool(self._basic_options.get("prefer_gpu", True)))
        s.setValue("embed_subtitles", bool(self._basic_options.get("embed_subtitles", False)))
        s.setValue("burn_subtitles", bool(self._basic_options.get("burn_subtitles", False)))
        s.setValue("cleanup_gpu", bool(self._basic_options.get("cleanup_gpu", False)))

        s.setValue("soft_embed_method", str(self._basic_options.get("soft_embed_method", "auto")))
        s.setValue(
            "soft_embed_overwrite_source",
            bool(self._basic_options.get("soft_embed_overwrite_source", False)),
        )
        s.setValue("srt_title", str(self._basic_options.get("srt_title", "Srtforge (English)")))
        s.setValue("srt_language", str(self._basic_options.get("srt_language", "eng")))
        s.setValue("srt_default", bool(self._basic_options.get("srt_default", False)))
        s.setValue("srt_forced", bool(self._basic_options.get("srt_forced", False)))
        # NEW
        s.setValue("srt_next_to_media", bool(self._basic_options.get("srt_next_to_media", False)))
        s.setValue("dark_mode", bool(getattr(self, "_dark_mode", False)))

        s.sync()

    # ---- runtime helpers ---------------------------------------------------------
    def _update_tool_status(self) -> None:
        lines: list[str] = []
        if self.ffmpeg_paths:
            lines.append(f"FFmpeg detected at {self.ffmpeg_paths.ffmpeg.parent}")
        else:
            lines.append("FFmpeg not found. Burning and FFmpeg-based embedding will be unavailable.")
        if self.mkv_paths:
            lines.append(f"MKVToolNix (mkvmerge) detected at {self.mkv_paths.mkvmerge.parent}")
        else:
            lines.append("MKVToolNix (mkvmerge) not found. Set SRTFORGE_MKV_DIR or install MKVToolNix for soft embedding.")
        detail = "\n".join(lines)

        if not hasattr(self, "status_indicator"):
            return

        # Tiny coloured bullet + short text; hover shows full details
        all_ok = bool(self.ffmpeg_paths)
        color = "#10b981" if all_ok else "#f97316"
        text = "System ready" if all_ok else "Limited: FFmpeg missing"

        self.status_indicator.setText(
            f'<span style="color:{color};">●</span> {text}'
        )
        self.status_indicator.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.status_indicator.setToolTip(detail)

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
        ffprobe = self.ffmpeg_paths.ffprobe if self.ffmpeg_paths else None

        for path in _normalize_paths(files):
            if path in existing:
                continue
            display_name = _elide_filename(path.name, max_chars=60)
            item = QtWidgets.QListWidgetItem(display_name)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, str(path))
            self.queue_list.addItem(item)
            existing.add(path)

            # Cache duration for total in card footer
            try:
                duration_s = _probe_media_duration_ffprobe(path, ffprobe)
            except Exception:
                duration_s = 0.0
            self._queue_duration_cache[str(path)] = float(duration_s or 0.0)

        self._update_start_state()

    def _remove_selected_items(self) -> None:
        for item in self.queue_list.selectedItems():
            path = item.data(QtCore.Qt.ItemDataRole.UserRole)
            row = self.queue_list.row(item)
            self.queue_list.takeItem(row)
            if path:
                self._queue_duration_cache.pop(str(path), None)
        self._update_start_state()

    def _clear_queue(self) -> None:
        self.queue_list.clear()
        self._queue_duration_cache.clear()
        self._update_start_state()

    def _update_start_state(self) -> None:
        has_items = self.queue_list.count() > 0
        self.start_button.setEnabled(has_items and not self._worker)

        # Switch between empty placeholder and actual list
        if hasattr(self, "queue_stack"):
            self.queue_stack.setCurrentWidget(
                self.queue_list if has_items else self.queue_placeholder
            )

        self._update_queue_summary()

    def _update_queue_summary(self) -> None:
        if not hasattr(self, "queue_summary_label"):
            return

        count = self.queue_list.count()
        if count == 0:
            # Empty state: let the central watermark do the talking.
            self.queue_summary_label.setText("")
            return

        total_s = 0.0
        for i in range(count):
            path = self.queue_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole)
            if not path:
                continue
            total_s += float(self._queue_duration_cache.get(str(path), 0.0))

        if total_s <= 0:
            summary = f"{count} file{'s' if count != 1 else ''} in queue"
        else:
            total_seconds = int(round(total_s))
            minutes, secs = divmod(total_seconds, 60)
            hours, minutes = divmod(minutes, 60)
            if hours:
                dur_str = f"{hours:d}:{minutes:02d}:{secs:02d}"
            else:
                dur_str = f"{minutes:02d}:{secs:02d}"
            summary = f"{count} file{'s' if count != 1 else ''} – Total duration: {dur_str}"

        self.queue_summary_label.setText(summary)

    def _set_queue_item_status(self, media: str, status: str) -> None:
        path = Path(media)
        for i in range(self.queue_list.count()):
            item = self.queue_list.item(i)
            raw = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if not raw:
                continue
            if Path(str(raw)) == path:
                short = _elide_filename(path.name, max_chars=60)
                item.setText(f"{short} — {status}")
                break

    # (embed panel handling removed — lives in OptionsDialog now)

    def _start_processing(self) -> None:
        if self._worker:
            return
        files = [self.queue_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole) for i in range(self.queue_list.count())]
        if not files:
            return
        self._clear_eta()
        basic = dict(self._basic_options)
        prefer_gpu = bool(basic.get("prefer_gpu", True))
        options = WorkerOptions(
            prefer_gpu=prefer_gpu,
            embed_subtitles=bool(basic.get("embed_subtitles", False)),
            burn_subtitles=bool(basic.get("burn_subtitles", False)),
            cleanup_gpu=bool(basic.get("cleanup_gpu", False)),
            ffmpeg_bin=str(self.ffmpeg_paths.ffmpeg) if self.ffmpeg_paths else None,
            ffprobe_bin=str(self.ffmpeg_paths.ffprobe) if self.ffmpeg_paths else None,
            soft_embed_method=str(basic.get("soft_embed_method", "auto")),
            mkvmerge_bin=str(self.mkv_paths.mkvmerge) if self.mkv_paths else None,
            srt_title=basic.get("srt_title", "Srtforge (English)"),
            srt_language=basic.get("srt_language", "eng"),
            srt_default=bool(basic.get("srt_default", False)),
            srt_forced=bool(basic.get("srt_forced", False)),
            soft_embed_overwrite_source=bool(
                basic.get("soft_embed_overwrite_source", False)
            ),
            # NEW
            place_srt_next_to_media=bool(basic.get("srt_next_to_media", False)),
            config_path=self._runtime_config_path,
        )
        self._last_worker_options = options
        self._worker = TranscriptionWorker(files, options)
        self._worker.logMessage.connect(self._append_log)
        self._worker.fileStarted.connect(self._on_file_started)
        self._worker.fileCompleted.connect(self._on_file_completed)
        self._worker.fileFailed.connect(self._on_file_failed)
        self._worker.queueFinished.connect(self._on_queue_finished)
        self._worker.runLogReady.connect(self._handle_run_log_ready)
        self._worker.etaMeasured.connect(self._on_eta_measured)
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
        self.options_button.setEnabled(not running)

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _on_file_started(self, path: str) -> None:
        if self._log_tailer:
            self._log_tailer.start()
        self._append_log(f"Processing {path}")
        self._set_queue_item_status(path, "Processing…")
        self._eta_mode_gpu = bool(self._basic_options.get("prefer_gpu", True))
        self._eta_media = path
        ffprobe = self.ffmpeg_paths.ffprobe if self.ffmpeg_paths else None
        duration_s = _probe_media_duration_ffprobe(Path(path), ffprobe)
        estimate = self._eta_memory.estimate(self._eta_mode_gpu, duration_s)
        if estimate > 0:
            self._set_eta(estimate)
        else:
            short = _elide_filename(Path(path).name, max_chars=40)
            self.eta_label.setText(f"Processing {short}")
            if hasattr(self, "progress_bar"):
                self.progress_bar.setVisible(False)

    def _on_file_completed(self, media: str, summary: str) -> None:
        if self._log_tailer:
            self._log_tailer.stop()
        self._append_log(f"✅ {media}: {summary}")
        self._set_queue_item_status(media, "Completed")
        self._clear_eta()

    def _on_file_failed(self, media: str, reason: str) -> None:
        if self._log_tailer:
            self._log_tailer.stop()
        self._append_log(f"⚠️ {media}: {reason}")
        self._set_queue_item_status(media, "Failed")
        self._clear_eta()

    def _handle_run_log_ready(self, run_id: str) -> None:
        if self._log_tailer:
            self._log_tailer.set_run_id(run_id)

    def _on_queue_finished(self, stopped: bool) -> None:
        self._append_log("Queue cancelled" if stopped else "All files processed")
        if self._log_tailer:
            self._log_tailer.stop()
        self._worker = None
        self._set_running_state(False)
        self._update_start_state()
        if (
            self._last_worker_options
            and self._last_worker_options.cleanup_gpu
            and self._last_worker_options.prefer_gpu
        ):
            cleanup_gpu_memory()
            self._append_log("GPU cache cleared")
        self._clear_eta()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: D401 - Qt override
        if self._worker:
            self._worker.request_stop()
            self._worker.wait(2000)
        self._save_persistent_options()
        if self._runtime_config_path:
            try:
                os.unlink(self._runtime_config_path)
            except OSError:
                pass
        super().closeEvent(event)

    def _open_options_dialog(self) -> None:
        initial_basic = dict(self._basic_options)
        dialog = OptionsDialog(parent=self, initial_basic=initial_basic, initial_settings=settings)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        basic = dialog.basic_values()
        self._basic_options = basic
        payload = dialog.settings_payload(prefer_gpu=basic["prefer_gpu"])
        if self._runtime_config_path:
            try:
                os.unlink(self._runtime_config_path)
            except OSError:
                pass
        self._runtime_config_path = self._write_runtime_yaml(payload)
        self._append_log(f"Using custom options for this session ({self._runtime_config_path})")

    def _write_runtime_yaml(self, payload: dict) -> str:
        fd, path = tempfile.mkstemp(prefix="srtforge_gui_", suffix=".yaml")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)
        return path

    def _set_eta(self, seconds: float) -> None:
        self._eta_total = float(seconds)
        if self._eta_total <= 0:
            self._clear_eta()
            return
        self._eta_deadline = time.monotonic() + self._eta_total
        self._tick_eta()
        if not self._eta_timer.isActive():
            self._eta_timer.start()

    def _clear_eta(self) -> None:
        self._eta_deadline = None
        self._eta_total = 0.0
        if hasattr(self, "eta_label"):
            self.eta_label.setText("Idle")
        if hasattr(self, "progress_bar"):
            self.progress_bar.setVisible(False)
            self.progress_bar.setValue(0)
        if self._eta_timer.isActive():
            self._eta_timer.stop()

    def _tick_eta(self) -> None:
        if self._eta_deadline is None or self._eta_total <= 0:
            if hasattr(self, "eta_label"):
                self.eta_label.setText("Idle")
            if hasattr(self, "progress_bar"):
                self.progress_bar.setVisible(False)
            return

        now = time.monotonic()
        remaining = max(0.0, self._eta_deadline - now)
        elapsed = self._eta_total - remaining
        progress = max(0.0, min(1.0, elapsed / self._eta_total))
        percent = int(progress * 100)

        minutes, secs = divmod(int(round(remaining)), 60)
        if self._eta_media:
            basename = _elide_filename(Path(self._eta_media).name, max_chars=40)
        else:
            basename = "current file"

        if hasattr(self, "eta_label"):
            self.eta_label.setText(
                f"Processing {basename} ({percent:3d}%) – ETA ~ {minutes:02d}:{secs:02d}"
            )
        if hasattr(self, "progress_bar"):
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(percent)

        if remaining <= 0:
            self._eta_timer.stop()

    def _on_eta_measured(self, media: str, runtime_s: float, duration_s: float, prefer_gpu: bool) -> None:
        self._eta_memory.update(prefer_gpu, duration_s, runtime_s)


def _load_app_icon() -> QtGui.QIcon:
    """Return the application logo as a QIcon, trimming transparent padding."""

    candidates: list[Path] = []

    # 1) Packaged resource (installed via setuptools)
    try:
        res = resources.files("srtforge.assets.images").joinpath("srtforge_logo.png")
        candidates.append(Path(str(res)))
    except Exception:
        pass

    # 2) Fallbacks for dev checkouts (run-from-source)
    here = Path(__file__).resolve().parent
    candidates.append(here / "assets" / "images" / "srtforge_logo.png")
    candidates.append(here / "srtforge_logo.png")

    for path in candidates:
        if not path.exists():
            continue

        pixmap = QtGui.QPixmap(str(path))
        if pixmap.isNull():
            continue

        # Trim fully transparent rows/columns so we get rid of big borders.
        img = pixmap.toImage()
        rect = img.rect()
        left, right = rect.right(), rect.left()
        top, bottom = rect.bottom(), rect.top()

        for y in range(rect.top(), rect.bottom() + 1):
            for x in range(rect.left(), rect.right() + 1):
                if img.pixelColor(x, y).alpha() > 0:
                    if x < left:
                        left = x
                    if x > right:
                        right = x
                    if y < top:
                        top = y
                    if y > bottom:
                        bottom = y

        if left <= right and top <= bottom:
            img = img.copy(left, top, right - left + 1, bottom - top + 1)
            pixmap = QtGui.QPixmap.fromImage(img)

        return QtGui.QIcon(pixmap)

    return QtGui.QIcon()


def main() -> None:
    """Entry point used by ``srtforge-gui``."""

    QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_EnableHighDpiScaling)
    # Optional: nicer HiDPI rounding on recent Qt builds
    try:
        QtCore.QCoreApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    icon = _load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def _elide_filename(name: str, max_chars: int = 60) -> str:
    """Return ``name`` truncated with an ellipsis if it exceeds ``max_chars``.

    Keeps the extension visible when possible, e.g.
    'really_long_file_name...mkv'.
    """

    if max_chars <= 0:
        return ""
    if len(name) <= max_chars:
        return name
    if max_chars <= 3:
        return "..."[:max_chars]

    base, dot, ext = name.rpartition(".")
    if not dot:
        return name[: max_chars - 3] + "..."

    ext_block = dot + ext  # ".mkv"
    if len(ext_block) + 3 >= max_chars:
        # extension is too long to be clever; just hard-truncate
        return name[: max_chars - 3] + "..."

    keep = max_chars - len(ext_block) - 3
    return base[:keep] + "..." + ext_block

def _probe_media_duration_ffprobe_cmd(ffprobe_bin: Optional[Path], media: Path) -> float:
    exe = str(ffprobe_bin or "ffprobe")
    try:
        out = subprocess.check_output(
            [exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(media)],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return float((out or "0").strip() or 0.0)
    except Exception:
        return 0.0


def _probe_media_duration_ffprobe(media: Path, ffprobe_bin: Optional[Path]) -> float:
    return _probe_media_duration_ffprobe_cmd(ffprobe_bin, media)


class _EtaMemory:
    """Persist simple throughput ratios per device to estimate ETAs."""

    def __init__(self) -> None:
        self.path = LOGS_DIR / "eta_memory.json"
        self.data = {"gpu": {"factor": 1.0, "samples": 0}, "cpu": {"factor": 1.0, "samples": 0}}
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {"gpu": {"factor": 1.0, "samples": 0}, "cpu": {"factor": 1.0, "samples": 0}}

    def _save(self) -> None:
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def estimate(self, prefer_gpu: bool, media_duration_s: float) -> float:
        if media_duration_s <= 0:
            return 0.0
        mode = "gpu" if prefer_gpu else "cpu"
        factor = float(self.data.get(mode, {}).get("factor", 1.0) or 1.0)
        return float(media_duration_s) * factor

    def update(self, prefer_gpu: bool, media_duration_s: float, runtime_s: float) -> None:
        if media_duration_s <= 0 or runtime_s <= 0:
            return
        mode = "gpu" if prefer_gpu else "cpu"
        entry = self.data.get(mode, {"factor": 1.0, "samples": 0})
        samples = int(entry.get("samples", 0))
        current_factor = float(entry.get("factor", 1.0) or 1.0)
        new_factor = float(runtime_s) / float(media_duration_s)
        blended = (current_factor * samples + new_factor) / (samples + 1)
        self.data[mode] = {"factor": blended, "samples": samples + 1}
        self._save()


if __name__ == "__main__":  # pragma: no cover - manual launch helper
    main()