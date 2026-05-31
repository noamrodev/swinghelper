# Trading Data Center launcher
# Double-click this file (or run: powershell -ExecutionPolicy Bypass -File run.ps1)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
Write-Host "Starting Trading Data Center..." -ForegroundColor Cyan
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue) }
if (-not $py) { Write-Host "Python not found. Install Python 3 from python.org, then re-run." -ForegroundColor Red; Read-Host "Press Enter"; exit 1 }
& $py.Source "app.py"
