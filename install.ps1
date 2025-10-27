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

    try {
        $output = Invoke-WithArgs -Command $Command -Args @('-c', $pythonInfoScript)
        $json = ($output | Out-String).Trim()
        if (-not $json) {
            return $null
        }

        $data = $json | ConvertFrom-Json
        $version = [Version]$data.version
        if ($version.Major -ne 3 -or $version.Minor -lt 10 -or $version.Minor -gt 12) {
            return $null
        }

        return [pscustomobject]@{
            Command   = [string[]]$Command
            Version   = $version
            Display   = $data.version
            Executable = $data.executable
        }
    } catch {
        return $null
    }
}

function Resolve-PythonCommand {
    if ($PythonPath) {
        $info = Get-PythonInfo @($PythonPath)
        if (-not $info) {
            throw "Unable to use Python at '$PythonPath'. Ensure it is Python 3.10 through 3.12."
        }

        return $info
    }

    if ($PythonVersion) {
        $versionCandidates = @()
        if ($IsWindows -and (Get-Command py -ErrorAction SilentlyContinue)) {
            $versionCandidates += ,@('py', "-$PythonVersion")
        }
        $versionCandidates += ,@("python$PythonVersion")

        foreach ($candidate in $versionCandidates) {
            $info = Get-PythonInfo $candidate
            if ($info) {
                return $info
            }
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

    $candidates = @()
    foreach ($command in $candidateCommands) {
        $info = Get-PythonInfo $command
        if ($info) {
            $candidates += ,$info
        }
    }

    if ($candidates.Count -eq 0) {
        throw "Unable to find a compatible Python interpreter. Install Python 3.10 through 3.12 or pass -PythonPath/-PythonVersion."
    }

    $unique = $candidates |
        Group-Object Executable |
        ForEach-Object { $_.Group | Sort-Object Version -Descending | Select-Object -First 1 } |
        Sort-Object Version -Descending, Executable

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
    if (Test-Path $target -PathType Leaf -and (Get-Item $target).Length -gt 0) {
        Write-Host "$($item.File) already present"
        return
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
