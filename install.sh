#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN=${PYTHON:-python3}
VENV_DIR=".venv"
USE_GPU="auto"

for arg in "$@"; do
  case "$arg" in
    --cpu)
      USE_GPU="cpu"
      ;;
    --gpu)
      USE_GPU="gpu"
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment in $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip wheel
pip install -r requirements.txt

install_torch() {
  local device="$1"
  local packages=(torch torchvision torchaudio)
  if [ "$device" = "gpu" ]; then
    local cuda_tag="130"
    if [ "${CUDA_VERSION:-auto}" != "auto" ]; then
      cuda_tag="$CUDA_VERSION"
    fi
    echo "Installing Torch with CUDA ${cuda_tag} wheels"
    pip uninstall -y "${packages[@]}" >/dev/null 2>&1 || true
    pip install \
      --upgrade \
      --force-reinstall \
      --no-cache-dir \
      --index-url "https://download.pytorch.org/whl/cu${cuda_tag}" \
      --extra-index-url https://pypi.org/simple \
      "${packages[@]}"
    python <<'PY'
import sys

try:
    import torch
except Exception as exc:  # pragma: no cover - diagnostic helper
    print(f"WARNING: Unable to import torch after installation: {exc}", file=sys.stderr)
else:
    cuda_version = getattr(torch.version, "cuda", None)
    if not cuda_version:
        print("WARNING: PyTorch CUDA runtime was not detected after installation. CPU-only wheels may still be in use.", file=sys.stderr)
    elif not torch.cuda.is_available():
        print(f"WARNING: PyTorch reports CUDA {cuda_version} but no GPU is currently available. Check your NVIDIA drivers.", file=sys.stderr)
    else:
        print(f"Detected CUDA-enabled PyTorch (CUDA {cuda_version}).")
PY
  else
    echo "Installing Torch CPU wheels"
    pip install --index-url https://download.pytorch.org/whl/cpu "${packages[@]}"
  fi
}

install_onnxruntime() {
  local device="$1"
  if [ "$device" = "gpu" ]; then
    echo "Installing ONNX Runtime with CUDA support"
    if ! pip install "onnxruntime-gpu>=1.23.2"; then
      cat <<'EOF'
Failed to install the CUDA-enabled ONNX Runtime wheel. GPU vocal separation requires the
`onnxruntime-gpu` package and a compatible NVIDIA driver. Falling back to the CPU build.
EOF
      pip install "onnxruntime>=1.23.2"
      return 1
    fi
  else
    echo "Installing ONNX Runtime CPU build"
    pip install "onnxruntime>=1.23.2"
  fi
  return 0
}

SELECTED_DEVICE="cpu"
if [ "$USE_GPU" = "cpu" ]; then
  SELECTED_DEVICE="cpu"
elif [ "$USE_GPU" = "gpu" ]; then
  SELECTED_DEVICE="gpu"
else
  if command -v nvidia-smi >/dev/null 2>&1; then
    SELECTED_DEVICE="gpu"
  else
    echo "No NVIDIA GPU detected, falling back to CPU wheels"
    SELECTED_DEVICE="cpu"
  fi
fi

install_torch "$SELECTED_DEVICE"
install_onnxruntime "$SELECTED_DEVICE"

echo "Installing cuda-python bindings required for NeMo CUDA graphs"
pip install "cuda-python>=12.3"

pip install "nemo_toolkit[asr]>=2.5.1,<2.6"

python <<'PY'
import importlib
import signal
import sys

if not hasattr(signal, "SIGKILL"):
    _sigkill_fallback = getattr(signal, "SIGTERM", getattr(signal, "SIGABRT", 9))
    setattr(signal, "SIGKILL", _sigkill_fallback)

try:
    importlib.import_module("nemo.collections.asr")
except Exception as exc:  # pragma: no cover - installer validation
    print(
        "ERROR: NVIDIA NeMo ASR components failed to import after installation. "
        "This usually means one of its dependencies (such as numpy, pyarrow or matplotlib) "
        "was not installed correctly.",
        file=sys.stderr,
    )
    print(f"       Original import error: {exc}", file=sys.stderr)
    sys.exit(1)
else:
    print("✔ Verified NVIDIA NeMo ASR modules are importable.")
PY

pip install -e .

python <<'PY'
import os
import sys
from pathlib import Path

import requests

MODELS = [
    (
        "https://huggingface.co/audio-separator/melband-roformer-fv4/resolve/main/voc_fv4.ckpt?download=1",
        "voc_fv4.ckpt",
    ),
    (
        "https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2/resolve/main/parakeet-tdt-0.6b-v2.nemo?download=1",
        "parakeet-tdt-0.6b-v2.nemo",
    ),
]

models_dir = Path(__file__).parent / "models"
models_dir.mkdir(parents=True, exist_ok=True)

def download(url: str, filename: str) -> None:
    target = models_dir / filename
    if target.exists() and target.stat().st_size > 0:
        print(f"✔ {filename} already present")
        return

    headers = {}
    token = os.getenv("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    print(f"Downloading {filename} from {url}")
    with requests.get(url, stream=True, headers=headers, timeout=300) as response:
        if response.status_code == 401:
            print(f"!! Authorization required for {url}. Set HF_TOKEN with a valid Hugging Face token.", file=sys.stderr)
            response.raise_for_status()
        response.raise_for_status()
        with open(target, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 15):
                if chunk:
                    handle.write(chunk)

for url, filename in MODELS:
    download(url, filename)
PY

echo "Installation complete. Activate the virtual environment with 'source $VENV_DIR/bin/activate'."
