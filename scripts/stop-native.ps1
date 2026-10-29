#Requires -Version 7.0
<#
.SYNOPSIS
    WarrantyVault AI — Stop native development services
.DESCRIPTION
    Stops backend (uvicorn), worker, and frontend (vite) processes.
    Does NOT stop PostgreSQL, Redis, or Docker. Does NOT delete any data.
#>

param([switch]$Force)

$ErrorActionPreference = "Continue"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  WarrantyVault AI — Stop Services" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

$stopped = 0

Write-Host "Checking for backend (uvicorn) processes..." -ForegroundColor Yellow
$uvicornProcs = Get-Process -Name "uvicorn" -ErrorAction SilentlyContinue
if ($uvicornProcs) {
    foreach ($proc in $uvicornProcs) {
        if ($Force -or $PSCmdlet.ShouldContinue("Stop uvicorn (PID $($proc.Id))?", "Confirm")) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            Write-Host "  Stopped: uvicorn (PID $($proc.Id))" -ForegroundColor Green
            $stopped++
        }
    }
}
else { Write-Host "  No uvicorn processes found." -ForegroundColor Gray }

Write-Host "Checking for background worker (python) processes..." -ForegroundColor Yellow
$workerProcs = Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {
    try { $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)").CommandLine; $cmdLine -match "app.workers.run" } catch { $false }
}
if ($workerProcs) {
    foreach ($proc in $workerProcs) {
        if ($Force -or $PSCmdlet.ShouldContinue("Stop worker (PID $($proc.Id))?", "Confirm")) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            Write-Host "  Stopped: worker (PID $($proc.Id))" -ForegroundColor Green
            $stopped++
        }
    }
}
else { Write-Host "  No worker processes found." -ForegroundColor Gray }

Write-Host "Checking for frontend (node/vite) processes on port 5173..." -ForegroundColor Yellow
$frontendConns = Get-NetTCPConnection -LocalPort 5173 -ErrorAction SilentlyContinue
if ($frontendConns) {
    $frontendPids = $frontendConns | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($pid in $frontendPids) {
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if ($proc) {
            if ($Force -or $PSCmdlet.ShouldContinue("Stop $($proc.ProcessName) (PID $pid) on port 5173?", "Confirm")) {
                Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                Write-Host "  Stopped: $($proc.ProcessName) (PID $pid)" -ForegroundColor Green
                $stopped++
            }
        }
    }
}
else { Write-Host "  No frontend processes found on port 5173." -ForegroundColor Gray }

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
if ($stopped -gt 0) { Write-Host "  Stopped $stopped process(es)" -ForegroundColor Green }
else { Write-Host "  No services were running" -ForegroundColor Yellow }
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Note: PostgreSQL and Redis were NOT stopped. No data modified." -ForegroundColor Gray