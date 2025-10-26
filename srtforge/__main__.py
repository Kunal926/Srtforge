"""Allow running ``python -m srtforge`` to expose the CLI."""

from __future__ import annotations

from .cli import app


if __name__ == "__main__":  # pragma: no cover
    app()
