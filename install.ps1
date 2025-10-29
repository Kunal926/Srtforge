Param(
    [switch]$Cpu,
    [switch]$Gpu,
    [string]$PythonPath,
    [string]$PythonVersion,
    [ValidateSet('auto', '118', '121', '124', '126', '128', '130')]
    [string]$Cuda = 'auto'
)

$ErrorActionPreference = "Stop"

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

    & $Command[0] @baseArgs @Args
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
        $output = Invoke-WithArgs -Command $Command -Args @($tempScript)
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
    } catch {
        return $null
    } finally {
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
                        $tag = $Matches['tag']
                        $path = $Matches['path']
                        & $addCandidate @('py', "-$tag")
                        if ($path) {
                            & $addCandidate @($path.Trim())
                        }
                    }
                }
            } catch {
                # Ignore py launcher enumeration failures and fall back to manual guesses.
            }
        }

        foreach ($version in @('3.12', '3.11', '3.10')) {
            & $addCandidate @('py', "-$version")
        }

        & $addCandidate @('py')
    }

    & $addCandidate @('python3')
    & $addCandidate @('python')

    foreach ($version in @('3.12', '3.11', '3.10')) {
        $trimmed = $version.Replace('.', '')
        & $addCandidate @("python$version")
        & $addCandidate @("python$trimmed")
    }

    $commandPatterns = if ($IsWindows) { @('python*.exe') } else { @('python*') }
    foreach ($pattern in $commandPatterns) {
        $commands = Get-Command -Name $pattern -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandType -eq 'Application' -and $_.Source }
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

    $unique = $candidates |
        Group-Object Executable |
        ForEach-Object { $_.Group | Sort-Object -Property Version -Descending | Select-Object -First 1 } |
        Sort-Object -Property @{ Expression = 'Version'; Descending = $true }, @{ Expression = 'Executable'; Descending = $false }

    if ($unique.Count -eq 1) {
        return $unique[0]
    }

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

& $venvPython -m pip install --upgrade pip wheel
& $venvPip install -r requirements.txt

function Install-Torch($device) {
    if ($device -eq 'gpu') {
        $cudaTag = if ($Cuda -eq 'auto') { '124' } else { $Cuda }
        Write-Host "Installing Torch with CUDA $cudaTag wheels"
        & $venvPip install --extra-index-url "https://download.pytorch.org/whl/cu$cudaTag" torch torchvision torchaudio
    } else {
        Write-Host "Installing Torch CPU wheels"
        & $venvPip install --extra-index-url https://download.pytorch.org/whl/cpu torch torchvision torchaudio
    }
}

if ($Cpu) {
    Install-Torch 'cpu'
} elseif ($Gpu) {
    Install-Torch 'gpu'
} else {
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        Install-Torch 'gpu'
    } else {
        Write-Host "No NVIDIA GPU detected, falling back to CPU wheels"
        Install-Torch 'cpu'
    }
}

& $venvPip install nemo_toolkit[asr]==2.0.0

& $venvPip install -e .

$modelsDir = Join-Path (Get-Location) 'models'
if (-not (Test-Path $modelsDir)) {
    New-Item -ItemType Directory -Path $modelsDir | Out-Null
}

$downloads = @(
    @{ Url = 'https://huggingface.co/audio-separator/melband-roformer-fv4/resolve/main/voc_fv4.ckpt?download=1'; File = 'voc_fv4.ckpt' },
    @{ Url = 'https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2/resolve/main/parakeet_tdt_0.6b_v2.nemo?download=1'; File = 'parakeet_tdt_0.6b_v2.nemo' }
)

function Download-Model($item) {
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
    } catch {
        if ($_.Exception.Response.StatusCode.Value__ -eq 401) {
            Write-Error "Authorization required for $($item.Url). Set HF_TOKEN with a valid Hugging Face token."
        }
        throw
    }
}

foreach ($item in $downloads) {
    Download-Model $item
}

Write-Host 'Installation complete. Activate the virtual environment with ''.\.venv\Scripts\Activate.ps1''.'
