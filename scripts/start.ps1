$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Fallen is not installed yet. Running the installer..."
    & "$PSScriptRoot\install.ps1"
}

& ".venv\Scripts\python.exe" -m fallen_helper @args
