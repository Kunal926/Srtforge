Param(
    [switch]$Cpu,
    [switch]$Gpu,
    [string]$PythonPath,
    [string]$PythonVersion,
    [ValidateSet('auto', '118', '121', '124', '126', '128', '130')]
    [string]$Cuda = 'auto'
)

$ErrorActionPreference = "Stop"

# --- Compatibility for Windows PowerShell 5.x (no $IsWindows automatic var) ---
if ($null -eq $IsWindows) {
    $IsWindows = [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows
    )
}

function Invoke-WithArgs {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Command,
        [Parameter(Mandatory = $true)]
        [string[]]$Args
    )

    $baseArgs = @()
    if ($Command.Length -gt 1) {
        $baseArgs = $Command[1..($Command.Length - 1)]
    }

    $global:LASTEXITCODE = 0
    $result = & $Command[0] @baseArgs @Args
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne $null -and $exitCode -ne 0) {
        $commandLine = [string]::Join(' ', @($Command + $Args))
        throw "Command '$commandLine' failed with exit code $exitCode."
    }

    return $result
}

function Invoke-CommandWithScript {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Command,
        [Parameter(Mandatory = $true)]
        [string]$ScriptContent
    )

    $tempBase = [System.IO.Path]::GetTempFileName()
    $tempScript = [System.IO.Path]::ChangeExtension($tempBase, '.py')
    Move-Item -Path $tempBase -Destination $tempScript -Force
    [System.IO.File]::WriteAllText($tempScript, $ScriptContent)

    try {
        Invoke-WithArgs -Command $Command -Args @($tempScript)
    }
    finally {
        Remove-Item -Path $tempScript -ErrorAction SilentlyContinue
        Remove-Item -Path $tempBase -ErrorAction SilentlyContinue
    }
}

function Test-CudartImport {
    param([string]$PythonExe)

    $probe = @'
try:
    import importlib
    import cuda.cudart  # legacy import expected by NeMo 2.5.x
    print("OK")
except Exception as e:
    print("FAIL:", type(e).__name__, str(e))
'@
    try {
        $out = Invoke-CommandWithScript -Command @($PythonExe) -ScriptContent $probe
        $txt = ($out | Out-String).Trim()
        return $txt -like 'OK*'
    }
    catch {
        return $false
    }
}

function Install-Cuda12Runtime {
    param([string]$PythonExe, [string]$PipExe)

    if (Test-CudartImport -PythonExe $PythonExe) {
        Write-Host 'cuda.cudart is already importable'
        return
    }

    Write-Host 'Installing CUDA 12 runtime (cudart) into the venv'
    $ok = $true
    try {
        Invoke-WithArgs -Command @($PipExe) -Args @(
            'install',
            '--extra-index-url','https://pypi.ngc.nvidia.com',
            'cuda-toolkit[cudart]==12.9.*'
        )
    }
    catch {
        $ok = $false
        Write-Warning "cuda-toolkit[cudart]==12.9.* installation failed, falling back to nvidia-cuda-runtime-cu12"
    }

    if (-not $ok) {
        try {
            Invoke-WithArgs -Command @($PipExe) -Args @(
                'install',
                '--extra-index-url','https://pypi.ngc.nvidia.com',
                'nvidia-cuda-runtime-cu12==12.9.*'
            )
            $ok = $true
        }
        catch {
            $ok = $false
        }
    }

    if ($ok) {
        if (Test-CudartImport -PythonExe $PythonExe) {
            Write-Host 'CUDA 12 runtime present: cuda.cudart import OK'
        } else {
            Write-Warning 'CUDA 12 runtime was installed but cuda.cudart still does not import. NeMo CUDA graphs may remain disabled.'
        }
    } else {
        Write-Warning 'Failed to install CUDA 12 runtime. NeMo CUDA graphs may remain disabled.'
    }
}

$pythonInfoScript = @'
import json
import pathlib
import sys

info = {
    "version": "%d.%d.%d" % sys.version_info[:3],
    "major": sys.version_info[0],
    "minor": sys.version_info[1],
    "micro": sys.version_info[2],
    "executable": str(pathlib.Path(sys.executable).resolve()),
}

print(json.dumps(info))
'@

function Get-PythonInfo {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Command
    )

    $tempBase = [System.IO.Path]::GetTempFileName()
    $tempScript = [System.IO.Path]::ChangeExtension($tempBase, '.py')
    Move-Item -Path $tempBase -Destination $tempScript -Force
    [System.IO.File]::WriteAllText($tempScript, $pythonInfoScript)

    try {
        # Call the candidate quietly (discard stderr) so missing versions don't spam the console
        $baseArgs = @()
        if ($Command.Length -gt 1) { $baseArgs = $Command[1..($Command.Length - 1)] }
        $global:LASTEXITCODE = 0
        $output = & $Command[0] @baseArgs @($tempScript) 2>$null
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne $null -and $exitCode -ne 0) { return $null }
        $json = ($output | Out-String).Trim()
        if (-not $json) {
            return $null
        }

        $data = $json | ConvertFrom-Json
        $version = [Version]$data.version
        $isCompatible = ($version.Major -eq 3 -and $version.Minor -ge 10 -and $version.Minor -le 12)

        return [pscustomobject]@{
            Command     = [string[]]$Command
            Version     = $version
            Display     = $data.version
            Executable  = $data.executable
            IsCompatible = $isCompatible
        }
    }
    catch {
        return $null
    }
    finally {
        Remove-Item -Path $tempScript -ErrorAction SilentlyContinue
        Remove-Item -Path $tempBase -ErrorAction SilentlyContinue
    }
}

if ($PythonPath) {
    $pythonCmd = @($PythonPath)
} elseif ($PythonVersion) {
    $pythonCmd = @("py", "-$PythonVersion")
} elseif ($env:PYTHON) {
    $pythonCmd = @($env:PYTHON)
} else {
    $pythonCmd = @("python")
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args
    )

    if ($pythonCmd.Length -gt 1) {
        & $pythonCmd[0] @($pythonCmd[1..($pythonCmd.Length - 1)]) @Args
    } else {
        & $pythonCmd[0] @Args
    }
}

function Resolve-PythonCommand {
    if ($PythonPath) {
        $info = Get-PythonInfo @($PythonPath)
        if (-not $info) {
            throw "Unable to use Python at '$PythonPath'. Ensure it is Python 3.10 through 3.12."
        }

        if (-not $info.IsCompatible) {
            throw "Python at '$PythonPath' reports version $($info.Display). Install Python 3.10 through 3.12 or choose a different interpreter with -PythonPath/-PythonVersion."
        }

        return $info
    }

    if ($PythonVersion) {
        $versionCandidates = @()
        $incompatible = @()
        if ($IsWindows -and (Get-Command py -ErrorAction SilentlyContinue)) {
            $versionCandidates += ,@('py', "-$PythonVersion")
        }
        $versionCandidates += ,@("python$PythonVersion")

        foreach ($candidate in $versionCandidates) {
            $info = Get-PythonInfo $candidate
            if ($info) {
                if (-not $info.IsCompatible) {
                    $incompatible += ,$info
                    continue
                }
                return $info
            }
        }

        if ($incompatible.Count -gt 0) {
            $details = ($incompatible | Select-Object -ExpandProperty Display -Unique) -join ', '
            throw "Found Python $details, but Srtforge supports versions 3.10 through 3.12. Install a compatible interpreter or point -PythonPath to one."
        }

        throw "Unable to locate Python $PythonVersion. Install a compatible version or use -PythonPath."
    }

    $candidateCommands = New-Object System.Collections.Generic.List[object]
    $seen = @{}

    $addCandidate = {
        param(
            [Parameter(Mandatory = $true)]
            [string[]]$Command
        )

        $key = [string]::Join('|', $Command)
        if (-not $seen.ContainsKey($key)) {
            $candidateCommands.Add($Command) | Out-Null
            $seen[$key] = $true
        }
    }

    if ($env:PYTHON) {
        & $addCandidate @($env:PYTHON)
    }

    if ($IsWindows) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            try {
                $pyList = & py -0p 2>$null
                foreach ($line in $pyList) {
                    if ($line -match '^\s*-(?<tag>[^\s]+)\s*(?<default>\*)?\s*(?<path>.+)$') {
                        $tag  = $Matches['tag']
                        $path = $Matches['path']
                        # Only add tags we actually support
                        if ($tag -match '^3\.(10|11|12)$') {
                            & $addCandidate @('py', "-$tag")
                        }
                        if ($path) {
                            & $addCandidate @($path.Trim())
                        }
                    }
                }
            }
            catch {
                # Ignore py launcher enumeration failures and fall back to manual guesses.
            }
        }

        foreach ($version in @('3.12', '3.11', '3.10')) {
            & $addCandidate @('py', "-$version")
        }

        & $addCandidate @('py')
    }

    foreach ($version in @('3.12', '3.11', '3.10')) {
        $trimmed = $version.Replace('.', '')
        & $addCandidate @("python$version")
        & $addCandidate @("python$trimmed")
    }

    $commandPatterns = if ($IsWindows) { @('python*.exe') } else { @('python*') }
    $windowsAppsDir  = if ($IsWindows) { (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps') } else { $null }

    foreach ($pattern in $commandPatterns) {
        $commands = Get-Command -Name $pattern -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandType -eq 'Application' -and $_.Source -and `
                (-not $windowsAppsDir -or -not $_.Source.StartsWith($windowsAppsDir, [StringComparison]::OrdinalIgnoreCase))
            }
        foreach ($command in $commands) {
            & $addCandidate @($command.Source)
        }
    }

    $candidates = @()
    $incompatible = @()
    foreach ($command in $candidateCommands) {
        $info = Get-PythonInfo $command
        if ($info) {
            if ($info.IsCompatible) {
                $candidates += ,$info
            } else {
                $incompatible += ,$info
            }
        }
    }

    if ($candidates.Count -eq 0) {
        if ($incompatible.Count -gt 0) {
            $uniqueIncompatible = $incompatible |
                Group-Object Display |
                ForEach-Object {
                    $paths = $_.Group |
                        Select-Object -ExpandProperty Executable |
                        Sort-Object -Unique |
                        ForEach-Object { "    $_" }
                    "Python $($_.Name):`n$([string]::Join("`n", $paths))"
                }
            $details = [string]::Join("`n", $uniqueIncompatible)
            $help = "Found Python installations, but their versions are not supported.`n$details`nSrtforge requires Python 3.10 through 3.12. Install a compatible release or pass -PythonPath/-PythonVersion to select one explicitly."
        } else {
            $help = "Unable to find a compatible Python interpreter. " +
                "Install Python 3.10 through 3.12 (for example via https://www.python.org/downloads/) " +
                "or pass -PythonPath/-PythonVersion. If Windows shows the 'Python was not found; run without arguments to install from the Microsoft Store' message, " +
                "install Python manually or disable the App execution alias before retrying."
        }

        throw $help
    }

    $unique = @(
        $candidates |
            Group-Object Executable |
            ForEach-Object { $_.Group | Sort-Object -Property Version -Descending | Select-Object -First 1 } |
            Sort-Object -Property @{ Expression = 'Version'; Descending = $true }, @{ Expression = 'Executable'; Descending = $false }
    )

    if ($unique.Count -eq 1) {
        return $unique[0]
    }

    if ($unique.Count -gt 1) {
        Write-Host "Multiple compatible Python interpreters detected:" -ForegroundColor Yellow
        for ($i = 0; $i -lt $unique.Count; $i++) {
            $entry = $unique[$i]
            $index = $i + 1
            Write-Host ("  [{0}] Python {1} - {2}" -f $index, $entry.Display, $entry.Executable)
        }

        while ($true) {
            $selection = Read-Host "Select interpreter [1-$($unique.Count)]"
            $parsed = 0
            if ([int]::TryParse($selection, [ref]$parsed)) {
                if ($parsed -ge 1 -and $parsed -le $unique.Count) {
                    return $unique[$parsed - 1]
                }
            }
            Write-Host "Invalid selection. Please enter a value between 1 and $($unique.Count)." -ForegroundColor Yellow
        }
    }

    throw "Unable to locate a compatible Python interpreter (3.10-3.12). Install one or pass -PythonPath/-PythonVersion."
}

$pythonSelection = Resolve-PythonCommand
$script:pythonCmd = $pythonSelection.Command

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args
    )

    Invoke-WithArgs -Command $script:pythonCmd -Args $Args
}

$pythonExe = $pythonSelection.Executable
$pythonVersion = $pythonSelection.Display
Write-Host "Using Python interpreter ($pythonVersion): $pythonExe"

$venvDir = ".venv"
if (-not (Test-Path $venvDir)) {
    Write-Host "Creating virtual environment in $venvDir"
    Invoke-Python @("-m", "venv", $venvDir)
}

$venvPython = Join-Path $venvDir "Scripts/python.exe"
$venvPip = Join-Path $venvDir "Scripts/pip.exe"

Invoke-WithArgs -Command @($venvPython) -Args @('-m', 'pip', 'install', '--upgrade', 'pip', 'wheel')
Invoke-WithArgs -Command @($venvPip) -Args @('install', '-r', 'requirements.txt')

Write-Host 'Installing PyInstaller so Windows bundles can be produced immediately'
Invoke-WithArgs -Command @($venvPip) -Args @('install', 'pyinstaller')

$global:ffmpegDownloadUrls = @(
    # GitHub-hosted nightly build maintained by BtbN. Stable URL that always
    # serves the most recent GPL-configured Win64 build with ffmpeg/ffprobe
    # binaries in the ./bin directory.
    'https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip',
    # Legacy mirror (gyan.dev). This site occasionally responds with 404s, so
    # keep it as a fallback instead of the primary source.
    'https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-6.1.1-essentials_build.zip'
)

function Ensure-Directory {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Ensure-FfmpegBinaries {
    $ffmpegBaseDir = Join-Path (Get-Location) 'packaging'
    $ffmpegBaseDir = Join-Path $ffmpegBaseDir 'windows'
    $ffmpegBaseDir = Join-Path $ffmpegBaseDir 'ffmpeg'
    $ffmpegBinDir = Join-Path $ffmpegBaseDir 'bin'

    Ensure-Directory $ffmpegBinDir

    $ffmpegExe = Join-Path $ffmpegBinDir 'ffmpeg.exe'
    $ffprobeExe = Join-Path $ffmpegBinDir 'ffprobe.exe'

    if ((Test-Path $ffmpegExe) -and (Test-Path $ffprobeExe)) {
        Write-Host "FFmpeg already present in $ffmpegBinDir"
        $env:SRTFORGE_FFMPEG_DIR = $ffmpegBinDir
        try {
            [Environment]::SetEnvironmentVariable('SRTFORGE_FFMPEG_DIR', $ffmpegBinDir, 'User')
        }
        catch {
            Write-Warning 'Unable to persist SRTFORGE_FFMPEG_DIR user environment variable.'
        }
        return
    }

    $tempBase = [System.IO.Path]::GetTempFileName()
    $tempZip = [System.IO.Path]::ChangeExtension($tempBase, '.zip')
    Move-Item -Path $tempBase -Destination $tempZip -Force

    Write-Host "Downloading FFmpeg build to support the GUI bundler"
    $downloaded = $false
    foreach ($url in $global:ffmpegDownloadUrls) {
        Write-Host "Attempting download from $url"
        try {
            Invoke-WebRequest -Uri $url -OutFile $tempZip -UseBasicParsing
            $downloaded = $true
            break
        }
        catch {
            Write-Warning "FFmpeg download failed from $url - $_"
        }
    }

    if (-not $downloaded) {
        throw 'Unable to download FFmpeg binaries from any configured mirror.'
    }

    $tempExtract = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
    Ensure-Directory $tempExtract
    Expand-Archive -LiteralPath $tempZip -DestinationPath $tempExtract -Force

    $expandedRoot = Get-ChildItem -Directory -Path $tempExtract | Select-Object -First 1
    if (-not $expandedRoot) {
        throw 'FFmpeg archive structure unexpected; bin directory not found.'
    }

    $sourceBin = Join-Path $expandedRoot.FullName 'bin'
    $sourceFfmpeg = Join-Path $sourceBin 'ffmpeg.exe'
    $sourceFfprobe = Join-Path $sourceBin 'ffprobe.exe'
    if (-not ((Test-Path $sourceFfmpeg) -and (Test-Path $sourceFfprobe))) {
        throw 'Downloaded FFmpeg package is missing ffmpeg.exe/ffprobe.exe.'
    }

    Copy-Item -Path $sourceFfmpeg -Destination $ffmpegExe -Force
    Copy-Item -Path $sourceFfprobe -Destination $ffprobeExe -Force

    Remove-Item -Path $tempZip -ErrorAction SilentlyContinue
    Remove-Item -Path $tempExtract -Recurse -Force -ErrorAction SilentlyContinue

    $env:SRTFORGE_FFMPEG_DIR = $ffmpegBinDir
    try {
        [Environment]::SetEnvironmentVariable('SRTFORGE_FFMPEG_DIR', $ffmpegBinDir, 'User')
    }
    catch {
        Write-Warning 'Unable to persist SRTFORGE_FFMPEG_DIR user environment variable.'
    }

    Write-Host "FFmpeg binaries downloaded to $ffmpegBinDir"
}

function Install-MKVToolNix {
  param(
    [string]$InstallRoot = (Join-Path $PSScriptRoot 'packaging\windows\mkvtoolnix')
  )

  # Already on PATH?
  $mkvmerge = Get-Command mkvmerge -ErrorAction SilentlyContinue
  if ($mkvmerge) {
    $dir = Split-Path -Parent $mkvmerge.Path
    [Environment]::SetEnvironmentVariable('SRTFORGE_MKV_DIR', $dir, 'User')
    Write-Host "MKVToolNix found at $dir"
    return
  }

  # Default Program Files location after winget/installer
  $pf = Join-Path ${env:ProgramFiles} 'MKVToolNix\mkvmerge.exe'
  if (Test-Path $pf) {
    [Environment]::SetEnvironmentVariable('SRTFORGE_MKV_DIR', (Split-Path -Parent $pf), 'User')
    Write-Host "MKVToolNix found at $((Split-Path -Parent $pf))"
    return
  }

  # Try winget first (most reliable unattended path)
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install --id MoritzBunkus.MKVToolNix -e --silent `
      --accept-package-agreements --accept-source-agreements
    if (Test-Path $pf) {
      [Environment]::SetEnvironmentVariable('SRTFORGE_MKV_DIR', (Split-Path -Parent $pf), 'User')
      Write-Host "MKVToolNix installed via winget."
      return
    }
  }

  # Portable fallback: download the latest x64 ZIP directly and unpack
  $downloads = Invoke-WebRequest -UseBasicParsing 'https://mkvtoolnix.download/downloads.html'
  $m = [regex]::Match($downloads.Content, 'current version v(?<ver>\d+\.\d+)')
  if (-not $m.Success) { throw "Unable to determine latest MKVToolNix version from downloads page." }
  $ver = $m.Groups['ver'].Value
  $zipUrl = "https://mkvtoolnix.download/windows/releases/$ver/mkvtoolnix-64-bit-$ver.zip"
  $tmpDir = Join-Path $env:TEMP "srtforge-mkvtoolnix-$ver"
  New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
  $zipPath = Join-Path $tmpDir "mkvtoolnix-$ver.zip"
  Invoke-WebRequest $zipUrl -OutFile $zipPath -UseBasicParsing
  $dstRoot = Join-Path $InstallRoot 'bin'
  New-Item -ItemType Directory -Force -Path $dstRoot | Out-Null
  Expand-Archive -Path $zipPath -DestinationPath $dstRoot -Force
  $exe = Get-ChildItem $dstRoot -Recurse -Filter mkvmerge.exe -File | Select-Object -First 1
  if ($exe -and ($exe.Directory.FullName -ne $dstRoot)) {
    Move-Item $exe.Directory.FullName\* $dstRoot -Force
  }
  [Environment]::SetEnvironmentVariable('SRTFORGE_MKV_DIR', $dstRoot, 'User')
  Write-Host "MKVToolNix extracted to $dstRoot"
}

Ensure-FfmpegBinaries
Install-MKVToolNix

$torchInfoScript = @'
import json

try:
    import torch
except Exception as exc:  # pragma: no cover - diagnostic helper
    print(json.dumps({
        "ok": False,
        "error": str(exc),
        "cuda_version": None,
        "cuda_available": False,
    }))
else:
    print(json.dumps({
        "ok": True,
        "error": None,
        "cuda_version": getattr(torch.version, "cuda", None),
        "cuda_available": bool(torch.cuda.is_available()),
    }))
'@

function Get-TorchCudaInfo {
    try {
        $result = Invoke-CommandWithScript -Command @($venvPython) -ScriptContent $torchInfoScript
        if (-not $result) {
            return $null
        }

        $json = ($result | Out-String).Trim()
        if (-not $json) {
            return $null
        }

        return $json | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Install-Torch($device) {
    if ($device -eq 'gpu') {
        $cudaTag = if ($Cuda -eq 'auto') { '130' } else { $Cuda }
        Write-Host "Installing Torch with CUDA $cudaTag wheels"
        $packages = @('torch', 'torchvision', 'torchaudio')
        $uninstallArgs = @('uninstall', '-y') + $packages
        Invoke-WithArgs -Command @($venvPip) -Args $uninstallArgs | Out-Null

        $installArgs = @(
            'install',
            '--upgrade',
            '--force-reinstall',
            '--no-cache-dir',
            '--index-url', "https://download.pytorch.org/whl/cu$cudaTag",
            '--extra-index-url', 'https://pypi.org/simple'
        ) + $packages
        Invoke-WithArgs -Command @($venvPip) -Args $installArgs

        $torchInfo = Get-TorchCudaInfo
        if ($torchInfo -and $torchInfo.ok) {
            if (-not $torchInfo.cuda_version) {
                Write-Warning 'PyTorch CUDA runtime was not detected after installation. CPU-only wheels may still be in use.'
            } elseif (-not $torchInfo.cuda_available) {
                Write-Warning "PyTorch reports CUDA $($torchInfo.cuda_version) but no GPU is currently available. Check your NVIDIA drivers."
            } else {
                Write-Host "Detected CUDA-enabled PyTorch (CUDA $($torchInfo.cuda_version))."
            }
        } else {
            Write-Warning 'Unable to verify the CUDA-enabled PyTorch installation.'
        }
    } else {
        Write-Host "Installing Torch CPU wheels"
        Invoke-WithArgs -Command @($venvPip) -Args @(
            'install',
            '--index-url', 'https://download.pytorch.org/whl/cpu',
            'torch', 'torchvision', 'torchaudio'
        )
    }
}

function Install-OnnxRuntime($device) {
    if ($device -eq 'gpu') {
        Write-Host "Installing ONNX Runtime GPU package"
        try {
            Invoke-WithArgs -Command @($venvPip) -Args @('install', 'onnxruntime-gpu>=1.23.2')
            return $true
        }
        catch {
            Write-Warning "Failed to install onnxruntime-gpu. Ensure a compatible NVIDIA driver is available if you expect GPU vocal separation. Falling back to the CPU build."
            Invoke-WithArgs -Command @($venvPip) -Args @('install', 'onnxruntime>=1.23.2')
            return $false
        }
    } else {
        Write-Host "Installing ONNX Runtime CPU package"
        Invoke-WithArgs -Command @($venvPip) -Args @('install', 'onnxruntime>=1.23.2')
        return $true
    }
}

function Test-CudaToolkitInstalled {
    $cudaPath = $env:CUDA_PATH
    if ($cudaPath -and (Test-Path $cudaPath)) {
        return $true
    }

    $defaultCudaRoot = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA'
    if (Test-Path $defaultCudaRoot) {
        return $true
    }

    if (Get-Command nvcc.exe -ErrorAction SilentlyContinue) {
        return $true
    }

    return $false
}

function Ensure-CudaToolkit {
    param (
        [switch] $Silent
    )

    if (Test-CudaToolkitInstalled) {
        Write-Host 'CUDA toolkit already present'
        return $true
    }

    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        Write-Warning 'winget not available. Install NVIDIA CUDA manually from https://developer.nvidia.com/cuda-downloads'
        return $false
    }

    Write-Host 'Installing NVIDIA CUDA toolkit via winget'
    try {
        $arguments = @(
            'install',
            '--id', 'NVIDIA.CUDA',
            '-e',
            '--accept-package-agreements',
            '--accept-source-agreements'
        )
        if ($Silent) {
            $arguments += '--silent'
        }

        Invoke-WithArgs -Command @('winget.exe') -Args $arguments
        return $true
    }
    catch {
        Write-Warning "Failed to install CUDA toolkit automatically. Install it manually from NVIDIA's website."
        return $false
    }
}

$selectedDevice = 'cpu'
if ($Cpu) {
    $selectedDevice = 'cpu'
} elseif ($Gpu) {
    $selectedDevice = 'gpu'
} else {
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $selectedDevice = 'gpu'
    } else {
        Write-Host "No NVIDIA GPU detected, falling back to CPU wheels"
        $selectedDevice = 'cpu'
    }
}

if ($selectedDevice -eq 'gpu') {
    if (-not (Ensure-CudaToolkit)) {
        Write-Warning 'Continuing with GPU Python packages, but CUDA toolkit installation may be incomplete.'
    }
}

Install-Torch $selectedDevice
$onnxGpuInstalled = Install-OnnxRuntime $selectedDevice
if (-not $onnxGpuInstalled -and $selectedDevice -eq 'gpu') {
    Write-Warning "Vocal separation is falling back to the CPU build of ONNX Runtime. Re-run the installer after fixing your CUDA driver to re-enable GPU separation."
}

Write-Host 'Installing cuda-python==12.9.* for NeMo CUDA Graphs compatibility'
Invoke-WithArgs -Command @($venvPython) -Args @('-m', 'pip', 'install', 'cuda-python==12.9.*')

# Ensure cudart64_12*.dll is available inside the venv for cuda-python 12.9
Install-Cuda12Runtime -PythonExe $venvPython -PipExe $venvPip

$nemoRequirement = 'nemo_toolkit[asr]~=2.5.1'
Invoke-WithArgs -Command @($venvPython) -Args @(
    '-m','pip','install',$nemoRequirement
)

# --- Build the NeMo verification script via Base64 (exact Python content from your comments) ---
$verifyNeMoScriptB64 = @'
aW1wb3J0IGltcG9ydGxpYiwgc2lnbmFsLCBzeXMKaWYgbm90IGhhc2F0dHIoc2lnbmFsLCAiU0lHS0lM
TCIpOgogICAgc2V0YXR0cihzaWduYWwsICJTSUdLSUxMIiwgZ2V0YXR0cihzaWduYWwsICJTSUdURVJN
IiwgZ2V0YXR0cihzaWduYWwsICJTSUdBQlJUIiwgOSkpKQp0cnk6CiAgICBpbXBvcnRsaWIuaW1wb3J0
X21vZHVsZSgibmVtby5jb2xsZWN0aW9ucy5hc3IiKQpleGNlcHQgRXhjZXB0aW9uIGFzIGV4YzoKICAg
IHByaW50KCJFUlJPUjogTlZJRElBIE5lTW8gQVNSIGNvbXBvbmVudHMgZmFpbGVkIHRvIGltcG9ydCBh
ZnRlciBpbnN0YWxsYXRpb24uIFRoaXMgdXN1YWxseSBtZWFucyBvbmUgb2YgaXRzIGRlcGVuZGVuY2ll
cyAoc3VjaCBhcyBudW1weSwgcHlhcnJvdyBvciBtYXRwbG90bGliKSB3YXMgbm90IGluc3RhbGxlZCBj
b3JyZWN0bHkuIiwgZmlsZT1zeXMuc3RkZXJyKQogICAgcHJpbnQoZiIgICAgICAgT3JpZ2luYWwgaW1w
b3J0IGVycm9yOiB7ZXhjfSIsIGZpbGU9c3lzLnN0ZGVycikKICAgIHN5cy5leGl0KDEpCmVsc2U6CiAg
ICBwcmludCgiVmVyaWZpZWQgTlZJRElBIE5lTW8gQVNSIG1vZHVsZXMgYXJlIGltcG9ydGFibGUuIikK
'@

$verifyNeMoScript = [System.Text.Encoding]::UTF8.GetString(
    [System.Convert]::FromBase64String($verifyNeMoScriptB64)
)

Invoke-CommandWithScript -Command @($venvPython) -ScriptContent $verifyNeMoScript

# Install the local package in editable mode
Invoke-WithArgs -Command @($venvPip) -Args @('install', '-e', '.')

# ----------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------
$modelsDir = Join-Path (Get-Location) 'models'
if (-not (Test-Path $modelsDir)) {
    New-Item -ItemType Directory -Path $modelsDir | Out-Null
}

$downloads = @(
    @{ Url = 'https://github.com/StiensGate928/Srtforge/releases/download/v1.0.0/voc_fv4.ckpt'; File = 'voc_fv4.ckpt' },
    @{ Url = 'https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2/resolve/main/parakeet-tdt-0.6b-v2.nemo?download=1'; File = 'parakeet-tdt-0.6b-v2.nemo' }
)

function Download-Model([hashtable]$item) {
    $target = Join-Path $modelsDir $item.File
    if (Test-Path $target -PathType Leaf) {
        $existingFile = Get-Item $target -ErrorAction SilentlyContinue
        if ($existingFile -and $existingFile.Length -gt 0) {
            Write-Host "$($item.File) already present"
            return
        }
    }

    $headers = @{}
    if ($env:HF_TOKEN) {
        $headers['Authorization'] = "Bearer $($env:HF_TOKEN)"
    }

    Write-Host "Downloading $($item.File)"
    try {
        Invoke-WebRequest -Uri $item.Url -Headers $headers -OutFile $target -UseBasicParsing
    }
    catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode.Value__ -eq 401) {
            Write-Error "Authorization required for $($item.Url). Set HF_TOKEN with a valid Hugging Face token."
        }
        throw
    }
}

foreach ($item in $downloads) {
    Download-Model $item
}

# Final message - NOTE: no extra quote at the end of this line
Write-Host "Installation complete. Activate the virtual environment with '.\.venv\Scripts\Activate.ps1'."
