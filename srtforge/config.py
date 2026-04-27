"""Static configuration and default paths used by srtforge."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent


def _resolve_project_root() -> Path:
    """Pick the project root that should host ``models/``, ``output/``, etc.

    Outside a PyInstaller bundle this is just the package's parent directory
    (i.e. the Srtforge repo root). Inside a one-file bundle, ``__file__``
    points into the per-launch ``_MEI*`` extraction directory, which is wiped
    on exit and doesn't contain user data, so we instead look at:

      1. the ``SRTFORGE_PROJECT_ROOT`` env var (explicit override, used by
         the Tauri shell to pin the location regardless of where the sidecar
         exe lives), or
      2. the directory containing ``sys.executable`` — i.e. the folder the
         user installed the bundle into. Convention: ship a ``models/``
         subfolder alongside the exe.
    """
    if getattr(sys, "frozen", False):
        override = os.environ.get("SRTFORGE_PROJECT_ROOT")
        if override:
            return Path(override).expanduser().resolve()
        return Path(sys.executable).resolve().parent
    return PACKAGE_ROOT.parent


PROJECT_ROOT = _resolve_project_root()
MODELS_DIR = PROJECT_ROOT / "models"
FV4_MODEL = MODELS_DIR / "voc_fv4.ckpt"
FV4_CONFIG = MODELS_DIR / "voc_gabox.yaml"
DEFAULT_OUTPUT_SUFFIX = ".srt"
