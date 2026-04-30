$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repoRoot

$activate = Join-Path $repoRoot '.venv\Scripts\Activate.ps1'
if (-not (Test-Path $activate)) {
    Write-Error "Project .venv is missing. Create it before running Codex action pytest-default."
    exit 1
}

. $activate
python -m pytest --color=no -q
