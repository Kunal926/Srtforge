"""Console and file logging helpers built on top of Rich."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Iterator, Optional
from uuid import uuid4

from rich.console import Console

from .config import PROJECT_ROOT

_console = Console()
_cleanup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="log-cleanup")

logger = logging.getLogger(__name__)

LOGS_DIR = PROJECT_ROOT / "logs"
LATEST_LOG = LOGS_DIR / "srtforge.log"


def get_console() -> Console:
    """Return the shared :class:`~rich.console.Console` instance."""

    return _console


@contextmanager
def status(message: str) -> Iterator[None]:
    """Show a transient status spinner when running slow operations."""

    with _console.status(message, spinner="dots"):
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


def cleanup_old_logs(max_age_hours: int = 24, wait: bool = True) -> None:
    """Remove ``*.log`` files in :data:`LOGS_DIR` older than ``max_age_hours``.
    
    This function submits a cleanup task to a background thread. By default,
    it waits for the cleanup to complete to avoid race conditions with concurrent
    log file creation. Set ``wait=False`` for fire-and-forget cleanup.
    
    Args:
        max_age_hours: Maximum age in hours for log files to keep
        wait: If True, blocks until cleanup completes. If False, returns immediately.
    """
    # Use a module-level executor to avoid resource leaks
    future = _cleanup_executor.submit(_cleanup_old_logs_task, max_age_hours)
    if wait:
        # Wait for cleanup to complete to avoid race conditions
        future.result()


@dataclass(slots=True)
class _TimedStep:
    """Context manager recording the duration of a logging step."""

    logger: "RunLogger"
    label: str
    _start: float = 0.0

    def __enter__(self) -> None:
        self.logger._log(f"START {self.label}")
        self._start = monotonic()

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        duration = monotonic() - getattr(self, "_start", monotonic())
        if exc_type:
            self.logger._log(f"ERROR in {self.label}: {exc}")
        self.logger._log(f"END {self.label} – {duration:.2f}s")


class RunLogger:
    """Helper responsible for structured run logging and timing information."""

    def __init__(self, run_id: str, log_path: Path) -> None:
        self.run_id = run_id
        self.path = log_path
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        cleanup_old_logs()
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

    def step(self, label: str) -> _TimedStep:
        """Return a context manager recording the duration of ``label``."""

        return _TimedStep(self, label)

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
    "get_console",
    "status",
]
