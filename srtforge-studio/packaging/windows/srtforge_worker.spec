# PyInstaller spec for the headless Srtforge worker, bundled as the
# Tauri sidecar `srtforge_worker-x86_64-pc-windows-msvc.exe`.
#
# Build (from anywhere — paths are resolved relative to this spec file):
#
#   pyinstaller srtforge-studio\packaging\windows\srtforge_worker.spec ^
#       --distpath srtforge-studio\src-tauri\binaries
#
# Then rename the produced exe to add Rust's target triple suffix that
# Tauri's sidecar lookup expects:
#
#   ren srtforge-studio\src-tauri\binaries\srtforge_worker.exe ^
#       srtforge_worker-x86_64-pc-windows-msvc.exe
#
# Optional: bundle ffmpeg/ffprobe if you set SRTFORGE_FFMPEG_DIR before
# invoking PyInstaller.
#
# Models (.ckpt, .yaml, ~600 MB) are NOT bundled. The worker reads them
# from %APPDATA%\Srtforge\models\ at runtime; first-run UX downloads them
# from GitHub Releases.

# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# `SPECPATH` is provided by PyInstaller and points to the directory of
# this .spec file (srtforge-studio\packaging\windows\). Walk three levels
# up to reach the Srtforge repo root so paths work regardless of where
# pyinstaller is invoked from.
PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, "..", "..", ".."))

# The actual entry point — `python -m srtforge` exposes the CLI; the
# `worker` subcommand is the persistent JSON-IPC loop the Tauri shell
# spawns and writes to.
worker_entry = os.path.join(PROJECT_ROOT, "srtforge", "__main__.py")

datas = []
binaries = []

# Optionally bundle ffmpeg/ffprobe if a path is set in the build env.
ffmpeg_dir = os.environ.get("SRTFORGE_FFMPEG_DIR")
if ffmpeg_dir:
    for exe in ("ffmpeg.exe", "ffprobe.exe"):
        full = os.path.join(ffmpeg_dir, exe)
        if os.path.isfile(full):
            binaries.append((full, "."))

# Pull in the default config so a fresh install has sane defaults.
default_config = os.path.join(PROJECT_ROOT, "srtforge", "config.yaml")
if os.path.isfile(default_config):
    datas.append((default_config, "srtforge"))

# Hidden imports — anything reflectively loaded by the pipeline.
hidden = []
hidden += collect_submodules("srtforge")
hidden += [
    "torch",
    "torchaudio",
    # Parakeet / NeMo (only present if the Parakeet engine is installed)
    "nemo_toolkit",
    "nemo.collections.asr",
    # FV4 separation backend (only present if installed)
    "audio_separator",
]

a = Analysis(
    [worker_entry],
    pathex=[PROJECT_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # IMPORTANT: exclude PySide6 — this build is the headless worker only.
    excludes=["PySide6", "PyQt5", "PyQt6", "tkinter", "matplotlib", "pytest"],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="srtforge_worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,            # headless worker speaks JSON on stdout
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
