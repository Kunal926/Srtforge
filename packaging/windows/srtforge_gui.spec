# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the srtforge Windows GUI."""

import os
from pathlib import Path

from PyInstaller.building.datastruct import Tree

block_cipher = None

here = Path(__file__).resolve().parent.parent
project_root = here.parent

ffmpeg_dir = os.environ.get("SRTFORGE_FFMPEG_DIR")
datas = []

models_dir = project_root / "models"
if models_dir.exists():
    datas.append(Tree(str(models_dir), prefix="models"))

config_yaml = project_root / "srtforge" / "config.yaml"
if config_yaml.exists():
    datas.append((str(config_yaml), "srtforge"))

win11_qss = project_root / "srtforge" / "assets" / "styles" / "win11.qss"
if win11_qss.exists():
    datas.append((str(win11_qss), "srtforge/assets/styles"))

if ffmpeg_dir:
    ffmpeg_path = Path(ffmpeg_dir)
    ffmpeg_bin = ffmpeg_path / "ffmpeg.exe"
    ffprobe_bin = ffmpeg_path / "ffprobe.exe"
    if ffmpeg_bin.exists() and ffprobe_bin.exists():
        datas.append((str(ffmpeg_bin), "ffmpeg"))
        datas.append((str(ffprobe_bin), "ffmpeg"))

hiddenimports = ["PySide6.QtSvg", "PySide6.QtNetwork"]

gui_script = str(project_root / "srtforge" / "gui_app.py")
cli_script = str(project_root / "srtforge" / "cli.py")

analysis_kwargs = dict(
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

gui_analysis = Analysis([gui_script], **analysis_kwargs)
gui_pyz = PYZ(gui_analysis.pure, gui_analysis.zipped_data, cipher=block_cipher)
gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
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

cli_analysis = Analysis([cli_script], **analysis_kwargs)
cli_pyz = PYZ(cli_analysis.pure, cli_analysis.zipped_data, cipher=block_cipher)
cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name="SrtforgeCLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    gui_exe,
    cli_exe,
    gui_analysis.binaries,
    cli_analysis.binaries,
    gui_analysis.zipfiles,
    cli_analysis.zipfiles,
    gui_analysis.datas,
    cli_analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SrtforgeGUI",
)
