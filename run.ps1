# Trading Data Center launcher
# Double-click this file (or run: powershell -ExecutionPolicy Bypass -File run.ps1)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
Write-Host "Starting Trading Data Center..." -ForegroundColor Cyan
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue) }
if (-not $py) { Write-Host "Python not found. Install Python 3 from python.org, then re-run." -ForegroundColor Red; Read-Host "Press Enter"; exit 1 }
# KILL any stale server holding port 8765 FIRST, so the (re)launch actually loads the new code. A leftover
# process keeping the port would otherwise make the new launch fail to bind and the OLD code keep serving —
# the "I restarted but nothing changed" bug. (Leaves the tray app coach_app.py alone — it doesn't own 8765.)
try {
  $stale = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($procId in $stale) {
    Write-Host "Stopping stale server (PID $procId) on port 8765..." -ForegroundColor Yellow
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
  }
  if ($stale) { Start-Sleep -Milliseconds 700 }   # let the port free up before re-binding
} catch {}
& $py.Source "app.py"
