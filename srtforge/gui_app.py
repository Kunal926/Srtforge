"""Windows 11-inspired GUI for the srtforge transcription pipeline."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .ffmpeg import FFmpegTooling
from .pipeline import PipelineConfig, run_pipeline


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

        self._stop_event.set()
        process = self._active_process
        if process and process.poll() is None:
            process.terminate()

    def run(self) -> None:  # noqa: D401 - Qt override
        if self.options.ffmpeg_bin and self.options.ffprobe_bin:
            tooling = FFmpegTooling(self.options.ffmpeg_bin, self.options.ffprobe_bin)
        else:
            tooling = FFmpegTooling()
        total = len(self.files)
        self.progress.emit(0, total)
        for index, media_path in enumerate(self.files, start=1):
            if self._stop_event.is_set():
                break
            self.fileStarted.emit(str(media_path))
            try:
                config = PipelineConfig(
                    media_path=media_path,
                    tools=tooling,
                    prefer_gpu=self.options.prefer_gpu,
                    separation_prefer_gpu=self.options.prefer_gpu,
                )
                result = run_pipeline(config)
                if result.skipped:
                    reason = result.reason or "Pipeline skipped"
                    self.fileFailed.emit(str(media_path), reason)
                else:
                    srt_path = result.output_path
                    if not srt_path:
                        self.fileFailed.emit(str(media_path), "SRT output missing")
                    else:
                        embed_output = None
                        burn_output = None
                        if self.options.embed_subtitles and self.options.ffmpeg_bin:
                            try:
                                embed_output = self._embed_subtitles(media_path, srt_path)
                            except Exception as exc:  # pragma: no cover - defensive logging
                                self.fileFailed.emit(str(media_path), f"Embed failed: {exc}")
                        if self.options.burn_subtitles and self.options.ffmpeg_bin:
                            try:
                                burn_output = self._burn_subtitles(media_path, srt_path)
                            except Exception as exc:  # pragma: no cover - defensive logging
                                self.fileFailed.emit(str(media_path), f"Burn failed: {exc}")
                        summary_parts = [str(srt_path)]
                        if embed_output:
                            summary_parts.append(f"embedded → {embed_output}")
                        if burn_output:
                            summary_parts.append(f"burned → {burn_output}")
                        self.fileCompleted.emit(str(media_path), "; ".join(summary_parts))
            except Exception as exc:  # pragma: no cover - defensive safeguard
                self.fileFailed.emit(str(media_path), str(exc))
            self.progress.emit(index, total)
        stopped = self._stop_event.is_set()
        self.queueFinished.emit(stopped)

    # ---- helpers -----------------------------------------------------------------
    def _embed_subtitles(self, media: Path, subtitles: Path) -> Path:
        output = media.with_name(f"{media.stem}_subbed{media.suffix}")
        codec = "mov_text" if media.suffix.lower() in {".mp4", ".m4v", ".mov"} else "srt"
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
            "-disposition:s:0",
            "default",
            "-metadata:s:s:0",
            "language=eng",
            str(output),
        ]
        self._run_command(command, "Embed subtitles")
        return output

    def _burn_subtitles(self, media: Path, subtitles: Path) -> Path:
        output = media.with_name(f"{media.stem}_burned{media.suffix}")
        subtitles_arg = subtitles.as_posix()
        command = [
            self.options.ffmpeg_bin or "ffmpeg",
            "-y",
            "-i",
            str(media),
            "-vf",
            f"subtitles={subtitles_arg}:force_style='Fontsize=24'",
            "-c:a",
            "copy",
            str(output),
        ]
        self._run_command(command, "Burn subtitles")
        return output

    def _run_command(self, command: List[str], description: str) -> None:
        self.logMessage.emit(f"{description}: {' '.join(command)}")
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self._active_process = process
        stdout, stderr = process.communicate()
        self._active_process = None
        if process.returncode != 0:
            message = stderr.strip() or stdout.strip() or f"{description} failed"
            raise RuntimeError(message)


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
        for button in (self.start_button, self.stop_button):
            button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        layout.addLayout(button_row)

        self.ffmpeg_status = QtWidgets.QLabel()
        self.ffmpeg_status.setWordWrap(True)
        layout.addWidget(self.ffmpeg_status)

        self.setCentralWidget(central)

    def _apply_styles(self) -> None:
        palette = self.palette()
        palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#f3f3f3"))
        palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#ffffff"))
        palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor("#2563eb"))
        palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor("#2563eb"))
        palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor("#ffffff"))
        self.setPalette(palette)
        self.setStyleSheet(
            """
            #HeaderLabel {
                font-size: 20px;
                font-weight: 600;
            }
            #DropArea {
                border: 2px dashed #94a3b8;
                border-radius: 16px;
                background-color: #ffffff;
            }
            QPushButton {
                border-radius: 8px;
                padding: 8px 18px;
                font-weight: 600;
            }
            QPushButton:disabled {
                background-color: #cbd5f5;
                color: #7c818c;
            }
            QGroupBox {
                border: 1px solid #e2e8f0;
                border-radius: 12px;
                margin-top: 12px;
                padding: 12px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 20px;
                padding: 0 4px;
            }
            QListWidget {
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
            QPlainTextEdit {
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                background-color: #0f172a;
                color: #e2e8f0;
                font-family: Consolas, 'Cascadia Code', monospace;
            }
        """
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
        self._append_log("Stopping after current task…")
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
