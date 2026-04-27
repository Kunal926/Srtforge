"""Entry shim for the PyInstaller-bundled worker sidecar.

`srtforge/__main__.py` does `from .cli import app` — a relative import
that only resolves when the package is run as `python -m srtforge`.
PyInstaller bundles the entry as a standalone top-level script, so the
dot has no parent package and the import fails with::

    ImportError: attempted relative import with no known parent package

This shim re-exports the same Typer app via an absolute import. The
`srtforge_worker.spec` points at this file as the Analysis entry.
"""

from srtforge.cli import app


if __name__ == "__main__":
    app()
