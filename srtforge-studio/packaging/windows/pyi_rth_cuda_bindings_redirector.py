"""PyInstaller runtime hook for CUDA Python 12.x compatibility imports."""

from __future__ import annotations

import sys
import os


def _add_dll_directory(relative_path: str) -> None:
    base_dir = getattr(sys, "_MEIPASS", None)
    if not base_dir or not hasattr(os, "add_dll_directory"):
        return
    candidate = os.path.join(base_dir, relative_path)
    if os.path.isdir(candidate):
        os.add_dll_directory(candidate)


for _dll_dir in ("cuda", os.path.join("torch", "lib"), "torchvision"):
    _add_dll_directory(_dll_dir)


try:
    import _cuda_bindings_redirector  # noqa: F401
except Exception as exc:  # pragma: no cover - runs inside frozen sidecar
    sys.stderr.write(f"[srtforge] CUDA bindings redirector hook skipped: {exc}\n")
