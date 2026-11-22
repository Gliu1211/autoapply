# AutoApply launcher: sets everything up on first run, then starts the app at http://127.0.0.1:8742
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env. Put your ANTHROPIC_API_KEY in it, then run this again." -ForegroundColor Yellow
    notepad .env
    exit 0
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating Python environment..."
    python -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt
if (-not (Test-Path "$env:LOCALAPPDATA\ms-playwright\chromium-*")) {
    Write-Host "Installing the Playwright browser (used for PDF cover letters and as a fallback)..."
    & .\.venv\Scripts\python.exe -m playwright install chromium
}

if (-not (Test-Path "frontend\dist\index.html")) {
    Write-Host "Building the UI..."
    Push-Location frontend
    npm install --no-audit --no-fund
    npm run build
    Pop-Location
}

# Keep the Chrome extension's form reader identical to the app's.
& .\.venv\Scripts\python.exe backend\tools\build_extension.py --no-icons | Out-Null

$port = if ($env:AUTOAPPLY_PORT) { $env:AUTOAPPLY_PORT } else { "8742" }
Start-Job -ScriptBlock { param($p) Start-Sleep 3; Start-Process "http://127.0.0.1:$p" } -ArgumentList $port | Out-Null
& .\.venv\Scripts\python.exe backend\run.py
