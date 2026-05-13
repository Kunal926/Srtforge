#!/usr/bin/env bash
# scripts/check.sh
#
# Lightweight validation harness for Srtforge — Unix / WSL.
# Mirrors scripts/check.ps1 on Windows.
#
# This script must run without:
#   - CUDA / real GPU
#   - real model files (FV4, Whisper, Parakeet)
#   - model downloads
#   - real media
#   - private secrets
#   - heavyweight FFmpeg processing
#
# Skipped checks print a clear reason. Failures use exit code 1.
#
# Run from the repo root:
#
#     bash ./scripts/check.sh

set -u
set +e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

failed=()
skipped=()

step() {
    local name="$1"; shift
    echo ""
    echo "==> $name"
    "$@"
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "    FAILED: $name (exit $rc)"
        failed+=("$name")
    else
        echo "    OK: $name"
    fi
}

skip() {
    local name="$1"; shift
    local reason="$1"; shift
    echo ""
    echo "==> $name"
    echo "    SKIPPED: $reason"
    skipped+=("$name: $reason")
}

has() { command -v "$1" >/dev/null 2>&1; }

# ----------------------------------------------------------------------
# Environment summary
# ----------------------------------------------------------------------
echo "Srtforge lightweight check"
echo "  Repo root: $REPO_ROOT"
echo "  Platform:  $(uname -srm)"
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PY="$REPO_ROOT/.venv/bin/python"
    PY_ENV="repo venv"
elif [ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]; then
    PY="$REPO_ROOT/.venv/Scripts/python.exe"
    PY_ENV="repo venv"
elif has python || has python3; then
    PY=$(command -v python || command -v python3)
    PY_ENV="PATH"
else
    PY=""
    PY_ENV="not found"
fi

if [ -n "$PY" ]; then
    echo "  Python:    $($PY --version 2>&1)"
    echo "  Python env: $PY_ENV ($PY)"
else
    echo "  Python:    not found on PATH"
fi
has pnpm  && echo "  pnpm:      $(pnpm --version)"
has node  && echo "  node:      $(node --version)"
has cargo && echo "  cargo:     $(cargo --version)"
has ffmpeg && echo "  ffmpeg:    present (heavy tests still skipped by default)"

# ----------------------------------------------------------------------
# Python smoke
# ----------------------------------------------------------------------
if [ -z "$PY" ]; then
    skip 'Python import smoke' 'python not on PATH'
    skip 'pytest (default selection)' 'python not on PATH'
    skip 'CLI smoke (--help)' 'python not on PATH'
else
    step 'Python import smoke' "$PY" -c "import srtforge, srtforge.cli, srtforge.pipeline, srtforge.settings"

    if "$PY" -c "import pytest" 2>/dev/null; then
        step 'pytest (default selection)' "$PY" -m pytest --color=no -q
    else
        skip 'pytest (default selection)' 'pytest not installed (pip install -e .[dev])'
    fi

    step 'CLI smoke (--help)' "$PY" -m srtforge --help >/dev/null
fi

# ----------------------------------------------------------------------
# Lint
# ----------------------------------------------------------------------
if [ -n "$PY" ] && "$PY" -c "import ruff" 2>/dev/null; then
    step 'ruff (srtforge + tests + scripts)' "$PY" -m ruff check srtforge tests scripts
elif has ruff; then
    step 'ruff (srtforge + tests + scripts)' ruff check srtforge tests scripts
else
    skip 'ruff' 'ruff not installed (pip install -e .[dev])'
fi

# ----------------------------------------------------------------------
# Public docs / contract sanity
# ----------------------------------------------------------------------
if [ -n "$PY" ]; then
    step 'Public docs and contract check (scripts/check_docs.py)' "$PY" scripts/check_docs.py
fi

# ----------------------------------------------------------------------
# Frontend (Tauri Studio)
# ----------------------------------------------------------------------
STUDIO="$REPO_ROOT/srtforge-studio"
if [ ! -d "$STUDIO" ]; then
    skip 'Frontend type-check' 'srtforge-studio/ not present'
elif [ ! -d "$STUDIO/node_modules" ]; then
    skip 'Frontend type-check' 'srtforge-studio/node_modules missing (run `cd srtforge-studio && pnpm install`)'
elif ! has pnpm; then
    skip 'Frontend type-check' 'pnpm not on PATH'
else
    (
        cd "$STUDIO"
        step 'Frontend type-check (pnpm tsc --noEmit)' pnpm exec tsc --noEmit
    )
fi

# ----------------------------------------------------------------------
# Rust (cargo check)
# ----------------------------------------------------------------------
TAURI="$STUDIO/src-tauri"
if [ ! -d "$TAURI" ]; then
    skip 'cargo check' 'srtforge-studio/src-tauri not present'
elif ! has cargo; then
    skip 'cargo check' 'cargo not on PATH'
else
    (
        cd "$TAURI"
        step 'cargo check (Rust shell)' cargo check --quiet
    )
fi

# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------
echo ""
echo "Summary"
if [ ${#skipped[@]} -gt 0 ]; then
    echo "  Skipped:"
    for s in "${skipped[@]}"; do echo "    - $s"; done
fi
if [ ${#failed[@]} -gt 0 ]; then
    echo "  Failed:"
    for f in "${failed[@]}"; do echo "    - $f"; done
    exit 1
fi
echo "  All executed checks passed."
exit 0
