# PyInstaller spec for the headless Srtforge worker, bundled as a Tauri
# resource directory at `src-tauri/binaries/srtforge_worker/`.
#
# Build (from anywhere — paths are resolved relative to this spec file):
#
#   pyinstaller srtforge-studio\packaging\windows\srtforge_worker.spec ^
#       --distpath srtforge-studio\src-tauri\binaries
#
# The onedir output keeps PyInstaller's `_internal` folder beside the worker
# exe. Do not convert this back to one-file unless worker startup extraction
# time is acceptable again.
#
# Optional: bundle ffmpeg/ffprobe if you set SRTFORGE_FFMPEG_DIR before
# invoking PyInstaller.
#
# Models (.ckpt, .yaml, ~600 MB) are NOT bundled. The worker reads them
# from %APPDATA%\Srtforge\models\ at runtime; first-run UX downloads them
# from GitHub Releases.

# -*- mode: python ; coding: utf-8 -*-

import importlib.util
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# `SPECPATH` is provided by PyInstaller and points to the directory of
# this .spec file (srtforge-studio\packaging\windows\). Walk three levels
# up to reach the Srtforge repo root so paths work regardless of where
# pyinstaller is invoked from.
PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, "..", "..", ".."))

# The actual entry point. We don't bundle `srtforge/__main__.py` directly
# because it uses `from .cli import app` (relative import) which requires
# being run as `python -m srtforge`; PyInstaller runs the entry as a
# top-level script, so we use a tiny shim with an absolute import.
worker_entry = os.path.join(SPECPATH, "srtforge_worker_entry.py")
cuda_redirector_hook = os.path.join(SPECPATH, "pyi_rth_cuda_bindings_redirector.py")

datas = []
binaries = []
hidden = []

# Heavy ML / audio packages: hidden-imports + collect_submodules misses
# lazily-loaded submodules and native DLLs in PyInstaller bundles (e.g.
# `torch.version`, ONNX Runtime's CUDA EPs, audio_separator's adapters).
# `collect_all` walks the package and grabs every .py + every data file +
# every binary, so the bundle behaves like the venv. Skips silently if a
# package isn't installed in the build environment.
for pkg in (
    "torch",
    "torchaudio",
    "onnxruntime",
    "audio_separator",
    "av",
    "pydub",
    "librosa",
    "soundfile",
    "tqdm",
    "rich",
    "typer",
    # cuda-python ships a `cuda` namespace package with native .pyd
    # extensions (cuda.bindings.runtime, cuda.cudart, ...). Without
    # collect_all, PyInstaller misses the bindings and the worker raises
    # "cuda-python is installed but its CUDA runtime bindings are missing"
    # at runtime when the Parakeet engine tries to load on GPU.
    "cuda",
    # NeMo pulls in PyTorch Lightning. lightning_fabric reads its
    # `version.info` data file at import time; without collect_all the
    # bundle is missing it and Parakeet fails with
    # "[Errno 2] No such file or directory: '..._MEI*/lightning_fabric/version.info'".
    "lightning_fabric",
    "pytorch_lightning",
    "lightning",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += h
    except Exception as exc:
        print(f"[srtforge spec] skip collect_all({pkg!r}): {exc}")

# cuda-python 12.x installs a top-level redirector and .pth file that make
# `from cuda import __version__` and legacy trampoline imports work. PyInstaller
# does not execute .pth files, so include the module explicitly and import it
# from a runtime hook before NeMo's CUDA-graph checks run.
hidden.append("_cuda_bindings_redirector")
try:
    redirector_spec = importlib.util.find_spec("_cuda_bindings_redirector")
except Exception as exc:
    print(f"[srtforge spec] skip CUDA redirector discovery: {exc}")
else:
    if redirector_spec and redirector_spec.origin and os.path.isfile(redirector_spec.origin):
        datas.append((redirector_spec.origin, "."))
        pth = os.path.join(os.path.dirname(redirector_spec.origin), "_cuda_bindings_redirector.pth")
        if os.path.isfile(pth):
            datas.append((pth, "."))

# cuda-python 12.9's `cuda.bindings.runtime` imports
# `cuda.bindings.driver` dynamically; collect_all("cuda") can miss that
# native module in PyInstaller on Windows.
for mod_name in (
    "cuda.bindings.driver",
    "cuda.bindings.cydriver",
    "cuda.bindings.cyruntime",
    "cuda.bindings._bindings.cydriver",
    "cuda.bindings._bindings.cyruntime",
    "cuda.bindings._bindings.cyruntime_ptds",
):
    try:
        mod_spec = importlib.util.find_spec(mod_name)
    except Exception as exc:
        print(f"[srtforge spec] skip {mod_name} discovery: {exc}")
    else:
        if mod_spec and mod_spec.origin and os.path.isfile(mod_spec.origin):
            module_dir = os.path.dirname(mod_name.replace(".", os.sep))
            binaries.append((mod_spec.origin, module_dir))

try:
    audio_separator_spec = importlib.util.find_spec("audio_separator")
except Exception as exc:
    print(f"[srtforge spec] skip audio_separator data discovery: {exc}")
else:
    if audio_separator_spec and audio_separator_spec.origin:
        audio_separator_dir = os.path.dirname(audio_separator_spec.origin)
        for name in ("models-scores.json", "models.json", "model-data.json", "ensemble_presets.json"):
            full = os.path.join(audio_separator_dir, name)
            if os.path.isfile(full):
                datas.append((full, "audio_separator"))

# Optional: include nemo_toolkit if installed (only needed when the
# Parakeet engine is selected at runtime).
try:
    d, b, h = collect_all("nemo_toolkit")
    datas += d; binaries += b; hidden += h
except Exception:
    pass

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

# Project itself.
hidden += collect_submodules("srtforge")

a = Analysis(
    [worker_entry],
    pathex=[PROJECT_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[cuda_redirector_hook],
    # IMPORTANT: exclude PySide6 — this build is the headless worker only.
    # matplotlib is NOT excluded: NeMo's ASR imports it at runtime for
    # spectrogram/debug helpers and the bundle fails with
    # "No module named 'matplotlib'" partway through Parakeet inference.
    excludes=["PySide6", "PyQt5", "PyQt6", "tkinter", "pytest"],
    # NeMo's Parakeet model uses torch.jit.script on functions like
    # `nemo.collections.asr.parts.utils.activations.snake`. TorchScript
    # reads the function source via inspect.getsource() at runtime, so
    # the bundle MUST ship the original .py files, not just .pyc.
    # `pyz+py` keeps both forms; `nemo` covers both `nemo` and
    # `nemo_toolkit` install layouts. `torch` is included because parts
    # of torch's jit machinery also reflect on their own source.
    module_collection_mode={
        "nemo": "pyz+py",
        "torch": "pyz+py",
    },
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name="srtforge_worker",
    exclude_binaries=True,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    contents_directory="_internal",
    runtime_tmpdir=None,
    console=True,            # headless worker speaks JSON on stdout
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
    upx=False,
    name="srtforge_worker",
)
