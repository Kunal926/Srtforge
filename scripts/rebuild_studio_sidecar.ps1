param(
    [switch]$SkipStop
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$spec = Join-Path $repoRoot "srtforge-studio\packaging\windows\srtforge_worker.spec"
$binDir = Join-Path $repoRoot "srtforge-studio\src-tauri\binaries"
$plainExe = Join-Path $binDir "srtforge_worker.exe"
$suffixedExe = Join-Path $binDir "srtforge_worker-x86_64-pc-windows-msvc.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project venv Python not found: $python"
}
if (-not (Test-Path -LiteralPath $spec)) {
    throw "PyInstaller spec not found: $spec"
}

if (-not $SkipStop) {
    $workers = @(Get-CimInstance Win32_Process -Filter "Name='srtforge_worker.exe'" -ErrorAction SilentlyContinue)
    foreach ($worker in $workers) {
        Write-Host "Stopping stale srtforge_worker.exe pid=$($worker.ProcessId)"
        Stop-Process -Id $worker.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

New-Item -ItemType Directory -Force -Path $binDir | Out-Null

Write-Host "Building Studio sidecar with PyInstaller..."
& $python -m PyInstaller --clean --noconfirm $spec --distpath $binDir
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

if (-not (Test-Path -LiteralPath $plainExe)) {
    throw "PyInstaller did not produce expected worker: $plainExe"
}

Copy-Item -LiteralPath $plainExe -Destination $suffixedExe -Force
Write-Host "Copied sidecar to $suffixedExe"

Write-Host "Running suffixed sidecar gpu-smoke..."
& $suffixedExe gpu-smoke
if ($LASTEXITCODE -ne 0) {
    throw "Sidecar gpu-smoke failed with exit code $LASTEXITCODE"
}

$mtime = (Get-Item -LiteralPath $suffixedExe).LastWriteTime
Write-Host "Studio sidecar is ready: $suffixedExe"
Write-Host "LastWriteTime: $mtime"
