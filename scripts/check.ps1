# scripts/check.ps1
#
# Lightweight validation harness for Srtforge — Windows / PowerShell.
# Mirrors scripts/check.sh on Unix.
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
#     pwsh ./scripts/check.ps1
#
# If PowerShell 7 is not installed on Windows, run the same harness with
# Windows PowerShell:
#
#     powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1
#
# Or activate the project venv first and run:
#
#     .\.venv\Scripts\Activate.ps1
#     .\scripts\check.ps1

$ErrorActionPreference = 'Continue'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

$failed = @()
$skipped = @()

function Step {
    param(
        [string]$Name,
        [scriptblock]$Block
    )
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Block
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    FAILED: $Name (exit $LASTEXITCODE)" -ForegroundColor Red
        $script:failed += $Name
    }
    else {
        Write-Host "    OK: $Name" -ForegroundColor Green
    }
}

function Skip {
    param([string]$Name, [string]$Reason)
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    Write-Host "    SKIPPED: $Reason" -ForegroundColor Yellow
    $script:skipped += "${Name}: ${Reason}"
}

function Has-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

# ----------------------------------------------------------------------
# Environment summary
# ----------------------------------------------------------------------
Write-Host "Srtforge lightweight check" -ForegroundColor Cyan
Write-Host "  Repo root: $repoRoot"
Write-Host "  Platform:  $($PSVersionTable.OS)"
Write-Host "  PS edition: $($PSVersionTable.PSEdition) $($PSVersionTable.PSVersion)"

if (Has-Command python) {
    $pyVer = & python --version 2>&1
    Write-Host "  Python:    $pyVer"
}
else {
    Write-Host "  Python:    not found on PATH" -ForegroundColor Yellow
}

$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
    $PythonCmd = $venvPython
    Write-Host "  Python env: repo venv ($PythonCmd)"
}
elseif (Has-Command python) {
    $PythonCmd = 'python'
    Write-Host "  Python env: PATH"
}
else {
    $PythonCmd = $null
}

if (Has-Command pnpm)   { Write-Host "  pnpm:      $(pnpm --version)" }
if (Has-Command node)   { Write-Host "  node:      $(node --version)" }
if (Has-Command cargo)  { Write-Host "  cargo:     $(cargo --version)" }
if (Has-Command ffmpeg) { Write-Host "  ffmpeg:    present (heavy tests still skipped by default)" }

# ----------------------------------------------------------------------
# Python smoke
# ----------------------------------------------------------------------
if (-not $PythonCmd) {
    Skip 'Python import smoke' 'python not on PATH'
    Skip 'pytest (default selection)' 'python not on PATH'
    Skip 'CLI smoke (--help)' 'python not on PATH'
}
else {
    Step 'Python import smoke' {
        & $PythonCmd -c "import srtforge, srtforge.cli, srtforge.pipeline, srtforge.settings"
    }

    # Default pytest selection excludes slow/model/cuda/media/ffmpeg.
    # The exclusions are encoded in pyproject.toml, so a bare invocation works.
    & $PythonCmd -c "import pytest" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Step 'pytest (default selection)' {
            & $PythonCmd -m pytest --color=no -q
        }
    }
    else {
        Skip 'pytest (default selection)' 'pytest not installed (pip install -e .[dev])'
    }

    Step 'CLI smoke (--help)' {
        & $PythonCmd -m srtforge --help | Out-Null
    }
}

# ----------------------------------------------------------------------
# Lint
# ----------------------------------------------------------------------
if ($PythonCmd) {
    & $PythonCmd -c "import ruff" 2>$null
}
else {
    $global:LASTEXITCODE = 1
}
if (($PythonCmd -and $LASTEXITCODE -eq 0) -or (Has-Command ruff)) {
    Step 'ruff (srtforge + tests + scripts)' {
        if ($PythonCmd -and $LASTEXITCODE -eq 0) {
            & $PythonCmd -m ruff check srtforge tests scripts
        }
        else {
            & ruff check srtforge tests scripts
        }
    }
}
else {
    Skip 'ruff' 'ruff not installed (pip install -e .[dev])'
}

# ----------------------------------------------------------------------
# Public docs / contract sanity
# ----------------------------------------------------------------------
if ($PythonCmd) {
    Step 'Public docs and contract check (scripts/check_docs.py)' {
        & $PythonCmd scripts/check_docs.py
    }
}

# ----------------------------------------------------------------------
# Frontend (Tauri Studio) — only if dependencies are already installed.
# ----------------------------------------------------------------------
$studioRoot = Join-Path $repoRoot 'srtforge-studio'
$studioNm = Join-Path $studioRoot 'node_modules'
if (-not (Test-Path $studioRoot)) {
    Skip 'Frontend type-check' 'srtforge-studio/ not present'
}
elseif (-not (Test-Path $studioNm)) {
    Skip 'Frontend type-check' 'srtforge-studio/node_modules missing (run `cd srtforge-studio && pnpm install`)'
}
elseif (-not (Has-Command pnpm)) {
    Skip 'Frontend type-check' 'pnpm not on PATH'
}
else {
    Step 'Frontend type-check (pnpm tsc --noEmit)' {
        Push-Location $studioRoot
        try {
            & pnpm exec tsc --noEmit
        }
        finally {
            Pop-Location
        }
    }
}

# ----------------------------------------------------------------------
# Rust (cargo check) — only if cargo is on PATH and the project is present.
# ----------------------------------------------------------------------
$tauriRoot = Join-Path $studioRoot 'src-tauri'
if (-not (Test-Path $tauriRoot)) {
    Skip 'cargo check' 'srtforge-studio/src-tauri not present'
}
elseif (-not (Has-Command cargo)) {
    Skip 'cargo check' 'cargo not on PATH'
}
else {
    Step 'cargo check (Rust shell)' {
        Push-Location $tauriRoot
        try {
            & cargo check --quiet
        }
        finally {
            Pop-Location
        }
    }
}

# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------
Write-Host ""
Write-Host "Summary" -ForegroundColor Cyan
if ($skipped.Count -gt 0) {
    Write-Host "  Skipped:" -ForegroundColor Yellow
    foreach ($s in $skipped) { Write-Host "    - $s" -ForegroundColor Yellow }
}
if ($failed.Count -gt 0) {
    Write-Host "  Failed:" -ForegroundColor Red
    foreach ($f in $failed) { Write-Host "    - $f" -ForegroundColor Red }
    exit 1
}

Write-Host "  All executed checks passed." -ForegroundColor Green
exit 0
