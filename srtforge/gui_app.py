"""Windows 11-inspired GUI for the srtforge transcription pipeline."""

from __future__ import annotations

import importlib.resources as resources
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .config import DEFAULT_OUTPUT_SUFFIX
from .settings import settings
from .win11_backdrop import apply_win11_look, get_windows_accent_qcolor


@dataclass(slots=True)
class FFmpegBinaries:
    """Resolved FFmpeg and ffprobe executables."""

    ffmpeg: Path
    ffprobe: Path


@dataclass(slots=True)
class WorkerOptions:
    """Options that control how the transcription worker runs."""

    prefer_gpu: bool
    embed_subtitles: bool
    burn_subtitles: bool
    cleanup_gpu: bool
    ffmpeg_bin: Optional[str]
    ffprobe_bin: Optional[str]


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

    def __init__(self, files: Iterable[str], options: WorkerOptions, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self.files = [Path(path) for path in files]
        self.options = options
        self._stop_event = threading.Event()
        self._active_process: Optional[subprocess.Popen[str]] = None

    def request_stop(self) -> None:
        """Ask the worker to stop after the current task finishes."""

        import signal as _signal
        import os as _os
        self._stop_event.set()
        process = self._active_process
        if process and process.poll() is None:
            try:
                if _os.name == "nt":
                    # Signal the process group spawned with CREATE_NEW_PROCESS_GROUP
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
                if self.options.embed_subtitles and self.options.ffmpeg_bin:
                    if self._stop_event.is_set():
                        raise StopRequested
                    try:
                        embed_output = self._embed_subtitles(media_path, srt_path)
                    except StopRequested:
                        raise
                    except Exception as exc:  # pragma: no cover - defensive logging
                        raise RuntimeError(f"Embed failed: {exc}") from exc
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
    def _run_pipeline_subprocess(self, media: Path) -> Path:
        cli_binary = locate_cli_executable()
        if cli_binary:
            command = [str(cli_binary), "run", str(media)]
        else:
            command = [sys.executable, "-m", "srtforge.cli", "run", str(media)]
        if not self.options.prefer_gpu:
            command.append("--cpu")
        env = os.environ.copy()
        ffmpeg_dir = _ffmpeg_directory_from_options(self.options)
        if ffmpeg_dir:
            env_path = env.get("PATH", "")
            env["PATH"] = os.pathsep.join(str(part) for part in (ffmpeg_dir, env_path) if part)
        return_code, stdout, stderr = self._run_command(command, "Transcription", env=env, check=False)
        if return_code == 0:
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
        output = media.with_name(f"{media.stem}_subbed{media.suffix}")
        codec = "mov_text" if media.suffix.lower() in {".mp4", ".m4v", ".mov"} else "srt"
        subtitle_index = self._count_subtitle_streams(media)
        command = [
            self.options.ffmpeg_bin or "ffmpeg",
            "-y",
            "-i",
            str(media),
            "-i",
            str(subtitles),
            "-c",
            "copy",
            "-c:s",
            codec,
            "-map",
            "0",
            "-map",
            "1",
            f"-disposition:s:{subtitle_index}",
            "default",
            f"-metadata:s:s:{subtitle_index}",
            "language=eng",
            str(output),
        ]
        self._run_command(command, "Embed subtitles")
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
            "-c:a", "copy",
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
            text=True,
            env=env,
            **kwargs,
        )
        self._active_process = process
        stdout = ""
        stderr = ""
        try:
            stdout, stderr = process.communicate()
        finally:
            self._active_process = None
        for stream in (stdout, stderr):
            for line in (stream or "").splitlines():
                if line.strip():
                    self.logMessage.emit(line)
        if check and process.returncode != 0:
            if self._stop_event.is_set():
                raise StopRequested
            message = stderr.strip() or stdout.strip() or f"{description} failed"
            raise RuntimeError(message)
        return process.returncode or 0, stdout or "", stderr or ""


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


class MainWindow(QtWidgets.QMainWindow):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("srtforge Studio")
        self.setMinimumSize(960, 640)
        self.setObjectName("MainWindow")
        self._worker: Optional[TranscriptionWorker] = None
        self.ffmpeg_paths = locate_ffmpeg_binaries()
        self._build_ui()
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
        self.burn_checkbox = QtWidgets.QCheckBox("Burn subtitles (hard sub)")
        self.cleanup_checkbox = QtWidgets.QCheckBox("Free GPU memory when stopping")
        options_layout.addWidget(self.embed_checkbox, 1, 0, 1, 2)
        options_layout.addWidget(self.burn_checkbox, 2, 0, 1, 2)
        options_layout.addWidget(self.cleanup_checkbox, 3, 0, 1, 2)
        layout.addWidget(options_group)
        add_shadow(options_group)

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
        if self.ffmpeg_paths:
            self.ffmpeg_status.setText(f"FFmpeg detected at {self.ffmpeg_paths.ffmpeg.parent}")
        else:
            self.ffmpeg_status.setText(
                "FFmpeg binaries not found. Place ffmpeg/ffprobe next to the executable or set "
                "SRTFORGE_FFMPEG_DIR."
            )
            self.embed_checkbox.setEnabled(False)
            self.burn_checkbox.setEnabled(False)

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

    def _start_processing(self) -> None:
        if self._worker:
            return
        files = [self.queue_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole) for i in range(self.queue_list.count())]
        if not files:
            return
        prefer_gpu = bool(self.device_combo.currentData())
        options = WorkerOptions(
            prefer_gpu=prefer_gpu,
            embed_subtitles=self.embed_checkbox.isChecked(),
            burn_subtitles=self.burn_checkbox.isChecked(),
            cleanup_gpu=self.cleanup_checkbox.isChecked(),
            ffmpeg_bin=str(self.ffmpeg_paths.ffmpeg) if self.ffmpeg_paths else None,
            ffprobe_bin=str(self.ffmpeg_paths.ffprobe) if self.ffmpeg_paths else None,
        )
        self._worker = TranscriptionWorker(files, options)
        self._worker.logMessage.connect(self._append_log)
        self._worker.progress.connect(self._update_progress)
        self._worker.fileStarted.connect(self._on_file_started)
        self._worker.fileCompleted.connect(self._on_file_completed)
        self._worker.fileFailed.connect(self._on_file_failed)
        self._worker.queueFinished.connect(self._on_queue_finished)
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
        self.embed_checkbox.setEnabled((self.ffmpeg_paths is not None) and not running)
        self.burn_checkbox.setEnabled((self.ffmpeg_paths is not None) and not running)

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _update_progress(self, current: int, total: int) -> None:
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(current)
        if total:
            self.progress_label.setText(f"{current}/{total} files")
        else:
            self.progress_label.setText("Idle")

    def _on_file_started(self, path: str) -> None:
        self._append_log(f"Processing {path}")

    def _on_file_completed(self, media: str, summary: str) -> None:
        self._append_log(f"✅ {media}: {summary}")

    def _on_file_failed(self, media: str, reason: str) -> None:
        self._append_log(f"⚠️ {media}: {reason}")

    def _on_queue_finished(self, stopped: bool) -> None:
        self._append_log("Queue cancelled" if stopped else "All files processed")
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
