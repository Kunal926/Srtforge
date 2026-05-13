"""Console and file logging helpers built on top of Rich."""

from __future__ import annotations

import atexit
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Callable, Iterator, Optional
from uuid import uuid4

from .config import PROJECT_ROOT

if TYPE_CHECKING:
    from rich.console import Console

_console: Optional["Console"] = None
_cleanup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="log-cleanup")

# Module-level event emitter for pipeline worker events. Installed by
# callers that want pipeline timing/progress events surfaced (typically
# the worker JSON loop in :mod:`srtforge.cli`); left as ``None`` in plain
# CLI usage so `srtforge run ...` stays quiet on stdout.
_event_emitter: Optional[Callable[[dict], None]] = None


def set_event_emitter(cb: Optional[Callable[[dict], None]]) -> None:
    """Install (or clear) a callback for pipeline worker events.

    The callback receives event dicts emitted by ``RunLogger.step`` and
    progress helpers; the worker typically installs a closure that injects
    the current job id and forwards to the JSON line emitter. Pass ``None``
    to clear after the job finishes.
    """

    global _event_emitter
    _event_emitter = cb


def _emit_event(payload: dict) -> bool:
    """Best-effort dispatch to the registered emitter (swallows exceptions)."""

    cb = _event_emitter
    if cb is None:
        return False
    try:
        cb(payload)
        return True
    except Exception:
        # A broken emitter must never take down the pipeline.
        return False


def _emit_stage(payload: dict) -> None:
    _emit_event(payload)


def emit_progress(stage: str, fraction: float, *, eta: Optional[str] = None) -> None:
    """Emit a worker ``progress`` event through the installed pipeline emitter.

    The emitted payload intentionally omits ``id`` here. The worker's per-job
    emitter injects the active job id before writing the JSON line, matching
    the existing stage-event flow.
    """

    from .worker_protocol import progress_event

    payload = progress_event(id="", stage=stage, fraction=fraction, eta=eta)  # type: ignore[arg-type]
    payload.pop("id", None)
    _emit_event(payload)


def emit_log(message: str, *, lvl: str = "info", source: str = "pipeline") -> bool:
    """Emit a worker ``log`` event through the installed pipeline emitter."""

    from .worker_protocol import log_event

    payload = log_event(message, lvl=lvl, source=source)
    return _emit_event(payload)


@contextmanager
def log_heartbeat(
    label: str,
    callback: Callable[[str], None],
    *,
    interval_seconds: float = 30.0,
) -> Iterator[None]:
    """Call ``callback`` periodically while a blocking operation is running."""

    if interval_seconds <= 0:
        yield
        return

    stop = threading.Event()
    started = monotonic()

    def beat() -> None:
        while not stop.wait(interval_seconds):
            elapsed = monotonic() - started
            try:
                callback(f"{label} still running after {elapsed:.0f}s")
            except Exception:
                logger.debug("Log heartbeat callback failed", exc_info=True)

    thread = threading.Thread(
        target=beat,
        name=f"srtforge-{label.lower().replace(' ', '-')}-heartbeat",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1.0)


def _shutdown_executor() -> None:
    """Shutdown the executor on module unload.
    
    Note: Uses wait=True to ensure cleanup completes, but since cleanup tasks
    are designed to be fast (simple file deletion), this should not cause
    significant delays during interpreter shutdown.
    """
    _cleanup_executor.shutdown(wait=True)


# Ensure executor is properly shutdown when module is unloaded
atexit.register(_shutdown_executor)

logger = logging.getLogger(__name__)

LOGS_DIR = PROJECT_ROOT / "logs"
LATEST_LOG = LOGS_DIR / "srtforge.log"


def get_console() -> "Console":
    """Return the shared Rich console (created lazily)."""

    global _console
    if _console is None:
        from rich.console import Console

        _console = Console()
    return _console


@contextmanager
def status(message: str) -> Iterator[None]:
    """Show a transient status spinner when running slow operations."""

    with get_console().status(message, spinner="dots"):
        yield


def _cleanup_old_logs_task(max_age_hours: int) -> None:
    """Background task to remove old log files."""
    try:
        if not LOGS_DIR.exists():
            return

        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        for candidate in LOGS_DIR.glob("*.log"):
            if candidate == LATEST_LOG:
                # ``LATEST_LOG`` is recreated on every run and handled separately.
                continue
            try:
                modified = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if modified < cutoff:
                try:
                    candidate.unlink()
                except OSError:
                    continue
    except Exception as e:
        # Use standard logging for thread-safe error reporting
        logger.warning("Failed to cleanup old logs: %s", e)


def cleanup_old_logs(max_age_hours: int = 24, *, wait: bool = True, timeout: float = 30.0) -> None:
    """Remove ``*.log`` files in :data:`LOGS_DIR` older than ``max_age_hours``.
    
    This function submits a cleanup task to a background thread. By default,
    it waits for the cleanup to complete to avoid race conditions with concurrent
    log file creation. Set ``wait=False`` for fire-and-forget cleanup.
    
    Args:
        max_age_hours: Maximum age in hours for log files to keep
        wait: If True, blocks until cleanup completes. If False, returns immediately.
        timeout: Maximum time in seconds to wait for cleanup when wait=True.
            Ignored when wait=False. (default: 30s)
    
    Note:
        If timeout occurs while waiting, the cleanup task continues running in the
        background. The timeout only affects how long this function blocks, not the
        cleanup task itself.
    """
    # Use a module-level executor to avoid resource leaks
    future = _cleanup_executor.submit(_cleanup_old_logs_task, max_age_hours)
    if wait:
        try:
            # Wait for cleanup to complete to avoid race conditions
            future.result(timeout=timeout)
        except FutureTimeoutError:
            logger.warning("Log cleanup timed out after %s seconds (task continues in background)", timeout)


@dataclass(slots=True)
class _TimedStep:
    """Context manager recording the duration of a logging step.

    When constructed with a ``stage`` identifier, also pushes ``stage``
    events through the module-level emitter so the GUI can light up
    per-stage progress dots without the pipeline knowing the GUI exists.

    Stage events carry:

    - ``stage`` — canonical stage name (see
      ``docs/contracts/worker-protocol.md``).
    - ``state`` — ``"start"`` or ``"end"``.
    - ``msg`` — the human-readable step label, useful for log overlays.
    - ``run_id`` — current ``RunLogger.run_id`` so the GUI can correlate
      stage events with files produced by the same run.
    - ``seconds``, ``ok`` — only on ``state:"end"``.
    """

    logger: "RunLogger"
    label: str
    stage: Optional[str] = None
    _start: float = 0.0

    def __enter__(self) -> None:
        self.logger._log(f"START {self.label}")
        self._start = monotonic()
        if self.stage:
            _emit_stage(
                {
                    "event": "stage",
                    "stage": self.stage,
                    "state": "start",
                    "msg": self.label,
                    "run_id": self.logger.run_id,
                }
            )

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        duration = monotonic() - getattr(self, "_start", monotonic())
        if exc_type:
            self.logger._log(f"ERROR in {self.label}: {exc}")
        self.logger._log(f"END {self.label} – {duration:.2f}s")
        if self.stage:
            _emit_stage(
                {
                    "event": "stage",
                    "stage": self.stage,
                    "state": "end",
                    "msg": self.label,
                    "run_id": self.logger.run_id,
                    "seconds": round(duration, 3),
                    "ok": exc_type is None,
                }
            )


class RunLogger:
    """Helper responsible for structured run logging and timing information."""

    def __init__(self, run_id: str, log_path: Path) -> None:
        self.run_id = run_id
        self.path = log_path
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        cleanup_old_logs(wait=False)
        self._handle = log_path.open("w", encoding="utf8")
        self._latest_handle = LATEST_LOG.open("w", encoding="utf8")
        now = datetime.now(timezone.utc)
        self._log_header(f"Run {run_id} started at {now.isoformat()}Z")
        self._start = monotonic()
        self._status: str = "completed"
        self._detail: Optional[str] = None

    @classmethod
    def start(cls) -> "RunLogger":
        """Create a :class:`RunLogger` bound to a new UUID."""

        run_id = uuid4().hex
        return cls(run_id, LOGS_DIR / f"{run_id}.log")

    def _log_header(self, message: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        line = f"[{timestamp}] {message}\n"
        self._handle.write(line)
        self._handle.flush()
        self._latest_handle.write(line)
        self._latest_handle.flush()

    def _log(self, message: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        line = f"[{timestamp}] {message}\n"
        self._handle.write(line)
        self._handle.flush()
        self._latest_handle.write(line)
        self._latest_handle.flush()

    def log(self, message: str) -> None:
        """Record ``message`` with the current timestamp."""

        self._log(message)

    def log_error(self, message: str) -> None:
        """Record an error message and mark the run as failed."""

        self._status = "failed"
        self._detail = message
        self._log(f"ERROR: {message}")

    def mark_skipped(self, reason: str) -> None:
        """Mark the run as skipped with ``reason``."""

        self._status = "skipped"
        self._detail = reason
        self._log(f"SKIPPED: {reason}")

    def step(self, label: str, *, stage: Optional[str] = None) -> _TimedStep:
        """Return a context manager recording the duration of ``label``.

        Pass ``stage`` (e.g. ``"probe"``, ``"asr"``) to also emit
        ``stage`` events through the module-level emitter on enter/exit.
        """

        return _TimedStep(self, label, stage=stage)

    def close(self) -> None:
        """Finalize the log with the run summary."""

        total = monotonic() - self._start
        detail = f" ({self._detail})" if self._detail else ""
        self._log(f"Run {self.run_id} {self._status} in {total:.2f}s{detail}")
        self._handle.close()
        self._latest_handle.close()

    def __enter__(self) -> "RunLogger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        if exc_type:
            self.log_error(str(exc))
        self.close()


__all__ = [
    "RunLogger",
    "cleanup_old_logs",
    "emit_log",
    "emit_progress",
    "get_console",
    "log_heartbeat",
    "set_event_emitter",
    "status",
]
