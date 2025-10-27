Param(
    [switch]$Cpu,
    [switch]$Gpu,
    [string]$PythonPath,
    [string]$PythonVersion,
    [ValidateSet('auto', '118', '121', '124', '126', '128', '130')]
    [string]$Cuda = 'auto'
)

$ErrorActionPreference = "Stop"

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
