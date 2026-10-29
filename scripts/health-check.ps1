#Requires -Version 7.0
<#
.SYNOPSIS
    WarrantyVault AI — Health Check for Native Development
.DESCRIPTION
    Verifies that all services are running and accessible.
#>

param([int]$BackendPort = 8000, [int]$FrontendPort = 5173)

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  WarrantyVault AI — Health Check" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

$allOk = $true

Write-Host "--- Backend API (port $BackendPort) ---" -ForegroundColor Yellow
try {
    $healthResp = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/health" -TimeoutSec 5 -ErrorAction Stop
    Write-Host "  [OK] /health responds: status=$($healthResp.status) env=$($healthResp.env)" -ForegroundColor Green
}
catch {
    Write-Host "  [FAIL] Backend not reachable at http://127.0.0.1:$BackendPort" -ForegroundColor Red
    $allOk = $false
}

try {
    $readyResp = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/health/ready" -TimeoutSec 5 -ErrorAction Stop
    if ($readyResp.database -eq "ok") { Write-Host "  [OK] Database: ok" -ForegroundColor Green }
    else { Write-Host "  [FAIL] Database: $($readyResp.database)" -ForegroundColor Red; $allOk = $false }
    Write-Host "  [INFO] Queue: $($readyResp.queue)" -ForegroundColor Gray
}
catch { Write-Host "  [WARN] /health/ready not reachable" -ForegroundColor Yellow }

Write-Host "--- Frontend Dev Server (port $FrontendPort) ---" -ForegroundColor Yellow
try {
    $frontendResp = Invoke-WebRequest -Uri "http://localhost:$FrontendPort" -TimeoutSec 5 -ErrorAction Stop -UseBasicParsing
    if ($frontendResp.StatusCode -eq 200) { Write-Host "  [OK] Frontend responds with HTTP 200" -ForegroundColor Green }
    else { Write-Host "  [WARN] Frontend responded with HTTP $($frontendResp.StatusCode)" -ForegroundColor Yellow }
}
catch {
    Write-Host "  [FAIL] Frontend not reachable at http://localhost:$FrontendPort" -ForegroundColor Red
    $allOk = $false
}

Write-Host "--- Frontend -> Backend Proxy ---" -ForegroundColor Yellow
try {
    $proxyResp = Invoke-WebRequest -Uri "http://localhost:$FrontendPort/api/v1/system/info" -TimeoutSec 5 -ErrorAction Stop -UseBasicParsing
    Write-Host "  [OK] Proxy works: HTTP $($proxyResp.StatusCode)" -ForegroundColor Green
}
catch {
    if ($_.Exception.Response) {
        $statusCode = [int]$_.Exception.Response.StatusCode
        if ($statusCode -eq 404) { Write-Host "  [OK] Proxy works (404 expected if endpoint missing)" -ForegroundColor Green }
        else { Write-Host "  [INFO] Proxy returned HTTP $statusCode" -ForegroundColor Yellow }
    }
    else {
        Write-Host "  [FAIL] Proxy not working" -ForegroundColor Red; $allOk = $false
    }
}

Write-Host "--- PostgreSQL ---" -ForegroundColor Yellow
$pgReady = Get-Command pg_isready -ErrorAction SilentlyContinue
if ($pgReady) {
    & pg_isready -h 127.0.0.1 -p 5432 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "  [OK] PostgreSQL accepting connections" -ForegroundColor Green }
    else { Write-Host "  [FAIL] PostgreSQL not accepting connections" -ForegroundColor Red; $allOk = $false }
}
else { Write-Host "  [INFO] pg_isready not in PATH" -ForegroundColor Yellow }

Write-Host "--- Redis ---" -ForegroundColor Yellow
$redisCli = Get-Command redis-cli -ErrorAction SilentlyContinue
if ($redisCli) {
    $redisResult = & redis-cli ping 2>&1
    if ($redisResult -eq "PONG") { Write-Host "  [OK] Redis is running" -ForegroundColor Green }
    else { Write-Host "  [WARN] Redis not responding (in-memory fallback used)" -ForegroundColor Yellow }
}
else { Write-Host "  [INFO] Redis not installed — using in-memory rate limiting" -ForegroundColor Yellow }

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
if ($allOk) { Write-Host "  All checks passed!" -ForegroundColor Green }
else { Write-Host "  Some checks failed — see above" -ForegroundColor Red }
Write-Host "============================================" -ForegroundColor Cyan