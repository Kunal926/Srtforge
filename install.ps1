Param(
    [switch]$Cpu,
    [switch]$Gpu,
    [switch]$ShowProgress,
    [string]$PythonPath,
    [string]$PythonVersion,
    [ValidateSet('auto', '118', '121', '124', '126', '128', '130')]
    [string]$Cuda = 'auto'
)

$ErrorActionPreference = "Stop"

# --- Performance defaults ------------------------------------------------
# Windows PowerShell's progress rendering (Write-Progress) can dramatically
# slow down large downloads performed by Invoke-WebRequest. Disable it by
# default (users can opt back in with -ShowProgress).
$script:OriginalProgressPreference = $ProgressPreference
if (-not $ShowProgress) {
    $ProgressPreference = 'SilentlyContinue'
}

# --- Timing helpers ----------------------------------------------------
$script:ScriptStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$script:StepTimers = @{}
$script:StepTimings = @()

function Format-ElapsedTime {
    param(
        [Parameter(Mandatory = $true)]
        [TimeSpan]$Elapsed
    )

    return $Elapsed.ToString("hh\:mm\:ss\.ff")
}

function Start-StepTimer {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $script:StepTimers[$Name] = [System.Diagnostics.Stopwatch]::StartNew()
}

function Stop-StepTimer {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [string]$Status = 'OK'
    )

    $stopwatch = $script:StepTimers[$Name]
    if ($null -ne $stopwatch) {
        $stopwatch.Stop()
        $duration = Format-ElapsedTime -Elapsed $stopwatch.Elapsed
    } else {
        $duration = Format-ElapsedTime -Elapsed ([TimeSpan]::Zero)
    }

    $script:StepTimings += [pscustomobject]@{
        Step     = $Name
        Duration = $duration
        Status   = $Status
    }
}

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
    $commandLine = [string]::Join(' ', @($Command + $Args))
    $capturedOutput = @()
    $result = & $Command[0] @baseArgs @Args 2>&1 | Tee-Object -Variable capturedOutput
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne $null -and $exitCode -ne 0) {
        $message = "Command '$commandLine' failed with exit code $exitCode."
        if ($capturedOutput.Count -gt 0) {
            $message += "`nOutput:`n$([string]::Join("`n", $capturedOutput))"
        }
        throw $message
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
Start-StepTimer -Name "Virtualenv creation"
$venvStepSucceeded = $true
$venvCreated = $false
try {
    if (-not (Test-Path $venvDir)) {
        Write-Host "Creating virtual environment in $venvDir"
        Invoke-Python @("-m", "venv", $venvDir)
        $venvCreated = $true
    } else {
        Write-Host "Virtual environment already present in $venvDir"
    }
}
catch {
    $venvStepSucceeded = $false
    throw
}
finally {
    $venvStatus = if ($venvStepSucceeded) { if ($venvCreated) { 'OK' } else { 'Skipped' } } else { 'Failed' }
    Stop-StepTimer -Name "Virtualenv creation" -Status $venvStatus
}

$venvPython = Join-Path $venvDir "Scripts/python.exe"
$venvPip = Join-Path $venvDir "Scripts/pip.exe"

# pip command-line defaults: keep installs non-interactive, quiet, and fast.
$script:PipGlobalArgs = @(
    '--disable-pip-version-check',
    '--no-input'
)

function Invoke-Pip {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args
    )

    Invoke-WithArgs -Command @($venvPip) -Args ($script:PipGlobalArgs + $Args)
}

function Test-PipPackageInstalled {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    try {
        Invoke-WithArgs -Command @($venvPip) -Args @('--disable-pip-version-check', 'show', $Name) | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Get-RequirementsPackages {
    $requirementsPath = Join-Path (Get-Location) 'requirements.txt'
    if (-not (Test-Path $requirementsPath)) {
        return @()
    }

    $packages = @()
    foreach ($line in Get-Content $requirementsPath) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) {
            continue
        }

        if ($trimmed -match '^(?<name>[A-Za-z0-9_.-]+)') {
            $packages += $Matches['name']
        }
    }

    return $packages | Sort-Object -Unique
}

function Ensure-RequirementsInstalled {
    $requirements = Get-RequirementsPackages
    if ($requirements.Count -eq 0) {
        return
    }

    $missing = @()
    foreach ($package in $requirements) {
        if (-not (Test-PipPackageInstalled -Name $package)) {
            $missing += $package
        }
    }

    if ($missing.Count -eq 0) {
        Write-Host 'Requirements already installed. Skipping reinstall.'
        return
    }

    Write-Host "Installing missing requirements: $([string]::Join(', ', $missing))"
    Invoke-Pip -Args @(
        'install',
        '--progress-bar', 'off',
        '--upgrade-strategy', 'only-if-needed',
        '--prefer-binary',
        '-r', 'requirements.txt'
    )
}

Start-StepTimer -Name "pip/wheel bootstrap"
$pipBootstrapSucceeded = $true
try {
    if (-not (Test-PipPackageInstalled -Name 'pip') -or -not (Test-PipPackageInstalled -Name 'wheel')) {
        Invoke-WithArgs -Command @($venvPython) -Args @(
            '-m', 'pip',
            '--disable-pip-version-check',
            '--no-input',
            'install',
            '--progress-bar', 'off',
            '--upgrade',
            'pip', 'wheel'
        )
    } else {
        Write-Host 'pip and wheel already installed. Skipping upgrade.'
    }
}
catch {
    $pipBootstrapSucceeded = $false
    throw
}
finally {
    $pipBootstrapStatus = if ($pipBootstrapSucceeded) { 'OK' } else { 'Failed' }
    Stop-StepTimer -Name "pip/wheel bootstrap" -Status $pipBootstrapStatus
}

# Decide on CPU vs GPU early so that we can preinstall the correct PyTorch build
# before resolving requirements (audio-separator would otherwise pull in the CPU
# torch wheel first, then we'd replace it later).
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

$script:TorchInstalledBeforeRequirements = $false
if ($selectedDevice -eq 'gpu') {
    Start-StepTimer -Name "Install Torch"
    $torchStepSucceeded = $true
    try {
        Install-Torch $selectedDevice
        $script:TorchInstalledBeforeRequirements = $true
    }
    catch {
        $torchStepSucceeded = $false
        throw
    }
    finally {
        $torchStatus = if ($torchStepSucceeded) { 'OK' } else { 'Failed' }
        Stop-StepTimer -Name "Install Torch" -Status $torchStatus
    }
}



Start-StepTimer -Name "Install requirements"
$requirementsStepSucceeded = $true
try {
    Ensure-RequirementsInstalled
}
catch {
    $requirementsStepSucceeded = $false
    throw
}
finally {
    $requirementsStatus = if ($requirementsStepSucceeded) { 'OK' } else { 'Failed' }
    Stop-StepTimer -Name "Install requirements" -Status $requirementsStatus
}

Start-StepTimer -Name "PyInstaller install"
$pyInstallerStepSucceeded = $true
try {
    if (-not (Test-PipPackageInstalled -Name 'pyinstaller')) {
        Write-Host 'Installing PyInstaller so Windows bundles can be produced immediately'
        Invoke-WithArgs -Command @($venvPip) -Args @('install', 'pyinstaller')
    } else {
        Write-Host 'PyInstaller already installed. Skipping reinstall.'
    }
}
catch {
    $pyInstallerStepSucceeded = $false
    throw
}
finally {
    $pyInstallerStatus = if ($pyInstallerStepSucceeded) { 'OK' } else { 'Failed' }
    Stop-StepTimer -Name "PyInstaller install" -Status $pyInstallerStatus
}

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

Start-StepTimer -Name "Ensure FFmpeg binaries"
$ffmpegStepSucceeded = $true
try {
    Ensure-FfmpegBinaries
}
catch {
    $ffmpegStepSucceeded = $false
    throw
}
finally {
    $ffmpegStatus = if ($ffmpegStepSucceeded) { 'OK' } else { 'Failed' }
    Stop-StepTimer -Name "Ensure FFmpeg binaries" -Status $ffmpegStatus
}

Start-StepTimer -Name "Install MKVToolNix"
$mkvStepSucceeded = $true
try {
    Install-MKVToolNix
}
catch {
    $mkvStepSucceeded = $false
    throw
}
finally {
    $mkvStatus = if ($mkvStepSucceeded) { 'OK' } else { 'Failed' }
    Stop-StepTimer -Name "Install MKVToolNix" -Status $mkvStatus
}

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
    $torchInstalled = Test-PipPackageInstalled -Name 'torch'
    $torchInfo = if ($torchInstalled) { Get-TorchCudaInfo } else { $null }

    if ($device -eq 'gpu') {
        if ($torchInstalled -and $torchInfo -and $torchInfo.ok -and $torchInfo.cuda_version) {
            Write-Host "CUDA-enabled PyTorch already installed (CUDA $($torchInfo.cuda_version)). Skipping reinstall."
            return
        }

        $cudaTag = if ($Cuda -eq 'auto') { '130' } else { $Cuda }
        Write-Host "Installing Torch with CUDA $cudaTag wheels"
        $packages = @('torch', 'torchvision', 'torchaudio')

        $installArgs = @(
            'install',
            '--upgrade',
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
        if ($torchInstalled -and $torchInfo -and $torchInfo.ok -and -not $torchInfo.cuda_version) {
            Write-Host 'CPU-only PyTorch already installed. Skipping reinstall.'
            return
        }

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
        if (Test-PipPackageInstalled -Name 'onnxruntime-gpu') {
            Write-Host 'ONNX Runtime GPU package already installed. Skipping reinstall.'
            return $true
        }

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
        if (Test-PipPackageInstalled -Name 'onnxruntime') {
            Write-Host 'ONNX Runtime CPU package already installed. Skipping reinstall.'
            return $true
        }

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

# Device selection is performed earlier (before requirements are installed) so
# that GPU installs can pull the CUDA-enabled PyTorch wheels up front.

if ($selectedDevice -eq 'gpu') {
    Start-StepTimer -Name "Ensure CUDA toolkit"
    $cudaStepSucceeded = $true
    try {
        if (-not (Ensure-CudaToolkit)) {
            Write-Warning 'Continuing with GPU Python packages, but CUDA toolkit installation may be incomplete.'
        }
    }
    catch {
        $cudaStepSucceeded = $false
        throw
    }
    finally {
        $cudaStatus = if ($cudaStepSucceeded) { 'OK' } else { 'Failed' }
        Stop-StepTimer -Name "Ensure CUDA toolkit" -Status $cudaStatus
    }
}

# If Torch was already installed before requirements (GPU path), don't reinstall here.
if (-not $script:TorchInstalledBeforeRequirements) {
    Start-StepTimer -Name "Install Torch"
    $torchStepSucceeded = $true
    try {
        Install-Torch $selectedDevice
    }
    catch {
        $torchStepSucceeded = $false
        throw
    }
    finally {
        $torchStatus = if ($torchStepSucceeded) { 'OK' } else { 'Failed' }
        Stop-StepTimer -Name "Install Torch" -Status $torchStatus
    }
}

Start-StepTimer -Name "Install ONNX Runtime"
$onnxStepSucceeded = $true
try {
    $onnxGpuInstalled = Install-OnnxRuntime $selectedDevice
}
catch {
    $onnxStepSucceeded = $false
    throw
}
finally {
    $onnxStatus = if ($onnxStepSucceeded) { 'OK' } else { 'Failed' }
    Stop-StepTimer -Name "Install ONNX Runtime" -Status $onnxStatus
}
if (-not $onnxGpuInstalled -and $selectedDevice -eq 'gpu') {
    Write-Warning "Vocal separation is falling back to the CPU build of ONNX Runtime. Re-run the installer after fixing your CUDA driver to re-enable GPU separation."
}

# Install the local package in editable mode
Start-StepTimer -Name "Editable install and import verification"
$editableStepSucceeded = $true
try {
    Invoke-WithArgs -Command @($venvPip) -Args @('install', '-e', '.')

    $importCheckScript = @'
import importlib
import sys

try:
    importlib.import_module("srtforge")
except Exception as exc:
    print(f"IMPORT_ERROR: {exc}")
    sys.exit(1)
else:
    print("✔ Verified srtforge is importable.")
'@

    try {
        Invoke-CommandWithScript -Command @($venvPython) -ScriptContent $importCheckScript
    }
    catch {
        Write-Warning "Editable install could not import srtforge. Falling back to a legacy editable install."
        try {
            Invoke-WithArgs -Command @($venvPip) -Args @('install', '-e', '.', '--config-settings', 'editable_mode=compat')
        }
        catch {
            Write-Warning "Legacy editable install failed. Installing a non-editable build instead."
            Invoke-WithArgs -Command @($venvPip) -Args @('install', '.')
        }

        try {
            Invoke-CommandWithScript -Command @($venvPython) -ScriptContent $importCheckScript
        }
        catch {
            Write-Warning "Legacy editable install still could not import srtforge. Installing a non-editable build instead."
            Invoke-WithArgs -Command @($venvPip) -Args @('install', '.')
            Invoke-CommandWithScript -Command @($venvPython) -ScriptContent $importCheckScript
        }
    }
}
catch {
    $editableStepSucceeded = $false
    throw
}
finally {
    $editableStatus = if ($editableStepSucceeded) { 'OK' } else { 'Failed' }
    Stop-StepTimer -Name "Editable install and import verification" -Status $editableStatus
}

# ----------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------
$modelsDir = Join-Path (Get-Location) 'models'
if (-not (Test-Path $modelsDir)) {
    New-Item -ItemType Directory -Path $modelsDir | Out-Null
}

$downloads = @(
    @{ Url = 'https://github.com/StiensGate928/Srtforge/releases/download/v1.0.0/voc_fv4.ckpt'; File = 'voc_fv4.ckpt' }
    @{ Url = 'https://github.com/StiensGate928/Srtforge/releases/download/v1.0.0/voc_gabox.yaml'; File = 'voc_gabox.yaml' }
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

Start-StepTimer -Name "Model downloads"
$modelStepSucceeded = $true
try {
    foreach ($item in $downloads) {
        Download-Model $item
    }
}
catch {
    $modelStepSucceeded = $false
    throw
}
finally {
    $modelStatus = if ($modelStepSucceeded) { 'OK' } else { 'Failed' }
    Stop-StepTimer -Name "Model downloads" -Status $modelStatus
}

# Timing summary
$script:ScriptStopwatch.Stop()
Write-Host ""
Write-Host "Installation timing summary:"
$script:StepTimings | Format-Table -AutoSize
$totalElapsed = Format-ElapsedTime -Elapsed $script:ScriptStopwatch.Elapsed
Write-Host ("Total elapsed time: {0}" -f $totalElapsed)

# Restore caller progress preference (matters if the script is dot-sourced)
if ($null -ne $script:OriginalProgressPreference) {
    $ProgressPreference = $script:OriginalProgressPreference
}

# Final message - NOTE: no extra quote at the end of this line
Write-Host "Installation complete. Activate the virtual environment with '.\.venv\Scripts\Activate.ps1'."
