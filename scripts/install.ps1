$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = "py"
    $PythonArgs = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = "python"
    $PythonArgs = @()
} else {
    throw "Python 3.10 or newer is required: https://www.python.org/downloads/"
}

& $Python @PythonArgs -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.10 or newer is required."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating local Python environment..."
    & $Python @PythonArgs -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -e .

Write-Host "`nFallen Helper is installed." -ForegroundColor Cyan
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "Ollama was not found. Install it from https://ollama.com/download" -ForegroundColor Yellow
} else {
    Write-Host "Ollama detected. If needed, run: ollama pull qwen3:4b"
}
Write-Host "Start Fallen with: .\scripts\start.ps1"
