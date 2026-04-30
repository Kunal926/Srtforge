$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repoRoot

$activate = Join-Path $repoRoot '.venv\Scripts\Activate.ps1'
if (-not (Test-Path $activate)) {
    Write-Error "Project .venv is missing. Create it before running Codex action harness-check."
    exit 1
}

. $activate

$checkScript = Join-Path $repoRoot 'scripts\check.ps1'
$pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
if ($pwsh) {
    & $pwsh.Source $checkScript
    exit $LASTEXITCODE
}

$windowsPowerShell = Get-Command powershell.exe -ErrorAction SilentlyContinue
if ($windowsPowerShell) {
    Write-Host "pwsh not found; falling back to Windows PowerShell." -ForegroundColor Yellow
    & $windowsPowerShell.Source -NoProfile -ExecutionPolicy Bypass -File $checkScript
    exit $LASTEXITCODE
}

Write-Error "Neither pwsh nor powershell.exe is available to run scripts/check.ps1."
exit 1
