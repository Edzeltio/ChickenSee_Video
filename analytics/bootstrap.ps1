# analytics/bootstrap.ps1
# Local Windows setup and launch helper for the ChickenSee analytics services.

$ErrorActionPreference = "Stop"

Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  ZAMPEN Native Layer Chicken Analytics Pipeline Bootstrap" -ForegroundColor Cyan
Write-Host "  Local poultry analytics (compatible YOLO + ByteTrack)" -ForegroundColor Cyan
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

# 2. Check / Install dependencies
Write-Host "`n[STEP 1/4] Verifying Python Dependencies..." -ForegroundColor Yellow
$requirementsFile = Join-Path $PSScriptRoot "requirements.txt"
& python -m pip install -r $requirementsFile --quiet
Write-Host "[OK] All Python dependencies satisfied." -ForegroundColor Green

# 3. Resolve the project root and check the active model/device setup.
$projectRoot = Split-Path $PSScriptRoot -Parent
$env:PYTHONPATH = $projectRoot
Write-Host "`n[STEP 2/4] Checking active model and available hardware..." -ForegroundColor Yellow
& python (Join-Path $projectRoot "model_manager.py") list
& python (Join-Path $projectRoot "setup_check.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Fix the setup-check issues before launching the services." -ForegroundColor Red
    exit 1
}

# 4. Initialize Database
Write-Host "`n[STEP 3/4] Initializing Analytics SQLite Database..." -ForegroundColor Yellow
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