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
from functools import lru_cache
from pathlib import Path
from shutil import which
from typing import Callable, Iterable, List, Optional, TextIO

from collections import deque

import yaml

from PySide6 import QtCore, QtGui, QtWidgets

from .config import DEFAULT_OUTPUT_SUFFIX, PACKAGE_ROOT
from .logging import LATEST_LOG, LOGS_DIR
from .settings import (
    CONFIG_ENV_VAR,
    DEFAULT_CONFIG_FILENAME,
    get_persistent_config_path,
    load_settings,
    settings,
)
from .win11_backdrop import apply_win11_look, get_windows_accent_qcolor

_STARTUP_T0 = time.perf_counter()


def _startup_trace(msg: str) -> None:
    if os.getenv("SRTFORGE_STARTUP_TRACE") != "1":
        return
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        dt = time.perf_counter() - _STARTUP_T0
        with (LOGS_DIR / "startup_trace.log").open("a", encoding="utf-8") as f:
            f.write(f"{dt:8.3f}s  {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# GUI Basic-tab factory defaults (single source of truth).
# These are *GUI-only* defaults, persisted in srtforge.config under "gui".
# ---------------------------------------------------------------------------
DEFAULT_BASIC_OPTIONS: dict[str, object] = {
    "prefer_gpu": True,
    "embed_subtitles": True,
    "burn_subtitles": False,
    "cleanup_gpu": True,
    "soft_embed_method": "auto",
    "soft_embed_overwrite_source": True,
    "srt_title": "Srtforge (English)",
    "srt_language": "eng",
    "srt_default": True,
    "srt_forced": True,
    "srt_next_to_media": False,
}


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


def add_shadow(
    widget: QtWidgets.QWidget,
    *,
    blur_radius: int = 30,
    offset: tuple[int, int] = (0, 12),
    color: Optional[QtGui.QColor] = None,
) -> None:
    """Add a soft drop shadow to widgets to emulate Windows 11 cards."""

    effect = QtWidgets.QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(int(blur_radius))
    effect.setOffset(int(offset[0]), int(offset[1]))
    effect.setColor(color or QtGui.QColor(0, 0, 0, 80))
    widget.setGraphicsEffect(effect)


class QueueItemDelegate(QtWidgets.QStyledItemDelegate):
    """
    Custom delegate for the queue list.

    It draws a single pastel highlight for the whole row and removes the
    inner focus rectangle / darker first cell, so a row looks selected
    only once.
    """

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:  # type: ignore[override]
        # Copy + initialise the standard style option
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        # If this cell has an index widget installed (via setItemWidget), don't
        # paint the model text/icon as well. Otherwise the text can "ghost"
        # through translucent widgets (e.g. the Metadata chips) and look like it
        # was written twice.
        try:
            view = opt.widget
            if isinstance(view, QtWidgets.QAbstractItemView):
                if view.indexWidget(index) is not None:
                    opt.text = ""
                    opt.icon = QtGui.QIcon()
        except Exception:
            # Painting must never crash the UI.
            pass

        style = opt.widget.style() if opt.widget else QtWidgets.QApplication.style()

        painter.save()

        # Remember if this cell is selected
        is_selected = bool(opt.state & QtWidgets.QStyle.StateFlag.State_Selected)

        if is_selected:
            # Use the app's accent colour as a soft background so it works
            # in both light and dark themes.
            highlight = opt.palette.color(QtGui.QPalette.ColorRole.Highlight)
            bg = QtGui.QColor(highlight)
            bg.setAlpha(40)  # ≈ 15–20% opacity → Win11‑style pastel
            painter.fillRect(opt.rect, bg)

            # Don't let Qt apply hover styling on top of our selection
            opt.state &= ~QtWidgets.QStyle.StateFlag.State_MouseOver

        # Prevent Qt / the stylesheet from drawing *another* selection/focus box
        opt.state &= ~QtWidgets.QStyle.StateFlag.State_HasFocus
        opt.state &= ~QtWidgets.QStyle.StateFlag.State_Selected

        # Let Qt draw text, icon, etc. as if the cell was unselected
        style.drawControl(
            QtWidgets.QStyle.ControlElement.CE_ItemViewItem,
            opt,
            painter,
            opt.widget,
        )

        painter.restore()


STATUS_SORT_ORDER = {
    "Queued": 0,
    "Processing…": 1,
    "Completed": 2,
    "Failed": 3,
}


class QueueTreeWidgetItem(QtWidgets.QTreeWidgetItem):
    """Custom item so Name / Duration / Status sort sanely."""

    def __lt__(self, other: "QtWidgets.QTreeWidgetItem") -> bool:  # type: ignore[override]
        tree = self.treeWidget()
        # If we're not attached to a tree yet, just fall back to name text.
        if tree is None:
            return (self.text(0) or "") < (other.text(0) or "")

        column = tree.sortColumn()

        # Duration column (index 2) – sort by numeric seconds stored in UserRole.
        if column == 2:
            self_val = self.data(column, QtCore.Qt.ItemDataRole.UserRole)
            other_val = other.data(column, QtCore.Qt.ItemDataRole.UserRole)
            try:
                return float(self_val or 0.0) < float(other_val or 0.0)
            except (TypeError, ValueError):
                # If anything weird happens, fall back to text comparison below.
                pass

        # Status column (index 1) – use STATUS_SORT_ORDER, then fall back to name.
        if column == 1:
            self_status = self.text(column)
            other_status = other.text(column)
            self_rank = STATUS_SORT_ORDER.get(self_status, 999)
            other_rank = STATUS_SORT_ORDER.get(other_status, 999)
            if self_rank != other_rank:
                return self_rank < other_rank
            # Tie‑breaker: file name.
            return (self.text(0) or "") < (other.text(0) or "")

        # Any other column (Name, ETA, etc.): case‑insensitive text compare.
        self_text = (self.text(column) or "").lower()
        other_text = (other.text(column) or "").lower()
        return self_text < other_text


class NoFocusFrameStyle(QtWidgets.QProxyStyle):
    """
    Proxy style that disables the dotted focus rectangle around item views.

    This removes the extra inner box that appears on top of the normal
    selection highlight in the queue list.
    """

    def drawPrimitive(
        self,
        element: QtWidgets.QStyle.PrimitiveElement,
        option: QtWidgets.QStyleOption,
        painter: QtGui.QPainter,
        widget: Optional[QtWidgets.QWidget] = None,
    ) -> None:  # type: ignore[override]
        if element == QtWidgets.QStyle.PrimitiveElement.PE_FrameFocusRect:
            # Skip drawing the focus frame entirely
            return
        super().drawPrimitive(element, option, painter, widget)


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


class _DurationProbeEmitter(QtCore.QObject):
    """Thread-safe signal bridge for async media probing."""

    durationReady = QtCore.Signal(str, float)


class _DurationProbeTask(QtCore.QRunnable):
    """Background task that probes media duration without blocking the UI."""

    def __init__(
        self,
        media_path: Path,
        ffprobe_bin: Optional[Path],
        emitter: _DurationProbeEmitter,
    ) -> None:
        super().__init__()
        self._media_path = media_path
        self._ffprobe_bin = ffprobe_bin
        self._emitter = emitter

    def run(self) -> None:  # noqa: D401 - QRunnable override
        try:
            duration_s = float(_probe_media_duration_ffprobe_cmd(self._ffprobe_bin, self._media_path) or 0.0)
        except Exception:
            duration_s = 0.0
        # Emitting across threads is safe; Qt will queue-deliver to the UI thread.
        self._emitter.durationReady.emit(str(self._media_path), duration_s)



class _StreamProbeEmitter(QtCore.QObject):
    """Thread-safe signal bridge for async audio/video stream probing."""

    streamReady = QtCore.Signal(str, str, str)


class _StreamProbeTask(QtCore.QRunnable):
    """Background task that probes audio/video stream info without blocking the UI."""

    def __init__(
        self,
        media_path: Path,
        ffprobe_bin: Optional[Path],
        emitter: _StreamProbeEmitter,
    ) -> None:
        super().__init__()
        self._media_path = media_path
        self._ffprobe_bin = ffprobe_bin
        self._emitter = emitter

    def run(self) -> None:  # noqa: D401 - QRunnable override
        try:
            audio_text, video_text = _probe_media_streams_ffprobe_cmd(
                self._ffprobe_bin, self._media_path
            )
        except Exception:
            audio_text, video_text = ETA_PLACEHOLDER, ETA_PLACEHOLDER
        # Emitting across threads is safe; Qt will queue-deliver to the UI thread.
        self._emitter.streamReady.emit(
            str(self._media_path), str(audio_text), str(video_text)
        )


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
                self._file_media_duration = self._probe_media_duration_seconds(media_path)

                # Measure *transcription* time only (the CLI pipeline). Optional
                # post-processing (soft-embed / burn) is excluded so ETA training
                # reflects the transcription workload accurately.
                pipeline_start_ts = time.perf_counter()
                self._file_start_ts = pipeline_start_ts

                self._log_timing(f"{media_path.name}: pipeline start")
                srt_path = self._run_pipeline_subprocess(media_path)
                pipeline_runtime_s = max(0.0, time.perf_counter() - pipeline_start_ts)
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
                self.etaMeasured.emit(
                    str(media_path),
                    float(pipeline_runtime_s),
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

        # Keep only a bounded tail of output to avoid unbounded memory growth
        # when ffmpeg/NeMo emits lots of logs.
        stdout_parts = deque(maxlen=250)  # type: ignore[var-annotated]
        stderr_parts = deque(maxlen=250)  # type: ignore[var-annotated]
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

        stdout = "".join(list(stdout_parts))
        stderr = "".join(list(stderr_parts))
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


def _qcolor_to_rgba(color: QtGui.QColor) -> str:
    """Return a Qt stylesheet-ready color string.

    Qt's QSS `rgba(r,g,b,a)` expects alpha in 0-255.
    """
    c = QtGui.QColor(color)
    if not c.isValid():
        return "#000000"
    if c.alpha() >= 255:
        return c.name()
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"



class _ComboPopupFixer(QtCore.QObject):
    """Fix ComboBox dropdown popups looking "boxed" on Windows.

    On Windows (and with some Qt styles), a QComboBox popup can keep a square
    frame / shadow even if you round the internal QListView via QSS. That shows
    up as a sharp rectangle (and sometimes black bars) around a dropdown that is
    meant to look like a Win11 rounded menu.

    This helper patches the *popup container window* that Qt creates for the
    dropdown so it matches the styled list:

    - removes the native frame/drop-shadow hints (best-effort)
    - forces the popup background to be painted (prevents black bars)
    - applies a rounded mask so the popup window itself is clipped
    - keeps it defensive + non-blocking (no infinite style repolish loops)
    """

    def __init__(self, combo: QtWidgets.QComboBox, *, radius: int = 12) -> None:
        super().__init__(combo)
        self._combo = combo
        self._radius = max(0, int(radius))

        # Force creation of the internal view early.
        self._view: QtWidgets.QAbstractItemView = combo.view()
        self._popup: Optional[QtWidgets.QWidget] = None

        # Re-entrancy / event-loop safety.
        self._polishing = False
        self._last_qss: str = ""

        # Avoid a second border coming from the view itself.
        try:
            self._view.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)  # type: ignore[attr-defined]
        except Exception:
            pass
        self._view.setContentsMargins(0, 0, 0, 0)

        # Hook events on the view; the popup container gets hooked once discovered.
        self._view.installEventFilter(self)
        self._ensure_popup_hook()

    def _ensure_popup_hook(self) -> None:
        """Discover and hook the real popup container window."""

        try:
            popup = self._view.window()
        except Exception:
            return
        if not isinstance(popup, QtWidgets.QWidget):
            return

        # Only hook the dropdown popup (Qt::Popup). Guard against accidentally
        # grabbing the dialog/main window and changing its flags.
        try:
            if popup is self._combo.window():
                return
            if not (popup.windowFlags() & QtCore.Qt.WindowType.Popup):
                return
        except Exception:
            return

        if popup is self._popup:
            return

        self._popup = popup
        popup.installEventFilter(self)

    def _try_apply_dwm_rounding(self, popup: QtWidgets.QWidget) -> None:
        """Ask DWM for rounded corners on Windows 11 (best-effort)."""
        if sys.platform != "win32":  # pragma: no cover - Windows only
            return
        try:
            import ctypes  # local import: keep non-Windows safe
        except Exception:
            return
        try:
            hwnd = int(popup.winId())
        except Exception:
            return
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2
        try:
            value = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
                hwnd,
                33,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except Exception:
            pass

    def _apply_mask(self, popup: QtWidgets.QWidget) -> None:
        """Apply a rounded window mask so the popup is actually clipped."""

        if self._radius <= 0:
            popup.clearMask()
            return

        rect = popup.rect()
        if rect.isEmpty():
            return

        # NOTE: don't shrink the rect. Shrinking can leave a 1px "halo" where
        # the unmasked background shows through (it looks like a weird boundary).
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(rect), float(self._radius), float(self._radius))
        popup.setMask(QtGui.QRegion(path.toFillPolygon().toPolygon()))

    def _build_qss(self) -> str:
        """Build a theme-aware stylesheet for the popup and its list view.

        In dark mode we force a neutral black/grey palette for the dropdown
        itself (Windows/Qt can otherwise inject a blue-tinted popup background).
        """

        pal = self._combo.palette()

        window_col = pal.color(QtGui.QPalette.ColorRole.Window)
        is_dark = window_col.lightness() < 128

        # Use AlternateBase so the dropdown matches our "surface" color (queue/menu)
        # instead of the inset field background.
        if is_dark:
            bg = "#0C0C0E"
        else:
            bg = _qcolor_to_rgba(pal.color(QtGui.QPalette.ColorRole.AlternateBase))

        text = _qcolor_to_rgba(pal.color(QtGui.QPalette.ColorRole.Text))

        if is_dark:
            # Neutral greys (avoid blue/green selection in the popup list).
            border = "rgba(227,227,230,0.12)"
            hover_bg = "rgba(227,227,230,0.06)"
            sel_bg = "rgba(227,227,230,0.10)"
            sel_text = text
        else:
            border = "rgba(0,0,0,0.08)"
            hover_bg = "rgba(0,0,0,0.04)"
            sel_bg = _qcolor_to_rgba(pal.color(QtGui.QPalette.ColorRole.Highlight))
            sel_text = _qcolor_to_rgba(pal.color(QtGui.QPalette.ColorRole.HighlightedText))

        # IMPORTANT:
        #   Paint the background on the *popup container* so there are no
        #   unpainted areas (a common source of black bars).
        #   Keep the border minimal so there's no obvious extra "outline box".
        return f"""
        QWidget#SrtforgeComboPopup {{
            background-color: {bg};
            border: 1px solid {border};
            border-radius: {self._radius}px;
            padding: 0px;
            margin: 0px;
        }}
        QWidget#SrtforgeComboPopup QListView {{
            background: transparent;
            border: none;
            outline: 0;
            padding: 4px 0px;
            margin: 0px;
        }}
        QWidget#SrtforgeComboPopup QListView::item {{
            padding: 6px 12px;
            margin: 0px 6px;
            border-radius: 8px;
            color: {text};
        }}
        QWidget#SrtforgeComboPopup QListView::item:hover:!selected {{
            background-color: {hover_bg};
        }}
        QWidget#SrtforgeComboPopup QListView::item:selected {{
            background-color: {sel_bg};
            color: {sel_text};
        }}
        """

    def _polish(self) -> None:
        """(Re)apply popup flags + QSS + masking."""

        if self._polishing:
            return
        self._polishing = True
        try:
            self._ensure_popup_hook()
            popup = self._popup
            if not isinstance(popup, QtWidgets.QWidget):
                return

            # Remove the system frame/shadow where possible.
            # Avoid relying on translucency (it can cause black bars on some builds).
            try:
                flags = popup.windowFlags()
                wanted = (
                    flags
                    | QtCore.Qt.WindowType.FramelessWindowHint
                    | QtCore.Qt.WindowType.NoDropShadowWindowHint
                )
                if wanted != flags:
                    geo = popup.geometry()
                    was_visible = popup.isVisible()
                    popup.setWindowFlags(wanted)
                    popup.setGeometry(geo)
                    if was_visible:
                        popup.show()
            except Exception:
                pass

            popup.setContentsMargins(0, 0, 0, 0)
            if popup.layout() is not None:
                try:
                    popup.layout().setContentsMargins(0, 0, 0, 0)
                    popup.layout().setSpacing(0)
                except Exception:
                    pass

            if isinstance(popup, QtWidgets.QFrame):
                popup.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
                popup.setLineWidth(0)
                popup.setMidLineWidth(0)

            if popup.objectName() != "SrtforgeComboPopup":
                popup.setObjectName("SrtforgeComboPopup")

            # Ensure the widget paints its own background (no black gaps).
            popup.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
            try:
                popup.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, False)
            except Exception:
                pass

            self._try_apply_dwm_rounding(popup)

            qss = self._build_qss().strip()
            if qss and qss != self._last_qss:
                popup.setStyleSheet(qss)
                self._last_qss = qss

            self._apply_mask(popup)
        finally:
            self._polishing = False

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # noqa: N802
        try:
            if watched in (self._view, self._popup):
                if event.type() in (
                    QtCore.QEvent.Type.Polish,
                    QtCore.QEvent.Type.Show,
                    QtCore.QEvent.Type.Resize,
                    QtCore.QEvent.Type.PaletteChange,
                    QtCore.QEvent.Type.ApplicationPaletteChange,
                ):
                    self._polish()
        except Exception:
            pass
        return super().eventFilter(watched, event)


class OptionsDialog(QtWidgets.QDialog):
    """Popup dialog with Basic, Performance, and Advanced tabs for configuration."""

    def __init__(self, *, parent=None, initial_basic: dict, initial_settings) -> None:
        super().__init__(parent)
        # Helps QSS target the Options dialog without affecting native/system dialogs.
        self.setObjectName("OptionsDialog")
        self.setWindowTitle("Options")
        self.resize(760, 520)
        self._eta_reset_requested = False
        self._defaults_reset_requested = False
        layout = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget(self)
        self.tabs.setObjectName("OptionsTabs")
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

        # Compact "Clear ETA" action (aligned like dialog buttons)
        self.reset_eta_button = QtWidgets.QPushButton("Clear ETA history")
        self.reset_eta_button.setObjectName("ResetEtaButton")
        self.reset_eta_button.setToolTip(
            "Clear stored ETA measurements so future runs can retrain from scratch."
        )
        self.reset_eta_button.clicked.connect(self._on_reset_eta_clicked)

        # NEW: Reset all GUI options back to shipped defaults (including theme)
        self.reset_defaults_button = QtWidgets.QPushButton("Reset to defaults")
        self.reset_defaults_button.setObjectName("ResetDefaultsButton")
        self.reset_defaults_button.setToolTip(
            "Reset Basic / Performance / Advanced options back to defaults. "
            "Also switches the app back to light mode when you press OK."
        )
        self.reset_defaults_button.clicked.connect(self._on_reset_defaults_clicked)
        eta_row = QtWidgets.QHBoxLayout()
        eta_row.addStretch()
        eta_row.addWidget(self.reset_defaults_button)
        eta_row.addWidget(self.reset_eta_button)
        grid.addLayout(eta_row, row, 0, 1, 2)
        row += 1

        # Push any extra vertical space below the controls
        grid.setRowStretch(row, 1)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        self.tabs.addTab(basic, "Basic")

        # ----- Performance tab (hardware tuning; defaults preserve existing behavior) ---
        perf = QtWidgets.QWidget()
        perf_form = QtWidgets.QFormLayout(perf)

        # Force float32 (moved from Advanced -> Performance)
        self.force_f32 = QtWidgets.QCheckBox()
        self.force_f32.setChecked(bool(initial_settings.parakeet.force_float32))
        self.force_f32.setToolTip(
            "Force float32 for Parakeet inference. Helps stability on some GPUs, but may be slower."
        )
        perf_form.addRow("Force float32 (Parakeet)", self.force_f32)

        # Local attention window (rel_pos_local_attn)
        rel_attn = getattr(initial_settings.parakeet, "rel_pos_local_attn", [768, 768]) or [768, 768]
        try:
            left_default = int(rel_attn[0]) if len(rel_attn) > 0 else 768
            right_default = int(rel_attn[1]) if len(rel_attn) > 1 else 768
        except Exception:
            left_default, right_default = 768, 768

        self.local_attn_left = QtWidgets.QSpinBox()
        self.local_attn_left.setRange(32, 4096)
        self.local_attn_left.setSingleStep(32)
        self.local_attn_left.setValue(left_default)

        self.local_attn_right = QtWidgets.QSpinBox()
        self.local_attn_right.setRange(32, 4096)
        self.local_attn_right.setSingleStep(32)
        self.local_attn_right.setValue(right_default)

        attn_row = QtWidgets.QHBoxLayout()
        attn_widget = QtWidgets.QWidget()
        attn_widget.setLayout(attn_row)
        attn_row.addWidget(QtWidgets.QLabel("Left"))
        attn_row.addWidget(self.local_attn_left)
        attn_row.addWidget(QtWidgets.QLabel("Right"))
        attn_row.addWidget(self.local_attn_right)
        attn_row.addStretch(1)

        attn_widget.setToolTip(
            "Local attention window used when Parakeet applies long-audio settings (rel_pos_local_attn). "
            "Lower values can reduce VRAM usage on weaker GPUs for long inputs. "
            "Default: 768, 768 (matches current behaviour)."
        )
        perf_form.addRow("Local attention window", attn_widget)

        # Subsampling conv chunking (factor=1)
        self.subsampling_conv_chunking = QtWidgets.QCheckBox("Enable (factor=1)")
        self.subsampling_conv_chunking.setChecked(
            bool(getattr(initial_settings.parakeet, "subsampling_conv_chunking", False))
        )
        self.subsampling_conv_chunking.setToolTip(
            "Enable Parakeet subsampling conv chunking (factor=1). "
            "Can reduce memory pressure on long audio, but may reduce throughput. "
            "Default: Off."
        )
        perf_form.addRow("Subsampling conv chunking", self.subsampling_conv_chunking)

        # GPU limiter (%)
        self.gpu_limit_spin = QtWidgets.QSpinBox()
        self.gpu_limit_spin.setRange(10, 100)
        self.gpu_limit_spin.setSingleStep(5)
        self.gpu_limit_spin.setValue(int(getattr(initial_settings.parakeet, "gpu_limit_percent", 100) or 100))
        self.gpu_limit_spin.setSuffix("%")
        self.gpu_limit_spin.setToolTip(
            "Best-effort GPU limiter. 100% preserves current behaviour. "
            "When set below 100%, Srtforge will attempt to keep the desktop responsive by limiting the CUDA allocator "
            "memory fraction and running inference on a low-priority CUDA stream."
        )
        perf_form.addRow("VRAM limit (best effort)", self.gpu_limit_spin)

        # Low-priority CUDA stream (independent of VRAM limit)
        self.low_pri_cuda_stream = QtWidgets.QCheckBox()
        self.low_pri_cuda_stream.setChecked(
            bool(getattr(initial_settings.parakeet, "use_low_priority_cuda_stream", False))
        )
        self.low_pri_cuda_stream.setToolTip(
            "Run Parakeet inference on a low-priority CUDA stream so the desktop stays responsive. "
            "This is independent of the VRAM limit. Default: Off."
        )
        perf_form.addRow("Low-priority CUDA stream", self.low_pri_cuda_stream)

        self.tabs.addTab(perf, "Performance")

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

        # Fix Windows ComboBox dropdown chrome (square outline/shadow + sharp corners).
        # Applies to:
        #   - Device (Basic tab)
        #   - Soft-embed method (Basic tab)
        #   - Separation backend (Advanced tab)
        self._combo_popup_fixers = [
            _ComboPopupFixer(self.device_combo, radius=12),
            _ComboPopupFixer(self.embed_method, radius=12),
            _ComboPopupFixer(self.backend, radius=12),
        ]


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
        self.ff_extraction_mode = QtWidgets.QComboBox()
        self.ff_extraction_mode.addItem("Stereo Mix (Default)", "stereo_mix")
        self.ff_extraction_mode.addItem("Dual Mono (Center Isolation)", "dual_mono_center")
        current_mode = (getattr(initial_settings.ffmpeg, "extraction_mode", "stereo_mix") or "stereo_mix")
        current_mode = str(current_mode).strip().lower()
        idx = self.ff_extraction_mode.findData(current_mode)
        if idx < 0:
            idx = self.ff_extraction_mode.findData("stereo_mix")
        self.ff_extraction_mode.setCurrentIndex(max(0, idx))
        self.ff_extraction_mode.setToolTip(
            "Stereo Mix: standard downmix of all channels (safest).\n"
            "Dual Mono: if the source has a Center (FC) channel, extract it and map to L/R."
        )
        form.addRow("Audio extraction mode", self.ff_extraction_mode)
        # Keep the Windows 11 popup chrome fixes consistent.
        try:
            self._combo_popup_fixers.append(_ComboPopupFixer(self.ff_extraction_mode, radius=12))
        except Exception:
            pass

        self.filter_chain = QtWidgets.QPlainTextEdit(initial_settings.ffmpeg.filter_chain or "")
        self.filter_chain.setMinimumHeight(80)
        form.addRow("FFmpeg filter chain", self.filter_chain)

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
                "extraction_mode": str(self.ff_extraction_mode.currentData() or "stereo_mix"),
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
                "rel_pos_local_attn": [int(self.local_attn_left.value()), int(self.local_attn_right.value())],
                "subsampling_conv_chunking": self.subsampling_conv_chunking.isChecked(),
                "gpu_limit_percent": int(self.gpu_limit_spin.value()),
                "use_low_priority_cuda_stream": self.low_pri_cuda_stream.isChecked(),
            },
        }

    def _on_reset_eta_clicked(self) -> None:
        """Mark ETA memory for reset and inform the user."""
        self._eta_reset_requested = True
        QtWidgets.QMessageBox.information(
            self,
            "ETA training reset",
            (
                "Existing ETA measurements will be cleared when you press OK.\n"
                "The next few runs may have less accurate ETAs while the model retrains."
            ),
        )

    def _on_reset_defaults_clicked(self) -> None:
        """Reset every option in the dialog back to shipped defaults."""

        resp = QtWidgets.QMessageBox.question(
            self,
            "Reset to defaults",
            (
                "This will reset all options in Basic, Performance, and Advanced back to their default values.\n\n"
                "Theme note: Light mode will be applied when you press OK.\n\n"
                "Continue?"
            ),
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if resp != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self._defaults_reset_requested = True

        # ---- Basic defaults (single source of truth) ------------------------
        default_basic = DEFAULT_BASIC_OPTIONS

        try:
            self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(True)))
        except Exception:
            self.device_combo.setCurrentIndex(0)

        self.embed_checkbox.setChecked(bool(default_basic["embed_subtitles"]))

        try:
            self.embed_method.setCurrentIndex(
                max(0, self.embed_method.findData(str(default_basic.get("soft_embed_method", "auto"))))
            )
        except Exception:
            self.embed_method.setCurrentIndex(0)

        self.title_edit.setText(str(default_basic.get("srt_title", "Srtforge (English)")))
        self.lang_edit.setText(str(default_basic.get("srt_language", "eng")))
        self.default_cb.setChecked(bool(default_basic.get("srt_default", False)))
        self.forced_cb.setChecked(bool(default_basic.get("srt_forced", False)))
        self.embed_overwrite_cb.setChecked(
            bool(default_basic.get("soft_embed_overwrite_source", False))
        )
        self.external_srt_cb.setChecked(bool(default_basic.get("srt_next_to_media", False)))
        self.burn_cb.setChecked(bool(default_basic.get("burn_subtitles", False)))
        self.cleanup_cb.setChecked(bool(default_basic.get("cleanup_gpu", False)))

        # ---- Performance defaults (from shipped config.yaml -> `settings`) ---
        self.force_f32.setChecked(bool(getattr(settings.parakeet, "force_float32", True)))

        rel_attn = getattr(settings.parakeet, "rel_pos_local_attn", [768, 768]) or [768, 768]
        try:
            left_default = int(rel_attn[0]) if len(rel_attn) > 0 else 768
            right_default = int(rel_attn[1]) if len(rel_attn) > 1 else 768
        except Exception:
            left_default, right_default = 768, 768
        self.local_attn_left.setValue(left_default)
        self.local_attn_right.setValue(right_default)

        self.subsampling_conv_chunking.setChecked(
            bool(getattr(settings.parakeet, "subsampling_conv_chunking", False))
        )
        self.gpu_limit_spin.setValue(int(getattr(settings.parakeet, "gpu_limit_percent", 100) or 100))
        self.low_pri_cuda_stream.setChecked(
            bool(getattr(settings.parakeet, "use_low_priority_cuda_stream", False))
        )

        # ---- Advanced defaults (paths + separation + ffmpeg) ----------------
        try:
            self.output_dir.setText(str(getattr(settings.paths, "output_dir", "") or ""))
            self.temp_dir.setText(str(getattr(settings.paths, "temp_dir", "") or ""))
        except Exception:
            self.output_dir.setText("")
            self.temp_dir.setText("")

        backend_value = (getattr(settings.separation, "backend", "fv4") or "fv4").lower()
        self.backend.setCurrentIndex(max(0, self.backend.findData(backend_value)))
        self.sep_hz.setValue(int(getattr(settings.separation, "sep_hz", 48000) or 48000))
        self.sep_prefer_center.setChecked(bool(getattr(settings.separation, "prefer_center", False)))
        self.allow_untagged.setChecked(
            bool(getattr(settings.separation, "allow_untagged_english", False))
        )

        # FFmpeg extraction mode (dropdown)
        try:
            default_mode = getattr(settings.ffmpeg, "extraction_mode", "stereo_mix") or "stereo_mix"
            default_mode = str(default_mode).strip().lower()
            idx = self.ff_extraction_mode.findData(default_mode)
            if idx < 0:
                idx = self.ff_extraction_mode.findData("stereo_mix")
            self.ff_extraction_mode.setCurrentIndex(max(0, idx))
        except Exception:
            self.ff_extraction_mode.setCurrentIndex(max(0, self.ff_extraction_mode.findData("stereo_mix")))
        self.filter_chain.setPlainText(
            str(getattr(settings.ffmpeg, "filter_chain", "") or "")
        )

    def eta_reset_requested(self) -> bool:
        """Return True if the user requested ETA training reset in this session."""
        return bool(self._eta_reset_requested)

    def defaults_reset_requested(self) -> bool:
        """Return True if the user requested a full reset-to-defaults in this session."""
        return bool(self._defaults_reset_requested)


class MainWindow(QtWidgets.QMainWindow):
    """Main application window."""

    def __init__(self, app_icon: Optional[QtGui.QIcon] = None) -> None:
        _startup_trace("MainWindow: enter")
        super().__init__()
        if os.getenv("SRTFORGE_STARTUP_TRACE") == "1":
            self._boot_t0 = time.perf_counter()
        # Capitalize brand and open at the size in the screenshot
        self.setWindowTitle("Srtforge Studio")
        self.resize(1200, 720)
        self.setMinimumSize(960, 640)
        self.setObjectName("MainWindow")

        icon = app_icon or _load_app_icon()
        _startup_trace("MainWindow: _load_app_icon done")
        self._app_icon = icon  # store for reuse
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.setAcceptDrops(True)
        self._worker: Optional[TranscriptionWorker] = None
        self._log_tailer: Optional[LogTailer] = None
        self._last_worker_options: Optional[WorkerOptions] = None
        self._runtime_config_path: Optional[str] = None

        # Single persistent settings file (YAML content with a .config extension).
        # This replaces the per-session temp YAML that used to live under %TEMP%.
        self._config_path: Path = get_persistent_config_path()

        # Theme state (persisted via srtforge.config)
        self._dark_mode: bool = False
        # Single source of truth for user options (kept only in the Options dialog)
        self._basic_options = dict(DEFAULT_BASIC_OPTIONS)
        self.ffmpeg_paths = locate_ffmpeg_binaries()
        _startup_trace("MainWindow: locate_ffmpeg_binaries done")
        self.mkv_paths = locate_mkvmerge_binary()
        _startup_trace("MainWindow: locate_mkvmerge_binary done")
        self._qsettings = QtCore.QSettings("srtforge", "SrtforgeStudio")
        # Persist the last folder used in the "Add files…" dialog across app restarts.
        # (Qt remembers it only for the current process by default.)
        self._last_media_dir: str = ""
        self._eta_timer = QtCore.QTimer(self)
        self._eta_timer.setInterval(1000)
        self._eta_timer.timeout.connect(self._tick_eta)
        self._eta_deadline: Optional[float] = None
        self._eta_mode_gpu: bool = True
        self._eta_media: Optional[str] = None
        self._eta_memory = _EtaMemory()
        # Track per-file durations for the “Total duration” summary
        self._queue_duration_cache: dict[str, float] = {}
        # Per-row progress widgets (queue list column)
        self._item_progress: dict[str, QtWidgets.QProgressBar] = {}
        # NEW: per-row "Open…" buttons
        self._open_buttons: dict[str, QtWidgets.QToolButton] = {}
        # NEW: per-file output artifacts (SRT, diagnostics, log)
        # keys: media path string; values: {"srt": str, "diag_csv": str, "diag_json": str, "run_id": str, "log": str}
        self._item_outputs: dict[str, dict[str, str]] = {}
        # NEW: map media path -> run_id for opening logs
        self._file_run_ids: dict[str, str] = {}
        # Queue-level progress (for the footer progress bar)
        self._queue_total_count: int = 0
        self._queue_completed_count: int = 0
        # Track the current ETA window so we can compute % progress
        self._eta_total: float = 0.0
        # Animated folder GIFs for per-row Output buttons (keyed by media path).
        # Keep one QMovie per row so hover playback is instant (avoid reloading/decoding
        # the GIF on every mouse-enter).
        self._folder_movies: dict[str, QtGui.QMovie] = {}
        # Monotonic hover generation per row so stale signals can't clobber newer hovers.
        self._folder_hover_gen: dict[str, int] = {}

        # Buffered log appender so chatty subprocesses don't freeze the UI.
        self._log_buffer: list[str] = []
        self._log_flush_timer = QtCore.QTimer(self)
        self._log_flush_timer.setInterval(50)
        self._log_flush_timer.timeout.connect(self._flush_log_buffer)

        # Async duration probing avoids blocking the main thread when adding files.
        self._duration_probe = _DurationProbeEmitter(self)
        self._duration_probe.durationReady.connect(self._on_duration_probed)

        # Async stream probing fills the Metadata column without blocking the UI.
        self._stream_probe = _StreamProbeEmitter(self)
        self._stream_probe.streamReady.connect(self._on_stream_probed)
        self._boot_mark("_build_ui: start")
        self._build_ui()  # builds a page widget; we wrap it in a scroll area below
        _startup_trace("MainWindow: _build_ui done")
        self._boot_mark("_build_ui: end")
        self._log_tailer = LogTailer(self._append_log, self)
        self._load_persistent_options()
        _startup_trace("MainWindow: _load_persistent_options done")
        self._apply_styles()
        _startup_trace("MainWindow: _apply_styles done")
        self._update_tool_status()
        _startup_trace("MainWindow: _update_tool_status done")
        apply_win11_look(self)
        _startup_trace("MainWindow: apply_win11_look done")

    def _boot_mark(self, label: str) -> None:
        t0 = getattr(self, "_boot_t0", None)
        if t0 is None:
            return
        dt = time.perf_counter() - t0
        print(f"{dt:8.3f}s  {label}", flush=True)

    # ---- UI construction ---------------------------------------------------------
    def _build_ui(self) -> None:
        page = QtWidgets.QWidget()
        page.setObjectName("MainPage")
        layout = QtWidgets.QVBoxLayout(page)
        # No extra vertical gap above/below the header; keep side padding
        layout.setSpacing(0)
        # A small bottom margin prevents card shadows from being clipped by the
        # scroll-area viewport edge (which otherwise creates sharp cut-offs).
        layout.setContentsMargins(16, 0, 16, 8)
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
        # Tiny vertical padding so the logo/title don't touch the titlebar
        # or the queue card (keep this subtle, Win11-style).
        brand_row.setContentsMargins(0, 4, 0, 4)
        brand_row.setSpacing(0)


        logo_label = QtWidgets.QLabel()
        logo_label.setContentsMargins(0, 0, 0, 0)
        # Remove all padding/margins around the logo so the pixmap box is tight
        logo_label.setStyleSheet("margin: 0px; padding: 0px;")

        # Jellyfin-style branding: logo slightly larger than text with a small gap.
        LOGO_SIZE = 72
        LOGO_TEXT_GAP = 10

        has_logo = False

        icon = getattr(self, "_app_icon", _load_app_icon())
        if icon and not icon.isNull():
            # Keep the logo at the same visual size, but with no extra border
            logo_pix = icon.pixmap(LOGO_SIZE, LOGO_SIZE)
            logo_label.setPixmap(logo_pix)

            has_logo = True

            # HiDPI fix: QIcon.pixmap() may return a device-pixel-scaled pixmap.
            # QLabel geometry is in device-independent pixels, so size the label
            # using the pixmap's device-independent size to avoid fake 'padding'.
            try:
                dpr = float(logo_pix.devicePixelRatioF())
            except Exception:
                try:
                    dpr = float(logo_pix.devicePixelRatio())
                except Exception:
                    dpr = 1.0
            if not dpr or dpr <= 0:
                dpr = 1.0
            w = int(round(logo_pix.width() / dpr))
            h = int(round(logo_pix.height() / dpr))
            if w > 0 and h > 0:
                logo_label.setFixedSize(w, h)
            logo_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        brand_row.addWidget(logo_label, 0, QtCore.Qt.AlignVCenter)
        if has_logo:
            brand_row.addSpacing(LOGO_TEXT_GAP)

        title = QtWidgets.QLabel("SrtForge\nStudio")
        title.setObjectName("HeaderLabel")
        title.setContentsMargins(0, 0, 0, 0)
        title.setMargin(0)
        title.setIndent(0)
        # Completely kill padding/margins so text hugs the logo
        title.setStyleSheet("margin: 0px; padding: 0px;")

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
        # Make the glyph a bit larger so the emoji doesn't look tiny
        # inside the circular button.
        try:
            f = self.theme_toggle.font()
            pt = f.pointSize()
            if pt <= 0:
                pt = 12
            f.setPointSize(max(pt, 18))
            self.theme_toggle.setFont(f)
        except Exception:
            pass
        self.theme_toggle.toggled.connect(self._on_theme_toggled)
        actions_row.addWidget(self.theme_toggle)
        self.options_button = QtWidgets.QToolButton()
        self.options_button.setObjectName("OptionsButton")
        self.options_button.setCursor(pointer_cursor)
        self.options_button.setToolTip("Options")

        # Prefer a gear icon from the current icon theme, with a Unicode fallback.
        gear_icon = QtGui.QIcon.fromTheme("settings")
        if gear_icon.isNull():
            gear_icon = QtGui.QIcon.fromTheme("preferences-system")

        if not gear_icon.isNull():
            self.options_button.setIcon(gear_icon)
            self.options_button.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
            # Slightly larger icon to better match the theme toggle.
            self.options_button.setIconSize(QtCore.QSize(24, 24))
        else:
            self.options_button.setText("⚙")
            self.options_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            try:
                f = self.options_button.font()
                pt = f.pointSize()
                if pt <= 0:
                    pt = 12
                f.setPointSize(max(pt, 18))
                self.options_button.setFont(f)
            except Exception:
                pass

        self.options_button.clicked.connect(self._open_options_dialog)
        actions_row.addWidget(self.options_button)

        actions_widget = QtWidgets.QWidget()
        actions_widget.setLayout(actions_row)
        header_layout.addWidget(actions_widget, 0, 2, alignment=QtCore.Qt.AlignRight)
        self._boot_mark("_build_ui: header built")

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

        self.remove_button = QtWidgets.QPushButton("Remove selected")
        self.remove_button.setObjectName("SecondaryButton")
        self.remove_button.setCursor(pointer_cursor)
        self.remove_button.setToolTip("Remove the selected items from the queue")
        self.remove_button.clicked.connect(self._remove_selected_items)
        action_bar.addWidget(self.remove_button)

        self.clear_button = QtWidgets.QPushButton("Clear queue")
        self.clear_button.setObjectName("SecondaryButton")
        self.clear_button.setCursor(pointer_cursor)
        self.clear_button.setFlat(True)  # secondary / ghost-style
        self.clear_button.setToolTip("Remove all items from the queue")
        self.clear_button.clicked.connect(self._clear_queue)
        action_bar.addWidget(self.clear_button)

        action_bar.addStretch()

        card_layout.addLayout(action_bar)

        # Center: stacked empty state vs queue list
        # Helps QSS target the stack so it stays transparent inside the card.
        self.queue_stack = QtWidgets.QStackedWidget()
        self.queue_stack.setObjectName("QueueStack")

        # Empty drag & drop state
        self.queue_placeholder = QtWidgets.QWidget()
        # Helps QSS target the placeholder so it stays transparent inside the card.
        self.queue_placeholder.setObjectName("QueuePlaceholder")
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

        # Actual queue list with expandable header
        self.queue_list = QtWidgets.QTreeWidget()
        self.queue_list.setObjectName("QueueList")
        self.queue_list.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.queue_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        # Row-based selection with no in-cell text editing; avoids the
        # "double highlight" effect when clicking a cell.
        self.queue_list.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.queue_list.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.queue_list.setVerticalScrollMode(
            QtWidgets.QAbstractItemView.ScrollPerPixel
        )
        self.queue_list.setUniformRowHeights(False)
        self.queue_list.setRootIsDecorated(False)
        self.queue_list.setItemsExpandable(False)
        self.queue_list.setIndentation(0)
        self.queue_list.setIconSize(QtCore.QSize(22, 22))
        # Name, Status, Duration, Metadata, ETA, Progress, Output
        self.queue_list.setHeaderLabels([
            "Name",
            "Status",
            "Duration",
            "Metadata",
            "ETA",
            "Progress",
            "Output",
        ])
        # width‑based truncation (Explorer‑style)
        self.queue_list.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.queue_list.setMinimumHeight(160)

        header = self.queue_list.header()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)
        # Column sizing policy:
        #   - Keep Name user-resizable (normal divider after the Name column).
        #   - Let Progress be the elastic column that absorbs remaining space so
        #     the table always looks "filled" with no dead area to the right.
        #   - Output stays compact but wide enough that its header label
        #     ("Output") never elides.
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Interactive)          # Name
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Fixed)                # Status
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Fixed)                # Duration
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Interactive)          # Metadata
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Fixed)                # ETA
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.Stretch)              # Progress
        header.setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.Fixed)                # Output

        # Allow narrower columns (Duration/ETA) so the table can fit without
        # triggering a horizontal scrollbar.
        header.setMinimumSectionSize(52)

        # Make the Name column wide enough that the per-file Progress bar can
        # start near the left edge of its column at the default window size.
        # (With a too-narrow Name column, the Progress column becomes very wide and
        # the fixed-width bar looks like it's floating in the middle.)
        #
        # Roughly ~60 characters is a good balance for 1200px wide layouts.
        fm = self.queue_list.fontMetrics()
        avg_char = max(1, fm.averageCharWidth())
        name_width = max(315, avg_char * 52)
        header.resizeSection(0, name_width)

        # Status column: keep it fixed so it doesn't jump between Queued/
        # Processing…, but make it wide enough that "Processing…" never elides.
        longest_status = "Processing…"
        status_width = (
            fm.horizontalAdvance(longest_status)
            + self.queue_list.iconSize().width()
            + 32
        )
        header.resizeSection(1, max(140, status_width))

        # Duration / ETA: fit "H:MM:SS" comfortably, but keep them tight.
        duration_width = fm.horizontalAdvance("0:00:00") + 24
        eta_width = fm.horizontalAdvance("0:00:00") + 24

        # Metadata: three chips (sr, ch, fps). Estimate a safe default width so
        # common values like "44.1 kHz" / "23.976 FPS" don't get clipped.
        chip_pad = 16  # QSS: padding: 2px 8px (left+right)
        chip_gap = 6   # layout spacing between chips
        # Sample rates/FPS often include decimals (44.1 kHz / 23.976 FPS),
        # which are wider than the integer examples used in the original
        # estimate and can cause the last chip to clip.
        sr_samples = ("44.1 kHz", "48 kHz", "96 kHz", "176.4 kHz", "192 kHz")
        ch_samples = ("2 Ch", "6 Ch", "8 Ch")
        fps_samples = ("23.976 FPS", "29.97 FPS", "59.94 FPS", "119.88 FPS")

        sr_w = max(fm.horizontalAdvance(s) for s in sr_samples)
        ch_w = max(fm.horizontalAdvance(s) for s in ch_samples)
        fps_w = max(fm.horizontalAdvance(s) for s in fps_samples)

        meta_width = (
            sr_w
            + ch_w
            + fps_w
            + (chip_pad * 3)
            + (chip_gap * 2)
            + 24  # slack for borders/rounding/HiDPI
        )

        header.resizeSection(2, max(72, duration_width))
        header.resizeSection(3, max(200, meta_width))
        header.resizeSection(4, max(72, eta_width))

        # Progress: only slightly wider than the footer progress bar.
        header.resizeSection(5, max(230, QUEUE_PROGRESS_WIDTH + 10))

        # Output column: compact but wide enough that "Output" doesn't elide.
        # Keep it narrow so the folder.gif icon remains visually centred.
        header_fm = header.fontMetrics()
        output_label = "Output"
        try:
            header_item = self.queue_list.headerItem()
            if header_item is not None:
                output_label = header_item.text(6) or output_label
        except Exception:
            pass

        # Header sections use padding (see QSS: padding: 4px 8px), so add some slack.
        output_header_w = header_fm.horizontalAdvance(output_label) + 24
        # The per-row Output button uses a 30×30 icon.
        output_icon_w = 30 + 24
        output_width = max(72, output_header_w, output_icon_w)
        header.resizeSection(6, output_width)

        # 🔧 Determine column indices from header labels so they stay correct even if
        #     the column order changes in the future.
        header_item = self.queue_list.headerItem()
        col_map: dict[str, int] = {}
        if header_item is not None:
            for col in range(header_item.columnCount()):
                label = header_item.text(col).strip().lower()
                if label:
                    col_map[label] = col

        # Fall back to the expected positions if, for some reason, the labels are missing.
        self._status_column = col_map.get("status", 1)
        self._meta_column = col_map.get("metadata", 3)
        self._eta_column = col_map.get("eta", 4)
        self._progress_column = col_map.get("progress", 5)
        self._outputs_column = col_map.get("output", 6)

        # Center the Output header label so it lines up with the folder icon
        if header_item is not None:
            header_item.setTextAlignment(
                self._outputs_column,
                QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignVCenter,
            )

        # Enable click-to-sort; default to Name ascending.
        self.queue_list.setSortingEnabled(True)
        self.queue_list.sortByColumn(0, QtCore.Qt.SortOrder.AscendingOrder)

        # 🔧 Remove inner focus border (fixes 'double boxing') without changing
        #     the widget's global QStyle. On some PySide6/Qt builds, wrapping
        #     the view in a QProxyStyle (NoFocusFrameStyle) and then inserting
        #     rows causes a native crash when the item view repaints. The
        #     delegate alone is enough to get rid of the inner focus rectangle.
        self.queue_list.setItemDelegate(QueueItemDelegate(self.queue_list))
        # NOTE: NoFocusFrameStyle is intentionally *not* applied to
        #       self.queue_list to avoid the crash when files are added.

        self.queue_stack.addWidget(self.queue_list)
        self._boot_mark("_build_ui: queue_list built")

        # Let the queue/table area grow and shrink with the window.
        card_layout.addWidget(self.queue_stack, 1)

        # Bottom bar: total duration (left) + Start / Stop (right)
        bottom_bar = QtWidgets.QHBoxLayout()
        self.queue_summary_label = QtWidgets.QLabel("")
        self.queue_summary_label.setObjectName("QueueSummaryLabel")
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

        # Let the queue card absorb any extra vertical space (so the queue isn't
        # stuck at a fixed height and we don't get a huge blank gap).
        layout.addWidget(queue_card, 1)
        # Slightly smaller shadow so cards can sit closer to the footer without
        # getting their shadow clipped (which looks like a sharp edge).
        add_shadow(queue_card, blur_radius=12, offset=(0, 4))

        # Log drawer (hidden by default – toggled from the status bar)
        # NOTE: We add a small gap + bottom spacer so the card shadow isn't
        # clipped by the scroll-area edge (clipping looks like a sharp
        # rectangular box).
        self.log_gap = QtWidgets.QWidget()
        self.log_gap.setObjectName("LogGap")
        # Small separation between the queue card and the log card.
        self.log_gap.setFixedHeight(8)
        self.log_gap.setVisible(False)
        self.log_gap.setAutoFillBackground(False)
        layout.addWidget(self.log_gap)

        self.log_container = QtWidgets.QFrame()
        self.log_container.setObjectName("LogContainer")
        self.log_container.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.log_container.setAutoFillBackground(False)

        # The console should behave like the Queue card: a raised Win11-style
        # card with padding, not a "sunken" text field.
        log_layout = QtWidgets.QVBoxLayout(self.log_container)
        log_layout.setContentsMargins(16, 12, 16, 12)
        log_layout.setSpacing(0)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setFrameShape(QtWidgets.QFrame.NoFrame)
        # Make the console truly blend into the card (prevents a nested text-field box)
        self.log_view.setAutoFillBackground(False)
        try:
            self.log_view.viewport().setAutoFillBackground(False)
        except Exception:
            pass
        # Enforce transparency regardless of the global theme QSS.
        self.log_view.setStyleSheet(
            "QPlainTextEdit { background: transparent; background-color: transparent; border: none; padding: 10px; }"
            "QPlainTextEdit::viewport { background: transparent; }"
        )
        self.log_view.setMinimumHeight(130)
        self.log_view.setMaximumHeight(280)
        self.log_view.setMaximumBlockCount(10000)
        self._init_log_zoom()
        log_layout.addWidget(self.log_view)

        # Add elevation to the *card* instead of the text edit so it doesn't
        # look depressed/inset.
        # Keep the log drawer close to the footer without clipped shadow edges.
        add_shadow(self.log_container, blur_radius=12, offset=(0, 4))
        self._boot_mark("_build_ui: shadows applied")

        # Start hidden; user can reveal via the terminal icon in the status bar
        self.log_container.setVisible(False)
        layout.addWidget(self.log_container)

        # Bottom spacer so the last card's drop shadow (queue or log drawer)
        # never clips against the scroll-area edge (which otherwise creates a
        # sharp rectangular cutoff).
        self.log_shadow_spacer = QtWidgets.QWidget()
        self.log_shadow_spacer.setObjectName("LogShadowSpacer")
        self.log_shadow_spacer.setAutoFillBackground(False)
        # A couple extra pixels of slack avoids any 1px shadow clipping due to
        # rounding/HiDPI scaling.
        self.log_shadow_spacer.setFixedHeight(10)
        # Keep it visible all the time: when the log drawer is closed the queue
        # card becomes the bottom-most widget and also needs this space.
        self.log_shadow_spacer.setVisible(True)
        layout.addWidget(self.log_shadow_spacer)

        scroll = QtWidgets.QScrollArea()
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.setCentralWidget(scroll)

        # --- Status bar: system status + ETA + log toggle --------------------
        status_bar = QtWidgets.QStatusBar(self)
        self.setStatusBar(status_bar)

        status_bar.setSizeGripEnabled(False)

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
        self.progress_bar.setObjectName("FooterProgressBar")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        # comfortable width for "in queue" progress; stays sane if window shrinks
        self.progress_bar.setMinimumWidth(QUEUE_PROGRESS_WIDTH)
        self.progress_bar.setMaximumWidth(320)
        self.progress_bar.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred,
            QtWidgets.QSizePolicy.Fixed,
        )
        self.progress_bar.setVisible(False)
        status_bar.addPermanentWidget(self.progress_bar)

        # “Terminal” toggle for the log drawer
        console_trigger = QtWidgets.QWidget()
        console_trigger.setObjectName("FooterConsoleTrigger")
        console_trigger.setCursor(pointer_cursor)

        # Enable :hover styling on the whole pill (some Qt builds require WA_Hover on QWidget)
        try:
            console_trigger.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)
        except Exception:
            pass
        console_trigger.setMouseTracking(True)

        console_layout = QtWidgets.QHBoxLayout(console_trigger)
        # Give the pill its own padding instead of the icon button doing it.
        # Keep vertical padding tiny so the status bar doesn't get taller when
        # we restore the console icon size.
        console_layout.setContentsMargins(8, 0, 8, 0)
        console_layout.setSpacing(0)

        # Icon button (acts as the actual toggle)
        self.log_toggle_button = QtWidgets.QToolButton(console_trigger)
        self.log_toggle_button.setObjectName("LogToggle")
        self.log_toggle_button.setCheckable(True)
        self.log_toggle_button.setToolTip("Show console")
        self.log_toggle_button.setCursor(pointer_cursor)
        # Icon wired up below via the Command Prompt PNG
        self.log_toggle_button.setAutoRaise(True)
        self.log_toggle_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        # Restore the larger Command_Prompt.png size.
        self.log_toggle_button.setIconSize(QtCore.QSize(30, 30))
        self.log_toggle_button.setText("")
        self.log_toggle_button.toggled.connect(self._toggle_log_panel)

        # 🔧 Ensure the icon button itself never adds its own grey background
        self.log_toggle_button.setStyleSheet(
            """
            QToolButton#LogToggle {
                background-color: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
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

        # Use the static Command_Prompt.png icon for the console toggle
        cmd_icon = _load_asset_icon(
            "Command_Prompt.png",
            QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon,
            self.style(),
        )
        self.log_toggle_button.setIcon(cmd_icon)

        # Optional but helps make it pixel-perfect:
        self.log_toggle_button.setFixedSize(QtCore.QSize(32, 32))
        console_layout.addWidget(self.log_toggle_button, 0, QtCore.Qt.AlignCenter)


        # Make the whole pill clickable
        def _toggle_console_from_mouse(event: QtGui.QMouseEvent) -> None:
            if event.button() == QtCore.Qt.LeftButton:
                self.log_toggle_button.toggle()
            event.accept()

        console_trigger.mousePressEvent = _toggle_console_from_mouse  # type: ignore[assignment]

        status_bar.addPermanentWidget(console_trigger)
        self._boot_mark("_build_ui: status bar built")

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

        # Keep spacing helpers in sync so the UI doesn't jump / clip shadows.
        if hasattr(self, "log_gap"):
            self.log_gap.setVisible(checked)
        # NOTE: log_shadow_spacer stays visible all the time so the bottom-most
        # card's drop shadow never gets clipped.

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
        """Apply theme palette + QSS.

        Dark mode uses Discord-like neutrals (no blue contrast) + Srtforge green.
        """

        # Brand accent ramp (green)
        accent = QtGui.QColor("#16A34A")
        accent_hover = QtGui.QColor("#22C55E")
        accent_pressed = QtGui.QColor("#15803D")

        # Discord-like dark neutrals (core set)
        # Dark mode background (requested): #0C0C0E
        deep_black = "#0C0C0E"
        # Card/containers surface (match Queue + Options dialog)
        surface_bg = "#0C0C0E"
        app_bg_std = "#060608"  # inset fields (slightly darker for depth)
        text_primary = "#e3e3e6"
        text_secondary = "#9b9ca3"
        text_muted = "#85868e"

        # Depth overlays (alpha on top of surfaces)
        border_hairline = "rgba(227,227,230,0.10)"
        border_strong = "rgba(227,227,230,0.14)"
        hover_overlay = "rgba(227,227,230,0.06)"
        pressed_overlay = "rgba(227,227,230,0.10)"
        green_select = "rgba(22,163,74,0.22)"
        green_select_soft = "rgba(22,163,74,0.18)"
        green_select_strong = "rgba(22,163,74,0.26)"
        # ---- Palette --------------------------------------------------------
        palette = self.palette()
        if self._dark_mode:
            app_bg = deep_black
            inset_bg = app_bg_std  # keep a subtle lifted inset even in OLED mode

            palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(app_bg))
            palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(inset_bg))
            palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor(surface_bg))
            palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(text_primary))
            palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(text_primary))
            palette.setColor(QtGui.QPalette.ColorRole.Button, accent)
            # Better contrast on #16A34A with dark text
            palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(text_primary))
            palette.setColor(QtGui.QPalette.ColorRole.Highlight, accent)
            palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor(surface_bg))
            palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor(surface_bg))
            palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor(text_primary))
        else:
            # Light mode stays Win11-ish, but we remove remaining blue accents.
            palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#F8FAFC"))
            palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#FFFFFF"))
            palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor("#F1F5F9"))
            palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#0F172A"))
            palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor("#0F172A"))
            palette.setColor(QtGui.QPalette.ColorRole.Button, accent)
            palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor("#FFFFFF"))
            palette.setColor(QtGui.QPalette.ColorRole.Highlight, accent)
            palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor("#FFFFFF"))
            palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor("#E5E7EB"))
            palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor("#020617"))

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.setPalette(palette)

        self.setPalette(palette)

        # Only use the Win11 .qss file as a base for light mode; in dark mode we fully override.
        base_qss = ""
        if not self._dark_mode:
            base_qss = self._load_win11_stylesheet(accent, accent_hover) or ""

        # QSS expects these names
        lighter = QtGui.QColor(accent_hover)
        darker = QtGui.QColor(accent_pressed)

        if self._dark_mode:
            app_bg = deep_black
            inset_bg = app_bg_std

            # --- Dark mode QSS (Discord-neutral + green accent) ---
            custom = f"""
            #MainWindow {{
                background-color: {app_bg};
            }}

            /* Ensure the scroll area's viewport + page paint the same deep background */
            QScrollArea::viewport {{
                background-color: {app_bg};
            }}
            QWidget#MainPage {{
                background-color: {app_bg};
            }}

            /* Options dialog: match Queue surface */
            QDialog#OptionsDialog {{
                background-color: {surface_bg};
            }}
            QDialog#OptionsDialog QTabWidget::pane {{
                background-color: {surface_bg};
                border: 1px solid {border_hairline};
                border-radius: 12px;
                top: -1px;
            }}
            QDialog#OptionsDialog QWidget#qt_tabwidget_stackedwidget {{
                background-color: {surface_bg};
                border-radius: 12px;
            }}
            QDialog#OptionsDialog QTabBar::tab {{
                background-color: {inset_bg};
                color: {text_secondary};
                border: 1px solid {border_hairline};
                border-bottom: none;
                padding: 6px 14px;
                margin-right: 4px;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }}
            QDialog#OptionsDialog QTabBar::tab:selected {{
                background-color: {surface_bg};
                color: {text_primary};
            }}
            QDialog#OptionsDialog QTabBar::tab:hover:!selected {{
                background-color: {hover_overlay};
            }}

            /* Keep sizing identical to light mode */
            QWidget {{
                font-family: "Segoe UI", "Inter", system-ui;
                font-size: 14px;
            }}

            /* Footer/status bar: crisp separator line and no framed items */
            QStatusBar {{
                background-color: #000000;
                border-top: 1px solid {border_hairline};
            }}
            QStatusBar::item {{
                border: none;
                background: transparent;
            }}
            QStatusBar QLabel {{
                background: transparent;
                border: none;
            }}

            QLabel {{
                color: {text_primary};
            }}
            QLabel#HeaderLabel {{
                color: {text_primary};
                font-size: 22px;
                font-weight: 500;
            }}
            QLabel#EtaLabel {{
                color: {text_secondary};
                padding: 6px 10px;
            }}
            QLabel#QueueSummaryLabel {{
                background-color: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }}

            #QueueCard {{
                background-color: {inset_bg};
                border-radius: 16px;
                border: 1px solid {border_hairline};
            }}

            /* Keep internal queue stack/placeholder transparent so QueueCard shows through */
            #QueueStack, #QueuePlaceholder {{
                background: transparent;
                background-color: transparent;
                border: none;
            }}

            #LogContainer {{
                background-color: {inset_bg};
                border-radius: 16px;
                border: 1px solid {border_hairline};
            }}

            #LogGap, #LogShadowSpacer {{
                background: transparent;
                border: none;
            }}

            QPlainTextEdit, QTextEdit {{
                background-color: {inset_bg};
                color: {text_primary};
                border-radius: 12px;
                border: 1px solid {border_hairline};
                padding: 6px 10px;
                selection-background-color: {accent.name()};
                selection-color: {surface_bg};
            }}

            QPlainTextEdit::viewport, QTextEdit::viewport {{
                background: transparent;
            }}

            /* Checkboxes: neutral greys (avoid blue OS accent in dark mode) */
            QCheckBox {{
                color: {text_primary};
                spacing: 6px;
                padding: 4px 0;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 3px;
                border: 1px solid {border_strong};
                background: {inset_bg};
            }}
            QCheckBox::indicator:hover {{
                border-color: rgba(227,227,230,0.22);
            }}
            QCheckBox::indicator:checked {{
                background: rgba(227,227,230,0.18);
                border-color: rgba(227,227,230,0.22);
            }}
            QCheckBox::indicator:checked:hover {{
                background: rgba(227,227,230,0.22);
            }}
            QCheckBox::indicator:disabled {{
                background: rgba(227,227,230,0.06);
                border-color: rgba(227,227,230,0.10);
            }}

            /* Console/log view (dark mode): text sits on the LogContainer card */
            QPlainTextEdit#LogView {{
                background: transparent;
                background-color: transparent;
                border: none;
                color: {text_primary};
                font-family: "Cascadia Code", Consolas, monospace;
                padding: 10px;
                selection-background-color: {accent.name()};
                selection-color: {surface_bg};
            }}
            QPlainTextEdit#LogView::viewport {{
                background: transparent;
            }}
            QPlainTextEdit#LogView:focus {{
                outline: none;
                border: none;
            }}

            /* Progress bars: queue footer + per-row */
            QProgressBar#FooterProgressBar,
            QProgressBar#QueueProgressBar {{
                border-radius: 999px;
                background-color: {hover_overlay};
                border: 1px solid rgba(227,227,230,0.12);
                height: 10px;
                padding: 0px;
                text-align: center;
            }}
            QProgressBar#FooterProgressBar::chunk,
            QProgressBar#QueueProgressBar::chunk {{
                border-radius: 999px;
                background-color: {accent.name()};
            }}

            QGroupBox {{
                background-color: {inset_bg};
                border-radius: 16px;
                border: none;
                margin-top: 16px;
                padding: 20px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 16px;
                padding: 4px 8px 4px 8px;
                color: {text_primary};
                font-weight: 500;
            }}

            /* Global removal of dotted focus rectangles */
            *:focus {{
                outline: none;
            }}
            QTreeWidget::focus,
            QToolButton::focus,
            QPushButton::focus,
            QLineEdit::focus {{
                outline: none;
                border: none;
            }}

            #EmbedHeader {{
                border-radius: 8px;
                padding: 2px 8px;
            }}
            #EmbedHeader[checked="true"] {{
                background-color: {pressed_overlay};
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
                border: 1px solid {border_strong};
                background: {inset_bg};
                margin-right: 6px;
            }}
            QCheckBox#EmbedCheckbox::indicator:checked {{
                /* Neutral checked state (avoid bright green fill) */
                background: rgba(227,227,230,0.18);
                border-color: rgba(227,227,230,0.22);
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

            #QueueList {{
                background-color: {inset_bg};
                border-radius: 10px;
                border: none;
            }}
            #QueueList QHeaderView::section {{
                background-color: transparent;
                color: #E3E3E6;
                border: none;
                border-right: 1px solid {border_hairline};
                padding: 4px 8px;
                font-weight: 500;
            }}
            #QueueList QHeaderView::section:last {{
                border-right: none;
            }}
            #QueueList::item {{
                padding: 4px 8px;
                border: none;
                outline: none;
                min-height: 38px;  /* tall enough for the progress bar */
            }}
            #QueueList::item:hover:!selected {{
                background-color: {hover_overlay};
            }}
            #QueueList::item:selected,
            #QueueList::item:selected:active,
            #QueueList::item:selected:!active {{
                background-color: {green_select};
                color: {text_primary};
                border: none;
                outline: none;
            }}
            /* Don't draw a second darker box just for keyboard focus */
            #QueueList::item:focus {{
                background-color: transparent;
                border: none;
                outline: none;
            }}

            QPushButton, QToolButton {{
                background-color: {accent.name()};
                color: {text_primary};
                border-radius: 8px;
                padding: 6px 14px;
                border: none;
            }}
            /* Match light mode's (win11.qss) heavier button text */
            QPushButton {{
                font-weight: 600;
            }}
            QPushButton:disabled, QToolButton:disabled {{
                background-color: rgba(227,227,230,0.12);
                color: {text_muted};
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
                color: {text_secondary};
                border-radius: 8px;
                border: 1px solid {border_hairline};
            }}
            QPushButton#SecondaryButton:disabled {{
                color: {text_muted};
                border: 1px solid {border_hairline};
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
                background-color: transparent;
                border: none;
                font-size: 20px;
                color: {text_primary};
            }}
            QToolButton#ThemeToggle:hover,
            QToolButton#OptionsButton:hover {{
                background-color: {hover_overlay};
            }}
            QToolButton#ThemeToggle:pressed,
            QToolButton#OptionsButton:pressed {{
                background-color: {pressed_overlay};
            }}

            /* Console pill in status bar (dark mode) */
            QToolButton#LogToggle {{
                background-color: transparent;
                color: {text_secondary};
                border: none;
                padding: 0;
                margin: 0;
            }}
            QToolButton#LogToggle:hover,
            QToolButton#LogToggle:pressed,
            QToolButton#LogToggle:checked {{
                background-color: transparent;
            }}

            #FooterConsoleTrigger {{
                border-radius: 999px;
                padding: 2px 10px;
                background-color: transparent;
                border: none;
            }}
            #FooterConsoleTrigger:hover {{
                background-color: {hover_overlay};
            }}
            /* Selected/open state (neutral grey, not green) */
            #FooterConsoleTrigger[checked="true"] {{
                background-color: {pressed_overlay};
            }}
            #FooterConsoleTrigger[checked="true"]:hover {{
                background-color: {border_strong};
            }}

            QLabel#LogToggleLabel {{
                color: {text_secondary};
            }}
            #FooterConsoleTrigger:hover QLabel#LogToggleLabel {{
                color: {text_primary};
            }}
            #FooterConsoleTrigger[checked="true"] QLabel#LogToggleLabel {{
                color: {text_primary};
            }}

            QLineEdit, QComboBox {{
                background-color: {inset_bg};
                border-radius: 8px;
                border: 1px solid {border_hairline};
                padding: 4px 8px;
                color: {text_primary};
                selection-background-color: {accent.name()};
                selection-color: {surface_bg};
            }}

            QLabel#DropIcon {{
                font-size: 56px;
                color: rgba(227,227,230,0.38);
            }}
            QLabel#DropHint {{
                color: {text_secondary};
            }}

            QScrollArea {{
                background-color: {app_bg};
                border: none;
            }}

            #GlobalDropOverlay {{
                background: rgba(0, 0, 0, 0.70);
            }}
            """
        else:
            # --- Light mode QSS (keep it clean + green/neutral, no blue) ---
            custom = f"""
            /* Footer/status bar: crisp separator + no framed items */
            QStatusBar {{
                background-color: #FFFFFF;
                border-top: 1px solid #E2E8F0;
            }}
            QStatusBar::item {{
                border: none;
                background: transparent;
            }}
            QStatusBar QLabel {{
                background: transparent;
                border: none;
            }}

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

            /* Kill the last remaining blue-ish border from win11.qss in combo popups */
            QComboBox QAbstractItemView {{
                border-radius: 12px;
                border: 1px solid #E2E8F0;
                selection-background-color: {accent.name()};
            }}

            /* Top-right header icons: ghost buttons (light mode) */
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
                font-size: 20px;
                color: #0F172A;
            }}
            QToolButton#ThemeToggle:hover,
            QToolButton#OptionsButton:hover {{
                background-color: rgba(0,0,0,0.05);
            }}
            QToolButton#ThemeToggle:pressed,
            QToolButton#OptionsButton:pressed {{
                background-color: rgba(0,0,0,0.08);
            }}

            /* Console pill in status bar (light mode) */
            QToolButton#LogToggle {{
                background-color: transparent;
                color: #64748B;
                border: none;
                padding: 0;
                margin: 0;
            }}
            QToolButton#LogToggle:hover,
            QToolButton#LogToggle:pressed,
            QToolButton#LogToggle:checked {{
                background-color: transparent;
            }}

            #FooterConsoleTrigger {{
                border-radius: 999px;
                padding: 2px 10px;
                background-color: transparent;
                border: none;
            }}
            #FooterConsoleTrigger:hover {{
                background-color: rgba(15, 23, 42, 0.05);
            }}
            #FooterConsoleTrigger[checked="true"] {{
                /* Neutral light grey (no green ring) */
                background-color: #E5E7EB;
            }}
            #FooterConsoleTrigger[checked="true"]:hover {{
                background-color: #CBD5E1;
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

            /* Global removal of dotted focus rectangles */
            *:focus {{
                outline: none;
            }}
            QTreeWidget::focus,
            QToolButton::focus,
            QPushButton::focus,
            QLineEdit::focus {{
                outline: none;
                border: none;
            }}

            #EmbedHeader {{
                border-radius: 8px;
                padding: 2px 8px;
            }}
            #EmbedHeader[checked="true"] {{
                background-color: rgba(22, 163, 74, 0.08);
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

            #QueueList QHeaderView::section {{
                background: #F8FAFC;
                color: #475569;
                border: none;
                border-right: 1px solid #E2E8F0;
                padding: 4px 8px;
                font-weight: 500;
            }}
            #QueueList QHeaderView::section:last {{
                border-right: none;
            }}

            #QueueList::item {{
                padding: 4px 8px;
                border: none;
                outline: none;
                min-height: 38px;
            }}

            #QueueList::item:hover:!selected {{
                background: rgba(0,0,0,0.03);
            }}

            #QueueList::item:selected,
            #QueueList::item:selected:active,
            #QueueList::item:selected:!active {{
                background: rgba(22, 163, 74, 0.14);
                color: #111827;
                border: none;
                outline: none;
            }}

            /* Keep focus from adding a second box on top of selection */
            #QueueList::item:focus {{
                background: transparent;
                border: none;
                outline: none;
            }}

            #EtaLabel {{
                color: #64748B;
                padding: 6px 10px;
            }}
            #QueueSummaryLabel {{
                color: #475569;
                background: transparent;
                background-color: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
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

            QLabel#DropIcon {{
                font-size: 56px;
                color: rgba(15, 23, 42, 0.28);
            }}
            QLabel#DropHint {{
                color: #64748B;
            }}

            #LogContainer {{
                background: #FFFFFF;
                border-radius: 16px;
                border: 1px solid #E2E8F0;
            }}

            #LogGap, #LogShadowSpacer {{
                background: transparent;
                border: none;
            }}

            QPlainTextEdit {{
                background-color: #FFFFFF;
                border-radius: 10px;
                border: 1px solid #E5E7EB;
                color: #0F172A;
                selection-background-color: {accent.name()};
                selection-color: #FFFFFF;
            }}

            QPlainTextEdit::viewport {{
                background: transparent;
            }}

            /* Console/log view (light mode): plain text on the LogContainer card */
            QPlainTextEdit#LogView {{
                background: transparent;
                background-color: transparent;
                border: none;
                color: #0F172A;
                font-family: "Cascadia Code", Consolas, monospace;
                padding: 10px;
                selection-background-color: {accent.name()};
                selection-color: #FFFFFF;
            }}
            QPlainTextEdit#LogView::viewport {{
                background: transparent;
            }}
            QPlainTextEdit#LogView:focus {{
                outline: none;
                border: none;
            }}

            /* Progress bars: queue footer + per-row */
            QProgressBar#FooterProgressBar,
            QProgressBar#QueueProgressBar {{
                border-radius: 999px;
                background-color: #E5E7EB;
                border: 1px solid #CBD5E1;
                height: 10px;
                padding: 0px;
                text-align: center;
            }}
            QProgressBar#FooterProgressBar::chunk,
            QProgressBar#QueueProgressBar::chunk {{
                border-radius: 999px;
                background-color: {accent.name()};
            }}
            """

        # ---- Per-row "Open…" button + its menu -------------------------------
        if self._dark_mode:
            menu_bg = surface_bg
            menu_border = border_hairline
            menu_item = text_primary
            menu_item_hover = green_select_soft
            menu_sep = border_hairline
        else:
            menu_bg = "#FFFFFF"
            menu_border = "#E2E8F0"
            menu_item = "#0F172A"
            menu_item_hover = "rgba(22, 163, 74, 0.12)"
            menu_sep = "#E2E8F0"

        custom += f"""
        /* Output column: icon‑only GIF, no pill/rectangle */
        QToolButton#QueueOpenButton,
        QToolButton#QueueOpenButton:hover,
        QToolButton#QueueOpenButton:hover:!disabled,
        QToolButton#QueueOpenButton:pressed,
        QToolButton#QueueOpenButton:checked,
        QToolButton#QueueOpenButton:focus {{
            background: transparent;
            background-color: transparent;
            border: none;
            border-radius: 0px;
            padding: 0px;
            margin: 0px;
            outline: none;
        }}
        QToolButton#QueueOpenButton:disabled {{
            background: transparent;
            background-color: transparent;
            border: none;
            border-radius: 0px;
            padding: 0px;
            margin: 0px;
            outline: none;
        }}
        /* Hide the tiny default menu indicator so we truly only see the GIF */
        QToolButton#QueueOpenButton::menu-indicator {{
            image: none;
            width: 0px;
        }}

        /* View menu styling */
        QMenu#QueueOpenMenu {{
            background-color: {menu_bg};
            border: 1px solid {menu_border};
            border-radius: 10px;
            padding: 4px 0;
            icon-size: 20px;
        }}
        QMenu#QueueOpenMenu::item {{
            padding: 6px 12px;
            color: {menu_item};
            font-size: 13px;
        }}
        QMenu#QueueOpenMenu::icon {{
            padding-left: 6px;
            padding-right: 4px;
        }}
        QMenu#QueueOpenMenu::item:selected {{
            background-color: {menu_item_hover};
        }}
        QMenu#QueueOpenMenu::separator {{
            height: 1px;
            background: {menu_sep};
            margin: 4px 10px;
        }}
        """

        # ---- Metadata column chips -----------------------------------------
        if self._dark_mode:
            chip_text = text_primary
            chip_bg = "rgba(227,227,230,0.08)"
            chip_border = "rgba(227,227,230,0.14)"
        else:
            chip_text = "#0F172A"
            chip_bg = "rgba(0,0,0,0.06)"
            chip_border = "rgba(0,0,0,0.10)"

        custom += f"""
        QWidget#MetaContainer {{
            background: transparent;
        }}
        QLabel#MetaChip {{
            padding: 2px 8px;
            border-radius: 999px;
            font-size: 12px;
            color: {chip_text};
            background-color: {chip_bg};
            border: 1px solid {chip_border};
        }}
        """

        self.setStyleSheet(base_qss + custom)

        # Keep the console-toggle icon readable (avoid the "solid black square" look).
        if hasattr(self, "log_toggle_button"):
            icon_color = QtGui.QColor(text_primary) if self._dark_mode else QtGui.QColor("#475569")
            icon = _load_asset_icon(
                "Command_Prompt.png",
                QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon,
                self.style(),
            )
            try:
                pm = icon.pixmap(64, 64)
                if _pixmap_looks_like_flat_block(pm):
                    icon = _make_terminal_icon(icon_color)
            except Exception:
                icon = _make_terminal_icon(icon_color)
            self.log_toggle_button.setIcon(icon)


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
        # Persist UI state immediately so theme survives crashes/forced closes.
        self._save_persistent_options()



    def _load_win11_stylesheet(
        self,
        accent: QtGui.QColor,
        accent_hover: Optional[QtGui.QColor] = None,
    ) -> Optional[str]:
        """Load the bundled Win11 QSS and substitute our green accent."""
        try:
            data = resources.files("srtforge.assets.styles").joinpath("win11.qss").read_text(encoding="utf-8")
        except Exception:  # pragma: no cover - packaging guard
            return None

        hover = QtGui.QColor(accent_hover or accent)
        if accent_hover is None:
            hover = hover.lighter(115)

        return (
            data.replace("{ACCENT_COLOR}", accent.name())
            .replace("{ACCENT_COLOR_LIGHT}", hover.name())
        )

    # ---- persistent options ------------------------------------------------------
    #
    # Everything the GUI persists (basic options, Advanced/Performance tuning,
    # last-used paths, theme) is stored in a single YAML file with a `.config`
    # extension.
    #
    # The file location is resolved by settings.get_persistent_config_path() and
    # defaults to `PROJECT_ROOT/srtforge.config` (or next to the executable for
    # PyInstaller builds).

    def _read_persistent_config(self) -> dict:
        try:
            path = Path(self._config_path)
            if not path.exists():
                return {}
            with open(path, "r", encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle) or {}
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    def _write_persistent_config(self, payload: dict) -> None:
        try:
            path = Path(self._config_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(path.name + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as handle:
                yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)
            os.replace(tmp_path, path)
        except Exception:
            # Persistence should never crash the app.
            pass

    def _update_persistent_config(self, *, pipeline: Optional[dict] = None, gui: Optional[dict] = None) -> None:
        payload = self._read_persistent_config()
        if not isinstance(payload, dict):
            payload = {}

        if isinstance(pipeline, dict):
            # Pipeline sections (used by the CLI subprocess via SRTFORGE_CONFIG).
            for key, value in pipeline.items():
                payload[key] = value

        if isinstance(gui, dict):
            existing_gui = payload.get("gui")
            if not isinstance(existing_gui, dict):
                existing_gui = {}
            existing_gui.update(gui)
            payload["gui"] = existing_gui

        self._write_persistent_config(payload)

    def _ensure_persistent_config_file(self) -> None:
        """Ensure srtforge.config exists (seeded from config.yaml / legacy QSettings)."""

        path = Path(self._config_path)
        if path.exists():
            self._runtime_config_path = str(path)
            return

        base: dict = {}

        # Seed from shipped config.yaml so CLI defaults remain stable.
        try:
            default_cfg = PACKAGE_ROOT / DEFAULT_CONFIG_FILENAME
            if default_cfg.exists():
                with open(default_cfg, "r", encoding="utf-8") as handle:
                    loaded = yaml.safe_load(handle) or {}
                if isinstance(loaded, dict):
                    base = loaded
        except Exception:
            base = {}

        # One-time migration from legacy QSettings (pre-srtforge.config builds).
        try:
            legacy = getattr(self, "_qsettings", None)
            if legacy is not None:
                gui_patch: dict = {}

                # Basic options
                if legacy.contains("device_prefer_gpu"):
                    gui_patch["prefer_gpu"] = legacy.value("device_prefer_gpu", True, type=bool)
                if legacy.contains("embed_subtitles"):
                    gui_patch["embed_subtitles"] = legacy.value("embed_subtitles", False, type=bool)
                if legacy.contains("burn_subtitles"):
                    gui_patch["burn_subtitles"] = legacy.value("burn_subtitles", False, type=bool)
                if legacy.contains("cleanup_gpu"):
                    gui_patch["cleanup_gpu"] = legacy.value("cleanup_gpu", False, type=bool)
                if legacy.contains("soft_embed_method"):
                    gui_patch["soft_embed_method"] = legacy.value("soft_embed_method", "auto", type=str)
                if legacy.contains("soft_embed_overwrite_source"):
                    gui_patch["soft_embed_overwrite_source"] = legacy.value(
                        "soft_embed_overwrite_source", False, type=bool
                    )
                if legacy.contains("srt_title"):
                    gui_patch["srt_title"] = legacy.value("srt_title", "Srtforge (English)", type=str)
                if legacy.contains("srt_language"):
                    gui_patch["srt_language"] = legacy.value("srt_language", "eng", type=str)
                if legacy.contains("srt_default"):
                    gui_patch["srt_default"] = legacy.value("srt_default", False, type=bool)
                if legacy.contains("srt_forced"):
                    gui_patch["srt_forced"] = legacy.value("srt_forced", False, type=bool)
                if legacy.contains("srt_next_to_media"):
                    gui_patch["srt_next_to_media"] = legacy.value("srt_next_to_media", False, type=bool)

                # Last folder + theme
                last_dir = (legacy.value("last_media_dir", "", type=str) or "").strip()
                if last_dir:
                    gui_patch["last_media_dir"] = last_dir
                if legacy.contains("dark_mode"):
                    gui_patch["dark_mode"] = legacy.value("dark_mode", False, type=bool)

                if gui_patch:
                    existing_gui = base.get("gui")
                    if not isinstance(existing_gui, dict):
                        existing_gui = {}
                    existing_gui.update(gui_patch)
                    base["gui"] = existing_gui

                # Advanced/Performance tuning (stored as pipeline YAML sections)
                if legacy.contains("adv_output_dir") or legacy.contains("perf_vram_limit"):
                    gpu_pref = bool(gui_patch.get("prefer_gpu", True))
                    base["paths"] = {
                        "temp_dir": (legacy.value("adv_temp_dir", "", type=str) or "").strip() or None,
                        "output_dir": (legacy.value("adv_output_dir", "", type=str) or "").strip() or None,
                    }
                    legacy_ff_prefer_center = legacy.value("adv_ff_prefer_center", False, type=bool)
                    base["ffmpeg"] = {
                        "extraction_mode": "dual_mono_center" if legacy_ff_prefer_center else "stereo_mix",
                        "filter_chain": legacy.value("adv_filter_chain", settings.ffmpeg.filter_chain, type=str),
                    }
                    base["separation"] = {
                        "backend": legacy.value("adv_sep_backend", settings.separation.backend, type=str),
                        "sep_hz": int(legacy.value("adv_sep_hz", settings.separation.sep_hz, type=int)),
                        "prefer_center": legacy.value(
                            "adv_sep_prefer_center", settings.separation.prefer_center, type=bool
                        ),
                        "prefer_gpu": gpu_pref,
                        "allow_untagged_english": legacy.value(
                            "adv_allow_untagged", settings.separation.allow_untagged_english, type=bool
                        ),
                    }
                    base["parakeet"] = {
                        "force_float32": legacy.value(
                            "perf_force_f32", settings.parakeet.force_float32, type=bool
                        ),
                        "prefer_gpu": gpu_pref,
                        "rel_pos_local_attn": [
                            int(legacy.value("perf_attn_left", 768, type=int)),
                            int(legacy.value("perf_attn_right", 768, type=int)),
                        ],
                        "subsampling_conv_chunking": legacy.value(
                            "perf_subsampling_chunk", False, type=bool
                        ),
                        "gpu_limit_percent": int(legacy.value("perf_vram_limit", 100, type=int)),
                        "use_low_priority_cuda_stream": legacy.value(
                            "perf_low_pri_stream", False, type=bool
                        ),
                    }
        except Exception:
            # Migration should never crash the app.
            pass

        # Ensure the GUI section always exists with sane defaults.
        gui_section = base.get("gui")
        if not isinstance(gui_section, dict):
            gui_section = {}
        for key, default in self._basic_options.items():
            gui_section.setdefault(key, default)
        gui_section.setdefault("last_media_dir", "")
        gui_section.setdefault("dark_mode", False)
        base["gui"] = gui_section

        self._write_persistent_config(base)
        self._runtime_config_path = str(path) if path.exists() else None

    def _load_persistent_options(self) -> None:
        """Restore user-facing options from srtforge.config."""

        self._ensure_persistent_config_file()

        cfg = self._read_persistent_config()
        gui = cfg.get("gui") if isinstance(cfg.get("gui"), dict) else {}

        self._basic_options["prefer_gpu"] = bool(gui.get("prefer_gpu", self._basic_options["prefer_gpu"]))
        self._basic_options["embed_subtitles"] = bool(gui.get("embed_subtitles", self._basic_options["embed_subtitles"]))
        self._basic_options["burn_subtitles"] = bool(gui.get("burn_subtitles", self._basic_options["burn_subtitles"]))
        self._basic_options["cleanup_gpu"] = bool(gui.get("cleanup_gpu", self._basic_options["cleanup_gpu"]))
        self._basic_options["soft_embed_method"] = str(gui.get("soft_embed_method", self._basic_options["soft_embed_method"]))
        self._basic_options["soft_embed_overwrite_source"] = bool(
            gui.get("soft_embed_overwrite_source", self._basic_options["soft_embed_overwrite_source"])
        )
        self._basic_options["srt_title"] = str(gui.get("srt_title", self._basic_options["srt_title"]))
        self._basic_options["srt_language"] = str(gui.get("srt_language", self._basic_options["srt_language"]))
        self._basic_options["srt_default"] = bool(gui.get("srt_default", self._basic_options["srt_default"]))
        self._basic_options["srt_forced"] = bool(gui.get("srt_forced", self._basic_options["srt_forced"]))
        self._basic_options["srt_next_to_media"] = bool(gui.get("srt_next_to_media", self._basic_options["srt_next_to_media"]))

        # "Add files…" dialog: remember the last folder across runs.
        last_dir = (str(gui.get("last_media_dir", "")) or "").strip()
        if last_dir:
            try:
                if Path(last_dir).expanduser().exists():
                    self._last_media_dir = last_dir
                else:
                    self._last_media_dir = ""
            except Exception:
                self._last_media_dir = ""
        else:
            self._last_media_dir = ""

        # Theme
        self._dark_mode = bool(gui.get("dark_mode", False))
        if hasattr(self, "theme_toggle"):
            block = self.theme_toggle.blockSignals(True)
            try:
                self.theme_toggle.setChecked(self._dark_mode)
            finally:
                self.theme_toggle.blockSignals(block)
        self._update_theme_toggle_label()

        # Ensure CLI runs inherit the same persistent config.
        try:
            cfg_path = Path(self._config_path)
            self._runtime_config_path = str(cfg_path) if cfg_path.exists() else None
        except Exception:
            self._runtime_config_path = None

    def _save_persistent_options(self) -> None:
        """Persist current GUI options to srtforge.config."""

        gui_payload = dict(self._basic_options)
        gui_payload["last_media_dir"] = str(getattr(self, "_last_media_dir", "") or "")
        gui_payload["dark_mode"] = bool(getattr(self, "_dark_mode", False))
        self._update_persistent_config(gui=gui_payload)

    def _remember_last_media_dir(self, paths: list[Path]) -> None:
        """Persist the directory of the most recently added media file."""

        if not paths:
            return

        try:
            last = paths[-1]

            # If the user dropped a directory, remember it directly.
            # Otherwise remember the parent folder of the file.
            try:
                if last.exists() and last.is_dir():
                    directory = last
                else:
                    directory = last.parent
            except Exception:
                directory = last.parent

            if not directory:
                return

            self._last_media_dir = str(directory)
            # Sync immediately so it survives crashes/forced closes.
            self._save_persistent_options()
        except Exception:
            # Persistence should never crash the app.
            pass

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
        start_dir = getattr(self, "_last_media_dir", "") or ""
        try:
            # If the persisted folder no longer exists (e.g. unplugged drive),
            # fall back to Qt's default behaviour.
            if start_dir and not Path(start_dir).expanduser().exists():
                start_dir = ""
        except Exception:
            start_dir = ""

        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select media files",
            start_dir,
        )
        if files:
            self._add_files_to_queue(files)

    def _add_files_to_queue(self, files: Iterable[str]) -> None:
        normalized = _normalize_paths(files)
        if not normalized:
            return

        # Update the persisted "Add files" folder even if all selected files
        # were already in the queue.
        self._remember_last_media_dir(normalized)

        existing = {
            Path(
                self.queue_list.topLevelItem(i).data(
                    0, QtCore.Qt.ItemDataRole.UserRole
                )
            )
            for i in range(self.queue_list.topLevelItemCount())
        }
        ffprobe = self.ffmpeg_paths.ffprobe if self.ffmpeg_paths else None
        pointer_cursor = QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        for path in normalized:
            if path in existing:
                continue
            item = QueueTreeWidgetItem()
            # Full filename; truncation handled by view + header width
            item.setText(0, path.name)
            # Duration (filled below once we know it)
            # Metadata + ETA will be filled during probing/processing
            # store the full path on column 0
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, str(path))
            # tooltip showing the full path
            item.setToolTip(0, str(path))
            # Do not allow in-place editing; clicking anywhere should just
            # select the whole row.
            flags = item.flags()
            flags &= ~QtCore.Qt.ItemFlag.ItemIsEditable
            item.setFlags(flags)

            self.queue_list.addTopLevelItem(item)
            existing.add(path)

            # Initial queue state (with icon + tooltip)
            self._apply_status_icon_and_tooltip(item, "Queued")

            # Show placeholders until media streams/ETA are known.
            item.setText(self._meta_column, "…")
            item.setToolTip(self._meta_column, "Probing media info…")

            item.setText(self._eta_column, ETA_PLACEHOLDER)
            item.setData(self._eta_column, QtCore.Qt.ItemDataRole.UserRole, 0.0)
            item.setToolTip(self._eta_column, "")

            # Per-file progress bar; only attached while processing.
            progress = QtWidgets.QProgressBar()
            progress.setObjectName("QueueProgressBar")
            progress.setRange(0, 100)
            progress.setValue(0)
            progress.setTextVisible(False)
            # We want a pill that has a fixed size like the footer bar,
            # not something that stretches to fill the entire cell.
            progress.setSizePolicy(
                QtWidgets.QSizePolicy.Fixed,
                QtWidgets.QSizePolicy.Fixed,
            )

            # Width will be matched to the footer bar the first time we show it
            # in _set_queue_item_progress.
            progress.setVisible(False)

            key = str(path)
            self._item_progress[key] = progress

            # Per-row “View” button for outputs/logs
            open_button = QtWidgets.QToolButton()
            open_button.setObjectName("QueueOpenButton")
            open_button.setCursor(pointer_cursor)
            open_button.setAutoRaise(False)
            open_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            open_button.setEnabled(False)

            # Used by eventFilter() to trigger a single GIF play per hover.
            open_button.setProperty("srtforge_media_key", key)
            open_button.installEventFilter(self)

            # Ensure hover events fire instantly inside the QTreeWidget cell.
            open_button.setMouseTracking(True)
            open_button.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)

            # Icon‑only button: just the folder GIF, no pill/background or text
            open_button.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)

            folder_icon = _load_asset_icon(
                "folder.gif",
                QtWidgets.QStyle.StandardPixmap.SP_DirOpenIcon,
                self.style(),
            )
            open_button.setIcon(folder_icon)
            # Slightly larger than the row text
            open_button.setIconSize(QtCore.QSize(30, 30))
            open_button.setToolTip("View outputs for this file (SRT, diagnostics, log)")

            # Preload the GIF decoder so hover playback feels instant.
            try:
                movie = _load_asset_movie("folder.gif")
            except Exception:
                movie = None
            if movie is not None:
                movie.setParent(open_button)
                movie.setCacheMode(QtGui.QMovie.CacheMode.CacheAll)
                set_loop = getattr(movie, "setLoopCount", None)
                if callable(set_loop):
                    set_loop(1)
                try:
                    movie.jumpToFrame(0)
                except Exception:
                    pass
                self._folder_movies[key] = movie

            menu = QtWidgets.QMenu(open_button)
            menu.setObjectName("QueueOpenMenu")  # for styling
            open_button.setMenu(menu)
            open_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)

            # Track the button + empty artifact dict
            self._open_buttons[key] = open_button
            self._item_outputs.setdefault(key, {})

            # Populate menu lazily and flip the arrow while it is open
            def _rebuild_menu(k: str = key, m: QtWidgets.QMenu = menu) -> None:
                self._populate_outputs_menu(k, m)

            def _reset_arrow() -> None:
                # No-op; we no longer show arrow text on the button
                pass

            menu.aboutToShow.connect(_rebuild_menu)
            menu.aboutToHide.connect(_reset_arrow)

            # Attach the button to the Output column
            if hasattr(self, "_outputs_column"):
                self.queue_list.setItemWidget(item, self._outputs_column, open_button)

            # Populate Duration asynchronously so adding many files doesn't freeze the UI.
            self._queue_duration_cache[str(path)] = 0.0
            item.setText(2, "…")
            item.setData(2, QtCore.Qt.ItemDataRole.UserRole, 0.0)

            try:
                QtCore.QThreadPool.globalInstance().start(
                    _DurationProbeTask(path, ffprobe, self._duration_probe)
                )
            except Exception:
                # Best-effort; duration is nice-to-have.
                pass

            try:
                QtCore.QThreadPool.globalInstance().start(
                    _StreamProbeTask(path, ffprobe, self._stream_probe)
                )
            except Exception:
                # Best-effort; stream info is nice-to-have.
                pass

        self._update_start_state()

    def _remove_selected_items(self) -> None:
        items = self.queue_list.selectedItems()
        if not items:
            return

        rows_and_keys: list[tuple[int, Optional[str]]] = []

        for item in items:
            row = self.queue_list.indexOfTopLevelItem(item)
            path = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            key = str(path) if path else None
            rows_and_keys.append((row, key))

        # Remove rows from bottom to top so indices don't shift under us.
        rows_and_keys.sort(key=lambda rk: rk[0], reverse=True)

        for row, key in rows_and_keys:
            if row >= 0:
                self.queue_list.takeTopLevelItem(row)
            if key:
                # Clean all per-file caches + widgets
                self._queue_duration_cache.pop(key, None)

                progress = self._item_progress.pop(key, None)
                if progress is not None:
                    progress.deleteLater()

                button = self._open_buttons.pop(key, None)
                if button is not None:
                    button.deleteLater()

                self._item_outputs.pop(key, None)
                self._file_run_ids.pop(key, None)

                movie = self._folder_movies.pop(key, None)
                if movie is not None:
                    movie.stop()
                    movie.deleteLater()

        self._update_start_state()

    def _clear_queue(self) -> None:
        # Delete per-row widgets explicitly to avoid leaking QWidget instances.
        for bar in list(self._item_progress.values()):
            try:
                bar.deleteLater()
            except Exception:
                pass
        for btn in list(self._open_buttons.values()):
            try:
                btn.deleteLater()
            except Exception:
                pass

        self.queue_list.clear()
        self._queue_duration_cache.clear()
        self._item_progress.clear()
        # Clear outputs + buttons + run id mapping
        self._open_buttons.clear()
        self._item_outputs.clear()
        self._file_run_ids.clear()
        for movie in self._folder_movies.values():
            try:
                movie.stop()
                movie.deleteLater()
            except Exception:
                pass
        self._folder_movies.clear()
        self._folder_hover_gen.clear()
        self._update_start_state()

    def _update_start_state(self) -> None:
        has_items = self.queue_list.topLevelItemCount() > 0
        self.start_button.setEnabled(has_items and not self._worker)

        # Switch between empty placeholder and actual list
        if hasattr(self, "queue_stack"):
            self.queue_stack.setCurrentWidget(
                self.queue_list if has_items else self.queue_placeholder
            )

        if not has_items:
            self._reset_queue_progress_bar()

        # When nothing is running, keep all row progress bars hidden
        if not self._worker:
            self._hide_all_row_progress_bars()

        self._update_queue_summary()

    def _update_queue_summary(self) -> None:
        if not hasattr(self, "queue_summary_label"):
            return

        count = self.queue_list.topLevelItemCount()
        if count == 0:
            # Empty state: let the central watermark do the talking.
            self.queue_summary_label.setText("")
            return

        total_s = 0.0
        for i in range(count):
            item = self.queue_list.topLevelItem(i)
            path = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if not path:
                continue
            total_s += float(self._queue_duration_cache.get(str(path), 0.0))

        if total_s <= 0:
            summary = f"{count} file{'s' if count != 1 else ''} in queue"
        else:
            dur_str = _format_hms(total_s)
            summary = (
                f"{count} file{'s' if count != 1 else ''} – Total duration: {dur_str}"
            )

        self.queue_summary_label.setText(summary)

    @QtCore.Slot(str, float)
    def _on_duration_probed(self, media: str, duration_s: float) -> None:
        """Update the queued row once ffprobe duration is available."""

        try:
            key = str(Path(media).resolve())
        except Exception:
            key = str(media)

        # If the item has been removed from the queue, ignore the update.
        item = self._find_queue_item(key)
        if item is None:
            return

        duration_s = float(duration_s or 0.0)
        self._queue_duration_cache[key] = duration_s
        item.setText(2, _format_hms(duration_s))
        item.setData(2, QtCore.Qt.ItemDataRole.UserRole, duration_s)
        self._update_queue_summary()

    def _metadata_parts(self, audio_text: str, video_text: str) -> list[tuple[str, str]]:
        """Split probe results into small 'badge' parts for the Metadata column."""
        parts: list[tuple[str, str]] = []

        a = (audio_text or "").strip()
        if a and a not in {ETA_PLACEHOLDER, "…"}:
            # Normalise common probe formats:
            #   - "48 kHz 2ch"  -> "48 kHz", "2 Ch"
            #   - "44.1kHz 6ch" -> "44.1 kHz", "6 Ch"
            #   - tolerate case variants like "KHz"/"khz" and "Ch"/"CH".
            try:
                import re

                sr_m = re.search(r"(?i)\b(\d+(?:\.\d+)?)\s*k\s*hz\b", a)
                if sr_m:
                    sr_val = sr_m.group(1).rstrip("0").rstrip(".")
                    parts.append(("sr", f"{sr_val} kHz"))

                ch_m = re.search(r"(?i)\b(\d+)\s*ch\b", a)
                if ch_m:
                    parts.append(("ch", f"{int(ch_m.group(1))} Ch"))
            except Exception:
                # Fallback: keep the previous behaviour, but with friendlier casing.
                tokens = a.split()
                khz_idx = None
                for i, tok in enumerate(tokens):
                    if tok.strip().lower() == "khz":
                        khz_idx = i
                        break
                if khz_idx is not None:
                    khz = " ".join(tokens[: khz_idx + 1]).strip()
                    if khz:
                        parts.append(("sr", khz.replace("khz", "kHz").replace("KHz", "kHz")))
                    tokens = tokens[khz_idx + 1 :]

                for tok in tokens:
                    t = tok.strip()
                    if not t:
                        continue
                    # "2ch" -> "2 Ch"
                    if t.lower().endswith("ch") and t[:-2].isdigit():
                        parts.append(("ch", f"{int(t[:-2])} Ch"))
                    else:
                        parts.append(("ch", t))

        v = (video_text or "").strip()
        if v and v not in {ETA_PLACEHOLDER, "…"}:
            # Normalise fps casing so it matches the other chips.
            try:
                import re

                fps_m = re.search(r"(?i)\b(\d+(?:\.\d+)?)\s*fps\b", v)
                if fps_m:
                    fps_val = fps_m.group(1).rstrip("0").rstrip(".")
                    parts.append(("fps", f"{fps_val} FPS"))
                else:
                    parts.append(("fps", v))
            except Exception:
                parts.append(("fps", v))

        return parts

    def _build_metadata_widget(self, parts: list[tuple[str, str]]) -> QtWidgets.QWidget:
        """Create a compact chip-row widget for the Metadata column."""
        container = QtWidgets.QWidget()
        container.setObjectName("MetaContainer")
        container.setAutoFillBackground(False)
        try:
            container.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_TranslucentBackground,
                True,
            )
        except Exception:
            pass

        # Decorative only: allow clicks to select the whole row underneath.
        try:
            container.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                True,
            )
        except Exception:
            pass

        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        for kind, text in parts:
            chip = QtWidgets.QLabel(text)
            chip.setObjectName("MetaChip")
            chip.setProperty("kind", kind)  # enables QSS selectors if you want
            chip.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            chip.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Fixed,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
            try:
                chip.setAttribute(
                    QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                    True,
                )
            except Exception:
                pass

            layout.addWidget(chip, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)

        layout.addStretch(1)
        return container

    def _on_stream_probed(self, media: str, audio_text: str, video_text: str) -> None:
        if not hasattr(self, "queue_list"):
            return

        item = self._find_queue_item(media)
        if item is None:
            return

        meta_col = getattr(self, "_meta_column", 3)

        audio_text = (audio_text or "").strip() or ETA_PLACEHOLDER
        video_text = (video_text or "").strip() or ETA_PLACEHOLDER

        parts = self._metadata_parts(audio_text, video_text)
        if not parts:
            item.setText(meta_col, ETA_PLACEHOLDER)
            item.setToolTip(meta_col, ETA_PLACEHOLDER)
            try:
                self.queue_list.removeItemWidget(item, meta_col)
            except Exception:
                pass
            return

        meta_text = " • ".join([p[1] for p in parts])
        item.setText(meta_col, meta_text)  # keeps sorting working
        item.setToolTip(meta_col, meta_text)

        self.queue_list.setItemWidget(item, meta_col, self._build_metadata_widget(parts))

    def _status_icon_and_tooltip(
        self,
        status: str,
    ) -> tuple[Optional[QtGui.QIcon], str]:
        """
        Map a logical status string to an icon + accessible tooltip.

        The raw status text ("Queued", "Processing…", "Completed", "Failed")
        stays unchanged so existing logic that compares the text still works.
        """
        try:
            style = self.queue_list.style()
        except Exception:
            style = self.style()

        icon: Optional[QtGui.QIcon] = None
        tooltip = ""

        s = status.lower()

        if s.startswith("queued"):
            tooltip = "Queued – waiting for its turn in the queue."
        elif s.startswith("processing"):
            icon = style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload)
            tooltip = "Processing – subtitles are being generated."
        elif s.startswith("completed"):
            icon = style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogApplyButton)
            tooltip = "Completed – subtitles were generated successfully."
        elif s.startswith("failed"):
            icon = style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning)
            tooltip = (
                "Failed – open the console (>_ Console) or choose 'Run log' from the "
                "Output menu to see details and possible fixes."
            )

        return icon, tooltip

    def _apply_status_icon_and_tooltip(
        self,
        item: QtWidgets.QTreeWidgetItem,
        status: str,
    ) -> None:
        """Update the Status cell text, icon and tooltip for a given row."""
        col = getattr(self, "_status_column", 1)
        item.setText(col, status)

        icon, tooltip = self._status_icon_and_tooltip(status)
        if icon is not None:
            item.setIcon(col, icon)
        else:
            # Clear any previous icon
            item.setIcon(col, QtGui.QIcon())

        item.setToolTip(col, tooltip or "")

    def _find_queue_item(self, media: str) -> Optional[QtWidgets.QTreeWidgetItem]:
        if not hasattr(self, "queue_list"):
            return None

        path = Path(media)
        for i in range(self.queue_list.topLevelItemCount()):
            item = self.queue_list.topLevelItem(i)
            raw = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if raw and Path(str(raw)) == path:
                return item
        return None

    def _set_queue_item_status(self, media: str, status: str) -> None:
        path = Path(media)
        target_item: Optional[QtWidgets.QTreeWidgetItem] = None

        # First, locate the target item and clear any existing row bars
        for i in range(self.queue_list.topLevelItemCount()):
            item = self.queue_list.topLevelItem(i)
            raw = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if not raw:
                continue

            # Update the logical status + icon/tooltip for this row
            if Path(str(raw)) == path:
                target_item = item
                self._apply_status_icon_and_tooltip(item, status)

            # Hide any progress widget currently attached to this row
            widget = self.queue_list.itemWidget(item, self._progress_column)
            if widget is not None:
                widget.setVisible(False)

        if target_item is None:
            return

        # Progress bar centring / visibility is handled by
        # ``_set_queue_item_progress``; this helper only updates text + icon.

    def _set_queue_item_progress(self, media: str, percent: int) -> None:
        """Update the per-file progress bar in the queue list, if present."""
        key = str(media)
        bar = self._item_progress.get(key)

        # Lazily create the bar the first time we see this file.
        if bar is None:
            bar = QtWidgets.QProgressBar()
            bar.setObjectName("QueueProgressBar")
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(False)
            bar.setSizePolicy(
                QtWidgets.QSizePolicy.Fixed,
                QtWidgets.QSizePolicy.Fixed,
            )
            self._item_progress[key] = bar

        # Match size to the footer progress bar so they look identical.
        if hasattr(self, "progress_bar") and self.progress_bar is not None:
            footer = self.progress_bar

            # NOTE: The footer bar lives in a QStatusBar, so its *rendered* size can
            # differ from sizeHint(). Use the live geometry first so the row bar
            # matches pixel-perfectly.
            footer_h = int(footer.height() or footer.sizeHint().height() or 0)
            footer_w = int(footer.width() or footer.minimumWidth() or footer.sizeHint().width() or 0)

            if footer_h > 0 and bar.height() != footer_h:
                bar.setFixedHeight(footer_h)

            # Keep the row progress bar in sync with the footer bar, but cap it
            # to the actual Progress column width so it never clips (or forces
            # a horizontal scrollbar) when the default window is relatively
            # narrow.
            target_w = footer_w
            try:
                col_w = int(self.queue_list.columnWidth(self._progress_column))
            except Exception:
                col_w = 0
            if col_w > 0:
                # Leave a bit of breathing room so stylesheet padding/borders
                # don't cause sub-pixel clipping.
                target_w = min(target_w, max(60, col_w - 16))

            if target_w > 0 and bar.width() != target_w:
                bar.setFixedWidth(target_w)

        current_item = self._find_queue_item(media)
        if current_item is None:
            return

        # Attach the bar to a tiny wrapper widget so it can be aligned cleanly
        # inside the cell (and keep the wrapper transparent).
        existing_container = self.queue_list.itemWidget(current_item, self._progress_column)
        container = bar.parent() if isinstance(bar.parent(), QtWidgets.QWidget) else None

        if container is None or container is not existing_container:
            container = QtWidgets.QWidget()
            container.setObjectName("QueueProgressContainer")

            # Critical: make the wrapper *transparent* so there is no white box.
            container.setAutoFillBackground(False)
            container.setStyleSheet("QWidget#QueueProgressContainer { background: transparent; }")
            try:
                container.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
            except Exception:
                pass

            layout = QtWidgets.QHBoxLayout(container)
            # Match the left padding used by the tree rows so the bar starts at the
            # Progress column's content edge (not centred in the cell).
            layout.setContentsMargins(8, 0, 0, 0)
            layout.setSpacing(0)
            layout.addWidget(bar, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            layout.addStretch(1)
            self.queue_list.setItemWidget(current_item, self._progress_column, container)

        value = max(0, min(100, int(percent)))
        container.setVisible(True)
        bar.setVisible(True)
        bar.setValue(value)

    def _set_queue_item_eta(self, path: str, remaining_s: float) -> None:
        if not hasattr(self, "queue_list"):
            return

        key = str(path)
        remaining = 0.0
        try:
            remaining = max(0.0, float(remaining_s or 0.0))
        except Exception:
            remaining = 0.0

        # Default: placeholder (no ETA / unknown)
        text = ETA_PLACEHOLDER
        seconds_val = 0.0
        if remaining > 1:
            # Round to the nearest second and format as MM:SS / H:MM:SS
            text = _format_hms(remaining)
            seconds_val = remaining

        for i in range(self.queue_list.topLevelItemCount()):
            item = self.queue_list.topLevelItem(i)
            raw = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if raw and str(raw) == key:
                item.setText(self._eta_column, text)
                item.setData(self._eta_column, QtCore.Qt.ItemDataRole.UserRole, float(seconds_val))
                if text == ETA_PLACEHOLDER:
                    item.setToolTip(self._eta_column, "")
                return

    def _clear_all_eta_cells(self) -> None:
        """Reset ETA column for all rows back to the placeholder."""
        if not hasattr(self, "queue_list"):
            return
        for i in range(self.queue_list.topLevelItemCount()):
            item = self.queue_list.topLevelItem(i)
            item.setText(self._eta_column, ETA_PLACEHOLDER)
            item.setData(self._eta_column, QtCore.Qt.ItemDataRole.UserRole, 0.0)
            item.setToolTip(self._eta_column, "")

    def _update_queue_progress_bar(self, current_file_fraction: float) -> None:
        """Update the footer progress bar to reflect whole-queue progress."""
        if not hasattr(self, "progress_bar"):
            return

        total = self._queue_total_count or self.queue_list.topLevelItemCount()
        if total <= 0:
            self.progress_bar.setVisible(False)
            self.progress_bar.setValue(0)
            return

        done = max(0, min(total, self._queue_completed_count))
        frac_current = max(0.0, min(1.0, float(current_file_fraction)))
        overall = (done + frac_current) / float(total)
        overall = max(0.0, min(1.0, overall))

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(int(round(overall * 100)))

    def _reset_queue_progress_bar(self) -> None:
        self._queue_total_count = 0
        self._queue_completed_count = 0
        if hasattr(self, "progress_bar"):
            self.progress_bar.setVisible(False)
            self.progress_bar.setValue(0)

    def _hide_all_row_progress_bars(self) -> None:
        """Hide every per-file progress bar in the queue list."""
        if not hasattr(self, "queue_list"):
            return
        for i in range(self.queue_list.topLevelItemCount()):
            item = self.queue_list.topLevelItem(i)
            widget = self.queue_list.itemWidget(item, self._progress_column)
            # ``itemWidget`` now returns a small wrapper widget that contains
            # the actual QProgressBar, so we just hide whatever is there.
            if widget is not None:
                widget.setVisible(False)

    def _populate_outputs_menu(self, key: str, menu: QtWidgets.QMenu) -> None:
        """
        Rebuild the per-row 'Open…' menu for the given media key.

        Called on QMenu.aboutToShow so we can discover diagnostics/logs lazily.
        """
        menu.clear()

        artifacts = self._item_outputs.get(key) or {}

        # Convert stored strings to Path objects where applicable
        srt_path = Path(artifacts["srt"]) if artifacts.get("srt") else None
        diag_csv = Path(artifacts["diag_csv"]) if artifacts.get("diag_csv") else None
        diag_json = Path(artifacts["diag_json"]) if artifacts.get("diag_json") else None
        log_path = Path(artifacts["log"]) if artifacts.get("log") else None

        # Lazily discover diagnostics sidecars next to the SRT if we didn't record them yet
        if srt_path and srt_path.exists():
            if diag_csv is None:
                candidate = srt_path.with_suffix(srt_path.suffix + ".diag.csv")
                if candidate.exists():
                    diag_csv = candidate
                    artifacts["diag_csv"] = str(candidate)
            if diag_json is None:
                candidate = srt_path.with_suffix(srt_path.suffix + ".diag.json")
                if candidate.exists():
                    diag_json = candidate
                    artifacts["diag_json"] = str(candidate)

        # Lazily derive log path from run_id, if necessary
        run_id = artifacts.get("run_id") or self._file_run_ids.get(key)
        if run_id and (log_path is None or not log_path.exists()):
            candidate = LOGS_DIR / f"{run_id}.log"
            if candidate.exists():
                log_path = candidate
                artifacts["log"] = str(candidate)

        # Persist any new discoveries
        self._item_outputs[key] = artifacts

        has_actions = False

        # --- Icons: custom PNGs for SRT / CSV / JSON / log / folder -------------

        style = self.style()
        srt_icon = _load_asset_icon("srt.png", QtWidgets.QStyle.StandardPixmap.SP_FileIcon, style)
        csv_icon = _load_asset_icon("csv.png", QtWidgets.QStyle.StandardPixmap.SP_FileIcon, style)
        json_icon = _load_asset_icon("json.png", QtWidgets.QStyle.StandardPixmap.SP_FileIcon, style)
        log_icon = _load_asset_icon(
            "log.png",
            QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView,
            style,
        )
        folder_icon = _load_asset_icon(
            "folder.png",
            QtWidgets.QStyle.StandardPixmap.SP_DirOpenIcon,
            style,
        )

        # -----------------------------------------------------------------------

        def _add_action(
            label: str,
            icon: QtGui.QIcon,
            path: Optional[Path],
            *,
            open_folder: bool = False,
        ) -> None:
            nonlocal has_actions
            if not path or not path.exists():
                return
            has_actions = True
            action = menu.addAction(icon, label)

            def _open() -> None:
                target = path
                if open_folder:
                    target = path if path.is_dir() else path.parent
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(target)))

            action.triggered.connect(_open)

        # Primary entry: open the SRT in the default app
        _add_action("SRT file", srt_icon, srt_path)
        # File‑type “photos” for diagnostics + log
        _add_action("Diagnostics CSV", csv_icon, diag_csv)
        _add_action("Diagnostics JSON", json_icon, diag_json)
        _add_action("Run log (details)", log_icon, log_path)

        # Convenience: open the SRT folder in Explorer/Finder
        if srt_path and srt_path.exists():
            _add_action("Containing folder", folder_icon, srt_path, open_folder=True)

        if not has_actions:
            placeholder = menu.addAction("No outputs available yet")
            placeholder.setEnabled(False)

    def _outputs_ready_for_hover_animation(self, key: str) -> bool:
        """Return True when the Output button has *anything* meaningful to open.

        We treat a run log as an output too (useful even for failed/cancelled runs),
        so the hover animation should work as soon as the button is enabled.
        """
        artifacts = self._item_outputs.get(key) or {}

        # If the button is enabled, we already consider it "ready" for hover animation.
        btn = self._open_buttons.get(key)
        if btn is not None and btn.isEnabled():
            return True

        # Otherwise, fall back to checking for any on-disk artifact.
        for field in ("srt", "log", "diag_csv", "diag_json"):
            raw = artifacts.get(field)
            if not raw:
                continue
            try:
                if Path(str(raw)).exists():
                    return True
            except Exception:
                continue
        return False


    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # noqa: N802
        """Play the folder GIF once per hover (only after outputs exist) and stop on unhover."""
        try:
            if (
                isinstance(watched, QtWidgets.QToolButton)
                and watched.objectName() == "QueueOpenButton"
            ):
                key = watched.property("srtforge_media_key")
                if isinstance(key, str) and key:
                    if event.type() == QtCore.QEvent.Type.Enter:
                        if self._outputs_ready_for_hover_animation(key):
                            self._play_folder_gif_once(key)
                    elif event.type() == QtCore.QEvent.Type.Leave:
                        self._stop_folder_gif_animation(key)

        except Exception:
            # Never let hover animation logic break the rest of the UI.
            pass
        return super().eventFilter(watched, event)

    def _play_folder_gif_once(self, key: str) -> None:
        """Play the folder.gif animation a single time for this row's Output button.

        This is used for the "play once per hover" behaviour (mouseenter triggers
        a single pass, mouseleave cancels/reset).
        """
        button = self._open_buttons.get(key)
        if not button:
            return

        # Bump the hover generation so any stale signals from an older run
        # can't restore the static icon mid-animation.
        gen = int(self._folder_hover_gen.get(key, 0)) + 1
        self._folder_hover_gen[key] = gen

        movie = self._folder_movies.get(key)
        if movie is None:
            movie = _load_asset_movie("folder.gif")
            if movie is None:
                return
            movie.setParent(button)
            movie.setCacheMode(QtGui.QMovie.CacheMode.CacheAll)
            self._folder_movies[key] = movie

        # Ensure we only connect signals once per movie instance.
        if not bool(movie.property("srtforge_connected")):
            def _on_frame_changed(_frame: int, *, k: str = key, m: QtGui.QMovie = movie, b: QtWidgets.QToolButton = button) -> None:
                if int(self._folder_hover_gen.get(k, 0)) != int(m.property("srtforge_gen") or 0):
                    return
                pix = m.currentPixmap()
                if not pix.isNull():
                    b.setIcon(QtGui.QIcon(pix))

            def _on_finished(*, k: str = key, m: QtGui.QMovie = movie) -> None:
                if int(self._folder_hover_gen.get(k, 0)) != int(m.property("srtforge_gen") or 0):
                    return
                self._restore_folder_static_icon(k)

            movie.frameChanged.connect(_on_frame_changed)
            movie.finished.connect(_on_finished)
            movie.setProperty("srtforge_connected", True)

        movie.setProperty("srtforge_gen", gen)

        # Force the first frame immediately (no waiting for the first frameChanged).
        try:
            movie.stop()
            set_loop = getattr(movie, "setLoopCount", None)
            if callable(set_loop):
                set_loop(1)
            movie.jumpToFrame(0)
        except Exception:
            pass

        pix = movie.currentPixmap()
        if not pix.isNull():
            button.setIcon(QtGui.QIcon(pix))

        movie.start()

    def _stop_folder_gif_animation(self, key: str) -> None:
        """Stop any in-flight folder.gif animation for this row and restore the static icon."""
        # Advance generation token to invalidate any pending "finished" callbacks.
        self._folder_hover_gen[key] = int(self._folder_hover_gen.get(key, 0)) + 1
        movie = self._folder_movies.get(key)
        if movie is not None:
            try:
                movie.stop()
                movie.jumpToFrame(0)
            except Exception:
                pass
        self._restore_folder_static_icon(key)

    def _restore_folder_static_icon(self, key: str) -> None:
        """Restore the static folder icon on the row's Output button."""
        button = self._open_buttons.get(key)
        if button is None:
            return
        static_icon = _load_asset_icon(
            "folder.gif",
            QtWidgets.QStyle.StandardPixmap.SP_DirOpenIcon,
            self.style(),
        )
        button.setIcon(static_icon)

    # (embed panel handling removed — lives in OptionsDialog now)

    def _start_processing(self) -> None:
        if self._worker:
            return
        files = [
            self.queue_list.topLevelItem(i).data(
                0, QtCore.Qt.ItemDataRole.UserRole
            )
            for i in range(self.queue_list.topLevelItemCount())
        ]
        if not files:
            return

        self._clear_eta()
        self._clear_all_eta_cells()

        # Fresh run: reset per-file status + progress
        for i in range(self.queue_list.topLevelItemCount()):
            item = self.queue_list.topLevelItem(i)
            self._apply_status_icon_and_tooltip(item, "Queued")
            # Hide whichever wrapper widget is currently attached to the cell.
            wrapper = self.queue_list.itemWidget(item, self._progress_column)
            if wrapper is not None:
                wrapper.setVisible(False)
            # Reset the underlying bar (stored per media key).
            raw = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if raw:
                bar = self._item_progress.get(str(raw))
                if bar is not None:
                    bar.setValue(0)
                    bar.setVisible(False)

        # Queue-level progress for the footer bar
        self._queue_total_count = len(files)
        self._queue_completed_count = 0
        self._update_queue_progress_bar(0.0)

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

        # The Progress column stays visible; we only attach a bar to the
        # row that is currently processing.
        # When a run starts, make sure all row bars are hidden until the
        # specific file's handler shows its own bar.
        if running:
            self._hide_all_row_progress_bars()

    def _append_log(self, message: str) -> None:
        # Buffer rapid-fire logs and flush in batches to keep the UI responsive.
        if not message:
            return
        self._log_buffer.append(str(message))
        if not self._log_flush_timer.isActive():
            self._log_flush_timer.start()

    def _flush_log_buffer(self) -> None:
        if not getattr(self, "_log_buffer", None):
            self._log_flush_timer.stop()
            return
        chunk = "\n".join(self._log_buffer)
        self._log_buffer.clear()
        try:
            self.log_view.appendPlainText(chunk)
            sb = self.log_view.verticalScrollBar()
            sb.setValue(sb.maximum())
        except Exception:
            # Log output must never crash the UI.
            pass

    def _on_file_started(self, path: str) -> None:
        if self._log_tailer:
            self._log_tailer.start()
        self._append_log(f"Processing {path}")
        # Ensure only this file's bar is visible
        self._hide_all_row_progress_bars()
        self._set_queue_item_status(path, "Processing…")
        self._set_queue_item_progress(path, 0)

        self._eta_mode_gpu = bool(self._basic_options.get("prefer_gpu", True))
        self._eta_media = path
        # Avoid blocking the UI thread with ffprobe; use cached duration (filled
        # asynchronously when files are queued) and fall back to "unknown".
        duration_s = float(self._queue_duration_cache.get(str(Path(path)), 0.0) or 0.0)
        estimate = self._eta_memory.estimate(self._eta_mode_gpu, duration_s)
        if estimate > 0:
            self._set_eta(estimate)
            self._set_queue_item_eta(path, estimate)
        else:
            total = self._queue_total_count or self.queue_list.topLevelItemCount() or 1
            current_index = min(total, max(1, self._queue_completed_count + 1))

            # Overall queue % when we don't have ETA training yet
            if total > 0:
                overall_fraction = float(self._queue_completed_count) / float(total)
            else:
                overall_fraction = 0.0
            percent_total = int(round(max(0.0, min(1.0, overall_fraction)) * 100))

            # New footer format: Transcribing X of Y (a%) – Queue ETA ~ –
            self.eta_label.setText(
                f"Transcribing {current_index} of {total} ({percent_total}%) – Queue ETA ~ –"
            )

            # We still show queue-level progress (discrete per file)
            self._update_queue_progress_bar(0.0)

    def _on_file_completed(self, media: str, summary: str) -> None:
        if self._log_tailer:
            self._log_tailer.stop()
        self._append_log(f"✅ {media}: {summary}")
        self._set_queue_item_status(media, "Completed")
        self._set_queue_item_progress(media, 100)

        # NEW: capture SRT + diagnostics sidecars for this row
        key = str(media)
        artifacts = self._item_outputs.get(key, {})

        srt_path: Optional[Path] = None

        # 1) Try to parse the SRT path from the summary (first segment before ';')
        try:
            first_part = summary.split(";", 1)[0].strip()
        except Exception:
            first_part = ""
        if first_part:
            candidate = Path(first_part).expanduser()
            if candidate.exists():
                srt_path = candidate

        # 2) Fallback to the expected SRT location if parsing fails
        if srt_path is None:
            expected = _expected_srt_path(Path(media))
            if expected.exists():
                srt_path = expected

        if srt_path is not None and srt_path.exists():
            artifacts["srt"] = str(srt_path)

            # Diagnostics are written next to the SRT by default
            csv_candidate = srt_path.with_suffix(srt_path.suffix + ".diag.csv")
            if csv_candidate.exists():
                artifacts["diag_csv"] = str(csv_candidate)
            json_candidate = srt_path.with_suffix(srt_path.suffix + ".diag.json")
            if json_candidate.exists():
                artifacts["diag_json"] = str(json_candidate)

        # If we already know the run_id, capture the log path here too
        run_id = self._file_run_ids.get(key)
        if run_id:
            artifacts["run_id"] = run_id
            log_candidate = LOGS_DIR / f"{run_id}.log"
            if log_candidate.exists():
                artifacts["log"] = str(log_candidate)

        self._item_outputs[key] = artifacts

        # Enable the "View ▾" button now that we have something to open
        button = self._open_buttons.get(key)
        if button and artifacts:
            button.setEnabled(True)
            # Run the folder.gif animation once to indicate new files in the folder
            self._play_folder_gif_once(key)

        if self._queue_total_count:
            self._queue_completed_count = min(
                self._queue_total_count, self._queue_completed_count + 1
            )
            # No active file at this instant → current_file_fraction = 0
            self._update_queue_progress_bar(0.0)

        self._clear_eta()

    def _on_file_failed(self, media: str, reason: str) -> None:
        if self._log_tailer:
            self._log_tailer.stop()
        self._append_log(f"⚠️ {media}: {reason}")
        self._set_queue_item_status(media, "Failed")
        self._set_queue_item_progress(media, 0)

        if self._queue_total_count:
            # Treat failed attempts as "processed" for queue-level progress
            self._queue_completed_count = min(
                self._queue_total_count, self._queue_completed_count + 1
            )
            self._update_queue_progress_bar(0.0)

        self._clear_eta()

    def _handle_run_log_ready(self, run_id: str) -> None:
        if self._log_tailer:
            self._log_tailer.set_run_id(run_id)

        # NEW: associate this run_id with the currently active media file
        media = self._eta_media
        if not media:
            return

        key = str(media)
        self._file_run_ids[key] = run_id

        artifacts = self._item_outputs.get(key, {})
        artifacts["run_id"] = run_id
        log_candidate = LOGS_DIR / f"{run_id}.log"
        if log_candidate.exists():
            artifacts["log"] = str(log_candidate)
        self._item_outputs[key] = artifacts

        # As soon as we have a log, the "Open…" button is already useful
        button = self._open_buttons.get(key)
        if button:
            button.setEnabled(True)

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
        self._reset_queue_progress_bar()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: D401 - Qt override
        if self._worker:
            self._worker.request_stop()
            self._worker.wait(2000)
        self._save_persistent_options()
        super().closeEvent(event)

    def _open_options_dialog(self) -> None:
        initial_basic = dict(self._basic_options)
        initial_settings = settings

        # The Options dialog should reflect the *current* session configuration.
        #
        # We keep a single persistent YAML (srtforge.config) that stores both GUI
        # state and Advanced/Performance tuning. When re-opening Options we must
        # load from that file so controls don't snap back to shipped defaults
        # (e.g. GPU limit -> 100%).
        if self._runtime_config_path:
            try:
                runtime_path = Path(self._runtime_config_path)
                if runtime_path.exists():
                    initial_settings = load_settings(runtime_path)
            except Exception:
                # Never fail to open Options just because loading a session YAML failed.
                initial_settings = settings

        dialog = OptionsDialog(parent=self, initial_basic=initial_basic, initial_settings=initial_settings)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return

        reset_eta = dialog.eta_reset_requested()
        reset_defaults = dialog.defaults_reset_requested()

        # If the user hit "Reset to defaults", also reset the app theme to
        # light mode. We apply this here (after OK) so Cancel truly cancels.
        # Persistence is handled by the _update_persistent_config() call below.
        if reset_defaults:
            self._dark_mode = False
            if hasattr(self, "theme_toggle"):
                block = self.theme_toggle.blockSignals(True)
                try:
                    self.theme_toggle.setChecked(False)
                finally:
                    self.theme_toggle.blockSignals(block)
            self._update_theme_toggle_label()
            self._apply_styles()
        basic = dialog.basic_values()
        self._basic_options = basic
        payload = dialog.settings_payload(prefer_gpu=basic["prefer_gpu"])
        # Persist *both* GUI and pipeline settings into the single srtforge.config file.
        gui_payload = dict(basic)
        gui_payload["last_media_dir"] = str(getattr(self, "_last_media_dir", "") or "")
        gui_payload["dark_mode"] = bool(getattr(self, "_dark_mode", False))
        self._update_persistent_config(pipeline=payload, gui=gui_payload)
        try:
            cfg_path = Path(self._config_path)
            self._runtime_config_path = str(cfg_path) if cfg_path.exists() else None
        except Exception:
            self._runtime_config_path = None
        self._append_log(f"Using persistent options file ({str(self._config_path)})")

        if reset_eta:
            try:
                self._eta_memory.reset()
                self._append_log("ETA training data cleared; future runs will retrain from new jobs.")
            except Exception as exc:
                self._append_log(f"Failed to reset ETA training data: {exc}")

    def _write_runtime_yaml(self, payload: dict) -> str:
        """Legacy helper.

        Older builds wrote a random per-session YAML into %TEMP% (e.g.
        ``srtforge_gui_xxxxx.yaml``). The GUI now persists everything into a
        single ``srtforge.config`` file, so this helper simply updates that file
        and returns its path.
        """

        self._update_persistent_config(pipeline=payload)
        try:
            cfg_path = Path(self._config_path)
            self._runtime_config_path = str(cfg_path) if cfg_path.exists() else None
        except Exception:
            self._runtime_config_path = None
        return self._runtime_config_path or str(self._config_path)

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
        """Stop ETA timer and reset ETA bookkeeping.

        Note: we intentionally do NOT clear the whole ETA column here because
        completed rows repurpose it to show their actual transcription time.
        """

        active = self._eta_media

        self._eta_deadline = None
        self._eta_total = 0.0
        self._eta_media = None

        if hasattr(self, "eta_label"):
            self.eta_label.setText("Idle")
        if self._eta_timer.isActive():
            self._eta_timer.stop()

        # If we were tracking a file that did *not* complete, clear its ETA cell
        # so we don't leave a stale countdown behind after cancellation/failure.
        if active:
            item = self._find_queue_item(active)
            if item is not None:
                status = item.text(getattr(self, "_status_column", 1))
                if status != "Completed":
                    item.setText(self._eta_column, ETA_PLACEHOLDER)
                    item.setData(self._eta_column, QtCore.Qt.ItemDataRole.UserRole, 0.0)
                    item.setToolTip(self._eta_column, "")

    def _estimate_queue_remaining(self, current_remaining: float) -> float:
        """Return an estimated remaining wall time for the entire queue.

        ``current_remaining`` is the remaining ETA for the active file in seconds.
        """
        remaining = max(0.0, float(current_remaining))

        # If the queue list or ETA memory are not available, fall back to the
        # current file only.
        if not hasattr(self, "queue_list") or not hasattr(self, "_eta_memory"):
            return remaining

        current_path = Path(self._eta_media) if self._eta_media else None

        for i in range(self.queue_list.topLevelItemCount()):
            item = self.queue_list.topLevelItem(i)
            raw = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if not raw:
                continue

            path = Path(str(raw))
            # The active file is already accounted for via current_remaining.
            if current_path is not None and path == current_path:
                continue

            status = item.text(getattr(self, "_status_column", 1))
            if status in {"Completed", "Failed"}:
                continue

            duration_s = float(self._queue_duration_cache.get(str(path), 0.0))
            if duration_s <= 0:
                continue

            try:
                eta_for_file = float(
                    self._eta_memory.estimate(self._eta_mode_gpu, duration_s)
                )
            except Exception:
                # If for some reason the estimator fails, assume 1x real time.
                eta_for_file = duration_s

            remaining += max(0.0, eta_for_file)

        return remaining

    def _tick_eta(self) -> None:
        if self._eta_deadline is None or self._eta_total <= 0:
            if hasattr(self, "eta_label"):
                self.eta_label.setText("Idle")
            # Queue progress bar is managed separately
            return

        now = time.monotonic()
        # Per-file numbers
        remaining_file = max(0.0, self._eta_deadline - now)
        elapsed_file = self._eta_total - remaining_file
        progress_file = max(0.0, min(1.0, elapsed_file / self._eta_total))
        percent_file = int(progress_file * 100)

        # Whole-queue ETA: current file + all queued files.
        queue_remaining = self._estimate_queue_remaining(remaining_file)
        queue_remaining = max(0.0, queue_remaining)

        # Queue position: completed files + the current one
        total_files = self._queue_total_count or (
            self.queue_list.topLevelItemCount() if hasattr(self, "queue_list") else 0
        )
        if total_files <= 0:
            total_files = 1
        current_index = min(total_files, max(1, self._queue_completed_count + 1))

        # Queue‑level completion %: completed files + current file fraction
        done_files = max(0, min(total_files, self._queue_completed_count))
        overall = (done_files + progress_file) / float(total_files)
        overall = max(0.0, min(1.0, overall))
        percent_queue = int(round(overall * 100))

        if hasattr(self, "eta_label"):
            queue_eta_str = _format_hms(queue_remaining)
            # New footer format:
            # Transcribing X of Y (a%) – Queue ETA ~ HH:MM:SS
            self.eta_label.setText(
                f"Transcribing {current_index} of {total_files} ({percent_queue}%) – Queue ETA ~ {queue_eta_str}"
            )

        # Update the row progress bar and ETA cell for the current file (per-file ETA).
        if self._eta_media:
            self._set_queue_item_progress(self._eta_media, percent_file)
            self._set_queue_item_eta(self._eta_media, remaining_file)

        # Footer progress bar tracks whole-queue completion (files done + current fraction).
        self._update_queue_progress_bar(progress_file)

        if remaining_file <= 0:
            self._eta_timer.stop()

    def _on_eta_measured(self, media: str, runtime_s: float, duration_s: float, prefer_gpu: bool) -> None:
        # Persist throughput sample for future estimates.
        try:
            self._eta_memory.update(prefer_gpu, duration_s, runtime_s)
        except Exception:
            # ETA training should never break the UI.
            pass

        # For successfully completed files, repurpose the ETA column to show the
        # actual transcription time for that file.
        item = self._find_queue_item(media)
        if item is None:
            return

        try:
            runtime = max(0.0, float(runtime_s or 0.0))
        except Exception:
            runtime = 0.0

        # Format as elapsed wall time (MM:SS / H:MM:SS).
        runtime_text = _format_hms(runtime)
        if runtime_text == ETA_PLACEHOLDER:
            runtime_text = "00:00"

        item.setText(self._eta_column, runtime_text)
        item.setData(self._eta_column, QtCore.Qt.ItemDataRole.UserRole, runtime)

        # Helpful tooltip: include realtime factor when the media duration is known.
        tip = f"Transcription time: {runtime_text}"
        try:
            dur = float(duration_s or 0.0)
        except Exception:
            dur = 0.0
        if dur > 0 and runtime > 0:
            speed = dur / runtime
            tip += f" ({speed:.2f}× realtime)"
        item.setToolTip(self._eta_column, tip)


def _asset_candidates(filename: str) -> list[Path]:
    """Return likely locations for an image asset."""
    candidates: list[Path] = []

    # Packaged resource
    try:
        res = resources.files("srtforge.assets.images").joinpath(filename)
        candidates.append(Path(str(res)))
    except Exception:
        pass

    here = Path(__file__).resolve().parent
    candidates.append(here / "assets" / "images" / filename)
    candidates.append(here / "assets" / filename)
    candidates.append(here / filename)

    # Developer checkout path you mentioned
    if os.name == "nt":
        candidates.append(Path(r"C:\Srtforge\srtforge\assets\images") / filename)

    # Deduplicate and normalise
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        try:
            norm = path.resolve()
        except Exception:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        unique.append(norm)
    return unique




def _load_asset_pixmap(filename: str) -> Optional[QtGui.QPixmap]:
    """Load a pixmap for ``filename`` from our assets folder, if available."""
    for path in _asset_candidates(filename):
        if path.exists():
            pixmap = QtGui.QPixmap(str(path))
            if not pixmap.isNull():
                return pixmap
    return None


def _load_asset_icon(
    filename: str,
    fallback: QtWidgets.QStyle.StandardPixmap,
    style: Optional[QtWidgets.QStyle] = None,
) -> QtGui.QIcon:
    """Return an icon for ``filename`` or fall back to a standard icon."""
    pixmap = _load_asset_pixmap(filename)
    if pixmap is not None:
        return QtGui.QIcon(pixmap)
    if style is None:
        style = QtWidgets.QApplication.style()
    return style.standardIcon(fallback)

def _pixmap_looks_like_flat_block(pixmap: QtGui.QPixmap) -> bool:
    """Heuristic: detect an icon pixmap that renders as a near-solid block.

    This is used to avoid the console toggle showing up as a plain black square
    if the PNG asset loses detail when scaled or when its alpha channel isn't
    preserved correctly by the host environment.
    """
    try:
        if pixmap.isNull():
            return False
        img = pixmap.toImage()
        if img.isNull():
            return False

        w, h = img.width(), img.height()
        if w < 4 or h < 4:
            return False

        step = max(1, min(w, h) // 32)
        total_samples = ((w + step - 1) // step) * ((h + step - 1) // step)

        opaque = 0
        rmin = gmin = bmin = 255
        rmax = gmax = bmax = 0

        for y in range(0, h, step):
            for x in range(0, w, step):
                c = img.pixelColor(x, y)
                if c.alpha() < 200:
                    continue
                opaque += 1
                r = int(c.red())
                g = int(c.green())
                b = int(c.blue())
                if r < rmin:
                    rmin = r
                if g < gmin:
                    gmin = g
                if b < bmin:
                    bmin = b
                if r > rmax:
                    rmax = r
                if g > gmax:
                    gmax = g
                if b > bmax:
                    bmax = b

        # Mostly transparent => not a solid block.
        if opaque == 0 or (opaque / max(1, total_samples)) < 0.75:
            return False

        # Very low colour variance => likely a flat/solid square.
        return (rmax - rmin) < 10 and (gmax - gmin) < 10 and (bmax - bmin) < 10
    except Exception:
        return False


def _make_terminal_icon(color: QtGui.QColor, *, size: int = 64) -> QtGui.QIcon:
    """Generate a simple terminal glyph icon (transparent background)."""
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.GlobalColor.transparent)

    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

    pen_w = max(3, int(size * 0.08))
    pen = QtGui.QPen(color)
    pen.setWidth(pen_w)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.BrushStyle.NoBrush)

    # Outer rounded square
    pad = pen_w / 2.0
    rect = QtCore.QRectF(pad, pad, size - (2 * pad), size - (2 * pad))
    radius = size * 0.18
    p.drawRoundedRect(rect, radius, radius)

    # Prompt: ">"
    x_left = size * 0.32
    x_tip = size * 0.45
    y_top = size * 0.34
    y_mid = size * 0.50
    y_bot = size * 0.66
    p.drawLine(QtCore.QPointF(x_left, y_top), QtCore.QPointF(x_tip, y_mid))
    p.drawLine(QtCore.QPointF(x_left, y_bot), QtCore.QPointF(x_tip, y_mid))

    # Prompt: "_"
    ux1 = size * 0.54
    ux2 = size * 0.74
    uy = size * 0.66
    p.drawLine(QtCore.QPointF(ux1, uy), QtCore.QPointF(ux2, uy))

    p.end()
    return QtGui.QIcon(pm)



def _load_asset_movie(filename: str) -> Optional[QtGui.QMovie]:
    """Return a QMovie for an animated asset if it exists."""
    for path in _asset_candidates(filename):
        if path.exists():
            movie = QtGui.QMovie(str(path))
            if movie.isValid():
                return movie
    return None


@lru_cache(maxsize=1)
def _load_app_icon() -> QtGui.QIcon:
    """Return the application logo as a QIcon."""

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

        # Bound the work: icons don’t need multi-megapixel source images at runtime.
        max_dim = max(pixmap.width(), pixmap.height())
        if max_dim > 512:
            pixmap = pixmap.scaled(
                512,
                512,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )

        return QtGui.QIcon(pixmap)

    return QtGui.QIcon()


def main() -> None:
    """Entry point used by ``srtforge-gui``."""

    _startup_trace("main: enter")
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_EnableHighDpiScaling)
    _startup_trace("main: AA_EnableHighDpiScaling set")
    # Optional: nicer HiDPI rounding on recent Qt builds
    try:
        QtCore.QCoreApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass
    _startup_trace("main: dpi rounding policy set")
    app = QtWidgets.QApplication(sys.argv)
    _startup_trace("main: QApplication created")
    app.setStyle("Fusion")
    _startup_trace("main: style set")
    icon = _load_app_icon()
    _startup_trace("main: _load_app_icon done")
    if not icon.isNull():
        app.setWindowIcon(icon)

    window = MainWindow(app_icon=icon)
    _startup_trace("main: MainWindow constructed")
    window.show()
    _startup_trace("main: window.show() returned")
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


def _parse_ffprobe_ratio(value: str) -> float:
    """Parse ffprobe ratio strings like '24000/1001' into float."""

    try:
        value = (value or "").strip()
        if not value or value == "0/0":
            return 0.0
        if "/" in value:
            num_s, den_s = value.split("/", 1)
            num = float(num_s)
            den = float(den_s)
            if den == 0:
                return 0.0
            return num / den
        return float(value)
    except Exception:
        return 0.0


def _format_fps(fps: float) -> str:
    """Format an fps value for the queue metadata."""

    if fps <= 0:
        return ETA_PLACEHOLDER
    rounded = round(float(fps))
    if abs(fps - rounded) < 0.01:
        return f"{int(rounded)} FPS"
    # Trim trailing zeros while keeping useful precision
    s = f"{fps:.3f}".rstrip("0").rstrip(".")
    return f"{s} FPS"


def _format_khz(sample_rate: object) -> str:
    """Format an audio sample rate in kHz for the queue metadata."""

    if sample_rate is None:
        return ETA_PLACEHOLDER
    try:
        hz = float(sample_rate)
    except Exception:
        return ETA_PLACEHOLDER
    if hz <= 0:
        return ETA_PLACEHOLDER

    khz = hz / 1000.0
    rounded = round(khz)
    if abs(khz - rounded) < 0.01:
        return f"{int(rounded)} kHz"

    s = f"{khz:.1f}".rstrip("0").rstrip(".")
    return f"{s} kHz"


def _probe_media_streams_ffprobe_cmd(ffprobe_bin: Optional[Path], media: Path) -> tuple[str, str]:
    """Return (audio_display, video_display) for the given media file.

    Queue column policy:
      - Audio: show sample rate (kHz) + channel count (e.g. "48 kHz 2ch").
      - Video: show frame rate (fps) (e.g. "23.976 FPS").
    """

    exe = str(ffprobe_bin or "ffprobe")

    cmd = [
        exe,
        "-v",
        "error",
        "-show_entries",
        "stream=index,codec_type,channels,sample_rate,r_frame_rate,avg_frame_rate:stream_tags=language,LANGUAGE",
        "-of",
        "json",
        str(media),
    ]

    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
        data = json.loads(out or "{}")
    except Exception:
        return (ETA_PLACEHOLDER, ETA_PLACEHOLDER)

    streams = data.get("streams") or []
    audio_streams = [s for s in streams if (s or {}).get("codec_type") == "audio"]
    video_streams = [s for s in streams if (s or {}).get("codec_type") == "video"]

    # ---- audio ----
    audio_display = ETA_PLACEHOLDER
    if audio_streams:

        def _lang(s: dict) -> str:
            tags = (s or {}).get("tags") or {}
            return str(tags.get("language") or tags.get("LANGUAGE") or "").lower()

        preferred = None
        for s in audio_streams:
            lang = _lang(s)
            if lang in {"eng", "en"} or lang.startswith("en"):
                preferred = s
                break
        if preferred is None:
            preferred = audio_streams[0]

        sr = _format_khz((preferred or {}).get("sample_rate"))
        ch_raw = (preferred or {}).get("channels")
        ch_txt = ""
        try:
            ch_i = int(ch_raw)
            if ch_i > 0:
                ch_txt = f"{ch_i}ch"
        except Exception:
            if ch_raw:
                ch_txt = f"{ch_raw}ch"

        bits: list[str] = []
        if sr != ETA_PLACEHOLDER:
            bits.append(sr)
        if ch_txt:
            bits.append(ch_txt)
        if bits:
            audio_display = " ".join(bits)

    # ---- video ----
    video_display = ETA_PLACEHOLDER
    if video_streams:
        v0 = video_streams[0] or {}
        fps = _parse_ffprobe_ratio(str(v0.get("avg_frame_rate") or ""))
        if fps <= 0:
            fps = _parse_ffprobe_ratio(str(v0.get("r_frame_rate") or ""))
        video_display = _format_fps(fps)

    return (audio_display, video_display)



def _probe_media_duration_ffprobe(media: Path, ffprobe_bin: Optional[Path]) -> float:
    return _probe_media_duration_ffprobe_cmd(ffprobe_bin, media)


def _format_hms(seconds: float) -> str:
    """Format seconds as H:MM:SS or MM:SS; return '–' for unknown."""

    if seconds <= 0:
        return "–"
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


QUEUE_PROGRESS_WIDTH = 220
ETA_PLACEHOLDER = "–"


class _EtaMemory:
    """Persist simple throughput ratios per device to estimate ETAs."""

    def __init__(self) -> None:
        self.path = LOGS_DIR / "eta_memory.json"
        self.data = self._defaults()
        self._load()

    def _defaults(self) -> dict:
        return {
            "gpu": {"factor": 1.0, "samples": 0},
            "cpu": {"factor": 1.0, "samples": 0},
        }

    def _load(self) -> None:
        try:
            if self.path.exists():
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = self._defaults()

    def reset(self) -> None:
        """Clear persisted ETA history so future runs retrain from scratch."""
        self.data = self._defaults()
        try:
            if self.path.exists():
                self.path.unlink()
        except Exception:
            # If deletion fails (permissions, etc.), just overwrite with defaults.
            pass
        self._save()

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
