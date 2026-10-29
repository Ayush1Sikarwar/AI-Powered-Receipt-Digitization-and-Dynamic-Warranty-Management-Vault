#Requires -Version 7.0
<#
.SYNOPSIS
    WarrantyVault AI — Native Windows Development Setup
.DESCRIPTION
    Checks prerequisites and installs dependencies for native (Docker-free) development.
    Does NOT modify existing databases, .env files, or user data.
#>

param(
    [switch]$SkipFrontend,
    [switch]$SkipBackend
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  WarrantyVault AI — Native Setup Check" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot" -ForegroundColor Gray
Write-Host ""

$issues = @()

# --- Python ---
Write-Host "--- Python ---" -ForegroundColor Yellow
$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) {
        $ver = & $found --version 2>&1
        if ($ver -match "Python 3\.(1[1-9]|[2-9])") {
            $pythonCmd = $found.Source
            Write-Host "  [OK] $($found.Source) ($ver)" -ForegroundColor Green
            break
        }
    }
}
if (-not $pythonCmd) {
    $issues += "Python 3.11+ is required. Install from https://python.org"
    Write-Host "  [MISSING] Python 3.11+ not found" -ForegroundColor Red
}

# --- Node.js ---
Write-Host "--- Node.js ---" -ForegroundColor Yellow
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    $nodeVer = & node --version 2>&1
    Write-Host "  [OK] Node.js $nodeVer ($($node.Source))" -ForegroundColor Green
} else {
    $issues += "Node.js 20+ is required. Install from https://nodejs.org"
    Write-Host "  [MISSING] Node.js not found" -ForegroundColor Red
}

$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($npm) {
    $npmVer = & npm --version 2>&1
    Write-Host "  [OK] npm $npmVer" -ForegroundColor Green
} else {
    $issues += "npm is required (normally installed with Node.js)"
    Write-Host "  [MISSING] npm not found" -ForegroundColor Red
}

# --- PostgreSQL ---
Write-Host "--- PostgreSQL ---" -ForegroundColor Yellow
$pgReady = Get-Command pg_isready -ErrorAction SilentlyContinue
if ($pgReady) {
    $pgResult = & pg_isready -h 127.0.0.1 -p 5432 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] PostgreSQL is running on 127.0.0.1:5432" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] pg_isready found but PostgreSQL may not be running" -ForegroundColor Yellow
        Write-Host "         Start PostgreSQL service or install from https://www.postgresql.org/download/windows/" -ForegroundColor Gray
    }
} else {
    $psql = Get-Command psql -ErrorAction SilentlyContinue
    if ($psql) {
        Write-Host "  [INFO] psql found at $($psql.Source) — verify PostgreSQL is running" -ForegroundColor Yellow
    } else {
        Write-Host "  [INFO] PostgreSQL client tools not in PATH" -ForegroundColor Yellow
        Write-Host "         PostgreSQL must be running on 127.0.0.1:5432 for the backend." -ForegroundColor Gray
        Write-Host "         Alternatively, you can use Docker ONLY for PostgreSQL:" -ForegroundColor Gray
        Write-Host "         docker run -d --name warranty-pg -e POSTGRES_USER=warranty -e POSTGRES_PASSWORD=warranty_dev_pw -e POSTGRES_DB=warranty_vault -p 5432:5432 postgres:17" -ForegroundColor Gray
    }
}

# --- Redis (optional) ---
Write-Host "--- Redis (optional) ---" -ForegroundColor Yellow
$redisCli = Get-Command redis-cli -ErrorAction SilentlyContinue
if ($redisCli) {
    $redisResult = & redis-cli ping 2>&1
    if ($redisResult -eq "PONG") {
        Write-Host "  [OK] Redis is running (in-memory fallback will NOT be used)" -ForegroundColor Green
    } else {
        Write-Host "  [INFO] redis-cli found but Redis may not be running" -ForegroundColor Yellow
        Write-Host "         Rate limiting will use in-memory fallback (OK for development)." -ForegroundColor Gray
    }
} else {
    Write-Host "  [INFO] Redis not installed — in-memory rate limiting will be used" -ForegroundColor Yellow
    Write-Host "         This is fine for development. Install Redis for production-like behavior." -ForegroundColor Gray
}

# --- Tesseract OCR (optional, for AI features) ---
Write-Host "--- Tesseract OCR (optional, for AI features) ---" -ForegroundColor Yellow
$tesseract = Get-Command tesseract -ErrorAction SilentlyContinue
if ($tesseract) {
    $tessVer = & tesseract --version 2>&1 | Select-Object -First 1
    Write-Host "  [OK] Tesseract $tessVer" -ForegroundColor Green
} else {
    Write-Host "  [INFO] Tesseract not found — OCR features will not work" -ForegroundColor Yellow
    Write-Host "         Install from https://github.com/UB-Mannheim/tesseract/wiki" -ForegroundColor Gray
}

# --- Environment file ---
Write-Host "--- Environment Configuration ---" -ForegroundColor Yellow
$envExample = Join-Path $ProjectRoot ".env.example"
$backendEnv = Join-Path $ProjectRoot "backend/.env"
if (Test-Path $backendEnv) {
    Write-Host "  [OK] backend/.env exists" -ForegroundColor Green
} elseif (Test-Path $envExample) {
    Write-Host "  [MISSING] backend/.env not found" -ForegroundColor Yellow
    Write-Host "  To create: Copy-Item .env.example backend/.env" -ForegroundColor Gray
} else {
    Write-Host "  [WARN] Neither .env.example nor backend/.env found" -ForegroundColor Red
}

# --- Backend virtual environment ---
Write-Host "--- Backend Python Environment ---" -ForegroundColor Yellow
$venvPath = Join-Path $ProjectRoot "backend/.venv"
$venvPy = Join-Path $venvPath "Scripts/python.exe"
if (Test-Path $venvPy) {
    $venvVer = & $venvPy --version 2>&1
    Write-Host "  [OK] Virtual environment exists at backend/.venv ($venvVer)" -ForegroundColor Green
} else {
    $altVenvs = @("backend/venv", "backend/.venv")
    $foundAlt = $false
    foreach ($av in $altVenvs) {
        $altPy = Join-Path $ProjectRoot "$av/Scripts/python.exe"
        if (Test-Path $altPy) {
            Write-Host "  [OK] Virtual environment found at $av" -ForegroundColor Green
            $foundAlt = $true
            break
        }
    }
    if (-not $foundAlt) {
        Write-Host "  [MISSING] No virtual environment found in backend/" -ForegroundColor Yellow
        Write-Host "  To create: cd backend; python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -e `".[dev]`"" -ForegroundColor Gray
    }
}

# --- Frontend node_modules ---
Write-Host "--- Frontend Dependencies ---" -ForegroundColor Yellow
$nodeModules = Join-Path $ProjectRoot "frontend/node_modules"
if (Test-Path $nodeModules) {
    Write-Host "  [OK] frontend/node_modules exists" -ForegroundColor Green
} else {
    Write-Host "  [MISSING] frontend/node_modules not found" -ForegroundColor Yellow
    Write-Host "  To install: cd frontend; npm ci" -ForegroundColor Gray
}

# --- Summary ---
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
if ($issues.Count -eq 0) {
    Write-Host "  All critical prerequisites satisfied!" -ForegroundColor Green
} else {
    Write-Host "  Issues found:" -ForegroundColor Red
    foreach ($issue in $issues) {
        Write-Host "  - $issue" -ForegroundColor Red
    }
}
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Quick start (once prerequisites are met):" -ForegroundColor White
Write-Host "  # Terminal 1: Backend" -ForegroundColor Gray
Write-Host "  cd $ProjectRoot\backend" -ForegroundColor Gray
Write-Host "  .\.venv\Scripts\Activate.ps1" -ForegroundColor Gray
Write-Host "  uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload" -ForegroundColor Gray
Write-Host ""
Write-Host "  # Terminal 2: Frontend" -ForegroundColor Gray
Write-Host "  cd $ProjectRoot\frontend" -ForegroundColor Gray
Write-Host "  npm run dev" -ForegroundColor Gray
Write-Host ""
Write-Host "  Frontend: http://localhost:5173" -ForegroundColor Cyan
Write-Host "  Backend:  http://localhost:8000" -ForegroundColor Cyan
Write-Host "  API docs: http://localhost:8000/api/docs" -ForegroundColor Cyan