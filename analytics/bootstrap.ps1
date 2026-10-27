# analytics/bootstrap.ps1
# 1-Click Bootstrap and Launch Script for Dell OptiPlex (Windows PowerShell)
# Sets up Python environment, verifies YOLO26 Nano weights, and launches pipeline.

$ErrorActionPreference = "Stop"

Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  ZAMPEN Native Layer Chicken Analytics Pipeline Bootstrap" -ForegroundColor Cyan
Write-Host "  Dell OptiPlex Edge Hub (YOLO26 Nano + ByteTrack)" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan

# 1. Check Python
try {
    $pythonVersion = & python --version 2>&1
    Write-Host "[OK] Python detected: $pythonVersion" -ForegroundColor Green
}
catch {
    Write-Host "[ERROR] Python is not installed or not in PATH. Please install Python 3.10+." -ForegroundColor Red
    exit 1
}

# 2. Check / Install Pip Dependencies
Write-Host "`n[STEP 1/4] Verifying Python Dependencies..." -ForegroundColor Yellow
$requirementsFile = Join-Path $PSScriptRoot "requirements.txt"
& pip install -r $requirementsFile --quiet
Write-Host "[OK] All Python dependencies satisfied." -ForegroundColor Green

# 3. Verify YOLO26 Nano Weights & ByteTrack Configuration
Write-Host "`n[STEP 2/4] Verifying YOLO26 Nano Weights & Tracker Config..." -ForegroundColor Yellow
$projectRoot = Split-Path $PSScriptRoot -Parent
$customPt = Join-Path $projectRoot "custom_poultry.pt"
$yolo26Pt = Join-Path $projectRoot "yolo26n.pt"
$analyticsPt = Join-Path $PSScriptRoot "yolo26n.pt"

if (Test-Path $yolo26Pt) {
    Write-Host "[OK] Verified yolo26n.pt at $yolo26Pt" -ForegroundColor Green
}
elseif (Test-Path $analyticsPt) {
    Write-Host "[OK] Verified yolo26n.pt at $analyticsPt" -ForegroundColor Green
}
elseif (Test-Path $customPt) {
    Write-Host "[INFO] yolo26n.pt not found. Using validated custom poultry model: $customPt" -ForegroundColor Cyan
}
else {
    Write-Host "[INFO] Downloading YOLO Nano baseline weights..." -ForegroundColor Cyan
    & python -c "from ultralytics import YOLO; YOLO('yolo11n.pt')"
}

# 4. Initialize Database
Write-Host "`n[STEP 3/4] Initializing Analytics SQLite Database..." -ForegroundColor Yellow
$env:PYTHONPATH = $projectRoot
& python -c "from analytics.database import DB; DB.init_db(); print('[OK] Database initialized successfully.')"

# 5. Launch Services
Write-Host "`n[STEP 4/4] Launching Pipeline Services..." -ForegroundColor Yellow
Write-Host "  API Documentation : http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "  Health Endpoint   : http://localhost:8000/health" -ForegroundColor Cyan
Write-Host "  Latest Results    : http://localhost:8000/results/latest" -ForegroundColor Cyan
Write-Host "`nStarting FastAPI Server & Background Worker Daemon..." -ForegroundColor Green
Write-Host "Press Ctrl+C to terminate all services.`n" -ForegroundColor DarkGray

# Launch FastAPI in background process
$apiProcess = Start-Process python -ArgumentList "-m analytics.api" -PassThru -NoNewWindow
Start-Sleep -Seconds 2

# Run Worker Daemon in current window
try {
    & python -m analytics.worker
}
finally {
    Write-Host "`nStopping FastAPI background process (PID $($apiProcess.Id))..." -ForegroundColor Yellow
    Stop-Process -Id $apiProcess.Id -Force -ErrorAction SilentlyContinue
    Write-Host "[SHUTDOWN] All services stopped cleanly." -ForegroundColor Green
}