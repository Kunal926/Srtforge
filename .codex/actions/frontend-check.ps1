$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$studioRoot = Join-Path $repoRoot 'srtforge-studio'
Set-Location $repoRoot

if (-not (Test-Path $studioRoot)) {
    Write-Host "SKIPPED: srtforge-studio/ is missing."
    exit 0
}

if (-not (Test-Path (Join-Path $studioRoot 'node_modules'))) {
    Write-Host "SKIPPED: srtforge-studio/node_modules is missing. This action does not install dependencies."
    exit 0
}

if ($null -eq (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    Write-Host "SKIPPED: pnpm is not on PATH."
    exit 0
}

Set-Location $studioRoot
pnpm exec tsc --noEmit
