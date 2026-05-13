param(
    [switch]$SkipStop
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$spec = Join-Path $repoRoot "srtforge-studio\packaging\windows\srtforge_worker.spec"
$binDir = Join-Path $repoRoot "srtforge-studio\src-tauri\binaries"
$sidecarDir = Join-Path $binDir "srtforge_worker"
$oneDirExe = Join-Path $sidecarDir "srtforge_worker.exe"
$sidecarPlaceholder = Join-Path $sidecarDir ".gitkeep"
$plainExe = Join-Path $binDir "srtforge_worker.exe"
$suffixedExe = Join-Path $binDir "srtforge_worker-x86_64-pc-windows-msvc.exe"
$startupMaxSeconds = 15.0

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
$resolvedBinDir = [System.IO.Path]::GetFullPath($binDir).TrimEnd('\', '/')

function Assert-UnderBinDir {
    param([string]$Path)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not ($fullPath.StartsWith($resolvedBinDir + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase))) {
        throw "Refusing to clean path outside Studio binaries directory: $fullPath"
    }
}

foreach ($oldPath in @($plainExe, $suffixedExe, $sidecarDir)) {
    Assert-UnderBinDir -Path $oldPath
    if (Test-Path -LiteralPath $oldPath) {
        Write-Host "Removing stale sidecar output: $oldPath"
        Remove-Item -LiteralPath $oldPath -Recurse -Force
    }
}

function Ensure-SidecarPlaceholder {
    New-Item -ItemType Directory -Force -Path $sidecarDir | Out-Null
    if (-not (Test-Path -LiteralPath $sidecarPlaceholder)) {
        New-Item -ItemType File -Path $sidecarPlaceholder | Out-Null
    }
}

try {
    Write-Host "Building Studio sidecar one-dir with PyInstaller..."
    & $python -m PyInstaller --clean --noconfirm $spec --distpath $binDir
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    if (-not (Test-Path -LiteralPath $oneDirExe)) {
        throw "PyInstaller did not produce expected worker: $oneDirExe"
    }
    Ensure-SidecarPlaceholder

    Write-Host "Running one-dir sidecar gpu-smoke..."
    & $oneDirExe gpu-smoke
    if ($LASTEXITCODE -ne 0) {
        throw "Sidecar gpu-smoke failed with exit code $LASTEXITCODE"
    }

    Write-Host "Measuring one-dir sidecar worker startup..."
    $startupTimer = [System.Diagnostics.Stopwatch]::StartNew()
    $startupOutput = @() | & $oneDirExe worker --no-preload 2>&1
    $startupExitCode = $LASTEXITCODE
    $startupTimer.Stop()
    $startupSeconds = [Math]::Round($startupTimer.Elapsed.TotalSeconds, 3)
    foreach ($line in $startupOutput) {
        Write-Host "  $line"
    }
    if ($startupExitCode -ne 0) {
        throw "Sidecar worker startup smoke failed with exit code $startupExitCode"
    }
    if (-not (($startupOutput | Out-String) -match '"event"\s*:\s*"worker_ready"')) {
        throw "Sidecar worker startup smoke did not emit worker_ready"
    }
    if ($startupTimer.Elapsed.TotalSeconds -gt $startupMaxSeconds) {
        throw "Sidecar worker startup took $startupSeconds seconds, above ${startupMaxSeconds}s threshold; this still looks like one-file startup behavior"
    }
    Write-Host "Sidecar worker startup: ${startupSeconds}s"

    $mtime = (Get-Item -LiteralPath $oneDirExe).LastWriteTime
    Write-Host "Studio sidecar is ready: $oneDirExe"
    Write-Host "LastWriteTime: $mtime"
}
finally {
    Ensure-SidecarPlaceholder
}
