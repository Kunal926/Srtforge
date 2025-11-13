# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the srtforge Windows GUI."""

import os
from pathlib import Path

block_cipher = None

here = Path(__file__).resolve().parent.parent
project_root = here.parent

ffmpeg_dir = os.environ.get("SRTFORGE_FFMPEG_DIR")
datas = []

models_dir = project_root / "models"
if models_dir.exists():
    datas.append((str(models_dir), "models"))

config_yaml = project_root / "srtforge" / "config.yaml"
if config_yaml.exists():
    datas.append((str(config_yaml), "srtforge"))

if ffmpeg_dir:
    ffmpeg_path = Path(ffmpeg_dir)
    ffmpeg_bin = ffmpeg_path / "ffmpeg.exe"
    ffprobe_bin = ffmpeg_path / "ffprobe.exe"
    if ffmpeg_bin.exists() and ffprobe_bin.exists():
        datas.append((str(ffmpeg_bin), "ffmpeg/ffmpeg.exe"))
        datas.append((str(ffprobe_bin), "ffmpeg/ffprobe.exe"))

hiddenimports = ["PySide6.QtSvg", "PySide6.QtNetwork"]


a = Analysis(
    [str(project_root / "srtforge" / "gui_app.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SrtforgeGUI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SrtforgeGUI",
)
