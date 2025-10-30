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
  if [ "$device" = "gpu" ]; then
    echo "Installing Torch with CUDA wheels"
    pip install --extra-index-url https://download.pytorch.org/whl/cu124 torch torchvision torchaudio
  else
    echo "Installing Torch CPU wheels"
    pip install --extra-index-url https://download.pytorch.org/whl/cpu torch torchvision torchaudio
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

pip install nemo_toolkit[asr]==2.0.0

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
