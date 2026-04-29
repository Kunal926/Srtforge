$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tauriRoot = Join-Path $repoRoot 'srtforge-studio\src-tauri'
Set-Location $repoRoot

if (-not (Test-Path $tauriRoot)) {
    Write-Host "SKIPPED: srtforge-studio/src-tauri is missing."
    exit 0
}

if ($null -eq (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Write-Host "SKIPPED: cargo is not on PATH."
    exit 0
}

Set-Location $tauriRoot
cargo check
