#Requires -Version 7.0
<#
.SYNOPSIS
    WarrantyVault AI — Start all services natively (without Docker)
.DESCRIPTION
    Starts the backend API server, background worker, and frontend dev server
    in separate processes. Requires PostgreSQL to be running.
    Redis is optional (rate limiting falls back to in-memory).
.NOTES
    This script does NOT start PostgreSQL or Redis.
    Ensure PostgreSQL is running on 127.0.0.1:5432 before running this script.
#>

param(
    [switch]$NoWorker,
    [switch]$NoFrontend,
    [switch]$NoBackend,
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  WarrantyVault AI — Native Startup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

$script:StartedProcesses = @()

function Stop-AllServices {
    Write-Host ""
    Write-Host "Stopping services..." -ForegroundColor Yellow
    foreach ($proc in $script:StartedProcesses) {
        if (-not $proc.HasExited) {
            try {
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                Write-Host "  Stopped: $($proc.ProcessName) (PID $($proc.Id))" -ForegroundColor Gray
            }
            catch {}
        }
    }
}

$venvPy = Join-Path $ProjectRoot "backend/.venv/Scripts/python.exe"
$venvUvicorn = Join-Path $ProjectRoot "backend/.venv/Scripts/uvicorn.exe"
if (-not (Test-Path $venvPy)) {
    $altVenvs = @("backend/venv/Scripts/python.exe")
    foreach ($alt in $altVenvs) {
        $altPath = Join-Path $ProjectRoot $alt
        if (Test-Path $altPath) {
            $venvPy = $altPath
            $venvUvicorn = Join-Path (Split-Path $altPath) "uvicorn.exe"
            break
        }
    }
}
if (-not (Test-Path $venvPy)) {
    Write-Error "Backend virtual environment not found."
    exit 1
}

if (-not $NoBackend) {
    Write-Host "Starting backend API on port $BackendPort..." -ForegroundColor Green
    $existingListener = Get-NetTCPConnection -LocalPort $BackendPort -ErrorAction SilentlyContinue
    if ($existingListener) {
        Write-Host "  [WARN] Port $BackendPort is already in use." -ForegroundColor Yellow
    }
    else {
        $backendProc = Start-Process -FilePath $venvUvicorn `
            -ArgumentList "app.main:app", "--host", "127.0.0.1", "--port", $BackendPort, "--reload" `
            -WorkingDirectory (Join-Path $ProjectRoot "backend") `
            -PassThru -NoNewWindow
        $script:StartedProcesses += $backendProc
        Write-Host "  Backend PID: $($backendProc.Id)" -ForegroundColor Gray
    }
}

if (-not $NoWorker -and -not $NoBackend) {
    Write-Host "Starting background worker..." -ForegroundColor Green
    $workerProc = Start-Process -FilePath $venvPy `
        -ArgumentList "-m", "app.workers.run" `
        -WorkingDirectory (Join-Path $ProjectRoot "backend") `
        -PassThru -NoNewWindow
    $script:StartedProcesses += $workerProc
    Write-Host "  Worker PID: $($workerProc.Id)" -ForegroundColor Gray
}

if (-not $NoFrontend) {
    $nodeModulesPath = Join-Path $ProjectRoot "frontend/node_modules"
    if (-not (Test-Path $nodeModulesPath)) {
        Write-Host "  [WARN] frontend/node_modules not found. Run 'cd frontend; npm ci' first." -ForegroundColor Yellow
    }
    else {
        Write-Host "Starting frontend dev server on port $FrontendPort..." -ForegroundColor Green
        $existingFrontend = Get-NetTCPConnection -LocalPort $FrontendPort -ErrorAction SilentlyContinue
        if ($existingFrontend) {
            Write-Host "  [WARN] Port $FrontendPort already in use." -ForegroundColor Yellow
        }
        else {
            $npmCmd = (Get-Command npm).Source
            $frontendProc = Start-Process -FilePath $npmCmd `
                -ArgumentList "run", "dev", "--", "--port", $FrontendPort `
                -WorkingDirectory (Join-Path $ProjectRoot "frontend") `
                -PassThru -NoNewWindow
            $script:StartedProcesses += $frontendProc
            Write-Host "  Frontend PID: $($frontendProc.Id)" -ForegroundColor Gray
        }
    }
}

Start-Sleep -Seconds 3

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Services started!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "URLs:" -ForegroundColor White
if (-not $NoBackend) {
    Write-Host "  Backend API:  http://127.0.0.1:$BackendPort" -ForegroundColor Cyan
    Write-Host "  API docs:     http://127.0.0.1:$BackendPort/api/docs" -ForegroundColor Cyan
    Write-Host "  Health:       http://127.0.0.1:$BackendPort/health" -ForegroundColor Cyan
}
if (-not $NoFrontend) {
    Write-Host "  Frontend:     http://localhost:$FrontendPort" -ForegroundColor Cyan
}
Write-Host ""
Write-Host "To stop all services, run:" -ForegroundColor Yellow
Write-Host "  .\scripts\stop-native.ps1" -ForegroundColor White

if ($script:StartedProcesses.Count -gt 0) {
    try {
        Write-Host "Monitoring processes (press Ctrl+C to stop all)..." -ForegroundColor DarkGray
        while ($true) {
            Start-Sleep -Seconds 5
            $alive = $script:StartedProcesses | Where-Object { -not $_.HasExited }
            if ($alive.Count -eq 0) { break }
        }
    }
    finally {
        Stop-AllServices
    }
}