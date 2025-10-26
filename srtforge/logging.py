"""Console logging helpers built on top of Rich."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from rich.console import Console

_console = Console()


def get_console() -> Console:
    """Return the shared :class:`~rich.console.Console` instance."""

    return _console


@contextmanager
def status(message: str) -> Iterator[None]:
    """Show a transient status spinner when running slow operations."""

    with _console.status(message, spinner="dots"):
        yield
