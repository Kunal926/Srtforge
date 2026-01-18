"""Static configuration and default paths used by srtforge."""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
MODELS_DIR = PROJECT_ROOT / "models"
FV4_MODEL = MODELS_DIR / "voc_fv4.ckpt"
FV4_CONFIG = MODELS_DIR / "voc_gabox.yaml"
PARAKEET_MODEL = MODELS_DIR / "parakeet-tdt-0.6b-v2.nemo"
DEFAULT_OUTPUT_SUFFIX = ".srt"
